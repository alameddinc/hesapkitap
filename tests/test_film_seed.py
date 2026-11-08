"""Reklam filmi hesapları (app/film_seed.py, app/film_scope.py): iki hesap ortak defterde, ekranların uçları dolu; uydurma
"Market A…E" fiyatlarını yalnız film hesapları görür, film hesapları da gerçek topluluk verisini görmez. Bütün adlar,
e-postalar, kartlar ve tutarlar uydurma."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import film_scope, film_seed

OWNER, MEMBER = "ayse@film.ornek", "mehmet@film.ornek"
PW = "film-sifre-12345"


@pytest.fixture()
def hosted_dir(tmp_path, monkeypatch):
    from app import system

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.delenv("HESAPKITAP_PRICE_SOURCES", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    system.init()  # tmp_path/system.db (conftest)
    (tmp_path / "pw.txt").write_text(PW + "\n", encoding="utf-8")
    film_scope.forget()
    yield tmp_path
    film_scope.forget()


def _run(d, *extra):
    return film_seed.main(["--data-dir", str(d), "--owner-email", OWNER, "--member-email", MEMBER,
                           "--password-file", str(d / "pw.txt"), *extra])


def _login(email, pw=PW):
    from app.api.main import app

    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": email, "password": pw}).status_code == 200
    return c


@pytest.fixture()
def seeded(hosted_dir):
    assert _run(hosted_dir, "--zakat") == 0
    return hosted_dir


def test_every_film_product_resolves_and_receipts_use_known_titles():
    keys = {t: film_seed.key_of(t) for t, *_ in film_seed.PRODUCTS}
    assert len(set(keys.values())) == len(film_seed.PRODUCTS)
    for _d, ch, _w, lines in film_seed.RECEIPTS:
        assert ch in film_scope.CHAINS and all(t in keys for t, _q in lines)
    for c in film_seed.CARDS:  # kart numarası maskeli, tam numara yok
        assert "*" in c[1] and "*" in c[2]


def test_dry_run_writes_nothing(hosted_dir, capsys):
    from app import system

    assert _run(hosted_dir, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "hiçbir şey yazılmadı" in out and "Market A" in out
    assert system.users() == [] and not film_scope.user_ids()
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM community_prices").fetchone()[0] == 0


def test_accounts_plan_seat_and_prefs(seeded):
    from app import plans, push, system

    o, m = system.user_by_name(OWNER), system.user_by_name(MEMBER)
    assert film_scope.user_ids() == {o["id"], m["id"]}
    assert o["plan"] == "aile" and o["plan_source"] == "manual" and plans.plan_of(o) == plans.FAMILY
    assert m["member_of"] == o["id"] and m["book_role"] == "editor" and plans.seat_state(m) == "seat"
    assert plans.plan_of(m) == plans.FAMILY
    for u in (o, m):
        t = push.get_prefs(u["id"])["types"]
        assert t["ortak"] and t["liste"]
        assert system.get(f"price_share:{u['id']}") == "0"
    owner = _login(OWNER)
    assert owner.get("/api/push/prefs").json()["types"]["ortak"] is True
    me = _login(MEMBER).get("/api/book/me").json()
    assert me["member"] and me["role"] == "editor"


def test_screens_are_filled_for_both(seeded):
    month = (date.today() - timedelta(days=5)).isoformat()[:7]  # ayın ilk günlerinde de dolu dönem
    for email in (OWNER, MEMBER):
        c = _login(email)
        inst = c.get("/api/installments").json()
        assert inst["count"] >= 3 and inst["total"] > 0 and inst["remaining"] > 0
        assert any(i["title"] and "Buzdolabı" in (i["description"] or i["title"]) and i["installment"] == "5/6"
                   for i in inst["items"])
        ms = c.get("/api/book/member-spend", params={"month": month}).json()
        names = {p["name"]: p for p in ms["people"]}
        assert {"ayse", "mehmet"} <= set(names) and all(names[n]["amount"] > 0 for n in ("ayse", "mehmet"))
        assert names["mehmet"]["categories"][0]["name"] in ("Kafe & Restoran", "Market & Gıda", "Yemek Siparişi")
        s = c.get("/api/shopping-list").json()
        assert len(s["items"]) == len(film_seed.LIST) and {"6 adet", "2,5 kg"} <= {i["amount_label"] for i in s["items"]}
        assert all(i["best"] and i["best"]["chain"] in film_scope.CHAINS for i in s["items"])
        assert s["saved_plan"] and s["saved_plan"]["total"] > 0
        assert all(ch["chain"] in film_scope.CHAINS for ch in s["saved_plan"]["chains"])
        opt = c.get("/api/shopping-list/optimize").json()
        assert not opt["unpriced"] and opt["plans"] and "resmi" not in opt["source_info"]["kinds"]
        trips = c.get("/api/shopping-trips").json()["trips"]
        assert len(trips) == 1 and trips[0]["linked"] and trips[0]["saving"] > 0
        assert trips[0]["chains"] == [film_seed.TRIP_CHAIN]
        assert c.get("/api/shopping-trips/savings").json()["month"] == trips[0]["saving"]
        pr = c.get("/api/prices").json()
        sut = next(p for p in pr["products"] if p["group"] and p["group"]["key"] == "sut|1l")
        assert sut["market"] and sut["market_ref"]["median"] > 0 and sut["market_ref"]["source"] == "community"
        assert sut["advice"]["kind"] == "cheaper_at" and sut["advice"]["chain"] == "Market A" and sut["advice"]["saving"] > 0
        mk = c.get("/api/prices/market", params={"group": "sut|1l", "days": 90}).json()
        assert len(mk["days"]) >= 30 and mk["days"][-1]["day"] == date.today().isoformat()
        assert {d["source"] for d in mk["days"]} == {"community"} and mk["attribution"] is None
        assert c.get("/api/budgets").json()
        assert c.get("/api/summary").status_code == 200
    owner = _login(OWNER)
    last = max((e for e in owner.get("/api/transactions").json() if e.get("description") == "Market B"),
               key=lambda e: e["date"])
    notes = owner.get(f"/api/entries/{last['id']}/receipt-items").json()["check"]["market_notes"]
    assert {n["group"] for n in notes} == {"sut|1l", "yumurta|30ad"}
    assert owner.get("/api/zakat").status_code == 200


def test_film_prices_are_invisible_to_others_and_real_data_to_film(seeded):
    from app import community_prices, system

    with system.connect() as c:  # gerçek kullanıcıların topluluk katkısı (24 haneli onaltılık kimlik)
        c.executemany("INSERT INTO community_prices (chain, group_key, name_norm, unit_price, day, contributor) "
                      "VALUES ('BİM', 'sut|1l', 'SUT 1 L', 31.5, ?, ?)",
                      [(date.today().isoformat(), f"{i:024x}") for i in range(3)])
    from app import auth

    u = auth.create_user("baska@ornek.com", "baska-sifre-12345")
    system.update_user(u["id"], plan="premium", plan_source="manual")  # fiyat sayfası Premium
    other = _login("baska@ornek.com", "baska-sifre-12345")
    got = other.get("/api/prices/groups", params={"q": "süt"}).json()
    chains = {o["chain"] for g in got for o in g["best"]}
    assert chains == {"BİM"}  # uydurma zincir yok
    mk = other.get("/api/prices/market", params={"group": "sut|1l", "days": 90}).json()
    assert all(d.get("cheapest_chain") not in film_scope.CHAINS for d in mk["days"])
    film = _login(MEMBER).get("/api/prices/groups", params={"q": "süt"}).json()
    fchains = {o["chain"] for g in film for o in g["best"] if o["source"] != "own"}
    assert fchains and fchains <= set(film_scope.CHAINS)  # gerçek satır film hesabına görünmez
    assert all(ch not in film_scope.CHAINS for ch, _g in community_prices.medians())  # bağlamsız (iş): film yok
    from app import community_export

    assert all(not r[3].startswith("film-") for r in community_export._source(date.today().isoformat()))


def test_film_user_receipts_do_not_feed_community(seeded):
    from app import community_prices, system

    uid = system.user_by_name(OWNER)["id"]
    system.put(f"price_share:{uid}", None)  # paylaşım açılsa bile
    n = community_prices.contribute(uid, "BİM", None, [{"name": "SÜT 1 L", "qty": 1, "amount": 30.0}],
                                    date.today().isoformat(), receipt_total=30.0)
    assert n == 0


def test_refuses_existing_and_reset_removes_only_film(seeded, capsys):
    from app import auth, system

    auth.create_user("baska@ornek.com", "baska-sifre-12345")
    with pytest.raises(SystemExit, match="zaten film hesabı"):
        _run(seeded)
    with pytest.raises(SystemExit, match="film dışı"):
        film_seed.main(["--data-dir", str(seeded), "--owner-email", "baska@ornek.com", "--member-email", MEMBER,
                        "--password-file", str(seeded / "pw.txt"), "--reset-film"])
    old = sorted(film_scope.user_ids())
    assert _run(seeded, "--reset-film") == 0
    new = sorted(film_scope.user_ids())
    assert len(new) == 2 and not set(old) & set(new)
    assert all(system.user(i) is None for i in old) and not (seeded / "users" / str(old[0])).exists()
    assert film_seed.main(["--data-dir", str(seeded), "--remove"]) == 0
    assert [u["username"] for u in system.users()] == ["baska@ornek.com"] and not film_scope.user_ids()
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM community_prices WHERE contributor LIKE 'film-%'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == 0
    assert not (seeded / "users" / str(new[0])).exists()


def test_refresh_prices_rewrites_only_film_rows(seeded):
    from app import system

    with system.connect() as c:
        c.execute("INSERT INTO community_prices (chain, group_key, name_norm, unit_price, day, contributor) "
                  "VALUES ('BİM', 'sut|1l', 'SUT 1 L', 31.5, ?, 'abc123')", (date.today().isoformat(),))
        before = c.execute("SELECT COUNT(*) FROM community_prices WHERE contributor LIKE 'film-%'").fetchone()[0]
    assert film_seed.main(["--data-dir", str(seeded), "--refresh-prices"]) == 0
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM community_prices WHERE contributor LIKE 'film-%'").fetchone()[0] == before
        assert c.execute("SELECT COUNT(*) FROM community_prices WHERE contributor = 'abc123'").fetchone()[0] == 1
