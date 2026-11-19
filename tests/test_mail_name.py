"""Kişisel ekstre adresi (<ad>@alan adı): ad kuralları, çakışma, değiştirme, eski ad teslimi, yeni kart onayı.
Bütün kişiler ve adresler uydurmadır."""
from email.message import EmailMessage

import pytest

from tests.conftest import fixture_bytes as _fixture

DOMAIN = "ornek.app"


def pdf(name: str) -> bytes:
    return b"%PDF-1.4\n" + _fixture(name)


def _raw(sender="bildirim@vakifbank.com.tr", body="Bildirim", attach: bytes | None = None, mid="<1@x>") -> bytes:
    m = EmailMessage()
    m["From"] = f"Gönderen <{sender}>"
    m["To"] = f"deneme@{DOMAIN}"
    m["Subject"] = "Belge"
    m["Date"] = "Tue, 20 Oct 2026 10:00:00 +0300"
    m["Message-ID"] = mid
    m.set_content(body)
    if attach:
        m.add_attachment(attach, maintype="application", subtype="pdf", filename="belge.pdf")
    return m.as_bytes()


def signed(*domains):
    return lambda raw: set(domains)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_MAIL_DOMAIN", DOMAIN)


@pytest.fixture
def owner(tmpdb, on):
    from app import auth

    return auth.create_user("deniz.kaya@ornek.com", "deniz-sifre-12345")


@pytest.fixture
def other(tmpdb, on):
    from app import auth

    return auth.create_user("ozan@ornek.com", "ozan-sifre-12345")


# ---------------------------------------------------------------- kurallar

@pytest.mark.parametrize("raw,expected", [
    ("Çağrı", "cagri"), ("ŞÜKRÜ.Öz", "sukru.oz"), ("Işıl Ilgın", "isil.ilgin"), ("a b", "a.b"), ("x!y#z", "xyz"),
])
def test_normalize_turkish_to_ascii(raw, expected):
    from app import mail_name

    assert mail_name.normalize(raw) == expected


@pytest.mark.parametrize("name,code", [
    ("ab", "short"), ("a" * 31, "long"), (".ali", "chars"), ("ali.", "chars"), ("ali..veli", "chars"),
    ("admin", "reserved"), ("destek", "reserved"), ("postmaster", "reserved"), ("no-reply", "reserved"),
    ("fatura-abc123", "reserved"), ("ekstre", "reserved"),
])
def test_rule_violations(on, name, code):
    from app import mail_name

    ok, _, reason = mail_name.available(name)
    assert not ok and reason == code


def test_valid_names(on):
    from app import mail_name

    for n in ("ali", "ali.veli", "ali_veli-2", "a" * 30, "x.y.z"):
        assert mail_name.available(n)[0], n


def test_claim_unique_case_insensitive(owner, other):
    from app import mail_name

    assert mail_name.claim(owner["id"], "Deniz") == "deniz"
    ok, _, code = mail_name.available("DENIZ", other["id"])
    assert not ok and code == "taken"
    with pytest.raises(mail_name.NameError_) as e:
        mail_name.claim(other["id"], "deniz")
    assert e.value.code == "taken"
    assert mail_name.available("deniz", owner["id"])[2] == "same"


def test_disabled_without_domain(tmpdb, monkeypatch):
    from app import auth, mail_name

    monkeypatch.delenv("HESAPKITAP_MAIL_DOMAIN", raising=False)
    u = auth.create_user("ece@ornek.com", "ece-sifre-12345")
    assert not mail_name.enabled()
    with pytest.raises(mail_name.NameError_):
        mail_name.claim(u["id"], "ece")


def test_change_once_and_old_name_kept_90_days(owner, other, monkeypatch):
    from datetime import timedelta

    from app import inbound, mail_name

    mail_name.claim(owner["id"], "deniz")
    mail_name.claim(owner["id"], "deniz.k")
    assert mail_name.current(owner["id"]) == "deniz.k"
    with pytest.raises(mail_name.NameError_) as e:
        mail_name.claim(owner["id"], "deniz.kaya")
    assert e.value.code == "limit"
    # eski ad başkasına verilmez, hâlâ aynı deftere gelir
    assert mail_name.available("deniz", other["id"])[2] == "taken"
    assert inbound.resolve(f"deniz@{DOMAIN}")["id"] == owner["id"]
    assert inbound.resolve(f"deniz.k@{DOMAIN}")["id"] == owner["id"]
    # 90 gün sonra serbest
    real = mail_name._now
    monkeypatch.setattr(mail_name, "_now", lambda: real() + timedelta(days=91))
    assert inbound.resolve(f"deniz@{DOMAIN}") is None
    assert mail_name.claim(other["id"], "deniz") == "deniz"


def test_suggestions(owner):
    from app import mail_name

    s = mail_name.suggestions("deniz.kaya@ornek.com", book_id=owner["id"])
    assert 3 <= len(s) <= 5
    assert s[:3] == ["deniz", "deniz.k", "denizkaya"]
    mail_name.claim(owner["id"], "deniz")
    assert "deniz" not in mail_name.suggestions("deniz.kaya@ornek.com", book_id=999)
    assert all(mail_name.available(x)[0] for x in mail_name.suggestions("deniz.kaya@ornek.com", book_id=999))


# ---------------------------------------------------------------- gelen posta

def test_personal_address_only_on_configured_domain(owner):
    from app import inbound, mail_name

    mail_name.claim(owner["id"], "deniz")
    assert inbound.resolve(f"Deniz@{DOMAIN}")["id"] == owner["id"]
    assert inbound.resolve("deniz@baska.app") is None
    assert inbound.resolve(f"yok@{DOMAIN}") is None


def test_unknown_card_waits_then_approve(owner):
    from app import db, inbound, mail_approval, mail_name, system

    mail_name.claim(owner["id"], "deniz")
    to = f"deniz@{DOMAIN}"
    r = inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), to, verify=signed("vakifbank.com.tr"))
    assert r["status"] == "ok" and "onay bekliyor" in r["detail"]
    with db.use_space(system.user_db_path(owner), owner):
        assert db.list_statements() == []
        items = mail_approval.pending()
        assert len(items) == 1 and items[0]["doc_type"] == "ekstre" and "senin mi" in items[0]["question"]
        assert items[0]["label"].startswith("•••• ") and items[0]["amount"]
        out = mail_approval.approve(items[0]["id"])
        assert "ekstresi" in out["message"]
        assert len(db.list_statements()) == 1 and mail_approval.pending() == []
        assert mail_approval.known("card:1234")
    # başka bir kart (••••9012) yine sorulur
    r2 = inbound.handle(_raw(attach=pdf("vakifbank_b.txt"), mid="<2@x>"), to, verify=signed("vakifbank.com.tr"))
    assert "•••• 9012" in r2["detail"] and "onay bekliyor" in r2["detail"]


def test_known_card_processed_directly(owner):
    """Defterde ekstresi olan kart bilinir; onaylanmış kart da: kişisel adreste onay sorulmaz."""
    from app import db, inbound, mail_approval, mail_name, system

    mail_name.claim(owner["id"], "deniz")
    inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), inbound.address(owner), verify=signed("vakifbank.com.tr"))
    with db.use_space(system.user_db_path(owner), owner):
        assert mail_approval.known("card:1234") and not mail_approval.known("card:9012")
        mail_approval.trust("card:9012")
    r = inbound.handle(_raw(attach=pdf("vakifbank_b.txt"), mid="<k2@x>"), f"deniz@{DOMAIN}",
                       verify=signed("vakifbank.com.tr"))
    assert r["status"] == "ok" and "ekstresi" in r["detail"] and "onay" not in r["detail"]
    with db.use_space(system.user_db_path(owner), owner):
        assert mail_approval.pending() == [] and len(db.list_statements()) == 2


def test_reject_deletes_and_blocks(owner):
    from app import db, inbound, mail_approval, mail_name, system

    mail_name.claim(owner["id"], "deniz")
    to = f"deniz@{DOMAIN}"
    inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), to, verify=signed("vakifbank.com.tr"))
    with db.use_space(system.user_db_path(owner), owner):
        pid = mail_approval.pending()[0]["id"]
        assert mail_approval.reject(pid)["removed"] == 1
        assert mail_approval.pending() == []
        with pytest.raises(KeyError):
            mail_approval.approve(pid)
    with db.use_space(system.user_db_path(owner), owner):
        # aynı kartın sonraki belgesi sessizce atlanır, yeniden sorulmaz
        held = []
        msg = mail_approval.make_gate("vakifbank.com.tr", held)({"type": "ekstre", "card_no": "4543********1234"},
                                                                "sonraki.pdf", b"%PDF-1.4")
        assert "engellendi" in msg and held == []
    r = inbound.handle(_raw(attach=pdf("vakifbank_a.txt"), mid="<r2@x>"), to, verify=signed("vakifbank.com.tr"))
    assert "engellendi" in r["detail"]
    with db.use_space(system.user_db_path(owner), owner):
        assert db.list_statements() == [] and mail_approval.pending() == []


def test_pending_expires_after_30_days(owner, monkeypatch):
    from datetime import timedelta

    from app import db, inbound, mail_approval, mail_name, system

    mail_name.claim(owner["id"], "deniz")
    inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), f"deniz@{DOMAIN}", verify=signed("vakifbank.com.tr"))
    real = mail_approval._now
    monkeypatch.setattr(mail_approval, "_now", lambda: real() + timedelta(days=31))
    with db.use_space(system.user_db_path(owner), owner):
        assert mail_approval.pending() == []


def test_own_forward_needs_no_approval(owner):
    from app import db, inbound, mail_approval, mail_name, system

    mail_name.claim(owner["id"], "deniz")
    r = inbound.handle(_raw("deniz.kaya@ornek.com", attach=pdf("vakifbank_a.txt")), f"deniz@{DOMAIN}",
                       verify=signed("ornek.com"))
    assert r["status"] == "ok" and "onay" not in r["detail"]
    with db.use_space(system.user_db_path(owner), owner):
        assert len(db.list_statements()) == 1 and mail_approval.pending() == []


def test_coded_address_unchanged(owner):
    """fatura- adresi: ilk kez görülen kart bile eskisi gibi doğrudan işlenir."""
    from app import db, inbound, mail_approval, system

    r = inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), inbound.address(owner), verify=signed("vakifbank.com.tr"))
    assert r["status"] == "ok" and "ekstresi" in r["detail"]
    with db.use_space(system.user_db_path(owner), owner):
        assert len(db.list_statements()) == 1 and mail_approval.pending() == []


def test_notification_needs_trusted_bank_on_personal(owner):
    from app import inbound, mail_name

    mail_name.claim(owner["id"], "deniz")
    r = inbound.handle(_raw(body="Hesabınızdan 100 TL çıkış"), f"deniz@{DOMAIN}", verify=signed("vakifbank.com.tr"))
    assert r["status"] == "skip" and "onaylı belge yok" in r["detail"]


def test_gmail_forwarding_confirmation_captured(owner):
    from app import db, inbound, mail_approval, mail_name, system

    mail_name.claim(owner["id"], "deniz")
    body = ("deniz.kaya@ornek.com has requested to automatically forward mail to your email address.\n"
            "Confirmation code: 123456789\n"
            "https://mail-settings.google.com/mail/vf-%5Bdeneme%5D-ornek\n")
    r = inbound.handle(_raw("forwarding-noreply@google.com", body=body), f"deniz@{DOMAIN}", verify=signed("google.com"))
    assert r["kind"] == "gmail_onay" and r["status"] == "ok"
    with db.use_space(system.user_db_path(owner), owner):
        c = mail_approval.gmail_confirmation()
        assert c["code"] == "123456789" and c["link"].startswith("https://mail-settings.google.com/")
        assert c["from_email"] == "deniz.kaya@ornek.com"


# ---------------------------------------------------------------- API

@pytest.fixture
def api(client, monkeypatch, on):
    from app import auth
    from app.api import main

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    main._name_checks.clear()
    auth.create_admin("deniz.kaya@ornek.com", "deniz-sifre-12345")
    assert client.post("/api/auth/login", json={"username": "deniz.kaya@ornek.com",
                                                "password": "deniz-sifre-12345"}).status_code == 200
    return client


def test_api_flow(api):
    s = api.get("/api/mail/name").json()
    assert s["enabled"] and s["name"] is None and s["domain"] == DOMAIN and len(s["suggestions"]) >= 3
    assert api.get("/api/mail/name", params={"check": "admin"}).json()["reason"] == "reserved"
    assert api.get("/api/mail/name", params={"check": "Deniz"}).json()["available"] is True
    r = api.post("/api/mail/name", json={"name": "Deniz"}).json()
    assert r["address"] == f"deniz@{DOMAIN}" and r["can_change"] is True
    r = api.post("/api/mail/name", json={"name": "deniz.k"}).json()
    assert r["name"] == "deniz.k" and r["can_change"] is False
    bad = api.post("/api/mail/name", json={"name": "deniz.ka"})
    assert bad.status_code == 409 and bad.headers["X-Reason"] == "limit"
    assert api.get("/api/mail/pending").json() == {"items": [], "gmail_confirmation": None}
    assert api.post("/api/mail/pending/999/approve").status_code == 404


def test_api_rate_limit(api):
    codes = [api.get("/api/mail/name", params={"check": f"ad{i:03d}"}).status_code for i in range(32)]
    assert codes[:30] == [200] * 30 and codes[-1] == 429


def test_api_disabled(api, monkeypatch):
    monkeypatch.delenv("HESAPKITAP_MAIL_DOMAIN")
    assert api.get("/api/mail/name").json() == {"enabled": False}
