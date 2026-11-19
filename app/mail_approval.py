"""Kişisel adrese (<ad>@<alan adı>) gelen banka belgesinde yeni kart / hesap onayı.

Ad tahmin edilebilir: başkası kendi bankasının e-ekstresini bu adrese yönlendirebilir. Bu yüzden kişisel adrese gelen
banka belgesi defterde BİLİNEN bir karta (son 4) ya da hesaba (IBAN / banka) aitse eskisi gibi işlenir; ilk kez görülen
kart / hesapsa İŞLENMEZ, belge + özet (banka, son 4 / hesap, dönem, tutar) "onay bekliyor" olarak saklanır:
- Evet  → belge işlenir, kart/hesap bu defter için güvenilir olur
- Hayır → belge silinir, o kart/hesap bu adres için engellenir (sonraki belgeler sessizce atlanır)
Onay bekleyenler PENDING_DAYS gün sonra silinir. Kodlu (fatura-) adreste ve kullanıcının kendi e-postasından elle
iletilen belgede bu adım yoktur.

Kimlik biçimi: "card:<son4>", "iban:<TR…>", "acct:<kısa banka adı>". Defterde saklanır (her defter kendi dosyası).
"""
import json
from datetime import datetime, timedelta, timezone

from app import db

PENDING_DAYS = 30
_TRUST = "posta:trusted_ids"
_BLOCK = "posta:blocked_ids"
_DOMAINS = "posta:trusted_domains"  # kişisel adreste işlem bildirimi kabul edilen banka alan adları


def _init(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS mail_pending (
                 id INTEGER PRIMARY KEY,
                 created_at TEXT NOT NULL,
                 ident TEXT NOT NULL,
                 doc_type TEXT NOT NULL,           -- ekstre | dokum | dekont
                 bank TEXT,
                 label TEXT,                       -- "•••• 1234" ya da "TR… 5678"
                 period TEXT,                      -- ekstre: dönem sonu (YYYY-MM-DD); döküm/dekont: tarih
                 amount REAL,
                 filename TEXT,
                 file TEXT NOT NULL,               -- uploads/ altındaki PDF
                 sender_domain TEXT)""")


def _list(key: str) -> list[str]:
    try:
        v = json.loads(db.get_setting(key) or "[]")
        return [x for x in v if isinstance(x, str)]
    except ValueError:
        return []


def _add(key: str, value: str, cap: int = 200) -> None:
    cur = _list(key)
    if value and value not in cur:
        db.set_setting(key, json.dumps((cur + [value])[-cap:]))


def _remove(key: str, value: str) -> None:
    cur = _list(key)
    if value in cur:
        db.set_setting(key, json.dumps([x for x in cur if x != value]))


def last4(card_no: str | None) -> str:
    digits = "".join(ch for ch in (card_no or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else ""


def ident_of(info: dict) -> str:
    """mailbox.handle_pdf kapısından gelen bilgi → kimlik."""
    if info["type"] == "ekstre":
        l4 = last4(info.get("card_no"))
        return f"card:{l4}" if l4 else f"acct:{(info.get('bank') or '').strip().lower()}"
    iban = (info.get("iban") or "").replace(" ", "").upper()
    if iban:
        return f"iban:{iban}"
    from app.bank_import import short_bank

    return f"acct:{short_bank(info.get('bank') or '').strip().lower()}"


def known(ident: str) -> bool:
    """Defterde bu kart/hesap var mı (önceki ekstre/döküm) ya da kullanıcı onayladı mı?"""
    if ident in _list(_TRUST):
        return True
    kind, _, val = ident.partition(":")
    if not val:
        return False
    with db.connect() as c:
        if kind == "card":
            return any(last4(r[0]) == val for r in c.execute("SELECT DISTINCT card_no FROM statements"))
        if kind == "iban":
            return bool(c.execute("SELECT 1 FROM accounts WHERE REPLACE(UPPER(iban), ' ', '') = ?", (val,)).fetchone())
        if kind == "acct":
            from app.bank_import import short_bank

            names = {short_bank(r[0]).strip().lower() for r in c.execute("SELECT name FROM accounts WHERE kind = 'banka'")}
            banks = {(r[0] or "").strip().lower() for r in c.execute("SELECT DISTINCT bank FROM statements")}
            return val in names or val in banks
    return False


def blocked(ident: str) -> bool:
    return ident in _list(_BLOCK)


def trust(ident: str, sender_domain: str | None = None) -> None:
    _add(_TRUST, ident)
    _remove(_BLOCK, ident)
    if sender_domain:
        _add(_DOMAINS, sender_domain.lower())


def domain_trusted(sender_domain: str) -> bool:
    d = (sender_domain or "").lower()
    return any(d == x or d.endswith("." + x) or x.endswith("." + d) for x in _list(_DOMAINS))


def _label(ident: str) -> str:
    kind, _, val = ident.partition(":")
    if kind == "card":
        return f"•••• {val}"
    if kind == "iban":
        return f"{val[:2]}… {val[-4:]}"
    return "hesap"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hold(info: dict, name: str, data: bytes, sender_domain: str) -> int:
    from app import verification

    ident = ident_of(info)
    with db.connect() as c:
        _init(c)
        return c.execute(
            "INSERT INTO mail_pending (created_at, ident, doc_type, bank, label, period, amount, filename, file, "
            "sender_domain) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now().strftime("%Y-%m-%dT%H:%M:%SZ"), ident, info["type"], info.get("bank") or None, _label(ident),
             info.get("period"), info.get("amount"), name, verification._store_pdf(data), sender_domain)).lastrowid


def make_gate(sender_domain: str, held_ids: list[int]):
    """mailbox.handle_pdf(gate=…) için kapı: bilinen kart/hesap → None (işle); engelli → atla; yeni → onaya koy."""

    def gate(info: dict, name: str, data: bytes) -> str | None:
        ident = ident_of(info)
        if blocked(ident):
            return f"⏭ {name}: bu kart/hesap bu adres için engellendi"
        if known(ident):
            _add(_DOMAINS, sender_domain.lower())
            return None
        pid = hold(info, name, data, sender_domain)
        held_ids.append(pid)
        return (f"⏸ {info.get('bank') or 'Banka'} {_label(ident)} {_what(info['type'])}: ilk kez görüldü, "
                "onay bekliyor · uygulamada 'Bu kart senin mi?' sorusuna yanıt ver")

    return gate


def _what(doc_type: str) -> str:
    return {"ekstre": "ekstresi", "dokum": "hesap dökümü", "dekont": "dekontu"}.get(doc_type, "belgesi")


def purge(days: int = PENDING_DAYS) -> int:
    cut = (_now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.connect() as c:
        _init(c)
        return c.execute("DELETE FROM mail_pending WHERE created_at < ?", (cut,)).rowcount


def pending() -> list[dict]:
    purge()
    with db.connect() as c:
        _init(c)
        rows = c.execute("SELECT id, created_at, ident, doc_type, bank, label, period, amount, filename "
                         "FROM mail_pending ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["kind"] = d["ident"].split(":", 1)[0]
        d["question"] = (f"{d['bank'] or 'Banka'} {d['label']} {_what(d['doc_type'])} geldi. "
                         f"Bu {'kart' if d['kind'] == 'card' else 'hesap'} senin mi?")
        d.pop("ident")
        out.append(d)
    return out


def _row(pid: int):
    purge()
    with db.connect() as c:
        _init(c)
        return c.execute("SELECT * FROM mail_pending WHERE id = ?", (pid,)).fetchone()


def approve(pid: int) -> dict:
    """Evet: belgeyi işle, kart/hesabı güvenilir yap. Bulunamazsa KeyError."""
    from app import mailbox

    r = _row(pid)
    if not r:
        raise KeyError(pid)
    data = db.read_file("uploads", r["file"]) or b""
    trust(r["ident"], r["sender_domain"])
    # aynı kimlikten bekleyen diğer belgeler de işlenir
    with db.connect() as c:
        same = c.execute("SELECT * FROM mail_pending WHERE ident = ? ORDER BY id", (r["ident"],)).fetchall()
        c.execute("DELETE FROM mail_pending WHERE ident = ?", (r["ident"],))
    details = []
    for x in same:
        blob = data if x["id"] == pid else (db.read_file("uploads", x["file"]) or b"")
        if not blob:
            details.append(f"⏭ {x['filename']}: belge bulunamadı")
            continue
        try:
            details.append(str(mailbox.handle_pdf(x["filename"] or "belge.pdf", blob)))
        except Exception as e:  # noqa: BLE001 - kullanıcıya nedenini göster
            details.append(f"⚠️ {x['filename']}: {str(e)[:200]}")
    return {"ok": True, "message": "\n".join(details)}


def reject(pid: int) -> dict:
    """Hayır: belgeyi sil, kart/hesabı bu adres için engelle. Bulunamazsa KeyError."""
    r = _row(pid)
    if not r:
        raise KeyError(pid)
    _add(_BLOCK, r["ident"])
    with db.connect() as c:
        n = c.execute("DELETE FROM mail_pending WHERE ident = ?", (r["ident"],)).rowcount
    return {"ok": True, "removed": n}


# ---------------------------------------------------------------- Gmail iletim onayı

GMAIL_FORWARD_SENDER = "forwarding-noreply@google.com"
_CONFIRM = "posta:gmail_confirm"


def save_gmail_confirmation(body: str, received: str) -> dict | None:
    """Gmail "iletim onayı" mailinden onay kodu ve bağlantısını çıkar, defterde sakla (otomatik tıklanmaz)."""
    import re

    code = re.search(r"(?:onay kodu|confirmation code)\s*[:：]?\s*(\d{6,12})", body, re.I)
    link = re.search(r"https://mail(?:-settings)?\.google\.com/mail/[^\s<>\"')]+", body)
    who = re.search(r"([\w.+-]+@[\w.-]+\.\w+)\s+(?:adlı kullanıcı|has requested|kullanıcısı)", body)
    if not code and not link:
        return None
    info = {"code": code.group(1) if code else None, "link": link.group(0) if link else None,
            "from_email": who.group(1) if who else None, "received_at": received}
    db.set_setting(_CONFIRM, json.dumps(info))
    return info


def gmail_confirmation() -> dict | None:
    try:
        v = json.loads(db.get_setting(_CONFIRM) or "null")
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def clear_gmail_confirmation() -> None:
    db.set_setting(_CONFIRM, None)
