"""Ödemelerden sonra (spendable; yetenek spendable_all_due) bu dönemin KESİN ödemelerinin hepsini düşer: kesilmiş ve
kesilecek kart ekstrelerinin kesin kısmına ek olarak dönem içinde vadeli ödenmemiş faturalar, girilmemiş düzenli giderler,
kesilecek ekstre listesinde olmayan kartın bu dönemki taksit/abonelikleri ve son ödeme günü geçmiş kart kalanına
işleyecek faiz+vergi tahmini. Yeni satırlar yalnız sıfırdan büyükse; eski satırlar aynı. Kalemler Ay sonunda kalacak
(expected.parts) ile aynı kaynaktan: bir kalem iki satırda sayılmaz. Tarihler bugüne göre; kart numaraları, işyerleri,
kişiler ve tutarlar uydurmadır."""
from datetime import date, timedelta

import pytest

from app import debt_plan, finance
from app.parsers.base import PURCHASE, Statement, Transaction


@pytest.fixture(autouse=True)
def _genis_ufuk(monkeypatch):
    """Bu dosya Ödemelerden sonra kalemlerinin kuralını sınar; TEK UFUK (spend_horizon, lead 16.11) bugünün takvimine
    bağlı olduğundan burada sabit ve geniş (bugün + 400 gün, yalnız içinde bulunulan dönem); ufuk kuralı
    tests/test_spendable_horizon.py'de sabit tarihlerle."""
    from datetime import timedelta as _td

    from app import finance as _f

    monkeypatch.setattr(_f, "spend_horizon", lambda today: {"end": today + _td(days=400), "periods": [_f.current_period(today)],
                                                             "later_end": today + _td(days=430)})


CARD = "4543***1234"
CARD_B = "5400***9602"
T = date.today()
CASH = 100000.0
INST, SUB = 1250.0, 150.0
OLD_KEYS = ["cash", "card_debt", "pending_cards", "payable", "reserved", "zakat_due"]
NEW_KEYS = {"pending_other", "card_interest", "card_late_interest", "bills", "recurring"}


def _stmt(tmpdb, *, card=CARD, bank="VakıfBank", pe, due, nxt_pe, nxt_due, lines, total=None, min_payment=None):
    total = sum(x.amount for x in lines) if total is None else total
    s = Statement(bank=bank, card_no=card, period_end=pe, due_date=due, total_debt=total,
                  min_payment=min_payment if min_payment is not None else round(total * 0.2, 2), credit_limit=50000,
                  period_activity=total, next_period_end=nxt_pe, next_due_date=nxt_due)
    s.transactions = lines
    tmpdb.save_statement(f"st-{card}-{pe}".encode(), "e.pdf", s)
    with tmpdb.connect() as c:  # abonelik kategorisi (sınıflandırıcıdan bağımsız)
        c.execute("UPDATE transactions SET category = 'Dijital Abonelik' WHERE description = 'ORNEK DIZI'")
    return next(x["id"] for x in tmpdb.list_statements() if x["card_no"] == card and x["period_end"] == pe.isoformat())


def _lines(card=CARD, inst_no=2, left=3, extra=()):
    return [
        Transaction(txn_date=T - timedelta(days=20), description="ORNEK MARKET", amount=2000.0, txn_type=PURCHASE, card=card),
        Transaction(txn_date=T - timedelta(days=40), description="ORNEK BEYAZ ESYA", amount=INST, txn_type=PURCHASE,
                    card=card, installment_no=inst_no, installments_left=left, installment_left_amount=INST),
        Transaction(txn_date=T - timedelta(days=15), description="ORNEK DIZI", amount=SUB, txn_type=PURCHASE, card=card),
        *extra,
    ]


def _fixture_statement(tmpdb, card=CARD):
    """Kesimi 10 gün önce, son ödemesi 5 gün sonra; sıradaki kesim 20 gün sonra (açık dönem: pending_cards)."""
    return _stmt(tmpdb, card=card, pe=T - timedelta(days=10), due=T + timedelta(days=5),
                 nxt_pe=T + timedelta(days=20), nxt_due=T + timedelta(days=30), lines=_lines(card))


def _account(client, balance=CASH):
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": balance}).status_code == 200


def _nw(client):
    return client.get("/api/net-worth").json()


def _parts(nw):
    return {p["key"]: p for p in nw["spendable_parts"]}


def _from_parts(nw):
    return round(sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in nw["spendable_parts"]), 2)


def _expected(client):
    return client.get("/api/summary", params={"month": finance.current_period()}).json()["expected"]


def _bill(client, amount, due, provider="Örnek Elektrik"):
    r = client.post("/api/bills", json={"provider": provider, "amount": amount, "due_date": due.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _rent(client, amount=15000.0, desc="Kira", kind="gider", day=28):
    r = client.post("/api/recurring", json={"description": desc, "amount": amount, "kind": kind, "category": "Kira",
                                            "day_of_month": day, "start_month": finance.add_months(finance.current_period(), -2)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _card_entry(client, amount, day, desc="Kırtasiye", last4="1234"):
    r = client.post("/api/entries", json={"entry_date": day.isoformat(), "description": desc, "amount": amount,
                                          "kind": "gider", "category": "Alışveriş", "payment": "kredi_karti",
                                          "card_last4": last4})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture()
def book(client, tmpdb):
    _account(client)
    _fixture_statement(tmpdb)
    return client


def _period_end():
    return finance.period_bounds(finance.current_period())[1]


# ---------------------------------------------------------------- eski satırlar aynı

def test_old_rows_unchanged_without_new_items(book):
    nw = _nw(book)
    assert [p["key"] for p in nw["spendable_parts"]] == OLD_KEYS  # sıfır yeni satır yok, eski satırlar (sıfır da) aynı sırada
    assert nw["spendable"] == CASH - 3400.0 - INST - SUB == _from_parts(nw)
    for k in NEW_KEYS:
        assert nw[f"{k}_items"] == []
    assert nw["card_debt_items"] == [{"title": f"VakıfBank {CARD}", "amount": 3400.0, "date": (T + timedelta(days=5)).isoformat(),
                                      "overdue": False}]
    assert nw["pending_cards_items"][0]["account"] == CARD


def test_capability(client):
    caps = client.get("/api/meta").json()["capabilities"]
    assert "spendable_all_due" in caps and "spendable_pending_cards" in caps


# ---------------------------------------------------------------- faturalar

def test_unpaid_bill_this_period_deducted(book):
    once = _nw(book)
    bid = _bill(book, 640.0, T)
    nw = _nw(book)
    row = _parts(nw)["bills"]
    assert row == {"key": "bills", "label": "Ödenmemiş faturalar", "amount": 640.0, "sign": "−"}
    assert nw["bills_items"] == [{"title": "Örnek Elektrik", "amount": 640.0, "date": T.isoformat(), "id": bid, "overdue": False}]
    assert nw["spendable"] == round(once["spendable"] - 640.0, 2) == _from_parts(nw) == nw["totals"]["spendable"]
    assert _expected(book)["parts"]["pending_bills"] == 640.0  # Ay sonunda kalacak ile aynı kalem


def test_paid_bill_not_deducted(book):
    once = _nw(book)
    bid = _bill(book, 640.0, T)
    # "Hesabımdan değil" (account_id null): fatura kalemi düşmez, hesap belirsizliği de yok
    assert book.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": T.isoformat(),
                                                   "account_id": None}).status_code == 200
    nw = _nw(book)
    assert "bills" not in _parts(nw) and nw["bills_items"] == []
    assert nw["spendable"] == once["spendable"]
    # hesap söylenmeden (eski istemci): fatura kalemi yine yok, ödeme ekstrenin belirsiz ödemesiyle aynı kuralla
    # "Hesabı belli olmayan ödeme" satırında (yetenek bill_pay_account; çapa kuralı tutarsa düşülür)
    assert book.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": T.isoformat()}).status_code == 200
    nw = _nw(book)
    assert "bills" not in _parts(nw) and nw["bills_items"] == []
    assert nw["spendable"] == once["spendable"]  # anahtar yok: önceki "Hesabımdan değil" cevabı korunur


def test_bill_due_next_period_not_deducted(book, monkeypatch):
    # ufuk = dönem sonu (dosyanın geniş ufku yerine; dönemin son günü kuralı test_spendable_horizon.py'de)
    monkeypatch.setattr(finance, "spend_horizon", lambda today: {"end": _period_end(), "periods": [finance.current_period(today)],
                                                                 "later_end": _period_end() + timedelta(days=31)})
    once = _nw(book)
    _bill(book, 900.0, _period_end() + timedelta(days=3), provider="Örnek Su")
    nw = _nw(book)
    assert "bills" not in _parts(nw) and nw["spendable"] == once["spendable"]
    assert _expected(book)["parts"]["pending_bills"] == 0


def test_overdue_bill_from_previous_period_deducted(book):
    """Vadesi önceki dönemde geçmiş, hâlâ ödenmemiş fatura hâlâ borç: düşülür (gecikti). Ay sonunda kalacak bugünkü
    kuralında (yalnız bu dönemin faturası): bilinçli fark. Ödenince düşmez."""
    once = _nw(book)
    old_due = finance.period_bounds(finance.current_period())[0] - timedelta(days=3)
    bid = _bill(book, 480.0, old_due, provider="Örnek Doğalgaz")
    nw = _nw(book)
    assert _parts(nw)["bills"]["amount"] == 480.0
    assert nw["bills_items"] == [{"title": "Örnek Doğalgaz", "amount": 480.0, "date": old_due.isoformat(), "id": bid, "overdue": True}]
    assert nw["spendable"] == round(once["spendable"] - 480.0, 2) == _from_parts(nw)
    assert _expected(book)["parts"]["pending_bills"] == 0  # Ay sonunda kalacak'a girmez
    assert book.post(f"/api/bills/{bid}/pay", json={"amount": 480.0, "paid_at": T.isoformat()}).status_code == 200
    assert "bills" not in _parts(_nw(book))


def test_overdue_recurring_from_previous_period_not_deducted(book):
    """Geçmiş dönemde girilmemiş düzenli ödeme (tahmin) düşülmez: yalnız bu dönemin kirası."""
    _rent(book)  # başlangıç 2 dönem önce; geçmiş dönemlerin kirası girilmedi
    assert _parts(_nw(book))["recurring"]["amount"] == 15000.0


# ---------------------------------------------------------------- düzenli ödemeler (kira, aidat)

def test_pending_rent_deducted_and_once_when_entered(book):
    once = _nw(book)
    rid = _rent(book)
    nw = _nw(book)
    assert _parts(nw)["recurring"] == {"key": "recurring", "label": "Düzenli ödemeler (kira, aidat…)", "amount": 15000.0, "sign": "−"}
    assert [(x["title"], x["amount"], x["recurring_id"]) for x in nw["recurring_items"]] == [("Kira", 15000.0, rid)]
    assert nw["recurring_items"][0]["date"]
    assert nw["spendable"] == round(once["spendable"] - 15000.0, 2)
    s = book.get("/api/summary", params={"month": finance.current_period()}).json()
    assert s["pending"]["expense"] == 15000.0 == s["expected"]["parts"]["pending_recurring_expense"]
    # kira girildi (bankadan çıktı): bekleyen olmaktan çıkar, ikinci kez düşülmez
    r = book.post("/api/entries", json={"entry_date": T.isoformat(), "description": "Kira", "amount": 15000.0, "kind": "gider",
                                        "category": "Kira", "payment": "nakit", "recurring_id": rid})
    assert r.status_code == 200, r.text
    after = _nw(book)
    assert "recurring" not in _parts(after) and after["recurring_items"] == []


def test_pending_income_not_deducted(book):
    once = _nw(book)
    _rent(book, 40000.0, desc="Maaş", kind="gelir")
    nw = _nw(book)
    assert nw["spendable"] == once["spendable"] and "recurring" not in _parts(nw)
    assert nw["spendable_after_income"]["amount"] == round(nw["spendable"] + 40000.0, 2)


# ---------------------------------------------------------------- taksit / abonelik: çift sayım yok

def test_card_installments_not_double_counted(book):
    """Açık dönemi olan kartın taksit ve aboneliği pending_cards'ta; kart dışı satırda yeniden yok."""
    nw = _nw(book)
    assert nw["pending_cards"] == INST + SUB
    assert "pending_other" not in _parts(nw) and nw["pending_other_items"] == []


def test_installments_of_card_without_open_cycle_deducted(client, tmpdb):
    """Kesilecek ekstre listesinde olmayan kart (sıradaki son ödeme günü de geçti, ekstresi yüklenmemiş): Ay sonunda
    kalacak'taki bu dönemki taksit ve abonelikleri ayrı satırda; toplamı expected.parts ile aynı."""
    _account(client)
    sid = _stmt(tmpdb, pe=T - timedelta(days=45), due=T - timedelta(days=35), nxt_pe=T - timedelta(days=15),
                nxt_due=T - timedelta(days=5), lines=_lines(left=6))
    # eski ekstre ödendi (faiz/kalan yok): yalnız bekleyen taksit/abonelik kalsın
    assert client.post(f"/api/statements/{sid}/pay", json={"amount": 3400.0, "paid_at": (T - timedelta(days=36)).isoformat()}).status_code == 200
    nw = _nw(client)
    parts = _expected(client)["parts"]
    want = round(parts["pending_installments"] + parts["pending_subscriptions"], 2)
    assert want >= SUB  # abonelik her ay; taksit bu döneme düşüyorsa o da
    assert nw["pending_cards"] == 0
    assert _parts(nw)["pending_other"] == {"key": "pending_other", "label": "Bekleyen taksit ve abonelikler (kart dışı)",
                                           "amount": want, "sign": "−"}
    assert round(sum(x["amount"] for x in nw["pending_other_items"]), 2) == want
    assert {x["account"] for x in nw["pending_other_items"]} == {CARD}
    assert nw["spendable"] == round(CASH - want, 2) == _from_parts(nw)


def test_every_pending_expense_counted_once(client, tmpdb):
    """Ay sonunda kalacak'ın bekleyen giderleri (expected.parts.pending_expense) Ödemelerden sonra'da tam bir kez:
    fatura + düzenli + kart dışı + açık dönemi bu döneme düşen kartların taksit/aboneliği. Eksik kalem ya da çift yok."""
    _account(client)
    _fixture_statement(tmpdb)
    sid = _stmt(tmpdb, card=CARD_B, bank="Kuveyt Türk", pe=T - timedelta(days=45), due=T - timedelta(days=35),
                nxt_pe=T - timedelta(days=15), nxt_due=T - timedelta(days=5), lines=_lines(card=CARD_B, left=6))
    assert client.post(f"/api/statements/{sid}/pay", json={"amount": 3400.0, "paid_at": (T - timedelta(days=36)).isoformat()}).status_code == 200
    _bill(client, 640.0, T)
    late = _bill(client, 480.0, finance.period_bounds(finance.current_period())[0] - timedelta(days=3), provider="Örnek Doğalgaz")
    _rent(client)
    nw, exp = _nw(client), _expected(client)
    p = _parts(nw)
    # önceki dönemden kalan ödenmemiş fatura yalnız Ödemelerden sonra'da (gecikti); Ay sonunda kalacak'ta yok
    prev = sum(x["amount"] for x in nw["bills_items"] if x["id"] == late)
    assert prev == 480.0 and all(x["overdue"] for x in nw["bills_items"] if x["id"] == late)
    cur = finance.current_period()
    # açık dönemi bu dönemde kesilen kartın kesin kısmı Ay sonunda kalacak'ta taksit/abonelik satırı (değilse sonraki ay)
    in_cur = sum(x["installments"] + x["subscriptions"] for x in nw["pending_cards_items"]
                 if any(m["month"] == cur and any(ln.get("account") == x["account"] and ln["type"] in ("taksit", "abonelik")
                                                  for ln in m["lines"]) for m in finance.plan(1)["months"]))
    got = p["bills"]["amount"] - prev + p["recurring"]["amount"] + p["pending_other"]["amount"] + in_cur
    assert round(got, 2) == exp["parts"]["pending_expense"]
    keys = [x["key"] for x in nw["spendable_parts"]]
    assert len(keys) == len(set(keys))
    assert nw["spendable"] == _from_parts(nw)


def test_forecast_relation_documented(client, tmpdb):
    """Yaklaşık ilişki (tam eşitlik şart değil): Ay sonunda kalacak ≈ Ödemelerden sonra + bekleyen gelir − değişken tahmin
    (+ dönem başı farkları). Bu kurguda dönemin gerçekleşen hareketi yok, kart ekstreleri kesilmiş/kesilecek kısım
    olarak ikisinde de aynı; fark yalnız elde olan para, kesilmiş ekstre (önceki dönemin harcaması) ve dönem dışı kısımlar."""
    _account(client)
    _bill(client, 640.0, T)
    _rent(client)
    # maaş 26'sı: bu dönemin geliri (28'i ay sonuna ≤5 gün → sonraki dönemi fonlardı, db.salary_lead)
    _rent(client, 40000.0, desc="Maaş", kind="gelir", day=26)
    nw, exp = _nw(client), _expected(client)
    # kartsız defter: forecast_net = gelir − (fatura + kira); spendable = nakit − (fatura + kira)
    assert exp["forecast_net"] == round(40000.0 - 640.0 - 15000.0, 2)
    assert nw["spendable"] == round(CASH - 640.0 - 15000.0, 2)
    assert round(nw["spendable"] + exp["parts"]["pending_income"] - exp["variable_estimate"] - CASH, 2) == exp["forecast_net"]


# ---------------------------------------------------------------- provizyon (kesimden hemen önceki kart harcaması)

def test_provision_spend_counted_once_across_statement(client, tmpdb):
    """Kesimden 1 gün önce yapılmış, ekstrede çıkmayan (provizyonda) kart harcaması sonraki ekstreye devreder
    (POSTING_DAYS kuralı, değişmedi): pending_cards'ta bir kez. Sonraki ekstre yüklenip harcama orada çıkınca
    pending_cards'tan düşer, kesilmiş ekstre kalanında sayılır; toplamda iki kez yok."""
    _account(client)
    # eski ekstre bir önceki (ya da daha eski) dönemde kesildi ve ödendi; sıradaki kesim dün
    old = _stmt(tmpdb, pe=T - timedelta(days=32), due=T - timedelta(days=22), nxt_pe=T - timedelta(days=1),
                nxt_due=T + timedelta(days=9), lines=_lines())
    assert client.post(f"/api/statements/{old}/pay", json={"amount": 3400.0, "paid_at": (T - timedelta(days=23)).isoformat()}).status_code == 200
    eid = _card_entry(client, 777.0, T - timedelta(days=33))  # eski kesimden 1 gün önce
    tmpdb.auto_link()
    assert next(e for e in tmpdb.list_entries() if e["id"] == eid)["linked_txn_id"] is None  # eski ekstrede yok
    before = _nw(client)
    assert before["card_debt"] == 0
    item = before["pending_cards_items"][0]
    assert item["spent"] == 777.0 and before["pending_cards"] == INST + SUB + 777.0
    # sonraki ekstre: devreden taksit (3/5) + abonelik + provizyondaki harcama
    prov = Transaction(txn_date=T - timedelta(days=33), description="KIRTASIYE ORNEK", amount=777.0, txn_type=PURCHASE, card=CARD)
    lines = [Transaction(txn_date=T - timedelta(days=40), description="ORNEK BEYAZ ESYA", amount=INST, txn_type=PURCHASE,
                         card=CARD, installment_no=3, installments_left=2, installment_left_amount=INST),
             Transaction(txn_date=T - timedelta(days=5), description="ORNEK DIZI", amount=SUB, txn_type=PURCHASE, card=CARD), prov]
    _stmt(tmpdb, pe=T - timedelta(days=1), due=T + timedelta(days=9), nxt_pe=T + timedelta(days=29),
          nxt_due=T + timedelta(days=39), lines=lines)
    tmpdb.auto_link()
    assert next(e for e in tmpdb.list_entries() if e["id"] == eid)["linked_txn_id"] is not None
    after = _nw(client)
    assert after["card_debt"] == INST + SUB + 777.0  # harcama kesilmiş ekstrede
    assert after["pending_cards_items"][0]["spent"] == 0  # ... ve artık kesilecekte değil
    # fark yalnız yeni öğrenilen sıradaki dönemin taksit (4/5) + aboneliği: 777 iki kez düşülmedi
    assert round(before["spendable"] - after["spendable"], 2) == INST + SUB
    assert after["pending_cards"] == INST + SUB


# ---------------------------------------------------------------- asgari / hiç ödenmemiş ekstre, faiz tahmini

def _overdue_statement(tmpdb, card=CARD):
    """Kesimi 40, son ödemesi 30 gün önce geçmiş 3.400'lük ekstre (asgari 680); sıradaki kesim 8 gün önce
    (yüklenmedi; 32 gün ara: her zaman başka dönem), sıradaki son ödeme 2 gün sonra."""
    return _stmt(tmpdb, card=card, pe=T - timedelta(days=40), due=T - timedelta(days=30), nxt_pe=T - timedelta(days=8),
                 nxt_due=T + timedelta(days=2), lines=_lines(card), min_payment=680.0)


def _pay(client, sid, amount):
    r = client.post(f"/api/statements/{sid}/pay", json={"amount": amount, "paid_at": (T - timedelta(days=31)).isoformat()})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("paid,key,label", [
    (680.0, "card_interest", "Tahmini faiz ve vergi (devreden kalana)"),   # asgari ödendi
    (1000.0, "card_interest", "Tahmini faiz ve vergi (devreden kalana)"),  # asgarinin üstü, tamamı değil
    (300.0, "card_late_interest", "Tahmini gecikme faizi ve vergi"),       # asgarinin altında
    (0.0, "card_late_interest", "Tahmini gecikme faizi ve vergi"),         # hiç ödenmedi
])
def test_unpaid_remaining_after_due_and_interest(client, tmpdb, paid, key, label):
    _account(client)
    sid = _overdue_statement(tmpdb)
    if paid:
        _pay(client, sid, paid)
    nw = _nw(client)
    left = round(3400.0 - paid, 2)
    # (1) ödenmemiş kalan kesilmiş ekstre olarak düşülmeye devam eder; gecikti işareti dökümde
    assert nw["card_debt"] == left
    assert nw["card_debt_items"] == [{"title": f"VakıfBank {CARD}", "amount": left,
                                      "date": (T - timedelta(days=30)).isoformat(), "overdue": True}]
    # (3) faiz + vergi tahmini: Kartlar ekranının faiz satırıyla aynı hesap (oran yok → genel oran)
    want = debt_plan.monthly_interest(left, debt_plan.GENERIC_RATE)
    other = ({"card_interest", "card_late_interest"} - {key}).pop()
    assert _parts(nw)[key] == {"key": key, "label": label, "amount": want, "sign": "−"} and other not in _parts(nw)
    assert nw[f"{key}_items"][0]["amount"] == want and nw[f"{key}_items"][0]["remaining"] == left
    assert nw["spendable"] == round(CASH - left - nw["pending_cards"] - want, 2) == _from_parts(nw)
    card = next(c for c in client.get("/api/payables").json() if c["type"] == "statement")
    assert card["interest"]["amount"] == want and card["interest"]["state"] == "after_due"
    # değer ilkesi: metin asgari ödemeyi önermez
    assert "asgari" not in nw["spendable_explain"].lower() and "tamamını ödersen faiz durur" in nw["spendable_explain"]


@pytest.mark.parametrize("paid", [680.0, 0.0])
def test_rolled_into_next_statement_counted_once(client, tmpdb, paid):
    """(2) Sonraki ekstre gelince devreden kalan (ve işleyen faiz) o ekstrenin içinde: eski ekstre rolled_into ile
    toplamdan çıkar, faiz satırı da kalkar (yeni ekstrenin son ödemesi gelmedi). İki kez düşülmez."""
    _account(client)
    sid = _overdue_statement(tmpdb)
    if paid:
        _pay(client, sid, paid)
    left = round(3400.0 - paid, 2)
    carry = Transaction(txn_date=T - timedelta(days=9), description="ORNEK FAIZ", amount=round(left * 0.05, 2),
                        txn_type=PURCHASE, card=CARD)
    new_total = round(left + INST + SUB + carry.amount, 2)
    _stmt(tmpdb, pe=T - timedelta(days=8), due=T + timedelta(days=2), nxt_pe=T + timedelta(days=22),
          nxt_due=T + timedelta(days=32), lines=_lines(inst_no=3, left=2, extra=[carry])[1:], total=new_total)
    nw = _nw(client)
    assert nw["card_debt"] == new_total  # yalnız son ekstre
    assert [o["statement_id"] for o in nw["card_overdue"]] == [sid]  # eski ekstre bilgi, toplamda yok
    assert "card_interest" not in _parts(nw) and "card_late_interest" not in _parts(nw)
    assert nw["card_debt_items"][0]["overdue"] is False
    assert nw["spendable"] == round(CASH - new_total - nw["pending_cards"], 2) == _from_parts(nw)


def test_before_due_partial_payment_no_interest_row(client, tmpdb):
    """Son ödeme günü gelmeden kısmi ödeme: kalanın tamamı zaten düşülüyor; faiz satırı yok (tamamı ödenirse işlemez)."""
    _account(client)
    sid = _fixture_statement(tmpdb)
    assert client.post(f"/api/statements/{sid}/pay", json={"amount": 680.0, "paid_at": T.isoformat()}).status_code == 200
    nw = _nw(client)
    assert nw["card_debt"] == 3400.0 - 680.0
    assert not ({"card_interest", "card_late_interest"} & set(_parts(nw)))
