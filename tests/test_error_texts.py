"""Kullanıcıya giden hata metinleri Türkçe (denetim M10(b)): doğrulama 422, çıplak İngilizce/teknik metinler,
genel 404, Gemini hata yanıtları."""
import re

from app import errors


def test_validation_422_is_turkish(client):
    r = client.post("/api/entries", json={"entry_date": "2026-13-40", "description": "Kahve", "amount": -5,
                                          "kind": "gider"})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert isinstance(d, str) and errors.is_user_text(d)
    assert "Tarih geçerli bir tarih olmalı" in d
    assert not re.search(r"Input should|Field required|value is not", d)
    assert r.json()["errors"][0]["loc"][-1] in ("entry_date", "amount")


def test_validation_missing_field(client):
    r = client.post("/api/entries", json={"description": "Kahve"})
    assert r.status_code == 422 and "eksik" in r.json()["detail"]


def test_amount_minimum_message(client):
    r = client.post("/api/entries", json={"entry_date": "2026-10-05", "description": "Kahve", "amount": -5, "kind": "gider"})
    assert r.json()["detail"] == "Tutar en az 0 olmalı."


def test_unknown_route_and_method_turkish(client):
    r = client.get("/api/boyle-bir-uc-yok")
    assert r.status_code == 404 and "adres yok" in r.json()["detail"]
    r = client.delete("/api/summary")
    assert r.status_code == 405 and errors.is_user_text(r.json()["detail"])


def test_generic_404_text(client):
    r = client.post("/api/settings/backup/olmayan.zip/restore")  # önce gövdesiz HTTPException(404) → "Not Found"
    assert r.status_code == 404
    assert r.json()["detail"] == errors.GENERIC[404] and r.json()["refresh"] is True


def test_python_internal_messages_not_shown():
    assert errors.user_text(ValueError("could not convert string to float: 'abc'")) == errors.GENERIC[422]
    assert errors.user_text(KeyError(42), 404) == errors.GENERIC[404]
    assert errors.user_text(ValueError("Alt kalemlerin toplamı (10.00) harcamayı (5.00) geçemez")).startswith("Alt kalemlerin")
    assert errors.user_text(errors.UserError("Kural sınırı")) == "Kural sınırı"
    assert errors.user_text(ValueError("File is not a zip file")) == errors.GENERIC[422]


def test_bad_excel_message_has_no_library_text(client):
    r = client.post("/api/bank-import/preview", files={"file": ("h.xlsx", b"bu bir excel degil", "application/vnd.ms-excel")})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert errors.is_user_text(d) and "zip" not in d.lower()


def test_bad_regex_rule_turkish(client):
    r = client.post("/api/rules", json={"pattern": "MARKET[", "category": "Market & Gıda"})
    assert r.status_code == 422 and "düzenli ifade" in r.json()["detail"]


def test_machine_codes_keep_detail_and_add_message(client, monkeypatch):
    """İstemciler detail koduna göre davranıyor (consent_required): kod korunur, yanına Türkçe message."""
    from fastapi import HTTPException

    from app.api.main import app

    @app.get("/api/_test_consent")
    def _t():
        raise HTTPException(409, "consent_required")
    try:
        r = client.get("/api/_test_consent")
        assert r.json()["detail"] == "consent_required" and r.json()["code"] == "consent_required"
        assert errors.is_user_text(r.json()["message"])
    finally:
        app.router.routes = [x for x in app.router.routes if getattr(x, "path", "") != "/api/_test_consent"]


def test_gemini_errors_turkish(monkeypatch):
    import httpx
    import pytest

    from app import receipt

    class R:
        def __init__(self, code, msg):
            self.status_code, self.headers, self._m, self.text = code, {"content-type": "application/json"}, msg, msg

        def json(self):
            return {"error": {"message": self._m}}

    monkeypatch.setenv("GEMINI_API_KEY", "test-anahtar")
    for code, msg, want in ((400, "API key not valid. Please pass a valid API key.", "anahtarı geçersiz"),
                            (429, "Resource has been exhausted (e.g. check quota).", "kullanım sınırı"),
                            (503, "The model is overloaded. Please try again later.", "yanıt vermiyor")):
        monkeypatch.setattr(httpx, "post", lambda *a, _c=code, _m=msg, **k: R(_c, _m))
        with pytest.raises(receipt.ReceiptError) as ei:
            receipt._gemini_json("p", b"x", "image/jpeg")
        assert want in str(ei.value) and errors.is_user_text(str(ei.value)) and "API key" not in str(ei.value)
