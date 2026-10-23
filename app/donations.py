"""Sadaka ve bağışlar: sadaka, fitre, fidye, kurban, adak, kefaret, vakıf/dernek bağışı.

Kayıt elle girilebilir (nakit verildiyse gider olarak da yazılır) ya da hesap dökümü / kart ekstresindeki bir
hareket işaretlenir. "Bağış & Sadaka" kategorisindeki işaretlenmemiş hareketler "bu sadaka mı, zekât mı?" diye
sorulur. Zekât olarak işaretlenen hareket o kişinin zekât yılına ödeme olarak yazılır (zakat_payments).
"""
from datetime import date

from app import db

KINDS = {"sadaka": "Sadaka", "fitre": "Fitre", "fidye": "Fidye", "kurban": "Kurban", "adak": "Adak",
         "kefaret": "Kefaret", "bagis": "Vakıf / dernek bağışı"}
CATEGORY = "Bağış & Sadaka"

SCHEMA = """
CREATE TABLE IF NOT EXISTS donations (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    given_date DATE NOT NULL,
    recipient TEXT,                -- kişi/kurum (opsiyonel)
    note TEXT,
    entry_id INTEGER,              -- hesap/elle kaydı (varsa)
    txn_id INTEGER,                -- kart işlemi (varsa)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _init(c) -> None:
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(zakat_payments)")}
    if cols and "entry_id" not in cols:  # zekât ödemesi bir hareketten geldiyse
        c.execute("ALTER TABLE zakat_payments ADD COLUMN entry_id INTEGER")
        c.execute("ALTER TABLE zakat_payments ADD COLUMN txn_id INTEGER")


def _source(source: str, sid: int) -> dict:
    with db.connect() as c:
        if source == "entry":
            r = c.execute("SELECT id, entry_date AS date, description, amount, kind FROM entries WHERE id = ?", (sid,)).fetchone()
        else:
            r = c.execute("SELECT id, txn_date AS date, description, amount, txn_type AS kind FROM transactions WHERE id = ?",
                          (sid,)).fetchone()
    if not r:
        raise LookupError("Hareket yok")
    return dict(r)


def _set_category(source: str, sid: int) -> None:
    if source == "entry":
        db.update_entry(sid, {"category": CATEGORY})
    else:
        db.update_card_transaction(sid, {"category": CATEGORY})


def add(kind: str, amount: float, given_date: str, recipient: str | None = None, note: str | None = None,
        cash_account_id: int | None = None, as_expense: bool = False) -> int:
    """Elle kayıt. as_expense: nakit/hesaptan verildi ve dökümde görünmeyecek → gider olarak da yaz."""
    if kind not in KINDS:
        raise ValueError("Bilinmeyen tür")
    entry_id = None
    if as_expense:
        entry_id = db.create_entry({"entry_date": given_date, "description": KINDS[kind], "amount": amount, "kind": "gider",
                                    "category": CATEGORY, "payment": "nakit", "account_id": cash_account_id,
                                    "note": (recipient or None)})
    with db.connect() as c:
        _init(c)
        return c.execute("INSERT INTO donations (kind, amount, given_date, recipient, note, entry_id) VALUES (?, ?, ?, ?, ?, ?)",
                         (kind, amount, given_date, (recipient or "").strip() or None, (note or "").strip() or None,
                          entry_id)).lastrowid


def mark(source: str, sid: int, kind: str, person_id: int | None = None, year_start: str | None = None,
         recipient: str | None = None) -> dict:
    """Bir hareketi sadaka/bağış ya da zekât olarak işaretle. kind == 'zekat' ise zekât ödemesine yazılır."""
    if source not in ("entry", "card"):
        raise ValueError("Kaynak entry ya da card olmalı")
    t = _source(source, sid)
    col = "entry_id" if source == "entry" else "txn_id"
    unmark(source, sid)
    if kind == "zekat":
        from app import zakat

        with db.connect() as c:
            zakat._init(c)
            _init(c)
            p = c.execute("SELECT id, hawl_start FROM zakat_people WHERE id = ?" if person_id else
                          "SELECT id, hawl_start FROM zakat_people WHERE is_self = 1", ((person_id,) if person_id else ())).fetchone()
            if not p:
                raise LookupError("Kişi yok")
            ys = year_start or p["hawl_start"]
            if not ys:
                raise ValueError("Önce o kişinin zekât yılı başlangıcını gir")
            c.execute(f"INSERT INTO zakat_payments (person_id, year_start, pay_date, amount, note, {col}) VALUES (?, ?, ?, ?, ?, ?)",
                      (p["id"], ys, t["date"], abs(t["amount"]), t["description"][:80], sid))
    else:
        if kind not in KINDS:
            raise ValueError("Bilinmeyen tür")
        with db.connect() as c:
            _init(c)
            c.execute(f"INSERT INTO donations (kind, amount, given_date, recipient, note, {col}) VALUES (?, ?, ?, ?, ?, ?)",
                      (kind, abs(t["amount"]), t["date"], (recipient or "").strip() or None, t["description"][:80], sid))
    _set_category(source, sid)
    return {"ok": True}


def unmark(source: str, sid: int) -> None:
    col = "entry_id" if source == "entry" else "txn_id"
    from app import zakat

    with db.connect() as c:
        zakat._init(c)
        _init(c)
        c.execute(f"DELETE FROM donations WHERE {col} = ?", (sid,))
        c.execute(f"DELETE FROM zakat_payments WHERE {col} = ?", (sid,))


def delete(did: int) -> bool:
    with db.connect() as c:
        _init(c)
        return c.execute("DELETE FROM donations WHERE id = ?", (did,)).rowcount > 0


def mark_of(source: str, sid: int) -> dict | None:
    """Bu hareket işaretli mi? (formda göstermek için)"""
    col = "entry_id" if source == "entry" else "txn_id"
    from app import zakat

    with db.connect() as c:
        zakat._init(c)
        _init(c)
        if r := c.execute(f"SELECT kind FROM donations WHERE {col} = ?", (sid,)).fetchone():
            return {"kind": r["kind"], "label": KINDS.get(r["kind"], r["kind"])}
        if c.execute(f"SELECT 1 FROM zakat_payments WHERE {col} = ?", (sid,)).fetchone():
            return {"kind": "zekat", "label": "Zekât"}
    return None


def summary(today: date | None = None) -> dict:
    today = today or date.today()
    from app import zakat

    with db.connect() as c:
        zakat._init(c)
        _init(c)
        items = db.rows(c.execute("SELECT * FROM donations ORDER BY given_date DESC, id DESC"))
        zp = db.rows(c.execute("SELECT zp.*, p.name AS person FROM zakat_payments zp JOIN zakat_people p ON p.id = zp.person_id "
                               "ORDER BY pay_date DESC"))
        marked_e = {r[0] for r in c.execute("SELECT entry_id FROM donations WHERE entry_id IS NOT NULL UNION "
                                            "SELECT entry_id FROM zakat_payments WHERE entry_id IS NOT NULL")}
        marked_t = {r[0] for r in c.execute("SELECT txn_id FROM donations WHERE txn_id IS NOT NULL UNION "
                                            "SELECT txn_id FROM zakat_payments WHERE txn_id IS NOT NULL")}
        # "Bağış & Sadaka" kategorisinde olup henüz işaretlenmemiş hareketler: sadaka mı zekât mı?
        pending = [{**r, "source": "entry"} for r in db.rows(c.execute(
            "SELECT id, entry_date AS date, description, amount FROM entries WHERE kind = 'gider' AND skipped = 0 AND category = ? "
            "ORDER BY entry_date DESC", (CATEGORY,))) if r["id"] not in marked_e]
        pending += [{**r, "source": "card"} for r in db.rows(c.execute(
            "SELECT id, txn_date AS date, description, amount FROM transactions WHERE txn_type = 'harcama' AND is_deleted = 0 "
            "AND category = ? ORDER BY txn_date DESC", (CATEGORY,))) if r["id"] not in marked_t]
    for i in items:
        i["label"] = KINDS.get(i["kind"], i["kind"])
    years: dict[str, dict] = {}
    for i in items:
        y = years.setdefault(i["given_date"][:4], {"year": i["given_date"][:4], "sadaka": 0.0, "zekat": 0.0})
        y["sadaka"] += i["amount"]
    for z in zp:
        y = years.setdefault(z["pay_date"][:4], {"year": z["pay_date"][:4], "sadaka": 0.0, "zekat": 0.0})
        y["zekat"] += z["amount"]
    month = today.strftime("%Y-%m")
    return {"items": items, "zakat_payments": zp, "pending": sorted(pending, key=lambda r: r["date"], reverse=True),
            "kinds": KINDS, "years": sorted(({**y, "sadaka": round(y["sadaka"], 2), "zekat": round(y["zekat"], 2)}
                                             for y in years.values()), key=lambda y: y["year"], reverse=True),
            "this_month": round(sum(i["amount"] for i in items if i["given_date"][:7] == month), 2),
            "this_year": round(sum(i["amount"] for i in items if i["given_date"][:4] == month[:4]), 2)}
