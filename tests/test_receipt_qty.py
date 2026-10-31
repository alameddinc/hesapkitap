"""Türk fişinde miktar satırı ('2 ad X 2,50') ürün satırının ÜSTÜNDEDİR ve altındaki ürüne aittir; ürün satırında
toplam ('*5,00') yazar. Yapay zekâ miktar satırını ayrı ürün yazınca adlar bir aşağı kayıyordu."""
from app import ocr, receipt

GOOD = [("POSET ORTA BOY", 2, 5.00), ("MADEN SUYU 6X200", 1, 60.00), ("KAGIT HAVLU 4LU", 1, 55.00),
        ("MUZ ADET", 2, 80.00), ("BISKUVI 30G", 1, 85.00)]


def _flat(items):
    return [(i["name"], i["qty"], i["amount"]) for i in items]


def test_ai_output_with_shifted_names_is_realigned():
    bad = {"items": [{"name": "2 ad X 2.50", "qty": 2, "amount": 5.00}, {"name": "POSET ORTA BOY", "amount": 60.0},
                     {"name": "MADEN SUYU 6X200", "amount": 55.0},
                     {"name": "2 ad X 40.00 KAGIT HAVLU 4LU", "qty": 2, "amount": 80.00},
                     {"name": "MUZ ADET", "amount": 85.0}, {"name": "BISKUVI 30G", "amount": None}]}
    assert _flat(receipt._items(bad)) == GOOD


def test_ai_output_correct_stays_and_merged_qty_text_removed():
    ok = {"items": [{"name": "POSET ORTA BOY", "qty": 2, "amount": 5.00}, {"name": "MADEN SUYU 6X200", "amount": 60.0},
                    {"name": "KAGIT HAVLU 4LU", "amount": 55.0}, {"name": "2 AD X 40,00 MUZ ADET", "amount": 80.00},
                    {"name": "BISKUVI 30G", "amount": 85.0}]}
    assert _flat(receipt._items(ok)) == GOOD


def test_separate_qty_line_without_amount_goes_to_next():
    items = {"items": [{"name": "0,750 KG X 89,90", "amount": None}, {"name": "DOMATES", "amount": 67.43}]}
    out = receipt._items(items)
    assert [(i["name"], i["qty"], i["unit"], i["amount"]) for i in out] == [("DOMATES", 0.75, "kg", 67.43)]


def test_prompt_explains_turkish_qty_line():
    assert "MİKTAR SATIRI" in receipt.DOC_PROMPT and "ÜRÜN SATIRINDAN ÖNCE" in receipt.ITEM_RULES


def test_ocr_qty_line_attaches_to_next_item():
    lines = ["2 ad X 2,50", "POSET ORTA BOY *5,00", "MADEN SUYU 6X200 *60,00", "KAGIT HAVLU 4LU *55,00",
             "2 ad X 40,00", "MUZ ADET *80,00", "BISKUVI 30G *85,00", "TOPLAM *285,00"]
    assert _flat(ocr._items(lines)) == GOOD


def test_prompts_format_cleanly():
    for p in (receipt.PROMPT, receipt.DOC_PROMPT):
        out = p.format(categories="Market", split_rule="")
        assert '{"name": "POSET ORTA BOY", "qty": 2' in out
