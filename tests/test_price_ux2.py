"""Fiyat deneyimi v2: tutarlı gruplama (marka → ürün, katalog, tutarlılık işi, kalite ölçüleri), birleşen yazılışlar,
öneri, alışveriş listesinde boy varsayımı ve "Nereye gideyim?" cümlesi, ürün önerisi (advice), topluluk fiyatının
sağlamlık kuralları, push rotaları. Tüm adlar ve fiyatlar uydurma."""
from datetime import date, timedelta

import pytest

from app import community_prices as cp
from app import price_sources, product_catalog, product_groups, push, shopping, system
from tests.test_book import owner  # noqa: F401  (fixture)

TODAY = date.today()
T = TODAY.isoformat()


def _d(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


@pytest.fixture(autouse=True)
def _fresh_catalog():
    product_catalog.reset()
    yield
    product_catalog.reset()


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)


def _key(name, brand=None):
    g = product_groups.rule_group(name, brand)
    return g and g["key"]


def _official(rows):
    """[(zincir, grup, ad, fiyat[, gün, bölge])]"""
    with system.connect() as c:
        for r in rows:
            chain, g, title, price = r[:4]
            day = r[4] if len(r) > 4 else T
            region = r[5] if len(r) > 5 else "İstanbul"
            c.execute("INSERT INTO official_prices (chain, group_key, title, price, day, region) VALUES (?, ?, ?, ?, ?, ?)",
                      (chain, g, title, price, day, region))


def _buy(client, store, items, amount=None, day=T, category="Market & Gıda"):
    total = amount if amount is not None else round(sum(i["amount"] for i in items), 2)
    return client.post("/api/entries", json={"entry_date": day, "description": store, "amount": total, "kind": "gider",
                                             "category": category, "payment": "nakit", "items": items}).json()


# ---------------------------------------------------------------- 1. gruplama

GOLDEN = [  # (ad, marka, beklenen anahtar) — fiş yazılışları uydurma
    ("COLA TURKA 330ML", None, "kola|330ml"),
    ("Cola Turka 2,5 L", None, "kola|2.5l"),
    ("COCA COLA ZERO 1L", None, "kola|1l"),
    ("PEPSI 1 LT", None, "kola|1l"),
    ("Cola turka 50", None, "kola"),                      # birimsiz rakam boy değil
    ("COLA TURKA 50 CL", None, "kola|500ml"),
    ("FANTA 1L", None, "portakalli-gazoz|1l"),
    ("SPRITE 330 ML", None, "limonlu-gazoz|330ml"),
    ("7UP 1 L", None, "limonlu-gazoz|1l"),
    ("LIPTON ICE TEA SEFTALI 1L", None, "soguk-cay|1l"),
    ("NESCAFE GOLD 100 G", None, "hazir-kahve|100g"),       # kahvenin biçimi (granül) ayrı grup
    ("NUTELLA BISKUVI 304 G", None, "biskuvi|304g"),        # markanın ürünü değil, addaki ürün
    ("TORKU SUT 1L", None, "sut|1l"),
    ("ORNEKKOY BEYAZ PEYNIR 500 G", "Örnekköy", "beyaz-peynir|500g"),
]


@pytest.mark.parametrize("name,brand,want", GOLDEN)
def test_golden_groups(name, brand, want):
    assert _key(name, brand) == want


def _catalog():
    return [
        {"product_id": "c1", "title": "Örnekmarka Gazlı İçecek 330 Ml", "brand": "Örnekmarka", "volume": "330 ML",
         "main_category": "Gazlı İçecekler", "categories": "Gazlı Içecek Kola|Kola"},
        {"product_id": "c2", "title": "Örnekmarka Gazlı İçecek 1 Lt", "brand": "Örnekmarka", "volume": "1 LT",
         "main_category": "Gazlı İçecekler", "categories": "Kola"},
        {"product_id": "c3", "title": "Zirvetepe Çay Bisküvisi 200 Gr", "brand": "Zirvetepe", "volume": "200 GR",
         "main_category": "Bisküvi ve Kraker", "categories": "Bisküviler"},
    ]


def test_catalog_brands_and_match(client):
    assert _key("ORNEKMARKA GAZLI ICECEK 330ML") is None                 # kural bilmiyor
    got = product_catalog.store(_catalog())
    assert got == {"products": 3, "brands": 2, "generic": 3}
    # raf etiketinden genel ad: c1/c2 → Kola; c3 kuraldan (marka atıldı) → Bisküvi
    g, src = product_groups.resolve("ORNEKMARKA GAZ.ICECEK 330 ML")
    assert (g["key"], src) == ("kola|330ml", "catalog")
    assert product_groups.resolve("ZIRVETEPE CAY BISKUVISI 200G")[0]["key"] == "biskuvi|200g"  # marka listesi katalogdan
    out = product_groups.groups_for([{"name": "ORNEKMARKA GAZLI ICECEK 1L"}])
    assert out[0]["key"] == "kola|1l" and not out[0].get("pending")
    st = product_catalog.status()
    assert (st["products"], st["with_generic"], st["brands"]) == (3, 3, 2)


def test_consistency_rekeys_conflicting_ai_rows(client):
    with system.connect() as c:  # aynı biçim iki ayrı gruba düşmüş: yapay zekâ "gazli-icecek" demiş
        product_groups._store(c, "ORNEKMARKA GAZOZ 330 ML", product_groups._compose("Gazli Icecek", {"value": 330.0,
                              "base": "ml", "pack": 1}), "ai")
        product_groups._store(c, "ORNEKMARKA GAZOZ 1 L", product_groups._compose("Gazoz", {"value": 1000.0, "base": "ml",
                              "pack": 1}), "rule")
        got = product_groups.consistency(c)
        keys = dict(c.execute("SELECT name_norm, group_key FROM product_groups").fetchall())
    assert got["conflicts"] >= 1 and got["rekeyed"] >= 1
    assert keys["ORNEKMARKA GAZOZ 330 ML"] == "gazoz|330ml"                 # kural yapay zekânın önünde
    q = product_groups.quality()
    assert q["conflicts"]["count"] >= 1 and q["conflicts"]["top"][0]["resolved_to"] == "Gazoz"
    assert {"names", "grouped", "grouped_pct", "pending", "by_source", "wide_spread", "catalog"} <= set(q)


def test_quality_wide_spread(client):
    _official([("BİM", "sut|1l", "Örnek Süt 1 L", 30.0), ("A101", "sut|1l", "Örnek Süt 1 L", 32.0),
               ("Migros", "sut|1l", "Örnek Özel Süt 1 L", 140.0)])
    q = product_groups.quality()
    assert q["wide_spread"]["count"] == 1 and q["wide_spread"]["top"][0]["group_key"] == "sut|1l"


def test_regroup_version_bumped():
    assert product_groups.GROUPS_VERSION >= 4


# ---------------------------------------------------------------- 2. birleşen yazılışlar ve öneri

def test_near_duplicate_products_merge(client):
    _buy(client, "Örnek Büfe", [{"name": "Zirve Slims Mavi Sigara", "qty": 1, "amount": 100}], day=_d(3))
    _buy(client, "Örnek Büfe", [{"name": "Zirve Slims Mavi Sigara", "qty": 1, "amount": 100}], day=_d(2))
    _buy(client, "Örnek Büfe", [{"name": "Zirve Slim Mavi Sigara", "qty": 1, "amount": 105}], day=_d(1))
    _buy(client, "Örnek Büfe", [{"name": "Süt 1 L", "qty": 1, "amount": 40}, {"name": "Süt 2 L", "qty": 1, "amount": 75}])
    prods = client.get("/api/prices").json()["products"]
    cig = [p for p in prods if "ZIRVE" in p["key"]]
    assert len(cig) == 1 and cig[0]["count"] == 3
    assert cig[0]["product"] == "Zirve Slims Mavi Sigara" and cig[0]["aliases"] == ["Zirve Slim Mavi Sigara"]
    assert len([p for p in prods if p["key"].startswith("SUT")]) == 2         # boy farklı: birleşmez
    assert all("_entries" not in p for p in prods)


def test_suggest_and_did_you_mean(client):
    _buy(client, "Örnek Büfe", [{"name": "Zirve Slims Mavi Sigara", "qty": 1, "amount": 100}])
    r = client.get("/api/prices/suggest", params={"q": "zirv"}).json()
    assert r["items"][0]["name"] == "Zirve Slims Mavi Sigara" and r["items"][0]["last_price"] == 100.0
    assert r["did_you_mean"] is None
    r = client.get("/api/prices/suggest", params={"q": "Zirve Slim Mavi Sigara"}).json()
    assert r["did_you_mean"]["name"] == "Zirve Slims Mavi Sigara"
    r = client.get("/api/prices/suggest", params={"q": "soğuk"}).json()
    assert any(i["name"] == "Soğuk Çay" and i["group_label"] == "Soğuk Çay" for i in r["items"])
    assert client.get("/api/prices/suggest", params={"q": ""}).json() == {"items": [], "did_you_mean": None}


# ---------------------------------------------------------------- 3–4. alışveriş listesi

def test_sizeless_item_resolves_to_common_size(client, resmi):
    _official([("BİM", "sut|1l", "Örnek Süt 1 L", 31.5), ("A101", "sut|1l", "Örnek Süt 1 L", 34.0),
               ("ŞOK", "sut|1l", "Örnek Süt 1 L", 33.0), ("Migros", "sut|500ml", "Örnek Süt 500 Ml", 20.0),
               ("BİM", "elma|kg", "Elma Kg", 40.0)])
    a = client.post("/api/shopping-list", json={"text": "6x Süt"}).json()
    assert (a["text"], a["qty"]) == ("Süt", 6)
    assert a["group"]["key"] == "sut|1l" and a["assumed"] == {"key": "sut|1l", "label": a["group"]["label"],
                                                               "size_label": "1 L"}
    elma = client.post("/api/shopping-list", json={"text": "Elma"}).json()
    assert elma["group"]["key"] == "elma|kg" and elma["assumed"]["size_label"] == "kg"
    items = client.get("/api/shopping-list").json()["items"]
    best = {i["id"]: i["best"] for i in items}
    assert (best[a["id"]]["chain"], best[a["id"]]["price"], best[a["id"]]["per_unit"]) == ("BİM", 31.5, "L")
    sizes = client.get(f"/api/shopping-list/{a['id']}/sizes").json()
    # boy listesi küçükten büyüğe (shop_best_value; önceden en yaygın önceydi)
    assert [s["key"] for s in sizes] == ["sut|500ml", "sut|1l"] and sizes[0]["size_label"] == "500 ml"
    p = client.patch(f"/api/shopping-list/{a['id']}", json={"group_key": "sut|500ml"}).json()
    assert p["group"]["key"] == "sut|500ml" and p["assumed"] is None and p["group"]["pinned"]
    p = client.patch(f"/api/shopping-list/{a['id']}", json={"group_key": None}).json()
    assert p["group"]["key"] == "sut|1l" and p["assumed"]
    assert client.patch(f"/api/shopping-list/{a['id']}", json={"group_key": "KÖTÜ anahtar"}).status_code == 422
    assert client.get("/api/shopping-list/999/sizes").status_code == 404
    opt = client.get("/api/shopping-list/optimize").json()
    line = opt["plans"][0]["stops"][0]["items"][0]
    assert line["assumed"]["size_label"] in ("1 L", "kg")


def test_optimize_headline_and_unpriced(client, resmi):
    _official([("BİM", "sut|1l", "Örnek Süt 1 L", 31.5), ("BİM", "ekmek", "Ekmek", 10.0),
               ("Migros", "sut|1l", "Örnek Süt 1 L", 45.0), ("Migros", "ekmek", "Ekmek", 15.0)])
    client.post("/api/shopping-list", json={"text": "Süt 1 L", "qty": 2})
    client.post("/api/shopping-list", json={"text": "Ekmek"})
    odd = client.post("/api/shopping-list", json={"text": "Zzqx Örnek Şey"}).json()
    opt = client.get("/api/shopping-list/optimize").json()
    h = opt["headline"]
    assert h["title"] == "Önerimiz: Hepsini BİM'den al — ₺73" and h["cta"] == "BİM'e git"
    assert (h["subtitle"], h["count"], h["total"], h["chains"]) == ("2 ürün", 2, 73.0, ["BİM"])
    assert opt["unpriced_info"][0]["id"] == odd["id"] and opt["unpriced_info"][0]["reason"] in ("no_group", "pending")
    assert opt["empty"] is None


def test_headline_two_stops_and_empty(client, resmi):
    client.post("/api/shopping-list", json={"text": "Zzqx Örnek Şey"})
    opt = client.get("/api/shopping-list/optimize").json()
    assert opt["headline"] is None and opt["empty"]["title"] == "Henüz fiyat yok"
    p = {"k": 2, "stops": [{"chain": "BİM"}, {"chain": "Migros"}], "total": 1200.0, "covered": 6}
    h = shopping.headline(p, {"total": 1540.0, "covered": 6}, 6)
    assert (h["title"], h["subtitle"], h["cta"]) == ("Önerimiz: BİM + Migros — ₺1.200", "₺340 daha ucuz", "BİM + Migros")


def test_add_from_product_detail_with_group(client):
    r = client.post("/api/shopping-list", json={"text": "Torku Süt 1L", "group_key": "sut|1l"}).json()
    assert r["group"]["key"] == "sut|1l" and r["group"]["pinned"]


# ---------------------------------------------------------------- 5. ürün önerisi

def test_advice_cheaper_at_and_good_price(client, resmi):
    _buy(client, "Migros", [{"name": "Torku Süt 1 L", "qty": 6, "amount": 348.0}])        # birim 58
    _buy(client, "BİM", [{"name": "Ekmek", "qty": 1, "amount": 9.0}])
    _official([("BİM", "sut|1l", "Örnek Süt 1 L", 31.5), ("A101", "sut|1l", "Örnek Süt 1 L", 34.95),
               ("ŞOK", "sut|1l", "Örnek Süt 1 L", 36.0), ("BİM", "ekmek", "Ekmek", 10.0), ("A101", "ekmek", "Ekmek", 12.0)])
    prods = {p["group"]["key"]: p for p in client.get("/api/prices").json()["products"] if p.get("group")}
    milk = prods["sut|1l"]
    assert milk["market"] is True and milk["market_ref"]["median"] == 34.95 and milk["market_ref"]["cheapest_chain"] == "BİM"
    a = milk["advice"]
    assert (a["kind"], a["chain"], a["price"], a["paid"], a["diff"], a["qty"], a["saving"]) == \
        ("cheaper_at", "BİM", 31.5, 58.0, 26.5, 6, 159.0)
    assert prods["ekmek"]["advice"]["kind"] == "good_price" and prods["ekmek"]["advice"]["median"] == 11.0


def test_advice_store_compare_for_cafe(client, resmi):
    _buy(client, "Örnek Kafe", [{"name": "Latte", "qty": 1, "amount": 180.0}], category="Kafe & Restoran")
    _buy(client, "Deneme Kahvecisi", [{"name": "Latte", "qty": 1, "amount": 230.0}], category="Kafe & Restoran")
    _official([("BİM", "latte", "Örnek Hazır Latte", 20.0)])
    p = next(p for p in client.get("/api/prices").json()["products"] if p["key"] == "LATTE")
    assert p["market"] is False and p["market_ref"] is None
    assert p["advice"] == {"kind": "store_compare", "cheapest": "Örnek Kafe", "diff": 50.0,
                           "stores": [{"store": "Örnek Kafe", "price": 180.0, "date": T},
                                      {"store": "Deneme Kahvecisi", "price": 230.0, "date": T}]}


def test_own_record_never_altered_by_market_data(client, resmi):
    e = _buy(client, "Migros", [{"name": "Torku Süt 1 L", "qty": 1, "amount": 58.0}])
    _official([("BİM", "sut|1l", "Örnek Süt 1 L", 31.5)])
    for uid in (1, 2, 3):
        cp.contribute(uid, "Migros", None, [{"name": "Süt 1 L", "qty": 1, "amount": 30.0}], T, receipt_total=30.0)
    client.get("/api/prices")
    client.get("/api/prices/groups", params={"q": "süt"})
    from app import db

    with db.connect() as c:
        assert c.execute("SELECT amount FROM entries WHERE id = ?", (e["id"],)).fetchone()[0] == 58.0
        r = c.execute("SELECT amount, unit_price FROM receipt_items WHERE entry_id = ?", (e["id"],)).fetchone()
    assert (r[0], r[1]) == (58.0, 58.0)


# ---------------------------------------------------------------- topluluk fiyatının kuralları

def test_unsound_receipt_not_contributed():
    bad = [{"name": "Süt 1 L", "qty": 1, "amount": 36}, {"name": "Ekmek", "qty": 1, "amount": 10}]
    assert cp.contribute(1, "A101", None, bad, T, receipt_total=56.0) == 0                 # toplam tutmuyor
    mis = [{"name": "Süt 1 L", "qty": 2, "unit_price": 18, "amount": 30}]                  # adet × birim tutmuyor
    assert cp.contribute(1, "A101", None, mis, T, receipt_total=30.0) == 0
    assert cp.contribute(1, "A101", None, bad, T, receipt_total=46.0) == 2


def test_save_entry_skips_unsound_receipt(client):
    client.post("/api/entries", json={"entry_date": T, "description": "A101", "amount": 99.0, "kind": "gider",
                                      "category": "Market & Gıda", "payment": "nakit", "receipt_file": "yok.jpg",
                                      "items": [{"name": "Süt 1 L", "qty": 1, "amount": 36}]})
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM community_prices").fetchone()[0] == 0


def test_discounted_rows_separate_and_window(client):
    for uid, p in ((1, 40), (2, 41), (3, 42)):
        cp.contribute(uid, "A101", None, [{"name": "Süt 1 L", "qty": 1, "amount": p}], T)
    for uid in (4, 5, 6):
        cp.contribute(uid, "A101", None, [{"name": "Süt 1 L", "qty": 1, "amount": 40, "discount": 10}], T)
    m = cp.medians()[("A101", "sut|1l")]
    assert (m["median"], m["n"], m["discounted"]) == (41.0, 3, 30.0)
    from app import community_export

    assert all(r["unit_price"] != 30.0 for r in community_export.aggregate(T))
    with system.connect() as c:
        c.execute("UPDATE community_prices SET day = ?", (_d(8),))
    assert cp.medians() == {}                                                               # 7 günden eski


def test_community_first_with_official_and_differs(client, resmi):
    for uid, p in ((1, 38.5), (2, 38.5), (3, 39.0)):
        cp.contribute(uid, "BİM", None, [{"name": "Süt 1 L", "qty": 1, "amount": p}], T)
    _official([("BİM", "sut|1l", "Örnek Süt 1 L", 33.0), ("A101", "sut|1l", "Örnek Süt 1 L", 36.0)])
    best = price_sources.best_offers(["sut|1l"])["sut|1l"]
    bim = next(o for o in best if o["chain"] == "BİM")
    assert (bim["source"], bim["price"], bim["n"], bim["window_days"]) == ("community", 38.5, 3, 7)
    assert bim["official"]["price"] == 33.0 and bim["differs"] is True
    a101 = next(o for o in best if o["chain"] == "A101")
    assert a101["source"] == "resmi" and "official" not in a101


def test_disagreements_list(client):
    for back in (0, 1, 2):
        day = _d(back)
        for uid, p in ((1, 50), (2, 50), (3, 50)):
            cp.contribute(uid, "BİM", None, [{"name": "Süt 1 L", "qty": 1, "amount": p}], day,
                          today=TODAY)
        _official([("BİM", "sut|1l", "Örnek Süt 1 L", 40.0, day)])
    got = cp.disagreements()
    assert got and got[0]["chain"] == "BİM" and got[0]["group_key"] == "sut|1l" and got[0]["days"] >= 3
    assert got[0]["community"] == 50.0 and got[0]["official"] == 40.0 and got[0]["pct"] == 25.0


def test_admin_endpoint_has_quality(owner):  # noqa: F811
    st = owner.get("/api/admin/official-prices").json()
    assert "grouping_quality" in st and "disagreements" in st
    assert st["grouping_quality"]["conflicts"]["count"] == 0


# ---------------------------------------------------------------- 6. bildirim rotaları

def test_system_push_route_and_test_text(monkeypatch):
    from app import feedback

    sent = []
    monkeypatch.setattr(feedback, "admins", lambda: [{"id": 7}])
    monkeypatch.setattr(push, "notify", lambda uid, items, **kw: sent.extend(items) or items)
    push.ops_alert("test", None, "herhangi")
    push.ops_alert("failed", "Ankara", "Toplayıcı durdu")
    assert sent[0]["route"] == "#/diger/ayarlar/sistem" and sent[0]["body"] == \
        "Test bildirimi: fiyat toplama uyarıları çalışıyor."
    assert (sent[1]["title"], sent[1]["route"]) == ("Fiyat toplama: failed · Ankara", "#/diger/ayarlar/sistem")


# ---------------------------------------------------------------- resmî içe aktarım kipi (regional_chains)

def test_regional_mode_keeps_national_chains(monkeypatch):
    from app import price_import_bq as pib

    _official([("BİM", "sut|1l", "Örnek Süt 1 L", 33.0, T, "Kayseri"),
               ("Hakmar", "sut|1l", "Örnek Süt 1 L", 41.0, T, "Kayseri")])
    rows = [("Hakmar", "sut|1l", "Örnek Süt 1 L", None, 39.0, None, T, "", 0, "Kayseri", None, None, None, None)]
    monkeypatch.setattr(pib.bq, "query", lambda *a, **k: [])
    monkeypatch.setattr(pib, "rows_for", lambda got, day, region: (rows, {"products": 1, "matched": 1, "rows": 1,
                                                                        "skipped_chain": 0, "unmatched": 0,
                                                                        "outliers": 0, "inconsistent": {}}))
    pib.import_region(T, "Kayseri", mode="regional_chains")
    with system.connect() as c:
        got = sorted(tuple(r) for r in c.execute("SELECT chain, price FROM official_prices WHERE region = 'Kayseri'"))
        imp = c.execute("SELECT mode, status FROM official_imports WHERE region = 'Kayseri'").fetchone()
    assert got == [("BİM", 33.0), ("Hakmar", 39.0)]                    # BİM satırı yerinde
    assert tuple(imp) == ("regional_chains", "ok")
    pib.import_region(T, "Kayseri", mode="full")
    with system.connect() as c:
        assert [r[0] for r in c.execute("SELECT chain FROM official_prices WHERE region = 'Kayseri'")] == ["Hakmar"]


def test_anomaly_compares_same_mode():
    from app import price_import_bq as pib

    with system.connect() as c:
        c.execute("INSERT INTO official_imports (run_date, region, rows, products, unmatched, imported_at, mode) "
                  "VALUES (?, 'Kayseri', 1000, 1000, 0, 'x', 'full')", (_d(1),))
        c.execute("INSERT INTO official_imports (run_date, region, rows, products, unmatched, imported_at, mode) "
                  "VALUES (?, 'Kayseri', 40, 40, 0, 'x', 'regional_chains')", (_d(2),))
        few = [("Hakmar", f"urun-{i}", "x", None, 10.0) for i in range(35)]
        assert pib.anomaly(c, T, "Kayseri", few, "regional_chains") is None               # 40 → 35: aynı kip
        assert "satır sayısı" in pib.anomaly(c, T, "Kayseri", few, "full")               # 1000 → 35
