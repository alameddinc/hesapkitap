"""Fişin işyeri adı: iki satıra bölünmüş unvan birleşir, OCR'ın noktasını kaçırdığı İ ("MAMULLERI") düzelir, aynı VKN'li
önceki kaydın adı korunur, kesik ad unvandan tamamlanır (app/receipt_templates.py _merchant/_title,
app/merchant_name.py). Veriler uydurmadır (işyeri adları ve kontrol hanesi tutan uydurma VKN'ler)."""
import sqlite3

from app import merchant_name
from app import receipt_templates as rt

VKN = "8218105998"   # uydurma, kontrol hanesi tutar
VKN2 = "4605721929"  # uydurma, kontrol hanesi tutar
LEGAL = "ORNEK UNLU MAMULLERİ PASTACILIK SANAYİ TİCARET LTD. ŞTİ."


def _head(*lines):
    orig, folded = rt._lines("\n".join(lines))
    return rt._merchant(orig, folded)


# ---------------------------------------------------------------- başlıktan ad

def test_split_legal_name_is_joined():
    name, legal = _head("ORNEK UNLU", "MAMULLERİ PASTACILIK SAN. TİC. LTD. ŞTİ.", "DENEME MAH. NO:1", "TARIH 01.11.2026")
    assert name == "Ornek Unlu Mamulleri Pastacılık"
    assert legal == "ORNEK UNLU MAMULLERİ PASTACILIK SAN. TİC. LTD. ŞTİ."


def test_split_legal_name_dotless_ocr():
    name, _ = _head("ORNEK UNLU", "MAMULLERI SAN. TIC. LTD. STI.", "TARIH 01.11.2026")
    assert name == "Ornek Unlu Mamulleri"


def test_brand_above_legal_not_joined():
    name, legal = _head("ORNEK", "ORNEK GIDA SAN. VE TIC. LTD. STI.", "TARIH 01.11.2026")
    assert name == "Ornek Gıda"
    assert legal == "ORNEK GIDA SAN. VE TIC. LTD. STI."


def test_single_line_legal_unchanged():
    name, _ = _head("ORNEK GIDA SAN. VE TIC. LTD. STI.", "ORNEK MAH. DENEME CAD. NO:1", "TARIH 01.11.2026")
    assert name == "Ornek Gıda"


def test_address_line_not_joined():
    name, _ = _head("DENEME MAH. ORNEK CAD.", "ORNEK GIDA SAN. VE TIC. LTD. STI.")
    assert name == "Ornek Gıda"


# ---------------------------------------------------------------- noktasız I

def test_title_uses_legal_spelling():
    assert rt._title("MAMULLERI", LEGAL) == "Mamulleri"


def test_title_vowel_harmony():
    assert rt._title("MAMULLERI") == "Mamulleri"
    assert rt._title("KAPI") == "Kapı"
    assert rt._title("ELMALI") == "Elmalı"
    assert rt._title("EKMEKÇI") == "Ekmekçi"  # ilk hecedeki I için ipucu yok: eskisi gibi ı


def test_title_keeps_real_dotted_and_mixed_case():
    assert rt._title("İSTANBUL ÇİÇEKÇİSİ") == "İstanbul Çiçekçisi"
    assert rt._title("Ornek Cafe") == "Ornek Cafe"


def test_core_name():
    assert rt.core_name(LEGAL) == "ORNEK UNLU MAMULLERİ PASTACILIK"
    assert rt.core_name("ORNEK GIDA SAN. VE DIŞ TİC. A.Ş.") == "ORNEK GIDA"
    assert rt.core_name(None) is None


# ---------------------------------------------------------------- unvandan tamamlama

def test_from_legal_completes_truncated():
    assert merchant_name.from_legal("Mamullerı", LEGAL) == "Ornek Unlu Mamulleri"


def test_from_legal_keeps_complete_or_unrelated():
    assert merchant_name.from_legal("Ornek Unlu", LEGAL) is None          # unvanın başı: kısaltılmış marka
    assert merchant_name.from_legal("Deneme Cafe", LEGAL) is None         # unvanla ilgisiz marka
    assert merchant_name.from_legal("Ornek Unlu Mamulleri Pastacılık", LEGAL) is None
    assert merchant_name.from_legal("Mamulleri", None) is None


# ---------------------------------------------------------------- aynı VKN'li önceki kayıt

def _db(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, kind TEXT, entry_date TEXT, description TEXT, tax_no TEXT)")
    c.executemany("INSERT INTO entries (kind, entry_date, description, tax_no) VALUES (?, ?, ?, ?)", rows)
    return c


def test_same_vkn_uses_latest_description():
    c = _db([("gider", "2026-10-01", "Ornek Fırın", VKN), ("gider", "2026-11-01", "Ornek Unlu Mamulleri", VKN),
             ("gider", "2026-11-05", "Başka Yer", VKN2), ("gelir", "2026-11-09", "İade", VKN)])
    doc = {"merchant": "Mamullerı", "legal_name": LEGAL, "tax_no": VKN}
    merchant_name.fix(doc, c)
    assert doc["merchant"] == "Ornek Unlu Mamulleri"


def test_no_previous_entry_falls_back_to_legal():
    c = _db([("gider", "2026-11-05", "Başka Yer", VKN2)])
    doc = {"merchant": "Mamullerı", "legal_name": LEGAL, "tax_no": VKN}
    merchant_name.fix(doc, c)
    assert doc["merchant"] == "Ornek Unlu Mamulleri"


def test_invalid_vkn_ignored():
    c = _db([("gider", "2026-11-01", "Ornek Unlu Mamulleri", "8218105997")])
    doc = {"merchant": "Deneme Cafe", "legal_name": None, "tax_no": "8218105997"}
    merchant_name.fix(doc, c)
    assert doc["merchant"] == "Deneme Cafe"


def test_fix_never_raises():
    doc = {"merchant": "Deneme", "tax_no": VKN}
    merchant_name.fix(doc, sqlite3.connect(":memory:"))  # tablo yok
    assert doc["merchant"] == "Deneme"
