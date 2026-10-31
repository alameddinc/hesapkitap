import pytest


@pytest.fixture()
def authed(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth._setup_code = None
    return client


def test_env_password(authed, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "dogru-sifre-123")
    c = authed
    assert c.get("/api/meta").status_code == 401
    assert c.post("/api/auth/login", json={"password": "yanlis"}).status_code == 401
    assert c.post("/api/auth/login", json={"password": "dogru-sifre-123"}).status_code == 200
    assert c.get("/api/meta").status_code == 200
    # env'den gelen şifre uygulamadan değiştirilemez
    assert c.post("/api/settings/password", json={"current": "dogru-sifre-123", "new": "baska-sifre-456"}).status_code == 409
    c.post("/api/auth/logout")
    assert c.get("/api/meta").status_code == 401


def test_first_run_setup_local(authed):
    c = authed  # TestClient istemci IP'si 'testclient' → yerel değil sayılır; yerel durumu ayrıca test ediliyor
    st = c.get("/api/auth/status").json()
    assert st["setup_required"] is True
    assert c.get("/api/meta").json()["detail"] == "setup_required"


def test_first_run_setup_remote_needs_code(authed):
    from app import auth

    c = authed
    assert c.get("/api/auth/status").json()["setup_code_needed"] is True
    assert c.post("/api/auth/setup", json={"password": "uzun-bir-sifre-1"}).status_code == 403
    code = auth.setup_code()
    assert c.post("/api/auth/setup", json={"password": "kisa", "code": code}).status_code == 422
    assert c.post("/api/auth/setup", json={"password": "uzun-bir-sifre-1", "code": code}).status_code == 200
    assert c.get("/api/meta").status_code == 200  # kurulumla oturum açıldı
    assert c.post("/api/auth/setup", json={"password": "baska-sifre-999", "code": code}).status_code == 409

    # şifre değiştir: eski oturumlar geçersiz olur, bu cihaz yeni çerezle devam eder
    old_cookie = c.cookies.get(auth.COOKIE)
    assert c.post("/api/settings/password", json={"current": "yanlis", "new": "yeni-sifre-12345"}).status_code == 403
    assert c.post("/api/settings/password", json={"current": "uzun-bir-sifre-1", "new": "yeni-sifre-12345"}).status_code == 200
    assert not auth.valid_token(old_cookie)
    assert c.get("/api/meta").status_code == 200
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"password": "uzun-bir-sifre-1"}).status_code == 401
    assert c.post("/api/auth/login", json={"password": "yeni-sifre-12345"}).status_code == 200


def test_local_setup_without_code(authed, monkeypatch):
    from app import auth

    monkeypatch.setattr(auth, "is_local", lambda ip: True)
    assert authed.post("/api/auth/setup", json={"password": "uzun-bir-sifre-1"}).status_code == 200


def test_password_is_hashed(authed, tmpdb):
    from app import auth

    monkeypatch_ip = auth.is_local
    auth.is_local = lambda ip: True
    try:
        authed.post("/api/auth/setup", json={"password": "uzun-bir-sifre-1"})
    finally:
        auth.is_local = monkeypatch_ip
    from app import system

    stored = system.users()[0]["password_hash"]
    assert stored.startswith("pbkdf2$") and "uzun-bir-sifre-1" not in stored


def test_rate_limit(authed, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "dogru-sifre-123")
    for _ in range(5):
        authed.post("/api/auth/login", json={"password": "x"})
    assert authed.post("/api/auth/login", json={"password": "dogru-sifre-123"}).status_code == 429


def test_tampered_cookie(authed, monkeypatch):
    from app import auth

    monkeypatch.setenv("APP_PASSWORD", "dogru-sifre-123")
    authed.cookies.set(auth.COOKIE, "9999999999.abcd.deadbeef")
    assert authed.get("/api/meta").status_code == 401


def test_config_keys_from_settings(authed, monkeypatch):
    from app import config

    monkeypatch.setenv("HESAPKITAP_NO_AUTH", "1")
    c = authed
    assert c.put("/api/settings/config", json={"name": "GEMINI_API_KEY", "value": "AIzaTEST1234abcd"}).status_code == 200
    d = {x["name"]: x for x in c.get("/api/settings/config").json()}
    assert d["GEMINI_API_KEY"]["source"] == "app" and d["GEMINI_API_KEY"]["value_hint"] == "••••abcd"
    assert "AIzaTEST" not in c.get("/api/settings/config").text  # tam değer asla dönmez
    assert config.get("GEMINI_API_KEY") == "AIzaTEST1234abcd"
    assert c.get("/api/meta").json()["receipt_enabled"] is True
    # env öncelikli ve uygulamadan değiştirilemez
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token-9999")
    assert c.put("/api/settings/config", json={"name": "TELEGRAM_BOT_TOKEN", "value": "x"}).status_code == 409
    assert c.put("/api/settings/config", json={"name": "BILINMEYEN", "value": "x"}).status_code == 404
    pw = c.post("/api/settings/config/generate-backup-passphrase").json()["passphrase"]
    assert config.get("BACKUP_PASSPHRASE") == pw and len(pw) >= 30


def test_single_user_install_migrates_to_admin(authed, tmpdb):
    """Eski kurulum: ana veritabanında şifre hash'i var → yönetici 'admin' olur, şifre ve veri aynen."""
    from app import auth, system

    tmpdb.set_setting("auth:password_hash", auth._hash("eski-sifre-1234"))
    tmpdb.create_entry({"entry_date": "2026-10-01", "description": "Eski kayıt", "amount": 10, "kind": "gider"})
    assert not system.has_users()
    assert authed.post("/api/auth/login", json={"password": "eski-sifre-1234"}).status_code == 200   # kullanıcı adsız
    u = system.users()[0]
    assert (u["username"], u["role"], u["db_file"]) == ("admin", "admin", None)
    assert any(e["description"] == "Eski kayıt" for e in authed.get("/api/transactions", params={"month": "2026-10"}).json())
    authed.post("/api/auth/logout")
    assert authed.post("/api/auth/login", json={"username": "admin", "password": "eski-sifre-1234"}).status_code == 200


def test_users_cannot_see_each_others_data(authed, tmpdb):
    """İki kullanıcı: her istek kendi veritabanında çalışır; yönetici başkasının verisini görmez."""
    from fastapi.testclient import TestClient

    from app import auth, system
    from app.api.main import app

    admin = auth.create_admin("admin", "yonetici-sifre-1")
    ayse = auth.create_user("ayse", "ayse-sifre-12345")
    a, b = TestClient(app), TestClient(app)
    assert a.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    assert b.post("/api/auth/login", json={"username": "ayse", "password": "ayse-sifre-12345"}).status_code == 200
    assert a.post("/api/entries", json={"entry_date": "2026-10-01", "description": "Yönetici gideri", "amount": 5,
                                        "kind": "gider"}).status_code == 200
    assert b.post("/api/entries", json={"entry_date": "2026-10-01", "description": "Ayşe gideri", "amount": 7,
                                        "kind": "gider"}).status_code == 200
    ea = {e["description"] for e in a.get("/api/transactions", params={"month": "2026-10"}).json()}
    eb = {e["description"] for e in b.get("/api/transactions", params={"month": "2026-10"}).json()}
    assert "Ayşe gideri" not in ea and "Yönetici gideri" not in eb and "Ayşe gideri" in eb
    assert b.get("/api/auth/status").json()["user"]["username"] == "ayse"
    # ayrı dosya
    assert system.user_db_path(system.user(ayse["id"])).endswith(f"{ayse['id']}/ekstre.db")
    # kapatılan kullanıcının oturumu düşer
    system.update_user(ayse["id"], disabled=1)
    assert b.get("/api/meta").status_code == 401
    assert admin["role"] == "admin"


def test_hosted_single_admin_needs_username(authed, monkeypatch):
    from app import auth

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    auth.create_admin("admin@ornek.com", "yonetici-sifre-1")
    assert authed.post("/api/auth/login", json={"password": "yonetici-sifre-1"}).status_code == 401
