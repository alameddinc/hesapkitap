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
    assert (p["plan"], p["ai_docs"], p["features"]["fiyatlar"]) == ("free", 3, False)
    assert system.ai_quota(u) == 3
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
