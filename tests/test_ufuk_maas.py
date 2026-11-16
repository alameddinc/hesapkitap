"""Ufuk maaş yatınca kayar (kullanıcı kararı 16.11): dönem sonuna ≤7 gün kaldı ve bu dönemin maaşı yattıysa Ödemelerden
sonra / forecast_balance / in_horizon ufku sonraki dönem sonu. Maaş yatmadıysa eski kural (son gün). Ay dönümü 17:
2026-12 = 17 Kas – 16 Ara, 2027-01 = 17 Ara – 16 Oca. Kart, tutar ve kişiler uydurmadır."""
from datetime import date

import pytest

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction

CARD = "5400***0002"
INST = 1500.0
SALARY = 40000.0


def _freeze(monkeypatch, d):
    class _D(date):
        @classmethod
        def today(cls):
            return d
    monkeypatch.setattr(finance, "date", _D)


@pytest.fixture()
def book(client, tmpdb):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 50000.0}).status_code == 200
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=date(2026, 11, 13), due_date=date(2026, 11, 23),
                  total_debt=3000.0, min_payment=600.0, credit_limit=90000, period_activity=3000.0,
                  next_period_end=date(2026, 12, 13), next_due_date=date(2026, 12, 23))
    s.transactions = [Transaction(txn_date=date(2026, 11, 5), description="ORNEK MARKET", amount=1500.0, txn_type=PURCHASE, card=CARD),
                      Transaction(txn_date=date(2026, 9, 5), description="ORNEK BEYAZ ESYA", amount=INST, txn_type=PURCHASE,
                                  card=CARD, installment_no=2, installments_left=4, installment_left_amount=INST)]
    tmpdb.save_statement(b"st-ufuk", "e.pdf", s)
    sid = next(x["id"] for x in tmpdb.list_statements())
    tmpdb.mark_statement_paid(sid, 3000.0, "2026-11-20", None)
    tmpdb.create_recurring({"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                            "day_of_month": 15, "start_month": "2026-09"})
    for d in ("2026-09-15", "2026-10-15", "2026-11-14"):
        tmpdb.create_entry({"entry_date": d, "description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş"})
    return tmpdb


def _maas(tmpdb, d):
    return tmpdb.create_entry({"entry_date": d, "description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş"})


def _nw(monkeypatch, d):
    _freeze(monkeypatch, d)
    return finance.net_worth(d)


def _kart_ana(nw):
    return {x["due_date"] for x in nw["pending_cards_items"]}


def _kart_later(nw):
    return {x["due_date"] for x in nw["spendable_later"]["items"] if x["type"] == "card_estimate"}


def test_no_salary_yet_cards_later(book, monkeypatch):
    nw = _nw(monkeypatch, date(2026, 12, 14))
    assert nw["spendable_horizon"] == "2026-12-16"
    assert "2026-12-23" in _kart_later(nw) and not _kart_ana(nw)


def test_salary_entered_moves_horizon(book, monkeypatch):
    _maas(book, "2026-12-15")
    nw = _nw(monkeypatch, date(2026, 12, 15))
    assert nw["spendable_horizon"] == "2027-01-16"
    assert _kart_ana(nw) == {"2026-12-23"} and not _kart_later(nw)
    est = next(p for p in finance.payables(date(2026, 12, 15)) if p["type"] == "estimate")
    assert est["in_horizon"] is True
    # forecast: Aralık maaşı nakitte (girildi), beklenen gelir yalnız Ocak maaşı — çift sayım yok
    assert nw["spendable_after_income"]["income"] == SALARY
    p = {x["key"]: x["amount"] for x in nw["forecast_balance"]["parts"]}
    assert p["income"] == SALARY and nw["forecast_balance"]["horizon"] == "2027-01-16"


def test_early_salary_before_holiday(book, monkeypatch):
    """Bayram öncesi 13 Ara'da erken yattı: 13'ünde (dönem sonuna 3 gün) ufuk kayar."""
    eid = _maas(book, "2026-12-13")
    assert next(e for e in book.list_entries() if e["id"] == eid)["month"] == "2026-12"
    nw = _nw(monkeypatch, date(2026, 12, 13))
    assert nw["spendable_horizon"] == "2027-01-16"


def test_late_salary_last_day_rule(book, monkeypatch):
    """Maaş 18 Ara'ya kaydı: 14 Ara'da ufuk 16 Ara; 16 Ara (son gün) eski kuralla kayar."""
    assert _nw(monkeypatch, date(2026, 12, 14))["spendable_horizon"] == "2026-12-16"
    assert _nw(monkeypatch, date(2026, 12, 16))["spendable_horizon"] == "2027-01-16"


def test_more_than_7_days_left_no_shift(book, monkeypatch):
    """Dönem sonuna 8+ gün varken maaş girilse de (ör. 8 Ara erken) ufuk kaymaz."""
    _maas(book, "2026-12-08")
    assert _nw(monkeypatch, date(2026, 12, 8))["spendable_horizon"] == "2026-12-16"


def test_no_salary_item_old_rule(client, tmpdb, monkeypatch):
    tmpdb.set_cycle_start_day(17)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 1000.0}).status_code == 200
    _freeze(monkeypatch, date(2026, 12, 14))
    assert finance.spend_horizon(date(2026, 12, 14))["end"] == date(2026, 12, 16)
