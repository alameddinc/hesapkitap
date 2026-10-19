"""Testler için SAHTE ekstre metinleri üretir (gerçek kişi/kart bilgisi yoktur).

Format bankaların PDF'inden pdfplumber ile çıkan metne birebir benzer; toplamlar hesaplanır, mutabakat tutar.
Çalıştırma: python tests/fixtures/make_fixtures.py
"""
from pathlib import Path

HERE = Path(__file__).parent


def us(x: float) -> str:  # VakıfBank: 1,234.56
    return f"{x:,.2f}"


def tr(x: float) -> str:  # Kuveyt Türk: 1.234,56
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def vakifbank(name, card, holder, kesim, due, nxt_kesim, nxt_due, prev, paid_on, sections, fees=()):
    """sections: [(kart_maskesi | None, sahip, [(tarih, açıklama, tutar, ek)])]; tutar<0 = iade/alacak."""
    lines, total = [], 0.0
    for sec_card, sec_holder, txns in sections:
        if sec_card:
            lines.append(f"{sec_card} {sec_holder}")
        sub = 0.0
        for d, desc, amt, extra in txns:
            s = f"+{us(-amt)}" if amt < 0 else us(amt)
            lines.append(f"{d} {desc} {s}{(' ' + extra) if extra else ''}".rstrip())
            sub += amt
        if sec_card:
            lines.append(f"{sec_card} Toplam Harcama Tutarı {us(sub)}")
        total += sub
    fee_total = 0.0
    for d, desc, amt in fees:
        lines.insert(0, f"{d} {desc} {us(amt)}")
        fee_total += amt
    activity = round(total, 2)
    fee_total = round(fee_total, 2)
    debt = round(prev + activity + fee_total - prev, 2)
    head = f"""Kredi Kartı Hesap Özeti (TL)
HESAP BİLGİLERİNİZ
Dönem Borcunuz : {us(debt)} TL 0000000000000000
Asgari Ödeme Tutarı : {us(round(debt * 0.4))} TL
Son Ödeme Tarihi : {due} Son Ödeme Tarihi: {due}
Şube Adı : ORNEK (0000)
VB Müşteri No : 000000000000 Sayın {holder}
Kart No : {card}
Limitiniz : 100,000.00 TL
Hesap Kesim Tarihi : {kesim}
Bir Sonraki Hesap Kesim Tarihi : {nxt_kesim}
Bir Sonraki Son Ödeme Tarihi : {nxt_due}
Detaylı bilgi için www.vakifkart.com.tr adresini ziyaret edebilirsiniz.
İŞLEM TARİHİ AÇIKLAMA TUTAR (TL) KALAN TAKSİT WORLDPUAN
13.09.2026 ÖNCEKİ DÖNEM HESAP ÖZETİ BAKİYENİZ {us(prev)}
{paid_on} ÖDEMENİZ İÇİN TEŞEKKÜRLER +{us(prev)}"""
    tail = f"""HESAP ÖZETİ
Önceki Hesap Bakiyesi {us(prev)}
Dönem Içi Islemler {us(activity)}
Toplam Faiz ve Ücretler {us(fee_total)}
Ödemeler +{us(prev)}
Dönem Borcunuz {us(debt)}"""
    (HERE / name).write_text("\n".join([head, *lines, tail]) + "\n", encoding="utf-8")


def kuveytturk(name):
    card = "411100******1111"
    rows = [
        ("15/09/2026", "ORNEK CAFE İSTANBUL TR", 185.00, None),
        ("16/09/2026", "OPET PETROL/ORNEK İSTANBUL TR", 720.00, None),
        ("17/09/2026", "MİGROS ORNEK İSTANBUL TR", 455.50, None),
        ("19/09/2026", "Kredi Kartı Borç Ödeme", -10000.00, None),
        ("24/09/2026", "ŞOK-1234- ORNEK İSTANBUL TR", 312.25, None),
        ("27/09/2026", "HALKÖDE ÖD/POAŞ ORNEK İSTANBUL TR", 1280.00, None),
        ("28/09/2026", "ORNEK KEBAP EVİ ANKARA TR", 960.00, None),
        ("29/09/2026", "ORNEK PETROL ANKARA TR", 640.00, (1, 9, 71.11)),
        ("29/09/2026", "Vade Farki - ORNEK PETROL ANKARA TR", 640.00, (1, 9, 20.48)),
        ("29/09/2026", "BSMV - ORNEK PETROL ANKARA TR", 640.00, (1, 9, 3.07)),
        ("29/09/2026", "KKDF - ORNEK PETROL ANKARA TR", 640.00, (1, 9, 3.07)),
        ("04/10/2026", "H&M ORNEK AVM İSTANBUL TR", 2400.00, (1, 9, 266.67)),
        ("04/10/2026", "Vade Farki - H&M ORNEK AVM İSTANBUL TR", 2400.00, (1, 9, 76.80)),
        ("04/10/2026", "BSMV - H&M ORNEK AVM İSTANBUL TR", 2400.00, (1, 9, 11.52)),
        ("04/10/2026", "KKDF - H&M ORNEK AVM İSTANBUL TR", 2400.00, (1, 9, 11.52)),
        ("08/10/2026", "ORNEK KITAP D&R ISTANBUL TUR", 1875.40, None),
        ("12/10/2026", "HM HENNES ORNEK ISTANBUL TR", 849.99, None),
    ]
    out, spend = [], 0.0
    for d, desc, amt, inst in rows:
        if inst:
            n, tot, part = inst
            out.append(f"{d} {desc} {tr(amt)} TL İşlemin {n} / {tot} Taksidi {tr(part)} TL 0.00")
            spend += part
        else:
            out.append(f"{d} {desc} {tr(amt)} TL 0.00")
            if amt > 0:
                spend += amt
    spend = round(spend, 2)
    prev = 10000.00
    text = f"""KREDİ KARTI EKSTRESİ /Credit Card Statement
Kart ve Müşteri Bilgileriniz /Your Card and Customer Details
Sayın AYŞE YILMAZ
Müşteri No /Customer Number 00000000
Kart No /Card Number {card}
Kart Limiti /Card Limit 50.000,00 TL
Kullanılabilir Limit /Available Limit 40.000,00 TL
Hesap Kesim Tarihi /Statement Issue Date 15/10/2026
Son Ödeme Tarihi /Due Date for Payment 26/10/2026
Dönem Borcunuz /Your Current Debt {tr(spend)} TL
Asgari Ödeme Tutarı /Minimum Payment Amount {tr(spend)} TL
Sonraki Dönem Bilgileriniz /Next Statement's Details
Sonraki Hesap Kesim Tarihi /Next Statement Date 15/11/2026
Sonraki Son Ödeme Tarihi /Next Due Date for Payment 25/11/2026
İŞLEM TARİHİ İŞLEM AÇIKLAMASI TUTAR ALTINPUAN
{card} ile yapılan işlemler
""" + "\n".join(out) + f"""
Devreden Bakiye Dönem Harcamaları Ödemeleriniz Dönem Borcunuz
{tr(prev)} + {tr(spend)} - {tr(prev)} = {tr(spend)}
Kuveyt Türk Katılım Bankası A.Ş.
www.kuveytturk.com.tr
"""
    (HERE / name).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    # Tutarlar ve işyerleri tamamen uydurmadır.
    # A: aboneliklerin olduğu kart (ana + ek kart), önceki ekstre 18.09'da ödenmiş
    vakifbank(
        "vakifbank_a.txt", "4543********1234", "AYŞE YILMAZ", "13.10.2026", "23.10.2026", "13.11.2026", "23.11.2026",
        prev=20000.00, paid_on="18.09.2026",
        sections=[
            (None, "", [
                ("25.09.2026", "ORNEK MARKET", 412.75, "2"),
                ("26.09.2026", "ORNEK BEBEK/IZMIR", 1248.60, "125"),
                ("27.09.2026", "0000000000123456789 police tah4. Taksit", 1315.40, "5x1,315.40"),
            ]),
            ("4543***5678", "AYŞE YILMAZ", [
                ("18.09.2026", "7.25 USD OPENAI/SAN FRANCISCO", 318.47, ""),
                ("20.09.2026", "TKPAY/ORNEK TEKNOLOJI 1. Taksit", 6240.00, "5x6,240.00"),
                ("20.09.2026", "9.99 USD SCRIBD R000000000/4150000000", 447.18, ""),
                ("19.09.2026", "Uber Eats Yemek", 365.00, ""),
                ("04.10.2026", "IYZICO/SPOTIFY.COM/ISTANBUL", 99.99, ""),
                ("08.10.2026", "100 USD ANTHROPIC* CLAUDE SUB/SAN FRANCI", 4476.20, ""),
                ("09.10.2026", "21.30 USD Hetzner Online/www.hetzner.c", 953.62, ""),
                ("11.10.2026", "OTOYOL YATIRIM VE İŞLETME/ORNEK", 1875.00, ""),
            ]),
        ],
    )
    # B: faiz + taksit iadeleri + ek kart (kart parçası soneki)
    vakifbank(
        "vakifbank_b.txt", "5400********9012", "AYŞE YILMAZ", "16.10.2026", "26.10.2026", "15.11.2026", "25.11.2026",
        prev=15000.00, paid_on="26.09.2026",
        sections=[
            (None, "", [
                ("22.05.2026", "LCWAIKIKI ORNEK 5. Taksit", 249.90, "1x249.90"),
                ("12.07.2026", "HEPSIPAY /HEPSIBU 4. Taksit", 2875.25, "2x2,875.25"),
                ("01.09.2026", "HEPSIPAY /HEPSIBU 2. İade", -129.00, "1x129.00"),
                ("17.09.2026", "IYZICO/ORNEK MOBILYA 3. Taksit", 8750.00, "3x8,750.00"),
                ("14.09.2026", "USTA TESİSAT", 2750.00, "6 *"),
                ("19.09.2026", "OPET PETROL/ORNEK/İSTANBUL", 610.00, ""),
                ("21.09.2026", "ORNEK CAFE/İSTANBUL", 145.00, ""),
                ("24.09.2026", "İSPARK GENEL MÜDÜRLÜK ÖKC/İSTANBUL", 195.00, ""),
            ]),
            ("5400***3456", "MEHMET YILMAZ", [
                ("12.07.2026", "ORNEK COLINS 4. Taksit", 712.30, "Son Taksit"),
                ("20.08.2026", "LC WAIKIKI 2. Taksit", 279.90, "4x279.90"),
                ("20.08.2026", "LC WAIKIKI 2. İade 65", -59.90, "4x59.90"),
                ("18.09.2026", "ORNEK KIDS 5400*3456", 489.00, "978"),
                ("18.09.2026", "İADE/ ORNEK KIDS 5400*345", -489.00, ""),
                ("07.10.2026", "ORNEK MARKET İSTANBUL 5400*3456", 268.40, ""),
                ("15.10.2026", "9916 I065 A101 ORNEK 5400*3456", 557.35, "11"),
            ]),
        ],
        fees=[
            ("16.10.2026", "KKDF", 71.15),
            ("16.10.2026", "BSMV", 71.15),
            ("16.10.2026", "GECİKME FAİZİ (Oran:4.05)", 59.80),
            ("16.10.2026", "ALIŞVERİŞ FAİZİ (Oran:3.75)", 474.30),
        ],
    )
    kuveytturk("kuveytturk.txt")
    print("ok")
