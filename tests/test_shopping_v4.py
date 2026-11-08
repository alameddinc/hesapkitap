"""Alışveriş listesi v4: mağaza süzgeci (shop_store_filter), satırı mağazaya bağlama ve "Marketleri ben seçeyim"
(shop_store_pin), marka + boy (shop_brand_size), birim fiyat / En kârlı / boy önerisi / plan tasarrufu
(shop_best_value). Zincir fiyatları, markalar ve yerel market adları uydurmadır."""
from datetime import date, timedelta

import pytest

from app import price_sources, system
from tests.test_book import _member, owner  # noqa: F401  (fixture)

TODAY = date.today().isoformat()


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)
    from app import shop_stores

    shop_stores._cache.clear()


def _official(rows, region="İstanbul", day=TODAY):
    """rows: [(zincir, grup, fiyat[, marka[, ad]])]"""
    with system.connect() as c:
        c.executemany("INSERT INTO official_prices (chain, group_key, title, brand, price, day, region) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(r[0], r[1], (r[4] if len(r) > 4 else f"Örnek {r[1]} {r[3] if len(r) > 3 else ''}"),
                        (r[3] if len(r) > 3 else "Örnekmarka"), r[2], day, region) for r in rows])


def _add(c, text, **kw):
    return c.post("/api/shopping-list", json={"text": text, **kw}).json()["id"]


def _chains(plan):
    return sorted(s["chain"] for s in plan["stops"])


# ---------------------------------------------------------------- mağaza süzgeci

def test_hidden_chain_never_offered(client, resmi):
    _official([("BİM", "sut|1l", 30), ("CarrefourSA", "sut|1l", 25), ("Migros", "sut|1l", 35),
               ("CarrefourSA", "cay|1kg", 150), ("Migros", "cay|1kg", 170)])
    milk = _add(client, "Süt 1 L")
    _add(client, "Çay 1 kg")
    lst = client.get("/api/shopping-list").json()
    assert next(i for i in lst["items"] if i["id"] == milk)["best"]["chain"] == "CarrefourSA"
    assert lst["hidden_stores"] == {"count": 0, "chains": [], "local": False}

    r = client.put("/api/me/ui", json={"shop_hidden": {"chains": ["CARREFOURSA"]}})
    assert r.status_code == 200 and r.json()["shop_hidden"] == {"chains": ["CarrefourSA"], "local": False}
    assert client.get("/api/meta").json()["ui"]["shop_hidden"] == {"chains": ["CarrefourSA"], "local": False}

    lst = client.get("/api/shopping-list").json()
    assert next(i for i in lst["items"] if i["id"] == milk)["best"]["chain"] == "BİM"
    assert lst["hidden_stores"]["count"] == 1 and lst["hidden_stores"]["chains"] == ["CarrefourSA"]
    o = client.get("/api/shopping-list/optimize").json()
    seen = {s["chain"] for p in o["plans"] for s in p["stops"]} | {s["chain"] for s in o["single"]}
    assert "CarrefourSA" not in seen and o["hidden"]["count"] == 1
    assert all("CarrefourSA" not in str(x.get("cheaper_elsewhere")) for p in o["plans"] for s in p["stops"]
               for x in s["items"])
    assert {c["chain"] for c in o["chain_options"]} == {"BİM", "Migros"}
    g = client.get("/api/prices/groups", params={"q": "sut|1l"}).json()
    assert [b["chain"] for b in g[0]["best"]] == ["BİM", "Migros"] and g[0]["hidden_count"] == 1
    sz = client.get(f"/api/shopping-list/{milk}/sizes").json()
    one = next(x for x in sz if x["key"] == "sut|1l")
    assert one["chain"] == "BİM" and one["hidden_count"] == 1
    off = client.get("/api/prices/offers", params={"group": "sut|1l"}).json()
    assert [x["chain"] for x in off["offers"]] == ["BİM", "Migros"] and off["hidden"]["count"] == 1

    st = client.get("/api/shopping-list/stores").json()
    assert {"chain": "CarrefourSA", "hidden": True} in st["chains"] and {"chain": "BİM", "hidden": False} in st["chains"]
    # geri aç: verilmeyen alan korunur, boş liste hepsini açar
    assert client.put("/api/me/ui", json={"shop_hidden": {"local": True}}).json()["shop_hidden"] == \
        {"chains": ["CarrefourSA"], "local": True}
    assert client.put("/api/me/ui", json={"shop_hidden": {"chains": [], "local": False}}).json()["shop_hidden"] == \
        {"chains": [], "local": False}
    assert client.get("/api/shopping-list").json()["items"][0]["best"]["chain"] == "CarrefourSA"
    assert client.put("/api/me/ui", json={"shop_hidden": {"chains": ["x"] * 41}}).status_code == 422


def test_hide_local_shops(client, resmi):
    """Yerel market: kendi fişindeki zincir olmayan işyeri; "yerel marketler" kapatılınca grup olarak gizlenir."""
    _official([("BİM", "sut|1l", 30)])
    r = client.post("/api/entries", json={"entry_date": TODAY, "description": "Örnek Bakkal", "amount": 20,
                                          "kind": "gider", "category": "Market & Gıda", "payment": "nakit",
                                          "items": [{"name": "SÜT 1 L", "qty": 1, "amount": 20}]})
    assert r.status_code == 200, r.text
    milk = _add(client, "Süt 1 L")
    assert client.get("/api/shopping-list").json()["items"][0]["best"]["chain"] == "Örnek Bakkal"
    st = client.get("/api/shopping-list/stores").json()
    assert st["local"]["names"] == ["Örnek Bakkal"] and st["local"]["count"] == 1
    assert all(c["chain"] != "Örnek Bakkal" for c in st["chains"])
    client.put("/api/me/ui", json={"shop_hidden": {"local": True}})
    lst = client.get("/api/shopping-list").json()
    assert lst["items"][0]["best"]["chain"] == "BİM"
    assert lst["hidden_stores"] == {"count": 1, "chains": ["Örnek Bakkal"], "local": True}
    # açık seçim süzgeçten önce gelir: satırı yerel markete bağlamak o fiyatı getirir
    client.patch(f"/api/shopping-list/{milk}", json={"store": "Örnek Bakkal"})
    it = client.get("/api/shopping-list").json()["items"][0]
    assert it["store"] == "Örnek Bakkal" and it["best"]["chain"] == "Örnek Bakkal" and it["best"]["pinned"]


def test_filter_is_per_person_in_shared_book(owner, resmi):  # noqa: F811
    _official([("BİM", "sut|1l", 30), ("Hakmar", "sut|1l", 25)])
    m = _member(owner, "uye", "editor")
    _add(owner, "Süt 1 L")
    owner.put("/api/me/ui", json={"shop_hidden": {"chains": ["Hakmar"]}})
    assert owner.get("/api/shopping-list").json()["items"][0]["best"]["chain"] == "BİM"
    assert m.get("/api/shopping-list").json()["items"][0]["best"]["chain"] == "Hakmar"   # üyenin kendi seçimi
    assert m.get("/api/meta").json()["ui"]["shop_hidden"] == {"chains": [], "local": False}
    assert m.put("/api/me/ui", json={"shop_hidden": {"chains": ["BİM"]}}).status_code == 200
    assert m.get("/api/shopping-list").json()["items"][0]["best"]["chain"] == "Hakmar"
    assert owner.get("/api/meta").json()["ui"]["shop_hidden"]["chains"] == ["Hakmar"]


# ---------------------------------------------------------------- mağazaya bağla, marketleri ben seçeyim

def test_pinned_item_fixed_to_store(client, resmi):
    _official([("BİM", "sut|1l", 30), ("Migros", "sut|1l", 40), ("BİM", "cay|1kg", 150), ("Migros", "cay|1kg", 160),
               ("BİM", "pirinc|1kg", 60)])
    milk = _add(client, "Süt 1 L")
    tea = _add(client, "Çay 1 kg")
    rice = _add(client, "Pirinç 1 kg")
    o = client.get("/api/shopping-list/optimize").json()
    assert [_chains(p) for p in o["plans"]] == [["BİM"]]
    r = client.patch(f"/api/shopping-list/{milk}", json={"store": "MIGROS TICARET A.S."})
    assert r.json()["store"] == "Migros"
    o = client.get("/api/shopping-list/optimize").json()
    assert o["pinned"] == [{"id": milk, "text": "Süt 1 L", "chain": "Migros"}]
    assert [_chains(p) for p in o["plans"]] == [["Migros"], ["BİM", "Migros"]]   # bağlı mağaza her planda durak
    p1 = o["plans"][1]
    assert o["recommended_k"] == 2 and p1["recommended"]
    mig = next(s for s in p1["stops"] if s["chain"] == "Migros")
    assert mig["pinned"] and [x["id"] for x in mig["items"]] == [milk] and mig["items"][0]["pinned"]
    assert mig["items"][0]["price"] == 40.0 and "cheaper_elsewhere" not in mig["items"][0]
    assert {x["id"] for s in p1["stops"] if s["chain"] == "BİM" for x in s["items"]} == {tea, rice}
    # bağlı mağazada fiyat yok: satır fiyatsız, durak yine planda
    client.patch(f"/api/shopping-list/{milk}", json={"store": "Hakmar"})
    it = next(i for i in client.get("/api/shopping-list").json()["items"] if i["id"] == milk)
    assert it["best"] is None and it["store_missing"] is True
    o = client.get("/api/shopping-list/optimize").json()
    hk = next(s for s in o["plans"][0]["stops"] if s["chain"] == "Hakmar")
    assert hk["items"] == [] and hk["total"] == 0 and hk["pinned_unpriced"] == [{"id": milk, "text": "Süt 1 L"}]
    info = next(u for u in o["unpriced_info"] if u["id"] == milk)
    assert info["reason"] == "store_no_price" and info["hint"].startswith("Hakmar:")
    client.patch(f"/api/shopping-list/{milk}", json={"store": None})
    assert client.get("/api/shopping-list/optimize").json()["pinned"] == []


def test_manual_chain_choice(client, resmi):
    _official([("BİM", "sut|1l", 30), ("Migros", "sut|1l", 40), ("A101", "sut|1l", 28),
               ("BİM", "cay|1kg", 150), ("Migros", "cay|1kg", 160), ("A101", "pirinc|1kg", 55)])
    milk = _add(client, "Süt 1 L")
    tea = _add(client, "Çay 1 kg")
    rice = _add(client, "Pirinç 1 kg")
    r = client.get("/api/shopping-list/optimize", params={"chains": "Migros,BİM"}).json()
    assert len(r["plans"]) == 1 and r["plans"][0]["recommended"] and r["recommended_k"] == 2
    p = r["plans"][0]
    assert {s["chain"]: [x["id"] for x in s["items"]] for s in p["stops"]} == {"BİM": [milk, tea], "Migros": []} or \
        {s["chain"]: sorted(x["id"] for x in s["items"]) for s in p["stops"]} == {"BİM": sorted([milk, tea]), "Migros": []}
    assert r["manual"] == {"chains": ["Migros", "BİM"],
                           "missing": [{"id": rice, "text": "Pirinç 1 kg", "cheapest": {"chain": "A101", "price": 55.0}}]}
    assert r["headline"]["title"].startswith("Seçtiğin marketler: ")
    assert client.get("/api/shopping-list/optimize", params={"chains": "A,B,C,D"}).status_code == 422
    saved = client.put("/api/shopping-list/saved-plan", json={"chains": ["A101"]}).json()
    assert [c["chain"] for c in saved["chains"]] == ["A101"] and saved["k"] == 1
    assert client.get("/api/shopping-list/optimize").json()["manual"] is None


# ---------------------------------------------------------------- marka + boy

def test_brand_offers_and_item_brand(client, resmi):
    _official([("BİM", "sut|1l", 30, "Ucuzsüt"), ("BİM", "sut|1l", 42, "ÖRNEKSÜT"),
               ("A101", "sut|1l", 44.5, "ÖRNEKSÜT"), ("Migros", "sut|1l", 47.95, "Örneksüt"),
               ("Migros", "sut|500ml", 26, "ÖRNEKSÜT"), ("A101", "sut|1l", 33, "Mandıra Köy")])
    milk = _add(client, "Süt 1 L")
    b = client.get("/api/prices/brands", params={"group": "sut|1l"}).json()
    assert b["group"] == "sut"
    ors = next(x for x in b["brands"] if x["brand"] == "Örneksüt")   # SÜTAŞ / Sütaş gibi yazılışlar tek satır
    assert ors["chains"] == 3 and ors["sizes"] == ["sut|1l", "sut|500ml"]
    assert b["brands"][0]["brand"] == "Örneksüt"                   # çok zincirde olan önce
    assert client.get(f"/api/shopping-list/{milk}/brands").json()["brands"] == b["brands"]

    any_ = client.get("/api/prices/offers", params={"group": "sut|1l"}).json()
    assert [(x["chain"], x["price"]) for x in any_["offers"]] == [("BİM", 30.0), ("A101", 33.0), ("Migros", 47.95)]
    br = client.get("/api/prices/offers", params={"group": "sut|1l", "brand": "örneksüt"}).json()
    assert [(x["chain"], x["price"]) for x in br["offers"]] == [("BİM", 42.0), ("A101", 44.5), ("Migros", 47.95)]
    assert br["offers"][0]["unit_price_per"] == 42.0 and br["offers"][0]["per_unit"] == "L"
    assert br["label"] == "Süt · 1 L" and br["size_label"] == "1 L" and br["offers"][0]["date"] == TODAY

    r = client.patch(f"/api/shopping-list/{milk}", json={"brand": "Örneksüt"})
    assert r.json()["brand"] == "Örneksüt"
    it = client.get("/api/shopping-list").json()["items"][0]
    assert it["best"]["chain"] == "BİM" and it["best"]["price"] == 42.0 and it["best"]["brand"] == "ÖRNEKSÜT"
    o = client.get("/api/shopping-list/optimize").json()
    assert o["plans"][0]["stops"][0]["items"][0]["price"] == 42.0
    sz = client.get(f"/api/shopping-list/{milk}/sizes").json()
    assert {x["key"]: x["price"] for x in sz}["sut|500ml"] == 26.0   # o markanın boyu da listede
    assert {x["key"]: x["price"] for x in client.get(f"/api/shopping-list/{milk}/sizes?brand=").json()}["sut|1l"] == 30.0
    # aynı metin yeniden gönderilince marka kalır; metin değişince kalkar
    assert client.patch(f"/api/shopping-list/{milk}", json={"text": "Süt 1 L", "qty": 2}).json()["brand"] == "Örneksüt"
    client.patch(f"/api/shopping-list/{milk}", json={"brand": "Yokmarka"})
    o = client.get("/api/shopping-list/optimize").json()
    assert o["unpriced"] == [milk] and o["unpriced_info"][0]["reason"] == "brand_no_price"
    assert client.patch(f"/api/shopping-list/{milk}", json={"text": "Süt 500 ml"}).json()["brand"] is None


# ---------------------------------------------------------------- en kârlı boy

def test_sizes_sorted_and_best_value(client, resmi):
    _official([("BİM", "sut|1l", 30), ("BİM", "sut|500ml", 17), ("BİM", "sut|2l", 56), ("Migros", "sut|5l", 125),
               ("Migros", "sut|750ml", 27), ("A101", "sut|1l", 31)])
    milk = _add(client, "Süt")
    sz = client.get(f"/api/shopping-list/{milk}/sizes").json()
    assert [x["size_label"] for x in sz] == ["500 ml", "750 ml", "1 L", "2 L", "5 L"]
    assert [x["key"] for x in sz if x["best_value"]] == ["sut|5l"]   # ₺25/L
    one = next(x for x in sz if x["key"] == "sut|1l")
    assert (one["chain"], one["price"], one["unit_price_per"], one["per_unit"], one["size_amount"]) == \
        ("BİM", 30.0, 30.0, "L", 1.0)


def test_better_size_tip_and_market_saving(client, resmi):
    _official([("BİM", "sut|1l", 30), ("BİM", "sut|2l", 50), ("Migros", "sut|1l", 36), ("A101", "sut|1l", 40),
               ("BİM", "cay|1kg", 150), ("Migros", "cay|1kg", 170)])
    milk = _add(client, "Süt 1 L", qty=2)
    _add(client, "Çay 1 kg")
    o = client.get("/api/shopping-list/optimize").json()
    p = o["plans"][0]
    line = next(x for s in p["stops"] for x in s["items"] if x["id"] == milk)
    assert line["line_total"] == 60.0 and line["unit_price_per"] == 30.0
    tip = line["better_size"]
    assert (tip["key"], tip["packs"], tip["chain"], tip["total"], tip["saving"]) == ("sut|2l", 1, "BİM", 50.0, 10.0)
    assert tip["size_label"] == "2 L" and tip["unit_price_per"] == 25.0
    assert p["size_saving"] == 10.0
    # ortanca: süt (30, 36, 40) → 36; çay (150, 170) → 160: (36−30)×2 + (160−150) = 22
    assert p["saving_vs_market"] == 22.0
    prof = o["headline"]["profit"]
    assert prof["text"] == "Bu planla ₺22 tasarruf" and prof["size_text"] == "Boy önerileriyle ₺10 daha"
    # satır değişmez (yalnız öneri)
    assert client.get("/api/shopping-list").json()["items"][0]["group_key"] == "sut|1l"


def test_no_tip_when_not_cheaper_or_brand_pinned(client, resmi):
    _official([("BİM", "sut|1l", 30), ("BİM", "sut|2l", 59), ("BİM", "sut|1l", 31, "Örneksüt")])
    milk = _add(client, "Süt 1 L", qty=2)
    o = client.get("/api/shopping-list/optimize").json()
    assert "better_size" not in o["plans"][0]["stops"][0]["items"][0]   # ₺1 < ₺2 eşiği
    client.patch(f"/api/shopping-list/{milk}", json={"brand": "Örneksüt"})
    o = client.get("/api/shopping-list/optimize").json()
    assert "better_size" not in o["plans"][0]["stops"][0]["items"][0]
    assert o["headline"]["profit"] is None


def _community(chain, group, price, n=3, day=TODAY):
    with system.connect() as c:
        c.executemany("INSERT INTO community_prices (chain, group_key, name_norm, unit_price, day, contributor, region) "
                      "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                      [(chain, group, "SUT 1 L", price, day, f"ornek-katki-{chain}-{i}") for i in range(n)])


def test_exclusion_strictly_per_user_community_store(owner, resmi):  # noqa: F811
    """A "Örnek Bölge Market"te (bölgesel zincir; fiyatı başkalarının fişlerinden, topluluk) alışveriş yapar; B onu
    kapatır. Yalnız B'nin görünümünden kalkar: A görmeye devam eder, topluluk satırları olduğu gibi kalır."""
    from fastapi.testclient import TestClient

    from app import auth
    from app.api.main import app

    _official([("BİM", "sut|1l", 30)])
    _community("Örnek Bölge Market", "sut|1l", 24)
    auth.create_user("ikinci@ornek.com", "ikinci-sifre-12345")
    b = TestClient(app)
    assert b.post("/api/auth/login", json={"username": "ikinci@ornek.com",
                                           "password": "ikinci-sifre-12345"}).status_code == 200
    a = owner
    for c in (a, b):
        _add(c, "Süt 1 L")
        assert c.get("/api/shopping-list").json()["items"][0]["best"]["chain"] == "Örnek Bölge Market"
    st = b.get("/api/shopping-list/stores").json()
    assert {"chain": "Örnek Bölge Market", "hidden": False} in st["chains"]   # topluluk zinciri de seçilebilir
    assert b.put("/api/me/ui", json={"shop_hidden": {"chains": ["Örnek Bölge Market"]}}).status_code == 200

    lb = b.get("/api/shopping-list").json()
    assert lb["items"][0]["best"]["chain"] == "BİM" and lb["hidden_stores"]["chains"] == ["Örnek Bölge Market"]
    ob = b.get("/api/shopping-list/optimize").json()
    assert "Örnek Bölge Market" not in {s["chain"] for p in ob["plans"] for s in p["stops"]}
    assert [x["chain"] for x in b.get("/api/prices/groups", params={"q": "sut|1l"}).json()[0]["best"]] == ["BİM"]
    sb = next(x for x in b.get(f"/api/shopping-list/{lb['items'][0]['id']}/sizes").json() if x["key"] == "sut|1l")
    assert sb["chain"] == "BİM" and sb["hidden_count"] == 1

    la = a.get("/api/shopping-list").json()
    assert la["items"][0]["best"]["chain"] == "Örnek Bölge Market" and la["hidden_stores"]["count"] == 0
    oa = a.get("/api/shopping-list/optimize").json()
    assert oa["plans"][0]["stops"][0]["chain"] == "Örnek Bölge Market"
    assert a.get("/api/meta").json()["ui"]["shop_hidden"] == {"chains": [], "local": False}
    assert {"chain": "Örnek Bölge Market", "hidden": False} in a.get("/api/shopping-list/stores").json()["chains"]
    with system.connect() as c:   # topluluk verisi dokunulmadı
        assert c.execute("SELECT COUNT(*) FROM community_prices WHERE chain = 'Örnek Bölge Market'").fetchone()[0] == 3


def test_capabilities_and_old_shapes(client, resmi):
    caps = client.get("/api/meta").json()["capabilities"]
    for c in ("shop_store_filter", "shop_store_pin", "shop_brand_size", "shop_best_value"):
        assert c in caps
    _official([("BİM", "sut|1l", 30)], day=(date.today() - timedelta(days=1)).isoformat())
    _add(client, "Süt 1 L")
    o = client.get("/api/shopping-list/optimize").json()
    for k in ("plans", "single", "recommended_k", "headline", "unpriced_info", "source_info"):
        assert k in o
    assert o["plans"][0]["stops"][0]["items"][0]["date"] == (date.today() - timedelta(days=1)).isoformat()
