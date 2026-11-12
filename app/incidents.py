"""Sorun kaydı (yetenek "incidents"): kullanıcının karşılaştığı hatalar, KİŞİSEL VERİ OLMADAN, yöneticinin "Sorunlar"
sayfası için.

Ne kaydedilir: hata kodu (aşağıdaki sabit sözlük KODLAR), yer (sunucuda uç kalıbı "POST /api/entries/{eid}", istemcide
ekran yolu "diger/ekstreler/:id"), uygulama sürümü, platform (ios | android | web), kullanıcı id'si (oturum varsa), gün,
ilk/son görülme ve sayaç. Aynı kişi + tür + gün tek satırda birleşir (sayaç artar).
Ne kaydedilmez: belge/fiş içeriği, tutar, isim, kart no, açıklama, dosya, IP, tarayıcı kimliği. Türün "örnek bağlamı"
yalnız teknik süzgeçten geçmiş kısa özettir (kisa_ozet: rakamlar "#", tırnak içi metin, adres, e-posta/IBAN/kart
atılır; en çok 160 karakter). Ham yığın izi yalnız sunucu günlüğünde.

Kaynaklar:
- Sunucu (ara katman, app/api/main.py): kullanıcıya dönen 5xx, anlamlı 4xx (400/422 iş kuralı, 413, 429; doğrulama
  hatası 422 ve 401/402/403/404/409 kaydedilmez) ve uçların açıkça işaretledikleri (note: okunamayan ekstre/döküm/fiş/
  fatura/dekont, okuma hakkı doldu). Uç işaret koyduysa durum kodundan ayrıca kayıt çıkmaz.
- Arka plan işleri: "ekstre.jobs" günlüğüne düşen ERROR (log işleyicisi).
- İstemci: POST /api/incidents/client (yakalanmamış JS hatası, ekran çökmesi, ağ zaman aşımı, sunucuya ulaşılamadı,
  telefon eklentisi hatası). Oturumsuz da kabul; saatlik sınır (kişi / bağlantı başı, bellekte; adres saklanmaz).

Yazma isteği yavaşlatmaz: kayıt bellekteki tampona eklenir, arka plan iş parçacığı FLUSH_SEC'te bir toplu yazar
(testlerde iş parçacığı yok; flush() elle, yönetici uçları okumadan önce de boşaltır).

Uyarı: yeni "kritik" tür, düzeltildikten sonra yeniden görülen tür ya da ani artış (son SPIKE_WINDOW dakikada
SPIKE_USERS+ kişi) → yöneticilere "sistem" push'u + Telegram; tür başına saatte en çok bir, sunucu geneli saatte
ALERT_MAX.
"Düzeltildi": yönetici işaretleyince türü son NOTIFY_DAYS günde yaşamış kullanıcılara bir kez "duzeltme" push'u
("… düzelttik, tekrar deneyebilirsin"; düzeltme sürümünün altındaki uygulamaya "önce güncelle" eki).
Saklama: KEEP_DAYS gün (gece temizliği jobs.run_global); hesap silinince kişinin kayıtları (system.delete_user).
"""
import contextvars
import html
import json
import logging
import os
import re
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from app import system

log = logging.getLogger("hesapkitap.incidents")

KEEP_DAYS = 90
NOTIFY_DAYS = 30
SPIKE_USERS = 5          # bu kadar farklı kişi …
SPIKE_WINDOW = 60        # … son bu kadar dakikada → ani artış
ALERT_GAP = 3600         # tür başına uyarı arası (sn)
ALERT_MAX = 10           # sunucu geneli saatte en çok uyarı
FLUSH_SEC = 2.0
FLUSH_AT = 200           # tampon bu kadar olunca hemen yazılır
BUFFER_MAX = 5000        # yazılamazsa (disk) bellek şişmesin
CLIENT_MAX_EVENTS = 10   # bir istekte en çok olay
RATE_USER = 60           # istemci olayı: kişi başı saatte
RATE_ANON = 20           # oturumsuz: bağlantı başı saatte
STATUSES = ("yeni", "inceleniyor", "duzeltildi")
R_SORUNLAR = "#/diger/ayarlar/sorunlar"

# kod → (yönetici etiketi, ağırlık kritik | uyari | bilgi, kaynak, kullanıcıya düzeltme metni | None)
# ağırlık: kritik → ilk görülüşte de uyarı; uyari → yalnız ani artış / yeniden görülme; bilgi → uyarı yok, haber yok.
KODLAR: dict[str, tuple[str, str, str, str | None]] = {
    # sunucu: kullanıcıya dönen hata
    "sunucu_hatasi": ("Sunucu hatası (500)", "kritik", "sunucu", "Karşılaştığın sunucu hatasını"),
    "servis_yok": ("Bağlı servis yanıt vermedi (502/504)", "kritik", "sunucu", "Bağlantı sorununu"),
    "sunucu_mesgul": ("Sunucu meşgul (503)", "uyari", "sunucu", "Yoğunluk sorununu"),
    "is_kurali": ("İşlem reddedildi (400/422)", "uyari", "sunucu", "Kaydederken aldığın hatayı"),
    "dosya_buyuk": ("Dosya ya da istek çok büyük (413)", "uyari", "sunucu", "Dosya yükleme sorununu"),
    "cok_istek": ("Çok fazla istek (429)", "uyari", "sunucu", None),
    "okuma_hakki_doldu": ("Okuma hakkı doldu", "bilgi", "sunucu", None),
    # sunucu: belge okuma
    "ekstre_okunamadi": ("Ekstre okunamadı", "uyari", "sunucu", "Ekstre okuma sorununu"),
    "ekstre_yerine_foto": ("Ekstre yerine fotoğraf yüklendi", "bilgi", "sunucu", None),
    "ekstre_yerine_fatura": ("Ekstre yerine fatura yüklendi", "bilgi", "sunucu", None),
    "foto_bicimi": ("Fotoğraf biçimi okunamadı", "uyari", "sunucu", "Fotoğraf okuma sorununu"),
    "pdf_sifre": ("PDF şifresi yanlış", "bilgi", "sunucu", None),
    "dokum_okunamadi": ("Hesap dökümü okunamadı", "uyari", "sunucu", "Hesap dökümü okuma sorununu"),
    "fis_okunamadi": ("Fiş okunamadı", "uyari", "sunucu", "Fiş okuma sorununu"),
    "fatura_okunamadi": ("Fatura okunamadı", "uyari", "sunucu", "Fatura okuma sorununu"),
    "belge_okunamadi": ("Belge okunamadı", "uyari", "sunucu", "Belge okuma sorununu"),
    "dekont_okunamadi": ("Dekont okunamadı", "uyari", "sunucu", "Dekont okuma sorununu"),
    "okuma_servisi": ("Okuma servisi hatası (yapay zekâ)", "kritik", "sunucu", "Belge okuma sorununu"),
    # sunucu: arka plan
    "is_hatasi": ("Arka plan işi başarısız", "kritik", "is", None),
    # istemci (mobil + web)
    "js_hatasi": ("Uygulama hatası (yakalanmamış)", "kritik", "istemci", "Uygulamada karşılaştığın hatayı"),
    "ekran_coktu": ("Ekran çöktü", "kritik", "istemci", "Ekranın kapanmasına yol açan hatayı"),
    "ag_zaman_asimi": ("Sunucu zamanında yanıt vermedi", "uyari", "istemci", "Yavaşlık sorununu"),
    "sunucuya_ulasilamadi": ("Sunucuya ulaşılamadı", "uyari", "istemci", "Bağlantı sorununu"),
    "eklenti_hatasi": ("Telefon özelliği hatası (kamera, dosya…)", "uyari", "istemci", "Telefon özelliğiyle ilgili sorunu"),
}
CLIENT_CODES = frozenset(k for k, v in KODLAR.items() if v[2] == "istemci")
PLATFORMS = ("ios", "android", "web")


def enabled() -> bool:
    return os.environ.get("HESAPKITAP_INCIDENTS", "1").strip() != "0"


def _utc(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- süzgeçler (kişisel veri yok)

_URL = re.compile(r"(?:https?|capacitor|file|blob|data):\S*", re.I)
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`|“[^”]*”|‘[^’]*’|«[^»]*»")
_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")
_PATHLIKE = re.compile(r"(?:/(?!api/)[\w.-]+){2,}")  # dosya yolu (kişinin dosya adı); uç yolu (/api/…) kalır


def kisa_ozet(text: str | None, limit: int = 160) -> str | None:
    """Yöneticiye gösterilecek kısa, teknik özet: ilk satır; adres, tırnak içi metin (ad, açıklama, dosya adı), e-posta,
    IBAN, kart, rakamlar atılır. Boşsa None."""
    from app import logs

    if not text:
        return None
    s = str(text).strip().splitlines()[0] if str(text).strip() else ""
    s = logs.mask(s)
    s = _URL.sub("<adres>", s)
    s = _QUOTED.sub("…", s)
    s = _PATHLIKE.sub("<yol>", s)
    s = _DIGITS.sub("#", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[: limit - 1] + "…" if len(s) > limit else s) or None


_SAFE_PLACE = re.compile(r"[^a-z0-9/_:{}\- ]")


def ekran(route: str | None) -> str:
    """İstemcinin ekran yolu → kayıt yeri: "#/diger/kumbara/12?x" → "diger/kumbara/:id". Kimlik ve sorgu atılır."""
    s = (route or "").strip().lower().split("?")[0]
    s = re.sub(r"^#?/*", "", s)
    parts = [":id" if re.fullmatch(r"[\d-]+|[0-9a-f]{8,}", p) else p for p in s.split("/") if p]
    s = _SAFE_PLACE.sub("", "/".join(parts))[:60]
    return s or "-"


def uc(request) -> str:
    """Sunucu ucunun kalıbı ("POST /api/entries/{eid}"); kalıp yoksa yoldaki sayılar {id}."""
    route = request.scope.get("route")
    path = getattr(route, "path", None) or re.sub(r"/\d+(?=/|$)", "/{id}", request.url.path)
    return f"{request.method} {path}"[:90]


def _exc_ozet(e: BaseException) -> str:
    """İstisnanın türü ve uygulamadaki son çerçeve (dosya:işlev); metni (kullanıcı verisi taşıyabilir) yazılmaz."""
    where = ""
    for fr in reversed(traceback.extract_tb(e.__traceback__) or []):
        if "/app/" in fr.filename.replace("\\", "/"):
            where = f" @ {fr.filename.replace(chr(92), '/').split('/app/')[-1]}:{fr.name}"
            break
    return f"{type(e).__name__}{where}"[:160]


def surum(v: str | None) -> str | None:
    """Uygulama sürümü: "1.0.8 (45)", "1.0.8" gibi; başka bir şey yazılmaz."""
    m = re.match(r"^\s*(\d{1,3}(?:\.\d{1,4}){0,3})(?:\s*\((\d{1,6})\))?", v or "")
    if not m:
        return None
    return f"{m.group(1)} ({m.group(2)})" if m.group(2) else m.group(1)


def _ver(v: str | None) -> tuple[int, ...] | None:
    from app import announce

    return announce.parse_version(v)


# ---------------------------------------------------------------- tampon ve toplu yazma

_buf: list[tuple[str, dict]] = []
_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None


def _auto() -> bool:
    return "PYTEST_CURRENT_TEST" not in os.environ


def record(code: str, place: str = "-", *, uid: int | None = None, app_version: str | None = None,
           platform: str | None = None, ozet: str | None = None, http: int | None = None,
           at: float | None = None) -> None:
    """Bir olayı tampona ekle (hızlı; veritabanına arka planda). Bilinmeyen kod yok sayılır; hata fırlatmaz."""
    if code not in KODLAR or not enabled():
        return
    ev = {"code": code, "place": (place or "-")[:90], "uid": int(uid) if uid else 0, "ver": surum(app_version),
          "platform": platform if platform in PLATFORMS else None, "ozet": kisa_ozet(ozet), "http": http,
          "at": at or time.time()}
    with _lock:
        if len(_buf) >= BUFFER_MAX:
            return
        _buf.append((system._path(), ev))
        n = len(_buf)
    if _auto():
        _start()
        if n >= FLUSH_AT:
            _wake.set()


def _start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, daemon=True, name="incidents-flush")
        _thread.start()


def _loop() -> None:
    while True:
        _wake.wait(FLUSH_SEC)
        _wake.clear()
        try:
            flush()
        except Exception:  # noqa: BLE001 - kayıt hatası hiçbir şeyi bozmaz
            log.exception("sorun kaydı yazılamadı")


def flush() -> int:
    """Tampondakileri yaz (yalnız şu anki sistem veritabanına ait olanlar; testte eski veritabanınınkiler atılır).
    Yazılan olay sayısı. Ardından uyarı denetimi."""
    with _lock:
        items = _buf[:]
        _buf.clear()
    here = system._path()
    evs = [e for p, e in items if p == here]
    if not evs:
        return 0
    touched: dict[int, dict] = {}
    with system.connect() as c:
        for e in evs:
            kid, new, reopened = _kind_upsert(c, e)
            t = touched.setdefault(kid, {"new": False, "reopened": False})
            t["new"] |= new
            t["reopened"] |= reopened
            day = _utc(e["at"])[:10]
            ts = _utc(e["at"])
            c.execute("INSERT INTO incidents (kind_id, user_id, day, count, first_at, last_at, app_version, platform) "
                      "VALUES (?, ?, ?, 1, ?, ?, ?, ?) ON CONFLICT (kind_id, user_id, day) DO UPDATE SET "
                      "count = count + 1, last_at = MAX(last_at, excluded.last_at), "
                      "app_version = COALESCE(excluded.app_version, app_version), "
                      "platform = COALESCE(excluded.platform, platform)",
                      (kid, e["uid"], day, ts, ts, e["ver"], e["platform"]))
    try:
        _alerts(touched)
    except Exception:  # noqa: BLE001
        log.exception("sorun uyarısı gönderilemedi")
    return len(evs)


def _kind_upsert(c, e: dict) -> tuple[int, bool, bool]:
    """Türü bul ya da kur; son görülme ve örnek bağlam güncellenir. (id, yeni mi, düzeltilmişken yeniden mi görüldü)."""
    ts = _utc(e["at"])
    r = c.execute("SELECT id, status, fixed_at, fixed_version FROM incident_kinds WHERE code = ? AND place = ?",
                  (e["code"], e["place"])).fetchone()
    sample = json.dumps({k: v for k, v in (("ozet", e["ozet"]), ("http", e["http"] if (e["http"] or 0) >= 400 else None), ("platform", e["platform"]),
                                           ("app_version", e["ver"])) if v}, ensure_ascii=False)
    if not r:
        c.execute("INSERT OR IGNORE INTO incident_kinds (code, place, first_seen, last_seen, status, sample) "
                  "VALUES (?, ?, ?, ?, 'yeni', ?)", (e["code"], e["place"], ts, ts, sample))
        kid = c.execute("SELECT id FROM incident_kinds WHERE code = ? AND place = ?", (e["code"], e["place"])).fetchone()[0]
        return kid, True, False
    reopened = False
    if r["status"] == "duzeltildi" and r["fixed_at"] and ts > r["fixed_at"]:
        fixed = _ver(r["fixed_version"])
        have = _ver(e["ver"])
        # düzeltme sürümü verildiyse yalnız o sürüm ve sonrasında görülen hata yeniden açar (eski uygulama sayılmaz)
        if not fixed or (have and have >= fixed) or (not have and KODLAR[e["code"]][2] != "istemci"):
            reopened = True
    c.execute("UPDATE incident_kinds SET first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?), sample = CASE WHEN ? <> '{}' THEN ? ELSE sample END"
              + (", status = 'yeni', reopened = reopened + 1" if reopened else "") + " WHERE id = ?",
              (ts, ts, sample, sample, r["id"]))
    return r["id"], False, reopened


def pending() -> int:
    with _lock:
        return len(_buf)


# ---------------------------------------------------------------- istek bağlamı (ara katman)

_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("hk_incident", default=None)


def begin(request) -> contextvars.Token:
    """İstek başında: bu isteğin kayıt bağlamı (uçların işaretleri, kullanıcı, doğrulama hatası)."""
    return _ctx.set({"notes": [], "uid": None, "validation": False, "detail": None, "request": request})


def end(token) -> None:
    _ctx.reset(token)


def set_user(user: dict | None) -> None:
    ctx = _ctx.get()
    if ctx is not None and user and user.get("id") and not user.get("demo"):
        ctx["uid"] = user["id"]


def mark_validation() -> None:
    """İstek gövdesi/parametre doğrulama hatası (422): kaydedilmez (biçim hatası, iş kuralı değil)."""
    ctx = _ctx.get()
    if ctx is not None:
        ctx["validation"] = True


def mark_detail(detail) -> None:
    ctx = _ctx.get()
    if ctx is not None and isinstance(detail, str):
        ctx["detail"] = detail


def note(code: str, ozet: str | None = None) -> None:
    """Uç bu istekte bir sorunu işaretler (ör. okunamayan fiş). İstek dışında çağrılırsa (iş, test) oturumsuz kayıt."""
    ctx = _ctx.get()
    if ctx is None:
        record(code, "-", ozet=ozet)
        return
    ctx["notes"].append((code, ozet))


HTTP_CODES = {500: "sunucu_hatasi", 502: "servis_yok", 504: "servis_yok", 503: "sunucu_mesgul", 413: "dosya_buyuk",
              429: "cok_istek", 400: "is_kurali", 422: "is_kurali"}
# belge okuyan uçlar: 400/422 → okunamadı türü, 5xx → okuma servisi
READ_PLACES = {"POST /api/bank-import/preview": "dokum_okunamadi", "POST /api/receipts/parse": "fis_okunamadi",
               "POST /api/bills/parse": "fatura_okunamadi", "POST /api/documents/parse": "belge_okunamadi",
               "POST /api/dekont/parse": "dekont_okunamadi", "POST /api/statements/upload": "ekstre_okunamadi"}
SKIP_PATHS = ("/api/incidents/client", "/api/csp-report", "/api/ops/alert", "/api/feedback")


def finish(request, status: int, exc: BaseException | None = None) -> None:
    """İstek sonunda: uçların işaretleri, yoksa anlamlı durum kodu. Hata fırlatmaz."""
    try:
        ctx = _ctx.get()
        path = request.url.path
        if ctx is None or not path.startswith("/api/") or path in SKIP_PATHS:
            return
        notes = ctx["notes"]
        place = uc(request)
        if not notes:
            code = HTTP_CODES.get(status) if status >= 500 or not ctx["validation"] else None
            if not code:
                return
            if place in READ_PLACES and code in ("is_kurali", "servis_yok", "sunucu_hatasi"):
                code = READ_PLACES[place] if code == "is_kurali" else "okuma_servisi"
            ozet = _exc_ozet(exc) if exc is not None else _detail_ozet(ctx["detail"], status)
            notes = [(code, ozet)]
        from app import funnel

        plat = funnel.platform_of(request.headers)
        uid = ctx["uid"]
        ver = _device_version(uid, plat) if uid else None
        for code, ozet in notes:
            record(code, place, uid=uid, app_version=ver, platform=plat if plat in PLATFORMS else None, ozet=ozet,
                   http=status)
    except Exception:  # noqa: BLE001
        log.exception("sorun kaydı hazırlanamadı")


def _detail_ozet(detail, status: int) -> str | None:
    from app import errors

    if not isinstance(detail, str) or not detail:
        return None
    return errors.user_text(detail, status) if not errors.is_user_text(detail) else detail


_dev_cache: dict[tuple[int, str], tuple[float, str | None]] = {}


def _device_version(uid: int, plat: str) -> str | None:
    """Sunucu tarafı kaydında uygulama sürümü: kişinin o platformdaki telefonunun bildirdiği son sürüm (push cihazı);
    tarayıcıda yok. 10 dk bellekte."""
    if plat not in ("ios", "android"):
        return None
    now = time.time()
    hit = _dev_cache.get((uid, plat))
    if hit and now - hit[0] < 600:
        return hit[1]
    v = None
    try:
        with system.connect() as c:
            r = c.execute("SELECT app_version FROM push_devices WHERE user_id = ? AND platform = ? AND app_version IS NOT NULL "
                          "ORDER BY last_seen DESC LIMIT 1", (uid, plat)).fetchone()
        v = r[0] if r else None
    except sqlite3.Error:
        pass
    if len(_dev_cache) > 5000:
        _dev_cache.clear()
    _dev_cache[(uid, plat)] = (now, v)
    return v


# ---------------------------------------------------------------- arka plan işleri (günlük işleyicisi)

class JobErrors(logging.Handler):
    """"ekstre.jobs" günlüğündeki ERROR satırı → "is_hatasi" (yer: işin adı / mesaj kalıbı)."""

    def __init__(self):
        super().__init__(level=logging.ERROR)

    def emit(self, r: logging.LogRecord) -> None:
        try:
            if r.msg == "job %s failed" and r.args:
                place = f"iş: {r.args[0] if isinstance(r.args, tuple) else r.args}"
            else:
                place = f"iş: {kisa_ozet(str(r.msg), 60) or '-'}"
            ozet = _exc_ozet(r.exc_info[1]) if r.exc_info and r.exc_info[1] else None
            record("is_hatasi", place[:90], ozet=ozet)
        except Exception:  # noqa: BLE001
            pass


_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    logging.getLogger("ekstre.jobs").addHandler(JobErrors())


# ---------------------------------------------------------------- istemci olayları

_rate: dict[str, list[float]] = {}
_SALT = os.urandom(16)


def anon_key(ip: str | None) -> str:
    """Oturumsuz istemcinin saatlik sınır anahtarı: bağlantı adresinin süreç tuzlu özeti (yalnız bellekte; saklanmaz)."""
    import hashlib

    return "a" + hashlib.sha256(_SALT + (ip or "").encode()).hexdigest()[:20]


def client_allowed(key: str, n: int, cap: int) -> int:
    """Saatlik sınır (bellekte): bu istekten kabul edilecek olay sayısı."""
    now = time.time()
    with _lock:
        hits = [t for t in _rate.get(key, []) if now - t < 3600]
        ok = max(0, min(n, cap - len(hits)))
        hits.extend([now] * ok)
        _rate[key] = hits
        if len(_rate) > 20000:
            _rate.clear()
    return ok


def client_events(events: list[dict], uid: int | None, platform: str | None) -> int:
    """İstemcinin olayları: bilinen istemci kodları kabul; ekran yolu ve özet süzülür. Kabul edilen sayı."""
    n = 0
    for ev in events:
        code = ev.get("code")
        if code not in CLIENT_CODES:
            continue
        plat = ev.get("platform") if ev.get("platform") in PLATFORMS else platform
        place = ekran(ev.get("screen"))
        if code == "eklenti_hatasi" and ev.get("plugin"):  # hangi telefon özelliği: kısa ad
            place = f"{place} · {re.sub(r'[^a-z0-9_-]', '', str(ev['plugin']).lower())[:20]}"
        record(code, place, uid=uid, app_version=ev.get("app_version"), platform=plat, ozet=ev.get("detail"))
        n += 1
    return n


# ---------------------------------------------------------------- uyarı (yöneticiye)

_alert_times: list[float] = []


def _admins() -> list[dict]:
    return [u for u in system.users() if u["role"] == "admin" and not u["disabled"]]


def _alerts(touched: dict[int, dict]) -> None:
    now = time.time()
    since = _utc(now - SPIKE_WINDOW * 60)
    out: list[tuple] = []
    with system.connect() as c:
        for kid, t in touched.items():
            k = c.execute("SELECT * FROM incident_kinds WHERE id = ?", (kid,)).fetchone()
            if not k:
                continue
            sev = KODLAR.get(k["code"], ("", "bilgi"))[1]
            if sev == "bilgi":
                continue
            users = c.execute("SELECT COUNT(DISTINCT user_id), COALESCE(SUM(count), 0) FROM incidents WHERE kind_id = ? "
                              "AND last_at >= ?", (kid, since)).fetchone()
            why = None
            if t["reopened"]:
                why = "Düzeltilen sorun yeniden görüldü"
            elif t["new"] and sev == "kritik":
                why = "Yeni hata türü"
            elif users[0] >= SPIKE_USERS:
                why = "Ani artış"
            if not why:
                continue
            if k["alerted_at"] and now - datetime.strptime(k["alerted_at"], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc).timestamp() < ALERT_GAP:
                continue
            _alert_times[:] = [x for x in _alert_times if now - x < 3600]
            if len(_alert_times) >= ALERT_MAX:
                log.warning("sorun uyarısı sınırı doldu: %s", k["code"])
                break
            _alert_times.append(now)
            c.execute("UPDATE incident_kinds SET alerted_at = ? WHERE id = ?", (_utc(now), kid))
            out.append((dict(k), why, users[0], users[1]))
    for a in out:  # gönderim bağlantı kapandıktan sonra (push kendi bağlantısıyla yazar)
        _send_alert(*a)


def _send_alert(k: dict, why: str, users: int, count: int) -> None:
    from app import push

    label = KODLAR[k["code"]][0]
    title = f"{why}: {label}"[:120]
    sample = _json(k.get("sample"))
    body = f"{k['place']} · son {SPIKE_WINDOW} dk: {users} kişi, {count} kez" + (f" · {sample['ozet']}" if sample.get("ozet") else "")
    pid = f"sorun:{k['id']}:{_utc()[:13]}"
    for a in _admins():
        try:
            push.notify(a["id"], [push.item("sistem", pid, title, body, R_SORUNLAR)])
        except Exception:  # noqa: BLE001
            log.exception("sorun uyarısı push")
    try:
        _telegram(f"⚠️ {html.escape(title)}\n{html.escape(body)}")
    except Exception:  # noqa: BLE001
        log.exception("sorun uyarısı telegram")


def _telegram(text: str) -> None:
    from app import auth, db, telegram

    a = next(iter(_admins()), None)
    if not a and not auth.disabled():
        return
    with db.use_space(system.user_db_path(a) if a else None, a):
        if telegram.enabled():
            telegram.send(text[:4000])


# ---------------------------------------------------------------- yönetici: liste, ayrıntı, durum

def _json(s) -> dict:
    try:
        v = json.loads(s or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _kind_out(k, stats: dict | None = None) -> dict:
    label, sev, source, notice = KODLAR.get(k["code"], (k["code"], "bilgi", "sunucu", None))
    return {"id": k["id"], "code": k["code"], "label": label, "severity": sev, "source": source, "place": k["place"],
            "status": k["status"], "note": k["note"], "fixed_version": k["fixed_version"], "fixed_at": k["fixed_at"],
            "user_text": k["user_text"], "notified": k["notified"], "notified_at": k["notified_at"],
            "reopened": k["reopened"], "first_seen": k["first_seen"], "last_seen": k["last_seen"],
            "sample": _json(k["sample"]), "notifiable": notice is not None,
            "notice": fix_text(k["code"], k["user_text"]), **(stats or {})}


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def list_kinds(status: str | None = None, days: int = 30, severity: str | None = None) -> dict:
    flush()
    since = _since(days)
    hour = _utc(time.time() - SPIKE_WINDOW * 60)
    with system.connect() as c:
        stats = {r["kind_id"]: {"users": r["users"], "count": r["n"], "anon": r["anon"], "last_hour_users": r["lh"]}
                 for r in c.execute(
                     "SELECT kind_id, COUNT(DISTINCT CASE WHEN user_id > 0 THEN user_id END) AS users, SUM(count) AS n, "
                     "SUM(CASE WHEN user_id = 0 THEN count ELSE 0 END) AS anon, "
                     "COUNT(DISTINCT CASE WHEN last_at >= ? AND user_id > 0 THEN user_id END) AS lh "
                     "FROM incidents WHERE day >= ? GROUP BY kind_id", (hour, since))}
        vers: dict[int, dict[str, int]] = {}
        plats: dict[int, dict[str, int]] = {}
        for r in c.execute("SELECT kind_id, COALESCE(app_version, '') AS v, COALESCE(platform, '') AS p, SUM(count) AS n "
                           "FROM incidents WHERE day >= ? GROUP BY kind_id, v, p", (since,)):
            vers.setdefault(r["kind_id"], {})
            vers[r["kind_id"]][r["v"] or "bilinmiyor"] = vers[r["kind_id"]].get(r["v"] or "bilinmiyor", 0) + r["n"]
            plats.setdefault(r["kind_id"], {})
            plats[r["kind_id"]][r["p"] or "bilinmiyor"] = plats[r["kind_id"]].get(r["p"] or "bilinmiyor", 0) + r["n"]
        rows = c.execute("SELECT * FROM incident_kinds WHERE last_seen >= ? ORDER BY last_seen DESC", (since,)).fetchall()
        counts = {s: 0 for s in STATUSES}
        items = []
        for k in rows:
            counts[k["status"]] = counts.get(k["status"], 0) + 1
            if status and k["status"] != status:
                continue
            if severity and KODLAR.get(k["code"], ("", "bilgi"))[1] != severity:
                continue
            st = stats.get(k["id"], {"users": 0, "count": 0, "anon": 0, "last_hour_users": 0})
            items.append(_kind_out(k, {**st,
                                       "versions": _top(vers.get(k["id"], {})), "platforms": _top(plats.get(k["id"], {}))}))
    order = {"yeni": 0, "inceleniyor": 1, "duzeltildi": 2}
    sev_order = {"kritik": 0, "uyari": 1, "bilgi": 2}
    items.sort(key=lambda x: (order.get(x["status"], 3), sev_order.get(x["severity"], 3), -x["users"], -x["count"]))
    return {"items": items, "counts": counts, "days": days,
            "thresholds": {"spike_users": SPIKE_USERS, "spike_minutes": SPIKE_WINDOW, "alert_gap_minutes": ALERT_GAP // 60,
                           "keep_days": KEEP_DAYS, "notify_days": NOTIFY_DAYS}}


def _top(d: dict[str, int], n: int = 6) -> list[dict]:
    return [{"key": k, "n": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]


def _names() -> dict[int, str]:
    return {u["id"]: u["username"] for u in system.users()}


def kind_detail(kid: int, days: int = 30) -> dict | None:
    flush()
    since = _since(days)
    with system.connect() as c:
        k = c.execute("SELECT * FROM incident_kinds WHERE id = ?", (kid,)).fetchone()
        if not k:
            return None
        per_day = [{"day": r["day"], "count": r["n"], "users": r["u"]} for r in c.execute(
            "SELECT day, SUM(count) AS n, COUNT(DISTINCT CASE WHEN user_id > 0 THEN user_id END) AS u FROM incidents "
            "WHERE kind_id = ? AND day >= ? GROUP BY day ORDER BY day", (kid, since))]
        urows = c.execute("SELECT user_id, SUM(count) AS n, MIN(first_at) AS f, MAX(last_at) AS l, MAX(app_version) AS v, "
                          "MAX(platform) AS p FROM incidents WHERE kind_id = ? AND day >= ? GROUP BY user_id "
                          "ORDER BY l DESC LIMIT 100", (kid, since)).fetchall()
    names = _names()
    users = [{"user_id": r["user_id"] or None, "username": names.get(r["user_id"]) if r["user_id"] else None,
              "count": r["n"], "first_at": r["f"], "last_at": r["l"], "app_version": r["v"], "platform": r["p"]}
             for r in urows]
    tot = {"users": sum(1 for u in users if u["user_id"]), "count": sum(u["count"] for u in users)}
    return {**_kind_out(k, tot), "days": per_day, "people": users}


def user_timeline(uid: int, days: int = 30) -> dict:
    flush()
    with system.connect() as c:
        rows = c.execute("SELECT i.*, k.code, k.place, k.status FROM incidents i JOIN incident_kinds k ON k.id = i.kind_id "
                         "WHERE i.user_id = ? AND i.day >= ? ORDER BY i.last_at DESC LIMIT 300", (uid, _since(days))).fetchall()
    u = system.user(uid)
    return {"user": {"id": uid, "username": u["username"] if u else None},
            "items": [{"kind_id": r["kind_id"], "code": r["code"], "label": KODLAR.get(r["code"], (r["code"],))[0],
                       "place": r["place"], "status": r["status"], "day": r["day"], "count": r["count"],
                       "first_at": r["first_at"], "last_at": r["last_at"], "app_version": r["app_version"],
                       "platform": r["platform"]} for r in rows]}


def fix_text(code: str, custom: str | None = None, update: bool = False) -> str | None:
    """Kullanıcıya gidecek "düzelttik" metni."""
    notice = KODLAR.get(code, (None, None, None, None))[3]
    if not notice and not custom:
        return None
    head = (custom or notice).strip().rstrip(".")
    out = f"{head} düzelttik; tekrar deneyebilirsin." if not custom else f"{head}."
    return out + (" Önce uygulamayı güncelle." if update else "")


def _turkce(text: str) -> bool:
    """Kullanıcıya gidecek metin Türkçe mi (harf ya da sık kelime; errors._TR büyük/küçük harf duyarsız "i"yi de sayar)."""
    return bool(re.search(r"[çğıöşüÇĞİÖŞÜ]", text) or re.search(
        r"\b(ve|bir|bu|ile|için|tekrar|sorun|hata|düzelt\w*|artık|yeniden|deneyebilirsin)\b", text, re.I))


class IncidentError(ValueError):
    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


def update_kind(kid: int, status: str | None = None, note: str | None = None, fixed_version: str | None = None,
                user_text: str | None = None, notify_users: bool = True) -> dict | None:
    """Durum / not / düzeltme sürümü / kullanıcı metni. "duzeltildi"ye geçişte (notify_users) kullanıcılara haber.
    Dönen: {kind, notified: kişi sayısı | None}."""
    from app import errors

    if status is not None and status not in STATUSES:
        raise IncidentError("Geçersiz durum")
    if fixed_version:
        if not _ver(fixed_version):
            raise IncidentError("Sürüm 1.0.9 biçiminde olmalı")
        fixed_version = surum(fixed_version)
    if user_text is not None and user_text.strip():
        user_text = user_text.strip()
        if len(user_text) > 160 or not _turkce(user_text) or errors._TECH.search(user_text):
            raise IncidentError("Kullanıcıya gidecek metin Türkçe ve en fazla 160 karakter olmalı")
    with system.connect() as c:
        k = c.execute("SELECT * FROM incident_kinds WHERE id = ?", (kid,)).fetchone()
        if not k:
            return None
        sets, args = [], []
        for col, v in (("note", note), ("fixed_version", fixed_version), ("user_text", user_text)):
            if v is not None:
                sets.append(f"{col} = ?")
                args.append(v.strip()[:2000] or None if isinstance(v, str) else v)
        fixing = status == "duzeltildi" and k["status"] != "duzeltildi"
        if status is not None:
            sets.append("status = ?")
            args.append(status)
            if fixing:
                sets += ["fixed_at = ?", "fix_seq = fix_seq + 1"]
                args.append(_utc())
        if sets:
            c.execute(f"UPDATE incident_kinds SET {', '.join(sets)} WHERE id = ?", (*args, kid))
    sent = notify_fixed(kid) if fixing and notify_users else None
    return {"kind": kind_detail(kid), "notified": sent}


def notify_fixed(kid: int) -> int:
    """Türü son NOTIFY_DAYS günde yaşamış (oturumlu, etkin) kişilere bir kez "duzeltme" push'u. Kişi sayısı."""
    from app import push

    with system.connect() as c:
        k = c.execute("SELECT * FROM incident_kinds WHERE id = ?", (kid,)).fetchone()
        if not k or not fix_text(k["code"], k["user_text"]):
            return 0
        rows = c.execute("SELECT user_id, MAX(app_version) AS v, MAX(platform) AS p FROM incidents WHERE kind_id = ? AND "
                         "user_id > 0 AND day >= ? AND last_at <= ? GROUP BY user_id",
                         (kid, _since(NOTIFY_DAYS), k["fixed_at"] or _utc())).fetchall()
    fixed = _ver(k["fixed_version"])
    route = route_for(k["code"], k["place"])
    pid = f"duzeltme:{kid}:{k['fix_seq']}"
    n = 0
    for r in rows:
        u = system.user(r["user_id"])
        if not u or u["disabled"] or u.get("deleted_at"):
            continue
        old = bool(fixed and r["p"] in ("ios", "android") and _older(r["user_id"], r["v"], fixed))
        body = fix_text(k["code"], k["user_text"], update=old)
        try:
            if push.notify(u["id"], [push.item("duzeltme", pid, "Sorun düzeltildi", body, route)]):
                n += 1
        except Exception:  # noqa: BLE001
            log.exception("düzeltme bildirimi")
    with system.connect() as c:
        c.execute("UPDATE incident_kinds SET notified = notified + ?, notified_at = ? WHERE id = ?", (n, _utc(), kid))
    return n


def _older(uid: int, seen: str | None, fixed: tuple[int, ...]) -> bool:
    """Kişinin telefonu düzeltme sürümünün altında mı: cihazların bildirdiği en yeni sürüm (yoksa hatadaki sürüm)."""
    from app import push

    best = max((v for v in (_ver(d.get("app_version")) for d in push.devices(uid)) if v), default=None) or _ver(seen)
    return bool(best and best < fixed)


ROUTES = {"ekstre": "#/diger/ekstreler", "dokum": "#/diger/ekstreler", "fis": "#/ekle", "fatura": "#/diger/faturalar",
          "dekont": "#/diger/ekstreler", "belge": "#/ekle", "foto": "#/ekle"}


def route_for(code: str, place: str) -> str:
    """Düzeltme bildiriminin açacağı ekran: istemci türünde hatanın ekranı, okuma türlerinde ilgili ekran, yoksa Özet."""
    if KODLAR.get(code, ("", "", ""))[2] == "istemci" and place and place != "-" and ":" not in place.split(" · ")[0]:
        return "#/" + place.split(" · ")[0]
    return ROUTES.get(code.split("_")[0], "#/ozet")


# ---------------------------------------------------------------- saklama

def prune(now: float | None = None) -> int:
    """KEEP_DAYS günden eski sayaçlar ve o tarihten beri görülmeyen türler."""
    cut = (datetime.fromtimestamp(now or time.time(), timezone.utc) - timedelta(days=KEEP_DAYS))
    with system.connect() as c:
        n = c.execute("DELETE FROM incidents WHERE day < ?", (cut.strftime("%Y-%m-%d"),)).rowcount
        c.execute("DELETE FROM incident_kinds WHERE last_seen < ? AND id NOT IN (SELECT DISTINCT kind_id FROM incidents)",
                  (cut.strftime("%Y-%m-%d %H:%M:%S"),))
    return n


def forget_user(uid: int) -> int:
    """Hesap silinince: kişinin sayaçları (ve tamponda bekleyenleri)."""
    with _lock:
        _buf[:] = [(p, e) for p, e in _buf if e["uid"] != uid]
    with system.connect() as c:
        return c.execute("DELETE FROM incidents WHERE user_id = ?", (uid,)).rowcount


def has_notes() -> bool:
    ctx = _ctx.get()
    return bool(ctx and ctx["notes"])
