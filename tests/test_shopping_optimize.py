"""Alışveriş listesi "Nereye gideyim?": en iyi 1 / 2 / 3 duraklı plan, önerilen durak sayısı, eksik ürünler, bölge
yedeği, yalnız kendi fişleri, ekstra durak tercihi (stop_cost). Fiyatlar ve ürünler uydurmadır."""
from datetime import date

import pytest

from app import price_sources, system
from tests.test_book import owner  # noqa: F401  (fixture)

TODAY = date.today().isoformat()
# ürün → (grup, {zincir: fiyat}); 4 ürün, adet 1
OFFERS = {
    "Zeytinyağı 1 L": ("zeytinyagi|1l", {"BİM": 100, "Migros": 300, "A101": 300}),
    "Çay 1 kg": ("cay|1kg", {"BİM": 500, "Migros": 160, "A101": 500}),
    "Pirinç 1 kg": ("pirinc|1kg", {"BİM": 100, "Migros": 300, "A101": 60}),
    "Deterjan": ("deterjan", {"BİM": 200, "Migros": 300, "A101": 300}),
}


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)


def _official(rows, region="İstanbul", day=TODAY):
    """rows: [(zincir, grup, fiyat[, ad])]"""
    with system.connect() as c:
        c.executemany("INSERT INTO official_prices (chain, group_key, title, brand, price, day, region) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(ch, g, (r[3] if len(r) > 3 else f"Örnek {g}"), "Örnekmarka", p, day, region)
                       for r in rows for ch, g, p in [r[:3]]])


def _seed_offers():
    _official([(ch, g, p) for g, by in OFFERS.values() for ch, p in by.items()])


def _add_items(c):
    return {t: c.post("/api/shopping-list", json={"text": t}).json()["id"] for t in OFFERS}


def test_two_stops_recommended_third_not_worth_it(client, resmi):
    _seed_offers()
    ids = _add_items(client)
    r = client.get("/api/shopping-list/optimize").json()
    assert r["region"] == "İstanbul" and r["date"] == TODAY and r["stop_cost"] == 100
    p1, p2, p3 = r["plans"]
    assert [p["k"] for p in r["plans"]] == [1, 2, 3]
    assert ([s["chain"] for s in p1["stops"]], p1["total"]) == (["BİM"], 900.0)
    assert sorted(s["chain"] for s in p2["stops"]) == ["BİM", "Migros"] and p2["total"] == 560.0
    assert p2["saving_vs_single"] == 340.0 and p2["saving_vs_prev"] == 340.0
    assert p3["total"] == 520.0 and p3["saving_vs_prev"] == 40.0 and p3["saving_vs_single"] == 380.0
    assert r["recommended_k"] == 2 and p2["recommended"] and not p3["recommended"]
    assert (p1["extra_vs_best"], p2["extra_vs_best"], p3["extra_vs_best"]) == (340.0, 0.0, -40.0)
    bim = next(s for s in p2["stops"] if s["chain"] == "BİM")
    assert {x["id"] for x in bim["items"]} == {ids["Zeytinyağı 1 L"], ids["Pirinç 1 kg"], ids["Deterjan"]}
    assert bim["total"] == 400.0
    line = next(x for x in bim["items"] if x["id"] == ids["Zeytinyağı 1 L"])
    assert line["group_label"] == "Zeytinyağı · 1 L" and line["brand"] == "Örnekmarka" and line["source"] == "resmi"
    assert line["unit_price_per"] == 100.0 and line["per_unit"] == "L" and line["region"] == "ulusal"
    assert line["date"] == TODAY and line["line_total"] == 100.0
    assert [(s["chain"], s["total"], s["vs_recommended"]) for s in r["single"]] == \
        [("BİM", 900.0, 340.0), ("Migros", 1060.0, 500.0), ("A101", 1160.0, 600.0)]
    assert r["one_store"]["chain"] == "BİM" and r["one_store"]["missing"] == []
    assert r["unpriced"] == [] and r["priced"] == 4 and r["sources"][0].startswith("Kaynak: Market Fiyatı")
    assert r["source_info"] == {"kinds": ["resmi"], "resmi_date": TODAY, "national": True}


def test_quantity_and_missing_items_per_chain(client, resmi):
    _official([("BİM", "sut|1l", 30), ("Migros", "sut|1l", 40), ("Migros", "cay|1kg", 200)])
    milk = client.post("/api/shopping-list", json={"text": "Süt 1 L", "qty": 3}).json()["id"]
    tea = client.post("/api/shopping-list", json={"text": "Çay 1 kg"}).json()["id"]
    odd = client.post("/api/shopping-list", json={"text": "Örnek Şey"}).json()["id"]
    done = client.post("/api/shopping-list", json={"text": "Un 1 kg"}).json()["id"]
    client.patch(f"/api/shopping-list/{done}", json={"done": True})
    r = client.get("/api/shopping-list/optimize?max_stops=2").json()
    assert r["unpriced"] == [odd] and r["max_stops"] == 2
    assert [(s["chain"], s["total"], s["covered"], s["missing"]) for s in r["single"]] == \
        [("Migros", 320.0, 2, []), ("BİM", 90.0, 1, [tea])]       # çok karşılayan önce
    p1, p2 = r["plans"]
    assert p1["stops"][0]["chain"] == "Migros" and p1["total"] == 320.0 and p1["missing"] == []
    assert p2["total"] == 290.0 and p2["saving_vs_prev"] == 30.0 and r["recommended_k"] == 1   # 30 ₺ < 100 ₺
    assert r["single"][1]["vs_recommended"] == -230.0


def test_more_coverage_wins_extra_stop(client, resmi):
    _official([("BİM", "sut|1l", 30), ("BİM", "deterjan", 90), ("ŞOK", "cay|1kg", 200)])
    for t in ("Süt 1 L", "Deterjan", "Çay 1 kg"):
        client.post("/api/shopping-list", json={"text": t})
    r = client.get("/api/shopping-list/optimize").json()
    assert [(p["k"], p["covered"], len(p["missing"])) for p in r["plans"]] == [(1, 2, 1), (2, 3, 0)]
    assert r["recommended_k"] == 2                                  # eksik ürünü bulan durak, pahalı olsa da


def test_stop_cost_pref_and_param(client, resmi):
    _seed_offers()
    _add_items(client)
    assert client.get("/api/meta").json()["ui"]["stop_cost"] == 100
    assert client.put("/api/me/ui", json={"stop_cost": 30}).json()["stop_cost"] == 30
    assert client.get("/api/meta").json()["ui"]["stop_cost"] == 30
    assert client.get("/api/shopping-list/optimize").json()["recommended_k"] == 3   # 40 ₺ ≥ 30 ₺
    assert client.get("/api/shopping-list/optimize?stop_cost=400").json()["recommended_k"] == 1
    assert client.put("/api/me/ui", json={"stop_cost": -1}).status_code == 422
    assert "stop_cost" not in client.put("/api/me/ui", json={"stop_cost": None}).json()
    assert client.get("/api/shopping-list/optimize").json()["recommended_k"] == 2
    assert client.get("/api/shopping-list/optimize?max_stops=4").status_code == 422
    assert "shopping_optimize" in client.get("/api/meta").json()["capabilities"]


def test_region_fallback(owner, resmi):  # noqa: F811
    _official([("Migros", "sut|1l", 45)], region="İstanbul")
    _official([("Migros", "sut|1l", 99)], region="Ankara")
    _official([("BİM", "sut|1l", 33)], region="Ankara")
    assert owner.put("/api/me/ui", json={"region": "Bursa"}).status_code == 200
    owner.post("/api/shopping-list", json={"text": "Süt 1 L"})
    r = owner.get("/api/shopping-list/optimize").json()
    assert r["region"] == "Bursa"
    got = {s["chain"]: s["items"][0] for p in r["plans"] for s in p["stops"]}
    assert got["BİM"]["region"] == "ulusal" and got["BİM"]["price"] == 33.0     # BİM: başka ilden, ulusal
    assert {s["chain"]: s["total"] for s in r["single"]} == {"BİM": 33.0, "Migros": 45.0}  # Bursa → İstanbul
    assert len(r["plans"]) == 1 and r["recommended_k"] == 1                   # tek ürün: ikinci durak boş kalırdı


def test_own_receipts_only(client):
    for store, name, price in (("A101", "Süt 1L", 36), ("BİM", "Süt 1L", 38), ("BİM", "Ekmek", 12)):
        client.post("/api/entries", json={"entry_date": TODAY, "description": store, "amount": price, "kind": "gider",
                                          "category": "Market & Gıda", "payment": "nakit",
                                          "items": [{"name": name, "qty": 1, "amount": price}]})
    client.post("/api/shopping-list", json={"text": "Süt 1 L", "qty": 2})
    client.post("/api/shopping-list", json={"text": "Ekmek"})
    r = client.get("/api/shopping-list/optimize").json()
    assert r["region"] == "İstanbul" and r["sources"] == [] and r["source_info"]["kinds"] == ["own"]
    p1 = r["plans"][0]
    assert (p1["stops"][0]["chain"], p1["total"]) == ("BİM", 88.0)
    assert [p["total"] for p in r["plans"]] == [88.0, 84.0] and r["recommended_k"] == 1
    assert all(x["source"] == "own" for x in p1["stops"][0]["items"])


def test_empty_list_and_gate(client):
    r = client.get("/api/shopping-list/optimize").json()
    assert (r["plans"], r["single"], r["recommended_k"], r["one_store"], r["unpriced"]) == ([], [], None, None, [])
    from app import plans

    assert plans.gate_for("/api/shopping-list/optimize") == "alisveris_listesi"
