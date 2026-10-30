"""Fiş fotoğrafından gider bilgisi çıkarma (Gemini API, REST)."""
import base64
import json
import os
import re
from datetime import date

import httpx

from app import config

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
MAX_BYTES = 15 * 1024 * 1024

ITEM_RULES = """items (sadece fiş, en fazla 40 satır, fişteki sırayla):
- name: satırın fişteki yazısı. product: aynı ürünü farklı marketlerde eşleştirmek için STANDART ad: marka + ürün + gramaj/hacim,
  Türkçe karakterli, kısaltmalar açık (ör. "ULKER GOFRET CIK 36GR" → "Ülker Çikolatalı Gofret 36 g"; "SUT 1L PINAR" → "Pınar Süt 1 L").
- brand: ürünün markası (ör. Ülker, Pınar, Marlboro); kafe/restoranın kendi ürünüyse (latte, tost) işletmenin markası
  (ör. Kahve Dünyası); bilinmiyorsa null.
- "2Adt x 230,00 Latte" → qty=2, unit="adet", unit_price=230, amount=460 (satır toplamı). unit_price yalnız fişte birim
  fiyat yazıyorsa (miktar satırı), yoksa null.
- Tartılı ürün "0,750 KG x 89,90" → qty=0.75, unit="kg", amount=67.43; litre ise unit="lt".
- Türk yazar kasa fişinde MİKTAR SATIRI ("2 ad X 2,90", "2 AD X 44.90", "0,750 KG X 89,90") ÜRÜN SATIRINDAN ÖNCE, kendi
  satırında gelir ve ALTINDAKİ ürüne aittir; o ürünün satırında sağda toplam yazar ("*5,80" = 2 × 2,90). Miktar satırını
  AYRI ÜRÜN olarak YAZMA ve adını ürün adına katma; qty ve unit'i altındaki ürüne ver. Her ürünün amount'u KENDİ satırının
  sağındaki tutardır (bir alttaki ya da üstteki ürünün tutarı değil). Örnek:
    2 ad X 2,90
    K. CANTA ORTA BOY        *5,80
    KIZILAY 6X200 SADE      *66,00
  → [{{"name": "K. CANTA ORTA BOY", "qty": 2, "amount": 5.80}}, {{"name": "KIZILAY 6X200 SADE", "qty": 1, "amount": 66.00}}]
- Bir ürünün hemen altındaki İNDİRİM/KAMPANYA satırı o ürünün amount'undan düşülür; hangi ürüne ait olduğu belli olmayan genel
  indirimleri item olarak yazma.
- Fiyatı okunamayan ürünü amount null ile yine yaz.
- Birden fazla görüntü varsa hepsi AYNI fişin sırayla parçalarıdır: üst üste binen (iki fotoğrafta da görünen) satırları BİR kez
  yaz; total son parçadaki GENEL TOPLAM'dır.
"""


PROMPT = """Bu bir Türkiye'de alınmış alışveriş fişi fotoğrafı. Bilgileri çıkar ve SADECE şu JSON'u döndür:
{{
  "merchant": "MARKA / tabela adı, kısa (ör. Espressolab, Migros)",
  "legal_name": "fişteki resmi şirket unvanı (ör. NEW TASTE GIDA SAN.VE DIŞ.TİC.LTD.ŞTİ.) veya null",
  "date": "YYYY-MM-DD (fişteki tarih; okunamazsa null)",
  "total": 0.00,
  "vat": 0.00,
  "payment": "nakit | kart | bilinmiyor",
  "category": "şu listeden en uygun olan: {categories}",
  "items": [{{"name": "fişteki satır yazısı", "product": "standart ürün adı", "qty": 1, "unit": "adet", "unit_price": null, "amount": 0.00}}],
  "confidence": "yüksek | orta | düşük"
}}
Kurallar: total = fişteki GENEL TOPLAM / TOPLAM (KDV dahil, ödenen tutar). Türk sayı formatında 1.234,56 = 1234.56.
Fiş değilse veya okunamıyorsa total null ve confidence "düşük" döndür.
""" + ITEM_RULES




class ReceiptError(Exception):
    pass


def _shared_key_user() -> dict | None:
    """Kendi sunucusu: ortak (yöneticinin) anahtarını kullanan yönetici-olmayan kullanıcı; kota ona, yapay zekâ çağrısı
    başına uygulanır. Hazır sürümde None: orada hak BELGE başınadır (app/doc_reads.py) — bir belgenin içindeki birden çok
    yapay zekâ çağrısı ayrıca sayılmaz, çift sayım olmaz."""
    from app import db, plans

    if plans.hosted():
        return None
    u = db.current_user()
    if not u or u.get("role") == "admin" or config.own_ai_key():
        return None
    return u


def _check_quota() -> None:
    from app import system

    u = _shared_key_user()
    if not u:
        return
    quota, used = system.ai_quota(u), system.ai_used_pool(u)  # ortak defterde ailenin ortak havuzu
    if quota is not None and used >= quota:
        raise ReceiptError(f"Bu ayki yapay zekâ okuma hakkın doldu ({used}/{quota}). Ay başında yenilenir; beklemek "
                           "istemezsen Ayarlar'dan kendi Gemini anahtarını girebilirsin.")


def _count_usage() -> None:
    from app import system

    u = _shared_key_user()
    if u:
        system.ai_add(u["id"])


def enabled() -> bool:
    return bool(config.get("GEMINI_API_KEY"))


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ReceiptError("Model JSON döndürmedi")
    return json.loads(m.group(0))


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v).replace("₺", "").replace("TL", "").strip()
    if "," in s:  # Türk formatı
        s = s.replace(".", "").replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


BILL_PROMPT = """Bu bir Türkiye'de kesilmiş fatura (elektrik, doğalgaz, su, internet, telefon, aidat vb.). PDF veya fotoğraf olabilir.
SADECE şu JSON'u döndür:
{
  "provider": "kurum adı, kısa (ör. Enerjisa, İGDAŞ, Türk Telekom, Turkcell, İSKİ)",
  "type": "elektrik | dogalgaz | su | internet | telefon | aidat | diger",
  "amount": 0.00,
  "due_date": "YYYY-MM-DD (SON ÖDEME TARİHİ; okunamazsa null)",
  "issue_date": "YYYY-MM-DD veya null",
  "period": "fatura dönemi, ör. 'Ekim 2026' veya null",
  "subscriber_no": "abone/tesisat/hizmet no veya null",
  "confidence": "yüksek | orta | düşük"
}
amount = ÖDENECEK TUTAR (KDV dahil toplam). Türk sayı formatında 1.234,56 = 1234.56."""

BILL_CATEGORY = {"elektrik": "Elektrik", "dogalgaz": "Doğalgaz", "su": "Su", "internet": "İnternet & TV",
                 "telefon": "Telefon", "aidat": "Kira & Aidat"}


def _gemini_json(prompt: str, data: bytes, mime: str, max_output_tokens: int | None = None,
                 more: list[tuple[bytes, str]] | None = None, model: str | None = None) -> tuple[dict, dict]:
    """more: aynı belgenin devam görüntüleri (uzun fiş birden fazla fotoğraf)."""
    key = config.get("GEMINI_API_KEY")
    if not key:
        raise ReceiptError("Gemini API anahtarı tanımlı değil (Ayarlar → Anahtarlar)")
    _check_quota()
    docs = [(data, mime), *(more or [])]
    if sum(len(d) for d, _ in docs) > MAX_BYTES:
        raise ReceiptError("Dosya çok büyük (en fazla 15 MB)")
    model = model or config.get("GEMINI_MODEL") or DEFAULT_MODEL
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            *({"inline_data": {"mime_type": m, "data": base64.b64encode(d).decode()}} for d, m in docs),
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0,
                             **({"maxOutputTokens": max_output_tokens} if max_output_tokens else {})},
    }
    try:
        r = httpx.post(GEMINI_URL.format(model=model), json=body, timeout=180,
                       headers={"x-goog-api-key": key})
    except httpx.HTTPError as e:
        raise ReceiptError(f"Gemini'ye ulaşılamadı: {e}") from e
    if r.status_code != 200:
        msg = r.json().get("error", {}).get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        raise ReceiptError(f"Gemini hatası ({r.status_code}): {msg}")

    resp = r.json()
    try:
        text = "".join(p.get("text", "") for p in resp["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as e:
        raise ReceiptError("Gemini boş yanıt döndü") from e
    _count_usage()
    usage = resp.get("usageMetadata", {})
    return _extract_json(text), {"model": model, "input": usage.get("promptTokenCount"),
                                 "output": usage.get("candidatesTokenCount")}


def card_last4(v) -> str | None:
    """Kartın son 4 hanesi: "6081", "****6081", 6081 → "6081"; okunamadıysa None."""
    m = re.search(r"(\d{4})\D*$", str(v or ""))
    return m.group(1) if m else None


def _installments(v) -> int:
    try:
        n = int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 1
    return n if 2 <= n <= 36 else 1


# miktar satırı: "2 ad X 2.90", "2 AD x 44,90", "0,750 KG X 89,90" (ardından ürün adı gelebilir: model birleştirmiş)
QTY_LINE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(AD|ADT|ADET|KG|GR|LT|L)\.?\s*[xX×*]\s*(\d+(?:[.,]\d{1,3})?)\s*", re.I)


def _qty_of(m: re.Match) -> tuple[float, str, float]:
    n, unit, price = float(m.group(1).replace(",", ".")), m.group(2).upper(), float(m.group(3).replace(",", "."))
    return n, ("kg" if unit in ("KG", "GR") else "lt" if unit in ("LT", "L") else "adet"), price


def fix_qty_lines(items: list[dict]) -> list[dict]:
    """Model miktar satırını ("2 ad X 2.90") ayrı ürün yazdıysa ya da ürün adına kattıysa düzelt.

    Türk fişinde miktar satırı ürün satırının ÜSTÜNDEDİR. Model onu ayrı ürün yazıp ürünün tutarını ona verince sonraki
    adlar bir aşağı kayar ("2× 2 ad X 2.90 5,80", "K. CANTA ORTA BOY 66,00"…): tutarların sırası doğru, adlar kaymış.
    O noktadan sonra adlar (miktar satırları çıkarılarak) tutarlarla yeniden eşlenir; ad kalmayan son tutar 'Okunamayan
    ürün' olur (toplam korunur). Miktar (adet × birim) toplamı tutan ilk satıra verilir: '2 ad X 44.90' → 89,80'lik ürün."""
    toks = []
    for it in items:
        m = QTY_LINE.match(it.get("name") or "")
        q = _qty_of(m) if m else None
        rest = (it["name"][m.end():] if m else it.get("name") or "").strip(" -*")
        toks.append({**it, "_q": q, "_rest": rest})
    if not any(t["_q"] for t in toks):
        return items

    def total(q):
        return round(q[0] * q[2], 2)

    def near(a, b):
        return a is not None and abs(a - b) <= 0.02

    # tutar yuvası kaplayan (kaydıran) ayrı miktar satırı: tutarı adet × birim ve bir sonraki satırın tutarı değil
    shift_at = next((k for k, t in enumerate(toks) if t["_q"] and not t["_rest"] and near(t.get("amount"), total(t["_q"]))
                     and not (k + 1 < len(toks) and near(toks[k + 1].get("amount"), total(t["_q"])))), None)
    head = [t for t in toks[: shift_at if shift_at is not None else len(toks)] if t["_rest"]]
    out = [{**t, "name": t["_rest"]} for t in head]
    if shift_at is not None:
        tail = toks[shift_at:]
        names = [t for t in tail if t["_rest"]]
        amounts = [t.get("amount") for t in tail if t["_rest"] or near(t.get("amount"), total(t["_q"]))]
        for i in range(max(len(names), len(amounts))):
            if i < len(names):
                out.append({**names[i], "name": names[i]["_rest"], "amount": amounts[i] if i < len(amounts) else None,
                            "qty": 1, "unit": "adet"})
            elif amounts[i] is not None:
                out.append({"name": "Okunamayan ürün", "product": None, "brand": None, "qty": 1, "unit": "adet",
                            "amount": amounts[i], "split": None})
    # miktarlar: miktar satırından sonraki (birleşikse kendisi) ilk üç ürün içinde tutarı adet × birim olana
    taken: set[int] = set()
    seen_names = 0
    for t in toks:
        if t["_q"]:
            n, unit, _ = t["_q"]
            for j in range(min(seen_names, len(out) - 1), min(seen_names + 3, len(out))):
                if j not in taken and near(out[j].get("amount"), total(t["_q"])):
                    out[j] = {**out[j], "qty": int(n) if unit == "adet" and float(n).is_integer() else n, "unit": unit,
                              "unit_price": t["_q"][2]}
                    taken.add(j)
                    break
        if t["_rest"]:
            seen_names += 1
    for o in out:
        o.pop("_q", None)
        o.pop("_rest", None)
    return out


def _items(raw: dict) -> list[dict]:
    """Ürün satırları; "2Adt x 230,00" gibi adetler qty olarak gelir."""
    out = []
    raw = {**raw, "items": fix_qty_lines([i for i in (raw.get("items") or [])[:40] if isinstance(i, dict) and i.get("name")])}
    for i in (raw.get("items") or [])[:40]:
        if not isinstance(i, dict) or not i.get("name"):
            continue
        unit = i.get("unit") if i.get("unit") in ("adet", "kg", "lt") else "adet"
        q = _num(i.get("qty"))
        q = q if q and q > 0 else 1
        if unit == "adet":
            q = int(q) if float(q).is_integer() and q >= 1 else 1
        split = i.get("split") if isinstance(i.get("split"), str) and i.get("split") else None
        product = str(i.get("product") or "").strip()[:80] or None
        brand = str(i.get("brand") or "").strip()[:40] or None
        out.append({"name": str(i["name"])[:60], "product": product, "brand": brand, "qty": q, "unit": unit,
                    "amount": _num(i.get("amount")), "split": split, "unit_price": _num(i.get("unit_price"))})
    return out


def _iso(d) -> str | None:
    try:
        return date.fromisoformat(d).isoformat() if d else None
    except (ValueError, TypeError):
        return None


DOC_PROMPT = """Bu belge Türkiye'den; ya bir ALIŞVERİŞ FİŞİ (ödeme yapılmış, POS/yazar kasa fişi) ya da
ödenecek bir FATURA (elektrik, doğalgaz, su, internet, telefon, aidat; "son ödeme tarihi" içerir). PDF veya fotoğraf olabilir.
SADECE şu JSON'u döndür:
{{
  "doc_type": "fis | fatura",
  "merchant": "MARKA / tabela / kurum adı, kısa (ör. Espressolab, Enerjisa)",
  "legal_name": "belgedeki resmi şirket unvanı (ör. NEW TASTE GIDA SAN.VE DIŞ.TİC.LTD.ŞTİ.) veya null",
  "address": "fişte yazan işyeri adresi (mahalle/cadde, ilçe/il) kısa, veya null",
  "branch": "şube / semt adı, kısa (ör. Fıstıkağacı, Kadıköy Moda); fiş başlığında ya da adreste geçen; yoksa null",
  "date": "YYYY-MM-DD fiş tarihi / fatura düzenleme tarihi veya null",
  "total": 0.00,
  "due_date": "YYYY-MM-DD SON ÖDEME TARİHİ (sadece fatura) veya null",
  "bill_type": "elektrik | dogalgaz | su | internet | telefon | aidat | diger | null",
  "period": "fatura dönemi veya null",
  "subscriber_no": "abone/tesisat no veya null",
  "payment": "nakit | kart | yemek_karti | bilinmiyor  (YEMEK ÇEKİ/KARTI, Multinet, Sodexo/Pluxee, Edenred, Setcard, Metropol → yemek_karti)",
  "payment_card": "yemek/hediye kartı markası (ör. Multinet) veya null",
  "card_last4": "kartla ödendiyse slipte/fişte maskeli yazan kart numarasının SON 4 hanesi (ör. 4543 **** **** 6081 → \"6081\"); yoksa null",
  "installments": "kartla TAKSİTLİ ödendiyse taksit sayısı (fişte/slipte 'TAKSİT SAYISI: 3', '3 TAKSİT', '3 AY' gibi), tek çekimse 1",
  "category": "fiş ise şu listeden en uygunu: {categories}",
  "items": [{{"name": "fişteki satır yazısı", "product": "standart ürün adı", "brand": "marka veya null", "qty": 1, "unit": "adet", "unit_price": null, "amount": 0.00, "split": null}}],
  "confidence": "yüksek | orta | düşük"
}}
{split_rule}total = ödenen/ödenecek GENEL TOPLAM (KDV dahil). Türk sayı formatında 1.234,56 = 1234.56.
""" + ITEM_RULES


SPLIT_RULE = """items[].split: ürün şu kategorilerden birine AÇIKÇA giriyorsa o kategorinin adı, değilse null: {cats}.
Fiş kısaltmalarını çöz (ör. "MRLB TCH BLU" = Marlboro sigara). Emin değilsen null.
"""


def parse_document(data: bytes, mime: str, categories: list[str], split_categories: list[str] | None = None,
                   more: list[tuple[bytes, str]] | None = None) -> dict:
    """Fiş mi fatura mı kendisi anlar. Gemini yoksa, kota dolduysa ya da ulaşılamazsa yerel okuma (Tesseract)."""
    from app import ocr

    if not enabled():
        return _local(data, mime, categories, "Yapay zekâ anahtarı yok; yerel okuma kullanıldı", more)
    try:
        return _parse_document_ai(data, mime, categories, split_categories, more)
    except ReceiptError as e:
        if not (ocr.available() or mime == "application/pdf"):
            raise
        return _local(data, mime, categories, str(e), more)


def _local(data: bytes, mime: str, categories: list[str], reason: str, more: list[tuple[bytes, str]] | None = None) -> dict:
    from app import ocr

    try:
        doc = ocr.parse(data, mime, categories, more)
    except ValueError as e:
        raise ReceiptError(f"{reason}. {e}") from e
    doc["note"] = reason
    return doc


def _parse_document_ai(data: bytes, mime: str, categories: list[str], split_categories: list[str] | None = None,
                       more: list[tuple[bytes, str]] | None = None) -> dict:
    """Tek Gemini çağrısı. split_categories: kalem kurallarının kategorileri; model her fiş satırını bunlardan
    birine etiketler (kelime kuralının yakalayamadığı kısaltmalar için)."""
    split_rule = SPLIT_RULE.format(cats=", ".join(split_categories)) if split_categories else ""
    raw, meta = _gemini_json(DOC_PROMPT.format(categories=", ".join(categories), split_rule=split_rule), data, mime,
                             max_output_tokens=8000, more=more)
    if raw.get("doc_type") == "fatura" or raw.get("due_date"):
        return {
            "type": "bill",
            "provider": (raw.get("merchant") or "").strip()[:60] or "Fatura",
            "legal_name": (raw.get("legal_name") or "").strip()[:120] or None,
            "category": BILL_CATEGORY.get(raw.get("bill_type"), "Faturalar"),
            "amount": _num(raw.get("total")),
            "due_date": _iso(raw.get("due_date")),
            "description": " · ".join(x for x in (raw.get("period"), raw.get("subscriber_no") and f"Abone {raw['subscriber_no']}") if x),
            "confidence": raw.get("confidence") or "orta",
        }
    category = raw.get("category") if raw.get("category") in categories else "Diğer"
    return {
        "type": "receipt",
        "merchant": (raw.get("merchant") or "").strip()[:80] or "Fiş",
        "date": _iso(raw.get("date")),
        "total": _num(raw.get("total")),
        "payment": raw.get("payment") or "bilinmiyor",
        "payment_card": (raw.get("payment_card") or "").strip()[:40] or None,
        "installments": _installments(raw.get("installments")),
        "card_last4": card_last4(raw.get("card_last4")),
        "category": category,
        "items": _items(raw),
        "legal_name": (raw.get("legal_name") or "").strip()[:120] or None,
        "address": (raw.get("address") or "").strip()[:160] or None,
        "branch": (raw.get("branch") or "").strip()[:60] or None,
        "confidence": raw.get("confidence") or "orta",
    }


def parse_bill(data: bytes, mime: str) -> dict:
    raw, meta = _gemini_json(BILL_PROMPT, data, mime)
    return {
        "provider": (raw.get("provider") or "").strip()[:60] or "Fatura",
        "category": BILL_CATEGORY.get(raw.get("type"), "Faturalar"),
        "amount": _num(raw.get("amount")),
        "due_date": _iso(raw.get("due_date")),
        "issue_date": _iso(raw.get("issue_date")),
        "description": " · ".join(x for x in (raw.get("period"), raw.get("subscriber_no") and f"Abone {raw['subscriber_no']}") if x),
        "confidence": raw.get("confidence") or "orta",
        "model": meta["model"],
    }


def parse_receipt(image: bytes, mime: str, categories: list[str]) -> dict:
    raw, meta = _gemini_json(PROMPT.format(categories=", ".join(categories)), image, mime)

    d = _iso(raw.get("date"))
    category = raw.get("category") if raw.get("category") in categories else "Diğer"
    return {
        "merchant": (raw.get("merchant") or "").strip()[:80] or "Fiş",
        "date": d,
        "total": _num(raw.get("total")),
        "vat": _num(raw.get("vat")),
        "payment": raw.get("payment") or "bilinmiyor",
        "category": category,
        "items": _items(raw),
        "legal_name": (raw.get("legal_name") or "").strip()[:120] or None,
        "confidence": raw.get("confidence") or "orta",
        "model": meta["model"],
        "tokens": {"input": meta["input"], "output": meta["output"]},
    }


REREAD_PROMPT = """Bu, daha önce okunmuş bir Türkiye alışveriş fişinin fotoğrafı; ilk okumada ürün kalemleri hatalı çıktı
(ad ile fiyat kaymış ya da miktar satırı ayrı ürün sanılmış olabilir). Satır satır dikkatle yeniden oku.
{total_hint}SADECE şu JSON'u döndür:
{{"total": 0.00, "items": [{{"name": "fişteki satır yazısı", "product": "standart ürün adı", "brand": "marka veya null",
"qty": 1, "unit": "adet", "unit_price": null, "amount": 0.00}}], "confidence": "yüksek | orta | düşük"}}
Her ürünün amount'u KENDİ satırının sağındaki tutardır. Türk sayı biçimi 1.234,56 = 1234.56.
""" + ITEM_RULES


def reread_model() -> str:
    """Yeniden okuma modeli: GEMINI_REREAD_MODEL; yoksa varsayılan modelin '-lite' olmayan sürümü (daha güçlü)."""
    base = config.get("GEMINI_MODEL") or DEFAULT_MODEL
    return os.environ.get("GEMINI_REREAD_MODEL") or base.replace("-lite", "")


def reread_items(image: bytes, mime: str, total: float | None) -> dict:
    """Saklı fiş fotoğrafını daha güçlü modelle, fiş toplamını bildirerek yeniden oku. {items, total, model, confidence}."""
    hint = (f"Fişin GENEL TOPLAMI {total:.2f} TL: kalemlerin amount toplamı (indirimler düşülmüş) bunu TUTMALI; tutmuyorsa "
            "kaçan ya da kayan satırı bul.\n") if total else ""
    model = reread_model()
    raw, meta = _gemini_json(REREAD_PROMPT.format(total_hint=hint), image, mime, max_output_tokens=8000, model=model)
    return {"items": _items(raw), "total": _num(raw.get("total")) or total, "model": meta["model"],
            "confidence": raw.get("confidence") or "orta"}
