"""Dosya depolama (app/storage.py): yerel disk bugünkü yollarla birebir; S3 arka ucu bellek içi sahte sunucuyla.
Fiş fotoğrafı saklama (küçültme, EXIF/konum silme), alan sınırı, hesap silme/arşiv, geçiş komutu, güvenlik kuralları."""
import io
import json
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import fixture_bytes

ENDPOINT = "https://hesapacc.eu.r2.cloudflarestorage.com"
SECRET = "r2-gizli-anahtar-xyz"


class FakeS3:
    """Bellek içi S3: PUT / GET / HEAD / DELETE / ListObjectsV2 / CopyObject (path-style, bucket hk-dosya)."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKFILES/")
        assert SECRET not in request.headers["authorization"]
        if self.fail:
            return httpx.Response(500, text="<Error><Code>InternalError</Code></Error>")
        from urllib.parse import unquote

        bucket, _, key = unquote(request.url.path).lstrip("/").partition("/")
        assert bucket == "hk-dosya"
        if request.method == "PUT" and "x-amz-copy-source" in request.headers:
            src = unquote(request.headers["x-amz-copy-source"]).lstrip("/").partition("/")[2]
            if src not in self.objects:
                return httpx.Response(404, text="<Error><Code>NoSuchKey</Code></Error>")
            self.objects[key] = self.objects[src]
            return httpx.Response(200, text="<CopyObjectResult></CopyObjectResult>")
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
            return httpx.Response(200, text=f"<ListBucketResult><IsTruncated>false</IsTruncated>{items}</ListBucketResult>")
        if request.method in ("GET", "HEAD"):
            if key not in self.objects:
                return httpx.Response(404, text="<Error><Code>NoSuchKey</Code></Error>")
            body = self.objects[key]
            return httpx.Response(200, content=b"" if request.method == "HEAD" else body,
                                  headers={"content-length": str(len(body))})
        return httpx.Response(400)


@pytest.fixture()
def s3(monkeypatch):
    from app import storage

    fake = FakeS3()
    monkeypatch.setenv("HESAPKITAP_FILES_S3_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("HESAPKITAP_FILES_S3_BUCKET", "hk-dosya")
    monkeypatch.setenv("HESAPKITAP_FILES_S3_KEY", "AKFILES")
    monkeypatch.setenv("HESAPKITAP_FILES_S3_SECRET", SECRET)
    monkeypatch.setattr(storage, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def _photo(w=3000, h=2000, gps=True, orientation=6) -> bytes:
    """Konum (GPS) ve yön (EXIF Orientation) bilgisi taşıyan büyük bir JPEG."""
    from PIL import Image

    im = Image.new("RGB", (w, h), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "TelefonMarka"
    if gps:
        exif[0x8825] = {1: "N", 2: (41.0, 2.0, 3.0), 3: "E", 4: (29.0, 1.0, 2.0)}
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=95, exif=exif)
    return buf.getvalue()


RECEIPT = {"type": "receipt", "merchant": "Migros", "date": "2026-10-20", "total": 123.45, "payment": "kart",
           "payment_card": None, "installments": 1, "card_last4": None, "category": "Market & Gıda", "items": [],
           "legal_name": None, "address": None, "branch": None, "confidence": "yüksek"}


@pytest.fixture()
def reader(monkeypatch):
    """Okuma (Gemini/Tesseract) yerine: okunan görüntüyü kaydeder, sabit bir fiş döndürür."""
    from app import receipt

    seen = []

    def fake(data, mime, cats, split=None, more=None):
        seen.append((data, mime, more))
        return dict(RECEIPT)

    monkeypatch.setattr(receipt, "parse_document", fake)
    return seen


def _parse_photo(client, data=None, mime="image/jpeg"):
    return client.post("/api/documents/parse", files=[("file", ("fis.jpg", data or _photo(), mime))]).json()


# ---------------------------------------------------------------- yerel disk: bugünkü yollar

def test_local_backend_keeps_todays_paths(tmpdb, tmp_path):
    from app import db, storage, system

    b = storage.LocalBackend()
    assert b.path("main/uploads/x.pdf") == os.path.join(db.UPLOAD_DIR, "x.pdf")
    assert b.path("main/bills/y.jpg") == os.path.join(db.BILL_DIR, "y.jpg")
    assert b.path("main/receipts/z.jpg") == os.path.join(os.path.dirname(db.DB_PATH), "receipts", "z.jpg")
    assert b.path("users/7/uploads/x.pdf") == os.path.join(system.data_root(), "7", "uploads", "x.pdf")
    u = system.create_user("ayse", "x")
    with db.use_space(system.user_db_path(u), u):
        assert db.files_scope() == (f"users/{u['id']}/", None)
        assert storage.local_dir("uploads") == db.upload_dir() and storage.local_dir("bills") == db.bill_dir()
    with db.use_space(str(tmp_path / "baska" / "ekstre.db")):  # data/users dışı (demo kopyası): yanında, yerel
        assert db.files_scope() == (None, str(tmp_path / "baska"))
    for bad in ("../x", "main/../x", "/main/x", "main/a b", "main//x", "main/x\n"):
        with pytest.raises(ValueError):
            b.path(bad)


def test_local_statement_and_bill_files_where_they_always_were(client, tmpdb):
    data = fixture_bytes("vakifbank_a.txt")
    client.post("/api/statements/upload", files=[("files", ("a.pdf", data, "application/pdf"))])
    p = os.path.join(tmpdb.upload_dir(), f"{tmpdb.file_hash(data)}.pdf")
    assert open(p, "rb").read() == data
    sid = tmpdb.list_statements()[0]["id"]
    r = client.get(f"/api/statements/{sid}/file")
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == "application/pdf"
    assert tmpdb.storage_used() == len(data)


def test_existing_local_files_are_indexed_once(tmp_path):
    from app import db, system

    u = system.create_user("eski", "x")
    path = system.user_db_path(u)
    up = os.path.join(os.path.dirname(path), "uploads")
    os.makedirs(up)
    open(os.path.join(up, "a" * 64 + ".pdf"), "wb").write(b"%PDF eski")
    open(os.path.join(up, "notlar.txt"), "wb").write(b"ad kurala uymuyor")
    with db.use_space(path, u):
        assert db.storage_used() == 9
        with db.connect() as c:
            assert [tuple(r) for r in c.execute("SELECT legacy FROM stored_files")] == [(1,)]
        assert db.gc_files(min_age_hours=0) == []  # eski dosyaya temizlik dokunmaz


# ---------------------------------------------------------------- fiş fotoğrafı

def test_receipt_photo_is_shrunk_stripped_stored_and_served(client, tmpdb, reader):
    from PIL import Image

    big = _photo()
    doc = _parse_photo(client, big)
    small, mime, _ = reader[0]
    assert mime == "image/jpeg" and len(small) < len(big)
    im = Image.open(io.BytesIO(small))
    assert max(im.size) == 1600 and im.size == (1067, 1600)  # yön uygulandı (6: 90° döndür)
    assert not im.getexif() and b"TelefonMarka" not in small  # EXIF / konum yok
    assert len(small) <= 1.5 * 1024 * 1024
    name = doc["receipt_file"]
    assert name == f"{tmpdb.file_hash(small)}.jpg"
    assert "/" not in name and "receipts" not in name  # istemciye anahtar/yol değil, yalnız içerik adı

    eid = client.post("/api/entries", json={"entry_date": "2026-10-20", "description": "Migros", "amount": 123.45,
                                            "kind": "gider", "category": "Market & Gıda", "payment": "nakit",
                                            "receipt_file": name}).json()["id"]
    row = next(t for t in client.get("/api/transactions?month=2026-10").json() if t["uid"] == f"m{eid}")
    assert row["receipt_file"] == name
    r = client.get(f"/api/entries/{eid}/receipt")
    assert r.status_code == 200 and r.content == small
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in r.headers["cache-control"] and "private" in r.headers["cache-control"]
    assert r.headers["content-disposition"].startswith('inline; filename="fis-2026-10-20-')
    path = os.path.join(os.path.dirname(tmpdb.DB_PATH), "receipts", name)
    assert os.path.exists(path)
    client.delete(f"/api/entries/{eid}")
    assert not os.path.exists(path) and client.get(f"/api/entries/{eid}/receipt").status_code == 404


def test_receipt_shared_by_two_entries_survives_first_delete(client, tmpdb, reader):
    name = _parse_photo(client)["receipt_file"]
    body = {"entry_date": "2026-10-20", "description": "A", "amount": 1, "kind": "gider", "payment": "nakit",
            "receipt_file": name}
    a, b = (client.post("/api/entries", json=body).json()["id"] for _ in range(2))
    client.delete(f"/api/entries/{a}")
    assert client.get(f"/api/entries/{b}/receipt").status_code == 200


def test_unopenable_photo_is_read_but_not_stored(client, tmpdb, reader, caplog):
    heic = b"\x00\x00\x00\x18ftypheic" + os.urandom(200)
    doc = _parse_photo(client, heic, "image/heic")
    assert reader[0][0] == heic and doc["receipt_file"] is None
    assert tmpdb.storage_used() == 0 and "saklanmıyor" in caplog.text


def test_entry_cannot_point_at_arbitrary_file(client, tmpdb):
    body = {"entry_date": "2026-10-20", "description": "A", "amount": 1, "kind": "gider", "payment": "nakit"}
    for bad in ("../ekstre.db", "a" * 64 + ".pdf", "users/2/receipts/" + "a" * 64 + ".jpg", "A" * 64 + ".jpg"):
        assert client.post("/api/entries", json=body | {"receipt_file": bad}).status_code == 422
    eid = client.post("/api/entries", json=body | {"receipt_file": "b" * 64 + ".jpg"}).json()["id"]  # bu defterde yok
    assert client.get(f"/api/entries/{eid}/receipt").status_code == 404
    with tmpdb.connect() as c:
        assert c.execute("SELECT receipt_file FROM entries WHERE id = ?", (eid,)).fetchone()[0] is None


def test_unattached_photos_are_cleaned_after_a_day(client, tmpdb, reader):
    keep = _parse_photo(client)["receipt_file"]
    client.post("/api/entries", json={"entry_date": "2026-10-20", "description": "A", "amount": 1, "kind": "gider",
                                      "payment": "nakit", "receipt_file": keep})
    drop = _parse_photo(client, _photo(w=2000, h=1000))["receipt_file"]
    assert tmpdb.gc_files() == []  # 24 saat dolmadı: kullanıcı belki hâlâ formda
    with tmpdb.connect() as c:
        c.execute("UPDATE stored_files SET created_at = datetime('now', '-2 days')")
    assert tmpdb.gc_files() == [f"receipts/{drop}"]
    assert tmpdb.file_indexed("receipts", keep) and not tmpdb.file_indexed("receipts", drop)


def test_bill_photo_heic_served_only_as_download(client, tmpdb):
    heic = b"\x00\x00\x00\x18ftypheic-fatura"
    name = tmpdb.save_bill_file(heic, ".heic")
    bid = client.post("/api/bills", json={"provider": "İGDAŞ \"x\"\r\n", "amount": 10, "due_date": "2026-11-01",
                                          "file_name": name}).json()["id"]
    r = client.get(f"/api/bills/{bid}/file")
    assert r.status_code == 200 and r.content == heic
    assert r.headers["content-type"] == "application/octet-stream"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;") and "\r" not in cd and "\n" not in cd and cd.count('"') == 2
    # elle verilmiş yol: dosya okunmaz
    bad = client.post("/api/bills", json={"provider": "X", "amount": 1, "due_date": "2026-11-01",
                                          "file_name": "../t.db"}).json()["id"]
    assert client.get(f"/api/bills/{bad}/file").status_code == 404


# ---------------------------------------------------------------- S3 arka ucu

def test_s3_backend_statement_bill_receipt_roundtrip(client, tmpdb, s3, reader):
    data = fixture_bytes("vakifbank_a.txt")
    client.post("/api/statements/upload", files=[("files", ("a.pdf", data, "application/pdf"))])
    key = f"main/uploads/{tmpdb.file_hash(data)}.pdf"
    assert s3.objects[key] == data
    assert not os.path.exists(tmpdb.upload_dir()) or not os.listdir(tmpdb.upload_dir())  # yerelde kopya yok
    sid = tmpdb.list_statements()[0]["id"]
    assert client.get(f"/api/statements/{sid}/file").content == data

    name = _parse_photo(client)["receipt_file"]
    assert f"main/receipts/{name}" in s3.objects
    eid = client.post("/api/entries", json={"entry_date": "2026-10-20", "description": "M", "amount": 5, "kind": "gider",
                                            "payment": "nakit", "receipt_file": name}).json()["id"]
    assert client.get(f"/api/entries/{eid}/receipt").content == s3.objects[f"main/receipts/{name}"]

    z = client.get("/api/settings/export.zip")  # dışa aktarma da depodan okur
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert f"uploads/{tmpdb.file_hash(data)}.pdf" in names and f"receipts/{name}" in names

    client.delete(f"/api/entries/{eid}")
    client.delete(f"/api/statements/{sid}")
    assert key not in s3.objects and f"main/receipts/{name}" not in s3.objects


def test_s3_errors_are_generic_and_leak_nothing(client, tmpdb, s3, reader, caplog):
    name = _parse_photo(client)["receipt_file"]
    eid = client.post("/api/entries", json={"entry_date": "2026-10-20", "description": "M", "amount": 5, "kind": "gider",
                                            "payment": "nakit", "receipt_file": name}).json()["id"]
    s3.fail = True
    r = client.get(f"/api/entries/{eid}/receipt")
    assert r.status_code == 503 and r.json()["detail"] == "Dosya şu an alınamadı"
    for text in (r.text, caplog.text, json.dumps(client.get("/api/meta").json())):
        assert ENDPOINT not in text and "hesapacc" not in text and SECRET not in text and "AKFILES" not in text
    assert "InternalError" in caplog.text  # ayrıntı sunucu günlüğünde (anahtarsız)


def test_s3_quota_counts_index_not_listing(client, tmpdb, s3, monkeypatch):
    s3.objects["main/uploads/" + "c" * 64 + ".pdf"] = b"x" * 999  # depoda olup dizinde olmayan sayılmaz
    assert tmpdb.storage_used() == 0


def _login_admin(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _register(admin, name, member=False):
    from app import plans, system
    from app.api.main import app

    inv = admin.post("/api/book/invites" if member else "/api/admin/invites", json={"note": name}).json()
    c = TestClient(app)
    body = {"code": inv["code"], "username": name, "password": f"{name}-sifre-12345"}
    if plans.hosted():  # hazır sürüm: e-posta + KVKK onayı
        from app.api import main

        main._signups.clear()
        body |= {"username": f"{name}@ornek.com", "consent": ["kosullar", "acik_riza"]}
    assert c.post("/api/auth/register", json=body).status_code == 200
    return c, system.user_by_name(body["username"])


def test_user_prefix_isolation_and_idor(client, tmpdb, s3, reader, monkeypatch):
    admin = _login_admin(client, monkeypatch)
    a, ua = _register(admin, "ayse")
    b, ub = _register(admin, "mehmet")
    name = _parse_photo(a)["receipt_file"]
    assert f"users/{ua['id']}/receipts/{name}" in s3.objects
    eid = a.post("/api/entries", json={"entry_date": "2026-10-20", "description": "M", "amount": 5, "kind": "gider",
                                       "payment": "nakit", "receipt_file": name}).json()["id"]
    data = fixture_bytes("kuveytturk.txt")
    a.post("/api/statements/upload", files=[("files", ("k.pdf", data, "application/pdf"))])
    bill = a.post("/api/bills", json={"provider": "Enerjisa", "amount": 10, "due_date": "2026-11-01",
                                      "file_name": _bill(a)}).json()["id"]
    sid = a.get("/api/statements").json()[0]["id"]
    for url in (f"/api/entries/{eid}/receipt", f"/api/statements/{sid}/file", f"/api/bills/{bill}/file"):
        assert a.get(url).status_code == 200
        assert b.get(url).status_code == 404  # B'nin defterinde bu kayıt yok; A'nın nesnesine ulaşamaz
    # B aynı içerik adını kendi kaydına bağlayamaz (kendi defterinde saklanmış değil)
    other = b.post("/api/entries", json={"entry_date": "2026-10-20", "description": "X", "amount": 1, "kind": "gider",
                                         "payment": "nakit", "receipt_file": name}).json()["id"]
    assert b.get(f"/api/entries/{other}/receipt").status_code == 404


def _bill(c) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (0, 0, 255)).save(buf, format="PNG")
    r = c.post("/api/bills/parse", files=[("file", ("fatura.png", buf.getvalue(), "image/png"))]).json()
    assert r["file_name"].endswith(".jpg")  # fotoğraf fatura da küçültülüp EXIF'siz JPEG saklanır
    return r["file_name"]


def test_member_hidden_entry_photo_is_404(client, tmpdb, s3, reader, monkeypatch):
    owner = _login_admin(client, monkeypatch)
    m, _ = _register(owner, "esim", member=True)
    name = _parse_photo(owner)["receipt_file"]
    body = {"entry_date": "2026-10-20", "description": "Sürpriz", "amount": 5, "kind": "gider", "payment": "nakit",
            "receipt_file": name}
    sec = owner.post("/api/entries", json=body).json()["id"]
    pub = owner.post("/api/entries", json=body | {"description": "Market"}).json()["id"]
    owner.patch(f"/api/entries/{sec}", json={"hidden": True})
    assert m.get(f"/api/entries/{pub}/receipt").status_code == 200  # ortak defter: sahibin öneki (main/)
    assert m.get(f"/api/entries/{sec}/receipt").status_code == 404
    data = fixture_bytes("vakifbank_a.txt")
    owner.post("/api/statements/upload", files=[("files", ("a.pdf", data, "application/pdf"))])
    st = owner.get("/api/statements").json()[0]
    owner.put("/api/book/rules", json={"hidden_cards": [st["card_no"]]})
    assert owner.get(f"/api/statements/{st['id']}/file").status_code == 200
    assert m.get(f"/api/statements/{st['id']}/file").status_code == 404  # gizli kartın ekstresi


def test_account_delete_purges_prefix_and_archive_moves_then_expires(client, tmpdb, s3, reader, monkeypatch):
    from app import system

    admin = _login_admin(client, monkeypatch)
    a, ua = _register(admin, "silen")
    _parse_photo(a)
    assert any(k.startswith(f"users/{ua['id']}/") for k in s3.objects)
    assert a.post("/api/me/delete", json={"password": "silen-sifre-12345"}).status_code == 200
    assert any(k.startswith(f"users/{ua['id']}/") for k in s3.objects)  # 7 gün geri alınabilir: nesneler duruyor
    from datetime import datetime, timedelta, timezone

    from app import deletion

    assert deletion.purge_due(datetime.now(timezone.utc) + timedelta(days=8)) == [ua["id"]]
    assert not any(k.startswith(f"users/{ua['id']}/") for k in s3.objects)

    b, ub = _register(admin, "kapanan")
    _parse_photo(b, _photo(w=1800, h=900))
    assert admin.delete(f"/api/admin/users/{ub['id']}").json()["ok"]
    assert not any(k.startswith(f"users/{ub['id']}/") for k in s3.objects)
    moved = [k for k in s3.objects if k.startswith(f"deleted/{ub['id']}-")]
    assert len(moved) == 1 and "/receipts/" in moved[0] and "kapanan" not in moved[0]  # kullanıcı adı anahtarda yok
    assert system.purge_archived(30, now=time.time() + 5 * 86400) == []
    removed = system.purge_archived(30, now=time.time() + 31 * 86400)
    assert any(r.startswith(f"deleted/{ub['id']}-") for r in removed)
    assert not any(k.startswith("deleted/") for k in s3.objects)


def test_hosted_storage_limit_keeps_record_drops_file_warns_once(client, tmpdb, monkeypatch, reader):
    from app import plans

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    admin = _login_admin(client, monkeypatch)
    c, u = _register(admin, "dolu")
    monkeypatch.setitem(plans.STORAGE_BYTES, plans.FREE, 1000)
    lim = c.get("/api/meta").json()["plan"]["limits"]
    assert lim["storage_bytes"] == 1000 and lim["storage_used"] == 0
    first = _parse_photo(c)
    assert first["receipt_file"] is None and "alanın doldu" in first["storage_warning"]
    second = _parse_photo(c, _photo(w=1200, h=800))
    assert second["receipt_file"] is None and "storage_warning" not in second  # bir kez
    data = fixture_bytes("vakifbank_a.txt")
    assert len(data) < 1000
    res = c.post("/api/statements/upload", files=[("files", ("a.pdf", data, "application/pdf"))]).json()
    assert res[0]["status"] == "saved"  # sığan dosya saklanır, uyarı bayrağı sıfırlanır
    assert c.get("/api/meta").json()["plan"]["limits"]["storage_used"] == len(data)
    third = _parse_photo(c, _photo(w=1300, h=800))
    assert third["receipt_file"] is None and "storage_warning" in third
    assert plans.storage_limit({"role": "user", "plan": "premium"}) == 2 * 1024 ** 3
    monkeypatch.delenv("HESAPKITAP_HOSTED")
    assert plans.storage_limit({"role": "user"}) is None  # kendi sunucusunda sınır yok


def test_bank_statement_file_is_kept_and_downloadable(client, tmpdb):
    from tests.test_bank_import import CSV, _upload

    m = _upload(client, CSV.encode("utf-8"), "hareket.csv").json()
    _upload(client, CSV.encode("utf-8"), "hareket.csv", m["mapping"], m["header_row"])
    docs = client.get("/api/bank-import/documents").json()
    assert len(docs) == 1 and docs[0]["filename"] == "hareket.csv" and "file_name" not in docs[0]
    r = client.get(f"/api/bank-import/documents/{docs[0]['id']}/file")
    assert r.status_code == 200 and r.content == CSV.encode("utf-8")
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["content-disposition"].startswith("attachment")
    linked = [e for e in tmpdb.list_entries() if e["bank_doc_id"] == docs[0]["id"]]
    assert linked and all(e["import_hash"] for e in linked)


# ---------------------------------------------------------------- geçiş

def test_migrate_copies_verifies_and_keeps_local_unless_asked(tmp_path, s3, monkeypatch):
    from app import db, storage, system

    u = system.create_user("tasinan", "x")
    with db.use_space(system.user_db_path(u), u):
        db.init_db()
        db.store_file("receipts", "d" * 64 + ".jpg", b"jpeg-veri")  # S3 açıkken yazılan zaten orada
    env = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("HESAPKITAP_FILES_S3_")}  # ...önceki yerel
    with db.use_space(system.user_db_path(u), u):
        db.store_file("uploads", "e" * 64 + ".pdf", b"%PDF kullanici")
    db.store_file("bills", "f" * 24 + ".pdf", b"%PDF fatura")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    local = os.path.join(system.data_root(), str(u["id"]), "uploads", "e" * 64 + ".pdf")
    r = storage.migrate()
    assert r["files"] == 2 and r["copied"] == 2 and r["verified"] == 2 and not r["failed"] and r["removed"] == 0
    assert s3.objects[f"users/{u['id']}/uploads/{'e' * 64}.pdf"] == b"%PDF kullanici"
    assert s3.objects[f"main/bills/{'f' * 24}.pdf"] == b"%PDF fatura"
    assert os.path.exists(local)
    s3.objects[f"main/bills/{'f' * 24}.pdf"] = b"bozuk"  # uzaktaki kopya tutmuyorsa yeniden yazılır
    r = storage.migrate(remove_local=True)
    assert r["already"] == 1 and r["copied"] == 1 and r["removed"] == 2 and not os.path.exists(local)
    assert s3.objects[f"main/bills/{'f' * 24}.pdf"] == b"%PDF fatura"
    assert storage.main(["migrate", "--dry-run"]) == 0


def test_migrate_refuses_without_s3(tmp_path):
    from app import storage

    with pytest.raises(storage.StorageError):
        storage.migrate()


# ---------------------------------------------------------------- yedek: S3'teyse belgeler arşive girmez

def test_cloud_backup_skips_documents_when_files_in_s3(tmp_path, tmp_path_factory, monkeypatch, s3):
    from app import cloud_backup as cb
    from app import db, system

    out = tmp_path_factory.mktemp("yedek")
    monkeypatch.setenv("HESAPKITAP_BACKUP_DIR", str(out))
    monkeypatch.setenv("HESAPKITAP_BACKUP_KEY", "test-yedek-anahtari-0123456789")
    for k in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_KEY", "BACKUP_S3_SECRET"):
        monkeypatch.delenv(k, raising=False)
    u = system.create_user("ayse", "x")
    with db.use_space(system.user_db_path(u), u):
        db.init_db()
        os.makedirs(db.upload_dir())
        open(os.path.join(db.upload_dir(), "a" * 64 + ".pdf"), "wb").write(b"gecis artigi")
    assert cb.s3_config()["prefix"] == "backups/"  # ayrı yedek bucket'ı yoksa dosyaların bucket'ı, backups/ öneki
    from datetime import datetime, timezone

    r = cb.run(datetime(2026, 10, 28, tzinfo=timezone.utc), s3_client=httpx.Client(transport=httpx.MockTransport(s3)))
    assert r["remote"].startswith("backups/hesapkitap-") and r["remote"] in s3.objects
    v = cb.verify(r["archive"])
    assert v["ok"] and v["documents"] == "s3" and v["blobs"] == 0
    assert not os.path.exists(out / "files")


def test_incremental_blobs_written_once_and_expire(tmp_path, tmp_path_factory, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app import cloud_backup as cb
    from app import db, system

    out = tmp_path_factory.mktemp("yedek")
    monkeypatch.setenv("HESAPKITAP_BACKUP_DIR", str(out))
    monkeypatch.setenv("HESAPKITAP_BACKUP_KEY", "test-yedek-anahtari-0123456789")
    for k in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_KEY", "BACKUP_S3_SECRET", "HESAPKITAP_FILES_IN_S3"):
        monkeypatch.delenv(k, raising=False)
    u = system.create_user("ayse", "x")
    with db.use_space(system.user_db_path(u), u):
        db.init_db()
        db.store_file("receipts", "a" * 64 + ".jpg", b"fis-1")
        db.store_file("bills", "b" * 24 + ".pdf", b"fatura-1")
    day0 = datetime(2026, 10, 1, 1, tzinfo=timezone.utc)
    cb.run(day0)
    blob_dir = out / "files"
    first = {n: os.path.getmtime(blob_dir / n) for n in os.listdir(blob_dir) if n.endswith(".hkenc")}
    assert len(first) == 2
    time.sleep(0.01)
    with db.use_space(system.user_db_path(u), u):  # ertesi gün fatura silindi, yeni fiş geldi
        db.remove_file("bills", "b" * 24 + ".pdf")
        db.store_file("receipts", "c" * 64 + ".jpg", b"fis-2")
    a1 = cb.run(day0 + timedelta(days=1))["archive"]
    now = {n: os.path.getmtime(blob_dir / n) for n in os.listdir(blob_dir) if n.endswith(".hkenc")}
    assert len(now) == 3 and all(now[n] == t for n, t in first.items())  # var olan blob yeniden yazılmadı
    # geri açma: o günün belgeleri blob'lardan
    dest = tmp_path_factory.mktemp("acilan")
    cb.extract(a1, str(dest), user_id=u["id"])
    got = dest / "data" / "users" / str(u["id"]) / "receipts"
    assert sorted(os.listdir(got)) == ["a" * 64 + ".jpg", "c" * 64 + ".jpg"]
    assert (got / ("c" * 64 + ".jpg")).read_bytes() == b"fis-2"
    assert cb.verify(a1)["blobs"] == 2
    # faturanın blob'u en son 0. gün görüldü: o günün arşiviyle aynı gün (30. takvim günü) silinir
    cb.run(day0 + timedelta(days=29))
    assert len([n for n in os.listdir(blob_dir) if n.endswith(".hkenc")]) == 3
    assert any(n.startswith("hesapkitap-20261001") for n in os.listdir(out))
    r = cb.run(day0 + timedelta(days=30))
    assert not any(n.startswith("hesapkitap-20261001") for n in os.listdir(out))
    assert r["pruned_blobs"] == 1 and len([n for n in os.listdir(blob_dir) if n.endswith(".hkenc")]) == 2
    raw = b"".join((blob_dir / n).read_bytes() for n in os.listdir(blob_dir) if n.endswith(".hkenc"))
    assert b"fis-" not in raw  # blob'lar şifreli


def test_images_prepare_limits():
    from PIL import Image

    from app import images

    assert images.prepare(b"bu bir resim degil") is None
    noise = Image.effect_noise((1600, 1600), 120).convert("RGB")  # sıkışmayan görüntü: 1.5 MB sınırı
    buf = io.BytesIO()
    noise.save(buf, format="PNG")
    out = images.prepare(buf.getvalue())
    assert out and len(out) <= images.MAX_BYTES and Image.open(io.BytesIO(out)).format == "JPEG"
    rgba = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    assert Image.open(io.BytesIO(images.prepare(buf.getvalue()))).mode == "RGB"
