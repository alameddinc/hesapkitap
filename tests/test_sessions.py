"""Oturumlar (denetim M9, Ek B Y3): çıkış anahtarı sunucuda da kapatır, "tümünden çık", kayan süre, oturum listesi."""
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    return client


def _mobile_login():
    from app.api.main import app

    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"},
               headers={"x-hk-client": "mobile", "user-agent": "HesapKitap/1.0 (iPhone; iOS 18)"})
    assert r.status_code == 200
    return c, {"authorization": f"Bearer {r.headers['X-Session-Token']}"}


def _get(h):
    from app.api.main import app

    return TestClient(app).get("/api/summary", headers=h).status_code


def test_logout_revokes_token(owner):
    """PoC: çıkıştan sonra aynı anahtarla istek 200 dönüyordu; şimdi 401."""
    c, h = _mobile_login()
    assert _get(h) == 200
    assert c.post("/api/auth/logout", headers=h).status_code == 200
    assert _get(h) == 401


def test_web_logout_revokes_cookie(owner):
    from app import auth
    from app.api.main import app

    c = TestClient(app)
    c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"})
    cookie = c.cookies.get(auth.COOKIE)
    c.post("/api/auth/logout")
    assert TestClient(app, cookies={auth.COOKIE: cookie}).get("/api/summary").status_code == 401


def test_list_and_revoke_one(owner):
    a, ha = _mobile_login()
    b, hb = _mobile_login()
    from app.api.main import app

    lst = TestClient(app).get("/api/me/sessions", headers=ha).json()
    assert len(lst) >= 2 and sum(s["current"] for s in lst) == 1
    assert any(s["device"].startswith("HesapKitap uygulaması · iOS") for s in lst)
    other = next(s for s in TestClient(app).get("/api/me/sessions", headers=hb).json() if s["current"])
    assert TestClient(app).delete(f"/api/me/sessions/{other['id']}", headers=ha).status_code == 200
    assert _get(hb) == 401 and _get(ha) == 200
    assert TestClient(app).delete("/api/me/sessions/olmayan-oturum", headers=ha).status_code == 404


def test_revoke_all_kills_other_devices(owner):
    """'Tümünden çık' başka cihazın anahtarını geçersiz kılar; bu cihaz yeni anahtarla devam eder."""
    from app.api.main import app

    a, ha = _mobile_login()
    b, hb = _mobile_login()
    r = TestClient(app).post("/api/me/sessions/revoke-all", json={"keep_current": True},
                             headers={**ha, "x-hk-client": "mobile"})
    assert r.status_code == 200 and r.json()["revoked"] >= 2
    assert _get(hb) == 401 and _get(ha) == 401  # eski anahtarlar düştü
    assert _get({"authorization": f"Bearer {r.headers['X-Session-Token']}"}) == 200  # bu cihaz yenisiyle


def test_mobile_sliding_idle_expiry(owner):
    from app import sessions, system

    a, h = _mobile_login()
    assert _get(h) == 200
    with system.connect() as c:  # 15 gün kullanılmadı (mobil kayan süre 14 gün)
        c.execute("UPDATE sessions SET last_seen = ?", (time.time() - 15 * 86400,))
    assert _get(h) == 401
    a, h = _mobile_login()
    with system.connect() as c:  # 13 gün: hâlâ geçerli ve süre uzar
        c.execute("UPDATE sessions SET last_seen = ? WHERE revoked = 0", (time.time() - 13 * 86400,))
    assert _get(h) == 200
    with system.connect() as c:
        assert c.execute("SELECT MAX(last_seen) FROM sessions WHERE revoked = 0").fetchone()[0] > time.time() - 60
    assert sessions.MOBILE_IDLE_DAYS == 14


def test_legacy_token_listed_and_revocable(owner):
    """Bu tablodan önce verilmiş anahtar: ilk kullanımda listeye girer, çıkış onu da kapatır (yeniden canlanmaz)."""
    from app import auth, system

    a, h = _mobile_login()
    with system.connect() as c:
        c.execute("DELETE FROM sessions")  # tablo öncesi durum
    assert _get(h) == 200
    with system.connect() as c:
        assert c.execute("SELECT kind FROM sessions").fetchone()[0] == "eski"
    from app.api.main import app

    TestClient(app).post("/api/auth/logout", headers=h)
    assert _get(h) == 401
    assert auth.token_nonce(h["authorization"][7:])


def test_member_can_manage_own_sessions(owner):
    from tests.test_book import _member

    assert owner.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    m = _member(owner, "cocuk", "viewer")
    assert m.get("/api/me/sessions").status_code == 200
    assert m.post("/api/me/sessions/revoke-all", json={"keep_current": True}).status_code == 200
