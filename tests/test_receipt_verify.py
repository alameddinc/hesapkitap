"""Fiş toplamının metinle doğrulanması (yetenek total_check, app/receipt_verify.py) ve genişleyen yeniden okuma tetiği
(app/receipt_check.py reread_reasons): toplam telefon/PDF metninde Türk biçimiyle geçiyor mu, kalemli fişte adet × birim
/ iki satır belirtisi / metinde olmayan toplam yeniden okutur, kalemsiz fişte okutmaz. Veriler uydurmadır."""
import json

import httpx
import pytest

from app import receipt_check, receipt_verify
from tests.test_daily_reads import _pdf
from tests.test_doc_reads import _jpeg


# ---------------------------------------------------------------- saf: toplam / tarih / işyeri metinde mi

@pytest.mark.parametrize("text", ["GENEL TOPLAM 1.795,70", "TOPLAM 1795,70", "TOPLAM *1.795,70", "TOPLAM  1 795,70",
                                  "TOP *1.795, 70", "TUTAR:1795.70 TL", "TOPLAM\n*1.795,70\nNAKIT"])
def test_total_found_in_turkish_formats(text):
    assert receipt_verify.total_in_text(1795.70, text) is True
    assert receipt_verify.verify({"total": 1795.70}, text)["total_check"] == "ok"


@pytest.mark.parametrize("text", ["TOPLAM 1.795,07", "TOPLAM 11.795,70", "TOPLAM 1.795,701", "TOPLAM 1795",
                                  "TOPLAM 179,57", "", "OKUNAMADI"])
def test_total_not_found(text):
    got = receipt_verify.verify({"total": 1795.70}, text)["total_check"]
    assert got == (None if not text else "kontrol")


def test_total_check_null_without_text_or_total():
    assert receipt_verify.verify({"total": 25.0}, None)["total_check"] is None
    assert receipt_verify.verify({"total": 25.0}, "   ")["total_check"] is None
    assert receipt_verify.verify({"total": None}, "TOPLAM 25,00")["total_check"] is None
    assert receipt_verify.verify({"total": 0}, "TOPLAM 0,00")["total_check"] is None
    assert receipt_verify.total_in_text(25.0, "TOPLAM *25,00") and receipt_verify.total_in_text(7.5, "ODENEN 7,50")


def test_date_and_merchant_in_text():
    text = "ORNEK KIRTASIYE LTD STI\nTARIH: 3.11.2026 SAAT 14:05\nTOPLAM *42,50"
    v = receipt_verify.verify({"total": 42.5, "date": "2026-11-03", "merchant": "Örnek Kırtasiye"}, text)
    assert v == {"total_check": "ok", "date_in_text": True, "merchant_in_text": True}
    assert receipt_verify.date_in_text("2026-11-03", "03/11/26") and receipt_verify.date_in_text("2026-11-03", "03-11-2026")
    assert not receipt_verify.date_in_text("2026-11-03", "13.11.2026")
    assert receipt_verify.merchant_in_text(["Baska Isyeri"], text) is False
    assert receipt_verify.merchant_in_text(["Fiş"], text) is None  # karar verilemez (anlamlı kelime yok)


# ---------------------------------------------------------------- saf: yeniden okuma tetiği

def _it(name, amount, qty=1, unit_price=None):
    return {"name": name, "qty": qty, "unit": "adet", "amount": amount, "unit_price": unit_price}


def test_reread_reasons():
    good = [_it("SUT 1L", 40.0), _it("EKMEK", 15.0)]
    assert receipt_check.reread_reasons(good, receipt_check.check(good, 55.0), "ok") == []
    assert receipt_check.reread_reasons(good, receipt_check.check(good, 55.0), None) == []
    # toplam metinde yok (metin var): kalemli fişte yeniden oku
    assert receipt_check.reread_reasons(good, receipt_check.check(good, 55.0), "kontrol") == ["metin"]
    # iki satıra bölünmüş ürün: sonraki satır yalnız gramaj
    split = [_it("DETERJAN", 90.0), _it("500 GR", 4.0)]
    assert receipt_check.reread_reasons(split, receipt_check.check(split, 94.0)) == ["bolunmus"]
    # adet × birim tutmuyor
    mis = [_it("MUZ", 75.0, 2, 40.0), _it("EKMEK", 25.0)]
    assert receipt_check.reread_reasons(mis, receipt_check.check(mis, 100.0)) == ["birim"]
    # kalemsiz / tek kalemli fiş (akaryakıt, slip): yeni tetikler yok
    one = [_it("BENZIN", 1200.0, 30, 41.0)]
    assert receipt_check.reread_reasons(one, receipt_check.check(one, 1200.0), "kontrol") == []
    assert receipt_check.reread_reasons([], receipt_check.check([], 1200.0), "kontrol") == []
    # eski tetikler aynen: fiyatsız kalem, toplam farkı
    assert receipt_check.reread_reasons([_it("SU", None)], receipt_check.check([_it("SU", None)], None)) == ["fiyatsiz"]
    assert "toplam" in receipt_check.reread_reasons(good, receipt_check.check(good, 60.0))


def test_take_reread_prefers_total_found_in_text():
    text = "ORNEK\nTOPLAM *1.795,70"
    items = [_it("KAHVE", 900.0), _it("CAY", 895.0)]
    doc = {"total": 1795.0, "items": items, "check": receipt_check.check(items, 1795.0), "total_check": "kontrol"}
    assert receipt_check.reread_hint(doc) is None  # şüpheli toplam modele bildirilmez
    again = {"total": 1795.70, "items": [_it("KAHVE", 900.0), _it("CAY", 895.70)]}
    assert receipt_check.take_reread(doc, again, text)
    assert doc["total"] == 1795.70 and doc["total_check"] == "ok" and doc["check"]["ok"]
    # yeni toplam da metinde yoksa toplam değişmez; kalemler daha kötüyse alınmaz
    doc = {"total": 1795.0, "items": items, "check": receipt_check.check(items, 1795.0), "total_check": "kontrol"}
    assert not receipt_check.take_reread(doc, {"total": 1800.0, "items": [_it("KAHVE", 10.0)]}, text)
    assert doc["total"] == 1795.0 and doc["items"] == items
    assert not receipt_check.take_reread(doc, {"total": 1795.0, "items": []}, text)


def test_bench_uses_app_split_rule():
    from app import receipt_bench

    assert receipt_bench.split_suspects is receipt_check.split_suspects
    assert receipt_bench.continuation_like is receipt_check.continuation_like


def test_bench_app_path_verifies_and_rereads():
    from app import receipt_bench as rb

    case = rb.Case(image=b"", mime="image/jpeg", amount=1795.70, on=None, description="Ornek", linked=False,
                   text="ORNEK\nTOPLAM *1.795,70")
    b_doc = {"type": "receipt", "total": 1795.0, "items": [_it("KAHVE", 900.0), _it("CAY", 895.0)]}
    seen = []

    def reread(c, total):
        seen.append(total)
        return {"total": 1795.70, "items": [_it("KAHVE", 900.0), _it("CAY", 895.70)]}, {"promptTokenCount": 100}

    doc, info = rb.app_path(b_doc, case, reread)
    assert info["total_check"] == "ok" and info["tried"] and info["used"] and info["reasons"] == ["metin"]
    assert seen == [None] and doc["total"] == 1795.70 and b_doc["total"] == 1795.0  # B'nin sonucu değişmez
    doc, info = rb.app_path(b_doc, case, None)  # --reread yok: tetik hesaplanır, okunmaz
    assert info["reasons"] == ["metin"] and not info["tried"] and doc["total"] == 1795.0


# ---------------------------------------------------------------- uçtan uca: /api/documents/parse (sahte Gemini)

@pytest.fixture()
def gemini(monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    st = {"first": None, "again": None, "prompts": []}

    def post(url, json=None, timeout=None, headers=None):
        prompt = json["contents"][0]["parts"][0]["text"]
        st["prompts"].append(prompt)
        body = st["again"] if "yeniden oku" in prompt else st["first"]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dump(body)}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", post)
    return st


def _dump(d):
    return json.dumps(d, ensure_ascii=False)


_FIS = {"doc_type": "fis", "merchant": "Ornek Kirtasiye", "date": "2026-11-03", "category": "Diğer", "confidence": "orta"}
TEXT = "ORNEK KIRTASIYE\nTARIH 03.11.2026\nDEFTER  *420,50\nKALEM  *1.375,20\nTOPLAM  *1.795,70\nKREDI KARTI"


def _parse(client, text=TEXT, files=None):
    data = {"ocr_text": text, "ocr_source": "vision"} if text is not None else {}
    return client.post("/api/documents/parse", files=files or [("file", ("f.jpg", _jpeg(), "image/jpeg"))], data=data)


def test_parse_total_ok_with_text(client, gemini):
    gemini["first"] = {**_FIS, "total": 1795.70, "items": [{"name": "DEFTER", "amount": 420.50},
                                                          {"name": "KALEM", "amount": 1375.20}]}
    r = _parse(client).json()
    assert r["total_check"] == "ok" and r["text_match"] == {"date": True, "merchant": True}
    assert "reread" not in r and len(gemini["prompts"]) == 1


def test_parse_total_kontrol_rereads_without_hint_and_takes_text_total(client, gemini):
    # ucuz model toplamı yanlış okudu (uydurma), kalemleri de ona uydurdu: kalem toplamı tutuyor ama metinde yok
    gemini["first"] = {**_FIS, "total": 1795.0, "items": [{"name": "DEFTER", "amount": 420.0},
                                                         {"name": "KALEM", "amount": 1375.0}]}
    gemini["again"] = {"total": 1795.70, "items": [{"name": "DEFTER", "amount": 420.50}, {"name": "KALEM", "amount": 1375.20}]}
    r = _parse(client).json()
    assert r["reread"]["tried"] and r["reread"]["used"] and r["reread_reasons"] == ["metin"]
    assert r["total"] == 1795.70 and r["total_check"] == "ok" and r["check"]["ok"]
    assert "GENEL TOPLAMI" not in gemini["prompts"][-1]  # şüpheli toplam bildirilmedi


def test_parse_total_kontrol_kept_when_reread_not_better(client, gemini):
    gemini["first"] = {**_FIS, "total": 1795.0, "items": [{"name": "DEFTER", "amount": 420.0},
                                                         {"name": "KALEM", "amount": 1375.0}]}
    gemini["again"] = {"total": 1795.0, "items": [{"name": "DEFTER", "amount": 420.0}, {"name": "KALEM", "amount": 1375.0}]}
    r = _parse(client).json()
    assert r["total_check"] == "kontrol" and r["reread"]["used"] is False and r["total"] == 1795.0


def test_parse_single_line_slip_no_reread(client, gemini):
    gemini["first"] = {**_FIS, "total": 1200.0, "items": [{"name": "BENZIN", "amount": 1200.0}]}
    r = _parse(client, text="AKARYAKIT\nTUTAR 1.250,00").json()
    assert r["total_check"] == "kontrol" and "reread" not in r and len(gemini["prompts"]) == 1


def test_parse_without_text_total_check_null(client, gemini):
    gemini["first"] = {**_FIS, "total": 1795.70, "items": [{"name": "DEFTER", "amount": 420.50},
                                                          {"name": "KALEM", "amount": 1375.20}]}
    r = _parse(client, text=None).json()
    assert r["total_check"] is None and "text_match" not in r


def test_parse_pdf_uses_text_layer(client, gemini, monkeypatch):
    from app import bill_templates

    monkeypatch.setattr(bill_templates, "text_of", lambda data, mime: TEXT if mime == "application/pdf" else None)
    gemini["first"] = {**_FIS, "total": 1795.70, "items": [{"name": "DEFTER", "amount": 420.50},
                                                          {"name": "KALEM", "amount": 1375.20}]}
    r = _parse(client, text=None, files=[("file", ("f.pdf", _pdf(1), "application/pdf"))]).json()
    assert r["type"] == "receipt" and r["total_check"] == "ok"


def test_capability_listed(client):
    assert "total_check" in client.get("/api/meta").json()["capabilities"]
