"""Ekstre tutmuyor: ne tutmuyor ve nasıl düzeltilir (app/statement_fix.py, parsers.base.equation). UYDURMA veri.

Kapsam: özetin "harcamalar"ı iadesiz, iade / kampanya / geri ödeme ayrı satır (iadeler ayrı biçimi); özet doğru ama
işlem listesinde fazla bilgi satırı (puan / Bonus), eksik faiz satırı, işareti ters satır; özetin bir ara rakamı yanlış
ama satırlar önceki borçtan yeni borca götürüyor; elle satır ekle / düzelt (önizleme, tek geri al, yetki); şablonla
yeniden okumada iade diye okunan ödeme."""
from datetime import date

import pytest

from app import db, integrity, statement_fix as fx, statement_reread as rr, undo
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND, Statement, Transaction, equation, reconciliation_diff

CARD = "5400********1111"


def _t(day: str, desc: str, amount: float, kind: str = PURCHASE) -> Transaction:
    return Transaction(txn_date=date.fromisoformat(day), description=desc, amount=amount, txn_type=kind, card=CARD)


def _save(key: str, txns, prev, pay, act, total, fees=0.0, bank="Örnek Bank", source="yapay_zeka") -> int:
    st = Statement(bank=bank, card_no=CARD, period_end=date(2026, 10, 28), due_date=date(2026, 11, 7),
                   total_debt=total, min_payment=None, credit_limit=None, previous_balance=prev, period_activity=act,
                   interest_fees=fees, payments=pay, transactions=list(txns), source=source)
    return db.save_statement(f"FIX:{key}".encode(), f"{key}.pdf", st)


def _stmt(sid: int) -> dict:
    return next(s for s in db.list_statements() if s["id"] == sid)


def _rows(sid: int) -> list[dict]:
    with db.connect() as c:
        return db.rows(c.execute("SELECT * FROM transactions WHERE statement_id = ? ORDER BY id", (sid,)))


# ---------------------------------------------------------------- denklem: iadeler ayrı biçimi

def test_equation_refunds_separate_format():
    # önceki 2.000 − ödeme 2.000 + harcamalar 1.500 (iadesiz) + iade −120 = 1.380
    assert equation(2000, 2000, 1500, 0, 1380)["ok"] is False
    e = equation(2000, 2000, 1500, 0, 1380, refunds=-120)
    assert e["ok"] and e["refunds_separate"] and e["diff"] == 0
    assert reconciliation_diff(1380, 1500, 0, 0, 2000, 2000, 1380, refunds=-120) == 0
    # iadeler harcamaların içindeyse (çoğu banka) biçim değişmez, anahtar da gelmez
    assert equation(2000, 2000, 1380, 0, 1380, refunds=-120) == {"fees_included": None, "diff": 0, "ok": True}


def test_halkbank_like_campaign_refund_reconciles(tmpdb):
    sid = _save("hb", [_t("2026-10-02", "ODEMENIZ ICIN TESEKKURLER", -2000, PAYMENT), _t("2026-10-05", "ORNEK MARKET", 900),
                       _t("2026-10-09", "ORNEK AKARYAKIT", 600), _t("2026-10-11", "KAMPANYA INDIRIMI", -120, REFUND)],
                prev=2000, pay=2000, act=1500, total=1380)
    s = _stmt(sid)
    assert s["needs_check"] is False and s["check_kind"] is None and s["refunds_separate"] is True
    assert s["reconciliation_diff"] == 0 and s["fix_hint"] is None
    assert integrity._ekstre_denklem([s]) == [] and integrity._ekstre_satir_farki([s]) == []


# ---------------------------------------------------------------- özet doğru, işlem listesi tutmuyor

def test_extra_info_row_is_named_and_hiding_it_reconciles(tmpdb):
    # Garanti benzeri: özet doğru, yapay zekâ "harcanan puan" bilgi satırını iade yazmış (özete girmiyor)
    sid = _save("gb", [_t("2026-10-03", "ORNEK MARKET", 500), _t("2026-10-06", "ORNEK KAFE", 300),
                       _t("2026-09-29", "PUAN KULLANIMI", -50, REFUND)], prev=1000, pay=1000, act=800, total=800)
    s = _stmt(sid)
    assert s["needs_check"] and s["check_kind"] == "rows" and s["fix_diff"] == -50
    assert s["fix_badge"] == "İşlemler eksik" and s["fix_title"] == "Özet doğru, işlem listesinde ₺50,00 fark var"
    bonus = next(r for r in _rows(sid) if r["description"] == "PUAN KULLANIMI")
    h = s["fix_hint"]
    assert h["kind"] == "extra_row" and h["row_id"] == bonus["id"]
    assert h["text"] == "₺50,00 fark: ₺50,00 tutarındaki iade satırı ekstrede yoksa fazla okunmuş; hesaba katma"

    pre = fx.edit_row(sid, bonus["id"], {"is_deleted": True}, dry_run=True)
    assert pre["dry_run"] and pre["check"]["ok"] is True and _stmt(sid)["needs_check"]  # önizleme yazmaz
    res = fx.edit_row(sid, bonus["id"], {"is_deleted": True})
    assert res["undo_id"] and res["check"]["ok"] is True
    s = _stmt(sid)
    assert s["needs_check"] is False and s["check_kind"] is None
    assert undo.undo(res["undo_id"])["ok"] and _stmt(sid)["needs_check"]


def test_missing_fee_row_suggestion_and_add(tmpdb):
    # özet: harcamalar 1.000 (30 TL faiz/BSMV dahil), listede faiz satırı yok
    sid = _save("fee", [_t("2026-10-03", "ORNEK MARKET", 970), _t("2026-10-02", "ODEME", -500, PAYMENT)],
                prev=500, pay=500, act=1000, total=1000)
    s = _stmt(sid)
    assert s["fix_diff"] == -30 and s["fix_hint"]["kind"] == "fee_missing"
    assert s["fix_hint"]["text"] == "₺30,00 fark: muhtemelen faiz / BSMV / KKDF satırı eksik"
    assert s["fix_hint"]["suggest"] == {"txn_type": FEE, "amount": 30, "txn_date": "2026-10-28"}

    assert fx.add_row(sid, {"txn_type": FEE, "amount": 20}, dry_run=True)["check"]["diff"] == -10  # kalan fark
    res = fx.add_row(sid, {"txn_type": FEE, "amount": 30})
    assert res["check"]["ok"] is True and res["undo_id"]
    row = next(r for r in _rows(sid) if r["id"] == res["id"])
    assert (row["amount"], row["txn_type"], row["txn_date"], row["description"], row["card"]) == \
        (30, FEE, "2026-10-28", "Faiz / ücret", CARD)
    assert _stmt(sid)["needs_check"] is False
    assert undo.undo(res["undo_id"])["ok"] and len(_rows(sid)) == 2


def test_refund_and_payment_rows_are_entered_positive(tmpdb):
    sid = _save("ref", [_t("2026-10-03", "ORNEK MARKET", 400)], prev=0, pay=0, act=320, total=320)
    assert _stmt(sid)["fix_hint"]["kind"] == "credit_missing"
    assert _stmt(sid)["fix_hint"]["suggest"]["txn_type"] == REFUND
    res = fx.add_row(sid, {"txn_type": REFUND, "amount": 80, "description": "ORNEK IADE", "txn_date": "2026-10-10"})
    assert res["check"]["ok"]
    assert next(r for r in _rows(sid) if r["id"] == res["id"])["amount"] == -80  # işaret türden


def test_wrong_sign_row_suggestion_and_type_change(tmpdb):
    sid = _save("sign", [_t("2026-10-03", "ORNEK MARKET", 940), _t("2026-10-08", "ORNEK MAGAZA IADE", 60)],
                prev=0, pay=0, act=880, total=880)
    s = _stmt(sid)
    iade = next(r for r in _rows(sid) if "IADE" in r["description"])
    assert s["fix_diff"] == 120 and s["fix_badge"] == "İşlemler fazla"
    assert s["fix_hint"]["kind"] == "sign" and s["fix_hint"]["row_id"] == iade["id"]
    res = fx.edit_row(sid, iade["id"], {"txn_type": REFUND})
    assert res["check"]["ok"]
    r = next(r for r in _rows(sid) if r["id"] == iade["id"])
    assert (r["amount"], r["txn_type"]) == (-60, REFUND)


# ---------------------------------------------------------------- özet tutmuyor

def test_summary_figure_misread_but_rows_reconcile_is_ok(tmpdb):
    # özetin "harcamalar"ı yanlış okunmuş (999); satırlar önceki borçtan yeni borca kuruşu kuruşuna götürüyor
    sid = _save("sum", [_t("2026-10-02", "ODEME", -1000, PAYMENT), _t("2026-10-05", "ORNEK MARKET", 700)],
                prev=1000, pay=1000, act=999, total=700)
    s = _stmt(sid)
    assert s["equation_diff"] == -299 and s["rows_ok"] and s["needs_check"] is False
    assert integrity._ekstre_denklem([s]) == [] and integrity._ekstre_satir_farki([s]) == []


def test_summary_broken_payment_row_missing(tmpdb):
    sid = _save("pm", [_t("2026-10-05", "ORNEK MARKET", 500)], prev=1000, pay=0, act=500, total=500)
    s = _stmt(sid)
    assert s["needs_check"] and s["check_kind"] == "summary" and s["fix_badge"] == "Özet tutmuyor"
    # özet: 1.000 − 0 + 500 ≠ 500; özetin ödemesi okunmamış, fark tam önceki borç kadar → ödeme şüpheli
    assert s["fix_title"] == "Ekstre özeti ₺1.000,00 tutmuyor: ödeme rakamı yanlış okunmuş olabilir"
    assert s["fix_hint"]["kind"] == "payment_missing"
    assert s["fix_hint"]["suggest"] == {"txn_type": PAYMENT, "amount": 1000, "txn_date": "2026-10-28"}
    assert fx.add_row(sid, {"txn_type": PAYMENT, "amount": 1000})["check"]["ok"]
    assert _stmt(sid)["needs_check"] is False


def test_summary_suspect_names_the_figure(tmpdb):
    # özetin harcaması 45 TL faizle şişkin okunmuş, faiz satırı listede yok: fark tam faiz kadar
    sid = _save("sus", [_t("2026-10-05", "ORNEK MARKET", 1000)], prev=0, pay=0, act=1090, total=1045, fees=45)
    s = _stmt(sid)
    assert s["check_kind"] == "summary" and s["fix_badge"] == "Özet tutmuyor"
    assert s["fix_title"] == "Ekstre özeti ₺45,00 tutmuyor: faiz / BSMV / KKDF rakamı yanlış okunmuş olabilir"


def test_rows_path_does_not_hide_payment_typed_as_refund(tmpdb):
    # özet doğru; ödeme iade diye yazılmış: satır denklemi tutar (ikisi de eksi) ama harcamalar eksik görünür
    sid = _save("pr", [_t("2026-10-01", "HESAPTAN AKTARIM", -1000, REFUND), _t("2026-10-05", "ORNEK MARKET", 500)],
                prev=1000, pay=1000, act=500, total=500)
    s = _stmt(sid)
    assert s["rows_diff"] == 0 and s["needs_check"] and s["check_kind"] == "rows"


# ---------------------------------------------------------------- şablonla yeniden okuma: iade diye okunan ödeme

def test_reread_plan_retypes_refund_that_is_the_missing_payment(tmpdb):
    rows = [_t("2026-10-05", "ORNEK MARKET", 200), _t("2026-10-06", "ORNEK KAFE", 100), _t("2026-10-07", "ORNEK KITAP", 120),
            _t("2026-10-08", "ORNEK SINEMA", 80), _t("2026-10-09", "ORNEK IADE", -40, REFUND)]
    sid = _save("isb", rows, prev=1000, pay=1000, act=460, total=460)
    with db.connect() as c:
        st = dict(c.execute("SELECT * FROM statements WHERE id = ?", (sid,)).fetchone())
    read = Statement(bank="Örnek Bank", card_no=CARD, period_end=date(2026, 10, 28), due_date=date(2026, 11, 7),
                     total_debt=460, min_payment=None, credit_limit=None, previous_balance=1000,
                     transactions=[_t("2026-10-01", "1234-567890 HESAPTAN AKTARIM", -1000, REFUND), *rows])
    p = rr.plan_for(st, _rows(sid), read)
    assert p and [(a["txn_type"], a["amount"]) for a in p["added"]] == [(PAYMENT, -1000)]


def test_template_reads_account_transfer_as_payment():
    from app import card_reader

    low = card_reader._fold("1234-567890 HESAPTAN AKTARIM 1234 INTERAKTIF".lower())
    assert card_reader._PAY_WORDS.search(low)


# ---------------------------------------------------------------- API ve yetki

def test_api_rows_add_edit_dry_run_and_validation(client):
    sid = _save("api", [_t("2026-10-03", "ORNEK MARKET", 970), _t("2026-10-02", "ODEME", -500, PAYMENT)],
                prev=500, pay=500, act=1000, total=1000)
    s = next(x for x in client.get("/api/statements").json() if x["id"] == sid)
    assert s["can_fix"] is True and s["fix_hint"]["kind"] == "fee_missing"
    got = client.get(f"/api/statements/{sid}/rows").json()
    assert len(got["rows"]) == 2 and got["check"]["ok"] is False and got["check"]["diff"] == -30
    assert client.post(f"/api/statements/{sid}/rows", json={"txn_type": "x", "amount": 5}).status_code == 422
    assert client.post(f"/api/statements/{sid}/rows", json={"txn_type": FEE, "amount": -5}).status_code == 422
    assert client.post("/api/statements/999/rows", json={"txn_type": FEE, "amount": 5}).status_code == 404
    pre = client.post(f"/api/statements/{sid}/rows", json={"txn_type": FEE, "amount": 30, "dry_run": True}).json()
    assert pre["check"]["ok"] and len(client.get(f"/api/statements/{sid}/rows").json()["rows"]) == 2
    r = client.post(f"/api/statements/{sid}/rows", json={"txn_type": FEE, "amount": 30}).json()
    assert r["check"]["ok"] and r["undo_id"]
    market = next(x for x in got["rows"] if x["description"] == "ORNEK MARKET")
    e = client.patch(f"/api/statements/{sid}/rows/{market['id']}", json={"amount": 960, "dry_run": True}).json()
    assert e["check"]["diff"] == -10
    assert client.patch(f"/api/statements/{sid}/rows/99999", json={"amount": 1}).status_code == 404
    assert client.post(f"/api/imports/{r['undo_id']}/undo").json()["ok"]
    from app import version

    assert "statement_fix" in version.CAPABILITIES


@pytest.mark.parametrize("role,ok", [("viewer", False), ("editor", False), ("full", True)])
def test_shared_book_permissions(client, monkeypatch, role, ok):
    from tests.test_book import _member

    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    sid = _save("perm", [_t("2026-10-03", "ORNEK MARKET", 970)], prev=0, pay=0, act=1000, total=1000)
    m = _member(client, role=role)
    assert (m.get(f"/api/statements/{sid}/rows").status_code == 200) is ok
    r = m.post(f"/api/statements/{sid}/rows", json={"txn_type": FEE, "amount": 30})
    assert (r.status_code == 200) is ok, r.text
    assert next(x for x in m.get("/api/statements").json() if x["id"] == sid)["can_fix"] is ok
