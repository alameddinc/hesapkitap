"""Defter birleştirme / taşıma: başka bir defteri (KAYNAK) bir kullanıcının defterine (HEDEF) temel yapar; hedefte
--since gününden beri eklenen kayıtlar bu temelin üstüne taşınır.

    python -m app.book_merge --source <kaynak ekstre.db> --source-files <kaynak dosya dizini|-> \\
        --target-user <kullanıcı adı> --since YYYY-MM-DD [--apply] [--json] [--skip-pre-since] \\
        [--prefer-target-edits] [--user-map 3:1,4:2] [--out <birleşik kopya.db>] [--force]

Varsayılan KURU ÇALIŞTIRMA: kaynak ve hedef veritabanlarının KOPYALARI üzerinde birleştirir; canlı veritabanına,
depoya (R2) ve sistem veritabanına hiçbir şey yazmaz (depoda yalnız "dosya var mı" bakılır). --apply: önce hedefin
yedeği (backups/pre-merge-<zaman>.db), sonra eksik dosyalar hedefin deposuna, sonra birleşik veritabanı geçici
dosyadan atomik olarak hedefin yerine; en sonda doğruluk denetimi (integrity.run).

Kurallar:
- Sonuç = kaynak defterin tam kopyası (şeması hedefin güncel şemasına göç ettirilir: db.init_db + modül şemaları)
  + hedefin kaynakta olmayan kayıtları. Hedeften taşınan satır yeni kimlik alır; ona bağlı satırlar (borç ödemeleri,
  bölmeler, fiş kalemleri, eşleşmeler, ekstre işlemleri, döküm belgesi, hesaplar…) yeni kimliklere yeniden bağlanır.
- Tekilleştirme (bire bir): aynı import_hash (hareketler, virmanlar, kart ödemeleri; tablo fark etmeksizin) → tek;
  aynı ekstre (file_hash ya da banka + kart + kesim) → tek; elle kayıt: aynı gün + tutar + tür + açıklama → tek;
  parmak izi farklı (eski biçim) ama gün + tutar + tür + açıklama + hesap aynı → tek. Benzer ama aynı olmayan
  (aynı gün/tutar başka açıklama ya da ±3 gün aynı açıklama) → ikisi de kalır, raporda "olası kopya".
- Eşleşen kayıtta kaynaktaki tutulur. Hedefteki düzenleme: kaynakta boş alanlar hedeften doldurulur, 'Kategorisiz'
  kategori hedefinkiyle, hedefte açılmış bayraklar (gizli, silindi, atlandı, elle kategori…) uygulanır; ikisinde de dolu
  ve farklı kullanıcı alanları (açıklama, kategori, not, kişi…) "çakışma" olarak raporlanır, --prefer-target-edits ile
  hedefinki uygulanır. Hesap bakiyesi: tarihi (as_of) yeni olan taraf.
- Ayarlar: hazır sürüme ait anahtarlar hedefte kalır (TARGET_KEYS), kendi-sunucusu anahtarları taşınmaz (SOURCE_DROP),
  defter verisi ayarları kaynaktan (listeler birleşir). Raporda yalnız anahtar adları (değer yok).
- Hedefte --since ÖNCESİ olup kaynakta olmayan kayıtlar da varsayılan taşınır ve ayrı UYARI bölümünde listelenir
  (--skip-pre-since: taşınmaz).
"""
import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import db

# ---------------------------------------------------------------- ayarlar

# hazır sürüme / hedef kullanıcıya / cihaza ait: hedefteki değer kalır, kaynaktaki atılır
TARGET_KEYS = {"modules", "onboarding_dismissed", "posta:senders", "storage:full_warned"}
TARGET_PREFIXES = ("consent:", "ui:", "reminders:state", "insights:dismissed", "integrity:", "digest_seen:", "book:rules", "receipt_reread:",
                   "budget:")
# kendi sunucusuna ait (IMAP, Telegram, uygulama şifresi, yedek, kendi yapay zekâ anahtarı, zamanlanmış iş damgaları):
# taşınmaz. Hazır sürümde bu anahtarlar zaten kullanılmaz (config.locked)
SOURCE_DROP_KEYS = {"telegram_chat_id", "telegram_chat_name", "telegram_pair", "telegram_offset", "auth:password_hash"}
SOURCE_DROP_PREFIXES = ("cfg:", "mail:", "job:")
# şema / veri göçü işaretleri: birleşik defterdeki (göç ettirilmiş kaynak) değer geçerli
MIGRATION_KEYS = {"rules_version", "item_rules_seeded", "targets_migrated", "files_indexed", "statements_backfilled"}
# JSON liste: iki tarafınki birleşir (kaynak önce)
LIST_KEYS = {"pdf_passwords", "bank_owner_names", "recurring_dismissed"}


def setting_class(key: str) -> str:
    if key in TARGET_KEYS or key.startswith(TARGET_PREFIXES):
        return "target"
    if key in SOURCE_DROP_KEYS or key.startswith(SOURCE_DROP_PREFIXES):
        return "drop"
    if key in MIGRATION_KEYS:
        return "migration"
    if key in LIST_KEYS:
        return "list"
    return "data"


# ---------------------------------------------------------------- tablolar

def _norm(s) -> str:
    from app.textnorm import normalize

    return normalize(str(s or ""))


def _amt(v):
    return None if v is None else round(float(v), 2)


@dataclass
class T:
    name: str
    pk: str | None = "id"          # None: doğal anahtarlı (birincil anahtar = keys[0] kolonları)
    keys: tuple = ()               # eşleşme anahtarları sırayla: kolon adları tuple'ı ya da fn(row, side) → tuple|None
    fks: dict = field(default_factory=dict)   # kolon → tablo
    required: tuple = ()           # bu bağ çözülemezse satır taşınmaz
    when: object = None            # since sınıfı: kolon adı / kolon listesi (ilk dolu) / ("parent", kolon)
    mode: str = "merge"            # merge | target | source | clear
    edit: tuple = ()               # ikisinde de dolu ve farklıysa çakışma
    flags: tuple = ()              # hedefte 1 ise uygulanır
    group: tuple = ()              # eşleşen ebeveynin kaynakta bu tabloda satırı varsa hedefinkiler taşınmaz
    accept: object = None          # fn(hedef, kaynak) → eşleşme kabul mü
    extra_parents: tuple = ()      # group'a ek: eşleşmeyen satır yalnız ebeveyni taşındıysa taşınır (ekstre işlemleri)
    label: tuple = ()              # rapor: (tarih, tutar, açıklama, tür) kolonları; tür sabit metin olabilir
    child: bool = False            # raporda tek tek listelenmez


def _entry_hash(r, side):
    return ("h", r["import_hash"]) if r.get("import_hash") else None


def _entry_same(r, side):
    """Aynı gün + tutar + tür + açıklama: elle girilen (ya da eşitlemeyle parmak izi almış) kayıt, eski biçim parmak izi."""
    return ("m", r["entry_date"], _amt(r["amount"]), r["kind"], _norm(r["description"]))


def _entry_accept(t: dict, s: dict) -> bool:
    """İkisinin de (farklı) parmak izi varsa ayrıca aynı hesap olmalı: aynı gün aynı tutarlı iki ayrı banka hareketi
    yanlışlıkla tekleşmesin."""
    if t.get("import_hash") and s.get("import_hash"):
        return t.get("account_id") == s.get("account_id")
    return True


def _hash(r, side):
    return ("h", r["import_hash"]) if r.get("import_hash") else None


def _opt(*cols):
    """Boş olabilen kolonlarla anahtar (None → '')."""
    return lambda r, side: tuple("" if r.get(c) is None else (_amt(r[c]) if c in ("amount", "quantity") else r[c])
                                 for c in cols)


TABLES = [
    T("accounts", keys=(("name",), lambda r, s: ("iban", r["iban"]) if r.get("iban") else None),
      fks={"anchor_entry_id": "entries"}, when="updated_at", label=("as_of", "balance", "name", "kind")),
    T("counterparties", pk=None, keys=(("name",),), flags=("track_debt",), edit=("category",), child=True),
    T("merchant_aliases", pk=None, keys=(("source",),), edit=("display",), child=True),
    T("budgets", pk=None, keys=(("category",),), edit=("monthly_limit",), child=True),
    T("card_limits", pk=None, keys=(("card_no",),), edit=("credit_limit",), child=True),
    T("rules", keys=(("pattern", "category"),), child=True),
    T("subcategories", keys=(("category", "name"),), when="created_at", child=True),
    T("item_rules", keys=(("keywords", "category"),), when="created_at", child=True),
    T("subscription_state", pk=None, keys=(("merchant",),), when="changed_at", child=True),
    T("mail_log", pk=None, keys=(("message_id",),), when="processed_at", child=True),
    T("match_rules", keys=(("source", "pattern", "entry_key"),), when="updated_at", child=True),
    T("zakat_people", keys=(("name",),), flags=("is_self", "is_minor"), label=("hawl_start", None, "name", "zekât kişisi")),
    T("recurring", keys=(lambda r, s: (_norm(r["description"]), r["kind"]),), fks={"wallet_id": "accounts", "account_id": "accounts"},
      label=("start_month", "amount", "description", "kind")),
    T("recurring_overrides", pk=None, keys=(("recurring_id", "month"),), fks={"recurring_id": "recurring"},
      required=("recurring_id",), when=("parent", "recurring_id"), child=True),
    T("recurring_changes", keys=(("recurring_id", "from_month"),), fks={"recurring_id": "recurring"},
      required=("recurring_id",), when="created_at", label=("from_month", "amount", None, "tutar değişikliği")),
    T("salary_changes", keys=(("recurring_id", "from_month"),), fks={"recurring_id": "recurring"},
      required=("recurring_id",), when="created_at", label=("from_month", "gross", "employer", "maaş değişikliği")),
    T("statements", keys=(("file_hash",), ("bank", "card_no", "period_end")), when="uploaded_at",
      label=("period_end", "total_debt", "card_no", "ekstre")),
    T("transactions", keys=(lambda r, s: (r["statement_id"], r["txn_date"], _amt(r["amount"]), r["description"]),),
      fks={"statement_id": "statements"}, required=("statement_id",), when=("parent", "statement_id"),
      edit=("category", "note"), flags=("is_deleted", "hidden"), extra_parents=("statement_id",), child=True),
    T("bank_documents", keys=(("file_name",),), fks={"account_id": "accounts"}, when="uploaded_at",
      label=("uploaded_at", None, "filename", "hesap dökümü")),
    T("entries", keys=(_entry_hash, _entry_same), accept=_entry_accept,
      fks={"recurring_id": "recurring", "linked_txn_id": "transactions", "debt_id": "debts", "wallet_id": "accounts",
           "account_id": "accounts", "bank_doc_id": "bank_documents"},
      when=("created_at", "entry_date"), edit=("description", "category", "note", "counterparty", "location", "payment"),
      flags=("hidden", "skipped", "not_virman"), label=("entry_date", "amount", "description", "kind")),
    T("debts", keys=(("person", "direction", "asset", "amount", "debt_date"),),
      fks={"settled_entry_id": "entries", "given_entry_id": "entries", "account_id": "accounts", "split_id": "splits"},
      when="created_at", edit=("note",), label=("debt_date", "amount", "person", "direction")),
    T("splits", keys=(lambda r, s: (r.get("entry_id"), r.get("txn_id"), _amt(r["amount"]), r["category"]),),
      fks={"entry_id": "entries", "txn_id": "transactions", "debt_id": "debts"}, when=("parent", "entry_id"),
      group=("entry_id", "txn_id"), child=True),
    T("debt_payments", keys=(lambda r, s: (r["debt_id"], _amt(r["amount"]), r["paid_at"]),),
      fks={"debt_id": "debts", "entry_id": "entries", "account_id": "accounts"}, required=("debt_id",), when="paid_at",
      label=("paid_at", "amount", None, "borç ödemesi")),
    T("receipt_items", keys=(lambda r, s: (r["entry_id"], r["name"], _amt(r["amount"]), r["qty"]),),
      fks={"entry_id": "entries"}, required=("entry_id",), when=("parent", "entry_id"), group=("entry_id",), child=True),
    T("entry_matches", pk=None, keys=(("entry_id",),),
      fks={"entry_id": "entries", "txn_id": "transactions", "account_id": "accounts", "rule_id": "match_rules"},
      required=("entry_id",), when="created_at", child=True),
    T("match_rejects", pk=None, keys=(("entry_id", "other"),), fks={"entry_id": "entries"}, required=("entry_id",),
      when="created_at", child=True),
    T("own_transfers", keys=(_hash, _opt("transfer_date", "amount", "account")), fks={"account_id": "accounts"},
      when="transfer_date", edit=("description", "note"), label=("transfer_date", "amount", "description", "virman")),
    T("card_payments", keys=(_hash, _opt("pay_date", "amount", "card_last4", "statement_id")),
      fks={"account_id": "accounts", "statement_id": "statements"}, when=("created_at", "pay_date"),
      label=("pay_date", "amount", "description", "kart ödemesi")),
    T("bills", keys=(lambda r, s: ("f", r["file_name"]) if r.get("file_name") else None,
                     ("provider", "due_date", "amount")),
      fks={"entry_id": "entries"}, when="created_at", label=("due_date", "amount", "provider", "fatura")),
    T("savings_goals", keys=(("kind", "name", "start_month"),), when="created_at", label=("start_month", "target", "name", "kısma hedefi")),
    T("saving_targets", keys=(("name", "deadline"),), when="created_at", label=("deadline", "amount", "name", "birikim hedefi")),
    T("jars", keys=(("name",),), fks={"account_id": "accounts", "debt_id": "debts", "closed_payment_id": "debt_payments"}, when="created_at", label=("created_at", "goal", "name", "kumbara")),
    T("zakat_assets", keys=(_opt("person_id", "kind", "quantity", "acquired"),), fks={"person_id": "zakat_people"},
      required=("person_id",), when="created_at", label=("acquired", "quantity", "kind", "zekât varlığı")),
    T("jar_moves", keys=(lambda r, s: (r["jar_id"], r["move_date"], _amt(r["amount"])),),
      fks={"jar_id": "jars", "asset_id": "zakat_assets", "entry_id": "entries", "debt_payment_id": "debt_payments"},
      required=("jar_id",), when="move_date",
      label=("move_date", "amount", "note", "kumbara hareketi")),
    T("zakat_payments", keys=(("person_id", "year_start", "pay_date", "amount"),),
      fks={"person_id": "zakat_people", "entry_id": "entries", "txn_id": "transactions"}, required=("person_id",),
      when="pay_date", label=("pay_date", "amount", "note", "zekât ödemesi")),
    T("donations", keys=(_opt("kind", "amount", "given_date", "recipient"),),
      fks={"entry_id": "entries", "txn_id": "transactions"}, when="created_at",
      label=("given_date", "amount", "recipient", "kind")),
    T("shared_inbox", keys=(("kind", "doc"),), when="created_at", label=("created_at", None, "filename", "kind")),
    T("account_separate", pk=None, keys=(("a", "b"),), fks={"a": "accounts", "b": "accounts"}, required=("a", "b"), child=True),
    T("stored_files", pk=None, keys=(("kind", "name"),), mode="files", child=True),
    T("settings", pk=None, keys=(("key",),), mode="settings", child=True),
    T("idempotency", pk=None, mode="target", child=True),
    T("import_batches", pk=None, mode="clear", child=True),
    T("template_submissions", mode="source", child=True),
    T("bill_templates", mode="source", child=True),  # fatura şablonları: yeniden öğrenilir, kaynaktaki kalır
]
BY_NAME = {t.name: t for t in TABLES}
CREATED_BY = {"entries", "jar_moves", "entry_matches", "match_rejects"}
FILE_REFS = {  # kaynak defterden: tür → dosya adları
    "uploads": "SELECT file_hash || '.pdf' FROM statements",
    "bills": "SELECT file_name FROM bills WHERE file_name IS NOT NULL",
    "receipts": "SELECT receipt_file FROM entries WHERE receipt_file IS NOT NULL",
    "statements-bank": "SELECT file_name FROM bank_documents WHERE stored = 1",
}


def _empty(v) -> bool:
    return v is None or v == ""


# ---------------------------------------------------------------- yardımcılar

def _copy_db(src: str, dst: str) -> None:
    s = sqlite3.connect(f"file:{os.path.abspath(src)}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prepare(path: str, user: dict | None) -> None:
    """Şema + göçler (uygulamanın açılışta yaptığı) ve tembel kurulan modül tabloları."""
    from app import donations, share, undo, zakat

    with db.use_space(path, user):  # ilk açılışta init_db (şema + göçler)
        with db.connect() as c:
            zakat._init(c)
            donations._init(c)
            share._init(c)
            c.execute(undo.SCHEMA)


def _tables(c) -> list[str]:
    return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                                    "ORDER BY name")]


def _cols(c, table: str) -> list[str]:
    return [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]


def _rows(c, table: str, pk: str | None) -> list[dict]:
    c.row_factory = sqlite3.Row
    order = pk or "rowid"
    return [dict(r) for r in c.execute(f'SELECT * FROM "{table}" ORDER BY {order}')]


def _since_value(t: T, row: dict) -> str | None:
    w = t.when
    if isinstance(w, str):
        return row.get(w)
    if isinstance(w, tuple) and w and w[0] != "parent":
        for col in w:
            if row.get(col):
                return row[col]
    return None


def _summary(t: T, row: dict) -> dict:
    d, a, desc, kind = (list(t.label) + [None] * 4)[:4]
    k = row.get(kind) if kind in row else kind
    return {"table": t.name, "date": str(row.get(d) or "")[:10] if d else "", "amount": row.get(a) if a else None,
            "description": str(row.get(desc) or "")[:80] if desc else "", "kind": k}


# ---------------------------------------------------------------- birleştirme

class Merge:
    def __init__(self, merged: str, target: str, since: str, skip_pre: bool, prefer_target: bool, user_map: dict):
        self.mpath, self.tpath = merged, target
        self.since, self.skip_pre, self.prefer_target, self.user_map = since, skip_pre, prefer_target, user_map
        self.m = sqlite3.connect(merged)
        self.m.row_factory = sqlite3.Row
        self.m.execute("PRAGMA foreign_keys = OFF")
        self.t = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        self.t.row_factory = sqlite3.Row
        self.idmap: dict[str, dict] = defaultdict(dict)       # tablo → hedef id → sonuç id (None: taşınmadı)
        self.status: dict[str, dict] = defaultdict(dict)      # tablo → hedef id → paired | moved | skipped
        self.tsince: dict[str, dict] = defaultdict(dict)      # tablo → hedef id → True (since sonrası) / False
        self.done: set[str] = set()
        self.pending: list[tuple] = []                        # (tablo, sonuç id, kolon, ref tablo, hedef ref id)
        self.stats: dict[str, dict] = {}
        self.moved_since: list[dict] = []
        self.moved_pre: list[dict] = []
        self.dupes: list[dict] = []
        self.edits: list[dict] = []
        self.conflicts: list[dict] = []
        self.notes: list[str] = []
        self.dropped_children: dict[str, int] = defaultdict(int)
        self.moved_entries: list[int] = []
        self.src_hashes = {r[0] for r in self.m.execute(
            "SELECT import_hash FROM own_transfers WHERE import_hash IS NOT NULL UNION "
            "SELECT import_hash FROM card_payments WHERE import_hash IS NOT NULL")}
        self.src_any_hashes = self.src_hashes | {r[0] for r in self.m.execute(
            "SELECT import_hash FROM entries WHERE import_hash IS NOT NULL")}
        self.settings_report: dict = {}
        self.created_by_mapped = 0
        self.touched_debts: set = set()

    # ------------------------------------------------ eşleme
    def _map(self, ref: str, tid):
        if tid is None or tid == 0:
            return tid
        return self.idmap[ref].get(tid)

    def _mapped(self, t: T, row: dict) -> tuple[dict, list]:
        """Hedef satırının bağları sonuç kimliklerine çevrilmiş hali; henüz işlenmemiş tablolara bağlar ertelenir."""
        out, later = dict(row), []
        for col, ref in t.fks.items():
            if col not in row or row[col] is None or row[col] == 0:
                continue
            if ref in self.done:
                out[col] = self.idmap[ref].get(row[col])
            else:
                later.append((col, ref, row[col]))
                out[col] = None
        if t.name == "match_rejects" and str(row.get("other") or "").startswith("t:"):
            tid = self._map("transactions", int(row["other"][2:]))
            out["other"] = f"t:{tid}" if tid else None
        return out, later

    def _key(self, spec, row: dict, side: str):
        if callable(spec):
            return spec(row, side)
        vals = tuple(_amt(row.get(c)) if c in ("amount", "quantity", "gross", "monthly_limit") else row.get(c) for c in spec)
        return None if vals[0] is None else vals

    def _since_of(self, t: T, row: dict, mrow: dict) -> bool | None:
        if isinstance(t.when, tuple) and t.when and t.when[0] == "parent":
            col = t.when[1]
            ref = t.fks.get(col)
            return self.tsince[ref].get(row.get(col)) if ref else None
        v = _since_value(t, row)
        return None if not v else str(v)[:10] >= self.since

    # ------------------------------------------------ tablo
    def table(self, t: T) -> None:
        mt, tt = set(_tables(self.m)), set(_tables(self.t))
        st = {"source": 0, "target": 0, "paired": 0, "moved_since": 0, "moved_pre": 0, "skipped": 0, "dupes": 0}
        self.stats[t.name] = st
        if t.name not in mt and t.name not in tt:
            self.done.add(t.name)
            return
        if t.name not in mt:  # kaynakta hiç yok (olmamalı: şema kuruldu)
            self.notes.append(f"{t.name}: birleşik defterde tablo yok, hedefteki satırlar taşınmadı")
            self.done.add(t.name)
            return
        st["source"] = self.m.execute(f'SELECT COUNT(*) FROM "{t.name}"').fetchone()[0]
        trows = _rows(self.t, t.name, t.pk) if t.name in tt else []
        st["target"] = len(trows)
        if t.mode == "clear":
            st["skipped"] = st["source"] + st["target"]
            self.m.execute(f'DELETE FROM "{t.name}"')
        elif t.mode == "target":
            self.m.execute(f'DELETE FROM "{t.name}"')
            cols = [c for c in _cols(self.m, t.name) if c in (trows[0] if trows else {})]
            for r in trows:
                self._insert(t, {c: r[c] for c in cols})
            st["paired"] = len(trows)
        elif t.mode == "source":
            st["skipped"] = len(trows)
        elif t.mode == "settings":
            self._settings(trows, st)
        elif t.mode == "files":
            self._files_union(trows, st)
        else:
            self._merge(t, trows, st)
        self.done.add(t.name)

    def _merge(self, t: T, trows: list[dict], st: dict) -> None:
        srows = _rows(self.m, t.name, t.pk)
        mcols = set(_cols(self.m, t.name))
        pkcols = list(t.keys[0]) if t.pk is None else [t.pk]
        sid = (lambda r: r[t.pk]) if t.pk else (lambda r: tuple(r[c] for c in pkcols))
        sbyid = {sid(r): r for r in srows}
        index = [defaultdict(deque) for _ in t.keys]
        for r in srows:
            for i, spec in enumerate(t.keys):
                k = self._key(spec, r, "s")
                if k is not None:
                    index[i][k].append(sid(r))
        used: set = set()
        # ebeveyn grubu: kaynakta bu ebeveynin satırı var mı
        sgroup = {(g, r[g]) for g in t.group for r in srows if r.get(g) is not None}
        prepared = []
        for r in trows:
            mrow, later = self._mapped(t, r)
            prepared.append((r, mrow, later))
        pair: dict[int, object] = {}
        how: dict[int, int] = {}
        for i, spec in enumerate(t.keys):  # anahtar sırasıyla: önce kesin (parmak izi), sonra gevşek
            for n, (r, mrow, _) in enumerate(prepared):
                if n in pair:
                    continue
                k = self._key(spec, mrow, "t")
                if k is None:
                    continue
                q = index[i].get(k)
                while q and q[0] in used:
                    q.popleft()
                if not q:
                    continue
                cand = next((x for x in q if x not in used and (t.accept is None or t.accept(mrow, sbyid[x]))), None)
                if cand is not None:
                    pair[n] = cand
                    used.add(cand)
                    how[n] = i
        for n, (r, mrow, later) in enumerate(prepared):
            tid = r.get(t.pk) if t.pk else n
            since = self._since_of(t, r, mrow)
            self.tsince[t.name][tid] = since
            if n in pair:
                st["paired"] += 1
                self.status[t.name][tid] = "paired"
                if t.pk:
                    self.idmap[t.name][tid] = pair[n]
                self._update_paired(t, sbyid[pair[n]], mrow, later, pair[n], pkcols)
                s0 = sbyid[pair[n]]
                if t.name == "entries" and how[n] == 1 and r.get("import_hash") and s0.get("import_hash"):
                    self.notes.append(f"entries: parmak izi farklı (eski biçim) kayıt tekilleşti: {r['entry_date']} "
                                      f"{r['amount']} {str(r['description'])[:40]}")
                continue
            skip = self._skip_reason(t, r, mrow, sgroup)
            if skip:
                st["skipped"] += 1
                self.status[t.name][tid] = "skipped"
                if t.pk:
                    self.idmap[t.name][tid] = None
                if skip != "parent":
                    self.dropped_children[f"{t.name}: {skip}"] += 1
                continue
            if since is not True and self.skip_pre:
                st["skipped"] += 1
                self.status[t.name][tid] = "skipped"
                if t.pk:
                    self.idmap[t.name][tid] = None
                if not t.child:
                    self.moved_pre.append({**_summary(t, r), "moved": False})
                continue
            if t.name == "entries":
                self._possible_dupes(r, srows)
            vals = {c: mrow[c] for c in mrow if c in mcols and c != t.pk}
            new = self._insert(t, vals)
            if new is None:
                st["skipped"] += 1
                self.status[t.name][tid] = "skipped"
                if t.pk:
                    self.idmap[t.name][tid] = None
                continue
            self.status[t.name][tid] = "moved"
            if t.pk:
                self.idmap[t.name][tid] = new
                for col, ref, val in later:
                    self.pending.append((t.name, new, col, ref, val, None))
            if t.name == "entries":
                self.moved_entries.append(new)
            elif t.name == "debts":
                self.touched_debts.add(new)
            elif t.name == "debt_payments":
                self.touched_debts.add(vals.get("debt_id"))
            if since:
                st["moved_since"] += 1
                if not t.child:
                    self.moved_since.append(_summary(t, r))
            else:
                st["moved_pre"] += 1
                if not t.child:
                    self.moved_pre.append({**_summary(t, r), "moved": True, "no_time": since is None})
        st["dupes"] = sum(1 for d in self.dupes if d["table"] == t.name)

    def _skip_reason(self, t: T, r: dict, mrow: dict, sgroup: set) -> str | None:
        for col in t.required:
            if mrow.get(col) is None:
                return "parent"
        if t.name == "rules" and (r.get("priority") or 0) >= 1000:
            return "varsayılan kural (kaynakta güncel sürümü var)"
        if t.name == "entries" and r.get("import_hash") in self.src_hashes:
            return "aynı parmak izi kaynakta virman/kart ödemesi olarak var"
        if t.name in ("own_transfers", "card_payments") and r.get("import_hash") and \
                r["import_hash"] in self.src_any_hashes:
            return "aynı parmak izi kaynakta başka tabloda var"
        if t.name == "match_rejects" and mrow.get("other") is None:
            return "parent"
        for g in t.group:
            if mrow.get(g) is not None and self.status[t.fks[g]].get(r.get(g)) == "paired" and (g, mrow[g]) in sgroup:
                return "eşleşen kayıtta kaynaktaki satırlar tutuldu"
        if t.name == "splits" and mrow.get("entry_id") is None and mrow.get("txn_id") is None:
            return "parent"
        for col in t.extra_parents:  # eşleşen ekstrenin fazladan satırı: kaynak ekstre esas
            if self.status[t.fks[col]].get(r.get(col)) == "paired":
                return "eşleşen ekstrede kaynaktaki işlemler tutuldu"
        return None

    def _possible_dupes(self, r: dict, srows: list[dict]) -> None:
        from datetime import date

        d0 = date.fromisoformat(str(r["entry_date"])[:10])
        a, desc = _amt(r["amount"]), _norm(r["description"])
        for s in srows:
            if _amt(s["amount"]) != a or s["kind"] != r["kind"]:
                continue
            try:
                dd = abs((date.fromisoformat(str(s["entry_date"])[:10]) - d0).days)
            except ValueError:
                continue
            if (dd == 0 and _norm(s["description"]) != desc) or (0 < dd <= 3 and _norm(s["description"]) == desc):
                self.dupes.append({"table": "entries", "target": _summary(BY_NAME["entries"], r),
                                   "source": _summary(BY_NAME["entries"], s), "source_id": s["id"]})
                return

    def _insert(self, t: T, vals: dict):
        cols = list(vals)
        try:
            cur = self.m.execute(f'INSERT INTO "{t.name}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                                 f'VALUES ({", ".join("?" * len(cols))})', [vals[c] for c in cols])
        except sqlite3.IntegrityError as e:
            self.notes.append(f"{t.name}: hedef satırı eklenemedi ({e}); atlandı")
            return None
        return cur.lastrowid if t.pk else True

    def _update_paired(self, t: T, s: dict, mrow: dict, later: list, sid, pkcols: list) -> None:
        sets: dict = {}
        mcols = set(s)
        skip = {t.pk, "created_at", "uploaded_at", "updated_at", "month", "processed_at", *pkcols}
        pending_cols = {c for c, _, _ in later}
        for c, tv in mrow.items():
            if c not in mcols or c in skip or c in pending_cols:
                continue
            sv = s.get(c)
            if c in t.flags:
                if tv and not sv:
                    sets[c] = tv
                continue
            if _empty(tv) or tv == sv:
                continue
            if _empty(sv) or (c == "category" and sv == "Kategorisiz"):
                sets[c] = tv
            elif c in t.edit:
                rec = {"table": t.name, "id": sid, "field": c, "source": sv, "target": tv}
                self.conflicts.append(rec)
                if self.prefer_target:
                    sets[c] = tv
        # özel kurallar
        if t.name == "transactions" and mrow.get("category_manual") and not s.get("category_manual") \
                and mrow.get("category") and mrow["category"] != s.get("category"):
            sets["category"], sets["category_manual"] = mrow["category"], 1
        if t.name == "accounts" and mrow.get("as_of") and (not s.get("as_of") or str(mrow["as_of"]) > str(s["as_of"])):
            for c in ("balance", "as_of", "source", "updated_at"):
                if c in mrow:
                    sets[c] = mrow[c]
            for col, ref, val in later:
                if col == "anchor_entry_id":
                    self.pending.append((t.name, sid, col, ref, val, "force"))
            if "anchor_entry_id" not in pending_cols:
                sets["anchor_entry_id"] = mrow.get("anchor_entry_id") or 0
        if t.name == "subscription_state" and str(mrow.get("changed_at") or "") > str(s.get("changed_at") or ""):
            for c in ("status", "changed_at", "new_amount", "note"):
                if c in mrow:
                    sets[c] = mrow[c]
        if t.name == "match_rules":
            for c in ("hits", "used"):
                if (mrow.get(c) or 0) > (s.get(c) or 0):
                    sets[c] = mrow[c]
        for col, ref, val in later:  # kaynakta boş bağ: hedefin bağı (sonra çözülür)
            if col != "anchor_entry_id" and _empty(s.get(col)):
                self.pending.append((t.name, sid, col, ref, val, "fill"))
        if not sets:
            return
        for c, v in sets.items():
            self.edits.append({"table": t.name, "id": sid, "field": c, "from": s.get(c), "to": v})
        where = " AND ".join(f'"{c}" = ?' for c in pkcols)
        key = [s[c] for c in pkcols]
        try:
            self.m.execute(f'UPDATE "{t.name}" SET {", ".join(chr(34) + c + chr(34) + " = ?" for c in sets)} WHERE {where}',
                           [*sets.values(), *key])
        except sqlite3.IntegrityError as e:
            self.notes.append(f"{t.name}#{sid}: hedefteki düzenleme uygulanamadı ({e})")
            self.edits = self.edits[:-len(sets)]
            return
        s.update(sets)

    def resolve_pending(self) -> int:
        dangling = 0
        for table, rid, col, ref, val, mode in self.pending:
            if col == "anchor_entry_id":
                self.m.execute("UPDATE accounts SET anchor_entry_id = ? WHERE id = ?", (self._anchor(rid, val), rid))
                continue
            new = self.idmap[ref].get(val)
            if new is None:
                dangling += 1
                continue
            pk = BY_NAME[table].pk
            if mode == "fill":
                self.m.execute(f'UPDATE "{table}" SET "{col}" = ? WHERE {pk} = ? AND "{col}" IS NULL', (new, rid))
            else:
                self.m.execute(f'UPDATE "{table}" SET "{col}" = ? WHERE {pk} = ?', (new, rid))
        return dangling

    def _anchor(self, rid: int, target_anchor: int) -> int:
        """Hedefin bakiye çapası: çapa gününde çapadan önceki (id <= çapa) hedef kayıtları birleşik defterde de çapanın
        içinde kalsın, sonrakiler dışında. Yeni çapa = o günkü bu kayıtların birleşik defterdeki en büyük kimliği."""
        as_of = self.m.execute("SELECT as_of FROM accounts WHERE id = ?", (rid,)).fetchone()[0]
        ids = [self.idmap["entries"].get(r[0]) for r in self.t.execute(
            "SELECT id FROM entries WHERE entry_date = ? AND id <= ?", (as_of, target_anchor))]
        ids = [i for i in ids if i]
        if ids:
            return max(ids)
        new = self.idmap["entries"].get(target_anchor)
        return new or 0

    # ------------------------------------------------ ayarlar
    def _settings(self, trows: list[dict], st: dict) -> None:
        src = {r["key"]: r["value"] for r in self.m.execute("SELECT key, value FROM settings")}
        tgt = {r["key"]: r["value"] for r in trows}
        rep = {"kept_target": [], "dropped_source": [], "from_source": [], "filled_from_target": [], "merged_list": [],
               "differs_kept_source": [], "migration": []}
        out = {}
        for k, v in src.items():
            cls = setting_class(k)
            if cls == "drop":
                rep["dropped_source"].append(k)
            elif cls == "target":
                continue
            elif cls == "migration":
                out[k] = v
                rep["migration"].append(k)
            elif cls == "list":
                try:
                    a, b = json.loads(v or "[]"), json.loads(tgt.get(k) or "[]")
                    merged = list(dict.fromkeys([*a, *b]))
                except (ValueError, TypeError):
                    merged = None
                out[k] = json.dumps(merged, ensure_ascii=False) if merged is not None else v
                if merged is not None and len(merged) != len(a):
                    rep["merged_list"].append(k)
                else:
                    rep["from_source"].append(k)
            else:
                out[k] = v
                rep["from_source"].append(k)
                if k in tgt and tgt[k] != v:
                    rep["differs_kept_source"].append(k)
        for k, v in tgt.items():
            cls = setting_class(k)
            if cls == "target":
                out[k] = v
                rep["kept_target"].append(k)
            elif cls in ("data", "list") and k not in src:
                out[k] = v
                rep["filled_from_target"].append(k)
            elif cls == "drop":
                pass  # hedefte kendi-sunucusu anahtarı olmaz; varsa da taşımıyoruz
        self.m.execute("DELETE FROM settings")
        self.m.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", list(out.items()))
        st["paired"] = len([k for k in tgt if k in out and out[k] == tgt[k]])
        st["skipped"] = len(rep["dropped_source"])
        self.settings_report = {k: sorted(v) for k, v in rep.items()}

    # ------------------------------------------------ dosya dizini (dosyalar ayrı adımda)
    def _files_union(self, trows: list[dict], st: dict) -> None:
        for r in trows:
            cur = self.m.execute("INSERT OR IGNORE INTO stored_files (kind, name, size, legacy, created_at) "
                                 "VALUES (?, ?, ?, ?, ?)", (r["kind"], r["name"], r["size"], r["legacy"], r["created_at"]))
            st["moved_since" if cur.rowcount else "paired"] += cur.rowcount or 1

    # ------------------------------------------------ son düzeltmeler
    def finish(self) -> dict:
        out = {"dangling": self.resolve_pending(), "created_by_mapped": self.created_by_mapped}
        self.m.commit()
        return out

    def map_source_users(self) -> None:
        """Kaynak satırlarındaki 'ekleyen' kullanıcı kimlikleri (ortak defter) → hedef sunucudaki kimlikler.
        Birleştirmeden ÖNCE: yalnız kaynaktan gelen satırlar değişir."""
        n = 0
        for table in CREATED_BY:
            if table in _tables(self.m) and "created_by" in _cols(self.m, table):
                cases = " ".join(f"WHEN {int(a)} THEN {int(b)}" for a, b in self.user_map.items())
                if cases:
                    n += self.m.execute(f'UPDATE "{table}" SET created_by = CASE created_by {cases} END WHERE created_by IN '
                                        f'({",".join(str(int(a)) for a in self.user_map)})').rowcount
        self.created_by_mapped = n

    def close(self) -> None:
        self.m.commit()
        self.m.close()
        self.t.close()


def _recompute(merged: str, user: dict | None, m: Merge) -> dict:
    """Uygulamanın kendi hesapları: borçların ödenen tutarı, taşınan kayıtların dönemi, ekstre ödendi işaretleri."""
    out = {}
    with db.use_space(merged, user):
        with db.connect() as c:
            for d in sorted(x for x in m.touched_debts if x):  # yalnız hedeften ödemesi/borcu gelenler
                if c.execute("SELECT 1 FROM debts WHERE id = ?", (d,)).fetchone():
                    db._recalc_debt(c, d)
            for eid in m.moved_entries:
                r = c.execute("SELECT entry_date, month FROM entries WHERE id = ?", (eid,)).fetchone()
                if r:
                    p = db.period_of(r[0])
                    if p != r[1]:
                        c.execute("UPDATE entries SET month = ? WHERE id = ?", (p, eid))
        if m.stats.get("card_payments", {}).get("moved_since") or m.stats.get("card_payments", {}).get("moved_pre") \
                or m.stats.get("statements", {}).get("moved_since") or m.stats.get("statements", {}).get("moved_pre"):
            r = db.reassign_statement_payments(apply=True)
            out["statements_marked"] = r.get("marked")
            out["statements_cleared"] = r.get("cleared")
    return out


# ---------------------------------------------------------------- ölçümler

def _measure(path: str, user: dict | None) -> dict:
    from app import finance

    with db.use_space(path, user):
        accounts = {a["name"]: {"balance": a["balance"], "as_of": a["as_of"], "current": a.get("current")}
                    for a in db.list_accounts()}
        months: dict = defaultdict(lambda: {"gelir": 0.0, "gider": 0.0, "kart_harcama": 0.0})
        with db.connect() as c:
            for m, kind, s in c.execute("SELECT month, kind, SUM(amount) FROM entries WHERE skipped = 0 AND debt_id IS NULL "
                                        "GROUP BY month, kind"):
                months[m][kind] += s or 0
            counts = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in _tables(c)}
        try:
            for t in finance.transactions(None):
                if t.get("source") == "card" and not t.get("deleted") and t["kind"] in ("harcama", "faiz_ucret", "iade"):
                    months[t["month"]]["kart_harcama"] += t["amount"]
        except Exception as e:  # noqa: BLE001 - ölçüm best effort
            months["_hata"] = {"gelir": 0.0, "gider": 0.0, "kart_harcama": 0.0, "error": str(e)[:200]}
    return {"accounts": accounts, "months": {k: {x: round(v, 2) if isinstance(v, float) else v for x, v in d.items()}
                                             for k, d in sorted(months.items())}, "counts": counts}


def _integrity(path: str, user: dict | None) -> dict:
    from app import integrity

    with db.use_space(path, user):
        r = integrity.check()
    return {"counts": r["counts"], "refs": r["refs"]}


# ---------------------------------------------------------------- dosyalar

def _target_store(target_db: str, main: bool):
    from app import storage

    prefix, root = db._files_scope(None if main else target_db)
    if prefix is None:
        return storage.LocalBackend(root), ""
    return storage.backend(), prefix


def _file_plan(merged: str, src_db: str, source_files: str | None, target_db: str, main: bool) -> list[dict]:
    from app import storage

    c = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    try:
        want: dict = {}
        tabs = set(_tables(c))
        if "stored_files" in tabs:
            for kind, name, size in c.execute("SELECT kind, name, size FROM stored_files"):
                want[(kind, name)] = size
        for kind, sql in FILE_REFS.items():
            try:
                for (name,) in c.execute(sql):
                    want.setdefault((kind, name), None)
            except sqlite3.OperationalError:
                pass
    finally:
        c.close()
    backend, prefix = _target_store(target_db, main)
    plan = []
    for (kind, name), size in sorted(want.items()):
        if not storage.valid_name(kind, name):
            continue
        item = {"kind": kind, "name": name, "size": size}
        try:
            have = backend.exists(f"{prefix}{kind}/{name}")
        except storage.StorageError:
            have = None
        local = os.path.join(source_files, kind, name) if source_files else None
        if have:
            item["status"] = "zaten_var"
        elif local and os.path.isfile(local):
            item["status"], item["size"], item["path"] = "yuklenecek", os.path.getsize(local), local
        else:
            item["status"] = "eksik" if have is False else "bilinmiyor"
        plan.append(item)
    return plan


def _apply_file_index(merged: str, plan: list[dict]) -> None:
    """Birleşik defterin dosya dizini: hedefte var olan ya da yüklenecek kaynak dosyaları; eksikler dizinden çıkar."""
    c = sqlite3.connect(merged)
    try:
        for f in plan:
            if f["status"] in ("zaten_var", "yuklenecek"):
                c.execute("INSERT OR IGNORE INTO stored_files (kind, name, size, legacy) VALUES (?, ?, ?, 0)",
                          (f["kind"], f["name"], f["size"] or 0))
                if f["size"]:
                    c.execute("UPDATE stored_files SET size = ? WHERE kind = ? AND name = ? AND size = 0",
                              (f["size"], f["kind"], f["name"]))
            elif f["status"] == "eksik":
                c.execute("DELETE FROM stored_files WHERE kind = ? AND name = ?", (f["kind"], f["name"]))
        c.commit()
    finally:
        c.close()


def _upload(plan: list[dict], target_db: str, main: bool) -> dict:
    from app import storage

    backend, prefix = _target_store(target_db, main)
    out = {"uploaded": 0, "failed": []}
    for f in plan:
        if f["status"] != "yuklenecek":
            continue
        with open(f["path"], "rb") as fh:
            data = fh.read()
        try:
            backend.put(f"{prefix}{f['kind']}/{f['name']}", data, storage.content_type(f["name"]))
            got = backend.get(f"{prefix}{f['kind']}/{f['name']}")
            if hashlib.sha256(got).digest() != hashlib.sha256(data).digest():
                raise storage.StorageError("doğrulama tutmadı")
            out["uploaded"] += 1
        except (storage.StorageError, storage.NotFound) as e:
            out["failed"].append(f"{f['kind']}/{f['name']}: {e}")
    return out


# ---------------------------------------------------------------- çalıştırma

def _resolve_target(username: str | None, target_db: str | None) -> tuple[str, dict | None, bool]:
    from app import system

    if target_db:
        return os.path.abspath(target_db), None, os.path.abspath(target_db) == os.path.abspath(db.DB_PATH)
    u = system.user_by_name(username or "")
    if not u:
        raise LookupError(f"kullanıcı yok: {username}")
    if u.get("member_of"):
        raise LookupError("ortak defter üyesi: sahibin kullanıcı adıyla çalıştır")
    p = system.user_db_path(u)
    return os.path.abspath(p or db.DB_PATH), u, p is None


def run(source: str, source_files: str | None, since: str, target_user: str | None = None, target_db: str | None = None,
        apply: bool = False, skip_pre_since: bool = False, prefer_target_edits: bool = False,
        user_map: dict | None = None, out: str | None = None, force: bool = False) -> dict:
    datetime.strptime(since, "%Y-%m-%d")
    source = os.path.abspath(source)
    tpath, user, main = _resolve_target(target_user, target_db)
    if not os.path.isfile(source):
        raise FileNotFoundError(f"kaynak yok: {source}")
    if not os.path.isfile(tpath):
        raise FileNotFoundError(f"hedef defter yok: {tpath}")
    if os.path.samefile(source, tpath):
        raise ValueError("kaynak ve hedef aynı dosya")
    if source_files and not os.path.isdir(source_files):
        raise FileNotFoundError(f"kaynak dosya dizini yok: {source_files}")
    sha_before = _sha(tpath)
    tmp = tempfile.mkdtemp(prefix="book-merge-")
    try:
        tcopy = os.path.join(tmp, "target", "hedef.db")
        mcopy = os.path.join(tmp, "merged", "ekstre.db")
        scopy = os.path.join(tmp, "source", "kaynak.db")
        for p in (tcopy, mcopy, scopy):
            os.makedirs(os.path.dirname(p))
        _copy_db(tpath, tcopy)
        _copy_db(source, scopy)
        shutil.copyfile(scopy, mcopy)
        _prepare(tcopy, user)
        _prepare(mcopy, user)
        before = _measure(tcopy, user)
        integrity_before = _integrity(tcopy, user)
        source_meas = _measure(mcopy, user)

        m = Merge(mcopy, tcopy, since, skip_pre_since, prefer_target_edits, user_map or {})
        src_created_by = sorted({r[0] for t in CREATED_BY if t in _tables(m.m) and "created_by" in _cols(m.m, t)
                                 for r in m.m.execute(f'SELECT DISTINCT created_by FROM "{t}" WHERE created_by IS NOT NULL')})
        m.map_source_users()
        known = {t.name for t in TABLES}
        for t in TABLES:
            m.table(t)
        unknown_target = [x for x in _tables(m.t) if x not in known]
        unknown_rows = {x: m.t.execute(f'SELECT COUNT(*) FROM "{x}"').fetchone()[0] for x in unknown_target}
        fin = m.finish()
        m.close()
        recomputed = _recompute(mcopy, user, m)
        plan = _file_plan(mcopy, source, source_files, tpath, main)
        _apply_file_index(mcopy, plan)
        fk = sqlite3.connect(mcopy)
        try:
            fk_problems = fk.execute("PRAGMA foreign_key_check").fetchall()
            quick = fk.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            fk.close()
        after = _measure(mcopy, user)
        integrity_after = _integrity(mcopy, user)

        files = {s: sum(1 for f in plan if f["status"] == s) for s in ("zaten_var", "yuklenecek", "eksik", "bilinmiyor")}
        files["bytes_to_upload"] = sum(f["size"] or 0 for f in plan if f["status"] == "yuklenecek")
        files["missing"] = [f"{f['kind']}/{f['name']}" for f in plan if f["status"] == "eksik"][:200]
        from app import plans

        c = sqlite3.connect(mcopy)
        files["result_bytes"] = c.execute("SELECT COALESCE(SUM(size), 0) FROM stored_files").fetchone()[0]
        c.close()
        files["limit_bytes"] = plans.storage_limit(user) if user else None
        files["over_limit"] = files["limit_bytes"] is not None and files["result_bytes"] > files["limit_bytes"]
        report = {
            "mode": "apply" if apply else "dry-run", "since": since, "source": source, "target": tpath,
            "target_user": (user or {}).get("username"), "tables": m.stats,
            "result_counts": after["counts"], "moved_since": m.moved_since, "pre_since_warning": m.moved_pre,
            "possible_duplicates": m.dupes, "edits_applied": m.edits, "edit_conflicts": m.conflicts,
            "conflicts_applied": prefer_target_edits, "dropped": dict(m.dropped_children), "notes": m.notes,
            "settings": m.settings_report, "unknown_target_tables": unknown_rows,
            "source_created_by": src_created_by, "finish": fin, "recomputed": recomputed,
            "balances": _balances(before["accounts"], after["accounts"], source_meas["accounts"]),
            "months": _months(before["months"], after["months"]),
            "integrity": {"target_before": integrity_before, "result": integrity_after},
            "sqlite": {"foreign_key_check": len(fk_problems), "integrity_check": quick},
            "files": files,
        }
        if out:
            shutil.copyfile(mcopy, out)
            report["out"] = os.path.abspath(out)
        if not apply:
            report["target_unchanged"] = _sha(tpath) == sha_before
            return report
        # ------------------------------------------------ uygula
        if quick != "ok":
            raise RuntimeError(f"birleşik veritabanı bozuk: {quick}")
        if _sha(tpath) != sha_before and not force:
            raise RuntimeError("hedef defter bu arada değişti (uygulama yazdı): yeniden çalıştır (ya da --force)")
        wal = tpath + "-wal"
        if os.path.exists(wal) and os.path.getsize(wal) > 0:
            raise RuntimeError("hedefte bekleyen WAL var: uygulamayı durdurup yeniden dene")
        bdir = os.path.join(os.path.dirname(tpath), "backups")
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, f"pre-merge-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.db")
        _copy_db(tpath, backup)
        report["backup"] = backup
        report["upload"] = _upload(plan, tpath, main)
        if report["upload"]["failed"]:
            fails = {x.split(":", 1)[0] for x in report["upload"]["failed"]}
            c = sqlite3.connect(mcopy)
            for kn in fails:
                kind, name = kn.split("/", 1)
                c.execute("DELETE FROM stored_files WHERE kind = ? AND name = ?", (kind, name))
            c.commit()
            c.close()
        staged = os.path.join(os.path.dirname(tpath), f".merge-{os.getpid()}.db")
        shutil.copyfile(mcopy, staged)
        with open(staged, "rb") as fh:
            os.fsync(fh.fileno())
        if _sha(tpath) != sha_before and not force:
            os.remove(staged)
            raise RuntimeError("hedef defter yükleme sırasında değişti: yeniden çalıştır (yedek alındı, dosyalar yüklendi)")
        os.replace(staged, tpath)
        journal = tpath + "-journal"
        if os.path.exists(journal):
            os.remove(journal)
        from app import integrity

        db._ready_dbs.discard(tpath)
        with db.use_space(None if main else tpath, user):
            db.init_db()
            report["integrity_run"] = integrity.run()
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _balances(before: dict, after: dict, source: dict) -> list[dict]:
    out = []
    for name in sorted(set(before) | set(after)):
        b, a, s = before.get(name, {}), after.get(name, {}), source.get(name, {})
        out.append({"account": name, "target_before": b.get("current"), "source": s.get("current"),
                    "result": a.get("current"), "as_of": a.get("as_of"),
                    "diff": round((a.get("current") or 0) - (b.get("current") or 0), 2)
                    if a.get("current") is not None and b.get("current") is not None else None})
    return out


def _months(before: dict, after: dict) -> list[dict]:
    out = []
    for mo in sorted(set(before) | set(after)):
        b, a = before.get(mo, {}), after.get(mo, {})
        keys = ("gelir", "gider", "kart_harcama")
        out.append({"month": mo, "target_before": {k: b.get(k, 0) for k in keys}, "result": {k: a.get(k, 0) for k in keys},
                    "diff": {k: round(a.get(k, 0) - b.get(k, 0), 2) for k in keys}})
    return out


# ---------------------------------------------------------------- metin rapor

def _tl(v) -> str:
    return "—" if v is None else f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _line(x: dict) -> str:
    return f"{x['date']:10} {_tl(x['amount']) if x['amount'] is not None else '':>14}  {str(x['kind'] or ''):14} " \
           f"{x['table']:16} {x['description']}"


def render(r: dict, limit: int = 200) -> str:
    o = [f"DEFTER BİRLEŞTİRME ({'UYGULANDI' if r['mode'] == 'apply' else 'KURU ÇALIŞTIRMA — hiçbir şey yazılmadı'})",
         f"  kaynak: {r['source']}", f"  hedef:  {r['target']} ({r.get('target_user') or '-'})", f"  since:  {r['since']}"]
    o += ["", "TABLOLAR: kaynak / hedef / tekilleşen (eşleşen) / taşınan since / taşınan önce / atlanan / olası kopya → sonuç"]
    for t, s in r["tables"].items():
        if not (s["source"] or s["target"]):
            continue
        o.append(f"  {t:22} {s['source']:6} / {s['target']:6} / {s['paired']:6} / {s['moved_since']:5} / "
                 f"{s['moved_pre']:5} / {s['skipped']:5} / {s['dupes']:3} → {r['result_counts'].get(t, '-')}")
    o += ["", f"HEDEFTEN TAŞINAN --since KAYITLARI ({len(r['moved_since'])})"]
    o += ["  " + _line(x) for x in r["moved_since"][:limit]]
    if len(r["moved_since"]) > limit:
        o.append(f"  … {len(r['moved_since']) - limit} kayıt daha (--json)")
    pre = r["pre_since_warning"]
    o += ["", f"UYARI: KAYNAKTA OLMAYIP HEDEFTE --since ÖNCESİ OLAN KAYITLAR ({len(pre)})"
              + (" — taşındı (--skip-pre-since ile taşınmaz)" if any(x.get("moved") for x in pre) else
                 " — TAŞINMADI, birleştirmede kaybolacak" if pre else "")]
    o += ["  " + _line(x) + ("  [zaman bilgisi yok]" if x.get("no_time") else "") for x in pre[:limit]]
    o += ["", f"OLASI KOPYALAR (ikisi de kaldı) ({len(r['possible_duplicates'])})"]
    for d in r["possible_duplicates"][:limit]:
        o.append(f"  hedef: {_line(d['target'])}")
        o.append(f"  kaynak: {_line(d['source'])}")
    o += ["", f"UYGULANAN HEDEF DÜZENLEMELERİ ({len(r['edits_applied'])}) · ÇAKIŞMALAR ({len(r['edit_conflicts'])})"
              + (" — hedefinki uygulandı" if r["conflicts_applied"] else " — kaynaktaki tutuldu (--prefer-target-edits)")]
    for e in r["edits_applied"][:limit]:
        o.append(f"  uygulandı {e['table']}#{e['id']} {e['field']}: {str(e['from'])[:40]!r} → {str(e['to'])[:40]!r}")
    for e in r["edit_conflicts"][:limit]:
        o.append(f"  {e['table']}#{e['id']} {e['field']}: kaynak {str(e['source'])[:40]!r} / hedef {str(e['target'])[:40]!r}")
    if r["dropped"]:
        o += ["", "TAŞINMAYAN BAĞLI SATIRLAR"] + [f"  {k}: {v}" for k, v in r["dropped"].items()]
    s = r["settings"]
    o += ["", "AYARLAR (yalnız anahtar adları)",
          f"  hedefte kalan (hazır sürüm): {', '.join(s.get('kept_target', [])) or '-'}",
          f"  taşınmayan (kendi sunucusu): {', '.join(s.get('dropped_source', [])) or '-'}",
          f"  kaynaktan (defter verisi): {', '.join(s.get('from_source', [])) or '-'}",
          f"  hedeften eklenen: {', '.join(s.get('filled_from_target', [])) or '-'}",
          f"  birleşen listeler: {', '.join(s.get('merged_list', [])) or '-'}",
          f"  iki tarafta farklı, kaynaktaki tutuldu: {', '.join(s.get('differs_kept_source', [])) or '-'}"]
    o += ["", "HESAP BAKİYELERİ: hedef önce / kaynak / sonuç (fark)"]
    for b in r["balances"]:
        o.append(f"  {b['account'][:28]:28} {_tl(b['target_before']):>14} / {_tl(b['source']):>14} / "
                 f"{_tl(b['result']):>14} ({_tl(b['diff'])})")
    o += ["", "AY AY: gelir / gider / kart harcaması — hedef önce → sonuç"]
    for mo in r["months"]:
        b, a = mo["target_before"], mo["result"]
        o.append(f"  {mo['month']}: gelir {_tl(b['gelir'])} → {_tl(a['gelir'])} · gider {_tl(b['gider'])} → "
                 f"{_tl(a['gider'])} · kart {_tl(b['kart_harcama'])} → {_tl(a['kart_harcama'])}")
    ib, ia = r["integrity"]["target_before"]["counts"], r["integrity"]["result"]["counts"]
    o += ["", f"DOĞRULUK DENETİMİ: hedef önce {ib or 'temiz'} → sonuç {ia or 'temiz'}",
          f"  sqlite: integrity_check {r['sqlite']['integrity_check']}, yabancı anahtar sorunu {r['sqlite']['foreign_key_check']}"]
    f = r["files"]
    o += ["", f"DOSYALAR: hedefte var {f['zaten_var']} · yüklenecek {f['yuklenecek']} ({f['bytes_to_upload']} bayt) · "
              f"kaynakta yok {f['eksik']} · bilinmiyor {f['bilinmiyor']}"]
    o += ["  eksik: " + x for x in f["missing"][:20]]
    o.append(f"  birleşik defterin dosya alanı {f['result_bytes']} bayt" + (
        f" / paket sınırı {f['limit_bytes']}" + (" — SINIR AŞILIYOR (yükleme yine yapılır; yeni dosyalar saklanmaz)"
                                                 if f["over_limit"] else "") if f["limit_bytes"] is not None else ""))
    if r["unknown_target_tables"]:
        o += ["", "BİLİNMEYEN HEDEF TABLOLARI (taşınmadı): " + ", ".join(f"{k} ({v})" for k, v in r["unknown_target_tables"].items())]
    if r["source_created_by"]:
        o += ["", f"kaynakta 'ekleyen' kullanıcı kimlikleri: {r['source_created_by']} (--user-map ile hedef kimliklerine çevrilir)"]
    if r["notes"]:
        o += ["", "NOTLAR"] + ["  " + n for n in r["notes"][:limit]]
    if r.get("out"):
        o.append(f"\nbirleşik kopya: {r['out']}")
    if r["mode"] == "apply":
        o += ["", f"yedek: {r['backup']}", f"yüklenen dosya: {r['upload']['uploaded']}, hata: {len(r['upload']['failed'])}",
              f"doğruluk denetimi (canlı): {r['integrity_run']}"]
    else:
        o.append(f"\nhedef değişmedi: {'evet' if r.get('target_unchanged') else 'HAYIR!'}")
    return "\n".join(o)


def _user_map(v: str | None) -> dict:
    out = {}
    for part in (v or "").split(","):
        if part.strip():
            a, b = part.split(":")
            out[int(a)] = int(b)
    return out


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m app.book_merge", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True)
    p.add_argument("--source-files", required=True, help="kaynağın dosya dizini (uploads/, bills/, receipts/, "
                                                         "statements-bank/ içeren); '-' dosya yok")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--target-user")
    g.add_argument("--target-db", help="kullanıcı yerine doğrudan defter dosyası (sınama)")
    p.add_argument("--since", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--skip-pre-since", action="store_true")
    p.add_argument("--prefer-target-edits", action="store_true")
    p.add_argument("--user-map")
    p.add_argument("--out")
    p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    try:
        r = run(a.source, None if a.source_files == "-" else a.source_files, a.since, a.target_user, a.target_db,
                apply=a.apply, skip_pre_since=a.skip_pre_since, prefer_target_edits=a.prefer_target_edits,
                user_map=_user_map(a.user_map), out=a.out, force=a.force)
    except (LookupError, ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str) if a.json else render(r))
    return 0


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        raise SystemExit(main(sys.argv[1:]))
