"""Yazı tipi kodlu PDF (Akbank: Type3, ToUnicode yok, kodlar belgeden belgeye değişir) metnini glif şekli → karakter
eşlemesiyle kurtarma (app/glyphs.py), kurtarılan metinle kart şablonu öğrenme / okuma ve geriye dönük öğrenme.
PDF'ler tests/type3_pdf.py ile üretilir; içerik uydurma (tests/card_layouts.py akbank_pdf). Varsayılan Pillow yazı
tipinde Türkçe harf yok: metin Türkçe karaktersiz (card_reader etiketleri zaten katlayarak arar)."""
import io
import re
import shutil
import subprocess

import pdfplumber
import pytest

from app import card_reader, glyphs, parsers
from tests import card_layouts as L
from tests import type3_pdf as T
from tests.test_card_templates import _cards, gemini  # noqa: F401 - fixture
from tests.test_plans import _user, hosted  # noqa: F401 - fixture

FOLD = str.maketrans("şŞğĞıİöÖüÜçÇ", "sSgGiIoOuUcC")


def _lines(lay: L.Layout) -> list[str]:
    return lay.text.translate(FOLD).splitlines()


def _raw(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as p:
        return "\n".join(pg.extract_text() or "" for pg in p.pages)


def _seed(text: str) -> None:
    """Eşlemeyi elle öğret (OCR'sız testler): her glife yeterli oy."""
    from collections import Counter

    glyphs.add_votes(Counter({(s, ch): 5 for ch, s in T.shapes(text).items()}))


def _st():
    from app.parsers.base import Statement

    return Statement("Akbank", "****", None, None, 0.0, None, None)


@pytest.fixture(autouse=True)
def _fresh_text_cache():
    parsers._TEXTS.clear()  # aynı PDF baytları başka testte başka eşlemeyle çözülmüş olabilir
    yield
    parsers._TEXTS.clear()


@pytest.fixture()
def no_ocr(monkeypatch):
    monkeypatch.setattr(glyphs, "ocr_available", lambda: False)


def test_type3_pdf_without_unicode_map_reads_as_cid_codes():
    pdf = T.make(_lines(L.akbank_pdf()))
    raw = _raw(pdf)
    assert glyphs.cid_heavy(raw) and card_reader._cid_text(raw)
    assert "Donem" not in raw and "4,200.00" not in raw


def test_known_glyph_shapes_recover_exact_text_whatever_the_codes(no_ocr):
    lines = _lines(L.akbank_pdf())
    _seed("\n".join(lines))
    a = T.make(lines)
    b = T.make(lines, seed_order="9876543210zyxwvu")  # aynı glifler başka kodlarla (her ekstrede değişir)
    assert _raw(a) != _raw(b)
    for pdf in (a, b):
        text = parsers.extract_text(pdf)
        assert text == "\n".join(lines)
        assert parsers.recovery_of(text) == {"via": "glyph", "ocr_pages": 0, "unknown": 0}


def test_unknown_glyphs_stay_marked_and_normal_text_is_untouched(no_ocr):
    lines = _lines(L.akbank_pdf())
    text = parsers.extract_text(T.make(lines))  # eşleme boş, OCR yok: bilinmeyen işaretli, yerleşim korunur
    assert set(text) <= {glyphs.UNKNOWN, " ", "\n"} and len(text.splitlines()) == len(lines)
    assert parsers.recovery_of(text)["unknown"] > 100
    assert card_reader.explain_from_ai(text, _st())[1] == "text_glyphs"
    assert glyphs.recover(b"%PDF", None, "Normal metin 1.250,00") is None  # cid değil: PDF'e bakılmaz bile


def test_blank_bitmap_glyph_is_a_space():
    mask = b"5 0 d0 q 5 0 0 9 0 0 cm BI /W 5 /H 9 /BPC 1 /IM true /D [1 0]\rID "
    assert glyphs._blank(mask + b"\x00" * 9 + b"\rEI Q")  # [1 0]: 1 boyar, hepsi 0 → boş
    assert not glyphs._blank(mask + b"\x00\x18" + b"\x00" * 7 + b"\rEI Q")
    assert not glyphs._blank(mask.replace(b"[1 0]", b"[0 1]") + b"\x00" * 9 + b"\rEI Q")  # [0 1]: 0 boyar
    assert glyphs._blank(b"250 0 d0\n") and not glyphs._blank(b"500 0 d0\n0 0 10 10 re\nf\n")


def test_akbank_like_paren_credit_layout_learns_from_text():
    lay = L.akbank_pdf()
    spec, st = card_reader.learn(lay.text, "Akbank")
    assert (spec["num"], spec["credit"], spec["date"]) == ("us", "paren", {"sep": "/", "order": "dmy"})
    assert card_reader.is_safe(spec) and st.reconciliation_diff() == 0 and len(st.transactions) == len(lay.rows)
    assert st.previous_balance == 4200.0 and st.min_payment == round(lay.total * 0.4, 2)
    kinds = {t.description: (t.amount, t.txn_type) for t in st.transactions}
    assert kinds["Odemeniz icin tesekkurler"] == (-4200.0, "odeme") and kinds["ORNEK GIYIM"] == (-120.0, "iade")
    assert kinds["Toplam Donem Faizi"] == (37.25, "faiz_ucret")
    inst = next(t for t in st.transactions if "ELEKTRONIK" in t.description)
    assert (inst.amount, inst.installment_no) == (1000.0, 2)


def test_cid_statement_reads_without_ai_once_glyphs_are_known(gemini, no_ocr):  # noqa: F811
    """Glifler biliniyorsa yazı tipi kodlu ekstre ilk yüklemede bile yapay zekâsız: metin denklemi tutuyor, kural
    öğrenilip kaydedilir; sonraki ay (başka kodlar) kayıtlı şablonla."""
    first, nxt = L.akbank_pdf(), L.akbank_pdf(cut="12/11/2026", due="22/11/2026", prev=3283.65, month=10)
    _seed(first.text.translate(FOLD) + nxt.text.translate(FOLD))
    st = parsers.parse_statement(T.make(_lines(first)))
    assert st.source == "sablon" and gemini["calls"] == 0 and st.reconciliation_diff() == 0
    [t] = _cards()
    assert t["spec"]["bank"] == "Akbank" and t["spec"]["credit"] == "paren"
    st2 = parsers.parse_statement(T.make(_lines(nxt), seed_order="zyx987"))
    assert st2.source == "sablon" and gemini["calls"] == 0
    assert len(_cards()) == 1 and (st2.total_debt, st2.period_end.isoformat()) == (nxt.total, "2026-11-12") and st2.reconciliation_diff() == 0


def test_explain_reports_missing_ocr_for_cid_text(no_ocr):
    raw = _raw(T.make(_lines(L.akbank_pdf())))
    assert card_reader.explain_from_ai(raw, _st())[1] == "text_cid_no_ocr"


def test_dry_run_votes_stay_in_memory():
    from collections import Counter

    with glyphs.dry_run():
        glyphs.add_votes(Counter({("abcd", "7"): 3}))
        assert glyphs.labels(["abcd"]) == {"abcd": "7"}
    assert glyphs.labels(["abcd"]) == {}


def test_votes_decide_by_majority_and_cap_per_document():
    from collections import Counter

    glyphs.add_votes(Counter({("s1", "0"): 50, ("s1", "O"): 1}))  # tek belge en fazla VOTE_CAP oy verir
    glyphs.add_votes(Counter({("s1", "O"): 9}))
    assert glyphs.labels(["s1"]) == {}  # 10'a 10: karar yok
    glyphs.add_votes(Counter({("s1", "0"): 6}))  # 16 / 26 ≥ %60
    assert glyphs.labels(["s1"]) == {"s1": "0"}


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract kurulu değil (Docker imajında var)")
def test_local_ocr_teaches_glyphs_once_then_no_ocr(monkeypatch):
    """Eşleme boş: OCR (Tesseract) kelime kutularıyla glifleri öğrenir; tarih / tutar satırları birebir. Sonraki
    ekstre (başka kodlar, başka tutarlar) OCR'sız okunur."""
    first, nxt = L.akbank_pdf(), L.akbank_pdf(cut="12/11/2026", due="22/11/2026", prev=3283.65, month=10)
    text = parsers.extract_text(T.make(_lines(first)))
    info = parsers.recovery_of(text)
    assert info["via"] == "glyph_ocr" and info["ocr_pages"] == 1
    want = _lines(first)
    got = text.splitlines()
    money_lines = [i for i, ln in enumerate(want) if re.search(r"\d", ln) and "**" not in ln]
    assert [got[i] for i in money_lines] == [want[i] for i in money_lines]
    assert info["unknown"] <= 10  # OCR'ın okuyamadığı birkaç glif ("*") işaretli kalır

    def no_tesseract(*a, **k):
        raise AssertionError("ikinci ekstrede OCR gerekmemeli")

    monkeypatch.setattr(subprocess, "run", no_tesseract)
    text2 = parsers.extract_text(T.make(_lines(nxt), seed_order="zyx987"))
    assert parsers.recovery_of(text2)["via"] == "glyph"
    assert card_reader.learn(text2, "Akbank")[1].reconciliation_diff() == 0


def test_backfill_learns_from_stored_cid_statement(hosted, gemini, no_ocr, monkeypatch, capsys):  # noqa: F811
    """Eskiden yapay zekâyla okunmuş yazı tipi kodlu ekstre: geriye dönük öğrenme saklı PDF'in metnini glif eşlemesiyle
    kurtarır ve şablonu öğrenir; çıktıda belge içeriği yok, kurtarma yolu yazılır (text=glyph)."""
    from app import card_reader_backfill

    c, _ = _user(hosted)
    lay = L.akbank_pdf()
    _seed(lay.text.translate(FOLD))
    with monkeypatch.context() as m:  # düzeltme öncesi: metin okunamıyordu, yapay zekâ okudu, şablon öğrenilmedi
        m.setattr(parsers, "parse_text", lambda text: None)
        m.setattr(card_reader, "learn_from_ai", lambda text, st, record=True: None)
        gemini["replies"].append(lay.reply())
        r = c.post("/api/statements/upload", files=[("files", ("ak.pdf", T.make(_lines(lay)), "application/pdf"))])
        assert r.json()[0]["status"] == "saved", r.text
    assert _cards() == [] and gemini["calls"] == 1
    parsers._TEXTS.clear()
    recs = card_reader_backfill.run(dry_run=True)
    assert [(r["bank"], r["reason"], r["via"]) for r in recs] == [("Akbank", "would_learn", "glyph")]
    assert _cards() == []
    card_reader_backfill.run()
    assert [t["spec"]["credit"] for t in _cards()] == ["paren"] and gemini["calls"] == 1
    out = capsys.readouterr().out
    assert "reason=learned" in out and "text=glyph" in out
    for personal in ("ORNEK", "5571", "4410", "4,200", "ayse"):
        assert personal not in out
