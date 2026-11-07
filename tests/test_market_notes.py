"""Fişte piyasadan pahalı kalemler (check.market_notes): fiş okuma, kayıtlı fişin kalemleri ve düzenleme. Uydurma veri."""
from datetime import date, timedelta

import pytest

from app import community_prices as cp
from app import market_ref, price_sources, prefs, system

TODAY = date.today()


def _d(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)


def _daily(day, mn, med, g="sut|1l", region="İstanbul", chain="BİM"):
    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO official_daily (group_key, region, day, min, median, max, cheapest_chain, "
                  "chains) VALUES (?, ?, ?, ?, ?, ?, ?, 3)", (g, region, day, mn, med, med * 1.3, chain))


ITEMS = [{"name": "Süt 1L", "qty": 1, "amount": 42.0},           # 35,95'e göre +%16,8, ₺6,05
         {"name": "Yoğurt 1 kg", "qty": 1, "amount": 60.0},      # +%9,1: eşik altı
         {"name": "Ekmek", "qty": 1, "amount": 14.0},                     # +%40 ama ₺4 → var
         {"name": "Beyaz Peynir 500 g", "qty": 2, "amount": 300.0}]    # birim 150, ortanca 120: +%25


def _market(day=None):
    d = day or _d(0)
    _daily(d, 33.0, 35.95)
    _daily(d, 50.0, 55.0, g="yogurt|1kg")
    _daily(d, 8.0, 10.0, g="ekmek")
    _daily(d, 100.0, 120.0, g="beyaz-peynir|500g", chain="A101")


def test_notes_rules_and_order(client, resmi):
    _market(_d(2))                                                         # fiş günü −2: ±3 içinde
    got = market_ref.receipt_notes(ITEMS, None, "Örnek Market", _d(0))
    keys = [n["group"] for n in got]
    assert "yogurt|1kg" not in keys
    assert [n["index"] for n in got] == [2, 3, 0]                          # en pahalı önce
    milk = next(n for n in got if n["index"] == 0)
    assert milk == {"index": 0, "name": "Süt 1L", "group": "sut|1l", "label": milk["label"], "paid_unit": 42.0,
                    "market_median": 35.95, "market_min": 33.0, "cheapest_chain": "BİM", "pct": 16.8, "day": _d(2),
                    "source": "resmi", "region": "İstanbul"}
    cheese = next(n for n in got if n["index"] == 3)
    assert (cheese["paid_unit"], cheese["pct"], cheese["cheapest_chain"]) == (150.0, 25.0, "A101")
    assert market_ref.receipt_notes(ITEMS, None, "Örnek Market", _d(10)) == []     # ±3 gün dışında referans yok


def test_small_diff_and_national_chain(client, resmi):
    _daily(_d(0), 8.0, 10.0, g="ekmek")
    assert market_ref.receipt_notes([{"name": "Ekmek", "qty": 1, "amount": 11.5}], None, "Örnek", _d(0)) == []  # ₺1,5
    _market()
    got = market_ref.receipt_notes(ITEMS, None, "BİM BİRLEŞİK MAĞAZALAR", _d(0))
    assert {n["region"] for n in got} == {"ulusal"}


def test_user_region_and_fallback(client, resmi, monkeypatch):
    _daily(_d(0), 30.0, 40.0, region="Ankara")
    _daily(_d(0), 30.0, 34.0, region="İstanbul")
    monkeypatch.setattr(prefs, "region", lambda uid: "Konya")               # Bölgem Konya → Ankara
    got = market_ref.receipt_notes([{"name": "Süt 1L", "qty": 1, "amount": 50.0}], None, "Örnek", _d(0))
    assert (got[0]["region"], got[0]["market_median"]) == ("Ankara", 40.0)


def test_skips_reading_errors_and_unit_mismatch(client, resmi):
    _market()
    chk = {"digit_fix": [{"index": 0}], "suspect": [3], "items": [{"index": 2, "mismatch": True}]}
    assert market_ref.receipt_notes(ITEMS, chk, "Örnek", _d(0)) == []
    assert market_ref.receipt_notes(ITEMS, {"suggested_shift": {"from_index": 1}, "items": [{}] * 4},
                                    "Örnek", _d(0))[0]["index"] == 0
    weighed = [{"name": "Süt 1L", "qty": 1.25, "unit": "kg", "amount": 60.0}]   # tartılı ama grup tartısız
    assert market_ref.receipt_notes(weighed, None, "Örnek", _d(0)) == []


def test_top5_and_community_source(client):
    for uid, p in ((1, 30.0), (2, 32.0), (3, 34.0)):
        cp.contribute(uid, "A101", None, [{"name": "Süt 1L", "qty": 1, "amount": p}], _d(1))
    got = market_ref.receipt_notes([{"name": "Süt 1L", "qty": 1, "amount": 40.0}], None, "Örnek", _d(0))
    assert [(n["source"], n["region"], n["market_median"], n["cheapest_chain"]) for n in got] == [
        ("community", None, 32.0, "A101")]
    names = ["Süt 500 ml", "Su 1,5 L", "Ayran 1 L", "Makarna 500 g", "Pirinç 1 kg", "Un 2 kg", "Çay 1 kg"]
    for n in names:
        for uid in (1, 2, 3):
            cp.contribute(uid, "A101", None, [{"name": n, "qty": 1, "amount": 10.0}], _d(1))
    many = [{"name": n, "qty": 1, "amount": 20.0 + i} for i, n in enumerate(names)]
    got = market_ref.receipt_notes(many, None, "Örnek", _d(0))
    assert len(got) == market_ref.NOTE_MAX and got[0]["index"] == 6           # en çok 5, en pahalı önce


def test_needs_price_plan(client, resmi, monkeypatch):
    from app import plans

    _market()
    monkeypatch.setattr(plans, "allows", lambda u, f: f != "fiyatlar")
    assert market_ref.receipt_notes(ITEMS, None, "Örnek", _d(0)) == []


def test_in_document_parse_and_saved_receipt(client, resmi, monkeypatch):
    from app import receipt

    _market()

    def fake(data, mime, cats, split, more=None):
        return {"type": "receipt", "merchant": "Örnek Market", "total": 116.0, "category": "Market & Gıda",
                "legal_name": None, "confidence": "yüksek", "date": _d(0), "payment": "nakit",
                "items": [{"name": "Süt 1L", "qty": 1, "amount": 42.0},
                          {"name": "Yoğurt 1 kg", "qty": 1, "amount": 60.0},
                          {"name": "Ekmek", "qty": 1, "amount": 14.0}]}

    monkeypatch.setattr(receipt, "parse_document", fake)
    doc = client.post("/api/documents/parse", files=[("file", ("f.jpg", b"\xff\xd8x", "image/jpeg"))]).json()
    assert [n["index"] for n in doc["check"]["market_notes"]] == [2, 0]
    r = client.post("/api/entries", json={"entry_date": _d(0), "description": "Örnek Market", "amount": 116.0,
                                          "kind": "gider", "category": "Market & Gıda", "payment": "nakit",
                                          "items": doc["items"]}).json()
    assert "market_notes" not in r
    chk = client.get(f"/api/entries/{r['id']}/receipt-items").json()["check"]
    assert [n["index"] for n in chk["market_notes"]] == [2, 0]
    put = client.put(f"/api/entries/{r['id']}/receipt-items",
                     json={"items": [{"name": "Süt 1L", "qty": 1, "amount": 36.0},
                                     {"name": "Yoğurt 1 kg", "qty": 1, "amount": 60.0},
                                     {"name": "Ekmek", "qty": 1, "amount": 20.0}]}).json()
    assert [n["index"] for n in put["check"]["market_notes"]] == [2]
    assert "market_notes" in client.get("/api/meta").json()["capabilities"]
