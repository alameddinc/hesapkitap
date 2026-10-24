"""Yüklemeyi geri al: yüklemenin yaptığı her şey (yeni satırlar, güncellemeler, bakiye) tersine döner."""
from tests.conftest import fixture_bytes
from tests.test_bank_import import CSV, _upload


def _snapshot(db):
    with db.connect() as c:
        return {t: [tuple(r) for r in c.execute(f'SELECT * FROM "{t}" ORDER BY rowid')]
                for t in ("entries", "statements", "transactions", "own_transfers", "card_payments", "accounts", "recurring")}


def test_undo_bank_import_restores_everything(client, tmpdb):
    client.post("/api/recurring", json={"description": "Maaş", "amount": 85000, "kind": "gelir", "category": "Maaş",
                                        "day_of_month": 15, "start_month": "2026-01"})
    client.post("/api/entries", json={"entry_date": "2026-10-19", "description": "Psikolog", "amount": 3000,
                                      "kind": "gider", "category": "Sağlık", "payment": "havale"})
    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                  "VALUES ('k', 'VakıfBank', '4543***1234', '2026-10-13', '2026-10-23', 12500)")
    before = _snapshot(tmpdb)
    pv = _upload(client, CSV.encode("utf-8"), "hareket.csv").json()
    r = _upload(client, CSV.encode("utf-8"), "hareket.csv", pv["mapping"], pv["header_row"]).json()
    assert r["gelir"] == 1 and r["odendi_isaretlenen"] == 1
    assert _snapshot(tmpdb) != before

    imports = client.get("/api/imports").json()
    assert imports[0]["label"].startswith("Hesap dökümü") and imports[0]["counts"]["entries"]["added"] >= 2
    # yüklemeden sonra elle eklenen kayıt geri almada kaybolmaz
    client.post("/api/entries", json={"entry_date": "2026-10-21", "description": "Ekmek", "amount": 20,
                                      "kind": "gider", "category": "Market & Gıda", "payment": "nakit"})
    res = client.post(f"/api/imports/{imports[0]['id']}/undo").json()
    assert res["ok"]
    after = _snapshot(tmpdb)
    assert [e for e in after["entries"] if e[3] != "Ekmek"] == before["entries"]  # psikolog bağlantısı da çözüldü
    for t in ("statements", "own_transfers", "card_payments", "recurring"):
        assert after[t] == before[t], t
    assert client.get("/api/imports").json()[0]["undone_at"]
    # tekrar yüklenebilir (parmak izleri silindi)
    again = _upload(client, CSV.encode("utf-8"), "hareket.csv", pv["mapping"], pv["header_row"]).json()
    assert again["tekrar"] == 0 and again["gelir"] == 1


def test_undo_statement_upload_and_conflict(client, tmpdb):
    before = _snapshot(tmpdb)
    res = client.post("/api/statements/upload", files=[("files", ("a.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))]).json()
    assert res[0]["status"] == "saved"
    bid = client.get("/api/imports").json()[0]["id"]
    with tmpdb.connect() as c:  # yüklemeden sonra bir işlemin kategorisi elle değişti
        c.execute("UPDATE transactions SET category = 'Elle', category_manual = 1 WHERE id = (SELECT MIN(id) FROM transactions)")
    r = client.post(f"/api/imports/{bid}/undo").json()
    assert not r["ok"] and r["conflicts"]
    assert client.post(f"/api/imports/{bid}/undo?force=true").json()["ok"]
    assert _snapshot(tmpdb)["statements"] == before["statements"] == []
    assert _snapshot(tmpdb)["transactions"] == []
    assert client.post("/api/statements/upload", files=[("files", ("a.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))]).json()[0]["status"] == "saved"


def test_duplicate_upload_records_no_batch(client, tmpdb):
    f = [("files", ("a.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))]
    client.post("/api/statements/upload", files=f)
    n = len(client.get("/api/imports").json())
    assert client.post("/api/statements/upload", files=f).json()[0]["status"] == "duplicate"
    assert len(client.get("/api/imports").json()) == n


def test_failed_upload_partial_writes_are_undoable(tmpdb):
    from app import undo

    try:
        with undo.batch("Bozuk"):
            tmpdb.create_entry({"entry_date": "2026-10-01", "description": "yarım", "amount": 5, "kind": "gider"})
            raise RuntimeError("okuma hatası")
    except RuntimeError:
        pass
    b = undo.list_batches()[0]
    assert b["label"] == "Bozuk" and b["counts"]["entries"]["added"] == 1
    assert undo.undo(b["id"])["ok"]
    assert not tmpdb.list_entries()
