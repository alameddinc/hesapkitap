"""FastAPI JSON API. Frontend (web/) /api altından konuşur.
Production'da WEB_DIST tanımlıysa derlenmiş React uygulamasını da aynı porttan sunar."""
import asyncio
import logging
import threading
import mimetypes
import os
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import AfterValidator, BaseModel, Field, model_validator

from app import auth, backup, config, db, deletion, donations, errors, finance, idempotency, jar_goal, jobs, limits, money, ocr, plans, prefs, receipt, receipt_check, receipt_verify, system, telegram, zakat
from app.categorize import CATEGORIES, suggest_pattern
from app.parsers import parse_statement
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND

from app import logs as _logs  # noqa: E402

_logs.install()  # günlükte kişisel veri maskeleme, erişim günlüğünde sorgu dizgesi yok (S8)
db.init_db()
auth.ensure_admin()


def _migrate_user_dbs() -> None:
    """Açılışta her kullanıcının veritabanına göçleri uygula (yeni kolonlar; kullanıcı giriş yapmasını beklemeden)."""
    import logging

    for u in system.users():
        path = system.user_db_path(u) if u.get("db_file") else None
        if path and os.path.exists(path):
            try:
                with db.use_space(path, u):
                    pass  # use_space ilk kullanımda init_db çalıştırır
            except Exception:  # noqa: BLE001 - bir kullanıcının dosyası diğerlerini engellemesin
                logging.getLogger("ekstre").exception("kullanıcı veritabanı göçü: u%s", u.get("id"))


_migrate_user_dbs()


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(jobs.scheduler()) if os.environ.get("JOBS_ENABLED", "1") == "1" else None
    yield
    if task:
        task.cancel()


# Belge okuma (pdfplumber, OCR, yapay zekâ) bellek yer: toplu yüklemede 30 dosya aynı anda okununca kapsayıcı bellek
# sınırını aşıp düşüyordu (OOM → "Sunucuya ulaşılamadı"). Aynı anda en fazla N belge okunur; gerisi sırada bekler.
_HEAVY = threading.BoundedSemaphore(max(1, int(os.environ.get("HESAPKITAP_PARSE_SLOTS", "2"))))


def _heavy(fn):
    """Ağır okuma yuvası. Sırada HESAPKITAP_PARSE_WAIT saniyeden uzun beklenirse 503: istek iş parçacığını sonsuza
    dek tutmaz, istemci (Cloudflare 100 sn) zaman aşımına düşüp aynı belgeyi yeniden göndermez."""
    import functools

    from app import limits

    @functools.wraps(fn)
    def run(*a, **kw):
        if not _HEAVY.acquire(timeout=limits.parse_wait()):
            raise HTTPException(503, "Sunucu şu an başka belgeleri okuyor; biraz sonra tekrar dene")
        try:
            return fn(*a, **kw)
        finally:
            _HEAVY.release()
    return run


# TL tutarı: API sınırında yarım yukarı 2 ondalığa yuvarlanır (kuruş altı tutar saklanmasın; app/money.py).
# Miktar olabilen alanlar (altın/döviz borcu, birimli kumbara, gram) bilerek float kalır.
Money = Annotated[float, AfterValidator(money.money)]


app = FastAPI(title="HesapKitap", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


MACHINE_CODES = {"consent_required": "Devam etmek için koşulları ve açık rızayı onayla."}


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    """Geçersiz istek gövdesi/parametre: Türkçe tek cümle (istemciler detail metnini gösterir). errors: alan ve tür."""
    errs = exc.errors()
    return JSONResponse({"detail": errors.validation_text(errs),
                         "errors": [{"loc": list(e.get("loc", ())), "type": e.get("type")} for e in errs]}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    """Çıplak İngilizce (Starlette'in "Not Found", "Method Not Allowed") ve boş metinler Türkçe; makine kodlarına
    (consent_required) code ve message eklenir (istemci detail'e göre davranmaya devam eder)."""
    detail = exc.detail
    extra = {}
    if exc.status_code == 404 and detail == "Not Found":
        detail = "Böyle bir adres yok; uygulama güncel değil olabilir."
    elif isinstance(detail, str) and detail in MACHINE_CODES:
        extra = {"code": detail, "message": MACHINE_CODES[detail]}
    elif not detail or detail in ("Method Not Allowed", "Internal Server Error", "Bad Request", "Unauthorized",
                                  "Forbidden", "Conflict", "Unprocessable Entity", "Unprocessable Content"):
        detail = errors.GENERIC.get(exc.status_code, errors.GENERIC[400 if exc.status_code < 500 else 500])
    if exc.status_code == 404:
        extra["refresh"] = True  # istemci listeyi tazelesin (kayıt başka cihazda silinmiş/değişmiş olabilir)
    return JSONResponse({"detail": detail, **extra}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(auth.Busy)
async def _auth_busy(request: Request, exc: auth.Busy):
    return JSONResponse({"detail": "Sunucu şu an çok yoğun; birkaç saniye sonra tekrar dene."}, status_code=503,
                        headers={"Retry-After": "5"})


def _attempt_gate(request: Request) -> None:
    """Şifre hesabı (PBKDF2) gerektiren kimliksiz uçlar: IP başı deneme sınırı (sonuçtan bağımsız, S18)."""
    if auth.attempt_limited(client_ip(request)):
        raise HTTPException(429, "Bu bağlantıdan çok fazla deneme yapıldı. 15 dakika sonra tekrar dene.")


def _signup_daily_full() -> bool:
    """Hazır sürüm: sunucu geneli günlük yeni hesap sınırı (toplu sahte hesap; HESAPKITAP_SIGNUP_DAILY, varsayılan
    300; 0 = sınırsız). Kendi sunucusunda yok."""
    if not plans.hosted():
        return False
    cap = int(os.environ.get("HESAPKITAP_SIGNUP_DAILY", "300"))
    if not cap:
        return False
    with system.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM users WHERE created_at >= date('now')").fetchone()[0]
    return n >= cap


SIGNUP_FULL = "Bugün çok sayıda yeni hesap açıldı; kayıt yarın yeniden açılacak. Davet kodun varsa onunla kayıt olabilirsin."


@app.exception_handler(limits.LimitError)
async def _limit_error(request: Request, exc: limits.LimitError):
    """Kaynak sınırı (app/limits.py) hangi uçta aşılırsa aşılsın anlamlı Türkçe 413."""
    return JSONResponse({"detail": str(exc)}, status_code=413)


class FastJSON(JSONResponse):
    """Hazır JSON (yalnız str/int/float/bool/None/list/dict içeren içerik): FastAPI'nin jsonable_encoder'ı atlanır —
    büyük listelerde sürenin çoğu oradaydı. Çıktı JSONResponse ile aynı biçim."""


def _tl_tr(x: float) -> str:
    """Hata metinleri için: ₺1.234,50 (money.tl, istemcilerle aynı biçim)."""
    return money.tl(x)


def client_ip(request: Request) -> str:
    """Başlıklara yalnız güvenilir vekilden gelince inanılır (HESAPKITAP_TRUSTED_PROXIES; ayrıntı: app/netaddr.py)."""
    from app import netaddr

    return netaddr.client_ip(request.client.host if request.client else None, request.headers)


def is_local_request(request: Request) -> bool:
    """Kurulum kodu muafiyeti: tüm adaylar (soket dahil) yerel olmalı. X-Real-IP'yi ezmeden geçiren bir vekil
    gerçek adresi X-Forwarded-For'a yazıyorsa, uydurulmuş X-Real-IP: 127.0.0.1 tek başına yetmez."""
    from app import netaddr

    return all(auth.is_local(c) for c in netaddr.candidates(request.client.host if request.client else None,
                                                             request.headers))


from app.compress import GZip  # noqa: E402

app.add_middleware(GZip)  # JS ~880 KB → ~250 KB; API yanıtları da küçülür. <1 KB, resim/PDF/zip/yedek sıkıştırılmaz

# Mobil uygulama (Capacitor): arayüz telefonun içinde (capacitor://localhost, https://localhost), API bu sunucuda.
# Çerez yok: oturum anahtarı X-Session-Token başlığıyla gelir, Authorization: Bearer ile geri gönderilir.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

APP_ORIGINS = ["capacitor://localhost", "https://localhost", "http://localhost", "http://localhost:5174"] + [
    o.strip() for o in os.environ.get("HESAPKITAP_APP_ORIGINS", "").split(",") if o.strip()]


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


async def _demo_request(request: Request, call_next):
    """Canlı demo: giriş yok; her ziyaretçi örnek verinin kendi kopyasında (1 saat), yükleme/ayarlar kapalı."""
    from app import demo_mode

    path = request.url.path
    if demo_mode.closed(path):
        return JSONResponse({"detail": "Demoda kapalı — kendi sunucuna kurunca açılır"}, status_code=403)
    # mobil uygulama çerez tutmaz: demo kopyasının kimliği X-Demo-Sid başlığıyla gider gelir
    try:
        sid, new = demo_mode.resolve(request.cookies.get(demo_mode.COOKIE) or request.headers.get("x-demo-sid"),
                                     client_ip(request))
    except demo_mode.TooManySandboxes:
        return JSONResponse({"detail": "Bu bağlantıdan çok fazla demo oturumu açıldı; biraz sonra yeniden dene."},
                            status_code=429, headers={"Retry-After": "600"})
    db_file, user = demo_mode.space(sid)
    with db.use_space(db_file, user):  # okuma sırası ve Idempotency-Key (kapsam: demo kopyası + anahtar) burada da
        response = await _run(request, call_next, db_file, path)
    response.headers["X-Demo-Sid"] = sid
    demo_mode.track(sid, path, request.method, request.headers.get("x-real-ip") or (request.client.host if request.client else ""),
                    request.headers.get("user-agent", ""), request.headers.get("x-hk-client", ""))
    if new:
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(demo_mode.COOKIE, sid, max_age=demo_mode.TTL, httponly=True, secure=https, samesite="lax", path="/")
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Robots-Tag", "noindex")
    return response


def _book_choice(request: Request) -> str:
    """Üyenin bu istekte açtığı defter: "kendi" ("Benim") ya da "ortak". Açık başlık (X-HK-Book: kendi | ortak) çerezden
    (hk_book, web'in kalıcı seçimi) önce gelir: istemci tek isteği öbür deftere yazabilir (fiş onayında "Deftere").
    Başlık yoksa ya da tanınmıyorsa çerez."""
    h = (request.headers.get("x-hk-book") or "").strip().lower()
    if h in ("kendi", "ortak"):
        return h
    return "kendi" if request.cookies.get("hk_book") == "kendi" else "ortak"


def _access(request: Request, method: str, path: str, user: dict) -> "tuple[dict, str | None] | JSONResponse":
    """Oturumu doğrulanmış kullanıcı bu isteği yapabilir mi? Tek yer: /api/ middleware'i ve /api/ dışındaki yazma
    uçları (/share-target) aynı kontrolden geçer: zorunlu şifre değişimi, paket kapısı, ortak defter yetkisi ve
    kuralları. İzin varsa (kullanıcı, veritabanı dosyası); yoksa hazır hata yanıtı."""
    # yöneticinin verdiği geçici şifreyle girenler önce kendi şifresini belirler
    if user["must_change_password"] and path != "/api/settings/password":
        return JSONResponse({"detail": "password_change_required", "code": "password_change_required",
                             "message": "Önce kendi şifreni belirle."}, status_code=403)
    feature = plans.gate_for(path, method)
    if feature and not plans.allows(user, feature):  # barındırılan sürüm: paketinde olmayan özellik
        return JSONResponse({"detail": str(plans.PlanRequired(feature)), "plan_required": feature}, status_code=402)
    db_file = system.user_db_path(user)
    if user.get("member_of") and _book_choice(request) == "kendi":
        # ortak defter üyesi kendi özel defterinde: kurallar yok, tam yetki; paket yine defter sahibinden
        return {**user, "own_book": True}, system.own_db_path(user)
    if user.get("member_of"):  # ortak defter üyesi: yetkisi ve sahibin kurallarıyla sınırlı
        from app import book

        with db.use_space(db_file, user):
            why = book.denied(method, path)
            hidden = not why and book.hidden_target(path)
        if why:
            return JSONResponse({"detail": why}, status_code=403)
        if hidden:  # gizli kayıt/ekstre/hesap: var olduğu da belli olmasın
            return JSONResponse({"detail": "Bulunamadı"}, status_code=404)
    return user, db_file


def _visible_or_404(kind: str, oid: int | None) -> None:
    """Gövdede gelen id (bağlanacak işlem, ödemenin çıktığı hesap…) üyenin görebildiği bir nesne mi?"""
    from app import book

    if not book.visible(kind, oid):
        raise HTTPException(404, "Bulunamadı")


# Okuma sırası. Aynı süreçte eşzamanlı ağır okumalar (Özet açılışında 14 GET) GIL ve SQLite çekişmesiyle 2–5 kat
# yavaşlıyordu; sırayla çalışınca hem toplam süre hem her isteğin süresi kısalıyor (önbellek de ilk istekte dolar).
# Defter başına 1, süreç geneli HESAPKITAP_READ_SLOTS (varsayılan 2): bir kullanıcının fırtınası diğerini durdurmaz.
# Sırada bekleyen istek iş parçacığı tutmaz (asyncio). Yazmalar sıraya girmez.
_READ_BOOK: dict = {}
_READ_ALL: dict = {}


class _read_slot:
    def __init__(self, book: str | None):
        loop = asyncio.get_running_loop()  # semaforlar döngüye bağlı (testler kendi döngüsünü açar)
        key = (id(loop), book or "")
        self.book = _READ_BOOK.setdefault(key, asyncio.Semaphore(1))
        self.all = _READ_ALL.setdefault(id(loop), asyncio.Semaphore(max(1, int(os.environ.get("HESAPKITAP_READ_SLOTS", "2")))))

    async def __aenter__(self):
        await self.book.acquire()
        try:
            await self.all.acquire()
        except BaseException:
            self.book.release()
            raise

    async def __aexit__(self, *exc):
        self.all.release()
        self.book.release()


async def _run(request: Request, call_next, book: str | None, path: str):
    from app import doc_reads

    with doc_reads.scope():  # okuma hakkı ayırmaları bu isteğe ait; istek bitince sayılmayanlar silinir
        return await _run_inner(request, call_next, book, path)


async def _run_inner(request: Request, call_next, book: str | None, path: str):
    if request.method == "GET" and path.startswith("/api/"):
        async with _read_slot(book):
            return await call_next(request)
    if request.method in idempotency.METHODS and path.startswith("/api/") and request.headers.get("idempotency-key"):
        return await _idempotent(request, call_next)
    return await call_next(request)


async def _idempotent(request: Request, call_next):
    """Idempotency-Key'li yazma (app/idempotency.py): ilk istek işlenir ve yanıtı saklanır; tekrarı ilk yanıtı alır."""
    from fastapi.responses import Response as RawResponse

    key = request.headers["idempotency-key"].strip()
    fp = idempotency.fingerprint(request.method, request.url.path, request.url.query, await request.body(),
                                 request.headers.get("content-type"))
    uid = (db.current_user() or {}).get("id")
    for _ in range(2):  # önceki deneme sunucu hatasıyla bittiyse kayıt silinmiştir: bu istek yeniden ilk sayılır
        try:
            row = idempotency.begin(uid, key, fp)
        except idempotency.KeyError_ as e:
            return JSONResponse({"detail": str(e)}, status_code=e.status)
        if row is None:
            break
        loop = asyncio.get_running_loop()
        end = loop.time() + idempotency.WAIT
        while row and row["state"] == "running" and loop.time() < end:
            await asyncio.sleep(0.1)
            row = idempotency.get(uid, key)
        if row is None:
            continue
        if row["state"] == "running":
            return JSONResponse({"detail": "İşlem sürüyor; birkaç saniye sonra listeyi yenile, tekrar gönderme."},
                                status_code=409, headers={"Retry-After": "2"})
        return RawResponse(row["body"] or b"", status_code=row["status"], media_type=row["content_type"],
                           headers={"Idempotent-Replayed": "true"})
    try:
        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])
    except BaseException:
        idempotency.abort(uid, key)
        raise
    idempotency.finish(uid, key, response.status_code, body, response.headers.get("content-type"))
    out = RawResponse(body, status_code=response.status_code)
    out.raw_headers = [h for h in out.raw_headers if h[0] == b"content-length"] + [
        h for h in response.raw_headers if h[0] != b"content-length"]  # çoklu Set-Cookie korunur
    return out


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Tek ara katman (oturum, defter, okuma sırası, Idempotency-Key). İşlenmemiş hata burada Türkçe 500'e çevrilir:
    yoksa Starlette'in en dıştaki hata katmanı CORS'un DIŞINDA yanıt üretir, mobil uygulama başlıksız yanıtı CORS
    hatası ("Sunucuya ulaşılamadı") sanar. Bütün erken dönüşler (401/402/403/404/409/413/422/429/503) zaten CORS içinde."""
    mark = config.mark_request()  # oturumsuz istekte config.is_admin() False (D3)
    try:
        return await _require_login(request, call_next)
    except Exception:  # noqa: BLE001
        logging.getLogger("hesapkitap.api").exception("işlenmemiş hata: %s %s", request.method, request.url.path)
        return JSONResponse({"detail": errors.GENERIC[500]}, status_code=500)
    finally:
        config._in_request.reset(mark)


# bu önekli uçlarda başarılı yazmadan sonra o defterde doğruluk denetimi yeniden çalışır (app/integrity.after_write)
_INTEGRITY_WRITES = ("/api/entries", "/api/own-transfers", "/api/debts", "/api/debt-payments", "/api/statements",
                     "/api/bank-import", "/api/imports", "/api/accounts", "/api/card-transactions", "/api/splits",
                     "/api/inbox", "/api/bills", "/share-target", "/api/share", "/api/dekont")
CSRF_ORIGINS = {o.rstrip("/") for o in APP_ORIGINS} | {
    o.strip().rstrip("/") for o in os.environ.get("HESAPKITAP_CSRF_ORIGINS", "").split(",") if o.strip()}
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _origin_of(url: str | None) -> str | None:
    from urllib.parse import urlsplit

    if not url or url == "null":
        return None
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower() if u.scheme and u.netloc else None


# Sunucudan sunucuya, çerezsiz çağrılan uçlar: kimlik doğrulaması kendi içinde (Apple: imzalı JWT). Tarayıcı başlığı
# taşımasa da (bugün geçer) bir vekil Origin eklerse reddedilmesin diye açık istisna.
CSRF_EXEMPT = frozenset({"/api/auth/apple/notifications"})


def _csrf_ok(request: Request) -> bool:
    """Çerezle (tarayıcı) gelen yazma isteği başka siteden mi (S9)? SameSite=Lax tek başına yetmez: aynı alan adındaki
    kardeş siteler 'aynı site' sayılır. Bearer'lı istek (mobil, Kısayollar) çerez taşımaz: denetlenmez.
    İzinli: isteğin kendi adresi (Host), HESAPKITAP_PUBLIC_URL, uygulama kökenleri (APP_ORIGINS) ve
    HESAPKITAP_CSRF_ORIGINS. Sec-Fetch-Site same-origin/none (adres çubuğu, paylaşım hedefi) geçer; same-site ve
    cross-site yalnız izinli Origin'le. Başlık hiç yoksa (eski tarayıcı, sunucudan sunucuya çağrı) geçer."""
    if request.method not in _UNSAFE or _bearer(request) or request.url.path in CSRF_EXEMPT:
        return True
    sfs = (request.headers.get("sec-fetch-site") or "").lower()
    if sfs in ("same-origin", "none"):
        return True
    origin = _origin_of(request.headers.get("origin")) or (
        _origin_of(request.headers.get("referer")) if not request.headers.get("origin") else None)
    if origin is None:
        return not sfs and request.headers.get("origin") in (None, "")  # 'Origin: null' (sandbox iframe) geçmez
    host = (request.headers.get("host") or request.url.netloc).lower()
    allowed = {o.lower() for o in CSRF_ORIGINS} | {f"https://{host}", f"http://{host}"}
    if pub := os.environ.get("HESAPKITAP_PUBLIC_URL"):
        allowed.add((_origin_of(pub) or "").lower())
    return origin in allowed


async def _require_login(request: Request, call_next):
    path = request.url.path
    user = None
    from app import demo_mode

    if not _csrf_ok(request):
        return JSONResponse({"detail": "Bu istek başka bir siteden geldiği için reddedildi; sayfayı yenileyip "
                                       "uygulamanın kendisinden tekrar dene.", "code": "csrf"}, status_code=403)

    if demo_mode.enabled() and path == "/api/demo/stats":  # yönetici paneli çeker (anahtarla); sandbox açmaz, sayılmaz
        return await call_next(request)
    if demo_mode.enabled() and path.startswith("/api/") and path != "/api/auth/status":
        return await _demo_request(request, call_next)
    if demo_mode.enabled() and request.method == "GET" and "." not in path.rsplit("/", 1)[-1]:
        # sayfa açılışı: sandbox çerezi HTML ile birlikte verilir; yoksa aynı anda giden ilk API istekleri
        # her biri ayrı kopya açardı
        try:
            sid, new = demo_mode.resolve(request.cookies.get(demo_mode.COOKIE), client_ip(request))
        except demo_mode.TooManySandboxes:  # sayfa yine açılır; API istekleri 429 alır
            sid, new = "", False
        response = await call_next(request)
        if new:
            https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
            response.set_cookie(demo_mode.COOKIE, sid, max_age=demo_mode.TTL, httponly=True, secure=https, samesite="lax", path="/")
        return response
    # şablon gönderimi (hub) ve gelen posta (Cloudflare Email Worker, kendi gizli anahtarıyla): oturumsuz
    public = path.startswith("/api/auth/") or path in ("/api/templates/submit", "/api/mail/inbound", "/api/billing/revenuecat",
                                                       "/api/me/restore", "/api/regions/demand", "/api/ops/alert")  # geri alma: oturum yok, giriş belgesiyle
    if auth.enabled() and path.startswith("/api/") and not public:
        if auth.setup_required():
            return JSONResponse({"detail": "setup_required", "code": "setup_required",
                                 "message": "Önce kurulumu tamamla (yönetici şifresi)."}, status_code=401)
        user = auth.token_user(request.cookies.get(auth.COOKIE))
        if not user and _bearer(request):  # mobil uygulama: oturum anahtarı başlıkta
            user = auth.token_user(_bearer(request))
        if not user and path == "/api/share":  # telefondan Paylaş (iOS Kısayollar): kişisel anahtar, sadece bu uçta
            user = system.token_owner(_bearer(request) or "") if _bearer(request) else None
        if not user and path == "/api/feedback":  # "Sorun bildir" giriş ekranından da: oturumsuz gider (user_id yok)
            pass
        elif not user:
            return JSONResponse({"detail": "Giriş gerekli"}, status_code=401)
        else:
            gate = _access(request, request.method, path, user)
            if isinstance(gate, JSONResponse):
                return gate
            user, db_file = gate
    if user:  # isteğin tamamı bu kullanıcının veritabanında çalışır
        with db.use_space(db_file, user):
            response = await _run(request, call_next, db_file, path)
    else:
        response = await _run(request, call_next, None, path)
    if user:  # Ölçüm (app/funnel.py): kişinin bugünü bir kez, kayıt sayısı en çok 5; hata isteği bozmaz
        from app import funnel

        funnel.touch(user, request.method, path, response.status_code, request.headers)
    if request.method in _UNSAFE and response.status_code < 400 and path.startswith(_INTEGRITY_WRITES):
        from app import integrity  # düzeltilen sorunun Yapılacaklar maddesi hemen düşsün (arka planda, gecikmeli)

        integrity.after_write(db_file if user else None, user)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif path.startswith("/assets/"):  # adında hash olan derleme dosyaları değişmez
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response


class LoginIn(BaseModel):
    username: str | None = None  # boş: yönetici (eski tek kullanıcılı giriş)
    password: str


class SetupIn(BaseModel):
    username: str = "admin"
    password: str
    code: str | None = None
    share_templates: bool = True  # yeni bankaların okuma kuralını toplulukla paylaş (kişisel veri değil)


class PasswordChange(BaseModel):
    current: str
    new: str


def _apple_enabled() -> bool:
    from app import social

    return social.apple_enabled()


def _google_enabled() -> bool:
    from app import social

    return social.google_enabled()


def _email_unverified(uid) -> bool:
    from app import email_verify

    return email_verify.unverified(uid)


def _reset_enabled() -> bool:
    from app import reset

    return reset.enabled()


def _pending_deletion(u: dict) -> JSONResponse:
    """Kimliği doğrulandı ama hesap silinmek üzere bekliyor: oturum açılmaz. İstemci "geri almak ister misin?" sorar,
    evet derse restore_token ile POST /api/me/restore. Sahibi defteri sildiği için silinen üye kendi başına geri alamaz."""
    ok = deletion.restorable(u)
    return JSONResponse({"detail": "account_pending_deletion", "code": "account_pending_deletion",
                         "message": "Bu hesap silinmek üzere; geri almak istersen devam et.", "delete_after": u.get("delete_after"),
                         "deleted_at": u.get("deleted_at"), "username": u["username"], "restorable": ok,
                         "restore_token": auth.make_restore_token(u) if ok else None,
                         "subscription": plans.subscription(u)}, status_code=409)


def _start_session(request: Request, response: Response, user: dict, via: str | None = None) -> None:
    # HTTPS arkasındaysa (doğrudan ya da proxy başlığıyla) çerez otomatik Secure olur
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    from app import sessions

    mobile = request.headers.get("x-hk-client") == "mobile"
    token = auth.make_token(system.user(user["id"]), "mobile" if mobile else "web",
                            sessions.device_name(request.headers.get("user-agent"), mobile), via)
    if mobile:  # mobil uygulama: anahtarı kendisi saklar
        response.headers["X-Session-Token"] = token
        return
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, secure=auth.cookie_secure() or https, samesite="lax", path="/")


@app.get("/api/auth/status")
def auth_status(request: Request):
    """Giriş durumu + sunucu el sıkışması (api_version, capabilities, flags; girişten önce de gerekir: kayıt adımı)."""
    from app import version

    return {**_auth_status(request), **version.handshake()}


def _auth_status(request: Request):
    from app import demo_mode

    if demo_mode.enabled():
        return {"required": False, "authenticated": True, "setup_required": False, "demo": True}
    if not auth.enabled():
        return {"required": False, "authenticated": True, "setup_required": False}
    if auth.setup_required():
        local = is_local_request(request)
        if not local:
            auth.setup_code()  # log'a yazılsın
        return {"required": True, "authenticated": False, "setup_required": True, "setup_code_needed": not local}
    u = auth.token_user(request.cookies.get(auth.COOKIE) or _bearer(request))
    return {"required": True, "authenticated": bool(u), "setup_required": False, "open_signup": plans.open_signup(),
            "login": "email" if plans.hosted() else "username",
            "reset": _reset_enabled(),
            "google": _google_enabled(),
            "apple": _apple_enabled(),
            "consent": sorted(CONSENT_ITEMS) if plans.hosted() else [],  # giriş alanı: hazır sürümde e-posta; kendi sunucusunda kullanıcı adı ya da e-posta
            "legal": {"terms": os.environ.get("HESAPKITAP_TERMS_URL"), "privacy": os.environ.get("HESAPKITAP_PRIVACY_URL")},
            "password_source": auth.password_source(u) if u else None,
            "delete_grace_days": deletion.grace_days(),  # hesap silinince geri alma süresi
            "user": {"id": u.get("id"), "username": u["username"], "password_set": not auth.password_unset(u.get("id")),
                     "email_verified": not _email_unverified(u.get("id")), "role": u["role"], "must_change_password": bool(u["must_change_password"]),
                     "subscription": plans.subscription(u)} if u else None}


@app.post("/api/auth/setup")
def setup(body: SetupIn, request: Request, response: Response):
    """İlk açılış: şifre belirle. Sadece hiç şifre yokken çalışır."""
    if not auth.setup_required():
        raise HTTPException(409, "Şifre zaten belirlenmiş")
    ip = client_ip(request)
    if not is_local_request(request):
        if auth.rate_limited(ip):
            raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
        if body.code != auth.setup_code():
            auth.record_fail(ip)
            raise HTTPException(403, "Kurulum kodu yanlış. Tek tıkla kurduysan barındırma panelindeki SETUP_CODE değişkeni; Docker'da: docker compose logs | grep kurulum")
    try:
        u = auth.create_admin(body.username, body.password)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    from app import templates

    templates.set_sharing(body.share_templates)
    _start_session(request, response, u)
    return {"ok": True}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    if not auth.enabled():
        return {"ok": True}
    if auth.setup_required():
        raise HTTPException(409, "Önce şifre belirlenmeli")
    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    _attempt_gate(request)
    acct = auth.account_key(body.username)
    if auth.account_locked(acct):  # IP'den bağımsız: dağıtık deneme de hesabı geçici kilitler
        raise HTTPException(429, "Bu hesap için çok fazla hatalı deneme yapıldı. 15 dakika sonra tekrar dene.")
    u = auth.login(body.username, body.password)
    if not u:
        auth.record_fail(ip)
        auth.record_account_fail(acct)
        raise HTTPException(401, "Kullanıcı adı ya da şifre yanlış" if body.username else "Şifre yanlış")
    auth.clear_account_fails(acct)
    from app import email_verify

    if email_verify.required() and email_verify.unverified(u["id"]):  # şifre doğru, e-posta doğrulanmamış: kod gider
        try:
            email_verify.send_code(u)
        except Exception:  # noqa: BLE001
            logging.getLogger("hesapkitap.auth").exception("doğrulama postası gönderilemedi")
        return JSONResponse({"detail": "email_unverified", "code": "email_unverified", "verify_required": True,
                             "email": u["username"], "message": "E-postana gönderdiğimiz kodla hesabını doğrula."},
                            status_code=403)
    if u.get("deleted_at"):
        return _pending_deletion(u)
    _start_session(request, response, u)
    return {"ok": True}


@app.post("/api/settings/password")
def change_password(body: PasswordChange, request: Request, response: Response):
    u = db.current_user() or auth.admin()
    if auth.password_source(u) == "env":
        raise HTTPException(409, "Şifre sunucuda APP_PASSWORD ortam değişkeniyle belirlenmiş; oradan değiştir.")
    if not auth.check_password(u, body.current):
        raise HTTPException(403, "Mevcut şifre yanlış")
    try:
        auth.set_password(u["id"], body.new)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    _start_session(request, response, u)  # eski oturumlar düştü; bu cihazda oturum devam etsin
    return {"ok": True}


class RegisterIn(BaseModel):
    code: str | None = None  # barındırılan sürümde (açık kayıt) gerekmez
    username: str
    password: str
    consent: list[str] | None = None  # hazır sürümde: ["kosullar", "acik_riza"]


CONSENT_VERSION = "2026-10-30"  # hukuk metinlerinin sürümü; metin değişirse güncellenir
CONSENT_ITEMS = {"kosullar", "acik_riza"}  # Koşullar + Aydınlatma okundu · Açık rıza (yurt dışı sağlayıcılar dahil)


def _consent_ok(items: list[str] | None) -> bool:
    return not plans.hosted() or CONSENT_ITEMS <= set(items or [])


def _record_consent(uid: int, request: Request) -> None:
    """KVKK: onayın ne zaman, hangi metin sürümüne verildiği ispat edilebilmeli."""
    if plans.hosted():
        import json
        import time

        system.put(f"consent:{uid}", json.dumps({"version": CONSENT_VERSION, "items": sorted(CONSENT_ITEMS),
                                                  "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ip": client_ip(request)}))


_signups: dict[str, list[float]] = {}
_rate_lock = threading.Lock()  # _signups / _forgots: uçlar iş parçacığı havuzunda eşzamanlı çalışır


def _signup_limited(ip: str) -> bool:
    """Açık kayıt: aynı adresten saatte en fazla 5 hesap (toplu sahte hesap açılmasın)."""
    import time

    now = time.time()
    with _rate_lock:
        recent = [t for t in _signups.get(ip, []) if now - t < 3600]
        _signups[ip] = recent
        return len(recent) >= 5


def _signup_record(ip: str) -> None:
    import time

    with _rate_lock:
        _signups.setdefault(ip, []).append(time.time())


INVITE_REASONS = {"used": "Bu davet kodu kullanılmış; davet edenden yeni bağlantı iste.",
                  "expired": "Bu davetin süresi dolmuş; davet edenden yeni bağlantı iste.",
                  "unknown": "Davet kodu bulunamadı; kodu kontrol et ya da davet edenden yeni bağlantı iste."}


def _public_base(request: Request) -> str:
    """Bağlantılar için sunucunun dış adresi: HESAPKITAP_PUBLIC_URL, yoksa isteğin adresi (vekil başlığıyla https)."""
    env = os.environ.get("HESAPKITAP_PUBLIC_URL", "").strip().rstrip("/")
    if env:
        return env
    scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else request.url.scheme
    return f"{scheme}://{request.headers.get('host') or request.url.netloc}"


def _invite_out(inv: dict, request: Request) -> dict:
    """Davet + bağlantıları. link: https://<dış adres>/davet/<kod> — telefonda uygulama yüklüyse evrensel bağlantı
    (iOS apple-app-site-association, Android assetlinks.json) uygulamayı açar, yoksa web kayıt sayfasına yönlenir; QR'a
    bu yazılır. web_link: eski biçim (#/kayit?kod=, hâlâ çalışır). app_link: mobil derin bağlantı (HESAPKITAP_APP_SCHEME,
    varsayılan hesapkitap)."""
    from app import book

    code = inv["code"]
    scheme = os.environ.get("HESAPKITAP_APP_SCHEME", "hesapkitap")
    base = _public_base(request)
    out = {**inv, "link": f"{base}/davet/{code}", "web_link": f"{base}/#/kayit?kod={code}",
           "app_link": f"{scheme}://kayit?kod={code}", "kind": "book" if inv.get("member_of") else "account"}
    if inv.get("member_of"):
        out["role_label"] = book.ROLE_LABELS.get(inv.get("book_role") or "editor")
    return out


def _book_seat_or_409(inv: dict) -> None:
    """Davetle YENİ hesap: kendi aboneliği yok, Aile koltuğu gerekir (kendi Premium'u olan var olan hesapla katılır)."""
    if inv.get("member_of") and not plans.free_seat(inv["member_of"]):
        raise HTTPException(409, "Aile koltukları dolu; davet edenden yer açmasını iste")


@app.get("/api/auth/invite/{code}")
def invite_check(code: str, request: Request):
    """Davet bağlantısı açılınca (kayıttan önce): geçerli mi, hangi deftere, hangi yetkiyle. Geçersizse Türkçe neden.
    {valid, reason?, message?, kind: book|account, book?: sahibin görünen adı, role?, role_label?, expires_at?}"""
    from app import book

    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    inv, status = system.invite_status(code)
    if status != "ok":
        auth.record_fail(ip)  # kod tahmini yavaşlasın
        return {"valid": False, "reason": status, "message": INVITE_REASONS[status]}
    out = {"valid": True, "kind": "book" if inv.get("member_of") else "account", "expires_at": inv["expires_at"]}
    if inv.get("member_of"):
        owner = system.user(inv["member_of"]) or {}
        out.update(book=(owner.get("username") or "").split("@")[0], role=inv.get("book_role") or "editor",
                   role_label=book.ROLE_LABELS.get(inv.get("book_role") or "editor"),
                   **({"seats_full": not plans.free_seat(inv["member_of"])} if plans.hosted() else {}))
    return out


class SocialIn(BaseModel):
    id_token: str
    consent: list[str] | None = None  # yeni hesap açılacaksa hazır sürümde gerekli
    invite: str | None = None         # davet bağlantısıyla gelen: yeni hesap ortak deftere üye olur
    nonce: str | None = Field(None, max_length=128)  # POST /api/auth/social/nonce'tan alınan (app/social.py)
    # yalnız Apple: uygulamanın aldığı authorizationCode (tek kullanımlık, 5 dk). Sunucu refresh_token'a çevirip şifreli
    # saklar; hesap silinince Apple'da iptal eder (app/apple.py, yetenek apple_revoke). Yoksa giriş aynen sürer.
    authorization_code: str | None = Field(None, max_length=2048)


@app.post("/api/auth/social/nonce")
def social_nonce(request: Request):
    """Google/Apple girişinden önce tek kullanımlık nonce: belge bu nonce'ı taşır, sunucu bir kez kabul eder (D1)."""
    from app import social

    if auth.rate_limited(client_ip(request)):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    return {"nonce": social.issue_nonce(), "expires_in": social.NONCE_TTL}


@app.post("/api/auth/google")
def google_login(body: SocialIn, request: Request, response: Response):
    """Google ile giriş: doğrulanmış e-postayla hesap varsa ona girer, yoksa (açık kayıtta) yeni hesap açar."""
    from app import social

    return _social_login(social.verify_google, body, request, response)


@app.post("/api/auth/apple")
def apple_login(body: SocialIn, request: Request, response: Response):
    """Apple ile giriş: Google ile aynı kurallar (e-posta eşleşmesi, yeni hesapta onay)."""
    from app import social

    return _social_login(social.verify_apple, body, request, response)


@app.post("/api/auth/apple/notifications")
async def apple_notifications(request: Request):
    """Sign in with Apple sunucudan sunucuya bildirimleri (App ID'de tanımlı uç). Gövde {"payload": JWT}; imza Apple
    JWKS'iyle doğrulanır (app/apple.py). Her durumda 200: Apple yeniden denemesin; hatalar loglanır. Oturumsuz,
    CSRF istisnası (CSRF_EXEMPT)."""
    import json
    import logging

    from app import apple

    try:
        raw = await request.body()
        if len(raw) > 64 * 1024:
            raise ValueError("gövde çok büyük")
        try:
            payload = (json.loads(raw) or {}).get("payload")
        except ValueError:
            from urllib.parse import parse_qs

            payload = (parse_qs(raw.decode("utf-8", "replace")).get("payload") or [None])[0]
        if isinstance(payload, str) and payload:
            await asyncio.to_thread(apple.handle_notification, payload)
        else:
            logging.getLogger("hesapkitap.apple").warning("apple bildirimi: payload yok")
    except Exception:  # noqa: BLE001
        logging.getLogger("hesapkitap.apple").exception("apple bildirimi işlenemedi")
    return {"ok": True}


def _social_login(verify, body: SocialIn, request: Request, response: Response):
    import secrets

    from app import social

    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    try:
        who = verify(body.id_token, **({"nonce": body.nonce} if body.nonce else {}))
    except social.SocialError as e:
        auth.record_fail(ip)
        raise HTTPException(401, errors.user_text(e, 401)) from e
    u = system.user_by_name(who["email"])
    created = False
    if u and u["disabled"]:
        raise HTTPException(403, "Bu hesap kapatılmış")
    if u and u.get("deleted_at"):
        return _pending_deletion(u)
    from app import email_verify

    if u and email_verify.invite_pending(u["id"]):
        # başkasının davetiyle bu e-postaya açılmış, doğrulanmamış hesap: e-postanın sahibi habersiz o deftere üye
        # olmasın; bekleyen hesap silinir (davet serbest kalır), aşağıda yeni hesap gibi devam edilir
        email_verify.drop_invite_pending(u["id"])
        u = None
    if u and email_verify.unverified(u["id"]):
        # şifreyle açılmış ama doğrulanmamış hesap (belki başkası önceden açtı): Google/Apple e-postanın sahibini
        # kanıtladı. Eski şifre geçersiz, açık oturumlar düşer, hesap doğrulanır; sahibine bilgi gider.
        system.update_user(u["id"], password_hash=auth._hash(secrets.token_urlsafe(32)))
        system.put(f"pw_unset:{u['id']}", "1")
        system.bump_session(u["id"])
        email_verify.mark_verified(u["id"])
        email_verify.notify_taken_over(u)
        u = system.user(u["id"])
    inv = None
    if not u and body.invite:  # davet bağlantısıyla Google/Apple: yeni hesap davetin defterine üye olur
        inv, status = system.invite_status(body.invite)
        if status != "ok":
            raise HTTPException(403, INVITE_REASONS[status])
    if not u and inv:
        if not _consent_ok(body.consent):
            raise HTTPException(409, "consent_required")
        _book_seat_or_409(inv)
        u = auth.create_user(who["email"], secrets.token_urlsafe(32), member_of=inv.get("member_of"),
                             book_role=inv.get("book_role"))
        if not system.use_invite(inv["code"], u["id"]):  # aynı anda iki kayıt: ikincisi geri alınır
            system.delete_user(u["id"])
            raise HTTPException(409, "Davet kodu az önce kullanıldı")
        if inv.get("member_of"):
            from app import book_join

            book_join.announce(inv["member_of"], u, joined=True)
        system.put(f"pw_unset:{u['id']}", "1")
        _record_consent(u["id"], request)
        created = True
    if not u:
        if not plans.open_signup():
            raise HTTPException(403, "Bu e-postayla bir hesap yok")
        if not _consent_ok(body.consent):  # uygulama iki onayı sorup aynı belgeyle yeniden dener
            raise HTTPException(409, "consent_required")
        if _signup_limited(ip):
            raise HTTPException(429, "Bu bağlantıdan çok fazla hesap açıldı. Bir saat sonra tekrar dene.")
        if _signup_daily_full():
            raise HTTPException(429, SIGNUP_FULL)
        # şifresiz hesap olmasın (boş şifre eski tek kullanıcılı girişe düşerdi): kimsenin bilmediği rastgele şifre;
        # isterse "Şifremi unuttum" ile kendi şifresini koyar
        u = auth.create_user(who["email"], secrets.token_urlsafe(32))
        system.put(f"pw_unset:{u['id']}", "1")  # şifresini bilmiyor: silme "SİL" yazarak onaylanır
        _signup_record(ip)
        _record_consent(u["id"], request)
        created = True
    if not social.consume_nonce(body.nonce):  # aynı nonce'lu belgeyle aynı anda ikinci giriş
        raise HTTPException(401, "Giriş belgesinin süresi doldu ya da zaten kullanıldı; yeniden dene")
    system.update_user(u["id"], last_login=__import__("time").strftime("%Y-%m-%d %H:%M:%S"))
    if who.get("via") == "apple":  # 409'da (onay) değil, oturum açılırken: kod tek kullanımlık, yeniden denemede lazım
        from app import apple

        apple.link(u["id"], who.get("sub"), who.get("aud"), body.authorization_code)
    _start_session(request, response, system.user(u["id"]), who.get("via"))
    return {"ok": True, "created": created}


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    email: str
    code: str
    password: str


_forgots: dict[str, list[float]] = {}


@app.post("/api/auth/forgot")
def forgot_password(body: ForgotIn, request: Request):
    """Şifremi unuttum: e-postaya 6 haneli kod. Hesap olsun olmasın aynı yanıt (kimin üye olduğu anlaşılmasın)."""
    import logging
    import time

    from app import reset

    if not reset.enabled():
        raise HTTPException(404, "Bu sunucuda şifre sıfırlama kapalı; sunucu yöneticisine sor")
    ip, now = client_ip(request), time.time()
    with _rate_lock:  # say ve ekle tek adımda: eşzamanlı istekler sınırı aşamasın
        _forgots[ip] = [t for t in _forgots.get(ip, []) if now - t < 3600]
        limited = len(_forgots[ip]) >= 10
        if not limited:
            _forgots[ip].append(now)
    if limited:
        raise HTTPException(429, "Çok fazla istek. Bir saat sonra tekrar dene.")
    try:
        reset.request(body.email)
    except Exception:  # noqa: BLE001 - posta sunucusu hatası dışarıya hesap bilgisi sızdırmasın
        logging.getLogger("hesapkitap.reset").exception("sıfırlama kodu gönderilemedi")
        raise HTTPException(503, "Şu an mail gönderilemiyor, biraz sonra tekrar dene") from None
    return {"ok": True}


@app.post("/api/auth/reset")
def reset_password(body: ResetIn, request: Request, response: Response):
    """Kod doğruysa yeni şifre; eski oturumlar düşer, bu cihazda oturum açılır."""
    from app import reset

    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    _attempt_gate(request)
    try:
        auth._check_strength(body.password)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    u = reset.verify(body.email, body.code)
    if not u:
        auth.record_fail(ip)
        raise HTTPException(400, "Kod yanlış ya da süresi dolmuş")
    auth.set_password(u["id"], body.password)
    reset.notify_changed(u)
    from app import email_verify

    email_verify.mark_verified(u["id"])  # sıfırlama kodu da e-postanın sahibini kanıtlar
    email_verify.finish_invite(u["id"])
    if u.get("deleted_at"):  # kodla kimliğini kanıtladı: geri almayı sor (yeni şifre geçerli)
        return _pending_deletion(system.user(u["id"]))
    _start_session(request, response, system.user(u["id"]))
    return {"ok": True}


@app.post("/api/auth/register")
def register(body: RegisterIn, request: Request, response: Response):
    """Davet koduyla hesap aç. Kod tek kullanımlık; her kullanıcının verisi kendi dosyasında."""
    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    _attempt_gate(request)
    if not _consent_ok(body.consent):
        raise HTTPException(422, "Devam etmek için iki onay kutusunu da işaretle")
    if not body.code and plans.open_signup():
        if _signup_limited(ip):
            raise HTTPException(429, "Bu bağlantıdan çok fazla hesap açıldı. Bir saat sonra tekrar dene.")
        if _signup_daily_full():
            raise HTTPException(429, SIGNUP_FULL)
        from app import email_verify

        if email_verify.required():  # hazır sürüm: e-posta koduyla doğrulanmadan oturum açılmaz (app/email_verify.py)
            return _register_with_code(body, request, ip)
        if email_verify.protected():  # kod adımı kapalı (HESAPKITAP_EMAIL_VERIFY=0): nötr yanıt, işaretli hesap
            return _register_without_code(body, request, response, ip)
        try:
            u = auth.create_user(body.username, body.password)
        except ValueError as e:
            raise HTTPException(422, errors.user_text(e, 422)) from e
        _signup_record(ip)
        _record_consent(u["id"], request)
        _start_session(request, response, u)
        return {"ok": True}
    from app import email_verify

    if email_verify.required():  # sahte adresle üye açılmasın: süresi dolan bekleyen davet hesapları önce düşer
        email_verify.purge_invite_pending()
    if not body.code or not system.valid_invite(body.code):
        auth.record_fail(ip)
        raise HTTPException(403, "Davet kodu geçersiz, kullanılmış ya da süresi dolmuş")
    inv = system.invite(body.code) or {}
    _book_seat_or_409(inv)
    if email_verify.required():  # hazır sürüm: davetle kayıt da e-posta koduyla doğrulanır (açık kayıtla aynı yanıt)
        return _register_invite_with_code(body, inv, request)
    try:  # ortak defter davetiyse: kayıt olan, daveti oluşturanın defterine üye olur
        u = auth.create_user(body.username, body.password, member_of=inv.get("member_of"), book_role=inv.get("book_role"))
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    if not system.use_invite(body.code, u["id"]):  # aynı anda iki kayıt: ikincisi geri alınır
        system.delete_user(u["id"])
        raise HTTPException(409, "Davet kodu az önce kullanıldı")
    if inv.get("member_of"):
        from app import book_join

        book_join.announce(inv["member_of"], u, joined=True)
    _record_consent(u["id"], request)
    _start_session(request, response, u)
    return {"ok": True}


def _register_with_code(body: RegisterIn, request: Request, ip: str) -> dict:
    """Şifreli açık kayıt, e-posta doğrulamalı. Yanıt her durumda aynı (e-postanın kayıtlı olup olmadığı anlaşılmaz):
    {"ok": true, "verify_required": true, "email"}. Oturum, POST /api/auth/verify-email ile kod girilince açılır."""
    from app import email_verify

    name = (body.username or "").strip()
    if "@" not in name:
        raise HTTPException(422, "E-posta adresini yaz")
    try:
        auth._check_strength(body.password)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    pw_hash = auth._hash(body.password)  # her yolda aynı iş: yanıt süresi de bir şey söylemesin
    u = system.user_by_name(name)
    if u and email_verify.invite_pending(u["id"]):  # başkasının davetiyle açılmış, doğrulanmamış: kendi hesabı açılır
        email_verify.drop_invite_pending(u["id"])
        u = None
    if not u:
        try:
            system.check_username(name)
        except ValueError as e:  # e-posta biçimi (hesap var mı yok mu söylemeden önce)
            raise HTTPException(422, errors.user_text(e, 422)) from e
    try:
        if u and email_verify.unverified(u["id"]):
            # doğrulanmamış hesap (belki başkası açtı): yeni şifre, yeni kod; kodu giren (e-postanın sahibi) hesabı alır
            system.update_user(u["id"], password_hash=pw_hash)
            system.bump_session(u["id"])
            email_verify.send_code(u)
        elif u:
            email_verify.notify_existing(u)
        else:
            u = system.create_user(name, pw_hash)
            email_verify.mark_unverified(u["id"])
            _signup_record(ip)
            _record_consent(u["id"], request)
            email_verify.send_code(u)
    except Exception:  # noqa: BLE001 - posta gönderilemese de yanıt aynı (kod yeniden istenebilir)
        logging.getLogger("hesapkitap.auth").exception("doğrulama postası gönderilemedi")
    return {"ok": True, "verify_required": True, "email": name}


def _register_invite_with_code(body: RegisterIn, inv: dict, request: Request) -> dict:
    """Davetle kayıt, e-posta doğrulamalı (hazır sürüm). Hesap açılır ve davet hemen tüketilir (bağlantı ikinci kez
    kullanılamaz; ortak defterde Aile koltuğu sayılır), ama oturum POST /api/auth/verify-email ile kod girilince açılır;
    sahibe "katıldı" bildirimi de o zaman gider (email_verify.finish_invite). Doğrulanmazsa INVITE_TTL sonunda hesap
    silinir, davet serbest kalır. Yanıt açık kayıtla aynı: {"ok": true, "verify_required": true, "email"}. E-posta
    kayıtlıysa yanıt yine aynı (adres kayıtlı mı anlaşılmaz), davet tüketilmez, adrese bilgi maili gider."""
    from app import email_verify

    name = (body.username or "").strip()
    if "@" not in name:
        raise HTTPException(422, "E-posta adresini yaz")
    try:
        auth._check_strength(body.password)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    pw_hash = auth._hash(body.password)
    u = system.user_by_name(name)
    if u and email_verify.invite_pending(u["id"]):  # başka (ya da aynı) davetle açılıp doğrulanmamış: yerine bu kayıt
        email_verify.drop_invite_pending(u["id"])
        u = None
    if not u:
        try:
            system.check_username(name)
        except ValueError as e:
            raise HTTPException(422, errors.user_text(e, 422)) from e
    try:
        if u and email_verify.unverified(u["id"]):
            # açık kayıtla açılmış, doğrulanmamış hesap: açık kayıttaki gibi yeni şifre ve kod; davet tüketilmez
            # (hesabı alan sonra davet bağlantısıyla var olan hesabıyla katılabilir: app/book_join.py)
            system.update_user(u["id"], password_hash=pw_hash)
            system.bump_session(u["id"])
            email_verify.send_code(u)
            return {"ok": True, "verify_required": True, "email": name}
        if u:
            email_verify.notify_existing(u)
            return {"ok": True, "verify_required": True, "email": name}
    except Exception:  # noqa: BLE001 - posta gönderilemese de yanıt aynı
        logging.getLogger("hesapkitap.auth").exception("doğrulama postası gönderilemedi")
        return {"ok": True, "verify_required": True, "email": name}
    try:  # ortak defter davetiyse: kayıt olan, daveti oluşturanın defterine üye olur
        u = system.create_user(name, pw_hash, member_of=inv.get("member_of"), book_role=inv.get("book_role"))
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    if not system.use_invite(inv["code"], u["id"]):  # aynı anda iki kayıt: ikincisi geri alınır
        system.delete_user(u["id"])
        raise HTTPException(409, "Davet kodu az önce kullanıldı")
    email_verify.mark_invite_pending(u["id"], inv["code"])
    _record_consent(u["id"], request)
    try:
        email_verify.send_code(u)
    except Exception:  # noqa: BLE001 - kod "yeniden gönder" ya da girişle yeniden istenebilir
        logging.getLogger("hesapkitap.auth").exception("doğrulama postası gönderilemedi")
    return {"ok": True, "verify_required": True, "email": name}


def _register_without_code(body: RegisterIn, request: Request, response: Response, ip: str) -> dict:
    """Kod adımı kapalıyken kayıt: yeni e-postada hesap açılır ve oturum başlar, ama hesap "doğrulanmamış" işaretlenir
    (Google/Apple ile e-postanın sahibi gelirse şifre geçersiz, oturumlar düşer). Kayıtlı e-postada yanıt yine
    {"ok": true} (e-postanın kayıtlı olduğu söylenmez), hesaba dokunulmaz, adrese bilgi maili gider."""
    from app import email_verify

    name = (body.username or "").strip()
    if "@" not in name:
        raise HTTPException(422, "E-posta adresini yaz")
    try:
        auth._check_strength(body.password)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    pw_hash = auth._hash(body.password)  # her yolda aynı iş
    u = system.user_by_name(name)
    if u:
        email_verify.notify_existing(u)
        return {"ok": True}
    try:
        u = system.create_user(name, pw_hash)
    except ValueError as e:  # e-posta biçimi
        raise HTTPException(422, errors.user_text(e, 422)) from e
    email_verify.mark_unverified(u["id"])
    _signup_record(ip)
    _record_consent(u["id"], request)
    _start_session(request, response, u)
    return {"ok": True}


class VerifyEmailIn(BaseModel):
    email: str
    code: str


class EmailIn(BaseModel):
    email: str


@app.post("/api/auth/verify-email")
def verify_email(body: VerifyEmailIn, request: Request, response: Response):
    """Kayıttan sonra e-postaya gelen kod: doğruysa hesap doğrulanır ve oturum açılır."""
    from app import email_verify

    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    u = email_verify.verify(body.email, body.code)
    if not u:
        auth.record_fail(ip)
        raise HTTPException(400, "Kod yanlış ya da süresi dolmuş; yeni kod iste")
    u = system.user(u["id"])
    system.update_user(u["id"], last_login=__import__("time").strftime("%Y-%m-%d %H:%M:%S"))
    email_verify.finish_invite(u["id"])  # davetle açılmışsa: sahibe "katıldı" şimdi gider
    _start_session(request, response, u)
    return {"ok": True}


@app.post("/api/auth/verify-email/resend")
def verify_email_resend(body: EmailIn, request: Request):
    """Doğrulama kodunu yeniden gönder. Yanıt her durumda aynı; aynı hesaba dakikada en fazla bir kod."""
    from app import email_verify

    if auth.rate_limited(client_ip(request)):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    u = system.user_by_name((body.email or "").strip())
    if u and email_verify.unverified(u["id"]):
        try:
            email_verify.send_code(u)
        except Exception:  # noqa: BLE001
            logging.getLogger("hesapkitap.auth").exception("doğrulama postası gönderilemedi")
    return {"ok": True}


# ---------------------------------------------------------------- yönetici: kullanıcılar ve davetler
# Yönetici sadece hesapları yönetir; kullanıcıların finans verisine erişen bir uç yoktur.

def _require_admin() -> None:
    if not config.is_admin():
        raise HTTPException(403, "Sadece yönetici")


def _user_row(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "role": u["role"], "created_at": u["created_at"],
            "last_login": u["last_login"], "disabled": bool(u["disabled"]),
            "must_change_password": bool(u["must_change_password"]),
            "ai_used": system.ai_used(u["id"]), "ai_quota": system.ai_quota(u), "ai_quota_custom": u["ai_quota"],
            "plan": u.get("plan") or "free", "plan_until": u.get("plan_until"), "plan_source": u.get("plan_source"),
            "deleted_at": u.get("deleted_at"), "delete_after": u.get("delete_after"),
            **({"reads": _admin_reads(u)} if plans.hosted() else {})}


def _admin_reads(u: dict) -> dict | None:
    """Yönetici listesi: bugünkü okuma (ücretsizde bu ayki toplam) ve ek paket bakiyesi (finans verisi değil)."""
    from app import doc_reads

    return doc_reads.status(u)


class AdminUserIn(BaseModel):
    username: str


class AdminUserPatch(BaseModel):
    disabled: bool | None = None
    ai_quota: int | None = Field(None, ge=0, le=100000)
    reset_quota: bool = False  # True: varsayılan kotaya dön
    plan: Literal["free", "premium", "aile"] | None = None  # hazır sürüm: yöneticinin verdiği paket (test kullanıcıları)
    plan_days: int | None = Field(None, ge=1, le=3660)      # kaç gün; boşsa 90


class InviteIn(BaseModel):
    note: str | None = None
    days: int = Field(14, ge=1, le=90)
    book_role: Literal["viewer", "editor", "full"] = "editor"  # ortak defter daveti: üyenin yetkisi


@app.get("/api/admin/users")
def admin_users(request: Request):
    _require_admin()
    return {"users": [_user_row(u) for u in system.users()], "invites": [_invite_out(i, request) for i in system.invites()],
            "default_quota": system.ai_quota({"role": "user"}), "hosted": plans.hosted()}


class PlanTableIn(BaseModel):
    tiers: dict[str, Literal["free", "premium", "aile"]] = {}
    reset: bool = False                 # True: bütün satırlar varsayılana (koddaki tablo)
    upgrade_yearly: bool | None = None  # hak dolunca aylık abonelere yıllığa geçişi öner


def _plan_table_out() -> dict:
    return {"rows": plans.table(), "tiers": list(plans.TIERS), "labels": {k: plans.LABELS[k] for k in plans.TIERS},
            "upgrade_yearly": plans.upgrade_yearly_enabled(), "hosted": plans.hosted(),
            "reads": plans.READS, "packs": [{"id": k, "reads": n} for k, n in plans.PACKS.items()],
            # günlük tek sayaç: {paket: {day, month, text}} ("Günde 30 okuma (fiş, fatura, ekstre)")
            "daily_reads": {p: {"day": plans.DAILY_READS[p], "month": plans.MONTHLY_CAP.get(p), "text": plans.reads_text(p)}
                            for p in plans.TIERS}}


@app.get("/api/admin/plan-table")
def admin_plan_table():
    """Sayfa / özellik tablosu: her satır hangi paketten itibaren açık (hazır sürüm; kendi sunucusunda uygulanmaz)."""
    _require_admin()
    return _plan_table_out()


@app.put("/api/admin/plan-table")
def admin_put_plan_table(body: PlanTableIn):
    """Yönetici paneli: satırların paketini değiştir (system.db; deploy gerekmez, ~10 sn içinde her işçide geçerli)."""
    _require_admin()
    try:
        plans.set_tiers(dict(body.tiers), reset=body.reset)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if body.upgrade_yearly is not None:
        plans.set_upgrade_yearly(body.upgrade_yearly)
    return _plan_table_out()


class AppUpdateIn(BaseModel):
    model_config = {"extra": "forbid"}  # bilinmeyen platform 422
    ios: dict[str, str | None] | None = None      # {latest, min, store_url}; boş değer: alanı ayardan kaldır
    android: dict[str, str | None] | None = None
    reset: bool = False                           # True: yönetici ayarını sil (ortam değişkenleri geçerli olur)


def _app_update_out() -> dict:
    from app import app_update

    return {"fields": app_update.raw(), "effective": app_update.config(), "default_store": app_update.DEFAULT_STORE}


@app.get("/api/admin/app-regions")
def admin_app_regions():
    """Bölge seçen kullanıcı sayısı il başına: [{region, users}] (yalnız en az 3 kişinin seçtiği iller; kişi bilgisi yok).
    Fiyat toplayan ekip (BigQuery marketfiyati) hangi illerin çekileceğini buradan alır: servis hesabımız salt okuma
    olduğu için app_regions tablosuna biz yazamıyoruz; onlar bu ucu yönetici anahtarıyla (Bearer) çeker."""
    _require_admin()
    return prefs.region_counts(3)


@app.get("/api/regions/demand")
def regions_demand(request: Request):
    """Fiyat toplayan iş (Cloud Run, marketfiyati) için oturumsuz uç: admin_app_regions ile aynı liste. Yetki yalnız
    HESAPKITAP_APP_REGIONS_KEY (Authorization: Bearer …); anahtar yalnız bu ucu açar, sızarsa başka hiçbir şeye erişilmez.
    Anahtar ayarlı değilse ya da tutmazsa 404 (uç var mı yok mu belli olmaz)."""
    import hmac

    key = os.environ.get("HESAPKITAP_APP_REGIONS_KEY", "")
    got = request.headers.get("authorization", "")
    if not key or not hmac.compare_digest(got, f"Bearer {key}"):
        raise HTTPException(404, "Not Found")
    return prefs.region_counts(3)


class OpsAlertIn(BaseModel):
    status: str = Field(..., min_length=1, max_length=40)       # failed | blocked | schema_changed | anomaly …
    region: str | None = Field(None, max_length=60)
    text: str = Field(..., min_length=1, max_length=400)


@app.post("/api/ops/alert")
def ops_alert(body: OpsAlertIn, request: Request):
    """Fiyat toplayan iş (Cloud Run, marketfiyati) aksadığında yöneticinin telefonuna push ("sistem" türü, sessiz saate
    uyar). Yetki yalnız HESAPKITAP_OPS_ALERT_KEY (Authorization: Bearer …); tutmazsa 404. Aynı gün + durum + bölge
    bir kez gider."""
    import hmac

    from app import push

    key = os.environ.get("HESAPKITAP_OPS_ALERT_KEY", "")
    if not key or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {key}"):
        raise HTTPException(404, "Not Found")
    return {"ok": True, "sent": push.ops_alert(body.status, body.region, body.text)}


@app.get("/api/admin/official-prices")
def admin_official_prices():
    """Resmî fiyat içe aktarımı (BigQuery → official_prices): {configured, last_day, regions, rows, imported_at,
    last_run_at, last_error, last_error_at} (app/price_import_bq.py status) + grouping_quality (app/product_groups.py
    quality: gruplanan ad oranı, kaynaklar, çelişen gruplar, fiyatı 3 kat açılan gruplar, katalog) + disagreements
    (topluluk ile Market Fiyatı son 7 günün en az 3'ünde %10'dan çok ayrışan zincir × ürün; app/community_prices.py)."""
    from app import community_prices, price_import_bq, product_groups

    _require_admin()
    return {**price_import_bq.status(), "grouping_quality": product_groups.quality(),
            "disagreements": community_prices.disagreements()}


@app.get("/api/admin/app-update")
def admin_app_update():
    """Mobil uygulama sürüm yönlendirmesi: son / en düşük sürüm ve mağaza adresi (değer ve kaynağı: ayar | ortam)."""
    _require_admin()
    return _app_update_out()


@app.put("/api/admin/app-update")
def admin_put_app_update(body: AppUpdateIn):
    """Yönetici paneli: sürüm değerlerini system.db'ye yaz (deploy gerekmez, ~10 sn içinde her işçide geçerli)."""
    from app import app_update

    _require_admin()
    try:
        app_update.set_config({k: v for k, v in (("ios", body.ios), ("android", body.android)) if v is not None},
                              reset=body.reset)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _app_update_out()


class AiCostSettingsIn(BaseModel):
    prices: dict[str, dict[str, float]] | None = None  # {"model": {"input": $/1M, "output": $/1M}}; {}: varsayılana dön
    usd_try: float | None = None
    alert_try: float | None = None  # kullanıcı başı aylık ₺ eşiği (ai_alert_try)


@app.get("/api/admin/ai-costs")
def admin_ai_costs(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")):
    """Yapay zekâ maliyeti (bu ay ya da ?month=YYYY-MM): tür başına çağrı/okuma, ortalama token, tahmini maliyet, en
    yoğun 10 kullanıcı (maskeli). İçerik kaydedilmez."""
    from app import ai_costs

    _require_admin()
    return ai_costs.summary(month)


@app.put("/api/admin/ai-costs")
def admin_put_ai_costs(body: AiCostSettingsIn):
    from app import ai_costs

    _require_admin()
    try:
        ai_costs.set_settings(body.prices, body.usd_try, body.alert_try)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return ai_costs.summary()


@app.get("/api/admin/receipt-templates")
def admin_receipt_templates():
    """Zincir / akaryakıt fişi şablonları (sunucu geneli): şablon başına isabet, hata, son kullanım, devre dışı durumu.
    Kişisel veri yok: ad yalnız bilinen zincirse, değilse imzanın kısa özeti (app/receipt_templates.py)."""
    from app import receipt_templates

    _require_admin()
    return receipt_templates.overview()


@app.get("/api/admin/card-templates")
def admin_card_templates(days: int = Query(30, ge=1, le=365)):
    """Kart ekstresi şablonları (banka başına isabet / hata / durum) ve yapay zekâ okumasından öğrenme denemeleri: son
    `days` günde banka başına başarılı / başarısız ve neden kodları. Kişisel veri yok (app/templates.py card_overview)."""
    from app import templates

    _require_admin()
    return templates.card_overview(days)


@app.get("/api/admin/ai-costs/users")
def admin_ai_cost_users(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")):
    """Kullanıcı başı aylık yapay zekâ maliyeti (en pahalı üstte): çağrı, okuma, tahmini ₺, paket, eşik, kısıtlama."""
    from app import ai_costs

    _require_admin()
    return ai_costs.users_month(month)


class AiRestrictionIn(BaseModel):
    mode: Literal["no_ai", "cap"]
    daily_cap: int | None = Field(None, ge=0, le=1000)
    note: str | None = Field(None, max_length=300)


def _restricted_user(uid: int) -> dict:
    u = system.user(uid)
    if not u or u["role"] == "admin":
        raise HTTPException(404, "Kullanıcı yok")
    return u


def _restriction_out(uid: int) -> dict:
    from app import ai_restrictions

    return {"user_id": uid, "restriction": ai_restrictions.get(uid), "today_reads": ai_restrictions.today_reads(uid),
            "history": ai_restrictions.history(uid)}


@app.get("/api/admin/ai-restrictions/{uid}")
def admin_ai_restriction(uid: int):
    _require_admin()
    _restricted_user(uid)
    return _restriction_out(uid)


@app.put("/api/admin/ai-restrictions/{uid}")
def admin_put_ai_restriction(uid: int, body: AiRestrictionIn):
    """Kötüye kullanım: no_ai (yapay zekâ okuması kapalı, yerel okuma açık) ya da cap (günde en çok daily_cap belge;
    paketin hakkından düşükse o bağlar). İşlem ai_restriction_log'a yazılır."""
    from app import ai_restrictions

    _require_admin()
    _restricted_user(uid)
    try:
        ai_restrictions.put(uid, body.mode, body.daily_cap, body.note, by=(db.current_user() or {}).get("id"))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _restriction_out(uid)


@app.delete("/api/admin/ai-restrictions/{uid}")
def admin_delete_ai_restriction(uid: int):
    from app import ai_restrictions

    _require_admin()
    _restricted_user(uid)
    if not ai_restrictions.remove(uid, by=(db.current_user() or {}).get("id")):
        raise HTTPException(404, "Bu hesapta kısıtlama yok")
    return _restriction_out(uid)


@app.post("/api/admin/users")
def admin_create_user(body: AdminUserIn):
    """Davetsiz hesap: geçici şifre bir kez gösterilir, ilk girişte değiştirilir."""
    _require_admin()
    temp = auth.temp_password()
    try:
        u = auth.create_user(body.username, temp, must_change=True)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    return {"user": _user_row(u), "temp_password": temp}


@app.patch("/api/admin/users/{uid}")
def admin_update_user(uid: int, body: AdminUserPatch):
    _require_admin()
    u = system.user(uid)
    if not u:
        raise HTTPException(404, "Kullanıcı yok")
    if u["role"] == "admin" and body.disabled:
        raise HTTPException(409, "Yönetici kapatılamaz")
    if body.disabled is not None:
        system.update_user(uid, disabled=int(body.disabled))
        if body.disabled:
            system.bump_session(uid)
    if body.reset_quota:
        system.update_user(uid, ai_quota=None)
    elif body.ai_quota is not None:
        system.update_user(uid, ai_quota=body.ai_quota)
    if body.plan is not None:
        if body.plan == "free":
            system.update_user(uid, plan="free", plan_until=None, plan_source="manual")
        else:
            from datetime import datetime, timedelta, timezone

            until = (datetime.now(timezone.utc) + timedelta(days=body.plan_days or 90)).isoformat(timespec="seconds")
            system.update_user(uid, plan=body.plan, plan_until=until, plan_source="manual")
    return _user_row(system.user(uid))


@app.post("/api/admin/users/{uid}/reset-password")
def admin_reset_password(uid: int):
    _require_admin()
    u = system.user(uid)
    if not u or u["role"] == "admin":
        raise HTTPException(404, "Kullanıcı yok")
    temp = auth.temp_password()
    auth.set_password(uid, temp, must_change=True)
    from app import reset

    reset.notify_changed(u, by_admin=True)  # kullanıcı yöneticinin sıfırladığını bilsin (Y4)
    return {"temp_password": temp}


@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: int):
    """Hesabı siler; veri dosyası hemen silinmez, kenara (users/_silinen) taşınır, 30 gün sonra kalıcı silinir."""
    _require_admin()
    u = system.user(uid)
    if not u or u["role"] == "admin":
        raise HTTPException(404, "Kullanıcı yok")
    deletion.admin_deleted(u)  # anonim kayıt (e-posta özeti, paket); veri aşağıda _silinen'e taşınır
    system.delete_user(uid)
    system.archive_user_data(u)
    return {"ok": True}


class DeletedQuery(BaseModel):
    email: str | None = Field(None, max_length=254)


@app.post("/api/admin/deleted")
def admin_deleted_accounts_post(body: DeletedQuery):
    """GET ile aynı; e-posta gövdede (adres sorgu dizgesine, dolayısıyla erişim günlüğüne düşmesin — S8)."""
    return admin_deleted_accounts(body.email)


@app.get("/api/admin/deleted")
def admin_deleted_accounts(email: str | None = None):
    """Silinen hesapların anonim kaydı. email verilirse özeti hesaplanıp eşleştirilir (düz e-posta saklanmaz).
    Yeni istemci POST /api/admin/deleted {email} kullansın (eski GET de çalışır; günlükte sorgu dizgesi yazılmaz)."""
    _require_admin()
    rows = deletion.records(email=(email or "").strip() or None)
    return {"records": [{k: r[k] for k in ("user_id", "deleted_at", "delete_after", "purged_at", "reason", "plan",
                                           "plan_until", "plan_source", "plan_store", "will_renew", "store_customer",
                                           "notes", "warnings")} | {"email_hash": r["email_hash"][:12]} for r in rows],
            "keep_days": deletion.RECORD_KEEP_DAYS, "grace_days": deletion.grace_days()}


@app.post("/api/admin/invites")
def admin_create_invite(body: InviteIn, request: Request):
    _require_admin()
    return _invite_out(system.create_invite(body.note, body.days), request)


@app.delete("/api/admin/invites/{code}")
def admin_delete_invite(code: str):
    _require_admin()
    if not system.delete_invite(code):
        raise HTTPException(404, "Davet yok ya da kullanılmış")
    return {"ok": True}


# ---------------------------------------------------------------- "Sorun bildir" (app/feedback.py)

def _upload_bytes(f: UploadFile, cap: int) -> bytes:
    """En fazla cap + 1 bayt okur: büyük dosya belleğe tamamen alınmadan reddedilir."""
    f.file.seek(0)
    return f.file.read(cap + 1)


@app.post("/api/feedback")
def feedback_send(request: Request, message: str = Form(""), context: str | None = Form(None),
                  screenshots: list[UploadFile] | None = File(None)):
    """Kullanıcının sorun bildirimi yöneticiye: mesaj, 0–3 ekran görüntüsü, bağlam. Oturumsuz (giriş ekranı), demo ve
    ortak defter üyesi (her yetki) de gönderir. Saatte 10 (kullanıcı; oturumsuzda bağlantı başı)."""
    from app import feedback

    u = db.current_user() or {}
    demo = bool(u.get("demo"))
    uid = u.get("id") if u.get("id") and not demo else None
    try:
        msg = feedback.check_message(message)
        ctx = feedback.check_context(context)
        files = [f for f in (screenshots or []) if f.filename or f.size]
        if len(files) > feedback.MAX_SHOTS:
            raise feedback.FeedbackError(f"En fazla {feedback.MAX_SHOTS} ekran görüntüsü eklenebilir")
        shots = feedback.check_shots([_upload_bytes(f, feedback.MAX_SHOT_BYTES) for f in files])
    except feedback.FeedbackError as e:
        raise HTTPException(e.status, str(e)) from e
    key = feedback.client_key(client_ip(request))
    if feedback.limited(uid, key):
        raise HTTPException(429, "Bu saat içinde çok fazla bildirim gönderildi; biraz sonra tekrar dene.",
                            headers={"Retry-After": "600"})
    ua = (request.headers.get("user-agent") or "")[:300]
    if ua:
        ctx["user_agent"] = ua
    fid = feedback.create(msg, ctx, shots, uid, key, demo=demo)
    try:
        feedback.notify_async(fid, msg, uid, demo, shots[0] if shots else None)
    except Exception:  # noqa: BLE001 - bildirim hatası kaydı bozmaz
        logging.getLogger("hesapkitap.feedback").exception("geri bildirim bildirimi başlatılamadı")
    return {"ok": True, "id": fid}


class FeedbackPatch(BaseModel):
    status: Literal["yeni", "okundu", "cozuldu"] | None = None
    admin_note: str | None = Field(None, max_length=2000)


@app.get("/api/admin/feedback")
def admin_feedback(status: Literal["yeni", "okundu", "cozuldu"] | None = None, limit: int = Query(100, ge=1, le=500)):
    """Geri bildirimler (yeniden eskiye) ve durum sayıları. Kullanıcı adı tam görünür (yönetici hesabı zaten görür)."""
    from app import feedback

    _require_admin()
    return feedback.list_items(status, limit)


@app.get("/api/admin/feedback/{fid}/screenshot/{n}")
def admin_feedback_screenshot(fid: int, n: int):
    """n: 0'dan başlayan sıra (liste satırındaki screenshots sayısından küçük)."""
    from app import feedback

    _require_admin()
    got = feedback.screenshot(fid, n)
    if not got:
        raise HTTPException(404, "Görüntü yok")
    data, ctype = got
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600",
                                                     "Content-Security-Policy": "default-src 'none'"})


@app.patch("/api/admin/feedback/{fid}")
def admin_feedback_patch(fid: int, body: FeedbackPatch):
    from app import feedback

    _require_admin()
    out = feedback.update(fid, body.status, body.admin_note)
    if not out:
        raise HTTPException(404, "Geri bildirim yok")
    return out


@app.get("/api/me/ai")
def my_ai():
    """Okuma kotası. unit "docs": hazır sürüm, günlük tek okuma hakkı (app/doc_reads.py; kişi başı).
    unit "ai": kendi sunucusu, ortak anahtarla aylık yapay zekâ çağrısı kotası; kendi anahtarı varsa sınırsız."""
    unit = "docs" if plans.hosted() else "ai"
    u = db.current_user()
    if not u or u["role"] == "admin":
        return {"used": None, "quota": None, "own_key": False, "unit": unit}
    own = bool(config.own_ai_key())
    shared = bool(system.get("cfg:GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY"))
    if plans.hosted():
        # reads: günlük tek sayaç (doc_reads.status; "daily_reads"). Geriye uyum: used/quota (tek sayı), belge ve fiş
        # (eski iki sayaç; ikisi de aynı günlük sayacı yansıtır), extra: ek okuma paketi bakiyesi
        from app import doc_reads

        st = doc_reads.status(u)
        return {"used": st["used"], "quota": st["limit"], "own_key": own, "unit": unit, "shared_available": shared,
                "belge": st["belge"], "fis": st["fis"], "extra": st["extra"], "reads": st}
    return {"used": system.ai_used_pool(u), "quota": None if own else system.ai_quota(u), "own_key": own, "unit": unit,
            "shared_available": shared}


class DeleteMeIn(BaseModel):
    password: str | None = None
    confirm: str | None = None  # şifresi olmayan (Google/Apple ile açılmış) hesap: "SİL" yazarak onaylar


@app.get("/api/me/subscription")
def my_subscription():
    """Kendi mağaza aboneliğin: silme akışının ilk adımı (yenileniyor mu, ne zaman bitiyor, hangi mağaza)."""
    u = db.current_user()
    return plans.subscription(system.user(u["id"]) if u else None)


@app.post("/api/me/delete")
def delete_me(body: DeleteMeIn, response: Response):
    """Kendi hesabını sil (şifreyle ya da şifresizse "SİL" ile onay). Hesap hemen kapanır; bekleme süresi
    (HESAPKITAP_DELETE_GRACE_DAYS, varsayılan 7) içinde giriş yapıp geri alınabilir, sonra kalıcı silinir.
    Ortak defterin sahibiysen üyelerin de aynı akışa girer. Mağaza aboneliği engel değildir (iptali kullanıcının)."""
    u = db.current_user()
    if not u or u["role"] == "admin":
        raise HTTPException(403, "Yönetici hesabı buradan silinemez")
    if auth.password_unset(u["id"]):
        if (body.confirm or "").strip().upper().replace("I", "İ") != "SİL":
            raise HTTPException(403, "Onay için SİL yaz")
    elif not auth.check_password(u, body.password or ""):
        raise HTTPException(403, "Şifre yanlış")
    res = deletion.request_delete(system.user(u["id"]))  # oturumdaki kopya değil: güncel satır (paket, mağaza)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True, **res}


class RestoreIn(BaseModel):
    restore_token: str


@app.post("/api/me/restore")
def restore_me(body: RestoreIn, request: Request, response: Response):
    """Silinmek üzere bekleyen hesabı geri al. Belge girişte (409 account_pending_deletion) verilir; oturum açılır."""
    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    u = auth.restore_user(body.restore_token)
    if not u:
        auth.record_fail(ip)
        raise HTTPException(401, "Geri alma süresi doldu; yeniden giriş yap")
    if not deletion.restorable(u):
        raise HTTPException(409, "Bu defterin sahibi hesabını sildi; yalnız o geri alabilir")
    deletion.restore(u)
    u = system.user(u["id"])
    _start_session(request, response, u)
    return {"ok": True, "subscription": plans.subscription(u)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    """Bu oturumu kapat: anahtar (çerez ya da Bearer) sunucuda da geçersiz olur."""
    from app import sessions

    token = request.cookies.get(auth.COOKIE) or _bearer(request)
    nonce = auth.token_nonce(token)
    if nonce:
        u = auth.token_user(token)
        sessions.revoke(u["id"] if u else None, nonce)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


def _current_nonce(request: Request) -> str | None:
    return auth.token_nonce(request.cookies.get(auth.COOKIE) or _bearer(request))


@app.get("/api/me/sessions")
def my_sessions(request: Request):
    """Açık oturumlar: [{id, kind (web | mobile | eski), device, created_at, last_seen, current}] (en son kullanılan önce)."""
    from app import sessions

    u = db.current_user()
    if not u:
        return []
    return sessions.listing(u["id"], _current_nonce(request))


@app.delete("/api/me/sessions/{sid}")
def revoke_my_session(sid: str, request: Request):
    from app import sessions

    u = db.current_user()
    if not u or not re.fullmatch(r"[\w-]{6,40}", sid) or not sessions.revoke_public(u["id"], sid):
        raise HTTPException(404, errors.GENERIC[404])
    return {"ok": True}


class RevokeAllIn(BaseModel):
    keep_current: bool = True  # bu cihazda oturum açık kalsın (yeni anahtar verilir)


@app.post("/api/me/sessions/revoke-all")
def revoke_all_sessions(request: Request, response: Response, body: RevokeAllIn | None = None):
    """Tümünden çık: bütün oturumlar (hiç kullanılmamış eski anahtarlar dahil) geçersiz. keep_current: bu cihaza yeni
    anahtar (web: çerez; mobil: X-Session-Token başlığı)."""
    from app import sessions

    u = db.current_user()
    if not u:
        raise HTTPException(401, errors.GENERIC[401])
    n = sessions.revoke_all(u["id"])
    from app import push

    # bildirim kayıtları da düşer: çıkış yaptırılan telefonlara hatırlatma gitmesin. Bu cihaz yeni oturumla açık
    # kalıyorsa mobil uygulama oturum değişince kendini yeniden kaydeder.
    push.forget_devices(u["id"])
    if (body or RevokeAllIn()).keep_current:
        _start_session(request, response, system.user(u["id"]))
    else:
        response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True, "revoked": n}

Kind = Literal["gelir", "gider"]
# kart: ön ödemeli (yemek/hediye/giyim), wallet_id ile. altin_bozdurma / altin: zekât/sadaka altınla verildi (hesapsız,
# donations.create_expense açar); kayıt düzenlenip geri gönderilince kabul edilsin diye burada da
Payment = Literal["kredi_karti", "nakit", "havale", "kart", "altin_bozdurma", "altin"]
# zekât/sadaka "Nasıl ödedin?" (donations.PAYMENTS); bilinmeyen değer eskisi gibi 422
DonationPayment = Literal["banka", "nakit", "havale", "kredi_karti", "altin_bozdurma", "altin"]
CardKind = Literal["harcama", "iade", "faiz_ucret", "odeme"]
EXPENSE_CATEGORIES = ["Altın & Döviz Birikimi", "Kira & Aidat", "Faturalar", "Elektrik", "Doğalgaz", "Su", "İnternet & TV", "Telefon", "Sağlık",
                      "Eğitim", "Ulaşım & Araç", "Market & Gıda", "Kredi & Borç", "Bağış & Sadaka", "Diğer"]
INCOME_CATEGORIES = ["Maaş", "Ek Gelir", "Kira Geliri", "Yatırım", "Diğer Gelir"]


# ---------------------------------------------------------------- meta

def _by_usage(cats: set[str]) -> list[str]:
    usage = db.category_usage()
    return sorted(cats, key=lambda c: (-usage.get(c, 0), c))


# ---------------------------------------------------------------- alt kategoriler

def _can_edit_subcategories() -> bool:
    """Alt kategori listesini değiştirebilir mi: defter sahibi (tek kullanıcılı kurulum dahil) ve tam yetkili üye.
    Görür + ekler (editor) üye listedekileri seçer, yenisini açamaz; sadece görür (viewer) hiçbir şey yazamaz."""
    from app import book

    return not book.member() or book.role() == "full"


def _subcategory(category: str | None, sub: str | None) -> str | None:
    """Kayda/kurala yazılacak alt kategori: ana kategori yoksa ya da alt kategori boşsa None. Listede yoksa yetkili
    kişi için listeye eklenir (formdaki "+ Alt kategori" ayrı istek atmadan da çalışsın), yetkisizde 422.
    Dönen ad listedeki yazılıştır ("temizlik" → "Temizlik")."""
    sub = db.clean_sub_name(sub)
    if not sub or not category or category == "Kategorisiz":
        return None
    if len(sub) > 40:
        raise HTTPException(422, "Alt kategori adı en çok 40 karakter olabilir")
    if (hit := db.find_subcategory(category, sub)):
        return hit["name"]
    if not _can_edit_subcategories():
        raise HTTPException(422, f"“{category}” altında “{sub}” alt kategorisi yok; defter sahibi ekleyebilir")
    return db.add_subcategory(category, sub)[0]["name"]


def _patch_subcategory(data: dict, current: dict | None) -> None:
    """PATCH gövdesi: alt kategori verildiyse (yeni ya da mevcut) ana kategoriye göre doğrulanır. Yalnız ana kategori
    değiştiyse (eski istemci alt kategori göndermez) eski alt kategori yeni ana kategoriye ait olmadığı için silinir."""
    if current is None:
        return
    cat = data["category"] if "category" in data else current.get("category")
    if "subcategory" in data:
        data["subcategory"] = _subcategory(cat, data["subcategory"])
    elif "category" in data and data["category"] != current.get("category") and current.get("subcategory"):
        data["subcategory"] = None


class SubcategoryIn(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=40)


class SubcategoryPatch(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class SubcategoryOrderIn(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    ids: list[int] = Field(max_length=500)


def _subcategories_out() -> dict:
    items = db.list_subcategories()
    return {"subcategories": db.subcategories_map(), "items": items, "can_edit": _can_edit_subcategories()}


@app.get("/api/subcategories")
def subcategories_list():
    """Alt kategoriler: {subcategories: {ana: [alt…]}, items: [{id, category, name, position, count}], can_edit}."""
    return _subcategories_out()


@app.post("/api/subcategories")
def subcategories_add(body: SubcategoryIn):
    """Ana kategorinin altına alt kategori. Aynı ad (büyük/küçük harf farkı gözetmeden) varsa yenisi açılmaz:
    var olan döner (created: false). Ana kategoriyle aynı ad da serbest."""
    name, cat = db.clean_sub_name(body.name), db.clean_sub_name(body.category)
    if not name or not cat:
        raise HTTPException(422, "Alt kategori adı boş olamaz")
    if cat == "Kategorisiz":
        raise HTTPException(422, "Kategorisiz altına alt kategori eklenmez")
    item, created = db.add_subcategory(cat, name)
    return {**item, "created": created}


@app.patch("/api/subcategories/{sid}")
def subcategories_rename(sid: int, body: SubcategoryPatch):
    """Yeniden adlandır: bu alt kategorideki işlemler, kayıtlar ve kurallar da yeni adı alır (updated: satır sayısı)."""
    name = db.clean_sub_name(body.name)
    if not name:
        raise HTTPException(422, "Alt kategori adı boş olamaz")
    try:
        n = db.rename_subcategory(sid, name)
    except KeyError as e:
        raise HTTPException(404, "Alt kategori bulunamadı") from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "updated": n, **(db.get_subcategory(sid) or {})}


@app.delete("/api/subcategories/{sid}")
def subcategories_delete(sid: int):
    """Sil: işlemlerden, kayıtlardan ve kurallardan kaldırılır, ana kategorileri değişmez (cleared: satır sayısı)."""
    try:
        return {"ok": True, "cleared": db.delete_subcategory(sid)}
    except KeyError as e:
        raise HTTPException(404, "Alt kategori bulunamadı") from e


@app.put("/api/subcategories/order")
def subcategories_order(body: SubcategoryOrderIn):
    """Ana kategorinin alt kategorilerini sırala (ids: istenen sıra; eksik kalanlar sona)."""
    db.reorder_subcategories(body.category, body.ids)
    return _subcategories_out()


@app.post("/api/billing/revenuecat")
async def revenuecat_webhook(request: Request):
    """App Store / Google Play aboneliği (RevenueCat webhook). Yetki: RevenueCat panelinde girilen Authorization değeri."""
    import hmac

    secret = os.environ.get("REVENUECAT_WEBHOOK_AUTH", "")
    if not plans.hosted() or not secret or not hmac.compare_digest(request.headers.get("authorization", ""), secret):
        raise HTTPException(404, errors.GENERIC[404])
    body = await request.json()
    res = await asyncio.to_thread(plans.apply_revenuecat, body.get("event") or {})  # SQLite yazma: döngü beklemesin
    if res.get("ignored") != "app_user_id":  # Ölçüm: yalnız toplam (app/funnel.py), kişi yazılmaz; anonim kimlik sayılmaz
        from app import funnel

        await asyncio.to_thread(funnel.record_store_event, body.get("event") or {})
    import logging

    logging.getLogger("ekstre").info("revenuecat %s %s", (body.get("event") or {}).get("type"), res)
    return {"ok": True}


@app.post("/api/mail/inbound")
async def mail_inbound(request: Request):
    """Cloudflare Email Worker: deftere özel adrese gelen ham mail (message/rfc822). Yetki: HESAPKITAP_INBOUND_SECRET."""
    import hmac

    from app import inbound

    secret = os.environ.get("HESAPKITAP_INBOUND_SECRET", "")
    if not secret or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {secret}"):
        raise HTTPException(404, errors.GENERIC[404])
    raw = await request.body()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "Mail çok büyük")
    to = request.headers.get("x-hk-to", "")
    if not inbound.resolve(to):
        raise HTTPException(404, "Adres yok")
    # Belgeleri okumak dakikalar sürebilir (çok ekli mail, yapay zekâ): mail hemen kabul edilir, okuma ayrı iş
    # parçacığında yapılır. Hem sunucu diğer isteklere yanıt vermeye devam eder hem de Cloudflare beklerken zaman
    # aşımına düşüp maili yeniden göndermez. Aynı mail işlenirken tekrar gelirse ikinci kez başlatılmaz.
    key = inbound.message_key(raw, to)
    with _inbound_lock:
        if key in _inbound_busy:
            return {"status": "processing"}
        _inbound_busy.add(key)

    def work():
        try:
            with _HEAVY:
                inbound.handle(raw, to)
        except Exception:  # noqa: BLE001 - arka planda: günlüğe yaz, sunucuyu düşürme
            logging.getLogger("hesapkitap.inbound").exception("gelen posta işlenemedi")
        finally:
            with _inbound_lock:
                _inbound_busy.discard(key)

    threading.Thread(target=work, name="inbound", daemon=True).start()
    return {"status": "accepted"}


_inbound_lock = threading.Lock()
_inbound_busy: set[str] = set()


@app.get("/api/mail/address")
def mail_address():
    """Defterin e-posta adresi (yoksa oluşturulur). Paketinde yoksa adres verilmez, kilit bilgisi döner."""
    from app import inbound

    u = db.current_user()
    if not u:
        raise HTTPException(409, "Posta adresi için giriş (kullanıcı hesabı) gerekli")
    if not plans.allows(u, "posta"):
        return {"locked": True, "address": None}
    return {"locked": False, "address": inbound.address(u), "senders": inbound.senders(), "log": db.mail_log(20)}


class SendersIn(BaseModel):
    senders: list[str] = Field(default_factory=list, max_length=10)


@app.put("/api/mail/senders")
def mail_senders(body: SendersIn):
    """Güvendiğin e-posta adreslerin: bunlardan elle iletilen ekstre/fatura kabul edilir (imza yine doğrulanır)."""
    from app import inbound

    return {"senders": inbound.set_senders(body.senders)}


@app.post("/api/mail/address/regenerate")
def mail_address_regenerate():
    from app import inbound

    u = db.current_user()
    if not u:
        raise HTTPException(409, "Posta adresi için giriş gerekli")
    if not plans.allows(u, "posta"):
        raise HTTPException(402, detail=str(plans.PlanRequired("posta")), headers={"X-Plan-Required": "posta"})
    return {"address": inbound.regenerate(u)}


@app.get("/api/meta")
def meta():
    return {
        "months": finance.available_months(),
        "today": date.today().isoformat(),
        "categories": _by_usage(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories()) - {"Ödeme"}),
        "income_categories": INCOME_CATEGORIES,
        # alt kategoriler {ana: [alt, …]} (defterin; yetenek "subcategories") ve bu kişi listeyi değiştirebilir mi
        "subcategories": db.subcategories_map(), "subcategories_editable": _can_edit_subcategories(),
        "card_kinds": [PURCHASE, REFUND, FEE, PAYMENT],
        "receipt_enabled": receipt.enabled() or ocr.available(),
        "receipt_engine": "gemini" if receipt.enabled() else "yerel" if ocr.available() else None,
        "current_month": finance.current_period(),
        "period": {**dict(zip(("month", "start", "end"), (finance.current_period(),
                                                        *(d.isoformat() for d in finance.period_bounds(finance.current_period()))))),
                   # dönemin adı: başladığı ay (anahtar month dönemin bittiği ay; db.period_name_month)
                   "name": finance.period_name(finance.current_period(), year=False), "label": finance.period_name(finance.current_period())},
        "period_naming": "start",  # istemci: ay dönümü > 1 ise ad = anahtarın bir önceki ayı
        "cycle_start_day": db.cycle_start_day(),
        "cycle_mode": db.cycle_mode(),
        "suggested_cycle_day": db.suggested_cycle_day(),
        "modules": prefs.modules(), "modules_chosen": prefs.modules_chosen(), "ui": _ui_out(),
        "module_labels": {k: {"label": v[0], "desc": v[1]} for k, v in prefs.MODULES.items()},
        "module_consent": {k: prefs.CONSENT[k] for k in prefs._consent_needed()},
        "module_consent_title": {k: prefs.CONSENT_TITLE.get(k) for k in prefs._consent_needed()},
        "plan": _plan_info(),
        "price_share": prefs.price_share_out((db.current_user() or {}).get("id")),  # topluluk fiyatı (kişi başı)
        **_handshake(),  # server: api_version, capabilities, flags, version (S17)
    }


def _handshake() -> dict:
    from app import version

    h = version.handshake(with_version=True)
    return {"api_version": h["api_version"], "capabilities": h["capabilities"], "app_update": h["app_update"],
            "server": {"version": h["version"], "flags": h["flags"]}}


def _plan_info() -> dict:
    """Paket bilgisi; hazır sürümde dosya alanı da (kendi sunucusunda sınır yok, alan gösterilmez)."""
    u = db.current_user()
    info = plans.info(u)
    if plans.hosted():
        info["limits"] = {**info["limits"], "storage_bytes": plans.storage_limit(u), "storage_used": db.storage_used()}
        from app import doc_reads

        info["reads"] = doc_reads.status(u)  # bugünkü okuma (tek günlük sayaç) ve ek paket (yönetici, oturumsuz: None)
        info["upgrade_yearly"] = plans.upgrade_yearly(u)
    return info


class ModulesIn(BaseModel):
    modules: dict[str, bool]
    consent: list[str] = []  # barındırılan sürüm: açık rıza verilen özellikler (ör. zekat)


@app.put("/api/settings/modules")
def put_modules(body: ModulesIn):
    """Hangi özellikler açık (defter sahibi; ortak defterde herkes için)."""
    try:
        return prefs.set_modules(body.modules, body.consent)
    except prefs.ConsentRequired as e:
        raise HTTPException(422, f"{prefs.MODULES[str(e)][0]} için açık rıza gerekli") from e


class ShopHiddenIn(BaseModel):
    chains: list[str] | None = Field(None, max_length=40)  # kapatılan zincirler ("CarrefourSA"); verilmezse korunur
    local: bool | None = None                              # yerel marketler (bilinen zincir dışı) kapalı mı


class UiIn(BaseModel):
    simple: bool | None = None
    layout: list[str] | None = None
    layout_mobile: list[str] | None = None  # mobil uygulamanın Özet düzeni
    reset_layout: bool = False
    price_share: bool | None = None  # fiyat paylaşımı (kişi başı; /api/me/price-share ile aynı tercih)
    region: str | None = Field(None, max_length=40)  # Bölgem: il adı (app/provinces.py); null / "" seçimi kaldırır
    stop_cost: float | None = Field(None, ge=0, le=10_000)  # "Nereye gideyim?": ekstra durak için en az tasarruf (₺)
    shop_hidden: ShopHiddenIn | None = None  # mağaza süzgeci (shop_store_filter; kişi başı, app/shop_stores.py)


@app.put("/api/me/ui")
def put_ui(body: UiIn):
    """Kişisel görünüm: sade mod, Özet düzeni (herkes kendi). price_share verilirse fiyat paylaşımı tercihi de yazılır
    (sistem veritabanında kişi başı; yanıtta da döner). region verilirse (gönderildiyse; null / "" kaldırır) Bölgem:
    81 ilden biri (bilinmeyen 422); resmî fiyatlar o ilden gelir. Yanıtta region döner. stop_cost (₺, 0–10000; null
    varsayılana döner: 100): alışveriş listesi "Nereye gideyim?" ekstra durağı bu kadar ucuzlatmıyorsa önermez.
    shop_hidden {chains?, local?}: kapatılan mağazalar (kişi başı; yanıtta shop_hidden {chains, local})."""
    data = body.model_dump(exclude_unset=True, exclude={"reset_layout", "price_share", "region", "shop_hidden"})
    if body.reset_layout:
        data["layout"] = None
    u = db.current_user()
    if "region" in body.model_fields_set:  # önce doğrula: geçersiz il diğer alanları yarım yazmasın
        from app import provinces

        if body.region and not provinces.valid(body.region):
            raise HTTPException(422, "Bilinmeyen il")
        if not u or not u.get("id"):
            raise HTTPException(409, "Bölge seçimi için giriş gerekli")
    out = prefs.set_ui(data)
    if body.price_share is not None:
        if not u or not u.get("id"):
            raise HTTPException(409, "Fiyat paylaşımı için giriş gerekli")
        out["price_share"] = prefs.set_price_share(u["id"], body.price_share)
    if "region" in body.model_fields_set:
        out["region"] = prefs.set_region(u["id"], body.region)
    if body.shop_hidden is not None:
        from app import shop_stores

        try:
            out["shop_hidden"] = shop_stores.put(body.shop_hidden.model_dump(exclude_unset=True))
        except ValueError as e:
            raise HTTPException(422, errors.user_text(e, 422)) from e
    return out


def _ui_out() -> dict:
    """meta.ui: görünüm tercihleri + price_share (fiyat paylaşımı, kişi başı) + region (Bölgem: il adı | null)."""
    uid = (db.current_user() or {}).get("id")
    from app import shopping

    from app import shop_stores

    return {**prefs.ui(), "price_share": prefs.price_share(uid), "region": prefs.region(uid),
            "stop_cost": shopping.stop_cost_pref(), "shop_hidden": shop_stores.get(uid)}


def _history_gate(month: str | None) -> None:
    """Barındırılan sürüm, ücretsiz paket: bu dönem + geçen dönem; daha eskisi Premium."""
    if not plans.history_ok(db.current_user(), month):
        raise HTTPException(402, detail=str(plans.PlanRequired("gecmis")), headers={"X-Plan-Required": "gecmis"})


@app.get("/api/summary")
def summary(month: str | None = None):
    """month verilmezse içinde bulunulan dönem (mobil uygulama ayı beklemeden ister)."""
    _history_gate(month)
    from app import insight_state

    out = finance.summary(month or finance.current_period())
    out["insights_hidden"] = insight_state.hidden_count(out["insights"])  # kişinin kapattığı uyarılar (önbellek dışında süzülür)
    out["insights"] = insight_state.visible(out["insights"])
    return out


@app.get("/api/installments")
def installments_due(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")):
    """Özet → "Bu ay ödenecek taksitler" listesi (yetenek summary_installments; finance.installments_due): dönemin
    taksitleri, harcamaya dahil olanlar (in_spend) ve ekstresi beklenenler, kalan taksit borcu. Her kalemde row: dokununca
    düzenlenecek satır (GET /api/transactions ile aynı biçim). Özet'in temel bilgisi: ücretsiz pakette de açık (geçmiş
    dönem kuralı /api/summary ile aynı). Ortak defterde her kalemde spender / spender_name (Kim harcadı kuralı)."""
    _history_gate(month)
    out = finance.installments_due(month or finance.current_period())
    if ppl := _shared_people():
        sp = finance.Spenders(ppl)
        for i in out["items"]:
            r = i["row"]
            r["spender"], r["spender_set"] = sp.of(r), sp.is_set(r)
            i["spender"] = r["spender"]
            i["spender_name"] = sp.name(r["spender"])
    for i in out["items"]:
        i["row"].setdefault("raw", None)
        i["row"].setdefault("note_user", i["row"].get("note"))
        i["row"]["counts_as_spend"], i["row"]["counts_as_income"] = finance.counts(i["row"])
    return FastJSON(out)


class InsightKeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f]+$")


@app.get("/api/insights/hidden")
def insights_hidden(month: str | None = None):
    """Bu dönemin gizlenen uyarıları: [{key, title, level, detail}] (Özet'te "N gizli uyarı · Göster" → Geri getir)."""
    _history_gate(month)
    from app import insight_state

    return insight_state.hidden(finance.summary(month or finance.current_period())["insights"])


@app.post("/api/insights/dismiss")
def insights_dismiss(body: InsightKeyIn):
    """Özet uyarısını kişi başına gizle (summary.insights[].key). Koşul değişince anahtar değişir, uyarı yeniden çıkar."""
    from app import insight_state

    return insight_state.dismiss(body.key)


@app.post("/api/insights/restore")
def insights_restore(body: InsightKeyIn):
    """Gizlemeyi geri al ("Geri al")."""
    from app import insight_state

    return insight_state.restore(body.key)


@app.get("/api/cards/summary")
def cards_summary(recent: int = Query(20, ge=0, le=200)):
    """Kartlar ekranı açılışı: kart başına son ekstre, açık dönem, taksit/abonelik, son N işlem (finance.cards_summary).
    Kartın tam geçmişi: GET /api/transactions?card=<card_no>&limit=…
    Ortak defterde her kartta spender: "u<id>" (kartın kişisi, PUT /api/cards/{card_no}/spender) ya da null (Ortak);
    recent satırlarında spender / spender_set (GET /api/transactions ile aynı)."""
    out = finance.cards_summary(recent=recent)
    if ppl := _shared_people():
        sp = finance.Spenders(ppl)
        for c in out["cards"]:
            c["spender"] = sp.card(c["card_no"])
            for r in c["recent"]:
                r["spender"], r["spender_set"] = sp.of(r), sp.is_set(r)
                if sp.left(r["spender"]):
                    r["spender_name"] = sp.name(r["spender"])  # ayrılan kişi: "Ayşe (ayrıldı)"
    return FastJSON(out)


class CardLimitIn(BaseModel):
    card_no: str = Field(..., min_length=1, max_length=40)  # ekstrenin kart numarası (cards/summary card_no)
    limit: float | None = Field(None, ge=0, le=100_000_000)  # None ya da 0: elle limiti kaldır (ekstredeki geçerli)


@app.put("/api/cards/limit")
def put_card_limit(body: CardLimitIn):
    """Kartın limitini elle gir/düzelt (ekstreden okunamayan bankalar, limit artışı). Yanıt: o kartın limit alanları
    (limit, limit_source "ekstre"|"elle", available, used_pct …; cards/summary ile aynı). Ortak defterde gizli kart
    görünmediği için bulunamaz; yazma yetkisi defter kurallarında (app/book.py: sahip ve tam yetkili)."""
    if not any(s["card_no"] == body.card_no for s in db.list_statements()):
        raise HTTPException(404, "Kart bulunamadı")
    db.set_card_limit(body.card_no, body.limit or None)
    card = next((c for c in finance.cards_summary(recent=0)["cards"] if c["card_no"] == body.card_no), None)
    if card is None:
        raise HTTPException(404, "Kart bulunamadı")
    return {k: card[k] for k in ("card_no", "limit", "limit_source", "statement_limit", "available", "available_source",
                                 "available_as_of", "used", "used_pct")}


@app.get("/api/transactions")
def transactions(month: str | None = None, include_deleted: bool = False, include_estimates: bool = False,
                 totals: bool = False, limit: int | None = Query(None, ge=1, le=5000),
                 before: str | None = Query(None, max_length=80), card: str | None = Query(None, max_length=40),
                 category: str | None = Query(None, max_length=60), subcategory: str | None = Query(None, max_length=40),
                 who: str | None = Query(None, pattern=r"^(u\d+|ortak)$"), q: str | None = Query(None, max_length=80)):
    """Gerçek işlemler. Tahmini satırlar (ekstresiz kart ödemesi, dökümsüz maaş; estimated: true) varsayılan listede yok:
    include_estimates=1 ile aynı diziye eklenir — istemci `estimated` ile ayırır, hiçbir toplama katmaz.
    Her satırda counts_as_spend / counts_as_income: gider/gelir toplamına girer mi (sunucu kuralı, finance.counts);
    süzgeç toplamı = bayrağı true olan satırların amount toplamı (iade eksi). totals=1: {"rows": [...], "totals": {...}}
    (Özet ile aynı _totals + count). category / subcategory: yalnız o ana kategori / alt kategori (yetenek
    "subcategories"; subcategory tek başına verilirse her ana kategorideki bu ad). who=u<id>|ortak: ortak defterde o
    kişinin harcamaları ("Kim ne harcadı" raporunun satırları; yalnız gider satırları, aynı harcayan kuralı:
    kayıtta elle seçilen → ekleyen → kart işleminde eşleşen kaydı ekleyen → kartın kişisi → Ortak; finance.Spenders).
    Ortak defterde her satırda spender ("u<id>"|"ortak") ve spender_set (kayıtta elle seçilmiş mi; PUT /api/spender).
    q: metin araması (yetenek "txn_search"): işyeri, açıklama, kategori, alt kategori, not, kişi ve tutar içinde; büyük/küçük
    harf ve Türkçe harf farkı gözetmez. month verilmezse TÜM dönemlerde arar (ücretsizde açık pencerede); her satırda
    month (dönem anahtarı) var, istemci döneme göre gruplar."""
    _history_gate(month)
    rows = finance.transactions(month, include_deleted, include_estimates)
    ppl = _shared_people()
    sp = finance.Spenders(ppl) if (who or ppl) else None
    if who:
        rows = [r for r in rows if finance.in_spend(r) and sp.of(r) == who]
    u = db.current_user()
    if month is None and not plans.allows(u, "gecmis"):  # tüm aylar istendi: ücretsizde görünür pencereyle sınırlı
        rows = [r for r in rows if plans.history_ok(u, r["month"])]
    if card:  # tek kartın satırları (ekstre ana kart numarası; elle kayıtta son 4 hane): Kartlar ekranı geçmişi
        rows = [r for r in rows if finance.of_card(r, card)]
    if category:
        rows = [r for r in rows if r["category"] == category]
    if subcategory:
        rows = [r for r in rows if r.get("subcategory") == subcategory]
    if q and q.strip():
        rows = [r for r in rows if _txn_matches(r, q)]
    for r in rows:  # her satırda: raw (banka açıklaması, yoksa null), note_user (kullanıcının kendi notu)
        r.setdefault("raw", None)
        r.setdefault("note_user", r.get("note"))
        r["counts_as_spend"], r["counts_as_income"] = finance.counts(r)
        if ppl:
            r["spender"], r["spender_set"] = sp.of(r), sp.is_set(r)
            if sp.left(r["spender"]):
                r["spender_name"] = sp.name(r["spender"])  # ayrılan kişi: "Ayşe (ayrıldı)"
    if not (totals or limit or before):
        return FastJSON(rows)  # eski biçim: düz dizi (jsonable_encoder atlanır; içerik bayt bayt aynı)
    out: dict = {}
    if totals:  # toplamlar sayfanın değil, istenen kümenin tamamı (ay ya da tüm geçmiş)
        out["totals"] = {**finance._totals([r for r in rows if not r.get("estimated")]), "count": len(rows)}
    if limit or before:
        page, nxt = _paginate(rows, limit or 500, before)
        out.update(rows=page, next=nxt)
    else:
        out["rows"] = rows
    return FastJSON(out)


def _search_fold(s: str) -> str:
    """Arama karşılaştırması (web format.fold ile aynı): Türkçe küçük harf, aksansız, ı → i."""
    import unicodedata

    s = (s or "").replace("I", "ı").replace("İ", "i").lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if not unicodedata.combining(ch))
    return s.replace("ı", "i")


def _txn_matches(r: dict, q: str) -> bool:
    """İşlem araması: her kelime satırın metninde geçmeli. Tutar hem "1231.5" hem "1.231,50" biçiminde aranır."""
    a = float(r.get("amount") or 0)
    nums = f"{int(a)}" if a == int(a) else f"{a}"
    tr = f"{a:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    hay = _search_fold(" ".join(str(r.get(k) or "") for k in ("merchant", "description", "category", "subcategory",
                                                                 "note", "counterparty", "card")) + f" {nums} {tr}")
    return all(w in hay for w in _search_fold(q).split())


def _cursor(r: dict) -> str:
    return f"{r['date'][:10]}|{r['uid']}"


def _paginate(rows: list[dict], limit: int, before: str | None) -> tuple[list[dict], str | None]:
    """Sayfalama (satırlar tarih ve uid'ye göre yeniden eskiye): before = önceki sayfanın next'i ("YYYY-MM-DD|uid"),
    bu satırdan SONRAKİLER (daha eskiler) döner. next: sıradaki sayfanın imleci, son sayfada null."""
    start = 0
    if before:
        d, _, uid = before.partition("|")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or not uid:
            raise HTTPException(422, "before biçimi: YYYY-MM-DD|uid (önceki yanıtın next alanı)")
        key = (d, uid)
        start = next((i for i, r in enumerate(rows) if (r["date"][:10], r["uid"]) < key), len(rows))
    page = rows[start:start + limit]
    return page, (_cursor(page[-1]) if start + limit < len(rows) and page else None)


@app.get("/api/plan")
def plan(months: int = 6):
    return finance.plan(plans.plan_months(db.current_user(), max(1, min(months, 24))))  # ücretsiz: 1 ay


class SimulateIn(BaseModel):
    amount: Money = Field(gt=0)
    installments: int = Field(ge=1, le=36)
    monthly_rate: float = Field(default=0.0, ge=0, le=0.2)  # 0.0375 = aylık %3,75 (vade farkı / kâr payı)
    start_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


@app.post("/api/plan/simulate")
def simulate(body: SimulateIn):
    """Alırsam ne olur: taksit planı, toplam/ek maliyet, ay ay net öncesi→sonrası."""
    return finance.simulate_purchase(body.amount, body.installments, body.monthly_rate, body.start_month)


# ---------------------------------------------------------------- card transactions

class CardTxnPatch(BaseModel):
    category: str | None = None
    subcategory: str | None = Field(None, max_length=60)  # alt kategori ("" / null: kaldır); ana kategori değişip bu
    # alan gönderilmezse eski alt kategori silinir (eski istemci)
    txn_type: CardKind | None = None
    note: str | None = None
    description: str | None = None
    is_deleted: bool | None = None
    hidden: bool | None = None  # ortak defterde üyeye gösterme (sadece sahip)
    create_rule: bool = False  # kategori değişince aynı işyeri için kural oluştur


@app.patch("/api/card-transactions/{txn_id}")
def patch_card_txn(txn_id: int, body: CardTxnPatch):
    from app import book

    fields = body.model_dump(exclude_unset=True, exclude={"create_rule"})
    if book.member():  # üye: görebildiği işlemde sadece kategori ve not
        if not book.can_see_txn(txn_id):
            raise HTTPException(404, "İşlem bulunamadı")
        fields = {k: v for k, v in fields.items() if k in ("category", "subcategory", "note")}
        body.create_rule = False
    for k in ("is_deleted", "hidden"):
        if k in fields:
            fields[k] = int(fields[k])
    txn = next((t for t in db.card_transactions(True) if t["id"] == txn_id), None)
    _patch_subcategory(fields, txn)
    db.update_card_transaction(txn_id, fields)
    rule = None
    if body.create_rule and body.category and txn:
        rule = suggest_pattern(txn["description"])
        db.add_rule(rule, body.category, subcategory=fields.get("subcategory"))
        db.recategorize_all()
    return {"ok": True, "rule": rule}


# ---------------------------------------------------------------- manual entries

class EntryIn(BaseModel):
    entry_date: date
    description: str = Field(min_length=1)
    amount: Money = Field(ge=0)
    kind: Kind
    category: str | None = None
    subcategory: str | None = Field(None, max_length=60)  # ana kategorinin altındaki alt kategori (isteğe bağlı)
    note: str | None = None
    recurring_id: int | None = None
    skipped: bool = False
    payment: Payment | None = None
    wallet_id: int | None = None  # ödeme 'kart' ise hangi ön ödemeli kart
    counterparty: str | None = None  # kişi (Transferler / borç defteri)
    splits: list[dict] | None = None  # formda onaylanan alt kalemler [{amount, category, description}]
    items: list[dict] | None = None  # fiş satırları: fiyat takibi için saklanır
    location: str | None = None  # fişteki işyeri adresi (opsiyonel)
    branch: str | None = None  # fişteki şube/semt (fiyat geçmişinde)
    legal_name: str | None = None  # fişteki resmi unvan: saklanır (muhasebeci listesi) ve eşleştirme öğrenilir
    account_id: int | None = None  # banka/nakit hesabı
    installments: int = Field(1, ge=1, le=36)  # kredi kartı taksit sayısı
    card_last4: str | None = Field(None, pattern=r"^\d{4}$")  # hangi kredi kartı (fişte yazıyorsa okunur)
    receipt_file: str | None = Field(None, pattern=r"^[0-9a-f]{64}\.jpg$")  # okuma yanıtındaki fiş fotoğrafı
    # kim harcadı (ortak defter, yetenek save_book_choice): "u<id>" | "ortak"; otomatikten (ekleyen) farklıysa saklanır
    spender: str | None = Field(None, max_length=20)
    # fişin vergi bilgisi (okuma yanıtındaki alanlar; app/vat.py)
    tax_no: str | None = Field(None, max_length=20)  # satıcının VKN'si: 10 hane, kontrol hanesi tutmalı
    tax_office: str | None = Field(None, max_length=80)
    vat_total: Money | None = Field(None, ge=0)
    vat_lines: list[dict] | None = Field(None, max_length=10)  # [{rate: 1|10|20, amount}]


class EntryPatch(BaseModel):
    entry_date: date | None = None
    description: str | None = None
    amount: Money | None = Field(default=None, ge=0)
    kind: Kind | None = None
    category: str | None = None
    subcategory: str | None = Field(None, max_length=60)  # "" / null: kaldır; gönderilmez ve ana kategori değişirse silinir
    note: str | None = None
    skipped: bool | None = None
    payment: Payment | None = None
    location: str | None = None
    wallet_id: int | None = None  # ödeme 'kart' ise hangi ön ödemeli kart
    counterparty: str | None = None  # kişi (Transferler / borç defteri)
    account_id: int | None = None  # banka/nakit hesabı
    installments: int | None = Field(None, ge=1, le=36)
    card_last4: str | None = Field(None, pattern=r"^\d{4}$")
    hidden: bool | None = None  # ortak defterde üyeye gösterme (sadece sahip)
    legal_name: str | None = Field(None, max_length=160)  # fişin vergi bilgisi; "" ya da null → sil
    tax_no: str | None = Field(None, max_length=20)
    tax_office: str | None = Field(None, max_length=80)
    vat_total: Money | None = Field(None, ge=0)
    vat_lines: list[dict] | None = Field(None, max_length=10)


def _tax_fields(data: dict) -> None:
    """Fişin vergi alanları (yerinde): boş → None, VKN 10 hane ve kontrol hanesi tutmalı (11 haneli TCKN saklanmaz),
    KDV dökümü JSON metni olarak; dökümü verilip toplamı verilmediyse toplam dökümden."""
    import json as _json

    from app import vat

    if "legal_name" in data:
        data["legal_name"] = (data["legal_name"] or "").strip()[:120] or None
    if "tax_no" in data:
        raw = re.sub(r"[\s.\-/]", "", data["tax_no"] or "")
        if raw and not vat.vkn(raw):
            raise HTTPException(422, "Vergi numarası 10 haneli olmalı (11 haneli T.C. kimlik numarası saklanmaz); "
                                     "fişteki numarayı kontrol et.")
        data["tax_no"] = raw or None
    if "tax_office" in data:
        data["tax_office"] = vat.tax_office(data["tax_office"])
    if "vat_lines" in data:
        lines = vat.lines_of(data["vat_lines"] or [])
        data["vat_lines"] = _json.dumps(lines) if lines else None
        if lines and data.get("vat_total") is None:
            data["vat_total"] = round(sum(x["amount"] for x in lines), 2)
    if "vat_total" in data and not data["vat_total"]:
        data["vat_total"] = None


def _save_spender(who: str | None) -> tuple[list[dict], int | None] | None:
    """Kaydederken seçilen "kim harcadı" (POST /api/entries, /api/bills; yetenek save_book_choice): yalnız ortak
    defterde ve paket "aile_rapor" açıkken; başka yerde sessizce yok sayılır (None). Defterde olmayan kişi 400
    (PUT /api/spender gibi). (kişiler, user_id | None=Ortak) döner. Kayıt oluşturulmadan ÖNCE çağrılır."""
    if who is None:
        return None
    u = db.current_user()
    ppl = _shared_people()
    if not ppl or (plans.hosted() and not plans.allows(u, "aile_rapor")):
        return None
    return ppl, _spender_uid(who, ppl)


def _apply_spender(choice: tuple[list[dict], int | None] | None, entry_id: int | None) -> None:
    """Seçim otomatik harcayandan (kaydı ekleyen) farklıysa elle seçim olarak sakla; aynıysa hiçbir şey yazılmaz."""
    if not choice or not entry_id:
        return
    ppl, uid = choice
    row = finance.spender_row("manual", entry_id)
    want = finance.SHARED if uid is None else f"u{uid}"
    if row and finance.Spenders(ppl).of(row) != want:
        db.set_spender_override("manual", entry_id, uid)


def _adopt_file(kind: str, name: str | None) -> bool:
    """Dosya bu defterde saklı mı? Değilse ve istek ortak defter üyesinden geliyorsa, AYNI üyenin öbür defterinde
    (ortak defter ↔ "Benim"; X-HK-Book ile öbür deftere kaydederken) saklı dosya bu deftere kopyalanır. Başka hiçbir
    defterden alınmaz. Ortak defterden "Benim"e yalnız hiçbir kayda bağlı olmayan (yeni okunmuş) ya da yalnız üyenin
    kendi kayıtlarına bağlı dosya kopyalanır (sahibin gizli kaydının fotoğrafı sızmasın). Okuma hakkı yeniden düşmez."""
    from app import storage

    if not storage.valid_name(kind, name):
        return False
    if db.file_indexed(kind, name):
        return True
    u = db.current_user()
    base = system.user(u["id"]) if u and u.get("member_of") and u.get("id") else None
    if not base or base.get("member_of") != u.get("member_of"):
        return False
    if u.get("own_book"):  # "Benim" defterine kaydediliyor: dosya ortak defterde okunmuştu
        src, src_user = system.user_db_path(base), base
    else:  # ortak deftere kaydediliyor: dosya "Benim" defterinde okunmuştu
        src, src_user = system.own_db_path(base), {**base, "own_book": True}
    path = src or db.DB_PATH  # None: ana veritabanı (yöneticinin ortak defteri)
    if os.path.abspath(path) == os.path.abspath(db.db_path()) or not os.path.exists(path):
        return False
    with db.use_space(src, src_user):
        if not db.file_indexed(kind, name):
            return False
        if u.get("own_book"):  # kaynak ortak defter: dosya kimin kaydına bağlı?
            with db.connect() as c:
                if kind == "receipts":
                    owners = {r[0] for r in c.execute("SELECT created_by FROM entries WHERE receipt_file = ?", (name,))}
                else:
                    owners = {None} if c.execute("SELECT 1 FROM bills WHERE file_name = ?", (name,)).fetchone() else set()
            if owners - {base["id"]}:
                return False
        try:
            data = db.read_file(kind, name)
        except storage.StorageError:
            return False
    if not data:
        return False
    try:
        return db.store_file(kind, name, data, storage.content_type(name))
    except (storage.StorageError, ValueError):
        return False


@app.post("/api/entries")
def create_entry(body: EntryIn):
    """Kayıt ekle. Ortak defter (yetenek save_book_choice): spender "u<id>" | "ortak" — kim harcadı, otomatikten
    (ekleyen) farklıysa elle seçim olarak saklanır; ortak defter değilse yok sayılır, defterde olmayan kişi 400. Üye
    fişi öbür defterine kaydedebilir (X-HK-Book: kendi | ortak): okumadaki receipt_file öbür defterdeyse kopyalanır."""
    _visible_or_404("account", body.account_id)  # üye gizli hesaba kayıt yazamaz
    choice = _save_spender(body.spender)
    data = body.model_dump(exclude={"splits", "items", "branch", "spender"})
    _tax_fields(data)
    data["entry_date"] = body.entry_date.isoformat()
    data["skipped"] = int(body.skipped)
    data["subcategory"] = _subcategory(body.category, body.subcategory)
    if body.legal_name and body.kind == "gider":
        db.learn_from_receipt(body.legal_name, body.description, body.category)
    if data.get("counterparty"):
        data["counterparty"] = db.known_counterparty(data["counterparty"].strip())
    _check_wallet(data)
    if data.get("payment") == "nakit" and not data.get("account_id") and not data.get("wallet_id"):
        # tek nakit hesabı (cüzdan) varsa nakit harcama/gelir ondan düşer/ona eklenir; birden fazlaysa kullanıcı seçer
        cash = [a for a in db.list_accounts() if a["kind"] == "nakit"]
        if len(cash) == 1:
            data["account_id"] = cash[0]["id"]
    if (u := db.current_user()):
        data["created_by"] = u["id"]  # ortak defterde kimin eklediği
    if data.get("receipt_file") and not _adopt_file("receipts", data["receipt_file"]):
        data["receipt_file"] = None  # bu defterde (ya da üyenin öbür defterinde) saklanmış bir fotoğraf değil: bağlanmaz
    eid = db.create_entry(data)
    alerts = {"check": {}, "items": [i for i in body.items or [] if isinstance(i, dict)], "merchant": body.description}
    if body.items and body.kind == "gider":
        _add_price_alerts(alerts, eid, data["entry_date"], market=False)  # kalemler yazılmadan: önceki alımlarla
        db.save_receipt_items(eid, [i for i in body.items if isinstance(i, dict)], body.description, data["entry_date"],
                              total=body.amount, branch=body.branch)
        if data.get("receipt_file"):  # yalnız okunmuş fiş (elle girilen değil): anonim topluluk fiyatı (tercih açıksa)
            _share_prices(body.description, body.legal_name, body.items, data["entry_date"], body.branch, body.amount)
    splits = []
    if body.splits and body.kind == "gider":
        try:
            splits = db.set_splits("entry", eid, [p for p in body.splits if isinstance(p, dict)
                                                  and isinstance(p.get("amount"), (int, float))], origin="fis")
        except ValueError as e:
            db.delete_entry(eid)
            raise HTTPException(422, errors.user_text(e, 422)) from e
    db.auto_link()  # ekstre önceden yüklendiyse hemen eşleşsin
    from app import sync

    sync.reconcile()  # döküm önceden yüklendiyse: dökümdeki aynı hareketle kesin eşleşen kayıt birleşir (app/sync.py)
    debt = db.link_debt_movement(data.get("counterparty"), body.kind, body.amount, data["entry_date"], eid)
    _apply_spender(choice, eid)
    match = sync.match_info().get(eid)
    shopping_link = _shopping_link(eid) if body.items and body.kind == "gider" else None  # kalemler kaydedildikten sonra
    return {"id": eid, "linked": any(e["id"] == eid and e["linked_txn_id"] for e in db.list_entries()), "match": match,
            "debt_settled": debt[1]["id"] if debt else None, "debt_step": debt[0] if debt else None,
            "splits": [{"category": x["category"], "amount": x["amount"]} for x in splits],
            "price_alerts": alerts["check"].get("price_alerts", []),  # yetenek price_alerts
            "shopping_link": shopping_link}  # yetenek shopping_receipt_link


def _shopping_link(entry_id: int) -> dict | None:
    """Kaydedilen fiş alışveriş listesine ait mi (app/shopping_trips.py on_receipt_saved): {status linked|ask, …} |
    None. Paketinde alışveriş listesi yoksa ya da ortak defterde sayfa kapalıysa None. Hata kaydı bozmaz."""
    import logging

    from app import book, shopping_trips

    try:
        if not plans.allows(db.current_user(), "alisveris_listesi"):
            return None
        if book.member() and book.denied("GET", "/api/shopping-list"):
            return None
        return shopping_trips.on_receipt_saved(entry_id)
    except Exception:  # noqa: BLE001
        logging.getLogger("hesapkitap.shopping").warning("fiş listeye bağlanamadı", exc_info=True)
        return None


def _share_prices(merchant: str | None, legal_name: str | None, items: list, on: str, branch: str | None = None,
                  total: float | None = None) -> None:
    """Fiş kalemlerini anonim topluluk fiyatına kat (app/community_prices.py; branch: fişteki şube yazısı, yalnız il adı
    için; total: kaydın tutarı — kalemler tutmuyorsa hiçbiri yazılmaz). Hata kaydı bozmaz."""
    from app import community_prices

    try:
        u = db.current_user()
        community_prices.contribute((u or {}).get("id"), merchant, legal_name, [i for i in items if isinstance(i, dict)], on,
                                    branch=branch, receipt_total=total)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("hesapkitap.community_prices").warning("topluluk fiyatı yazılamadı", exc_info=True)


@app.get("/api/me/price-share")
def get_price_share():
    """Fiyat paylaşımı tercihi (kişi başı, varsayılan açık): {on, text (gizlilik açıklaması)}."""
    u = db.current_user()
    return prefs.price_share_out((u or {}).get("id"))


class PriceShareIn(BaseModel):
    on: bool


@app.put("/api/me/price-share")
def put_price_share(body: PriceShareIn):
    u = db.current_user()
    if not u or not u.get("id"):
        raise HTTPException(409, "Fiyat paylaşımı için giriş gerekli")
    prefs.set_price_share(u["id"], body.on)
    return prefs.price_share_out(u["id"])


@app.patch("/api/entries/{entry_id}")
def patch_entry(entry_id: int, body: EntryPatch):
    from app import book

    data = body.model_dump(exclude_unset=True)
    _visible_or_404("account", data.get("account_id"))
    if "note" in data:  # notun ilk satırındaki banka açıklaması (ham metin) korunur; kullanıcı notu altına (app/rawnote.py)
        from app import rawnote

        with db.connect() as c:
            cur = c.execute("SELECT note FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if cur:
            data["note"] = rawnote.with_user_note(cur["note"], data["note"])
    if (m := book.member()):  # üye: görebildiği kayıt; başkasının kaydında sadece kategori/not/açıklama
        if not book.can_see_entry(entry_id):
            raise HTTPException(404, "Kayıt bulunamadı")
        with db.connect() as c:
            owner = c.execute("SELECT created_by FROM entries WHERE id = ?", (entry_id,)).fetchone()[0]
        data.pop("hidden", None)
        if owner != m["id"]:
            data = {k: v for k, v in data.items() if k in book.ENTRY_MEMBER_FIELDS}
    if "category" in data or "subcategory" in data:
        with db.connect() as c:
            cur = c.execute("SELECT category, subcategory FROM entries WHERE id = ?", (entry_id,)).fetchone()
        _patch_subcategory(data, dict(cur) if cur else None)
    if "hidden" in data:
        data["hidden"] = int(bool(data["hidden"]))
    _tax_fields(data)
    if "entry_date" in data:
        data["entry_date"] = data["entry_date"].isoformat()
    if "skipped" in data:
        data["skipped"] = int(data["skipped"])
    if data.get("payment") and data["payment"] != "kredi_karti":
        data["linked_txn_id"] = None  # kartla ödenmediyse eşleşme anlamsız
    if data.get("counterparty"):
        data["counterparty"] = db.known_counterparty(data["counterparty"].strip())
    elif "counterparty" in data:
        data["counterparty"] = None
    if "payment" in data or "wallet_id" in data or "account_id" in data:
        _check_wallet(data)
    split_total = sum(p["amount"] for p in db.splits_map().get(("entry", entry_id), []))
    if "amount" in data and money.lt(data["amount"], split_total):
        raise HTTPException(422, f"Bu kayıt bölünmüş: alt kalemlerin toplamı {_tl_tr(split_total)}. Tutar bundan az "
                                 "olamaz; önce bölmeyi düzelt.")
    db.update_entry(entry_id, data)
    db.auto_link()
    return {"ok": True}


class LinkIn(BaseModel):
    txn_id: int | None  # None = eşleşmeyi kaldır


@app.get("/api/entries/{entry_id}/receipt")
def entry_receipt(entry_id: int):
    """Kaydın fiş fotoğrafı. Kayıt bu defterden okunur; ortak defter üyesi göremediği (gizli) kaydın fotoğrafını da göremez."""
    from app import book

    with db.connect() as c:
        r = c.execute("SELECT id, entry_date, receipt_file FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not r or not r["receipt_file"] or (book.member() and not book.can_see_entry(entry_id)):
        raise HTTPException(404, "Fotoğraf yok")
    pdf = r["receipt_file"].endswith(".pdf")  # havale dekontu PDF olarak saklanmış olabilir (app/dekont_parse.py)
    return _stored_file("receipts", r["receipt_file"], f"{'dekont' if pdf else 'fis'}-{r['entry_date']}-{entry_id}",
                        inline=True)


@app.get("/api/entries/{entry_id}/candidates")
def link_candidates(entry_id: int):
    return db.link_candidates(entry_id)


@app.post("/api/entries/{entry_id}/link")
def link_entry(entry_id: int, body: LinkIn):
    _visible_or_404("txn", body.txn_id)
    try:
        db.link_entry(entry_id, body.txn_id)
    except ValueError as e:
        raise HTTPException(409, errors.user_text(e, 409)) from e
    return {"ok": True}


# ---------------------------------------------------------------- eşitleme: elle girilen ↔ döküm / ekstre (app/sync.py)

class MatchIn(BaseModel):
    other: str = Field(pattern=r"^[et]\d{1,12}$")  # e<dökümden gelen kayıt id> | t<ekstre satırı id>
    same: bool                                     # True: aynı (birleştir) · False: farklı (bir daha sorma)


def _match_guard(entry_id: int) -> None:
    from app import book

    if book.member() and not book.can_see_entry(entry_id):
        raise HTTPException(404, "Kayıt bulunamadı")


@app.post("/api/entries/{entry_id}/match")
def match_entry(entry_id: int, body: MatchIn):
    """"Bu ikisi aynı mı?" cevabı. Aynı: elle girilen kayıt kalır, dökümdeki satır ona bağlanır (geri alınabilir)."""
    from app import sync, undo

    _match_guard(entry_id)
    _visible_or_404("entry" if body.other[0] == "e" else "txn", int(body.other[1:]))
    try:
        if not body.same:
            return sync.decide(entry_id, body.other, False)
        with undo.batch(f"Eşleştirme: kayıt #{entry_id}") as b:
            res = sync.decide(entry_id, body.other, True)
            b["summary"] = res
        return {**res, "undo_id": b["id"]}
    except sync.SyncError as e:
        raise HTTPException(409, errors.user_text(e, 409)) from e


@app.post("/api/entries/{entry_id}/unmatch")
def unmatch_entry(entry_id: int):
    """Eşleşmeyi geri al: elle girilen ve dökümden/ekstreden gelen iki ayrı kayda döner; bir daha birleşmez."""
    from app import sync, undo

    _match_guard(entry_id)
    try:
        with undo.batch(f"Eşleşme geri alındı: kayıt #{entry_id}") as b:
            res = sync.unmatch(entry_id)
            b["summary"] = res
    except KeyError as e:
        raise HTTPException(404, "Kayıt bulunamadı") from e
    except sync.SyncError as e:
        raise HTTPException(409, errors.user_text(e, 409)) from e
    return {**res, "undo_id": b["id"]}


@app.get("/api/match-rules")
def match_rules():
    """Öğrenilen eşleşmeler: dökümdeki kalıp ↔ elle yazılan açıklama (iki onaydan sonra kendiliğinden birleşir)."""
    from app import sync

    return sync.list_rules()


@app.delete("/api/match-rules/{rule_id}")
def delete_match_rule(rule_id: int):
    from app import sync

    if not sync.delete_rule(rule_id):
        raise HTTPException(404, "Kural yok")
    return {"ok": True}


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int):
    db.delete_entry(entry_id)
    return {"ok": True}


# ---------------------------------------------------------------- recurring

class RecurringIn(BaseModel):
    description: str = Field(min_length=1)
    amount: Money = Field(ge=0)
    kind: Kind
    category: str | None = None
    day_of_month: int = Field(default=1, ge=1, le=31)
    start_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    end_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    note: str | None = None
    gross_amount: Money | None = Field(default=None, ge=0)
    advance_pay: bool | None = None  # maaş peşin (memur): zam farkı ilk zamlı maaşta
    account_id: int | None = None  # banka/nakit hesabı


class RecurringPatch(BaseModel):
    description: str | None = None
    amount: Money | None = Field(default=None, ge=0)
    kind: Kind | None = None
    category: str | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    start_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    end_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    note: str | None = None
    gross_amount: Money | None = Field(default=None, ge=0)
    advance_pay: bool | None = None  # maaş peşin (memur): zam farkı ilk zamlı maaşta
    account_id: int | None = None  # banka/nakit hesabı


class RecurringAction(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: Money | None = Field(default=None, ge=0)  # o ay farklı tutar olduysa


@app.get("/api/payroll/net")
def payroll_net(gross: float, year: int | None = None, start_month: int = 1):
    """Brütten nete ay ay döküm (2026 parametreleri; tanımsız yıl için en yakın yıl)."""
    from app.payroll import net_by_month

    if gross <= 0 or not 1 <= start_month <= 12:
        raise HTTPException(422, "Geçersiz değer")
    return net_by_month(gross, year or date.today().year, start_month)


class SalaryChangeIn(BaseModel):
    """Yeni maaş üç biçimde: gross (brüt) | net (o ayın neti) | pct + basis ("%15 zam", brüte ya da nete göre).
    Sunucu hepsini brüte çevirip kaydeder (app/salary.py resolve_draft; yetenek salary_raise)."""
    from_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    gross: Money | None = Field(default=None, gt=0)
    net: Money | None = Field(default=None, gt=0)
    pct: float | None = Field(default=None, gt=-50, le=300)
    basis: Literal["brut", "net"] = "brut"
    reason: Literal["zam", "is_degisikligi"] = "zam"
    reset_cum: bool = False
    pay_day: int | None = Field(default=None, ge=1, le=31)
    employer: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _one_amount(self):
        if self.gross is None and self.net is None and not self.pct:
            raise ValueError("Yeni brüt, net ya da zam yüzdesi gerekli")
        return self


def _resolve_salary(rid: int, draft: SalaryChangeIn, changes: list[dict] | None = None) -> dict:
    from app import salary

    r = next((x for x in db.list_recurring() if x["id"] == rid), None)
    if not r:
        raise HTTPException(404, "Sabit kalem yok")
    try:
        return salary.resolve_draft(r, db.salary_changes(rid) if changes is None else changes, draft.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


class PayrollPlanIn(BaseModel):
    year: int
    draft: SalaryChangeIn | None = None  # kaydetmeden önce "olursa ne olur" önizlemesi


@app.get("/api/recurring/{rid}/salary-changes")
def list_salary_changes(rid: int):
    return db.salary_changes(rid)


@app.post("/api/recurring/{rid}/salary-changes")
def add_salary_change(rid: int, body: SalaryChangeIn):
    res = _resolve_salary(rid, body)
    data = {**body.model_dump(), "gross": res["gross"], "pct_basis": body.basis}
    try:
        return {"id": db.add_salary_change(rid, data), "gross": res["gross"]}
    except KeyError as e:
        raise HTTPException(404, "Sabit kalem yok") from e


@app.delete("/api/salary-changes/{cid}")
def delete_salary_change(cid: int):
    if not db.delete_salary_change(cid):
        raise HTTPException(404, "Kayıt yok")
    return {"ok": True}


@app.post("/api/recurring/{rid}/payroll")
def recurring_payroll(rid: int, body: PayrollPlanIn):
    """Bir yılın ay ay brüt/net dökümü; kayıtlı değişiklikler + (varsa) taslak değişiklik."""
    from app.payroll import net_schedule

    r = next((x for x in db.list_recurring() if x["id"] == rid), None)
    if not r:
        raise HTTPException(404, "Sabit kalem yok")
    saved = db.salary_changes(rid)
    changes = [c for c in saved if not body.draft or c["from_month"] != body.draft.from_month]
    resolved = None
    if body.draft:
        resolved = _resolve_salary(rid, body.draft, saved)  # yüzde / net → brüt (+ peşin maaş farkı metni)
        changes = sorted([*changes, {**body.draft.model_dump(), "gross": resolved["gross"], "id": None}],
                         key=lambda c: c["from_month"])
    if not (r.get("gross_amount") or changes):
        raise HTTPException(422, "Brüt maaş tanımlı değil")
    grosses, resets = db.gross_schedule(r, body.year, changes)
    return {**net_schedule(body.year, grosses, resets), "resets": sorted(resets), "draft_resolved": resolved}


@app.get("/api/recurring")
def list_recurring():
    return db.list_recurring()


def _with_gross_net(data: dict) -> dict:
    """Brüt maaşta şablon tutar = bu ayın neti (listelerde görünen; plan her ay kendi netini hesaplar)."""
    if data.get("gross_amount"):
        from app.payroll import net_for

        t = date.today()
        data["amount"] = net_for(data["gross_amount"], t.year, t.month)
    return data


@app.post("/api/recurring")
def create_recurring(body: RecurringIn):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    return {"id": db.create_recurring(_with_gross_net(body.model_dump()))}


@app.patch("/api/recurring/{rid}")
def patch_recurring(rid: int, body: RecurringPatch):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    db.update_recurring(rid, _with_gross_net(body.model_dump(exclude_unset=True)))
    return {"ok": True}


@app.delete("/api/recurring/{rid}")
def delete_recurring(rid: int):
    db.delete_recurring(rid)
    return {"ok": True}


def _recurring_or_404(rid: int) -> dict:
    r = next((r for r in db.list_recurring() if r["id"] == rid), None)
    if not r:
        raise HTTPException(404, "Sabit kalem bulunamadı")
    return r


@app.post("/api/recurring/{rid}/confirm")
def confirm_recurring(rid: int, body: RecurringAction):
    """Sabit kalem bu ay gerçekleşti → elle girilmiş kayda dönüştür."""
    from app import sync

    r = _recurring_or_404(rid)
    eid = db.create_entry({
        "entry_date": db.occurrence_date(body.month, db.day_at(r, body.month)), "month": body.month, "description": r["description"],
        "amount": body.amount if body.amount is not None else db.planned_amount(r, body.month), "kind": r["kind"],
        "category": r["category"], "recurring_id": rid, "skipped": 0, "account_id": r["account_id"],
    })
    sync.reconcile()  # kira dökümden zaten geldiyse: aynı hareketle birleşir, iki kez sayılmaz
    return {"id": eid}


class MonthAmount(BaseModel):
    amount: Money | None = Field(default=None, ge=0)  # None = aya özel tutarı kaldır (şablona dön)


@app.put("/api/recurring/{rid}/months/{month}")
def set_month_amount(rid: int, month: str, body: MonthAmount):
    """Sabit kalemin belirli bir ay için planlanan tutarı (ör. değişken net maaş)."""
    _recurring_or_404(rid)
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(422, "Ay YYYY-MM olmalı")
    db.set_recurring_override(rid, month, body.amount)
    return {"ok": True}


@app.post("/api/recurring/{rid}/skip")
def skip_recurring(rid: int, body: RecurringAction):
    """Sabit kalem bu ay olmadı (ör. psikoloğa gidilmedi)."""
    r = _recurring_or_404(rid)
    return {"id": db.create_entry({
        "entry_date": db.occurrence_date(body.month, db.day_at(r, body.month)), "month": body.month,
        "description": r["description"], "amount": 0, "kind": r["kind"],
        "category": r["category"], "recurring_id": rid, "skipped": 1,
    })}


# ---------------------------------------------------------------- banka hesap hareketleri

def _bank_profile_key(columns: list[str]) -> str:
    import hashlib

    return "bank_profile:" + hashlib.sha1("|".join(c.strip().casefold() for c in columns).encode()).hexdigest()[:16]


# Önizleme ile içe aktarma arasında yapay zekâ çağrısını tekrarlamamak için. Anahtar: (defter, dosya özeti) — başka
# defterin şifresiyle açılmış sonuç bu deftere verilmez. Uçlar iş parçacığında çalışır: erişim kilitle.
_PDF_CACHE: dict[tuple[str, str], dict] = {}
_PDF_CACHE_LOCK = threading.Lock()


def _owners() -> list[str]:
    """Hesap sahibi adları: kart sahipleri + dökümlerden öğrenilen hesap sahibi (kendi hesapları arası virman)."""
    return db.card_holders() + db.owner_names()


class QuotaFull(HTTPException):
    """Belge okuma hakkı doldu (hazır sürüm): yanıt {"detail", "manual": true, "quota_full", "limit", "upgrade", ...}.
    İstemci elle girişe yönlendirir; fişte okunmadan saklanan fotoğraf (receipt_file) gider formuna bağlanabilir."""

    def __init__(self, status: int, e, **extra):
        super().__init__(status, str(e))
        self.extra = {**e.fields(), **extra}


@app.exception_handler(QuotaFull)
async def _quota_full(request: Request, exc: QuotaFull):
    return JSONResponse({"detail": exc.detail, **exc.extra}, status_code=exc.status_code)


# Belge okuyan uçlar (PDF/OCR/yapay zekâ/Excel/SQLite yazma) senkron `def`: FastAPI onları iş parçacığı havuzunda
# çalıştırır (bağlam — db.use_space — kopyalanır), olay döngüsü ve diğer kullanıcıların istekleri beklemez. Bu yüzden
# yardımcılar da senkron: yüklenen dosya UploadFile.file (SpooledTemporaryFile) üzerinden doğrudan okunur.

def _file_bytes(file: UploadFile) -> bytes:
    file.file.seek(0)
    return file.file.read()


def _doc_check(file: UploadFile, status: int = 422) -> bytes:
    """Hazır sürüm: belge okuma hakkı doluysa dosyayı okumadan reddet (app/doc_reads.py). Dosyanın baytları (sayım
    anahtarı) döner; dosya başa sarılır."""
    from app import doc_reads

    data = _file_bytes(file)
    file.file.seek(0)
    try:
        doc_reads.check(data)
    except doc_reads.QuotaError as e:
        raise QuotaFull(status, e) from e
    return data


def _charge_if_new(key: bytes, counts: dict) -> None:
    """Hesap dökümü: yeni hareket getirdiyse okuma hakkı (PDF her 10 sayfa 1; hepsi "zaten vardı" ise düşmez; aynı dosya
    bir kez)."""
    from app import doc_reads

    if any(v for k, v in counts.items() if k in ("gelir", "gider", "kart_odemesi", "virman")):
        doc_reads.charge(key)


def _read_bank_pdf(file: UploadFile, password: str | None = None) -> tuple[list[dict], dict]:
    import hashlib

    from app import bank_import, bank_pdf, doc_cache

    data = _file_bytes(file)
    key = (os.path.abspath(db.db_path()), hashlib.sha256(data).hexdigest())
    with _PDF_CACHE_LOCK:
        res = _PDF_CACHE.get(key)
    if res is None:  # ayrıştırma (yapay zekâ olabilir) kilit dışında: başka defterlerin okuması beklemesin
        try:  # hazır sürüm: yapay zekâ okuması defter başı 30 gün saklanır (app/doc_cache.py)
            with doc_cache.reading(data, doc_cache.DOKUM):
                res = bank_pdf.parse_account_pdf(data, ([password] if password else []) + db.pdf_passwords())
        except (bank_pdf.BankPdfError, receipt.ReceiptError) as e:
            raise HTTPException(422, errors.user_text(e, 422)) from e
        with _PDF_CACHE_LOCK:
            _PDF_CACHE[key] = res
            while len(_PDF_CACHE) > 8:
                _PDF_CACHE.pop(next(iter(_PDF_CACHE)))
    owners = [res.get("owner_name") or "", *_owners()]
    meta = {k: res[k] for k in ("bank", "owner_name", "verified", "chain_bad", "closing_ok")}
    meta["iban"] = res.get("iban")
    return bank_import.finalize(res["rows"], owners), meta


def _read_bank_file(file: UploadFile):
    from app import bank_import

    data = _file_bytes(file)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Dosya çok büyük")
    try:
        rows = bank_import.read_rows(data, file.filename or "dosya.csv")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, errors.user_text(e, 422, "Dosya okunamadı; Excel (.xlsx, .xls) ya da CSV dosyası olduğundan emin ol.")) from e
    if not rows:
        raise HTTPException(422, "Dosya boş")
    from app import card_excel

    if card_excel.detect(rows):  # kart işlem dökümü: tutarlar işaretsiz, burada harcamalar gelir sanılırdı
        raise HTTPException(422, "Bu bir kredi kartı işlem dökümü (hesap hareketi değil). Ekstreler sayfasına bırak; "
                                 "aylara bölünüp kart harcaması olarak eklenir.")
    return rows


def _account_match(account: str, iban: str | None) -> dict:
    """Önizleme: döküm hangi hesaba gidecek? ask ise arayüz "Bu döküm 'Garanti' hesabına mı ait?" diye sorar ve
    cevabı içe aktarmada account_id (Evet) ya da new_account=1 (Yeni hesap aç) olarak gönderir."""
    from app import sync

    return sync.resolve_account(account or None, iban)


def _balance_check(rows: list[dict], account: str, iban: str | None = None) -> dict:
    """İçe aktarmadan önce: bu döküm hesabın bakiyesini nasıl değiştirecek? Şüpheliyse uyarılar (onay istenir)."""
    warnings = []
    if len(rows) == 1:
        warnings.append("Belgede tek hareket var: bu bir dekont olabilir. Dekontsa Ekstreler'e bırak; bakiyeye dokunmadan "
                        "tek işlem olarak eklenir.")
    from app.bank_import import last_balance

    lb = last_balance(rows)  # yeniden eskiye sıralı dosyada da günün son hareketi
    am = _account_match(account, iban) if account else {}
    aid = am.get("account_id") or am.get("default_id")
    acc = next((a for a in db.list_accounts() if a["id"] == aid), None) if aid else None
    change = None
    if lb and acc and acc["as_of"]:
        d, new = lb
        cur = acc["current"] if acc["current"] is not None else acc["balance"]
        if d >= acc["as_of"]:  # daha eski döküm bakiyeyi değiştirmez
            change = {"account": acc["name"], "current": round(cur, 2), "new": round(new, 2), "date": d}
            if money.is_zero(new) and abs(cur) >= 1000:
                warnings.append(f"{acc['name']} bakiyesi {money.tl(cur)} → ₺0 olacak. Belge doğru mu?")
            elif abs(new - cur) > max(abs(cur) * 0.5, 20000):
                warnings.append(f"{acc['name']} bakiyesi {money.tl(cur)} → {money.tl(new)} olacak (büyük değişiklik). "
                                "Arada yüklenmemiş bir dönem ya da yanlış belge olabilir.")
    return {"balance_change": change, "checks": warnings}


def _preview_counts(parsed: list[dict], account: str, iban: str | None) -> tuple[dict, dict | None]:
    """Önizleme sayaçları: tür başına; "tekrar" (parmak izi, eski biçim dahil) ve "cakisma" (aynı hesapta aynı gün ve
    tutarla başka belgeden yüklü). overlap: {count, from, to, message} ya da None (hesap belli değilse de None)."""
    with db.connect() as c:
        seen = {r[0] for r in c.execute("SELECT import_hash FROM entries WHERE import_hash IS NOT NULL "
                                        "UNION SELECT import_hash FROM own_transfers WHERE import_hash IS NOT NULL "
                                        "UNION SELECT import_hash FROM card_payments WHERE import_hash IS NOT NULL")}
    am = _account_match(account, iban) if (account or iban) else {}
    ov = db.import_overlaps(parsed, am.get("account_id") or am.get("default_id"), account, seen, preview=True)
    counts: dict[str, int] = {}
    for i, r in enumerate(parsed):
        if r["type"] == "gider" and db.match_card_payment(abs(r["amount"]), r["date"]):
            r["type"] = "kart_odemesi"  # tutar bir ekstreyle birebir → kart ödemesi (önizlemede de görünsün)
        k = ("tekrar" if r["hash"] in seen or r.get("alt_hash") in seen else "cakisma" if i in ov["rows"] else r["type"])
        counts[k] = counts.get(k, 0) + 1
    return counts, ({k: ov[k] for k in ("count", "from", "to", "message")} if ov["count"] else None)


@app.post("/api/bank-import/preview")
@_heavy
def bank_import_preview(file: UploadFile = File(...), mapping: str | None = Form(None),
                         header_row: int | None = Form(None), password: str | None = Form(None)):
    """Sütunları tahmin et (ya da verilen eşlemeyi kullan), ilk satırları ve özetini döndür."""
    import json

    from app import bank_import

    doc = _doc_check(file)
    if (file.filename or "").lower().endswith(".pdf"):
        parsed, meta = _read_bank_pdf(file, password)
        counts, overlap = _preview_counts(parsed, meta.get("bank") or "", meta.get("iban"))
        for r in parsed:
            if r["type"] in ("gelir", "gider"):
                r["who"] = bank_import.counterparty(r["description"], r["amount"], [meta.get("owner_name")] if meta.get("owner_name") else _owners())
        _charge_if_new(doc, counts)
        from app import verification

        return {"pdf": True, **meta, **verification.account_check(meta), "header_row": 0, "columns": [], "mapping": {}, "profile_saved": False,
                "total": len(parsed), "counts": counts, "overlap": overlap, "sample": parsed[:200], "raw_preview": [],
                **_balance_check(parsed, meta.get("bank") or "", meta.get("iban")),
                "account_match": _account_match(meta.get("bank") or "", meta.get("iban"))}

    rows = _read_bank_file(file)
    det = bank_import.detect(rows)
    saved = db.get_setting(_bank_profile_key(det["columns"]))
    if mapping:
        det["mapping"] = json.loads(mapping)
    elif saved:
        det["mapping"] = json.loads(saved)
    if header_row is not None:
        det["header_row"], det["columns"] = header_row, rows[header_row]
    parsed = bank_import.parse(rows, det["mapping"], det["header_row"], _owners())
    from app import sync

    iban = sync.find_iban_rows(rows)
    counts, overlap = _preview_counts(parsed, "", iban)
    # güvenlik ağı: neredeyse hiç eksi tutar yok ve bakiye sütunu yoksa kart dökümü olabilir (harcamalar gelir görünür)
    warning = None
    amounts = [r["amount"] for r in parsed if r.get("amount") is not None]
    if len(amounts) >= 5 and sum(a > 0 for a in amounts) / len(amounts) >= 0.9 and not det["mapping"].get("balance"):
        warning = ("Bu dosyada tutarların neredeyse hepsi artı ve bakiye sütunu yok: bir kredi kartı dökümü olabilir. "
                   "Öyleyse içe aktarma — harcamalar gelir sayılır. Kart dökümünü Ekstreler sayfasına bırak; tanınmazsa "
                   "bize bildir.")
    _charge_if_new(doc, counts)
    return {**det, "profile_saved": bool(saved), "total": len(parsed), "counts": counts, "overlap": overlap,
            "sample": parsed[:15],
            "raw_preview": rows[det["header_row"]: det["header_row"] + 6], "warning": warning, "iban": iban,
            "account_match": _account_match("", iban)}


@app.post("/api/bank-import")
@_heavy
def bank_import_run(file: UploadFile = File(...), mapping: str = Form("{}"), header_row: int = Form(0),
                    password: str | None = Form(None), account_id: int | None = Form(None),
                    new_account: bool = Form(False)):
    """account_id: önizlemedeki "Bu döküm X hesabına mı ait?" sorusuna Evet; new_account: Yeni hesap aç."""
    import json

    from app import bank_import, sync

    account, iban, meta_v = "", None, None
    doc = _doc_check(file)
    if (file.filename or "").lower().endswith(".pdf"):
        parsed, meta = _read_bank_pdf(file, password)
        meta_v = meta
        account, iban = meta.get("bank") or "", meta.get("iban")
        if meta.get("owner_name"):
            db.remember_owner_name(meta["owner_name"])  # e-posta bildirimlerinde de "kendin" tanınsın
        # kişi ayrımında "kendin" = yalnızca döküm sahibi; ek kart sahipleri (eş, çocuk) Transferler'de kişi olarak görünsün
        owners = [meta.get("owner_name") or ""] if meta.get("owner_name") else _owners()
    else:
        owners = _owners()
        rows = _read_bank_file(file)
        m = json.loads(mapping)
        parsed = bank_import.parse(rows, m, header_row, _owners())
        iban = sync.find_iban_rows(rows)
        if parsed:
            db.set_setting(_bank_profile_key(rows[header_row]), json.dumps(m))  # bu banka biçimini hatırla
    if not parsed:
        raise HTTPException(422, "İçe aktarılacak satır bulunamadı; sütun eşlemesini kontrol et")
    from app import undo

    raw = _file_bytes(file)
    with undo.batch(f"Hesap dökümü: {file.filename or account or 'dosya'}") as b:
        if account_id and not any(a["id"] == account_id and a["kind"] == "banka" for a in db.list_accounts()):
            raise HTTPException(422, "Hesap bulunamadı")
        # açık seçim yoksa önizlemeyle aynı çözümleme (IBAN → hesap, banka adı → hesap): hesapsız eklenmesin
        acc_id = sync.account_for_import(account or None, iban, account_id, new_account)
        stats = db.import_bank_rows(parsed, account=account, owners=owners, account_id=acc_id)
        db.auto_link()
        # dökümün kendisi de saklanır (kayıtların detayından indirilir); geri alınınca bağ kopar, gece temizliği siler
        try:
            doc_id = db.save_bank_document(raw, file.filename or "", account or None, account_id=acc_id)
        except Exception:  # noqa: BLE001 - dosya saklanamasa da içe aktarma geçerli
            import logging

            logging.getLogger("ekstre.storage").exception("hesap dökümü dosyası saklanamadı")
            doc_id = None
        if doc_id:
            db.link_bank_document(doc_id, [r.get("hash") for r in parsed])
        if meta_v is not None:  # PDF: bakiye zinciri (S6); Excel/CSV: doğrulaması yok, alan da yok
            from app import verification

            stats.update(verification.account_check(meta_v))
            verification.set_document(doc_id, stats["verified"])
        b["summary"] = stats
    _charge_if_new(doc, stats)  # önizlemede sayıldıysa aynı dosya ikinci kez sayılmaz
    if warn := db.take_storage_warning():
        stats["storage_warning"] = warn
    return stats


@app.post("/api/bank-import/documents/{doc_id}/confirm")
def bank_document_confirm(doc_id: int):
    """Bakiye zinciri tutmayan dökümü kullanıcı belgeyle karşılaştırdı, doğru: verification 'onaylandi' (S6)."""
    from app import verification

    d = db.get_bank_document(doc_id)
    if not d or not _bank_doc_visible(d):
        raise HTTPException(404, errors.GENERIC[404])
    verification.confirm_document(doc_id)
    return {"ok": True, "verification": verification.document_state(db.get_bank_document(doc_id).get("verified"))}


@app.get("/api/bank-import/documents")
def bank_documents():
    return [d for d in db.list_bank_documents() if _bank_doc_visible(d)]


def _bank_doc_visible(d: dict) -> bool:
    """Ortak defterde (tam yetkili üye) gizli hesabın dökümü görünmez."""
    from app import book

    r = book.active()
    return not (r and d.get("account_id") in set(r["hidden_accounts"]))


@app.get("/api/bank-import/documents/{doc_id}/file")
def bank_document_file(doc_id: int):
    d = db.get_bank_document(doc_id)
    if not d or not d["stored"] or not _bank_doc_visible(d):
        raise HTTPException(404, "Dosya yok")
    base = os.path.splitext(d["filename"] or "")[0] or f"hesap-dokumu-{doc_id}"
    return _stored_file("statements-bank", d["file_name"], base)


# ---------------------------------------------------------------- ön ödemeli kartlar

def _check_wallet(data: dict) -> None:
    """Ödeme 'kart' ise geçerli bir kart seçilmiş olmalı; değilse kart bağlantısı kalkar.
    Hesap (banka/nakit): kredi kartı ya da yemek kartıyla ödenen giderde olmaz; seçildiyse var olmalı."""
    if data.get("payment") == "kart":
        if not data.get("wallet_id") or not db.get_wallet(data["wallet_id"]):
            raise HTTPException(422, "Hangi karttan ödendiğini seç")
        data["account_id"] = None
    elif "payment" in data:
        data["wallet_id"] = None
    if data.get("payment") == "kredi_karti":
        data["account_id"] = None
    elif "payment" in data:
        data["card_last4"] = None  # kart son 4 hanesi sadece kredi kartı ödemesinde anlamlı
    if data.get("account_id") and not any(a["id"] == data["account_id"] for a in db.list_accounts()):
        raise HTTPException(422, "Hesap bulunamadı")


class WalletIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    card_type: Literal["yemek", "hediye", "giyim", "diger"] = "yemek"
    balance: Money = 0
    expires_at: date | None = None
    monthly_amount: Money | None = Field(default=None, gt=0)
    load_day: int = Field(default=1, ge=1, le=31)


class WalletPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    card_type: Literal["yemek", "hediye", "giyim", "diger"] | None = None
    balance: Money | None = None
    expires_at: date | None = None


class WalletSchedule(BaseModel):
    amount: Money = Field(gt=0)
    day: int = Field(default=1, ge=1, le=31)
    from_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class WalletStop(BaseModel):
    mode: Literal["now", "after_this", "resume"]


class WalletLoad(BaseModel):
    amount: Money = Field(gt=0)
    on: date | None = None


@app.get("/api/wallets")
def wallets():
    return db.list_wallets()


@app.post("/api/wallets")
def create_wallet(body: WalletIn):
    try:
        wid = db.create_wallet(body.name, body.card_type, body.balance, body.expires_at.isoformat() if body.expires_at else None)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    if body.monthly_amount:
        db.set_wallet_schedule(wid, body.monthly_amount, body.load_day, finance.current_period(), body.name.strip())
    return {"id": wid}


@app.patch("/api/wallets/{wid}")
def patch_wallet(wid: int, body: WalletPatch):
    data = body.model_dump(exclude_unset=True)
    if data.get("expires_at"):
        data["expires_at"] = data["expires_at"].isoformat()
    if not db.update_wallet(wid, data):
        raise HTTPException(404, "Kart yok")
    return {"ok": True}


@app.delete("/api/wallets/{wid}")
def delete_wallet(wid: int):
    if not db.delete_wallet(wid):
        raise HTTPException(404, "Kart yok")
    return {"ok": True}


@app.put("/api/wallets/{wid}/schedule")
def wallet_schedule(wid: int, body: WalletSchedule):
    w = db.get_wallet(wid)
    if not w:
        raise HTTPException(404, "Kart yok")
    rid = db.set_wallet_schedule(wid, body.amount, body.day, body.from_month or finance.current_period(), w["name"])
    db.auto_load_wallets(date.today())
    return {"recurring_id": rid}


@app.post("/api/wallets/{wid}/schedule/stop")
def wallet_schedule_stop(wid: int, body: WalletStop):
    cur = finance.current_period()
    last = {"now": finance.add_months(cur, -1), "after_this": cur, "resume": None}[body.mode]
    if body.mode == "now":  # bu dönemin yüklemesi yapılmışsa o kalır; yapılmadıysa artık yapılmaz
        w = db.get_wallet(wid)
        if w and w["schedule"] and db.recurring_done(w["schedule"]["id"], cur):
            last = cur
    if not db.stop_wallet_schedule(wid, last):
        raise HTTPException(404, "Aylık yükleme yok")
    if body.mode == "resume":
        db.auto_load_wallets(date.today())
    return {"ok": True}


@app.post("/api/wallets/{wid}/load")
def wallet_load(wid: int, body: WalletLoad):
    """Tek seferlik yükleme (hediye kartına para yükleme, ikramiye vb.)."""
    w = db.get_wallet(wid)
    if not w:
        raise HTTPException(404, "Kart yok")
    return {"id": db.create_entry({"entry_date": (body.on or date.today()).isoformat(), "description": f"{w['name']} yüklemesi",
                                   "amount": body.amount, "kind": "gelir", "category": db.WALLET_INCOME_CATEGORY,
                                   "wallet_id": wid, "skipped": 0})}


# ---------------------------------------------------------------- hesaplar / net varlık

class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["banka", "nakit", "diger"] = "nakit"
    balance: Money
    as_of: date | None = None


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    kind: Literal["banka", "nakit", "diger"] | None = None
    balance: Money | None = None
    as_of: date | None = None


@app.get("/api/accounts")
def accounts():
    """Seçim listeleri için: banka/nakit hesapları (bakiyesi bilinmeyenler dahil) ve yemek/hediye kartları."""
    return {"accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"], "current": a["current"]} for a in db.list_accounts()],
            "wallets": [{"id": w["id"], "name": w["name"], "card_type": w["card_type"], "current": w["current"]}
                        for w in db.list_wallets()]}


class ToOpening(BaseModel):
    account_id: int


@app.post("/api/entries/{entry_id}/to-opening")
def entry_to_opening(entry_id: int, body: ToOpening):
    """Gelir olarak girilmiş başlangıç bakiyesini hesabın bakiyesine çevir."""
    _visible_or_404("account", body.account_id)
    try:
        return {"message": db.entry_to_opening_balance(entry_id, body.account_id)}
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


class PaymentAccount(BaseModel):
    account_id: int | None = None


@app.patch("/api/debt-payments/{pid}")
def set_debt_payment_account(pid: int, body: PaymentAccount):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    if not db.set_debt_payment_account(pid, body.account_id):
        raise HTTPException(404, "Ödeme yok")
    return {"ok": True}


@app.delete("/api/debt-payments/{pid}")
def delete_debt_payment(pid: int):
    """Tek ödemeyi geri al (DELETE /api/debts/{id}/pay hepsini siler). İdempotent: ödeme zaten yoksa da 200,
    deleted: false. Dönüş: {ok, deleted, debt_id}."""
    did = db.delete_debt_payment(pid)
    return {"ok": True, "deleted": did is not None, "debt_id": did}


@app.get("/api/integrity")
def integrity_summary():
    """Gece doğruluk denetiminin son koşusu: {last: {day, ran_at, ms, counts} | null, details: [{code, label, count,
    hint, route, items: [{ref, date, amount, title, route}]}], total, rerun (bu saat kalan yeniden kontrol hakkı)}."""
    from app import integrity

    return integrity.summary()


@app.post("/api/integrity/run")
def integrity_rerun():
    """'Yeniden kontrol et': hemen denetle (saatte 6). Dönüş GET ile aynı; sınırdaysa 429."""
    from app import book, integrity

    m = book.member()
    if m and book.role(m) == "viewer":
        raise HTTPException(403, errors.GENERIC[403])
    out = integrity.rerun()
    if out is None:
        raise HTTPException(429, "Bu saat içinde yeterince kontrol ettik; biraz sonra tekrar dene.")
    return out


@app.get("/api/onboarding")
def onboarding(v: int = 1):
    """Başlarken: hangi adımlar tamam (Özet'teki rehber kartı). v=2: mobil sırası (hesap → gelir → kart → sabit
    gider → posta → bütçe); v=1 (web) olduğu gibi."""
    from app import mailbox, telegram

    if v >= 2:
        return _onboarding_v2()

    with db.connect() as c:
        one = lambda q: bool(c.execute(q).fetchone())  # noqa: E731
        has_card = one("SELECT 1 FROM statements")
        has_account = one("SELECT 1 FROM entries WHERE import_hash IS NOT NULL")
        loaded = [x for x, ok in (("kart ekstresi", has_card), ("hesap dökümü", has_account)) if ok]
        steps = [
            {"key": "ekstre", "done": has_card or has_account, "route": "ekstreler",
             "title": "Ekstrelerini yükle",
             "text": ("Yüklendi: " + " ve ".join(loaded) + ". Diğer bankalarını da ekleyebilirsin.") if loaded else
                     "Kart ekstresi ve hesap dökümü PDF'lerini Ekstreler'e bırak; kartların, hesapların, bakiyen ve "
                     "maaşın kendiliğinden gelir.",
             "guide": [
                 "Bankanın mobil uygulaması ya da internet şubesi → Kartlar → Ekstreler → son ekstre → PDF indir/paylaş.",
                 "Vadesiz hesap için: Hesaplar → Hesap hareketleri → son 1–3 ay → PDF (dekont/hesap dökümü).",
                 "İndirdiğin PDF'leri Ekstreler sayfasındaki alana bırak; hangisinin kart, hangisinin hesap olduğunu "
                 "kendisi anlar. Şifreliyse (çoğu bankada TC kimlik no'nun ilk/son haneleri) bir kez sorar.",
                 "Telefonda: PDF'i açıp Paylaş → Dosyalara kaydet, sonra Ekstreler'de 'Dosya seç'.",
             ]},
            {"key": "hesap", "done": one("SELECT 1 FROM accounts WHERE as_of IS NOT NULL"), "route": "hesaplar",
             "title": "Hesap bakiyelerini kontrol et",
             "text": "Dökümden gelen hesapların bakiyesi hazır; nakdini ve yemek kartını sen ekle."},
            {"key": "maas", "done": one("SELECT 1 FROM recurring WHERE kind = 'gelir'"), "route": "plan",
             "action": "salary", "cta": "Maaşımı gir",
             "title": "Maaşını ve sabit giderlerini gir",
             "text": "Maaş, kira, aidat… Planlama gelecek ayları buna göre hesaplar.",
             "guide": [
                 "Net maaşını biliyorsan: tutarı yaz, 'Her ay tekrarlansın' açık kalsın, yattığı günü seç.",
                 "Brüt maaşını biliyorsan: 'Brüt maaş'ı işaretle, aylık brütü yaz. Net her ay vergi dilimine göre "
                 "hesaplanır (yıl ilerledikçe düşer, ocakta yeniden yükselir); altında ay ay dökümü görürsün.",
                 "Zam ya da iş değişikliği olunca: Planlama'da maaş kalemini aç → 'Zam / iş değişikliği'; o aydan "
                 "itibaren yeni brütle hesaplar, iş değişince vergi matrahı sıfırdan başlar.",
                 "Hesap dökümü yüklediysen yatan maaşlar zaten gelir olarak görünür; bu adım gelecek ayların planı için.",
                 "Kira, aidat, abonelik gibi her ay olan giderleri de aynı şekilde 'Her ay' olarak gir.",
             ]},
            {"key": "fis", "done": one("SELECT 1 FROM receipt_items") or one("SELECT 1 FROM entries WHERE note LIKE 'Fiş:%'"),
             "route": "ozet", "action": "add", "title": "Bir fiş okut",
             "text": "+ → Fiş çek: tutar, işyeri, ürünler kendiliğinden dolar; fiyat takibi başlar."},
        ]
        if config.is_admin():  # kullanıcılar için Telegram henüz yok
            steps.append({"key": "telegram", "done": bool(telegram.chat_id()), "route": "ayarlar",
                          "title": "Telegram'ı bağla (isteğe bağlı)",
                          "text": "Son ödeme hatırlatmaları ve ay sonu raporu telefonuna gelsin."})
        if not receipt.enabled():  # engel değil: fiş yine yerelde okunur, anahtarla daha iyi
            steps.append({"key": "gemini", "done": False, "route": "ayarlar", "cta": "Anahtarı gir",
                          "title": "Yapay zekâ anahtarı (isteğe bağlı, ücretsiz)",
                          "text": "Fişler şu an yerel okunuyor (toplam, tarih, işyeri). Anahtarla ürün satırları ve tanınmayan "
                                  "bankaların ekstreleri de okunur.",
                          "guide": [
                              "aistudio.google.com/apikey adresine Google hesabınla gir.",
                              "\"Create API key\" (API anahtarı oluştur) → çıkan anahtarı kopyala.",
                              "HesapKitap → Ayarlar → Anahtarlar → Gemini API anahtarı → yapıştır → Kaydet.",
                              "Ücretsiz katman kişisel kullanıma yeter; faturalandırmayı açarsan verilerin model eğitiminde kullanılmaz.",
                          ]})
        steps.append({"key": "mail", "done": mailbox.enabled(), "route": "ayarlar",
                      "title": "E-postayı bağla (isteğe bağlı)", "text": "Bankanın e-ekstreleri kendiliğinden içe aktarılsın."})
    return {"steps": steps, "done": sum(s_["done"] for s_ in steps), "total": len(steps),
            "dismissed": db.get_setting("onboarding_dismissed") == "1"}


def _onboarding_v2() -> dict:
    """Mobil Başlarken: kurulum sırası. Ortak defter üyesinde adım yok (kurulum defter sahibinin işi)."""
    from app import book, inbound

    dismissed = db.get_setting("onboarding_dismissed") == "1"
    if book.member():
        return {"steps": [], "done": 0, "total": 0, "dismissed": True, "member": True}
    u = db.current_user()
    with db.connect() as c:
        one = lambda q: bool(c.execute(q).fetchone())  # noqa: E731
        steps = [
            {"key": "hesap", "done": one("SELECT 1 FROM accounts WHERE kind <> 'kart' AND as_of IS NOT NULL"),
             "route": "ekstreler", "action": "dokum", "cta": "Hesap dökümü yükle",
             "alt": {"cta": "Elle ekle", "route": "hesaplar", "action": "account"},  # ikincil: hesabı elle aç
             "title": "Banka hesabını ekle",
             "text": "En kolayı: hesap dökümünü yükle; hesap, bakiye ve hareketler tek seferde gelir.",
             "guide": ["Bankanın uygulaması → Hesaplar → Hesap hareketleri → son 1–3 ay → PDF olarak paylaş.",
                       "Hesap dökümü yükle ile PDF'i seç; hesabın, bakiyen ve hareketlerin kendiliğinden gelir.",
                       "Elle girdiğin harcamalar dökümdekilerle eşleşir; iki kez sayılmaz."]},
            {"key": "maas", "done": one("SELECT 1 FROM recurring WHERE kind = 'gelir'"),
             "route": "plan", "action": "salary", "cta": "Maaşımı gir",
             "title": "Maaşını ve düzenli gelirlerini gir",
             "text": "Maaş, kira geliri, burs… Her ay gelen para planı ileriye taşır.",
             "guide": ["Net tutarı yaz; 'Her ay tekrarlansın' açık kalsın.",
                       "Yattığı günü seç; plan gelecek ayları buna göre kurar.",
                       "Brütünü biliyorsan 'Brüt'ü seç: net her ay vergi dilimine göre hesaplanır."]},
            {"key": "kart", "done": one("SELECT 1 FROM statements"),
             "route": "ekstreler", "action": "upload", "cta": "Ekstre yükle",
             "title": "Kredi kartlarını ekle",
             "text": "İlk ekstreni yükle; kartın, harcamaların ve son ödeme tarihin kendiliğinden gelir.",
             "guide": ["Bankanın uygulaması → Kartlar → Ekstreler → son ekstre → PDF olarak paylaş.",
                       "+ → Ekstre yükle ile PDF'i seç; hangi kart olduğunu kendisi anlar.",
                       "Şifreliyse (çoğu bankada TC kimlik no'nun bir kısmı) bir kez sorar."]},
            {"key": "sabit", "done": one("SELECT 1 FROM recurring WHERE kind = 'gider'"),
             "route": "plan", "action": "fixed", "cta": "Sabit gider ekle",
             "title": "Sabit giderlerini gir",
             "text": "Kira, aidat, abonelik, fatura… Her ay ödediklerin ay sonunu doğru gösterir.",
             "guide": ["Adını ve tutarını yaz, ödeme gününü seç.",
                       "Tutarı değişen faturalarda ortalamayı yaz; fatura gelince düzeltirsin.",
                       "Bitiş ayı olan kalemler (ör. kira sözleşmesi, kurs taksidi) o ay kendiliğinden biter."]},
        ]
        if u and plans.hosted() and plans.allows(u, "posta"):
            steps.append({"key": "posta", "done": one("SELECT 1 FROM mail_log"), "route": "ayarlar", "action": "posta",
                          "cta": "Adresimi göster", "title": "Posta adresini kur",
                          "text": "Bankanın e-ekstre ve fatura maillerini sana özel adrese ilet; kendiliğinden okunsun.",
                          "guide": ["Posta'da sana özel adresi kopyala.",
                                    "Bankanın e-ekstre ayarında ya da mail kutunda bu adrese yönlendirme kur.",
                                    "İlk mail gelince bu adım tamamlanır."],
                          "address": bool(inbound.address(u, create=False))})
        steps.append({"key": "butce", "done": one("SELECT 1 FROM budgets"), "route": "ozet", "action": "budget",
                      "cta": "Bütçe koy", "title": "İlk bütçeni koy",
                      "text": "Market, yemek gibi bir kategoriye aylık sınır koy; yaklaşınca haber verelim.",
                      "guide": ["Bir kategori seç (en çok harcadığın iyi bir başlangıç).",
                                "Aylık sınırı yaz; sınıra yaklaşınca Özet'te uyarırız.",
                                "Ay dönünce bütçe kendiliğinden sıfırlanır."]})
    return {"steps": steps, "done": sum(s_["done"] for s_ in steps), "total": len(steps), "dismissed": dismissed}


@app.post("/api/onboarding/dismiss")
def onboarding_dismiss():
    db.set_setting("onboarding_dismissed", "1")
    return {"ok": True}


# ---------------------------------------------------------------- hatırlatmalar (Yapılacaklar; ileride push)

REMINDER_ID = (r"^((ekstre|dokum|odeme):[^\s/]{1,80}:\d{4}-\d{2}(-\d{2})?|esles:\d{1,12}:[et]\d{1,12}|"
               r"(hesap|borc|sabit):\d{1,12}:\d{1,12}|kart:\d{1,12}|dogruluk:[0-9a-f]{10}|"
               r"(gelen|dokum_kontrol|ekstre_kontrol):\d{1,12})$")


class SnoozeIn(BaseModel):
    days: int = Field(default=1, ge=1, le=31)


@app.get("/api/reminders")
def reminders_list():
    """Yapılacaklar: [{id, kind, title, text, cta, route, action, due, severity, tag, period, ...}]; id döneme bağlı, kararlı."""
    from app import reminders

    return reminders.build()


def _reminder_id(rid: str) -> str:
    if not re.match(REMINDER_ID, rid):
        raise HTTPException(422, "Geçersiz hatırlatma")
    return rid


@app.post("/api/reminders/{rid}/snooze")
def reminders_snooze(rid: str, body: SnoozeIn | None = None):
    from app import reminders

    return reminders.snooze(_reminder_id(rid), (body or SnoozeIn()).days)


@app.post("/api/reminders/{rid}/dismiss")
def reminders_dismiss(rid: str):
    from app import reminders

    return reminders.dismiss(_reminder_id(rid))


# ---------------------------------------------------------------- telefona bildirim (app/push.py)

class PushDeviceIn(BaseModel):
    token: str = Field(min_length=16, max_length=4096)
    platform: Literal["ios", "android"]
    env: Literal["production", "sandbox"] | None = None
    app_version: str | None = Field(default=None, max_length=40)


class PushTokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


class PushQuietIn(BaseModel):
    model_config = {"populate_by_name": True}
    from_: str | None = Field(default=None, alias="from", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    to: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class PushPrefsIn(BaseModel):
    types: dict[str, bool] | None = None
    quiet: PushQuietIn | None = None
    digest: bool | None = None


def _push_uid() -> int:
    u = db.current_user()
    if not u or not u.get("id"):
        raise HTTPException(409, "Bildirim için giriş (kullanıcı hesabı) gerekli")
    return u["id"]


@app.post("/api/push/devices")
def push_device_add(body: PushDeviceIn):
    """Cihaz belgesi (FCM / APNs). Aynı belge başka kullanıcıdaysa bu hesaba taşınır (telefonda hesap değişti)."""
    from app import push

    if not push.valid_token(body.token):
        raise HTTPException(422, "Geçersiz cihaz belgesi")
    push.register(_push_uid(), body.token, body.platform, body.env, body.app_version)
    return {"ok": True}


@app.delete("/api/push/devices")
def push_device_delete(body: PushTokenIn):
    """Çıkışta: bu telefona bildirim gitmesin."""
    from app import push

    push.unregister(_push_uid(), body.token)
    return {"ok": True}


@app.get("/api/push/prefs")
def push_prefs_get():
    """{enabled (sunucuda push yapılandırılmış mı), types, labels, quiet {from, to}, digest, devices}."""
    from app import push

    return push.prefs_out(_push_uid())


@app.put("/api/push/prefs")
def push_prefs_put(body: PushPrefsIn):
    """Kısmi güncelleme: yalnız gelen alanlar."""
    from app import push

    uid = _push_uid()
    data = body.model_dump(exclude_unset=True, by_alias=True)
    try:
        push.set_prefs(uid, data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return push.prefs_out(uid)


class AnnounceTargetIn(BaseModel):
    platform: Literal["all", "ios", "android"] = "all"
    below_version: str | None = Field(default=None, max_length=20)
    test_only: bool = False


class AnnounceIn(AnnounceTargetIn):
    title: str = Field(max_length=500)
    body: str = Field(max_length=2000)
    route: str | None = Field(default=None, max_length=200)
    force: bool = False  # aynı metin 10 dk içinde gönderildiyse yine de gönder (istemci onayıyla)


def _announce_admin() -> dict:
    _require_admin()
    u = db.current_user()
    if not u or not u.get("id"):
        raise HTTPException(409, "Duyuru için yönetici hesabıyla giriş gerekli")
    return u


@app.post("/api/admin/announce/preview")
def admin_announce_preview(body: AnnounceTargetIn):
    """Duyurunun hedefi: {users, devices, by_platform {ios, android}, opted_out (duyuruyu kapatan), enabled}."""
    from app import announce

    u = _announce_admin()
    v = (body.below_version or "").strip() or None
    if v and not announce.VERSION_RE.match(v):
        raise HTTPException(422, "Sürüm 1.0.5 biçiminde olmalı")
    return announce.preview(body.platform, v, body.test_only, u["id"])


@app.post("/api/admin/announce")
def admin_announce(body: AnnounceIn):
    """Duyuru gönder (arka planda). Tercihinde "duyuru" kapalı olana gitmez, sessiz saatte kuyruğa girer, kişi başı bir
    kez. test_only: yalnız kendi cihazlarına, hemen. Aynı metin 10 dk içinde ikinci kez: 409 (force ile geçer)."""
    from app import announce

    u = _announce_admin()
    try:
        a = announce.create(u, body.model_dump(), test_only=body.test_only, force=body.force)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except announce.DuplicateError as e:
        return JSONResponse({"detail": "Bu duyuru 10 dakika içinde zaten gönderildi", "code": "duplicate",
                             "existing": announce.to_json(e.existing)}, status_code=409)
    return announce.to_json(a)


@app.get("/api/admin/announcements")
def admin_announcements():
    """Geçmiş duyurular (yeniden eskiye, en çok 50)."""
    from app import announce

    _announce_admin()
    return {"items": [announce.to_json(a) for a in announce.history()], "limits": {"title": announce.TITLE_MAX,
                                                                                   "body": announce.BODY_MAX}}


@app.post("/api/push/test")
def push_test():
    """Kullanıcının cihazlarına "Bildirimler açık" (dakikada bir)."""
    from app import push

    uid = _push_uid()
    if not push.enabled():
        raise HTTPException(409, "Bildirimler bu sunucuda kapalı")
    if not push.devices(uid):
        raise HTTPException(409, "Kayıtlı telefon yok; uygulamada bildirim iznini aç")
    if not push.test_allowed(uid):
        raise HTTPException(429, "Bir dakika sonra yeniden dene", headers={"Retry-After": str(push.TEST_EVERY)})
    return {"ok": True, **push.send_test(uid)}


JarUnit = Literal["TRY", "ALTIN_24", "ALTIN_22", "CEYREK", "YARIM", "TAM", "CUMHURIYET", "ATA", "GUMUS", "USD", "EUR", "GBP"]


class JarBuy(BaseModel):
    quantity: float = Field(gt=0)
    cost: Money = Field(gt=0)          # ödenen TL
    buy_date: date | None = None
    payment: Literal["havale", "nakit", "kredi_karti"] = "havale"
    account_id: int | None = None
    note: str | None = None


class JarIn(BaseModel):
    unit: JarUnit = "TRY"
    name: str = Field(min_length=1, max_length=60)
    goal: float | None = Field(None, gt=0)
    deadline: date | None = None
    account_id: int | None = None
    monthly: float | None = Field(None, gt=0)
    auto_day: int | None = Field(None, ge=1, le=28)
    note: str | None = None
    initial: float = Field(0, ge=0)


class JarPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    goal: float | None = Field(None, gt=0)
    deadline: date | None = None
    account_id: int | None = None
    monthly: float | None = Field(None, gt=0)
    auto_day: int | None = Field(None, ge=1, le=28)
    note: str | None = None
    closed: bool | None = None
    unlink_debt: bool | None = None  # "Başka hedefe çevir": borçla bağı kaldır (yetenek debt_jar)


class JarMoveIn(BaseModel):
    amount: float = Field(gt=0)
    direction: Literal["yatir", "cek"] = "yatir"
    move_date: date | None = None
    note: str | None = None


def _jar_debt(j: dict, debts: dict[int, dict] | None) -> dict | None:
    """Borca bağlı kumbara (yetenek debt_jar): "Borç: <ad> için" etiketi ve borcun durumu. Borç kapandı / silindiyse
    nötr öneri (kumbarayı kapat ya da başka hedefe çevir); kumbara kapalıysa öneri yok. debts None: Borç / Alacak
    sayfası kapalı (yalnız etiket)."""
    title = j.get("debt_title")
    if not j.get("debt_id") and not title:
        return None
    out = {"id": j.get("debt_id"), "title": title, "label": f"Borç: {title} için", "route": "borc-plani",
           "state": None, "remaining": None, "suggestion": None}
    if debts is None:
        return out
    d = debts.get(j["debt_id"]) if j.get("debt_id") else None
    if d is None:
        out["state"] = "silindi"
        text = "Borç silindi: kumbarayı kapat ya da başka bir hedefe çevir."
    elif d["settled_at"] or d["written_off_at"]:
        out["state"] = "kapandi"
        text = "Borç kapandı: kumbarayı kapat ya da başka bir hedefe çevir."
    else:
        out["state"] = "acik"
        out["remaining"] = round(d["remaining"], 2)
        text = None
    if text and not j.get("closed_at"):
        out["suggestion"] = {"text": text, "close": "Kumbarayı kapat", "convert": "Başka hedefe çevir"}
    return out


def _jar_view(j: dict, today: date, debts: dict[int, dict] | None = None, plans: dict[int, dict] | None = None) -> dict:
    """Hedef ilerlemesi, son tarihe göre ayda gereken ve bu hızla ne zaman ulaşılacağı.
    Hız: otomatik aylık ayırma varsa o; yoksa son 3 ayda ayrılan (başlangıç ayırması hariç) aylık ortalaması."""
    from datetime import timedelta

    out = {**j}
    out["debt"] = _jar_debt(j, debts)
    unit = j.get("unit") or "TRY"
    if unit != "TRY":  # altın/döviz: miktar, bugünkü değer, ödenen, kâr/zarar, bu ayki alım
        from app import zakat

        label, _, sym = zakat.KINDS[unit]
        try:
            px = zakat.prices()["prices"]
            out["value"] = zakat._value_at(unit, j["balance"], px)
            out["unit_price"] = zakat._value_at(unit, 1, px)
        except Exception:  # noqa: BLE001 - fiyat servisine ulaşılamazsa değer boş kalır
            out["value"] = out["unit_price"] = None
        cost = round(sum(m["cost"] or 0 for m in j["moves"]), 2)
        out.update(unit_label=label, unit_symbol=sym, cost=cost,
                   gain=round(out["value"] - cost, 2) if out["value"] is not None and cost else None)
        month = today.strftime("%Y-%m")
        out["bought_this_month"] = round(sum(m["amount"] for m in j["moves"] if m["move_date"][:7] == month and m["cost"]), 3)
        out["buy_due"] = bool(j["monthly"] and j["auto_day"] and not j["closed_at"] and today.day >= j["auto_day"]
                              and out["bought_this_month"] < j["monthly"] - 1e-9)
    since = (today - timedelta(days=90)).isoformat()
    recent = sum(m["amount"] for m in j["moves"] if m["move_date"] >= since
                 and m["note"] not in ("Başlangıç", "Hedeften aktarıldı"))
    out["pace"] = j["monthly"] or (round(recent / 3, 2) if recent > 0 else 0.0)
    if j["goal"]:
        out["pct"] = min(100.0, round(j["balance"] / j["goal"] * 100, 1))
        left = max(0.0, j["goal"] - j["balance"])
        out["left"] = round(left, 2)
        if left > 0 and out["pace"] > 0:
            n = -(-left // out["pace"])
            out["eta"] = finance.add_months(today.strftime("%Y-%m"), int(n))
        if j["deadline"] and left > 0:
            dl = date.fromisoformat(j["deadline"])
            months = max(1, (dl.year - today.year) * 12 + dl.month - today.month)
            out["need_per_month"] = round(left / months, 2)
            out["on_track"] = bool(out.get("eta") and out["eta"] <= j["deadline"][:7])
    # hedef durumu (rozet + "ayda ₺… ayır"): gerçek ayırma hızına göre yolunda / geride / önde / tarihsiz
    out["goal_status"] = jar_goal.jar_goal_status(j, today, out.get("unit_price"),
                                                  include_current=bool(j.get("debt_id")))
    if (out["debt"] or {}).get("suggestion"):
        out["goal_status"] = None  # borç kapandı / silindi: "ayda ₺… ayır" yerine "kumbarayı kapat ya da çevir" önerisi
    plan = (plans or {}).get(j.get("debt_id")) if out["debt"] and out["debt"]["state"] == "acik" and not j.get("closed_at") else None
    if out["debt"]:
        out["debt"]["plan"] = plan
    if plan:  # borca bağlı kumbarada tek kaynak borç planı: kumbaranın kendi aylık hesabı çelişmesin
        out["goal_status"] = None
        for k in ("need_per_month", "eta", "on_track"):
            out.pop(k, None)
    return out


def _jar_debts(jars: list[dict]) -> dict[int, dict] | None:
    """Borca bağlı kumbaraların borçları (Borç / Alacak sayfası üyeye kapalıysa None)."""
    from app import book

    if not any(j.get("debt_id") or j.get("debt_title") for j in jars):
        return {}
    r = book.active()
    if r and "borclar" in r["hidden_pages"]:
        return None
    return {d["id"]: d for d in db.list_debts()}


@app.get("/api/jars")
def list_jars():
    today = date.today()
    jars = db.list_jars(today)
    debts = _jar_debts(jars)
    return [_jar_view(j, today, debts, _jar_plans(jars, debts)) for j in jars]


def _jar_plans(jars: list[dict], debts: dict[int, dict] | None) -> dict[int, dict]:
    """Açık borca bağlı açık kumbara varsa borç planından durumları (plan bir kez hesaplanır)."""
    from app import debt_plan as dp

    if not debts or not any(j.get("debt_id") in debts and not j.get("closed_at") for j in jars):
        return {}
    return dp.jar_plans()


@app.post("/api/jars")
def create_jar(body: JarIn):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    data = body.model_dump(exclude={"initial"})
    data["name"] = data["name"].strip()
    if data["deadline"]:
        data["deadline"] = data["deadline"].isoformat()
    if bool(data["monthly"]) != bool(data["auto_day"]):
        data["monthly"] = data["auto_day"] = None
    return {"id": db.create_jar(data, body.initial)}


@app.patch("/api/jars/{jid}")
def patch_jar(jid: int, body: JarPatch):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    data = body.model_dump(exclude_unset=True)
    if "deadline" in data and data["deadline"]:
        data["deadline"] = data["deadline"].isoformat()
    if data.pop("unlink_debt", None):
        if not db.unlink_jar_debt(jid):
            raise HTTPException(404, "Kumbara yok")
        if not data:
            return {"ok": True}
    if "closed" in data:
        reopen = not data["closed"]
        data["closed_at"] = date.today().isoformat() if data.pop("closed") else None
        if reopen and db.reopen_conflict(jid):
            raise HTTPException(409, "Bu borcun açık bir kumbarası daha var; bunu açmadan önce ötekini kapat")
    if not db.update_jar(jid, data):
        raise HTTPException(404, "Kumbara yok")
    return {"ok": True}


@app.delete("/api/jars/{jid}")
def delete_jar(jid: int):
    if not db.delete_jar(jid):
        raise HTTPException(404, "Kumbara yok")
    return {"ok": True}


@app.post("/api/jars/{jid}/moves")
def add_jar_move(jid: int, body: JarMoveIn):
    amt = body.amount if body.direction == "yatir" else -body.amount
    try:
        return {"id": db.add_jar_move(jid, amt, (body.move_date or date.today()).isoformat(), body.note)}
    except LookupError as e:
        raise HTTPException(404, errors.user_text(e, 404)) from e
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


class DebtJarIn(BaseModel):
    """Borca bağlı kumbara (yetenek debt_jar). Boş alanlar plandan: ad "<Kişi> borcu" / "<Banka> kredisini kapat",
    hedef borcun kalanı (kredide güncel tahmini kapatma tutarı), son tarih plandaki ödeme ayı, aylık plandaki birikim."""
    name: str | None = Field(None, min_length=1, max_length=60)
    goal: Money | None = Field(None, gt=0)
    deadline: date | None = None
    monthly: Money | None = Field(None, gt=0)
    account_id: int | None = None


@app.post("/api/debts/{did}/jar")
def create_debt_jar(did: int, body: DebtJarIn):
    """Borç planındaki birikim için kumbara aç (Premium: 402 kumbara; ortak defterde sahip / tam yetkili). 409: bu
    borcun açık kumbarası var; 422: bu borç için birikim yok (tek seferde ödenecek borç ya da erken kapatma planı olan
    kredi değil). Dönüş: {id, jar}."""
    from app import debt_plan as dp

    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    try:
        offer = dp.jar_offer_for(did)
    except db.DebtJarExists as e:
        raise HTTPException(409, str(e)) from None
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    data = body.model_dump(exclude_unset=True)
    deadline = data["deadline"].isoformat() if data.get("deadline") else (None if "deadline" in data else offer["deadline"])
    try:
        # aylık tutar saklanmaz (verilmedikçe): bağlı kumbarada ay ay tutarlar borç planından gelir
        jid = db.create_debt_jar(did, data.get("name") or offer["name"], data.get("goal") or offer["goal"], deadline,
                                 data.get("monthly"), data.get("account_id"))
    except db.DebtJarExists as e:
        raise HTTPException(409, str(e)) from None
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    today = date.today()
    jars = db.list_jars(today)
    j = next(x for x in jars if x["id"] == jid)
    message, plan = dp.jar_open_message(did, today)
    return {"id": jid, "message": message, "jar": _jar_view(j, today, _jar_debts([j]), {did: plan} if plan else {})}


class DebtFromJarIn(BaseModel):
    amount: Money | None = Field(None, gt=0)  # boş: kumbaradaki kadar (en çok kalan borç); kredide zorunlu
    paid_at: date
    account_id: int | None = None             # verilmezse kumbaranın hesabı


@app.post("/api/jars/{jid}/pay-debt")
def pay_debt_from_jar(jid: int, body: DebtFromJarIn):
    """"Kumbaradan öde" (yetenek debt_jar): borç ödemesi + kumbaradan çekim; kredide erken kapatma (Kapattım). Artan
    kalırsa kumbara açık kalır ve ask döner (kumbarayı kapat / başka hedefe çevir); azsa kısmi ödeme. Geri al: DELETE
    /api/debt-payments/{payment_id} (kumbara hareketi de geri gelir, kumbara yeniden açılır)."""
    _visible_or_404("account", body.account_id)
    try:
        out = db.pay_debt_from_jar(jid, body.amount, body.paid_at.isoformat(), body.account_id,
                                   account_given="account_id" in body.model_fields_set)
    except LookupError as e:
        raise HTTPException(404, errors.user_text(e, 404)) from e
    except ValueError as e:  # OverpayError dahil
        raise HTTPException(422, errors.user_text(e, 422)) from e
    ask = None
    if out["close_early"]:
        msg = "Kredi kumbaradan kapandı"
    elif not out["debt_closed"]:
        msg = f"{money.tl(out['paid'], trim=True)} ödendi; borçta {money.tl(out['debt_left'], trim=True)} kaldı. Kumbara açık, kalanı için ayırmaya devam edebilirsin."
    else:
        msg = "Borç kumbaradan ödendi" + ("; kumbara kapandı" if out["jar_closed"] else "")
    if out["leftover"] > 0 and out["debt_closed"]:
        ask = {"key": "leftover", "amount": out["leftover"],
               "text": f"Borç kapandı; kumbarada {money.tl(out['leftover'], trim=True)} kaldı. Kumbarayı kapatırsan para serbest kalır; "
                       "istersen başka bir hedefe çevir.",
               "close": "Kumbarayı kapat", "convert": "Başka hedefe çevir"}
    return {"ok": True, **out, "message": msg, "ask": ask}


@app.post("/api/jars/{jid}/buy")
def buy_into_jar(jid: int, body: JarBuy):
    """Altın/döviz kumbarası: aldım → varlık + gider kaydı + kumbara hareketi."""
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    try:
        return {"id": db.buy_into_jar(jid, body.quantity, body.cost, (body.buy_date or date.today()).isoformat(),
                                      body.account_id, body.payment, body.note)}
    except LookupError as e:
        raise HTTPException(404, errors.user_text(e, 404)) from e
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


class JarMovePatch(BaseModel):
    quantity: float | None = Field(None, gt=0)
    cost: Money | None = Field(None, gt=0)
    move_date: date | None = None


@app.patch("/api/jar-moves/{mid}")
def patch_jar_purchase(mid: int, body: JarMovePatch):
    """Altın/döviz kumbarası alımını düzelt (kuyumcuda ödenen farklıysa)."""
    if not db.update_jar_purchase(mid, body.quantity, body.cost, body.move_date.isoformat() if body.move_date else None):
        raise HTTPException(404, "Alım bulunamadı")
    return {"ok": True}


@app.delete("/api/jar-moves/{mid}")
def delete_jar_move(mid: int):
    if not db.delete_jar_move(mid):
        raise HTTPException(404, "Hareket yok")
    return {"ok": True}


# ---------------------------------------------------------------- sadaka ve bağışlar

class DonationIn(BaseModel):
    kind: str
    amount: float = Field(gt=0)
    given_date: date | None = None
    recipient: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=200)
    as_expense: bool = False   # eski istemci: nakitten verdim → gider olarak da yaz (payment = nakit)
    account_id: int | None = None
    # nasıl verdin: gider olarak da yazılır. kredi_karti → hemen "ekstre bekleniyor" gider, ekstre gelince eşleşir;
    # banka | nakit | havale | kredi_karti | altin_bozdurma | altin (donations.PAYMENTS)
    payment: DonationPayment | None = None
    card_last4: str | None = Field(None, pattern=r"^\d{4}$")


class DonationMarkIn(BaseModel):
    source: Literal["entry", "card"]
    id: int
    kind: str                  # sadaka, fitre, … ya da "zekat"
    person_id: int | None = None
    year_start: date | None = None
    recipient: str | None = Field(None, max_length=80)


@app.get("/api/donations")
def donations_summary():
    return donations.summary()


@app.post("/api/donations")
def donations_add(body: DonationIn):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    try:
        return {"id": donations.add(body.kind, body.amount, (body.given_date or date.today()).isoformat(), body.recipient,
                                    body.note, body.account_id, body.as_expense, body.payment, body.card_last4)}
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


class SadakaReminderIn(BaseModel):
    rhythm: Literal["haftalik", "aylik", "kapali"] | None = None  # Her Cuma / Ayda bir (ayın ilk Cuması) / Hatırlatma
    day: int | None = Field(None, ge=0, le=6)  # hatırlatma günü (0 Pazartesi … 4 Cuma … 6 Pazar); arayüz yalnız Cuma
    show_streak: bool | None = None            # "Sürekliliği göster" (varsayılan açık)


@app.get("/api/donations/reminder")
def sadaka_reminder_get():
    """Sadaka hatırlatması (kişiye özel): ritim, süreklilik, son sadaka, bu dönem kayıtlı mı, Özet kartının metni."""
    from app import sadaka_reminder

    return sadaka_reminder.status()


@app.put("/api/donations/reminder")
def sadaka_reminder_put(body: SadakaReminderIn):
    """Kişinin kendi seçimi (yalnız gelen alanlar). Ortak defterde salt görüntüleyen üye de seçer; Zekât ve Sadaka sayfası
    üyeye kapalıysa middleware keser, özellik kapalıysa 409."""
    from app import sadaka_reminder

    if not sadaka_reminder.available():
        raise HTTPException(409, "Zekât ve Sadaka kapalı")
    try:
        sadaka_reminder.set_pref(body.rhythm, body.day, body.show_streak)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    return sadaka_reminder.status()


@app.get("/api/donations/ratio")
def sadaka_ratio(months: int = Query(12, ge=1, le=24)):
    """Sadakanın gelire oranı (kişinin; faiz geliri hariç), son 6 ya da 12 dönem + nötr özet cümlesi."""
    from app import sadaka_reminder

    return sadaka_reminder.ratio(months)


@app.delete("/api/donations/{did}")
def donations_delete(did: int):
    if not donations.delete(did):
        raise HTTPException(404, "Kayıt yok")
    return {"ok": True}


@app.post("/api/donations/mark")
def donations_mark(body: DonationMarkIn):
    # gövdedeki kayıt: üyeye gizli kayıt işaretlenirse açıklaması ve tutarı bağış listesine kopyalanıyordu
    _visible_or_404("entry" if body.source == "entry" else "txn", body.id)
    try:
        return donations.mark(body.source, body.id, body.kind, body.person_id,
                              body.year_start.isoformat() if body.year_start else None, body.recipient)
    except LookupError as e:
        raise HTTPException(404, errors.user_text(e, 404)) from e
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.get("/api/donations/mark/{source}/{sid}")
def donations_mark_of(source: Literal["entry", "card"], sid: int):
    return {"mark": donations.mark_of(source, sid)}


@app.delete("/api/donations/mark/{source}/{sid}")
def donations_unmark(source: Literal["entry", "card"], sid: int):
    donations.unmark(source, sid)
    return {"ok": True}


# ---------------------------------------------------------------- zekât

class ZakatPersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class ZakatPersonPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    hawl_start: date | None = None
    note: str | None = None
    is_minor: bool | None = None
    check_date: date | None = None  # ek kontrol günü (boş göndermek için null)


class ZakatAssetIn(BaseModel):
    person_id: int
    kind: str
    quantity: float = Field(gt=0)
    is_jewelry: bool = False
    note: str | None = None
    acquired: date | None = None
    cost: Money | None = Field(None, gt=0)
    estimated: bool = False


class ZakatAssetPatch(BaseModel):
    person_id: int | None = None
    kind: str | None = None
    quantity: float | None = Field(None, gt=0)
    is_jewelry: bool | None = None
    note: str | None = None
    acquired: date | None = None
    cost: Money | None = Field(None, gt=0)
    estimated: bool | None = None


class ZakatPaymentIn(BaseModel):
    amount: Money = Field(gt=0)
    pay_date: date | None = None
    note: str | None = None
    year_start: date | None = None  # geçmiş bir yılın ödemesi
    # nasıl ödendi (ops.): verilirse gider olarak da yazılır (Bağış & Sadaka). kredi_karti → hemen "ekstre bekleniyor"
    # gider, ekstre gelince kart satırıyla eşleşir (tek sayım). Yoksa (eski istemci) yalnız zekât ödemesi.
    # banka | nakit | havale | kredi_karti | altin_bozdurma | altin
    payment: DonationPayment | None = None
    account_id: int | None = None   # banka/havale/nakit: hangi hesaptan (ops.)
    card_last4: str | None = Field(None, pattern=r"^\d{4}$")  # kredi kartı: hangisi (ops.)
    recipient: str | None = Field(None, max_length=80)  # havale: kime (dökümdeki havaleyle eşleşir)
    asset_id: int | None = None     # altınla ödendi: düşülecek altın varlığı (ops.)
    asset_qty: float | None = Field(None, gt=0)  # düşülecek miktar (gram / adet)


class ZakatExpenseIn(BaseModel):
    """Gider olarak görünmeyen zekât ödemesine gider aç: nasıl ödendi."""
    payment: DonationPayment
    account_id: int | None = None
    card_last4: str | None = Field(None, pattern=r"^\d{4}$")
    recipient: str | None = Field(None, max_length=80)
    pay_date: date | None = None  # verilmezse ödemenin tarihi


class ZakatSettingsIn(BaseModel):
    madhhab: Literal["hanefi", "safii"] | None = None
    include_app: bool | None = None
    include_receivables: bool | None = None
    manual_gram: float | None = Field(None, ge=0)  # 0: elle fiyatı kaldır
    fitre_amount: float | None = Field(None, ge=0)  # kişi başı fitre (Diyanet her yıl açıklar)
    fitre_people: int | None = Field(None, ge=0, le=30)  # 0: aile üyesi sayısı kadar


@app.get("/api/holdings")
def holdings():
    return zakat.holdings()


@app.get("/api/holdings/estimate")
def holding_estimate(kind: str, cost: float, day: date):
    """Miktarı bilinmeyen altın: ödenen tutar ve o günkü fiyattan ihtiyatlı (fazla) tahmin."""
    try:
        return zakat.estimate_quantity(kind, cost, day)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    except Exception as e:  # noqa: BLE001 - fiyat servisine ulaşılamadı
        raise HTTPException(502, errors.user_text(e, 502, "Geçmiş fiyat alınamadı; biraz sonra tekrar dene.")) from e


@app.get("/api/holdings/candidates")
def holding_candidates(min_amount: float = 1000):
    """Altını ne zaman aldığını hatırlamayanlar için: büyük nakit çekimler (ATM, gişe/"Nakit Ödeme"), giden havale/EFT ve kuyumcu
    harcamaları (hesap dökümü + kart). Açıklamalar boşluksuz ve Türkçe karaktersiz karşılaştırılır ("NakitÖdeme")."""
    from app.categorize import fold_key

    cash = ("ATM", "PARACEK", "NAKITCEK", "NAKITODEME", "GISE")
    gold = ("KUYUMCU", "ALTIN", "GOLD", "JEWEL", "MUCEVHER", "DARPHANE")
    sent = ("HAVALE", "EFT", "FAST", "GIDEN", "GONDER", "TRANSFER", "VIRMAN")
    with db.connect() as c:
        rows = db.rows(c.execute(
            "SELECT entry_date AS date, description, amount, category, 'hesap' AS src FROM entries "
            "WHERE kind = 'gider' AND skipped = 0 AND amount >= ? "
            "UNION ALL SELECT txn_date, description, amount, category, 'kart' FROM transactions "
            "WHERE txn_type = 'harcama' AND amount >= ? ORDER BY date DESC", (min_amount, min_amount)))
    out = []
    for r in rows:
        k = fold_key(r["description"])
        if any(g in k for g in gold):
            out.append({**r, "kind": "kuyumcu"})
        elif r["category"] == "Nakit Çekim" or any(x in k for x in cash):
            out.append({**r, "kind": "nakit"})
        elif r["src"] == "hesap" and any(x in k for x in sent):  # kuyumcuya havale/EFT ile ödeme
            out.append({**r, "kind": "gonderim"})
    return out[:500]


@app.get("/api/zakat")
def zakat_summary():
    return zakat.compute()


@app.put("/api/zakat/settings")
def zakat_settings(body: ZakatSettingsIn):
    d = body.model_dump(exclude_unset=True)
    if "madhhab" in d:
        db.set_setting("zakat:madhhab", d["madhhab"])
    for k in ("include_app", "include_receivables"):
        if k in d:
            db.set_setting(f"zakat:{k}", "1" if d[k] else "0")
    if "manual_gram" in d:
        db.set_setting("zakat:manual_gram", str(d["manual_gram"]) if d["manual_gram"] else None)
    for k in ("fitre_amount", "fitre_people"):
        if k in d:
            db.set_setting(f"zakat:{k}", str(d[k]) if d[k] else None)
    zakat.refresh_self_due()  # mezhep / dahil edilenler ödenmemiş zekâtı değiştirir (Net durum)
    return {"ok": True}


@app.delete("/api/zakat/data")
def zakat_wipe():
    """Zekât ve sadaka verilerinin hepsini sil (kişiler, varlıklar, ödemeler, bağış kayıtları, zekât ayarları). Hesap
    dökümündeki hareketler yerinde kalır; sadece zekât/sadaka işaretleri kalkar. Özelliği kapatırken "verileri de sil"."""
    return zakat.wipe()


@app.get("/api/zakat/holy")
def zakat_holy():
    return zakat.holy()


@app.post("/api/zakat/prices/refresh")
def zakat_prices_refresh():
    return zakat.fetch_prices(force=True)


@app.post("/api/zakat/people")
def zakat_add_person(body: ZakatPersonIn):
    import sqlite3

    try:
        return {"id": zakat.add_person(body.name)}
    except sqlite3.IntegrityError as e:
        raise HTTPException(409, "Bu isimde biri zaten var") from e


@app.patch("/api/zakat/people/{pid}")
def zakat_patch_person(pid: int, body: ZakatPersonPatch):
    d = body.model_dump(exclude_unset=True)
    if "hawl_start" in d and d["hawl_start"]:
        d["hawl_start"] = d["hawl_start"].isoformat()
    if "is_minor" in d:
        d["is_minor"] = int(bool(d["is_minor"]))
    if d.get("check_date"):
        d["check_date"] = d["check_date"].isoformat()
    if not zakat.update_person(pid, d):
        raise HTTPException(404, "Kişi yok")
    return {"ok": True}


@app.delete("/api/zakat/people/{pid}")
def zakat_delete_person(pid: int):
    if not zakat.delete_person(pid):
        raise HTTPException(404, "Kişi yok ya da silinemez")
    return {"ok": True}


@app.get("/api/zakat/people/{pid}/history")
def zakat_history(pid: int):
    try:
        return zakat.history(pid)
    except LookupError as e:
        raise HTTPException(404, errors.user_text(e, 404)) from e


@app.post("/api/zakat/people/{pid}/next-year")
def zakat_next_year(pid: int):
    try:
        return {"hawl_start": zakat.next_year(pid)}
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.post("/api/zakat/people/{pid}/payments")
def zakat_add_payment(pid: int, body: ZakatPaymentIn):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba gider yazamaz
    try:
        return {"id": zakat.add_payment(pid, body.amount, (body.pay_date or date.today()).isoformat(), body.note,
                                        body.year_start.isoformat() if body.year_start else None,
                                        body.payment, body.account_id, body.card_last4,
                                        body.recipient, body.asset_id, body.asset_qty)}
    except LookupError as e:
        raise HTTPException(404, errors.user_text(e, 404)) from e
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


class ZakatPaymentPatch(BaseModel):
    amount: Money | None = Field(None, gt=0)
    pay_date: date | None = None
    note: str | None = None
    year_start: date | None = None
    # ödeme şeklini değiştir (yetenek donation_payment_gold): açtığı gider kaydı yeni şekille yeniden yazılır
    payment: DonationPayment | None = None
    account_id: int | None = None
    card_last4: str | None = Field(None, pattern=r"^\d{4}$")
    recipient: str | None = Field(None, max_length=80)
    asset_id: int | None = None
    asset_qty: float | None = Field(None, gt=0)


@app.patch("/api/zakat/payments/{pay_id}")
def zakat_update_payment(pay_id: int, body: ZakatPaymentPatch):
    """Ödemeyi tek işlemde düzenle; yalnız gönderilen alanlar değişir. Dönüş: güncel ödeme."""
    _visible_or_404("account", body.account_id)  # üye gizli hesaba gider yazamaz
    data = body.model_dump(exclude_unset=True)
    for k in ("pay_date", "year_start"):
        if data.get(k):
            data[k] = data[k].isoformat()
    try:
        r = zakat.update_payment(pay_id, data)
    except LookupError as e:  # düşülecek altın varlığı yok
        raise HTTPException(404, errors.user_text(e, 404)) from e
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    if r is None:
        raise HTTPException(404, "Ödeme yok")
    return r


@app.post("/api/zakat/payments/{pay_id}/expense")
def zakat_payment_expense(pay_id: int, body: ZakatExpenseIn):
    """Zekât ekranındaki "Ödediğin ₺X zekât gider olarak görünmüyor · Gider olarak ekle" (GET /api/zakat unexpensed)."""
    _visible_or_404("account", body.account_id)
    try:
        return zakat.add_expense(pay_id, body.payment, body.account_id, body.card_last4, body.recipient,
                                 body.pay_date.isoformat() if body.pay_date else None)
    except LookupError as e:
        raise HTTPException(404, errors.user_text(e, 404)) from e
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.post("/api/zakat/payments/{pay_id}/no-expense")
def zakat_payment_no_expense(pay_id: int):
    """"Gerek yok": bu ödeme için gider bir daha sorulmaz."""
    if not zakat.dismiss_expense(pay_id):
        raise HTTPException(404, "Ödeme yok")
    return {"ok": True}


@app.delete("/api/zakat/payments/{pay_id}")
def zakat_delete_payment(pay_id: int):
    if not zakat.delete_payment(pay_id):
        raise HTTPException(404, "Ödeme yok")
    return {"ok": True}


@app.post("/api/zakat/assets")
def zakat_add_asset(body: ZakatAssetIn):
    try:
        return {"id": zakat.add_asset(body.person_id, body.kind, body.quantity, body.is_jewelry, body.note,
                                      body.acquired.isoformat() if body.acquired else None, body.cost,
                                      body.estimated)}
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.patch("/api/zakat/assets/{aid}")
def zakat_patch_asset(aid: int, body: ZakatAssetPatch):
    d = body.model_dump(exclude_unset=True)
    for k in ("is_jewelry", "estimated"):
        if k in d and d[k] is not None:
            d[k] = int(d[k])
    if d.get("acquired"):
        d["acquired"] = d["acquired"].isoformat()
    if not zakat.update_asset(aid, d):
        raise HTTPException(404, "Varlık yok")
    return {"ok": True}


@app.delete("/api/zakat/assets/{aid}")
def zakat_delete_asset(aid: int):
    if not zakat.delete_asset(aid):
        raise HTTPException(404, "Varlık yok")
    return {"ok": True}


@app.get("/api/interest-history")
def interest_history():
    return finance.interest_history()


@app.get("/api/interest")
def interest_report(months: int = Query(12, ge=0, le=120)):
    """Faiz ve ücretler sayfası: son N dönem (0: tüm geçmiş). Ortak defterde faiz gizliyse hidden=True ve boş."""
    return finance.interest_report(months)


@app.get("/api/debt-plan")
def debt_plan(monthly: float | None = Query(None, ge=0, le=100_000_000),
              order: Literal["tarih", "kucuk", "faiz", "soz"] | None = None):
    """Borç kapatma planı (yetenek debt_plan, app/debt_plan.py): açık kart ve elden borçlar, ay ay takvim, bu ayın
    ödenecekleri, faiz uyarıları. monthly/order verilirse önizleme (kayıtlı ayar değişmez)."""
    from app import debt_plan as dp

    return dp.build(monthly=monthly, order=order)


class DebtPlanIn(BaseModel):
    monthly: Money | None = Field(default=None, ge=0, le=100_000_000)  # null: kayıtlı tutarı kaldır (öneri kullanılır)
    order: Literal["tarih", "kucuk", "faiz", "soz"] | None = None  # faiz / soz: eski ayar, "tarih" olarak kaydedilir


@app.put("/api/debt-plan")
def save_debt_plan(body: DebtPlanIn):
    """Planın aylık tutarı ve sıralaması (defter ayarı; ortak defterde ekleyebilen üye de değiştirir)."""
    from app import debt_plan as dp

    return dp.save_settings(body.model_dump(exclude_unset=True))


@app.post("/api/debt-plan/commit")
def commit_debt_plan(body: DebtPlanIn):
    """Planı başlat / "Bu planla devam" / "Planı yeniden oluştur": bugünkü borçlarla ay ay hedefler kaydedilir (eski
    plan geçmişte kalır). Sonraki aylarda plan yanıtındaki commitment bu hedeflere göre ilerlemeyi söyler."""
    from app import debt_plan as dp

    try:
        return dp.commit_plan(**body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/api/net-worth/at")
def net_worth_at(month: str):
    """Seçilen geçmiş dönemin son günündeki net durum (yaklaşık)."""
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(422, "Ay YYYY-AA olmalı")
    end = finance.period_bounds(month)[1]
    return finance.net_worth_at(min(end, date.today()))


# ---------------------------------------------------------------- banka şablonları (topluluk)

_tpl_hits: dict[str, list[float]] = {}


@app.post("/api/templates/submit")
def template_submit(body: dict, request: Request):
    """Başka sunuculardan gelen şablon (sadece hub). Oturumsuz; IP başına saatte 30 gönderim."""
    import time as _t

    from app import templates

    if not templates.is_hub():
        raise HTTPException(404, errors.GENERIC[404])
    ip = client_ip(request)
    now = _t.time()
    hits = [x for x in _tpl_hits.get(ip, []) if now - x < 3600]
    if len(hits) >= 30:
        raise HTTPException(429, "Çok fazla gönderim")
    _tpl_hits[ip] = [*hits, now]
    if not templates.submit_local(body.get("spec") or {}):
        raise HTTPException(422, "Geçersiz şablon")
    return {"ok": True}


class SharingIn(BaseModel):
    on: bool


@app.get("/api/templates")
def templates_overview():
    from app import templates

    return {"templates": templates.all_templates(), "sharing": templates.sharing_enabled(), "is_hub": templates.is_hub(),
            "submissions": templates.submissions() if templates.is_hub() and config.is_admin() else []}


@app.put("/api/templates/sharing")
def templates_sharing(body: SharingIn):
    from app import templates

    if not config.is_admin():
        raise HTTPException(403, "Sadece yönetici")
    templates.set_sharing(body.on)
    return {"ok": True}


@app.post("/api/templates/submissions/{sid}/{status}")
def templates_review(sid: int, status: Literal["onaylandi", "reddedildi", "bekliyor"]):
    from app import templates

    if not config.is_admin():
        raise HTTPException(403, "Sadece yönetici")
    if not templates.review(sid, status):
        raise HTTPException(404, errors.GENERIC[404])
    return {"ok": True}


@app.post("/api/templates/sync")
def templates_sync():
    from app import templates

    return {"added": templates.sync_community(force=True)}


@app.get("/api/version")
def version_info(refresh: bool = False):
    """Bu kurulumun sürümü ve GitHub'daki son sürüm (yönetici: güncelleme bildirimi)."""
    from app import version

    if not config.is_admin():
        return {"current": version.current()}
    return version.check(force=refresh)


@app.get("/api/net-worth")
def net_worth():
    return finance.net_worth()


@app.post("/api/accounts")
def create_account(body: AccountIn):
    db.set_account_balance(body.name.strip(), body.balance, (body.as_of or date.today()).isoformat(), "elle", body.kind,
                           origin="elle")
    return {"ok": True}


class AccountMergeIn(BaseModel):
    into: int  # birleşip kalacak hesap


@app.post("/api/accounts/{aid}/merge")
def merge_account(aid: int, body: AccountMergeIn):
    """Başka hesapla birleştir: bu hesabın işlemleri, sabit kalemleri, borç hareketleri, dökümleri ve bakiye çapası
    'into' hesabına taşınır, bu hesap silinir. Geri alınabilir (undo_id → POST /api/imports/{id}/undo)."""
    from app import book, sync, undo

    if book.member():  # tam yetkili üye de birleştirebilir ama gizli hesabı göremez/birleştiremez
        visible = {a["id"] for a in db.list_accounts()}
        if aid not in visible or body.into not in visible:
            raise HTTPException(404, "Hesap yok")
    names = {a["id"]: a["name"] for a in db.list_accounts()}
    try:
        with undo.batch(f"Hesap birleştirme: {names.get(aid, aid)} → {names.get(body.into, body.into)}") as b:
            res = sync.merge_accounts(aid, body.into)
            b["summary"] = res
    except sync.SyncError as e:
        raise HTTPException(409, errors.user_text(e, 409)) from e
    return {**res, "undo_id": b["id"]}


class AccountSeparateIn(BaseModel):
    other: int


@app.post("/api/accounts/{aid}/separate")
def separate_account(aid: int, body: AccountSeparateIn):
    """"Bu döküm X hesabına mı ait?" → Hayır, yeni (ayrı) hesap: bir daha sorulmaz."""
    from app import sync

    visible = {a["id"] for a in db.list_accounts()}
    if aid not in visible or body.other not in visible:
        raise HTTPException(404, "Hesap yok")
    sync.keep_separate(aid, body.other)
    return {"ok": True}


@app.patch("/api/accounts/{aid}")
def patch_account(aid: int, body: AccountPatch):
    data = body.model_dump(exclude_unset=True)
    if "as_of" in data or "balance" in data:
        data["as_of"] = (data.get("as_of") or date.today()).isoformat() if not isinstance(data.get("as_of"), str) else data["as_of"]
    if not db.update_account(aid, data):
        raise HTTPException(404, "Hesap yok")
    return {"ok": True}


@app.delete("/api/accounts/{aid}")
def delete_account(aid: int):
    if not db.delete_account(aid):
        raise HTTPException(404, "Hesap yok")
    return {"ok": True}


# ---------------------------------------------------------------- bölme (alt kalemler) ve kalem kuralları

class SplitPart(BaseModel):
    amount: Money = Field(gt=0)
    category: str = ""
    description: str | None = None
    person: str | None = None  # ortak harcama: bu kısım kişinin payı → alacak


class SplitsIn(BaseModel):
    parts: list[SplitPart]


class ItemRuleIn(BaseModel):
    keywords: str = Field(min_length=1)
    category: str = Field(min_length=1)


@app.get("/api/splits/{target}/{pid}")
def get_splits(target: Literal["entry", "card"], pid: int):
    from app import book

    if book.member() and not (book.can_see_entry(pid) if target == "entry" else book.can_see_txn(pid)):
        return []
    return db.splits_map().get((target, pid), [])


@app.put("/api/splits/{target}/{pid}")
def put_splits(target: Literal["entry", "card"], pid: int, body: SplitsIn):
    try:
        return db.set_splits(target, pid, [p.model_dump() for p in body.parts])
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


class SplitProposalIn(BaseModel):
    items: list[dict]
    category: str | None = None
    total: Money | None = None


@app.post("/api/splits/propose")
def propose_split(body: SplitProposalIn):
    """Fiş okununca formda gösterilecek bölme önerisi; kaydetmez."""
    return db.propose_split([i for i in body.items if isinstance(i, dict)], body.category, body.total)


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    amount: Money = Field(gt=0)
    deadline: date
    already: Money = Field(default=0, ge=0)


@app.get("/api/targets")
def targets():
    return finance.targets()


@app.post("/api/targets")
def create_target(body: TargetIn):
    return {"id": db.create_target(body.name, body.amount, body.deadline.isoformat(), finance.current_period(), body.already)}


@app.delete("/api/targets/{tid}")
def delete_target(tid: int):
    if not db.delete_target(tid):
        raise HTTPException(404, "Hedef yok")
    return {"ok": True}


@app.get("/api/compare")
def compare(month: str | None = None, all: bool = False):
    """Bu dönem, aynı güne kadar: geçen dönem ve son 3 dönem ortalamasıyla; hafta günleri ve haftalar."""
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(422, "Ay YYYY-MM olmalı")
    return finance.compare(month, variable_only=not all)


@app.get("/api/day/{d}")
def day_detail(d: date, all: bool = True):
    """O günün harcamaları (Özet → karşılaştırma → güne dokun)."""
    return finance.day_detail(d.isoformat(), variable_only=not all)


@app.get("/api/prices")
def prices(since: date | None = None, store: str | None = None, brand: str | None = None):
    """Fişlerden fiyat takibi: ürün / mağaza / marka kırılımı; tarihten beri, mağaza, marka filtresi.
    products[].history[] ve stores[].products[].history[]: vs_market {median, min, max, pct, verdict "ucuz"|"normal"|
    "pahali", cheapest_chain, day, source "resmi"|"community"} — o günün (±3 gün) piyasa fiyatına göre (app/market_ref.py)."""
    from app import market_ref, price_advice

    out = db.products(since.isoformat() if since else None, store or None, brand or None)
    market_ref.attach(out["products"] + [p for s in out["stores"] for p in s["products"]])
    price_advice.attach(out["products"])  # market, market_ref, advice (price_advice yeteneği)
    for p in out["products"] + [p for s in out["stores"] for p in s["products"]]:
        p.pop("_entries", None)
    return out


@app.get("/api/prices/suggest")
def prices_suggest(q: str = Query("", max_length=80), limit: int = Query(8, ge=1, le=20)):
    """Fiş kalemi / elle gider kalem adı önerisi (price_suggest): {items: [{name, group_key, group_label, last_price,
    last_store, count}], did_you_mean: {name, group_label} | null} — kendi fişlerindeki adlar (neredeyse aynı
    yazılışlar birleşik), sonra genel ürün adları (app/price_advice.py suggest)."""
    from app import price_advice

    return price_advice.suggest(q, limit)


@app.get("/api/prices/market")
def prices_market(group: str = Query(min_length=1, max_length=80), days: int = Query(90, ge=1, le=400)):
    """Ürün grubunun gün gün piyasa bandı (kişinin ili; app/market_ref.py market): {group, region, days: [{day, min,
    median, max, cheapest_chain, chains, source}], attribution}. Veri yoksa days boş."""
    from app import market_ref

    return market_ref.market(group, days)


@app.get("/api/prices/groups")
def price_groups(q: str | None = None, limit: int = 30):
    """Nereden alınır: ürün grupları (markadan bağımsız; "süt" → Süt · 1 L, Süt · 500 ml …) ve zincir başına en ucuz
    teklifler. [{key, label, best: [{chain, price, brand?, source "own"|"community", date? (kendi fişin), n? (kaç kişi)}]}]
    best ucuzdan pahalıya, en çok 5 (app/price_sources.py merge). Kendi verin: bu defterin fişleri (ortak defter üyesinde
    görebildiğin kayıtlar). Sıra: kendi aldıkların (son alım yeni→eski), sonra yalnız toplulukta olanlar (ada göre);
    metinde boy varsa ("1l süt", "süt 1 lt") o boydaki gruplar, sonra adı tam aranan ürün olanlar önce; boylar farklıysa
    aynı öbekte litre / kilo / adet fiyatı (unit_price_per) ucuz olan önce. Grup: category (product_groups.CATEGORIES),
    subcategory (alt kategori, app/product_taxonomy.py; bilinmiyorsa null);
    teklif: unit_price_per, per_unit ("L" | "kg" | "adet"; boysuz grupta yok). source "resmi" (sunucu açtıysa):
    attribution, region (kişinin ili ya da yedek il; BİM/A101/ŞOK "ulusal"), discount?, promotion?, change_7d?,
    change_30d? (yüzde) — il meta.ui.region'dan (seçilmemişse İstanbul).
    shop_brand_auto: aramada marka yazılıysa ("çaykur turist çay 1 kg") ve adı başka sonuç vermiyorsa ürünün grubu o
    markanın (ve serinin) teklifleriyle: satırda brand, brand_line, brand_label, any_brand (marka fark etmez en ucuz)."""
    from app import price_sources

    q = (q or "").strip()[:60]
    limit = max(1, min(limit, 50))
    srcs = price_sources.open_sources()
    labels: dict[str, str] = {}
    for s in srcs:  # kaynak sırası: önce kendi verin (adı da oradan)
        for k, v in s.search(q).items():
            labels.setdefault(k, v)
    if q in labels:  # istemci grup anahtarını gönderdi ("ekmek", "sut|1l"): yalnız o grup
        labels = {q: labels[q]}
    if q and "|" not in q and not labels:  # "çaykur turist çay 1 kg": adda marka (shop_brand_auto)
        from app import shop_stores, shopping

        got = shopping.brand_search(q, limit, shop_stores.current())
        if got is not None:
            return got
    own = next((s for s in srcs if isinstance(s, price_sources.OwnSource)), None)
    last = own.last_dates() if own else {}
    mine = sorted((k for k in labels if k in last), key=lambda k: (last[k], k), reverse=True)
    keys = mine + sorted((k for k in labels if k not in last), key=lambda k: labels[k])
    if q and "|" not in q:  # "1l süt" / "süt 1 lt": o boy önce; sonra adı tam aranan ürün ("süt" → Süt, Çikolatalı Süt değil)
        from app import product_groups

        word, size = product_groups.query_parts(q)
        keys.sort(key=lambda k: (not (size and k.endswith(f"|{size}")), k.split("|")[0] != word))  # sıralama kararlı
    keys = keys[:limit * 2]
    from app import shop_stores

    hidden = shop_stores.current()  # kişinin kapattığı mağazalar (shop_store_filter): hiç görünmez, sayısı hidden_count
    best = price_sources.best_offers(keys, srcs, hidden=hidden)
    best = {k: [{**o, "chain": price_sources.chain_name(o["chain"])} for o in v] for k, v in best.items()}  # yalnız zincir
    keys = [k for k in keys if best.get(k)]
    if q and "|" not in q and len({k.split("|", 1)[-1] for k in keys}) > 1:
        # boylar farklı (500 ml / 1 L): aynı sıra öbeğinde (boy, ad) litre / kilo fiyatı ucuz olan önce
        from app import product_groups

        word, size = product_groups.query_parts(q)
        per = lambda k: min((o.get("unit_price_per") or 1e12) for o in best[k])  # noqa: E731
        keys.sort(key=lambda k: (not (size and k.endswith(f"|{size}")), k.split("|")[0] != word, per(k)))
    from app import product_groups

    paths = product_groups.paths(keys[:limit])
    return [{"key": k, "label": labels[k], "category": (paths.get(k) or (None, None))[0],
             "subcategory": (paths.get(k) or (None, None))[1], "best": best[k],
             "hidden_count": len(hidden.by_key.get(k, ()))} for k in keys[:limit]]


@app.get("/api/prices/brands")
def price_brands(group: str = Query(min_length=1, max_length=60)):
    """Marka seç (shop_brand_size): ürünün (boysuz "sut" ya da boylu "sut|1l" — boylu verilse de bütün boylar) seçilebilir
    markaları: {group (boysuz), brands [{brand, sizes [grup anahtarı], chains}]} — kendi fişlerin + Market Fiyatı (son 7
    gün); topluluk ortancası markasızdır. Marka yoksa brands boş ("Marka fark etmez")."""
    from app import price_sources, shopping

    base = group.split("|", 1)[0].strip()
    return {"group": base, "brands": price_sources.brands(shopping.brand_keys(base))}


@app.get("/api/prices/combos")
def price_combos(group: str = Query(min_length=1, max_length=60), chain: str | None = Query(None, max_length=60)):
    """Zincir × marka × boy (shop_combo_picker): group boyluysa ("sut|1l") o boy, boysuzsa ("sut") en yaygın boylar.
    {keys, rows [{chain, brand | null (markasız: topluluk ortancası / markası okunmamış fişin), key, size_label, price,
    unit_price_per?, per_unit?, source own|community|resmi, date?, n?, region?, discount?, product?}] ucuzdan (en çok
    200), chains, brands, sizes [{key, size_label}], hidden {count, chains, local}, attribution}. chain verilirse yalnız
    o zincir ("BİM'de süt"). Kapatılan mağazalar yok."""
    from app import shop_stores, shopping

    return shopping.combos_out(shopping.combo_keys(group.strip()), chain, shop_stores.current())


@app.get("/api/prices/offers")
def price_offers(group: str = Query(min_length=1, max_length=60), brand: str | None = Query(None, max_length=40)):
    """Bu boy (ve marka) hangi mağazada kaça (shop_brand_size): {group, label, size_label, brand | null, offers [{chain,
    price, unit_price_per?, per_unit?, brand?, source, date?, n?, region?, discount?}] ucuzdan (bütün zincirler, en çok
    20; kişinin kapattığı mağazalar yok), hidden {count, chains, local}, attribution [kaynak yazısı]}. brand boşsa
    marka fark etmez (zincir başına gruptaki en ucuz ürün)."""
    from app import price_sources, product_groups, shop_stores

    key = group.strip()
    hidden = shop_stores.current()
    offs = price_sources.best_offers([key], limit=20, brand=brand or None, hidden=hidden).get(key, [])
    keep = ("chain", "price", "unit_price_per", "per_unit", "brand", "source", "date", "n", "region", "discount",
            "official", "differs")
    out = [{**{k: o[k] for k in keep if k in o}, "chain": price_sources.chain_name(o["chain"])} for o in offs]
    lab = product_groups.labels([key]).get(key) or key
    return {"group": key, "label": lab, "size_label": product_groups.size_label(key), "brand": brand or None,
            "offers": out, "hidden": hidden.out([key]),
            "attribution": sorted({o["attribution"] for o in offs if o.get("attribution")})}


# ---------------------------------------------------------------- alışveriş listesi (app/shopping.py)

class ShoppingIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    qty: float = Field(default=1, gt=0, le=99_999)  # birime göre sınır app/shopping.py UNIT_MAX (adet ≤ 999)
    unit: str | None = Field(default=None, max_length=8)  # adet | kg | g | L | ml (shopping_units); boşsa metinden
    group_key: str | None = Field(default=None, max_length=60)  # ürün detayından: o grubun boyu (shopping_assumed_size)


class ShoppingPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=200)
    qty: float | None = Field(default=None, gt=0, le=99_999)
    unit: str | None = Field(default=None, max_length=8)
    done: bool | None = None
    group_key: str | None = Field(default=None, max_length=60)  # boy seçimi ("sut|500ml"); null: seçimi kaldır
    store: str | None = Field(default=None, max_length=60)  # "Bu ürünü Migros'tan alacağım" (shop_store_pin); null kaldırır
    brand: str | None = Field(default=None, max_length=40)  # marka ("Sütaş"; shop_brand_size); null: marka fark etmez


class ShoppingBulkIn(BaseModel):
    items: list[ShoppingIn] = Field(min_length=1, max_length=50)


class ShoppingPlanIn(BaseModel):
    k: int | None = Field(default=None, ge=1, le=6)  # durak sayısı; boşsa önerilen (bağlı mağazalarla 3'ü geçebilir)
    chains: list[str] | None = Field(default=None, max_length=3)  # "Marketleri ben seçeyim" planı (shop_store_pin)


class ShoppingCompleteIn(BaseModel):
    carry_over: bool = True  # alınmayanlar listede kalsın (yeni listeye taşı)


class ShoppingLinkIn(BaseModel):
    entry_id: int
    target: str | None = Field(default=None, pattern="^(list|trip)$")
    trip_id: int | None = None


class TripReuseIn(BaseModel):
    only_bought: bool = False


@app.get("/api/shopping-list")
def shopping_list():
    """{items: [{id, text, qty, unit (adet|kg|g|L|ml), amount_label ("2,5 kg"), mult (teklif çarpanı | null), unit_mismatch,
    done, group {key, label, pending?, pinned?} | null, assumed {key, label, size_label} | null (boysuz satır en yaygın
    boya çözüldü; adette), best {chain, price, line_total (price × mult | null), unit_price_per?, per_unit?, source, n?}
    | null (en ucuz teklif; alınmışlarda null; kişinin ürününe bağlı satırda source "own" + product + date: "Senin son
    alımın: ₺95 · Örnekbüfe"), own_product {name, price, place, date} | null (yazılan ad kişinin fişteki ürününe
    bağlandı), own_place {place, price} | null (kişinin bu ürünü son kafede aldığı yer ve fiyat; market eşleşmesi
    değişmez), planned {chain, unit_price, line_total} | null (kaydedilen plan),
    actual {unit_price, line_total, entry_id} | null (bağlanan fiş), added_by, added_by_name, created_at, done_at}],
    saved_plan {k, total, chains [{chain, total, item_ids}], items, created_at, date, source_info} | null} — önce
    işaretlenmemişler."""
    from app import shop_stores, shopping, shopping_trips

    hidden = shop_stores.current()
    items = shopping.list_items(with_best=True, hidden=hidden)
    return {"items": items, "saved_plan": shopping_trips.attach_planned(items), "hidden_stores": hidden.out()}


@app.get("/api/shopping-list/stores")
def shopping_stores():
    """Mağazalar (shop_store_filter): {hidden {chains, local}, chains [{chain, hidden}] (fiyat verisinde ve fişlerinde
    görülen zincirler), local {hidden, names, count} (kendi fişlerindeki yerel marketler)}. Değiştirmek: PUT /api/me/ui
    {shop_hidden {chains?, local?}} (kişi başı; ortak defterde herkesin kendi süzgeci)."""
    from app import shop_stores

    return shop_stores.stores()


@app.post("/api/shopping-list")
def shopping_add(body: ShoppingIn, request: Request):
    """{text, qty?, unit?, group_key?}: miktar ve birim verilmezse metinden ("2,5 kg elma", "6x süt"). Idempotency-Key
    satıra kalıcı yazılır: çevrimdışı kuyruğun günler sonraki tekrarı da aynı satırı döner (_client_key)."""
    from app import shopping

    try:
        return shopping.add(body.text, body.qty, (db.current_user() or {}).get("id"), body.group_key, body.unit,
                            client_key=_client_key(request))
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.post("/api/shopping-list/bulk")
def shopping_bulk(body: ShoppingBulkIn):
    """Birden çok satır (Listeyi doldur, sık aldıkların): {added, skipped, items}."""
    from app import shopping_autofill

    return shopping_autofill.bulk_add([x.model_dump() for x in body.items], (db.current_user() or {}).get("id"))


@app.get("/api/shopping-list/suggest")
def shopping_suggest(q: str = Query("", max_length=60), limit: int = Query(8, ge=1, le=12)):
    """Yazarken öneri (shopping_autofill): {items [{text, group_key | null, label, source recent|own|product|group,
    count?, last_price?, place?}]} — product: kendi aldığın ürünün adı ("Örneksan Slims Mavi Sigara · son ₺95")."""
    from app import shopping_autofill

    return shopping_autofill.suggest(q, limit)


@app.get("/api/shopping-list/frequent")
def shopping_frequent(limit: int = Query(8, ge=1, le=12)):
    """Sık aldıkların (son 90 gün, listede olmayanlar): {items [{text, group_key, label, count, last_date}], days}."""
    from app import shopping_autofill

    return shopping_autofill.frequent(limit)


@app.get("/api/shopping-list/due")
def shopping_due():
    """Listeyi doldur: düzenli alınıp zamanı gelenler {items [{text, group_key, label, qty, unit, last_date,
    interval_days, days_since, reason}]}."""
    from app import shopping_autofill

    return shopping_autofill.due()


@app.put("/api/shopping-list/saved-plan")
def shopping_plan_save(body: ShoppingPlanIn):
    """"Bu planla git": Nereye gideyim? planını kaydet (k durak; boşsa önerilen) → saved_plan."""
    from app import shopping_trips

    try:
        return shopping_trips.save_plan(body.k, (db.current_user() or {}).get("id"), body.chains)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.delete("/api/shopping-list/saved-plan")
def shopping_plan_clear():
    """"Planı değiştir": kaydedilen planı kaldır."""
    from app import shopping_trips

    return {"deleted": shopping_trips.clear_plan()}


@app.post("/api/shopping-list/complete")
def shopping_complete(body: ShoppingCompleteIn):
    """"Alışverişi tamamla": alınanlar geçmiş alışverişe (shopping_trips) yazılır ve listeden silinir; alınmayanlar
    carry_over ise listede kalır. → trip (+ carried). Alınan yoksa 422."""
    from app import shopping_trips

    try:
        return shopping_trips.complete(body.carry_over, (db.current_user() or {}).get("id"))
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.get("/api/shopping-list/receipt-match")
def shopping_receipt_match(entry_id: int | None = None):
    """Fiş bu listeye / son alışverişe ait mi? entry_id verilirse o fiş, yoksa son 2 günün bağlanmamış (ve "hayır"
    denmemiş) fişleri: {entry_id, target list|trip, trip_id, chain, day, count, in_plan, confident, names} | null."""
    from app import shopping_trips

    return shopping_trips.receipt_match(entry_id) if entry_id else shopping_trips.pending_match()


@app.post("/api/shopping-list/link-receipt")
def shopping_link_receipt(body: ShoppingLinkIn):
    """"Evet, bu listeye ait": eşleşen satırlar işaretlenir, gerçek fiyatlar yazılır → {linked, target, trip_id, count,
    chain, names, saving, actual_total, market_total}. Eşleşme yoksa 404."""
    from app import shopping_trips

    got = shopping_trips.link_receipt(body.entry_id, body.target, body.trip_id)
    if not got:
        raise HTTPException(404, "Bu fiş listedeki ürünlerle eşleşmedi")
    return got


@app.post("/api/shopping-list/receipt-dismiss")
def shopping_receipt_dismiss(body: ShoppingLinkIn):
    """"Hayır": bu fiş bir daha sorulmaz."""
    from app import shopping_trips

    shopping_trips.dismiss(body.entry_id)
    return {"ok": True}


@app.get("/api/shopping-trips")
def shopping_trips_list(limit: int = Query(50, ge=1, le=200)):
    """Geçmiş alışverişler (yeniden eskiye): {trips [{id, day, completed_at, chains, planned_total, actual_total,
    market_total, saving, planned_matched, actual_matched, item_count, bought_count, linked, plan_saved, entry_ids,
    created_by, created_by_name}]}."""
    from app import shopping_trips

    return {"trips": shopping_trips.trips(limit)}


@app.get("/api/shopping-trips/savings")
def shopping_trips_savings():
    """Tasarruf (piyasa ortancasına göre; fişle bağlanan alışverişler): {month, year, month_trips, year_trips, basis}."""
    from app import shopping_trips

    return shopping_trips.savings()


@app.get("/api/shopping-trips/{tid}")
def shopping_trip_get(tid: int):
    """Alışveriş ayrıntısı: özet + items [{text, qty, unit, amount_label, group_key, label, done, planned_chain,
    planned_unit, planned_line, actual_unit, actual_line, entry_id, receipt_key, receipt_qty, market_unit,
    market_line}], plan."""
    from app import shopping_trips

    got = shopping_trips.get_trip(tid)
    if not got:
        raise HTTPException(404, "Alışveriş yok")
    return got


@app.post("/api/shopping-trips/{tid}/reuse")
def shopping_trip_reuse(tid: int, body: TripReuseIn):
    """"Tekrar kullan": satırları listeye ekle (listede olan atlanır) → {added, skipped, items}."""
    from app import shopping_trips

    got = shopping_trips.reuse(tid, body.only_bought, (db.current_user() or {}).get("id"))
    if got is None:
        raise HTTPException(404, "Alışveriş yok")
    return got


@app.delete("/api/shopping-trips/{tid}")
def shopping_trip_delete(tid: int):
    from app import shopping_trips

    if not shopping_trips.delete_trip(tid):
        raise HTTPException(404, "Alışveriş yok")
    return {"deleted": True}


@app.get("/api/shopping-list/plan")
def shopping_plan():
    """Satır başına en ucuz zincir ve "hepsini tek yerden" zincir toplamları (app/shopping.py plan)."""
    from app import shopping

    return shopping.plan()


@app.get("/api/shopping-list/{rid}/sizes")
def shopping_sizes(rid: int, brand: str | None = Query(None, max_length=40)):
    """Satırın boy seçenekleri: [{key, label, size_label, size_amount?, chain | null, price | null, unit_price_per?,
    per_unit?, best_value, hidden_count}] küçükten büyüğe (shop_best_value; en yaygın en çok 8 boy, resmî + topluluk
    satırları); best_value: litre / kilo fiyatı en düşük boy ("En kârlı"). brand verilirse o markanın fiyatları (boşsa
    "" marka fark etmez; verilmezse satırın markası). Kapatılan mağazalar görünmez. Seçim PATCH {group_key, brand?}."""
    from app import shop_stores, shopping

    got = shopping.sizes(rid, brand, shop_stores.current())
    if got is None:
        raise HTTPException(404, "Satır yok")
    return got


@app.get("/api/shopping-list/{rid}/combos")
def shopping_combos(rid: int, chain: str | None = Query(None, max_length=60), all_sizes: bool = True):
    """Satırın zincir × marka × boy seçicisi (shop_combo_picker): /api/prices/combos + selected {store, brand, key}.
    Satıra dokunulan seçim tek PATCH ile yazılır: {store, brand, group_key} (marka fark etmez: brand null; otomatik
    mağaza: store null). Plan sonra tam o teklifi kullanır (mağazaya bağlı + markalı satır)."""
    from app import shop_stores, shopping

    got = shopping.item_combos(rid, chain, all_sizes, shop_stores.current())
    if got is None:
        raise HTTPException(404, "Satır yok")
    return got


@app.get("/api/shopping-list/{rid}/brands")
def shopping_brands(rid: int):
    """Satırın ürününün markaları (shop_brand_size): {group, brands [{brand, sizes, chains}], selected | null}."""
    from app import price_sources, shopping

    with db.connect() as c:
        r = c.execute("SELECT text, brand FROM shopping_list WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "Satır yok")
    g = shopping._resolve([r["text"]])[0]
    if not g or g.get("pending"):
        return {"group": None, "brands": [], "selected": r["brand"]}
    base = g["key"].split("|", 1)[0]
    return {"group": base, "brands": price_sources.brands(shopping.brand_keys(base)), "selected": r["brand"]}


@app.get("/api/shopping-list/optimize")
def shopping_optimize(max_stops: int = Query(3, ge=1, le=3), stop_cost: float | None = Query(None, ge=0, le=10_000),
                      chains: str | None = Query(None, max_length=200)):
    """Nereye gideyim? En iyi 1 / 2 / 3 duraklı plan ve önerilen durak sayısı (app/shopping.py optimize).
    {region, date, stop_cost, max_stops, plans: [{k, stops: [{chain, total, items: [{id, text, qty, group_label, price
    (birim), line_total, unit_price_per?, per_unit?, brand?, source, date?, region?, product? (kişinin ürününün son
    alımı), own_place {place, price} | null (kafe alımı), cheaper_elsewhere {chain, price}? (satırın en ucuz zinciri
    birleşimde değil: "Hakmar'da ₺41,50")}]}], total, covered, missing [id],
    saving_vs_single, saving_vs_prev, extra_vs_best, recommended}], single: [{chain, total, covered, missing [id],
    vs_recommended}], recommended_k (null: fiyatlı satır yok), one_store (single[0]), priced, unpriced [id],
    headline {title, subtitle, cta, chains, total, saving, count} | null, unpriced_info [{id, text, reason no_size |
    no_group | no_price | pending, hint}], empty {title, text} | null, sources
    [kaynak yazısı], source_info {kinds [resmi|community|own], resmi_date, national}}. stop_cost verilmezse kişinin
    tercihi (meta.ui.stop_cost; varsayılan 100 ₺).
    shop_store_filter / shop_store_pin / shop_best_value: hidden {count, chains, local} (kişinin kapattığı mağazalar;
    hiç önerilmez), pinned [{id, text, chain}] (mağazaya bağlı satırlar; o mağaza her planda durak, stops[].pinned,
    fiyatı yoksa stops[].pinned_unpriced [{id, text}], satırda pinned), chains="Migros,BİM" (1–3; "Marketleri ben
    seçeyim") → tek plan yalnız o mağazalar üzerinden, manual {chains, missing [{id, text, cheapest {chain, price}}]} |
    null; chain_options [{chain, covered}] (seçici için, çok karşılayan önce); plans[].saving_vs_market (mağazaların
    orta fiyatına göre), size_saving, items[].better_size {key, label, size_label, chain, packs, price, total, saving,
    unit_price_per?, per_unit?} (En kârlı boy önerisi; yalnız öneri); headline.profit {saving, text, basis,
    size_saving, size_text} | null; unpriced_info reason store_no_price | brand_no_price."""
    from app import shopping

    want = [c for c in (chains or "").split(",") if c.strip()]
    try:
        return shopping.optimize(max_stops, stop_cost, want or None)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.patch("/api/shopping-list/{rid}")
def shopping_patch(rid: int, body: ShoppingPatch):
    from app import shopping

    try:
        got = shopping.update(rid, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    if not got:
        raise HTTPException(404, "Satır yok")
    return got


@app.delete("/api/shopping-list/{rid}")
def shopping_delete(rid: int):
    from app import shopping

    if not shopping.delete(rid):
        raise HTTPException(404, "Satır yok")
    return {"deleted": True}


@app.delete("/api/shopping-list")
def shopping_clear(done: bool = True):
    """İşaretlenmiş (alınmış) satırları sil. done=false desteklenmez (listeyi topluca silmek yok)."""
    from app import shopping

    if not done:
        raise HTTPException(422, "Yalnız alınmış satırlar topluca silinir")
    return {"deleted": shopping.clear_done()}


@app.get("/api/item-rules")
def list_item_rules():
    return db.list_item_rules()


@app.post("/api/item-rules")
def create_item_rule(body: ItemRuleIn):
    try:
        return {"id": db.save_item_rule(body.keywords, body.category)}
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.put("/api/item-rules/{rid}")
def update_item_rule(rid: int, body: ItemRuleIn):
    try:
        return {"id": db.save_item_rule(body.keywords, body.category, rid)}
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.delete("/api/item-rules/{rid}")
def delete_item_rule(rid: int):
    if not db.delete_item_rule(rid):
        raise HTTPException(404, "Kural yok")
    return {"ok": True}


@app.post("/api/item-rules/test")
def test_item_rule(body: dict):
    """Kurallar sayfasında deneme: 'MARLBORO TOUCH' → Sigara. rule_id / keyword: eşleşen kural ve kelime (yoksa null)."""
    text = str(body.get("text") or "")
    hit = db.match_item_rule(text)
    return {"category": hit["category"] if hit else None, "rule_id": hit["id"] if hit else None,
            "keyword": hit["keyword"] if hit else None}


# ---------------------------------------------------------------- borç / alacak

_YM = r"^\d{4}-(0[1-9]|1[0-2])$"


class DebtPlanFields(BaseModel):
    """Ödeme planı olan borç (yetenek debt_schedule; app/debt_schedule.py): başka yerde anlaşılmış sabit taksitler,
    yalnız kayıt. plan_installment verilince amount isteğe bağlı (taksit × sayı); sayı yerine plan_end (son taksit ayı)
    ya da toplam (amount) verilebilir."""
    plan_installment: float | None = Field(default=None, gt=0, le=100_000_000)
    plan_count: int | None = Field(default=None, ge=1, le=240)
    plan_end: str | None = Field(default=None, pattern=_YM)     # son taksit ayı (sayı yerine)
    plan_start: str | None = Field(default=None, pattern=_YM)   # ilk taksit ayı
    plan_day: int | None = Field(default=None, ge=1, le=31)
    creditor_type: Literal["vergi_sgk", "banka", "magaza", "kisi", "diger"] | None = None
    plan_interest: Literal["yok", "var", "bilmiyorum"] | None = None
    # borcun koşulları (yetenek debt_terms; yalnız "borcum", boşsa eski davranış): ödeme koşulu ve tarih türü
    pay_terms: Literal["taksitli", "parca", "tek", "bilinmiyor"] | None = None
    due_strict: bool | None = None  # true: tarihinde ödenmesi şart (kesin) · false: söz (esnek) · null: varsayılan
    # erken kapatma (yetenek debt_payoff; yalnız faizli planda): bankadan öğrenilen bugün kapatma tutarı, öğrenildiği
    # gün (boşsa bugün) ve isteğe bağlı aylık faiz oranı (yüzde). null: siler; gönderilmezse dokunulmaz
    payoff_amount: float | None = Field(default=None, gt=0, le=1_000_000_000)
    payoff_date: date | None = None
    plan_rate: float | None = Field(default=None, gt=0, le=15)

    def _payoff(self) -> dict:
        """Erken kapatma alanlarından yazılacaklar (yalnız gönderilenler). Tutar verilip gün verilmezse bugün."""
        out: dict = {}
        sent = self.model_fields_set
        if "payoff_amount" in sent:
            out["payoff_amount"] = money.money(self.payoff_amount) if self.payoff_amount else None
            if self.payoff_amount and "payoff_date" not in sent:
                out["payoff_date"] = date.today().isoformat()
            if not self.payoff_amount:
                out["payoff_date"] = None
        if "payoff_date" in sent and out.get("payoff_amount", True):
            out["payoff_date"] = self.payoff_date.isoformat() if self.payoff_date else out.get("payoff_date")
        if "plan_rate" in sent:
            out["plan_rate"] = round(self.plan_rate, 4) if self.plan_rate else None
        return out

    def _plan(self, amount: float | None) -> float | None:
        """Planı tamamla ve doğrula; dönen: toplam (amount)."""
        from app import debt_schedule

        if self.plan_installment is None:
            return amount
        self.plan_installment = money.money(self.plan_installment)
        if not self.plan_start:
            self.plan_start = date.today().isoformat()[:7]
        if self.plan_count is None:
            self.plan_count = debt_schedule.count_from(self.plan_installment, amount, self.plan_end, self.plan_start)
        if self.plan_count is None:
            raise ValueError("Taksit sayısını ya da son taksit ayını yaz")
        total = money.money(amount) if amount is not None else money.money(self.plan_installment * self.plan_count)
        err = debt_schedule.validate(self.plan_installment, self.plan_count, total)
        if err:
            raise ValueError(err)
        self.plan_day = self.plan_day or 1
        self.creditor_type = self.creditor_type or "diger"
        return total


class DebtIn(DebtPlanFields):
    person: str = Field(min_length=1)
    direction: Literal["borc", "alacak"]
    asset: str = Field(default="TRY", min_length=1, max_length=40)
    amount: float | None = Field(default=None, gt=0)  # ödeme planlı borçta isteğe bağlı (toplam)
    debt_date: date | None = None  # boş: bugün (ödeme planlı borçta ilk taksit ayı)
    due_date: date | None = None
    note: str | None = None
    account_id: int | None = None  # banka/nakit hesabı

    @model_validator(mode="after")
    def _tl(self):
        if self.direction == "alacak":  # koşullar yalnız borçta
            self.pay_terms = self.due_strict = None
        if self.plan_installment is not None:
            if self.direction != "borc" or (self.asset or "TRY") != "TRY":
                raise ValueError("Ödeme planı yalnız TL borçta girilir")
            self.amount = self._plan(self.amount)
            self.due_date = None  # vade taksitlerden gelir
            self.pay_terms = "taksitli"
        elif self.pay_terms == "taksitli":
            raise ValueError("Taksitli borçta aylık taksiti ve taksit sayısını yaz")
        if self.amount is None:
            raise ValueError("Miktar gerekli")
        if (self.asset or "TRY") == "TRY":  # TL borç: kuruşa; gram/adet olduğu gibi
            self.amount = money.money(self.amount)
        if self.debt_date is None:
            self.debt_date = date.fromisoformat(self.plan_start + "-01") if self.plan_start else date.today()
        return self


class DebtPatch(DebtPlanFields):
    person: str | None = None
    direction: Literal["borc", "alacak"] | None = None
    asset: str | None = None
    amount: float | None = Field(default=None, gt=0)
    debt_date: date | None = None
    due_date: date | None = None
    note: str | None = None
    account_id: int | None = None  # banka/nakit hesabı
    plan_off: bool = False  # ödeme planını kaldır: diğer borç olarak kalır (tutar ve ödemeler değişmez)

    @model_validator(mode="after")
    def _tl(self):
        if self.plan_installment is not None:
            self.amount = self._plan(self.amount)
            self.pay_terms = "taksitli"
        if self.amount is not None and self.asset == "TRY":  # TL borç: kuruşa; gram/adet olduğu gibi
            self.amount = money.money(self.amount)
        return self


# planı kaldırınca plan alanları boşalır: "planlı oluştur + kaldır" ile "diğer borç" aynı kayıt. Alacaklı türü
# (creditor_type) borcun kendisinindir, kalır; ödeme koşulu "taksitli" ise boşalır (bilinmiyor)
_PLAN_COLS = ("plan_installment", "plan_count", "plan_start", "plan_day", "plan_interest", "payoff_amount", "payoff_date",
              "plan_rate")


class DebtPayIn(BaseModel):
    amount: float | None = Field(default=None, gt=0)  # boş → kalanın tamamı
    paid_at: date
    account_id: int | None = None  # banka/nakit hesabı
    entry_id: int | None = None    # var olan hareket bu ödemedir (Yapılacaklar "Bu havale borçtan mı?" → Evet)
    # erken kapatma (yetenek debt_payoff): ödeme planlı borç bu ödemeyle kapandı; bankanın almadığı kalan faiz borçtan
    # düşer (borç tutarı = ödenen toplam). Ödeme geri alınırsa borç ödenmemiş kapatma tutarıyla açılır
    close_early: bool = False


@app.get("/api/debts")
def list_debts():
    return db.list_debts()


_PAYOFF_COLS = ("payoff_amount", "payoff_date", "plan_rate")
_PAYOFF_ONLY_INTEREST = "Kapatma tutarı yalnız faizli ödeme planında girilir"


@app.post("/api/debts")
def create_debt(body: DebtIn):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    data = body.model_dump(exclude={"plan_end", *_PAYOFF_COLS})
    data["debt_date"] = body.debt_date.isoformat()
    data["due_date"] = body.due_date.isoformat() if body.due_date else None
    payoff = body._payoff()
    if any(v is not None for v in payoff.values()):
        if body.plan_installment is None or body.plan_interest != "var":
            raise HTTPException(422, _PAYOFF_ONLY_INTEREST)
        data.update(payoff)
    return {"id": db.create_debt(data)}


@app.patch("/api/debts/{did}")
def patch_debt(did: int, body: DebtPatch):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    data = body.model_dump(exclude_unset=True, exclude={"plan_end", "plan_off", *_PAYOFF_COLS})
    payoff = body._payoff()
    if any(v is not None for v in payoff.values()):
        with db.connect() as c:
            row = c.execute("SELECT plan_installment, plan_interest FROM debts WHERE id = ?", (did,)).fetchone()
        interest = data["plan_interest"] if "plan_interest" in data else (row["plan_interest"] if row else None)
        if row is not None and (not (row["plan_installment"] or body.plan_installment) or interest != "var"):
            raise HTTPException(422, _PAYOFF_ONLY_INTEREST)
    data.update(payoff)
    if "plan_interest" in data and data["plan_interest"] != "var":  # faiz yoksa erken kapatma hesabı da yok
        data.update({k: None for k in _PAYOFF_COLS})
    if body.plan_installment is not None:  # tamamlanan alanlar (sayı, gün, toplam) da yazılsın
        data.update({k: getattr(body, k) for k in ("plan_installment", "plan_count", "plan_start", "plan_day",
                                                   "creditor_type", "amount", "pay_terms")})
        data["due_date"] = None
    if body.plan_off:
        data.update({k: None for k in _PLAN_COLS})
        if data.get("pay_terms") == "taksitli" or "pay_terms" not in data:
            data["pay_terms"] = None
    elif data.get("pay_terms") == "taksitli" and body.plan_installment is None:
        with db.connect() as c:
            row = c.execute("SELECT plan_installment FROM debts WHERE id = ?", (did,)).fetchone()
        if row is not None and not row["plan_installment"]:
            raise HTTPException(422, "Taksitli borçta ödeme planını ekle (aylık taksit ve taksit sayısı)")
    if data.get("amount") is not None:  # ödenmiş kısımdan aşağı indirilemez (fazla ödeme kalırdı)
        with db.connect() as c:
            paid = c.execute("SELECT COALESCE(SUM(amount), 0) FROM debt_payments WHERE debt_id = ?", (did,)).fetchone()[0]
        if paid and data["amount"] < paid - 1e-9:
            raise HTTPException(422, f"Bu kayıttan {_tl_tr(paid)} ödenmiş; tutar bundan az olamaz")
    for k in ("debt_date", "due_date"):
        if data.get(k):
            data[k] = data[k].isoformat()
    if not db.update_debt(did, data):
        raise HTTPException(404, "Kayıt yok")
    return {"ok": True}


class DebtScheduleIn(DebtPlanFields):
    """Var olan açık TL borcuna ödeme planı ekle ya da planı değiştir (yetenek debt_schedule_edit). Borcun tutarı
    korunur; taksitler kalan (tutar eksi ilk taksit ayından önce ödenen) üzerinden kurulur, toplam ≠ kalan olabilir (son
    taksit farkı alır). amount verilirse borcun toplamı da değişir (ör. "taksit toplamına göre güncelle")."""
    plan_installment: float = Field(gt=0, le=100_000_000)
    amount: float | None = Field(default=None, gt=0, le=1_000_000_000)


def _debt_row(did: int) -> dict:
    d = next((x for x in db.list_debts() if x["id"] == did), None)
    if not d:
        raise HTTPException(404, "Kayıt yok")
    return d


@app.put("/api/debts/{did}/schedule")
def put_debt_schedule(did: int, body: DebtScheduleIn):
    """Ödeme planı borcun alt kaydı: elden borca sonradan plan ekle ya da var olan planı düzenle. Dönüş: {ok, debt}."""
    from app import debt_schedule

    with db.connect() as c:
        d = c.execute("SELECT * FROM debts WHERE id = ?", (did,)).fetchone()
        pays = db.rows(c.execute("SELECT amount, paid_at FROM debt_payments WHERE debt_id = ?", (did,)))
    if not d:
        raise HTTPException(404, "Kayıt yok")
    if d["direction"] != "borc" or (d["asset"] or "TRY") != "TRY":
        raise HTTPException(422, "Ödeme planı yalnız TL borçta eklenir")
    if d["settled_at"] or d["written_off_at"]:
        raise HTTPException(422, "Bu borç kapanmış; ödeme planı eklenemez")
    inst = money.money(body.plan_installment)
    start = body.plan_start or date.today().isoformat()[:7]
    amount = money.money(body.amount) if body.amount is not None else d["amount"]
    paid = money.money(sum(p["amount"] or 0 for p in pays))
    if money.lt(amount, paid):
        raise HTTPException(422, f"Bu kayıttan {_tl_tr(paid)} ödenmiş; tutar bundan az olamaz")
    rest = money.money(amount - debt_schedule.paid_before(pays, start))
    if rest <= 0:
        raise HTTPException(422, "İlk taksit ayından önce borcun tamamı ödenmiş; ilk taksit ayını değiştir")
    count = body.plan_count or debt_schedule.count_from(inst, rest, body.plan_end, start)
    if not count:
        raise HTTPException(422, "Taksit sayısını ya da son taksit ayını yaz")
    if count > debt_schedule.MAX_COUNT:
        raise HTTPException(422, f"Taksit sayısı 1 ile {debt_schedule.MAX_COUNT} arasında olmalı")
    data = {"plan_installment": inst, "plan_count": count, "plan_start": start, "plan_day": body.plan_day or 1,
            "creditor_type": body.creditor_type or d["creditor_type"] or "diger", "plan_interest": body.plan_interest,
            "due_date": None, "pay_terms": "taksitli"}  # vade taksitlerden gelir
    # erken kapatma: yalnız gönderilen alanlar yazılır (eski istemci planı düzenleyince girilen tutar silinmez); faiz
    # "var" değilse silinir
    payoff = body._payoff()
    if body.plan_interest != "var":
        if any(v is not None for v in payoff.values()):
            raise HTTPException(422, _PAYOFF_ONLY_INTEREST)
        payoff = {k: None for k in _PAYOFF_COLS}
    data.update(payoff)
    if body.amount is not None:
        data["amount"] = amount
    db.update_debt(did, data)
    with db.connect() as c:
        db._recalc_debt(c, did)
    return {"ok": True, "debt": _debt_row(did)}


@app.delete("/api/debts/{did}/schedule")
def delete_debt_schedule(did: int):
    """Planı kaldır: borç diğer borç olarak kalır (tutar, ödemeler ve alacaklı türü değişmez; ödeme koşulu boşalır).
    Dönüş: {ok, debt}."""
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM debts WHERE id = ?", (did,)).fetchone():
            raise HTTPException(404, "Kayıt yok")
    db.update_debt(did, {**{k: None for k in _PLAN_COLS}, "pay_terms": None})
    return {"ok": True, "debt": _debt_row(did)}


@app.delete("/api/debts/{did}")
def delete_debt(did: int):
    if not db.delete_debt(did):
        raise HTTPException(404, "Kayıt yok")
    return {"ok": True}


def _client_key(request: Request) -> str | None:
    """İstemcinin işlem kimliği: "<kullanıcı id>:<Idempotency-Key>". Ara katman aynı anahtarın tekrarını 24 saat
    yakalar (app/idempotency.py); para ve liste yazan uçlar (borç ödemesi, listeye ekle) bunu satıra da yazar, çevrimdışı
    kuyruk (mobil lib/bekleyen.ts) günler sonra gönderse de çift kayıt olmaz."""
    key = (request.headers.get("idempotency-key") or "").strip()
    if not idempotency.KEY_RE.match(key):
        return None
    return f"{(db.current_user() or {}).get('id') or 0}:{key}"


@app.post("/api/debts/{did}/pay")
def pay_debt(did: int, body: DebtPayIn, request: Request):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba yazamaz (gövdedeki id)
    if body.entry_id is not None:
        _visible_or_404("entry", body.entry_id)
        with db.connect() as c:
            e = c.execute("SELECT debt_id FROM entries WHERE id = ?", (body.entry_id,)).fetchone()
        if not e:
            raise HTTPException(404, "Kayıt yok")
        if e["debt_id"] or db._entry_is_debt_linked(body.entry_id):
            raise HTTPException(409, "Bu hareket zaten bir borca bağlı")
    try:
        if body.close_early:
            with db.connect() as c:
                row = c.execute("SELECT plan_installment, direction, settled_at FROM debts WHERE id = ?", (did,)).fetchone()
            if row is not None and (not row["plan_installment"] or row["direction"] != "borc" or row["settled_at"]):
                raise HTTPException(422, "Erken kapatma yalnız açık, ödeme planı olan borçta yazılır")
            if body.amount is None:
                raise HTTPException(422, "Kapatırken ödediğin tutarı yaz")
        pid = db.pay_debt(did, body.amount, body.paid_at.isoformat(), entry_id=body.entry_id, account_id=body.account_id,
                          client_key=_client_key(request))
        if body.close_early:
            db.close_debt_early(did)
    except KeyError as e:
        raise HTTPException(404, "Kayıt yok") from e
    except db.OverpayError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    return {"ok": True, "payment_id": pid}  # Geri al: DELETE /api/debt-payments/{payment_id}


class DebtWriteOffIn(BaseModel):
    on: date


@app.post("/api/debts/{did}/write-off")
def write_off_debt(did: int, body: DebtWriteOffIn):
    try:
        db.write_off_debt(did, body.on.isoformat())
    except KeyError as e:
        raise HTTPException(404, "Kayıt yok") from e
    return {"ok": True}


@app.delete("/api/debts/{did}/pay")
def unpay_debt(did: int):
    db.unpay_debt(did)
    return {"ok": True}


# ---------------------------------------------------------------- transferler

class CounterpartyIn(BaseModel):
    category: str | None = None
    track_debt: bool | None = None
    note: str | None = None


@app.get("/api/transfers")
def transfers(month: str | None = None, start: str | None = None, end: str | None = None):
    """month=YYYY-MM ya da start/end=YYYY-MM-DD (dönem: ör. kart dönemi 16 Eki–15 Kas): kişi toplamları ve items o
    aralıkta. months (genel) ve people[].months (kişi başı) aylık kırılım her zaman tüm zaman için."""
    import re as _re

    for v, pat in ((month, r"\d{4}-\d{2}"), (start, r"\d{4}-\d{2}-\d{2}"), (end, r"\d{4}-\d{2}-\d{2}")):
        if v and not _re.fullmatch(pat, v):
            raise HTTPException(422, "Tarih biçimi: month=YYYY-MM, start/end=YYYY-MM-DD")
    from app import rawnote, sync

    out = db.transfers_overview(month, start, end)
    out["totals"] = _transfer_totals(out)
    info = sync.match_info()
    for key in ("items", "self"):
        for i in out.get(key) or []:
            m = info.get(i.get("id")) or {}
            i["raw"] = m["description"] if m.get("source") == "dokum" and m.get("description") else \
                rawnote.raw_of(i.get("note"))
            i["note_user"] = rawnote.user_note(i.get("note"))
    return out


def _transfer_totals(out: dict) -> dict:
    """Aralığın toplamları (istemci toplamasın): kişilere giden/gelen (kendin hariç), kendi hesapların arası giriş/çıkış."""
    rng = out["range"]
    start, end = (f"{rng['month']}-01", f"{rng['month']}-31") if rng.get("month") else (rng.get("start"), rng.get("end"))
    own = [o for o in out["own"] if (not start or o["transfer_date"] >= start) and (not end or o["transfer_date"] <= end)]
    sent = money.money(sum(p["sent"] for p in out["people"]))
    received = money.money(sum(p["received"] for p in out["people"]))
    return {"sent": sent, "received": received, "net": money.money(received - sent),
            "own_in": money.money(sum(o["amount"] for o in own if o["amount"] > 0)),
            "own_out": money.money(-sum(o["amount"] for o in own if o["amount"] < 0)), "own_count": len(own)}


@app.put("/api/transfers/people/{name}")
def set_person(name: str, body: CounterpartyIn):
    return {"updated": db.set_counterparty(name, body.category, body.track_debt, body.note)}


class PersonRename(BaseModel):
    name: str  # yeni ad; başka bir kişinin adıysa iki kişi birleşir


class OwnTransferPatch(BaseModel):
    description: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=500)
    not_transfer: bool = False  # "virman değil": gelir/gider kaydına geri çevir


@app.patch("/api/own-transfers/{tid}")
def patch_own_transfer(tid: int, body: OwnTransferPatch):
    """Kendi hesapları arası aktarım: açıklama/not düzenle ya da yanlış eşleşmeyse (not_transfer) gelir/gider kaydına
    çevir. Dönüş: {own_transfer} ya da {entry_id, kind} (çevrildiyse)."""
    from app import book

    if book.member():
        raise HTTPException(403, "Bu işlemi defter sahibi yapabilir")
    if body.not_transfer:
        eid = db.own_transfer_to_entry(tid)
        if eid is None:
            raise HTTPException(404, "Aktarım yok")
        with db.connect() as c:
            kind = c.execute("SELECT kind FROM entries WHERE id = ?", (eid,)).fetchone()[0]
        return {"entry_id": eid, "kind": kind}
    r = db.update_own_transfer(tid, body.description.strip() if body.description is not None else None, body.note)
    if r is None:
        raise HTTPException(404, "Aktarım yok")
    return {"own_transfer": r}


@app.patch("/api/transfers/people/{name}")
def rename_person(name: str, body: PersonRename):
    """Kişiyi yeniden adlandır / başka kişiyle birleştir: kayıtları, borç defteri ve kişi ayarı yeni ada geçer."""
    if len(body.name.strip()) > 80:
        raise HTTPException(422, "Ad çok uzun")
    try:
        return db.rename_counterparty(name, body.name)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


# ---------------------------------------------------------------- statements

@app.get("/api/statements")
def list_statements():
    from app import statement_reread

    return statement_reread.annotate(db.list_statements())  # reread_rows: şablonla yeniden okunabilir (önbellekten)


class _Kind(str):
    sure = True  # sınıflandırıcı belge türünden emin mi


def _statement_kind(data: bytes, passwords: list[str]) -> str | None:
    """PDF kart ekstresi mi hesap dökümü mü dekont mu? (açılamazsa None: kart akışı şifre ister)"""
    k = _classify_pdf(data, passwords)
    if not k:
        return None
    out = _Kind(k[0])
    out.sure = k[1]
    return out


def _try_text(data: bytes, pw: str | None) -> str | None:
    from app.parsers import extract_text

    try:
        return extract_text(data, pw)
    except Exception:  # noqa: BLE001 - şifreli / bozuk
        return None


def _classify_pdf(data: bytes, passwords: list[str]) -> tuple[str, bool] | None:
    from app.mailbox import classify
    from app.parsers import extract_text

    from app.limits import LimitError

    for pw in [None, *passwords]:
        try:
            text = extract_text(data, pw)
        except LimitError:
            raise  # şifreyle ilgisi yok: her şifreyle yeniden denemek aynı süreyi tekrar harcar
        except Exception:  # noqa: BLE001 - şifreli: sıradaki
            continue
        return classify(text)
    return None


@app.post("/api/statements/upload")
@_heavy
def upload_statements(files: list[UploadFile] = File(...), password: str | None = Form(None),
                      remember: bool = Form(False),
                      kind: Literal["card", "account", "dekont"] | None = Form(None)):
    from app import undo

    limits.check_count(len(files))  # 413, Türkçe neden (web tüm seçimi tek istekte gönderir)
    names = ", ".join(f.filename or "dosya" for f in files)
    with undo.batch(f"Ekstre: {names[:120]}") as b:
        results = _upload_statements(files, password, remember, kind)
        b["summary"] = [{k: r.get(k) for k in ("filename", "status", "bank", "card_no", "period_end", "txn_count",
                                                "message")} for r in results]
    return results


def _upload_statements(files: list[UploadFile], password: str | None, remember: bool,
                       forced: str | None = None) -> list[dict]:
    from app import doc_cache, doc_reads
    from app.parsers import PasswordRequired

    results = []
    tried = ([password] if password else []) + db.pdf_passwords()
    for f in files:
        data = _file_bytes(f)
        res = {"filename": f.filename}
        name = (f.filename or "").lower()
        try:
            limits.check_size(data)
        except limits.LimitError as e:
            results.append({**res, "status": "error", "message": errors.user_text(e)})
            continue
        try:  # hazır sürüm: her yeni belge 1 okuma hakkı; doluysa bu dosya okunmaz
            doc_reads.check(data)
        except doc_reads.QuotaError as e:
            results.append({**res, "status": "error", "message": errors.user_text(e), **e.fields()})
            continue
        if name.endswith((".xlsx", ".xls", ".csv")):  # tablo: kart işlem dökümü mü, hesap hareketleri mi?
            from app import card_excel

            try:
                imp = card_excel.import_file(data, f.filename or "kart.xlsx")
            except limits.LimitError as e:  # zip bombası vb.: hesap hareketleri akışına da gönderilmez
                results.append({**res, "status": "error", "message": errors.user_text(e)})
                continue
            except Exception:  # noqa: BLE001 - okunamazsa hesap hareketleri akışı denesin
                imp = None
            if imp:
                if imp["statements"]:  # hepsi zaten vardıysa hak düşmez
                    doc_reads.charge(data)
                results.append({**res, "status": "saved", "bank": imp["bank"], "card_no": imp["card_no"],
                                "card_excel": imp, "txn_count": imp["transactions"],
                                "message": f"Kart dökümü: {imp['statements']} aylık ekstre, {imp['transactions']} işlem"})
            else:
                results.append({**res, "status": "account_statement"})
            continue
        try:
            guess = _statement_kind(data, tried)
        except limits.LimitError as e:  # çok sayfalı / çok uzun süren PDF: bu dosya okunmaz, diğerleri sürer
            results.append({**res, "status": "error", "message": errors.user_text(e)})
            continue
        kind = forced or guess
        if not forced and guess == "account" and not getattr(guess, "sure", True):
            # hesap dökümü olduğundan emin değil: tahminle bakiyeyi bozma, kullanıcıya sor
            results.append({**res, "status": "ask", "guess": str(guess)})
            continue
        if kind == "dekont":  # tek işlem: hesaba eklenir, bakiye değişmez
            from app import mailbox

            try:
                if forced:  # kullanıcı "dekont" dedi: başlıkta "dekont" yazmasa da tek işlem olarak oku
                    from app import dekont

                    text = next(t for t in (_try_text(data, pw) for pw in [None, *tried]) if t)
                    d = dekont.import_text(text)
                    msg = f"Dekont: {money.tl(d['amount'])} {d['kind']}" + (" (zaten vardı)" if d["status"] == "duplicate" else "")
                    if d["status"] not in ("duplicate", "updated"):
                        doc_reads.charge(data)
                else:  # handle_pdf yeni belgeyi kendisi sayar
                    msg = mailbox.handle_pdf(f.filename or "dekont.pdf", data)
                results.append({**res, "status": "saved", "message": msg, "dekont": True})
            except Exception as e:  # noqa: BLE001
                results.append({**res, "status": "error", "message": errors.user_text(e)})
            continue
        if kind == "account":  # vadesiz hesap dökümü: arayüz hesap hareketleri önizlemesine yönlendirir
            if password and remember:
                db.remember_pdf_password(password)
            results.append({**res, "status": "account_statement"})
            continue
        try:
            with doc_cache.reading(data, doc_cache.EKSTRE) as slot:  # yapay zekâ yedeği: aynı ekstre yeniden okunmaz
                stmt = parse_statement(data, passwords=tried)
        except PasswordRequired as e:
            results.append({**res, "status": "password_required", "message": errors.user_text(e) if password else "PDF şifreli"})
            continue
        except Exception as e:  # noqa: BLE001 - kullanıcıya göster
            results.append({**res, "status": "error", "message": errors.user_text(e)})
            continue
        if password and remember:
            db.remember_pdf_password(password)
        res.update(bank=stmt.bank, card_no=stmt.card_no, period_end=stmt.period_end.isoformat(),
                   total_debt=stmt.total_debt, txn_count=len(stmt.transactions),
                   reconciliation_diff=stmt.reconciliation_diff(), ai=stmt.ai_parsed, source=stmt.source,
                   **({"cached": True} if slot.hit else {}))
        ai_called = stmt.ai_parsed and not slot.hit
        if ai_called:  # yapay zekâ gerçekten okudu (önbellekte yoktu): tekrar muafiyeti yok, okuma sayılır
            doc_reads.forget(data)
        if db.statement_exists(data, stmt):
            # zaten vardı: yerel okumada hak düşmez; yapay zekâ çağrıldıysa düşer (sonuç önbellekte, tekrarı bedava)
            if ai_called and (charged := doc_reads.charge(data)):
                res["charged"] = charged
            results.append({**res, "status": "duplicate"})
            continue
        sid = db.save_statement(data, f.filename or "ekstre.pdf", stmt)
        res["statement_id"] = sid  # İlk gün özeti (GET /api/first-look?ids=) bu ekstreleri özetler
        # yerel ayrıştırıcı ya da yapay zekâ yedek okuması: belge başına (her 10 sayfa) 1
        if charged := doc_reads.charge(data):
            res["charged"] = charged
        from app import verification

        chk = verification.statement_check(stmt)  # tutmadıysa Yapılacaklar'da 'ekstre_kontrol' (S6)
        results.append({**res, "status": "saved", "linked": db.auto_link(), "marked_paid": db.auto_mark_paid(),
                        **{k: chk[k] for k in ("verified", "verification", "verification_text", "equation_diff")}})
    from app import sync

    sync.reconcile()  # öğrenilmiş kurallarla eşleşen kart harcamaları (kalanlar Yapılacaklar'da sorulur)
    changed = db.auto_update_cycle()
    if changed and results:
        results[-1]["cycle_day"] = changed  # arayüz "ay dönümü X'e güncellendi" der
    if (warn := db.take_storage_warning()) and results:
        results[-1]["storage_warning"] = warn
    return results


@app.get("/api/first-look")
def first_look(ids: str | None = Query(None, max_length=400, pattern=r"^\d{1,12}(,\d{1,12}){0,49}$")):
    """İlk gün özeti (app/first_look.py): yüklenen kart ekstrelerinden bu ay ödenecekler, ödenen faiz/ücret, gelecek
    taksitler ve sonraki adımlar. ids: yükleme sonucundaki statement_id'ler (virgülle); yoksa her kartın son ekstresi.
    first: kullanıcının ilk haftası (istemci özeti yalnız o zaman belirgin gösterir)."""
    from app import first_look as fl

    return fl.build([int(x) for x in ids.split(",")] if ids else None)


@app.get("/api/statement-help")
def statement_help(platform: Literal["web", "mobile"] | None = None):
    """Ekstre ve hesap dökümü nasıl indirilir (app/statement_help.py): web ve mobil aynı içerik, tek kaynak."""
    from app import statement_help as sh

    return sh.content(platform)


@app.get("/api/statements/{sid}/file")
def statement_file(sid: int):
    """Ekstrenin saklanan PDF'i. Kayıt bu defterden okunur; ortak defterde gizli kartın ekstresi yok sayılır."""
    s = next((x for x in db.list_statements() if x["id"] == sid), None)  # list_statements üye kurallarıyla süzer
    if not s:
        raise HTTPException(404, "Ekstre bulunamadı")
    name = f"{s['file_hash']}.pdf"
    return _stored_file("uploads", name, f"{s['bank']}-{s['card_no'][-4:]}-{s['period_end']}.pdf")


@app.post("/api/statements/{sid}/confirm")
def confirm_statement(sid: int):
    """'Denklem tutmasa da rakamlar doğru': confirmed_at yazılır, needs_check false olur. Geri alma: DELETE aynı yol."""
    if not any(x["id"] == sid for x in db.list_statements()):
        raise HTTPException(404, "Ekstre bulunamadı")
    db.confirm_statement(sid, True)
    return {"ok": True, "confirmed_at": next(x["confirmed_at"] for x in db.list_statements() if x["id"] == sid)}


@app.delete("/api/statements/{sid}/confirm")
def unconfirm_statement(sid: int):
    if not any(x["id"] == sid for x in db.list_statements()):
        raise HTTPException(404, "Ekstre bulunamadı")
    db.confirm_statement(sid, False)
    return {"ok": True, "confirmed_at": None}


class RereadApplyIn(BaseModel):
    sig: str | None = Field(None, max_length=40)  # önizlemedeki imza: arada satırlar değiştiyse 409


def _reread_guard() -> None:
    from app import statement_reread

    if not statement_reread._can_apply():
        raise HTTPException(403, "Bu işlemi defter sahibi yapabilir")


@app.get("/api/statements/{sid}/reread-preview")
def statement_reread_preview(sid: int):
    """Yapay zekânın okuduğu ekstre ile aynı belgenin şablon (yapay zekâsız) okuması arasındaki fark: {available, rows,
    sig, diff {added, removed, changed [{before, after}]}, items [{kind, label, text, note?}], totals_before/after,
    equation_ok_after}. Yapay zekâ çağrılmaz, okuma hakkı düşmez (app/statement_reread.py)."""
    from app import statement_reread

    _reread_guard()
    try:
        return statement_reread.preview(sid)
    except KeyError:
        raise HTTPException(404, "Ekstre bulunamadı") from None


@app.post("/api/statements/{sid}/reread-apply")
def statement_reread_apply(sid: int, body: RereadApplyIn | None = None):
    """Farkı uygula: eşleşen satırlar (kategori, not, eşleşme, bölme, kim harcadı) korunur, eksik eklenir, fazla
    silinir, ters işaret düzelir; kaynak 'sablon'. Geri al: undo_id → POST /api/imports/{id}/undo."""
    from app import statement_reread

    _reread_guard()
    try:
        return statement_reread.apply(sid, body.sig if body else None)
    except KeyError:
        raise HTTPException(404, "Ekstre bulunamadı") from None
    except statement_reread.RereadError as e:
        raise HTTPException(409, str(e)) from None


_ROW_TYPE = Literal["harcama", "iade", "odeme", "faiz_ucret"]


class StatementRowIn(BaseModel):
    txn_date: date | None = None  # yoksa kesim tarihi
    description: str | None = Field(None, max_length=120)
    amount: Money = Field(gt=0, le=10_000_000)  # artı; işaret türden (iade, ödeme eksi)
    txn_type: _ROW_TYPE
    dry_run: bool = False  # yazmadan sonucu döndür (canlı "Tuttu ✓")


class StatementRowPatch(BaseModel):
    amount: Money | None = Field(None, gt=0, le=10_000_000)
    txn_type: _ROW_TYPE | None = None
    txn_date: date | None = None
    is_deleted: bool | None = None  # hesaba katma / kat
    dry_run: bool = False


def _row_call(fn, *a, **kw):
    from app import statement_fix

    if not statement_fix.can_edit():
        raise HTTPException(403, "Bu işlemi defter sahibi yapabilir")
    try:
        return fn(*a, **kw)
    except KeyError:
        raise HTTPException(404, "Ekstre ya da satır bulunamadı") from None
    except statement_fix.FixError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/api/statements/{sid}/rows")
def statement_rows(sid: int):
    """Ekstrenin satırları (düzeltme için) ve durumu: {rows [{id, txn_date, description, amount, txn_type,
    is_deleted}], check {ok, diff, hint, reconciliation_diff, equation_diff, rows_diff}} (app/statement_fix.py)."""
    from app import statement_fix

    return _row_call(statement_fix.rows_of, sid)


@app.post("/api/statements/{sid}/rows")
def statement_row_add(sid: int, body: StatementRowIn):
    """Ekstreye eksik satır ekle (tutar artı, işaret türden). dry_run: yazmaz, sonucu döndürür. Yanıt {ok, id,
    undo_id, check}; geri al: POST /api/imports/{undo_id}/undo."""
    from app import statement_fix

    data = body.model_dump(exclude={"dry_run"})
    return _row_call(statement_fix.add_row, sid, data, dry_run=body.dry_run)


@app.patch("/api/statements/{sid}/rows/{txn_id}")
def statement_row_edit(sid: int, txn_id: int, body: StatementRowPatch):
    """Ekstre satırının tutarını (artı), türünü (işaret türden), tarihini düzelt ya da hesaba katma. dry_run: yazmaz.
    Yanıt {ok, undo_id, check}; geri al: POST /api/imports/{undo_id}/undo."""
    from app import statement_fix

    data = body.model_dump(exclude={"dry_run"}, exclude_none=True)
    return _row_call(statement_fix.edit_row, sid, txn_id, data, dry_run=body.dry_run)


@app.delete("/api/statements/{sid}")
def delete_statement(sid: int):
    db.delete_statement(sid)
    return {"ok": True}


# ---------------------------------------------------------------- sabit kalem önerileri

class SuggestionAccept(BaseModel):
    key: str
    description: str | None = Field(None, max_length=60)
    amount: Money | None = Field(None, gt=0)


class AmountChange(BaseModel):
    from_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: Money = Field(ge=0)


@app.get("/api/recurring/{rid}/changes")
def list_amount_changes(rid: int):
    return db.amount_changes(rid)


@app.post("/api/recurring/{rid}/changes")
def add_amount_change(rid: int, body: AmountChange):
    if not any(r["id"] == rid for r in db.list_recurring()):
        raise HTTPException(404, "Sabit kalem bulunamadı")
    return {"id": db.add_amount_change(rid, body.from_month, body.amount)}


@app.delete("/api/recurring-changes/{cid}")
def delete_amount_change(cid: int):
    db.delete_amount_change(cid)
    return {"ok": True}


@app.get("/api/recurring/drift")
def recurring_drift(salary: bool = False):
    """Tutarı değişen sabit kalemler. salary=1 (yetenek salary_raise): maaş kalemleri yatan maaşa göre ayrı kuralla
    (app/salary.py; type "maas", title/text sunucudan, Güncelle → .../salary-drift/accept). Parametresiz eski davranış."""
    return finance.recurring_drift(salary=salary)


class SalaryDriftIn(BaseModel):
    recurring_id: int
    month: str = Field(pattern=r"^\d{4}-\d{2}$")  # önerinin dayandığı son yatan ay (drift[].month)


@app.post("/api/recurring/salary-drift/accept")
def accept_salary_drift(body: SalaryDriftIn):
    """"Güncelle": önerideki brüt from_month'tan itibaren maaş değişikliği olur (aynı aya girilen beklenen zamın
    yerine geçer). Öneri artık yoksa 409."""
    from app import salary

    try:
        return {"id": salary.accept(body.recurring_id, body.month)}
    except KeyError as e:
        raise HTTPException(409, "Bu öneri artık geçerli değil") from e


@app.post("/api/recurring/salary-drift/dismiss")
def dismiss_salary_drift(body: SalaryDriftIn):
    """"Bu ay farklıydı": o ay öneriden ve taban hesabından çıkar (ikramiye, eksik gün)."""
    from app import salary

    if not any(r["id"] == body.recurring_id for r in db.list_recurring()):
        raise HTTPException(404, "Sabit kalem bulunamadı")
    salary.dismiss(body.recurring_id, body.month)
    return {"ok": True}


@app.get("/api/year-summary")
def year_summary(year: int | None = None):
    return finance.year_summary(year or date.today().year)


def _digest_key(month: str) -> str:
    u = db.current_user()  # ortak defterde herkes kendi kapatır
    return f"digest_seen:{month}" + (f":{u['id']}" if u and u.get("member_of") else "")


@app.get("/api/digest")
def digest():
    """Yeni dönemin ilk haftası: biten dönemin özeti (kapatılmadıysa). Başka zaman null."""
    cur = finance.current_period()
    prev = finance.add_months(cur, -1)
    if (date.today() - finance.period_bounds(cur)[0]).days > 7 or db.get_setting(_digest_key(prev)):
        return None
    return finance.month_digest(prev)


@app.post("/api/digest/dismiss")
def digest_dismiss():
    db.set_setting(_digest_key(finance.add_months(finance.current_period(), -1)), date.today().isoformat())
    return {"ok": True}


@app.get("/api/recurring/suggestions")
def recurring_suggestions():
    return finance.recurring_suggestions()


@app.post("/api/recurring/suggestions/accept")
def accept_recurring_suggestion(body: SuggestionAccept):
    try:
        return {"id": finance.accept_recurring_suggestion(body.key, body.description, body.amount)}
    except KeyError as e:
        raise HTTPException(404, "Öneri bulunamadı") from e


@app.post("/api/recurring/suggestions/dismiss")
def dismiss_recurring_suggestion(body: SuggestionAccept):
    finance.dismiss_recurring_suggestion(body.key)
    return {"ok": True}


# Aynı olabilecek iki sabit kalem (app/recurring_dupes.py): yalnız öneri; birleştirme kullanıcının onayıyla
@app.get("/api/recurring/duplicates")
def recurring_duplicates():
    from app import recurring_dupes

    return recurring_dupes.find()


class DuplicateMergeIn(BaseModel):
    keep: int  # kalacak (ödemelere bağlı olan)
    drop: int  # silinecek


@app.post("/api/recurring/duplicates/merge")
def merge_recurring_duplicates(body: DuplicateMergeIn):
    """Öneriyi uygula: drop'un bağlı kayıtları ve aya özel tutarları keep'e geçer, drop silinir. Yalnız şu an önerilen
    çift birleşir (409). Geri alınabilir: undo_id → POST /api/imports/{id}/undo."""
    from app import recurring_dupes, undo

    if not any(p["keep"]["id"] == body.keep and p["drop"]["id"] == body.drop for p in recurring_dupes.find()):
        raise HTTPException(409, "Bu öneri artık geçerli değil")
    try:
        with undo.batch(f"Sabit kalem birleştirme: #{body.drop} → #{body.keep}") as b:
            res = recurring_dupes.merge(body.keep, body.drop)
            b["summary"] = res
    except recurring_dupes.DupError as e:
        raise HTTPException(409, str(e)) from e
    return {**res, "undo_id": b["id"]}


class DuplicateSeparateIn(BaseModel):
    a: int
    b: int


@app.post("/api/recurring/duplicates/separate")
def separate_recurring_duplicates(body: DuplicateSeparateIn):
    """"Ayrı kalsın": bu iki sabit kalem bir daha aynı diye önerilmez."""
    from app import recurring_dupes

    ids = {r["id"] for r in db.list_recurring()}
    if body.a not in ids or body.b not in ids or body.a == body.b:
        raise HTTPException(404, "Sabit kalem bulunamadı")
    recurring_dupes.keep_separate(body.a, body.b)
    return {"ok": True}


# ---------------------------------------------------------------- ortak defter (eş vb.)

class BookRules(BaseModel):
    hidden_cards: list[str] | None = None
    hidden_accounts: list[int] | None = None
    hide_fees: bool | None = None
    hidden_pages: list[str] | None = None


def _book_owner() -> dict:
    """Ortak defteri yönetebilen: giriş yapmış ve kendisi üye olmayan kullanıcı."""
    u = db.current_user()
    if not u:
        raise HTTPException(409, "Ortak defter için giriş (kullanıcı hesabı) gerekli")
    if u.get("member_of"):
        raise HTTPException(403, "Bu bölüm defter sahibine ait")
    return u


@app.get("/api/book/me")
def book_me():
    from app import book

    out = book.info()
    u = db.current_user()
    if plans.hosted() and u and u.get("member_of"):  # Aile koltuğu (uygulama içi uyarı: grace / none)
        out["seat"] = plans.seat_info(system.user(u["id"]) or u)
    return out


def _shared_people() -> list[dict]:
    from app import book

    return book.shared_people()


@app.get("/api/book/member-spend")
def book_member_spend(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")):
    """Kim ne harcadı (Aile, plans "aile_rapor"): ortak defterde dönemin gideri kişi başına, kategori kırılımıyla.
    Harcayan (finance.Spenders): kayıtta elle seçilen (PUT /api/spender; sabit kalemde her ay) → elle kaydı ekleyen →
    kart işleminde eşleştiği elle kaydı ekleyen → kartın kişisi (PUT /api/cards/{card_no}/spender) → "Ortak" (key
    "ortak"); defterden ayrılan kişinin payı Ortak'a düşer.
    Üyenin göremediği kart/kayıt/faiz rapora girmez (veri okuma süzgeçleri); üyenin "Benim" defteri hiç girmez
    (o defterde 404). shared: false → tek kişilik defter (people boş). İşlemler süzgeci: /api/transactions?who=<key>."""
    u = db.current_user()
    if u and u.get("own_book"):
        raise HTTPException(404, "Kişi başı harcama yalnız ortak defterde")
    _history_gate(month)
    m = month or finance.current_period()
    ppl = _shared_people()
    if not ppl:
        return {"month": m, "shared": False, "total": 0.0, "people": [], "me": u["id"] if u else None}
    out = finance.member_spend(m, ppl)
    for p in out["people"]:
        p["me"] = bool(u and p["user_id"] == u["id"])
    return {**out, "shared": True, "me": u["id"] if u else None}


# ---------------------------------------------------------------- kim harcadı: kartı kişiye bağla, kayıtta seç

def _spender_book() -> list[dict]:
    """Harcayan atama uçlarının ortak koşulu: ortak defter (sahip + en az bir üye; üyenin "Benim" defteri değil).
    Paket kapısı middleware'de (plans "aile_rapor"), yazma yetkisi defter kurallarında (app/book.py MEMBER_WRITES:
    sahip, tam yetkili ve "görür ve ekler"; sadece görür 403)."""
    ppl = _shared_people()
    if not ppl:
        raise HTTPException(404, "Yalnız ortak defterde")
    return ppl


def _spender_uid(who: str | None, ppl: list[dict]) -> int | None:
    """"u<id>" → defterdeki kişinin id'si; "ortak" / None → None. Defterde olmayan kişi 400."""
    if who is None or who == finance.SHARED:
        return None
    m = re.fullmatch(r"u(\d+)", who)
    if not m or int(m.group(1)) not in {p["id"] for p in ppl}:
        raise HTTPException(400, "Kişi bu defterde değil")
    return int(m.group(1))


class SpenderIn(BaseModel):
    source: Literal["manual", "card", "recurring"]  # elle kayıt | kart işlemi | sabit kalem (her ayı)
    id: int = Field(..., ge=1)
    who: str | None = Field(None, max_length=20)  # "u<id>" | "ortak" (açıkça Ortak) | null (seçimi kaldır: otomatik)


@app.put("/api/spender")
def put_spender(body: SpenderIn):
    """Kayıtta kim harcadı (Aile, ortak defter): otomatik kuralın önüne geçer (finance.Spenders). who null: elle seçim
    kalkar, harcayan yine otomatik bulunur. Yanıt: {who: değişiklikten sonraki harcayan ("u<id>"|"ortak"), set: elle
    seçim var mı}. Kayıt yoksa ya da üyenin göremediği (gizli kart/kayıt) bir kayıtsa 404."""
    from app import book

    ppl = _spender_book()
    row = finance.spender_row(body.source, body.id)
    kind = {"manual": "entry", "card": "txn"}.get(body.source)
    if row is None or (kind and not book.visible(kind, body.id)):
        raise HTTPException(404, "Kayıt bulunamadı")
    if body.who is None:
        db.clear_spender_override(body.source, body.id)
    else:
        db.set_spender_override(body.source, body.id, _spender_uid(body.who, ppl))
    sp = finance.Spenders(ppl)
    return {"who": sp.of(row), "set": sp.is_set(row)}


class CardSpenderIn(BaseModel):
    who: str | None = Field(None, max_length=20)  # "u<id>" | null ya da "ortak": kart kimseye bağlı değil (Ortak)


@app.get("/api/cards/spenders")
def card_spenders():
    """Ortak defterde kartların kişisi: {cards: {card_no: "u<id>"}} (bağlı olmayan kart yok; cards/summary spender ile
    aynı). Üyeye gizli kart listede yer almaz."""
    sp = finance.Spenders(_spender_book())
    seen = {s["card_no"] for s in db.list_statements()}
    return {"cards": {no: who for no in sorted(seen) if (who := sp.card(no))}}


@app.put("/api/cards/{card_no}/spender")
def put_card_spender(card_no: str, body: CardSpenderIn):
    """Kartı kişiye bağla (Aile, ortak defter): bu kartın ekstre satırları o kişinin harcaması sayılır (kayıtta elle
    seçim ve eşleşen elle kaydı ekleyen yine önce gelir). who null / "ortak": bağ kalkar. Yanıt: {card_no, who}.
    Kart yoksa ya da üyeye gizliyse 404."""
    ppl = _spender_book()
    if not any(s["card_no"] == card_no for s in db.list_statements()):  # üyeye gizli kart listede yok
        raise HTTPException(404, "Kart bulunamadı")
    db.set_card_spender(card_no, _spender_uid(body.who, ppl))
    return {"card_no": card_no, "who": finance.Spenders(ppl).card(card_no)}


@app.get("/api/book")
def book_overview(request: Request):
    from app import book

    u = _book_owner()
    from app import email_verify

    if email_verify.required():
        email_verify.purge_invite_pending()
    cards = {}
    for s in db.list_statements():
        cards.setdefault(s["card_no"], {"card_no": s["card_no"], "bank": s["bank"]})
    return {"members": [{"id": m["id"], "username": m["username"], "last_login": m["last_login"], "disabled": bool(m["disabled"]),
                         "role": book.role(m), "own_rules": book.has_own_rules(m["id"]), "rules": book.rules(m["id"]),
                         # hazır sürüm: Aile koltuğu {state: own|seat|grace|none, grace_until?}; kendi hesabıyla katılan
                         # (çıkarılınca hesabı silinmez, yalnız ayrılır)
                         **({"seat": plans.seat_info(m)} if plans.hosted() else {}), "joined": bool(m.get("db_file")),
                         # davetle açıldı, e-posta kodu henüz girilmedi (girilmezse hesap kendiliğinden silinir)
                         "email_verified": not email_verify.invite_pending(m["id"])}
                        for m in system.members(u["id"])],
            "invites": [_invite_out(i, request) for i in system.invites() if i.get("member_of") == u["id"] and not i["used_at"]],
            "rules": book.rules(), "cards": list(cards.values()),
            "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"]} for a in db.list_accounts()],
            "pages": book.PAGE_LABELS, "roles": book.ROLE_LABELS,
            **({"seats": plans.seats(u["id"])} if plans.hosted() else {})}


def _my_member(uid: int) -> dict:
    u = _book_owner()
    m = system.user(uid)
    if not m or m.get("member_of") != u["id"]:
        raise HTTPException(404, "Üye yok")
    return m


@app.put("/api/book/rules")
def book_set_rules(body: BookRules, member: int | None = None):
    """Gizleme kuralları: member verilirse o üyeye özel, yoksa ortak (kendine özel kuralı olmayan herkes)."""
    from app import book

    _book_owner()
    if member is not None:
        _my_member(member)
    return book.set_rules(body.model_dump(exclude_unset=True), member)


@app.delete("/api/book/members/{uid}/rules")
def book_clear_member_rules(uid: int):
    """Üyenin kişiye özel kurallarını kaldır: ortak kurallara döner."""
    from app import book

    _my_member(uid)
    book.clear_rules(uid)
    return {"ok": True}


class BookMemberPatch(BaseModel):
    book_role: Literal["viewer", "editor", "full"]


@app.patch("/api/book/members/{uid}")
def book_patch_member(uid: int, body: BookMemberPatch):
    _my_member(uid)
    system.update_user(uid, book_role=body.book_role)
    return {"ok": True, "role": body.book_role}


@app.post("/api/book/invites")
def book_invite(body: InviteIn, request: Request):
    u = _book_owner()
    if plans.seats_left(u["id"]) == 0:
        raise HTTPException(409, f"Ortak defter en çok {plans.MAX_MEMBERS} üyelik; yer açmak için bir üyeyi ya da bekleyen "
                                 "daveti kaldır")
    return _invite_out(system.create_invite(body.note, body.days, member_of=u["id"], book_role=body.book_role), request)


@app.post("/api/book/invites/{code}/renew")
def book_renew_invite(code: str, request: Request):
    """Daveti yenile (bağlantı paylaşıldı ama kullanılmadı ya da süresi doldu): eski kod geçersiz, yeni kod ve süre."""
    u = _book_owner()
    inv = system.invite(code)
    if not inv or inv.get("member_of") != u["id"] or inv["used_at"]:
        raise HTTPException(404, "Davet yok ya da kullanılmış")
    return _invite_out(system.renew_invite(code), request)


@app.delete("/api/book/invites/{code}")
def book_delete_invite(code: str):
    u = _book_owner()
    inv = system.invite(code)
    if not inv or inv.get("member_of") != u["id"] or not system.delete_invite(code):
        raise HTTPException(404, "Davet yok ya da kullanılmış")
    return {"ok": True}


@app.delete("/api/book/members/{uid}")
def book_remove_member(uid: int):
    """Üyeyi çıkar (eklediği kayıtlar defterde kalır). Davetle yeni açılmış üyenin hesabı silinir; kendi ("Benim")
    defteri sahipsiz kalmasın diye users/_silinen altına taşınır, 30 gün sonra kendiliğinden silinir. Var olan hesabıyla
    katılmış üye (app/book_join.py) silinmez: yalnız defterden ayrılır, kendi defteri ve hesabı kalır."""
    from app import book_join

    u = _book_owner()
    m = system.user(uid)
    if not m or m.get("member_of") != u["id"]:
        raise HTTPException(404, "Üye yok")
    if book_join.joined_with_account(m):
        book_join.detach(m)
        return {"ok": True, "detached": True}
    before = plans.seat_holders(u["id"])
    deletion.admin_deleted(m, reason="member_removed")  # anonim silme kaydı (D12), yönetici silmesiyle aynı
    system.delete_user(uid)
    system.archive_user_data(m)
    plans.clear_seat(uid)
    plans.notify_seat_changes(u["id"], before - {uid})  # boşalan koltuğa sıradaki geçer
    return {"ok": True}


class SeatGiveIn(BaseModel):
    from_member: int  # koltuğu alınacak (koltuklu) üye


@app.post("/api/book/members/{uid}/seat")
def book_give_seat(uid: int, body: SeatGiveIn):
    """Sahip "Koltuk ver": koltuk bekleyen üyeye (uid) koltuklu bir üyenin koltuğu verilir; koltuğu alınan sıranın
    sonuna geçer (kendi aboneliği yoksa ücretsiz sınırlara düşer, ona bildirim gider)."""
    u = _book_owner()
    _my_member(uid)
    _my_member(body.from_member)
    before = plans.seat_holders(u["id"])
    try:
        plans.give_seat(u["id"], uid, body.from_member)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    plans.notify_seat_changes(u["id"], before)
    return {"ok": True, "seats": plans.seats(u["id"])}


def _join_user() -> dict:
    u = db.current_user()
    if not u or u.get("demo") or not u.get("id"):
        raise HTTPException(409, "Ortak deftere katılmak için hesabınla giriş yap")
    return system.user(u["id"]) or u  # oturumdaki kopya değil, güncel satır (member_of, plan)


@app.get("/api/book/join/{code}")
def book_join_preview(code: str, request: Request):
    """Hesabı açık kullanıcı davet bağlantısını açtı: kim davet ediyor, hangi yetkiyle, katılabilir mi (değilse neden).
    {valid, can_join, reason?, message?, owner?, owner_email?, role?, role_label?, expires_at?, own_subscription?}"""
    from app import book_join

    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    out = book_join.preview(code, _join_user())
    if out.get("reason") == "unknown":
        auth.record_fail(ip)  # kod tahmini yavaşlasın
    return out


@app.post("/api/book/join/{code}")
def book_join_accept(code: str, request: Request):
    """Daveti kabul et: sahibin defterine üye ol (davetteki yetkiyle; sahibin gizleme kuralları geçer). Kendi verin
    "Benim" defterinde kalır. {ok, owner, role, role_label, own_subscription}"""
    from app import book_join

    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    try:
        return book_join.accept(code, _join_user())
    except book_join.JoinError as e:
        if e.reason == "unknown":
            auth.record_fail(ip)
        raise HTTPException(e.status, e.message) from e


@app.post("/api/book/leave")
def book_leave():
    """Üye ortak defterden kendi isteğiyle ayrılır: ortak defteri artık göremez; "Benim" defteri hesabının defteri olur."""
    from app import book_join

    try:
        return book_join.leave(_join_user())
    except book_join.JoinError as e:
        raise HTTPException(e.status, e.message) from e


@app.post("/api/book/members/{uid}/reset-password")
def book_reset_member_password(uid: int):
    """Davetle defterde açılmış üyenin şifresini sıfırla (geçici şifre bir kez döner). Var olan hesabıyla katılmış üye
    (kendi "Benim" defteri ve verisi var) 403: sahip geçici şifreyle o hesaba girip kişinin kendi verisini ele
    geçirebilirdi; üye şifresini kendisi sıfırlar."""
    from app import book_join

    u = _book_owner()
    m = system.user(uid)
    if not m or m.get("member_of") != u["id"]:
        raise HTTPException(404, "Üye yok")
    if book_join.joined_with_account(m):
        raise HTTPException(403, "Bu üye kendi hesabıyla katıldı; şifresini kendisi sıfırlar (Şifremi unuttum).")
    temp = auth.temp_password()
    auth.set_password(uid, temp, must_change=True)
    from app import reset

    reset.notify_changed(m, by_admin=True)
    return {"temp_password": temp}


# ---------------------------------------------------------------- canlı demo

@app.get("/api/demo")
def demo_info():
    from app import demo_mode

    u = db.current_user()
    if not demo_mode.enabled() or not (u and u.get("demo")):
        raise HTTPException(404, "Demo değil")
    return {"expires_at": u["expires_at"], "ttl": demo_mode.TTL,
            "samples": [{"key": k, "label": v["label"], "kind": v["kind"]} for k, v in demo_mode.SAMPLES.items()]}


@app.get("/api/demo/stats")
def demo_stats(request: Request, days: int = 7):
    """Demo kullanım istatistikleri: sadece DEMO_STATS_KEY bilen (canlı uygulamanın yönetici paneli) okur."""
    import hmac

    from app import demo_mode

    key = os.environ.get("DEMO_STATS_KEY", "")
    if not demo_mode.enabled() or not key or not hmac.compare_digest(request.headers.get("x-stats-key", ""), key):
        raise HTTPException(404, errors.GENERIC[404])
    return demo_mode.stats(max(1, min(days, 365)))


def _demo_stats_fetch(days: int) -> dict | None:
    """Demo container'ının istatistiği (sunucu içinden, DEMO_STATS_KEY ile). Yapılandırılmamışsa None; ulaşılamazsa hata."""
    import httpx

    url, key = os.environ.get("DEMO_STATS_URL"), os.environ.get("DEMO_STATS_KEY")
    if not url or not key:
        return None
    r = httpx.get(url, params={"days": days}, headers={"x-stats-key": key}, timeout=10)
    r.raise_for_status()
    return r.json()


@app.get("/api/admin/demo-stats")
def admin_demo_stats(days: int = 7):
    """Yönetici: canlı demonun istatistikleri (demo container'ından, sunucu içinden çekilir)."""
    _require_admin()
    try:
        s = _demo_stats_fetch(days)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, errors.user_text(e, 502, "Demoya ulaşılamadı; biraz sonra tekrar dene.")) from e
    return {"configured": False} if s is None else {"configured": True, **s}


# ---------------------------------------------------------------- Ölçüm (dönüşüm hunisi; app/funnel.py)

class EventIn(BaseModel):
    name: str


@app.post("/api/events")
def track_event(body: EventIn, request: Request):
    """Uygulama olayı (beyaz liste: funnel.EVENTS). Yalnız günlük toplam yazılır; kişi, IP, cihaz kimliği yok.
    Kişi başı saatte funnel.EVENT_HOURLY (429). Demoda ve ölçümün kapalı olduğu sunucuda sayılmaz (counted: false)."""
    from app import funnel

    try:
        counted = funnel.record_event(db.current_user(), body.name, funnel.platform_of(request.headers))
    except ValueError as e:
        raise HTTPException(422, "Bilinmeyen olay") from e
    except funnel.RateLimited as e:
        raise HTTPException(429, "Çok fazla olay; biraz sonra.", headers={"Retry-After": "600"}) from e
    return {"ok": True, "counted": counted}


@app.get("/api/admin/funnel")
def admin_funnel(platform: str | None = None, weeks: int = 12):
    """Yönetici "Ölçüm": son haftalar ve toplam; platform web | ios | android (boş: hepsi). Demo sunucusu
    yapılandırılmışsa haftalık demo oturumları da (ulaşılamazsa demo_error, sayfa yine açılır)."""
    from app import funnel

    _require_admin()
    weeks = max(1, min(weeks, 52))
    demo, demo_error, configured = None, None, bool(os.environ.get("DEMO_STATS_URL") and os.environ.get("DEMO_STATS_KEY"))
    if configured:
        try:
            demo = (_demo_stats_fetch(weeks * 7) or {}).get("günlük") or {}
        except Exception as e:  # noqa: BLE001
            demo_error = errors.user_text(e, 502, "Demoya ulaşılamadı.")
    out = funnel.report(weeks=weeks, platform=platform, demo=demo)
    return {**out, "enabled": funnel.enabled(), "demo_configured": configured, "demo_error": demo_error}


@app.post("/api/demo/reset")
def demo_reset(response: Response):
    from app import demo_mode

    if not demo_mode.enabled():
        raise HTTPException(404, "Demo değil")
    response.delete_cookie(demo_mode.COOKIE, path="/")  # sonraki istek yeni ve temiz bir kopya açar
    return {"ok": True}


class WaitlistIn(BaseModel):
    email: str
    platform: str | None = None
    note: str | None = None
    tester: bool = False  # Android kapalı testine katılır (Play'de 12 kişi × 14 gün şartı)


@app.post("/api/demo/waitlist")
def demo_waitlist(body: WaitlistIn, request: Request):
    """Demo: kurulumsuz (hazır) sürüm açılınca haber ver listesine yazıl."""
    from app import demo_mode

    if not demo_mode.enabled():
        raise HTTPException(404, errors.GENERIC[404])
    try:
        new = demo_mode.join_waitlist(request.cookies.get(demo_mode.COOKIE) or "", body.email, body.platform, body.note, body.tester)
    except demo_mode.WaitlistError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    return {"ok": True, "new": new}


@app.post("/api/demo/sample/{key}")
@_heavy
def demo_sample(key: str):
    """Örnek ekstre / fiş: gerçek yükleme yolundan geçer (hazır okuyucu, genel okuyucu, yerel OCR)."""
    from app import demo_mode, undo
    from app.parsers import parse_text

    if not demo_mode.enabled() or key not in demo_mode.SAMPLES:
        raise HTTPException(404, "Örnek yok")
    s = demo_mode.SAMPLES[key]
    if s["kind"] == "receipt":  # demoda anahtar yok: yerel OCR (Tesseract)
        return _read_document(demo_mode.sample_image(key), "image/jpeg")
    st = parse_text(demo_mode.sample_text(key))
    if not st:
        raise HTTPException(422, "Örnek okunamadı")
    from app import demo_names

    demo_names.scrub_statement(st)  # gerçek banka / işyeri adı görünmez
    data = f"demo-sample:{key}".encode()
    res = {"filename": s["label"], "bank": st.bank, "card_no": st.card_no, "period_end": st.period_end.isoformat(),
           "total_debt": st.total_debt, "txn_count": len(st.transactions), "reconciliation_diff": st.reconciliation_diff(),
           "ai": False}
    if db.statement_exists(data, st):
        return [{**res, "status": "duplicate"}]
    with undo.batch(f"Ekstre: {s['label']}"):
        sid = db.save_statement(data, s["label"], st)
        linked = db.auto_link()
        db.auto_mark_paid()
    return [{**res, "status": "saved", "linked": linked, "statement_id": sid}]  # İlk bakış (first_look) demoda da


# ---------------------------------------------------------------- yüklemeyi geri al

@app.get("/api/imports")
def list_imports():
    from app import undo

    return undo.list_batches()


@app.post("/api/imports/{bid}/undo")
def undo_import(bid: str, force: bool = False):
    from app import undo

    try:
        return undo.undo(bid, force)
    except KeyError as e:
        raise HTTPException(404, "Yükleme bulunamadı") from e


# ---------------------------------------------------------------- kategorisizleri düzenle (app/uncategorized.py)

def _unc_month(month: str | None) -> str | None:
    if not month or month == "all":
        return None
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(422, "Dönem biçimi: month=YYYY-MM ya da month=all")
    return month


def _expense_categories() -> list[str]:
    return sorted(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories())
                  - {"Ödeme", "Kategorisiz", "Borç / Alacak", *INCOME_CATEGORIES})


@app.get("/api/uncategorized/groups")
def uncategorized_groups(month: str | None = None, sort: Literal["count", "total"] = "count"):
    """Kategorisiz kayıtlar işyerine/kişiye göre gruplu, her gruba ücretsiz öneri (geçmiş/kural/bilinen). Küme Özet'teki
    "N işlem kategorisiz" uyarısıyla aynı; month yoksa ya da "all" ise tüm zaman. ai: yapay zekâ önerisi açık mı, kalan hak.
    sort: count (en çok işlem önce) | total (en çok tutan önce). Grupta one_off: tek işlemli, tutarı one_off_max'ı
    aşmayan gider ("Tek seferlikleri Diğer yap"); renamable: kart işlemi var, adı düzeltilebilir."""
    from app import uncategorized

    m = _unc_month(month)
    gs = uncategorized.groups(m, sort)
    return {"month": m, "groups": gs, "count": sum(g["count"] for g in gs), "ai": uncategorized.ai_status(),
            "one_off_max": uncategorized.ONE_OFF_MAX, "ai_max": uncategorized.AI_MAX_KEYS}


class UncApplyIn(BaseModel):
    key: str = Field(min_length=3, max_length=300)
    category: str = Field(min_length=1, max_length=80)
    remember: bool = True
    month: str | None = None


@app.post("/api/uncategorized/apply")
def uncategorized_apply(body: UncApplyIn):
    """Gruptaki bütün kategorisiz kayıtlara kategori. remember: işyeri → kural (sonraki ekstre/döküm de bu kategoriye
    düşer), kişiye giden para → kişi kategorisi. Yanıttaki undo_id ile POST /api/uncategorized/undo geri alır."""
    from app import uncategorized

    cat = body.category.strip()
    if not cat or cat == "Kategorisiz":
        raise HTTPException(422, "Bir kategori seç")
    try:
        return uncategorized.apply(body.key, cat, body.remember, _unc_month(body.month))
    except KeyError as e:
        raise HTTPException(404, "Bu grupta kategorisiz kayıt kalmadı") from e


class UncItem(BaseModel):
    key: str = Field(min_length=3, max_length=300)
    category: str = Field(min_length=1, max_length=80)


class UncApplyManyIn(BaseModel):
    items: list[UncItem] = Field(min_length=1, max_length=500)
    remember: bool = True
    month: str | None = None


@app.post("/api/uncategorized/apply-many")
def uncategorized_apply_many(body: UncApplyManyIn):
    """Birden çok gruba kategori, TEK geri alma kaydıyla (önerilenlerin hepsi, çoklu seçim, tek seferlikler → Diğer).
    remember tek uygulamadakiyle aynı. Kategorisiz kaydı kalmamış anahtarlar atlanır (skipped). Hiçbiri yoksa 404.
    Yanıttaki undo_id ile POST /api/uncategorized/undo hepsini birden geri alır."""
    from app import uncategorized

    items = []
    for it in body.items:
        cat = it.category.strip()
        if not cat or cat == "Kategorisiz":
            raise HTTPException(422, "Bir kategori seç")
        items.append((it.key, cat))
    out = uncategorized.apply_many(items, body.remember, _unc_month(body.month))
    if not out["groups"]:
        raise HTTPException(404, "Bu gruplarda kategorisiz kayıt kalmadı")
    return out


class MerchantRenameIn(BaseModel):
    display: str = Field(max_length=60)
    key: str | None = Field(default=None, min_length=3, max_length=300)
    uid: str | None = Field(default=None, max_length=20)
    month: str | None = None


@app.post("/api/merchants/rename")
def merchant_rename(body: MerchantRenameIn):
    """Görünen işyeri adını düzelt (ör. ödeme aracısı ya da resmi unvan yerine marka). Ekstredeki ham açıklama
    değişmez; bu işyerinin bütün kart işlemleri yeni adla görünür ve gruplanır. key (Kategorisizler grubu) ya da uid
    (kart işlemi "c<id>") verilir. display boşsa düzeltme kaldırılır. Geri al: POST /api/uncategorized/undo."""
    from app import uncategorized

    if not body.key and not body.uid:
        raise HTTPException(422, "Grup ya da işlem gerekli")
    try:
        return uncategorized.rename(body.display, key=body.key, uid=body.uid, month=_unc_month(body.month))
    except KeyError as e:
        raise HTTPException(404, "Bu adla kart işlemi bulunamadı") from e


class UncUndoIn(BaseModel):
    undo_id: str = Field(min_length=1, max_length=40)


@app.post("/api/uncategorized/undo")
def uncategorized_undo(body: UncUndoIn):
    """Son "Uygula"yı geri al: kayıtların eski kategorisi, eklenen kural ya da kişi kategorisi geri gelir."""
    from app import uncategorized

    try:
        return uncategorized.undo_apply(body.undo_id)
    except KeyError as e:
        raise HTTPException(404, "Geri alınacak işlem bulunamadı") from e


class UncAiIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=200)
    month: str | None = None


@app.post("/api/uncategorized/ai-suggest")
@_heavy
def uncategorized_ai_suggest(body: UncAiIn):
    """Önerisi olmayan işyeri grupları için yapay zekâdan kategori (tek çağrı = 1 hak; hazır sürümde belge okuma hakkı,
    kendi sunucusunda ortak anahtarın kotası). Yalnız işyeri ADLARI gönderilir: tutar, tarih, kişi adı gitmez.
    Gemini anahtarı yoksa 404; hak doluysa 402. Yapay zekâ her çağrıldığında 1 hak: eskiden anahtar "gün + adlar"
    olduğundan aynı gün aynı adlarla tekrar sormak tekrar muafiyetine takılıp bedavaya yapay zekâ çağırıyordu."""
    import uuid

    from app import doc_reads, uncategorized

    if not receipt.enabled():
        raise HTTPException(404, "Yapay zekâ önerisi bu sunucuda kapalı")
    names = uncategorized.ai_names(body.keys, _unc_month(body.month))
    if not names:
        return {"suggestions": {}, "asked": 0, "ai": uncategorized.ai_status()}
    key = f"ai-kategori:{uuid.uuid4().hex}"  # her çağrı ayrı okuma (tekrar muafiyeti yok)
    try:
        doc_reads.check(key)  # hazır sürüm: hak doluysa sorulmaz (geçerse hak bu isteğe ayrılır)
    except doc_reads.QuotaError as e:
        raise QuotaFull(402, e) from e
    try:
        receipt._check_quota()  # kendi sunucusu: ortak anahtarın aylık kotası
    except receipt.ReceiptError as e:
        raise HTTPException(402, errors.user_text(e, 402)) from e
    try:
        out = uncategorized.ai_suggest(names, _expense_categories())
    except receipt.DailyLimitError as e:
        raise HTTPException(429, errors.user_text(e, 429)) from e
    except receipt.ReceiptError as e:
        raise HTTPException(502, errors.user_text(e, 502)) from e
    doc_reads.charge(key)
    return {"suggestions": out, "asked": len(names), "ai": uncategorized.ai_status()}


# ---------------------------------------------------------------- rules

class RuleIn(BaseModel):
    pattern: str = Field(min_length=1)
    category: str = Field(min_length=1)
    subcategory: str | None = Field(None, max_length=60)  # eşleşen işleme alt kategori de (isteğe bağlı)
    priority: int = 10


class RulePatch(BaseModel):
    pattern: str | None = None
    category: str | None = None
    subcategory: str | None = Field(None, max_length=60)
    priority: int | None = None


def _check_regex(p: str | None):
    """Kayıt ve denemede desen denetimi: geçersiz, çok uzun ya da felaket geri izlemeye açık (ReDoS) desen 422."""
    from app import saferegex

    if p is None:
        return
    if why := saferegex.problem(p):
        raise HTTPException(422, why)


@app.get("/api/rules")
def list_rules():
    from app import book

    return book.filter_rules(db.list_rules())  # ortak defter üyesi: gizli karttan öğrenilen kural görünmez


@app.post("/api/rules")
def create_rule(body: RuleIn):
    _check_regex(body.pattern)
    rid = db.add_rule(body.pattern, body.category, body.priority, _subcategory(body.category, body.subcategory))
    return {"id": rid, "recategorized": db.recategorize_all()}


@app.patch("/api/rules/{rule_id}")
def patch_rule(rule_id: int, body: RulePatch):
    _check_regex(body.pattern)
    data = body.model_dump(exclude_unset=True)
    _patch_subcategory(data, db.get_rule(rule_id))
    db.update_rule(rule_id, data)
    return {"ok": True, "recategorized": db.recategorize_all()}


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int):
    db.delete_rule(rule_id)
    return {"ok": True, "recategorized": db.recategorize_all()}


@app.get("/api/rules/test")
def test_rule(text: str, pattern: str | None = None):
    """pattern verilirse yalnız o desen denenir (eski biçim). Verilmezse sunucunun gerçek sırasıyla (öncelik, id) TÜM
    kurallar: ilk eşleşen kural (rule: {id, pattern, category, priority} | null) ve işlemin alacağı kategori — istemci
    kendi regex/normalize kopyasını çalıştırmasın."""
    from app import saferegex
    from app.categorize import UNCATEGORIZED
    from app.textnorm import normalize

    n = normalize(text[:saferegex.MAX_TEXT])
    if pattern is not None:
        _check_regex(pattern)
        return {"normalized": n, "match": bool(saferegex.search(pattern, n))}
    from app import book

    for r in book.filter_rules(db.list_rules()):
        if saferegex.search(r["pattern"], n):
            return {"normalized": n, "match": True, "rule": r, "category": r["category"]}
    return {"normalized": n, "match": False, "rule": None, "category": UNCATEGORIZED}


# ---------------------------------------------------------------- receipt

def _unreadable(doc: dict) -> bool:
    """Fiş okunamadı: toplam yok ya da 0 ve tutarlı hiçbir kalem yok (bulanık, uzaktan, kadraj dışı fotoğraf). Ölçüm
    04.11.2026: okunamayan fotoğrafta model uydurmuyor, toplamı 0/boş döndürüyor. Hak düşmez; istemci yeniden çektirir."""
    if doc.get("type") not in (None, "receipt"):
        return False
    t = doc.get("total")
    return not (isinstance(t, (int, float)) and t > 0) and not any(
        isinstance(i.get("amount"), (int, float)) and i["amount"] > 0 for i in doc.get("items") or [])


UNREADABLE_MSG = "Fotoğraf net değil, fişi yakından ve düz çek."


@app.post("/api/receipts/parse")
@_heavy
def parse_receipt(file: UploadFile = File(...)):
    """Fiş fotoğrafını oku, kaydetmeden önce kullanıcıya önizleme döndür."""
    data = _file_bytes(file)
    mime = file.content_type or "image/jpeg"
    if not mime.startswith("image/"):
        raise HTTPException(422, "Sadece fotoğraf yüklenebilir")
    from app import doc_cache, doc_reads

    original = data
    data, mime, photo = _prepare_photo(data, mime)
    if (hit := doc_cache.get(original, doc_cache.FIS)) is not None:  # aynı fotoğraf bu defterde okunmuştu
        return _cached_receipt(hit, photo)
    cats = sorted(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories()) - {"Ödeme", "Kategorisiz"})
    try:
        doc_reads.check(original, doc_reads.FIS)  # hazır sürüm: okuma hakkı doluysa okunmaz
    except doc_reads.QuotaError as e:  # fotoğraf okunmadan saklanır: elle girilen gidere bağlanır
        raise QuotaFull(502, e, receipt_file=_store_receipt(photo)) from e
    doc_reads.forget(original)  # önbellekte yok: yeniden okunursa yeniden sayılır
    try:
        out = receipt.parse_receipt(data, mime, cats)
    except doc_reads.QuotaError as e:  # yönetici kısıtlaması (yapay zekâ kapalı): fotoğraf saklanır, elle girilir
        raise QuotaFull(502, e, receipt_file=_store_receipt(photo)) from e
    except receipt.ReceiptError as e:
        raise HTTPException(502, errors.user_text(e, 502)) from e
    if _unreadable(out):  # okunamadı: yeniden okuma denenmez; istemci yeniden çektirir
        _mark_unreadable(out, UNREADABLE_MSG)
    elif charged := doc_reads.charge(original, doc_reads.FIS):
        out["charged"] = charged  # {daily, extra}: istemci "ek paketten N okuma kullanıldı" der
    out["check"] = receipt_check.check(out.get("items") or [], out.get("total"))
    receipt_verify.apply(out, None)  # bu uçta telefon metni yok: total_check null
    if not out.get("unreadable"):
        _auto_reread(out, data, mime)
        _digit_evidence(out.get("check"), out.get("items") or [], None, out.get("vat_lines"), out.get("merchant"))
    out["receipt_file"] = _store_receipt(photo)
    if not out.get("unreadable"):
        doc_cache.put(original, doc_cache.FIS, out, out["receipt_file"])
    _add_price_alerts(out)  # önbelleğe girmez: geçmiş her okumada güncel
    if warn := db.take_storage_warning():
        out["storage_warning"] = warn
    return out


def _add_price_alerts(doc: dict, exclude_entry: int | None = None, on: str | None = None, market: bool = True) -> None:
    """check.price_alerts: kendi son fiyatına göre zam (app/price_alerts.py; fiyat takibi paketinde). on: kayıtlı fişin
    günü (o günden önceki alımlarla karşılaştırılır; okumada bugün). market: ayrıca check.market_notes (piyasadan pahalı
    kalemler, app/market_ref.py receipt_notes; fiş günü: on, yoksa fişteki tarih). Hata okumayı bozmaz."""
    import logging

    from app import market_ref, price_alerts

    if not isinstance(doc.get("check"), dict):
        return
    try:
        day = date.fromisoformat(str(on)[:10]) if on else None
        doc["check"]["price_alerts"] = price_alerts.receipt_alerts(doc.get("items") or [], doc.get("merchant"),
                                                                   exclude_entry, day)
    except Exception:  # noqa: BLE001
        logging.getLogger("hesapkitap.price_alerts").warning("fiyat uyarısı hesaplanamadı", exc_info=True)
        doc["check"]["price_alerts"] = []
    if not market:
        return
    try:  # yetenek market_notes
        doc["check"]["market_notes"] = market_ref.receipt_notes(doc.get("items") or [], doc["check"], doc.get("merchant"),
                                                                on or doc.get("date"), doc.get("legal_name"))
    except Exception:  # noqa: BLE001
        logging.getLogger("hesapkitap.market_ref").warning("piyasa notu hesaplanamadı", exc_info=True)
        doc["check"]["market_notes"] = []


def _mark_unreadable(doc: dict, msg: str) -> None:
    """Okunamadı: kişi başı günün ilk doc_reads.FREE_UNREADABLE_PER_DAY denemesi ücretsiz, sonrası 1 hak (mesaja
    eklenir, charged döner)."""
    from app import doc_reads

    doc.update(unreadable=True, message=msg)
    if charged := doc_reads.charge_unreadable():
        doc.update(charged=charged, message=f"{msg} {doc_reads.UNREADABLE_CHARGED_MSG}")


def _cached_receipt(hit: dict, photo: bytes | None) -> dict:
    """Önbellekten fiş: yapay zekâ yok, hak düşmez; fotoğraf bu defterde yeniden saklanır (silinmiş olabilir)."""
    from app import doc_cache

    out = doc_cache.hit(hit)
    if "receipt_file" in out or photo:
        out["receipt_file"] = _store_receipt(photo)
    _add_price_alerts(out)
    if warn := db.take_storage_warning():
        out["storage_warning"] = warn
    return out


# ---------------------------------------------------------------- saklanan dosyalar: fotoğraf hazırlama ve güvenli yanıt

def _prepare_photo(data: bytes, mime: str) -> tuple[bytes, str, bytes | None]:
    """Fotoğrafı küçült, yönünü düzelt, EXIF/konumu sil (app/images.py). (okunacak veri, türü, saklanacak JPEG|None).
    Açılamazsa (ör. HEIC) özgün veriyle okunur, saklanmaz."""
    from app import images

    if not mime.startswith("image/"):
        return data, mime, None
    small = images.prepare(data)
    if small is None:
        import logging

        logging.getLogger("ekstre.storage").warning("fotoğraf açılamadı (%s, %d bayt): okunuyor, saklanmıyor", mime, len(data))
        return data, mime, None
    return small, "image/jpeg", small


def _store_receipt(photo: bytes | None) -> str | None:
    """Hazırlanmış fiş fotoğrafını sakla; istemciye dönen yalnız içerik adıdır (<sha256>.jpg), depo adresi/anahtarı değil."""
    if not photo:
        return None
    name = f"{db.file_hash(photo)}.jpg"
    try:
        return name if db.store_file("receipts", name, photo, "image/jpeg") else None
    except Exception:  # noqa: BLE001 - depo geçici olarak yoksa okuma yine sonuç versin
        import logging

        logging.getLogger("ekstre.storage").exception("fiş fotoğrafı saklanamadı")
        return None


SAFE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


def _safe_filename(name: str) -> tuple[str, str]:
    """(ASCII yedeği, RFC 5987 UTF-8 hali) — tırnak, satır sonu, yol ayıracı yok."""
    from urllib.parse import quote

    clean = re.sub(r'[\x00-\x1f\x7f"\\/;:*?<>|]+', "_", name).strip(" .") or "dosya"
    clean = clean[:120]
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    ascii_ = re.sub(r"[^A-Za-z0-9._-]+", "_", clean.translate(tr)).strip("_") or "dosya"
    return ascii_, quote(clean, safe="")


def file_response(data: bytes, ctype: str, filename: str, inline: bool = False) -> Response:
    """Saklanan dosyayı akıt: izinli türler dışında her şey indirme (octet-stream). Önbelleğe alınmaz."""
    if ctype not in SAFE_TYPES:
        ctype, inline = "application/octet-stream", False
    a, u = _safe_filename(filename)
    return Response(data, media_type=ctype, headers={
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename=\"{a}\"; filename*=UTF-8''{u}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


def _stored_file(kind: str, name: str | None, filename: str, inline: bool = False) -> Response:
    """Açık defterin dosyası (anahtar oturumdan + kaydın kendi alanından türetilir). Yoksa 404, depo hatası 503."""
    from app import storage

    if not storage.valid_name(kind, name):
        raise HTTPException(404, "Dosya yok")
    try:
        data = db.read_file(kind, name)
    except storage.StorageError as e:
        raise HTTPException(503, "Dosya şu an alınamadı") from e
    if data is None:
        raise HTTPException(404, "Dosya yok")
    ext = os.path.splitext(name)[1].lower()
    return file_response(data, storage.content_type(name), filename + ("" if filename.lower().endswith(ext) else ext), inline)


# ---------------------------------------------------------------- tasarruf

class SubState(BaseModel):
    merchant: str = Field(min_length=1)
    status: Literal["aktif", "iptal", "dusuruldu"]
    changed_at: date | None = None
    new_amount: Money | None = Field(default=None, ge=0)  # paket düşürüldüyse yeni aylık tutar
    note: str | None = None


class GoalIn(BaseModel):
    kind: Literal["category", "merchant"]
    name: str = Field(min_length=1)
    baseline: Money = Field(ge=0)
    target: Money = Field(ge=0)
    start_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    note: str | None = None


class GoalPatch(BaseModel):
    target: Money | None = Field(default=None, ge=0)
    baseline: Money | None = Field(default=None, ge=0)
    note: str | None = None
    active: bool | None = None


@app.get("/api/savings")
def savings():
    subs = finance.subscriptions()
    goals = finance.goals_review()
    cancelled = sum(x["saving"] for x in subs if x["status"] in ("iptal", "dusuruldu"))
    goal_saving = sum(g["monthly_saving"] for g in goals if g["active"])
    ref = finance.reference_month()
    summ = finance.summary(ref)
    spend_now = {k: {g["name"]: g["amount"] for g in summ[k]} for k in ("by_category", "by_merchant")}
    return {"subscriptions": subs, "goals": goals,
            "monthly_saving": round(cancelled + goal_saving, 2),
            "cancelled_monthly": round(cancelled, 2), "goal_monthly": round(goal_saving, 2),
            "last_month_spend": spend_now, "reference_month": ref}


@app.put("/api/subscriptions")
def put_subscription(body: SubState):
    if body.status == "dusuruldu" and body.new_amount is None:
        raise HTTPException(422, "Yeni paket tutarı gerekli")
    db.set_subscription_state(body.merchant, body.status, (body.changed_at or date.today()).isoformat(), body.note,
                              body.new_amount)
    return {"ok": True}


# ---------------------------------------------------------------- uygulama mağazası abonelikleri (app/store_subs.py)

@app.get("/api/subscriptions/catalog")
def subscriptions_catalog():
    """Hazır abonelik kataloğu (yerli + global): {items [{id, name, glyph, color, fg, group, stores}], groups}.
    Logo yok: renkli baş harf. Eşleşme kalıpları sunucuda kalır (app/sub_catalog.py)."""
    from app import sub_catalog

    return sub_catalog.public()


@app.get("/api/subscriptions/apps")
def subscriptions_apps():
    """App Store / Google Play abonelikleri (yetenek store_subscriptions): {available, items [aday: id, title, name|null,
    store, store_label, logo, status abonelik|aday|degil|iptal, cycle aylik|yillik|null, cycle_text, amount, monthly,
    yearly, counted, next_date, next_text, first_text, price_up, guesses [Olabilir], charges, manage, pending …], other
    (elle eklenen, mağaza dışı), totals {monthly, yearly, count, text}, candidates, help}."""
    from app import store_subs

    return store_subs.overview()


class StoreSubAct(BaseModel):
    action: Literal["adlandir", "abonelik", "degil", "iptal", "aktif", "bilmiyorum", "sifirla"]
    catalog_id: str | None = Field(None, max_length=40)
    name: str | None = Field(None, max_length=60)
    cycle: Literal["aylik", "yillik"] | None = None
    when: date | None = Field(None, alias="date")  # iptal günü (varsayılan bugün)


@app.put("/api/subscriptions/apps/{sid}")
def subscriptions_app_act(sid: str, body: StoreSubAct):
    """Adaya karar: adlandır (katalog ya da yazılan ad), abonelik (dönem verilebilir), değil (tek seferlik satın alma),
    iptal (iptal ettim), aktif (geri al), bilmiyorum (fiyattan tahmin önerme), sıfırla. Dönen: güncel aday."""
    from app import store_subs

    try:
        store_subs.act(sid, body.action, body.catalog_id, body.name, body.cycle,
                       body.when.isoformat() if body.when else None)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"ok": True, "item": store_subs.find(sid)}


class ManualSubIn(BaseModel):
    catalog_id: str | None = Field(None, max_length=40)
    name: str | None = Field(None, max_length=60)
    plan: str | None = Field(None, max_length=60)
    amount: Money = Field(gt=0, le=1_000_000)
    cycle: Literal["aylik", "yillik"] = "aylik"
    next_date: date
    store: Literal["apple", "google", "diger"] = "diger"


class SourcePatch(BaseModel):
    catalog_id: str | None = Field(None, max_length=40)
    name: str | None = Field(None, min_length=1, max_length=60)
    plan: str | None = Field(None, max_length=60)
    amount: Money | None = Field(None, gt=0, le=1_000_000)
    cycle: Literal["aylik", "yillik"] | None = None
    next_date: date | None = None
    store: Literal["apple", "google", "diger"] | None = None


@app.post("/api/subscriptions/apps/manual")
def subscriptions_manual(body: ManualSubIn):
    """Elle abonelik (katalogdan seç ya da adını yaz): ekstreye düşünce aynı mağaza + tutar + günle adayla birleşir."""
    from app import store_subs, sub_catalog

    cat = sub_catalog.get(body.catalog_id)
    if body.catalog_id and not cat:
        raise HTTPException(422, "Katalogda yok")
    name = (cat or {}).get("name") or (body.name or "").strip()
    if not name:
        raise HTTPException(422, "Bir ad seç ya da yaz")
    sid, _new = store_subs.save_source("elle", {"name": name, "plan": body.plan, "catalog_id": body.catalog_id,
                                                "store": body.store, "amount": body.amount, "cycle": body.cycle,
                                                "date": body.next_date, "active": True}, date.today())
    return {"ok": True, "source_id": sid, "item": next((i for i in store_subs.items() if i.get("source_id") == sid), None)}


@app.patch("/api/subscriptions/apps/sources/{src_id}")
def subscriptions_source_patch(src_id: int, body: SourcePatch):
    """Elle eklenen ya da ekran görüntüsünden gelen aboneliği düzelt (ad, tutar, dönem, sonraki gün, mağaza)."""
    from app import store_subs

    data = body.model_dump(exclude_unset=True)
    if "next_date" in data:
        data["renews"] = data.pop("next_date")
    try:
        ok = store_subs.update_source(src_id, data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not ok:
        raise HTTPException(404, "Abonelik bulunamadı")
    return {"ok": True}


@app.delete("/api/subscriptions/apps/sources/{src_id}")
def subscriptions_source_delete(src_id: int):
    from app import store_subs

    if not store_subs.delete_source(src_id):
        raise HTTPException(404, "Abonelik bulunamadı")
    return {"ok": True}


SUB_SCREEN_UNREADABLE = "Ekran görüntüsünde abonelik listesi bulunamadı. Mağazanın Abonelikler sayfasının görüntüsü mü?"


@app.post("/api/subscriptions/apps/screenshot")
@_heavy
def subscriptions_screenshot(files: list[UploadFile] = File(...), ocr_text: list[str] = Form(default=[]),
                             store: str | None = Form(None)):
    """Abonelik listesi ekran görüntüleri (1–5; iPhone Ayarlar → Abonelikler, Play Store → Ödemeler ve abonelikler →
    Abonelikler). Her görüntü 1 okuma hakkı (okunamayan: günün ilk denemeleri ücretsiz, aynı görüntü tekrar: önbellek).
    ocr_text[i]: telefonun o görüntüden okuduğu yazı (varsa önce kural okuması, yapay zekâsız). Okunan satırlar adaylarla
    mağaza + tutar + günden eşleşir, eşleşmeyen etkin olanlar "henüz ekstreye düşmemiş" olarak eklenir.
    Yanıt: {rows [{name, plan, amount, cycle, date, active, match {id, pending, title}|null}], matched, pending, skipped,
    message, read [{file, source metin|yapay_zeka|onbellek|null, rows, unreadable?}], charged?}"""
    from app import doc_cache, doc_reads, store_screens, store_subs

    if not 1 <= len(files) <= 5:
        raise HTTPException(422, "En çok 5 ekran görüntüsü")
    st = store if store in ("apple", "google") else None
    today = date.today()
    found: list[dict] = []
    read: list[dict] = []
    charged = None
    for idx, f in enumerate(files):
        data = _file_bytes(f)
        mime = f.content_type or ""
        if not mime.startswith("image/"):
            raise HTTPException(422, "Yalnız ekran görüntüsü (fotoğraf) yüklenebilir")
        limits.check_size(data, f.filename)
        text = (ocr_text[idx] if idx < len(ocr_text) else "").replace("\r\n", "\n").strip()[:receipt.MAX_OCR_CHARS]
        hit = doc_cache.get(data, "abonelik")
        if hit is not None:
            rows = hit.get("rows") or []
            read.append({"file": f.filename, "source": "onbellek", "rows": len(rows)})
            found += rows
            continue
        try:
            doc_reads.check(data)
        except doc_reads.QuotaError as e:
            raise QuotaFull(502, e) from e
        parsed = store_screens.parse_screen_text(text, today, st) if text else {"items": []}
        source = "metin" if parsed["items"] else None
        if not parsed["items"] and receipt.enabled():
            img, img_mime, _photo = _prepare_photo(data, mime)
            try:
                doc_reads.forget(data)
                raw, _meta = receipt._gemini_json(store_screens.SCREEN_PROMPT.format(today=today.isoformat())
                                                  + (f"\nTelefonun okuduğu yazı (yardımcı):\n{text[:6000]}" if text else ""),
                                                  img, img_mime, max_output_tokens=2000, kind="belge")
            except doc_reads.QuotaError as e:
                raise QuotaFull(502, e) from e
            except receipt.ReceiptError as e:
                raise HTTPException(502, errors.user_text(e, 502)) from e
            parsed = store_screens.from_ai(raw, today, st)
            source = "yapay_zeka"
        rows = [{**x, "date": x["date"].isoformat() if isinstance(x.get("date"), date) else x.get("date"),
                 "store": x.get("store") or parsed.get("store") or st} for x in parsed["items"]]
        if not rows:
            note = {"file": f.filename, "source": source, "rows": 0, "unreadable": True}
            if source == "yapay_zeka":
                _mark_unreadable(note, SUB_SCREEN_UNREADABLE)
            read.append(note)
            continue
        if c := doc_reads.charge(data):
            charged = c
        doc_cache.put(data, "abonelik", {"rows": rows})
        read.append({"file": f.filename, "source": source, "rows": len(rows)})
        found += rows
    res = store_subs.ingest(found, "ekran", today)
    out = {**res, "message": store_subs.summary_text(res) if found else SUB_SCREEN_UNREADABLE, "read": read}
    if charged:
        out["charged"] = charged
    return out


@app.post("/api/goals")
def create_goal(body: GoalIn):
    return {"id": db.create_goal(body.model_dump())}


@app.patch("/api/goals/{gid}")
def patch_goal(gid: int, body: GoalPatch):
    data = body.model_dump(exclude_unset=True)
    if "active" in data:
        data["active"] = int(data["active"])
    db.update_goal(gid, data)
    return {"ok": True}


@app.delete("/api/goals/{gid}")
def delete_goal(gid: int):
    db.delete_goal(gid)
    return {"ok": True}


# ---------------------------------------------------------------- bütçeler

class BudgetIn(BaseModel):
    category: str = Field(min_length=1)
    monthly_limit: Money | None = Field(default=None, ge=0)  # boş/0 = kaldır


@app.get("/api/budgets")
def list_budgets(month: str | None = None):
    m = month or finance.current_period()
    # öneri için son ekstre ayının kategori harcaması
    last = {g["name"]: g["amount"] for g in finance.category_spend(finance.reference_month())}  # tam özet gerekmez
    status = finance.budgets(m)
    # ortak bütçe (Aile): her bütçede kim ne kadar harcadı — yalnız ortak defterde ve paket izin veriyorsa
    if status and (ppl := _shared_people()) and plans.allows(db.current_user(), "aile_rapor"):
        split = finance.budget_split(m, ppl)
        for b in status:
            b["by_person"] = split.get(b["category"], [])
    return {"limits": db.list_budgets(), "status": status, "last_month": last, "totals": finance.budgets_totals(status)}


@app.put("/api/budgets")
def put_budget(body: BudgetIn):
    limits = db.list_budgets()
    u = db.current_user()
    if body.monthly_limit and body.category not in limits and not plans.allows(u, "butce") \
            and len(limits) >= plans.FREE_BUDGETS:
        raise HTTPException(402, detail=str(plans.PlanRequired("butce")), headers={"X-Plan-Required": "butce"})
    db.set_budget(body.category, body.monthly_limit)
    return {"ok": True}


# ---------------------------------------------------------------- ödemeler + faturalar

class PayIn(BaseModel):
    amount: Money = Field(ge=0)
    paid_at: date
    payment: Payment | None = None  # sadece fatura için


@app.get("/api/payables")
def payables(totals: bool = False, days: int | None = Query(None, ge=0, le=366)):
    """Ödenecekler (liste; eski istemciler için değişmedi). totals=1: {"items": [...], "totals": {...}}: istemci toplamı
    kendisi hesaplamasın (finance.payables_totals). Kalemde in_total: toplama girer mi; rolled_into: kalanı bu id'li
    son ekstreye devretmiş ödenmemiş eski ekstre (gecikmiş bilgisi). days=N (totals'ı da açar): totals.window =
    vadesi N gün içinde ya da geçmiş, toplama giren kalemlerin kalanı (başlık toplamı; tahmin ayrı: window.estimated)."""
    items = finance.payables()
    if totals or days is not None:
        return {"items": items, "totals": finance.payables_totals(items=items, days=days)}
    return items


class StatementPayIn(PayIn):
    account_id: int | None = None  # ödemenin çıktığı banka/nakit hesabı (isteğe bağlı)


@app.post("/api/statements/{sid}/pay")
def pay_statement(sid: int, body: StatementPayIn):
    """Ekstre ödendi / ödemeyi düzelt. account_id: X → o hesaptan çıkan kart ödemesi kaydı (gider değil, bakiye düşer);
    null → bağlı elle kayıt kaldırılır; anahtar yok → bağlı elle kaydın hesabı korunur, tutar/tarih güncellenir. Dökümden
    gelen kayda yazılmaz (tutar farklıysa warning). Dönüş: {ok, card_payment_id, linked, link, warning?}."""
    if not any(x["id"] == sid for x in db.list_statements()):
        raise HTTPException(404, "Ekstre bulunamadı")
    _visible_or_404("account", body.account_id)
    acc = body.account_id if "account_id" in body.model_fields_set else db._KEEP  # anahtar yok: bağlı hesabı koru
    try:
        r = db.mark_statement_paid(sid, body.amount, body.paid_at.isoformat(), acc)
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    return {"ok": True, **r}


@app.delete("/api/statements/{sid}/pay")
def unpay_statement(sid: int):
    db.mark_statement_paid(sid, None, None)
    return {"ok": True}


class BillIn(BaseModel):
    provider: str = Field(min_length=1)
    description: str | None = None
    amount: Money = Field(ge=0)
    due_date: date
    category: str | None = "Faturalar"
    note: str | None = None
    file_name: str | None = None
    # belge ödeme dekontuysa ya da kullanıcı "ödendi" dediyse: fatura ödenmiş olarak açılır (gider kaydıyla, /pay gibi)
    paid_at: date | None = None
    payment: Payment | None = None
    spender: str | None = Field(None, max_length=20)  # kim harcadı (ortak defter; ödenmiş açılan faturanın gider kaydı)


class BillPatch(BaseModel):
    provider: str | None = None
    description: str | None = None
    amount: Money | None = Field(default=None, ge=0)
    due_date: date | None = None
    category: str | None = None
    note: str | None = None


BILL_TYPES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
              "image/heic": ".heic"}


@app.post("/api/bills/parse")
@_heavy
def parse_bill(file: UploadFile = File(...), transfer_check: bool = Form(False)):
    """Fatura PDF/fotoğrafını sakla ve oku; kullanıcı onaylayınca /api/bills ile kaydedilir.
    Yanlış seçim koruması (yetenek dekont_parse): transfer_check=1 (yeni istemci) ve PDF metni havale/EFT dekontuysa
    fatura okunmaz (yapay zekâ yok, hak düşmez) → {looks_like_transfer: true}. Okunan faturada da looks_like_transfer
    (model doc_kind "transfer" dedi ya da metin dekont; ek çağrı yok). "Yine de fatura olarak ekle": transfer_check'siz."""
    data = _file_bytes(file)
    mime = file.content_type or ""
    if mime not in BILL_TYPES:
        raise HTTPException(422, "Sadece PDF veya fotoğraf yüklenebilir")
    from app import dekont_parse, doc_reads

    if mime == "application/pdf" and transfer_check and dekont_parse.is_transfer(_pdf_text(data)):
        return {"looks_like_transfer": True, "source": "metin", "provider": None, "amount": None, "file_name": None}
    read, read_mime, photo = _prepare_photo(data, mime)
    name = db.save_bill_file(photo, ".jpg") if photo else db.save_bill_file(data, BILL_TYPES[mime])
    from app import doc_cache

    if (hit := doc_cache.get(data, doc_cache.FATURA)) is not None:  # aynı fatura bu defterde okunmuştu
        warn = db.take_storage_warning()
        return {**doc_cache.hit(hit), "file_name": name, **({"storage_warning": warn} if warn else {})}
    try:
        if receipt.enabled():
            doc_reads.check(data)  # hazır sürüm: hak doluysa dosya saklanır ama okunmaz (elle girilir)
            doc_reads.forget(data)  # önbellekte yok: yeniden okunursa yeniden sayılır
        parsed = receipt.parse_bill(read, read_mime) if receipt.enabled() else {}
    except receipt.ReceiptError as e:
        parsed = {"error": errors.user_text(e, 502), **(e.fields() if isinstance(e, doc_reads.QuotaError) else {})}
    if parsed and "error" not in parsed:
        if charged := doc_reads.charge(data):
            parsed["charged"] = charged
        doc_cache.put(data, doc_cache.FATURA, {k: v for k, v in parsed.items() if k != "file_name"})
    warn = db.take_storage_warning()
    return {**parsed, "file_name": name, **({"storage_warning": warn} if warn else {})}


# ---------------------------------------------------------------- havale / EFT dekontu (app/dekont_parse.py)

def _pdf_text(data: bytes) -> str | None:
    """PDF metin katmanı (bilinen PDF şifreleriyle de denenir); yoksa None. Yapay zekâ yok, hak düşmez."""
    for pw in [None, *db.pdf_passwords()]:
        if t := _try_text(data, pw):
            return t
    return None


@app.post("/api/documents/classify")
@_heavy
def classify_document(file: UploadFile = File(...)):
    """Paylaş seçim sayfası için öneri (yetenek dekont_parse): yalnız PDF metni, yapay zekâsız, hak düşmez, dosya
    saklanmaz. {suggest: "dekont" | "ekstre" | null}. Fotoğrafta (metin yok) null."""
    from app import dekont_parse

    data = _file_bytes(file)
    limits.check_size(data, file.filename)
    if (file.content_type or "") != "application/pdf" and not (file.filename or "").lower().endswith(".pdf"):
        return {"suggest": None}
    return {"suggest": dekont_parse.classify_text(_pdf_text(data))}


def _store_dekont_pdf(data: bytes) -> str | None:
    """PDF dekontu fiş fotoğraflarının yanında sakla (receipts/<sha256>.pdf); kayda receipt_file olarak bağlanır."""
    name = f"{db.file_hash(data)}.pdf"
    try:
        return name if db.store_file("receipts", name, data, "application/pdf") else None
    except Exception:  # noqa: BLE001 - depo geçici olarak yoksa okuma yine sonuç versin
        import logging

        logging.getLogger("ekstre.storage").exception("dekont dosyası saklanamadı")
        return None


DEKONT_UNREADABLE = "Dekont okunamadı. Fotoğrafı yakından ve düz çek ya da bilgileri elle gir."


@app.post("/api/dekont/parse")
@_heavy
def parse_dekont(file: UploadFile = File(...), ocr_text: str | None = Form(None)):
    """Havale/EFT/FAST dekontu (fotoğraf ya da PDF) → Transfer formu taslağı (kaydetmez). PDF metni varsa önce kural
    okuması (yapay zekâsız), değilse/yetmezse yapay zekâ (kısa istem). Okunan yeni belge 1 hak; okunamadıysa düşmez.
    Yanıt: {type: "dekont", direction: giden|gelen|null, amount, date, counterparty, counterparty_known, iban_last4,
    bank, description, transfer_type, ref, account_hint, own_transfer, parties {sender, receiver: {name, iban_last4,
    bank, is_me}}, duplicate? {entry_id, message}, debt_match? {id, person, direction, remaining, amount, full},
    dekont_file, source: metin|yapay_zeka, unreadable?, message?, charged?}. IBAN tam dönmez (son 4 hane)."""
    from app import dekont, dekont_parse, doc_reads

    data = _file_bytes(file)
    mime = file.content_type or ""
    if mime not in BILL_TYPES:
        raise HTTPException(422, "Sadece PDF veya fotoğraf yüklenebilir")
    limits.check_size(data, file.filename)
    text_hint = (ocr_text or "").strip()
    if len(text_hint) > receipt.MAX_OCR_CHARS:
        raise HTTPException(422, "Belgeden çıkarılan metin çok uzun")
    read, read_mime, photo = _prepare_photo(data, mime)
    dfile = _store_receipt(photo) if photo else _store_dekont_pdf(data) if mime == "application/pdf" else None
    from app import doc_cache

    hit = doc_cache.get(data, doc_cache.DEKONT)  # yapay zekâyla okunmuş alanlar (kişi/hesap eşleşmesi her seferinde)
    if hit is None:
        try:
            doc_reads.check(data)  # hazır sürüm: hak doluysa okunmaz; dosya saklıdır, elle girilen transfere bağlanır
        except doc_reads.QuotaError as e:
            raise QuotaFull(502, e, dekont_file=dfile) from e
    text = _pdf_text(data) if mime == "application/pdf" and hit is None else None
    fields, source = (hit["fields"], hit.get("source")) if hit is not None else (None, None)
    if hit is None and text and (dekont_parse.is_transfer(text) or dekont.is_dekont(text)):
        try:
            fields, source = dekont_parse.fields_from_text(text), "metin"
        except ValueError:
            fields = None
        if fields and not (fields["sender_name"] or fields["receiver_name"]) and receipt.enabled():
            fields = None  # kişiler okunamadı: yapay zekâ dener
    if fields is None:
        if not receipt.enabled():
            fields, source = {}, None
        else:
            try:
                doc_reads.forget(data)  # yapay zekâ okuyacak, önbellekte yok: yeniden sayılır
                fields = dekont_parse.fields_from_ai(read, read_mime, text_hint or (text or "")[:6000] or None)
                source = "yapay_zeka"
            except doc_reads.QuotaError as e:  # yönetici kısıtlaması (yapay zekâ kapalı)
                raise QuotaFull(502, e, dekont_file=dfile) from e
            except receipt.ReceiptError as e:
                raise HTTPException(502, errors.user_text(e, 502)) from e
    out = dekont_parse.resolve(fields)
    out.update(source=source, dekont_file=dfile)
    if fields.get("not_transfer"):
        out["not_transfer"] = True  # model: bu bir transfer dekontu değil (istemci uyarır)
    ai = source == "yapay_zeka"
    if not (isinstance(out.get("amount"), (int, float)) and out["amount"] > 0 and out.get("date")):
        if ai:  # yapay zekâ okuyamadı: günün ilk denemeleri ücretsiz (_mark_unreadable)
            _mark_unreadable(out, DEKONT_UNREADABLE)
        else:  # metin okuması (yapay zekâ yok): hak düşmez
            out.update(unreadable=True, message=DEKONT_UNREADABLE)
    elif hit is not None:
        out["cached"] = True  # önbellekten: hak düşmez
    elif charged := doc_reads.charge(data):
        out["charged"] = charged
    if ai and hit is None and not out.get("unreadable"):
        doc_cache.put(data, doc_cache.DEKONT, {"fields": fields, "source": source}, dfile)
    if warn := db.take_storage_warning():
        out["storage_warning"] = warn
    return out


class DekontIn(BaseModel):
    date: date
    amount: Money = Field(gt=0)
    direction: Literal["giden", "gelen"]
    counterparty: str = Field(min_length=1, max_length=80)
    account_id: int | None = None
    description: str | None = Field(None, max_length=200)  # havale mesajı / açıklama: kayıt açıklamasında korunur
    note: str | None = Field(None, max_length=500)
    ref: str | None = Field(None, max_length=40)
    transfer_type: Literal["havale", "eft", "fast"] | None = None
    bank: str | None = Field(None, max_length=60)
    dekont_file: str | None = Field(None, pattern=r"^[0-9a-f]{64}\.(?:jpg|pdf)$")  # okuma yanıtındaki dosya
    debt_id: int | None = None   # "Bu, X'in borç ödemesi mi?" → Evet: kayıt bu borcun ödemesi olur
    debt_link: bool = True       # Hayır: açık borçla kendiliğinden eşleştirme de yapılmaz
    force: bool = False          # aynı gün/tutar/kişide ikinci, ayrı bir işlem (istemci sorduktan sonra)


@app.post("/api/dekont/save")
def save_dekont(body: DekontIn):
    """Dekonttan transfer: tek işlem (bakiye çapası değişmez), import_hash 'dekont:…' — o ayın hesap dökümü gelince aynı
    satırla birleşir. Aynı dekont kayıtlıysa 409 {detail: "Bu dekont zaten eklenmiş", entry_id}."""
    from app import book, dekont_parse, sync

    _visible_or_404("account", body.account_id)
    data = body.model_dump()
    data["date"] = body.date.isoformat()
    if data["dekont_file"] and not db.file_indexed("receipts", data["dekont_file"]):
        data["dekont_file"] = None  # bu defterde saklanmış bir dosya değil: bağlanmaz
    if (m := book.member()) and book.role(m) != "full":
        data["debt_id"] = None  # borç defteri sahibin (ve tam yetkilinin); üye kaydında kendiliğinden eşleşme sürer
    if u := db.current_user():
        data["created_by"] = u["id"]
    try:
        out = dekont_parse.save(data)
    except dekont_parse.Duplicate as e:
        return JSONResponse({"detail": str(e), "entry_id": e.entry_id, "duplicate": True}, status_code=409)
    except db.OverpayError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    except ValueError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e
    db.auto_link()
    sync.reconcile()
    return out


# ---------------------------------------------------------------- telefondan Paylaş

SHARE_MAX_BYTES = 15 * 1024 * 1024  # paylaşılan dosya başına (hazır sürümde nginx de 15 MB)


@app.post("/api/share")
async def share(request: Request, file: list[UploadFile] = File(...)):
    """iOS Kısayollar / uygulama: paylaşılan PDF ve fotoğraflar. Yanıt düz metin (Kısayol bildirim olarak gösterir)."""
    from fastapi.responses import PlainTextResponse

    from app import share as share_mod

    out = []
    for f in file[:5]:
        data = await f.read()
        if len(data) > SHARE_MAX_BYTES:
            out.append(f"{f.filename}: dosya çok büyük")
            continue
        try:
            res = await asyncio.to_thread(_heavy(share_mod.process), f.filename or "dosya", data, f.content_type or "")
        except (HTTPException, limits.LimitError) as e:
            out.append(f"{f.filename}: {getattr(e, 'detail', None) or e}")
            continue
        out.append(res["message"])
        if res.get("storage_warning"):
            out.append(res["storage_warning"])
        if res.get("status") in ("ok", "bekliyor"):
            from app import push

            push.after_document("paylas", res["message"])  # telefona "belge işlendi" (kapalıysa hiçbir şey yapmaz)
    if "text/plain" in request.headers.get("accept", "") or "json" not in request.headers.get("accept", ""):
        return PlainTextResponse("\n".join(out) or "Dosya yok")
    return {"messages": out}


def _shortcut_file(name: str = "hesapkitap.shortcut") -> str:
    """İmzalı iOS kısayolu (scripts/make_shortcut.py ile Mac'te üretilir; sunucuya özel adres içerir, repoda yok)."""
    return os.path.join(os.path.dirname(os.path.abspath(system.SYSTEM_DB)), "shortcuts", name)


@app.get("/api/share/shortcut")
def shortcut_info():
    cam = os.path.exists(_shortcut_file("hesapkitap-fis.shortcut"))
    return {"available": os.path.exists(_shortcut_file()), **({"camera": True} if cam else {})}


@app.get("/hesapkitap-fis.shortcut")
def shortcut_camera_download():
    """"Fiş çek" kısayolu: kamerayı açar, fişi gönderir."""
    f = _shortcut_file("hesapkitap-fis.shortcut")
    if not os.path.exists(f):
        raise HTTPException(404, "Kısayol bu sunucuda hazır değil")
    return FileResponse(f, filename="Fiş çek.shortcut", media_type="application/octet-stream")


@app.get("/hesapkitap.shortcut")
def shortcut_download():
    """iPhone Safari bu dosyayı açınca "Kısayol Ekle" çıkar. Gizli bilgi içermez (anahtar eklerken sorulur)."""
    if not os.path.exists(_shortcut_file()):
        raise HTTPException(404, "Bu sunucuda hazır kısayol yok; Ayarlar'daki adımlarla elle kur")
    return FileResponse(_shortcut_file(), filename="HesapKitap'a gönder.shortcut", media_type="application/octet-stream")


@app.post("/share-target")
async def share_target(request: Request):
    """Android: yüklü uygulamanın Paylaş hedefi (manifest share_target). Oturum çereziyle; sonra uygulamaya döner."""
    from fastapi.responses import RedirectResponse

    from app import share as share_mod

    u = auth.token_user(request.cookies.get(auth.COOKIE)) if auth.enabled() else None
    if auth.enabled() and not u:
        return RedirectResponse("/#/ozet", status_code=303)
    db_file = None
    if u:  # /api/ dışında: middleware'den geçmez; /api/share ile aynı yetki, paket ve şifre kontrolü
        gate = _access(request, "POST", "/api/share", u)
        if isinstance(gate, JSONResponse):
            return gate
        u, db_file = gate
    form = await request.form()
    msgs = []
    with db.use_space(db_file, u):
        for f in form.getlist("file")[:5]:
            if hasattr(f, "read"):
                data = await f.read()
                if len(data) > SHARE_MAX_BYTES:
                    msgs.append(f"{f.filename}: dosya çok büyük")
                    continue
                try:
                    res = await asyncio.to_thread(_heavy(share_mod.process), f.filename or "dosya", data, f.content_type or "")
                except (HTTPException, limits.LimitError) as e:
                    msgs.append(f"{f.filename}: {getattr(e, 'detail', None) or e}")
                    continue
                msgs.append(res["message"])
    from urllib.parse import quote

    return RedirectResponse("/#/ozet?paylasim=" + quote(" · ".join(msgs)[:300]), status_code=303)


@app.get("/api/inbox")
def shared_inbox():
    from app import share as share_mod

    return share_mod.inbox()


def _held_access() -> None:
    from app import book

    m = book.member()
    if m and book.role(m) != "full":  # belge yükleme gibi: ortak defterde yalnız tam yetkili üye
        raise HTTPException(403, errors.GENERIC[403])


@app.get("/api/inbox/{iid}/file")
def inbox_file(iid: int):
    """Onay bekleyen dokum/ekstre belgesinin PDF'i (inline). Yetki import ile aynı; başka tür ya da gizli kayıt 404."""
    from app import verification

    _held_access()
    doc = verification.held_doc(iid)
    if not doc or not doc.get("file"):
        raise HTTPException(404, "Dosya yok")
    return _stored_file("uploads", doc["file"], doc.get("filename") or "belge.pdf", inline=True)


@app.post("/api/inbox/{iid}/import")
def inbox_import(iid: int):
    """Doğrulanamadığı için e-postadan/paylaşımdan onaya düşen belgeyi (kind dokum | ekstre) kaydet (S6). Dönüş:
    {ok, kind, message, stats? (döküm sayaçları), statement_id?}. Yalnız bekleyen dokum/ekstre kaydı: yoksa 404."""
    from app import undo, verification

    _held_access()
    try:
        with undo.batch(f"Gelen kutusu: belge #{iid}"):
            return verification.import_held(iid)
    except KeyError as e:
        raise HTTPException(404, errors.GENERIC[404]) from e


@app.post("/api/inbox/{iid}/{action}")
def shared_inbox_close(iid: int, action: Literal["kaydedildi", "silindi"]):
    from app import share as share_mod

    if not share_mod.close(iid, action):
        raise HTTPException(404, errors.GENERIC[404])
    return {"ok": True}


class TokenIn(BaseModel):
    name: str | None = None


@app.get("/api/settings/tokens")
def list_tokens():
    u = db.current_user() or auth.admin()
    return system.tokens(u["id"]) if u else []


@app.post("/api/settings/tokens")
def create_token(body: TokenIn):
    u = db.current_user() or auth.admin()
    if not u:
        raise HTTPException(409, "Önce kurulum")
    return {"token": system.create_token(u["id"], body.name or "Paylaş kısayolu")}


@app.delete("/api/settings/tokens/{tid}")
def delete_token(tid: int):
    u = db.current_user() or auth.admin()
    if not u or not system.delete_token(u["id"], tid):
        raise HTTPException(404, errors.GENERIC[404])
    return {"ok": True}


@app.post("/api/documents/parse")
@_heavy
def parse_document(file: list[UploadFile] = File(...), ocr_text: str | None = Form(None),
                   ocr_source: str | None = Form(None)):
    """Fiş veya fatura (foto/PDF): türünü kendisi anlar. Faturaysa dosya saklanır.
    Birden fazla dosya: uzun fişin sırayla çekilmiş parçaları (en fazla 4).
    ocr_text (yetenek receipt_text): telefonda çıkarılmış metin (ocr_source "vision" | "mlkit"). Yapay zekâ yine
    FOTOĞRAFI okur (daha ucuz ve daha tutarlı, app/receipt_bench.py ölçümü); metin yardımcıdır: fatura şablonu metinle
    yerelde okunur, model KDV/VKN'yi boş bırakırsa metinden doldurulur, yapay zekâ yoksa yerel okuma bu metinle yapılır
    (_read_with_text). HESAPKITAP_RECEIPT_TEXT_FIRST=1: eski yol — metin fotoğraf yerine gider, tutarsızsa fotoğrafla
    bir kez daha okunur (_read_by_text)."""
    files = file[:4]
    data = _file_bytes(files[0])
    for f in files:
        limits.check_size(_file_bytes(f), f.filename)
    mime = files[0].content_type or ""
    if mime not in BILL_TYPES or any((f.content_type or "") not in BILL_TYPES for f in files[1:]):
        raise HTTPException(422, "Sadece PDF veya fotoğraf yüklenebilir")
    more = [(_file_bytes(f), f.content_type or "") for f in files[1:]]
    text = (ocr_text or "").strip() if mime.startswith("image/") else ""
    if len(text) > receipt.MAX_OCR_CHARS:
        raise HTTPException(422, "Fişten çıkarılan metin çok uzun")
    return _read_document(data, mime, more, ocr_text=text or None,
                          ocr_source=ocr_source if ocr_source in ("vision", "mlkit") else "diger")


def _read_with_text(text: str, source: str, data: bytes, mime: str, cats: list[str], split: list[str],
                    more: list | None) -> dict:
    """Varsayılan yol (fotoğraf + telefon metni): yapay zekâ fotoğrafı okur (maliyet türü fis), metin yalnız yardımcı
    (receipt.parse_document ocr_text). doc['ocr'] = {source, used, fallback, helper}: used — sonuç metinden çıktı (fatura
    şablonu ya da yerel okuma); yapay zekâya metin gitmez."""
    doc = receipt.parse_document(data, mime, cats, split, more=more, ocr_text=text)
    from_text = doc.get("engine") == "yerel" or doc.get("source") == "sablon"
    doc["ocr"] = {"source": source, "used": from_text, "fallback": False, "helper": True}
    return doc


def _read_by_text(text: str, source: str, data: bytes, mime: str, cats: list[str], split: list[str],
                  more: list | None) -> dict:
    """Eski metin yolu (HESAPKITAP_RECEIPT_TEXT_FIRST=1, ölçüm için): telefonda çıkarılmış metin yapay zekâya gider.
    Metin okunamazsa ya da çıkan fiş tutarsızsa (toplam yok, fiyatsız kalem, kalem toplamı fişi tutmuyor) fotoğrafla
    bir kez daha okunur; fotoğraf okuması da olmazsa metnin sonucu kalır. doc['ocr'] = {source, used, fallback}."""
    try:
        doc = receipt.parse_document_text(text, cats, split)
    except receipt.ReceiptError:
        doc = None
    if doc is not None and doc["type"] == "receipt":
        items = doc.get("items") or []
        bad = doc.get("total") is None or bool(receipt_check.inconsistent(items, receipt_check.check(items, doc.get("total"))))
    else:
        bad = doc is None
    if not bad:
        doc["ocr"] = {"source": source, "used": True, "fallback": False}
        return doc
    try:
        again = receipt._parse_document_ai(data, mime, cats, split, more, kind="otomatik_foto") if doc is not None \
            else receipt.parse_document(data, mime, cats, split, more=more)
    except receipt.ReceiptError:
        if doc is None:
            raise
        again = None
    if again is None:
        doc["ocr"] = {"source": source, "used": True, "fallback": False}
        return doc
    again["ocr"] = {"source": source, "used": False, "fallback": True}
    return again


def _read_document(data: bytes, mime: str, more: list | None = None, ocr_text: str | None = None,
                   ocr_source: str | None = None) -> dict:
    """Fotoğraflar okunmadan önce küçültülür ve EXIF'ten arındırılır; fişin (ilk) fotoğrafı saklanır."""
    import hashlib

    from app import doc_cache, doc_reads

    original, original_mime = data, mime
    # çok sayfalı (birden çok fotoğraflı) fiş tek belgedir: anahtar bütün parçaların özeti; hak her parçanın
    # ağırlığının toplamı (uzun PDF bir fotoğrafla birlikte gönderilince 1 hakka inmesin; doc_reads.weight_many)
    key = hashlib.sha256(b"".join(hashlib.sha256(d).digest() for d in [data, *(d for d, _ in (more or []))])).digest() \
        if more else data
    need = doc_reads.weight_many([data, *(d for d, _ in more)]) if more else None
    data, mime, photo = _prepare_photo(data, mime)
    # önbellek anahtarı telefon metnini de içerir (sonuç metne göre değişebilir: şablon, KDV/VKN yedeği); metni
    # değiştirip önbelleği atlayan okuma forget ile yeniden sayılır
    ckey = hashlib.sha256(hashlib.sha256(key).digest() + f"{ocr_source or ''}\n{ocr_text}".encode()).digest() \
        if ocr_text else key
    if (hit := doc_cache.get(ckey, doc_cache.BELGE)) is not None:  # aynı belge bu defterde okunmuştu: yapay zekâ yok
        if hit.get("type") != "bill":
            return _cached_receipt(hit, photo)
        out = doc_cache.hit(hit)
        out["file_name"] = db.save_bill_file(photo, ".jpg") if photo else \
            db.save_bill_file(original, BILL_TYPES.get(original_mime, ".pdf"))
        if warn := db.take_storage_warning():
            out["storage_warning"] = warn
        return out
    more = [_prepare_photo(d, m)[:2] for d, m in (more or [])] or None
    cats = sorted(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories()) - {"Ödeme", "Kategorisiz"})
    try:
        # tür okumadan belli değil: fotoğraf fiş sayılır (PDF belge); sayım okunan gerçek türe gider
        doc_reads.check(key, doc_reads.guess_kind(original_mime), need)  # hazır sürüm: okuma hakkı doluysa okunmaz
    except doc_reads.QuotaError as e:  # fiş fotoğrafı okunmadan saklanır: elle girilen gidere bağlanır
        raise QuotaFull(502, e, receipt_file=_store_receipt(photo)) from e
    doc_reads.forget(key)  # önbellekte yok: yeniden okunursa yeniden sayılır
    split = sorted({r["category"] for r in db.list_item_rules()})
    try:
        if ocr_text and receipt.enabled() and receipt.text_first():
            doc = _read_by_text(ocr_text, ocr_source or "diger", data, mime, cats, split, more)
        elif ocr_text:  # fotoğraf okunur, metin yardımcı (fatura şablonu, KDV/VKN yedeği, yerel okuma)
            doc = _read_with_text(ocr_text, ocr_source or "diger", data, mime, cats, split, more)
        else:
            doc = receipt.parse_document(data, mime, cats, split, more=more)
    except doc_reads.QuotaError as e:  # yönetici kısıtlaması (yapay zekâ kapalı, yerel okuma da olmadı)
        raise QuotaFull(502, e, receipt_file=_store_receipt(photo)) from e
    except receipt.ReceiptError as e:
        raise HTTPException(502, errors.user_text(e, 502)) from e
    if _unreadable(doc):  # okunamadı: günün ilk denemeleri ücretsiz (bkz. _mark_unreadable)
        _mark_unreadable(doc, UNREADABLE_MSG)
    elif charged := doc_reads.charge(key, doc_reads.kind_of_doc(doc.get("type")), need):
        doc["charged"] = charged  # {daily, extra}
    if doc["type"] == "receipt":
        # kategori: önce kullanıcının kuralları (unvan, sonra marka), yoksa modelin tahmini
        from app.categorize import UNCATEGORIZED, categorize

        with db.connect() as c:
            rules = db.load_rules(c)
        for name in (doc.get("legal_name"), doc.get("merchant")):
            cat = categorize(name, "harcama", rules) if name else UNCATEGORIZED
            if cat != UNCATEGORIZED:
                doc["category"] = cat
                break
        # vergi bilgisi okunamadıysa aynı işyerinin önceki fişlerinden (app/tax_fill.py; tek VKN'ye çıkıyorsa)
        from app import tax_fill

        got = tax_fill.Book(db.list_entries()).complete(
            {"description": doc.get("merchant"), **{k: doc.get(k) for k in tax_fill.FIELDS}})
        for k in tax_fill.FIELDS:
            doc[k] = got.get(k)
        if got.get("tax_filled"):
            doc["tax_filled"] = got["tax_filled"]
    if doc["type"] == "bill":
        doc["file_name"] = db.save_bill_file(photo, ".jpg") if photo else \
            db.save_bill_file(original, BILL_TYPES.get(original_mime, ".pdf"))
    else:
        doc["receipt_file"] = _store_receipt(photo)
        doc["check"] = receipt_check.check(doc.get("items") or [], doc.get("total"))
        # toplam doğrulama (bedava): telefon metni, PDF'te metin katmanı (yetenek total_check)
        from app import bill_templates

        text = ocr_text or (bill_templates.text_of(original, original_mime) if not more else None)
        receipt_verify.apply(doc, text)
        if not doc.get("unreadable"):
            _auto_reread(doc, data, mime, more, text)
        # digit_fix: metin kalemin yanında düzeltilmiş tutarı gösteriyor mu, KDV grupları, fiyat geçmişi
        _digit_evidence(doc.get("check"), doc.get("items") or [], text, doc.get("vat_lines"), doc.get("merchant"))
    if not doc.get("unreadable"):
        doc_cache.put(ckey, doc_cache.BELGE, doc, doc.get("receipt_file"))
    if doc["type"] != "bill":
        _add_price_alerts(doc)  # önbelleğe girmez: geçmiş her okumada güncel
    if warn := db.take_storage_warning():
        doc["storage_warning"] = warn
    return doc


# ---------------------------------------------------------------- kayıtlı fişin kalemleri ve yeniden okuma

_AUTO_REREAD_PER_DAY = 10  # kullanıcı başına günlük otomatik yeniden okuma (maliyet sınırı; okuma hakkı düşmez)


def _auto_reread(doc: dict, data: bytes, mime: str, more: list | None = None, text: str | None = None) -> None:
    """Kademeli okuma: kalemler fişi tutmuyorsa (receipt_check.reread_reasons: fiyatsız kalem, kalem toplamı fiş
    toplamından ±0,05'ten fazla sapıyor; kalemli fişte ayrıca adet × birim tutmayan kalem, iki satıra bölünmüş ürün ya
    da toplamın telefon/PDF metninde geçmemesi) aynı istekte bir kez daha güçlü modelle oku; yalnız daha iyiyse al
    (receipt_check.take_reread). Toplam metinde yoksa modele bildirilmez (receipt_check.reread_hint). Okuma hakkı
    düşmez; kullanıcı başı günlük _AUTO_REREAD_PER_DAY; model hatasında ilk sonuç.
    doc['reread'] = {tried, used, model}; doc['reread_reasons'] = nedenler."""
    import json as _json
    from datetime import date as _date

    from app import system

    chk = doc.get("check") or {}
    reasons = receipt_check.reread_reasons(doc.get("items") or [], chk, doc.get("total_check"))
    if not reasons or not receipt.enabled():
        return
    u = db.current_user()
    key = f"receipt_auto_reread:{u['id'] if u else 0}"
    try:
        st = _json.loads(system.get(key) or "{}")
    except ValueError:
        st = {}
    today = _date.today().isoformat()
    used = st.get("n", 0) if st.get("day") == today else 0
    if used >= _AUTO_REREAD_PER_DAY:
        return
    system.put(key, _json.dumps({"day": today, "n": used + 1}))
    model = receipt.reread_model()
    doc["reread"] = {"tried": True, "used": False, "model": model}
    doc["reread_reasons"] = reasons  # fiyatsiz|toplam|birim|bolunmus|metin (ölçüm ve destek için)
    try:
        again = receipt.reread_items(data, mime, receipt_check.reread_hint(doc), more)
    except Exception:  # noqa: BLE001 - yeniden okuma olmadıysa ilk okuma geçerli
        import logging

        logging.getLogger("ekstre.receipt").warning("otomatik yeniden okuma başarısız", exc_info=True)
        return
    doc["reread"]["used"] = receipt_check.take_reread(doc, again, text)


_REREAD_PER_DAY = 3  # aynı fotoğraf için günde (her elle yeniden okuma ayrıca doc_reads.REREAD_COST hak)


def _entry_for_receipt(entry_id: int) -> dict:
    from app import book

    with db.connect() as c:
        r = c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not r or (book.member() and not book.can_see_entry(entry_id)):
        raise HTTPException(404, "Kayıt yok")
    return dict(r)


def _digit_evidence(chk: dict | None, items: list[dict], text: str | None = None, vat_lines=None,
                    merchant: str | None = None, exclude_entry: int | None = None) -> None:
    """Fiş tutmuyorsa check'in karışan rakam önerilerini kanıtlarla yeniden hesapla (receipt_check.add_evidence):
    okuma anındaki telefon/PDF metni (saklanmaz), fişin KDV dökümü ve bu defterdeki son 90 günün birim fiyatları
    (db.recent_unit_prices; aynı mağaza önce)."""
    if not chk or chk.get("ok") or chk.get("diff") is None or abs(chk["diff"]) <= receipt_check.TOL:
        return
    if isinstance(vat_lines, str):
        import json as _json

        from app import finance

        try:
            vat_lines = finance._vat_lines(_json.loads(vat_lines))
        except ValueError:
            vat_lines = None
    names = [db.item_product(it.get("name"), it.get("product")) or it.get("name") or "" for it in items]
    prices = db.recent_unit_prices(names, merchant, exclude_entry=exclude_entry)
    history = {i: prices[k] for i, n in enumerate(names) if (k := db.product_key(n)) in prices}
    receipt_check.add_evidence(chk, items, text, vat_lines or None, history or None, _community_evidence(items, merchant))


def _community_evidence(items: list[dict], merchant: str | None) -> dict[int, float] | None:
    """Karışan rakam kanıtı: bilinen zincirde kalemin ürün grubunun topluluk ortanca birim fiyatı (en az 3 kişi; app/
    community_prices.py), sıra → ortanca. Zincir değilse ya da veri yoksa None. Yapay zekâ çağrılmaz (yalnız kural ve
    önbellek)."""
    from app import community_prices, product_groups
    from app.receipt_templates import chain_of

    chain = chain_of(merchant)
    if not chain or not items:
        return None
    groups = product_groups.groups_for(items, learn=False)
    med = community_prices.medians([g["key"] for g in groups if g], chain)
    out = {i: m["median"] for i, g in enumerate(groups) if g and (m := med.get((chain, g["key"])))}
    return out or None


def _receipt_items_out(entry_id: int, total: float | None, entry: dict | None = None) -> dict:
    with db.connect() as c:
        items = [dict(x) for x in c.execute(
            "SELECT id, name, product, brand, qty, unit, amount, unit_price, price_known FROM receipt_items "
            "WHERE entry_id = ? ORDER BY id", (entry_id,))]
    # saklanan unit_price tutar / adet'ten türetilmiştir: adet × birim denetimi burada anlamsız, yalnız toplam denetlenir
    bare = [{**i, "unit_price": None} for i in items]
    chk = receipt_check.check(bare, total)
    out = {"entry_id": entry_id, "items": items, "check": chk}
    if entry:
        _digit_evidence(chk, bare, vat_lines=entry.get("vat_lines"), merchant=entry.get("description"), exclude_entry=entry_id)
        _add_price_alerts({**out, "merchant": entry.get("description"), "legal_name": entry.get("legal_name")}, entry_id,
                          entry.get("entry_date"))
    return out


@app.get("/api/entries/{entry_id}/receipt-items")
def get_receipt_items(entry_id: int):
    """Kayıtlı fişin kalemleri ve denetimi (toplam tutuyor mu)."""
    e = _entry_for_receipt(entry_id)
    return _receipt_items_out(entry_id, e["amount"], e)


class ReceiptItemsIn(BaseModel):
    items: list[dict]


@app.put("/api/entries/{entry_id}/receipt-items")
def put_receipt_items(entry_id: int, body: ReceiptItemsIn):
    """Kalemleri tamamen değiştir ({name, product?, brand?, qty, unit, amount, unit_price?}). Kaydın kendisi, kategorisi
    ve bölmeleri (splits) değişmez; fiyat takibi bu kalemlerle güncellenir."""
    e = _entry_for_receipt(entry_id)
    if len(body.items) > 60:
        raise HTTPException(422, "En fazla 60 kalem")
    n = db.save_receipt_items(entry_id, [i for i in body.items if isinstance(i, dict)], e["description"],
                              e["entry_date"], total=e["amount"])
    return {"saved": n, **_receipt_items_out(entry_id, e["amount"], e)}


def _reread_photo(name: str | None, total: float | None, current: list[dict] | None = None) -> dict:
    """Saklı fotoğrafı daha güçlü modelle yeniden oku: hazır sürümde doc_reads.REREAD_COST (2) okuma hakkı (önce
    günlük, kalanı ek paket; yetmezse okumadan 402 "hak doldu"), yanıtta charged {daily, extra}. Ayrıca fotoğraf başına
    günde _REREAD_PER_DAY. Sonuç kaydedilmez.
    Kullanıcının düzeltmeleri korunur: current (şu anki kalemler) yapay zekânın bu fotoğraftaki önceki okumasından
    (okuma önbelleği, doc_cache.for_file) farklı tutarlı kalemleri yeni okumada da kalır (receipt_check.user_edits /
    keep_user_edits); yanıtta kept_edits [{index, name, read, amount}]. Önceki okuma bilinmiyorsa (kendi sunucusu,
    30 günden eski) korunmaz. Önbelleğe yapay zekânın ham okuması yazılır."""
    import json as _json
    from datetime import date as _date

    from app import doc_reads, storage

    if not name or not name.endswith(".jpg") or not storage.valid_name("receipts", name) or not db.file_indexed("receipts", name):
        raise HTTPException(404, "Fiş fotoğrafı yok")  # PDF dekont yeniden okunmaz
    if not receipt.enabled():
        raise HTTPException(422, "Yeniden okuma yapay zekâ ister (Ayarlar → Anahtarlar)")
    key = f"receipt_reread:{name}"
    try:
        st = _json.loads(db.get_setting(key) or "{}")
    except ValueError:
        st = {}
    today = _date.today().isoformat()
    used = st.get("n", 0) if st.get("day") == today else 0
    if used >= _REREAD_PER_DAY:
        raise HTTPException(429, f"Bu fiş bugün {_REREAD_PER_DAY} kez yeniden okundu; yarın tekrar dene ya da kalemleri elle düzelt")
    try:
        doc_reads.check_reread()
    except doc_reads.QuotaError as e:
        raise QuotaFull(402, e, receipt_file=name) from e
    try:
        data = db.read_file("receipts", name)
    except storage.StorageError as e:
        raise HTTPException(503, "Fotoğraf şu an alınamadı") from e
    if not data:
        raise HTTPException(404, "Fiş fotoğrafı yok")
    try:
        out = receipt.reread_items(data, "image/jpeg", total)
    except receipt.ReceiptError as e:
        raise HTTPException(502, errors.user_text(e, 502)) from e
    db.set_setting(key, _json.dumps({"day": today, "n": used + 1}))
    if charged := doc_reads.charge_reread():
        out["charged"] = charged
    from app import doc_cache

    raw = [dict(i) for i in out.get("items") or []]
    prev = doc_cache.for_file(name) if current else None
    edits = receipt_check.user_edits(current, (prev or {}).get("items"))
    out["kept_edits"] = receipt_check.keep_user_edits(out["items"], edits) if edits else []
    out["check"] = receipt_check.check(out["items"], total if total is not None else out.get("total"))
    out["receipt_file"] = name

    def _fresh(res: dict) -> dict:  # önbellekteki okuma bu fotoğrafın yeni (ham) kalemleriyle
        return {**res, "items": raw,
                "check": receipt_check.check(raw, res.get("total") if res.get("total") is not None else total)}

    doc_cache.refresh_file(name, _fresh)
    return out


class EntryRereadIn(BaseModel):
    items: list[dict] | None = None  # düzenleyicideki (henüz kaydedilmemiş olabilir) kalemler; yoksa kayıtlılar


@app.post("/api/entries/{entry_id}/receipt-reread")
@_heavy
def reread_entry_receipt(entry_id: int, body: EntryRereadIn | None = None):
    """Kayıtlı giderin fiş fotoğrafını kaydın tutarıyla yeniden oku; istemci onaylarsa PUT …/receipt-items ile yazar.
    Kullanıcının elle düzelttiği kalemler korunur (_reread_photo kept_edits)."""
    e = _entry_for_receipt(entry_id)
    current = body.items if body and body.items is not None else None
    if current is None:
        with db.connect() as c:
            current = [dict(x) for x in c.execute(
                "SELECT name, qty, amount, unit_price FROM receipt_items WHERE entry_id = ? ORDER BY id", (entry_id,))]
    return _reread_photo(e.get("receipt_file"), e["amount"], [i for i in current if isinstance(i, dict)][:60])


class RereadIn(BaseModel):
    receipt_file: str
    total: Money | None = None
    items: list[dict] | None = None  # düzenleyicideki kalemler: elle düzeltilenler yeniden okumada korunur


@app.post("/api/receipts/reread")
@_heavy
def reread_receipt(body: RereadIn):
    """Kaydetmeden önce (önizleme): okumadan dönen receipt_file adıyla yeniden oku (items: elle düzeltilenler korunur)."""
    return _reread_photo(body.receipt_file, body.total, [i for i in body.items or [] if isinstance(i, dict)][:60])


@app.get("/api/bills/summary")
def bills_summary(months: int = 24, until: str | None = None):
    """Faturalar özeti (app/bill_summary.py): kurum ve kategori bazında aylık seri, ortalama, en yüksek ay, önceki aya
    ve geçen yılın aynı ayına göre değişim. until=YYYY-MM (varsayılan bu ay), months=1–60."""
    import re as _re

    from app import bill_summary

    if until and not _re.fullmatch(r"\d{4}-\d{2}", until):
        raise HTTPException(422, "until=YYYY-MM")
    return bill_summary.summary(months, until)


@app.get("/api/bills")
def list_bills():
    return db.list_bills()


@app.post("/api/bills")
def create_bill(body: BillIn):
    """Fatura ekle. Yetenek save_book_choice: üye öbür defterine kaydedebilir (X-HK-Book): okumadaki file_name öbür
    defterdeyse kopyalanır; spender (POST /api/entries gibi) ödenmiş açılan faturanın gider kaydına uygulanır."""
    choice = _save_spender(body.spender)
    data = body.model_dump(exclude={"paid_at", "payment", "spender"})
    data["due_date"] = body.due_date.isoformat()
    if data.get("file_name") and not db.file_indexed("bills", data["file_name"]):
        _adopt_file("bills", data["file_name"])  # üyenin öbür defterinde okunmuş fatura (eski davranış: ad aynen kalır)
    bid = db.create_bill(data)
    if body.paid_at is None:
        return {"id": bid}
    eid = db.pay_bill(bid, body.amount, body.paid_at.isoformat(), body.payment)
    _apply_spender(choice, eid)
    return {"id": bid, "entry_id": eid}


@app.patch("/api/bills/{bid}")
def patch_bill(bid: int, body: BillPatch):
    data = body.model_dump(exclude_unset=True)
    if data.get("due_date"):
        data["due_date"] = data["due_date"].isoformat()
    db.update_bill(bid, data)
    return {"ok": True}


@app.delete("/api/bills/{bid}")
def delete_bill(bid: int):
    db.delete_bill(bid)
    return {"ok": True}


@app.post("/api/bills/{bid}/pay")
def pay_bill(bid: int, body: PayIn):
    try:
        return {"entry_id": db.pay_bill(bid, body.amount, body.paid_at.isoformat(), body.payment)}
    except ValueError as e:
        raise HTTPException(404, errors.user_text(e, 404)) from e


@app.delete("/api/bills/{bid}/pay")
def unpay_bill(bid: int):
    db.unpay_bill(bid)
    return {"ok": True}


@app.get("/api/bills/{bid}/file")
def bill_file(bid: int):
    b = db.get_bill(bid)
    if not b or not b["file_name"]:
        raise HTTPException(404, errors.GENERIC[404])
    return _stored_file("bills", b["file_name"], f"{b['provider']}-{b['due_date']}")


# ---------------------------------------------------------------- ayarlar: telegram + yedek

def _chat_name() -> str | None:
    """Eski sürümde bağlanmış sohbetin adını bir kez Telegram'dan çekip sakla."""
    name = db.get_setting("telegram_chat_name")
    if name or not telegram.enabled():
        return name
    try:
        ch = telegram._call("getChat", json={"chat_id": telegram.chat_id()})
        name = " ".join(x for x in (ch.get("first_name"), ch.get("last_name")) if x) or ch.get("username")
        if name:
            db.set_setting("telegram_chat_name", name)
    except telegram.TelegramError:
        pass
    return name


@app.get("/api/settings")
def settings_status():
    return {
        "telegram": {"token": bool(telegram.token()), "connected": bool(telegram.chat_id()),
                     "chat_name": _chat_name()},
        "backup": {"encrypted_offsite": bool(backup.passphrase()), "files": backup.list_backups()[:10],
                   "last_run": db.get_setting("job:backup")},
        "last_reminder": db.get_setting("job:remind"),
    }


@app.get("/api/settings/web-only")
def settings_web_only(request: Request):
    """Mobilde kurulamayan, web Ayarlar'da kurulan ayarlar (C9): {self_hosted, url, items[]}. Yetenek: web_only_settings."""
    from app import web_settings

    return web_settings.info(_public_base(request))


class CycleIn(BaseModel):
    mode: Literal["auto", "manual"] = "manual"
    day: int | None = Field(default=None, ge=1, le=28)  # manual için


@app.put("/api/settings/cycle")
def set_cycle(body: CycleIn):
    db.set_setting("cycle_mode", body.mode)
    if body.mode == "auto":
        db.auto_update_cycle()
    elif body.day:
        db.set_cycle_start_day(body.day)
    return {"day": db.cycle_start_day(), "mode": db.cycle_mode(), "current_month": finance.current_period()}


class ConfigIn(BaseModel):
    name: str
    value: str | None = None


@app.get("/api/mail/status")
def mail_status():
    from app import mailbox

    return {"enabled": mailbox.enabled(), "last_poll": db.get_setting("mail:last_poll"),
            "poll_minutes": mailbox.POLL_MINUTES, "log": db.mail_log(30)}


@app.post("/api/mail/poll")
def mail_poll():
    from app import mailbox

    if not mailbox.enabled():
        raise HTTPException(400, "E-posta otomasyonu ayarlı değil")
    try:
        return mailbox.poll()
    except mailbox.MailError as e:
        raise HTTPException(400, errors.user_text(e, 400)) from e


@app.get("/api/settings/config")
def get_config():
    return config.describe()


@app.get("/api/settings/pdf-passwords")
def list_pdf_passwords():
    # şifrelerin kendisi değil, sadece maskeli ipucu döner
    return [{"index": i, "hint": (p[:1] + "•" * max(0, len(p) - 2) + p[-1:]) if len(p) > 2 else "••"}
            for i, p in enumerate(db.pdf_passwords())]


@app.delete("/api/settings/pdf-passwords/{index}")
def delete_pdf_password(index: int):
    db.forget_pdf_password(index)
    return {"ok": True}


@app.put("/api/settings/config")
def put_config(body: ConfigIn):
    if body.name not in config.EDITABLE:
        raise HTTPException(404, "Bilinmeyen ayar")
    if config.source(body.name) == "env":
        raise HTTPException(409, "Bu değer sunucuda ortam değişkeniyle belirlenmiş; oradan değiştir.")
    try:
        config.set_value(body.name, body.value)
    except PermissionError as e:
        raise HTTPException(403, "Bu ayarı sadece yönetici değiştirebilir") from e
    return {"ok": True}


@app.post("/api/settings/config/test/{name}")
def test_config(name: str):
    import httpx

    if name == "GEMINI_API_KEY":
        key = config.get(name)
        if not key:
            raise HTTPException(400, "Anahtar girilmemiş")
        try:
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": key}, timeout=15)
        except httpx.HTTPError as e:
            raise HTTPException(502, errors.user_text(e, 502, "Google'a ulaşılamadı; biraz sonra tekrar dene.")) from e
        if r.status_code != 200:
            raise HTTPException(400, "Anahtar geçersiz görünüyor")
        model = config.get("GEMINI_MODEL")
        names = {m.get("name", "").split("/")[-1] for m in r.json().get("models", [])}
        return {"ok": True, "detail": "Anahtar çalışıyor" + ("" if model in names else f" (uyarı: '{model}' modeli listede yok)")}
    if name in ("MAIL_USER", "MAIL_PASSWORD"):
        from app import mailbox

        if not mailbox.enabled():
            raise HTTPException(400, "E-posta adresi ve uygulama şifresi gerekli")
        try:
            return {"ok": True, "detail": mailbox.test_connection()}
        except mailbox.MailError as e:
            raise HTTPException(400, errors.user_text(e, 400)) from e
    if name == "TELEGRAM_BOT_TOKEN":
        try:
            me = telegram._call("getMe")
        except telegram.TelegramError as e:
            raise HTTPException(400, errors.user_text(e, 400)) from e
        return {"ok": True, "detail": f"Bot: @{me.get('username')}"}
    raise HTTPException(404, errors.GENERIC[404])


@app.post("/api/settings/config/generate-backup-passphrase")
def gen_backup_pass():
    """Güçlü bir yedek şifresi üret, kaydet ve BİR KEZ göster."""
    import secrets as _s

    if config.source("BACKUP_PASSPHRASE") == "env":
        raise HTTPException(409, "Yedek şifresi ortam değişkeniyle belirlenmiş")
    pw = _s.token_urlsafe(24)
    config.set_value("BACKUP_PASSPHRASE", pw)
    return {"passphrase": pw}


@app.post("/api/settings/telegram/pair")
def telegram_pair():
    try:
        return telegram.start_pairing()
    except telegram.TelegramError as e:
        raise HTTPException(400, errors.user_text(e, 400)) from e


@app.post("/api/settings/telegram/connect")
def telegram_connect():
    try:
        info = telegram.discover_chat()
        telegram.send("✅ HesapKitap bağlandı. Son ödeme hatırlatmaları ve günlük şifreli yedek buraya gelecek.")
        return info
    except telegram.TelegramError as e:
        raise HTTPException(400, errors.user_text(e, 400)) from e


@app.post("/api/settings/telegram/disconnect")
def telegram_disconnect():
    db.set_setting("telegram_chat_id", None)
    db.set_setting("telegram_chat_name", None)
    return {"ok": True}


@app.post("/api/settings/telegram/test")
def telegram_test():
    try:
        msgs = jobs.reminders(date.today())
        telegram.send("🔔 Test mesajı. Bugün gönderilecek hatırlatma sayısı: " + str(len(msgs)))
        for m in msgs:
            telegram.send(m)
        return {"sent": len(msgs) + 1}
    except telegram.TelegramError as e:
        raise HTTPException(400, errors.user_text(e, 400)) from e


@app.post("/api/settings/telegram/report")
def telegram_report(month: str | None = None):
    """Biten (ya da verilen) dönemin raporunu şimdi gönder; Ayarlar'dan denemek için."""
    m = month or finance.add_months(finance.current_period(), -1)
    try:
        telegram.send(finance.period_report(m))
    except telegram.TelegramError as e:
        raise HTTPException(400, errors.user_text(e, 400)) from e
    return {"month": m}


@app.post("/api/settings/backup/now")
def backup_now():
    try:
        return {"path": os.path.basename(jobs.run_backup(date.today()))}
    except telegram.TelegramError as e:
        raise HTTPException(502, errors.user_text(e, 502, "Yedek alındı ama Telegram'a gönderilemedi; bot anahtarını ve sohbeti kontrol et.")) from e


@app.get("/api/settings/export.zip")
def export_zip():
    """Tüm veri tek zip: veritabanı + ekstre PDF'leri + faturalar (taşıma ve geri yükleme için)."""
    from starlette.background import BackgroundTask

    path = backup.export_zip()
    return FileResponse(path, filename=f"hesapkitap-{date.today():%Y%m%d}.zip", media_type="application/zip",
                        background=BackgroundTask(os.remove, path))


@app.get("/api/settings/export.xlsx")
def export_xlsx():
    from fastapi.responses import Response

    from app import export

    return Response(export.workbook(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="hesapkitap-{date.today():%Y%m%d}.xlsx"'})


@app.get("/api/receipts/export")
def export_receipts(from_: date | None = Query(None, alias="from"), to: date | None = None,
                    period: Literal["month", "year"] | None = None, value: str | None = Query(None, max_length=7),
                    format: Literal["xlsx", "csv"] = "xlsx"):
    """Muhasebeci için fiş listesi (tarih, işyeri, unvan, VKN, vergi dairesi, toplam, KDV %1/%10/%20, KDV toplamı, ödeme,
    kategori). Dönem: period=month&value=2026-11 | period=year&value=2026 ya da from/to. Excel'de ikinci sayfa
    "KDV özeti" (ay ay; yıllıkta 12 ay + yıl toplamı); CSV yalnız liste (app/export.py)."""
    from app import export

    label = "Toplam"
    if period:
        try:
            from_, to = export.period_range(period, value or "")
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        label = "Yıl toplamı" if period == "year" else "Ay toplamı"
    if from_ and to and to < from_:
        raise HTTPException(422, "Bitiş tarihi başlangıçtan önce olamaz")
    span = (value.replace("-", "") if period and value else
            "-".join(x for x in (from_ and f"{from_:%Y%m%d}", to and f"{to:%Y%m%d}") if x) or f"{date.today():%Y%m%d}")
    if format == "csv":
        return Response(export.receipts_csv(from_, to), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="hesapkitap-fisler-{span}.csv"'})
    return Response(export.receipts_xlsx(from_, to, label),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="hesapkitap-fisler-{span}.xlsx"'})


# ---------------------------------------------------------------- garanti arşivi (app/warranty.py)

class WarrantyIn(BaseModel):
    """Ürün. entry_id / txn_id: kayıtlı fiş ya da kart harcamasından eklendi. receipt_file: okuma yanıtındaki ya da
    kayıttaki fiş fotoğrafı (belge olarak bağlanır; bu defterde saklanmış olmalı). attach_receipt: kaydın fiş fotoğrafı
    bağlansın (varsayılan evet)."""
    name: str = Field(min_length=1, max_length=120)
    brand: str | None = Field(None, max_length=80)
    model: str | None = Field(None, max_length=80)
    store: str | None = Field(None, max_length=120)
    purchase_date: date
    price: Money | None = Field(None, ge=0)
    months: int | None = Field(None, ge=0, le=240)
    extra_months: int | None = Field(None, ge=0, le=240)
    serial_no: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=1000)
    entry_id: int | None = None
    txn_id: int | None = None
    receipt_file: str | None = Field(None, pattern=r"^[0-9a-f]{64}\.(?:jpg|pdf)$")
    attach_receipt: bool = True


class WarrantyPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    brand: str | None = Field(None, max_length=80)
    model: str | None = Field(None, max_length=80)
    store: str | None = Field(None, max_length=120)
    purchase_date: date | None = None
    price: Money | None = Field(None, ge=0)
    months: int | None = Field(None, ge=0, le=240)
    extra_months: int | None = Field(None, ge=0, le=240)
    serial_no: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=1000)


def _warranty_or_404(wid: int) -> dict:
    from app import warranty

    w = warranty.get(wid)
    if not w:
        raise HTTPException(404, "Ürün bulunamadı")
    return w


def _warranty_call(fn, *a):
    from app import warranty

    try:
        return fn(*a)
    except KeyError as e:
        raise HTTPException(404, "Ürün bulunamadı") from e
    except warranty.Forbidden as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:  # TooMany dahil
        raise HTTPException(422, str(e)) from e


@app.get("/api/warranties")
def warranties(q: str | None = Query(None, max_length=100), status: Literal["aktif", "yakinda", "bitti", "all"] | None = None):
    """Garantiler: {items, counts {aktif, yakinda, bitti, all}, quota {limit, used, locked, need}, default_months,
    soon_days, max_files, doc_types}. items: bitişi yakın önce, bitenler sonda; q ad/marka/model/mağaza/seri no/notta."""
    from app import warranty

    return warranty.listing(db.current_user(), q, status)


@app.get("/api/warranties/draft")
def warranty_draft(entry_id: int | None = None, txn_id: int | None = None):
    """"Garantiye ekle" formu kayıttan / kart harcamasından: {source, entry_id, txn_id, name, store, purchase_date, price,
    items [{name, qty, amount}] (fiş kalemleri; seçilen kalem ad ve fiyatı doldurur), receipt_file, total}."""
    from app import warranty

    if (entry_id is None) == (txn_id is None):
        raise HTTPException(422, "entry_id ya da txn_id gerekli")
    _visible_or_404("entry" if entry_id is not None else "txn", entry_id if entry_id is not None else txn_id)
    d = warranty.draft_from_entry(entry_id) if entry_id is not None else warranty.draft_from_txn(txn_id)
    if not d:
        raise HTTPException(404, "Kayıt bulunamadı")
    return d


@app.get("/api/warranties/receipts")
def warranty_receipts(q: str | None = Query(None, max_length=100), limit: int = Query(30, ge=1, le=100)):
    """"Kayıtlı fişten ekle": fotoğrafı ya da kalemleri olan son gider kayıtları [{entry_id, date, store, amount, items,
    photo, preview}]."""
    from app import warranty

    return warranty.receipts(q, limit)


@app.get("/api/warranties/{wid}")
def warranty_get(wid: int):
    return _warranty_or_404(wid)


@app.post("/api/warranties")
def warranty_create(body: WarrantyIn, request: Request):
    """Ürün ekle. Ücretsiz pakette (plans "garanti") defterde en çok plans.FREE_WARRANTIES ürün: 402. Idempotency-Key
    satıra yazılır (çevrimdışı kuyruğun tekrarı aynı ürünü döner)."""
    from app import warranty

    if body.entry_id is not None:
        _visible_or_404("entry", body.entry_id)
    if body.txn_id is not None:
        _visible_or_404("txn", body.txn_id)
    key = _client_key(request)
    with db.connect() as c:
        dup = key and c.execute("SELECT id FROM warranties WHERE client_key = ?", (key,)).fetchone()
    if dup:
        return _warranty_or_404(dup[0])
    q = warranty.quota(db.current_user())
    if q["locked"]:
        raise HTTPException(402, detail=str(plans.PlanRequired("garanti")), headers={"X-Plan-Required": "garanti"})
    receipt_name = body.receipt_file
    if body.entry_id is not None and body.attach_receipt and not receipt_name:
        with db.connect() as c:
            r = c.execute("SELECT receipt_file FROM entries WHERE id = ?", (body.entry_id,)).fetchone()
        receipt_name = r[0] if r else None
    if receipt_name and not _adopt_file("receipts", receipt_name):
        receipt_name = None  # bu defterde (ya da üyenin öbür defterinde) saklanmış bir fotoğraf değil: bağlanmaz
    data = body.model_dump(include=set(warranty.FIELDS), exclude_unset=False)
    data["purchase_date"] = body.purchase_date.isoformat()
    try:
        wid = warranty.create(data, body.entry_id, body.txn_id, key)
        if receipt_name:
            warranty.link_file(wid, "receipts", receipt_name, "fatura", None, None, check=False)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _warranty_or_404(wid)


@app.patch("/api/warranties/{wid}")
def warranty_patch(wid: int, body: WarrantyPatch):
    from app import warranty

    data = body.model_dump(exclude_unset=True)
    if data.get("purchase_date"):
        data["purchase_date"] = data["purchase_date"].isoformat()
    for k in ("name", "purchase_date"):
        if k in data and data[k] is None:
            raise HTTPException(422, "Ürün adı ve alış tarihi boş olamaz")
    _warranty_call(warranty.update, wid, data)
    return _warranty_or_404(wid)


@app.delete("/api/warranties/{wid}")
def warranty_delete(wid: int):
    from app import warranty

    _warranty_call(warranty.delete, wid)
    return {"ok": True}


WARRANTY_FULL_MSG = ("Dosya alanın doldu: belge saklanmadı. Eski belgeleri silerek ya da paketini yükselterek yer "
                     "açabilirsin.")


@app.post("/api/warranties/{wid}/files")
def warranty_file_add(wid: int, file: UploadFile = File(...), doc_type: str = Form("diger")):
    """Belge ekle: fotoğraf (EXIF/konum silinir, küçültülür; JPEG saklanır) ya da PDF. Ürün başına en çok
    warranty.MAX_FILES; yapay zekâ okuması yok (okuma hakkı düşmez). Alan doluysa 507."""
    from app import images, warranty

    _warranty_or_404(wid)
    _warranty_call(warranty.can_add_file, wid)
    data = _file_bytes(file)
    limits.check_size(data, file.filename)
    mime = (file.content_type or "").lower()
    if data[:5] == b"%PDF-":
        name, blob = f"{db.file_hash(data)}.pdf", data
    elif mime.startswith("image/") or data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
        blob = images.prepare(data)
        if blob is None:
            raise HTTPException(422, "Bu fotoğraf açılamadı; JPEG, PNG ya da PDF yükle")
        name = f"{db.file_hash(blob)}.jpg"
    else:
        raise HTTPException(422, "Yalnız fotoğraf ya da PDF eklenebilir")
    try:
        stored = db.store_file("warranty", name, blob)
    except Exception as e:  # noqa: BLE001 - depo hatası
        raise HTTPException(503, "Dosya şu an saklanamadı; biraz sonra tekrar dene") from e
    if not stored:
        db.take_storage_warning()
        raise HTTPException(507, WARRANTY_FULL_MSG)
    _warranty_call(warranty.link_file, wid, "warranty", name, doc_type, file.filename, len(blob))
    return _warranty_or_404(wid)


@app.get("/api/warranties/{wid}/files/{fid}")
def warranty_file(wid: int, fid: int, download: bool = False):
    """Belgenin kendisi (inline; download=1 ile indirme). "Arıza / servis": istemci bunu paylaşır."""
    from app import warranty

    r = warranty.file_row(wid, fid)
    if not r:
        raise HTTPException(404, "Belge yok")
    w = r["warranty"]
    slug = re.sub(r"[^0-9A-Za-zçğıöşüÇĞİÖŞÜ]+", "-", w["name"]).strip("-")[:40] or "urun"
    label = {"fatura": "fatura", "garanti": "garanti-belgesi"}.get(r["doc_type"], "belge")
    return _stored_file(r["kind"], r["name"], f"{slug}-{label}-{fid}", inline=not download)


@app.delete("/api/warranties/{wid}/files/{fid}")
def warranty_file_delete(wid: int, fid: int):
    from app import warranty

    _warranty_or_404(wid)
    _warranty_call(warranty.unlink_file, wid, fid)
    return _warranty_or_404(wid)


@app.post("/api/settings/backup/restore")
@_heavy
def backup_restore(file: UploadFile = File(...), passphrase: str | None = Form(None)):
    try:
        return backup.restore(_file_bytes(file), file.filename or "", passphrase)
    except backup.RestoreError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.post("/api/settings/backup/{name}/restore")
@_heavy
def backup_restore_named(name: str):
    if name not in {b["name"] for b in backup.list_backups()}:
        raise HTTPException(404, errors.GENERIC[404])
    with open(os.path.join(backup.backup_dir(), name), "rb") as f:
        data = f.read()
    try:
        return backup.restore(data, name)
    except backup.RestoreError as e:
        raise HTTPException(422, errors.user_text(e, 422)) from e


@app.get("/api/settings/backup/{name}")
def backup_download(name: str):
    if name not in {b["name"] for b in backup.list_backups()}:
        raise HTTPException(404, errors.GENERIC[404])
    return FileResponse(os.path.join(backup.backup_dir(), name), filename=name, media_type="application/octet-stream")


# ---------------------------------------------------------------- evrensel bağlantı (davet → uygulama)

IOS_APP_ID = os.environ.get("HESAPKITAP_IOS_APP_ID", "D78HMKA7Z8.com.alameddinc.hesapkitap")
ANDROID_PACKAGE = os.environ.get("HESAPKITAP_ANDROID_PACKAGE", "com.alameddinc.hesapkitap")


@app.get("/.well-known/apple-app-site-association", include_in_schema=False)
def apple_app_site_association():
    """iOS evrensel bağlantı: /davet/* uygulamayı açar (hash rotası #/kayit evrensel bağlantıda eşleşmez)."""
    body = {"applinks": {"apps": [], "details": [{"appIDs": [IOS_APP_ID], "appID": IOS_APP_ID, "paths": ["/davet/*"],
                                                    "components": [{"/": "/davet/*", "comment": "davet bağlantısı"}]}]}}
    return JSONResponse(body, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/.well-known/assetlinks.json", include_in_schema=False)
def android_assetlinks():
    """Android App Links: HESAPKITAP_ANDROID_SHA256 (Play Console → Uygulama imzalama, virgülle birden çok) yoksa 404."""
    fps = [f.strip().upper() for f in os.environ.get("HESAPKITAP_ANDROID_SHA256", "").split(",") if f.strip()]
    if not fps:
        raise HTTPException(404, errors.GENERIC[404])
    body = [{"relation": ["delegate_permission/common.handle_all_urls"],
             "target": {"namespace": "android_app", "package_name": ANDROID_PACKAGE, "sha256_cert_fingerprints": fps}}]
    return JSONResponse(body, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/davet/{code}", include_in_schema=False)
def invite_redirect(code: str):
    """Uygulama yüklü değilse (ya da masaüstünde) davet bağlantısı web kayıt sayfasını açar."""
    from urllib.parse import quote

    from fastapi.responses import RedirectResponse

    ok = re.fullmatch(r"[A-Za-z0-9-]{1,40}", code or "")
    return RedirectResponse("/#/kayit" + (f"?kod={quote(code.upper())}" if ok else ""), status_code=302)


# ---------------------------------------------------------------- tanıtım ve hukuk sayfaları (isteğe bağlı)

WEB_DIST = os.environ.get("WEB_DIST")
SITE_PAGES = {"hakkinda": "index.html", "gizlilik": "gizlilik.html", "kosullar": "kosullar.html", "acik-riza": "acik-riza.html",
              "hesap-sil": "hesap-sil.html"}


def _site_dir() -> str:
    """Sunucu sahibinin koyduğu sayfalar: <veri klasörü>/site/*.html (hazır sürümde tanıtım, gizlilik, koşullar)."""
    return os.environ.get("HESAPKITAP_SITE_DIR") or os.path.join(os.path.dirname(system.SYSTEM_DB), "site")


def _site_route(page: str):
    def serve():
        f = os.path.join(_site_dir(), SITE_PAGES[page])
        if not os.path.isfile(f):
            raise HTTPException(404, errors.GENERIC[404])
        return FileResponse(f, media_type="text/html", headers={"Cache-Control": "public, max-age=300"})

    app.add_api_route(f"/{page}", serve, methods=["GET"], include_in_schema=False)


for _page in SITE_PAGES:
    _site_route(_page)


# ---------------------------------------------------------------- frontend (production)

mimetypes.add_type("application/manifest+json", ".webmanifest")
if WEB_DIST and os.path.isdir(WEB_DIST):
    _dist = os.path.realpath(WEB_DIST)
    app.mount("/assets", StaticFiles(directory=os.path.join(_dist, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, errors.GENERIC[404])
        f = os.path.realpath(os.path.join(_dist, path))
        if path and f.startswith(_dist + os.sep) and os.path.isfile(f):
            # service worker ve manifest her açılışta tazelensin
            headers = {"Cache-Control": "no-cache"} if path in ("sw.js", "manifest.webmanifest") else None
            return FileResponse(f, headers=headers)
        return FileResponse(os.path.join(_dist, "index.html"), headers={"Cache-Control": "no-cache"})


# tarayıcı güvenlik başlıkları (CSP raporu, HSTS, Permissions-Policy) ve POST /api/csp-report: app/security_headers.py.
# Yalnız yanıt başlığına ekler; dosya ve akış yanıtlarının gövdesine dokunmaz. CORS'un içinde: ön istek (OPTIONS) CORS'ta biter
from app.security_headers import SecurityHeaders  # noqa: E402

app.add_middleware(SecurityHeaders, index_file=lambda: os.path.join(WEB_DIST, "index.html") if WEB_DIST else None,
                   site_paths=[f"/{p}" for p in SITE_PAGES])

# en dışta olmalı (en son eklenen en dışta çalışır): ön istek (OPTIONS) ve 401 yanıtları da CORS başlığı alsın
app.add_middleware(CORSMiddleware, allow_origins=APP_ORIGINS, allow_credentials=False, allow_methods=["*"],
                   allow_headers=["Authorization", "Content-Type", "X-HK-Client", "X-Demo-Sid", "X-HK-Book", "Idempotency-Key"],
                   expose_headers=["X-Session-Token", "X-Demo-Sid", "Idempotent-Replayed", "Retry-After"], max_age=86400)
