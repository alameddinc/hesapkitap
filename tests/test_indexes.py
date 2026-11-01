"""Göç sonrası indeksler (C1): yeni ve eski defterde kurulur, sık sorgular tam tarama yapmaz, sonuç değişmez."""
import sqlite3

from app import db

NAMES = {"ix_entries_import_hash", "ix_entries_account_date", "ix_card_payments_stmt"}


def _indexes(path: str) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def _plan(sql: str, args: tuple) -> str:
    with db.connect() as c:
        return " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))


def test_new_book_has_indexes(tmpdb):
    assert NAMES <= _indexes(tmpdb.DB_PATH)


def test_old_book_gets_indexes_after_migration(tmp_path):
    # account_id / import_hash / statement_id sonradan eklenen kolonlar: indeks SCHEMA'da olsaydı eski dosya açılmazdı
    p = str(tmp_path / "eski" / "ekstre.db")
    (tmp_path / "eski").mkdir()
    old = sqlite3.connect(p)
    old.executescript("CREATE TABLE entries (id INTEGER PRIMARY KEY, entry_date DATE NOT NULL, month TEXT NOT NULL, "
                      "description TEXT NOT NULL, amount REAL NOT NULL, kind TEXT NOT NULL, category TEXT);"
                      "CREATE TABLE card_payments (id INTEGER PRIMARY KEY, pay_date DATE NOT NULL, amount REAL NOT NULL, "
                      "card_last4 TEXT, account TEXT, description TEXT, import_hash TEXT UNIQUE);")
    old.close()
    db._ready_dbs.discard(p)
    with db.use_space(p, {"id": 9, "username": "eski", "role": "user"}):
        pass
    assert NAMES <= _indexes(p)


def test_hot_queries_use_indexes(tmpdb):
    assert "ix_entries_import_hash" in _plan("SELECT 1 FROM entries WHERE import_hash = ?", ("h1",))
    assert "ix_entries_account_date" in _plan(
        "SELECT id FROM entries WHERE import_hash LIKE 'dekont:%' AND account_id = ? AND entry_date = ?", (1, "2026-01-02"))
    assert "ix_card_payments_stmt" in _plan("SELECT * FROM card_payments WHERE statement_id = ?", (1,))
    # hesap bakiyesi: alt sınır da indeksten (çapa tarihi), yalnız üst sınır değil
    plan = _plan("SELECT 1 FROM entries WHERE account_id = ? AND skipped = 0 AND entry_date >= ? "
                 "AND (entry_date > ? OR (entry_date = ? AND id > ?)) AND entry_date <= ?",
                 (1, "2026-01-01", "2026-01-01", "2026-01-01", 0, "9999-12-31"))
    assert "ix_entries_account_date (account_id=? AND entry_date>? AND entry_date<?)" in plan


def test_balance_unchanged_around_anchor(tmpdb):
    # çapa günü: çapa kaydı ve öncesi bakiyenin içinde, aynı gün sonra eklenen ve sonraki günler eklenir
    with db.connect() as c:
        aid = c.execute("INSERT INTO accounts (name, kind, balance, as_of) VALUES ('Örnek Banka', 'banka', 1000, "
                        "'2026-03-10')").lastrowid

        def add(day, amount, kind):
            return c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, account_id) "
                             "VALUES (?, ?, 'örnek', ?, ?, ?)", (day, day[:7], amount, kind, aid)).lastrowid
        add("2026-03-09", 50, "gider")                    # çapadan önce: sayılmaz
        anchor = add("2026-03-10", 70, "gider")           # çapa kaydı: bakiyenin içinde
        add("2026-03-10", 20, "gelir")                    # aynı gün, çapadan sonra: sayılır
        add("2026-03-11", 5.5, "gider")                   # sonraki gün: sayılır
        c.execute("UPDATE accounts SET anchor_entry_id = ? WHERE id = ?", (anchor, aid))
        a = dict(c.execute("SELECT * FROM accounts WHERE id = ?", (aid,)).fetchone())
        assert db._account_balance(c, a) == 1014.5
        assert db._account_balance(c, a, until="2026-03-10") == 1020
