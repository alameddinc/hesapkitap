"""Türkçe metin normalizasyonu: kural eşleştirmede büyük/küçük harf ve aksan farkını yok eder."""

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
    import re

    norm = normalize(description)
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


# Ödeme kuruluşları / sanal POS aracıları: asıl satıcı '/' sonrasında yazar
PAYMENT_GATEWAYS = ("MOKAUNITED", "MOKA UNITED", "MOKA", "IYZICO", "TKPAY", "ODEAL", "PAYTR", "SIPAY",
                    "PARAM", "CRAFTGATE", "PAPARA", "HEPSIPAY", "ESNEKPOS", "PAYCELL", "HALKODE OD", "NKOLAY")


def strip_gateway(norm: str) -> str:
    """'MOKAUNITED/ORNEK MOBILYA' -> 'ORNEK MOBILYA', 'ODEAL//ORNEK KASAP/ISTANBUL' -> 'ORNEK KASAP/ISTANBUL'"""
    head, sep, rest = norm.partition("/")
    if sep and head.strip() in PAYMENT_GATEWAYS and rest.strip("/ "):
        return rest.lstrip("/ ")
    return norm
