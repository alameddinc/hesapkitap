"""Türkçe metin normalizasyonu: kural eşleştirmede büyük/küçük harf ve aksan farkını yok eder."""
import functools

_TR_MAP = str.maketrans({
    "İ": "I", "ı": "I", "i": "I",
    "Ş": "S", "ş": "S",
    "Ğ": "G", "ğ": "G",
    "Ü": "U", "ü": "U",
    "Ö": "O", "ö": "O",
    "Ç": "C", "ç": "C",
})


def normalize(text: str) -> str:
    """'Uber Eats Market İSTANBUL' -> 'UBER EATS MARKET ISTANBUL'"""
    return " ".join(text.translate(_TR_MAP).upper().split())


def parse_amount(raw: str) -> float:
    """'1,234.56' / '+12,345.67' -> float. VakıfBank US formatı kullanıyor."""
    return float(raw.replace(",", "").replace("+", ""))


def merchant_name(description: str) -> str:
    """İşyeri gruplaması için kısa ad: 'FACEBK *ABC123XYZ9/Dublin' -> 'FACEBK', 'Uber Eats Yemek' -> 'UBER EATS YEMEK'"""
    _tables()  # aracı listesi değiştiyse (başka süreçte onay) önbellek burada temizlenir
    return _merchant_name_cached(description)


@functools.lru_cache(maxsize=16384)  # aynı açıklama tek istekte yüzlerce kez geliyor; aracı listesi değişince temizlenir
def _merchant_name_cached(description: str) -> str:
    return merchant_name_with(description, _tables())


merchant_name.cache_clear = _merchant_name_cached.cache_clear  # eski çağıranlar için


# İşyeri adı kuralının sürümü. Artınca her defterde ada bağlı ayarlar (abonelik durumu, kısma hedefi, öğrenilmiş
# eşleşme kuralı, sabit kalem anahtarı) eski addan yeni ada arka planda taşınır (app/merchant_rekey.py).
# 1: ilk kural; 2 (13.11.2026): sondaki konum eki atılır ('SPOTIFY.COM ISTANBUL TR' → 'SPOTIFY').
NAME_VERSION = 2


def merchant_name_with(description: str, gs: "GatewaySet", version: int = NAME_VERSION) -> str:
    """merchant_name, verilen aracı kümesiyle (önbelleksiz): eski / yeni aracı listesine (app/db.py
    migrate_gateway_keys) ya da eski / yeni ad kuralı sürümüne (app/merchant_rekey.py) göre adı karşılaştırmak için."""
    import re

    norm = gs.strip(normalize(description))  # 'PAYCO/ORNEK BILISIM' → 'ORNEK BILISIM' (aracı değil asıl işyeri)
    # Satıcı adına tahsilat yapan aracılar (merchant of record): asıl satıcı yıldızdan sonra
    # 'PADDLE.NET* GEOGUESSR/0808…' -> 'GEOGUESSR', 'FS *SUBLIME HQ' -> 'SUBLIME HQ'
    norm = re.sub(r"^(PADDLE\.NET|PADDLE|FS|2CO\.COM|2CO|LEMSQZY|XSOLLA|DLOCAL)\s*\*\s*", "", norm)
    # Pazar yeri önekleri: 'GOOGLE *Google One' / 'GOOGLE*CLOUD' -> 'GOOGLE ONE' / 'GOOGLE CLOUD'
    norm = re.sub(r"^(GOOGLE|APPLE|PAYPAL|AMZN|MICROSOFT|MSFT)\s*\*\s*", r"\1 ", norm)
    norm = re.sub(r"^(\w+) \1\b", r"\1", norm)  # 'GOOGLE GOOGLE ONE' -> 'GOOGLE ONE'
    norm = re.sub(r"(\w)\*(\w)", r"\1 \2", norm)
    norm = gs.strip(norm)
    head = re.split(r"[*/]", norm, maxsplit=1)[0].strip() or norm
    # sayı ve 'Q1W2E3' gibi işlem referans kodlarını at; 'A101', 'F202' gibi kısa kodlar kalsın
    words = [w for w in head.split()
             if not re.fullmatch(r"[\d\-#]+", w) and not (len(w) >= 5 and re.search(r"\d", w))]
    if version >= 2:
        words = drop_trailing_location(words)  # 'SPOTIFY.COM ISTANBUL TR' → 'SPOTIFY.COM', 'MIGROS KADIKOY' → 'MIGROS'
    name = " ".join(words[:3]) or norm
    name = re.sub(r"\.COM(\.TR)?$", "", name)  # 'SPOTIFY.COM' -> 'SPOTIFY'
    return MERCHANT_ALIASES.get(name, name)


# Konum eki atılınca geriye yalnız bunlardan biri kalırsa atılmaz: 'CAFE TAKSIM', 'OTEL ANKARA' ayrı işyerleri
GENERIC_SHOP_WORDS = frozenset("""
CAFE KAFE CAFFE KAHVE KAHVECI OTEL HOTEL MOTEL PANSIYON HOSTEL RESTORAN RESTAURANT RESTAURANTE LOKANTA LOKANTASI
KEBAP KEBAPCI KEBABCI DONER DONERCI PIDE PIDECI BUFE BUFESI PASTANE PASTANESI PASTAHANE FIRIN FIRINI EKMEK BAKKAL
MARKET MARKETI SUPERMARKET MANAV KASAP KASABI ECZANE ECZANESI KUAFOR BERBER OTOPARK OTOPARKI KIRTASIYE BAR PUB
GALERI BUTIK GIYIM AVM CARSI PAZAR GARAJ TERMINAL OTOGAR SPOR GYM KLINIK HASTANE HASTANESI OPTIK TICARET
""".split())
# Önünde şehir yokken de atılan iki harfli ülke kodları (ekstrede sık: 'ORNEK OYUN NL'); ötekiler ('AT', 'NO', 'IT',
# 'BE', 'CA'…) yalnız bir konumun ardından ('VIENNA AT'): tek başına kelime / kısaltma olabilir
_SOLO_CODES = frozenset({"TR", "US", "GB", "UK", "IE", "NL", "LU", "DE", "SE", "CY"})
# İl ama sık soyadı / kişi adı: sonda yazınca işyeri sahibinin adı olabilir ('ORNEK AYDIN')
_KEEP_TRAILING = frozenset({"AYDIN"})


def drop_trailing_location(words: list[str]) -> list[str]:
    """Sondaki konum sözcüklerini at (app/places.py sözlüğü: il, büyük ilçe, yabancı şehir, ülke adı / kodu, 'IST'):
    ['SPOTIFY.COM', 'ISTANBUL', 'TR'] → ['SPOTIFY.COM']. Kesik son parça da atılır: bir konumun ardından gelen, en çok
    3 harflik konum başlangıcı ('ISTANBUL TU'); tek başına kesik sözcük atılmaz ('ORNEK BAHCE' ≠ BAHCELIEVLER).
    Atılmaz: hepsi konumsa ('ISTANBUL', 'ISTANBUL TR'), kalan tek kelime genel bir dükkân adıysa ('CAFE TAKSIM') ya da
    tek karakterse. Sayılar önceden atılmış olmalı (merchant_name). Zincir + şube ('ORNEKZINCIR KADIKOY') tek işyeri
    olur: eğik çizgili yazılış ('ORNEKZINCIR/KADIKOY') zaten öyleydi; şube ham açıklamada ve fişin konum alanında kalır."""
    from app import places

    loc = places.words()
    ws = list(words)
    if len(ws) < 2:
        return ws
    cut = len(ws)
    last = ws[-1]
    if last not in loc and len(ws) >= 3 and ws[-2] in loc and len(last) <= 3 and places.is_prefix(last):
        cut -= 1  # satır sonunda kesilmiş ülke: 'ISTANBUL TU' (TURKIYE)
    while cut > 0 and ws[cut - 1] in loc and ws[cut - 1] not in _KEEP_TRAILING:
        w = ws[cut - 1]
        if len(w) == 2 and w not in _SOLO_CODES and (cut < 2 or ws[cut - 2] not in loc):
            break  # 'ORNEK DUKKAN AT' / 'ORNEK BUFE NO': iki harf tek başına ancak yaygın ülke koduysa
        cut -= 1
    if cut == len(ws) or cut == 0:
        return ws
    rest = ws[:cut]
    if len(rest) == 1 and (rest[0] in GENERIC_SHOP_WORDS or len(rest[0]) < 2):
        return ws
    return rest


# Satıcının adı önde olan önekler (pazar yeri / merchant of record): merchant_name bunları kendisi işler, ödeme aracısı
# adayı sayılmaz (app/gateways.py)
MARKETPLACE_PREFIXES = frozenset({"PADDLE.NET", "PADDLE", "FS", "2CO.COM", "2CO", "LEMSQZY", "XSOLLA", "DLOCAL",
                                  "GOOGLE", "APPLE", "PAYPAL", "AMZN", "MICROSOFT", "MSFT"})

# Aynı işyerinin bankadan bankaya farklı yazılışları
MERCHANT_ALIASES = {
    "SPOTIFYAB": "SPOTIFY",
    "AMAZONPRIMETR": "AMAZON PRIME",
    "NETFLIX.COM": "NETFLIX",
    "YOUTUBEPREMIUM": "YOUTUBE PREMIUM",
}


# Ödeme kuruluşları / sanal POS aracıları (payment facilitator): kart ekstresinde işyeri adı aracının adıyla başlar,
# asıl satıcı '/' ya da '*' sonrasında yazar ('PAYCO/ORNEK BILISIM', 'IYZ*ORNEK KITAP', 'ODEAL//ORNEK KASAP').
# Tek liste: görünen ad (merchant_name), gruplama anahtarı, kural eşleşmesi (categorize) ve kural önerisi
# (suggest_pattern) hepsi strip_gateway'den geçer. Yeni aracı eklemek için buraya normalize() biçiminde yaz
# (büyük harf, Türkçe karaktersiz). Ham açıklama (veritabanındaki description) hiç değişmez.
# Sabit listeye ek olarak yöneticinin onayladığı önekler (system.db payment_gateways, app/gateways.py) deploy beklemeden
# katılır; bilinen bir aracıya 1 harf uzaklıktaki yazılış (en az 5 harfli adlarda) da aracı sayılır (GatewaySet).
PAYMENT_GATEWAYS = (
    "MOKAUNITED", "MOKA UNITED", "MOKA", "IYZICO", "IZICO", "IYZCO", "IYZ", "IYZI",  # IZICO: bazı bankaların yazılışı (12.11)
    "TKPAY", "ODEAL", "PAYTR", "SIPAY", "PARAM",
    "PARAMPOS", "PARAM POS", "CRAFTGATE", "PAPARA", "HEPSIPAY", "ESNEKPOS", "PAYCELL", "HALKODE OD", "NKOLAY",
    "PAYCO", "ELEKSE", "TOSLA", "PAYU", "LIDIO", "PAYNET", "IPARA", "PARATIKA", "BIRLESIK ODEME", "PAYGURU",
    # TCMB lisanslı ödeme / elektronik para kuruluşu listesinden (12.11.2026), ayırt edici marka adları
    "QNBPAY", "QNB PAY", "MONEYPAY", "PAYTREK", "PAYHOPPA", "TOMPAY", "TOM PAY", "NOMUPAY", "NOMU PAY", "TRENDPAY",
    "HALKODE", "AKODE", "DGPARA",
)
# Benzer yazım (1 harf) toleransının dışında tutulan yaygın işyeri / kart / yer adları: 'TESLA' (TOSLA), 'IDEAL' (ODEAL),
# 'PARAF' (kart markası; PARAM), 'KARAM' (marka; PARAM), 'TOSYA' (ilçe; TOSLA)
FUZZY_BLOCK = frozenset({"TESLA", "IDEAL", "PARAMA", "KAPARA", "SIPAHI", "PAYDOS", "PAYNE", "ELEKS", "PARAF", "KARAM",
                         "TOSYA", "DOSLA"})
FUZZY_MIN_LETTERS = 5
_FUZZY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _letters(s: str) -> int:
    return sum(ch.isalpha() for ch in s)


def _edits1(word: str) -> set[str]:
    """1 düzenleme (silme / ekleme / değiştirme) uzaklığındaki bütün yazılışlar."""
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    out = {a + b[1:] for a, b in splits if b}
    out |= {a + ch + b[1:] for a, b in splits if b for ch in _FUZZY_ALPHABET if ch != b[0]}
    out |= {a + ch + b for a, b in splits for ch in _FUZZY_ALPHABET}
    return out


class GatewaySet:
    """Aracı önekleri: tam eşleşme + ön hesaplanmış benzer yazım sözlüğü (yazılış → bilinen aracı). Değişmez; liste
    değişince yenisi kurulur. Benzer yazım yalnız en az FUZZY_MIN_LETTERS harfli aracıda ve en az o kadar harfli önekte
    geçerli; reddedilen önekler ve FUZZY_BLOCK hiç benzer sayılmaz; benzer eşleşmede sağ taraf yalnız konumsa
    ('TESLA/ISTANBUL') aracı sayılmaz."""

    def __init__(self, names, blocked=(), fuzzy: bool = True):
        self.names = frozenset(names)
        self.blocked = frozenset(blocked) | FUZZY_BLOCK
        self.fuzzy = fuzzy
        near: dict[str, str] = {}
        if fuzzy:
            for g in sorted(self.names):
                if _letters(g) < FUZZY_MIN_LETTERS:
                    continue
                for v in _edits1(g):
                    if (v not in self.names and v not in self.blocked and _letters(v) >= FUZZY_MIN_LETTERS
                            and not v.startswith(" ") and not v.endswith(" ")):
                        near.setdefault(v, g)
        self.near = near
        self.key = (tuple(sorted(self.names)), tuple(sorted(self.blocked)), fuzzy)
        import hashlib
        import json

        self.sig = hashlib.sha256(json.dumps(self.data(), sort_keys=True).encode()).hexdigest()[:16]

    def data(self) -> dict:
        """Kalıcı biçim (app/gateways.py remember_set): adlar, benzer yazımı engellenenler, benzer yazım açık mı."""
        return {"names": list(self.key[0]), "blocked": list(self.key[1]), "fuzzy": self.fuzzy}

    def match(self, prefix: str) -> str | None:
        """Önek bir aracı mı: aracının kendisi (tam ya da benzer yazımın karşılığı), değilse None."""
        if prefix in self.names:
            return prefix
        return self.near.get(prefix)

    def split(self, norm: str) -> tuple[str, str] | None:
        """Baştaki aracı ve kalan metin: 'IYZ*ORNEK KITAP' → ('IYZ', 'ORNEK KITAP'); aracı yoksa None."""
        m = _GATEWAY_HEAD.match(norm)
        if not m or not m.group(2).strip("/* "):
            return None
        head = m.group(1)
        if head in self.names:
            return head, m.group(2)
        if head in self.near:
            from app.places import is_location

            if is_location(m.group(2)):  # 'TESLA/ISTANBUL' gibi: işyeri + şehir, aracı değil
                return None
            return head, m.group(2)
        return None

    def strip(self, norm: str) -> str:
        for _ in range(3):
            g = self.split(norm)
            if not g:
                break
            norm = g[1]
        return norm


_GATEWAY_HEAD = __import__("re").compile(r"^\s*([A-Z0-9 .]+?)\s*(?:/+|\*)[\s/*]*(.*)$")
GATEWAY_RELOAD_SECONDS = 30.0  # başka süreç (ya da elle) onaylanan önek en geç bu kadar sonra görülür
_gw_lock = __import__("threading").RLock()
_gw = {"set": None, "src": None, "ver": None, "next": 0.0, "loading": False}


def _tables() -> GatewaySet:
    """Geçerli aracı kümesi: sabit liste + yöneticinin onayladıkları (system.db). Her çağrıda yalnız saat kontrolü;
    GATEWAY_RELOAD_SECONDS'ta bir sistem veritabanındaki sürüm damgasına bakılır."""
    import time

    gs = _gw["set"]
    if gs is not None and time.monotonic() < _gw["next"]:
        return gs
    with _gw_lock:  # tek iş parçacığı okur; ötekiler bekler
        if _gw["loading"]:  # aynı iş parçacığında iç içe çağrı (sistem veritabanı göçü vb.): eldeki ya da sabit liste
            return _gw["set"] or _STATIC()
        gs = _gw["set"]
        if gs is not None and time.monotonic() < _gw["next"]:
            return gs
        return _refresh()


@functools.lru_cache(maxsize=1)
def _STATIC() -> "GatewaySet":
    return GatewaySet(PAYMENT_GATEWAYS)


def _refresh() -> GatewaySet:
    import logging
    import time

    approved, rejected, src, ver = (), (), None, None
    _gw["loading"] = True
    try:
        from app import gateways

        src, ver, approved, rejected = gateways.load()
    except Exception:  # noqa: BLE001 - sistem veritabanı yoksa / bozuksa sabit liste
        logging.getLogger(__name__).warning("ödeme aracısı listesi okunamadı", exc_info=True)
    finally:
        _gw["loading"] = False
    if _gw["set"] is None or (src, ver) != (_gw["src"], _gw["ver"]):
        gs = GatewaySet(set(PAYMENT_GATEWAYS) | set(approved), rejected)
        changed = _gw["set"] is None or gs.key != _gw["set"].key
        _gw.update(set=gs, src=src, ver=ver)
        if changed:
            _forget_derived()
    _gw["next"] = time.monotonic() + GATEWAY_RELOAD_SECONDS
    return _gw["set"]


def _forget_derived() -> None:
    """İşyeri adından türeyen önbellekler: ad önbelleği, Özet hesapları (finance._memo), okuma önbelleği."""
    import sys

    _merchant_name_cached.cache_clear()
    for mod, attr in (("app.finance", "_CACHE"), ("app.db", "_READ_CACHE")):
        m = sys.modules.get(mod)
        if m is not None and isinstance(getattr(m, attr, None), dict):
            getattr(m, attr).clear()


def reload_gateways() -> GatewaySet:
    """Liste değişti (onay / ret / kaldırma, test): hemen yeniden oku, işyeri adı önbelleğini temizle."""
    with _gw_lock:
        _gw["next"] = 0.0
        _gw["ver"] = object()  # sürüm aynı görünse de yeniden kur
        return _refresh()


def current_gateways() -> GatewaySet:
    return _tables()


def is_gateway(prefix: str) -> bool:
    return _tables().match(prefix) is not None


def gateway_of(norm: str) -> tuple[str, str] | None:
    """Baştaki aracı ve kalan metin: 'IYZ*ORNEK KITAP' → ('IYZ', 'ORNEK KITAP'); aracı yoksa None."""
    return _tables().split(norm)


def strip_gateway(norm: str) -> str:
    """'MOKAUNITED/ORNEK MOBILYA' -> 'ORNEK MOBILYA', 'ODEAL//ORNEK KASAP/ISTANBUL' -> 'ORNEK KASAP/ISTANBUL',
    'PAYCO/ELEKSE/ORNEK BILISIM' -> 'ORNEK BILISIM' (iç içe aracı), 'IYZ*ORNEK KITAP' -> 'ORNEK KITAP'."""
    return _tables().strip(norm)


def drop_gateway(text: str) -> str:
    """strip_gateway'in asıl yazılışı koruyan hâli (görünen ad için): 'Payco/Örnek Bilişim' → 'Örnek Bilişim'."""
    import re

    gs = _tables()
    for _ in range(3):
        m = re.match(r"^\s*([^/*]+?)\s*(?:/+|\*)[\s/*]*(.*)$", text, re.S)
        if not m or not m.group(2).strip("/* ") or not gs.split(normalize(m.group(1)) + "/" + normalize(m.group(2))):
            break
        text = m.group(2)
    return text


# Şirket unvanı ekleri: fişte "SAN.VE DIŞ.TİC.LTD.ŞTİ.", ekstrede "SANAYİ VE" — ikisi de aynı köke insin
_COMPANY_WORDS = {"SAN", "SANAYI", "SANAYII", "VE", "TIC", "TICARET", "DIS", "LTD", "LIMITED", "STI", "SIRKETI",
                  "AS", "A", "ANONIM", "INS", "INSAAT", "PAZ", "PAZARLAMA", "ITH", "IHR", "TUR", "TURIZM", "HIZ",
                  "HIZMETLERI"}


def legal_key(name: str) -> str:
    """Resmi unvanın kökü: 'NEW TASTE GIDA SAN.VE DIŞ.TİC.LTD.ŞTİ.' ve 'NEW TASTE GIDA SANAYİ VE' → 'NEW TASTE GIDA'."""
    import re

    norm = strip_gateway(normalize(name))
    norm = re.split(r"/", norm, maxsplit=1)[0]
    words = []
    for w in re.split(r"[\s.,*]+", norm):
        if not w:
            continue
        if w in _COMPANY_WORDS:
            break
        words.append(w)
    return " ".join(words[:3])


_DIGIT_WORDS = {"0": "sıfır", "1": "bir", "2": "iki", "3": "üç", "4": "dört", "5": "beş", "6": "altı", "7": "yedi",
                "8": "sekiz", "9": "dokuz"}
_HARD = set("fstkçşhp")
_READ_AS = {"center": "sentır", "market": "market"}  # yabancı adın okunuşu (Macrocenter'dan, Happy Center'a)


def suffix(name: str, case: str) -> str:
    """Özel ada kesme işaretiyle durum eki: case "den" (ayrılma: "BİM'den", "ŞOK'tan", "Migros'tan", "A101'den",
    "CarrefourSA'dan") ya da "e" (yönelme: "BİM'e", "Migros'a", "CarrefourSA'ya", "Tarım Kredi'ye"). Kısaltma gibi
    yazılan ad (BİM, ŞOK) okunduğu gibi; rakamla biten ad rakamın okunuşuyla."""
    s = (name or "").strip()
    if not s:
        return s
    spoken = s.lower().replace("i̇", "i")
    if spoken[-1].isdigit():
        spoken = _DIGIT_WORDS[spoken[-1]]
    for w, r in _READ_AS.items():
        if spoken.endswith(w):
            spoken = spoken[: -len(w)] + r
    vowels = [ch for ch in spoken if ch in "aeıioöuü"]
    last = vowels[-1] if vowels else "e"
    front = last in "eiöü"
    end = spoken[-1]
    if case == "e":
        if end in "aeıioöuü":
            return f"{s}'{'ye' if front else 'ya'}"
        return f"{s}'{'e' if front else 'a'}"
    d = "t" if end in _HARD else "d"
    return f"{s}'{d}{'en' if front else 'an'}"
