"""Telefondan "Paylaş": iOS Kısayollar (kişisel anahtarla) ya da Android'de yüklü uygulama (paylaşım hedefi).

- Kart ekstresi / hesap dökümü PDF'i: hemen içe aktarılır (e-posta ve Telegram ile aynı yol).
- Fiş fotoğrafı/PDF'i ve fatura: okunur, "gelen kutusu"nda bekler; uygulamada onaylanınca kaydedilir
  (yanlış okuma sessizce gider olmasın).
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
    status TEXT NOT NULL DEFAULT 'bekliyor'   -- bekliyor | kaydedildi | silindi
);
"""
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def _init(c) -> None:
    c.executescript(SCHEMA)


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
            return {"status": "ok", "message": msg}
        except Exception as e:  # noqa: BLE001 - kullanıcıya göster
            return {"status": "hata", "message": str(e)}
    if not is_pdf and mime not in IMAGE_TYPES:
        return {"status": "hata", "message": f"{name}: sadece PDF ya da fotoğraf paylaşılabilir"}
    from app.api.main import BILL_TYPES, CATEGORIES, EXPENSE_CATEGORIES

    cats = sorted(set(CATEGORIES) | set(EXPENSE_CATEGORIES) | set(db.known_categories()) - {"Ödeme", "Kategorisiz"})
    try:
        doc = receipt.parse_document(data, "application/pdf" if is_pdf else mime, cats,
                                     sorted({r["category"] for r in db.list_item_rules()}))
    except receipt.ReceiptError as e:
        return {"status": "hata", "message": str(e)}
    if doc["type"] == "bill" and (is_pdf or mime in BILL_TYPES):
        doc["file_name"] = db.save_bill_file(data, BILL_TYPES.get("application/pdf" if is_pdf else mime, ".pdf"))
    with db.connect() as c:
        _init(c)
        iid = c.execute("INSERT INTO shared_inbox (filename, kind, doc) VALUES (?, ?, ?)",
                        (name, doc["type"], json.dumps(doc, ensure_ascii=False))).lastrowid
    if doc["type"] == "bill":
        what = f"Fatura: {doc.get('provider') or '?'} {doc.get('amount') or ''} TL"
    else:
        what = f"Fiş: {doc.get('merchant') or '?'} {doc.get('total') or ''} TL"
    return {"status": "bekliyor", "message": f"{what} · uygulamada onayla", "id": iid}


def inbox() -> list[dict]:
    with db.connect() as c:
        _init(c)
        rows = db.rows(c.execute("SELECT * FROM shared_inbox WHERE status = 'bekliyor' ORDER BY id DESC"))
    for r in rows:
        r["doc"] = json.loads(r["doc"])
    return rows


def close(iid: int, status: str) -> bool:
    with db.connect() as c:
        _init(c)
        return c.execute("UPDATE shared_inbox SET status = ? WHERE id = ?", (status, iid)).rowcount > 0
