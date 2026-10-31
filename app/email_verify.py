"""E-posta doğrulama (hazır sürüm, açık kayıt, posta ayarlıysa): hesabın önceden ele geçirilmesine karşı (denetim M8).

Şifreyle açılan hesap, e-postaya giden 6 haneli kod girilene kadar DOĞRULANMAMIŞ: oturum açılmaz (veri yazamaz,
okuyamaz). Doğrulanmamış hesaba aynı e-postayla Google/Apple girişi gelirse e-postanın sahibi odur: şifre geçersiz
kılınır, oturumlar düşer, hesap doğrulanmış olur (main._social_login). Aynı e-postayla tekrar kayıt: doğrulanmamışsa
yeni şifre ve yeni kod (kodu kim girerse hesap onun), doğrulanmışsa yanıt yine nötr; adrese "zaten hesabın var" maili.
Şifre sıfırlama kodu da e-postayı doğrular.

HESAPKITAP_EMAIL_VERIFY=0: kod adımı yok (kayıt hemen oturum açar), ama hesap yine "doğrulanmamış" işaretlenir: Google/
Apple girişi gelince şifre geçersiz, oturumlar düşer; kayıt yanıtı nötr kalır.

Durum sistem veritabanında: "email_unverified:<id>" (yoksa doğrulanmış sayılır: eski hesaplar, davetle açılanlar,
Google/Apple ile açılanlar). Kod yalnız özetiyle durur; 30 dakika, en çok 5 deneme, dakikada en fazla bir gönderim.
"""
import hashlib
import hmac
import os
import secrets
import time

from app import mailer, plans, system

CODE_TTL = 30 * 60
MAX_TRIES = 5
RESEND_GAP = 60
DAILY_CODES = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS email_codes (
    user_id INTEGER PRIMARY KEY,
    code_hash TEXT NOT NULL,
    expires INTEGER NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0,
    sent_at INTEGER NOT NULL
);
"""


def protected() -> bool:
    """Önceden ele geçirme korumaları (hazır sürüm + açık kayıt): şifreli kayıt "doğrulanmamış" işaretlenir (Google/
    Apple girişi gelince e-postanın sahibi hesabı alır), kayıt yanıtı nötr, girişte sahte PBKDF2."""
    return plans.hosted() and plans.open_signup()


def required() -> bool:
    """Kayıt sonrası kod adımı istenir mi? Korumalar açık + giden posta ayarlı + HESAPKITAP_EMAIL_VERIFY != "0".
    Anahtar yalnız kod adımını kapatır (kod adımı olmayan eski mobil sürüm takılmasın); diğer korumalar sürer."""
    return protected() and mailer.enabled() and os.environ.get("HESAPKITAP_EMAIL_VERIFY", "1") != "0"


def unverified(uid: int | None) -> bool:
    return bool(uid) and system.get(f"email_unverified:{uid}") == "1"


def mark_unverified(uid: int) -> None:
    system.put(f"email_unverified:{uid}", "1")


def mark_verified(uid: int) -> None:
    system.put(f"email_unverified:{uid}", None)
    with system.connect() as c:
        c.executescript(SCHEMA)
        c.execute("DELETE FROM email_codes WHERE user_id = ?", (uid,))


def _digest(uid: int, code: str) -> str:
    return hashlib.sha256(f"verify:{uid}:{code}".encode()).hexdigest()


def send_code(u: dict) -> bool:
    """Doğrulama kodu gönder (dakikada en fazla bir). Gönderildiyse True."""
    now = int(time.time())
    with system.connect() as c:
        c.executescript(SCHEMA)
        row = c.execute("SELECT sent_at FROM email_codes WHERE user_id = ?", (u["id"],)).fetchone()
        if row and now - row[0] < RESEND_GAP:
            return False
        from app import reset

        c.executescript(reset.SCHEMA)
        if reset.daily_count(c, u["id"], "verify_code") >= DAILY_CODES:  # hesap başı günlük posta tavanı (O3)
            return False
        reset.bump(c, u["id"], "verify_code")
        code = f"{secrets.randbelow(1_000_000):06d}"
        c.execute("INSERT OR REPLACE INTO email_codes (user_id, code_hash, expires, tries, sent_at) VALUES (?, ?, ?, 0, ?)",
                  (u["id"], _digest(u["id"], code), now + CODE_TTL, now))
    mailer.send(
        u["username"], f"HesapKitap doğrulama kodun: {code}",
        f"Merhaba,\n\nHesapKitap hesabını açmak için doğrulama kodun: {code}\n\nKod 30 dakika geçerli. Bu kaydı sen "
        "yapmadıysan bu maili yok sayabilirsin; kod girilmeden hesap açılmaz.\n\nHesapKitap",
        f"""<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:420px;margin:auto;padding:24px;color:#1c1917">
<h2 style="margin:0 0 12px">E-postanı doğrula</h2>
<p>HesapKitap hesabını açmak için kodun:</p>
<p style="font-size:34px;font-weight:700;letter-spacing:8px;margin:18px 0">{code}</p>
<p style="color:#57534e">Kod 30 dakika geçerli. Bu kaydı sen yapmadıysan bu maili yok sayabilirsin; kod girilmeden hesap açılmaz.</p>
</div>""")
    return True


def notify_existing(u: dict) -> None:
    """Doğrulanmış hesabın e-postasıyla yeniden kayıt denendi: hesap sahibine bilgi (yanıt nötr kaldı)."""
    try:
        mailer.send(u["username"], "HesapKitap: bu e-postayla zaten bir hesabın var",
                    "Merhaba,\n\nBu e-posta adresiyle yeni bir HesapKitap hesabı açılmak istendi; adresin zaten kayıtlı. "
                    "Giriş yapabilir ya da şifreni unuttuysan \"Şifremi unuttum\" ile yenileyebilirsin. Bu isteği sen "
                    "yapmadıysan bir şey yapmana gerek yok; hesabın değişmedi.\n\nHesapKitap")
    except Exception:  # noqa: BLE001 - posta gitmese de yanıt aynı
        pass


def notify_taken_over(u: dict) -> None:
    """Doğrulanmamış hesaba Google/Apple ile girildi: e-postanın sahibine bilgi."""
    try:
        mailer.send(u["username"], "HesapKitap: hesabın Google/Apple girişine bağlandı",
                    "Merhaba,\n\nBu e-postayla daha önce şifreyle açılmış ama doğrulanmamış bir hesap vardı. Google/Apple "
                    "ile girdiğin için hesap artık senin: eski şifre geçersiz kılındı ve açık oturumlar kapatıldı. "
                    "İstersen \"Şifremi unuttum\" ile kendi şifreni koyabilirsin.\n\nHesapKitap")
    except Exception:  # noqa: BLE001
        pass


def verify(email: str, code: str) -> dict | None:
    """Kod doğruysa kullanıcı (ve hesap doğrulanır). Yanlışsa deneme sayılır; 5 yanlıştan sonra kod geçersiz."""
    email = (email or "").strip().lower()
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    u = system.user_by_name(email) if "@" in email else None
    if not u or not unverified(u["id"]):
        return None
    with system.connect() as c:
        c.executescript(SCHEMA)
        row = c.execute("SELECT code_hash, expires, tries FROM email_codes WHERE user_id = ?", (u["id"],)).fetchone()
        if not row or row[1] < time.time() or row[2] >= MAX_TRIES:
            return None
        if not hmac.compare_digest(row[0], _digest(u["id"], code)):
            c.execute("UPDATE email_codes SET tries = tries + 1 WHERE user_id = ?", (u["id"],))
            return None
    mark_verified(u["id"])
    return u
