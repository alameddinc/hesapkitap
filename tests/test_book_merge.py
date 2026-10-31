"""Defter birleştirme (app/book_merge.py): iki sentetik defter (uydurma veriler).

KAYNAK: eski kendi-sunucusu defteri (temel). HEDEF: hazır sürümdeki defter; since sonrası telefondan girilenler,
yüklenen döküm, yeni borç, Gelen kutusu, fiş. Ortak: aynı ekstre, aynı banka hareketi, aynı elle kayıt, aynı borç.
"""
import hashlib
import json
import os
import sqlite3

import pytest

from app import book_merge, db, system

SINCE = "2026-10-20"
PDF = "a" * 64
RECEIPT_T = "b" * 64 + ".jpg"
BANKDOC_T = "c" * 64 + ".pdf"


def _ins(c, table, **v):
    cols = ", ".join(v)
    return c.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(v))})", list(v.values())).lastrowid


def _init(path):
    book_merge._prepare(path, None)  # şema + modül tabloları (zekât, Gelen kutusu…)


def _common(c, txn_cat="Market"):
    """İki defterde de aynı olan: hesap, ekstre (aynı dosya), banka hareketi, elle kira kaydı, borç + ilk ödemesi."""
    st = _ins(c, "statements", file_hash=PDF, filename="ekstre.pdf", bank="VakıfBank", card_no="5555 **** **** 1234",
              period_end="2026-09-15", due_date="2026-09-25", total_debt=300.0, uploaded_at="2026-09-16 10:00:00")
    t1 = _ins(c, "transactions", statement_id=st, txn_date="2026-09-01", description="ORNEK MARKET", amount=200.0,
              txn_type="harcama", category=txn_cat)
    _ins(c, "transactions", statement_id=st, txn_date="2026-09-03", description="ORNEK KAFE", amount=100.0,
         txn_type="harcama", category="Yeme-İçme")
    return st, t1


def _source(path):
    _init(path)
    c = sqlite3.connect(path)
    acc = _ins(c, "accounts", name="Banka A", kind="banka", balance=1000.0, as_of="2026-09-30", source="dokum",
               updated_at="2026-09-30 12:00:00")
    _common(c)
    e1 = _ins(c, "entries", entry_date="2026-09-10", month="2026-09", description="MARKET ALISVERIS", amount=100.0,
              kind="gider", category="Market", import_hash="h1", account_id=acc, created_at="2026-09-11 08:00:00")
    _ins(c, "splits", entry_id=e1, amount=20.0, category="Sigara")
    _ins(c, "entries", entry_date="2026-09-12", month="2026-09", description="Kira", amount=5000.0, kind="gider",
         category="Kira", account_id=acc, created_at="2026-09-12 09:00:00")
    d1 = _ins(c, "debts", person="Ali Örnek", direction="alacak", asset="TRY", amount=1000.0, paid_amount=200.0,
              debt_date="2026-08-01", created_at="2026-08-01 10:00:00")
    _ins(c, "debt_payments", debt_id=d1, amount=200.0, paid_at="2026-09-01")
    _ins(c, "own_transfers", transfer_date="2026-09-05", amount=-300.0, account="Banka A", description="Kendi hesabına",
         import_hash="o1")
    _ins(c, "rules", pattern="ORNEK KAFE", category="Yeme-İçme", priority=10)
    for k, v in (("cfg:MAIL_PASSWORD", "gizli-sifre"), ("telegram_chat_id", "42"), ("auth:password_hash", "x"),
                 ("pdf_passwords", json.dumps(["p1"])), ("zakat:madhhab", "hanefi"), ("ui:owner", "{}")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
    _ins(c, "stored_files", kind="uploads", name=f"{PDF}.pdf", size=9)
    c.commit()
    c.close()


def _target(path):
    _init(path)
    c = sqlite3.connect(path)
    acc = _ins(c, "accounts", name="Banka A", kind="banka", balance=1500.0, as_of="2026-10-25", source="dokum",
               updated_at="2026-10-25 12:00:00")
    _ins(c, "accounts", name="Nakit", kind="nakit", balance=200.0, as_of="2026-10-21", source="elle",
         updated_at="2026-10-21 12:00:00")
    _, t1 = _common(c, txn_cat="Market")
    c.execute("UPDATE transactions SET category = 'Ev', category_manual = 1 WHERE id = ?", (t1,))
    # ortak hareket: hedefte not eklenmiş (kaynakta boş → doldurulur), kategori farklı (çakışma)
    _ins(c, "entries", entry_date="2026-09-10", month="2026-09", description="MARKET ALISVERIS", amount=100.0,
         kind="gider", category="Gıda", note="hedef notu", import_hash="h1", account_id=acc,
         created_at="2026-09-11 08:00:00")
    _ins(c, "entries", entry_date="2026-09-12", month="2026-09", description="Kira", amount=5000.0, kind="gider",
         category="Kira", account_id=acc, created_at="2026-09-12 09:00:00")
    # olası kopya: aynı gün aynı tutar başka açıklama
    _ins(c, "entries", entry_date="2026-09-12", month="2026-09", description="Kira ödemesi", amount=5000.0,
         kind="gider", category="Kira", created_at="2026-09-12 09:05:00")
    # since öncesi, kaynakta yok → UYARI
    _ins(c, "entries", entry_date="2026-08-05", month="2026-08", description="Eski kayıt", amount=75.0, kind="gider",
         category="Diğer", created_at="2026-08-05 10:00:00")
    # since sonrası: telefondan fişli kahve (fiş kalemi + bölme), dökümden gelen hareket
    e3 = _ins(c, "entries", entry_date="2026-10-25", month="2026-10", description="Kahve", amount=50.0, kind="gider",
              category="Yeme-İçme", receipt_file=RECEIPT_T, created_at="2026-10-25 09:00:00", created_by=1)
    _ins(c, "receipt_items", entry_id=e3, purchased_at="2026-10-25", name="Latte", product_key="LATTE", qty=1,
         amount=40.0, unit_price=40.0)
    _ins(c, "receipt_items", entry_id=e3, purchased_at="2026-10-25", name="Kurabiye", product_key="KURABIYE", qty=1,
         amount=10.0, unit_price=10.0)
    _ins(c, "splits", entry_id=e3, amount=10.0, category="Atıştırmalık")
    doc = _ins(c, "bank_documents", file_name=BANKDOC_T, filename="dokum.pdf", bank="Banka A", account_id=acc, size=5,
               uploaded_at="2026-10-23 10:00:00")
    _ins(c, "entries", entry_date="2026-10-22", month="2026-10", description="MAAS", amount=20000.0, kind="gelir",
         category="Maaş", import_hash="h4", account_id=acc, bank_doc_id=doc, created_at="2026-10-23 10:00:01")
    # ortak borç + since sonrası ikinci ödeme; yeni borç + ödeme hareketi (entries.debt_id → debts: ertelenen bağ)
    d1 = _ins(c, "debts", person="Ali Örnek", direction="alacak", asset="TRY", amount=1000.0, paid_amount=500.0,
              debt_date="2026-08-01", created_at="2026-08-01 10:00:00")
    _ins(c, "debt_payments", debt_id=d1, amount=200.0, paid_at="2026-09-01")
    _ins(c, "debt_payments", debt_id=d1, amount=300.0, paid_at="2026-10-26")
    d2 = _ins(c, "debts", person="Veli Örnek", direction="borc", asset="TRY", amount=500.0, paid_amount=100.0,
              debt_date="2026-10-21", created_at="2026-10-21 10:00:00")
    e7 = _ins(c, "entries", entry_date="2026-10-27", month="2026-10", description="Veli'ye ödeme", amount=100.0,
              kind="gider", category="Borç / Alacak", debt_id=d2, created_at="2026-10-27 10:00:00")
    _ins(c, "debt_payments", debt_id=d2, amount=100.0, paid_at="2026-10-27", entry_id=e7)
    _ins(c, "card_payments", pay_date="2026-10-24", amount=300.0, card_last4="1234", account_id=acc,
         description="Kart ödemesi", import_hash="cp1", created_at="2026-10-24 10:00:00")
    _ins(c, "own_transfers", transfer_date="2026-09-05", amount=-300.0, account="Banka A", description="Kendi hesabına",
         import_hash="o1")
    c.execute("INSERT INTO shared_inbox (created_at, filename, kind, doc) VALUES (?, ?, ?, ?)",
              ("2026-10-28 10:00:00", "fis.jpg", "receipt", json.dumps({"total": 12.5})))
    for k, v in (("modules", json.dumps({"zekat": False})), ("onboarding_dismissed", "1"), ("ui:1", "{\"simple\": true}"),
                 ("pdf_passwords", json.dumps(["p2"])), ("posta:senders", "[]")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
    _ins(c, "stored_files", kind="receipts", name=RECEIPT_T, size=3)
    _ins(c, "stored_files", kind="statements-bank", name=BANKDOC_T, size=5)
    c.commit()
    c.close()


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _dump(path, skip=("integrity:",)):
    c = sqlite3.connect(path)
    out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
        rows = c.execute(f'SELECT * FROM "{t}"').fetchall()
        if t == "settings":
            rows = [r for r in rows if not str(r[0]).startswith(skip)]
        if t == "stored_files":
            rows = [r[:4] for r in rows]
        out[t] = sorted(map(repr, rows))
    c.close()
    return out


@pytest.fixture()
def books(tmp_path):
    src = tmp_path / "kaynak" / "ekstre.db"
    src.parent.mkdir()
    _source(str(src))
    files = tmp_path / "kaynak-dosyalar"
    (files / "uploads").mkdir(parents=True)
    (files / "uploads" / f"{PDF}.pdf").write_bytes(b"FIXTURE")
    # hedef: ana defter (users id 1, db_file NULL) — conftest'in yalıtılmış DB_PATH'i
    _target(db.DB_PATH)
    os.makedirs(os.path.join(os.path.dirname(db.DB_PATH), "receipts"), exist_ok=True)
    system.create_user("sahip@example.com", None, role="admin", main_db=True)
    return str(src), str(files)


def _q(path, sql, *a):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, a)]
    finally:
        c.close()


def test_dry_run_reports_and_leaves_target(books, tmp_path):
    src, files = books
    before = _sha(db.DB_PATH)
    out = tmp_path / "birlesik.db"
    r = book_merge.run(src, files, SINCE, target_user="sahip@example.com", out=str(out))
    assert r["target_unchanged"] and _sha(db.DB_PATH) == before
    assert not os.path.exists(os.path.join(os.path.dirname(db.DB_PATH), "uploads", f"{PDF}.pdf"))  # dosya da yok
    m = str(out)
    # ekstre ve işlemleri tek; hedefte elle verilen kategori uygulanır
    assert len(_q(m, "SELECT * FROM statements")) == 1
    txns = _q(m, "SELECT * FROM transactions ORDER BY id")
    assert len(txns) == 2 and txns[0]["category"] == "Ev" and txns[0]["category_manual"] == 1
    ents = {e["description"]: e for e in _q(m, "SELECT * FROM entries")}
    assert sorted(ents) == sorted(["MARKET ALISVERIS", "Kira", "Kira ödemesi", "Eski kayıt", "Kahve", "MAAS",
                                   "Veli'ye ödeme"])
    # ortak hareket tek: kaynaktaki tutulur, boş not hedeften doldurulur, farklı kategori çakışma (kaynak kalır)
    assert ents["MARKET ALISVERIS"]["note"] == "hedef notu" and ents["MARKET ALISVERIS"]["category"] == "Market"
    assert any(x["field"] == "category" and x["target"] == "Gıda" for x in r["edit_conflicts"])
    # fiş kalemleri ve bölme taşınan kayda bağlı; kaynaktaki bölme yerinde
    kahve = ents["Kahve"]["id"]
    assert {i["name"] for i in _q(m, "SELECT * FROM receipt_items WHERE entry_id = ?", kahve)} == {"Latte", "Kurabiye"}
    assert len(_q(m, "SELECT * FROM splits WHERE entry_id = ?", kahve)) == 1
    assert len(_q(m, "SELECT * FROM splits WHERE entry_id = ?", ents["MARKET ALISVERIS"]["id"])) == 1
    # döküm belgesi yeniden bağlandı
    doc = _q(m, "SELECT id FROM bank_documents WHERE file_name = ?", BANKDOC_T)[0]["id"]
    assert ents["MAAS"]["bank_doc_id"] == doc
    # borçlar: ortak borç tek, ödemesi iki (biri taşındı), ödenen tutar yeniden hesaplandı; yeni borç ve bağlı hareket
    debts = {d["person"]: d for d in _q(m, "SELECT * FROM debts")}
    assert len(debts) == 2 and debts["Ali Örnek"]["paid_amount"] == 500.0
    assert len(_q(m, "SELECT * FROM debt_payments WHERE debt_id = ?", debts["Ali Örnek"]["id"])) == 2
    assert ents["Veli'ye ödeme"]["debt_id"] == debts["Veli Örnek"]["id"]
    pay = _q(m, "SELECT * FROM debt_payments WHERE debt_id = ?", debts["Veli Örnek"]["id"])
    assert len(pay) == 1 and pay[0]["entry_id"] == ents["Veli'ye ödeme"]["id"]
    # virman tek, kart ödemesi taşındı, Gelen kutusu taşındı
    assert len(_q(m, "SELECT * FROM own_transfers")) == 1
    assert len(_q(m, "SELECT * FROM card_payments")) == 1
    assert len(_q(m, "SELECT * FROM shared_inbox")) == 1
    # hesaplar: as_of yeni olan (hedef) bakiye; hedefe özgü hesap eklendi
    accs = {a["name"]: a for a in _q(m, "SELECT * FROM accounts")}
    assert accs["Banka A"]["balance"] == 1500.0 and accs["Banka A"]["as_of"] == "2026-10-25" and "Nakit" in accs
    # ayarlar: kendi-sunucusu anahtarları yok, hazır sürüm anahtarları hedeften, listeler birleşti
    st = {s["key"]: s["value"] for s in _q(m, "SELECT * FROM settings")}
    assert not any(k.startswith("cfg:") for k in st) and "telegram_chat_id" not in st and "auth:password_hash" not in st
    assert "ui:owner" not in st and st["ui:1"] == "{\"simple\": true}" and st["onboarding_dismissed"] == "1"
    assert json.loads(st["pdf_passwords"]) == ["p1", "p2"] and st["zakat:madhhab"] == "hanefi"
    assert "cfg:MAIL_PASSWORD" in r["settings"]["dropped_source"]
    assert "gizli-sifre" not in json.dumps(r, ensure_ascii=False, default=str)  # rapora değer yazılmaz
    # rapor: since kayıtları, since öncesi uyarı, olası kopya
    since = {(x["table"], x["description"]) for x in r["moved_since"]}
    assert {("entries", "Kahve"), ("entries", "MAAS"), ("debts", "Veli Örnek"), ("debt_payments", "")} <= since
    pre = {x["description"] for x in r["pre_since_warning"]}
    assert "Eski kayıt" in pre and "Kira ödemesi" in pre
    assert r["possible_duplicates"] and r["possible_duplicates"][0]["target"]["description"] == "Kira ödemesi"
    assert r["tables"]["entries"]["paired"] == 2
    # dosyalar: kaynağın ekstre PDF'i yüklenecek; hedefin dosyaları dizinde
    assert r["files"]["yuklenecek"] == 1
    files_idx = {(f["kind"], f["name"]) for f in _q(m, "SELECT * FROM stored_files")}
    assert {("uploads", f"{PDF}.pdf"), ("receipts", RECEIPT_T), ("statements-bank", BANKDOC_T)} <= files_idx
    assert r["sqlite"]["integrity_check"] == "ok"
    assert "TABLOLAR" in book_merge.render(r)


def test_skip_pre_since(books):
    src, files = books
    r = book_merge.run(src, files, SINCE, target_user="sahip@example.com", skip_pre_since=True)
    pre = {x["description"]: x for x in r["pre_since_warning"]}
    assert pre["Eski kayıt"]["moved"] is False


def test_apply_then_idempotent(books):
    src, files = books
    r1 = book_merge.run(src, files, SINCE, target_user="sahip@example.com", apply=True)
    assert os.path.isfile(r1["backup"]) and "pre-merge-" in r1["backup"]
    assert r1["upload"]["uploaded"] == 1
    assert os.path.isfile(os.path.join(db.UPLOAD_DIR, f"{PDF}.pdf"))
    assert "integrity_run" in r1
    first = _dump(db.DB_PATH)
    assert len(_q(db.DB_PATH, "SELECT * FROM entries")) == 7
    r2 = book_merge.run(src, files, SINCE, target_user="sahip@example.com", apply=True)
    assert r2["upload"]["uploaded"] == 0 and r2["files"]["zaten_var"] >= 1
    assert _dump(db.DB_PATH) == first  # ikinci birleştirme hiçbir şeyi değiştirmez
    assert r2["tables"]["entries"]["paired"] == 7 - r2["tables"]["entries"]["moved_since"] - r2["tables"]["entries"]["moved_pre"]


def test_cli_dry_run_json(books, capsys):
    src, _ = books
    before = _sha(db.DB_PATH)
    assert book_merge.main(["--source", src, "--source-files", "-", "--target-user", "sahip@example.com",
                            "--since", SINCE, "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["mode"] == "dry-run" and r["files"]["eksik"] == 1 and _sha(db.DB_PATH) == before
    assert book_merge.main(["--source", src, "--source-files", "-", "--target-user", "yok", "--since", SINCE]) == 1


def test_synced_manual_entry_and_hash_rules(tmp_path):
    """Hedefte eşitlemeyle parmak izi almış kayıt ↔ kaynaktaki aynı elle kayıt: tek (iz kaynağa geçer). İkisinin de
    farklı izi var ve hesap farklı: aynı gün/tutar/açıklama olsa da ayrı hareket sayılır."""
    src, tgt = str(tmp_path / "s" / "ekstre.db"), str(tmp_path / "t" / "ekstre.db")
    for p in (src, tgt):
        os.makedirs(os.path.dirname(p))
        _init(p)
    c = sqlite3.connect(src)
    a1 = _ins(c, "accounts", name="Banka A", balance=0, as_of="2026-09-01")
    _ins(c, "accounts", name="Banka B", balance=0, as_of="2026-09-01")
    _ins(c, "entries", entry_date="2026-09-20", month="2026-09", description="Market", amount=80.0, kind="gider",
         created_at="2026-09-20 10:00:00")
    _ins(c, "entries", entry_date="2026-09-21", month="2026-09", description="EFT", amount=10.0, kind="gider",
         import_hash="eski", account_id=a1, created_at="2026-09-21 10:00:00")
    c.commit()
    c.close()
    c = sqlite3.connect(tgt)
    _ins(c, "accounts", name="Banka A", balance=0, as_of="2026-09-01")
    b2 = _ins(c, "accounts", name="Banka B", balance=0, as_of="2026-09-01")
    _ins(c, "entries", entry_date="2026-09-20", month="2026-09", description="Market", amount=80.0, kind="gider",
         import_hash="yeni", created_at="2026-09-20 10:00:00")
    _ins(c, "entries", entry_date="2026-09-21", month="2026-09", description="EFT", amount=10.0, kind="gider",
         import_hash="baska", account_id=b2, created_at="2026-10-21 10:00:00")
    c.commit()
    c.close()
    out = str(tmp_path / "m.db")
    r = book_merge.run(src, None, SINCE, target_db=tgt, out=out)
    ents = _q(out, "SELECT description, import_hash FROM entries ORDER BY id")
    assert ents == [{"description": "Market", "import_hash": "yeni"}, {"description": "EFT", "import_hash": "eski"},
                    {"description": "EFT", "import_hash": "baska"}]
    assert r["tables"]["entries"]["paired"] == 1 and r["target_unchanged"]
