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

PROMPT = """Bu bir Türkiye'de alınmış alışveriş fişi fotoğrafı. Bilgileri çıkar ve SADECE şu JSON'u döndür:
{{
  "merchant": "işyeri adı (fişin en üstündeki ticari unvan veya marka, kısa)",
  "date": "YYYY-MM-DD (fişteki tarih; okunamazsa null)",
  "total": 0.00,
  "vat": 0.00,
  "payment": "nakit | kart | bilinmiyor",
  "category": "şu listeden en uygun olan: {categories}",
  "items": [{{"name": "ürün", "amount": 0.00}}],
  "confidence": "yüksek | orta | düşük"
}}
Kurallar: total = fişteki GENEL TOPLAM / TOPLAM (KDV dahil, ödenen tutar). Türk sayı formatında 1.234,56 = 1234.56.
items en fazla 15 kalem. Fiş değilse veya okunamıyorsa total null ve confidence "düşük" döndür."""


class ReceiptError(Exception):
    pass


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


def _gemini_json(prompt: str, data: bytes, mime: str, max_output_tokens: int | None = None) -> tuple[dict, dict]:
    key = config.get("GEMINI_API_KEY")
    if not key:
        raise ReceiptError("Gemini API anahtarı tanımlı değil (Ayarlar → Anahtarlar)")
    if len(data) > MAX_BYTES:
        raise ReceiptError("Dosya çok büyük (en fazla 8 MB)")
    model = config.get("GEMINI_MODEL") or DEFAULT_MODEL
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
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
    usage = resp.get("usageMetadata", {})
    return _extract_json(text), {"model": model, "input": usage.get("promptTokenCount"),
                                 "output": usage.get("candidatesTokenCount")}


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
  "merchant": "işyeri veya kurum adı, kısa",
  "date": "YYYY-MM-DD fiş tarihi / fatura düzenleme tarihi veya null",
  "total": 0.00,
  "due_date": "YYYY-MM-DD SON ÖDEME TARİHİ (sadece fatura) veya null",
  "bill_type": "elektrik | dogalgaz | su | internet | telefon | aidat | diger | null",
  "period": "fatura dönemi veya null",
  "subscriber_no": "abone/tesisat no veya null",
  "payment": "nakit | kart | bilinmiyor",
  "category": "fiş ise şu listeden en uygunu: {categories}",
  "items": [{{"name": "ürün", "amount": 0.00}}],
  "confidence": "yüksek | orta | düşük"
}}
total = ödenen/ödenecek GENEL TOPLAM (KDV dahil). Türk sayı formatında 1.234,56 = 1234.56. items en fazla 15 (sadece fiş)."""


def parse_document(data: bytes, mime: str, categories: list[str]) -> dict:
    """Fiş mi fatura mı kendisi anlar; tek Gemini çağrısı."""
    raw, meta = _gemini_json(DOC_PROMPT.format(categories=", ".join(categories)), data, mime)
    if raw.get("doc_type") == "fatura" or raw.get("due_date"):
        return {
            "type": "bill",
            "provider": (raw.get("merchant") or "").strip()[:60] or "Fatura",
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
        "category": category,
        "items": [{"name": str(i.get("name", ""))[:60], "amount": _num(i.get("amount"))}
                  for i in (raw.get("items") or [])[:15] if isinstance(i, dict)],
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
        "items": [{"name": str(i.get("name", ""))[:60], "amount": _num(i.get("amount"))}
                  for i in (raw.get("items") or [])[:15] if isinstance(i, dict)],
        "confidence": raw.get("confidence") or "orta",
        "model": meta["model"],
        "tokens": {"input": meta["input"], "output": meta["output"]},
    }
