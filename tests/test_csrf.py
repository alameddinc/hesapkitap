"""S9: çerezle gelen yazma isteğinde Origin / Sec-Fetch-Site denetimi; Bearer'lı (mobil) istek etkilenmez."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def web(client, monkeypatch):
    from app import auth
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth.create_admin("admin", "yonetici-sifre-1")
    c = TestClient(app, base_url="https://para.ornek.com")
    assert c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return c


BODY = {"entry_date": "2026-10-05", "description": "Market", "amount": 10, "kind": "gider"}


def test_foreign_origin_post_rejected(web):
    r = web.post("/api/entries", json=BODY, headers={"Origin": "https://kardes.ornek.com", "Sec-Fetch-Site": "same-site"})
    assert r.status_code == 403 and r.json()["code"] == "csrf"
    assert web.post("/api/entries", json=BODY, headers={"Origin": "https://kotu.example"}).status_code == 403
    assert web.post("/api/settings/backup/restore", headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://kotu.example"},
                    files={"file": ("y.db", b"x")}).status_code == 403
    assert web.delete("/api/entries/1", headers={"Referer": "https://kotu.example/sayfa"}).status_code == 403
    assert web.post("/api/entries", json=BODY, headers={"Origin": "null"}).status_code == 403


def test_own_origin_and_fetch_metadata_pass(web):
    assert web.post("/api/entries", json=BODY, headers={"Origin": "https://para.ornek.com",
                                                        "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert web.post("/api/entries", json=BODY, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert web.post("/api/entries", json=BODY).status_code == 200  # başlıksız (eski tarayıcı / araç)
    assert web.get("/api/meta", headers={"Origin": "https://kotu.example"}).status_code == 200  # okuma denetlenmez


def test_bearer_and_app_origins_unaffected(web, monkeypatch):
    from app import auth, system
    from app.api.main import app

    tok = auth.make_token(system.user_by_name("admin"), kind="mobile")
    m = TestClient(app)
    r = m.post("/api/entries", json=BODY, headers={"Authorization": f"Bearer {tok}", "Origin": "capacitor://localhost",
                                                    "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200
    # mobil giriş (henüz Bearer yok): uygulama kökeni izinli
    r = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"},
                             headers={"Origin": "capacitor://localhost", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


def test_extra_origin_from_env(web, monkeypatch):
    from app.api import main

    monkeypatch.setattr(main, "CSRF_ORIGINS", main.CSRF_ORIGINS | {"https://yonetim.ornek.com"})
    assert web.post("/api/entries", json=BODY, headers={"Origin": "https://yonetim.ornek.com",
                                                        "Sec-Fetch-Site": "same-site"}).status_code == 200
