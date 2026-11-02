"""Google (ve ileride Apple) ile giriş: uygulamanın aldığı kimlik belgesini (ID token) sunucu doğrular.

Belge Google'ın açık anahtarıyla imzalı bir JWT'dir; imza, yayıncı, süre ve "bu belge bizim uygulamamız için mi"
(aud = bizim client ID'lerimizden biri) kontrol edilir. Ek kütüphane yok: cryptography + httpx.
İzin verilen client ID'ler: HESAPKITAP_GOOGLE_CLIENT_IDS (virgülle; web, iOS, Android).
"""
import base64
import json
import os
import time

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

GOOGLE_CERTS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
APPLE_KEYS = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
_jwks: dict = {"at": 0.0, "keys": {}}
_apple_jwks: dict = {"at": 0.0, "keys": {}}


class SocialError(Exception):
    pass


def google_client_ids() -> list[str]:
    return [x.strip() for x in os.environ.get("HESAPKITAP_GOOGLE_CLIENT_IDS", "").split(",") if x.strip()]


def google_enabled() -> bool:
    return bool(google_client_ids())


def apple_client_ids() -> list[str]:
    """Apple'da aud = uygulamanın bundle ID'si (iOS'tan giriş). HESAPKITAP_APPLE_CLIENT_IDS, virgülle."""
    return [x.strip() for x in os.environ.get("HESAPKITAP_APPLE_CLIENT_IDS", "").split(",") if x.strip()]


def apple_enabled() -> bool:
    return bool(apple_client_ids())


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _google_keys(force: bool = False) -> dict:
    if force or time.time() - _jwks["at"] > 3600 or not _jwks["keys"]:
        data = httpx.get(GOOGLE_CERTS, timeout=10).json()
        _jwks["keys"] = {k["kid"]: k for k in data.get("keys", []) if k.get("kty") == "RSA"}
        _jwks["at"] = time.time()
    return _jwks["keys"]


def _apple_keys(force: bool = False) -> dict:
    if force or time.time() - _apple_jwks["at"] > 3600 or not _apple_jwks["keys"]:
        data = httpx.get(APPLE_KEYS, timeout=10).json()
        _apple_jwks["keys"] = {k["kid"]: k for k in data.get("keys", []) if k.get("kty") == "RSA"}
        _apple_jwks["at"] = time.time()
    return _apple_jwks["keys"]


# ---------------------------------------------------------------- tek kullanımlık nonce (D1: belge tekrar oynatma)
# Uygulama girişten önce POST /api/auth/social/nonce ile bir nonce alır, Google/Apple'a verir; belge o nonce'ı taşır
# (Google: aynen; Apple: çoğu kütüphane SHA-256 özetini koyar, ikisi de kabul). Nonce bir kez kullanılır, 10 dk geçerli:
# sızan bir kimlik belgesi (günlük, vekil) başka bir oturumda yeniden kullanılamaz. Nonce'suz eski istemci şimdilik
# kabul edilir; HESAPKITAP_SOCIAL_NONCE=required ile zorunlu olur (mobil sürüm nonce gönderince açılacak).
# Süreç belleğinde (hız sınırı sayaçları gibi): sunucu tek süreç; yeniden başlarsa kullanıcı yalnız tekrar dener.
NONCE_TTL = 600
NONCE_MAX = 20000
_nonces: dict[str, float] = {}
_nonce_lock = __import__("threading").Lock()


def nonce_required() -> bool:
    return os.environ.get("HESAPKITAP_SOCIAL_NONCE", "").strip().lower() == "required"


def issue_nonce() -> str:
    import secrets

    n = secrets.token_urlsafe(24)
    now = time.time()
    with _nonce_lock:
        if len(_nonces) >= NONCE_MAX:  # önce süresi dolanlar, yine doluysa en eskiler
            for k in [k for k, t in _nonces.items() if t < now] or list(_nonces)[: NONCE_MAX // 10]:
                _nonces.pop(k, None)
        _nonces[n] = now + NONCE_TTL
    return n


def check_nonce(claims: dict, nonce: str | None) -> None:
    """Belgedeki nonce, istemcinin verdiğiyle (ya da SHA-256 özetiyle) aynı mı ve bu sunucu mu vermişti, süresi geçerli
    mi? Burada düşmez: yeni hesapta uygulama onayı sorup AYNI belgeyle yeniden dener; oturum açılırken consume_nonce
    düşürür. Nonce'suz istek yalnız zorunlu değilken geçer."""
    if not nonce:
        if nonce_required():
            raise SocialError("Giriş belgesi eski: uygulamayı güncelleyip yeniden dene")
        return
    claim = str(claims.get("nonce") or "")
    import hashlib
    import hmac

    hashed = hashlib.sha256(nonce.encode()).hexdigest()
    if not claim or not (hmac.compare_digest(claim, nonce) or hmac.compare_digest(claim, hashed)):
        raise SocialError("Giriş belgesi bu oturuma ait değil")
    with _nonce_lock:
        until = _nonces.get(nonce)
    if not until or until < time.time():
        raise SocialError("Giriş belgesinin süresi doldu ya da zaten kullanıldı; yeniden dene")


def consume_nonce(nonce: str | None) -> bool:
    """Oturum açılmadan hemen önce: nonce bir kez kullanılır. Aynı anda iki istek gelirse yalnız biri True alır."""
    if not nonce:
        return not nonce_required()
    with _nonce_lock:
        until = _nonces.pop(nonce, None)
    return bool(until) and until >= time.time()


def _verify_rs256(token: str, keys_for) -> dict:
    try:
        head_b, body_b, sig_b = token.split(".")
        head, body = json.loads(_b64(head_b)), json.loads(_b64(body_b))
    except Exception as e:  # noqa: BLE001
        raise SocialError("Kimlik belgesi bozuk") from e
    if head.get("alg") != "RS256":
        raise SocialError("Desteklenmeyen imza")
    key = keys_for().get(head.get("kid")) or keys_for(force=True).get(head.get("kid"))
    if not key:
        raise SocialError("İmza anahtarı bulunamadı")
    pub = rsa.RSAPublicNumbers(int.from_bytes(_b64(key["e"]), "big"), int.from_bytes(_b64(key["n"]), "big")).public_key()
    try:
        pub.verify(_b64(sig_b), f"{head_b}.{body_b}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:  # noqa: BLE001
        raise SocialError("İmza doğrulanamadı") from e
    return body


def verify_google(id_token: str, keys_for=None, nonce: str | None = None) -> dict:
    """Doğrulanmış e-posta ve ad döner: {"email", "name"}. aud bizim client ID'lerimizden biri olmalı (D1): Android'de
    aud web client ID'sidir (azp Android'inki), iOS'ta iOS client ID'si; ikisi de HESAPKITAP_GOOGLE_CLIENT_IDS'te."""
    ids = google_client_ids()
    if not ids:
        raise SocialError("Google ile giriş bu sunucuda kapalı")
    c = _verify_rs256(id_token, keys_for or _google_keys)
    now = time.time()
    if c.get("iss") not in GOOGLE_ISSUERS:
        raise SocialError("Belge Google'dan değil")
    if c.get("aud") not in ids:
        if c.get("azp") in ids:  # eskiden kabul ediliyordu: yapılandırmada aud'un client ID'si eksik olabilir
            import logging

            logging.getLogger("hesapkitap.auth").warning(
                "Google belgesi reddedildi: aud listede yok, azp var (HESAPKITAP_GOOGLE_CLIENT_IDS'e web client ID'si eklenmeli)")
        raise SocialError("Belge bu uygulama için değil")
    if not (c.get("exp", 0) > now - 60 and c.get("iat", now) < now + 300):
        raise SocialError("Belgenin süresi dolmuş")
    if not c.get("email") or c.get("email_verified") not in (True, "true"):
        raise SocialError("Google hesabının e-postası doğrulanmamış")
    check_nonce(c, nonce)
    return {"email": c["email"].strip().lower(), "name": c.get("name") or ""}


def verify_apple(id_token: str, keys_for=None, nonce: str | None = None) -> dict:
    """Apple kimlik belgesi → {"email", "name": ""}. E-posta "Hide My Email" ile gizli aktarma adresi olabilir; o da
    kalıcıdır ve kişiye ulaşır, hesap onunla açılır. Ad belgede yoktur (yalnız ilk girişte uygulamaya verilir)."""
    ids = apple_client_ids()
    if not ids:
        raise SocialError("Apple ile giriş bu sunucuda kapalı")
    c = _verify_rs256(id_token, keys_for or _apple_keys)
    now = time.time()
    if c.get("iss") != APPLE_ISSUER:
        raise SocialError("Belge Apple'dan değil")
    if c.get("aud") not in ids:
        raise SocialError("Belge bu uygulama için değil")
    if not (c.get("exp", 0) > now - 60 and c.get("iat", now) < now + 300):
        raise SocialError("Belgenin süresi dolmuş")
    if not c.get("email") or c.get("email_verified") not in (True, "true"):
        raise SocialError("Apple hesabının e-postası alınamadı; Ayarlar'dan e-posta paylaşımına izin ver")
    check_nonce(c, nonce)
    # sub: Apple kimliği (bildirimlerde kullanıcıyı bulmak için özeti saklanır, app/apple.py); aud: token iptali client_id'si
    return {"email": c["email"].strip().lower(), "name": "", "sub": c.get("sub"), "aud": c.get("aud"), "via": "apple"}
