"""Yapay zekâ okumasından kart şablonu öğrenmenin sessizce düştüğü durumlar (app/card_reader.py explain_from_ai) ve
gözlem (template_learn_attempts, /api/admin/card-templates), statements.source, saklı ekstrelerden geriye dönük öğrenme
(app/card_reader_backfill.py). Düzenler tests/card_layouts.py: banka adları gerçek, düzen taklit, değerler uydurma."""
import json

import pytest

from app import card_reader, parsers, templates
from tests import card_layouts as L
from tests.test_card_templates import _cards, gemini, pdf  # noqa: F401 - fixture
from tests.test_plans import _user, hosted  # noqa: F401 - fixture


def _ai(lay: L.Layout, **kw):
    """Yanıtı parse_pdf_with_ai'den geçir (maskeleme, işaretler canlıdaki gibi)."""
    from app import receipt
    from app.parsers import ai

    reply = lay.reply(**kw)
    orig = receipt._gemini_json
    receipt._gemini_json = lambda *a, **k: (reply, {})
    try:
        return ai.parse_pdf_with_ai(b"x")
    finally:
        receipt._gemini_json = orig


def _why(lay: L.Layout, **kw) -> str:
    return card_reader.explain_from_ai(lay.text, _ai(lay, **kw))[1]


def _upload_pair(gemini, pdf, first: L.Layout, second: L.Layout):  # noqa: F811
    """İlki yapay zekâyla okunur ve şablon öğretir; ikincisi yapay zekâsız okunmalı."""
    pdf["a"], pdf["b"] = first.text, second.text
    gemini["replies"].append(first.reply())
    st = parsers.parse_statement(b"STMT:a")
    assert st.source == "yapay_zeka" and gemini["calls"] == 1
    st2 = parsers.parse_statement(b"STMT:b")
    assert st2.source == "sablon" and gemini["calls"] == 1, "ikinci ekstre şablonla okunmalıydı"
    return st2


# ---------------------------------------------------------------- bankaya özgü düzenler: öğren, sonraki ay yapay zekâsız

def test_akbank_like_layout_learns_and_next_month_reads_without_ai(tmpdb, gemini, pdf):  # noqa: F811
    nxt = L.akbank(cut="12.11.2026", due="22.11.2026", prev=3100.00, month=10)
    st = _upload_pair(gemini, pdf, L.akbank(), nxt)
    [t] = _cards()
    spec = t["spec"]
    assert spec["bank"] == "Akbank" and spec["cards"] == "section" and spec["stop"] == ["gelecek donem taksitleriniz"]
    assert (st.total_debt, st.period_end.isoformat(), st.due_date.isoformat()) == (nxt.total, "2026-11-12", "2026-11-22")
    assert st.min_payment == round(nxt.total * 0.4, 2) and st.credit_limit == 30000.0
    assert len(st.transactions) == len(nxt.rows) and st.reconciliation_diff() == 0
    ek = [x for x in st.transactions if x.card == "5571***8812"]
    assert sorted(x.description for x in ek) == ["ORNEK KAFE", "ORNEK KITAPCI"]  # ek kart bölümü kendi kartında
    inst = next(x for x in st.transactions if "ELEKTRONIK" in x.description)
    assert (inst.amount, inst.installment_no, inst.installments_left) == (1000.0, 2, 4)  # taksit, işlem toplamı değil
    fx = next(x for x in st.transactions if "DIJITAL" in x.description)
    assert (fx.amount, fx.orig_amount, fx.orig_currency) == (857.50, 25.00, "USD")


def test_isbank_like_layout_learns_and_next_month_reads_without_ai(tmpdb, gemini, pdf):  # noqa: F811
    st = _upload_pair(gemini, pdf, L.isbank(), L.isbank(cut="05.11.2026", due="15.11.2026", prev=1420.90))
    assert _cards()[0]["spec"]["credit"] == "trailplus"
    assert st.previous_balance == 1420.90 and st.payments == 1420.90 and st.total_debt == L.isbank(prev=1420.90).total
    assert {x.description: x.txn_type for x in st.transactions}["ORNEK SINEMA IADE"] == "iade"


def test_albaraka_like_layout_learns_and_next_month_reads_without_ai(tmpdb, gemini, pdf):  # noqa: F811
    st = _upload_pair(gemini, pdf, L.albaraka(), L.albaraka(cut="08/11/2026", due="18/11/2026"))
    assert _cards()[0]["spec"]["prev0"] is True and st.previous_balance == 0
    assert {x.description: x.txn_type for x in st.transactions}["KAR PAYI"] == "faiz_ucret"  # katılım bankası faizi


_HALK_ACCOUNT = {"v": 1, "kind": "account", "bank": "Halkbank", "keywords": ["hesap hareket"],
                 "date": {"sep": "-", "order": "dmy"}, "amount_idx": 0, "balance_idx": 1}


def test_halkbank_like_wrapped_descriptions_learn_card_template_beside_account_template(tmpdb, gemini, pdf):  # noqa: F811
    """Halkbank kart ekstresi: açıklaması tarih satırının üstüne / altına taşan satırlar okunmuyordu ("lines"). Aynı
    adlı bankanın hesap dökümü şablonu (topluluktan, kind "account") kart okumasına karışmaz; ayrı kart şablonu öğrenilir."""
    assert templates.save(_HALK_ACCOUNT, source="community")
    nxt = L.halkbank(cut="01/12/2026", due="11/12/2026", prev=3100.00, month=11)
    st = _upload_pair(gemini, pdf, L.halkbank(), nxt)
    kinds = sorted(t["spec"].get("kind", "account") for t in templates.all_templates() if t["bank"] == "Halkbank")
    assert kinds == ["account", "card"]
    spec = _cards()[0]["spec"]
    assert spec["wrap"] is True and spec["cards"] == "section" and spec["num"] == "us" and spec["credit"] == "plus"
    assert (st.total_debt, st.previous_balance, st.card_no) == (nxt.total, 3100.00, "5571***4410")
    assert len(st.transactions) == len(nxt.rows) and st.reconciliation_diff() == 0
    by = {x.description: x for x in st.transactions}
    assert "ORNEK MAGAZACILIK ANONIM SIRKETI" in by and by["ORNEK MAGAZACILIK ANONIM SIRKETI"].amount == 1234.50
    assert by["OTOMATIK ODEME - TESEKKUR EDERIZ"].txn_type == "odeme" and by["ORNEK GIYIM IADE"].txn_type == "iade"
    assert not any("DENEME" in x.description for x in st.transactions)  # kart no satırı (ad soyad) açıklamaya girmez
    elek = next(x for x in st.transactions if "ELEKTRONIK" in x.description)
    assert (elek.amount, elek.installment_no) == (400.00, 9)  # üst / alt parça ("3600.00/9-", "09.Taksit") eklenir
    assert by["ORNEK KAFE"].installment_no is None  # komşu taksit satırının parçası almaz
    ek = sorted(x.description for x in st.transactions if x.card == "5571***8812")
    assert ek == ["900.00/3- ORNEK SPOR 02.Taksit", "ORNEK KITAPCILIK YAYINCILIK"]


def test_garanti_like_bonus_column_learns_and_next_month_reads_without_ai(tmpdb, gemini, pdf):  # noqa: F811
    """Garanti (Bonus) kart ekstresi: yapay zekâ ödeme satırını yalnız özete yazmış ve harcanan Bonus satırını iade
    saymış; denklem özet alanlarıyla tesadüfen "tutuyordu", şablon yapay zekâyı aynen veremediği için "lines" kalıyordu.
    Belgenin kendi denklemini tutan şablon okumasıyla düzeltilip öğrenilir; sonraki ay yapay zekâsız okunur."""
    nxt = L.garanti(cut="27.11.2026", due="07.12.2026", prev=5481.89, month=11)
    st = _upload_pair(gemini, pdf, L.garanti(), nxt)
    spec = _cards()[0]["spec"]
    assert spec["bank"] == "Garanti" and spec["minus_pts"] is True and spec["credit"] == "trailplus"
    assert (st.total_debt, st.previous_balance, st.payments) == (nxt.total, 5481.89, 5481.89)
    assert len(st.transactions) == len(nxt.rows) and st.reconciliation_diff() == 0
    by = {x.description: x for x in st.transactions}
    assert not any("BONUS" in d for d in by)  # harcanan Bonus satırı işlem değil
    assert [x.txn_type for x in st.transactions].count("odeme") == 1 and st.payments == 5481.89

    def one(start):
        [x] = [x for d, x in by.items() if d.startswith(start)]
        return x
    assert one("ORNEK 12-34-AB ONLINE").amount == 734.60  # kazanılan Bonus (önceki sütun) tutar değil
    assert (one("ORNEK ELEKTRONIK").amount, one("ORNEK ELEKTRONIK").installment_no) == (1000.00, 3)
    assert (one("ORNEK DIJITAL").orig_amount, one("ORNEK GIYIM").card, one("ORNEK MARKET").card) \
        == (25.00, "5571***8812", "5571***4410")


def test_spent_bonus_line_ai_counted_as_refund_is_fixed_not_copied():
    lay = L.garanti()
    ai = _ai(lay)
    assert card_reader.ai_check(ai) is None and card_reader._summary_only(ai)  # özet "faiz" farkı tesadüf
    built, why = card_reader.explain_from_ai(lay.text, ai)
    assert why == "ok_fixed" and card_reader.is_safe(built[0])
    st = built[1]
    assert [x.txn_type for x in st.transactions].count("odeme") == 1 and not any(x.amount == -50 for x in st.transactions)
    # puan sütunu kuralı olmadan harcanan Bonus satırı tutar sayılır: denklem tutmaz
    plain = {k: v for k, v in built[0].items() if k != "minus_pts"}
    assert card_reader.apply(lay.text, plain, "Garanti")[1] is False
    # yapay zekâ doğru okuduysa aynı kural doğrudan öğrenilir
    ok = L.garanti(ai_bonus_refund=False)
    built, why = card_reader.explain_from_ai(ok.text, _ai(ok))
    assert why == "ok" and built[0]["minus_pts"] is True
    # sonu "-" olmayan düzende kural öğrenilmez
    assert "minus_pts" not in card_reader.explain_from_ai(L.isbank().text, _ai(L.isbank()))[0][0]


def test_ai_skipped_purchase_is_not_fixed_by_summary_coincidence():
    """Yapay zekâ bir harcamayı atlamış, özet farkı onu "faiz" gibi açıklıyor: şablon fazladan HARCAMA okuyor —
    düzeltme yalnız özetteki ödeme / faiz kalemleri için; öğrenilmez."""
    lay = L.garanti(ai_bonus_refund=False)
    reply = lay.reply()
    tx = [x for x in reply["transactions"] if x["description"] != "ORNEK KAFE"]
    ai = _ai(lay, transactions=tx)
    assert card_reader._summary_only(ai)
    assert card_reader.explain_from_ai(lay.text, ai)[0] is None


def test_equation_line_with_currency_and_fee_term():
    h = card_reader.header("10,00 TL + 25,50 TL + 1,50 TL - 10,00 TL = 27,00 TL 9,00 TL", "tr")
    assert h["equation"] == [10.0, 27.0, 10.0, 27.0]  # faiz/ücret dönem içine katılır
    assert "equation" not in card_reader.header("10,00 TL + 25,50 TL + 1,50 TL - 10,00 TL = 99,00 TL", "tr")
    assert card_reader.header("1,000.00 + 250.00 - 1,000.00 = 250.00", "us")["equation"] == [1000.0, 250.0, 1000.0, 250.0]


def test_amount_repeated_in_two_columns_does_not_decide_the_column():
    """"450,00 TL 450,00" satırında tutar iki sütunda: oy bölünür, sırayı "0,07 734,60" (Bonus önde) belirler."""
    lay = L.garanti(ai_bonus_refund=False)
    idx, at, _ = card_reader._amount_at(lay.text, {"sep": "ay", "order": "dmy"}, "tr", True, _ai(lay))
    assert idx == -1 and "2" not in at


def test_wrapped_description_is_learned_only_when_needed():
    lay = L.halkbank()
    built, why = card_reader.explain_from_ai(lay.text, _ai(lay))
    assert why == "ok" and built[0]["wrap"] is True and card_reader.is_safe(built[0])
    no_wrap = {k: v for k, v in built[0].items() if k != "wrap"}
    res = card_reader.apply(lay.text, no_wrap, "Halkbank")
    assert len(res[0].transactions) < len(lay.rows)  # eski davranış: açıklamasız tarih satırı atlanır → "lines"
    # açıklaması tarih satırında olan düzende wrap öğrenilmez; açılsa da sütunlu başlık değer satırı işlem sayılmaz
    akb = L.akbank(ek=False, future=False)
    built, why = card_reader.explain_from_ai(akb.text, _ai(akb))
    assert why == "ok" and "wrap" not in built[0]
    res = card_reader.apply(akb.text, {**built[0], "wrap": True}, "Akbank")
    assert res[1] and len(res[0].transactions) == len(akb.rows)


def test_ai_date_of_installment_line_differs_from_document():
    """Yapay zekâ taksit satırına belgedeki ilk alışveriş tarihi yerine kesimden sonraki bir günü yazmış: tutar, kart,
    satır sayısı birebir; şablon belgeyi okur (ok_fixed). Taksit olmayan satırda tarih farkı yine reddedilir."""
    lay = L.halkbank()
    reply = lay.reply()
    tx = [dict(x) for x in reply["transactions"]]
    i = next(k for k, x in enumerate(tx) if x["description"] == "ORNEK ELEKTRONIK")
    tx[i]["date"] = "2026-11-02"
    built, why = card_reader.explain_from_ai(lay.text, _ai(lay, transactions=tx))
    assert why == "ok_fixed"
    assert next(x for x in built[1].transactions if "ELEKTRONIK" in x.description).txn_date.isoformat() == "2026-02-14"
    tx = [dict(x) for x in reply["transactions"]]
    j = next(k for k, x in enumerate(tx) if x["description"] == "ORNEK KAFE")
    tx[j]["date"] = "2026-10-22"
    assert card_reader.explain_from_ai(lay.text, _ai(lay, transactions=tx))[1] == "amounts"


def test_masked_card_number_with_hash_split_over_two_lines():
    h = card_reader.header("Kart No :5571-23##-\n####-4410/ DENEME KİŞİ", "us")
    assert h["card_no"] == "5571***4410"
    assert card_reader._card_at(["Kart No :5571-23##-", "####-8812/ DENEME"], 0).groups() == ("5571", "8812")
    assert card_reader._card_at(["####-8812/ DENEME"], 0) is None


# ---------------------------------------------------------------- her düşme nedeni ayrı ayrı

def test_columnar_header_value_row_is_not_a_transaction():
    """Sütunlu başlığın değer satırı tarihle başlıyor ("12.10.2026 22.10.2026 7.512,35 TL …"): işlem sayılırdı."""
    lay = L.akbank(ek=False, future=False)
    assert lay.text.splitlines()[6].startswith("12.10.2026 22.10.2026")
    assert _why(lay) == "ok"


def test_supplementary_card_sections_are_learned_not_refused():
    """Ek kartlı ekstre eskiden hiç öğrenilmiyordu; bölüm başlığındaki kart no ile ayrılır."""
    assert _why(L.akbank(future=False)) == "ok"


def test_wrong_supplementary_card_assignment_is_refused():
    lay = L.akbank(future=False)
    for r in lay.rows:
        if r.card:
            r.card = None  # yapay zekâ ek kart işlemlerini ana karta yazdı: metindeki bölümle çelişir
    lay.rows[0].card = "5571***9999"
    assert _why(lay) == "card"


def test_future_installment_table_is_cut_by_learned_section_heading():
    lay = L.akbank(ek=False)
    assert "Gelecek Dönem Taksitleriniz" in lay.text
    built, why = card_reader.explain_from_ai(lay.text, _ai(lay))
    assert why == "ok" and built[0]["stop"] == ["gelecek donem taksitleriniz"]


def test_installment_line_with_same_value_count_uses_its_own_column():
    """Taksit satırı (toplam, taksit, puan) ile dövizsiz puanlı satır (tutar, puan) — değer sayısı çakışırsa ayrı kural."""
    lay = L.akbank(ek=False, future=False)
    lay.rows.append(L.Row("29.09.2026", "ORNEK SPOR", 300.00, points=3.00))
    lay.text = lay.text.replace("Ana Kart Toplamı", "29.09.2026 ORNEK SPOR 300,00 3,00 0,00\nAna Kart Toplamı")
    lay.text = lay.text.replace(L.m(lay.total - 300), L.m(lay.total)).replace(
        L.m(round((lay.total - 300) * 0.4, 2)), L.m(round(lay.total * 0.4, 2)))
    lay.available = False
    built, why = card_reader.explain_from_ai(lay.text, _ai(lay, available_limit=None))
    assert why == "ok" and built[0]["inst_at"] == {"3": 1}  # taksit satırında 2. değer; taksitsiz 3 değerlide 1.


def test_previous_label_before_total_does_not_shadow_it():
    """"Önceki Hesap Özeti Borcu" dönem borcundan önce: ortak etiket ve öğrenilen çapa önceki borcu okuyordu."""
    lay = L.isbank()
    h = card_reader.header(lay.text, "tr")
    assert (h["total"], h["prev"]) == (lay.total, 2150.00)
    assert _why(lay) == "ok"


def test_learned_anchor_is_not_part_of_a_longer_label():
    lines = ["Sonraki Son Ödeme Tarihi 15.11.2026", "Son Ödeme Tarihi 15.10.2026",
             "Önceki Dönem Borcu 1.000,00", "Dönem Borcu 2.000,00"]
    assert card_reader._anchor_value(lines, {"label": "son odeme tarihi", "off": 0, "nth": 0}, "date", "tr").month == 10
    assert card_reader._anchor_value(lines, {"label": "donem borcu", "off": 0, "nth": 0}, "money", "tr") == 2000.00


def test_trailing_plus_credit_sign():
    assert _why(L.isbank()) == "ok"
    lay = L.isbank()
    lay.text = lay.text.replace(" +", "")  # işaret yoksa ödeme yine "ödeme" kelimesinden (words) — iade de kelimeden
    assert _why(lay) == "ok"


def test_missing_previous_balance_line_with_zero_previous():
    lay = L.albaraka()
    assert card_reader.header(lay.text, "tr")["prev"] is None
    built, why = card_reader.explain_from_ai(lay.text, _ai(lay))
    assert why == "ok" and built[0]["prev0"] is True
    # önceki borç sıfır değilse ama metinde yoksa şablon tutturamaz: öğrenilmez (yanlış şablon yok)
    lay.text = lay.text.replace(f"Ekstre Borcu {L.m(lay.total)}", f"Ekstre Borcu {L.m(lay.total + 100)}")
    assert card_reader.explain_from_ai(lay.text, _ai(lay, previous_balance=100.0, total_debt=lay.total + 100))[1] \
        == "equation"


def test_ai_summary_fields_wrong_but_transactions_consistent_still_learns():
    """Model özet alanlarını (dönem içi, ödemeler) faiz hariç / boş yazabiliyor; işlemler denklemi tutuyorsa öğrenilir."""
    lay = L.isbank()
    assert _why(lay, period_activity=0.0, payments=0.0) == "ok"


def test_old_installment_line_without_ai_installment_number_is_plausible():
    """Yapay zekâ "3/6" taksit satırına taksit numarası yazmadı: 62 günden eski satır "dönem dışı" sayılıyordu."""
    lay = L.isbank()
    lay.text = lay.text.replace("ORNEK MOBILYA 3. TAKSIT", "ORNEK MOBILYA 3/6")
    lay.rows[2].desc, lay.rows[2].inst = "ORNEK MOBILYA 3/6", None
    assert _why(lay) == "ok"


def test_ai_null_optional_field_is_skipped_not_fatal():
    lay = L.akbank(ek=False, future=False)
    built, why = card_reader.explain_from_ai(lay.text, _ai(lay, credit_limit=None, available_limit=None))
    assert why == "ok" and set(built[0]["skip"]) == {"limit", "available"}
    st, ok = card_reader.apply(lay.text, built[0], "Akbank")
    assert ok and st.credit_limit is None and st.available_limit is None  # doğrulanamayan alan boş kalır


def test_untrustworthy_ai_reasons():
    lay = L.isbank()
    reply = lay.reply()
    # eksik iade satırı: farkı özet açıklamaz (son satır KKDF düşseydi "faiz yalnız özette" sayılır: test_card_ai_fix)
    assert reply["transactions"].pop(3)["type"] == "iade"
    from app import receipt
    from app.parsers import ai

    orig = receipt._gemini_json
    receipt._gemini_json = lambda *a, **k: (reply, {})
    try:
        st = ai.parse_pdf_with_ai(b"x")
    finally:
        receipt._gemini_json = orig
    assert card_reader.explain_from_ai(lay.text, st)[1] == "ai_equation"
    assert _why(lay, due_date="2026-10-01") == "ai_dates"
    assert card_reader.explain_from_ai("", _ai(lay))[1] == "no_text"


def test_amount_the_text_does_not_contain_is_refused():
    """Yapay zekâ iki tutarı yanlış okudu ama toplam aynı kaldı: denklem tutar, şablon metinden farklı okur → öğrenilmez."""
    lay = L.isbank()
    lay.rows[1].amount += 10  # ORNEK MARKET
    lay.rows[4].amount -= 10  # ORNEK RESTORAN
    assert _why(lay) == "amounts"


def test_bank_name_only_in_logo_or_conflicting():
    lay = L.isbank()
    lay.text = lay.text.replace("TÜRKİYE İŞ BANKASI A.Ş.", "").replace("Maximum Kart", "Kart")
    assert _why(lay) == "bank_not_in_text"  # sonraki ekstre bankayla eşleşemezdi: kaydetmek boşuna
    assert _why(L.isbank(), bank="Akbank") == "bank_mismatch"


def test_learned_specs_are_safe_and_carry_no_document_values():
    for lay in (L.akbank(), L.isbank(), L.albaraka(), L.halkbank()):
        built, why = card_reader.explain_from_ai(lay.text, _ai(lay))
        assert why == "ok" and card_reader.is_safe(built[0]) and templates.is_safe(built[0])
        raw = json.dumps(built[0], ensure_ascii=False)
        for personal in ("DENEME", "5571", "4410", "8812", "4506", "2290", "6631", "ORNEK", "2026", "30.000", "Chip"):
            assert personal not in raw


def test_is_safe_rejects_bad_new_fields():
    ok = {"v": 1, "kind": "card", "bank": "Akbank", "keywords": [], "date": {"sep": ".", "order": "dmy"},
          "num": "tr", "amount_idx": 0, "credit": "trailplus", "inst_at": {"3": 1}, "skip": ["min"], "prev0": True,
          "cards": "section", "stop": ["gelecek donem taksitleriniz"], "wrap": True, "minus_pts": True}
    assert card_reader.is_safe(ok)
    for bad in ({**ok, "stop": ["ayse yilmaz"]}, {**ok, "stop": ["gelecek  donem"]}, {**ok, "stop": "gelecek"},
                {**ok, "skip": ["total"]}, {**ok, "skip": []}, {**ok, "prev0": 1}, {**ok, "cards": "4410"},
                {**ok, "inst_at": {"3": 9}}, {**ok, "credit": "trailx"}, {**ok, "amount_idx": True},
                {**ok, "wrap": 1}, {**ok, "wrap": "evet"}, {**ok, "minus_pts": 1}, {**ok, "minus_pts": "-"}):
        assert not card_reader.is_safe(bad), bad


# ---------------------------------------------------------------- gözlem, kaynak, önbellek

def _attempts() -> dict:
    return {a["bank"]: a for a in templates.card_overview()["attempts"]}


def test_attempts_are_recorded_per_bank_with_reason(tmpdb, gemini, pdf):  # noqa: F811
    lay = L.isbank()
    pdf["ok"], pdf["bad"] = lay.text, lay.text.replace("TÜRKİYE İŞ BANKASI A.Ş.", "").replace("Maximum Kart", "Kart")
    gemini["replies"] += [lay.reply(), L.isbank().reply(bank="Akbank T.A.Ş.")]
    parsers.parse_statement(b"STMT:ok")
    parsers.parse_statement(b"STMT:bad")
    a = _attempts()
    assert a["İş Bankası"]["ok"] == 1 and a["İş Bankası"]["reasons"] == {"learned": 1}
    assert a["Akbank"]["failed"] == 1 and a["Akbank"]["reasons"] == {"bank_not_in_text": 1}
    with __import__("app.system", fromlist=["x"]).connect() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(template_learn_attempts)")]
    assert cols == ["day", "bank", "ok", "reason", "n"]  # içerik yok: yalnız gün, banka, sonuç, kod, sayı


def test_admin_endpoint_shows_attempts_and_card_templates(client, gemini, pdf):  # noqa: F811
    pdf["a"] = L.albaraka().text
    gemini["replies"].append(L.albaraka().reply())
    parsers.parse_statement(b"STMT:a")
    r = client.get("/api/admin/card-templates").json()
    assert r["attempts"][0]["bank"] == "Albaraka" and r["attempts"][0]["reasons"] == {"learned": 1}
    assert [t["bank"] for t in r["templates"]] == ["Albaraka"] and r["templates"][0]["disabled"] is False
    assert "DENEME" not in json.dumps(r, ensure_ascii=False)


def test_admin_endpoint_requires_admin(hosted):  # noqa: F811
    c, _ = _user(hosted)
    assert c.get("/api/admin/card-templates").status_code == 403
    assert hosted.get("/api/admin/card-templates").status_code == 200


def test_statement_source_is_stored(client, gemini, pdf):  # noqa: F811
    from app import db

    pdf["a"], pdf["b"] = L.isbank().text, L.isbank(cut="05.11.2026", due="15.11.2026", prev=1420.90).text
    gemini["replies"].append(L.isbank().reply())
    for name in ("a", "b"):
        r = client.post("/api/statements/upload", files=[("files", (f"{name}.pdf", f"STMT:{name}".encode(),
                                                                    "application/pdf"))]).json()[0]
        assert r["status"] == "saved", r
    with db.connect() as c:
        assert [x[0] for x in c.execute("SELECT source FROM statements ORDER BY period_end")] == ["yapay_zeka", "sablon"]


def test_cached_ai_result_still_teaches(tmpdb, gemini, pdf, monkeypatch):  # noqa: F811
    """Önbellekten dönen yapay zekâ okuması (aynı PDF yeniden) da öğretir: eski sürümde öğrenilemeyen ekstre için."""
    from app import doc_cache

    monkeypatch.setattr(doc_cache, "_where", lambda: 1)  # hazır sürümde kullanıcının defteri (önbellek defter başına)
    pdf["a"] = L.isbank().text
    gemini["replies"].append(L.isbank().reply())
    with monkeypatch.context() as m:
        m.setattr(card_reader, "learn_from_ai", lambda text, st, record=True: None)  # eski sürüm: öğrenmedi
        with doc_cache.reading(b"STMT:a", doc_cache.EKSTRE):
            parsers.parse_statement(b"STMT:a")
    assert _cards() == []
    with doc_cache.reading(b"STMT:a", doc_cache.EKSTRE) as slot:
        st = parsers.parse_statement(b"STMT:a")
    assert slot.hit and st.ai_parsed and st.source == "yapay_zeka" and gemini["calls"] == 1 and len(_cards()) == 1


# ---------------------------------------------------------------- saklı ekstrelerden geriye dönük öğrenme

def _upload(c, name: str):
    r = c.post("/api/statements/upload", files=[("files", (f"{name}.pdf", f"STMT:{name}".encode(), "application/pdf"))])
    assert r.json()[0]["status"] == "saved", r.text
    return r.json()[0]


def test_backfill_learns_from_stored_statements_without_ai(hosted, gemini, pdf, monkeypatch, capsys):  # noqa: F811
    """Düzeltmeden önce yapay zekâyla okunmuş (şablon öğretmemiş) ekstreler: saklı PDF + kayıtlı sonuçtan öğrenilir.
    Yapay zekâya gidilmez, defter değişmez, çıktıda belge içeriği yok, ikinci çalıştırma bir şey değiştirmez."""
    import sqlite3

    from app import card_reader_backfill, system

    c, u = _user(hosted)
    lays = {"ak": L.akbank(), "is": L.isbank(), "al": L.albaraka()}
    with monkeypatch.context() as m:
        m.setattr(card_reader, "learn_from_ai", lambda text, st, record=True: None)  # düzeltme öncesi: öğrenmedi
        for k, lay in lays.items():
            pdf[k] = lay.text
            gemini["replies"].append(lay.reply())
            _upload(c, k)
    assert _cards() == [] and gemini["calls"] == 3
    path = system.user_db_path(u)
    with sqlite3.connect(path) as cx:  # eski kayıt: kaynak yazılmamıştı
        cx.execute("UPDATE statements SET source = NULL WHERE filename = 'is.pdf'")
    before = open(path, "rb").read()

    recs = card_reader_backfill.run(dry_run=True)
    assert sorted((r["bank"], r["reason"]) for r in recs) == [("Akbank", "would_learn"), ("Albaraka", "would_learn"),
                                                              ("İş Bankası", "would_learn")]
    assert _cards() == []  # deneme kipi: kayıt yok
    recs = card_reader_backfill.run()
    assert sorted(r["reason"] for r in recs) == ["learned"] * 3 and all(r["user"] == u["id"] and r["ok"] for r in recs)
    assert sorted(t["bank"] for t in _cards()) == ["Akbank", "Albaraka", "İş Bankası"]
    assert {r["reason"] for r in card_reader_backfill.run()} == {"already_local"}  # tekrar: değişiklik yok
    assert gemini["calls"] == 3 and open(path, "rb").read() == before  # yapay zekâ yok, defter aynı
    out = capsys.readouterr().out
    assert "reason=learned" in out
    for personal in ("DENEME", "ORNEK", "5571", "4410", "2290", "6631", "4.200", "2.150", "ayse"):
        assert personal not in out
    # bir sonraki ay: şablonla, yapay zekâsız (şablonlar sunucu geneli; ücretsiz paketin günlük 3 okuması doldu)
    pdf["ak2"] = L.akbank(cut="12.11.2026", due="22.11.2026", prev=3100.00, month=10).text
    assert parsers.parse_statement(b"STMT:ak2").source == "sablon" and gemini["calls"] == 3


def test_backfill_reports_reasons_for_unlearnable_and_missing_file(hosted, gemini, pdf, monkeypatch):  # noqa: F811
    from app import card_reader_backfill, storage

    c, _ = _user(hosted)
    lay = L.isbank()
    lay.text = lay.text.replace("TÜRKİYE İŞ BANKASI A.Ş.", "").replace("Maximum Kart", "Kart")  # banka adı logoda
    pdf["x"] = lay.text
    gemini["replies"].append(lay.reply())
    _upload(c, "x")
    assert [r["reason"] for r in card_reader_backfill.run(dry_run=True)] == ["bank_not_in_text"]
    monkeypatch.setattr(storage, "book_get", lambda kind, name: None)
    assert [(r["bank"], r["reason"], r["ok"]) for r in card_reader_backfill.run(dry_run=True)] == \
        [("İş Bankası", "no_file", False)]
    assert card_reader_backfill.main(["--dry-run", "--user", "yok@ornek.com"]) == 0
