"""Sunucu geneli yedek (çok kullanıcılı kurulum): bütün veri klasörü tek, şifreli arşiv.

app/backup.py tek bir veritabanının (kullanıcının kendi alanının) yedeğidir; bu modül sunucuyu işletenin yedeğidir:
system.db + her kullanıcının ekstre.db'si (SQLite online backup API ile, uygulama çalışırken tutarlı kopya) → tar →
AES-256-GCM ile parça parça şifreli tek dosya (bellek kullanımı sabit, ~1 MB).

Belgeler (uploads/ bills/ receipts/ statements-bank/, app/storage.py):
  - Dosyalar nesne deposundaysa (HESAPKITAP_FILES_S3_* ya da yedek container'ında HESAPKITAP_FILES_IN_S3=1) arşive
    GİRMEZ: zaten depodadırlar; arşiv yalnız veritabanlarıdır.
  - Yerel diskteyse artımlı: içerik adlı dosyalar değişmez, her gün yeniden arşivlenmez. Her dosya bir kez, SHA-256
    adıyla şifreli olarak <yedek klasörü>/files/<sha256>.hkenc'e (ve uzak kopyada <önek>files/) yazılır; günün
    manifest.json'u hangi yolun hangi özete karşılık geldiğini tutar. Hiçbir saklanan arşivde geçmeyen dosya
    (files/index.json'daki son görülme günü KEEP_DAYS'i geçen) silinir. verify / extract bu klasörden okur.

Arşive girmeyenler: backups/ klasörleri (zaten yedek), users/_silinen/ (silinen hesaplar; yedekte yaşamasın),
SQLite -wal/-shm/-journal dosyaları (kopya zaten tutarlı).

Saklama: yerel ve (ayarlıysa) uzak kopyada KEEP_DAYS günden eski arşivler silinir; users/_silinen/ altındaki
silinmiş hesap klasörleri de aynı süre sonunda kalıcı silinir (system.purge_archived).

Ortam değişkenleri:
  HESAPKITAP_BACKUP_KEY       zorunlu: şifre/anahtar (uzun rastgele bir dize; sunucu dışında da sakla!)
  HESAPKITAP_BACKUP_DIR       zorunlu: arşivlerin yazılacağı klasör (veri klasörünün DIŞINDA olmalı)
  HESAPKITAP_BACKUP_KEEP_DAYS saklama süresi, gün (varsayılan 30)
  BACKUP_S3_ENDPOINT / BACKUP_S3_BUCKET / BACKUP_S3_KEY / BACKUP_S3_SECRET   isteğe bağlı: S3 uyumlu dış kopya
  BACKUP_S3_REGION (varsayılan "auto": Cloudflare R2) / BACKUP_S3_PREFIX (varsayılan "hesapkitap/")
  BACKUP_S3_* yoksa ve HESAPKITAP_FILES_S3_* varsa: dosyaların bucket'ı, "backups/" öneki (HESAPKITAP_BACKUP_S3_PREFIX)
  HESAPKITAP_FILES_IN_S3=1   yedek container'ı dosya deposunun kimliğini görmez; dosyaların depoda olduğunu bu söyler

Kullanım (container içinde ya da aynı ortamda):
  python -m app.cloud_backup run                     yedek al + dışarı gönder + eskileri temizle
  python -m app.cloud_backup list                    yerel ve uzak arşivler
  python -m app.cloud_backup verify <arşiv>          şifreyi çöz, sağlama toplamları + PRAGMA integrity_check
  python -m app.cloud_backup extract <arşiv> <klasör> [--user ID]   aç (tamamı ya da tek kullanıcı)
  python -m app.cloud_backup fetch <ad> [klasör]     uzak kopyadan indir (+ arşivin gerektirdiği files/ blob'ları)
"""
import functools
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import tarfile
import time
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.s3 import EMPTY_SHA, S3, S3Error, config_from_env, sigv4_headers  # noqa: F401 - eski adlar burada da

FORMAT = 2  # 1: belgeler tar'ın içinde; 2: belgeler files/<sha256>.hkenc (artımlı) ya da nesne deposunda
MAGIC = b"HKBACK1\x00"
CHUNK = 1 << 20
SUFFIX = ".tar.hkenc"
NAME_RE = re.compile(r"^hesapkitap-(\d{8}T\d{6}Z)" + re.escape(SUFFIX) + "$")
SKIP_DIRS = {"backups", "_silinen"}
SKIP_SUFFIXES = ("-wal", "-shm", "-journal")
SQLITE_HEADER = b"SQLite format 3\x00"
DOC_DIRS = {"uploads", "bills", "receipts", "statements-bank"}  # app/storage.KINDS
BLOB_DIR = "files"
BLOB_SUFFIX = ".hkenc"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class BackupError(RuntimeError):
    pass


def files_in_s3() -> bool:
    """Belgeler nesne deposunda mı? (yedek container'ı depo kimliğini görmez: HESAPKITAP_FILES_IN_S3=1)"""
    if os.environ.get("HESAPKITAP_FILES_IN_S3") == "1":
        return True
    try:
        return config_from_env("HESAPKITAP_FILES_S3_") is not None
    except ValueError as e:
        raise BackupError(str(e)) from None


# ---------------------------------------------------------------- ayarlar

def keep_days() -> int:
    return int(os.environ.get("HESAPKITAP_BACKUP_KEEP_DAYS") or 30)


def data_dir() -> str:
    from app import system

    return os.path.dirname(os.path.abspath(system.SYSTEM_DB))


def backup_dir() -> str:
    d = os.environ.get("HESAPKITAP_BACKUP_DIR")
    if not d:
        raise BackupError("HESAPKITAP_BACKUP_DIR ayarlı değil (veri klasörünün dışında bir klasör olmalı)")
    d = os.path.abspath(d)
    data = data_dir()
    if d == data or d.startswith(data + os.sep):
        raise BackupError("HESAPKITAP_BACKUP_DIR veri klasörünün içinde olamaz")
    os.makedirs(d, exist_ok=True)
    return d


def key() -> str:
    k = os.environ.get("HESAPKITAP_BACKUP_KEY")
    if not k or len(k) < 16:
        raise BackupError("HESAPKITAP_BACKUP_KEY ayarlı değil ya da çok kısa (en az 16 karakter)")
    return k


# ---------------------------------------------------------------- şifreleme (akış, parça parça AES-256-GCM)
# Dosya: MAGIC(8) | salt(16) | nonce öneki(7) | parça boyu(4)  ve ardından her parça: uzunluk(4) | şifreli+etiket.
# Parça nonce'u = önek | sıra no(4) | son mu(1). Başlık her parçaya ek veri (AAD) olarak bağlıdır:
# parça yer değiştirme, silme ya da arşivi sondan kesme şifre çözmede hata verir.

@functools.lru_cache(maxsize=64)  # bir çalışmanın bütün blob'ları tek tuzla: anahtar bir kez türetilir
def _derive(passphrase: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480_000).derive(passphrase.encode())


class EncryptWriter(io.RawIOBase):
    def __init__(self, out, passphrase: str, chunk: int = CHUNK, salt: bytes | None = None):
        # salt verilirse (aynı çalışmadaki blob'lar) anahtar önbellekten; nonce öneki her dosyada yeni ve rastgele
        salt, self._prefix = salt or os.urandom(16), os.urandom(7)
        self._header = MAGIC + salt + self._prefix + struct.pack(">I", chunk)
        self._aead, self._out, self._chunk = AESGCM(_derive(passphrase, salt)), out, chunk
        self._buf, self._n = bytearray(), 0
        out.write(self._header)

    def writable(self):
        return True

    def _emit(self, data: bytes, last: bool) -> None:
        nonce = self._prefix + struct.pack(">I", self._n) + (b"\x01" if last else b"\x00")
        ct = self._aead.encrypt(nonce, bytes(data), self._header)
        self._out.write(struct.pack(">I", len(ct)) + ct)
        self._n += 1

    def write(self, b) -> int:
        self._buf += b
        while len(self._buf) > self._chunk:  # son parça close()'da: en az bir bayt ya da boş "son" parçası
            self._emit(self._buf[:self._chunk], False)
            del self._buf[:self._chunk]
        return len(b)

    def close(self) -> None:
        if not self.closed and not self._out.closed:
            self._emit(self._buf, True)
            self._buf.clear()
        super().close()


def decrypt_chunks(f, passphrase: str):
    header = f.read(len(MAGIC) + 16 + 7 + 4)
    if not header.startswith(MAGIC):
        raise BackupError("HesapKitap sunucu yedeği değil")
    salt, prefix = header[8:24], header[24:31]
    aead = AESGCM(_derive(passphrase, salt))
    n, pending = 0, f.read(4)
    while True:
        if len(pending) < 4:
            raise BackupError("Arşiv eksik (kesilmiş)")
        ct = f.read(struct.unpack(">I", pending)[0])
        pending = f.read(4)
        last = not pending
        try:
            yield aead.decrypt(prefix + struct.pack(">I", n) + (b"\x01" if last else b"\x00"), ct, header)
        except Exception as e:
            raise BackupError("Şifre çözülemedi: anahtar yanlış ya da arşiv bozuk") from e
        if last:
            return
        n += 1


class DecryptReader(io.RawIOBase):
    def __init__(self, f, passphrase: str):
        self._it, self._buf = decrypt_chunks(f, passphrase), b""

    def readable(self):
        return True

    def readinto(self, b) -> int:
        while not self._buf:
            try:
                self._buf = next(self._it)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n], self._buf = self._buf[:n], self._buf[n:]
        return n


# ---------------------------------------------------------------- arşiv

def _is_sqlite(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(16) == SQLITE_HEADER


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _walk(root: str):
    """Arşive girecek dosyalar (göreli yol). backups/ ve _silinen/ hiçbir derinlikte alınmaz."""
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith(".staging"))
        for name in sorted(files):
            if name.endswith(SKIP_SUFFIXES) or name == ".DS_Store":
                continue
            p = os.path.join(d, name)
            if os.path.isfile(p) and not os.path.islink(p):
                yield os.path.relpath(p, root)


def _is_doc(rel: str) -> bool:
    """Saklanan belge mi (uploads/x.pdf, users/7/receipts/y.jpg ...)? Ötekiler (veritabanları, site/, shortcuts/) değil."""
    parts = rel.replace(os.sep, "/").split("/")
    return len(parts) >= 2 and parts[-2] in DOC_DIRS


def _integrity(path: str) -> str:
    c = sqlite3.connect(path)
    try:
        return c.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        c.close()


def _snapshot(src: str, dst: str) -> None:
    s, d = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        s.backup(d)  # yazma sürerken bile tutarlı kopya
    finally:
        d.close()
        s.close()


def create(now: datetime | None = None) -> str:
    """Arşivi oluştur; yolunu döndür. Yarım kalan arşiv .part olarak kalmaz (sonra ad verilir)."""
    now = now or datetime.now(timezone.utc)
    root, out_dir, passphrase = data_dir(), backup_dir(), key()
    in_s3 = files_in_s3()
    blobs = BlobStore(out_dir, passphrase)
    name = f"hesapkitap-{now:%Y%m%dT%H%M%SZ}{SUFFIX}"
    final, part = os.path.join(out_dir, name), os.path.join(out_dir, name + ".part")
    staging = os.path.join(out_dir, f".staging-{now:%Y%m%dT%H%M%S}-{os.getpid()}")
    os.makedirs(staging)
    manifest = {"format": FORMAT, "created": now.isoformat(timespec="seconds"), "files": [],
                "documents": "s3" if in_s3 else "blobs"}
    try:
        with open(part, "wb") as raw:
            enc = EncryptWriter(raw, passphrase)
            with tarfile.open(fileobj=enc, mode="w|") as tar:
                for rel in _walk(root):
                    src = os.path.join(root, rel)
                    entry = {"path": rel}
                    if _is_doc(rel):
                        if in_s3:
                            continue  # nesne deposunda (yerelde kalmış geçiş artığı da oradadır: storage migrate)
                        sha, size = blobs.add(src, rel)
                        manifest["files"].append(entry | {"kind": "file", "size": size, "sha256": sha, "blob": sha})
                        continue
                    if rel.endswith(".db") and _is_sqlite(src):
                        copy = os.path.join(staging, rel)
                        os.makedirs(os.path.dirname(copy), exist_ok=True)
                        _snapshot(src, copy)
                        ok = _integrity(copy)
                        if ok != "ok":  # bozuk DB'yi sessizce yedekleme: haber ver ama arşive yine koy
                            print(f"UYARI: {rel} integrity_check: {ok}", file=sys.stderr)
                        entry |= {"kind": "sqlite", "integrity": ok}
                        src = copy
                    else:
                        entry["kind"] = "file"
                    entry |= {"size": os.path.getsize(src), "sha256": _sha256(src)}
                    tar.add(src, arcname=f"data/{rel}", recursive=False)
                    manifest["files"].append(entry)
                    if entry["kind"] == "sqlite":
                        os.remove(src)
                blob = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size, info.mtime = len(blob), int(now.timestamp())
                tar.addfile(info, io.BytesIO(blob))
            enc.close()
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(part, 0o600)
        os.replace(part, final)
        blobs.commit(now)  # yalnız arşiv tamamlanınca: bu blob'lar bugün "görüldü"
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if os.path.exists(part):
            os.remove(part)
    return final


# ---------------------------------------------------------------- belgeler: içerik adlı, şifreli, bir kez (artımlı)

class BlobStore:
    """<yedek klasörü>/files/<sha256>.hkenc + index.json ({sha: {"seen": gün, "remote": bool}}, yollar için stat
    önbelleği). Dizinde yalnız özetler ve göreli yollar var; içerik ve ad bilgisi şifreli blob'larda."""

    def __init__(self, out_dir: str, passphrase: str):
        self.dir = os.path.join(out_dir, BLOB_DIR)
        self.passphrase, self._salt = passphrase, None
        self.index = load_index(self.dir)
        self.seen: set[str] = set()

    def path(self, sha: str) -> str:
        return os.path.join(self.dir, sha + BLOB_SUFFIX)

    def add(self, src: str, rel: str) -> tuple[str, int]:
        st = os.stat(src)
        cached = self.index["paths"].get(rel)
        sha = cached[2] if cached and cached[:2] == [st.st_size, st.st_mtime_ns] else _sha256(src)
        self.index["paths"][rel] = [st.st_size, st.st_mtime_ns, sha]
        if not os.path.exists(self.path(sha)):
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            self._salt = self._salt or os.urandom(16)
            part = self.path(sha) + ".part"
            with open(src, "rb") as f, open(part, "wb") as raw:
                enc = EncryptWriter(raw, self.passphrase, salt=self._salt)
                for block in iter(lambda: f.read(CHUNK), b""):
                    enc.write(block)
                enc.close()
                raw.flush()
                os.fsync(raw.fileno())
            os.chmod(part, 0o600)
            os.replace(part, self.path(sha))
            self.index["blobs"].setdefault(sha, {})["remote"] = False
        self.seen.add(sha)
        return sha, st.st_size

    def commit(self, now: datetime) -> None:
        day = now.date().isoformat()
        for sha in self.seen:
            self.index["blobs"].setdefault(sha, {"remote": False})["seen"] = day
        self.index["paths"] = {r: v for r, v in self.index["paths"].items() if v[2] in self.index["blobs"]}
        if self.seen or os.path.isdir(self.dir):
            save_index(self.dir, self.index)


def load_index(d: str) -> dict:
    try:
        with open(os.path.join(d, "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        return {"blobs": dict(idx.get("blobs") or {}), "paths": dict(idx.get("paths") or {})}
    except (FileNotFoundError, ValueError):
        return {"blobs": {}, "paths": {}}


def save_index(d: str, idx: dict) -> None:
    os.makedirs(d, mode=0o700, exist_ok=True)
    p = os.path.join(d, "index.json")
    with open(p + ".part", "w", encoding="utf-8") as f:
        json.dump(idx, f, separators=(",", ":"))
    os.chmod(p + ".part", 0o600)
    os.replace(p + ".part", p)


def read_blob(path: str, passphrase: str) -> bytes:
    with open(path, "rb") as f:
        return b"".join(decrypt_chunks(f, passphrase))


def _blob_dirs(archive: str) -> list[str]:
    """Blob'ların arandığı yerler: arşivin yanındaki files/, sonra (ayarlıysa) yedek klasörünün files/."""
    out = [os.path.join(os.path.dirname(os.path.abspath(archive)), BLOB_DIR)]
    if os.environ.get("HESAPKITAP_BACKUP_DIR"):
        out.append(os.path.join(os.path.abspath(os.environ["HESAPKITAP_BACKUP_DIR"]), BLOB_DIR))
    return list(dict.fromkeys(out))


def _find_blob(archive: str, sha: str) -> str | None:
    if not SHA_RE.match(sha or ""):
        return None
    return next((p for d in _blob_dirs(archive) if os.path.exists(p := os.path.join(d, sha + BLOB_SUFFIX))), None)


def _safe_rel(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return bool(rel) and not rel.startswith("/") and all(p not in ("", ".", "..") for p in parts)


def _members(tar_path: str, passphrase: str):
    f = open(tar_path, "rb")
    tar = tarfile.open(fileobj=io.BufferedReader(DecryptReader(f, passphrase), CHUNK), mode="r|")
    return f, tar


def _user_match(rel: str, uid: int | None) -> bool:
    return uid is None or rel.startswith(f"users/{uid}/")


def extract(archive: str, dest: str, user_id: int | None = None, passphrase: str | None = None) -> dict:
    """Arşivi dest/ altına aç (dest/data/..., dest/manifest.json). user_id: yalnız users/<id>/ alınır."""
    passphrase = passphrase or key()
    os.makedirs(dest, exist_ok=True)
    f, tar = _members(archive, passphrase)
    names = []
    try:
        with tar:
            for m in tar:
                rel = m.name.removeprefix("data/")
                if m.name == "manifest.json" or (m.name.startswith("data/") and _user_match(rel, user_id)):
                    if hasattr(tarfile, "data_filter"):
                        tar.extract(m, dest, filter="data")
                    else:  # pragma: no cover - eski Python
                        tar.extract(m, dest)
                    names.append(m.name)
    finally:
        f.close()
    restored, missing = _restore_blobs(archive, dest, user_id, passphrase)
    out = {"dest": dest, "files": len([n for n in names if n != "manifest.json"]) + restored}
    if missing:
        out["missing_blobs"] = missing
    return out


def _restore_blobs(archive: str, dest: str, user_id: int | None, passphrase: str) -> tuple[int, int]:
    """Biçim 2: manifest'teki belgeleri files/<sha256>.hkenc'ten dest/data/<yol>'a aç. (açılan, bulunamayan)"""
    mpath = os.path.join(dest, "manifest.json")
    if not os.path.exists(mpath):
        return 0, 0
    with open(mpath, encoding="utf-8") as fh:
        manifest = json.load(fh)
    n = missing = 0
    for e in manifest.get("files", []):
        if not e.get("blob") or not _user_match(e["path"], user_id) or not _safe_rel(e["path"]):
            continue
        src = _find_blob(archive, e["blob"])
        if not src:
            missing += 1
            continue
        out = os.path.join(dest, "data", *e["path"].replace("\\", "/").split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as g:
            g.write(read_blob(src, passphrase))
        n += 1
    return n, missing


def verify(archive: str, passphrase: str | None = None, workdir: str | None = None) -> dict:
    """Şifreyi çöz, aç, manifest'teki sağlama toplamlarını ve her DB'nin PRAGMA integrity_check'ini denetle."""
    import tempfile

    # workdir: büyük arşivde /tmp (tmpfs, bellek) yerine diskte açmak için; ".staging-" öneki: yarıda kalırsa
    # prune_local ertesi gün temizler
    with tempfile.TemporaryDirectory(prefix=".staging-verify-", dir=workdir) as tmp:
        extract(archive, tmp, passphrase=passphrase)
        mpath = os.path.join(tmp, "manifest.json")
        if not os.path.exists(mpath):
            raise BackupError("Arşivde manifest.json yok")
        manifest = json.load(open(mpath, encoding="utf-8"))
        problems, dbs, users = [], 0, set()
        for e in manifest["files"]:
            p = os.path.join(tmp, "data", e["path"])
            if not os.path.exists(p):
                problems.append(f"eksik: {e['path']}")
                continue
            if _sha256(p) != e["sha256"]:
                problems.append(f"sağlama toplamı tutmuyor: {e['path']}")
            if e["kind"] == "sqlite":
                dbs += 1
                if (ok := _integrity(p)) != "ok":
                    problems.append(f"integrity_check {e['path']}: {ok}")
            if m := re.match(r"users/(\d+)/", e["path"]):
                users.add(int(m[1]))
        if problems:
            raise BackupError("; ".join(problems))
        return {"ok": True, "created": manifest["created"], "files": len(manifest["files"]), "databases": dbs,
                "users": sorted(users), "documents": manifest.get("documents", "tar"),
                "blobs": sum(1 for e in manifest["files"] if e.get("blob"))}


# ---------------------------------------------------------------- saklama süresi

def archive_time(name: str) -> datetime | None:
    m = NAME_RE.match(name)
    return datetime.strptime(m[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) if m else None


def expired(name: str, now: datetime, days: int | None = None) -> bool:
    """Takvim günüyle: 30 günlük saklamada bugünkü dahil en fazla 30 arşiv kalır (en eskisi 29 günlük).
    Böylece silinen bir hesabın verisi taşıyan son arşiv, silmeden itibaren en geç 30 gün içinde düşer."""
    t = archive_time(name)
    return bool(t) and (now.date() - t.date()).days >= (days or keep_days())


def prune_local(now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    d, removed = backup_dir(), []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if expired(name, now):
            os.remove(p)
            removed.append(name)
        elif name.endswith(".part") or name.startswith(".staging-"):  # yarıda kalmış eski çalışma
            if time.time() - os.path.getmtime(p) > 86400:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    return removed


# ---------------------------------------------------------------- S3 uyumlu dış kopya (app/s3.py: SigV4, httpx ile)

def s3_config() -> dict | None:
    """BACKUP_S3_* (önek varsayılan "hesapkitap/"); yoksa ve dosyalar nesne deposundaysa aynı bucket, "backups/" öneki."""
    try:
        cfg = config_from_env("BACKUP_S3_", "hesapkitap/")
        if cfg is None:
            cfg = config_from_env("HESAPKITAP_FILES_S3_")
            if cfg:
                cfg["prefix"] = os.environ.get("HESAPKITAP_BACKUP_S3_PREFIX", "backups/")
    except ValueError as e:
        raise BackupError(str(e)) from None
    return cfg


def upload(path: str, s3: S3) -> str:
    k = s3.cfg["prefix"] + os.path.basename(path)
    s3.put_file(path, k)
    if not any(o["key"] == k and o["size"] == os.path.getsize(path) for o in s3.list(k)):
        raise BackupError(f"Yüklenen arşiv uzak listede görünmüyor: {k}")
    return k


def prune_remote(s3: S3, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    removed = []
    for o in s3.list(s3.cfg["prefix"]):
        name = o["key"][len(s3.cfg["prefix"]):]
        if "/" not in name and expired(name, now):
            s3.delete(o["key"])
            removed.append(name)
    return removed


def prune_blobs(now: datetime | None = None, s3: S3 | None = None) -> list[str]:
    """Hiçbir saklanan arşivde geçmeyen belge blob'larını sil: son görüldüğü gün + KEEP_DAYS (arşivlerle aynı kural:
    o blob'u gösteren son arşiv de aynı gün düşer). Dizinde olmayan, yarım kalmış blob'lar da (1 gün sonra)."""
    now = now or datetime.now(timezone.utc)
    d = os.path.join(backup_dir(), BLOB_DIR)
    if not os.path.isdir(d):
        return []
    idx, removed, days = load_index(d), [], keep_days()
    for sha, info in list(idx["blobs"].items()):
        seen = info.get("seen")
        if seen and (now.date() - datetime.fromisoformat(seen).date()).days >= days:
            try:
                os.remove(os.path.join(d, sha + BLOB_SUFFIX))
            except FileNotFoundError:
                pass
            if s3 and info.get("remote"):
                s3.delete(s3.cfg["prefix"] + f"{BLOB_DIR}/{sha}{BLOB_SUFFIX}")
            del idx["blobs"][sha]
            removed.append(sha)
    for name in os.listdir(d):
        p = os.path.join(d, name)
        sha = name.removesuffix(BLOB_SUFFIX)
        stray = name.endswith(".part") or (name.endswith(BLOB_SUFFIX) and sha not in idx["blobs"])
        if stray and time.time() - os.path.getmtime(p) > 86400:
            os.remove(p)
    idx["paths"] = {r: v for r, v in idx["paths"].items() if v[2] in idx["blobs"]}
    save_index(d, idx)
    return removed


def upload_blobs(s3: S3) -> int:
    """Henüz uzak kopyada olmayan blob'ları gönder (her blob bir kez)."""
    d = os.path.join(backup_dir(), BLOB_DIR)
    if not os.path.isdir(d):
        return 0
    idx, n = load_index(d), 0
    for sha, info in idx["blobs"].items():
        p = os.path.join(d, sha + BLOB_SUFFIX)
        if not info.get("remote") and os.path.exists(p):
            s3.put_file(p, s3.cfg["prefix"] + f"{BLOB_DIR}/{sha}{BLOB_SUFFIX}")
            info["remote"] = True
            n += 1
            save_index(d, idx)  # yarıda kesilirse gönderilenler yeniden gönderilmesin
    return n


def read_manifest(archive: str, passphrase: str | None = None) -> dict:
    f, tar = _members(archive, passphrase or key())
    try:
        with tar:
            for m in tar:
                if m.name == "manifest.json":
                    return json.load(tar.extractfile(m))
    finally:
        f.close()
    raise BackupError("Arşivde manifest.json yok")


def fetch(name: str, dest_dir: str | None = None, s3_client=None) -> dict:
    """Uzak kopyadan arşivi ve (biçim 2) gerektirdiği blob'ları indir."""
    cfg = s3_config()
    if not cfg:
        raise BackupError("S3 ayarlı değil")
    s3 = S3(cfg, s3_client)
    dest_dir = dest_dir or backup_dir()
    dest = os.path.join(dest_dir, os.path.basename(name))
    s3.get_file(cfg["prefix"] + os.path.basename(name), dest)
    got = 0
    for e in read_manifest(dest).get("files", []):
        sha = e.get("blob")
        if sha and SHA_RE.match(sha) and not _find_blob(dest, sha):
            os.makedirs(os.path.join(dest_dir, BLOB_DIR), mode=0o700, exist_ok=True)
            s3.get_file(cfg["prefix"] + f"{BLOB_DIR}/{sha}{BLOB_SUFFIX}", os.path.join(dest_dir, BLOB_DIR, sha + BLOB_SUFFIX))
            got += 1
    return {"saved": dest, "blobs": got}


# ---------------------------------------------------------------- tek komut

def run(now: datetime | None = None, s3_client=None) -> dict:
    """Günlük iş: arşiv → (ayarlıysa) dış kopya → eskileri ve süresi dolan silinmiş hesapları temizle.
    Dış kopya ya da temizlik başarısız olursa hata yükselir (cron çıkış kodu ≠ 0 → izlenebilir)."""
    from app import system

    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    path = create(now)
    report = {"archive": path, "size": os.path.getsize(path)}
    report["pruned_local"] = prune_local(now)
    cfg = s3_config()
    s3 = S3(cfg, s3_client) if cfg else None
    if s3:
        report["uploaded_blobs"] = upload_blobs(s3)  # önce belgeler: arşiv uzakta varsa gösterdiği blob'lar da var
        report["remote"] = upload(path, s3)
        report["pruned_remote"] = prune_remote(s3, now)
    report["pruned_blobs"] = len(prune_blobs(now, s3))
    report["purged_deleted_accounts"] = system.purge_archived(keep_days(), now=now.timestamp())
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


def _list() -> dict:
    out = {"local": sorted(n for n in os.listdir(backup_dir()) if NAME_RE.match(n)),
           "blobs": len(load_index(os.path.join(backup_dir(), BLOB_DIR))["blobs"])}
    if cfg := s3_config():
        s3 = S3(cfg)
        out["remote"] = sorted(o["key"] for o in s3.list(cfg["prefix"]) if "/" not in o["key"][len(cfg["prefix"]):])
    return out


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    try:
        if cmd == "run":
            result = run()
        elif cmd == "list":
            result = _list()
        elif cmd == "verify" and len(argv) == 2:
            result = verify(argv[1], workdir=os.environ.get("HESAPKITAP_BACKUP_DIR") and backup_dir())
        elif cmd == "extract" and len(argv) in (3, 5) and (len(argv) == 3 or argv[3] == "--user"):
            result = extract(argv[1], argv[2], int(argv[4]) if len(argv) == 5 else None)
        elif cmd == "fetch" and len(argv) in (2, 3):
            result = fetch(argv[1], argv[2] if len(argv) == 3 else None)
        else:
            print(__doc__)
            return 2
    except (BackupError, S3Error) as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
