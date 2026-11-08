"""Fiş denetiminde karışan rakam önerisi (check.digit_fix, yetenek digit_fix): termal baskıda 6↔8 gibi benzer rakamlar
yanlış okununca kalemler fiş toplamını tutmaz; tek kalemin tek basamağı eşiyle değişince fark tam kapanıyorsa önerilir.
Telefon metni (ocr_text) kalemin satırında düzeltilmiş tutarı gösteriyorsa satır tanıklı olur (evidence ["ocr"]); KDV grupları ("group") ve
kullanıcının kendi defterindeki fiyat geçmişi ("history") öneriyi daraltır.
Bütün kalemler, tutarlar ve işyeri uydurmadır."""
import json

import httpx
import pytest

from app import money, receipt_check
from tests.test_doc_reads import _jpeg, hosted  # noqa: F401 - fixture
from tests.test_plans import _user


def _it(name, amount, qty=1, unit_price=None):
    return {"name": name, "qty": qty, "unit": "adet", "amount": amount, "unit_price": unit_price}


def _bare(fixes):
    """Kanıtsız önerinin özü (evidence [] ve score 0 atılır; kanıtlı alanlar kalır)."""
    return [{k: v for k, v in f.items() if not (k == "evidence" and v == []) and not (k == "score" and v == 0)}
            for f in fixes]


# ~20 kalemlik market fişi; 13. kalem fişte 89,00, okumada 69,00 (onlar basamağında 6 olan tek kalem bu)
MARKET = [_it("EKMEK TAM BUGDAY", 17.50), _it("SUT 1 LT", 42.90), _it("YUMURTA 15LI", 119.90), _it("PEYNIR BEYAZ", 249.00),
          _it("ZEYTIN SIYAH", 189.75), _it("DOMATES KG", 47.35), _it("SALATALIK KG", 39.90), _it("MUZ KG", 94.20),
          _it("ELMA KG", 54.40), _it("MAKARNA 500G", 22.75), _it("PIRINC 1KG", 79.95), _it("ZEYTINYAGI 1L", 429.00),
          _it("MEYVE PURESI", 69.00), _it("DETERJAN 4KG", 349.90), _it("KAGIT HAVLU 8LI", 199.00),
          _it("CAY 1KG", 289.50), _it("SEKER 1KG", 44.90), _it("UN 2KG", 72.45), _it("SU 5L", 29.90), _it("TAVUK BUT KG", 746.17)]


def test_market_total_matches_fixture():
    assert money.money(sum(i["amount"] for i in MARKET)) == 3187.42


def test_real_case_69_read_for_89():
    chk = receipt_check.check(MARKET, 3207.42)
    assert not chk["ok"] and chk["diff"] == 20.0
    assert _bare(chk["digit_fix"]) == [{"index": 12, "name": "MEYVE PURESI", "from": 69.0, "to": 89.0, "digits": "6→8"}]
    fixed = [dict(i) for i in MARKET]
    fixed[12]["amount"] = 89.0
    after = receipt_check.check(fixed, 3207.42)
    assert after["ok"] and after["digit_fix"] == []


def test_reverse_direction_8_read_for_6():
    items = [_it("A URUN", 18.40), _it("B URUN", 89.00), _it("C URUN", 12.25)]  # gerçek 69,00: kalemler ₺20 fazla
    assert _bare(receipt_check.check(items, 99.65)["digit_fix"]) == \
        [{"index": 1, "name": "B URUN", "from": 89.0, "to": 69.0, "digits": "8→6"}]


def test_ok_receipt_and_unknown_total_have_no_fix():
    assert _bare(receipt_check.check(MARKET, 3187.42)["digit_fix"]) == []
    assert _bare(receipt_check.check(MARKET, None)["digit_fix"]) == []
    assert receipt_check.digit_fixes(MARKET, 3187.42) == []
    assert _bare(receipt_check.check(MARKET, 3187.43)["digit_fix"]) == []  # 1 kuruş: tolerans içinde, öneri aranmaz


def test_no_candidate():
    # ₺4 fark: hiçbir karışan eşin farkı 4 değil (6↔8 2, 3↔8 5, 5↔6 1, 1↔7 6, 0↔8 8, 9↔8 1, 5↔8 3)
    assert _bare(receipt_check.check(MARKET, 3191.42)["digit_fix"]) == []


def test_kurus_digit():
    items = [_it("SAKIZ", 12.35), _it("GAZETE", 20.00), _it("SU 0,5L", 10.00)]  # fişte 12,85: kuruşta 3→8
    assert _bare(receipt_check.check(items, 42.85)["digit_fix"]) == \
        [{"index": 0, "name": "SAKIZ", "from": 12.35, "to": 12.85, "digits": "3→8"}]


def test_k1_two_solutions_no_fix_but_suspects():
    # ₺10 eksik: 5→6 (52) ya da 8→9 (81), ikisi de tek satır: hangisi belli değil → öneri yok, iki satır şüpheli
    items = [_it("KALEM 1", 81.00), _it("KALEM 2", 52.00), _it("KALEM 3", 20.00)]
    chk = receipt_check.check(items, 163.0)
    assert chk["digit_fix"] == [] and chk["digit_fix_all"] is None and chk["residual"] == 10.0
    assert chk["suspect"] == [0, 1]


def test_k2_unique_solution():
    # ₺20,50 eksik: tek satır kapatamıyor; iki satır tek yoldan: 66,00→86,00 (+20) ve 10,30→10,80 (kuruşta 3→8, +0,50)
    items = [_it("A", 66.00), _it("B", 10.30), _it("C", 2.24)]
    chk = receipt_check.check(items, 99.04)
    assert [(f["index"], f["from"], f["to"], f["digits"]) for f in chk["digit_fix"]] == [(0, 66.0, 86.0, "6→8"), (1, 10.3, 10.8, "3→8")]
    assert chk["digit_fix_all"] == {"indices": [0, 1], "total_ok": True} and chk["residual"] == 0 and chk["suspect"] == []
    fixed = [dict(i) for i in items]
    for f in chk["digit_fix"]:
        fixed[f["index"]]["amount"] = f["to"]
    assert receipt_check.check(fixed, 99.04)["ok"]


def test_k2_ambiguous_no_fix_suspects():
    items = [_it("A", 66.00), _it("B", 64.00), _it("C", 10.30), _it("D", 2.24)]  # ₺20,50: A ya da B, artı C
    chk = receipt_check.check(items, 163.04)
    assert chk["digit_fix"] == [] and chk["residual"] == 20.5 and chk["suspect"][0] == 2 and set(chk["suspect"]) == {0, 1, 2}


def test_ambiguous_more_candidates():
    items = [_it("KALEM 1", 51.00), _it("KALEM 2", 52.00), _it("KALEM 3", 53.00), _it("KALEM 4", 54.00)]
    chk = receipt_check.check(items, 220.0)  # dördünde de onlarda 5→6: hangisi belli değil
    assert chk["digit_fix"] == [] and sorted(chk["suspect"]) == [0, 1, 2, 3]


def test_unit_price_digit_with_qty():
    # 2 × 36,50 = 73,00 okundu; fişte 2 × 38,50 = 77,00. Tutarın kendi basamağında ₺4'lük eş yok, birim fiyatta var
    items = [_it("DETERJAN", 73.00, 2, 36.50), _it("SABUN", 25.00), _it("SAMPUAN", 120.00)]
    assert _bare(receipt_check.check(items, 222.0)["digit_fix"]) == [
        {"index": 0, "name": "DETERJAN", "from": 73.0, "to": 77.0, "digits": "6→8",
         "unit_price_from": 36.5, "unit_price_to": 38.5}]


def test_qty_one_with_unit_price_changes_both():
    items = [_it("MEYVE PURESI", 69.00, 1, 69.00), _it("SU", 10.00, 1, 10.00)]
    assert _bare(receipt_check.check(items, 99.0)["digit_fix"]) == [
        {"index": 0, "name": "MEYVE PURESI", "from": 69.0, "to": 89.0, "digits": "6→8",
         "unit_price_from": 69.0, "unit_price_to": 89.0}]


def test_qty_amount_digit_keeps_unit_price_consistent():
    # 3 × 23,00 = 69,00; fişte tutar 89,00 olamaz (3 × birim) ama tutar basamağı da aday: birim tutar ÷ adet olur
    items = [_it("YOGURT", 69.00, 3, 23.00), _it("EKMEK", 10.00)]
    got = receipt_check.check(items, 99.0)["digit_fix"]
    assert _bare(got) == [{"index": 0, "name": "YOGURT", "from": 69.0, "to": 89.0, "digits": "6→8",
                    "unit_price_from": 23.0, "unit_price_to": 29.67}]


def test_mismatched_unit_price_only_amount():
    # adet × birim tutmuyor (2 × 36,50 = 73 ≠ 69; 69 ile 73 karışma değil): birim fiyat değiştirilmez, yalnız tutar
    items = [_it("KAHVE", 69.00, 2, 36.50), _it("SU", 10.00)]
    got = receipt_check.check(items, 99.0)["digit_fix"]
    assert _bare(got) == [{"index": 0, "name": "KAHVE", "from": 69.0, "to": 89.0, "digits": "6→8"}]


def test_leading_digit_never_becomes_zero_and_discounts_skipped():
    items = [_it("A", 8.00), _it("INDIRIM", -6.00), _it("B", 1.00)]
    assert receipt_check.digit_fixes(items, -5.0) == []  # 8→0 rakam düşürür; eksi satıra dokunulmaz
    assert receipt_check.digit_fixes([_it("A", 80.00)], 0.0) == []


def test_direct_search_is_exact_for_every_position():
    # fark farktan doğrudan: her öneri kalem toplamını fiş toplamına kuruşu kuruşuna eşitler
    for total in (3207.42, 3167.42, 3187.92, 3192.42, 3237.42, 3127.42):
        for f in receipt_check.check(MARKET, total)["digit_fix"]:
            fixed = [dict(i) for i in MARKET]
            fixed[f["index"]]["amount"] = f["to"]
            assert money.kurus(sum(i["amount"] for i in fixed)) == money.kurus(total)


# ---------------------------------------------------------------- telefon metni ve adet × birim tanıklığı

TWO = [_it("SUT 1L", 64.50), _it("MEYVE PURESI", 69.00), _it("EKMEK", 10.00)]  # ₺20 eksik: ikisinde de onlarda 6


def test_without_text_ambiguous():
    chk = receipt_check.digit_check(TWO, 163.5)
    assert chk["digit_fix"] == [] and chk["suspect"] == [0, 1]


def test_ocr_text_witnesses_line():
    text = "ORNEK MARKET\nSUT 1L  *64,50\nMEYVE PUR3SI  *89,00\nEKMEK  *10,00\nTOPLAM *163,50"
    chk = receipt_check.digit_check(TWO, 163.5, text)
    assert _bare(chk["digit_fix"]) == [{"index": 1, "name": "MEYVE PURESI", "from": 69.0, "to": 89.0, "digits": "6→8",
                                 "evidence": ["ocr"], "score": 4}]
    assert chk["residual"] == 0 and chk["digit_fix_all"] is None and chk["suspect"] == []


def test_ocr_text_price_on_next_line_and_dot_decimal():
    text = "MEYVE PURESI\n89.00\nSUT 1L\n64,50"  # ad satırında tutar yok: alt satırdaki
    got = receipt_check.digit_fixes(TWO, 163.5, text)
    assert [(f["index"], f.get("evidence")) for f in got] == [(1, ["ocr"])]


def test_ocr_text_confirms_original_amount_excludes_line():
    text = "SUT 1L  *64,50\nEKMEK  *10,00"  # SUT'un yanında okunan 64,50 yazıyor: o satır doğru, geriye tek aday
    got = receipt_check.digit_fixes(TWO, 163.5, text)
    assert [(f["index"], f["to"]) for f in got] == [(1, 89.0)] and got[0]["evidence"] == []


def test_ocr_four_lines_close_the_gap():
    # dört satır yanlış (30 + 20 + 30 + 20 = ₺100): toplam tek başına belirsiz, metin her satıra tanık
    items = [_it("ELMA KG", 53.00), _it("ARMUT KG", 61.00), _it("KIRAZ KG", 153.00), _it("ERIK KG", 69.00),
             _it("SU 5L", 29.90)]
    text = "ELMA KG *83,00\nARMUT KG *81,00\nKIRAZ KG *183,00\nERIK KG *89,00\nSU 5L *29,90\nTOPLAM *465,90"
    chk = receipt_check.digit_check(items, 465.90, text)
    assert [(f["index"], f["to"], f["digits"], f.get("evidence")) for f in chk["digit_fix"]] == [
        (0, 83.0, "5→8", ["ocr"]), (1, 81.0, "6→8", ["ocr"]), (2, 183.0, "5→8", ["ocr"]), (3, 89.0, "6→8", ["ocr"])]
    assert chk["digit_fix_all"] == {"indices": [0, 1, 2, 3], "total_ok": True} and chk["residual"] == 0
    assert receipt_check.digit_check(items, 465.90)["digit_fix"] == []  # metinsiz: 4 satır aranmaz


ALL_AGREE = "SUT 1L *64,50\nMEYVE PURESI *69,00\nEKMEK *10,00"  # telefon metni de her satırı okunanla aynı okudu


def test_ocr_agreeing_line_still_searched_when_nothing_else_closes():
    # silik 8'i metin de 6 okudu (MEYVE 69,00); SUT'ta onlar basamağı 6 değil: tek çözüm MEYVE, güveni düşük
    items = [_it("SUT 1L", 44.50), _it("MEYVE PURESI", 69.00), _it("EKMEK", 10.00)]
    text = "SUT 1L *44,50\nMEYVE PURESI *69,00\nEKMEK *10,00"
    chk = receipt_check.digit_check(items, 143.5, text)
    assert [(f["index"], f["to"], f["digits"], f["evidence"], f["score"], f["ocr_agrees"]) for f in chk["digit_fix"]] == [
        (1, 89.0, "6→8", [], -1, True)]
    assert chk["residual"] == 0 and chk["suspect"] == []
    assert "ocr_agrees" not in receipt_check.digit_check(items, 143.5)["digit_fix"][0]  # metinsiz: olağan öneri


def test_ocr_agreeing_lines_ambiguous_no_fix_but_suspects():
    chk = receipt_check.digit_check(TWO, 163.5, ALL_AGREE)  # iki satırda da onlarda 6: hangisi belli değil
    assert chk["digit_fix"] == [] and chk["residual"] == 20.0 and chk["suspect"] == [0, 1]


def test_ocr_agreeing_lines_history_breaks_tie():
    chk = receipt_check.digit_check(TWO, 163.5, ALL_AGREE, history={1: [89.0]})
    assert [(f["index"], f["evidence"], f["score"], f.get("ocr_agrees")) for f in chk["digit_fix"]] == [
        (1, ["history"], 1, True)]


def test_ocr_agreeing_lines_not_used_when_unverified_line_closes_the_gap():
    text = "SUT 1L *64,50\nEKMEK *10,00"  # MEYVE metinde yok (tanıksız): çözüm orada, SUT'a bakılmaz
    got = receipt_check.digit_fixes(TWO, 163.5, text)
    assert [(f["index"], f.get("ocr_agrees")) for f in got] == [(1, None)]


def test_ocr_agreeing_lines_need_exact_single_swap():
    assert receipt_check.digit_check(TWO, 147.5, ALL_AGREE)["digit_fix"] == []  # ₺4: hiçbir eşin farkı değil
    # ₺40: iki satır birden (64,50→84,50 ve 69→89) gerekir; metnin doğruladığı satırlarda yalnız tek satır aranır
    assert receipt_check.digit_check(TWO, 183.5, ALL_AGREE)["digit_fix"] == []
    ok = receipt_check.check(TWO, 143.5)  # fiş tutuyor: metin ne derse desin öneri yok
    receipt_check.add_evidence(ok, TWO, ALL_AGREE)
    assert ok["ok"] and ok["digit_fix"] == [] and ok["suspect"] == []


def test_unknown_count_two_witnessed_third_residual():
    # üç satır yanlış; metin ikisine tanık (ELMA +30, ARMUT +20); kalan ₺10 iki satırda olabilir (5→6 ya da 8→9)
    items = [_it("ELMA KG", 53.00), _it("ARMUT KG", 61.00), _it("PATATES", 52.00), _it("SOGAN", 81.00)]
    chk = receipt_check.digit_check(items, 307.0, "ELMA KG *83,00\nARMUT KG *81,00")
    assert [(f["index"], f["to"]) for f in chk["digit_fix"]] == [(0, 83.0), (1, 81.0)]
    assert chk["residual"] == 10.0 and chk["digit_fix_all"] == {"indices": [0, 1], "total_ok": False}
    assert sorted(chk["suspect"]) == [2, 3]
    # kalan fark tek yoldan kapanıyorsa (yalnız PATATES): o da eklenir, fiş tutar
    items[3] = _it("SOGAN", 24.00)
    chk = receipt_check.digit_check(items, 250.0, "ELMA KG *83,00\nARMUT KG *81,00")
    assert [(f["index"], f.get("evidence")) for f in chk["digit_fix"]] == [(0, ["ocr"]), (1, ["ocr"]), (2, [])]
    assert chk["digit_fix_all"]["total_ok"] and chk["residual"] == 0


def test_qty_witness():
    # 2 × 34,50 = 69,00 olmalı, tutar 89,00 okundu (8↔6): adet × birim tanık; fiş toplamı da tutuyor
    items = [_it("KAHVE", 89.00, 2, 34.50), _it("SU", 10.00)]
    chk = receipt_check.check(items, 79.0)
    assert _bare(chk["digit_fix"]) == [{"index": 0, "name": "KAHVE", "from": 89.0, "to": 69.0, "digits": "8→6", "evidence": ["qty"], "score": 4}]
    assert chk["residual"] == 0


def test_add_evidence_in_place():
    chk = receipt_check.check(TWO, 163.5)
    assert chk["digit_fix"] == []
    receipt_check.add_evidence(chk, TWO, "MEYVE PURESI 89,00")
    assert chk["digit_fix"][0]["index"] == 1 and chk["digit_fix"][0]["evidence"] == ["ocr"]
    ok = receipt_check.check(TWO, 143.5)
    receipt_check.add_evidence(ok, TWO, "MEYVE PURESI 89,00")
    assert ok["digit_fix"] == []


# ---------------------------------------------------------------- KDV grupları, ürün sayısı, fiyat geçmişi

VAT = [{"rate": 1, "amount": 0.98}, {"rate": 20, "amount": 10.75}]  # %1: 89 + 10 = 99 → 0,98; %20: 64,50 → 10,75


def test_vat_groups_localize_the_line():
    items = [{**TWO[0], "vat_rate": 20}, {**TWO[1], "vat_rate": 1}, {**TWO[2], "vat_rate": 1}]
    assert receipt_check.digit_check(items, 163.5)["digit_fix"] == []  # KDV dökümü yok: iki aday
    chk = receipt_check.digit_check(items, 163.5, vat_lines=VAT)
    assert [(f["index"], f["to"], f["evidence"], f["score"]) for f in chk["digit_fix"]] == [(1, 89.0, ["group"], 1)]
    assert [(g["rate"], g["items_total"], g["tax"], g["printed"], g["ok"]) for g in chk["groups"]] == [
        (1, 79.0, 0.78, 0.98, False), (20, 64.5, 10.75, 10.75, True)]


def test_vat_rate_from_phone_text_line():
    text = "SUT 1L %20 *64,50\nMEYVE PURESI %1\nEKMEK %1 *10,00"  # MEYVE PURESI'nin tutarı okunamamış, oranı satırda
    chk = receipt_check.digit_check(TWO, 163.5, text, vat_lines=VAT)
    assert [(f["index"], f["evidence"]) for f in chk["digit_fix"]] == [(1, ["group"])]
    assert receipt_check.digit_check(TWO, 163.5, "SUT 1L *64,50", vat_lines=VAT)["groups"] == []  # oranı bilinmeyen kalem


def test_vat_groups_all_bad_do_not_restrict():
    items = [{**TWO[0], "vat_rate": 20}, {**TWO[1], "vat_rate": 1}, {**TWO[2], "vat_rate": 1}]
    chk = receipt_check.digit_check(items, 163.5, vat_lines=[{"rate": 1, "amount": 5.0}, {"rate": 20, "amount": 1.0}])
    assert chk["digit_fix"] == [] and sorted(chk["suspect"]) == [0, 1]


def test_printed_count_says_line_missing():
    items = [_it("A URUN", 69.00), _it("B URUN", 10.00), _it("C URUN", 5.00)]
    assert _bare(receipt_check.digit_check(items, 104.0)["digit_fix"])[0]["to"] == 89.0
    chk = receipt_check.digit_check(items, 104.0, "ORNEK\nTOPLAM 4 ÜRÜN\nTOPLAM *104,00")
    assert chk["digit_fix"] == [] and chk["line_count"] == {"printed": 4, "read": 3} and chk["residual"] == 20.0
    assert receipt_check.digit_check(items, 104.0, "ÜRÜN SAYISI: 3")["line_count"] is None


def test_history_picks_among_equal_candidates():
    chk = receipt_check.digit_check(TWO, 163.5, history={1: [89.0, 87.5]})
    assert [(f["index"], f["evidence"], f["score"], f.get("history_price")) for f in chk["digit_fix"]] == [
        (1, ["history"], 2, 89.0)]
    # okunan tutar geçmişle tutuyor (SUT geçen sefer de 64,50): o satır geride kalır, öteki önerilir
    chk = receipt_check.digit_check(TWO, 163.5, history={0: [64.5]})
    assert [(f["index"], f["evidence"]) for f in chk["digit_fix"]] == [(1, [])]
    assert receipt_check.digit_check(TWO, 163.5, history={0: [64.5], 1: [69.0]})["digit_fix"] == []  # ikisi de -2: belirsiz


def test_history_within_two_percent_and_ocr_plus_history_score():
    chk = receipt_check.digit_check(TWO, 163.5, history={1: [88.0]})  # 89 ile %1,1 fark: tutar
    assert chk["digit_fix"][0]["history_price"] == 88.0
    assert receipt_check.digit_check(TWO, 163.5, history={1: [80.0]})["digit_fix"] == []
    chk = receipt_check.digit_check(TWO, 163.5, "MEYVE PURESI *89,00", history={1: [89.0]})
    assert chk["digit_fix"][0]["evidence"] == ["ocr", "history"] and chk["digit_fix"][0]["score"] == 6


def test_saved_receipt_uses_own_price_history(client, tmpdb):
    from datetime import date, timedelta

    old = (date.today() - timedelta(days=20)).isoformat()
    client.post("/api/entries", json={"entry_date": old, "description": "Ornek Market", "amount": 99.0, "kind": "gider",
                                      "category": "Market & Gıda",
                                      "items": [_it("MEYVE PURESI", 89.0), _it("EKMEK", 10.0)]})
    eid = client.post("/api/entries", json={"entry_date": date.today().isoformat(), "description": "Ornek Market",
                                            "amount": 163.5, "kind": "gider", "category": "Market & Gıda",
                                            "items": TWO}).json()["id"]
    chk = client.get(f"/api/entries/{eid}/receipt-items").json()["check"]
    assert [(f["index"], f["to"], f["evidence"], f["history_price"]) for f in chk["digit_fix"]] == [
        (1, 89.0, ["history"], 89.0)]


def test_recent_unit_prices_same_store_first(tmpdb):
    from datetime import date, timedelta

    today = date.today()
    for d, m, price in ((5, "Ornek Market", 89.0), (10, "Baska Market", 92.0), (200, "Ornek Market", 70.0)):
        e = tmpdb.create_entry({"entry_date": (today - timedelta(days=d)).isoformat(), "description": m, "amount": price,
                                "kind": "gider", "category": "Market & Gıda"})
        tmpdb.save_receipt_items(e, [_it("MEYVE PURESI", price)], m, (today - timedelta(days=d)).isoformat())
    key = tmpdb.product_key("MEYVE PURESI")
    assert tmpdb.recent_unit_prices(["MEYVE PURESI"], "ORNEK MARKET") == {key: [89.0]}  # 200 gün önceki dışarıda
    assert tmpdb.recent_unit_prices(["MEYVE PURESI"], "Yeni Market") == {key: [89.0, 92.0]}


def test_capability():
    from app import version

    assert "digit_fix" in version.handshake()["capabilities"]


# ---------------------------------------------------------------- uçlar

def test_saved_receipt_items_digit_fix_and_put_clears(client, tmpdb):
    eid = client.post("/api/entries", json={"entry_date": "2026-11-06", "description": "Ornek Market", "amount": 3207.42,
                                            "kind": "gider", "category": "Market & Gıda", "items": MARKET}).json()["id"]
    got = client.get(f"/api/entries/{eid}/receipt-items").json()
    assert got["check"]["diff"] == 20.0
    assert [(f["index"], f["from"], f["to"]) for f in got["check"]["digit_fix"]] == [(12, 69.0, 89.0)]
    items = got["items"]
    items[12] = {**items[12], "amount": 89.0, "unit_price": 89.0}
    out = client.put(f"/api/entries/{eid}/receipt-items", json={"items": items}).json()
    assert out["check"]["ok"] and out["check"]["digit_fix"] == []


@pytest.fixture()
def ai(monkeypatch):
    """Sahte Gemini: fiş hep 69,00 okunur (yeniden okuma da aynı)."""
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    fis = {"doc_type": "fis", "merchant": "Ornek Market", "date": "2026-11-06", "total": 163.5,
           "category": "Market & Gıda", "confidence": "orta",
           "items": [{"name": i["name"], "amount": i["amount"]} for i in TWO]}

    def post(url, json=None, timeout=None, headers=None):
        body = {"total": 163.5, "items": fis["items"]} if "yeniden oku" in json["contents"][0]["parts"][0]["text"] else fis
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dump(body)}]}}],
                                         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})

    monkeypatch.setattr(receipt.httpx, "post", post)


def _dump(d):
    return json.dumps(d, ensure_ascii=False)


def test_document_parse_uses_phone_text_as_evidence(hosted, ai):  # noqa: F811
    c, _ = _user(hosted)
    text = "ORNEK MARKET\nSUT 1L *64,50\nMEYVE PURESI *89,00\nEKMEK *10,00\nTOPLAM *163,50"
    r = c.post("/api/documents/parse", files=[("file", ("f.jpg", _jpeg(), "image/jpeg"))],
               data={"ocr_text": text, "ocr_source": "vision"})
    assert r.status_code == 200, r.text
    fixes = r.json()["check"]["digit_fix"]
    assert [(f["index"], f.get("evidence")) for f in fixes] == [(1, ["ocr"])]
    # metinsiz okuma: iki satırda olabilir, öneri yok, şüpheli satırlar
    r = c.post("/api/documents/parse", files=[("file", ("g.jpg", _jpeg(), "image/jpeg"))])
    assert r.json()["check"]["digit_fix"] == [] and r.json()["check"]["suspect"] == [0, 1]
