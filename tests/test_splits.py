"""Alt kalemler (bölme) ve kalem kuralları."""
from app import db, finance


def _entry(client, amount=520, category="Market & Gıda", items=None, **kw):
    return client.post("/api/entries", json={"entry_date": "2026-10-21", "description": "Bakkal", "amount": amount,
                                             "kind": "gider", "category": category, "payment": "nakit",
                                             "items": items, **kw}).json()


def test_default_rule_matches_brands_not_lookalikes(tmpdb):
    assert db.match_item("MARLBORO TOUCH BLUE") == "Sigara"
    assert db.match_item("Kent Switch") == "Sigara"
    assert db.match_item("Parliament Night Blue x2") == "Sigara"
    assert db.match_item("KENTUCKY CIPS") is None   # 'kent' kelime sınırıyla aranır
    assert db.match_item("Ülker Çikolata") is None


def test_receipt_items_split_by_rule_and_ai(client, tmpdb):
    client.post("/api/item-rules", json={"keywords": "bira, efes", "category": "Alkol"})
    r = _entry(client, items=[{"name": "MARLBORO TOUCH", "qty": 2, "amount": 220},
                              {"name": "MRLB RED", "qty": 1, "amount": 110, "split": "Sigara"},   # kısaltma: model etiketi
                              {"name": "EFES PILSEN 50CL", "qty": 1, "amount": 60},
                              {"name": "Ülker Çikolata", "qty": 1, "amount": 130, "split": "Uydurma"}])
    assert sorted((s["category"], s["amount"]) for s in r["splits"]) == [("Alkol", 60), ("Sigara", 330)]
    m = tmpdb.list_entries()[0]["month"]
    rows = [x for x in finance.transactions(m) if x["description"].startswith("Bakkal")]
    by = {x["category"]: x["amount"] for x in rows}
    assert by == {"Market & Gıda": 130, "Sigara": 330, "Alkol": 60}
    t = client.get("/api/summary", params={"month": m}).json()
    assert t["totals"]["expense"] == 520
    assert {c["name"]: c["amount"] for c in t["by_category"]}["Sigara"] == 330


def test_whole_receipt_one_category_changes_category(client, tmpdb):
    r = _entry(client, amount=220, items=[{"name": "Parliament", "qty": 2, "amount": 220}])
    assert r["splits"] == []
    assert next(e for e in tmpdb.list_entries() if e["id"] == r["id"])["category"] == "Sigara"


def test_manual_split_and_limits(client, tmpdb):
    eid = _entry(client)["id"]
    assert client.put(f"/api/splits/entry/{eid}", json={"parts": [{"amount": 600, "category": "Sigara"}]}).status_code == 422
    assert client.put(f"/api/splits/entry/{eid}", json={"parts": [{"amount": 220, "category": "Sigara"}]}).status_code == 200
    assert client.patch(f"/api/entries/{eid}", json={"amount": 100}).status_code == 422
    client.put(f"/api/splits/entry/{eid}", json={"parts": []})   # bölmeyi kaldır
    assert finance.transactions(tmpdb.list_entries()[0]["month"])[0]["amount"] == 520


def test_split_follows_receipt_to_card_transaction(client, tmpdb):
    import hashlib

    eid = _entry(client, payment="kredi_karti", items=[{"name": "Marlboro", "qty": 1, "amount": 110},
                                                        {"name": "Su", "qty": 1, "amount": 10}], amount=120)["id"]
    with tmpdb.connect() as c:
        sid = c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                        "VALUES (?, 'B', '1111***2222', '2026-11-10', '2026-11-20', 120)",
                        (hashlib.sha1(b"x").hexdigest(),)).lastrowid
        tid = c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
                        "VALUES (?, '2026-10-21', 'BAKKAL', 120, 'harcama', 'Market & Gıda')", (sid,)).lastrowid
        c.execute("UPDATE entries SET linked_txn_id = ? WHERE id = ?", (tid, eid))
    rows = finance.transactions("2026-11")
    card = {x["category"]: x["amount"] for x in rows if x["source"] == "card"}
    assert card == {"Market & Gıda": 10, "Sigara": 110}
    assert finance.summary("2026-11")["totals"]["expense"] == 120   # fiş kaydı eşleştiği için sayılmaz
