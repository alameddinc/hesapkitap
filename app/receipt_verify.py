"""Fiş okumasının metinle doğrulanması (yapay zekâsız, bedava): okunan toplam, tarih ve işyeri telefonda çıkarılmış
metinde (Apple Vision / ML Kit, ocr_text) ya da PDF'in metin katmanında geçiyor mu?

Neden: ucuz model temiz fişte toplamı hep doğru okuyor, ama zaten kötü bir fotoğraf daha da bozulunca toplamı
uydurabiliyor (ölçüm 04.11.2026). Toplam metinde yoksa kullanıcıya "Tutarı fişle karşılaştır" denir
(total_check "kontrol") ve kalemli fişte güçlü modelle yeniden okuma tetiklenir (receipt_check.needs_reread).

Çıktı (`verify`): {"total_check": "ok" | "kontrol" | None, "date_in_text": bool | None, "merchant_in_text": bool | None}
None = karar verilemez (metin yok ya da alan okunmamış). Toplam Türk biçimleriyle aranır: "1.795,70", "1795,70",
"*1.795,70", "1 795,70", "1795.70"; kuruşsuz yazım ("1795") sayılmaz (fişte tutar her zaman kuruşlu basılır, kuruşsuz
sayı adet/gramaj olabilir).
"""
import re

from app import money

_SPACES = re.compile(r"[   \t]")


def _clean(text: str | None) -> str:
    return _SPACES.sub(" ", text or "")


def amount_pattern(total: float) -> re.Pattern:
    """Tutarın metindeki Türk biçimli yazımları: binlik ayırıcı nokta, boşluk, kesme ya da hiç; kuruş virgül (ya da
    nokta) ile; virgülden sonra OCR boşluğu olabilir. Önünde/arkasında rakam olamaz ("11.795,70" 1.795,70 sayılmaz)."""
    kurus = money.kurus(abs(total))
    whole, frac = divmod(kurus, 100)
    digits = str(whole)
    groups = []
    while digits:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    body = r"[.\s']?".join(groups)
    return re.compile(rf"(?<![\d.,]){body}\s?[,.]\s?{frac:02d}(?!\d)")


def total_in_text(total: float | None, text: str | None) -> bool | None:
    if not isinstance(total, (int, float)) or total <= 0 or not (text or "").strip():
        return None
    return bool(amount_pattern(total).search(_clean(text)))


def date_in_text(iso: str | None, text: str | None) -> bool | None:
    """ISO tarih ("2026-10-31") metinde gün.ay.yıl biçiminde (ayırıcı . / -, yıl 4 ya da 2 hane, gün/ay tek haneli de
    olabilir) geçiyor mu."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m or not (text or "").strip():
        return None
    y, mo, d = m.groups()
    pat = rf"(?<!\d)0?{int(d)}\s?[./-]\s?0?{int(mo)}\s?[./-]\s?(?:{y}|{y[2:]})(?!\d)"
    return bool(re.search(pat, _clean(text)))


_GENERIC = {"MARKET", "MARKETLERI", "MAGAZA", "MAGAZALARI", "MAGAZACILIK", "GIDA", "TICARET", "SANAYI", "LIMITED",
            "SIRKETI", "ANONIM", "PAZARLAMA", "HIZMETLERI", "TURIZM", "SUBESI", "SUBE"}


def merchant_in_text(names: list[str | None], text: str | None) -> bool | None:
    """İşyeri adının (ya da unvanının) en az 4 harfli, genel olmayan bir kelimesi metinde geçiyor mu (büyük harf,
    Türkçe karakterler sadeleşmiş)."""
    from app.textnorm import normalize

    if not (text or "").strip():
        return None
    words = {w for n in names if n for w in re.sub(r"[^A-Z0-9 ]", " ", normalize(n)).split()
             if len(w) >= 4 and w not in _GENERIC and not w.isdigit()}
    if not words:
        return None
    hay = " " + re.sub(r"[^A-Z0-9 ]", " ", normalize(_clean(text))) + " "
    return any(f" {w} " in hay or w in hay.replace(" ", "") for w in words)


def verify(doc: dict, text: str | None) -> dict:
    """Fiş sonucunu metinle karşılaştır. Metin yoksa hepsi None."""
    has_text = bool((text or "").strip())
    t = total_in_text(doc.get("total"), text) if has_text else None
    return {"total_check": None if t is None else ("ok" if t else "kontrol"),
            "date_in_text": date_in_text(doc.get("date"), text) if has_text else None,
            "merchant_in_text": merchant_in_text([doc.get("merchant"), doc.get("legal_name")], text) if has_text else None}


def apply(doc: dict, text: str | None) -> dict:
    """verify sonucunu fişe yaz (total_check, text_match {date, merchant}); metin yoksa total_check None, text_match yok."""
    v = verify(doc, text)
    doc["total_check"] = v["total_check"]
    if (text or "").strip():
        doc["text_match"] = {"date": v["date_in_text"], "merchant": v["merchant_in_text"]}
    else:
        doc.pop("text_match", None)
    return doc
