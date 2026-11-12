"""Yapay zekâ maliyet güvenliği ve ölçümü: çıktı token sınırı (fiş/fatura 8000), yapay zekâ yolunda sayfa sınırı
(HESAPKITAP_AI_MAX_PAGES), döküm tekrar denemesi en çok 2, her çağrının token kaydı (içeriksiz) ve yönetici özeti."""
import json

import httpx
import pytest

from tests import test_plans
from tests.test_plans import _user

hosted = test_plans.hosted


@pytest.fixture()
def fake(monkeypatch):
    """Sahte Gemini: gönderilen gövdeleri saklar, usageMetadata döndürür."""
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    sent = []
    reply = {"doc_type": "fis", "merchant": "Örnek Market", "date": "2026-11-01", "total": 42.5, "items": [],
             "confidence": "yüksek", "provider": "Örnek Elektrik", "amount": 100.0, "due_date": "2026-11-20"}

    def post(url, json=None, timeout=None, headers=None):
        sent.append(json)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(reply)}]}}],
            "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 300, "thoughtsTokenCount": 100}})

    monkeypatch.setattr(receipt.httpx, "post", post)
    return sent


def _calls():
    from app import system

    with system.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ai_calls ORDER BY id")]


def test_receipt_and_bill_output_cap(fake):
    from app import receipt

    receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
    receipt.parse_bill(b"%PDF-x", "image/jpeg")
    assert [b["generationConfig"]["maxOutputTokens"] for b in fake] == [8000, 8000]
    # görüntü ayarına dokunulmadı; fiş çağrısında düşünme düzeyi varsayılan "low" (app/receipt.py receipt_thinking),
    # fatura çağrısında düşünme ayarı yok
    assert all("mediaResolution" not in b["generationConfig"] for b in fake)
    assert fake[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "thinkingConfig" not in fake[1]["generationConfig"]


def test_usage_recorded_without_content(fake):
    from app import receipt

    receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
    receipt.parse_bill(b"fatura", "image/jpeg")
    receipt.gemini_text_json("işyeri adları")
    rows = _calls()
    assert [r["kind"] for r in rows] == ["fis", "belge", "kategori"]
    assert (rows[0]["input"], rows[0]["output"], rows[0]["thoughts"], rows[0]["model"]) == (
        1200, 300, 100, "gemini-3.1-flash-lite")
    cols = set(rows[0])
    assert cols == {"id", "at", "month", "user_id", "kind", "model", "input", "output", "thoughts", "cached"}  # metin yok
    assert "Örnek" not in json.dumps(rows, ensure_ascii=False)


def test_document_kind_from_answer(fake, monkeypatch):
    from app import receipt

    receipt._parse_document_ai(b"foto", "image/jpeg", ["Market & Gıda"])  # yanıtta due_date var → belge
    assert _calls()[-1]["kind"] == "belge"


def test_ai_page_limit_only_on_ai_path(fake, monkeypatch):
    from app import parsers, receipt

    monkeypatch.setattr(receipt, "_pdf_pages", lambda d, m: 51 if m == "application/pdf" else None)
    with pytest.raises(receipt.ReceiptError, match="Bu belge 51 sayfa; yapay zekâ en çok 50 sayfalık belge okur"):
        receipt._gemini_json("x", b"%PDF", "application/pdf")
    assert not fake  # yapay zekâya gidilmedi
    monkeypatch.setenv("HESAPKITAP_AI_MAX_PAGES", "60")
    receipt._gemini_json("x", b"%PDF", "application/pdf")
    assert len(fake) == 1
    # PDF'ten çıkarılmış metin (banka dökümü yapay zekâ yolu): bilinen sayfa sayısı
    monkeypatch.undo()
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    monkeypatch.setattr(receipt.httpx, "post", lambda *a, **k: pytest.fail("yapay zekâya gidilmemeli"))
    text = "\n".join(f"sayfa {i}" for i in range(55))
    parsers._remember_pages(text, [f"sayfa {i}" for i in range(55)])
    with pytest.raises(receipt.ReceiptError, match="55 sayfa"):
        receipt._gemini_json("x", text.encode(), "text/plain")


def test_bank_pdf_retries_at_most_twice(monkeypatch):
    from app import bank_pdf

    calls = []
    monkeypatch.setattr(bank_pdf, "parse_rule_based", lambda t: None)
    monkeypatch.setattr(bank_pdf.receipt, "enabled", lambda: True)
    from app import templates

    monkeypatch.setattr(templates, "read", lambda t: None)

    def bad(text):
        calls.append(1)
        return {"verified": False, "closing_ok": False, "chain_bad": [1], "rows": []}

    monkeypatch.setattr(bank_pdf, "_parse_text", bad)
    bank_pdf._parse_account_text("döküm metni")
    assert len(calls) == 2


def test_admin_summary_masks_users_and_prices(hosted, fake):
    from app import ai_costs, db, receipt, system

    c, u = _user(hosted)
    with db.use_space(system.user_db_path(u), u):
        for _ in range(3):
            receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
        receipt.parse_bill(b"fatura", "image/jpeg")
    assert c.get("/api/admin/ai-costs").status_code == 403  # yönetici değil
    s = hosted.get("/api/admin/ai-costs").json()
    fis = next(k for k in s["kinds"] if k["kind"] == "fis")
    assert (fis["calls"], fis["avg_input"], fis["avg_output"], fis["avg_thoughts"]) == (3, 1200, 300, 100)
    # 3 × (1200 × 0,25 + 400 × 1,50) / 1M = 0,0027 $
    assert fis["cost_usd"] == pytest.approx(0.0027)
    assert fis["cost_try"] == pytest.approx(round(0.0027 * ai_costs.DEFAULT_USD_TRY, 2))
    top = s["top_users"][0]
    assert top["calls"] == 4 and top["user"] == f"#{u['id']} · ay***@o***.com" and "ayse" not in json.dumps(s)
    # fiyat ve kur değiştirilebilir
    r = hosted.put("/api/admin/ai-costs", json={"prices": {"gemini-3.1-flash-lite": {"input": 1, "output": 2}},
                                               "usd_try": 40})
    fis = next(k for k in r.json()["kinds"] if k["kind"] == "fis")
    assert fis["cost_usd"] == pytest.approx(3 * (1200 + 400 * 2) / 1e6) and r.json()["usd_try"] == 40
    assert hosted.put("/api/admin/ai-costs", json={"usd_try": -1}).status_code == 422
    assert hosted.put("/api/admin/ai-costs", json={"prices": {"m": {"input": 1}}}).status_code == 422
    hosted.put("/api/admin/ai-costs", json={"prices": {}})
    assert ai_costs.prices() == ai_costs.DEFAULT_PRICES
    assert hosted.get("/api/admin/ai-costs?month=2000-01").json()["calls"] == 0


def test_prune_old_records():
    from app import ai_costs, system

    with system.connect() as c:
        c.execute("INSERT INTO ai_calls (at, month, kind, model) VALUES ('2000-01-01', '2000-01', 'fis', 'm')")
    ai_costs.record(None, "fis", "m", {})
    assert ai_costs.prune() == 1 and len(_calls()) == 1
    assert ai_costs.mask(None, None).startswith("Hesapsız")
