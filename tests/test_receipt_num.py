"""Fiş/fatura tutarı okuma (denetim A3): binlik nokta ondalık sanılmasın. Tutarlar uydurmadır."""
import pytest

from app import receipt


@pytest.mark.parametrize("v, want", [
    ("1.234", 1234.0),          # tek nokta + tam 3 hane, virgül yok: binlik
    ("1.234.567", 1234567.0),   # birden çok binlik nokta (önce None dönüp satır atlanıyordu)
    ("1.234,50", 1234.5),
    ("1.234,56", 1234.56),
    ("12,90", 12.9),
    ("2.90", 2.9),              # nokta + 2 hane: ondalık (fişte sık)
    ("1234.56", 1234.56),
    ("1,234.56", 1234.56),      # İngilizce biçim: son ayraç ondalık
    ("0.5", 0.5),
    ("₺ 45,00", 45.0),
    ("1.250 TL", 1250.0),
    ("-5,50", -5.5),
    ("12.345,678", 12345.68),   # yarım yukarı (app/money.py)
    (12.345, 12.35),
    (100, 100.0),
    ("", None), (None, None), ("abc", None), ("1.2.3", None),
])
def test_amount(v, want):
    assert receipt._num(v) == want


@pytest.mark.parametrize("v, want", [("1.234", 1.23), ("0.5", 0.5), ("2", 2.0), ("1,5", 1.5)])
def test_qty_dot_is_decimal(v, want):
    """Adet/kg alanında nokta ondalıktır (1.234 kg), binlik değil."""
    assert receipt._num(v, qty=True) == want


def test_items_use_thousands_rule():
    items = receipt._items({"items": [{"name": "Televizyon", "qty": "1", "unit": "adet", "amount": "12.499",
                                       "unit_price": "12.499"},
                                      {"name": "Elma", "qty": "1.250", "unit": "kg", "amount": "37,50"}]})
    assert items[0]["amount"] == 12499.0 and items[0]["unit_price"] == 12499.0
    assert items[1]["qty"] == 1.25 and items[1]["amount"] == 37.5
