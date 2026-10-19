"""VakıfBank kredi kartı hesap özeti (PDF) parser'ı."""
import re
from datetime import date, datetime

import pdfplumber

from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND, Statement, Transaction
from app.textnorm import normalize, parse_amount

BANK = "VakıfBank"

_AMT = r"\+?\d{1,3}(?:,\d{3})*\.\d{2}"
_DATE = r"\d{2}\.\d{2}\.\d{4}"

# 20.09.2026 TKPAY/ORNEK TEKNOLOJI 1. Taksit 6,240.00 5x6,240.00 125
TXN_RE = re.compile(
    rf"^(?P<date>{_DATE})\s+(?P<desc>.+?)\s*"
    rf"(?:(?P<inst_no>\d+)\.\s*(?P<kind>Taksit|İade)(?:\s+\d+)?\s+)?"
    rf"(?<![\d.,])(?P<amount>{_AMT})"
    rf"(?:\s+(?P<rest>.*))?$"
)
CARD_HEADER_RE = re.compile(r"^(?P<card>\d{4}\*{3}\d{4})\s+(?P<holder>(?!Toplam).+)$")
CARD_TOTAL_RE = re.compile(r"^\d{4}\*{3}\d{4}\s+Toplam Harcama Tutarı")
FX_RE = re.compile(r"^(?P<amt>\d+(?:\.\d+)?)\s+(?P<cur>USD|EUR|GBP|CHF)\s+(?P<desc>.+)$")
REMAINING_RE = re.compile(rf"^(?P<n>\d+)x(?P<amt>{_AMT})\s*(?P<tail>.*)$")
# Ek kart işlemlerinde açıklamaya eklenen kart parçası: '5400*3456'
CARD_SUFFIX_RE = re.compile(r"\s+\d{4}\*\d{3,4}$")

SKIP_PREFIXES = ("ÖNCEKİ DÖNEM HESAP ÖZETİ",)
FEE_KEYWORDS = ("FAIZI", "BSMV", "KKDF", "UCRETI", "UCRET")


def _d(s: str) -> date:
    return datetime.strptime(s, "%d.%m.%Y").date()


def _field(text: str, label: str) -> str | None:
    m = re.search(rf"{label}\s*:\s*(\S+)", text)
    return m.group(1) if m else None


def _summary_amount(text: str, label: str) -> float:
    m = re.search(rf"^{label}\s+({_AMT})", text, re.M)
    return parse_amount(m.group(1)) if m else 0.0


def is_vakifbank(text: str) -> bool:
    return "vakifkart" in text.lower() or "VB Müşteri No" in text


def parse_text(text: str) -> Statement:
    card_no = _field(text, "Kart No")
    if not card_no:
        raise ValueError("Kart No bulunamadı; VakıfBank ekstresi değil mi?")
    main_card = re.sub(r"(\d{4})\*+(\d{4})", r"\1***\2", card_no)

    stmt = Statement(
        bank=BANK,
        card_no=main_card,
        period_end=_d(_field(text, "Hesap Kesim Tarihi")),
        due_date=_d(d) if (d := _field(text, "Son Ödeme Tarihi")) else None,
        total_debt=parse_amount(_field(text, "Dönem Borcunuz")),
        min_payment=parse_amount(a) if (a := _field(text, "Asgari Ödeme Tutarı")) else None,
        credit_limit=parse_amount(l) if (l := _field(text, "Limitiniz")) else None,
        previous_balance=_summary_amount(text, "Önceki Hesap Bakiyesi"),
        period_activity=_summary_amount(text, r"Dönem I[çc]i I[şs]lemler"),
        interest_fees=_summary_amount(text, "Toplam Faiz ve Ücretler"),
        payments=_summary_amount(text, "Ödemeler"),
        next_period_end=_d(d) if (d := _field(text, "Bir Sonraki Hesap Kesim Tarihi")) else None,
        next_due_date=_d(d) if (d := _field(text, "Bir Sonraki Son Ödeme Tarihi")) else None,
    )

    in_table = False
    owner = re.search(r"Sayın\s+(.+)$", text, re.M)
    card, holder = main_card, owner.group(1).strip() if owner else ""
    for line in (l.strip() for l in text.splitlines()):
        if line.startswith("İŞLEM TARİHİ"):
            in_table = True
            continue
        if line == "HESAP ÖZETİ":
            break
        if not in_table:
            continue
        if CARD_TOTAL_RE.match(line):
            continue
        if m := CARD_HEADER_RE.match(line):
            card, holder = m.group("card"), m.group("holder").strip()
            continue
        if txn := _parse_txn_line(line, card, holder):
            stmt.transactions.append(txn)
    return stmt


def _parse_txn_line(line: str, card: str, holder: str) -> Transaction | None:
    m = TXN_RE.match(line)
    if not m:
        return None
    desc = m.group("desc").strip()
    if desc.startswith(SKIP_PREFIXES):
        return None

    raw_amount = m.group("amount")
    amount = parse_amount(raw_amount)
    is_credit = raw_amount.startswith("+")
    norm = normalize(desc)

    if "ODEMENIZ ICIN TESEKKURLER" in norm or norm.startswith("ODEME"):
        txn_type = PAYMENT
    elif is_credit or m.group("kind") == "İade" or norm.startswith("IADE"):
        txn_type = REFUND
    elif any(k in norm for k in FEE_KEYWORDS):
        txn_type = FEE
    else:
        txn_type = PURCHASE
    if is_credit:
        amount = -amount

    orig_amount = orig_cur = None
    if fx := FX_RE.match(desc):
        orig_amount, orig_cur, desc = float(fx.group("amt")), fx.group("cur"), fx.group("desc")
    desc = CARD_SUFFIX_RE.sub("", desc).strip()

    inst_no = int(m.group("inst_no")) if m.group("inst_no") else None
    left = left_amt = points = None
    rest = (m.group("rest") or "").strip()
    if rest.startswith("Son Taksit"):
        left, rest = 0, rest[len("Son Taksit"):].strip()
    elif r := REMAINING_RE.match(rest):
        left, left_amt, rest = int(r.group("n")), parse_amount(r.group("amt")), r.group("tail")
    if p := re.match(r"^(\d+)", rest.strip()):
        points = int(p.group(1))

    return Transaction(
        txn_date=_d(m.group("date")),
        description=desc,
        amount=amount,
        txn_type=txn_type,
        card=card,
        card_holder=holder,
        orig_amount=orig_amount,
        orig_currency=orig_cur,
        installment_no=inst_no,
        installments_left=left,
        installment_left_amount=left_amt,
        points=points,
    )


def parse_pdf(fp) -> Statement:
    with pdfplumber.open(fp) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return parse_text(text)
