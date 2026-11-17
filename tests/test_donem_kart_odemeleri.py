"""Dönemde ödenen kart ekstreleri (yetenek summary_card_payments): Özet / Hareketler "Kart ödemeleri" bilgi satırı.
Bilgidir: harcama ve gelir toplamlarına katılmaz. Tutarlar, banka ve kart numaraları uydurmadır."""
from datetime import date

import pytest

from app.parsers.base import PURCHASE, Statement, Transaction

CARD_A, CARD_B = "4111***0001", "5400***0002"


def _st(pe, due, total, card, bank="Örnek Bank"):
    s = Statement(bank=bank, card_no=card, period_end=pe, due_date=due, total_debt=total, min_payment=round(total * 0.4, 2),
                  credit_limit=50000, previous_balance=0.0, period_activity=total, payments=0.0)
    s.transactions = [Transaction(txn_date=pe, description="ORNEK MAGAZA", amount=total, txn_type=PURCHASE, card=card)]
    return s


@pytest.fixture()
def odenmis(tmpdb):
    """Ekim ekstresi (A) 8.000 → 20 Kas'ta ödendi; Ekim ekstresi (B) 3.000 → 2 Kas'ta ödendi; Eylül (A) 5.000 → 20 Eki'de."""
    tmpdb.save_statement(b"a9", "a9.pdf", _st(date(2026, 9, 28), date(2026, 10, 22), 5000.0, CARD_A))
    tmpdb.save_statement(b"a10", "a10.pdf", _st(date(2026, 10, 28), date(2026, 11, 22), 8000.0, CARD_A))
    tmpdb.save_statement(b"b10", "b10.pdf", _st(date(2026, 10, 25), date(2026, 11, 5), 3000.0, CARD_B, bank="Mavi Bank"))
    ids = {(s["card_no"], s["period_end"]): s["id"] for s in tmpdb.list_statements()}
    tmpdb.mark_statement_paid(ids[(CARD_A, "2026-09-28")], 5000.0, "2026-10-20")
    tmpdb.mark_statement_paid(ids[(CARD_A, "2026-10-28")], 8000.0, "2026-11-20")
    tmpdb.mark_statement_paid(ids[(CARD_B, "2026-10-25")], 3000.0, "2026-11-02")
    return ids


def test_donemde_odenen_ekstreler(tmpdb, odenmis):
    from app import finance

    k = finance.card_payments_in("2026-11")
    assert k["total"] == 11000.0 and k["count"] == 2
    assert [(x["card_no"], x["amount"], x["paid_at"], x["period_end"]) for x in k["items"]] == [
        (CARD_A, 8000.0, "2026-11-20", "2026-10-28"), (CARD_B, 3000.0, "2026-11-02", "2026-10-25")]
    assert k["items"][0]["statement_id"] == odenmis[(CARD_A, "2026-10-28")]
    assert k["items"][1]["title"] == f"Mavi Bank {CARD_B}"
    eki = finance.card_payments_in("2026-10")
    assert (eki["total"], eki["count"]) == (5000.0, 1)
    assert finance.card_payments_in("2026-12") == {"total": 0.0, "count": 0, "items": []}


def test_toplamlara_katilmaz(tmpdb, odenmis):
    """Ödendi işareti dönemin harcamasını, gelirini ve kategorilerini değiştirmez."""
    from app import finance

    once = finance.summary("2026-11", today=date(2026, 11, 25))
    for sid in odenmis.values():
        tmpdb.mark_statement_paid(sid, None, None)
    sonra = finance.summary("2026-11", today=date(2026, 11, 25))
    assert sonra["card_payments"]["count"] == 0 and once["card_payments"]["total"] == 11000.0
    for k in ("expense", "income", "net", "card_spend", "manual_expense"):
        assert once["totals"][k] == sonra["totals"][k], k
    assert once["by_category"] == sonra["by_category"]


def test_pencere_disi_isaret_sayilmaz(tmpdb):
    """Geç yüklenen eski ekstre "yükleme günü ödendi" diye işaretlenmişse (son ödemeden çok sonra) ödeme sayılmaz."""
    from app import finance

    tmpdb.save_statement(b"old", "old.pdf", _st(date(2026, 6, 28), date(2026, 7, 22), 4000.0, CARD_A))
    sid = tmpdb.list_statements()[0]["id"]
    tmpdb.mark_statement_paid(sid, 4000.0, "2026-11-10")
    assert finance.card_payments_in("2026-11")["count"] == 0


def test_api_ve_yetenek(client, odenmis):
    meta = client.get("/api/meta").json()
    assert "summary_card_payments" in meta["capabilities"]
    s = client.get("/api/summary", params={"month": "2026-11"}).json()
    assert s["card_payments"]["total"] == 11000.0
    assert {x["card_no"] for x in s["card_payments"]["items"]} == {CARD_A, CARD_B}
