"""Sistem veritabanı: kullanıcılar, davetler, yapay zekâ kullanımı, sistem geneli ayarlar.

Her kullanıcının finans verisi kendi SQLite dosyasındadır (data/users/<id>/ekstre.db); yönetici (ilk kullanıcı)
eski tek kullanıcılı kurulumun data/ekstre.db dosyasını kullanır. Bu modül hiçbir finans verisi tutmaz —
yönetici kullanıcıları açıp kapatabilir ama verilerini göremez.
"""
import logging
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date

SYSTEM_DB = os.environ.get("HESAPKITAP_SYSTEM_DB",
                           os.path.join(os.path.dirname(__file__), "..", "data", "system.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT,                 -- NULL: şifre ortam değişkeninde (APP_PASSWORD, sadece yönetici)
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    db_file TEXT,                       -- NULL: ana veritabanı (data/ekstre.db, yönetici)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_login TEXT,
    disabled INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    ai_quota INTEGER,                   -- NULL: varsayılan kota
    session_version INTEGER NOT NULL DEFAULT 1  -- artınca o kullanıcının açık oturumları düşer
);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT,
    used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS ai_usage (     -- kendi sunucusu: yapay zekâ çağrısı; hazır sürüm: okunan belge (app/doc_reads.py)
    user_id INTEGER NOT NULL,
    month TEXT NOT NULL,                -- YYYY-MM (takvim ayı)
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, month)
);
CREATE TABLE IF NOT EXISTS doc_reads (    -- hazır sürüm: sayılmış belgeler; aynı içerik aynı defterde bir kez sayılır
    book_id INTEGER NOT NULL,           -- defter sahibi (ortak defterde sahip)
    hash TEXT NOT NULL,                 -- sha256(dosya baytları ya da "mail:<Message-ID>"); içerik saklanmaz
    month TEXT NOT NULL,
    PRIMARY KEY (book_id, hash)
);
CREATE TABLE IF NOT EXISTS read_usage (   -- hazır sürüm: aylık haktan düşen okuma, tür başı (belge | fis); app/doc_reads.py
    user_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    kind TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, month, kind)
);
CREATE TABLE IF NOT EXISTS read_daily (   -- hazır sürüm: günlük okuma hakkından düşen (tek sayaç, Türkiye günü); app/doc_reads.py
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,                  -- YYYY-MM-DD (Europe/Istanbul)
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS read_unreadable (  -- hazır sürüm: okunamayan fotoğraf denemesi, kişi başı Türkiye günü
    user_id INTEGER NOT NULL,           -- (ilk doc_reads.FREE_UNREADABLE_PER_DAY ücretsiz, sonrası 1 hak)
    day TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS read_book_daily (  -- hazır sürüm: ortak defterin günlük haktan düşen toplamı (aile tavanı)
    book_id INTEGER NOT NULL,           -- defter sahibi (doc_reads._book); üyenin "Benim" defteri sayılmaz
    day TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, day)
);
CREATE TABLE IF NOT EXISTS read_reserve (  -- hazır sürüm: kontrolden geçmiş, sayımı bekleyen okuma (doc_reads.check);
    id INTEGER PRIMARY KEY,             -- eşzamanlı istekler aynı kalan hakkı iki kez kullanamasın. İstek bitince silinir
    user_id INTEGER NOT NULL,
    family INTEGER,                     -- aile tavanının defteri (doc_reads._family) ya da NULL
    hash TEXT,                          -- doc_reads._key(anahtar) ya da NULL (anahtarsız kontrol)
    n INTEGER NOT NULL,
    created_at REAL NOT NULL            -- unix zamanı; RESERVE_TTL'den eskisi (çöken süreç) yok sayılır
);
CREATE TABLE IF NOT EXISTS doc_cache (    -- hazır sürüm: okunmuş belgenin sonucu (app/doc_cache.py); 30 gün
    book_id INTEGER NOT NULL,           -- doc_reads._book: önbellek defter başı
    hash TEXT NOT NULL,                 -- sha256(dosya baytları); dosyanın kendisi saklanmaz
    kind TEXT NOT NULL,                 -- belge | fis | fatura | dekont | ekstre | dokum
    created_at INTEGER NOT NULL,        -- unix zamanı
    file TEXT,                          -- yanıttaki receipt_file (elle yeniden okuma önbelleği tazeler)
    result TEXT NOT NULL,               -- okuma sonucu (JSON)
    PRIMARY KEY (book_id, hash, kind)
);
CREATE INDEX IF NOT EXISTS doc_cache_age ON doc_cache(created_at);
CREATE TABLE IF NOT EXISTS read_split (   -- eski tek sayaçtan iki sayaca göç bir kez yapıldı (kullanıcı, ay)
    user_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    PRIMARY KEY (user_id, month)
);
CREATE TABLE IF NOT EXISTS read_credits ( -- ek okuma paketi bakiyesi (belge + fiş ortak); ay dönümünde silinmez
    user_id INTEGER PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS read_packs (   -- mağazadan alınan ek paketler; txn = mağaza işlem kimliği (idempotent)
    txn TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    product TEXT NOT NULL,
    amount INTEGER NOT NULL,
    at TEXT DEFAULT CURRENT_TIMESTAMP,
    refunded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ai_calls (     -- yapay zekâ maliyet ölçümü (app/ai_costs.py); içerik/metin YOK, yalnız sayılar
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    month TEXT NOT NULL,
    user_id INTEGER,                    -- NULL: kullanıcısız çağrı (kendi sunucusu yöneticisi, sistem)
    kind TEXT NOT NULL,                 -- fis | belge | dokum | kategori
    model TEXT,
    input INTEGER NOT NULL DEFAULT 0,
    output INTEGER NOT NULL DEFAULT 0,
    thoughts INTEGER NOT NULL DEFAULT 0,
    cached INTEGER NOT NULL DEFAULT 0   -- önbellekten okunan girdi token'ı (input'un içinde, indirimli fiyat)
);
CREATE INDEX IF NOT EXISTS ai_calls_month ON ai_calls(month);
CREATE TABLE IF NOT EXISTS ai_restrictions (  -- yöneticinin kötüye kullanım kısıtlaması (app/ai_restrictions.py)
    user_id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('no_ai', 'cap')),  -- no_ai: yapay zekâ kapalı (yerel okuma açık); cap: günlük üst sınır
    daily_cap INTEGER,                  -- günde en çok okunan belge (no_ai'de isteğe bağlı)
    note TEXT,                          -- yöneticinin notu (kullanıcıya gösterilmez)
    at TEXT NOT NULL,
    by INTEGER                          -- kısıtlayan yönetici
);
CREATE TABLE IF NOT EXISTS ai_restriction_log (  -- kısıtlama işlem kaydı: koyma / değiştirme / kaldırma
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,               -- set | remove
    mode TEXT, daily_cap INTEGER, note TEXT,
    by INTEGER
);
CREATE TABLE IF NOT EXISTS ai_restriction_days (  -- günlük okunan belge (kısıtlamanın günlük üst sınırı için); 7 gün tutulur
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,                  -- YYYY-MM-DD
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS sys_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT,
    token_hash TEXT NOT NULL UNIQUE,      -- sadece özet; anahtarın kendisi bir kez gösterilir
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_used TEXT
);
CREATE TABLE IF NOT EXISTS tg_pairs (
    code TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS deleted_accounts (  -- silinen hesabın anonim kaydı (app/deletion.py); 1 yıl sonra düşer
    user_id INTEGER PRIMARY KEY,         -- id hiç yeniden verilmez; RevenueCat app_user_id ile aynı
    deleted_at TEXT NOT NULL,            -- silme isteği (ISO, UTC)
    delete_after TEXT,                   -- bekleme süresinin sonu; geri alınabilir pencere
    purged_at TEXT,                      -- veri kalıcı silindi (NULL: henüz bekliyor)
    reason TEXT,                         -- self | owner (sahip defteri sildi) | admin
    plan TEXT, plan_until TEXT, plan_source TEXT, plan_store TEXT, will_renew INTEGER,
    store_customer TEXT,                 -- RevenueCat app_user_id (= user_id)
    email_hash TEXT,                     -- HMAC-SHA256(sunucu anahtarı, küçük harf e-posta); düz e-posta yok
    notes TEXT                           -- JSON liste: silme sonrası mağaza olayları, iptal denemeleri
);
CREATE INDEX IF NOT EXISTS deleted_accounts_email ON deleted_accounts(email_hash);
CREATE TABLE IF NOT EXISTS push_devices (   -- telefona bildirim (app/push.py): cihaz belgesi (FCM / APNs)
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL,              -- ios | android
    env TEXT,                            -- iOS: production | sandbox
    app_version TEXT,
    created TEXT,
    last_seen TEXT
);
CREATE INDEX IF NOT EXISTS push_devices_user ON push_devices(user_id);
CREATE TABLE IF NOT EXISTS push_prefs (     -- kişinin bildirim tercihleri (JSON: types, quiet, digest)
    user_id INTEGER PRIMARY KEY,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_sent (      -- tekrar yok: (kullanıcı, bildirim kimliği) bir kez; 60 gün sonra düşer
    user_id INTEGER NOT NULL,
    id TEXT NOT NULL,
    at TEXT NOT NULL,                    -- YYYY-MM-DD (Türkiye günü)
    PRIMARY KEY (user_id, id)
);
CREATE TABLE IF NOT EXISTS push_queue (     -- gönderilecekler (sessiz saat bitince / arka planda)
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    payload TEXT NOT NULL,               -- JSON {title, body, route, kind, id}
    not_before REAL NOT NULL,            -- unix zamanı
    created TEXT,
    tries INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS feedback (       -- uygulama içi "Sorun bildir" (app/feedback.py); yönetici görür
    id INTEGER PRIMARY KEY,
    user_id INTEGER,                     -- oturumsuz (giriş ekranı) ve demoda NULL
    created_at TEXT NOT NULL,            -- UTC, YYYY-MM-DD HH:MM:SS
    message TEXT NOT NULL,
    context TEXT,                        -- JSON {route, app_version, platform, error, code, book}
    status TEXT NOT NULL DEFAULT 'yeni', -- yeni | okundu | cozuldu
    admin_note TEXT,
    shots TEXT NOT NULL DEFAULT '[]',    -- JSON: depodaki ekran görüntüsü adları (main/feedback/<ad>)
    client_key TEXT,                     -- bağlantının tuzlu kısa özeti (yalnız saatlik sınır; adres saklanmaz)
    demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS product_groups (  -- ürün adı → markadan bağımsız grup (app/product_groups.py); sonsuza dek
    name_norm TEXT PRIMARY KEY,          -- name_norm(ad): 'SUTAS GUNLUK SUT 1 L'
    group_key TEXT NOT NULL,             -- 'sut|1l'; '' = ürün değil (yapay zekâ: poşet, hizmet)
    label TEXT NOT NULL,                 -- 'Süt · 1 L'
    size_value REAL,
    size_unit TEXT,                      -- l | ml | kg | g | ad
    source TEXT NOT NULL CHECK (source IN ('rule', 'ai')),
    created_at TEXT NOT NULL,
    category TEXT                        -- kategori (product_groups.CATEGORIES): 'Süt ve Kahvaltılık'
);
CREATE INDEX IF NOT EXISTS product_groups_key ON product_groups(group_key);
CREATE TABLE IF NOT EXISTS product_group_queue (  -- kuralın çözemediği adlar: toplu yapay zekâ bekliyor (yalnız ad)
    name_norm TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    added_at TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS community_prices (  -- anonim topluluk fiyatı (app/community_prices.py): şube/kişi/kayıt yok
    id INTEGER PRIMARY KEY,
    chain TEXT NOT NULL,                 -- bilinen zincir (receipt_templates.CHAINS görünen adı)
    group_key TEXT NOT NULL,             -- ürün grubu (product_groups)
    name_norm TEXT NOT NULL,             -- yapay zekâ grubu öğrenince satır doğru gruba geçsin diye
    unit_price REAL NOT NULL,
    day TEXT NOT NULL,                   -- alım günü
    contributor TEXT NOT NULL            -- HMAC(günün tuzu, kullanıcı id): günler arası ve kullanıcıya bağlanamaz
);
CREATE INDEX IF NOT EXISTS community_prices_group ON community_prices(group_key, chain, day);
CREATE INDEX IF NOT EXISTS community_prices_name ON community_prices(name_norm);
CREATE TABLE IF NOT EXISTS official_prices (  -- resmî kaynak fiyatı (app/price_import.py, app/price_import_bq.py): kişi yok
    id INTEGER PRIMARY KEY,
    chain TEXT NOT NULL,                 -- zincirin görünen adı (receipt_templates.CHAINS)
    group_key TEXT NOT NULL,             -- ürün grubu (product_groups)
    title TEXT NOT NULL,                 -- kaynaktaki ürün adı
    brand TEXT,
    price REAL NOT NULL,
    unit_price REAL,                     -- kaynağın litre / kilo fiyatı (unitPriceValue)
    day TEXT NOT NULL,
    depot_id TEXT NOT NULL DEFAULT '',   -- kaynağın şube numarası (dosya içe aktarımı; konum / şube adı saklanmaz)
    discount INTEGER NOT NULL DEFAULT 0, -- kampanyalı fiyat
    region TEXT NOT NULL DEFAULT '',     -- kaynaktaki bölge ("İzmir", "DEFAULT_KADIKOY"); dosya içe aktarımında ''
    promotion TEXT,                      -- kampanya yazısı
    depots INTEGER,                      -- kaç şubede bu fiyat (BigQuery özeti)
    change_7d REAL,                      -- 7 / 30 gün önceki fiyata göre değişim, yüzde (+8.0 = %8 zam)
    change_30d REAL,
    UNIQUE (chain, region, depot_id, title, day)
);
CREATE INDEX IF NOT EXISTS official_prices_group ON official_prices(group_key, chain, day);
CREATE TABLE IF NOT EXISTS official_imports (  -- BigQuery içe aktarımı yapılan (gün, bölge): yeniden çekilmez
    run_date TEXT NOT NULL,
    region TEXT NOT NULL,
    rows INTEGER NOT NULL DEFAULT 0,
    products INTEGER NOT NULL DEFAULT 0,
    unmatched INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (run_date, region)
);
CREATE INDEX IF NOT EXISTS feedback_user ON feedback(user_id, created_at);
CREATE INDEX IF NOT EXISTS feedback_status ON feedback(status, id);
"""

DEFAULT_AI_QUOTA = 100


def _path() -> str:
    return SYSTEM_DB


_ready: set[str] = set()


@contextmanager
def connect():
    p = _path()
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if p not in _ready:  # şema (ve göçler) her dosya için bir kez kurulur
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()  # göç yazdıysa (yeniden gruplama) açık işlem kalmasın: iç içe bağlantı kilitlenmez
        _ready.add(p)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(c) -> None:
    # ortak defter: üye kendi kullanıcısıyla girer ama sahibin defterini açar
    for table in ("users", "invites"):
        if "member_of" not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN member_of INTEGER")
    # paket (barındırılan sürüm): free / premium / aile, bitiş (ISO, UTC), kaynak (revenuecat / elle)
    have = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    for col in ("plan", "plan_until", "plan_source"):
        if col not in have:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
    # ortak defter v2: üyenin yetkisi (viewer | editor | full); davette seçilir
    for table in ("users", "invites"):
        if "book_role" not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN book_role TEXT")
    # yumuşak silme (app/deletion.py): istek zamanı, kalıcı silinme zamanı, ortak defter sahibiyle birlikte silinen üye
    # abonelik: mağaza yenilemesi açık mı (CANCELLATION → 0), hangi mağaza (APP_STORE / PLAY_STORE)
    have = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    # deneme süresi (RevenueCat period_type TRIAL) ve mağaza ürünü (premium_monthly...): hak doldu yanıtı için
    for col, typ in (("deleted_at", "TEXT"), ("delete_after", "TEXT"), ("deleted_with", "INTEGER"),
                     ("plan_renews", "INTEGER"), ("plan_store", "TEXT"), ("plan_trial", "INTEGER"),
                     ("plan_product", "TEXT")):
        if col not in have:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
    # sayılmış belgenin türü (belge | fis); eski kayıtlarda NULL
    if "kind" not in {r[1] for r in c.execute("PRAGMA table_info(doc_reads)")}:
        c.execute("ALTER TABLE doc_reads ADD COLUMN kind TEXT")
    # yapay zekâ maliyet kaydı: önbellekli girdi token'ı (app/ai_costs.py)
    if "cached" not in {r[1] for r in c.execute("PRAGMA table_info(ai_calls)")}:
        c.execute("ALTER TABLE ai_calls ADD COLUMN cached INTEGER NOT NULL DEFAULT 0")
    if "category" not in {r[1] for r in c.execute("PRAGMA table_info(product_groups)")}:
        c.execute("ALTER TABLE product_groups ADD COLUMN category TEXT")
    # resmî fiyat: bölge (BigQuery il bazında), kampanya yazısı, şube sayısı, 7 / 30 günlük değişim. Tekillik bölgeyi de
    # içermeli; SQLite kısıtı değiştiremediği için tablo yeniden kurulur (eski satırlar bölge '' ile taşınır)
    if "region" not in {r[1] for r in c.execute("PRAGMA table_info(official_prices)")}:
        c.execute("ALTER TABLE official_prices RENAME TO official_prices_old")
        c.execute("DROP INDEX IF EXISTS official_prices_group")
        start = SCHEMA.index("CREATE TABLE IF NOT EXISTS official_prices (")
        c.executescript(SCHEMA[start:SCHEMA.index("CREATE TABLE IF NOT EXISTS official_imports")])
        c.execute("INSERT OR IGNORE INTO official_prices (chain, group_key, title, brand, price, unit_price, day, depot_id, "
                  "discount) SELECT chain, group_key, title, brand, price, unit_price, day, depot_id, discount "
                  "FROM official_prices_old")
        c.execute("DROP TABLE official_prices_old")
    c.execute("CREATE INDEX IF NOT EXISTS official_prices_region ON official_prices(region, day)")
    # ürün grubu kuralları değişti (app/product_groups.py GROUPS_VERSION): önbellek ve topluluk fiyatları bir kez
    from app import product_groups

    r = c.execute("SELECT value FROM sys_settings WHERE key = 'product_groups:version'").fetchone()
    if not r or int(r[0] or 0) < product_groups.GROUPS_VERSION:
        try:
            got = product_groups.regroup(c)
            if got["changed"] or got["queued"]:
                logging.getLogger("hesapkitap.product_groups").info("ürün grupları yenilendi: %s", got)
        except Exception:  # noqa: BLE001 - açılışı engellemesin; sonraki açılışta yeniden denenir
            logging.getLogger("hesapkitap.product_groups").exception("ürün grubu yeniden gruplama başarısız")
            return
        c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES ('product_groups:version', ?)",
                  (str(product_groups.GROUPS_VERSION),))


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        _migrate(c)


def get(key: str) -> str | None:
    with connect() as c:
        r = c.execute("SELECT value FROM sys_settings WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None


def put(key: str, value: str | None) -> None:
    with connect() as c:
        if value is None:
            c.execute("DELETE FROM sys_settings WHERE key = ?", (key,))
        else:
            c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES (?, ?)", (key, value))


# ---------------------------------------------------------------- kullanıcılar

def users() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY id")]


def user(uid: int) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(r) if r else None


def user_by_name(username: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
        return dict(r) if r else None


def has_users() -> bool:
    with connect() as c:
        return bool(c.execute("SELECT 1 FROM users LIMIT 1").fetchone())


def data_root() -> str:
    """Kullanıcı klasörlerinin kökü: sistem veritabanının yanındaki users/."""
    return os.path.join(os.path.dirname(os.path.abspath(_path())), "users")


def check_username(username: str) -> str:
    """Kullanıcı adı / e-posta biçimi; geçerliyse saklanacak hâli (e-posta küçük harf)."""
    name = (username or "").strip()
    if "@" in name:  # mobil uygulama ve hazır sürüm: e-posta ile kayıt (büyük/küçük harf fark etmez: COLLATE NOCASE)
        import re

        if len(name) > 120 or not re.fullmatch(r"[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,24}", name):
            raise ValueError("Geçerli bir e-posta yaz")
        return name.lower()
    if not (3 <= len(name) <= 32) or not all(ch.isalnum() or ch in "._-" for ch in name):
        raise ValueError("Kullanıcı adı 3–32 karakter; harf, rakam, nokta, alt çizgi ya da tire")
    return name


def create_user(username: str, password_hash: str | None, role: str = "user", main_db: bool = False,
                must_change: bool = False, member_of: int | None = None, book_role: str | None = None) -> dict:
    """main_db: yöneticinin eski tek kullanıcılı veritabanını kullanması (db_file NULL).
    member_of: ortak defter üyesi; kendi dosyası olmaz, o kullanıcının defterini açar."""
    name = check_username(username)
    with connect() as c:
        if c.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
            raise ValueError("Bu kullanıcı adı alınmış")
        # id hiçbir zaman yeniden verilmez (silinen son kullanıcının id'si de): klasör users/<id>/, erişim anahtarları ve
        # ortak defter davetleri id'ye bağlı; yeniden kullanım eski hesabın artığını yeni hesaba bağlayabilirdi.
        last = c.execute("SELECT value FROM sys_settings WHERE key = 'last_user_id'").fetchone()
        top = c.execute("SELECT COALESCE(MAX(id), 0) FROM users").fetchone()[0]
        uid = max(top, int(last[0]) if last and str(last[0]).isdigit() else 0) + 1
        c.execute("INSERT INTO users (id, username, password_hash, role, must_change_password, member_of, book_role) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (uid, name, password_hash, role, int(must_change), member_of, book_role if member_of else None))
        c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES ('last_user_id', ?)", (str(uid),))
        if not main_db and not member_of:
            c.execute("UPDATE users SET db_file = ? WHERE id = ?", (os.path.join(str(uid), "ekstre.db"), uid))
    return user(uid)


def user_db_path(u: dict) -> str | None:
    """Kullanıcının finans veritabanı dosyası; None: ana veritabanı (yönetici). Ortak defter üyesi: sahibinin defteri."""
    if u.get("member_of"):
        owner = user(u["member_of"])
        if owner and not owner.get("member_of"):
            return user_db_path(owner)
        return own_db_path(u)  # sahibi silinmiş: kendine ait defter
    return os.path.join(data_root(), u["db_file"]) if u.get("db_file") else None


def own_db_path(u: dict) -> str:
    """Ortak defter üyesinin kendine ait (özel) defteri: "Benim" seçiliyken açılır. Davetle yeni açılan üyede
    db_file yoktur (users/<id>/ekstre.db, boş başlar); var olan hesabıyla katılan üyede db_file kalır: kendi verisi
    "Benim" defteri olur (app/book_join.py)."""
    if u.get("db_file"):
        return os.path.join(data_root(), u["db_file"])
    return os.path.join(data_root(), str(u["id"]), "ekstre.db")


def set_membership(uid: int, member_of: int | None, book_role: str | None = None) -> None:
    """Ortak defter üyeliğini değiştir (katıl / ayrıl). Ayrılan üyenin kendi defteri db_file olarak bağlanır: "Benim"
    defteri tek başına hesabın defteri olur (davetle açılmış hesapta da users/<id>/ekstre.db)."""
    with connect() as c:
        if member_of:
            c.execute("UPDATE users SET member_of = ?, book_role = ? WHERE id = ?", (member_of, book_role or "editor", uid))
        else:
            c.execute("UPDATE users SET member_of = NULL, book_role = NULL, "
                      "db_file = COALESCE(db_file, ?) WHERE id = ?", (os.path.join(str(uid), "ekstre.db"), uid))


def members(owner_id: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users WHERE member_of = ? ORDER BY id", (owner_id,))]


def update_user(uid: int, **fields) -> None:
    allowed = {"username", "password_hash", "disabled", "last_login", "must_change_password", "ai_quota", "role",
               "plan", "plan_until", "plan_source", "book_role", "deleted_at", "delete_after", "deleted_with",
               "plan_renews", "plan_store", "plan_trial", "plan_product"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    with connect() as c:
        c.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?", (*data.values(), uid))


def bump_session(uid: int) -> None:
    """O kullanıcının tüm açık oturumlarını geçersiz kıl (şifre değişince, kapatılınca)."""
    with connect() as c:
        c.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (uid,))


def delete_user(uid: int) -> None:
    with connect() as c:
        gone = bool(c.execute("DELETE FROM users WHERE id = ? AND role <> 'admin'", (uid,)).rowcount)
        if gone:
            c.execute("DELETE FROM ai_usage WHERE user_id = ?", (uid,))
            for t in ("read_usage", "read_daily", "read_split", "read_credits", "read_unreadable", "ai_restrictions",
                      "ai_restriction_log", "ai_restriction_days", "push_devices", "push_prefs", "push_sent", "push_queue"):
                c.execute(f"DELETE FROM {t} WHERE user_id = ?", (uid,))
            c.execute("DELETE FROM doc_reads WHERE book_id = ?", (uid,))
            for t in ("doc_cache", "read_book_daily"):  # defterin okuma önbelleği ve aile günlük toplamı
                c.execute(f"DELETE FROM {t} WHERE book_id = ?", (uid,))
            c.execute("DELETE FROM api_tokens WHERE user_id = ?", (uid,))  # silinen hesabın anahtarı çalışmasın
            c.execute("DELETE FROM invites WHERE member_of = ? AND used_at IS NULL", (uid,))  # bekleyen ortak defter daveti
            c.execute("DELETE FROM tg_pairs WHERE user_id = ?", (uid,))
            # fiyat paylaşımı tercihi; topluluk fiyatı satırları kişiye bağlanamaz, kalır (app/community_prices.py)
            c.execute("DELETE FROM sys_settings WHERE key IN (?, ?)", (f"price_share:{uid}", f"region:{uid}"))
            for sql in ("DELETE FROM password_resets WHERE user_id = ?",   # tablolar ilk kullanımda kurulur (reset, inbound)
                        # gelen posta adresi: kod hiç yeniden verilmesin diye satır kalır, hesapla bağı kopar (D12)
                        "UPDATE inbound SET revoked = 1, user_id = 0 WHERE user_id = ?"):
                try:
                    c.execute(sql, (uid,))
                except sqlite3.OperationalError:
                    pass
    if gone:  # geri bildirimleri ve ekran görüntüleri (depo çağrısı bağlantı dışında)
        from app import feedback

        try:
            feedback.forget_user(uid)
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("hesapkitap.feedback").exception("geri bildirimler silinemedi")


def user_dirs(u: dict) -> list[str]:
    """Kullanıcıya ait klasörler: users/<id>/ (kendi defteri; ortak defter üyesinin "Benim" defteri de burada) ve
    db_file başka bir klasörü gösteriyorsa o. Kökün kendisi ya da _silinen asla dönmez."""
    root = os.path.abspath(data_root())
    cands = [os.path.join(root, str(int(u["id"])))]
    if u.get("db_file"):
        cands.append(os.path.join(root, os.path.dirname(u["db_file"])))
    out = []
    for p in map(os.path.abspath, cands):
        if p.startswith(root + os.sep) and os.path.basename(p) != "_silinen" and p not in out and os.path.isdir(p):
            out.append(p)
    return out


def user_prefixes(u: dict) -> list[str]:
    """Kullanıcının nesne deposundaki önekleri (app/storage.py): users/<id>/ ve db_file başka klasördeyse o."""
    import re

    out = [f"users/{int(u['id'])}/"]
    if u.get("db_file"):
        rel = os.path.dirname(u["db_file"]).replace(os.sep, "/")
        if re.fullmatch(r"[0-9A-Za-z_-]{1,64}(?:/[0-9A-Za-z_-]{1,64})*", rel) and rel.split("/")[0] != DELETED_DIR:
            out.append(f"users/{rel}/")
    return list(dict.fromkeys(out))


def _s3_files() -> bool:
    from app import storage

    return storage.s3_enabled()


def purge_user_data(u: dict) -> None:
    """Kullanıcı kendi hesabını sildi: klasörü (veritabanı, PDF'ler, faturalar, kendi günlük yedekleri) hemen ve kalıcı
    olarak silinir (KVKK); dosyalar nesne deposundaysa users/<id>/ öneki de. Sunucu yedeklerindeki kopyalar en geç
    30 gün içinde düşer (app/cloud_backup.py)."""
    import shutil

    for p in user_dirs(u):
        shutil.rmtree(p)
    if _s3_files():
        from app import storage

        for prefix in user_prefixes(u):
            storage.delete_prefix(prefix)


DELETED_DIR = "_silinen"


def archive_user_data(u: dict) -> str | None:
    """Silinen kullanıcının klasörünü users/_silinen/ altına taşı (geri dönüş için). purge_archived süresi dolanı siler."""
    import re
    import shutil
    import time

    dst = None
    safe = re.sub(r"[^A-Za-z0-9._@-]", "_", u["username"])[:80]  # e-posta adında "/" olabilir: yol dışına çıkmasın
    ts = int(time.time())
    for i, src in enumerate(user_dirs(u)):
        dst = os.path.join(data_root(), DELETED_DIR, f"{u['id']}-{safe}-{ts}" + (f"-{i}" if i else ""))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
    if _s3_files():  # nesneler deleted/<id>-<zaman>/ altına (kullanıcı adı anahtara girmez); 30 gün sonra silinir
        from app import storage

        for i, prefix in enumerate(user_prefixes(u)):
            storage.move_prefix(prefix, f"deleted/{int(u['id'])}-{ts}" + (f"-{i}" if i else "") + "/")
    return dst


def deleted_keep_days() -> int:
    """users/_silinen/ saklama süresi (gün). 0: kendiliğinden silinmez."""
    v = os.environ.get("HESAPKITAP_DELETED_KEEP_DAYS", "30")
    return int(v) if v.isdigit() else 30


def purge_archived(days: int | None = None, now: float | None = None) -> list[str]:
    """users/_silinen/ altında `days` günden eski klasörleri kalıcı sil. Ad: <id>-<kullanıcı>-<unix zamanı>[-n];
    zaman okunamazsa klasörün değişme zamanı esas alınır."""
    import re
    import shutil
    import time
    from datetime import datetime

    days = deleted_keep_days() if days is None else days
    base = os.path.join(data_root(), DELETED_DIR)
    if not days:
        return []
    if not os.path.isdir(base):
        return _purge_archived_objects(days, time.time() if now is None else now)
    now = time.time() if now is None else now
    removed = []
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        m = re.search(r"-(\d{9,11})(?:-\d+)?$", name)
        ts = int(m[1]) if m else os.path.getmtime(p)
        # takvim günüyle (yedeklerle aynı kural): taşındığı gün + days. gün çalışan temizlikte düşer
        if (datetime.fromtimestamp(now).date() - datetime.fromtimestamp(ts).date()).days >= days:
            shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
            removed.append(name)
    return removed + _purge_archived_objects(days, now)


def _purge_archived_objects(days: int, now: float) -> list[str]:
    """Nesne deposunda deleted/<id>-<unix zamanı>[-n]/ önekleri: aynı takvim günü kuralı."""
    import re
    from datetime import datetime

    if not days or not _s3_files():
        return []
    from app import storage

    groups = {k.split("/")[1] for k in storage.backend().list("deleted/") if k.count("/") >= 2}
    removed = []
    for g in sorted(groups):
        m = re.fullmatch(r"(\d+)-(\d{9,11})(?:-\d+)?", g)
        if m and (datetime.fromtimestamp(now).date() - datetime.fromtimestamp(int(m[2])).date()).days >= days:
            storage.delete_prefix(f"deleted/{g}/")
            removed.append(f"deleted/{g}/")
    return removed


# ---------------------------------------------------------------- davetler

def create_invite(note: str | None = None, days: int = 14, member_of: int | None = None, book_role: str | None = None) -> dict:
    """member_of: ortak defter daveti (kayıt olan o kullanıcının defterine üye olur); book_role: üyenin yetkisi."""
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    with connect() as c:
        c.execute("INSERT INTO invites (code, note, expires_at, member_of, book_role) VALUES (?, ?, date('now', ?), ?, ?)",
                  (code, (note or "").strip()[:60] or None, f"+{days} days", member_of, book_role if member_of else None))
        return dict(c.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone())


def invites() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT i.*, u.username AS used_by_name FROM invites i LEFT JOIN users u ON u.id = i.used_by "
            "ORDER BY i.created_at DESC")]


def invite(code: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM invites WHERE code = ?", (code.strip().upper(),)).fetchone()
        return dict(r) if r else None


def invite_status(code: str) -> tuple[dict | None, str]:
    """(davet, durum): durum ok | used | expired | unknown."""
    inv = invite(code or "")
    if not inv:
        return None, "unknown"
    if inv["used_at"]:
        return inv, "used"
    with connect() as c:
        expired = c.execute("SELECT ? < date('now')", (inv["expires_at"],)).fetchone()[0] if inv["expires_at"] else 0
    return inv, "expired" if expired else "ok"


def renew_invite(code: str) -> dict | None:
    """Kullanılmamış daveti yenile: eski kod geçersiz, aynı not/yetki/defterle yeni kod ve yeni süre."""
    inv = invite(code or "")
    if not inv or inv["used_at"]:
        return None
    with connect() as c:
        days = c.execute("SELECT MAX(1, CAST(julianday(?) - julianday(?) AS INTEGER))",
                         (inv["expires_at"] or "", (inv["created_at"] or "")[:10])).fetchone()[0] if inv["expires_at"] else 14
    delete_invite(inv["code"])
    return create_invite(inv["note"], int(days or 14), member_of=inv.get("member_of"), book_role=inv.get("book_role"))


def valid_invite(code: str) -> bool:
    with connect() as c:
        return bool(c.execute("SELECT 1 FROM invites WHERE code = ? AND used_at IS NULL AND "
                              "(expires_at IS NULL OR expires_at >= date('now'))", (code.strip().upper(),)).fetchone())


def use_invite(code: str, uid: int) -> bool:
    with connect() as c:
        return c.execute("UPDATE invites SET used_by = ?, used_at = CURRENT_TIMESTAMP WHERE code = ? AND used_at IS NULL "
                         "AND (expires_at IS NULL OR expires_at >= date('now'))", (uid, code.strip().upper())).rowcount > 0


def delete_invite(code: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM invites WHERE code = ? AND used_at IS NULL", ((code or "").strip().upper(),)).rowcount > 0


# ---------------------------------------------------------------- okuma kotası
# Kendi sunucusu: ortak Gemini anahtarıyla yapılan yapay zekâ çağrısı sayısı (receipt._count_usage).
# Hazır sürüm: okunan belge sayısı (app/doc_reads.py) — "belge okuma hakkı".

def ai_used(uid: int, month: str | None = None) -> int:
    month = month or date.today().strftime("%Y-%m")
    with connect() as c:
        r = c.execute("SELECT count FROM ai_usage WHERE user_id = ? AND month = ?", (uid, month)).fetchone()
        return r[0] if r else 0


def ai_used_pool(u: dict, month: str | None = None) -> int:
    """Ortak defterde okuma hakkı ailenin ortak havuzu: sahip + bütün üyeler (hazır sürümde belge, kendi sunucusunda
    yapay zekâ çağrısı)."""
    owner_id = u.get("member_of") or u["id"]
    ids = [owner_id] + [m["id"] for m in members(owner_id)]
    return sum(ai_used(i, month) for i in ids)


def ai_add(uid: int) -> None:
    month = date.today().strftime("%Y-%m")
    with connect() as c:
        c.execute("INSERT INTO ai_usage (user_id, month, count) VALUES (?, ?, 1) "
                  "ON CONFLICT(user_id, month) DO UPDATE SET count = count + 1", (uid, month))


def ai_quota(u: dict) -> int | None:
    """None: sınırsız (yönetici). Hazır sürümde paketin GÜNLÜK okuma hakkı (plans.DAILY_READS, plans.read_limit;
    kişiye özel ai_quota önce gelir); kendi sunucusunda aylık yapay zekâ çağrısı kotası (kişiye özel ya da varsayılan)."""
    if u["role"] == "admin":
        return None
    from app import plans

    if plans.hosted():
        return plans.ai_limit(u)
    if u.get("ai_quota") is not None:
        return u["ai_quota"]
    v = get("ai_default_quota")
    return int(v) if v and v.isdigit() else DEFAULT_AI_QUOTA


# ---------------------------------------------------------------- kişisel erişim anahtarları (Paylaş kısayolu)

def _token_hash(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def create_token(uid: int, name: str | None = None) -> str:
    token = "hk_" + secrets.token_urlsafe(32)
    with connect() as c:
        c.execute("INSERT INTO api_tokens (user_id, name, token_hash) VALUES (?, ?, ?)",
                  (uid, (name or "").strip()[:40] or None, _token_hash(token)))
    return token


def tokens(uid: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT id, name, created_at, last_used FROM api_tokens WHERE user_id = ? "
                                           "ORDER BY id DESC", (uid,))]


def delete_token(uid: int, tid: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM api_tokens WHERE id = ? AND user_id = ?", (tid, uid)).rowcount > 0


def token_owner(token: str) -> dict | None:
    """Anahtarın sahibi (kapatılmış kullanıcıysa None)."""
    if not token or not token.startswith("hk_"):
        return None
    with connect() as c:
        r = c.execute("SELECT user_id FROM api_tokens WHERE token_hash = ?", (_token_hash(token),)).fetchone()
        if not r:
            return None
        c.execute("UPDATE api_tokens SET last_used = CURRENT_TIMESTAMP WHERE token_hash = ?", (_token_hash(token),))
    u = user(r["user_id"])
    return u if u and not u["disabled"] and not u.get("deleted_at") else None  # silinmek üzere: API erişimi yok
