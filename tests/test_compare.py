"""Dönem karşılaştırması."""
from datetime import date, timedelta

from app import finance


def test_compare_same_day_weekdays_and_fixed_excluded(client, tmpdb):
    tmpdb.set_cycle_start_day(1)
    def spend(d, amt, cat="Market & Gıda"):
        tmpdb.create_entry({"entry_date": d, "description": "x", "amount": amt, "kind": "gider", "category": cat})
    # önceki 3 ay: her ayın 1–10'u günde 100; Kasım: günde 50
    for m in ("2026-08", "2026-09", "2026-10"):
        for day in range(1, 11):
            spend(f"{m}-{day:02d}", 100)
    for day in range(1, 11):
        spend(f"2026-11-{day:02d}", 50)
    spend("2026-11-05", 30000, "Kira & Aidat")                        # sabit gider: varsayılanda hariç
    c = finance.compare("2026-11", today=date(2026, 11, 10))
    assert c["day"] == 10 and c["length"] == 30
    assert c["at_day"]["current"] == 500 and c["at_day"]["prev"] == 1000 and c["at_day"]["avg3"] == 1000
    assert c["at_day"]["vs_prev"] == -50.0 and c["at_day"]["vs_avg3"] == -50.0
    assert any("daha az" in n for n in c["notes"])
    assert c["weeks"][0]["current"] == 350 and c["weeks"][0]["done"]
    assert finance.compare("2026-11", variable_only=False, today=date(2026, 11, 10))["at_day"]["current"] == 30500
    assert finance.period_bounds("2026-11") == (date(2026, 11, 1), date(2026, 11, 30))
    tmpdb.set_cycle_start_day(20)
    assert finance.period_bounds("2026-11") == (date(2026, 10, 20), date(2026, 11, 19))
