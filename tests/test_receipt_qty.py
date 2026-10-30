"""Türk fişinde miktar satırı ('2 ad X 2,90') ürün satırının ÜSTÜNDEDİR ve altındaki ürüne aittir; ürün satırında
toplam ('*5,80') yazar. Yapay zekâ miktar satırını ayrı ürün yazınca adlar bir aşağı kayıyordu."""
from app import ocr, receipt

GOOD = [("K. CANTA ORTA BOY", 2, 5.80), ("KIZILAY 6X200 SADE", 1, 66.00), ("DO.PIS.KAG. ACTISOFT", 1, 59.00),
        ("AVOKADO ADET", 2, 89.80), ("HIPP ELMALI GOFR.30G", 1, 89.00)]


def _flat(items):
    return [(i["name"], i["qty"], i["amount"]) for i in items]


def test_ai_output_with_shifted_names_is_realigned():
    bad = {"items": [{"name": "2 ad X 2.90", "qty": 2, "amount": 5.80}, {"name": "K. CANTA ORTA BOY", "amount": 66.0},
                     {"name": "KIZILAY 6X200 SADE", "amount": 59.0},
                     {"name": "2 ad X 44.90 DO.PIS.KAG. ACTISOFT", "qty": 2, "amount": 89.80},
                     {"name": "AVOKADO ADET", "amount": 89.0}, {"name": "HIPP ELMALI GOFR.30G", "amount": None}]}
    assert _flat(receipt._items(bad)) == GOOD


def test_ai_output_correct_stays_and_merged_qty_text_removed():
    ok = {"items": [{"name": "K. CANTA ORTA BOY", "qty": 2, "amount": 5.80}, {"name": "KIZILAY 6X200 SADE", "amount": 66.0},
                    {"name": "DO.PIS.KAG. ACTISOFT", "amount": 59.0}, {"name": "2 AD X 44,90 AVOKADO ADET", "amount": 89.80},
                    {"name": "HIPP ELMALI GOFR.30G", "amount": 89.0}]}
    assert _flat(receipt._items(ok)) == GOOD


def test_separate_qty_line_without_amount_goes_to_next():
    items = {"items": [{"name": "0,750 KG X 89,90", "amount": None}, {"name": "DOMATES", "amount": 67.43}]}
    out = receipt._items(items)
    assert [(i["name"], i["qty"], i["unit"], i["amount"]) for i in out] == [("DOMATES", 0.75, "kg", 67.43)]


def test_prompt_explains_turkish_qty_line():
    assert "MİKTAR SATIRI" in receipt.DOC_PROMPT and "ÜRÜN SATIRINDAN ÖNCE" in receipt.ITEM_RULES


def test_ocr_qty_line_attaches_to_next_item():
    lines = ["2 ad X 2,90", "K. CANTA ORTA BOY *5,80", "KIZILAY 6X200 SADE *66,00", "DO.PIS.KAG. ACTISOFT *59,00",
             "2 ad X 44,90", "AVOKADO ADET *89,80", "HIPP ELMALI GOFR.30G *89,00", "TOPLAM *309,60"]
    assert _flat(ocr._items(lines)) == GOOD


def test_prompts_format_cleanly():
    for p in (receipt.PROMPT, receipt.DOC_PROMPT):
        out = p.format(categories="Market", split_rule="")
        assert '{"name": "K. CANTA ORTA BOY", "qty": 2' in out
