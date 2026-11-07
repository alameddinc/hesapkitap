"""Fiyat artışı: fiş okunurken/kaydedilirken kendi son fiyatınla (check.price_alerts) ve günlük topluluk bildirimi."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import community_prices as cp
from app import price_alerts as pa

TODAY = date.today()


def _d(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _buy(client, store, items, day):
    return client.post("/api/entries", json={
        "entry_date": day, "description": store, "amount": sum(i["amount"] for i in items), "kind": "gider",
        "category": "Market & Gıda", "payment": "nakit", "items": items}).json()


def test_receipt_alerts_rules(client):
    _buy(client, "A101", [{"name": "Torku Süt 1L", "qty": 1, "amount": 30}, {"name": "Ekmek", "qty": 1, "amount": 10},
                          {"name": "Peynir", "qty": 1, "amount": 100}], _d(20))
    _buy(client, "BİM", [{"name": "Su", "qty": 1, "amount": 5}, {"name": "Torku Süt 1L", "qty": 1, "amount": 20}], _d(5))
    _buy(client, "A101", [{"name": "Kahve", "qty": 1, "amount": 50}, {"name": "Çay", "qty": 1, "amount": 60}], _d(70))
    items = [{"name": "Torku Süt 1L", "qty": 2, "amount": 72},   # 36 vs 30 → %20, ₺6
             {"name": "Ekmek", "qty": 1, "amount": 11},          # %10 ama ₺1 < ₺2
             {"name": "Peynir", "qty": 1, "amount": 105},        # %5
             {"name": "Kahve", "qty": 1, "amount": 80}]          # 70 gün önce: pencere dışı
    got = pa.receipt_alerts(items, "A101 YENİ MAĞAZACILIK")
    assert got == [{"index": 0, "name": "Torku Süt 1L", "paid": 36.0, "last_paid": 30.0, "last_date": _d(20),
                    "change_pct": 20.0, "chain": "A101", "note": "geçen sefer ₺30"}]   # BİM'deki ₺20 sayılmaz
    assert pa.receipt_alerts(items, "Örnek Bakkal") == []


def test_alerts_in_entry_save_and_saved_receipt(client):
    first = _buy(client, "Migros", [{"name": "Süt 1 L", "qty": 1, "amount": 30},
                                    {"name": "Ekmek", "qty": 1, "amount": 10}], _d(10))
    assert first["price_alerts"] == []
    r = _buy(client, "Migros", [{"name": "Süt 1 L", "qty": 1, "amount": 40}, {"name": "Ekmek", "qty": 1, "amount": 10}],
             _d(1))
    assert [(a["index"], a["paid"], a["last_paid"]) for a in r["price_alerts"]] == [(0, 40.0, 30.0)]
    chk = client.get(f"/api/entries/{r['id']}/receipt-items").json()["check"]
    assert [a["last_paid"] for a in chk["price_alerts"]] == [30.0]          # kaydın kendisi hariç, önceki alım
    old = client.get(f"/api/entries/{first['id']}/receipt-items").json()["check"]
    assert old["price_alerts"] == []                                         # sonraki alım sayılmaz


def test_alerts_in_document_parse(client, monkeypatch):
    from app import receipt

    _buy(client, "Migros", [{"name": "Süt 1 L", "qty": 1, "amount": 30}, {"name": "Ekmek", "qty": 1, "amount": 10}], _d(3))

    def fake(data, mime, cats, split, more=None):
        return {"type": "receipt", "merchant": "Migros", "total": 50, "category": "Market & Gıda", "legal_name": None,
                "confidence": "yüksek", "date": TODAY.isoformat(), "payment": "nakit",
                "items": [{"name": "Süt 1 L", "qty": 1, "amount": 40}, {"name": "Ekmek", "qty": 1, "amount": 10}]}

    monkeypatch.setattr(receipt, "parse_document", fake)
    doc = client.post("/api/documents/parse", files=[("file", ("f.jpg", b"\xff\xd8x", "image/jpeg"))]).json()
    assert [a["note"] for a in doc["check"]["price_alerts"]] == ["geçen sefer ₺30"]


def test_alerts_need_price_tracking_plan(client, monkeypatch):
    from app import plans

    _buy(client, "A101", [{"name": "Süt 1 L", "qty": 1, "amount": 30}, {"name": "Ekmek", "qty": 1, "amount": 10}], _d(3))
    monkeypatch.setattr(plans, "allows", lambda u, f: f != "fiyatlar")
    assert pa.receipt_alerts([{"name": "Süt 1 L", "qty": 1, "amount": 40}], "A101") == []


def test_community_rises_and_daily_push(client):
    from app import db, push

    for ago, price in ((40, 30), (10, 36)):  # A101'de 2 kez süt, son ₺36
        _buy(client, "A101", [{"name": "Torku Süt 1L", "qty": 1, "amount": price},
                              {"name": "Ekmek", "qty": 1, "amount": 12}], _d(ago))
    _buy(client, "BİM", [{"name": "Su 1,5 L", "qty": 1, "amount": 10}, {"name": "Ekmek", "qty": 1, "amount": 12}], _d(5))
    for uid, p in ((1, 41), (2, 42), (3, 43)):
        cp.contribute(uid, "A101", None, [{"name": "Süt 1L", "qty": 1, "amount": p},
                                          {"name": "Ekmek", "qty": 1, "amount": 12.5}], _d(1))
        cp.contribute(uid, "BİM", None, [{"name": "Su 1,5 L", "qty": 1, "amount": 20}], _d(1))  # 1 alım: sık değil
    rises = pa.community_rises()
    assert rises == [{"group": "sut|1l", "label": "Süt · 1 L", "chain": "A101", "last_paid": 36.0, "median": 42.0,
                      "n": 3, "change_pct": 16.7, "product_key": "TORKU SUT 1 L"}]  # ekmek ₺0,5: eşik altı
    assert pa.digest(rises) == "Sık aldığın 1 üründe fiyat arttı: Süt · 1 L A101 ₺36 → ₺42"
    with db.use_space(None, None):
        [it] = [i for i in push.collect({"id": 1}, TODAY) if i["kind"] == "fiyat"]
    assert (it["title"], it["route"]) == ("Fiyat artışı", "#/diger/fiyatlar/TORKU%20SUT%201%20L") and it["id"].startswith("fiyat:")
    now = datetime.now(ZoneInfo("Europe/Istanbul")).replace(hour=12)
    assert [x["kind"] for x in push.notify(1, [it], now, daily=True, background=False)] == ["fiyat"]
    assert push.notify(1, [it], now, daily=True, background=False) == []    # aynı artış ikinci kez gitmez
    push.set_prefs(1, {"types": {"fiyat": False}})
    with db.use_space(None, None):
        assert "fiyat" not in {i["kind"] for i in push.collect({"id": 1}, TODAY)}


def test_digest_text_many():
    rises = [{"label": f"Ürün {i}", "chain": "A101", "last_paid": 10, "median": 12} for i in range(4)]
    assert pa.digest(rises).endswith("Ürün 1 A101 ₺10 → ₺12 ve 2 ürün daha")
