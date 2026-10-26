"""Genel kredi kartı ekstresi okuyucu: yapay zekâsız, şablonla.

Türk bankalarının ekstreleri ortak bir dil kullanır ("Hesap Kesim Tarihi", "Son Ödeme Tarihi", "Dönem Borcu",
"Asgari Ödeme"); işlemler tarihle başlayan satırlardır. Bankadan bankaya değişen birkaç şey bir şablonda tutulur:

    {"v": 1, "kind": "card", "bank": "Garanti", "keywords": ["hesap kesim tarihi", "son ödeme tarihi"],
     "date": {"sep": ".", "order": "dmy"},   # işlem satırı bu biçimde tarihle başlar
     "num": "tr",                            # sayı biçimi: tr 1.234,56 | us 1,234.56
     "amount_idx": -1,                       # satırdaki para değerlerinden hangisi tutar
     "credit": "minus"}                      # ödeme/iade nasıl işaretli: minus "-1.000,00" | plus "+1,000.00"

Sonuç her zaman EKSTRE DENKLEMİYLE doğrulanır: önceki borç − ödemeler + harcamalar = dönem borcu (kuruşu kuruşuna).
Tutmazsa şablon kullanılmaz. Öğrenme: olası kombinasyonlar denenir, denklemi tutan seçilir. Şablon belgeden metin
taşımaz (isim, kart no, tutar yok); topluluğa paylaşılabilir.
"""
import re
from datetime import date

from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND, Statement, Transaction

KEYWORDS = ("hesap kesim tarihi", "son ödeme tarihi", "dönem borcu", "asgari ödeme", "kart limiti", "hesap özeti",
            "ekstre", "kredi kartı")
_DATES = {("dmy", "."): r"(\d{2})\.(\d{2})\.(\d{4})", ("dmy", "/"): r"(\d{2})/(\d{2})/(\d{4})",
          ("dmy", "-"): r"(\d{2})-(\d{2})-(\d{4})"}
_ANY_DATE = re.compile(r"(\d{2})[./-](\d{2})[./-](\d{4})")
# para: isteğe bağlı işaret + değer; önünde rakam/ayraç/"x" (5x1.315,40 taksit gösterimi) olmasın
_MONEY = {"tr": re.compile(r"(?<![\d.,x×])([+-]?)\s?(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d,%]|\.\d)"),
          "us": re.compile(r"(?<![\d.,x×])([+-]?)\s?(\d{1,3}(?:,\d{3})*\.\d{2})(?![\d.%]|,\d)")}
_CARD = re.compile(r"(\d{4})[\s-]?(?:\d{2})?[*xX•][*xX•\s-]{3,}(\d{4})")
_EQUATION = {"tr": r"(\d[\d.]*,\d{2})\s*\+\s*(\d[\d.]*,\d{2})\s*-\s*(\d[\d.]*,\d{2})\s*=\s*(\d[\d.]*,\d{2})",
             "us": r"(\d[\d,]*\.\d{2})\s*\+\s*(\d[\d,]*\.\d{2})\s*-\s*(\d[\d,]*\.\d{2})\s*=\s*(\d[\d,]*\.\d{2})"}

# etiketler Türkçe karaktersiz yazılır; metin de _fold ile aynı biçime çevrilir ("ÖDEME" = "ODEME")
LABELS = {
    "period_end": r"hesap kesim tarihi|ekstre kesim tarihi|hesap ozeti tarihi|ekstre tarihi|kesim tarihi",
    "due": r"son odeme tarihi",
    "total": r"donem borcu|toplam borc|ekstre borcu|hesap ozeti borcu|guncel donem borcu|odenecek toplam",
    "min": r"asgari odeme",
    "limit": r"kart limiti|kredi limiti|toplam limit",
    "prev": r"onceki donem (?:borcu|bakiye)|onceki ekstre borcu|gecen donem(?:den)? (?:devreden )?borc|devreden borc",
    "next_cut": r"sonraki hesap kesim|sonraki kesim",
    "next_due": r"sonraki son odeme",
}
_PREV_LINE = re.compile(r"onceki donem|onceki ekstre|devreden bakiye|devreden borc")
_PAY_WORDS = re.compile(r"odeme|tesekkur|payment|tahsilat")
_FEE_WORDS = re.compile(r"faiz|bsmv|kkdf|ucret|aidat|vade fark|gecikme")
_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def _low(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


def _fold(s: str) -> str:
    """Küçük harf + Türkçe karaktersiz; uzunluk değişmez (eşleşme konumları orijinal satıra uyar)."""
    return _low(s).translate(_FOLD)


def _num(v: str, fmt: str) -> float:
    v = v.replace(" ", "")
    return float(v.replace(".", "").replace(",", ".") if fmt == "tr" else v.replace(",", ""))


def _date(m) -> date | None:
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _field(lines: list[str], key: str, kind: str, fmt: str):
    """Etiketin aynı satırındaki (yoksa sonraki satırdaki) ilk tarih/para. "Sonraki …" etiketleri karışmasın."""
    pat = re.compile(LABELS[key])
    for i, ln in enumerate(lines):
        low = _fold(ln)
        m = pat.search(low)
        if not m or (key not in ("next_cut", "next_due") and re.search(r"sonraki|next", low[:m.start() + 1])):
            continue
        for cand in (ln[m.end():], lines[i + 1] if i + 1 < len(lines) else ""):
            if kind == "date":
                if d := _ANY_DATE.search(cand):
                    return _date(d)
            elif v := _MONEY[fmt].search(cand):
                return _num(v.group(2), fmt)
    return None


def header(text: str, fmt: str) -> dict:
    lines = text.splitlines()
    out = {k: _field(lines, k, "date" if k in ("period_end", "due", "next_cut", "next_due") else "money", fmt)
           for k in LABELS}
    if eq := re.search(_EQUATION[fmt], text):
        out["equation"] = [_num(x, fmt) for x in eq.groups()]  # önceki + dönem içi − ödemeler = dönem borcu
    card = _CARD.search(text)
    out["card_no"] = f"{card.group(1)}***{card.group(2)}" if card else None
    return out


def _installment(desc: str) -> tuple[int | None, int | None]:
    """(kaçıncı taksit, kalan taksit). "İşlemin 3 / 9 Taksidi" → (3, 6); "1. Taksit … 5x6.240,00" (kalan 5) → (1, 5);
    "4. Taksit … Son Taksit" → (4, 0); taksitli iade "2. İade … 4x…" → (2, 4). Tarih parçaları (29/03/2025) sayılmaz."""
    low = _fold(desc)
    if m := re.search(r"(?<![\d/.])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/.])", low):
        n, total = int(m.group(1)), int(m.group(2))
        return (n, total - n) if 0 < n <= total <= 48 else (None, None)
    m = re.search(r"(?<![\d.])(\d{1,2})\.\s*(?:taksit|iade)", low)
    if "son taksit" in low:
        return (int(m.group(1)) if m else None), 0
    if m:
        left = re.search(r"(?<![\d.,])(\d{1,2})\s*[x×]", low)
        return int(m.group(1)), int(left.group(1)) if left else None
    return None, None


def apply(text: str, spec: dict, bank: str = "") -> tuple[Statement, bool] | None:
    """Şablonla oku → (Statement, denklem tuttu mu) ya da None."""
    fmt = spec["num"]
    h = header(text, fmt)
    if not h["period_end"] or h["total"] is None:
        return None
    dre = re.compile(r"^\s*" + _DATES[(spec["date"]["order"], spec["date"]["sep"])] + r"\b")
    card = h["card_no"] or "****"
    prev = h["prev"]
    txns = []
    for ln in text.splitlines():
        m = dre.match(ln)
        if not m or not (d := _date(m)):
            continue
        rest = ln[m.end():]
        vals = list(_MONEY[fmt].finditer(rest))
        if not vals:
            continue
        low = _fold(rest)
        if _PREV_LINE.search(low):  # "önceki dönem hesap özeti bakiyeniz": işlem değil, devreden borç
            prev = _num(vals[-1].group(2), fmt) if prev is None else prev
            continue
        try:
            v = vals[spec["amount_idx"]]
        except IndexError:
            continue
        amount = _num(v.group(2), fmt)
        credit = (spec["credit"] == "minus" and v.group(1) == "-") or (spec["credit"] == "plus" and v.group(1) == "+")
        desc = re.sub(r"\s+", " ", _MONEY[fmt].sub(" ", rest)).strip()[:120] or "?"
        if credit:
            kind = PAYMENT if _PAY_WORDS.search(low) else REFUND
            amount = -amount
        else:
            kind = FEE if _FEE_WORDS.search(low) else PURCHASE
        n, left = _installment(desc) if kind != PAYMENT else (None, None)
        txns.append(Transaction(txn_date=d, description=desc, amount=amount, txn_type=kind, card=card,
                                installment_no=n, installments_left=left,
                                installment_left_amount=amount if left else None))
    if len(txns) < 2:
        return None
    eq = h.get("equation")
    if prev is None and eq:
        prev = eq[0]
    charges = round(sum(t.amount for t in txns if t.txn_type != PAYMENT), 2)
    fees = round(sum(t.amount for t in txns if t.txn_type == FEE), 2)
    paid = round(-sum(t.amount for t in txns if t.txn_type == PAYMENT), 2)
    if prev is not None:
        ok = abs(prev - paid + charges - h["total"]) <= 0.05
    elif eq:
        ok = abs(charges - eq[1]) <= 0.05 and abs(paid - eq[2]) <= 0.05
    else:
        ok = False
    st = Statement(bank=bank or spec.get("bank") or "Kredi kartı", card_no=card, period_end=h["period_end"],
                   due_date=h["due"], total_debt=h["total"], min_payment=h["min"], credit_limit=h["limit"],
                   previous_balance=prev or 0.0, period_activity=round(charges - fees, 2), interest_fees=fees,
                   payments=paid, next_period_end=h["next_cut"], next_due_date=h["next_due"], transactions=txns)
    return st, ok


def _keywords(text: str) -> list[str]:
    head = _low(text[:4000])
    return [k for k in KEYWORDS if k in head][:4]


def learn(text: str, bank: str) -> tuple[dict, Statement] | None:
    """Denklemi tutan kuralı ara. (spec, Statement) ya da None."""
    best = None
    for (order, sep), pat in _DATES.items():
        if len(re.findall(r"(?m)^\s*" + pat, text)) < 3:
            continue
        for fmt in ("tr", "us"):
            for idx in (-1, 0, -2, 1):
                for credit in ("minus", "plus"):
                    spec = {"v": 1, "kind": "card", "bank": bank, "keywords": _keywords(text),
                            "date": {"sep": sep, "order": order}, "num": fmt, "amount_idx": idx, "credit": credit}
                    res = apply(text, spec, bank)
                    if res and res[1] and (best is None or len(res[0].transactions) > len(best[1].transactions)):
                        best = (spec, res[0])
    return best


# banka adı ya da kart markası → banka (ekstrede ilk geçen kazanır; kart ekstresinde IBAN çoğu zaman yok)
BANK_HINTS = [
    (r"vakıf katılım|vakif katilim", "Vakıf Katılım"), (r"ziraat katılım|ziraat katilim", "Ziraat Katılım"),
    (r"emlak katılım|emlak katilim", "Emlak Katılım"), (r"vakıfbank|vakifbank|vak[ıi]fkart|vakıflar bankası", "VakıfBank"),
    (r"kuveyt ?türk|kuveyt ?turk", "Kuveyt Türk"), (r"garanti|\bbonus\b", "Garanti"), (r"akbank|\baxess\b|\bwings\b", "Akbank"),
    (r"yapı ?kredi|yapi ?kredi|world ?card|worldcard", "Yapı Kredi"), (r"iş ?bankası|is ?bankasi|işbank|isbank|\bmaximum\b", "İş Bankası"),
    (r"ziraat|bankkart", "Ziraat"), (r"halkbank|halk bankası|\bparaf\b", "Halkbank"), (r"\bqnb\b|cardfinans", "QNB"),
    (r"denizbank", "DenizBank"), (r"türk ekonomi bankası|\bteb\b", "TEB"), (r"ing bank", "ING"),
    (r"türkiye finans|turkiye finans", "Türkiye Finans"), (r"albaraka", "Albaraka"), (r"şekerbank|sekerbank", "Şekerbank"),
    (r"odeabank", "Odeabank"), (r"fibabanka", "Fibabanka"), (r"\bhsbc\b", "HSBC"), (r"anadolubank", "Anadolubank"),
]


def bank_of(text: str) -> str:
    from app.templates import bank_of as account_bank

    if b := account_bank(text[:6000]) if re.search(r"TR\s?\d{2}\s?\d{4}", text[:6000]) else "":
        return b
    low = _low(text)
    hits = [(m.start(), bank) for pat, bank in BANK_HINTS if (m := re.search(pat, low))]
    return min(hits)[1] if hits else ""


def matches(text: str, spec: dict) -> bool:
    if spec.get("bank") and bank_of(text) != spec["bank"]:
        return False
    return all(k in _low(text[:4000]) for k in spec.get("keywords", []))


def is_safe(spec: dict) -> bool:
    from app.templates import IBAN_BANKS

    try:
        if set(spec) - {"v", "kind", "bank", "keywords", "date", "num", "amount_idx", "credit"}:
            return False
        if spec.get("kind") != "card" or spec.get("bank") not in set(IBAN_BANKS.values()) | {""}:
            return False
        if not all(k in KEYWORDS for k in spec.get("keywords", [])):
            return False
        if (spec["date"]["order"], spec["date"]["sep"]) not in _DATES or set(spec["date"]) != {"order", "sep"}:
            return False
        return spec["num"] in ("tr", "us") and spec["credit"] in ("minus", "plus") \
            and isinstance(spec["amount_idx"], int) and -3 <= spec["amount_idx"] <= 3
    except (KeyError, TypeError, AttributeError):
        return False


def read(text: str) -> Statement | None:
    """Kayıtlı kart şablonlarından biri denklemi tutturuyorsa onu; yoksa yeni kural öğrenmeyi dene."""
    from app import templates

    templates.sync_community()
    bank = bank_of(text)
    for t in templates.all_templates():
        spec = t["spec"]
        if spec.get("kind") != "card" or not matches(text, spec):
            continue
        res = apply(text, spec, bank)
        if res and res[1]:
            templates._used(t["id"])
            return res[0]
    learned = learn(text, bank)
    if learned:
        spec, st = learned
        if spec["bank"] and templates.save(spec):  # banka adı bilinmiyorsa kaydetme (yanlış eşleşmesin)
            templates.share_async(spec)
        return st
    return None
