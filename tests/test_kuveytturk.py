from app.categorize import DEFAULT_RULES, categorize
from app.parsers import parse_statement
from app.parsers.base import FEE, PAYMENT, PURCHASE
from app.parsers.kuveytturk import TXN_RE, _clean_desc, amount
from app.textnorm import merchant_name
from tests.conftest import fixture_bytes


def test_amount_turkish_format():
    assert amount("1.234,56") == 1234.56
    assert amount("-12.345,67") == -12345.67


def test_line_with_installment():
    m = TXN_RE.match("29/09/2026 ORNEK PETROL ANKARA TR 640,00 TL İşlemin 1 / 9 Taksidi 71,11 TL 0.00")
    assert m and m["inst_no"] == "1" and m["inst_total"] == "9" and m["inst_amt"] == "71,11"


def test_location_stripped_for_cross_bank_grouping():
    assert _clean_desc("ORNEK CAFE İSTANBUL TR") == "ORNEK CAFE"
    assert merchant_name(_clean_desc("ORNEK CAFE İSTANBUL TR")) == merchant_name("ORNEK CAFE/İSTANBUL")
    assert merchant_name("HALKÖDE ÖD/POAŞ ORNEK") == "POAS ORNEK"


def test_rules():
    cat = lambda d: categorize(d, PURCHASE, DEFAULT_RULES)  # noqa: E731
    assert cat("DOCO PETROL ORNEK") == "Ulaşım & Araç"
    assert cat("ŞOK-1234- ORNEK") == "Market & Gıda"
    assert cat("HM HENNES ORNEK") == "Giyim"
    assert cat("İSKENDER ORNEK AŞ") == "Kafe & Restoran"


def test_statement():
    s = parse_statement(fixture_bytes("kuveytturk.txt"))
    assert s.bank == "Kuveyt Türk" and s.card_no == "4111***1111"
    assert s.reconciliation_diff() == 0
    assert s.next_due_date and s.next_due_date > s.due_date
    fees = [t for t in s.transactions if t.txn_type == FEE]
    assert fees and all(t.installments_left == 8 for t in fees)  # vade farkı taksitleri gelecek aylara yansır
    assert any(t.txn_type == PAYMENT and t.amount < 0 for t in s.transactions)
    inst = next(t for t in s.transactions if t.description == "ORNEK PETROL" and t.txn_type == PURCHASE)
    assert (inst.amount, inst.installment_no, inst.installments_left) == (71.11, 1, 8)  # toplam değil taksit tutarı
