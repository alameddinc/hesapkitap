"""Yöneticinin duyurusu (app/announce.py, /api/admin/announce*): yetki, doğrulama, hedef sayısı (platform, sürüm,
kapatan), gönderim (sahte FCM/APNs), sessiz saat kuyruğu, kişi başı bir kez, çift tıklama, yalnız bana, mağaza eylemi,
cihaz sürümünün güncellenmesi. Kişiler ve metinler uydurma."""
import json

import pytest

from tests.test_book import _member, owner  # noqa: F401 - fixture
from tests.test_push import FCM_TOKEN, IOS_TOKEN, _at, keys, nokeys  # noqa: F401 - fixture

DAY = _at("2026-11-11T14:00")
NIGHT = _at("2026-11-11T23:30")
MORNING = _at("2026-11-12T09:30")


@pytest.fixture()
def inline(monkeypatch):
    """Arka plan gönderimi aynı akışta; saat sabit (varsayılan gündüz)."""
    from app import announce, push

    clock = {"now": DAY}
    monkeypatch.setattr(announce, "_spawn", lambda fn: fn())
    monkeypatch.setattr(push, "_now", lambda: clock["now"])
    return clock


def _people():
    """Yönetici (android 1.0.0), Ayşe (iOS 1.0.4 (36)), Mehmet (android, sürüm bilinmiyor), Can (duyuruyu kapattı),
    Deniz (kapatılmış hesap)."""
    from app import push, system

    admin = system.user_by_name("admin")
    push.register(admin["id"], FCM_TOKEN, "android", app_version="1.0.0")
    ayse = system.create_user("ayse@ornek.com", None)
    push.register(ayse["id"], IOS_TOKEN, "ios", "production", "1.0.4 (36)")
    mehmet = system.create_user("mehmet@ornek.com", None)
    push.register(mehmet["id"], "fcm-mehmet-belgesi-0002-abcdefghij", "android")
    can = system.create_user("can@ornek.com", None)
    push.register(can["id"], "fcm-can-belgesi-0003-abcdefghijkl", "android", app_version="1.0.1")
    push.set_prefs(can["id"], {"types": {"duyuru": False}})
    deniz = system.create_user("deniz@ornek.com", None)
    push.register(deniz["id"], "fcm-deniz-belgesi-0004-abcdefghij", "android", app_version="1.0.1")
    system.update_user(deniz["id"], disabled=1)
    return admin, ayse, mehmet, can, deniz


BODY = {"title": "1.0.5 sürümü geldi", "body": "Yeni sürümde borç planı daha hızlı. Güncellemek için dokun.", "route": "store"}


# ---------------------------------------------------------------- yetki ve doğrulama

def test_non_admin_forbidden(owner, nokeys):  # noqa: F811
    m = _member(owner)
    assert m.post("/api/admin/announce/preview", json={}).status_code == 403
    assert m.post("/api/admin/announce", json=BODY).status_code == 403
    assert m.get("/api/admin/announcements").status_code == 403


@pytest.mark.parametrize("patch, text", [
    ({"title": "   "}, "Başlık boş"),
    ({"body": ""}, "Metin boş"),
    ({"title": "x" * 81}, "Başlık en çok 80"),
    ({"body": "y" * 301}, "Metin en çok 300"),
    ({"route": "https://ornek.com"}, "Yol"),
    ({"below_version": "yeni"}, "Sürüm"),
])
def test_validation(owner, nokeys, inline, patch, text):  # noqa: F811
    r = owner.post("/api/admin/announce", json={**BODY, **patch})
    assert r.status_code == 422 and text in r.json()["detail"]
    assert owner.get("/api/admin/announcements").json()["items"] == []


def test_too_long_raw_input_rejected(owner, nokeys):  # noqa: F811
    assert owner.post("/api/admin/announce", json={**BODY, "body": "z" * 5000}).status_code == 422
    assert owner.post("/api/admin/announce/preview", json={"platform": "windows"}).status_code == 422


# ---------------------------------------------------------------- hedef

def test_preview_counts(owner, nokeys):  # noqa: F811
    _people()
    r = owner.post("/api/admin/announce/preview", json={}).json()
    # yönetici + Ayşe + Mehmet; Can kapattı, Deniz kapalı hesap
    assert r == {"users": 3, "devices": 3, "by_platform": {"ios": 1, "android": 2}, "opted_out": 1, "enabled": False}
    r = owner.post("/api/admin/announce/preview", json={"platform": "ios"}).json()
    assert (r["users"], r["by_platform"]) == (1, {"ios": 1, "android": 0})
    # 1.0.4 altı: yönetici (1.0.0) + Mehmet (bilinmiyor); Ayşe 1.0.4 (36) hariç
    r = owner.post("/api/admin/announce/preview", json={"below_version": "1.0.4"}).json()
    assert (r["users"], r["devices"]) == (2, 2)
    r = owner.post("/api/admin/announce/preview", json={"below_version": "1.0.5"}).json()
    assert r["users"] == 3
    r = owner.post("/api/admin/announce/preview", json={"test_only": True}).json()
    assert (r["users"], r["devices"]) == (1, 1)
    assert owner.post("/api/admin/announce/preview", json={"below_version": "bir"}).status_code == 422


def test_version_compare():
    from app import announce

    assert announce.parse_version("1.0.4 (36)") == (1, 0, 4, 0)
    assert announce.parse_version("1.10") > announce.parse_version("1.9.9")
    assert announce.parse_version(None) is None and announce.parse_version("bilinmiyor") is None
    d = {"platform": "android", "app_version": "1.0.4"}
    assert not announce.device_matches(d, "all", "1.0.4") and announce.device_matches(d, "all", "1.0.5")
    assert not announce.device_matches(d, "ios") and announce.device_matches({"platform": "ios"}, "ios", "1.0.0")


# ---------------------------------------------------------------- gönderim

def test_send_daytime_store_action_and_history(owner, keys, inline):  # noqa: F811
    from app import push

    admin, ayse, mehmet, can, deniz = _people()
    r = owner.post("/api/admin/announce", json=BODY)
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["target_users"], a["target_devices"]) == (3, 3)
    [h] = owner.get("/api/admin/announcements").json()["items"]
    assert (h["sent"], h["failed"], h["queued"], h["status"], h["admin_name"]) == (3, 0, 0, "done", "admin")
    assert h["route"] == "store" and h["platform"] == "all" and h["test_only"] is False

    fcm = [json.loads(x.content)["message"] for x in keys.by_host("fcm")]
    assert {m["token"] for m in fcm} == {FCM_TOKEN, "fcm-mehmet-belgesi-0002-abcdefghij"}  # Can ve Deniz yok
    assert fcm[0]["data"] == {"route": "#/ozet", "kind": "duyuru", "id": f"duyuru:{a['id']}", "action": "store"}
    assert fcm[0]["notification"]["title"] == "1.0.5 sürümü geldi"
    [ios] = keys.by_host("apple")
    p = json.loads(ios.content)
    assert p["action"] == "store" and p["kind"] == "duyuru" and p["aps"]["alert"]["body"].startswith("Yeni sürümde")
    assert push.was_sent(ayse["id"], f"duyuru:{a['id']}") and not push.was_sent(can["id"], f"duyuru:{a['id']}")

    # aynı duyuru kişiye ikinci kez gitmez (send yeniden çağrılsa da)
    from app import announce

    n = len(keys.calls)
    assert announce.send(a["id"], [admin["id"], ayse["id"]]) == {"sent": 0, "failed": 0, "queued": 0}
    assert len(keys.calls) == n


def test_double_click_guard_and_force(owner, keys, inline):  # noqa: F811
    _people()
    assert owner.post("/api/admin/announce", json=BODY).status_code == 200
    r = owner.post("/api/admin/announce", json=BODY)
    assert r.status_code == 409 and r.json()["code"] == "duplicate" and "10 dakika" in r.json()["detail"]
    assert r.json()["existing"]["title"] == BODY["title"]
    assert owner.post("/api/admin/announce", json={**BODY, "body": BODY["body"] + " Teşekkürler."}).status_code == 200
    assert owner.post("/api/admin/announce", json={**BODY, "test_only": True}).status_code == 200  # test ayrı sayılır
    r = owner.post("/api/admin/announce", json={**BODY, "force": True})
    assert r.status_code == 200 and r.json()["target_users"] == 3
    assert len(owner.get("/api/admin/announcements").json()["items"]) == 4


def test_quiet_hours_queue_then_flush(owner, keys, inline):  # noqa: F811
    from app import push

    _people()
    inline["now"] = NIGHT
    a = owner.post("/api/admin/announce", json={**BODY, "route": "#/diger/borc-plani", "platform": "android"}).json()
    [h] = owner.get("/api/admin/announcements").json()["items"]
    assert (h["sent"], h["queued"], h["status"]) == (0, 2, "queued") and keys.calls == []
    q = [x for x in push.queue() if x["payload"].get("ann") == a["id"]]
    assert len(q) == 2 and q[0]["payload"]["route"] == "#/diger/borc-plani" and "action" not in q[0]["payload"]

    assert push.flush(NIGHT) == 0  # hâlâ sessiz saat
    assert push.flush(MORNING) == 2
    [h] = owner.get("/api/admin/announcements").json()["items"]
    assert (h["sent"], h["failed"], h["queued"], h["status"]) == (2, 0, 0, "done")
    assert keys.by_host("apple") == []  # yalnız android hedeflendi


def test_queued_payload_keeps_device_filter(owner, keys, inline):  # noqa: F811
    """Kuyruktaki duyuru, kişinin o arada eklediği hedef dışı cihaza (başka platform) gitmez."""
    from app import push, system

    _people()
    inline["now"] = NIGHT
    owner.post("/api/admin/announce", json={**BODY, "platform": "android", "below_version": "1.0.4"})
    mehmet = system.user_by_name("mehmet@ornek.com")
    push.register(mehmet["id"], "b2" * 32, "ios", "production", "1.0.0")
    push.flush(MORNING)
    assert keys.by_host("apple") == [] and len(keys.by_host("fcm")) == 2


def test_test_only_goes_to_admin_now(owner, keys, inline):  # noqa: F811
    from app import push, system

    _people()
    admin = system.user_by_name("admin")
    push.set_prefs(admin["id"], {"types": {"duyuru": False}})  # kendine deneme: tercih ve sessiz saat yok sayılır
    inline["now"] = NIGHT
    r = owner.post("/api/admin/announce", json={**BODY, "test_only": True}).json()
    assert (r["target_users"], r["target_devices"]) == (1, 1)
    [h] = owner.get("/api/admin/announcements").json()["items"]
    assert (h["sent"], h["queued"], h["test_only"]) == (1, 0, True)
    assert [json.loads(x.content)["message"]["token"] for x in keys.by_host("fcm")] == [FCM_TOKEN]


def test_failed_and_removed_devices_counted(owner, keys, inline):  # noqa: F811
    _people()
    keys.fcm = (500, None)
    owner.post("/api/admin/announce", json={**BODY, "platform": "android"})
    [h] = owner.get("/api/admin/announcements").json()["items"]
    assert (h["sent"], h["failed"]) == (0, 2)


# ---------------------------------------------------------------- tercih ve cihaz sürümü

def test_duyuru_pref_visible_to_everyone(owner, nokeys):  # noqa: F811
    m = _member(owner)
    p = m.get("/api/push/prefs").json()
    assert p["types"]["duyuru"] is True and p["labels"]["duyuru"] == "Duyurular ve yeni sürümler"
    assert m.put("/api/push/prefs", json={"types": {"duyuru": False}}).json()["types"]["duyuru"] is False


def test_register_updates_app_version(owner, nokeys):  # noqa: F811
    from app import push, system

    admin = system.user_by_name("admin")
    owner.post("/api/push/devices", json={"token": FCM_TOKEN, "platform": "android", "app_version": "1.0.0"})
    owner.post("/api/push/devices", json={"token": FCM_TOKEN, "platform": "android", "app_version": "1.0.4 (33)"})
    assert push.devices(admin["id"])[0]["app_version"] == "1.0.4 (33)"
    owner.post("/api/push/devices", json={"token": FCM_TOKEN, "platform": "android"})  # sürüm yok: eskisi kalır
    assert push.devices(admin["id"])[0]["app_version"] == "1.0.4 (33)"


def test_capability():
    from app import version

    assert "push_announce" in version.CAPABILITIES
