"""Kart Excel'i + PDF ekstresi aynı dönem (denetim A2, Ek A F3.5): harcamalar bir kez sayılır; Excel'den üretilen
ekstrenin "borç" alanı (kurgu) kart borcuna ve ödendi eşleşmesine girmez. Kart numarası ve tutarlar uydurmadır."""
from datetime import date

from app.parsers.base import PURCHASE, Statement, Transaction
from tests.test_card_excel import _xlsx

CARD = "411111******3333"
ROWS = [
    ["Kart Türü", "Örnek Kart"], ["Kart Numarası", CARD], ["Şube", "X"], [], ["Toplu Ekstre"],
    ["İşlem Tarihi", "Açıklama", "Tutar", "Sektör", "Mil/ Altın Puan"],
    ["10.06.2026", "ORNEK KAFE TR", "120.50 TL", "Yemek Yerleri ve Restoranlar", "0"],
    ["20.05.2026", "ORNEK PETROL TR", "900.00 TL", "Servis İstasyonları", "0"],
]


def _excel(client):
    r = client.post("/api/statements/upload", files={"files": ("kart.xlsx", _xlsx(ROWS), "application/vnd.ms-excel")})
    return r.json()[0]


def _pdf_statement(pe=date(2026, 6, 16), bank="Örnek Bank"):
    s = Statement(bank=bank, card_no="4111 **** **** 3333", period_end=pe, due_date=date(2026, 6, 26),
                  total_debt=3020.5, min_payment=900, credit_limit=40000, previous_balance=2000, period_activity=1020.5,
                  payments=0)
    s.transactions = [Transaction(txn_date=date(2026, 6, 10), description="ORNEK KAFE TR", amount=120.5, txn_type=PURCHASE,
                                  card=CARD),
                      Transaction(txn_date=date(2026, 5, 20), description="ORNEK PETROL TR", amount=900.0, txn_type=PURCHASE,
                                  card=CARD)]
    return s


def _june_spend(tmpdb):
    with tmpdb.connect() as c:
        return c.execute("SELECT COUNT(*), ROUND(SUM(amount), 2) FROM transactions t JOIN statements s "
                         "ON s.id = t.statement_id").fetchone()[:]


def test_excel_then_pdf_counts_once(client, tmpdb):
    """Önce Excel (kesim tahmini 15.06), sonra PDF (gerçek kesim 16.06, banka adı farklı): tek ekstre, harcama bir kez."""
    assert _excel(client)["card_excel"]["statements"] == 1
    st = _pdf_statement()
    assert not tmpdb.statement_exists(b"pdf-june", st)  # Excel ekstresi PDF'in yerini almasına engel değil
    tmpdb.save_statement(b"pdf-june", "haziran.pdf", st)
    sts = tmpdb.list_statements()
    assert len(sts) == 1 and sts[0]["source"] is None and sts[0]["total_debt"] == 3020.5
    assert _june_spend(tmpdb) == (2, 1020.5)  # önce 4 satır / 2.041


def test_pdf_then_excel_skips_period(client, tmpdb):
    tmpdb.save_statement(b"pdf-june", "haziran.pdf", _pdf_statement())
    r = _excel(client)
    assert r["card_excel"]["statements"] == 0  # kesim 15.06 ≈ 16.06: aynı dönem
    assert _june_spend(tmpdb) == (2, 1020.5)


def test_same_pdf_different_bank_name_is_duplicate(tmpdb):
    """Yapay zekâ banka adını farklı yazabilir: aynı kartın aynı dönemi (±5 gün) ikinci kez kaydedilmez."""
    tmpdb.save_statement(b"a", "a.pdf", _pdf_statement(bank="Örnek Bank"))
    assert tmpdb.statement_exists(b"b", _pdf_statement(bank="ÖRNEK BANKASI A.Ş.", pe=date(2026, 6, 15)))
    assert not tmpdb.statement_exists(b"c", _pdf_statement(pe=date(2026, 7, 16)))


def test_excel_statement_not_card_debt(client, tmpdb):
    from app import finance

    _excel(client)
    st = tmpdb.list_statements()[0]
    assert st["source"] == "excel"
    nw = finance.net_worth(date(2026, 6, 20))
    assert nw["card_debt"] == 0.0 and tmpdb.card_debt_at("2026-06-20") == 0.0
    assert not any(p["type"] == "statement" for p in finance.payables(date(2026, 6, 20)))
    # kurgu borçla aynı tutarlı banka çıkışı kart ödemesi sanılmaz, ekstre "ödendi" olmaz
    assert tmpdb.match_card_payment(st["total_debt"], "2026-06-20") is None
    with tmpdb.connect() as c:
        c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, import_hash) VALUES ('2026-06-20', ?, '3333', 'x')",
                  (st["total_debt"],))
    tmpdb.reassign_statement_payments()
    assert tmpdb.list_statements()[0]["paid_at"] is None
    # kart listesinde yine görünür (son dönemi), borcu 0
    assert [(c["card_no"][-4:], c["unpaid"]) for c in nw["credit_cards"]] == [("3333", 0.0)]


def test_old_excel_statements_marked_on_startup(tmpdb):
    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt) "
                  "VALUES ('k1', 'Kart Dokumu.XLSX', 'Örnek', ?, '2026-06-15', '2026-06-25', 100)", (CARD,))
    tmpdb.init_db()
    assert tmpdb.list_statements()[0]["source"] == "excel"
