"""Sunucu tarafı oturum listesi (denetim M9): çıkış oturumu gerçekten kapatır, "tümünden çık", kayan süre.

Oturum anahtarı (auth.make_token) imzalı ve içinde rastgele bir oturum kimliği (nonce) taşır; burada yalnız özeti durur.
token_user her istekte satıra bakar: iptal edilmiş, süresi dolmuş ya da başka kullanıcıya ait oturum geçersizdir.
Kayan süre: son kullanımdan bu yana mobilde MOBILE_IDLE_DAYS (14), web'de WEB_IDLE_DAYS (30) gün geçtiyse oturum düşer;
her kullanım süreyi uzatır (yazma en fazla saatte bir). Mobil anahtarın mutlak ömrü MOBILE_MAX_DAYS (180).

Bu tablodan önce verilmiş anahtar (satırı yok) ilk kullanımda listeye eklenir; çıkış onu da kapatır (satır iptal
olarak kalır, yeniden canlanmaz). "Tümünden çık" ayrıca oturum sürümünü artırır: hiç kullanılmamış eski anahtarlar da düşer.
"""
import hashlib
import os
import re
import secrets
import time

from app import system

WEB_IDLE_DAYS = 30
MOBILE_IDLE_DAYS = int(os.environ.get("HESAPKITAP_MOBILE_IDLE_DAYS", "14"))
MOBILE_MAX_DAYS = 180
TOUCH_EVERY = 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sid_hash TEXT PRIMARY KEY,
    public_id TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,            -- web | mobile | eski
    device TEXT,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
"""
_ready: set[str] = set()


def _c():
    c = system.connect()
    return c


def _ensure(c) -> None:
    key = system.SYSTEM_DB
    if key not in _ready:
        c.executescript(SCHEMA)
        _ready.add(key)


def _h(nonce: str) -> str:
    return hashlib.sha256(f"sid:{nonce}".encode()).hexdigest()


def idle_days(kind: str) -> int:
    return MOBILE_IDLE_DAYS if kind == "mobile" else WEB_IDLE_DAYS


def device_name(user_agent: str | None, mobile: bool) -> str:
    ua = user_agent or ""
    os_ = next((n for p, n in ((r"iPhone|iPad|iOS", "iOS"), (r"Android", "Android"), (r"Mac OS X|Macintosh", "macOS"),
                               (r"Windows", "Windows"), (r"Linux", "Linux")) if re.search(p, ua)), "")
    if mobile:
        return f"HesapKitap uygulaması{' · ' + os_ if os_ else ''}"
    br = next((n for p, n in ((r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/", "Firefox"),
                              (r"Chrome/", "Chrome"), (r"Safari/", "Safari")) if re.search(p, ua)), "Tarayıcı")
    return f"{br}{' · ' + os_ if os_ else ''}"


def create(uid: int, nonce: str, kind: str, device: str | None) -> None:
    now = time.time()
    with _c() as c:
        _ensure(c)
        c.execute("INSERT OR IGNORE INTO sessions (sid_hash, public_id, user_id, kind, device, created_at, last_seen) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (_h(nonce), secrets.token_urlsafe(9), uid, kind, (device or "")[:80], now, now))


def check(uid: int, nonce: str) -> bool:
    """Oturum geçerli mi (ve kullanımı yaz). Satırı yoksa: bu tablodan önceki anahtar, listeye eklenir."""
    now = time.time()
    with _c() as c:
        _ensure(c)
        r = c.execute("SELECT user_id, kind, last_seen, revoked FROM sessions WHERE sid_hash = ?", (_h(nonce),)).fetchone()
        if r is None:
            c.execute("INSERT OR IGNORE INTO sessions (sid_hash, public_id, user_id, kind, device, created_at, last_seen) "
                      "VALUES (?, ?, ?, 'eski', 'Önceki oturum', ?, ?)", (_h(nonce), secrets.token_urlsafe(9), uid, now, now))
            return True
        user_id, kind, last_seen, revoked = r
        if revoked or user_id != uid:
            return False
        if now - last_seen > idle_days(kind) * 86400:
            c.execute("UPDATE sessions SET revoked = 1 WHERE sid_hash = ?", (_h(nonce),))
            return False
        if now - last_seen > TOUCH_EVERY:
            c.execute("UPDATE sessions SET last_seen = ? WHERE sid_hash = ?", (now, _h(nonce)))
    return True


def revoke(uid: int | None, nonce: str) -> None:
    """Bu oturumu kapat (satırı yoksa iptal olarak eklenir: eski anahtar yeniden canlanmasın)."""
    now = time.time()
    with _c() as c:
        _ensure(c)
        c.execute("INSERT OR IGNORE INTO sessions (sid_hash, public_id, user_id, kind, device, created_at, last_seen) "
                  "VALUES (?, ?, ?, 'eski', 'Önceki oturum', ?, ?)", (_h(nonce), secrets.token_urlsafe(9), uid or 0, now, now))
        c.execute("UPDATE sessions SET revoked = 1 WHERE sid_hash = ?", (_h(nonce),))


def revoke_public(uid: int, public_id: str) -> bool:
    with _c() as c:
        _ensure(c)
        return c.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ? AND public_id = ? AND revoked = 0",
                         (uid, public_id)).rowcount > 0


def revoke_all(uid: int) -> int:
    with _c() as c:
        _ensure(c)
        n = c.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0", (uid,)).rowcount
    system.bump_session(uid)  # satırı olmayan (hiç kullanılmamış eski) anahtarlar da düşsün
    return n


def listing(uid: int, current_nonce: str | None) -> list[dict]:
    now = time.time()
    cur = _h(current_nonce) if current_nonce else None
    with _c() as c:
        _ensure(c)
        rows = c.execute("SELECT sid_hash, public_id, kind, device, created_at, last_seen FROM sessions "
                         "WHERE user_id = ? AND revoked = 0 ORDER BY last_seen DESC", (uid,)).fetchall()
    out = []
    for h, pid, kind, device, created, seen in rows:
        if now - seen > idle_days(kind) * 86400:
            continue
        out.append({"id": pid, "kind": kind, "device": device or "", "current": h == cur,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(created)),
                    "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(seen))})
    return out


def cleanup(days: int = 200) -> int:
    """İptal edilmiş ya da çok eski satırlar (gece işi)."""
    cut = time.time() - days * 86400
    with _c() as c:
        _ensure(c)
        return c.execute("DELETE FROM sessions WHERE last_seen < ? OR (revoked = 1 AND last_seen < ?)",
                         (cut, time.time() - 30 * 86400)).rowcount
