"""Abonelik ayrıntısı (app/sub_detail.py, GET /api/subscriptions/detail).

Bütün veriler uydurmadır: kart numaraları, işyeri adları ("ORNEKAI", "ORNEK MUZIK"), tutarlar ve kurlar elle yazılmış
örneklerdir; gerçek bir ekstreden alınmamıştır.
"""
from datetime import date, timedelta

import pytest

CARD = "4111********1111"
CARD2 = "5500********2222"
TODAY = date(2026, 11, 12)
AI = "AI & Yazılım Abonelik"


def _stmt(db, pe: date, rows: list, card: str = CARD) -> int:
    from app.parsers.base import Statement, Transaction

    txns = [Transaction(d, desc, amt, "harcama", card, orig_amount=fx[0] if fx else None,
                        orig_currency=fx[1] if fx else None) for d, desc, amt, fx in rows]
    s = Statement(bank="Örnek Bank", card_no=card, period_end=pe, due_date=pe + timedelta(days=10),
                  total_debt=round(sum(r[2] for r in rows), 2), min_payment=None, credit_limit=None, transactions=txns)
    return db.save_statement(f"ornek-{card}-{pe}".encode(), f"ornek-{pe}.pdf", s)


def _ym(y: int, m: int, k: int) -> tuple[int, int]:
    y, m = divmod(y * 12 + m - 1 + k, 12)
    return y, m + 1


def _set_cat(db, like: str, cat: str = AI):
    with db.connect() as c:
        c.execute("UPDATE transactions SET category = ? WHERE description LIKE ?", (cat, like))


@pytest.fixture
def ai14(tmpdb):
    """14 ay (Eyl 2025 – Eki 2026) her ayın 5'i dövizli abonelik: 20 USD, Haziran 2026'dan 25 USD (zam); kur oynar.
    Aynı kartta TL'li müzik aboneliği 9 ay, Mayıs 2026'da zam."""
    for k in range(14):
        y, m = _ym(2025, 9, k)
        usd = 25 if (y, m) >= (2026, 6) else 20
        rate = 40 + k * 0.5
        rows = [(date(y, m, 5), "ORNEKAI SUBSCRIPTION", round(usd * rate, 2), (usd, "USD"))]
        if k >= 5:
            rows.append((date(y, m, 9), "ORNEK MUZIK", 99.99 if (y, m) < (2026, 5) else 119.99, None))
        _stmt(tmpdb, date(y, m, 20), rows)
    _set_cat(tmpdb, "ORNEK%")
    return tmpdb


def test_card_detail_totals_months_fx(ai14):
    from app import finance, sub_detail

    sub = next(s for s in finance.subscriptions() if s["merchant"] == "ORNEKAI SUBSCRIPTION")
    d = sub_detail.card_detail("ORNEKAI SUBSCRIPTION", TODAY)
    assert d["kind"] == "card" and d["title"] == "Ornekai Subscription" and d["status"] == "aktif"
    assert d["amount"] == sub["monthly"] and d["per"] == "ay"
    # aynı gruplama: Tasarruf satırıyla birebir aynı işlemler
    ids = {f"c{t['id']}" for t in finance.subscription_groups()["ORNEKAI SUBSCRIPTION"]}
    assert {x["uid"] for x in d["transactions"]} == ids and len(ids) == sub["months"] == 14
    a = d["totals"]["all"]
    assert a["count"] == 14 and a["first_date"] == "2025-09-05" and a["text"] == "Eylül 2025'ten bu yana 14 ödeme"
    assert a["amount"] == pytest.approx(sum(x["amount"] for x in d["transactions"]))
    assert a["fx_text"] == f"{9 * 20 + 5 * 25} USD"
    l12 = d["totals"]["last12"]
    assert d["since12"] == l12["since"] == "2025-12" and l12["count"] == 11 and l12["text"] == "Son 12 ayda 11 ödeme"
    assert d["fx"] and "TL karşılığı" in d["fx_note"]
    months = d["months"]
    assert [m["month"] for m in months][:2] == ["2026-10", "2026-09"] and months[-1]["month"] == "2025-09"
    assert months[-1]["change"] is None and months[0]["fx_text"] == "25 USD" and months[0]["label"] == "Ekim 2026"
    jun = next(m for m in months if m["month"] == "2026-06")
    assert jun["change"]["kind"] == "zam" and jun["change"]["fx_text"] == "20 USD → 25 USD"
    # döviz tutarı aynı, TL kurla değişti: zam değil
    assert {m["change"]["kind"] for m in months if m["change"] and m["month"] != "2026-06"} == {"kur"}
    t0 = d["transactions"][0]
    assert t0["date"] == "2026-10-05" and t0["label"] == "Örnek Bank …1111" and t0["fx"] == "25 USD" and t0["via"] == "kart"
    assert t0["month"] == "2026-10"  # satırın dönemi (istemci işlemi bu dönemde bulur)


def test_card_detail_tl_price_up_and_cancel(ai14):
    from app import db, sub_detail

    d = sub_detail.card_detail("ORNEK MUZIK", TODAY)
    assert d["totals"]["all"]["count"] == 9 and not d["fx"] and d["fx_note"] is None
    may = next(m for m in d["months"] if m["month"] == "2026-05")
    assert may["change"] == {"kind": "zam", "diff": 20.0, "from": 99.99, "to": 119.99, "fx_text": None}
    assert all(m["change"] is None for m in d["months"] if m["month"] != "2026-05" and m["month"] != "2026-02")
    db.set_subscription_state("ORNEK MUZIK", "iptal", "2026-08-15", None, None)
    d = sub_detail.card_detail("ORNEK MUZIK", TODAY)
    assert d["status"] == "iptal" and d["status_date"] == "2026-08-15"
    assert d["after_cancel"] == {"amount": 239.98, "count": 2}
    assert [m["month"] for m in d["months"] if m["after_cancel"]] == ["2026-10", "2026-09"]
    assert [x["after_cancel"] for x in d["transactions"][:3]] == [True, True, False]


def test_card_detail_downgrade_over(ai14):
    from app import db, sub_detail

    db.set_subscription_state("ORNEK MUZIK", "dusuruldu", "2026-08-01", None, 59.99)
    d = sub_detail.card_detail("ORNEK MUZIK", TODAY)
    assert d["status"] == "dusuruldu" and d["amount"] == 59.99 and d["prev_amount"] == 119.99
    assert [m["month"] for m in d["months"] if m["over"]] == ["2026-10", "2026-09"]


def test_card_detail_unknown_and_api(ai14, client):
    r = client.get("/api/subscriptions/detail", params={"merchant": "ORNEK MUZIK"})
    assert r.status_code == 200 and r.json()["totals"]["all"]["count"] == 9
    assert client.get("/api/subscriptions/detail", params={"merchant": "YOK BOYLE"}).status_code == 404
    assert client.get("/api/subscriptions/detail").status_code == 422
    assert client.get("/api/subscriptions/detail", params={"merchant": "X", "app": "a1"}).status_code == 422
    assert client.get("/api/subscriptions/detail", params={"app": "a000"}).status_code == 404
    assert "subscription_detail" in client.get("/api/meta").json()["capabilities"]


def test_card_detail_multiple_charges_same_month(tmpdb):
    from app import sub_detail

    _stmt(tmpdb, date(2026, 9, 20), [(date(2026, 9, 5), "ORNEK BULUT", 50.0, None)])
    _stmt(tmpdb, date(2026, 10, 20), [(date(2026, 10, 5), "ORNEK BULUT", 50.0, None),
                                      (date(2026, 10, 18), "ORNEK BULUT", 50.0, None)])
    _set_cat(tmpdb, "ORNEK%")
    d = sub_detail.card_detail("ORNEK BULUT", TODAY)
    oct_ = d["months"][0]
    assert oct_["count"] == 2 and oct_["amount"] == 100.0 and oct_["change"]["kind"] == "adet"


def test_member_hidden_card_not_counted(tmpdb):
    """Ortak defter: gizli kartın işlemleri ne sayılır ne listelenir."""
    from app import book, sub_detail

    for m in (8, 9, 10):
        _stmt(tmpdb, date(2026, m, 20), [(date(2026, m, 5), "ORNEK DIZI", 100.0, None)])
        _stmt(tmpdb, date(2026, m, 20), [(date(2026, m, 7), "ORNEK DIZI", 100.0, None)], card=CARD2)
    _set_cat(tmpdb, "ORNEK%")
    assert sub_detail.card_detail("ORNEK DIZI", TODAY)["totals"]["all"]["count"] == 6
    member = {"id": 7, "username": "uye", "member_of": 1, "book_role": "full"}
    with tmpdb.use_space(tmpdb.db_path(), member):
        book.set_rules({"hidden_cards": [CARD2]})
        d = sub_detail.card_detail("ORNEK DIZI", TODAY)
        assert d["totals"]["all"]["count"] == 3 and d["totals"]["all"]["amount"] == 300.0
        assert all(x["label"].endswith("1111") for x in d["transactions"])


# ---------------------------------------------------------------- mağaza aboneliği

def test_store_detail_all_charges(tmpdb):
    """Mağaza adayının listesinde son 12 tahsilat var; ayrıntıda hepsi (14) ve toplamı."""
    from app import store_subs, sub_detail

    for k in range(14):
        y, m = _ym(2025, 9, k)
        _stmt(tmpdb, date(y, m, 20), [(date(y, m, 14), "APPLE.COM/BILL", 59.99 if (y, m) < (2026, 4) else 69.99, None)])
    it = next(i for i in store_subs.items(TODAY) if i["cycle"] == "aylik")
    assert len(it["charges"]) == 12
    d = sub_detail.store_detail(it["id"], TODAY)
    assert d["kind"] == "store" and d["title"] == it["title"] and d["store_label"] == "App Store" and d["per"] == "ay"
    assert d["totals"]["all"]["count"] == 14 and d["totals"]["all"]["amount"] == pytest.approx(7 * 59.99 + 7 * 69.99)
    assert next(m for m in d["months"] if m["month"] == "2026-04")["change"]["kind"] == "zam"
    assert d["transactions"][0]["uid"].startswith("c") and d["transactions"][0]["via"] == "kart"


def test_store_detail_bank_entry(tmpdb, client):
    """Hesap dökümünden gelen mağaza tahsilatı da işlemlerde (hesap adıyla)."""
    from app import db, store_subs

    for m in (8, 9, 10):
        db.create_entry({"entry_date": f"2026-{m:02d}-03", "description": "GOOGLE *Ornek Meditasyon", "amount": 89.99,
                      "kind": "gider", "category": "Diğer", "note": None})
    it = next(i for i in store_subs.items(TODAY) if i["store"] == "google")
    r = client.get("/api/subscriptions/detail", params={"app": it["id"]}).json()
    assert r["totals"]["all"]["count"] == 3
    assert {x["via"] for x in r["transactions"]} == {"hesap"} and r["transactions"][0]["uid"].startswith("m")
    assert r["transactions"][0]["label"] == "Hesap dökümü"
