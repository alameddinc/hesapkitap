"""Davetle kayıtta e-posta doğrulama (hazır sürüm): ortak defter ve yönetici davetiyle açılan hesap da koda kadar
oturum açamaz; sahibe "katıldı" bildirimi kod girilince gider. E-postalar ve kodlar uydurmadır."""
import re
import time

import pytest
from fastapi.testclient import TestClient

CONSENT = ["kosullar", "acik_riza"]


def _app():
    from app.api.main import app

    return TestClient(app)


@pytest.fixture()
def admin(client, monkeypatch):
    from app import auth
    from app.api import main

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


@pytest.fixture()
def announced(monkeypatch):
    from app import book_join

    calls = []
    monkeypatch.setattr(book_join, "announce", lambda owner_id, m, joined: calls.append((owner_id, m["username"], joined)))
    return calls


@pytest.fixture()
def hosted(admin, monkeypatch):
    """Kod adımı açık: hazır sürüm + açık kayıt + giden posta."""
    from app import mailer, social

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("HESAPKITAP_OPEN_SIGNUP", "1")
    monkeypatch.setenv("HESAPKITAP_GOOGLE_CLIENT_IDS", "test")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("MAIL_FROM", "HesapKitap <noreply@example.invalid>")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append((to, subject, text)))
    monkeypatch.setattr(social, "verify_google", lambda t, keys_for=None: {"email": "uye@example.com", "name": "U"})
    from app import email_verify

    assert email_verify.required()
    return admin, sent


def _code(sent, to):
    return re.search(r"\b(\d{6})\b", next(t for a, s, t in reversed(sent) if a == to and "kod" in s)).group(1)


def _book_invite(owner, **kw):
    r = owner.post("/api/book/invites", json={"note": "Üye", **kw})
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _register(c, code, email="uye@example.com", pw="uye-sifre-12345"):
    return c.post("/api/auth/register", json={"consent": CONSENT, "code": code, "username": email, "password": pw})


def test_book_invite_requires_code_then_session_and_announce(hosted, announced):
    from app import email_verify, system

    owner, sent = hosted
    code = _book_invite(owner, book_role="viewer")
    c = _app()
    r = _register(c, code)
    assert r.status_code == 200 and r.json() == {"ok": True, "verify_required": True, "email": "uye@example.com"}
    assert c.get("/api/transactions").status_code == 401  # doğrulanmadan oturum yok
    assert c.get("/api/auth/status").json().get("authenticated") is not True
    m = system.user_by_name("uye@example.com")
    assert m["member_of"] == system.user_by_name("admin")["id"] and m["book_role"] == "viewer"
    assert email_verify.unverified(m["id"]) and email_verify.invite_pending(m["id"])
    assert system.invite(code)["used_at"]  # davet hemen tüketildi: ikinci kez kullanılamaz
    assert _register(_app(), code, "baska@example.com").status_code == 403
    assert announced == []  # sahibe bildirim henüz yok
    members = owner.get("/api/book").json()["members"]
    assert [x["email_verified"] for x in members if x["username"] == "uye@example.com"] == [False]

    lg = _app().post("/api/auth/login", json={"username": "uye@example.com", "password": "uye-sifre-12345"})
    assert lg.status_code == 403 and lg.json()["detail"] == "email_unverified"

    assert c.post("/api/auth/verify-email", json={"email": "uye@example.com", "code": "000000"}).status_code == 400
    assert c.get("/api/transactions").status_code == 401
    assert announced == []

    assert c.post("/api/auth/verify-email", json={"email": "uye@example.com", "code": _code(sent, "uye@example.com")}).status_code == 200
    assert c.get("/api/transactions").status_code == 200
    st = c.get("/api/auth/status").json()
    assert st["authenticated"] and st["user"]["email_verified"] is True
    assert announced == [(m["member_of"], "uye@example.com", True)]
    assert not email_verify.invite_pending(m["id"]) and system.get(f"invite_pending:{m['id']}") is None
    members = owner.get("/api/book").json()["members"]
    assert [x["email_verified"] for x in members if x["username"] == "uye@example.com"] == [True]


def test_admin_invite_requires_code(hosted, announced):
    from app import system

    owner, sent = hosted
    code = owner.post("/api/admin/invites", json={}).json()["code"]
    c = _app()
    r = _register(c, code, "tek@example.com")
    assert r.json() == {"ok": True, "verify_required": True, "email": "tek@example.com"}
    assert c.get("/api/transactions").status_code == 401
    assert not system.user_by_name("tek@example.com")["member_of"]
    assert c.post("/api/auth/verify-email", json={"email": "tek@example.com", "code": _code(sent, "tek@example.com")}).status_code == 200
    assert c.get("/api/transactions").status_code == 200
    assert announced == []  # ortak defter değil: bildirim yok


def test_invite_register_needs_email(hosted):
    owner, _ = hosted
    r = _register(_app(), _book_invite(owner), "uye")
    assert r.status_code == 422


def test_invite_existing_email_neutral_and_invite_kept(hosted, announced):
    """Kayıtlı e-postayla davetli kayıt: yanıt yine aynı, hesaba dokunulmaz, davet tüketilmez, adrese bilgi gider."""
    from app import system

    owner, sent = hosted
    first = owner.post("/api/admin/invites", json={}).json()["code"]
    c = _app()
    _register(c, first, "var@example.com", "var-sifre-12345")
    c.post("/api/auth/verify-email", json={"email": "var@example.com", "code": _code(sent, "var@example.com")})
    code = _book_invite(owner)
    r = _register(_app(), code, "var@example.com", "baska-sifre-123")
    assert r.status_code == 200 and r.json() == {"ok": True, "verify_required": True, "email": "var@example.com"}
    assert "alınmış" not in r.text
    assert any("zaten bir hesabın var" in s for a, s, t in sent if a == "var@example.com")
    assert not system.invite(code)["used_at"] and not system.user_by_name("var@example.com")["member_of"]
    assert _app().post("/api/auth/login", json={"username": "var@example.com", "password": "var-sifre-12345"}).status_code == 200
    assert announced == []


def test_unverified_invite_account_expires_and_releases_invite(hosted, announced):
    from app import email_verify, jobs, system

    owner, _ = hosted
    code = _book_invite(owner)
    _register(_app(), code, "sahte@example.com")
    uid = system.user_by_name("sahte@example.com")["id"]
    assert email_verify.purge_invite_pending() == []  # süre dolmadı
    assert system.user(uid)
    from datetime import datetime, timedelta

    later = datetime.now(jobs.TZ) + timedelta(seconds=email_verify.INVITE_TTL + 60)
    jobs.run_account_purge(later)
    assert system.user(uid) is None and system.user_by_name("sahte@example.com") is None
    assert system.get(f"invite_pending:{uid}") is None and not email_verify.unverified(uid)
    assert not system.invite(code)["used_at"]  # davet serbest: sahip bekleyen davet olarak görür, bağlantı yeniden çalışır
    assert code in [i["code"] for i in owner.get("/api/book").json()["invites"]]
    assert _register(_app(), code, "dogru@example.com").json()["verify_required"] is True
    assert announced == []


def test_pending_invite_account_lazily_purged_on_book_overview(hosted, monkeypatch):
    from app import email_verify, system

    owner, _ = hosted
    code = _book_invite(owner)
    _register(_app(), code, "sahte@example.com")
    uid = system.user_by_name("sahte@example.com")["id"]
    system.put(f"invite_pending:{uid}", f"{code}|{int(time.time()) - email_verify.INVITE_TTL - 1}")
    members = owner.get("/api/book").json()["members"]
    assert all(m["username"] != "sahte@example.com" for m in members)
    assert system.user(uid) is None


def test_open_signup_replaces_pending_invite_account(hosted, announced):
    """Sahibi e-postayı başkasının davetiyle açılmış (doğrulanmamış) hesapta bulursa, habersiz o deftere üye olmaz:
    bekleyen hesap silinir, kendi hesabı açılır."""
    from app import system

    owner, sent = hosted
    code = _book_invite(owner)
    _register(_app(), code, "kurban@example.com", "saldirgan-sifre-1")
    old = system.user_by_name("kurban@example.com")["id"]
    with system.connect() as c:  # dakikada bir gönderim sınırını test için aş
        c.execute("UPDATE email_codes SET sent_at = ?", (int(time.time()) - 120,))
    c = _app()
    r = c.post("/api/auth/register", json={"username": "kurban@example.com", "password": "sahibin-sifresi-1", "consent": CONSENT})
    assert r.json()["verify_required"] is True
    u = system.user_by_name("kurban@example.com")
    assert u["id"] != old and not u["member_of"] and system.user(old) is None
    assert not system.invite(code)["used_at"]
    assert c.post("/api/auth/verify-email", json={"email": "kurban@example.com", "code": _code(sent, "kurban@example.com")}).status_code == 200
    assert announced == []


def test_social_takeover_of_pending_invite_account_does_not_join_book(hosted, announced):
    from app import system

    owner, _ = hosted
    code = _book_invite(owner)
    _register(_app(), code, "uye@example.com")
    old = system.user_by_name("uye@example.com")["id"]
    c = _app()
    g = c.post("/api/auth/google", json={"id_token": "x", "consent": CONSENT})
    assert g.status_code == 200 and g.json()["created"] is True
    u = system.user_by_name("uye@example.com")
    assert u["id"] != old and not u["member_of"] and system.user(old) is None
    assert not system.invite(code)["used_at"]
    assert announced == []


def test_social_with_invite_unchanged(hosted, announced):
    """Google/Apple ile davet: sağlayıcı e-postayı doğruladı; kod adımı yok, hemen üye ve oturum, sahibe bildirim."""
    from app import email_verify, system

    owner, _ = hosted
    code = _book_invite(owner)
    c = _app()
    g = c.post("/api/auth/google", json={"id_token": "x", "consent": CONSENT, "invite": code})
    assert g.status_code == 200 and g.json()["created"] is True
    u = system.user_by_name("uye@example.com")
    assert u["member_of"] == system.user_by_name("admin")["id"]
    assert not email_verify.unverified(u["id"]) and not email_verify.invite_pending(u["id"])
    assert c.get("/api/transactions").status_code == 200
    assert announced == [(u["member_of"], "uye@example.com", True)]


def test_password_reset_completes_pending_invite(hosted, announced, monkeypatch):
    """Şifre sıfırlama kodu da e-postayı kanıtlar: bekleyen davet tamamlanır, sahibe bildirim gider."""
    from app import reset, system

    owner, _ = hosted
    _register(_app(), _book_invite(owner))
    u = system.user_by_name("uye@example.com")
    monkeypatch.setattr(reset, "verify", lambda email, code: system.user_by_name(email))
    monkeypatch.setattr(reset, "notify_changed", lambda u: None)
    c = _app()
    r = c.post("/api/auth/reset", json={"email": "uye@example.com", "code": "123456", "password": "yeni-sifre-12345"})
    assert r.status_code == 200, r.text
    assert c.get("/api/transactions").status_code == 200
    assert announced == [(u["member_of"], "uye@example.com", True)]


def test_self_hosted_invite_register_unchanged(admin, announced):
    """Kendi sunucusu (kod adımı yok): davetle kayıt hemen oturum açar, sahibe bildirim hemen gider."""
    from app import email_verify, system

    assert not email_verify.required()
    code = _book_invite(admin)
    c = _app()
    r = _register(c, code, "uye")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert c.get("/api/transactions").status_code == 200
    u = system.user_by_name("uye")
    assert not email_verify.unverified(u["id"]) and system.get(f"invite_pending:{u['id']}") is None
    assert announced == [(u["member_of"], "uye", True)]
