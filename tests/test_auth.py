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
    stored = tmpdb.get_setting("auth:password_hash")
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
