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

    def fake(data: bytes, password: str | None = None) -> str:
        name = data.removeprefix(b"%PDF-1.4\n")  # gelen posta testleri: PDF başlığıyla sarılmış fixture
        if name.startswith(b"FIXTURE:"):
            return (FIXTURES / name[8:].decode()).read_text(encoding="utf-8")
        return real(data, password)

    monkeypatch.setattr(parsers, "extract_text", fake)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # testler dışarıya istek atmaz
    for k in ("APP_PASSWORD", "SECRET_KEY", "TELEGRAM_BOT_TOKEN", "BACKUP_PASSPHRASE", "GEMINI_MODEL",
              "MAIL_USER", "MAIL_PASSWORD", "MAIL_IMAP_HOST", "MAIL_SENDERS", "SMTP_HOST", "MAIL_FROM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JOBS_ENABLED", "0")
    monkeypatch.delenv("HESAPKITAP_IS_HUB", raising=False)
    from app import templates

    # şablon paylaşımı / topluluktan çekme ağa çıkmasın (ilgili testler kendi sahtesini kurar)
    monkeypatch.setattr(templates, "sync_community", lambda force=False: 0)
    monkeypatch.setattr(templates, "share_async", lambda spec: None)
    monkeypatch.setenv("HESAPKITAP_NO_AUTH", "1")  # test_auth bunu kendisi kaldırır
    from app import auth

    auth._account_fails.clear()  # hesap başı giriş sayacı süreç geneli; testler birbirini kilitlemesin


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Güvenlik ağı: hiçbir test geliştiricinin gerçek data/ekstre.db'sine dokunmasın; tmpdb kullanmayan test de
    boş, şeması kurulmuş geçici bir veritabanı görür (CI'daki temiz ortamla aynı)."""
    from app import db

    from app import system

    monkeypatch.setattr(system, "SYSTEM_DB", str(tmp_path / "system.db"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "isolated.db"))
    monkeypatch.setattr(db, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(db, "BILL_DIR", str(tmp_path / "bills"))
    db.init_db()


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
