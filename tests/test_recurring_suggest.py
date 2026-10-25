"""Tekrarlayan hareketlerden sabit kalem önerisi (kira artışı, bölünmüş ödeme, sonraki hareketin bağlanması)."""
from datetime import date

from app import finance

TODAY = date(2026, 10, 25)


def _e(client, d, amount, who="Ev Sahibi", cat="Kira & Aidat", kind="gider", desc=None):
    client.post("/api/entries", json={"entry_date": d, "description": desc or f"{who} · kira", "amount": amount, "kind": kind,
                                      "category": cat, "payment": "havale" if kind == "gider" else None, "counterparty": who})


def test_rent_suggested_accepted_and_new_payment_attaches(client, tmpdb):
    for m, amt in [("05", 30000), ("06", 30000), ("07", 35000), ("08", 35000), ("09", 35000)]:
        _e(client, f"2026-{m}-17", amt)
    _e(client, "2026-10-17", 5000)
    _e(client, "2026-10-18", 30000)  # ekim ikiye bölünmüş
    for m in ("07", "08", "09", "10"):  # ATM: önerilmez
        _e(client, f"2026-{m}-03", 2000, who=None, cat="Nakit Çekim", desc="ATM PARA CEKME")
    _e(client, "2026-09-05", 1200, who="Arkadaş", cat="Kategorisiz")  # tek sefer
    sug = finance.recurring_suggestions(TODAY)
    assert len(sug) == 1
    s = sug[0]
    assert s["amount"] == 35000 and s["day_of_month"] == 17 and s["description"] == "Kira (Ev Sahibi)"

    r = client.post("/api/recurring/suggestions/accept", json={"key": s["key"]}).json()
    rec = next(x for x in tmpdb.list_recurring() if x["id"] == r["id"])
    assert rec["match_key"] and rec["amount"] == 35000 and rec["start_month"] == "2026-05"
    linked = [e for e in tmpdb.list_entries() if e["recurring_id"] == r["id"]]
    assert len(linked) == 6  # her ay bir kayıt (ekimde büyük olan)
    assert finance.recurring_suggestions(TODAY) == []

    # kasımda dökümden gelen kira (açıklama farklı) aynı sabit kaleme bağlanır
    _e(client, "2026-11-17", 35000, desc="Ev Sahibi · kasım kirası")
    nov = next(e for e in tmpdb.list_entries() if e["entry_date"] == "2026-11-17")
    assert nov["recurring_id"] == r["id"]


def test_dismiss(client, tmpdb):
    for m in ("07", "08", "09", "10"):
        _e(client, f"2026-{m}-15", 1055, who=None, cat="İnternet & TV", desc=f"TURKNET {m}/2026 FATURA")
    s = finance.recurring_suggestions(TODAY)
    assert len(s) == 1 and s[0]["amount"] == 1055
    client.post("/api/recurring/suggestions/dismiss", json={"key": s[0]["key"]})
    assert finance.recurring_suggestions(TODAY) == []
