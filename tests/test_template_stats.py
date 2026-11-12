"""Şablon isabeti (app/template_stats.py) ve fiş şablonunun kör noktaları (app/receipt_templates.py): VKN'si sonradan
okunan işyeri, tek rakamı karışan metin / yapay zekâ okuması, güçlü modelle düzeltilen okumadan öğrenme, okuma kaçma
nedenleri, yönetici göstergesi. Bütün işyerleri, VKN'ler ve tutarlar uydurmadır."""
import json

from app import receipt_templates as rt, system, template_stats
from app.parsers.base import Statement
from tests.test_doc_reads import _jpeg, _used, hosted  # noqa: F401 - fixture
from tests.test_plans import _user
from tests.test_receipt_templates import (CATS, FIRST, SECOND, VKN, _day, _photo, _read, _rows, gemini,  # noqa: F401
                                          market, market_ai)


def _events() -> dict:
    with system.connect() as c:
        template_stats._init(c)
        return {(r["doc"], r["outcome"], r["reason"]): r["n"] for r in c.execute("SELECT * FROM template_events")}


def _no_vkn(text: str) -> str:
    """OCR VKN'yi bozmuş: vergi dairesi satırında kontrol hanesi tutan 10 hane yok."""
    return text.replace(f"DENEME VD: {VKN}", "DENEME VD: 99912 3")


def test_word_signature_template_used_when_vkn_read_later(tmpdb, gemini):
    # ilk fişte VKN okunamadı: şablon unvan kelimeleriyle (w:) öğrenilir
    gemini["replies"].append({**market_ai(FIRST, _day(5)), "tax_no": None})
    _read(_no_vkn(market(FIRST, _day(5))))
    rows = _rows()
    assert len(rows) == 1 and rows[0]["sig"].startswith("w:")
    # sonraki fişte VKN okundu: eskiden yalnız VKN şablonu denenirdi (yok) → yapay zekâ; şimdi yedek imza denenir
    r = _read(market(SECOND, _day(1)))
    assert r["source"] == "sablon" and gemini["calls"] == 1 and r["tax_no"] == VKN
    rows = _rows()
    assert len(rows) == 2 and rows[1]["sig"] == "vkn:" + rt._h(VKN, 16)  # VKN imzalı kopya kaydedildi
    assert json.loads(rows[1]["spec"])["sig"]["words"] == json.loads(rows[0]["spec"])["sig"]["words"]
    # üçüncü fiş doğrudan VKN şablonuyla
    assert _read(market(SECOND, _day(1)))["source"] == "sablon" and gemini["calls"] == 1
    assert _rows()[1]["uses"] == 1


def test_word_fallback_failure_not_counted(tmpdb, gemini):
    gemini["replies"].append({**market_ai(FIRST, _day(5)), "tax_no": None})
    _read(_no_vkn(market(FIRST, _day(5))))
    # VKN'li fiş, toplam satırı bozuk: yedek imzayla denenen şablonun hatası sayılmaz (başka işyeri olabilir)
    broken = market(SECOND, _day(1)).replace("ÇAY 1 KG   %1   *159,50\n", "")
    gemini["replies"].append({**market_ai(SECOND, _day(1)), "confidence": "düşük"})
    assert _read(broken)["source"] == "yapay_zeka"
    assert _rows()[0]["fails"] == 0 and rt.last_miss() == "sum"


def test_ocr_digit_miss_keeps_template_and_relearns(tmpdb, gemini):
    gemini["replies"].append(market_ai(FIRST, _day(5)))
    _read(market(FIRST, _day(5)))
    # telefon metninde 89,90 → 69,90 (termal fişte 8↔6): toplam tutmaz, şablon kabul edilmez ama hata sayılmaz
    noisy = market(SECOND, _day(1)).replace("*89,90", "*69,90")
    for n in (2, 3):
        gemini["replies"].append(market_ai(SECOND, _day(1)))
        assert _read(noisy)["source"] == "yapay_zeka" and gemini["calls"] == n
        assert rt.last_miss() == "ocr_digit"
    row = _rows()[0]
    assert row["fails"] == 0 and not row["disabled"] and len(_rows()) == 1  # aynı düzen: yeni şablon yok
    assert _events()[("fis", "learn", "known")] == 2  # bozuk metinden de aynı düzen doğrulandı (tek rakam yolu)
    assert _read(market(SECOND, _day(1)))["source"] == "sablon"


def test_learn_when_ai_misreads_one_digit(tmpdb, gemini):
    # yapay zekâ 89,90'ı 69,90 okudu (kalemler toplamı tutmuyor); telefon metni doğru: eskiden öğrenilmezdi
    ai = market_ai(SECOND, _day(3))
    ai["items"][0]["amount"] = 69.9
    gemini["replies"].append(ai)
    _read(market(SECOND, _day(3)))
    assert len(_rows()) == 1 and _events()[("fis", "learn", "learned_digit")] == 1
    r = _read(market(FIRST, _day(1)))
    assert r["source"] == "sablon" and gemini["calls"] == 1 and r["total"] == 65.0


def test_no_learn_when_difference_not_a_confusable_digit(tmpdb, gemini):
    ai = market_ai(SECOND, _day(3))
    ai["items"][0]["amount"] = 29.9  # 8 → 2 karışan eş değil: düzen kanıtlanmadı
    gemini["replies"].append(ai)
    _read(market(SECOND, _day(3)))
    assert _rows() == [] and _events()[("fis", "learn", "ai_sum")] == 1
    # iki kalem birden farklı: yine öğrenilmez
    ai = market_ai(SECOND, _day(3))
    ai["items"][0]["amount"], ai["items"][1]["amount"] = 69.9, 179.5
    gemini["replies"].append(ai)
    _read(market(SECOND, _day(2)))
    assert _rows() == []


def test_one_digit_off_rules():
    doc = {"total": 100.0, "date": "2026-01-02", "items": [{"amount": 89.0}, {"amount": 11.0}]}
    got = {**doc, "items": [{"amount": 69.0}, {"amount": 11.0}]}
    assert rt._one_digit_off(got, doc)
    assert not rt._one_digit_off({**got, "date": "2026-01-03"}, doc)
    assert not rt._one_digit_off({**got, "total": 80.0}, doc)
    assert not rt._one_digit_off({**got, "items": [{"amount": 29.0}, {"amount": 11.0}]}, doc)  # 8→2 eş değil
    assert not rt._one_digit_off({**got, "items": [{"amount": 66.0}, {"amount": 11.0}]}, doc)  # iki rakam
    assert not rt._one_digit_off({**got, "items": [{"amount": 69.0}]}, doc)  # kalem sayısı farklı
    assert not rt._one_digit_off(doc, doc)  # aynısı: tek-rakam yolu değil (birebir yol)
    one = {"total": 89.0, "date": "2026-01-02", "items": [{"amount": 89.0}]}
    assert not rt._one_digit_off({**one, "items": [{"amount": 69.0}]}, one)  # tek kalemde ayrım yok


def test_miss_reasons():
    assert rt.reject_reason(None) == "no_total"
    base = {"total": 100.0, "date": rt._today().isoformat(), "items": [{"amount": 89.0, "qty": 1}, {"amount": 11.0}]}
    assert rt.reject_reason(base) is None and rt.acceptable(base)
    assert rt.reject_reason({**base, "items": []}) == "no_items"
    assert rt.reject_reason({**base, "items": [{"amount": 69.0}, {"amount": 11.0}]}) == "ocr_digit"
    assert rt.reject_reason({**base, "items": [{"amount": 21.0}, {"amount": 11.0}]}) == "sum"
    assert rt.reject_reason({**base, "items": [{"amount": 69.0}, {"amount": 11.0}]}, ignore_sum=True) is None
    assert rt.reject_reason({**base, "date": None}) == "date"
    assert rt.reject_reason({**base, "_bad": True}) == "qty"
    assert rt._later("no_template", "sum") == "sum" and rt._later("date", "no_structure") == "date"


def test_last_miss_no_template_and_no_text(tmpdb):
    assert rt.read(None) is None and rt.last_miss() == "no_text"
    assert rt.read(market(FIRST, _day(1)), CATS) is None and rt.last_miss() == "no_template"


def test_learn_after_auto_reread(hosted, gemini):  # noqa: F811
    c, _ = _user(hosted)

    def parse(text, color):
        return c.post("/api/documents/parse", files=[("file", ("f.jpg", _photo(color), "image/jpeg"))],
                      data={"ocr_text": text, "ocr_source": "vision"}).json()

    # ilk okuma: bir kalem eksik (kalemler tutmuyor, tek rakam da değil) → öğrenilmez; güçlü model düzeltir → öğrenilir
    bad = market_ai(SECOND, _day(3))
    bad["items"] = bad["items"][1:]
    good = market_ai(SECOND, _day(3))
    gemini["replies"] += [bad, {"items": good["items"], "total": good["total"], "confidence": "yüksek"}]
    r = parse(market(SECOND, _day(3)), "white")
    assert r["reread"]["used"] and gemini["calls"] == 2
    assert len(_rows()) == 1
    r2 = parse(market(FIRST, _day(1)), "gray")
    assert r2["source"] == "sablon" and gemini["calls"] == 2
    ev = _events()
    assert ev[("fis", "learn", "ai_sum")] == 1 and ev[("fis", "learn", "learned")] == 1
    assert ev[("fis", "ai", "no_template")] == 1 and ev[("fis", "sablon", "-")] == 1
    a = hosted.get("/api/admin/receipt-templates").json()["hit_rate"]
    assert a["fis"]["reads"] == 2 and a["fis"]["free"] == 1 and a["fis"]["rate"] == 50
    assert a["fis"]["miss_reasons"] == {"no_template": 1} and a["since"] == rt._today().isoformat()
    assert VKN not in json.dumps(a)


def test_api_receipt_without_phone_text_counted(hosted, gemini):  # noqa: F811
    c, _ = _user(hosted)
    gemini["replies"].append(market_ai(FIRST, _day(2)))
    c.post("/api/documents/parse", files=[("file", ("f.jpg", _photo("white"), "image/jpeg"))])
    assert _events()[("fis", "ai", "no_text")] == 1


def test_statement_outcomes_and_overview(tmpdb):
    def st(source, ai=False, miss=None):
        s = Statement(bank="Örnek", card_no="**** 0000", period_end=_day(10), due_date=_day(1), total_debt=0.0,
                      min_payment=0.0, credit_limit=None)
        s.source, s.ai_parsed = source, ai
        if miss:
            s.template_miss = miss
        return s

    template_stats.record_statement(st("parser"))
    template_stats.record_statement(st("sablon"))
    template_stats.record_statement(st("yapay_zeka", True, "text_cid"))
    template_stats.record_statement(st("yapay_zeka", True, "no_template"))
    template_stats.record("dokum", "sablon")
    template_stats.record("fis", "ai", "Bad Code!")  # geçersiz kod içerik taşımasın: "-"
    template_stats.record("nope", "ai")  # bilinmeyen tür sayılmaz
    o = template_stats.overview()
    assert o["ekstre"]["reads"] == 4 and o["ekstre"]["free"] == 2 and o["ekstre"]["rate"] == 50
    assert o["ekstre"]["miss_reasons"] == {"text_cid": 1, "no_template": 1}
    assert o["dokum"]["rate"] == 100 and o["fis"]["miss_reasons"] == {"-": 1} and o["fis"]["rate"] == 0
    assert _events().get(("nope", "ai", "-")) is None
    assert template_stats.prune() == 0


def test_statement_upload_counted(client, tmpdb):
    from tests.conftest import fixture_bytes

    r = client.post("/api/statements/upload",
                    files=[("files", ("v.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))])
    assert r.status_code == 200, r.text
    assert _events()[("ekstre", "parser", "-")] == 1
