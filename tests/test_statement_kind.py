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
