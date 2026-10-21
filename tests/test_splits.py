"""Alt kalemler (bölme) ve kalem kuralları."""
from app import db, finance


def _entry(client, amount=520, category="Market & Gıda", **kw):
    body = {"entry_date": "2026-10-21", "description": "Bakkal", "amount": amount, "kind": "gider", "category": category,
            "payment": "nakit", **kw}
    return client.post("/api/entries", json=body).json()


def test_default_rule_matches_brands_not_lookalikes(tmpdb):
    assert db.match_item("MARLBORO TOUCH BLUE") == "Sigara"
    assert db.match_item("Kent Switch") == "Sigara"
    assert db.match_item("Parliament Night Blue x2") == "Sigara"
    assert db.match_item("KENTUCKY CIPS") is None   # 'kent' kelime sınırıyla aranır
    assert db.match_item("Ülker Çikolata") is None


def test_receipt_proposal_by_rule_and_ai_then_confirmed(client, tmpdb):
    client.post("/api/item-rules", json={"keywords": "bira, efes", "category": "Alkol"})
    items = [{"name": "MARLBORO TOUCH", "qty": 2, "amount": 220},
             {"name": "MRLB RED", "qty": 1, "amount": 110, "split": "Sigara"},   # kısaltma: model etiketi
             {"name": "EFES PILSEN 50CL", "qty": 1, "amount": 60},
             {"name": "Ülker Çikolata", "qty": 1, "amount": 130, "split": "Uydurma"}]
    pr = client.post("/api/splits/propose", json={"items": items, "category": "Market & Gıda", "total": 520}).json()
    assert sorted((p["category"], p["amount"]) for p in pr["parts"]) == [("Alkol", 60), ("Sigara", 330)]
    assert tmpdb.list_entries() == []          # öneri kaydetmez
    # kullanıcı Alkol'ü kaldırıp onaylar
    r = _entry(client, splits=[p for p in pr["parts"] if p["category"] == "Sigara"])
    assert [(s["category"], s["amount"]) for s in r["splits"]] == [("Sigara", 330)]
    m = tmpdb.list_entries()[0]["month"]
    by = {x["category"]: x["amount"] for x in finance.transactions(m) if x["description"].startswith("Bakkal")}
    assert by == {"Market & Gıda": 190, "Sigara": 330}
    t = client.get("/api/summary", params={"month": m}).json()
    assert t["totals"]["expense"] == 520


def test_whole_receipt_one_category_suggests_category(client, tmpdb):
    pr = client.post("/api/splits/propose", json={"items": [{"name": "Parliament", "qty": 2, "amount": 220}],
                                                  "category": "Market & Gıda", "total": 220}).json()
    assert pr == {"parts": [], "category": "Sigara"}


def test_too_large_confirmed_split_rejected_without_orphan(client, tmpdb):
    r = client.post("/api/entries", json={"entry_date": "2026-10-21", "description": "Bakkal", "amount": 100, "kind": "gider",
                                          "category": "Market & Gıda", "splits": [{"amount": 300, "category": "Sigara"}]})
    assert r.status_code == 422 and tmpdb.list_entries() == []


def test_manual_split_and_limits(client, tmpdb):
    eid = _entry(client)["id"]
    assert client.put(f"/api/splits/entry/{eid}", json={"parts": [{"amount": 600, "category": "Sigara"}]}).status_code == 422
    assert client.put(f"/api/splits/entry/{eid}", json={"parts": [{"amount": 220, "category": "Sigara"}]}).status_code == 200
    assert client.patch(f"/api/entries/{eid}", json={"amount": 100}).status_code == 422
    client.put(f"/api/splits/entry/{eid}", json={"parts": []})   # bölmeyi kaldır
    assert finance.transactions(tmpdb.list_entries()[0]["month"])[0]["amount"] == 520


def test_split_follows_receipt_to_card_transaction(client, tmpdb):
    import hashlib

    eid = _entry(client, payment="kredi_karti", amount=120, splits=[{"amount": 110, "category": "Sigara"}])["id"]
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


def test_shared_expense_part_becomes_receivable(client, tmpdb):
    # 900 TL yemek; 300'ü Ali'nin payı → giderim 600, Ali'ye 300 alacak; Ali havale edince kapanır
    eid = client.post("/api/entries", json={"entry_date": "2026-10-21", "description": "Restoran", "amount": 900,
                                            "kind": "gider", "category": "Kafe & Restoran", "payment": "nakit"}).json()["id"]
    r = client.put(f"/api/splits/entry/{eid}", json={"parts": [{"amount": 300, "person": "Ali Örnek"}]})
    assert r.status_code == 200
    m = tmpdb.list_entries()[0]["month"]
    t = client.get("/api/summary", params={"month": m}).json()["totals"]
    assert t["expense"] == 600 and t["debt_flow"] == -300 and t["receivable"] == 300 and t["cash_net"] == -900
    d = tmpdb.list_debts()[0]
    assert (d["person"], d["direction"], d["amount"], d["split_id"] is not None) == ("Ali Örnek", "alacak", 300, True)
    # Ali havale etti
    tmpdb.import_bank_rows([{"date": "2026-10-23", "amount": 300.0, "type": "gelir", "hash": "ali",
                             "description": "Gelen FAST GİDEN FAST - ALİ ÖRNEK TARAFINDAN GÖNDERİLEN"}], owners=["AYŞE DEMIR"])
    t = client.get("/api/summary", params={"month": m}).json()["totals"]
    assert t["income"] == 0 and t["receivable"] == 0 and t["cash_net"] == -600
    # tahsil edilmiş payı silmek engellenir
    assert client.put(f"/api/splits/entry/{eid}", json={"parts": []}).status_code == 422


def test_removing_unpaid_share_deletes_receivable(client, tmpdb):
    eid = client.post("/api/entries", json={"entry_date": "2026-10-21", "description": "Market", "amount": 400,
                                            "kind": "gider", "category": "Market & Gıda", "payment": "nakit"}).json()["id"]
    client.put(f"/api/splits/entry/{eid}", json={"parts": [{"amount": 100, "person": "Veli"}]})
    client.put(f"/api/splits/entry/{eid}", json={"parts": [{"amount": 150, "person": "Veli"}]})   # payı güncelle
    assert [(d["amount"]) for d in tmpdb.list_debts()] == [150]
    client.put(f"/api/splits/entry/{eid}", json={"parts": []})
    assert tmpdb.list_debts() == []


def test_shares_at_creation_and_partial_transfer(client, tmpdb):
    # 1200 ödedim: Ali 300, Veli 200 → benim giderim 700; Ali 250 havale etti → 50 kalır
    r = client.post("/api/entries", json={"entry_date": "2026-10-21", "description": "Yemek", "amount": 1200, "kind": "gider",
                                          "category": "Kafe & Restoran", "payment": "nakit",
                                          "splits": [{"amount": 300, "person": "Ali Örnek"}, {"amount": 200, "person": "Veli Örnek"}]})
    assert r.status_code == 200
    m = tmpdb.list_entries()[0]["month"]
    t = client.get("/api/summary", params={"month": m}).json()["totals"]
    assert t["expense"] == 700 and t["receivable"] == 500
    tmpdb.import_bank_rows([{"date": "2026-10-23", "amount": 250.0, "type": "gelir", "hash": "a1",
                             "description": "Gelen FAST GİDEN FAST - ALİ ÖRNEK TARAFINDAN GÖNDERİLEN"}], owners=["AYŞE DEMIR"])
    ali = next(d for d in tmpdb.list_debts() if d["person"] == "Ali Örnek")
    assert ali["remaining"] == 50 and ali["settled_at"] is None
    t = client.get("/api/summary", params={"month": m}).json()["totals"]
    assert t["income"] == 0                                         # havale gelir sayılmaz, tahsilat
