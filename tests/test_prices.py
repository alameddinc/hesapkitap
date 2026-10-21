"""Fişten fiyat takibi ve adres."""
from app import db, finance


def _receipt(client, day, price, merchant="Espressolab", qty=1):
    return client.post("/api/entries", json={
        "entry_date": day, "description": merchant, "amount": price * qty, "kind": "gider", "category": "Kafe & Restoran",
        "payment": "nakit", "location": "Maslak, Sarıyer/İstanbul",
        "items": [{"name": "Latte 16 Oz", "qty": qty, "amount": price * qty}, {"name": "Su", "qty": 1, "amount": None}]}).json()


def test_product_key_merges_spellings():
    assert db.product_key("Latte 16 Oz") == db.product_key("LATTE 16OZ") == "LATTE 16 OZ"


def test_price_history_and_insight(client, tmpdb):
    from datetime import date

    _receipt(client, "2026-08-01", 150)
    _receipt(client, "2026-10-15", 90, qty=2)            # birim 90 → değil; adet 2, satır 180
    _receipt(client, "2026-10-20", 180)
    ph = client.get("/api/prices").json()["products"]
    latte = next(p for p in ph if p["key"] == "LATTE 16 OZ")
    assert (latte["count"], latte["first_price"], latte["last_price"], latte["change_pct"]) == (3, 150, 180, 20.0)
    assert any("zam" in i["title"] for i in finance.insights("2026-10", today=date(2026, 10, 21)))
    e = tmpdb.list_entries()[0]
    assert e["location"] == "Maslak, Sarıyer/İstanbul"
    # kayıt silinince satırları da gider
    for x in tmpdb.list_entries():
        client.delete(f"/api/entries/{x['id']}")
    assert client.get("/api/prices").json()["products"] == []


def _buy(client, merchant, day, items, total):
    return client.post("/api/entries", json={"entry_date": day, "description": merchant, "amount": total, "kind": "gider",
                                             "category": "Market & Gıda", "payment": "nakit", "items": items}).json()


def test_products_single_unknown_inferred_and_store_compare(client, tmpdb):
    # File: gofret 49,90 + peynir 0,750 kg (89,90/kg) + fiyatı okunamayan su (toplamdan çıkar)
    _buy(client, "File", "2026-10-20", [
        {"name": "ULKER GOFRET", "product": "Ülker Çikolatalı Gofret 36 g", "qty": 1, "unit": "adet", "amount": 49.90},
        {"name": "PEYNIR", "product": "Beyaz Peynir", "qty": 0.75, "unit": "kg", "amount": 67.43},
        {"name": "SU 1,5L", "product": "Su 1,5 L", "qty": 1, "amount": None}], total=127.33)
    # BİM: aynı gofret farklı yazılışla, iki fiyatsız ürün (tahmin edilemez)
    _buy(client, "BİM", "2026-10-21", [
        {"name": "ÜLKER ÇİK.GOFRET 36GR", "product": "Ülker Çikolatalı Gofret 36 g", "qty": 2, "amount": 79.80},
        {"name": "EKMEK", "product": "Ekmek", "qty": 1, "amount": None},
        {"name": "YUMURTA", "product": "Yumurta 10'lu", "qty": 1, "amount": None}], total=150)
    ps = {p["product"]: p for p in client.get("/api/prices").json()["products"]}
    g = ps["Ülker Çikolatalı Gofret 36 g"]
    assert [(s["store"], s["price"], s.get("cheapest", False)) for s in g["stores"]] == [("BİM", 39.9, True), ("File", 49.9, False)]
    assert g["change_pct"] == -20.0
    assert ps["Beyaz Peynir"]["unit"] == "kg" and ps["Beyaz Peynir"]["last_price"] == 89.91   # kg fiyatı
    assert ps["Su 1,5 L"]["last_price"] == 10.0 and ps["Su 1,5 L"]["last_estimated"]           # 127,33 − 117,33
    assert ps["Ekmek"]["last_price"] is None and ps["Ekmek"]["count"] == 1                      # tek alım, fiyatsız da görünür


def test_parse_accepts_multiple_photos(client, monkeypatch):
    from app import receipt

    seen = {}

    def fake(data, mime, cats, split, more=None):
        seen["n"] = 1 + len(more or [])
        return {"type": "receipt", "merchant": "Bim", "total": 10, "items": [], "category": "Market & Gıda",
                "legal_name": None, "confidence": "yüksek", "date": None, "payment": "nakit"}

    monkeypatch.setattr(receipt, "parse_document", fake)
    files = [("file", (f"p{i}.jpg", b"\xff\xd8x", "image/jpeg")) for i in range(3)]
    assert client.post("/api/documents/parse", files=files).status_code == 200 and seen["n"] == 3


def test_store_branch_brand_breakdown_and_filters(client, tmpdb):
    def buy(store, branch, day, price, brand="Kahve Dünyası"):
        client.post("/api/entries", json={"entry_date": day, "description": store, "amount": price, "kind": "gider",
                                          "category": "Kafe & Restoran", "payment": "nakit", "branch": branch,
                                          "items": [{"name": "LATTE", "product": "Latte", "brand": brand, "qty": 1, "amount": price}]})
    buy("KAHVE DÜNYASI", "Fıstıkağacı", "2026-11-05", 120)
    buy("Kahve Dünyası", "Kadıköy", "2026-11-20", 130)
    buy("Kahve Konağı", None, "2026-11-21", 90, brand="Kahve Konağı")
    r = client.get("/api/prices").json()
    stores = {s["store"]: s for s in r["stores"]}
    assert set(stores) == {"Kahve Dünyası", "Kahve Konağı"}            # ilk kelimeye göre karışmaz
    kd = stores["Kahve Dünyası"]
    assert kd["branches"] == ["Fıstıkağacı", "Kadıköy"] and kd["visits"] == 2
    assert kd["products"][0]["history"][0] == {"date": "2026-11-05", "price": 120, "store": "Kahve Dünyası",
                                               "branch": "Fıstıkağacı", "qty": 1, "name": "LATTE"}
    assert {b["brand"] for b in r["brands"]} == {"Kahve Dünyası", "Kahve Konağı"}
    only = client.get("/api/prices", params={"store": "kahve dünyası", "since": "2026-11-10"}).json()["products"]
    assert [p["last_price"] for p in only] == [130] and only[0]["count"] == 1
