"""SQLite katmanı. Tek dosya: data/ekstre.db"""
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import date, timedelta

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
-- Maaş değişiklikleri (zam, iş değişikliği): o dönemden itibaren geçerli brüt. Eski aylar eski brütle kalır.
CREATE TABLE IF NOT EXISTS salary_changes (
    id INTEGER PRIMARY KEY,
    recurring_id INTEGER NOT NULL REFERENCES recurring(id) ON DELETE CASCADE,
    from_month TEXT NOT NULL,          -- dönem (YYYY-MM), uygulamadaki ay seçicisiyle aynı
    gross REAL NOT NULL CHECK (gross >= 0),
    reason TEXT NOT NULL DEFAULT 'zam' CHECK (reason IN ('zam', 'is_degisikligi')),
    reset_cum INTEGER NOT NULL DEFAULT 0,   -- yeni işveren vergi matrahını sıfırdan başlattı
    pay_day INTEGER,                   -- yeni maaş günü (iş değişikliği)
    employer TEXT,                     -- banka açıklamasında geçen yeni işveren adı (eşleştirme için)
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- İşyeri takma adları: ekstredeki resmi unvan → marka (ör. NEW TASTE GIDA → ESPRESSOLAB). Fişten öğrenilir.
CREATE TABLE IF NOT EXISTS merchant_aliases (
    source TEXT PRIMARY KEY,   -- merchant_name(resmi unvan)
    display TEXT NOT NULL      -- merchant_name(marka)
);
-- Kendi hesapları arası aktarımlar (gelir/gider sayılmaz; Transferler panelinde görünür)
CREATE TABLE IF NOT EXISTS own_transfers (
    id INTEGER PRIMARY KEY,
    transfer_date DATE NOT NULL,
    amount REAL NOT NULL,            -- giren +, çıkan −
    account TEXT,                    -- dökümün bankası
    description TEXT,
    import_hash TEXT UNIQUE
);
-- Hesaptan yapılan kart ödemeleri (gider sayılmaz; ekstresi yüklenmemiş kartı fark etmek için tutulur)
CREATE TABLE IF NOT EXISTS card_payments (
    id INTEGER PRIMARY KEY,
    pay_date DATE NOT NULL,
    amount REAL NOT NULL,
    card_last4 TEXT,
    account TEXT,
    description TEXT,
    import_hash TEXT UNIQUE
);
-- Borç / alacak defteri: gelir/gider toplamlarını etkilemez, sadece kim kime ne borçlu takibi.
-- Birim TL, döviz, altın ya da serbest metin olabilir (ör. TRY, USD, GRAM_ALTIN, CEYREK_ALTIN, "22 ayar bilezik").
CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY,
    person TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('borc', 'alacak')),  -- borc: ben ona borçluyum
    asset TEXT NOT NULL DEFAULT 'TRY',
    amount REAL NOT NULL CHECK (amount > 0),
    paid_amount REAL NOT NULL DEFAULT 0,
    debt_date DATE NOT NULL,
    due_date DATE,
    note TEXT,
    settled_at DATE,
    settled_entry_id INTEGER,          -- (eski) kapatan hareket; artık debt_payments.entry_id
    given_entry_id INTEGER,            -- parayı verdiğim/aldığım banka hareketi (varsa)
    written_off_at DATE,               -- "vazgeçtim": kalan o gün gider (alacak) / gelir (borç) olur
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Borç ödemeleri / tahsilatları (kısmi olabilir). entry_id: bunu karşılayan banka hareketi ya da elle kayıt
CREATE TABLE IF NOT EXISTS debt_payments (
    id INTEGER PRIMARY KEY,
    debt_id INTEGER NOT NULL REFERENCES debts(id) ON DELETE CASCADE,
    amount REAL NOT NULL CHECK (amount > 0),
    paid_at DATE NOT NULL,
    entry_id INTEGER
);
-- E-posta otomasyonu günlüğü: her mail bir kez işlenir (Message-ID)
CREATE TABLE IF NOT EXISTS mail_log (
    message_id TEXT PRIMARY KEY,
    received_at TEXT,
    sender TEXT,
    subject TEXT,
    kind TEXT,            -- pdf | bildirim | diger | -
    status TEXT,          -- ok | skip | error
    detail TEXT,
    processed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Alt kalemler: bir harcamanın bir kısmı başka kategoride (ör. market fişindeki sigara). Ana kayıt kalan tutarla sayılır.
CREATE TABLE IF NOT EXISTS splits (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
    txn_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
    amount REAL NOT NULL CHECK (amount > 0),
    category TEXT NOT NULL,
    description TEXT,
    origin TEXT NOT NULL DEFAULT 'manual',   -- manual | kural | ai
    CHECK ((entry_id IS NULL) <> (txn_id IS NULL))
);
-- Fiş satırları: ürün fiyat takibi (kişisel enflasyon). Kayıt silinince satırları da gider.
CREATE TABLE IF NOT EXISTS receipt_items (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
    purchased_at DATE NOT NULL,
    merchant TEXT,
    name TEXT NOT NULL,
    product_key TEXT NOT NULL,         -- normalize edilmiş ad: aynı ürün farklı fişlerde eşleşsin
    qty REAL NOT NULL DEFAULT 1,
    amount REAL NOT NULL,
    unit_price REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_receipt_items_key ON receipt_items(product_key);
-- Birikim hedefleri: "Haziran'a kadar 100 bin". İlerleme = hedef konduğu dönemden beri eldeki paradaki artış.
CREATE TABLE IF NOT EXISTS saving_targets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    deadline DATE NOT NULL,
    start_month TEXT NOT NULL,
    already REAL NOT NULL DEFAULT 0,        -- hedefe başlarken zaten ayrılmış tutar
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Kumbara: bir hedef için ayrılan para. Para hesapta/cepte durmaya devam eder (toplam servet değişmez) ama
-- "serbest" bakiyeden düşer; böylece harcanabilecek gerçek tutar görünür.
CREATE TABLE IF NOT EXISTS jars (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    goal REAL CHECK (goal IS NULL OR goal > 0),
    deadline DATE,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,  -- paranın durduğu hesap (bilgi)
    monthly REAL CHECK (monthly IS NULL OR monthly > 0),            -- her ay otomatik ayrılan
    auto_day INTEGER CHECK (auto_day IS NULL OR auto_day BETWEEN 1 AND 28),
    last_auto TEXT,                                                 -- son otomatik ayrılan ay (YYYY-MM)
    note TEXT,
    closed_at DATE,                                                 -- hedefe ulaşıldı / harcandı: ayrılan serbest kalır
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jar_moves (
    id INTEGER PRIMARY KEY,
    jar_id INTEGER NOT NULL REFERENCES jars(id) ON DELETE CASCADE,
    move_date DATE NOT NULL,
    amount REAL NOT NULL CHECK (amount <> 0),   -- + yatır, − çek
    note TEXT,
    auto INTEGER NOT NULL DEFAULT 0
);
-- Kalem kuralları: fişteki satırda bu kelimelerden biri geçerse o satır bu kategoriye ayrılır
CREATE TABLE IF NOT EXISTS item_rules (
    id INTEGER PRIMARY KEY,
    keywords TEXT NOT NULL,                  -- virgülle: "sigara, marlboro, parliament"
    category TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Hesaplar (tampon): her hesabın son bilinen bakiyesi. Döküm yüklenince kendiliğinden güncellenir; nakit/diğer elle.
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'banka',      -- banka | nakit | diger
    balance REAL NOT NULL DEFAULT 0,
    as_of DATE,
    source TEXT NOT NULL DEFAULT 'elle',     -- dokum | elle
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Kişi bazında ayar: kategori ve borç takibi
CREATE TABLE IF NOT EXISTS counterparties (
    name TEXT PRIMARY KEY,
    category TEXT,
    track_debt INTEGER NOT NULL DEFAULT 0,
    note TEXT
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
    counterparty TEXT,                   -- havale/FAST karşı tarafı (kişi)
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
DEFAULT_ITEM_RULE = ("sigara, marlboro, parliament, camel, winston, kent, lucky strike, chesterfield, l&m, "
                     "tekel, samsun, maltepe, viceroy, west, davidoff, muratti, iqos, heets, terea, tütün, puro, sarmalık")

MIGRATIONS = [
    ("statements", "next_period_end", "DATE"),
    ("statements", "next_due_date", "DATE"),
    ("transactions", "is_deleted", "INTEGER NOT NULL DEFAULT 0"),
    ("recurring", "gross_amount", "REAL"),
    ("entries", "installments", "INTEGER NOT NULL DEFAULT 1"),  # kredi kartı taksit sayısı
    ("recurring", "advance_pay", "INTEGER NOT NULL DEFAULT 0"),  # maaş peşin (memur): zam farkı ilk zamlı maaşta
    ("statements", "paid_amount", "REAL"),
    ("statements", "paid_at", "DATE"),
    ("entries", "payment", "TEXT"),
    ("entries", "import_hash", "TEXT"),
    ("entries", "counterparty", "TEXT"),
    ("entries", "debt_id", "INTEGER"),  # borç hareketi: gelir/gider sayılmaz, sadece eldeki parayı etkiler
    ("debts", "given_entry_id", "INTEGER"),
    ("debts", "written_off_at", "DATE"),
    ("splits", "debt_id", "INTEGER"),
    ("entries", "location", "TEXT"),            # fişteki işyeri adresi (opsiyonel)
    ("receipt_items", "unit", "TEXT NOT NULL DEFAULT 'adet'"),   # adet | kg | lt
    ("receipt_items", "product", "TEXT"),       # standart ürün adı (marketler arası eşleşme)
    ("receipt_items", "price_known", "INTEGER NOT NULL DEFAULT 1"),  # 1 okundu · 2 toplamdan çıkarıldı · 0 okunamadı
    ("receipt_items", "branch", "TEXT"),        # şube/semt (Fıstıkağacı)
    ("entries", "wallet_id", "INTEGER"),        # ön ödemeli kart (yemek/hediye/giyim): gider = harcama, gelir = yükleme
    ("recurring", "wallet_id", "INTEGER"),      # karta aylık yükleme (kendiliğinden gerçekleşir)
    ("accounts", "card_type", "TEXT"),          # kart: yemek | hediye | giyim | diger
    ("accounts", "expires_at", "DATE"),         # hediye kartı son kullanma
    ("accounts", "anchor_entry_id", "INTEGER NOT NULL DEFAULT 0"),  # kart bakiyesi bu kayıttan sonrakilerle güncellenir
    ("entries", "account_id", "INTEGER"),       # gelirin girdiği / giderin çıktığı hesap (banka, nakit)
    ("recurring", "account_id", "INTEGER"),     # sabit kalemin hesabı (Maaş → Halkbank)
    ("debts", "account_id", "INTEGER"),         # borcun verildiği/alındığı hesap
    ("debt_payments", "account_id", "INTEGER"), # ödeme/tahsilatın hesabı
    ("receipt_items", "brand", "TEXT"),         # ürün markası (Ülker) ya da kafenin kendisi           # ortak harcamada kişinin payı → alacak
    ("debts", "split_id", "INTEGER"),           # alacak bir harcamanın payından doğduysa (veriliş = o harcama)
    ("entries", "linked_txn_id", "INTEGER REFERENCES transactions(id) ON DELETE SET NULL"),
]


# ---------------------------------------------------------------- aktif kullanıcının alanı
# Her istek (ve zamanlanmış iş) giriş yapan kullanıcının veritabanında çalışır. Bağlam boşsa ana veritabanı
# (DB_PATH: yönetici / tek kullanıcılı kurulum / testler).
_space: ContextVar[dict | None] = ContextVar("hesapkitap_space", default=None)


def db_path() -> str:
    s = _space.get()
    return s["db"] if s else DB_PATH


def upload_dir() -> str:
    s = _space.get()
    return s["uploads"] if s else UPLOAD_DIR


def bill_dir() -> str:
    s = _space.get()
    return s["bills"] if s else BILL_DIR


def current_user() -> dict | None:
    s = _space.get()
    return s.get("user") if s else None


@contextmanager
def use_space(db_file: str | None, user: dict | None = None):
    """db_file None: ana veritabanı. Kullanıcı klasöründe ilk kullanımda şema kurulur."""
    if db_file:
        base = os.path.dirname(os.path.abspath(db_file))
        space = {"db": db_file, "uploads": os.path.join(base, "uploads"), "bills": os.path.join(base, "bills"), "user": user}
    else:
        space = {"db": DB_PATH, "uploads": UPLOAD_DIR, "bills": BILL_DIR, "user": user}
    token = _space.set(space)
    try:
        if db_file and not os.path.exists(db_file):
            init_db()
        yield
    finally:
        _space.reset(token)


@contextmanager
def connect():
    path = db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
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
        if not c.execute("SELECT 1 FROM settings WHERE key = 'item_rules_seeded'").fetchone():
            # hazır örnek: kullanıcı silebilir/değiştirebilir, bir kez eklenir
            c.execute("INSERT INTO item_rules (keywords, category) VALUES (?, ?)", (DEFAULT_ITEM_RULE, "Sigara"))
            c.execute("INSERT INTO settings (key, value) VALUES ('item_rules_seeded', '1')")
        # eski borç kayıtları: ödenen tutar tek bir ödeme satırına
        c.execute("""INSERT INTO debt_payments (debt_id, amount, paid_at, entry_id)
                     SELECT id, paid_amount, COALESCE(settled_at, date('now')), settled_entry_id FROM debts
                     WHERE paid_amount > 0 AND id NOT IN (SELECT debt_id FROM debt_payments)""")
        c.execute("""UPDATE entries SET debt_id = (SELECT debt_id FROM debt_payments p WHERE p.entry_id = entries.id)
                     WHERE debt_id IS NULL AND id IN (SELECT entry_id FROM debt_payments WHERE entry_id IS NOT NULL)""")
        _sync_default_rules(c)
    auto_mark_paid()
    auto_update_cycle()
    _ensure_imported_bank_accounts()


def _ensure_imported_bank_accounts() -> None:
    """Dökümü yüklenmiş bankalar kayıtlarda seçilebilsin: hesap yoksa bakiyesi bilinmeyen hesap olarak aç."""
    from app.bank_import import short_bank

    for name in {short_bank(b) for b in imported_bank_names()} - {"Banka hesabı"}:
        ensure_account(name)


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
        path = os.path.join(upload_dir(), f"{r['file_hash']}.pdf")
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
    os.makedirs(upload_dir(), exist_ok=True)
    with open(os.path.join(upload_dir(), f"{file_hash(data)}.pdf"), "wb") as out:
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
            n = int(e.get("installments") or 1)
            target = e["amount"] / n  # taksitli: ekstreye ilk taksit yansır
            cands = [t for t in free if abs(t["amount"] - target) <= (0.05 if n == 1 else 0.02 * n)
                     and (n == 1 or (t.get("installment_no") or 1) == 1)
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
                "payment", "linked_txn_id", "month", "import_hash", "counterparty", "debt_id", "location", "wallet_id", "account_id",
                "installments")


def installment_parts(e: dict) -> list[tuple[int, str, float]]:
    """Taksitli kredi kartı harcaması: (sıra, dönem, tutar). Tek çekimde tek parça. Kuruş farkı son taksitte."""
    n = int(e.get("installments") or 1)
    if n <= 1 or e.get("payment") != "kredi_karti" or e.get("kind") != "gider":
        return [(0, e["month"], e["amount"])]
    part = round(e["amount"] / n, 2)
    y, m = map(int, e["month"].split("-"))
    out = []
    for k in range(n):
        mm = (m - 1 + k) % 12 + 1
        yy = y + (m - 1 + k) // 12
        out.append((k, f"{yy:04d}-{mm:02d}", part if k < n - 1 else round(e["amount"] - part * (n - 1), 2)))
    return out


def installment_entries() -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT * FROM entries WHERE installments > 1 AND payment = 'kredi_karti' AND kind = 'gider'"))


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
    best = min(cands, key=lambda m: abs((_date.fromisoformat(occurrence_date(m, day_at(rec, m))) - d).days))
    data["recurring_id"], data["month"] = rec["id"], best


def _nearest_recurring_month(rec, d) -> str:
    """Tarihe en yakın sabit kalem döneminin ayı (dolu olup olmamasına bakmadan)."""
    from datetime import date as _date

    return min(_months_around(period_of(d)),
               key=lambda m: abs((_date.fromisoformat(occurrence_date(m, day_at(rec, m))) - d).days))


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

RECURRING_FIELDS = ("description", "amount", "kind", "category", "day_of_month", "start_month", "end_month", "note", "wallet_id", "account_id",
                    "gross_amount", "advance_pay")


def list_recurring() -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT * FROM recurring ORDER BY kind DESC, amount DESC"))


def _norm_recurring(data: dict) -> dict:
    if "advance_pay" in data:
        if data["advance_pay"] is None:
            data.pop("advance_pay")
        else:
            data["advance_pay"] = int(bool(data["advance_pay"]))
    return data


def create_recurring(data: dict) -> int:
    data = _norm_recurring({k: data.get(k) for k in RECURRING_FIELDS if k in data})
    with connect() as c:
        return c.execute(f"INSERT INTO recurring ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                         tuple(data.values())).lastrowid


def update_recurring(rid: int, data: dict) -> None:
    data = _norm_recurring({k: v for k, v in data.items() if k in RECURRING_FIELDS})
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
            os.remove(os.path.join(bill_dir(), b["file_name"]))
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
    os.makedirs(bill_dir(), exist_ok=True)
    name = f"{file_hash(data)[:24]}{ext}"
    with open(os.path.join(bill_dir(), name), "wb") as f:
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


def planned_amount(r: dict, month: str, overrides: dict | None = None, changes: list[dict] | None = None) -> float:
    """Sabit kalemin o ay için beklenen tutarı: aya özel tutar > brüt maaştan hesaplanan net > şablon."""
    r = dict(r)
    ov = overrides if overrides is not None else recurring_overrides()
    if (r["id"], month) in ov:
        return ov[(r["id"], month)]
    changes = salary_changes(r["id"]) if changes is None else changes
    if r.get("gross_amount") or changes:
        if gross_at(r, month, changes) is None:  # brüt bilinmeyen (ilk değişiklikten önceki) dönem: şablon net
            return r["amount"]
        from app.payroll import net_schedule

        paid = occurrence_date(month, day_at(r, month, changes))  # maaşın o dönemde yattığı gerçek ay
        year = int(paid[:4])
        grosses, resets = gross_schedule(r, year, changes)
        return net_schedule(year, grosses, resets)["months"][int(paid[5:7]) - 1]["net"]
    return r["amount"]


# ---------------------------------------------------------------- maaş değişiklikleri

def salary_changes(recurring_id: int) -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT * FROM salary_changes WHERE recurring_id = ? ORDER BY from_month, id",
                              (recurring_id,)))


def add_salary_change(recurring_id: int, data: dict) -> int:
    with connect() as c:
        if not c.execute("SELECT 1 FROM recurring WHERE id = ?", (recurring_id,)).fetchone():
            raise KeyError(recurring_id)
        # aynı döneme ikinci değişiklik: öncekinin yerine geçer
        c.execute("DELETE FROM salary_changes WHERE recurring_id = ? AND from_month = ?", (recurring_id, data["from_month"]))
        return c.execute(
            "INSERT INTO salary_changes (recurring_id, from_month, gross, reason, reset_cum, pay_day, employer, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (recurring_id, data["from_month"], data["gross"], data.get("reason") or "zam", int(bool(data.get("reset_cum"))),
             data.get("pay_day"), (data.get("employer") or "").strip() or None, data.get("note"))).lastrowid


def delete_salary_change(change_id: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM salary_changes WHERE id = ?", (change_id,)).rowcount > 0


def gross_at(r: dict, month: str, changes: list[dict]) -> float | None:
    """O dönemde geçerli brüt: son değişiklik, yoksa kalemin brütü."""
    g = dict(r).get("gross_amount")
    for ch in changes:
        if ch["from_month"] <= month:
            g = ch["gross"]
    return g


def day_at(r: dict, month: str, changes: list[dict] | None = None) -> int:
    """O dönemde maaş günü (iş değişikliğinde değişebilir)."""
    r = dict(r)
    d = r.get("day_of_month") or 1
    for ch in changes if changes is not None else salary_changes(r["id"]):
        if ch["from_month"] <= month and ch.get("pay_day"):
            d = ch["pay_day"]
    return d


def gross_schedule(r: dict, year: int, changes: list[dict]) -> tuple[dict[int, float], set[int]]:
    """Takvim yılının her ayında ödenen brüt + vergi matrahının sıfırlandığı aylar.
    Kayıttan önceki aylar (yıl başı) bilinen ilk brütle dolu varsayılır: kümülatif matrah gerçekçi kalsın."""
    r = dict(r)
    first = r.get("gross_amount") or (changes[0]["gross"] if changes else 0.0)
    grosses: dict[int, float] = {}
    for m in range(1, 13):
        period = period_of(f"{year}-{m:02d}-{min(day_at(r, f'{year}-{m:02d}', changes), 28):02d}")
        g = gross_at(r, period, changes)
        grosses[m] = g if g is not None else first
    # Peşin maaş (memur: ayın 15'inde o günden sonraki ay için): zam ayın 1'inde başlar, 1..(gün-1) arası eski
    # maaşla önceden ödenmiştir → aradaki fark zamlı ilk maaşa eklenir (vergiye tabi brüt olarak)
    if r.get("advance_pay"):
        day = r.get("day_of_month") or 1
        prev_g = gross_at(r, period_of(f"{year - 1}-12-{min(day, 28):02d}"), changes) or first
        for m in range(1, 13):
            base = grosses[m]
            if base > prev_g and day > 1:
                grosses[m] = round(base + (base - prev_g) * (day - 1) / 30, 2)
            prev_g = base
    resets = set()
    for ch in changes:
        if ch["reason"] == "is_degisikligi" and ch["reset_cum"]:
            paid = occurrence_date(ch["from_month"], ch.get("pay_day") or r.get("day_of_month") or 1)
            if int(paid[:4]) == year:
                resets.add(int(paid[5:7]))
    return grosses, resets


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


def import_bank_rows(rows: list[dict], account: str = "", owners: list[str] | None = None) -> dict:
    """Ayrıştırılmış hesap hareketlerini kaydet. Özet sayaçları döner."""
    import re as _re
    from datetime import date as _date, timedelta

    from app.textnorm import normalize

    stats = {"gelir": 0, "gider": 0, "kart_odemesi": 0, "virman": 0, "tekrar": 0, "eslesen_elle": 0,
             "odendi_isaretlenen": 0, "maas": 0}
    from app.bank_import import short_bank

    acc_id = ensure_account(short_bank(account)) if account else None
    with connect() as c:
        rules = load_rules(c)
        seen = {r[0] for r in c.execute("SELECT import_hash FROM entries WHERE import_hash IS NOT NULL "
                                        "UNION SELECT import_hash FROM own_transfers WHERE import_hash IS NOT NULL "
                                        "UNION SELECT import_hash FROM card_payments WHERE import_hash IS NOT NULL")}
        recurring = [dict(r) for r in c.execute("SELECT * FROM recurring")]
    from app.bank_import import counterparty

    # bu dökümdeki ve daha önce görülen kişiler: aynı tutarda birden çok havale varsa elle girilen kaydı kişiye göre eşle
    people = {p for p in (counterparty(x["description"], x["amount"], owners) for x in rows
                          if x.get("type") in ("gelir", "gider")) if p}
    with connect() as c:
        people |= {n for (n,) in c.execute("SELECT DISTINCT counterparty FROM entries WHERE counterparty IS NOT NULL")}
    for r in rows:
        if r["hash"] in seen or (r["hash"].startswith("mail:") and _already_imported(r, people, owners)):
            stats["tekrar"] += 1
            if acc_id:  # eski yükleme hesapsız kaldıysa: tekrar yüklenince hesabına bağlanır
                with connect() as c:
                    c.execute("UPDATE entries SET account_id = ? WHERE import_hash = ? AND account_id IS NULL AND wallet_id IS NULL",
                              (acc_id, r["hash"]))
                    c.execute("UPDATE recurring SET account_id = ? WHERE account_id IS NULL AND id IN "
                              "(SELECT recurring_id FROM entries WHERE import_hash = ? AND recurring_id IS NOT NULL)",
                              (acc_id, r["hash"]))  # maaş bu hesaba yatıyormuş: sabit kalem öğrenir
            continue
        d = _date.fromisoformat(r["date"])
        if r["type"] == "virman":
            stats["virman"] += 1
            with connect() as c:
                if not r["hash"].startswith("mail:"):  # PDF geldi: aynı aktarımın bildirimden gelen geçici kaydını sil
                    c.execute("DELETE FROM own_transfers WHERE import_hash LIKE 'mail:%' AND ABS(amount - ?) < 0.01 "
                              "AND transfer_date BETWEEN date(?, '-2 days') AND date(?, '+2 days')",
                              (r["amount"], r["date"], r["date"]))
                c.execute("INSERT OR IGNORE INTO own_transfers (transfer_date, amount, account, description, import_hash) "
                          "VALUES (?, ?, ?, ?, ?)", (r["date"], r["amount"], account, r["description"][:500], r["hash"]))
            continue
        if r["type"] == "gider" and match_card_payment(abs(r["amount"]), r["date"]):
            r["type"] = "kart_odemesi"  # açıklamada "kart" yok ama tutar bir ekstreyle birebir
        if r["type"] == "kart_odemesi":
            stats["kart_odemesi"] += 1
            amt = abs(r["amount"])
            card = _re.search(r"\d{4}\s*\*+\s*(\d{4})", r["description"])
            with connect() as c:
                if not r["hash"].startswith("mail:"):
                    c.execute("DELETE FROM card_payments WHERE import_hash LIKE 'mail:%' AND ABS(amount - ?) < 0.01 "
                              "AND pay_date BETWEEN date(?, '-2 days') AND date(?, '+2 days')", (amt, r["date"], r["date"]))
                c.execute("INSERT OR IGNORE INTO card_payments (pay_date, amount, card_last4, account, description, import_hash) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (r["date"], amt, card.group(1) if card else None, account,
                                                        r["description"][:300], r["hash"]))
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
            cands = [dict(x) for x in c.execute(
                """SELECT id, entry_date, description, note, counterparty FROM entries
                   WHERE (import_hash IS NULL OR (import_hash LIKE 'mail:%' AND COALESCE(payment, '') <> 'kredi_karti'))
                   AND import_hash IS NOT ? AND wallet_id IS NULL AND kind = ? AND ABS(amount - ?) < 0.01
                   AND entry_date BETWEEN ? AND ?""",
                (r["hash"], kind, amount, (d - timedelta(days=3)).isoformat(), (d + timedelta(days=3)).isoformat()))]
            who_here = counterparty(r["description"], r["amount"], owners) or _who_hint(r, owners)
            dup = _pick_manual(cands, who_here, people, d)
            if dup:
                c.execute("UPDATE entries SET import_hash = ?, counterparty = COALESCE(counterparty, ?), "
                          "account_id = COALESCE(account_id, ?) WHERE id = ?", (r["hash"], who_here, acc_id, dup["id"]))
                matched = c.execute("SELECT counterparty FROM entries WHERE id = ?", (dup["id"],)).fetchone()[0]
        if dup:  # bağlantı yazıldıktan sonra (ayrı bağlantı açan borç kapatma kilitlenmesin)
            if link_debt_movement(matched, kind, amount, r["date"], dup["id"]):
                stats["borc_hareketi"] = stats.get("borc_hareketi", 0) + 1
            stats["eslesen_elle"] += 1
            seen.add(r["hash"])
            continue
        norm = normalize(desc)
        category = None
        rec = None
        if kind == "gelir":
            rec = next((x for x in recurring if x["kind"] == "gelir"
                        and (normalize(x["description"]) in norm
                             or any(ch["employer"] and normalize(ch["employer"]) in norm for ch in salary_changes(x["id"]))
                             or (_re.search(r"MAAS|UCRET|BORDRO", norm)
                             and _re.search(r"MAAS", normalize(x["description"]))))), None)
            if rec:
                desc, category = rec["description"], rec["category"]  # sabit kaleme bağlansın
                stats["maas"] += 1
                if acc_id and not rec.get("account_id"):  # maaş bu hesaba yatıyor: sabit kalem öğrenir
                    with connect() as c:
                        c.execute("UPDATE recurring SET account_id = ? WHERE id = ? AND account_id IS NULL", (acc_id, rec["id"]))
                    rec["account_id"] = acc_id
                # tarihe en yakın dönemin maaşı zaten elle girildi/onaylandıysa: yeni kayıt açma,
                # bankadaki gerçek tutar ve tarihle güncelle (net maaş elle girilenden birkaç lira farklı olabilir)
                target = _nearest_recurring_month(rec, d)
                with connect() as c:
                    existing = c.execute(
                        "SELECT id FROM entries WHERE recurring_id = ? AND month = ? AND import_hash IS NULL AND skipped = 0",
                        (rec["id"], target)).fetchone()
                    if existing:
                        c.execute("UPDATE entries SET amount = ?, entry_date = ?, import_hash = ?, note = ?, "
                                  "account_id = COALESCE(account_id, ?) WHERE id = ?",
                                  (amount, r["date"], r["hash"], f"Hesap hareketi · {r['description']}", acc_id, existing["id"]))
                        stats["eslesen_elle"] += 1
                        seen.add(r["hash"])
                        continue
            category = category or "Diğer Gelir"
        else:
            category = categorize(desc, "harcama", rules)
            if category == "Kategorisiz":
                category = None
        payment = "havale" if _re.search(r"EFT|HAVALE|FAST|TRANSFER", norm) else "nakit"
        from app.bank_import import counterparty, transfer_note

        who = None if rec else counterparty(r["description"], r["amount"], owners) or _who_hint(r, owners)  # maaşta işveren kişi değil
        if who:
            who, payment = _known_counterparty(who), "havale"
            cp = get_counterparty(who)
            if cp and cp["category"]:
                category = cp["category"]  # kişiye verilmiş kategori
            note = transfer_note(r["description"])
            short = f"{who}" + (f" · {note}" if note else "")
            desc = short if desc == r["description"] else desc
        if desc == r["description"] and len(desc) > 120:
            desc = desc[:120]  # uzun banka metni notta tam haliyle kalır
        eid = create_entry({"entry_date": r["date"], "description": desc, "amount": amount, "kind": kind,
                            "category": category or "Kategorisiz", "payment": payment if kind == "gider" else None,
                            "note": "Hesap hareketi" + ("" if desc == r["description"] else f" · {r['description']}"),
                            "import_hash": r["hash"], "counterparty": who, "account_id": acc_id})
        if link_debt_movement(who, kind, amount, r["date"], eid):
            stats["borc_hareketi"] = stats.get("borc_hareketi", 0) + 1
        stats[kind] += 1
        seen.add(r["hash"])
    # hesabın son bakiyesi: dökümdeki en son satırın bakiyesi (kayıtlar yazıldıktan sonra çapalanır)
    with_bal = [(r["date"], i, r["balance"]) for i, r in enumerate(rows) if r.get("balance") is not None]
    if with_bal and account:
        d_, _, bal = max(with_bal)
        set_account_balance(short_bank(account), bal, d_, source="dokum")
    return stats


# ---------------------------------------------------------------- kişiler (transferler paneli)

def _mentions(entry: dict, name: str) -> bool:
    """Elle girilen kayıt bu kişiden mi bahsediyor? ('Harun'a borç' ↔ 'Harun Büyüktepe'). Soyad tek başına yetmez:
    aynı soyadlı iki kişi (aile) karışmasın; ad ya da ad+soyad geçmeli."""
    import re as _re

    from app.textnorm import normalize

    words = set(_re.findall(r"[A-Z]+", normalize(" ".join(str(entry.get(k) or "") for k in ("description", "note", "counterparty")))))
    toks = _re.findall(r"[A-Z]+", normalize(name))
    if not toks:
        return False
    # Türkçe ek: 'HARUNA', 'HARUNUN' gibi yazımlar da ad sayılsın
    return any(w == toks[0] or (w.startswith(toks[0]) and len(w) - len(toks[0]) <= 3) for w in words)


def _pick_manual(cands: list[dict], who: str | None, people: set[str], d) -> dict | None:
    """Aynı tutar ve ±3 gün içindeki elle girilmiş kayıtlardan banka hareketine en uygunu.
    1) Hareketin karşı tarafını anan kayıt  2) Başka bir kişiyi anmayan kayıt  — ikisinde de tarihe en yakın."""
    from datetime import date as _date

    def closest(xs):
        return min(xs, key=lambda x: abs((_date.fromisoformat(x["entry_date"]) - d).days)) if xs else None

    if who:
        named = [x for x in cands if _mentions(x, who)]
        if named:
            return closest(named)
    return closest([x for x in cands if not any(_mentions(x, p) for p in people if p != who)])


def known_counterparty(name: str) -> str:
    return _known_counterparty(name)


def _known_counterparty(name: str) -> str:
    """Aynı kişi farklı bankada farklı yazılabilir ('Elif DEMIR' / 'Elif Demir'): ilk kaydedilen yazılışı kullan."""
    import re as _re

    from app.textnorm import normalize

    key = _re.sub(r"[^A-Z]", "", normalize(name))
    with connect() as c:
        for (n,) in c.execute("SELECT DISTINCT counterparty FROM entries WHERE counterparty IS NOT NULL "
                              "UNION SELECT name FROM counterparties UNION SELECT person FROM debts"):
            if _re.sub(r"[^A-Z]", "", normalize(n)) == key:
                return n
    return name


def get_counterparty(name: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM counterparties WHERE name = ?", (name,)).fetchone()
        return dict(r) if r else None


def set_counterparty(name: str, category: str | None = None, track_debt: bool | None = None, note: str | None = None) -> int:
    """Kişi ayarı; kategori verilirse o kişinin tüm transferlerine uygulanır. Etkilenen kayıt sayısını döner."""
    cur = get_counterparty(name) or {"category": None, "track_debt": 0, "note": None}
    cat = category if category is not None else cur["category"]
    td = int(track_debt) if track_debt is not None else cur["track_debt"]
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO counterparties (name, category, track_debt, note) VALUES (?, ?, ?, ?)",
                  (name, cat, td, note if note is not None else cur["note"]))
        if category:
            return c.execute("UPDATE entries SET category = ? WHERE counterparty = ?", (category, name)).rowcount
    return 0


def transfers_overview() -> dict:
    with connect() as c:
        people = rows(c.execute(
            """SELECT e.counterparty AS name,
                      ROUND(SUM(CASE WHEN e.kind = 'gider' THEN e.amount ELSE 0 END), 2) AS sent,
                      ROUND(SUM(CASE WHEN e.kind = 'gelir' THEN e.amount ELSE 0 END), 2) AS received,
                      COUNT(*) AS count, MAX(e.entry_date) AS last_date,
                      cp.category AS category, COALESCE(cp.track_debt, 0) AS track_debt
               FROM entries e LEFT JOIN counterparties cp ON cp.name = e.counterparty
               WHERE e.counterparty IS NOT NULL GROUP BY e.counterparty ORDER BY sent + received DESC"""))
        own = rows(c.execute("SELECT * FROM own_transfers ORDER BY transfer_date DESC"))
        items = rows(c.execute(
            "SELECT id, entry_date, description, amount, kind, category, counterparty, note FROM entries "
            "WHERE counterparty IS NOT NULL ORDER BY entry_date DESC"))
    debts = list_debts()
    by_name = {p["name"]: p for p in people}
    for p in people:
        p["net"] = round(p["received"] - p["sent"], 2)  # + ise sana gelen fazla
    for d in debts:
        if d["person"] not in by_name:  # sadece defterde olan kişi (ör. nakit/altın borcu)
            by_name[d["person"]] = {"name": d["person"], "sent": 0.0, "received": 0.0, "net": 0.0, "count": 0,
                                    "last_date": d["debt_date"], "category": None, "track_debt": 1}
            people.append(by_name[d["person"]])
    for p in people:
        # açık borç/alacak birim birim: {"TRY": -200, "CEYREK_ALTIN": 2} (+ alacak, − borç)
        bal: dict[str, float] = {}
        for d in debts:
            if d["person"] == p["name"] and not d["settled_at"]:
                bal[d["asset"]] = round(bal.get(d["asset"], 0) + (d["remaining"] if d["direction"] == "alacak" else -d["remaining"]), 4)
        p["open"] = {k: v for k, v in bal.items() if v}
    return {"people": people, "own": own, "items": items, "debts": debts}


def interest_charges(period: str) -> list[dict]:
    """Bu dönemin ekstrelerindeki faiz / vade farkı / faize bağlı vergi satırları ve nedeni için önceki ekstre ile
    ona yapılan ödemeler (geç mi, eksik mi)."""
    with connect() as c:
        rs = rows(c.execute(
            "SELECT t.description, t.amount, s.id AS statement_id, s.bank, s.card_no, s.period_end FROM transactions t "
            "JOIN statements s ON s.id = t.statement_id WHERE t.txn_type = 'faiz_ucret' AND t.is_deleted = 0 "
            "AND substr(s.period_end, 1, 7) = ?", (period,)))
        out = []
        for st_id in dict.fromkeys(r["statement_id"] for r in rs):
            lines = [r for r in rs if r["statement_id"] == st_id]
            st = lines[0]
            prev = c.execute("SELECT period_end, due_date, total_debt FROM statements WHERE card_no = ? AND bank = ? "
                             "AND period_end < ? ORDER BY period_end DESC LIMIT 1",
                             (st["card_no"], st["bank"], st["period_end"])).fetchone()
            pays = []
            if prev and prev["due_date"]:
                pays = rows(c.execute("SELECT pay_date, amount FROM card_payments WHERE card_last4 = ? AND pay_date >= ? "
                                      "AND pay_date <= ? ORDER BY pay_date",
                                      (st["card_no"][-4:], prev["period_end"], _shift_days(prev["due_date"], 25))))
            out.append({"bank": st["bank"], "card_no": st["card_no"], "period_end": st["period_end"], "lines": lines,
                        "prev": dict(prev) if prev else None, "payments": pays})
    return out


def card_payments_without_statement() -> list[dict]:
    """Bankadan kart ödemesi yapılmış ama o döneme ait ekstre yüklenmemiş: harcamalar hiç sayılmamış demektir."""
    with connect() as c:
        pays = rows(c.execute("SELECT * FROM card_payments ORDER BY pay_date"))
        sts = rows(c.execute("SELECT card_no, bank, period_end, due_date, total_debt FROM statements WHERE due_date IS NOT NULL"))
    first: dict[str, str] = {}  # kartın yüklenen ilk ekstresi: öncesi bankanın arşivinden eski olabilir
    for s in sts:
        k = s["card_no"][-4:]
        first[k] = min(first.get(k, s["period_end"]), s["period_end"])
    out = []
    for p in pays:
        covered = False
        for s in sts:
            same_card = s["card_no"][-4:] == p["card_last4"] if p["card_last4"] else abs(s["total_debt"] - p["amount"]) <= 1
            if same_card and s["period_end"] <= p["pay_date"] <= _shift_days(s["due_date"], 20):
                covered = True
                break
        if not covered:
            # ilk ekstreden önceki ödeme: bankalar çoğunlukla son 12 ayı verir, o ekstreler yüklenemeyebilir
            out.append({**p, "before_first": bool(p["card_last4"] and p["card_last4"] in first
                                                 and p["pay_date"] < first[p["card_last4"]])})
    return out


def _shift_days(d: str, n: int) -> str:
    from datetime import date as _date, timedelta

    return (_date.fromisoformat(d) + timedelta(days=n)).isoformat()


# ---------------------------------------------------------------- borç / alacak

DEBT_FIELDS = ("person", "direction", "asset", "amount", "debt_date", "due_date", "note", "account_id")


def list_debts(open_only: bool = False) -> list[dict]:
    with connect() as c:
        out = rows(c.execute("SELECT * FROM debts" + (" WHERE settled_at IS NULL" if open_only else "")
                             + " ORDER BY settled_at IS NOT NULL, COALESCE(due_date, debt_date), id"))
    pays = debt_payments() if out else []
    for d in out:
        d["remaining"] = 0.0 if d["written_off_at"] else round(d["amount"] - d["paid_amount"], 4)
        d["payments"] = [p for p in pays if p["debt_id"] == d["id"]]
    return out


def create_debt(data: dict) -> int:
    data = {k: data.get(k) for k in DEBT_FIELDS}
    data["person"] = _known_counterparty(data["person"].strip())
    data["asset"] = (data.get("asset") or "TRY").strip()
    with connect() as c:
        did = c.execute(f"INSERT INTO debts ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                        tuple(data.values())).lastrowid
    _link_existing_given(did)
    return did


def update_debt(debt_id: int, data: dict) -> bool:
    data = {k: v for k, v in data.items() if k in DEBT_FIELDS}
    if "person" in data and data["person"]:
        data["person"] = _known_counterparty(data["person"].strip())
    if not data:
        return False
    with connect() as c:
        return c.execute(f"UPDATE debts SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?",
                         (*data.values(), debt_id)).rowcount > 0


def delete_debt(debt_id: int) -> bool:
    with connect() as c:  # bağlı hareketler yeniden normal gelir/gider olur
        c.execute("UPDATE entries SET debt_id = NULL WHERE debt_id = ?", (debt_id,))
        c.execute("DELETE FROM splits WHERE debt_id = ?", (debt_id,))  # ortak harcama payı: ana harcamaya geri döner
        c.execute("DELETE FROM debt_payments WHERE debt_id = ?", (debt_id,))
        return c.execute("DELETE FROM debts WHERE id = ?", (debt_id,)).rowcount > 0


def pay_debt(debt_id: int, amount: float | None, on: str, entry_id: int | None = None, account_id: int | None = None) -> None:
    """Ödeme/tahsilat işle: kalanın tamamıysa kapanır, azsa kısmi. amount None → kalanın tamamı.
    entry_id verilirse o hareket (havale/elle kayıt) borç hareketi olur: gelir/gider sayılmaz."""
    with connect() as c:
        d = c.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
        if not d:
            raise KeyError(debt_id)
        amt = amount if amount is not None else d["amount"] - d["paid_amount"]
        c.execute("INSERT INTO debt_payments (debt_id, amount, paid_at, entry_id, account_id) VALUES (?, ?, ?, ?, ?)",
                  (debt_id, amt, on, entry_id, account_id))
        if entry_id:
            c.execute("UPDATE entries SET debt_id = ?, category = 'Borç / Alacak' WHERE id = ?", (debt_id, entry_id))
        _recalc_debt(c, debt_id)


def _recalc_debt(c, debt_id: int) -> None:
    d = c.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    paid, last = c.execute("SELECT COALESCE(SUM(amount), 0), MAX(paid_at) FROM debt_payments WHERE debt_id = ?",
                           (debt_id,)).fetchone()
    settled = d["written_off_at"] or (last if paid >= d["amount"] - 0.005 else None)
    c.execute("UPDATE debts SET paid_amount = ?, settled_at = ? WHERE id = ?", (min(paid, d["amount"]), settled, debt_id))


def unpay_debt(debt_id: int) -> None:
    """Ödemeleri ve vazgeçmeyi geri al; bağlı hareketler yeniden normal gelir/gider olur."""
    with connect() as c:
        c.execute("UPDATE entries SET debt_id = NULL WHERE id IN (SELECT entry_id FROM debt_payments WHERE debt_id = ?)",
                  (debt_id,))
        c.execute("DELETE FROM debt_payments WHERE debt_id = ?", (debt_id,))
        c.execute("UPDATE debts SET written_off_at = NULL WHERE id = ?", (debt_id,))
        _recalc_debt(c, debt_id)


def write_off_debt(debt_id: int, on: str) -> None:
    """Vazgeçtim: kalan alacak o gün gider, kalan borç (karşı taraf sildi) o gün gelir olur."""
    with connect() as c:
        if not c.execute("UPDATE debts SET written_off_at = ? WHERE id = ?", (on, debt_id)).rowcount:
            raise KeyError(debt_id)
        _recalc_debt(c, debt_id)


def debt_payments(debt_id: int | None = None) -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT * FROM debt_payments" + (" WHERE debt_id = ?" if debt_id else "") + " ORDER BY paid_at",
                              (debt_id,) if debt_id else ()))


def _entry_is_debt_linked(entry_id: int | None) -> bool:
    if not entry_id:
        return False
    with connect() as c:
        r = c.execute("SELECT debt_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return bool(r and r["debt_id"])


def link_debt_movement(person: str | None, kind: str, amount: float, on: str, entry_id: int | None) -> tuple[str, dict] | None:
    """Kişiye/kişiden TL hareketi açık bir borç kaydına ait mi?
    1) Borcun VERİLİŞİ: aynı tutar, borç tarihine ±3 gün (alacak → giden para, borç → gelen para)
    2) ÖDEME/TAHSİLAT: kalan tutar birebir (borcum → giden para, alacağım → gelen para)
    Bağlanan hareket gelir/gider sayılmaz. Döner: ("verildi" | "odeme", borç) ya da None."""
    from datetime import date as _date

    if not person or _entry_is_debt_linked(entry_id):
        return None
    key = _name_key(person)
    d_on = _date.fromisoformat(on)
    debts_ = [d for d in list_debts() if d["asset"] == "TRY" and _name_key(d["person"]) == key]
    given_dir = "alacak" if kind == "gider" else "borc"
    for d in debts_:
        if (d["direction"] == given_dir and not d["given_entry_id"] and not d.get("split_id") and abs(d["amount"] - amount) < 0.01
                and abs((_date.fromisoformat(d["debt_date"]) - d_on).days) <= 3):
            with connect() as c:
                c.execute("UPDATE debts SET given_entry_id = ? WHERE id = ?", (entry_id, d["id"]))
                if entry_id:
                    c.execute("UPDATE entries SET debt_id = ?, category = 'Borç / Alacak' WHERE id = ?", (d["id"], entry_id))
            return "verildi", d
    pay_dir = "borc" if kind == "gider" else "alacak"
    open_ = [d for d in debts_ if d["direction"] == pay_dir and not d["settled_at"] and d["debt_date"] <= on]
    for d in open_:
        if abs(d["remaining"] - amount) < 0.01:
            pay_debt(d["id"], amount, on, entry_id)
            return "odeme", d
    # kısmi: kişinin TEK açık kaydı varsa ve gelen/giden tutar kalandan azsa ondan düşülür (300'ün 250'si)
    if len(open_) == 1 and amount < open_[0]["remaining"]:
        pay_debt(open_[0]["id"], amount, on, entry_id)
        return "odeme", open_[0]
    return None


def settle_debt_by_transfer(person: str | None, kind: str, amount: float, on: str, entry_id: int | None) -> dict | None:
    """Geriye uyumluluk: hareket bir borca bağlandıysa o borç."""
    r = link_debt_movement(person, kind, amount, on, entry_id)
    return r[1] if r else None


def _link_existing_given(debt_id: int) -> None:
    """Borç sonradan yazıldıysa: o gün civarı o kişiye/kişiden aynı tutarlı hareket zaten varsa verilişe bağla."""
    from datetime import date as _date, timedelta

    with connect() as c:
        d = c.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
        if d["asset"] != "TRY" or d["given_entry_id"] or d["split_id"]:
            return
        kind = "gider" if d["direction"] == "alacak" else "gelir"
        day = _date.fromisoformat(d["debt_date"])
        key = _name_key(d["person"])
        for e in c.execute("""SELECT id, counterparty FROM entries WHERE debt_id IS NULL AND kind = ? AND ABS(amount - ?) < 0.01
                              AND entry_date BETWEEN ? AND ? AND counterparty IS NOT NULL""",
                           (kind, d["amount"], (day - timedelta(days=3)).isoformat(), (day + timedelta(days=3)).isoformat())):
            if _name_key(e["counterparty"]) == key:
                c.execute("UPDATE debts SET given_entry_id = ? WHERE id = ?", (e["id"], debt_id))
                c.execute("UPDATE entries SET debt_id = ?, category = 'Borç / Alacak' WHERE id = ?", (debt_id, e["id"]))
                return


def debt_rows(month: str | None = None) -> list[dict]:
    """TL borçların kayıtlı hareketi olmayan adımları (nakit verildi/alındı, elle "Ödedim") ve vazgeçmeler:
    finans listesine sentetik satır olarak girer."""
    out = []
    pays = debt_payments()
    for d in list_debts():
        if d["asset"] != "TRY":
            continue
        out_flow = -1 if d["direction"] == "alacak" else 1  # alacak: para benden çıktı
        if not d["given_entry_id"] and not d.get("split_id"):  # harcamanın payıysa veriliş o harcamanın alt kalemi
            out.append({"uid": f"d{d['id']}g", "debt_id": d["id"], "date": d["debt_date"], "flow": out_flow,
                        "amount": d["amount"], "person": d["person"], "account_id": d.get("account_id"),
                        "step": "verildi" if d["direction"] == "alacak" else "alindi"})
        for p in pays:
            if p["debt_id"] == d["id"] and not p["entry_id"]:
                out.append({"uid": f"d{d['id']}p{p['id']}", "debt_id": d["id"], "date": p["paid_at"], "flow": -out_flow,
                            "amount": p["amount"], "person": d["person"], "account_id": p.get("account_id"),
                            "step": "tahsil" if d["direction"] == "alacak" else "odendi"})
        if d["written_off_at"]:
            left = round(d["amount"] - sum(p["amount"] for p in pays if p["debt_id"] == d["id"]), 2)
            if left > 0.005:
                out.append({"uid": f"d{d['id']}w", "debt_id": d["id"], "date": d["written_off_at"], "flow": 0,
                            "amount": left, "person": d["person"], "step": "vazgecildi", "direction": d["direction"]})
    for r in out:
        r["month"] = period_of(r["date"])
    return [r for r in out if not month or r["month"] == month]


def _name_key(name: str) -> str:
    import re as _re

    from app.textnorm import normalize

    return _re.sub(r"[^A-Z]", "", normalize(name))


# ---------------------------------------------------------------- e-posta otomasyonu

def _who_hint(r: dict, owners: list[str] | None) -> str | None:
    """Bildirim mailinde model karşı tarafı ayrıca verir; kendisi değilse kullan."""
    import re as _re

    from app.bank_import import _pretty, _squash

    w = (r.get("who") or "").strip()
    if not w or len(w) < 3:
        return None
    own = {_squash(o) for o in owners or [] if o}
    if _squash(w) in own:
        return None
    return _pretty(_re.sub(r"\s+", " ", w.upper()), w) if w.isupper() else w


def _already_imported(r: dict, people: set[str], owners: list[str] | None) -> bool:
    """Bildirimden gelen hareket, dökümden (PDF) zaten içe aktarılmış mı? (bildirim geç işlendiyse çift olmasın)"""
    from datetime import date as _date

    from app.bank_import import counterparty

    amt, d = abs(r["amount"]), r["date"]
    window = ("date(?, '-3 days')", "date(?, '+3 days')")
    with connect() as c:
        if r["type"] == "virman":
            return c.execute(f"SELECT 1 FROM own_transfers WHERE ABS(ABS(amount) - ?) < 0.01 AND transfer_date "
                             f"BETWEEN {window[0]} AND {window[1]}", (amt, d, d)).fetchone() is not None
        if r["type"] == "kart_odemesi":
            return c.execute(f"SELECT 1 FROM card_payments WHERE ABS(amount - ?) < 0.01 AND pay_date "
                             f"BETWEEN {window[0]} AND {window[1]}", (amt, d, d)).fetchone() is not None
        kind = "gelir" if r["amount"] > 0 else "gider"
        cands = [dict(x) for x in c.execute(
            f"""SELECT id, entry_date, description, note, counterparty FROM entries WHERE import_hash IS NOT NULL
                AND import_hash NOT LIKE 'mail:%' AND wallet_id IS NULL AND kind = ? AND ABS(amount - ?) < 0.01
                AND entry_date BETWEEN {window[0]} AND {window[1]}""", (kind, amt, d, d))]
    if not cands:
        return False
    who = counterparty(r["description"], r["amount"], owners) or _who_hint(r, owners)
    return _pick_manual(cands, who, people, _date.fromisoformat(d)) is not None


def mail_seen(message_id: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM mail_log WHERE message_id = ?", (message_id,)).fetchone() is not None


def log_mail(r: dict) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO mail_log (message_id, received_at, sender, subject, kind, status, detail) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (r["message_id"], r.get("received_at"), r.get("sender"),
                                                  r.get("subject"), r.get("kind"), r.get("status"), r.get("detail")))


def mail_log(limit: int = 50) -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT * FROM mail_log ORDER BY processed_at DESC, received_at DESC LIMIT ?", (limit,)))


def card_holders() -> list[str]:
    """Kart ekstrelerindeki kart sahipleri (kendi hesapları arası virmanı tanımak için)."""
    with connect() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT card_holder FROM transactions WHERE card_holder <> ''")]


def owner_names() -> list[str]:
    import json

    return json.loads(get_setting("bank_owner_names") or "[]")


def remember_owner_name(name: str) -> None:
    import json

    names = owner_names()
    if name and name not in names:
        set_setting("bank_owner_names", json.dumps([*names, name], ensure_ascii=False))


def create_card_notice(on: str, amount: float, merchant: str, import_hash: str) -> bool:
    """Kart harcama bildirimi → "ekstre bekleniyor" kaydı; ekstre gelince karttaki işlemle eşleşir."""
    from app.categorize import categorize

    with connect() as c:
        if c.execute("SELECT 1 FROM entries WHERE import_hash = ?", (import_hash,)).fetchone():
            return False
        rules = load_rules(c)
    create_entry({"entry_date": on, "description": merchant[:120], "amount": amount, "kind": "gider",
                  "category": categorize(merchant, "harcama", rules), "payment": "kredi_karti",
                  "note": "Kart bildirimi (e-posta)", "import_hash": import_hash})
    auto_link()
    return True


# ---------------------------------------------------------------- alt kalemler (bölme) ve kalem kuralları

def list_item_rules() -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT * FROM item_rules ORDER BY category, id"))


def save_item_rule(keywords: str, category: str, rule_id: int | None = None) -> int:
    kws = ", ".join(k.strip() for k in keywords.split(",") if k.strip())
    if not kws or not category.strip():
        raise ValueError("Kelime ve kategori gerekli")
    with connect() as c:
        if rule_id:
            c.execute("UPDATE item_rules SET keywords = ?, category = ? WHERE id = ?", (kws, category.strip(), rule_id))
            return rule_id
        return c.execute("INSERT INTO item_rules (keywords, category) VALUES (?, ?)", (kws, category.strip())).lastrowid


def delete_item_rule(rule_id: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM item_rules WHERE id = ?", (rule_id,)).rowcount > 0


def match_item(name: str, rules: list[dict] | None = None) -> str | None:
    """Fiş satırı hangi kalem kuralına giriyor? Kelime başında eşleşir ('KENT' ✓, 'KENTUCKY' ✗ değil: 'kent' kelime
    sınırıyla aranır; 4+ harfli kelimeler satırın içinde de bulunur, fişteki bitişik yazımlar için)."""
    import re as _re

    from app.textnorm import normalize

    n = normalize(name)
    for r in rules if rules is not None else list_item_rules():
        for kw in (k.strip() for k in r["keywords"].split(",")):
            k = normalize(kw)
            if not k:
                continue
            if _re.search(rf"(?<![A-Z]){_re.escape(k)}(?![A-Z])", n) or (len(k) >= 6 and k in n):
                return r["category"]
    return None


def splits_map() -> dict[tuple[str, int], list[dict]]:
    """('entry'|'card', id) → alt kalemler. Fişten girilen kayıt ekstredeki kart işlemiyle eşleştiyse
    bölmesi kart işlemine de uygulanır (kart işleminin kendi bölmesi yoksa)."""
    out: dict[tuple[str, int], list[dict]] = {}
    with connect() as c:
        for r in rows(c.execute("SELECT s.*, d.person, d.settled_at AS debt_settled_at, d.amount - d.paid_amount AS debt_remaining "
                                "FROM splits s LEFT JOIN debts d ON d.id = s.debt_id ORDER BY s.id")):
            key = ("entry", r["entry_id"]) if r["entry_id"] else ("card", r["txn_id"])
            out.setdefault(key, []).append(r)
        if any(k[0] == "entry" for k in out):
            for e in c.execute("SELECT id, linked_txn_id FROM entries WHERE linked_txn_id IS NOT NULL"):
                if ("entry", e["id"]) in out and ("card", e["linked_txn_id"]) not in out:
                    out[("card", e["linked_txn_id"])] = out[("entry", e["id"])]
    return out


def _parent_amount(c, target: str, pid: int) -> float:
    if target == "entry":
        r = c.execute("SELECT amount, kind FROM entries WHERE id = ?", (pid,)).fetchone()
        if not r or r["kind"] != "gider":
            raise ValueError("Sadece gider bölünebilir")
    else:
        r = c.execute("SELECT amount, txn_type AS kind FROM transactions WHERE id = ?", (pid,)).fetchone()
        if not r or r["kind"] != "harcama":
            raise ValueError("Sadece harcama bölünebilir")
    return r["amount"]


def set_splits(target: str, pid: int, parts: list[dict], origin: str = "manual") -> list[dict]:
    """Alt kalemleri baştan yaz. Toplam ana tutarı geçemez; eşitse ana kayıt tümüyle bölünmüş olur.
    Kalemde kişi varsa (ortak harcama: "300'ü Ali'nin payı") o kısım gider değil, o kişiye ALACAK olur."""
    col = "entry_id" if target == "entry" else "txn_id"
    parts = [p for p in parts if (p.get("amount") or 0) > 0
             and ((p.get("category") or "").strip() or (p.get("person") or "").strip())]
    for p in parts:
        if (p.get("person") or "").strip():
            p["person"] = _known_counterparty(p["person"].strip())
            p["category"] = "Borç / Alacak"
    with connect() as c:
        total = _parent_amount(c, target, pid)
        if sum(p["amount"] for p in parts) > total + 0.005:
            raise ValueError(f"Alt kalemlerin toplamı ({sum(p['amount'] for p in parts):.2f}) harcamayı ({total:.2f}) geçemez")
        pdate = c.execute("SELECT entry_date FROM entries WHERE id = ?" if target == "entry"
                          else "SELECT txn_date FROM transactions WHERE id = ?", (pid,)).fetchone()[0]
        pdesc = c.execute("SELECT description FROM entries WHERE id = ?" if target == "entry"
                          else "SELECT description FROM transactions WHERE id = ?", (pid,)).fetchone()[0]
        old = rows(c.execute(f"SELECT s.*, d.person, d.paid_amount FROM splits s LEFT JOIN debts d ON d.id = s.debt_id "
                             f"WHERE s.{col} = ?", (pid,)))
        old_debts = {_name_key(o["person"]): o for o in old if o["debt_id"]}
        keep: set[int] = set()
        for p in parts:
            if p.get("person"):
                o = old_debts.get(_name_key(p["person"]))
                if o and o["paid_amount"] and p["amount"] + 0.005 < o["paid_amount"]:
                    raise ValueError(f"{p['person']} payının {o['paid_amount']:.2f} TL'si tahsil edilmiş; daha az olamaz")
                if o:
                    keep.add(o["debt_id"])
                    p["debt_id"] = o["debt_id"]
                    c.execute("UPDATE debts SET amount = ? WHERE id = ?", (round(p["amount"], 2), o["debt_id"]))
        for o in old:
            if o["debt_id"] and o["debt_id"] not in keep:
                if o["paid_amount"]:
                    raise ValueError(f"{o['person']} payının tahsilatı var; önce Borç / Alacak'tan geri al")
                c.execute("DELETE FROM debts WHERE id = ?", (o["debt_id"],))
        c.execute(f"DELETE FROM splits WHERE {col} = ?", (pid,))
        for p in parts:
            sid = c.execute(f"INSERT INTO splits ({col}, amount, category, description, origin, debt_id) VALUES (?, ?, ?, ?, ?, ?)",
                            (pid, round(p["amount"], 2), p["category"].strip(), (p.get("description") or "").strip()[:80] or None,
                             p.get("origin") or origin, p.get("debt_id"))).lastrowid
            if p.get("person") and not p.get("debt_id"):
                did = c.execute("INSERT INTO debts (person, direction, asset, amount, debt_date, note, split_id) "
                                "VALUES (?, 'alacak', 'TRY', ?, ?, ?, ?)",
                                (p["person"], round(p["amount"], 2), pdate, f"Ortak harcama: {pdesc}"[:120], sid)).lastrowid
                c.execute("UPDATE splits SET debt_id = ? WHERE id = ?", (did, sid))
            elif p.get("debt_id"):
                c.execute("UPDATE debts SET split_id = ? WHERE id = ?", (sid, p["debt_id"]))
        for o in {o["debt_id"] for o in old if o["debt_id"]} & keep:
            _recalc_debt(c, o)
    return splits_map().get((target, pid), [])


def propose_split(items: list[dict], category: str | None, total: float | None) -> dict:
    """Fiş satırlarından bölme ÖNERİSİ (kaydetmez; kullanıcı formda onaylar/düzeltir).
    Döner: {"parts": [{category, amount, description, origin}], "category": tüm fiş tek kategoriyse o kategori}"""
    rules = list_item_rules()
    allowed = {r["category"] for r in rules}
    groups: dict[str, dict] = {}
    for it in items or []:
        amt = it.get("amount")
        if not amt or amt <= 0 or not it.get("name"):
            continue
        cat, origin = match_item(it["name"], rules), "kural"
        if not cat and it.get("split") in allowed:
            cat, origin = it["split"], "ai"
        if not cat or cat == category:
            continue
        g = groups.setdefault(cat, {"amount": 0.0, "names": [], "origin": origin})
        g["amount"] += amt
        g["names"].append(f"{it['qty']}× {it['name']}" if (it.get("qty") or 1) > 1 else it["name"])
    split_total = sum(g["amount"] for g in groups.values())
    if total and len(groups) == 1 and split_total >= total - 0.01:  # fişin tamamı tek kategori
        return {"parts": [], "category": next(iter(groups))}
    if not groups or (total and split_total > total + 0.01):  # satırlar toplamı tutmuyorsa (okuma hatası) önerme
        return {"parts": [], "category": None}
    return {"parts": [{"category": c, "amount": round(g["amount"], 2), "description": ", ".join(g["names"])[:80],
                       "origin": g["origin"]} for c, g in groups.items()], "category": None}


# ---------------------------------------------------------------- hesaplar (bakiye tamponu)

def set_account_balance(name: str, balance: float, as_of: str, source: str = "elle", kind: str = "banka") -> None:
    """Bakiye çapası: o tarihteki bakiye. Sonraki (ve aynı gün sonradan girilen) hareketler bunun üstüne eklenir.
    Döküm daha eski bir tarihe aitse mevcut (daha yeni) bakiyeyi ezmez."""
    with connect() as c:
        cur = c.execute("SELECT as_of FROM accounts WHERE name = ?", (name,)).fetchone()
        if cur and cur["as_of"] and source == "dokum" and cur["as_of"] > as_of:
            return
        anchor = c.execute("SELECT COALESCE(MAX(id), 0) FROM entries").fetchone()[0]
        c.execute("""INSERT INTO accounts (name, kind, balance, as_of, source, anchor_entry_id, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                     ON CONFLICT(name) DO UPDATE SET balance = excluded.balance, as_of = excluded.as_of,
                     source = excluded.source, anchor_entry_id = excluded.anchor_entry_id, updated_at = CURRENT_TIMESTAMP""",
                  (name, kind, round(balance, 2), as_of, source, anchor))


def ensure_account(name: str, kind: str = "banka") -> int:
    """Hesap yoksa bakiyesi bilinmeyen (as_of boş) olarak aç; id döner."""
    with connect() as c:
        r = c.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
        if r:
            return r["id"]
        return c.execute("INSERT INTO accounts (name, kind, balance, as_of, source) VALUES (?, ?, 0, NULL, 'dokum')",
                         (name, kind)).lastrowid


def _account_balance(c, a: dict) -> float | None:
    """Çapa + çapadan sonraki hareketler (elle girilen, e-posta bildirimi, borç hareketleri). Bakiyesi hiç girilmemiş: None."""
    if not a["as_of"]:
        return None
    flows = c.execute("""SELECT COALESCE(SUM(CASE WHEN kind = 'gelir' THEN amount ELSE -amount END), 0) FROM entries
                         WHERE account_id = ? AND skipped = 0 AND (entry_date > ? OR (entry_date = ? AND id > ?))""",
                      (a["id"], a["as_of"], a["as_of"], a["anchor_entry_id"] or 0)).fetchone()[0]
    debt = 0.0
    for d in debt_rows():
        if d.get("account_id") == a["id"] and d["date"] > a["as_of"] and d["step"] != "vazgecildi":
            debt += d["flow"] * d["amount"]
    return round(a["balance"] + flows + debt, 2)


def list_accounts() -> list[dict]:
    """Banka / nakit / diğer hesaplar (ön ödemeli kartlar ayrı: list_wallets). current: bugünkü tahmini bakiye."""
    with connect() as c:
        accs = rows(c.execute("SELECT * FROM accounts WHERE kind <> 'kart' ORDER BY kind, name"))
        for a in accs:
            a["current"] = _account_balance(c, a)
    return accs


def update_account(acc_id: int, data: dict) -> bool:
    data = {k: v for k, v in data.items() if k in ("name", "kind", "balance", "as_of")}
    if not data:
        return False
    data["source"] = "elle"
    with connect() as c:
        if "balance" in data:  # yeni çapa: bu andan sonraki hareketler üstüne eklenir
            data["anchor_entry_id"] = c.execute("SELECT COALESCE(MAX(id), 0) FROM entries").fetchone()[0]
        return c.execute(f"UPDATE accounts SET {', '.join(f'{k} = ?' for k in data)}, updated_at = CURRENT_TIMESTAMP "
                         "WHERE id = ?", (*data.values(), acc_id)).rowcount > 0


def delete_account(acc_id: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM accounts WHERE id = ?", (acc_id,)).rowcount > 0


# ---------------------------------------------------------------- fiyat takibi (fiş satırları)

def product_key(name: str) -> str:
    """'Latte 16 Oz' / 'LATTE 16OZ' → 'LATTE 16 OZ'. Fiyat/adet ekleri ve noktalama atılır."""
    import re as _re

    from app.textnorm import normalize

    n = normalize(name)
    n = _re.sub(r"(\d)([A-Z])", r"\1 \2", n)
    n = _re.sub(r"[^A-Z0-9 ]", " ", n)
    n = _re.sub(r"\b(ADT|ADET|X\d+|\d+X)\b", " ", n)
    return " ".join(n.split())[:60]


def save_receipt_items(entry_id: int, items: list[dict], merchant: str, on: str, total: float | None = None,
                       branch: str | None = None) -> int:
    """Fiş satırlarını sakla. Tek bir satırın fiyatı okunamadıysa ve toplam belliyse: toplam − diğerleri (tahmini)."""
    good = [it for it in items if (it.get("name") or "").strip() and product_key(it.get("product") or it["name"])]
    missing = [it for it in good if not isinstance(it.get("amount"), (int, float)) or it["amount"] <= 0]
    known_sum = sum(it["amount"] for it in good if it not in missing)
    inferred = None
    if len(missing) == 1 and total and total - known_sum > 0.009:
        inferred = round(total - known_sum, 2)
    n = 0
    with connect() as c:
        c.execute("DELETE FROM receipt_items WHERE entry_id = ?", (entry_id,))
        for it in good:
            qty = it.get("qty") or 1
            amt, known = it.get("amount"), 1
            if it in missing:
                amt, known = (inferred, 2) if inferred else (0.0, 0)
            product = (it.get("product") or "").strip() or None
            c.execute("INSERT INTO receipt_items (entry_id, purchased_at, merchant, branch, name, product, brand, product_key, qty, "
                      "unit, amount, unit_price, price_known) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (entry_id, on, (merchant or "").strip()[:60], (branch or "").strip()[:60] or None, it["name"].strip()[:60],
                       product, (it.get("brand") or "").strip()[:40] or None, product_key(product or it["name"]), qty,
                       it.get("unit") or "adet", round(amt, 2), round(amt / qty, 2) if known else 0.0, known))
            n += 1
    return n


def _store_key(name: str | None) -> str:
    """Mağaza gruplaması tam ada göre: 'KAHVE DÜNYASI' = 'Kahve Dünyası' (ilk kelimeye göre değil)."""
    import re as _re

    from app.textnorm import normalize

    return _re.sub(r"[^A-Z0-9]", "", normalize(name or "")) or "?"


def products(since: str | None = None, store: str | None = None, brand: str | None = None) -> dict:
    """Fişlerden: ürünler (tek alınanlar dahil, mağaza mağaza son fiyat, en ucuz işaretli, ilk→son değişim),
    mağazalar (orada alınan ürünler) ve markalar (ürünleri, ortalama değişim). Filtre: tarihten beri / mağaza / marka."""
    with connect() as c:
        items = rows(c.execute("SELECT * FROM receipt_items ORDER BY purchased_at, id"))
    names: dict[str, str] = {}
    for it in items:  # gösterim adı: küçük harf içeren (fişteki BÜYÜK HARF yerine) son yazılış
        k, n = _store_key(it["merchant"]), (it["merchant"] or "?").strip()
        if k not in names or not n.isupper() or names[k].isupper():
            names[k] = n
        it["store_key"] = k
    brand_names = {}
    for it in items:
        if it["brand"]:
            brand_names[_store_key(it["brand"])] = it["brand"]
    all_stores = sorted({names[k] for k in names})
    all_brands = sorted(set(brand_names.values()))
    items = [it for it in items if (not since or it["purchased_at"] >= since)
             and (not store or it["store_key"] == _store_key(store))
             and (not brand or _store_key(it["brand"]) == _store_key(brand))]

    def summarize(xs: list[dict]) -> dict:
        priced = [x for x in xs if x["price_known"]]
        last = xs[-1]
        per_store: dict[str, dict] = {}
        for x in priced:
            per_store[x["store_key"]] = {"store": names.get(x["store_key"], x["merchant"]), "branch": x["branch"],
                                         "price": x["unit_price"], "date": x["purchased_at"], "estimated": x["price_known"] == 2}
        st = sorted(per_store.values(), key=lambda s_: s_["price"])
        if len(st) > 1:
            st[0]["cheapest"] = True
        change = None
        if len(priced) >= 2 and priced[0]["unit_price"]:
            change = round((priced[-1]["unit_price"] - priced[0]["unit_price"]) / priced[0]["unit_price"] * 100, 1)
        return {"key": last["product_key"], "product": next((x["product"] for x in reversed(xs) if x["product"]), last["name"]),
                "brand": next((x["brand"] for x in reversed(xs) if x["brand"]), None),
                "unit": last["unit"], "count": len(xs), "last_date": last["purchased_at"],
                "last_price": priced[-1]["unit_price"] if priced else None,
                "last_estimated": bool(priced) and priced[-1]["price_known"] == 2,
                "first_price": priced[0]["unit_price"] if priced else None,
                "first_date": priced[0]["purchased_at"] if priced else None, "change_pct": change, "stores": st,
                "history": [{"date": x["purchased_at"], "price": x["unit_price"] if x["price_known"] else None,
                             "store": names.get(x["store_key"], x["merchant"]), "branch": x["branch"], "qty": x["qty"],
                             "name": x["name"]} for x in xs]}

    by: dict[str, list[dict]] = {}
    for it in items:
        by.setdefault(it["product_key"], []).append(it)
    prods = sorted((summarize(xs) for xs in by.values()), key=lambda x: x["last_date"], reverse=True)

    stores_out = []
    for sk in {it["store_key"] for it in items}:
        xs = [it for it in items if it["store_key"] == sk]
        per_p: dict[str, list[dict]] = {}
        for x in xs:
            per_p.setdefault(x["product_key"], []).append(x)
        stores_out.append({"store": names.get(sk, sk), "visits": len({x["entry_id"] for x in xs}),
                           "branches": sorted({x["branch"] for x in xs if x["branch"]}),
                           "last_date": max(x["purchased_at"] for x in xs),
                           "products": sorted((summarize(v) for v in per_p.values()), key=lambda x: x["last_date"], reverse=True)})
    stores_out.sort(key=lambda x: x["last_date"], reverse=True)

    brands_out = []
    for bk in {_store_key(it["brand"]) for it in items if it["brand"]}:
        bp = [p for p in prods if p["brand"] and _store_key(p["brand"]) == bk]
        ch = [p["change_pct"] for p in bp if p["change_pct"] is not None]
        brands_out.append({"brand": brand_names.get(bk, bk), "products": bp,
                           "avg_change": round(sum(ch) / len(ch), 1) if ch else None})
    brands_out.sort(key=lambda x: (-len(x["products"]), x["brand"]))
    return {"products": prods, "stores": stores_out, "brands": brands_out, "all_stores": all_stores, "all_brands": all_brands}


def price_history(min_count: int = 2) -> list[dict]:
    """En az iki fiyatı bilinen ürünler, değişime göre (Özet uyarısı için)."""
    return sorted([p for p in products()["products"] if p["change_pct"] is not None], key=lambda x: -abs(x["change_pct"]))


# ---------------------------------------------------------------- birikim hedefleri

def list_targets() -> list[dict]:
    with connect() as c:
        return rows(c.execute("SELECT * FROM saving_targets ORDER BY deadline"))


def create_target(name: str, amount: float, deadline: str, start_month: str, already: float = 0) -> int:
    with connect() as c:
        return c.execute("INSERT INTO saving_targets (name, amount, deadline, start_month, already) VALUES (?, ?, ?, ?, ?)",
                         (name.strip(), amount, deadline, start_month, already)).lastrowid


# ---------------------------------------------------------------- kumbaralar

def _next_month(m: str) -> str:
    y, mo = map(int, m.split("-"))
    return f"{y + 1:04d}-01" if mo == 12 else f"{y:04d}-{mo + 1:02d}"


def _auto_start(today: date, day: int) -> str:
    """İlk otomatik ayırmadan önceki ay: bu ayın günü geçtiyse bu ay, geçmediyse geçen ay (yani bu ay başlar)."""
    if today.day >= day:
        return today.strftime("%Y-%m")
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def _apply_auto_jar(c, j: dict, today: date) -> None:
    """Otomatik aylık ayırma: günü gelmiş her ay için bir kez (istek anında; zamanlayıcı gerekmez)."""
    if not j["monthly"] or not j["auto_day"] or j["closed_at"] or not j["last_auto"]:
        return
    m, last = _next_month(j["last_auto"]), None
    while True:
        d = date(*map(int, m.split("-")), j["auto_day"])
        if d > today:
            break
        c.execute("INSERT INTO jar_moves (jar_id, move_date, amount, note, auto) VALUES (?, ?, ?, 'Otomatik', 1)",
                  (j["id"], d.isoformat(), j["monthly"]))
        last, m = m, _next_month(m)
    if last:
        c.execute("UPDATE jars SET last_auto = ? WHERE id = ?", (last, j["id"]))


def _targets_to_jars(c) -> None:
    """Birikim hedefleri kumbaraya taşındı (bir kez): ad, tutar, son tarih; "başta ayrılan" başlangıç ayırması olur.
    Eski tablo yerinde kalır (geri dönüş için)."""
    if c.execute("SELECT 1 FROM settings WHERE key = 'targets_migrated'").fetchone():
        return
    for t in rows(c.execute("SELECT * FROM saving_targets ORDER BY id")):
        jid = c.execute("INSERT INTO jars (name, goal, deadline, created_at) VALUES (?, ?, ?, ?)",
                        (t["name"], t["amount"], t["deadline"], t["created_at"])).lastrowid
        if t["already"]:
            c.execute("INSERT INTO jar_moves (jar_id, move_date, amount, note) VALUES (?, ?, ?, 'Hedeften aktarıldı')",
                      (jid, (t["created_at"] or date.today().isoformat())[:10], t["already"]))
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('targets_migrated', '1')")


def list_jars(today: date | None = None, include_closed: bool = True) -> list[dict]:
    today = today or date.today()
    with connect() as c:
        _targets_to_jars(c)
        for j in rows(c.execute("SELECT * FROM jars WHERE closed_at IS NULL")):
            _apply_auto_jar(c, j, today)
        js = rows(c.execute("SELECT j.*, a.name AS account_name FROM jars j LEFT JOIN accounts a ON a.id = j.account_id "
                            "ORDER BY j.closed_at IS NOT NULL, COALESCE(j.deadline, '9999'), j.id"))
        for j in js:
            j["moves"] = rows(c.execute("SELECT * FROM jar_moves WHERE jar_id = ? ORDER BY move_date DESC, id DESC", (j["id"],)))
            j["balance"] = round(sum(m["amount"] for m in j["moves"]), 2)
    return [j for j in js if include_closed or not j["closed_at"]]


def jar_reserved(today: date | None = None) -> dict:
    """Açık kumbaralarda ayrılan toplam ve hesap bazında dağılım."""
    by_acc: dict[int | None, float] = {}
    for j in list_jars(today, include_closed=False):
        by_acc[j["account_id"]] = round(by_acc.get(j["account_id"], 0) + j["balance"], 2)
    return {"total": round(sum(by_acc.values()), 2), "by_account": by_acc}


JAR_FIELDS = ("name", "goal", "deadline", "account_id", "monthly", "auto_day", "note", "closed_at")


def create_jar(data: dict, initial: float = 0, today: date | None = None) -> int:
    today = today or date.today()
    data = {k: v for k, v in data.items() if k in JAR_FIELDS}
    with connect() as c:
        jid = c.execute(f"INSERT INTO jars ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                        tuple(data.values())).lastrowid
        if initial:
            c.execute("INSERT INTO jar_moves (jar_id, move_date, amount, note) VALUES (?, ?, ?, 'Başlangıç')",
                      (jid, today.isoformat(), initial))
        if data.get("monthly") and data.get("auto_day"):
            c.execute("UPDATE jars SET last_auto = ? WHERE id = ?", (_auto_start(today, data["auto_day"]), jid))
    return jid


def update_jar(jid: int, data: dict) -> bool:
    data = {k: v for k, v in data.items() if k in JAR_FIELDS}
    if not data:
        return False
    if data.get("auto_day"):  # otomatik ayırma yeniden kurulunca geçmiş aylar doldurulmaz
        data["last_auto"] = _auto_start(date.today(), data["auto_day"])
    with connect() as c:
        return c.execute(f"UPDATE jars SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?",
                         (*data.values(), jid)).rowcount > 0


def delete_jar(jid: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM jars WHERE id = ?", (jid,)).rowcount > 0


def add_jar_move(jid: int, amount: float, move_date: str, note: str | None = None) -> int:
    """amount > 0 yatır, < 0 çek. Kumbarada olandan fazlası çekilemez."""
    with connect() as c:
        j = c.execute("SELECT closed_at FROM jars WHERE id = ?", (jid,)).fetchone()
        if not j:
            raise LookupError("Kumbara yok")
        if j["closed_at"]:
            raise ValueError("Kumbara kapalı; önce yeniden aç")
        bal = c.execute("SELECT COALESCE(SUM(amount), 0) FROM jar_moves WHERE jar_id = ?", (jid,)).fetchone()[0]
        if amount < 0 and bal + amount < -0.005:
            raise ValueError(f"Kumbarada {bal:.2f} var; daha fazlası çekilemez")
        return c.execute("INSERT INTO jar_moves (jar_id, move_date, amount, note) VALUES (?, ?, ?, ?)",
                         (jid, move_date, amount, (note or "").strip() or None)).lastrowid


def delete_jar_move(mid: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM jar_moves WHERE id = ?", (mid,)).rowcount > 0


def delete_target(tid: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM saving_targets WHERE id = ?", (tid,)).rowcount > 0


# ---------------------------------------------------------------- ön ödemeli kartlar (yemek / hediye / giyim)

WALLET_INCOME_CATEGORY = "Yan Haklar"


def _wallet_balance(c, w: dict) -> float:
    """Çapa (elle girilen/düzeltilen bakiye; o anki son kayıt no + tarih) + sonrasında kaydedilen yükleme − harcama.
    Çapadan önceki tarihli kayıtlar (ör. bu ayın 1'indeki yükleme, girilen bakiyenin içinde) iki kez sayılmaz."""
    r = c.execute("""SELECT COALESCE(SUM(CASE WHEN kind = 'gelir' THEN amount ELSE -amount END), 0) FROM entries
                     WHERE wallet_id = ? AND skipped = 0 AND entry_date >= ? AND id > ?""",
                  (w["id"], w["as_of"] or "0000", w["anchor_entry_id"] or 0)).fetchone()[0]
    return round(w["balance"] + r, 2)


def list_wallets() -> list[dict]:
    with connect() as c:
        ws = rows(c.execute("SELECT * FROM accounts WHERE kind = 'kart' ORDER BY card_type, name"))
        for w in ws:
            w["current"] = _wallet_balance(c, w)
            rec = c.execute("SELECT * FROM recurring WHERE wallet_id = ? ORDER BY id DESC LIMIT 1", (w["id"],)).fetchone()
            w["schedule"] = dict(rec) if rec else None
            w["recent"] = rows(c.execute("SELECT id, entry_date, description, amount, kind FROM entries WHERE wallet_id = ? "
                                         "ORDER BY entry_date DESC, id DESC LIMIT 8", (w["id"],)))
    return ws


def get_wallet(wid: int) -> dict | None:
    return next((w for w in list_wallets() if w["id"] == wid), None)


def create_wallet(name: str, card_type: str, balance: float, expires_at: str | None = None) -> int:
    from datetime import date as _date

    with connect() as c:
        if c.execute("SELECT 1 FROM accounts WHERE name = ?", (name.strip(),)).fetchone():
            raise ValueError("Bu adla bir hesap/kart zaten var")
        anchor = c.execute("SELECT COALESCE(MAX(id), 0) FROM entries").fetchone()[0]
        return c.execute("INSERT INTO accounts (name, kind, card_type, balance, as_of, source, expires_at, anchor_entry_id) "
                         "VALUES (?, 'kart', ?, ?, ?, 'elle', ?, ?)",
                         (name.strip(), card_type, round(balance, 2), _date.today().isoformat(), expires_at, anchor)).lastrowid


def update_wallet(wid: int, data: dict) -> bool:
    """balance verilirse bugünden itibaren yeni çapa (uygulamadaki gerçek bakiyeyle düzeltme)."""
    from datetime import date as _date

    sets, vals = [], []
    for k in ("name", "card_type", "expires_at"):
        if k in data:
            sets.append(f"{k} = ?")
            vals.append(data[k])
    if not sets and data.get("balance") is None:
        return False
    with connect() as c:
        if data.get("balance") is not None:
            sets += ["balance = ?", "as_of = ?", "updated_at = CURRENT_TIMESTAMP", "anchor_entry_id = ?"]
            vals += [round(data["balance"], 2), _date.today().isoformat(),
                     c.execute("SELECT COALESCE(MAX(id), 0) FROM entries").fetchone()[0]]
        return c.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ? AND kind = 'kart'", (*vals, wid)).rowcount > 0


def delete_wallet(wid: int) -> bool:
    """Kart silinir; geçmiş harcama/yüklemeler kayıtlarda kalır, aylık yükleme biter."""
    with connect() as c:
        c.execute("UPDATE entries SET wallet_id = NULL WHERE wallet_id = ?", (wid,))
        c.execute("UPDATE recurring SET end_month = COALESCE(end_month, start_month), wallet_id = NULL WHERE wallet_id = ?", (wid,))
        return c.execute("DELETE FROM accounts WHERE id = ? AND kind = 'kart'", (wid,)).rowcount > 0


def _shift_month(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def set_wallet_schedule(wid: int, amount: float, day: int, from_month: str, wallet_name: str) -> int:
    """Aylık yükleme = karta bağlı gelir sabit kalemi. Tutar değişirse eskisi bir önceki dönem biter, yenisi başlar."""
    with connect() as c:
        cur = c.execute("SELECT * FROM recurring WHERE wallet_id = ? AND (end_month IS NULL OR end_month >= ?) "
                        "ORDER BY id DESC LIMIT 1", (wid, from_month)).fetchone()
        if cur and cur["start_month"] >= from_month:  # aynı dönemden başlayan: güncelle
            c.execute("UPDATE recurring SET amount = ?, day_of_month = ?, end_month = NULL WHERE id = ?", (amount, day, cur["id"]))
            return cur["id"]
        if cur:
            c.execute("UPDATE recurring SET end_month = ? WHERE id = ?", (_shift_month(from_month, -1), cur["id"]))
        return c.execute("INSERT INTO recurring (description, amount, kind, category, day_of_month, start_month, wallet_id) "
                         "VALUES (?, ?, 'gelir', ?, ?, ?, ?)",
                         (f"{wallet_name} yüklemesi", amount, WALLET_INCOME_CATEGORY, day, from_month, wid)).lastrowid


def stop_wallet_schedule(wid: int, last_month: str | None) -> bool:
    """last_month: son yüklemenin dönemi ("bu ay son" = bu dönem, "durdur" = önceki dönem); None = devam ettir."""
    with connect() as c:
        rec = c.execute("SELECT * FROM recurring WHERE wallet_id = ? ORDER BY id DESC LIMIT 1", (wid,)).fetchone()
        if not rec:
            return False
        if last_month is not None and last_month < rec["start_month"]:
            last_month = _shift_month(rec["start_month"], -1)  # hiç yüklenmeden durduruldu
        c.execute("UPDATE recurring SET end_month = ? WHERE id = ?", (last_month, rec["id"]))
        return True


def recurring_done(rid: int, month: str) -> bool:
    with connect() as c:
        return bool(c.execute("SELECT 1 FROM entries WHERE recurring_id = ? AND month = ?", (rid, month)).fetchone())


def auto_load_wallets(today) -> int:
    """Kartlara aylık yükleme günü geldiyse kendiliğinden gerçekleşir (onay beklemez)."""
    n = 0
    with connect() as c:
        recs = rows(c.execute("SELECT * FROM recurring WHERE wallet_id IS NOT NULL"))
    month = period_of(today)
    for r in recs:
        for m in sorted({_shift_month(month, -1), month}):  # bir önceki dönem kaçırılmış olabilir
            if m < r["start_month"] or (r["end_month"] and m > r["end_month"]):
                continue
            when = occurrence_date(m, day_at(r, m))
            if when > today.isoformat():
                continue
            with connect() as c:
                if c.execute("SELECT 1 FROM entries WHERE recurring_id = ? AND month = ?", (r["id"], m)).fetchone():
                    continue
            create_entry({"entry_date": when, "month": m, "description": r["description"], "amount": planned_amount(r, m),
                          "kind": "gelir", "category": r["category"], "recurring_id": r["id"], "skipped": 0,
                          "wallet_id": r["wallet_id"], "note": "Otomatik yükleme"})
            n += 1
    return n


def imported_bank_names() -> list[str]:
    """Hesap dökümü yüklenmiş bankalar (bakiyesi kaydedilmemiş olsa da): virman ve kart ödemesi kayıtlarından."""
    with connect() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT account FROM own_transfers WHERE COALESCE(account, '') <> '' "
            "UNION SELECT DISTINCT account FROM card_payments WHERE COALESCE(account, '') <> ''")]


def set_debt_payment_account(payment_id: int, account_id: int | None) -> bool:
    with connect() as c:
        return c.execute("UPDATE debt_payments SET account_id = ? WHERE id = ?", (account_id, payment_id)).rowcount > 0


def entry_to_opening_balance(entry_id: int, account_id: int) -> str:
    """Yanlışlıkla gelir girilmiş başlangıç bakiyesi → hesabın o tarihteki bakiyesi; gelir kaydı silinir.
    Hesabın daha yeni bir çapası (ör. döküm bakiyesi) varsa o doğrudur: kayıt yalnızca silinir."""
    with connect() as c:
        e = c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        a = c.execute("SELECT * FROM accounts WHERE id = ? AND kind <> 'kart'", (account_id,)).fetchone()
        if not e or not a or e["kind"] != "gelir":
            raise ValueError("Gelir kaydı ya da hesap bulunamadı")
        if a["as_of"] and a["as_of"] > e["entry_date"]:
            c.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            return f"{a['name']} bakiyesi {a['as_of']} tarihli daha yeni bilgiden geliyor; kayıt gelirden çıkarıldı"
        same = a["as_of"] == e["entry_date"]
        c.execute("UPDATE accounts SET balance = ?, as_of = ?, anchor_entry_id = 0, source = 'elle', updated_at = CURRENT_TIMESTAMP "
                  "WHERE id = ?", (round((a["balance"] if same else 0) + e["amount"], 2), e["entry_date"], account_id))
        c.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return f"{a['name']} başlangıç bakiyesi {e['entry_date']} itibarıyla kaydedildi; gelirden çıkarıldı"
