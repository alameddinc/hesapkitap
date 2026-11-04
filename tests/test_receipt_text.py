"""Telefonda çıkarılan metinle fiş okuma (yetenek receipt_text): POST /api/documents/parse ocr_text + ocr_source.
Varsayılan: yapay zekâya FOTOĞRAF gider (maliyet türü fis); metin yardımcıdır — fatura şablonu metinle yerelde okunur
(ve metinden öğrenilir), model KDV/VKN'yi boş bırakırsa metinden doldurulur, yapay zekâ okuyamazsa yerel okuma metinle.
HESAPKITAP_RECEIPT_TEXT_FIRST=1 (eski yol): metin fotoğraf yerine gider (satır sonları korunur), fotoğraf yine saklanır,
fiş hakkı 1 düşer; metinden çıkan fiş tutarsızsa bir kez fotoğrafla okunur (fis_metin / fis_foto). Veriler uydurmadır:
VKN kontrol hanesi tutan uydurma numara."""
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
        state["sent"].append({"photo": photo, "prompt": parts[0]["text"], "url": url,
                              "mimes": [p["inline_data"]["mime_type"] for p in parts if "inline_data" in p]})
        body = state["replies"]["foto" if photo else "metin"]
        if "yeniden oku" in parts[0]["text"]:
            body = {"total": 25.0, "items": GOOD["items"]}
        if body is None:
            return httpx.Response(500, json={"error": {"message": "yok"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dump(body)}]}}],
                                         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})

    monkeypatch.setattr(receipt.httpx, "post", post)
    return state


@pytest.fixture()
def text_first(monkeypatch):
    """Eski davranış: metin fotoğraf yerine yapay zekâya gider."""
    monkeypatch.setenv("HESAPKITAP_RECEIPT_TEXT_FIRST", "1")


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


def test_text_goes_to_ai_instead_of_photo(hosted, ai, text_first):  # noqa: F811
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


def test_inconsistent_text_falls_back_to_photo_once(hosted, ai, text_first):  # noqa: F811
    c, u = _user(hosted)
    ai["replies"]["metin"] = BAD  # fiyatsız kalem: satır kayması
    r = _parse(c, source="mlkit").json()
    assert [s["photo"] for s in ai["sent"]] == [False, True]
    assert r["ocr"] == {"source": "mlkit", "used": False, "fallback": True} and r["check"]["ok"]
    assert [i["amount"] for i in r["items"]] == [5.0, 20.0] and "reread" not in r
    assert _used(u["id"]) == 1 and _kinds() == ["fis_metin", "fis_foto"]


def test_sum_off_text_falls_back_and_photo_error_keeps_text(hosted, ai, text_first):  # noqa: F811
    c, _ = _user(hosted)
    ai["replies"]["metin"] = {**_FIS, "items": [{"name": "POSET", "amount": 5.0}, {"name": "SU", "amount": 10.0}]}
    ai["replies"]["foto"] = None  # fotoğraf okuması başarısız: metnin sonucu kalır
    r = _parse(c).json()
    assert r["ocr"]["used"] and not r["ocr"]["fallback"]
    # metnin sonucu hâlâ tutmuyor: bugünkü gibi güçlü modelle fotoğraftan kalemler yeniden okunur
    assert [s["photo"] for s in ai["sent"]] == [False, True, True] and r["reread"]["used"] and r["check"]["ok"]


def test_text_error_reads_photo(hosted, ai, text_first):  # noqa: F811
    c, u = _user(hosted)
    ai["replies"]["metin"] = None
    r = _parse(c).json()
    assert [s["photo"] for s in ai["sent"]] == [False, True] and r["ocr"]["fallback"] and r["total"] == 25.0
    assert _used(u["id"]) == 1


def test_unit_price_mismatch_alone_does_not_fall_back(hosted, ai, text_first):  # noqa: F811
    c, _ = _user(hosted)
    ai["replies"]["metin"] = {**_FIS, "items": [{"name": "MUZ", "qty": 2, "unit_price": 4.0, "amount": 5.0},
                                                {"name": "SU", "amount": 20.0}]}
    r = _parse(c).json()
    assert len(ai["sent"]) == 1 and r["ocr"]["used"] and r["check"]["items"][0]["mismatch"]


def test_bill_from_text(hosted, ai, text_first):  # noqa: F811
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


# ---------------------------------------------------------------- varsayılan: fotoğraf okunur, metin yardımcı

VKN = "8218105998"  # uydurma, kontrol hanesi tutar
TAX_TEXT = TEXT.replace("TOPLAM", "TOPKDV  *2,50\nTOPLAM") + f"\nKADIKOY V.D. V.NO: {VKN}"


def test_photo_with_text_sends_photo_not_text(hosted, ai):  # noqa: F811
    c, u = _user(hosted)
    r = _parse(c)
    assert r.status_code == 200, r.text
    r = r.json()
    assert len(ai["sent"]) == 1 and ai["sent"][0]["photo"] and ai["sent"][0]["mimes"] == ["image/jpeg"]
    assert TEXT not in ai["sent"][0]["prompt"] and "METİN" not in ai["sent"][0]["prompt"]  # metin yapay zekâya gitmez
    assert r["type"] == "receipt" and r["total"] == 25.0 and [i["amount"] for i in r["items"]] == [5.0, 20.0]
    assert r["ocr"] == {"source": "vision", "used": False, "fallback": False, "helper": True}
    assert r["receipt_file"] and r["check"]["ok"] and "reread" not in r
    assert _used(u["id"]) == 1 and _kinds() == ["fis"]


def test_long_receipt_parts_all_go_as_images(hosted, ai):  # noqa: F811
    c, _ = _user(hosted)
    _parse(c, n=3)
    assert len(ai["sent"]) == 1 and len(ai["sent"][0]["mimes"]) == 3 and _kinds() == ["fis"]


def test_vat_and_tax_no_filled_from_text(hosted, ai):  # noqa: F811
    c, _ = _user(hosted)
    r = _parse(c, text=TAX_TEXT).json()  # model KDV/VKN'yi boş bıraktı (GOOD'da yok)
    assert ai["sent"][0]["photo"] and r["tax_no"] == VKN and r["vat_total"] == 2.5 and r["tax_office"]
    ai["replies"]["foto"] = {**GOOD, "tax_no": "4605721929"}  # modelin okuduğu korunur
    from app import system

    with system.connect() as cx:  # ilk okuma fiş şablonu öğretti; bu adım yapay zekâ yolunu sınıyor
        cx.execute("DELETE FROM receipt_templates")
    assert _parse(c, text=TAX_TEXT).json()["tax_no"] == "4605721929"


def test_inconsistent_photo_rereads_photo_not_text(hosted, ai):  # noqa: F811
    c, _ = _user(hosted)
    ai["replies"]["foto"] = BAD
    r = _parse(c).json()
    assert [s["photo"] for s in ai["sent"]] == [True, True] and r["reread"]["used"] and r["check"]["ok"]
    assert _kinds() == ["fis", "fis"] and not r["ocr"]["used"]


def test_photo_error_reads_text_locally(hosted, ai):  # noqa: F811
    c, u = _user(hosted)
    ai["replies"]["foto"] = None  # yapay zekâ okuyamadı: Tesseract yerine telefon metniyle kural tabanlı okuma
    r = _parse(c)
    assert r.status_code == 200, r.text
    r = r.json()
    assert r["engine"] == "yerel" and r["total"] == 25.0 and r["date"] == "2026-11-01" and r["note"]
    assert r["ocr"]["used"] and not any(not s["photo"] for s in ai["sent"])
    assert _used(u["id"]) == 1


def test_bill_template_learned_and_read_from_text(hosted, ai):  # noqa: F811
    from tests.test_bill_templates import _d, elektrik

    c, _ = _user(hosted)
    ai["replies"]["foto"] = {"doc_type": "fatura", "merchant": "Örnek Enerji", "bill_type": "elektrik",
                             "total": 845.30, "due_date": _d(10).isoformat(), "date": _d(-10).isoformat(),
                             "period": "Ekim 2026", "subscriber_no": "4001234567", "confidence": "yüksek"}
    r = _parse(c, text=elektrik(845.30, _d(10), _d(-10))).json()
    assert r["type"] == "bill" and r["source"] == "yapay_zeka" and ai["sent"][0]["photo"] and _kinds() == ["belge"]
    # aynı kurumun sonraki faturası: telefon metni şablonu tutuyor, yapay zekâya gidilmez
    r = _parse(c, text=elektrik(99.90, _d(14), _d(-3))).json()
    assert len(ai["sent"]) == 1 and r["type"] == "bill" and r["source"] == "sablon" and r["amount"] == 99.9
    assert r["ocr"]["used"] and r["file_name"] and _kinds() == ["belge"]


def test_text_first_setting(monkeypatch):
    from app import receipt

    monkeypatch.delenv("HESAPKITAP_RECEIPT_TEXT_FIRST", raising=False)
    assert not receipt.text_first()
    monkeypatch.setenv("HESAPKITAP_RECEIPT_TEXT_FIRST", "1")
    assert receipt.text_first()
    monkeypatch.setenv("HESAPKITAP_RECEIPT_TEXT_FIRST", "0")
    assert not receipt.text_first()


def test_local_text_parse_skips_part_lines():
    from app import ocr

    d = ocr.parse_text("ORNEK MARKET\n--- PARÇA 1 ---\nSU  *20,00\n--- PARÇA 2 ---\nTOPLAM  *20,00", ["Diğer"])
    assert [i["name"] for i in d["items"]] == ["SU"] and d["total"] == 20.0


def test_parse_document_text_prompt():
    from app import receipt

    with pytest.raises(receipt.ReceiptError):
        receipt.parse_document_text("  ", ["Diğer"])
