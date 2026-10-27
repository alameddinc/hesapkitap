"""Çok kullanıcı: davetle kayıt, yönetici kullanıcı işlemleri, ayar ayrımı ve yapay zekâ kotası."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def admin_client(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _new_client():
    from app.api.main import app

    return TestClient(app)


def test_invite_register_flow(admin_client):
    inv = admin_client.post("/api/admin/invites", json={"note": "Ayşe"}).json()
    c = _new_client()
    assert c.get(f"/api/auth/invite/{inv['code']}").json() == {"valid": True}
    assert c.post("/api/auth/register", json={"code": "YANL-IS00-KOD0", "username": "ayse",
                                              "password": "ayse-sifre-12345"}).status_code == 403
    r = c.post("/api/auth/register", json={"code": inv["code"].lower(), "username": "ayse", "password": "ayse-sifre-12345"})
    assert r.status_code == 200
    assert c.get("/api/auth/status").json()["user"]["username"] == "ayse"
    assert c.get("/api/meta").status_code == 200
    # kod tek kullanımlık
    d = _new_client()
    assert d.post("/api/auth/register", json={"code": inv["code"], "username": "mehmet",
                                              "password": "mehmet-sifre-123"}).status_code == 403
    rows = admin_client.get("/api/admin/users").json()
    assert rows["invites"][0]["used_by_name"] == "ayse"
    # yönetici olmayan yönetici uçlarına giremez
    assert c.get("/api/admin/users").status_code == 403
    assert c.post("/api/admin/invites", json={}).status_code == 403


def test_admin_created_user_must_change_password(admin_client):
    from app import system

    r = admin_client.post("/api/admin/users", json={"username": "ali"}).json()
    temp = r["temp_password"]
    c = _new_client()
    assert c.post("/api/auth/login", json={"username": "ali", "password": temp}).status_code == 200
    assert c.get("/api/auth/status").json()["user"]["must_change_password"] is True
    assert c.get("/api/meta").json()["detail"] == "password_change_required"
    assert c.post("/api/settings/password", json={"current": temp, "new": "ali-yeni-sifre-1"}).status_code == 200
    assert c.get("/api/meta").status_code == 200
    # şifre sıfırlama eski oturumu düşürür
    uid = r["user"]["id"]
    temp2 = admin_client.post(f"/api/admin/users/{uid}/reset-password").json()["temp_password"]
    assert c.get("/api/meta").status_code == 401
    assert c.post("/api/auth/login", json={"username": "ali", "password": temp2}).status_code == 200
    # kapat / aç
    admin_client.patch(f"/api/admin/users/{uid}", json={"disabled": True})
    assert c.get("/api/meta").status_code == 401
    assert c.post("/api/auth/login", json={"username": "ali", "password": temp2}).status_code == 401
    # sil: veri klasörü arşive
    assert admin_client.delete(f"/api/admin/users/{uid}").status_code == 200
    assert system.user(uid) is None
    # yönetici silinemez / kapatılamaz
    aid = system.user_by_name("admin")["id"]
    assert admin_client.delete(f"/api/admin/users/{aid}").status_code == 404
    assert admin_client.patch(f"/api/admin/users/{aid}", json={"disabled": True}).status_code == 409


def test_config_split_and_quota(admin_client, monkeypatch):
    from app import receipt, system

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert admin_client.put("/api/settings/config", json={"name": "GEMINI_API_KEY", "value": "AIzaADMIN0000aaaa"}).status_code == 200
    r = admin_client.post("/api/admin/users", json={"username": "veli"}).json()
    uid = r["user"]["id"]
    c = _new_client()
    c.post("/api/auth/login", json={"username": "veli", "password": r["temp_password"]})
    c.post("/api/settings/password", json={"current": r["temp_password"], "new": "veli-yeni-sifre-1"})
    # kullanıcı yönetici ayarını değiştiremez, yönetici anahtarını göremez
    assert c.put("/api/settings/config", json={"name": "TELEGRAM_BOT_TOKEN", "value": "x"}).status_code == 403
    assert "aaaa" not in c.get("/api/settings/config").text
    assert c.get("/api/meta").json()["receipt_enabled"] is True  # ortak anahtar
    assert c.get("/api/me/ai").json()["quota"] == 100

    import httpx

    keys = []

    def fake_post(url, json=None, timeout=None, headers=None):
        keys.append(headers["x-goog-api-key"])
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", fake_post)
    admin_client.patch(f"/api/admin/users/{uid}", json={"ai_quota": 1})
    with system_space(uid):
        receipt._gemini_json("x", b"data", "image/png")
        with pytest.raises(receipt.ReceiptError, match="hakkın doldu"):
            receipt._gemini_json("x", b"data", "image/png")
    assert system.ai_used(uid) == 1
    # kendi anahtarı: kota yok
    assert c.put("/api/settings/config", json={"name": "GEMINI_API_KEY", "value": "AIzaVELI0000bbbb"}).status_code == 200
    assert c.get("/api/me/ai").json()["own_key"] is True
    with system_space(uid):
        receipt._gemini_json("x", b"data", "image/png")
    assert system.ai_used(uid) == 1 and keys == ["AIzaADMIN0000aaaa", "AIzaVELI0000bbbb"]
    # yöneticinin anahtarı değişmedi
    from app import config

    assert config.get("GEMINI_API_KEY") == "AIzaADMIN0000aaaa"


def system_space(uid):
    from app import db, system

    u = system.user(uid)
    return db.use_space(system.user_db_path(u), u)


def test_existing_user_db_gets_new_columns(tmp_path, tmpdb):
    """Eski sürümde açılmış kullanıcı veritabanı: ilk kullanımda göçler uygulanır (yeni kolonlar eklenir)."""
    import sqlite3

    from app import db

    p = str(tmp_path / "eski" / "ekstre.db")
    (tmp_path / "eski").mkdir()
    old = sqlite3.connect(p)
    old.executescript("CREATE TABLE entries (id INTEGER PRIMARY KEY, entry_date DATE NOT NULL, month TEXT NOT NULL, "
                      "description TEXT NOT NULL, amount REAL NOT NULL, kind TEXT NOT NULL, category TEXT);")
    old.close()
    db._ready_dbs.discard(p)
    with db.use_space(p, {"id": 9, "username": "eski", "role": "user"}):
        eid = db.create_entry({"entry_date": "2026-10-01", "description": "x", "amount": 5, "kind": "gider",
                               "card_last4": "1234", "created_by": 9, "hidden": 0})
        cols = {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(entries)")}
    assert eid and {"card_last4", "hidden", "created_by"} <= cols
