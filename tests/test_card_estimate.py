def _payment(c, d, amt, last4):
    c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account, description) VALUES (?, ?, ?, 'VakıfBank', 'x')",
              (d, amt, last4))


def _statement(c, period_end, due, total, card="6501***6613"):
    c.execute("INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt) "
              "VALUES (?, 'x.pdf', 'VakıfBank', ?, ?, ?, ?)", (f"h{period_end}", card, period_end, due, total))


def test_payments_before_first_statement_are_estimated(client, tmpdb):
    with tmpdb.connect() as c:
        _statement(c, "2025-11-15", "2025-11-25", 56780.98)
        _payment(c, "2025-10-18", 70699.92, "6613")   # ilk ekstreden eski: arşiv
        _payment(c, "2025-11-17", 56780.98, "6613")   # ekstresi var
        _payment(c, "2026-01-17", 83047.52, "6613")   # gerçek boşluk (Aralık/Ocak ekstresi yok)
    miss = tmpdb.card_payments_without_statement()
    assert [(x["pay_date"], x["before_first"]) for x in miss] == [("2025-10-18", True), ("2026-01-17", False)]
    m = tmpdb.period_of("2025-10-18")
    rows = [r for r in client.get("/api/transactions", params={"month": m}).json() if r["source"] == "estimate"]
    assert [(r["amount"], r["category"]) for r in rows] == [(70699.92, "Kart harcaması (ekstresiz)")]
    from app import finance

    titles = [i["title"] for i in finance.insights(tmpdb.period_of("2026-01-17"))]
    assert any("1 ödemenin ekstresi yüklenmemiş" in t for t in titles)  # sadece gerçek boşluk uyarılır
    assert not any("2 ödemenin" in t for t in titles)


def test_interest_alarm_explains_late_payment(client, tmpdb):
    from app import finance

    with tmpdb.connect() as c:
        _statement(c, "2026-08-15", "2026-08-25", 38450.82)
        _statement(c, "2026-09-15", "2026-09-25", 37125.32)
        _payment(c, "2026-08-26", 38450.82, "6613")  # bir gün geç
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
    trend = {t["month"]: t["income"] for t in finance.summary("2026-02")["trend"]}
    assert trend["2026-02"] == 200000 and trend["2026-01"] == 210000  # grafikte de tahmin var


def test_no_pending_salary_when_actual_exists(client, tmpdb):
    from app import finance

    client.post("/api/recurring", json={"description": "Maaş", "amount": 200000, "kind": "gelir", "day_of_month": 15,
                                        "start_month": "2025-12"})
    tmpdb.create_entry({"entry_date": "2026-01-15", "description": "Maaş ödemesi", "amount": 210000, "kind": "gelir",
                        "category": "Maaş"})
    jan = [r for r in finance.transactions("2026-01") if r["kind"] == "gelir"]
    assert [(r["amount"], bool(r.get("pending"))) for r in jan] == [(210000, False)]


def test_advance_pay_raise_difference(tmpdb):
    """Memur: maaş 15'inde peşin; ocak zammının 1–14 günlük farkı ocak maaşına eklenir."""
    rid = tmpdb.create_recurring({"description": "Maaş", "amount": 0, "kind": "gelir", "day_of_month": 15,
                                  "start_month": "2025-12", "gross_amount": 100000})
    tmpdb.add_salary_change(rid, {"from_month": "2026-01", "gross": 120000, "reason": "zam"})
    r = dict(next(x for x in tmpdb.list_recurring() if x["id"] == rid))
    plain = tmpdb.planned_amount(r, "2026-01")
    tmpdb.update_recurring(rid, {"advance_pay": True})
    r = dict(next(x for x in tmpdb.list_recurring() if x["id"] == rid))
    jan, feb = tmpdb.planned_amount(r, "2026-01"), tmpdb.planned_amount(r, "2026-02")
    assert jan > plain and jan > feb  # ocakta fark var, şubatta yok
    g, _ = tmpdb.gross_schedule(r, 2026, tmpdb.salary_changes(rid))
    assert g[1] == round(120000 + 20000 * 14 / 30, 2) and g[2] == 120000


def test_card_debt_at_uses_latest_statement(tmpdb):
    with tmpdb.connect() as c:
        _statement(c, "2026-07-15", "2026-07-25", 50000)   # ödeme kaydı yok
        _statement(c, "2026-08-15", "2026-08-25", 60000)   # eskinin ödenmeyeni bunun içinde
    assert tmpdb.card_debt_at("2026-08-20") == 60000
    assert tmpdb.card_debt_at("2026-07-20") == 50000
    with tmpdb.connect() as c:
        c.execute("UPDATE statements SET paid_at = '2026-08-24', paid_amount = 60000 WHERE period_end = '2026-08-15'")
    assert tmpdb.card_debt_at("2026-08-30") == 0
