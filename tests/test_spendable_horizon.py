"""Ödemelerden sonra (spendable) TEK ufuk (lead 16.11, U1) ve ekstresi yüklenmemiş döngünün ödenmesi (U2).

Ufuk = içinde bulunulan dönemin son günü; dönemin sonuna en çok 1 gün kaldıysa bir sonraki dönemin son günü. Vadesi ufka
kadar olan (geçmiş ve vadesiz dahil) kesin ödemeler düşülür; ufuktan sonraki dönemin sonuna kadar vadeli olanlar
spendable_later'da (düşülmez). Sonraki dönemin düzenli ödemeleri spendable_later'a girmez.
Ay dönümü 17: 2026-11 = 17 Eki – 16 Kas, 2026-12 = 17 Kas – 16 Ara, 2027-01 = 17 Ara – 16 Oca. Bugün sabit (16/17/20 Kas,
14 Kas). Kart numaraları, işyerleri, kişiler ve tutarlar uydurmadır."""
from datetime import date

import pytest

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction

CARD = "4543***1234"
CASH = 100000.0
INST = 1250.0  # tahmini döngüye düşen bilinen taksit (kesin kısım)
BILL = 900.0   # 19 Kas vadeli fatura
DEBT = 5000.0  # 31 Ara vadeli borç
RENT = 15000.0  # kira, her ayın 18'i


def _stmt(tmpdb, *, pe, due, nxt_pe, nxt_due, lines, total=None):
    total = sum(x.amount for x in lines) if total is None else total
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=pe, due_date=due, total_debt=total,
                  min_payment=round(total * 0.2, 2), credit_limit=90000, period_activity=total,
                  next_period_end=nxt_pe, next_due_date=nxt_due)
    s.transactions = lines
    tmpdb.save_statement(f"st-{CARD}-{pe}".encode(), "e.pdf", s)
    return next(x["id"] for x in tmpdb.list_statements() if x["period_end"] == pe.isoformat())


@pytest.fixture()
def book(client, tmpdb):
    """Kesim 13 Kas (son ödeme 23 Kas, ÖDENDİ), sıradaki kesim 13 Ara, son ödeme 23 Ara (tahmini döngü: bilinen taksit
    INST); fatura 19 Kas; borç 31 Ara; kira her ayın 18'i (Kasım döneminden beri)."""
    tmpdb.set_setting("cycle_start_day", "17")
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": CASH}).status_code == 200
    lines = [Transaction(txn_date=date(2026, 11, 2), description="ORNEK MARKET", amount=2000.0, txn_type=PURCHASE, card=CARD),
             Transaction(txn_date=date(2026, 9, 5), description="ORNEK BEYAZ ESYA", amount=INST, txn_type=PURCHASE,
                         card=CARD, installment_no=2, installments_left=3, installment_left_amount=INST)]
    sid = _stmt(tmpdb, pe=date(2026, 11, 13), due=date(2026, 11, 23), nxt_pe=date(2026, 12, 13),
                nxt_due=date(2026, 12, 23), lines=lines)
    tmpdb.mark_statement_paid(sid, 3250.0, "2026-11-14", None)
    assert client.post("/api/bills", json={"provider": "Örnek Elektrik", "amount": BILL,
                                           "due_date": "2026-11-19"}).status_code == 200
    assert client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": DEBT,
                                           "debt_date": "2026-11-01", "due_date": "2026-12-31",
                                           "pay_terms": "tek"}).status_code == 200
    r = client.post("/api/recurring", json={"description": "Kira", "amount": RENT, "kind": "gider", "category": "Kira",
                                            "day_of_month": 18, "start_month": "2026-12"})
    assert r.status_code == 200, r.text
    return client


def _nw(day):
    return finance.net_worth(day)


def _later(nw):
    return {(x["type"], x["due_date"]): x["amount"] for x in nw["spendable_later"]["items"]}


def _parts(nw):
    return {p["key"]: p["amount"] for p in nw["spendable_parts"]}


def test_horizon_rule():
    from app import db

    db.set_setting("cycle_start_day", "17")
    h = finance.spend_horizon
    assert h(date(2026, 11, 14))["end"] == date(2026, 11, 16)  # 2 gün kaldı: bu dönem
    assert h(date(2026, 11, 15))["end"] == date(2026, 12, 16)  # 1 gün kaldı: sonraki dönem
    assert h(date(2026, 11, 16))["end"] == date(2026, 12, 16)  # son gün: sonraki dönem
    assert h(date(2026, 11, 16))["periods"] == ["2026-11", "2026-12"]
    assert h(date(2026, 11, 17))["end"] == date(2026, 12, 16) and h(date(2026, 11, 17))["periods"] == ["2026-12"]
    assert h(date(2026, 11, 20))["end"] == date(2026, 12, 16)
    assert h(date(2026, 11, 20))["later_end"] == date(2027, 1, 16)


@pytest.mark.parametrize("day", [date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 20)])
def test_single_horizon(book, day):
    """16 (son gün → sonraki dönem), 17, 20 Kas: ufuk 16 Ara. Fatura (19 Kas) ve kira (18 Kas) düşülür; 23 Ara kart
    tahmini ve 31 Ara borç sonraya; 18 Ara kirası (sonraki dönemin düzenli ödemesi) hiçbir yerde."""
    nw = _nw(day)
    assert nw["spendable_horizon"] == "2026-12-16"
    p = _parts(nw)
    assert p["bills"] == BILL and p["recurring"] == RENT
    assert p["pending_cards"] == 0 and p["payable"] == 0 and p["card_debt"] == 0
    assert nw["spendable"] == CASH - BILL - RENT
    assert _later(nw) == {("card_estimate", "2026-12-23"): INST, ("borc", "2026-12-31"): DEBT}
    assert nw["spendable_later"]["total"] == INST + DEBT
    assert [x["date"] for x in nw["recurring_items"]] == ["2026-11-18"]  # 18 Ara kirası yok
    # Net durum değişmedi: borç ve tahmin orada tam
    assert nw["payable"] == DEBT


def test_before_last_day_horizon_is_this_period(book):
    """14 Kas: dönemin bitmesine 2 gün var, ufuk 16 Kas. 19 Kas faturası sonraya; kira (18 Kas, sonraki dönemin) hiçbir
    yerde; 23 Kas'ta ödenmiş ekstre yok; 23 Ara tahmini sonraki dönem sonundan (16 Ara) da sonra: yok. 31 Ara borcu
    var (TL borç vadesi ne kadar ileride olursa olsun; lead 16.11)."""
    nw = _nw(date(2026, 11, 14))
    assert nw["spendable_horizon"] == "2026-11-16"
    assert nw["spendable"] == CASH
    assert _later(nw) == {("bill", "2026-11-19"): BILL, ("borc", "2026-12-31"): DEBT}
    assert not nw["recurring_items"]


def test_rent_paid_early_is_nowhere(book, client):
    """Bu dönemin (vadesi 18 Kas) kirası 16 Kas'ta ödendi: ne spendable ne spendable_later kira içerir."""
    r = client.post("/api/entries", json={"entry_date": "2026-11-16", "description": "Kira", "amount": RENT,
                                          "kind": "gider", "category": "Kira", "payment": "havale"})
    assert r.status_code == 200, r.text
    nw = _nw(date(2026, 11, 16))
    assert "recurring" not in _parts(nw) and not nw["recurring_items"]
    assert not [x for x in nw["spendable_later"]["items"] if "Kira" in x["title"]]


def test_overdue_and_undated_debt_always_now(book, client):
    """Vadesi geçmiş ve vadesiz borç ufuktan bağımsız düşülür."""
    for due in ("2026-11-10", None):
        body = {"person": "Örnek Komşu", "direction": "borc", "amount": 700.0, "debt_date": "2026-11-01"}
        if due:
            body["due_date"] = due
        assert client.post("/api/debts", json=body).status_code == 200
    nw = _nw(date(2026, 11, 20))
    assert _parts(nw)["payable"] == 1400.0
    assert nw["spendable"] == CASH - BILL - RENT - 1400.0


def test_far_future_debt_in_later_bill_not(book, client):
    """Mart 2027 vadeli borç spendable_later'da (bilinen tüm borçlar); aynı vadeli fatura yok (pencere sonraki dönem sonu)."""
    assert client.post("/api/debts", json={"person": "Örnek Akraba", "direction": "borc", "amount": 30000.0,
                                           "debt_date": "2026-11-01", "due_date": "2027-03-10",
                                           "pay_terms": "tek"}).status_code == 200
    assert client.post("/api/bills", json={"provider": "Örnek Sigorta", "amount": 4000.0,
                                           "due_date": "2027-03-10"}).status_code == 200
    nw = _nw(date(2026, 11, 20))
    later = _later(nw)
    assert later[("borc", "2027-03-10")] == 30000.0 and ("bill", "2027-03-10") not in later
    assert nw["spendable"] == CASH - BILL - RENT  # düşülmedi
    assert nw["spendable_later"]["total"] == INST + DEBT + 30000.0


def test_debt_jar_not_double_counted_later(book, client, tmpdb):
    """31 Ara borcu için kumbarada 2.000: kumbara düşülür (reserved), sonraya yalnız karşılanmayan 3.000."""
    did = next(d["id"] for d in tmpdb.list_debts() if d["person"] == "Örnek Kişi")
    client.put("/api/debt-plan", json={"monthly": 1000})
    r = client.post(f"/api/debts/{did}/jar", json={})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    r = client.post(f"/api/jars/{jid}/moves", json={"amount": 2000.0, "move_date": "2026-11-15", "note": "Borç"})
    assert r.status_code == 200, r.text
    nw = _nw(date(2026, 11, 20))
    assert nw["reserved"] == 2000.0
    assert _later(nw)[("borc", "2026-12-31")] == DEBT - 2000.0
    assert nw["spendable"] == CASH - BILL - RENT - 2000.0


def test_scheduled_debt_only_installments_in_horizon(client, tmpdb):
    """Ödeme planlı borç: ufka kadar vadesi gelen taksit düşülür, sonraki dönemin taksiti sonraya, gerisi hiçbirinde."""
    tmpdb.set_setting("cycle_start_day", "17")
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": CASH}).status_code == 200
    r = client.post("/api/debts", json={"person": "Örnek Mağaza", "direction": "borc", "creditor_type": "magaza",
                                        "plan_installment": 1000.0, "plan_count": 6, "plan_start": "2026-11",
                                        "plan_day": 25, "debt_date": "2026-11-01"})
    assert r.status_code == 200, r.text
    nw = _nw(date(2026, 11, 20))  # ufuk 16 Ara: 25 Kas taksiti; sonra 25 Ara (≤ 16 Oca); 25 Oca+ hiçbirinde
    assert _parts(nw)["payable"] == 1000.0
    assert _later(nw) == {("borc", "2026-12-25"): 5000.0}  # kalan 5 taksit, vadesi sıradaki taksit


# ---------------------------------------------------------------- U2: ekstresi yüklenmemiş döngü ödendi

@pytest.fixture()
def cut_passed(client, tmpdb):
    """Kesimi 15 Kas'ta geçmiş, ekstresi yüklenmemiş döngü (son ödeme 25 Kas): bilinen taksit 1.250 + kartla girilmiş
    4.000 harcama; geçmiş 3 dönem değişken harcama ~10.000."""
    tmpdb.set_setting("cycle_start_day", "17")
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": CASH}).status_code == 200
    for i, (pe, due) in enumerate([(date(2026, 8, 15), date(2026, 8, 25)), (date(2026, 9, 15), date(2026, 9, 25)),
                                   (date(2026, 10, 15), date(2026, 10, 25))]):
        nxt_pe = date(pe.year + (pe.month == 12), pe.month % 12 + 1, 15)
        nxt_due = date(nxt_pe.year, nxt_pe.month, 25)
        lines = [Transaction(txn_date=date(pe.year, pe.month, 3), description="ORNEK MARKET", amount=10000.0,
                             txn_type=PURCHASE, card=CARD),
                 Transaction(txn_date=date(2026, 7, 5), description="ORNEK BEYAZ ESYA", amount=INST, txn_type=PURCHASE,
                             card=CARD, installment_no=1 + i, installments_left=5 - i, installment_left_amount=INST)]
        sid = _stmt(tmpdb, pe=pe, due=due, nxt_pe=nxt_pe, nxt_due=nxt_due, lines=lines)
        tmpdb.mark_statement_paid(sid, 11250.0, due.isoformat(), None)
    r = client.post("/api/entries", json={"entry_date": "2026-11-05", "description": "Kırtasiye", "amount": 4000.0,
                                          "kind": "gider", "category": "Alışveriş", "payment": "kredi_karti",
                                          "card_last4": "1234"})
    assert r.status_code == 200, r.text
    return client


def _pending(nw):
    return {x["account"]: x["amount"] for x in nw["pending_cards_items"]}


def test_unpaid_cut_cycle_is_deducted(cut_passed):
    nw = _nw(date(2026, 11, 20))
    assert _pending(nw) == {CARD: INST + 4000.0}


@pytest.mark.parametrize("paid,left", [(11500.0, 0.0), (5250.0, 0.0), (3000.0, INST + 4000.0 - 3000.0)])
def test_bank_payment_after_cut_marks_cycle_paid(cut_passed, tmpdb, paid, left):
    """Dökümden gelen kart ödemesi (son 4 hane tutuyor) kesimden sonra: tahminin %80'i ya da kesin kısım kadar ödendiyse
    düşülmez, eksikse kalan. Net durumdaki tahmin de ödenen kadar azalır."""
    before = _nw(date(2026, 11, 20))
    with tmpdb.connect() as c:
        c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account, description, import_hash) "
                  "VALUES ('2026-11-18', ?, '1234', 'Örnek Banka', 'KREDI KARTI ODEMESI', 'h-ornek-1')", (paid,))
    nw = _nw(date(2026, 11, 20))
    assert _pending(nw).get(CARD, 0.0) == left
    est = next(p for p in finance.payables(date(2026, 11, 20)) if p["type"] == "estimate")
    assert est["cycle_paid"]["amount"] == paid and est["cycle_paid"]["covered"] == (left == 0)
    assert nw["card_estimated"] == (0.0 if left == 0 else round(before["card_estimated"] - paid, 2))


def test_payment_before_cut_is_not_for_cycle(cut_passed, tmpdb):
    """Kesimden (15 Kas) önceki ödeme bir önceki ekstrenindir: döngü ödendi sayılmaz."""
    with tmpdb.connect() as c:
        c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account, description, import_hash) "
                  "VALUES ('2026-11-14', 20000, '1234', 'Örnek Banka', 'KREDI KARTI ODEMESI', 'h-ornek-2')")
    assert _pending(_nw(date(2026, 11, 20))) == {CARD: INST + 4000.0}


def test_other_card_payment_not_counted(cut_passed, tmpdb):
    with tmpdb.connect() as c:
        c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account, description, import_hash) "
                  "VALUES ('2026-11-18', 20000, '9999', 'Örnek Banka', 'KREDI KARTI ODEMESI', 'h-ornek-3')")
    assert _pending(_nw(date(2026, 11, 20))) == {CARD: INST + 4000.0}


def test_cycle_pay_endpoint(cut_passed, monkeypatch):
    """Elle "Ödendi" (ekstresi yüklenmemiş döngü): POST /api/cards/cycle-pay; geri al DELETE."""
    import app.finance as f

    real = f.net_worth
    monkeypatch.setattr(f, "net_worth", lambda today=None: real(today or date(2026, 11, 20)))
    r = cut_passed.post("/api/cards/cycle-pay", json={"card_no": CARD, "amount": 9000.0, "paid_at": "2026-11-19"})
    assert r.status_code == 200, r.text
    assert r.json()["card_payment_id"]
    assert _pending(_nw(date(2026, 11, 20))) == {}
    assert cut_passed.request("DELETE", "/api/cards/cycle-pay", params={"card_no": CARD}).status_code == 200
    assert _pending(_nw(date(2026, 11, 20))) == {CARD: INST + 4000.0}
    assert cut_passed.post("/api/cards/cycle-pay", json={"card_no": "0000***0000", "amount": 10.0}).status_code == 404


@pytest.mark.parametrize("amount,paid", [(9500.0, True), (2000.0, False), (60000.0, False)])
def test_payment_without_card_digits(cut_passed, tmpdb, amount, paid):
    """Kart hanesi olmayan ödeme (başka bankanın kartına EFT): yalnız ödeme eşiği ile tahminin 1,5 katı arasındaysa."""
    with tmpdb.connect() as c:
        c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account, description, import_hash) "
                  "VALUES ('2026-11-18', ?, NULL, 'Örnek Banka', 'EFT ORNEK', 'h-ornek-4')", (amount,))
    assert (_pending(_nw(date(2026, 11, 20))) == {}) == paid


# ---------------------------------------------------------------- kayma (lead 16.11): hafta sonuna denk gelen vade

def _freeze(monkeypatch, d: date) -> None:
    class _D(date):
        @classmethod
        def today(cls):
            return d
    monkeypatch.setattr(finance, "date", _D)


def test_salary_on_saturday_still_awaited(tmpdb, monkeypatch):
    """15 Ağu Cumartesi maaşı 17 Ağu (yeni dönem) sabahı yatmadı: tahmini gerçek gelir değil, bekliyor; Maaş gelince
    iki maaş (bugün gelecek Ağustos + 15 Eylül). Tolerans (SALARY_GRACE_DAYS) geçince tahmini gelire döner."""
    tmpdb.set_setting("cycle_start_day", "17")
    _freeze(monkeypatch, date(2026, 8, 17))
    tmpdb.create_recurring({"description": "Maaş", "amount": 50000.0, "kind": "gelir", "category": "Maaş",
                            "day_of_month": 15, "start_month": "2026-05"})
    sai = finance.net_worth(date(2026, 8, 17))["spendable_after_income"]
    assert sai and sai["income"] == 100000.0
    _freeze(monkeypatch, date(2026, 8, 25))
    assert finance.net_worth(date(2026, 8, 25))["spendable_after_income"]["income"] == 50000.0


def test_previous_period_rent_still_deducted_within_late_window(tmpdb, monkeypatch):
    """16 May Cumartesi vadeli kira, 17 May (yeni dönem) girilmedi: hâlâ düşülür (gecikti); 15 günden eskisi düşülmez."""
    tmpdb.set_setting("cycle_start_day", "17")
    _freeze(monkeypatch, date(2026, 5, 17))
    tmpdb.create_recurring({"description": "Kira", "amount": 20000.0, "kind": "gider", "category": "Kira & Aidat",
                            "day_of_month": 16, "start_month": "2026-01"})
    tmpdb.create_entry({"entry_date": "2026-04-16", "description": "Kira", "amount": 20000.0, "kind": "gider",
                        "category": "Kira & Aidat", "payment": "nakit"})
    due = finance._due_this_period(date(2026, 5, 17), [])["recurring"]["items"]
    assert sorted(x["date"] for x in due) == ["2026-05-16", "2026-06-16"]
    assert [x.get("overdue") for x in sorted(due, key=lambda x: x["date"])] == [True, None]
    later = finance._due_this_period(date(2026, 6, 2), [])["recurring"]["items"]
    assert sorted(x["date"] for x in later) == ["2026-06-16"]
