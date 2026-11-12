"""Piyasa karşılaştırmasının güvenilirliği (12.11.2026 cihaz testi: "KINDER JOY 20 GR ₺42,75" düz 20 g çikolatanın
bandıyla karşılaştırılıp "pahalı · en ucuz ₺11" dendi): Sürpriz Yumurta ayrı grup, dağınık bant ve marka rafında not
yok, temel gıdada davranış aynı. İşyeri adları ve fiyatlar uydurma; ürün adları genel marka adları."""
from datetime import date, timedelta

import pytest

from app import market_ref, price_sources, product_groups, system

TODAY = date.today()
T = TODAY.isoformat()


def _d(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)


def _daily(g, mn, med, mx, day=T, chain="A101", region="İstanbul"):
    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO official_daily (group_key, region, day, min, median, max, cheapest_chain, "
                  "chains) VALUES (?, ?, ?, ?, ?, ?, ?, 4)", (g, region, day, mn, med, mx, chain))


def _notes(items):
    return market_ref.receipt_notes(items, None, "Örnek Market", T)


KINDER = {"name": "KINDER JOY 20 GR", "qty": 1, "amount": 42.75}
PLAIN = {"name": "Sütlü Çikolata 20 g", "qty": 1, "amount": 25.0}


# ---------------------------------------------------------------- 1. gruplama

@pytest.mark.parametrize("name,want", [
    ("KINDER JOY 20 GR", "surpriz-yumurta|20g"),
    ("KINDER SURPRISE 20G", "surpriz-yumurta|20g"),
    ("KINDER JOY CIKOLATA", "surpriz-yumurta"),          # zayıf "çikolata" marka ürününü ezmez
    ("TAYYO SURPRİZ YUMUR", "surpriz-yumurta"),          # kısaltılmış fiş adı
    ("SURP.YUMURTA 20G", "surpriz-yumurta|20g"),
    ("SÜRPRİZLİ YUMURTA 20 G", "surpriz-yumurta|20g"),   # "-li" sıfatı yumurtaya düşürmez
    ("KINDER CIKOLATA 50G", "cikolata|50g"),             # markanın düz çikolatası çikolata
    ("ULKER CIKOLATA 20 GR", "cikolata|20g"),
    ("YUMURTA 30LU", "yumurta|30ad"),                    # tavuk yumurtası aynı
    ("GEZEN TAVUK YUMURTASI 10 LU", "yumurta|10ad"),
])
def test_surprise_egg_own_group(name, want):
    g = product_groups.rule_group(name)
    assert g and g["key"] == want
    if want.startswith("surpriz"):
        assert (g["category"], g["subcategory"]) == ("Atıştırmalık", "Çikolata")


def test_rule_beats_wrong_cached_group_and_regroup_fixes_cache(client):
    """Önbellekte yapay zekânın "Çikolata" dediği ad: kural önce gelir; GROUPS_VERSION 6 arka plan yeniden gruplaması
    önbelleği ve resmî satırları da taşır (açılışta değil, regroup_tick)."""
    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO product_groups (name_norm, group_key, label, source, created_at, category, "
                  "subcategory) VALUES ('KINDER JOY 20 GR', 'cikolata|20g', 'Çikolata · 20 g', 'ai', '2026-11-01', "
                  "'Atıştırmalık', 'Çikolata')")
        c.execute("INSERT INTO official_prices (chain, group_key, title, price, day, region) VALUES "
                  "('A101', 'cikolata|20g', 'KINDER JOY 20 GR', 41.5, ?, 'İstanbul')", (T,))
        c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES ('product_groups:version', '5')")
    assert product_groups.group_for("KINDER JOY 20 GR", learn=False)["key"] == "surpriz-yumurta|20g"
    assert product_groups.regroup_pending()
    got = product_groups.regroup_tick()
    assert got and got["finished"]
    with system.connect() as c:
        assert c.execute("SELECT group_key FROM product_groups WHERE name_norm = 'KINDER JOY 20 GR'").fetchone()[0] == \
            "surpriz-yumurta|20g"
        assert c.execute("SELECT group_key FROM official_prices WHERE title = 'KINDER JOY 20 GR'").fetchone()[0] == \
            "surpriz-yumurta|20g"


# ---------------------------------------------------------------- 2. fiş notu

def test_kinder_joy_no_note_against_plain_chocolate_band(client, resmi):
    _daily("cikolata|20g", 11.0, 26.9, 30.0)                 # olaydaki bant: ortanca / en ucuz 2,45×
    assert _notes([KINDER]) == []


def test_kinder_joy_no_note_even_if_cached_in_chocolate_and_band_tight(client, resmi):
    """Eski sürümde çikolataya yazılmış ad bile (kural önce) çikolata bandına bakmaz; kural olmasa da marka rafında
    ödenen bandın en ucuzunun 3 katını geçince not yok."""
    _daily("cikolata|20g", 11.0, 12.0, 14.0)
    assert _notes([KINDER]) == []
    assert _notes([{"name": "Sütlü Çikolata 20 g", "qty": 1, "amount": 42.75}]) == []   # 3,9× en ucuz


def test_plain_chocolate_tight_band_gets_note(client, resmi):
    _daily("cikolata|20g", 15.0, 16.0, 18.0)                 # sıkı bant (1,2×)
    got = _notes([PLAIN])
    assert [(n["group"], n["pct"], n["market_min"], n["cheapest_chain"]) for n in got] == [
        ("cikolata|20g", 56.2, 15.0, "A101")]


def test_brand_shelf_needs_higher_pct(client, resmi):
    _daily("cikolata|20g", 15.0, 16.0, 18.0)
    assert _notes([{"name": "Sütlü Çikolata 20 g", "qty": 1, "amount": 19.5}]) == []    # +%21,9 < %30
    _daily("sut|1l", 33.0, 35.95, 46.7)
    assert _notes([{"name": "Süt 1L", "qty": 1, "amount": 42.0}])[0]["pct"] == 16.8      # temel gıdada %10 yeter


def test_brand_shelf_wide_band_no_note(client, resmi):
    _daily("cikolata|20g", 15.0, 18.0, 24.0)                 # 1,6× > marka rafı sınırı 1,5×
    assert _notes([PLAIN]) == []


def test_milk_unchanged(client, resmi):
    _daily("sut|1l", 33.0, 35.95, 46.7)                      # 1,42×: mevcut davranış
    got = _notes([{"name": "Süt 1L", "qty": 1, "amount": 42.0}])
    assert [(n["group"], n["pct"], n["market_min"], n["cheapest_chain"]) for n in got] == [
        ("sut|1l", 16.8, 33.0, "A101")]
    _daily("sut|1l", 33.0, 35.95, 60.0)                      # 1,82×: hâlâ sınır içinde
    assert len(_notes([{"name": "Süt 1L", "qty": 1, "amount": 42.0}])) == 1
    # marka rafındaki tavan temel gıdaya uygulanmaz (en ucuzun 3 katı: yine not)
    assert _notes([{"name": "Süt 1L", "qty": 1, "amount": 120.0}])[0]["pct"] > 200


def test_dispersed_band_no_note(client, resmi):
    _daily("sut|1l", 20.0, 35.95, 46.7)                      # 2,34× > SPREAD_MAX: grup heterojen
    assert _notes([{"name": "Süt 1L", "qty": 1, "amount": 42.0}]) == []


# ---------------------------------------------------------------- 3. diğer yüzeyler

def test_comparable_and_compare():
    choc = {"category": "Atıştırmalık", "subcategory": "Çikolata"}
    milk = {"category": "Süt ve Kahvaltılık", "subcategory": "Süt"}
    ref = {"min": 15.0, "median": 16.0, "max": 18.0, "cheapest_chain": "A101", "day": T, "source": "resmi"}
    assert market_ref.brand_priced(choc) and not market_ref.brand_priced(milk)
    assert market_ref.brand_priced({"category": "Su ve İçecek", "subcategory": "Gazlı İçecek"})
    assert not market_ref.brand_priced({"category": "Temel Gıda", "subcategory": "Un ve İrmik"})
    assert market_ref.comparable(ref, choc, 25.0) and not market_ref.comparable(ref, choc, 46.0)
    assert market_ref.comparable(ref, milk, 46.0)
    assert not market_ref.comparable({**ref, "min": 8.0}, milk)                      # 2,25×
    assert not market_ref.comparable({**ref, "min": None}, choc) and market_ref.comparable({**ref, "min": None}, milk)
    assert not market_ref.comparable(None)
    # vs_market: marka rafında "pahalı" eşiği %30; dağınık bantta karşılaştırma yok
    assert market_ref.compare(18.0, ref, choc)["verdict"] == "normal"                 # +%12,5
    assert market_ref.compare(18.0, ref, milk)["verdict"] == "pahali"
    assert market_ref.compare(18.0, {**ref, "min": 8.0}, milk) is None
    assert market_ref.compare(13.0, ref, choc)["verdict"] == "ucuz"
    # taksonomideki adlar
    assert market_ref.BRAND_PRICED_CATEGORIES <= set(product_groups.CATEGORIES)
    subs = {s for v in product_groups.SUBCATEGORIES.values() for s in v}
    assert market_ref.BRAND_PRICED_SUBCATEGORIES <= subs


def _buy(client, store, items, day=T):
    total = round(sum(i["amount"] for i in items), 2)
    return client.post("/api/entries", json={"entry_date": day, "description": store, "amount": total, "kind": "gider",
                                             "category": "Market & Gıda", "payment": "nakit", "items": items}).json()


def _official(rows):
    with system.connect() as c:
        for chain, g, title, price in rows:
            c.execute("INSERT INTO official_prices (chain, group_key, title, price, day, region) VALUES (?, ?, ?, ?, ?, "
                      "'İstanbul')", (chain, g, title, price, T))


def test_prices_page_no_cheaper_at_or_vs_market_for_heterogeneous_group(client, resmi):
    """Fiyatlar sayfası: çikolata bandı dağınık (₺11 – ₺30) → Kinder Joy'u da düz çikolatayı da "X'te daha ucuz",
    piyasa çizgisi ve vs_market yok; süt (sıkı bant) eskisi gibi."""
    _buy(client, "Migros", [{"name": "Sütlü Çikolata 20 g", "qty": 1, "amount": 42.75},
                            {"name": "Torku Süt 1 L", "qty": 1, "amount": 58.0}])
    _official([("A101", "cikolata|20g", "Örnek Çikolata 20 G", 11.0), ("BİM", "cikolata|20g", "Örnek Bar 20 G", 26.9),
               ("ŞOK", "cikolata|20g", "Örnek Gofret Çikolata 20 G", 30.0),
               ("BİM", "sut|1l", "Örnek Süt 1 L", 31.5), ("A101", "sut|1l", "Örnek Süt 1 L", 34.95),
               ("ŞOK", "sut|1l", "Örnek Süt 1 L", 36.0)])
    prods = {p["group"]["key"]: p for p in client.get("/api/prices").json()["products"] if p.get("group")}
    choc, milk = prods["cikolata|20g"], prods["sut|1l"]
    assert choc["market_ref"] is None and choc["advice"] is None
    assert all("vs_market" not in h for h in choc["history"])
    assert milk["market_ref"]["median"] == 34.95 and milk["advice"]["kind"] == "cheaper_at"
    assert milk["history"][0]["vs_market"]["verdict"] == "pahali"


def test_shopping_savings_skip_dispersed_band(client, resmi):
    from app import shopping_trips

    _daily("cikolata|20g", 11.0, 26.9, 30.0)
    _daily("sut|1l", 33.0, 35.95, 46.7)
    got = shopping_trips._market_units([("cikolata|20g", T), ("sut|1l", T)])
    assert got == {("sut|1l", T): 35.95}
