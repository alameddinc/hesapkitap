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
    # F1.3: bankalar gibi artan kuruş İLK taksitte (eskiden son taksitteydi: 33,33 + 33,33 + 33,34)
    assert parts == [(0, "2026-11", 33.34), (1, "2026-12", 33.33), (2, "2027-01", 33.33)]


def test_installment_parts_bank_method_keeps_total(tmpdb):
    from app import money

    def amounts(total, n):
        return [a for _, _, a in tmpdb.installment_parts({"installments": n, "payment": "kredi_karti", "kind": "gider",
                                                           "month": "2026-12", "amount": total})]

    assert amounts(100, 6) == [16.70, 16.66, 16.66, 16.66, 16.66, 16.66]
    assert amounts(2499.99, 9) == [277.83] + [277.77] * 8
    assert amounts(849.99, 3) == [283.33] * 3  # tam bölünen: değişmez
    assert amounts(0.05, 6) == [0.05, 0, 0, 0, 0, 0]
    for total, n in [(100, 6), (2499.99, 9), (1000, 12), (0.1 + 0.2, 4), (12345.67, 18), (7, 3)]:
        parts = amounts(total, n)
        assert money.kurus(sum(parts)) == money.kurus(total), (total, n)
        assert parts[0] >= max(parts[1:]) and len(set(parts[1:])) == 1  # fark yalnız ilk taksitte
    # ay geçişi: Aralık'tan başlayan taksit yıl atlar
    assert [m for _, m, _ in tmpdb.installment_parts({"installments": 3, "payment": "kredi_karti", "kind": "gider",
                                                      "month": "2026-12", "amount": 30})] == ["2026-12", "2027-01", "2027-02"]
