"""Günlük hatırlatma saati (tercih "daily_at", app/push.py run_daily): varsayılan 09:00, 06:00–21:00 arası seçilir, günlük
iş kişinin saatinden önce o kişiye çalışmaz, sessiz saat yine geçerli. Kişiler, kartlar ve tutarlar uydurma."""
from tests.test_book import owner  # noqa: F401 - fixture
from tests.test_push import FCM_TOKEN, IOS_TOKEN, _at, _user, keys, nokeys  # noqa: F401 - fixture


def test_daily_at_default_and_put(owner, nokeys):  # noqa: F811
    p = owner.get("/api/push/prefs").json()
    assert p["daily_at"] == "09:00"
    r = owner.put("/api/push/prefs", json={"daily_at": "07:30"}).json()
    assert r["daily_at"] == "07:30" and r["quiet"] == {"from": "22:00", "to": "09:00"} and r["digest"] is True
    for bad in ("05:59", "21:01", "7:30", "25:00", ""):
        assert owner.put("/api/push/prefs", json={"daily_at": bad}).status_code == 422, bad
    assert owner.get("/api/push/prefs").json()["daily_at"] == "07:30"
    # başka alan güncellenince saat korunur
    assert owner.put("/api/push/prefs", json={"digest": False}).json()["daily_at"] == "07:30"


def test_bad_stored_value_falls_back():
    import json

    from app import push, system

    u = _user()
    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO push_prefs (user_id, json) VALUES (?, ?)",
                  (u["id"], json.dumps({"daily_at": "03:00"})))
    assert push.get_prefs(u["id"])["daily_at"] == "09:00"


def test_run_daily_respects_each_users_hour(keys, monkeypatch):  # noqa: F811
    from app import push

    a, b = _user("erken"), _user("gec")
    push.register(a["id"], FCM_TOKEN, "android")
    push.register(b["id"], IOS_TOKEN, "ios")
    push.set_prefs(a["id"], {"daily_at": "07:00"})
    push.set_prefs(b["id"], {"daily_at": "18:15"})
    seen: list[int] = []
    monkeypatch.setattr(push, "collect", lambda u, today, prefs=None: seen.append(u["id"]) or [])

    assert push.run_daily(_at("2026-11-02T05:59")) == {}  # aralığın başından önce kimse
    assert push.run_daily(_at("2026-11-02T07:00")) == {a["id"]: 0}
    assert push.run_daily(_at("2026-11-02T12:00")) == {}  # erken kişiye günde bir; geç kişinin saati gelmedi
    assert push.run_daily(_at("2026-11-02T18:15")) == {b["id"]: 0}
    assert push.run_daily(_at("2026-11-02T20:00")) == {}
    assert seen == [a["id"], b["id"]]
    assert push.run_daily(_at("2026-11-03T07:01")) == {a["id"]: 0}  # ertesi gün yeniden


def test_early_daily_waits_for_quiet_end(keys):  # noqa: F811
    from app import push

    u = _user()
    push.register(u["id"], FCM_TOKEN, "android")
    push.set_prefs(u["id"], {"daily_at": "07:00"})  # sessiz saat 22–09 sürerken
    p = push.get_prefs(u["id"])
    push.enqueue(u["id"], push.item("odeme", "odeme:x", "Son ödeme yarın · ••0000", "Örnek Banka · ₺100.", "#/diger/odemeler"),
                 _at("2026-11-02T07:00"), p)
    assert push.flush(_at("2026-11-02T08:59")) == 0
    assert push.flush(_at("2026-11-02T09:00")) == 1
