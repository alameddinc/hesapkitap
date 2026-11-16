"""Ekstre "ödendi" tek kural (db.statement_paid): ödeme tarihi ileri bir gün seçilse de bugün ödenmiş sayılır; Ödemeler
durumu, Kartlarım (cards-summary unpaid), net durum ve kart borcu aynı rakamı verir. Geçmiş bir gün sorulursa o güne
kadarki ödeme sayılır. Kart numarası ve tutarlar uydurmadır."""
from datetime import date, timedelta

from app.parsers.base import PURCHASE, Statement, Transaction

CARD = "5400***4321"
T = date.today()


def _stmt(tmpdb) -> int:
    s = Statement(bank="Örnek Bank", card_no=CARD, period_end=T - timedelta(days=10), due_date=T + timedelta(days=5),
                  total_debt=1800.0, min_payment=400.0, credit_limit=30000, period_activity=1800.0,
                  next_period_end=T + timedelta(days=20), next_due_date=T + timedelta(days=30))
    s.transactions = [Transaction(txn_date=T - timedelta(days=15), description="ORNEK MARKET", amount=1800.0,
                                  txn_type=PURCHASE, card=CARD)]
    tmpdb.save_statement(b"st-paid-rule", "e.pdf", s)
    return tmpdb.list_statements()[0]["id"]


def test_future_dated_payment_counts_everywhere(client, tmpdb):
    sid = _stmt(tmpdb)
    later = (T + timedelta(days=3)).isoformat()
    assert client.post(f"/api/statements/{sid}/pay", json={"amount": 1800.0, "paid_at": later}).status_code == 200
    p = next(x for x in client.get("/api/payables").json() if x["type"] == "statement")
    assert p["status"] == "odendi"
    card = next(c for c in client.get("/api/cards/summary").json()["cards"] if c["card_no"] == CARD)
    assert card["unpaid"] == 0 and card["last_statement"]["unpaid"] == 0
    nw = client.get("/api/net-worth").json()
    assert nw["card_debt"] == 0
    assert tmpdb.card_debt_at(T.isoformat()) == 0
    # geçmiş gün: o gün henüz ödenmemişti
    st = tmpdb.list_statements()[0]
    assert tmpdb.statement_remaining(st, (T - timedelta(days=1)).isoformat()) == 1800.0
    assert tmpdb.statement_remaining(st, later) == 0
