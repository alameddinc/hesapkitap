"""Fiş kalemleri: denetim (toplam, adet × birim, kayma önerisi), kayıtlı fişin kalemlerini oku/değiştir, saklı
fotoğrafı yeniden oku (belge hakkı düşmez, günlük sınır, sonuç kaydedilmez)."""
import json

import httpx
import pytest

from app import receipt_check
from tests.test_doc_reads import _jpeg, _used, hosted  # noqa: F401 - fixture
from tests.test_plans import _user


def _it(name, amount, qty=1, unit_price=None):
    return {"name": name, "qty": qty, "unit": "adet", "amount": amount, "unit_price": unit_price}


def test_check_ok_and_mismatch_and_shift():
    good = [_it("K. CANTA ORTA BOY", 5.80, 2, 2.90), _it("KIZILAY", 66.0), _it("DO.PIS.KAG", 59.0),
            _it("AVOKADO ADET", 89.80, 2, 44.90), _it("HIPP", 89.0)]
    r = receipt_check.check(good, 309.60)
    assert r["ok"] and r["diff"] == 0 and r["shift_start"] is None and r["suggested_shift"] is None
    # fiyatlar bir satır aşağı kaymış: ilk ürünün fiyatı kaybolmuş, son ürünün fiyatı boş
    bad = [_it("K. CANTA ORTA BOY", 66.0, 2, 2.90), _it("KIZILAY", 59.0), _it("DO.PIS.KAG", 89.80),
           _it("AVOKADO ADET", 89.0, 2, 44.90), _it("HIPP", None)]
    r = receipt_check.check(bad, 309.60)
    assert not r["ok"] and r["items_total"] == 303.8 and r["diff"] == 5.8
    assert [i["mismatch"] for i in r["items"]] == [True, False, False, True, False]
    assert r["items"][0]["expected"] == 5.8 and r["shift_start"] == 0
    s = r["suggested_shift"]
    assert s["from_index"] == 0 and s["direction"] == "down" and s["amounts"] == [5.8, 66.0, 59.0, 89.8, 89.0]
    assert s["diff"] == 0


def test_check_total_only_no_shift_guess():
    r = receipt_check.check([_it("A", 10.0), _it("B", 20.0)], 35.0)
    assert not r["ok"] and r["diff"] == 5 and r["suggested_shift"] is None


def test_saved_receipt_items_get_put(client, tmpdb):
    eid = client.post("/api/entries", json={"entry_date": "2026-10-29", "description": "File Market", "amount": 309.60,
                                            "kind": "gider", "category": "Market & Gıda",
                                            "items": [_it("K. CANTA ORTA BOY", 66.0), _it("KIZILAY", 243.6)]}).json()["id"]
    got = client.get(f"/api/entries/{eid}/receipt-items").json()
    assert [i["name"] for i in got["items"]] == ["K. CANTA ORTA BOY", "KIZILAY"] and got["check"]["ok"]
    tmpdb.set_splits("entry", eid, [{"amount": 66.0, "category": "Ev"}], origin="fis")
    new = [_it("K. CANTA ORTA BOY", 5.80, 2), _it("KIZILAY", 66.0), _it("DO.PIS.KAG", 59.0), _it("AVOKADO", 89.8, 2),
           _it("HIPP", 89.0)]
    out = client.put(f"/api/entries/{eid}/receipt-items", json={"items": new}).json()
    assert out["saved"] == 5 and out["check"]["ok"] and out["items"][0]["qty"] == 2
    with tmpdb.connect() as c:  # kayıt, kategori ve bölme değişmez
        e = c.execute("SELECT amount, category FROM entries WHERE id = ?", (eid,)).fetchone()
        sp = c.execute("SELECT amount, category FROM splits WHERE entry_id = ?", (eid,)).fetchall()
    assert tuple(e) == (309.6, "Market & Gıda") and [tuple(x) for x in sp] == [(66.0, "Ev")]
    assert client.get("/api/entries/99999/receipt-items").status_code == 404


@pytest.fixture()
def reread_ai(monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    seen = []
    fis = {"doc_type": "fis", "merchant": "File", "date": "2026-10-29", "total": 309.6, "category": "Market & Gıda",
           "items": [{"name": "2 ad X 2.90", "amount": 5.8}, {"name": "K. CANTA ORTA BOY", "amount": 66.0},
                     {"name": "KIZILAY", "amount": 59.0}], "confidence": "orta"}
    again = {"total": 309.6, "items": [{"name": "K. CANTA ORTA BOY", "qty": 2, "unit_price": 2.9, "amount": 5.8},
                                        {"name": "KIZILAY", "amount": 66.0}, {"name": "DO.PIS.KAG", "amount": 59.0},
                                        {"name": "AVOKADO", "qty": 2, "unit_price": 44.9, "amount": 89.8},
                                        {"name": "HIPP", "amount": 89.0}], "confidence": "yüksek"}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.append((url, json["contents"][0]["parts"][0]["text"]))
        body = again if "yeniden oku" in seen[-1][1] else fis
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dump(body)}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", fake_post)
    return seen


def _dump(d):
    return json.dumps(d, ensure_ascii=False)


def test_reread_does_not_charge_and_is_limited(hosted, reread_ai):  # noqa: F811
    c, u = _user(hosted)
    r = c.post("/api/documents/parse", files=[("file", ("fis.jpg", _jpeg(), "image/jpeg"))]).json()
    assert _used(u["id"]) == 1 and r["receipt_file"] and "check" in r
    # ilk okuma fişi tutmadı: aynı istekte güçlü modelle bir kez daha okundu, daha iyi olduğu için o kullanıldı
    assert r["reread"] == {"tried": True, "used": True, "model": "gemini-3.5-flash"} and r["check"]["ok"]
    out = c.post("/api/receipts/reread", json={"receipt_file": r["receipt_file"], "total": 309.6})
    assert out.status_code == 200, out.text
    out = out.json()
    assert out["check"]["ok"] and len(out["items"]) == 5 and out["model"] == "gemini-3.5-flash"
    assert "GENEL TOPLAMI 309.60" in reread_ai[-1][1] and "gemini-3.5-flash:" in reread_ai[-1][0]
    assert _used(u["id"]) == 1  # yeniden okuma belge hakkı düşürmez
    eid = c.post("/api/entries", json={"entry_date": "2026-10-29", "description": "File", "amount": 309.6,
                                       "kind": "gider", "category": "Market & Gıda",
                                       "receipt_file": r["receipt_file"]}).json()["id"]
    assert c.post(f"/api/entries/{eid}/receipt-reread").status_code == 200
    assert c.get(f"/api/entries/{eid}/receipt-items").json()["items"] == []  # sonuç kaydedilmez
    assert c.post(f"/api/entries/{eid}/receipt-reread").status_code == 200
    assert c.post(f"/api/entries/{eid}/receipt-reread").status_code == 429  # günde 3
    assert _used(u["id"]) == 1
    assert c.post("/api/receipts/reread", json={"receipt_file": "0" * 64 + ".jpg"}).status_code == 404


def _ai(monkeypatch, first, second=None, fail=False):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(url)
        again = "yeniden oku" in json["contents"][0]["parts"][0]["text"]
        if again and fail:
            return httpx.Response(500, json={"error": {"message": "model yok"}})
        body = second if again else first
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dump(body)}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", fake_post)
    return calls


_FIS = {"doc_type": "fis", "merchant": "File", "total": 100.0, "category": "Market & Gıda", "confidence": "orta"}


def test_auto_reread_skipped_when_ok(client, monkeypatch):
    from tests.test_doc_reads import _jpeg as jpeg

    calls = _ai(monkeypatch, {**_FIS, "items": [{"name": "A", "amount": 60.0}, {"name": "B", "amount": 40.0}]})
    r = client.post("/api/receipts/parse", files={"file": ("f.jpg", jpeg(), "image/jpeg")}).json()
    assert r["check"]["ok"] and "reread" not in r and len(calls) == 1


def test_auto_reread_worse_keeps_first_and_error_is_silent(client, monkeypatch):
    from tests.test_doc_reads import _jpeg as jpeg

    first = {**_FIS, "items": [{"name": "A", "amount": 60.0}, {"name": "B", "amount": 30.0}]}  # 10 eksik
    _ai(monkeypatch, first, {"total": 100, "items": [{"name": "A", "amount": 10.0}]})
    r = client.post("/api/receipts/parse", files={"file": ("f.jpg", jpeg(), "image/jpeg")}).json()
    assert r["reread"] == {"tried": True, "used": False, "model": "gemini-3.5-flash"} and r["check"]["diff"] == 10
    assert [i["amount"] for i in r["items"]] == [60.0, 30.0]
    _ai(monkeypatch, first, fail=True)
    r = client.post("/api/documents/parse", files=[("file", ("f.jpg", jpeg(), "image/jpeg"))])
    assert r.status_code == 200 and r.json()["reread"]["used"] is False and len(r.json()["items"]) == 2


def test_auto_reread_daily_cap(client, monkeypatch):
    from app.api import main
    from tests.test_doc_reads import _jpeg as jpeg

    monkeypatch.setattr(main, "_AUTO_REREAD_PER_DAY", 1)
    calls = _ai(monkeypatch, {**_FIS, "items": [{"name": "A", "amount": 1.0}]}, {"total": 100, "items": [{"name": "A", "amount": 1.0}]})
    client.post("/api/receipts/parse", files={"file": ("f.jpg", jpeg(), "image/jpeg")})
    assert len(calls) == 2
    r = client.post("/api/receipts/parse", files={"file": ("g.jpg", jpeg(), "image/jpeg")}).json()
    assert len(calls) == 3 and "reread" not in r  # sınır doldu: yalnız ilk okuma
