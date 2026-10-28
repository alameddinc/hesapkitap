"""Mobil uygulama (Capacitor): çerezsiz oturum anahtarı, CORS, demo kopyası başlıkla."""
from fastapi.testclient import TestClient

APP = "capacitor://localhost"
MOBILE = {"X-HK-Client": "mobile", "Origin": APP}


def _admin(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")


def test_mobile_login_returns_token_and_bearer_works(client, monkeypatch):
    from app.api.main import app

    _admin(client, monkeypatch)
    m = TestClient(app)
    r = m.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}, headers=MOBILE)
    assert r.status_code == 200 and "set-cookie" not in r.headers  # çerez yok, anahtar başlıkta
    token = r.headers["x-session-token"]
    assert r.headers["access-control-allow-origin"] == APP
    assert "X-Session-Token" in r.headers["access-control-expose-headers"]
    fresh = TestClient(app)  # çerez kavanozu boş: sadece başlıkla
    assert fresh.get("/api/summary").status_code == 401
    ok = fresh.get("/api/meta", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    st = fresh.get("/api/auth/status", headers={"Authorization": f"Bearer {token}"}).json()
    assert st["authenticated"] is True
    # yanlış anahtar
    assert fresh.get("/api/summary", headers={"Authorization": "Bearer x.y.z.w.v"}).status_code == 401


def test_cors_preflight_and_401_carry_headers(client, monkeypatch):
    from app.api.main import app

    _admin(client, monkeypatch)
    c = TestClient(app)
    pre = c.options("/api/summary", headers={"Origin": APP, "Access-Control-Request-Method": "GET",
                                             "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == APP
    r = c.get("/api/summary", headers={"Origin": APP})
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == APP  # uygulama 401'i görebilsin
    other = c.get("/api/summary", headers={"Origin": "https://kotu.example"})
    assert "access-control-allow-origin" not in other.headers


def test_demo_sandbox_via_header(tmp_path, monkeypatch, tmpdb):
    monkeypatch.setenv("HESAPKITAP_DEMO", "1")
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    from app.api.main import app

    a = TestClient(app)
    r = a.get("/api/summary", headers={"Origin": APP})
    sid = r.headers["x-demo-sid"]
    a.post("/api/entries", headers={"X-Demo-Sid": sid}, json={"entry_date": "2026-10-02", "description": "Mobil test",
                                                              "amount": 77, "kind": "gider", "category": "Diğer",
                                                              "payment": "nakit"})
    b = TestClient(app)  # ayrı istemci, aynı kopya
    rows = b.get("/api/transactions", headers={"X-Demo-Sid": sid}).json()
    assert any(x["description"] == "Mobil test" for x in rows)
    assert b.get("/api/summary").headers["x-demo-sid"] != sid  # başlıksız: yeni kopya


def test_summary_without_month_is_current_period(client):
    from app import finance

    assert client.get("/api/summary").json()["month"] == finance.current_period()
