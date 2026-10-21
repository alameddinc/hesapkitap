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
