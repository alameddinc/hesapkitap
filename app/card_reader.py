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
     "wrap": true,                           # açıklama tarih satırının üstüne/altına taşıyor (Halkbank: hücre içinde
                                             #   kaydırılmış açıklama; tarih + tutar satırında açıklama yok)
     "minus_pts": true,                      # sonu "-" işaretli değer puan sütunudur, tutar değil (Garanti: harcanan
                                             #   Bonus "50,00 -" borcu değiştirmez; ödeme "4.200,00 +")
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
# ay adı (Türkçe, büyük/küçük, Türkçe karaktersiz ya da üç harf kısaltma): "15 Ağustos 2026"; kelime arası boşluğu metne
# çıkmayan PDF'te "15Ağustos2026"
_MONTH_NAMES = (r"(?i:ocak|[şs]ubat|mart|n[ıiİ]san|may[ıiİ]s|haz[ıiİ]ran|temmuz|a[ğg]ustos|eyl[üu]l|ek[ıiİ]m|kas[ıiİ]m"
                r"|aral[ıiİ]k|oca|[şs]ub|mar|n[ıiİ]s|may|haz|tem|a[ğg]u|eyl|ek[ıiİ]|kas|ara)")
_MONTH = {k: i for i, k in enumerate(("oca", "sub", "mar", "nis", "may", "haz", "tem", "agu", "eyl", "eki", "kas", "ara"),
                                     1)}
_DATES = {("dmy", "."): r"(\d{2})\.(\d{2})\.(\d{4})", ("dmy", "/"): r"(\d{2})/(\d{2})/(\d{4})",
          ("dmy", "-"): r"(\d{2})-(\d{2})-(\d{4})",
          ("dmy", "ay"): r"(\d{1,2})\s?(" + _MONTH_NAMES + r")\.?\s?(\d{4})"}
_ANY_DATE = re.compile(r"(?<!\d)(\d{2})[./-](\d{2})[./-](\d{4}|\d{2})(?!\d)"
                       r"|(?<!\d)(\d{1,2})\s?(" + _MONTH_NAMES + r")\.?\s?(\d{4})(?!\d)")
# para: isteğe bağlı işaret + değer; önünde rakam/ayraç/"x" (5x1.315,40 taksit gösterimi) olmasın
_MONEY = {"tr": re.compile(r"(?<![\d.,x×])([+-]?)\s?(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d,%]|\.\d)"),
          "us": re.compile(r"(?<![\d.,x×])([+-]?)\s?(\d{1,3}(?:,\d{3})*\.\d{2})(?![\d.%]|,\d)")}
_CUR = re.compile(r"(?<![A-Za-z])(USD|EUR|GBP|CHF|\$|€|£)(?![A-Za-z])")
_CUR_CODE = {"$": "USD", "€": "EUR", "£": "GBP"}
# maskeli kart no: "5571 23** **** 4410", "557123******4410"; Halkbank "5571-23##-####-4410" (iki satıra bölünebilir:
# maske sınıfında boşluk = satır sonu da)
_CARD = re.compile(r"(\d{4})[\s-]?(?:\d{2})?[*xX•#][*xX•#\s-]{3,}(\d{4})")
_EQUATION = {"tr": r"(\d[\d.]*,\d{2})\s*\+\s*(\d[\d.]*,\d{2})\s*-\s*(\d[\d.]*,\d{2})\s*=\s*(\d[\d.]*,\d{2})",
             "us": r"(\d[\d,]*\.\d{2})\s*\+\s*(\d[\d,]*\.\d{2})\s*-\s*(\d[\d,]*\.\d{2})\s*=\s*(\d[\d,]*\.\d{2})"}
# Garanti: "4.200,00 TL + 5.481,89 TL + 0,00 TL - 4.200,00 TL = 5.481,89 TL" (önceki + harcamalar + faiz/ücret −
# ödemeler = dönem borcu; her değerin yanında para birimi). Yalnız aritmetiği tutuyorsa kullanılır.
_N = {"tr": r"(\d[\d.]*,\d{2})", "us": r"(\d[\d,]*\.\d{2})"}
_EQUATION_CUR = {f: (r"{n}{c}\s*\+\s*{n}{c}(?:\s*\+\s*{n}{c})?\s*-\s*{n}{c}\s*=\s*{n}").format(
    n=n, c=r"(?:\s*(?i:tl|try))?") for f, n in _N.items()}

# etiketler Türkçe karaktersiz yazılır; metin de _fold ile aynı biçime çevrilir ("ÖDEME" = "ODEME")
LABELS = {
    "period_end": r"hesap kesim tarihi|ekstre kesim tarihi|hesap ozeti tarihi|ekstre tarihi|kesim tarihi",
    "due": r"son odeme tarihi",
    # "Önceki dönem borcu" / "Önceki hesap özeti borcu" dönem borcu sayılmasın (önceki borç satırı çoğu zaman üstte)
    "total": r"(?<!onceki )(?<!gecen )(?:donem borcu|toplam borc|ekstre borcu|hesap ozeti borcu|guncel donem borcu"
             r"|odenecek toplam)",
    "min": r"asgari odeme|en az odeme",
    # "Kullanılabilir kart limiti" toplam limit sayılmasın (kendi etiketi: available)
    "limit": r"(?<!kullanilabilir )(?<!kalan )(?:kart limiti|kredi limiti|toplam limit)",
    "available": r"kullanilabilir (?:kart |kredi )?limit|kalan (?:kart |kredi )?limit",
    "prev": r"onceki donem (?:hesap ozeti )?(?:borcu|bakiye)|onceki ekstre (?:borcu|bakiye)|onceki hesap ozeti (?:borcu|bakiye)"
            r"|gecen donem(?:den)? (?:devreden )?borc|devreden borc|donem basi (?:borcu|bakiye)"
            r"|onceki donem(?:den)? devreden",  # Halkbank: "Önceki Dönem Devreden" / ": 1,234.56 TL" / "Ekstre Borcu"
    "next_cut": r"sonraki hesap kesim|sonraki kesim",
    "next_due": r"sonraki son odeme",
}
_DATE_KEYS = ("period_end", "due", "next_cut", "next_due")


def _loose(pat: str) -> str:
    """Etiket deseni boşluksuz metinde de tutsun ("HesapKesimTarihi", "DönemBorcu(TL)"): kelime arası boşluk isteğe
    bağlı; "(?<!onceki )" gibi geriye bakışlar bitişik biçim için de eklenir."""
    out = []
    for part in re.split(r"(\(\?<![^)]*\))", pat):
        if part.startswith("(?<!"):
            body = part[4:-1]
            out.append(part + (f"(?<!{body.replace(' ', '')})" if " " in body else ""))
        else:
            out.append(part.replace(" ", " ?"))
    return "".join(out)


_LABEL_RES = {k: re.compile(_loose(v)) for k, v in LABELS.items()}
_PREV_LINE = re.compile(r"onceki donem|onceki ekstre|onceki hesap ozeti|devreden bakiye|devreden borc|gecen donem"
                        r"|donem basi bakiye")
_PAY_WORDS = re.compile(r"odeme|tesekkur|payment|tahsilat|hesaptan aktarim")  # İş Bankası: "HESAPTAN AKTARIM"
_REFUND_WORDS = re.compile(r"iade|iptal|refund")
_FEE_WORDS = re.compile(r"faiz|bsmv|kkdf|ucret|aidat|vade fark|gecikme|kar payi")  # kâr payı: katılım bankası faizi
_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")

# öğrenilen etiketlerde izin verilen kelimeler (katlanmış). Şablona bunların dışında belgeden kelime girmez.
LABEL_WORDS = frozenset("""
hesap hesabi hesabinizin kesim tarihi tarih tarihiniz tarihli ekstre ekstrenin ekstreniz ekstresi ozeti ozet
ozetinin ozetiniz bildirim bildirimi duzenleme duzenlenme son odeme odemesi odenecek odenmesi gereken donem donemi
donemin donemine borcu borcunuz borc borcun toplam toplami genel guncel asgari minimum en az tutari tutar tutariniz
kart karti kartinizin kartiniz kredi limiti limit limitiniz kullanilabilir kalan nakit onceki gecen devreden bakiye
bakiyesi bakiyeniz yeni bu ay ayin icin olan ve ile itibariyla itibari tl try vade vadesi gunu sonu donemden basi
odemeniz
""".split())
# öğrenilen bölüm sonu başlığında ("Gelecek Dönem Taksitleriniz"): bu satırdan sonra tarihli satırlar işlem değildir.
# Yalnız bu sabit kelimelerden oluşabilir (belgeden metin taşımaz).
SECTION_WORDS = frozenset("""
gelecek sonraki donem donemler donemlerde donemde donemine donemlere taksit taksitler taksitleri taksitleriniz taksitli
taksitlendirilmis kalan islem islemler islemleri islemleriniz plan plani odeme odemeler odemeleri yansiyacak
yansiyacak yansiyacaktir tutar tutari tutarlari toplam bilgi bilgileri bilgilendirme detay detayi detaylari ozet ozeti
puan puanlari puaniniz maxipuan bonus chip para chippara worldpuan mil kazanilan kazandiginiz kullanilan harcanan ve
ile ay aylik aylara kampanya kampanyalar hareket hareketleri ileri tarihli vadeli bakiye bakiyesi borclari borc
hesap kart karti kartiniz kredi ek
""".split())
# öğrenilebilen başlık alanı → (her kümeden en az bir kelime, olmaması gereken kelimeler); yapay zekâ alanı
_NEED = {
    "period_end": ([{"kesim", "ekstre", "ekstrenin", "ekstreniz", "ekstresi", "ozeti", "ozet", "ozetinin", "ozetiniz",
                     "bildirim", "bildirimi", "duzenleme", "duzenlenme"}], {"son", "odeme", "vade", "vadesi"}),
    "due": ([{"son", "vade", "vadesi"}, {"odeme", "odemesi", "tarihi", "tarih", "gunu", "vade", "vadesi"}],
            {"asgari", "minimum", "tutar", "tutari", "kesim"}),
    "total": ([{"borcu", "borcunuz", "borc", "borcun", "toplam", "toplami", "odenecek", "bakiye", "bakiyesi",
                "bakiyeniz"}], {"onceki", "gecen", "devreden", "basi", "asgari", "minimum", "limit", "limiti",
                                "limitiniz", "nakit"}),
    "min": ([{"asgari", "minimum", "az"}], set()),
    "limit": ([{"limit", "limiti", "limitiniz"}], {"kullanilabilir", "kalan", "nakit"}),
    "available": ([{"kullanilabilir", "kalan"}, {"limit", "limiti", "limitiniz"}], {"nakit"}),
    "prev": ([{"onceki", "gecen", "devreden", "basi"}], set()),
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
    """Gün, ay (sayı ya da ay adı), yıl gruplarından tarih (_ANY_DATE'in hangi seçeneği eşleştiyse)."""
    try:
        d, mo, y = [g for g in m.groups() if g is not None][:3]
        mo = int(mo) if mo.isdigit() else _MONTH.get(_fold(mo)[:3])
        y = int(y)
        return date(y + 2000 if y < 100 else y, mo, int(d)) if mo else None
    except (ValueError, TypeError):
        return None


def _date_re(d: dict) -> re.Pattern:
    """İşlem satırının başındaki tarih ("year": 2 → iki haneli yıl, 14.10.26). Dört haneli yıldan sonra sınır aranmaz:
    bazı PDF'lerde tarih açıklamaya ya da referans numarasına bitişik çıkar ("01/10/2026MARKET", "01/10/20261234-…")."""
    pat = _DATES[(d["order"], d["sep"])]
    if d.get("year") == 2:
        return re.compile(r"^\s*" + pat.replace(r"(\d{4})", r"(\d{2})(?!\d)"))
    return re.compile(r"^\s*" + pat)


def _field(lines: list[str], key: str, kind: str, fmt: str):
    """Etiketin aynı satırındaki (yoksa sonraki satırdaki) ilk tarih/para. "Sonraki …" etiketleri karışmasın."""
    pat = _LABEL_RES[key]
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
        whole = len(a["label"].split()) < 8  # öğrenilen etiket önündeki sözlük kelimelerinin hepsini içerir (en çok 8)
        for ln in lines:
            low = _fold(ln)
            for lm in lre.finditer(low):
                # etiket başka bir etiketin parçası olmasın: "dönem borcu" ≠ "Önceki Dönem Borcu", "son ödeme tarihi" ≠
                # "Sonraki Son Ödeme Tarihi" (önündeki kelime sözlükte ya da "sonraki" ise başka alan)
                prev_word = re.search(r"([a-z]+)[^a-z0-9]{1,3}$", low[max(0, lm.start() - 40):lm.start()])
                if whole and prev_word and (prev_word.group(1) in LABEL_WORDS
                                            or prev_word.group(1) in ("sonraki", "gelecek", "next")):
                    continue
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


def header(text: str, fmt: str, labels: dict | None = None, skip=()) -> dict:
    """Başlık alanları: önce şablonun öğrenilmiş etiketleri, bulunamazsa ortak etiket sözlüğü (LABELS). skip: şablonun
    okumadığı (yapay zekâyla doğrulanamamış) isteğe bağlı alanlar — boş kalır."""
    lines = text.splitlines()
    out = {}
    for k in LABELS:
        if k in skip:
            out[k] = None
            continue
        kind = "date" if k in _DATE_KEYS else "money"
        v = None
        for a in (labels or {}).get(k, []):
            if (v := _anchor_value(lines, a, kind, fmt)) is not None:
                break
        out[k] = v if v is not None else _field(lines, k, kind, fmt)
    if eq := re.search(_EQUATION[fmt], text):
        out["equation"] = [_num(x, fmt) for x in eq.groups()]  # önceki + dönem içi − ödemeler = dönem borcu
    elif eq := re.search(_EQUATION_CUR[fmt], text):
        a, b, c, d, e = (_num(x, fmt) if x else 0.0 for x in eq.groups())
        if money.eq(a + b + c - d, e):
            out["equation"] = [a, round(b + c, 2), d, e]  # faiz/ücret terimi dönem içine katılır
    card = _CARD.search(text)
    out["card_no"] = f"{card.group(1)}***{card.group(2)}" if card else None
    return out


def _installment(desc: str) -> tuple[int | None, int | None]:
    """(kaçıncı taksit, kalan taksit). "İşlemin 3 / 9 Taksidi" → (3, 6); "1. Taksit … 5x6.240,00" (kalan 5) → (1, 5);
    "4. Taksit … Son Taksit" → (4, 0); taksitli iade "2. İade … 4x…" → (2, 4). Tarih parçaları (29/03/2025) sayılmaz."""
    low = _fold(desc)
    if m := re.search(r"(?<![\d/.])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/]|\.\d)", low):  # "2/6.Taksit" (Akbank)
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
        return bool(_TRAIL_MINUS.match(rest[v.end():]))
    if mode == "trailplus":  # "2.150,00 +" / "2.150,00+"
        return bool(re.match(r"\s?\+(?!\s?\d)", rest[v.end():]))
    if mode == "paren":  # Akbank: "1,250.00(-)" / "1,250.00 (-)"
        return bool(_PAREN_MINUS.match(rest[v.end():]))
    return bool(_PAY_WORDS.search(low) or _REFUND_WORDS.search(low))  # words


# Akbank: "1,250.00(-)"; taksit satırında kalan taksit sayısı arada olabilir: "250.00x4(-)"
_PAREN_MINUS = re.compile(r"(?:[x×]\d{1,2})?\s?\(\s?-\s?\)")
_TRAIL_MINUS = re.compile(r"\s?-(?!\s?\d)")  # değerin sonundaki "-": "1.000,00-" / "50,00 -"


def _values(rest: str, toks: list, pts: bool) -> list[re.Match]:
    """Tutar adayı değerler: döviz kodlu değer (orig_amount) hariç; pts ("minus_pts") açıksa sonu "-" işaretli puan
    sütunu değeri de hariç."""
    return [t for t, code in toks if not code and not (pts and _TRAIL_MINUS.match(rest[t.end():]))]


def _desc(rest: str, fmt: str, fx: bool, credit: str) -> str:
    desc = _MONEY[fmt].sub(" ", rest)
    if fx:
        desc = _CUR.sub(" ", desc)
    if credit == "trail":
        desc = re.sub(r"(?<=\s)-(?=\s|$)", " ", desc)
    if credit == "trailplus":
        desc = re.sub(r"(?<=\s)\+(?=\s|$)", " ", desc)
    if credit == "paren":
        desc = _PAREN_MINUS.sub(" ", desc)
    return re.sub(r"\s+", " ", desc).strip()[:120] or "?"


def _looks_installment(desc: str) -> bool:
    """Taksit satırı mı (tutar sütunu taksitsiz satırdan farklı olabilir: işlem toplamı + bu ayki taksit)."""
    return _installment(desc)[0] is not None or "taksit" in _fold(desc)


def _pick(vals: list, at: dict, inst_at: dict, idx: int, inst: bool):
    n = str(len(vals))
    try:
        return vals[inst_at[n] if inst and n in inst_at else at.get(n, idx)]
    except IndexError:
        return None


def _stop_words(spec: dict) -> list[list[str]]:
    return [s.split() for s in spec.get("stop") or []]


def _fragment(ln: str, dre: re.Pattern, fmt: str) -> bool:
    """Kaydırılmış açıklama parçası mı: tarihle başlamaz, tarih / kart no / para değeri yok (taksit toplamı
    "1.234,00/9-" gösterimi sayılmaz), kısa. Tutar asla buradan okunmaz; yalnız açıklama (tür, taksit) için."""
    s = ln.strip()
    if not s or len(s) > 80 or dre.match(ln) or _ANY_DATE.search(ln) or re.search(r"[*#•]{3}|[xX]{4}", ln):
        return False  # maskeli kart no parçası ("####-4410/ AD SOYAD") açıklama değil
    if any(not re.match(r"\s?/\s?\d", ln[m.end():]) for m in _MONEY[fmt].finditer(ln)):
        return False
    return len(_words(_fold(s))) <= 12


def _bare(desc: str) -> bool:
    """Açıklamada harf yok (yalnız tarih / TL): sütunlu başlığın değer satırı ya da açıklaması taşmış işlem satırı."""
    return not re.search(r"[a-z]", re.sub(r"\b(?:tl|try)\b", " ", _ANY_DATE.sub(" ", _fold(desc))))


def _card_at(lines: list[str], i: int) -> re.Match | None:
    """Satırdaki ek kart başlığı; kart no iki satıra bölünmüşse ("Kart No: 5571-23##-" / "####-8812 AD") ilk satırda."""
    if c := _CARD.search(lines[i]):
        return c
    if i + 1 < len(lines) and (c := _CARD.search(lines[i] + "\n" + lines[i + 1])) and c.start() < len(lines[i]):
        return c
    return None


def apply(text: str, spec: dict, bank: str = "", trace: list | None = None) -> tuple[Statement, bool] | None:
    """Şablonla oku → (Statement, denklem tuttu mu) ya da None. trace verilirse işlemlerin satır numaraları eklenir.
    İsteğe bağlı kurallar (yapay zekâ okumasından öğrenilir): cards "section" — ek kart bölümü başlığındaki kart
    numarası sonraki işlemlerin kartı; stop — bu başlıktan sonraki tarihli satırlar işlem değil (gelecek taksit tablosu);
    inst_at — taksit satırında tutarın sırası; prev0 — önceki borç yazmıyorsa 0; skip — okunmayan isteğe bağlı alanlar;
    wrap — açıklama tarih satırının hemen üst / alt satırına taşmış (hücrede kaydırılmış): bitişik parçalar (_fragment)
    açıklamaya eklenir, açıklamasız tarih + tutar satırı da (tek tarihli, en az bir parçası varsa) işlemdir;
    minus_pts — sonu "-" işaretli değer puan sütunudur (tutar sayılmaz; yalnız ondan oluşan satır işlem değil)."""
    fmt = spec["num"]
    h = header(text, fmt, spec.get("labels"), spec.get("skip") or ())
    if not h["period_end"] or h["total"] is None:
        return None
    dre = _date_re(spec["date"])
    fx = bool(spec.get("fx"))
    at, inst_at = spec.get("amount_at") or {}, spec.get("inst_at") or {}
    sections = spec.get("cards") == "section"
    stops = _stop_words(spec)
    card = h["card_no"] or "****"
    prev = h["prev"]
    wrap = bool(spec.get("wrap"))
    pts = bool(spec.get("minus_pts"))
    txns = []
    lines = text.splitlines()
    # wrap: açıklamasız tarih satırı bitişik parçaları alır. Açıklaması kendi satırında olan satır (üç satırlık hücre:
    # "3600.00/9-" / satır / "09.Taksit") bir parçayı, parça başka tarih satırına da değiyorsa yalnız öbür yanındaki
    # parça tek sahipliyse alır (hücre dikeyde ortalı) — komşu satırın parçası karışmasın.
    led_at = {j for j, x in enumerate(lines) if dre.match(x)} if wrap else set()

    def frag(j: int) -> bool:
        return 0 <= j < len(lines) and _fragment(lines[j], dre, fmt)

    def parts(i: int, bare: bool) -> tuple[list[str], list[str]]:
        if not wrap:
            return [], []
        up, down = frag(i - 1), frag(i + 1)
        up_own, down_own = up and i - 2 not in led_at, down and i + 2 not in led_at
        up, down = (up, down) if bare else (up_own or (up and down_own), down_own or (down and up_own))
        return [lines[i - 1]] if up else [], [lines[i + 1]] if down else []
    for i, ln in enumerate(lines):
        m = dre.match(ln)
        if not m or not (d := _date(m)):
            if stops and not re.search(r"\d", ln) and _words(_fold(ln)) in stops:
                break
            if sections and (c := _card_at(lines, i)):
                card = f"{c.group(1)}***{c.group(2)}"
            continue
        rest = ln[m.end():]
        toks = _tokens(rest, fmt, fx)
        vals = _values(rest, toks, pts)
        if not vals:
            continue  # pts: yalnız harcanan puan satırı ("Bonus kullanımı 50,00 -") işlem değil
        low = _fold(rest)
        if _PREV_LINE.search(low):  # "önceki dönem hesap özeti bakiyeniz": işlem değil, devreden borç
            prev = _num(vals[-1].group(2), fmt) if prev is None else prev
            continue
        desc = _desc(rest, fmt, fx, spec["credit"])
        if pts:
            desc = " ".join(re.sub(r"(?<=\s)-(?=\s|$)", " ", f" {desc} ").split()) or "?"
        bare = _bare(desc)
        above, below = parts(i, bare)
        if bare and not ((above or below) and len(_ANY_DATE.findall(ln)) == 1):
            continue  # açıklamasız tarih satırı (sütunlu başlığın değer satırı: "12.10.2026 22.10.2026 3.923,90 TL")
        if above or below:
            desc = re.sub(r"\s+", " ", " ".join([*above, "" if bare else desc, *below])).strip()[:120]
            low = f"{low} {_fold(desc)}"
        v = _pick(vals, at, inst_at, spec["amount_idx"], bool(inst_at) and _looks_installment(desc))
        if v is None:
            continue
        amount = _num(v.group(2), fmt)
        credit = _is_credit(spec["credit"], v, rest, low)
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
        if trace is not None:
            trace.append(i)
    if len(txns) < 2:
        return None
    eq = h.get("equation")
    if prev is None and eq:
        prev = eq[0]
    if prev is None and spec.get("prev0"):
        prev = 0.0
    charges = round(sum(t.amount for t in txns if t.txn_type != PAYMENT), 2)
    fees = round(sum(t.amount for t in txns if t.txn_type == FEE), 2)
    paid = round(-sum(t.amount for t in txns if t.txn_type == PAYMENT), 2)
    if prev is not None:
        ok = abs(prev - paid + charges - h["total"]) <= 0.05
    elif eq:
        ok = abs(charges - eq[1]) <= 0.05 and abs(paid - eq[2]) <= 0.05
    else:
        ok = False
    st = Statement(bank=bank or spec.get("bank") or "Kredi kartı", card_no=h["card_no"] or "****",
                   period_end=h["period_end"],
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
        if t.installment_no is None and not _looks_installment(t.description) \
                and t.txn_date < st.period_end - timedelta(days=_PERIOD_DAYS):
            return False
    return True


def _txn_equation_diff(st: Statement) -> float | None:
    """önceki borç − ödemeler + harcamalar (iade eksi, faiz dahil) − dönem borcu; işlemlerin kendisinden."""
    if st.total_debt is None:
        return None
    charges = sum(t.amount for t in st.transactions if t.txn_type != PAYMENT)
    paid = -sum(t.amount for t in st.transactions if t.txn_type == PAYMENT)
    return round((st.previous_balance or 0.0) - paid + charges - st.total_debt, 2)


def _summary_only(st: Statement) -> dict[str, float]:
    """Yapay zekânın özete yazıp işlem listesine koymadığı kalemler → {işlem türü: listede eksik toplam (ödeme eksi)}.
    Bilinen alışkanlıklar: ödeme satırı yok ama özetteki "ödemeler" dolu (önceki borç kapatılmış); faiz/BSMV/KKDF
    satırları yok ama özetteki dönem içi tutar onları içeriyor. Yalnız iki açık da ≥ 0 ve işlem denkleminin farkını
    KURUŞU KURUŞUNA açıklıyorsa; değilse {} (yapay zekânın fazladan / ters işaretli satırı tolere edilmez)."""
    d = _txn_equation_diff(st)
    if d is None or abs(d) <= 0.05:
        return {}
    paid = -sum(t.amount for t in st.transactions if t.txn_type == PAYMENT)
    charges = sum(t.amount for t in st.transactions if t.txn_type != PAYMENT)
    pay_gap = round((st.payments or 0.0) - paid, 2)
    fee_gap = round((st.period_activity or 0.0) + (st.interest_fees or 0.0) - charges, 2)
    pay_gap = pay_gap if pay_gap > 0.05 else 0.0
    fee_gap = fee_gap if fee_gap > 0.05 else 0.0
    # eksik ödeme işlem farkını artırır (önceki − 0 + harcama > borç), eksik faiz azaltır
    if not (pay_gap or fee_gap) or abs(d - pay_gap + fee_gap) > 0.05:
        return {}
    return {k: v for k, v in ((PAYMENT, -pay_gap), (FEE, fee_gap)) if v}


def ai_check(st: Statement) -> str | None:
    """Yapay zekâ okuması şablon öğretecek kadar güvenilir mi; değilse neden kodu. Ölçüt ŞABLONUN kendi ölçütüyle
    aynı: işlemlerden kurulan ekstre denklemi (önceki − ödemeler + harcamalar = dönem borcu) kuruşu kuruşuna ve tarihler
    tutarlı. Modelin özet alanları (period_activity, payments) burada aranmaz: model onları işlemlerden ayrı okur ve
    sık sık faizi dahil/hariç yazar — işlemler denklemi tutuyorsa özet alanı yanlış olsa da işlem listesi doğrudur.
    İstisna (_summary_only): ödeme / faiz satırı listede hiç yok ama özetteki tutarı farkı aynen açıklıyor — okuma
    kabul edilir; şablon o satırları da okumalı ve kendi denklemini tutturmalı (_mismatch)."""
    if len(st.transactions) < 2 or not st.total_debt:
        return "ai_few_txns"
    d = _txn_equation_diff(st)
    if d is None or (abs(d) > 0.05 and not _summary_only(st)):
        return "ai_equation"
    if not plausible(st):
        return "ai_dates"
    return None


def _abs_keys(txns) -> Counter:
    return Counter((t.txn_date, money.kurus(abs(t.amount))) for t in txns)


def merge_fix(ai: Statement, tmpl: Statement) -> Statement:
    """Yapay zekâ okuması + aynı belgenin şablon okuması (denklemi tutan): satırlar şablondan (sıra, tutar, işaret);
    şablonla aynı (tarih, tutar) satır yapay zekâda varsa açıklama, kart, taksit, döviz bilgisi yapay zekâdan — işareti
    terse okunmuşsa (iade harcama yazılmış) tutar ve tür şablondan. Başlık ve özet alanları yapay zekânın."""
    from dataclasses import replace

    pool: dict[tuple, list[Transaction]] = {}
    for t in ai.transactions:
        pool.setdefault((t.txn_date, money.kurus(abs(t.amount))), []).append(t)
    out = []
    for t in tmpl.transactions:
        same = pool.get((t.txn_date, money.kurus(abs(t.amount))))
        if not same:
            out.append(replace(t, card=ai.card_no if _card_key(ai.card_no) else t.card))
            continue
        a = next((x for x in same if (x.amount < 0) == (t.amount < 0)), same[0])
        same.remove(a)
        out.append(a if (a.amount < 0) == (t.amount < 0) else replace(a, amount=t.amount, txn_type=t.txn_type))
    return replace(ai, transactions=out)


def _text_fix(text: str, ai: Statement, bank: str) -> tuple[Statement, tuple[dict, Statement]] | None:
    """Yapay zekânın işlemleri denklemi tutmuyor: metinden (yapay zekâsız, learn) denklemi tutan okuma varsa ve AYNI
    belgeyi okuduğu belliyse (dönem borcu, kesim, son ödeme aynı; satırların en az %80'i tarih + tutarla — işaretsiz —
    eşleşiyor) → (düzeltilmiş okuma (merge_fix), learn sonucu). Yapay zekâ birkaç satırı atlamış, işaretini ters
    okumuş (taksitli iade harcama yazılmış) ya da fazladan satır eklemiş olabilir; şablonun kendi denklemi tutar."""
    got = learn(text, bank)
    if not got:
        return None
    fixed = _merged(got[1], ai)
    return (fixed, got) if fixed else None


def _same_doc(st: Statement, ai: Statement) -> bool:
    """Şablon okuması yapay zekânınkiyle AYNI belge mi: dönem borcu, kesim, son ödeme aynı; satırların en az %80'i
    tarih + tutarla (işaretsiz) eşleşiyor."""
    if money.ne(st.total_debt, ai.total_debt) or st.period_end != ai.period_end or st.due_date != ai.due_date:
        return False
    common = sum((_abs_keys(ai.transactions) & _abs_keys(st.transactions)).values())
    return common >= 0.8 * max(len(ai.transactions), len(st.transactions))


def _merged(st: Statement, ai: Statement) -> Statement | None:
    """Denklemi kendi tutan şablon okuması (st) aynı belgeyse yapay zekâ okumasının düzeltilmişi (merge_fix): işlem
    denklemi kuruşu kuruşuna ve tarihler tutarlı; değilse None."""
    if not _same_doc(st, ai):
        return None
    fixed = merge_fix(ai, st)
    d = _txn_equation_diff(fixed)
    return fixed if d is not None and abs(d) <= 0.05 and plausible(fixed) else None


def _cid_text(text: str) -> bool:
    """PDF yazı tipinde karakter eşlemesi yok: metin "(cid:123)" glif kodlarından ibaret — şablon öğrenilemez."""
    n = text.count("(cid:")
    return n >= 20 and n * 7 > len(text) * 0.2


def _glyph_gaps(text: str) -> bool:
    """Glif eşlemesiyle kurtarılan metinde (app/glyphs.py) tanınmayan karakter çok: şablon güvenle öğrenilemez."""
    n = text.count("\ufffd")
    return n >= 20 and n > 0.05 * sum(not c.isspace() for c in text)


def trustworthy(st: Statement) -> bool:
    return ai_check(st) is None


def _keywords(text: str) -> list[str]:
    head = _low(text[:4000])
    return [k for k in KEYWORDS if k in head][:4]


def _credit_modes(text: str, all_modes: bool = False) -> tuple[str, ...]:
    """Denenecek alacak (ödeme / iade) işareti biçimleri; "(-)" yalnız metinde geçiyorsa (deneme sayısı artmasın)."""
    modes = ("minus", "plus", "trail", "trailplus", "words") if all_modes else ("minus", "plus")
    return modes + ("paren",) if "(-)" in text or "( - )" in text else modes


def learn(text: str, bank: str) -> tuple[dict, Statement] | None:
    """Denklemi tutan kuralı ara. (spec, Statement) ya da None."""
    best = None
    for (order, sep), pat in _DATES.items():
        if len(re.findall(r"(?m)^\s*" + pat, text)) < 3:
            continue
        for fmt in ("tr", "us"):
            for idx in (-1, 0, -2, 1):
                for credit in _credit_modes(text):
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
            elif set(label) <= {"tl", "try"} and j <= 5:  # değer satırında yalnız para birimi: "… 3.923,90 TL 1.569,56"
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


def _hint_bank(name: str) -> str:
    low = _low(name or "")
    return next((bank for pat, bank in BANK_HINTS if re.search(pat, low)), "")


def _ai_bank(text: str, ai_bank: str) -> str:
    return _bank_check(text, ai_bank)[0]


def _bank_check(text: str, ai_bank: str) -> tuple[str, str | None]:
    """(banka, neden kodu). Şablon yalnız metinden tanınan bankayla kaydedilir: sonraki ekstre matches() ile aynı
    yoldan (bank_of) eşleşmeli — banka adı yalnız logoda olan belgede yapay zekânın yazdığı adla kaydedilen şablon hiç
    kullanılamazdı. Metindeki banka yapay zekânınkiyle çelişirse (işyeri adında başka bankanın adı) öğrenilmez."""
    from app.templates import IBAN_BANKS

    known = set(IBAN_BANKS.values())
    b, ai_b = bank_of(text), _hint_bank(ai_bank)
    if not b:
        return "", "bank_not_in_text"
    if b not in known:
        return "", "bank_unknown"
    if ai_b and ai_b != b:
        return "", "bank_mismatch"
    return b, None


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


def _rel(p: int, n: int) -> int:
    return p if p <= 3 else p - n


def _amount_at(text: str, d: dict, fmt: str, fx: bool, ai: Statement, pts: bool = False) -> tuple[int, dict, dict]:
    """Satırdaki değer sayısına göre tutarın sırası: yapay zekânın işlemleri satırlarda aranır. (amount_idx, amount_at,
    inst_at): taksit satırlarında tutar başka sütunda olabilir (işlem toplamı + bu ayki taksit + puan; taksitsiz satırda
    tutar + puan — değer sayısı aynı, sıra farklı). Tutar satırda birden çok sütunda aynıysa ("1.000,00 TL 1.000,00":
    işlem tutarı ve TL karşılığı) oy sütunlara bölünür: sırayı tek anlamlı satırlar belirler."""
    want: dict[date, list[float]] = {}
    for t in ai.transactions:
        want.setdefault(t.txn_date, []).append(round(abs(t.amount), 2))
    dre = _date_re(d)
    pos: dict[bool, dict[int, Counter]] = {False: {}, True: {}}
    for ln in text.splitlines():
        m = dre.match(ln)
        if not m or (dt := _date(m)) not in want:
            continue
        rest = ln[m.end():]
        vals = _values(rest, _tokens(rest, fmt, fx), pts)
        inst = _looks_installment(_desc(rest, fmt, fx, ""))
        nums = [round(_num(v.group(2), fmt), 2) for v in vals]
        x = next((x for x in nums if x in want[dt]), None)
        if x is None:
            continue
        want[dt].remove(x)
        hits = [i for i, y in enumerate(nums) if y == x]
        for i in hits:
            pos[inst].setdefault(len(vals), Counter())[i] += 1 / len(hits)
    base = pos[False] or pos[True]
    if not base:
        return -1, {}, {}
    best = {n: c.most_common(1)[0][0] for n, c in base.items()}
    common = max(base, key=lambda n: sum(base[n].values()))
    p = best[common]
    idx = -1 if p == common - 1 else p
    if not -3 <= idx <= 3:
        idx = p - common
    at = {}
    for n, p in best.items():
        here = idx if idx >= 0 else n + idx
        if p != here and 1 <= n <= 8:
            at[str(n)] = _rel(p, n)
    inst_at = {}
    if pos[False]:
        for n, c in pos[True].items():
            p = c.most_common(1)[0][0]
            rule = at.get(str(n), idx)
            if p != (rule if rule >= 0 else n + rule) and 1 <= n <= 8:
                # bu değer sayısında taksitsiz satır yoksa genel kural yeter; çakışıyorsa taksit satırına ayrı kural
                (inst_at if n in pos[False] else at)[str(n)] = _rel(p, n)
    return idx, at, inst_at


def _card_key(card: str | None) -> str | None:
    return card if re.fullmatch(r"\d{4}\*\*\*\d{4}", card or "") else None


def _txn_keys(txns, with_card: bool) -> Counter:
    return Counter((t.txn_date, money.kurus(t.amount), t.card if with_card else None) for t in txns)


def _without_summary_lines(txns: list[Transaction], ai: Statement) -> list[Transaction]:
    """Yapay zekâ ödeme / faiz satırını yalnız özete yazdıysa (_summary_only): şablonun yapay zekâda olmayan satırları
    TAM O kalemler mi (alacak satırları toplamı = eksik ödeme, borç satırları faiz/ücret ve toplamı = eksik faiz,
    kuruşu kuruşuna) — öyleyse karşılaştırmada onlar çıkarılır (şablon onları da okur, kendi denklemi tutmalı).
    Değilse liste olduğu gibi (fark "lines" olarak görünür)."""
    extra = _summary_only(ai)
    if not extra:
        return txns
    want = _txn_keys(ai.transactions, False)
    keep, rest = [], []
    for t in txns:
        k = (t.txn_date, money.kurus(t.amount), None)
        if want[k] > 0:
            want[k] -= 1
            keep.append(t)
        else:
            rest.append(t)
    credit = sum(t.amount for t in rest if t.amount < 0)
    debit = sum(t.amount for t in rest if t.amount > 0)
    if rest and not +want and abs(credit - extra.get(PAYMENT, 0.0)) <= 0.05 \
            and abs(debit - extra.get(FEE, 0.0)) <= 0.05 and all(t.txn_type == FEE for t in rest if t.amount > 0):
        return keep
    return txns


_INST_SHIFT_MAX = 3  # yapay zekânın tarihini farklı yazdığı en çok kaç taksit satırı hoş görülür


def _inst_shifted(txns: list[Transaction], ai: Statement) -> list[Transaction] | None:
    """Şablonun yapay zekâyla eşleşmeyen satırları YALNIZ taksit satırlarının tarihinde mi ayrılıyor: belgede taksit
    satırı ilk işlem (alışveriş) tarihini taşır, yapay zekâ bazen başka bir tarih (yansıma / kesim sonrası) yazar.
    Her artık şablon satırı, aynı tutarlı (kuruş, işaretli) artık bir yapay zekâ taksit satırına birebir eşleşmeli
    (kart sonra _mismatch'te denetlenir), iki taraf da taksit olmalı, en çok _INST_SHIFT_MAX satır. Eşleşirse tarihleri yapay zekânınki
    yapılmış liste (karşılaştırma için; şablonun kendi okuması / denklemi / tarih tutarlılığı ayrıca denetlenir);
    yoksa None."""
    from dataclasses import replace

    want = _txn_keys(ai.transactions, False)
    rest_t, keep = [], []
    for t in txns:
        k = (t.txn_date, money.kurus(t.amount), None)
        if want[k] > 0:
            want[k] -= 1
            keep.append((t, None))
        else:
            rest_t.append(t)
            keep.append((t, len(rest_t) - 1))
    if not rest_t or len(rest_t) > _INST_SHIFT_MAX:
        return None
    rest_a = []
    for a in ai.transactions:
        k = (a.txn_date, money.kurus(a.amount), None)
        if want[k] > 0:  # yapay zekânın şablonda karşılığı olmayan satırı
            want[k] -= 1
            rest_a.append(a)
    if len(rest_a) != len(rest_t):
        return None
    pool = list(rest_a)
    dates = []
    for t in rest_t:
        a = next((a for a in pool if money.kurus(a.amount) == money.kurus(t.amount)
                  and (a.installment_no is not None or _looks_installment(a.description or ""))
                  and (t.installment_no is not None or _looks_installment(t.description))), None)
        if a is None:
            return None
        pool.remove(a)
        dates.append(a.txn_date)
    return [t if j is None else replace(t, txn_date=dates[j]) for t, j in keep]


def _mismatch(res: tuple[Statement, bool] | None, ai: Statement) -> str | None:
    """Şablon sonucu yapay zekânınkini AYNEN veriyor mu; vermiyorsa ilk fark (neden kodu). İşlemler tek tek (tarih,
    kuruş, ek kart varsa kart) karşılaştırılır; başlık alanları; ekstre denklemi; tarih tutarlılığı. Tek istisna
    (_inst_shifted): yapay zekâ birkaç taksit satırına belgedekinden başka tarih yazmış — tutar, kart, satır sayısı
    yine birebir; tarih tutarlılığı şablonun KENDİ tarihleriyle denetlenir."""
    if not res:
        return "header"
    st, ok = res
    if money.ne(st.total_debt, ai.total_debt) or st.due_date != ai.due_date or st.period_end != ai.period_end:
        return "header_values"
    txns = _without_summary_lines(st.transactions, ai)
    if len(txns) == len(ai.transactions) and _txn_keys(txns, False) != _txn_keys(ai.transactions, False):
        txns = _inst_shifted(txns, ai) or txns
    if len(txns) != len(ai.transactions):
        return "lines"
    if _txn_keys(txns, False) != _txn_keys(ai.transactions, False):
        return "amounts"
    cards = all(_card_key(t.card) for t in ai.transactions)
    if (_card_key(ai.card_no) and st.card_no != ai.card_no) or (cards and _txn_keys(txns, True)
                                                                 != _txn_keys(ai.transactions, True)):
        return "card"
    if not ok:
        return "equation"
    if not plausible(st):
        return "dates"
    for a, b in ((st.min_payment, ai.min_payment), (st.credit_limit, ai.credit_limit),
                 (st.available_limit, ai.available_limit)):  # yanlış değer yazmaktansa boş kalsın
        if a is not None and (b is None or money.ne(a, b)):
            return "fields"
    return None


def _same(st: Statement, ai: Statement) -> bool:
    return _mismatch((st, True), ai) is None


_STAGES = ("header", "header_values", "lines", "amounts", "card", "equation", "dates", "fields")


def _extra_stop(text: str, spec: dict, bank: str, ai: Statement) -> list[str] | None:
    """Şablon yapay zekânın işlem saymadığı tarihli satırlar okuyorsa (gelecek dönem taksit tablosu, puan tablosu) ve
    bunların hepsi listenin SONUNDAYSA: hemen üstlerindeki başlık (yalnız SECTION_WORDS kelimeleri) bölüm sonu olur."""
    trace: list[int] = []
    res = apply(text, spec, bank, trace)
    if not res:
        return None
    want = _txn_keys(ai.transactions, False)
    gaps = _summary_only(ai)  # yapay zekânın yalnız özete yazdığı ödeme / faiz satırları fazlalık sayılmaz
    extra = []
    for t, i in zip(res[0].transactions, trace):
        k = (t.txn_date, money.kurus(t.amount), None)
        if want[k] > 0:
            want[k] -= 1
            if extra:
                return None  # yapay zekânın işlemi fazlalıklardan sonra: bölüm sonu değil
        elif (t.amount < 0 and PAYMENT in gaps) or (t.txn_type == FEE and FEE in gaps):
            continue
        else:
            extra.append(i)
    if not extra:
        return None
    lines = text.splitlines()
    last_txn = max((i for i in trace if i < extra[0]), default=-1)
    for i in range(extra[0] - 1, last_txn, -1):
        low = _fold(lines[i])
        ws = _words(low)
        if not ws or re.search(r"\d", low):
            continue
        if len(ws) <= 8 and all(w in SECTION_WORDS for w in ws):
            return [" ".join(ws)]
    return None


def _bare_ai_lines(lines: list[str], d: dict, fmt: str, ai: Statement) -> bool:
    """Açıklamasız (yalnız tarih + değer), tek tarihli satırda yapay zekânın o günkü bir tutarı var mı."""
    dre = _date_re(d)
    want: dict[date, set[int]] = {}
    for t in ai.transactions:
        want.setdefault(t.txn_date, set()).add(money.kurus(abs(t.amount)))
    for ln in lines:
        m = dre.match(ln)
        if not m or (dt := _date(m)) not in want or len(_ANY_DATE.findall(ln)) != 1:
            continue
        rest = ln[m.end():]
        if not _bare(_desc(rest, fmt, True, "")):
            continue
        if any(money.kurus(_num(v.group(2), fmt)) in want[dt] for v, _ in _tokens(rest, fmt, False)):
            return True
    return False


def _only_summary_rows(st: Statement, ai: Statement) -> bool:
    """Şablonun yapay zekâda olmayan satırları YALNIZ yapay zekânın özete yazıp listeye koymadığı kalemler mi: ödeme
    satırları toplamı eksik ödemeye kuruşu kuruşuna eşit, kalanı faiz/ücret satırı ve toplamı eksik faizi aşmıyor.
    Şablon yapay zekânın atladığı bir harcamayı okuyorsa (özet onu faiz sanmış olabilir) düzeltme yapılmaz."""
    gaps = _summary_only(ai)
    want = _txn_keys(ai.transactions, False)
    rest = []
    for t in st.transactions:
        k = (t.txn_date, money.kurus(t.amount), None)
        if want[k] > 0:
            want[k] -= 1
        else:
            rest.append(t)
    if any(t.txn_type not in (PAYMENT, FEE) for t in rest):
        return False
    paid = sum(t.amount for t in rest if t.txn_type == PAYMENT)
    fees = sum(t.amount for t in rest if t.txn_type == FEE)
    return abs(paid - gaps.get(PAYMENT, 0.0)) <= 0.05 and fees <= gaps.get(FEE, 0.0) + 0.05


def _minus_vals(lines: list[str], d: dict, fmt: str) -> bool:
    """İşlem satırlarından birinde sonu "-" işaretli değer var mı ("50,00 -")."""
    dre = _date_re(d)
    for ln in lines:
        if m := dre.match(ln):
            rest = ln[m.end():]
            if any(_TRAIL_MINUS.match(rest[v.end():]) for v in _MONEY[fmt].finditer(rest)):
                return True
    return False


def explain_from_ai(text: str, ai: Statement) -> tuple[tuple[dict, Statement] | None, str]:
    """Yapay zekâ okumasından şablon (kaydetmeden) ve neden kodu: değerler metinde aranır, satır deseni ve etiketler
    çıkarılır; aynı metne uygulanınca yapay zekânın sonucunu aynen vermeli ve ekstre denklemi tutmalı.
    Yapay zekânın işlemleri denklemi tutmuyorsa metinden denklemi tutan okuma aranır (_text_fix): bulunursa şablon
    düzeltilmiş okumaya göre öğrenilir (kod ok_fixed; döndürülen Statement şablonun okuması). Denklem yalnız özet
    alanlarıyla tutuyorsa (_summary_only) ve şablon yapay zekâyı aynen veremiyorsa da denklemi kendi tutan, aynı belgeyi
    okuyan aday (_merged) aynı yoldan düzeltir (Garanti: yapay zekâ harcanan Bonus satırını iade yazmış).
    Kodlar: ok | ok_fixed | no_text | text_cid | text_cid_no_ocr | text_glyphs | too_long | ai_few_txns | ai_equation |
    ai_dates | bank_not_in_text | bank_unknown | bank_mismatch | date_format | header | header_values | lines | amounts |
    card | equation | dates | fields."""
    if not text:
        return None, "no_text"
    if len(text) > 400_000:
        return None, "too_long"
    if _cid_text(text):
        # app/glyphs.py kurtaramadı: Type3 değil / OCR'la da olmadı (text_cid) ya da sunucuda Tesseract yok
        from app import glyphs

        return None, "text_cid" if glyphs.ocr_available() else "text_cid_no_ocr"
    if _glyph_gaps(text):
        return None, "text_glyphs"
    if reason := ai_check(ai):
        if reason == "ai_equation":
            bank, why = _bank_check(text, ai.bank)
            if not why and (fix := _text_fix(text, ai, bank)):
                built, _ = explain_from_ai(text, fix[0])  # düzeltilmiş okuma denklemi tutar: özyineleme bir kez
                if built:
                    return built, "ok_fixed"
                spec, st = fix[1]  # etiketsiz kural (canlı okumada read() de bunu kaydeder)
                if spec.get("bank") and is_safe(spec):
                    return (spec, st), "ok_fixed"
        return None, reason
    bank, reason = _bank_check(text, ai.bank)
    if reason:
        return None, reason
    dates = _date_formats(text, {t.txn_date for t in ai.transactions})
    if not dates:
        return None, "date_format"
    lines = text.splitlines()
    # ek kartlı ekstre: işlemler bölüm başlığındaki kart numarasıyla kartlara ayrılır (kart no metinde yoksa olmaz)
    multi = len({_card_key(t.card) or t.card for t in ai.transactions}) > 1
    # dövizli işlem varsa önce döviz sütununu ayıran kural denenir (orig_amount da okunsun)
    fx_opts = (True, False) if any(t.orig_currency for t in ai.transactions) or _CUR.search(text) else (False,)
    worst = -1
    summary = bool(_summary_only(ai))  # yapay zekâ okuması denklemi yalnız özet alanlarıyla tutuyor
    best_fix: tuple[int, Statement] | None = None
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
        # isteğe bağlı alan (asgari, limitler) şablonla yapay zekânınkinden farklı / yapay zekâda yoksa okunmaz
        h2 = header(text, fmt, labels)
        skip = [k for k in ("min", "limit", "available") if h2[k] is not None
                and (getattr(ai, _AI_FIELD[k]) is None or money.ne(h2[k], getattr(ai, _AI_FIELD[k])))]
        prev0 = not ai.previous_balance and h2["prev"] is None and "equation" not in h2
        for d in dates:
            # açıklaması üst / alt satıra taşmış işlem satırı varsa (yapay zekânın tutarı açıklamasız tarih satırında)
            wraps = (False, True) if _bare_ai_lines(lines, d, fmt, ai) else (False,)
            # işlem satırında sonu "-" işaretli değer varsa: puan sütunu olabilir (Garanti harcanan Bonus)
            ptss = (False, True) if _minus_vals(lines, d, fmt) else (False,)
            for fx, wrap, pts in ((fx, wrap, pts) for pts in ptss for wrap in wraps for fx in fx_opts):
                idx, at, inst_at = _amount_at(text, d, fmt, fx, ai, pts)
                for credit in _credit_modes(text, all_modes=True):
                    if pts and credit == "trail":
                        continue  # sondaki "-" ya alacak işaretidir ya puan sütunu
                    spec = {"v": 1, "kind": "card", "bank": bank, "keywords": _keywords(text), "date": d, "num": fmt,
                            "amount_idx": idx, "credit": credit}
                    spec.update({k: v for k, v in (("amount_at", at), ("inst_at", inst_at), ("fx", fx),
                                                   ("labels", labels), ("skip", skip), ("prev0", prev0),
                                                   ("cards", "section" if multi else None), ("wrap", wrap),
                                                   ("minus_pts", pts)) if v})
                    if not is_safe(spec):
                        continue
                    res = apply(text, spec, bank)
                    why = _mismatch(res, ai)
                    if why in ("lines", "dates") and (stop := _extra_stop(text, spec, bank, ai)):
                        spec = {**spec, "stop": stop}
                        res = apply(text, spec, bank)
                        why = _mismatch(res, ai)
                    if why is None and is_safe(spec):
                        # yapay zekâ taksit satırı tarihini belgedekinden farklı yazmış: şablon belgeyi okur (ok_fixed)
                        shifted = _txn_keys(res[0].transactions, False) != _txn_keys(ai.transactions, False) \
                            and not _summary_only(ai)
                        return (spec, res[0]), "ok_fixed" if shifted else "ok"
                    worst = max(worst, _STAGES.index(why))
                    if summary and res and res[1] and plausible(res[0]) and _only_summary_rows(res[0], ai) \
                            and (fixed := _merged(res[0], ai)):
                        n = sum((_abs_keys(ai.transactions) & _abs_keys(res[0].transactions)).values())
                        if best_fix is None or n > best_fix[0]:
                            best_fix = (n, fixed)
    if best_fix:
        # yapay zekânın işlemleri denklemi yalnız özet alanlarıyla "tutuyordu" (_summary_only) ama şablon onu aynen
        # vermedi: denklemi KENDİ tutan, aynı belgeyi okuyan şablon okumasıyla düzeltilmiş okumadan öğren (bir kez)
        built, _ = explain_from_ai(text, best_fix[1])
        if built:
            return built, "ok_fixed"
    return None, _STAGES[worst] if worst >= 0 else "header"


def fixed_read(text: str | None, ai: Statement, spec: dict | None) -> Statement | None:
    """Yapay zekânın işlemleri ekstre denklemini kuruşu kuruşuna tutmuyor ama aynı belgeye uygulanan şablon (spec:
    learn_from_ai'nin öğrendiği / bildiği) tutuyorsa düzeltilmiş okuma (merge_fix: eksik ödeme / faiz satırı eklenir,
    ters işaret düzelir); yoksa None. Yalnız yeni yüklemede kullanılır — kayıtlı ekstre kendiliğinden değişmez. Hata
    okumayı bozmaz (None)."""
    try:
        return _fixed_read(text, ai, spec)
    except Exception:  # noqa: BLE001
        log.warning("şablonla düzeltme denenemedi", exc_info=True)
        return None


def _fixed_read(text: str | None, ai: Statement, spec: dict | None) -> Statement | None:
    d = _txn_equation_diff(ai)
    if not text or not spec or d is None or abs(d) <= 0.05:
        return None
    res = apply(text, spec, spec.get("bank") or bank_of(text))
    if not res or not res[1] or not plausible(res[0]):
        return None
    st = res[0]
    if not _same_doc(st, ai):
        return None
    fixed = merge_fix(ai, st)
    d = _txn_equation_diff(fixed)
    return fixed if d is not None and abs(d) <= 0.05 else None


def build_from_ai(text: str, ai: Statement) -> tuple[dict, Statement] | None:
    return explain_from_ai(text, ai)[0]


def learn_from_ai(text: str | None, ai: Statement, record: bool = True) -> dict | None:
    """Yapay zekâ okumasından kart şablonu öğren (ya da devre dışı kalmış aynı kuralı yeniden aç) ve paylaş.
    Hata okumayı bozmaz. Öğrenilen şablonu döndürür. Her deneme (banka, sonuç, neden kodu) sunucu geneli sayaca ve
    günlüğe yazılır — belgeden içerik yazılmaz (app/templates.py record_attempt)."""
    from app import templates

    bank = _hint_bank(ai.bank) or bank_of(text or "") or "?"
    try:
        built, reason = explain_from_ai(text or "", ai)
        spec = None
        if built:
            spec = built[0]
            if templates.save(spec):
                templates.share_async(spec)
                reason = "learned"
            elif templates._revive(spec):
                reason = "revived"
            else:
                reason = "known"
    except Exception:  # noqa: BLE001
        log.warning("kart şablonu öğrenilemedi", exc_info=True)
        spec, reason = None, "error"
    log.info("kart şablonu öğrenme: banka=%s sonuç=%s", bank, reason)
    if record:
        try:
            templates.record_attempt(bank, spec is not None, reason)
        except Exception:  # noqa: BLE001 - sayaç okumayı bozmaz
            log.warning("öğrenme sayacı yazılamadı", exc_info=True)
    return spec


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

    def small_map(at) -> bool:
        return isinstance(at, dict) and 0 < len(at) <= 8 and all(
            k in {str(n) for n in range(1, 9)} and type(v) is int and -3 <= v <= 3 for k, v in at.items())

    try:
        if set(spec) - {"v", "kind", "bank", "keywords", "date", "num", "amount_idx", "credit", "amount_at", "fx",
                        "labels", "inst_at", "skip", "prev0", "cards", "stop", "wrap", "minus_pts"}:
            return False
        if spec.get("kind") != "card" or spec.get("bank") not in set(IBAN_BANKS.values()) | {""}:
            return False
        if not all(k in KEYWORDS for k in spec.get("keywords", [])):
            return False
        d = spec["date"]
        if (d["order"], d["sep"]) not in _DATES or set(d) - {"order", "sep", "year"} or d.get("year", 4) not in (2, 4):
            return False
        if any(k in spec and not small_map(spec[k]) for k in ("amount_at", "inst_at")):
            return False
        if any(k in spec and spec[k] is not True for k in ("fx", "prev0", "wrap", "minus_pts")):
            return False
        if "cards" in spec and spec["cards"] != "section":
            return False
        if "skip" in spec and not (isinstance(spec["skip"], list) and 0 < len(spec["skip"]) <= 3
                                   and all(k in ("min", "limit", "available") for k in spec["skip"])):
            return False
        if "stop" in spec:
            st = spec["stop"]
            if not isinstance(st, list) or not 1 <= len(st) <= 2 or not all(
                    isinstance(x, str) and x == " ".join(x.split()) and 1 <= len(x.split()) <= 8
                    and all(w in SECTION_WORDS for w in x.split()) for x in st):
                return False
        if "labels" in spec and not _safe_labels(spec["labels"]):
            return False
        return spec["num"] in ("tr", "us") and spec["credit"] in ("minus", "plus", "trail", "trailplus", "words", "paren") \
            and type(spec["amount_idx"]) is int and -3 <= spec["amount_idx"] <= 3
    except (KeyError, TypeError, AttributeError):
        return False


# ---------------------------------------------------------------- taksit erken kapama (savunma)

# Bankayı arayıp taksitleri erken kapattıran kullanıcının sonraki ekstresinde kalan taksitler tek seferde gelir: ya bir
# kapama satırı ("ERKEN KAPAMA", "TAKSİT KAPAMA", "KALAN TAKSİT TUTARI") ya da kalan taksitler aynı ekstrede tek tek
# ("4/6", "5/6", "6/6"). İkisi de tek çekimdir: kalan taksit (installment_left_amount) yazılırsa Planlama ve Özet aynı
# tutarı sonraki aylara yeniden yayar. "Kalan taksit: 3" gibi sayı bildiren satır kapama değildir (yalnız tutar/çoğul).
_CLOSE_LINE = re.compile(r"erken\s*(?:kapa(?:ma|tma)|odeme)|taksit\w*\s*(?:islem\w*\s*)?kapa(?:ma|tma)|toplu\s*kapa(?:ma|tma)"
                         r"|kalan\s*taksit(?:ler|\s*tutar)")
_INST_MARK = re.compile(r"(?<![\d/.])\d{1,2}\s*/\s*\d{1,2}(?![\d/])|\d{1,2}\.\s*(?:taksit|iade)|son\s*taksit"
                        r"|\d{1,2}\s*[x×]\s*[\d.,]+|taksit\w*")


def is_close_line(desc: str) -> bool:
    """Açıklama taksit erken kapama satırı mı ("ERKEN KAPAMA", "TAKSİT KAPAMA", "KALAN TAKSİTLER", "KALAN TAKSİT TUTARI")."""
    return bool(_CLOSE_LINE.search(_fold(desc or "")))


def installment_base(desc: str) -> str:
    """Taksit işaretleri ve sayılar atılmış açıklama ("ÖRNEK MOBİLYA 3/6 TAKSİT" → "ornekmobilya"): aynı alışverişin
    farklı ekstrelerdeki ya da aynı ekstrede tek tek listelenen taksitleri aynı anahtarı taşır."""
    s = _INST_MARK.sub(" ", _fold(desc or ""))
    return "".join(re.sub(r"[\d.,/:*-]+", " ", s).split())[:24]


def close_installments(st: Statement) -> int:
    """Ekstrede erken kapanan taksitler sonraki aylara yayılmasın (yerinde düzeltir; düzeltilen satır sayısı).
    - açıklamasında kapama ifadesi geçen satır tek çekimdir: taksit bilgisi silinir;
    - aynı alışverişin (aynı gün, aynı açıklama, aynı taksit sayısı) son taksite kadar ardışık birden çok taksiti aynı
      ekstredeyse hepsi bu ekstrede ödenir: kalan taksit tutarı silinir (sıra bilgisi "4/6" görünmeye devam eder)."""
    n = 0
    for t in st.transactions:
        if t.txn_type != PAYMENT and is_close_line(t.description) and (
                t.installment_no is not None or t.installments_left is not None or t.installment_left_amount is not None):
            t.installment_no = t.installments_left = t.installment_left_amount = None
            n += 1
    runs: dict[tuple, list[Transaction]] = {}
    for t in st.transactions:
        if t.txn_type in (PURCHASE, REFUND) and t.installment_no is not None and t.installments_left is not None:
            runs.setdefault((t.txn_date, installment_base(t.description), t.installment_no + t.installments_left,
                             t.txn_type), []).append(t)
    for (_d, base, total, _k), ts in runs.items():
        nos = sorted(t.installment_no for t in ts)
        if not base or len(ts) < 2 or nos[-1] != total or nos != list(range(nos[0], total + 1)):
            continue
        for t in ts:
            if t.installment_left_amount is not None:
                t.installment_left_amount = None
                n += 1
    return n


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
