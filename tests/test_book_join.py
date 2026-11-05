"""Ortak deftere var olan hesapla katılma / ayrılma (app/book_join.py, yetenek book_join). Veriler uydurmadır."""
import pytest
from fastapi.testclient import TestClient

CONSENT = ["kosullar", "acik_riza"]
OWN = {"X-HK-Book": "kendi"}


def _entry(desc, amount=45):
    return {"entry_date": "2026-10-05", "description": desc, "amount": amount, "kind": "gider",
            "category": "Market & Gıda", "payment": "nakit"}


@pytest.fixture()
def admin(client, monkeypatch):
    from app import auth
    from app.api import main

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


@pytest.fixture()
def hosted(admin, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH", "Bearer rc-gizli")
    return admin


def _account(admin, name):
    """Tek başına (kendi defteri olan) hesap: yönetici davetiyle kayıt."""
    from app import system
    from app.api.main import app

    inv = admin.post("/api/admin/invites", json={}).json()
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"consent": CONSENT, "code": inv["code"], "username": f"{name}@ornek.com",
                                           "password": f"{name}-sifre-12345"})
    assert r.status_code == 200, r.text
    return c, system.user_by_name(f"{name}@ornek.com")


def _rc(kind, uid, ents=("premium",), exp_ms=4102444800000):
    from app.api.main import app

    return TestClient(app).post("/api/billing/revenuecat", headers={"Authorization": "Bearer rc-gizli"},
                                json={"event": {"type": kind, "app_user_id": str(uid), "entitlement_ids": list(ents),
                                                "expiration_at_ms": exp_ms}})


def _invite_member(owner, name):
    """Davetle YENİ açılan üye (eski yol)."""
    from app.api.main import app

    inv = owner.post("/api/book/invites", json={"note": name}).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"consent": CONSENT, "code": inv["code"], "username": f"{name}@ornek.com",
                                              "password": f"{name}-sifre-12345"}).status_code == 200
    return m


def test_existing_account_joins_and_keeps_own_data_as_benim(admin):
    from app import system

    admin.post("/api/entries", json=_entry("Ortak market", 300))
    c, ayse = _account(admin, "ayse")
    assert c.post("/api/entries", json=_entry("Ayşe kitap", 120)).status_code == 200
    inv = admin.post("/api/book/invites", json={"note": "Ayşe", "book_role": "viewer"}).json()

    pv = c.get(f"/api/book/join/{inv['code']}").json()
    assert pv["valid"] and pv["can_join"] and pv["owner"] == "admin" and pv["role"] == "viewer"
    assert pv["role_label"] == "Sadece görür" and pv["own_subscription"] is False

    r = c.post(f"/api/book/join/{inv['code']}")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and r.json()["role"] == "viewer"
    me = c.get("/api/book/me").json()
    assert me["member"] and me["book"] == "ortak" and me["role"] == "viewer"
    # ortak: sahibin verisi; Benim: kendi eski verisi aynen
    assert [x["description"] for x in c.get("/api/transactions").json()] == ["Ortak market"]
    assert [x["description"] for x in c.get("/api/transactions", headers=OWN).json()] == ["Ayşe kitap"]
    assert c.post("/api/entries", json=_entry("x")).status_code == 403  # viewer: ortakta yazamaz
    assert c.post("/api/entries", json=_entry("Benim kayıt"), headers=OWN).status_code == 200
    assert not any(x["description"] == "Ayşe kitap" for x in admin.get("/api/transactions").json())  # sahip görmez
    assert [m["username"] for m in admin.get("/api/book").json()["members"]] == ["ayse@ornek.com"]
    assert system.invite(inv["code"])["used_by"] == ayse["id"]  # davet tüketildi
    assert c.post(f"/api/book/join/{inv['code']}").status_code in (409, 410)


def test_cannot_join_own_invite_or_twice(admin):
    inv = admin.post("/api/book/invites", json={}).json()
    pv = admin.get(f"/api/book/join/{inv['code']}").json()
    assert not pv["can_join"] and pv["reason"] == "own_invite"
    r = admin.post(f"/api/book/join/{inv['code']}")
    assert r.status_code == 409 and "kendi davetin" in r.json()["detail"]

    c, _ = _account(admin, "ayse")
    assert c.post(f"/api/book/join/{inv['code']}").status_code == 200
    again = admin.post("/api/book/invites", json={}).json()
    # üye ortak defterdeyken de önizleme/katıl uçlarına ulaşır (403 değil, anlaşılır 409)
    pv = c.get(f"/api/book/join/{again['code']}").json()
    assert pv["reason"] == "already_member"
    assert c.post(f"/api/book/join/{again['code']}").status_code == 409


def test_member_of_other_book_must_leave_first(admin):
    a, _ = _account(admin, "ayse")
    b, _ = _account(admin, "berk")
    assert a.post(f"/api/book/join/{admin.post('/api/book/invites', json={}).json()['code']}").status_code == 200
    binv = b.post("/api/book/invites", json={}).json()
    r = a.post(f"/api/book/join/{binv['code']}")
    assert r.status_code == 409 and "ayrıl" in r.json()["detail"]
    assert a.post("/api/book/leave").status_code == 200
    assert a.post(f"/api/book/join/{binv['code']}").status_code == 200
    assert a.get("/api/book/me").json()["owner"] == "berk@ornek.com"


def test_owner_with_members_cannot_join(admin):
    b, berk = _account(admin, "berk")
    _invite_member(b, "cem")
    pending = b.post("/api/book/invites", json={}).json()
    inv = admin.post("/api/book/invites", json={}).json()
    r = b.post(f"/api/book/join/{inv['code']}")
    assert r.status_code == 409 and "üyeleri çıkar" in r.json()["detail"]
    # üyesi yok, yalnız bekleyen daveti var: katılabilir; bekleyen davet geçersiz olur
    cem = next(m for m in b.get("/api/book").json()["members"])
    assert b.delete(f"/api/book/members/{cem['id']}").status_code == 200
    assert b.post(f"/api/book/join/{inv['code']}").status_code == 200
    from app import system

    assert system.invite(pending["code"]) is None


def test_unknown_and_account_invites(admin):
    c, _ = _account(admin, "ayse")
    pv = c.get("/api/book/join/AAAA-BBBB-CCCC").json()
    assert pv == {"valid": False, "can_join": False, "reason": "unknown", "message": pv["message"]}
    assert c.post("/api/book/join/AAAA-BBBB-CCCC").status_code == 404
    acc = admin.post("/api/admin/invites", json={}).json()  # hesap daveti (ortak defter değil)
    assert c.get(f"/api/book/join/{acc['code']}").json()["reason"] == "not_book"
    assert c.post(f"/api/book/join/{acc['code']}").status_code == 400


def _family(hosted):
    o, owner = _account(hosted, "sahip")
    _rc("INITIAL_PURCHASE", owner["id"], ents=("aile",))
    return o, owner


def _join(c, o):
    r = c.post(f"/api/book/join/{o.post('/api/book/invites', json={}).json()['code']}")
    assert r.status_code == 200, r.text
    return r.json()


def _premium_member(hosted, o, name):
    """Kendi Premium aboneliği olan (mağaza) var olan hesap, ortak deftere katılır."""
    c, u = _account(hosted, name)
    _rc("INITIAL_PURCHASE", u["id"])
    return c, u, _join(c, o)


def _seated(o, n):
    for i in range(n):
        _invite_member(o, f"uye{i}")


@pytest.fixture()
def pushes(monkeypatch):
    from app import push

    sent = []
    monkeypatch.setattr(push, "notify", lambda uid, items, *a, **k: sent.extend((uid, i) for i in items) or items)
    return sent


def test_owner_plan_expired_blocks_join(hosted):
    from app import system

    o, owner = _family(hosted)
    c, _ = _account(hosted, "ayse")
    inv = o.post("/api/book/invites", json={}).json()
    system.update_user(owner["id"], plan_until="2020-01-01T00:00:00+00:00")
    r = c.post(f"/api/book/join/{inv['code']}")
    assert r.status_code == 409 and "Aile paketi" in r.json()["detail"]
    assert c.get(f"/api/book/join/{inv['code']}").json()["reason"] == "owner_plan"


def test_own_premium_member_takes_no_seat(hosted):
    from app import plans, system

    o, owner = _family(hosted)
    _seated(o, 3)  # sahip + 3 koltuk dolu
    c, ayse, res = _premium_member(hosted, o, "ayse")
    assert res["own_subscription"] is True
    u = system.user(ayse["id"])
    assert plans.plan_of(u) == "premium" and plans.seat_state(u) == "own"  # kendi aboneliğiyle
    assert u["plan_source"] == "revenuecat"  # aboneliğe dokunulmadı
    ov = o.get("/api/book").json()
    assert ov["seats"] == {"total": 3, "used": 3, "members": 4, "max_members": 8, "waiting": 0}
    assert next(m for m in ov["members"] if m["id"] == ayse["id"])["seat"] == {"state": "own"}
    assert c.get("/api/book/me").json()["seat"] == {"state": "own"}
    # kendi aboneliği olmayan: koltuk yok
    f, _ = _account(hosted, "fatma")
    inv = o.post("/api/book/invites", json={}).json()
    pv = f.get(f"/api/book/join/{inv['code']}").json()
    assert pv["can_join"] is False and pv["reason"] == "full"
    r = f.post(f"/api/book/join/{inv['code']}")
    assert r.status_code == 409 and "koltukları dolu" in r.json()["detail"]


def test_max_eight_members(hosted):
    from app import system

    o, owner = _family(hosted)
    _seated(o, 3)
    for i in range(5):
        _premium_member(hosted, o, f"prem{i}")
    assert len(system.members(owner["id"])) == 8
    assert o.post("/api/book/invites", json={}).status_code == 409  # davet de açılmaz
    c, u = _account(hosted, "dokuz")
    _rc("INITIAL_PURCHASE", u["id"])
    late = system.create_invite(member_of=owner["id"])
    r = c.post(f"/api/book/join/{late['code']}")
    assert r.status_code == 409 and "en çok 8" in r.json()["detail"]


def test_lapse_with_free_seat_moves_into_seat(hosted, pushes):
    from app import plans, system

    o, owner = _family(hosted)
    _seated(o, 1)
    c, ayse, _ = _premium_member(hosted, o, "ayse")
    _rc("EXPIRATION", ayse["id"], ents=())
    u = system.user(ayse["id"])
    assert plans.seat_state(u) == "seat" and plans.plan_of(u) == "aile"  # kesintisiz
    assert pushes == []  # koltuk vardı: uyarı yok


def test_lapse_when_full_grace_then_free_access_continues(hosted, pushes):
    from app import plans, system

    o, owner = _family(hosted)
    o.post("/api/entries", json=_entry("Ortak market", 300))
    _seated(o, 3)
    c, ayse, _ = _premium_member(hosted, o, "ayse")
    c.post("/api/entries", json=_entry("Ayşe kitap"), headers=OWN)
    _rc("EXPIRATION", ayse["id"], ents=())
    u = system.user(ayse["id"])
    assert plans.seat_state(u) == "grace" and plans.plan_of(u) == "aile"  # 7 gün ek süre
    me = c.get("/api/book/me").json()["seat"]
    assert me["state"] == "grace" and me["grace_until"]
    # iki tarafa bildirim
    to = {uid: i for uid, i in pushes}
    assert set(to) == {ayse["id"], owner["id"]}
    assert to[ayse["id"]]["body"].startswith("Premium'un bitti, Aile koltukları dolu.")
    assert "aile yöneticisinden koltuk isteyebilirsin" in to[ayse["id"]]["body"]
    assert to[owner["id"]]["body"].startswith("ayse: Premium'u bitti, koltuklar dolu.")
    assert to[owner["id"]]["route"] == "#/diger/ortak-defter" and to[ayse["id"]]["kind"] == "aile"
    assert c.get("/api/prices").status_code == 200

    # ek süre bitti: Premium kapanır (kendi defterinde de), ortak defter erişimi sürer, üye atılmaz
    system.put(f"seat_queue:{ayse['id']}", "2020-01-01 00:00:00")
    u = system.user(ayse["id"])
    assert plans.seat_state(u) == "none" and plans.plan_of(u) == "free"
    assert c.get("/api/prices").status_code == 402
    assert c.get("/api/prices", headers=OWN).status_code == 402
    assert c.get("/api/meta").json()["plan"]["ai_docs"] == 3  # günde 3 okuma
    assert [x["description"] for x in c.get("/api/transactions").json()] == ["Ortak market"]
    assert [x["description"] for x in c.get("/api/transactions", headers=OWN).json()] == ["Ayşe kitap"]
    assert c.get("/api/book/me").json()["seat"]["state"] == "none"
    ov = o.get("/api/book").json()
    assert ov["seats"]["waiting"] == 1 and ayse["id"] in [m["id"] for m in ov["members"]]

    # koltuk boşalınca bekleyen kendiliğinden alınır (+ bildirim)
    pushes.clear()
    uye0 = system.user_by_name("uye0@ornek.com")
    assert o.delete(f"/api/book/members/{uye0['id']}").status_code == 200
    u = system.user(ayse["id"])
    assert plans.seat_state(u) == "seat" and plans.plan_of(u) == "aile"
    assert [(uid, i["title"]) for uid, i in pushes] == [(ayse["id"], "Aile koltuğuna geçtin")]
    assert c.get("/api/prices").status_code == 200


def test_owner_gives_seat(hosted, pushes):
    from app import plans, system

    o, owner = _family(hosted)
    _seated(o, 3)
    c, ayse, _ = _premium_member(hosted, o, "ayse")
    _rc("EXPIRATION", ayse["id"], ents=())
    system.put(f"seat_queue:{ayse['id']}", "2020-01-01 00:00:00")  # ek süre de bitti
    uye1 = system.user_by_name("uye1@ornek.com")
    pushes.clear()
    # koltuklu olmayandan koltuk alınamaz, koltuklu olana verilemez
    assert o.post(f"/api/book/members/{uye1['id']}/seat", json={"from_member": ayse["id"]}).status_code == 409
    r = o.post(f"/api/book/members/{ayse['id']}/seat", json={"from_member": uye1["id"]})
    assert r.status_code == 200 and r.json()["seats"]["waiting"] == 1
    assert plans.seat_state(system.user(ayse["id"])) == "seat"
    assert plans.seat_state(system.user(uye1["id"])) == "none"
    assert plans.plan_of(system.user(uye1["id"])) == "free"  # kendi aboneliği yok: ek süre de yok
    titles = {uid: i["title"] for uid, i in pushes}
    assert titles == {ayse["id"]: "Aile koltuğuna geçtin", uye1["id"]: "Aile koltuğun başka üyeye verildi"}
    states = {m["id"]: m["seat"]["state"] for m in o.get("/api/book").json()["members"]}
    assert states[ayse["id"]] == "seat" and states[uye1["id"]] == "none"
    # üye koltuk dağıtamaz
    assert c.post(f"/api/book/members/{uye1['id']}/seat", json={"from_member": ayse["id"]}).status_code == 403


def test_plan_until_lapse_without_webhook_daily_notice(hosted):
    from datetime import datetime, timedelta, timezone

    from app import plans, push, system

    o, owner = _family(hosted)
    _seated(o, 3)
    c, ayse, _ = _premium_member(hosted, o, "ayse")
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    system.update_user(ayse["id"], plan_until=past)  # webhook gelmedi, süre geçti
    u = system.user(ayse["id"])
    assert plans.seat_state(u) == "grace" and plans.plan_of(u) == "aile"
    mine = push._aile(u)
    assert len(mine) == 1 and mine[0]["kind"] == "aile"
    theirs = push._aile(system.user(owner["id"]))
    assert len(theirs) == 1 and theirs[0]["id"] == mine[0]["id"]
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds")
    system.update_user(ayse["id"], plan_until=old)
    assert plans.plan_of(system.user(ayse["id"])) == "free"


def test_free_user_can_join_family_book(hosted):
    """Katılan çoğu zaman ücretsiz pakette: katıl ucu paket kapısına (ortak_defter) takılmaz."""
    from app import plans, system

    o, owner = _account(hosted, "sahip")
    _rc("INITIAL_PURCHASE", owner["id"], ents=("aile",))
    c, ayse = _account(hosted, "ayse")
    inv = o.post("/api/book/invites", json={}).json()
    assert c.get(f"/api/book/join/{inv['code']}").status_code == 200
    r = c.post(f"/api/book/join/{inv['code']}")
    assert r.status_code == 200 and r.json()["own_subscription"] is False
    assert plans.plan_of(system.user(ayse["id"])) == "aile"
    assert c.get("/api/prices").status_code == 200  # Premium özelliği açık


def test_leave_keeps_benim_and_cuts_shared_access(admin):
    from app import system

    admin.post("/api/entries", json=_entry("Ortak market", 300))
    c, ayse = _account(admin, "ayse")
    c.post("/api/entries", json=_entry("Ayşe kitap"))
    inv = admin.post("/api/book/invites", json={"book_role": "viewer"}).json()
    c.post(f"/api/book/join/{inv['code']}")
    uid = ayse["id"]
    admin.put(f"/api/book/rules?member={uid}", json={"hide_fees": True})
    assert c.post("/api/book/leave").status_code == 200  # viewer da ayrılabilir
    assert c.get("/api/book/me").json()["member"] is False
    assert [x["description"] for x in c.get("/api/transactions").json()] == ["Ayşe kitap"]
    assert admin.get("/api/book").json()["members"] == []
    assert system.user(uid)["db_file"] == ayse["db_file"]
    assert c.post("/api/book/leave").status_code == 409  # artık üye değil


def test_invited_new_member_leaves_and_keeps_benim(admin):
    from app import system

    m = _invite_member(admin, "cem")
    assert m.post("/api/entries", json=_entry("Cem defteri"), headers=OWN).status_code == 200
    assert m.post("/api/book/leave").status_code == 200
    u = system.user_by_name("cem@ornek.com")
    assert not u["member_of"] and u["db_file"]
    assert [x["description"] for x in m.get("/api/transactions").json()] == ["Cem defteri"]


def test_owner_removes_joined_member_account_stays(admin):
    from app import system

    c, ayse = _account(admin, "ayse")
    c.post("/api/entries", json=_entry("Ayşe kitap"))
    c.post(f"/api/book/join/{admin.post('/api/book/invites', json={}).json()['code']}")
    r = admin.delete(f"/api/book/members/{ayse['id']}")
    assert r.status_code == 200 and r.json()["detached"] is True
    assert system.user(ayse["id"]) and not system.user(ayse["id"])["member_of"]
    assert [x["description"] for x in c.get("/api/transactions").json()] == ["Ayşe kitap"]  # oturum ve veri duruyor
    # davetle yeni açılan üye: eskisi gibi hesabı silinir
    m = _invite_member(admin, "cem")
    cem = system.user_by_name("cem@ornek.com")
    assert "detached" not in admin.delete(f"/api/book/members/{cem['id']}").json()
    assert system.user(cem["id"]) is None and m.get("/api/transactions").status_code == 401


def test_owner_deletion_detaches_joined_member(admin):
    from app import deletion, system

    o, owner = _account(admin, "sahip")
    c, ayse = _account(admin, "ayse")
    c.post("/api/entries", json=_entry("Ayşe kitap"))
    c.post(f"/api/book/join/{o.post('/api/book/invites', json={}).json()['code']}")
    _invite_member(o, "cem")
    cem = system.user_by_name("cem@ornek.com")
    assert o.post("/api/me/delete", json={"password": "sahip-sifre-12345"}).status_code == 200
    a = system.user(ayse["id"])
    assert not a["member_of"] and not a.get("deleted_at")  # kendi hesabıyla katılan: yalnız ayrıldı
    assert system.user(cem["id"])["deleted_with"] == owner["id"]  # davetle açılan: sahiple birlikte silinir
    assert [x["description"] for x in c.get("/api/transactions").json()] == ["Ayşe kitap"]
    assert deletion.pending(system.user(owner["id"]))


def test_demo_or_anonymous_cannot_join(client):
    r = client.get("/api/book/join/AAAA-BBBB-CCCC")
    assert r.status_code in (401, 409)


def test_capability_listed(client):
    from app import version

    assert "book_join" in version.CAPABILITIES


def test_member_with_own_family_plan_cannot_start_family(hosted):
    """Tek aile: kendi Aile paketi olan kişi başka aileye katıldıysa davet üretemez, aile kuramaz (önce ayrılmalı)."""
    o, _ = _family(hosted)
    c, u = _account(hosted, "ayse")
    _rc("INITIAL_PURCHASE", u["id"], ents=("aile",))
    _join(c, o)
    for h in ({}, OWN):
        assert c.post("/api/book/invites", json={}, headers=h).status_code == 403
        assert c.get("/api/book", headers=h).status_code == 403
    assert c.post("/api/book/leave").status_code == 200
    assert c.post("/api/book/invites", json={}).status_code == 200  # ayrılınca kendi ailesini kurabilir


def _plan(c, h=None):
    return c.get("/api/meta", headers=h or {}).json()["plan"]


def _all_open(p):
    return p["plan"] == "aile" and all(p["features"].values())


def test_free_member_gets_family_in_both_books_and_loses_it_on_leave(hosted):
    """Ücretsiz gelen üye: ortak defterde de "Benim" defterinde de Aile özellikleri açık; ayrılınca kapanır."""
    o, _ = _family(hosted)
    c, _ = _account(hosted, "ayse")
    assert _plan(c)["plan"] == "free"
    _join(c, o)
    assert _all_open(_plan(c)) and _all_open(_plan(c, OWN))
    assert c.get("/api/year-summary").status_code != 402
    assert c.post("/api/book/leave").status_code == 200
    p = _plan(c)
    assert p["plan"] == "free" and not p["features"]["istatistik"]


def test_member_of_admin_book_gets_family(hosted):
    """Yöneticinin ortak defteri: yönetici Aile sayılır, ücretsiz gelen üyesi de (canlı hata 05.11: üye ücretsiz kaldı)."""
    c, _ = _account(hosted, "ayse")
    _join(c, hosted)
    assert _all_open(_plan(c)) and _all_open(_plan(c, OWN))


def test_member_falls_back_when_owner_lapses_or_removes(hosted):
    """Sahibin Aile paketi biterse (ödemezse) ya da sahip üyeyi çıkarırsa: kendi paketi yoksa ücretsize döner."""
    o, owner = _family(hosted)
    c, ayse = _account(hosted, "ayse")
    _join(c, o)
    assert _all_open(_plan(c))
    _rc("EXPIRATION", owner["id"], ents=(), exp_ms=946684800000)
    assert _plan(c)["plan"] == "free" and _plan(c, OWN)["plan"] == "free"
    _rc("RENEWAL", owner["id"], ents=("aile",))
    assert _all_open(_plan(c))
    assert o.delete(f"/api/book/members/{ayse['id']}").status_code == 200
    assert _plan(c)["plan"] == "free"
    # kendi Premium'u olan: çıkarılınca Premium kalır
    p, prem = _account(hosted, "prem")
    _rc("INITIAL_PURCHASE", prem["id"])
    _join(p, o)
    assert o.delete(f"/api/book/members/{prem['id']}").status_code == 200
    assert _plan(p)["plan"] == "premium"
