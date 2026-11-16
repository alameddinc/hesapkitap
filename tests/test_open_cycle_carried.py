"""Açık dönem (kesilecek ekstre) "devreden" kuralı: kesimden önceki POSTING_DAYS içinde girilmiş, ekstreyle eşleşmemiş
elle kart kaydı sonraki ekstreye devretmiş sayılır. Ama kesilmiş ekstrede aynı tutarlı (±%1 / ₺1), yakın tarihli
(±POSTING_DAYS), başka kayda bağlanmamış satır varsa harcama ZATEN o ekstrede (card_debt): açık dönemde (so_far,
pending_cards, Ödemelerden sonra) ikinci kez sayılmaz. Kart numarası, işyerleri ve tutarlar uydurmadır."""
from datetime import date, timedelta

import pytest

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction


@pytest.fixture(autouse=True)
def _genis_ufuk(monkeypatch):
    """Bu dosya Ödemelerden sonra kalemlerinin kuralını sınar; TEK UFUK (spend_horizon, lead 16.11) bugünün takvimine
    bağlı olduğundan burada sabit ve geniş (bugün + 400 gün, yalnız içinde bulunulan dönem); ufuk kuralı
    tests/test_spendable_horizon.py'de sabit tarihlerle."""
    from datetime import timedelta as _td

    from app import finance as _f

    monkeypatch.setattr(_f, "spend_horizon", lambda today: {"end": today + _td(days=400), "periods": [_f.current_period(today)],
                                                             "later_end": today + _td(days=430)})


CARD = "4543***7788"
T = date.today()
CUT = T - timedelta(days=5)


def _statement(tmpdb):
    lines = [Transaction(txn_date=CUT - timedelta(days=2), description="ORNEK MARKET A.S.", amount=812.40, txn_type=PURCHASE, card=CARD),
             Transaction(txn_date=CUT - timedelta(days=20), description="ORNEK GIYIM", amount=1500.0, txn_type=PURCHASE, card=CARD)]
    s = Statement(bank="Örnek Bank", card_no=CARD, period_end=CUT, due_date=T + timedelta(days=5), total_debt=2312.40,
                  min_payment=500.0, credit_limit=50000, period_activity=2312.40, next_period_end=CUT + timedelta(days=30),
                  next_due_date=CUT + timedelta(days=40))
    s.transactions = lines
    tmpdb.save_statement(b"st-carried", "e.pdf", s)


def _card_entry(tmpdb, d: date, amount: float, desc: str) -> int:
    return tmpdb.create_entry({"entry_date": d.isoformat(), "description": desc, "amount": amount, "kind": "gider",
                               "category": "Market & Gıda", "payment": "kredi_karti", "card_last4": CARD[-4:]})


def _cycle():
    return finance.open_cycles(T)[CARD]


def test_unmatched_entry_already_in_statement_is_not_carried(tmpdb):
    _statement(tmpdb)
    in_st = _card_entry(tmpdb, CUT - timedelta(days=1), 812.0, "Market")  # ekstrede 812,40 (yuvarlanmış elle tutar)
    carried = _card_entry(tmpdb, CUT - timedelta(days=1), 640.0, "Ornek Eczane")  # ekstrede yok: devretti
    after = _card_entry(tmpdb, CUT + timedelta(days=2), 300.0, "Ornek Kafe")  # kesimden sonra
    es = {e["id"]: e for e in tmpdb.list_entries()}
    assert es[in_st]["linked_txn_id"] is None  # eşleşme kaçağı (tutar 40 kuruş farklı)
    c = _cycle()
    ids = {i["entry_id"] for i in c["items"] if i["type"] == "harcama"}
    assert in_st not in ids and {carried, after} <= ids
    assert c["spent"] == 940.0
    pend = {x["account"]: x["amount"] for x in finance.net_worth(T)["pending_cards_items"]}
    assert pend[CARD] == 940.0


def test_statement_row_counts_once_for_two_entries(tmpdb):
    """Ekstrede tek satır, elle iki aynı kayıt: biri ekstrede sayılır, öteki devreden (satır bir kez kullanılır)."""
    _statement(tmpdb)
    a = _card_entry(tmpdb, CUT - timedelta(days=2), 812.40, "Market")
    b = _card_entry(tmpdb, CUT - timedelta(days=1), 812.40, "Market")
    ids = {i["entry_id"] for i in _cycle()["items"] if i["type"] == "harcama"}
    assert len(ids & {a, b}) == 1
