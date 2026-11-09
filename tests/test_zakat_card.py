"""Zekât/sadaka nasıl ödendi: kredi kartıyla verilen hemen gider (ekstre bekleniyor), ekstre gelince kart satırıyla
eşleşip bir kez sayılır, ekstre satırı yeniden "sadaka mı zekât mı?" diye sorulmaz. Tutarlar, kurumlar uydurma."""
from datetime import date

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction

CARD = "4543***1234"


def _me(client):
    me = next(p for p in client.get("/api/zakat").json()["people"] if p["is_self"])["id"]
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-03-01"})
    return me


def _pay(client, me, **kw):
    r = client.post(f"/api/zakat/people/{me}/payments", json={"amount": 2500, "pay_date": "2026-10-20", **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _rows(client, month="2026-10"):
    return [r for r in client.get("/api/transactions", params={"month": month}).json() if r["category"] == "Bağış & Sadaka"]


def _totals(client, month="2026-10"):
    return client.get("/api/summary", params={"month": month}).json()["totals"]


def _statement(tmpdb, lines):
    total = round(sum(a for _, _, a in lines), 2)
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=date(2026, 10, 26), due_date=date(2026, 11, 5),
                  total_debt=total, min_payment=100, credit_limit=50000, previous_balance=0, period_activity=total,
                  payments=0)
    s.transactions = [Transaction(txn_date=d, description=desc, amount=a, txn_type=PURCHASE, card=CARD)
                      for d, desc, a in lines]
    tmpdb.save_statement(b"st-zekat", "e.pdf", s)


def _payments(client):
    return client.get("/api/donations").json()["zakat_payments"]


def test_zakat_by_card_is_expense_now_and_once_after_statement(client, tmpdb):
    me = _me(client)
    _pay(client, me, payment="kredi_karti", card_last4="1234")
    # hemen gider: Özet'te "Kart … (ekstre bekleniyor)" tarafında
    [row] = _rows(client)
    assert row["amount"] == 2500 and row["payment"] == "kredi_karti" and row["awaiting_statement"]
    t = _totals(client)
    assert t["expense"] == 2500 and t["card_pending"] == 2500
    # Planlama / Dönem sonu: bir kez (tek seferlik satır)
    m = next(x for x in finance.plan(2, date(2026, 10, 25))["months"] if x["month"] == "2026-10")
    assert sum(1 for l in m["lines"] if l.get("entry_id") == row["id"]) == 1 and m["expense"] == 2500

    # ekstre geldi: satır elle kayıtla eşleşir, bir kez sayılır (kart satırı)
    _statement(tmpdb, [(date(2026, 10, 20), "ORNEK VAKFI BAGIS", 2500.0), (date(2026, 10, 5), "ORNEK MARKET", 300.0)])
    assert tmpdb.auto_link() == 1
    t = _totals(client)
    assert t["expense"] == 2800 and t["card_pending"] == 0 and t["manual_expense"] == 0
    m = next(x for x in finance.plan(2, date(2026, 10, 25))["months"] if x["month"] == "2026-10")
    assert m["expense"] == 2800 and not any(l.get("entry_id") == row["id"] for l in m["lines"])
    # ekstre satırı "sadaka mı zekât mı?" diye sorulmaz; işareti zekât görünür
    d = client.get("/api/donations").json()
    assert d["pending"] == [] and len(d["zakat_payments"]) == 1
    tid = next(e["linked_txn_id"] for e in tmpdb.list_entries() if e["id"] == row["id"])
    assert client.get(f"/api/donations/mark/card/{tid}").json()["mark"]["kind"] == "zekat"
    # satırı yine de zekât diye işaretlerse ikinci ödeme açılmaz
    assert client.post("/api/donations/mark", json={"source": "card", "id": tid, "kind": "zekat"}).status_code == 200
    assert len(_payments(client)) == 1
    me_row = next(p for p in client.get("/api/zakat").json()["people"] if p["is_self"])
    assert me_row["hawl"]["paid"] == 2500


def test_zakat_payment_edit_and_delete_follow_expense(client, tmpdb):
    me = _me(client)
    pid = _pay(client, me, payment="havale")
    assert [r["amount"] for r in _rows(client)] == [2500]
    assert client.patch(f"/api/zakat/payments/{pid}", json={"amount": 2000, "pay_date": "2026-10-22"}).status_code == 200
    [row] = _rows(client)
    assert row["amount"] == 2000 and row["date"] == "2026-10-22"
    assert client.delete(f"/api/zakat/payments/{pid}").status_code == 200
    assert _rows(client) == [] and _totals(client)["expense"] == 0


def test_zakat_without_payment_keeps_old_behaviour(client, tmpdb):
    me = _me(client)
    _pay(client, me)  # eski istemci / geçmiş yıl: yalnız zekât ödemesi
    assert _rows(client) == [] and len(_payments(client)) == 1
    assert client.post(f"/api/zakat/people/{me}/payments",
                       json={"amount": 10, "payment": "cek"}).status_code == 422


def test_marked_entry_payment_not_deleted_with_mark(client, tmpdb):
    """Dökümden gelip zekât diye işaretlenen kayıt ödemenin açtığı değil: ödeme silinince kayıt kalır."""
    _me(client)
    eid = tmpdb.create_entry({"entry_date": "2026-10-21", "description": "ORNEK VAKFI", "amount": 500, "kind": "gider",
                              "category": "Bağış & Sadaka", "payment": "havale"})
    client.post("/api/donations/mark", json={"source": "entry", "id": eid, "kind": "zekat"})
    pid = _payments(client)[0]["id"]
    assert client.delete(f"/api/zakat/payments/{pid}").status_code == 200
    assert any(e["id"] == eid for e in tmpdb.list_entries())


def test_donation_by_card(client, tmpdb):
    r = client.post("/api/donations", json={"kind": "sadaka", "amount": 150, "given_date": "2026-10-20",
                                            "payment": "kredi_karti"})
    assert r.status_code == 200
    [row] = _rows(client)
    assert row["payment"] == "kredi_karti" and row["awaiting_statement"]
    assert client.delete(f"/api/donations/{r.json()['id']}").status_code == 200
    assert _rows(client) == []
    # eski istemci: as_expense = nakit
    client.post("/api/donations", json={"kind": "sadaka", "amount": 200, "given_date": "2026-10-20", "as_expense": True})
    assert [r["payment"] for r in _rows(client)] == ["nakit"]


def test_unexpensed_zakat_prompt_add_and_dismiss(client, tmpdb):
    """Ödeme şekli sorulmadan kaydedilmiş (eski sürüm) zekât: Zekât ekranı bir kez sorar; veri kendiliğinden taşınmaz."""
    me = _me(client)
    a = _pay(client, me)                                     # eski istemci: gider yok
    b = _pay(client, me, amount=300, pay_date="2026-10-02")  # eski istemci
    c = _pay(client, me, payment="nakit", amount=50)         # gideri var: sorulmaz
    assert {u["id"] for u in client.get("/api/zakat").json()["unexpensed"]} == {a, b}
    assert [r["amount"] for r in _rows(client)] == [50]
    # "Gider olarak ekle" → kartla: hemen ekstre bekleyen gider, ödemenin tarihinde
    r = client.post(f"/api/zakat/payments/{a}/expense", json={"payment": "kredi_karti"}).json()
    assert r["created"] is True
    row = next(x for x in _rows(client) if x["id"] == r["entry_id"])
    assert row["amount"] == 2500 and row["date"] == "2026-10-20" and row["awaiting_statement"]
    assert client.post(f"/api/zakat/payments/{a}/expense", json={"payment": "nakit"}).json()["created"] is False
    # "Gerek yok" → bir daha sorulmaz
    assert client.post(f"/api/zakat/payments/{b}/no-expense").status_code == 200
    assert client.get("/api/zakat").json()["unexpensed"] == []
    # gider "sayılmasın" yapılırsa yeniden sorulur; eklemek aynı kaydı yeniden saydırır (yeni kayıt açmaz)
    eid = next(p for p in _payments(client) if p["id"] == c)["entry_id"]
    tmpdb.update_entry(eid, {"skipped": 1})
    assert [u["id"] for u in client.get("/api/zakat").json()["unexpensed"]] == [c]
    assert client.post(f"/api/zakat/payments/{c}/expense", json={"payment": "nakit"}).json() == {"entry_id": eid, "created": False}
    assert client.get("/api/zakat").json()["unexpensed"] == []
    assert client.post("/api/zakat/payments/999/expense", json={"payment": "nakit"}).status_code == 404


def test_bank_account_and_havale_recipient(client, tmpdb):
    me = _me(client)
    client.post("/api/accounts", json={"name": "Örnek Bank", "kind": "banka", "balance": 10000, "as_of": "2026-10-01"})
    acc = next(a for a in tmpdb.list_accounts() if a["name"] == "Örnek Bank")["id"]
    _pay(client, me, payment="nakit", account_id=acc, amount=1000)   # banka hesabından (banka kartı)
    _pay(client, me, payment="havale", account_id=acc, recipient="Örnek Vakfı", amount=500)
    rows = {r["payment"]: r for r in _rows(client)}
    assert rows["nakit"]["account_id"] == acc and rows["havale"]["account_id"] == acc
    assert rows["havale"]["counterparty"] == "Örnek Vakfı"
    bal = next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == acc)
    assert bal["current"] == 8500  # hesabın bakiyesinden düştü
