"""Yapay zekâ okuması ekstre denklemini tutmuyor (09.11.2026 canlı backfill: dört ekstre "ai_equation"). Düzenler
canlıdaki hata biçimlerini UYDURMA veriyle taklit eder (tests/card_layouts.py):

- Metinde tarih açıklamaya / referans numarasına bitişik ("12/09/2026ORNEK"): satır başı tarih deseni tutmuyordu.
- Metinde kelime arası boşluk yok, tarih ay adıyla ("16Eylül2026"), etiketler bitişik ("HesapKesimTarihi").
- Yapay zekâ ödeme satırını yalnız özete yazmış (işlem listesinde yok) / faiz satırlarını yalnız özete yazmış.
- Yapay zekâ "+" işaretli taksitli iadeyi harcama yazmış (fark iadenin iki katı).
- PDF yazı tipinde karakter eşlemesi yok: metin "(cid:N)" kodları.

Şablon her durumda kendi başına denklemi tutmalı ve aynı belgeden yapay zekânın doğru satırlarını aynen vermeli;
yanlış şablon kabul edilmez. Kayıtlı ekstre değişmez; yeni yüklemede kullanıcı düzeltilmiş okumayı görür."""
from dataclasses import replace

from app import card_reader, parsers
from app.parsers.base import FEE, PAYMENT, REFUND
from tests import card_layouts as L
from tests.test_card_learning import _ai
from tests.test_card_templates import _cards, gemini, pdf  # noqa: F401 - fixture


def _without(lay: L.Layout, kind: str) -> dict:
    """Yanıt: o türden satırlar işlem listesinde yok (özet alanları olduğu gibi)."""
    reply = lay.reply()
    return {**reply, "transactions": [t for t in reply["transactions"] if t["type"] != kind]}


# ---------------------------------------------------------------- tarih biçimleri, bitişik etiketler

def test_dates_glued_to_text_and_month_names():
    d = card_reader._date_re({"order": "dmy", "sep": "/"})
    assert card_reader._date(d.match("12/09/2026ORNEK MARKET 312,40")).isoformat() == "2026-09-12"
    assert card_reader._date(d.match("08/09/20261234-567890 ODEME -1,00")).isoformat() == "2026-09-08"
    ay = card_reader._date_re({"order": "dmy", "sep": "ay"})
    for s, iso in (("16Eylül2026 ORNEK", "2026-09-16"), ("3 EKİM 2026 ORNEK", "2026-10-03"),
                   ("01Ağu2026X", "2026-08-01"), ("28 Şubat 2027 Y", "2027-02-28")):
        assert card_reader._date(ay.match(s)).isoformat() == iso
    assert ay.match("16 Ekimden 2026") is None
    h = card_reader.header("HesapKesimTarihi :08Ekim2026\nSonÖdemeTarihi :18Ekim2026\nDönemBorcu(TL) :1.234,56\n"
                           "ÖncekiDönemBorcu 99,00", "tr")
    assert (h["period_end"].isoformat(), h["due"].isoformat(), h["total"], h["prev"]) == \
        ("2026-10-08", "2026-10-18", 1234.56, 99.0)  # "Önceki dönem borcu" dönem borcu sayılmaz (bitişikte de)


def test_glued_layouts_read_without_ai():
    """Boşluksuz / bitişik tarihli metin yapay zekâsız (learn) okunuyor: canlıda bu ekstreler yapay zekâya gitmez."""
    for lay, bank in ((L.isbank_glued(), "İş Bankası"), (L.albaraka_glued(), "Albaraka")):
        spec, st = card_reader.learn(lay.text, card_reader.bank_of(lay.text))
        assert spec["bank"] == bank and st.total_debt == lay.total and card_reader._txn_equation_diff(st) == 0
        assert len(st.transactions) == len(lay.rows)


# ---------------------------------------------------------------- yapay zekâ özete yazdı, listeye koymadı

def test_payment_only_in_summary_is_tolerated_and_template_reads_it():
    lay = L.isbank_glued()
    ai = _ai(lay, transactions=_without(lay, "odeme")["transactions"])
    assert card_reader._txn_equation_diff(ai) == lay.prev and not any(t.txn_type == PAYMENT for t in ai.transactions)
    assert card_reader.ai_check(ai) is None  # özetteki ödeme farkı kuruşu kuruşuna açıklıyor
    (spec, st), why = card_reader.explain_from_ai(lay.text, ai)
    assert why == "ok" and len(st.transactions) == len(ai.transactions) + 1  # şablon ödeme satırını da okur
    assert card_reader._txn_equation_diff(st) == 0
    fixed = card_reader.fixed_read(lay.text, ai, spec)
    assert fixed is not None and card_reader._txn_equation_diff(fixed) == 0
    pay = [t for t in fixed.transactions if t.amount < 0 and t.txn_date.isoformat() == "2026-09-08"]
    assert [round(t.amount, 2) for t in pay] == [-lay.prev]
    assert (fixed.source, fixed.card_no, fixed.min_payment) == (ai.source, ai.card_no, ai.min_payment)  # başlık AI'ın


def test_fees_only_in_summary_is_tolerated():
    """Faiz/BSMV/KKDF satırları yanıtta yok ama dönem içi tutar onları içeriyor: şablon onları faiz olarak okur."""
    lay = L.isbank()
    ai = _ai(lay, transactions=_without(lay, "faiz_ucret")["transactions"])
    assert card_reader.ai_check(ai) is None
    (spec, st), why = card_reader.explain_from_ai(lay.text, ai)
    assert why == "ok" and round(sum(t.amount for t in st.transactions if t.txn_type == FEE), 2) == 58.50


def test_missing_purchase_is_not_tolerated():
    """Eksik satır faiz değil (harcama): özet açığı aynı olsa da şablon kabul edilmez."""
    lay = L.isbank()
    reply = lay.reply()
    ai = _ai(lay, transactions=[t for t in reply["transactions"] if t["description"] != "ORNEK RESTORAN"])
    assert card_reader.ai_check(ai) is None  # açık ≥ 0 ve farkı açıklıyor: öğrenme denenir…
    built, why = card_reader.explain_from_ai(lay.text, ai)
    assert built is None and why == "lines"  # …ama şablonun fazladan satırı faiz değil


def test_extra_ai_line_is_not_tolerated():
    lay = L.isbank()
    reply = lay.reply()
    dup = {**reply["transactions"][1]}
    ai = _ai(lay, transactions=reply["transactions"] + [dup])  # yapay zekâ bir satırı iki kez yazdı
    assert card_reader.ai_check(ai) == "ai_equation"
    assert card_reader.explain_from_ai(lay.text, ai) == (None, "ai_equation")  # metin tek başına okunamıyor


# ---------------------------------------------------------------- yapay zekâ işareti ters okudu: metinden düzeltme

def _wrong_sign_reply(lay: L.Layout) -> dict:
    reply = lay.reply()
    txns = [{**t, "type": "harcama"} if "TAKSITLIIADE" in t["description"] else t for t in reply["transactions"]]
    return {**reply, "transactions": txns}


def test_installment_refund_read_as_purchase_is_fixed_from_text():
    lay = L.albaraka_glued()
    ai = _ai(lay, **_wrong_sign_reply(lay))
    assert card_reader._txn_equation_diff(ai) == round(2 * 137.40, 2) and card_reader.ai_check(ai) == "ai_equation"
    (spec, st), why = card_reader.explain_from_ai(lay.text, ai)
    assert why == "ok_fixed" and spec["bank"] == "Albaraka" and spec["date"]["sep"] == "ay"
    assert spec.get("cards") == "section" and card_reader.is_safe(spec)  # ek kart bölümü de öğrenildi
    assert {t.card for t in st.transactions} == {"5258***6631", "5258***7720"}
    fixed = card_reader.fixed_read(lay.text, ai, spec)
    assert card_reader._txn_equation_diff(fixed) == 0
    inst = next(t for t in fixed.transactions if "TAKSITLIIADE" in t.description)
    assert (inst.amount, inst.txn_type) == (-137.40, REFUND)  # açıklama yapay zekânın, işaret şablonun
    assert [t.description for t in fixed.transactions if t.card == "5258***7720"] == ["ORNEKKAFE", "ORNEKKITAPCIIADE"]


def test_text_fix_rejects_other_document_or_low_overlap():
    lay = L.albaraka_glued()
    reply = _wrong_sign_reply(lay)
    ai = _ai(lay, **{**reply, "total_debt": lay.total + 10})  # yapay zekâ başka dönem borcu okumuş: aynı belge değil
    assert card_reader.explain_from_ai(lay.text, ai) == (None, "ai_equation")
    few = _ai(lay, **{**reply, "transactions": reply["transactions"][:3]})  # satırların çoğu eşleşmiyor
    assert card_reader.explain_from_ai(lay.text, few)[0] is None


def test_cid_text_is_reported():
    lay = L.albaraka_glued()
    ai = _ai(lay, **_wrong_sign_reply(lay))
    garbled = "\n".join("".join(f"(cid:{(i * 7 + j) % 200})" for j in range(30)) for i in range(40))
    assert card_reader.explain_from_ai(garbled, ai) == (None, "text_cid")


# ---------------------------------------------------------------- canlı yükleme

def test_upload_with_ai_payment_missing_learns_and_returns_fixed_read(tmpdb, gemini, pdf):  # noqa: F811
    """Yalnız yönlendirmeli öğrenmeyle okunan düzen (Akbank benzeri): yapay zekâ ödeme satırını listeye koymadı.
    Şablon öğrenilir; kullanıcıya dönen okuma ödeme satırını içerir ve denklemi tutar; sonraki ay yapay zekâsız."""
    lay = L.akbank()
    pdf["a"] = lay.text
    gemini["replies"].append(_without(lay, "odeme"))
    st = parsers.parse_statement(b"STMT:a")
    assert gemini["calls"] == 1 and st.source == "yapay_zeka" and len(_cards()) == 1
    assert len(st.transactions) == len(lay.rows) and card_reader._txn_equation_diff(st) == 0
    assert [round(t.amount, 2) for t in st.transactions if t.txn_type == PAYMENT] == [-lay.prev]
    pdf["b"] = L.akbank(cut="12.11.2026", due="22.11.2026", prev=3100.00, month=10).text
    assert parsers.parse_statement(b"STMT:b").source == "sablon" and gemini["calls"] == 1


def test_upload_of_glued_layouts_needs_no_ai(tmpdb, gemini, pdf):  # noqa: F811
    for k, lay in (("i", L.isbank_glued()), ("a", L.albaraka_glued())):
        pdf[k] = lay.text
        st = parsers.parse_statement(f"STMT:{k}".encode())
        assert st.source == "sablon" and st.total_debt == lay.total
    assert gemini["calls"] == 0


def test_fixed_read_leaves_good_ai_read_alone():
    lay = L.akbank()
    ai = _ai(lay)
    spec, _ = card_reader.explain_from_ai(lay.text, ai)[0]
    assert card_reader.fixed_read(lay.text, ai, spec) is None  # denklem zaten tutuyor: dokunma
    assert card_reader.fixed_read(lay.text, replace(ai, total_debt=ai.total_debt + 5), spec) is None
