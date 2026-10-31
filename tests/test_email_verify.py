"""Hesabın önceden ele geçirilmesi (denetim M8, Ek B Y2): hazır sürümde şifreli kayıt e-posta koduyla doğrulanır;
doğrulanmamış hesaba Google/Apple girişi gelince e-postanın sahibi hesabı alır. E-postalar uydurmadır."""
import re

import pytest
from fastapi.testclient import TestClient

CONSENT = ["kosullar", "acik_riza"]


@pytest.fixture()
def hosted(client, monkeypatch):
    from app import auth, mailer, social

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("HESAPKITAP_OPEN_SIGNUP", "1")
    monkeypatch.setenv("HESAPKITAP_GOOGLE_CLIENT_IDS", "test")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("MAIL_FROM", "HesapKitap <noreply@example.invalid>")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append((to, subject, text)))
    monkeypatch.setattr(social, "verify_google", lambda t, keys_for=None: {"email": "kurban@example.com", "name": "K"})
    auth._fails.clear()
    _signups_clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    return sent


def _signups_clear():
    from app.api import main

    main._signups.clear()  # IP başı saatlik kayıt sınırı süreç geneli: testler birbirini sınırlamasın


def _app():
    from app.api.main import app

    return TestClient(app)


def _code(sent, to):
    return re.search(r"\b(\d{6})\b", next(t for a, s, t in reversed(sent) if a == to)).group(1)


def _register(c, email, pw):
    return c.post("/api/auth/register", json={"username": email, "password": pw, "consent": CONSENT})


def test_signup_requires_code_then_session(hosted):
    c = _app()
    r = _register(c, "ayse@example.com", "ayse-sifre-123")
    assert r.status_code == 200 and r.json() == {"ok": True, "verify_required": True, "email": "ayse@example.com"}
    assert c.get("/api/transactions").status_code == 401  # doğrulanmadan oturum yok, veri yazamaz/okuyamaz
    r = c.post("/api/auth/login", json={"username": "ayse@example.com", "password": "ayse-sifre-123"})
    assert r.status_code == 403 and r.json()["detail"] == "email_unverified"
    assert c.post("/api/auth/verify-email", json={"email": "ayse@example.com", "code": "000000"}).status_code == 400
    assert c.post("/api/auth/verify-email", json={"email": "ayse@example.com", "code": _code(hosted, "ayse@example.com")}).status_code == 200
    assert c.get("/api/transactions").status_code == 200
    assert c.get("/api/auth/status").json()["user"]["email_verified"] is True
    assert _app().post("/api/auth/login", json={"username": "ayse@example.com", "password": "ayse-sifre-123"}).status_code == 200


def test_prehijack_reversed(hosted):
    """PoC: saldırgan kurbanın e-postasıyla şifreli hesap açar; kurban Google ile girer. Önce saldırgan kurbanın
    kayıtlarını görüyordu; şimdi saldırganın şifresi geçersiz, oturumu yok."""
    attacker = _app()
    assert _register(attacker, "kurban@example.com", "saldirgan-sifre-1").status_code == 200
    victim = _app()
    g = victim.post("/api/auth/google", json={"id_token": "x", "consent": CONSENT})
    assert g.status_code == 200 and g.json()["created"] is False
    victim.post("/api/entries", json={"entry_date": "2026-10-05", "description": "Maas EFT", "amount": 85000,
                                      "kind": "gelir", "category": "Maaş", "payment": "havale"})
    assert attacker.get("/api/transactions").status_code == 401
    r = _app().post("/api/auth/login", json={"username": "kurban@example.com", "password": "saldirgan-sifre-1"})
    assert r.status_code == 401
    assert any("Google/Apple girişine bağlandı" in s for a, s, t in hosted if a == "kurban@example.com")
    assert victim.get("/api/auth/status").json()["user"]["password_set"] is False


def test_attacker_code_cannot_be_used_after_owner_reregisters(hosted):
    """Doğrulanmamış hesaba yeniden kayıt: yeni şifre, yeni kod; eski şifre ve eski kod geçersiz."""
    _register(_app(), "kurban@example.com", "saldirgan-sifre-1")
    old_code = _code(hosted, "kurban@example.com")
    import time as _t

    from app import email_verify, system

    with system.connect() as c:  # dakikada bir gönderim sınırını test için aş
        c.execute("UPDATE email_codes SET sent_at = ?", (int(_t.time()) - 120,))
    _register(_app(), "kurban@example.com", "sahibin-sifresi-1")
    new_code = _code(hosted, "kurban@example.com")
    c = _app()
    if new_code != old_code:  # (rastgele kodlar 1e-6 olasılıkla aynı çıkabilir)
        assert c.post("/api/auth/verify-email", json={"email": "kurban@example.com", "code": old_code}).status_code == 400
    assert c.post("/api/auth/verify-email", json={"email": "kurban@example.com", "code": new_code}).status_code == 200
    assert _app().post("/api/auth/login", json={"username": "kurban@example.com", "password": "saldirgan-sifre-1"}).status_code == 401
    assert not email_verify.unverified(system.user_by_name("kurban@example.com")["id"])


def test_register_does_not_reveal_existing_email(hosted):
    """PoC [enum]: önce 422 'Bu kullanıcı adı alınmış'; şimdi yanıt yeni kayıtla aynı, sahibine mail gider."""
    from app import email_verify, system

    c = _app()
    _register(c, "ayse@example.com", "ayse-sifre-123")
    c.post("/api/auth/verify-email", json={"email": "ayse@example.com", "code": _code(hosted, "ayse@example.com")})
    r1 = _register(_app(), "ayse@example.com", "baska-sifre-12")
    r2 = _register(_app(), "yeni@example.com", "baska-sifre-12")
    assert r1.status_code == r2.status_code == 200
    assert r1.json() | {"email": ""} == r2.json() | {"email": ""}
    assert "alınmış" not in r1.text
    assert any("zaten bir hesabın var" in s for a, s, t in hosted if a == "ayse@example.com")
    # mevcut hesabın şifresi değişmedi
    assert _app().post("/api/auth/login", json={"username": "ayse@example.com", "password": "ayse-sifre-123"}).status_code == 200
    assert not email_verify.unverified(system.user_by_name("ayse@example.com")["id"])


def test_password_reset_verifies_email(hosted):
    from app import email_verify, system

    _register(_app(), "ayse@example.com", "ayse-sifre-123")
    uid = system.user_by_name("ayse@example.com")["id"]
    assert email_verify.unverified(uid)
    c = _app()
    c.post("/api/auth/forgot", json={"email": "ayse@example.com"})
    code = re.search(r"\b(\d{6})\b", next(t for a, s, t in reversed(hosted) if "sıfırlama" in s)).group(1)
    assert c.post("/api/auth/reset", json={"email": "ayse@example.com", "code": code, "password": "yeni-sifre-1234"}).status_code == 200
    assert not email_verify.unverified(uid)


def test_without_mail_old_flow(client, monkeypatch):
    """Posta ayarlı değilse (kendi sunucusu) doğrulama istenmez: eski akış (hemen oturum)."""
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("HESAPKITAP_OPEN_SIGNUP", "1")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    auth._fails.clear()
    _signups_clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    c = _app()
    assert _register(c, "ayse@example.com", "ayse-sifre-123").json() == {"ok": True}
    assert c.get("/api/transactions").status_code == 200


def test_login_unknown_user_does_dummy_hash(monkeypatch, tmpdb):
    from app import auth

    calls = []
    real = auth._verify
    monkeypatch.setattr(auth, "_verify", lambda pw, h: (calls.append(1), real(pw, h))[1])
    assert auth.login("yok@example.com", "bir-sifre-123") is None
    assert calls  # var olmayan hesapta da PBKDF2 çalışır (zamanlama farkı yok)


# --- HESAPKITAP_EMAIL_VERIFY=0: kod adımı yok, diğer korumalar sürer -------------------------------------------------

@pytest.fixture()
def no_code(hosted, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_EMAIL_VERIFY", "0")
    return hosted


def test_switch_off_signup_opens_session(no_code):
    from app import email_verify, system

    c = _app()
    r = _register(c, "ayse@example.com", "ayse-sifre-123")
    assert r.json() == {"ok": True} and c.get("/api/transactions").status_code == 200  # eski mobil sürüm takılmaz
    assert email_verify.unverified(system.user_by_name("ayse@example.com")["id"])  # ama işaretli
    assert _app().post("/api/auth/login", json={"username": "ayse@example.com", "password": "ayse-sifre-123"}).status_code == 200
    assert not any("doğrulama kodun" in s for a, s, t in no_code)


def test_switch_off_prehijack_still_reversed(no_code):
    attacker = _app()
    assert _register(attacker, "kurban@example.com", "saldirgan-sifre-1").json() == {"ok": True}
    assert attacker.get("/api/transactions").status_code == 200  # kod adımı kapalı: oturum var
    victim = _app()
    assert victim.post("/api/auth/google", json={"id_token": "x", "consent": CONSENT}).json()["created"] is False
    assert attacker.get("/api/transactions").status_code == 401  # Google girişi saldırganın oturumunu düşürdü
    assert _app().post("/api/auth/login", json={"username": "kurban@example.com", "password": "saldirgan-sifre-1"}).status_code == 401


def test_switch_off_register_still_neutral(no_code):
    c = _app()
    _register(c, "ayse@example.com", "ayse-sifre-123")
    r = _register(_app(), "ayse@example.com", "baska-sifre-12")
    assert r.status_code == 200 and r.json() == {"ok": True} and "alınmış" not in r.text
    assert _app().post("/api/auth/login", json={"username": "ayse@example.com", "password": "ayse-sifre-123"}).status_code == 200
    assert _app().post("/api/auth/login", json={"username": "ayse@example.com", "password": "baska-sifre-12"}).status_code == 401


def test_switch_default_on(hosted, monkeypatch):
    from app import email_verify

    monkeypatch.delenv("HESAPKITAP_EMAIL_VERIFY", raising=False)
    assert email_verify.required()
    monkeypatch.setenv("HESAPKITAP_EMAIL_VERIFY", "1")
    assert email_verify.required()
    monkeypatch.setenv("HESAPKITAP_EMAIL_VERIFY", "0")
    assert not email_verify.required() and email_verify.protected()


@pytest.mark.parametrize("switch", ["0", "1"])
def test_bad_email_is_422_in_both_modes(hosted, monkeypatch, switch):
    monkeypatch.setenv("HESAPKITAP_EMAIL_VERIFY", switch)
    r = _register(_app(), "ayse@", "ayse-sifre-123")
    assert r.status_code == 422 and "e-posta" in r.json()["detail"]
