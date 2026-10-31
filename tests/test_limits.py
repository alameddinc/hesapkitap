"""Belge okuma kaynak sınırları (denetim M7 / O2): dosya sayısı ve boyutu, zip bombası, PDF sayfa/süre sınırı,
ağır okuma yuvası, sayfa başı page.close() ve aynı PDF'in tek kez metne çevrilmesi. Belgeler docs/demo'daki uydurma
örneklerdir."""
import io
import zipfile
from pathlib import Path

import pytest

from tests.conftest import fixture_bytes

DEMO = Path(__file__).parent.parent / "docs" / "demo"


def _bomb(member: str, mb: int = 60) -> bytes:
    """Açılınca mb MB sıfır olan, sıkışmış hâli birkaç yüz KB'lık zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(member, b"\0" * (mb * 1024 * 1024))
    return buf.getvalue()


def test_too_many_files_rejected(client):
    files = [("files", (f"e{i}.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf")) for i in range(31)]
    r = client.post("/api/statements/upload", files=files)
    assert r.status_code == 413
    assert "en fazla 30 dosya" in r.json()["detail"]


def test_file_size_limit(client, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_MAX_FILE_MB", "0.00001")  # ~10 bayt
    r = client.post("/api/statements/upload", files=[("files", ("a.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))])
    assert r.status_code == 200
    assert r.json()[0]["status"] == "error" and "çok büyük" in r.json()[0]["message"]
    r = client.post("/api/documents/parse", files=[("file", ("f.jpg", b"x" * 100, "image/jpeg"))])
    assert r.status_code == 413 and "çok büyük" in r.json()["detail"]


def test_xlsx_zip_bomb_rejected(client):
    bomb = _bomb("xl/worksheets/sheet1.xml")
    assert len(bomb) < 1024 * 1024
    r = client.post("/api/statements/upload", files=[("files", ("hareketler.xlsx", bomb, "application/vnd.ms-excel"))])
    assert r.json()[0]["status"] == "error" and "sıkıştırma bombası" in r.json()[0]["message"]
    r = client.post("/api/bank-import/preview", files={"file": ("hareketler.xlsx", bomb, "application/vnd.ms-excel")})
    assert r.status_code in (413, 422) and "sıkıştırma bombası" in r.json()["detail"]


def test_backup_zip_bomb_rejected(client):
    bomb = _bomb("hesapkitap.db")
    r = client.post("/api/settings/backup/restore", files={"file": ("yedek.zip", bomb, "application/zip")})
    assert r.status_code == 422 and "sıkıştırma bombası" in r.json()["detail"]


def test_check_zip_total_size():
    from app import limits

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("a", b"x" * 2048)
    limits.check_zip(buf.getvalue(), 4096)
    with pytest.raises(limits.LimitError, match="en fazla"):
        limits.check_zip(buf.getvalue(), 1024)


# --- PDF -------------------------------------------------------------------------------------------------------

def _demo_pdf() -> bytes:
    return (DEMO / "3-kuveytturk-ekstre.pdf").read_bytes()


def test_pdf_page_close_and_same_text(monkeypatch):
    """Her sayfa kapatılır (bellek sayfa sayısıyla büyümesin); metin kapatmadan okunanla birebir aynı."""
    import pdfplumber
    from pdfplumber.page import Page

    from app import parsers

    data = _demo_pdf()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        n = len(pdf.pages)
        plain = "\n".join(p.extract_text() or "" for p in pdf.pages)
    closed = []
    real_close = Page.close
    monkeypatch.setattr(Page, "close", lambda self: (closed.append(self.page_number), real_close(self))[1])
    parsers._TEXTS.clear()
    assert parsers.extract_text(data) == plain
    assert set(closed) == set(range(1, n + 1))  # (belge kapanırken pdfplumber de bir kez daha kapatır)


def test_pdf_page_and_time_limits(monkeypatch):
    from app import limits, parsers

    data = _demo_pdf()
    parsers._TEXTS.clear()
    monkeypatch.setenv("HESAPKITAP_PDF_MAX_PAGES", "0")
    with pytest.raises(limits.LimitError, match="sayfa"):
        parsers.extract_text(data)
    monkeypatch.setenv("HESAPKITAP_PDF_MAX_PAGES", "300")
    monkeypatch.setenv("HESAPKITAP_PDF_TIMEOUT", "-1")
    with pytest.raises(limits.LimitError, match="uzun sürede"):
        parsers.extract_text(data)


def test_card_pdf_converted_to_text_once(client, monkeypatch):
    """Yüklemede tür tahmini ve okuyucu aynı metni kullanır: PDF bir kez açılır."""
    import pdfplumber

    from app import parsers

    parsers._TEXTS.clear()
    opened = []
    real = pdfplumber.open
    monkeypatch.setattr(pdfplumber, "open", lambda *a, **kw: (opened.append(1), real(*a, **kw))[1])
    r = client.post("/api/statements/upload", files=[("files", ("k.pdf", _demo_pdf(), "application/pdf"))])
    assert r.status_code == 200 and r.json()[0]["status"] == "saved"
    assert len(opened) == 1


# --- ağır okuma yuvası -----------------------------------------------------------------------------------------

def test_heavy_slot_wait_times_out(client, monkeypatch):
    from app.api import main

    monkeypatch.setenv("HESAPKITAP_PARSE_WAIT", "0.05")
    held = 0
    while main._HEAVY.acquire(blocking=False):  # tüm yuvalar başka okumalarda
        held += 1
    try:
        r = client.post("/api/documents/parse", files=[("file", ("f.jpg", b"x" * 100, "image/jpeg"))])
        assert r.status_code == 503 and "biraz sonra" in r.json()["detail"]
        msg = client.post("/api/share", files=[("file", ("e.pdf", fixture_bytes("kuveytturk.txt"), "application/pdf"))]).text
        assert "biraz sonra" in msg
        r = client.post("/api/settings/backup/restore", files={"file": ("y.zip", b"PK", "application/zip")})
        assert r.status_code == 503
    finally:
        for _ in range(held):
            main._HEAVY.release()


def test_heavy_endpoints_are_wrapped():
    """Belge okuyan her uç _heavy'den geçer (denetim O2 listesi)."""
    from app.api.main import app

    heavy = {"/api/documents/parse", "/api/settings/backup/restore", "/api/settings/backup/{name}/restore",
             "/api/demo/sample/{key}", "/api/statements/upload", "/api/receipts/parse", "/api/bills/parse",
             "/api/bank-import/preview", "/api/bank-import/run"}
    for r in app.routes:
        if getattr(r, "path", None) in heavy and "POST" in r.methods:
            assert getattr(r.endpoint, "__wrapped__", None) is not None, r.path
