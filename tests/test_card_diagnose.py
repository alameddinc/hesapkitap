"""Gönüllüler için teşhis ve anonimleştirme: kişisel veri çıkmaz, anonim metin yine okunur."""
from app import card_reader
from tests.test_card_reader import GARANTI


def test_anonymize_keeps_structure_drops_personal_data():
    text = GARANTI.replace("Sayın AYŞE YILMAZ", "Sayın AYŞE YILMAZ ayse@ornek.net TCKN 12345678901")
    a = card_reader.anonymize(text)
    assert "AYŞE" not in a and "12345678901" not in a and "ayse@" not in a and "3456" not in a and "ORNEK MARKET" not in a
    st = card_reader.read(a)
    assert st and st.bank == "Garanti" and len(st.transactions) == 8 and st.reconciliation_diff() == 0


def test_diagnose_reports_where_it_fails_without_amounts():
    ok = card_reader.diagnose(GARANTI)
    assert ok["okundu"] and ok["işlem"] == 8 and ok["banka"] == "Garanti"
    bad = card_reader.diagnose(GARANTI.replace("Dönem Borcu", "Borç"))
    assert not bad["okundu"] and bad["tarihli_satır"]["dmy/"] == 8
    assert bad["etiketler_tr"]["total"] is False and bad["etiketler_tr"]["period_end"] is True
    assert "3.865" not in str(bad) and "AYŞE" not in str(bad)
