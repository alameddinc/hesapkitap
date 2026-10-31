"""Gelen posta (Premium): her defterin kendine ait adresi, ör. fatura-x7k2m9@posta.alameddinc.com.

Akış: banka / kurum maili → Cloudflare Email Routing → Email Worker → POST /api/mail/inbound (ham MIME, gizli anahtarla)
→ adresin kodundan defter bulunur → imza (DKIM) burada doğrulanır → ekstre, hesap dökümü, bildirim ya da fatura işlenir.

Kimden kabul edilir (DKIM imzası From alan adıyla uyuşmalı):
- banka alan adları (mailbox.allowed_domains) → ekstre / döküm / dekont / işlem bildirimi
- kurum alan adları (BILL_DOMAINS)             → fatura (Ödemeler'e "bu ay geldi")
- defterdeki kişilerin kendi e-postası         → kullanıcının elle ilettiği belge (önce banka belgesi, olmazsa fatura)
Diğer her şey "atlandı" diye posta günlüğüne düşer. Aynı mail (Message-ID) ikinci kez işlenmez.

Adres defterin sahibine (ortak defter) ya da üyenin kendi defterine bağlıdır; kod tahmin edilemez, yenilenebilir.
"""
import logging
import os
import re
import secrets
from datetime import date
from email import message_from_bytes
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from app import db, money, system

log = logging.getLogger("hesapkitap.inbound")

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # karışan harf yok (l/1, o/0)
CODE_LEN = 6
MAX_PDF = 10 * 1024 * 1024  # tek ek: ekstreler birkaç yüz KB
MAX_PDFS = 5  # bir maildeki ek sayısı (banka / kurum)
MAX_PDFS_OWN = {"free": 10, "premium": 50, "aile": 50}  # kendi / güvendiğin adresinden toplu iletim
DAILY_LIMIT = 60  # bir adrese günde işlenen mail (fazlası günlüğe bile yazılmaz)
LOG_KEEP = 300  # günlükte tutulan son satır
PREFIX = "fatura-"
LOCAL = re.compile(rf"^{PREFIX}([a-z0-9]{{6,16}})$")

# Faturayı e-postayla gönderen kurumlar (alt alan adları da geçerli: efatura.enerjisa.com.tr)
BILL_DOMAINS = (
    # elektrik
    "enerjisa.com.tr", "ck-enerji.com.tr", "ckbogazici.com.tr", "aydemenerji.com.tr", "gdzelektrik.com.tr",
    "uludagenerji.com.tr", "osmangazienerji.com.tr", "kcetas.com.tr", "yesilirmak.com.tr", "akdenizepsas.com.tr",
    "meram.com.tr", "dicleedas.com.tr", "vangolu.com.tr", "coruhedas.com.tr", "firatedas.com.tr", "trakyaedas.com.tr",
    # doğalgaz
    "igdas.istanbul", "igdas.com.tr", "baskentdogalgaz.com.tr", "izmirgaz.com.tr", "aksagaz.com.tr", "bursagaz.com.tr",
    "enerya.com.tr", "kayserigaz.com.tr", "palendogalgaz.com.tr",
    # su
    "iski.istanbul", "iski.gov.tr", "aski.gov.tr", "izsu.gov.tr", "buski.gov.tr", "asat.gov.tr",
    # internet / telefon / TV
    "turktelekom.com.tr", "ttnet.com.tr", "turkcell.com.tr", "superonline.net", "vodafone.com.tr",
    "turknet.com.tr", "millenicom.com.tr", "dsmart.com.tr", "digiturk.com.tr", "kablonet.com.tr", "turksat.com.tr",
    # e-fatura altyapıları (kurum adına gönderen)
    "efatura.gov.tr", "edm.com.tr", "logo.com.tr", "uyumsoft.com.tr", "izibiz.com.tr", "foriba.com", "mysoft.com.tr",
)


def domain() -> str:
    return os.environ.get("HESAPKITAP_INBOUND_DOMAIN", "posta.alameddinc.com")


def _init(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS inbound (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                 created_at TEXT DEFAULT CURRENT_TIMESTAMP, revoked INTEGER NOT NULL DEFAULT 0)""")


def _book_user(u: dict) -> int:
    """Adresin bağlı olduğu defter: ortak defterde sahip; üyenin "Benim" defterinde üyenin kendisi."""
    if u.get("member_of") and not u.get("own_book"):
        return u["member_of"]
    return u["id"]


def address(u: dict, create: bool = True) -> str | None:
    uid = _book_user(u)
    with system.connect() as c:
        _init(c)
        r = c.execute("SELECT token FROM inbound WHERE user_id = ? AND revoked = 0", (uid,)).fetchone()
        if r:
            return f"{PREFIX}{r[0]}@{domain()}"
        if not create:
            return None
        while True:
            token = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
            if not c.execute("SELECT 1 FROM inbound WHERE token = ?", (token,)).fetchone():
                break
        c.execute("INSERT INTO inbound (token, user_id) VALUES (?, ?)", (token, uid))
        return f"{PREFIX}{token}@{domain()}"


def regenerate(u: dict) -> str:
    """Adres sızdıysa: eskisi hemen çalışmaz olur, yenisi verilir."""
    with system.connect() as c:
        _init(c)
        c.execute("UPDATE inbound SET revoked = 1 WHERE user_id = ?", (_book_user(u),))
    return address(u)


def resolve(to: str) -> dict | None:
    """fatura-<kod>@... → adresin sahibi (kullanıcı satırı). Alan adına bakılmaz: ileride yeni alan adına geçilirse
    eski adresler de çalışsın."""
    local = (to or "").strip().lower().split("@", 1)[0]
    m = LOCAL.match(local)
    if not m:
        return None
    with system.connect() as c:
        _init(c)
        r = c.execute("SELECT user_id FROM inbound WHERE token = ? AND revoked = 0", (m.group(1),)).fetchone()
    u = system.user(r[0]) if r else None
    return u if u and not u.get("deleted_at") else None  # silinmek üzere bekleyen defterin adresi mail kabul etmez


# ---------------------------------------------------------------- imza

def _dkim_domains(raw: bytes) -> set[str]:
    """Maildeki DKIM imzalarından doğrulananların alan adları (d=)."""
    import dkim

    out: set[str] = set()
    n = len(re.findall(rb"(?im)^dkim-signature:", raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]))
    for i in range(n):
        try:
            d = dkim.DKIM(raw)
            if d.verify(idx=i):
                out.add(d.domain.decode().lower())
        except Exception:  # noqa: BLE001 - bozuk/eksik imza: o imza sayılmaz
            continue
    return out


def _aligned(sender_domain: str, signed: set[str]) -> bool:
    return any(sender_domain == d or sender_domain.endswith("." + d) or d.endswith("." + sender_domain) for d in signed)


def _match(domain_: str, allowed: tuple[str, ...]) -> str | None:
    return next((d for d in allowed if domain_ == d or domain_.endswith("." + d)), None)


def classify_sender(msg: Message, raw: bytes, people: list[str], verify=_dkim_domains) -> tuple[str | None, str]:
    """("banka" | "fatura" | "kendi" | None, gönderen alan adı). None: kabul edilmez."""
    from app import mailbox

    addr = parseaddr(msg.get("From", ""))[1].lower()
    dom = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    if _match(dom, mailbox.allowed_domains()):
        kind = "banka"
    elif _match(dom, BILL_DOMAINS):
        kind = "fatura"
    elif addr in people:
        kind = "kendi"
    else:
        return None, dom
    return (kind if _aligned(dom, verify(raw)) else None), dom


# ---------------------------------------------------------------- işleme

def _bill(name: str, data: bytes, mime: str) -> str:
    """Fatura belgesini (PDF ya da mail metni) oku, Ödemeler'e ekle; aynı fatura ikinci kez eklenmez.
    Hazır sürümde yeni fatura 1 belge okuma hakkı; hak doluysa okunmaz."""
    from app import doc_reads, receipt

    if not doc_reads.allowed(data):
        return f"⏭ {name}: belge okuma hakkı doldu"
    parsed = receipt.parse_bill(data, mime)
    if not parsed.get("amount") or not parsed.get("due_date"):
        raise ValueError(f"{name}: tutar ya da son ödeme tarihi okunamadı")
    for b in db.list_bills():
        if b["provider"] == parsed["provider"] and b["due_date"] == parsed["due_date"] \
                and money.eq(b["amount"], parsed["amount"]):
            return f"Fatura {parsed['provider']} {money.tl(parsed['amount'])}: zaten vardı"
    file_name = db.save_bill_file(data, ".pdf") if mime == "application/pdf" else None
    db.create_bill({"provider": parsed["provider"], "amount": parsed["amount"], "due_date": parsed["due_date"],
                    "category": parsed["category"], "description": parsed.get("description") or None,
                    "note": "E-postayla geldi", "file_name": file_name})
    doc_reads.charge(data)
    prev = [b for b in db.list_bills() if b["provider"] == parsed["provider"] and b["due_date"] < parsed["due_date"]]
    change = ""
    if prev and prev[0]["amount"]:
        pct = round((parsed["amount"] - prev[0]["amount"]) / prev[0]["amount"] * 100)
        if abs(pct) >= 25:
            change = f" · geçen faturadan %{abs(pct)} {'fazla' if pct > 0 else 'az'}"
    return f"🧾 {parsed['provider']} faturası: {money.tl(parsed['amount'])} · son ödeme {parsed['due_date'][8:]}.{parsed['due_date'][5:7]}{change}"


def _max_pdfs(kind: str | None, ctx: dict) -> int:
    """Banka mailinde 5; kullanıcının kendi (ya da güvendiği) adresinden toplu iletimde pakete göre 10 / 50."""
    from app import plans

    if kind != "kendi":
        return MAX_PDFS
    p = plans.plan_of(ctx)
    return MAX_PDFS_OWN.get(p, 50 if p == plans.SELF else MAX_PDFS_OWN["free"])


def _safe_pdfs(pdfs: list[tuple[str, bytes]], limit: int = MAX_PDFS) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Yalnız gerçek PDF'ler (dosya başı %PDF-), makul boyutta ve sayıda. Gerisi okunmaz, saklanmaz; nedeni günlüğe."""
    ok, notes = [], []
    for name, data in pdfs:
        if len(ok) >= limit:
            notes.append(f"⏭ {name}: bir mailde en çok {limit} ek okunur")
        elif len(data) > MAX_PDF:
            notes.append(f"⏭ {name}: çok büyük ({len(data) // 1048576} MB)")
        elif b"%PDF-" not in data[:1024]:
            notes.append(f"⏭ {name}: PDF değil, okunmadı")
        else:
            ok.append((name, data))
    return ok, notes


def _people_emails(owner_id: int) -> list[str]:
    """Defterdeki kişilerin e-postası (kullanıcı adı e-postaysa) + defterin "güvendiğin adresler" listesi."""
    ids = [owner_id] + [m["id"] for m in system.members(owner_id)]
    return [u["username"].lower() for i in ids if (u := system.user(i)) and "@" in u["username"]] + senders()


EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,24}$")


def senders() -> list[str]:
    """Bu defterin güvendiği gönderenler (kullanıcının kendi Gmail'i vb.): elle ilettiği belgeler kabul edilir.
    İmza (DKIM) yine zorunlu: başkası bu adres adına mail atamaz."""
    import json

    try:
        return [s for s in json.loads(db.get_setting("posta:senders") or "[]") if isinstance(s, str)]
    except ValueError:
        return []


def set_senders(emails: list[str]) -> list[str]:
    import json

    clean = sorted({e.strip().lower() for e in emails if EMAIL.match(e.strip().lower())})[:10]
    db.set_setting("posta:senders", json.dumps(clean))
    return clean


def message_key(raw: bytes, to: str) -> str:
    """Aynı mailin aynı adrese tekrar gelişini tanımak için (Message-ID, yoksa içeriğin özeti)."""
    import hashlib

    mid = (message_from_bytes(raw[:65536]).get("Message-ID") or "").strip()
    return f"{to.lower()}|{mid or hashlib.sha256(raw).hexdigest()}"


def handle(raw: bytes, to: str, verify=_dkim_domains) -> dict:
    """Bir maili işle. Dönen: posta günlüğü satırı (+ 'user' anahtarı yok). Adres yoksa LookupError."""
    from app import doc_reads, mailbox, plans

    u = resolve(to)
    if not u:
        raise LookupError("adres yok")
    msg = message_from_bytes(raw)
    mid = (msg.get("Message-ID") or "").strip() or f"<inbound-{hash(raw)}>"
    subject = mailbox._text(msg.get("Subject"))
    try:
        received = parsedate_to_datetime(msg.get("Date")).date().isoformat()
    except Exception:  # noqa: BLE001
        received = date.today().isoformat()
    entry = {"message_id": mid, "received_at": received, "sender": parseaddr(msg.get("From", ""))[1],
             "subject": subject[:200]}
    own = bool(u.get("member_of"))  # adres üyeye bağlıysa üyenin kendi defteri
    ctx = {**u, "own_book": True} if own else u
    db_file = system.own_db_path(u) if own else system.user_db_path(u)
    with db.use_space(db_file, ctx):
        if db.mail_seen(mid):
            return {**entry, "kind": "-", "status": "skip", "detail": "Bu mail zaten işlendi"}
        if db.mail_count_today() >= DAILY_LIMIT:  # sel: imza kontrolüne, okumaya, günlüğe hiç girme
            return {**entry, "kind": "-", "status": "skip", "detail": "Günlük posta sınırı doldu"}
        if not plans.allows(ctx, "posta"):
            r = {**entry, "kind": "-", "status": "skip", "detail": "Postadan otomatik okuma Premium paketinde"}
            db.log_mail(r)
            return r
        kind, dom = classify_sender(msg, raw, _people_emails(u.get("member_of") or u["id"]), verify)
        if not kind:
            r = {**entry, "kind": "-", "status": "skip",
                 "detail": f"Tanınan bir banka/kurum değil ya da imza doğrulanamadı ({dom or 'bilinmiyor'})"}
            db.log_mail(r)
            return r
        if not doc_reads.allowed():  # hazır sürüm: bu ayın belge okuma hakkı bitti; hiçbir şey okunmaz
            r = {**entry, "kind": "-", "status": "skip", "detail": "Belge okuma hakkı doldu"}
            db.log_mail(r)
            return r
        try:
            pdfs, details = _safe_pdfs(mailbox._pdfs(msg), _max_pdfs(kind, ctx))
            if details and not pdfs:  # ekler vardı ama hiçbiri uygun değil: hiçbir şey okunmaz
                r = {**entry, "kind": "-", "status": "skip", "detail": "\n".join(details)}
            elif kind == "fatura":
                details += [_bill(n, d, "application/pdf") for n, d in pdfs] or \
                          [_bill("mail", mailbox._body_text(msg).encode(), "text/plain")]
                r = {**entry, "kind": "fatura", "status": "ok", "detail": "\n".join(details)}
            elif pdfs:
                for n, d in pdfs:
                    if not doc_reads.allowed(d):  # bir maildeki birden çok belge: hak ortada biterse kalanlar okunmaz
                        details.append(f"⏭ {n}: belge okuma hakkı doldu")
                        continue
                    try:
                        details.append(mailbox.handle_pdf(n, d))
                    except Exception:  # noqa: BLE001 - kullanıcının ilettiği PDF banka belgesi değilse fatura olabilir
                        if kind != "kendi":
                            raise
                        details.append(_bill(n, d, "application/pdf"))
                r = {**entry, "kind": "pdf", "status": "ok", "detail": "\n".join(details)}
            else:
                res = mailbox.handle_notification(mailbox._body_text(msg), received, mid) if kind == "banka" else None
                r = {**entry, "kind": "bildirim" if res else "diger", "status": "ok" if res else "skip",
                     "detail": res or "İşlenecek belge ya da hareket yok"}
        except Exception as e:  # noqa: BLE001 - bir mail patlarsa günlükte görünsün
            log.exception("gelen posta %s işlenemedi", mid)
            r = {**entry, "kind": "-", "status": "error", "detail": str(e)[:300]}
        db.log_mail(r)
        db.trim_mail_log(LOG_KEEP)
        return r
