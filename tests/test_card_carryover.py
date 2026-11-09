"""Provizyonda kalan kart harcaması: kesim 14'ünde, alışveriş 12'sinde; o ekstreye girmez, bir sonrakine devreder.
Elle kayıt "ekstre bekleniyor" kalır (Özet'te bir kez), sonraki ekstredeki satırla (işlem ya da işleme tarihiyle)
eşleşir, "ekstrede bulunamadı" sonraki ekstre gelmeden sorulmaz, Kartlar'da açık döneme girer. Tutarlar, işyerleri uydurma."""
from datetime import date

import pytest

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction

CARD = "4543***1234"


def _statement(tmpdb, pe: date, lines: list[tuple[date, str, float]], nxt: date | None = None):
    total = round(sum(a for _, _, a in lines), 2)
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=pe, due_date=date(pe.year, pe.month, 24),
                  total_debt=total, min_payment=100, credit_limit=50000, previous_balance=0, period_activity=total,
                  payments=0, next_period_end=nxt)
    s.transactions = [Transaction(txn_date=d, description=desc, amount=a, txn_type=PURCHASE, card=CARD)
                      for d, desc, a in lines]
    tmpdb.save_statement(f"st-{pe}".encode(), "e.pdf", s)


def _kirtasiye(client, d="2026-09-12", amount=340.0):
    r = client.post("/api/entries", json={"entry_date": d, "description": "Kırtasiye", "amount": amount, "kind": "gider",
                                          "category": "Alışveriş", "payment": "kredi_karti", "card_last4": "1234"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _entry(tmpdb, eid):
    return next(e for e in tmpdb.list_entries() if e["id"] == eid)


def _totals(client, month):
    return client.get("/api/summary", params={"month": month}).json()["totals"]


def _card_todo(client):
    return [r for r in client.get("/api/reminders").json() if r["kind"] == "kart_eslesmedi"]


def _sept(tmpdb):  # kesim 14 Eylül: 12 Eylül'deki kırtasiye bu ekstrede YOK (provizyonda)
    _statement(tmpdb, date(2026, 9, 14), [(date(2026, 9, 2), "ORNEK MARKET", 412.75)], nxt=date(2026, 10, 14))


@pytest.mark.parametrize("posted", [date(2026, 9, 12), date(2026, 9, 13), date(2026, 9, 16)])
def test_carried_purchase_waits_then_matches_next_statement(client, tmpdb, posted):
    eid = _kirtasiye(client)
    _sept(tmpdb)
    tmpdb.auto_link()
    assert _entry(tmpdb, eid)["linked_txn_id"] is None
    # Eylül ekstresi geldi ama harcama orada değil: "ekstre bekleniyor", Özet'te Eylül gideri (elle kart) bir kez
    t = _totals(client, "2026-09")
    assert t["card_pending"] == 340.0 and t["expense"] == round(412.75 + 340.0, 2)
    # sonraki ekstreye devretmesi normal: bu yüzden sorulmaz
    assert _card_todo(client) == []
    # Kartlar: sonraki kesimin "bugüne kadar"ına devreden olarak girer
    cyc = finance.open_cycles(date(2026, 9, 20))[CARD]
    item = next(i for i in cyc["items"] if i.get("entry_id") == eid)
    assert item["carried"] is True and cyc["spent"] == 340.0

    # Ekim ekstresinde satır (işlem tarihi ya da 4 gün sonraki işleme tarihiyle): kendiliğinden eşleşir
    _statement(tmpdb, date(2026, 10, 14), [(posted, "ORNEK KIRTASIYE", 340.0), (date(2026, 10, 1), "ORNEK FIRIN", 90.0)])
    assert tmpdb.auto_link() == 1
    assert _entry(tmpdb, eid)["linked_txn_id"] is not None
    # tek sayım: kart satırı ekstrenin dönemine (Ekim) yazılır, elle kayıt artık sayılmaz
    sep, oct_ = _totals(client, "2026-09"), _totals(client, "2026-10")
    assert sep["card_pending"] == 0 and sep["expense"] == 412.75
    assert oct_["card_pending"] == 0 and oct_["expense"] == 430.0
    assert _card_todo(client) == []


def test_carried_purchase_not_on_next_statement_is_asked(client, tmpdb):
    eid = _kirtasiye(client)
    _sept(tmpdb)
    assert _card_todo(client) == []
    _statement(tmpdb, date(2026, 10, 14), [(date(2026, 10, 1), "ORNEK FIRIN", 90.0)])
    tmpdb.auto_link()
    assert [t["id"] for t in _card_todo(client)] == [f"kart:{eid}"]  # iki ekstrede de yok: şimdi sorulur


def test_purchase_well_before_cut_still_asked_on_that_statement(client, tmpdb):
    eid = _kirtasiye(client, d="2026-09-05")  # kesimden 9 gün önce: o ekstrede olmalıydı
    _sept(tmpdb)
    tmpdb.auto_link()
    assert [t["id"] for t in _card_todo(client)] == [f"kart:{eid}"]
    cyc = finance.open_cycles(date(2026, 9, 20))[CARD]
    assert not any(i.get("entry_id") == eid for i in cyc["items"])  # açık döneme devretmiş sayılmaz


def test_late_line_needs_exact_single_candidate(client, tmpdb):
    eid = _kirtasiye(client)
    _sept(tmpdb)
    # iki aynı tutarlı geç satır: hangisi olduğu belli değil, kendiliğinden bağlanmaz
    _statement(tmpdb, date(2026, 10, 14), [(date(2026, 9, 16), "ORNEK KIRTASIYE", 340.0),
                                           (date(2026, 9, 18), "BASKA DUKKAN", 340.0)])
    assert tmpdb.auto_link() == 0 and _entry(tmpdb, eid)["linked_txn_id"] is None


def test_late_line_beyond_window_or_before_purchase_not_auto(client, tmpdb):
    eid = _kirtasiye(client)
    _sept(tmpdb)
    _statement(tmpdb, date(2026, 10, 14), [(date(2026, 9, 21), "ORNEK KIRTASIYE", 340.0),   # 9 gün sonra
                                           (date(2026, 9, 7), "ORNEK KIRTASIYE", 340.0)])   # 5 gün önce
    assert tmpdb.auto_link() == 0 and _entry(tmpdb, eid)["linked_txn_id"] is None
