"""Kişiselleştirme: özellik aç/kapat (defter), görünüm (kişi)."""
from datetime import date


def test_modules_toggle_hides_insights_and_zakat_due(client, tmpdb, monkeypatch):
    from app import finance, prefs, zakat

    m = client.get("/api/meta").json()
    assert m["modules"]["zekat"] is True and m["modules_chosen"] is False and "zekat" in m["module_labels"]
    monkeypatch.setattr(zakat, "self_due", lambda: 1234.0)
    assert finance.net_worth(date.today())["zakat_due"] == 1234.0
    client.put("/api/settings/modules", json={"modules": {"zekat": False, "olmayan": False}})
    m = client.get("/api/meta").json()
    assert m["modules"]["zekat"] is False and m["modules_chosen"] is True and "olmayan" not in m["modules"]
    assert finance.net_worth(date.today())["zakat_due"] == 0
    monkeypatch.setattr(zakat, "holy_insights", lambda today=None: [{"level": "bilgi", "title": "Ramazan", "detail": "", "link": "zekat"}])
    assert not [i for i in finance.insights(finance.current_period()) if i.get("link") == "zekat"]
    prefs.set_modules({"zekat": True})
    assert [i for i in finance.insights(finance.current_period()) if i.get("link") == "zekat"]


def test_ui_prefs_per_user(client, tmpdb):
    r = client.put("/api/me/ui", json={"simple": True, "layout": ["kpi", "trend", "odemeler"]}).json()
    assert r == {"simple": True, "layout": ["kpi", "trend", "odemeler"]}
    assert client.get("/api/meta").json()["ui"]["simple"] is True
    assert client.put("/api/me/ui", json={"reset_layout": True}).json()["layout"] is None
