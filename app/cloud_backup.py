"""Sunucu geneli yedek (çok kullanıcılı kurulum): bütün veri klasörü tek, şifreli arşiv.

app/backup.py tek bir veritabanının (kullanıcının kendi alanının) yedeğidir; bu modül sunucuyu işletenin yedeğidir:
system.db + her kullanıcının ekstre.db'si (SQLite online backup API ile, uygulama çalışırken tutarlı kopya) +
uploads/ bills/ dosyaları → tar → AES-256-GCM ile parça parça şifreli tek dosya (bellek kullanımı sabit, ~1 MB).

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

Kullanım (container içinde ya da aynı ortamda):
  python -m app.cloud_backup run                     yedek al + dışarı gönder + eskileri temizle
  python -m app.cloud_backup list                    yerel ve uzak arşivler
  python -m app.cloud_backup verify <arşiv>          şifreyi çöz, sağlama toplamları + PRAGMA integrity_check
  python -m app.cloud_backup extract <arşiv> <klasör> [--user ID]   aç (tamamı ya da tek kullanıcı)
  python -m app.cloud_backup fetch <ad> [klasör]     uzak kopyadan indir (sunucu kaybında)
"""
import hashlib
import hmac
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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

FORMAT = 1
MAGIC = b"HKBACK1\x00"
CHUNK = 1 << 20
SUFFIX = ".tar.hkenc"
NAME_RE = re.compile(r"^hesapkitap-(\d{8}T\d{6}Z)" + re.escape(SUFFIX) + "$")
SKIP_DIRS = {"backups", "_silinen"}
SKIP_SUFFIXES = ("-wal", "-shm", "-journal")
SQLITE_HEADER = b"SQLite format 3\x00"


class BackupError(RuntimeError):
    pass


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

def _derive(passphrase: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480_000).derive(passphrase.encode())


class EncryptWriter(io.RawIOBase):
    def __init__(self, out, passphrase: str, chunk: int = CHUNK):
        salt, self._prefix = os.urandom(16), os.urandom(7)
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
    name = f"hesapkitap-{now:%Y%m%dT%H%M%SZ}{SUFFIX}"
    final, part = os.path.join(out_dir, name), os.path.join(out_dir, name + ".part")
    staging = os.path.join(out_dir, f".staging-{now:%Y%m%dT%H%M%S}-{os.getpid()}")
    os.makedirs(staging)
    manifest = {"format": FORMAT, "created": now.isoformat(timespec="seconds"), "files": []}
    try:
        with open(part, "wb") as raw:
            enc = EncryptWriter(raw, passphrase)
            with tarfile.open(fileobj=enc, mode="w|") as tar:
                for rel in _walk(root):
                    src = os.path.join(root, rel)
                    entry = {"path": rel}
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
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if os.path.exists(part):
            os.remove(part)
    return final


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
    return {"dest": dest, "files": len([n for n in names if n != "manifest.json"])}


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
                "users": sorted(users)}


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


# ---------------------------------------------------------------- S3 uyumlu dış kopya (SigV4, httpx ile; ek bağımlılık yok)

def s3_config() -> dict | None:
    keys = ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_KEY", "BACKUP_S3_SECRET")
    vals = {k: os.environ.get(k) for k in keys}
    if not any(vals.values()):
        return None
    if not all(vals.values()):
        raise BackupError("S3 ayarı eksik: " + ", ".join(k for k, v in vals.items() if not v))
    prefix = os.environ.get("BACKUP_S3_PREFIX", "hesapkitap/")
    return {"endpoint": vals["BACKUP_S3_ENDPOINT"].rstrip("/"), "bucket": vals["BACKUP_S3_BUCKET"],
            "key": vals["BACKUP_S3_KEY"], "secret": vals["BACKUP_S3_SECRET"],
            "region": os.environ.get("BACKUP_S3_REGION", "auto"), "prefix": prefix}


EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def _q(s: str, safe: str = "-_.~") -> str:
    return quote(s, safe=safe)


def sigv4_headers(method: str, host: str, path: str, query: dict, headers: dict, payload_sha: str,
                  access_key: str, secret: str, region: str, now: datetime, service: str = "s3") -> dict:
    """AWS Signature V4 (S3). Saf fonksiyon: AWS'nin örnek vektörüyle test edilir."""
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    h = {k.lower(): str(v).strip() for k, v in headers.items()}
    h |= {"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha}
    signed = ";".join(sorted(h))
    canonical = "\n".join([
        method, _q(path, safe="/-_.~"),
        "&".join(f"{_q(k)}={_q(str(v))}" for k, v in sorted(query.items())),
        "".join(f"{k}:{h[k]}\n" for k in sorted(h)), signed, payload_sha])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (day, region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return h


class S3:
    """Yol biçimli (path-style) adres: <endpoint>/<bucket>/<anahtar>. R2, MinIO, Backblaze B2, AWS S3 ile çalışır."""

    def __init__(self, cfg: dict, client=None):
        import httpx

        self.cfg = cfg
        self.host = urlsplit(cfg["endpoint"]).netloc
        self.http = client or httpx.Client(timeout=httpx.Timeout(60.0, write=600.0))

    def _req(self, method: str, key: str = "", query: dict | None = None, headers: dict | None = None,
             payload_sha: str = EMPTY_SHA, content=None, stream: bool = False):
        path = f"/{self.cfg['bucket']}" + (f"/{key}" if key else "")
        query = query or {}
        h = sigv4_headers(method, self.host, path, query, headers or {}, payload_sha, self.cfg["key"],
                          self.cfg["secret"], self.cfg["region"], datetime.now(timezone.utc))
        url = self.cfg["endpoint"] + _q(path, safe="/-_.~")
        if query:
            url += "?" + "&".join(f"{_q(k)}={_q(str(v))}" for k, v in sorted(query.items()))
        h.pop("host")
        req = self.http.build_request(method, url, headers=h, content=content)
        r = self.http.send(req, stream=stream)
        if r.status_code >= 300:
            body = r.read()[:300] if stream else r.content[:300]
            raise BackupError(f"S3 {method} {key or '/'}: HTTP {r.status_code} {body!r}")
        return r

    def put_file(self, path: str, key: str) -> None:
        size, sha = os.path.getsize(path), _sha256(path)

        def body():
            with open(path, "rb") as f:
                yield from iter(lambda: f.read(CHUNK), b"")

        self._req("PUT", key, headers={"content-length": size, "content-type": "application/octet-stream"},
                  payload_sha=sha, content=body())

    def list(self, prefix: str) -> list[dict]:
        out, token = [], None
        while True:
            q = {"list-type": "2", "prefix": prefix} | ({"continuation-token": token} if token else {})
            root = ET.fromstring(self._req("GET", query=q).content)
            ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
            for c in root.findall(f"{ns}Contents"):
                out.append({"key": c.findtext(f"{ns}Key"), "size": int(c.findtext(f"{ns}Size") or 0)})
            if root.findtext(f"{ns}IsTruncated") != "true":
                return out
            token = root.findtext(f"{ns}NextContinuationToken")

    def delete(self, key: str) -> None:
        self._req("DELETE", key)

    def get_file(self, key: str, dest: str) -> None:
        r = self._req("GET", key, stream=True)
        try:
            with open(dest + ".part", "wb") as f:
                for block in r.iter_bytes(CHUNK):
                    f.write(block)
        finally:
            r.close()
        os.replace(dest + ".part", dest)


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
    if cfg:
        s3 = S3(cfg, s3_client)
        report["remote"] = upload(path, s3)
        report["pruned_remote"] = prune_remote(s3, now)
    report["purged_deleted_accounts"] = system.purge_archived(keep_days(), now=now.timestamp())
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


def _list() -> dict:
    out = {"local": sorted(n for n in os.listdir(backup_dir()) if NAME_RE.match(n))}
    if cfg := s3_config():
        s3 = S3(cfg)
        out["remote"] = sorted(o["key"] for o in s3.list(cfg["prefix"]))
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
            cfg = s3_config()
            if not cfg:
                raise BackupError("S3 ayarlı değil")
            dest = os.path.join(argv[2] if len(argv) == 3 else backup_dir(), os.path.basename(argv[1]))
            S3(cfg).get_file(cfg["prefix"] + os.path.basename(argv[1]), dest)
            result = {"saved": dest}
        else:
            print(__doc__)
            return 2
    except BackupError as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
