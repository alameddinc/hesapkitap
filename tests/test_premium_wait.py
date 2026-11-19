"""Premium bekliyor: ücretsiz pakette kişisel adrese gelen banka belgesi okunmadan saklanır; sınırlar, temizlik, uyarı,
"kendim yükleyeyim", Premium'a geçişte okuma. Kişiler, adresler ve belgeler uydurmadır."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from tests.conftest import fixture_bytes as _fixture

DOMAIN = "ornek.app"


def pdf(name: str) -> bytes:
    return b"%PDF-1.4\n" + _fixture(name)


def _raw(sender="ekstre@vakifbank.com.tr", attach: bytes | None = None, mid="<1@x>", body="Ekstreniz ektedir") -> bytes:
    m = EmailMessage()
    m["From"] = f"Banka <{sender}>"
    m["To"] = f"selin@{DOMAIN}"
    m["Subject"] = "Ekstre"
    m["Date"] = "Tue, 20 Oct 2026 10:00:00 +0300"
    m["Message-ID"] = mid
    m.set_content(body)
    if attach:
        m.add_attachment(attach, maintype="application", subtype="pdf", filename="ekstre.pdf")
    return m.as_bytes()


def signed(*domains):
    return lambda raw: set(domains)


@pytest.fixture
def free(tmpdb, monkeypatch):
    from app import auth, mail_name

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("HESAPKITAP_MAIL_DOMAIN", DOMAIN)
    u = auth.create_user("selin.ak@ornek.com", "selin-sifre-12345")
    assert mail_name.current(u["id"]) == "selin"  # kayıtta verildi
    return u


def space(u):
    from app import db, system

    return db.use_space(system.user_db_path(u), u)


def _fake_doc(i: int, card: str = "4543********1234") -> bytes:
    """Metin çıkarmada kart numarası görünen uydurma PDF (fixture: gerçek PDF değil, metni sahte okuyucu verir)."""
    return f"%PDF-1.4\nUydurma ekstre {i}\nKart No : {card}\n".encode()


@pytest.fixture
def fake_text(monkeypatch):
    """extract_text: uydurma baytların metnini döndür (gerçek PDF ayrıştırma yok)."""
    import app.parsers as parsers

    real = parsers.extract_text

    def fake(data, pw=None):
        if data.startswith(b"%PDF-1.4\nUydurma"):
            return data.decode()
        return real(data, pw)

    monkeypatch.setattr(parsers, "extract_text", fake)


def test_free_user_bank_pdf_waits_unread(free, monkeypatch):
    from app import db, doc_reads, inbound, premium_wait, push

    sent = []
    monkeypatch.setattr(push, "notify", lambda uid, items, **k: sent.append((uid, items)))
    called = []
    monkeypatch.setattr(doc_reads, "charge", lambda *a, **k: called.append(a))
    r = inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), f"selin@{DOMAIN}", verify=signed("vakifbank.com.tr"))
    assert r["status"] == "ok" and r["kind"] == "bekliyor" and "Premium bekliyor" in r["detail"]
    assert not called  # okunmadı, hak düşmedi
    with space(free):
        assert db.list_statements() == []
        items = premium_wait.items()
        assert len(items) == 1 and items[0]["bank"] and items[0]["expires_at"]
        assert db.list_files("uploads")  # dosya storage'da
    assert sent and "ekstren geldi" in sent[0][1][0]["body"]


def test_free_user_bill_and_notification_not_kept(free, monkeypatch):
    from app import inbound, premium_wait

    r = inbound.handle(_raw("efatura@enerjisa.com.tr", attach=b"%PDF-1", mid="<f1@x>"), f"selin@{DOMAIN}",
                       verify=signed("enerjisa.com.tr"))
    assert r["status"] == "skip" and "Premium" in r["detail"]
    r = inbound.handle(_raw(mid="<n1@x>"), f"selin@{DOMAIN}", verify=signed("vakifbank.com.tr"))  # PDF'siz bildirim
    assert r["status"] == "skip"
    r = inbound.handle(_raw("x@kotu.com", attach=pdf("vakifbank_a.txt"), mid="<k1@x>"), f"selin@{DOMAIN}",
                       verify=signed("kotu.com"))
    assert r["status"] == "skip"
    with space(free):
        assert premium_wait.items() == []


def test_coded_address_free_unchanged(free):
    from app import inbound, premium_wait

    r = inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), inbound.address(free), verify=signed("vakifbank.com.tr"))
    assert r["status"] == "skip" and "Premium" in r["detail"]
    with space(free):
        assert premium_wait.items() == []


def test_limits_per_card_and_per_user(free, fake_text):
    from app import db, premium_wait

    with space(free):
        for i in range(4):
            premium_wait.stash(free["id"], False, f"e{i}.pdf", _fake_doc(i), "vakifbank.com.tr")
        same = premium_wait.items()
        assert len(same) == 3 and all(x["label"] == "•••• 1234" for x in same)
        assert {x["filename"] for x in same} == {"e1.pdf", "e2.pdf", "e3.pdf"}  # en eski silindi
        for i in range(10):
            premium_wait.stash(free["id"], False, f"k{i}.pdf", _fake_doc(100 + i, f"5400********{1000 + i}"),
                               "halkbank.com.tr")
        assert premium_wait.count() == 10
        names = {x["filename"] for x in premium_wait.items()}
        assert "k9.pdf" in names and "e1.pdf" not in names
        assert len(db.list_files("uploads")) == 10  # silinenler storage'dan da gitti


def test_global_cap_drops_oldest(free, fake_text, monkeypatch):
    from app import auth, db, premium_wait, system

    other = auth.create_user("kaan@ornek.com", "kaan-sifre-12345")
    with space(free):
        premium_wait.stash(free["id"], False, "eski.pdf", _fake_doc(1), "vakifbank.com.tr")
    size = len(_fake_doc(1))
    monkeypatch.setenv("HESAPKITAP_PENDING_MAX_MB", str((size * 1.5) / 1024 / 1024))
    with db.use_space(system.user_db_path(other), other):
        premium_wait.stash(other["id"], False, "yeni.pdf", _fake_doc(2, "5400********9012"), "halkbank.com.tr")
        assert [x["filename"] for x in premium_wait.items()] == ["yeni.pdf"]
    with space(free):
        assert premium_wait.items() == [] and db.list_files("uploads") == []  # başka defterde, storage'dan da silindi


def test_expiry_and_warning(free, fake_text, monkeypatch):
    from app import db, premium_wait, push

    sent = []
    monkeypatch.setattr(push, "notify", lambda uid, items, **k: sent.append(items[0]["body"]))
    with space(free):
        premium_wait.stash(free["id"], False, "a.pdf", _fake_doc(1), "vakifbank.com.tr")
        premium_wait.stash(free["id"], False, "b.pdf", _fake_doc(2, "5400********9012"), "vakifbank.com.tr")
    now = datetime.now(timezone.utc)
    r = premium_wait.tick(now + timedelta(days=84))
    assert r["warned"] == 1 and r["removed"] == 0 and "2 ekstren 7 gün içinde silinecek" in sent[-1]
    assert premium_wait.tick(now + timedelta(days=85))["warned"] == 0  # bir kez
    assert premium_wait.tick(now + timedelta(days=91))["removed"] == 2
    with space(free):
        assert premium_wait.items() == [] and db.list_files("uploads") == []


def test_self_upload_charges_free_read(free, monkeypatch):
    from app import db, doc_reads, inbound, premium_wait

    inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), f"selin@{DOMAIN}", verify=signed("vakifbank.com.tr"))
    real, charged = doc_reads.charge, []
    monkeypatch.setattr(doc_reads, "charge", lambda *a, **k: (charged.append(a), real(*a, **k))[1])
    with space(free):
        pid = premium_wait.items()[0]["id"]
        msg = premium_wait.self_upload(pid, free)
        assert "ekstresi" in msg and len(db.list_statements()) == 1 and premium_wait.items() == []
        assert len(charged) == 1  # ücretsiz okuma hakkından düştü
        with pytest.raises(KeyError):
            premium_wait.self_upload(pid, free)


def test_self_upload_quota_full_keeps_doc(free, monkeypatch):
    from app import doc_reads, inbound, premium_wait

    inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), f"selin@{DOMAIN}", verify=signed("vakifbank.com.tr"))

    def full(*a, **k):
        raise doc_reads.QuotaError("Bu ayki okuma hakkın doldu", 5, True)

    monkeypatch.setattr(doc_reads, "check", full)
    with space(free):
        pid = premium_wait.items()[0]["id"]
        with pytest.raises(doc_reads.QuotaError):
            premium_wait.self_upload(pid, free)
        assert len(premium_wait.items()) == 1  # belge bekliyor


def test_premium_upgrade_releases_with_card_approval(free):
    from app import db, inbound, mail_approval, premium_wait, system

    inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), f"selin@{DOMAIN}", verify=signed("vakifbank.com.tr"))
    until = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")
    system.update_user(free["id"], plan="premium", plan_until=until, plan_source="manual")
    r = premium_wait.release(free["id"])
    assert r["processed"] == 1 and r["held"] == 1  # ilk kez görülen kart: onay kuyruğuna
    u = system.user(free["id"])
    with space(u):
        assert premium_wait.items() == []
        assert len(mail_approval.pending()) == 1 and db.list_statements() == []


def test_premium_user_unchanged(free):
    from app import db, inbound, mail_approval, premium_wait, system

    until = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")
    system.update_user(free["id"], plan="premium", plan_until=until, plan_source="manual")
    r = inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), f"selin@{DOMAIN}", verify=signed("vakifbank.com.tr"))
    assert "onay bekliyor" in r["detail"]  # premium: bekletme yok, yeni kart onayı
    with space(system.user(free["id"])):
        assert premium_wait.items() == [] and len(mail_approval.pending()) == 1


def test_approval_reject_removes_storage_file(free):
    from app import db, inbound, mail_approval, system

    until = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")
    system.update_user(free["id"], plan="premium", plan_until=until, plan_source="manual")
    inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), f"selin@{DOMAIN}", verify=signed("vakifbank.com.tr"))
    with space(system.user(free["id"])):
        assert db.list_files("uploads")
        mail_approval.reject(mail_approval.pending()[0]["id"])
        assert db.list_files("uploads") == []


def _premium(uid):
    from app import system

    until = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")
    system.update_user(uid, plan="premium", plan_until=until, plan_source="manual")
    return system.user(uid)


def test_encrypted_waits_for_password_then_reads(free, fake_text, monkeypatch):
    """Şifreli bekleyen: Premium geçişinde silinmez, "şifre bekliyor"; şifre girilince hatırlanır ve okunur."""
    from app import db, mailbox, premium_wait, push

    sent = []
    monkeypatch.setattr(push, "notify", lambda uid, items, **k: sent.append(items[0]["body"]))
    with space(free):
        premium_wait.stash(free["id"], False, "sifreli.pdf", _fake_doc(1), "halkbank.com.tr")
        premium_wait.stash(free["id"], False, "acik.pdf", _fake_doc(2, "5400********9012"), "halkbank.com.tr")

    def handle(name, data, gate=None):
        if name == "sifreli.pdf" and "1234" not in db.pdf_passwords():
            raise mailbox.MailError(f"{name}: PDF şifreli; Ekstreler'den bir kez elle yükleyip şifreyi hatırlat")
        return f"💳 Halkbank {name} ekstresi kaydedildi"

    monkeypatch.setattr(mailbox, "handle_pdf", handle)
    u = _premium(free["id"])
    r = premium_wait.release(free["id"])
    assert r["processed"] == 1 and r["locked"] == 1
    assert sent[-1] == "1 ekstren okundu · 1 ekstren şifreli, şifreyi bir kez gir"
    with space(u):
        items = premium_wait.items()
        assert [(x["filename"], x["state"]) for x in items] == [("sifreli.pdf", "sifre")]
        import app.parsers as parsers

        real = parsers.extract_text
        monkeypatch.setattr(parsers, "extract_text", lambda d, pw=None: real(d, pw) if pw == "1234" else (_ for _ in ()).throw(ValueError("şifreli")))
        with pytest.raises(ValueError):
            premium_wait.unlock(items[0]["id"], "yanlis", u)
        assert premium_wait.items()[0]["state"] == "sifre"
        msg = premium_wait.unlock(items[0]["id"], "1234", u)
        assert "kaydedildi" in msg and premium_wait.items() == [] and "1234" in db.pdf_passwords()
        assert db.list_files("uploads") == []
    assert premium_wait.release(free["id"])["processed"] == 0  # şifre bekleyen yeniden denenmez (zaten yok)


def test_release_push_counts_held(free, monkeypatch):
    from app import inbound, premium_wait, push

    sent = []
    monkeypatch.setattr(push, "notify", lambda uid, items, **k: sent.append(items[0]["body"]))
    inbound.handle(_raw(attach=pdf("vakifbank_a.txt")), f"selin@{DOMAIN}", verify=signed("vakifbank.com.tr"))
    _premium(free["id"])
    premium_wait.release(free["id"])
    assert sent[-1] == "1 belge onayını bekliyor"


def test_encrypted_self_upload_marks_password(free, fake_text, monkeypatch):
    from app import mailbox, premium_wait

    with space(free):
        pid = premium_wait.stash(free["id"], False, "sifreli.pdf", _fake_doc(1), "halkbank.com.tr")["id"]

        def locked(name, data, gate=None):
            raise mailbox.MailError("sifreli.pdf: PDF açılamadı; şifresi Ayarlar → PDF şifreleri'nde kayıtlı mı?")

        monkeypatch.setattr(mailbox, "handle_pdf", locked)
        with pytest.raises(mailbox.MailError):
            premium_wait.self_upload(pid, free)
        assert premium_wait.items()[0]["state"] == "sifre"
