"""SQLite yedekleme: tutarlı kopya (sqlite backup API), 30 gün saklama, şifreli dış kopya.

Şifreleme: Fernet (AES-128-CBC + HMAC), anahtar BACKUP_PASSPHRASE'ten PBKDF2 ile türetilir.
Geri açma:  python -m app.backup decrypt ekstre-20261019.db.enc  →  ekstre-20261019.db
"""
import base64
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app import db

KEEP_DAYS = 30
MAGIC = b"EKSTRE1"  # dosya başı: MAGIC + 16 bayt salt + fernet token


def backup_dir() -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(db.db_path())), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def make_backup(today: date | None = None) -> str:
    """Çalışan veritabanının tutarlı kopyasını al (yazma sürerken bile güvenli)."""
    today = today or date.today()
    path = os.path.join(backup_dir(), f"ekstre-{today:%Y%m%d}.db")
    src = sqlite3.connect(db.db_path())
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    prune(today)
    return path


def prune(today: date | None = None) -> list[str]:
    today = today or date.today()
    removed = []
    for name in os.listdir(backup_dir()):
        if not (name.startswith("ekstre-") and name[7:15].isdigit()):
            continue
        d = datetime.strptime(name[7:15], "%Y%m%d").date()
        if today - d > timedelta(days=KEEP_DAYS):
            os.remove(os.path.join(backup_dir(), name))
            removed.append(name)
    return removed


def list_backups() -> list[dict]:
    out = []
    for name in sorted(os.listdir(backup_dir()), reverse=True):
        if name.endswith(".db"):
            p = os.path.join(backup_dir(), name)
            out.append({"name": name, "size": os.path.getsize(p),
                        "created": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="minutes")})
    return out


def _fernet(passphrase: str, salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480_000)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode())))


def encrypt(data: bytes, passphrase: str) -> bytes:
    salt = os.urandom(16)
    return MAGIC + salt + _fernet(passphrase, salt).encrypt(data)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("Ekstre yedeği değil")
    salt, token = blob[len(MAGIC):len(MAGIC) + 16], blob[len(MAGIC) + 16:]
    return _fernet(passphrase, salt).decrypt(token)


def passphrase() -> str | None:
    from app import config

    return config.get("BACKUP_PASSPHRASE") or None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "decrypt":
        pw = passphrase() or input("Yedek şifresi: ")
        src = sys.argv[2]
        out = src[:-4] if src.endswith(".enc") else src + ".db"
        with open(src, "rb") as f, open(out, "wb") as g:
            g.write(decrypt(f.read(), pw))
        print(f"Açıldı: {out}")
    elif len(sys.argv) == 2 and sys.argv[1] == "now":
        print(make_backup())
    else:
        print("Kullanım: python -m app.backup now | decrypt <dosya.enc>")
