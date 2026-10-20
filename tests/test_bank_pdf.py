from app import bank_pdf
from app.bank_pdf import solve_chain, verify_chain


def test_ambiguous_columns_resolved_by_chain():
    """Kuveyt Türk düzeni: kart ödemesi satırında ilk sayı bakiye, tutar alt satırda. Tek satıra bakınca simetrik;
    sonraki satır ve kapanış bakiyesi doğru yorumu belirler."""
    rows = [
        {"date": "2026-07-19", "description": "FAST gelen", "numbers": [55000.00, 66157.88], "amount": 55000, "balance": 66157.88},
        {"date": "2026-07-19", "description": "Kart borcu", "numbers": [12357.52, 53800.36], "amount": -12357.52, "balance": 53800.36},
        {"date": "2026-08-15", "description": "FAST gelen", "numbers": [53000.00, 65357.52], "amount": 53000, "balance": 65357.52},
    ]
    solve_chain(rows, 11157.88, 65357.52)
    assert [r["amount"] for r in rows] == [55000.0, -53800.36, 53000.0]
    assert [r["balance"] for r in rows] == [66157.88, 12357.52, 65357.52]
    assert verify_chain(rows, 11157.88, 65357.52)["verified"]


def test_sign_fixed_from_balance():
    rows = [{"date": "2026-07-01", "description": "x", "amount": 100.0, "balance": 900.0}]  # işaret kaybolmuş
    r = verify_chain(rows, 1000.0, 900.0)
    assert rows[0]["amount"] == -100.0 and r["chain_fixed"] == 1 and r["verified"]


def test_opening_derived_when_missing_or_wrong(monkeypatch):
    """VakıfBank dökümünde açılış bakiyesi yok; model yanlış tahmin ederse ilk satırdan geriye hesaplanır."""
    from app import bank_pdf, receipt

    fake = {"opening_balance": 20227.32, "closing_balance": 14989.08, "rows": [
        {"date": "2026-07-01", "description": "Vergi", "numbers": [-2851.11, 17392.58], "amount": -2851.11, "balance": 17392.58},
        {"date": "2026-07-01", "description": "Vergi", "numbers": [-2403.50, 14989.08], "amount": -2403.50, "balance": 14989.08},
    ]}
    monkeypatch.setattr(receipt, "_gemini_json", lambda *a, **k: (fake, {}))
    r = bank_pdf._parse_text("x")
    assert r["verified"] and [x["amount"] for x in r["rows"]] == [-2851.11, -2403.5]


def test_restore_descriptions_from_raw_text():
    text = ("22.07.2026 19:39 2026010332692722 -8.000,00 18.231,19 Gönderilen havale\n"
            "TR68 0001 nolu AYŞE YILMAZ hesabından TR40 0001 nolu ZEYNEP ÖRNEK hesabına\n"
            "2026072200723344 referans nolu havale yapılmıştır.\n"
            "24.07.2026 14:34 2026010417636188 -600,00 17.631,19 Vodafone Tahsilatı\n")
    rows = [{"date": "2026-07-22", "description": "Gönderilen havale TR68 0001 nolu AYŞE YILMAZ hesabından",
             "amount": -8000.0},
            {"date": "2026-07-24", "description": "Elektrik", "amount": -600.0}]
    bank_pdf.restore_descriptions(rows, text)
    assert "ZEYNEP ÖRNEK hesabına" in rows[0]["description"]
    assert rows[0]["description"].startswith("Gönderilen havale")
    assert rows[1]["description"] == "Elektrik"  # başı uyuşmuyor → dokunma
