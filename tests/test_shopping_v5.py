"""Alışveriş listesi v5: zincir × marka × boy seçicisi (shop_combo_picker) — "BİM · Örneksüt 1 L ₺36,50" satırına
dokununca satırın mağazası, markası ve boyu tek adımda yazılır; plan tam o teklifi kullanır. Fiyatlar, markalar ve
kişi adları uydurmadır."""
from app import system
from tests.test_shopping_v4 import TODAY, _add, _community, _official, resmi  # noqa: F401  (fixture)


def _seed():
    _official([("BİM", "sut|1l", 36.5, "ÖRNEKSÜT"), ("BİM", "sut|1l", 42.9, "Mandıra Köy"),
               ("BİM", "sut|1l", 44.0, "Mandıra Köy", "Mandıra Köy Tam Yağlı 1 L"),
               ("A101", "sut|1l", 37.25, "ÖRNEKSÜT"), ("Migros", "sut|1l", 44.95, "Yayla Örnek"),
               ("CarrefourSA", "sut|1l", 35.0, "ÖRNEKSÜT"), ("BİM", "sut|500ml", 21.0, "ÖRNEKSÜT")])


def test_combo_rows_per_chain_and_brand(client, resmi):  # noqa: F811
    _seed()
    _community("Hakmar", "sut|1l", 39.0)
    r = client.get("/api/prices/combos", params={"group": "sut|1l"}).json()
    rows = [(x["chain"], x["brand"], x["price"]) for x in r["rows"]]
    assert rows == [("CarrefourSA", "Örneksüt", 35.0), ("BİM", "Örneksüt", 36.5), ("A101", "Örneksüt", 37.25),
                    ("Hakmar", None, 39.0), ("BİM", "Mandıra Köy", 42.9), ("Migros", "Yayla Örnek", 44.95)]
    bim = next(x for x in r["rows"] if x["brand"] == "Mandıra Köy")
    assert bim["key"] == "sut|1l" and bim["size_label"] == "1 L" and bim["unit_price_per"] == 42.9
    assert bim["per_unit"] == "L" and bim["source"] == "resmi" and bim["date"] == TODAY and bim["product"]
    assert r["chains"] == ["CarrefourSA", "BİM", "A101", "Hakmar", "Migros"]
    assert r["brands"][0] == "Örneksüt" and set(r["brands"]) == {"Örneksüt", "Mandıra Köy", "Yayla Örnek"}
    assert r["attribution"] and r["hidden"]["count"] == 0
    only = client.get("/api/prices/combos", params={"group": "sut|1l", "chain": "BIM"}).json()
    assert [(x["chain"], x["brand"]) for x in only["rows"]] == [("BİM", "Örneksüt"), ("BİM", "Mandıra Köy")]


def test_combo_hidden_and_sizes(client, resmi):  # noqa: F811
    _seed()
    client.put("/api/me/ui", json={"shop_hidden": {"chains": ["CarrefourSA"]}})
    milk = _add(client, "Süt")
    r = client.get(f"/api/shopping-list/{milk}/combos").json()
    assert "CarrefourSA" not in r["chains"] and r["hidden"]["count"] == 1
    assert [s["size_label"] for s in r["sizes"]] == ["500 ml", "1 L"]
    assert r["selected"]["store"] is None and r["selected"]["brand"] is None
    assert ("BİM", "Örneksüt", "sut|500ml") in {(x["chain"], x["brand"], x["key"]) for x in r["rows"]}
    assert client.get("/api/shopping-list/999/combos").status_code == 404


def test_pick_combo_sets_store_brand_size_and_plan_uses_it(client, resmi):  # noqa: F811
    _seed()
    milk = _add(client, "Süt 1 L", qty=2)
    pick = next(x for x in client.get(f"/api/shopping-list/{milk}/combos").json()["rows"]
                if x["chain"] == "BİM" and x["brand"] == "Mandıra Köy")
    p = client.patch(f"/api/shopping-list/{milk}", json={"store": pick["chain"], "brand": pick["brand"],
                                                          "group_key": pick["key"]}).json()
    assert (p["store"], p["brand"], p["group"]["key"]) == ("BİM", "Mandıra Köy", "sut|1l")
    it = client.get("/api/shopping-list").json()["items"][0]
    assert (it["best"]["chain"], it["best"]["price"], it["best"]["pinned"]) == ("BİM", 42.9, True)
    o = client.get("/api/shopping-list/optimize").json()
    line = o["plans"][0]["stops"][0]["items"][0]
    assert (o["plans"][0]["stops"][0]["chain"], line["price"], line["line_total"], line["pinned"]) == \
        ("BİM", 42.9, 85.8, True)
    assert "better_size" not in line
    sel = client.get(f"/api/shopping-list/{milk}/combos").json()["selected"]
    assert sel == {"store": "BİM", "brand": "Mandıra Köy", "key": "sut|1l"}
    # "Marka fark etmez" + otomatik mağaza: eski davranış
    client.patch(f"/api/shopping-list/{milk}", json={"store": None, "brand": None})
    it = client.get("/api/shopping-list").json()["items"][0]
    assert (it["best"]["chain"], it["best"]["price"]) == ("CarrefourSA", 35.0)


def test_own_receipt_brand_in_combos(client, resmi):  # noqa: F811
    _official([("BİM", "sut|1l", 36.5, "ÖRNEKSÜT")])
    r = client.post("/api/entries", json={"entry_date": TODAY, "description": "BİM", "amount": 34,
                                          "kind": "gider", "category": "Market & Gıda", "payment": "nakit",
                                          "items": [{"name": "ÖRNEKSÜT SÜT 1 L", "qty": 1, "amount": 34,
                                                     "brand": "Örneksüt"}]})
    assert r.status_code == 200
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM official_prices").fetchone()[0] == 1
    rows = client.get("/api/prices/combos", params={"group": "sut|1l"}).json()["rows"]
    bim = [x for x in rows if x["chain"] == "BİM"]
    assert len(bim) == 1 and bim[0]["brand"] == "Örneksüt"
    assert bim[0]["source"] == "own" and bim[0]["price"] == 34.0   # kendi taze fişin Market Fiyatından önce
    assert "shop_combo_picker" in client.get("/api/meta").json()["capabilities"]
