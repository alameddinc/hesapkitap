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
CREATE TABLE IF NOT EXISTS ai_usage (
    user_id INTEGER NOT NULL,
    month TEXT NOT NULL,                -- YYYY-MM (takvim ayı)
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, month)
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
                must_change: bool = False, member_of: int | None = None) -> dict:
    """main_db: yöneticinin eski tek kullanıcılı veritabanını kullanması (db_file NULL).
    member_of: ortak defter üyesi; kendi dosyası olmaz, o kullanıcının defterini açar."""
    name = username.strip()
    if not (3 <= len(name) <= 32) or not all(ch.isalnum() or ch in "._-" for ch in name):
        raise ValueError("Kullanıcı adı 3–32 karakter; harf, rakam, nokta, alt çizgi ya da tire")
    with connect() as c:
        if c.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
            raise ValueError("Bu kullanıcı adı alınmış")
        uid = c.execute("INSERT INTO users (username, password_hash, role, must_change_password, member_of) VALUES (?, ?, ?, ?, ?)",
                        (name, password_hash, role, int(must_change), member_of)).lastrowid
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


def members(owner_id: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users WHERE member_of = ? ORDER BY id", (owner_id,))]


def update_user(uid: int, **fields) -> None:
    allowed = {"username", "password_hash", "disabled", "last_login", "must_change_password", "ai_quota", "role",
               "plan", "plan_until", "plan_source"}
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
            c.execute("DELETE FROM ai_usage WHERE user_id = ?", (uid,))  # id ileride yeniden verilebilir


def archive_user_data(u: dict) -> str | None:
    """Silinen kullanıcının klasörünü users/_silinen/ altına taşı (geri dönüş için; elle temizlenir)."""
    import shutil
    import time

    if not u.get("db_file"):
        return None
    src = os.path.join(data_root(), os.path.dirname(u["db_file"]))
    if not os.path.isdir(src):
        return None
    dst = os.path.join(data_root(), "_silinen", f"{u['id']}-{u['username']}-{int(time.time())}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return dst


# ---------------------------------------------------------------- davetler

def create_invite(note: str | None = None, days: int = 14, member_of: int | None = None) -> dict:
    """member_of: ortak defter daveti (kayıt olan o kullanıcının defterine üye olur)."""
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    with connect() as c:
        c.execute("INSERT INTO invites (code, note, expires_at, member_of) VALUES (?, ?, date('now', ?), ?)",
                  (code, (note or "").strip()[:60] or None, f"+{days} days", member_of))
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


# ---------------------------------------------------------------- yapay zekâ kotası

def ai_used(uid: int, month: str | None = None) -> int:
    month = month or date.today().strftime("%Y-%m")
    with connect() as c:
        r = c.execute("SELECT count FROM ai_usage WHERE user_id = ? AND month = ?", (uid, month)).fetchone()
        return r[0] if r else 0


def ai_add(uid: int) -> None:
    month = date.today().strftime("%Y-%m")
    with connect() as c:
        c.execute("INSERT INTO ai_usage (user_id, month, count) VALUES (?, ?, 1) "
                  "ON CONFLICT(user_id, month) DO UPDATE SET count = count + 1", (uid, month))


def ai_quota(u: dict) -> int | None:
    """None: sınırsız (yönetici). Barındırılan sürümde paketin kotası (ücretsiz 3, premium/aile sınırsız)."""
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
    return u if u and not u["disabled"] else None
