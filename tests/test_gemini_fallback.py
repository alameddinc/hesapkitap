"""Gemini yoğunluğu: 503/429/UNAVAILABLE ya da zaman aşımında bir kez yedek modele geçiş, süre sınırları, süre kaydı
(günlük + ai_calls.ms) ve eski şemada ms sütunu göçü. Sahte httpx; dışarıya istek yok, uydurma veri."""
import json
import logging
import sqlite3

import httpx
import pytest

REPLY = {"doc_type": "fis", "merchant": "Örnek Market", "date": "2026-11-01", "total": 42.5, "items": [],
         "confidence": "yüksek"}


def _ok():
    return httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": json.dumps(REPLY)}]}}],
        "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200}})


def _busy(status=503):
    return httpx.Response(status, json={"error": {"code": status, "status": "UNAVAILABLE",
                                                  "message": "This model is currently experiencing high demand"}})


@pytest.fixture()
def gemini(monkeypatch):
    """Sahte Gemini: model başına sıradaki yanıt (Response ya da yükseltilecek istisna); çağrılar kaydedilir."""
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    state = {"calls": [], "plan": {}}

    def post(url, json=None, timeout=None, headers=None):
        model = url.split("/models/")[1].split(":")[0]
        state["calls"].append({"model": model, "timeout": timeout, "body": json})
        r = state["plan"][model].pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(receipt.httpx, "post", post)
    return state


def _calls():
    from app import system

    with system.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ai_calls ORDER BY id")]


def test_503_switches_to_fallback_and_counts_fallback_model(gemini, caplog):
    from app import receipt

    gemini["plan"] = {"gemini-3.1-flash-lite": [_busy()], "gemini-3.5-flash": [_ok()]}
    with caplog.at_level(logging.INFO, logger="hesapkitap.receipt"):
        out = receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
    assert out["total"] == 42.5
    assert [c["model"] for c in gemini["calls"]] == ["gemini-3.1-flash-lite", "gemini-3.5-flash"]
    rows = _calls()
    assert len(rows) == 1 and rows[0]["model"] == "gemini-3.5-flash" and rows[0]["kind"] == "fis"
    assert isinstance(rows[0]["ms"], int) and rows[0]["ms"] >= 0
    # yedek flash, düzey verilmese de kısılmış düşünmeyle çağrılır
    assert gemini["calls"][1]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    lines = [r.getMessage() for r in caplog.records if "Gemini çağrısı" in r.getMessage()]
    assert len(lines) == 2 and "durum=503" in lines[0] and "durum=200" in lines[1]
    assert all("kind=fis" in l and "ms=" in l for l in lines)
    assert "Örnek" not in caplog.text  # içerik günlüğe yazılmaz


def test_429_also_switches(gemini):
    from app import receipt

    gemini["plan"] = {"gemini-3.1-flash-lite": [_busy(429)], "gemini-3.5-flash": [_ok()]}
    receipt.gemini_text_json("işyeri adları")
    assert _calls()[-1]["model"] == "gemini-3.5-flash"


def test_both_busy_raises_turkish_error(gemini):
    from app import receipt

    gemini["plan"] = {"gemini-3.1-flash-lite": [_busy()], "gemini-3.5-flash": [_busy()]}
    with pytest.raises(receipt.ReceiptError, match="şu an yanıt vermiyor"):
        receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
    assert len(gemini["calls"]) == 2 and _calls() == []


def test_timeout_switches_to_fallback_within_budget(gemini):
    from app import receipt

    gemini["plan"] = {"gemini-3.1-flash-lite": [httpx.ReadTimeout("yavaş")], "gemini-3.5-flash": [_ok()]}
    receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
    first, second = (c["timeout"] for c in gemini["calls"])
    assert (first.connect, first.read) == (10.0, 25.0)
    assert second.read <= 40.0 and second.connect <= 10.0  # yedek kalan bütçeyle
    assert _calls()[-1]["model"] == "gemini-3.5-flash"


def test_both_timeout_raises(gemini):
    from app import receipt

    gemini["plan"] = {"gemini-3.1-flash-lite": [httpx.ConnectTimeout("x")],
                      "gemini-3.5-flash": [httpx.ReadTimeout("x")]}
    with pytest.raises(receipt.ReceiptError, match="ulaşılamadı"):
        receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])


def test_non_busy_error_does_not_fallback(gemini):
    from app import receipt

    gemini["plan"] = {"gemini-3.1-flash-lite": [httpx.Response(403, json={"error": {"message": "API key invalid"}})]}
    with pytest.raises(receipt.ReceiptError, match="anahtarı geçersiz"):
        receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
    assert len(gemini["calls"]) == 1


def test_no_fallback_when_budget_spent(gemini, monkeypatch):
    from app import receipt

    monkeypatch.setenv("HESAPKITAP_GEMINI_TOTAL_S", "0.001")
    gemini["plan"] = {"gemini-3.1-flash-lite": [_busy()]}
    with pytest.raises(receipt.ReceiptError):
        receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
    assert len(gemini["calls"]) == 1


def test_fallback_model_configurable_and_differs_from_primary(gemini, monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")
    assert receipt.fallback_model("gemini-3.1-flash-lite") == "gemini-2.5-flash"
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
    assert receipt.fallback_model("gemini-3.1-flash-lite") == "gemini-3.5-flash"
    monkeypatch.delenv("GEMINI_FALLBACK_MODEL")
    assert receipt.fallback_model("gemini-3.5-flash") == "gemini-3.1-flash-lite"  # ana flash ise yedek lite


def test_long_documents_keep_long_timeout(gemini):
    from app import receipt

    gemini["plan"] = {"gemini-3.1-flash-lite": [_ok()]}
    receipt._gemini_json("döküm", b"metin", "text/plain", max_output_tokens=60000, kind="dokum")
    t = gemini["calls"][0]["timeout"]
    assert t.read == 180.0 and t.connect == 10.0


def test_thinking_400_retry_kept(gemini, monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")
    bad = httpx.Response(400, json={"error": {"message": "thinking_level is not supported"}})
    gemini["plan"] = {"gemini-3.5-flash": [bad, _ok()]}
    receipt._gemini_json("x", b"foto", "image/jpeg", max_output_tokens=8000, kind="fis", thinking="low")
    first, second = (c["body"]["generationConfig"] for c in gemini["calls"])
    assert "thinkingConfig" in first and "thinkingConfig" not in second
    assert [c["model"] for c in gemini["calls"]] == ["gemini-3.5-flash"] * 2


def test_old_schema_gets_ms_column(tmp_path, monkeypatch):
    from app import ai_costs, system

    p = str(tmp_path / "old_system.db")
    with sqlite3.connect(p) as c:  # ms sütunsuz eski ai_calls, içinde kayıt
        c.execute("CREATE TABLE ai_calls (id INTEGER PRIMARY KEY, at TEXT NOT NULL, month TEXT NOT NULL, user_id INTEGER,"
                  " kind TEXT NOT NULL, model TEXT, input INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0,"
                  " thoughts INTEGER NOT NULL DEFAULT 0, cached INTEGER NOT NULL DEFAULT 0)")
        c.execute("INSERT INTO ai_calls (at, month, kind, model) VALUES ('2026-01-01', '2026-01', 'fis', 'm')")
    monkeypatch.setattr(system, "SYSTEM_DB", p)
    ai_costs.record(None, "fis", "m", {}, ms=1234)
    with system.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT ms FROM ai_calls ORDER BY id")]
    assert rows == [{"ms": None}, {"ms": 1234}]
    system._ready.discard(p)
    with system.connect() as c:  # göç ikinci kez güvenli
        assert "ms" in {r[1] for r in c.execute("PRAGMA table_info(ai_calls)")}


def test_503_retries_same_model_once_before_fallback(gemini, monkeypatch):
    """Anlık yoğunluk: aynı model kısa beklemeyle bir kez daha denenir; olursa yavaş yedeğe gidilmez."""
    from app import receipt

    monkeypatch.setenv("HESAPKITAP_GEMINI_SAME_RETRY_S", "0.01")
    gemini["plan"] = {"gemini-3.1-flash-lite": [_busy(), _ok()]}
    out = receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
    assert out["total"] == 42.5 and out["model"] == "gemini-3.1-flash-lite"
    assert [c["model"] for c in gemini["calls"]] == ["gemini-3.1-flash-lite", "gemini-3.1-flash-lite"]
    rows = _calls()
    assert len(rows) == 1 and rows[0]["model"] == "gemini-3.1-flash-lite"


def test_503_twice_then_fallback(gemini, monkeypatch):
    from app import receipt

    monkeypatch.setenv("HESAPKITAP_GEMINI_SAME_RETRY_S", "0.01")
    gemini["plan"] = {"gemini-3.1-flash-lite": [_busy(), _busy(429)], "gemini-3.5-flash": [_ok()]}
    out = receipt.parse_receipt(b"foto", "image/jpeg", ["Market & Gıda"])
    assert out["model"] == "gemini-3.5-flash"
    assert [c["model"] for c in gemini["calls"]] == ["gemini-3.1-flash-lite"] * 2 + ["gemini-3.5-flash"]


def test_same_model_retry_setting():
    from app import receipt

    import os

    os.environ.pop("HESAPKITAP_GEMINI_SAME_RETRY_S", None)
    assert receipt.same_model_retry_s() == receipt.SAME_MODEL_RETRY_S == 0.8
    for v, want in (("0", 0.0), ("1.5", 1.5), ("x", 0.8), ("-2", 0.0)):
        os.environ["HESAPKITAP_GEMINI_SAME_RETRY_S"] = v
        assert receipt.same_model_retry_s() == want
