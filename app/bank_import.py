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


def _squash(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize(s))


def is_self_transfer(desc: str, owners: list[str]) -> bool:
    """Kendi hesapları arası: gönderen ve alıcı aynı kişi (boşluksuz yazımlar dahil, ör. Kuveyt Türk)."""
    sq = _squash(desc)
    m = re.search(r"GONDEREN([A-Z]+?)ALICI([A-Z]+?)(FAST|EFT|HAVALE|PARA|$)", sq)
    if m and m.group(1) == m.group(2):
        return True
    names = {_squash(o) for o in owners if o and len(_squash(o)) >= 6}
    if not names:
        return False
    # "X hesabından … X hesabına" / "X TARAFINDAN GÖNDERİLEN … X hesabına gelen"
    for nm in names:
        if sq.count(nm) >= 2 and re.search(r"HESABINDAN|TARAFINDANGONDERILEN|GONDEREN", sq):
            return True
    return False


def classify(desc: str, amount: float, owners: list[str] | None = None) -> str:
    n = normalize(desc)
    sq = _squash(desc)
    if amount < 0 and (CARD_PAYMENT.search(n) or re.search(r"KREDIKARTI(BORC|ODEME)|KARTBORC|KKBORC", sq)):
        return "kart_odemesi"
    if TRANSFER.search(n) or is_self_transfer(desc, owners or []):
        return "virman"
    return "gelir" if amount > 0 else "gider"


def finalize(rows: list[dict], owners: list[str]) -> list[dict]:
    """PDF'ten gelen satırlara tür ve parmak izi ekle."""
    out = []
    for r in rows:
        d = parse_date(r["date"])
        if not d or not r.get("amount"):
            continue
        # Parmak izi açıklamaya bakmaz: model aynı satırı her okumada biraz farklı yazabilir; tarih + tutar +
        # bakiye (zincirle doğrulanmış) hareketi tek başına tanımlar, aynı PDF tekrar yüklenince çift kayıt olmaz
        bal = r.get("balance")
        out.append({"date": d.isoformat(), "description": r["description"][:500], "amount": round(r["amount"], 2),
                    "type": classify(r["description"], r["amount"], owners),
                    "hash": row_hash(d, "" if bal is not None else r["description"], r["amount"], f"{bal:.2f}" if bal is not None else "")})
    return out


def row_hash(d: date, desc: str, amount: float, balance: str) -> str:
    return hashlib.sha1(f"{d}|{normalize(desc)}|{amount:.2f}|{balance}".encode()).hexdigest()[:20]


def parse(rows: list[list[str]], mapping: dict, header_row: int, owners: list[str] | None = None) -> list[dict]:
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
        out.append({"date": d.isoformat(), "description": desc[:500], "amount": round(amt, 2),
                    "type": classify(desc, amt, owners), "hash": row_hash(d, desc, amt, get("balance"))})
    return out


# ---------------------------------------------------------------- karşı taraf (kişi) çıkarma

_BANK_WORDS = re.compile(r"\b(TURKIYE|T\.?|IS|HALK|ZIRAAT|VAKIFLAR|VAKIFBANK|KUVEYT|TURK|KATILIM|GARANTI|BBVA|YAPI|VE|KREDI|"
                         r"AKBANK|QNB|FINANSBANK|DENIZBANK|ING|TEB|ALBARAKA|ENPARA|PAPARA|BANKASI|BANKA|A\.?S\.?|T\.?A\.?S\.?)\b")


def _clean_name(raw: str) -> str:
    n = normalize(raw)
    n = re.sub(r"TR\d{2}[\d\s*]+(NOLU|NO'LU|NOLU)?", " ", n)
    n = _BANK_WORDS.sub(" ", n)
    n = re.sub(r"\b(ALINAN|GELEN|GIDEN|GONDERILEN|HAVALE|FAST|EFT|ANLIK|ODEME|NOLU|NO LU)\b", " ", n)
    n = re.sub(r"[^A-Z ]", " ", n)
    words = [w for w in n.split() if len(w) > 1]
    cut = next((i for i, w in enumerate(words) if w in _COMPANY_CUT), None)
    if cut:  # şirket: 'ELIF ORNEK EGITIM DANISMANLIK LIMITED SIRKETI' → unvanın başı
        return " ".join(words[max(0, cut - 4):cut][:3])
    return " ".join(words[-3:])


_COMPANY_CUT = {"LIMITED", "LTD", "SIRKETI", "STI", "ANONIM", "TIC", "TICARET", "SAN", "SANAYI"}


def _tr_title(word: str, dotless_i: bool = True) -> str:
    low = word.replace("I", "ı" if dotless_i else "i").replace("İ", "i").lower()
    return (low[0].replace("i", "İ").replace("ı", "I").upper() + low[1:]) if low else low


def _pretty(clean: str, original: str) -> str:
    """'ZEYNEP OZDEMIR' → asıl metindeki yazılışla 'Zeynep Özdemir'."""
    tokens = re.findall(r"[^\W\d_]+", original)
    out = []
    for w in clean.split():
        found = [t for t in tokens if normalize(t) == w]
        # büyük harfli yazımda İ/I ayrımı kaybolabiliyor ('DEMIR'): küçük harf içeren yazılışı tercih et
        orig = next((t for t in found if not t.isupper()), found[0] if found else w)
        # metinde hiç 'İ' yoksa banka büyük harfte İ yerine I basıyordur ('AYŞE DEMIR')
        out.append(_tr_title(orig, dotless_i="İ" in original))
    return " ".join(out)


def counterparty(desc: str, amount: float, owners: list[str] | None = None) -> str | None:
    """Havale/FAST açıklamasından karşı tarafın adı. Kendisi ise None."""
    n = normalize(desc)
    own = {_squash(o) for o in owners or [] if o}
    name = None
    m = re.search(r"GONDEREN\s*:\s*([A-Z ]+?)\s*,\s*ALICI\s*:\s*([A-Z ]+?)\s*(,|$)", n)
    if m:
        name = m.group(2) if amount < 0 else m.group(1)
    elif amount < 0 and (m := re.search(r"HESABINDAN\s+(.+?)\s+HESABINA", n)):
        name = m.group(1)
    elif amount > 0 and (m := re.search(r"(?:FAST\s*-\s*)?([A-Z ]{5,}?)\s+TARAFINDAN GONDERILEN", n)):
        name = m.group(1)
    elif amount > 0 and (m := re.search(r"([A-Z ]{5,}?)\s+HESABINDAN", re.sub(r"TR\d{2}[\d\s*]+NOLU", " ", n))):
        name = m.group(1)
    if not name and amount < 0:
        # Halkbank: "GIDEN FAST - X TARAFINDAN GONDERILEN,Alıcı,Banka A.S.,TR.." ya da "not,Alıcı,Banka,TR.."
        parts = [p.strip() for p in re.sub(r"^.*?TARAFINDAN GONDERILEN\s*,", "", n).split(",")]
        bank_i = next((i for i, p in enumerate(parts) if re.search(r"BANK|T\.A\.[OS]|KATILIM", p)), None)
        # yalnızca "…,Alıcı,Banka,TRxx" kalıbı (fatura açıklamalarındaki virgüller karışmasın)
        if bank_i and bank_i + 1 < len(parts) and re.match(r"TR\d{2}", parts[bank_i + 1]):
            name = parts[bank_i - 1]
    if not name:
        return None
    clean = _clean_name(name)
    if not clean or _squash(clean) in own or any(_squash(clean) in o or o in _squash(clean) for o in own):
        return None
    return _pretty(clean, desc)


def transfer_note(desc: str) -> str | None:
    """'FAST Anlık Ödeme ev kirası (… sorgu no'lu …)' → 'ev kirası'"""
    m = re.match(r"\s*(?:FAST ANLIK ODEME|GONDERILEN HAVALE|FAST ANLIK ÖDEME|GÖNDERİLEN HAVALE)\s+(.+?)\s*(\(|/|$)", desc, re.I)
    note = m.group(1).strip() if m else None
    return note if note and not re.match(r"TR\d", note) else None
