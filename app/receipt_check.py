"""Fiş kalemlerinin denetimi: kalemler fiş toplamını tutuyor mu, adet × birim fiyat satır tutarını tutuyor mu, fiyatlar
bir satır kaymış mı? /api/receipts/parse, /api/documents/parse ve kayıtlı fişin kalemleri bunu döndürür; istemci tutmayan
kalemi işaretler ve önerilen kaydırmayı tek dokunuşla uygulatır (sonra PUT /api/entries/{id}/receipt-items).

Çıktı:
    {"items_total", "receipt_total", "diff" (fiş − kalemler), "ok",
     "items": [{"index", "expected" (adet × birim, bilinmiyorsa null), "mismatch"}],
     "shift_start" (art arda tutmayan kalemlerin başladığı sıra, yoksa null),
     "suggested_shift": null | {"from_index", "direction": "up"|"down", "amounts": [...], "items_total", "diff"}}
direction: "down" = from_index'ten itibaren her fiyat bir alt satıra iner (o satıra boşluk: adet × birim ile dolar);
"up" = her fiyat bir üst satıra çıkar.

Yuvarlama app/money.py ile (yarım yukarı); web karşılığı (web/src/components/receipt/check.ts) aynı kuralı uygular.
"""
import re

from app import money

TOL = 0.02
SUM_TOL = 0.05  # yeniden okuma kararında kalem toplamı ile fiş toplamı arasındaki pay (TL)


def _expected(it: dict) -> float | None:
    up, q = it.get("unit_price"), it.get("qty") or 1
    return money.money(q * up) if isinstance(up, (int, float)) and up > 0 else None


def _mismatches(items: list[dict], amounts: list) -> list[bool]:
    out = []
    for it, a in zip(items, amounts):
        e = _expected(it)
        out.append(e is not None and (a is None or abs(a - e) > TOL))
    return out


def _fill(items: list[dict], amounts: list) -> list:
    """Boş kalan fiyatı (kaydırmada açılan yuva) adet × birimle doldur."""
    return [a if a is not None else _expected(it) for it, a in zip(items, amounts)]


def _total(amounts: list) -> float:
    return money.money(sum(a for a in amounts if isinstance(a, (int, float))))


def check(items: list[dict], receipt_total: float | None) -> dict:
    amounts = [it.get("amount") for it in items]
    mis = _mismatches(items, amounts)
    items_total = _total(amounts)
    diff = money.money(receipt_total - items_total) if receipt_total is not None else None
    shift_start = next((i for i, m in enumerate(mis) if m), None)
    suggested = None
    if shift_start is not None:  # kaydırma ancak adet × birim tutmayan kalem varsa önerilir (toplam farkı tek başına yetmez)
        base = (sum(mis), abs(diff) if diff is not None else 0.0)
        best = None
        for k in range(shift_start + 1):
            for direction in ("down", "up"):
                tail = amounts[k:]
                moved = [None, *tail[:-1]] if direction == "down" else [*tail[1:], None]
                cand = _fill(items, amounts[:k] + moved)
                score = (sum(_mismatches(items, cand)), abs(receipt_total - _total(cand)) if receipt_total is not None else 0.0)
                if score < base and (best is None or score < best[0]):
                    best = (score, k, direction, cand)
        if best:
            _, k, direction, cand = best
            suggested = {"from_index": k, "direction": direction, "amounts": cand, "items_total": _total(cand),
                         "diff": money.money(receipt_total - _total(cand)) if receipt_total is not None else None}
    return {"items_total": items_total, "receipt_total": receipt_total, "diff": diff,
            "ok": not any(mis) and (diff is None or abs(diff) <= TOL),
            "items": [{"index": i, "expected": _expected(it), "mismatch": m} for i, (it, m) in enumerate(zip(items, mis))],
            "shift_start": shift_start, "suggested_shift": suggested}


_SIZE_ONLY = re.compile(r"^[\d.,\s]*(?:(?:G|GR|KG|ML|L|LT|CL|CC|AD|ADT|ADET|X\s*\d+)\.?\s*)*$", re.I)
_JOINERS = "-/&+,("


def continuation_like(name: str) -> bool:
    """Ürün adının devamı gibi görünen satır: 2'den az harf ("SU" gerçek ürün), yalnız gramaj/adet ("500 GR", "X6"), küçük harfle ya da
    bağlaçla ("-", "/", "&", "+", ",", "(") başlıyor."""
    s = (name or "").strip()
    if not s:
        return True
    letters = sum(ch.isalpha() for ch in s)
    return letters < 2 or bool(_SIZE_ONLY.fullmatch(s)) or s[0].islower() or s[0] in _JOINERS


def split_suspects(items: list[dict]) -> int:
    """İki satıra bölünmüş ürün belirtisi sayısı. Ardışık (önceki, sonraki) her kalem çifti için belirti:
    (a) ikisinden birinin fiyatı yok (fiyatsız kalem = komşusunun adının parçası), ya da
    (b) sonrakinin adı devam gibi (`continuation_like`), ya da önceki ad bağlaçla bitiyor ("-", "/", "&", "+", ",").
    Her sonraki kalem en çok bir kez sayılır. Tek kalemli fişte fiyatsız kalem de bir belirtidir. Hem yeniden okuma
    tetiği (needs_reread) hem kıyas (app/receipt_bench.py structure) bunu kullanır."""
    n = 0
    if len(items) == 1 and items[0].get("amount") is None:
        return 1
    for prev, cur in zip(items, items[1:]):
        p, c = (prev.get("name") or "").strip(), (cur.get("name") or "").strip()
        if prev.get("amount") is None or cur.get("amount") is None or continuation_like(c) or (p and p[-1] in _JOINERS):
            n += 1
    return n


def inconsistent(items: list[dict], chk: dict) -> list[str]:
    """Kalemler fişi açıkça tutmuyor: "fiyatsiz" (fiyatsız kalem) ve/veya "toplam" (kalem toplamı fiş toplamından
    ±SUM_TOL'dan fazla sapıyor). Eski metin yolunun (HESAPKITAP_RECEIPT_TEXT_FIRST=1) fotoğraf yedeği yalnız buna bakar."""
    if not items:
        return []
    out = []
    if any(it.get("amount") is None for it in items):
        out.append("fiyatsiz")
    diff = chk.get("diff")
    if diff is not None and abs(diff) > SUM_TOL:
        out.append("toplam")
    return out


def reread_reasons(items: list[dict], chk: dict, total_check: str | None = None) -> list[str]:
    """Fişi güçlü modelle yeniden okuma nedenleri (boşsa okunmaz):
    - "fiyatsiz": fiyatsız kalem var (satır kayması belirtisi);
    - "toplam": kalem toplamı (indirim satırları dahil) fiş toplamından ±SUM_TOL'dan fazla sapıyor;
    yalnız kalemli fişte (2+ kalem; benzin, kart slibi gibi kalemsiz/tek kalemli fişte gereksiz maliyet) ayrıca:
    - "birim": adet × birim fiyat satır tutarını tutmayan kalem (check.items[].mismatch);
    - "bolunmus": iki satıra bölünmüş ürün belirtisi (split_suspects);
    - "metin": toplam telefon/PDF metninde geçmiyor (app/receipt_verify.py total_check "kontrol"; metin yoksa None).
    Ölçüm 04.11.2026: kalemli 12 fişin 5'inde ilk okumanın kalemleri yanlış hizalıydı, yalnız toplam farkı tetiklerken
    biri yakalanıyordu."""
    out = inconsistent(items, chk)
    if len(items) >= 2:
        if any(i.get("mismatch") for i in chk.get("items") or []):
            out.append("birim")
        if split_suspects(items):
            out.append("bolunmus")
        if total_check == "kontrol":
            out.append("metin")
    return out


def needs_reread(items: list[dict], chk: dict, total_check: str | None = None) -> bool:
    """Fişi güçlü modelle yeniden okumak gerekir mi? Nedenler: reread_reasons."""
    return bool(reread_reasons(items, chk, total_check))


def score(chk: dict) -> tuple[int, float]:
    """Kalemlerin fişi tutma puanı (küçük iyi): (adet × birim tutmayan kalem, |fiş − kalem toplamı|)."""
    return sum(1 for i in chk["items"] if i["mismatch"]), abs(chk["diff"]) if chk["diff"] is not None else 0.0


def reread_hint(doc: dict) -> float | None:
    """Yeniden okumaya bildirilecek fiş toplamı: toplam metinde yoksa (total_check "kontrol") şüphelidir, bildirilmez
    (model kalemleri uydurma toplama uydurmasın); güçlü model toplamı kendisi okur."""
    return None if doc.get("total_check") == "kontrol" else doc.get("total")


def take_reread(doc: dict, again: dict, text: str | None = None) -> bool:
    """Yeniden okuma sonucunu yalnız daha iyiyse al (doc yerinde değişir; alındıysa True).
    - İlk toplam metinde yoktu ve yeni toplam metinde VAR: yeni toplam + kalemler alınır (total_check "ok").
    - Yoksa toplam değişmez; yeni kalemler ilk toplama göre daha az tutmayan kalem ve daha küçük farkla (biri kesin
      iyi, öteki kötü değil) tutuyorsa alınır."""
    from app import receipt_verify

    items = again.get("items") or []
    if not items:
        return False
    new_total = again.get("total")
    if doc.get("total_check") == "kontrol" and new_total and new_total != doc.get("total") and \
            receipt_verify.total_in_text(new_total, text):
        doc.update(total=new_total, items=items, check=check(items, new_total), total_check="ok")
        return True
    chk2 = check(items, doc.get("total"))
    (m1, d1), (m2, d2) = score(doc.get("check") or check(doc.get("items") or [], doc.get("total"))), score(chk2)
    if (m2 < m1 and d2 <= d1) or (m2 <= m1 and d2 < d1):
        doc["items"], doc["check"] = items, chk2
        return True
    return False
