"""Borç ödemesi "Ödendi" sayfası (lead 16.11): ödemenin notu (yetenek debt_payment_note) ve hesaplı ödemede çapa günü
sorusu (yetenek debt_anchor_conflict). Kullanıcı ÖNCE hesap bakiyesini bugüne günceller, SONRA aynı gün o hesaptan borç
öder: in_balance true → ödeme o bakiyede (düşmez), false → sonra yapıldı (düşer), yoksa eski kural (çapa günü: düşmez).
Kişiler, hesaplar ve tutarlar uydurmadır."""
from datetime import date

import pytest

from app import finance, version
from tests.test_gercek_gun_tekrari import freeze

TODAY = date(2026, 11, 16)


@pytest.fixture()
def book(client, tmpdb, monkeypatch):
    freeze(monkeypatch, TODAY)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 10000.0}).status_code == 200
    with tmpdb.connect() as c:
        aid = c.execute("SELECT id FROM accounts WHERE name = 'Örnek Banka'").fetchone()[0]
    ids = {}
    for person, direction in (("Örnek Komşu", "borc"), ("Örnek Arkadaş", "alacak")):
        r = client.post("/api/debts", json={"person": person, "direction": direction, "asset": "TRY", "amount": 1000.0,
                                            "debt_date": "2026-10-01"})
        assert r.status_code == 200, r.text
        ids[direction] = r.json()["id"]
    return {"aid": aid, **ids}


def _cur(aid):
    return next(a["current"] for a in finance.net_worth(TODAY)["accounts"] if a["id"] == aid)


def test_capabilities():
    caps = version.handshake()["capabilities"]
    assert "debt_payment_note" in caps and "debt_anchor_conflict" in caps


def test_note_saved_and_listed(client, book):
    r = client.post(f"/api/debts/{book['borc']}/pay", json={"paid_at": TODAY.isoformat(), "account_id": None,
                                                           "note": "  eşim nakit verdi "})
    assert r.status_code == 200, r.text
    assert "anchor_conflict" not in r.json()  # hesap yok: soru yok
    d = next(x for x in client.get("/api/debts").json() if x["id"] == book["borc"])
    assert d["settled_at"] and [p["note"] for p in d["payments"]] == ["eşim nakit verdi"]
    assert _cur(book["aid"]) == 10000.0  # hesaptan değil: bakiye değişmez


def test_note_too_long_rejected(client, book):
    r = client.post(f"/api/debts/{book['borc']}/pay", json={"paid_at": TODAY.isoformat(), "note": "x" * 201})
    assert r.status_code == 422


@pytest.mark.parametrize("direction,in_balance,expect", [
    ("borc", True, 10000.0), ("borc", False, 9000.0), ("borc", None, 10000.0),
    ("alacak", True, 10000.0), ("alacak", False, 11000.0), ("alacak", None, 10000.0),
])
def test_same_day_after_balance_update(client, book, direction, in_balance, expect):
    body = {"amount": 1000.0, "paid_at": TODAY.isoformat(), "account_id": book["aid"]}
    if in_balance is not None:
        body["in_balance"] = in_balance
    r = client.post(f"/api/debts/{book[direction]}/pay", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["anchor_conflict"] == {"account_id": book["aid"], "name": "Örnek Banka", "as_of": TODAY.isoformat()}
    assert _cur(book["aid"]) == expect
    # Geri al: tek ödeme silinir, bakiye eski hâline döner
    assert client.delete(f"/api/debt-payments/{r.json()['payment_id']}").json()["deleted"] is True
    assert _cur(book["aid"]) == 10000.0


def test_after_anchor_day_no_question(client, book, monkeypatch):
    """Ödeme bakiye gününden sonra: soru yok, eski kural (düşer)."""
    freeze(monkeypatch, date(2026, 11, 18))
    r = client.post(f"/api/debts/{book['borc']}/pay", json={"amount": 400.0, "paid_at": "2026-11-17", "account_id": book["aid"],
                                                           "in_balance": True})
    assert r.status_code == 200 and "anchor_conflict" not in r.json()
    assert next(a["current"] for a in finance.net_worth(date(2026, 11, 18))["accounts"] if a["id"] == book["aid"]) == 9600.0


@pytest.mark.parametrize("day,in_anchor,after", [
    ("2026-11-17", None, True), ("2026-11-17", 1, False), ("2026-11-17", 0, True),
    ("2026-11-16", None, False), ("2026-11-16", 1, False), ("2026-11-16", 0, True),
    ("2026-11-15", None, False), ("2026-11-15", 0, True),
])
def test_debt_after_anchor_rule(day, in_anchor, after):
    """card_payments.in_anchor ile aynı kural; cevapsız satırda eski davranış (yalnız çapa gününden sonrası)."""
    from app import db

    assert db.debt_after_anchor({"date": day, "in_anchor": in_anchor}, "2026-11-16") is after
    assert db.debt_after_anchor({"date": day}, "2026-11-16") is (day > "2026-11-16")
