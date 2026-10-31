"""CORS: mobil uygulamanın (capacitor://localhost) gördüğü HER yanıtta Access-Control-Allow-Origin olmalı; erken
dönüşler (oturum, yetki, sıra, sınır, idempotency, hata) dahil. Başlıksız yanıtı tarayıcı CORS hatası sayar."""
import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "capacitor://localhost"}


def _ok(r):
    assert r.headers.get("access-control-allow-origin") == "capacitor://localhost", (r.status_code, dict(r.headers))
    return r


@pytest.fixture()
def api(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    from app.api.main import app

    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"},
               headers={**ORIGIN, "x-hk-client": "mobile"})
    return c, {**ORIGIN, "authorization": f"Bearer {r.headers['X-Session-Token']}"}


def test_auth_and_routing_errors(api):
    c, h = api
    assert _ok(c.get("/api/summary", headers=ORIGIN)).status_code == 401
    assert _ok(c.get("/api/yok-boyle", headers=h)).status_code == 404
    assert _ok(c.delete("/api/summary", headers=h)).status_code == 405
    assert _ok(c.post("/api/entries", json={"amount": -1}, headers=h)).status_code == 422
    assert _ok(c.get("/api/settings/backup/olmayan.zip", headers=h)).status_code == 404


def test_rate_limit_429(api, monkeypatch):
    from app import auth

    c, h = api
    monkeypatch.setattr(auth, "rate_limited", lambda ip: True)
    assert _ok(c.post("/api/auth/login", json={"username": "admin", "password": "x"}, headers=ORIGIN)).status_code == 429


def test_heavy_slot_503_and_limit_413(api, monkeypatch):
    from app.api import main

    c, h = api
    monkeypatch.setenv("HESAPKITAP_PARSE_WAIT", "0.05")
    held = 0
    while main._HEAVY.acquire(blocking=False):
        held += 1
    try:
        r = c.post("/api/documents/parse", files=[("file", ("f.jpg", b"x" * 100, "image/jpeg"))], headers=h)
        assert _ok(r).status_code == 503
    finally:
        for _ in range(held):
            main._HEAVY.release()
    files = [("files", (f"e{i}.pdf", b"x", "application/pdf")) for i in range(31)]
    assert _ok(c.post("/api/statements/upload", files=files, headers=h)).status_code == 413


def test_idempotency_409_and_replay(api, monkeypatch):
    from app import idempotency

    c, h = api
    monkeypatch.setattr(idempotency, "WAIT", 0.1)
    body = {"entry_date": "2026-10-05", "description": "Kahve", "amount": 85, "kind": "gider", "category": "Diğer",
            "payment": "nakit"}
    fp = idempotency.fingerprint("POST", "/api/entries", "", c.build_request("POST", "/api/entries", json=body).read())
    from app import system

    uid = system.user_by_name("admin")["id"]
    idempotency.begin(uid, "cors-anahtar-01", fp)
    assert _ok(c.post("/api/entries", json=body, headers={**h, "Idempotency-Key": "cors-anahtar-01"})).status_code == 409
    r1 = _ok(c.post("/api/entries", json=body, headers={**h, "Idempotency-Key": "cors-anahtar-02"}))
    r2 = _ok(c.post("/api/entries", json=body, headers={**h, "Idempotency-Key": "cors-anahtar-02"}))
    assert r2.headers["Idempotent-Replayed"] == "true" and r1.content == r2.content
    assert "idempotent-replayed" in r2.headers["access-control-expose-headers"].lower()


def test_unhandled_error_500_has_cors(api, monkeypatch):
    """Önce: işlenmemiş hata CORS katmanının dışında (ServerErrorMiddleware) yanıtlanıyordu, başlık yoktu."""
    from app import finance

    c, h = api
    monkeypatch.setattr(finance, "summary", lambda *a, **k: 1 / 0)
    r = _ok(c.get("/api/summary?month=2026-10", headers=h))
    assert r.status_code == 500 and "Beklenmeyen" in r.json()["detail"]


def test_preflight_with_app_headers(api):
    c, h = api
    r = c.options("/api/entries", headers={**ORIGIN, "Access-Control-Request-Method": "POST",
                                           "Access-Control-Request-Headers": "authorization,content-type,x-hk-client,"
                                                                             "x-demo-sid,x-hk-book,idempotency-key"})
    assert _ok(r).status_code == 200


def test_demo_early_returns(tmpdb, monkeypatch):
    from app.api.main import app

    monkeypatch.setenv("HESAPKITAP_DEMO", "1")
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    c = TestClient(app, raise_server_exceptions=False)
    assert _ok(c.get("/api/budgets?month=2026-10", headers=ORIGIN)).status_code == 200
    assert _ok(c.get("/api/compare?month=2026-10&all=true", headers=ORIGIN)).status_code == 200
    assert _ok(c.post("/api/statements/upload", files=[("files", ("a.pdf", b"x", "application/pdf"))],
                      headers=ORIGIN)).status_code == 403  # demoda kapalı
