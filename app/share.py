"""Telefondan "Paylaş": iOS Kısayollar (kişisel anahtarla) ya da Android'de yüklü uygulama (paylaşım hedefi).

- Kart ekstresi / hesap dökümü PDF'i: hemen içe aktarılır (e-posta ve Telegram ile aynı yol).
- Fiş fotoğrafı/PDF'i ve fatura: okunur, "gelen kutusu"nda bekler; uygulamada onaylanınca kaydedilir
  (yanlış okuma sessizce gider olmasın).

Ortak defterde gelen kutusu: sahip ve tam yetkili üye hepsini görür ve kapatır; diğer üyeler yalnız kendi
gönderdiklerini (sent_by). Sahibin e-postayla/paylaşımla gelen belgesi (fatura, fiş: mağaza, tutar, kalemler) başka
üyeye görünmez, onu kapatamaz.
"""
import json

from app import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_inbox (
    id INTEGER PRIMARY KEY,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    filename TEXT,
    kind TEXT NOT NULL,          -- receipt | bill
    doc TEXT NOT NULL,           -- okunan belge (JSON)
    status TEXT NOT NULL DEFAULT 'bekliyor',  -- bekliyor | kaydedildi | silindi
    sent_by INTEGER              -- gönderen kullanıcı (ortak defter üyesi); NULL: sahip / e-posta / eski kayıt
);
"""
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def _init(c) -> None:
    c.executescript(SCHEMA)
    if "sent_by" not in {r[1] for r in c.execute("PRAGMA table_info(shared_inbox)")}:
        c.execute("ALTER TABLE shared_inbox ADD COLUMN sent_by INTEGER")


def sender() -> int | None:
    """Kaydı yazan: ortak defter üyesiyse onun kimliği (sahibin kendi ve e-postayla gelen kayıtları NULL)."""
    from app import book

    m = book.member()
    return m["id"] if m else None


def _own_only() -> int | None:
    """Yalnız kendi gönderdiklerini görecek üyenin kimliği; sahip ve tam yetkili üye için None (hepsi)."""
    from app import book

    m = book.member()
    return m["id"] if m and book.role(m) != "full" else None


def _is_statement(data: bytes) -> bool:
    """PDF kart ekstresi ya da hesap dökümü mü (fiş/fatura değil)?"""
    import re

    from app.mailbox import _looks_like_account_statement
    from app.parsers import extract_text

    for pw in [None, *db.pdf_passwords()]:
        try:
            text = extract_text(data, pw)
        except Exception:  # noqa: BLE001 - şifreli: sıradaki
            continue
        if _looks_like_account_statement(text):
            return True
        t = text.replace("İ", "i").lower()
        return bool(re.search(r"son [öo]deme tarihi|hesap kesim|asgari [öo]deme|d[öo]nem borcu", t)) and "fatura" not in t[:1500]
    return True  # açılamayan (şifreli) PDF: ekstre yolu şifre hatasını söyler


def process(name: str, data: bytes, mime: str) -> dict:
    """Tek dosyayı işle; {status: ok|bekliyor|hata, message}."""
    from app import mailbox, receipt

    name = name or "dosya"
    is_pdf = mime == "application/pdf" or name.lower().endswith(".pdf")
    if is_pdf and _is_statement(data):
        from app import undo

        try:
            with undo.batch(f"Telefondan: {name}"):  # yanlışsa Ekstreler → Son yüklemeler'den geri alınır
                msg = mailbox.handle_pdf(name, data)
            if getattr(msg, "inbox_id", None):  # rakamlar tutmadı: Gelen kutusu'nda onay bekliyor (app/verification.py)
                return {"status": "bekliyor", "message": str(msg), "id": msg.inbox_id, "verification": "dogrulanamadi"}
            return {"status": "ok", "message": msg}
        except Exception as e:  # noqa: BLE001 - kullanıcıya göster
            return {"status": "hata", "message": str(e)}
    if not is_pdf and mime not in IMAGE_TYPES:
        return {"status": "hata", "message": f"{name}: sadece PDF ya da fotoğraf paylaşılabilir"}
    from fastapi import HTTPException

    from app.api.main import _read_document

    # uygulamadaki okumayla aynı yol (app/api/main._read_document): fotoğraf küçültülür ve EXIF'ten arındırılır, aynı
    # içerik önbellekten döner (yapay zekâ yok, hak düşmez), önbellekte yoksa yeniden sayılır (forget), okunamayan
    # fotoğrafta günün ücretsiz denemeleri; fiş fotoğrafı / fatura dosyası saklanır. Eskiden burada önbelleğe bakılmıyor
    # ve tekrar kaydı düşürülmüyordu: aynı fotoğraf her paylaşımda yapay zekâya gidip hak düşmüyordu (güvenlik denetimi)
    try:
        doc = _read_document(data, "application/pdf" if is_pdf else mime)
    except HTTPException as e:  # hak doldu (QuotaFull) ya da okunamadı
        return {"status": "hata", "message": str(e.detail)}
    except receipt.ReceiptError as e:
        return {"status": "hata", "message": str(e)}
    if doc.get("unreadable"):
        return {"status": "hata", "message": f"{name}: {doc.get('message') or 'okunamadı'}"}
    with db.connect() as c:
        _init(c)
        iid = c.execute("INSERT INTO shared_inbox (filename, kind, doc, sent_by) VALUES (?, ?, ?, ?)",
                        (name, doc["type"], json.dumps(doc, ensure_ascii=False), sender())).lastrowid
    if doc["type"] == "bill":
        what = f"Fatura: {doc.get('provider') or '?'} {doc.get('amount') or ''} TL"
    else:
        what = f"Fiş: {doc.get('merchant') or '?'} {doc.get('total') or ''} TL"
    out = {"status": "bekliyor", "message": f"{what} · uygulamada onayla", "id": iid}
    if warn := db.take_storage_warning():
        out["storage_warning"] = warn
    return out


def inbox() -> list[dict]:
    mine = _own_only()
    with db.connect() as c:
        _init(c)
        rows = db.rows(c.execute("SELECT * FROM shared_inbox WHERE status = 'bekliyor' "
                                 + ("AND sent_by = ? " if mine else "") + "ORDER BY id DESC", (mine,) if mine else ()))
    for r in rows:
        r["doc"] = json.loads(r["doc"])
    return rows


def close(iid: int, status: str) -> bool:
    """Kaydı kapat (kaydedildi | silindi). Üye yalnız kendi gönderdiğini kapatır (başkasınınki yok sayılır: False)."""
    mine = _own_only()
    with db.connect() as c:
        _init(c)
        return c.execute("UPDATE shared_inbox SET status = ? WHERE id = ?" + (" AND sent_by = ?" if mine else ""),
                         (status, iid, mine) if mine else (status, iid)).rowcount > 0
