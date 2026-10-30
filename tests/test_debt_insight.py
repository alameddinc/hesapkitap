"""Özet uyarısı: kapanmış alacak sayılmaz, uyarı Borç / Alacak sayfasına götürür."""
from datetime import date


def _debt_insights(db):
    from app import finance

    return [i for i in finance.insights("2026-10", today=date(2026, 10, 30)) if "alacağın var" in i["title"]]


def test_settled_receivable_not_in_insight(tmpdb):
    db = tmpdb
    did = db.create_debt({"person": "Deniz Ak", "direction": "alacak", "amount": 742.25, "debt_date": "2026-10-29"})
    ins = _debt_insights(db)
    assert len(ins) == 1 and ins[0]["link"] == "borclar" and "Borç / Alacak sayfasından" in ins[0]["detail"]
    db.pay_debt(did, None, "2026-10-30")
    assert _debt_insights(db) == []
