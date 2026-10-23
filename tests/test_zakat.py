from datetime import date

import pytest

PRICES = {"GRA": 6400.0, "YIA": 5900.0, "CEYREKALTIN": 10400.0, "USD": 50.0, "GUMUS": 87.0}


@pytest.fixture(autouse=True)
def fake_prices(monkeypatch):
    from app import zakat

    monkeypatch.setattr(zakat, "fetch_prices", lambda force=False: {"prices": dict(PRICES), "updated": "test",
                                                                     "fetched": 0, "source": "test"})


def _person(data, name):
    return next(p for p in data["people"] if p["name"] == name)


def test_nisab_and_ownership(client, tmpdb):
    d = client.get("/api/zakat").json()
    assert d["nisab"] == round(80.18 * 6400, 2)
    me = _person(d, "Ben")
    wife = client.post("/api/zakat/people", json={"name": "Eşim"}).json()["id"]
    # düğün takıları eşin: zekât ona düşer
    client.post("/api/zakat/assets", json={"person_id": wife, "kind": "ALTIN_22", "quantity": 100, "is_jewelry": True})
    client.post("/api/zakat/assets", json={"person_id": me["id"], "kind": "CEYREK", "quantity": 5})
    d = client.get("/api/zakat").json()
    w, me = _person(d, "Eşim"), _person(d, "Ben")
    assert w["total"] == 590000 and w["above_nisab"] and w["estimate"] == 14750
    assert me["total"] == 52000 and not me["above_nisab"]  # kendi payı nisabın altında
    # Şâfiî: kullanılan ziynet muaf
    client.put("/api/zakat/settings", json={"madhhab": "safii"})
    assert _person(client.get("/api/zakat").json(), "Eşim")["total"] == 0


def test_app_data_and_debts_hanefi(client, tmpdb):
    client.post("/api/accounts", json={"name": "Vakıf", "kind": "banka", "balance": 700000})
    client.post("/api/debts", json={"person": "Ali", "direction": "borc", "amount": 100000, "debt_date": "2026-10-01"})
    client.post("/api/debts", json={"person": "Veli", "direction": "alacak", "amount": 100, "asset": "USD",
                                    "debt_date": "2026-10-01"})
    me = _person(client.get("/api/zakat").json(), "Ben")
    assert me["gross"] == 705000 and me["deduct"] == 100000 and me["total"] == 605000 and me["above_nisab"]
    client.put("/api/zakat/settings", json={"madhhab": "safii"})  # Şâfiî: borç düşülmez
    assert _person(client.get("/api/zakat").json(), "Ben")["total"] == 705000


def test_hawl_payments_and_next_year(client, tmpdb):
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    assert client.post(f"/api/zakat/people/{me}/payments", json={"amount": 10}).status_code == 422  # yıl yok
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "TRY", "quantity": 1000000})
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-01-01"})
    client.post(f"/api/zakat/people/{me}/payments", json={"amount": 5000})
    h = _person(client.get("/api/zakat").json(), "Ben")["hawl"]
    assert h["due"] == "2026-12-21" and h["zakat"] == 25000 and h["paid"] == 5000 and h["remaining"] == 20000
    assert "Ramazan" in h["start_hijri"] or h["start_hijri"]  # kameri tarih gösterilir
    assert client.post(f"/api/zakat/people/{me}/next-year").json()["hawl_start"] == "2026-12-21"
    assert _person(client.get("/api/zakat").json(), "Ben")["hawl"]["paid"] == 0  # yeni yıl temiz başlar


def test_reminder(tmpdb):
    from app import jobs, zakat

    me = zakat.people()[0]["id"]
    zakat.add_asset(me, "TRY", 1000000, False, None)
    zakat.update_person(me, {"hawl_start": "2026-01-01"})
    assert any("Zekât" in m for m in jobs.reminders(date(2026, 12, 14)))
    assert not any("Zekât" in m for m in jobs.reminders(date(2026, 12, 13)))


def test_hijri():
    from app.zakat import to_hijri

    assert to_hijri(date(2026, 2, 19)).endswith("Ramazan 1447")  # Ramazan 1447 ≈ 18/19 Şubat 2026


def test_acquired_date_suggest_and_safii(client, tmpdb):
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "CEYREK", "quantity": 20, "acquired": "2025-03-01"})
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "CEYREK", "quantity": 40, "acquired": "2025-09-01"})
    p = _person(client.get("/api/zakat").json(), "Ben")
    assert p["suggest_start"] == "2025-09-01"  # 60 çeyrek ≈ 624 bin > nisap; 20'si tek başına yetmez
    # Hanefî: yıl içinde eklenen de sayılır; Şâfiî: yılını doldurmamış olan sayılmaz
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2025-03-01"})  # yıl 2026-02-18'de biter
    assert _person(client.get("/api/zakat").json(), "Ben")["total"] == 624000
    client.put("/api/zakat/settings", json={"madhhab": "safii"})
    assert _person(client.get("/api/zakat").json(), "Ben")["total"] == 208000


def test_holdings_in_net_worth(client, tmpdb):
    assert client.get("/api/holdings").json()["items"] == []
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    wife = client.post("/api/zakat/people", json={"name": "Eşim"}).json()["id"]
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "CEYREK", "quantity": 2, "acquired": "2025-01-10",
                                           "cost": 16000})
    client.post("/api/zakat/assets", json={"person_id": wife, "kind": "ALTIN_22", "quantity": 10, "is_jewelry": True})
    h = client.get("/api/holdings").json()
    mine = next(i for i in h["items"] if i["is_self"])
    assert mine["value"] == 20800 and mine["gain"] == 4800 and mine["gain_pct"] == 30.0
    assert h["self_total"] == 20800 and h["family_total"] == 59000
    nw = client.get("/api/net-worth").json()
    assert nw["holdings"] == 20800 and nw["family_holdings"] == 59000 and nw["net"] == 20800  # eşinki net duruma girmez


def test_holding_candidates(client, tmpdb):
    tmpdb.create_entry({"entry_date": "2025-03-02", "description": "ATM PARA CEKME", "amount": 25000, "kind": "gider",
                        "category": "Nakit Çekim"})
    tmpdb.create_entry({"entry_date": "2025-03-03", "description": "Market", "amount": 2500, "kind": "gider"})
    tmpdb.create_entry({"entry_date": "2025-03-04", "description": "ATM", "amount": 200, "kind": "gider"})
    rows = client.get("/api/holdings/candidates").json()
    assert [(r["date"], r["amount"], r["kind"]) for r in rows] == [("2025-03-02", 25000, "nakit")]
