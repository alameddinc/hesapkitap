"""S6: doğrulanamayan (yapay zekâ ile okunmuş, rakamları tutmayan) belge. Veriler uydurmadır."""
from datetime import date

import pytest

from tests.conftest import fixture_bytes

ROWS = [{"date": date.today().isoformat(), "description": "MARKET ORNEK ALISVERIS", "amount": -100.0, "balance": 900.0},
        {"date": date.today().isoformat(), "description": "EFT GELEN ORNEK", "amount": 50.0, "balance": 1000.0}]


def _bad_account(ok=False):
    return {"rows": [dict(r) for r in ROWS], "bank": "Örnek Bank", "owner_name": "", "verified": ok,
            "chain_bad": [] if ok else [1], "closing_ok": ok, "iban": None}


@pytest.fixture
def hold(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_HOLD_UNVERIFIED", "1")


@pytest.fixture
def account_pdf(monkeypatch):
    from app import bank_pdf, mailbox

    box = {"res": _bad_account()}
    monkeypatch.setattr(bank_pdf, "parse_account_pdf", lambda data, pws=None: box["res"])
    monkeypatch.setattr(mailbox, "_looks_like_account_statement", lambda text: True)
    return box


def _ai_statement(broken=True):
    from app.parsers.base import PURCHASE, Statement, Transaction

    return Statement(bank="Örnek Bank", card_no="5555 **** **** 1234", period_end=date(2026, 10, 15),
                     due_date=date(2026, 10, 25), total_debt=1500.0 if broken else 1000.0, min_payment=300.0,
                     credit_limit=None, previous_balance=0.0, period_activity=1000.0, payments=0.0,
                     transactions=[Transaction(date(2026, 10, 1), "ORNEK MAGAZA", 1000.0, PURCHASE, "5555***1234")],
                     ai_parsed=True)


def test_mail_account_unverified_waits_for_approval(client, tmpdb, account_pdf, hold):
    from app import mailbox

    msg = mailbox.handle_pdf("dokum.pdf", b"%PDF-1.4\n" + fixture_bytes("kuveytturk.txt"))
    assert msg.inbox_id and "Gelen kutusu" in msg
    assert not [e for e in tmpdb.list_entries() if e.get("import_hash")]  # içe aktarılmadı
    inbox = client.get("/api/inbox").json()
    item = next(x for x in inbox if x["id"] == msg.inbox_id)
    assert item["kind"] == "dokum" and item["doc"]["verification"] == "dogrulanamadi"
    assert item["doc"]["verification_text"].startswith("Rakamlar tutmadı")
    todo = [r for r in client.get("/api/reminders").json() if r["kind"] == "belge_onay"]
    assert todo and todo[0]["id"] == f"gelen:{msg.inbox_id}" and todo[0]["action"] == "inbox_review"
    assert "okundu ama rakamlar tutmadı:" in todo[0]["text"]  # cümle içinde küçük harf

    r = client.post(f"/api/inbox/{msg.inbox_id}/import").json()
    assert r["ok"] and r["kind"] == "dokum" and r["stats"]["gider"] == 1 and r["stats"]["gelir"] == 1
    assert len([e for e in tmpdb.list_entries() if e.get("import_hash")]) == 2
    assert not [r for r in client.get("/api/reminders").json() if r["kind"] == "belge_onay"]
    assert client.post(f"/api/inbox/{msg.inbox_id}/import").status_code == 404  # ikinci kez yok
    docs = client.get("/api/bank-import/documents").json()
    assert docs and docs[0]["verification"] == "onaylandi"


def test_mail_account_verified_imports_directly(tmpdb, account_pdf):
    from app import mailbox

    account_pdf["res"] = _bad_account(ok=True)
    msg = mailbox.handle_pdf("dokum.pdf", b"%PDF-1.4\n" + fixture_bytes("kuveytturk.txt"))
    assert not getattr(msg, "inbox_id", None) and "1 gelir, 1 gider" in msg


def test_mail_ai_card_statement_broken_equation_held(client, tmpdb, monkeypatch, hold):
    from app import mailbox, parsers

    monkeypatch.setattr(parsers, "parse_statement", lambda data, passwords=None: _ai_statement())
    msg = mailbox.handle_pdf("ekstre.pdf", b"%PDF-1.4\n" + fixture_bytes("kuveytturk.txt"))
    assert msg.inbox_id and not tmpdb.list_statements()
    item = next(x for x in client.get("/api/inbox").json() if x["id"] == msg.inbox_id)
    assert item["kind"] == "ekstre" and item["doc"]["equation_diff"] == 500.0
    r = client.post(f"/api/inbox/{msg.inbox_id}/import").json()
    assert r["kind"] == "ekstre" and r["statement_id"]
    st = tmpdb.list_statements()
    assert len(st) == 1 and st[0]["total_debt"] == 1500.0 and not st[0]["needs_check"]  # onaylı kaydedildi
    assert len(client.get(f"/api/statements/{st[0]['id']}/file").content) > 0


def test_held_file_endpoint(client, tmpdb, account_pdf, hold):
    from app import mailbox

    data = b"%PDF-1.4\n" + fixture_bytes("kuveytturk.txt")
    msg = mailbox.handle_pdf("Ekim dokumu.pdf", data)
    r = client.get(f"/api/inbox/{msg.inbox_id}/file")
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == "application/pdf"
    assert "inline" in r.headers.get("content-disposition", "")
    assert client.get("/api/inbox/9999/file").status_code == 404
    from app import db, share

    with db.connect() as c:  # fiş/fatura kaydı: bu uçtan dosya verilmez
        share._init(c)
        rid = c.execute("INSERT INTO shared_inbox (filename, kind, doc) VALUES ('f.jpg', 'receipt', '{}')").lastrowid
    assert client.get(f"/api/inbox/{rid}/file").status_code == 404


def test_hold_off_imports_but_warns(client, tmpdb, account_pdf, monkeypatch):
    """Varsayılan (HESAPKITAP_HOLD_UNVERIFIED=0): eski istemciler için eskisi gibi içe aktarılır; uyarı yine çıkar."""
    from app import mailbox, parsers

    monkeypatch.delenv("HESAPKITAP_HOLD_UNVERIFIED", raising=False)
    msg = mailbox.handle_pdf("dokum.pdf", b"%PDF-1.4\n" + fixture_bytes("kuveytturk.txt"))
    assert not getattr(msg, "inbox_id", None) and "Rakamlar tutmadı" in msg
    assert len([e for e in tmpdb.list_entries() if e.get("import_hash")]) == 2
    assert not client.get("/api/inbox").json()
    doc = client.get("/api/bank-import/documents").json()[0]
    assert doc["verification"] == "dogrulanamadi"
    monkeypatch.setattr(parsers, "parse_statement", lambda data, passwords=None: _ai_statement())
    monkeypatch.setattr(mailbox, "_looks_like_account_statement", lambda text: False)
    assert not getattr(mailbox.handle_pdf("e.pdf", b"%PDF-1.4\nFIXTURE:vakifbank_a.txt"), "inbox_id", None)
    assert len(tmpdb.list_statements()) == 1
    ids = {r["id"] for r in client.get("/api/reminders").json() if r["kind"] == "belge_dogrulanamadi"}
    assert f"dokum_kontrol:{doc['id']}" in ids and f"ekstre_kontrol:{tmpdb.list_statements()[0]['id']}" in ids


def test_mail_ai_card_statement_ok_saved(tmpdb, monkeypatch):
    from app import mailbox, parsers

    monkeypatch.setattr(parsers, "parse_statement", lambda data, passwords=None: _ai_statement(broken=False))
    msg = mailbox.handle_pdf("ekstre.pdf", b"%PDF-1.4\n" + fixture_bytes("kuveytturk.txt"))
    assert not getattr(msg, "inbox_id", None) and len(tmpdb.list_statements()) == 1


def test_share_path_reports_waiting(client, tmpdb, account_pdf, hold):
    from app import share

    r = share.process("dokum.pdf", b"%PDF-1.4\n" + fixture_bytes("kuveytturk.txt"), "application/pdf")
    assert r["status"] == "bekliyor" and r["verification"] == "dogrulanamadi" and r["id"]


def test_upload_unverified_document_flagged_until_confirmed(client, tmpdb, account_pdf):
    pdf = {"file": ("dokum.pdf", b"%PDF-1.4 ornek", "application/pdf")}
    pre = client.post("/api/bank-import/preview", files=pdf).json()
    assert pre["verification"] == "dogrulanamadi" and pre["chain_bad_count"] == 1
    st = client.post("/api/bank-import", files=pdf).json()
    assert st["verified"] is False and st["verification"] == "dogrulanamadi" and "Rakamlar tutmadı" in st["verification_text"]
    doc = client.get("/api/bank-import/documents").json()[0]
    assert doc["verification"] == "dogrulanamadi"
    todo = [r for r in client.get("/api/reminders").json() if r["kind"] == "belge_dogrulanamadi"]
    assert todo and todo[0]["id"] == f"dokum_kontrol:{doc['id']}" and todo[0]["action"] == "document_confirm"
    assert todo[0]["cta"] == "Bak" and todo[0]["confirm_label"] == "Rakamlar doğru"
    assert client.post(f"/api/bank-import/documents/{doc['id']}/confirm").json()["verification"] == "onaylandi"
    assert not [r for r in client.get("/api/reminders").json() if r["kind"] == "belge_dogrulanamadi"]


def test_upload_statement_reports_verification(client, upload):
    res = upload(["vakifbank_a.txt"])
    assert res[0]["status"] == "saved" and res[0]["verification"] in ("dogrulandi", "dogrulanamadi")
    assert isinstance(res[0]["verified"], bool)


def test_statement_needs_check_todo(client, tmpdb):
    with tmpdb.connect() as c:
        sid = c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, previous_balance, "
                        "payments, period_activity, total_debt) VALUES ('hx', 'Örnek Bank', '**** 9999', ?, ?, 0, 0, "
                        "1000, 1500)", (date.today().isoformat(), date.today().isoformat())).lastrowid
    ids = [r["id"] for r in client.get("/api/reminders").json()]
    assert f"ekstre_kontrol:{sid}" in ids
    client.post(f"/api/statements/{sid}/confirm")
    assert f"ekstre_kontrol:{sid}" not in [r["id"] for r in client.get("/api/reminders").json()]
