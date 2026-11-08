"""Alışveriş listesi: biçime ayrılan gruplarda varsayılan boy, kg / L satırında boy listesi, adda yazılan miktar
(shop_unit_sizes). 08.11.2026 telefondan: "Çekirdek kahve" → "10 adet kabul edildi"; "1 kg çekirdek kahve" kg satırında
boy listesi yok; "çekirdek kahve" + kg → "Bu ürün bu birimle satılmıyor". Zincir fiyatları ve markalar uydurmadır."""
from datetime import date

import pytest

from app import price_sources, shopping, system

TODAY = date.today().isoformat()


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)
    from app import shop_stores

    shop_stores._cache.clear()


def _official(rows):
    """rows: [(zincir, grup, fiyat, ad)] — ad zincir başına ayrı ürün sayılır (en yaygın boy)."""
    with system.connect() as c:
        c.executemany("INSERT INTO official_prices (chain, group_key, title, brand, price, day, region) "
                      "VALUES (?, ?, ?, 'Örnekkahve', ?, ?, 'İstanbul')",
                      [(ch, k, t, p, TODAY) for ch, k, p, t in rows])


BEANS = [("BİM", "cekirdek-kahve|250g", 180.0, "Örnekkahve Çekirdek Kahve 250 Gr"),
         ("A101", "cekirdek-kahve|250g", 175.0, "Örnekkahve Çekirdek Kahve 250 Gr"),
         ("Migros", "cekirdek-kahve|250g", 190.0, "Örnekkahve Espresso Çekirdek Kahve 250 Gr"),
         ("BİM", "cekirdek-kahve|1kg", 600.0, "Örnekkahve Çekirdek Kahve 1 Kg"),
         ("Migros", "cekirdek-kahve|1kg", 640.0, "Örnekkahve Barista Çekirdek Kahve 1 Kg"),
         # aynı grupta tek tük aykırı boy ve adetli paket: varsayılan olmamalı
         ("A101", "cekirdek-kahve|10ad", 45.0, "Örnekkahve Çekirdek Kahve 10 Adet"),
         ("BİM", "cekirdek-kahve|10ad", 44.0, "Örnekkahve Çekirdek Kahve Deneme 10 Adet"),
         ("Migros", "cekirdek-kahve|10ad", 46.0, "Örnekkahve Çekirdek Kahve Mini 10 Adet"),
         ("ŞOK", "cekirdek-kahve|10ad", 45.5, "Örnekkahve Çekirdek Kahve Tadım 10 Adet"),
         ("ŞOK", "cekirdek-kahve|5g", 9.0, "Örnekkahve Çekirdek Kahve 5 Gr"),
         ("A101", "cekirdek-kahve|5g", 9.5, "Örnekkahve Çekirdek Kahve Tadım 5 Gr")]


def test_default_size_prefers_group_unit_kind_and_skips_outliers(client, resmi):
    """En çok satırı olan tür (ağırlık) ve o türün tipik boyu: 10 adet ya da 5 g değil, 250 g."""
    assert shopping.order_sizes(["a|10ad", "a|250g", "a|1kg", "a|5g"], {"a|10ad": 4, "a|250g": 3, "a|1kg": 2,
                                                                         "a|5g": 2})[:2] == ["a|250g", "a|1kg"]
    _official(BEANS)
    it = client.post("/api/shopping-list", json={"text": "Çekirdek kahve"}).json()
    assert it["group"]["key"] == "cekirdek-kahve|250g" and it["assumed"]["size_label"] == "250 g"
    assert it["unit_mismatch"] is False


def test_kg_row_lists_weight_sizes_and_never_claims_not_sold(client, resmi):
    _official(BEANS)
    it = client.post("/api/shopping-list", json={"text": "çekirdek kahve", "qty": 1, "unit": "kg"}).json()
    assert (it["unit"], it["qty"], it["group"]["key"], it["mult"], it["unit_mismatch"]) == \
        ("kg", 1, "cekirdek-kahve|1kg", 1.0, False)                      # 1 kg için 1 kg'lık paket
    sz = client.get(f"/api/shopping-list/{it['id']}/sizes").json()
    assert [x["key"] for x in sz] == ["cekirdek-kahve|5g", "cekirdek-kahve|250g", "cekirdek-kahve|1kg"]
    assert all(x["size_unit"] == "kg" for x in sz)                       # adetli boy kg satırında yok
    one = next(x for x in sz if x["key"] == "cekirdek-kahve|1kg")
    assert one["unit_price_per"] == 600.0 and one["per_unit"] == "kg" and one["best_value"] is True

    # 2 kg: 2 kg'lık paket yok → türün en yaygın boyu (250 g × 8)
    two = client.patch(f"/api/shopping-list/{it['id']}", json={"qty": 2, "unit": "kg"}).json()
    assert two["group"]["key"] == "cekirdek-kahve|250g" and two["mult"] == 8.0 and not two["unit_mismatch"]
    # boy seçimi kg satırında: 4 × 250 g
    pick = client.patch(f"/api/shopping-list/{it['id']}", json={"qty": 1, "unit": "kg",
                                                                 "group_key": "cekirdek-kahve|250g"}).json()
    assert pick["mult"] == 4.0
    lst = client.get("/api/shopping-list").json()["items"]
    assert next(i for i in lst if i["id"] == it["id"])["best"]["line_total"] == 700.0   # 4 × A101 175


def test_unit_mismatch_only_when_no_size_of_that_kind(client, resmi):
    _official([("BİM", "kahve-karisimi|10ad", 45.0, "Örnekkahve 3'ü 1 Arada 10 Adet")])
    it = client.post("/api/shopping-list", json={"text": "3ü1 arada", "qty": 1, "unit": "kg"}).json()
    assert it["unit_mismatch"] is True                                   # yalnız adetli paket var
    assert client.get(f"/api/shopping-list/{it['id']}/sizes").json() == []
    ok = client.patch(f"/api/shopping-list/{it['id']}", json={"qty": 2, "unit": "adet"}).json()
    assert ok["unit_mismatch"] is False and ok["group"]["key"] == "kahve-karisimi|10ad"


def test_unit_change_drops_pinned_size_of_other_kind(client, resmi):
    _official(BEANS)
    it = client.post("/api/shopping-list", json={"text": "Çekirdek kahve"}).json()
    client.patch(f"/api/shopping-list/{it['id']}", json={"group_key": "cekirdek-kahve|10ad"})
    kg = client.patch(f"/api/shopping-list/{it['id']}", json={"qty": 1, "unit": "kg"}).json()
    assert kg["group"]["key"] == "cekirdek-kahve|1kg" and not kg["unit_mismatch"]


def test_amount_in_name_is_read_on_add_and_edit(client, resmi):
    _official(BEANS)
    a = client.post("/api/shopping-list", json={"text": "1 kg çekirdek kahve", "qty": 1, "unit": "kg"}).json()
    assert (a["text"], a["qty"], a["unit"]) == ("çekirdek kahve", 1, "kg")
    b = client.post("/api/shopping-list", json={"text": "Elma"}).json()
    e = client.patch(f"/api/shopping-list/{b['id']}", json={"text": "2,5 kg elma", "qty": 1, "unit": "adet"}).json()
    assert (e["text"], e["qty"], e["unit"]) == ("elma", 2.5, "kg")
    e = client.patch(f"/api/shopping-list/{b['id']}", json={"text": "500 g çekirdek kahve", "qty": 1, "unit": "kg"}).json()
    assert (e["text"], e["qty"], e["unit"]) == ("çekirdek kahve", 500, "g")
    assert e["group"]["key"] == "cekirdek-kahve|250g" and e["mult"] == 2.0
    # adda bilinen paket boyu ve adet: boy olarak kalır (1 paket 1 kg)
    p = client.patch(f"/api/shopping-list/{b['id']}", json={"text": "çekirdek kahve 1 kg", "qty": 1, "unit": "adet"}).json()
    assert (p["text"], p["qty"], p["unit"], p["group"]["key"]) == ("çekirdek kahve 1 kg", 1, "adet", "cekirdek-kahve|1kg")


def test_parent_name_shows_forms(client, resmi):
    """Yalnız "Kahve" yazılınca (üst ad, kendi satırı yok) biçimlerin boyları: biçim adıyla ("Türk Kahvesi · 100 g")."""
    _official([("BİM", "turk-kahvesi|100g", 80.0, "Örnekkahve Türk Kahvesi 100 Gr"),
               ("A101", "turk-kahvesi|100g", 78.0, "Örnekkahve Türk Kahvesi 100 Gr"),
               ("BİM", "hazir-kahve|100g", 150.0, "Örnekkahve Gold Kahve 100 Gr"),
               ("BİM", "kahve-karisimi|18g", 6.5, "Örnekkahve 3'ü 1 Arada 18 Gr"),
               ("A101", "kahve-karisimi|18g", 6.0, "Örnekkahve 3'ü 1 Arada 18 Gr"),
               ("ŞOK", "kahve-karisimi|18g", 6.2, "Örnekkahve 3'ü 1 Arada Fındıklı 18 Gr")])
    it = client.post("/api/shopping-list", json={"text": "Kahve"}).json()
    assert it["group"]["key"] == "turk-kahvesi|100g"
    assert it["assumed"]["size_label"] == "Türk Kahvesi · 100 g" and it["group"]["label"] == "Türk Kahvesi · 100 g"
    sz = client.get(f"/api/shopping-list/{it['id']}/sizes").json()
    assert [x["size_label"] for x in sz] == ["Türk Kahvesi · 100 g", "Hazır Kahve · 100 g", "Kahve Karışımı · 18 g"]
    pin = client.patch(f"/api/shopping-list/{it['id']}", json={"group_key": "hazir-kahve|100g"}).json()
    assert pin["group"]["key"] == "hazir-kahve|100g" and pin["group"]["pinned"] and not pin["unit_mismatch"]
    assert "hazir-kahve|100g" in [x["key"] for x in client.get(f"/api/shopping-list/{it['id']}/sizes").json()]


def test_capability(client):
    assert "shop_unit_sizes" in client.get("/api/meta").json()["capabilities"]
