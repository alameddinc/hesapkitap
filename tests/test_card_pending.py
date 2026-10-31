"""Özet: ekstresi henüz gelmemiş kartla elle girilmiş harcama (totals.card_pending) — 'Kart ₺742 (ekstresi bekleniyor)'."""


def test_card_pending_total(client):
    for amt, pay in ((650.0, "kredi_karti"), (100.0, "nakit")):
        assert client.post("/api/entries", json={"entry_date": "2026-10-20", "description": "Market", "amount": amt,
                                                 "kind": "gider", "category": "Market & Gıda", "payment": pay,
                                                 "card_last4": "4444" if pay == "kredi_karti" else None}).status_code == 200
    t = client.get("/api/summary?month=2026-10").json()["totals"]
    assert t["card_pending"] == 650.0 and t["manual_expense"] == 750.0
