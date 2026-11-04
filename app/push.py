"""Telefona push bildirimi (hazır sürüm, çok kullanıcılı).

Gönderim
- Android: Firebase Cloud Messaging HTTP v1. Kimlik: servis hesabı JSON dosyası (HESAPKITAP_FCM_CREDENTIALS = dosya
  yolu). Erişim belirteci: servis hesabının anahtarıyla RS256 imzalı JWT → https://oauth2.googleapis.com/token
  (jwt-bearer); ~55 dk önbellekte.
- iOS: doğrudan APNs (Firebase yok), HTTP/2 (httpx[http2]). Token tabanlı kimlik: .p8 anahtarıyla ES256 JWT
  (HESAPKITAP_APNS_KEY_PATH, HESAPKITAP_APNS_KEY_ID, HESAPKITAP_APNS_TEAM_ID; konu HESAPKITAP_APNS_TOPIC), ~50 dk
  önbellekte. Cihaz kaydındaki env: production (TestFlight, App Store) → api.push.apple.com; sandbox (Xcode) →
  api.sandbox.push.apple.com.
- Anahtar yoksa (kendi sunucusu, testler) push sessizce kapalı; Telegram hatırlatmaları (app/jobs.py) bundan bağımsız.
- Geçersiz cihaz (FCM UNREGISTERED / 404, APNs 410 / BadDeviceToken / Unregistered): kayıt silinir.

Yük: {title, body, route ("#/…" uygulama içi yol), kind (tür), id (tekil anahtar)}. Android'de notification + data,
kanal "hatirlatma", tag = id; iOS'ta aps.alert, thread-id = kind, apns-collapse-id = id: aynı anahtar ikinci kez
gelirse eskisinin üstüne yazar.

Zamanlama
- Günlük iş (app/jobs.scheduler her dakika tick): Türkiye saatiyle 09:00'dan sonra cihazı olan her kullanıcı için bir
  kez; zamanlı türler kullanıcının defterinde (ortak defter üyesinde sahibin defteri, üyenin gizleme kurallarıyla)
  hesaplanır. Aynı gün birden çok bildirim varsa günlük özet (digest) tek bildirim olur; özet kapalıysa en çok 3 ayrı
  bildirim, fazlası özet.
- Olaylar (ekstre okununca faiz / abonelik, posta ya da Paylaş ile gelen belge): ilgili akışa tek satır kanca; hesap
  arka planda (istek yolu yavaşlamaz).
- Sessiz saatler (varsayılan 22:00–09:00): kuyruktaki bildirim sessiz saat bitince gider.
- Tekrar yok: aynı (kullanıcı, id) bir kez (push_sent; 60 gün sonra temizlenir).

Saklama: sistem veritabanı (push_devices, push_prefs, push_sent, push_queue; app/system.py şeması).
"""
import base64
import calendar
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db, system

log = logging.getLogger("hesapkitap.push")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Istanbul"))

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
APNS_HOSTS = {"production": "api.push.apple.com", "sandbox": "api.sandbox.push.apple.com"}
DEFAULT_TEAM_ID = "D78HMKA7Z8"
DEFAULT_TOPIC = "com.alameddinc.hesapkitap"
ANDROID_CHANNEL = "hatirlatma"

HTTP_CLIENT = None   # FCM + Google OAuth; testler httpx.MockTransport'lu istemci koyar
APNS_CLIENT = None   # APNs (HTTP/2); testler sahte istemci koyar

FCM_TOKEN_TTL = 55 * 60
APNS_JWT_TTL = 50 * 60
MAX_DEVICES = 10        # kullanıcı başı cihaz
SENT_KEEP_DAYS = 60     # gönderilenler tablosu
DAILY_HOUR = 9          # günlük iş bu saatten sonra (Türkiye saati)
MAX_SEPARATE = 3        # özet kapalıyken en çok bu kadar ayrı bildirim; fazlası tek özet
MAX_TRIES = 3           # ağ hatasında yeniden deneme (kuyrukta)
EVENT_DELAY = 20.0      # ekstre kaydı sonrası olay hesabı (toplu yüklemede tek hesap)
TEST_EVERY = 60         # POST /api/push/test: dakikada bir
EKSTRE_FRESH = 7        # kesimden bu kadar gün geçtiyse "ekstre yükle" artık bildirim olmaz (Yapılacaklar'da durur)
DOKUM_FRESH = 10
GELIR_LATE = 2          # beklenen gelir gününden bu kadar gün sonra

# tür → (Türkçe ad, varsayılan)
TYPES = {
    "odeme": ("Kart son ödeme", True),
    "fatura": ("Fatura son ödeme", True),
    "ekstre": ("Ekstre yükleme hatırlatması", True),
    "dokum": ("Hesap dökümü hatırlatması", True),
    "butce": ("Bütçe uyarısı", True),
    "faiz": ("Faiz ve vade farkı", True),
    "gelir": ("Beklenen gelir yatmadı", True),
    "abonelik": ("Abonelik zammı ve iptal sonrası çekim", True),
    "ozet": ("Dönem özeti", True),
    "belge": ("Gelen belge işlendi", True),
    "ortak": ("Ortak defterde yeni kayıt", False),
    "zekat": ("Zekât ve mübarek günler", True),
}
# günlük özette sayılan ad
NOUNS = {"odeme": "ödeme", "fatura": "fatura", "ekstre": "ekstre", "dokum": "döküm", "butce": "bütçe uyarısı",
         "faiz": "faiz uyarısı", "gelir": "gelir uyarısı", "abonelik": "abonelik uyarısı", "ozet": "dönem özeti",
         "belge": "belge", "ortak": "ortak defter kaydı", "zekat": "zekât hatırlatması"}
DEFAULT_QUIET = {"from": "22:00", "to": "09:00"}
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9:_\-.]{16,4096}$")
AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

# mobil uygulamanın rotaları (hesapkitap-mobile src/App.tsx)
R_OZET, R_ODEMELER, R_EKSTRELER, R_BUTCELER = "#/ozet", "#/diger/odemeler", "#/diger/ekstreler", "#/diger/butceler"
R_FAIZ, R_ISLEMLER, R_TASARRUF, R_ZEKAT = "#/diger/faiz", "#/islemler", "#/diger/tasarruf", "#/diger/zekat"
R_GELENLER, R_POSTA = "#/diger/gelenler", "#/diger/posta"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _now() -> datetime:
    return datetime.now(TZ)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


# ---------------------------------------------------------------- yapılandırma

_cache: dict = {}  # anahtar dosyaları (yol + değişme zamanı), erişim belirteci, APNs JWT
_cache_lock = threading.Lock()


def _read_file(path: str) -> tuple[float, str] | None:
    try:
        st = os.stat(path)
        with open(path, encoding="utf-8") as f:
            return st.st_mtime, f.read()
    except OSError:
        return None


def _fcm_creds() -> dict | None:
    """Servis hesabı: {project_id, client_email, private_key (RSA), token_uri}. Yoksa / bozuksa None."""
    path = _env("HESAPKITAP_FCM_CREDENTIALS")
    if not path:
        return None
    got = _read_file(path)
    if not got:
        return None
    key = ("fcm_creds", path, got[0])
    if key in _cache:
        return _cache[key]
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    out = None
    try:
        data = json.loads(got[1])
        pk = load_pem_private_key(data["private_key"].encode(), password=None)
        if isinstance(pk, rsa.RSAPrivateKey) and data.get("project_id") and data.get("client_email"):
            out = {"project_id": data["project_id"], "client_email": data["client_email"], "key": pk,
                   "kid": data.get("private_key_id"), "token_uri": data.get("token_uri") or GOOGLE_TOKEN_URL}
    except Exception:  # noqa: BLE001 - bozuk dosya: Android push kapalı sayılır
        log.warning("push: FCM servis hesabı dosyası okunamadı")
    _cache[key] = out
    return out


def _apns_key():
    path = _env("HESAPKITAP_APNS_KEY_PATH")
    if not path or not _env("HESAPKITAP_APNS_KEY_ID"):
        return None
    got = _read_file(path)
    if not got:
        return None
    key = ("apns_key", path, got[0])
    if key in _cache:
        return _cache[key]
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    out = None
    try:
        pk = load_pem_private_key(got[1].strip().encode(), password=None)
        out = pk if isinstance(pk, ec.EllipticCurvePrivateKey) else None
    except Exception:  # noqa: BLE001
        log.warning("push: APNs anahtarı okunamadı (.p8 bekleniyor)")
    _cache[key] = out
    return out


def fcm_enabled() -> bool:
    return _fcm_creds() is not None


def apns_enabled() -> bool:
    return _apns_key() is not None


def enabled() -> bool:
    """Sunucuda push yapılandırılmış mı (en az bir platform)? Ayar yoksa dosyaya bile bakılmaz."""
    if not (_env("HESAPKITAP_FCM_CREDENTIALS") or _env("HESAPKITAP_APNS_KEY_PATH")):
        return False
    return fcm_enabled() or apns_enabled()


def _platform_on(platform: str) -> bool:
    return fcm_enabled() if platform == "android" else apns_enabled() if platform == "ios" else False


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------- JWT

def _jwt(head: dict, body: dict, sign) -> str:
    signing = f"{_b64e(json.dumps(head, separators=(',', ':')).encode())}.{_b64e(json.dumps(body, separators=(',', ':')).encode())}"
    return f"{signing}.{_b64e(sign(signing.encode()))}"


def fcm_assertion(creds: dict, now: float | None = None) -> str:
    """Google OAuth jwt-bearer onayı: RS256, servis hesabıyla imzalı, 1 saat geçerli."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    iat = int(now or time.time())
    head = {"alg": "RS256", "typ": "JWT", **({"kid": creds["kid"]} if creds.get("kid") else {})}
    body = {"iss": creds["client_email"], "scope": FCM_SCOPE, "aud": creds["token_uri"], "iat": iat, "exp": iat + 3600}
    return _jwt(head, body, lambda m: creds["key"].sign(m, padding.PKCS1v15(), hashes.SHA256()))


def apns_jwt(now: float | None = None, fresh: bool = False) -> str:
    """APNs sağlayıcı belgesi: ES256 (JOSE r||s), iss = Team ID, kid = Key ID; ~50 dk aynı belge kullanılır
    (Apple 20 dakikadan sık yenilemeyi reddeder, 60 dakikadan eskisini kabul etmez)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    t = now or time.time()
    with _cache_lock:
        c = _cache.get("apns_jwt")
        if c and not fresh and t - c[0] < APNS_JWT_TTL:
            return c[1]
    key = _apns_key()
    if key is None:
        raise RuntimeError("APNs anahtarı yok")

    def sign(m: bytes) -> bytes:
        r, s = decode_dss_signature(key.sign(m, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    tok = _jwt({"alg": "ES256", "kid": _env("HESAPKITAP_APNS_KEY_ID")},
               {"iss": _env("HESAPKITAP_APNS_TEAM_ID", DEFAULT_TEAM_ID), "iat": int(t)}, sign)
    with _cache_lock:
        _cache["apns_jwt"] = (t, tok)
    return tok


def _http():
    global HTTP_CLIENT
    if HTTP_CLIENT is None:
        import httpx

        HTTP_CLIENT = httpx.Client(timeout=10)
    return HTTP_CLIENT


def _apns_http():
    global APNS_CLIENT
    if APNS_CLIENT is None:  # APNs yalnız HTTP/2 konuşur; bağlantı açık tutulur (Apple önerisi)
        import httpx

        APNS_CLIENT = httpx.Client(http2=True, timeout=10)
    return APNS_CLIENT


def fcm_access_token(fresh: bool = False) -> str:
    creds = _fcm_creds()
    if not creds:
        raise RuntimeError("FCM servis hesabı yok")
    with _cache_lock:
        c = _cache.get("fcm_token")
        if c and not fresh and time.time() < c[0]:
            return c[1]
    r = _http().post(creds["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                               "assertion": fcm_assertion(creds)})
    if r.status_code != 200:
        raise RuntimeError(f"FCM erişim belirteci {r.status_code}")
    data = r.json()
    ttl = min(FCM_TOKEN_TTL, max(60, int(data.get("expires_in") or 3600) - 300))
    with _cache_lock:
        _cache["fcm_token"] = (time.time() + ttl, data["access_token"])
    return data["access_token"]


# ---------------------------------------------------------------- gönderim

def collapse_id(pid: str) -> str:
    """apns-collapse-id en çok 64 bayt; uzun kimlik özetlenir (Android tag'i de aynı değer)."""
    return pid if len(pid.encode()) <= 64 else "h:" + hashlib.sha256(pid.encode()).hexdigest()[:40]


def fcm_message(token: str, p: dict) -> dict:
    cid = collapse_id(p["id"])
    return {"message": {
        "token": token,
        "notification": {"title": p["title"], "body": p["body"]},
        "data": {"route": p.get("route") or R_OZET, "kind": p["kind"], "id": p["id"]},
        "android": {"priority": "HIGH", "notification": {"channel_id": ANDROID_CHANNEL, "tag": cid}},
    }}


def apns_payload(p: dict) -> dict:
    return {"aps": {"alert": {"title": p["title"], "body": p["body"]}, "sound": "default", "thread-id": p["kind"]},
            "route": p.get("route") or R_OZET, "kind": p["kind"], "id": p["id"]}


def send_fcm(token: str, p: dict) -> str:
    """"ok" | "invalid" (cihaz kaydı silinmeli) | "error" (geçici)."""
    creds = _fcm_creds()
    if not creds:
        return "error"
    url = f"https://fcm.googleapis.com/v1/projects/{creds['project_id']}/messages:send"
    for attempt in (0, 1):
        try:
            r = _http().post(url, json=fcm_message(token, p),
                             headers={"authorization": f"Bearer {fcm_access_token(fresh=bool(attempt))}"})
        except Exception:  # noqa: BLE001 - ağ hatası: kuyrukta yeniden denenir
            log.warning("push: FCM isteği başarısız", exc_info=True)
            return "error"
        if r.status_code == 200:
            return "ok"
        if r.status_code == 401 and not attempt:  # belge iptal / süresi doldu: yenisiyle bir kez daha
            continue
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        codes = {d.get("errorCode") for d in err.get("details") or [] if isinstance(d, dict)}
        if r.status_code == 404 or "UNREGISTERED" in codes:
            return "invalid"
        log.warning("push: FCM %s %s", r.status_code, err.get("status"))
        return "error"
    return "error"


def send_apns(token: str, env: str | None, p: dict) -> str:
    host = APNS_HOSTS.get(env or "production", APNS_HOSTS["production"])
    body = json.dumps(apns_payload(p), ensure_ascii=False, separators=(",", ":")).encode()
    for attempt in (0, 1):
        try:
            headers = {"authorization": f"bearer {apns_jwt(fresh=bool(attempt))}",
                       "apns-topic": _env("HESAPKITAP_APNS_TOPIC", DEFAULT_TOPIC), "apns-push-type": "alert",
                       "apns-priority": "10", "apns-collapse-id": collapse_id(p["id"]),
                       "apns-expiration": str(int(time.time()) + 86400), "content-type": "application/json"}
            r = _apns_http().post(f"https://{host}/3/device/{token}", content=body, headers=headers)
        except Exception:  # noqa: BLE001
            log.warning("push: APNs isteği başarısız", exc_info=True)
            return "error"
        if r.status_code == 200:
            return "ok"
        try:
            reason = (r.json() or {}).get("reason")
        except ValueError:
            reason = None
        if r.status_code == 403 and reason in ("ExpiredProviderToken", "InvalidProviderToken") and not attempt:
            continue
        if r.status_code == 410 or reason in ("BadDeviceToken", "Unregistered"):
            return "invalid"
        log.warning("push: APNs %s %s", r.status_code, reason)
        return "error"
    return "error"


def deliver(uid: int, p: dict) -> dict:
    """Kullanıcının bütün cihazlarına gönder. Geçersiz cihazlar silinir. {sent, removed, failed}."""
    out = {"sent": 0, "removed": 0, "failed": 0}
    for d in devices(uid):
        if not _platform_on(d["platform"]):
            continue
        res = send_fcm(d["token"], p) if d["platform"] == "android" else send_apns(d["token"], d["env"], p)
        if res == "ok":
            out["sent"] += 1
        elif res == "invalid":
            with system.connect() as c:
                c.execute("DELETE FROM push_devices WHERE token = ?", (d["token"],))
            out["removed"] += 1
        else:
            out["failed"] += 1
    return out


# ---------------------------------------------------------------- cihazlar

def valid_token(token: str) -> bool:
    return bool(TOKEN_RE.match(token or ""))


def register(uid: int, token: str, platform: str, env: str | None = None, app_version: str | None = None) -> None:
    """Cihazı kaydet. Aynı belge başka kullanıcıdaysa (telefonda hesap değişti) bu kullanıcıya taşınır."""
    now = _utc_iso()
    env = env if platform == "ios" and env in APNS_HOSTS else ("production" if platform == "ios" else None)
    with system.connect() as c:
        old = c.execute("SELECT user_id FROM push_devices WHERE token = ?", (token,)).fetchone()
        if old and old["user_id"] != uid:
            c.execute("DELETE FROM push_devices WHERE token = ?", (token,))
            old = None
        if old:
            c.execute("UPDATE push_devices SET platform = ?, env = ?, app_version = ?, last_seen = ? WHERE token = ?",
                      (platform, env, app_version, now, token))
        else:
            c.execute("INSERT INTO push_devices (token, user_id, platform, env, app_version, created, last_seen) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)", (token, uid, platform, env, app_version, now, now))
        extra = [r[0] for r in c.execute("SELECT token FROM push_devices WHERE user_id = ? ORDER BY last_seen DESC, "
                                         "created DESC LIMIT -1 OFFSET ?", (uid, MAX_DEVICES))]
        for t in extra:  # sınır: en uzun süredir görünmeyen düşer
            c.execute("DELETE FROM push_devices WHERE token = ?", (t,))


def unregister(uid: int, token: str) -> bool:
    with system.connect() as c:
        return c.execute("DELETE FROM push_devices WHERE token = ? AND user_id = ?", (token, uid)).rowcount > 0


def devices(uid: int) -> list[dict]:
    with system.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM push_devices WHERE user_id = ? ORDER BY last_seen DESC", (uid,))]


def forget_devices(uid: int) -> None:
    """Hesap kapandı (silinmek üzere): cihazlar ve bekleyen bildirimler düşer; tercihler geri alma için kalır."""
    with system.connect() as c:
        c.execute("DELETE FROM push_devices WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM push_queue WHERE user_id = ?", (uid,))


# ---------------------------------------------------------------- tercihler

def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def get_prefs(uid: int) -> dict:
    with system.connect() as c:
        r = c.execute("SELECT json FROM push_prefs WHERE user_id = ?", (uid,)).fetchone()
    try:
        raw = json.loads(r[0]) if r else {}
    except ValueError:
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    types = raw.get("types") if isinstance(raw.get("types"), dict) else {}
    q = raw.get("quiet") if isinstance(raw.get("quiet"), dict) else {}
    quiet = {k: q[k] if isinstance(q.get(k), str) and HHMM.match(q[k]) else DEFAULT_QUIET[k] for k in ("from", "to")}
    return {"types": {k: bool(types.get(k, d)) for k, (_, d) in TYPES.items()}, "quiet": quiet,
            "digest": bool(raw.get("digest", True))}


def set_prefs(uid: int, data: dict) -> dict:
    """Kısmi güncelleme: yalnız gelen alanlar değişir; bilinmeyen tür yok sayılır."""
    cur = get_prefs(uid)
    if isinstance(data.get("types"), dict):
        cur["types"].update({k: bool(v) for k, v in data["types"].items() if k in TYPES})
    if isinstance(data.get("quiet"), dict):
        for k in ("from", "to"):
            v = data["quiet"].get(k)
            if v is not None:
                if not isinstance(v, str) or not HHMM.match(v):
                    raise ValueError("Sessiz saat SS:DD biçiminde olmalı")
                cur["quiet"][k] = v
    if data.get("digest") is not None:
        cur["digest"] = bool(data["digest"])
    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO push_prefs (user_id, json) VALUES (?, ?)", (uid, json.dumps(cur)))
    return cur


def prefs_out(uid: int) -> dict:
    p = get_prefs(uid)
    return {"enabled": enabled(), **p, "labels": {k: v[0] for k, v in TYPES.items()}, "devices": len(devices(uid))}


def quiet_until(prefs: dict, now: datetime) -> datetime | None:
    """Şu an sessiz saatteyse bitiş zamanı; değilse None. from == to: sessiz saat yok."""
    f, t = _hhmm(prefs["quiet"]["from"]), _hhmm(prefs["quiet"]["to"])
    if f == t:
        return None
    now = now.astimezone(TZ)
    cur = now.hour * 60 + now.minute
    inside = f <= cur < t if f < t else (cur >= f or cur < t)
    if not inside:
        return None
    end = now.replace(hour=t // 60, minute=t % 60, second=0, microsecond=0)
    return end if end > now else end + timedelta(days=1)


# ---------------------------------------------------------------- tekrar yok + kuyruk

def claim(uid: int, pid: str, today: date | None = None) -> bool:
    """Bu (kullanıcı, kimlik) ilk kez mi? İlk kezse gönderildi olarak yazılır."""
    with system.connect() as c:
        return c.execute("INSERT OR IGNORE INTO push_sent (user_id, id, at) VALUES (?, ?, ?)",
                         (uid, pid, (today or _now().date()).isoformat())).rowcount > 0


def was_sent(uid: int, pid: str) -> bool:
    with system.connect() as c:
        return bool(c.execute("SELECT 1 FROM push_sent WHERE user_id = ? AND id = ?", (uid, pid)).fetchone())


def prune(today: date) -> int:
    with system.connect() as c:
        return c.execute("DELETE FROM push_sent WHERE at < ?", ((today - timedelta(days=SENT_KEEP_DAYS)).isoformat(),)).rowcount


def enqueue(uid: int, p: dict, now: datetime | None = None, prefs: dict | None = None) -> None:
    now = now or _now()
    q = quiet_until(prefs or get_prefs(uid), now)
    with system.connect() as c:
        c.execute("INSERT INTO push_queue (user_id, payload, not_before, created) VALUES (?, ?, ?, ?)",
                  (uid, json.dumps(p, ensure_ascii=False), (q or now).timestamp(), _utc_iso()))


def queue(uid: int | None = None) -> list[dict]:
    with system.connect() as c:
        rows = c.execute("SELECT * FROM push_queue" + (" WHERE user_id = ?" if uid else "") + " ORDER BY id",
                         (uid,) if uid else ()).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]


_flush_lock = threading.Lock()


def flush(now: datetime | None = None) -> int:
    """Zamanı gelen bildirimleri gönder. Sessiz saate denk gelen ertelenir; ağ hatası MAX_TRIES kez yeniden denenir."""
    now = now or _now()
    if not enabled():
        return 0
    n = 0
    with _flush_lock:
        with system.connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM push_queue WHERE not_before <= ? ORDER BY id LIMIT 500",
                                               (now.timestamp(),))]
        for r in rows:
            q = quiet_until(get_prefs(r["user_id"]), now)  # tercih kuyruğa girdikten sonra değişmiş olabilir
            with system.connect() as c:
                if q:
                    c.execute("UPDATE push_queue SET not_before = ? WHERE id = ?", (q.timestamp(), r["id"]))
                    continue
                c.execute("DELETE FROM push_queue WHERE id = ?", (r["id"],))
            u = system.user(r["user_id"])
            if not u or u.get("disabled") or u.get("deleted_at"):
                continue
            res = deliver(r["user_id"], json.loads(r["payload"]))
            n += res["sent"]
            if res["failed"] and not res["sent"] and (r.get("tries") or 0) + 1 < MAX_TRIES:
                with system.connect() as c:
                    c.execute("INSERT INTO push_queue (user_id, payload, not_before, created, tries) VALUES (?, ?, ?, ?, ?)",
                              (r["user_id"], r["payload"], now.timestamp() + 300 * ((r.get("tries") or 0) + 1),
                               r["created"], (r.get("tries") or 0) + 1))
    return n


def flush_soon() -> None:
    """Olay bildirimi kuyruğa girdi: istek yolunu bekletmeden arka planda gönder."""
    def go():
        try:
            flush()
        except Exception:  # noqa: BLE001
            log.exception("push gönderimi")

    threading.Thread(target=go, name="push", daemon=True).start()


# ---------------------------------------------------------------- birleştirme

def item(kind: str, pid: str, title: str, body: str, route: str) -> dict:
    return {"kind": kind, "id": pid, "title": _clean(title)[:120], "body": _clean(body)[:400], "route": route}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def summary(items: list[dict], today: date, rest: bool = False) -> dict:
    counts: dict[str, int] = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    parts = ", ".join(f"{n} {NOUNS.get(k, k)}" for k, n in counts.items())
    return {"kind": "gunluk", "id": f"gunluk:{today.isoformat()}" + (":diger" if rest else ""),
            "title": "Bugünün hatırlatmaları" if not rest else "Diğer hatırlatmalar",
            "body": f"Bugün {parts}" if not rest else f"Ayrıca {parts}", "route": R_OZET}


def combine(items: list[dict], digest: bool, today: date) -> list[dict]:
    """Aynı günün bildirimleri: özet açıksa birden çoğu tek bildirim; kapalıysa en çok MAX_SEPARATE ayrı, fazlası özet."""
    if len(items) <= 1:
        return list(items)
    if digest:
        return [summary(items, today)]
    head, tail = items[:MAX_SEPARATE], items[MAX_SEPARATE:]
    return head + ([summary(tail, today, rest=True)] if tail else [])


def notify(uid: int, items: list[dict], now: datetime | None = None, daily: bool = False,
           background: bool = True) -> list[dict]:
    """Tercihe göre süz, daha önce gönderilmişleri at, birleştir, kuyruğa yaz. Dönen: kuyruğa giren bildirimler.
    daily: günlük işin bildirimleri (kişinin özet tercihi geçerli); olay bildiriminde özet yok, yalnız fazlası özet."""
    now = now or _now()
    p = get_prefs(uid)
    fresh = []
    for i in items:
        if p["types"].get(i["kind"]) and i["id"] not in {x["id"] for x in fresh} and claim(uid, i["id"], now.date()):
            fresh.append(i)
    out = combine(fresh, bool(p["digest"]) and daily, now.date())
    for x in out:
        if x["kind"] == "gunluk":
            claim(uid, x["id"], now.date())
        enqueue(uid, x, now, p)
    if out and background:
        flush_soon()
    return out


# ---------------------------------------------------------------- zamanlı türler (kullanıcının defterinde)

def _odeme_ekstre_dokum(today: date, on: dict) -> list[dict]:
    from app import reminders

    if not (on["odeme"] or on["ekstre"] or on["dokum"]):
        return []
    out = []
    for r in reminders.build(today):  # ortak defter yetkisi, gizli sayfa/kart ve kişinin kapattıkları burada süzülü
        if r["kind"] == "odeme" and on["odeme"]:
            left = (date.fromisoformat(r["due"]) - today).days
            tag = "3gun" if left == 3 else "songun" if left == 0 else None
            if tag:
                out.append(item("odeme", f"{r['id']}:{tag}", r["title"], r["text"], R_ODEMELER))
        elif r["kind"] == "ekstre" and on["ekstre"] and (today - date.fromisoformat(r["due"])).days <= EKSTRE_FRESH:
            out.append(item("ekstre", r["id"], r["title"], r["text"], R_EKSTRELER))
        elif r["kind"] == "dokum" and on["dokum"] and (today - date.fromisoformat(r["due"])).days <= DOKUM_FRESH:
            out.append(item("dokum", r["id"], r["title"], r["text"], R_EKSTRELER))
    return out


def _fatura(today: date) -> list[dict]:
    from app import money

    out = []
    for b in db.list_bills():
        if b["paid_at"] or not b["due_date"] or (b["paid_amount"] and money.ge(b["paid_amount"], b["amount"])):
            continue
        if (date.fromisoformat(b["due_date"]) - today).days == 2:
            d = date.fromisoformat(b["due_date"])
            out.append(item("fatura", f"fatura:{b['id']}:{b['due_date']}:2gun", f"{b['provider']} faturası",
                            f"Son ödeme {d.day} {AY[d.month - 1]} (2 gün kaldı) · {money.tl(b['amount'], trim=True)}",
                            R_ODEMELER))
    return out


def _butce(today: date) -> list[dict]:
    from app import finance, money

    month = finance.current_period(today)
    out = []
    for b in finance.budgets(month):
        for th in (100, 80):
            if b["pct"] >= th:
                slug = finance._slug(b["category"])  # Özet uyarısının anahtarıyla aynı
                title = f"{b['category']} bütçesi aşıldı" if th == 100 else f"{b['category']} bütçesinin %{b['pct']:.0f} kadarı harcandı"
                out.append(item("butce", f"butce:{month}:{slug}:{th}", title,
                                f"{money.tl(b['spent'], trim=True)} / {money.tl(b['limit'], trim=True)}", R_BUTCELER))
                break
    return out


def _gelir(today: date) -> list[dict]:
    """Düzenli gelir (maaş) günü GELIR_LATE gün geçti, son haftada yatmış görünmüyor. Yalnız önceki aylarda dökümde
    görünen gelir için (döküm yüklemeyen kişiye her ay boşuna uyarı gitmesin)."""
    from app import finance, money

    m = today.strftime("%Y-%m")
    recs = [r for r in db.list_recurring() if r["kind"] == "gelir" and r["start_month"] <= m
            and (not r["end_month"] or r["end_month"] >= m)]
    if not recs:
        return []
    entries = [e for e in db.list_entries() if e["kind"] == "gelir" and not e["skipped"]]
    out = []
    for r in recs:
        exp = today.replace(day=min(max(1, r["day_of_month"] or 1), calendar.monthrange(today.year, today.month)[1]))
        if (today - exp).days != GELIR_LATE:
            continue
        salary = finance._is_salary(r)
        mine = [date.fromisoformat(e["entry_date"]) for e in entries
                if e.get("recurring_id") == r["id"] or (salary and finance._salary_entry(e, {r["id"]}))]
        lo = exp - timedelta(days=7)
        if any(lo <= d <= today for d in mine) or not any(exp - timedelta(days=70) <= d < lo for d in mine):
            continue
        out.append(item("gelir", f"gelir:{r['id']}:{m}", f"{r['description']} yatmadı görünüyor",
                        f"Beklenen gün {exp.day} {AY[exp.month - 1]}'di ({money.tl(r['amount'], trim=True)}). Yattıysa "
                        "hesap dökümünü yükle; yatmadıysa işverenine sor.", R_ISLEMLER))
    return out


def _ozet(today: date) -> list[dict]:
    from app import finance, money

    if finance.current_period(today) == finance.current_period(today - timedelta(days=1)):
        return []
    prev = finance.add_months(finance.current_period(today), -1)
    t = finance.summary(prev)["totals"]
    y, mo = map(int, prev.split("-"))
    return [item("ozet", f"ozet:{prev}", f"{AY[mo - 1]} {y} dönemi bitti",
                 f"Gelir {money.tl(t['income'], trim=True)} · Gider {money.tl(t['expense'], trim=True)} · "
                 f"Net {money.tl(t['net'], trim=True)}", R_OZET)]


def _ortak(u: dict, today: date) -> list[dict]:
    """Ortak defterde son 24 saatte başka biri kayıt eklediyse tek bildirim (üyenin göremediği kayıt sayılmaz)."""
    if not (u.get("member_of") or system.members(u["id"])):
        return []
    cut = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    n = sum(1 for e in db.list_entries() if e.get("created_by") and e["created_by"] != u["id"]
            and str(e.get("created_at") or "") >= cut)
    if not n:
        return []
    return [item("ortak", f"ortak:{today.isoformat()}", "Ortak defterde yeni kayıt",
                 f"Dünden beri {n} yeni kayıt eklendi", R_ISLEMLER)]


def _zekat(today: date) -> list[dict]:
    from app import book, prefs, zakat

    r = book.active()
    if not prefs.enabled("zekat") or (r and "zekat" in r["hidden_pages"]):
        return []
    out = []
    for msg in zakat.reminders(today) + zakat.holy_reminders(today):
        m = re.search(r"<b>(.*?)</b>", msg)
        title = _clean(m[1]) if m else "Zekât"
        body = _clean(msg.replace(m[0], "", 1) if m else msg).lstrip("🌙🐑 ·").strip() or title
        out.append(item("zekat", f"zekat:{today.isoformat()}:{hashlib.sha256(msg.encode()).hexdigest()[:12]}",
                        title, body, R_ZEKAT))
    return out


def collect(u: dict, today: date, prefs: dict | None = None) -> list[dict]:
    """Bugünün zamanlı bildirimleri (açık türler), kullanıcının defterinde ve görebildiği kadarıyla. Bağlam: use_space."""
    on = (prefs or get_prefs(u["id"]))["types"]
    out: list[dict] = []
    for kind, fn in (("odeme", lambda: _odeme_ekstre_dokum(today, on)), ("fatura", lambda: _fatura(today)),
                     ("butce", lambda: _butce(today)), ("gelir", lambda: _gelir(today)), ("ozet", lambda: _ozet(today)),
                     ("ortak", lambda: _ortak(u, today)), ("zekat", lambda: _zekat(today))):
        if kind != "odeme" and not on.get(kind):
            continue
        try:
            out += fn()
        except Exception:  # noqa: BLE001 - bir tür patlarsa diğerleri sürsün
            log.exception("push türü %s (u%s)", kind, u.get("id"))
    return out


def _space_of(u: dict) -> str | None:
    return system.user_db_path(u)


def run_daily(now: datetime) -> dict:
    """Günde bir kez (DAILY_HOUR'dan sonra), cihazı olan her kullanıcı için zamanlı bildirimler."""
    now = now.astimezone(TZ)
    today = now.date()
    if now.hour < DAILY_HOUR:
        return {}
    with system.connect() as c:
        uids = [r[0] for r in c.execute("SELECT DISTINCT user_id FROM push_devices ORDER BY user_id")]
    done = {}
    for uid in uids:
        if system.get(f"push:daily:{uid}") == today.isoformat():
            continue
        system.put(f"push:daily:{uid}", today.isoformat())
        u = system.user(uid)
        if not u or u.get("disabled") or u.get("deleted_at"):
            continue
        if not any(_platform_on(d["platform"]) for d in devices(uid)):
            continue
        try:
            p = get_prefs(uid)
            with db.use_space(_space_of(u), u):
                items = collect(u, today, p)
            done[uid] = len(notify(uid, items, now, daily=True, background=False))
        except Exception:  # noqa: BLE001 - bir kullanıcı diğerlerini engellemesin
            log.exception("push günlük u%s", uid)
    if system.get("push:prune") != today.isoformat():
        system.put("push:prune", today.isoformat())
        prune(today)
    return done


def tick(now: datetime) -> None:
    """Zamanlayıcıdan her dakika (app/jobs.scheduler)."""
    if not enabled():
        return
    run_daily(now)
    flush(now)


# ---------------------------------------------------------------- olaylar

def _has_devices(uid: int) -> bool:
    with system.connect() as c:
        return bool(c.execute("SELECT 1 FROM push_devices WHERE user_id = ? LIMIT 1", (uid,)).fetchone())


def _ctx() -> tuple[dict, str | None] | None:
    """Açık defterin kullanıcısı ve dosyası (arka plan iş parçacığına taşınır). Push kapalıysa ya da cihaz yoksa None."""
    u = db.current_user()
    if not u or not u.get("id") or not enabled() or not _has_devices(u["id"]):
        return None
    path = db.db_path()
    return u, (None if os.path.abspath(path) == os.path.abspath(db.DB_PATH) else path)


def statement_items(today: date) -> list[dict]:
    """Ekstre okununca: faiz / vade farkı ve abonelik (iptal sonrası çekim, paket düşmemiş, çift çekim, zam)."""
    from app import book, finance, insight_state, money

    out = []
    cur = finance.current_period(today)
    seen = set()
    for month in (cur, finance.add_months(cur, -1)):
        for i in insight_state.visible(finance.insights(month, today)):
            k = i.get("key") or ""
            if k in seen:
                continue
            seen.add(k)
            if (k.startswith("faiz:") or k.startswith("vade_farki:")) and not book.hide_fees():
                out.append(item("faiz", k, i["title"], i.get("detail") or "", R_FAIZ))
            elif k.split(":")[0] in ("iptal_cekim", "paket", "dup"):
                out.append(item("abonelik", k, i["title"], i.get("detail") or "", R_TASARRUF))
    recent = (today - timedelta(days=45)).isoformat()
    for s in finance.subscriptions():
        prev = s.get("prev_monthly")
        if s["status"] == "aktif" and prev and s["last_seen"] >= recent and money.gt(s["monthly"], prev * 1.05):
            slug = finance._slug(s["merchant"])
            out.append(item("abonelik", f"abonelik:zam:{slug}:{s['last_period']}", f"{s['merchant'].title()} zamlandı",
                            f"{money.tl(prev, trim=True)} → {money.tl(s['monthly'], trim=True)}. Kullanmıyorsan "
                            "Tasarruf'tan iptal edildi diye işaretle.", R_TASARRUF))
    return out


_pending: dict[str, threading.Timer] = {}
_pending_lock = threading.Lock()
_Timer = threading.Timer  # testler hemen çalışan sahtesini koyar


def after_statement() -> None:
    """db.save_statement kancası: EVENT_DELAY sonra o defterde olay bildirimleri (toplu yüklemede tek hesap).
    Hiçbir zaman hata fırlatmaz; push kapalıysa yalnız ortam değişkenine bakar."""
    try:
        ctx = _ctx()
        if not ctx:
            return
        u, path = ctx

        def go():
            with _pending_lock:
                _pending.pop(f"{u['id']}:{path}", None)
            try:
                with db.use_space(path, u):
                    items = statement_items(_now().date())
                notify(u["id"], items)
            except Exception:  # noqa: BLE001 - arka plan
                log.exception("push ekstre olayı u%s", u.get("id"))

        key = f"{u['id']}:{path}"
        with _pending_lock:
            if old := _pending.pop(key, None):
                old.cancel()
            t = _Timer(EVENT_DELAY, go)
            t.daemon = True
            _pending[key] = t
        t.start()
    except Exception:  # noqa: BLE001 - kanca kaydı engellemesin
        log.exception("push ekstre kancası")


def after_document(source: str, text: str) -> None:
    """Posta (source "posta") ya da Paylaş ("paylas") ile gelen belge işlendi. Hiçbir zaman hata fırlatmaz."""
    try:
        ctx = _ctx()
        if not ctx or not (text or "").strip():
            return
        u = ctx[0]
        lines = [x for x in (text or "").splitlines() if x.strip()]
        title = "Postayla gelen belge işlendi" if source == "posta" else "Paylaştığın belge işlendi"
        body = lines[0] + (f" (+{len(lines) - 1} belge daha)" if len(lines) > 1 else "")
        pid = f"belge:{source}:{hashlib.sha256((text + _utc_iso()).encode()).hexdigest()[:16]}"
        notify(u["id"], [item("belge", pid, title, body, R_POSTA if source == "posta" else R_GELENLER)])
    except Exception:  # noqa: BLE001
        log.exception("push belge kancası")


# ---------------------------------------------------------------- test bildirimi

def send_test(uid: int) -> dict:
    """Sessiz saat ve tercihlerden bağımsız, hemen. Dakikada bir (çağıran denetler: test_allowed)."""
    system.put(f"push:test:{uid}", str(time.time()))
    return deliver(uid, {"kind": "test", "id": f"test:{int(time.time())}", "title": "Bildirimler açık",
                         "body": "HesapKitap hatırlatmaları bu telefona gelecek.", "route": "#/diger/ayarlar"})


def test_allowed(uid: int) -> bool:
    v = system.get(f"push:test:{uid}")
    try:
        return not v or time.time() - float(v) >= TEST_EVERY
    except ValueError:
        return True
