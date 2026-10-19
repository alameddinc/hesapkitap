"""Tek kullanıcılı şifre koruması.

Şifre kaynağı: APP_PASSWORD ortam değişkeni (sunucu yöneticileri) ya da ilk açılışta uygulamadan belirlenen şifre
(PBKDF2-SHA256 hash'i DB'de). Hiçbiri yoksa "kurulum gerekli": ilk açan kişi şifreyi belirler.
Yerel ağ dışından gelen kurulum isteği için container log'una yazılan tek seferlik kod istenir.

Oturum: HMAC-SHA256 imzalı çerez (son kullanma + imza). Sunucu tarafında durum tutulmaz.
HESAPKITAP_NO_AUTH=1 sadece testler içindir.
"""
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import time
from collections import defaultdict, deque

from app import config, db

COOKIE = "ekstre_session"
SESSION_DAYS = 30
MAX_FAILS = 5          # pencere başına hatalı deneme
FAIL_WINDOW = 15 * 60  # saniye
PBKDF2_ITER = 600_000
MIN_PASSWORD = 10

log = logging.getLogger("hesapkitap.auth")
_fails: dict[str, deque] = defaultdict(deque)
_setup_code: str | None = None


def disabled() -> bool:
    return os.environ.get("HESAPKITAP_NO_AUTH") == "1"


def _hash(pw: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${salt.hex()}${h.hex()}"


def _verify(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except ValueError:
        return False


def password_source() -> str:
    if os.environ.get("APP_PASSWORD"):
        return "env"
    return "app" if db.get_setting("auth:password_hash") else "none"


def setup_required() -> bool:
    return not disabled() and password_source() == "none"


def enabled() -> bool:
    return not disabled()


def setup_code() -> str:
    """Yerel ağ dışından ilk kurulum için tek seferlik kod; log'a yazılır."""
    global _setup_code
    if not _setup_code:
        _setup_code = f"{secrets.randbelow(10**6):06d}"
        log.warning("HesapKitap ilk kurulum kodu: %s (uzaktan kurulumda istenir)", _setup_code)
    return _setup_code


def is_local(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback
    except ValueError:
        return False


def set_password(pw: str) -> None:
    if len(pw) < MIN_PASSWORD:
        raise ValueError(f"Şifre en az {MIN_PASSWORD} karakter olmalı")
    db.set_setting("auth:password_hash", _hash(pw))
    # şifre değişince eski oturumlar düşsün
    db.set_setting("auth:secret_key", secrets.token_hex(32))


def check_password(pw: str) -> bool:
    env = os.environ.get("APP_PASSWORD")
    if env:
        return hmac.compare_digest(pw.encode(), env.encode())
    stored = db.get_setting("auth:password_hash")
    return bool(stored) and _verify(pw, stored)


def _secret() -> bytes:
    return hashlib.sha256(config.secret_key().encode()).digest()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{exp}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(payload)}"


def valid_token(token: str | None) -> bool:
    if not token or token.count(".") != 2:
        return False
    exp, nonce, sig = token.split(".")
    if not hmac.compare_digest(sig, _sign(f"{exp}.{nonce}")):
        return False
    return exp.isdigit() and int(exp) > time.time()


def rate_limited(ip: str) -> bool:
    q = _fails[ip]
    now = time.time()
    while q and q[0] < now - FAIL_WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILS


def record_fail(ip: str) -> None:
    _fails[ip].append(time.time())


def cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "0") == "1"
