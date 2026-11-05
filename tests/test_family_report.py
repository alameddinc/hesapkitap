"""Aile'ye özel: "Kim ne harcadı" (GET /api/book/member-spend), ortak bütçede kişi başı kırılım, bütçe bildiriminin
defterdeki herkese gitmesi. Kişiler, kartlar ve tutarlar uydurma."""
from datetime import date

from app import money
from tests.test_book import _member, _setup_book, owner  # noqa: F401 - fixture
from tests.test_plans import _user, hosted  # noqa: F401 - fixture
from tests.test_push import FCM_TOKEN, IOS_TOKEN, _at, keys  # noqa: F401 - fixture

MONTH = "2026-10"


def _entry(c, desc, amount, category="Market & Gıda", day="2026-10-05", headers=None):
    r = c.post("/api/entries", json={"entry_date": day, "description": desc, "amount": amount, "kind": "gider",
                                     "category": category, "payment": "nakit"}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _ids():
    from app import system

    return system.user_by_name("admin")["id"], system.user_by_name("esim")["id"]


def _month_of_cards(c):
    return next(r["month"] for r in c.get("/api/transactions").json() if r["source"] == "card")


def test_member_spend_per_person_matches_category_total(owner):  # noqa: F811
    _setup_book(owner)
    m = _member(owner)
    admin, esim = _ids()
    _entry(m, "Fırın", 120)
    _entry(m, "Kırtasiye", 80, category="Eğitim")
    out = owner.get("/api/book/member-spend", params={"month": MONTH}).json()
    assert out["shared"] is True and out["month"] == MONTH and out["me"] == admin
    by = {p["key"]: p for p in out["people"]}
    # sahibin iki elle kaydı (900 + 300), üyenin iki kaydı (120 + 80)
    assert money.eq(by[f"u{esim}"]["amount"], 200) and by[f"u{esim}"]["name"] == "esim" and not by[f"u{esim}"]["owner"]
    assert {c["name"]: c["amount"] for c in by[f"u{esim}"]["categories"]} == {"Market & Gıda": 120, "Eğitim": 80}
    assert by[f"u{admin}"]["owner"] and by[f"u{admin}"]["me"] and not by[f"u{esim}"]["me"]
    assert money.ge(by[f"u{admin}"]["amount"], 1200)
    # toplam = Özet'in kategori toplamı (bütçeyle aynı küme); paylar yüzde yüze yakın
    cats = owner.get("/api/summary", params={"month": MONTH}).json()["by_category"]
    assert money.eq(out["total"], sum(g["amount"] for g in cats))
    assert money.eq(sum(p["amount"] for p in out["people"]), out["total"])
    assert abs(sum(p["pct"] for p in out["people"]) - 100) < 0.5
    # üyenin kartı yok: ekstre satırları "Ortak" (en sonda)
    cm = _month_of_cards(owner)
    card_out = owner.get("/api/book/member-spend", params={"month": cm}).json()
    assert card_out["people"][-1]["key"] == "ortak" and card_out["people"][-1]["name"] == "Ortak"
    # İşlemler süzgeci: aynı kural
    rows = owner.get("/api/transactions", params={"month": MONTH, "who": f"u{esim}"}).json()
    assert sorted(r["description"] for r in rows) == ["Fırın", "Kırtasiye"]
    assert owner.get("/api/transactions", params={"who": "x1"}).status_code == 422
    assert owner.get("/api/book/member-spend", params={"month": "2026-1"}).status_code == 422


def test_card_txn_attributed_to_matching_entry_creator(owner):  # noqa: F811
    from app import db

    _setup_book(owner)
    m = _member(owner)
    _, esim = _ids()
    cm = _month_of_cards(owner)
    card = next(r for r in owner.get("/api/transactions", params={"month": cm}).json()
                if r["source"] == "card" and r["counts_as_spend"] and r["amount"] > 0 and not r.get("split_of"))
    eid = _entry(m, "Kartla market", card["amount"], day=card["date"][:10])
    with db.connect() as c:  # ekstreyle eşleşmiş elle kayıt (eşitleme bunu yapar)
        c.execute("UPDATE entries SET linked_txn_id = ?, payment = 'kredi_karti' WHERE id = ?", (card["id"], eid))
    out = owner.get("/api/book/member-spend", params={"month": cm}).json()
    mine = next(p for p in out["people"] if p["key"] == f"u{esim}")
    assert money.ge(mine["amount"], card["amount"])  # kart satırı üyeye; elle kayıt (linked) ikinci kez sayılmaz
    rows = owner.get("/api/transactions", params={"month": cm, "who": f"u{esim}"}).json()
    assert [r["uid"] for r in rows if r["source"] == "card"] == [card["uid"]]


def test_hidden_card_and_entry_stay_hidden_from_member(owner):  # noqa: F811
    sec, _ = _setup_book(owner)
    m = _member(owner)
    owner.patch(f"/api/entries/{sec}", json={"hidden": True})  # 900 TL'lik gizli kayıt
    ov = owner.get("/api/book").json()
    kuveyt = next(c["card_no"] for c in ov["cards"] if c["bank"] == "Kuveyt Türk")
    owner.put("/api/book/rules", json={"hidden_cards": [kuveyt]})
    for month in {r["month"] for r in owner.get("/api/transactions").json()}:
        mine = m.get("/api/book/member-spend", params={"month": month}).json()
        full = owner.get("/api/book/member-spend", params={"month": month}).json()
        hidden = sum(r["amount"] for r in owner.get("/api/transactions", params={"month": month}).json()
                     if r["counts_as_spend"] and (r.get("account") == kuveyt or r["description"] == "Sürpriz hediye"))
        assert money.eq(full["total"] - mine["total"], hidden), month
        visible = {r["uid"] for r in m.get("/api/transactions", params={"month": month}).json()}
        for p in mine["people"]:
            rows = m.get("/api/transactions", params={"month": month, "who": p["key"]}).json()
            assert all(r["uid"] in visible for r in rows)
            assert money.eq(sum(r["amount"] for r in rows), p["amount"])
    # İstatistik sayfası üyeye kapalıysa rapor da kapalı
    owner.put("/api/book/rules", json={"hidden_pages": ["istatistik"]})
    assert m.get("/api/book/member-spend").status_code == 403


def test_own_book_never_in_report(owner):  # noqa: F811
    m = _member(owner, role="viewer")
    own = {"X-HK-Book": "kendi"}
    _entry(m, "Kendi alışverişim", 5000, headers=own)
    _entry(owner, "Market", 300)
    out = owner.get("/api/book/member-spend", params={"month": MONTH}).json()
    assert money.eq(out["total"], 300)
    assert m.get("/api/book/member-spend", params={"month": MONTH}).json()["total"] == out["total"]
    assert m.get("/api/book/member-spend", params={"month": MONTH}, headers=own).status_code == 404


def test_single_person_book_has_no_split(owner):  # noqa: F811
    _entry(owner, "Market", 300)
    out = owner.get("/api/book/member-spend", params={"month": MONTH}).json()
    assert out["shared"] is False and out["people"] == []
    owner.put("/api/budgets", json={"category": "Market & Gıda", "monthly_limit": 1000})
    [b] = owner.get("/api/budgets", params={"month": MONTH}).json()["status"]
    assert "by_person" not in b  # tek kişilik (Premium) defterde kırılım yok


def test_budget_split_per_person(owner):  # noqa: F811
    m = _member(owner)
    admin, esim = _ids()
    owner.put("/api/budgets", json={"category": "Market & Gıda", "monthly_limit": 1000})
    owner.put("/api/budgets", json={"category": "Eğitim", "monthly_limit": 500})
    _entry(owner, "Market", 300)
    _entry(m, "Fırın", 150)
    _entry(m, "Manav", 50)
    st = {b["category"]: b for b in m.get("/api/budgets", params={"month": MONTH}).json()["status"]}
    assert [(p["key"], p["name"], p["amount"]) for p in st["Market & Gıda"]["by_person"]] == \
        [(f"u{admin}", "admin", 300), (f"u{esim}", "esim", 200)]
    assert money.eq(sum(p["amount"] for p in st["Market & Gıda"]["by_person"]), st["Market & Gıda"]["spent"])
    assert st["Eğitim"]["by_person"] == []


def test_premium_locked_family_open(hosted):  # noqa: F811
    from app import system

    c, u = _user(hosted)
    system.update_user(u["id"], plan="premium")
    r = c.get("/api/book/member-spend")
    assert r.status_code == 402 and r.json()["plan_required"] == "aile_rapor"
    meta = c.get("/api/meta").json()["plan"]
    assert meta["features"]["aile_rapor"] is False
    assert next(x for x in meta["table"] if x["key"] == "aile_rapor")["tier"] == "aile"
    system.update_user(u["id"], plan="aile")
    assert c.get("/api/meta").json()["plan"]["features"]["aile_rapor"] is True
    out = c.get("/api/book/member-spend").json()
    assert out["shared"] is False and out["people"] == []  # henüz üye yok


def test_budget_push_reaches_every_member(owner, keys):  # noqa: F811
    from app import db, push, system

    m = _member(owner)
    admin, me = system.user_by_name("admin"), system.user_by_name("esim")
    push.register(admin["id"], FCM_TOKEN, "android")
    push.register(me["id"], IOS_TOKEN, "ios")
    owner.put("/api/budgets", json={"category": "Market & Gıda", "monthly_limit": 1000})
    _entry(owner, "Market", 700, day="2026-11-02")
    _entry(m, "Fırın", 150, day="2026-11-02")
    push.run_daily(_at("2026-11-03T09:30"))
    got = {}
    for u in (admin, me):
        [b] = [q["payload"] for q in push.queue(u["id"]) if q["payload"]["kind"] == "butce"]
        got[u["username"]] = b
    assert got["admin"]["id"] == got["esim"]["id"] and got["admin"]["id"].endswith(":80")
    assert "Sen ₺700" in got["admin"]["body"] and "esim ₺150" in got["admin"]["body"]
    assert "Sen ₺150" in got["esim"]["body"] and "admin ₺700" in got["esim"]["body"]
    # üye kendi tercihinde bütçeyi kapatırsa ona gitmez (sahibe yine gider)
    push.set_prefs(me["id"], {"types": {"butce": False}})
    with db.use_space(system.user_db_path(me), me):
        assert "butce" not in {i["kind"] for i in push.collect(me, date(2026, 11, 3))}
    with db.use_space(system.user_db_path(admin), admin):
        assert "butce" in {i["kind"] for i in push.collect(admin, date(2026, 11, 3))}
