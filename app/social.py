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
_jwks: dict = {"at": 0.0, "keys": {}}


class SocialError(Exception):
    pass


def google_client_ids() -> list[str]:
    return [x.strip() for x in os.environ.get("HESAPKITAP_GOOGLE_CLIENT_IDS", "").split(",") if x.strip()]


def google_enabled() -> bool:
    return bool(google_client_ids())


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _google_keys(force: bool = False) -> dict:
    if force or time.time() - _jwks["at"] > 3600 or not _jwks["keys"]:
        data = httpx.get(GOOGLE_CERTS, timeout=10).json()
        _jwks["keys"] = {k["kid"]: k for k in data.get("keys", []) if k.get("kty") == "RSA"}
        _jwks["at"] = time.time()
    return _jwks["keys"]


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


def verify_google(id_token: str, keys_for=None) -> dict:
    """Doğrulanmış e-posta ve ad döner: {"email", "name"}."""
    ids = google_client_ids()
    if not ids:
        raise SocialError("Google ile giriş bu sunucuda kapalı")
    c = _verify_rs256(id_token, keys_for or _google_keys)
    now = time.time()
    if c.get("iss") not in GOOGLE_ISSUERS:
        raise SocialError("Belge Google'dan değil")
    if c.get("aud") not in ids and c.get("azp") not in ids:
        raise SocialError("Belge bu uygulama için değil")
    if not (c.get("exp", 0) > now - 60 and c.get("iat", now) < now + 300):
        raise SocialError("Belgenin süresi dolmuş")
    if not c.get("email") or c.get("email_verified") not in (True, "true"):
        raise SocialError("Google hesabının e-postası doğrulanmamış")
    return {"email": c["email"].strip().lower(), "name": c.get("name") or ""}
