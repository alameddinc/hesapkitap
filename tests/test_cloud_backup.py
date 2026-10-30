"""Sunucu geneli şifreli yedek (app/cloud_backup.py), 30 gün kuralı, silinen hesap klasörleri, ekstre PDF temizliği."""
import io
import json
import os
import sqlite3
import tarfile
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import fixture_bytes

KEY = "test-yedek-anahtari-0123456789"
NOW = datetime(2026, 10, 28, 0, 30, tzinfo=timezone.utc)


@pytest.fixture()
def server(tmp_path, tmp_path_factory, monkeypatch):
    """Çok kullanıcılı veri klasörü: system.db + ana ekstre.db + iki kullanıcı (+ atlanması gerekenler)."""
    from app import db, system

    for k in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_KEY", "BACKUP_S3_SECRET", "BACKUP_S3_PREFIX",
              "HESAPKITAP_BACKUP_KEEP_DAYS"):
        monkeypatch.delenv(k, raising=False)
    out = tmp_path_factory.mktemp("yedekler")
    monkeypatch.setenv("HESAPKITAP_BACKUP_DIR", str(out))
    monkeypatch.setenv("HESAPKITAP_BACKUP_KEY", KEY)
    users = []
    for name in ("ayse", "mehmet"):
        u = system.create_user(name, "x")
        with db.use_space(system.user_db_path(u), u):
            db.init_db()
            db.create_entry({"entry_date": "2026-10-01", "description": f"{name} market", "amount": 10, "kind": "gider",
                          "category": "Market & Gıda"})
            os.makedirs(db.upload_dir(), exist_ok=True)
            open(os.path.join(db.upload_dir(), f"{name}.pdf"), "wb").write(b"%PDF " + name.encode())
            os.makedirs(db.bill_dir(), exist_ok=True)
            open(os.path.join(db.bill_dir(), "fatura.jpg"), "wb").write(b"jpg")
            from app import backup

            backup.make_backup()  # kullanıcının kendi günlük yedeği: sunucu arşivine girmemeli
        users.append(u)
    root = tmp_path
    os.makedirs(root / "users" / "_silinen" / "9-eski-1700000000", exist_ok=True)
    (root / "users" / "_silinen" / "9-eski-1700000000" / "ekstre.db").write_bytes(b"silinmis")
    os.makedirs(root / "backups", exist_ok=True)
    (root / "backups" / "predeploy-20261028-system.db").write_bytes(b"x")
    (root / "ekstre.db-journal").write_bytes(b"")
    return {"root": root, "out": out, "users": users}


def _names(path) -> list[str]:
    from app import cloud_backup as cb

    with open(path, "rb") as f:
        with tarfile.open(fileobj=io.BufferedReader(cb.DecryptReader(f, KEY)), mode="r|") as t:
            return [m.name for m in t]


def test_run_creates_encrypted_archive_that_verifies(server):
    from app import cloud_backup as cb

    r = cb.run(NOW)
    path = r["archive"]
    assert os.path.basename(path) == "hesapkitap-20261028T003000Z.tar.hkenc"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    raw = open(path, "rb").read()
    assert raw.startswith(cb.MAGIC) and b"SQLite format 3" not in raw and b"market" not in raw  # gerçekten şifreli

    names = _names(path)
    u1, u2 = (u["id"] for u in server["users"])
    for n in ("data/system.db", "data/isolated.db", f"data/users/{u1}/ekstre.db", f"data/users/{u2}/ekstre.db",
              "manifest.json"):
        assert n in names
    # belgeler tar'a girmez: içerik adlı, şifreli blob olarak bir kez (artımlı); manifest yolu ve özeti tutar
    assert not any("/uploads/" in n or "/bills/" in n for n in names)
    blobs = os.listdir(server["out"] / "files")
    # iki kullanıcının aynı içerikli fatura.jpg'si tek blob (içerik adı)
    assert len([b for b in blobs if b.endswith(".hkenc")]) == 3 and "index.json" in blobs
    assert not any("/backups/" in n or n.startswith("data/backups") or "_silinen" in n or n.endswith("-journal")
                   for n in names)

    v = cb.verify(path)
    assert v["ok"] and v["databases"] == 4 and v["users"] == [u1, u2]
    assert not any(n.startswith(".staging") or n.endswith(".part") for n in os.listdir(server["out"]))


def test_restore_drill_single_user_and_full(server, tmp_path_factory):
    from app import cloud_backup as cb

    path = cb.create(NOW)
    u1 = server["users"][0]["id"]
    one = tmp_path_factory.mktemp("tek")
    assert cb.extract(path, str(one), user_id=u1)["files"] >= 3
    got = {os.path.relpath(os.path.join(d, f), one) for d, _, fs in os.walk(one) for f in fs}
    assert all(p.startswith(f"data/users/{u1}/") or p == "manifest.json" for p in got)
    c = sqlite3.connect(one / "data" / "users" / str(u1) / "ekstre.db")
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("SELECT description FROM entries").fetchall() == [("ayse market",)]

    full = tmp_path_factory.mktemp("tam")
    cb.extract(path, str(full))
    manifest = json.load(open(full / "manifest.json"))
    assert {e["path"] for e in manifest["files"]} >= {"system.db", f"users/{u1}/ekstre.db"}
    assert all(e["integrity"] == "ok" for e in manifest["files"] if e["kind"] == "sqlite")
    users = sqlite3.connect(full / "data" / "system.db").execute("SELECT username FROM users ORDER BY id").fetchall()
    assert users == [("ayse",), ("mehmet",)]


def test_wrong_key_tamper_and_truncation_are_rejected(server, tmp_path):
    from app import cloud_backup as cb

    path = cb.create(NOW)
    with pytest.raises(cb.BackupError, match="anahtar yanlış"):
        cb.verify(path, passphrase="baska-bir-anahtar-123456")
    blob = bytearray(open(path, "rb").read())
    bad = tmp_path / "bozuk.tar.hkenc"
    blob[len(blob) // 2] ^= 1
    bad.write_bytes(bytes(blob))
    with pytest.raises(cb.BackupError):
        cb.verify(str(bad))


def test_stream_cipher_chunks_order_and_truncation():
    from app import cloud_backup as cb

    data = os.urandom(1000)
    buf = io.BytesIO()
    w = cb.EncryptWriter(buf, KEY, chunk=64)
    for i in range(0, len(data), 37):
        w.write(data[i:i + 37])
    w.close()
    blob = buf.getvalue()
    assert b"".join(cb.decrypt_chunks(io.BytesIO(blob), KEY)) == data
    # sondaki parçayı at: kesilmiş arşiv sessizce "tamam" sayılmamalı
    head = len(cb.MAGIC) + 16 + 7 + 4
    offs, p = [], head
    while p < len(blob):
        offs.append(p)
        p += 4 + int.from_bytes(blob[p:p + 4], "big")
    with pytest.raises(cb.BackupError):
        b"".join(cb.decrypt_chunks(io.BytesIO(blob[:offs[-1]]), KEY))
    # iki parçanın yeri değişirse
    a, b = blob[offs[0]:offs[1]], blob[offs[1]:offs[2]]
    with pytest.raises(cb.BackupError):
        b"".join(cb.decrypt_chunks(io.BytesIO(blob[:offs[0]] + b + a + blob[offs[2]:]), KEY))


def test_refuses_without_key_or_inside_data_dir(server, monkeypatch):
    from app import cloud_backup as cb

    monkeypatch.setenv("HESAPKITAP_BACKUP_DIR", str(server["root"] / "yedek"))
    with pytest.raises(cb.BackupError, match="içinde olamaz"):
        cb.create(NOW)
    monkeypatch.setenv("HESAPKITAP_BACKUP_DIR", str(server["out"]))
    monkeypatch.setenv("HESAPKITAP_BACKUP_KEY", "kisa")
    with pytest.raises(cb.BackupError, match="KEY"):
        cb.create(NOW)
    assert cb.main(["run"]) == 1


def test_local_retention_30_days(server):
    from app import cloud_backup as cb

    out = server["out"]
    for days in (0, 1, 29, 30, 45):
        d = NOW - timedelta(days=days)
        (out / f"hesapkitap-{d:%Y%m%dT%H%M%SZ}{cb.SUFFIX}").write_bytes(b"x")
    (out / "baska-dosya.txt").write_bytes(b"x")
    removed = cb.prune_local(NOW)
    assert sorted(removed) == sorted(f"hesapkitap-{NOW - timedelta(days=d):%Y%m%dT%H%M%SZ}{cb.SUFFIX}" for d in (30, 45))
    left = [n for n in os.listdir(out) if cb.NAME_RE.match(n)]
    assert len(left) == 3 and (out / "baska-dosya.txt").exists()


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.auth: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("authorization", ""))
        assert request.headers["x-amz-content-sha256"]
        bucket, _, key = request.url.path.lstrip("/").partition("/")
        assert bucket == "hk-yedek"
        if request.method == "PUT":
            self.objects[key] = request.read()
            return httpx.Response(200)
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        if request.method == "GET" and not key:
            prefix = request.url.params.get("prefix", "")
            items = "".join(f"<Contents><Key>{k}</Key><Size>{len(v)}</Size></Contents>"
                            for k, v in sorted(self.objects.items()) if k.startswith(prefix))
            return httpx.Response(200, text='<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/'
                                             f'2006-03-01/"><IsTruncated>false</IsTruncated>{items}</ListBucketResult>')
        if request.method == "GET":
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
        return httpx.Response(400)


def test_offsite_upload_and_remote_retention(server, monkeypatch, tmp_path_factory):
    from app import cloud_backup as cb

    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "https://hesap.r2.cloudflarestorage.com")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "hk-yedek")
    monkeypatch.setenv("BACKUP_S3_KEY", "AKID")
    monkeypatch.setenv("BACKUP_S3_SECRET", "gizli")
    fake = FakeS3()
    old = f"hesapkitap/hesapkitap-{NOW - timedelta(days=31):%Y%m%dT%H%M%SZ}{cb.SUFFIX}"
    fake.objects[old] = b"eski"
    fake.objects["hesapkitap/elle-konmus.txt"] = b"dokunma"
    r = cb.run(NOW, s3_client=httpx.Client(transport=httpx.MockTransport(fake)))
    key = r["remote"]
    assert key == "hesapkitap/" + os.path.basename(r["archive"])
    assert fake.objects[key] == open(r["archive"], "rb").read()
    assert old not in fake.objects and "hesapkitap/elle-konmus.txt" in fake.objects
    assert r["pruned_remote"] == [old.split("/")[1]]
    assert all(a.startswith("AWS4-HMAC-SHA256 Credential=AKID/20") and "/auto/s3/aws4_request" in a for a in fake.auth)

    # sunucu kaybı: uzaktan indir → aç → doğrula
    s3 = cb.S3(cb.s3_config(), httpx.Client(transport=httpx.MockTransport(fake)))
    dest = tmp_path_factory.mktemp("indirilen") / os.path.basename(key)
    s3.get_file(key, str(dest))
    assert cb.verify(str(dest))["ok"]


def test_s3_config_partial_is_an_error(monkeypatch):
    from app import cloud_backup as cb

    for k in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_KEY", "BACKUP_S3_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert cb.s3_config() is None
    monkeypatch.setenv("BACKUP_S3_BUCKET", "x")
    with pytest.raises(cb.BackupError, match="BACKUP_S3_ENDPOINT"):
        cb.s3_config()


def test_sigv4_matches_aws_example():
    """AWS belgelerindeki örnek: GET Object (examplebucket/test.txt, Range: bytes=0-9)."""
    from app import cloud_backup as cb

    h = cb.sigv4_headers("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {}, {"Range": "bytes=0-9"},
                         cb.EMPTY_SHA, "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
                         datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert h["authorization"].endswith(
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date,"
        " Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


# ---------------------------------------------------------------- silinen hesaplar: 30 gün

def test_purge_archived_after_30_days(tmp_path, monkeypatch):
    from app import system

    base = tmp_path / "users" / "_silinen"
    now = time.time()
    for name, age in (("5-eski-{}", 31), ("6-sinir-{}", 30), ("7-yeni-{}", 3)):
        os.makedirs(base / name.format(int(now - age * 86400)))
    removed = system.purge_archived(30, now=now)
    assert sorted(n.split("-")[1] for n in removed) == ["eski", "sinir"]
    assert [n.split("-")[1] for n in os.listdir(base)] == ["yeni"]
    assert system.purge_archived(0, now=now + 999 * 86400) == []  # 0: kendiliğinden silinmez


def test_global_job_runs_once_a_day(tmp_path):
    from app import jobs, system

    base = tmp_path / "users" / "_silinen"
    os.makedirs(base / f"5-eski-{int(time.time()) - 40 * 86400}")
    now = datetime.now()
    early, late = now.replace(hour=1), now.replace(hour=5)
    assert jobs.run_global(early) is None
    assert len(jobs.run_global(late)) == 1
    assert jobs.run_global(late) is None and system.get("job:purge_deleted") == late.date().isoformat()


def test_archive_name_cannot_escape(tmp_path):
    from app import system

    u = system.create_user("x@y.com", "h")
    os.makedirs(os.path.dirname(system.user_db_path(u)))
    with system.connect() as c:
        c.execute("UPDATE users SET username = '../../kacak@y.com' WHERE id = ?", (u["id"],))
    dst = system.archive_user_data(system.user(u["id"]))
    assert os.path.dirname(dst) == str(tmp_path / "users" / "_silinen")


def test_user_id_is_never_reused_and_tokens_go_with_account():
    from app import system

    a = system.create_user("birinci", "h")
    b = system.create_user("ikinci", "h")
    tok = system.create_token(b["id"], "kısayol")
    assert system.token_owner(tok)["id"] == b["id"]
    system.delete_user(b["id"])
    assert system.token_owner(tok) is None
    c = system.create_user("ucuncu", "h")
    assert c["id"] > b["id"] > a["id"]


# ---------------------------------------------------------------- hesap silme: veri hemen gider

@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _account(owner, name, member=False):
    from app.api.main import app

    inv = owner.post("/api/book/invites" if member else "/api/admin/invites", json={"note": name}).json()
    c = TestClient(app)
    assert c.post("/api/auth/register", json={"code": inv["code"], "username": name,
                                              "password": f"{name}-sifre-12345"}).status_code == 200
    from app import system

    return c, system.user_by_name(name)


ENTRY = {"entry_date": "2026-10-05", "description": "Fırın", "amount": 45, "kind": "gider",
         "category": "Market & Gıda", "payment": "nakit"}
OWN = {"X-HK-Book": "kendi"}


def _after_grace(days=8):
    from datetime import datetime, timedelta, timezone

    from app import deletion

    return deletion.purge_due(datetime.now(timezone.utc) + timedelta(days=days))


def test_member_deleting_self_removes_own_book(owner):
    from app import system

    m, mu = _account(owner, "esim", member=True)
    assert m.post("/api/entries", json=ENTRY, headers=OWN).status_code == 200
    folder = os.path.dirname(system.own_db_path(mu))
    assert os.path.isdir(folder)
    assert m.post("/api/me/delete", json={"password": "esim-sifre-12345"}).status_code == 200
    assert os.path.isdir(folder)  # 7 gün geri alınabilir
    _after_grace()
    assert not os.path.exists(folder)


def test_owner_deleting_account_removes_members_own_books(owner):
    from app import system
    from app.api.main import app

    inv = owner.post("/api/admin/invites", json={}).json()
    o = TestClient(app)
    assert o.post("/api/auth/register", json={"code": inv["code"], "username": "sahip",
                                              "password": "sahip-sifre-12345"}).status_code == 200
    _, mu = _account(o, "uye", member=True)
    m = TestClient(app)
    assert m.post("/api/auth/login", json={"username": "uye", "password": "uye-sifre-12345"}).status_code == 200
    assert m.post("/api/entries", json=ENTRY, headers=OWN).status_code == 200
    ou = system.user_by_name("sahip")
    folders = [os.path.dirname(system.user_db_path(ou)), os.path.dirname(system.own_db_path(mu))]
    assert all(os.path.isdir(f) for f in folders)
    assert o.post("/api/me/delete", json={"password": "sahip-sifre-12345"}).status_code == 200
    assert system.user(mu["id"])["deleted_with"] == ou["id"]  # üye de aynı akışta
    _after_grace()
    assert not any(os.path.exists(f) for f in folders)
    assert system.user(mu["id"]) is None


def test_removed_member_own_book_is_archived_not_orphaned(owner):
    from app import system

    m, mu = _account(owner, "esim", member=True)
    assert m.post("/api/entries", json=ENTRY, headers=OWN).status_code == 200
    folder = os.path.dirname(system.own_db_path(mu))
    assert owner.delete(f"/api/book/members/{mu['id']}").json()["ok"]
    assert not os.path.exists(folder)
    archived = os.listdir(os.path.join(system.data_root(), "_silinen"))
    assert len(archived) == 1 and archived[0].startswith(f"{mu['id']}-esim-")


# ---------------------------------------------------------------- ekstre silinince PDF de gider

def test_delete_statement_removes_stored_pdf(tmpdb):
    from app.parsers import parse_statement

    ids = []
    for n in ("vakifbank_a.txt", "kuveytturk.txt"):
        ids.append(tmpdb.save_statement(fixture_bytes(n), n, parse_statement(fixture_bytes(n))))
    pdfs = {n: os.path.join(tmpdb.upload_dir(), f"{tmpdb.file_hash(fixture_bytes(n))}.pdf")
            for n in ("vakifbank_a.txt", "kuveytturk.txt")}
    assert all(os.path.exists(p) for p in pdfs.values())
    tmpdb.delete_statement(ids[0])
    assert not os.path.exists(pdfs["vakifbank_a.txt"]) and os.path.exists(pdfs["kuveytturk.txt"])
    tmpdb.delete_statement(ids[0])  # ikinci kez: hata yok
    with tmpdb.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM transactions WHERE statement_id = ?", (ids[0],)).fetchone()[0] == 0


def test_delete_statement_api_removes_pdf(client, tmpdb):
    client.post("/api/statements/upload", files=[("files", ("a.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))])
    sid = tmpdb.list_statements()[0]["id"]
    assert os.listdir(tmpdb.upload_dir())
    assert client.delete(f"/api/statements/{sid}").json()["ok"]
    assert os.listdir(tmpdb.upload_dir()) == []


def test_user_backup_prune_covers_dated_copies(tmpdb):
    from datetime import date

    from app import backup

    d = backup.backup_dir()
    for name in ("predeploy-20260901-120000-system.db", "pre-admin-rename-20261020-125549-system.db",
                 "ekstre-20260920.db", "ekstre-20260929.db", "ekstre-20260930.db", "notlar.txt"):
        open(os.path.join(d, name), "wb").write(b"x")
    removed = backup.prune(date(2026, 10, 29))
    assert sorted(removed) == ["ekstre-20260920.db", "ekstre-20260929.db", "predeploy-20260901-120000-system.db"]
    assert {"ekstre-20260930.db", "notlar.txt", "pre-admin-rename-20261020-125549-system.db"} <= set(os.listdir(d))
