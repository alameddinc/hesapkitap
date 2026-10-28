"""Google ile giriş: imzalı kimlik belgesi doğrulanır; sahte imza, yanlış uygulama, süresi dolmuş belge reddedilir."""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

WEB = "web-123.apps.googleusercontent.com"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@pytest.fixture()
def google(client, monkeypatch):
    from app import auth, social
    from app.api import main

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = key.public_key().public_numbers()
    jwk = {"kid": "k1", "kty": "RSA", "n": _b64(n.n.to_bytes(256, "big")), "e": _b64(n.e.to_bytes(3, "big"))}
    monkeypatch.setattr(social, "_google_keys", lambda force=False: {"k1": jwk})
    monkeypatch.setenv("HESAPKITAP_GOOGLE_CLIENT_IDS", f"{WEB},ios-456.apps.googleusercontent.com")
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("HESAPKITAP_OPEN_SIGNUP", "1")
    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")

    def token(signer=key, **claims):
        now = int(time.time())
        body = {"iss": "https://accounts.google.com", "aud": WEB, "email": "ayse@gmail.com", "email_verified": True,
                "name": "Ayşe", "iat": now, "exp": now + 3600, **claims}
        head = _b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
        payload = _b64(json.dumps(body).encode())
        sig = signer.sign(f"{head}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{payload}.{_b64(sig)}"

    return token


def test_google_creates_then_logs_in(google):
    from app import system
    from app.api.main import app

    c = TestClient(app)
    assert c.get("/api/auth/status").json()["google"] is True
    r = c.post("/api/auth/google", json={"id_token": google()})
    assert r.status_code == 200 and r.json()["created"] is True
    assert c.get("/api/auth/status").json()["user"]["username"] == "ayse@gmail.com"
    # aynı e-posta: yeni hesap açılmaz, aynı hesaba girer
    r = TestClient(app).post("/api/auth/google", json={"id_token": google(email="Ayse@Gmail.com")})
    assert r.json()["created"] is False
    assert sum(u["username"] == "ayse@gmail.com" for u in system.users()) == 1
    # Google'la açılan hesabın boş şifresi yok: boş şifreyle girilemez
    assert TestClient(app).post("/api/auth/login", json={"username": "ayse@gmail.com", "password": ""}).status_code == 401


def test_google_rejects_bad_tokens(google):
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

    from app.api.main import app

    other = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad = {
        "sahte imza": google(signer=other),
        "başka uygulama": google(aud="baska.apps.googleusercontent.com"),
        "süresi dolmuş": google(exp=int(time.time()) - 3600),
        "doğrulanmamış e-posta": google(email_verified=False),
        "başka yayıncı": google(iss="https://kotu.example"),
    }
    for why, t in bad.items():
        r = TestClient(app).post("/api/auth/google", json={"id_token": t})
        assert r.status_code == 401, why


def test_google_existing_account_links_and_off_when_not_configured(google, monkeypatch):
    from app import auth
    from app.api.main import app

    auth.create_user("mehmet@gmail.com", "mehmet-sifre-12345")
    r = TestClient(app).post("/api/auth/google", json={"id_token": google(email="mehmet@gmail.com")})
    assert r.status_code == 200 and r.json()["created"] is False
    monkeypatch.delenv("HESAPKITAP_GOOGLE_CLIENT_IDS")
    assert TestClient(app).post("/api/auth/google", json={"id_token": google()}).status_code == 401
