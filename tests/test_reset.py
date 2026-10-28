"""Şifremi unuttum: e-postaya kod, kodla yeni şifre; hesap var mı anlaşılmaz; posta ayarlı değilse kapalı."""
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def mail(client, monkeypatch):
    from app import auth, mailer
    from app.api import main

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("MAIL_FROM", "HesapKitap <noreply@example.com>")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append((to, subject, text)))
    auth._fails.clear()
    main._forgots.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    auth.create_user("ayse@ornek.com", "eski-sifre-12345")
    return sent


def _code(sent):
    return re.search(r"\b(\d{6})\b", sent[-1][1]).group(1)


def test_reset_flow(mail):
    from app.api.main import app

    c = TestClient(app)
    assert c.get("/api/auth/status").json()["reset"] is True
    assert c.post("/api/auth/forgot", json={"email": "Ayse@Ornek.com"}).json() == {"ok": True}
    assert len(mail) == 1 and mail[0][0] == "ayse@ornek.com"
    # olmayan hesap: aynı yanıt, mail yok
    assert c.post("/api/auth/forgot", json={"email": "yok@ornek.com"}).json() == {"ok": True}
    assert len(mail) == 1
    # dakikada bir: hemen tekrar istenirse yeni kod gitmez
    c.post("/api/auth/forgot", json={"email": "ayse@ornek.com"})
    assert len(mail) == 1
    code = _code(mail)
    assert c.post("/api/auth/reset", json={"email": "ayse@ornek.com", "code": code, "password": "kisa"}).status_code == 422
    bad = "000000" if code != "000000" else "111111"
    assert c.post("/api/auth/reset", json={"email": "ayse@ornek.com", "code": bad, "password": "yeni-sifre-12345"}).status_code == 400
    r = c.post("/api/auth/reset", json={"email": "ayse@ornek.com", "code": code, "password": "yeni-sifre-12345"})
    assert r.status_code == 200 and c.get("/api/auth/status").json()["authenticated"]
    # kod tek kullanımlık; yeni şifre çalışır, eskisi çalışmaz
    assert c.post("/api/auth/reset", json={"email": "ayse@ornek.com", "code": code, "password": "baska-sifre-12345"}).status_code == 400
    login = lambda pw: TestClient(app).post("/api/auth/login", json={"username": "ayse@ornek.com", "password": pw}).status_code
    assert login("yeni-sifre-12345") == 200 and login("eski-sifre-12345") == 401


def test_code_dies_after_five_wrong_tries(mail):
    from app import auth, reset

    reset.request("ayse@ornek.com")
    code = _code(mail)
    wrong = [f"{(int(code) + i) % 1_000_000:06d}" for i in range(1, 6)]
    for w in wrong:
        assert reset.verify("ayse@ornek.com", w) is None
    assert reset.verify("ayse@ornek.com", code) is None  # 5 yanlıştan sonra doğrusu da geçmez
    auth._fails.clear()


def test_reset_off_without_smtp(client, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    from app.api.main import app

    c = TestClient(app)
    assert c.post("/api/auth/forgot", json={"email": "a@b.co"}).status_code == 404
