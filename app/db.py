"""SQLite katmanı. Tek dosya: data/ekstre.db"""
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict

from app.categorize import DEFAULT_RULES, RULES_VERSION, categorize
from app.parsers.base import Statement

DB_PATH = os.environ.get("EKSTRE_DB", os.path.join(os.path.dirname(__file__), "..", "data", "ekstre.db"))
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "uploads")
BILL_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "bills")

SCHEMA = """
CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY,
    file_hash TEXT UNIQUE NOT NULL,
    filename TEXT,
    bank TEXT NOT NULL,
    card_no TEXT NOT NULL,
    period_end DATE NOT NULL,
    due_date DATE,
    total_debt REAL,
    min_payment REAL,
    credit_limit REAL,
    previous_balance REAL,
    period_activity REAL,
    interest_fees REAL,
    payments REAL,
    next_period_end DATE,
    next_due_date DATE,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (bank, card_no, period_end)
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    statement_id INTEGER NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
    txn_date DATE NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    txn_type TEXT NOT NULL,
    card TEXT,
    card_holder TEXT,
    orig_amount REAL,
    orig_currency TEXT,
    installment_no INTEGER,
    installments_left INTEGER,
    installment_left_amount REAL,
    points INTEGER,
    category TEXT,
    category_manual INTEGER DEFAULT 0,
    note TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_txn_stmt ON transactions(statement_id);
-- Faturalar (elektrik, internet, telefon...). Ödenince entries'e gider olarak düşer.
CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,          -- kurum: Enerjisa, Türk Telekom...
    description TEXT,                -- dönem / abone no
    amount REAL NOT NULL CHECK (amount >= 0),
    due_date DATE NOT NULL,
    category TEXT,
    note TEXT,
    file_name TEXT,                  -- data/bills/ altındaki dosya
    paid_at DATE,
    paid_amount REAL,
    entry_id INTEGER REFERENCES entries(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Abonelik durumu (işyeri adına göre; merchant_name() çıktısı)
CREATE TABLE IF NOT EXISTS subscription_state (
    merchant TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('aktif', 'iptal', 'dusuruldu')),
    changed_at DATE NOT NULL,
    new_amount REAL,               -- paket düşürüldüyse yeni aylık tutar
    note TEXT
);
-- Kısma hedefleri: bir kategori ya da işyeri için aylık hedef
CREATE TABLE IF NOT EXISTS savings_goals (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('category', 'merchant')),
    name TEXT NOT NULL,
    baseline REAL NOT NULL,        -- hedef konduğundaki aylık harcama
    target REAL NOT NULL CHECK (target >= 0),
    start_month TEXT NOT NULL,     -- YYYY-MM
    note TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Sabit kalemin belirli bir ay için planlanan tutarı (ör. brüt maaşta her ay değişen net)
CREATE TABLE IF NOT EXISTS recurring_overrides (
    recurring_id INTEGER NOT NULL REFERENCES recurring(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (recurring_id, month)
);
-- İşyeri takma adları: ekstredeki resmi unvan → marka (ör. NEW TASTE GIDA → ESPRESSOLAB). Fişten öğrenilir.
CREATE TABLE IF NOT EXISTS merchant_aliases (
    source TEXT PRIMARY KEY,   -- merchant_name(resmi unvan)
    display TEXT NOT NULL      -- merchant_name(marka)
);
-- Kategori bazında aylık harcama limiti
CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL CHECK (monthly_limit > 0)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL,
    category TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100
);
-- Elle girilen gelir/giderler (maaş, kira, psikolog...). Gelecek tarihli olanlar planlamaya girer.
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    entry_date DATE NOT NULL,
    month TEXT NOT NULL,            -- YYYY-MM
    description TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    kind TEXT NOT NULL CHECK (kind IN ('gelir', 'gider')),
    category TEXT,
    note TEXT,
    recurring_id INTEGER REFERENCES recurring(id) ON DELETE SET NULL,
    skipped INTEGER NOT NULL DEFAULT 0,  -- tekrarlayan kalem bu ay olmadı
    payment TEXT,                        -- kredi_karti | nakit | havale
    import_hash TEXT,                    -- banka hesap hareketinden geldiyse parmak izi (tekrarı önler)
    linked_txn_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,  -- ekstredeki karşılığı
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_entries_month ON entries(month);
-- Her ay tekrarlayan kalemler (maaş, kira, aidat...)
CREATE TABLE IF NOT EXISTS recurring (
    id INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    kind TEXT NOT NULL CHECK (kind IN ('gelir', 'gider')),
    category TEXT,
    day_of_month INTEGER NOT NULL DEFAULT 1,
    start_month TEXT NOT NULL,      -- YYYY-MM
    end_month TEXT,                 -- YYYY-MM, NULL = süresiz
    note TEXT,
    gross_amount REAL               -- brüt maaş: net her ay vergi dilimine göre hesaplanır
);
"""

# Eski şemaya sonradan eklenen kolonlar: (tablo, kolon, tanım)
MIGRATIONS = [
    ("statements", "next_period_end", "DATE"),
    ("statements", "next_due_date", "DATE"),
    ("transactions", "is_deleted", "INTEGER NOT NULL DEFAULT 0"),
    ("recurring", "gross_amount", "REAL"),
    ("statements", "paid_amount", "REAL"),
    ("statements", "paid_at", "DATE"),
    ("entries", "payment", "TEXT"),
    ("entries", "import_hash", "TEXT"),
    ("entries", "linked_txn_id", "INTEGER REFERENCES transactions(id) ON DELETE SET NULL"),
]


@contextmanager
def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        for table, col, ddl in MIGRATIONS:
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        _migrate_subscription_state(c)
        _backfill_statements(c)
        _sync_default_rules(c)
    auto_mark_paid()
    auto_update_cycle()


def _migrate_subscription_state(c) -> None:
    """Eski şema 'dusuruldu' durumuna izin vermiyordu (CHECK değişmez): tabloyu veriyle birlikte yeniden kur."""
    sql = c.execute("SELECT sql FROM sqlite_master WHERE name = 'subscription_state'").fetchone()[0]
    if "dusuruldu" in sql:
        return
    c.executescript("""
        ALTER TABLE subscription_state RENAME TO subscription_state_old;
        CREATE TABLE subscription_state (
            merchant TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('aktif', 'iptal', 'dusuruldu')),
            changed_at DATE NOT NULL,
            new_amount REAL,
            note TEXT
        );
        INSERT INTO subscription_state (merchant, status, changed_at, note)
            SELECT merchant, status, changed_at, note FROM subscription_state_old;
        DROP TABLE subscription_state_old;
    """)


def _sync_default_rules(c) -> None:
    """Varsayılan kuralları yükle/güncelle. Varsayılanlar priority >= 1000; kullanıcı kuralları (<1000) dokunulmaz."""
    row = c.execute("SELECT value FROM settings WHERE key = 'rules_version'").fetchone()
    has_rules = c.execute("SELECT 1 FROM rules LIMIT 1").fetchone() is not None
    current = int(row[0]) if row else (1 if has_rules else 0)
    if current >= RULES_VERSION:
        return
    # Yeni sürümde kaldırılan/değişen varsayılan kurallar kullanıcı kuralı olarak korunur:
    # mevcut kurulumlarda kategoriler sessizce değişmesin
    new = set(DEFAULT_RULES)
    for r in c.execute("SELECT pattern, category FROM rules WHERE priority >= 1000").fetchall():
        if (r["pattern"], r["category"]) not in new:
            c.execute("INSERT INTO rules (pattern, category, priority) VALUES (?, ?, 500)", (r["pattern"], r["category"]))
    c.execute("DELETE FROM rules WHERE priority >= 1000")
    c.executemany("INSERT INTO rules (pattern, category, priority) VALUES (?, ?, ?)",
                  [(p, cat, 1000 + i) for i, (p, cat) in enumerate(DEFAULT_RULES)])
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('rules_version', ?)", (str(RULES_VERSION),))
    if current:  # ilk kurulum değilse mevcut işlemleri yeni kurallarla tazele (elle verilenler hariç)
        rules = load_rules(c)
        rs = c.execute("SELECT id, description, txn_type FROM transactions WHERE category_manual = 0").fetchall()
        c.executemany("UPDATE transactions SET category = ? WHERE id = ?",
                      [(categorize(r["description"], r["txn_type"], rules), r["id"]) for r in rs])


def _backfill_statements(c) -> None:
    """Sonradan eklenen ekstre alanlarını saklanan PDF'ten yeniden okuyup doldur."""
    from app.parsers import parse_statement

    for r in c.execute("SELECT id, file_hash FROM statements WHERE next_due_date IS NULL").fetchall():
        path = os.path.join(UPLOAD_DIR, f"{r['file_hash']}.pdf")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                st = parse_statement(f.read())
        except Exception:  # noqa: BLE001 - backfill best effort
            continue
        c.execute("UPDATE statements SET next_period_end = ?, next_due_date = ? WHERE id = ?",
                  (_iso(st.next_period_end), _iso(st.next_due_date), r["id"]))


def get_setting(key: str) -> str | None:
    with connect() as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None


def set_setting(key: str, value: str | None) -> None:
    with connect() as c:
        if value is None:
            c.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- statements

def statement_exists(data: bytes, stmt: Statement) -> bool:
    with connect() as c:
        return c.execute(
            "SELECT 1 FROM statements WHERE file_hash = ? OR (bank = ? AND card_no = ? AND period_end = ?)",
            (file_hash(data), stmt.bank, stmt.card_no, stmt.period_end.isoformat()),
        ).fetchone() is not None


def _iso(d):
    return d.isoformat() if d else None


def save_statement(data: bytes, filename: str, stmt: Statement) -> int:
    with connect() as c:
        rules = load_rules(c)
        cur = c.execute(
            """INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt,
               min_payment, credit_limit, previous_balance, period_activity, interest_fees, payments,
               next_period_end, next_due_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (file_hash(data), filename, stmt.bank, stmt.card_no, stmt.period_end.isoformat(),
             _iso(stmt.due_date), stmt.total_debt, stmt.min_payment, stmt.credit_limit, stmt.previous_balance,
             stmt.period_activity, stmt.interest_fees, stmt.payments,
             _iso(stmt.next_period_end), _iso(stmt.next_due_date)),
        )
        sid = cur.lastrowid
        for t in stmt.transactions:
            row = asdict(t)
            row["txn_date"] = t.txn_date.isoformat()
            row["category"] = categorize(t.description, t.txn_type, rules)
            row["statement_id"] = sid
            cols = ", ".join(row)
            c.execute(f"INSERT INTO transactions ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(os.path.join(UPLOAD_DIR, f"{file_hash(data)}.pdf"), "wb") as out:
        out.write(data)
    return sid


# ---------------------------------------------------------------- elle girilen ↔ ekstre eşleştirme

MATCH_DAYS = 3        # otomatik eşleşmede tarih toleransı
CANDIDATE_DAYS = 15   # elle eşleştirme adaylarında tarih toleransı


def _days(a: str, b: str) -> int:
    from datetime import date

    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)


def _name_score(a: str, b: str) -> float:
    from app.textnorm import merchant_name

    wa, wb = set(merchant_name(a).split()), set(merchant_name(b).split())
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def _free_card_txns(c) -> list[dict]:
    return rows(c.execute(
        """SELECT t.id, t.txn_date, t.description, t.amount, t.category, t.category_manual, t.note, s.card_no AS account
           FROM transactions t JOIN statements s ON s.id = t.statement_id
           WHERE t.txn_type = 'harcama' AND t.is_deleted = 0
             AND t.id NOT IN (SELECT linked_txn_id FROM entries WHERE linked_txn_id IS NOT NULL)"""))


def _apply_link(c, entry: dict, txn: dict) -> None:
    c.execute("UPDATE entries SET linked_txn_id = ? WHERE id = ?", (txn["id"], entry["id"]))
    # Kullanıcının verdiği bilgi ekstre satırına taşınır
    note = " · ".join(x for x in (txn["note"], entry["description"], entry["note"]) if x)
    if entry["category"] and not txn["category_manual"]:
        c.execute("UPDATE transactions SET category = ?, category_manual = 1, note = ? WHERE id = ?",
                  (entry["category"], note, txn["id"]))
    else:
        c.execute("UPDATE transactions SET note = ? WHERE id = ?", (note, txn["id"]))


def auto_link() -> int:
    """Kredi kartıyla ödenmiş, henüz eşleşmemiş elle girilen giderleri ekstre satırlarıyla eşleştir.
    Kural: tutar birebir (±0,05), tarih ±MATCH_DAYS gün; birden fazla aday varsa en yakın tarih + isim benzerliği."""
    linked = 0
    with connect() as c:
        pending = rows(c.execute(
            "SELECT * FROM entries WHERE payment = 'kredi_karti' AND kind = 'gider' AND linked_txn_id IS NULL AND skipped = 0"))
        if not pending:
            return 0
        free = _free_card_txns(c)
        for e in sorted(pending, key=lambda e: e["entry_date"]):
            cands = [t for t in free if abs(t["amount"] - e["amount"]) <= 0.05
                     and _days(t["txn_date"], e["entry_date"]) <= MATCH_DAYS]
            if not cands:
                continue
            best = min(cands, key=lambda t: (_days(t["txn_date"], e["entry_date"]),
                                             -_name_score(t["description"], e["description"])))
            _apply_link(c, e, best)
            free.remove(best)
            linked += 1
    return linked


def link_candidates(entry_id: int) -> list[dict]:
    with connect() as c:
        e = c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not e:
            return []
        out = []
        for t in _free_card_txns(c):
            d = _days(t["txn_date"], e["entry_date"])
            if d <= CANDIDATE_DAYS and abs(t["amount"] - e["amount"]) <= max(1.0, e["amount"] * 0.05):
                out.append({**t, "days": d, "diff": round(t["amount"] - e["amount"], 2)})
        return sorted(out, key=lambda t: (abs(t["diff"]), t["days"]))[:10]


def link_entry(entry_id: int, txn_id: int | None) -> None:
    with connect() as c:
        if txn_id is None:
            c.execute("UPDATE entries SET linked_txn_id = NULL WHERE id = ?", (entry_id,))
            return
        e = dict(c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone())
        t = next((t for t in _free_card_txns(c) if t["id"] == txn_id), None)
        if not t:
            raise ValueError("Kart işlemi bulunamadı veya başka bir kayda bağlı")
        _apply_link(c, e, t)


def list_statements() -> list[dict]:
    with connect() as c:
        return rows(c.execute(
            """SELECT s.*, substr(s.period_end, 1, 7) AS period, COUNT(t.id) AS txn_count
               FROM statements s LEFT JOIN transactions t ON t.statement_id = s.id
               GROUP BY s.id ORDER BY s.period_end DESC"""))


def delete_statement(statement_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM statements WHERE id = ?", (statement_id,))


# ---------------------------------------------------------------- card transactions

def card_transactions(include_deleted: bool = False) -> list[dict]:
    """Tüm kart işlemleri; 'period' = ekstre kesim ayı (YYYY-MM)."""
    with connect() as c:
        return rows(c.execute(
            f"""SELECT t.*, s.bank, s.card_no AS account, s.period_end, substr(s.period_end, 1, 7) AS period
                FROM transactions t JOIN statements s ON s.id = t.statement_id
                {'' if include_deleted else 'WHERE t.is_deleted = 0'}
                ORDER BY t.txn_date DESC, t.id DESC"""))


CARD_EDITABLE = {"category", "txn_type", "note", "is_deleted", "description"}


def update_card_transaction(txn_id: int, fields: dict) -> None:
    fields = {k: v for k, v in fields.items() if k in CARD_EDITABLE}
    if not fields:
        return
    if "category" in fields:
        fields["category_manual"] = 1
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE transactions SET {sets} WHERE id = ?", (*fields.values(), txn_id))


# ---------------------------------------------------------------- manual entries

ENTRY_FIELDS = ("entry_date", "description", "amount", "kind", "category", "note", "recurring_id", "skipped",
                "payment", "linked_txn_id", "month", "import_hash")


def list_entries(month: str | None = None) -> list[dict]:
    with connect() as c:
        if month:
            return rows(c.execute("SELECT * FROM entries WHERE month = ? ORDER BY entry_date DESC, id DESC", (month,)))
        return rows(c.execute("SELECT * FROM entries ORDER BY entry_date DESC, id DESC"))


def cycle_start_day() -> int:
    """Ay dönümü günü: 1 = takvim ayı. Ör. 17 → "Kasım dönemi" 17 Ekim – 16 Kasım (kart kesimlerine hizalı)."""
    v = get_setting("cycle_start_day")
    return int(v) if v and v.isdigit() and 1 <= int(v) <= 28 else 1


def period_of(d, start_day: int | None = None) -> str:
    """Bir tarihin ait olduğu dönem (YYYY-MM). Ay dönümü gününden itibaren bir sonraki ayın dönemi başlar."""
    from datetime import date as _date

    d = _date.fromisoformat(str(d)[:10])
    start = start_day or cycle_start_day()
    y, m = d.year, d.month
    if start > 1 and d.day >= start:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}"


def occurrence_date(month: str, day: int, start_day: int | None = None) -> str:
    """Bir sabit kalemin (ayın `day`'inde) `month` dönemine denk gelen tarihi.
    Dönem = [önceki ayın ay dönümü günü, bu ayın ay dönümü günü - 1]. Ör. ay dönümü 3, maaş 20 →
    Kasım dönemi (3 Eki – 2 Kas) maaşı 20 Ekim'dir."""
    start = start_day or cycle_start_day()
    y, m = map(int, month.split("-"))
    if start > 1 and day >= start:
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y:04d}-{m:02d}-{min(day, 28):02d}"


def _months_around(period: str) -> list[str]:
    y, m = map(int, period.split("-"))
    out = []
    for d in (-1, 0, 1):
        t = y * 12 + (m - 1) + d
        out.append(f"{t // 12:04d}-{t % 12 + 1:02d}")
    return out


def _attach_recurring(c, data: dict, exclude_id: int | None = None) -> None:
    """Elle girilen kayıt bir sabit kalemle aynı adı taşıyorsa (ör. "Maaş"), tarihine en yakın dönemin
    o sabit kalemine bağla: maaş bir gün erken/geç yatsa da her dönemde tam bir kez sayılır."""
    from datetime import date as _date

    if data.get("recurring_id") or data.get("skipped"):
        return
    name = (data.get("description") or "").strip().casefold()
    rec = next((r for r in c.execute("SELECT * FROM recurring").fetchall()
                if r["kind"] == data.get("kind") and r["description"].strip().casefold() == name), None)
    if not rec:
        return
    d = _date.fromisoformat(str(data["entry_date"])[:10])
    taken = {r["month"] for r in c.execute(
        "SELECT month FROM entries WHERE recurring_id = ? AND id IS NOT ?", (rec["id"], exclude_id))}
    cands = [m for m in _months_around(period_of(d)) if m not in taken]
    if not cands:
        return  # komşu dönemlerin hepsi dolu: bağlama, çift kayıt uyarısı görünsün
    best = min(cands, key=lambda m: abs((_date.fromisoformat(occurrence_date(m, rec["day_of_month"])) - d).days))
    data["recurring_id"], data["month"] = rec["id"], best


def _nearest_recurring_month(rec, d) -> str:
    """Tarihe en yakın sabit kalem döneminin ayı (dolu olup olmamasına bakmadan)."""
    from datetime import date as _date

    return min(_months_around(period_of(d)),
               key=lambda m: abs((_date.fromisoformat(occurrence_date(m, rec["day_of_month"])) - d).days))


def set_cycle_start_day(day: int) -> int:
    """Ay dönümünü değiştir ve elle girilen kayıtların dönemlerini yeniden hesapla."""
    day = max(1, min(28, int(day)))
    set_setting("cycle_start_day", str(day))
    with connect() as c:
        rs = c.execute("SELECT id, entry_date, recurring_id, month FROM entries").fetchall()
        # sabit kalem onayları hangi aya onaylandıysa orada kalır; diğerleri tarihine göre
        c.executemany("UPDATE entries SET month = ? WHERE id = ?",
                      [(period_of(r["entry_date"], day), r["id"]) for r in rs if not r["recurring_id"]])
    return day


def suggested_cycle_day() -> int | None:
    """Her kartın SON ekstresindeki kesim günlerinin en geçinden bir gün sonrası: her ekstre kendi dönemine düşer.
    Banka kesim gününü kaydırırsa ya da yeni kart eklenirse öneri de değişir."""
    with connect() as c:
        days = [int(r[0][8:10]) for r in c.execute(
            "SELECT MAX(period_end) FROM statements GROUP BY card_no")]
    return min(28, max(days) + 1) if days else None


def cycle_mode() -> str:
    """auto: ay dönümü her ekstre yüklemesinde kesim günlerine göre güncellenir; manual: kullanıcı seçti."""
    return get_setting("cycle_mode") or "auto"


def auto_update_cycle() -> int | None:
    """Otomatik moddaysa ay dönümünü kesim günlerine hizala. Değiştiyse yeni günü döner."""
    if cycle_mode() != "auto":
        return None
    sug = suggested_cycle_day()
    if sug and sug != cycle_start_day():
        return set_cycle_start_day(sug)
    return None


def create_entry(data: dict) -> int:
    data = {k: data.get(k) for k in ENTRY_FIELDS if k in data}
    month = data.pop("month", None)
    data["month"] = month or period_of(data["entry_date"])
    with connect() as c:
        _attach_recurring(c, data)
        cols = ", ".join(data)
        return c.execute(f"INSERT INTO entries ({cols}) VALUES ({', '.join('?' * len(data))})",
                         tuple(data.values())).lastrowid


def update_entry(entry_id: int, data: dict) -> None:
    data = {k: v for k, v in data.items() if k in ENTRY_FIELDS}
    data.pop("month", None)
    if not data:
        return
    with connect() as c:
        if "entry_date" in data:
            cur = dict(c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone())
            merged = {**cur, **data, "recurring_id": None}
            merged["month"] = period_of(merged["entry_date"])
            _attach_recurring(c, merged, exclude_id=entry_id)
            data["month"], data["recurring_id"] = merged["month"], merged["recurring_id"]
        c.execute(f"UPDATE entries SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?", (*data.values(), entry_id))


def delete_entry(entry_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM entries WHERE id = ?", (entry_id,))


# ---------------------------------------------------------------- recurring

RECURRING_FIELDS = ("description", "amount", "kind", "category", "day_of_month", "start_month", "end_month", "note",
                    "gross_amount")


def list_recurring() -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT * FROM recurring ORDER BY kind DESC, amount DESC"))


def create_recurring(data: dict) -> int:
    data = {k: data.get(k) for k in RECURRING_FIELDS if k in data}
    with connect() as c:
        return c.execute(f"INSERT INTO recurring ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                         tuple(data.values())).lastrowid


def update_recurring(rid: int, data: dict) -> None:
    data = {k: v for k, v in data.items() if k in RECURRING_FIELDS}
    if not data:
        return
    with connect() as c:
        c.execute(f"UPDATE recurring SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?", (*data.values(), rid))


def delete_recurring(rid: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM recurring WHERE id = ?", (rid,))


# ---------------------------------------------------------------- rules

def load_rules(conn) -> list[tuple[str, str]]:
    # Kullanıcı kuralları (düşük priority) varsayılanlardan önce çalışır
    return [(r["pattern"], r["category"]) for r in conn.execute("SELECT pattern, category FROM rules ORDER BY priority, id")]


def list_rules() -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT id, pattern, category, priority FROM rules ORDER BY priority, id"))


def add_rule(pattern: str, category: str, priority: int = 10) -> int:
    with connect() as c:
        return c.execute("INSERT INTO rules (pattern, category, priority) VALUES (?, ?, ?)",
                         (pattern, category, priority)).lastrowid


def update_rule(rule_id: int, data: dict) -> None:
    data = {k: v for k, v in data.items() if k in ("pattern", "category", "priority")}
    if data:
        with connect() as c:
            c.execute(f"UPDATE rules SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?", (*data.values(), rule_id))


def delete_rule(rule_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def recategorize_all() -> int:
    """Elle atanmamış tüm işlemleri güncel kurallarla yeniden kategorize et."""
    with connect() as c:
        rules = load_rules(c)
        rs = c.execute("SELECT id, description, txn_type FROM transactions WHERE category_manual = 0").fetchall()
        c.executemany("UPDATE transactions SET category = ? WHERE id = ?",
                      [(categorize(r["description"], r["txn_type"], rules), r["id"]) for r in rs])
        return len(rs)


def known_categories() -> list[str]:
    with connect() as c:
        found = {r[0] for r in c.execute(
            "SELECT category FROM transactions UNION SELECT category FROM entries "
            "UNION SELECT category FROM recurring UNION SELECT category FROM rules") if r[0]}
    return sorted(found)


def category_usage() -> dict[str, int]:
    """Kategori → kullanım sayısı (seçicide sık kullanılanlar üstte)."""
    with connect() as c:
        return {r[0]: r[1] for r in c.execute(
            """SELECT category, COUNT(*) FROM (
                 SELECT category FROM transactions WHERE is_deleted = 0 UNION ALL SELECT category FROM entries)
               WHERE category IS NOT NULL GROUP BY category""")}


# ---------------------------------------------------------------- ödemeler: ekstre + fatura

def auto_mark_paid() -> int:
    """Bir ekstredeki ödeme satırları (ÖDEMENİZ İÇİN TEŞEKKÜRLER / Borç Ödeme) bir önceki ekstrenin ödemesidir.
    Elle işaretlenmemiş önceki ekstreleri bu satırlara göre ödendi yapar."""
    marked = 0
    with connect() as c:
        sts = rows(c.execute("SELECT id, card_no, period_end, total_debt, paid_amount FROM statements ORDER BY card_no, period_end"))
        for prev, cur in zip(sts, sts[1:]):
            if prev["card_no"] != cur["card_no"] or prev["paid_amount"] is not None:
                continue
            pays = c.execute("SELECT txn_date, amount FROM transactions WHERE statement_id = ? AND txn_type = 'odeme' "
                             "AND txn_date > ?", (cur["id"], prev["period_end"])).fetchall()
            if pays:
                total = round(-sum(p["amount"] for p in pays), 2)
                c.execute("UPDATE statements SET paid_amount = ?, paid_at = ? WHERE id = ?",
                          (min(total, prev["total_debt"]), max(p["txn_date"] for p in pays), prev["id"]))
                marked += 1
    return marked


def mark_statement_paid(sid: int, amount: float | None, paid_at: str | None) -> None:
    """Kart ekstresi ödendi. Gider eklenmez: kart harcamaları zaten ekstreden sayılıyor."""
    with connect() as c:
        c.execute("UPDATE statements SET paid_amount = ?, paid_at = ? WHERE id = ?", (amount, paid_at, sid))


BILL_FIELDS = ("provider", "description", "amount", "due_date", "category", "note", "file_name")


def list_bills() -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT * FROM bills ORDER BY due_date DESC, id DESC"))


def get_bill(bid: int) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM bills WHERE id = ?", (bid,)).fetchone()
        return dict(r) if r else None


def create_bill(data: dict) -> int:
    data = {k: data.get(k) for k in BILL_FIELDS if k in data}
    with connect() as c:
        return c.execute(f"INSERT INTO bills ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                         tuple(data.values())).lastrowid


def update_bill(bid: int, data: dict) -> None:
    data = {k: v for k, v in data.items() if k in BILL_FIELDS}
    if data:
        with connect() as c:
            c.execute(f"UPDATE bills SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?", (*data.values(), bid))


def delete_bill(bid: int) -> None:
    b = get_bill(bid)
    if not b:
        return
    with connect() as c:
        if b["entry_id"]:
            c.execute("DELETE FROM entries WHERE id = ?", (b["entry_id"],))
        c.execute("DELETE FROM bills WHERE id = ?", (bid,))
    if b["file_name"]:
        try:
            os.remove(os.path.join(BILL_DIR, b["file_name"]))
        except OSError:
            pass


def pay_bill(bid: int, amount: float, paid_at: str, payment: str | None) -> int:
    """Fatura ödendi → gider kaydı oluştur (kredi kartıyla ödendiyse ekstre gelince eşleşir)."""
    b = get_bill(bid)
    if not b:
        raise ValueError("Fatura yok")
    with connect() as c:
        if b["entry_id"]:
            c.execute("DELETE FROM entries WHERE id = ?", (b["entry_id"],))
    eid = create_entry({"entry_date": paid_at, "description": b["provider"], "amount": amount, "kind": "gider",
                        "category": b["category"] or "Faturalar", "payment": payment,
                        "note": " · ".join(x for x in ("Fatura", b["description"]) if x)})
    with connect() as c:
        c.execute("UPDATE bills SET paid_at = ?, paid_amount = ?, entry_id = ? WHERE id = ?", (paid_at, amount, eid, bid))
    auto_link()
    return eid


def unpay_bill(bid: int) -> None:
    b = get_bill(bid)
    if not b:
        return
    with connect() as c:
        if b["entry_id"]:
            c.execute("DELETE FROM entries WHERE id = ?", (b["entry_id"],))
        c.execute("UPDATE bills SET paid_at = NULL, paid_amount = NULL, entry_id = NULL WHERE id = ?", (bid,))


def save_bill_file(data: bytes, ext: str) -> str:
    os.makedirs(BILL_DIR, exist_ok=True)
    name = f"{file_hash(data)[:24]}{ext}"
    with open(os.path.join(BILL_DIR, name), "wb") as f:
        f.write(data)
    return name


# ---------------------------------------------------------------- bütçeler

def list_budgets() -> dict[str, float]:
    with connect() as c:
        return {r[0]: r[1] for r in c.execute("SELECT category, monthly_limit FROM budgets ORDER BY category")}


def set_budget(category: str, limit: float | None) -> None:
    with connect() as c:
        if not limit:
            c.execute("DELETE FROM budgets WHERE category = ?", (category,))
        else:
            c.execute("INSERT OR REPLACE INTO budgets (category, monthly_limit) VALUES (?, ?)", (category, limit))


# ---------------------------------------------------------------- abonelik durumu + kısma hedefleri

def subscription_states() -> dict[str, dict]:
    with connect() as c:
        return {r["merchant"]: dict(r) for r in c.execute("SELECT * FROM subscription_state")}


def set_subscription_state(merchant: str, status: str, changed_at: str, note: str | None = None,
                           new_amount: float | None = None) -> None:
    with connect() as c:
        if status == "aktif":
            c.execute("DELETE FROM subscription_state WHERE merchant = ?", (merchant,))
        else:
            c.execute("INSERT OR REPLACE INTO subscription_state (merchant, status, changed_at, new_amount, note) "
                      "VALUES (?, ?, ?, ?, ?)", (merchant, status, changed_at, new_amount if status == "dusuruldu" else None, note))


GOAL_FIELDS = ("kind", "name", "baseline", "target", "start_month", "note", "active")


def list_goals(active_only: bool = False) -> list[dict]:
    with connect() as c:
        q = "SELECT * FROM savings_goals" + (" WHERE active = 1" if active_only else "") + " ORDER BY active DESC, id DESC"
        return rows(c.execute(q))


def create_goal(data: dict) -> int:
    data = {k: data.get(k) for k in GOAL_FIELDS if k in data}
    with connect() as c:
        return c.execute(f"INSERT INTO savings_goals ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                         tuple(data.values())).lastrowid


def update_goal(gid: int, data: dict) -> None:
    data = {k: v for k, v in data.items() if k in GOAL_FIELDS}
    if data:
        with connect() as c:
            c.execute(f"UPDATE savings_goals SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?", (*data.values(), gid))


def delete_goal(gid: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM savings_goals WHERE id = ?", (gid,))


# ---------------------------------------------------------------- sabit kalem: aya özel tutar

def recurring_overrides() -> dict[tuple[int, str], float]:
    with connect() as c:
        return {(r[0], r[1]): r[2] for r in c.execute("SELECT recurring_id, month, amount FROM recurring_overrides")}


def set_recurring_override(rid: int, month: str, amount: float | None) -> None:
    with connect() as c:
        if amount is None:
            c.execute("DELETE FROM recurring_overrides WHERE recurring_id = ? AND month = ?", (rid, month))
        else:
            c.execute("INSERT OR REPLACE INTO recurring_overrides (recurring_id, month, amount) VALUES (?, ?, ?)",
                      (rid, month, amount))


def planned_amount(r: dict, month: str, overrides: dict | None = None) -> float:
    """Sabit kalemin o ay için beklenen tutarı: aya özel tutar > brüt maaştan hesaplanan net > şablon."""
    ov = overrides if overrides is not None else recurring_overrides()
    if (r["id"], month) in ov:
        return ov[(r["id"], month)]
    if r.get("gross_amount"):
        from app.payroll import net_for

        paid = occurrence_date(month, r["day_of_month"] or 1)  # maaşın o dönemde yattığı gerçek ay
        return net_for(r["gross_amount"], int(paid[:4]), int(paid[5:7]))
    return r["amount"]


# ---------------------------------------------------------------- işyeri takma adları (fişten öğrenilir)

def merchant_aliases() -> dict[str, str]:
    with connect() as c:
        return {r[0]: r[1] for r in c.execute("SELECT source, display FROM merchant_aliases")}


def learn_from_receipt(legal_name: str, brand: str, category: str | None) -> dict:
    """Fişte marka (Espressolab) ile resmi unvan (NEW TASTE GIDA…) farklıysa:
    1) ekstredeki unvanlı satırlar markayla gruplansın diye takma ad kaydet,
    2) unvan için hiçbir kural yoksa fişin kategorisiyle kural oluştur (ekstre gelince aynı kategoriye düşsün)."""
    import re as _re

    from app.textnorm import legal_key, merchant_name

    src, dst = legal_key(legal_name), merchant_name(brand)
    out = {"alias": None, "rule": None}
    if not src or not dst or src == dst:
        return out
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO merchant_aliases (source, display) VALUES (?, ?)", (src, dst))
        out["alias"] = (src, dst)
        if category and categorize(src, "harcama", load_rules(c)) == "Kategorisiz":
            pattern = _re.escape(src)
            c.execute("INSERT INTO rules (pattern, category, priority) VALUES (?, ?, 10)", (pattern, category))
            out["rule"] = pattern
    if out["rule"]:
        recategorize_all()
    return out


# ---------------------------------------------------------------- PDF şifreleri (kullanıcı "hatırla" derse)

def pdf_passwords() -> list[str]:
    import json

    try:
        return json.loads(get_setting("pdf_passwords") or "[]")
    except ValueError:
        return []


def remember_pdf_password(pw: str) -> None:
    import json

    pws = pdf_passwords()
    if pw and pw not in pws:
        set_setting("pdf_passwords", json.dumps([pw, *pws][:10]))


def forget_pdf_password(index: int) -> None:
    import json

    pws = pdf_passwords()
    if 0 <= index < len(pws):
        pws.pop(index)
        set_setting("pdf_passwords", json.dumps(pws))


# ---------------------------------------------------------------- banka hesap hareketleri

def match_card_payment(amount: float, on: str, only_unpaid: bool = False):
    """Hesaptan çıkan tutar bir ekstrenin borcu/asgarisi ile aynı ve tarih ödeme penceresindeyse o ekstre.
    Açıklamada "kart" geçmese de (ör. başka bankanın kartına EFT) kart ödemesini yakalar."""
    from datetime import date as _date, timedelta

    d = _date.fromisoformat(on)
    with connect() as c:
        return c.execute(
            f"""SELECT * FROM statements WHERE due_date IS NOT NULL
                AND (ABS(total_debt - ?) <= 1 OR ABS(COALESCE(min_payment, -1) - ?) <= 1
                     OR ABS(COALESCE(paid_amount, -1) - ?) <= 1)
                AND ? BETWEEN period_end AND date(due_date, '+10 days')
                {'AND paid_amount IS NULL' if only_unpaid else ''}
                ORDER BY due_date LIMIT 1""",
            (amount, amount, amount, (d - timedelta(days=0)).isoformat())).fetchone()


def import_bank_rows(rows: list[dict]) -> dict:
    """Ayrıştırılmış hesap hareketlerini kaydet. Özet sayaçları döner."""
    import re as _re
    from datetime import date as _date, timedelta

    from app.textnorm import normalize

    stats = {"gelir": 0, "gider": 0, "kart_odemesi": 0, "virman": 0, "tekrar": 0, "eslesen_elle": 0,
             "odendi_isaretlenen": 0, "maas": 0}
    with connect() as c:
        rules = load_rules(c)
        seen = {r[0] for r in c.execute("SELECT import_hash FROM entries WHERE import_hash IS NOT NULL")}
        recurring = [dict(r) for r in c.execute("SELECT * FROM recurring")]
    for r in rows:
        if r["hash"] in seen:
            stats["tekrar"] += 1
            continue
        d = _date.fromisoformat(r["date"])
        if r["type"] == "virman":
            stats["virman"] += 1
            continue
        if r["type"] == "gider" and match_card_payment(abs(r["amount"]), r["date"]):
            r["type"] = "kart_odemesi"  # açıklamada "kart" yok ama tutar bir ekstreyle birebir
        if r["type"] == "kart_odemesi":
            stats["kart_odemesi"] += 1
            amt = abs(r["amount"])
            st = match_card_payment(amt, r["date"], only_unpaid=True)
            if st:
                with connect() as c:
                    c.execute("UPDATE statements SET paid_amount = ?, paid_at = ? WHERE id = ?", (amt, r["date"], st["id"]))
                stats["odendi_isaretlenen"] += 1
            seen.add(r["hash"])
            continue
        kind = "gelir" if r["amount"] > 0 else "gider"
        amount = abs(r["amount"])
        desc = r["description"]
        # daha önce elle girilmiş aynı hareket (ör. havaleyle ödenen psikolog) → yeni kayıt açma, bağla
        with connect() as c:
            dup = c.execute(
                """SELECT id FROM entries WHERE import_hash IS NULL AND kind = ? AND ABS(amount - ?) < 0.01
                   AND entry_date BETWEEN ? AND ? LIMIT 1""",
                (kind, amount, (d - timedelta(days=3)).isoformat(), (d + timedelta(days=3)).isoformat())).fetchone()
            if dup:
                c.execute("UPDATE entries SET import_hash = ? WHERE id = ?", (r["hash"], dup["id"]))
                stats["eslesen_elle"] += 1
                seen.add(r["hash"])
                continue
        norm = normalize(desc)
        category = None
        if kind == "gelir":
            rec = next((x for x in recurring if x["kind"] == "gelir"
                        and (normalize(x["description"]) in norm or (_re.search(r"MAAS|UCRET|BORDRO", norm)
                             and _re.search(r"MAAS", normalize(x["description"]))))), None)
            if rec:
                desc, category = rec["description"], rec["category"]  # sabit kaleme bağlansın
                stats["maas"] += 1
                # tarihe en yakın dönemin maaşı zaten elle girildi/onaylandıysa: yeni kayıt açma,
                # bankadaki gerçek tutar ve tarihle güncelle (net maaş elle girilenden birkaç lira farklı olabilir)
                target = _nearest_recurring_month(rec, d)
                with connect() as c:
                    existing = c.execute(
                        "SELECT id FROM entries WHERE recurring_id = ? AND month = ? AND import_hash IS NULL AND skipped = 0",
                        (rec["id"], target)).fetchone()
                    if existing:
                        c.execute("UPDATE entries SET amount = ?, entry_date = ?, import_hash = ?, note = ? WHERE id = ?",
                                  (amount, r["date"], r["hash"], f"Hesap hareketi · {r['description']}", existing["id"]))
                        stats["eslesen_elle"] += 1
                        seen.add(r["hash"])
                        continue
            category = category or "Diğer Gelir"
        else:
            category = categorize(desc, "harcama", rules)
            if category == "Kategorisiz":
                category = None
        payment = "havale" if _re.search(r"EFT|HAVALE|FAST|TRANSFER", norm) else "nakit"
        create_entry({"entry_date": r["date"], "description": desc, "amount": amount, "kind": kind,
                      "category": category or "Kategorisiz", "payment": payment if kind == "gider" else None,
                      "note": "Hesap hareketi" + ("" if desc == r["description"] else f" · {r['description']}"),
                      "import_hash": r["hash"]})
        stats[kind] += 1
        seen.add(r["hash"])
    return stats
