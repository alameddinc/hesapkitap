"""Alışveriş listesi: addaki marka (shop_brand_auto). "çaykur turist çay 1 kg" yazan kişi başka markanın en ucuz 1 kg
çayını değil Çaykur Turist'in fiyatını görür; "Marka fark etmez" ipucu ve geri dönüş. Ürün adları raf dilinde, fiyatlar
ve katalog satırları uydurmadır (gerçek veriden kopyalanmadı)."""
import pytest

from app import product_catalog, product_groups as pg, shop_brand, system
from tests.test_shopping_v4 import _add, _official, resmi  # noqa: F401  (fixture)


@pytest.fixture
def catalog():
    """Uydurma katalog: iki Çaykur serisi, bir başka çay markası, adı sıradan kelime olan bir yağ markası."""
    product_catalog.store([
        {"product_id": "t1", "title": "Çaykur Turist Çay", "brand": "ÇAYKUR", "volume": "1000 G", "main_category": "Çay"},
        {"product_id": "t2", "title": "Çaykur Rize Çay", "brand": "ÇAYKUR", "volume": "1000 G", "main_category": "Çay"},
        {"product_id": "t3", "title": "Çaykur Turist Çay", "brand": "ÇAYKUR", "volume": "500 G", "main_category": "Çay"},
        {"product_id": "t4", "title": "Efor Dökme Çay", "brand": "EFOR", "volume": "1000 G", "main_category": "Çay"},
        {"product_id": "y1", "title": "Kristal Ayçiçek Yağı", "brand": "KRİSTAL", "volume": "1 L", "main_category": "Ayçiçek Yağı"},
    ])
    with system.connect() as c:
        assert all(g for (g,) in c.execute("SELECT generic FROM catalog_products"))
    yield
    product_catalog.store([])


def _seed_tea():
    _official([("BİM", "cay|1kg", 389.0, "ÇAYKUR", "ÇAYKUR TURİST ÇAY 1000 G"),
               ("A101", "cay|1kg", 395.5, "ÇAYKUR", "Çaykur Turist Çay 1 Kg"),
               ("BİM", "cay|1kg", 310.0, "ÇAYKUR", "ÇAYKUR RİZE ÇAY 1000 G"),
               ("ŞOK", "cay|1kg", 199.9, "EFOR", "EFOR DÖKME ÇAY 1000 G"),
               ("ŞOK", "cay|500g", 105.0, "EFOR", "EFOR DÖKME ÇAY 500 G")])


def _item(client, rid):
    return next(i for i in client.get("/api/shopping-list").json()["items"] if i["id"] == rid)


def test_typed_brand_and_series_pick_that_product(client, resmi, catalog):  # noqa: F811
    _seed_tea()
    rid = _add(client, "çaykur turist çay 1kg")
    it = _item(client, rid)
    assert it["group"]["key"] == "cay|1kg"
    assert (it["brand"], it["brand_line"], it["brand_label"], it["brand_auto"]) == ("Çaykur", "Turist", "Çaykur Turist", True)
    assert (it["best"]["chain"], it["best"]["price"]) == ("BİM", 389.0)  # Rize (310) ve Efor (199,9) değil
    assert it["brand_missing"] is False
    assert it["any_brand"]["chain"] == "ŞOK" and it["any_brand"]["price"] == 199.9 and it["any_brand"]["brand"] == "Efor"
    o = client.get("/api/shopping-list/optimize").json()
    line = o["plans"][0]["stops"][0]["items"][0]
    assert (o["plans"][0]["stops"][0]["chain"], line["price"]) == ("BİM", 389.0)
    sz = {x["key"]: x for x in client.get(f"/api/shopping-list/{rid}/sizes").json()}
    assert sz["cay|1kg"]["price"] == 389.0


@pytest.mark.parametrize("text", ["ÇAYKUR TURİST ÇAY 1 KG", "Caykur Turist cay 1 kg", "caykur turıst çay 1kg"])
def test_case_and_diacritics(client, resmi, catalog, text):  # noqa: F811
    _seed_tea()
    it = _item(client, _add(client, text))
    assert price(it) == 389.0 and it["brand_line"]


def price(it):
    return (it.get("best") or {}).get("price")


def test_possessive_generic_is_not_a_series(client, resmi, catalog):  # noqa: F811
    """"çayı" ürünün kendisidir (seri değil); grup yapay zekâdan gelmiş gibi önbellekte."""
    _seed_tea()
    with system.connect() as c:
        pg._store(c, pg.name_norm("çaykur turist çayı 1kg"), {"key": "cay|1kg", "label": "Çay · 1 kg",
                                                              "category": "Çay ve Kahve"}, "ai")
    it = _item(client, _add(client, "çaykur turist çayı 1kg"))
    assert (it["brand"], it["brand_line"], price(it)) == ("Çaykur", "Turist", 389.0)


def test_brand_only_without_series(client, resmi, catalog):  # noqa: F811
    _seed_tea()
    it = _item(client, _add(client, "Çaykur çay 1 kg"))
    assert (it["brand"], it["brand_line"], it["brand_label"]) == ("Çaykur", None, "Çaykur")
    assert (it["best"]["chain"], price(it)) == ("BİM", 310.0)   # markanın en ucuzu (seri yazılmadı)


def test_noise_word_is_not_a_series(client, resmi, catalog):  # noqa: F811
    """Markanın hiçbir ürün adında geçmeyen kelime seri sayılmaz (fiyat gizlenmesin)."""
    _seed_tea()
    it = _item(client, _add(client, "çaykur çay annemlere 1 kg"))
    assert it["brand"] == "Çaykur" and it["brand_line"] is None and price(it) == 310.0


def test_no_offer_for_brand_size_says_so(client, resmi, catalog):  # noqa: F811
    _seed_tea()
    rid = _add(client, "çaykur turist çay 500 g")
    it = _item(client, rid)
    assert it["group"]["key"] == "cay|500g" and it["brand_label"] == "Çaykur Turist"
    assert it["best"] is None and it["brand_missing"] is True
    assert (it["any_brand"]["price"], it["any_brand"]["brand"]) == (105.0, "Efor")
    info = client.get("/api/shopping-list/optimize").json()["unpriced_info"]
    assert info == [{"id": rid, "text": it["text"], "reason": "brand_no_price",
                     "hint": "Çaykur Turist 500 g için fiyat yok; “Marka fark etmez” seçersen fiyat gelir."}]


def test_switch_to_any_brand_sticks(client, resmi, catalog):  # noqa: F811
    _seed_tea()
    rid = _add(client, "çaykur turist çay 1 kg")
    p = client.patch(f"/api/shopping-list/{rid}", json={"brand": None}).json()
    assert p["brand"] is None and p["brand_line"] is None and p["brand_auto"] is False
    it = _item(client, rid)
    assert (it["best"]["chain"], price(it)) == ("ŞOK", 199.9) and it["brand"] is None
    assert "any_brand" not in it
    # metin aynı kaldıkça (düzenle → kaydet) yeniden okunmaz
    client.patch(f"/api/shopping-list/{rid}", json={"text": "çaykur turist çay 1 kg", "qty": 2})
    assert _item(client, rid)["brand"] is None
    # elle aynı markayı seçmek seriyi düşürür; aynı marka yeniden gönderilince seri kalır
    rid2 = _add(client, "çaykur turist çay 1 kg")
    assert client.patch(f"/api/shopping-list/{rid2}", json={"brand": "ÇAYKUR"}).json()["brand_line"] == "Turist"
    assert client.patch(f"/api/shopping-list/{rid2}", json={"brand": "Efor"}).json()["brand_line"] is None


def test_edit_text_reads_brand_again(client, resmi, catalog):  # noqa: F811
    _seed_tea()
    rid = _add(client, "çay 1 kg")
    assert _item(client, rid)["brand"] is None and price(_item(client, rid)) == 199.9
    p = client.patch(f"/api/shopping-list/{rid}", json={"text": "çaykur turist çay 1 kg"}).json()
    assert (p["brand"], p["brand_line"]) == ("Çaykur", "Turist")
    p = client.patch(f"/api/shopping-list/{rid}", json={"text": "Efor çay 1 kg"}).json()
    assert (p["brand"], p["brand_line"]) == ("Efor", None)
    assert price(_item(client, rid)) == 199.9
    p = client.patch(f"/api/shopping-list/{rid}", json={"text": "çay 1 kg"}).json()
    assert p["brand"] is None
    # metinle birlikte marka verilirse kişinin seçimi geçer
    p = client.patch(f"/api/shopping-list/{rid}", json={"text": "çaykur çay 1 kg", "brand": None}).json()
    assert p["brand"] is None


def test_old_rows_are_read_once(client, resmi, catalog):  # noqa: F811
    """Göçten önce eklenen satır (brand_auto NULL) ilk okumada düzelir; kişinin seçtiği marka korunur."""
    _seed_tea()
    from app import db

    with db.connect() as c:
        c.execute("INSERT INTO shopping_list (text, group_key, qty, unit) VALUES ('çaykur turist çay 1 kg', 'cay|1kg', 1, 'adet')")
        c.execute("INSERT INTO shopping_list (text, group_key, qty, unit, brand) VALUES ('çaykur çay 1 kg', 'cay|1kg', 1, "
                  "'adet', 'Efor')")
    its = client.get("/api/shopping-list").json()["items"]
    assert [(i["brand"], i["brand_line"]) for i in its] == [("Çaykur", "Turist"), ("Efor", None)]
    with db.connect() as c:
        assert [r[0] for r in c.execute("SELECT brand_auto FROM shopping_list ORDER BY id")] == [1, 0]


def test_no_false_positives(client, resmi, catalog):  # noqa: F811
    _seed_tea()
    _official([("BİM", "seker|1kg", 45.0, "ÖRNEKŞEKER"), ("BİM", "sut|1l", 30.0, "ÖRNEKSÜT")])
    for text in ("kristal şeker 1 kg",   # Kristal katalogda yağ markası: şeker değil
                 "migros süt 1 L",       # zincir adı mağaza demek olabilir
                 "nar", "sade süt 1 L", "çay 1 kg"):
        it = _item(client, _add(client, text))
        assert it["brand"] is None, text
    assert price(next(i for i in client.get("/api/shopping-list").json()["items"] if i["text"] == "kristal şeker 1 kg")) == 45.0


def test_unknown_brand_without_data_keeps_any_brand(client, resmi):  # noqa: F811
    """Veride / katalogda olmayan marka yazılmaz: fiyat eskisi gibi markadan bağımsız (gizlenmez)."""
    _official([("BİM", "sut|1l", 30.0, "ÖRNEKSÜT")])
    it = _item(client, _add(client, "pınar süt 1 L"))
    assert it["brand"] is None and price(it) == 30.0


def test_brand_from_price_data_without_catalog(client, resmi):  # noqa: F811
    _official([("BİM", "sut|1l", 30.0, "ÖRNEKSÜT"), ("A101", "sut|1l", 41.0, "Pınar", "PINAR SÜT 1 L")])
    it = _item(client, _add(client, "PINAR süt 1 L"))
    assert (it["brand"], price(it), it["any_brand"]["price"]) == ("Pınar", 41.0, 30.0)


def test_prices_search_with_brand(client, resmi, catalog):  # noqa: F811
    _seed_tea()
    g = client.get("/api/prices/groups", params={"q": "çaykur turist çay 1 kg"}).json()
    assert len(g) == 1 and g[0]["key"] == "cay|1kg" and g[0]["brand_label"] == "Çaykur Turist"
    assert g[0]["label"] == "Çaykur Turist Çay · 1 kg"
    assert [b["chain"] for b in g[0]["best"]] == ["BİM", "A101"] and g[0]["any_brand"]["price"] == 199.9
    g = client.get("/api/prices/groups", params={"q": "çaykur turist çay 500 g"}).json()
    assert g[0]["best"] == [] and g[0]["any_brand"]["brand"] == "Efor"
    # markasız arama eskisi gibi
    g = client.get("/api/prices/groups", params={"q": "çay 1 kg"}).json()
    assert g[0]["key"] == "cay|1kg" and "brand" not in g[0] and g[0]["best"][0]["price"] == 199.9


def test_detect_unit():
    assert shop_brand.detect("çaykur çay", None) is None
    assert shop_brand.detect("çaykur çay", {"key": "cay", "label": "Çay", "pending": True}) is None
    assert shop_brand.label("Çaykur", "Turist") == "Çaykur Turist" and shop_brand.label(None, "x") is None
