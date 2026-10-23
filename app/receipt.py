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
- "2Adt x 230,00 Latte" → qty=2, unit="adet", amount=460 (satır toplamı).
- Tartılı ürün "0,750 KG x 89,90" → qty=0.75, unit="kg", amount=67.43; litre ise unit="lt".
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
  "items": [{{"name": "fişteki satır yazısı", "product": "standart ürün adı", "qty": 1, "unit": "adet", "amount": 0.00}}],
  "confidence": "yüksek | orta | düşük"
}}
Kurallar: total = fişteki GENEL TOPLAM / TOPLAM (KDV dahil, ödenen tutar). Türk sayı formatında 1.234,56 = 1234.56.
Fiş değilse veya okunamıyorsa total null ve confidence "düşük" döndür.
""" + ITEM_RULES




class ReceiptError(Exception):
    pass


def _shared_key_user() -> dict | None:
    """Ortak (yöneticinin) anahtarını kullanan yönetici-olmayan kullanıcı; kota ona uygulanır."""
    from app import db

    u = db.current_user()
    if not u or u.get("role") == "admin" or config.own_ai_key():
        return None
    return u


def _check_quota() -> None:
    from app import system

    u = _shared_key_user()
    if not u:
        return
    quota, used = system.ai_quota(u), system.ai_used(u["id"])
    if quota is not None and used >= quota:
        raise ReceiptError(f"Bu ayki ücretsiz yapay zekâ okuma hakkın doldu ({used}/{quota}). Ay başında yenilenir; "
                           "beklemek istemezsen Ayarlar'dan kendi Gemini anahtarını girebilirsin.")


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
                 more: list[tuple[bytes, str]] | None = None) -> tuple[dict, dict]:
    """more: aynı belgenin devam görüntüleri (uzun fiş birden fazla fotoğraf)."""
    key = config.get("GEMINI_API_KEY")
    if not key:
        raise ReceiptError("Gemini API anahtarı tanımlı değil (Ayarlar → Anahtarlar)")
    _check_quota()
    docs = [(data, mime), *(more or [])]
    if sum(len(d) for d, _ in docs) > MAX_BYTES:
        raise ReceiptError("Dosya çok büyük (en fazla 15 MB)")
    model = config.get("GEMINI_MODEL") or DEFAULT_MODEL
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


def _installments(v) -> int:
    try:
        n = int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 1
    return n if 2 <= n <= 36 else 1


def _items(raw: dict) -> list[dict]:
    """Ürün satırları; "2Adt x 230,00" gibi adetler qty olarak gelir."""
    out = []
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
                    "amount": _num(i.get("amount")), "split": split})
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
  "installments": "kartla TAKSİTLİ ödendiyse taksit sayısı (fişte/slipte 'TAKSİT SAYISI: 3', '3 TAKSİT', '3 AY' gibi), tek çekimse 1",
  "category": "fiş ise şu listeden en uygunu: {categories}",
  "items": [{{"name": "fişteki satır yazısı", "product": "standart ürün adı", "brand": "marka veya null", "qty": 1, "unit": "adet", "amount": 0.00, "split": null}}],
  "confidence": "yüksek | orta | düşük"
}}
{split_rule}total = ödenen/ödenecek GENEL TOPLAM (KDV dahil). Türk sayı formatında 1.234,56 = 1234.56.
""" + ITEM_RULES


SPLIT_RULE = """items[].split: ürün şu kategorilerden birine AÇIKÇA giriyorsa o kategorinin adı, değilse null: {cats}.
Fiş kısaltmalarını çöz (ör. "MRLB TCH BLU" = Marlboro sigara). Emin değilsen null.
"""


def parse_document(data: bytes, mime: str, categories: list[str], split_categories: list[str] | None = None,
                   more: list[tuple[bytes, str]] | None = None) -> dict:
    """Fiş mi fatura mı kendisi anlar; tek Gemini çağrısı. split_categories: kalem kurallarının kategorileri;
    model her fiş satırını bunlardan birine etiketler (kelime kuralının yakalayamadığı kısaltmalar için)."""
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
