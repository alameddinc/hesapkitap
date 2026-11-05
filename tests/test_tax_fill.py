"""Fiş vergi bilgisini önceki fişlerden tamamlama (app/tax_fill.py): aynı işyeri tek VKN'ye çıkıyorsa eksik doldurulur,
aynı VKN'nin unvanı/vergi dairesi tek yazıma getirilir; birden çok VKN varsa tahmin yok. Veriler uydurma."""
from app import tax_fill

VKN = "1000000000"
VKN2 = "2000000002"


def _e(desc, legal=None, tax=None, office=None, i=1):
    return {"id": i, "description": desc, "legal_name": legal, "tax_no": tax, "tax_office": office}


def test_missing_filled_from_same_merchant_and_spelling_unified():
    es = [_e("Örnek Müd. Kafe", "ORNEK GENEL MUDURLUGU", None, "Ankara Kurumlar", 1),
          _e("Örnek Mud. Kafe", None, None, "Ankara Kurumlar", 2),
          _e("Örnek Müd. Kafe", "ÖRNEK GENEL MÜDÜRLÜĞÜ", VKN, "ANKARA KURUMLAR", 3),
          _e("Örnek Müd. Kafe", "ORNEK GENEL MUDURLUGU", VKN, "Ankara Kurumlar", 4)]
    out = tax_fill.complete_all(es)
    assert all(o["tax_no"] == VKN for o in out)
    assert {o["legal_name"] for o in out} == {"ÖRNEK GENEL MÜDÜRLÜĞÜ"}  # Türkçe harfli yazım
    assert {o["tax_office"] for o in out} == {"Ankara Kurumlar"}
    assert "tax_no" in out[1]["tax_filled"] and "legal_name" in out[1]["tax_filled"]


def test_no_guess_when_same_name_has_two_tax_numbers():
    es = [_e("Cafe", "A LTD", VKN, None, 1), _e("Cafe", "B LTD", VKN2, None, 2), _e("Cafe", None, None, None, 3)]
    out = tax_fill.complete_all(es)
    assert out[2]["tax_no"] is None and "tax_filled" not in out[2]


def test_different_legal_name_kept():
    es = [_e("Ornek Market", "ORNEK MARKET A.S.", VKN, None, 1), _e("Ornek Market", "ORNEK SUBE LTD", VKN, None, 2)]
    out = tax_fill.complete_all(es)
    assert {o["legal_name"] for o in out} == {"ORNEK MARKET A.S.", "ORNEK SUBE LTD"}  # farklı ad: dokunulmaz


def test_invalid_tax_no_ignored():
    es = [_e("Ornek", "ORNEK", "1000000001", None, 1), _e("Ornek", None, None, None, 2)]
    out = tax_fill.complete_all(es)
    assert out[1]["tax_no"] is None
