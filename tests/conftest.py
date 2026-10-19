"""Ortak test ayarları. Gerçek ekstre kullanılmaz: tests/fixtures/*.txt sahte ekstre metinleridir.

PDF yerine b"FIXTURE:<dosya>" baytları yüklenir; extract_text bunu fixture metnine çevirir.
"""
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
STATEMENTS = ["vakifbank_a.txt", "vakifbank_b.txt", "kuveytturk.txt"]


def fixture_bytes(name: str) -> bytes:
    return f"FIXTURE:{name}".encode()


@pytest.fixture(autouse=True)
def _fixture_pdfs(monkeypatch):
    import app.parsers as parsers

    real = parsers.extract_text

    def fake(data: bytes) -> str:
        if data.startswith(b"FIXTURE:"):
            return (FIXTURES / data[8:].decode()).read_text(encoding="utf-8")
        return real(data)

    monkeypatch.setattr(parsers, "extract_text", fake)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # testler dışarıya istek atmaz
    for k in ("APP_PASSWORD", "SECRET_KEY", "TELEGRAM_BOT_TOKEN", "BACKUP_PASSPHRASE", "GEMINI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JOBS_ENABLED", "0")
    monkeypatch.setenv("HESAPKITAP_NO_AUTH", "1")  # test_auth bunu kendisi kaldırır


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(db, "BILL_DIR", str(tmp_path / "bills"))
    db.init_db()
    return db


@pytest.fixture()
def client(tmpdb):
    from fastapi.testclient import TestClient

    from app.api.main import app

    return TestClient(app)


@pytest.fixture()
def upload(client):
    """Sahte ekstreleri API üzerinden yükle."""
    def _upload(names=STATEMENTS):
        files = [("files", (n.replace(".txt", ".pdf"), fixture_bytes(n), "application/pdf")) for n in names]
        return client.post("/api/statements/upload", files=files).json()
    return _upload
