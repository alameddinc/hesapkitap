"""SQLite yedekleme: tutarlı kopya (sqlite backup API), 30 gün saklama, şifreli dış kopya.

Şifreleme: Fernet (AES-128-CBC + HMAC), anahtar BACKUP_PASSPHRASE'ten PBKDF2 ile türetilir.
Geri açma:  python -m app.backup decrypt ekstre-20261019.db.enc  →  ekstre-20261019.db

Taşıma: export_zip() veritabanı + ekstre PDF'leri + fatura dosyalarını tek zip yapar; restore() zip'i, .db'yi ya da
şifreli .enc'yi doğrulayıp geri yükler (önce mevcut hal yedeklenir).
"""
import base64
import os
import sqlite3
import sys
import tempfile
import zipfile
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


DB_NAME = "hesapkitap.db"
README = """HesapKitap yedeği
=================
hesapkitap.db : tüm kayıtlar (SQLite)
uploads/      : yüklenen ekstre PDF'leri
bills/        : fatura dosyaları

Geri yüklemek için: HesapKitap → Ayarlar → Yedekler → "Yedekten geri yükle" ile bu zip'i seç.
Bu dosya kişisel finans verini içerir; güvenli bir yerde sakla.
"""


def _snapshot(path: str) -> None:
    src = sqlite3.connect(db.db_path())
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def export_zip() -> str:
    """Tüm veri tek zip (geçici dosya yolu; çağıran gönderip siler)."""
    fd, out = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, DB_NAME)
        _snapshot(snap)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, DB_NAME)
            z.writestr("OKUBENI.txt", README)
            for arc, folder in (("uploads", db.upload_dir()), ("bills", db.bill_dir())):
                if os.path.isdir(folder):
                    for name in sorted(os.listdir(folder)):
                        p = os.path.join(folder, name)
                        if os.path.isfile(p):
                            z.write(p, f"{arc}/{name}")
    return out


class RestoreError(ValueError):
    pass


def _check_db(path: str) -> dict:
    try:
        c = sqlite3.connect(path)
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("entries", "statements") if t in tables}
        c.close()
    except sqlite3.DatabaseError as e:
        raise RestoreError("Dosya okunamadı: HesapKitap yedeği değil ya da bozuk") from e
    if ok != "ok" or not {"entries", "statements", "settings"} <= tables:
        raise RestoreError("Bu dosya bir HesapKitap yedeği değil ya da bozuk")
    return counts


def restore(data: bytes, filename: str = "", passphrase: str | None = None) -> dict:
    """Yedeği geri yükle. Önce mevcut veritabanı 'geri yükleme öncesi' olarak yedeklenir."""
    files: list[tuple[str, bytes]] = []
    if data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(__import__("io").BytesIO(data))
        except zipfile.BadZipFile as e:
            raise RestoreError("Zip açılamadı") from e
        if DB_NAME not in z.namelist():
            raise RestoreError(f"Zip içinde {DB_NAME} yok; HesapKitap'tan alınmış bir yedek seç")
        raw = z.read(DB_NAME)
        for n in z.namelist():
            top, _, name = n.partition("/")
            if top in ("uploads", "bills") and name and "/" not in name and not name.startswith("."):
                files.append((n, z.read(n)))
    elif data.startswith(MAGIC):
        if not passphrase:
            raise RestoreError("Bu yedek şifreli: yedek şifresini gir")
        try:
            raw = decrypt(data, passphrase)
        except Exception as e:  # noqa: BLE001 - yanlış şifre
            raise RestoreError("Şifre yanlış ya da dosya bozuk") from e
    else:
        raw = data
    if not raw.startswith(b"SQLite format 3\x00"):
        raise RestoreError("Bu dosya bir HesapKitap yedeği değil")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "restore.db")
        with open(path, "wb") as f:
            f.write(raw)
        counts = _check_db(path)
        before = os.path.join(backup_dir(), f"ekstre-{datetime.now():%Y%m%d-%H%M%S}-geri-yukleme-oncesi.db")
        _snapshot(before)
        src, dst = sqlite3.connect(path), sqlite3.connect(db.db_path())
        try:
            src.backup(dst)  # canlı bağlantılar açıkken de tutarlı kopya
        finally:
            dst.close()
            src.close()
    for n, blob in files:
        top, _, name = n.partition("/")
        folder = db.upload_dir() if top == "uploads" else db.bill_dir()
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, os.path.basename(name)), "wb") as f:
            f.write(blob)
    db.init_db()  # eski sürümden gelen yedek: göçler uygulanır
    return {**counts, "files": len(files), "before": os.path.basename(before)}


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
