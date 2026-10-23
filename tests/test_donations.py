def test_charity_rule():
    from app.categorize import categorize
    from app import db

    with db.connect() as c:
        rules = db.load_rules(c)
    assert categorize("TURKIYE DIYANET VAKFI ISTANBUL", "harcama", rules) == "Bağış & Sadaka"
    assert categorize("KIZILAY BAGIS", "harcama", rules) == "Bağış & Sadaka"


def test_manual_donation_and_marking(client, tmpdb):
    # nakitten verilen sadaka: gider olarak da yazılır
    r = client.post("/api/donations", json={"kind": "sadaka", "amount": 200, "given_date": "2026-10-20", "as_expense": True})
    assert r.status_code == 200
    rows = [x for x in client.get("/api/transactions", params={"month": tmpdb.period_of("2026-10-20")}).json()
            if x["category"] == "Bağış & Sadaka"]
    assert [x["amount"] for x in rows] == [200]
    # dökümden gelen bağış: bekleyenler listesinde
    eid = tmpdb.create_entry({"entry_date": "2026-10-21", "description": "KIZILAY", "amount": 500, "kind": "gider",
                              "category": "Bağış & Sadaka"})
    s = client.get("/api/donations").json()
    assert [p["id"] for p in s["pending"]] == [eid]
    # zekât olarak işaretle → zekât yılına ödeme
    me = next(p for p in client.get("/api/zakat").json()["people"] if p["is_self"])["id"]
    assert client.post("/api/donations/mark", json={"source": "entry", "id": eid, "kind": "zekat"}).status_code == 422  # yıl yok
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-03-01"})
    assert client.post("/api/donations/mark", json={"source": "entry", "id": eid, "kind": "zekat"}).status_code == 200
    s = client.get("/api/donations").json()
    assert not s["pending"] and s["zakat_payments"][0]["amount"] == 500
    assert client.get(f"/api/donations/mark/entry/{eid}").json()["mark"]["kind"] == "zekat"
    # sadakaya çevir: zekât ödemesi kalkar
    client.post("/api/donations/mark", json={"source": "entry", "id": eid, "kind": "bagis", "recipient": "Kızılay"})
    s = client.get("/api/donations").json()
    assert not s["zakat_payments"] and {i["kind"] for i in s["items"]} == {"sadaka", "bagis"}
    assert s["years"][0]["sadaka"] == 700
