"""Ölçüm (app/funnel.py): aktif gün, kohort matematiği, olay beyaz listesi / sınır, mağaza olayları, yalnız yönetici,
ortak defter çift sayılmaz. Bütün e-postalar ve kimlikler uydurmadır."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import funnel

CONSENT = ["kosullar", "acik_riza"]
IOS = {"x-hk-client": "mobile", "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile"}
ANDROID = {"x-hk-client": "mobile", "user-agent": "Mozilla/5.0 (Linux; Android 15; Pixel) Mobile"}


@pytest.fixture(autouse=True)
def _fresh_memory():
    funnel._reset_memory()
    funnel._started_in.clear()
    yield
    funnel._reset_memory()


@pytest.fixture()
def hosted(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH", "Bearer rc-gizli")
    from app.api import main

    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _user(admin, name="deneme1", headers=None):
    from app import system
    from app.api.main import app

    inv = admin.post("/api/admin/invites", json={}).json()
    c = TestClient(app, headers=headers or {})
    r = c.post("/api/auth/register", json={"consent": CONSENT, "code": inv["code"], "username": f"{name}@ornek.com",
                                           "password": f"{name}-sifre-12345"})
    assert r.status_code == 200, r.text
    if headers and headers.get("x-hk-client") == "mobile":  # mobil: oturum anahtarı başlıkta
        c.headers["Authorization"] = f"Bearer {r.headers['X-Session-Token']}"
    return c, next(u for u in system.users() if u["username"] == f"{name}@ornek.com")


def _rc(event: dict):
    from app.api.main import app

    return TestClient(app).post("/api/billing/revenuecat", headers={"Authorization": "Bearer rc-gizli"},
                                json={"event": event})


def _activity(uid=None):
    from app import system

    with system.connect() as c:
        q = "SELECT user_id, day, platform, writes FROM user_activity" + (" WHERE user_id = ?" if uid else "")
        return [tuple(r) for r in c.execute(q, (uid,) if uid else ())]


# ---------------------------------------------------------------- aktif gün

def test_activity_one_row_per_day_platform_and_capped_writes(hosted):
    c, u = _user(hosted, headers=IOS)
    assert _activity(u["id"]) == []  # kayıt / oturum uçları aktif gün sayılmaz
    c.get("/api/auth/status")
    c.post("/api/events", json={"name": "plans_open"})
    assert _activity(u["id"]) == []
    for _ in range(3):
        assert c.get("/api/summary").status_code == 200
    rows = _activity(u["id"])
    assert rows == [(u["id"], funnel.today().isoformat(), "ios", 0)]
    for i in range(7):
        assert c.post("/api/entries", json={"entry_date": date.today().isoformat(), "description": f"Kayıt {i}",
                                            "amount": 10, "kind": "gider", "payment": "nakit"}).status_code == 200
    assert _activity(u["id"])[0][3] == funnel.WRITE_CAP  # en çok 5 sayılır
    # bellek sıfırlansa (süreç yeniden başladı) da satır tekrarlanmaz, sınır aşılmaz
    funnel._reset_memory()
    c.post("/api/entries", json={"entry_date": date.today().isoformat(), "description": "x", "amount": 1, "kind": "gider",
                                 "payment": "nakit"})
    assert len(_activity(u["id"])) == 1 and _activity(u["id"])[0][3] == funnel.WRITE_CAP
    from app import system

    assert system.get(funnel.STARTED_KEY) == funnel.today().isoformat()


def test_platform_detection():
    assert funnel.platform_of(IOS) == "ios" and funnel.platform_of(ANDROID) == "android"
    assert funnel.platform_of({"user-agent": "Mozilla/5.0 (iPhone) Safari"}) == "web"  # telefon tarayıcısı: web
    assert funnel.platform_of({"x-hk-client": "mobile", "user-agent": "?"}) == "mobil"


def test_self_hosted_records_nothing(client, monkeypatch):
    monkeypatch.delenv("HESAPKITAP_HOSTED", raising=False)
    monkeypatch.delenv("HESAPKITAP_FUNNEL", raising=False)
    funnel.touch({"id": 5}, "GET", "/api/summary", 200, {})
    assert _activity() == []
    assert funnel.record_event({"id": 5}, "paywall_view", "web") is False


def test_demo_user_not_counted(hosted):
    funnel.touch({"id": 0, "demo": True}, "GET", "/api/summary", 200, {})
    assert funnel.record_event({"id": 0, "demo": True}, "paywall_view", "web") is False
    assert _activity() == []


def test_deleted_user_activity_removed(hosted):
    from app import system

    c, u = _user(hosted)
    c.get("/api/summary")
    assert _activity(u["id"])
    system.delete_user(u["id"])
    assert _activity(u["id"]) == []


# ---------------------------------------------------------------- olaylar

def test_event_whitelist_aggregate_only_and_rate_limit(hosted, monkeypatch):
    from app import system

    c, u = _user(hosted, headers=ANDROID)
    assert c.post("/api/events", json={"name": "rastgele"}).status_code == 422
    for _ in range(3):
        r = c.post("/api/events", json={"name": "paywall_view"})
        assert r.status_code == 200 and r.json()["counted"] is True
    with system.connect() as cx:
        rows = [dict(r) for r in cx.execute("SELECT * FROM funnel_events")]
    assert rows == [{"day": funnel.today().isoformat(), "event": "paywall_view", "platform": "android",
                     "plan_state": "free", "n": 3, "users": 1}]  # kişi kimliği yok; kişi bir kez
    assert set(rows[0]) == {"day", "event", "platform", "plan_state", "n", "users"}  # kişi sütunu yok
    monkeypatch.setattr(funnel, "EVENT_HOURLY", 5)
    codes = [c.post("/api/events", json={"name": "plans_open"}).status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and 429 in codes
    # günlük olay sınırı: sayılmaz ama hata da değil
    funnel._reset_memory()
    monkeypatch.setattr(funnel, "EVENT_HOURLY", 1000)
    monkeypatch.setattr(funnel, "EVENT_DAILY_CAP", 2)
    out = [c.post("/api/events", json={"name": "purchase_start"}).json()["counted"] for _ in range(3)]
    assert out == [True, True, False]


def test_event_requires_login(hosted):
    from app.api.main import app

    assert TestClient(app).post("/api/events", json={"name": "paywall_view"}).status_code == 401


def test_event_plan_state_trial(hosted):
    from app import system

    c, u = _user(hosted, headers=IOS)
    _rc({"type": "INITIAL_PURCHASE", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
         "period_type": "TRIAL", "product_id": "premium_monthly", "expiration_at_ms": 4102444800000})
    c.post("/api/events", json={"name": "plans_open"})
    with system.connect() as cx:
        assert tuple(cx.execute("SELECT plan_state, platform FROM funnel_events").fetchone()) == ("trial", "ios")


# ---------------------------------------------------------------- mağaza

def test_store_events_kinds_dedupe_sandbox(hosted):
    _c, u = _user(hosted)
    uid = str(u["id"])
    base = {"app_user_id": uid, "entitlement_ids": ["aile"], "product_id": "aile_yearly", "store": "PLAY_STORE",
            "expiration_at_ms": 4102444800000, "environment": "PRODUCTION"}
    _rc({**base, "id": "e1", "type": "INITIAL_PURCHASE", "period_type": "TRIAL"})
    _rc({**base, "id": "e1", "type": "INITIAL_PURCHASE", "period_type": "TRIAL"})  # webhook tekrarı
    _rc({**base, "id": "e2", "type": "RENEWAL", "period_type": "NORMAL", "is_trial_conversion": True})
    _rc({**base, "id": "e3", "type": "CANCELLATION", "period_type": "NORMAL", "cancel_reason": "CUSTOMER_SUPPORT"})
    _rc({**base, "id": "e4", "type": "CANCELLATION", "period_type": "NORMAL", "cancel_reason": "UNSUBSCRIBE"})
    _rc({**base, "id": "e5", "type": "INITIAL_PURCHASE", "period_type": "NORMAL", "environment": "SANDBOX"})
    _rc({"id": "e6", "type": "INITIAL_PURCHASE", "app_user_id": "$RCAnonymousID:abc", "product_id": "premium_monthly"})
    r = funnel.report()
    st = r["total"]["store"]
    assert (st["trial_start"], st["trial_convert"], st["refund"], st["cancel"], st["purchase"]) == (1, 1, 1, 1, 0)
    assert r["store_sandbox"] == 1  # test satın alımı toplamlara girmez
    assert r["total"]["store_split"]["trial_start"] == {"aile·yearly": 1}
    assert r["weeks"][-1]["store"]["trial_start"] == 1
    assert funnel.report(platform="ios")["total"]["store"]["trial_start"] == 0  # Play = android
    assert funnel.report(platform="android")["total"]["store"]["trial_start"] == 1


def test_mrr_counts_only_own_paid_subscriptions_shared_book_not_double(hosted):
    """Aile sahibi öder; ortak defter üyesi sahibin paketini kullanır ama MRR'ye ikinci kez girmez. Deneme ve sandbox
    gelir sayılmaz; yöneticinin elle verdiği paket de."""
    from app import plans, system

    owner_c, owner = _user(hosted, "sahip1", headers=IOS)
    _rc({"id": "o1", "type": "INITIAL_PURCHASE", "app_user_id": str(owner["id"]), "entitlement_ids": ["aile"],
         "product_id": "aile_monthly", "store": "APP_STORE", "period_type": "NORMAL", "expiration_at_ms": 4102444800000})
    inv = owner_c.post("/api/book/invites", json={"note": "Üye"}).json()["code"]
    from app.api.main import app

    m = TestClient(app, headers=IOS)
    r = m.post("/api/auth/register", json={"consent": CONSENT, "code": inv, "username": "uye1@ornek.com",
                                           "password": "uye1-sifre-12345"})
    m.headers["Authorization"] = f"Bearer {r.headers['X-Session-Token']}"
    member = system.user_by_name("uye1@ornek.com")
    assert plans.plan_of(member) == "aile"
    # deneme ve sandbox
    _t, tu = _user(hosted, "deneme2")
    _rc({"id": "t1", "type": "INITIAL_PURCHASE", "app_user_id": str(tu["id"]), "entitlement_ids": ["premium"],
         "product_id": "premium_yearly", "store": "APP_STORE", "period_type": "TRIAL", "expiration_at_ms": 4102444800000})
    _s, su = _user(hosted, "sandbox1")
    _rc({"id": "s1", "type": "INITIAL_PURCHASE", "app_user_id": str(su["id"]), "entitlement_ids": ["premium"],
         "product_id": "premium_monthly", "store": "APP_STORE", "environment": "SANDBOX", "period_type": "NORMAL",
         "expiration_at_ms": 4102444800000})
    _g, gu = _user(hosted, "elle1")
    system.update_user(gu["id"], plan="premium", plan_source="manual")
    subs = funnel.report()["subs"]
    assert subs["active"] == {"premium_monthly": 0, "premium_yearly": 0, "aile_monthly": 1, "aile_yearly": 0}
    assert subs["paying"] == 1 and subs["trials"] == {"premium": 1, "aile": 0}
    assert subs["mrr_gross"] == 149.99
    assert subs["mrr_net"] == round(149.99 / 1.2 * 0.85, 2)
    # üye ve sahip ayrı kişi: ikisinin de aktif günü kendi satırında; üyenin ortak defterdeki isteği sahibe yazılmaz
    with system.connect() as cx:  # sahibin davet yazması onun aktif günüydü; temiz başla
        cx.execute("DELETE FROM user_activity")
    funnel._reset_memory()
    m.get("/api/summary")
    assert len(_activity(member["id"])) == 1 and _activity(owner["id"]) == []
    owner_c.get("/api/summary")
    assert len(_activity(owner["id"])) == 1


def test_prices_override(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_STORE_PRICES", '{"premium_monthly": 99.5, "bilinmeyen": 1}')
    p = funnel.prices()
    assert p["premium_monthly"] == 99.5 and "bilinmeyen" not in p and p["aile_yearly"] == 1199.99
    monkeypatch.setenv("HESAPKITAP_STORE_PRICES", "bozuk")
    assert funnel.prices() == funnel.DEFAULT_PRICES


# ---------------------------------------------------------------- kohort matematiği

def _seed_user(c, uid, created: date, platform="web", active=(), writes=None, reads=()):
    c.execute("INSERT INTO users (id, username, role, created_at) VALUES (?, ?, 'user', ?)",
              (uid, f"k{uid}@ornek.com", f"{created.isoformat()} 09:00:00"))
    for k in sorted(set(active) | {0} if active is not None else ()):
        d = created + timedelta(days=k)
        c.execute("INSERT INTO user_activity (user_id, day, platform, writes) VALUES (?, ?, ?, ?)",
                  (uid, d.isoformat(), platform, (writes or {}).get(k, 0)))
    for k in reads:
        c.execute("INSERT INTO read_daily (user_id, day, count) VALUES (?, ?, 1)", (uid, (created + timedelta(days=k)).isoformat()))


def test_cohort_activation_and_retention_math(hosted):
    from app import system

    today = date(2026, 11, 9)  # pazartesi
    started = date(2026, 9, 1)
    system.put(funnel.STARTED_KEY, started.isoformat())
    with system.connect() as c:
        c.execute("DELETE FROM users WHERE role <> 'admin'")
        # 5 hafta önce açılan dört hesap (aynı hafta): tüm pencereler dolmuş
        w = date(2026, 10, 5)  # pazartesi
        _seed_user(c, 101, w, "ios", active=(0, 8, 22), reads=(2,))           # belge + 2. ve 4. hafta
        _seed_user(c, 102, w, "ios", active=(0, 1, 2), writes={0: 3, 1: 2})   # 5 kayıt, geri gelmedi
        _seed_user(c, 103, w + timedelta(days=1), "android", active=(0, 10), writes={0: 4})  # 4 kayıt: aktif değil
        _seed_user(c, 104, w + timedelta(days=2), "web", active=(0, 7, 8), reads=(9,))  # 7. günden sonra okuma: sayılmaz
        # ölçüm başlamadan açılmış hesap: oranlara girmez, hesap sayısına girer
        _seed_user(c, 105, date(2026, 8, 25), "web", active=(0, 8))
        # bu hafta açılan: aktivasyon penceresi dolmadı
        _seed_user(c, 106, today, "web", active=(0,), writes={0: 5})
        # hiç aktif olmayan (platform bilinmiyor)
        _seed_user(c, 107, w, None, active=None)
    r = funnel.report(weeks=12, now=today)
    wk = next(x for x in r["weeks"] if x["start"] == w.isoformat())["cohort"]
    assert wk["signups"] == 5 and (wk["ios"], wk["android"], wk["web"], wk["bilinmiyor"]) == (2, 1, 1, 1)
    assert (wk["act_n"], wk["act_doc"], wk["act_rec"], wk["act_any"]) == (5, 1, 1, 2)
    assert wk["act_any_pct"] == 40.0
    assert (wk["w2_n"], wk["w2"]) == (5, 3)   # 101 (8. gün), 103 (10. gün), 104 (7–8. gün)
    assert (wk["w4_n"], wk["w4"]) == (5, 1)   # 101 (22. gün)
    last = r["weeks"][-1]
    assert last["start"] == today.isoformat() and last["partial"]
    assert last["cohort"]["signups"] == 1 and last["cohort"]["act_n"] == 0 and last["cohort"]["w2_pct"] is None
    tot = r["total"]["cohort"]
    assert tot["signups"] == 7 and tot["act_n"] == 5  # 105 ölçümden önce, 106 penceresi dolmadı
    # platform süzgeci: yalnız iOS kohortu
    ios = funnel.report(weeks=12, now=today, platform="ios")["total"]["cohort"]
    assert (ios["signups"], ios["act_any"], ios["w2"], ios["w4"]) == (2, 2, 1, 1)
    assert len(r["weeks"]) == 12 and r["weeks"][0]["start"] == (today - timedelta(weeks=11)).isoformat()


def test_retention_window_must_be_complete(hosted):
    from app import system

    today = date(2026, 11, 9)
    system.put(funnel.STARTED_KEY, "2026-01-01")
    with system.connect() as c:
        c.execute("DELETE FROM users WHERE role <> 'admin'")
        _seed_user(c, 201, today - timedelta(days=14), "web", active=(0, 8))  # 2. hafta bitti, 4. hafta bitmedi
    t = funnel.report(now=today)["total"]["cohort"]
    assert (t["w2_n"], t["w2"], t["w4_n"], t["w4_pct"]) == (1, 1, 0, None)


def test_demo_weeks_and_signup_demo(hosted):
    from app import system

    today = date(2026, 11, 9)
    with system.connect() as c:
        c.execute("INSERT INTO funnel_events VALUES ('2026-11-09', 'signup_demo', 'ios', 'free', 1, 1)")
        c.execute("INSERT INTO funnel_events VALUES ('2026-11-03', 'signup_demo', 'web', 'free', 2, 2)")
    demo = {"2026-11-09": {"oturum": 10, "ziyaretçi": 8, "platform": {"web": 6, "ios": 3, "android": 1}},
            "2026-11-04": {"oturum": 4, "ziyaretçi": 4, "platform": {"web": 4, "ios": 0, "android": 0}},
            "2020-01-01": {"oturum": 99, "ziyaretçi": 99}}
    r = funnel.report(now=today, demo=demo)
    assert r["weeks"][-1]["demo"] == {"sessions": 10, "visitors": 8} and r["weeks"][-1]["signup_demo"] == 1
    assert r["weeks"][-2]["demo"]["sessions"] == 4 and r["weeks"][-2]["signup_demo"] == 2
    assert r["total"]["signup_demo"] == 3
    ios = funnel.report(now=today, demo=demo, platform="ios")
    assert ios["weeks"][-1]["demo"]["sessions"] == 3 and ios["total"]["signup_demo"] == 1
    # eski demo sunucusu platform vermiyor: platform süzgecinde demo yok (yanlış sayı yerine boş)
    old = {"2026-11-09": {"oturum": 10, "ziyaretçi": 8}}
    assert funnel.report(now=today, demo=old, platform="ios")["demo_available"] is False
    assert funnel.report(now=today, demo=None)["demo_available"] is False


def test_paywall_funnel_weekly(hosted):
    from app import system

    today = date(2026, 11, 9)
    with system.connect() as c:
        for ev, n, us in (("paywall_view", 9, 5), ("plans_open", 4, 3), ("purchase_start", 2, 2), ("purchase_success", 1, 1)):
            c.execute("INSERT INTO funnel_events VALUES ('2026-11-09', ?, 'android', 'free', ?, ?)", (ev, n, us))
        c.execute("INSERT INTO funnel_events VALUES ('2026-11-09', 'paywall_view', 'ios', 'trial', 1, 1)")
    r = funnel.report(now=today)
    pw = r["weeks"][-1]["paywall"]
    assert pw["paywall_view"] == {"users": 6, "n": 10} and pw["purchase_success"]["users"] == 1
    assert r["total"]["paywall_by_state"]["trial"]["paywall_view"] == 1
    assert funnel.report(now=today, platform="android")["weeks"][-1]["paywall"]["paywall_view"]["users"] == 5


# ---------------------------------------------------------------- erişim, bakım

def test_admin_only(hosted):
    c, _u = _user(hosted)
    assert c.get("/api/admin/funnel").status_code == 403
    r = hosted.get("/api/admin/funnel?platform=ios&weeks=4")
    assert r.status_code == 200
    j = r.json()
    assert j["platform"] == "ios" and len(j["weeks"]) == 4 and j["enabled"] is True and j["demo_configured"] is False
    assert "aktivasyon" in j["definitions"]


def test_prune(hosted):
    from app import system

    today = date(2026, 11, 9)
    with system.connect() as c:
        c.execute("INSERT INTO user_activity VALUES (1, ?, 'web', 0)", ((today - timedelta(days=401)).isoformat(),))
        c.execute("INSERT INTO user_activity VALUES (1, ?, 'web', 0)", ((today - timedelta(days=399)).isoformat(),))
        c.execute("INSERT INTO funnel_events VALUES (?, 'paywall_view', 'web', 'free', 1, 1)",
                  ((today - timedelta(days=1200)).isoformat(),))
        c.execute("INSERT INTO store_event_seen VALUES ('x', ?)", ((today - timedelta(days=60)).isoformat(),))
    out = funnel.prune(today)
    assert out["activity"] == 1 and out["events"] == 1
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM user_activity").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM store_event_seen").fetchone()[0] == 0


def test_capability_advertised(hosted):
    assert "funnel_events" in hosted.get("/api/meta").json()["capabilities"]


# ---------------------------------------------------------------- Özet görünümü (Minimal / Ayrıntılı / Sade)

def test_gorunum_events_whitelisted_aggregate_and_admin_card(hosted):
    """Görünüm olayları beyaz listede, yalnız toplam; admin Ölçüm "Görünüm" kartı: görünüm başına kişi-gün ve elle
    değiştirme (nereden → nereye). Kişi kimliği yok."""
    from app import system

    c, _ = _user(hosted, headers=IOS)
    for name in ("ozet_gorunum_minimal", "ozet_gorunum_minimal", "ozet_degisti_minimal_ayrintili", "ozet_gorunum_ayrintili"):
        assert c.post("/api/events", json={"name": name}).json()["counted"] is True
    assert c.post("/api/events", json={"name": "ozet_degisti_minimal_minimal"}).status_code == 422  # aynı görünüm yok
    assert c.post("/api/events", json={"name": "ozet_gorunum_klasik"}).status_code == 422
    with system.connect() as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(funnel_events)")}
        rows = {r["event"]: (r["n"], r["users"]) for r in cx.execute("SELECT * FROM funnel_events")}
    assert "user_id" not in cols
    assert rows["ozet_gorunum_minimal"] == (2, 1) and rows["ozet_degisti_minimal_ayrintili"] == (1, 1)
    today = funnel.today()
    with system.connect() as cx:  # dün web'de iki kişi Ayrıntılı, biri Sade'ye geçti; 10 gün önce sayılmaz
        y = (today - timedelta(days=1)).isoformat()
        cx.execute("INSERT INTO funnel_events VALUES (?, 'ozet_gorunum_ayrintili', 'web', 'free', 2, 2)", (y,))
        cx.execute("INSERT INTO funnel_events VALUES (?, 'ozet_degisti_ayrintili_sade', 'web', 'free', 1, 1)", (y,))
        cx.execute("INSERT INTO funnel_events VALUES (?, 'ozet_gorunum_sade', 'web', 'free', 5, 5)",
                   ((today - timedelta(days=10)).isoformat(),))
    g = hosted.get("/api/admin/funnel").json()["gorunum"]
    assert g["days"] == 7
    assert g["views"]["minimal"] == {"user_days": 1, "daily_avg": 0.1}
    assert g["views"]["ayrintili"]["user_days"] == 3 and g["views"]["sade"]["user_days"] == 0
    assert g["changes"]["minimal>ayrintili"] == 1 and g["changes"]["ayrintili>sade"] == 1 and g["changes_total"] == 2
    w = hosted.get("/api/admin/funnel", params={"platform": "web"}).json()["gorunum"]
    assert w["views"]["minimal"]["user_days"] == 0 and w["views"]["ayrintili"]["user_days"] == 2
    # paket ekranı hunisi görünüm olaylarını saymaz
    assert set(hosted.get("/api/admin/funnel").json()["total"]["paywall"]) == {"paywall_view", "plans_open", "purchase_start",
                                                                               "purchase_success", "purchase_cancel"}
