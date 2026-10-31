"""Idempotency-Key (isteğe bağlı başlık): ağ zaman aşımından sonra tekrar basılan "Kaydet" çift kayıt açmasın.

Aynı kullanıcı + defter + anahtar ile 24 saat içinde gelen ikinci yazma isteği (POST/PATCH/PUT/DELETE) işlemi tekrar
yapmaz; ilk yanıt (durum kodu + gövde) aynen döner, "Idempotent-Replayed: true" başlığıyla. İlk istek hâlâ sürüyorsa
sonucu WAIT saniye beklenir, bitmezse 409 "İşlem sürüyor". Aynı anahtar başka bir istek (yöntem, yol, sorgu ya da gövde
farklı) için gelirse 422. 5xx yanıt saklanmaz (tekrar denenebilir). Kayıtlar defterin kendi veritabanında
(idempotency tablosu); 24 saatten eskiler her yeni anahtarda temizlenir.
"""
import hashlib
import re
import time

from app import db

TTL = 24 * 3600
WAIT = 20.0            # sürmekte olan aynı istek için en fazla bekleme (sn)
MAX_BODY = 1024 * 1024  # bundan büyük yanıt saklanmaz (o istek için koruma yok)
KEY_RE = re.compile(r"^[\w.:\-]{8,128}$")
METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class KeyError_(ValueError):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def fingerprint(method: str, path: str, query: str, body: bytes, content_type: str | None = None) -> str:
    """İsteğin parmak izi. Çok parçalı gövdede (dosya yükleme) sınır dizesi her gönderimde rastgele: nötrlenir, aynı
    dosyanın tekrarı aynı istek sayılır."""
    m = re.search(r"boundary=\"?([^\";]+)", content_type or "")
    if m and (content_type or "").lower().startswith("multipart/"):
        body = body.replace(m.group(1).encode(), b"--sinir--")
    h = hashlib.sha256()
    for part in (method.encode(), path.encode(), query.encode(), body):
        h.update(len(part).to_bytes(8, "big") + part)
    return h.hexdigest()


def _k(uid: int | None, key: str) -> str:
    return f"{uid or 0}:{key}"


def begin(uid: int | None, key: str, fp: str) -> dict | None:
    """İlk istekse kayıt açar ve None döner (işlem yapılacak). Daha önce görülmüşse kayıt (state running/done).
    Anahtar geçersizse ya da başka istek için kullanılmışsa KeyError_."""
    if not KEY_RE.match(key or ""):
        raise KeyError_(422, "Idempotency-Key geçersiz: 8–128 karakter, harf, rakam, - _ . :")
    now = time.time()
    with db.connect() as c:
        c.execute("DELETE FROM idempotency WHERE created_at < ?", (now - TTL,))
        cur = c.execute("INSERT OR IGNORE INTO idempotency (key, fingerprint, state, created_at) VALUES (?, ?, 'running', ?)",
                        (_k(uid, key), fp, now))
        if cur.rowcount:
            return None
        row = dict(c.execute("SELECT * FROM idempotency WHERE key = ?", (_k(uid, key),)).fetchone())
    if row["fingerprint"] != fp:
        raise KeyError_(422, "Bu Idempotency-Key başka bir istek için kullanıldı; yeni işlem için yeni anahtar üret")
    return row


def get(uid: int | None, key: str) -> dict | None:
    with db.connect() as c:
        r = c.execute("SELECT * FROM idempotency WHERE key = ?", (_k(uid, key),)).fetchone()
    return dict(r) if r else None


def finish(uid: int | None, key: str, status: int, body: bytes, content_type: str | None) -> None:
    with db.connect() as c:
        if status >= 500 or len(body) > MAX_BODY:  # sunucu hatası: tekrar denenebilsin
            c.execute("DELETE FROM idempotency WHERE key = ?", (_k(uid, key),))
        else:
            c.execute("UPDATE idempotency SET state = 'done', status = ?, body = ?, content_type = ? WHERE key = ?",
                      (status, body, content_type, _k(uid, key)))


def abort(uid: int | None, key: str) -> None:
    with db.connect() as c:
        c.execute("DELETE FROM idempotency WHERE key = ?", (_k(uid, key),))
