"""Fiyat artışı uyarıları (fiyat takibi paketinde: plans "fiyatlar").

1. Fiş okunurken / kaydedilirken (check.price_alerts; POST /api/entries yanıtında price_alerts):
   kalemin birim fiyatı, AYNI zincirde (ya da aynı işyerinde) son LOOKBACK_DAYS günde kendi ödediğin son birim fiyattan
   en az RISE_PCT yüksek VE en az MIN_DIFF ₺ fazlaysa
   {index, name, paid, last_paid, last_date, change_pct, chain, note "geçen sefer ₺30"}.
2. Günlük bildirim (app/push.py tür "fiyat", günde en çok bir özet): son FREQUENT_DAYS günde aynı zincirde en az
   FREQUENT_MIN kez aldığın ürün grubunda topluluk ortancası (en az 3 kişi, app/community_prices.py) son ödediğinden en
   az RISE_PCT (ve MIN_DIFF ₺) yüksekse: "Sık aldığın 2 üründe fiyat arttı: Süt · 1 L A101 ₺36 → ₺42".
"""
from datetime import date, timedelta

from app import db, money

RISE_PCT = 10.0
MIN_DIFF = 2.0
LOOKBACK_DAYS = 60
FREQUENT_DAYS = 90
FREQUENT_MIN = 2


def allowed() -> bool:
    from app import book, plans, prefs

    r = book.active()
    return plans.allows(db.current_user(), "fiyatlar") and prefs.enabled("fiyatlar") and \
        not (r and "fiyatlar" in r["hidden_pages"])


def _rise(paid: float, last: float) -> float | None:
    if last <= 0 or paid - last < MIN_DIFF:
        return None
    pct = (paid - last) / last * 100
    return round(pct, 1) if pct >= RISE_PCT else None


def receipt_alerts(items: list[dict], merchant: str | None, exclude_entry: int | None = None,
                   today: date | None = None) -> list[dict]:
    """Fişin kalemlerinde, kendi son fiyatına göre artış (yukarıdaki 1). Paket/sayfa kapalıysa []."""
    from app.receipt_templates import chain_of

    if not items or not merchant or not allowed():
        return []
    names = [it.get("product") or it.get("name") or "" for it in items]
    last = db.last_unit_prices(names, merchant, LOOKBACK_DAYS, exclude_entry, today)
    chain = chain_of(merchant) or merchant.strip()
    out = []
    for i, (it, n) in enumerate(zip(items, names)):
        amt, qty = it.get("amount"), it.get("qty") or 1
        prev = last.get(db.product_key(n)) if n else None
        if not prev or not isinstance(amt, (int, float)) or isinstance(amt, bool) or amt <= 0 or qty <= 0:
            continue
        paid = money.money(amt / qty)
        if (pct := _rise(paid, prev["price"])) is not None:
            out.append({"index": i, "name": it.get("name") or n, "paid": paid, "last_paid": prev["price"],
                        "last_date": prev["date"], "change_pct": pct, "chain": chain,
                        "note": f"geçen sefer {money.tl(prev['price'], trim=True)}"})
    return out


def community_rises(today: date | None = None) -> list[dict]:
    """Sık aldığın ürünlerde topluluk fiyatı artışı (yukarıdaki 2), en çok artan önce:
    [{group, label, chain, last_paid, median, n, change_pct}]."""
    from app import community_prices
    from app.receipt_templates import chain_of

    today = today or date.today()
    since = (today - timedelta(days=FREQUENT_DAYS)).isoformat()
    items = [it for it in db.priced_items() if it["purchased_at"] >= since and it["price_known"] == 1
             and it["unit_price"] > 0]
    if not items:
        return []
    db.attach_groups(items, learn=False)
    per: dict[tuple[str, str], dict] = {}
    for it in items:  # eski→yeni
        chain, g = chain_of(it["merchant"], it.get("legal_name")), it.get("group")
        if not chain or not g or g.get("pending"):
            continue
        st = per.setdefault((chain, g["key"]), {"label": g["label"], "entries": set(), "last": None})
        st["entries"].add(it["entry_id"])
        st["last"] = it["unit_price"]
    freq = {k: v for k, v in per.items() if len(v["entries"]) >= FREQUENT_MIN}
    if not freq:
        return []
    med = community_prices.medians([g for _, g in freq], today=today)
    out = []
    for (chain, g), st in freq.items():
        m = med.get((chain, g))
        if m and (pct := _rise(m["median"], st["last"])) is not None:
            out.append({"group": g, "label": st["label"], "chain": chain, "last_paid": st["last"],
                        "median": m["median"], "n": m["n"], "change_pct": pct})
    return sorted(out, key=lambda x: (-x["change_pct"], x["label"]))


def digest(rises: list[dict], shown: int = 2) -> str:
    """'Sık aldığın 2 üründe fiyat arttı: Süt · 1 L A101 ₺36 → ₺42; Ekmek BİM ₺12 → ₺15'."""
    parts = [f"{r['label']} {r['chain']} {money.tl(r['last_paid'], trim=True)} → {money.tl(r['median'], trim=True)}"
             for r in rises[:shown]]
    more = f" ve {len(rises) - shown} ürün daha" if len(rises) > shown else ""
    return f"Sık aldığın {len(rises)} üründe fiyat arttı: " + "; ".join(parts) + more
