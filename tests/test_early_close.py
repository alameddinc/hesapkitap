"""Taksitleri erken kapatma (app/early_close.py, yetenek card_early_close; PUT/DELETE /api/cards/early-close) ve ekstre
okumasında kapama satırları (card_reader.close_installments).

Veriler uydurmadır: örnek banka "Mavi Bank", örnek kart numaraları, örnek işyerleri ve tutarlar.
"""
from datetime import date

import pytest

from app import card_reader, finance, money
from app.parsers.base import FEE, PAYMENT, PURCHASE, Statement, Transaction
from tests.test_book import _member, owner  # noqa: F401 (pytest fixture)

CARD = "5555***0001"
P_END, L_END = date(2026, 10, 15), date(2026, 11, 15)
BUY_DATE = date(2026, 8, 10)


def _t(desc, amount, kind=PURCHASE, no=None, left=None, per=None, d=BUY_DATE):
    return Transaction(txn_date=d, description=desc, amount=amount, txn_type=kind, card=CARD, installment_no=no,
                       installments_left=left, installment_left_amount=per)


def _st(pe: date, txns: list[Transaction], card=CARD) -> Statement:
    nxt = date(pe.year + pe.month // 12, pe.month % 12 + 1, pe.day)
    s = Statement(bank="Mavi Bank", card_no=card, period_end=pe, due_date=date(pe.year, pe.month, 25),
                  total_debt=round(sum(t.amount for t in txns), 2), min_payment=None, credit_limit=50000.0,
                  next_period_end=nxt, next_due_date=date(nxt.year, nxt.month, 25))
    s.transactions = txns
    return s


def _prev(card=CARD) -> Statement:
    """Ekim ekstresi: mobilya 2/6 (kalan 4 × 1.000) + vade farkı ve BSMV taksitleri, telefon 5/6 (kalan 1 × 500)."""
    return _st(P_END, [
        _t("ORNEK MOBILYA 2/6 TAKSIT", 1000.0, no=2, left=4, per=1000.0),
        _t("VADE FARKI ORNEK MOBILYA 2/6", 150.0, FEE, no=2, left=4, per=150.0),
        _t("BSMV ORNEK MOBILYA 2/6", 22.5, FEE, no=2, left=4, per=22.5),
        _t("ORNEK TELEFON 5/6", 500.0, no=5, left=1, per=500.0, d=date(2026, 5, 3)),
        _t("ORNEK MARKET", 300.0, d=date(2026, 10, 2)),
    ], card)


@pytest.fixture()
def card(client, tmpdb):
    tmpdb.save_statement(b"st-ekim", "ekim.pdf", _prev())
    return tmpdb


def _proj(card_no=CARD):
    return finance.card_projection()[card_no]


def _by_month(card_no=CARD):
    out: dict[str, float] = {}
    for i in _proj(card_no)["installments"]:
        out[i["month"]] = money.money(out.get(i["month"], 0) + i["amount"])
    return out


def _summary_card(client, card_no=CARD):
    return next(c for c in client.get("/api/cards/summary").json()["cards"] if c["card_no"] == card_no)


def _put(client, month, amount=None, card_no=CARD):
    return client.put("/api/cards/early-close", json={"card_no": card_no, "month": month, "amount": amount})


# ---------------------------------------------------------------- kazanç tahmini ve kayıt

def test_hint_before_closing(client, card):
    assert _by_month() == {"2026-11": 1672.5, "2026-12": 1172.5, "2027-01": 1172.5, "2027-02": 1172.5}
    ec = _summary_card(client)["early_close"]
    assert ec["available"] and ec["rest"] == 4500.0 and ec["fee_left"] == 690.0
    assert ec["hint"] == ("Taksitleri erken kapatırsan en çok ₺690,00 vade farkı ve vergi ödemezsin; kesin tutarı "
                          "bankandan öğren.")
    assert ec["default_month"] == "2026-11" and [m["month"] for m in ec["months"]] == ["2026-11", "2026-12", "2027-01"]
    assert ec["months"][0]["label"] == "Kasım 2026 ekstresi"
    assert ec["record"] is None and ec["detected"] is None
    # Özet uyarısı ve Faiz sayfası önerisi tutarı söyler
    ins = finance.insights("2026-11", today=date(2026, 10, 20))
    vf = next(i for i in ins if i["key"].startswith("vade_farki:"))
    assert "erken kapatırsan en çok ₺690,00 vade farkı ve vergi ödemezsin" in vf["detail"]
    rep = client.get("/api/interest").json()
    tip = next(t for t in rep["tips"] if t["key"] == "erken_kapat")
    assert "₺690,00" in tip["detail"] and "bankandan öğren" in tip["detail"]
    assert [c["card_no"] for c in rep["early_close"]] == [CARD] and rep["early_close"][0]["fee_left"] == 690.0


def test_record_next_month_single_line_everywhere(client, card):
    r = _put(client, "2026-11")
    assert r.status_code == 200, r.text
    ec = r.json()["early_close"]
    assert ec["record"]["month"] == "2026-11" and ec["record"]["total"] == 4500.0 and ec["record"]["saved"] == 690.0
    assert ec["record"]["saved_text"] == "Erken kapama: ₺690,00 vade farkından kurtuldun."
    assert ec["record"]["text"] == "Kalan taksitler Kasım 2026 ekstrende tek seferde: ₺4.500,00."
    assert ec["hint"] is None and ec["fee_left"] == 0.0
    # projeksiyon: tek satır, vade farkı/vergi yok
    inst = _proj()["installments"]
    assert len(inst) == 1 and inst[0]["early_close"] and inst[0]["description"] == "Taksit erken kapama"
    assert _by_month() == {"2026-11": 4500.0}
    # Özet: bu ay ödenecek taksitler (beklenen) ve kalan taksit borcu
    oct_ = client.get("/api/installments", params={"month": "2026-10"}).json()
    assert money.eq(oct_["remaining"], 4500.0) and oct_["remaining_count"] == 1
    nov = client.get("/api/installments", params={"month": "2026-11"}).json()
    item = next(i for i in nov["items"] if i.get("early_close"))
    assert (item["title"], item["amount"], item["card_no"], item["fee"], item["in_spend"]) == (
        "Taksit erken kapama", 4500.0, CARD, False, False)
    assert item["row"]["uid"].startswith("c") and item["count"] == 2  # eski istemci dokununca kapanan alışverişi açar
    assert nov["expected"] == 4500.0 and nov["remaining"] == 0 and not any(i["fee"] for i in nov["items"])
    # Planlama: kasımda tek taksit satırı, sonraki aylarda taksit yok
    plan = finance.plan(6, today=date(2026, 11, 1))
    lines = {m["month"]: [ln for ln in m["lines"] if ln["type"] == "taksit"] for m in plan["months"]}
    assert [ln["amount"] for ln in lines["2026-11"]] == [4500.0] and lines["2026-11"][0]["early_close"]
    assert "kalan taksitler tek seferde" in lines["2026-11"][0]["description"]
    assert all(not lines[m] for m in ("2026-12", "2027-01", "2027-02"))
    # Kartlar: kalan taksitler ve açık dönem kalemi
    k = _summary_card(client)
    assert k["installments_left"]["total"] == 4500.0 and k["installments_left"]["next_month"] == 4500.0
    # Özet uyarısı: vade farkı uyarısı ve "taksit bitiyor" yok
    ins = finance.insights("2026-11", today=date(2026, 10, 20))
    assert not any(i["key"].startswith(("vade_farki:", "taksit_bitiyor:")) for i in ins)
    # Faiz sayfası: kartın satırı kayıtla, öneri yok
    rep = client.get("/api/interest").json()
    assert rep["early_close"][0]["record"]["saved"] == 690.0
    assert not any(t["key"] == "erken_kapat" for t in rep["tips"])


def test_bank_amount_and_later_month_and_edit_undo(client, card):
    # bankanın söylediği tutar: kazanç = kalan toplam (vade farkı dahil 5.190) − tutar
    ec = _put(client, "2026-11", 4400).json()["early_close"]
    assert ec["record"]["amount"] == 4400.0 and ec["record"]["total"] == 4400.0 and ec["record"]["saved"] == 790.0
    assert _by_month() == {"2026-11": 4400.0}
    # tutar kalan toplamdan büyükse kazanç eksiye düşmez
    assert _put(client, "2026-11", 6000).json()["early_close"]["record"]["saved"] == 0.0
    assert _put(client, "2026-11", 6000).json()["early_close"]["record"]["saved_text"] is None
    # düzenle: aralıkta kapat — kasım taksitleri olduğu gibi, aralıkta kalan alışveriş taksitleri tek satır
    ec = _put(client, "2026-12").json()["early_close"]
    assert ec["record"]["total"] == 3000.0 and ec["record"]["saved"] == 517.5 and ec["record"]["amount"] is None
    assert _by_month() == {"2026-11": 1672.5, "2026-12": 3000.0}
    # borç planı: kalan taksit metni tek seferi söyler
    from app import debt_plan

    cards, _ = debt_plan._cards(date(2026, 10, 20))
    fut = next(c for c in cards if c["card_no"] == CARD)["future_installments"]
    assert fut["early_close"] and fut["last_month"] == "2026-12" and fut["total"] == 4672.5
    assert "erken kapattın" in fut["text"] and "Aralık 2026" in fut["text"]
    # geçersiz ay ve kalan taksiti olmayan ay
    assert _put(client, "2026-10").status_code == 422
    assert _put(client, "2027-03").status_code == 422
    assert _put(client, "2026-11", card_no="9999***0000").status_code == 404
    # geri al: yeniden aylara yayılır
    r = client.delete("/api/cards/early-close", params={"card_no": CARD})
    assert r.status_code == 200 and r.json()["early_close"]["record"] is None
    assert _by_month() == {"2026-11": 1672.5, "2026-12": 1172.5, "2027-01": 1172.5, "2027-02": 1172.5}
    assert client.delete("/api/cards/early-close", params={"card_no": CARD}).status_code == 404


def test_card_without_installments(client, tmpdb):
    tmpdb.save_statement(b"st-duz", "d.pdf", _st(P_END, [_t("ORNEK MARKET", 300.0, d=date(2026, 10, 2)),
                                                         _t("ORNEK KAFE", 120.0, d=date(2026, 10, 5))]))
    assert _summary_card(client)["early_close"] is None
    assert _put(client, "2026-11").status_code == 422
    assert client.get("/api/interest").json()["early_close"] == []


def test_capability(client):
    assert "card_early_close" in client.get("/api/meta").json()["capabilities"]


# ---------------------------------------------------------------- yeni ekstre: kayıt devre dışı, ekstreden tanıma

def test_new_statement_with_close_line_detected_and_verifies_record(client, card):
    _put(client, "2026-11")
    card.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        # kapama satırı: taksit bilgisi taşısa da tek çekim okunur
        _t("TAKSIT ERKEN KAPAMA ORNEK MOBILYA", 4000.0, no=3, left=3, per=4000.0, d=date(2026, 11, 3)),
        _t("ORNEK TELEFON 6/6", 500.0, no=6, left=0, d=date(2026, 5, 3)),
        _t("ODEME", -2000.0, PAYMENT, d=date(2026, 10, 24)),
    ]))
    p = _proj()
    assert p["latest_period"] == "2026-11" and p["installments"] == []  # yeniden yayılmaz, kayıt devre dışı
    ec = _summary_card(client)["early_close"]
    assert not ec["available"] and ec["record"] is None
    assert ec["detected"]["saved"] == 690.0 and ec["detected"]["verified"] and ec["detected"]["by_line"]
    assert ec["detected"]["text"] == ("Mavi Bank kartında taksitler erken kapandı: ₺690,00 vade farkından kurtuldun. "
                                      "Ekstreyle doğrulandı.")
    rep = client.get("/api/interest").json()
    assert rep["early_close"][0]["detected"]["saved"] == 690.0
    # kasımın harcaması: kapama satırı bir kez (gerçek satır), beklenen yok
    nov = client.get("/api/installments", params={"month": "2026-11"}).json()
    assert nov["expected"] == 0 and nov["remaining"] == 0


def test_new_statement_lists_remaining_one_by_one(client, card):
    card.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        *[_t(f"ORNEK MOBILYA {n}/6 TAKSIT", 1000.0, no=n, left=6 - n, per=1000.0) for n in (3, 4, 5, 6)],
        _t("ORNEK TELEFON 6/6", 500.0, no=6, left=0, d=date(2026, 5, 3)),
    ]))
    assert _proj()["installments"] == []
    det = _summary_card(client)["early_close"]["detected"]
    assert det["saved"] == 690.0 and not det["verified"] and not det["by_line"]
    assert det["text"] == "Mavi Bank kartında taksitler erken kapandı: ₺690,00 vade farkından kurtuldun."
    # satırlar sırası görünmeye devam eder ("4/6"), harcamaya bir kez girer
    rows = [r for r in client.get("/api/transactions", params={"month": "2026-11"}).json() if "MOBILYA" in r["description"]]
    assert sorted(r["installment"] for r in rows) == ["3/6", "4/6", "5/6", "6/6"]


def test_new_statement_single_row_with_remaining_total(client, card):
    # banka kalanların toplamını "3/6" satırına yazmış: kalan taksit gibi görünse de yeniden yayılmaz
    card.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        _t("ORNEK MOBILYA 3/6 TAKSIT", 4000.0, no=3, left=3, per=4000.0),
        _t("ORNEK TELEFON 6/6", 500.0, no=6, left=0, d=date(2026, 5, 3)),
    ]))
    assert _proj()["installments"] == []
    assert _summary_card(client)["early_close"]["detected"]["saved"] == 690.0


def test_normal_next_statement_not_detected(client, card):
    _put(client, "2026-11")
    card.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        _t("ORNEK MOBILYA 3/6 TAKSIT", 1000.0, no=3, left=3, per=1000.0),
        _t("VADE FARKI ORNEK MOBILYA 3/6", 150.0, FEE, no=3, left=3, per=150.0),
        _t("ORNEK TELEFON 6/6", 500.0, no=6, left=0, d=date(2026, 5, 3)),
    ]))
    ec = _summary_card(client)["early_close"]
    assert ec["detected"] is None and ec["record"] is None  # kayıt eski ekstreye bağlı: devre dışı
    assert _by_month() == {"2026-12": 1150.0, "2027-01": 1150.0, "2027-02": 1150.0}
    assert ec["fee_left"] == 450.0 and ec["months"][0]["month"] == "2026-12"


def test_push_once_for_detected(client, card, monkeypatch):
    from app import push

    card.save_statement(b"st-kasim", "kasim.pdf", _st(L_END, [
        _t("TAKSIT KAPAMA ORNEK MOBILYA", 4000.0, d=date(2026, 11, 3)),
        _t("ORNEK TELEFON 6/6", 500.0, no=6, left=0, d=date(2026, 5, 3)),
    ]))
    items = [i for i in push.statement_items(date(2026, 11, 16)) if i["id"].startswith("erken_kapama:")]
    assert len(items) == 1 and items[0]["kind"] == "faiz" and items[0]["route"] == push.R_FAIZ
    assert items[0]["id"] == "erken_kapama:0001:2026-11-15"
    assert items[0]["title"] == "Mavi Bank …0001: taksitler erken kapandı" and "₺690,00" in items[0]["body"]


# ---------------------------------------------------------------- ortak defter

def test_shared_book_member_rules(owner):
    from tests.conftest import fixture_bytes

    files = [("files", (n.replace(".txt", ".pdf"), fixture_bytes(n), "application/pdf"))
             for n in ("vakifbank_a.txt", "kuveytturk.txt")]
    assert all(r["status"] == "saved" for r in owner.post("/api/statements/upload", files=files).json())
    kuveyt = next(c for c in owner.get("/api/cards/summary").json()["cards"] if c["bank"] == "Kuveyt Türk")
    ec = kuveyt["early_close"]
    assert ec["available"] and money.gt(ec["fee_left"], 0) and ec["hint"]
    m = _member(owner)  # görür ve ekler: kapama kaydı sahibin (ve tam yetkilinin)
    assert m.put("/api/cards/early-close", json={"card_no": kuveyt["card_no"], "month": ec["default_month"]}).status_code == 403
    full = _member(owner, "tam", role="full")
    r = full.put("/api/cards/early-close", json={"card_no": kuveyt["card_no"], "month": ec["default_month"]})
    assert r.status_code == 200 and r.json()["early_close"]["record"]
    # faiz gizli: tutar ve kazanç yok; kart gizli: kayıt da yok
    owner.put("/api/book/rules", json={"hide_fees": True})
    seen = next(c for c in m.get("/api/cards/summary").json()["cards"] if c["card_no"] == kuveyt["card_no"])["early_close"]
    assert seen["record"] and seen["record"]["saved"] is None and seen["record"]["saved_text"] is None
    assert seen["fee_left"] is None and seen["hint"] is None
    owner.put("/api/book/rules", json={"hidden_cards": [kuveyt["card_no"]], "hide_fees": False})
    assert all(c["card_no"] != kuveyt["card_no"] for c in m.get("/api/cards/summary").json()["cards"])
    assert full.delete("/api/cards/early-close", params={"card_no": kuveyt["card_no"]}).status_code == 404
    # sahip görür ve geri alabilir
    assert owner.delete("/api/cards/early-close", params={"card_no": kuveyt["card_no"]}).status_code == 200


# ---------------------------------------------------------------- ekstre okuma savunması (card_reader)

def test_reader_close_line_is_single_charge():
    st = _st(L_END, [
        _t("ERKEN KAPAMA ORNEK MOBILYA", 4000.0, no=3, left=3, per=4000.0),
        _t("Kalan Taksitlerin Tahsilatı ORNEK SAAT", 900.0, no=2, left=2, per=450.0),
        _t("ORNEK BEYAZ ESYA TAKSİT KAPATMA", 1200.0, no=1, left=5, per=240.0),
        _t("KALAN TAKSIT TUTARI ORNEK BISIKLET", 800.0, no=4, left=2, per=400.0),
        # sayı bildiren satır kapama değil; ödeme satırı dokunulmaz
        _t("ORNEK KITAP 2/4 KALAN TAKSIT SAYISI 2", 100.0, no=2, left=2, per=100.0),
        _t("ERKEN ODEME", -500.0, PAYMENT),
    ])
    assert card_reader.close_installments(st) == 4
    got = [(t.installment_no, t.installments_left, t.installment_left_amount) for t in st.transactions]
    assert got[:4] == [(None, None, None)] * 4
    assert got[4] == (2, 2, 100.0)


def test_reader_remaining_listed_one_by_one():
    st = _st(L_END, [
        *[_t(f"ORNEK MOBILYA {n}/6", 1000.0, no=n, left=6 - n, per=1000.0 if n < 6 else None) for n in (4, 5, 6)],
        # aynı işyerinden iki ayrı alışveriş (farklı gün): kendi taksitleri sürer
        _t("ORNEK MARKET 2/3", 300.0, no=2, left=1, per=300.0, d=date(2026, 9, 1)),
        _t("ORNEK MARKET 3/3", 300.0, no=3, left=0, d=date(2026, 8, 1)),
        # ardışık değil (3 ve 5): dokunulmaz
        _t("ORNEK SAAT 3/6", 200.0, no=3, left=3, per=200.0, d=date(2026, 7, 1)),
        _t("ORNEK SAAT 5/6", 200.0, no=5, left=1, per=200.0, d=date(2026, 7, 1)),
    ])
    assert card_reader.close_installments(st) == 2
    left = [t.installment_left_amount for t in st.transactions]
    assert left == [None, None, None, 300.0, None, 200.0, 200.0]


def test_reader_template_text_close_line():
    # şablonla okunan satırda "TAKSİT KAPAMA" kalan taksit üretmez (kayıt öncesi kural: db.save_statement)
    assert card_reader.is_close_line("TAKSİT KAPAMA - ÖRNEK MOBİLYA 3/6")
    assert card_reader.is_close_line("TAKSİTLİ İŞLEM KAPAMA")
    assert card_reader.is_close_line("Kalan Taksitler")
    assert not card_reader.is_close_line("ORNEK MOBILYA 3/6 TAKSIT")
    assert not card_reader.is_close_line("Kalan taksit sayısı: 3")
    assert card_reader.installment_base("ÖRNEK MOBİLYA 3/6 TAKSİT") == card_reader.installment_base("ORNEK MOBILYA 4/6")
