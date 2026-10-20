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

from app import auth, backup, config, db, finance, jobs, receipt, telegram
from app.categorize import CATEGORIES, suggest_pattern
from app.parsers import parse_statement
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND

db.init_db()


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


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if auth.enabled() and path.startswith("/api/") and not path.startswith("/api/auth/"):
        if auth.setup_required():
            return JSONResponse({"detail": "setup_required"}, status_code=401)
        if not auth.valid_token(request.cookies.get(auth.COOKIE)):
            return JSONResponse({"detail": "Giriş gerekli"}, status_code=401)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


class LoginIn(BaseModel):
    password: str


class SetupIn(BaseModel):
    password: str
    code: str | None = None


class PasswordChange(BaseModel):
    current: str
    new: str


def _start_session(request: Request, response: Response) -> None:
    # HTTPS arkasındaysa (doğrudan ya da proxy başlığıyla) çerez otomatik Secure olur
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.SESSION_DAYS * 86400,
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
    return {"required": True, "authenticated": auth.valid_token(request.cookies.get(auth.COOKIE)),
            "setup_required": False, "password_source": auth.password_source()}


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
            raise HTTPException(403, "Kurulum kodu yanlış. Kodu sunucu log'unda bulabilirsin: docker compose logs | grep kurulum")
    try:
        auth.set_password(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    _start_session(request, response)
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
    if not auth.check_password(body.password):
        auth.record_fail(ip)
        raise HTTPException(401, "Şifre yanlış")
    _start_session(request, response)
    return {"ok": True}


@app.post("/api/settings/password")
def change_password(body: PasswordChange, request: Request, response: Response):
    if auth.password_source() == "env":
        raise HTTPException(409, "Şifre sunucuda APP_PASSWORD ortam değişkeniyle belirlenmiş; oradan değiştir.")
    if not auth.check_password(body.current):
        raise HTTPException(403, "Mevcut şifre yanlış")
    try:
        auth.set_password(body.new)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    _start_session(request, response)  # imza anahtarı yenilendi; bu cihazda oturum devam etsin
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}

Kind = Literal["gelir", "gider"]
Payment = Literal["kredi_karti", "nakit", "havale"]
CardKind = Literal["harcama", "iade", "faiz_ucret", "odeme"]
EXPENSE_CATEGORIES = ["Kira & Aidat", "Faturalar", "Elektrik", "Doğalgaz", "Su", "İnternet & TV", "Telefon", "Sağlık",
                      "Eğitim", "Ulaşım & Araç", "Market & Gıda", "Kredi & Borç", "Diğer"]
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
        "receipt_enabled": receipt.enabled(),
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
    legal_name: str | None = None  # fişteki resmi unvan; saklanmaz, eşleştirme öğrenmek için


class EntryPatch(BaseModel):
    entry_date: date | None = None
    description: str | None = None
    amount: float | None = Field(default=None, ge=0)
    kind: Kind | None = None
    category: str | None = None
    note: str | None = None
    skipped: bool | None = None
    payment: Payment | None = None


@app.post("/api/entries")
def create_entry(body: EntryIn):
    data = body.model_dump(exclude={"legal_name"})
    data["entry_date"] = body.entry_date.isoformat()
    data["skipped"] = int(body.skipped)
    if body.legal_name and body.kind == "gider":
        db.learn_from_receipt(body.legal_name, body.description, body.category)
    eid = db.create_entry(data)
    db.auto_link()  # ekstre önceden yüklendiyse hemen eşleşsin
    return {"id": eid, "linked": any(e["id"] == eid and e["linked_txn_id"] for e in db.list_entries())}


@app.patch("/api/entries/{entry_id}")
def patch_entry(entry_id: int, body: EntryPatch):
    data = body.model_dump(exclude_unset=True)
    if "entry_date" in data:
        data["entry_date"] = data["entry_date"].isoformat()
    if "skipped" in data:
        data["skipped"] = int(data["skipped"])
    if data.get("payment") and data["payment"] != "kredi_karti":
        data["linked_txn_id"] = None  # kartla ödenmediyse eşleşme anlamsız
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
        "category": r["category"], "recurring_id": rid, "skipped": 0,
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
    """Hesap sahibi adları: kart ekstrelerindeki ana kart sahipleri (kendi hesapları arası virmanı tanımak için)."""
    with db.connect() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT card_holder FROM transactions WHERE card_holder <> ''")]


async def _read_bank_pdf(file: UploadFile) -> tuple[list[dict], dict]:
    import hashlib

    from app import bank_import, bank_pdf

    data = await file.read()
    key = hashlib.sha256(data).hexdigest()
    if key not in _PDF_CACHE:
        try:
            _PDF_CACHE[key] = bank_pdf.parse_account_pdf(data, db.pdf_passwords())
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
    return rows


@app.post("/api/bank-import/preview")
async def bank_import_preview(file: UploadFile = File(...), mapping: str | None = Form(None),
                              header_row: int | None = Form(None)):
    """Sütunları tahmin et (ya da verilen eşlemeyi kullan), ilk satırları ve özetini döndür."""
    import json

    from app import bank_import

    if (file.filename or "").lower().endswith(".pdf"):
        parsed, meta = await _read_bank_pdf(file)
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
    return {**det, "profile_saved": bool(saved), "total": len(parsed), "counts": counts, "sample": parsed[:15],
            "raw_preview": rows[det["header_row"]: det["header_row"] + 6]}


@app.post("/api/bank-import")
async def bank_import_run(file: UploadFile = File(...), mapping: str = Form("{}"), header_row: int = Form(0)):
    import json

    from app import bank_import

    account = ""
    if (file.filename or "").lower().endswith(".pdf"):
        parsed, meta = await _read_bank_pdf(file)
        account = meta.get("bank") or ""
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
    stats = db.import_bank_rows(parsed, account=account, owners=owners)
    db.auto_link()
    return stats


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


@app.post("/api/statements/upload")
async def upload_statements(files: list[UploadFile] = File(...), password: str | None = Form(None),
                            remember: bool = Form(False)):
    from app.parsers import PasswordRequired

    results = []
    tried = ([password] if password else []) + db.pdf_passwords()
    for f in files:
        data = await f.read()
        res = {"filename": f.filename}
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


@app.post("/api/documents/parse")
async def parse_document(file: UploadFile = File(...)):
    """Fiş veya fatura (foto/PDF): türünü kendisi anlar. Faturaysa dosya saklanır."""
    data = await file.read()
    mime = file.content_type or ""
    if mime not in BILL_TYPES:
        raise HTTPException(422, "Sadece PDF veya fotoğraf yüklenebilir")
    cats = sorted(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories()) - {"Ödeme", "Kategorisiz"})
    try:
        doc = receipt.parse_document(data, mime, cats)
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
    path = os.path.join(db.BILL_DIR, b["file_name"])
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
    config.set_value(body.name, body.value)
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
