"""Uygulama içi "Sorun bildir" (yetenek "feedback"): POST /api/feedback doğrulama, boyut/tür sınırları, saatlik sınır,
oturumsuz, demo ve ortak defter üyesi; yönetici listesi, durum/not, ekran görüntüsü; yöneticiye push. Mesajlar ve
kişiler uydurma."""
import json

import pytest
from fastapi.testclient import TestClient

from tests.test_book import _member, owner  # noqa: F401 - fixture

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64


@pytest.fixture(autouse=True)
def _sync_notify(monkeypatch):
    """Bildirim arka plan iş parçacığında değil, istek içinde: kuyruğa giren push sınanabilsin."""
    from app import feedback

    monkeypatch.setattr(feedback, "notify_async", feedback.notify_admins)


def _send(c, message="Fiş okuturken ekran dondu", shots=(), context=None, headers=None):
    files = [("screenshots", (f"ekran{i}.png", data, "image/png")) for i, data in enumerate(shots)]
    data = {"message": message} | ({"context": context} if context is not None else {})
    return c.post("/api/feedback", data=data, files=files or None, headers=headers or {})


def _uid(name):
    from app import system

    return system.user_by_name(name)["id"]


def test_send_and_admin_flow(owner):  # noqa: F811
    from app import push

    ctx = json.dumps({"route": "#/islemler", "app_version": "1.4.0", "platform": "web", "error": "Sunucu hatası",
                      "code": "E500", "book": "ortak", "fazla": "atılır"})
    r = _send(owner, shots=[PNG, JPG], context=ctx)
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert r.json() == {"ok": True, "id": fid}

    lst = owner.get("/api/admin/feedback").json()
    assert lst["counts"] == {"yeni": 1, "okundu": 0, "cozuldu": 0}
    it = lst["items"][0]
    assert it["message"] == "Fiş okuturken ekran dondu" and it["status"] == "yeni" and it["screenshots"] == 2
    assert it["user"] == {"id": _uid("admin"), "username": "admin"} and it["demo"] is False
    assert it["context"]["route"] == "#/islemler" and it["context"]["code"] == "E500" and "fazla" not in it["context"]

    s0 = owner.get(f"/api/admin/feedback/{fid}/screenshot/0")
    assert s0.status_code == 200 and s0.content == PNG and s0.headers["content-type"] == "image/png"
    s1 = owner.get(f"/api/admin/feedback/{fid}/screenshot/1")
    assert s1.content == JPG and s1.headers["content-type"] == "image/jpeg"
    assert owner.get(f"/api/admin/feedback/{fid}/screenshot/2").status_code == 404
    assert owner.get("/api/admin/feedback/999/screenshot/0").status_code == 404

    p = owner.patch(f"/api/admin/feedback/{fid}", json={"status": "cozuldu", "admin_note": "1.4.1 ile düzeldi"})
    assert p.status_code == 200 and p.json()["status"] == "cozuldu" and p.json()["admin_note"] == "1.4.1 ile düzeldi"
    assert owner.get("/api/admin/feedback", params={"status": "yeni"}).json()["items"] == []
    assert [x["id"] for x in owner.get("/api/admin/feedback", params={"status": "cozuldu"}).json()["items"]] == [fid]
    assert owner.patch(f"/api/admin/feedback/{fid}", json={"status": "bilinmiyor"}).status_code == 422
    assert owner.patch("/api/admin/feedback/999", json={"status": "okundu"}).status_code == 404

    # yöneticiye push kuyruğa girdi (tür geri_bildirim, varsayılan açık)
    q = [x["payload"] for x in push.queue(_uid("admin")) if x["payload"]["kind"] == "geri_bildirim"]
    assert len(q) == 1 and q[0]["id"] == f"geri-bildirim:{fid}" and "admin" in q[0]["title"]
    assert q[0]["body"] == "Fiş okuturken ekran dondu" and q[0]["route"] == "#/diger/ayarlar"
    assert owner.get("/api/push/prefs").json()["labels"]["geri_bildirim"] == "Kullanıcı geri bildirimi"


@pytest.mark.parametrize("message,status", [("", 422), ("   ", 422), ("x" * 2001, 422), ("x" * 2000, 200)])
def test_message_length(owner, message, status):  # noqa: F811
    assert _send(owner, message=message).status_code == status


def test_screenshot_limits(owner, monkeypatch):  # noqa: F811
    from app import feedback

    assert _send(owner, shots=[PNG, JPG, WEBP]).status_code == 200
    r = _send(owner, shots=[PNG, PNG, PNG, PNG])
    assert r.status_code == 422 and "3" in r.json()["detail"]
    # tür baytlardan: adı .png olan PDF geçmez
    r = _send(owner, shots=[b"%PDF-1.4 sahte"])
    assert r.status_code == 422 and "resim değil" in r.json()["detail"]
    monkeypatch.setattr(feedback, "MAX_SHOT_BYTES", 100)
    r = _send(owner, shots=[PNG + b"\x00" * 200])
    assert r.status_code == 413 and "çok büyük" in r.json()["detail"]


def test_context_validation(owner):  # noqa: F811
    assert _send(owner, context="{bozuk").status_code == 422
    assert _send(owner, context="[1, 2]").status_code == 422
    assert _send(owner, context=json.dumps({"route": "x" * 5000})).status_code == 422  # 4 KB üstü
    assert _send(owner, context="").status_code == 200


def test_rate_limit_per_user(owner):  # noqa: F811
    for i in range(10):
        assert _send(owner, message=f"bildirim {i}").status_code == 200
    r = _send(owner, message="on birinci")
    assert r.status_code == 429 and r.headers["retry-after"]
    assert owner.get("/api/admin/feedback").json()["counts"]["yeni"] == 10


def test_anonymous_from_login_screen(owner):  # noqa: F811
    from app.api.main import app

    anon = TestClient(app)
    assert anon.get("/api/transactions").status_code == 401  # giriş duvarı yerinde
    r = _send(anon, message="Giriş yapamıyorum")
    assert r.status_code == 200
    it = owner.get("/api/admin/feedback").json()["items"][0]
    assert it["user"] is None and it["message"] == "Giriş yapamıyorum"
    for i in range(9):
        assert _send(anon, message=f"tekrar {i}").status_code == 200
    assert _send(anon).status_code == 429  # oturumsuzda bağlantı başı


@pytest.mark.parametrize("role", [None, "viewer", "full"])
def test_member_any_role_can_send(owner, role):  # noqa: F811
    m = _member(owner, "esim", role)
    r = _send(m, message="Ortak defterde kart görünmüyor", context=json.dumps({"book": "ortak"}))
    assert r.status_code == 200, r.text
    it = owner.get("/api/admin/feedback").json()["items"][0]
    assert it["user"]["username"] == "esim"
    # üye yönetici uçlarına giremez
    assert m.get("/api/admin/feedback").status_code == 403


def test_non_admin_forbidden(owner, monkeypatch):  # noqa: F811
    from app.api.main import app

    inv = owner.post("/api/admin/invites", json={}).json()
    c = TestClient(app)
    assert c.post("/api/auth/register", json={"code": inv["code"], "username": "ayse", "password": "ayse-sifre-12345"}).status_code == 200
    fid = _send(c, shots=[PNG]).json()["id"]
    assert c.get("/api/admin/feedback").status_code == 403
    assert c.get(f"/api/admin/feedback/{fid}/screenshot/0").status_code == 403
    assert c.patch(f"/api/admin/feedback/{fid}", json={"status": "okundu"}).status_code == 403
    # push kendi tercih listesinde yönetici türünü görmez
    assert "geri_bildirim" not in c.get("/api/push/prefs").json()["labels"]
    # hesap silinince geri bildirimi de gider
    from app import feedback, system

    system.delete_user(_uid("ayse"))
    assert feedback.get(fid) is None
    assert owner.get(f"/api/admin/feedback/{fid}/screenshot/0").status_code == 404


def test_demo_allowed(tmp_path, monkeypatch, tmpdb):
    from app import feedback

    monkeypatch.setenv("HESAPKITAP_DEMO", "1")
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    from app.api.main import app

    demo = TestClient(app)
    assert demo.get("/api/settings").status_code == 403  # demo kapısı yerinde
    r = _send(demo, message="Demoda grafik boş", shots=[PNG])
    assert r.status_code == 200, r.text
    it = feedback.get(r.json()["id"])
    assert it["demo"] is True and it["user"] is None and it["screenshots"] == 1


def test_notify_failure_does_not_break(owner, monkeypatch):  # noqa: F811
    from app import push

    def boom(*a, **k):
        raise RuntimeError("push düştü")

    monkeypatch.setattr(push, "notify", boom)
    assert _send(owner).status_code == 200


def test_telegram_gets_first_screenshot(owner, monkeypatch):  # noqa: F811
    from app import telegram

    sent = []
    monkeypatch.setattr(telegram, "enabled", lambda: True)
    monkeypatch.setattr(telegram, "send_photo", lambda name, data, caption="": sent.append(("photo", name, data, caption)))
    monkeypatch.setattr(telegram, "send", lambda text: sent.append(("text", text)))
    fid = _send(owner, message="Rapor <b>bozuk</b>", shots=[JPG, PNG]).json()["id"]
    assert sent[0][0] == "photo" and sent[0][2] == JPG and sent[0][1] == f"geri-bildirim-{fid}.jpg"
    assert "&lt;b&gt;bozuk" in sent[0][3]
    _send(owner, message="Görüntüsüz")
    assert sent[1][0] == "text" and "Görüntüsüz" in sent[1][1]


def test_capability():
    from app import version

    assert "feedback" in version.CAPABILITIES
