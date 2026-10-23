def test_installment_entry_spread_over_months(client, tmpdb):
    r = client.post("/api/entries", json={"entry_date": "2026-10-23", "description": "H&M", "amount": 849.99,
                                          "kind": "gider", "payment": "kredi_karti", "installments": 3, "category": "Giyim"})
    assert r.status_code == 200
    m = tmpdb.period_of("2026-10-23")
    from app import finance

    months = [m, finance.add_months(m, 1), finance.add_months(m, 2)]
    got = []
    for mo in months:
        rows = [x for x in client.get("/api/transactions", params={"month": mo}).json() if x["description"] == "H&M"]
        assert len(rows) == 1
        got.append((rows[0]["amount"], rows[0]["installment"]))
    assert got == [(283.33, "1/3"), (283.33, "2/3"), (283.33, "3/3")]
    assert not [x for x in client.get("/api/transactions", params={"month": finance.add_months(m, 3)}).json()
                if x["description"] == "H&M"]
    # tek çekime çevrilince tek parça
    eid = next(x for x in client.get("/api/transactions", params={"month": m}).json() if x["description"] == "H&M")["id"]
    client.patch(f"/api/entries/{eid}", json={"installments": 1})
    rows = [x for x in client.get("/api/transactions", params={"month": m}).json() if x["description"] == "H&M"]
    assert [(x["amount"], x["installment"]) for x in rows] == [(849.99, None)]


def test_installment_parts_rounding(tmpdb):
    parts = tmpdb.installment_parts({"installments": 3, "payment": "kredi_karti", "kind": "gider", "month": "2026-11",
                                     "amount": 100.0})
    assert parts == [(0, "2026-11", 33.33), (1, "2026-12", 33.33), (2, "2027-01", 33.34)]
