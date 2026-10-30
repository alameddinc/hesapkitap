"""Ekstre denklemi faiz ve ücreti sayar (VakıfBank: önceki borç − ödemeler + harcamalar + faiz/ücret = yeni borç).
Özetteki 'Toplam Faiz ve Ücretler' okunamadığında faiz/ücret işlemlerinin toplamı kullanılır; kayıtlı ekstrede de."""
from datetime import date

from app.parsers.base import FEE, PAYMENT, PURCHASE, Statement, Transaction


def _st(activity: float, total: float, interest_fees: float = 0.0) -> Statement:
    txns = [Transaction(date(2026, 9, 1), "MARKET", 15000.00, PURCHASE, "4000***0001"),
            Transaction(date(2026, 9, 15), "AKDI FAIZ", 700.00, FEE, "4000***0001"),
            Transaction(date(2026, 9, 15), "BSMV", 53.80, FEE, "4000***0001"),
            Transaction(date(2026, 9, 2), "ODEME", -20000.00, PAYMENT, "4000***0001")]
    return Statement(bank="VakıfBank", card_no="4000***0001", period_end=date(2026, 9, 15), due_date=date(2026, 9, 25),
                     total_debt=total, min_payment=None, credit_limit=None, previous_balance=20000.00,
                     period_activity=activity, interest_fees=interest_fees, payments=20000.00, transactions=txns)


def test_fees_separate_line_not_read():
    st = _st(15000.00, 15540.00)
    assert st.fees() == 540.00 and st.reconciliation_diff() == 0


def test_fees_included_in_activity():
    st = _st(15540.00, 15540.00)
    assert st.reconciliation_diff() == 0


def test_fees_read_from_summary():
    assert _st(15000.00, 15540.00, interest_fees=540.00).reconciliation_diff() == 0


def test_missing_line_still_detected():
    st = _st(15000.00, 15540.00)
    st.transactions.pop(0)
    assert st.reconciliation_diff() == -15000.00


def test_api_returns_fees_for_saved_statement(client, tmpdb):
    tmpdb.save_statement(b"x-fees", "v.pdf", _st(15000.00, 15540.00))
    s = client.get("/api/statements").json()[0]
    assert s["fees"] == 540.00 and s["reconciliation_diff"] == 0
    assert round(s["previous_balance"] - s["payments"] + s["period_activity"] + s["fees"], 2) == s["total_debt"]


def test_confirm_statement_clears_check(client, tmpdb):
    st = _st(15000.00, 15540.00)
    st.transactions.pop(0)  # denklem tutmuyor
    sid = tmpdb.save_statement(b"x-confirm", "v.pdf", st)
    s = client.get("/api/statements").json()[0]
    assert s["needs_check"] and s["confirmed_at"] is None
    r = client.post(f"/api/statements/{sid}/confirm").json()
    assert r["ok"] and r["confirmed_at"]
    s = client.get("/api/statements").json()[0]
    assert not s["needs_check"] and s["confirmed_at"]
    assert client.delete(f"/api/statements/{sid}/confirm").json()["confirmed_at"] is None
    assert client.get("/api/statements").json()[0]["needs_check"]
    assert client.post("/api/statements/999/confirm").status_code == 404
