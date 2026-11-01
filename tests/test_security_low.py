"""Güvenlik denetiminin düşük bulguları (Ek B: D3, D5, D6, D7, D8, D12). Veriler uydurmadır."""
import base64
import io
import os
import socket
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------- D3: oturumsuz istekte yönetici sayılmaz

def test_is_admin_without_user_depends_on_context(tmpdb, monkeypatch):
    from app import config

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    assert config.is_admin() is True  # istek dışında (zamanlayıcı ana alanı, komut satırı)
    tok = config.mark_request()
    try:
        assert config.is_admin() is False  # oturumsuz HTTP isteği
        monkeypatch.setenv("HESAPKITAP_NO_AUTH", "1")
        assert config.is_admin() is True  # tek kullanıcılı kurulum açıkça işaretli
    finally:
        config._in_request.reset(tok)
    with tmpdb.use_space(None, {"id": 5, "role": "user"}):
        assert config.is_admin() is False
    with tmpdb.use_space(None, {"id": 1, "role": "admin"}):
        assert config.is_admin() is True


def test_public_endpoint_is_not_admin(client, monkeypatch):
    """Oturumsuz bir uçta (ör. /api/auth/*) config.is_admin() yanlışlıkla True dönmesin."""
    from app import auth, config
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth.create_admin("admin", "yonetici-sifre-1")
    seen = []

    @app.get("/api/auth/_test_admin_probe")
    def probe():
        seen.append(config.is_admin())
        return {"ok": True}

    try:
        assert TestClient(app).get("/api/auth/_test_admin_probe").status_code == 200
        assert seen == [False]
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/api/auth/_test_admin_probe"]


# ---------------------------------------------------------------- D5: DKIM l= etiketli imza sayılmaz

def _dkim_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                          serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return pem, b"v=DKIM1; k=rsa; p=" + base64.b64encode(pub)


def test_dkim_length_limited_signature_is_not_trusted():
    import dkim

    from app import inbound

    pem, txt = _dkim_key()
    dns = lambda name, timeout=5: txt  # noqa: E731
    msg = (b"From: bildirim@ornekbanka.test\r\nTo: fatura-abc234@posta.ornek.test\r\nSubject: Ekstre\r\n"
           b"Message-ID: <d5@ornek.test>\r\n\r\nEkstreniz ektedir.\r\n")
    plain = dkim.sign(msg, b"s1", b"ornekbanka.test", pem, include_headers=[b"from", b"to", b"subject"]) + msg
    assert inbound._dkim_domains(plain, dnsfunc=dns) == {"ornekbanka.test"}
    limited = dkim.sign(msg, b"s1", b"ornekbanka.test", pem, include_headers=[b"from", b"to", b"subject"], length=True)
    # imza gövdenin yalnız ilk baytlarını kapsıyor: sonuna eklenen sahte ek imzayı bozmaz → güvenilmez
    forged = limited + msg + b"--ek\r\nContent-Type: application/pdf\r\n\r\nSAHTE\r\n"
    assert dkim.DKIM(forged).verify(dnsfunc=dns)  # kütüphane geçerli sayıyor
    assert inbound._dkim_domains(forged, dnsfunc=dns) == set()


# ---------------------------------------------------------------- D6: IMAP sunucusu iç ağa çıkamaz

@pytest.mark.parametrize("addr", ["127.0.0.1", "10.1.2.3", "192.168.1.5", "169.254.169.254", "100.64.0.1", "::1",
                                  "fd00::1", "::ffff:10.0.0.1"])
def test_imap_host_on_private_network_is_rejected_for_users(tmpdb, monkeypatch, addr):
    import imaplib

    from app import mailbox

    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(fam, socket.SOCK_STREAM, 6, "", (addr, 993))])
    called = []
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *a, **k: called.append(a))
    with tmpdb.use_space(None, {"id": 7, "role": "user"}):
        tmpdb.set_setting("cfg:MAIL_IMAP_HOST", "posta.ornek.test")
        with pytest.raises(mailbox.MailError, match="iç ağ"):
            mailbox._connect()
    assert called == []  # bağlantı hiç denenmedi


def test_imap_public_host_and_admin_local_host_allowed(tmpdb, monkeypatch):
    import imaplib

    from app import mailbox

    class Fake:
        def __init__(self, host, port, timeout=None):
            self.host = host

        def login(self, u, p):
            return "OK", []

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Fake)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                                  ("93.184.216.34", 993))])
    with tmpdb.use_space(None, {"id": 7, "role": "user"}):
        tmpdb.set_setting("cfg:MAIL_IMAP_HOST", "imap.ornek.test")
        assert mailbox._connect().host == "imap.ornek.test"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                                  ("192.168.1.5", 993))])
    with tmpdb.use_space(None, {"id": 1, "role": "admin"}):  # kendi sunucusunda yerel posta sunucusu
        tmpdb.set_setting("cfg:MAIL_IMAP_HOST", "posta.lan")
        assert mailbox._connect().host == "posta.lan"


def test_imap_unknown_host_message(tmpdb, monkeypatch):
    from app import mailbox

    def boom(*a, **k):
        raise socket.gaierror("yok")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with tmpdb.use_space(None, {"id": 7, "role": "user"}):
        with pytest.raises(mailbox.MailError, match="bulunamadı"):
            mailbox._check_host("olmayan.ornek.test")


# ---------------------------------------------------------------- D7: geri yüklemede yabancı nesne ve sahte dizin

def _backup_db(tmp_path, extra_sql: str = "") -> bytes:
    from app import db

    path = tmp_path / "yedek.db"
    with db.use_space(str(path)):
        db.init_db()
    c = sqlite3.connect(path)
    if extra_sql:
        c.executescript(extra_sql)
    c.commit()
    c.close()
    return path.read_bytes()


@pytest.mark.parametrize("sql", [
    "CREATE TRIGGER t1 AFTER INSERT ON entries BEGIN UPDATE entries SET amount = 0; END;",
    "CREATE VIEW v1 AS SELECT * FROM entries;",
])
def test_restore_rejects_triggers_and_views(client, tmpdb, tmp_path, sql):
    raw = _backup_db(tmp_path, sql)
    r = client.post("/api/settings/backup/restore", files={"file": ("x.db", raw, "application/octet-stream")})
    assert r.status_code == 422 and "tetikleyici" in r.json()["detail"]


def test_restore_rebuilds_file_index_so_quota_cannot_be_faked(client, tmpdb, tmp_path, monkeypatch):
    """Yedekte stored_files'a 0 boyutlu sahte satır yazılıp aynı ad zip'e konursa dosya sınırdan kaçardı."""
    from app import backup, db, plans

    name = "a" * 64 + ".jpg"
    big = b"\xff\xd8" + os.urandom(5000)
    raw = _backup_db(tmp_path, f"INSERT INTO stored_files (kind, name, size) VALUES ('receipts', '{name}', 0);"
                               "INSERT INTO stored_files (kind, name, size) VALUES ('receipts', '" + "b" * 64 + ".jpg', -999999);")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(backup.DB_NAME, raw)
        z.writestr(f"receipts/{name}", big)
    monkeypatch.setattr(plans, "storage_limit", lambda u: 1000)  # sınır dosyadan küçük
    r = backup.restore(buf.getvalue(), "yedek.zip")
    assert r["files"] == 0  # sınıra takıldı, saklanmadı
    assert db.storage_used() == 0
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM stored_files").fetchone()[0] == 0  # sahte satırlar gitti


def test_restore_keeps_existing_file_index(client, tmpdb, tmp_path):
    from app import backup, db

    name = "c" * 64 + ".jpg"
    assert db.store_file("receipts", name, b"\xff\xd8" + b"x" * 300)
    before = db.storage_used()
    r = backup.restore(_backup_db(tmp_path), "yedek.db")
    assert r["entries"] == 0
    assert db.storage_used() == before and db.file_indexed("receipts", name)  # depodaki dosya sayılmaya devam eder


# ---------------------------------------------------------------- D8: demo, bağlantı başına yeni kopya sınırı

def test_demo_new_sandbox_limit_per_ip(tmpdb, monkeypatch):
    from app import demo_mode
    from app.api.main import app

    monkeypatch.setenv("HESAPKITAP_DEMO", "1")
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setattr(demo_mode, "NEW_PER_IP", 3)
    demo_mode._new_by_ip.clear()
    codes = [TestClient(app).get("/api/summary").status_code for _ in range(5)]  # her biri çerezsiz: yeni kopya ister
    assert codes == [200, 200, 200, 429, 429]
    keep = TestClient(app)
    demo_mode._new_by_ip.clear()
    assert keep.get("/api/summary").status_code == 200
    for _ in range(5):  # çerezini taşıyan ziyaretçi sınırdan etkilenmez
        assert keep.get("/api/summary", headers={"x-demo-sid": keep.cookies.get(demo_mode.COOKIE)}).status_code == 200
    assert len(os.listdir(demo_mode._sandbox_dir())) == 4
    page = TestClient(app)
    monkeypatch.setattr(demo_mode, "NEW_PER_IP", 1)
    assert page.get("/").status_code in (200, 404)  # sayfa açılır (ya da derleme yoksa 404), kopya açılmaz
    assert demo_mode.COOKIE not in page.cookies
    demo_mode._new_by_ip.clear()


# ---------------------------------------------------------------- D12: silme kenar durumları

def test_removed_member_gets_deletion_record_and_inbound_unlinked(client, monkeypatch):
    from app import auth, deletion, inbound, system
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    inv = client.post("/api/book/invites", json={"note": "uye"}).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"code": inv["code"], "username": "uye1", "password": "uye1-sifre-12345"}).status_code == 200
    uid = system.user_by_name("uye1")["id"]
    addr = inbound.address({**system.user(uid), "own_book": True})
    assert client.delete(f"/api/book/members/{uid}").json()["ok"]
    rec = deletion.record(uid)
    assert rec and rec["reason"] == "member_removed" and rec["purged_at"]
    assert rec["email_hash"] == deletion.email_hash("uye1")
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM inbound WHERE user_id = ?", (uid,)).fetchone()[0] == 0
        row = c.execute("SELECT user_id, revoked FROM inbound WHERE token = ?",
                        (addr.split("@")[0].removeprefix("fatura-"),)).fetchone()
    assert tuple(row) == (0, 1)  # kod kalır (yeniden verilmez), hesapla bağı yok
    assert inbound.resolve(addr) is None


def test_reapply_deletions_after_restoring_old_system(client, tmp_path, monkeypatch):
    """Eski yedekten geri dönünce, yedekten sonra silinen hesaplar yeniden silinir / kapanır."""
    import shutil
    from datetime import datetime, timedelta, timezone

    from app import auth, deletion, system

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth.create_admin("admin", "yonetici-sifre-1")
    a = auth.create_user("silinen@ornek.test", "silinen-sifre-12345")
    b = auth.create_user("bekleyen@ornek.test", "bekleyen-sifre-12345")
    c = auth.create_user("kalan@ornek.test", "kalan-sifre-12345")
    old = tmp_path / "eski-yedek-system.db"
    shutil.copy(system.SYSTEM_DB if os.path.exists(system.SYSTEM_DB) else system._path(), old)  # yedek anı
    deletion.request_delete(system.user(a["id"]))
    deletion.purge(system.user(a["id"]))
    deletion.request_delete(system.user(b["id"]))
    newer = tmp_path / "system-once.db"
    shutil.copy(system._path(), newer)
    shutil.copy(old, system._path())  # geri yükleme: üç hesap da "canlı"
    system._ready.discard(system._path())
    assert system.user(a["id"]) and not system.user(b["id"])["deleted_at"]
    dry = deletion.reapply(str(newer))
    assert dry["purge"] == [a["id"]] and dry["pending"] == [b["id"]]
    assert system.user(a["id"])  # kuru çalıştırma dokunmaz
    deletion.reapply(str(newer), apply=True)
    assert system.user(a["id"]) is None
    assert system.user(b["id"])["deleted_at"] and deletion.record(b["id"])
    assert system.user(c["id"]) and not system.user(c["id"])["deleted_at"]
    assert deletion.reapply(str(newer))["purge"] == []  # ikinci çalıştırma bir şey yapmaz
    assert deletion.purge_due(datetime.now(timezone.utc) + timedelta(days=8)) == [b["id"]]
