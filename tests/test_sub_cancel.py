"""İptalden sonra çekim uyarısına karar ve iade eşleşmesi (app/sub_cancel.py, yetenek subscription_after_cancel).

Bütün veriler uydurmadır: kart numarası, işyeri adları ("ORNEK DIZI PLATFORMU"), tutarlar elle yazılmış örneklerdir.
"""
from datetime import date

import pytest

from tests.test_store_subs import _load, _months, _stmt

CARD = "4111********1111"
DESC = "ORNEK DIZI PLATFORMU ISTANBUL"


def _merchant() -> str:
    from app.textnorm import merchant_name

    return merchant_name(DESC)


@pytest.fixture
def dizi(client, tmpdb):
    """Her ay 5'i ₺149,90; 31 Ekim'de iptal edildi ama 5 Kasım'da yine çekildi."""
    _load(tmpdb, _months(2026, range(6, 12), 5, DESC, 149.90))
    r = client.put("/api/subscriptions", json={"merchant": _merchant(), "status": "iptal", "changed_at": "2026-10-31"})
    assert r.status_code == 200
    return client


def _sub(client):
    return next(s for s in client.get("/api/savings").json()["subscriptions"] if s["merchant"] == _merchant())


def _alerts(today=date(2026, 11, 25)):
    from app import finance

    return [i for i in finance.insights(finance.current_period(today), today) if i.get("link") == "tasarruf"
            and i.get("key", "").startswith(("iptal_cekim:", "iade_bekle:"))]


def _detail(client):
    return client.get("/api/subscriptions/detail", params={"merchant": _merchant()}).json()


def test_alert_carries_merchant_and_detail_info(dizi):
    assert _sub(dizi)["charged_after_cancel"] == 149.90 and _sub(dizi)["refund_requested_at"] is None
    [a] = _alerts()
    assert a["level"] == "kritik" and a["target"] == {"screen": "abonelikler", "merchant": _merchant()}
    d = _detail(dizi)
    after = [t for t in d["transactions"] if t["after_cancel"]]
    assert [t["date"] for t in after] == ["2026-11-05"] and after[0]["after_cancel_closed"] is None
    info = d["after_cancel_info"]
    assert info["open_amount"] == 149.90 and info["open_count"] == 1 and info["manage"] == []
    assert info["cancelled_at"] == "2026-10-31" and not info["refund_waiting"]


def test_refund_requested_then_refund_matches(dizi, tmpdb):
    r = dizi.post("/api/subscriptions/after-cancel", json={"merchant": _merchant(), "action": "iade_istedim"})
    assert r.status_code == 200
    [a] = _alerts()
    assert a["level"] == "uyari" and a["title"].endswith(": iade bekleniyor") and a["key"].startswith("iade_bekle:")
    assert _detail(dizi)["after_cancel_info"]["refund_waiting"]
    # iade ekstreye düştü (aynı işyeri, eksi tutar): çekim kapanır, uyarı gider
    _stmt(tmpdb, date(2026, 11, 28), [(date(2026, 11, 22), DESC, -149.90)], card=CARD)
    assert _alerts() == [] and _sub(dizi)["charged_after_cancel"] == 0
    d = _detail(dizi)
    t = next(t for t in d["transactions"] if t["date"] == "2026-11-05")
    assert not t["after_cancel"] and t["after_cancel_closed"] == "iade" and t["refund"]["date"] == "2026-11-22"
    assert d["after_cancel"] is None and d["after_cancel_info"]["closed_count"] == 1


def test_refund_matches_without_request(dizi, tmpdb):
    _stmt(tmpdb, date(2026, 11, 28), [(date(2026, 11, 9), DESC, -149.90)], card=CARD)
    assert _alerts() == []
    # başka tutardaki iade eşleşmez
    _load(tmpdb, [(date(2026, 12, 20), [(date(2026, 12, 5), DESC, 149.90), (date(2026, 12, 8), DESC, -50.0)])])
    [a] = _alerts(date(2026, 12, 25))
    assert a["level"] == "kritik" and "₺149,90" in a["detail"]


def test_last_charge_closes_until_new_charge(dizi, tmpdb):
    r = dizi.post("/api/subscriptions/after-cancel", json={"merchant": _merchant(), "action": "son_cekim"})
    assert r.status_code == 200 and _alerts() == []
    t = next(t for t in _detail(dizi)["transactions"] if t["date"] == "2026-11-05")
    assert t["after_cancel_closed"] == "son_cekim"
    # sonra yeni çekim: yeniden kritik, yalnız yeni tutar
    _load(tmpdb, [(date(2026, 12, 20), [(date(2026, 12, 5), DESC, 149.90)])])
    [a] = _alerts(date(2026, 12, 25))
    assert a["level"] == "kritik" and _sub(dizi)["charged_after_cancel"] == 149.90
    # geri al: iki çekim de açık
    dizi.post("/api/subscriptions/after-cancel", json={"merchant": _merchant(), "action": "sifirla"})
    assert _sub(dizi)["charged_after_cancel"] == round(2 * 149.90, 2)


def test_new_charge_after_refund_request_is_critical_again(dizi, tmpdb):
    dizi.post("/api/subscriptions/after-cancel", json={"merchant": _merchant(), "action": "iade_istedim"})
    _load(tmpdb, [(date(2026, 12, 20), [(date(2026, 12, 5), DESC, 149.90)])])
    [a] = _alerts(date(2026, 12, 25))
    assert a["level"] == "kritik" and a["key"].startswith("iptal_cekim:")


def test_continue_subscription_drops_state(dizi):
    dizi.post("/api/subscriptions/after-cancel", json={"merchant": _merchant(), "action": "son_cekim"})
    dizi.put("/api/subscriptions", json={"merchant": _merchant(), "status": "aktif"})
    assert _alerts() == []  # (kategorisi abonelik değil: aktifken listede yok)
    # yeniden iptal (başka gün): eski karar geçmez
    dizi.put("/api/subscriptions", json={"merchant": _merchant(), "status": "iptal", "changed_at": "2026-10-30"})
    [a] = _alerts()
    assert a["level"] == "kritik"


def test_errors(dizi, tmpdb):
    assert dizi.post("/api/subscriptions/after-cancel", json={"action": "son_cekim"}).status_code == 422
    assert dizi.post("/api/subscriptions/after-cancel", json={"merchant": "YOK BOYLE", "action": "son_cekim"}).status_code == 404
    assert dizi.post("/api/subscriptions/after-cancel", json={"merchant": _merchant(), "action": "bilinmez"}).status_code == 422
    dizi.put("/api/subscriptions", json={"merchant": _merchant(), "status": "dusuruldu", "new_amount": 99.9})
    assert dizi.post("/api/subscriptions/after-cancel", json={"merchant": _merchant(), "action": "son_cekim"}).status_code == 409


def test_store_subscription_actions(client, tmpdb):
    from app import store_subs

    _load(tmpdb, _months(2026, range(4, 12), 14, "APPLE.COM/BILL ITUNES.COM", 59.99))
    today = date.today()
    mon = next(i for i in store_subs.items() if i["cycle"] == "aylik")
    store_subs.act(mon["id"], "iptal", when="2026-10-20")
    assert store_subs.find(mon["id"])["charged_after_cancel"] == 59.99
    d = client.get("/api/subscriptions/detail", params={"app": mon["id"]}).json()
    info = d["after_cancel_info"]
    assert info["open_count"] == 1 and info["manage"] and info["manage"][0]["url"].startswith("https://")
    r = client.post("/api/subscriptions/after-cancel", json={"app": mon["id"], "action": "iade_istedim"})
    assert r.status_code == 200
    [a] = store_subs.cancel_alerts(today)
    assert a["level"] == "uyari" and a["key"].startswith("iade_bekle:store-")
    client.post("/api/subscriptions/after-cancel", json={"app": mon["id"], "action": "iade_geldi"})
    assert store_subs.cancel_alerts(today) == [] and store_subs.find(mon["id"])["charged_after_cancel"] == 0
    d = client.get("/api/subscriptions/detail", params={"app": mon["id"]}).json()
    assert d["after_cancel"] is None and d["after_cancel_info"]["closed_why"] == "iade_geldi"
    assert [t["after_cancel_closed"] for t in d["transactions"] if t["date"] > "2026-10-20"] == ["iade_geldi"]


def test_capability_listed(client):
    assert "subscription_after_cancel" in client.get("/api/meta").json()["capabilities"]
