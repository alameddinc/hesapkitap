"""Telefonda çıkarılan metinle fiş okuma (yetenek receipt_text): POST /api/documents/parse ocr_text + ocr_source.
Yapay zekâya fotoğraf yerine metin gider (satır sonları korunur), fotoğraf yine saklanır, fiş hakkı 1 düşer; metinden
çıkan fiş tutarsızsa bir kez fotoğrafla okunur (maliyet türleri fis_metin / fis_foto). Veriler uydurmadır."""
import json

import httpx
import pytest

from tests.test_doc_reads import _jpeg, _used, hosted  # noqa: F401 - fixture
from tests.test_plans import _user

TEXT = "ORNEK MARKET\nTARIH 01.11.2026\n2 AD X 2,50\nPOSET  *5,00\nSU 1,5 L  *20,00\nTOPLAM  *25,00\nKREDI KARTI"
_FIS = {"doc_type": "fis", "merchant": "Ornek Market", "date": "2026-11-01", "total": 25.0,
        "category": "Market & Gıda", "confidence": "yüksek"}
GOOD = {**_FIS, "items": [{"name": "POSET", "qty": 2, "unit_price": 2.5, "amount": 5.0},
                          {"name": "SU 1,5 L", "amount": 20.0}]}
BAD = {**_FIS, "items": [{"name": "POSET", "amount": 5.0}, {"name": "SU 1,5 L", "amount": None}]}


@pytest.fixture()
def ai(monkeypatch):
    """Sahte Gemini: metinli çağrıya `replies['metin']`, fotoğraflı çağrıya `replies['foto']` döner (None → 500)."""
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    state = {"sent": [], "replies": {"metin": GOOD, "foto": GOOD}}

    def post(url, json=None, timeout=None, headers=None):
        parts = json["contents"][0]["parts"]
        photo = any("inline_data" in p for p in parts)
        state["sent"].append({"photo": photo, "prompt": parts[0]["text"], "url": url})
        body = state["replies"]["foto" if photo else "metin"]
        if "yeniden oku" in parts[0]["text"]:
            body = {"total": 25.0, "items": GOOD["items"]}
        if body is None:
            return httpx.Response(500, json={"error": {"message": "yok"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dump(body)}]}}],
                                         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})

    monkeypatch.setattr(receipt.httpx, "post", post)
    return state


def _dump(d):
    return json.dumps(d, ensure_ascii=False)


def _kinds():
    from app import system

    with system.connect() as c:
        return [r[0] for r in c.execute("SELECT kind FROM ai_calls ORDER BY id")]


def _parse(c, text=TEXT, source="vision", n=1):
    data = {"ocr_text": text, "ocr_source": source} if text is not None else {}
    return c.post("/api/documents/parse", files=[("file", (f"f{i}.jpg", _jpeg(), "image/jpeg")) for i in range(n)],
                  data=data)


def test_capability():
    from app import version

    assert "receipt_text" in version.handshake()["capabilities"]


def test_text_goes_to_ai_instead_of_photo(hosted, ai):  # noqa: F811
    c, u = _user(hosted)
    r = _parse(c)
    assert r.status_code == 200, r.text
    r = r.json()
    assert len(ai["sent"]) == 1 and not ai["sent"][0]["photo"]
    prompt = ai["sent"][0]["prompt"]
    assert TEXT in prompt and "PARÇA" in prompt and "SADECE şu JSON" in prompt  # satır sonları korunur, aynı istem
    assert r["type"] == "receipt" and r["total"] == 25.0 and [i["amount"] for i in r["items"]] == [5.0, 20.0]
    assert r["ocr"] == {"source": "vision", "used": True, "fallback": False}
    assert r["receipt_file"] and r["check"]["ok"] and "reread" not in r  # fotoğraf yine saklanır
    assert _used(u["id"]) == 1 and _kinds() == ["fis_metin"]


def test_inconsistent_text_falls_back_to_photo_once(hosted, ai):  # noqa: F811
    c, u = _user(hosted)
    ai["replies"]["metin"] = BAD  # fiyatsız kalem: satır kayması
    r = _parse(c, source="mlkit").json()
    assert [s["photo"] for s in ai["sent"]] == [False, True]
    assert r["ocr"] == {"source": "mlkit", "used": False, "fallback": True} and r["check"]["ok"]
    assert [i["amount"] for i in r["items"]] == [5.0, 20.0] and "reread" not in r
    assert _used(u["id"]) == 1 and _kinds() == ["fis_metin", "fis_foto"]


def test_sum_off_text_falls_back_and_photo_error_keeps_text(hosted, ai):  # noqa: F811
    c, _ = _user(hosted)
    ai["replies"]["metin"] = {**_FIS, "items": [{"name": "POSET", "amount": 5.0}, {"name": "SU", "amount": 10.0}]}
    ai["replies"]["foto"] = None  # fotoğraf okuması başarısız: metnin sonucu kalır
    r = _parse(c).json()
    assert r["ocr"]["used"] and not r["ocr"]["fallback"]
    # metnin sonucu hâlâ tutmuyor: bugünkü gibi güçlü modelle fotoğraftan kalemler yeniden okunur
    assert [s["photo"] for s in ai["sent"]] == [False, True, True] and r["reread"]["used"] and r["check"]["ok"]


def test_text_error_reads_photo(hosted, ai):  # noqa: F811
    c, u = _user(hosted)
    ai["replies"]["metin"] = None
    r = _parse(c).json()
    assert [s["photo"] for s in ai["sent"]] == [False, True] and r["ocr"]["fallback"] and r["total"] == 25.0
    assert _used(u["id"]) == 1


def test_unit_price_mismatch_alone_does_not_fall_back(hosted, ai):  # noqa: F811
    c, _ = _user(hosted)
    ai["replies"]["metin"] = {**_FIS, "items": [{"name": "MUZ", "qty": 2, "unit_price": 4.0, "amount": 5.0},
                                                {"name": "SU", "amount": 20.0}]}
    r = _parse(c).json()
    assert len(ai["sent"]) == 1 and r["ocr"]["used"] and r["check"]["items"][0]["mismatch"]


def test_bill_from_text(hosted, ai):  # noqa: F811
    c, _ = _user(hosted)
    ai["replies"]["metin"] = {"doc_type": "fatura", "merchant": "Ornek Elektrik", "total": 100.0,
                              "due_date": "2026-11-20", "bill_type": "elektrik"}
    r = _parse(c, text="ORNEK ELEKTRIK\nSON ODEME TARIHI 20.11.2026\nODENECEK TUTAR 100,00").json()
    assert r["type"] == "bill" and r["amount"] == 100.0 and r["file_name"] and _kinds() == ["belge"]


def test_without_text_photo_as_before_and_limits(hosted, ai):  # noqa: F811
    c, _ = _user(hosted)
    r = _parse(c, text=None).json()
    assert [s["photo"] for s in ai["sent"]] == [True] and "ocr" not in r and _kinds() == ["fis"]
    assert _parse(c, text="x" * 20001).status_code == 422
    r = _parse(c, text="   ").json()  # boş metin: fotoğraf yolu
    assert "ocr" not in r and ai["sent"][-1]["photo"]
    r = _parse(c, source="baska").json()  # bilinmeyen kaynak
    assert r["ocr"]["source"] == "diger"


def test_parse_document_text_prompt():
    from app import receipt

    with pytest.raises(receipt.ReceiptError):
        receipt.parse_document_text("  ", ["Diğer"])
