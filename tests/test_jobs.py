import os
import sqlite3
from datetime import date, datetime

import pytest

from tests.conftest import STATEMENTS, fixture_bytes


def test_backup_roundtrip_and_prune(tmpdb):
    from app import backup

    tmpdb.set_setting("x", "1")
    old = backup.make_backup(date(2026, 8, 1))
    path = backup.make_backup(date(2026, 10, 19))
    assert not os.path.exists(old)  # 30 günden eski silindi
    assert sqlite3.connect(path).execute("SELECT value FROM settings WHERE key='x'").fetchone() == ("1",)

    blob = backup.encrypt(open(path, "rb").read(), "parola")
    assert backup.decrypt(blob, "parola") == open(path, "rb").read()
    with pytest.raises(Exception):
        backup.decrypt(blob, "yanlis")


def test_reminders(tmpdb):
    from app import jobs
    from app.parsers import parse_statement

    for n in STATEMENTS:
        tmpdb.save_statement(fixture_bytes(n), n, parse_statement(fixture_bytes(n)))
    # 4543***1234 son ödeme 23.10 → 20.10'da "3 gün kaldı"
    msgs = jobs.reminders(date(2026, 10, 20))
    assert any("4543***1234" in m and "3 gün" in m for m in msgs)
    assert not any("4543***1234" in m for m in jobs.reminders(date(2026, 10, 21)))  # 2 gün kala yok
    # sonraki kesim 13.11 → 16.11'de "ekstreyi yükle"
    assert any("ekstresi 13.11" in m for m in jobs.reminders(date(2026, 11, 16)))
    assert any("dönemi bitti" in m for m in jobs.reminders(date(2026, 11, 1)))  # ay dönümünde özet


def test_jobs_run_once_per_day(tmpdb):
    from app import jobs

    now = datetime(2026, 10, 19, 10, 0)
    assert {n for n, _ in jobs.due_jobs(now)} == {"backup", "remind"}
    tmpdb.set_setting("job:backup", "2026-10-19")
    assert {n for n, _ in jobs.due_jobs(now)} == {"remind"}
    assert [n for n, _ in jobs.due_jobs(datetime(2026, 10, 20, 2, 0))] == []  # 03:00'ten önce yedek yok


def test_period_report_on_cycle_day(tmpdb):
    from datetime import date

    from app import finance, jobs

    tmpdb.set_cycle_start_day(20)
    tmpdb.create_entry({"entry_date": "2026-10-05", "description": "Maaş", "amount": 1000, "kind": "gelir", "category": "Maaş"})
    tmpdb.create_entry({"entry_date": "2026-10-06", "description": "Market", "amount": 300, "kind": "gider", "category": "Market"})
    assert not any("dönemi bitti" in m for m in jobs.reminders(date(2026, 10, 19)))
    msg = next(m for m in jobs.reminders(date(2026, 10, 20)) if "dönemi bitti" in m)
    assert "Ekim 2026" in msg and "Market" in msg and "Bu ay plan" in msg
    assert finance.period_report("2026-10").count("\n") >= 3
