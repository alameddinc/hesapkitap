"""Demo/deneme için SAHTE belgeler üretir: ekstre PDF'leri, fiş fotoğrafları, fatura PDF'i.

Hepsi uydurmadır ("Ayşe Yılmaz"); üzerinde "ÖRNEK – GERÇEK DEĞİLDİR" yazar.
Ekstreler tests/fixtures metinlerinden üretilir: hazır okuyucular kuruşu kuruşuna okur.
Fontlar: DejaVu (serbest lisans; matplotlib paketiyle gelir).

Kullanım: pip install reportlab pillow matplotlib && python scripts/make_demo_files.py
Çıktı:    docs/demo/
"""
import os
from pathlib import Path

import matplotlib
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
OUT = ROOT / "docs" / "demo"
FONTS = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DV", str(FONTS / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DVB", str(FONTS / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("DVM", str(FONTS / "DejaVuSansMono.ttf")))
BANNER = "ÖRNEK BELGE – GERÇEK DEĞİLDİR (HesapKitap demo)"
W, H = A4


def _banner(c: canvas.Canvas):
    c.setFillColor(HexColor("#fde68a"))
    c.rect(0, H - 18, W, 18, stroke=0, fill=1)
    c.setFillColor(HexColor("#92400e"))
    c.setFont("DVB", 8)
    c.drawCentredString(W / 2, H - 12.5, BANNER)


def statement_pdf(fixture: str, out: str, brand: str, color: str):
    """Fixture metnini satır satır, gerçek ekstre düzenine benzer şekilde PDF'e yazar."""
    lines = (FIX / fixture).read_text(encoding="utf-8").splitlines()
    c = canvas.Canvas(str(OUT / out), pagesize=A4)
    c.setTitle(f"{brand} – örnek ekstre")

    def page_header():
        _banner(c)
        c.setFillColor(HexColor(color))
        c.rect(0, H - 58, W, 40, stroke=0, fill=1)
        c.setFillColor(HexColor("#ffffff"))
        c.setFont("DVB", 15)
        c.drawString(36, H - 44, brand)
        return H - 80

    y = page_header()
    for ln in lines:
        if y < 50:
            c.showPage()
            y = page_header()
        bold = ln.startswith(("HESAP", "İŞLEM TARİHİ", "Kredi Kartı Hesap", "KREDİ KARTI EKSTRESİ", "Devreden")) \
            or "Toplam Harcama" in ln or ln.endswith("ile yapılan işlemler") or (ln[:4].isdigit() and "***" in ln[:12])
        c.setFillColor(HexColor("#111827"))
        c.setFont("DVB" if bold else "DV", 8.6)
        c.drawString(36, y, ln)
        y -= 13 if not bold else 15
    c.save()


def unknown_bank_pdf(out: str):
    """Hazır okuyucusu olmayan banka: yapay zekâ ile okumayı göstermek için. Toplamlar tutarlıdır."""
    rows = [
        ("02.10.2026", "ORNEK MARKET ZINCIRI ISTANBUL", 642.30, ""),
        ("05.10.2026", "ORNEK AKARYAKIT ANKARA", 1_250.00, ""),
        ("07.10.2026", "ORNEK MOBILYA 2/6 TAKSIT", 1_500.00, "Kalan 4 taksit"),
        ("09.10.2026", "ORNEK RESTORAN ISTANBUL", 875.50, ""),
        ("11.10.2026", "8.99 USD NETFLIX.COM", 389.10, ""),
        ("12.10.2026", "BORÇ ÖDEMESİ - TEŞEKKÜRLER", -6_000.00, ""),
        ("14.10.2026", "ORNEK ELEKTRONIK IADE", -299.90, ""),
        ("16.10.2026", "KART AİDATI", 450.00, ""),
    ]
    prev = 6_000.00
    spend = round(sum(a for _, _, a, _ in rows if a > 0) + sum(a for _, d, a, _ in rows if a < 0 and "ÖDEME" not in d), 2)
    debt = round(prev + spend - 6_000.00, 2)
    tr = lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731
    c = canvas.Canvas(str(OUT / out), pagesize=A4)
    c.setTitle("Örnek Bank – örnek ekstre")
    _banner(c)
    c.setFillColor(HexColor("#0f766e"))
    c.rect(0, H - 70, W, 52, stroke=0, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont("DVB", 18)
    c.drawString(36, H - 50, "ÖRNEK BANK")
    c.setFont("DV", 9)
    c.drawRightString(W - 36, H - 40, "Kredi Kartı Hesap Bildirimi")
    c.drawRightString(W - 36, H - 54, "Kart: 5400 **** **** 7777")
    c.setFillColor(HexColor("#111827"))
    info = [("Kart Sahibi", "AYŞE YILMAZ"), ("Hesap Kesim Tarihi", "17.10.2026"), ("Son Ödeme Tarihi", "27.10.2026"),
            ("Kart Limiti", "60.000,00 TL"), ("Önceki Dönem Borcu", f"{tr(prev)} TL"), ("Ödemeler", f"{tr(6000)} TL"),
            ("Dönem İçi Harcamalar (ücretler dahil)", f"{tr(spend)} TL"), ("Toplam Borç", f"{tr(debt)} TL"),
            ("Asgari Ödeme", f"{tr(round(debt * 0.2, 2))} TL"), ("Sonraki Kesim / Son Ödeme", "17.11.2026 / 27.11.2026")]
    y = H - 100
    for k, v in info:
        c.setFont("DV", 9); c.drawString(36, y, k)
        c.setFont("DVB", 9); c.drawRightString(300, y, v)
        y -= 14
    y -= 10
    c.setFillColor(HexColor("#e5e7eb")); c.rect(30, y - 4, W - 60, 16, stroke=0, fill=1)
    c.setFillColor(HexColor("#111827")); c.setFont("DVB", 8.5)
    c.drawString(36, y, "Tarih"); c.drawString(110, y, "Açıklama"); c.drawString(380, y, "Not"); c.drawRightString(W - 36, y, "Tutar (TL)")
    y -= 18
    c.setFont("DV", 8.5)
    for d, desc, a, note in rows:
        c.drawString(36, y, d); c.drawString(110, y, desc); c.drawString(380, y, note)
        c.drawRightString(W - 36, y, ("-" if a < 0 else "") + tr(abs(a)))
        y -= 14
    c.save()


def bill_pdf(out: str):
    c = canvas.Canvas(str(OUT / out), pagesize=A4)
    c.setTitle("Örnek elektrik faturası")
    _banner(c)
    c.setFillColor(HexColor("#1d4ed8")); c.rect(0, H - 70, W, 52, stroke=0, fill=1)
    c.setFillColor(HexColor("#ffffff")); c.setFont("DVB", 16)
    c.drawString(36, H - 50, "ÖRNEK ELEKTRİK PERAKENDE SATIŞ A.Ş.")
    c.setFont("DV", 9); c.drawRightString(W - 36, H - 50, "e-Fatura")
    c.setFillColor(HexColor("#111827"))
    rows = [("Sayın", "AYŞE YILMAZ"), ("Fatura No", "OEP2026000098765"), ("Fatura Tarihi", "12.10.2026"),
            ("Tesisat No", "4000000001"), ("Dönem", "01.09.2026 – 30.09.2026"), ("Tüketim", "212 kWh")]
    y = H - 100
    for k, v in rows:
        c.setFont("DV", 10); c.drawString(36, y, k); c.setFont("DVB", 10); c.drawString(160, y, v); y -= 16
    y -= 12
    for k, v in (("Enerji Bedeli", "590,20 TL"), ("Dağıtım Bedeli", "114,30 TL"), ("KDV (%20)", "140,80 TL")):
        c.setFont("DV", 10); c.drawString(36, y, k); c.drawRightString(300, y, v); y -= 16
    y -= 8
    c.setFillColor(HexColor("#fef3c7")); c.rect(30, y - 30, 280, 46, stroke=0, fill=1)
    c.setFillColor(HexColor("#111827")); c.setFont("DVB", 12)
    c.drawString(36, y, "ÖDENECEK TUTAR"); c.drawRightString(300, y, "845,30 TL")
    c.setFont("DVB", 11); c.drawString(36, y - 20, "SON ÖDEME TARİHİ"); c.drawRightString(300, y - 20, "28.10.2026")
    c.save()


def receipt_jpg(out: str, shop: list[str], date: str, time: str, items: list[tuple[str, float]], payment: str, vat: float):
    mono = ImageFont.truetype(str(FONTS / "DejaVuSansMono.ttf"), 22)
    bold = ImageFont.truetype(str(FONTS / "DejaVuSansMono-Bold.ttf"), 22)
    tr = lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731
    total = round(sum(a for _, a in items), 2)
    body = [("b", s) for s in shop] + [("n", ""), ("n", f"TARİH: {date}    SAAT: {time}"), ("n", "FİŞ NO: 0042"), ("n", "-" * 34)]
    body += [("n", f"{n[:20]:<20}{('*' + tr(a)):>14}") for n, a in items]
    body += [("n", "-" * 34), ("n", f"{'TOPKDV':<20}{('*' + tr(vat)):>14}"), ("b", f"{'TOPLAM':<20}{('*' + tr(total)):>14}"),
             ("n", f"{payment:<20}{('*' + tr(total)):>14}"), ("n", ""), ("n", "  ÖRNEK FİŞ – GERÇEK DEĞİLDİR"),
             ("n", "      HesapKitap demo")]
    w, lh = 520, 34
    img = Image.new("RGB", (w + 60, len(body) * lh + 80), "#e7e5e4")
    paper = Image.new("RGB", (w, len(body) * lh + 40), "#fffdf7")
    d = ImageDraw.Draw(paper)
    for i, (kind, text) in enumerate(body):
        f = bold if kind == "b" else mono
        tw = d.textlength(text, font=f)
        x = (w - tw) / 2 if kind == "b" and i < len(shop) else 22
        d.text((x, 20 + i * lh), text, fill="#1c1917", font=f)
    img.paste(paper.rotate(1.2, expand=False, fillcolor="#e7e5e4"), (30, 40))
    img.save(OUT / out, quality=88)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    statement_pdf("vakifbank_a.txt", "1-vakifbank-ekstre.pdf", "VakıfBank · Kredi Kartı Hesap Özeti (ÖRNEK)", "#a16207")
    statement_pdf("vakifbank_b.txt", "2-vakifbank-ek-kartli-ekstre.pdf", "VakıfBank · Kredi Kartı Hesap Özeti (ÖRNEK)", "#a16207")
    statement_pdf("kuveytturk.txt", "3-kuveytturk-ekstre.pdf", "Kuveyt Türk · Kredi Kartı Ekstresi (ÖRNEK)", "#166534")
    unknown_bank_pdf("4-ornek-bank-yapay-zeka.pdf")
    bill_pdf("fatura-elektrik.pdf")
    # 25.09.2026 ORNEK MARKET 412,75: vakifbank ekstresindeki işlemle aynı → eşleşme demosu
    receipt_jpg("fis-market.jpg", ["ORNEK MARKET", "ORNEK MAH. DEMO SK. NO:1", "ISTANBUL"], "25.09.2026", "18:42",
                [("SUT 1L", 54.90), ("EKMEK", 17.50), ("DOMATES KG", 82.35), ("BEYAZ PEYNIR", 215.00), ("YUMURTA 10", 43.00)],
                "KREDI KARTI", 68.79)
    receipt_jpg("fis-kahve.jpg", ["ORNEK KAHVE EVI", "MODA / KADIKOY"], "18.10.2026", "10:15",
                [("FILTRE KAHVE", 95.00), ("CHEESECAKE", 90.00)], "NAKIT", 16.82)
    receipt_jpg("fis-eczane.jpg", ["ORNEK ECZANESI", "BESIKTAS / ISTANBUL"], "19.10.2026", "20:05",
                [("AGRI KESICI", 145.40), ("VITAMIN C", 310.00), ("PLASTER", 185.00)], "KREDI KARTI", 58.22)
    print("ok:", sorted(os.listdir(OUT)))
