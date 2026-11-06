"""Nereden alınır: GET /api/prices/groups (kendi fişlerin + topluluk ortancası, zincir başına en ucuz)."""
from datetime import date

from app import community_prices as cp
from tests.test_book import _member, owner  # noqa: F401  (fixture)

TODAY = date.today()


def _buy(client, store, name, price, brand=None):
    item = {"name": name, "qty": 1, "amount": price} | ({"brand": brand} if brand else {})
    client.post("/api/entries", json={"entry_date": TODAY.isoformat(), "description": store, "amount": price,
                                      "kind": "gider", "category": "Market & Gıda", "payment": "nakit", "items": [item]})


def _community(chain, name, prices):
    for uid, p in enumerate(prices, start=100):
        cp.contribute(uid, chain, None, [{"name": name, "qty": 1, "amount": p}], TODAY.isoformat())


def test_where_to_buy(client):
    _buy(client, "A101", "Torku Süt 1L", 36, brand="Torku")
    _buy(client, "BİM", "SÜTAŞ GÜNLÜK SÜT 1L", 38)
    _buy(client, "Örnek Bakkal", "Ekmek", 15)
    _community("Migros", "Pınar Süt 1 L", [33, 34, 35])
    _community("ŞOK", "Süt 1L", [30, 31])                        # 2 kişi: gösterilmez
    _community("Migros", "Ayran 200 ml", [10, 11, 12])           # yalnız toplulukta
    got = client.get("/api/prices/groups", params={"q": "süt"}).json()
    assert [g["key"] for g in got] == ["sut|1l"] and got[0]["label"] == "Süt · 1 L"
    best = got[0]["best"]
    assert [(b["chain"], b["price"], b["source"]) for b in best] == [
        ("Migros", 34.0, "community"), ("A101", 36.0, "own"), ("BİM", 38.0, "own")]
    assert best[0]["n"] == 3 and "date" not in best[0] and best[1]["date"] == TODAY.isoformat()
    assert best[1]["brand"] == "Torku"
    every = client.get("/api/prices/groups").json()
    assert [g["key"] for g in every][-1] == "ayran|200ml"              # kendi aldıkların önce, sonra topluluk
    assert {"ekmek", "sut|1l"} <= {g["key"] for g in every}
    assert len(client.get("/api/prices/groups", params={"limit": 1}).json()) == 1
    # istemci grup anahtarını gönderir: yalnız o grup
    _buy(client, "A101", "Kakaolu Süt 1L", 20)
    assert [g["key"] for g in client.get("/api/prices/groups", params={"q": "sut|1l"}).json()] == ["sut|1l"]
    assert [g["key"] for g in client.get("/api/prices/groups", params={"q": "ayran|200ml"}).json()] == ["ayran|200ml"]
    assert [g["key"] for g in client.get("/api/prices/groups", params={"q": "ekmek"}).json()] == ["ekmek"]
    assert client.get("/api/prices/groups", params={"q": "olmayan|1l"}).json() == []


def test_where_to_buy_is_premium():
    from app import plans

    assert plans.gate_for("/api/prices/groups") == "fiyatlar"


def test_member_does_not_see_hidden_receipt_prices(owner):  # noqa: F811
    r = owner.post("/api/entries", json={"entry_date": TODAY.isoformat(), "description": "A101", "amount": 36,
                                         "kind": "gider", "category": "Market & Gıda", "payment": "nakit",
                                         "items": [{"name": "Torku Süt 1L", "qty": 1, "amount": 36}]})
    _buy(owner, "BİM", "Ekmek", 15)
    assert owner.patch(f"/api/entries/{r.json()['id']}", json={"hidden": True}).status_code == 200
    assert {g["key"] for g in owner.get("/api/prices/groups").json()} == {"sut|1l", "ekmek"}
    viewer = _member(owner, "izleyici", "viewer")
    assert [g["key"] for g in viewer.get("/api/prices/groups").json()] == ["ekmek"]
