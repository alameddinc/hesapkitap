"""Ekstre 'ödendi' eşleşmesi: her ödeme en fazla bir ekstreye, kart no + tarih penceresi + tutar; kısmi ödemeler
toplanır; sonuç yükleme sırasından bağımsız; elle işaret korunur. (Canlı hata: aynı 43.000,00 ödemesi 3 ekstrede,
45.000,00 yanlış ekstrede, 46.000,00 hiç eşleşmemiş.)"""
import itertools

from app import bank_import

CARD = "4111***1111"
STS = [("2026-02-15", "2026-02-25", 61000.00, 2000), ("2026-04-17", "2026-04-27", 62000.00, 2000),
       ("2026-06-15", "2026-06-25", 43000.00, 2000), ("2026-07-17", "2026-07-27", 44000.00, 2000),
       ("2026-08-15", "2026-08-25", 45000.00, 2000), ("2026-09-15", "2026-09-25", 46000.00, 2000)]
PAYS = [("25.06.2026", 43000.00), ("26.08.2026", 45000.00), ("26.09.2026", 46000.00)]


def _statements(db):
    with db.connect() as c:
        for i, (pe, due, total, mn) in enumerate(STS):
            c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt, min_payment) "
                      "VALUES (?, 'VakıfBank', ?, ?, ?, ?, ?)", (f"h{i}", CARD, pe, due, total, mn))
    db.auto_mark_paid()


def _bank(db, pays=PAYS, card=True):
    rows = [{"date": d, "description": f"Kredi Kartı Borç Öde 4111********1111 no'lu Kredi Kartı Borç Ödemesi" if card
             else "FAST Anlık Ödeme kart borcu", "amount": -a, "balance": 100000.0 - i} for i, (d, a) in enumerate(pays)]
    db.import_bank_rows(bank_import.finalize(rows, []), account="VakıfBank", owners=[])


def _paid(db):
    return {s["period_end"]: (s["paid_amount"], s["paid_at"]) for s in db.list_statements()}


EXPECTED = {"2026-02-15": (None, None), "2026-04-17": (None, None), "2026-06-15": (43000.00, "2026-06-25"),
            "2026-07-17": (None, None), "2026-08-15": (45000.00, "2026-08-26"), "2026-09-15": (46000.00, "2026-09-26")}


def test_each_payment_one_statement_any_order(tmpdb):
    for first in ("statements", "bank"):
        with tmpdb.connect() as c:
            for t in ("statements", "card_payments", "entries"):
                c.execute(f"DELETE FROM {t}")
        (_statements(tmpdb), _bank(tmpdb)) if first == "statements" else (_bank(tmpdb), _statements(tmpdb))
        assert _paid(tmpdb) == EXPECTED, first


def test_old_wrong_marks_are_repaired(tmpdb):
    _statements(tmpdb)
    _bank(tmpdb)
    with tmpdb.connect() as c:  # eski algoritmanın bıraktığı işaretler (kaynağı bilinmiyor)
        c.execute("UPDATE statements SET paid_amount = 43000.00, paid_at = '2026-06-25', paid_source = NULL "
                  "WHERE period_end IN ('2026-02-15', '2026-04-17')")
        c.execute("UPDATE statements SET paid_amount = 45000.00, paid_at = '2026-08-26', paid_source = NULL "
                  "WHERE period_end = '2026-07-17'")
        c.execute("UPDATE statements SET paid_amount = NULL, paid_at = NULL, paid_source = NULL WHERE period_end = '2026-09-15'")
        c.execute("UPDATE statements SET paid_amount = 500, paid_at = '2026-03-01', paid_source = NULL "
                  "WHERE period_end = '2026-02-15' AND 0")  # (yok)
    dry = tmpdb.reassign_statement_payments(apply=False)
    assert {ch["period_end"] for ch in dry["changed"]} == {"2026-02-15", "2026-04-17", "2026-07-17", "2026-09-15"}
    assert _paid(tmpdb)["2026-09-15"] == (None, None)  # kuru: yazmadı
    tmpdb.reassign_statement_payments()
    assert _paid(tmpdb) == EXPECTED


def test_manual_mark_kept_and_unknown_old_manual_kept(client, tmpdb):
    _statements(tmpdb)
    sid = next(s["id"] for s in tmpdb.list_statements() if s["period_end"] == "2026-07-17")
    client.post(f"/api/statements/{sid}/pay", json={"amount": 44000.00, "paid_at": "2026-07-25"})
    with tmpdb.connect() as c:  # eski elle işaret (kaynak yok, hiçbir ödemeye denk gelmiyor)
        c.execute("UPDATE statements SET paid_amount = 62000.00, paid_at = '2026-04-26' WHERE period_end = '2026-04-17'")
    _bank(tmpdb)
    p = _paid(tmpdb)
    assert p["2026-07-17"] == (44000.00, "2026-07-25") and p["2026-04-17"] == (62000.00, "2026-04-26")
    assert p["2026-08-15"] == (45000.00, "2026-08-26")


def test_partial_payments_sum_and_unknown_card_by_amount(tmpdb):
    _statements(tmpdb)
    _bank(tmpdb, [("20.06.2026", 20000.0), ("24.06.2026", 23000.00)])  # iki parça
    assert _paid(tmpdb)["2026-06-15"] == (43000.00, "2026-06-24")
    _bank(tmpdb, [("26.08.2026", 45000.00)], card=False)  # açıklamada kart yok: tutar borçla tutuyor
    assert _paid(tmpdb)["2026-08-15"] == (45000.00, "2026-08-26")
    assert _paid(tmpdb)["2026-07-17"] == (None, None)


def test_payment_after_debt_closed_is_not_added(tmpdb):
    """09-15 borcu 09-26'da kapandı; 10-18'deki ödeme (yüklenmemiş Ekim ekstresinin) ona yazılmaz."""
    _statements(tmpdb)
    _bank(tmpdb, [*PAYS, ("18.10.2026", 47000.00)])
    assert _paid(tmpdb)["2026-09-15"] == (46000.00, "2026-09-26")
    with tmpdb.connect() as c:  # Ekim ekstresi gelince o ödeme ona gider
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt, min_payment) "
                  "VALUES ('h-oct', 'VakıfBank', ?, '2026-10-16', '2026-10-26', 47000.00, 2000)", (CARD,))
    tmpdb.auto_mark_paid()
    assert _paid(tmpdb)["2026-10-16"] == (47000.00, "2026-10-18") and _paid(tmpdb)["2026-09-15"] == (46000.00, "2026-09-26")


def test_partial_then_close_dates(tmpdb):
    """Asgari karşılandı ama borç kapanmadı: paid_at asgarinin karşılandığı ödeme; sonra kapanınca kapanış tarihi."""
    _statements(tmpdb)
    _bank(tmpdb, [("17.06.2026", 2500.0)])
    assert _paid(tmpdb)["2026-06-15"] == (2500.0, "2026-06-17")
    _bank(tmpdb, [("23.06.2026", 40500.00), ("30.06.2026", 999.0)])
    assert _paid(tmpdb)["2026-06-15"] == (43000.00, "2026-06-23")  # 30.06'daki fazla ödeme eklenmez
