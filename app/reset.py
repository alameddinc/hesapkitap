"""Şifremi unuttum: e-postaya 6 haneli kod, 15 dakika geçerli, kod başı en çok 5 deneme.

Hesap başı günlük sınırlar (O3, IP'den bağımsız): günde en çok 5 kod postası (posta bombası), günde 15 yanlış kod
denemesinden sonra 24 saat kilit (kaba kuvvet: 15/10⁶ günlük tahmin olasılığı). Sınırda yanıt yine nötr.
Başarılı sıfırlamada hesaba bilgilendirme postası gider (notify_changed).

Kod sistem veritabanında yalnız özet olarak durur. Hesap var mı yok mu dışarıdan anlaşılmaz (her istek aynı yanıtı alır).
"""
import hashlib
import hmac
import secrets
import time

from app import mailer, system

CODE_TTL = 15 * 60
MAX_TRIES = 5
RESEND_GAP = 60  # aynı hesaba en sık dakikada bir kod
DAILY_CODES = 5
DAILY_WRONG = 15
LOCK_SECONDS = 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS password_resets (
    user_id INTEGER PRIMARY KEY,
    code_hash TEXT NOT NULL,
    expires INTEGER NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0,
    sent_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_limits (     -- hesap başı günlük sayaçlar: reset_code, reset_wrong, verify_code
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    day TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    locked_until INTEGER,
    PRIMARY KEY (user_id, kind)
);
"""


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def daily_count(c, uid: int, kind: str) -> int:
    r = c.execute("SELECT day, n FROM auth_limits WHERE user_id = ? AND kind = ?", (uid, kind)).fetchone()
    return r[1] if r and r[0] == _day() else 0


def bump(c, uid: int, kind: str, lock_at: int | None = None) -> int:
    """Günlük sayacı bir artır; lock_at'e ulaşınca LOCK_SECONDS kilit. Yeni sayı."""
    n = daily_count(c, uid, kind) + 1
    lock = int(time.time()) + LOCK_SECONDS if lock_at and n >= lock_at else None
    c.execute("INSERT INTO auth_limits (user_id, kind, day, n, locked_until) VALUES (?, ?, ?, ?, ?) "
              "ON CONFLICT(user_id, kind) DO UPDATE SET day = excluded.day, n = excluded.n, "
              "locked_until = COALESCE(excluded.locked_until, auth_limits.locked_until)", (uid, kind, _day(), n, lock))
    return n


def locked(c, uid: int) -> bool:
    r = c.execute("SELECT locked_until FROM auth_limits WHERE user_id = ? AND kind = 'reset_wrong'", (uid,)).fetchone()
    return bool(r and r[0] and r[0] > time.time())


def _digest(uid: int, code: str) -> str:
    return hashlib.sha256(f"{uid}:{code}".encode()).hexdigest()


def enabled() -> bool:
    return mailer.enabled()


def request(email: str) -> None:
    """Hesap varsa ve e-postayla açılmışsa kod gönder. Yoksa sessizce hiçbir şey yapma."""
    email = (email or "").strip().lower()
    u = system.user_by_name(email) if "@" in email else None
    if not u or u["disabled"]:
        return
    now = int(time.time())
    with system.connect() as c:
        c.executescript(SCHEMA)
        row = c.execute("SELECT sent_at FROM password_resets WHERE user_id = ?", (u["id"],)).fetchone()
        if row and now - row[0] < RESEND_GAP:
            return
        if locked(c, u["id"]) or daily_count(c, u["id"], "reset_code") >= DAILY_CODES:
            return  # günlük kod tavanı ya da kilit: posta gitmez, yanıt yine aynı
        bump(c, u["id"], "reset_code")
        code = f"{secrets.randbelow(1_000_000):06d}"
        c.execute("INSERT OR REPLACE INTO password_resets (user_id, code_hash, expires, tries, sent_at) VALUES (?, ?, ?, 0, ?)",
                  (u["id"], _digest(u["id"], code), now + CODE_TTL, now))
    mailer.send(
        u["username"], f"HesapKitap şifre sıfırlama kodun: {code}",
        f"Merhaba,\n\nHesapKitap şifreni yenilemek için kodun: {code}\n\nKod 15 dakika geçerli. "
        "Bu isteği sen yapmadıysan bu maili yok sayabilirsin; şifren değişmez.\n\nHesapKitap",
        f"""<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:420px;margin:auto;padding:24px;color:#1c1917">
<h2 style="margin:0 0 12px">Şifre sıfırlama</h2>
<p>HesapKitap şifreni yenilemek için kodun:</p>
<p style="font-size:34px;font-weight:700;letter-spacing:8px;margin:18px 0">{code}</p>
<p style="color:#57534e">Kod 15 dakika geçerli. Bu isteği sen yapmadıysan bu maili yok sayabilirsin; şifren değişmez.</p>
</div>""")


def verify(email: str, code: str) -> dict | None:
    """Kod doğruysa kullanıcıyı döner ve kodu siler. Yanlışsa deneme sayılır; 5 yanlıştan sonra kod geçersiz."""
    email = (email or "").strip().lower()
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    u = system.user_by_name(email) if "@" in email else None
    if not u:
        return None
    with system.connect() as c:
        c.executescript(SCHEMA)
        if locked(c, u["id"]):  # günde 15 yanlıştan sonra 24 saat: doğru kod da kabul edilmez
            return None
        row = c.execute("SELECT code_hash, expires, tries FROM password_resets WHERE user_id = ?", (u["id"],)).fetchone()
        if not row or row[1] < time.time() or row[2] >= MAX_TRIES:
            bump(c, u["id"], "reset_wrong", lock_at=DAILY_WRONG)
            return None
        if not hmac.compare_digest(row[0], _digest(u["id"], code)):
            c.execute("UPDATE password_resets SET tries = tries + 1 WHERE user_id = ?", (u["id"],))
            bump(c, u["id"], "reset_wrong", lock_at=DAILY_WRONG)
            return None
        c.execute("DELETE FROM password_resets WHERE user_id = ?", (u["id"],))
    return u


def notify_changed(u: dict, by_admin: bool = False) -> None:
    """Şifre değişti bilgisi (kimin yaptığını kullanıcı bilsin). Posta kapalıysa ya da kullanıcı adı e-posta değilse
    gönderilmez; gönderim hatası işlemi bozmaz."""
    import logging

    if not (mailer.enabled() and "@" in (u.get("username") or "")):
        return
    who = ("Hesap yöneticisi şifreni sıfırladı ve sana geçici bir şifre verdi; ilk girişte yenisini belirlemen istenecek."
           if by_admin else "HesapKitap şifren e-postana gelen kodla değiştirildi.")
    try:
        mailer.send(u["username"], "HesapKitap: şifren değişti",
                    f"Merhaba,\n\n{who} Bütün açık oturumların kapatıldı.\n\nBunu sen yapmadıysan hemen \"Şifremi "
                    "unuttum\" ile yeni şifre belirle ve bize yaz.\n\nHesapKitap")
    except Exception:  # noqa: BLE001
        logging.getLogger("hesapkitap.reset").exception("şifre değişti postası gönderilemedi")
