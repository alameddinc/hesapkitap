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
- Ortak alışveriş listesi ("liste"): biri listeye ürün ekleyince defterin DİĞER kişilerine toplu tek bildirim ("Ayşe
  listeye 3 ürün ekledi: süt, yumurta, elma"); ekleyen başına en çok LIST_EVERY'de bir (ilk eklemeden LIST_DELAY sonra;
  arada eklenenler aynı bildirime girer, aralık dolmadıysa bir sonraki pencereye kalır). "Alışverişi tamamla" da
  diğerlerine gider ("Mehmet alışverişi tamamladı · 7 ürün alındı"). Yapan kişiye hiçbir zaman gitmez; listeyi gizlenen
  üyeye gitmez.
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
KUMBARA_DAYS = 7        # kumbaranın hedef tarihine bu kadar gün kala (hedefe ulaşılmadıysa)
KART_DAYS = (30, 7)     # ön ödemeli kartın (yemek / hediye) son kullanmasına bu kadar gün kala

# tür → (Türkçe ad, varsayılan)
TYPES = {
    "odeme": ("Kart son ödeme", True),
    "fatura": ("Fatura son ödeme", True),
    "ekstre": ("Ekstre yükleme hatırlatması", True),
    "dokum": ("Hesap dökümü hatırlatması", True),
    "butce": ("Bütçe uyarısı", True),
    "faiz": ("Faiz ve vade farkı", True),
    "gelir": ("Beklenen gelir yatmadı", True),
    # mağaza aboneliğinin yenilenmesine 3 gün kala (app/store_subs.reminders), zam, iptal sonrası çekim
    "abonelik": ("Abonelik yenileme, zam ve iptal sonrası çekim", True),
    "ozet": ("Dönem özeti", True),
    "belge": ("Gelen belge işlendi", True),
    "ortak": ("Ortak defterde yeni kayıt", False),
    "zekat": ("Zekât ve mübarek günler", True),
    "aile": ("Aile koltukları", True),
    "geri_bildirim": ("Kullanıcı geri bildirimi", True),  # yalnız yöneticiye (app/feedback.py)
    # destek yazışmasında yöneticinin yanıtı ve "Düzeltildi — tekrar dene" (app/support.py); metinde kişisel veri yok
    "destek": ("Destek yanıtları", True),
    "fiyat": ("Fiyat artışı", True),  # sık aldığın üründe topluluk fiyatı arttı (app/price_alerts.py), günde en çok bir
    "sistem": ("Sistem uyarısı", True),  # yalnız yöneticiye: fiyat toplama işi aksadı (POST /api/ops/alert)
    "liste": ("Alışveriş listesi", True),  # ortak listeye başkası ürün ekledi / alışverişi tamamladı (after_list_add)
    # borçta ve alacakta söz verilen ya da kesin gün ve ödeme planındaki taksit günü (2 gün kala ve o gün). Ad ve anahtar
    # aynı kalır (kişinin tercihi korunur); alacak hatırlatması nötr: "Ahmet: 2. taksit günü bugün"
    "borc": ("Borç ödeme günü", True),
    "duyuru": ("Duyurular ve yeni sürümler", True),  # yöneticinin duyurusu (app/announce.py)
    "kumbara": ("Kumbara hedef tarihi", True),  # hedef tarihine 7 gün kala, hedefin gerisindeyse (_kumbara)
    "kart_suresi": ("Yemek / hediye kartı son kullanma", True),  # son kullanmaya 30 ve 7 gün kala, bakiye varsa
    # "Hayırlı Cumalar": seçilen ritimde (Her Cuma / ayın ilk Cuması) o dönemde sadaka kaydı yoksa; seçim yapılmamışsa
    # ilk Cuma ve (kayıt olmazsa) sonraki ayın ilk Cuması, en çok 2 kez (app/sadaka_reminder.py)
    "sadaka": ("Sadaka hatırlatması", True),
    # garanti arşivindeki ürünün garantisi bitmeden: 30 gün kala ve 7 gün kala birer kez (app/warranty.push_stage)
    "garanti": ("Garanti bitişi", True),
}
ADMIN_TYPES = {"geri_bildirim", "sistem"}  # tercih listesinde yalnız yöneticiye görünür
# günlük özette sayılan ad
NOUNS = {"odeme": "ödeme", "fatura": "fatura", "ekstre": "ekstre", "dokum": "döküm", "butce": "bütçe uyarısı",
         "faiz": "faiz uyarısı", "gelir": "gelir uyarısı", "abonelik": "abonelik uyarısı", "ozet": "dönem özeti",
         "belge": "belge", "ortak": "ortak defter kaydı", "zekat": "zekât hatırlatması", "aile": "aile bildirimi",
         "geri_bildirim": "geri bildirim", "fiyat": "fiyat artışı", "sistem": "sistem uyarısı",
         "liste": "alışveriş listesi bildirimi", "borc": "borç ödeme günü", "duyuru": "duyuru",
         "kumbara": "kumbara hatırlatması", "kart_suresi": "kart son kullanma", "sadaka": "sadaka hatırlatması",
         "garanti": "garanti hatırlatması", "destek": "destek yanıtı"}
DEFAULT_QUIET = {"from": "22:00", "to": "09:00"}
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9:_\-.]{16,4096}$")
AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

# mobil uygulamanın rotaları (hesapkitap-mobile src/App.tsx)
R_OZET, R_ODEMELER, R_EKSTRELER, R_BUTCELER = "#/ozet", "#/diger/odemeler", "#/diger/ekstreler", "#/diger/butceler"
R_FAIZ, R_ISLEMLER, R_TASARRUF, R_ZEKAT = "#/diger/faiz", "#/islemler", "#/diger/tasarruf", "#/diger/zekat"
R_GELENLER, R_POSTA, R_ORTAK = "#/diger/gelenler", "#/diger/posta", "#/diger/ortak-defter"
R_FIYATLAR, R_ALISVERIS = "#/diger/fiyatlar", "#/diger/alisveris"
R_BORC_PLANI = "#/diger/borc-plani"
R_BORCLAR = "#/diger/borclar"  # /kayit/<id> ile o kayıt açık (alacak hatırlatması)
R_SADAKA = "#/diger/zekat/sadaka"  # Zekât ve Sadaka, sadaka formu açık (eski uygulama: Zekât ve Sadaka ana sayfası)
R_KUMBARA, R_YEMEK = "#/diger/kumbara", "#/diger/yemek-kartlari"  # kumbara: /<id> ile o kumbaranın sayfası
R_GARANTI = "#/diger/garantiler"  # /<id> ile ürünün ayrıntısı
GARANTI_MIN_APP = (1, 0, 7)  # Garantiler ekranı bu sürümde; eski uygulamada dokununca "Yakında" açılırdı
R_AYARLAR, R_SISTEM = "#/diger/ayarlar", "#/diger/ayarlar/sistem"  # sistem: yöneticiye "Sistem durumu" sayfası
# Destek yazışması (kullanıcı) ve yöneticinin destek gelen kutusu; app/support.DESTEK_MIN_APP'ten eski uygulamada R_AYARLAR
R_DESTEK, R_DESTEK_ADMIN = "#/diger/ayarlar/destek", "#/diger/ayarlar/destek-gelen"


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
    """apns-collapse-id en çok 64 bayt ve HTTP başlığı olduğu için yalnız ASCII: uzun ya da ASCII dışı karakterli kimlik
    ("sistem:…:İstanbul", ürün / kişi adı) özetlenir (Android tag'i de aynı değer). 07.11.2026: "İ" başlığı bozuyordu,
    APNs isteği gönderilemiyordu."""
    return pid if pid.isascii() and len(pid) <= 64 else "h:" + hashlib.sha256(pid.encode()).hexdigest()[:40]


def fcm_message(token: str, p: dict) -> dict:
    cid = collapse_id(p["id"])
    return {"message": {
        "token": token,
        "notification": {"title": p["title"], "body": p["body"]},
        "data": {"route": p.get("route") or R_OZET, "kind": p["kind"], "id": p["id"],
                 **({"action": p["action"]} if p.get("action") else {})},
        "android": {"priority": "HIGH", "notification": {"channel_id": ANDROID_CHANNEL, "tag": cid}},
    }}


def apns_payload(p: dict) -> dict:
    return {"aps": {"alert": {"title": p["title"], "body": p["body"]}, "sound": "default", "thread-id": p["kind"]},
            "route": p.get("route") or R_OZET, "kind": p["kind"], "id": p["id"],
            **({"action": p["action"]} if p.get("action") else {})}


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
    """Kullanıcının bütün cihazlarına gönder. Geçersiz cihazlar silinir. {sent, removed, failed}.
    p["only"] {platform, below} (duyuru): yalnız eşleşen cihazlar (app/announce.device_matches)."""
    out = {"sent": 0, "removed": 0, "failed": 0}
    only = p.get("only") if isinstance(p.get("only"), dict) else None
    if only:
        from app import announce
    for d in devices(uid):
        if not _platform_on(d["platform"]):
            continue
        if only and not announce.device_matches(d, only.get("platform") or "all", only.get("below")):
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
            # sürüm her kayıtta güncellenir (uygulama güncellenince); gönderilmediyse eskisi kalır
            c.execute("UPDATE push_devices SET platform = ?, env = ?, app_version = COALESCE(?, app_version), last_seen = ? "
                      "WHERE token = ?",
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
    admin = (system.user(uid) or {}).get("role") == "admin"
    keep = {k for k in TYPES if admin or k not in ADMIN_TYPES}
    p["types"] = {k: v for k, v in p["types"].items() if k in keep}
    return {"enabled": enabled(), **p, "labels": {k: v[0] for k, v in TYPES.items() if k in keep}, "devices": len(devices(uid))}


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


def enqueue(uid: int, p: dict, now: datetime | None = None, prefs: dict | None = None, urgent: bool = False) -> None:
    """urgent: kişinin o an beklediği olay (ör. QR'ı gösterirken eşinin katılması): sessiz saate ertelenmez."""
    now = now or _now()
    q = None if urgent else quiet_until(prefs or get_prefs(uid), now)
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
            p = json.loads(r["payload"])
            res = deliver(r["user_id"], p)
            n += res["sent"]
            retry = bool(res["failed"] and not res["sent"] and (r.get("tries") or 0) + 1 < MAX_TRIES)
            if p.get("ann"):  # duyuru: kuyruktan gidenler de kaydın sayılarına
                from app import announce

                announce.record(int(p["ann"]), res, final=not retry)
            if retry:
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
           background: bool = True, urgent: bool = False) -> list[dict]:
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
        enqueue(uid, x, now, p, urgent)
    if out and background:
        flush_soon()
    return out


def ops_alert(status: str, region: str | None, text: str, today: date | None = None) -> int:
    """Fiyat verisi aksadı (toplayıcı işin POST /api/ops/alert'i, içe aktarım anomalisi): yöneticilerin telefonuna
    "sistem" türü push (sessiz saate uyar). Aynı gün + durum + bölge bir kez gider. Dönen: kuyruğa giren bildirim."""
    if status == "test":  # toplayıcının deneme çağrısı: ne olduğu açık yazsın
        title, text = "Fiyat toplama: test", "Test bildirimi: fiyat toplama uyarıları çalışıyor."
    else:
        title = f"Fiyat toplama: {status}" + (f" · {region}" if region else "")
    return system_alert(f"sistem:{(today or date.today()).isoformat()}:{status}:{region or '-'}", title, text)


def system_alert(pid: str, title: str, text: str) -> int:
    """Yöneticilerin telefonuna "sistem" türü push (sessiz saate uyar); aynı pid bir kez gider (claim). Dönen: kuyruğa
    giren bildirim."""
    from app import feedback

    return sum(len(notify(a["id"], [item("sistem", pid, title, text, R_SISTEM)])) for a in feedback.admins())


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


def _borc(today: date) -> list[dict]:
    """Borçta (Borç / Alacak "borcum") söz verilen ya da kesin ödeme günü ve ödeme planındaki her taksit: 2 gün kala ve
    o gün. Dokununca Borç planı. Alacakta (yetenek debt_terms_receivable) aynı günler, nötr metin ("Ahmet: 2. taksit günü
    bugün"); dokununca Borç / Alacak'ta o kayıt. Borç / Alacak ortak defterde üyeye kapalıysa yok."""
    from app import debt_plan, money

    if debt_plan._hidden_people():
        return []
    out = []
    for d in db.list_debts(open_only=True):
        if d["written_off_at"] or d["remaining"] <= 0:
            continue
        alacak = d["direction"] == "alacak"
        route = f"{R_BORCLAR}/kayit/{d['id']}" if alacak else R_BORC_PLANI
        sch = d.get("schedule")
        if sch:  # ödeme planı: her taksitten 2 gün önce ve o gün (ödenmişse yok)
            nx = sch["next"]
            if not nx:
                continue
            left = (date.fromisoformat(nx["date"]) - today).days
            if left not in (0, 2):
                continue
            when = "bugün" if left == 0 else "2 gün sonra"
            amt = money.tl(max(nx["left"], sch["due_now"]), trim=True)
            key = f"borc:{d['id']}:taksit{nx['no']}:{nx['date']}:{'songun' if left == 0 else '2gun'}"
            if alacak:
                out.append(item("borc", key, f"{d['person']}: {nx['no']}. taksit günü {when}",
                                f"Taksit {amt}. Geldiyse Borç / Alacak'ta işaretle.", route))
            else:
                out.append(item("borc", key, f"{d['person']}: {nx['no']}. taksitin günü {when}",
                                f"Taksit {amt}. Ödediysen Borç planında işaretle.", route))
            continue
        if not d["due_date"]:
            continue
        left = (date.fromisoformat(d["due_date"]) - today).days
        if left not in (0, 2):
            continue
        amt = money.tl(d["remaining"], trim=True) if d["asset"] == "TRY" else f"{d['remaining']:g} {d['asset']}"
        when = "bugün" if left == 0 else "2 gün sonra"
        key = f"borc:{d['id']}:{d['due_date']}:{'songun' if left == 0 else '2gun'}"
        if alacak:
            what = "ödeme günü" if d.get("strict") else "söz verdiği gün"
            out.append(item("borc", key, f"{d['person']}: {what} {when}",
                            f"Kalan {amt}. Geldiyse Borç / Alacak'ta işaretle.", route))
            continue
        what = "ödeme günü" if d.get("strict") else "söz verdiğin ödeme günü"  # kesin tarih: vade
        out.append(item("borc", key, f"{d['person']}: {what} {when}",
                        f"Kalan {amt}. Ödediysen Borç planında işaretle.", route))
    return out


def _butce(today: date) -> list[dict]:
    from app import finance, money

    from app import book, plans

    month = finance.current_period(today)
    out = []
    status = finance.budgets(month)
    # ortak bütçe (Aile): bildirim defterdeki herkese kendi tercihine göre gider (run_daily her kişiyi ortak defterde
    # dolaşır); metinde kim ne kadar harcadı da yazar
    u = db.current_user()
    ppl = book.shared_people() if status and plans.allows(u, "aile_rapor") else []
    split = finance.budget_split(month, ppl) if ppl else {}
    for b in status:
        for th in (100, 80):
            if b["pct"] >= th:
                slug = finance._slug(b["category"])  # Özet uyarısının anahtarıyla aynı
                title = f"{b['category']} bütçesi aşıldı" if th == 100 else f"{b['category']} bütçesinin %{b['pct']:.0f} kadarı harcandı"
                body = f"{money.tl(b['spent'], trim=True)} / {money.tl(b['limit'], trim=True)}"
                who = [f"{'Sen' if u and p['user_id'] == u['id'] else p['name']} {money.tl(p['amount'], trim=True)}"
                       for p in split.get(b["category"], [])]
                if len(who) > 1 or (who and split[b["category"]][0]["user_id"] != (u or {}).get("id")):
                    body += " · " + ", ".join(who[:3])
                out.append(item("butce", f"butce:{month}:{slug}:{th}", title, body, R_BUTCELER))
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
    return [item("ozet", f"ozet:{prev}", finance.period_title(prev, " dönemi bitti"),
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


def _aile(u: dict) -> list[dict]:
    """Aile koltukları dolu (üyenin kendi aboneliği webhook'suz bitti: plan_until geçti). Webhook'ta zaten gönderilen
    aynı kimlikle gelir, ikinci kez gitmez. Üye kendisi için, sahip koltuksuz her üye için."""
    from app import plans

    if not plans.hosted():
        return []
    ppl = [u] if u.get("member_of") else system.members(u["id"])
    return [it for m in ppl for uid, it in plans.seat_notice_items(m) if uid == u["id"]]


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


def _sadaka(today: date) -> list[dict]:
    """Seçilen ritmin hatırlatma günü (haftalık: Cuma; aylık: ayın ilk Cuması), o dönemde kişinin sadaka kaydı yoksa tek
    bildirim; aynı dönem için bir kez. Seçim yoksa en çok 2 kez "Hayırlı Cumalar · not edelim mi?". Özellik / rıza kapalıysa ya da
    Zekât ve Sadaka sayfası üyeye gizliyse yok (app/sadaka_reminder.push_items)."""
    from app import sadaka_reminder

    return sadaka_reminder.push_items(today)


def _fiyat(today: date) -> list[dict]:
    """Sık aldığın ürünlerde (90 günde aynı zincirde 2+ alım) topluluk ortancası son ödediğinden %10+ yüksek: tek özet
    bildirim (app/price_alerts.py). Fiyat takibi paketinde ve sayfa açıkken. Kimlik artışların özeti: aynı artışlar
    ertesi gün yeniden gitmez; günlük iş günde bir çalıştığı için en çok bir bildirim."""
    from app import price_alerts

    if not price_alerts.allowed():
        return []
    rises = price_alerts.community_rises(today)
    if not rises:
        return []
    sig = hashlib.sha256(json.dumps([(r["chain"], r["group"], r["median"]) for r in rises]).encode()).hexdigest()[:12]
    from urllib.parse import quote

    one = rises[0].get("product_key") if len(rises) == 1 else None  # tek ürün: doğrudan o ürünün sayfası
    route = f"{R_FIYATLAR}/{quote(one, safe='')}" if one else R_FIYATLAR
    return [item("fiyat", f"fiyat:{sig}", "Fiyat artışı", price_alerts.digest(rises), route)]


def _hesaplar_hidden() -> bool:
    """Ortak defterde sahibin üyeye kapattığı Hesaplar sayfası (kumbara ve ön ödemeli kartlar orada)."""
    from app import book

    r = book.active()
    return bool(r and "hesaplar" in r["hidden_pages"])


def _amount(x: float, unit: str) -> str:
    from app import money, zakat

    if unit == "TRY":
        return money.tl(x, trim=True)
    sym = zakat.KINDS.get(unit, (unit, "", unit))[2]
    return f"{round(x, 3):g} {sym}".replace(".", ",")


def _kumbara(today: date) -> list[dict]:
    """Hedefi ve hedef tarihi olan açık kumbara: tarihe KUMBARA_DAYS gün kala biriken hedefin gerisindeyse tek
    hatırlatma (kimlik tarihe bağlı: tarih değişirse yeni tarih için yine bir kez). Dokununca o kumbara."""
    from app import book, money

    if _hesaplar_hidden():
        return []
    out = []
    for j in db.list_jars(today, include_closed=False):
        if not j.get("goal") or not j.get("deadline"):
            continue
        if j.get("account_id") and not book.can_see_account(j["account_id"]):
            continue
        dl = date.fromisoformat(j["deadline"][:10])
        if (dl - today).days != KUMBARA_DAYS:
            continue
        unit = j.get("unit") or "TRY"
        left = j["goal"] - j["balance"]
        if (money.le(left, 0) if unit == "TRY" else left <= 1e-9):  # altın/döviz miktarı yuvarlanmaz
            continue
        out.append(item("kumbara", f"kumbara:{j['id']}:{dl.isoformat()}:{KUMBARA_DAYS}gun",
                        f"{j['name']}: hedef tarihine {KUMBARA_DAYS} gün kaldı",
                        f"Hedef {_amount(j['goal'], unit)}, biriken {_amount(j['balance'], unit)}; {_amount(left, unit)} "
                        f"eksik. Son gün {dl.day} {AY[dl.month - 1]}. Ekleyebilir ya da tarihi değiştirebilirsin.",
                        f"{R_KUMBARA}/{j['id']}"))
    return out


def _kart_suresi(today: date) -> list[dict]:
    """Ön ödemeli kart (yemek / hediye / giyim) son kullanma tarihine 30 ve 7 gün kala, kartta para varsa. Bakiye 0 ya
    da eksiyse yok (kaybedilecek para yok)."""
    from app import book, money

    if _hesaplar_hidden():
        return []
    out = []
    for w in db.list_wallets():
        if not w.get("expires_at") or not book.can_see_account(w["id"]):
            continue
        exp = date.fromisoformat(str(w["expires_at"])[:10])
        left = (exp - today).days
        if left not in KART_DAYS or not money.gt(w.get("current") or 0, 0):
            continue
        out.append(item("kart_suresi", f"kart_suresi:{w['id']}:{exp.isoformat()}:{left}gun",
                        f"{w['name']}: son kullanmaya {left} gün kaldı",
                        f"Kartta {money.tl(w['current'], trim=True)} var; {exp.day} {AY[exp.month - 1]} sonrası "
                        "kullanılamayabilir. Harcamayı planla.", R_YEMEK))
    return out


def _app_version(v: str | None) -> tuple[int, ...]:
    """Cihaz kaydındaki sürüm ("1.0.7 (39)", "1.0.7") → (1, 0, 7); okunamazsa ()."""
    try:
        return tuple(int(x) for x in (v or "").split(" ")[0].split("."))
    except ValueError:
        return ()


def has_app(uid: int | None, at_least: tuple[int, ...]) -> bool:
    """Kişinin bu sürüm ya da sonrası bir telefonu var mı (bildirimin açtığı ekran o sürümde)."""
    return uid is not None and any(_app_version(d.get("app_version")) >= at_least for d in devices(uid))


def _garanti(u: dict, today: date) -> list[dict]:
    """Garantisi bitmek üzere ürün: bitişe 30 gün kala (7 günden fazla kaldıysa) ve 7 gün kala birer kez; kimlik bitiş
    tarihine ve aşamaya bağlı (süre değişirse yeni bitiş için yine birer kez). Yalnız Garantiler ekranı olan
    uygulamaya (GARANTI_MIN_APP). Ortak defterde üyenin göremediği kayda bağlı ürün yok (app/warranty.py)."""
    from app import book, warranty

    r = book.active()
    if (r and "garantiler" in r["hidden_pages"]) or not has_app(u.get("id"), GARANTI_MIN_APP):
        return []
    out = []
    for w in warranty.all_visible(today):
        stage = warranty.push_stage(w["days_left"])
        if not stage:
            continue
        end = date.fromisoformat(w["ends_at"])
        left = w["days_left"]
        when = "bugün bitiyor" if left == 0 else f"bitmesine {left} gün kaldı"
        out.append(item("garanti", f"garanti:{w['id']}:{end.isoformat()}:{stage}", f"{w['name']}: garantinin {when}",
                        f"Garanti {end.day} {AY[end.month - 1]} {end.year} tarihinde bitiyor. Arızası varsa şimdi servise "
                        "götür; fatura ve garanti belgesi Garantiler'de.", f"{R_GARANTI}/{w['id']}"))
    return out

def _abonelik(u: dict, today: date) -> list[dict]:
    """Uygulama mağazası aboneliği (adsız aday dahil) yenilenmeden REMIND_DAYS gün önce. Tasarruf paketinde değilse ya
    da ortak defterde Tasarruf sayfası üyeye kapalıysa yok. Dokununca aboneliğin ayrıntısı."""
    from app import book, plans, store_subs

    r = book.active()
    if (r and "tasarruf" in r["hidden_pages"]) or not plans.allows(u, "tasarruf"):
        return []
    return store_subs.reminders(today)


def collect(u: dict, today: date, prefs: dict | None = None) -> list[dict]:
    """Bugünün zamanlı bildirimleri (açık türler), kullanıcının defterinde ve görebildiği kadarıyla. Bağlam: use_space."""
    on = (prefs or get_prefs(u["id"]))["types"]
    out: list[dict] = []
    for kind, fn in (("odeme", lambda: _odeme_ekstre_dokum(today, on)), ("fatura", lambda: _fatura(today)),
                     ("butce", lambda: _butce(today)), ("gelir", lambda: _gelir(today)), ("ozet", lambda: _ozet(today)),
                     ("ortak", lambda: _ortak(u, today)), ("zekat", lambda: _zekat(today)), ("aile", lambda: _aile(u)),
                     ("fiyat", lambda: _fiyat(today)), ("borc", lambda: _borc(today)),
                     ("kumbara", lambda: _kumbara(today)), ("kart_suresi", lambda: _kart_suresi(today)),
                     ("sadaka", lambda: _sadaka(today)), ("garanti", lambda: _garanti(u, today)),
                     ("abonelik", lambda: _abonelik(u, today))):
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
    from app import book, finance, insight_state, money, store_subs

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
                tg = i.get("target") or {}  # mağaza aboneliği: ayrıntısına
                rt = store_subs.route(tg["id"]) if tg.get("screen") == "abonelik" and tg.get("id") else R_TASARRUF
                out.append(item("abonelik", k, i["title"], i.get("detail") or "", rt))
    if not book.hide_fees():  # ekstrede taksitler erken kapandı (app/early_close.py): kart ve son ekstre başına bir kez
        for c in finance.early_close_cards():
            d = c.get("detected")
            if d:
                out.append(item("faiz", f"erken_kapama:{c['card_no'][-4:]}:{c.get('period_end') or ''}",
                                f"{c['card']}: taksitler erken kapandı", d["text"], R_FAIZ))
    recent = (today - timedelta(days=45)).isoformat()
    for s in finance.subscriptions():
        prev = s.get("prev_monthly")
        # mağaza satırı ("APPLE"): aylık toplam yeni bir abonelikle de artar; zammı aday bazında price_up_items söyler
        if s.get("store"):
            continue
        if s["status"] == "aktif" and prev and s["last_seen"] >= recent and money.gt(s["monthly"], prev * 1.05):
            slug = finance._slug(s["merchant"])
            out.append(item("abonelik", f"abonelik:zam:{slug}:{s['last_period']}", f"{s['merchant'].title()} zamlandı",
                            f"{money.tl(prev, trim=True)} → {money.tl(s['monthly'], trim=True)}. Kullanmıyorsan "
                            "Tasarruf'tan iptal edildi diye işaretle.", R_TASARRUF))
    if not (book.active() and "tasarruf" in book.active()["hidden_pages"]):
        out += store_subs.price_up_items(today)  # mağaza aboneliğinde zam (adsız aday dahil)
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


# ---------------------------------------------------------------- ortak alışveriş listesi

LIST_EVERY = 30 * 60     # ekleyen başına en çok bu kadar saniyede bir bildirim
LIST_DELAY = 60.0        # ilk eklemeden sonra bekleme: art arda eklenenler tek bildirimde
LIST_NAMES = 3           # gövdede en çok bu kadar ürün adı
_list_pending: dict[str, threading.Timer] = {}


def _book_ctx() -> tuple[dict, int, str | None] | None:
    """(yapan kullanıcı, defter sahibi, defter dosyası) — ortak defterde ve push açıksa; "Benim" defteri ya da tek
    kişilik defter: None."""
    u = db.current_user()
    if not u or not u.get("id") or u.get("own_book") or not enabled():
        return None
    owner_id = u.get("member_of") or u["id"]
    if not u.get("member_of") and not system.members(owner_id):
        return None
    path = db.db_path()
    return u, owner_id, (None if os.path.abspath(path) == os.path.abspath(db.DB_PATH) else path)


def _list_people(owner_id: int, actor: int) -> list[dict]:
    """Bildirim gidecek kişiler (bağlam: defterin dosyası): sahip + üyeler, yapan hariç, telefonu olan, listesi
    gizlenmemiş (üyenin hidden_pages'inde "alisveris" yok)."""
    from app import book

    owner = system.user(owner_id)
    out = []
    for p in ([owner] if owner else []) + system.members(owner_id):
        if p["id"] == actor or p.get("disabled") or p.get("deleted_at") or not _has_devices(p["id"]):
            continue
        if p["id"] != owner_id and "alisveris" in book.rules(p["id"])["hidden_pages"]:
            continue
        out.append(p)
    return out


def _who(u: dict) -> str:
    return (u.get("username") or "").split("@")[0] or "Biri"


def list_body(name: str, texts: list[str]) -> str:
    """"Ayşe listeye 3 ürün ekledi: süt, yumurta, elma" (+ " ve 2 ürün daha")."""
    n = len(texts)
    head = ", ".join(t.strip() for t in texts[:LIST_NAMES])
    more = f" ve {n - LIST_NAMES} ürün daha" if n > LIST_NAMES else ""
    return f"{name} listeye {n} ürün ekledi: {head}{more}"


def _list_last(key: str) -> tuple[float | None, int]:
    """Ekleyenin son liste bildirimi: (zaman damgası | None, bildirilen son satır kimliği)."""
    raw = system.get(key) or ""
    try:
        at, rid = raw.split("|", 1)
        return float(at), int(rid)
    except ValueError:
        return None, 0


def _send_list_adds(u: dict, owner_id: int, path: str | None) -> list[dict]:
    """Ekleyenin son bildirimden (yoksa pencerenin başından) beri eklediği satırlar → diğer kişilere tek bildirim."""
    key = f"push:liste:{owner_id}:{u['id']}"
    now = datetime.now(timezone.utc)
    _at, last_id = _list_last(key)
    since = (now - timedelta(seconds=LIST_EVERY)).strftime("%Y-%m-%d %H:%M:%S")
    with db.use_space(path, u):
        with db.connect() as c:
            rows = [dict(r) for r in c.execute("SELECT id, text FROM shopping_list WHERE added_by = ? AND id > ? AND "
                                               "created_at >= ? ORDER BY id", (u["id"], last_id, since))]
        people = _list_people(owner_id, u["id"]) if rows else []
    if not rows:
        return []
    system.put(key, f"{now.timestamp():.0f}|{rows[-1]['id']}")
    it = item("liste", f"liste:{owner_id}:{u['id']}:{rows[-1]['id']}", "Alışveriş listesi",
              list_body(_who(u), [r["text"] for r in rows]), R_ALISVERIS)
    out = []
    for p in people:
        out += notify(p["id"], [it])
    return out


def after_list_add() -> None:
    """shopping.add kancası: ortak defterde diğer kişilere toplu bildirim zamanla (yukarıda). Hiçbir zaman hata
    fırlatmaz; push kapalıysa ya da kimsenin telefonu yoksa zamanlayıcı kurulmaz."""
    try:
        ctx = _book_ctx()
        if not ctx:
            return
        u, owner_id, path = ctx
        with db.use_space(path, u):
            if not _list_people(owner_id, u["id"]):
                return
        key = f"{owner_id}:{u['id']}"
        last, _rid = _list_last(f"push:liste:{key}")
        wait = LIST_DELAY if last is None else max(LIST_DELAY, LIST_EVERY - (time.time() - last))

        def go():
            with _pending_lock:
                _list_pending.pop(key, None)
            try:
                _send_list_adds(u, owner_id, path)
            except Exception:  # noqa: BLE001 - arka plan
                log.exception("push liste olayı u%s", u.get("id"))

        with _pending_lock:
            if key in _list_pending:
                return  # bu pencerenin bildirimi zaten kurulu: yeni satır ona girer
            t = _Timer(wait, go)
            t.daemon = True
            _list_pending[key] = t
        t.start()
    except Exception:  # noqa: BLE001 - kanca eklemeyi engellemesin
        log.exception("push liste kancası")


def after_trip_complete(trip: dict) -> list[dict]:
    """shopping_trips.complete kancası: "Mehmet alışverişi tamamladı · 7 ürün alındı" diğer kişilere (hemen; sessiz
    saatte kuyrukta bekler). Hiçbir zaman hata fırlatmaz."""
    try:
        ctx = _book_ctx()
        if not ctx:
            return []
        u, owner_id, _path = ctx
        n = sum(1 for x in trip.get("items") or [] if x.get("done"))
        it = item("liste", f"liste:trip:{owner_id}:{trip.get('id')}", "Alışveriş tamamlandı",
                  f"{_who(u)} alışverişi tamamladı · {n} ürün alındı", R_ALISVERIS)
        out = []
        for p in _list_people(owner_id, u["id"]):
            out += notify(p["id"], [it])
        return out
    except Exception:  # noqa: BLE001
        log.exception("push alışveriş tamamlandı kancası")
        return []


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
