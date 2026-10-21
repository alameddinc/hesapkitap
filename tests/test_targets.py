"""Birikim hedefi."""
from datetime import date

from app import finance


def test_target_progress_pace_and_eta(client, tmpdb):
    tmpdb.set_cycle_start_day(1)
    for m, inc, exp in (("2026-08", 10000, 6000), ("2026-09", 10000, 5000), ("2026-10", 10000, 4000)):
        tmpdb.create_entry({"entry_date": f"{m}-05", "description": "Maaş", "amount": inc, "kind": "gelir", "category": "Maaş"})
        tmpdb.create_entry({"entry_date": f"{m}-06", "description": "Gider", "amount": exp, "kind": "gider", "category": "Market"})
    tmpdb.create_target("Tatil", 20000, "2027-04-30", "2026-10", already=2000)
    t = finance.targets(date(2026, 11, 2))[0]
    # biriken: başta 2000 + Ekim neti 6000 (+ Kasım 0) = 8000; hız: Ağu–Eki ort. (4000+5000+6000)/3 = 5000
    assert (t["saved"], t["left"], t["pace"]) == (8000, 12000, 5000)
    assert t["months_left"] == 5 and t["need_per_month"] == 2400 and t["on_track"]
    assert t["eta"] == "2027-02"       # 12000 / 5000 → 3 ay
    assert client.get("/api/targets").json()[0]["name"] == "Tatil"
