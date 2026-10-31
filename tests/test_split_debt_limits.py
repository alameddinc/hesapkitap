"""Bölünmüş harcamada eksi gider ve borçta fazla ödeme (denetim A4, Ek A F3.8 / F3.9, değişmez I6 / I7).
Kişiler ve tutarlar uydurmadır."""
from app import finance


def _split_entry(client, amount=1000, parts=((400, "Sigara"), (500, "Alkol"))):
    r = client.post("/api/entries", json={"entry_date": "2026-10-21", "description": "Bakkal", "amount": amount,
                                          "kind": "gider", "category": "Market & Gıda", "payment": "havale",
                                          "splits": [{"amount": a, "category": c} for a, c in parts]})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _rows(month="2026-10"):
    return [x for x in finance.transactions(month) if x["description"].startswith("Bakkal")]


def test_patch_amount_below_splits_is_422(client, tmpdb):
    eid = _split_entry(client)
    r = client.patch(f"/api/entries/{eid}", json={"amount": 800})
    assert r.status_code == 422 and "₺900,00" in r.json()["detail"] and "bölmeyi düzelt" in r.json()["detail"]
    assert client.patch(f"/api/entries/{eid}", json={"amount": 900}).status_code == 200


def test_bank_amount_below_splits_scales_parts(client, tmpdb):
    """Elle 1.000 (400 + 500 bölünmüş), bankada 990 geldi: ana satır eksiye düşmez, alt kalemler orantılı küçülür."""
    eid = _split_entry(client, amount=1000, parts=((600, "Sigara"), (400, "Alkol")))
    tmpdb.import_bank_rows([{"date": "2026-10-21", "description": "POS BAKKAL ORNEK", "amount": -990.0, "type": "gider",
                             "hash": "pos-1", "balance": None}], account="Halkbank")
    q = next(r for r in client.get("/api/reminders").json() if r["kind"] == "esles" and r["entry_id"] == eid)
    assert client.post(f"/api/entries/{eid}/match", json={"other": q["other"], "same": True}).status_code == 200
    e = next(x for x in tmpdb.list_entries() if x["id"] == eid)
    assert e["amount"] == 990 and e["import_hash"] == "pos-1"
    rows = _rows()
    assert all(r["amount"] >= 0 for r in rows)  # önce ana satır −10
    assert sorted((r["category"], r["amount"]) for r in rows) == [("Alkol", 396.0), ("Market & Gıda", 0.0), ("Sigara", 594.0)]
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["expense"] == 990


def test_display_never_negative_even_if_data_overflows(client, tmpdb):
    """Güvenlik ağı: veride alt kalemler ana tutarı aşsa da (eski kayıt) ekranda eksi satır çıkmaz, toplam ana tutar."""
    eid = _split_entry(client, amount=1000, parts=((600, "Sigara"), (400, "Alkol")))
    with tmpdb.connect() as c:
        c.execute("UPDATE entries SET amount = 800 WHERE id = ?", (eid,))
    rows = _rows()
    assert min(r["amount"] for r in rows) >= 0 and round(sum(r["amount"] for r in rows), 2) == 800
    assert tmpdb.fit_overflowing_splits() == [("entry", eid)]
    with tmpdb.connect() as c:
        assert [r[0] for r in c.execute("SELECT amount FROM splits WHERE entry_id = ? ORDER BY id", (eid,))] == [480, 320]


# --- borçta fazla ödeme ---------------------------------------------------------------------------------------------

def _debt(client, amount=500, asset=None):
    body = {"person": "Ali Örnek", "direction": "alacak", "amount": amount, "debt_date": "2026-10-01"}
    if asset:
        body["asset"] = asset
    return client.post("/api/debts", json=body).json()["id"]


def test_overpayment_rejected(client, tmpdb):
    did = _debt(client)
    r = client.post(f"/api/debts/{did}/pay", json={"amount": 600, "paid_at": "2026-10-05"})
    assert r.status_code == 422 and "Kalan 500,00 TL" in r.json()["detail"]
    assert client.post(f"/api/debts/{did}/pay", json={"amount": 200, "paid_at": "2026-10-05"}).status_code == 200
    r = client.post(f"/api/debts/{did}/pay", json={"amount": 300.01, "paid_at": "2026-10-06"})
    assert r.status_code == 422 and "Kalan 300,00 TL" in r.json()["detail"]
    assert client.post(f"/api/debts/{did}/pay", json={"amount": 300, "paid_at": "2026-10-06"}).status_code == 200
    d = next(x for x in tmpdb.list_debts() if x["id"] == did)
    assert d["settled_at"] == "2026-10-06" and d["paid_amount"] == 500
    r = client.post(f"/api/debts/{did}/pay", json={"paid_at": "2026-10-07"})  # kapanmış kayda "kalanın tamamı"
    assert r.status_code == 422 and "kapanmış" in r.json()["detail"]
    t = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]
    assert t["debt_flow"] == 0  # 500 çıktı, 500 geldi; fazla akış yok


def test_overpayment_gold(client, tmpdb):
    did = _debt(client, amount=2, asset="CEYREK_ALTIN")
    r = client.post(f"/api/debts/{did}/pay", json={"amount": 3, "paid_at": "2026-10-05"})
    assert r.status_code == 422 and "CEYREK_ALTIN" in r.json()["detail"]
    assert client.post(f"/api/debts/{did}/pay", json={"amount": 2, "paid_at": "2026-10-05"}).status_code == 200


def test_debt_amount_not_below_paid(client, tmpdb):
    did = _debt(client)
    client.post(f"/api/debts/{did}/pay", json={"amount": 300, "paid_at": "2026-10-05"})
    r = client.patch(f"/api/debts/{did}", json={"amount": 200})
    assert r.status_code == 422 and "₺300,00" in r.json()["detail"]
    assert client.patch(f"/api/debts/{did}", json={"amount": 300}).status_code == 200


def test_delete_single_debt_payment(client, tmpdb):
    """Geri al: yalnız o ödeme silinir, borç yeniden açılır, bağlı hareket normal gidere döner; idempotent."""
    client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 1000, "as_of": "2026-10-01"})
    aid = next(a["id"] for a in client.get("/api/accounts").json()["accounts"] if a["name"] == "Örnek Banka")
    did = client.post("/api/debts", json={"person": "Ayşe Örnek", "direction": "borc", "amount": 300,
                                          "debt_date": "2026-09-01"}).json()["id"]
    p1 = client.post(f"/api/debts/{did}/pay", json={"amount": 100, "paid_at": "2026-10-02", "account_id": aid}).json()
    assert p1["ok"] and isinstance(p1["payment_id"], int)
    bal = lambda: next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid)["current"]  # noqa: E731
    assert bal() == 900
    eid = client.post("/api/entries", json={"entry_date": "2026-10-03", "description": "Havale", "amount": 200,
                                            "kind": "gider"}).json()["id"]
    p2 = client.post(f"/api/debts/{did}/pay", json={"amount": 200, "paid_at": "2026-10-03", "entry_id": eid}).json()
    debt = lambda: next(d for d in client.get("/api/debts").json() if d["id"] == did)  # noqa: E731
    assert debt()["settled_at"]

    r = client.delete(f"/api/debt-payments/{p2['payment_id']}").json()
    assert r == {"ok": True, "deleted": True, "debt_id": did}
    d = debt()
    assert not d["settled_at"] and d["paid_amount"] == 100  # yeniden açıldı, öbür ödeme duruyor
    with tmpdb.connect() as c:
        assert c.execute("SELECT debt_id FROM entries WHERE id = ?", (eid,)).fetchone()[0] is None
    assert client.delete(f"/api/debt-payments/{p2['payment_id']}").json()["deleted"] is False  # idempotent

    client.delete(f"/api/debt-payments/{p1['payment_id']}")
    assert bal() == 1000 and debt()["paid_amount"] == 0  # hesaptan çıkan ödeme geri geldi
