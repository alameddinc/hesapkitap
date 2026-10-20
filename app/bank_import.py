"""Banka hesap hareketleri (vadesiz hesap) içe aktarma: Excel (.xlsx/.xls) ya da CSV.

Bankalar farklı biçim kullanır; sütunlar başlıklardan tahmin edilir, kullanıcı önizlemede düzeltebilir.
- Gelen para → gelir, giden para → gider (banka kartı / havale)
- Kredi kartı borç ödemesi → gider sayılmaz (harcamalar ekstreden gelir), ilgili ekstre "ödendi" yapılır
- Kendi hesapları arası virman → atlanır
- Maaş → maaş sabit kalemine bağlanır (onay bekleyenden düşer)
- Aynı hareket ikinci kez eklenmez
"""
import csv
import hashlib
import io
import re
from datetime import date, datetime

from app.textnorm import normalize

HEADER_HINTS = {
    "date": ("TARIH", "ISLEM TARIHI", "VALOR", "DATE"),
    "description": ("ACIKLAMA", "ISLEM ACIKLAMASI", "ISLEM", "AYRINTI", "DETAY", "DESCRIPTION"),
    "amount": ("TUTAR", "ISLEM TUTARI", "AMOUNT"),
    "debit": ("BORC", "CIKAN", "GIDEN", "HARCAMA"),
    "credit": ("ALACAK", "GIREN", "GELEN", "YATAN"),
    "balance": ("BAKIYE", "BALANCE"),
}
CARD_PAYMENT = re.compile(r"KREDI KARTI|KART BORC|KK BORC|KK ODEME|KARTI ODEME|KART ODEME|CREDIT CARD")
TRANSFER = re.compile(r"HESAPLAR ARASI|VIRMAN|KENDI HESAB|HESABIMA|ARA HESAP|DOVIZ ALIS|DOVIZ SATIS|VADELI|KATILMA HESAB")
SALARY = re.compile(r"MAAS|UCRET ODEME|PERSONEL ODEME|BORDRO")


class ImportError_(ValueError):
    pass


# ---------------------------------------------------------------- dosya → satırlar

def read_rows(data: bytes, filename: str) -> list[list[str]]:
    name = filename.lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
    if name.endswith(".xls"):
        import xlrd

        book = xlrd.open_workbook(file_contents=data)
        sh = book.sheet_by_index(0)
        out = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate_as_datetime(cell.value, book.datemode).date().isoformat())
                else:
                    row.append(_cell(cell.value))
            out.append(row)
        return out
    # CSV: kodlama ve ayraç tahmini
    for enc in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_("Dosya okunamadı")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if text.count(";") > text.count(",") else ","
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return (v.date() if isinstance(v, datetime) else v).isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


# ---------------------------------------------------------------- sütun tahmini

def detect(rows: list[list[str]]) -> dict:
    """Başlık satırını ve sütun eşlemesini tahmin et."""
    best, best_score = 0, -1
    for i, row in enumerate(rows[:40]):
        norm = [normalize(c) for c in row]
        score = sum(any(any(h == n or n.startswith(h) for h in hints) for n in norm) for hints in HEADER_HINTS.values())
        if score > best_score:
            best, best_score = i, score
    header = [normalize(c) for c in rows[best]] if rows else []
    mapping: dict[str, int | None] = {}
    for key, hints in HEADER_HINTS.items():
        idx = next((j for j, n in enumerate(header) if any(n == h or n.startswith(h) for h in hints)), None)
        # "ISLEM TARIHI" hem tarih hem açıklama ipucuna uymasın: tarih sütunu açıklama olamaz
        if key == "description" and idx is not None and idx == mapping.get("date"):
            idx = next((j for j, n in enumerate(header) if j != idx and any(n.startswith(h) for h in hints)), None)
        mapping[key] = idx
    return {"header_row": best, "columns": rows[best] if rows else [], "mapping": mapping}


# ---------------------------------------------------------------- değer ayrıştırma

def parse_amount(v: str) -> float | None:
    t = re.sub(r"[^\d,.\-+]", "", v or "")
    if not t or t in "-+":
        return None
    neg = t.startswith("-") or (v or "").strip().endswith("-")
    t = t.lstrip("+-").rstrip("-")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        x = float(t)
    except ValueError:
        return None
    return -x if neg else x


def parse_date(v: str) -> date | None:
    v = (v or "").strip()[:19]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S",
                "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def classify(desc: str, amount: float) -> str:
    n = normalize(desc)
    if CARD_PAYMENT.search(n) and amount < 0:
        return "kart_odemesi"
    if TRANSFER.search(n):
        return "virman"
    return "gelir" if amount > 0 else "gider"


def row_hash(d: date, desc: str, amount: float, balance: str) -> str:
    return hashlib.sha1(f"{d}|{normalize(desc)}|{amount:.2f}|{balance}".encode()).hexdigest()[:20]


def parse(rows: list[list[str]], mapping: dict, header_row: int) -> list[dict]:
    out = []
    for row in rows[header_row + 1:]:
        get = lambda k: row[mapping[k]] if mapping.get(k) is not None and mapping[k] < len(row) else ""  # noqa: E731
        d = parse_date(get("date"))
        desc = get("description")
        if mapping.get("amount") is not None:
            amt = parse_amount(get("amount"))
        else:
            debit, credit = parse_amount(get("debit")) or 0, parse_amount(get("credit")) or 0
            amt = (abs(credit) - abs(debit)) if (debit or credit) else None
        if not d or amt is None or amt == 0 or not desc:
            continue
        out.append({"date": d.isoformat(), "description": desc[:120], "amount": round(amt, 2),
                    "type": classify(desc, amt), "hash": row_hash(d, desc, amt, get("balance"))})
    return out
