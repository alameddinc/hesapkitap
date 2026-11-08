"""Okunmuş belgenin sonucu (yalnız hazır sürüm; app/doc_reads.py ile aynı kişi ve defter).

Aynı içerik (fiş fotoğrafı, fatura, dekont, yapay zekâyla okunan ekstre/döküm) aynı defterde yeniden gönderilirse
yapay zekâ yeniden çağrılmaz ve hak düşmez: TTL_DAYS gün içindeki başarılı okumanın sonucu döner ("cached": true).
- Anahtar (defter, sha256(içerik), tür). Defter = doc_reads._book: ortak defter sahibin kimliği, üyenin "Benim"
  defteri üyenin kimliği; bir defterin sonucu başka deftere hiç dönmez.
- Yalnız sonuç JSON'u saklanır (dosyanın kendisi değil). Okunamayan / hatalı okuma saklanmaz.
- Süre: okumada eskiyse silinir; ayrıca gece işi (jobs.run_global → prune). Hesap silinince defterin satırları gider
  (system.delete_user).
- Elle yeniden okuma (receipt-reread) önbelleği atlar; sonucu aynı fotoğrafın (file = receipt_file) satırına yazılır
  (refresh_file).
Kendi sunucusunda (kota yapay zekâ çağrısı başına) ve yöneticide hiçbir şey yapmaz: get None, put yok.
"""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

TTL_DAYS = 30
TTL = TTL_DAYS * 86400
FIS, BELGE, FATURA, DEKONT, EKSTRE, DOKUM = "fis", "belge", "fatura", "dekont", "ekstre", "dokum"
_VOLATILE = ("charged", "storage_warning", "cached")  # her yanıtta yeniden hesaplanır


def _now() -> int:
    from app import doc_reads

    return int(doc_reads._now().timestamp())


def _where() -> int | None:
    """Önbelleğin defteri (doc_reads._book); hazır sürümde sayılan kullanıcı yoksa (kendi sunucusu, yönetici) None."""
    from app import doc_reads

    u = doc_reads._subject()
    return doc_reads._book(u) if u else None


def get(key: bytes | str, kind: str) -> dict | None:
    """Bu defterde TTL içinde okunmuş aynı içeriğin sonucu; yoksa None. Eskiyse satır silinir."""
    from app import doc_reads, system

    book = _where()
    if book is None:
        return None
    h = doc_reads._key(key)
    with system.connect() as c:
        r = c.execute("SELECT created_at, result FROM doc_cache WHERE book_id = ? AND hash = ? AND kind = ?",
                      (book, h, kind)).fetchone()
        if not r:
            return None
        if r[0] < _now() - TTL:
            c.execute("DELETE FROM doc_cache WHERE book_id = ? AND hash = ? AND kind = ?", (book, h, kind))
            return None
    try:
        out = json.loads(r[1])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _dump(result: dict) -> str:
    clean = {k: v for k, v in result.items() if k not in _VOLATILE}
    return json.dumps(clean, ensure_ascii=False, default=lambda o: o.isoformat() if isinstance(o, date) else str(o))


def put(key: bytes | str, kind: str, result: dict, file: str | None = None) -> None:
    """Başarılı okumanın sonucunu sakla (aynı anahtar varsa yenilenir). Hata okumayı bozmaz."""
    from app import doc_reads, system

    book = _where()
    if book is None or not isinstance(result, dict):
        return
    try:
        with system.connect() as c:
            c.execute("INSERT INTO doc_cache (book_id, hash, kind, created_at, file, result) VALUES (?, ?, ?, ?, ?, ?) "
                      "ON CONFLICT(book_id, hash, kind) DO UPDATE SET created_at = excluded.created_at, "
                      "file = excluded.file, result = excluded.result",
                      (book, doc_reads._key(key), kind, _now(), file, _dump(result)))
    except Exception:  # noqa: BLE001 - önbellek yazılamadıysa okuma yine sonuç versin
        import logging

        logging.getLogger("ekstre.doc_cache").exception("okuma önbelleği yazılamadı")


def for_file(file: str) -> dict | None:
    """Bu defterde receipt_file'ı file olan en yeni önbellek sonucu (TTL içinde; elle yeniden okumada kullanıcının
    düzelttiği kalemleri bulmak için: yapay zekânın önceki okuması). Yoksa None."""
    from app import system

    book = _where()
    if book is None or not file:
        return None
    with system.connect() as c:
        r = c.execute("SELECT result FROM doc_cache WHERE book_id = ? AND file = ? AND created_at >= ? "
                      "ORDER BY created_at DESC LIMIT 1", (book, file, _now() - TTL)).fetchone()
    try:
        out = json.loads(r[0]) if r else None
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def refresh_file(file: str, update) -> int:
    """Bu defterde receipt_file'ı file olan önbellek satırlarını update(result) -> result ile yenile (elle yeniden
    okuma). Yenilenen satır sayısı."""
    from app import system

    book = _where()
    if book is None or not file:
        return 0
    n = 0
    with system.connect() as c:
        rows = c.execute("SELECT hash, kind, result FROM doc_cache WHERE book_id = ? AND file = ?", (book, file)).fetchall()
        for h, kind, raw in rows:
            try:
                res = json.loads(raw)
            except ValueError:
                continue
            c.execute("UPDATE doc_cache SET result = ?, created_at = ? WHERE book_id = ? AND hash = ? AND kind = ?",
                      (_dump(update(res)), _now(), book, h, kind))
            n += 1
    return n


def prune(now: float | None = None) -> int:
    """TTL'i dolan satırları sil (gece işi). Silinen sayı."""
    from app import system

    cut = int(now if now is not None else _now()) - TTL
    with system.connect() as c:
        return c.execute("DELETE FROM doc_cache WHERE created_at < ?", (cut,)).rowcount


class Slot:
    """Tek belgenin önbellek yeri: reading() ile açılır; okuyucu (parsers.parse_statement, bank_pdf.parse_account_pdf)
    current() ile alır, yapay zekâdan hemen önce get(), sonra put(result) çağırır. hit: get bir sonuç döndürdü mü
    (yanıtta cached)."""

    def __init__(self, key: bytes | str, kind: str):
        self.key, self.kind, self.hit = key, kind, False

    def get(self) -> dict | None:
        out = get(self.key, self.kind)
        self.hit = out is not None
        return out

    def put(self, result: dict) -> None:
        put(self.key, self.kind, result)


_current: ContextVar["Slot | None"] = ContextVar("doc_cache_slot", default=None)


@contextmanager
def reading(key: bytes | str, kind: str):
    """Bu bloktaki ekstre/döküm okuması için önbellek yeri (okuyucunun imzası değişmez)."""
    slot = Slot(key, kind)
    token = _current.set(slot)
    try:
        yield slot
    finally:
        _current.reset(token)


def current() -> "Slot | None":
    return _current.get()


def hit(result: dict) -> dict:
    """Önbellekten dönen yanıt: {..., cached: true}; hak düşmediği için charged yok."""
    return {**{k: v for k, v in result.items() if k not in _VOLATILE}, "cached": True}
