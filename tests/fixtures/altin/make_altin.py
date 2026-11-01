"""Altın fikstür setinin ikili dosyalarını üretir (uydurma veri; gerçek kişi, hesap, işyeri yok).

Çalıştırma: python tests/fixtures/altin/make_altin.py
Beklenen değerler (satır sayısı, giren/çıkan toplamı, son bakiye) beklenen.json'da ELLE yazılıdır; uygulama koduyla
hesaplanmaz. Yeni belge eklerken: dosyayı buraya ya da klasöre koy, rakamlarını belgenin kendisinden yaz.
"""
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).parent


def yeniden_eskiye_xlsx() -> None:
    """Yeniden eskiye sıralı, Borç/Alacak sütunlu döküm; son günde iki hareket (F3.3c: çapa günün SON hareketi)."""
    wb = Workbook()
    ws = wb.active
    ws.append(["ORNEK KATILIM BANKASI HESAP DÖKÜMÜ"])
    ws.append(["IBAN: TR00 0000 0000 0000 0000 0000 00"])
    ws.append([])
    ws.append(["İşlem Tarihi", "Açıklama", "Borç", "Alacak", "Bakiye"])
    for row in [
        ["08.10.2026", "SU FATURASI ORNEK SU IDARESI", "300,00", "", "6.064,85"],
        ["08.10.2026", "ORNEK MARKET POS", "1.034,75", "", "6.364,85"],
        ["04.10.2026", "IADE ORNEK MAGAZA", "", "120,00", "7.399,60"],
        ["04.10.2026", "INTERNET FATURASI ORNEKNET", "449,90", "", "7.279,60"],
        ["01.10.2026", "OTOPARK POS", "60,00", "", "7.729,50"],
        ["01.10.2026", "ORNEK ECZANE POS", "210,50", "", "7.789,50"],
        ["01.10.2026", "PRIM ODEMESI", "", "3.000,00", "8.000,00"],
    ]:
        ws.append(row)
    wb.save(HERE / "hesap-yeniden-eskiye.xlsx")


if __name__ == "__main__":
    yeniden_eskiye_xlsx()
    print("hesap-yeniden-eskiye.xlsx yazıldı")
