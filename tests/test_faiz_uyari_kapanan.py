"""Kapatılmış taksit ve ödenmiş ekstrenin faizi uyarı olmaz (18.11 geri bildirimi: "Taksitlendirme sana ₺X ödetecek" ve
geçen ayın faiz alarmı, taksitler erken kapatılıp ekstre ödendikten sonra da duruyordu).

- ekstreden kapama tanıma: bankaların öbür kalıpları ("TOPLU TAHSİLAT", "TAKSİT ERKEN ÖDEME") ve kalıptan bağımsız, kalan
  taksit toplamına eşit tek satır; kapanan alışverişin L'de "kalan 3" yazılmış vade farkı dilimi yayılmaz
- "Bunu kapattım": vade farkı uyarısında kart listesi; yalnız vade farkı/vergi dilimi kalmış kartta da kapama girilir
- faiz alarmı yalnız açık (ödenmemiş, kartın son) ekstre için; ödenince Faiz sayfasında bilgi kalır, uyarı/bildirim yok

Veriler uydurmadır: örnek banka "Mavi Bank", örnek kart numarası, örnek işyerleri ve tutarlar.
"""
from datetime import date

from app import finance
from app.parsers.base import FEE
from tests.test_early_close import CARD, L_END, _proj, _put, _st, _summary_card, _t
from tests.test_early_close import card  # noqa: F401 (pytest fixture)

TODAY = date(2026, 11, 18)


def _vf(month="2026-11", today=date(2026, 10, 20)):
    return [i for i in finance.insights(month, today=today) if i["key"].startswith("vade_farki:")]


def test_vade_farki_insight_lists_cards_for_bunu_kapattim(client, card):
    vf = _vf()
    assert len(vf) == 1 and vf[0]["cta_closed"] == "Bunu kapattım"
    assert vf[0]["early_close"] == [{"card_no": CARD, "card": "Mavi Bank …0001", "fee_left": 690.0}]
    assert "Bunu kapattım" in vf[0]["detail"]


def test_new_close_pattern_toplu_tahsilat_detected(client, card):
    card.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        _t("TAKSITLI ISLEMLER TOPLU TAHSILAT", 4000.0, d=date(2026, 11, 3)),
        _t("ORNEK TELEFON 6/6", 500.0, no=6, left=0, d=date(2026, 5, 3)),
    ]))
    assert _proj()["installments"] == []
    det = _summary_card(client)["early_close"]["detected"]
    assert det["by_line"] and det["saved"] == 690.0
    assert not _vf(today=TODAY)


def test_lump_line_with_unknown_text_matches_remaining_total(client, card):
    """Kapama satırı bilinmeyen bir kalıpla: kalan alışveriş taksitlerinin toplamı (4 × 1.000) tek satır."""
    card.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        _t("MAVI BANK ISLEM 0001", 4000.0, d=date(2026, 11, 3)),
        _t("ORNEK TELEFON 6/6", 500.0, no=6, left=0, d=date(2026, 5, 3)),
        _t("ORNEK MARKET", 250.0, d=date(2026, 11, 4)),
    ]))
    assert _proj()["installments"] == []
    assert _summary_card(client)["early_close"]["detected"]["by_line"]
    assert not _vf(today=TODAY)


def test_ordinary_big_purchase_is_not_a_lump(client, card):
    """Kalan toplamla tutmayan büyük alışveriş kapama değildir: taksitler sürer."""
    card.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        _t("ORNEK MOBILYA 3/6 TAKSIT", 1000.0, no=3, left=3, per=1000.0),
        _t("VADE FARKI ORNEK MOBILYA 3/6", 150.0, FEE, no=3, left=3, per=150.0),
        _t("ORNEK ELEKTRONIK", 3500.0, d=date(2026, 11, 4)),
    ]))
    assert _summary_card(client)["early_close"]["detected"] is None
    assert any(i["fee"] for i in _proj()["installments"])


def test_closed_purchase_fee_rows_still_listed_do_not_spread(client, card):
    """Banka kapama satırını yazmış ama kapanan alışverişin vade farkı/BSMV dilimini yine "3/6, kalan 3" diye basmış."""
    card.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        _t("TAKSIT ERKEN KAPAMA ORNEK MOBILYA", 4000.0, d=date(2026, 11, 3)),
        _t("VADE FARKI ORNEK MOBILYA 3/6", 150.0, FEE, no=3, left=3, per=150.0),
        _t("BSMV ORNEK MOBILYA 3/6", 22.5, FEE, no=3, left=3, per=22.5),
        _t("ORNEK TELEFON 6/6", 500.0, no=6, left=0, d=date(2026, 5, 3)),
    ]))
    assert _proj()["installments"] == []
    det = _summary_card(client)["early_close"]["detected"]
    assert det["saved"] == 517.5  # 690 − bu ay kesilen 172,50
    assert not _vf(today=TODAY)
    rep = client.get("/api/interest").json()
    assert not any(t["key"] == "erken_kapat" for t in rep["tips"])


def test_other_purchase_keeps_its_fee_after_one_closes(client, tmpdb):
    """İki taksitli alışveriş: biri kapandı (satır), öbürü sürüyor; sürenin vade farkı yayılmaya devam eder."""
    tmpdb.save_statement(b"st-ekim", "ekim.pdf", _st(date(2026, 10, 15), [
        _t("ORNEK MOBILYA 2/6 TAKSIT", 1000.0, no=2, left=4, per=1000.0),
        _t("VADE FARKI ORNEK MOBILYA 2/6", 150.0, FEE, no=2, left=4, per=150.0),
        _t("ORNEK BEYAZ ESYA 2/6 TAKSIT", 800.0, no=2, left=4, per=800.0),
        _t("VADE FARKI ORNEK BEYAZ ESYA 2/6", 90.0, FEE, no=2, left=4, per=90.0),
    ]))
    tmpdb.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        _t("TAKSIT ERKEN KAPAMA ORNEK MOBILYA", 4000.0, d=date(2026, 11, 3)),
        _t("VADE FARKI ORNEK MOBILYA 3/6", 150.0, FEE, no=3, left=3, per=150.0),
        _t("ORNEK BEYAZ ESYA 3/6 TAKSIT", 800.0, no=3, left=3, per=800.0),
        _t("VADE FARKI ORNEK BEYAZ ESYA 3/6", 90.0, FEE, no=3, left=3, per=90.0),
    ]))
    fees = [i for i in _proj()["installments"] if i["fee"]]
    assert {i["description"] for i in fees} == {"VADE FARKI ORNEK BEYAZ ESYA 3/6"} and len(fees) == 3


def test_fee_only_card_can_say_bunu_kapattim(client, tmpdb):
    """Son ekstrede yalnız vade farkı/vergi dilimleri kalmış (alışveriş taksiti yok): kapama girilir, dilimler düşer."""
    tmpdb.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        _t("VADE FARKI ORNEK MOBILYA 3/6", 150.0, FEE, no=3, left=3, per=150.0),
        _t("BSMV ORNEK MOBILYA 3/6", 22.5, FEE, no=3, left=3, per=22.5),
    ]))
    vf = _vf("2026-12", today=TODAY)
    assert vf and vf[0]["early_close"][0]["fee_left"] == 517.5
    ec = _summary_card(client)["early_close"]
    assert ec["available"] and ec["fee_left"] == 517.5 and ec["months"][0]["month"] == "2026-12"
    r = _put(client, "2026-12")
    assert r.status_code == 200, r.text
    rec = r.json()["early_close"]["record"]
    assert rec["saved"] == 517.5 and rec["text"].startswith("Kapattın: Aralık 2026 ekstrenden sonra")
    assert _proj()["installments"] == []
    assert not _vf("2026-12", today=TODAY)
    assert not any(t["key"] == "erken_kapat" for t in client.get("/api/interest").json()["tips"])


# ---------------------------------------------------------------- faiz alarmı: yalnız açık ekstre

def _faizli(tmpdb):
    """Ekim ekstresi gecikmeli ödendi → kasım ekstresinde gecikme faizi (son ödeme 25.11)."""
    with tmpdb.connect() as c:
        for pe, due, debt in (("2026-10-05", "2026-10-25", 8000.0), ("2026-11-05", "2026-11-25", 9000.0)):
            c.execute("INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt) "
                      "VALUES (?, 'x.pdf', 'Mavi Bank', ?, ?, ?, ?)", (f"h{pe}", CARD, pe, due, debt))
        sid = c.execute("SELECT id FROM statements WHERE period_end = '2026-11-05'").fetchone()[0]
        for desc, amt in (("GECIKME FAIZI", 120.0), ("BSMV", 18.0)):
            c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
                      "VALUES (?, '2026-11-05', ?, ?, 'faiz_ucret', 'Banka Faiz & Ücret')", (sid, desc, amt))
        c.execute("INSERT INTO card_payments (pay_date, amount, card_last4) VALUES ('2026-10-28', 8000, '0001')")
    return sid


def _faiz_keys(today=TODAY):
    return [i["key"] for i in finance.insights("2026-11", today=today) if i["key"].startswith("faiz:")]


def test_interest_alarm_only_until_statement_is_paid(client, tmpdb):
    from app import push

    sid = _faizli(tmpdb)
    assert _faiz_keys() and any(i["id"].startswith("faiz:") for i in push.statement_items(TODAY))
    assert client.get("/api/summary", params={"month": "2026-11"}).json()["interest_latest"]["paid"] is False
    with tmpdb.connect() as c:
        c.execute("UPDATE statements SET paid_at = '2026-11-17', paid_amount = 9000 WHERE id = ?", (sid,))
    assert _faiz_keys() == []
    assert not any(i["id"].startswith("faiz:") for i in push.statement_items(TODAY))
    # geçmiş bilgi: Faiz sayfasında ve faiz geçmişinde kalır
    rep = finance.interest_report(12, today=TODAY)
    assert rep["total"] == 138.0 and rep["statements"][0]["reason_kind"] == "gec"
    assert finance.interest_history()["months"][0]["alarms"]
    lf = client.get("/api/summary", params={"month": "2026-11"}).json()["interest_latest"]
    assert lf["state"] == "var" and lf["paid"] is True and lf["statements"][0]["paid"] is True


def test_interest_alarm_gone_when_newer_statement_rolled_over(client, tmpdb):
    _faizli(tmpdb)
    with tmpdb.connect() as c:  # aralık ekstresi kesildi: kasımın faizi geçmiş (borç yeni ekstreye devretti)
        c.execute("INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt) "
                  "VALUES ('h12', 'x.pdf', 'Mavi Bank', ?, '2026-12-05', '2026-12-25', 9500)", (CARD,))
    assert _faiz_keys(date(2026, 12, 8)) == []


def test_partial_payment_keeps_alarm(client, tmpdb):
    sid = _faizli(tmpdb)
    with tmpdb.connect() as c:
        c.execute("UPDATE statements SET paid_at = '2026-11-17', paid_amount = 3000 WHERE id = ?", (sid,))
    assert _faiz_keys()


def test_capability(client):
    caps = client.get("/api/meta").json()["capabilities"]
    assert "interest_settled" in caps and "finance_audit_own_context" in caps


