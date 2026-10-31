"""S18: kimliksiz CPU/maliyet sınırları (IP başı deneme, PBKDF2 eşzamanlılığı, günlük kayıt, günlük yapay zekâ)."""
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_auth(client, monkeypatch):
    from app import auth
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth.create_admin("admin", "yonetici-sifre-1")
    return app


def test_ip_attempts_limited_even_when_correct(app_auth, monkeypatch):
    """Doğru şifreyle de olsa IP başı deneme sınırı: PBKDF2 sınırsız çalıştırılamaz."""
    monkeypatch.setenv("HESAPKITAP_AUTH_ATTEMPTS", "3")
    c = TestClient(app_auth)
    for _ in range(3):
        assert c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    assert c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 429
    assert c.post("/api/auth/register", json={"code": "x", "username": "a", "password": "b" * 12,
                                               "consent": ["kvkk", "terms"]}).status_code == 429


def test_kdf_busy_returns_503(app_auth, monkeypatch):
    from app import auth

    monkeypatch.setattr(auth, "_KDF", threading.BoundedSemaphore(1))
    monkeypatch.setattr(auth, "KDF_WAIT", 0.01)
    auth._KDF.acquire()  # yuva dolu: başka bir hesap sürüyormuş gibi
    try:
        r = TestClient(app_auth).post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"})
        assert r.status_code == 503 and "yoğun" in r.json()["detail"]
    finally:
        auth._KDF.release()


def test_hosted_daily_signup_cap(client, monkeypatch):
    from app import auth
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("HESAPKITAP_OPEN_SIGNUP", "1")
    monkeypatch.setenv("HESAPKITAP_SIGNUP_DAILY", "2")
    auth.create_admin("admin@ornek.com", "yonetici-sifre-1")
    auth.create_user("bir@ornek.com", "sifre-uzun-12345")  # bugün 2 hesap: sınır doldu
    r = TestClient(app).post("/api/auth/register", json={"username": "iki@ornek.com", "password": "sifre-uzun-12345",
                                                         "consent": ["kosullar", "acik_riza"]})
    assert r.status_code == 429 and "yarın" in r.json()["detail"]
    monkeypatch.setenv("HESAPKITAP_SIGNUP_DAILY", "0")  # 0: sınırsız
    r = TestClient(app).post("/api/auth/register", json={"username": "uc@ornek.com", "password": "sifre-uzun-12345",
                                                         "consent": ["kosullar", "acik_riza"]})
    assert r.status_code != 429


def test_ai_daily_cap(tmpdb, monkeypatch):
    from app import receipt, system

    monkeypatch.setenv("HESAPKITAP_AI_DAILY", "2")
    monkeypatch.setattr(receipt, "_daily_user", lambda: {"id": 7, "role": "user"})
    receipt._daily_gate()
    receipt._daily_gate()
    with pytest.raises(receipt.DailyLimitError):
        receipt._daily_gate()
    monkeypatch.setattr(receipt, "_daily_user", lambda: {"id": 8, "role": "user"})
    receipt._daily_gate()  # başka kullanıcı etkilenmez
    monkeypatch.setenv("HESAPKITAP_AI_DAILY", "0")
    for _ in range(5):
        receipt._daily_gate()  # 0: sınırsız
    assert system  # noqa: B015
