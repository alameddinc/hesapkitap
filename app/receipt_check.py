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
from app import money

TOL = 0.02


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
