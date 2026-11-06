"""Zincir mağaza / akaryakıt fişi şablonları: aynı işyerinin fişi, telefonun çıkardığı OCR metninden yapay zekâsız okunur.

Aynı zincirin (BİM, Migros, Opet…) ya da aynı istasyonun fişleri hep aynı düzendedir. İlk fiş yapay zekâyla okunur;
sonuç doğrulanırsa (toplam metinde geçiyor, kalemler toplamı tutuyor, tarih var) fişin DÜZENİ öğrenilir; sonraki fişler
OCR metninden yerelde okunur (maliyet sıfır). Mimari fatura şablonuyla (app/bill_templates.py) ve kart ekstresi
şablonuyla (app/card_reader.py) aynı.

Şablon KOD DEĞİL, küçük bir tanımdır; içinde yalnız sabit sözlük kelimeleri, seçenekler ve özetler (hash) vardır.
Ürün adı, tutar, tarih, adres, kart no, plaka, kişi adı ŞABLONA GİRMEZ (is_safe bunu kesin denetler):

    {"v": 1, "kind": "receipt",
     "sig": {"vkn": "<16 hex>", "words": ["<12 hex>", ...]},  # imza: VKN'nin ve unvan kelimelerinin özeti (açık değil)
     "brand": "Opet",                     # yalnız sabit CHAINS listesindeki bir zincir; yoksa alan yok
     "category": "Ulaşım & Araç", "fuel": true,
     "num": "tr",                         # tr: 1.234,56 | dot: 1234.56 | mix: ikisi birden (e-arşiv fişi)
     "date": {"sep": "-", "year": 4, "label": false},          # 28-10-2026; label: "TARİH" etiketli satır
     "total": [{"label": "toplam", "off": 0, "occ": 0}],      # TOTAL_WORDS'ten etiket; off 1 = değer alt satırda
     "qty": "above",                      # miktar satırı ("2 ADET X 39,95", "27,73 LT X 97,40") ürünün üstünde | altında
     "single": false}                     # kalem satırı yok (POS slibi): tek kalem = toplam (yalnız akaryakıt)

- İmza: fişteki VKN (V.D./VKN/V.NO, 10 hane, kontrol hanesi tutan) en güçlü imzadır: zincirin bütün şubeleri aynı
  VKN'yi kullanır. Şablonda VKN'nin özeti durur (sabit tuzlu SHA-256; açık yazılmaz). VKN okunamayan fişte (OCR rakamı
  bozduysa) yedek imza: unvan kelimelerinin özetleri (zincirde sabit CHAINS anahtar kelimeleri). Metinde geçerli bir VKN
  varsa yalnız o VKN'nin şablonu uygulanır.
- Kalemler: tarih satırından toplam/KDV satırına kadar; "ad … *tutar" satırı (KDV sütunu, *·+ işaretleri, OCR'ın böldüğü
  ad/tutar satırları, barkod satırları), miktar satırı (adet / KG / LT), indirim satırı (DISCOUNT_WORDS → eksi).
- Öğrenme (learn): yapay zekâ okuması doğrulamadan geçerse seçenekler denenir; aynı metne uygulanınca yapay zekânın
  sonucunu (toplam, tarih, kalem sayısı ve tutarları) AYNEN veren ilk tanım kaydedilir.
- Okuma (read) — kabul ölçütleri: toplam, şablonun TOPLAM (ya da eşdeğer) etiketinin satırında/altında bulundu (metnin
  herhangi bir yerinde geçmesi yetmez; ARA TOPLAM / TOPLAM KDV / KDV TUTARI sayılmaz); kalemler (indirim dahil) toplamı
  ±TOL tutuyor; tarih bugünden en çok 1 gün ileri, 2 yıl geri; akaryakıtta litre × birim fiyat tutarı tutuyor. Eşleşen
  şablonlar sırayla denenir, ölçütü geçen ilk sonuç alınır; hiçbiri geçmezse None (çağıran yapay zekâya gider), denenenlerin
  hata sayısı artar, art arda MAX_FAILS → devre dışı. Yapay zekâ aynı kuralı yeniden öğretirse açılır.
- Bir imzada birden çok düzen: zincirin şubelerinde farklı yazar kasa yazılımı olabilir. Yeni düzen eskisini silmez,
  yanına eklenir; imza başına en çok MAX_PER_SIG (en az kullanılan düşer).

Saklama: SUNUCU GENELİ (system veritabanı, receipt_templates tablosu): bir kullanıcının ilk fişi aynı işyerinin bütün
fişlerine şablon olur; şablonda kişisel veri yoktur, okunan değerler her zaman o anki fişin metninden gelir.
Paylaşım: yalnız CHAINS'teki bir zincirin VKN imzalı şablonu topluluğa gönderilir (app/templates.py share_async /
sync_community; shareable). Okuma hakkı (app/doc_reads.py) çağıranda aynen düşer; yapay zekâ maliyet kaydı yazılmaz.
Yönetici: GET /api/admin/receipt-templates (overview: isabet, hata, son kullanım, durum; ad yalnız bilinen zincirse).
Ölçüm (04.11.2026, 40 gerçek fiş, repo dışı): öğrenmeden sonra gelen aynı-işyeri fişinin %55'i şablonla okundu, hepsi
yapay zekâyla aynı (toplam, kalem, tarih); kalanı OCR bozması yüzünden kabul ölçütüne takılıp yapay zekâya düştü.
"""
import hashlib
import json
import logging
import re
from datetime import date, timedelta

from app import money

log = logging.getLogger("hesapkitap.receipt_templates")

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipt_templates (
    id INTEGER PRIMARY KEY,
    sig TEXT NOT NULL,                      -- 'vkn:<özet>' ya da 'w:<özet> <özet>'; bir imzada birden çok düzen olabilir
    spec TEXT NOT NULL,                     -- JSON (yukarıdaki biçim)
    source TEXT NOT NULL DEFAULT 'learned', -- learned (bu sunucuda) | community (topluluktan)
    uses INTEGER NOT NULL DEFAULT 0,
    fails INTEGER NOT NULL DEFAULT 0,       -- art arda kabul edilmeyen okuma
    disabled INTEGER NOT NULL DEFAULT 0,
    last_used TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (sig, spec)
);
"""
MAX_FAILS = 2
MAX_PER_SIG = 5  # imza başına en çok bu kadar düzen (şubelerde farklı yazar kasa yazılımı); en az kullanılan düşer
TOL = 0.05  # kalem toplamı ile fiş toplamı arasındaki pay (TL)
_PEPPER = "hesapkitap-fis-sablonu-v1:"  # sabit tuz: aynı VKN her sunucuda aynı özet (topluluk şablonu eşleşsin)

# etiket sözlüğü (katlanmış): toplam etiketi yalnız bunlardan oluşur
TOTAL_WORDS = frozenset("toplam toplami genel tutar tutari satis odenecek net top tl emv kdv dahil".split())
_TOTAL_KEY = {"toplam", "toplami", "tutar", "tutari", "odenecek", "genel", "satis"}  # etikette en az biri (POS slibi: "SATIŞ")
_BLOCK = {"kdv", "topkdv", "ara", "indirim", "ind", "iskonto", "vergi"}  # etiketin önünde bunlar varsa başka toplam
# kalem bölgesini bitiren satır başı kelimeleri
_STOP = ("topkdv", "kdv", "toplam", "top", "ara", "genel", "kdvli", "matrah")
DISCOUNT_WORDS = frozenset("indirim indirimi ind iskonto kampanya promosyon kupon puan".split())
_LEGAL = {"a", "s", "as", "ltd", "sti", "san", "tic", "ve", "anonim", "sirketi", "sirket", "limited", "ins", "paz",
          "gida", "akaryakit", "urunleri", "petrol", "market", "marketler", "magazalari", "magazacilik", "otomotiv",
          "turizm", "restoran", "cafe", "sube", "ticaret", "sanayi", "dis", "ith", "ihr", "nak", "tur", "org", "hiz",
          "hizmetleri", "isletmeleri", "otoyol", "mah", "cad", "sok", "no", "istanbul", "ankara", "izmir"}
# bilinen zincirler: (görünen ad, metinde kelime olarak geçen sabit anahtar kelimeler, kategori)
CHAINS = [
    ("BİM", ["bim"], "Market & Gıda"), ("A101", ["a101"], "Market & Gıda"), ("Migros", ["migros"], "Market & Gıda"),
    ("ŞOK", ["sok", "marketler"], "Market & Gıda"), ("CarrefourSA", ["carrefoursa"], "Market & Gıda"),
    ("Macrocenter", ["macrocenter"], "Market & Gıda"), ("Metro", ["metro", "grossmarket"], "Market & Gıda"),
    ("Hakmar", ["hakmar"], "Market & Gıda"), ("Tarım Kredi", ["tarim", "kredi", "kooperatif"], "Market & Gıda"),
    ("Bizim Toptan", ["bizim", "toptan"], "Market & Gıda"), ("Mopaş", ["mopas"], "Market & Gıda"),
    ("File", ["file", "market"], "Market & Gıda"), ("Happy Center", ["happy", "center"], "Market & Gıda"),
    ("Opet", ["opet"], "Ulaşım & Araç"), ("Shell", ["shell"], "Ulaşım & Araç"),
    ("Petrol Ofisi", ["petrol", "ofisi"], "Ulaşım & Araç"), ("BP", ["bp"], "Ulaşım & Araç"),
    ("TotalEnergies", ["totalenergies"], "Ulaşım & Araç"), ("Aytemiz", ["aytemiz"], "Ulaşım & Araç"),
    ("Lukoil", ["lukoil"], "Ulaşım & Araç"), ("Gratis", ["gratis"], None), ("Watsons", ["watsons"], None),
    ("Rossmann", ["rossmann"], None), ("Teknosa", ["teknosa"], None), ("MediaMarkt", ["mediamarkt"], None),
    ("LC Waikiki", ["waikiki"], None), ("DeFacto", ["defacto"], None), ("Koton", ["koton"], None),
    ("Decathlon", ["decathlon"], None), ("IKEA", ["ikea"], None), ("Koçtaş", ["koctas"], None),
    ("Starbucks", ["starbucks"], None), ("Burger King", ["burger", "king"], None),
    ("Kahve Dünyası", ["kahve", "dunyasi"], None), ("Simit Sarayı", ["simit", "sarayi"], None),
]
_BRANDS = {b for b, _, _ in CHAINS}


def chain_of(*names: str | None) -> str | None:
    """İşyeri adı (ya da unvanı) bilinen bir zincir mi (CHAINS): görünen adı, değilse None. Eşleşme: adın bitişik yazılışı
    zincirin adı ("ŞOK" = "Şok", "KAHVE DÜNYASI"), ya da anahtar kelimeler adda kelime olarak geçiyor (tek anahtarlıda o
    kelime: "MIGROS TICARET"; çok anahtarlıda ilk ikisi: "TARIM KREDI KOOPERATIF"). Fiyat karşılaştırması (app/db.py
    products, app/community_prices.py) bunu kullanır."""
    for name in names:
        if not name:
            continue
        words = re.findall(r"[a-z0-9]+", fold(name))
        compact, ws = "".join(words), set(words)
        for brand, keys, _ in CHAINS:
            if compact == "".join(re.findall(r"[a-z0-9]+", fold(brand))) or \
                    (all(k in ws for k in keys[:2]) if len(keys) > 1 else keys[0] in ws):
                return brand
    return None
_FUEL = re.compile(r"(?<![a-z])(akaryakit|motorin|benzin|kursunsuz|dizel|diesel|lpg|otogaz|epdk|pompa)(?![a-z])")
FUEL_ITEM = "Akaryakıt"  # POS slibinde (kalem satırı yok) tek kalemin adı

_FOLD = str.maketrans("şığüöçâîûøŞĞÜÖÇÂÎÛØ", "siguocaiuoSGUOCAIUO")


def _today() -> date:
    return date.today()


def fold(s: str) -> str:
    """Küçük harf, Türkçe harfsiz (OCR'ın düşürdüğü harfler de aynı yazılsın). Uzunluk korunur."""
    return s.replace("İ", "i").replace("I", "ı").translate(_FOLD).lower()


def _h(s: str, n: int) -> str:
    return hashlib.sha256((_PEPPER + s).encode()).hexdigest()[:n]


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z]+", s)


def _lines(text: str) -> tuple[list[str], list[str]]:
    """(özgün satırlar, katlanmış satırlar); boş satırlar ve "--- PARÇA n ---" ayraçları atılır."""
    orig = [ln.strip() for ln in (text or "").splitlines()]
    orig = [ln for ln in orig if ln and not re.match(r"^-+\s*PAR[ÇC]A\b", ln, re.I)]
    return orig, [fold(ln) for ln in orig]


# ---------------------------------------------------------------- sayılar, tarih, VKN

_MONEY = {  # OCR boşlukları: "*2. 700,90", "*166, 74", "*2 .500,76"
    "tr": re.compile(r"(?<![\d.,])(\d{1,3}(?:\s?\.\s?\d{3})+|\d+)\s?,\s?(\d{2})(?!\d)"),
    "dot": re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?![\d.])"),
}


NUMS = ("tr", "dot", "mix")  # mix: aynı fişte iki biçim (e-arşiv fişinde kalem 359.00, toplam 389,00)


def _monies(s: str, num: str) -> list[tuple[int, int, float]]:
    if num == "mix":
        out, taken = [], []
        for x in sorted(_monies(s, "tr") + _monies(s, "dot")):
            if not any(x[0] < e and b < x[1] for b, e in taken):
                out.append(x)
                taken.append((x[0], x[1]))
        return out
    out = []
    for m in _MONEY[num].finditer(s):
        whole = re.sub(r"[\s.,]", "", m.group(1))
        v = round(int(whole) + int(m.group(2)) / 100, 2)
        if re.search(r"-\s?\*?\s?$", s[max(0, m.start() - 3):m.start()]):
            v = -v
        out.append((m.start(), m.end(), v))
    return out


# yıl dört haneyse ardından saat bitişik gelebilir ("01.11.202509:56")
_DATE = re.compile(r"(?<!\d)(\d{1,2})\s?([./-])\s?(\d{1,2})\s?\2\s?(?:(\d{4})(?=\d{2}:\d{2})|(\d{4}|\d{2})(?!\d))")


def _dates(lines: list[str]) -> list[tuple[int, str, int, str, bool]]:
    """(satır, ayraç, yıl hanesi, ISO tarih, 'tarih' etiketli satır mı) — geçersiz tarihler atlanır."""
    out = []
    for i, ln in enumerate(lines):
        labeled = any(_wmatch(w, "tarih") for w in _words(ln))
        for m in _DATE.finditer(ln):
            y = m.group(4) or m.group(5)
            yy = int(y) + 2000 if len(y) == 2 else int(y)
            try:
                iso = date(yy, int(m.group(3)), int(m.group(1))).isoformat()
            except ValueError:
                continue
            out.append((i, m.group(2), len(y), iso, labeled))
    return out


_VKN_LABEL = re.compile(r"(?<![a-z])(?:v\s?\.?\s?[do]\s?\.?m?|vkn(?:\s?/\s?tckn)?|v\s?\.?\s?no|vergi\s*(?:kimlik\s*)?"
                        r"(?:no(?:su)?|numarasi))\s*[:.]?\s*(\d[\d ]{8,12}\d)(?!\d)")


def vkn_of(text: str) -> str | None:
    """Fişteki satıcı VKN'si (etiketli ya da vergi dairesi satırının komşusu; kontrol hanesi tutan 10 hane)."""
    from app import vat

    _, lines = _lines(text)
    for i, ln in enumerate(lines):
        for m in _VKN_LABEL.finditer(ln):
            if v := vat.vkn(m.group(1).replace(" ", "")):
                return v
        if re.fullmatch(r"\d{10}", ln.replace(" ", "")) and any(
                re.search(r"(?<![a-z])(v\.?\s?d|vergi dairesi)(?![a-z])", lines[j])
                for j in (i - 1, i + 1) if 0 <= j < len(lines)):
            if v := vat.vkn(ln.replace(" ", "")):
                return v
    return vat.from_text(text).get("tax_no")


# ---------------------------------------------------------------- etiket eşleme (OCR harf hatasına toleranslı)

def _lev1(a: str, b: str) -> bool:
    """Düzenleme uzaklığı ≤ 1."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    return any(b[:k] + b[k + 1:] == a for k in range(len(b)))


def _wmatch(w: str, target: str) -> bool:
    if w == target:
        return True
    if len(target) >= 5:
        return _lev1(w, target)
    if len(target) >= 3:  # kısa kelime: yalnız aynı uzunlukta, ilk harfi tutan tek harf hatası ("kov" → kdv)
        return len(w) == len(target) and w[0] == target[0] and sum(x != y for x, y in zip(w, target)) <= 1
    return False


def _label_at(ln: str, label: list[str], num: str) -> float | None | bool:
    """Satırda etiket var mı; varsa etiketten sonraki ilk tutar (araya yazı girmeden). Etiket yoksa False."""
    toks = [(m.start(), m.end(), m.group()) for m in re.finditer(r"[a-z]+", ln)]
    for k in range(len(toks) - len(label) + 1):
        if not all(_wmatch(toks[k + j][2], label[j]) for j in range(len(label))):
            continue
        if k > 0 and toks[k - 1][2] in _BLOCK:
            continue
        rest_at = toks[k + len(label) - 1][1]
        for s, _, v in _monies(ln[rest_at:], num):
            if not re.search(r"[a-z]{2,}", ln[rest_at:rest_at + s]):
                return v
            break
        return None
    return False


def _occurrences(lines: list[str], anchor: dict, num: str) -> list[tuple[int, float]]:
    """Toplam çapasının belgedeki geçişleri: (etiket satırı, verdiği değer), sırayla. off 0: değer etiketin sağında
    (araya yazı girmeden); off 1: etiket satırın sonunda (tutarsız satır), değer alt satırın ilk tutarı."""
    label, out = anchor["label"].split(), []
    for i, ln in enumerate(lines):
        if anchor["off"] == 0:
            v = _label_at(ln, label, num)
            if v is not False and v is not None:
                out.append((i, v))
            continue
        ws = _words(ln)
        k = len(ws) - len(label)
        if k < 0 or not all(_wmatch(a, b) for a, b in zip(ws[k:], label)) or (k and ws[k - 1] in _BLOCK) \
                or _monies(ln, num) or i + 1 >= len(lines):
            continue
        if ms := _monies(lines[i + 1], num):
            out.append((i, ms[0][2]))
    return out


def _hits(lines: list[str], anchor: dict, num: str) -> list[float]:
    return [v for _, v in _occurrences(lines, anchor, num)]


def _total_line(lines: list[str], spec: dict) -> tuple[float | None, int | None]:
    """Fişin toplamı ve etiket satırı: şablonun TOPLAM (ya da eşdeğer) etiketinin satırında/altında; metnin herhangi bir
    yerinde geçen aynı sayı yetmez (ara toplam, KDV tutarı karışmasın)."""
    for a in spec["total"]:
        occ = _occurrences(lines, a, spec["num"])
        if len(occ) > a.get("occ", 0):
            i, v = occ[a.get("occ", 0)]
            return v, i
    return None, None


def has_structure(text_lines: list[str], spec: dict) -> bool:
    """Şablonun toplam etiketi metinde geçiyor mu (imza tutsa da başka düzendeki belgeye — ör. POS slibine — uygulanmaz)."""
    return any(_hits(text_lines, a, spec["num"]) for a in spec["total"])


# ---------------------------------------------------------------- kalemler

_QTY = re.compile(r"^\s*(\d+(?:[.,]\d{1,3})?)\s*(adet|adt|ad|kg|kilo|gr|lt|litre|l)?\.?\s*[x×*]\s*\*?\s*"
                  r"(\d{1,3}(?:\.\d{3})+,\d{1,3}|\d+(?:[.,]\d{1,3})?)(?:\s*tl)?(?![\d])")
_KDV_TOKEN = re.compile(r"\s+(?:[%×x*#]\s?\d{1,2}|[%s]?0?[18]0?|[3568]?0?[12]0|[3568]0?1|s01)\s*$", re.I)


# kalem adı sayılmayan başlık satırları: fiş no, saat, tarih, kasiyer, plaka ("34 ABC 123")
_HEADER = re.compile(r"(?<![a-z])(fis|saat|tarih|kasiyer|kasa|no\s?:)(?![a-z])|(?<!\d)\d{2}\s?[a-z]{1,3}\s?\d{2,4}(?!\d)")


def _f(s: str) -> float:
    s = s.strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _qty(m: re.Match, fuel: bool) -> tuple[float, str, float]:
    n, unit, price = _f(m.group(1)), (m.group(2) or ""), _f(m.group(3))
    if unit in ("kg", "kilo", "gr"):
        u = "kg"
    elif unit in ("lt", "litre", "l") or (not unit and fuel):
        u = "lt"
    else:
        u = "adet"
    return n, u, price


def _qty_ok(it: dict) -> bool:
    up, q = it.get("unit_price"), it.get("qty")
    if not up or not q:
        return True
    tol = max(TOL, 0.006 * up) if it["unit"] == "lt" else TOL  # pompa litreyi yuvarlar: ±0,005 lt × birim fiyat
    return abs(q * up - it["amount"]) <= tol


def _name(orig: str) -> str:
    s = re.sub(r"[*·•+×]+\s*$", "", orig).strip(" .:-*·•")
    s = _KDV_TOKEN.sub("", s).strip(" .:-*·•")
    return s[:60]


def _item(name: str | None, amount: float) -> dict:
    return {"name": name or "Ürün", "product": None, "brand": None, "qty": 1, "unit": "adet", "amount": amount,
            "split": None, "unit_price": None}


def _attach(it: dict, q: tuple[float, str, float]) -> None:
    n, unit, price = q
    cand = {**it, "qty": int(n) if unit == "adet" and float(n).is_integer() else n, "unit": unit, "unit_price": price}
    if _qty_ok(cand):
        it.update(cand)
    elif unit == "lt":
        it["_bad"] = True  # akaryakıtta litre × birim fiyat tutmuyor: okuma kabul edilmez
    # adet/kg tutmuyorsa (OCR rakamı bozmuş) miktar atılır, tutar kalır


def _region(lines: list[str], spec: dict, total_at: int, date_at: int | None) -> tuple[int, int]:
    start = date_at + 1 if date_at is not None and date_at < total_at else 0
    end = total_at
    for i in range(start, total_at):
        ws = _words(lines[i])
        if ws and any(_wmatch(ws[0], s) for s in _STOP) and (_monies(lines[i], spec["num"]) or len(ws) <= 3):
            end = i
            break
    return start, end


def _rows(orig: list[str], lines: list[str], spec: dict, start: int, end: int, total_at: int) -> list[dict]:
    num, fuel = spec["num"], spec.get("fuel", False)
    items: list[dict] = []
    pending_name: str | None = None
    pending_qty = None
    for i in range(start, end):
        ln = lines[i]
        if re.fullmatch(r"[\d\s]{8,}", ln):  # barkod
            continue
        q = _QTY.match(ln)
        if q:
            qv = _qty(q, fuel)
            rest = ln[q.end():]
            ms = _monies(rest, num)
            if ms:  # miktar satırına tutar (ve ad) bitişmiş
                amt = ms[-1][2]
                nm = _name(orig[i][q.end():q.end() + ms[-1][0]]) if re.search(r"[a-z]{3,}", rest[:ms[-1][0]]) \
                    else pending_name
                it = _item(nm, amt)
                pending_name = None
                if abs(qv[0] * qv[2] - amt) <= max(TOL, 0.006 * qv[2]):
                    _attach(it, qv)
                elif spec["qty"] == "above" and pending_qty:
                    _attach(it, pending_qty)
                    pending_qty = qv
                items.append(it)
                continue
            if spec["qty"] == "above":
                pending_qty = qv
            elif items and items[-1].get("unit_price") is None:
                _attach(items[-1], qv)
            continue
        ms = _monies(ln, num)
        if not ms:
            if re.search(r"[a-z]{2,}", ln) and not _HEADER.search(ln):
                pending_name = _name(orig[i])
            continue
        s, _, amt = ms[-1]
        prefix = ln[:s]
        has_name = bool(re.search(r"[a-z]{2,}", _KDV_TOKEN.sub("", prefix)))
        it = _item(_name(orig[i][:s]) if has_name else pending_name, amt)
        if set(_words(prefix)) & DISCOUNT_WORDS:
            it["amount"] = -abs(amt)
        pending_name = None
        if pending_qty and spec["qty"] == "above":
            _attach(it, pending_qty)
            pending_qty = None
        items.append(it)
    for i in range(end, total_at):  # ara toplamdan sonra gelen genel indirim satırları
        ws = set(_words(lines[i]))
        if ws & DISCOUNT_WORDS and (ms := _monies(lines[i], num)):
            items.append({**_item(_name(orig[i][:ms[-1][0]]), -abs(ms[-1][2]))})
    if not items and spec.get("single"):
        it = _item(FUEL_ITEM, None)
        it["_single"] = True
        if pending_qty:
            it["_q"] = pending_qty
        items.append(it)
    return items


# ---------------------------------------------------------------- okuma

def _payment(u: str) -> tuple[str, str | None, int, str | None]:
    """(ödeme şekli, yemek kartı markası, taksit, kart son 4) — fişin katlanmış metninden kural ile."""
    meal = next((k for k in ("multinet", "sodexo", "pluxee", "edenred", "ticket", "setcard", "metropol")
                 if re.search(rf"(?<![a-z]){k}", u)), None)
    if meal or re.search(r"yemek\s*(kart|cek)", u):
        pay = "yemek_karti"
    elif re.search(r"kred[i]?\s*kart|k\s?\.\s?kart|banka\s*kart|(?<![a-z])kart(?![a-z])|kredi(?![a-z])|(?<![a-z])pos(?![a-z])"
                   r"|temassiz|mastercard|visa|troy", u):
        pay = "kart"
    elif re.search(r"(?<![a-z])nakit(?![a-z])", u):
        pay = "nakit"
    else:
        pay = "bilinmiyor"
    card = re.search(r"(?:\d{4,6}|[*x]{4})[\s*x]*[*x]{4,}[\s*x]*(\d{4})(?!\d)", u)
    inst = re.search(r"(\d{1,2})\s*taksit|taksit\s*(?:sayisi)?\s*:?\s*(\d{1,2})", u)
    n = int(inst.group(1) or inst.group(2)) if inst else 1
    return pay, meal.title() if meal else None, n if 2 <= n <= 36 else 1, card.group(1) if card else None


_SUFFIX = re.compile(r"(?<![a-z])(a\.?\s?s\.?|ltd|sti|tic\.?|san\.?|anonim|limited)(?![a-z])")


def _strip_suffix(orig: str, folded: str) -> str:
    """Unvan eklerini özgün satırdan çıkar (katlama uzunluğu koruduğu için yerler aynı)."""
    for m in reversed(list(_SUFFIX.finditer(folded))):
        orig = orig[:m.start()] + orig[m.end():]
    return orig


def _merchant(orig: list[str], lines: list[str]) -> tuple[str, str | None]:
    """(işyeri adı, resmi unvan) fişin başlığından: unvan eki (A.Ş., LTD. ŞTİ.) geçen satır ve üstündeki ad satırı."""
    head = [(o, ln) for o, ln in zip(orig[:8], lines[:8])]
    for k, (o, ln) in enumerate(head):
        if not _SUFFIX.search(ln):
            continue
        bare = _SUFFIX.sub("", ln)
        if len(re.findall(r"[a-z]{3,}", bare)) >= 2 and not re.search(r"\d{3,}", ln):
            return _title(_strip_suffix(o, ln)), o[:120]
        if k > 0 and len(re.findall(r"[a-z]", lines[k - 1])) >= 4 and not re.search(r"\d{3,}", lines[k - 1]):
            return _title(orig[k - 1]), f"{orig[k - 1]} {o}"[:120]
    for o, ln in head:
        if len(re.findall(r"[a-z]", ln)) >= 4 and not re.search(r"\d{3,}", ln) and not re.search(
                r"(?<![a-z])(tarih|saat|fis|tel|mah|cad|sok|vkn|mersis)(?![a-z])", ln):
            return _title(o), None
    return "Fiş", None


def _title(s: str) -> str:
    s = re.sub(r"[\s.,*-]+$", "", re.sub(r"\s+", " ", s)).strip(" .,*-")
    return " ".join(w[:1] + w[1:].replace("I", "ı").replace("İ", "i").lower() for w in s.split())[:80] or "Fiş"


def apply(text: str, spec: dict, categories: list[str] | None = None) -> dict | None:
    """Şablonla oku (kabul denetimi yapmadan): receipt.parse_document biçiminde fiş; toplam etiketi yoksa None."""
    orig, lines = _lines(text)
    total, total_at = _total_line(lines, spec)
    if total is None:
        return None
    dates = [d for d in _dates(lines) if d[1] == spec["date"]["sep"] and d[2] == spec["date"]["year"]]
    if spec["date"].get("label"):
        dates.sort(key=lambda d: not d[4])
    day = dates[0] if dates else None
    start, end = _region(lines, spec, total_at, day[0] if day else None)
    items = _rows(orig, lines, spec, start, end, total_at)
    for it in items:
        if it.pop("_single", False):
            it["amount"] = total
            if q := it.pop("_q", None):
                _attach(it, q)
    bad = any(it.pop("_bad", False) for it in items)
    u = " ".join(lines)
    pay, meal, inst, last4 = _payment(u)
    brand = spec.get("brand")
    merchant, legal = _merchant(orig, lines)
    cats = categories or []
    category = spec.get("category") if spec.get("category") in cats else ("Diğer" if "Diğer" in cats or not cats
                                                                           else cats[0])
    doc = {"type": "receipt", "merchant": brand or merchant, "date": day[3] if day else None, "total": total,
           "payment": pay, "payment_card": meal, "installments": inst, "card_last4": last4, "category": category,
           "items": items[:40], "legal_name": legal, "address": None, "branch": None,
           "confidence": "yüksek", "source": "sablon"}
    if bad:
        doc["_bad"] = True
    return doc


def acceptable(doc: dict | None) -> bool:
    """Kabul ölçütleri: toplam (etiketiyle) bulundu, kalemler (indirim dahil) toplamı ±TOL tutuyor, tarih makul
    (bugünden en çok 1 gün ileri, 2 yıl geri), akaryakıtta litre × birim fiyat tutarı veriyor."""
    if not doc or doc.get("_bad"):
        return False
    total, items = doc.get("total"), doc.get("items") or []
    if total is None or not 0 < total < 1_000_000 or not items:
        return False
    if any(not isinstance(it.get("amount"), (int, float)) for it in items) or not all(_qty_ok(it) for it in items):
        return False
    if abs(round(sum(it["amount"] for it in items), 2) - total) > TOL:
        return False
    if not doc.get("date"):
        return False
    d, today = date.fromisoformat(doc["date"]), _today()
    return today - timedelta(days=730) <= d <= today + timedelta(days=1)


# ---------------------------------------------------------------- imza

def _brand(merchant: str | None, legal: str | None, words: set[str]) -> tuple[str, str | None] | None:
    """Yapay zekânın işyeri adı CHAINS'teki bir zincirse ve anahtar kelimeleri metinde geçiyorsa (ad, kategori)."""
    name = set(re.findall(r"[a-z0-9]+", fold(f"{merchant or ''} {legal or ''}")))
    for brand, keys, cat in CHAINS:
        if keys[0] in name and all(k in words for k in keys):
            return brand, cat
    return None


def _text_words(lines: list[str]) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", " ".join(lines[:15])))


def _sig_words(merchant: str | None, legal: str | None, words: set[str], brand: str | None) -> list[str] | None:
    """Yedek imza: zincirde sabit anahtar kelimeler; değilse unvanın metinde geçen ayırt edici kelimeleri (özetlenir)."""
    if brand:
        keys = next(k for b, k, _ in CHAINS if b == brand)
    else:
        toks = re.findall(r"[a-z]+", fold(f"{merchant or ''} {legal or ''}"))
        keys = [t for t in dict.fromkeys(toks) if t in words and t not in _LEGAL and len(t) >= 3][:4]
        if not any(len(t) >= 4 for t in keys):
            return None
    return [_h(k, 12) for k in keys]


def _key(sig: dict) -> str:
    return f"vkn:{sig['vkn']}" if sig.get("vkn") else "w:" + " ".join(sorted(sig["words"]))


def is_safe(spec: dict) -> bool:
    """Şablon yalnız izinli alanlar, seçenekler, sözlük kelimeleri ve özetler içerir: belgeden metin ya da değer (ürün
    adı, tutar, tarih, adres, kart no, plaka, kişi adı) taşıyamaz, kod çalıştıramaz."""
    from app.categorize import CATEGORIES

    try:
        if set(spec) - {"v", "kind", "sig", "brand", "category", "fuel", "num", "date", "total", "qty", "single"}:
            return False
        if spec.get("v") != 1 or spec.get("kind") != "receipt":
            return False
        sig = spec["sig"]
        if not isinstance(sig, dict) or not sig or set(sig) - {"vkn", "words"}:
            return False
        if "vkn" in sig and not (isinstance(sig["vkn"], str) and re.fullmatch(r"[0-9a-f]{16}", sig["vkn"])):
            return False
        if "words" in sig and not (isinstance(sig["words"], list) and 1 <= len(sig["words"]) <= 4 and all(
                isinstance(w, str) and re.fullmatch(r"[0-9a-f]{12}", w) for w in sig["words"])):
            return False
        if "brand" in spec and spec["brand"] not in _BRANDS:
            return False
        if "category" in spec and spec["category"] not in set(CATEGORIES):
            return False
        if any(k in spec and type(spec[k]) is not bool for k in ("fuel", "single")):
            return False
        if spec["num"] not in ("tr", "dot") or spec["qty"] not in ("above", "below"):
            return False
        d = spec["date"]
        if not isinstance(d, dict) or set(d) != {"sep", "year", "label"} or d["sep"] not in "./-" or len(d["sep"]) != 1 \
                or d["year"] not in (2, 4) or type(d["label"]) is not bool:
            return False
        if not isinstance(spec["total"], list) or not 1 <= len(spec["total"]) <= 3:
            return False
        for a in spec["total"]:
            ws = a["label"].split()
            if set(a) != {"label", "off", "occ"} or not 1 <= len(ws) <= 4 or a["label"] != " ".join(ws) \
                    or not all(w in TOTAL_WORDS for w in ws):
                return False
            if not all(type(a[k]) is int and 0 <= a[k] <= lim for k, lim in (("off", 1), ("occ", 3))):
                return False
        return True
    except (KeyError, TypeError, AttributeError):
        return False


def shareable(spec: dict) -> bool:
    """Topluluğa gönderilebilir mi: güvenli VE bilinen bir zincirin (CHAINS) VKN imzalı şablonu (yedek imza zincirin sabit
    anahtar kelimeleri). Küçük işyerinin (unvanı kişi adı olabilir) şablonu bu sunucuda kalır."""
    return is_safe(spec) and spec.get("brand") in _BRANDS and bool(spec["sig"].get("vkn"))


# ---------------------------------------------------------------- öğrenme

def ai_ok(doc: dict, text_lines: list[str]) -> bool:
    """Yapay zekâ okuması öğrenmeye uygun mu: fiş, toplam metinde geçiyor, kalemler (indirim dahil) toplamı tutuyor, tarih
    var, güven düşük değil."""
    total, items = doc.get("total"), doc.get("items") or []
    if doc.get("type") != "receipt" or not total or total <= 0 or not doc.get("date") or not items \
            or doc.get("confidence") == "düşük":
        return False
    if any(not isinstance(it.get("amount"), (int, float)) for it in items):
        return False
    if abs(round(sum(it["amount"] for it in items), 2) - total) > TOL:
        return False
    return any(money.eq(v, total) for ln in text_lines for num in ("tr", "dot") for _, _, v in _monies(ln, num))


def _label_ok(label: list[str]) -> bool:
    return bool(label) and bool(_TOTAL_KEY & set(label)) and ("kdv" not in label or "dahil" in label)


def _trailing(ws: list[str]) -> list[str]:
    """Kelimelerin sonundaki, sözlükte olan en çok dört kelime (etiket adayı)."""
    label: list[str] = []
    for w in reversed(ws):
        if w not in TOTAL_WORDS or len(label) == 4:
            break
        label.insert(0, w)
    return label


def _total_anchors(lines: list[str], total: float, num: str) -> list[dict]:
    """Yapay zekânın toplamını veren etiket adayları: aynı satırda solundaki sözlük kelimeleri (off 0) ya da toplam kendi
    satırındaysa üst satırın sonundaki sözlük kelimeleri (off 1)."""
    out = []
    for i, ln in enumerate(lines):
        for s, _, v in _monies(ln, num):
            if money.ne(v, total):
                continue
            prefix = ln[:s]
            last = [d.end() for d in re.finditer(r"\d", prefix)]
            ws = _words(prefix[last[-1]:] if last else prefix)
            if _label_ok(label := _trailing(ws)):
                out.append({"label": " ".join(label), "off": 0, "occ": 0})
            elif not _words(prefix) and i > 0 and not _monies(lines[i - 1], num) \
                    and _label_ok(label := _trailing(_words(lines[i - 1]))):
                out.append({"label": " ".join(label), "off": 1, "occ": 0})
    seen, uniq = set(), []
    for a in out:
        k = (a["label"], a["off"])
        if k in seen:
            continue
        seen.add(k)
        hits = _hits(lines, a, num)
        if any(money.eq(h, total) for h in hits):
            a["occ"] = min(3, next(n for n, h in enumerate(hits) if money.eq(h, total)))
            uniq.append(a)
    # önce "toplam" içeren, aynı satırdaki, ilk geçen etiket
    return sorted(uniq, key=lambda a: (-("toplam" in a["label"]), a["off"], a["occ"]))


def _same(got: dict | None, doc: dict) -> bool:
    if not got or got.get("total") is None or money.ne(got["total"], doc["total"]) or got.get("date") != doc["date"]:
        return False
    a = sorted(round(it["amount"], 2) for it in got["items"] if isinstance(it.get("amount"), (int, float)))
    b = sorted(round(it["amount"], 2) for it in doc["items"])
    return len(got["items"]) == len(doc["items"]) and a == b


def build(text: str, doc: dict) -> dict | None:
    """Yapay zekâ sonucundan şablon (kaydetmeden); öğrenilemezse None."""
    _, lines = _lines(text)
    if not ai_ok(doc, lines):
        return None
    vkn = vkn_of(text)
    ai_vkn = doc.get("tax_no")
    if vkn and ai_vkn and vkn != ai_vkn:
        return None  # metin ile fotoğraf farklı VKN gösteriyor: güvenme
    words = _text_words(lines)
    br = _brand(doc.get("merchant"), doc.get("legal_name"), words)
    sig = {}
    if vkn:
        sig["vkn"] = _h(vkn, 16)
    if w := _sig_words(doc.get("merchant"), doc.get("legal_name"), words, br[0] if br else None):
        sig["words"] = w
    if not sig:
        return None
    dfmts = list(dict.fromkeys((d[1], d[2], d[4]) for d in _dates(lines) if d[3] == doc["date"]))
    if not dfmts:
        return None
    fuel = bool(_FUEL.search(" ".join(lines)))
    base = {"v": 1, "kind": "receipt", "sig": sig, "fuel": fuel}
    if br:
        base["brand"] = br[0]
    cat = (br[1] if br else None) or doc.get("category")
    from app.categorize import CATEGORIES

    if cat in set(CATEGORIES):
        base["category"] = cat
    qty_lines = [q for ln in lines if (q := _QTY.match(ln))]
    fuel_qty = fuel and any(_qty(q, True)[1] == "lt" for q in qty_lines)
    singles = [False, True] if fuel and len(doc["items"]) == 1 else [False]
    for num in NUMS:
        if not any(money.eq(v, doc["total"]) for ln in lines for _, _, v in _monies(ln, num)):
            continue
        for anchor in _total_anchors(lines, doc["total"], num)[:3]:
            for sep, year, labeled in dfmts:
                best = None  # (miktarı tutarlı bağlanan kalem sayısı, şablon): miktar satırı üstte mi altta mı
                for qty in ("above", "below") if qty_lines else ("above",):
                    for single in singles:
                        spec = {**base, "num": num, "date": {"sep": sep, "year": year, "label": labeled},
                                "total": [anchor], "qty": qty, "single": single}
                        if not is_safe(spec):
                            continue
                        got = apply(text, spec, [doc.get("category") or "Diğer"])
                        if _same(got, doc) and acceptable(got):
                            score = sum(1 for it in got["items"] if it.get("unit_price"))
                            if best is None or score > best[0]:
                                best = (score, spec)
                if best and not (fuel_qty and best[0] == 0):  # akaryakıtta litre satırı bağlanmalı
                    return best[1]
    return None


def _init(c) -> None:
    c.executescript(SCHEMA)


def save(spec: dict, source: str = "learned") -> bool:
    """Şablonu kaydet. Aynı imzada aynı tanım varsa yeniden açılır (hata sıfırlanır); farklı tanım (başka şubenin yazar
    kasa düzeni) eskisini silmez, yanına eklenir; imza başına MAX_PER_SIG'den fazlası olursa en az kullanılan düşer.
    Yeni kaydedildiyse True."""
    from app import system

    if not is_safe(spec) or (source == "community" and not shareable(spec)):
        return False
    key, js = _key(spec["sig"]), json.dumps(spec, ensure_ascii=False, sort_keys=True)
    with system.connect() as c:
        _init(c)
        if c.execute("SELECT 1 FROM receipt_templates WHERE sig = ? AND spec = ?", (key, js)).fetchone():
            c.execute("UPDATE receipt_templates SET fails = 0, disabled = 0, updated_at = CURRENT_TIMESTAMP "
                      "WHERE sig = ? AND spec = ? AND (fails > 0 OR disabled)", (key, js))
            return False
        new = c.execute("INSERT INTO receipt_templates (sig, spec, source) VALUES (?, ?, ?)", (key, js, source)).lastrowid
        # yeni düzen öğrenildi: aynı imzadaki öbür düzenlerin bu fişteki hatası bozukluk değil, başka şubenin kasasıydı
        # (o şubenin fişleri yine onunla okunur); hata sayacı sıfırlanır, devre dışı olanlar dokunulmadan kalır
        c.execute("UPDATE receipt_templates SET fails = 0 WHERE sig = ? AND id != ? AND fails > 0 AND NOT disabled",
                  (key, new))
        others = [r["id"] for r in c.execute(
            "SELECT id FROM receipt_templates WHERE sig = ? AND id != ? ORDER BY disabled DESC, uses ASC, id ASC",
            (key, new))]
        c.executemany("DELETE FROM receipt_templates WHERE id = ?",
                      [(x,) for x in others[:max(0, len(others) + 1 - MAX_PER_SIG)]])
    return True


def learn(text: str | None, doc: dict) -> dict | None:
    """Yapay zekâ okumasından şablon öğren / yenile; zincir şablonuysa topluluğa gönder. Hata okumayı bozmaz."""
    if not text or len(text) > 200_000:
        return None
    try:
        spec = build(text, doc)
        if not spec:
            return None
        if save(spec) and shareable(spec):
            from app import templates

            templates.share_async(spec)
        return spec
    except Exception:  # noqa: BLE001
        log.warning("fiş şablonu öğrenilemedi", exc_info=True)
        return None


# ---------------------------------------------------------------- okuma

def _candidates(c, vkn: str | None, words: set[str]) -> list[tuple[dict, bool]]:
    """(şablon satırı, VKN ile mi eşleşti). Metinde geçerli VKN varsa yalnız o VKN'nin şablonu; yoksa yedek imza."""
    rows = [dict(r) for r in c.execute("SELECT * FROM receipt_templates WHERE disabled = 0 ORDER BY uses DESC, id")]
    if vkn:
        key = "vkn:" + _h(vkn, 16)
        return [(r, True) for r in rows if r["sig"] == key]
    hashed = {_h(w, 12) for w in words}
    out = []
    for r in rows:
        ws = json.loads(r["spec"])["sig"].get("words")
        if ws and all(w in hashed for w in ws):
            out.append((r, False))
    return sorted(out, key=lambda x: (not x[0]["sig"].startswith("vkn:"), -x[0]["uses"]))


def read(text: str | None, categories: list[str] | None = None) -> dict | None:
    """İmzası ve düzeni tutan şablonlar sırayla denenir; kabul ölçütünü geçen ilk sonuç döner. Hiçbiri geçmezse None
    (çağıran yapay zekâya gider) ve denenenlerin hata sayısı artar (art arda MAX_FAILS → devre dışı)."""
    if not text or len(text) > 200_000:
        return None
    try:
        from app import system, templates, vat

        templates.sync_community()
        _, lines = _lines(text)
        vkn = vkn_of(text)
        with system.connect() as c:
            _init(c)
            failed = []
            for row, by_vkn in _candidates(c, vkn, _text_words(lines)):
                spec = json.loads(row["spec"])
                if not is_safe(spec) or not has_structure(lines, spec):
                    continue
                doc = apply(text, spec, categories)
                if acceptable(doc):
                    c.execute("UPDATE receipt_templates SET uses = uses + 1, fails = 0, last_used = CURRENT_TIMESTAMP "
                              "WHERE id = ?", (row["id"],))
                    return vat.fill({**doc, "tax_no": vkn}, text)  # VKN imzadan (vat.from_text "VD: …"yu tanımaz)
                # hata sayacı: VKN ile eşleşen (ya da VKN'siz) şablonda; yedek imzayla eşleşen başka işyeri düzeni sayılmaz
                if by_vkn or not row["sig"].startswith("vkn:"):
                    failed.append(row["id"])
            c.executemany("UPDATE receipt_templates SET fails = fails + 1, disabled = (fails + 1 >= ?), "
                          "updated_at = CURRENT_TIMESTAMP WHERE id = ?", [(MAX_FAILS, x) for x in failed])
    except Exception:  # noqa: BLE001
        log.warning("fiş şablonu uygulanamadı", exc_info=True)
    return None


def overview() -> dict:
    """Yönetici görünümü: şablon başına isabet/hata, son kullanım, durum. Kişisel veri yok: ad yalnız bilinen zincir
    (CHAINS) ise, değilse imzanın kısa özeti."""
    from app import system

    with system.connect() as c:
        _init(c)
        rows = [dict(r) for r in c.execute("SELECT * FROM receipt_templates ORDER BY disabled, uses DESC, id")]
    out = []
    for r in rows:
        spec = json.loads(r["spec"])
        kind = "VKN" if r["sig"].startswith("vkn:") else "unvan"
        out.append({"id": r["id"], "name": spec.get("brand") or f"{kind} #{_h(r['sig'], 6)}", "signature": kind,
                    "fuel": bool(spec.get("fuel")), "single": bool(spec.get("single")), "source": r["source"],
                    "uses": r["uses"], "fails": r["fails"], "disabled": bool(r["disabled"]),
                    "last_used": r["last_used"], "created_at": r["created_at"], "shareable": shareable(spec)})
    return {"templates": out, "total": len(out), "active": sum(1 for t in out if not t["disabled"]),
            "uses": sum(t["uses"] for t in out)}
