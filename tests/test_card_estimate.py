def _payment(c, d, amt, last4):
    c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account, description) VALUES (?, ?, ?, 'VakıfBank', 'x')",
              (d, amt, last4))


def _statement(c, period_end, due, total, card="4111***1111"):
    c.execute("INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt) "
              "VALUES (?, 'x.pdf', 'VakıfBank', ?, ?, ?, ?)", (f"h{period_end}", card, period_end, due, total))


def test_payments_before_first_statement_are_estimated(client, tmpdb):
    with tmpdb.connect() as c:
        _statement(c, "2025-11-15", "2025-11-25", 41000.00)
        _payment(c, "2025-10-18", 52000.00, "1111")   # ilk ekstreden eski: arşiv
        _payment(c, "2025-11-17", 41000.00, "1111")   # ekstresi var
        _payment(c, "2026-01-17", 61500.00, "1111")   # gerçek boşluk (Aralık/Ocak ekstresi yok)
    miss = tmpdb.card_payments_without_statement()
    assert [(x["pay_date"], x["before_first"]) for x in miss] == [("2025-10-18", True), ("2026-01-17", False)]
    # Eski beklenti: ilk ekstreden eski ödeme o ayın "Kart harcaması (ekstresiz)" gideriydi. Yeni kural: tahmini satır
    # gerçek harcamaya girmez; ilk ekstreden eski (bankanın arşivi) ödemeler ise hiçbir yerde gösterilmez.
    m = tmpdb.period_of("2025-10-18")
    rows = client.get("/api/transactions", params={"month": m, "include_estimates": 1}).json()
    assert not [r for r in rows if r["source"] == "estimate"]
    assert client.get("/api/summary", params={"month": m}).json()["totals"]["estimated_expense"] == 0
    gap = tmpdb.period_of("2026-01-17")
    assert not [r for r in client.get("/api/transactions", params={"month": gap}).json() if r.get("estimated")]
    est = [r for r in client.get("/api/transactions", params={"month": gap, "include_estimates": 1}).json() if r.get("estimated")]
    assert [(r["amount"], r["category"], r["source"]) for r in est] == [(61500.00, "Kart harcaması (ekstresiz)", "estimate")]
    from app import finance

    titles = [i["title"] for i in finance.insights(tmpdb.period_of("2026-01-17"))]
    assert any("1 ödemenin ekstresi yüklenmemiş" in t for t in titles)  # sadece gerçek boşluk uyarılır
    assert not any("2 ödemenin" in t for t in titles)


def test_interest_alarm_explains_late_payment(client, tmpdb):
    from app import finance

    with tmpdb.connect() as c:
        _statement(c, "2026-08-15", "2026-08-25", 33000.00)
        _statement(c, "2026-09-15", "2026-09-25", 34000.00)
        _payment(c, "2026-08-26", 33000.00, "1111")  # bir gün geç
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
    # Kullanıcı kararı (30.10): ayrılan yalnız ekstresiz KART ÖDEMESİ tahmini (gider şişmesin). Dökümü olmayan ayın
    # maaş tahmini gerçek gelire sayılmaya devam eder (elle giren / döküm yüklemeyen kullanıcıda aylar gelirsiz kalmasın).
    feb = [r for r in finance.transactions("2026-02") if r["kind"] == "gelir"]
    assert [(r["amount"], bool(r.get("estimated")), r.get("salary_estimate")) for r in feb] == [(200000, False, True)]
    jan = [r for r in finance.transactions("2026-01") if r["kind"] == "gelir"]
    assert [(r["amount"], r.get("salary_estimate")) for r in jan] == [(210000, None)]  # gerçek maaş var: tahmin yok
    s = finance.summary("2026-02")
    assert s["totals"]["income"] == 200000 and s["totals"]["estimated_income"] == 0
    assert s["estimated_items"] == []
    trend = {t["month"]: t for t in s["trend"]}
    assert trend["2026-02"]["income"] == 200000 and trend["2026-01"]["income"] == 210000


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


# ---------------------------------------------------------------- tahmini satırlar gerçek toplamlara girmez

def _purchase(c, period_end, d, desc, amount, category="Market"):
    sid = c.execute("SELECT id FROM statements WHERE period_end = ?", (period_end,)).fetchone()[0]
    c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
              "VALUES (?, ?, ?, ?, 'harcama', ?)", (sid, d, desc, amount, category))


def _gap_setup(tmpdb):
    """Eylül ekstresi var; 18 Eki'de karta 47.000 ödendi ama Ekim ekstresi (kesim 15 Eki) yüklenmedi.
    Ekim'de gerçek gider: elle girilen 1.000 TL market."""
    with tmpdb.connect() as c:
        _statement(c, "2026-09-15", "2026-09-25", 30000)
        _purchase(c, "2026-09-15", "2026-09-01", "MIGROS", 30000)
        _payment(c, "2026-09-24", 30000, "1111")   # Eylül ekstresi ödendi: tahmin yok
        _payment(c, "2026-10-18", 47000, "1111")   # Ekim ekstresi yok: tahmini
    tmpdb.create_entry({"entry_date": "2026-10-05", "description": "Bakkal", "amount": 1000, "kind": "gider", "category": "Market"})


def test_card_payment_without_statement_is_not_spending(client, tmpdb):
    from datetime import date

    from app import finance

    _gap_setup(tmpdb)
    tmpdb.set_budget("Market", 5000)
    tmpdb.set_budget(finance.CARD_ESTIMATE_CATEGORY, 100)
    s = client.get("/api/summary", params={"month": "2026-10"}).json()
    t = s["totals"]
    assert t["expense"] == 1000 and t["manual_expense"] == 1000  # harcadığın = gerçek giderler
    assert t["estimated_expense"] == 47000 and t["estimated_income"] == 0
    assert [(i["source"], i["kind"], i["amount"], i["date"], i["card_last4"], i["expected_period"], i["card"])
            for i in s["estimated_items"]] == [("card_payment", "gider", 47000, "2026-10-18", "1111", "2026-10", "VakıfBank …1111")]
    assert [c["name"] for c in s["by_category"]] == ["Market"]  # kategori dağılımında tahmin yok
    assert all(m["name"] != "VakıfBank …1111" for m in s["by_merchant"])
    assert {b["category"]: b["spent"] for b in s["budgets"]} == {"Market": 1000, finance.CARD_ESTIMATE_CATEGORY: 0}
    oct_ = next(x for x in s["trend"] if x["month"] == "2026-10")
    assert oct_["expense"] == 1000 and oct_["estimated_expense"] == 47000
    assert next(x for x in s["trend"] if x["month"] == "2026-09")["estimated_expense"] == 0

    # işlem listesi: varsayılanda yok, include_estimates=1 ile estimated işaretli gelir
    rows = client.get("/api/transactions", params={"month": "2026-10"}).json()
    assert not [r for r in rows if r.get("estimated") or r["source"] == "estimate"]
    with_est = client.get("/api/transactions", params={"month": "2026-10", "include_estimates": True}).json()
    assert [(r["amount"], r["estimated"]) for r in with_est if r.get("estimated")] == [(47000, True)]
    assert len(with_est) == len(rows) + 1
    assert not [r for r in client.get("/api/transactions").json() if r.get("estimated")]  # tüm aylar da

    # istatistik (yıllık), kıyas, gün dökümü, dönem özeti
    y = client.get("/api/year-summary", params={"year": 2026}).json()
    assert y["expense"] == 31000 and y["estimated_expense"] == 47000
    assert all(c["name"] != finance.CARD_ESTIMATE_CATEGORY for c in y["categories"])
    assert {m["month"]: (m["expense"], m["estimated_expense"]) for m in y["per_month"]} == {
        "2026-09": (30000, 0), "2026-10": (1000, 47000)}
    cmp = finance.compare("2026-10", variable_only=False, today=date(2026, 10, 30))
    assert cmp["at_day"]["current"] == 1000 and cmp["estimated_expense"]["current"] == 47000
    assert finance.day_detail("2026-10-18")["total"] == 0
    d = finance.month_digest("2026-10", today=date(2026, 11, 2))
    assert d["expense"] == 1000 and d["estimated_expense"] == 47000 and d["estimated_count"] == 1
    assert all(x["name"] != finance.CARD_ESTIMATE_CATEGORY for x in d["top"])
    rep = finance.period_report("2026-10")
    assert "Gider ₺1.000" in rep and "tahmini ~₺47.000" in rep
    # Planlama / harcanabilir / ödemeler kart borcunu ekstreden ayrıca hesaplar: tahmini satırdan etkilenmez
    assert all(l["description"] != "VakıfBank …1111 ödemesi (ekstre yok, tahmini harcama)"
               for m in finance.plan(2, today=date(2026, 10, 30))["months"] for l in m["lines"])


def test_estimate_disappears_when_statement_arrives(client, tmpdb):
    _gap_setup(tmpdb)
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["estimated_expense"] == 47000
    with tmpdb.connect() as c:  # Ekim ekstresi yüklendi
        _statement(c, "2026-10-15", "2026-10-25", 47000)
        _purchase(c, "2026-10-15", "2026-10-02", "A101", 17000)
        _purchase(c, "2026-10-15", "2026-10-09", "SHELL", 30000, "Akaryakıt")
    s = client.get("/api/summary", params={"month": "2026-10"}).json()
    assert s["totals"]["estimated_expense"] == 0 and s["estimated_items"] == []
    assert s["totals"]["expense"] == 1000 + 47000  # gerçek harcamalar geldi
    assert {c["name"]: c["amount"] for c in s["by_category"]} == {"Market": 18000, "Akaryakıt": 30000}
    assert not [r for r in client.get("/api/transactions", params={"month": "2026-10", "include_estimates": 1}).json()
                if r.get("estimated")]
