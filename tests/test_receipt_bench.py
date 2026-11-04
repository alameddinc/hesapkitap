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


def test_text_path_and_fallback(monkeypatch, tmp_path):
    from app import ocr

    monkeypatch.setattr(ocr, "text_of", lambda data, mime: FIS)
    b_doc = {"type": "receipt", "merchant": "Örnek Bakkal", "date": "2026-10-12", "total": 53.70,
             "items": [_it("SUT 1L", 41.20), _it("EKMEK", 12.50)]}
    usage = {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 0}
    t_usage = {"promptTokenCount": 400, "candidatesTokenCount": 200, "thoughtsTokenCount": 0}
    ai = lambda case: (b_doc, usage)  # noqa: E731
    good = lambda case: (b_doc, t_usage)  # noqa: E731
    row = rb.bench_case(_case(text=FIS), ai=ai, text_ai=good)
    assert row["T"]["total_ok"] and row["T"]["ok"] and not row["T+"]["fallback"]
    assert row["T+"]["usd"] == row["T"]["usd"] < row["B"]["usd"]
    assert row["U"]["usd"] == row["B"]["usd"] and row["U"]["total_ok"] and not row["U"]["local"]  # U: B + metin
    # metinden fiyatsız kalem: fotoğrafla bir kez daha (B'nin sonucu ve maliyeti eklenir)
    bad = lambda case: ({**b_doc, "items": [_it("SUT 1L", 41.20), _it("EKMEK", None)]}, t_usage)  # noqa: E731
    fb = rb.bench_case(_case(text=FIS), ai=ai, text_ai=bad)
    assert fb["T+"]["fallback"] and fb["T"]["priceless"] == 1 and fb["T+"]["priceless"] == 0
    assert fb["T+"]["usd"] == pytest.approx(fb["T"]["usd"] + fb["B"]["usd"])
    assert "T" not in rb.bench_case(_case(), ai=ai, text_ai=good)  # metin yoksa T yok
    s = rb.summarize([row, fb])
    assert s["T"]["n"] == 2 and s["T+"]["fallback"] == 1 and s["T"]["avg_input"] == 400 and s["T+"]["sum_ok_pct"] == 100
    assert s["U"]["n"] == 2 and s["U"]["local"] == 0 and s["U"]["usd_per_receipt"] == s["B"]["usd_per_receipt"]
    out = rb.render([row, fb], s)
    assert "→foto" in out and "BAKKAL" not in out and "SUT" not in out
    # metin dosyası eşlemesi: <fiş dosyası adı uzantısız>.txt
    (tmp_path / ("a" * 64 + ".txt")).write_text("ORNEK\nTOPLAM 1,00\n", encoding="utf-8")
    assert rb.text_for(_case(key="a" * 64 + ".jpg"), str(tmp_path)) == "ORNEK\nTOPLAM 1,00"
    assert rb.text_for(_case(key="b" * 64 + ".jpg"), str(tmp_path)) is None


def test_ai_text_read_is_isolated(monkeypatch):
    sent = []
    _fake_gemini(monkeypatch, {"doc_type": "fis", "merchant": "Örnek", "total": 5.0, "items": []},
                 {"promptTokenCount": 300, "candidatesTokenCount": 50})
    import httpx as _h

    real = _h.post
    monkeypatch.setattr(_h, "post", lambda *a, **k: sent.append(k["json"]) or real(*a, **k))
    doc, usage = rb.ai_text_read(_case(text="ORNEK\nTOPLAM 5,00"))
    assert doc["total"] == 5.0 and usage["promptTokenCount"] == 300
    assert not any("inline_data" in p for p in sent[0]["contents"][0]["parts"])


def test_export_photos_refuses_repo_dir(monkeypatch):
    import os

    with pytest.raises(SystemExit):
        rb.export_photos([1], os.path.join(os.path.dirname(rb.__file__), "..", "tmp-fotolar"))


def test_dir_mode_photo_vs_text(monkeypatch, tmp_path, capsys):
    """--photo-dir: klasördeki (uydurma) fotoğraflar; metni olan fişte B ↔ T kıyası, olmayanda yalnız B. Çıktıda dosya
    adı, işyeri ya da kalem adı yok."""
    for name in ("fis-ornek-1.jpg", "fis-ornek-2.png"):
        (tmp_path / name).write_bytes(_jpeg())
    (tmp_path / "fis-ornek-1.txt").write_text(FIS, encoding="utf-8")
    (tmp_path / "notlar.md").write_text("fotoğraf değil", encoding="utf-8")
    cases = list(rb.iter_dir_cases(str(tmp_path)))
    assert [c.key for c in cases] == ["fis-ornek-1.jpg", "fis-ornek-2.png"]
    assert cases[0].text and cases[0].text.startswith("ORNEK BAKKAL") and cases[1].text is None
    assert all(c.mime == "image/jpeg" and c.amount is None for c in cases)  # uygulamadaki gibi JPEG'e hazırlanır

    b_doc = {"type": "receipt", "merchant": "Örnek Bakkal", "date": "2026-10-12", "total": 53.70,
             "items": [_it("SUT 1L", 41.20), _it("EKMEK", 12.50)]}
    usage = {"promptTokenCount": 1500, "candidatesTokenCount": 200, "thoughtsTokenCount": 0}
    t_usage = {"promptTokenCount": 500, "candidatesTokenCount": 200, "thoughtsTokenCount": 0}
    two_line = {**b_doc, "date": "2026-10-20", "items": [_it("ULKER CIKOLATALI", None), _it("GOFRET", 41.20),
                                                         _it("EKMEK", 12.50)]}
    row = rb.bench_pair(cases[0], ai=lambda c: (b_doc, usage), text_ai=lambda c: (b_doc, t_usage))
    assert row["agree"] == {"total": True, "date": True, "merchant": True} and not row["T+"]["fallback"]
    assert row["T+"]["usd"] == row["T"]["usd"] < row["B"]["usd"]
    # U (uygulamadaki yol): B'nin sonucu, KDV metinden (TOPKDV); maliyet B
    assert row["U"]["found"]["vat"] and not row["B"]["found"]["vat"] and row["U"]["usd"] == row["B"]["usd"]
    assert b_doc.get("vat_total") is None  # B'nin sonucu değişmez
    none_b = rb.bench_pair(cases[0], ai=lambda c: (None, {}), text_ai=lambda c: (b_doc, t_usage))
    assert none_b["U"]["local"] and none_b["U"]["found"]["total"] and not none_b["B"]["receipt"]
    bad = rb.bench_pair(cases[0], ai=lambda c: (b_doc, usage), text_ai=lambda c: (two_line, t_usage))
    assert bad["T"]["priceless"] == 1 and bad["T"]["splits"] >= 1 and bad["T+"]["fallback"]
    assert bad["T+"]["priceless"] == 0 and bad["agree"]["total"] and not bad["agree"]["date"]
    assert bad["T+"]["usd"] == pytest.approx(bad["T"]["usd"] + bad["B"]["usd"])
    only_b = rb.bench_pair(cases[1], ai=lambda c: (b_doc, usage), text_ai=lambda c: pytest.fail("metin yok"))
    assert "T" not in only_b and not only_b["has_text"]

    s = rb.summarize_pair([row, bad, only_b])
    assert s["n"] == 3 and s["with_text"] == 2 and s["B"]["n"] == 3 and s["T"]["n"] == 2
    assert s["T"]["receipts_with_priceless"] == 1 and s["T+"]["fallback"] == 1 and s["T"]["avg_input"] == 500
    assert s["T_vs_B"] == {"n": 2, "total_same_pct": 100.0, "date_same_pct": 50.0, "merchant_same_pct": 100.0}
    assert s["U"]["n"] == 2 and s["U"]["vat_found_pct"] == 100.0 and s["B"]["vat_found_pct"] == 0.0
    assert s["U_cost_vs_T+_pct"] < 100
    out = rb.render_pair([row, bad, only_b], s)
    assert "→foto" in out and "metin yok" in out
    for secret in ("ornek", "Örnek", "BAKKAL", "SUT", "GOFRET", "53.7"):
        assert secret not in out.split("\n{")[0]  # satırlarda içerik yok (özet yalnız sayı)
    assert "ornek" not in json.dumps(s).lower()

    # uçtan uca (sahte Gemini): anahtar ortamdan, defter açılmaz, çıktı JSON'unda dosya adı yok
    _fake_gemini(monkeypatch, {"doc_type": "fis", "merchant": "Örnek Bakkal", "date": "2026-10-12", "total": 53.70,
                               "items": [{"name": "SUT 1L", "amount": 41.20}, {"name": "EKMEK", "amount": 12.50}]},
                 {"promptTokenCount": 800, "candidatesTokenCount": 100})
    assert rb.main(["--photo-dir", str(tmp_path), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["summary"]["n"] == 2 and res["summary"]["T"]["n"] == 1 and res["summary"]["T+"]["fallback"] == 0
    assert "fis-ornek" not in json.dumps(res)


def test_dir_mode_needs_key_and_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        rb.main(["--photo-dir", str(tmp_path / "yok")])
    with pytest.raises(SystemExit):
        rb.main(["--photo-dir", str(tmp_path)])  # anahtar yok
    with pytest.raises(SystemExit):
        rb.main([])  # ne --user-id ne --photo-dir
