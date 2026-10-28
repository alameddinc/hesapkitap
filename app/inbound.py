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

from app import db, system

log = logging.getLogger("hesapkitap.inbound")

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # karışan harf yok (l/1, o/0)
CODE_LEN = 6
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
    return system.user(r[0]) if r else None


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
    """Fatura belgesini (PDF ya da mail metni) oku, Ödemeler'e ekle; aynı fatura ikinci kez eklenmez."""
    from app import receipt

    parsed = receipt.parse_bill(data, mime)
    if not parsed.get("amount") or not parsed.get("due_date"):
        raise ValueError(f"{name}: tutar ya da son ödeme tarihi okunamadı")
    for b in db.list_bills():
        if b["provider"] == parsed["provider"] and b["due_date"] == parsed["due_date"] \
                and abs(b["amount"] - parsed["amount"]) < 0.01:
            return f"Fatura {parsed['provider']} {parsed['amount']:,.2f} TL: zaten vardı"
    file_name = db.save_bill_file(data, ".pdf") if mime == "application/pdf" else None
    db.create_bill({"provider": parsed["provider"], "amount": parsed["amount"], "due_date": parsed["due_date"],
                    "category": parsed["category"], "description": parsed.get("description") or None,
                    "note": "E-postayla geldi", "file_name": file_name})
    prev = [b for b in db.list_bills() if b["provider"] == parsed["provider"] and b["due_date"] < parsed["due_date"]]
    change = ""
    if prev and prev[0]["amount"]:
        pct = round((parsed["amount"] - prev[0]["amount"]) / prev[0]["amount"] * 100)
        if abs(pct) >= 25:
            change = f" · geçen faturadan %{abs(pct)} {'fazla' if pct > 0 else 'az'}"
    return f"🧾 {parsed['provider']} faturası: {parsed['amount']:,.2f} TL · son ödeme {parsed['due_date'][8:]}.{parsed['due_date'][5:7]}{change}"


def _people_emails(owner_id: int) -> list[str]:
    ids = [owner_id] + [m["id"] for m in system.members(owner_id)]
    return [u["username"].lower() for i in ids if (u := system.user(i)) and "@" in u["username"]]


def handle(raw: bytes, to: str, verify=_dkim_domains) -> dict:
    """Bir maili işle. Dönen: posta günlüğü satırı (+ 'user' anahtarı yok). Adres yoksa LookupError."""
    from app import mailbox, plans

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
        try:
            pdfs = mailbox._pdfs(msg)
            details = []
            if kind == "fatura":
                details = [_bill(n, d, "application/pdf") for n, d in pdfs] or \
                          [_bill("mail", mailbox._body_text(msg).encode(), "text/plain")]
                r = {**entry, "kind": "fatura", "status": "ok", "detail": "\n".join(details)}
            elif pdfs:
                for n, d in pdfs:
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
        return r
