"""Demoda gerçek marka / şirket adı yok: uydurma verinin yanında gerçek banka, zincir, işyeri ya da kurum adı görünmez.

Demo ekstreleri tests/fixtures altındaki sahte metinlerdir; bankaların okuyucuları (app/parsers) gerçek ekstre biçimini
tanımak için o metinlere bakar, o yüzden metin değişmez: okunduktan SONRA scrub_statement() bankayı ve işlem
açıklamalarını kurgu adlarla değiştirir (app/demo.py şablonu, app/demo_mode.py "örnek ekstre" düğmeleri). Kurgu adlar
varsayılan kurallarla doğru kategoriye düşmüyorsa RULES (demo kuralları) şablona yazılır; kategoriler eskisi gibi kalır.
Market zincirleri app/demo_prices.py CHAINS'tedir ("Market A" …); ulusal fiyat davranışı orada (NATIONAL).
"""
import re

# ekstreyi okuyan okuyucunun verdiği banka adı → demodaki kurgu ad
BANKS = {"VakıfBank": "Örnek Bank", "Kuveyt Türk": "Mavi Bank", "Garanti": "Yeşil Bank"}
DEFAULT_BANK = "Örnek Bank"
FICTIONAL_BANKS = frozenset({*BANKS.values(), DEFAULT_BANK})

# (desen, yerine) — sırayla, büyük/küçük harf duyarsız; önce tam satırlar, sonra tek tek markalar (yedek)
_DESC: list[tuple[str, str]] = [
    # vakifbank_a
    (r"OPENAI/SAN FRANCISCO", "ÖRNEK YAPAY ZEKA/SAN FRANCISCO"),
    (r"ANTHROPIC\* ?CLAUDE SUB/SAN FRANCI", "ÖRNEK AI ASİSTAN/SAN FRANCISCO"),
    (r"SCRIBD R\d+/\d+", "ÖRNEK E-KİTAP ABONELİĞİ"),
    (r"IYZICO/SPOTIFY\.COM/ISTANBUL", "ÖRNEK MÜZİK ABONELİĞİ/İSTANBUL"),
    (r"Hetzner Online/www\.hetzner\.c\w*", "ÖRNEK BULUT SUNUCU"),
    (r"Uber Eats Yemek", "ÖRNEK YEMEK SİPARİŞİ"),
    (r"TKPAY/ORNEK TEKNOLOJI", "ÖRNEK TEKNOLOJİ"),
    (r"OTOYOL YATIRIM VE İŞLETME/ORNEK", "ÖRNEK OTOYOL GEÇİŞİ"),
    # kuveytturk
    (r"OPET PETROL/ORNEK", "ÖRNEK AKARYAKIT"),
    (r"HALKÖDE ÖD/POAŞ ORNEK", "ÖRNEK AKARYAKIT İSTASYONU"),
    (r"M[İI]GROS ORNEK", "MARKET D"),
    (r"ŞOK-\d+- ORNEK", "MARKET C"),
    (r"H&M ORNEK AVM", "ÖRNEK GİYİM AVM"),
    (r"HM HENNES ORNEK", "ÖRNEK GİYİM MAĞAZASI"),
    (r"ORNEK KITAP D&R", "ÖRNEK KİTAPÇI"),
    # vakifbank_b ("örnek ekstre yükle")
    (r"LC ?WAIKIKI ORNEK", "ÖRNEK GİYİM"),
    (r"LC ?WAIKIKI", "ÖRNEK GİYİM"),
    (r"HEPSIPAY ?/HEPSIBU\w*", "ÖRNEK E-TİCARET"),
    (r"IYZICO/ORNEK MOBILYA", "ÖRNEK MOBİLYA"),
    (r"İSPARK GENEL MÜDÜRLÜK ÖKC", "ÖRNEK OTOPARK"),
    (r"ORNEK COLINS", "ÖRNEK JEAN"),
    (r"\bA101\b", "MARKET B"),
    # yedek: tek tek markalar (yeni bir örnek eklenirse gerçek ad sızmasın)
    (r"\bB[İI]M\b", "MARKET A"), (r"\bM[İI]GROS\b", "MARKET D"), (r"\bŞOK\b", "MARKET C"),
    (r"\bCARREFOUR ?SA\b|\bCARREFOUR\b", "MARKET E"),
    (r"\bOPET\b|\bSHELL\b|\bPOAŞ\b|\bPETROL OFİSİ\b", "ÖRNEK AKARYAKIT"),
    (r"\bSPOTIFY\b|\bNETFLIX\b|\bYOUTUBE\b", "ÖRNEK ABONELİK"), (r"\bH&M\b|\bHENNES\b", "ÖRNEK GİYİM"),
    (r"\bD&R\b", "ÖRNEK KİTAPÇI"), (r"\bIYZICO\b/?|\bHEPSIPAY\b/?|\bTKPAY\b/?", ""),
]
_DESC_RE = [(re.compile(p, re.I), r) for p, r in _DESC]

# kurgu adların kategorisi (varsayılan kurallar bunları tanımaz); kullanıcı kurallarından önce, varsayılanlardan önce
RULES: list[tuple[str, str]] = [
    (r"ORNEK YAPAY ZEKA|ORNEK AI ASISTAN", "AI & Yazılım Abonelik"),
    (r"ORNEK E-KITAP ABONELIGI|ORNEK MUZIK ABONELIGI|ORNEK ABONELIK", "Dijital Abonelik"),
    (r"ORNEK BULUT SUNUCU", "Yazılım & Bulut"),
    (r"ORNEK YEMEK SIPARISI", "Yemek Siparişi"),
    (r"ORNEK GIYIM|ORNEK JEAN", "Giyim"),
    (r"ORNEK E-TICARET", "Online Alışveriş"),
    (r"ORNEK MOBILYA", "Ev & Tamirat"),
]
RULE_PRIORITY = 400

# demo ekstrelerinin dosya adı (yükleme listesinde görünür)
FILES = {"vakifbank_a.txt": "ornek-bank-ekim.pdf", "vakifbank_b.txt": "ornek-bank-ek-kart.pdf",
         "kuveytturk.txt": "mavi-bank-ekim.pdf"}


def bank(name: str | None) -> str | None:
    if not name:
        return name
    return name if name in FICTIONAL_BANKS else BANKS.get(name, DEFAULT_BANK)


def description(text: str) -> str:
    out = text
    for rx, rep in _DESC_RE:
        out = rx.sub(rep, out)
    return " ".join(out.split()) or text


def scrub_statement(st):
    """Okunmuş ekstrede (app.parsers.base.Statement) banka ve işlem açıklamaları kurgu adla; yerinde, st döner."""
    st.bank = bank(st.bank)
    for t in st.transactions:
        t.description = description(t.description)
    return st


def add_rules() -> None:
    """Demo kurallarını (RULES) deftere yaz (yoksa)."""
    from app import db

    have = {(r["pattern"], r["category"]) for r in db.list_rules()}
    for p, cat in RULES:
        if (p, cat) not in have:
            db.add_rule(p, cat, RULE_PRIORITY)
