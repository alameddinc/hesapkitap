"""Hesap silme: hemen kapanır, 7 gün geri alınabilir, sonra kalıcı silinir; anonim silme kaydı; mağaza aboneliği."""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_storage import FakeS3, ENDPOINT, SECRET  # noqa: F401 - sahte nesne deposu

CONSENT = ["kosullar", "acik_riza"]
ENTRY = {"entry_date": "2026-10-02", "description": "Market", "amount": 100, "kind": "gider",
         "category": "Market & Gıda", "payment": "nakit"}


def _later(days: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


@pytest.fixture()
def hosted(client, monkeypatch):
    from app import auth
    from app.api import main

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.delenv("HESAPKITAP_DELETE_GRACE_DAYS", raising=False)
    monkeypatch.delenv("REVENUECAT_SECRET_KEY", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH", "Bearer rc-gizli")
    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


@pytest.fixture()
def s3(monkeypatch):
    from app import storage

    fake = FakeS3()
    monkeypatch.setenv("HESAPKITAP_FILES_S3_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("HESAPKITAP_FILES_S3_BUCKET", "hk-dosya")
    monkeypatch.setenv("HESAPKITAP_FILES_S3_KEY", "AKFILES")
    monkeypatch.setenv("HESAPKITAP_FILES_S3_SECRET", SECRET)
    monkeypatch.setattr(storage, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def _user(admin, name="ayse"):
    from app import system
    from app.api.main import app

    inv = admin.post("/api/admin/invites", json={}).json()
    c = TestClient(app)
    assert c.post("/api/auth/register", json={"consent": CONSENT, "code": inv["code"], "username": f"{name}@ornek.com",
                                              "password": f"{name}-sifre-12345"}).status_code == 200
    return c, system.user_by_name(f"{name}@ornek.com")


def _login(name="ayse", mobile=False):
    from app.api.main import app

    c = TestClient(app)
    return c, c.post("/api/auth/login", json={"username": f"{name}@ornek.com", "password": f"{name}-sifre-12345"},
                     headers={"X-HK-Client": "mobile"} if mobile else {})


def _rc(kind, uid, store="APP_STORE", ents=("premium",), exp_ms=4102444800000):
    from app.api.main import app

    return TestClient(app).post("/api/billing/revenuecat", headers={"Authorization": "Bearer rc-gizli"},
                                json={"event": {"type": kind, "app_user_id": str(uid), "entitlement_ids": list(ents),
                                                "expiration_at_ms": exp_ms, "store": store}})


# ---------------------------------------------------------------- sil → giriş 409 → geri al

def test_delete_closes_account_login_409_then_restore_keeps_data(hosted):
    from app import system

    c, u = _user(hosted)
    assert c.post("/api/entries", json=ENTRY).status_code == 200
    r = c.post("/api/me/delete", json={"password": "ayse-sifre-12345"})
    assert r.status_code == 200 and r.json()["purged"] is False
    after = datetime.fromisoformat(r.json()["delete_after"])
    assert timedelta(days=6, hours=23) < after - datetime.now(timezone.utc) <= timedelta(days=7)
    assert c.get("/api/summary").status_code == 401  # oturum hemen düştü
    folder = os.path.dirname(system.user_db_path(u))
    assert os.path.isdir(folder)  # veri duruyor

    m, r = _login(mobile=True)
    assert r.status_code == 409 and "X-Session-Token" not in r.headers
    body = r.json()
    assert body["detail"] == "account_pending_deletion" and body["restorable"] is True
    assert body["delete_after"] == system.user(u["id"])["delete_after"] and body["restore_token"]
    assert m.get("/api/summary").status_code == 401

    assert m.post("/api/me/restore", json={"restore_token": "restore.1.2.3.yanlis"}).status_code == 401
    r = m.post("/api/me/restore", json={"restore_token": body["restore_token"]}, headers={"X-HK-Client": "mobile"})
    assert r.status_code == 200 and r.headers["X-Session-Token"]
    tok = r.headers["X-Session-Token"]
    rows = m.get("/api/transactions", headers={"Authorization": f"Bearer {tok}"}).json()
    assert any(x.get("description") == "Market" for x in rows)  # veriler duruyor
    assert system.user(u["id"])["deleted_at"] is None
    from app import deletion

    assert deletion.record(u["id"]) is None  # geri alınan hesabın silme kaydı yok
    assert m.post("/api/me/restore", json={"restore_token": body["restore_token"]}).status_code == 401  # bir kez
    assert _login()[1].status_code == 200


def test_pending_account_blocks_tokens_mail_and_jobs(hosted):
    from app import inbound, jobs, system

    c, u = _user(hosted)
    token = system.create_token(u["id"], "kısayol")
    address = inbound.address(u)
    assert system.token_owner(token) and inbound.resolve(address)
    assert c.post("/api/me/delete", json={"password": "ayse-sifre-12345"}).status_code == 200
    assert system.token_owner(token) is None
    assert inbound.resolve(address) is None
    assert all((usr or {}).get("id") != u["id"] for _, usr in jobs.spaces())


# ---------------------------------------------------------------- kalıcı silme

def test_purge_after_grace_removes_data_and_objects_keeps_anonymous_record(hosted, s3):
    from app import deletion, storage, system

    c, u = _user(hosted)
    c.post("/api/entries", json=ENTRY)
    storage.put(f"users/{u['id']}/receipts/fis.jpg", b"jpeg", "image/jpeg")
    storage.put("users/999/receipts/baska.jpg", b"jpeg", "image/jpeg")
    folder = os.path.dirname(system.user_db_path(u))
    assert c.post("/api/me/delete", json={"password": "ayse-sifre-12345"}).status_code == 200

    assert deletion.purge_due(_later(6)) == []  # süre dolmadı
    assert system.user(u["id"]) and any(k.startswith(f"users/{u['id']}/") for k in s3.objects)
    assert deletion.purge_due(_later(7.01)) == [u["id"]]
    assert system.user(u["id"]) is None and not os.path.exists(folder)
    assert not any(k.startswith(f"users/{u['id']}/") for k in s3.objects)
    assert "users/999/receipts/baska.jpg" in s3.objects  # başkasınınki durur
    assert system.get(f"consent:{u['id']}") is None
    assert _login()[1].status_code == 401

    rec = deletion.record(u["id"])
    assert rec["purged_at"] and rec["reason"] == "self" and rec["store_customer"] == str(u["id"])
    # düz e-posta hiçbir tabloda yok; özet ile bulunur
    with sqlite3.connect(system.SYSTEM_DB) as db:
        for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            assert "ayse@ornek.com" not in json.dumps(db.execute(f"SELECT * FROM {t}").fetchall(), default=str)
    assert [r["user_id"] for r in deletion.records(email="AYSE@ornek.com ")] == [u["id"]]
    assert deletion.records(email="baska@ornek.com") == []
    found = hosted.get("/api/admin/deleted", params={"email": "ayse@ornek.com"}).json()["records"]
    assert [r["user_id"] for r in found] == [u["id"]] and "ayse" not in json.dumps(found)

    assert deletion.expire_records(_later(7 + 300)) == 0  # kalıcı silmeden (7. gün) 1 yıl sonra düşer
    assert deletion.expire_records(_later(7.02 + 365)) == 1 and deletion.record(u["id"]) is None


def test_grace_zero_deletes_immediately(hosted, monkeypatch):
    from app import deletion, system

    monkeypatch.setenv("HESAPKITAP_DELETE_GRACE_DAYS", "0")
    c, u = _user(hosted)
    r = c.post("/api/me/delete", json={"password": "ayse-sifre-12345"})
    assert r.json() == {"ok": True, "delete_after": None, "purged": True}
    assert system.user(u["id"]) is None and deletion.record(u["id"])["purged_at"]


def test_jobs_scheduler_purges_due_accounts(hosted):
    from app import jobs, system

    c, u = _user(hosted)
    c.post("/api/me/delete", json={"password": "ayse-sifre-12345"})
    assert jobs.run_account_purge(_later(1)) == []
    assert jobs.run_account_purge(_later(8)) == [u["id"]] and system.user(u["id"]) is None


def test_admin_delete_keeps_archive_and_writes_record(hosted):
    from app import deletion, system

    c, u = _user(hosted)
    c.post("/api/entries", json=ENTRY)
    assert hosted.delete(f"/api/admin/users/{u['id']}").json()["ok"]
    assert system.user(u["id"]) is None
    archived = os.listdir(os.path.join(system.data_root(), "_silinen"))
    assert len(archived) == 1 and archived[0].startswith(f"{u['id']}-")
    rec = deletion.record(u["id"])
    assert rec["reason"] == "admin" and rec["purged_at"]


# ---------------------------------------------------------------- abonelik

def test_active_store_subscription_does_not_block_and_reports_renewal(hosted):
    from app import deletion

    c, u = _user(hosted)
    assert _rc("INITIAL_PURCHASE", u["id"]).status_code == 200
    sub = c.get("/api/me/subscription").json()
    assert sub == {"active": True, "will_renew": True, "until": "2100-01-01T00:00:00+00:00", "store": "app_store"}
    assert c.get("/api/auth/status").json()["user"]["subscription"]["will_renew"] is True
    _rc("CANCELLATION", u["id"], exp_ms=4102444800000)
    assert c.get("/api/me/subscription").json()["will_renew"] is False
    _rc("UNCANCELLATION", u["id"])
    assert c.get("/api/me/subscription").json()["will_renew"] is True

    r = c.post("/api/me/delete", json={"password": "ayse-sifre-12345"})  # aktif abonelik silmeyi engellemez
    assert r.status_code == 200
    rec = deletion.record(u["id"])
    assert rec["plan"] == "premium" and rec["plan_source"] == "revenuecat" and rec["will_renew"] == 1
    assert rec["plan_store"] == "app_store"
    pending = _login()[1].json()
    assert pending["subscription"]["will_renew"] is True  # geri alma ekranı da görür

    deletion.purge_due(_later(8))
    r = _rc("RENEWAL", u["id"])  # silinmiş hesapta yenileme: kayda uyarı
    assert r.status_code == 200
    rec = deletion.record(u["id"])
    assert rec["warnings"] and "silinmiş hesapta" in rec["warnings"][-1]["warning"]
    listed = hosted.get("/api/admin/deleted").json()["records"]
    assert listed[0]["user_id"] == u["id"] and listed[0]["warnings"]


def test_self_hosted_subscription_inactive(client):
    from app import plans

    assert plans.subscription({"plan": "premium", "plan_source": "revenuecat"})["active"] is False


class FakeRC:
    def __init__(self, store="play_store"):
        self.calls: list[tuple[str, str]] = []
        self.store = store

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk_gizli"
        self.calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"subscriber": {"subscriptions": {
                "premium_aylik": {"store": self.store, "expires_date": "2100-01-01T00:00:00Z",
                                  "unsubscribe_detected_at": None, "store_transaction_id": "GPA.1234-5678..0"},
                "eski": {"store": self.store, "expires_date": "2020-01-01T00:00:00Z",
                         "unsubscribe_detected_at": None, "store_transaction_id": "GPA.0000"}}}})
        return httpx.Response(200, json={})


@pytest.mark.parametrize("store", ["PLAY_STORE", "APP_STORE"])
def test_revenuecat_key_cancels_android_and_deletes_customer(hosted, monkeypatch, store):
    from app import deletion

    fake = FakeRC("play_store" if store == "PLAY_STORE" else "app_store")
    monkeypatch.setenv("REVENUECAT_SECRET_KEY", "sk_gizli")
    monkeypatch.setattr(deletion, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(fake)))
    c, u = _user(hosted)
    _rc("INITIAL_PURCHASE", u["id"], store=store)
    assert c.post("/api/me/delete", json={"password": "ayse-sifre-12345"}).status_code == 200
    uid = u["id"]
    if store == "PLAY_STORE":  # yenileme silme anında durdurulur (yalnız süresi geçmemiş abonelik)
        assert fake.calls == [("GET", f"/v1/subscribers/{uid}"),
                              ("POST", f"/v1/subscribers/{uid}/subscriptions/GPA.1234-5678..0/cancel")]
    else:  # Apple: iptal API'si yok, yalnız okunur
        assert fake.calls == [("GET", f"/v1/subscribers/{uid}")]
    fake.calls.clear()
    deletion.purge_due(_later(8))
    assert fake.calls == [("DELETE", f"/v1/subscribers/{uid}")]  # kalıcı silmede müşteri kaydı
    actions = [n.get("action") for n in deletion.record(uid)["notes"]]
    assert "rc_delete" in actions


def test_revenuecat_failure_never_blocks_delete(hosted, monkeypatch):
    from app import deletion, system

    def boom(request):
        raise httpx.ConnectError("ağ yok")

    monkeypatch.setenv("REVENUECAT_SECRET_KEY", "sk_gizli")
    monkeypatch.setattr(deletion, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(boom)))
    c, u = _user(hosted)
    _rc("INITIAL_PURCHASE", u["id"], store="PLAY_STORE")
    assert c.post("/api/me/delete", json={"password": "ayse-sifre-12345"}).status_code == 200
    assert deletion.purge_due(_later(8)) == [u["id"]] and system.user(u["id"]) is None


def test_no_revenuecat_key_makes_no_calls(hosted, monkeypatch):
    from app import deletion

    def never(request):
        raise AssertionError("ağa çıkılmamalı")

    monkeypatch.setattr(deletion, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(never)))
    c, u = _user(hosted)
    _rc("INITIAL_PURCHASE", u["id"], store="PLAY_STORE")
    c.post("/api/me/delete", json={"password": "ayse-sifre-12345"})
    assert deletion.purge_due(_later(8)) == [u["id"]]


# ---------------------------------------------------------------- ortak defter, Google

@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.delenv("HESAPKITAP_DELETE_GRACE_DAYS", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _account(by, name, member=False):
    from app import system
    from app.api.main import app

    inv = by.post("/api/book/invites" if member else "/api/admin/invites", json={"note": name}).json()
    c = TestClient(app)
    assert c.post("/api/auth/register", json={"code": inv["code"], "username": name,
                                              "password": f"{name}-sifre-12345"}).status_code == 200
    return c, system.user_by_name(name)


def test_owner_delete_takes_members_restore_brings_them_back(owner):
    from app import deletion, system
    from app.api.main import app

    o, ou = _account(owner, "sahip")
    m, mu = _account(o, "uye", member=True)
    assert o.post("/api/me/delete", json={"password": "sahip-sifre-12345"}).status_code == 200
    assert system.user(mu["id"])["deleted_with"] == ou["id"]
    assert m.get("/api/summary").status_code == 401  # üyenin oturumu da düştü

    r = TestClient(app).post("/api/auth/login", json={"username": "uye", "password": "uye-sifre-12345"})
    assert r.status_code == 409 and r.json()["restorable"] is False and r.json()["restore_token"] is None

    r = TestClient(app).post("/api/auth/login", json={"username": "sahip", "password": "sahip-sifre-12345"})
    assert r.status_code == 409 and r.json()["restorable"]
    assert TestClient(app).post("/api/me/restore", json={"restore_token": r.json()["restore_token"]}).status_code == 200
    assert system.user(mu["id"])["deleted_at"] is None and deletion.record(mu["id"]) is None
    assert TestClient(app).post("/api/auth/login", json={"username": "uye", "password": "uye-sifre-12345"}).status_code == 200


def test_owner_purge_removes_members_member_delete_only_self(owner):
    from app import deletion, system

    o, ou = _account(owner, "sahip")
    m, mu = _account(o, "uye", member=True)
    m2, mu2 = _account(o, "uye2", member=True)
    assert m2.post("/api/me/delete", json={"password": "uye2-sifre-12345"}).status_code == 200
    assert system.user(ou["id"])["deleted_at"] is None and system.user(mu["id"])["deleted_at"] is None
    assert o.post("/api/me/delete", json={"password": "sahip-sifre-12345"}).status_code == 200
    assert system.user(mu2["id"])["deleted_with"] is None  # kendi silmişti: kendi tarihi kalır
    assert sorted(deletion.purge_due(_later(8))) == sorted([ou["id"], mu["id"], mu2["id"]])
    assert all(system.user(i) is None for i in (ou["id"], mu["id"], mu2["id"]))
    assert deletion.record(mu["id"])["reason"] == "owner"


def test_google_login_on_pending_account_offers_restore(client, monkeypatch):
    import base64
    import time

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    from app import auth, social, system
    from app.api import main
    from app.api.main import app

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = key.public_key().public_numbers()
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    jwk = {"kid": "k1", "kty": "RSA", "n": b64(n.n.to_bytes(256, "big")), "e": b64(n.e.to_bytes(3, "big"))}
    monkeypatch.setattr(social, "_google_keys", lambda force=False: {"k1": jwk})
    monkeypatch.setenv("HESAPKITAP_GOOGLE_CLIENT_IDS", "web-1.apps.googleusercontent.com")
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("HESAPKITAP_OPEN_SIGNUP", "1")
    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")

    def token():
        now = int(time.time())
        body = {"iss": "https://accounts.google.com", "aud": "web-1.apps.googleusercontent.com", "email": "ayse@gmail.com",
                "email_verified": True, "iat": now, "exp": now + 3600}
        head = b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
        payload = b64(json.dumps(body).encode())
        return f"{head}.{payload}.{b64(key.sign(f'{head}.{payload}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"

    c = TestClient(app)
    assert c.post("/api/auth/google", json={"id_token": token(), "consent": CONSENT}).status_code == 200
    assert c.post("/api/me/delete", json={"confirm": "SİL"}).status_code == 200
    r = TestClient(app).post("/api/auth/google", json={"id_token": token()})
    assert r.status_code == 409 and r.json()["detail"] == "account_pending_deletion"
    assert TestClient(app).post("/api/me/restore", json={"restore_token": r.json()["restore_token"]}).status_code == 200
    u = system.user_by_name("ayse@gmail.com")
    assert u["deleted_at"] is None and system.get(f"pw_unset:{u['id']}") == "1"  # şifresiz hesap öyle kalır


def test_password_reset_on_pending_account_offers_restore(client, monkeypatch):
    import re

    from app import auth, mailer, system
    from app.api import main
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.delenv("HESAPKITAP_DELETE_GRACE_DAYS", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("MAIL_FROM", "HesapKitap <noreply@example.com>")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append(subject))
    auth._fails.clear()
    main._forgots.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    u = auth.create_user("ayse@ornek.com", "eski-sifre-12345")
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "ayse@ornek.com", "password": "eski-sifre-12345"}).status_code == 200
    assert c.post("/api/me/delete", json={"password": "eski-sifre-12345"}).status_code == 200
    c.post("/api/auth/forgot", json={"email": "ayse@ornek.com"})
    code = re.search(r"\b(\d{6})\b", sent[-1]).group(1)
    r = c.post("/api/auth/reset", json={"email": "ayse@ornek.com", "code": code, "password": "yeni-sifre-12345"})
    assert r.status_code == 409 and r.json()["restorable"]
    assert c.get("/api/summary").status_code == 401  # oturum açılmadı
    assert c.post("/api/me/restore", json={"restore_token": r.json()["restore_token"]}).status_code == 200
    assert system.user(u["id"])["deleted_at"] is None and c.get("/api/auth/status").json()["authenticated"]
