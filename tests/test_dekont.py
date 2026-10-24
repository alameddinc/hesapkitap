from app import dekont

DEKONT = """DEKONT
MÜŞTERİ NO : 12345678 İŞLEM TARİHİ : 24/10/2026 - 15:22
TCKN/VKN/YKN : 1234567**** VALÖR TARİHİ : 24/10/2026
IBAN : TR12 0001 2009 9990 0001 2345 67 ETTN NO : -
İŞLEM TÜRÜ : Giden FAST
GÖNDEREN : AYŞE ÖRNEK
GÖNDEREN IBAN : TR12 0001 2009 9990 0001 2345 67
ALICI : ALİ ÖRNEK
ALICI IBAN : TR99 0006 4000 0000 0000 0000 01
ALICI BANKA : Örnek Bankası A.Ş.
ÖDEME AMACI : Kira
SORGU NO : 1234567890
İŞLEM TUTARI (TL) : 4,850.00
"""


def test_parse_dekont():
    assert dekont.is_dekont(DEKONT)
    p = dekont.parse(DEKONT)
    assert (p["date"], p["amount"], p["kind"], p["bank"], p["counterparty"]) == ("2026-10-24", 4850.0, "gider", "Halkbank", "ALİ ÖRNEK")
    gelen = DEKONT.replace("GÖNDEREN IBAN : TR12 0001 2009 9990 0001 2345 67", "GÖNDEREN IBAN : TR55 0001 0000 0000 0000 0000 99")
    assert dekont.parse(gelen)["kind"] == "gelir"


def test_dekont_upload_keeps_balance_and_merges_with_statement(client, tmpdb, monkeypatch):
    import app.parsers as parsers

    monkeypatch.setattr(parsers, "extract_text", lambda data, pw=None: DEKONT)
    tmpdb.set_account_balance("Halkbank", 50000, "2026-10-20", "dokum")
    r = client.post("/api/statements/upload", files={"files": ("Dekont.pdf", b"%PDF", "application/pdf")}).json()[0]
    assert r["status"] == "saved" and r.get("dekont")
    acc = next(a for a in tmpdb.list_accounts() if a["name"] == "Halkbank")
    assert acc["balance"] == 50000 and acc["as_of"] == "2026-10-20"  # bakiye çapası değişmedi
    assert acc["current"] == 45150  # işlem bakiyeden düştü
    # tekrar yükleme eklemez
    assert client.post("/api/statements/upload", files={"files": ("Dekont.pdf", b"%PDF", "application/pdf")}).json()[0]["status"] == "saved"
    with tmpdb.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM entries WHERE import_hash LIKE 'dekont:%'").fetchone()[0] == 1
    # o ayın dökümü gelince aynı işlem birleşir
    rows = [{"date": "2026-10-24", "description": "FAST ALI ORNEK", "amount": -4850.0, "balance": 45150.0, "type": "gider",
             "hash": "h1"}, {"date": "2026-10-25", "description": "Market", "amount": -150.0, "balance": 45000.0,
                             "type": "gider", "hash": "h2"}]
    st = tmpdb.import_bank_rows(rows, account="Halkbank", owners=[])
    with tmpdb.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM entries WHERE ABS(amount - 4850) < 0.01").fetchone()[0] == 1
    assert st["tekrar"] == 1
