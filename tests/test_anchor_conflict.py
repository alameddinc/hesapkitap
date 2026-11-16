"""Çapa günü çakışması (lead 16.11, canlı simülasyon): kullanıcı ÖNCE hesap bakiyesini bugünün gerçeğine günceller,
SONRA aynı gün o hesaptan kart ödemesi / döngü ödemesi / aktarım girer. in_balance: true → ödeme o bakiyede (düşmez),
false → sonra yapıldı (düşer); yoksa eski kural (aynı gün kayıt sırası: düşer). Kişiler, kartlar ve tutarlar uydurmadır."""
from datetime import date

import pytest

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction
from tests.test_finans_cekismeli import cut_cycle  # noqa: F401  (fixture)
from tests.test_gercek_gun_tekrari import freeze

TODAY = date(2026, 11, 16)
CARD = "5400***0002"


@pytest.fixture()
def book(client, tmpdb, monkeypatch):
    freeze(monkeypatch, TODAY)
    tmpdb.set_cycle_start_day(17)
    for n, b in (("VakıfBank", 50000.0), ("Örnek Banka", 20000.0)):
        assert client.post("/api/accounts", json={"name": n, "kind": "banka", "balance": b}).status_code == 200
    with tmpdb.connect() as c:
        ids = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM accounts")}
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=date(2026, 11, 13), due_date=date(2026, 11, 23),
                  total_debt=10000.0, min_payment=2000.0, credit_limit=90000, period_activity=10000.0,
                  next_period_end=date(2026, 12, 13), next_due_date=date(2026, 12, 23))
    s.transactions = [Transaction(txn_date=date(2026, 11, 5), description="ORNEK MARKET", amount=10000.0,
                                  txn_type=PURCHASE, card=CARD)]
    tmpdb.save_statement(b"st-anchor", "e.pdf", s)
    sid = next(x["id"] for x in tmpdb.list_statements())
    return {"v": ids["VakıfBank"], "o": ids["Örnek Banka"], "sid": sid}


def _cur(aid):
    return next(a["current"] for a in finance.net_worth(TODAY)["accounts"] if a["id"] == aid)


@pytest.mark.parametrize("in_balance,expect", [(True, 50000.0), (False, 40000.0), (None, 40000.0)])
def test_statement_pay_after_balance_update(client, book, in_balance, expect):
    body = {"amount": 10000.0, "paid_at": TODAY.isoformat(), "account_id": book["v"]}
    if in_balance is not None:
        body["in_balance"] = in_balance
    r = client.post(f"/api/statements/{book['sid']}/pay", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["anchor_conflict"] == {"account_id": book["v"], "name": "VakıfBank", "as_of": TODAY.isoformat()}
    assert _cur(book["v"]) == expect


def test_no_conflict_when_payment_after_anchor(client, book):
    r = client.post(f"/api/statements/{book['sid']}/pay",
                    json={"amount": 10000.0, "paid_at": "2026-11-17", "account_id": book["v"]})
    assert r.status_code == 200 and "anchor_conflict" not in r.json()


def test_earlier_day_payment_false_is_deducted(client, book):
    """Ödeme bakiye gününden ÖNCE ama kullanıcı 'sonra yaptım' (bakiyede yok) diyor → düşülür; alan yoksa içinde."""
    r = client.post(f"/api/statements/{book['sid']}/pay",
                    json={"amount": 10000.0, "paid_at": "2026-11-14", "account_id": book["v"], "in_balance": False})
    assert r.status_code == 200 and r.json()["anchor_conflict"]["as_of"] == TODAY.isoformat()
    assert _cur(book["v"]) == 40000.0
    client.request("DELETE", f"/api/statements/{book['sid']}/pay")
    r = client.post(f"/api/statements/{book['sid']}/pay",
                    json={"amount": 10000.0, "paid_at": "2026-11-14", "account_id": book["v"]})
    assert _cur(book["v"]) == 50000.0


@pytest.mark.parametrize("in_balance,v,o", [(True, 50000.0, 20000.0), (False, 45000.0, 25000.0)])
def test_own_transfer_after_balance_update(client, book, in_balance, v, o):
    r = client.post("/api/own-transfers", json={"date": TODAY.isoformat(), "amount": 5000.0, "from_account_id": book["v"],
                                                "to_account_id": book["o"], "in_balance": in_balance})
    assert r.status_code == 200, r.text
    assert {c["account_id"] for c in r.json()["anchor_conflicts"]} == {book["v"], book["o"]}
    assert (_cur(book["v"]), _cur(book["o"])) == (v, o)
    n = finance.net_worth(TODAY)
    assert n["cash"] == round(v + o, 2)


@pytest.mark.parametrize("in_balance,expect", [(True, 0.0), (False, -5000.0)])
def test_cycle_pay_after_balance_update(client, tmpdb, cut_cycle, in_balance, expect):
    """Döngü ödemesi: hesabın bakiyesi bugün güncellendi, sonra aynı gün hesaplı döngü ödemesi."""
    from tests.test_finans_cekismeli import A0, CARD as C2

    assert client.patch(f"/api/accounts/{cut_cycle}", json={"balance": A0, "as_of": TODAY.isoformat()}).status_code == 200
    r = client.post("/api/cards/cycle-pay", json={"card_no": C2, "amount": 5000.0, "paid_at": TODAY.isoformat(),
                                                   "account_id": cut_cycle, "in_balance": in_balance})
    assert r.status_code == 200, r.text
    assert r.json()["anchor_conflict"]["account_id"] == cut_cycle
    assert _cur(cut_cycle) == A0 + expect
