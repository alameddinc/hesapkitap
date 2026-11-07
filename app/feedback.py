"""Uygulama içi "Sorun bildir" (yetenek "feedback"): kullanıcının mesajı, isteğe bağlı ekran görüntüleri ve bağlamı
yöneticiye gelir (GitHub'a değil).

- POST /api/feedback (multipart): message (1–2000), screenshots (0–3 resim, her biri ≤ 5 MB, jpeg/png/webp; tür
  baytlardan anlaşılır, dosya adına/başlığa bakılmaz), context (isteğe bağlı JSON ≤ 4 KB: route, app_version, platform,
  error, code, book). Girişli kullanıcı, ortak defter üyesi (her yetki), demo ve giriş ekranı (oturumsuz) gönderebilir.
- Sınır: kullanıcı başı (oturumsuzda bağlantı başı) saatte RATE_MAX. Sayım system.db'deki satırlardan: birden çok işçide
  de tutar. Bağlantı adresi saklanmaz; yalnız sınır için tuzlu kısa özeti (client_key).
- Saklama: system.db → feedback; ekran görüntüleri depoda main/feedback/<id>-<n>.<uzantı> (yerelde data/feedback,
  herkese açık klasör değil). Yönetici /api/admin/feedback uçlarından görür.
- Bildirim: yöneticilere push (tür "geri_bildirim") ve yöneticinin Telegram'ı (ilk ekran görüntüsüyle). Arka planda;
  hata isteği bozmaz.
- Hesap silinince kişinin geri bildirimleri ve görüntüleri de silinir (forget_user; system.delete_user çağırır).
"""
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from app import storage, system

log = logging.getLogger("hesapkitap.feedback")

MAX_MESSAGE = 2000
MAX_SHOTS = 3
MAX_SHOT_BYTES = 5 * 1024 * 1024
MAX_CONTEXT = 4096
RATE_MAX = 10           # saatte
RATE_WINDOW = 3600
STATUSES = ("yeni", "okundu", "cozuldu")
CONTEXT_KEYS = ("route", "app_version", "platform", "error", "code", "book")
EXT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
PREFIX = "main/feedback/"


class FeedbackError(ValueError):
    """Kullanıcıya gösterilecek Türkçe neden; status: HTTP durumu."""

    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def image_ext(data: bytes) -> str | None:
    """Baytlardan resim türü (jpg | png | webp); başka bir şeyse None."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def check_message(message: str | None) -> str:
    m = (message or "").strip()
    if not m:
        raise FeedbackError("Ne olduğunu kısaca yaz")
    if len(m) > MAX_MESSAGE:
        raise FeedbackError(f"Mesaj en fazla {MAX_MESSAGE} karakter olabilir")
    return m


def check_context(raw: str | None) -> dict:
    """İstemcinin bağlamı: yalnız bilinen alanlar, kısa metin olarak. Bozuk JSON 422."""
    if not raw:
        return {}
    if len(raw.encode()) > MAX_CONTEXT:
        raise FeedbackError("Bağlam bilgisi çok uzun")
    try:
        obj = json.loads(raw)
    except ValueError as e:
        raise FeedbackError("Bağlam bilgisi okunamadı") from e
    if not isinstance(obj, dict):
        raise FeedbackError("Bağlam bilgisi okunamadı")
    out = {}
    for k in CONTEXT_KEYS:
        v = obj.get(k)
        if v is None or isinstance(v, (dict, list)):
            continue
        out[k] = str(v)[:1000]
    return out


def check_shots(shots: list[bytes]) -> list[tuple[bytes, str]]:
    if len(shots) > MAX_SHOTS:
        raise FeedbackError(f"En fazla {MAX_SHOTS} ekran görüntüsü eklenebilir")
    out = []
    for i, data in enumerate(shots, 1):
        if not data:
            continue
        if len(data) > MAX_SHOT_BYTES:
            raise FeedbackError(f"{i}. görüntü çok büyük (en fazla {MAX_SHOT_BYTES // (1024 * 1024)} MB)", 413)
        ext = image_ext(data)
        if not ext:
            raise FeedbackError(f"{i}. dosya resim değil (JPEG, PNG ya da WebP olmalı)")
        out.append((data, ext))
    return out


def client_key(ip: str | None) -> str:
    """Bağlantı adresinin tuzlu kısa özeti (yalnız saatlik sınır için; adres saklanmaz)."""
    salt = os.environ.get("SECRET_KEY") or system.get("feedback_salt")
    if not salt:
        salt = hashlib.sha256(os.urandom(32)).hexdigest()
        system.put("feedback_salt", salt)
    return hashlib.sha256(f"{salt}:{ip or ''}".encode()).hexdigest()[:24]


def limited(uid: int | None, key: str) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(seconds=RATE_WINDOW)).strftime("%Y-%m-%d %H:%M:%S")
    with system.connect() as c:
        if uid:
            n = c.execute("SELECT COUNT(*) FROM feedback WHERE user_id = ? AND created_at >= ?", (uid, since)).fetchone()[0]
        else:
            n = c.execute("SELECT COUNT(*) FROM feedback WHERE user_id IS NULL AND client_key = ? AND created_at >= ?",
                          (key, since)).fetchone()[0]
    return n >= RATE_MAX


def create(message: str, context: dict, shots: list[tuple[bytes, str]], uid: int | None, key: str,
           demo: bool = False) -> int:
    with system.connect() as c:
        fid = c.execute("INSERT INTO feedback (user_id, created_at, message, context, status, client_key, demo, shots) "
                        "VALUES (?, ?, ?, ?, 'yeni', ?, ?, '[]')",
                        (uid, _now(), message, json.dumps(context, ensure_ascii=False), key, int(demo))).lastrowid
    names = []
    for n, (data, ext) in enumerate(shots):
        name = f"{fid}-{n}.{ext}"
        storage.put(PREFIX + name, data, EXT_TYPES[ext])
        names.append(name)
    if names:
        with system.connect() as c:
            c.execute("UPDATE feedback SET shots = ? WHERE id = ?", (json.dumps(names), fid))
    return fid


def _shots(r) -> list[str]:
    try:
        v = json.loads(r["shots"] or "[]")
    except ValueError:
        return []
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _out(r, names: dict[int, str]) -> dict:
    try:
        ctx = json.loads(r["context"] or "{}")
    except ValueError:
        ctx = {}
    uid = r["user_id"]
    return {"id": r["id"], "created_at": r["created_at"], "message": r["message"], "context": ctx, "status": r["status"],
            "admin_note": r["admin_note"], "demo": bool(r["demo"]), "screenshots": len(_shots(r)),
            "user": {"id": uid, "username": names.get(uid)} if uid else None}


def _names() -> dict[int, str]:
    return {u["id"]: u["username"] for u in system.users()}


def list_items(status: str | None = None, limit: int = 100) -> dict:
    with system.connect() as c:
        q, args = "SELECT * FROM feedback", []
        if status:
            q, args = q + " WHERE status = ?", [status]
        rows = c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        counts = {s: 0 for s in STATUSES} | {r[0]: r[1] for r in c.execute(
            "SELECT status, COUNT(*) FROM feedback GROUP BY status")}
    names = _names()
    return {"items": [_out(r, names) for r in rows], "counts": counts}


def get(fid: int) -> dict | None:
    with system.connect() as c:
        r = c.execute("SELECT * FROM feedback WHERE id = ?", (fid,)).fetchone()
    return _out(r, _names()) if r else None


def screenshot(fid: int, n: int) -> tuple[bytes, str] | None:
    with system.connect() as c:
        r = c.execute("SELECT shots FROM feedback WHERE id = ?", (fid,)).fetchone()
    names = _shots(r) if r else []
    if not 0 <= n < len(names):
        return None
    try:
        data = storage.get(PREFIX + names[n])
    except FileNotFoundError:
        return None
    return data, EXT_TYPES.get(names[n].rsplit(".", 1)[-1], "application/octet-stream")


def update(fid: int, status: str | None = None, admin_note: str | None = None) -> dict | None:
    sets, args = [], []
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if admin_note is not None:
        sets.append("admin_note = ?")
        args.append(admin_note.strip() or None)
    with system.connect() as c:
        if not c.execute("SELECT 1 FROM feedback WHERE id = ?", (fid,)).fetchone():
            return None
        if sets:
            c.execute(f"UPDATE feedback SET {', '.join(sets)} WHERE id = ?", (*args, fid))
    return get(fid)


def forget_user(uid: int) -> int:
    """Hesap silinince: kişinin geri bildirimleri ve ekran görüntüleri."""
    with system.connect() as c:
        rows = c.execute("SELECT shots FROM feedback WHERE user_id = ?", (uid,)).fetchall()
        c.execute("DELETE FROM feedback WHERE user_id = ?", (uid,))
    for r in rows:
        for name in _shots(r):
            try:
                storage.delete(PREFIX + name)
            except Exception:  # noqa: BLE001 - dosya zaten yoksa da satır silindi
                log.warning("geri bildirim görüntüsü silinemedi: %s", name)
    return len(rows)


# ---------------------------------------------------------------- yöneticiye bildirim

def admins() -> list[dict]:
    return [u for u in system.users() if u["role"] == "admin" and not u["disabled"]]


def _who(uid: int | None, demo: bool) -> str:
    if demo:
        return "demo ziyaretçisi"
    if uid:
        u = system.user(uid)
        return u["username"] if u else f"#{uid}"
    return "giriş yapmamış biri"


def notify_admins(fid: int, message: str, uid: int | None, demo: bool, first_shot: tuple[bytes, str] | None) -> None:
    """Push (yöneticilerin telefonu) ve yöneticinin Telegram'ı. Her adım ayrı korunur; hiçbiri istisna fırlatmaz."""
    from app import push

    who = _who(uid, demo)
    short = message if len(message) <= 180 else message[:177] + "…"
    for a in admins():
        try:
            push.notify(a["id"], [push.item("geri_bildirim", f"geri-bildirim:{fid}", f"Geri bildirim: {who}", short,
                                            push.R_AYARLAR)])
        except Exception:  # noqa: BLE001
            log.exception("geri bildirim push")
    try:
        _telegram(fid, message, who, first_shot)
    except Exception:  # noqa: BLE001
        log.exception("geri bildirim telegram")


def _telegram(fid: int, message: str, who: str, first_shot: tuple[bytes, str] | None) -> None:
    import html

    from app import auth, db, telegram

    # Telegram sohbeti yöneticinin (ana) defterinde; tek kullanıcılı kurulumda da ana veritabanı
    a = next(iter(admins()), None)
    if not a and not auth.disabled():
        return
    with db.use_space(system.user_db_path(a) if a else None, a):
        if not telegram.enabled():
            return
        text = f"📝 Geri bildirim #{fid} · {html.escape(who)}\n\n{html.escape(message)}"
        if first_shot:
            telegram.send_photo(f"geri-bildirim-{fid}.{first_shot[1]}", first_shot[0], text[:1000])
        else:
            telegram.send(text[:4000])


def notify_async(*args) -> None:
    threading.Thread(target=notify_admins, args=args, daemon=True, name="feedback-notify").start()
