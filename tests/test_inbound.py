"""Gelen posta: deftere özel adres, imza doğrulama, banka belgesi / fatura, tekrar ve paket kilidi."""
from email.message import EmailMessage

import pytest

from tests.conftest import fixture_bytes


def _raw(sender="bildirim@vakifbank.com.tr", body="Bildirim", pdf: bytes | None = None, mid="<1@x>") -> bytes:
    m = EmailMessage()
    m["From"] = f"Gönderen <{sender}>"
    m["To"] = "fatura-abc@posta.example"
    m["Subject"] = "Belge"
    m["Date"] = "Tue, 20 Oct 2026 10:00:00 +0300"
    m["Message-ID"] = mid
    m.set_content(body)
    if pdf:
        m.add_attachment(pdf, maintype="application", subtype="pdf", filename="belge.pdf")
    return m.as_bytes()


@pytest.fixture
def owner(tmpdb):
    from app import auth

    return auth.create_user("ayse@ornek.com", "ayse-sifre-12345")


def signed(*domains):
    return lambda raw: set(domains)


def test_address_is_stable_unique_and_regenerates(owner):
    from app import auth, inbound

    a = inbound.address(owner)
    assert a.startswith("fatura-") and a.endswith("@posta.alameddinc.com") and len(a.split("@")[0]) == 13
    assert inbound.address(owner) == a  # aynı defter, aynı adres
    other = auth.create_user("mehmet", "mehmet-sifre-12345")
    assert inbound.address(other) != a  # her kullanıcının kendi adresi
    assert inbound.resolve(a)["id"] == owner["id"]
    assert inbound.resolve(a.replace("@posta.alameddinc.com", "@hesapkitap.app"))["id"] == owner["id"]  # alan adı değişse de
    b = inbound.regenerate(owner)
    assert b != a and inbound.resolve(a) is None and inbound.resolve(b)["id"] == owner["id"]
    assert inbound.resolve("baska@posta.alameddinc.com") is None


def test_bank_statement_by_mail(owner):
    from app import db, inbound, system

    to = inbound.address(owner)
    r = inbound.handle(_raw(pdf=fixture_bytes("vakifbank_a.txt")), to, verify=signed("vakifbank.com.tr"))
    assert r["status"] == "ok" and "ekstresi" in r["detail"]
    with db.use_space(system.user_db_path(owner), owner):
        assert len(db.list_statements()) == 1
    # aynı mail tekrar gelirse işlenmez
    assert inbound.handle(_raw(pdf=fixture_bytes("vakifbank_a.txt")), to, verify=signed("vakifbank.com.tr"))["status"] == "skip"


def test_unsigned_or_unknown_sender_skipped(owner):
    from app import inbound

    to = inbound.address(owner)
    assert inbound.handle(_raw(mid="<2@x>"), to, verify=signed())["status"] == "skip"  # imza yok
    assert inbound.handle(_raw(mid="<3@x>"), to, verify=signed("kotu.com"))["status"] == "skip"  # başka alan adı
    r = inbound.handle(_raw(sender="x@kotu.com", mid="<4@x>"), to, verify=signed("kotu.com"))
    assert r["status"] == "skip" and "Tanınan" in r["detail"]


def test_bill_by_mail_and_change_warning(owner, monkeypatch):
    from app import db, inbound, receipt, system

    bills = iter([{"provider": "Enerjisa", "category": "Faturalar", "amount": 600.0, "due_date": "2026-10-05"},
                  {"provider": "Enerjisa", "category": "Faturalar", "amount": 845.3, "due_date": "2026-11-05"},
                  {"provider": "Enerjisa", "category": "Faturalar", "amount": 845.3, "due_date": "2026-11-05"}])
    monkeypatch.setattr(receipt, "parse_bill", lambda data, mime: next(bills))
    to = inbound.address(owner)
    sig = signed("enerjisa.com.tr")
    assert inbound.handle(_raw("efatura@enerjisa.com.tr", pdf=b"%PDF-1", mid="<b1@x>"), to, verify=sig)["status"] == "ok"
    r = inbound.handle(_raw("efatura@enerjisa.com.tr", body="Faturanız 845,30 TL", mid="<b2@x>"), to, verify=sig)
    assert r["kind"] == "fatura" and "845.30 TL" in r["detail"].replace(",", "")
    assert "%41 fazla" in r["detail"]  # 600 → 845,30
    dup = inbound.handle(_raw("efatura@enerjisa.com.tr", mid="<b3@x>"), to, verify=sig)
    assert "zaten vardı" in dup["detail"]
    with db.use_space(system.user_db_path(owner), owner):
        assert len(db.list_bills()) == 2


def test_own_email_forward_trusted(owner):
    """Kullanıcının kendi e-postasından elle ilettiği ekstre (kendi DKIM'iyle) kabul edilir."""
    from app import inbound

    to = inbound.address(owner)
    r = inbound.handle(_raw("ayse@ornek.com", pdf=fixture_bytes("vakifbank_b.txt"), mid="<k1@x>"), to,
                       verify=signed("ornek.com"))
    assert r["status"] == "ok"


def test_inbound_endpoint_and_premium(client, owner, monkeypatch):
    from app import inbound

    to = inbound.address(owner)
    raw = _raw("x@kotu.com")
    assert client.post("/api/mail/inbound", content=raw, headers={"X-HK-To": to}).status_code == 404  # anahtar yok
    monkeypatch.setenv("HESAPKITAP_INBOUND_SECRET", "s3")
    h = {"Authorization": "Bearer s3", "X-HK-To": to}
    assert client.post("/api/mail/inbound", content=raw, headers={**h, "Authorization": "Bearer yanlis"}).status_code == 404
    assert client.post("/api/mail/inbound", content=raw, headers={**h, "X-HK-To": "fatura-yok999@x"}).status_code == 404
    assert client.post("/api/mail/inbound", content=raw, headers=h).json()["status"] == "skip"
    # hazır sürüm, ücretsiz paket: işlenmez
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    r = inbound.handle(_raw(mid="<p1@x>"), to, verify=signed("vakifbank.com.tr"))
    assert r["status"] == "skip" and "Premium" in r["detail"]


def test_real_dkim_rejects_unsigned():
    from app import inbound

    assert inbound._dkim_domains(_raw()) == set()  # imzasız mail: doğrulanan alan adı yok


def test_trusted_sender_list(owner):
    from app import db, inbound, system

    to = inbound.address(owner)
    raw = _raw("ali.gmail@gmail.com", pdf=fixture_bytes("vakifbank_a.txt"), mid="<g1@x>")
    assert inbound.handle(raw, to, verify=signed("gmail.com"))["status"] == "skip"  # tanımsız gönderen
    with db.use_space(system.user_db_path(owner), owner):
        assert inbound.set_senders(["Ali.Gmail@gmail.com", "bozuk"]) == ["ali.gmail@gmail.com"]
    raw2 = _raw("ali.gmail@gmail.com", pdf=fixture_bytes("vakifbank_a.txt"), mid="<g2@x>")
    assert inbound.handle(raw2, to, verify=signed("gmail.com"))["status"] == "ok"
    raw3 = _raw("ali.gmail@gmail.com", pdf=fixture_bytes("vakifbank_b.txt"), mid="<g3@x>")
    assert inbound.handle(raw3, to, verify=signed("kotu.com"))["status"] == "skip"  # imza tutmazsa yine reddedilir
