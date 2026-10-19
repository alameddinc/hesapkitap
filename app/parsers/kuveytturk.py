"""Kuveyt Türk kredi kartı ekstresi (PDF) parser'ı.

Format farkları (VakıfBank'a göre):
- Tarih 15/09/2026, tutar Türk formatı '1.234,56 TL', ödeme negatif '-12.345,67 TL'.
- Taksitlendirilmiş işlem: 'TOPLAM TL İşlemin 1 / 9 Taksidi TAKSİT TL' → ekstreye yansıyan = taksit tutarı.
  Aynı işlem için 'Vade Farki - ...', 'BSMV - ...', 'KKDF - ...' satırları da taksitli gelir (katılım bankası kâr payı).
- Açıklama sonunda şehir + ülke: 'ORNEK CAFE İSTANBUL TR' → 'ORNEK CAFE'.
"""
import re
from datetime import date, datetime

from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND, Statement, Transaction
from app.textnorm import normalize

BANK = "Kuveyt Türk"

_AMT = r"-?\d{1,3}(?:\.\d{3})*,\d{2}"
TXN_RE = re.compile(
    rf"^(?P<date>\d{{2}}/\d{{2}}/\d{{4}})\s+(?P<desc>.+?)\s+(?P<amount>{_AMT}) TL"
    rf"(?:\s+İşlemin (?P<inst_no>\d+) / (?P<inst_total>\d+) Taksidi (?P<inst_amt>{_AMT}) TL)?"
    rf"(?:\s+(?P<points>[\d.,]+))?$"
)
CARD_HEADER_RE = re.compile(r"^(?P<card>\d{6}\*+\d{4}) ile yapılan işlemler")
LOCATION_RE = re.compile(r"\s+\S+\s+(?:TR|TUR)$")
FEE_PREFIX_RE = re.compile(r"^(Vade Fark[ıi]|BSMV|KKDF|Kâr Payı|Kar Payı|Gecikme)\b", re.I)


def amount(raw: str) -> float:
    return float(raw.replace(".", "").replace(",", "."))


def _d(s: str) -> date:
    return datetime.strptime(s, "%d/%m/%Y").date()


def _mask(card: str) -> str:
    m = re.match(r"(\d{4})\d*\*+(\d{4})", card)
    return f"{m.group(1)}***{m.group(2)}" if m else card


def _field(text: str, label: str, pattern: str = r"(\S+)") -> str | None:
    m = re.search(rf"{label}\s*/[^\n]*?\s{pattern}(?:\s*TL)?\s*$", text, re.M)
    return m.group(1) if m else None


def is_kuveytturk(text: str) -> bool:
    return "Kuveyt Türk" in text and "KREDİ KARTI EKSTRESİ" in text


def _clean_desc(desc: str) -> str:
    return LOCATION_RE.sub("", desc).strip()


def parse_text(text: str) -> Statement:
    card_no = re.search(r"Kart No /Card Number\s+(\S+)", text)
    if not card_no:
        raise ValueError("Kart No bulunamadı; Kuveyt Türk ekstresi değil mi?")
    main_card = _mask(card_no.group(1))

    def money(label: str) -> float | None:
        m = re.search(rf"{label}[^\n]*?({_AMT}) TL", text)
        return amount(m.group(1)) if m else None

    def day(label: str) -> date | None:
        m = re.search(rf"{label}[^\n]*?(\d{{2}}/\d{{2}}/\d{{4}})", text)
        return _d(m.group(1)) if m else None

    # 'Devreden Bakiye Dönem Harcamaları Ödemeleriniz Dönem Borcunuz' altındaki denklem
    eq = re.search(rf"({_AMT}) \+ ({_AMT}) - ({_AMT}) = ({_AMT})", text)
    prev, activity, paid, debt = (amount(x) for x in eq.groups()) if eq else (0.0, 0.0, 0.0, money("Dönem Borcunuz") or 0.0)

    stmt = Statement(
        bank=BANK,
        card_no=main_card,
        period_end=day("Hesap Kesim Tarihi /Statement Issue Date"),
        due_date=day("Son Ödeme Tarihi /Due Date"),
        total_debt=debt,
        min_payment=money("Asgari Ödeme Tutarı"),
        credit_limit=money("Kart Limiti"),
        previous_balance=prev,
        period_activity=activity,
        interest_fees=0.0,  # Kuveyt Türk vade farkını dönem harcamalarına dahil ediyor
        payments=paid,
        next_period_end=day("Sonraki Hesap Kesim Tarihi"),
        next_due_date=day("Sonraki Son Ödeme Tarihi"),
    )
    if not stmt.period_end:
        raise ValueError("Hesap kesim tarihi okunamadı")

    owner = re.search(r"^Sayın\s+(.+)$", text, re.M)
    holder = owner.group(1).strip() if owner else ""
    card = main_card
    for line in (l.strip() for l in text.splitlines()):
        if m := CARD_HEADER_RE.match(line):
            card = _mask(m.group("card"))
            continue
        m = TXN_RE.match(line)
        if not m:
            continue
        desc = m.group("desc").strip()
        total = amount(m.group("amount"))
        norm = normalize(desc)

        inst_no = inst_left = inst_amt = None
        note = None
        billed = total
        if m.group("inst_no"):
            inst_no, n_total = int(m.group("inst_no")), int(m.group("inst_total"))
            inst_amt = amount(m.group("inst_amt"))
            billed, inst_left = inst_amt, n_total - inst_no
            note = f"Toplam {m.group('amount')} TL, {n_total} taksit"

        if "BORC ODEME" in norm or norm.startswith("ODEME"):
            txn_type = PAYMENT
        elif FEE_PREFIX_RE.match(desc):
            txn_type = FEE
        elif billed < 0:
            txn_type = REFUND
        else:
            txn_type = PURCHASE

        stmt.transactions.append(Transaction(
            txn_date=_d(m.group("date")),
            description=_clean_desc(desc),
            amount=billed,
            txn_type=txn_type,
            card=card,
            card_holder=holder,
            installment_no=inst_no,
            installments_left=inst_left,
            installment_left_amount=inst_amt if inst_left else None,
            note=note,
        ))
    return stmt
