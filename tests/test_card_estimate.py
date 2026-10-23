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
