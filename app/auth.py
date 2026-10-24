"""Kullanıcı girişi (çok kullanıcılı).

Kullanıcılar sistem veritabanındadır (app/system.py); her kullanıcının finans verisi kendi dosyasında.
İlk kullanıcı yöneticidir; eski tek kullanıcılı kurulum ilk açılışta otomatik yöneticiye taşınır (şifre ve veri aynen).
Yönetici şifresi APP_PASSWORD ortam değişkeninden de gelebilir (sunucu yöneticileri).
Hiç kullanıcı yoksa "kurulum gerekli": ilk açan kişi yönetici olur; yerel ağ dışından ise log'daki tek seferlik kod istenir.

Oturum: HMAC-SHA256 imzalı çerez (son kullanma, kullanıcı, oturum sürümü, imza). Şifre değişince ya da kullanıcı
kapatılınca oturum sürümü artar, eski çerezler geçersiz olur. HESAPKITAP_NO_AUTH=1 sadece testler içindir.
"""
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import time
from collections import defaultdict, deque

from app import db, system

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


def enabled() -> bool:
    return not disabled()


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


def ensure_admin() -> None:
    """Eski tek kullanıcılı kurulumu yöneticiye taşı (bir kez): APP_PASSWORD ya da ana veritabanındaki şifre."""
    from app import config

    system.init()
    config.migrate_globals()
    if system.has_users():
        return
    if os.environ.get("APP_PASSWORD"):
        system.create_user("admin", None, role="admin", main_db=True)
        return
    with db.use_space(None):
        old = db.get_setting("auth:password_hash")
    if old:
        system.create_user("admin", old, role="admin", main_db=True)
        log.warning("Tek kullanıcılı kurulum yönetici hesabına taşındı: kullanıcı adı 'admin', şifre aynı")


def admin() -> dict | None:
    return next((u for u in system.users() if u["role"] == "admin"), None)


def password_source(u: dict | None = None) -> str:
    u = u or admin()
    if u and u["role"] == "admin" and not u["password_hash"] and os.environ.get("APP_PASSWORD"):
        return "env"
    return "app" if u else "none"


def setup_required() -> bool:
    if disabled():
        return False
    ensure_admin()
    return not system.has_users()


def setup_code() -> str:
    """Yerel ağ dışından ilk kurulum için tek seferlik kod; log'a yazılır."""
    global _setup_code
    if not _setup_code and os.environ.get("SETUP_CODE"):  # tek tıkla kurulumda panelden okunur (Render/Railway/Fly)
        _setup_code = os.environ["SETUP_CODE"].strip()
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


def _check_strength(pw: str) -> None:
    if len(pw) < MIN_PASSWORD:
        raise ValueError(f"Şifre en az {MIN_PASSWORD} karakter olmalı")


def create_admin(username: str, pw: str) -> dict:
    """İlk kurulum: yönetici, ana veritabanını kullanır."""
    _check_strength(pw)
    return system.create_user(username or "admin", _hash(pw), role="admin", main_db=True)


def create_user(username: str, pw: str, must_change: bool = False) -> dict:
    _check_strength(pw)
    return system.create_user(username, _hash(pw), must_change=must_change)


def temp_password() -> str:
    """Yöneticinin verdiği geçici şifre (okunaklı, ilk girişte değiştirilir)."""
    abc = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(abc) for _ in range(4)) for _ in range(3))


def set_password(uid: int, pw: str, must_change: bool = False) -> None:
    _check_strength(pw)
    system.update_user(uid, password_hash=_hash(pw), must_change_password=int(must_change))
    system.bump_session(uid)  # eski oturumlar düşsün


def login(username: str | None, pw: str) -> dict | None:
    """Kullanıcı adı verilmezse yönetici (eski tek kullanıcılı giriş ekranı ve APP_PASSWORD uyumu)."""
    u = system.user_by_name(username) if username else admin()
    if not u or u["disabled"]:
        return None
    if not u["password_hash"]:
        env = os.environ.get("APP_PASSWORD")
        ok = bool(env) and u["role"] == "admin" and hmac.compare_digest(pw.encode(), env.encode())
    else:
        ok = _verify(pw, u["password_hash"])
    if ok:
        system.update_user(u["id"], last_login=time.strftime("%Y-%m-%d %H:%M:%S"))
    return u if ok else None


def check_password(u: dict, pw: str) -> bool:
    if not u["password_hash"]:
        env = os.environ.get("APP_PASSWORD")
        return bool(env) and hmac.compare_digest(pw.encode(), env.encode())
    return _verify(pw, u["password_hash"])


def _secret() -> bytes:
    v = os.environ.get("SECRET_KEY") or system.get("secret_key")
    if not v:
        v = secrets.token_hex(32)
        system.put("secret_key", v)
    return hashlib.sha256(v.encode()).digest()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(u: dict) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{exp}.{u['id']}.{u['session_version']}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(payload)}"


def token_user(token: str | None) -> dict | None:
    """Geçerli oturumun kullanıcısı (imza, süre, oturum sürümü ve kapatılma kontrol edilir)."""
    if not token or token.count(".") != 4:
        return None
    exp, uid, ver, nonce, sig = token.split(".")
    if not hmac.compare_digest(sig, _sign(f"{exp}.{uid}.{ver}.{nonce}")):
        return None
    if not (exp.isdigit() and int(exp) > time.time() and uid.isdigit() and ver.isdigit()):
        return None
    u = system.user(int(uid))
    if not u or u["disabled"] or u["session_version"] != int(ver):
        return None
    return u


def valid_token(token: str | None) -> bool:
    return token_user(token) is not None


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
