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


def test_daily_code_cap_and_wrong_lock(mail, monkeypatch):
    """S7/O3: hesap başı günde en çok 5 kod postası; günde 15 yanlıştan sonra 24 saat kilit (doğru kod da geçmez)."""
    from app import reset

    monkeypatch.setattr(reset, "RESEND_GAP", 0)
    for _ in range(6):
        reset.request("ayse@ornek.com")
    assert len([m for m in mail if "kodun" in m[1]]) == 5  # 6. istek posta göndermez (yanıt yine nötr)
    code = _code(mail)
    for i in range(15):  # kod başı 5 yanlış hakkı dolsa da günlük sayaç sürer
        assert reset.verify("ayse@ornek.com", f"{(int(code) + 1 + i) % 1_000_000:06d}") is None
    with reset.system.connect() as c:  # yeni kod gelse bile (kilit varken gelmez) 16. deneme kilitli
        c.execute("UPDATE auth_limits SET n = 0 WHERE kind = 'reset_code'")
    reset.request("ayse@ornek.com")
    assert _code(mail) == code  # kilitliyken yeni kod postası yok
    assert reset.verify("ayse@ornek.com", code) is None


def test_reset_success_and_admin_reset_notify(mail):
    from app import system
    from app.api.main import app

    c = TestClient(app)
    c.post("/api/auth/forgot", json={"email": "ayse@ornek.com"})
    code = _code(mail)
    assert c.post("/api/auth/reset", json={"email": "ayse@ornek.com", "code": code, "password": "yeni-sifre-12345"}).status_code == 200
    assert mail[-1][0] == "ayse@ornek.com" and "şifren değişti" in mail[-1][1] and code not in mail[-1][2]
    adm = TestClient(app)
    assert adm.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    uid = system.user_by_name("ayse@ornek.com")["id"]
    temp = adm.post(f"/api/admin/users/{uid}/reset-password").json()["temp_password"]
    assert "yönetici" in mail[-1][2].lower() and temp not in mail[-1][2]  # geçici şifre postada yok


def test_admin_needs_username_when_others_exist(mail, monkeypatch):
    """Y4: başka hesap varken (ya da hazır sürümde) boş kullanıcı adı yöneticiyi açmaz: 401."""
    from app.api.main import app

    c = TestClient(app)
    assert c.post("/api/auth/login", json={"password": "yonetici-sifre-1"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200


def test_admin_account_lock_stricter(mail, monkeypatch):
    """Yönetici kilidi 1 saat sürer (diğer hesaplar 15 dk): 20 dk sonra kullanıcı açılır, yönetici açılmaz."""
    import time as _time

    from app import auth

    now = _time.time()
    for key in (auth.account_key("admin"), auth.account_key("ayse@ornek.com")):
        for _ in range(auth.ACCOUNT_MAX_FAILS):
            auth.record_account_fail(key)
    monkeypatch.setattr(auth.time, "time", lambda: now + 20 * 60)
    assert auth.account_locked(auth.account_key("admin"))
    assert not auth.account_locked(auth.account_key("ayse@ornek.com"))
    auth._account_fails.clear()
