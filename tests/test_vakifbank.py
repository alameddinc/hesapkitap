from datetime import date

import pytest

from app.parsers import parse_statement
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND
from app.parsers.vakifbank import _parse_txn_line
from tests.conftest import fixture_bytes

CARD = "4543***5678"


def p(line):
    return _parse_txn_line(line, CARD, "TEST")


def test_simple_purchase_with_points():
    t = p("26.09.2026 ORNEK BEBEK/IZMIR 1,248.60 125")
    assert (t.txn_date, t.description, t.amount, t.txn_type, t.points) == (date(2026, 9, 26), "ORNEK BEBEK/IZMIR", 1248.60, PURCHASE, 125)


def test_foreign_currency_prefix():
    t = p("09.10.2026 21.30 USD Hetzner Online/www.hetzner.c 953.62")
    assert (t.description, t.amount, t.orig_amount, t.orig_currency) == ("Hetzner Online/www.hetzner.c", 953.62, 21.30, "USD")


def test_installment_with_remaining():
    t = p("20.09.2026 TKPAY/ORNEK TEKNOLOJI 1. Taksit 6,240.00 5x6,240.00")
    assert (t.installment_no, t.installments_left, t.installment_left_amount, t.amount) == (1, 5, 6240.0, 6240.0)


def test_installment_glued_to_description():
    t = p("27.09.2026 0000000000123456789 police tah4. Taksit 1,315.40 5x1,315.40")
    assert (t.installment_no, t.installments_left, t.amount) == (4, 5, 1315.40)


def test_last_installment():
    t = p("12.07.2026 ORNEK COLINS 4. Taksit 712.30 Son Taksit")
    assert (t.installment_no, t.installments_left) == (4, 0)


def test_installment_refund_is_negative():
    t = p("20.08.2026 LC WAIKIKI 2. İade 65 +59.90 4x59.90")
    assert (t.txn_type, t.amount, t.installments_left) == (REFUND, -59.90, 4)


def test_payment_and_fee():
    assert p("18.09.2026 ÖDEMENİZ İÇİN TEŞEKKÜRLER +20,000.00").txn_type == PAYMENT
    assert p("16.10.2026 BSMV 71.15").txn_type == FEE
    assert p("16.10.2026 ALIŞVERİŞ FAİZİ (Oran:3.75) 474.30").txn_type == FEE


def test_card_suffix_stripped():
    assert p("07.10.2026 ORNEK MARKET İSTANBUL 5400*3456 268.40").description == "ORNEK MARKET İSTANBUL"


def test_previous_balance_skipped():
    assert p("13.09.2026 ÖNCEKİ DÖNEM HESAP ÖZETİ BAKİYENİZ 20,000.00") is None


@pytest.mark.parametrize("name", ["vakifbank_a.txt", "vakifbank_b.txt"])
def test_statement_reconciles(name):
    st = parse_statement(fixture_bytes(name))
    assert st.bank == "VakıfBank" and st.transactions
    assert st.reconciliation_diff() == 0
    assert all(t.card_holder for t in st.transactions)
    assert st.next_due_date and st.next_period_end and st.next_due_date > st.due_date


def test_supplementary_cards():
    st = parse_statement(fixture_bytes("vakifbank_b.txt"))
    assert {t.card for t in st.transactions} == {"5400***9012", "5400***3456"}
    assert {t.card_holder for t in st.transactions if t.card == "5400***3456"} == {"MEHMET YILMAZ"}
    assert round(sum(t.amount for t in st.transactions if t.txn_type == FEE), 2) == st.interest_fees == 676.40
