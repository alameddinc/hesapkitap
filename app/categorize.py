"""Kural tabanlı kategorizasyon. Kural = normalize edilmiş açıklamada aranan regex."""
import re

from app.parsers.base import FEE, PAYMENT, REFUND
from app.textnorm import normalize, strip_gateway

UNCATEGORIZED = "Kategorisiz"
# Varsayılan kurallar değişince artır: mevcut veritabanlarına eksik kurallar eklenir (kullanıcı kuralları korunur)
RULES_VERSION = 4

# (pattern, kategori) — sıra önemli, ilk eşleşen kazanır. Pattern'ler normalize() çıktısına göre (büyük harf, ASCII).
DEFAULT_RULES: list[tuple[str, str]] = [
    (r"FACEBK|META ADS|GOOGLE ADS", "Reklam"),
    (r"GOOGLE ?\*?CLOUD|HETZNER|CLOUDFLARE|RAILWAY|SQSP|DOMAIN|WEBSHARE|GOOGLE WORKSPACE|AWS|DIGITALOCEAN|VERCEL", "Yazılım & Bulut"),
    (r"OPENAI|CHATGPT|ANTHROPIC|CLAUDE|ELEVENLABS|SUNO|GAMMA\.APP|CANVA|MIDJOURNEY|CURSOR", "AI & Yazılım Abonelik"),
    (r"SPOTIFY|NETFLIX|AMAZONPRIME|PRIME VIDEO|YOUTUBE|GOOGLE ONE|SCRIBD|DISNEY|EXXEN|BLUTV|APPLE\.COM", "Dijital Abonelik"),
    (r"UBER EATS YEMEK|YEMEKSEPETI|GETIR YEMEK|TRENDYOL YEMEK", "Yemek Siparişi"),
    (r"UBER EATS MARKET|GETIR|A101|BIM |BIM$|MIGROS|CARREFOUR|SOK MARKET|^SOK-|FILE F\d+|^FILE |MARKET|BAKKAL|KASAP|"
     r"GIDA SANAYI|STT GIDA|GROSS|TARIM KREDI|\bET$", "Market & Gıda"),
    (r"CAFE|KAHVE|STARBUCKS|BAKE|RESTORAN|RESTAURANT|LOKANTA|BURGER|PIZZA|ESPRESSO|TCHIBO|KOFTE|KEBAP|ISKENDER|"
     r"TAVUK|CHICKEN|KOFTECI|DONER", "Kafe & Restoran"),
    (r"OPET|SHELL|BP |PETROL|POAS|^PO |TOTAL|BENZIN|AKARYAKIT|OTOYOL|ISPARK|OTOPARK|HGS|OGS|UBER(?! EATS)|BITAKSI",
     "Ulaşım & Araç"),
    (r"LC ?WAIKIKI|MAVI|COLINS|DEFACTO|KOTON|ZARA|H&M|HENNES|TEKSTIL|TEKS\.|KIDS", "Giyim"),
    (r"EBEBEK|JOKER|BEBEK", "Bebek & Çocuk"),
    (r"HEPSIPAY|HEPSIBURADA|TRENDYOL|AMAZON|N11|CICEKSEPETI|IYZICO", "Online Alışveriş"),
    (r"TEKNOSA|MEDIAMARKT|MEDIA MARKT|VATAN BILGISAYAR|APPLE STORE|ITOPYA", "Elektronik"),
    (r"TESISAT|IKEA|KOCTAS|BAUHAUS|ENGLISH HOME|MADAME COCO", "Ev & Tamirat"),
    (r"OTEL|HOTEL|PEGASUS|THY|TURK HAVA|AJET|BOOKING|AIRBNB", "Seyahat & Konaklama"),
    (r"POLICE|SIGORTA", "Sigorta"),
    (r"VODAFONE|TURKCELL|TURK TELEKOM MOBIL|AVEA", "Telefon"),
    (r"TTNET|TURK TELEKOM|SUPERONLINE|TURKNET|DIGITURK|D-SMART|KABLONET", "İnternet & TV"),
    (r"ISKI|ASKI|IZSU|BUSKI|SU TAHSILAT", "Su"),
    (r"IGDAS|BASKENTGAZ|IZMIRGAZ|DOGALGAZ|DOGAL GAZ", "Doğalgaz"),
    (r"ENERJISA|CK BOGAZICI|BOGAZICI ELEKTRIK|AYEDAS|BEDAS|ELEKTRIK TAHSILAT|GEDIZ ELEKTRIK", "Elektrik"),
    (r"VERGI TAHSILAT|TASIT V|MTV|HARC|GIB |GELIR IDARESI|TRAFIK CEZA", "Vergi & Harç"),
    (r"ATM|PARA ?CEKME|NAKIT ?CEKIM|NAKIT ?ODEME", "Nakit Çekim"),
    (r"\bKIRA\b|KIRA ODEME|AIDAT", "Kira & Aidat"),
    (r"MASRAF TAHSIL|HESAP ISLETIM|KOMISYON|EFT UCRET|HAVALE UCRET", "Banka Faiz & Ücret"),
    (r"ECZANE|HASTANE|KLINIK|SAGLIK", "Sağlık"),
    (r"TOBACCO|TEKEL", "Tütün"),
    (r"D&R|KITAP|KIRTASIYE", "Kitap & Hobi"),
    (r"CICEK", "Hediye & Çiçek"),
    (r"UDEMY|COURSERA|OKUL|KURS", "Eğitim"),
]

CATEGORIES = sorted({c for _, c in DEFAULT_RULES} | {"Banka Faiz & Ücret", "Diğer", UNCATEGORIZED})


def categorize(description: str, txn_type: str, rules: list[tuple[str, str]]) -> str:
    if txn_type == FEE:
        return "Banka Faiz & Ücret"
    if txn_type == PAYMENT:
        return "Ödeme"
    norm = normalize(description)
    for pattern, category in rules:
        try:
            if re.search(pattern, norm):
                return category
        except re.error:
            continue
    return UNCATEGORIZED


def suggest_pattern(description: str) -> str:
    """Kullanıcı elle kategori verince otomatik kural önerisi: açıklamanın ilk anlamlı kelimesi(leri)."""
    norm = strip_gateway(normalize(description))
    norm = re.sub(r"[*/].*$", "", norm).strip() or norm  # 'FACEBK *ABC/DUBLIN' -> 'FACEBK'
    words = [w for w in norm.split() if not re.fullmatch(r"[\d\-]+", w)][:2]
    return re.escape(" ".join(words)) if words else re.escape(norm)


def fold_key(text: str) -> str:
    """Karşılaştırma anahtarı: büyük harf, Türkçe karaktersiz, boşluksuz ("Nakit Ödeme" = "NakitÖdeme" = "NAKITODEME")."""
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return "".join((text or "").translate(tr).upper().split())
