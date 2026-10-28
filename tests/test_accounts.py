"""Hesaplar tamponu ve net varlık."""
from datetime import date

from app import bank_import, finance


def test_short_bank_names():
    assert bank_import.short_bank("Türkiye Halk Bankası A.Ş") == "Halkbank"
    assert bank_import.short_bank("KUVEYT TÜRK KATILIM BANKASI A.Ş.") == "Kuveyt Türk"
    assert bank_import.short_bank("VakıfBank") == "VakıfBank"
    assert bank_import.short_bank("Ziraat Katılım Bankası") == "Ziraat Katılım"
    assert bank_import.short_bank("") == "Banka hesabı"


def test_statement_import_fills_balance_and_older_does_not_override(client, tmpdb):
    rows = [{"date": "2026-10-18", "description": "x", "amount": -10.0, "type": "gider", "hash": "a", "balance": 1000.0},
            {"date": "2026-10-20", "description": "y", "amount": -20.0, "type": "gider", "hash": "b", "balance": 980.0}]
    tmpdb.import_bank_rows(rows, account="Türkiye Halk Bankası A.Ş")
    acc = tmpdb.list_accounts()
    assert [(a["name"], a["balance"], a["as_of"], a["source"]) for a in acc] == [("Halkbank", 980.0, "2026-10-20", "dokum")]
    tmpdb.import_bank_rows([{"date": "2026-09-01", "description": "z", "amount": 5.0, "type": "gelir", "hash": "c",
                             "balance": 50.0}], account="Halkbank")
    assert tmpdb.list_accounts()[0]["balance"] == 980.0


def test_net_worth_with_manual_cash_cards_and_debts(client, tmpdb):
    tmpdb.set_account_balance("Halkbank", 100000, "2026-10-20", "dokum")
    client.post("/api/accounts", json={"name": "Cüzdan", "kind": "nakit", "balance": 1500, "as_of": "2026-10-20"})
    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                  "VALUES ('k', 'Örnek', '1111***2222', '2026-10-13', '2026-10-30', 20000)")
    client.post("/api/debts", json={"person": "Ali", "direction": "alacak", "amount": 600, "debt_date": "2026-10-10"})
    nw = finance.net_worth(date(2026, 10, 21))
    assert nw["cash"] == 101500 and nw["card_debt"] == 20000 and nw["receivable"] == 600
    assert nw["net"] == 101500 - 20000 + 600
    aid = next(a["id"] for a in tmpdb.list_accounts() if a["name"] == "Cüzdan")
    client.patch(f"/api/accounts/{aid}", json={"balance": 900})
    assert next(a for a in tmpdb.list_accounts() if a["id"] == aid)["balance"] == 900
    assert client.delete(f"/api/accounts/{aid}").json()["ok"]


def test_net_worth_lists_all_credit_cards_and_unknown_banks(client, tmpdb):
    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt, paid_amount, paid_at) "
                  "VALUES ('a', 'Örnek', '1111***2222', '2026-10-13', '2026-10-23', 5000, 5000, '2026-10-20')")
    tmpdb.import_bank_rows([{"date": "2026-10-01", "description": "GIDEN FAST - X TARAFINDAN GÖNDERILEN", "amount": -10.0,
                             "type": "virman", "hash": "v"}], account="Türkiye Halk Bankası A.Ş")
    nw = finance.net_worth(date(2026, 10, 21))
    assert [(c["title"], c["unpaid"], c["paid_at"]) for c in nw["credit_cards"]] == [("Örnek 1111***2222", 0, "2026-10-20")]
    assert nw["unknown_banks"] == ["Halkbank"]
    tmpdb.set_account_balance("Halkbank", 100, "2026-10-21", "elle")
    assert finance.net_worth(date(2026, 10, 21))["unknown_banks"] == []


def test_entries_move_account_balance_after_anchor(client, tmpdb):
    client.post("/api/accounts", json={"name": "VakıfBank", "kind": "banka", "balance": 10000, "as_of": "2026-10-20"})
    acc = next(a for a in tmpdb.list_accounts() if a["name"] == "VakıfBank")
    # çapadan önceki tarihli kayıt bakiyeyi değiştirmez (zaten içinde)
    client.post("/api/entries", json={"entry_date": "2026-10-10", "description": "Eski", "amount": 500, "kind": "gider",
                                      "payment": "havale", "account_id": acc["id"]})
    client.post("/api/entries", json={"entry_date": "2026-10-22", "description": "Market", "amount": 300, "kind": "gider",
                                      "payment": "nakit", "account_id": acc["id"]})
    client.post("/api/entries", json={"entry_date": "2026-10-25", "description": "Ek", "amount": 1300, "kind": "gelir",
                                      "account_id": acc["id"]})
    # borç ödemesi de hesaptan
    did = client.post("/api/debts", json={"person": "Ali", "direction": "borc", "amount": 200, "debt_date": "2026-10-01"}).json()["id"]
    client.post(f"/api/debts/{did}/pay", json={"paid_at": "2026-10-23", "account_id": acc["id"]})
    cur = next(a for a in tmpdb.list_accounts() if a["id"] == acc["id"])["current"]
    assert cur == 10000 - 300 + 1300 - 200
    # kredi kartıyla ödenen giderde hesap tutulmaz
    r = client.post("/api/entries", json={"entry_date": "2026-10-26", "description": "Kart", "amount": 50, "kind": "gider",
                                          "payment": "kredi_karti", "account_id": acc["id"]}).json()
    assert next(e for e in tmpdb.list_entries() if e["id"] == r["id"])["account_id"] is None


def test_bank_import_binds_account_and_reupload_backfills(client, tmpdb):
    rows = [{"date": "2026-10-18", "description": "Market", "amount": -50.0, "type": "gider", "hash": "m1", "balance": 950.0}]
    tmpdb.import_bank_rows(rows)                                   # hesap adı bilinmeden (eski yükleme gibi)
    assert tmpdb.list_entries()[0]["account_id"] is None
    tmpdb.import_bank_rows(rows, account="Türkiye Vakıflar Bankası T.A.O.")   # tekrar yüklendi
    acc = next(a for a in tmpdb.list_accounts() if a["name"] == "VakıfBank")
    assert tmpdb.list_entries()[0]["account_id"] == acc["id"] and acc["current"] == 950.0
    # sonraki elle gider bakiyeyi düşürür
    client.post("/api/entries", json={"entry_date": "2026-10-19", "description": "x", "amount": 100, "kind": "gider",
                                      "payment": "nakit", "account_id": acc["id"]})
    assert next(a for a in tmpdb.list_accounts() if a["id"] == acc["id"])["current"] == 850.0


def test_recurring_confirm_uses_account(client, tmpdb):
    client.post("/api/accounts", json={"name": "Halkbank", "kind": "banka", "balance": 0, "as_of": "2026-10-01"})
    acc = tmpdb.list_accounts()[0]
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 1000, "kind": "gelir", "day_of_month": 15,
                                              "start_month": "2026-10", "account_id": acc["id"]}).json()["id"]
    eid = client.post(f"/api/recurring/{rid}/confirm", json={"month": tmpdb.period_of("2026-10-15")}).json()["id"]
    assert next(e for e in tmpdb.list_entries() if e["id"] == eid)["account_id"] == acc["id"]


def test_income_converted_to_opening_balance(client, tmpdb):
    aid = tmpdb.ensure_account("VakıfBank")
    eid = client.post("/api/entries", json={"entry_date": "2026-07-20", "description": "Ek", "amount": 35000, "kind": "gelir",
                                            "category": "Önceden Bakiye"}).json()["id"]
    client.post("/api/entries", json={"entry_date": "2026-08-01", "description": "Market", "amount": 1000, "kind": "gider",
                                      "payment": "nakit", "account_id": aid})
    r = client.post(f"/api/entries/{eid}/to-opening", json={"account_id": aid})
    assert r.status_code == 200
    acc = next(a for a in tmpdb.list_accounts() if a["id"] == aid)
    assert (acc["as_of"], acc["current"]) == ("2026-07-20", 34000)
    assert all(e["id"] != eid for e in tmpdb.list_entries())        # gelir sayılmıyor


def test_salary_import_teaches_recurring_account(client, tmpdb):
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 1000, "kind": "gelir", "category": "Maaş",
                                              "day_of_month": 15, "start_month": "2026-01"}).json()["id"]
    rows = [{"date": "2026-10-15", "description": "ISCI UCRET 2026 EKIM UCRET ODEMESI", "amount": 1100.0, "type": "gelir",
             "hash": "s1", "balance": 5000.0}]
    tmpdb.import_bank_rows(rows)                       # eski yükleme: banka adı yok
    assert next(r for r in tmpdb.list_recurring() if r["id"] == rid).get("account_id") is None
    tmpdb.import_bank_rows(rows, account="Türkiye Halk Bankası A.Ş")   # tekrar yüklendi
    acc = next(a for a in tmpdb.list_accounts() if a["name"] == "Halkbank")
    assert next(r for r in tmpdb.list_recurring() if r["id"] == rid)["account_id"] == acc["id"]


def test_cash_entry_goes_to_single_wallet(client, tmpdb):
    client.post("/api/accounts", json={"name": "Cüzdan", "kind": "nakit", "balance": 1000})
    client.post("/api/entries", json={"entry_date": "2026-10-28", "description": "Simit", "amount": 40, "kind": "gider",
                                      "category": "Kafe & Restoran", "payment": "nakit"})
    acc = next(a for a in tmpdb.list_accounts() if a["name"] == "Cüzdan")
    assert acc["current"] == 960
