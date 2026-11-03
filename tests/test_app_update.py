"""Mobil uygulama güncelleme yönlendirmesi (yetenek app_update): ortam değişkeni ya da yönetici ayarı (system.db);
varsayılan boş = kapalı. Paket bilgisinde kaynak (manual | store): elle verilen paketin bitişi istemcide hatırlatılır."""
import pytest

from tests import test_plans
from tests.test_plans import _rc, _user

hosted = test_plans.hosted


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from app import app_update

    for p in ("IOS", "ANDROID"):
        for f in ("LATEST", "MIN", "STORE_URL"):
            monkeypatch.delenv(f"HESAPKITAP_APP_{p}_{f}", raising=False)
    app_update._cache = None
    yield
    app_update._cache = None


def test_off_by_default(client):
    r = client.get("/api/meta").json()
    assert "app_update" in r["capabilities"] and r["app_update"] == {}
    assert client.get("/api/auth/status").json()["app_update"] == {}


def test_env_values_and_default_store(client, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_APP_IOS_LATEST", "1.0.1")
    monkeypatch.setenv("HESAPKITAP_APP_IOS_MIN", "1.0")
    au = client.get("/api/meta").json()["app_update"]
    assert au == {"ios": {"latest": "1.0.1", "min": "1.0", "store_url": "https://apps.apple.com/app/id6828734294"}}
    # girişten önce de (zorunlu güncelleme ekranı oturum açmadan gösterilir)
    assert client.get("/api/auth/status").json()["app_update"] == au


def test_invalid_env_ignored_and_min_only(client, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_APP_IOS_LATEST", "yeni")
    monkeypatch.setenv("HESAPKITAP_APP_ANDROID_MIN", "1.2.0")
    au = client.get("/api/meta").json()["app_update"]
    assert "ios" not in au
    assert au["android"] == {"latest": "1.2.0", "min": "1.2.0",
                             "store_url": "https://play.google.com/store/apps/details?id=com.alameddinc.hesapkitap"}


def test_parse():
    from app import app_update

    assert app_update.parse("1.2") == (1, 2, 0) and app_update.parse(" 10.0.3 ") == (10, 0, 3)
    assert app_update.parse("1.0.0-beta") is None and app_update.parse("") is None and app_update.parse(None) is None
    assert app_update.parse("1.10.0") > app_update.parse("1.9.9")


def test_admin_sets_overrides_env(hosted, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_APP_ANDROID_LATEST", "1.0.0")
    c, _ = _user(hosted)
    assert c.get("/api/admin/app-update").status_code == 403
    assert c.put("/api/admin/app-update", json={"ios": {"latest": "9.9.9"}}).status_code == 403
    g = hosted.get("/api/admin/app-update").json()
    assert g["fields"]["android"]["latest"] == {"value": "1.0.0", "source": "ortam"}
    assert g["fields"]["ios"]["min"] == {"value": None, "source": None}

    r = hosted.put("/api/admin/app-update", json={"ios": {"latest": "1.0.1", "min": "1.0.0", "store_url": ""},
                                                 "android": {"latest": "1.0.2"}})
    assert r.status_code == 200
    eff = r.json()["effective"]
    assert eff["ios"]["latest"] == "1.0.1" and eff["ios"]["min"] == "1.0.0"
    assert eff["android"] == {"latest": "1.0.2", "min": None,
                              "store_url": "https://play.google.com/store/apps/details?id=com.alameddinc.hesapkitap"}
    assert r.json()["fields"]["android"]["latest"]["source"] == "ayar"
    assert c.get("/api/meta").json()["app_update"] == eff  # kullanıcıya hemen yansır (önbellek sıfırlandı)

    # boş değer alanı ayardan düşürür: ortam değişkeni geri gelir
    r = hosted.put("/api/admin/app-update", json={"android": {"latest": ""}})
    assert r.json()["effective"]["android"]["latest"] == "1.0.0" and r.json()["effective"]["ios"]["latest"] == "1.0.1"
    r = hosted.put("/api/admin/app-update", json={"reset": True})
    assert "ios" not in r.json()["effective"]


@pytest.mark.parametrize("body", [
    {"ios": {"latest": "bir"}},
    {"ios": {"latest": "1.0.0", "min": "1.1.0"}},
    {"ios": {"store_url": "http://ornek.com"}},
    {"ios": {"surum": "1.0"}},
    {"windows": {"latest": "1.0"}},
])
def test_admin_validation(hosted, body):
    assert hosted.put("/api/admin/app-update", json=body).status_code == 422
    assert hosted.get("/api/meta").json()["app_update"] == {}


def test_plan_source_manual_vs_store(hosted):
    c, u = _user(hosted, "elle")
    assert c.get("/api/meta").json()["plan"]["source"] is None  # ücretsiz
    hosted.patch(f"/api/admin/users/{u['id']}", json={"plan": "premium", "plan_days": 5})
    p = c.get("/api/meta").json()["plan"]
    assert p["source"] == "manual" and p["until"]
    c2, u2 = _user(hosted, "magaza")
    assert _rc(hosted, "INITIAL_PURCHASE", u2["id"]).status_code == 200
    assert c2.get("/api/meta").json()["plan"]["source"] == "store"
    assert hosted.get("/api/meta").json()["plan"]["source"] is None  # yönetici: süresiz
