"""Dosya depolama: yerel disk (varsayılan) ya da S3 uyumlu nesne deposu (Cloudflare R2).

Anahtar düzeni (iki arka uçta da aynı):
    main/<tür>/<ad>                    yönetici / tek kullanıcılı kurulum (ana veritabanı, db_file NULL)
    users/<klasör>/<tür>/<ad>          kullanıcı defteri (klasör = veritabanının data/users/ altındaki klasörü, ör. 7)
    deleted/<id>-<unix zamanı>/...     silinen hesabın nesneleri (yalnız S3; 30 gün sonra kalıcı silinir)
tür: uploads (ekstre PDF'i, <sha256>.pdf), bills (fatura, <sha256[:24]>.<uzantı>), receipts (fiş fotoğrafı,
<sha256>.jpg), statements-bank (hesap dökümü, <sha256>.<uzantı>).

Yerel arka uç bugünkü yollarla birebir aynıdır: main/uploads → data/uploads, main/bills → data/bills,
users/7/uploads → data/users/7/uploads ... Kendi sunucusunu kuranda hiçbir dosya yer değiştirmez.
Ortak defter üyesi sahibin defterini açar: önek, açılan veritabanının klasöründen çözülür (db.use_space).

Güvenlik: nesne anahtarı istemciden alınmaz; defter öneki oturumdan, ad kaydın kendi alanından (hash) türetilir ve
sıkı biçim denetiminden geçer. İstemciye adres, imzalı bağlantı ya da anahtar dönmez: dosya her zaman oturum korumalı
/api/... ucundan akar. Hata mesajlarında uç adresi ya da kimlik bilgisi yoktur (StorageError genel mesaj taşır).

Ortam (dördü birlikte; yoksa yerel disk):
    HESAPKITAP_FILES_S3_ENDPOINT / _BUCKET / _KEY / _SECRET, _REGION (varsayılan auto), _PREFIX (varsayılan boş)

Geçiş (tek seferlik):
    python -m app.storage migrate [--dry-run] [--remove-local]
        yerel dosyaları S3'e kopyalar, her birini geri okuyup boyut + SHA-256 ile doğrular. Yerel kopya YALNIZ
        --remove-local verilirse ve doğrulama geçtiyse silinir.
    python -m app.storage check       ayarı ve bağlantıyı dener (yaz/oku/sil), sonucu yazar (anahtar yazmaz)
"""
import hashlib
import json
import logging
import os
import re
import shutil
import sys

log = logging.getLogger("ekstre.storage")

ENV = "HESAPKITAP_FILES_S3_"
KINDS = ("uploads", "bills", "receipts", "statements-bank")
# türün kabul ettiği adlar: yalnız onaltılık hash + izinli uzantı ("..", "/", boşluk vb. hiçbir zaman geçmez)
NAME_RES = {
    "uploads": re.compile(r"^[0-9a-f]{64}\.pdf$"),
    "bills": re.compile(r"^[0-9a-f]{24,64}\.(?:pdf|jpg|png|webp|heic)$"),
    "receipts": re.compile(r"^[0-9a-f]{64}\.jpg$"),
    "statements-bank": re.compile(r"^[0-9a-f]{64}\.(?:pdf|xlsx|xls|csv)$"),
}
CONTENT_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
_PREFIX_RE = re.compile(r"^(?:main/|users/[0-9A-Za-z_-]{1,64}(?:/[0-9A-Za-z_-]{1,64})*/|deleted/[0-9]+-[0-9]+(?:-\d+)?/)$")


class StorageError(RuntimeError):
    """Kullanıcıya gösterilebilir genel hata; ayrıntı (anahtarsız) yalnız sunucu günlüğünde."""

    def __init__(self, message: str = "Dosya şu an alınamadı"):
        super().__init__(message)


class NotFound(FileNotFoundError):
    pass


def valid_name(kind: str, name: str | None) -> bool:
    return kind in NAME_RES and isinstance(name, str) and bool(NAME_RES[kind].fullmatch(name))


def content_type(name: str) -> str:
    return CONTENT_TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream")


def _check_key(key: str) -> str:
    parts = key.split("/")
    if not key or key.startswith("/") or "\\" in key or any(p in ("", ".", "..") for p in parts[:-1]) \
            or parts[-1] in (".", "..") or any(ch.isspace() or ord(ch) < 32 for ch in key):
        raise ValueError("geçersiz nesne anahtarı")
    return key


# ---------------------------------------------------------------- yerel disk

class LocalBackend:
    """root verilmezse anahtar bugünkü klasörlere çözülür (main/ → data/, users/ → data/users/). root verilirse
    (demo kopyası gibi data/users dışındaki defterler) anahtar doğrudan o klasörün altıdır."""

    name = "local"

    def __init__(self, root: str | None = None):
        self.root = root

    def path(self, key: str) -> str:
        _check_key(key)
        if self.root:
            return os.path.join(self.root, *key.split("/"))
        from app import db, system

        head, _, rest = key.partition("/")
        if head == "main":
            kind, _, name = rest.partition("/")
            if kind == "uploads" and name:
                return os.path.join(db.UPLOAD_DIR, *name.split("/"))
            if kind == "bills" and name:
                return os.path.join(db.BILL_DIR, *name.split("/"))
            return os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), *rest.split("/"))
        if head == "users" and rest:
            return os.path.join(system.data_root(), *rest.split("/"))
        raise ValueError("yerel depoda bu önek yok")

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self.path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.tmp-{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)  # yarım dosya kalmaz

    def get(self, key: str) -> bytes:
        try:
            with open(self.path(key), "rb") as f:
                return f.read()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as e:
            raise NotFound(key) from e

    def exists(self, key: str) -> bool:
        return os.path.isfile(self.path(key))

    def size(self, key: str) -> int | None:
        p = self.path(key)
        return os.path.getsize(p) if os.path.isfile(p) else None

    def delete(self, key: str) -> None:
        try:
            os.remove(self.path(key))
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            pass

    def list(self, prefix: str) -> list[str]:
        base = self.path(prefix.rstrip("/") + "/x")[:-1]
        out = []
        if os.path.isdir(base):
            for d, _, files in os.walk(base):
                for n in files:
                    rel = os.path.relpath(os.path.join(d, n), base).replace(os.sep, "/")
                    out.append(prefix + rel)
        return sorted(out)

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list(prefix)
        for k in keys:
            self.delete(k)
        return len(keys)

    def copy(self, src: str, dst: str) -> None:
        self.put(dst, self.get(src))


# ---------------------------------------------------------------- S3 / R2

HTTP_CLIENT = None  # testler sahte bir httpx.Client (MockTransport) koyar


class S3Backend:
    name = "s3"

    def __init__(self, cfg: dict, client=None):
        from app.s3 import S3

        self.cfg = cfg
        self.prefix = cfg.get("prefix") or ""
        self.s3 = S3(cfg, client)

    def _call(self, what: str, fn, *a):
        from app.s3 import S3Error

        try:
            return fn(*a)
        except S3Error as e:  # mesajda uç adresi / anahtar yok (app/s3.py); nesne anahtarı yalnız günlükte
            log.error("depolama %s başarısız: %s", what, e)
            raise StorageError() from None

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._call("put", self.s3.put_bytes, self.prefix + _check_key(key), data, content_type)

    def get(self, key: str) -> bytes:
        data = self._call("get", self.s3.get_bytes, self.prefix + _check_key(key))
        if data is None:
            raise NotFound(key)
        return data

    def exists(self, key: str) -> bool:
        return self._call("head", self.s3.head, self.prefix + _check_key(key)) is not None

    def size(self, key: str) -> int | None:
        h = self._call("head", self.s3.head, self.prefix + _check_key(key))
        return h["size"] if h else None

    def delete(self, key: str) -> None:
        self._call("delete", self.s3.delete, self.prefix + _check_key(key))

    def list(self, prefix: str) -> list[str]:
        n = len(self.prefix)
        return sorted(o["key"][n:] for o in self._call("list", self.s3.list, self.prefix + prefix))

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list(_check_key(prefix))
        for k in keys:
            self.delete(k)
        return len(keys)

    def copy(self, src: str, dst: str) -> None:
        self._call("copy", self.s3.copy, self.prefix + _check_key(src), self.prefix + _check_key(dst))


# ---------------------------------------------------------------- seçim

_cache: dict = {}


def s3_config() -> dict | None:
    from app.s3 import config_from_env

    try:
        return config_from_env(ENV)
    except ValueError as e:
        raise StorageError(str(e)) from None  # yalnız eksik değişkenlerin ADLARI


def s3_enabled() -> bool:
    return s3_config() is not None


def backend():
    """Ayarlı arka uç. Ortam değişirse (testler) yeniden kurulur."""
    cfg = s3_config()
    sig = (json.dumps(cfg, sort_keys=True) if cfg else None, id(HTTP_CLIENT))
    if _cache.get("sig") != sig:
        _cache["sig"], _cache["b"] = sig, (S3Backend(cfg, HTTP_CLIENT) if cfg else LocalBackend())
    return _cache["b"]


def put(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    backend().put(key, data, content_type)


def get(key: str) -> bytes:
    return backend().get(key)


def delete(key: str) -> None:
    backend().delete(key)


def delete_prefix(prefix: str) -> int:
    if not _PREFIX_RE.match(prefix):  # yanlışlıkla "users/" ya da "" ile her şeyi silmek olmasın
        raise ValueError("geçersiz önek")
    return backend().delete_prefix(prefix)


def exists(key: str) -> bool:
    return backend().exists(key)


def move_prefix(src: str, dst: str) -> int:
    """Önekteki nesneleri başka öneke taşı (kopyala + sil). Yarıda kalırsa kalanlar kaynakta durur."""
    if not (_PREFIX_RE.match(src) and _PREFIX_RE.match(dst)):
        raise ValueError("geçersiz önek")
    b = backend()
    keys = b.list(src)
    for k in keys:
        b.copy(k, dst + k[len(src):])
        b.delete(k)
    return len(keys)


# ---------------------------------------------------------------- defter (oturumdaki kullanıcının alanı)

def book() -> tuple:
    """(arka uç, önek) — açık defterin dosyaları. data/users dışındaki defter (demo kopyası) her zaman yerel."""
    from app import db

    prefix, root = db.files_scope()
    if prefix is None:
        return LocalBackend(root), ""
    return backend(), prefix


def book_key(kind: str, name: str) -> str:
    if not valid_name(kind, name):
        raise ValueError("geçersiz dosya adı")
    return book()[1] + f"{kind}/{name}"


def local_dir(kind: str) -> str | None:
    """Açık defterin bu türdeki YEREL klasörü (S3'te de: eski dosyalar ve geçiş için)."""
    from app import db

    prefix, root = db.files_scope()
    if prefix is None:
        return os.path.join(root, kind)
    return os.path.dirname(LocalBackend().path(f"{prefix}{kind}/x"))


def book_put(kind: str, name: str, data: bytes, ctype: str | None = None) -> None:
    b, p = book()
    if not valid_name(kind, name):
        raise ValueError("geçersiz dosya adı")
    b.put(f"{p}{kind}/{name}", data, ctype or content_type(name))


def book_get(kind: str, name: str) -> bytes | None:
    """Yoksa None. Arka uç hatası StorageError."""
    b, p = book()
    if not valid_name(kind, name):
        return None
    try:
        return b.get(f"{p}{kind}/{name}")
    except NotFound:
        return None


def book_delete(kind: str, name: str) -> None:
    b, p = book()
    if valid_name(kind, name):
        b.delete(f"{p}{kind}/{name}")


def book_exists(kind: str, name: str) -> bool:
    b, p = book()
    return valid_name(kind, name) and b.exists(f"{p}{kind}/{name}")


# ---------------------------------------------------------------- geçiş: yerel → S3

def _local_files() -> list[tuple[str, str]]:
    """(yerel yol, anahtar): ana defter + data/users altındaki her defter (_silinen hariç)."""
    from app import db, system

    out = []
    main_base = os.path.dirname(os.path.abspath(db.DB_PATH))
    dirs = [("main/uploads/", db.UPLOAD_DIR), ("main/bills/", db.BILL_DIR),
            ("main/receipts/", os.path.join(main_base, "receipts")),
            ("main/statements-bank/", os.path.join(main_base, "statements-bank"))]
    root = system.data_root()
    if os.path.isdir(root):
        for d, subdirs, _ in os.walk(root):
            subdirs[:] = sorted(s for s in subdirs if s != system.DELETED_DIR and s != "backups" and not s.startswith("."))
            kind = os.path.basename(d)
            rel = os.path.relpath(os.path.dirname(d), root).replace(os.sep, "/")
            if kind in KINDS and rel != ".":
                dirs.append((f"users/{rel}/{kind}/", d))
                subdirs[:] = []
    for prefix, d in dirs:
        kind = prefix.rstrip("/").rsplit("/", 1)[-1]
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                p = os.path.join(d, n)
                if os.path.isfile(p) and not os.path.islink(p) and valid_name(kind, n):
                    out.append((p, prefix + n))
    return out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def migrate(remove_local: bool = False, dry_run: bool = False) -> dict:
    """Yerel dosyaları S3'e kopyala ve doğrula. Tekrar çalıştırılabilir: S3'te aynı boyda ve aynı özette olan atlanır."""
    from app import db, system

    if not s3_enabled():
        raise StorageError(f"{ENV}* ayarlı değil: geçiş için S3 gerekli")
    b = backend()
    # önce her defteri bir kez aç: dosya dizini (stored_files) yerel dosyalardan doldurulsun (alan sınırı hesabı)
    books = [None] + [system.user_db_path(u) for u in system.users() if u.get("db_file")]
    root = system.data_root()
    if os.path.isdir(root):
        books += [os.path.join(root, n, "ekstre.db") for n in sorted(os.listdir(root))
                  if n != system.DELETED_DIR and os.path.exists(os.path.join(root, n, "ekstre.db"))]
    if not dry_run:
        for path in dict.fromkeys(books):
            if path is None or os.path.exists(path):
                with db.use_space(path):
                    db.init_db()  # şema (stored_files) yoksa kurulur; uygulama açılışındakiyle aynı, tekrar zararsız
                    db.index_local_files()
    report = {"files": 0, "bytes": 0, "copied": 0, "already": 0, "verified": 0, "failed": [], "removed": 0,
              "dry_run": dry_run}
    for path, key in _local_files():
        with open(path, "rb") as f:
            data = f.read()
        report["files"] += 1
        report["bytes"] += len(data)
        if dry_run:
            continue
        want = _sha(data)
        try:
            if b.size(key) == len(data) and _sha(b.get(key)) == want:
                report["already"] += 1
            else:
                b.put(key, data, content_type(key))
                report["copied"] += 1
            got = b.get(key)  # geri oku: boyut + SHA-256
            if len(got) != len(data) or _sha(got) != want:
                raise StorageError("doğrulama tutmadı")
            report["verified"] += 1
        except (StorageError, NotFound) as e:
            report["failed"].append({"key": key, "error": str(e) or "okunamadı"})
            continue
        if remove_local:
            os.remove(path)
            report["removed"] += 1
    return report


def check() -> dict:
    """Ayarlı arka ucu dene: yaz → oku → sil. Uç adresi / anahtar yazmaz."""
    b = backend()
    if b.name == "local":
        return {"backend": "local", "ok": True}
    key = "main/.hesapkitap-check"
    blob = os.urandom(32)
    b.put(key, blob)
    ok = b.get(key) == blob
    b.delete(key)
    return {"backend": "s3", "bucket_prefix": b.prefix or "", "ok": ok}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    try:
        if cmd == "migrate" and set(argv[1:]) <= {"--remove-local", "--dry-run"}:
            r = migrate(remove_local="--remove-local" in argv, dry_run="--dry-run" in argv)
            print(json.dumps(r, ensure_ascii=False))
            return 1 if r["failed"] else 0
        if cmd == "check":
            print(json.dumps(check(), ensure_ascii=False))
            return 0
    except StorageError as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main(sys.argv[1:]))
