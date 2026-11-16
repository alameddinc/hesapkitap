"""Kart tahmini (U3, 16.11): yeni açılan taksitlerin ilk taksiti değişken harcamaya dahil, değişken tahmin son 3 dönem
3:2:1 ağırlıklı. Sentetik geçmişte (artan harcama + her ay yeni taksitli alışveriş) geriye dönük sınama: yeni yöntemin
ortalama mutlak yüzde hatası eskisinden (taksitsiz medyan) düşük, sistematik düşük tahmin (bias) azalır. Kart
numaraları, işyerleri ve tutarlar uydurmadır."""
from datetime import date

import pytest

from app import card_backtest, finance
from app.parsers.base import PURCHASE, Statement, Transaction

CARD = "5400***6600"


def _save(tmpdb, i, lines):
    pe = date(2026, 1 + i, 15)
    s = Statement(bank="Örnek Bank", card_no=CARD, period_end=pe, due_date=date(pe.year, pe.month, 25),
                  total_debt=round(sum(x.amount for x in lines), 2), min_payment=100.0, credit_limit=500000,
                  period_activity=0, next_period_end=date(pe.year, pe.month + 1, 15),
                  next_due_date=date(pe.year, pe.month + 1, 25))
    s.transactions = lines
    tmpdb.save_statement(f"bt-{i}".encode(), "e.pdf", s)


@pytest.fixture()
def history(tmpdb):
    """9 dönem: taksitsiz harcama her ay %6 artar; her ay 6 taksitli yeni bir alışveriş (taksit 1.500) açılır."""
    for i in range(9):
        pe = date(2026, 1 + i, 15)
        lines = [Transaction(txn_date=date(pe.year, pe.month, 3), description="ORNEK MARKET",
                             amount=round(10000 * 1.06 ** i, 2), txn_type=PURCHASE, card=CARD)]
        for k in range(max(0, i - 5), i + 1):  # k. ayda açılan taksitli alışverişin bu aydaki taksiti
            no = i - k + 1
            lines.append(Transaction(txn_date=date(2026, 1 + k, 5), description=f"ORNEK MAGAZA {k}", amount=1500.0,
                                     txn_type=PURCHASE, card=CARD, installment_no=no, installments_left=6 - no,
                                     installment_left_amount=1500.0 if no < 6 else None))
        _save(tmpdb, i, lines)
    return tmpdb


def test_new_method_beats_old(history):
    res = card_backtest.backtest()
    old, new = res["median"], res["weighted"]
    assert old["n"] == new["n"] >= 5
    assert new["mape"] < old["mape"]
    assert old["bias"] < 0 and abs(new["bias"]) < abs(old["bias"])  # eski yöntem sistematik düşük


def test_first_installment_counts_as_variable(history):
    p = finance.card_projection()[CARD]
    # son 3 dönem: (taksitsiz + yeni alışverişin ilk taksiti) 3:2:1
    v = [round(10000 * 1.06 ** i, 2) + 1500.0 for i in (6, 7, 8)]
    assert p["variable_avg"] == round((3 * v[2] + 2 * v[1] + v[0]) / 6, 2)


def test_variable_estimate_methods():
    assert finance.variable_estimate([]) == 0.0
    assert finance.variable_estimate([100.0]) == 100.0
    assert finance.variable_estimate([100.0, 400.0]) == (3 * 400 + 2 * 100) / 5
    assert finance.variable_estimate([1, 2, 3, 100.0], "median") == 2.5
    # tek uç ay: ağırlıklı tahminin en çok yarısı
    assert finance.variable_estimate([1000.0, 1000.0, 7000.0]) == 4000.0
