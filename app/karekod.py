"""Belge karekodları (QR) ve kalemsiz POS slibi: yapay zekâsız, kural ile okuma.

Telefon fiş/fatura fotoğrafındaki karekodu cihazda çözer (iOS Vision VNDetectBarcodesRequest, Android ML Kit barcode
scanning; hesapkitap-mobile MetinPlugin) ve içeriği metinle birlikte gönderir (POST /api/documents/parse `qr_text`,
tekrarlanabilir alan; yetenek receipt_qr). Eski uygulama alanı göndermez: davranış aynen eskisi. Sunucuda görüntüden
karekod çözülmez (opencv-contrib WeChat QR imajı ~170 MB büyütür; önce telefon).

Sunucu yedeği (scan): telefon karekod göndermediyse (eski uygulama, web yüklemesi, PDF) fotoğrafta / PDF'in ilk
sayfalarında zxing-cpp ile okunur (wheel ~1 MB, kurulu ~2 MB; linux amd64/arm64 manylinux_2_28), SCAN_BUDGET süre
sınırıyla; kütüphane yoksa sessizce atlanır. Yerel ölçüm (12.11.2026, 12 karekodlu gerçek fiş, repo dışı): ön
işlemeyle 1600 px'te 10/12, tam çözünürlükte 11/12 (OpenCV WeChat 10/9, cv2.QRCodeDetector 3/4); karekodsuz 29
görüntüde yanlış okuma yok; süre ort. 17 ms, en çok 67 ms (1600 px).

Tanınan içerikler (parse):
- GİB e-Fatura / e-Arşiv karekodu (Karekod Standardı v1.1): JSON; anahtarlar küçük harf, bazı yazılımlarda boşluklu
  ya da büyük harfli ("hesaplanankdv (8)", "KDVMatrah(20)"); tutarlar "1234.56" ya da "1.234,56"; tarih YYYY-AA-GG.
  JSON bozuksa (tek tırnak, kesik) anahtar:değer çiftleri toleranslı ayıklanır. kind "gib".
  Alıcının VKN/TCKN'si (avkntckn) ALINMAZ (kişisel veri). Satıcı TCKN'si (11 hane) saklanmaz (app/vat.py kuralı).
- Akaryakıt pompası ÖKC karekodu: "VKN TARİH FİŞNO YAKIT_İSMİ YAKIT_MİKTARI" iki yazımda:
  "@" ayırıcılı, tarih GG-AA-YYYY ("1234567890@05-11-2026@0012@MOTORIN@35,42") ve boşluklu, tarih YYYY-AA-GG + SSDD
  ("1234567890 2026-11-05 1420 0012 MOTORIN 35,42"). SON ALAN LİTREDİR (toplam değil). kind "pompa".
- Bağlantı (http/https): ileride e-belgeye erişim bağlantısı olabilir; TANINIR (kind "link") ama içeriğe ağdan
  ERİŞİLMEZ ve okumada kullanılmaz.
- Tanınmayan içerik: yok sayılır.

Okuma (app/receipt.py rule_read, yapay zekâdan ÖNCE):
- Fatura (gib + metinde "son ödeme" tarihi, ödeme dekontu değil, para birimi TL): tutar = odenecek (yoksa vergidahil),
  son ödeme tarihi metinden, kurum: fatura şablonu imzası (app/bill_templates.py) → aynı VKN'li önceki kayıt
  (entries.tax_no) → metindeki şirket satırı. Son ödeme tarihi okunamazsa yapay zekâ okur, karekod tutarı düzeltir
  (apply_hints).
- Akaryakıt (pompa): VKN + tarih + litre karekoddan, birim fiyat OCR metninden (litre satırı "35,42 LT X 52,49" ya da
  10–200 TL aralığındaki bir tutar); toplam = litre × birim fiyat ve metinde bu değeri ±0,05 tutan TEK bir tutar
  olmalı; metinde etiketli TOPLAM varsa o da tutmalı. Tutarsa yapay zekâsız kayıt: tek kalem "Akaryakıt · X L"
  (adet X lt, birim fiyat). Toplam fişte yazan tutardır (pompa kuruş yuvarlar; L × fiyattan en çok 0,05 farklı).
- POS slibi (pos_slip): kalemsiz kart slibi — yalnız toplam + tarih + işyeri. Sıkı ölçütler (market fişi sanılmasın):
  SATIŞ/SALE + maskeli kart no + onay/provizyon kodu + işyeri/terminal no; KDV/TOPKDV/FİŞ NO/Z NO/EKÜ/ETTN YOK; metinde
  tek bir tutar değeri; tek tarih (2 yıl geri, 1 gün ileri); iptal/iade değil; en çok 30 satır.
Okuma hakkı (app/doc_reads.py) çağıranda AYNEN düşer (her okuma düşer; ₺0 yalnız yapay zekâ maliyeti). Yapay zekâ
maliyet kaydı (ai_calls) yazılmaz; sonuç doc["rule"] (qr_bill | qr_fuel | pos_slip) ile işaretlenir ve
app/template_stats.py'de outcome "parser", neden kodu rule olarak sayılır (app/api/main.py _count_receipt / _count_bill).
"""
import json
import logging
import re
from datetime import date, timedelta

from app import money

log = logging.getLogger("hesapkitap.karekod")

MAX_CODES = 8
MAX_LEN = 4096
TOL = 0.05
FUEL_PRICE = (10.0, 200.0)  # TL/L: birim fiyat adayı aralığı (LPG ~25, benzin/motorin ~50–70)
CURRENCY_TL = {"", "try", "tl", "trl"}


def _today() -> date:
    return date.today()


# ---------------------------------------------------------------- sayı / tarih

def _amount(v) -> float | None:
    """GİB karekodundaki tutar: 1234.56 | "1234.56" | "1.234,56" | "1234,56" | "1,234.56"."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = re.sub(r"[\s₺]|TL|TRY", "", str(v), flags=re.I)
    if not re.fullmatch(r"-?[\d.,]+", s or "x"):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _litre(s: str) -> float | None:
    s = s.strip()
    if not re.fullmatch(r"\d{1,4}(?:[.,]\d{1,3})?", s):
        return None
    v = float(s.replace(",", "."))
    return v if 0 < v < 2000 else None


def _iso(s) -> str | None:
    """YYYY-AA-GG | GG-AA-YYYY | GG.AA.YYYY | GG/AA/YYYY → ISO; geçersizse None."""
    s = str(s or "").strip()
    m = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    y, mo, d = (m.group(1), m.group(2), m.group(3)) if m else (None, None, None)
    if not m:
        m = re.match(r"^(\d{1,2})[-./](\d{1,2})[-./](\d{4})", s)
        if not m:
            return None
        d, mo, y = m.group(1), m.group(2), m.group(3)
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def _date_ok(iso: str | None) -> bool:
    if not iso:
        return False
    d, t = date.fromisoformat(iso), _today()
    return t - timedelta(days=730) <= d <= t + timedelta(days=1)


# ---------------------------------------------------------------- karekod içeriği

def _key(k: str) -> str:
    """"hesaplanankdv (8)" / "HesaplananKDV(8)" / "kdv_matrah(20)" → "hesaplanankdv(8)"."""
    k = str(k).strip().replace("İ", "i").replace("I", "i").lower().replace("ı", "i")
    return re.sub(r"[\s_.\-]+", "", k)


def _json_pairs(s: str) -> dict | None:
    """JSON nesnesi; bozuksa (tek tırnak, kesik, sonda virgül) "anahtar": değer çiftleri."""
    s = s.strip().lstrip("﻿")
    i, j = s.find("{"), s.rfind("}")
    body = s[i:j + 1] if i >= 0 and j > i else s[i:] if i >= 0 else None
    if body is None:
        return None
    for cand in (body, body.replace("'", '"'), re.sub(r",\s*}", "}", body.replace("'", '"'))):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except ValueError:
            pass
    pairs = re.findall(r"""["']([^"':{}]{1,40})["']\s*:\s*(?:["']([^"']{0,200})["']|([-\d.,]+))""", body)
    return {k: (a if a or not b else b) for k, a, b in pairs} or None


def _gib(raw: dict) -> dict | None:
    from app import vat

    d = {_key(k): v for k, v in raw.items()}
    if not {"vkntckn", "tarih"} <= set(d) and not {"ettn", "tarih"} <= set(d):
        return None
    payable = _amount(d.get("odenecek"))
    gross = _amount(d.get("vergidahil"))
    total = payable if payable is not None else gross
    if total is None or not 0 < total < 10_000_000:
        return None
    lines, vat_total = [], 0.0
    for k, v in d.items():
        m = re.fullmatch(r"hesaplanankdv\((\d{1,2})(?:[.,]0+)?\)", k)
        if m and (a := _amount(v)) is not None and a > 0:
            lines.append({"rate": int(m.group(1)), "amount": a})
            vat_total += a
    if not lines and (a := _amount(d.get("hesaplanankdv"))):
        vat_total = a
    seller = re.sub(r"\D", "", str(d.get("vkntckn") or ""))
    return {"kind": "gib", "vkn": vat.vkn(seller), "tckn": len(seller) == 11, "date": _iso(d.get("tarih")),
            "no": str(d.get("no") or "").strip()[:20] or None, "ettn": str(d.get("ettn") or "").strip()[:40] or None,
            "scenario": str(d.get("senaryo") or "").strip().upper()[:20] or None,
            "type": str(d.get("tip") or "").strip().upper()[:20] or None,
            "currency": str(d.get("parabirimi") or "").strip().upper()[:5] or None,
            "total": total, "gross": gross, "net": _amount(d.get("malhizmettoplam")),
            "vat_total": round(vat_total, 2) or None, "vat_lines": sorted(lines, key=lambda x: x["rate"])}


_PUMP_AT = re.compile(r"^(\d{10,11})@(\d{1,2}[-./]\d{1,2}[-./]\d{4})@([^@]*)@([^@]*)@\s*([\d.,]+)\s*$")
_PUMP_SP = re.compile(r"^(\d{10,11})\s+(\d{4}-\d{1,2}-\d{1,2})\s+(\d{3,4})\s+(\S+)\s+(.+?)\s+([\d.,]+)\s*$")


def _pump(s: str) -> dict | None:
    from app import vat

    s = s.strip()
    if m := _PUMP_AT.match(s):
        seller, day, no, fuel, lt = m.group(1), _iso(m.group(2)), m.group(3), m.group(4), _litre(m.group(5))
    elif m := _PUMP_SP.match(s):
        seller, day, no, fuel, lt = m.group(1), _iso(m.group(2)), m.group(4), m.group(5), _litre(m.group(6))
    else:
        return None
    if not day or lt is None:
        return None
    return {"kind": "pompa", "vkn": vat.vkn(seller), "tckn": len(seller) == 11, "date": day,
            "no": no.strip()[:20] or None, "fuel": re.sub(r"\s+", " ", fuel).strip()[:30] or None, "liters": lt}


def parse(payload: str | None) -> dict | None:
    """Tek karekod içeriği → {"kind": "gib" | "pompa" | "link", …} ya da None (tanınmadı)."""
    if not payload or not isinstance(payload, str):
        return None
    s = payload.strip()[:MAX_LEN]
    if re.match(r"^https?://", s, re.I):
        host = re.sub(r"^https?://", "", s, flags=re.I).split("/")[0].split("?")[0].lower()[:80]
        # POS / ödeme kuruluşunun satış sorgulama bağlantısı (ör. pavopay …/SaleInquiry…): belge alışveriş fişidir
        sale = bool(re.search(r"sale|satis|slip", s.split("?")[0], re.I))
        return {"kind": "link", "host": host, **({"sale": True} if sale else {})}  # tanınır, erişilmez
    if "{" in s:
        raw = _json_pairs(s)
        return _gib(raw) if raw else None
    return _pump(s)


def best(payloads: list[str] | None) -> dict | None:
    """Belgedeki karekodlardan en kullanışlısı: gib > pompa > link."""
    got = [x for p in (payloads or [])[:MAX_CODES] if (x := parse(p))]
    for kind in ("gib", "pompa", "link"):
        if hit := next((x for x in got if x["kind"] == kind), None):
            return hit
    return None


def clean(payloads) -> list[str]:
    """İstemciden gelen alan: en çok MAX_CODES, her biri MAX_LEN; boşlar atılır, sıra korunur, tekrar yok."""
    out = []
    for p in payloads or []:
        s = str(p or "").strip()[:MAX_LEN]
        if s and s not in out:
            out.append(s)
    return out[:MAX_CODES]


# ---------------------------------------------------------------- metin yardımcıları

def _text_lines(text: str):
    from app import receipt_templates as rt

    return rt._lines(text)


def _amounts(lines: list[str]) -> list[float]:
    from app import receipt_templates as rt

    out = []
    for ln in lines:
        for num in ("tr", "dot"):
            out += [v for _, _, v in rt._monies(ln, num) if v > 0]
    return out


_TOTAL_LABEL = re.compile(r"(?<![a-z])(toplam|genel toplam|tutar)(?![a-z])")
_NOT_TOTAL = re.compile(r"(?<![a-z])(ara|kdv|topkdv|matrah|indirim|vergi)(?![a-z])")


def _labeled_totals(lines: list[str]) -> list[float]:
    from app import receipt_templates as rt

    out = []
    for ln in lines:
        if _TOTAL_LABEL.search(ln) and not _NOT_TOTAL.search(ln):
            out += [v for num in ("tr", "dot") for _, _, v in rt._monies(ln, num) if v > 0]
    return out


# ---------------------------------------------------------------- akaryakıt

def fuel_doc(qr: dict, text: str | None, categories: list[str]) -> dict | None:
    """Pompa karekodu + OCR metni → yapay zekâsız akaryakıt fişi; doğrulanamazsa None."""
    if not qr or qr.get("kind") != "pompa" or not text or not _date_ok(qr.get("date")):
        return None
    from app import receipt_templates as rt, vat

    orig, lines = _text_lines(text)
    L = qr["liters"]
    amounts = _amounts(lines)
    prices: dict[float, int] = {}  # birim fiyat adayı → kanıt gücü (2: litre satırında "L LT X P")
    for ln in lines:
        if (q := rt._QTY.match(ln)) and (n := rt._qty(q, True))[1] == "lt" and round(n[0], 2) == round(L, 2):
            prices[round(n[2], 2)] = 2
    for p in amounts:
        if FUEL_PRICE[0] < p < FUEL_PRICE[1]:
            prices.setdefault(round(p, 2), 1)
    pairs = {}  # metindeki toplam → birim fiyat
    for p, strength in sorted(prices.items(), key=lambda x: -x[1]):
        for a in amounts:
            if a > p and abs(a - L * p) <= TOL:
                pairs.setdefault(round(a, 2), (p, strength))
    if len(pairs) != 1:
        return None  # hiç ya da birden çok aday: belirsiz → yapay zekâ
    total, (price, _) = next(iter(pairs.items()))
    labeled = _labeled_totals(lines)
    if labeled and not any(money.eq(v, total) for v in labeled):
        return None  # etiketli TOPLAM başka bir şey söylüyor
    merchant, legal = rt._merchant(orig, lines)
    pay, meal, inst, last4 = rt._payment(" ".join(lines))
    cats = categories or []
    cat = "Ulaşım & Araç" if "Ulaşım & Araç" in cats or not cats else ("Diğer" if "Diğer" in cats else cats[0])
    lt = f"{L:.3f}".rstrip("0").rstrip(".").replace(".", ",")
    item = {"name": f"Akaryakıt · {lt} L", "product": _fuel_product(qr.get("fuel")), "brand": None, "qty": L,
            "unit": "lt", "unit_price": price, "amount": total, "split": None}
    doc = {"type": "receipt", "merchant": rt.chain_of(merchant, legal) or merchant, "date": qr["date"], "total": total,
           "payment": pay, "payment_card": meal, "installments": inst, "card_last4": last4, "category": cat,
           "items": [item], "legal_name": legal, "address": None, "branch": None, "confidence": "yüksek",
           "source": "karekod", "rule": "qr_fuel", "qr": {"kind": "pompa"}}
    doc = vat.fill(doc, text)
    if qr.get("vkn"):
        doc["tax_no"] = qr["vkn"]
    return doc


_FUELS = (("motorin", "Motorin"), ("diesel", "Motorin"), ("dizel", "Motorin"), ("kursunsuz", "Benzin"),
          ("benzin", "Benzin"), ("lpg", "LPG"), ("otogaz", "LPG"))


def _fuel_product(name: str | None) -> str | None:
    from app.receipt_templates import fold

    f = fold(name or "")
    return next((p for k, p in _FUELS if k in f), None)


# ---------------------------------------------------------------- fatura

_DUE = re.compile(r"son\s*o?deme")


def _due_date(lines: list[str], issued: str | None) -> str | None:
    """Metindeki son ödeme tarihi: etiket satırında (etiketten sonra) ya da alttaki iki satırda; fatura tarihinden önce
    ya da 120 günden sonra olamaz. Sütunlu düzende ("Fatura Tarihi  Son Ödeme Tarihi" / alt satırda iki tarih) en geç olanı."""
    from app import receipt_templates as rt

    lo = date.fromisoformat(issued) if issued else None
    for i, ln in enumerate(lines):
        if not (m := _DUE.search(ln)):
            continue
        for j in (i, i + 1, i + 2):
            if j >= len(lines):
                break
            seg = lines[j][m.end():] if j == i else lines[j]
            ds = sorted({d[3] for d in rt._dates([seg])})
            ok = [d for d in ds if not lo or lo <= date.fromisoformat(d) <= lo + timedelta(days=120)]
            if ok:
                return ok[-1] if j != i and len(ok) > 1 else ok[0]
    return None


def _provider(text: str, lines_orig: list[str], vkn: str | None) -> tuple[str | None, str | None]:
    """(kurum, kategori): fatura şablonu imzası → aynı VKN'li önceki kayıt → metindeki şirket satırı."""
    from app import bill_templates, db, ocr

    try:
        with db.connect() as c:
            bill_templates._init(c)
            for t in bill_templates._templates(c):
                if bill_templates.matches(text, t["spec"]["sig"], t["exclude"]):
                    return t["spec"]["provider"], t["spec"].get("category")
            if vkn:
                r = c.execute("SELECT description, category FROM entries WHERE tax_no = ? AND description != '' "
                              "ORDER BY entry_date DESC, id DESC LIMIT 1", (vkn,)).fetchone()
                if r:
                    return r["description"][:60], r["category"]
    except Exception:  # noqa: BLE001 - kurum bulunamazsa metinden
        log.warning("karekod faturasında kurum aranamadı", exc_info=True)
    name = ocr._company(lines_orig)
    return (name[:60] if name else None), None


# abonelik / kamu hizmeti faturasının açık işaretleri (katlanmış metin). Kategori kelimesi tek başına (elektrik,
# doğalgaz, internet, gsm) sayılmaz: market/restoran e-Arşivinde ürün adı ya da telefon etiketi olabilir.
_UTILITY = re.compile(r"(?<![a-z])(abone|aboneli[gk]|abone\s*no|tesisat|sozlesme\s*hesap|kwh|endeks|sayac|m3|"
                      r"okuma\s*tarihi|ilk\s*okuma|son\s*okuma)(?![a-z])")
# "tüketim" tek başına kurum işareti değil: restoran fişinde "yerinde tüketim" / "paket" geçer (12.11 cihaz testi: karekodlu
# restoran e-Arşivi fatura sayılıyordu). Yalnız miktar / dönem / bedel ya da sayıyla birlikte
_CONSUMPTION = re.compile(r"(?<![a-z])(?<!yerinde\s)(?<!disarida\s)tuketim\s*(miktari|donemi|bedeli|degeri|tutari|\d)")


def utility(text: str | None) -> bool:
    """Metin bir abonelik / kamu hizmeti faturası mı: fatura şablonunun (öğrenilen ya da yerleşik kurum) imzası tutuyor
    ya da abone / tesisat / sözleşme hesap / tüketim (kWh, m3, endeks, sayaç) gibi açık işaret var."""
    if not text:
        return False
    _, lines = _text_lines(text)
    if any(_UTILITY.search(ln) or _CONSUMPTION.search(ln) for ln in lines):
        return True
    from app import bill_templates, db

    try:
        with db.connect() as c:
            bill_templates._init(c)
            return any(bill_templates.matches(text, t["spec"]["sig"], t["exclude"]) for t in bill_templates._templates(c))
    except Exception:  # noqa: BLE001
        log.warning("fatura şablonu imzası denetlenemedi", exc_info=True)
        return False


def bill_like(qr: dict | None, text: str | None, due: str | None = None) -> bool:
    """Ödenecek fatura belirtisi: metinde "son ödeme" geçiyor, ya da kurum (abonelik / kamu hizmeti) faturası, ya da
    okunan son ödeme tarihi karekodun fatura tarihinden SONRA (aynı gün: alışveriş belgesinde tarih tekrarı)."""
    if text:
        _, lines = _text_lines(text)
        if any(_DUE.search(ln) for ln in lines):
            return True
    if utility(text):
        return True
    return bool(due and qr and qr.get("date") and due > qr["date"])


def is_bill(qr: dict | None, text: str | None) -> bool:
    """GİB karekodlu belge ödenecek kurum faturası mı: bill_like (son ödeme / kurum işareti) ve ödeme dekontu değil.
    Değilse alışveriş e-Arşivi / e-Faturası: harcama (fiş) olarak işlenir (shopping)."""
    from app import bill_templates

    if not qr or qr.get("kind") != "gib" or not text:
        return False
    return bill_like(qr, text) and not bill_templates.is_paid_doc(text)


def shopping(qr: dict | None, text: str | None, due: str | None = None) -> bool:
    """GİB karekodlu ALIŞVERİŞ belgesi (restoran, market e-Arşivi): karekod TL, son ödeme / kurum işareti yok, ödeme
    dekontu değil. Karekod tek başına "ödenecek fatura" kararı vermez; yapay zekâ "fatura" dese de fiş olur."""
    from app import bill_templates

    if not qr or qr.get("kind") != "gib" or (qr.get("currency") or "").lower() not in CURRENCY_TL:
        return False
    if text and bill_templates.is_paid_doc(text):
        return False  # dekont / makbuz: mevcut davranış
    return not bill_like(qr, text, due)


def as_receipt(doc: dict, qr: dict, text: str | None, categories: list[str] | None = None) -> dict:
    """Yapay zekânın (ya da yerel okumanın) "fatura" dediği alışveriş e-Arşivi → fiş (harcama): tutar karekodun
    odenecek'i (yoksa vergidahil), tarih karekoddan, VKN ve KDV dökümü karekoddan, işyeri okumanın kurum adından (yoksa
    metnin başlığından). Kalemler metinden kural ile; toplamı karekodla ±TOL tutmuyorsa kalemsiz."""
    from app import ocr, receipt_templates as rt, vat

    cats = categories or []
    items, pay, last4, inst, merchant, legal = [], "bilinmiyor", None, 1, None, None
    if text:
        orig, lines = _text_lines(text)
        merchant, legal = rt._merchant(orig, lines)
        pay, _, inst, last4 = rt._payment(" ".join(lines))
        try:
            loc = ocr.parse_text(text, cats or ["Diğer"]).get("items") or []
        except ValueError:
            loc = []
        if loc and all(isinstance(i.get("amount"), (int, float)) for i in loc) \
                and abs(round(sum(i["amount"] for i in loc), 2) - qr["total"]) <= TOL:
            items = loc
    name = (doc.get("provider") or "").strip()
    out = {"type": "receipt", "merchant": (name if name and name != "Fatura" else merchant) or "Fiş",
           "date": qr["date"] if _date_ok(qr.get("date")) else None, "total": qr["total"], "payment": pay,
           "payment_card": None, "installments": inst, "card_last4": last4,
           "category": "Diğer" if "Diğer" in cats or not cats else cats[0], "items": items[:40],
           "legal_name": doc.get("legal_name") or legal, "address": None, "branch": None,
           "vat_total": qr.get("vat_total"), "vat_lines": qr.get("vat_lines") or [], "tax_no": qr.get("vkn"),
           "tax_office": None, "confidence": "orta", "source": doc.get("source") or "yapay_zeka"}
    for k in ("engine", "note", "model"):
        if doc.get(k):
            out[k] = doc[k]
    if text:
        vat.fill(out, text)
        if qr.get("vkn"):
            out["tax_no"] = qr["vkn"]
    out["qr_fixed"] = ["type"]
    return out


def bill_doc(qr: dict, text: str | None) -> dict | None:
    """GİB karekodu + metindeki son ödeme tarihi → yapay zekâsız fatura (receipt.parse_document biçimi)."""
    from app import bill_templates

    if not is_bill(qr, text) or (qr.get("currency") or "").lower() not in CURRENCY_TL or not _date_ok(qr.get("date")):
        return None
    orig, lines = _text_lines(text)
    due = _due_date(lines, qr.get("date"))
    if not due:
        return None
    provider, cat = _provider(text, orig, qr.get("vkn"))
    cat = cat if cat in bill_templates.CATEGORIES else "Faturalar"
    return {"type": "bill", "legal_name": None, "provider": provider or "Fatura", "category": cat,
            "amount": qr["total"], "due_date": due,
            "description": f"Fatura no {qr['no']}" if qr.get("no") else "", "paid": False, "paid_at": None,
            "payment": None, "confidence": "yüksek", "source": "karekod", "rule": "qr_bill", "qr": {"kind": "gib"}}


_BILL_KEEP = {"Elektrik", "Doğalgaz", "Su", "İnternet & TV", "Telefon", "Kira & Aidat"}


def bill_without_due(doc: dict, text: str | None, categories: list[str] | None = None, sale: bool = False) -> dict:
    """Karekod okunamadı (ya da GİB değil) ve okuma "fatura" dedi: son ödeme tarihi yok, kurum işareti yok, kategori
    abonelik değil ve ödeme dekontu değilse alışveriş belgesidir (restoran / market e-Arşivi; 12.11 cihaz testi: karekodu
    okunamayan restoran fişi Ödemeler'e düşüyordu) → fiş. Telefon metni yoksa karar verilmez (eski davranış). sale: karekod
    POS satış sorgulama bağlantısı (parse "sale"): metin olmasa da, kurum işareti olsa da fiş."""
    from app import bill_templates

    if doc.get("type") != "bill" or doc.get("paid") or doc.get("due_date") or not (text or sale):
        return doc
    if not sale and ((doc.get("category") or "") in _BILL_KEEP or bill_like(None, text)):
        return doc
    if text and bill_templates.is_paid_doc(text):
        return doc
    amount = doc.get("amount")
    if not isinstance(amount, (int, float)) or amount <= 0:
        return doc
    date = doc.get("issue_date") or doc.get("date")
    if not _date_ok(date) and text:
        from app import ocr

        try:
            date = ocr.parse_text(text, categories or ["Diğer"]).get("date")
        except ValueError:
            date = None
    pseudo = {"kind": "metin", "total": round(float(amount), 2), "date": date, "vkn": doc.get("tax_no"),
              "currency": "TRY"}
    out = as_receipt(doc, pseudo, text, categories)
    out["qr_fixed"] = ["type"]
    return out


def apply_hints(doc: dict, qr: dict | None, text: str | None = None, categories: list[str] | None = None) -> dict:
    """Yapay zekâ (ya da yerel) okumasından sonra GİB karekodu: tutar karekodla tutmuyorsa karekodunki (e-belgenin
    resmi tutarı), boş tarih / VKN / KDV dökümü karekoddan. Pompa karekodu: boş tarih / VKN. doc['qr_fixed'] alanları.
    Okuma "fatura" dedi ama belge alışveriş e-Arşivi (shopping: son ödeme / kurum işareti yok, ödenmiş fatura değil)
    → fiş (as_receipt): Ödemeler'e değil harcama olarak İşlemler'e girer."""
    if not doc:
        return doc
    if not qr or qr.get("kind") not in ("gib", "pompa"):
        return bill_without_due(doc, text, categories, sale=bool(qr and qr.get("sale")))
    if doc.get("type") == "bill" and qr["kind"] == "gib" and not doc.get("paid") \
            and shopping(qr, text, doc.get("due_date")):
        doc = as_receipt(doc, qr, text, categories)
    fixed = list(doc.pop("qr_fixed", []))
    tl = (qr.get("currency") or "").lower() in CURRENCY_TL
    if doc.get("type") == "bill":
        if qr["kind"] == "gib" and tl and (doc.get("amount") is None or not money.eq(doc["amount"], qr["total"])):
            doc["amount"] = qr["total"]
            fixed.append("amount")
    elif doc.get("type") == "receipt":
        if qr["kind"] == "gib" and tl and (doc.get("total") is None or abs(doc["total"] - qr["total"]) > TOL):
            doc["total"] = qr["total"]
            fixed.append("total")
        if not doc.get("date") and _date_ok(qr.get("date")):
            doc["date"] = qr["date"]
            fixed.append("date")
        if qr.get("vkn") and not doc.get("tax_no"):
            doc["tax_no"] = qr["vkn"]
            fixed.append("tax_no")
        if qr["kind"] == "gib" and tl and qr.get("vat_lines") and not doc.get("vat_lines"):
            doc["vat_lines"], doc["vat_total"] = qr["vat_lines"], qr["vat_total"]
            fixed.append("vat")
    if fixed:
        doc["qr_fixed"] = fixed
    doc.setdefault("qr", {"kind": qr["kind"]})
    return doc


# ---------------------------------------------------------------- POS slibi

_POS_SALE = re.compile(r"(?<![a-z])(satis|sale)(?![a-z])")
_POS_CARD = re.compile(r"(?:\d{4,6}|[*x]{4})[\s*x]*[*x]{4,}[\s*x]*\d{4}(?!\d)|[*x]{4,}\s?\d{4}(?!\d)")
_POS_AUTH = re.compile(r"(?<![a-z])(onay|provizyon|prov|auth|approval)(?![a-z])")
_POS_MERCH = re.compile(r"(?<![a-z])(isyeri|uye\s*isyeri|merchant|terminal|term|mid|tid)(?![a-z])")
_NOT_POS = re.compile(r"(?<![a-z])(kdv|topkdv|fis\s*no|z\s*no|eku|ettn|ara\s*toplam|adet|ad\s*x|kg|matrah|fatura|"
                      r"iptal|iade|void|refund|cancel|bakiye)(?![a-z])")
_POS_MAX_LINES = 30


def pos_slip(text: str | None, categories: list[str]) -> dict | None:
    """Kalemsiz kart slibi → yalnız toplam + tarih + işyeri (sıkı ölçütler; tutmazsa None)."""
    if not text or len(text) > 6000:
        return None
    from app import receipt_templates as rt

    orig, lines = _text_lines(text)
    if not 4 <= len(lines) <= _POS_MAX_LINES:
        return None
    u = " ".join(lines)
    if not (_POS_SALE.search(u) and _POS_CARD.search(u) and _POS_AUTH.search(u) and _POS_MERCH.search(u)):
        return None
    if _NOT_POS.search(u):
        return None
    values = {round(v, 2) for v in _amounts(lines)}
    if len(values) != 1:
        return None  # slipte tek tutar olur (TUTAR / SATIŞ TUTARI); birden çok tutar: fiş ya da başka belge
    total = values.pop()
    if not 0 < total < 1_000_000:
        return None
    days = {d[3] for d in rt._dates(lines)}
    if len(days) != 1 or not _date_ok(day := next(iter(days))):
        return None
    merchant, legal = rt._merchant(orig, lines)
    if merchant == "Fiş":
        return None  # işyeri adı yok
    _, _, inst, last4 = rt._payment(u)
    if not last4 and (m := re.search(r"[*x]{2,}[\s*x]*(\d{4})(?!\d)", u)):  # "4543 60** **** 1234"
        last4 = m.group(1)
    cats = categories or []
    return {"type": "receipt", "merchant": rt.chain_of(merchant, legal) or merchant, "date": day, "total": total,
            "payment": "kart", "payment_card": None, "installments": inst, "card_last4": last4,
            "category": "Diğer" if "Diğer" in cats or not cats else cats[0], "items": [], "legal_name": legal,
            "address": None, "branch": None, "vat_total": None, "vat_lines": [], "tax_no": None, "tax_office": None,
            "confidence": "yüksek", "source": "pos", "rule": "pos_slip"}


# ---------------------------------------------------------------- sunucu yedeği: görüntüden / PDF'ten karekod

SCAN_BUDGET = 1.5  # sn: yedek okuma bütçesi (adımlar arasında denetlenir; tek çağrı ~5–70 ms)
PDF_PAGES = 2      # PDF'te taranan ilk sayfalar
PDF_DPI = 200
_PDFIUM = __import__("threading").Lock()  # pdfium çizimi iş parçacığına güvenli değil


def _zx():
    try:
        import zxingcpp
    except ImportError:  # kütüphane yoksa (kendi sunucusu, eski imaj) yedek sessizce atlanır
        return None
    return zxingcpp


def _variants(img):
    """Ön işleme sırası (yerel ölçüm 12.11.2026, 12 karekodlu fiş: düz okuma 7/12, bu sırayla 1600 px'te 10/12, tam
    çözünürlükte 11/12; karekodsuz 29 görüntüde yanlış okuma 0): gri → oto kontrast → başka eşikleme → ölçek →
    küçük döndürme → alt bölge (karekod çoğu fişin altında) 2× büyütülmüş."""
    from PIL import ImageOps

    zx = _zx()
    g = ImageOps.grayscale(img)
    yield g, {}
    yield ImageOps.autocontrast(g, cutoff=1), {}
    for b in (zx.Binarizer.GlobalHistogram, zx.Binarizer.FixedThreshold):
        yield g, {"binarizer": b}
    w, h = g.size
    for s in (0.5, 0.75, 1.5):
        if max(w, h) * s <= 5000:
            yield g.resize((max(1, int(w * s)), max(1, int(h * s)))), {}
    for a in (-8, -4, 4, 8):
        yield g.rotate(a, expand=True, fillcolor=255), {}
    for y0 in (0.5, 0.66):
        r = g.crop((0, int(h * y0), w, h))
        if max(r.size) * 2 <= 5000:
            yield r.resize((r.width * 2, r.height * 2)), {}


def _scan_pil(img, deadline: float) -> list[str]:
    import time

    zx = _zx()
    for im, kw in _variants(img):
        if time.monotonic() > deadline:
            break
        try:
            got = [b.text for b in zx.read_barcodes(im, formats=zx.BarcodeFormat.QRCode, **kw) if b.text]
        except Exception:  # noqa: BLE001 - bozuk görüntü: sonraki seçenek
            got = []
        if got:
            return clean(got)
    return []


def scan(data: bytes | None, mime: str, original: bytes | None = None, budget: float = SCAN_BUDGET) -> list[str]:
    """Telefon karekod göndermediyse (eski uygulama, web yüklemesi, PDF) sunucuda oku: fotoğrafta önce hazırlanmış
    (1600 px) görüntü, olmazsa özgün tam çözünürlük; PDF'te ilk PDF_PAGES sayfa PDF_DPI ile çizilir. Süre sınırı
    budget; kütüphane (zxing-cpp) yoksa ya da hata olursa []. İçerik yalnız okumada kullanılır, saklanmaz."""
    import io
    import time

    if not data or _zx() is None:
        return []
    deadline = time.monotonic() + budget
    try:
        from PIL import Image, ImageOps

        if mime.startswith("image/"):
            for raw in dict.fromkeys(x for x in (data, original) if x):  # önce küçük (hızlı), sonra özgün
                if time.monotonic() > deadline:
                    break
                with Image.open(io.BytesIO(raw)) as im:
                    from app import images

                    if im.width * im.height > images.MAX_PIXELS:
                        continue
                    got = _scan_pil(ImageOps.exif_transpose(im).convert("RGB"), deadline)
                if got:
                    return got
        elif mime == "application/pdf":
            import pypdfium2 as pdfium

            with _PDFIUM:
                pdf = pdfium.PdfDocument(data)
                try:
                    pages = [pdf[i].render(scale=PDF_DPI / 72).to_pil() for i in range(min(len(pdf), PDF_PAGES))]
                finally:
                    pdf.close()
            for page in pages:
                if time.monotonic() > deadline:
                    break
                if got := _scan_pil(page.convert("RGB"), deadline):
                    return got
    except Exception:  # noqa: BLE001 - yedek: okumayı bozmaz (şifreli PDF, bozuk görüntü)
        log.info("sunucuda karekod okunamadı", exc_info=True)
    return []
