"""Sistem veritabanı: kullanıcılar, davetler, yapay zekâ kullanımı, sistem geneli ayarlar.

Her kullanıcının finans verisi kendi SQLite dosyasındadır (data/users/<id>/ekstre.db); yönetici (ilk kullanıcı)
eski tek kullanıcılı kurulumun data/ekstre.db dosyasını kullanır. Bu modül hiçbir finans verisi tutmaz —
yönetici kullanıcıları açıp kapatabilir ama verilerini göremez.
"""
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date

SYSTEM_DB = os.environ.get("HESAPKITAP_SYSTEM_DB",
                           os.path.join(os.path.dirname(__file__), "..", "data", "system.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT,                 -- NULL: şifre ortam değişkeninde (APP_PASSWORD, sadece yönetici)
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    db_file TEXT,                       -- NULL: ana veritabanı (data/ekstre.db, yönetici)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_login TEXT,
    disabled INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    ai_quota INTEGER,                   -- NULL: varsayılan kota
    session_version INTEGER NOT NULL DEFAULT 1  -- artınca o kullanıcının açık oturumları düşer
);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT,
    used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS ai_usage (     -- kendi sunucusu: yapay zekâ çağrısı; hazır sürüm: okunan belge (app/doc_reads.py)
    user_id INTEGER NOT NULL,
    month TEXT NOT NULL,                -- YYYY-MM (takvim ayı)
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, month)
);
CREATE TABLE IF NOT EXISTS doc_reads (    -- hazır sürüm: sayılmış belgeler; aynı içerik aynı defterde bir kez sayılır
    book_id INTEGER NOT NULL,           -- defter sahibi (ortak defterde sahip)
    hash TEXT NOT NULL,                 -- sha256(dosya baytları ya da "mail:<Message-ID>"); içerik saklanmaz
    month TEXT NOT NULL,
    PRIMARY KEY (book_id, hash)
);
CREATE TABLE IF NOT EXISTS sys_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT,
    token_hash TEXT NOT NULL UNIQUE,      -- sadece özet; anahtarın kendisi bir kez gösterilir
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_used TEXT
);
CREATE TABLE IF NOT EXISTS tg_pairs (
    code TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS deleted_accounts (  -- silinen hesabın anonim kaydı (app/deletion.py); 1 yıl sonra düşer
    user_id INTEGER PRIMARY KEY,         -- id hiç yeniden verilmez; RevenueCat app_user_id ile aynı
    deleted_at TEXT NOT NULL,            -- silme isteği (ISO, UTC)
    delete_after TEXT,                   -- bekleme süresinin sonu; geri alınabilir pencere
    purged_at TEXT,                      -- veri kalıcı silindi (NULL: henüz bekliyor)
    reason TEXT,                         -- self | owner (sahip defteri sildi) | admin
    plan TEXT, plan_until TEXT, plan_source TEXT, plan_store TEXT, will_renew INTEGER,
    store_customer TEXT,                 -- RevenueCat app_user_id (= user_id)
    email_hash TEXT,                     -- HMAC-SHA256(sunucu anahtarı, küçük harf e-posta); düz e-posta yok
    notes TEXT                           -- JSON liste: silme sonrası mağaza olayları, iptal denemeleri
);
CREATE INDEX IF NOT EXISTS deleted_accounts_email ON deleted_accounts(email_hash);
"""

DEFAULT_AI_QUOTA = 100


def _path() -> str:
    return SYSTEM_DB


_ready: set[str] = set()


@contextmanager
def connect():
    p = _path()
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if p not in _ready:  # şema (ve göçler) her dosya için bir kez kurulur
        conn.executescript(SCHEMA)
        _migrate(conn)
        _ready.add(p)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(c) -> None:
    # ortak defter: üye kendi kullanıcısıyla girer ama sahibin defterini açar
    for table in ("users", "invites"):
        if "member_of" not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN member_of INTEGER")
    # paket (barındırılan sürüm): free / premium / aile, bitiş (ISO, UTC), kaynak (revenuecat / elle)
    have = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    for col in ("plan", "plan_until", "plan_source"):
        if col not in have:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
    # ortak defter v2: üyenin yetkisi (viewer | editor | full); davette seçilir
    for table in ("users", "invites"):
        if "book_role" not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN book_role TEXT")
    # yumuşak silme (app/deletion.py): istek zamanı, kalıcı silinme zamanı, ortak defter sahibiyle birlikte silinen üye
    # abonelik: mağaza yenilemesi açık mı (CANCELLATION → 0), hangi mağaza (APP_STORE / PLAY_STORE)
    have = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    for col, typ in (("deleted_at", "TEXT"), ("delete_after", "TEXT"), ("deleted_with", "INTEGER"),
                     ("plan_renews", "INTEGER"), ("plan_store", "TEXT")):
        if col not in have:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        _migrate(c)


def get(key: str) -> str | None:
    with connect() as c:
        r = c.execute("SELECT value FROM sys_settings WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None


def put(key: str, value: str | None) -> None:
    with connect() as c:
        if value is None:
            c.execute("DELETE FROM sys_settings WHERE key = ?", (key,))
        else:
            c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES (?, ?)", (key, value))


# ---------------------------------------------------------------- kullanıcılar

def users() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY id")]


def user(uid: int) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(r) if r else None


def user_by_name(username: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
        return dict(r) if r else None


def has_users() -> bool:
    with connect() as c:
        return bool(c.execute("SELECT 1 FROM users LIMIT 1").fetchone())


def data_root() -> str:
    """Kullanıcı klasörlerinin kökü: sistem veritabanının yanındaki users/."""
    return os.path.join(os.path.dirname(os.path.abspath(_path())), "users")


def create_user(username: str, password_hash: str | None, role: str = "user", main_db: bool = False,
                must_change: bool = False, member_of: int | None = None, book_role: str | None = None) -> dict:
    """main_db: yöneticinin eski tek kullanıcılı veritabanını kullanması (db_file NULL).
    member_of: ortak defter üyesi; kendi dosyası olmaz, o kullanıcının defterini açar."""
    name = username.strip()
    if "@" in name:  # mobil uygulama ve hazır sürüm: e-posta ile kayıt (büyük/küçük harf fark etmez: COLLATE NOCASE)
        import re

        if len(name) > 120 or not re.fullmatch(r"[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,24}", name):
            raise ValueError("Geçerli bir e-posta yaz")
        name = name.lower()
    elif not (3 <= len(name) <= 32) or not all(ch.isalnum() or ch in "._-" for ch in name):
        raise ValueError("Kullanıcı adı 3–32 karakter; harf, rakam, nokta, alt çizgi ya da tire")
    with connect() as c:
        if c.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
            raise ValueError("Bu kullanıcı adı alınmış")
        # id hiçbir zaman yeniden verilmez (silinen son kullanıcının id'si de): klasör users/<id>/, erişim anahtarları ve
        # ortak defter davetleri id'ye bağlı; yeniden kullanım eski hesabın artığını yeni hesaba bağlayabilirdi.
        last = c.execute("SELECT value FROM sys_settings WHERE key = 'last_user_id'").fetchone()
        top = c.execute("SELECT COALESCE(MAX(id), 0) FROM users").fetchone()[0]
        uid = max(top, int(last[0]) if last and str(last[0]).isdigit() else 0) + 1
        c.execute("INSERT INTO users (id, username, password_hash, role, must_change_password, member_of, book_role) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (uid, name, password_hash, role, int(must_change), member_of, book_role if member_of else None))
        c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES ('last_user_id', ?)", (str(uid),))
        if not main_db and not member_of:
            c.execute("UPDATE users SET db_file = ? WHERE id = ?", (os.path.join(str(uid), "ekstre.db"), uid))
    return user(uid)


def user_db_path(u: dict) -> str | None:
    """Kullanıcının finans veritabanı dosyası; None: ana veritabanı (yönetici). Ortak defter üyesi: sahibinin defteri."""
    if u.get("member_of"):
        owner = user(u["member_of"])
        if owner and not owner.get("member_of"):
            return user_db_path(owner)
        return os.path.join(data_root(), str(u["id"]), "ekstre.db")  # sahibi silinmiş: boş, kendine ait defter
    return os.path.join(data_root(), u["db_file"]) if u.get("db_file") else None


def own_db_path(u: dict) -> str:
    """Ortak defter üyesinin kendine ait (özel) defteri: "Benim" seçiliyken açılır."""
    return os.path.join(data_root(), str(u["id"]), "ekstre.db")


def members(owner_id: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users WHERE member_of = ? ORDER BY id", (owner_id,))]


def update_user(uid: int, **fields) -> None:
    allowed = {"username", "password_hash", "disabled", "last_login", "must_change_password", "ai_quota", "role",
               "plan", "plan_until", "plan_source", "book_role", "deleted_at", "delete_after", "deleted_with",
               "plan_renews", "plan_store"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    with connect() as c:
        c.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?", (*data.values(), uid))


def bump_session(uid: int) -> None:
    """O kullanıcının tüm açık oturumlarını geçersiz kıl (şifre değişince, kapatılınca)."""
    with connect() as c:
        c.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (uid,))


def delete_user(uid: int) -> None:
    with connect() as c:
        if c.execute("DELETE FROM users WHERE id = ? AND role <> 'admin'", (uid,)).rowcount:
            c.execute("DELETE FROM ai_usage WHERE user_id = ?", (uid,))
            c.execute("DELETE FROM doc_reads WHERE book_id = ?", (uid,))
            c.execute("DELETE FROM api_tokens WHERE user_id = ?", (uid,))  # silinen hesabın anahtarı çalışmasın
            c.execute("DELETE FROM invites WHERE member_of = ? AND used_at IS NULL", (uid,))  # bekleyen ortak defter daveti
            c.execute("DELETE FROM tg_pairs WHERE user_id = ?", (uid,))
            for sql in ("DELETE FROM password_resets WHERE user_id = ?",   # tablolar ilk kullanımda kurulur (reset, inbound)
                        "UPDATE inbound SET revoked = 1 WHERE user_id = ?"):
                try:
                    c.execute(sql, (uid,))
                except sqlite3.OperationalError:
                    pass


def user_dirs(u: dict) -> list[str]:
    """Kullanıcıya ait klasörler: users/<id>/ (kendi defteri; ortak defter üyesinin "Benim" defteri de burada) ve
    db_file başka bir klasörü gösteriyorsa o. Kökün kendisi ya da _silinen asla dönmez."""
    root = os.path.abspath(data_root())
    cands = [os.path.join(root, str(int(u["id"])))]
    if u.get("db_file"):
        cands.append(os.path.join(root, os.path.dirname(u["db_file"])))
    out = []
    for p in map(os.path.abspath, cands):
        if p.startswith(root + os.sep) and os.path.basename(p) != "_silinen" and p not in out and os.path.isdir(p):
            out.append(p)
    return out


def user_prefixes(u: dict) -> list[str]:
    """Kullanıcının nesne deposundaki önekleri (app/storage.py): users/<id>/ ve db_file başka klasördeyse o."""
    import re

    out = [f"users/{int(u['id'])}/"]
    if u.get("db_file"):
        rel = os.path.dirname(u["db_file"]).replace(os.sep, "/")
        if re.fullmatch(r"[0-9A-Za-z_-]{1,64}(?:/[0-9A-Za-z_-]{1,64})*", rel) and rel.split("/")[0] != DELETED_DIR:
            out.append(f"users/{rel}/")
    return list(dict.fromkeys(out))


def _s3_files() -> bool:
    from app import storage

    return storage.s3_enabled()


def purge_user_data(u: dict) -> None:
    """Kullanıcı kendi hesabını sildi: klasörü (veritabanı, PDF'ler, faturalar, kendi günlük yedekleri) hemen ve kalıcı
    olarak silinir (KVKK); dosyalar nesne deposundaysa users/<id>/ öneki de. Sunucu yedeklerindeki kopyalar en geç
    30 gün içinde düşer (app/cloud_backup.py)."""
    import shutil

    for p in user_dirs(u):
        shutil.rmtree(p)
    if _s3_files():
        from app import storage

        for prefix in user_prefixes(u):
            storage.delete_prefix(prefix)


DELETED_DIR = "_silinen"


def archive_user_data(u: dict) -> str | None:
    """Silinen kullanıcının klasörünü users/_silinen/ altına taşı (geri dönüş için). purge_archived süresi dolanı siler."""
    import re
    import shutil
    import time

    dst = None
    safe = re.sub(r"[^A-Za-z0-9._@-]", "_", u["username"])[:80]  # e-posta adında "/" olabilir: yol dışına çıkmasın
    ts = int(time.time())
    for i, src in enumerate(user_dirs(u)):
        dst = os.path.join(data_root(), DELETED_DIR, f"{u['id']}-{safe}-{ts}" + (f"-{i}" if i else ""))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
    if _s3_files():  # nesneler deleted/<id>-<zaman>/ altına (kullanıcı adı anahtara girmez); 30 gün sonra silinir
        from app import storage

        for i, prefix in enumerate(user_prefixes(u)):
            storage.move_prefix(prefix, f"deleted/{int(u['id'])}-{ts}" + (f"-{i}" if i else "") + "/")
    return dst


def deleted_keep_days() -> int:
    """users/_silinen/ saklama süresi (gün). 0: kendiliğinden silinmez."""
    v = os.environ.get("HESAPKITAP_DELETED_KEEP_DAYS", "30")
    return int(v) if v.isdigit() else 30


def purge_archived(days: int | None = None, now: float | None = None) -> list[str]:
    """users/_silinen/ altında `days` günden eski klasörleri kalıcı sil. Ad: <id>-<kullanıcı>-<unix zamanı>[-n];
    zaman okunamazsa klasörün değişme zamanı esas alınır."""
    import re
    import shutil
    import time
    from datetime import datetime

    days = deleted_keep_days() if days is None else days
    base = os.path.join(data_root(), DELETED_DIR)
    if not days:
        return []
    if not os.path.isdir(base):
        return _purge_archived_objects(days, time.time() if now is None else now)
    now = time.time() if now is None else now
    removed = []
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        m = re.search(r"-(\d{9,11})(?:-\d+)?$", name)
        ts = int(m[1]) if m else os.path.getmtime(p)
        # takvim günüyle (yedeklerle aynı kural): taşındığı gün + days. gün çalışan temizlikte düşer
        if (datetime.fromtimestamp(now).date() - datetime.fromtimestamp(ts).date()).days >= days:
            shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
            removed.append(name)
    return removed + _purge_archived_objects(days, now)


def _purge_archived_objects(days: int, now: float) -> list[str]:
    """Nesne deposunda deleted/<id>-<unix zamanı>[-n]/ önekleri: aynı takvim günü kuralı."""
    import re
    from datetime import datetime

    if not days or not _s3_files():
        return []
    from app import storage

    groups = {k.split("/")[1] for k in storage.backend().list("deleted/") if k.count("/") >= 2}
    removed = []
    for g in sorted(groups):
        m = re.fullmatch(r"(\d+)-(\d{9,11})(?:-\d+)?", g)
        if m and (datetime.fromtimestamp(now).date() - datetime.fromtimestamp(int(m[2])).date()).days >= days:
            storage.delete_prefix(f"deleted/{g}/")
            removed.append(f"deleted/{g}/")
    return removed


# ---------------------------------------------------------------- davetler

def create_invite(note: str | None = None, days: int = 14, member_of: int | None = None, book_role: str | None = None) -> dict:
    """member_of: ortak defter daveti (kayıt olan o kullanıcının defterine üye olur); book_role: üyenin yetkisi."""
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    with connect() as c:
        c.execute("INSERT INTO invites (code, note, expires_at, member_of, book_role) VALUES (?, ?, date('now', ?), ?, ?)",
                  (code, (note or "").strip()[:60] or None, f"+{days} days", member_of, book_role if member_of else None))
        return dict(c.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone())


def invites() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT i.*, u.username AS used_by_name FROM invites i LEFT JOIN users u ON u.id = i.used_by "
            "ORDER BY i.created_at DESC")]


def invite(code: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM invites WHERE code = ?", (code.strip().upper(),)).fetchone()
        return dict(r) if r else None


def valid_invite(code: str) -> bool:
    with connect() as c:
        return bool(c.execute("SELECT 1 FROM invites WHERE code = ? AND used_at IS NULL AND "
                              "(expires_at IS NULL OR expires_at >= date('now'))", (code.strip().upper(),)).fetchone())


def use_invite(code: str, uid: int) -> bool:
    with connect() as c:
        return c.execute("UPDATE invites SET used_by = ?, used_at = CURRENT_TIMESTAMP WHERE code = ? AND used_at IS NULL "
                         "AND (expires_at IS NULL OR expires_at >= date('now'))", (uid, code.strip().upper())).rowcount > 0


def delete_invite(code: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM invites WHERE code = ? AND used_at IS NULL", (code,)).rowcount > 0


# ---------------------------------------------------------------- okuma kotası
# Kendi sunucusu: ortak Gemini anahtarıyla yapılan yapay zekâ çağrısı sayısı (receipt._count_usage).
# Hazır sürüm: okunan belge sayısı (app/doc_reads.py) — "belge okuma hakkı".

def ai_used(uid: int, month: str | None = None) -> int:
    month = month or date.today().strftime("%Y-%m")
    with connect() as c:
        r = c.execute("SELECT count FROM ai_usage WHERE user_id = ? AND month = ?", (uid, month)).fetchone()
        return r[0] if r else 0


def ai_used_pool(u: dict, month: str | None = None) -> int:
    """Ortak defterde okuma hakkı ailenin ortak havuzu: sahip + bütün üyeler (hazır sürümde belge, kendi sunucusunda
    yapay zekâ çağrısı)."""
    owner_id = u.get("member_of") or u["id"]
    ids = [owner_id] + [m["id"] for m in members(owner_id)]
    return sum(ai_used(i, month) for i in ids)


def ai_add(uid: int) -> None:
    month = date.today().strftime("%Y-%m")
    with connect() as c:
        c.execute("INSERT INTO ai_usage (user_id, month, count) VALUES (?, ?, 1) "
                  "ON CONFLICT(user_id, month) DO UPDATE SET count = count + 1", (uid, month))


def ai_quota(u: dict) -> int | None:
    """None: sınırsız (yönetici). Hazır sürümde paketin aylık belge okuma hakkı (plans.DOC_READS; kişiye özel ai_quota
    önce gelir); kendi sunucusunda aylık yapay zekâ çağrısı kotası (kişiye özel ya da varsayılan)."""
    if u["role"] == "admin":
        return None
    from app import plans

    if plans.hosted():
        return plans.ai_limit(u)
    if u.get("ai_quota") is not None:
        return u["ai_quota"]
    v = get("ai_default_quota")
    return int(v) if v and v.isdigit() else DEFAULT_AI_QUOTA


# ---------------------------------------------------------------- kişisel erişim anahtarları (Paylaş kısayolu)

def _token_hash(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def create_token(uid: int, name: str | None = None) -> str:
    token = "hk_" + secrets.token_urlsafe(32)
    with connect() as c:
        c.execute("INSERT INTO api_tokens (user_id, name, token_hash) VALUES (?, ?, ?)",
                  (uid, (name or "").strip()[:40] or None, _token_hash(token)))
    return token


def tokens(uid: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT id, name, created_at, last_used FROM api_tokens WHERE user_id = ? "
                                           "ORDER BY id DESC", (uid,))]


def delete_token(uid: int, tid: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM api_tokens WHERE id = ? AND user_id = ?", (tid, uid)).rowcount > 0


def token_owner(token: str) -> dict | None:
    """Anahtarın sahibi (kapatılmış kullanıcıysa None)."""
    if not token or not token.startswith("hk_"):
        return None
    with connect() as c:
        r = c.execute("SELECT user_id FROM api_tokens WHERE token_hash = ?", (_token_hash(token),)).fetchone()
        if not r:
            return None
        c.execute("UPDATE api_tokens SET last_used = CURRENT_TIMESTAMP WHERE token_hash = ?", (_token_hash(token),))
    u = user(r["user_id"])
    return u if u and not u["disabled"] and not u.get("deleted_at") else None  # silinmek üzere: API erişimi yok
