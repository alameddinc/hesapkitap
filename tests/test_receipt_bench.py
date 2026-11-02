"""Fiş okuma kıyası (app/receipt_bench.py): ölçütler, karma yolun kabul kuralı, yapay zekâ yalıtımı (kota/sayaç/maliyet
kaydı yazılmaz), salt okunur veri yükleme ve kişisel veri sızdırmayan çıktı. Görüntüler ve fiş metinleri uydurmadır."""
import io
import json

import httpx
import pytest

from app import receipt_bench as rb

FIS = """ORNEK BAKKAL
ORNEK MAH. DEMO SK. NO:1
TARİH: 12.10.2026 SAAT: 10:15
SUT 1L *41,20
EKMEK *17,50
İNDİRİM *5,00
TOPKDV *4,87
TOPLAM *53,70
KREDI KARTI *53,70
"""


def _jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 60), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _case(**kw) -> rb.Case:
    base = dict(image=_jpeg(), mime="image/jpeg", amount=53.70, on="2026-10-12", description="Örnek Bakkal",
                linked=True, stored_items=[{"amount": 41.20, "price_known": 1}, {"amount": 17.50, "price_known": 1}],
                categories=["Market & Gıda", "Diğer"])
    return rb.Case(**{**base, **kw})


def _it(name, amount):
    return {"name": name, "amount": amount}


def test_split_suspects_rule():
    assert rb.split_suspects([_it("SUT 1L", 40.0), _it("EKMEK", 15.0)]) == 0
    # bir ürün iki satıra yazılmış: ilk yarı fiyatsız ayrı kalem
    assert rb.split_suspects([_it("ULKER CIKOLATALI", None), _it("GOFRET 36GR", 12.5), _it("SU", 5.0)]) == 1
    assert rb.split_suspects([_it("DETERJAN", 90.0), _it("500 GR", 4.0)]) == 1  # devam satırı: yalnız gramaj
    assert rb.split_suspects([_it("PEYNIR -", 90.0), _it("BEYAZ", 4.0)]) == 1   # bağlaçla biten ad
    assert rb.split_suspects([_it("TEK URUN", None)]) == 1
    assert rb.continuation_like("x6") and rb.continuation_like("ab") and rb.continuation_like("1 KG")
    assert not rb.continuation_like("ELMA") and not rb.continuation_like("SU")


def test_structure_and_merchant():
    s = rb.structure([_it("ELMA", 40.0), _it("ARMUT", 15.0)], 50.0, [-5.0])
    assert s == {"n_items": 2, "priceless": 0, "sum_ok": True, "splits": 0, "ok": True}
    s = rb.structure([_it("ELMA", 40.0), _it("ARMUT", None)], 40.0)
    assert s["priceless"] == 1 and s["sum_ok"] and not s["ok"]
    assert rb.merchant_ok(["ÖRNEK BAKKAL GIDA SAN. TİC. LTD. ŞTİ."], "Örnek Bakkal")
    assert rb.merchant_ok([None, "Ornek Bakkalcilik"], "ORNEK BAKKAL")
    assert not rb.merchant_ok(["Fiş"], "Örnek Bakkal") and not rb.merchant_ok(["DEMO KIRTASIYE"], "Örnek Bakkal")


def test_accept_local_conditions():
    doc = {"type": "receipt", "merchant": "ORNEK", "date": "2026-10-12", "total": 50.0,
           "items": [_it("A", 40.0), _it("B", 15.0)]}
    assert rb.accept_local(doc, [-5.0]) == (True, [])
    assert rb.accept_local(doc) == (False, ["toplam_tutmuyor"])
    bad = {**doc, "merchant": "Fiş", "date": None, "items": [_it("A", None)]}
    ok, why = rb.accept_local(bad)
    assert not ok and {"isyeri_yok", "tarih_yok", "fiyatsiz_kalem", "toplam_tutmuyor"} <= set(why)
    assert rb.accept_local(None) == (False, ["tur"])


def test_local_read_uses_discount_lines(monkeypatch):
    from app import ocr

    calls = []
    monkeypatch.setattr(ocr, "text_of", lambda data, mime: calls.append(1) or FIS)
    doc, disc = rb.local_read(_case())
    assert len(calls) == 1 and disc == [-5.0]  # OCR bir kez
    assert [i["name"] for i in doc["items"]] == ["SUT 1L", "EKMEK"] and rb.accept_local(doc, disc)[0]


def _fake_gemini(monkeypatch, payload: dict, usage: dict):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def post(url, json=None, timeout=None, headers=None):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": __import__("json").dumps(payload)}]}}],
                                         "usageMetadata": usage}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)


def test_ai_read_is_isolated_and_captures_thoughts(monkeypatch):
    from app import ai_costs, receipt, system

    _fake_gemini(monkeypatch, {"doc_type": "fis", "merchant": "Örnek Bakkal", "date": "2026-10-12", "total": 50.0,
                               "items": [{"name": "SUT 1L", "amount": 40.0}, {"name": "EKMEK", "amount": 10.0}]},
                 {"promptTokenCount": 1200, "candidatesTokenCount": 300, "thoughtsTokenCount": 100})
    hits = []
    monkeypatch.setattr(ai_costs, "record", lambda *a, **k: hits.append("cost"))
    monkeypatch.setattr(system, "ai_add", lambda *a, **k: hits.append("ai_add"))
    real = receipt._daily_gate
    doc, usage = rb.ai_read(_case())
    assert doc["total"] == 50.0 and usage["thoughtsTokenCount"] == 100 and hits == []
    assert receipt._daily_gate is real  # yamalar geri alındı
    assert rb.cost_usd(usage) == pytest.approx((1200 * 0.25 + 400 * 1.5) / 1e6)


def test_bench_case_and_summary_hybrid(monkeypatch):
    from app import ocr

    monkeypatch.setattr(ocr, "text_of", lambda data, mime: FIS)
    b_doc = {"type": "receipt", "merchant": "Örnek Bakkal", "date": "2026-10-12", "total": 53.70,
             "items": [_it("SUT 1L", 41.20), _it("EKMEK", 12.50)]}
    usage = {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 0}
    ai = lambda case: (b_doc, usage)  # noqa: E731
    row = rb.bench_case(_case(), ai=ai)
    assert row["A"]["total_ok"] and row["A"]["date_ok"] and row["A"]["merchant_ok"]
    assert row["C"]["accepted_local"] and row["C"]["usd"] == 0 and not row["C"]["wrong_accept"]
    # yerel toplam yanlış okunmuş ama kendi içinde tutarlı: kabul edilir → yanlış kabul
    wrong = rb.bench_case(_case(amount=63.70), ai=ai)
    assert wrong["C"]["accepted_local"] and wrong["C"]["wrong_accept"]
    # yerel kalemler tutmuyor: yapay zekâya gider, maliyet B'ninki
    monkeypatch.setattr(ocr, "text_of", lambda data, mime: FIS.replace("İNDİRİM *5,00\n", ""))
    sent = rb.bench_case(_case(), ai=ai)
    assert not sent["C"]["accepted_local"] and sent["C"]["reasons"] == ["toplam_tutmuyor"]
    assert sent["C"]["usd"] == sent["B"]["usd"] > 0 and sent["C"]["total_ok"]
    s = rb.summarize([row, wrong, sent])
    assert s["C"]["accepted_local"] == 2 and s["C"]["wrong_accept"] == 1 and s["C"]["sent_to_ai"] == 1
    assert s["A"]["total_ok_pct"] == pytest.approx(66.7) and s["B"]["avg_input"] == 1000
    out = rb.render([row, wrong, sent], s)
    for secret in ("BAKKAL", "Bakkal", "SUT", "EKMEK", "53.7", "41.2", "2026-10-12"):  # ad, kalem, tutar, tarih çıktıda yok
        assert secret not in out
    json.dumps(s)


def test_ai_error_is_reported_by_type_only(monkeypatch):
    from app import ocr

    monkeypatch.setattr(ocr, "text_of", lambda data, mime: "")  # okunamayan fotoğraf

    def boom(case):
        raise RuntimeError("ORNEK BAKKAL 53,70")

    row = rb.bench_case(_case(), ai=boom)
    assert row["A"]["error"] == "ValueError" and row["B"]["error"] == "RuntimeError"
    assert not row["C"]["accepted_local"] and not row["C"]["total_ok"]
    assert "BAKKAL" not in json.dumps(row)


def test_load_cases_read_only(tmp_path, monkeypatch):
    from app import db, system

    u = system.create_user("bench@example.com", None, main_db=True)
    with db.use_space(None, None):
        db.store_file("receipts", "a" * 64 + ".jpg", _jpeg(), "image/jpeg")
        with db.connect() as c:
            eid = c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, receipt_file) "
                            "VALUES ('2026-10-12', '2026-10', 'Örnek Bakkal', 50.0, 'gider', 'Market & Gıda', ?)",
                            ("a" * 64 + ".jpg",)).lastrowid
            c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category) "
                      "VALUES ('2026-10-13', '2026-10', 'Fotoğrafsız', 10.0, 'gider', 'Diğer')")
            c.execute("INSERT INTO receipt_items (entry_id, purchased_at, name, product_key, amount, unit_price) "
                      "VALUES (?, '2026-10-12', 'SUT', 'SUT', 50.0, 50.0)", (eid,))
    before = (tmp_path / "isolated.db").stat().st_mtime_ns
    cases = rb.load_cases(u["id"])
    assert len(cases) == 1 and cases[0].amount == 50.0 and cases[0].image == _jpeg() and not cases[0].linked
    assert cases[0].stored_items == [{"amount": 50.0, "price_known": 1}] and "Diğer" in cases[0].categories
    assert (tmp_path / "isolated.db").stat().st_mtime_ns == before
