"""Kart ekstresini hesaptan ödeme: POST /api/statements/{id}/pay {account_id}. Gider sayılmaz, bakiye düşer; döküm
sonradan (ya da önceden) gelince çift sayılmaz; geri alınca bağlı elle kayıt silinir."""
from app import bank_import

ROW = {"date": "17.10.2026", "description": "KREDI KARTI BORC ODEMESI 4543****1234", "amount": -12500.0, "balance": 37500.0}


def _setup(client, tmpdb):
    client.post("/api/accounts", json={"name": "Örnek Bank", "kind": "banka", "balance": 50000, "as_of": "2026-10-01"})
    acc = next(a for a in client.get("/api/accounts").json()["accounts"] if a["name"] == "Örnek Bank")
    with tmpdb.connect() as c:
        sid = c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                        "VALUES ('k', 'VakıfBank', '4543***1234', '2026-10-13', '2026-10-23', 12500)").lastrowid
    return acc["id"], sid


def _current(client, aid):
    return next(a["current"] for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid)


def _gider(tmpdb):
    with tmpdb.connect() as c:
        return c.execute("SELECT COALESCE(SUM(amount), 0) FROM entries WHERE kind = 'gider'").fetchone()[0]


def _import(tmpdb, aid):
    return tmpdb.import_bank_rows(bank_import.finalize([ROW], []), account="Örnek Bank", owners=[], account_id=aid)


def _cps(tmpdb):
    with tmpdb.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM card_payments")]


def test_manual_then_statement_import(client, tmpdb):
    aid, sid = _setup(client, tmpdb)
    r = client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": "2026-10-16", "account_id": aid}).json()
    assert r["ok"] and r["card_payment_id"] and not r["linked"]
    assert _current(client, aid) == 37500 and _gider(tmpdb) == 0  # bakiye düştü, gider değil
    st = _import(tmpdb, aid)
    assert st["kart_odemesi"] == 1 and st["eslesen_elle"] == 1
    cps = _cps(tmpdb)
    assert len(cps) == 1 and cps[0]["import_hash"] and cps[0]["pay_date"] == "2026-10-17" and cps[0]["statement_id"] == sid
    assert tmpdb.list_statements()[0]["paid_at"] == "2026-10-17"
    assert _current(client, aid) == 37500  # döküm bakiyesi: çift düşmez
    # geri alma: dökümden gelen kayıt kalır, yalnız bağ kopar
    client.delete(f"/api/statements/{sid}/pay")
    cps = _cps(tmpdb)
    assert len(cps) == 1 and cps[0]["statement_id"] is None and tmpdb.list_statements()[0]["paid_amount"] is None


def test_statement_import_then_manual(client, tmpdb):
    aid, sid = _setup(client, tmpdb)
    _import(tmpdb, aid)  # döküm ekstreyi zaten ödendi işaretler
    r = client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": "2026-10-16", "account_id": aid}).json()
    assert r["linked"] and len(_cps(tmpdb)) == 1 and _cps(tmpdb)[0]["statement_id"] == sid
    assert _current(client, aid) == 37500


def test_undo_deletes_manual_payment_and_restores_balance(client, tmpdb):
    aid, sid = _setup(client, tmpdb)
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": "2026-10-16", "account_id": aid})
    assert _current(client, aid) == 37500
    assert client.delete(f"/api/statements/{sid}/pay").status_code == 200
    assert _cps(tmpdb) == [] and _current(client, aid) == 50000


def test_without_account_unchanged_and_bad_account(client, tmpdb):
    aid, sid = _setup(client, tmpdb)
    r = client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": "2026-10-16"}).json()
    assert r == {"ok": True, "card_payment_id": None, "linked": False} and _cps(tmpdb) == []
    assert _current(client, aid) == 50000
    assert client.post(f"/api/statements/{sid}/pay", json={"amount": 1, "paid_at": "2026-10-16", "account_id": 999}).status_code == 422
    assert client.post("/api/statements/999/pay", json={"amount": 1, "paid_at": "2026-10-16"}).status_code == 404
