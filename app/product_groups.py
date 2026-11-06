"""Ürün grupları: markadan bağımsız ürün + boy. "Torku Süt 1L" ve "SÜTAŞ GÜNLÜK SÜT 1L" → anahtar "sut|1l", ad "Süt · 1 L".

Fiyat karşılaştırması (hangi markette daha ucuz, alışveriş listesi, topluluk fiyatları) ürünü markasından bağımsız
gruplar: aynı boydaki süt hangi markadan olursa olsun aynı gruptur.

Çözüm sırası (group_for / groups_for):
1. Kurallar (deterministik, maliyetsiz): ad normalleştirilir (Türkçe harfsiz BÜYÜK harf, rakam-harf ayrılır: "1L" →
   "1 L"), boy okunur (L / ML / CL / LT, KG / GR / G, ADET / 'Lİ, paket "6X200ML" / "200 ML X6"), bilinen markalar
   (BRANDS + kalemin kendi brand alanı + zincir adları) ve tanımlayıcılar ("GÜNLÜK", "UHT", "PET" …) atılır; kalan
   kelimeler GENERICS'teki bir ürünse (tam ya da aroma/çeşit sıfatıyla: "ÇİKOLATALI GOFRET" → Gofret) grup odur.
   Tartılı satırda (unit kg / lt) boy "kg" / "1 L" (birim fiyat kilo/litre fiyatıdır).
2. Önbellek: sunucu geneli product_groups tablosu (system.db; ad → grup, sonsuza dek). Kural sonuçları da yazılır.
3. Yapay zekâ: kuralın çözemediği ad kuyruğa girer (product_group_queue); classify_pending toplu (en çok BATCH ad /
   çağrı) ucuz modelle (receipt.DEFAULT_MODEL, lite) genel ürün adını sorar, JSON alır, sonucu önbelleğe yazar (source
   'ai'). Boyu yine kurallar okur (model yalnız genel adı verir). Günde en çok AI_CALLS_PER_DAY çağrı (sunucu geneli);
   maliyet ai_calls'a kind "grup" olarak yazılır. Modele yalnız ürün adı gider: fiş, işyeri, fiyat, kişi bilgisi gitmez.
   Kuyruk boşalana kadar ad geçici gruptadır (pending: true; aynı yazılış aynı geçici grup).

Saklama şeması app/system.py SCHEMA'dadır (product_groups, product_group_queue).
"""
import logging
import re
import threading
from datetime import date, datetime, timezone

from app.textnorm import normalize

log = logging.getLogger("hesapkitap.product_groups")

BATCH = 50              # yapay zekâ çağrısı başına en çok ad
AI_CALLS_PER_DAY = 20   # sunucu geneli günlük çağrı tavanı (≈ 1000 yeni ad / gün)
QUEUE_MAX_TRIES = 3     # yanıtı bozuk gelen ad en çok bu kadar denenir

# ---------------------------------------------------------------- kurallar

# (görünen ad) — anahtarı name_norm(ad) kelimeleri; çok kelimeli olan önce eşleşir
GENERICS = [
    "Süt", "Kakaolu Süt", "Laktozsuz Süt", "Ayran", "Yoğurt", "Kefir", "Beyaz Peynir", "Kaşar Peyniri", "Peynir",
    "Tereyağı", "Margarin", "Yumurta", "Ekmek", "Tam Buğday Ekmeği", "Lavaş", "Simit", "Un", "Toz Şeker", "Küp Şeker",
    "Şeker", "Tuz", "Pirinç", "Bulgur", "Makarna", "Mercimek", "Nohut", "Fasulye", "Ayçiçek Yağı", "Zeytinyağı",
    "Zeytin", "Bal", "Reçel", "Tahin", "Pekmez", "Çay", "Kahve", "Türk Kahvesi", "Su", "Maden Suyu", "Soda", "Kola",
    "Gazoz", "Meyve Suyu", "Domates", "Salatalık", "Patates", "Soğan", "Elma", "Muz", "Portakal", "Limon", "Tavuk",
    "Kıyma", "Sucuk", "Salam", "Sosis", "Gofret", "Bisküvi", "Kraker", "Çikolata", "Cips", "Dondurma", "Salça",
    "Ketçap", "Mayonez", "Deterjan", "Sıvı Deterjan", "Toz Deterjan", "Bulaşık Deterjanı", "Yumuşatıcı",
    "Çamaşır Suyu", "Tuvalet Kağıdı", "Kağıt Havlu", "Peçete", "Islak Mendil", "Şampuan", "Duş Jeli", "Sabun",
    "Sıvı Sabun", "Diş Macunu", "Bebek Bezi", "Mama", "Kedi Maması", "Köpek Maması", "Latte", "Americano", "Espresso",
    "Cappuccino", "Filtre Kahve", "Motorin", "Benzin",
]
# aynı ürünün başka yazılışı → GENERICS'teki ad
SYNONYMS = {"KASAR": "Kaşar Peyniri", "KASAR PEYNIR": "Kaşar Peyniri", "BEYAZ PEYNIRI": "Beyaz Peynir",
            "TEREYAG": "Tereyağı", "TEREYAGI": "Tereyağı", "ZEYTINYAG": "Zeytinyağı", "ZEYTIN YAGI": "Zeytinyağı",
            "AYCICEK YAG": "Ayçiçek Yağı", "AYCICEKYAGI": "Ayçiçek Yağı", "YUMURTASI": "Yumurta", "EKMEGI": "Ekmek",
            "SUTU": "Süt", "DOGAL KAYNAK SUYU": "Su", "KAYNAK SUYU": "Su", "ICME SUYU": "Su",
            "MADEN SU": "Maden Suyu", "COLA": "Kola", "TUVALET KAGIT": "Tuvalet Kağıdı", "HAVLU KAGIT": "Kağıt Havlu",
            "KAGIT HAVLUSU": "Kağıt Havlu", "BEBEK BEZ": "Bebek Bezi", "DIS MACUN": "Diş Macunu", "BULASIK DETERJAN":
            "Bulaşık Deterjanı", "DOMATES SALCASI": "Salça", "BISKUVISI": "Bisküvi", "DIZEL": "Motorin",
            "KURSUNSUZ BENZIN": "Benzin", "KURSUNSUZ": "Benzin"}
# genel adın önünde grubu değiştirmeyen sıfat (aroma, çeşit): "ÇİKOLATALI GOFRET" → Gofret
MODIFIERS = {"CIKOLATALI", "KAKAOLU", "FINDIKLI", "FISTIKLI", "CILEKLI", "VISNELI", "MUZLU", "LIMONLU", "PORTAKALLI",
             "SADE", "MEYVELI", "KREMALI", "SUTLU", "BITTER", "BEYAZ", "SIYAH", "YESIL", "KIRMIZI", "TAM", "YAGLI",
             "YARIM", "AZ", "LIGHT", "ZERO", "SEKERSIZ", "TUZSUZ", "TUZLU", "KAVRULMUS", "KUCUK", "BUYUK", "ORTA",
             "MINI", "DILIM", "DILIMLI", "RENDE", "TAZE", "SICAK", "SOGUK", "BUZLU", "IRI", "INCE", "KALIN", "UZUN",
             "KISA", "YERLI", "ITHAL", "ORGANIK", "KOYU", "ACIK", "DOGRANMIS", "BUTUN", "PARCA", "BONFILE", "BAGET"}
# boy, marka değil ama grubu da değiştirmeyen kelimeler (ambalaj, kampanya, raf dili)
DESCRIPTORS = {"GUNLUK", "UHT", "PASTORIZE", "KLASIK", "DOGAL", "YENI", "EKO", "EKONOMIK", "AVANTAJ", "AVANTAJLI",
               "PAKET", "PAKETI", "PET", "CAM", "KUTU", "SISE", "POSET", "FIRSAT", "SUPER", "EXTRA", "EKSTRA",
               "ORIJINAL", "ADET", "ADT", "AD", "KG", "GR", "G", "ML", "L", "LT", "CL", "X", "KOLI", "AILE", "BOY",
               "EKONOMI", "TEKLI", "IKILI", "UCLU", "LI", "LU", "LIK", "LUK", "PKT", "KAMPANYA", "INDIRIMLI"}
# bilinen markalar (normalize() biçiminde; yalnız atılmak için — gruplama markadan bağımsızdır)
BRANDS = {"TORKU", "SUTAS", "PINAR", "ULKER", "ETI", "SEK", "ICIM", "MIS", "TIKVESLI", "DANONE", "NESTLE", "COCA",
          "PEPSI", "ULUDAG", "ERIKLI", "HAYAT", "SAKA", "BEYPAZARI", "KIZILAY", "LIPTON", "DOGADAN", "CAYKUR", "FILIZ",
          "BARILLA", "NUHUN", "ANKARA", "KOMILI", "YUDUM", "ORKIDE", "KRISTAL", "TAT", "TAMEK", "DIMES", "CAPPY",
          "FAIRY", "PRIL", "OMO", "ARIEL", "PERSIL", "BINGO", "ALO", "FAMILIA", "SELPAK", "SOLO", "PAPIA", "COLGATE",
          "SIGNAL", "IPANA", "ELIDOR", "DOVE", "NIVEA", "PRIMA", "MOLFIX", "SLEEPY", "PEYMAN", "TADIM", "OZMO",
          "NUTELLA", "MILKA", "KENT", "HARIBO", "LAYS", "DORITOS", "RUFFLES", "PATOS", "CEREZZA", "ALGIDA", "MAGNUM",
          "MARMARABIRLIK", "TARIS", "KOROGLU", "DARDANEL", "SUPERFRESH", "YAYLA", "DUYU", "BALIRAN", "ARBELLA",
          "CALVE", "HELLMANNS", "TUKAS", "OBA", "BEREKET", "BANVIT", "KEREVITAS", "NAMET", "APIKOGLU", "SANA", "BECEL",
          "FINISH", "VERNEL", "ACE", "DOMESTOS", "CIF", "VIM", "PANTENE", "PROTEX", "DALAN", "JOHNSONS", "HUGGIES",
          "PROPLAN", "WHISKAS", "FELIX", "PEDIGREE", "PURINA", "NESCAFE", "JACOBS", "KURUKAHVECI", "MEHMET", "EFENDI",
          "TCHIBO", "LAVAZZA"}
_UNITS = {"KG": ("g", 1000), "KILO": ("g", 1000), "GR": ("g", 1), "G": ("g", 1), "GRAM": ("g", 1),
          "ML": ("ml", 1), "CL": ("ml", 10), "LT": ("ml", 1000), "L": ("ml", 1000), "LITRE": ("ml", 1000),
          "ADET": ("ad", 1), "AD": ("ad", 1), "ADT": ("ad", 1), "LI": ("ad", 1), "LU": ("ad", 1), "LIK": ("ad", 1),
          "LUK": ("ad", 1), "RULO": ("ad", 1), "YAPRAK": ("ad", 1)}
_NUM = re.compile(r"^\d+(?:[.,]\d+)?$")


def name_norm(name: str | None) -> str:
    """'SÜTAŞ GÜNLÜK SÜT 1L' → 'SUTAS GUNLUK SUT 1 L', '6X200ML' → '6 X 200 ML', "10'LU" → '10 LU'. Ondalık virgül ve
    nokta rakamlar arasında kalır ('1,5 L'); geri kalan noktalama boşluk olur. Önbellek anahtarı (en çok 80 harf)."""
    n = normalize(name or "")
    n = re.sub(r"(?<=\d)(?=[A-Z])|(?<=[A-Z])(?=\d)", " ", n)
    n = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", n)
    n = re.sub(r"[^A-Z0-9., ]", " ", n)
    return " ".join(n.split())[:80]


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_size(tokens: list[str]) -> tuple[dict | None, set[int]]:
    """Kelimelerden boy: ({"value": tanesi (temel birimde), "base": ml|g|ad, "pack": adet}, kullanılan sıralar)."""
    used: set[int] = set()
    for i in range(len(tokens) - 1):
        if _NUM.match(tokens[i]) and tokens[i + 1] in _UNITS:
            base, mult = _UNITS[tokens[i + 1]]
            value, pack = _num(tokens[i]) * mult, 1
            used |= {i, i + 1}
            if i >= 2 and tokens[i - 1] == "X" and tokens[i - 2].isdigit():  # 6 X 200 ML
                pack = int(tokens[i - 2])
                used |= {i - 2, i - 1}
            elif i + 3 < len(tokens) and tokens[i + 2] == "X" and tokens[i + 3].isdigit():  # 200 ML X 6
                pack = int(tokens[i + 3])
                used |= {i + 2, i + 3}
            if value <= 0 or not 1 <= pack <= 500:
                return None, used
            return {"value": value, "base": base, "pack": pack}, used
    for i in range(len(tokens) - 1):  # yalnız paket: "YUMURTA X 30", "30 X"
        if tokens[i] == "X" and tokens[i + 1].isdigit() and 1 < int(tokens[i + 1]) <= 500:
            return {"value": 1.0, "base": "ad", "pack": int(tokens[i + 1])}, {i, i + 1}
    return None, used


def _fmt(v: float) -> str:
    return f"{v:g}"


def size_key(size: dict) -> tuple[str, str, float, str]:
    """(anahtar parçası "1l" / "500ml" / "6x200ml" / "10ad", görünen "1 L" / "6×200 ml" / "10 adet", toplam değer, birim)."""
    v, base, pack = size["value"], size["base"], size.get("pack") or 1
    if base == "ad":
        n = v * pack
        return f"{_fmt(n)}ad", f"{_fmt(n)} adet", n, "ad"
    big = v >= 1000
    unit, shown = ("l", "L") if base == "ml" and big else ("ml", "ml") if base == "ml" else ("kg", "kg") if big else ("g", "g")
    val = v / 1000 if big else v
    key = f"{_fmt(val)}{unit}"
    label = f"{_fmt(val).replace('.', ',')} {shown}"
    total = val * pack
    if pack > 1:
        return f"{pack}x{key}", f"{pack}×{label}", total, unit
    return key, label, total, unit


def slug(s: str) -> str:
    """'Beyaz Peynir' → 'beyaz-peynir' (Türkçe harfsiz, küçük)."""
    return "-".join(re.sub(r"[^A-Z0-9]", " ", normalize(s)).lower().split())[:40]


def _phrase(label: str) -> tuple[str, ...]:
    return tuple(name_norm(label).split())


_GENERIC = {_phrase(g): g for g in GENERICS} | {tuple(k.split()): v for k, v in SYNONYMS.items()}


def _strip(tokens: list[str], used: set[int], brands: set[str]) -> list[str]:
    return [t for i, t in enumerate(tokens) if i not in used and t not in brands and t not in DESCRIPTORS
            and not re.fullmatch(r"[\d.,]+", t) and len(t) > 1]


def _generic(rest: list[str]) -> str | None:
    """Kalan kelimeler bir genel ürün mü: tam eşleşme, yoksa sondaki genel ad + önündeki sıfatlar (MODIFIERS)."""
    if not rest:
        return None
    if (g := _GENERIC.get(tuple(rest))):
        return g
    for k in range(1, min(3, len(rest)) + 1):
        if (g := _GENERIC.get(tuple(rest[-k:]))) and all(t in MODIFIERS for t in rest[:-k]):
            return g
    return None


def _brand_words(*names: str | None) -> set[str]:
    from app.receipt_templates import CHAINS

    out = set(BRANDS)
    for n in names:
        out |= set(name_norm(n).split()) if n else set()
    for b, keys, _ in CHAINS:
        out |= set(name_norm(b).split())
    return out


def _compose(generic_label: str, size: dict | None, weighed: str | None = None) -> dict:
    """{key, label, size_value, size_unit} — tartılı satırda (kg / lt) boy birim fiyatın birimi."""
    s = slug(generic_label)
    if weighed == "kg":
        return {"key": f"{s}|kg", "label": f"{generic_label} · kg", "size_value": 1.0, "size_unit": "kg"}
    if weighed == "lt":
        return {"key": f"{s}|1l", "label": f"{generic_label} · 1 L", "size_value": 1.0, "size_unit": "l"}
    if not size:
        return {"key": s, "label": generic_label, "size_value": None, "size_unit": None}
    k, shown, total, unit = size_key(size)
    return {"key": f"{s}|{k}", "label": f"{generic_label} · {shown}", "size_value": total, "size_unit": unit}


def rule_group(name: str, brand: str | None = None, unit: str | None = None) -> dict | None:
    """Yalnız kurallarla grup ({key, label, size_value, size_unit}); çözemezse None."""
    tokens = name_norm(name).split()
    size, used = parse_size(tokens)
    g = _generic(_strip(tokens, used, _brand_words(brand)))
    return _compose(g, size, unit if unit in ("kg", "lt") else None) if g else None


def _provisional(name: str, unit: str | None) -> dict | None:
    """Yapay zekâ beklenirken: markası ve boyu atılmış adın kendisi (aynı yazılış aynı geçici grup)."""
    tokens = name_norm(name).split()
    size, used = parse_size(tokens)
    rest = _strip(tokens, used, _brand_words())
    if not rest:
        return None
    label = " ".join(w.capitalize() for w in rest)[:40]
    return {**_compose(label, size, unit if unit in ("kg", "lt") else None), "pending": True}


# ---------------------------------------------------------------- önbellek ve kuyruk (system.db)

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _store(c, nn: str, g: dict | None, source: str) -> None:
    c.execute("INSERT OR REPLACE INTO product_groups (name_norm, group_key, label, size_value, size_unit, source, "
              "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (nn, (g or {}).get("key") or "", (g or {}).get("label") or "", (g or {}).get("size_value"),
               (g or {}).get("size_unit"), source, _now()))


def _public(g: dict | None) -> dict | None:
    if not g or not g.get("key"):
        return None
    return {"key": g["key"], "label": g["label"], **({"pending": True} if g.get("pending") else {})}


def groups_for(items: list[dict], learn: bool = True) -> list[dict | None]:
    """Satırların grupları ({key, label[, pending]} ya da ürün değilse None), sırayla. Satır: {name, product?, brand?,
    unit?}. Ad olarak product (modelin standart adı) varsa o, yoksa name. Kural çözerse önbelleğe yazılır; çözemezse
    önbellekteki yapay zekâ sonucu, o da yoksa geçici grup (learn: ad kuyruğa girer)."""
    from app import system

    out: list[dict | None] = [None] * len(items)
    miss: dict[str, list[int]] = {}
    fresh: dict[str, dict] = {}
    for i, it in enumerate(items):
        name = (it.get("product") or it.get("name") or "").strip()
        if not name:
            continue
        unit = it.get("unit")
        if (g := rule_group(name, it.get("brand"), unit)):
            out[i] = g
            fresh.setdefault(name_norm(name), rule_group(name, it.get("brand")) or g)
        else:
            miss.setdefault(name_norm(name), []).append(i)
    if not miss and not fresh:
        return [_public(g) for g in out]
    with system.connect() as c:
        if fresh:
            have = {r[0] for r in c.execute(f"SELECT name_norm FROM product_groups WHERE name_norm IN "
                                            f"({','.join('?' * len(fresh))})", list(fresh))}
            for nn, g in fresh.items():
                if nn not in have:
                    _store(c, nn, g, "rule")
        cached = {}
        if miss:
            cached = {r["name_norm"]: dict(r) for r in c.execute(
                f"SELECT * FROM product_groups WHERE name_norm IN ({','.join('?' * len(miss))})", list(miss))}
        for nn, idx in miss.items():
            row = cached.get(nn)
            for i in idx:
                it = items[i]
                unit = it.get("unit") if it.get("unit") in ("kg", "lt") else None
                if row is not None:
                    if not row["group_key"]:
                        continue  # yapay zekâ: ürün değil (poşet, hizmet)
                    generic = row["label"].split(" · ")[0]
                    tokens = nn.split()
                    size, _ = parse_size(tokens)
                    out[i] = _compose(generic, size, unit) if unit else {"key": row["group_key"], "label": row["label"]}
                else:
                    out[i] = _provisional(it.get("product") or it.get("name") or "", unit)
            if row is None and learn:
                name = (items[idx[0]].get("product") or items[idx[0]].get("name") or "").strip()
                c.execute("INSERT OR IGNORE INTO product_group_queue (name_norm, name, added_at) VALUES (?, ?, ?)",
                          (nn, name[:80], _now()))
    return [_public(g) for g in out]


def group_for(name: str, product: str | None = None, brand: str | None = None, unit: str | None = None,
              learn: bool = True) -> dict | None:
    return groups_for([{"name": name, "product": product, "brand": brand, "unit": unit}], learn)[0]


def labels(keys: list[str]) -> dict[str, str]:
    """Grup anahtarı → görünen ad (önbellekten; topluluk fiyatlarının adı için)."""
    from app import system

    keys = sorted({k for k in keys if k})
    if not keys:
        return {}
    with system.connect() as c:
        return {r[0]: r[1] for r in c.execute(
            f"SELECT group_key, MAX(label) FROM product_groups WHERE group_key IN ({','.join('?' * len(keys))}) "
            "GROUP BY group_key", keys)}


def pending() -> int:
    from app import system

    with system.connect() as c:
        return c.execute("SELECT COUNT(*) FROM product_group_queue").fetchone()[0]


# ---------------------------------------------------------------- yapay zekâ (toplu)

PROMPT = """Türkiye'deki market fişlerinden gelen ürün adlarını MARKADAN BAĞIMSIZ genel ürün adına çevir.
Kurallar: g = Türkçe, kısa (1-3 kelime), ilk harfleri büyük genel ürün adı ("Süt", "Beyaz Peynir", "Bulaşık Deterjanı");
marka, boy/gramaj, aroma ve ambalaj YAZMA. Kısaltmaları aç ("CIK.GOFRET" → "Gofret"). Ürün değilse (poşet, indirim,
hizmet, depozito) g = null. Her ad için bir satır, sırayı koru.
Yanıt yalnız JSON: {"items": [{"i": 0, "g": "Süt"}, ...]}
Adlar:
"""
_LOCK = threading.Lock()


def _day_key() -> str:
    return f"price_groups_ai:{date.today().isoformat()}"


def ai_calls_today() -> int:
    from app import system

    try:
        return int(system.get(_day_key()) or 0)
    except ValueError:
        return 0


def _valid(g) -> str | None:
    if not isinstance(g, str):
        return None
    g = " ".join(g.split())[:40]
    return g if re.fullmatch(r"[A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû' .&-]{2,40}", g) else None


def classify_pending(max_calls: int = 1) -> int:
    """Kuyruktaki adları toplu sınıflandır (en çok max_calls çağrı, günlük tavan AI_CALLS_PER_DAY). Dönen: önbelleğe
    yazılan ad sayısı. Yapay zekâ kapalıysa (anahtar yok) 0. Bir çağrının hatası kuyruğu bozmaz (deneme sayısı artar)."""
    from app import community_prices, receipt, system

    if not receipt.enabled() or not _LOCK.acquire(blocking=False):
        return 0
    done = 0
    try:
        for _ in range(max_calls):
            if ai_calls_today() >= AI_CALLS_PER_DAY:
                break
            with system.connect() as c:
                batch = [dict(r) for r in c.execute(
                    "SELECT name_norm, name FROM product_group_queue WHERE tries < ? ORDER BY added_at, name_norm LIMIT ?",
                    (QUEUE_MAX_TRIES, BATCH))]
                if not batch:
                    break
                c.execute(f"UPDATE product_group_queue SET tries = tries + 1 WHERE name_norm IN "
                          f"({','.join('?' * len(batch))})", [b["name_norm"] for b in batch])
            system.put(_day_key(), str(ai_calls_today() + 1))
            names = "\n".join(f"{i}. {b['name']}" for i, b in enumerate(batch))
            try:
                data, _meta = receipt.gemini_text_json(PROMPT + names, max_output_tokens=2048, kind="grup",
                                                       model=receipt.DEFAULT_MODEL)
            except Exception:  # noqa: BLE001 - ağ / model hatası: adlar kuyrukta kalır, sonra yeniden denenir
                log.warning("ürün grubu yapay zekâ çağrısı başarısız", exc_info=True)
                break
            got = {}
            for row in data.get("items") or [] if isinstance(data, dict) else []:
                if isinstance(row, dict) and isinstance(row.get("i"), int) and 0 <= row["i"] < len(batch):
                    got[row["i"]] = row.get("g")
            with system.connect() as c:
                for i, b in enumerate(batch):
                    if i not in got:
                        continue  # yanıtta yok: kuyrukta kalır (deneme sayısı arttı)
                    g = _valid(got[i])
                    size, _ = parse_size(b["name_norm"].split())
                    grp = _compose(g, size) if g else None
                    _store(c, b["name_norm"], grp, "ai")
                    c.execute("DELETE FROM product_group_queue WHERE name_norm = ?", (b["name_norm"],))
                    community_prices.relabel(c, b["name_norm"], grp)  # geçici gruptaki topluluk satırları
                    done += 1
    finally:
        _LOCK.release()
    return done


def kick() -> None:
    """Kuyrukta ad varsa arka planda tek çağrı (istek yolu yavaşlamaz). Yapay zekâ kapalıysa hiçbir şey."""
    from app import receipt

    if not receipt.enabled():
        return
    try:
        if not pending():
            return
    except Exception:  # noqa: BLE001
        return
    threading.Thread(target=lambda: _safe(classify_pending, 1), daemon=True, name="product-groups").start()


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        log.exception("ürün grubu sınıflandırma")
        return 0
