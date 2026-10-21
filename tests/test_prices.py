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
    ph = client.get("/api/prices").json()
    latte = next(p for p in ph if p["key"] == "LATTE 16 OZ")
    assert (latte["count"], latte["first_price"], latte["last_price"], latte["change_pct"]) == (3, 150, 180, 20.0)
    assert any("zam" in i["title"] for i in finance.insights("2026-10", today=date(2026, 10, 21)))
    e = tmpdb.list_entries()[0]
    assert e["location"] == "Maslak, Sarıyer/İstanbul"
    # kayıt silinince satırları da gider
    for x in tmpdb.list_entries():
        client.delete(f"/api/entries/{x['id']}")
    assert client.get("/api/prices").json() == []
