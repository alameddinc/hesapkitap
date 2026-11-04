"""Fiş okuma maliyet diyeti: sıkı çıktı istemi (tek satır, null'suz JSON, kısa kalem anahtarları), kısa anahtarların
_doc_of'ta uzun adlara çevrilmesi (eski uzun anahtarlı yanıt da okunur), görüntü çözünürlüğü ayarı, önbellekli girdi
token'ının indirimli fiyatla sayılması ve her ayarın ortam değişkeniyle kapatılabilmesi. Veriler uydurmadır."""
import json

import httpx
import pytest

from app import receipt

CATS = ["Market & Gıda", "Diğer"]


@pytest.fixture()
def gemini(monkeypatch):
    """Sahte Gemini: gönderilen gövdeleri saklar; yanıt ve usageMetadata ayarlanabilir."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    state = {"sent": [], "reply": {"doc_type": "fis", "merchant": "Örnek Market", "total": 10.0, "items": []},
             "usage": {"promptTokenCount": 2000, "candidatesTokenCount": 200, "cachedContentTokenCount": 0}}

    def post(url, json=None, timeout=None, headers=None):
        state["sent"].append(json)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(state["reply"])}]}}],
            "usageMetadata": state["usage"]})

    monkeypatch.setattr(receipt.httpx, "post", post)
    return state


# ---------------------------------------------------------------- istem

def test_compact_prompt_is_default_and_formats_cleanly(monkeypatch):
    monkeypatch.delenv("HESAPKITAP_RECEIPT_COMPACT", raising=False)
    p = receipt.doc_prompt(CATS, ["Sigara"])
    assert "TEK SATIR" in p and "null olan alanı" in p and "UYDURMA" in p  # sıkı çıktı + okunamayan fotoğraf kuralı
    assert '"n": "POSET ORTA BOY", "q": 2, "a": 5.00' in p and "MİKTAR SATIRI" in p
    assert "Market & Gıda, Diğer" in p and "items[].s" in p and "Sigara" in p
    for k in ("vat_total", "vat_lines", "tax_no", "tax_office", "card_last4", "due_date", "paid"):
        assert f'"{k}"' in p
    assert "{" in p and "{{" not in p  # biçim dizgesi çözüldü
    assert "items[].s" not in receipt.doc_prompt(CATS)  # kalem kuralı yoksa kural cümlesi de yok


@pytest.mark.parametrize("off", ["0", "false", "off"])
def test_compact_prompt_can_be_turned_off(monkeypatch, off):
    monkeypatch.setenv("HESAPKITAP_RECEIPT_COMPACT", off)
    assert receipt.doc_prompt(CATS, ["Sigara"]) == receipt.DOC_PROMPT.format(
        categories="Market & Gıda, Diğer", split_rule=receipt.SPLIT_RULE.format(cats="Sigara"))


def test_parse_document_sends_compact_prompt_without_media_resolution(gemini, monkeypatch):
    monkeypatch.delenv("HESAPKITAP_RECEIPT_COMPACT", raising=False)
    monkeypatch.delenv("HESAPKITAP_RECEIPT_MEDIA_RES", raising=False)
    receipt._parse_document_ai(b"foto", "image/jpeg", CATS)
    body = gemini["sent"][0]
    parts = body["contents"][0]["parts"]
    assert "TEK SATIR" in parts[0]["text"] and "inline_data" in parts[1] and len(parts) == 2  # talimat önde, görsel sonda
    assert "mediaResolution" not in body["generationConfig"]


def test_media_resolution_setting_images_only(gemini, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_RECEIPT_MEDIA_RES", "medium")
    assert receipt.media_resolution() == "MEDIA_RESOLUTION_MEDIUM"
    receipt._parse_document_ai(b"foto", "image/jpeg", CATS, more=[(b"foto2", "image/jpeg")])
    receipt._parse_document_ai(b"%PDF-x", "application/pdf", CATS)  # PDF sayfası küçültülmez
    assert gemini["sent"][0]["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_MEDIUM"
    assert "mediaResolution" not in gemini["sent"][1]["generationConfig"]
    for v in ("", "off", "bilinmeyen"):
        monkeypatch.setenv("HESAPKITAP_RECEIPT_MEDIA_RES", v)
        assert receipt.media_resolution() is None


# ---------------------------------------------------------------- kısa anahtar dönüşümü

def test_doc_of_reads_short_item_keys():
    raw = {"doc_type": "fis", "merchant": "Örnek Market", "total": 75.0, "items": [
        {"n": "2 ad X 2,50"},  # miktar satırı ayrı gelse de altındaki ürüne bağlanır
        {"n": "POSET ORTA BOY", "a": 5.0},
        {"n": "ORNEK SUT 1L", "p": "Örnek Süt 1 L", "b": "Örnek", "q": 2, "up": 25, "a": 50.0, "s": "Market & Gıda"},
        {"n": "DOMATES", "q": "0,750", "u": "kg", "a": 20.0}]}
    items = receipt._doc_of(raw, CATS)["items"]
    assert [(i["name"], i["qty"], i["unit"], i["amount"]) for i in items] == [
        ("POSET ORTA BOY", 2, "adet", 5.0), ("ORNEK SUT 1L", 2, "adet", 50.0), ("DOMATES", 0.75, "kg", 20.0)]
    sut = items[1]
    assert (sut["product"], sut["brand"], sut["unit_price"], sut["split"]) == ("Örnek Süt 1 L", "Örnek", 25.0,
                                                                              "Market & Gıda")
    assert items[2]["unit_price"] is None and items[2]["product"] is None  # yazılmayan alan → varsayılan


def test_doc_of_still_reads_long_item_keys():
    raw = {"doc_type": "fis", "merchant": "Örnek Market", "total": 12.5, "items": [
        {"name": "EKMEK", "product": "Ekmek", "brand": None, "qty": 1, "unit": "adet", "unit_price": None,
         "amount": 12.5, "split": None}]}
    it = receipt._doc_of(raw, CATS)["items"][0]
    assert (it["name"], it["product"], it["amount"], it["qty"]) == ("EKMEK", "Ekmek", 12.5, 1)


def test_long_key_wins_over_short_and_unknown_keys_kept():
    assert receipt._long_item({"n": "KISA", "name": "UZUN", "a": 1, "x": 2}) == {"name": "UZUN", "amount": 1, "x": 2}
    assert receipt._items({"items": "bozuk"}) == [] and receipt._items({"items": [None, 3, {"a": 1}]}) == []


def test_doc_of_missing_null_fields_default():
    """Sıkı çıktıda null alanlar hiç gelmez: eksik alanlar eskiden null gelenle aynı sonucu verir."""
    doc = receipt._doc_of({"doc_type": "fis", "total": 9.9}, CATS)
    assert doc["type"] == "receipt" and doc["merchant"] == "Fiş" and doc["items"] == []
    assert doc["tax_no"] is None and doc["vat_total"] is None and doc["vat_lines"] == [] and doc["installments"] == 1
    assert doc["category"] == "Diğer" and doc["confidence"] == "orta"


# ---------------------------------------------------------------- önbellekli girdi fiyatı

def test_cost_counts_cached_tokens_at_discount():
    from app import ai_costs

    t = ai_costs.DEFAULT_PRICES
    lite = ai_costs.LITE
    assert ai_costs._cost(lite, 2000, 200, 0, t) == pytest.approx((2000 * 0.25 + 200 * 1.5) / 1e6)
    # 1500 önbellekli: 500 × 0,25 + 1500 × 0,025
    assert ai_costs._cost(lite, 2000, 200, 0, t, 1500) == pytest.approx((500 * 0.25 + 1500 * 0.025 + 200 * 1.5) / 1e6)
    # fiyat kaydında cached yoksa girdi × CACHED_RATIO; önbellekli girdiden fazla olamaz
    own = {lite: {"input": 1.0, "output": 2.0}}
    assert ai_costs._cost(lite, 1000, 0, 0, own, 400) == pytest.approx((600 + 400 * ai_costs.CACHED_RATIO) / 1e6)
    assert ai_costs._cost(lite, 1000, 0, 0, own, 5000) == pytest.approx(1000 * ai_costs.CACHED_RATIO / 1e6)


def test_cached_tokens_recorded_and_priced(gemini):
    from app import ai_costs, system

    gemini["usage"] = {"promptTokenCount": 2000, "candidatesTokenCount": 200, "cachedContentTokenCount": 1500}
    receipt._parse_document_ai(b"foto", "image/jpeg", CATS)
    with system.connect() as c:
        row = dict(c.execute("SELECT kind, input, output, cached FROM ai_calls ORDER BY id DESC LIMIT 1").fetchone())
    assert row == {"kind": "fis", "input": 2000, "output": 200, "cached": 1500}
    fis = next(k for k in ai_costs.summary()["kinds"] if k["kind"] == "fis")
    assert fis["cost_usd"] == pytest.approx(round((500 * 0.25 + 1500 * 0.025 + 200 * 1.5) / 1e6, 4))


def test_admin_cached_price_optional_and_validated():
    from app import ai_costs

    ai_costs.set_settings({"m": {"input": 1, "output": 2, "cached": 0.1}, "n": {"input": 1, "output": 2}})
    assert ai_costs.prices()["m"]["cached"] == 0.1 and "cached" not in ai_costs.prices()["n"]
    with pytest.raises(ValueError):
        ai_costs.set_settings({"m": {"input": 1, "output": 2, "cached": "çok"}})
    with pytest.raises(ValueError):
        ai_costs.set_settings({"m": {"input": 1, "output": 2, "cached": -1}})
    ai_costs.set_settings({})
    assert ai_costs.prices() == ai_costs.DEFAULT_PRICES


def test_bench_cost_counts_cached():
    from app import receipt_bench as rb

    u = {"promptTokenCount": 2000, "cachedContentTokenCount": 1500, "candidatesTokenCount": 200}
    assert rb.cost_usd(u) == pytest.approx((500 * 0.25 + 1500 * 0.025 + 200 * 1.5) / 1e6)
    assert rb._tokens(u)["cached"] == 1500
    assert json.dumps(rb._tokens({}))  # eksik alanlar None
