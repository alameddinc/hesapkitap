"""Eşzamanlılık: belge okuyan (yavaş, senkron) uçlar olay döngüsünü kilitlememeli.

Canlıda 29 ekli mail işlenirken /api/auth/status 20 sn'de zaman aşımına düşmüştü: `async def` uç içinde senkron PDF/OCR/
yapay zekâ işi tek uvicorn işçisinin döngüsünü durduruyordu. Burada gerçek bir uvicorn sunucusu (aynı süreçte, arka plan
iş parçacığında) başlatılır; yavaş okuma sürerken başka bir isteğin hemen yanıt aldığı doğrulanır. TestClient/ASGITransport
istekleri tek tek işlediği için döngü kilitlenmesini güvenilir göstermez; gerçek soket + iki eşzamanlı istemci kullanılır.
"""
import socket
import threading
import time

import httpx
import pytest

from tests.conftest import fixture_bytes

SLOW = 1.5   # taklit edilen belge okuma süresi (sn)
FAST = 0.5   # bu sürede başka istekler yanıt vermeli


@pytest.fixture()
def server(tmpdb):
    import uvicorn

    from app.api.main import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not srv.started:
        assert time.time() < deadline, "sunucu açılmadı"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


def _slow(started: threading.Event, result=None):
    def fn(*args, **kwargs):
        started.set()
        time.sleep(SLOW)  # senkron iş: PDF ayrıştırma / tesseract / Gemini (httpx senkron) gibi
        return result() if callable(result) else result
    return fn


def _assert_responsive(base: str, send_slow) -> httpx.Response:
    """Yavaş isteği arka planda başlat; o sürerken /api/auth/status ve /api/meta FAST içinde dönmeli."""
    out: dict = {}

    def run():
        with httpx.Client(base_url=base, timeout=30) as c:
            out["r"] = send_slow(c)

    t = threading.Thread(target=run)
    t0 = time.monotonic()
    t.start()
    with httpx.Client(base_url=base, timeout=10) as c:
        assert _started.wait(5), "yavaş okuma başlamadı"
        for path in ("/api/auth/status", "/api/meta"):
            s = time.monotonic()
            r = c.get(path)
            took = time.monotonic() - s
            assert r.status_code == 200, (path, r.status_code, r.text)
            assert took < FAST, f"{path} {took:.2f} sn bekledi: olay döngüsü belge okumasıyla kilitlenmiş"
    t.join(30)
    assert time.monotonic() - t0 >= SLOW  # yavaş iş gerçekten araya girdi
    return out["r"]


_started = threading.Event()


@pytest.fixture(autouse=True)
def _reset_started():
    _started.clear()


def test_statement_upload_does_not_block(server, monkeypatch):
    import app.parsers as parsers

    real = parsers.extract_text

    def slow_extract(data, password=None):
        _slow(_started)()
        return real(data, password)

    monkeypatch.setattr(parsers, "extract_text", slow_extract)
    r = _assert_responsive(server, lambda c: c.post("/api/statements/upload", files=[
        ("files", ("a.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))]))
    assert r.status_code == 200 and r.json()[0]["status"] == "saved"


def test_bank_pdf_preview_does_not_block(server, monkeypatch):
    from app import bank_pdf

    res = {"rows": [], "bank": "Test Bank", "owner_name": None, "verified": True, "chain_bad": 0,
           "closing_ok": True, "iban": None}
    monkeypatch.setattr(bank_pdf, "parse_account_pdf", _slow(_started, res))
    r = _assert_responsive(server, lambda c: c.post("/api/bank-import/preview", files={
        "file": ("hareket.pdf", b"%PDF-yavas", "application/pdf")}))
    assert r.status_code == 200 and r.json()["bank"] == "Test Bank"


def test_document_parse_does_not_block(server, monkeypatch):
    from app import receipt

    doc = {"type": "receipt", "merchant": "MARKET", "total": 10.0, "items": []}
    monkeypatch.setattr(receipt, "parse_document", _slow(_started, lambda: dict(doc)))
    r = _assert_responsive(server, lambda c: c.post("/api/documents/parse", files={
        "file": ("fis.pdf", b"%PDF-fis", "application/pdf")}))
    assert r.status_code == 200 and r.json()["merchant"] == "MARKET"


def test_receipt_and_bill_parse_do_not_block(server, monkeypatch):
    from app import receipt

    monkeypatch.setattr(receipt, "parse_receipt", _slow(_started, lambda: {"merchant": "FIRIN", "total": 5.0}))
    r = _assert_responsive(server, lambda c: c.post("/api/receipts/parse", files={
        "file": ("fis.jpg", b"not-really-a-jpeg", "image/jpeg")}))
    assert r.status_code == 200 and r.json()["merchant"] == "FIRIN"

    _started.clear()
    monkeypatch.setattr(receipt, "enabled", lambda: True)
    monkeypatch.setattr(receipt, "parse_bill", _slow(_started, lambda: {"amount": 99.0}))
    r = _assert_responsive(server, lambda c: c.post("/api/bills/parse", files={
        "file": ("fatura.pdf", b"%PDF-fatura", "application/pdf")}))
    assert r.status_code == 200 and r.json()["amount"] == 99.0 and r.json()["file_name"]


def test_backup_restore_does_not_block(server, monkeypatch):
    from app import backup

    monkeypatch.setattr(backup, "restore", _slow(_started, {"ok": True}))
    r = _assert_responsive(server, lambda c: c.post("/api/settings/backup/restore", files={
        "file": ("yedek.db", b"x", "application/octet-stream")}))
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_two_users_upload_concurrently_without_mixing(server, monkeypatch):
    """İki kullanıcı aynı anda ekstre yükler: okuma iş parçacığında sürerken her biri kendi defterine (db.use_space
    bağlamı iş parçacığına taşınır) yazar; birinin ekstresi ötekinde görünmez."""
    import app.parsers as parsers
    from app import auth, system

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    admin = httpx.Client(base_url=server, timeout=10)
    assert admin.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    users = {}
    for name in ("ayse", "mehmet"):
        code = admin.post("/api/admin/invites", json={"note": name}).json()["code"]
        c = httpx.Client(base_url=server, timeout=30)
        r = c.post("/api/auth/register", json={"code": code, "username": name, "password": f"{name}-sifre-12345",
                                               "consent": ["kosullar", "acik_riza"]})
        assert r.status_code == 200, r.text
        users[name] = c

    real = parsers.extract_text
    both = threading.Barrier(2, timeout=5)

    def slow_extract(data, password=None):
        try:
            both.wait()  # iki okuma gerçekten aynı anda sürüyor
        except threading.BrokenBarrierError:
            pass
        time.sleep(0.3)
        return real(data, password)

    monkeypatch.setattr(parsers, "extract_text", slow_extract)
    files = {"ayse": "vakifbank_a.txt", "mehmet": "kuveytturk.txt"}
    out: dict = {}

    def up(name):
        out[name] = users[name].post("/api/statements/upload", files=[
            ("files", (files[name].replace(".txt", ".pdf"), fixture_bytes(files[name]), "application/pdf"))])

    ts = [threading.Thread(target=up, args=(n,)) for n in files]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not both.broken, "yüklemeler eşzamanlı çalışmadı"
    banks = {}
    for name, c in users.items():
        assert out[name].status_code == 200 and out[name].json()[0]["status"] == "saved", out[name].text
        banks[name] = {s["bank"] for s in c.get("/api/statements").json()}
    assert len(banks["ayse"]) == 1 and len(banks["mehmet"]) == 1 and banks["ayse"] != banks["mehmet"], banks
    # yöneticinin (ana veritabanı) defterine de bir şey yazılmadı
    assert admin.get("/api/statements").json() == []
    # dosya düzeyinde: her kullanıcının veritabanı ayrı ve yalnız kendi ekstresini taşıyor
    import sqlite3

    paths = {name: system.user_db_path(system.user_by_name(name)) for name in users}
    assert paths["ayse"] != paths["mehmet"]
    for name, path in paths.items():
        with sqlite3.connect(path) as con:
            assert {r[0] for r in con.execute("SELECT bank FROM statements")} == banks[name]
    for c in (admin, *users.values()):
        c.close()
