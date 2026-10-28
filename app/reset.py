"""Şifremi unuttum: e-postaya 6 haneli kod, 15 dakika geçerli, en çok 5 deneme.

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS password_resets (
    user_id INTEGER PRIMARY KEY,
    code_hash TEXT NOT NULL,
    expires INTEGER NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0,
    sent_at INTEGER NOT NULL
);
"""


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
        row = c.execute("SELECT code_hash, expires, tries FROM password_resets WHERE user_id = ?", (u["id"],)).fetchone()
        if not row or row[1] < time.time() or row[2] >= MAX_TRIES:
            return None
        if not hmac.compare_digest(row[0], _digest(u["id"], code)):
            c.execute("UPDATE password_resets SET tries = tries + 1 WHERE user_id = ?", (u["id"],))
            return None
        c.execute("DELETE FROM password_resets WHERE user_id = ?", (u["id"],))
    return u
