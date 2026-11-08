"""Tanıtım filmi için KURGU belgeler: kamera önünde okutulacak fiş, kart ekstreleri, hesap dökümü, fatura, dekont.

Hepsi uydurmadır: kişiler Ayşe Örnek / Mehmet Örnek, kurumlar "Örnek Bank", "Mavi Bank", "Örnek Elektrik", zincir
"MARKET A" (app/film_scope.py CHAINS). Kart numarası maskeli (5555 **** **** 2222; tam numara yok), IBAN'lar TR00 0000…
(biçimi doğru, kontrol hanesi geçersiz: gerçek bir hesap olamaz), VKN 1234567890 (kontrol hanesi tutan yer tutucu).
Her belgede "Kurgu belge – gerçek değildir" yazar. Tarihler --today'e göredir (varsayılan TODAY); aynı tarih aynı
içeriği üretir. Film hesapları (app/film_seed.py) ile uyum:
- Kart ekstreleri Örnek Bank'ın ÜÇÜNCÜ bir kartı (…2222, kesim günü CUT_DAY): film_seed'in …0000 kartının son iki
  dönemi zaten yüklü; aynı kartın yeni dönemi bugünden sonraya düşerdi, eski dönemi de "zaten yüklü" olurdu.
- MARKET A fişi kredi kartıyla (…2222) ödenmiş: son ekstredeki "MARKET A İSTANBUL" satırıyla eşleşir. Ürün fiyatları
  film fiyat dünyasıyla aynı (film_seed.price, Market A).
- Hesap dökümünün kapanış bakiyesi film_seed'in "Örnek Bank" hesabıyla aynı (38.420,50); maaş 58.000 (ayın 1'i),
  kira 24.000 (ayın 5'i). Dekont dökümdeki 5'inin kira EFT'sidir (döküm gelince birleşir).
- Fatura film_seed'in "Örnek Elektrik" faturasıyla aynı tutar ve son ödeme (1.120,40; bugün + 6).

Fiş senaryosu (karışan rakam): "BULAŞIK DETERJANI 1,5 L" satırının fiyatı 82,95; termal baskıda 8'in sağ üst yayı
silik, 6 gibi okunur (62,95). TOPLAM 502,89 yalnız 82,95 ile tutar: yapay zekâ 62,95 okursa kalemler ₺20 eksik kalır
ve app/receipt_check.py digit_check "6→8" önerir (Uygula). Ayrıntı: docs/film/README.md.

Kullanım: uv run --with-requirements requirements.txt --with reportlab --with matplotlib \\
              python scripts/make_film_files.py [--today 2026-11-10]
Çıktı:    docs/film/
"""
import argparse
import functools
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "film"
TODAY = date(2026, 11, 10)
DISCLAIMER = "Kurgu belge – gerçek değildir (HesapKitap tanıtım filmi)"

OWNER, MEMBER = "AYŞE ÖRNEK", "MEHMET ÖRNEK"
IBAN_OWNER = "TR00 0000 0000 0000 0000 0000 01"   # kontrol hanesi 00: geçerli bir IBAN olamaz
IBAN_LANDLORD = "TR00 0000 0000 0000 0000 0000 02"
CARD_MASKED = "5555 **** **** 2222"
CARD_LAST4 = "2222"
CUT_DAY = 3            # kartın hesap kesim günü
DUE_DAYS = 10          # son ödeme = kesim + 10 gün
CARD_LIMIT = 60_000.00
INTEREST = 0.035       # akdi faiz (aylık)
VKN = "1234567890"     # kontrol hanesi tutan yer tutucu (app/vat.py vkn_valid)
MONTHS = ("OCAK", "ŞUBAT", "MART", "NİSAN", "MAYIS", "HAZİRAN", "TEMMUZ", "AĞUSTOS", "EYLÜL", "EKİM", "KASIM", "ARALIK")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def tr(x: float) -> str:
    """1234.5 → '1.234,50' (eksi işaretli)."""
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s


def d(x: date) -> str:
    return x.strftime("%d.%m.%Y")


def r2(x: float) -> float:
    from app import money

    return money.money(x)


# ---------------------------------------------------------------- fiş (MARKET A)

GHOST = 192  # silik yayın gri tonu (0 siyah, 250 kâğıt); Apple Vision 100–212 arasında hep 6 okudu

# İki fiş, iki ayrı tanık (app/receipt_check.py digit_check):
#  "a": tek adetli satırın fiyatı silik (82,95 → 62,95). Öneriyi yalnız fiş toplamı verir (tek satırlık arama);
#       telefon metni (ocr_text) o satırda 62,95 okursa satır "doğrulanmış" sayılır ve öneri ÇIKMAZ (README).
#  "b": adetli satırın tutarı silik ("4 AD X 20,50" → *82,00 → 62,00). adet × birim tanığı ("qty") telefon metninden
#       bağımsızdır: telefon metni 62,00 okusa da öneri çıkar.
# (başlık, adet, KDV) — fiyatlar film_seed.price(Market A, fiş günü); silik satırın tutarının onlar basamağı 8 olmalı
RECEIPTS = {
    "a": {"days_ago": 13, "time": "18:42", "no": "0127", "faded": 4, "pay": "KREDİ KARTI", "items": [
        ("SÜT 1 L", 3, 1), ("EKMEK 250 G", 1, 1), ("DOMATES", 1.245, 1), ("BEYAZ PEYNİR 500 G", 1, 1),
        ("BULAŞIK DETERJANI 1,5 L", 1, 20), ("YUMURTA 30LU", 1, 1)]},
    "b": {"days_ago": 4, "time": "11:07", "no": "0204", "faded": 1, "pay": "NAKİT", "items": [
        ("KOLA 2,5 L", 1, 1), ("MAKARNA 500 G", 4, 1), ("TOZ ŞEKER 1 KG", 1, 1), ("AYRAN 1 L", 2, 1),
        ("ELMA", 1.380, 1)]},
}


def receipt_date(today: date, key: str = "a") -> date:
    return today - timedelta(days=RECEIPTS[key]["days_ago"])


def receipt_items(today: date, key: str = "a") -> list[dict]:
    """Fişin GERÇEK kalemleri (name, qty, unit, unit_price, amount, vat_rate)."""
    from app import film_seed

    spec = RECEIPTS[key]
    items = []
    for title, qty, rate in spec["items"]:
        unit = "kg" if film_seed._BY[title][0] == "kg" else "adet"
        up = film_seed.price(title, "Market A", spec["days_ago"])
        items.append({"name": title, "qty": qty, "unit": unit, "unit_price": up, "amount": r2(qty * up),
                      "vat_rate": rate})
    faded = items[spec["faded"]]["amount"]
    assert f"{faded:.2f}"[-5] == "8", f"silik tutarın onlar basamağı 8 değil: {faded}"
    return items


def misread(amount: float) -> float:
    """Silik 8'in 6 okunduğu tutar (onlar basamağı)."""
    return r2(amount - 20)


def receipt_vat(items: list[dict]) -> dict[int, float]:
    out: dict[int, float] = {}
    for rate in sorted({it["vat_rate"] for it in items}):
        gross = r2(sum(it["amount"] for it in items if it["vat_rate"] == rate))
        out[rate] = r2(gross * rate / (100 + rate))
    return out


def receipt_total(items: list[dict]) -> float:
    return r2(sum(it["amount"] for it in items))


W_CH = 40  # fiş satırı genişliği (karakter)


def _lr(left: str, right: str) -> str:
    return left + " " * max(1, W_CH - len(left) - len(right)) + right


def receipt_lines(today: date, key: str = "a", faded_as: str | None = None) -> list[tuple[str, str]]:
    """Fişin satırları [(tür, metin)]: tür c (ortalı), b (kalın ortalı), n (sola dayalı), B (kalın), f (silik rakamlı
    satır). faded_as "6": metin olarak silik rakamın 6 okunduğu hali (telefon metni benzetimi)."""
    spec = RECEIPTS[key]
    items = receipt_items(today, key)
    vat = receipt_vat(items)
    total = receipt_total(items)
    out = [("b", "MARKET A"), ("c", "MARKET A GIDA PAZARLAMA A.Ş."), ("c", "ÖRNEK MAH. DEMO CAD. NO:12"),
           ("c", "MERKEZ / İSTANBUL"), ("c", f"ÖRNEK V.D. VKN: {VKN}"), ("n", ""),
           ("n", _lr(f"TARİH: {d(receipt_date(today, key))}", f"SAAT: {spec['time']}")),
           ("n", _lr(f"FİŞ NO: {spec['no']}", "Z NO: 0311")), ("n", "-" * W_CH)]
    for i, it in enumerate(items):
        if it["qty"] != 1:
            q = f"{it['qty']:.3f}".replace(".", ",") + " KG" if it["unit"] == "kg" else f"{it['qty']} AD"
            out.append(("n", f"{q} X {tr(it['unit_price'])}"))
        faded = i == spec["faded"]
        amount = tr(misread(it["amount"])) if faded and faded_as == "6" else tr(it["amount"])
        out.append(("f" if faded else "n", _lr(f"{it['name']:<24}%{it['vat_rate']:02d}", f"*{amount}")))
    out += [("n", "-" * W_CH)]
    out += [("n", _lr(f"KDV %{r:02d}", f"*{tr(v)}")) for r, v in vat.items()]
    out += [("n", _lr("TOPKDV", f"*{tr(sum(vat.values()))}")), ("B", _lr("TOPLAM", f"*{tr(total)}"))]
    if spec["pay"] == "KREDİ KARTI":
        out += [("n", _lr("KREDİ KARTI", f"*{tr(total)}")), ("n", "-" * W_CH),
                ("n", f"KART NO: **** **** **** {CARD_LAST4}"), ("n", "ONAY KODU: 482913   PROVİZYON")]
    else:
        paid = float(-(-total // 50) * 50)  # bir üst 50'lik
        out += [("n", _lr("NAKİT", f"*{tr(paid)}")), ("n", _lr("PARA ÜSTÜ", f"*{tr(r2(paid - total))}")),
                ("n", "-" * W_CH)]
    out += [("n", ""), ("c", "BİZİ TERCİH ETTİĞİNİZ İÇİN"), ("c", "TEŞEKKÜR EDERİZ"), ("n", ""),
            ("c", "KURGU FİŞ – GERÇEK DEĞİLDİR"), ("c", "HesapKitap tanıtım filmi")]
    return out


def receipt_text(today: date, key: str = "a", faded_as: str | None = None) -> str:
    """Fişin düz metni (telefon OCR'ının ideal çıktısı gibi): satır başına bir fiş satırı, boşluklar sadeleşmiş."""
    return "\n".join(" ".join(t.split()) for _, t in receipt_lines(today, key, faded_as))


def receipt_jpg(path: Path, today: date, key: str = "a") -> None:
    """Fotoğraf gibi termal fiş: kâğıt dokusu, hafif eğim/perspektif, gölge, ışık düşüşü; deterjan fiyatındaki 8'in
    sağ üst yayı silik (6 gibi)."""
    import matplotlib
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    fonts = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
    mono = ImageFont.truetype(str(fonts / "DejaVuSansMono.ttf"), 30)
    bold = ImageFont.truetype(str(fonts / "DejaVuSansMono-Bold.ttf"), 30)
    big = ImageFont.truetype(str(fonts / "DejaVuSansMono-Bold.ttf"), 46)
    rng = random.Random(f"film-fis-{key}")
    lines = receipt_lines(today, key)
    lh, pad = 44, 44
    char_w = mono.getlength("0")
    pw = int(W_CH * char_w + 2 * pad)
    ph = len(lines) * lh + 2 * pad + 52
    paper = Image.new("L", (pw, ph), 250)
    dr = ImageDraw.Draw(paper)
    y = pad
    for kind, text in lines:
        f = big if kind == "b" else bold if kind == "B" else mono
        if kind in ("b", "c"):
            x = (pw - dr.textlength(text, font=f)) / 2
        else:
            x = pad
        ink = rng.randint(28, 52)
        if kind == "f":  # silik 8: fiyatın onlar basamağı ("*82,95" → '8'); ısıl başlık sağ üst yayı basmamış
            col = text.rindex("*") + 1
            dr.text((x, y), text[:col] + " " + text[col + 1:], fill=ink, font=f)
            gx = x + col * char_w
            dr.text((gx, y), "8", fill=GHOST, font=f)  # basılmayan noktaların soluk izi
            dr.text((gx, y), "6", fill=ink, font=f)  # basılan kısım: 8'in sağ üst yayı olmadan
        else:
            dr.text((x, y + (6 if kind == "b" else 0)), text, fill=ink, font=f)
        y += lh + (22 if kind == "b" else 0)
    # termal baskı: hafif bulanıklık + kâğıt tanesi + yatay şerit (baskı kafası)
    paper = paper.filter(ImageFilter.GaussianBlur(0.7))
    px = paper.load()
    for yy in range(ph):
        band = 3 if (yy // 7) % 9 == 0 else 0
        for xx in range(pw):
            v = px[xx, yy] + rng.randint(-6, 4) + band
            px[xx, yy] = max(0, min(255, v))
    paper_rgb = Image.merge("RGB", [paper.point(lambda v: min(255, v + 2)), paper, paper.point(lambda v: max(0, v - 8))])
    # sahne: masa, gölge, perspektif
    sw, sh = pw + 260, ph + 300
    scene = Image.new("RGB", (sw, sh), (92, 78, 66))
    sp = scene.load()
    for yy in range(sh):
        for xx in range(sw):
            k = 1.0 - 0.28 * ((xx / sw - 0.35) ** 2 + (yy / sh - 0.3) ** 2)
            n = rng.randint(-5, 5)
            sp[xx, yy] = (int(92 * k) + n, int(78 * k) + n, int(66 * k) + n)
    ox, oy = 130, 150
    shadow = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(shadow).rectangle((ox + 18, oy + 22, ox + pw + 18, oy + ph + 22), fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    scene.paste((40, 32, 26), (0, 0), shadow)
    # ışık düşüşü: kâğıdın sağ altı biraz gölgede
    shade = Image.new("L", (pw, ph), 0)
    sd = shade.load()
    for yy in range(ph):
        for xx in range(pw):
            sd[xx, yy] = int(60 * max(0.0, (xx / pw) * 0.5 + (yy / ph) * 0.7 - 0.55))
    paper_rgb = Image.composite(Image.new("RGB", (pw, ph), (120, 115, 108)), paper_rgb, shade)
    # perspektif: üst kenar biraz dar (telefon yukarıdan eğik tutulmuş)
    inset = 26
    coeffs = _perspective([(inset, 0), (pw - inset, 0), (pw, ph), (0, ph)], [(0, 0), (pw, 0), (pw, ph), (0, ph)])
    mask = Image.new("L", (pw, ph), 255).transform((pw, ph), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    warped = paper_rgb.transform((pw, ph), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    rot = -1.6
    warped = warped.rotate(rot, Image.BICUBIC, expand=True)
    mask = mask.rotate(rot, Image.BICUBIC, expand=True)
    scene.paste(warped, (ox - (warped.width - pw) // 2, oy - (warped.height - ph) // 2), mask)
    scene = scene.resize((sw * 3 // 4, sh * 3 // 4), Image.LANCZOS)
    scene.save(path, quality=86, optimize=True)


def _perspective(src, dst):
    """PIL PERSPECTIVE katsayıları: çıktı noktası dst → kaynak noktası src."""
    import numpy as np

    a = []
    b = []
    for (x, y), (u, v) in zip(dst, src):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    return np.linalg.solve(np.array(a, float), np.array(b, float)).tolist()


# ---------------------------------------------------------------- kart ekstreleri (Örnek Bank …2222)

def cuts(today: date, n: int = 3) -> list[date]:
    """Bugünden önceki son n kesim günü (eskiden yeniye)."""
    y, m = today.year, today.month
    last = date(y, m, CUT_DAY)
    if last >= today:
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        last = date(y, m, CUT_DAY)
    out = [last]
    while len(out) < n:
        p = out[0]
        y, m = (p.year, p.month - 1) if p.month > 1 else (p.year - 1, 12)
        out.insert(0, date(y, m, CUT_DAY))
    return out


def statements(today: date) -> list[dict]:
    """Üç dönem: [{cut, due, prev, paid, rows: [(tarih, açıklama, tutar)], total, min, ...}]. Taksitler dönem dönem
    ilerler (televizyon 1/6 → 3/6, koltuk 1/3 → 2/3); son dönemde ödeme kısmi → akdi faiz + BSMV + KKDF."""
    c1, c2, c3 = cuts(today)
    tv_day, sofa_day = c1 - timedelta(days=28), c2 - timedelta(days=26)
    tv, sofa = r2(13_500 / 6), r2(8_400 / 3)
    rd = receipt_date(today, "a")
    rt = receipt_total(receipt_items(today, "a"))
    plan = [
        (c1, 3_418.60, None, [
            (tv_day, "ÖRNEK ELEKTRONİK TELEVİZYON TAKSİT 1/6", tv),
            (c1 - timedelta(days=24), "MARKET B İSTANBUL", 1_184.35),
            (c1 - timedelta(days=19), "ÖRNEK GİYİM İSTANBUL", 1_650.00),
            (c1 - timedelta(days=15), "ÖRNEK AKARYAKIT ANKARA YOLU", 2_050.00),
            (c1 - timedelta(days=10), "ÖRNEK KAFE KADIKÖY", 145.00),
            (c1 - timedelta(days=5), "ÖRNEK ECZANE", 287.40),
            (c1 - timedelta(days=1), "YILLIK KART ÜCRETİ", 750.00),
        ]),
        (c2, None, None, [
            (tv_day, "ÖRNEK ELEKTRONİK TELEVİZYON TAKSİT 2/6", tv),
            (sofa_day, "ÖRNEK MOBİLYA KOLTUK TAKSİT 1/3", sofa),
            (c2 - timedelta(days=22), "MARKET B İSTANBUL", 1_312.80),
            (c2 - timedelta(days=17), "ÖRNEK AKARYAKIT ANKARA YOLU", 2_100.00),
            (c2 - timedelta(days=12), "ÖRNEK GİYİM İSTANBUL", 980.00),
            (c2 - timedelta(days=8), "ÖRNEK GİYİM İSTANBUL İADE", -450.00),
            (c2 - timedelta(days=5), "ÖRNEK RESTORAN", 1_240.00),
        ]),
        (c3, None, 6_000.00, [
            (tv_day, "ÖRNEK ELEKTRONİK TELEVİZYON TAKSİT 3/6", tv),
            (sofa_day, "ÖRNEK MOBİLYA KOLTUK TAKSİT 2/3", sofa),
            (c3 - timedelta(days=25), "MARKET B İSTANBUL", 1_245.30),
            (c3 - timedelta(days=20), "ÖRNEK AKARYAKIT ANKARA YOLU", 2_150.00),
            (c3 - timedelta(days=16), "ÖRNEK KAFE KADIKÖY", 186.00),
            (c3 - timedelta(days=12), "ÖRNEK KİTAPÇI", 420.00),
            (rd, "MARKET A İSTANBUL", rt),
        ]),
    ]
    out = []
    prev_total = None
    for cut, prev0, partial, rows in plan:
        prev = prev0 if prev0 is not None else prev_total
        prev_cut = date(cut.year, cut.month - 1, CUT_DAY) if cut.month > 1 else date(cut.year - 1, 12, CUT_DAY)
        pay_day = prev_cut + timedelta(days=DUE_DAYS)
        paid = partial if partial is not None else prev
        rows = [(pay_day, "ÖDEMENİZ İÇİN TEŞEKKÜRLER", -paid)] + rows
        if partial is not None:  # kısmi ödeme: kalan borca akdi faiz (+ BSMV %15, KKDF %15)
            rest = r2(prev - paid)
            faiz = r2(rest * INTEREST)
            rows += [(cut, "AKDİ FAİZ", faiz), (cut, "BSMV", r2(faiz * 0.15)), (cut, "KKDF", r2(faiz * 0.15))]
        rows.sort(key=lambda r: r[0])
        charges = r2(sum(a for _, _, a in rows if a > 0) + sum(a for _, t, a in rows if a < 0 and "ÖDEME" not in t))
        total = r2(prev - paid + charges)
        nxt = date(cut.year + (cut.month == 12), cut.month % 12 + 1, CUT_DAY)
        out.append({"cut": cut, "due": cut + timedelta(days=DUE_DAYS), "prev": prev, "paid": paid, "rows": rows,
                    "charges": charges, "total": total, "min": r2(total * 0.4),
                    "next_cut": nxt, "next_due": nxt + timedelta(days=DUE_DAYS),
                    "available": r2(CARD_LIMIT - total - _remaining_installments(rows))})
        prev_total = total
    return out


def _remaining_installments(rows) -> float:
    """Gelecek dönemlere kalan taksit tutarı (kullanılabilir limitten düşer)."""
    import re

    left = 0.0
    for _, t, a in rows:
        if m := re.search(r"(\d+)/(\d+)", t):
            left += a * (int(m.group(2)) - int(m.group(1)))
    return r2(left)


# ---------------------------------------------------------------- hesap dökümü (Örnek Bank vadesiz)

CLOSING = 38_420.50  # film_seed "Örnek Bank" hesabı


def account_rows(today: date) -> tuple[date, date, float, list[tuple[date, str, float]]]:
    """(başlangıç, bitiş, açılış bakiyesi, [(tarih, açıklama, tutar)])."""
    st3 = statements(today)[-1]
    c3 = st3["cut"]
    m1 = date(c3.year, c3.month - 1, 1) if c3.month > 1 else date(c3.year - 1, 12, 1)  # son kesimden önceki ay
    m2 = date(c3.year, c3.month, 1)
    rows = [
        (m1, "MAAŞ ÖDEMESİ ÖRNEK YAZILIM A.Ş.", 58_000.00),
        (m1 + timedelta(days=1), f"FAST GELEN {MEMBER} ORTAK GİDER PAYI", 4_000.00),
        (m1 + timedelta(days=4), "EFT GİDEN ÖRNEK KONUT YÖNETİM LTD. ŞTİ. KİRA", -24_000.00),
        (m1 + timedelta(days=9), "VADELİ HESABA VİRMAN", -20_000.00),
        (next(r[0] for r in st3["rows"] if r[2] < 0 and "ÖDEME" in r[1]), f"KREDİ KARTI ÖDEMESİ {CARD_MASKED}",
         -st3["paid"]),
        (m1 + timedelta(days=14), "ÖRNEK ELEKTRİK FATURA ÖDEMESİ", -1_045.80),
        (m1 + timedelta(days=20), "ATM PARA ÇEKME ÖRNEK BANK", -2_000.00),
        (m1 + timedelta(days=26), f"FAST GİDEN {MEMBER} TATİL KUMBARASI", -3_000.00),
        (m2, "MAAŞ ÖDEMESİ ÖRNEK YAZILIM A.Ş.", 58_000.00),
        (m2 + timedelta(days=2), "KREDİ KARTI ÖDEMESİ MAVİ BANK 0000 **** **** 1111", -7_850.00),
        (m2 + timedelta(days=4), "EFT GİDEN ÖRNEK KONUT YÖNETİM LTD. ŞTİ. KİRA", -24_000.00),
        (m2 + timedelta(days=5), "ÖRNEK İNTERNET OTOMATİK ÖDEME", -499.90),
        (m2 + timedelta(days=7), f"FAST GELEN {MEMBER} MARKET PAYI", 2_500.00),
    ]
    rows = [r for r in rows if r[0] < today]
    rows.sort(key=lambda r: r[0])
    opening = r2(CLOSING - sum(a for _, _, a in rows))
    return m1, today - timedelta(days=1), opening, rows


def rent_row(today: date) -> tuple[date, str, float]:
    """Dekontun karşılığı: dökümdeki son kira EFT'si."""
    return [r for r in account_rows(today)[3] if "KİRA" in r[1]][-1]


# ---------------------------------------------------------------- fatura (Örnek Elektrik)

def bill(today: date) -> dict:
    """film_seed'deki faturayla aynı: 1.120,40 TL, son ödeme bugün + 6."""
    energy, dist = 700.00, 198.67
    btv = r2(energy * 0.05)                  # elektrik ve havagazı tüketim vergisi (%5, enerji bedeli üzerinden)
    base = r2(energy + dist + btv)
    kdv = r2(base * 0.20)
    assert r2(base + kdv) == 1_120.40        # film_seed'deki tutar
    issued = today - timedelta(days=6)
    p_end = date(issued.year, issued.month, 1) - timedelta(days=1)
    return {"no": f"OEL{issued:%Y}{issued:%m}0004127", "issued": issued, "due": today + timedelta(days=6),
            "period": (date(p_end.year, p_end.month, 1), p_end), "kwh": 318, "energy": energy, "dist": dist,
            "btv": btv, "kdv": kdv, "total": r2(base + kdv), "tesisat": "4000000127", "sozlesme": "1000000555"}


# ---------------------------------------------------------------- PDF

def _pdf():
    import matplotlib
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fonts = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
    for name, f in (("DV", "DejaVuSans.ttf"), ("DVB", "DejaVuSans-Bold.ttf"), ("DVM", "DejaVuSansMono.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / f)))


def _canvas(path: Path, title: str):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    _pdf()
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)  # invariant: aynı içerik aynı bayt (tarih/kimlik sabit)
    c.setTitle(title)
    c.setAuthor("HesapKitap (kurgu)")
    c.setCreator("scripts/make_film_files.py")
    return c


def _head(c, brand: str, subtitle: str, color: str, right: list[str] = ()):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4

    w, h = A4
    c.setFillColor(HexColor(color))
    c.rect(0, h - 74, w, 74, stroke=0, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont("DVB", 20)
    c.drawString(36, h - 40, brand)
    c.setFont("DV", 10)
    c.drawString(36, h - 58, subtitle)
    for i, s in enumerate(right):
        c.drawRightString(w - 36, h - 34 - 14 * i, s)
    c.setFillColor(HexColor("#92400e"))
    c.setFont("DV", 7.5)
    c.drawString(36, h - 88, DISCLAIMER)
    c.setFillColor(HexColor("#111827"))
    return h - 112


def statement_pdf(path: Path, st: dict) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4

    w, _h = A4
    c = _canvas(path, f"Örnek Bank kredi kartı hesap özeti {d(st['cut'])}")
    y = _head(c, "ÖRNEK BANK", "Kredi Kartı Hesap Özeti", "#0f766e", ["Örnek Kart", CARD_MASKED])
    info = [("Kart Sahibi", OWNER), ("Kart Numarası", CARD_MASKED), ("Hesap Kesim Tarihi", d(st["cut"])),
            ("Son Ödeme Tarihi", d(st["due"])), ("Dönem Borcu", f"{tr(st['total'])} TL"),
            ("Asgari Ödeme Tutarı", f"{tr(st['min'])} TL"), ("Önceki Dönem Borcu", f"{tr(st['prev'])} TL"),
            ("Ödemeleriniz", f"{tr(st['paid'])} TL"), ("Dönem İçi Harcamalar", f"{tr(st['charges'])} TL"),
            ("Kart Limiti", f"{tr(CARD_LIMIT)} TL"), ("Kullanılabilir Limit", f"{tr(st['available'])} TL"),
            ("Sonraki Hesap Kesim Tarihi", d(st["next_cut"])), ("Sonraki Son Ödeme Tarihi", d(st["next_due"]))]
    for k, v in info:
        c.setFont("DV", 9.5)
        c.drawString(36, y, k)
        c.setFont("DVB", 9.5)
        c.drawString(210, y, v)
        y -= 15
    y -= 6
    c.setFont("DV", 8.5)
    c.setFillColor(HexColor("#374151"))
    c.drawString(36, y, f"Hesap özeti: {tr(st['prev'])} + {tr(st['charges'])} - {tr(st['paid'])} = {tr(st['total'])}")
    y -= 22
    c.setFillColor(HexColor("#e5e7eb"))
    c.rect(30, y - 5, w - 60, 17, stroke=0, fill=1)
    c.setFillColor(HexColor("#111827"))
    c.setFont("DVB", 8.5)
    c.drawString(36, y, "İşlem Tarihi")
    c.drawString(110, y, "Açıklama")
    c.drawRightString(w - 36, y, "Tutar (TL)")
    y -= 18
    c.setFont("DV", 8.8)
    for day, text, amount in st["rows"]:
        c.drawString(36, y, d(day))
        c.drawString(110, y, text)
        c.drawRightString(w - 36, y, tr(amount))
        y -= 15
    y -= 14
    c.setFont("DV", 7.5)
    c.setFillColor(HexColor("#6b7280"))
    for s in ("Asgari ödeme tutarının son ödeme tarihine kadar ödenmemesi durumunda gecikme faizi uygulanır.",
              f"Akdi faiz oranı aylık %{INTEREST * 100:.2f}".replace(".", ",") + "; BSMV ve KKDF faiz tutarı üzerinden %15."):
        c.drawString(36, y, s)
        y -= 11
    c.save()


def account_pdf(path: Path, today: date) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4

    w, _h = A4
    start, end, opening, rows = account_rows(today)
    c = _canvas(path, "Örnek Bank vadesiz hesap hareketleri")
    y = _head(c, "ÖRNEK BANK", "Vadesiz TL Hesap Hareketleri", "#0f766e", [f"Dönem: {d(start)} - {d(end)}"])
    for k, v in (("Hesap Sahibi", OWNER), ("IBAN", IBAN_OWNER), ("Şube", "ÖRNEK ŞUBESİ (0001)"),
                 ("Devir Bakiyesi", f"{tr(opening)} TL")):
        c.setFont("DV", 9.5)
        c.drawString(36, y, f"{k}:")
        c.setFont("DVB", 9.5)
        c.drawString(150, y, v)
        y -= 15
    y -= 12
    c.setFillColor(HexColor("#e5e7eb"))
    c.rect(30, y - 5, w - 60, 17, stroke=0, fill=1)
    c.setFillColor(HexColor("#111827"))
    c.setFont("DVB", 8.5)
    c.drawString(36, y, "Tarih")
    c.drawString(104, y, "Açıklama")
    c.drawRightString(w - 110, y, "Tutar")
    c.drawRightString(w - 36, y, "Bakiye")
    y -= 18
    bal = opening
    c.setFont("DV", 8.6)
    for day, text, amount in rows:
        bal = r2(bal + amount)
        c.drawString(36, y, d(day))
        c.drawString(104, y, text)
        c.drawRightString(w - 110, y, tr(amount))
        c.drawRightString(w - 36, y, tr(bal))
        y -= 15
    assert abs(bal - CLOSING) < 0.005
    c.save()


def dekont_pdf(path: Path, today: date) -> None:
    from reportlab.lib.colors import HexColor

    day, _text, amount = rent_row(today)
    c = _canvas(path, "Örnek Bank EFT dekontu")
    y = _head(c, "ÖRNEK BANK", "EFT DEKONTU", "#0f766e", ["Örnek Şubesi (0001)"])
    fields = [("İŞLEM TARİHİ", f"{d(day)} 10:24"), ("İŞLEM TÜRÜ", "EFT"), ("GÖNDEREN", OWNER),
              ("GÖNDEREN IBAN", IBAN_OWNER), ("ALICI", "ÖRNEK KONUT YÖNETİM LTD. ŞTİ."),
              ("ALICI IBAN", IBAN_LANDLORD), ("ALICI BANKA", "MAVİ BANK"),
              ("İŞLEM TUTARI (TL)", tr(-amount)), ("MASRAF (TL)", "0,00"),
              ("AÇIKLAMA", f"{MONTHS[day.month - 1]} {day.year} KİRA ÖDEMESİ"), ("SORGU NO", f"{day:%Y%m%d}000123")]
    for k, v in fields:
        c.setFont("DVB", 10)
        c.drawString(36, y, f"{k} : {v}")
        y -= 20
    y -= 10
    c.setFont("DV", 8)
    c.setFillColor(HexColor("#6b7280"))
    c.drawString(36, y, "Bu dekont elektronik ortamda üretilmiştir. Kurgu belgedir; hiçbir hesapta karşılığı yoktur.")
    c.save()


def bill_pdf(path: Path, today: date) -> None:
    from reportlab.lib.colors import HexColor

    b = bill(today)
    c = _canvas(path, "Örnek Elektrik faturası")
    y = _head(c, "ÖRNEK ELEKTRİK PERAKENDE SATIŞ A.Ş.", "Elektrik Faturası (e-Fatura)", "#1d4ed8")
    for k, v in (("Fatura No", b["no"]), ("Sayın", OWNER), ("Adres", "ÖRNEK MAH. DEMO CAD. NO:12 D:4 MERKEZ / İSTANBUL"),
                 ("Fatura Tarihi", d(b["issued"])), ("Tesisat No", b["tesisat"]),
                 ("Sözleşme Hesap No", b["sozlesme"]),
                 ("Fatura Dönemi", f"{d(b['period'][0])} - {d(b['period'][1])}"), ("Tüketim", f"{b['kwh']} kWh")):
        c.setFont("DV", 10)
        c.drawString(36, y, k)
        c.setFont("DVB", 10)
        c.drawString(170, y, v)
        y -= 16
    y -= 12
    for k, v in (("Enerji Bedeli", b["energy"]), ("Dağıtım Bedeli", b["dist"]),
                 ("Elektrik Tüketim Vergisi (%5)", b["btv"]), ("KDV (%20)", b["kdv"])):
        c.setFont("DV", 10)
        c.drawString(36, y, k)
        c.drawRightString(330, y, f"{tr(v)} TL")
        y -= 16
    y -= 10
    c.setFillColor(HexColor("#fef3c7"))
    c.rect(30, y - 30, 310, 48, stroke=0, fill=1)
    c.setFillColor(HexColor("#111827"))
    c.setFont("DVB", 12)
    c.drawString(36, y, "ÖDENECEK TUTAR")
    c.drawRightString(330, y, f"{tr(b['total'])} TL")
    c.setFont("DVB", 11)
    c.drawString(36, y - 20, "SON ÖDEME TARİHİ")
    c.drawRightString(330, y - 20, d(b["due"]))
    y -= 60
    c.setFont("DV", 8)
    c.setFillColor(HexColor("#6b7280"))
    c.drawString(36, y, "Otomatik ödeme talimatı için Örnek Bank şubelerine başvurabilirsiniz.")
    c.save()


# ---------------------------------------------------------------- dosyalar

def files(today: date) -> dict[str, object]:
    """Çıktı dosya adı → üretici."""
    sts = statements(today)
    out = {"fis-market-a.jpg": lambda p: receipt_jpg(p, today, "a"),
           "fis-market-a-adetli.jpg": lambda p: receipt_jpg(p, today, "b")}
    for i, st in enumerate(sts, 1):
        out[f"ekstre-{i}-ornek-bank-{st['cut']:%Y-%m}.pdf"] = functools.partial(statement_pdf, st=st)
    out["hesap-dokumu-ornek-bank.pdf"] = lambda p: account_pdf(p, today)
    out["fatura-ornek-elektrik.pdf"] = lambda p: bill_pdf(p, today)
    out["dekont-ornek-bank-eft.pdf"] = lambda p: dekont_pdf(p, today)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--today", type=date.fromisoformat, default=TODAY, help="belgelerin göreli olduğu gün (YYYY-AA-GG)")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    for name, make in files(a.today).items():
        make(a.out / name)
        print("yazıldı:", (a.out / name).relative_to(ROOT) if a.out.is_relative_to(ROOT) else a.out / name)


if __name__ == "__main__":
    main()
