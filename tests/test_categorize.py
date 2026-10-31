from app.categorize import DEFAULT_RULES, UNCATEGORIZED, categorize, suggest_pattern
from app.parsers.base import FEE, PURCHASE
from app.textnorm import merchant_name, normalize


def cat(desc):
    return categorize(desc, PURCHASE, DEFAULT_RULES)


def test_normalize_turkish():
    assert normalize("Uber Eats Market İSTANBUL ışık") == "UBER EATS MARKET ISTANBUL ISIK"


def test_rules():
    assert cat("Uber Eats Yemek") == "Yemek Siparişi"
    assert cat("Uber Eats Market ORNEK") == "Market & Gıda"
    assert cat("FACEBK *ABC123XYZ9/Dublin") == "Reklam"
    assert cat("IYZICO/SPOTIFY.COM/ISTANBUL") == "Dijital Abonelik"
    assert cat("ANTHROPIC* CLAUDE SUB/SAN FRANCI") == "AI & Yazılım Abonelik"
    assert cat("OTOYOL YATIRIM VE İŞLETME/ORNEK") == "Ulaşım & Araç"
    assert cat("XYZ BILINMEYEN") == UNCATEGORIZED


def test_fee_type_wins():
    assert categorize("BSMV", FEE, DEFAULT_RULES) == "Banka Faiz & Ücret"


def test_suggest_pattern_and_merchant():
    assert suggest_pattern("ORNEK YAPI TICARET/ISTANBUL") == r"ORNEK\ YAPI"
    assert merchant_name("FACEBK *ABC123XYZ9/Dublin") == "FACEBK"


def test_merchant_groups_reference_codes():
    assert merchant_name("Google CLOUD Q1W2E3/Dublin") == "GOOGLE CLOUD"
    assert merchant_name("GOOGLE*CLOUD R4T5Y6/CC GOOGLE.COM") == "GOOGLE CLOUD"
    assert merchant_name("FILE F123 ORNEK K") == "FILE F123 ORNEK"


def test_payment_gateway_stripped():
    assert merchant_name("MokaUnited/ORNEK MOBILYA") == "ORNEK MOBILYA"
    assert merchant_name("IYZICO/SPOTIFY.COM/ISTANBUL") == "SPOTIFY"
    assert merchant_name("ÖDEAL//ORNEK KASAP/İSTANBUL") == "ORNEK KASAP"
    assert merchant_name("TKPAY/ORNEK TEKNOLOJI") == "ORNEK TEKNOLOJI"
    assert suggest_pattern("MokaUnited/ORNEK MOBILYA") == r"ORNEK\ MOBILYA"


def test_merchant_aliases_across_banks():
    assert merchant_name("GOOGLE *Google One/LONDON") == merchant_name("Google One/London") == "GOOGLE ONE"
    assert merchant_name("SpotifyAB/Stockholm") == merchant_name("IYZICO/SPOTIFY.COM/ISTANBUL") == "SPOTIFY"
    assert merchant_name("GOOGLE*CLOUD R4T5Y6/CC GOOGLE.COM") == "GOOGLE CLOUD"
    assert merchant_name("OPENAI *CHATGPT SUBSCR/4158799686") == merchant_name("OPENAI/SAN FRANCISCO") == "OPENAI"


def test_removed_default_rules_kept_for_existing_users(tmpdb):
    """Varsayılan kural listeden çıkınca eski kurulumlarda kullanıcı kuralı olarak kalır."""
    with tmpdb.connect() as c:
        c.execute("INSERT INTO rules (pattern, category, priority) VALUES ('ESKI KURAL', 'Market & Gıda', 1999)")
        c.execute("UPDATE settings SET value = '2' WHERE key = 'rules_version'")
    tmpdb.init_db()
    rules = tmpdb.list_rules()
    assert any(r["pattern"] == "ESKI KURAL" and r["priority"] == 500 for r in rules)


def test_merchant_of_record_gateways_show_real_seller():
    from app.textnorm import merchant_name

    assert merchant_name("PADDLE.NET* GEOGUESSR/08081781535") == "GEOGUESSR"
    assert merchant_name("PADDLE.NET* GEOGUESSR/London") == "GEOGUESSR"
    assert merchant_name("PADDLE.NET* SILENTCUT/080817815") == "SILENTCUT"
    assert merchant_name("FS *SUBLIME HQ") == "SUBLIME HQ"
    assert merchant_name("GOOGLE *Google One") == "GOOGLE ONE"  # mevcut davranış değişmedi


def test_duplicate_subscription_charges_warned(tmpdb):
    from datetime import date

    from app import finance

    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end) VALUES ('d', 'VakıfBank', '5400***2222', '2026-06-30')")
        sid = c.execute("SELECT id FROM statements").fetchone()[0]
        for d, desc, amt in (("2026-06-15", "PADDLE.NET* GEOGUESSR/08081781535", 968.52),
                             ("2026-06-19", "PADDLE.NET* GEOGUESSR/London", 948.0),
                             ("2026-01-27", "PADDLE.NET* AITURKAPPS/08081781535", 44.72),
                             ("2026-06-01", "NETFLIX.COM", 229.99), ("2026-07-01", "NETFLIX.COM", 229.99)):
            c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
                      "VALUES (?, ?, ?, ?, 'harcama', 'AI & Yazılım Abonelik')", (sid, d, desc, amt))
    w = finance.duplicate_charges(date(2026, 7, 10))
    assert len(w) == 1 and w[0]["title"].startswith("Geoguessr: 2 benzer çekim")  # aylık Netflix (30 gün arayla) uyarı değil
