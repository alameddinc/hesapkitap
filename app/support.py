"""Destek yazışması (yetenek "support_chat") ve okunamayan ekstrenin bildirimi (yetenek "upload_report", "upload_retry").

Kişi başı TEK yazışma: system.db → feedback satırları (user_id = yazışmanın sahibi) sırayla mesajlardır.
    author user    kullanıcının mesajı ("Sorun bildir", Destek'ten yazılan, ekstre bildirimi kind "ekstre"); eski
                   /api/feedback kayıtları da yazışmanın ilk mesajları olarak görünür
    author admin   yöneticinin yanıtı (kind "yanit"); action {type: "retry", ref: <ekstre bildirimi>} "Düzeltildi — tekrar
                   dene" eylemidir
    author system  sistem satırı (kind "sistem"): bekleyen dosya yeniden okundu
support_threads: yazışmanın durumu (acik | cozuldu), kullanıcının ve yöneticinin okuduğu son mesaj (okundu bilgisi,
okunmamış rozeti). Oturumsuz ve demo bildirimlerinin (user_id NULL) yazışması yoktur: yönetici görür, yanıtlayamaz.

Ekstre bildirimi (POST /api/support/report): hata kodu, dosya türü, sayfa sayısı, banka tahmini (IBAN kodu ya da başlık),
uygulama sürümü; kullanıcının yazdığı banka adı ve not. "Belgeyi bize gönder" açıksa (varsayılan kapalı):
    - yöneticiye dosyanın KENDİSİ gitmez: card_reader.anonymize(amounts=True) ile maskeli yapı (isim, açıklama, kart/hesap/
      TC no, e-posta ve tutarların rakamları maskeli; etiketler ve tarih/tutar düzeni kalır) + card_reader.diagnose
      (yalnız evet/hayır ve sayılar). Telegram'a yalnız özet (banka, kod, sayfa) gider.
    - dosya kullanıcının KENDİ defterinde (ortak defter üyesinde "Benim" defteri) "bekleyen yükleme" olarak en çok
      PENDING_DAYS gün durur (depo türü "pending"; yönetici dosyaya erişemez). "Tekrar dene" aynı yükleme yolundan okur
      (okuma hakkı kuralları aynen); okununca silinir. Süresi dolan gece silinir (jobs: "pending"); hesap silinince
      defter klasörüyle gider.
Push: yönetici yanıtlayınca kullanıcıya "destek" türü (ayarlardan kapatılabilir; metinde kişisel veri yok).
"""
import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app import db, feedback, system

log = logging.getLogger("hesapkitap.support")

PENDING_DAYS = 30
MAX_PENDING = 10          # defter başı bekleyen dosya
STRUCT_MAX = 60_000       # maskeli yapının en çok karakteri (yöneticiye)
MAX_NOTE = 1000
MAX_BANK = 80
STATUSES = ("acik", "cozuldu")
# yükleme hata satırının nedeni (istemci metni sunucudan alır; kod yöneticiye teşhis için)
CODES = {
    "ekstre_bulunamadi": "Kart ekstresi bulunamadı",
    "okunamadi": "Okuyucu belgeyi çözemedi",
    "sinir": "Sayfa / süre sınırı",
    "metin_yok": "PDF'te metin yok (taranmış)",
}
FILE_TYPES = ("pdf", "xlsx", "xls", "csv")
PENDING_SQL = """CREATE TABLE IF NOT EXISTS pending_uploads (  -- okunamayan, kullanıcının onayıyla bekleyen ekstre (app/support.py)
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,            -- UTC
    expires_at TEXT NOT NULL,            -- UTC; gece işi siler
    filename TEXT NOT NULL,              -- kullanıcının dosya adı (yalnız kendisine görünür)
    name TEXT NOT NULL,                  -- depodaki ad: pending/<sha256>.<uzantı>
    size INTEGER NOT NULL,
    feedback_id INTEGER,                 -- bağlı ekstre bildirimi (system.db feedback.id)
    book_owner INTEGER                   -- yüklendiği ortak defterin sahibi; NULL: kişinin kendi defteri
)"""


class SupportError(ValueError):
    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _json(v, default):
    try:
        out = json.loads(v) if v else default
    except ValueError:
        return default
    return out if isinstance(out, type(default)) else default


# ---------------------------------------------------------------- yükleme satırının bildirim bilgisi

def file_type(data: bytes, name: str) -> str:
    if b"%PDF" in data[:1024]:
        return "pdf"
    if data[:2] == b"PK":
        return "xlsx"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls"
    ext = (name or "").lower().rsplit(".", 1)[-1]
    return ext if ext in FILE_TYPES else "csv" if ext == "txt" else "pdf"


def upload_report(code: str, name: str, data: bytes, text: str | None) -> dict:
    """Okunamayan ekstre satırına eklenen hazır bilgi (istemci "Bildir" formunu bununla doldurur). Kişisel veri yok:
    kod, dosya türü, sayfa sayısı, banka tahmini (IBAN banka kodu ya da başlıktaki banka adı)."""
    from app import card_reader
    from app.parsers import pages_of

    has_text = bool(text and text.strip())
    if code == "okunamadi" and not has_text and file_type(data, name) == "pdf":
        code = "metin_yok"
    bank = ""
    pages = None
    if has_text:
        try:
            bank = card_reader.bank_of(text)
        except Exception:  # noqa: BLE001
            bank = ""
        pages = len(pages_of(text) or []) or None
    return {"code": code if code in CODES else "okunamadi", "file_type": file_type(data, name), "pages": pages,
            "bank_guess": bank, "has_text": has_text}


def check_report(raw: str | None) -> dict:
    """İstemcinin gönderdiği bildirim bilgisi: yalnız bilinen alanlar, kısa."""
    obj = _json(raw, {}) if raw and len(raw) <= 2048 else {}
    out = {"code": obj.get("code") if obj.get("code") in CODES else "okunamadi"}
    if obj.get("file_type") in FILE_TYPES:
        out["file_type"] = obj["file_type"]
    if isinstance(obj.get("pages"), int) and 0 < obj["pages"] < 10_000:
        out["pages"] = obj["pages"]
    for k, n in (("bank_guess", 40), ("app_version", 40), ("platform", 20)):
        if isinstance(obj.get(k), str) and obj[k].strip():
            out[k] = re.sub(r"\d{6,}", "", obj[k].strip())[:n]
    return out


def _clean_user_text(s: str | None, n: int) -> str:
    """Kullanıcının yazdığı banka adı / not: uzun rakam dizileri (kart, hesap, TC no) ve e-posta maskelenir."""
    s = (s or "").strip()[:n]
    s = re.sub(r"\S+@\S+", "[e-posta]", s)
    return re.sub(r"\d[\d\s-]{8,}\d", "[numara]", s)


def structure_of(data: bytes, passwords: list[str]) -> dict:
    """Yöneticiye giden maskeli yapı + teşhis. Dosyanın kendisi ve kişisel veri yok (app/card_reader.py)."""
    from app import card_reader
    from app.api.main import _try_text

    if file_type(data, "") != "pdf":
        return {"anon": "", "diag": {"not": "PDF değil (tablo); yapı çıkarılmadı"}}
    text = next((t for t in (_try_text(data, pw) for pw in [None, *passwords]) if t), None)
    if not text or not text.strip():
        return {"anon": "", "diag": {"not": "PDF'te metin yok ya da şifreli"}}
    try:
        diag = card_reader.diagnose(text)
    except Exception:  # noqa: BLE001 - teşhis bozulsa da maskeli yapı gider
        log.warning("teşhis çıkarılamadı", exc_info=True)
        diag = {"not": "teşhis çıkarılamadı"}
    anon = card_reader.anonymize(text, amounts=True)
    return {"anon": anon[:STRUCT_MAX], "diag": diag, "cut": len(anon) > STRUCT_MAX}


# ---------------------------------------------------------------- bekleyen dosyalar (kullanıcının kendi defteri)

def own_path(u: dict) -> str | None:
    """Kişinin kendi defteri: ortak defter üyesinde "Benim", diğerlerinde kendi defteri (yönetici: ana veritabanı)."""
    return system.own_db_path(u) if u.get("member_of") else system.user_db_path(u)


@contextmanager
def own_space(uid: int):
    u = system.user(uid)
    if not u:
        raise SupportError("Hesap bulunamadı", 404)
    with db.use_space(own_path(u), {**u, "own_book": True} if u.get("member_of") else u):
        yield u


def _pending_conn(c) -> None:
    c.execute(PENDING_SQL)
    if "book_owner" not in {r[1] for r in c.execute("PRAGMA table_info(pending_uploads)")}:
        try:
            c.execute("ALTER TABLE pending_uploads ADD COLUMN book_owner INTEGER")
        except Exception:  # noqa: BLE001 - eşzamanlı göç
            pass


def pending_room(data: bytes, filename: str) -> None:
    """Bildirim kaydedilmeden önce (own_space içinde): yer yoksa SupportError 409. Aynı içerik zaten bekliyorsa yer var."""
    import hashlib

    name = f"{hashlib.sha256(data).hexdigest()}.{file_type(data, filename)}"
    with db.connect() as c:
        _pending_conn(c)
        if c.execute("SELECT 1 FROM pending_uploads WHERE name = ?", (name,)).fetchone():
            return
        if c.execute("SELECT COUNT(*) FROM pending_uploads").fetchone()[0] >= MAX_PENDING:
            raise SupportError(f"En çok {MAX_PENDING} bekleyen dosya saklanabilir; önce birini sil ya da tekrar dene.", 409)


def upload_book() -> int | None:
    """İstekte açık defter: ortak defter üyesi ortak defterdeyse sahibin id'si, değilse None (kendi defteri)."""
    u = db.current_user() or {}
    return u["member_of"] if u.get("member_of") and not u.get("own_book") else None


@contextmanager
def target_space(uid: int, book_owner: int | None):
    """Tekrar denemenin kaydedileceği defter: yüklendiği defter. Kişi o ortak defterin üyesi değilse, defter yoksa ya da
    orada ekstre yükleme yetkisi yoksa kendi ("Benim") defteri. Verilen: (fallback_nedeni | None)."""
    from app import book

    u = system.user(uid)
    if not u:
        raise SupportError("Hesap bulunamadı", 404)
    why = None
    if book_owner:
        owner = system.user(book_owner)
        if u.get("member_of") != book_owner or not owner or owner.get("member_of") or owner.get("deleted_at"):
            why = "Ekstrenin yüklendiği ortak defter artık açık değil; Benim defterine kaydedildi."
        else:
            with db.use_space(system.user_db_path(u), u):
                if book.denied("POST", "/api/statements/upload"):
                    why = "Ortak defterde ekstre yükleme yetkin yok; Benim defterine kaydedildi."
            if not why:
                with db.use_space(system.user_db_path(u), u):
                    yield None
                return
    with own_space(uid):
        yield why


def save_pending(data: bytes, filename: str, fid: int, book_owner: int | None = None) -> dict:
    """Açık defterde (çağıran own_space içinde) bekleyen dosya. Aynı içerik zaten bekliyorsa onun süresi yenilenir."""
    import hashlib

    from app import storage

    name = f"{hashlib.sha256(data).hexdigest()}.{file_type(data, filename)}"
    now = datetime.now(timezone.utc)
    exp = (now + timedelta(days=PENDING_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with db.connect() as c:
        _pending_conn(c)
        old = c.execute("SELECT id FROM pending_uploads WHERE name = ?", (name,)).fetchone()
        if not old and c.execute("SELECT COUNT(*) FROM pending_uploads").fetchone()[0] >= MAX_PENDING:
            raise SupportError(f"En çok {MAX_PENDING} bekleyen dosya saklanabilir; önce birini sil ya da tekrar dene.", 409)
    storage.book_put("pending", name, data)
    with db.connect() as c:
        if old:
            c.execute("UPDATE pending_uploads SET expires_at = ?, feedback_id = ?, filename = ?, book_owner = ? WHERE id = ?",
                      (exp, fid, filename[:200], book_owner, old["id"]))
            pid = old["id"]
        else:
            pid = c.execute("INSERT INTO pending_uploads (created_at, expires_at, filename, name, size, feedback_id, book_owner) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)", (_now(), exp, filename[:200], name, len(data), fid, book_owner)).lastrowid
    return {"id": pid, "filename": filename[:200], "expires_at": exp}


def _has_table(c) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pending_uploads'").fetchone())


def pending_list() -> list[dict]:
    with db.connect() as c:
        if not _has_table(c):
            return []
        return [{"id": r["id"], "filename": r["filename"], "expires_at": r["expires_at"], "size": r["size"],
                 "feedback_id": r["feedback_id"]}
                for r in c.execute("SELECT * FROM pending_uploads WHERE expires_at > ? ORDER BY id DESC", (_now(),))]


def pending_get(pid: int) -> dict | None:
    with db.connect() as c:
        if not _has_table(c):
            return None
        r = c.execute("SELECT * FROM pending_uploads WHERE id = ? AND expires_at > ?", (pid, _now())).fetchone()
    return dict(r) if r else None


def pending_file(row: dict) -> bytes | None:
    from app import storage

    return storage.book_get("pending", row["name"])


def pending_delete(pid: int) -> bool:
    from app import storage

    with db.connect() as c:
        if not _has_table(c):
            return False
        r = c.execute("SELECT name FROM pending_uploads WHERE id = ?", (pid,)).fetchone()
        if not r:
            return False
        c.execute("DELETE FROM pending_uploads WHERE id = ?", (pid,))
        still = c.execute("SELECT 1 FROM pending_uploads WHERE name = ?", (r["name"],)).fetchone()
    if not still:
        try:
            storage.book_delete("pending", r["name"])
        except Exception:  # noqa: BLE001 - satır silindi; dosya gece temizliğinde de gitmez ama erişilemez
            log.warning("bekleyen dosya silinemedi")
    return True


def prune_pending(now: datetime | None = None) -> int:
    """Gece (her defterde): süresi dolan bekleyen dosyalar."""
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
    with db.connect() as c:
        if not _has_table(c):
            return 0
        ids = [r[0] for r in c.execute("SELECT id FROM pending_uploads WHERE expires_at <= ?", (ts,))]
    for pid in ids:
        pending_delete(pid)
    return len(ids)


# ---------------------------------------------------------------- yazışma

def _thread_row(c, uid: int) -> dict:
    r = c.execute("SELECT * FROM support_threads WHERE user_id = ?", (uid,)).fetchone()
    if r:
        return dict(r)
    # ilk kez: eski geri bildirimlerden durum ve okundu bilgisi
    rows = c.execute("SELECT id, status, author FROM feedback WHERE user_id = ? ORDER BY id", (uid,)).fetchall()
    users = [x for x in rows if x["author"] == "user"]
    status = "cozuldu" if users and all(x["status"] == "cozuldu" for x in users) else "acik"
    seen = max((x["id"] for x in users if x["status"] != "yeni"), default=0)
    upd = max((x["id"] for x in rows), default=0)
    at = c.execute("SELECT created_at FROM feedback WHERE id = ?", (upd,)).fetchone()
    t = {"user_id": uid, "status": status, "updated_at": at[0] if at else _now(), "user_seen": 0, "admin_seen": seen}
    c.execute("INSERT OR IGNORE INTO support_threads (user_id, status, updated_at, user_seen, admin_seen) "
              "VALUES (?, ?, ?, ?, ?)", (uid, status, t["updated_at"], 0, seen))
    return t


def touch(uid: int, reopen: bool = True) -> None:
    """Kullanıcı yazdı: yazışma açık, son mesaj zamanı (feedback.create çağırır)."""
    with system.connect() as c:
        _thread_row(c, uid)
        c.execute("UPDATE support_threads SET updated_at = ?" + (", status = 'acik'" if reopen else "") +
                  " WHERE user_id = ?", (_now(), uid))


def _add(c, uid: int, author: str, kind: str, body: str, admin_id: int | None = None, action: dict | None = None,
         context: dict | None = None) -> int:
    fid = c.execute("INSERT INTO feedback (user_id, created_at, message, context, status, demo, shots, author, admin_id, "
                    "kind, action) VALUES (?, ?, ?, ?, 'okundu', 0, '[]', ?, ?, ?, ?)",
                    (uid, _now(), body, json.dumps(context or {}, ensure_ascii=False), author, admin_id, kind,
                     json.dumps(action) if action else None)).lastrowid
    c.execute("UPDATE support_threads SET updated_at = ? WHERE user_id = ?", (_now(), uid))
    return fid


def _report_user(r) -> dict | None:
    rep = _json(r["report"], {})
    if not rep:
        return None
    return {k: rep[k] for k in ("code", "file_type", "pages", "bank_guess", "bank_name", "sent_doc") if k in rep}


def _msg(r, admin_seen: int, admin: bool, pending: dict[int, dict] | None, done: set[int]) -> dict:
    ctx = _json(r["context"], {})
    out = {"id": r["id"], "author": r["author"] or "user", "kind": r["kind"], "body": r["message"],
           "created_at": r["created_at"], "screenshots": len(feedback._shots(r))}
    if out["author"] == "user":
        out["seen"] = r["id"] <= admin_seen
    rep = _report_user(r)
    if rep:
        rep["code_label"] = CODES.get(rep.get("code"), "")
        out["report"] = rep
    act = _json(r["action"], {})
    if act.get("type") == "retry":
        ref = act.get("ref")
        out["action"] = {"type": "retry", "ref": ref, "done": ref in done,
                         "pending": (pending or {}).get(ref) if not admin else None}
    if out["kind"] == "ekstre" and not admin:
        out["pending"] = (pending or {}).get(r["id"])
    if admin:
        out["context"] = ctx
        out["structure"] = bool(r["structure"])
    else:  # kullanıcı kendi bağlamının yalnız ekranını görür (sürüm/platform zaten kendisinin)
        out["context"] = {k: ctx[k] for k in ("route", "error", "code") if ctx.get(k)}
    return out


def _pending_by_feedback(uid: int) -> dict[int, dict]:
    try:
        with own_space(uid):
            return {p["feedback_id"]: {"id": p["id"], "filename": p["filename"], "expires_at": p["expires_at"]}
                    for p in pending_list() if p["feedback_id"]}
    except Exception:  # noqa: BLE001 - defter açılamazsa yazışma yine görünür
        log.warning("bekleyen dosyalar okunamadı", exc_info=True)
        return {}


def _done_refs(rows) -> set[int]:
    """Tekrar denemesi başarılı olmuş ekstre bildirimleri (sistem satırının action.ref'i)."""
    out = set()
    for r in rows:
        if r["author"] == "system":
            a = _json(r["action"], {})
            if a.get("type") == "retried" and isinstance(a.get("ref"), int):
                out.add(a["ref"])
    return out


def thread(uid: int, admin: bool = False) -> dict:
    with system.connect() as c:
        t = _thread_row(c, uid)
        rows = c.execute("SELECT * FROM feedback WHERE user_id = ? ORDER BY id", (uid,)).fetchall()
    pending = None if admin else _pending_by_feedback(uid)
    done = _done_refs(rows)
    msgs = [_msg(r, t["admin_seen"], admin, pending, done) for r in rows]
    unread = sum(1 for m in msgs if m["author"] != "user" and m["id"] > t["user_seen"])
    admin_unread = sum(1 for m in msgs if m["author"] == "user" and m["id"] > t["admin_seen"])
    return {"thread": {"status": t["status"], "unread": unread, "admin_unread": admin_unread,
                       "updated_at": t["updated_at"]}, "messages": msgs}


def unread(uid: int) -> dict:
    with system.connect() as c:
        r = c.execute("SELECT status, user_seen FROM support_threads WHERE user_id = ?", (uid,)).fetchone()
        seen = r["user_seen"] if r else 0
        n = c.execute("SELECT COUNT(*) FROM feedback WHERE user_id = ? AND author <> 'user' AND id > ?",
                      (uid, seen)).fetchone()[0]
        has = bool(r) or bool(c.execute("SELECT 1 FROM feedback WHERE user_id = ? LIMIT 1", (uid,)).fetchone())
    return {"unread": n, "status": r["status"] if r else ("acik" if has else None)}


def mark_user_seen(uid: int) -> None:
    with system.connect() as c:
        _thread_row(c, uid)
        top = c.execute("SELECT MAX(id) FROM feedback WHERE user_id = ? AND author <> 'user'", (uid,)).fetchone()[0] or 0
        c.execute("UPDATE support_threads SET user_seen = MAX(user_seen, ?) WHERE user_id = ?", (top, uid))


def screenshot(uid: int, fid: int, n: int):
    with system.connect() as c:
        r = c.execute("SELECT user_id FROM feedback WHERE id = ?", (fid,)).fetchone()
    if not r or r["user_id"] != uid:
        return None
    return feedback.screenshot(fid, n)


def record_retry(uid: int, ref: int | None, ok: bool, note: str | None = None) -> None:
    """Bekleyen dosya yeniden okundu: yazışmaya sistem satırı (kişisel veri yok; dosya adı yazılmaz). note: kayıt
    yüklendiği defter yerine "Benim" defterine gittiyse nedeni."""
    if not ok:
        return
    with system.connect() as c:
        _thread_row(c, uid)
        _add(c, uid, "system", "sistem", "Bekleyen ekstre yeniden okundu ve kaydedildi." + (f" {note}" if note else ""),
             action={"type": "retried", "ref": ref} if ref else None)


# ---------------------------------------------------------------- ekstre bildirimi

def create_report(uid: int, key: str, report: dict, bank_name: str | None, note: str | None,
                  doc: tuple[bytes, str] | None, passwords: list[str], book_owner: int | None = None) -> dict:
    """Okunamayan ekstre bildirimi. doc: (baytlar, dosya adı) yalnız kullanıcı "Belgeyi bize gönder" dediyse."""
    bank = _clean_user_text(bank_name, MAX_BANK)
    text = _clean_user_text(note, MAX_NOTE)
    rep = {**report, "bank_name": bank or None, "sent_doc": bool(doc)}
    parts = ["Ekstre okunamadı"] + ([f"Banka: {bank}"] if bank else [])
    msg = " · ".join(parts) + (f"\n{text}" if text else "")
    struct = structure_of(doc[0], passwords) if doc else None
    ctx = {"route": "yukleme", "error": CODES.get(rep["code"], rep["code"]), "code": rep["code"]}
    for k in ("app_version", "platform"):
        if rep.get(k):
            ctx[k] = rep[k]
    with system.connect() as c:
        fid = c.execute("INSERT INTO feedback (user_id, created_at, message, context, status, client_key, demo, shots, "
                        "author, kind, report, structure) VALUES (?, ?, ?, ?, 'yeni', ?, 0, '[]', 'user', 'ekstre', ?, ?)",
                        (uid, _now(), msg, json.dumps(ctx, ensure_ascii=False), key,
                         json.dumps(rep, ensure_ascii=False),
                         json.dumps(struct, ensure_ascii=False) if struct else None)).lastrowid
    touch(uid)
    pending = None
    if doc:
        try:
            with own_space(uid):
                pending = save_pending(doc[0], doc[1] or "ekstre.pdf", fid, book_owner)
        except SupportError:
            raise
        except Exception:  # noqa: BLE001 - bildirim gitti; dosya saklanamadı
            log.exception("bekleyen dosya saklanamadı")
    return {"id": fid, "pending": pending, "message": msg, "summary": _summary(rep)}


def _summary(rep: dict) -> str:
    bank = rep.get("bank_name") or rep.get("bank_guess") or "banka bilinmiyor"
    bits = [bank, CODES.get(rep.get("code"), rep.get("code") or ""), rep.get("file_type") or ""]
    if rep.get("pages"):
        bits.append(f"{rep['pages']} sayfa")
    bits.append("yapı eklendi" if rep.get("sent_doc") else "belge yok")
    return " · ".join(b for b in bits if b)


def notify_report_async(fid: int, uid: int, summary: str) -> None:
    """Yöneticiye push + Telegram: yalnız özet (banka, kod, sayfa); kullanıcının notu ve yapı gitmez."""
    import threading

    threading.Thread(target=feedback.notify_admins, args=(fid, f"Ekstre bildirimi: {summary}", uid, False, None),
                     daemon=True, name="support-notify").start()


# ---------------------------------------------------------------- yönetici

def inbox(status: str | None = None) -> dict:
    names = {u["id"]: u for u in system.users()}
    with system.connect() as c:
        uids = [r[0] for r in c.execute("SELECT DISTINCT user_id FROM feedback WHERE user_id IS NOT NULL")]
        for uid in uids:
            _thread_row(c, uid)
        threads = {r["user_id"]: dict(r) for r in c.execute("SELECT * FROM support_threads")}
        last = {r["user_id"]: dict(r) for r in c.execute(
            "SELECT f.* FROM feedback f JOIN (SELECT user_id, MAX(id) m FROM feedback WHERE user_id IS NOT NULL "
            "GROUP BY user_id) x ON f.id = x.m")}
        last_user = {r["user_id"]: _json(r["context"], {}) for r in c.execute(
            "SELECT f.user_id, f.context FROM feedback f JOIN (SELECT user_id, MAX(id) m FROM feedback "
            "WHERE user_id IS NOT NULL AND author = 'user' AND (context LIKE '%platform%' OR context LIKE '%app_version%') "
            "GROUP BY user_id) x ON f.id = x.m")}  # bağlamı olan son kullanıcı mesajı (Destek'ten yazılan da taşır)
        unread = {r[0]: r[1] for r in c.execute(
            "SELECT f.user_id, COUNT(*) FROM feedback f JOIN support_threads t ON t.user_id = f.user_id "
            "WHERE f.author = 'user' AND f.id > t.admin_seen GROUP BY f.user_id")}
        anon = c.execute("SELECT COUNT(*), SUM(status = 'yeni') FROM feedback WHERE user_id IS NULL").fetchone()
    items, counts = [], {"acik": 0, "cozuldu": 0, "okunmamis": 0}
    for uid, t in threads.items():
        if uid not in last:
            continue
        counts[t["status"]] = counts.get(t["status"], 0) + 1
        if unread.get(uid):
            counts["okunmamis"] += 1
        if status and t["status"] != status:
            continue
        lr = last[uid]
        u = names.get(uid)
        ctx = last_user.get(uid, {})
        body = lr["message"] or ""
        items.append({"user": {"id": uid, "username": u["username"] if u else None,
                               "plan": (u or {}).get("plan")},
                      "status": t["status"], "unread": unread.get(uid, 0), "updated_at": t["updated_at"],
                      "last": {"author": lr["author"] or "user", "body": body if len(body) <= 160 else body[:157] + "…",
                               "created_at": lr["created_at"], "kind": lr["kind"]},
                      "context": {k: ctx[k] for k in ("platform", "app_version", "route") if ctx.get(k)}})
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    items.sort(key=lambda x: x["unread"] == 0)  # okunmamışlar üstte (sıralama kararlı: kendi içinde yeniden eskiye)
    return {"items": items, "counts": counts, "anon": {"count": anon[0] or 0, "new": anon[1] or 0}}


def anon_thread() -> dict:
    """Oturumsuz ve demo bildirimleri (yanıtlanamaz)."""
    with system.connect() as c:
        rows = c.execute("SELECT * FROM feedback WHERE user_id IS NULL ORDER BY id DESC LIMIT 200").fetchall()
    msgs = [_msg(r, 0, True, None, set()) | {"demo": bool(r["demo"]), "status": r["status"]} for r in reversed(rows)]
    return {"user": None, "thread": {"status": "acik", "unread": 0, "admin_unread": sum(r["status"] == "yeni" for r in rows),
                                     "updated_at": rows[0]["created_at"] if rows else None}, "messages": msgs}


def admin_thread(uid: int) -> dict | None:
    u = system.user(uid)
    with system.connect() as c:
        if not c.execute("SELECT 1 FROM feedback WHERE user_id = ? LIMIT 1", (uid,)).fetchone():
            return None
    out = thread(uid, admin=True)
    out["user"] = {"id": uid, "username": u["username"] if u else None, "plan": (u or {}).get("plan")}
    return out


def mark_admin_seen(uid: int) -> None:
    with system.connect() as c:
        _thread_row(c, uid)
        top = c.execute("SELECT MAX(id) FROM feedback WHERE user_id = ? AND author = 'user'", (uid,)).fetchone()[0] or 0
        c.execute("UPDATE support_threads SET admin_seen = MAX(admin_seen, ?) WHERE user_id = ?", (top, uid))
        c.execute("UPDATE feedback SET status = 'okundu' WHERE user_id = ? AND author = 'user' AND status = 'yeni'", (uid,))


def mark_anon_seen() -> None:
    with system.connect() as c:
        c.execute("UPDATE feedback SET status = 'okundu' WHERE user_id IS NULL AND status = 'yeni'")


def set_status(uid: int, status: str) -> None:
    with system.connect() as c:
        _thread_row(c, uid)
        c.execute("UPDATE support_threads SET status = ? WHERE user_id = ?", (status, uid))
        if status == "cozuldu":
            c.execute("UPDATE feedback SET status = 'cozuldu' WHERE user_id = ? AND author = 'user'", (uid,))


def structure(fid: int) -> dict | None:
    with system.connect() as c:
        r = c.execute("SELECT structure, report, user_id FROM feedback WHERE id = ?", (fid,)).fetchone()
    if not r or not r["structure"]:
        return None
    return {"id": fid, "report": _json(r["report"], {}), **_json(r["structure"], {})}


def reply(uid: int, admin_id: int, message: str, retry_ref: int | None = None, resolve: bool = False) -> dict:
    """Yöneticinin yanıtı. retry_ref: "Düzeltildi — tekrar dene" (kullanıcının ekstre bildirimi). Kullanıcıya push."""
    body = (message or "").strip()
    if not body:
        raise SupportError("Yanıtı yaz")
    if len(body) > feedback.MAX_MESSAGE:
        raise SupportError(f"Yanıt en fazla {feedback.MAX_MESSAGE} karakter olabilir")
    with system.connect() as c:
        if not c.execute("SELECT 1 FROM feedback WHERE user_id = ? LIMIT 1", (uid,)).fetchone():
            raise SupportError("Bu kişiyle yazışma yok", 404)
        if retry_ref is not None and not c.execute(
                "SELECT 1 FROM feedback WHERE id = ? AND user_id = ? AND kind = 'ekstre'", (retry_ref, uid)).fetchone():
            raise SupportError("Tekrar dene yalnız bu kişinin ekstre bildirimine bağlanır")
        _thread_row(c, uid)
        fid = _add(c, uid, "admin", "yanit", body, admin_id,
                   action={"type": "retry", "ref": retry_ref} if retry_ref is not None else None)
        top = c.execute("SELECT MAX(id) FROM feedback WHERE user_id = ? AND author = 'user'", (uid,)).fetchone()[0] or 0
        c.execute("UPDATE support_threads SET admin_seen = MAX(admin_seen, ?), status = ? WHERE user_id = ?",
                  (top, "cozuldu" if resolve else "acik", uid))
        c.execute("UPDATE feedback SET status = ? WHERE user_id = ? AND author = 'user' AND status <> 'cozuldu'",
                  ("cozuldu" if resolve else "okundu", uid))
    notify_user(uid, fid, retry_ref is not None)
    return {"id": fid}


DESTEK_MIN_APP = (1, 0, 9)  # Destek ekranı bu sürümde; eski uygulama Ayarlar'a gider


def notify_user(uid: int, fid: int, retry: bool) -> None:
    from app import push

    try:
        route = push.R_DESTEK if push.has_app(uid, DESTEK_MIN_APP) else push.R_AYARLAR
        title, body = (("Ekstre okuyucusu düzeltildi", "Yeniden yüklemeden tekrar deneyebilirsin.") if retry
                       else ("Destek yanıtladı", "Yanıtı görmek için dokun."))
        u = system.user(uid)
        if u and not u.get("disabled"):
            push.notify(uid, [push.item("destek", f"destek:{fid}", title, body, route)])
    except Exception:  # noqa: BLE001 - push hatası yanıtı bozmaz
        log.exception("destek push")
