"""Fiş kalemi: miktar satırı yanlış ürüne bağlanırsa komşusuna taşınır; tartılı ürün adedi 3 hane (0,915 kg)."""
from app import receipt, receipt_check


def _raw(items):
    return {"items": items}


def test_qty_moved_to_next_item_when_it_matches_neighbor():
    items = receipt._items(_raw([
        {"name": "ORNEK YOGURT 500 GR", "qty": 2, "unit": "adet", "unit_price": 194.95, "amount": 124.95},
        {"name": "ORNEK SUZME PEYNIR", "qty": 1, "unit": "adet", "amount": 389.90},
    ]))
    assert (items[0]["qty"], items[0]["unit_price"]) == (1, None)
    assert (items[1]["qty"], items[1]["unit_price"]) == (2, 194.95)
    chk = receipt_check.check(items, 514.85)
    assert chk["ok"] and not any(i["mismatch"] for i in chk["items"])


def test_qty_moved_to_previous_item():
    items = receipt._items(_raw([
        {"name": "ORNEK PUDING", "qty": 1, "unit": "adet", "amount": 79.90},
        {"name": "ORNEK KAKAO", "qty": 2, "unit": "adet", "unit_price": 39.95, "amount": 73.45},
    ]))
    assert items[0]["qty"] == 2 and items[0]["unit_price"] == 39.95
    assert items[1]["qty"] == 1 and items[1]["unit_price"] is None


def test_correct_neighbor_left_alone():
    items = receipt._items(_raw([
        {"name": "A", "qty": 2, "unit": "adet", "unit_price": 50.0, "amount": 60.0},
        {"name": "B", "qty": 2, "unit": "adet", "unit_price": 50.0, "amount": 100.0},
    ]))
    assert items[0]["qty"] == 2 and items[1]["qty"] == 2  # B kendi miktarıyla tutuyor: A işaretli kalır


def test_weighed_item_keeps_three_decimals():
    items = receipt._items(_raw([{"name": "MANAV MANDALINA", "qty": "0,915", "unit": "kg", "unit_price": 99.95, "amount": 91.45}]))
    assert items[0]["qty"] == 0.915
    assert not receipt_check.check(items, 91.45)["items"][0]["mismatch"]
    assert receipt._num(0.915, qty=True) == 0.915
