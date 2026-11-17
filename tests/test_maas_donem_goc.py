"""Maaş dönem ataması GÖÇÜ (db.salary_funding_periods, _once "salary_funding_v1") ve etkisini ölçen salt okuma betiği
(scripts/olc_maas_fonlama.py). Eski kural: ay dönümü 17, maaş 15 → 15 Kas maaşı 2026-11'e bağlı; yeni kural: fonladığı
2026-12'nin. Göç eski veriyi (kayıt, atlanan dönem, zam, aya özel tutar, başlangıç/bitiş) bir dönem ileri alır.
Tutarlar, kişiler uydurmadır."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

SALARY = 40000.0


def _old_entry(db, rid, d, month, skipped=0, amount=SALARY):
    """Eski kuralın yazdığı hâl: create_entry dönem hesaplamasın diye doğrudan SQL."""
    with db.connect() as c:
        return c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, recurring_id, "
                         "skipped) VALUES (?, ?, 'Maaş', ?, 'gelir', 'Maaş', ?, ?)",
                         (d, month, 0 if skipped else amount, rid, skipped)).lastrowid


def _months(db):
    with db.connect() as c:
        return {r[0]: r[1] for r in c.execute("SELECT id, month FROM entries")}


@pytest.fixture()
def old17(tmpdb):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    rid = tmpdb.create_recurring({"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                                  "day_of_month": 15, "start_month": "2026-09", "end_month": "2027-03"})
    ids = {
        "eyl": _old_entry(tmpdb, rid, "2026-09-15", "2026-09"),
        "eki": _old_entry(tmpdb, rid, "2026-10-15", "2026-10"),
        "kas_erken": _old_entry(tmpdb, rid, "2026-11-13", "2026-11"),
        "ara_atla": _old_entry(tmpdb, rid, "2026-12-15", "2026-12", skipped=1),
        "oca_gec": _old_entry(tmpdb, rid, "2027-01-18", "2027-01"),
    }
    with tmpdb.connect() as c:
        c.execute("INSERT INTO salary_changes (recurring_id, from_month, gross, reason) VALUES (?, '2026-11', 60000, 'zam')",
                  (rid,))
        c.executemany("INSERT INTO recurring_overrides (recurring_id, month, amount) VALUES (?, ?, ?)",
                      [(rid, "2026-11", 41000.0), (rid, "2026-12", 42000.0)])
        c.execute("DELETE FROM settings WHERE key = 'salary_funding_v1'")
    return tmpdb, rid, ids


def test_migration_moves_everything_one_period(old17):
    db, rid, ids = old17
    n = db.salary_funding_periods()
    m = _months(db)
    assert n == 5
    assert [m[ids[k]] for k in ("eyl", "eki", "kas_erken", "ara_atla", "oca_gec")] == \
        ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02"]
    with db.connect() as c:
        assert [r[0] for r in c.execute("SELECT from_month FROM salary_changes WHERE recurring_id = ?", (rid,))] == ["2026-12"]
        assert sorted(tuple(r) for r in c.execute("SELECT month, amount FROM recurring_overrides WHERE recurring_id = ?",
                                                  (rid,))) == [("2026-12", 41000.0), ("2027-01", 42000.0)]
        assert tuple(c.execute("SELECT start_month, end_month FROM recurring WHERE id = ?", (rid,)).fetchone()) == \
            ("2026-10", "2027-04")
    # tarihler (bakiye) değişmedi; her dönemde tek kayıt
    with db.connect() as c:
        assert [r[0] for r in c.execute("SELECT entry_date FROM entries ORDER BY id")] == \
            ["2026-09-15", "2026-10-15", "2026-11-13", "2026-12-15", "2027-01-18"]
    assert len(set(m.values())) == len(m)


def test_migration_runs_once(old17):
    db, _rid, ids = old17
    db._once("salary_funding_v1", db.salary_funding_periods)
    first = _months(db)
    db._once("salary_funding_v1", db.salary_funding_periods)
    db.init_db()
    assert _months(db) == first and first[ids["eki"]] == "2026-11"


def test_migration_leaves_in_period_salary_and_other_items(tmpdb):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    sal20 = tmpdb.create_recurring({"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                                    "day_of_month": 20, "start_month": "2026-09"})
    kira = tmpdb.create_recurring({"description": "Kira geliri", "amount": 9000.0, "kind": "gelir",
                                   "category": "Kira Geliri", "day_of_month": 15, "start_month": "2026-09"})
    a = _old_entry(tmpdb, sal20, "2026-10-20", "2026-11")
    b = _old_entry(tmpdb, kira, "2026-10-15", "2026-10", amount=9000.0)
    assert tmpdb.salary_funding_periods() == 0
    assert _months(tmpdb) == {a: "2026-11", b: "2026-10"}


def test_migration_cycle1_month_end(tmpdb):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(1)
    rid = tmpdb.create_recurring({"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                                  "day_of_month": 31, "start_month": "2026-09"})
    ids = [_old_entry(tmpdb, rid, d, d[:7]) for d in ("2026-09-30", "2026-10-30", "2026-11-30")]
    assert tmpdb.salary_funding_periods() == 3
    m = _months(tmpdb)
    assert [m[i] for i in ids] == ["2026-10", "2026-11", "2026-12"]


def test_migration_keeps_structure_when_dates_collide(old17):
    """Aynı döneme ikinci (ek) ödeme bağlıysa tarih kuralı çakışır: kayıtlar eski yapıyla (+1) taşınır."""
    db, rid, ids = old17
    extra = _old_entry(db, rid, "2026-10-16", "2026-10", amount=500.0)
    db.salary_funding_periods()
    m = _months(db)
    assert m[ids["eki"]] == m[extra] == "2026-11" and m[ids["kas_erken"]] == "2026-12"


def _script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "olc_maas_fonlama.py"
    spec = importlib.util.spec_from_file_location("olc_maas_fonlama", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_measure_script_reports_and_never_writes(old17, capsys):
    db, rid, ids = old17
    src = db.db_path()
    h0 = hashlib.sha256(Path(src).read_bytes()).hexdigest()
    mod = _script()
    r = mod.measure(src)
    assert [(m["entry_id"], m["now"], m["new"]) for m in r["moves"]] == [
        (ids["eyl"], "2026-09", "2026-10"), (ids["eki"], "2026-10", "2026-11"), (ids["kas_erken"], "2026-11", "2026-12"),
        (ids["ara_atla"], "2026-12", "2027-01"), (ids["oca_gec"], "2027-01", "2027-02")]
    assert len(r["salary_changes"]) == 1 and r["overrides"] == 2 and len(r["recurring"]) == 1
    assert r["doubles_before"] == r["doubles_after"] == 0
    # kaynak dosya baytı baytına aynı; göç bayrağı yazılmadı; uygulama yine kaynağa bakıyor
    assert hashlib.sha256(Path(src).read_bytes()).hexdigest() == h0
    assert db.db_path() == src and not db.get_setting("salary_funding_v1")
    assert _months(db)[ids["eki"]] == "2026-10"
    mod.main([src])
    out = capsys.readouterr().out
    assert "dönemi değişecek maaş kaydı 5" in out and "TOPLAM" in out and str(SALARY) not in out


def test_measure_script_skips_migrated(old17):
    db, *_ = old17
    db.set_setting("salary_funding_v1", "1")
    assert _script().measure(db.db_path())["skip"] == "göç zaten uygulanmış"
