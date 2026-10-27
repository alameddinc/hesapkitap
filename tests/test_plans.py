"""Paketler (barındırılan sürüm): ücretsiz / premium / aile; kendi sunucusunda her şey açık."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def hosted(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH", "Bearer rc-gizli")
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _user(admin, name="ayse"):
    from app.api.main import app

    inv = admin.post("/api/admin/invites", json={}).json()
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"code": inv["code"], "username": name, "password": f"{name}-sifre-12345"})
    assert r.status_code == 200
    from app import system

    return c, next(u for u in system.users() if u["username"] == name)


def _rc(admin, kind, uid, ents=("premium",), exp_ms=4102444800000):
    return admin.post("/api/billing/revenuecat", headers={"Authorization": "Bearer rc-gizli"},
                      json={"event": {"type": kind, "app_user_id": str(uid), "entitlement_ids": list(ents),
                                      "expiration_at_ms": exp_ms}})


def test_self_hosted_everything_open(client):
    from app import plans

    assert not plans.hosted()
    meta = client.get("/api/meta").json()["plan"]
    assert meta["plan"] == "self" and all(meta["features"].values())
    assert client.get("/api/prices").status_code == 200
    assert client.post("/api/billing/revenuecat", json={}).status_code in (401, 404)


def test_free_user_is_limited_and_store_purchase_unlocks(hosted):
    from app import system

    c, u = _user(hosted)
    p = c.get("/api/meta").json()["plan"]
    assert (p["plan"], p["ai_docs"], p["features"]["fiyatlar"]) == ("free", 10, False)
    assert system.ai_quota(u) == 10
    r = c.get("/api/prices")
    assert r.status_code == 402 and r.json()["plan_required"] == "fiyatlar"
    assert c.get("/api/settings/export.zip").status_code == 200  # verini indirmek her pakette serbest

    # yanlış anahtarlı webhook işlenmez
    assert hosted.post("/api/billing/revenuecat", headers={"Authorization": "x"}, json={}).status_code == 404
    assert _rc(hosted, "INITIAL_PURCHASE", u["id"]).status_code == 200
    p = c.get("/api/meta").json()["plan"]
    assert (p["plan"], p["ai_docs"]) == ("premium", None) and p["until"].startswith("2100-01-01")
    assert c.get("/api/prices").status_code == 200
    assert system.ai_quota(system.user(u["id"])) is None
    # premium aileye davet edemez
    assert c.post("/api/book/invites", json={"note": "Eşim"}).status_code == 402

    # iptal: süre bitene kadar sürer; bitince ücretsize döner
    _rc(hosted, "CANCELLATION", u["id"])
    assert c.get("/api/meta").json()["plan"]["plan"] == "premium"
    _rc(hosted, "EXPIRATION", u["id"], ents=())
    assert c.get("/api/meta").json()["plan"]["plan"] == "free"


def test_expired_subscription_falls_back_to_free(hosted):
    from app import plans, system

    _, u = _user(hosted)
    system.update_user(u["id"], plan="premium", plan_until="2020-01-01T00:00:00+00:00")
    assert plans.plan_of(system.user(u["id"])) == "free"


def test_family_plan_covers_members(hosted):
    from app import plans, system
    from app.api.main import app

    c, u = _user(hosted)
    _rc(hosted, "INITIAL_PURCHASE", u["id"], ents=("aile",))
    inv = c.post("/api/book/invites", json={"note": "Eşim"})
    assert inv.status_code == 200
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"code": inv.json()["code"], "username": "esim",
                                              "password": "esim-sifre-12345"}).status_code == 200
    member = next(x for x in system.users() if x["username"] == "esim")
    assert plans.plan_of(member) == "aile"  # üye sahibin paketini kullanır
    assert m.get("/api/prices").status_code == 200


def test_open_signup_only_when_enabled(hosted, monkeypatch):
    from app.api.main import app

    c = TestClient(app)
    assert c.get("/api/auth/status").json()["open_signup"] is False
    assert c.post("/api/auth/register", json={"username": "yeni", "password": "yeni-sifre-12345"}).status_code == 403
    monkeypatch.setenv("HESAPKITAP_OPEN_SIGNUP", "1")
    assert c.get("/api/auth/status").json()["open_signup"] is True
    assert c.post("/api/auth/register", json={"username": "yeni", "password": "yeni-sifre-12345"}).status_code == 200
    assert c.get("/api/meta").json()["plan"]["plan"] == "free"
    # aynı adresten saatte en fazla 5 hesap
    codes = [TestClient(app).post("/api/auth/register", json={"username": f"yeni{i}", "password": "yeni-sifre-12345"}).status_code
             for i in range(6)]
    assert codes[:4] == [200] * 4 and codes[-1] == 429


def test_hosted_user_sees_no_keys_and_cannot_set_own(hosted):
    c, _ = _user(hosted)
    assert c.get("/api/settings/config").json() == []
    r = c.put("/api/settings/config", json={"name": "GEMINI_API_KEY", "value": "kendi-anahtarim"})
    assert r.status_code == 403
    assert c.get("/api/me/ai").json()["own_key"] is False


def test_delete_my_account_removes_data(hosted):
    import os

    from app import system

    c, u = _user(hosted)
    c.post("/api/entries", json={"entry_date": "2026-10-02", "description": "Market", "amount": 100, "kind": "gider",
                                 "category": "Market & Gıda", "payment": "nakit"})
    folder = os.path.dirname(system.user_db_path(u))
    assert os.path.isdir(folder)
    assert c.post("/api/me/delete", json={"password": "yanlis"}).status_code == 403
    assert c.post("/api/me/delete", json={"password": "ayse-sifre-12345"}).status_code == 200
    assert system.user(u["id"]) is None and not os.path.exists(folder)
    assert c.get("/api/summary").status_code == 401  # oturum da düştü


def test_hosted_zakat_off_by_default_and_needs_consent(hosted):
    import json

    from app import db, system

    c, u = _user(hosted)
    meta = c.get("/api/meta").json()
    assert meta["modules"]["zekat"] is False and "zekat" in meta["module_consent"]
    assert meta["modules"]["plan"] is True
    r = c.put("/api/settings/modules", json={"modules": {"zekat": True}})
    assert r.status_code == 422 and "rıza" in r.json()["detail"]
    assert c.put("/api/settings/modules", json={"modules": {"zekat": True}, "consent": ["zekat"]}).status_code == 200
    assert c.get("/api/meta").json()["modules"]["zekat"] is True
    with db.use_space(system.user_db_path(u), u):
        rec = json.loads(db.get_setting("consent:zekat"))
    assert rec["version"] == "1" and rec["user"] == u["id"] and rec["at"]
    # kapatınca rıza geri alınır
    c.put("/api/settings/modules", json={"modules": {"zekat": False}})
    with db.use_space(system.user_db_path(u), u):
        assert db.get_setting("consent:zekat") is None


def test_self_hosted_zakat_on_without_consent(client):
    meta = client.get("/api/meta").json()
    assert meta["modules"]["zekat"] is True and meta["module_consent"] == {}
