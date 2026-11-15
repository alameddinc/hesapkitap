"""Fişin vergi bilgisi: KDV toplamı, oran oran KDV dökümü, satıcının vergi numarası (VKN) ve vergi dairesi.

Yapay zekâ okuması (receipt.DOC_PROMPT) bu alanları verir; boş bıraktıysa ya da okuma yerelse (Tesseract, PDF metni,
telefondaki OCR metni) fiş metnindeki "TOPKDV", "%1 *18,25", "V.NO: …", "… V.D." satırlarından kural ile doldurulur.

tax_no: 10 haneli VKN (kontrol hanesi tutan) ya da şahıs firmasının 11 haneli T.C. kimlik numarası (TCKN; resmî
algoritma: ilk hane 0 değil, 10. ve 11. hane kontrol basamağı). Önceden TCKN saklanmıyordu: şahıs marketinin fişinde
vergi no boş kalıyor, kullanıcı elle de giremiyordu (15.11.2026). TCKN kişisel veridir: kullanıcının kendi kaydında ve
muhasebeci fiş listesinde tam değer durur; günlükte / olay kaydında yazılmaz (app/logs.py maskeler), ekranda mask() ile.
Fiş şablonu imzası (app/receipt_templates.py) ve karekod "vkn" alanı yalnız VKN'dir (vkn()): TCKN özeti tutulmaz.
"""
import json
import re

RATES = (1, 8, 10, 18, 20)  # bugün %1 / %10 / %20; eski fişlerde %8 / %18
EXPORT_RATES = (1, 10, 20)  # muhasebeci listesinde ayrı sütun; %8 / %18 "diğer" sütununa
_AMOUNT = r"(\d{1,3}(?:[.\s]\d{3})*,\d{2}|\d+[.,]\d{2})"


def vkn_valid(v: str) -> bool:
    """Vergi kimlik numarası kontrol hanesi (Gelir İdaresi algoritması)."""
    if not re.fullmatch(r"\d{10}", v or ""):
        return False
    d = [int(c) for c in v]
    s = 0
    for i in range(9):
        t = (d[i] + 9 - i) % 10
        x = (t * 2 ** (9 - i)) % 9
        if t != 0 and x == 0:
            x = 9
        s += x
    return (10 - s % 10) % 10 == d[9]


def tckn_valid(v: str) -> bool:
    """T.C. kimlik numarası kontrol basamakları (Nüfus ve Vatandaşlık İşleri algoritması): 11 hane, ilk hane 0 değil;
    10. hane = (tek sıradakiler × 7 − çift sıradakiler) mod 10; 11. hane = ilk 10 hanenin toplamı mod 10."""
    if not re.fullmatch(r"[1-9]\d{10}", v or ""):
        return False
    d = [int(c) for c in v]
    if (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def _digits(v) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    return re.sub(r"[\s.\-/]", "", str(v))


def vkn(v) -> str | None:
    """Model/OCR/kullanıcıdan gelen vergi numarası → geçerli 10 haneli VKN ya da None (TCKN değil: şablon imzası,
    karekod "vkn"). Saklanan alan (tax_no) için tax_id()."""
    digits = _digits(v)
    return digits if digits and vkn_valid(digits) else None


def tax_id(v) -> str | None:
    """Fişin tax_no alanı: geçerli 10 haneli VKN ya da geçerli 11 haneli TCKN (şahıs firması); değilse None."""
    digits = _digits(v)
    return digits if digits and (vkn_valid(digits) or tckn_valid(digits)) else None


def is_tckn(v) -> bool:
    digits = _digits(v)
    return bool(digits) and tckn_valid(digits)


def mask(v) -> str | None:
    """Ekranda gösterim: TCKN ilk 3 + son 2 hane ("123******50"); VKN (şirket numarası) olduğu gibi."""
    if not v:
        return None
    s = str(v)
    return s[:3] + "*" * (len(s) - 5) + s[-2:] if is_tckn(s) else s


def tax_office(v) -> str | None:
    s = re.sub(r"\s+", " ", str(v or "")).strip(" .:-*")
    s = re.sub(r"\s*(V\.?\s?D\.?|VERG[İI] DA[İI]RES[İI]|VERGİ DAİRESİ)\s*$", "", s, flags=re.I).strip(" .:-")
    if len(s) < 2 or re.search(r"\d{3,}", s):
        return None
    return s[:60]


def _num(v) -> float | None:
    from app.receipt import _num as num

    return num(v)


def lines_of(v) -> list[dict]:
    """KDV dökümü [{rate, amount}]: oranlar izinli kümeden, aynı oran birleşir, oran sırasıyla. JSON metni de olur."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    by: dict[int, float] = {}
    for x in v if isinstance(v, list) else []:
        if not isinstance(x, dict):
            continue
        try:
            rate = int(round(float(str(x.get("rate")).replace("%", "").replace(",", ".").strip())))
        except (TypeError, ValueError):
            continue
        amount = _num(x.get("amount"))
        if rate in RATES and amount and amount > 0:
            by[rate] = round(by.get(rate, 0) + amount, 2)
    return [{"rate": r, "amount": by[r]} for r in sorted(by)]


def _sane(doc: dict) -> None:
    """KDV fiş toplamından küçük olmalı; döküm toplamı KDV toplamını tutmuyorsa döküm atılır."""
    total = doc.get("total")
    vt = doc.get("vat_total")
    if vt is not None and (vt <= 0 or (isinstance(total, (int, float)) and total > 0 and vt >= total)):
        doc["vat_total"] = vt = None
    lines = doc.get("vat_lines") or []
    s = round(sum(x["amount"] for x in lines), 2)
    if lines and vt is None:
        if not isinstance(total, (int, float)) or s < total:
            doc["vat_total"] = s
        else:
            doc["vat_lines"] = []
    elif lines and abs(s - vt) > 0.05:
        doc["vat_lines"] = []


def of_raw(raw: dict, total: float | None) -> dict:
    """Modelin ham JSON'undan fişin vergi alanları."""
    doc = {"total": total, "vat_total": _num(raw.get("vat_total")), "vat_lines": lines_of(raw.get("vat_lines")),
           "tax_no": tax_id(raw.get("tax_no")), "tax_office": tax_office(raw.get("tax_office"))}
    _sane(doc)
    doc.pop("total")
    return doc


def _title(s: str) -> str:
    """Türkçe baş harf büyük: "KADIKÖY" → "Kadıköy", "İSTİNYE" → "İstinye"."""
    return " ".join(w[:1] + w[1:].replace("I", "ı").replace("İ", "i").lower() for w in s.split())


def _up(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


# KDV toplamı: "TOPKDV", "TOPLAM KDV", "KDV TOPLAMI", "KDV TUTARI", "HESAPLANAN KDV" (KDV DAHİL / MATRAH değil); tek başına
# "KDV *4,55" satırı da (oransız; şahıs marketinin küçük yazar kasası böyle yazar — 15.11.2026)
_NOT_VAT = r"(?!\s*(?:DAH[İI]L|HAR[İI]Ç|MATRAH))"
_VAT_TOTAL = re.compile(r"(?:TOP\s*\.?\s*KDV|TOPLAM\s*KDV|KDV\s*TOPLAM[I]?|TOPLAM\s*VERG[İI]|KDV\s*TUTAR[I]?|HESAPLANAN\s*KDV)"
                        + _NOT_VAT)
_VAT_ONLY = re.compile(rf"^\s*KDV\s*[*:=]?\s*{_AMOUNT}\s*(?:TL)?\s*$")
# oran satırı: "%1 *18,25", "KDV %20 3,33", "KDV(%10) *4,55", "%10 KDV *4,55", "KDV %10,00 *4,55", "… 4,55 TL"
_VAT_LINE = re.compile(rf"^\s*(?:KDV\s*)?\(?\s*%\s*0?(\d{{1,2}})(?:[.,]00?)?\s*\)?\s*(?:KDV\s*)?[*:=]?\s*{_AMOUNT}"
                       r"\s*(?:TL)?\s*$")
_VKN = re.compile(r"(?:VKN\s*/\s*TCKN|VKN|V\.\s?K\.\s?N\.?|TCKN|T\.?\s?C\.?\s*(?:K[İI]ML[İI]K\s*)?N(?:O|UMARASI)|"
                  r"V\s?\.?\s?NO|VERG[İI]\s*(?:K[İI]ML[İI]K\s*)?N(?:O|UMARASI)|V\.\s?D\.?[^\n]{0,40}?NO)"
                  r"\s*[:.]?\s*(\d[\d\s]{9,13})(?!\d)")
# alıcı bloğu (e-Arşiv: "SAYIN …", "ALICI"): sonrasındaki 11 haneli numara alıcının TCKN'sidir, satıcının değil
_BUYER = re.compile(r"\b(?:SAYIN|ALICI|M[ÜU]ŞTER[İI]|MUSTERI)\b")
_OFFICE_AFTER = re.compile(r"(?:VERG[İI]\s*DA[İI]RES[İI]|V\.\s?D(?:\.|\b))\s*[:.]?\s*([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ.\- ]{1,30}?)"
                           r"(?=\s*(?:V\.?\s?NO|VKN|N[O0]\b|:|\d|$))")
_OFFICE_BEFORE = re.compile(r"([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ\- ]{1,30}?)\s*(?:VERG[İI]\s*DA[İI]RES[İI]|V\.\s?D\.)")


def from_text(text: str) -> dict:
    """Fiş metninden kural ile vergi alanları (okunamayan alan None / [])."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    vat_total = None
    vat_lines: list[dict] = []
    tax_no = office = None
    buyer = False
    for i, line in enumerate(lines):
        u = _up(line)
        if vat_total is None and (m := _VAT_ONLY.match(u)):
            vat_total = _num(m.group(1))
        elif vat_total is None and _VAT_TOTAL.search(u) and not _VAT_LINE.match(u):
            vals = re.findall(_AMOUNT, line)
            if not vals and i + 1 < len(lines) and re.fullmatch(rf"\*?\s*{_AMOUNT}\s*(?:TL)?", lines[i + 1].strip()):
                vals = re.findall(_AMOUNT, lines[i + 1])
            if vals:
                vat_total = _num(vals[-1])
        if (m := _VAT_LINE.match(u)) and int(m.group(1)) in RATES:
            vat_lines.append({"rate": int(m.group(1)), "amount": m.group(2)})
        buyer = buyer or bool(_BUYER.search(u))
        if tax_no is None and (m := _VKN.search(u)):
            t = tax_id(m.group(1))
            tax_no = None if buyer and t and is_tckn(t) else t
        for pat in (_OFFICE_AFTER, _OFFICE_BEFORE) if office is None else ():
            m = pat.search(u)
            name = m and m.group(1).strip(" .:-")
            if name and not re.match(r"V\.?\s?NO|VKN|TCKN|T\.?\s?C\.?(?:\s|$)|NO\b", name) and not re.search(r"\b(MAH|CAD|SOK|SK|NO)\b\.?$", name):
                office = tax_office(_title(name))
                if office:
                    break
    return {"vat_total": vat_total, "vat_lines": lines_of(vat_lines), "tax_no": tax_no, "tax_office": office}


def fill(doc: dict, text: str | None) -> dict:
    """Fiş sonucunda boş kalan vergi alanlarını metinden doldurur (yerinde; doc döner). Fatura sonucuna dokunmaz."""
    if doc.get("type") != "receipt":
        return doc
    for k in ("vat_total", "vat_lines", "tax_no", "tax_office"):
        doc.setdefault(k, [] if k == "vat_lines" else None)
    if not text:
        return doc
    found = from_text(text)
    for k, v in found.items():
        if v and not doc.get(k):
            doc[k] = v
    _sane(doc)
    return doc


def by_rate(lines) -> dict[int, float]:
    """Dışa aktarım: oran → KDV (yalnız 1/10/20 ayrı; %8 / %18 0 anahtarında, "diğer")."""
    out: dict[int, float] = {}
    for x in lines_of(lines):
        k = x["rate"] if x["rate"] in EXPORT_RATES else 0
        out[k] = round(out.get(k, 0) + x["amount"], 2)
    return out
