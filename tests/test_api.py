def test_entries_and_recurring_flow(client):
    r = client.post("/api/recurring", json={"description": "Maaş", "amount": 80000, "kind": "gelir",
                                            "category": "Maaş", "start_month": "2026-01"})
    rid = r.json()["id"]
    client.post("/api/recurring", json={"description": "Kira", "amount": 20000, "kind": "gider",
                                        "category": "Kira & Aidat", "start_month": "2026-01"})
    client.post("/api/entries", json={"entry_date": "2026-10-10", "description": "Psikolog", "amount": 3000,
                                      "kind": "gider", "category": "Sağlık"})

    s = client.get("/api/summary", params={"month": "2026-10"}).json()
    assert s["totals"]["manual_expense"] == 3000
    assert s["pending"]["count"] == 2  # maaş + kira onay bekliyor

    client.post(f"/api/recurring/{rid}/confirm", json={"month": "2026-10", "amount": 82000})
    s = client.get("/api/summary", params={"month": "2026-10"}).json()
    assert s["totals"]["income"] == 82000 and s["pending"]["count"] == 1

    rows = client.get("/api/transactions", params={"month": "2026-10"}).json()
    psik = next(r for r in rows if r["description"] == "Psikolog")
    client.patch(f"/api/entries/{psik['id']}", json={"amount": 3500})
    client.delete(f"/api/entries/{psik['id']}")
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["manual_expense"] == 0
    assert len(client.get("/api/plan", params={"months": 3}).json()["months"]) == 3


def test_upload_and_edit_fee(client, upload):
    res = upload()
    assert all(r["status"] == "saved" and r["reconciliation_diff"] == 0 and not r["ai"] for r in res)
    assert upload(["vakifbank_a.txt"])[0]["status"] == "duplicate"

    rows = client.get("/api/transactions", params={"month": "2026-10"}).json()
    fees = [r for r in rows if r["kind"] == "faiz_ucret" and r["account"] == "5400***9012"]
    before = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["fees"]
    client.patch(f"/api/card-transactions/{fees[0]['id']}", json={"txn_type": "harcama", "category": "Diğer"})  # faiz değil
    client.patch(f"/api/card-transactions/{fees[1]['id']}", json={"is_deleted": True})                     # sil
    after = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["fees"]
    assert round(before - after, 2) == round(fees[0]["amount"] + fees[1]["amount"], 2)
    client.patch(f"/api/card-transactions/{fees[1]['id']}", json={"is_deleted": False})                    # geri al
    assert any(r["id"] == fees[1]["id"] for r in client.get("/api/transactions", params={"month": "2026-10"}).json())


def test_unknown_bank_without_ai(client, tmp_path, monkeypatch):
    import app.parsers as parsers

    monkeypatch.setattr(parsers, "extract_text", lambda data, password=None: "BAŞKA BANKA EKSTRESİ")
    res = client.post("/api/statements/upload", files=[("files", ("x.pdf", b"%PDF", "application/pdf"))]).json()
    assert res[0]["status"] == "error" and "Banka tanınamadı" in res[0]["message"]


def test_credit_card_entry_links_to_statement(client, upload):
    # Ekstre gelmeden fişten girildi: 14.09 USTA TESİSAT 2.750 (vakifbank_b'de var)
    r = client.post("/api/entries", json={"entry_date": "2026-09-13", "description": "Usta tesisat", "amount": 2750,
                                          "kind": "gider", "category": "Ev & Tamirat", "note": "mutfak lavabo",
                                          "payment": "kredi_karti"}).json()
    nakit = client.post("/api/entries", json={"entry_date": "2026-09-13", "description": "Pazar", "amount": 2750,
                                              "kind": "gider", "payment": "nakit"}).json()
    assert r["linked"] is False
    assert client.get("/api/summary", params={"month": "2026-09"}).json()["totals"]["manual_expense"] == 5500

    res = upload()
    assert sum(x["linked"] for x in res) == 1  # sadece kredi kartı olan eşleşir
    assert client.get("/api/summary", params={"month": "2026-09"}).json()["totals"]["manual_expense"] == 2750

    oct_rows = client.get("/api/transactions", params={"month": "2026-10"}).json()
    card = next(x for x in oct_rows if x["source"] == "card" and "TESİSAT" in x["description"])
    assert card["category"] == "Ev & Tamirat" and "mutfak lavabo" in card["note"]

    client.post(f"/api/entries/{r['id']}/link", json={"txn_id": None})
    cands = client.get(f"/api/entries/{r['id']}/candidates").json()
    assert cands and cands[0]["id"] == card["id"]
    client.post(f"/api/entries/{r['id']}/link", json={"txn_id": card["id"]})
    assert client.post(f"/api/entries/{nakit['id']}/link", json={"txn_id": card["id"]}).status_code == 409


def test_duplicate_income_in_period_warns(client, tmpdb):
    from datetime import date

    from app import finance

    tmpdb.set_cycle_start_day(17)
    for d in ("2026-10-15", "2026-09-19"):  # ikisi de 17 Eyl – 16 Eki dönemine düşer
        client.post("/api/entries", json={"entry_date": d, "description": "Maaş", "amount": 1000, "kind": "gelir"})
    ins = finance.insights("2026-10", today=date(2026, 10, 19))
    assert any("Maaş" in i["title"] and "2 kez" in i["title"] for i in ins)


def test_recurring_counted_once_per_period(client, tmpdb):
    """Maaş günü ay dönümüne yakın/öbür tarafta olsa da her dönemde tek kez sayılır."""
    tmpdb.set_cycle_start_day(17)
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 1000, "kind": "gelir",
                                              "day_of_month": 15, "start_month": "2026-01"}).json()["id"]
    client.post(f"/api/recurring/{rid}/confirm", json={"month": "2026-10"})
    # geçmiş maaşlar yanlış günle (19) girildi: yine de kendi aylarına bağlanır, Ekim'e çift düşmez
    for d in ("2026-09-19", "2026-08-19", "2026-07-19"):
        client.post("/api/entries", json={"entry_date": d, "description": "maaş", "amount": 1000, "kind": "gelir"})
    months = sorted(e["month"] for e in tmpdb.list_entries())
    assert months == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert all(e["recurring_id"] == rid for e in tmpdb.list_entries())
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["income"] == 1000


def test_occurrence_when_salary_after_cycle_day(client, tmpdb):
    """Maaş 20'sinde, ekstre 2'sinde kesiliyor (ay dönümü 3): Kasım dönemi maaşı 20 Ekim."""
    tmpdb.set_cycle_start_day(3)
    assert tmpdb.occurrence_date("2026-11", 20) == "2026-10-20"
    assert tmpdb.occurrence_date("2026-11", 1) == "2026-11-01"
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 1000, "kind": "gelir",
                                              "day_of_month": 20, "start_month": "2026-01"}).json()["id"]
    rows = client.get("/api/transactions", params={"month": "2026-11"}).json()
    assert next(r for r in rows if r.get("pending"))["date"] == "2026-10-20"
    # hafta sonu yüzünden 18 Ekim'de erken yattı → yine Kasım dönemi, tek kez
    client.post("/api/entries", json={"entry_date": "2026-10-18", "description": "Maaş", "amount": 1000, "kind": "gelir"})
    s = client.get("/api/summary", params={"month": "2026-11"}).json()
    assert s["totals"]["income"] == 1000 and s["pending"]["count"] == 0


def test_variable_salary(client, tmpdb):
    """Brüt maaş: net her ay değişir. Onaylarken gerçek tutar, ileriki aylar için aya özel tutar."""
    from datetime import date

    from app import finance

    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 220000, "kind": "gelir",
                                              "day_of_month": 15, "start_month": "2026-01"}).json()["id"]
    client.post(f"/api/recurring/{rid}/confirm", json={"month": "2026-10", "amount": 214350.75})
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["income"] == 214350.75

    client.put(f"/api/recurring/{rid}/months/2026-12", json={"amount": 205000})  # Aralık'ta vergi dilimi
    months = {m["month"]: m for m in finance.plan(3, today=date(2026, 11, 2))["months"]}
    assert months["2026-11"]["income"] == 220000 and months["2026-12"]["income"] == 205000
    # bekleyen satır ve onay da aya özel tutarı kullanır
    rows = client.get("/api/transactions", params={"month": "2026-12"}).json()
    assert next(r for r in rows if r.get("pending"))["amount"] == 205000
    client.post(f"/api/recurring/{rid}/confirm", json={"month": "2026-12"})
    assert client.get("/api/summary", params={"month": "2026-12"}).json()["totals"]["income"] == 205000
    client.put(f"/api/recurring/{rid}/months/2026-12", json={"amount": None})  # şablona dön
    assert tmpdb.recurring_overrides() == {}


def test_receipt_teaches_legal_name_alias(client, upload, tmpdb):
    """Fişte marka 'Ornek Kahve', unvan 'ORNEK GIDA SAN. LTD. STI.'; ekstrede unvan geçer."""
    # ekstrede unvanla bir işlem olsun
    from app.parsers.base import PURCHASE, Statement, Transaction
    from datetime import date
    st = Statement(bank="Test", card_no="1111***2222", period_end=date(2026, 11, 13), due_date=date(2026, 11, 23),
                   total_debt=675, min_payment=None, credit_limit=None, period_activity=675)
    st.transactions.append(Transaction(txn_date=date(2026, 10, 20), description="ORNEK GIDA SANAYI VE",
                                       amount=675, txn_type=PURCHASE, card="1111***2222"))
    tmpdb.save_statement(b"x-test", "t.pdf", st)
    assert tmpdb.list_rules() and not any("ORNEK" in r["pattern"] for r in tmpdb.list_rules())

    client.post("/api/entries", json={"entry_date": "2026-10-20", "description": "Ornek Kahve", "amount": 675,
                                      "kind": "gider", "category": "Kafe & Restoran", "payment": "kredi_karti",
                                      "legal_name": "ORNEK GIDA SAN.VE DIŞ.TİC.LTD.ŞTİ."})
    assert tmpdb.merchant_aliases() == {"ORNEK GIDA": "ORNEK KAHVE"}  # "SAN.VE…" ve "SANAYI VE" aynı köke iner
    rows = client.get("/api/transactions", params={"month": "2026-11"}).json()
    card = next(r for r in rows if r["source"] == "card")
    assert card["merchant"] == "ORNEK KAHVE"           # ekstre satırı markayla gruplanır
    assert card["category"] == "Kafe & Restoran"       # unvan için kural öğrenildi
    linked = next(e for e in tmpdb.list_entries() if e["description"] == "Ornek Kahve")
    assert linked["linked_txn_id"] == card["id"]       # tutar+tarihle eşleşti


def test_encrypted_pdf_password_flow(client, monkeypatch, tmpdb):
    """Şifreli ekstre: önce şifre istenir, doğru şifreyle okunur, 'hatırla' denirse sonra sormaz."""
    import app.parsers as parsers
    from app.parsers import PasswordRequired
    from tests.conftest import FIXTURES

    text = (FIXTURES / "vakifbank_a.txt").read_text(encoding="utf-8")

    def fake(data, password=None):
        if data.startswith(b"ENC:") and password != "123456":
            raise PasswordRequired("PDF şifreli")
        return text

    monkeypatch.setattr(parsers, "extract_text", fake)
    f = lambda: [("files", ("e.pdf", b"ENC:1", "application/pdf"))]  # noqa: E731
    assert client.post("/api/statements/upload", files=f()).json()[0]["status"] == "password_required"
    r = client.post("/api/statements/upload", files=f(), data={"password": "yanlis"}).json()[0]
    assert r["status"] == "password_required" and "yanlış" in r["message"]
    r = client.post("/api/statements/upload", files=f(), data={"password": "123456", "remember": "true"}).json()[0]
    assert r["status"] == "saved" and r["reconciliation_diff"] == 0
    assert tmpdb.pdf_passwords() == ["123456"]
    hints = client.get("/api/settings/pdf-passwords").json()
    assert hints == [{"index": 0, "hint": "1••••6"}] and "123456" not in str(hints)
    # hatırlanan şifreyle başka ekstre kendiliğinden açılır
    tmpdb.delete_statement(tmpdb.list_statements()[0]["id"])
    assert client.post("/api/statements/upload", files=f()).json()[0]["status"] == "saved"


def test_summary_carries_positive_balance(client):
    client.post("/api/entries", json={"entry_date": "2026-01-05", "description": "Maaş", "amount": 1000, "kind": "gelir",
                                      "category": "Maaş"})
    client.post("/api/entries", json={"entry_date": "2026-01-06", "description": "Kira", "amount": 300, "kind": "gider",
                                      "category": "Kira & Aidat", "payment": "havale"})
    client.post("/api/entries", json={"entry_date": "2026-02-05", "description": "Kira", "amount": 900, "kind": "gider",
                                      "category": "Kira & Aidat", "payment": "havale"})
    m1 = client.get("/api/summary", params={"month": "2026-01"}).json()["totals"]
    m2 = client.get("/api/summary", params={"month": "2026-02"}).json()["totals"]
    m3 = client.get("/api/summary", params={"month": "2026-03"}).json()["totals"]
    assert m1["carry_in"] == 0 and m1["balance"] == 700
    assert m2["carry_in"] == 700 and m2["balance"] == -200
    assert m3["carry_in"] == 0  # eksi devretmez
