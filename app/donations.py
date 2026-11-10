"""Sadaka ve bağışlar: sadaka, fitre, fidye, kurban, adak, kefaret, vakıf/dernek bağışı.

Kayıt elle girilebilir ya da hesap dökümü / kart ekstresindeki bir hareket işaretlenir. "Bağış & Sadaka"
kategorisindeki işaretlenmemiş hareketler "bu sadaka mı, zekât mı?" diye sorulur. Zekât olarak işaretlenen hareket o
kişinin zekât yılına ödeme olarak yazılır (zakat_payments).

Elle girerken nasıl ödendiği seçilirse (nakit, havale, kredi kartı, altın bozdurup, altın vererek) gider olarak da yazılır (own_entry = 1; zekât
ödemesinde de aynı: app/zakat.add_payment). Kredi kartıyla verilen hemen "ekstre bekleniyor" elle gider olur (Özet'te o
gün sayılır); ekstre gelince kart satırıyla eşleşir (db.auto_link), bir kez sayılır ve eşleşen ekstre satırı yeniden
"sadaka mı zekât mı?" diye sorulmaz. Ödeme silinince/düzeltilince açtığı gider kaydı da.
"""
import sqlite3
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
    own_entry INTEGER NOT NULL DEFAULT 0,  -- entry_id'deki gider kaydını bu bağış açtı (silinince o da silinir)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
# altin_bozdurma: altın satılıp parası verildi; altin: doğrudan altın verildi. İkisinde de hesaptan para çıkmaz (hesap
# bakiyesinden düşmez, döküm/ekstreyle eşleşmez) ama gider yazılır (TL karşılığı); kayıtta ödeme yolu altın görünür.
GOLD_PAYMENTS = ("altin_bozdurma", "altin")
# banka: banka hesabından (banka kartı); gider kaydında "nakit / banka kartı" + (seçildiyse) banka hesabı. Hesap seçilmese
# de cüzdana yazılmaz (nakit'ten farkı). Eski istemci banka için nakit + hesap gönderir (aynı sonuç).
PAYMENTS = ("banka", "nakit", "havale", "kredi_karti", *GOLD_PAYMENTS)
PAYMENT_LABELS = {"banka": "Banka hesabından", "nakit": "Nakit / banka kartı", "havale": "Havale / EFT",
                  "kredi_karti": "Kredi kartı", "altin_bozdurma": "Altın bozdurup", "altin": "Altın vererek"}
PAYMENT_ERROR = "Ödeme şekli banka, nakit, havale, kredi kartı ya da altın olmalı"



def _add_col(c, table: str, col: str, ddl: str) -> None:
    try:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    except sqlite3.OperationalError as e:  # aynı defteri iki istek aynı anda açtı: öteki ekledi
        if "duplicate column name" not in str(e):
            raise


def _init(c) -> None:
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(zakat_payments)")}
    if cols and "entry_id" not in cols:  # zekât ödemesi bir hareketten geldiyse
        _add_col(c, "zakat_payments", "entry_id", "INTEGER")
        _add_col(c, "zakat_payments", "txn_id", "INTEGER")
    if cols and "own_entry" not in cols:  # gider kaydını ödeme açtı
        _add_col(c, "zakat_payments", "own_entry", "INTEGER NOT NULL DEFAULT 0")
    if cols and "no_expense" not in cols:  # "gider olarak görünmüyor" sorusuna "gerek yok" dendi
        _add_col(c, "zakat_payments", "no_expense", "INTEGER NOT NULL DEFAULT 0")
    if "own_entry" not in {r[1] for r in c.execute("PRAGMA table_info(donations)")}:
        _add_col(c, "donations", "own_entry", "INTEGER NOT NULL DEFAULT 0")


def create_expense(label: str, amount: float, day: str, payment: str, account_id: int | None = None,
                   card_last4: str | None = None, note: str | None = None, recipient: str | None = None) -> int:
    """Bağış/zekât ödemesini gider olarak yaz (Bağış & Sadaka). Kredi kartıyla: ekstre gelene kadar "ekstre bekleniyor"
    elle gider (Özet'te hemen sayılır, Kartlar'da açık döneme girer); ekstredeki satırla eşleşince kart satırı sayılır,
    elle kayıt sayılmaz (tek sayım). Havale/nakit: döküm önceden yüklendiyse aynı hareketle birleşir (app/sync.py)."""
    if payment not in PAYMENTS:
        raise ValueError(PAYMENT_ERROR)
    card = payment == "kredi_karti"
    gold = payment in GOLD_PAYMENTS  # hesaptan para çıkmadı: hesap yok, döküm/ekstreyle eşleşmez (app/sync.py)
    data = {"entry_date": day, "description": label, "amount": amount, "kind": "gider", "category": CATEGORY,
            "payment": "nakit" if payment == "banka" else payment, "account_id": None if card or gold else account_id,
            "card_last4": card_last4 if card else None,
            "note": note or (PAYMENT_LABELS[payment] if gold else None)}
    if recipient and payment == "havale":  # kime gönderildi: dökümdeki havaleyle eşleşmeye yardım eder
        data["counterparty"] = db.known_counterparty(recipient.strip())
    if payment == "nakit" and not account_id:  # tek nakit hesabı (cüzdan) varsa ondan düşer (POST /api/entries gibi)
        cash = [a for a in db.list_accounts() if a["kind"] == "nakit"]
        if len(cash) == 1:
            data["account_id"] = cash[0]["id"]
    if (u := db.current_user()):
        data["created_by"] = u["id"]  # ortak defterde kimin eklediği
    eid = db.create_entry(data)
    db.auto_link()  # ekstre önceden yüklendiyse hemen eşleşsin
    from app import sync

    sync.reconcile()  # döküm önceden yüklendiyse aynı hareketle birleşsin
    return eid


def drop_own_entry(entry_id: int | None) -> None:
    """Ödemenin açtığı gider kaydı da silinir (ekstreyle eşleşmişse ekstre satırı kalır: para yine çıktı)."""
    if entry_id:
        db.delete_entry(entry_id)


def sync_own_entry(entry_id: int | None, amount: float | None = None, day: str | None = None) -> None:
    """Ödeme düzeltildi: açtığı gider kaydı da düzelir (ekstre/dökümle eşleşmişse gerçek tutar oradadır, dokunulmaz)."""
    if not entry_id:
        return
    with db.connect() as c:
        e = c.execute("SELECT linked_txn_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
        matched = c.execute("SELECT 1 FROM entry_matches WHERE entry_id = ?", (entry_id,)).fetchone()
    data = {k: v for k, v in (("amount", amount), ("entry_date", day)) if v is not None}
    if e and not e[0] and not matched and data:
        db.update_entry(entry_id, data)


def _linked_entry(c, txn_id: int) -> int | None:
    """Ekstre satırına bağlanmış (kartla ödenip elle girilmiş, eşleşmiş) kayıt."""
    r = c.execute("SELECT id FROM entries WHERE linked_txn_id = ?", (txn_id,)).fetchone()
    return r[0] if r else None


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
        cash_account_id: int | None = None, as_expense: bool = False, payment: str | None = None,
        card_last4: str | None = None) -> int:
    """Elle kayıt. payment (nakit | havale | kredi_karti) verilirse gider olarak da yazılır; as_expense (eski istemci)
    = nakit. Hiçbiri yoksa yalnız bağış kaydı (döküm/ekstre gelince o hareket işaretlenir)."""
    if kind not in KINDS:
        raise ValueError("Bilinmeyen tür")
    payment = payment or ("nakit" if as_expense else None)
    if payment and payment not in PAYMENTS:
        raise ValueError(PAYMENT_ERROR)
    with db.connect() as c:
        _init(c)
    who = (recipient or "").strip() or None
    entry_id = create_expense(KINDS[kind], amount, given_date, payment, cash_account_id, card_last4, who,
                              recipient=who) if payment else None
    with db.connect() as c:
        return c.execute("INSERT INTO donations (kind, amount, given_date, recipient, note, entry_id, own_entry) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (kind, amount, given_date, (recipient or "").strip() or None, (note or "").strip() or None,
                          entry_id, int(bool(entry_id)))).lastrowid


def mark(source: str, sid: int, kind: str, person_id: int | None = None, year_start: str | None = None,
         recipient: str | None = None) -> dict:
    """Bir hareketi sadaka/bağış ya da zekât olarak işaretle. kind == 'zekat' ise zekât ödemesine yazılır."""
    if source not in ("entry", "card"):
        raise ValueError("Kaynak entry ya da card olmalı")
    if source == "card":  # kartla ödenip elle girilmiş, ekstreyle eşleşmiş satır: işaret elle kayıtta (tek ödeme)
        with db.connect() as c:
            eid = _linked_entry(c, sid)
        if eid:
            unmark("card", sid, refresh=False)
            source, sid = "entry", eid
    t = _source(source, sid)
    col = "entry_id" if source == "entry" else "txn_id"
    was_zakat = (mark_of(source, sid) or {}).get("kind") == "zekat"
    unmark(source, sid, refresh=False)
    if kind == "zekat":
        from app import zakat

        zakat._roll_years(date.today())
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
    if kind == "zekat" or was_zakat:
        zakat_refresh()
    return {"ok": True}


def zakat_refresh() -> None:
    """Zekât ödemesi değişti: Net durum'daki ödenmemiş zekât yeniden hesaplanır (app/zakat.refresh_self_due)."""
    from app import zakat

    zakat.refresh_self_due()


def unmark(source: str, sid: int, refresh: bool = True) -> None:
    col = "entry_id" if source == "entry" else "txn_id"
    from app import zakat

    with db.connect() as c:
        zakat._init(c)
        _init(c)
        c.execute(f"DELETE FROM donations WHERE {col} = ?", (sid,))
        gone = c.execute(f"DELETE FROM zakat_payments WHERE {col} = ?", (sid,)).rowcount
    if refresh and gone:
        zakat_refresh()


def delete(did: int) -> bool:
    with db.connect() as c:
        _init(c)
        r = c.execute("SELECT entry_id, own_entry FROM donations WHERE id = ?", (did,)).fetchone()
        if not r:
            return False
        c.execute("DELETE FROM donations WHERE id = ?", (did,))
    if r["own_entry"]:
        drop_own_entry(r["entry_id"])
    return True


def mark_of(source: str, sid: int) -> dict | None:
    """Bu hareket işaretli mi? (formda göstermek için)"""
    col = "entry_id" if source == "entry" else "txn_id"
    from app import zakat

    with db.connect() as c:
        zakat._init(c)
        _init(c)
        if source == "card" and (eid := _linked_entry(c, sid)):  # eşleşmiş elle kaydın işareti
            col, sid = "entry_id", eid
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
        # elle girilip ekstreyle eşleşmiş kart satırı: soru (ve işaret) elle kayıtta; ekstre satırı ayrıca sorulmaz
        linked_t = {r[0] for r in c.execute("SELECT linked_txn_id FROM entries WHERE linked_txn_id IS NOT NULL")}
        # "Bağış & Sadaka" kategorisinde olup henüz işaretlenmemiş hareketler: sadaka mı zekât mı?
        pending = [{**r, "source": "entry"} for r in db.rows(c.execute(
            "SELECT id, entry_date AS date, description, amount FROM entries WHERE kind = 'gider' AND skipped = 0 AND category = ? "
            "ORDER BY entry_date DESC", (CATEGORY,))) if r["id"] not in marked_e]
        pending += [{**r, "source": "card"} for r in db.rows(c.execute(
            "SELECT id, txn_date AS date, description, amount FROM transactions WHERE txn_type = 'harcama' AND is_deleted = 0 "
            "AND category = ? ORDER BY txn_date DESC", (CATEGORY,))) if r["id"] not in marked_t | linked_t]
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
