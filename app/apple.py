"""Apple ile giriş, sunucu tarafı: hesap silinince Apple belgesinin iptali (App Store 5.1.1(v)) ve Apple'ın sunucudan
sunucuya bildirimleri.

Token iptali (isteğe bağlı; üç ayar da varsa açık, yoksa sessizce kapalı ve giriş bugünkü gibi çalışır)
- HESAPKITAP_APPLE_TEAM_ID, HESAPKITAP_APPLE_KEY_ID, HESAPKITAP_APPLE_PRIVATE_KEY (PEM içerik; satır sonları "\\n" olarak
  da yazılabilir) ya da HESAPKITAP_APPLE_PRIVATE_KEY_FILE (.p8 dosyasının yolu).
- İstemci sırrı: ES256 JWT (iss = Team ID, sub = client_id yani bundle ID, aud = https://appleid.apple.com, kid = Key ID),
  .p8 anahtarıyla imzalı, 5 dk geçerli; her çağrıda yeniden üretilir.
- Girişte uygulama Apple'ın verdiği authorizationCode'u da gönderir: oturum açılırken POST /auth/token
  (grant_type=authorization_code) → refresh_token. Belge kullanıcı başına AES-GCM ile şifreli saklanır (anahtar sunucu
  gizli anahtarından HKDF ile; düz belge diske yazılmaz). Değişim başarısız olursa giriş yine açılır, yalnız loglanır.
- Hesap kalıcı silinirken (app/deletion.purge; yönetici silmesi ve üye çıkarma da) POST /auth/revoke
  (token_type_hint=refresh_token). Hata silmeyi hiçbir zaman engellemez: iptal sıraya yazılır, dakikalık iş
  (app/jobs.run_account_purge) artan aralıklarla yeniden dener; 30 gün sonra bırakır ve silme kaydına not düşer.

Sunucudan sunucuya bildirimler (POST /api/auth/apple/notifications; App ID'de tanımlanan uç)
- Gövde {"payload": "<JWT>"}; Apple'ın JWKS'iyle (RS256) imza, iss, aud (HESAPKITAP_APPLE_CLIENT_IDS) doğrulanır.
- Kullanıcı Apple kimliğiyle (sub) bulunur. sub düz saklanmaz: HMAC özeti (sunucu anahtarıyla) → kullanıcı id'si.
  Bu özellikten önce Apple ile girmiş kullanıcının özeti yoktur; olaydaki e-postayla eşleşir, ilk girişte özet yazılır.
- email-disabled / email-enabled: gizli aktarma adresine yönlendirme kapandı/açıldı → yalnız işaret (apple_email:<id>).
- consent-revoked: kullanıcı Apple ayarlarından uygulamanın bağını kaldırdı → Apple ile açılmış oturumlar düşer,
  saklı belge ve bağ silinir. Hesap ve veri kalır (e-posta/şifre ya da yeniden Apple ile girebilir).
- account-delete: Apple hesabı kalıcı silindi. Kişi bize başka yoldan ulaşamıyorsa (şifresi yok VE e-postası Apple'ın
  gizli aktarma adresi: o adres de Apple hesabıyla ölür) hesap 7 günlük yumuşak silme akışına girer (app/deletion;
  abonelik adımı aynı: Google Play yenilemesi durdurulmaya çalışılır, App Store aboneliği zaten Apple hesabıyla biter).
  Kişi ulaşabiliyorsa (şifresi var ya da gerçek e-posta) ya da ortak defter sahibiyse (üyelerin defteri birlikte
  silinmesin) yalnız Apple bağı kopar ve oturumlar düşer; hesabı kendisi silebilir. Yönetici hesabı hiç silinmez.
- Her durumda 200 döner (Apple yeniden denemesin); hatalar loglanır.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

from app import system

log = logging.getLogger("hesapkitap.apple")

AUD = "https://appleid.apple.com"
TOKEN_URL = "https://appleid.apple.com/auth/token"
REVOKE_URL = "https://appleid.apple.com/auth/revoke"
RELAY_DOMAIN = "@privaterelay.appleid.com"
HTTP_CLIENT = None  # testler httpx.MockTransport'lu istemci koyar
RETRY_GIVE_UP = 30 * 86400
NOTIFY_MAX_AGE = 30 * 86400  # Apple başarısız bildirimi bir süre yeniden dener; çok eski belge kabul edilmez


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _http():
    import httpx

    return HTTP_CLIENT or httpx.Client(timeout=8)


# ---------------------------------------------------------------- ayarlar ve istemci sırrı

def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _private_pem() -> str:
    v = os.environ.get("HESAPKITAP_APPLE_PRIVATE_KEY", "")
    if not v.strip() and (path := _env("HESAPKITAP_APPLE_PRIVATE_KEY_FILE")):
        try:
            with open(path, encoding="utf-8") as f:
                v = f.read()
        except OSError:
            log.warning("apple: HESAPKITAP_APPLE_PRIVATE_KEY_FILE okunamadı")
            return ""
    return v.replace("\\n", "\n").strip()


def _private_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    pem = _private_pem()
    if not pem:
        return None
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except Exception:  # noqa: BLE001 - bozuk anahtar: özellik kapalı sayılır, giriş bozulmaz
        log.warning("apple: özel anahtar okunamadı (PEM / .p8 bekleniyor)")
        return None
    return key if isinstance(key, ec.EllipticCurvePrivateKey) else None


def revoke_enabled() -> bool:
    """Token değişimi/iptali açık mı: Apple ile giriş açık + Team ID + Key ID + geçerli EC özel anahtar."""
    from app import social

    return bool(social.apple_enabled() and _env("HESAPKITAP_APPLE_TEAM_ID") and _env("HESAPKITAP_APPLE_KEY_ID")
                and _private_key() is not None)


def client_secret(client_id: str, now: float | None = None) -> str:
    """Apple REST istemci sırrı: ES256 JWT. İmza JOSE biçiminde (r || s, 64 bayt), DER değil."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = _private_key()
    if key is None:
        raise RuntimeError("Apple özel anahtarı yok")
    iat = int(now or time.time())
    head = {"alg": "ES256", "kid": _env("HESAPKITAP_APPLE_KEY_ID"), "typ": "JWT"}
    body = {"iss": _env("HESAPKITAP_APPLE_TEAM_ID"), "iat": iat, "exp": iat + 300, "aud": AUD, "sub": client_id}
    signing = f"{_b64e(json.dumps(head, separators=(',', ':')).encode())}.{_b64e(json.dumps(body, separators=(',', ':')).encode())}"
    r, s = decode_dss_signature(key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    return f"{signing}.{_b64e(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# ---------------------------------------------------------------- şifreli saklama

def _aead():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    from app import auth

    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"hesapkitap/apple-refresh-token/v1").derive(auth._secret())
    return AESGCM(key)


def seal(text: str, aad: str) -> str:
    n = secrets.token_bytes(12)
    return "v1:" + _b64e(n + _aead().encrypt(n, text.encode(), aad.encode()))


def unseal(blob: str, aad: str) -> str:
    if not blob.startswith("v1:"):
        raise ValueError("bilinmeyen biçim")
    raw = _b64d(blob[3:])
    return _aead().decrypt(raw[:12], raw[12:], aad.encode()).decode()


def sub_ref(sub: str) -> str:
    from app import auth

    return hmac.new(auth._secret(), f"apple-sub:{sub}".encode(), hashlib.sha256).hexdigest()


def _aad(uid: int) -> str:
    return f"apple-rt:{uid}"


def _load(key: str) -> dict | None:
    v = system.get(key)
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None


def link_of(uid: int) -> dict | None:
    return _load(f"apple:{uid}")


def user_for_sub(sub: str) -> int | None:
    v = system.get(f"apple_sub:{sub_ref(sub)}")
    return int(v) if v and v.isdigit() else None


def forget(uid: int) -> dict | None:
    """Apple bağını ve saklı belgeyi sil (özet dizini dahil). Dönen: silinen kayıt (iptal için)."""
    cur = link_of(uid)
    if cur and cur.get("ref") and system.get(f"apple_sub:{cur['ref']}") == str(uid):
        system.put(f"apple_sub:{cur['ref']}", None)
    system.put(f"apple:{uid}", None)
    return cur


# ---------------------------------------------------------------- giriş: bağ + token değişimi

def _claims_unverified(jwt: str) -> dict:
    try:
        return json.loads(_b64d(jwt.split(".")[1]))
    except Exception:  # noqa: BLE001
        return {}


def exchange(code: str, client_id: str) -> dict:
    """authorizationCode → {"refresh_token", "sub"}. Kod tek kullanımlık, 5 dk geçerli."""
    r = _http().post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret(client_id),
                                      "code": code, "grant_type": "authorization_code"})
    if r.status_code != 200:
        raise RuntimeError(f"token değişimi {r.status_code}: {(r.text or '')[:120]}")
    data = r.json()
    if not data.get("refresh_token"):
        raise RuntimeError("yanıtta refresh_token yok")
    # yanıt TLS ile doğrudan Apple'dan: id_token'daki sub yeterli (kodun bu kullanıcıya ait olduğunu denetlemek için)
    return {"refresh_token": data["refresh_token"], "sub": _claims_unverified(data.get("id_token") or "").get("sub")}


def link(uid: int, sub: str | None, client_id: str | None, code: str | None = None) -> dict:
    """Apple ile girişte (oturum açılmadan hemen önce): Apple kimliği ↔ hesap bağı ve (açıksa) refresh_token.
    Hiçbir hata girişi bozmaz. Dönen: {"linked", "token"} (testler ve log için)."""
    out = {"linked": False, "token": False}
    if not sub:
        return out
    try:
        ref = sub_ref(sub)
        cur = link_of(uid) or {}
        if cur.get("ref") and cur["ref"] != ref and system.get(f"apple_sub:{cur['ref']}") == str(uid):
            system.put(f"apple_sub:{cur['ref']}", None)  # aynı hesaba başka Apple kimliği girdi: eski dizin düşer
            cur = {}
        rec = {"ref": ref, "client_id": client_id or cur.get("client_id"), "rt": cur.get("rt"), "at": cur.get("at")}
        if code and client_id and revoke_enabled():
            try:
                got = exchange(code, client_id)
                if got["sub"] and got["sub"] != sub:
                    log.warning("apple: token değişimindeki kimlik giriş belgesiyle tutmuyor (kullanıcı %s); saklanmadı", uid)
                else:
                    rec.update(rt=seal(got["refresh_token"], _aad(uid)), at=int(time.time()))
                    out["token"] = True
            except Exception as e:  # noqa: BLE001 - değişim olmasa da giriş açılır
                log.warning("apple: token değişimi başarısız (kullanıcı %s): %s", uid, e)
        system.put(f"apple:{uid}", json.dumps(rec))
        system.put(f"apple_sub:{ref}", str(uid))
        out["linked"] = True
    except Exception:  # noqa: BLE001
        log.exception("apple: bağ yazılamadı (kullanıcı %s)", uid)
    return out


# ---------------------------------------------------------------- iptal (kalıcı silmede) ve yeniden deneme

def revoke(refresh_token: str, client_id: str) -> tuple[bool, str]:
    """(bitti mi, ayrıntı). 200: iptal edildi. 400 invalid_grant: belge zaten geçersiz (kullanıcı bağı kaldırmış) →
    bitti sayılır. Diğerleri (ağ, 5xx, invalid_client) yeniden denenir."""
    try:
        r = _http().post(REVOKE_URL, data={"client_id": client_id, "client_secret": client_secret(client_id),
                                           "token": refresh_token, "token_type_hint": "refresh_token"})
    except Exception as e:  # noqa: BLE001
        return False, f"ağ: {str(e)[:120]}"
    if r.status_code == 200:
        return True, "revoked"
    err = ""
    try:
        err = (r.json() or {}).get("error") or ""
    except ValueError:
        pass
    if r.status_code == 400 and err == "invalid_grant":
        return True, "already_invalid"
    return False, f"{r.status_code} {err}".strip()


def _note(uid: int, note: dict) -> None:
    try:
        from app import deletion

        deletion._note(uid, {"action": "apple_revoke", **note})
    except Exception:  # noqa: BLE001
        log.debug("apple: silme kaydına not yazılamadı", exc_info=True)


def _queue(uid: int, item: dict, now: float) -> None:
    n = int(item.get("attempts") or 0)
    item.update(attempts=n, next=now + min(3600 * 2 ** max(n - 1, 0), 86400))
    system.put(f"apple_revoke:{uid}", json.dumps(item))


def on_delete(uid: int, now: float | None = None) -> dict:
    """Hesap kalıcı silinirken: bağı sil, saklı belge varsa Apple'da iptal et. Başarısızsa sıraya yaz. Asla hata atmaz."""
    now = now or time.time()
    try:
        cur = forget(uid)
        system.put(f"apple_email:{uid}", None)
        if not cur or not cur.get("rt") or not cur.get("client_id"):
            return {"skipped": "no_token"}
        item = {"rt": cur["rt"], "client_id": cur["client_id"], "since": now, "attempts": 0}
        return _attempt(uid, item, now)
    except Exception as e:  # noqa: BLE001
        log.exception("apple: iptal hazırlanamadı (kullanıcı %s)", uid)
        return {"error": str(e)[:120]}


def _attempt(uid: int, item: dict, now: float) -> dict:
    if not revoke_enabled():
        item["attempts"] = int(item.get("attempts") or 0)
        if now - float(item.get("since") or now) >= RETRY_GIVE_UP:
            system.put(f"apple_revoke:{uid}", None)
            log.warning("apple: iptal ayarları 30 gündür eksik, belge bırakıldı (kullanıcı %s)", uid)
            _note(uid, {"result": "gave_up", "detail": "not_configured", "warning": "Apple belgesi iptal edilemedi"})
            return {"revoked": False, "gave_up": True}
        _queue(uid, item, now)  # anahtar kaldırılmış/eksik: ayar gelince denenir (30 gün)
        log.info("apple: iptal ayarları eksik, belge sırada bekliyor (kullanıcı %s)", uid)
        return {"queued": "not_configured"}
    try:
        token = unseal(item["rt"], _aad(uid))
    except Exception:  # noqa: BLE001 - sunucu anahtarı değişmiş: belge çözülemez, iptal edilemez
        system.put(f"apple_revoke:{uid}", None)
        log.warning("apple: saklı belge çözülemedi, iptal edilemedi (kullanıcı %s)", uid)
        _note(uid, {"result": "undecryptable"})
        return {"error": "undecryptable"}
    done, detail = revoke(token, item["client_id"])
    item["attempts"] = int(item.get("attempts") or 0) + 1
    if done:
        system.put(f"apple_revoke:{uid}", None)
        log.info("apple: belge iptal edildi (kullanıcı %s, %s)", uid, detail)
        _note(uid, {"result": detail, "attempts": item["attempts"]})
        return {"revoked": True, "detail": detail}
    if now - float(item.get("since") or now) >= RETRY_GIVE_UP:
        system.put(f"apple_revoke:{uid}", None)
        log.warning("apple: iptal 30 gündür başarısız, bırakıldı (kullanıcı %s): %s", uid, detail)
        _note(uid, {"result": "gave_up", "detail": detail, "attempts": item["attempts"], "warning": "Apple belgesi iptal edilemedi"})
        return {"revoked": False, "gave_up": True}
    _queue(uid, item, now)
    log.warning("apple: iptal başarısız, yeniden denenecek (kullanıcı %s): %s", uid, detail)
    _note(uid, {"result": "retry", "detail": detail, "attempts": item["attempts"]})
    return {"revoked": False, "queued": detail}


def retry_pending(now: float | None = None) -> list[int]:
    """Sırada bekleyen iptaller (dakikalık iş; sorgu ucuz, ağ yalnız zamanı gelende). Dönen: bu turda denenen id'ler."""
    now = now or time.time()
    with system.connect() as c:
        rows = c.execute("SELECT key, value FROM sys_settings WHERE key LIKE 'apple_revoke:%'").fetchall()
    tried = []
    for key, value in rows:
        try:
            item, uid = json.loads(value), int(key.split(":", 1)[1])
        except ValueError:
            system.put(key, None)
            continue
        if float(item.get("next") or 0) > now:
            continue
        _attempt(uid, item, now)
        tried.append(uid)
    return tried


# ---------------------------------------------------------------- sunucudan sunucuya bildirimler

def _verify_notification(payload: str, keys_for=None) -> dict:
    from app import social

    c = social._verify_rs256(payload, keys_for or social._apple_keys)
    now = time.time()
    if c.get("iss") != social.APPLE_ISSUER:
        raise social.SocialError("Belge Apple'dan değil")
    aud = c.get("aud")
    ids = social.apple_client_ids()
    if not ids or not (aud in ids or (isinstance(aud, list) and set(aud) & set(ids))):
        raise social.SocialError("Belge bu uygulama için değil")
    if c.get("exp") and c["exp"] < now - 60:
        raise social.SocialError("Belgenin süresi dolmuş")
    iat = c.get("iat") or now
    if iat > now + 300 or iat < now - NOTIFY_MAX_AGE:
        raise social.SocialError("Belge zamanı geçersiz")
    return c


def _find_user(sub: str | None, email: str | None) -> dict | None:
    uid = user_for_sub(sub) if sub else None
    if uid:
        return system.user(uid)
    if email:  # bu özellikten önce Apple ile girmiş kullanıcı: girişteki gibi e-postayla
        return system.user_by_name(email.strip().lower())
    return None


def _drop_apple_sessions(u: dict) -> int:
    from app import auth, sessions

    # şifresiz hesapta etiketsiz oturum şifreyle açılmış olamaz: etiketten önceki (muhtemelen Apple) oturumlar da düşer.
    # Google ile açılanlar (via google) kalır. Hiç kullanılmamış eski anahtarlar için ayrıca oturum sürümü artar.
    unset = auth.password_unset(u["id"])
    n = sessions.revoke_via(u["id"], "apple", untagged=unset)
    if unset and not _has_google_session(u["id"]):
        system.bump_session(u["id"])
    return n


def _has_google_session(uid: int) -> bool:
    with system.connect() as c:
        try:
            return bool(c.execute("SELECT 1 FROM sessions WHERE user_id = ? AND via = 'google' AND revoked = 0",
                                  (uid,)).fetchone())
        except Exception:  # noqa: BLE001 - tablo henüz yok
            return False


def handle_notification(payload: str, keys_for=None) -> dict:
    """Doğrulanmış Apple olayını uygula. Hata atmaz: sonucu döner (uç her durumda 200)."""
    from app import social

    try:
        c = _verify_notification(payload, keys_for)
    except social.SocialError as e:
        log.warning("apple bildirimi reddedildi: %s", e)
        return {"ok": False, "error": "invalid"}
    ev = c.get("events")
    try:
        ev = json.loads(ev) if isinstance(ev, str) else (ev or {})
    except ValueError:
        ev = {}
    kind, sub = ev.get("type"), ev.get("sub")
    u = _find_user(sub, ev.get("email"))
    if not u:
        log.info("apple bildirimi: %s, kullanıcı bulunamadı", kind)
        return {"ok": True, "event": kind, "user": None}
    uid = u["id"]
    if sub and not link_of(uid):  # e-postayla bulundu: sonraki olaylar için özet yazılır
        system.put(f"apple_sub:{sub_ref(sub)}", str(uid))
        system.put(f"apple:{uid}", json.dumps({"ref": sub_ref(sub), "client_id": c.get("aud") if isinstance(c.get("aud"), str) else None}))
    res: dict = {"ok": True, "event": kind, "user": uid}
    if kind in ("email-disabled", "email-enabled"):
        system.put(f"apple_email:{uid}", "disabled" if kind == "email-disabled" else None)
    elif kind == "consent-revoked":
        forget(uid)  # Apple belgeyi zaten geçersiz kıldı: iptal gerekmez
        res["sessions"] = _drop_apple_sessions(u)
    elif kind == "account-delete":
        res.update(_apple_account_deleted(u))
    else:
        log.info("apple bildirimi: bilinmeyen olay %s", kind)
        res["ignored"] = True
    log.info("apple bildirimi: %s kullanıcı %s → %s", kind, uid, {k: v for k, v in res.items() if k not in ("ok", "user")})
    return res


def _apple_account_deleted(u: dict) -> dict:
    from app import auth, deletion

    uid = u["id"]
    forget(uid)
    system.put(f"apple_email:{uid}", None)
    sessions_dropped = _drop_apple_sessions(u)
    if u.get("deleted_at"):
        return {"action": "already_pending", "sessions": sessions_dropped}
    unreachable = auth.password_unset(uid) and (u["username"] or "").lower().endswith(RELAY_DOMAIN)
    owner_with_members = not u.get("member_of") and any(not deletion.pending(m) for m in system.members(uid))
    if u["role"] == "admin" or owner_with_members or not unreachable:
        return {"action": "unlinked", "sessions": sessions_dropped}
    out = deletion.request_delete(u, reason="apple_account_deleted")
    return {"action": "soft_delete", "delete_after": out["delete_after"], "sessions": sessions_dropped}
