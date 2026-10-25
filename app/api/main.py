"""FastAPI JSON API. Frontend (web/) /api altından konuşur.
Production'da WEB_DIST tanımlıysa derlenmiş React uygulamasını da aynı porttan sunar."""
import asyncio
import mimetypes
import os
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auth, backup, config, db, donations, finance, jobs, ocr, receipt, system, telegram, zakat
from app.categorize import CATEGORIES, suggest_pattern
from app.parsers import parse_statement
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND

db.init_db()
auth.ensure_admin()


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(jobs.scheduler()) if os.environ.get("JOBS_ENABLED", "1") == "1" else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="HesapKitap", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def client_ip(request: Request) -> str:
    # Cloudflare arkasında gerçek IP bu header'da
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "?"))


from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=1000)  # JS ~880 KB → ~250 KB; API yanıtları da küçülür


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    user = None
    public = path.startswith("/api/auth/") or path == "/api/templates/submit"  # şablon gönderimi: oturumsuz (hub)
    if auth.enabled() and path.startswith("/api/") and not public:
        if auth.setup_required():
            return JSONResponse({"detail": "setup_required"}, status_code=401)
        user = auth.token_user(request.cookies.get(auth.COOKIE))
        if not user and path == "/api/share":  # telefondan Paylaş (iOS Kısayollar): kişisel anahtar, sadece bu uçta
            bearer = request.headers.get("authorization", "")
            user = system.token_owner(bearer[7:].strip()) if bearer.lower().startswith("bearer ") else None
        if not user:
            return JSONResponse({"detail": "Giriş gerekli"}, status_code=401)
        # yöneticinin verdiği geçici şifreyle girenler önce kendi şifresini belirler
        if user["must_change_password"] and path != "/api/settings/password":
            return JSONResponse({"detail": "password_change_required"}, status_code=403)
    if user:  # isteğin tamamı bu kullanıcının veritabanında çalışır
        with db.use_space(system.user_db_path(user), user):
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


def _start_session(request: Request, response: Response, user: dict) -> None:
    # HTTPS arkasındaysa (doğrudan ya da proxy başlığıyla) çerez otomatik Secure olur
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(auth.COOKIE, auth.make_token(system.user(user["id"])), max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, secure=auth.cookie_secure() or https, samesite="lax", path="/")


@app.get("/api/auth/status")
def auth_status(request: Request):
    if not auth.enabled():
        return {"required": False, "authenticated": True, "setup_required": False}
    if auth.setup_required():
        local = auth.is_local(client_ip(request))
        if not local:
            auth.setup_code()  # log'a yazılsın
        return {"required": True, "authenticated": False, "setup_required": True, "setup_code_needed": not local}
    u = auth.token_user(request.cookies.get(auth.COOKIE))
    return {"required": True, "authenticated": bool(u), "setup_required": False,
            "password_source": auth.password_source(u) if u else None,
            "user": {"username": u["username"], "role": u["role"], "must_change_password": bool(u["must_change_password"])} if u else None}


@app.post("/api/auth/setup")
def setup(body: SetupIn, request: Request, response: Response):
    """İlk açılış: şifre belirle. Sadece hiç şifre yokken çalışır."""
    if not auth.setup_required():
        raise HTTPException(409, "Şifre zaten belirlenmiş")
    ip = client_ip(request)
    if not auth.is_local(ip):
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
    u = auth.login(body.username, body.password)
    if not u:
        auth.record_fail(ip)
        raise HTTPException(401, "Kullanıcı adı ya da şifre yanlış" if body.username else "Şifre yanlış")
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
    code: str
    username: str
    password: str


@app.get("/api/auth/invite/{code}")
def invite_check(code: str):
    return {"valid": system.valid_invite(code)}


@app.post("/api/auth/register")
def register(body: RegisterIn, request: Request, response: Response):
    """Davet koduyla hesap aç. Kod tek kullanımlık; her kullanıcının verisi kendi dosyasında."""
    ip = client_ip(request)
    if auth.rate_limited(ip):
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar dene.")
    if not system.valid_invite(body.code):
        auth.record_fail(ip)
        raise HTTPException(403, "Davet kodu geçersiz, kullanılmış ya da süresi dolmuş")
    try:
        u = auth.create_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not system.use_invite(body.code, u["id"]):  # aynı anda iki kayıt: ikincisi geri alınır
        system.delete_user(u["id"])
        raise HTTPException(409, "Davet kodu az önce kullanıldı")
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
            "ai_used": system.ai_used(u["id"]), "ai_quota": system.ai_quota(u), "ai_quota_custom": u["ai_quota"]}


class AdminUserIn(BaseModel):
    username: str


class AdminUserPatch(BaseModel):
    disabled: bool | None = None
    ai_quota: int | None = Field(None, ge=0, le=100000)
    reset_quota: bool = False  # True: varsayılan kotaya dön


class InviteIn(BaseModel):
    note: str | None = None
    days: int = Field(14, ge=1, le=90)


@app.get("/api/admin/users")
def admin_users():
    _require_admin()
    return {"users": [_user_row(u) for u in system.users()], "invites": system.invites(),
            "default_quota": system.ai_quota({"role": "user"})}


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
    """Hesabı siler; veri dosyası hemen silinmez, kenara (users/_silinen) taşınır."""
    _require_admin()
    u = system.user(uid)
    if not u or u["role"] == "admin":
        raise HTTPException(404, "Kullanıcı yok")
    system.delete_user(uid)
    system.archive_user_data(u)
    return {"ok": True}


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
    """Yapay zekâ kullanımı: ortak anahtarla aylık kota; kendi anahtarı varsa sınırsız."""
    u = db.current_user()
    if not u or u["role"] == "admin":
        return {"used": None, "quota": None, "own_key": False}
    own = bool(config.own_ai_key())
    return {"used": system.ai_used(u["id"]), "quota": None if own else system.ai_quota(u), "own_key": own,
            "shared_available": bool(system.get("cfg:GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY"))}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}

Kind = Literal["gelir", "gider"]
Payment = Literal["kredi_karti", "nakit", "havale", "kart"]  # kart: ön ödemeli (yemek/hediye/giyim), wallet_id ile
CardKind = Literal["harcama", "iade", "faiz_ucret", "odeme"]
EXPENSE_CATEGORIES = ["Kira & Aidat", "Faturalar", "Elektrik", "Doğalgaz", "Su", "İnternet & TV", "Telefon", "Sağlık",
                      "Eğitim", "Ulaşım & Araç", "Market & Gıda", "Kredi & Borç", "Bağış & Sadaka", "Diğer"]
INCOME_CATEGORIES = ["Maaş", "Ek Gelir", "Kira Geliri", "Yatırım", "Diğer Gelir"]


# ---------------------------------------------------------------- meta

def _by_usage(cats: set[str]) -> list[str]:
    usage = db.category_usage()
    return sorted(cats, key=lambda c: (-usage.get(c, 0), c))


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
    }


@app.get("/api/summary")
def summary(month: str):
    return finance.summary(month)


@app.get("/api/transactions")
def transactions(month: str | None = None, include_deleted: bool = False):
    return finance.transactions(month, include_deleted)


@app.get("/api/plan")
def plan(months: int = 6):
    return finance.plan(max(1, min(months, 24)))


class SimulateIn(BaseModel):
    amount: float = Field(gt=0)
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
    create_rule: bool = False  # kategori değişince aynı işyeri için kural oluştur


@app.patch("/api/card-transactions/{txn_id}")
def patch_card_txn(txn_id: int, body: CardTxnPatch):
    fields = body.model_dump(exclude_unset=True, exclude={"create_rule"})
    if "is_deleted" in fields:
        fields["is_deleted"] = int(fields["is_deleted"])
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
    amount: float = Field(ge=0)
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


class EntryPatch(BaseModel):
    entry_date: date | None = None
    description: str | None = None
    amount: float | None = Field(default=None, ge=0)
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


@app.post("/api/entries")
def create_entry(body: EntryIn):
    data = body.model_dump(exclude={"legal_name", "splits", "items", "branch"})
    data["entry_date"] = body.entry_date.isoformat()
    data["skipped"] = int(body.skipped)
    if body.legal_name and body.kind == "gider":
        db.learn_from_receipt(body.legal_name, body.description, body.category)
    if data.get("counterparty"):
        data["counterparty"] = db.known_counterparty(data["counterparty"].strip())
    _check_wallet(data)
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
    debt = db.link_debt_movement(data.get("counterparty"), body.kind, body.amount, data["entry_date"], eid)
    return {"id": eid, "linked": any(e["id"] == eid and e["linked_txn_id"] for e in db.list_entries()),
            "debt_settled": debt[1]["id"] if debt else None, "debt_step": debt[0] if debt else None,
            "splits": [{"category": x["category"], "amount": x["amount"]} for x in splits]}


@app.patch("/api/entries/{entry_id}")
def patch_entry(entry_id: int, body: EntryPatch):
    data = body.model_dump(exclude_unset=True)
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
    if "amount" in data and data["amount"] + 0.005 < split_total:
        raise HTTPException(422, f"Bu kayıt bölünmüş ({split_total:.2f} alt kalem); tutar bundan az olamaz")
    db.update_entry(entry_id, data)
    db.auto_link()
    return {"ok": True}


class LinkIn(BaseModel):
    txn_id: int | None  # None = eşleşmeyi kaldır


@app.get("/api/entries/{entry_id}/candidates")
def link_candidates(entry_id: int):
    return db.link_candidates(entry_id)


@app.post("/api/entries/{entry_id}/link")
def link_entry(entry_id: int, body: LinkIn):
    try:
        db.link_entry(entry_id, body.txn_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int):
    db.delete_entry(entry_id)
    return {"ok": True}


# ---------------------------------------------------------------- recurring

class RecurringIn(BaseModel):
    description: str = Field(min_length=1)
    amount: float = Field(ge=0)
    kind: Kind
    category: str | None = None
    day_of_month: int = Field(default=1, ge=1, le=31)
    start_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    end_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    note: str | None = None
    gross_amount: float | None = Field(default=None, ge=0)
    advance_pay: bool | None = None  # maaş peşin (memur): zam farkı ilk zamlı maaşta
    account_id: int | None = None  # banka/nakit hesabı


class RecurringPatch(BaseModel):
    description: str | None = None
    amount: float | None = Field(default=None, ge=0)
    kind: Kind | None = None
    category: str | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    start_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    end_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    note: str | None = None
    gross_amount: float | None = Field(default=None, ge=0)
    advance_pay: bool | None = None  # maaş peşin (memur): zam farkı ilk zamlı maaşta
    account_id: int | None = None  # banka/nakit hesabı


class RecurringAction(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: float | None = Field(default=None, ge=0)  # o ay farklı tutar olduysa


@app.get("/api/payroll/net")
def payroll_net(gross: float, year: int | None = None, start_month: int = 1):
    """Brütten nete ay ay döküm (2026 parametreleri; tanımsız yıl için en yakın yıl)."""
    from app.payroll import net_by_month

    if gross <= 0 or not 1 <= start_month <= 12:
        raise HTTPException(422, "Geçersiz değer")
    return net_by_month(gross, year or date.today().year, start_month)


class SalaryChangeIn(BaseModel):
    from_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    gross: float = Field(gt=0)
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
    r = _recurring_or_404(rid)
    return {"id": db.create_entry({
        "entry_date": db.occurrence_date(body.month, db.day_at(r, body.month)), "month": body.month, "description": r["description"],
        "amount": body.amount if body.amount is not None else db.planned_amount(r, body.month), "kind": r["kind"],
        "category": r["category"], "recurring_id": rid, "skipped": 0, "account_id": r["account_id"],
    })}


class MonthAmount(BaseModel):
    amount: float | None = Field(default=None, ge=0)  # None = aya özel tutarı kaldır (şablona dön)


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


_PDF_CACHE: dict[str, dict] = {}  # önizleme ile içe aktarma arasında yapay zekâ çağrısını tekrarlamamak için


def _owners() -> list[str]:
    """Hesap sahibi adları: kart sahipleri + dökümlerden öğrenilen hesap sahibi (kendi hesapları arası virman)."""
    return db.card_holders() + db.owner_names()


async def _read_bank_pdf(file: UploadFile, password: str | None = None) -> tuple[list[dict], dict]:
    import hashlib

    from app import bank_import, bank_pdf

    data = await file.read()
    key = hashlib.sha256(data).hexdigest()
    if key not in _PDF_CACHE:
        try:
            _PDF_CACHE[key] = bank_pdf.parse_account_pdf(data, ([password] if password else []) + db.pdf_passwords())
        except (bank_pdf.BankPdfError, receipt.ReceiptError) as e:
            raise HTTPException(422, str(e)) from e
        while len(_PDF_CACHE) > 8:
            _PDF_CACHE.pop(next(iter(_PDF_CACHE)))
    res = _PDF_CACHE[key]
    owners = [res.get("owner_name") or "", *_owners()]
    meta = {k: res[k] for k in ("bank", "owner_name", "verified", "chain_bad", "closing_ok")}
    return bank_import.finalize(res["rows"], owners), meta


async def _read_bank_file(file: UploadFile):
    from app import bank_import

    data = await file.read()
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


@app.post("/api/bank-import/preview")
async def bank_import_preview(file: UploadFile = File(...), mapping: str | None = Form(None),
                              header_row: int | None = Form(None), password: str | None = Form(None)):
    """Sütunları tahmin et (ya da verilen eşlemeyi kullan), ilk satırları ve özetini döndür."""
    import json

    from app import bank_import

    if (file.filename or "").lower().endswith(".pdf"):
        parsed, meta = await _read_bank_pdf(file, password)
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
        return {"pdf": True, **meta, "header_row": 0, "columns": [], "mapping": {}, "profile_saved": False,
                "total": len(parsed), "counts": counts, "sample": parsed[:200], "raw_preview": []}

    rows = await _read_bank_file(file)
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
    return {**det, "profile_saved": bool(saved), "total": len(parsed), "counts": counts, "sample": parsed[:15],
            "raw_preview": rows[det["header_row"]: det["header_row"] + 6], "warning": warning}


@app.post("/api/bank-import")
async def bank_import_run(file: UploadFile = File(...), mapping: str = Form("{}"), header_row: int = Form(0),
                          password: str | None = Form(None)):
    import json

    from app import bank_import

    account = ""
    if (file.filename or "").lower().endswith(".pdf"):
        parsed, meta = await _read_bank_pdf(file, password)
        account = meta.get("bank") or ""
        if meta.get("owner_name"):
            db.remember_owner_name(meta["owner_name"])  # e-posta bildirimlerinde de "kendin" tanınsın
        # kişi ayrımında "kendin" = yalnızca döküm sahibi; ek kart sahipleri (eş, çocuk) Transferler'de kişi olarak görünsün
        owners = [meta.get("owner_name") or ""] if meta.get("owner_name") else _owners()
    else:
        owners = _owners()
        rows = await _read_bank_file(file)
        m = json.loads(mapping)
        parsed = bank_import.parse(rows, m, header_row, _owners())
        if parsed:
            db.set_setting(_bank_profile_key(rows[header_row]), json.dumps(m))  # bu banka biçimini hatırla
    if not parsed:
        raise HTTPException(422, "İçe aktarılacak satır bulunamadı; sütun eşlemesini kontrol et")
    from app import undo

    with undo.batch(f"Hesap dökümü: {file.filename or account or 'dosya'}") as b:
        stats = db.import_bank_rows(parsed, account=account, owners=owners)
        db.auto_link()
        b["summary"] = stats
    return stats


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
    balance: float = 0
    expires_at: date | None = None
    monthly_amount: float | None = Field(default=None, gt=0)
    load_day: int = Field(default=1, ge=1, le=31)


class WalletPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    card_type: Literal["yemek", "hediye", "giyim", "diger"] | None = None
    balance: float | None = None
    expires_at: date | None = None


class WalletSchedule(BaseModel):
    amount: float = Field(gt=0)
    day: int = Field(default=1, ge=1, le=31)
    from_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class WalletStop(BaseModel):
    mode: Literal["now", "after_this", "resume"]


class WalletLoad(BaseModel):
    amount: float = Field(gt=0)
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
    balance: float
    as_of: date | None = None


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    kind: Literal["banka", "nakit", "diger"] | None = None
    balance: float | None = None
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
def onboarding():
    """Başlarken: hangi adımlar tamam (Özet'teki rehber kartı)."""
    from app import mailbox, telegram

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


@app.post("/api/onboarding/dismiss")
def onboarding_dismiss():
    db.set_setting("onboarding_dismissed", "1")
    return {"ok": True}


class JarIn(BaseModel):
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
    cost: float | None = Field(None, gt=0)
    estimated: bool = False


class ZakatAssetPatch(BaseModel):
    person_id: int | None = None
    kind: str | None = None
    quantity: float | None = Field(None, gt=0)
    is_jewelry: bool | None = None
    note: str | None = None
    acquired: date | None = None
    cost: float | None = Field(None, gt=0)
    estimated: bool | None = None


class ZakatPaymentIn(BaseModel):
    amount: float = Field(gt=0)
    pay_date: date | None = None
    note: str | None = None
    year_start: date | None = None  # geçmiş bir yılın ödemesi


class ZakatSettingsIn(BaseModel):
    madhhab: Literal["hanefi", "safii"] | None = None
    include_app: bool | None = None
    include_receivables: bool | None = None
    manual_gram: float | None = Field(None, ge=0)  # 0: elle fiyatı kaldır


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
    return {"ok": True}


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
    db.set_account_balance(body.name.strip(), body.balance, (body.as_of or date.today()).isoformat(), "elle", body.kind)
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
    amount: float = Field(gt=0)
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
    total: float | None = None


@app.post("/api/splits/propose")
def propose_split(body: SplitProposalIn):
    """Fiş okununca formda gösterilecek bölme önerisi; kaydetmez."""
    return db.propose_split([i for i in body.items if isinstance(i, dict)], body.category, body.total)


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    amount: float = Field(gt=0)
    deadline: date
    already: float = Field(default=0, ge=0)


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


class DebtPatch(BaseModel):
    person: str | None = None
    direction: Literal["borc", "alacak"] | None = None
    asset: str | None = None
    amount: float | None = Field(default=None, gt=0)
    debt_date: date | None = None
    due_date: date | None = None
    note: str | None = None
    account_id: int | None = None  # banka/nakit hesabı


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
def transfers():
    return db.transfers_overview()


@app.put("/api/transfers/people/{name}")
def set_person(name: str, body: CounterpartyIn):
    return {"updated": db.set_counterparty(name, body.category, body.track_debt, body.note)}


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

    for pw in [None, *passwords]:
        try:
            text = extract_text(data, pw)
        except Exception:  # noqa: BLE001 - şifreli: sıradaki
            continue
        return classify(text)
    return None


@app.post("/api/statements/upload")
async def upload_statements(files: list[UploadFile] = File(...), password: str | None = Form(None),
                            remember: bool = Form(False),
                            kind: Literal["card", "account", "dekont"] | None = Form(None)):
    from app import undo

    names = ", ".join(f.filename or "dosya" for f in files)
    with undo.batch(f"Ekstre: {names[:120]}") as b:
        results = await _upload_statements(files, password, remember, kind)
        b["summary"] = [{k: r.get(k) for k in ("filename", "status", "bank", "card_no", "period_end", "txn_count",
                                                "message")} for r in results]
    return results


async def _upload_statements(files: list[UploadFile], password: str | None, remember: bool,
                             forced: str | None = None) -> list[dict]:
    from app.parsers import PasswordRequired

    results = []
    tried = ([password] if password else []) + db.pdf_passwords()
    for f in files:
        data = await f.read()
        res = {"filename": f.filename}
        name = (f.filename or "").lower()
        if name.endswith((".xlsx", ".xls", ".csv")):  # tablo: kart işlem dökümü mü, hesap hareketleri mi?
            from app import card_excel

            try:
                imp = card_excel.import_file(data, f.filename or "kart.xlsx")
            except Exception:  # noqa: BLE001 - okunamazsa hesap hareketleri akışı denesin
                imp = None
            if imp:
                results.append({**res, "status": "saved", "bank": imp["bank"], "card_no": imp["card_no"],
                                "card_excel": imp, "txn_count": imp["transactions"],
                                "message": f"Kart dökümü: {imp['statements']} aylık ekstre, {imp['transactions']} işlem"})
            else:
                results.append({**res, "status": "account_statement"})
            continue
        guess = _statement_kind(data, tried)
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
                else:
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
        results.append({**res, "status": "saved", "linked": db.auto_link(), "marked_paid": db.auto_mark_paid()})
    changed = db.auto_update_cycle()
    if changed and results:
        results[-1]["cycle_day"] = changed  # arayüz "ay dönümü X'e güncellendi" der
    return results


@app.delete("/api/statements/{sid}")
def delete_statement(sid: int):
    db.delete_statement(sid)
    return {"ok": True}


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
    return db.list_rules()


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
async def parse_receipt(file: UploadFile = File(...)):
    """Fiş fotoğrafını oku, kaydetmeden önce kullanıcıya önizleme döndür."""
    data = await file.read()
    mime = file.content_type or "image/jpeg"
    if not mime.startswith("image/"):
        raise HTTPException(422, "Sadece fotoğraf yüklenebilir")
    cats = sorted(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories()) - {"Ödeme", "Kategorisiz"})
    try:
        return receipt.parse_receipt(data, mime, cats)
    except receipt.ReceiptError as e:
        raise HTTPException(502, str(e)) from e


# ---------------------------------------------------------------- tasarruf

class SubState(BaseModel):
    merchant: str = Field(min_length=1)
    status: Literal["aktif", "iptal", "dusuruldu"]
    changed_at: date | None = None
    new_amount: float | None = Field(default=None, ge=0)  # paket düşürüldüyse yeni aylık tutar
    note: str | None = None


class GoalIn(BaseModel):
    kind: Literal["category", "merchant"]
    name: str = Field(min_length=1)
    baseline: float = Field(ge=0)
    target: float = Field(ge=0)
    start_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    note: str | None = None


class GoalPatch(BaseModel):
    target: float | None = Field(default=None, ge=0)
    baseline: float | None = Field(default=None, ge=0)
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
    monthly_limit: float | None = Field(default=None, ge=0)  # boş/0 = kaldır


@app.get("/api/budgets")
def list_budgets(month: str | None = None):
    m = month or finance.current_period()
    # öneri için son ekstre ayının kategori harcaması
    last = {g["name"]: g["amount"] for g in finance.summary(finance.reference_month())["by_category"]}
    return {"limits": db.list_budgets(), "status": finance.budgets(m), "last_month": last}


@app.put("/api/budgets")
def put_budget(body: BudgetIn):
    db.set_budget(body.category, body.monthly_limit)
    return {"ok": True}


# ---------------------------------------------------------------- ödemeler + faturalar

class PayIn(BaseModel):
    amount: float = Field(ge=0)
    paid_at: date
    payment: Payment | None = None  # sadece fatura için


@app.get("/api/payables")
def payables():
    return finance.payables()


@app.post("/api/statements/{sid}/pay")
def pay_statement(sid: int, body: PayIn):
    db.mark_statement_paid(sid, body.amount, body.paid_at.isoformat())
    return {"ok": True}


@app.delete("/api/statements/{sid}/pay")
def unpay_statement(sid: int):
    db.mark_statement_paid(sid, None, None)
    return {"ok": True}


class BillIn(BaseModel):
    provider: str = Field(min_length=1)
    description: str | None = None
    amount: float = Field(ge=0)
    due_date: date
    category: str | None = "Faturalar"
    note: str | None = None
    file_name: str | None = None


class BillPatch(BaseModel):
    provider: str | None = None
    description: str | None = None
    amount: float | None = Field(default=None, ge=0)
    due_date: date | None = None
    category: str | None = None
    note: str | None = None


BILL_TYPES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
              "image/heic": ".heic"}


@app.post("/api/bills/parse")
async def parse_bill(file: UploadFile = File(...)):
    """Fatura PDF/fotoğrafını sakla ve oku; kullanıcı onaylayınca /api/bills ile kaydedilir."""
    data = await file.read()
    mime = file.content_type or ""
    if mime not in BILL_TYPES:
        raise HTTPException(422, "Sadece PDF veya fotoğraf yüklenebilir")
    name = db.save_bill_file(data, BILL_TYPES[mime])
    try:
        parsed = receipt.parse_bill(data, mime) if receipt.enabled() else {}
    except receipt.ReceiptError as e:
        parsed = {"error": str(e)}
    return {**parsed, "file_name": name}


# ---------------------------------------------------------------- telefondan Paylaş

@app.post("/api/share")
async def share(request: Request, file: list[UploadFile] = File(...)):
    """iOS Kısayollar / uygulama: paylaşılan PDF ve fotoğraflar. Yanıt düz metin (Kısayol bildirim olarak gösterir)."""
    from fastapi.responses import PlainTextResponse

    from app import share as share_mod

    out = []
    for f in file[:5]:
        data = await f.read()
        if len(data) > 15 * 1024 * 1024:
            out.append(f"{f.filename}: dosya çok büyük")
            continue
        res = await asyncio.to_thread(share_mod.process, f.filename or "dosya", data, f.content_type or "")
        out.append(res["message"])
    if "text/plain" in request.headers.get("accept", "") or "json" not in request.headers.get("accept", ""):
        return PlainTextResponse("\n".join(out) or "Dosya yok")
    return {"messages": out}


def _shortcut_file() -> str:
    """İmzalı iOS kısayolu (scripts/make_shortcut.py ile Mac'te üretilir; sunucuya özel adres içerir, repoda yok)."""
    return os.path.join(os.path.dirname(os.path.abspath(system.SYSTEM_DB)), "shortcuts", "hesapkitap.shortcut")


@app.get("/api/share/shortcut")
def shortcut_info():
    return {"available": os.path.exists(_shortcut_file())}


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
    form = await request.form()
    msgs = []
    with db.use_space(system.user_db_path(u) if u else None, u):
        for f in form.getlist("file")[:5]:
            if hasattr(f, "read"):
                data = await f.read()
                res = await asyncio.to_thread(share_mod.process, f.filename or "dosya", data, f.content_type or "")
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
async def parse_document(file: list[UploadFile] = File(...)):
    """Fiş veya fatura (foto/PDF): türünü kendisi anlar. Faturaysa dosya saklanır.
    Birden fazla dosya: uzun fişin sırayla çekilmiş parçaları (en fazla 4)."""
    files = file[:4]
    data = await files[0].read()
    mime = files[0].content_type or ""
    if mime not in BILL_TYPES or any((f.content_type or "") not in BILL_TYPES for f in files[1:]):
        raise HTTPException(422, "Sadece PDF veya fotoğraf yüklenebilir")
    more = [(await f.read(), f.content_type or "") for f in files[1:]]
    cats = sorted(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories()) - {"Ödeme", "Kategorisiz"})
    try:
        doc = receipt.parse_document(data, mime, cats, sorted({r["category"] for r in db.list_item_rules()}), more=more)
    except receipt.ReceiptError as e:
        raise HTTPException(502, str(e)) from e
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
        doc["file_name"] = db.save_bill_file(data, BILL_TYPES[mime])
    return doc


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
    path = os.path.join(db.bill_dir(), b["file_name"])
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, filename=f"{b['provider']}-{b['due_date']}{os.path.splitext(path)[1]}")


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


@app.get("/api/settings/backup/{name}")
def backup_download(name: str):
    if name not in {b["name"] for b in backup.list_backups()}:
        raise HTTPException(404)
    return FileResponse(os.path.join(backup.backup_dir(), name), filename=name, media_type="application/octet-stream")


# ---------------------------------------------------------------- frontend (production)

mimetypes.add_type("application/manifest+json", ".webmanifest")
WEB_DIST = os.environ.get("WEB_DIST")
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
