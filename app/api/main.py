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

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, Field, model_validator

from app import auth, backup, config, db, deletion, donations, finance, jobs, limits, money, ocr, plans, prefs, receipt, receipt_check, system, telegram, zakat
from app.categorize import CATEGORIES, suggest_pattern
from app.parsers import parse_statement
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND

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
                logging.getLogger("ekstre").exception("kullanıcı veritabanı göçü: %s", u.get("username"))


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


@app.exception_handler(limits.LimitError)
async def _limit_error(request: Request, exc: limits.LimitError):
    """Kaynak sınırı (app/limits.py) hangi uçta aşılırsa aşılsın anlamlı Türkçe 413."""
    return JSONResponse({"detail": str(exc)}, status_code=413)


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


from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=1000)  # JS ~880 KB → ~250 KB; API yanıtları da küçülür

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
    sid, new = demo_mode.resolve(request.cookies.get(demo_mode.COOKIE) or request.headers.get("x-demo-sid"))
    db_file, user = demo_mode.space(sid)
    with db.use_space(db_file, user):
        response = await call_next(request)
    response.headers["X-Demo-Sid"] = sid
    demo_mode.track(sid, path, request.method, request.headers.get("x-real-ip") or (request.client.host if request.client else ""),
                    request.headers.get("user-agent", ""))
    if new:
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(demo_mode.COOKIE, sid, max_age=demo_mode.TTL, httponly=True, secure=https, samesite="lax", path="/")
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Robots-Tag", "noindex")
    return response


def _access(request: Request, method: str, path: str, user: dict) -> "tuple[dict, str | None] | JSONResponse":
    """Oturumu doğrulanmış kullanıcı bu isteği yapabilir mi? Tek yer: /api/ middleware'i ve /api/ dışındaki yazma
    uçları (/share-target) aynı kontrolden geçer: zorunlu şifre değişimi, paket kapısı, ortak defter yetkisi ve
    kuralları. İzin varsa (kullanıcı, veritabanı dosyası); yoksa hazır hata yanıtı."""
    # yöneticinin verdiği geçici şifreyle girenler önce kendi şifresini belirler
    if user["must_change_password"] and path != "/api/settings/password":
        return JSONResponse({"detail": "password_change_required"}, status_code=403)
    feature = plans.gate_for(path, method)
    if feature and not plans.allows(user, feature):  # barındırılan sürüm: paketinde olmayan özellik
        return JSONResponse({"detail": str(plans.PlanRequired(feature)), "plan_required": feature}, status_code=402)
    db_file = system.user_db_path(user)
    if user.get("member_of") and (request.headers.get("x-hk-book") == "kendi" or request.cookies.get("hk_book") == "kendi"):
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


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    user = None
    from app import demo_mode

    if demo_mode.enabled() and path == "/api/demo/stats":  # yönetici paneli çeker (anahtarla); sandbox açmaz, sayılmaz
        return await call_next(request)
    if demo_mode.enabled() and path.startswith("/api/") and path != "/api/auth/status":
        return await _demo_request(request, call_next)
    if demo_mode.enabled() and request.method == "GET" and "." not in path.rsplit("/", 1)[-1]:
        # sayfa açılışı: sandbox çerezi HTML ile birlikte verilir; yoksa aynı anda giden ilk API istekleri
        # her biri ayrı kopya açardı
        sid, new = demo_mode.resolve(request.cookies.get(demo_mode.COOKIE))
        response = await call_next(request)
        if new:
            https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
            response.set_cookie(demo_mode.COOKIE, sid, max_age=demo_mode.TTL, httponly=True, secure=https, samesite="lax", path="/")
        return response
    # şablon gönderimi (hub) ve gelen posta (Cloudflare Email Worker, kendi gizli anahtarıyla): oturumsuz
    public = path.startswith("/api/auth/") or path in ("/api/templates/submit", "/api/mail/inbound", "/api/billing/revenuecat",
                                                       "/api/me/restore")  # geri alma: oturum yok, giriş belgesiyle
    if auth.enabled() and path.startswith("/api/") and not public:
        if auth.setup_required():
            return JSONResponse({"detail": "setup_required"}, status_code=401)
        user = auth.token_user(request.cookies.get(auth.COOKIE))
        if not user and _bearer(request):  # mobil uygulama: oturum anahtarı başlıkta
            user = auth.token_user(_bearer(request))
        if not user and path == "/api/share":  # telefondan Paylaş (iOS Kısayollar): kişisel anahtar, sadece bu uçta
            user = system.token_owner(_bearer(request) or "") if _bearer(request) else None
        if not user:
            return JSONResponse({"detail": "Giriş gerekli"}, status_code=401)
        gate = _access(request, request.method, path, user)
        if isinstance(gate, JSONResponse):
            return gate
        user, db_file = gate
    if user:  # isteğin tamamı bu kullanıcının veritabanında çalışır
        with db.use_space(db_file, user):
            response = await call_next(request)
    else:
        response = await call_next(request)
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


def _reset_enabled() -> bool:
    from app import reset

    return reset.enabled()


def _pending_deletion(u: dict) -> JSONResponse:
    """Kimliği doğrulandı ama hesap silinmek üzere bekliyor: oturum açılmaz. İstemci "geri almak ister misin?" sorar,
    evet derse restore_token ile POST /api/me/restore. Sahibi defteri sildiği için silinen üye kendi başına geri alamaz."""
    ok = deletion.restorable(u)
    return JSONResponse({"detail": "account_pending_deletion", "delete_after": u.get("delete_after"),
                         "deleted_at": u.get("deleted_at"), "username": u["username"], "restorable": ok,
                         "restore_token": auth.make_restore_token(u) if ok else None,
                         "subscription": plans.subscription(u)}, status_code=409)


def _start_session(request: Request, response: Response, user: dict) -> None:
    # HTTPS arkasındaysa (doğrudan ya da proxy başlığıyla) çerez otomatik Secure olur
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    token = auth.make_token(system.user(user["id"]))
    if request.headers.get("x-hk-client") == "mobile":  # mobil uygulama: anahtarı kendisi saklar (Keychain)
        response.headers["X-Session-Token"] = token
        return
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, secure=auth.cookie_secure() or https, samesite="lax", path="/")


@app.get("/api/auth/status")
def auth_status(request: Request):
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
            "user": {"id": u.get("id"), "username": u["username"], "password_set": not auth.password_unset(u.get("id")), "role": u["role"], "must_change_password": bool(u["must_change_password"]),
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
        raise HTTPException(422, str(e)) from e
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
    acct = auth.account_key(body.username)
    if auth.account_locked(acct):  # IP'den bağımsız: dağıtık deneme de hesabı geçici kilitler
        raise HTTPException(429, "Bu hesap için çok fazla hatalı deneme yapıldı. 15 dakika sonra tekrar dene.")
    u = auth.login(body.username, body.password)
    if not u:
        auth.record_fail(ip)
        auth.record_account_fail(acct)
        raise HTTPException(401, "Kullanıcı adı ya da şifre yanlış" if body.username else "Şifre yanlış")
    auth.clear_account_fails(acct)
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
        raise HTTPException(422, str(e)) from e
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


@app.get("/api/auth/invite/{code}")
def invite_check(code: str):
    return {"valid": system.valid_invite(code)}


class SocialIn(BaseModel):
    id_token: str
    consent: list[str] | None = None  # yeni hesap açılacaksa hazır sürümde gerekli


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


def _social_login(verify, body: SocialIn, request: Request, response: Response):
    import secrets

    from app import social

    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    try:
        who = verify(body.id_token)
    except social.SocialError as e:
        auth.record_fail(ip)
        raise HTTPException(401, str(e)) from e
    u = system.user_by_name(who["email"])
    created = False
    if u and u["disabled"]:
        raise HTTPException(403, "Bu hesap kapatılmış")
    if u and u.get("deleted_at"):
        return _pending_deletion(u)
    if not u:
        if not plans.open_signup():
            raise HTTPException(403, "Bu e-postayla bir hesap yok")
        if not _consent_ok(body.consent):  # uygulama iki onayı sorup aynı belgeyle yeniden dener
            raise HTTPException(409, "consent_required")
        if _signup_limited(ip):
            raise HTTPException(429, "Bu bağlantıdan çok fazla hesap açıldı. Bir saat sonra tekrar dene.")
        # şifresiz hesap olmasın (boş şifre eski tek kullanıcılı girişe düşerdi): kimsenin bilmediği rastgele şifre;
        # isterse "Şifremi unuttum" ile kendi şifresini koyar
        u = auth.create_user(who["email"], secrets.token_urlsafe(32))
        system.put(f"pw_unset:{u['id']}", "1")  # şifresini bilmiyor: silme "SİL" yazarak onaylanır
        _signup_record(ip)
        _record_consent(u["id"], request)
        created = True
    system.update_user(u["id"], last_login=__import__("time").strftime("%Y-%m-%d %H:%M:%S"))
    _start_session(request, response, system.user(u["id"]))
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
    try:
        auth._check_strength(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    u = reset.verify(body.email, body.code)
    if not u:
        auth.record_fail(ip)
        raise HTTPException(400, "Kod yanlış ya da süresi dolmuş")
    auth.set_password(u["id"], body.password)
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
    if not _consent_ok(body.consent):
        raise HTTPException(422, "Devam etmek için iki onay kutusunu da işaretle")
    if not body.code and plans.open_signup():
        if _signup_limited(ip):
            raise HTTPException(429, "Bu bağlantıdan çok fazla hesap açıldı. Bir saat sonra tekrar dene.")
        try:
            u = auth.create_user(body.username, body.password)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        _signup_record(ip)
        _record_consent(u["id"], request)
        _start_session(request, response, u)
        return {"ok": True}
    if not body.code or not system.valid_invite(body.code):
        auth.record_fail(ip)
        raise HTTPException(403, "Davet kodu geçersiz, kullanılmış ya da süresi dolmuş")
    inv = system.invite(body.code) or {}
    try:  # ortak defter davetiyse: kayıt olan, daveti oluşturanın defterine üye olur
        u = auth.create_user(body.username, body.password, member_of=inv.get("member_of"), book_role=inv.get("book_role"))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not system.use_invite(body.code, u["id"]):  # aynı anda iki kayıt: ikincisi geri alınır
        system.delete_user(u["id"])
        raise HTTPException(409, "Davet kodu az önce kullanıldı")
    _record_consent(u["id"], request)
    _start_session(request, response, u)
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
            "deleted_at": u.get("deleted_at"), "delete_after": u.get("delete_after")}


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
def admin_users():
    _require_admin()
    return {"users": [_user_row(u) for u in system.users()], "invites": system.invites(),
            "default_quota": system.ai_quota({"role": "user"}), "hosted": plans.hosted()}


@app.post("/api/admin/users")
def admin_create_user(body: AdminUserIn):
    """Davetsiz hesap: geçici şifre bir kez gösterilir, ilk girişte değiştirilir."""
    _require_admin()
    temp = auth.temp_password()
    try:
        u = auth.create_user(body.username, temp, must_change=True)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
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


@app.get("/api/admin/deleted")
def admin_deleted_accounts(email: str | None = None):
    """Silinen hesapların anonim kaydı. email verilirse özeti hesaplanıp eşleştirilir (düz e-posta saklanmaz)."""
    _require_admin()
    rows = deletion.records(email=(email or "").strip() or None)
    return {"records": [{k: r[k] for k in ("user_id", "deleted_at", "delete_after", "purged_at", "reason", "plan",
                                           "plan_until", "plan_source", "plan_store", "will_renew", "store_customer",
                                           "notes", "warnings")} | {"email_hash": r["email_hash"][:12]} for r in rows],
            "keep_days": deletion.RECORD_KEEP_DAYS, "grace_days": deletion.grace_days()}


@app.post("/api/admin/invites")
def admin_create_invite(body: InviteIn):
    _require_admin()
    return system.create_invite(body.note, body.days)


@app.delete("/api/admin/invites/{code}")
def admin_delete_invite(code: str):
    _require_admin()
    if not system.delete_invite(code):
        raise HTTPException(404, "Davet yok ya da kullanılmış")
    return {"ok": True}


@app.get("/api/me/ai")
def my_ai():
    """Okuma kotası. unit "docs": hazır sürüm, aylık belge okuma hakkı (app/doc_reads.py; ailede ortak havuz).
    unit "ai": kendi sunucusu, ortak anahtarla aylık yapay zekâ çağrısı kotası; kendi anahtarı varsa sınırsız."""
    unit = "docs" if plans.hosted() else "ai"
    u = db.current_user()
    if not u or u["role"] == "admin":
        return {"used": None, "quota": None, "own_key": False, "unit": unit}
    own = bool(config.own_ai_key())
    return {"used": system.ai_used_pool(u), "quota": None if own else system.ai_quota(u), "own_key": own, "unit": unit,
            "shared_available": bool(system.get("cfg:GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY"))}


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
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}

Kind = Literal["gelir", "gider"]
Payment = Literal["kredi_karti", "nakit", "havale", "kart"]  # kart: ön ödemeli (yemek/hediye/giyim), wallet_id ile
CardKind = Literal["harcama", "iade", "faiz_ucret", "odeme"]
EXPENSE_CATEGORIES = ["Altın & Döviz Birikimi", "Kira & Aidat", "Faturalar", "Elektrik", "Doğalgaz", "Su", "İnternet & TV", "Telefon", "Sağlık",
                      "Eğitim", "Ulaşım & Araç", "Market & Gıda", "Kredi & Borç", "Bağış & Sadaka", "Diğer"]
INCOME_CATEGORIES = ["Maaş", "Ek Gelir", "Kira Geliri", "Yatırım", "Diğer Gelir"]


# ---------------------------------------------------------------- meta

def _by_usage(cats: set[str]) -> list[str]:
    usage = db.category_usage()
    return sorted(cats, key=lambda c: (-usage.get(c, 0), c))


@app.post("/api/billing/revenuecat")
async def revenuecat_webhook(request: Request):
    """App Store / Google Play aboneliği (RevenueCat webhook). Yetki: RevenueCat panelinde girilen Authorization değeri."""
    import hmac

    secret = os.environ.get("REVENUECAT_WEBHOOK_AUTH", "")
    if not plans.hosted() or not secret or not hmac.compare_digest(request.headers.get("authorization", ""), secret):
        raise HTTPException(404, "Yok")
    body = await request.json()
    res = await asyncio.to_thread(plans.apply_revenuecat, body.get("event") or {})  # SQLite yazma: döngü beklemesin
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
        raise HTTPException(404, "Yok")
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
        "card_kinds": [PURCHASE, REFUND, FEE, PAYMENT],
        "receipt_enabled": receipt.enabled() or ocr.available(),
        "receipt_engine": "gemini" if receipt.enabled() else "yerel" if ocr.available() else None,
        "current_month": finance.current_period(),
        "cycle_start_day": db.cycle_start_day(),
        "cycle_mode": db.cycle_mode(),
        "suggested_cycle_day": db.suggested_cycle_day(),
        "modules": prefs.modules(), "modules_chosen": prefs.modules_chosen(), "ui": prefs.ui(),
        "module_labels": {k: {"label": v[0], "desc": v[1]} for k, v in prefs.MODULES.items()},
        "module_consent": {k: prefs.CONSENT[k] for k in prefs._consent_needed()},
        "module_consent_title": {k: prefs.CONSENT_TITLE.get(k) for k in prefs._consent_needed()},
        "plan": _plan_info(),
    }


def _plan_info() -> dict:
    """Paket bilgisi; hazır sürümde dosya alanı da (kendi sunucusunda sınır yok, alan gösterilmez)."""
    u = db.current_user()
    info = plans.info(u)
    if plans.hosted():
        info["limits"] = {**info["limits"], "storage_bytes": plans.storage_limit(u), "storage_used": db.storage_used()}
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


class UiIn(BaseModel):
    simple: bool | None = None
    layout: list[str] | None = None
    layout_mobile: list[str] | None = None  # mobil uygulamanın Özet düzeni
    reset_layout: bool = False


@app.put("/api/me/ui")
def put_ui(body: UiIn):
    """Kişisel görünüm: sade mod, Özet düzeni (herkes kendi)."""
    data = body.model_dump(exclude_unset=True, exclude={"reset_layout"})
    if body.reset_layout:
        data["layout"] = None
    return prefs.set_ui(data)


def _history_gate(month: str | None) -> None:
    """Barındırılan sürüm, ücretsiz paket: bu dönem + geçen dönem; daha eskisi Premium."""
    if not plans.history_ok(db.current_user(), month):
        raise HTTPException(402, detail=str(plans.PlanRequired("gecmis")), headers={"X-Plan-Required": "gecmis"})


@app.get("/api/summary")
def summary(month: str | None = None):
    """month verilmezse içinde bulunulan dönem (mobil uygulama ayı beklemeden ister)."""
    _history_gate(month)
    return finance.summary(month or finance.current_period())


@app.get("/api/transactions")
def transactions(month: str | None = None, include_deleted: bool = False, include_estimates: bool = False):
    """Gerçek işlemler. Tahmini satırlar (ekstresiz kart ödemesi, dökümsüz maaş; estimated: true) varsayılan listede yok:
    include_estimates=1 ile aynı diziye eklenir — istemci `estimated` ile ayırır, hiçbir toplama katmaz."""
    _history_gate(month)
    rows = finance.transactions(month, include_deleted, include_estimates)
    u = db.current_user()
    if month is None and not plans.allows(u, "gecmis"):  # tüm aylar istendi: ücretsizde görünür pencereyle sınırlı
        rows = [r for r in rows if plans.history_ok(u, r["month"])]
    return rows


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
        fields = {k: v for k, v in fields.items() if k in ("category", "note")}
        body.create_rule = False
    for k in ("is_deleted", "hidden"):
        if k in fields:
            fields[k] = int(fields[k])
    db.update_card_transaction(txn_id, fields)
    rule = None
    if body.create_rule and body.category:
        txn = next((t for t in db.card_transactions(True) if t["id"] == txn_id), None)
        if txn:
            rule = suggest_pattern(txn["description"])
            db.add_rule(rule, body.category)
            db.recategorize_all()
    return {"ok": True, "rule": rule}


# ---------------------------------------------------------------- manual entries

class EntryIn(BaseModel):
    entry_date: date
    description: str = Field(min_length=1)
    amount: Money = Field(ge=0)
    kind: Kind
    category: str | None = None
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
    legal_name: str | None = None  # fişteki resmi unvan; saklanmaz, eşleştirme öğrenmek için
    account_id: int | None = None  # banka/nakit hesabı
    installments: int = Field(1, ge=1, le=36)  # kredi kartı taksit sayısı
    card_last4: str | None = Field(None, pattern=r"^\d{4}$")  # hangi kredi kartı (fişte yazıyorsa okunur)
    receipt_file: str | None = Field(None, pattern=r"^[0-9a-f]{64}\.jpg$")  # okuma yanıtındaki fiş fotoğrafı


class EntryPatch(BaseModel):
    entry_date: date | None = None
    description: str | None = None
    amount: Money | None = Field(default=None, ge=0)
    kind: Kind | None = None
    category: str | None = None
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


@app.post("/api/entries")
def create_entry(body: EntryIn):
    _visible_or_404("account", body.account_id)  # üye gizli hesaba kayıt yazamaz
    data = body.model_dump(exclude={"legal_name", "splits", "items", "branch"})
    data["entry_date"] = body.entry_date.isoformat()
    data["skipped"] = int(body.skipped)
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
    if data.get("receipt_file") and not db.file_indexed("receipts", data["receipt_file"]):
        data["receipt_file"] = None  # bu defterde saklanmış bir fotoğraf değil: bağlanmaz
    eid = db.create_entry(data)
    if body.items and body.kind == "gider":
        db.save_receipt_items(eid, [i for i in body.items if isinstance(i, dict)], body.description, data["entry_date"],
                              total=body.amount, branch=body.branch)
    splits = []
    if body.splits and body.kind == "gider":
        try:
            splits = db.set_splits("entry", eid, [p for p in body.splits if isinstance(p, dict)
                                                  and isinstance(p.get("amount"), (int, float))], origin="fis")
        except ValueError as e:
            db.delete_entry(eid)
            raise HTTPException(422, str(e)) from e
    db.auto_link()  # ekstre önceden yüklendiyse hemen eşleşsin
    from app import sync

    sync.reconcile()  # döküm önceden yüklendiyse: dökümdeki aynı hareketle kesin eşleşen kayıt birleşir (app/sync.py)
    debt = db.link_debt_movement(data.get("counterparty"), body.kind, body.amount, data["entry_date"], eid)
    match = sync.match_info().get(eid)
    return {"id": eid, "linked": any(e["id"] == eid and e["linked_txn_id"] for e in db.list_entries()), "match": match,
            "debt_settled": debt[1]["id"] if debt else None, "debt_step": debt[0] if debt else None,
            "splits": [{"category": x["category"], "amount": x["amount"]} for x in splits]}


@app.patch("/api/entries/{entry_id}")
def patch_entry(entry_id: int, body: EntryPatch):
    from app import book

    data = body.model_dump(exclude_unset=True)
    _visible_or_404("account", data.get("account_id"))
    if (m := book.member()):  # üye: görebildiği kayıt; başkasının kaydında sadece kategori/not/açıklama
        if not book.can_see_entry(entry_id):
            raise HTTPException(404, "Kayıt bulunamadı")
        with db.connect() as c:
            owner = c.execute("SELECT created_by FROM entries WHERE id = ?", (entry_id,)).fetchone()[0]
        data.pop("hidden", None)
        if owner != m["id"]:
            data = {k: v for k, v in data.items() if k in book.ENTRY_MEMBER_FIELDS}
    if "hidden" in data:
        data["hidden"] = int(bool(data["hidden"]))
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
        raise HTTPException(422, f"Bu kayıt bölünmüş ({split_total:.2f} alt kalem); tutar bundan az olamaz")
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
    return _stored_file("receipts", r["receipt_file"], f"fis-{r['entry_date']}-{entry_id}.jpg", inline=True)


@app.get("/api/entries/{entry_id}/candidates")
def link_candidates(entry_id: int):
    return db.link_candidates(entry_id)


@app.post("/api/entries/{entry_id}/link")
def link_entry(entry_id: int, body: LinkIn):
    _visible_or_404("txn", body.txn_id)
    try:
        db.link_entry(entry_id, body.txn_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
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
        raise HTTPException(409, str(e)) from e


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
        raise HTTPException(409, str(e)) from e
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
    from_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    gross: Money = Field(gt=0)
    reason: Literal["zam", "is_degisikligi"] = "zam"
    reset_cum: bool = False
    pay_day: int | None = Field(default=None, ge=1, le=31)
    employer: str | None = None
    note: str | None = None


class PayrollPlanIn(BaseModel):
    year: int
    draft: SalaryChangeIn | None = None  # kaydetmeden önce "olursa ne olur" önizlemesi


@app.get("/api/recurring/{rid}/salary-changes")
def list_salary_changes(rid: int):
    return db.salary_changes(rid)


@app.post("/api/recurring/{rid}/salary-changes")
def add_salary_change(rid: int, body: SalaryChangeIn):
    try:
        return {"id": db.add_salary_change(rid, body.model_dump())}
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
    changes = [c for c in db.salary_changes(rid) if not body.draft or c["from_month"] != body.draft.from_month]
    if body.draft:
        changes = sorted([*changes, {**body.draft.model_dump(), "id": None}], key=lambda c: c["from_month"])
    if not (r.get("gross_amount") or changes):
        raise HTTPException(422, "Brüt maaş tanımlı değil")
    grosses, resets = db.gross_schedule(r, body.year, changes)
    return {**net_schedule(body.year, grosses, resets), "resets": sorted(resets)}


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
    return {"id": db.create_recurring(_with_gross_net(body.model_dump()))}


@app.patch("/api/recurring/{rid}")
def patch_recurring(rid: int, body: RecurringPatch):
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
    """Hesap dökümü: yeni hareket getirdiyse 1 belge okuma hakkı (hepsi "zaten vardı" ise düşmez; aynı dosya bir kez)."""
    from app import doc_reads

    if any(v for k, v in counts.items() if k in ("gelir", "gider", "kart_odemesi", "virman")):
        doc_reads.charge(key)


def _read_bank_pdf(file: UploadFile, password: str | None = None) -> tuple[list[dict], dict]:
    import hashlib

    from app import bank_import, bank_pdf

    data = _file_bytes(file)
    key = (os.path.abspath(db.db_path()), hashlib.sha256(data).hexdigest())
    with _PDF_CACHE_LOCK:
        res = _PDF_CACHE.get(key)
    if res is None:  # ayrıştırma (yapay zekâ olabilir) kilit dışında: başka defterlerin okuması beklemesin
        try:
            res = bank_pdf.parse_account_pdf(data, ([password] if password else []) + db.pdf_passwords())
        except (bank_pdf.BankPdfError, receipt.ReceiptError) as e:
            raise HTTPException(422, str(e)) from e
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
        raise HTTPException(422, f"Dosya okunamadı: {e}") from e
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
    with_bal = [(r["date"], i, r["balance"]) for i, r in enumerate(rows) if r.get("balance") is not None]
    am = _account_match(account, iban) if account else {}
    aid = am.get("account_id") or am.get("default_id")
    acc = next((a for a in db.list_accounts() if a["id"] == aid), None) if aid else None
    change = None
    if with_bal and acc and acc["as_of"]:
        d, _, new = max(with_bal)
        cur = acc["current"] if acc["current"] is not None else acc["balance"]
        if d >= acc["as_of"]:  # daha eski döküm bakiyeyi değiştirmez
            change = {"account": acc["name"], "current": round(cur, 2), "new": round(new, 2), "date": d}
            if money.is_zero(new) and abs(cur) >= 1000:
                warnings.append(f"{acc['name']} bakiyesi {cur:,.2f} ₺ → 0 olacak. Belge doğru mu?")
            elif abs(new - cur) > max(abs(cur) * 0.5, 20000):
                warnings.append(f"{acc['name']} bakiyesi {cur:,.2f} ₺ → {new:,.2f} ₺ olacak (büyük değişiklik). "
                                "Arada yüklenmemiş bir dönem ya da yanlış belge olabilir.")
    return {"balance_change": change, "checks": warnings}


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
        with db.connect() as c:
            seen = {r[0] for r in c.execute("SELECT import_hash FROM entries WHERE import_hash IS NOT NULL")}
        counts: dict[str, int] = {}
        for r in parsed:
            if r["type"] == "gider" and db.match_card_payment(abs(r["amount"]), r["date"]):
                r["type"] = "kart_odemesi"
            k = "tekrar" if r["hash"] in seen else r["type"]
            counts[k] = counts.get(k, 0) + 1
            if r["type"] in ("gelir", "gider"):
                r["who"] = bank_import.counterparty(r["description"], r["amount"], [meta.get("owner_name")] if meta.get("owner_name") else _owners())
        _charge_if_new(doc, counts)
        return {"pdf": True, **meta, "header_row": 0, "columns": [], "mapping": {}, "profile_saved": False,
                "total": len(parsed), "counts": counts, "sample": parsed[:200], "raw_preview": [],
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
    with db.connect() as c:
        seen = {r[0] for r in c.execute("SELECT import_hash FROM entries WHERE import_hash IS NOT NULL")}
    counts: dict[str, int] = {}
    for r in parsed:
        if r["type"] == "gider" and db.match_card_payment(abs(r["amount"]), r["date"]):
            r["type"] = "kart_odemesi"  # tutar bir ekstreyle birebir → kart ödemesi (önizlemede de görünsün)
        k = "tekrar" if r["hash"] in seen else r["type"]
        counts[k] = counts.get(k, 0) + 1
    # güvenlik ağı: neredeyse hiç eksi tutar yok ve bakiye sütunu yoksa kart dökümü olabilir (harcamalar gelir görünür)
    warning = None
    amounts = [r["amount"] for r in parsed if r.get("amount") is not None]
    if len(amounts) >= 5 and sum(a > 0 for a in amounts) / len(amounts) >= 0.9 and not det["mapping"].get("balance"):
        warning = ("Bu dosyada tutarların neredeyse hepsi artı ve bakiye sütunu yok: bir kredi kartı dökümü olabilir. "
                   "Öyleyse içe aktarma — harcamalar gelir sayılır. Kart dökümünü Ekstreler sayfasına bırak; tanınmazsa "
                   "bize bildir.")
    from app import sync

    iban = sync.find_iban_rows(rows)
    _charge_if_new(doc, counts)
    return {**det, "profile_saved": bool(saved), "total": len(parsed), "counts": counts, "sample": parsed[:15],
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

    account, iban = "", None
    doc = _doc_check(file)
    if (file.filename or "").lower().endswith(".pdf"):
        parsed, meta = _read_bank_pdf(file, password)
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
        acc_id = sync.account_for_import(account or None, iban, account_id, new_account) if (account or account_id) else None
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
        b["summary"] = stats
    _charge_if_new(doc, stats)  # önizlemede sayıldıysa aynı dosya ikinci kez sayılmaz
    if warn := db.take_storage_warning():
        stats["storage_warning"] = warn
    return stats


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
        raise HTTPException(422, str(e)) from e
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
        raise HTTPException(422, str(e)) from e


class PaymentAccount(BaseModel):
    account_id: int | None = None


@app.patch("/api/debt-payments/{pid}")
def set_debt_payment_account(pid: int, body: PaymentAccount):
    if not db.set_debt_payment_account(pid, body.account_id):
        raise HTTPException(404, "Ödeme yok")
    return {"ok": True}


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
                       "Bitiş ayı olan kalemler (taksitli kredi gibi) o ay kendiliğinden biter."]},
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

REMINDER_ID = r"^((ekstre|dokum|odeme):[^\s/]{1,80}:\d{4}-\d{2}(-\d{2})?|esles:\d{1,12}:[et]\d{1,12}|hesap:\d{1,12}:\d{1,12})$"


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


class JarMoveIn(BaseModel):
    amount: float = Field(gt=0)
    direction: Literal["yatir", "cek"] = "yatir"
    move_date: date | None = None
    note: str | None = None


def _jar_view(j: dict, today: date) -> dict:
    """Hedef ilerlemesi, son tarihe göre ayda gereken ve bu hızla ne zaman ulaşılacağı.
    Hız: otomatik aylık ayırma varsa o; yoksa son 3 ayda ayrılan (başlangıç ayırması hariç) aylık ortalaması."""
    from datetime import timedelta

    out = {**j}
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
    return out


@app.get("/api/jars")
def list_jars():
    today = date.today()
    return [_jar_view(j, today) for j in db.list_jars(today)]


@app.post("/api/jars")
def create_jar(body: JarIn):
    data = body.model_dump(exclude={"initial"})
    data["name"] = data["name"].strip()
    if data["deadline"]:
        data["deadline"] = data["deadline"].isoformat()
    if bool(data["monthly"]) != bool(data["auto_day"]):
        data["monthly"] = data["auto_day"] = None
    return {"id": db.create_jar(data, body.initial)}


@app.patch("/api/jars/{jid}")
def patch_jar(jid: int, body: JarPatch):
    data = body.model_dump(exclude_unset=True)
    if "deadline" in data and data["deadline"]:
        data["deadline"] = data["deadline"].isoformat()
    if "closed" in data:
        data["closed_at"] = date.today().isoformat() if data.pop("closed") else None
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
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/jars/{jid}/buy")
def buy_into_jar(jid: int, body: JarBuy):
    """Altın/döviz kumbarası: aldım → varlık + gider kaydı + kumbara hareketi."""
    try:
        return {"id": db.buy_into_jar(jid, body.quantity, body.cost, (body.buy_date or date.today()).isoformat(),
                                      body.account_id, body.payment, body.note)}
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


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
    as_expense: bool = False   # nakitten/hesaptan verdim, dökümde görünmeyecek → gider olarak da yaz
    account_id: int | None = None


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
    try:
        return {"id": donations.add(body.kind, body.amount, (body.given_date or date.today()).isoformat(), body.recipient,
                                    body.note, body.account_id, body.as_expense)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.delete("/api/donations/{did}")
def donations_delete(did: int):
    if not donations.delete(did):
        raise HTTPException(404, "Kayıt yok")
    return {"ok": True}


@app.post("/api/donations/mark")
def donations_mark(body: DonationMarkIn):
    try:
        return donations.mark(body.source, body.id, body.kind, body.person_id,
                              body.year_start.isoformat() if body.year_start else None, body.recipient)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


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
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # noqa: BLE001 - fiyat servisine ulaşılamadı
        raise HTTPException(502, f"Geçmiş fiyat alınamadı: {e}") from e


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
        raise HTTPException(404, str(e)) from e


@app.post("/api/zakat/people/{pid}/next-year")
def zakat_next_year(pid: int):
    try:
        return {"hawl_start": zakat.next_year(pid)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/zakat/people/{pid}/payments")
def zakat_add_payment(pid: int, body: ZakatPaymentIn):
    try:
        return {"id": zakat.add_payment(pid, body.amount, (body.pay_date or date.today()).isoformat(), body.note,
                                        body.year_start.isoformat() if body.year_start else None)}
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


class ZakatPaymentPatch(BaseModel):
    amount: Money | None = Field(None, gt=0)
    pay_date: date | None = None
    note: str | None = None
    year_start: date | None = None


@app.patch("/api/zakat/payments/{pay_id}")
def zakat_update_payment(pay_id: int, body: ZakatPaymentPatch):
    """Ödemeyi tek işlemde düzenle; yalnız gönderilen alanlar değişir. Dönüş: güncel ödeme."""
    data = body.model_dump(exclude_unset=True)
    for k in ("pay_date", "year_start"):
        if data.get(k):
            data[k] = data[k].isoformat()
    try:
        r = zakat.update_payment(pay_id, data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if r is None:
        raise HTTPException(404, "Ödeme yok")
    return r


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
        raise HTTPException(422, str(e)) from e


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
        raise HTTPException(404)
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
        raise HTTPException(404)
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
        raise HTTPException(409, str(e)) from e
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
        raise HTTPException(422, str(e)) from e


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
    """Fişlerden fiyat takibi: ürün / mağaza / marka kırılımı; tarihten beri, mağaza, marka filtresi."""
    return db.products(since.isoformat() if since else None, store or None, brand or None)


@app.get("/api/item-rules")
def list_item_rules():
    return db.list_item_rules()


@app.post("/api/item-rules")
def create_item_rule(body: ItemRuleIn):
    try:
        return {"id": db.save_item_rule(body.keywords, body.category)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.put("/api/item-rules/{rid}")
def update_item_rule(rid: int, body: ItemRuleIn):
    try:
        return {"id": db.save_item_rule(body.keywords, body.category, rid)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.delete("/api/item-rules/{rid}")
def delete_item_rule(rid: int):
    if not db.delete_item_rule(rid):
        raise HTTPException(404, "Kural yok")
    return {"ok": True}


@app.post("/api/item-rules/test")
def test_item_rule(body: dict):
    """Kurallar sayfasında deneme: 'MARLBORO TOUCH' → Sigara"""
    return {"category": db.match_item(str(body.get("text") or ""))}


# ---------------------------------------------------------------- borç / alacak

class DebtIn(BaseModel):
    person: str = Field(min_length=1)
    direction: Literal["borc", "alacak"]
    asset: str = Field(default="TRY", min_length=1, max_length=40)
    amount: float = Field(gt=0)
    debt_date: date
    due_date: date | None = None
    note: str | None = None
    account_id: int | None = None  # banka/nakit hesabı

    @model_validator(mode="after")
    def _tl(self):
        if self.amount is not None and (self.asset or "TRY") == "TRY":  # TL borç: kuruşa; gram/adet olduğu gibi
            self.amount = money.money(self.amount)
        return self


class DebtPatch(BaseModel):
    person: str | None = None
    direction: Literal["borc", "alacak"] | None = None
    asset: str | None = None
    amount: float | None = Field(default=None, gt=0)
    debt_date: date | None = None
    due_date: date | None = None
    note: str | None = None
    account_id: int | None = None  # banka/nakit hesabı

    @model_validator(mode="after")
    def _tl(self):
        if self.amount is not None and self.asset == "TRY":  # TL borç: kuruşa; gram/adet olduğu gibi
            self.amount = money.money(self.amount)
        return self


class DebtPayIn(BaseModel):
    amount: float | None = Field(default=None, gt=0)  # boş → kalanın tamamı
    paid_at: date
    account_id: int | None = None  # banka/nakit hesabı


@app.get("/api/debts")
def list_debts():
    return db.list_debts()


@app.post("/api/debts")
def create_debt(body: DebtIn):
    data = body.model_dump()
    data["debt_date"] = body.debt_date.isoformat()
    data["due_date"] = body.due_date.isoformat() if body.due_date else None
    return {"id": db.create_debt(data)}


@app.patch("/api/debts/{did}")
def patch_debt(did: int, body: DebtPatch):
    data = body.model_dump(exclude_unset=True)
    for k in ("debt_date", "due_date"):
        if data.get(k):
            data[k] = data[k].isoformat()
    if not db.update_debt(did, data):
        raise HTTPException(404, "Kayıt yok")
    return {"ok": True}


@app.delete("/api/debts/{did}")
def delete_debt(did: int):
    if not db.delete_debt(did):
        raise HTTPException(404, "Kayıt yok")
    return {"ok": True}


@app.post("/api/debts/{did}/pay")
def pay_debt(did: int, body: DebtPayIn):
    try:
        db.pay_debt(did, body.amount, body.paid_at.isoformat(), account_id=body.account_id)
    except KeyError as e:
        raise HTTPException(404, "Kayıt yok") from e
    return {"ok": True}


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
    return db.transfers_overview(month, start, end)


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
        raise HTTPException(422, str(e)) from e


# ---------------------------------------------------------------- statements

@app.get("/api/statements")
def list_statements():
    return db.list_statements()


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
    from app import doc_reads
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
            results.append({**res, "status": "error", "message": str(e)})
            continue
        try:  # hazır sürüm: her yeni belge 1 okuma hakkı; doluysa bu dosya okunmaz
            doc_reads.check(data)
        except doc_reads.QuotaError as e:
            results.append({**res, "status": "error", "message": str(e), **e.fields()})
            continue
        if name.endswith((".xlsx", ".xls", ".csv")):  # tablo: kart işlem dökümü mü, hesap hareketleri mi?
            from app import card_excel

            try:
                imp = card_excel.import_file(data, f.filename or "kart.xlsx")
            except limits.LimitError as e:  # zip bombası vb.: hesap hareketleri akışına da gönderilmez
                results.append({**res, "status": "error", "message": str(e)})
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
            results.append({**res, "status": "error", "message": str(e)})
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
                    msg = f"Dekont: {d['amount']:,.2f} TL {d['kind']}" + (" (zaten vardı)" if d["status"] == "duplicate" else "")
                    if d["status"] not in ("duplicate", "updated"):
                        doc_reads.charge(data)
                else:  # handle_pdf yeni belgeyi kendisi sayar
                    msg = mailbox.handle_pdf(f.filename or "dekont.pdf", data)
                results.append({**res, "status": "saved", "message": msg, "dekont": True})
            except Exception as e:  # noqa: BLE001
                results.append({**res, "status": "error", "message": str(e)})
            continue
        if kind == "account":  # vadesiz hesap dökümü: arayüz hesap hareketleri önizlemesine yönlendirir
            if password and remember:
                db.remember_pdf_password(password)
            results.append({**res, "status": "account_statement"})
            continue
        try:
            stmt = parse_statement(data, passwords=tried)
        except PasswordRequired as e:
            results.append({**res, "status": "password_required", "message": str(e) if password else "PDF şifreli"})
            continue
        except Exception as e:  # noqa: BLE001 - kullanıcıya göster
            results.append({**res, "status": "error", "message": str(e)})
            continue
        if password and remember:
            db.remember_pdf_password(password)
        res.update(bank=stmt.bank, card_no=stmt.card_no, period_end=stmt.period_end.isoformat(),
                   total_debt=stmt.total_debt, txn_count=len(stmt.transactions),
                   reconciliation_diff=stmt.reconciliation_diff(), ai=stmt.ai_parsed)
        if db.statement_exists(data, stmt):
            results.append({**res, "status": "duplicate"})
            continue
        db.save_statement(data, f.filename or "ekstre.pdf", stmt)
        doc_reads.charge(data)  # yerel ayrıştırıcı ya da yapay zekâ yedek okuması: belge başına 1
        results.append({**res, "status": "saved", "linked": db.auto_link(), "marked_paid": db.auto_mark_paid()})
    from app import sync

    sync.reconcile()  # öğrenilmiş kurallarla eşleşen kart harcamaları (kalanlar Yapılacaklar'da sorulur)
    changed = db.auto_update_cycle()
    if changed and results:
        results[-1]["cycle_day"] = changed  # arayüz "ay dönümü X'e güncellendi" der
    if (warn := db.take_storage_warning()) and results:
        results[-1]["storage_warning"] = warn
    return results


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
def recurring_drift():
    return finance.recurring_drift()


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

    return book.info()


@app.get("/api/book")
def book_overview():
    from app import book

    u = _book_owner()
    cards = {}
    for s in db.list_statements():
        cards.setdefault(s["card_no"], {"card_no": s["card_no"], "bank": s["bank"]})
    return {"members": [{"id": m["id"], "username": m["username"], "last_login": m["last_login"], "disabled": bool(m["disabled"]),
                         "role": book.role(m), "own_rules": book.has_own_rules(m["id"]), "rules": book.rules(m["id"])}
                        for m in system.members(u["id"])],
            "invites": [i for i in system.invites() if i.get("member_of") == u["id"] and not i["used_at"]],
            "rules": book.rules(), "cards": list(cards.values()),
            "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"]} for a in db.list_accounts()],
            "pages": book.PAGE_LABELS, "roles": book.ROLE_LABELS}


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
def book_invite(body: InviteIn):
    u = _book_owner()
    if plans.seats_left(u["id"]) == 0:
        raise HTTPException(409, f"Aile paketi {plans.FAMILY_SEATS} kişilik; yer açmak için bir üyeyi ya da bekleyen daveti kaldır")
    return system.create_invite(body.note, body.days, member_of=u["id"], book_role=body.book_role)


@app.delete("/api/book/invites/{code}")
def book_delete_invite(code: str):
    u = _book_owner()
    inv = system.invite(code)
    if not inv or inv.get("member_of") != u["id"] or not system.delete_invite(code):
        raise HTTPException(404, "Davet yok ya da kullanılmış")
    return {"ok": True}


@app.delete("/api/book/members/{uid}")
def book_remove_member(uid: int):
    """Üyeyi çıkar: hesabı silinir (eklediği kayıtlar defterde kalır). Üyenin kendi ("Benim") defteri sahipsiz kalmasın
    diye users/_silinen altına taşınır; 30 gün sonra kendiliğinden silinir."""
    u = _book_owner()
    m = system.user(uid)
    if not m or m.get("member_of") != u["id"]:
        raise HTTPException(404, "Üye yok")
    system.delete_user(uid)
    system.archive_user_data(m)
    return {"ok": True}


@app.post("/api/book/members/{uid}/reset-password")
def book_reset_member_password(uid: int):
    u = _book_owner()
    m = system.user(uid)
    if not m or m.get("member_of") != u["id"]:
        raise HTTPException(404, "Üye yok")
    temp = auth.temp_password()
    auth.set_password(uid, temp, must_change=True)
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
        raise HTTPException(404, "Yok")
    return demo_mode.stats(max(1, min(days, 365)))


@app.get("/api/admin/demo-stats")
def admin_demo_stats(days: int = 7):
    """Yönetici: canlı demonun istatistikleri (demo container'ından, sunucu içinden çekilir)."""
    import httpx

    _require_admin()
    url, key = os.environ.get("DEMO_STATS_URL"), os.environ.get("DEMO_STATS_KEY")
    if not url or not key:
        return {"configured": False}
    try:
        r = httpx.get(url, params={"days": days}, headers={"x-stats-key": key}, timeout=10)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Demoya ulaşılamadı: {e}") from e
    return {"configured": True, **r.json()}


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
        raise HTTPException(404, "Yok")
    try:
        new = demo_mode.join_waitlist(request.cookies.get(demo_mode.COOKIE) or "", body.email, body.platform, body.note, body.tester)
    except demo_mode.WaitlistError as e:
        raise HTTPException(422, str(e)) from e
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
    data = f"demo-sample:{key}".encode()
    res = {"filename": s["label"], "bank": st.bank, "card_no": st.card_no, "period_end": st.period_end.isoformat(),
           "total_debt": st.total_debt, "txn_count": len(st.transactions), "reconciliation_diff": st.reconciliation_diff(),
           "ai": False}
    if db.statement_exists(data, st):
        return [{**res, "status": "duplicate"}]
    with undo.batch(f"Ekstre: {s['label']}"):
        db.save_statement(data, s["label"], st)
        linked = db.auto_link()
        db.auto_mark_paid()
    return [{**res, "status": "saved", "linked": linked}]


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


# ---------------------------------------------------------------- rules

class RuleIn(BaseModel):
    pattern: str = Field(min_length=1)
    category: str = Field(min_length=1)
    priority: int = 10


class RulePatch(BaseModel):
    pattern: str | None = None
    category: str | None = None
    priority: int | None = None


def _check_regex(p: str | None):
    import re

    if p is None:
        return
    try:
        re.compile(p)
    except re.error as e:
        raise HTTPException(422, f"Geçersiz regex: {e}") from e


@app.get("/api/rules")
def list_rules():
    from app import book

    return book.filter_rules(db.list_rules())  # ortak defter üyesi: gizli karttan öğrenilen kural görünmez


@app.post("/api/rules")
def create_rule(body: RuleIn):
    _check_regex(body.pattern)
    rid = db.add_rule(body.pattern, body.category, body.priority)
    return {"id": rid, "recategorized": db.recategorize_all()}


@app.patch("/api/rules/{rule_id}")
def patch_rule(rule_id: int, body: RulePatch):
    _check_regex(body.pattern)
    db.update_rule(rule_id, body.model_dump(exclude_unset=True))
    return {"ok": True, "recategorized": db.recategorize_all()}


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int):
    db.delete_rule(rule_id)
    return {"ok": True, "recategorized": db.recategorize_all()}


@app.get("/api/rules/test")
def test_rule(pattern: str, text: str):
    import re

    from app.textnorm import normalize

    _check_regex(pattern)
    return {"normalized": normalize(text), "match": bool(re.search(pattern, normalize(text)))}


# ---------------------------------------------------------------- receipt

@app.post("/api/receipts/parse")
@_heavy
def parse_receipt(file: UploadFile = File(...)):
    """Fiş fotoğrafını oku, kaydetmeden önce kullanıcıya önizleme döndür."""
    data = _file_bytes(file)
    mime = file.content_type or "image/jpeg"
    if not mime.startswith("image/"):
        raise HTTPException(422, "Sadece fotoğraf yüklenebilir")
    from app import doc_reads

    original = data
    data, mime, photo = _prepare_photo(data, mime)
    cats = sorted(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories()) - {"Ödeme", "Kategorisiz"})
    try:
        doc_reads.check(original)  # hazır sürüm: belge okuma hakkı doluysa okunmaz
    except doc_reads.QuotaError as e:  # fotoğraf okunmadan saklanır: elle girilen gidere bağlanır
        raise QuotaFull(502, e, receipt_file=_store_receipt(photo)) from e
    try:
        out = receipt.parse_receipt(data, mime, cats)
    except receipt.ReceiptError as e:
        raise HTTPException(502, str(e)) from e
    doc_reads.charge(original)
    out["check"] = receipt_check.check(out.get("items") or [], out.get("total"))
    _auto_reread(out, data, mime)
    out["receipt_file"] = _store_receipt(photo)
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
    last = {g["name"]: g["amount"] for g in finance.summary(finance.reference_month())["by_category"]}
    return {"limits": db.list_budgets(), "status": finance.budgets(m), "last_month": last}


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
def payables(totals: bool = False):
    """Ödenecekler (liste; eski istemciler için değişmedi). totals=1: {"items": [...], "totals": {...}}: istemci toplamı
    kendisi hesaplamasın (finance.payables_totals). Kalemde in_total: toplama girer mi; rolled_into: kalanı bu id'li
    son ekstreye devretmiş ödenmemiş eski ekstre (gecikmiş bilgisi)."""
    items = finance.payables()
    return {"items": items, "totals": finance.payables_totals(items=items)} if totals else items


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
        raise HTTPException(422, str(e)) from e
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
def parse_bill(file: UploadFile = File(...)):
    """Fatura PDF/fotoğrafını sakla ve oku; kullanıcı onaylayınca /api/bills ile kaydedilir."""
    data = _file_bytes(file)
    mime = file.content_type or ""
    if mime not in BILL_TYPES:
        raise HTTPException(422, "Sadece PDF veya fotoğraf yüklenebilir")
    from app import doc_reads

    read, read_mime, photo = _prepare_photo(data, mime)
    name = db.save_bill_file(photo, ".jpg") if photo else db.save_bill_file(data, BILL_TYPES[mime])
    try:
        if receipt.enabled():
            doc_reads.check(data)  # hazır sürüm: hak doluysa dosya saklanır ama okunmaz (elle girilir)
        parsed = receipt.parse_bill(read, read_mime) if receipt.enabled() else {}
    except receipt.ReceiptError as e:
        parsed = {"error": str(e), **(e.fields() if isinstance(e, doc_reads.QuotaError) else {})}
    if parsed and "error" not in parsed:
        doc_reads.charge(data)
    warn = db.take_storage_warning()
    return {**parsed, "file_name": name, **({"storage_warning": warn} if warn else {})}


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


@app.post("/api/inbox/{iid}/{action}")
def shared_inbox_close(iid: int, action: Literal["kaydedildi", "silindi"]):
    from app import share as share_mod

    if not share_mod.close(iid, action):
        raise HTTPException(404, "Yok")
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
        raise HTTPException(404, "Yok")
    return {"ok": True}


@app.post("/api/documents/parse")
@_heavy
def parse_document(file: list[UploadFile] = File(...)):
    """Fiş veya fatura (foto/PDF): türünü kendisi anlar. Faturaysa dosya saklanır.
    Birden fazla dosya: uzun fişin sırayla çekilmiş parçaları (en fazla 4)."""
    files = file[:4]
    data = _file_bytes(files[0])
    for f in files:
        limits.check_size(_file_bytes(f), f.filename)
    mime = files[0].content_type or ""
    if mime not in BILL_TYPES or any((f.content_type or "") not in BILL_TYPES for f in files[1:]):
        raise HTTPException(422, "Sadece PDF veya fotoğraf yüklenebilir")
    more = [(_file_bytes(f), f.content_type or "") for f in files[1:]]
    return _read_document(data, mime, more)


def _read_document(data: bytes, mime: str, more: list | None = None) -> dict:
    """Fotoğraflar okunmadan önce küçültülür ve EXIF'ten arındırılır; fişin (ilk) fotoğrafı saklanır."""
    import hashlib

    from app import doc_reads

    original, original_mime = data, mime
    # çok sayfalı (birden çok fotoğraflı) fiş tek belgedir: anahtar bütün parçaların özeti
    key = hashlib.sha256(b"".join(hashlib.sha256(d).digest() for d in [data, *(d for d, _ in (more or []))])).digest() \
        if more else data
    data, mime, photo = _prepare_photo(data, mime)
    more = [_prepare_photo(d, m)[:2] for d, m in (more or [])] or None
    cats = sorted(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories()) - {"Ödeme", "Kategorisiz"})
    try:
        doc_reads.check(key)  # hazır sürüm: belge okuma hakkı doluysa okunmaz
    except doc_reads.QuotaError as e:  # fiş fotoğrafı okunmadan saklanır: elle girilen gidere bağlanır
        raise QuotaFull(502, e, receipt_file=_store_receipt(photo)) from e
    try:
        doc = receipt.parse_document(data, mime, cats, sorted({r["category"] for r in db.list_item_rules()}), more=more)
    except receipt.ReceiptError as e:
        raise HTTPException(502, str(e)) from e
    doc_reads.charge(key)
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
    if doc["type"] == "bill":
        doc["file_name"] = db.save_bill_file(photo, ".jpg") if photo else \
            db.save_bill_file(original, BILL_TYPES.get(original_mime, ".pdf"))
    else:
        doc["receipt_file"] = _store_receipt(photo)
        doc["check"] = receipt_check.check(doc.get("items") or [], doc.get("total"))
        _auto_reread(doc, data, mime, more)
    if warn := db.take_storage_warning():
        doc["storage_warning"] = warn
    return doc


# ---------------------------------------------------------------- kayıtlı fişin kalemleri ve yeniden okuma

_AUTO_REREAD_PER_DAY = 30  # kullanıcı başına günlük otomatik yeniden okuma (maliyet sınırı; belge hakkı düşmez)


def _check_score(chk: dict) -> tuple[int, float]:
    return sum(1 for i in chk["items"] if i["mismatch"]), abs(chk["diff"]) if chk["diff"] is not None else 0.0


def _auto_reread(doc: dict, data: bytes, mime: str, more: list | None = None) -> None:
    """Kademeli okuma: kalemler fişi tutmuyorsa (check.ok false) aynı istekte bir kez daha güçlü modelle oku; daha iyiyse
    onun kalemlerini kullan. Belge hakkı düşmez; kullanıcı başı günlük _AUTO_REREAD_PER_DAY; model hatasında ilk sonuç.
    doc['reread'] = {tried, used, model}."""
    import json as _json
    from datetime import date as _date

    from app import system

    chk = doc.get("check") or {}
    if chk.get("ok", True) or not doc.get("items") or not receipt.enabled():
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
    try:
        again = receipt.reread_items(data, mime, doc.get("total"), more)
    except Exception:  # noqa: BLE001 - yeniden okuma olmadıysa ilk okuma geçerli
        import logging

        logging.getLogger("ekstre.receipt").warning("otomatik yeniden okuma başarısız", exc_info=True)
        return
    chk2 = receipt_check.check(again["items"], doc.get("total"))
    (m1, d1), (m2, d2) = _check_score(chk), _check_score(chk2)
    if again["items"] and ((m2 < m1 and d2 <= d1) or (m2 <= m1 and d2 < d1)):
        doc["items"], doc["check"] = again["items"], chk2
        doc["reread"]["used"] = True

_REREAD_PER_DAY = 3  # aynı fotoğraf için günde (yeniden okuma belge hakkı düşürmez; yapay zekâ maliyeti sınırlı kalsın)


def _entry_for_receipt(entry_id: int) -> dict:
    from app import book

    with db.connect() as c:
        r = c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not r or (book.member() and not book.can_see_entry(entry_id)):
        raise HTTPException(404, "Kayıt yok")
    return dict(r)


def _receipt_items_out(entry_id: int, total: float | None) -> dict:
    with db.connect() as c:
        items = [dict(x) for x in c.execute(
            "SELECT id, name, product, brand, qty, unit, amount, unit_price, price_known FROM receipt_items "
            "WHERE entry_id = ? ORDER BY id", (entry_id,))]
    # saklanan unit_price tutar / adet'ten türetilmiştir: adet × birim denetimi burada anlamsız, yalnız toplam denetlenir
    return {"entry_id": entry_id, "items": items,
            "check": receipt_check.check([{**i, "unit_price": None} for i in items], total)}


@app.get("/api/entries/{entry_id}/receipt-items")
def get_receipt_items(entry_id: int):
    """Kayıtlı fişin kalemleri ve denetimi (toplam tutuyor mu)."""
    e = _entry_for_receipt(entry_id)
    return _receipt_items_out(entry_id, e["amount"])


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
    return {"saved": n, **_receipt_items_out(entry_id, e["amount"])}


def _reread_photo(name: str | None, total: float | None) -> dict:
    """Saklı fotoğrafı daha güçlü modelle yeniden oku. Belge okuma hakkı DÜŞMEZ (aynı belge ilk okumada sayıldı);
    kötüye kullanım için fotoğraf başına günde _REREAD_PER_DAY. Sonuç kaydedilmez."""
    import json as _json
    from datetime import date as _date

    from app import storage

    if not name or not storage.valid_name("receipts", name) or not db.file_indexed("receipts", name):
        raise HTTPException(404, "Fiş fotoğrafı yok")
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
        data = db.read_file("receipts", name)
    except storage.StorageError as e:
        raise HTTPException(503, "Fotoğraf şu an alınamadı") from e
    if not data:
        raise HTTPException(404, "Fiş fotoğrafı yok")
    try:
        out = receipt.reread_items(data, "image/jpeg", total)
    except receipt.ReceiptError as e:
        raise HTTPException(502, str(e)) from e
    db.set_setting(key, _json.dumps({"day": today, "n": used + 1}))
    out["check"] = receipt_check.check(out["items"], total if total is not None else out.get("total"))
    out["receipt_file"] = name
    return out


@app.post("/api/entries/{entry_id}/receipt-reread")
@_heavy
def reread_entry_receipt(entry_id: int):
    """Kayıtlı giderin fiş fotoğrafını kaydın tutarıyla yeniden oku; istemci onaylarsa PUT …/receipt-items ile yazar."""
    e = _entry_for_receipt(entry_id)
    return _reread_photo(e.get("receipt_file"), e["amount"])


class RereadIn(BaseModel):
    receipt_file: str
    total: Money | None = None


@app.post("/api/receipts/reread")
@_heavy
def reread_receipt(body: RereadIn):
    """Kaydetmeden önce (önizleme): okumadan dönen receipt_file adıyla yeniden oku."""
    return _reread_photo(body.receipt_file, body.total)


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
    data = body.model_dump()
    data["due_date"] = body.due_date.isoformat()
    return {"id": db.create_bill(data)}


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
        raise HTTPException(404, str(e)) from e


@app.delete("/api/bills/{bid}/pay")
def unpay_bill(bid: int):
    db.unpay_bill(bid)
    return {"ok": True}


@app.get("/api/bills/{bid}/file")
def bill_file(bid: int):
    b = db.get_bill(bid)
    if not b or not b["file_name"]:
        raise HTTPException(404)
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
        raise HTTPException(400, str(e)) from e


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
            raise HTTPException(502, f"Google'a ulaşılamadı: {e}") from e
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
            raise HTTPException(400, str(e)) from e
    if name == "TELEGRAM_BOT_TOKEN":
        try:
            me = telegram._call("getMe")
        except telegram.TelegramError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "detail": f"Bot: @{me.get('username')}"}
    raise HTTPException(404)


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
        raise HTTPException(400, str(e)) from e


@app.post("/api/settings/telegram/connect")
def telegram_connect():
    try:
        info = telegram.discover_chat()
        telegram.send("✅ HesapKitap bağlandı. Son ödeme hatırlatmaları ve günlük şifreli yedek buraya gelecek.")
        return info
    except telegram.TelegramError as e:
        raise HTTPException(400, str(e)) from e


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
        raise HTTPException(400, str(e)) from e


@app.post("/api/settings/telegram/report")
def telegram_report(month: str | None = None):
    """Biten (ya da verilen) dönemin raporunu şimdi gönder; Ayarlar'dan denemek için."""
    m = month or finance.add_months(finance.current_period(), -1)
    try:
        telegram.send(finance.period_report(m))
    except telegram.TelegramError as e:
        raise HTTPException(400, str(e)) from e
    return {"month": m}


@app.post("/api/settings/backup/now")
def backup_now():
    try:
        return {"path": os.path.basename(jobs.run_backup(date.today()))}
    except telegram.TelegramError as e:
        raise HTTPException(502, f"Yedek alındı ama Telegram'a gönderilemedi: {e}") from e


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


@app.post("/api/settings/backup/restore")
@_heavy
def backup_restore(file: UploadFile = File(...), passphrase: str | None = Form(None)):
    try:
        return backup.restore(_file_bytes(file), file.filename or "", passphrase)
    except backup.RestoreError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/settings/backup/{name}/restore")
@_heavy
def backup_restore_named(name: str):
    if name not in {b["name"] for b in backup.list_backups()}:
        raise HTTPException(404)
    with open(os.path.join(backup.backup_dir(), name), "rb") as f:
        data = f.read()
    try:
        return backup.restore(data, name)
    except backup.RestoreError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/settings/backup/{name}")
def backup_download(name: str):
    if name not in {b["name"] for b in backup.list_backups()}:
        raise HTTPException(404)
    return FileResponse(os.path.join(backup.backup_dir(), name), filename=name, media_type="application/octet-stream")


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
            raise HTTPException(404)
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
            raise HTTPException(404)
        f = os.path.realpath(os.path.join(_dist, path))
        if path and f.startswith(_dist + os.sep) and os.path.isfile(f):
            # service worker ve manifest her açılışta tazelensin
            headers = {"Cache-Control": "no-cache"} if path in ("sw.js", "manifest.webmanifest") else None
            return FileResponse(f, headers=headers)
        return FileResponse(os.path.join(_dist, "index.html"), headers={"Cache-Control": "no-cache"})


# en dışta olmalı (en son eklenen en dışta çalışır): ön istek (OPTIONS) ve 401 yanıtları da CORS başlığı alsın
app.add_middleware(CORSMiddleware, allow_origins=APP_ORIGINS, allow_credentials=False, allow_methods=["*"],
                   allow_headers=["Authorization", "Content-Type", "X-HK-Client", "X-Demo-Sid", "X-HK-Book"],
                   expose_headers=["X-Session-Token", "X-Demo-Sid"], max_age=86400)
