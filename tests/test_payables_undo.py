"""Ödemeler: "Ödendi" → "Geri al" tekrarı listeyi çoğaltmaz; kısmi ödeme (asgari / başka tutar) kalanı bırakır.

Sahada görülen: aynı bankanın iki kartı, aynı gün son ödeme; sonraki ekstrelerin tahmini de aynı gün. Tahminin id'si yok,
istemcinin liste anahtarı (tür-id-tarih) iki tahminde aynı çıkıyordu ve birkaç Ödendi/Geri al sonrası satırlar üst üste
birikti. Sunucu her kaleme kararlı, benzersiz `key` verir (yetenek payables_key). Tutarlar ve kart numaraları uydurmadır."""
from datetime import date

import pytest

from app.parsers.base import PURCHASE, Statement, Transaction

TODAY = date(2026, 11, 9)
A, B = "5100***1111", "5200***2222"


def _st(card, total, min_pay):
    s = Statement(bank="Örnek Bank", card_no=card, period_end=date(2026, 11, 8), due_date=date(2026, 11, 18),
                  total_debt=total, min_payment=min_pay, credit_limit=50000, previous_balance=0.0,
                  period_activity=total, payments=0.0, next_period_end=date(2026, 12, 8), next_due_date=date(2026, 12, 18))
    s.transactions = [Transaction(txn_date=date(2026, 11, 1), description="ORNEK MARKET", amount=total, txn_type=PURCHASE, card=card)]
    return s


@pytest.fixture()
def two_cards(tmpdb):
    """İki kart, aynı kesim ve son ödeme; ikisinin de sonraki ekstresi tahmini (aynı gün)."""
    tmpdb.save_statement(b"a", "a.pdf", _st(A, 12000.0, 2400.0))
    tmpdb.save_statement(b"b", "b.pdf", _st(B, 6000.0, 1200.0))
    return {s["card_no"]: s["id"] for s in tmpdb.list_statements()}


def _items():
    from app import finance

    return finance.payables(TODAY)


def _shape(items):
    return sorted((p["type"], p["title"], p["status"], p["amount"], p.get("paid_amount")) for p in items)


def test_keys_unique_and_one_estimate_per_card(two_cards):
    items = _items()
    keys = [p["key"] for p in items]
    assert len(keys) == len(set(keys))
    est = [p for p in items if p["type"] == "estimate"]
    assert sorted(p["title"] for p in est) == [f"Örnek Bank {A}", f"Örnek Bank {B}"]
    # eski istemci anahtarı (tür-id-tarih) bu iki kalemde çakışıyordu: sunucunun anahtarı ayırır
    assert len({f"{p['type']}-{p['id']}-{p['due_date']}" for p in est}) == 1
    assert len({p["key"] for p in est}) == 2
    assert {p["key"] for p in items if p["type"] == "statement"} == {f"statement:{i}" for i in two_cards.values()}


def test_mark_paid_undo_repeated_is_idempotent(client, tmpdb, two_cards):
    from app import finance

    before = _shape(_items())
    sid = two_cards[A]
    for _ in range(8):
        assert client.post(f"/api/statements/{sid}/pay", json={"amount": 12000.0, "paid_at": "2026-11-09"}).status_code == 200
        paid = _items()
        assert next(p for p in paid if p["key"] == f"statement:{sid}")["status"] == "odendi"
        assert len(paid) == len(before) and len({p["key"] for p in paid}) == len(paid)
        assert sum(p["type"] == "estimate" for p in paid) == 2
        assert client.delete(f"/api/statements/{sid}/pay").status_code == 200
        assert client.delete(f"/api/statements/{sid}/pay").status_code == 200  # çift dokunuş da zararsız
        assert _shape(_items()) == before
    with tmpdb.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM card_payments").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    assert finance.payables_totals(TODAY)["open"] == 18000.0


def test_mark_paid_with_account_undo_repeated(client, tmpdb, two_cards):
    client.post("/api/accounts", json={"name": "Örnek Hesap", "kind": "banka", "balance": 50000, "as_of": "2026-11-01"})
    aid = next(a["id"] for a in client.get("/api/accounts").json()["accounts"] if a["name"] == "Örnek Hesap")
    sid = two_cards[A]
    for _ in range(5):
        client.post(f"/api/statements/{sid}/pay", json={"amount": 12000.0, "paid_at": "2026-11-09", "account_id": aid})
        client.post(f"/api/statements/{sid}/pay", json={"amount": 12000.0, "paid_at": "2026-11-09", "account_id": aid})  # tekrar
        with tmpdb.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM card_payments WHERE statement_id = ?", (sid,)).fetchone()[0] == 1
        client.delete(f"/api/statements/{sid}/pay")
        with tmpdb.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM card_payments").fetchone()[0] == 0
    assert next(a["current"] for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid) == 50000


def test_payables_api_has_keys(client, two_cards):
    r = client.get("/api/payables", params={"totals": 1}).json()
    assert all(isinstance(p["key"], str) and p["key"] for p in r["items"])
    assert "payables_key" in client.get("/api/meta").json()["capabilities"]


def test_partial_payment_keeps_remaining(client, tmpdb, two_cards):
    """Asgari ödendi: kalan ödenecekler listesinde ve toplamda; net durum / harcanabilir kalanı sayar; takvim kalanı gösterir."""
    from app import finance

    sid = two_cards[A]
    client.post("/api/accounts", json={"name": "Örnek Hesap", "kind": "banka", "balance": 50000, "as_of": "2026-11-01"})
    aid = next(a["id"] for a in client.get("/api/accounts").json()["accounts"] if a["name"] == "Örnek Hesap")
    client.post(f"/api/statements/{sid}/pay", json={"amount": 2400.0, "paid_at": "2026-11-09", "account_id": aid})
    p = next(x for x in _items() if x["key"] == f"statement:{sid}")
    assert p["status"] == "kismi" and p["paid_amount"] == 2400.0 and p["in_total"] is True
    t = finance.payables_totals(TODAY)
    assert t["open"] == 9600.0 + 6000.0 and t["card_debt"] == 15600.0
    nw = finance.net_worth(TODAY)
    assert nw["card_debt"] == 15600.0 and nw["cash"] == 50000 - 2400.0  # hesaptan yalnız ödenen çıktı
    assert nw["spendable"] == pytest.approx(nw["cash"] - 15600.0 - nw["payable"] - nw["reserved"] - nw["zakat_due"])
    due = next(d for d in finance.plan(3, today=TODAY)["dues"] if d["account"] == A and not d["estimated"])
    assert due["amount"] == 9600.0 and due["total_debt"] == 12000.0 and due["paid_amount"] == 2400.0
    # kalanı da ödendi (toplam ödenen = borç): kapanır
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12000.0, "paid_at": "2026-11-10", "account_id": aid})
    assert next(x for x in _items() if x["key"] == f"statement:{sid}")["status"] == "odendi"
    assert not any(d["account"] == A and not d["estimated"] for d in finance.plan(3, today=TODAY)["dues"])


def test_partial_payment_matches_bank_line_of_same_amount(client, tmpdb, two_cards):
    """Kısmi ödeme hesaptan girildi; banka dökümündeki aynı tutarlı kart ödemesi satırı ona bağlanır (çift sayılmaz)."""
    from app import bank_import

    sid = two_cards[A]
    client.post("/api/accounts", json={"name": "Örnek Hesap", "kind": "banka", "balance": 50000, "as_of": "2026-11-01"})
    aid = next(a["id"] for a in client.get("/api/accounts").json()["accounts"] if a["name"] == "Örnek Hesap")
    client.post(f"/api/statements/{sid}/pay", json={"amount": 3000.0, "paid_at": "2026-11-09", "account_id": aid})
    row = {"date": "10.11.2026", "description": "KREDI KARTI BORC ODEMESI 5100****1111", "amount": -3000.0, "balance": 47000.0}
    st = tmpdb.import_bank_rows(bank_import.finalize([row], []), account="Örnek Hesap", owners=[], account_id=aid)
    assert st["kart_odemesi"] == 1 and st["eslesen_elle"] == 1
    with tmpdb.connect() as c:
        cps = [dict(r) for r in c.execute("SELECT * FROM card_payments")]
    assert len(cps) == 1 and cps[0]["statement_id"] == sid and cps[0]["amount"] == 3000.0 and cps[0]["import_hash"]
    p = next(x for x in _items() if x["key"] == f"statement:{sid}")
    assert p["status"] == "kismi" and p["paid_amount"] == 3000.0


def test_partial_payment_without_account_bank_line_recognized(client, tmpdb, two_cards):
    """Hesap seçmeden kısmi ödeme: dökümdeki aynı tutar (açıklamada 'kart' geçmese de) kart ödemesi sayılır, gider olmaz."""
    from app import bank_import

    sid = two_cards[A]
    client.post("/api/accounts", json={"name": "Örnek Hesap", "kind": "banka", "balance": 50000, "as_of": "2026-11-01"})
    aid = next(a["id"] for a in client.get("/api/accounts").json()["accounts"] if a["name"] == "Örnek Hesap")
    client.post(f"/api/statements/{sid}/pay", json={"amount": 3456.78, "paid_at": "2026-11-09"})
    row = {"date": "10.11.2026", "description": "FAST GIDEN ORNEK BANK", "amount": -3456.78, "balance": 46543.22}
    st = tmpdb.import_bank_rows(bank_import.finalize([row], []), account="Örnek Hesap", owners=[], account_id=aid)
    assert st["kart_odemesi"] == 1 and st["gider"] == 0
    p = next(x for x in _items() if x["key"] == f"statement:{sid}")
    assert p["status"] == "kismi" and p["paid_amount"] == 3456.78
