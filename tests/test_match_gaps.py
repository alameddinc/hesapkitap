"""Elle kayıt ↔ içe aktarma eşleşme boşlukları (denetim A1, Ek A F3.4). Kişiler, tutarlar uydurmadır."""
from datetime import date

from app.parsers.base import PURCHASE, Statement, Transaction


def _gider(client, **kw):
    body = {"entry_date": "2026-10-20", "description": "Psikolog", "amount": 3000, "kind": "gider",
            "category": "Sağlık", "payment": "havale", **kw}
    r = client.post("/api/entries", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _expense(client, month="2026-10"):
    return client.get("/api/summary", params={"month": month}).json()["totals"]["expense"]


# --- 1) e-posta bildirimi önce, elle kayıt sonra ------------------------------------------------------------------

def test_mail_notice_then_manual_entry_is_one_expense(client, tmpdb):
    tmpdb.import_bank_rows([{"date": "2026-10-21", "description": "FAST PSIKOLOG AHMET ORNEK", "amount": -3000.0,
                             "type": "gider", "hash": "mail:bildirim-1"}], account="Halkbank")
    assert _expense(client) == 3000
    r = _gider(client)
    assert r["match"] and r["match"]["source"] == "dokum"
    assert _expense(client) == 3000  # önce 6.000 (bildirim + elle kayıt)
    assert len(tmpdb.list_entries()) == 1


# --- 2) ekstrede eşleşmeyen kart kaydı ----------------------------------------------------------------------------

def _statement(tmpdb, pe=date(2026, 10, 15), amount=1037.5, txn_date=date(2026, 10, 3)):
    s = Statement(bank="VakıfBank", card_no="4543***1234", period_end=pe, due_date=date(2026, 10, 25),
                  total_debt=amount, min_payment=100, credit_limit=50000, previous_balance=0, period_activity=amount,
                  payments=0)
    s.transactions = [Transaction(txn_date=txn_date, description="ORNEK ONLINE MAGAZA", amount=amount, txn_type=PURCHASE,
                                  card="4543***1234")]
    tmpdb.save_statement(f"st-{pe}".encode(), "e.pdf", s)


def _card_todo(client):
    return [r for r in client.get("/api/reminders").json() if r["kind"] == "kart_eslesmedi"]


def test_unmatched_card_entry_becomes_todo(client, tmpdb):
    """Elle girilen kart harcaması (950, döviz kuru farkı) ekstrede 1.037,50: tolerans dışında, iki kez sayılabilir."""
    eid = _gider(client, entry_date="2026-10-02", description="Yurt dışı alışveriş", amount=950,
                 category="Alışveriş", payment="kredi_karti")["id"]
    assert _card_todo(client) == []  # ekstre henüz yok: "ekstre bekleniyor" normal
    _statement(tmpdb)
    todo = _card_todo(client)
    assert [t["id"] for t in todo] == [f"kart:{eid}"] and todo[0]["action"] == "card_match"
    assert client.post(f"/api/reminders/kart:{eid}/dismiss").status_code == 200
    assert _card_todo(client) == []


def test_card_entry_near_cut_waits_for_next_statement(client, tmpdb):
    """Kesimden 2 gün önceki harcama sonraki ekstreye düşebilir: o ekstre gelmeden sorulmaz."""
    _statement(tmpdb)
    _gider(client, entry_date="2026-10-13", description="Kitap", amount=120, category="Alışveriş", payment="kredi_karti")
    assert _card_todo(client) == []


def test_matched_card_entry_not_asked(client, tmpdb):
    _gider(client, entry_date="2026-10-03", description="Online", amount=1037.5, category="Alışveriş", payment="kredi_karti")
    _statement(tmpdb)
    assert _card_todo(client) == []


# --- 3) ilgisiz havale borca yazılmaz -------------------------------------------------------------------------------

FAST = "GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,Ali Örnek,X Bankasi A.S.,TR00"


def test_unrelated_transfer_not_auto_debt_payment(client, tmpdb):
    did = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "borc", "amount": 5000,
                                          "debt_date": "2026-10-01"}).json()["id"]
    st = tmpdb.import_bank_rows([{"date": "2026-10-12", "amount": -250.0, "type": "gider", "hash": "yemek",
                                  "description": FAST + " YEMEK PAYI"}], owners=["AYŞE DEMIR"])
    assert "borc_hareketi" not in st
    d = next(x for x in tmpdb.list_debts() if x["id"] == did)
    assert d["remaining"] == 5000  # önce 4.750: yemek payı borçtan düşülüyordu
    assert _expense(client) == 250
    q = [r for r in client.get("/api/reminders").json() if r["kind"] == "borc_odeme"]
    assert len(q) == 1 and q[0]["debt_id"] == did
    # kullanıcı "evet, borçtan" derse
    r = client.post(f"/api/debts/{did}/pay", json=q[0]["pay"])
    assert r.status_code == 200
    assert next(x for x in tmpdb.list_debts() if x["id"] == did)["remaining"] == 4750
    assert _expense(client) == 0
    assert [r for r in client.get("/api/reminders").json() if r["kind"] == "borc_odeme"] == []


def test_partial_with_debt_words_still_automatic(client, tmpdb):
    did = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "borc", "amount": 5000,
                                          "debt_date": "2026-10-01"}).json()["id"]
    st = tmpdb.import_bank_rows([{"date": "2026-10-12", "amount": -1000.0, "type": "gider", "hash": "taksit",
                                  "description": FAST + " BORC TAKSIT 1"}], owners=["AYŞE DEMIR"])
    assert st["borc_hareketi"] == 1
    assert next(x for x in tmpdb.list_debts() if x["id"] == did)["remaining"] == 4000


def test_exact_remaining_still_settles(client, tmpdb):
    did = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "borc", "amount": 200,
                                          "debt_date": "2026-10-10"}).json()["id"]
    st = tmpdb.import_bank_rows([{"date": "2026-10-20", "amount": -200.0, "type": "gider", "hash": "h", "description": FAST}],
                                owners=["AYŞE DEMIR"])
    assert st["borc_hareketi"] == 1
    assert next(d for d in tmpdb.list_debts() if d["id"] == did)["settled_at"] == "2026-10-20"
