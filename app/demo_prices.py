"""Canlı demo (HESAPKITAP_DEMO=1) için uydurma fiyat verisi: Fiyat takibi, Nereden alınır, Alışveriş listesi canlı dursun.

Demo sunucusunda BigQuery yoktur; "piyasa" verisi buradan gelir. Bütün fiyatlar UYDURMADIR (gerçek bir zincirin gerçek
fiyatı değildir; marka yok, genel ürün adları). Zincirler de kurgudur ("Market A" … "Market E"): uydurma fiyatın yanında
gerçek bir zincirin adı görünmez. Gerçek ulusal zincirlerin (price_sources.NATIONAL_CHAINS) davranışı demoda NATIONAL ile
sürer (price_sources.national_chains; receipt_templates.chain_of demoda bu adları tanır). Üç parça:

1. seed_system(today) — system.db (bütün ziyaretçilerin ortak verisi), günde bir kez (işaret: sys_settings
   MARKER_KEY): ~30 ürün grubu × Market A … Market E için
   - official_prices: son LIVE_DAYS gün (Nereden alınır, Nereye gideyim? tekliflerinin "resmi" kaynağı; depot_id 'demo');
   - official_daily: önceki BAND_DAYS gün (ürün grafiğindeki piyasa bandı, fişin piyasa notu, tasarruf ölçüsü);
   - community_prices: birkaç grup için son günlerde 3 ya da 5 uydurma katkı (contributor 'demo-…'; ≥3 kişi eşiği geçer).
   Tarihler bugüne göredir; eski demo satırları silinip yeniden yazılır.
2. receipts() — app.demo seed'inde (şablon, ANCHOR'a göre tarihli; demo_mode.shift_db bugüne kaydırır): farklı
   zincirlerde, farklı günlerde market fişleri (receipt_items). Kalem fiyatları aynı fiyat işlevinden (price) türetilir:
   süt hep Market D'de alınır → "Market B'de daha ucuz" önerisi; son Market D fişinde yumurta piyasadan pahalı → fiş
   piyasa notu.
3. shopping(today) — şablon kaydırıldıktan SONRA (gerçek bugünle): bir geçmiş alışveriş (plan → tamamla → fiş bağlandı,
   tasarruf), etkin liste ("2,5 kg elma", "6x süt" …) ve kaydedilmiş plan ("Bu planla git").
Gruplar yalnız kurallarla çözülür (yapay zekâ yok); hepsi birkaç yüz ms.
"""
import hashlib
from datetime import date, timedelta

VERSION = 2  # 2: kurgu zincir adları
MARKER_KEY = "demo:prices_seeded"
REGION = "İstanbul"
CHAINS = ("Market A", "Market B", "Market C", "Market D", "Market E")
FACTOR = {"Market A": 0.93, "Market B": 0.95, "Market C": 0.97, "Market E": 1.03, "Market D": 1.06}
# fiyatı ülke genelinde aynı sayılan kurgu zincirler (gerçekteki BİM / A101 / ŞOK / Migros gibi); Market E il bazlı
# (CarrefourSA gibi: teklifi "İstanbul" etiketli). Yalnız demoda: price_sources.national_chains()
NATIONAL = frozenset({"Market A", "Market B", "Market C", "Market D"})
LIVE_DAYS = 3     # official_prices: bugün ve önceki 2 gün
BAND_DAYS = 120   # official_daily: grafiğin 90 günü ve en eski fiş rahat sığsın
DRIFT = 0.0006    # günlük fiyat artışı (geçmiş günler biraz ucuz: grafik düz olmasın)

# (fiyat başlığı, birim (tartılı: "kg"), bugünün ortanca fiyatı ₺, bulunmayan zincirler)
PRODUCTS: list[tuple[str, str | None, float, tuple[str, ...]]] = [
    ("SÜT 1 L", None, 34.0, ()),
    ("YUMURTA 30LU", None, 146.0, ()),
    ("EKMEK 250 G", None, 15.0, ()),
    ("AYÇİÇEK YAĞI 5 L", None, 385.0, ()),
    ("TOZ DETERJAN 6 KG", None, 335.0, ()),
    ("BEYAZ PEYNİR 500 G", None, 138.0, ()),
    ("DOMATES", "kg", 39.0, ()),
    ("ELMA", "kg", 44.0, ()),
    ("MAKARNA 500 G", None, 22.0, ()),
    ("PİRİNÇ 1 KG", None, 72.0, ()),
    ("KOLA 2,5 L", None, 62.0, ()),
    ("ÇAY 1 KG", None, 280.0, ()),
    ("TOZ ŞEKER 1 KG", None, 48.0, ()),
    ("UN 5 KG", None, 158.0, ()),
    ("YOĞURT 1 KG", None, 68.0, ()),
    ("TEREYAĞI 500 G", None, 228.0, ()),
    ("SU 5 L", None, 29.0, ()),
    ("MUZ", "kg", 75.0, ()),
    ("PATATES", "kg", 25.0, ()),
    ("SOĞAN", "kg", 20.0, ()),
    ("KAŞAR PEYNİR 400 G", None, 178.0, ()),
    ("ZEYTİN 500 G", None, 118.0, ()),
    ("BULAŞIK DETERJANI 1,5 L", None, 88.0, ()),
    ("TUVALET KAĞIDI 32Lİ", None, 298.0, ()),
    ("KAHVE 100 G", None, 158.0, ("Market A", "Market B")),
    ("SALÇA 830 G", None, 78.0, ()),
    ("MERCİMEK 1 KG", None, 64.0, ()),
    ("AYRAN 1 L", None, 38.0, ()),
    ("TAVUK BUT", "kg", 148.0, ("Market C",)),
    ("SALATALIK", "kg", 35.0, ()),
]
# topluluk satırı olan gruplar (başlık): zincir başına 3 ya da 5 uydurma kişi, son 1–5 gün
COMMUNITY = ("SÜT 1 L", "YUMURTA 30LU", "EKMEK 250 G", "DOMATES", "AYÇİÇEK YAĞI 5 L", "BEYAZ PEYNİR 500 G")
COMMUNITY_CHAINS = ("Market A", "Market B", "Market C", "Market D")
_BY_TITLE = {t: (u, m, miss) for t, u, m, miss in PRODUCTS}
_keys: dict[str, str] = {}


def _h(*parts) -> float:
    """Kararlı sahte rastgele [0, 1)."""
    d = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(d[:4], "big") / 2 ** 32


def key_of(title: str) -> str:
    """Başlığın ürün grubu (yalnız kurallar)."""
    if title not in _keys:
        from app import product_groups

        g = product_groups.rule_group(title, unit=_BY_TITLE[title][0])
        if not g:
            raise ValueError(f"demo ürünü gruba çözülemedi: {title}")
        _keys[title] = g["key"]
    return _keys[title]


def has(title: str, chain: str) -> bool:
    return chain not in _BY_TITLE[title][2]


def price(title: str, chain: str, days_ago: int) -> float:
    """Zincirin o günkü (uydurma) raf fiyatı: ortanca × zincir çarpanı × ürün-zincir sapması (±%3) × gün dalgası (±%1)
    × geçmiş günlerde biraz ucuz (DRIFT). Raf fiyatı gibi yuvarlanır (₺x,50 / ₺x,95)."""
    _u, med, _m = _BY_TITLE[title]
    p = med * FACTOR[chain] * (0.97 + 0.06 * _h(title, chain)) * (0.99 + 0.02 * _h(title, chain, days_ago // 4))
    p *= 1 - DRIFT * days_ago
    return shelf(p)


def shelf(p: float) -> float:
    if p < 10:
        return round(p, 2)
    whole = int(p)
    return round(whole + (0.5 if p - whole < 0.75 else 0.95), 2)


# ---------------------------------------------------------------- 1. system.db

def seed_system(today: date | None = None, force: bool = False) -> bool:
    """Bugünün demo piyasa verisini yaz (günde bir kez). Yazıldıysa True."""
    from statistics import median

    from app import product_groups, system

    today = today or date.today()
    mark = f"v{VERSION}:{today.isoformat()}"
    if not force and system.get(MARKER_KEY) == mark:
        return False
    keys = {t: key_of(t) for t, *_ in PRODUCTS}
    titles = {t: _title(t) for t in keys}
    live, daily, comm = [], [], []
    for t, _u, _m, _miss in PRODUCTS:
        for d in range(BAND_DAYS + 1):
            day = (today - timedelta(days=d)).isoformat()
            per = {ch: price(t, ch, d) for ch in CHAINS if has(t, ch)}
            if d < LIVE_DAYS:
                live += [(ch, keys[t], titles[t], p, day) for ch, p in per.items()]
            else:
                ch_min = min(per, key=lambda k: (per[k], k))
                daily.append((keys[t], REGION, day, min(per.values()), round(median(per.values()), 2),
                              max(per.values()), ch_min, len(per)))
    for t in COMMUNITY:
        for ch in COMMUNITY_CHAINS:
            n = 3 + 2 * int(_h("n", t, ch) * 2)  # 3 ya da 5 kişi (tek sayı: ortanca bir raf fiyatı)
            for who in range(n):
                d = 1 + int(_h("d", t, ch, who) * 5)
                p = round(price(t, ch, d) + (-0.5, 0.0, 0.5)[int(_h("p", t, ch, who) * 3)], 2)
                comm.append((ch, keys[t], product_groups.name_norm(t), p, (today - timedelta(days=d)).isoformat(),
                             f"demo-{ch}-{who}", REGION))
    ks = sorted(set(keys.values()))
    q = ",".join("?" * len(ks))
    with system.connect() as c:
        c.execute("DELETE FROM official_prices WHERE depot_id = 'demo'")
        c.execute(f"DELETE FROM official_daily WHERE region = ? AND group_key IN ({q})", [REGION, *ks])
        c.execute("DELETE FROM community_prices WHERE contributor LIKE 'demo-%'")
        c.executemany("INSERT OR REPLACE INTO official_prices (chain, group_key, title, brand, price, day, depot_id, "
                      "region, depots) VALUES (?, ?, ?, NULL, ?, ?, 'demo', ?, 1)",
                      [(ch, k, title, p, day, REGION) for ch, k, title, p, day in live])
        c.executemany("INSERT OR REPLACE INTO official_daily (group_key, region, day, min, median, max, cheapest_chain, "
                      "chains) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", daily)
        c.executemany("INSERT INTO community_prices (chain, group_key, name_norm, unit_price, day, contributor, region) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)", comm)
        c.execute("INSERT OR REPLACE INTO official_imports (run_date, region, rows, products, imported_at, mode) "
                  "VALUES (?, ?, ?, ?, ?, 'full')", (today.isoformat(), "demo", len(live), len(PRODUCTS),
                                                     f"{today.isoformat()} demo"))
    _labels(keys)
    system.put(MARKER_KEY, mark)
    return True


def _title(t: str) -> str:
    """Rafta görünen ad, grubun adından: "Süt 1 L", "Domates (kg)"."""
    from app import product_groups

    name, _, size = product_groups.rule_group(t, unit=_BY_TITLE[t][0])["label"].partition(" · ")
    return f"{name} (kg)" if size == "kg" else f"{name} {size}".strip()


def _labels(keys: dict[str, str]) -> None:
    """Tartılı grupların ("domates|kg") görünen adı önbellekte yoksa yaz (product_groups.labels yalnız önbellekten okur).
    Ad kuralların çözemeyeceği biçimdedir ("DEMO DOMATES KG"): yeniden gruplama bu satıra dokunmaz."""
    from app import product_groups, system

    have = product_groups.labels(list(keys.values()))
    rows = []
    for t, k in keys.items():
        if k in have:
            continue
        g = product_groups.rule_group(t, unit=_BY_TITLE[t][0])
        rows.append((f"DEMO {product_groups.name_norm(t)} {k.split('|')[-1].upper()}", g))
    if not rows:
        return
    with system.connect() as c:
        for nn, g in rows:
            product_groups._store(c, nn, g, "rule")


# ---------------------------------------------------------------- 2. fişler (şablon; ANCHOR'a göre)

# (kaç gün önce, zincir, [(başlık, adet / kilo)], fiyat oynaması {başlık: çarpan})
RECEIPTS: list[tuple[int, str, list[tuple[str, float]], dict[str, float]]] = [
    (55, "Market D", [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("BEYAZ PEYNİR 500 G", 1), ("EKMEK 250 G", 2),
                      ("DOMATES", 1.25), ("ÇAY 1 KG", 1)], {}),
    (41, "Market B", [("SÜT 1 L", 2), ("MAKARNA 500 G", 3), ("AYÇİÇEK YAĞI 5 L", 1), ("KOLA 2,5 L", 1),
                      ("PİRİNÇ 1 KG", 1)], {}),
    (27, "Market D", [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("TEREYAĞI 500 G", 1), ("ELMA", 1.6),
                      ("KAŞAR PEYNİR 400 G", 1)], {}),
    (16, "Market C", [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("EKMEK 250 G", 2), ("ZEYTİN 500 G", 1),
                      ("SALÇA 830 G", 1)], {}),
    (9, "Market E", [("SÜT 1 L", 2), ("TOZ DETERJAN 6 KG", 1), ("BULAŞIK DETERJANI 1,5 L", 1),
                     ("TUVALET KAĞIDI 32Lİ", 1), ("MUZ", 1.1)], {}),
    (3, "Market D", [("SÜT 1 L", 2), ("YUMURTA 30LU", 1), ("BEYAZ PEYNİR 500 G", 1), ("DOMATES", 1.4),
                     ("EKMEK 250 G", 1)], {"YUMURTA 30LU": 1.12}),  # yumurta piyasadan pahalı: fişte piyasa notu
]


def _save_receipt(day: str, chain: str, lines: list[tuple[str, float]], bump: dict[str, float],
                  days_ago: int) -> int:
    from app import db, money

    items = []
    for t, q in lines:
        unit = _BY_TITLE[t][0]
        up = shelf(price(t, chain, days_ago) * bump.get(t, 1.0))
        items.append({"name": t, "qty": q, "unit": "kg" if unit == "kg" else "adet",
                      "amount": money.money(up * q)})
    total = money.money(sum(i["amount"] for i in items))
    eid = db.create_entry({"entry_date": day, "description": chain, "amount": total, "kind": "gider",
                           "category": "Market & Gıda", "payment": "nakit"})
    db.save_receipt_items(eid, items, chain, day, total)
    return eid


def receipts(anchor: date) -> list[int]:
    """Şablon fişleri (anchor = demo_mode.ANCHOR; tarihler sonra bugüne kaydırılır)."""
    return [_save_receipt((anchor - timedelta(days=d)).isoformat(), ch, lines, bump, d)
            for d, ch, lines, bump in RECEIPTS]


# ---------------------------------------------------------------- 3. alışveriş listesi (kaydırmadan sonra; gerçek bugün)

TRIP = [("Ayçiçek yağı 5 L", "AYÇİÇEK YAĞI 5 L", 1), ("4x Makarna 500 g", "MAKARNA 500 G", 4),
        ("Pirinç 1 kg", "PİRİNÇ 1 KG", 1), ("Toz şeker 1 kg", "TOZ ŞEKER 1 KG", 1),
        ("Mercimek 1 kg", "MERCİMEK 1 KG", 1), ("Un 5 kg", "UN 5 KG", 1)]
TRIP_CHAIN = "Market A"  # planda zincir yoksa (olmamalı)
LIST = ["6x Süt", "2,5 kg Elma", "Yumurta 30'lu", "Beyaz peynir 500 g", "2x Kola 2,5 L", "Kahve 100 g",
        "1,5 kg Domates", "1 kg Tavuk but"]


def shopping(today: date | None = None) -> dict:
    """Geçmiş alışveriş (fişe bağlı, tasarruflu) + etkin liste + kaydedilmiş plan. Fiyat verisi (seed_system) önce
    yazılmış olmalı. {trip_id, entry_id, plan_k}."""
    from app import db, shopping as sl, shopping_trips

    today = today or date.today()
    back = min(3, today.day - 1)  # bu ayın içinde kalsın: "Bu ay ₺X tasarruf"
    tday = today - timedelta(days=back)
    ids = [sl.add(text)["id"] for text, _t, _q in TRIP]
    planned = shopping_trips.save_plan(k=1) or {}
    chain = next((c["chain"] for c in planned.get("chains") or []), TRIP_CHAIN)  # planın dediği markete gidildi
    for i in ids:
        sl.update(i, {"done": True})
    trip = shopping_trips.complete(carry_over=False)
    if back:  # birkaç gün önce tamamlanmış
        with db.connect() as c:
            c.execute("UPDATE shopping_trips SET day = ?, completed_at = ? WHERE id = ?",
                      (tday.isoformat(), f"{tday.isoformat()} 15:40:00", trip["id"]))
    eid = _save_receipt(tday.isoformat(), chain, [(t, q) for _x, t, q in TRIP], {}, back)
    shopping_trips.link_receipt(eid, "trip", trip["id"])
    for text in LIST:
        sl.add(text)
    plan = shopping_trips.save_plan()
    return {"trip_id": trip["id"], "entry_id": eid, "plan_k": plan["k"] if plan else None}
