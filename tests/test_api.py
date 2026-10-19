def test_entries_and_recurring_flow(client):
    r = client.post("/api/recurring", json={"description": "Maaş", "amount": 80000, "kind": "gelir",
                                            "category": "Maaş", "start_month": "2026-01"})
    rid = r.json()["id"]
    client.post("/api/recurring", json={"description": "Kira", "amount": 20000, "kind": "gider",
                                        "category": "Kira & Aidat", "start_month": "2026-01"})
    client.post("/api/entries", json={"entry_date": "2026-10-10", "description": "Psikolog", "amount": 3000,
                                      "kind": "gider", "category": "Sağlık"})

    s = client.get("/api/summary", params={"month": "2026-10"}).json()
    assert s["totals"]["manual_expense"] == 3000
    assert s["pending"]["count"] == 2  # maaş + kira onay bekliyor

    client.post(f"/api/recurring/{rid}/confirm", json={"month": "2026-10", "amount": 82000})
    s = client.get("/api/summary", params={"month": "2026-10"}).json()
    assert s["totals"]["income"] == 82000 and s["pending"]["count"] == 1

    rows = client.get("/api/transactions", params={"month": "2026-10"}).json()
    psik = next(r for r in rows if r["description"] == "Psikolog")
    client.patch(f"/api/entries/{psik['id']}", json={"amount": 3500})
    client.delete(f"/api/entries/{psik['id']}")
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["manual_expense"] == 0
    assert len(client.get("/api/plan", params={"months": 3}).json()["months"]) == 3


def test_upload_and_edit_fee(client, upload):
    res = upload()
    assert all(r["status"] == "saved" and r["reconciliation_diff"] == 0 and not r["ai"] for r in res)
    assert upload(["vakifbank_a.txt"])[0]["status"] == "duplicate"

    rows = client.get("/api/transactions", params={"month": "2026-10"}).json()
    fees = [r for r in rows if r["kind"] == "faiz_ucret" and r["account"] == "5400***9012"]
    before = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["fees"]
    client.patch(f"/api/card-transactions/{fees[0]['id']}", json={"txn_type": "harcama", "category": "Diğer"})  # faiz değil
    client.patch(f"/api/card-transactions/{fees[1]['id']}", json={"is_deleted": True})                     # sil
    after = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["fees"]
    assert round(before - after, 2) == round(fees[0]["amount"] + fees[1]["amount"], 2)
    client.patch(f"/api/card-transactions/{fees[1]['id']}", json={"is_deleted": False})                    # geri al
    assert any(r["id"] == fees[1]["id"] for r in client.get("/api/transactions", params={"month": "2026-10"}).json())


def test_unknown_bank_without_ai(client, tmp_path, monkeypatch):
    import app.parsers as parsers

    monkeypatch.setattr(parsers, "extract_text", lambda data: "BAŞKA BANKA EKSTRESİ")
    res = client.post("/api/statements/upload", files=[("files", ("x.pdf", b"%PDF", "application/pdf"))]).json()
    assert res[0]["status"] == "error" and "Banka tanınamadı" in res[0]["message"]


def test_credit_card_entry_links_to_statement(client, upload):
    # Ekstre gelmeden fişten girildi: 14.09 USTA TESİSAT 2.750 (vakifbank_b'de var)
    r = client.post("/api/entries", json={"entry_date": "2026-09-13", "description": "Usta tesisat", "amount": 2750,
                                          "kind": "gider", "category": "Ev & Tamirat", "note": "mutfak lavabo",
                                          "payment": "kredi_karti"}).json()
    nakit = client.post("/api/entries", json={"entry_date": "2026-09-13", "description": "Pazar", "amount": 2750,
                                              "kind": "gider", "payment": "nakit"}).json()
    assert r["linked"] is False
    assert client.get("/api/summary", params={"month": "2026-09"}).json()["totals"]["manual_expense"] == 5500

    res = upload()
    assert sum(x["linked"] for x in res) == 1  # sadece kredi kartı olan eşleşir
    assert client.get("/api/summary", params={"month": "2026-09"}).json()["totals"]["manual_expense"] == 2750

    oct_rows = client.get("/api/transactions", params={"month": "2026-10"}).json()
    card = next(x for x in oct_rows if x["source"] == "card" and "TESİSAT" in x["description"])
    assert card["category"] == "Ev & Tamirat" and "mutfak lavabo" in card["note"]

    client.post(f"/api/entries/{r['id']}/link", json={"txn_id": None})
    cands = client.get(f"/api/entries/{r['id']}/candidates").json()
    assert cands and cands[0]["id"] == card["id"]
    client.post(f"/api/entries/{r['id']}/link", json={"txn_id": card["id"]})
    assert client.post(f"/api/entries/{nakit['id']}/link", json={"txn_id": card["id"]}).status_code == 409


def test_duplicate_income_in_period_warns(client, tmpdb):
    from datetime import date

    from app import finance

    tmpdb.set_cycle_start_day(17)
    for d in ("2026-10-15", "2026-09-19"):  # ikisi de 17 Eyl – 16 Eki dönemine düşer
        client.post("/api/entries", json={"entry_date": d, "description": "Maaş", "amount": 1000, "kind": "gelir"})
    ins = finance.insights("2026-10", today=date(2026, 10, 19))
    assert any("Maaş" in i["title"] and "2 kez" in i["title"] for i in ins)


def test_recurring_counted_once_per_period(client, tmpdb):
    """Maaş günü ay dönümüne yakın/öbür tarafta olsa da her dönemde tek kez sayılır."""
    tmpdb.set_cycle_start_day(17)
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 1000, "kind": "gelir",
                                              "day_of_month": 15, "start_month": "2026-01"}).json()["id"]
    client.post(f"/api/recurring/{rid}/confirm", json={"month": "2026-10"})
    # geçmiş maaşlar yanlış günle (19) girildi: yine de kendi aylarına bağlanır, Ekim'e çift düşmez
    for d in ("2026-09-19", "2026-08-19", "2026-07-19"):
        client.post("/api/entries", json={"entry_date": d, "description": "maaş", "amount": 1000, "kind": "gelir"})
    months = sorted(e["month"] for e in tmpdb.list_entries())
    assert months == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert all(e["recurring_id"] == rid for e in tmpdb.list_entries())
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["income"] == 1000


def test_occurrence_when_salary_after_cycle_day(client, tmpdb):
    """Maaş 20'sinde, ekstre 2'sinde kesiliyor (ay dönümü 3): Kasım dönemi maaşı 20 Ekim."""
    tmpdb.set_cycle_start_day(3)
    assert tmpdb.occurrence_date("2026-11", 20) == "2026-10-20"
    assert tmpdb.occurrence_date("2026-11", 1) == "2026-11-01"
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 1000, "kind": "gelir",
                                              "day_of_month": 20, "start_month": "2026-01"}).json()["id"]
    rows = client.get("/api/transactions", params={"month": "2026-11"}).json()
    assert next(r for r in rows if r.get("pending"))["date"] == "2026-10-20"
    # hafta sonu yüzünden 18 Ekim'de erken yattı → yine Kasım dönemi, tek kez
    client.post("/api/entries", json={"entry_date": "2026-10-18", "description": "Maaş", "amount": 1000, "kind": "gelir"})
    s = client.get("/api/summary", params={"month": "2026-11"}).json()
    assert s["totals"]["income"] == 1000 and s["pending"]["count"] == 0
