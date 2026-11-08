"""Ürün grupları: markadan bağımsız ürün + boy. "Torku Süt 1L" ve "SÜTAŞ GÜNLÜK SÜT 1L" → anahtar "sut|1l", ad "Süt · 1 L".

Fiyat karşılaştırması (hangi markette daha ucuz, alışveriş listesi, topluluk fiyatları) ürünü markasından bağımsız
gruplar: aynı boydaki süt hangi markadan olursa olsun aynı gruptur.

Çözüm sırası (group_for / groups_for):
1. Kurallar (deterministik, maliyetsiz): ad normalleştirilir (Türkçe harfsiz BÜYÜK harf, rakam-harf ayrılır: "1L" →
   "1 L"), fiş kısaltmaları açılır ("FISTI." → FISTIK…, yalnız tek anlamlıysa), boy okunur (L / ML / CL / LT,
   KG / GR / G, ADET / 'Lİ, paket "6X200ML" / "200 ML X6"), bilinen markalar (BRANDS + app/brands_tr.txt + kalemin
   kendi brand alanı + zincir adları; çok kelimeli marka bitişik geçince) ve tanımlayıcılar ("GÜNLÜK", "UHT", "PET" …)
   atılır; kalan kelimelerde sözlükteki (app/product_taxonomy.py) bir genel ürün aranır: güçlü ad, en sağdaki, en
   uzun ("TAVUK ÇORBASI" → Çorba, "BEYAZ PEYNİR" > Peynir). Kalan her kelime bilinen bir sıfat / nitelik / sözlük
   kelimesi olmalı; bilinmeyen kelime adı yapay zekâya bırakır. Aroma süt ürününde ayrı grup (refine).
   Tartılı satırda (unit kg / lt) boy "kg" / "1 L" (birim fiyat kilo/litre fiyatıdır). Her grubun ana kategorisi
   (CATEGORIES) ve alt kategorisi (TAXONOMY: "Süt ve Kahvaltılık › Peynir") vardır.
2. Önbellek: sunucu geneli product_groups tablosu (system.db; ad → grup, sonsuza dek). Kural sonuçları da yazılır.
3. Yapay zekâ: kuralın çözemediği ad kuyruğa girer (product_group_queue); classify_pending toplu (en çok BATCH ad /
   çağrı) ucuz modelle (receipt.DEFAULT_MODEL, lite) genel ürün adını sorar, JSON alır, sonucu önbelleğe yazar (source
   'ai'). Boyu yine kurallar okur (model yalnız genel adı verir). Günde en çok AI_CALLS_PER_DAY çağrı (sunucu geneli);
   maliyet ai_calls'a kind "grup" olarak yazılır. Modele yalnız ürün adı gider: fiş, işyeri, fiyat, kişi bilgisi gitmez.
   Kuyruk boşalana kadar ad geçici gruptadır (pending: true; aynı yazılış aynı geçici grup).
   Alışveriş listesine yazılan ad beklemez (classify_names / kick_names): eklenince arka planda yalnız o adlar tek
   çağrıda sorulur (kişi başı ve sunucu geneli günlük sınırlarla; önbellekteki ad hiç sorulmaz).

Saklama şeması app/system.py SCHEMA'dadır (product_groups, product_group_queue).
"""
import logging
import re
import threading
from datetime import date, datetime, timezone

from app.product_taxonomy import BRAND_PRODUCTS, PACKAGING, QUALIFIERS, TAXONOMY
from app.textnorm import normalize

log = logging.getLogger("hesapkitap.product_groups")

BATCH = 50              # yapay zekâ çağrısı başına en çok ad
AI_CALLS_PER_DAY = 20   # sunucu geneli günlük çağrı tavanı (≈ 1000 yeni ad / gün)
QUEUE_MAX_TRIES = 3     # yanıtı bozuk gelen ad en çok bu kadar denenir

# ---------------------------------------------------------------- kurallar

# Genel ürün sözlüğü ve kategori ağacı app/product_taxonomy.py'dedir (TAXONOMY: ana › alt → genel adlar + yazılışlar).
# genel adın önünde grubu değiştirmeyen sıfat (aroma, çeşit): "ÇİKOLATALI GOFRET" → Gofret
MODIFIERS = {"CIKOLATALI", "KAKAOLU", "FINDIKLI", "FISTIKLI", "CILEKLI", "VISNELI", "MUZLU", "LIMONLU", "PORTAKALLI",
             "SADE", "MEYVELI", "KREMALI", "SUTLU", "BITTER", "BEYAZ", "SIYAH", "YESIL", "KIRMIZI", "TAM", "YAGLI",
             "YARIM", "AZ", "LIGHT", "ZERO", "SEKERSIZ", "TUZSUZ", "TUZLU", "KAVRULMUS", "KUCUK", "BUYUK", "ORTA",
             "MINI", "DILIM", "DILIMLI", "RENDE", "TAZE", "SICAK", "SOGUK", "BUZLU", "IRI", "INCE", "KALIN", "UZUN",
             "KISA", "YERLI", "ITHAL", "ORGANIK", "KOYU", "ACIK", "DOGRANMIS", "BUTUN", "PARCA", "BONFILE", "BAGET",
             "VANILYALI", "KARAMELLI"}
# Ürünü değiştiren çeşit (yalnız VARIANT_BASES'teki içecek / süt ürününde): aromalı süt, laktozsuz, keçi, protein ayrı
# gruptur ("ÇİKOLATALI GÜNLÜK SÜT 200 ML" → Çikolatalı Süt · 200 ml). Yağ oranı (tam / yarım yağlı, light) aynı grupta
# kalır (Süt). Kelime → (öncelik: küçük önce, görünen sıfat). "Kakaolu Süt" sözlükte zaten var; aynı ada çıkar.
VARIANTS = {"WHEY": (0, "Protein"), "PROTEIN": (0, "Protein"), "PROTEINLI": (0, "Protein"),
            "CIKOLATALI": (1, "Çikolatalı"), "CIKOLATA": (1, "Çikolatalı"), "KAKAOLU": (1, "Kakaolu"),
            "KAKAO": (1, "Kakaolu"), "CILEKLI": (1, "Çilekli"), "CILEK": (1, "Çilekli"), "MUZLU": (1, "Muzlu"),
            "MUZ": (1, "Muzlu"), "VANILYALI": (1, "Vanilyalı"), "KARAMELLI": (1, "Karamelli"),
            "VISNELI": (1, "Vişneli"), "MEYVELI": (1, "Meyveli"), "MEYVE": (1, "Meyveli"),
            "KAHVELI": (1, "Kahveli"), "PROTEINI": (0, "Protein"), "LAKTOZSUZ": (2, "Laktozsuz"),
            "KECI": (3, "Keçi")}
VARIANT_BASES = {"Süt", "Kefir", "Yoğurt", "Ayran", "Kakaolu Süt", "Laktozsuz Süt"}
# çeşitli adın özel yazılışı (yoksa "<sıfat> <taban>")
_VARIANT_NAMES = {("Protein", "Süt"): "Protein İçeceği", ("Keçi", "Süt"): "Keçi Sütü",
                  ("Protein", "Yoğurt"): "Proteinli Yoğurt", ("Protein", "Kefir"): "Proteinli Kefir"}
# boy, marka değil ama grubu da değiştirmeyen kelimeler (ambalaj, kampanya, raf dili)
DESCRIPTORS = {"GUNLUK", "UHT", "PASTORIZE", "KLASIK", "DOGAL", "YENI", "EKO", "EKONOMIK", "AVANTAJ", "AVANTAJLI",
               "PAKET", "PAKETI", "PET", "CAM", "KUTU", "SISE", "POSET", "FIRSAT", "SUPER", "EXTRA", "EKSTRA",
               "ORIJINAL", "ADET", "ADT", "AD", "KG", "GR", "G", "ML", "L", "LT", "CL", "X", "KOLI", "AILE", "BOY",
               "EKONOMI", "TEKLI", "IKILI", "UCLU", "LI", "LU", "LIK", "LUK", "PKT", "KAMPANYA",
               "INDIRIMLI"} | PACKAGING
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
# Kategori katmanı: sabit, kısa liste (Türkçe genel adlar; istemciler ve kayıtlı satırlar bu adları taşır). Her birinin
# alt kategorileri TAXONOMY'de ("Süt ve Kahvaltılık › Peynir"). Kural sözlükten, yapay zekâ listeden seçer.
CATEGORIES = [
    "Süt ve Kahvaltılık", "Fırın ve Unlu Mamul", "Temel Gıda", "Yağ", "Meyve ve Sebze", "Et, Tavuk ve Balık",
    "Şarküteri", "Su ve İçecek", "Çay ve Kahve", "Kafe", "Atıştırmalık", "Dondurma ve Tatlı", "Sos ve Baharat",
    "Konserve ve Hazır Gıda", "Dondurulmuş Gıda", "Bebek", "Evcil Hayvan", "Temizlik", "Kağıt Ürünleri",
    "Kişisel Bakım", "Sağlık", "Ev ve Mutfak", "Elektronik", "Akaryakıt", "Tütün", "Alkollü İçecek", "Kırtasiye",
    "Diğer",
]
SUBCATEGORIES: dict[str, list[str]] = {c: list(TAXONOMY.get(c, {})) for c in CATEGORIES}
SEP = " › "


def _parse_taxonomy():
    """TAXONOMY satırları → (eşleşen yazılış → genel ad, zayıf yazılışlar, genel ad → (ana, alt),
    eşleşen genel adlar)."""
    phrases: dict[tuple[str, ...], str] = {}
    weak: set[tuple[str, ...]] = set()
    path: dict[str, tuple[str, str]] = {}
    generics: list[str] = []
    for top, subs in TAXONOMY.items():
        for sub, entries in subs.items():
            for e in entries:
                label, _, syns = e.partition("=")
                only_cat, label = label.startswith("~"), label.lstrip("~")
                is_weak, label = label.startswith("?"), label.lstrip("?")
                path[label] = (top, sub)
                if only_cat:
                    continue
                generics.append(label)
                for raw in ([label, *(x for x in syns.split(",") if x.strip())]):
                    w = raw.strip().startswith("?")
                    key = tuple(name_norm(raw.strip().lstrip("?")).split())
                    phrases.setdefault(key, label)
                    if is_weak or w:
                        weak.add(key)
    return phrases, weak, path, generics


def category_for(generic: str | None) -> str | None:
    """Genel ad → ana kategori (bilinen genel adlar; çeşitli ad tabanından: "Çilekli Süt" → Süt ve Kahvaltılık)."""
    return (path_for(generic) or (None, None))[0]


def subcategory_for(generic: str | None) -> str | None:
    """Genel ad → alt kategori ("Beyaz Peynir" → Peynir, "Çilekli Yoğurt" → Yoğurt)."""
    return (path_for(generic) or (None, None))[1]


def path_for(generic: str | None) -> tuple[str, str] | None:
    if not generic:
        return None
    if (p := GENERIC_PATH.get(generic)):
        return p
    if generic.startswith("Dondurulmuş ") and generic[12:] in GENERIC_PATH:
        return "Dondurulmuş Gıda", "Dondurulmuş Gıda"
    for base in ("Süt", "Yoğurt", "Kefir", "Ayran"):
        if generic.endswith(f" {base}"):
            return GENERIC_PATH[base]
    return None


def valid_category(c) -> str | None:
    return c if isinstance(c, str) and c in _CATSET else None


def valid_subcategory(category: str | None, s) -> str | None:
    """Yapay zekânın alt kategorisi ana kategorinin altında mı ("Temel Gıda", "Bakliyat" → "Bakliyat")."""
    return s if category and isinstance(s, str) and s in SUBCATEGORIES.get(category, ()) else None


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


def query_parts(q: str | None) -> tuple[str, str | None]:
    """Arama metni → (kelimelerin slug'ı, boy anahtarı ya da None): "1l süt" / "süt 1 lt" → ("sut", "1l"),
    "Süt" → ("sut", None), "sut|1l" (grup anahtarı) → ("sut", "1l")."""
    q = (q or "").strip()
    if "|" in q:
        base, _, sz = q.partition("|")
        return base, sz or None
    tokens = name_norm(q).split()
    size, used = parse_size(tokens)
    if not size:  # "süt 1" gibi birimsiz rakam boy değildir; sade kelime
        return slug(" ".join(t for t in tokens if not re.fullmatch(r"[\d.,]+", t))), None
    words = [t for i, t in enumerate(tokens) if i not in used]
    return slug(" ".join(words)), size_key(size)[0]


def _phrase(label: str) -> tuple[str, ...]:
    return tuple(name_norm(label).split())


_GENERIC, _WEAK, GENERIC_PATH, GENERICS = _parse_taxonomy()
_BY_SLUG = {slug(g): g for g in GENERIC_PATH}
# eski adlar (geriye uyum): aynı ürünün başka yazılışı → genel ad; genel ad → ana kategori
SYNONYMS = {" ".join(k): v for k, v in _GENERIC.items() if k != _phrase(v)}
GENERIC_CATEGORY = {g: p[0] for g, p in GENERIC_PATH.items()}
GENERIC_SUBCATEGORY = {g: p[1] for g, p in GENERIC_PATH.items()}
_CATSET = set(CATEGORIES)
_MAX_PHRASE = max(len(k) for k in _GENERIC)
# genel ürünün yanında bulunabilecek bilinen kelimeler: sözlükteki her kelime, sıfatlar, çeşitler, nitelikler
_VOCAB = {w for k in _GENERIC for w in k} | MODIFIERS | set(VARIANTS) | QUALIFIERS
# sözlükte olmasa da sıfat / ortaç ekiyle biten kelime ürünü niteler, ürünün kendisi değildir ("BİBERİYELİ",
# "TÜTSÜLENMİŞ", "GLUTENSİZ", "TAVALIK")
_ADJ = re.compile(r"(LI|LU|SIZ|SUZ|MIS|MUS|LIK|LUK)$")
# grubu değiştirmeyen kelime öbekleri (atılır): "ŞEKER İLAVESİZ REÇEL" şeker değildir
_DROP = {("SEKER", "ILAVESIZ"), ("SEKER", "ILAVE"), ("ILAVE", "SEKERSIZ"), ("SEKER", "ILAVESI"), ("ILAVE", "SEKER")}


# Adında genel ürün kelimesi geçen marka ("KAHVE DÜNYASI" çikolata, gofret de satar): marka atılınca ürün kalmasa bile
# markanın kelimesi ürün sayılmaz (kalanla çözülür ya da katalog / yapay zekâya kalır). "ÇATAL SUCUK" gibi ürünü
# kendi adında olan markalar burada değildir.
_NAME_ONLY_BRANDS = {("KAHVE", "DUNYASI")}
# renk adı (saç boyası "AÇIK KAHVE", "KOYU KAHVE"): ürün kelimesi değil
_DROP |= {("ACIK", "KAHVE"), ("KOYU", "KAHVE"), ("KAHVE", "RENGI")}
_NOT_ADJ = {"RAVIOLI", "BROCCOLI", "CHILLI", "CHILI", "TORTELLINI", "VERMICELLI", "PANNACOTTA"}


def _known(t: str) -> bool:
    return t in _VOCAB or (len(t) >= 5 and bool(_ADJ.search(t)) and t not in _NOT_ADJ)


def _load_brands() -> tuple[set[str], dict[str, list[tuple[str, ...]]]]:
    """app/brands_tr.txt + BRANDS + zincir adları → (tek kelimelik markalar, ilk kelime → çok kelimeli markalar).
    Sözlükte geçen tek kelimelik marka ("NAR", "SADE") atılmaz: ürünün kendisini anlatıyor olabilir."""
    from pathlib import Path

    from app.receipt_templates import CHAINS

    names = set(BRANDS)
    try:
        text = (Path(__file__).parent / "brands_tr.txt").read_text(encoding="utf-8")
        names |= {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}
    except OSError:  # pragma: no cover - dosya paketle gelir; yoksa yalnız gömülü liste
        log.warning("marka listesi okunamadı (app/brands_tr.txt)")
    names |= {b for b, _, _ in CHAINS}
    single: set[str] = set()
    multi: dict[str, list[tuple[str, ...]]] = {}
    for n in names:
        toks = tuple(name_norm(n).split())
        if len(toks) == 1 and toks[0] not in _VOCAB and toks[0] not in DESCRIPTORS:
            single.add(toks[0])
        elif len(toks) > 1:
            multi.setdefault(toks[0], []).append(toks)
    for v in multi.values():
        v.sort(key=len, reverse=True)
    return single, multi


_BRAND_SINGLE, _BRAND_MULTI = _load_brands()
# Fişte kısaltılmış kelime ("ANTEP FISTI.", "OLGUN. GRAVY. PEY", "TAHSI. KAŞAR"): sözlükte tek anlamlı açılımı varsa
# ona açılır (adaylar birbirinin uzantısıysa en kısası: PEYNI → PEYNIR; DOND → DONDURMA / DONDURULMUŞ belirsiz,
# açılmaz), değilse bir markanın baş harfleriyse marka sayılır. Yalnız 4+ harf (marka için 5+).
_PREFIX_POOL: dict[str, list[str]] = {}
for _w in sorted(_VOCAB | DESCRIPTORS, key=lambda w: (len(w), w)):
    if len(_w) >= 5:
        _PREFIX_POOL.setdefault(_w[:4], []).append(_w)
_BRAND_TOKENS = _BRAND_SINGLE | {t for v in _BRAND_MULTI.values() for ph in v for t in ph if t not in _VOCAB}
_BRAND_PREFIX: set[str] = {t[:k] for t in _BRAND_TOKENS if len(t) > 5 for k in range(5, len(t))}
_BASE_BRANDS = (set(_BRAND_SINGLE), {k: list(v) for k, v in _BRAND_MULTI.items()})
_EXTRA_BRANDS: tuple[str, ...] = ()


def add_brands(names: list[str]) -> None:
    """Katalog markalarını (app/product_catalog.py, Market Fiyatı) gömülü listeye ekle; yeniden çağrılınca önceki ekler
    yerine yenisi geçer (boş liste: yalnız gömülü liste). Sözlükte / tanımlayıcılarda geçen ya da 3 harften kısa tek
    kelimelik marka atılmaz (ürünün kendisini anlatıyor olabilir)."""
    global _EXTRA_BRANDS
    names = tuple(sorted({n for n in names if n}))
    if names == _EXTRA_BRANDS:
        return
    single, multi = set(_BASE_BRANDS[0]), {k: list(v) for k, v in _BASE_BRANDS[1].items()}
    for n in names:
        toks = tuple(name_norm(n).split())
        if len(toks) == 1 and len(toks[0]) >= 3 and toks[0] not in _VOCAB and toks[0] not in DESCRIPTORS \
                and not toks[0].isdigit():
            single.add(toks[0])
        elif len(toks) > 1 and toks not in multi.get(toks[0], []):
            multi.setdefault(toks[0], []).append(toks)
    for v in multi.values():
        v.sort(key=len, reverse=True)
    _BRAND_SINGLE.clear()
    _BRAND_SINGLE.update(single)
    _BRAND_MULTI.clear()
    _BRAND_MULTI.update(multi)
    _BRAND_TOKENS.clear()
    _BRAND_TOKENS.update(_BRAND_SINGLE | {t for v in _BRAND_MULTI.values() for ph in v for t in ph if t not in _VOCAB})
    _BRAND_PREFIX.clear()
    _BRAND_PREFIX.update({t[:k] for t in _BRAND_TOKENS if len(t) > 5 for k in range(5, len(t))})
    _EXTRA_BRANDS = names


def _expand(tokens: list[str]) -> list[str]:
    """Kısaltılmış kelimeleri aç: sözlük kelimesine (OLGUN → OLGUNLASTIRILMIS) ya da marka işaretine ("" → atılır)."""
    out = []
    for t in tokens:
        if len(t) >= 4 and t not in _VOCAB and t not in DESCRIPTORS and t not in _BRAND_SINGLE and t.isalpha():
            cands = [w for w in _PREFIX_POOL.get(t[:4], ()) if w.startswith(t) and w != t]
            if cands and all(w.startswith(cands[0]) for w in cands):
                t = cands[0]
            elif t in _BRAND_PREFIX:
                t = ""
        out.append(t)
    return out


def _brand_marks(tokens: list[str], brand: str | None) -> tuple[set[int], set[int]]:
    """Marka olan sıralar: (tamamı — çok kelimeli markanın sözlükte geçen kelimesi dahil, sözlük dışı olanlar).
    "ÇATAL SUCUK" markası: tamamı atılınca ürün kalmaz; o zaman yalnız ÇATAL atılır, SUCUK kalır. Kalemin kendi
    markası (brand) da aynı biçimde atılır."""
    own = tuple(name_norm(brand).split()) if brand else ()
    full: set[int] = set()
    for i, t in enumerate(tokens):
        if t in _BRAND_SINGLE or (t in own and t not in _VOCAB):
            full.add(i)
        cands = list(_BRAND_MULTI.get(t, ()))
        if len(own) > 1 and own[0] == t:
            cands.append(own)
        for ph in sorted(cands, key=len, reverse=True):
            if tuple(tokens[i:i + len(ph)]) == ph:
                full |= set(range(i, i + len(ph)))
                break
    stop = {i for ph in _NAME_ONLY_BRANDS for j in range(len(tokens) - len(ph) + 1)  # hep atılır
            if tuple(tokens[j:j + len(ph)]) == ph for i in range(j, j + len(ph))}
    full |= stop
    partial = {i for i in full if tokens[i] not in _VOCAB} | stop
    return full, partial


def _strip(tokens: list[str], used: set[int], brand: str | None = None, partial: bool = False) -> list[str]:
    """Boy, marka, tanımlayıcı, rakam ve tek harf atılmış kelimeler (partial: çok kelimeli markanın sözlükte geçen
    kelimesi kalır)."""
    full, part = _brand_marks(tokens, brand)
    marks = part if partial else full
    for k, t in enumerate(tokens):  # "… 2X250 GR FİLTRE KAĞIDI HEDİYELİ": hediye ürünün kendisi değil
        if t in ("HEDIYELI", "HEDIYE") and k:
            n = next((n for n in range(min(k, _MAX_PHRASE), 0, -1) if tuple(tokens[k - n:k]) in _GENERIC), 0)
            marks = marks | set(range(k - n, k))
    rest = [t for i, t in enumerate(tokens) if i not in used and i not in marks and t not in DESCRIPTORS
            and not re.fullmatch(r"[\d.,]+", t) and len(t) > 1]
    if any(tuple(rest[i:i + 2]) in _DROP for i in range(len(rest) - 1)):
        drop = {j for i in range(len(rest) - 1) if tuple(rest[i:i + 2]) in _DROP for j in (i, i + 1)}
        rest = [t for i, t in enumerate(rest) if i not in drop]
    return rest


def _variant(generic: str | None, tokens: list[str]) -> str | None:
    """Süt ürününün çeşidi: ("Süt", ÇİKOLATALI …) → "Çikolatalı Süt", ("Süt", WHEY …) → "Protein İçeceği", ("Süt",
    LAKTOZSUZ YARIM YAĞLI …) → "Laktozsuz Süt". Taban VARIANT_BASES'te değilse ya da çeşit kelimesi yoksa olduğu gibi."""
    if not generic:
        return generic
    base = {"Kakaolu Süt": "Süt", "Laktozsuz Süt": "Süt"}.get(generic, generic)
    if generic not in VARIANT_BASES:
        return generic
    found = sorted({VARIANTS[t] for t in tokens if t in VARIANTS})
    if generic != base:  # "Kakaolu Süt" / "Laktozsuz Süt": adın kendi sıfatı da sayılır
        found = sorted(set(found) | {VARIANTS[name_norm(generic).split()[0]]})
    if not found:
        return generic
    adj = found[0][1]
    return _VARIANT_NAMES.get((adj, base)) or f"{adj} {base}"


def refine(generic: str | None, tokens: list[str]) -> str | None:
    """Genel ad + adın kelimeleri → çeşidi de taşıyan genel ad: süt ürününde aroma / laktozsuz / keçi / protein
    (_variant: ("Süt", ÇİKOLATALI …) → "Çikolatalı Süt"); kahve, çay ve deterjanda ürünün biçimi (form: "ÇEKİRDEK
    KAHVE 1 KG" → Çekirdek Kahve, "BUZLU ÇAY 330 ML" → Soğuk Çay, "TOZ DETERJAN" → Toz Deterjan). Kural, önbellek ve
    yapay zekâ sonucuna aynı biçimde uygulanır (model "Kahve" dese de 3ü1 arada ayrı gruba düşer). Diğer genel adlar
    (gofret, bisküvi: aroma grubu değiştirmez) olduğu gibi."""
    return form(_variant(generic, tokens), tokens) if generic else generic


# ---------------------------------------------------------------- ürün biçimi (kahve, çay, deterjan)
# Aynı genel adın altında fiyatı ve boyu bambaşka ürünler: çekirdek kahve (250 g – 1 kg), 3ü1 arada (18 g, tek
# kullanımlık), soğuk kahve (250 ml) aynı grupta olunca "en çok satılan boy" ve kilo fiyatı anlamsızlaşıyordu
# (08.11.2026: "Çekirdek kahve" → 10 adet kabul edildi). Biçim sırayla: addaki kelime, boyun türü (ml → içecek),
# resmî raf etiketi (shelf: Market Fiyatı main_category + categories, "Kahve|Çekirdek Kahveler"). Hiçbiri yoksa genel
# ad kalır ("Kahve": kafe fişi, "Kahve 100 g"). Biçimin üst adı FORM_PARENT'tadır: yalnız üst adı yazan listede alt
# biçimlerin boyları gösterilir (app/shopping.py common_sizes).
COFFEE_FORMS = ("Türk Kahvesi", "Hazır Kahve", "Filtre Kahve", "Çekirdek Kahve", "Kahve Karışımı", "Kapsül Kahve",
                "Soğuk Kahve", "Hazır Türk Kahvesi")
_CAFE = {"Latte", "Americano", "Espresso", "Cappuccino"}
_COFFEE = {"Kahve", *COFFEE_FORMS, *_CAFE}
_K_CAPSULE = {"KAPSUL", "KAPSULU", "KAPSULLU", "CAPSULE", "CAPSULES", "NESPRESSO"}
_K_COLD = {"SOGUK", "BUZLU", "ICEPRESSO", "XPRESS", "ICED", "FRAPPE", "COLD"}
_K_MIX = {"ARADA", "CAPPUCCINO", "CAPPUCINO", "KAPUCINO", "KAPUCCINO", "MOCHA", "MOCHACCINO", "KOPUKLU", "KARISIM",
          "KARISIMI", "KARISIMLI", "MACCHIATO"}
_K_BEAN = {"CEKIRDEK", "CEKIRDEGI"}
_K_GROUND = {"FILTRE", "OGUTULMUS", "DRIP"}
_K_INSTANT = {"GRANUL", "INSTANT", "COZUNEBILIR", "GOLD", "KLASIK", "CLASSIC", "STICK", "KAVANOZ", "HAZIR"}
# raf etiketi → biçim, sırayla (zincirin etiketi çoğu zaman birden çok: "Fi̇ltre Kahve|Çekirdek Kahveler" çekirdektir)
_K_SHELF = ((("KAPSUL",), "Kapsül Kahve"), (("CEKIRDEK",), "Çekirdek Kahve"), (("SOGUK", "KAHVE"), "Soğuk Kahve"),
            (("BUZLU", "KAHVE"), "Soğuk Kahve"), (("TURK", "KAHVE"), "Türk Kahvesi"), (("OGUTULMUS",), "Filtre Kahve"),
            (("FILTRE", "KAHVE"), "Filtre Kahve"), (("GRANUL",), "Hazır Kahve"), (("COZUNEBILIR",), "Hazır Kahve"),
            (("GOLD", "KAHVE"), "Hazır Kahve"), (("KLASIK", "KAHVE"), "Hazır Kahve"), (("KARISIM",), "Kahve Karışımı"),
            (("HAZIR", "KAHVE"), "Hazır Kahve"))
# kahveyle ilgisiz raf (etikette kahve / içecek kelimesi yoksa): kahve rengi saç boyası, kahveli çikolata, pasta süsü
_NOT_COFFEE_SHELF = ("KOZMETIK", "SAC", "BOYA", "BISKUVI", "CIKOLATA", "GOFRET", "SEKER", "PASTA", "TATLI", "KEK",
                     "MUTFAK", "TEMIZLIK", "KISISEL", "DRAJE", "HAMUR")
TEA_FORMS = ("Poşet Çay", "Bitki Çayı", "Soğuk Çay")
_T_HERBAL = {"BEYAZ", "YESIL", "FIT", "FORM", "ADA", "ADACAYI", "IHLAMUR", "KUSBURNU", "PAPATYA", "REZENE", "BITKI",
             "BITKISEL", "ZAYIFLAMA", "MEYVE"}
DETERGENT_FORMS = ("Toz Deterjan", "Sıvı Deterjan", "Kapsül Deterjan")
_DISH = ("Bulaşık Deterjanı", "Bulaşık Makinesi Deterjanı", "Bulaşık Tableti")
FORM_PARENT = {**{f: "Kahve" for f in COFFEE_FORMS}, **{f: "Çay" for f in TEA_FORMS},
               **{f: "Deterjan" for f in DETERGENT_FORMS}}
# Yalnız üst ad yazılınca ("kahve") gösterilecek alt biçimler, sırayla (en olağan önce)
FORM_CHILDREN = {"Kahve": [f for f in COFFEE_FORMS if f != "Hazır Türk Kahvesi"], "Deterjan": list(DETERGENT_FORMS)}


def _shelf_tags(shelf: str | None) -> list[tuple[str, ...]]:
    """"Kahve|Fi̇ltre Kahve|Çekirdek Kahveler" → [("KAHVE",), ("FILTRE", "KAHVE"), ("CEKIRDEK", "KAHVELER")]."""
    return [tuple(name_norm(t.replace("̇", "")).split()) for t in (shelf or "").split("|") if t.strip()]


def _tag_has(tags: list[tuple[str, ...]], words: tuple[str, ...]) -> bool:
    return any(all(any(x.startswith(w) for x in tag) for w in words) for tag in tags)


def _size_kind(tokens) -> tuple[str | None, float]:
    """(boyun türü ml | g | ad | None, adet): tek adet ("1 ADET") boy sayılmaz."""
    size = parse_size(list(tokens))[0]
    if not size:
        return None, 0
    n = size["value"] * (size.get("pack") or 1) if size["base"] == "ad" else size.get("pack") or 1
    if size["base"] == "ad" and n <= 1:
        return None, 0
    return size["base"], n


def _one_in_one(tokens) -> bool:
    """"3'Ü 1 ARADA", "2Sİ1", "3IN1" (name_norm sonrası "3 U 1", "2 SI 1", "3 IN 1")."""
    t = list(tokens)
    return any(t[i].isdigit() and t[i + 1] in ("U", "SI", "IN", "I") and t[i + 2] == "1" for i in range(len(t) - 2))


def coffee_form(generic: str | None, tokens, shelf: str | None = None) -> str | None:
    """Kahvenin biçimi: Kapsül Kahve, Soğuk Kahve (ml ya da soğuk / buzlu; latte içeceği "Latte" kalır), Türk Kahvesi /
    Hazır Türk Kahvesi, Kahve Karışımı (3ü1 / 2si1 arada, cappuccino, latte tozu), Çekirdek Kahve, Filtre Kahve
    (öğütülmüş, espresso), Hazır Kahve (granül, gold, klasik); addan çıkmazsa raf etiketi. Boysuz kafe içeceği ("LATTE",
    "ESPRESSO") aynen kalır. Raf etiketi verilmiş ve kahveyle ilgisi yoksa (saç boyası "AÇIK KAHVE", pasta süsü) None:
    kural çözmez (ürün katalog / yapay zekâyla gruplanır)."""
    if generic not in _COFFEE:
        return generic
    t = set(tokens)
    kind, _n = _size_kind(tokens)
    tags = _shelf_tags(shelf)
    words = {w for tag in tags for w in tag}
    if words and not any(w.startswith(("KAHV", "CAFE", "ICECEK", "SUT")) for w in words) \
            and any(w.startswith(_NOT_COFFEE_SHELF) for w in words):
        return None
    latte = "LATTE" in t or generic == "Latte"
    if generic in _CAFE and kind is None and not t & (_K_CAPSULE | _K_COLD):
        return generic
    if t & _K_CAPSULE:
        return "Kapsül Kahve"
    if kind == "ml" or t & _K_COLD:
        return "Latte" if latte else "Soğuk Kahve"
    if "TURK" in t or generic in ("Türk Kahvesi", "Hazır Türk Kahvesi"):
        return "Hazır Türk Kahvesi" if "HAZIR" in t or generic == "Hazır Türk Kahvesi" else "Türk Kahvesi"
    if latte or t & _K_MIX or generic == "Cappuccino" or _one_in_one(tokens):
        return "Kahve Karışımı"
    if t & _K_BEAN:
        return "Çekirdek Kahve"
    if t & _K_GROUND:
        return "Filtre Kahve"
    if t & _K_INSTANT:  # "GOLD ESPRESSO 80 G" granüldür
        return "Hazır Kahve"
    if "ESPRESSO" in t or generic == "Espresso":  # öğütülmüş espresso (çekirdek / kapsül yukarıda)
        return "Filtre Kahve"
    for words, f in _K_SHELF:
        if _tag_has(tags, words):
            return f
    return "Kahve" if generic in _CAFE else generic


def tea_form(generic: str | None, tokens) -> str | None:
    """Çayın biçimi: Soğuk Çay (ml, buzlu), Bitki Çayı (yeşil, beyaz, ıhlamur, form), Poşet Çay (poşet; demlik ya da
    10+ adetlik paket), yoksa (dökme) Çay. Boysuz "ÇAY" (kafe) Çay kalır."""
    if generic not in ("Çay", "Poşet Çay"):
        return generic
    t = set(tokens)
    kind, n = _size_kind(tokens)
    if kind == "ml" or t & {"BUZLU", "SOGUK", "ICE"}:
        return "Soğuk Çay"
    if t & _T_HERBAL:
        return "Bitki Çayı"
    if t & {"POSET", "POSETLI"} or (kind == "ad" and ("DEMLIK" in t or n >= 10)) or (kind == "g" and n >= 10):
        return "Poşet Çay"
    return generic


def detergent_form(generic: str | None, tokens) -> str | None:
    """Deterjanın biçimi: bulaşıkta el / makine / tablet; çamaşırda kapsül (tablet, adetli), toz (TOZ ya da ağırlık),
    sıvı (SIVI, JEL ya da hacim). Kelime boydan önce gelir (sıvı bulaşık deterjanı kiloyla da satılır)."""
    if generic not in ("Deterjan", *DETERGENT_FORMS, *_DISH):
        return generic
    t = set(tokens)
    kind, _n = _size_kind(tokens)
    if "BULASIK" in t or generic in _DISH:
        if t & {"TABLET", "TABLETI", "KAPSUL", "KAPSULU"}:
            return "Bulaşık Tableti"
        if t & {"MAKINESI", "MAKINE", "MAKINA", "MAKINASI"}:
            return "Bulaşık Makinesi Deterjanı"
        return "Bulaşık Deterjanı" if generic == "Deterjan" else generic
    if t & {"KAPSUL", "KAPSULU", "TABLET", "TABLETI", "POD", "PODS"}:
        return "Kapsül Deterjan"
    if "TOZ" in t:
        return "Toz Deterjan"
    if t & {"SIVI", "JEL"}:
        return "Sıvı Deterjan"
    if generic != "Deterjan":
        return generic
    return {"ad": "Kapsül Deterjan", "g": "Toz Deterjan", "ml": "Sıvı Deterjan"}.get(kind or "", generic)


def form(generic: str | None, tokens, shelf: str | None = None) -> str | None:
    """Kahve / çay / deterjan biçimi (coffee_form, tea_form, detergent_form); diğer genel adlar olduğu gibi. None: raf
    etiketi ürünün kahve olmadığını söylüyor."""
    return detergent_form(tea_form(coffee_form(generic, tokens, shelf), tokens), tokens)


# yalnız hacimle satılan içecekler: ağırlıklı boy başka üründür ("SÜT MISIR 130 G" çerez, kola şekeri)
_VOLUME_ONLY = {"Süt", "Ayran", "Kefir", "Meyve Suyu", "Soğuk Çay", "Soğuk Kahve", "Kola", "Gazoz", "Su", "Maden Suyu",
                "Soda", "Limonata", "Şalgam"}


def _dimension_ok(generic: str, size: dict | None) -> bool:
    return not (size and size["base"] == "g" and (generic in _VOLUME_ONLY or generic.endswith(" Süt")))


def form_parent(generic: str | None) -> str | None:
    """Biçimin üst adı ("Çekirdek Kahve" → "Kahve"); biçim değilse None."""
    return FORM_PARENT.get(generic or "")


def child_bases(base: str) -> list[str]:
    """Üst adın (boysuz anahtar "kahve") alt biçimlerinin anahtarları, sırayla; üst ad değilse []."""
    return [slug(c) for p, cs in FORM_CHILDREN.items() if slug(p) == base for c in cs]


def generic_of_key(key: str | None) -> str | None:
    """Grup anahtarının genel adı (sözlükten): "turk-kahvesi|100g" → "Türk Kahvesi"; bilinmiyorsa None."""
    return _BY_SLUG.get((key or "").split("|", 1)[0])


# genel baş ad: adda aynı alt kategoriden daha belirli bir ürün (ya da çeşit kelimesi) varsa o seçilir
_HEADS = {"Peynir": {"SUZME": "Süzme Peynir", "EZINE": "Beyaz Peynir"}, "Yoğurt": {"SUZME": "Süzme Yoğurt"},
          "Sos": {}}
_POULTRY = {"PILIC", "TAVUK", "HINDI"}
_POULTRY_OF = {"Döner": "Tavuk Döner", "Köfte": "Tavuk Köfte", "Pirzola": "Tavuk Pirzola", "Kebap": "Tavuk Kebap",
               "Sarma": "Tavuk Sarma", "Kuşbaşı": "Tavuk Kuşbaşı"}
_FROZEN = {"DONDURULMUS", "DONUK"}
_NONFOOD = {"Kişisel Bakım", "Temizlik", "Kağıt Ürünleri", "Ev ve Mutfak", "Evcil Hayvan"}


def _generic(rest: list[str]) -> str | None:
    """Kalan kelimeler bir genel ürün mü. Adda geçen sözlük yazılışlarından biri seçilir: önce güçlü olan (zayıf ad başka
    ürünü niteler: "TAVUK ÇORBASI" → Çorba, "YUMURTA GEZEN TAVUK" → Yumurta), sonra en sağdaki (Türkçede ad tamlamasının
    başı sondadır: "DOMATES SALÇASI"), sonra en uzun ("BEYAZ PEYNİR" > "PEYNİR"). Kalan her kelime bilinen bir kelime
    olmalı (sıfat, çeşit, nitelik ya da sözlük kelimesi); bilinmeyen kelime (çoğu zaman tanınmayan marka) varsa kural
    çözmez, ad yapay zekâya kalır. Çeşit refine ile genel ada eklenir."""
    if not rest:
        return None
    cands = []
    for i in range(len(rest)):
        for j in range(i + 1, min(len(rest), i + _MAX_PHRASE) + 1):
            key = tuple(rest[i:j])
            if key in _GENERIC:
                cands.append(((key not in _WEAK, j, j - i), i, j, _GENERIC[key]))
    if not cands:
        return None
    # gıda dışı ürünün adındaki gıda kelimesi kokudur, aromadır ("DUŞ JELİ VANİLYA SÜTÜ", "KEDİ MAMASI SOMON")
    nonfood = [c for c in cands if c[0][0] and GENERIC_PATH[c[3]][0] in _NONFOOD]
    _, i, j, g = max(nonfood or cands)
    if not all(_known(t) for t in rest[:i] + rest[j:]):
        return None
    if g in _HEADS:  # "ÇEÇİL PEY" → Çeçil Peyniri, "HARDAL SOS" → Hardal, "SÜZME … PEYNİR" → Süzme Peynir
        sub = GENERIC_PATH[g][1]
        same = [c for c in cands if c[3] != g and GENERIC_PATH[c[3]][1] == sub]
        if same:
            g = max(same, key=lambda c: (c[0][2], c[0][1]))[3]
        elif (hit := next((v for t, v in _HEADS[g].items() if t in rest), None)):
            g = hit
    if g in _POULTRY_OF and _POULTRY & set(rest):  # "PİLİÇ YAPRAK DÖNER" tavuk dönerdir
        g = _POULTRY_OF[g]
    elif _FROZEN & set(rest) and (GENERIC_PATH.get(g) or ("",))[0] == "Meyve ve Sebze":
        g = f"Dondurulmuş {g}"  # donuk sebze / meyve taze olanla aynı grup değil (fiyatı ayrı)
    return refine(g, rest)


def _compose(generic_label: str, size: dict | None, weighed: str | None = None, category: str | None = None,
             subcategory: str | None = None) -> dict:
    """{key, label, size_value, size_unit, category, subcategory} — tartılı satırda (kg / lt) boy birim fiyatın birimi.
    Kategori sözlükten (bilinen genel ad); bilinmiyorsa verilen (yapay zekâ)."""
    s = slug(generic_label)
    known = path_for(generic_label)
    top = known[0] if known else category
    sub = known[1] if known else valid_subcategory(top, subcategory)
    cat = {"category": top, "subcategory": sub}
    if weighed == "kg":
        return {"key": f"{s}|kg", "label": f"{generic_label} · kg", "size_value": 1.0, "size_unit": "kg", **cat}
    if weighed == "lt":
        return {"key": f"{s}|1l", "label": f"{generic_label} · 1 L", "size_value": 1.0, "size_unit": "l", **cat}
    if not size:
        return {"key": s, "label": generic_label, "size_value": None, "size_unit": None, **cat}
    k, shown, total, unit = size_key(size)
    return {"key": f"{s}|{k}", "label": f"{generic_label} · {shown}", "size_value": total, "size_unit": unit, **cat}


_KEY_SIZE = re.compile(r"^(?:(\d+)x)?(\d+(?:\.\d+)?)(l|ml|kg|g|ad)$")


def per_unit(key: str | None) -> tuple[float, str] | None:
    """Grup anahtarının boyu → (temel birimde miktar, birim "L" | "kg" | "adet"): "sut|500ml" → (0.5, "L"),
    "su|6x500ml" → (3.0, "L"), "yumurta|30ad" → (30, "adet"), "domates|kg" → (1, "kg"). Boysuz ya da tek adet: None."""
    if not key or "|" not in key:
        return None
    sz = key.split("|", 1)[1]
    if sz == "kg":
        return 1.0, "kg"
    m = _KEY_SIZE.match(sz)
    if not m:
        return None
    pack, v, u = int(m[1] or 1), float(m[2]), m[3]
    amount, unit = {"l": (v, "L"), "ml": (v / 1000, "L"), "kg": (v, "kg"), "g": (v / 1000, "kg"),
                    "ad": (v, "adet")}[u]
    amount *= pack
    if amount <= 0 or (unit == "adet" and amount <= 1):
        return None
    return amount, unit


def size_label(key: str | None) -> str | None:
    """Grup anahtarının boyu görünen biçimde: "sut|1l" → "1 L", "su|6x500ml" → "6×500 ml", "elma|kg" → "kg",
    "yumurta|30ad" → "30 adet"; boysuz None."""
    if not key or "|" not in key:
        return None
    sz = key.split("|", 1)[1]
    if sz == "kg":
        return "kg"
    m = _KEY_SIZE.match(sz)
    if not m:
        return None
    v = m[2].replace(".", ",")
    unit = {"l": "L", "ml": "ml", "kg": "kg", "g": "g", "ad": "adet"}[m[3]]
    return f"{m[1]}×{v} {unit}" if m[1] else f"{v} {unit}"


def unit_price(price: float | None, key: str | None) -> dict:
    """{unit_price_per, per_unit} ("₺59,00/L" için) ya da {} (boysuz grup, fiyat yok)."""
    pu = per_unit(key)
    if price is None or not pu:
        return {}
    return {"unit_price_per": round(price / pu[0], 2), "per_unit": pu[1]}


_OVERRIDES = {tuple(name_norm(k).split()): v for k, v in BRAND_PRODUCTS.items()}
_MAX_OVR = max(len(k) for k in _OVERRIDES)


def _override(tokens: list[str], used: set[int], brand: str | None) -> str | None:
    """Marka → genel ürün düzeltmesi (product_taxonomy.BRAND_PRODUCTS): adda en uzun marka öbeği varsa onun genel adı;
    ama adın geri kalanında güçlü bir genel ürün varsa o ("NUTELLA BİSKÜVİ" → Bisküvi, "COLA TURKA KOLA" → Kola)."""
    hit = None
    for i in range(len(tokens)):
        for j in range(min(len(tokens), i + _MAX_OVR), i, -1):
            if tuple(tokens[i:j]) in _OVERRIDES and (hit is None or j - i > hit[1] - hit[0]):
                hit = (i, j)
                break
    if not hit:
        return None
    gen = _OVERRIDES[tuple(tokens[hit[0]:hit[1]])]
    rest = _strip([t if not hit[0] <= i < hit[1] else "" for i, t in enumerate(tokens)], used, brand, partial=True)
    strong = [((j, j - i), _GENERIC[k]) for i in range(len(rest)) for j in range(i + 1, min(len(rest), i + _MAX_PHRASE) + 1)
              if (k := tuple(rest[i:j])) in _GENERIC and k not in _WEAK]
    if strong:
        return _generic(rest) or refine(max(strong)[1], rest)
    return refine(gen, rest)


def rule_group(name: str, brand: str | None = None, unit: str | None = None, shelf: str | None = None) -> dict | None:
    """Yalnız kurallarla grup ({key, label, size_value, size_unit, category, subcategory}); çözemezse None.
    Marka → ürün düzeltmesi (BRAND_PRODUCTS) önce. Birimsiz çıplak rakam ("COLA TURKA 50") boy değildir. Ürünün biçimi
    (form: kahve, çay, deterjan) adın bütün kelimeleri ve boyuyla, verilmişse resmî raf etiketiyle (shelf: Market Fiyatı
    "main_category|categories") seçilir. Yalnız hacimle satılan içecekte ağırlıklı boy (süt 130 g) kural dışıdır."""
    tokens = _expand(name_norm(name).split())
    size, used = parse_size(tokens)
    g = _override(tokens, used, brand) or _generic(_strip(tokens, used, brand)) or \
        _generic(_strip(tokens, used, brand, partial=True))
    g = form(g, tokens, shelf) if g else None
    if not g or not _dimension_ok(g, size):
        return None
    return _compose(g, size, unit if unit in ("kg", "lt") else None)


def resolve(name: str, brand: str | None = None, unit: str | None = None,
            exclude: str | None = None, shelf: str | None = None) -> tuple[dict | None, str | None]:
    """Ad → (grup, kaynak "rule" | "catalog" | None). Önce kurallar (marka → ürün düzeltmesi dahil; marka listesi
    katalogla büyür), çözülmezse katalog eşleşmesi (Market Fiyatı ürün listesi, app/product_catalog.py; güven ≥
    MIN_CONFIDENCE). Hiçbiri yoksa (None, None): önbellek / yapay zekâ (groups_for). Ölçüm (07.11.2026, 25 580 katalog
    ürünü, scratchpad eval): katalogu kuraldan önce koymak tutarsızlığı artırdı (aynı ürün markalı / markasız 236 → 490
    farklı gruba), bu yüzden kural önce. exclude: ölçümde katalog ürününün kendisi (dışarıda bırakılır)."""
    from app import product_catalog

    if (g := rule_group(name, brand, unit, shelf)):
        return g, "rule"
    if not product_catalog.size():
        return None, None
    gen, conf = product_catalog.match(name, exclude)
    if gen and conf >= product_catalog.MIN_CONFIDENCE:
        tokens = _expand(name_norm(name).split())
        gen = form(_variant(gen, tokens), tokens, shelf)
        if gen:
            return _compose(gen, parse_size(tokens)[0], unit if unit in ("kg", "lt") else None), "catalog"
    return None, None


def catalog_contains(name: str) -> dict | None:
    """Alışveriş listesi, yapay zekâdan önce: yazılan adın bütün kelimeleri katalog ürünlerinin adında geçiyorsa
    (product_catalog.contains) o genel ad + yazılan boy; yoksa None. Yalnız listede: fiş gruplaması (resolve) değişmez."""
    from app import product_catalog

    if not product_catalog.size():
        return None
    gen = product_catalog.contains(name)
    if not gen:
        return None
    tokens = _expand(name_norm(name).split())
    return _public(_compose(refine(gen, tokens), parse_size(tokens)[0]))


def _provisional(name: str, unit: str | None) -> dict | None:
    """Yapay zekâ beklenirken: markası ve boyu atılmış adın kendisi (aynı yazılış aynı geçici grup)."""
    tokens = name_norm(name).split()
    size, used = parse_size(tokens)
    rest = _strip(tokens, used, partial=True)
    if not rest:
        return None
    label = " ".join(w.capitalize() for w in rest)[:40]
    return {**_compose(label, size, unit if unit in ("kg", "lt") else None), "pending": True}


# ---------------------------------------------------------------- önbellek ve kuyruk (system.db)

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _store(c, nn: str, g: dict | None, source: str) -> None:
    c.execute("INSERT OR REPLACE INTO product_groups (name_norm, group_key, label, size_value, size_unit, source, "
              "created_at, category, subcategory) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (nn, (g or {}).get("key") or "", (g or {}).get("label") or "", (g or {}).get("size_value"),
               (g or {}).get("size_unit"), source, _now(), (g or {}).get("category"), (g or {}).get("subcategory")))


def _public(g: dict | None) -> dict | None:
    if not g or not g.get("key"):
        return None
    return {"key": g["key"], "label": g["label"], **({"category": g["category"]} if g.get("category") else {}),
            **({"subcategory": g["subcategory"]} if g.get("category") and g.get("subcategory") else {}),
            **({"pending": True} if g.get("pending") else {})}


def groups_for(items: list[dict], learn: bool = True) -> list[dict | None]:
    """Satırların grupları ({key, label[, category, subcategory, pending]} ya da ürün değilse None), sırayla.
    Satır: {name, product?, brand?, unit?}. Ad olarak product (modelin standart adı) varsa o, yoksa name. Kural çözerse önbelleğe yazılır; çözemezse
    önbellekteki yapay zekâ sonucu, o da yoksa geçici grup (learn: ad kuyruğa girer)."""
    from app import system

    out: list[dict | None] = [None] * len(items)
    miss: dict[str, list[int]] = {}
    fresh: dict[str, tuple] = {}
    fresh_idx: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        name = (it.get("product") or it.get("name") or "").strip()
        if not name:
            continue
        unit, shelf = it.get("unit"), it.get("shelf")
        g, src = resolve(name, it.get("brand"), unit, shelf=shelf)
        if g:
            out[i] = g
            nn = name_norm(name)
            fresh.setdefault(nn, ((resolve(name, it.get("brand"), shelf=shelf)[0] if unit else None) or g, src,
                                  bool(shelf)))
            fresh_idx.setdefault(nn, []).append(i)
        else:
            miss.setdefault(name_norm(name), []).append(i)
    if not miss and not fresh:
        return [_public(g) for g in out]
    with system.connect() as c:
        if fresh:
            have = {r[0]: (r[1], r[2]) for r in c.execute(
                f"SELECT name_norm, group_key, label FROM product_groups WHERE name_norm IN "
                f"({','.join('?' * len(fresh))})", list(fresh))}
            for nn, (g, src, shelved) in fresh.items():
                old = have.get(nn)
                if old is None or (shelved and old[0] and old[0] != g["key"]):
                    _store(c, nn, g, src or "rule")  # raf etiketiyle çözülen biçim önbelleği de düzeltir
                elif old[0] and form_parent(old[1].split(" · ")[0]) == g["label"].split(" · ")[0]:
                    # fişteki ad biçimi söylemiyor ("JACOBS ORNEK 1 KG" → Kahve) ama aynı ad resmî kaynakta raf
                    # etiketiyle biçime çözülmüş (Çekirdek Kahve): o biçim
                    for i in fresh_idx[nn]:
                        u = items[i].get("unit")
                        out[i] = _compose(old[1].split(" · ")[0], parse_size(nn.split())[0],
                                          u if u in ("kg", "lt") else None)
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
                    old = row["label"].split(" · ")[0]
                    tokens = nn.split()
                    generic = refine(old, tokens)
                    size, _ = parse_size(tokens)
                    cat, sub = (row["category"], row.get("subcategory")) if generic == old else (None, None)
                    known = path_for(old) if generic == old else None
                    out[i] = (_compose(generic, size, unit, cat, sub) if unit or generic != old
                              else {"key": row["group_key"], "label": row["label"],
                                    "category": known[0] if known else cat,
                                    "subcategory": known[1] if known else valid_subcategory(cat, sub)})
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


def paths(keys: list[str]) -> dict[str, tuple[str, str | None]]:
    """Grup anahtarı → (ana kategori, alt kategori | None): bilinen genel addan, yoksa önbellekteki (yapay zekâ)."""
    from app import system

    keys = sorted({k for k in keys if k})
    if not keys:
        return {}
    with system.connect() as c:
        got = {r[0]: (r[1], r[2], r[3]) for r in c.execute(
            "SELECT group_key, MAX(label), MAX(category), MAX(subcategory) FROM product_groups WHERE group_key IN "
            f"({','.join('?' * len(keys))}) GROUP BY group_key", keys)}
    out = {}
    for k in keys:
        label, cat, sub = got.get(k) or (None, None, None)
        if label and (known := path_for(label.split(" · ")[0])):
            out[k] = known
        elif cat:
            out[k] = (cat, valid_subcategory(cat, sub))
    return out


def categories(keys: list[str]) -> dict[str, str]:
    """Grup anahtarı → ana kategori (bilinen genel addan; yoksa önbellekteki)."""
    return {k: p[0] for k, p in paths(keys).items()}


def subcategories(keys: list[str]) -> dict[str, str]:
    """Grup anahtarı → alt kategori ("Peynir", "Bakliyat"); bilinmiyorsa anahtar yok."""
    return {k: p[1] for k, p in paths(keys).items() if p[1]}


def pending() -> int:
    from app import system

    with system.connect() as c:
        return c.execute("SELECT COUNT(*) FROM product_group_queue").fetchone()[0]


# ---------------------------------------------------------------- yapay zekâ (toplu)

PROMPT = """Türkiye'deki market fişlerinden gelen ürün adlarını MARKADAN BAĞIMSIZ genel ürün adına çevir.
Kurallar: g = Türkçe, kısa (1-3 kelime), ilk harfleri büyük genel ürün adı ("Süt", "Beyaz Peynir", "Bulaşık Deterjanı");
marka, boy/gramaj, yağ oranı (tam/yarım yağlı, light) ve ambalaj YAZMA. Atıştırmalıkta aroma YAZMA ("ÜLKER ÇİK.GOFRET"
→ "Gofret"). Ama süt, yoğurt, kefir, ayran gibi içecek ve süt ürünlerinde ürünü değiştiren çeşidi YAZ: aromalı süt ayrı
ürün ("ÇİKOLATALI GÜNLÜK SÜT 200ML" → "Çikolatalı Süt", "ÇİLEKLİ SÜT" → "Çilekli Süt"), "LAKTOZSUZ SÜT" → "Laktozsuz
Süt", "KEÇİ SÜTÜ" → "Keçi Sütü"; protein / whey içecek süt değildir ("WHEY SÜT 330ML" → "Protein İçeceği"). Yalnız
marka olan kelimeyi at ("HARRAS GÜNLÜK SÜT 1L" → "Süt"). Yalnız markasıyla anılan üründe markanın ürününü yaz
(""" + "; ".join(f'"{b.title()}" → "{g}"' for g, b in {g: b for b, g in reversed(BRAND_PRODUCTS.items())}.items()) + """); "Gazlı İçecek",
"İçecek", "Gıda" gibi raf adı YAZMA, ürünün kendisini yaz ("COLA TURKA 330ML" → "Kola"). Kısaltmaları aç. Ürün değilse (poşet, indirim, hizmet,
depozito) g = null. Her ad için bir satır, sırayı koru.
c = kategori, yalnız şu listeden biri (tırnaksız yaz): """ + "; ".join(f'"{c}"' for c in CATEGORIES) + """.
s = alt kategori, c'nin altındakilerden biri (uymuyorsa yazma): """ + "; ".join(
    f"{c}: {', '.join(subs)}" for c, subs in SUBCATEGORIES.items() if subs) + """.
Yanıt yalnız JSON: {"items": [{"i": 0, "g": "Süt", "c": "Süt ve Kahvaltılık", "s": "Süt"}, ...]}
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


GENERATION = 0  # yapay zekâ sonucu önbelleğe her yazılınca artar (app/shopping_known.py önbelleği bununla tazelenir)


def _ask(batch: list[dict]) -> dict:
    """Tek toplu çağrı: [{name_norm, name}] → modelin yanıtı (dict). Hata fırlatabilir (çağıran yakalar)."""
    from app import receipt

    names = "\n".join(f"{i}. {b['name']}" for i, b in enumerate(batch))
    data, _meta = receipt.gemini_text_json(PROMPT + names, max_output_tokens=2048, kind="grup", model=receipt.DEFAULT_MODEL)
    return data if isinstance(data, dict) else {}


def _apply(batch: list[dict], data: dict) -> int:
    """Yanıtı önbelleğe yaz, kuyruktan sil. Yanıtta olmayan ad kuyrukta kalır. Dönen: yazılan ad sayısı."""
    global GENERATION
    from app import community_prices, system

    got, cats, subs = {}, {}, {}
    for row in data.get("items") or []:
        if isinstance(row, dict) and isinstance(row.get("i"), int) and 0 <= row["i"] < len(batch):
            got[row["i"]] = row.get("g")
            cats[row["i"]] = valid_category(row.get("c"))
            subs[row["i"]] = valid_subcategory(cats[row["i"]], row.get("s"))
    done = 0
    with system.connect() as c:
        for i, b in enumerate(batch):
            if i not in got:
                continue  # yanıtta yok: kuyrukta kalır (deneme sayısı arttı)
            g = refine(_valid(got[i]), b["name_norm"].split())
            size, _ = parse_size(b["name_norm"].split())
            grp = _compose(g, size, category=cats.get(i), subcategory=subs.get(i)) if g else None
            _store(c, b["name_norm"], grp, "ai")
            c.execute("DELETE FROM product_group_queue WHERE name_norm = ?", (b["name_norm"],))
            community_prices.relabel(c, b["name_norm"], grp)  # geçici gruptaki topluluk satırları
            done += 1
    if done:
        GENERATION += 1
    return done


def classify_pending(max_calls: int = 1) -> int:
    """Kuyruktaki adları toplu sınıflandır (en çok max_calls çağrı, günlük tavan AI_CALLS_PER_DAY). Dönen: önbelleğe
    yazılan ad sayısı. Yapay zekâ kapalıysa (anahtar yok) 0. Bir çağrının hatası kuyruğu bozmaz (deneme sayısı artar)."""
    from app import receipt, system

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
            try:
                data = _ask(batch)
            except Exception:  # noqa: BLE001 - ağ / model hatası: adlar kuyrukta kalır, sonra yeniden denenir
                log.warning("ürün grubu yapay zekâ çağrısı başarısız", exc_info=True)
                break
            done += _apply(batch, data)
    finally:
        _LOCK.release()
    return done


# ---------------------------------------------------------------- alışveriş listesine yazılan adlar (hemen, tek çağrı)

LIST_AI_NAMES_PER_USER_DAY = 20   # kişi başı günde en çok bu kadar liste adı yapay zekâya gider
LIST_AI_CALLS_PER_DAY = 60        # sunucu geneli liste çağrısı tavanı (toplu kuyruğun AI_CALLS_PER_DAY'inden ayrı)
LOCK_WAIT_S = 90.0                # toplu sınıflandırma sürerken liste çağrısı bekler (aynı ad iki kez sorulmasın)


def _list_key(uid: int | None = None) -> str:
    day = date.today().isoformat()
    return f"list_groups_ai:{day}:{uid}" if uid else f"list_groups_ai:{day}"


def _count(key: str) -> int:
    from app import system

    try:
        return int(system.get(key) or 0)
    except ValueError:
        return 0


def enqueue(names: list[str]) -> None:
    """Adları yapay zekâ kuyruğuna yaz (önbellekte olan ya da zaten kuyrukta olan eklenmez)."""
    from app import system

    rows = {name_norm(n): n.strip()[:80] for n in names if n and n.strip() and name_norm(n)}
    if not rows:
        return
    with system.connect() as c:
        have = {r[0] for r in c.execute(f"SELECT name_norm FROM product_groups WHERE name_norm IN "
                                        f"({','.join('?' * len(rows))})", list(rows))}
        c.executemany("INSERT OR IGNORE INTO product_group_queue (name_norm, name, added_at) VALUES (?, ?, ?)",
                      [(nn, n, _now()) for nn, n in rows.items() if nn not in have])


def classify_names(names: list[str], uid: int | None = None) -> int:
    """Alışveriş listesine yazılan adlar beklemesin: YALNIZ bu adlar (kuyrukta olup önbellekte olmayanlar), tek toplu
    çağrıda. Maliyet sınırları: sunucu geneli önbellek (aynı ad ikinci kez sorulmaz, kim yazarsa yazsın); kişi başı günde
    LIST_AI_NAMES_PER_USER_DAY ad; sunucu geneli günde LIST_AI_CALLS_PER_DAY çağrı; kişinin günlük yapay zekâ sınırı
    (receipt._daily_gate: çağıranın defter bağlamında sayılır) ve yönetici kısıtlamaları. Dönen: önbelleğe yazılan ad."""
    from app import receipt, system

    nns = list(dict.fromkeys(name_norm(n) for n in names if n and name_norm(n)))
    if not nns or not receipt.enabled():
        return 0
    if not _LOCK.acquire(timeout=LOCK_WAIT_S):
        return 0
    try:
        left = LIST_AI_NAMES_PER_USER_DAY - _count(_list_key(uid)) if uid else BATCH
        if left <= 0 or _count(_list_key()) >= LIST_AI_CALLS_PER_DAY:
            return 0
        with system.connect() as c:
            q = ",".join("?" * len(nns))
            cached = {r[0] for r in c.execute(f"SELECT name_norm FROM product_groups WHERE name_norm IN ({q})", nns)}
            queued = {r["name_norm"]: dict(r) for r in c.execute(
                f"SELECT name_norm, name FROM product_group_queue WHERE tries < ? AND name_norm IN ({q})",
                [QUEUE_MAX_TRIES, *nns])}
        batch = [queued[nn] for nn in nns if nn in queued and nn not in cached][:min(left, BATCH)]
        if not batch:
            return 0
        try:
            data = _ask(batch)  # kişinin günlük sınırı burada (receipt._daily_gate) sayılır / durdurur
        except receipt.ReceiptError as e:
            if isinstance(e, receipt.DailyLimitError):
                return 0
            log.warning("liste adı yapay zekâ çağrısı başarısız: %s", e)
            data = None
        except Exception:  # noqa: BLE001 - ağ / model hatası: deneme sayısı artar, sonra yeniden denenir
            log.warning("liste adı yapay zekâ çağrısı başarısız", exc_info=True)
            data = None
        system.put(_list_key(), str(_count(_list_key()) + 1))
        if uid:
            system.put(_list_key(uid), str(_count(_list_key(uid)) + len(batch)))
        with system.connect() as c:
            c.execute(f"UPDATE product_group_queue SET tries = tries + 1 WHERE name_norm IN "
                      f"({','.join('?' * len(batch))})", [b["name_norm"] for b in batch])
            c.execute("DELETE FROM sys_settings WHERE key LIKE 'list_groups_ai:%' AND key < ?",
                      (f"list_groups_ai:{date.today().isoformat()}",))
        return _apply(batch, data) if data is not None else 0
    finally:
        _LOCK.release()


def _spawn(fn) -> None:
    threading.Thread(target=fn, daemon=True, name="product-groups-list").start()


def kick_names(names: list[str]) -> None:
    """Listeye yazılan adları arka planda hemen sınıflandır (istek yolu beklemez), açık defterin bağlamında: kişinin
    günlük yapay zekâ sınırı ona sayılır. Yapay zekâ kapalıysa ya da ad yoksa hiçbir şey."""
    import os

    from app import db, receipt

    names = [n for n in names if n and n.strip()]
    if not names or not receipt.enabled():
        return
    u = db.current_user()
    path = db.db_path()
    path = None if os.path.abspath(path) == os.path.abspath(db.DB_PATH) else path

    def go():
        with db.use_space(path, u):
            _safe(classify_names, names, (u or {}).get("id"))

    _spawn(go)


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


# ---------------------------------------------------------------- yeniden gruplama (kurallar değişince, bir kez)

GROUPS_VERSION = 5  # 2: aromalı / laktozsuz / keçi süt ve protein içeceği ayrı grup (refine)
#                    3: geniş sözlük (product_taxonomy), marka listesi, alt kategori; kural yapay zekâ sonucunun önünde
#                    4: marka → ürün düzeltmesi (BRAND_PRODUCTS: Cola Turka → Kola, Fanta → Portakallı Gazoz …);
#                       katalog eşleşmesi gecelik tutarlılık işinde (consistency)
#                    5: ürün biçimi (form): kahve çekirdek / filtre / Türk / granül / karışım / kapsül / soğuk; çay
#                       dökme / poşet / bitki / soğuk; deterjan toz / sıvı / kapsül, bulaşık makinesi; resmî satırlarda
#                       raf etiketi (catalog_products); hacimle satılan içecekte ağırlıklı boy kural dışı; gıda dışı ad
#                       gıda kelimesinden önce (duş jeli "vanilya sütü")


def regroup(c) -> dict:
    """Önbellekteki adları güncel kurallarla yeniden grupla (system._migrate, aynı bağlantı; sürüm başına bir kez):
    - her ad kurallarla yeniden denenir; kural çözerse o geçerlidir (groups_for da önce kuralı uygular; yapay zekâ
      sonucu da kuralın önüne geçmez). Kalemin markası bilinmediği için çözülemeyen ad eski grubunda kalır ama çeşidi
      (refine) yine uygulanır;
    - yapay zekâ sonucu (source ai) refine ile düzeltilir; tabanı süt ürünü olup adında bilinmeyen başka kelime olan
      (modelin marka sandığı bir çeşit adı olabilir) ad sıkılaştırılmış istemle yeniden sorulsun diye kuyruğa girer (önbellekteki
      grup yanıt gelene kadar geçerli kalır);
    - kategori ve alt kategori bilinen genel addan tamamlanır;
    - kuyrukta bekleyen ama artık kuralın çözdüğü ad kuyruktan çıkar (yapay zekâ çağrısı harcanmaz);
    - topluluk fiyatı satırları yeni anahtara taşınır (community_prices.relabel; tartılı |kg korunur); resmî kaynak
      satırları (official_prices) da adıyla yeniden gruplanır.
    Dönen: {"changed": grubu değişen ya da kuyruktan kurala geçen ad, "queued": kuyruğa giren ad}."""
    changed = queued = 0
    for r in [dict(x) for x in c.execute("SELECT * FROM product_groups WHERE group_key != ''")]:
        ch, q = _regroup_row(c, r)
        changed += ch
        queued += q
    changed += _regroup_queue(c)
    _regroup_official(c, [tuple(x) for x in c.execute("SELECT DISTINCT title, brand FROM official_prices").fetchall()],
                      _shelves())
    return {"changed": changed, "queued": queued}


def _shelves() -> dict[str, str]:
    from app import product_catalog

    try:
        return product_catalog.shelves()
    except Exception:  # noqa: BLE001 - katalogsuz: yalnız ad
        log.warning("raf etiketleri okunamadı", exc_info=True)
        return {}


def _regroup_row(c, r: dict) -> tuple[int, int]:
    """Bir önbellek satırını güncel kurallarla yeniden grupla (regroup gövdesi). Dönen: (değişti mi, kuyruğa girdi mi)."""
    from app import community_prices

    nn, tokens = r["name_norm"], r["name_norm"].split()
    size, used = parse_size(tokens)
    old = r["label"].split(" · ")[0]
    new = rule_group(nn)
    if new and form_parent(old) == new["label"].split(" · ")[0]:
        new = None  # önbellekteki biçim (raf etiketiyle çözülmüş: Çekirdek Kahve) addan çıkan üst addan (Kahve) kesin
    source = "rule" if new else r["source"]
    queued = 0
    if not new:
        g = refine(old, tokens)
        new = _compose(g, size) if g != old else None
    if r["source"] == "ai" and old in VARIANT_BASES and not new:  # çeşit kelimesi bulunduysa düzeltme yeter
        words = {w for k in _GENERIC for w in k}
        rest = [t for t in _strip(tokens, used) if t not in MODIFIERS and t not in VARIANTS and t not in words]
        if rest:
            c.execute("INSERT OR IGNORE INTO product_group_queue (name_norm, name, added_at) VALUES (?, ?, ?)",
                      (nn, nn, _now()))
            queued = 1
    if new and new["key"] != r["group_key"]:
        _store(c, nn, new, source)
        community_prices.relabel(c, nn, new)
        return 1, queued
    known = path_for(new["label"].split(" · ")[0] if new else old)
    cat = known[0] if known else r.get("category")
    sub = known[1] if known else valid_subcategory(cat, r.get("subcategory"))
    if (cat, sub) != (r.get("category"), r.get("subcategory")):
        c.execute("UPDATE product_groups SET category = ?, subcategory = ? WHERE name_norm = ?", (cat, sub, nn))
    return 0, queued


def _regroup_queue(c) -> int:
    """Kuyrukta bekleyen ama artık kuralın çözdüğü adlar kuyruktan çıkar (yapay zekâ çağrısı harcanmaz)."""
    from app import community_prices

    changed = 0
    for q in [dict(x) for x in c.execute("SELECT name_norm, name FROM product_group_queue")]:
        if (new := rule_group(q["name"]) or rule_group(q["name_norm"])):
            _store(c, q["name_norm"], new, "rule")
            c.execute("DELETE FROM product_group_queue WHERE name_norm = ?", (q["name_norm"],))
            community_prices.relabel(c, q["name_norm"], new)
            changed += 1
    return changed


def _regroup_official(c, titles: list[tuple], shelves: dict[str, str] | None = None) -> None:
    """Resmî fiyat satırları adıyla yeniden gruplanır (title indeksli; eşleme önce hesaplanır, toplu güncellenir).
    shelves: ad → raf etiketi (product_catalog.shelves; biçimi adı söylemeyen ürün için: "JACOBS ORNEK 1 KG" çekirdek
    mi öğütülmüş mü). Kural çözmezse katalog eşleşmesi (içe aktarımdaki gibi); o da yoksa satır grubunda kalır."""
    upd = []
    shelves = shelves or {}
    for title, brand in titles:
        if (new := resolve(title, brand, shelf=shelves.get(name_norm(title)))[0]):
            upd.append((new["key"], title, brand, new["key"]))
    if upd:
        c.executemany("UPDATE official_prices SET group_key = ? WHERE title = ? AND brand IS ? AND group_key != ?", upd)


REGROUP_CHUNK = 400        # bir işlemde yeniden gruplanan önbellek satırı
REGROUP_TITLES = 2000      # bir işlemde yeniden gruplanan resmî başlık
REGROUP_BUDGET_S = 20.0    # bir tick'te en çok bu kadar saniye (açılışı ve istekleri bekletmesin)


def regroup_pending() -> bool:
    from app import system

    return int(system.get("product_groups:version") or 0) < GROUPS_VERSION


def regroup_tick() -> dict | None:
    """Sürüm yükseldiyse yeniden gruplamayı ARKA PLANDA parça parça yap (jobs.scheduler her turda çağırır): her parça
    ayrı işlem (kilit kısa), ilerleme sys_settings'te (yeniden başlarsa kaldığı yerden). 07.11.2026: tek işlemde
    açılışta çalışınca canlı sunucu 15 dakika kilitlendi. Bitince sürüm yazılır; iş yoksa None."""
    import time as _t

    from app import system

    if not regroup_pending():
        return None
    t0, done = _t.monotonic(), {"rows": 0, "titles": 0}
    phase = system.get("product_groups:regroup_phase") or "rows"
    cur = int(system.get("product_groups:regroup_cursor") or 0)
    shelves = None
    while True:  # en az bir parça; sonra süre bütçesi dolana dek
        if done["rows"] + done["titles"] and _t.monotonic() - t0 >= REGROUP_BUDGET_S:
            break
        if phase != "rows" and shelves is None:  # raf etiketleri ve katalog dizini yazma işleminden önce okunur
            from app import product_catalog

            shelves = _shelves()
            product_catalog.size()
        with system.connect() as c:
            if phase == "rows":
                rows = [dict(x) for x in c.execute("SELECT rowid AS _rid, * FROM product_groups WHERE group_key != '' "
                                                   "AND rowid > ? ORDER BY rowid LIMIT ?", (cur, REGROUP_CHUNK))]
                for r in rows:
                    _regroup_row(c, r)
                if rows:
                    cur = rows[-1]["_rid"]
                    done["rows"] += len(rows)
                else:
                    _regroup_queue(c)
                    phase, cur = "official", 0
            else:
                titles = [tuple(x) for x in c.execute("SELECT DISTINCT title, brand FROM official_prices ORDER BY title, "
                                                      "brand LIMIT ? OFFSET ?", (REGROUP_TITLES, cur))]
                _regroup_official(c, titles, shelves)
                cur += len(titles)
                done["titles"] += len(titles)
                if len(titles) < REGROUP_TITLES:
                    c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES ('product_groups:version', ?)",
                              (str(GROUPS_VERSION),))
                    for k in ("product_groups:regroup_phase", "product_groups:regroup_cursor"):
                        c.execute("DELETE FROM sys_settings WHERE key = ?", (k,))
                    log.info("ürün grupları yeniden gruplandı (arka plan): %s", done)
                    return {**done, "finished": True}
            c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES ('product_groups:regroup_phase', ?)", (phase,))
            c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES ('product_groups:regroup_cursor', ?)", (str(cur),))
    return {**done, "finished": False}


# ---------------------------------------------------------------- tutarlılık (gecelik) ve kalite ölçüleri

CONFLICTS_KEY = "groups:conflicts"
CONFLICTS_TOP = 30
SPREAD_FACTOR = 3.0
_SOURCE_RANK = {"override": 3, "rule": 3, "catalog": 2, "consistency": 2, "ai": 1}


def form_of(nn: str) -> str:
    """Adın biçimi: boyu, markası (bilinen markalar), tanımlayıcıları, rakamları atılmış kelimeler ("COLA TURKA 330 ML"
    → "COLA"; marka listesi katalogdan büyür). Aynı biçim iki ayrı genel ada düşmemeli (consistency)."""
    tokens = _expand(nn.split())
    _, used = parse_size(tokens)
    keep = {t for k in _OVERRIDES for t in k if t in tokens}  # "FANTA PORTAKAL" ile "PORTAKAL" ayrı biçim
    full, _ = _brand_marks(tokens, None)
    return " ".join(t for i, t in enumerate(tokens) if t in keep and i not in used or
                    (i not in used and i not in full and t not in DESCRIPTORS and not re.fullmatch(r"[\d.,]+", t)
                     and len(t) > 1))


def _rekey(c, r: dict, generic: str, source: str) -> dict | None:
    """Önbellek satırını yeni genel ada taşı (boyu adından; tartılı |kg korunur); topluluk satırları da taşınır."""
    from app import community_prices

    size, _ = parse_size(r["name_norm"].split())
    new = _compose(generic, size, "kg" if r["group_key"].endswith("|kg") else None)
    if new["key"] == r["group_key"]:
        return None
    _store(c, r["name_norm"], new, source)
    community_prices.relabel(c, r["name_norm"], new)
    return new


CONSISTENCY_COMMIT = 200  # bu kadar taşımada bir ara kayıt (yazma kilidi uzun tutulmasın)


def consistency(c) -> dict:
    """Aynı biçimdeki adlar tek genel ada (gecelik, katalog yenilenince; c: system.connect()).
    1. Yapay zekâ / geçici sonuçlu ad bugün katalog ya da kuralla çözülüyorsa o gruba geçer (kural yapay zekâdan önce).
    2. Aynı biçim (form_of) birden çok genel ada düşmüşse: biçimin kendisi çözülüyorsa (katalog / kural) o, değilse
       kaynağı güçlü (kural, katalog > yapay zekâ) ve çok olan kazanır; yapay zekâ (ve önceki tutarlılık) sonuçlu
       diğerleri o genel ada (kendi boyuyla) taşınır. Kural / katalog sonucu taşınmaz (ikisi de kanıtlı): çelişki
       yalnız raporlanır ("PORTAKAL" meyve mi meyve suyu mu — boy ve marka karar verir).
    Çelişen biçimler (en çok CONFLICTS_TOP) sys_settings'e yazılır: yönetici paneli "çelişen gruplar".
    Dönen {rekeyed, conflicts}."""
    import json

    from app import product_catalog

    product_catalog.size()  # dizin önce (yazma başlamadan; iç içe okuma bağlantısı)
    rows = [dict(x) for x in c.execute("SELECT * FROM product_groups WHERE group_key != ''")]
    rekeyed = 0
    for r in rows:
        if r["source"] in ("ai", "consistency"):
            g, src = resolve(r["name_norm"])
            if g and g["key"].split("|")[0] != r["group_key"].split("|")[0]:
                if (new := _rekey(c, r, g["label"].split(" · ")[0], src or "catalog")):
                    r.update(group_key=new["key"], label=new["label"], source=src or "catalog")
                    rekeyed += 1
                    if rekeyed % CONSISTENCY_COMMIT == 0:
                        c.commit()  # kilit kısa: istekler beklemesin (07.11.2026 tek işlemde 4.711 satır → 500)
    by: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        f = form_of(r["name_norm"])
        if f:
            by.setdefault(f, {}).setdefault(r["group_key"].split("|")[0], []).append(r)
    found = []
    for f, groups in by.items():
        if len(groups) < 2:
            continue
        g, _src = resolve(f)
        base = g["key"].split("|")[0] if g else None
        if base not in groups:
            base = max(groups, key=lambda b: (max(_SOURCE_RANK.get(x["source"], 0) for x in groups[b]),
                                              len(groups[b]), b))
        label = (g["label"].split(" · ")[0] if g and g["key"].split("|")[0] == base
                 else groups[base][0]["label"].split(" · ")[0])
        found.append({"form": f, "groups": sorted(({"key": b, "label": v[0]["label"].split(" · ")[0], "n": len(v)}
                                                   for b, v in groups.items()), key=lambda x: (-x["n"], x["key"])),
                      "resolved_to": label})
        for b, v in groups.items():
            if b != base:
                for r in v:
                    if r["source"] in ("ai", "consistency") and _rekey(c, r, label, "consistency"):
                        rekeyed += 1
                        if rekeyed % CONSISTENCY_COMMIT == 0:
                            c.commit()
    found.sort(key=lambda x: (-sum(g["n"] for g in x["groups"]), x["form"]))
    c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES (?, ?)",
              (CONFLICTS_KEY, json.dumps({"at": _now(), "count": len(found), "top": found[:CONFLICTS_TOP]},
                                         ensure_ascii=False)))
    if found or rekeyed:
        log.info("ürün grubu tutarlılığı: %d çelişen biçim, %d ad taşındı", len(found), rekeyed)
    return {"rekeyed": rekeyed, "conflicts": len(found)}


def quality() -> dict:
    """Yönetici paneli grouping_quality: {names, grouped, grouped_pct, pending, not_product, by_source, conflicts {count,
    top, at}, wide_spread {count, top [{group_key, label, min, max, ratio}]}, catalog}. wide_spread: resmî fiyatın son
    gününde aynı gruptaki ürünlerin fiyatı SPREAD_FACTOR katından fazla açılıyor (büyük olasılıkla yanlış eşleme)."""
    import json

    from app import product_catalog, system

    with system.connect() as c:
        src = {r[0] or "?": r[1] for r in c.execute(
            "SELECT source, COUNT(*) FROM product_groups WHERE group_key != '' GROUP BY source")}
        not_product = c.execute("SELECT COUNT(*) FROM product_groups WHERE group_key = ''").fetchone()[0]
        queued = c.execute("SELECT COUNT(*) FROM product_group_queue").fetchone()[0]
        last = c.execute("SELECT MAX(day) FROM official_prices").fetchone()[0]
        spread = c.execute("SELECT group_key, MIN(price), MAX(price), COUNT(*) FROM official_prices WHERE day = ? "
                           "AND price > 0 GROUP BY group_key HAVING COUNT(*) >= 3", (last,)).fetchall() if last else []
        raw = c.execute("SELECT value FROM sys_settings WHERE key = ?", (CONFLICTS_KEY,)).fetchone()
    try:
        conf = json.loads(raw[0]) if raw else {}
    except ValueError:
        conf = {}
    grouped = sum(src.values())
    wide = sorted(({"group_key": g, "min": round(lo, 2), "max": round(hi, 2), "ratio": round(hi / lo, 1)}
                   for g, lo, hi, _n in spread if lo > 0 and hi / lo > SPREAD_FACTOR), key=lambda x: -x["ratio"])
    names = labels([w["group_key"] for w in wide[:20]])
    return {"names": grouped + not_product + queued, "grouped": grouped,
            "grouped_pct": round(100 * grouped / (grouped + queued), 1) if grouped + queued else None,
            "pending": queued, "not_product": not_product, "by_source": src,
            "conflicts": {"count": conf.get("count", 0), "top": conf.get("top", []), "at": conf.get("at")},
            "wide_spread": {"count": len(wide), "day": last,
                            "top": [{**w, "label": names.get(w["group_key"], w["group_key"])} for w in wide[:20]]},
            "catalog": product_catalog.status()}
