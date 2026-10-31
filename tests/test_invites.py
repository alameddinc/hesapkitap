"""Davet (denetim M12): bağlantı ve derin bağlantı, ön kontrol (defter adı, yetki), tek kullanım, süre, iptal/yenile,
davet bağlantısıyla Google girişi. Kullanıcılar uydurmadır."""
import pytest
from fastapi.testclient import TestClient

from tests.test_book import owner  # noqa: F401  (fixture)


def _new():
    from app.api.main import app

    return TestClient(app)


def _invite(owner, role="viewer"):
    r = owner.post("/api/book/invites", json={"note": "Eşim", "book_role": role})
    assert r.status_code == 200
    return r.json()


def test_invite_has_links(owner, monkeypatch):
    inv = _invite(owner)
    assert inv["link"] == f"http://testserver/#/kayit?kod={inv['code']}"
    assert inv["app_link"] == f"hesapkitap://kayit?kod={inv['code']}" and inv["kind"] == "book"
    assert inv["role_label"] == "Sadece görür"
    monkeypatch.setenv("HESAPKITAP_PUBLIC_URL", "https://para.ornek.com/")
    monkeypatch.setenv("HESAPKITAP_APP_SCHEME", "hkdeneme")
    inv2 = owner.get("/api/book").json()["invites"][0]
    assert inv2["link"].startswith("https://para.ornek.com/#/kayit?kod=") and inv2["app_link"].startswith("hkdeneme://")


def test_invite_check_shows_book_and_role(owner):
    inv = _invite(owner, "editor")
    r = _new().get(f"/api/auth/invite/{inv['code'].lower()}").json()
    assert r == {"valid": True, "kind": "book", "expires_at": inv["expires_at"], "book": "admin", "role": "editor",
                 "role_label": "Görür ve ekler"}


def test_invalid_codes_turkish(owner):
    from app import auth

    r = _new().get("/api/auth/invite/YANL-IS00-KOD0").json()
    assert r["valid"] is False and r["reason"] == "unknown" and "bulunamadı" in r["message"]
    inv = _invite(owner)
    with __import__("app.system", fromlist=["connect"]).connect() as c:
        c.execute("UPDATE invites SET expires_at = date('now', '-1 day') WHERE code = ?", (inv["code"],))
    r = _new().get(f"/api/auth/invite/{inv['code']}").json()
    assert r["reason"] == "expired" and "süresi dolmuş" in r["message"]
    auth._fails.clear()


def test_register_with_invite_becomes_member_and_single_use(owner):
    inv = _invite(owner, "editor")
    m = _new()
    r = m.post("/api/auth/register", json={"code": inv["code"], "username": "esim", "password": "esim-sifre-12345"})
    assert r.status_code == 200 and m.get("/api/book/me").json()["member"] is True
    r = _new().get(f"/api/auth/invite/{inv['code']}").json()
    assert r["valid"] is False and r["reason"] == "used" and "kullanılmış" in r["message"]
    assert _new().post("/api/auth/register", json={"code": inv["code"], "username": "baskasi",
                                                   "password": "baska-sifre-1234"}).status_code == 403


def test_register_with_invite_when_signup_closed(owner, monkeypatch):
    """Kayıt kapalı kendi sunucusu: davetli kayıt yine başarılı."""
    monkeypatch.delenv("HESAPKITAP_OPEN_SIGNUP", raising=False)
    inv = _invite(owner)
    assert _new().post("/api/auth/register", json={"username": "x", "password": "yok-sifre-1234"}).status_code == 403
    assert _new().post("/api/auth/register", json={"code": inv["code"], "username": "cocuk",
                                                   "password": "cocuk-sifre-1234"}).status_code == 200


def test_revoke_and_renew(owner):
    inv = _invite(owner)
    new = owner.post(f"/api/book/invites/{inv['code']}/renew").json()
    assert new["code"] != inv["code"] and new["book_role"] == "viewer" and new["note"] == "Eşim"
    assert _new().get(f"/api/auth/invite/{inv['code']}").json()["valid"] is False  # eski kod geçersiz
    assert _new().get(f"/api/auth/invite/{new['code']}").json()["valid"] is True
    assert owner.delete(f"/api/book/invites/{new['code']}").status_code == 200  # iptal
    assert _new().get(f"/api/auth/invite/{new['code']}").json()["valid"] is False
    assert owner.post(f"/api/book/invites/{new['code']}/renew").status_code == 404


def test_google_login_with_invite_joins_book(owner, monkeypatch):
    from app import social

    monkeypatch.setenv("HESAPKITAP_GOOGLE_CLIENT_IDS", "test")
    monkeypatch.setattr(social, "verify_google", lambda t, keys_for=None: {"email": "esim@example.com", "name": "E"})
    inv = _invite(owner, "editor")
    m = _new()
    r = m.post("/api/auth/google", json={"id_token": "x", "invite": inv["code"], "consent": ["kosullar", "acik_riza"]})
    assert r.status_code == 200 and r.json()["created"] is True
    assert m.get("/api/book/me").json()["member"] is True
    bad = _new().post("/api/auth/google", json={"id_token": "x", "invite": "YANL-IS00-KOD0"})
    assert bad.status_code == 200  # hesap artık var: davet yok sayılır, girer
