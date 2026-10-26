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
    assert {n for n, _ in jobs.due_jobs(now)} == {"backup", "remind", "wallet"}
    tmpdb.set_setting("job:backup", "2026-10-19")
    assert {n for n, _ in jobs.due_jobs(now)} == {"remind", "wallet"}
    assert [n for n, _ in jobs.due_jobs(datetime(2026, 10, 20, 0, 30))] == []  # gece yarısı hemen bir şey çalışmaz


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


def test_month_digest_and_card(client, tmpdb, monkeypatch):
    from datetime import date

    from app import finance

    client.post("/api/entries", json={"entry_date": "2026-09-10", "description": "Maaş", "amount": 50000, "kind": "gelir"})
    client.post("/api/entries", json={"entry_date": "2026-09-12", "description": "Market", "amount": 5000, "kind": "gider",
                                      "category": "Market & Gıda", "payment": "nakit"})
    client.post("/api/entries", json={"entry_date": "2026-08-12", "description": "Market", "amount": 4000, "kind": "gider",
                                      "category": "Market & Gıda", "payment": "nakit"})
    d = finance.month_digest("2026-09", today=date(2026, 10, 2))
    assert (d["income"], d["expense"], d["savings_rate"], d["fees"]) == (50000, 5000, 90, 0)
    assert d["top"][0] == {"name": "Market & Gıda", "amount": 5000, "change": 25} and d["label"] == "Eylül 2026"
    report = finance.period_report("2026-09")
    assert "Faiz ödemedin" in report and "%90" in report

    # Özet kartı: yeni dönemin ilk haftası, kapatılınca gelmez
    import app.api.main as m

    class D(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 3)

    monkeypatch.setattr(m, "date", D)
    monkeypatch.setattr(finance, "current_period", lambda today=None: "2026-10")
    assert client.get("/api/digest").json()["month"] == "2026-09"
    client.post("/api/digest/dismiss")
    assert client.get("/api/digest").json() is None


def test_year_summary(client, tmpdb):
    from app import finance

    for m, inc, exp in (("2025-06", 40000, 10000), ("2026-02", 50000, 20000), ("2026-03", 50000, 30000)):
        client.post("/api/entries", json={"entry_date": f"{m}-10", "description": "Maaş", "amount": inc, "kind": "gelir"})
        client.post("/api/entries", json={"entry_date": f"{m}-12", "description": "Kira", "amount": exp, "kind": "gider",
                                          "category": "Kira & Aidat", "payment": "havale"})
    y = finance.year_summary(2026)
    assert (y["income"], y["expense"], y["months"], y["avg_expense"], y["savings_rate"]) == (100000, 50000, 2, 25000, 50)
    assert y["highest"]["month"] == "2026-03" and y["lowest"]["month"] == "2026-02"
    assert y["categories"][0] == {"name": "Kira & Aidat", "amount": 50000, "count": 2, "share": 100}
    assert y["expense_change"] == 400 and y["years"] == [2026, 2025]
    assert client.get("/api/year-summary?year=2025").json()["income"] == 40000
