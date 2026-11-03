"""Fatura ödeme dekontu: okumada paid/paid_at/payment; /api/bills paid_at ile ödenmiş açılır (gider kaydıyla)."""
from app import db, receipt


def test_bill_paid_from_model_output():
    assert receipt._bill_paid({"paid": True, "paid_at": "2026-11-03", "pay_method": "havale"}) == \
        {"paid": True, "paid_at": "2026-11-03", "payment": "havale"}
    assert receipt._bill_paid({"paid": "true", "pay_method": "bilinmiyor"}) == {"paid": True, "paid_at": None, "payment": None}
    assert receipt._bill_paid({"paid": False, "paid_at": "2026-11-03"}) == {"paid": False, "paid_at": None, "payment": None}
    assert receipt._bill_paid({}) == {"paid": False, "paid_at": None, "payment": None}


def test_document_bill_carries_paid():
    doc = receipt._doc_of({"doc_type": "fatura", "merchant": "Örnek Su", "total": 250.0, "paid": True,
                           "paid_at": "2026-11-02", "pay_method": "kredi_karti"}, ["Diğer"])
    assert doc["type"] == "bill" and doc["paid"] is True and doc["paid_at"] == "2026-11-02" and doc["payment"] == "kredi_karti"


def test_create_bill_paid_opens_paid_with_entry(client):
    r = client.post("/api/bills", json={"provider": "Örnek Elektrik", "amount": 410.5, "due_date": "2026-11-10",
                                        "paid_at": "2026-11-03", "payment": "havale"})
    assert r.status_code == 200 and r.json()["entry_id"]
    b = db.get_bill(r.json()["id"])
    assert b["paid_at"] == "2026-11-03" and b["paid_amount"] == 410.5 and b["entry_id"] == r.json()["entry_id"]


def test_create_bill_unpaid_unchanged(client):
    r = client.post("/api/bills", json={"provider": "Örnek Doğalgaz", "amount": 300, "due_date": "2026-11-15"})
    assert r.status_code == 200 and "entry_id" not in r.json()
    assert db.get_bill(r.json()["id"])["paid_at"] is None
