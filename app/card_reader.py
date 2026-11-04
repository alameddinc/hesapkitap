"""Genel kredi kartı ekstresi okuyucu: yapay zekâsız, şablonla.

Türk bankalarının ekstreleri ortak bir dil kullanır ("Hesap Kesim Tarihi", "Son Ödeme Tarihi", "Dönem Borcu",
"Asgari Ödeme"); işlemler tarihle başlayan satırlardır. Bankadan bankaya değişen birkaç şey bir şablonda tutulur:

    {"v": 1, "kind": "card", "bank": "Garanti", "keywords": ["hesap kesim tarihi", "son ödeme tarihi"],
     "date": {"sep": ".", "order": "dmy"},   # işlem satırı bu biçimde tarihle başlar ("year": 2 → 14.10.26)
     "num": "tr",                            # sayı biçimi: tr 1.234,56 | us 1,234.56
     "amount_idx": -1,                       # satırdaki para değerlerinden hangisi tutar
     "credit": "minus"}                      # ödeme/iade nasıl işaretli: minus "-1.000,00" | plus "+1,000.00"
                                             #   | trail "1.000,00-" | words (işaretsiz; "ödeme"/"iade" sözcüğü)
    İsteğe bağlı (yapay zekâ okumasından öğrenilir, learn_from_ai):
     "amount_at": {"3": 1},                  # satırda N değer varsa tutar kaçıncısı (taksit toplamı, bonus sütunu)
     "fx": true,                             # döviz kodunun yanındaki değer (USD 20,00) tutar değil, orig_amount
     "labels": {"due": [{"label": "son odeme tarihi", "off": 1, "nth": 1}]}  # başlık alanının etiketi (sözlükten)
        off=0: değer etiketin sağında, aynı satırda; off=1..2: etiket satırının o kadar altındaki satırın nth'inci
        değeri (sütunlu düzen). Etiket yalnız sabit LABEL_WORDS kelimelerinden oluşur.

Sonuç her zaman EKSTRE DENKLEMİYLE doğrulanır: önceki borç − ödemeler + harcamalar = dönem borcu (kuruşu kuruşuna);
ayrıca tarihler dönem içinde, son ödeme kesimden sonra (plausible). Tutmazsa şablon kullanılmaz, hata sayısı artar; art
arda iki hata → devre dışı (app/templates.py). Öğrenme iki yoldan: (1) olası kombinasyonlar denenir, denklemi tutan
seçilir (learn); (2) yapay zekâ okuması doğrulanırsa değerleri metinde aranıp etiket ve satır deseni çıkarılır, şablon
aynı metne uygulanınca yapay zekânın sonucunu aynen veriyorsa kaydedilir (learn_from_ai). Şablon belgeden metin
taşımaz (isim, kart no, tutar yok); is_safe bunu denetler ve topluluğa paylaşılabilir.
"""
import logging
import re
from collections import Counter
from datetime import date, timedelta

from app import money
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND, Statement, Transaction, equation

log = logging.getLogger("hesapkitap.card_reader")

KEYWORDS = ("hesap kesim tarihi", "son ödeme tarihi", "dönem borcu", "asgari ödeme", "kart limiti", "hesap özeti",
            "ekstre", "kredi kartı")
_DATES = {("dmy", "."): r"(\d{2})\.(\d{2})\.(\d{4})", ("dmy", "/"): r"(\d{2})/(\d{2})/(\d{4})",
          ("dmy", "-"): r"(\d{2})-(\d{2})-(\d{4})"}
_ANY_DATE = re.compile(r"(?<!\d)(\d{2})[./-](\d{2})[./-](\d{4}|\d{2})(?!\d)")
# para: isteğe bağlı işaret + değer; önünde rakam/ayraç/"x" (5x1.315,40 taksit gösterimi) olmasın
_MONEY = {"tr": re.compile(r"(?<![\d.,x×])([+-]?)\s?(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d,%]|\.\d)"),
          "us": re.compile(r"(?<![\d.,x×])([+-]?)\s?(\d{1,3}(?:,\d{3})*\.\d{2})(?![\d.%]|,\d)")}
_CUR = re.compile(r"(?<![A-Za-z])(USD|EUR|GBP|CHF|\$|€|£)(?![A-Za-z])")
_CUR_CODE = {"$": "USD", "€": "EUR", "£": "GBP"}
_CARD = re.compile(r"(\d{4})[\s-]?(?:\d{2})?[*xX•][*xX•\s-]{3,}(\d{4})")
_EQUATION = {"tr": r"(\d[\d.]*,\d{2})\s*\+\s*(\d[\d.]*,\d{2})\s*-\s*(\d[\d.]*,\d{2})\s*=\s*(\d[\d.]*,\d{2})",
             "us": r"(\d[\d,]*\.\d{2})\s*\+\s*(\d[\d,]*\.\d{2})\s*-\s*(\d[\d,]*\.\d{2})\s*=\s*(\d[\d,]*\.\d{2})"}

# etiketler Türkçe karaktersiz yazılır; metin de _fold ile aynı biçime çevrilir ("ÖDEME" = "ODEME")
LABELS = {
    "period_end": r"hesap kesim tarihi|ekstre kesim tarihi|hesap ozeti tarihi|ekstre tarihi|kesim tarihi",
    "due": r"son odeme tarihi",
    "total": r"donem borcu|toplam borc|ekstre borcu|hesap ozeti borcu|guncel donem borcu|odenecek toplam",
    "min": r"asgari odeme",
    # "Kullanılabilir kart limiti" toplam limit sayılmasın (kendi etiketi: available)
    "limit": r"(?<!kullanilabilir )(?<!kalan )(?:kart limiti|kredi limiti|toplam limit)",
    "available": r"kullanilabilir (?:kart |kredi )?limit|kalan (?:kart |kredi )?limit",
    "prev": r"onceki donem (?:borcu|bakiye)|onceki ekstre borcu|gecen donem(?:den)? (?:devreden )?borc|devreden borc",
    "next_cut": r"sonraki hesap kesim|sonraki kesim",
    "next_due": r"sonraki son odeme",
}
_DATE_KEYS = ("period_end", "due", "next_cut", "next_due")
_PREV_LINE = re.compile(r"onceki donem|onceki ekstre|devreden bakiye|devreden borc")
_PAY_WORDS = re.compile(r"odeme|tesekkur|payment|tahsilat")
_REFUND_WORDS = re.compile(r"iade|iptal|refund")
_FEE_WORDS = re.compile(r"faiz|bsmv|kkdf|ucret|aidat|vade fark|gecikme")
_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")

# öğrenilen etiketlerde izin verilen kelimeler (katlanmış). Şablona bunların dışında belgeden kelime girmez.
LABEL_WORDS = frozenset("""
hesap hesabi hesabinizin kesim tarihi tarih tarihiniz tarihli ekstre ekstrenin ekstreniz ekstresi ozeti ozet
ozetinin ozetiniz bildirim bildirimi duzenleme duzenlenme son odeme odemesi odenecek odenmesi gereken donem donemi
donemin donemine borcu borcunuz borc borcun toplam toplami genel guncel asgari minimum en az tutari tutar tutariniz
kart karti kartinizin kartiniz kredi limiti limit limitiniz kullanilabilir kalan nakit onceki gecen devreden bakiye
bakiyesi bakiyeniz yeni bu ay ayin icin olan ve ile itibariyla itibari tl try vade vadesi gunu
""".split())
# öğrenilebilen başlık alanı → (her kümeden en az bir kelime, olmaması gereken kelimeler); yapay zekâ alanı
_NEED = {
    "period_end": ([{"kesim", "ekstre", "ekstrenin", "ekstreniz", "ekstresi", "ozeti", "ozet", "ozetinin", "ozetiniz",
                     "bildirim", "bildirimi", "duzenleme", "duzenlenme"}], {"son", "odeme", "vade", "vadesi"}),
    "due": ([{"son", "vade", "vadesi"}, {"odeme", "odemesi", "tarihi", "tarih", "gunu", "vade", "vadesi"}],
            {"asgari", "minimum", "tutar", "tutari", "kesim"}),
    "total": ([{"borcu", "borcunuz", "borc", "borcun", "toplam", "toplami", "odenecek", "bakiye", "bakiyesi",
                "bakiyeniz"}], {"onceki", "gecen", "devreden", "asgari", "minimum", "limit", "limiti", "limitiniz",
                                "nakit"}),
    "min": ([{"asgari", "minimum"}], set()),
    "limit": ([{"limit", "limiti", "limitiniz"}], {"kullanilabilir", "kalan", "nakit"}),
    "available": ([{"kullanilabilir", "kalan"}, {"limit", "limiti", "limitiniz"}], {"nakit"}),
    "prev": ([{"onceki", "gecen", "devreden"}], set()),
}
_AI_FIELD = {"period_end": "period_end", "due": "due_date", "total": "total_debt", "min": "min_payment",
             "limit": "credit_limit", "available": "available_limit", "prev": "previous_balance"}
_PERIOD_DAYS = 62  # taksitsiz işlem kesimden en çok bu kadar önce olabilir (geç yansıyan / dövizli işlemler için pay)


def _low(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


def _fold(s: str) -> str:
    """Küçük harf + Türkçe karaktersiz; uzunluk değişmez (eşleşme konumları orijinal satıra uyar)."""
    return _low(s).translate(_FOLD)


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z]+", s)


def _num(v: str, fmt: str) -> float:
    v = v.replace(" ", "")
    return float(v.replace(".", "").replace(",", ".") if fmt == "tr" else v.replace(",", ""))


def _date(m) -> date | None:
    try:
        y = int(m.group(3))
        return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _date_re(d: dict) -> re.Pattern:
    """İşlem satırının başındaki tarih ("year": 2 → iki haneli yıl, 14.10.26)."""
    pat = _DATES[(d["order"], d["sep"])]
    if d.get("year") == 2:
        pat = pat.replace(r"(\d{4})", r"(\d{2})(?!\d)")
    return re.compile(r"^\s*" + pat + r"\b")


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


# ---------------------------------------------------------------- öğrenilmiş etiketler (çapa)

def _label_re(label: str) -> re.Pattern:
    return re.compile(r"(?<![a-z])" + r"[^a-z0-9]{1,3}".join(map(re.escape, label.split())) + r"(?![a-z])")


def _val(kind: str, m: re.Match, fmt: str):
    return _date(m) if kind == "date" else _num(m.group(2), fmt)


def _anchor_value(lines: list[str], a: dict, kind: str, fmt: str):
    """Çapanın belgedeki ilk geçişinin verdiği değer (yoksa None)."""
    pat = _ANY_DATE if kind == "date" else _MONEY[fmt]
    if a["off"] == 0:
        lre = _label_re(a["label"])
        for ln in lines:
            for lm in lre.finditer(_fold(ln)):
                rest = ln[lm.end():]
                m = pat.search(rest)
                # değer etiketten sonraki İLK sayı olmalı (arada başka sayı varsa başka alanın değeri)
                if m and not re.search(r"\d", rest[:m.start()]):
                    return _val(kind, m, fmt)
        return None
    want = a["label"].split()
    for i, ln in enumerate(lines):
        low = _fold(ln)
        if _words(low) != want or re.search(r"\d", low) or i + a["off"] >= len(lines):
            continue
        ms = list(pat.finditer(lines[i + a["off"]]))
        if len(ms) > a["nth"]:
            return _val(kind, ms[a["nth"]], fmt)
    return None


def header(text: str, fmt: str, labels: dict | None = None) -> dict:
    """Başlık alanları: önce şablonun öğrenilmiş etiketleri, bulunamazsa ortak etiket sözlüğü (LABELS)."""
    lines = text.splitlines()
    out = {}
    for k in LABELS:
        kind = "date" if k in _DATE_KEYS else "money"
        v = None
        for a in (labels or {}).get(k, []):
            if (v := _anchor_value(lines, a, kind, fmt)) is not None:
                break
        out[k] = v if v is not None else _field(lines, k, kind, fmt)
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


def _tokens(rest: str, fmt: str, fx: bool) -> list[tuple[re.Match, str | None]]:
    """Satırdaki para değerleri ve (fx açıksa) döviz kodları: kod hemen önündeki değere ("20,00 USD"), yoksa hemen
    arkasındakine ("USD 20,00") bağlanır."""
    ms = list(_MONEY[fmt].finditer(rest))
    codes: dict[int, str] = {}
    if fx:
        for c in _CUR.finditer(rest):
            before = [i for i, m in enumerate(ms) if m.end() <= c.start() and not rest[m.end():c.start()].strip()]
            after = [i for i, m in enumerate(ms) if m.start() >= c.end() and not rest[c.end():m.start()].strip()]
            i = next(iter(before or after), None)
            if i is not None and i not in codes:
                codes[i] = _CUR_CODE.get(c.group(1), c.group(1))
    return [(m, codes.get(i)) for i, m in enumerate(ms)]


def _is_credit(mode: str, v: re.Match, rest: str, low: str) -> bool:
    if mode == "minus":
        return v.group(1) == "-"
    if mode == "plus":
        return v.group(1) == "+"
    if mode == "trail":
        return bool(re.match(r"\s?-(?!\s?\d)", rest[v.end():]))
    return bool(_PAY_WORDS.search(low) or _REFUND_WORDS.search(low))  # words


def apply(text: str, spec: dict, bank: str = "") -> tuple[Statement, bool] | None:
    """Şablonla oku → (Statement, denklem tuttu mu) ya da None."""
    fmt = spec["num"]
    h = header(text, fmt, spec.get("labels"))
    if not h["period_end"] or h["total"] is None:
        return None
    dre = _date_re(spec["date"])
    fx = bool(spec.get("fx"))
    at = spec.get("amount_at") or {}
    card = h["card_no"] or "****"
    prev = h["prev"]
    txns = []
    for ln in text.splitlines():
        m = dre.match(ln)
        if not m or not (d := _date(m)):
            continue
        rest = ln[m.end():]
        toks = _tokens(rest, fmt, fx)
        vals = [t for t, code in toks if not code]
        if not vals:
            continue
        low = _fold(rest)
        if _PREV_LINE.search(low):  # "önceki dönem hesap özeti bakiyeniz": işlem değil, devreden borç
            prev = _num(vals[-1].group(2), fmt) if prev is None else prev
            continue
        try:
            v = vals[at.get(str(len(vals)), spec["amount_idx"])]
        except IndexError:
            continue
        amount = _num(v.group(2), fmt)
        credit = _is_credit(spec["credit"], v, rest, low)
        desc = _MONEY[fmt].sub(" ", rest)
        if fx:
            desc = _CUR.sub(" ", desc)
        if spec["credit"] == "trail":
            desc = re.sub(r"(?<=\s)-(?=\s|$)", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()[:120] or "?"
        if credit:
            kind = PAYMENT if _PAY_WORDS.search(low) else REFUND
            amount = -amount
        else:
            kind = FEE if _FEE_WORDS.search(low) else PURCHASE
        orig = next(((t, code) for t, code in toks if code), None)
        n, left = _installment(desc) if kind != PAYMENT else (None, None)
        txns.append(Transaction(txn_date=d, description=desc, amount=amount, txn_type=kind, card=card,
                                orig_amount=_num(orig[0].group(2), fmt) if orig else None,
                                orig_currency=orig[1] if orig else None,
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
                   available_limit=h["available"],
                   previous_balance=prev or 0.0, period_activity=round(charges - fees, 2), interest_fees=fees,
                   payments=paid, next_period_end=h["next_cut"], next_due_date=h["next_due"], transactions=txns,
                   source="sablon")
    return st, ok


def plausible(st: Statement) -> bool:
    """Tarih tutarlılığı: son ödeme kesimden sonra (en çok 31 gün), işlemler dönem içinde (taksit satırı eski tarihli
    olabilir: yalnız kesimden sonra olmasın)."""
    if not st.due_date or not st.period_end or not (0 < (st.due_date - st.period_end).days <= 31):
        return False
    for t in st.transactions:
        if t.txn_date > st.period_end + timedelta(days=2):
            return False
        if t.installment_no is None and "taksit" not in _fold(t.description) \
                and t.txn_date < st.period_end - timedelta(days=_PERIOD_DAYS):
            return False
    return True


def trustworthy(st: Statement) -> bool:
    """Yapay zekâ okuması şablon öğretecek kadar güvenilir mi: ekstre denklemi ve işlemler toplamı kuruşu kuruşuna,
    ödemeler özetle aynı, tarihler tutarlı."""
    if len(st.transactions) < 2 or not st.total_debt:
        return False
    eq = equation(st.previous_balance, st.payments, st.period_activity, st.fees(), st.total_debt)
    paid = -sum(t.amount for t in st.transactions if t.txn_type == PAYMENT)
    return eq["ok"] is True and abs(eq["diff"]) <= 0.05 and abs(st.reconciliation_diff()) <= 0.05 \
        and abs(paid - (st.payments or 0)) <= 0.05 and plausible(st)


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
                    if res and res[1] and plausible(res[0]) \
                            and (best is None or len(res[0].transactions) > len(best[1].transactions)):
                        best = (spec, res[0])
    return best


# ---------------------------------------------------------------- yapay zekâ okumasından öğrenme

def _learn_anchor(lines: list[str], key: str, target, fmt: str) -> dict | None:
    """Değerin (yapay zekânın bulduğu) önündeki sözlük kelimeleri etiket olur; değer kendi satırında yalnızsa üstteki
    etiket satırı (sütunlu düzen). Çapa aynı metinde ilk geçişte bu değeri vermeli."""
    kind = "date" if key in _DATE_KEYS else "money"
    pat = _ANY_DATE if kind == "date" else _MONEY[fmt]
    need, avoid = _NEED[key]
    for i, ln in enumerate(lines):
        low = _fold(ln)
        ms = list(pat.finditer(ln))
        for j, m in enumerate(ms):
            if _val(kind, m, fmt) != target:
                continue
            prefix = low[:m.start()]
            digits = [d.end() for d in re.finditer(r"\d", prefix)]
            ws = _words(prefix[digits[-1]:] if digits else prefix)
            label: list[str] = []
            for w in reversed(ws):  # değere en yakın, sözlükteki kelimeler (önündeki ad/adres girmez)
                if w not in LABEL_WORDS or len(label) == 8:
                    break
                label.insert(0, w)
            cands = []
            if label and all(s & set(label) for s in need) and not avoid & set(label):
                cands.append({"label": " ".join(label), "off": 0, "nth": 0})
            elif not label and j <= 5:
                for off in (1, 2):
                    if i - off < 0:
                        break
                    up = _fold(lines[i - off])
                    uw = _words(up)
                    if uw and not re.search(r"\d", up) and len(uw) <= 12 and all(w in LABEL_WORDS for w in uw) \
                            and all(s & set(uw) for s in need):
                        cands.append({"label": " ".join(uw), "off": off, "nth": j})
                        break
            for a in cands:
                if _anchor_value(lines, a, kind, fmt) == target:
                    return a
    return None


def _ai_bank(text: str, ai_bank: str) -> str:
    from app.templates import IBAN_BANKS

    b = bank_of(text)
    if not b:
        low = _low(ai_bank or "")
        b = next((bank for pat, bank in BANK_HINTS if re.search(pat, low)), "")
    return b if b in set(IBAN_BANKS.values()) else ""


def _date_formats(text: str, ai_dates: set[date]) -> list[dict]:
    """Yapay zekânın işlem tarihlerinin satır başında hangi biçimde geçtiği (en az yarısı)."""
    out = []
    for order, sep in _DATES:
        for year in (4, 2):
            d = {"sep": sep, "order": order, **({"year": 2} if year == 2 else {})}
            dre = _date_re(d)
            found = {dt for ln in text.splitlines() if (m := dre.match(ln)) and (dt := _date(m)) in ai_dates}
            if len(found) >= max(1, len(ai_dates) // 2):
                out.append(d)
    return out


def _amount_at(text: str, d: dict, fmt: str, fx: bool, ai: Statement) -> tuple[int, dict]:
    """Satırdaki değer sayısına göre tutarın sırası: yapay zekânın işlemleri satırlarda aranır."""
    want: dict[date, list[float]] = {}
    for t in ai.transactions:
        want.setdefault(t.txn_date, []).append(round(abs(t.amount), 2))
    dre = _date_re(d)
    pos: dict[int, Counter] = {}
    for ln in text.splitlines():
        m = dre.match(ln)
        if not m or (dt := _date(m)) not in want:
            continue
        vals = [t for t, code in _tokens(ln[m.end():], fmt, fx) if not code]
        for i, v in enumerate(vals):
            x = round(_num(v.group(2), fmt), 2)
            if x in want[dt]:
                want[dt].remove(x)
                pos.setdefault(len(vals), Counter())[i] += 1
                break
    if not pos:
        return -1, {}
    best = {n: c.most_common(1)[0][0] for n, c in pos.items()}
    common = max(pos, key=lambda n: sum(pos[n].values()))
    p = best[common]
    idx = -1 if p == common - 1 else p
    if not -3 <= idx <= 3:
        idx = p - common
    at = {}
    for n, p in best.items():
        here = idx if idx >= 0 else n + idx
        if p != here and 1 <= n <= 8:
            at[str(n)] = p if p <= 3 else p - n
    return idx, at


def _same(st: Statement, ai: Statement) -> bool:
    """Şablonun sonucu yapay zekânınkiyle aynı mı (işlem sayısı ve toplamı, dönem borcu, tarihler, kart)."""
    if len(st.transactions) != len(ai.transactions):
        return False
    if money.ne(sum(t.amount for t in st.transactions), sum(t.amount for t in ai.transactions)):
        return False
    if money.ne(st.total_debt, ai.total_debt) or st.due_date != ai.due_date or st.period_end != ai.period_end:
        return False
    if re.fullmatch(r"\d{4}\*\*\*\d{4}", ai.card_no or "") and st.card_no != ai.card_no:
        return False
    for a, b in ((st.min_payment, ai.min_payment), (st.credit_limit, ai.credit_limit),
                 (st.available_limit, ai.available_limit)):  # yanlış değer yazmaktansa boş kalsın
        if a is not None and (b is None or money.ne(a, b)):
            return False
    return True


def build_from_ai(text: str, ai: Statement) -> tuple[dict, Statement] | None:
    """Yapay zekâ okumasından şablon (kaydetmeden): değerler metinde aranır, satır deseni ve etiketler çıkarılır;
    aynı metne uygulanınca yapay zekânın sonucunu aynen vermeli."""
    if not text or len(text) > 400_000 or not trustworthy(ai):
        return None
    if len({t.card for t in ai.transactions}) > 1:
        return None  # ek kartlı ekstre: şablon işlemleri kartlara ayıramaz
    bank = _ai_bank(text, ai.bank)
    if not bank:
        return None
    lines = text.splitlines()
    dates = _date_formats(text, {t.txn_date for t in ai.transactions})
    # dövizli işlem varsa önce döviz sütununu ayıran kural denenir (orig_amount da okunsun)
    fx_opts = (True, False) if any(t.orig_currency for t in ai.transactions) or _CUR.search(text) else (False,)
    for fmt in ("tr", "us"):
        h = header(text, fmt)
        labels = {}
        for key, attr in _AI_FIELD.items():
            target = getattr(ai, attr)
            if key == "prev" and not target:
                continue
            if target is None or h[key] == target:
                continue
            if a := _learn_anchor(lines, key, target, fmt):
                labels[key] = [a]
        for d in dates:
            for fx in fx_opts:
                idx, at = _amount_at(text, d, fmt, fx, ai)
                for credit in ("minus", "plus", "trail", "words"):
                    spec = {"v": 1, "kind": "card", "bank": bank, "keywords": _keywords(text), "date": d, "num": fmt,
                            "amount_idx": idx, "credit": credit}
                    spec.update({k: v for k, v in (("amount_at", at), ("fx", fx), ("labels", labels)) if v})
                    if not is_safe(spec):
                        continue
                    res = apply(text, spec, bank)
                    if res and res[1] and plausible(res[0]) and _same(res[0], ai):
                        return spec, res[0]
    return None


def learn_from_ai(text: str | None, ai: Statement) -> dict | None:
    """Yapay zekâ okumasından kart şablonu öğren (ya da devre dışı kalmış aynı kuralı yeniden aç) ve paylaş.
    Hata okumayı bozmaz. Öğrenilen şablonu döndürür."""
    from app import templates

    try:
        built = build_from_ai(text or "", ai)
        if not built:
            return None
        spec = built[0]
        if templates.save(spec):
            templates.share_async(spec)
        else:
            templates._revive(spec)
        return spec
    except Exception:  # noqa: BLE001
        log.warning("kart şablonu öğrenilemedi", exc_info=True)
        return None


# banka adı ya da kart markası → banka (ekstrede ilk geçen kazanır; kart ekstresinde IBAN çoğu zaman yok)
BANK_HINTS = [
    (r"vakıf katılım|vakif katilim", "Vakıf Katılım"), (r"ziraat katılım|ziraat katilim", "Ziraat Katılım"),
    (r"emlak katılım|emlak katilim", "Emlak Katılım"), (r"vakıfbank|vakifbank|vak[ıi]fkart|vakıflar bankası", "VakıfBank"),
    (r"kuveyt ?türk|kuveyt ?turk", "Kuveyt Türk"), (r"garanti|\bbonus\b", "Garanti"), (r"akbank|\baxess\b|\bwings\b", "Akbank"),
    (r"yapı ?kredi|yapi ?kredi|world ?card|worldcard", "Yapı Kredi"), (r"iş ?bankası|is ?bankasi|işbank|isbank|\bmaximum\b", "İş Bankası"),
    (r"ziraat|bankkart", "Ziraat"), (r"halkbank|halk bankası|\bparaf\b", "Halkbank"), (r"\bqnb\b|cardfinans|enpara", "QNB"),
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


def _safe_labels(labels) -> bool:
    if not isinstance(labels, dict) or not labels or set(labels) - set(_NEED):
        return False
    for anchors in labels.values():
        if not isinstance(anchors, list) or not 1 <= len(anchors) <= 3:
            return False
        for a in anchors:
            if not isinstance(a, dict) or set(a) != {"label", "off", "nth"} or not isinstance(a["label"], str):
                return False
            ws = a["label"].split()
            if not 1 <= len(ws) <= 12 or a["label"] != " ".join(ws) or not all(w in LABEL_WORDS for w in ws):
                return False
            if not all(type(a[k]) is int and 0 <= a[k] <= lim for k, lim in (("off", 2), ("nth", 5))):
                return False
    return True


def is_safe(spec: dict) -> bool:
    """Şablon yalnız izinli alanlar, sabit sözlük kelimeleri (anahtar kelime, etiket) ve küçük sayılar içerir: belgeden
    metin (isim, kart no, tutar, tarih) taşıyamaz, kod çalıştıramaz."""
    from app.templates import IBAN_BANKS

    try:
        if set(spec) - {"v", "kind", "bank", "keywords", "date", "num", "amount_idx", "credit", "amount_at", "fx",
                        "labels"}:
            return False
        if spec.get("kind") != "card" or spec.get("bank") not in set(IBAN_BANKS.values()) | {""}:
            return False
        if not all(k in KEYWORDS for k in spec.get("keywords", [])):
            return False
        d = spec["date"]
        if (d["order"], d["sep"]) not in _DATES or set(d) - {"order", "sep", "year"} or d.get("year", 4) not in (2, 4):
            return False
        if "amount_at" in spec:
            at = spec["amount_at"]
            if not isinstance(at, dict) or not at or len(at) > 8 or not all(
                    k in {str(n) for n in range(1, 9)} and type(v) is int and -3 <= v <= 3 for k, v in at.items()):
                return False
        if "fx" in spec and spec["fx"] is not True:
            return False
        if "labels" in spec and not _safe_labels(spec["labels"]):
            return False
        return spec["num"] in ("tr", "us") and spec["credit"] in ("minus", "plus", "trail", "words") \
            and isinstance(spec["amount_idx"], int) and -3 <= spec["amount_idx"] <= 3
    except (KeyError, TypeError, AttributeError):
        return False


def read(text: str) -> Statement | None:
    """Kayıtlı kart şablonlarından biri belgeyi doğrulanabilir okuyorsa onu; yoksa yeni kural öğrenmeyi dene.
    Eşleşen şablonların hiçbiri tutmadıysa hepsinin hata sayısı artar (art arda iki → devre dışı)."""
    from app import templates

    templates.sync_community()
    bank = bank_of(text)
    failed = []
    for t in templates.all_templates():
        spec = t["spec"]
        if spec.get("kind") != "card" or t.get("disabled") or not matches(text, spec):
            continue
        try:
            res = apply(text, spec, bank)
        except Exception:  # noqa: BLE001 - bozuk/uyumsuz şablon okumayı bozmaz
            res = None
        if res and res[1] and plausible(res[0]):
            templates._used(t["id"])
            return res[0]
        failed.append(t["id"])
    templates._failed(failed)
    learned = learn(text, bank)
    if learned:
        spec, st = learned
        if spec["bank"] and templates.save(spec):  # banka adı bilinmiyorsa kaydetme (yanlış eşleşmesin)
            templates.share_async(spec)
        return st
    return None


# ---------------------------------------------------------------- teşhis (gönüllüler için; kişisel veri yazmaz)

def diagnose(text: str) -> dict:
    """Okunamayan ekstre için: hangi etiketler bulundu, kaç tarihli satır var, en yakın deneme ne kadar tuttu.
    Çıktıda isim, kart numarası, açıklama ya da tutar yoktur (sadece evet/hayır, sayılar ve fark yüzdesi)."""
    bank = bank_of(text)
    learned = learn(text, bank)
    out = {"banka": bank or "tanınmadı", "okundu": bool(learned)}
    if learned:
        spec, st = learned
        out.update({"işlem": len(st.transactions), "kural": {k: spec[k] for k in ("date", "num", "amount_idx", "credit")}})
        return out
    out["tarihli_satır"] = {f"{o}{s}": len(re.findall(r"(?m)^\s*" + p, text)) for (o, s), p in _DATES.items()}
    best = None
    for fmt in ("tr", "us"):
        h = header(text, fmt)
        found = {k: h[k] is not None for k in LABELS} | {"denklem_satırı": "equation" in h, "kart_no": bool(h["card_no"])}
        for (order, sep) in _DATES:
            for idx in (-1, 0, -2, 1):
                for credit in ("minus", "plus"):
                    spec = {"v": 1, "kind": "card", "bank": bank, "keywords": [], "date": {"sep": sep, "order": order},
                            "num": fmt, "amount_idx": idx, "credit": credit}
                    try:
                        res = apply(text, spec, bank)
                    except Exception:  # noqa: BLE001
                        res = None
                    if not res or not res[0].total_debt:
                        continue
                    st = res[0]
                    charges = sum(t.amount for t in st.transactions if t.txn_type != PAYMENT)
                    diff = abs(st.previous_balance - st.payments + charges - st.total_debt) / st.total_debt * 100
                    if best is None or diff < best[0]:
                        best = (diff, fmt, found, len(st.transactions))
        if best is None or best[1] != fmt:
            out.setdefault("etiketler_" + fmt, found)
    if best:
        out.update({"en_yakın_fark_%": round(best[0], 2), "sayı_biçimi": best[1], "etiketler": best[2], "işlem_adayı": best[3]})
    return out


def anonymize(text: str) -> str:
    """Yapıyı koruyarak kişisel veriyi maskele: etiket sözlüğü ve tarih/tutar düzeni kalır; isimler, açıklamalar,
    kart/hesap/TC numaraları değişir. Tutarlar korunur (denklem test edilebilsin); istersen elle değiştir."""
    stems = set()
    words = [*LABELS.values(), *KEYWORDS, _PREV_LINE.pattern, _PAY_WORDS.pattern, _FEE_WORDS.pattern,
             *(p for p, _ in BANK_HINTS), "taksit taksidi iade toplam tutar tarih işlem açıklama tl usd eur son kalan kredi "
             "kartı kart limit puan bonus ekstre hesap özet sonraki bir"]
    for w in words:
        stems |= {x for x in re.findall(r"[a-z]+", _fold(w)) if len(x) >= 2}
    names: dict[str, str] = {}

    def word(m):
        w = _fold(m.group(0))
        if w in stems or any(len(st) >= 4 and w.startswith(st) for st in stems):  # "borcunuz" ← "borcu"
            return m.group(0)
        return names.setdefault(w, f"W{len(names) + 1}")

    out = []
    for ln in text.splitlines():
        ln = _CARD.sub(lambda m: "1111******2222", ln)
        ln = re.sub(r"TR\s?\d{2}(?:\s?\d{4}){5}\s?\d{0,2}", "TR00 0000 0000 0000 0000 0000 00", ln)
        ln = re.sub(r"\S+@\S+", "eposta@ornek.com", ln)
        ln = re.sub(r"(?<![\d.,])\d{6,}(?![\d.,])", lambda m: "9" * len(m.group(0)), ln)  # TCKN, müşteri/hesap/tel no
        ln = re.sub(r"[A-Za-zÇĞİÖŞÜçğıöşü]{2,}", word, ln)
        out.append(ln)
    return "\n".join(out)


if __name__ == "__main__":
    import json
    import sys

    from app.parsers import extract_text

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("Kullanım: python -m app.card_reader ekstre.pdf [pdf-şifresi] [--anonim]")
    txt = extract_text(open(args[0], "rb").read(), args[1] if len(args) > 1 else None)
    if "--anonim" in sys.argv:
        print(anonymize(txt))
    else:
        print(json.dumps(diagnose(txt), ensure_ascii=False, indent=2))
