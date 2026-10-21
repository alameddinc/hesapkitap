"""E-posta otomasyonu: gönderen doğrulama, bildirim → hareket, bildirim ↔ PDF çift sayım önleme."""
from email.message import EmailMessage

import pytest

from tests.conftest import fixture_bytes


def _mail(sender="bildirim@vakifbank.com.tr", dkim="vakifbank.com.tr", body="", pdf: bytes | None = None,
          mid="<1@x>"):
    m = EmailMessage()
    m["From"] = f"VakıfBank <{sender}>"
    m["To"] = "kutu@example.com"
    m["Subject"] = "Hesap hareketi"
    m["Date"] = "Tue, 20 Oct 2026 10:00:00 +0300"
    m["Message-ID"] = mid
    if dkim:
        m["Authentication-Results"] = f"mx.google.com; dkim=pass header.i=@{dkim} header.s=s1; spf=pass"
    m.set_content(body or "Bildirim")
    if pdf:
        m.add_attachment(pdf, maintype="application", subtype="pdf", filename="ekstre.pdf")
    return m


@pytest.fixture
def notif(monkeypatch):
    """Modeli taklit et: verilen hareket listesini döndür."""
    from app import mailbox, receipt

    box = {}
    monkeypatch.setattr(receipt, "enabled", lambda: True)
    monkeypatch.setattr(mailbox, "parse_notification", lambda text, d: box["raw"])
    return box


def test_sender_check(tmpdb):
    from app.mailbox import sender_check

    assert sender_check(_mail())[0]
    assert sender_check(_mail(sender="no-reply@bilgi.vakifbank.com.tr", dkim="bilgi.vakifbank.com.tr"))[0]
    assert not sender_check(_mail(dkim="kotu.com"))[0]            # From banka ama imza başka alan adı: sahte
    assert not sender_check(_mail(dkim=None))[0]                  # imza yok
    assert not sender_check(_mail(sender="x@vakifbank.com.tr.kotu.com", dkim="vakifbank.com.tr.kotu.com"))[0]


def test_notification_creates_transfer_and_card_notice(tmpdb, notif):
    from app import mailbox

    notif["raw"] = {"is_transaction": True, "bank": "VakıfBank", "transactions": [
        {"date": "2026-10-20", "amount": -200, "type": "fast", "description": "FAST gönderimi", "counterparty": "ALİ ÖRNEK"},
        {"date": "2026-10-20", "amount": -675, "type": "kart_harcama", "merchant": "ESPRESSOLAB", "card_last4": "6613"},
    ]}
    r = mailbox.process("<1@x>", _mail())
    assert r["status"] == "ok" and r["kind"] == "bildirim"
    es = {e["description"]: e for e in tmpdb.list_entries()}
    assert es["Ali Örnek"]["counterparty"] == "Ali Örnek" and es["Ali Örnek"]["kind"] == "gider"
    assert es["ESPRESSOLAB"]["payment"] == "kredi_karti"           # ekstre gelince kart işlemiyle eşleşir
    # reklam maili
    notif["raw"] = {"is_transaction": False}
    assert mailbox.process("<2@x>", _mail(mid="<2@x>"))["status"] == "skip"


def test_notification_then_pdf_counts_once(tmpdb, notif):
    from app import mailbox

    notif["raw"] = {"is_transaction": True, "bank": "VakıfBank", "transactions": [
        {"date": "2026-10-20", "amount": -200, "type": "fast", "description": "FAST", "counterparty": "Ali Örnek"},
        {"date": "2026-10-19", "amount": 5000, "type": "virman", "description": "Kendi hesabından"},
    ]}
    mailbox.process("<1@x>", _mail())
    # ay sonu dökümü: aynı iki hareket (PDF parmak iziyle)
    st = tmpdb.import_bank_rows([
        {"date": "2026-10-20", "amount": -200.0, "type": "gider", "hash": "pdf1",
         "description": "GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,Ali Örnek,X Bankasi A.S.,TR00"},
        {"date": "2026-10-19", "amount": 5000.0, "type": "virman", "hash": "pdf2", "description": "virman"},
    ], owners=["AYŞE DEMIR"])
    assert st["gider"] == 0 and st["eslesen_elle"] == 1
    es = tmpdb.list_entries()
    assert len(es) == 1 and es[0]["import_hash"] == "pdf1"         # bildirim kaydı dökümle doğrulandı
    with tmpdb.connect() as c:
        assert [r[0] for r in c.execute("SELECT import_hash FROM own_transfers")] == ["pdf2"]


def test_pdf_then_late_notification_skipped(tmpdb, notif):
    from app import mailbox

    tmpdb.import_bank_rows([{"date": "2026-10-20", "amount": -200.0, "type": "gider", "hash": "pdf1",
                             "description": "GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,Ali Örnek,X Bankasi A.S.,TR00"}],
                           owners=["AYŞE DEMIR"])
    notif["raw"] = {"is_transaction": True, "bank": "VakıfBank", "transactions": [
        {"date": "2026-10-20", "amount": -200, "type": "fast", "description": "FAST", "counterparty": "Ali Örnek"}]}
    mailbox.process("<1@x>", _mail())
    assert len(tmpdb.list_entries()) == 1


def test_spoofed_mail_ignored(tmpdb, notif):
    from app import mailbox

    notif["raw"] = {"is_transaction": True, "transactions": [{"date": "2026-10-20", "amount": 1_000_000, "type": "eft"}]}
    r = mailbox.process("<1@x>", _mail(dkim="saldirgan.com"))
    assert r["status"] == "skip" and tmpdb.list_entries() == []


def test_card_statement_pdf_from_mail(tmpdb):
    from app import mailbox

    r = mailbox.process("<1@x>", _mail(pdf=fixture_bytes("vakifbank_a.txt")))
    assert r["status"] == "ok" and r["kind"] == "pdf", r
    assert len(tmpdb.list_statements()) == 1
    assert "zaten vardı" in mailbox.process("<2@x>", _mail(pdf=fixture_bytes("vakifbank_a.txt"), mid="<2@x>"))["detail"]


def test_poll_logs_and_skips_seen(tmpdb, monkeypatch, notif):
    from app import mailbox

    monkeypatch.setenv("MAIL_USER", "kutu@example.com")
    monkeypatch.setenv("MAIL_PASSWORD", "x")
    notif["raw"] = {"is_transaction": False}
    monkeypatch.setattr(mailbox, "fetch_new", lambda: [("<1@x>", _mail())])
    assert mailbox.poll()["processed"] == 1
    assert tmpdb.mail_seen("<1@x>") and tmpdb.mail_log()[0]["status"] == "skip"


def test_salary_notification_updates_recurring(client, tmpdb, notif):
    from app import mailbox

    client.post("/api/recurring", json={"description": "Maaş", "amount": 220000, "kind": "gelir", "category": "Maaş",
                                        "day_of_month": 15, "start_month": "2026-01"})
    notif["raw"] = {"is_transaction": True, "bank": "Halkbank", "transactions": [
        {"date": "2026-10-15", "amount": 215088.75, "type": "maas", "description": "MAAŞ ÖDEMESİ",
         "counterparty": "ÖRNEK YAZILIM A.Ş."}]}
    mailbox.process("<1@x>", _mail(sender="bilgi@halkbank.com.tr", dkim="halkbank.com.tr"))
    e = tmpdb.list_entries()[0]
    assert e["description"] == "Maaş" and e["amount"] == 215088.75 and e["recurring_id"] and not e["counterparty"]
