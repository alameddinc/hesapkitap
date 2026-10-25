"""Yapay zekâsız (yerel) fiş/fatura okuma: Tesseract OCR (Türkçe) + kural tabanlı ayrıştırma.

Gemini anahtarı yoksa ya da ortak kota dolduysa devreye girer. Belge sunucudan dışarı çıkmaz. Toplam, tarih,
işyeri ve ödeme şekli genelde doğru çıkar; ürün satırları en iyi çabayla. Sonuç formda kontrol edilir.
PDF (e-arşiv fatura/fiş) ise önce içindeki metin kullanılır, OCR gerekmez.
"""
import io
import re
import shutil
import subprocess

AMOUNT = r"(\d{1,3}(?:[.\s]\d{3})*,\d{2}|\d+,\d{2})"
_DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2}|\d{2})\b")
_TOTAL_KEYS = ("GENEL TOPLAM", "ODENECEK TUTAR", "ÖDENECEK TUTAR", "TOPLAM TUTAR", "TOPLAM", "TUTAR", "TOP ")
_SKIP_TOTAL = ("KDV", "TOPKDV", "ARA TOPLAM", "INDIRIM", "İNDİRİM", "PARA USTU", "PARA ÜSTÜ", "NAKIT", "NAKİT")
_MEAL = ("MULTINET", "SODEXO", "PLUXEE", "EDENRED", "TICKET", "SETCARD", "METROPOL", "YEMEK KART", "YEMEK CEK")
_NOT_MERCHANT = ("TARIH", "TARİH", "SAAT", "FIS", "FİŞ", "VKN", "TCKN", "VERGI", "VERGİ", "TEL", "ADRES", "MAH", "CAD",
                 "SK.", "SOK", "NO:", "Z NO", "EKU", "MERSIS", "MERSİS", "WWW", "HTTP")


def available() -> bool:
    return shutil.which("tesseract") is not None


def _up(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def _num(v: str) -> float | None:
    try:
        return float(v.replace(" ", "").replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _image_text(data: bytes) -> str:
    """Ön işleme (gri, büyütme, kontrast) + Tesseract (tur+eng)."""
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("L")
    if im.width < 1400:  # küçük fotoğraflarda karakterler okunaklı olsun
        k = 1400 / im.width
        im = im.resize((int(im.width * k), int(im.height * k)))
    im = ImageOps.autocontrast(im)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    langs = "tur+eng" if _has_lang("tur") else "eng"
    r = subprocess.run(["tesseract", "stdin", "stdout", "-l", langs, "--psm", "4"], input=buf.getvalue(),
                       capture_output=True, timeout=60)
    return r.stdout.decode("utf-8", "ignore")


_langs: set[str] | None = None


def _has_lang(code: str) -> bool:
    global _langs
    if _langs is None:
        try:
            out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, timeout=10).stdout.decode()
            _langs = {x.strip() for x in out.splitlines()[1:]}
        except Exception:  # noqa: BLE001
            _langs = set()
    return code in _langs


def _pdf_text(data: bytes) -> str:
    from app.parsers import extract_text

    return extract_text(data, None)


def text_of(data: bytes, mime: str) -> str:
    if mime == "application/pdf":
        t = _pdf_text(data)
        if t.strip():
            return t
        raise ValueError("Taranmış PDF yerel olarak okunamıyor; fotoğrafını çek ya da Gemini anahtarı gir")
    if not available():
        raise ValueError("Yerel okuma (Tesseract) bu sunucuda kurulu değil")
    return _image_text(data)


def _date(text: str) -> str | None:
    for m in _DATE.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(y) + 2000 if len(y) == 2 else int(y)
        if 1 <= d <= 31 and 1 <= mo <= 12 and 2000 <= y <= 2100:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _total(lines: list[str]) -> float | None:
    best = None
    for key in _TOTAL_KEYS:  # en güvenilir anahtar kelimeden başla
        for line in lines:
            u = _up(line)
            if key in u and not any(sk in u for sk in _SKIP_TOTAL if sk not in key):
                vals = [_num(x) for x in re.findall(AMOUNT, line)]
                vals = [v for v in vals if v]
                if vals:
                    best = max(vals) if best is None else max(best, max(vals))
        if best:
            return best
    vals = [_num(x) for line in lines[-15:] for x in re.findall(AMOUNT, line)]  # yoksa alttaki en büyük tutar
    vals = [v for v in vals if v]
    return max(vals) if vals else None


def _merchant(lines: list[str]) -> str:
    for line in lines[:6]:
        u = _up(line).strip(" *-.")
        if len(u) >= 3 and not any(k in u for k in _NOT_MERCHANT) and not re.search(r"\d{3,}", u):
            return line.strip(" *-.")[:80]
    return "Fiş"


_COMPANY = re.compile(r"A\.?\s?Ş\.?|A\.?S\.?\b|LTD|ŞT[İI]|STI\b|T[İI]C\.|SAN\.|ELEKTR[İI]K|DO[ĞG]ALGAZ|SU VE KANAL|"
                      r"TELEKOM|TURKCELL|VODAFONE|DA[ĞG]ITIM|PERAKENDE")


def _company(lines: list[str]) -> str | None:
    """Faturada kurum: şirket eki ya da sektör kelimesi geçen ilk satır."""
    for line in lines[:12]:
        if _COMPANY.search(_up(line)) and not re.search(r"\d{4,}", line):
            return line.strip(" *-.")[:80]
    return None


def _items(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        m = re.match(rf"^\s*(.+?)\s+[*xX%]?\s*{AMOUNT}\s*$", line)
        if not m:
            continue
        name = m.group(1).strip(" *.")
        u = _up(name)
        if len(name) < 2 or any(k in u for k in ("TOPLAM", "KDV", "NAKIT", "NAKİT", "KART", "PARA", "TUTAR", "INDIRIM", "İNDİRİM")):
            continue
        out.append({"name": name[:60], "product": None, "brand": None, "qty": 1, "unit": "adet",
                    "amount": _num(m.group(2)), "split": None})
    return out[:40]


def parse(data: bytes, mime: str, categories: list[str], more: list[tuple[bytes, str]] | None = None) -> dict:
    """receipt.parse_document ile aynı biçimde sonuç (type receipt/bill). more: uzun fişin devam fotoğrafları."""
    text = "\n".join(text_of(d, m) for d, m in [(data, mime), *(more or [])])
    lines = [x for x in (ln.strip() for ln in text.splitlines()) if x]
    if not lines:
        raise ValueError("Belgede okunabilir yazı bulunamadı")
    u = _up(text)
    due = re.search(r"SON [ÖO]DEME(?: TAR[İI]H[İI])?\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", u)
    if due and "FATURA" in u:
        return {"type": "bill", "provider": (_company(lines) or _merchant(lines))[:60], "legal_name": None, "category": "Faturalar",
                "amount": _total(lines), "due_date": _date(due.group(1)), "description": "", "confidence": "düşük",
                "engine": "yerel"}
    payment = "yemek_karti" if any(k in u for k in _MEAL) else \
        "kart" if re.search(r"KRED[İI] KART|K\.KART|BANKA KART|KART\b|POS", u) else \
        "nakit" if re.search(r"NAK[İI]T", u) else "bilinmiyor"
    card = re.search(r"(?:\d{4,6}|[*X]{4})[\s*X]*[*X]{4,}[\s*X]*(\d{4})\b", u)  # 454360******6081 / **** **** 6081
    inst = re.search(r"(\d{1,2})\s*TAKS[İI]T|TAKS[İI]T\s*(?:SAYISI)?\s*:?\s*(\d{1,2})", u)
    n = int(inst.group(1) or inst.group(2)) if inst else 1
    return {"type": "receipt", "merchant": _merchant(lines), "date": _date(text), "total": _total(lines),
            "payment": payment, "payment_card": next((k.title() for k in _MEAL if k in u), None) if payment == "yemek_karti" else None,
            "installments": n if 2 <= n <= 36 else 1, "card_last4": card.group(1) if card else None, "category": "Diğer" if "Diğer" in categories else categories[0],
            "items": _items(lines), "legal_name": None, "address": None, "branch": None, "confidence": "düşük",
            "engine": "yerel"}
