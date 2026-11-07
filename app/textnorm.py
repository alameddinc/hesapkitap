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


@functools.lru_cache(maxsize=16384)  # aynı açıklama tek istekte yüzlerce kez geliyor; saf fonksiyon
def merchant_name(description: str) -> str:
    """İşyeri gruplaması için kısa ad: 'FACEBK *ABC123XYZ9/Dublin' -> 'FACEBK', 'Uber Eats Yemek' -> 'UBER EATS YEMEK'"""
    import re

    norm = strip_gateway(normalize(description))  # 'PAYCO/ORNEK BILISIM' → 'ORNEK BILISIM' (aracı değil asıl işyeri)
    # Satıcı adına tahsilat yapan aracılar (merchant of record): asıl satıcı yıldızdan sonra
    # 'PADDLE.NET* GEOGUESSR/0808…' -> 'GEOGUESSR', 'FS *SUBLIME HQ' -> 'SUBLIME HQ'
    norm = re.sub(r"^(PADDLE\.NET|PADDLE|FS|2CO\.COM|2CO|LEMSQZY|XSOLLA|DLOCAL)\s*\*\s*", "", norm)
    # Pazar yeri önekleri: 'GOOGLE *Google One' / 'GOOGLE*CLOUD' -> 'GOOGLE ONE' / 'GOOGLE CLOUD'
    norm = re.sub(r"^(GOOGLE|APPLE|PAYPAL|AMZN|MICROSOFT|MSFT)\s*\*\s*", r"\1 ", norm)
    norm = re.sub(r"^(\w+) \1\b", r"\1", norm)  # 'GOOGLE GOOGLE ONE' -> 'GOOGLE ONE'
    norm = re.sub(r"(\w)\*(\w)", r"\1 \2", norm)
    norm = strip_gateway(norm)
    head = re.split(r"[*/]", norm, maxsplit=1)[0].strip() or norm
    # sayı ve 'Q1W2E3' gibi işlem referans kodlarını at; 'A101', 'F202' gibi kısa kodlar kalsın
    words = [w for w in head.split()
             if not re.fullmatch(r"[\d\-#]+", w) and not (len(w) >= 5 and re.search(r"\d", w))]
    name = " ".join(words[:3]) or norm
    name = re.sub(r"\.COM(\.TR)?$", "", name)  # 'SPOTIFY.COM' -> 'SPOTIFY'
    return MERCHANT_ALIASES.get(name, name)


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
PAYMENT_GATEWAYS = (
    "MOKAUNITED", "MOKA UNITED", "MOKA", "IYZICO", "IYZ", "IYZI", "TKPAY", "ODEAL", "PAYTR", "SIPAY", "PARAM",
    "PARAMPOS", "PARAM POS", "CRAFTGATE", "PAPARA", "HEPSIPAY", "ESNEKPOS", "PAYCELL", "HALKODE OD", "NKOLAY",
    "PAYCO", "ELEKSE", "TOSLA", "PAYU", "LIDIO", "PAYNET", "IPARA", "PARATIKA", "BIRLESIK ODEME", "PAYGURU",
)
_GATEWAYS = frozenset(PAYMENT_GATEWAYS)
_GATEWAY_HEAD = None


def gateway_of(norm: str) -> tuple[str, str] | None:
    """Baştaki aracı ve kalan metin: 'IYZ*ORNEK KITAP' → ('IYZ', 'ORNEK KITAP'); aracı yoksa None."""
    import re

    global _GATEWAY_HEAD
    if _GATEWAY_HEAD is None:
        _GATEWAY_HEAD = re.compile(r"^\s*([A-Z0-9 .]+?)\s*(?:/+|\*)[\s/*]*(.*)$")
    m = _GATEWAY_HEAD.match(norm)
    if not m or m.group(1) not in _GATEWAYS or not m.group(2).strip("/* "):
        return None
    return m.group(1), m.group(2)


def strip_gateway(norm: str) -> str:
    """'MOKAUNITED/ORNEK MOBILYA' -> 'ORNEK MOBILYA', 'ODEAL//ORNEK KASAP/ISTANBUL' -> 'ORNEK KASAP/ISTANBUL',
    'PAYCO/ELEKSE/ORNEK BILISIM' -> 'ORNEK BILISIM' (iç içe aracı), 'IYZ*ORNEK KITAP' -> 'ORNEK KITAP'."""
    for _ in range(3):
        g = gateway_of(norm)
        if not g:
            break
        norm = g[1]
    return norm


def drop_gateway(text: str) -> str:
    """strip_gateway'in asıl yazılışı koruyan hâli (görünen ad için): 'Payco/Örnek Bilişim' → 'Örnek Bilişim'."""
    import re

    for _ in range(3):
        m = re.match(r"^\s*([^/*]+?)\s*(?:/+|\*)[\s/*]*(.*)$", text, re.S)
        if not m or normalize(m.group(1)) not in _GATEWAYS or not m.group(2).strip("/* "):
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
