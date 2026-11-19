"""Premium bekliyor: ücretsiz pakette kişisel adrese (<ad>@alan adı) gelen banka belgeleri.

Postadan otomatik okuma Premium'da. Ücretsiz kullanıcının kişisel adresine bankadan (DKIM doğrulanmış) gelen PDF atılmaz:
OKUNMADAN (yapay zekâ yok, okuma hakkı düşmez) saklanır. Kullanıcı:
- Premium'a geçer → bekleyenler sırayla okunur (yeni kart onayı kuralı geçerli: bilinmeyen kart onay kuyruğuna gider)
- "Kendim yükleyeyim" der → belge normal yükleme yolu gibi okunur, ücretsiz okuma hakkından düşer

Saklama (dosyalar app/storage üzerinden: canlıda R2, yerelde disk; silme de oradan):
- KEEP_DAYS gün; kart/hesap başına en yeni PER_IDENT belge; kullanıcı başı en çok PER_USER belge
- sunucu genelinde bekleyen toplamı HESAPKITAP_PENDING_MAX_MB'ı (varsayılan 2048) aşarsa en eskiler silinir
- silinmeden WARN_DAYS gün önce bir kez bildirim

Defter dosyasında: premium_wait (banka, kart/hesap kimliği, dosya). Sistem veritabanında yalnız dizin (defter, boyut,
tarih) tutulur: genel sınır ve gece temizliği bütün defterleri açmadan hesaplanır; finans bilgisi sistem tarafına çıkmaz.

Fatura/kurum mailleri ve PDF'siz işlem bildirimleri beklemez (eskisi gibi atlanır). Kodlu (fatura-) adres değişmez.
"""
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app import db, system

log = logging.getLogger("hesapkitap.premium_wait")

KEEP_DAYS = 90
WARN_DAYS = 7
PER_IDENT = 3
PER_USER = 10


def max_bytes() -> int:
    try:
        mb = float(os.environ.get("HESAPKITAP_PENDING_MAX_MB", "2048"))
    except ValueError:
        mb = 2048.0
    return int(mb * 1024 * 1024)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _init_sys(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS premium_wait_index (
                 book_id INTEGER NOT NULL, own INTEGER NOT NULL, local_id INTEGER NOT NULL,
                 size INTEGER NOT NULL, created_at TEXT NOT NULL, warned INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY (book_id, own, local_id))""")


def _init(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS premium_wait (
                 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, ident TEXT NOT NULL, bank TEXT,
                 sender_domain TEXT, filename TEXT, file TEXT NOT NULL, size INTEGER NOT NULL)""")


@contextmanager
def _space(book_id: int, own: bool):
    u = system.user(book_id)
    if not u:
        raise LookupError(book_id)
    ctx = {**u, "own_book": True} if own else u
    with db.use_space(system.own_db_path(u) if own else system.user_db_path(u), ctx):
        yield ctx


def _bank_label(text: str | None, sender_domain: str) -> str:
    if text:
        try:
            from app.templates import bank_of

            if b := bank_of(text):
                return b
        except Exception:  # noqa: BLE001
            pass
    from app import mailbox

    d = next((x for x in mailbox.allowed_domains() if sender_domain == x or sender_domain.endswith("." + x)), sender_domain)
    return d.split(".")[0].capitalize() if d else "Banka"


def _peek(data: bytes) -> str | None:
    """PDF metni (yalnız metin çıkarma; yapay zekâ yok). Açılamazsa None."""
    from app.parsers import extract_text

    for pw in [None, *db.pdf_passwords()]:
        try:
            return extract_text(data, pw)
        except Exception:  # noqa: BLE001
            continue
    return None


_CARD = re.compile(r"\b\d{4}[\s*xX•.-]{4,}(?:\d{4}[\s*xX•.-]*)?(\d{4})\b")
_IBAN = re.compile(r"\bTR\s?\d{2}(?:\s?\d{4}){5}\s?\d{2}\b")


def ident_of(text: str | None, sender_domain: str) -> str:
    if text:
        if m := _CARD.search(text):
            return f"card:{m.group(1)}"
        if m := _IBAN.search(text.upper()):
            return "iban:" + m.group(0).replace(" ", "")
    return f"bank:{sender_domain}"


def _label(ident: str) -> str:
    kind, _, v = ident.partition(":")
    return f"•••• {v}" if kind == "card" else f"{v[:2]}… {v[-4:]}" if kind == "iban" else ""


# ---------------------------------------------------------------- saklama

def stash(book_id: int, own: bool, name: str, data: bytes, sender_domain: str) -> dict:
    """Açık defter bağlamında çağrılır. Belgeyi okumadan sakla, sınırları uygula. Dönen: {id, bank, label}."""
    from app import verification

    text = _peek(data)
    ident = ident_of(text, sender_domain)
    bank = _bank_label(text, sender_domain)
    file = verification._store_pdf(data)  # app/storage (R2 / yerel)
    now = _iso(_now())
    with db.connect() as c:
        _init(c)
        lid = c.execute("INSERT INTO premium_wait (created_at, ident, bank, sender_domain, filename, file, size) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)", (now, ident, bank, sender_domain, name, file, len(data))).lastrowid
    with system.connect() as c:
        _init_sys(c)
        c.execute("INSERT OR REPLACE INTO premium_wait_index (book_id, own, local_id, size, created_at) VALUES (?, ?, ?, ?, ?)",
                  (book_id, int(own), lid, len(data), now))
    # kart/hesap başına en yeni PER_IDENT, kişi başı PER_USER
    with db.connect() as c:
        same = [r[0] for r in c.execute("SELECT id FROM premium_wait WHERE ident = ? ORDER BY id DESC", (ident,))]
        allr = [r[0] for r in c.execute("SELECT id FROM premium_wait ORDER BY id DESC")]
    drop = set(same[PER_IDENT:]) | set(allr[PER_USER:])
    if drop:
        _delete_here(book_id, own, sorted(drop))
    enforce_global()
    return {"id": lid, "bank": bank, "label": _label(ident)}


def _delete_here(book_id: int, own: bool, ids: list[int]) -> int:
    """Açık defterde bekleyenleri sil: storage'daki dosya + satır + sistem dizini."""
    if not ids:
        return 0
    q = ",".join("?" * len(ids))
    with db.connect() as c:
        _init(c)
        rows = c.execute(f"SELECT id, file FROM premium_wait WHERE id IN ({q})", ids).fetchall()
        c.execute(f"DELETE FROM premium_wait WHERE id IN ({q})", ids)
    for r in rows:
        with db.connect() as c:  # aynı dosya (aynı içerik) başka bekleyen satırda da varsa silinmez
            still = c.execute("SELECT 1 FROM premium_wait WHERE file = ?", (r[1],)).fetchone()
        if not still:
            try:
                db.remove_file("uploads", r[1])
            except Exception:  # noqa: BLE001
                log.exception("bekleyen belge dosyası silinemedi")
    with system.connect() as c:
        _init_sys(c)
        c.execute(f"DELETE FROM premium_wait_index WHERE book_id = ? AND own = ? AND local_id IN ({q})",
                  (book_id, int(own), *ids))
    return len(rows)


def _delete_in(book_id: int, own: bool, ids: list[int]) -> int:
    try:
        with _space(book_id, own):
            return _delete_here(book_id, own, ids)
    except LookupError:  # defter yok (silinmiş): yalnız dizin
        with system.connect() as c:
            _init_sys(c)
            c.execute("DELETE FROM premium_wait_index WHERE book_id = ? AND own = ?", (book_id, int(own)))
        return 0


def enforce_global() -> int:
    """Sunucu genelinde bekleyen toplamı sınırı aşıyorsa en eskileri sil."""
    cap = max_bytes()
    with system.connect() as c:
        _init_sys(c)
        total = c.execute("SELECT COALESCE(SUM(size), 0) FROM premium_wait_index").fetchone()[0]
        if total <= cap:
            return 0
        rows = c.execute("SELECT book_id, own, local_id, size FROM premium_wait_index ORDER BY created_at, local_id").fetchall()
    victims: dict[tuple[int, int], list[int]] = {}
    for b, o, lid, size in rows:
        if total <= cap:
            break
        victims.setdefault((b, o), []).append(lid)
        total -= size
    cur = db.db_path()
    n = 0
    for (b, o), ids in victims.items():
        here = _here(b, o, cur)
        n += _delete_here(b, bool(o), ids) if here else _delete_in(b, bool(o), ids)
    return n


def _here(book_id: int, own: int, cur_path: str) -> bool:
    u = system.user(book_id)
    if not u:
        return False
    p = system.own_db_path(u) if own else system.user_db_path(u)
    return bool(p) and os.path.abspath(p) == os.path.abspath(cur_path)


def tick(now: datetime | None = None) -> dict:
    """Gece işi: KEEP_DAYS'i dolanları sil; WARN_DAYS gün kala bir kez bildir."""
    now = now or _now()
    old = _iso(now - timedelta(days=KEEP_DAYS))
    warn = _iso(now - timedelta(days=KEEP_DAYS - WARN_DAYS))
    with system.connect() as c:
        _init_sys(c)
        expired = c.execute("SELECT book_id, own, local_id FROM premium_wait_index WHERE created_at <= ?", (old,)).fetchall()
        soon = c.execute("SELECT book_id, own, COUNT(*) FROM premium_wait_index WHERE created_at <= ? AND created_at > ? "
                         "AND warned = 0 GROUP BY book_id, own", (warn, old)).fetchall()
    groups: dict[tuple[int, int], list[int]] = {}
    for b, o, lid in expired:
        groups.setdefault((b, o), []).append(lid)
    removed = sum(_delete_in(b, bool(o), ids) for (b, o), ids in groups.items())
    warned = 0
    for b, o, n in soon:
        _notify(b, "Bekleyen ekstrelerin silinecek", f"{n} ekstren 7 gün içinde silinecek. Premium'la okunur ya da kendin yükle.",
                f"bekleyen:uyari:{b}:{o}:{now.date().isoformat()}")
        with system.connect() as c:
            c.execute("UPDATE premium_wait_index SET warned = 1 WHERE book_id = ? AND own = ? AND created_at <= ? AND created_at > ?",
                      (b, o, warn, old))
        warned += 1
    return {"removed": removed, "warned": warned, "global": enforce_global()}


def _notify(uid: int, title: str, body: str, pid: str) -> None:
    try:
        from app import push

        push.notify(uid, [push.item("belge", pid, title, body, push.R_OZET)])
    except Exception:  # noqa: BLE001
        log.exception("bekleyen belge bildirimi")


def notify_arrived(uid: int, bank: str) -> None:
    n = count()
    body = (f"📬 {bank} ekstren geldi. Premium'la otomatik okunur, ödeme günün hatırlatılır." if n <= 1
            else f"📬 Bekleyen {n} ekstren var. Premium'la otomatik okunur, ödeme günün hatırlatılır.")
    _notify(uid, "Ekstren geldi", body, f"bekleyen:{uid}:{_iso(_now())}")


# ---------------------------------------------------------------- okuma

def count() -> int:
    with db.connect() as c:
        _init(c)
        return c.execute("SELECT COUNT(*) FROM premium_wait").fetchone()[0]


def items() -> list[dict]:
    with db.connect() as c:
        _init(c)
        rows = c.execute("SELECT id, created_at, ident, bank, filename FROM premium_wait ORDER BY id DESC").fetchall()
    keep = timedelta(days=KEEP_DAYS)
    out = []
    for r in rows:
        created = datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        out.append({"id": r["id"], "created_at": r["created_at"], "bank": r["bank"], "label": _label(r["ident"]),
                    "kind": r["ident"].split(":", 1)[0], "filename": r["filename"],
                    "expires_at": _iso(created + keep)})
    return out


def _book_of(ctx: dict) -> tuple[int, bool]:
    """Açık defterin sistem tarafı anahtarı: (defter kullanıcısı, kendi defteri mi)."""
    if ctx.get("member_of") and ctx.get("own_book"):
        return ctx["id"], True
    return (ctx.get("member_of") or ctx["id"]), False


def self_upload(pid: int, ctx: dict) -> str:
    """"Kendim yükleyeyim": normal yükleme yolu gibi oku (okuma hakkı düşer; hak yoksa MailError). KeyError: yok."""
    from app import mailbox

    with db.connect() as c:
        _init(c)
        r = c.execute("SELECT * FROM premium_wait WHERE id = ?", (pid,)).fetchone()
    if not r:
        raise KeyError(pid)
    data = db.read_file("uploads", r["file"]) or b""
    if not data:
        b, o = _book_of(ctx)
        _delete_here(b, o, [pid])
        raise KeyError(pid)
    from app import doc_reads

    doc_reads.check(data)  # hak yoksa QuotaError: belge bekler, uç "hak doldu" yanıtı verir
    msg = str(mailbox.handle_pdf(r["filename"] or "ekstre.pdf", data))
    b, o = _book_of(ctx)
    _delete_here(b, o, [pid])
    return msg


def release(book_id: int) -> dict:
    """Premium'a geçildi: bu kullanıcının (ve kendi defterinin) bekleyenlerini sırayla oku. Yeni kart onayı geçerli."""
    from app import mail_approval, mailbox, plans

    done, held_total = [], 0
    with system.connect() as c:
        _init_sys(c)
        spaces = c.execute("SELECT DISTINCT book_id, own FROM premium_wait_index WHERE book_id = ?", (book_id,)).fetchall()
    for b, o in spaces:
        try:
            with _space(b, bool(o)) as ctx:
                if not plans.allows(ctx, "posta"):
                    continue
                with db.connect() as c:
                    _init(c)
                    rows = c.execute("SELECT * FROM premium_wait ORDER BY id").fetchall()
                for r in rows:
                    held: list[int] = []
                    data = db.read_file("uploads", r["file"]) or b""
                    try:
                        if data:
                            gate = mail_approval.make_gate(r["sender_domain"] or "", held)
                            done.append(str(mailbox.handle_pdf(r["filename"] or "ekstre.pdf", data, gate=gate)))
                    except Exception as e:  # noqa: BLE001 - biri okunamazsa ötekiler sürsün
                        log.warning("bekleyen belge okunamadı: %s", e)
                        done.append(f"⚠️ {r['filename']}: {str(e)[:200]}")
                    held_total += len(held)
                    _delete_here(b, bool(o), [r["id"]])
                if done:
                    db.log_mail({"message_id": f"<premium-wait-{_iso(_now())}>", "received_at": _now().date().isoformat(),
                                 "sender": "", "subject": "Bekleyen ekstreler", "kind": "pdf", "status": "ok",
                                 "detail": "\n".join(done)})
        except LookupError:
            continue
    return {"processed": len(done), "held": held_total, "details": done}


def release_async(book_id: int) -> None:
    import threading

    def go():
        try:
            release(book_id)
        except Exception:  # noqa: BLE001
            log.exception("bekleyen belgeler okunamadı")

    threading.Thread(target=go, name="premium-wait", daemon=True).start()
