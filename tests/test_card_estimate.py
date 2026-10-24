def _payment(c, d, amt, last4):
    c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account, description) VALUES (?, ?, ?, 'VakıfBank', 'x')",
              (d, amt, last4))


def _statement(c, period_end, due, total, card="4111********1111"):
    c.execute("INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt) "
              "VALUES (?, 'x.pdf', 'VakıfBank', ?, ?, ?, ?)", (f"h{period_end}", card, period_end, due, total))


def test_payments_before_first_statement_are_estimated(client, tmpdb):
    with tmpdb.connect() as c:
        _statement(c, "2025-11-15", "2025-11-25", 41000.00)
        _payment(c, "2025-10-18", 52000.00, "6613")   # ilk ekstreden eski: arşiv
        _payment(c, "2025-11-17", 41000.00, "6613")   # ekstresi var
        _payment(c, "2026-01-17", 61500.00, "6613")   # gerçek boşluk (Aralık/Ocak ekstresi yok)
    miss = tmpdb.card_payments_without_statement()
    assert [(x["pay_date"], x["before_first"]) for x in miss] == [("2025-10-18", True), ("2026-01-17", False)]
    m = tmpdb.period_of("2025-10-18")
    rows = [r for r in client.get("/api/transactions", params={"month": m}).json() if r["source"] == "estimate"]
    assert [(r["amount"], r["category"]) for r in rows] == [(52000.00, "Kart harcaması (ekstresiz)")]
    from app import finance

    titles = [i["title"] for i in finance.insights(tmpdb.period_of("2026-01-17"))]
    assert any("1 ödemenin ekstresi yüklenmemiş" in t for t in titles)  # sadece gerçek boşluk uyarılır
    assert not any("2 ödemenin" in t for t in titles)


def test_interest_alarm_explains_late_payment(client, tmpdb):
    from app import finance

    with tmpdb.connect() as c:
        _statement(c, "2026-08-15", "2026-08-25", 20000.00)
        _statement(c, "2026-09-15", "2026-09-25", 15540.00)
        _payment(c, "2026-08-26", 20000.00, "6613")  # bir gün geç
        sid = c.execute("SELECT id FROM statements WHERE period_end = '2026-09-15'").fetchone()[0]
        for d, a in (("ALIŞVERİŞ FAİZİ (Oran:3.75)", 538.31), ("GECİKME FAİZİ (Oran:4.05)", 41.53), ("BSMV", 87.0)):
            c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
                      "VALUES (?, '2026-09-15', ?, ?, 'faiz_ucret', 'Banka Faiz & Ücret')", (sid, d, a))
    alarms = finance.interest_alarm("2026-09")
    assert len(alarms) == 1
    a = alarms[0]
    assert "666,84" in a["title"] and "1 gün geç" in a["detail"] and "25.08" in a["detail"] and "26.08" in a["detail"]


def test_interest_history(client, tmpdb):
    test_interest_alarm_explains_late_payment(client, tmpdb)
    h = client.get("/api/interest-history").json()
    assert [m["month"] for m in h["months"]] == ["2026-09"] and h["total"] == 666.84 and h["checked"] == 2


def test_missing_salary_months(client, tmpdb):
    from app import finance

    for d in ("2025-11-15", "2026-07-15"):
        tmpdb.create_entry({"entry_date": d, "description": "Maaş", "amount": 100, "kind": "gelir", "category": "Maaş"})
    ins = [i for i in finance.insights(tmpdb.period_of("2026-07-15")) if "maaş dökümü yok" in i["title"]]
    assert ins and ins[0]["title"].startswith("7 ayda") and "Aralık 2025 – Haziran 2026" in ins[0]["detail"]


def test_salary_estimated_for_months_without_statement(client, tmpdb):
    from app import finance

    client.post("/api/recurring", json={"description": "Maaş", "amount": 200000, "kind": "gelir",
                                        "day_of_month": 15, "start_month": "2025-12"})
    tmpdb.create_entry({"entry_date": "2026-01-15", "description": "Maaş", "amount": 210000, "kind": "gelir", "category": "Maaş"})
    feb = [r for r in finance.transactions("2026-02") if r["kind"] == "gelir"]
    assert [(r["amount"], r.get("estimated"), r.get("pending")) for r in feb] == [(200000, True, False)]
    jan = [r for r in finance.transactions("2026-01") if r["kind"] == "gelir"]
    assert [(r["amount"], r.get("estimated")) for r in jan] == [(210000, None)]  # gerçek maaş var: tahmin yok
    assert finance.summary("2026-02")["totals"]["income"] == 200000


def test_no_pending_salary_when_actual_exists(client, tmpdb):
    from app import finance

    client.post("/api/recurring", json={"description": "Maaş", "amount": 200000, "kind": "gelir", "day_of_month": 15,
                                        "start_month": "2025-12"})
    tmpdb.create_entry({"entry_date": "2026-01-15", "description": "Maaş ödemesi", "amount": 210000, "kind": "gelir",
                        "category": "Maaş"})
    jan = [r for r in finance.transactions("2026-01") if r["kind"] == "gelir"]
    assert [(r["amount"], bool(r.get("pending"))) for r in jan] == [(210000, False)]
