"""Özet uyarıları bildirim gibi: her uyarının kararlı anahtarı (key) ve dokununca gidilecek hedefi (target) var;
kişi başına gizlenir (POST /api/insights/dismiss), "Geri al" ile döner (restore); koşul değişince yeniden çıkar."""
from tests.test_book import _member, owner  # noqa: F401 - fixture


def _month():
    from app import finance

    return finance.current_period()


def _entry(client, desc, amount, category, day="05"):
    m = _month()
    r = client.post("/api/entries", json={"entry_date": f"{m}-{day}", "description": desc, "amount": amount,
                                           "kind": "gider", "category": category, "payment": "nakit"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _insights(client):
    return client.get("/api/summary").json()["insights"]


def _by_title(client, start):
    return next((i for i in _insights(client) if i["title"].startswith(start)), None)


def test_every_insight_has_unique_key_and_targets(client):
    _entry(client, "Örnek Market", 120, "Kategorisiz")
    _entry(client, "Örnek Kafe", 1500, "Kafe & Restoran")
    client.put("/api/budgets", json={"category": "Kafe & Restoran", "monthly_limit": 1000})
    items = _insights(client)
    keys = [i["key"] for i in items]
    assert all(keys) and len(keys) == len(set(keys))
    unc = next(i for i in items if i["title"].endswith("işlem kategorisiz"))
    assert unc["key"].startswith(f"kategorisiz:{_month()}:")
    assert unc["target"] == {"screen": "islemler", "month": _month(), "title": unc["title"], "category": "Kategorisiz"}
    bud = next(i for i in items if "bütçesi aşıldı" in i["title"])
    assert bud["key"] == f"butce:kafe-&-restoran:{_month()}"
    assert bud["target"]["screen"] == "islemler" and bud["target"]["category"] == "Kafe & Restoran"
    inc = next(i for i in items if i["title"] == "Gelirin tanımlı değil")
    assert inc["key"] == "gelir_yok" and inc["target"] == {"screen": "planlama"}
    # web alanları olduğu gibi
    assert unc["filter"] == {"category": "Kategorisiz"} and inc["link"] == "plan"


def test_dismiss_restore_and_reappear_on_change(client):
    _entry(client, "Örnek Market", 120, "Kategorisiz")
    unc = _by_title(client, "1 işlem kategorisiz")
    assert client.post("/api/insights/dismiss", json={"key": unc["key"]}).json() == {"ok": True, "key": unc["key"]}
    s = client.get("/api/summary").json()
    assert not any(i["key"] == unc["key"] for i in s["insights"]) and s["insights_hidden"] == 1
    # Geri al
    assert client.post("/api/insights/restore", json={"key": unc["key"]}).json()["ok"]
    assert _by_title(client, "1 işlem kategorisiz")
    # yeniden gizle; yeni kategorisiz işlem gelince koşul değişti: yeni anahtarla yeniden çıkar
    client.post("/api/insights/dismiss", json={"key": unc["key"]})
    assert _by_title(client, "1 işlem kategorisiz") is None
    _entry(client, "Örnek Fırın", 40, "Kategorisiz", day="06")
    again = _by_title(client, "2 işlem kategorisiz")
    assert again and again["key"] != unc["key"]


def test_budget_key_stable_within_month(client):
    client.put("/api/budgets", json={"category": "Market & Gıda", "monthly_limit": 1000})
    _entry(client, "Örnek Market", 1200, "Market & Gıda")
    k = _by_title(client, "Market & Gıda bütçesi aşıldı")["key"]
    client.post("/api/insights/dismiss", json={"key": k})
    _entry(client, "Örnek Market", 300, "Market & Gıda", day="07")  # aynı ay daha da aştı: gizli kalır
    assert not any("Market & Gıda bütçesi" in i["title"] for i in _insights(client))


def test_bad_key_rejected(client):
    assert client.post("/api/insights/dismiss", json={"key": ""}).status_code == 422
    assert client.post("/api/insights/dismiss", json={"key": "x" * 201}).status_code == 422
    assert client.post("/api/insights/dismiss", json={"key": "a\nb"}).status_code == 422


def test_target_follows_modules(tmpdb):
    from datetime import date

    from app import finance, prefs

    today = date(2026, 10, 30)
    get = lambda: next(i for i in finance.insights("2026-10", today=today) if i["title"] == "Gelirin tanımlı değil")
    assert get()["target"] == {"screen": "planlama"}
    prefs.set_modules({"plan": False})
    i = get()
    assert "link" not in i and "target" not in i and i["key"] == "gelir_yok"


def test_target_fallbacks():
    from app import finance

    mods = {"plan": False}
    i = {"title": "T", "filter": {"kind": "faiz_ucret"}, "target": {"screen": "taksitler"}}
    finance._target(i, "2026-10", mods)  # Planlama kapalı: süzgece döner
    assert i["target"] == {"screen": "islemler", "month": "2026-10", "title": "T", "kind": "faiz_ucret"}
    i = {"title": "T", "link": "borclar"}
    finance._target(i, "2026-10", {}, {"borclar"})  # ortak defterde üyeye kapalı sayfa: hedef yok
    assert "target" not in i and i["key"].startswith("x:")
    i = {"title": "T", "link": "zekat"}
    finance._target(i, "2026-10", {})
    assert i["target"] == {"screen": "zekat"}


def test_member_dismiss_is_personal(owner):  # noqa: F811
    _entry(owner, "Örnek Market", 120, "Kategorisiz")
    v = _member(owner, name="cocuk", role="viewer")  # sadece görür: yine de kendi uyarısını gizleyebilir
    unc = _by_title(v, "1 işlem kategorisiz")
    assert unc
    assert v.post("/api/insights/dismiss", json={"key": unc["key"]}).status_code == 200
    assert _by_title(v, "1 işlem kategorisiz") is None
    assert _by_title(owner, "1 işlem kategorisiz")  # sahibinki açık


def test_duplicate_charge_targets_the_charges(tmpdb):
    from datetime import date

    from app import finance

    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end) VALUES ('d', 'Örnek Bank', '5400***1111', '2026-06-30')")
        sid = c.execute("SELECT id FROM statements").fetchone()[0]
        for d, amt in (("2026-06-15", 500.0), ("2026-06-19", 490.0)):
            c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
                      "VALUES (?, ?, 'ORNEK YAZILIM', ?, 'harcama', 'AI & Yazılım Abonelik')", (sid, d, amt))
        ids = [r[0] for r in c.execute("SELECT id FROM transactions ORDER BY id")]
    w = finance.duplicate_charges(date(2026, 7, 10))
    assert len(w) == 1 and w[0]["key"] == "dup:ornek-yazilim:2026-06" and w[0]["link"] == "tasarruf"
    assert w[0]["target"] == {"screen": "islemler", "month": "2026-06", "title": "Ornek Yazilim",
                              "uids": [f"c{i}" for i in ids]}
