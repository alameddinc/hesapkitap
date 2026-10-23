from app.mailbox import _looks_like_account_statement as acct


def test_card_statement_is_card():
    assert not acct("KREDİ KARTI HESAP ÖZETİ Hesap Kesim Tarihi 15.10.2026 Son Ödeme Tarihi 25.10.2026 Dönem Borcu 1.234,56")
    assert not acct("Önceki dönem bakiyesi 100,00 Asgari Ödeme Tutarı 50,00")


def test_account_statement_variants():
    assert acct("VADESİZ TL HESAP HAREKETLERİ Tarih Açıklama Tutar Bakiye")
    assert acct("İşlem Dökümü 01.01.2025 - 31.12.2026")
    # başlıksız döküm: IBAN ya da bakiye sütunu
    assert acct("Müşteri: X IBAN: TR12 0001 5001 5800 7300 0000 00 Tarih Açıklama Tutar")
    assert acct("Tarih Açıklama İşlem Tutarı Bakiye 01.03.2025 ATM PARA ÇEKME -5.000,00 12.000,00")


def test_uppercase_turkish_headers():
    # eskiden "VADESİZ".lower() birleşik noktalı i verdiği için eşleşmiyordu
    assert acct("VADESİZ HESAP")
    assert acct("IBAN: XX")
    assert not acct("KREDİ KARTI HESAP EKSTRESİ")


VB_TEXT = """Hesap Hareketleri
AD SOYAD SUBE
Hesap No: 00120000000000001 01.10.2026 10:00
VB Müşteri No : 123 Bakiye : 1.000,00 TL
Hesap Türü : Vadesiz Ek Hesap Limiti : 0,00 TL
IBAN : TR00 0000 0000 0000 0000 0000 00 Tarih Aralığı : 01.01.2026
TARİH SAAT İŞLEM NO TUTAR BAKİYE İŞLEM ADI
02.10.2026 10:00 1234567890123457 -500,00 1.000,00 ATM Para Çekme
ATM'den 500 TL para çekildi.
01.10.2026 09:00 1234567890123456 -500,00 1.500,00 Kredi Kartı Borç Ödeme
5400********1234 nolu kredi kartı borç ödemesi Son Ödeme Tarihi:05.10.2026
Hesap Hareketleri
AD SOYAD SUBE
Hesap No: 00120000000000001 01.10.2026 10:00
TARİH SAAT İŞLEM NO TUTAR BAKİYE İŞLEM ADI
30.09.2026 12:00 1234567890123455 2.000,00 2.000,00 Maaş
Şirket maaş ödemesi
"""


def test_account_statement_with_card_payments_in_rows():
    # işlem satırlarında "kredi kartı … son ödeme" geçse de başlık hesap dökümü diyor
    assert acct(VB_TEXT)


def test_vakifbank_rule_based_parser():
    from app import bank_pdf

    r = bank_pdf.parse_rule_based(VB_TEXT)
    assert r["verified"] and r["bank"] == "VakıfBank"
    assert [(x["date"], x["amount"]) for x in r["rows"]] == [("2026-09-30", 2000.0), ("2026-10-01", -500.0), ("2026-10-02", -500.0)]
    assert "AD SOYAD" not in " ".join(x["description"] for x in r["rows"])  # sayfa başlığı açıklamaya karışmaz
    assert "nolu kredi kartı" in r["rows"][1]["description"]
