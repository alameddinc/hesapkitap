"""Harcanabilir kesilecek kart ekstrelerinin KESİN kısmını da düşer (yetenek spendable_pending_cards): kartla girilmiş
ekstresiz harcama + ekstresi gelecek bilinen taksit + abonelik. Değişken "her zamanki harcama" tahmini düşülmez (o
Dönem sonu'nda). spendable_after_income: bu dönem beklenen gelirler gelince (bilgi). Tarihler bugüne göre; kart
numaraları, işyerleri ve tutarlar uydurmadır."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction

CARD = "4543***1234"
CARD_B = "5400***9602"
T = date.today()
CASH = 100000.0
# son ekstre: 3.400 = market 2.000 + taksit 1.250 (2/5, kalan 3 × 1.250) + abonelik 150
STMT_TOTAL = 3400.0
INST, SUB = 1250.0, 150.0


def _statement(tmpdb, card=CARD, bank="VakıfBank", total=STMT_TOTAL, scale=1.0):
    pe = T - timedelta(days=10)
    lines = [
        Transaction(txn_date=T - timedelta(days=20), description="ORNEK MARKET", amount=2000.0 * scale, txn_type=PURCHASE, card=card),
        Transaction(txn_date=T - timedelta(days=40), description="ORNEK BEYAZ ESYA", amount=INST * scale, txn_type=PURCHASE,
                    card=card, installment_no=2, installments_left=3, installment_left_amount=INST * scale),
        Transaction(txn_date=T - timedelta(days=15), description="ORNEK DIZI", amount=SUB * scale, txn_type=PURCHASE, card=card),
    ]
    s = Statement(bank=bank, card_no=card, period_end=pe, due_date=T + timedelta(days=5), total_debt=total * scale,
                  min_payment=100, credit_limit=50000, period_activity=total * scale,
                  next_period_end=T + timedelta(days=20), next_due_date=T + timedelta(days=30))
    s.transactions = lines
    tmpdb.save_statement(f"st-{card}-{pe}".encode(), "e.pdf", s)
    with tmpdb.connect() as c:  # abonelik kategorisi (sınıflandırıcıdan bağımsız)
        c.execute("UPDATE transactions SET category = 'Dijital Abonelik' WHERE description = 'ORNEK DIZI'")


def _account(client):
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": CASH}).status_code == 200


def _card_entry(client, amount=900.0, days_ago=3, last4="1234", payment="kredi_karti", desc="Kırtasiye"):
    r = client.post("/api/entries", json={"entry_date": (T - timedelta(days=days_ago)).isoformat(), "description": desc,
                                          "amount": amount, "kind": "gider", "category": "Alışveriş", "payment": payment,
                                          **({"card_last4": last4} if payment == "kredi_karti" else {})})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _nw(client):
    return client.get("/api/net-worth").json()


def _spendable_from_parts(nw):
    return round(sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in nw["spendable_parts"]), 2)


@pytest.fixture()
def book(client, tmpdb):
    _account(client)
    _statement(tmpdb)
    return client


def test_card_entry_without_statement_is_deducted(book):
    once = _nw(book)
    _card_entry(book, 900.0)
    _card_entry(book, 400.0, payment="nakit")  # nakit: bakiyeden zaten düşer, kart ekstresine girmez
    nw = _nw(book)
    assert nw["pending_cards"] == round(once["pending_cards"] + 900.0, 2) == INST + SUB + 900.0
    assert nw["spendable"] == round(once["spendable"] - 900.0, 2)
    assert nw["spendable"] == nw["totals"]["spendable"] == _spendable_from_parts(nw)
    item = nw["pending_cards_items"][0]
    assert (item["installments"], item["subscriptions"], item["spent"]) == (INST, SUB, 900.0)


def test_known_installment_and_subscription_deducted(book):
    nw = _nw(book)
    # kesilmiş ekstre (3.400) + sıradaki ekstreye düşecek taksit ve abonelik (1.400)
    assert nw["card_debt"] == STMT_TOTAL and nw["pending_cards"] == INST + SUB
    assert nw["spendable"] == CASH - STMT_TOTAL - INST - SUB


def test_variable_estimate_not_deducted(book):
    _card_entry(book, 900.0)
    nw = _nw(book)
    # kesim tahmini değişken harcamayı da (market medyanı 2.000) içerir; Harcanabilir yalnız kesin kısmı düşer
    assert nw["card_estimated"] > nw["pending_cards"]
    assert nw["spendable"] == round(CASH - nw["card_debt"] - nw["pending_cards"], 2)
    # net_display davranışı aynı: kesim tahmininin tamamı (çift düşüm yok, pending_cards ayrıca düşülmez)
    assert nw["net_display"] == round(nw["net"] - nw["card_estimated"], 2)
    assert "her zamanki" in nw["spendable_explain"] and "Dönem sonu" in nw["spendable_explain"]


def test_statement_line_not_double_counted(book, tmpdb):
    """Kesilmiş ekstredeki satırla eşleşen elle kayıt (linked) yalnız kesilmiş ekstrede (card_debt) sayılır."""
    once = _nw(book)
    eid = _card_entry(book, 2000.0, days_ago=20, desc="Market")
    tmpdb.auto_link()
    assert next(e for e in tmpdb.list_entries() if e["id"] == eid)["linked_txn_id"] is not None
    nw = _nw(book)
    assert nw["pending_cards"] == once["pending_cards"] and nw["spendable"] == once["spendable"]


def test_old_unmatched_and_skipped_entries_not_deducted(book, tmpdb):
    once = _nw(book)
    _card_entry(book, 700.0, days_ago=30)  # son kesimden 20 gün önce, ekstrede yok: "bulunamadı" diye sorulur, düşülmez
    _card_entry(book, 500.0, days_ago=-2)  # ileri tarihli
    assert _nw(book)["pending_cards"] == once["pending_cards"]


def test_paid_statement_keeps_pending_part(book, tmpdb):
    """Kesilmiş ekstre ödendi: card_debt 0; kesilecek kısım (taksit + abonelik + kartla harcama) yine düşülür."""
    _card_entry(book, 900.0)
    sid = tmpdb.list_statements()[0]["id"]
    r = book.post(f"/api/statements/{sid}/pay", json={"amount": STMT_TOTAL, "paid_at": T.isoformat()})
    assert r.status_code == 200, r.text
    nw = _nw(book)
    assert nw["card_debt"] == 0
    assert nw["spendable"] == round(nw["cash"] - (INST + SUB + 900.0), 2)


def test_spendable_parts_row(book):
    _card_entry(book, 900.0)
    nw = _nw(book)
    row = next(p for p in nw["spendable_parts"] if p["key"] == "pending_cards")
    assert row == {"key": "pending_cards", "label": "Kesilecek kart ekstreleri (harcadıkların)", "amount": INST + SUB + 900.0,
                   "sign": "−"}
    keys = [p["key"] for p in nw["spendable_parts"]]
    assert keys.index("pending_cards") == keys.index("card_debt") + 1
    assert nw["totals"]["pending_cards"] == nw["pending_cards"]


def test_no_cards_no_pending(client):
    _account(client)
    nw = _nw(client)
    assert nw["pending_cards"] == 0 and nw["pending_cards_items"] == [] and nw["spendable"] == CASH


def test_plan_and_forecast_unchanged(book):
    """Dönem sonu / Planlama Harcanabilir'den bağımsız: aynı veriyle aynı rakamlar."""
    before = finance.plan(3)["months"]
    exp = book.get("/api/summary", params={"month": finance.current_period()}).json()["expected"]
    _nw(book)
    assert finance.plan(3)["months"] == before
    assert book.get("/api/summary", params={"month": finance.current_period()}).json()["expected"] == exp


def test_capability(client):
    assert "spendable_pending_cards" in client.get("/api/meta").json()["capabilities"]


# ---------------------------------------------------------------- maaş gelince (spendable_after_income)

def _salary(client, amount, day=None, desc="Maaş"):
    """Bu dönemin henüz girilmemiş sabit geliri (Özet'in "bekliyor"u)."""
    d = day or 28
    r = client.post("/api/recurring", json={"description": desc, "amount": amount, "kind": "gelir", "category": "Maaş",
                                            "day_of_month": d, "start_month": finance.add_months(finance.current_period(), -2)})
    assert r.status_code == 200, r.text


def _pending_income(client):
    return client.get("/api/summary", params={"month": finance.current_period()}).json()["pending"]["income"]


def test_after_income_turns_positive(client, tmpdb):
    client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 2000.0})
    _statement(tmpdb)
    _card_entry(client, 900.0)
    _salary(client, 60000.0)
    nw = _nw(client)
    assert nw["spendable"] < 0
    inc = _pending_income(client)
    assert inc == 60000.0
    a = nw["spendable_after_income"]
    assert a["amount"] == round(nw["spendable"] + inc, 2) > 0
    assert a["income"] == inc and a["label"] == "Maaş" and a["count"] == 1 and a["main"]["label"] == "Maaş"
    assert a["date"][:7] <= finance.period_bounds(finance.current_period())[1].isoformat()[:7]


def test_after_income_null_without_expected_income(book):
    assert _nw(book)["spendable_after_income"] is None


def test_after_income_still_negative_and_several_incomes(client, tmpdb):
    client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 1000.0})
    _statement(tmpdb, scale=10)  # kesilmiş ekstre 34.000 + kesilecek 14.000
    _salary(client, 20000.0)
    _salary(client, 5000.0, desc="Kira geliri")
    nw = _nw(client)
    a = nw["spendable_after_income"]
    assert a["income"] == _pending_income(client) == 25000.0
    assert a["amount"] == round(nw["spendable"] + 25000.0, 2) < 0
    assert a["label"] == "Beklenen gelirler" and a["count"] == 2 and a["main"] == {**a["main"], "label": "Maaş", "amount": 20000.0}


# ---------------------------------------------------------------- ortak defter

@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _member(owner):
    from app.api.main import app

    inv = owner.post("/api/book/invites", json={"note": "esim"}).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"code": inv["code"], "username": "esim", "password": "esim-sifre-12345"}).status_code == 200
    return m


def test_member_view_follows_hidden_cards(owner, tmpdb):
    _account(owner)
    _statement(tmpdb)
    _statement(tmpdb, card=CARD_B, bank="Kuveyt Türk")
    _card_entry(owner, 900.0)
    m = _member(owner)
    own = _nw(owner)
    assert own["pending_cards"] == round(2 * (INST + SUB) + 900.0, 2)
    assert _nw(m)["pending_cards"] == own["pending_cards"] and _nw(m)["spendable"] == own["spendable"]

    assert owner.put("/api/book/rules", json={"hidden_cards": [CARD_B]}).status_code == 200
    mine = _nw(m)
    # gizli kartın ne kesilmiş ekstresi ne kesilecek kısmı üyede görünür; sahibin rakamı aynı
    assert mine["pending_cards"] == INST + SUB + 900.0
    assert not any("9602" in x["title"] for x in mine["pending_cards_items"])
    assert mine["spendable"] == _spendable_from_parts(mine)
    assert _nw(owner)["pending_cards"] == own["pending_cards"]
