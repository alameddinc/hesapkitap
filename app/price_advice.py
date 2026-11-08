"""Fiyat ekranlarının "ne yapmalıyım?" katmanı (price_advice, price_suggest yetenekleri).

attach(products): /api/prices products[]'ına
- market: bool — piyasa karşılaştırması anlamlı mı: ürün Kafe kategorisinde değil, kayıt Kafe & Restoran / Yemek
  Siparişi değil ve en az bir alım bilinen bir market zincirinde (receipt_templates.CHAINS "Market & Gıda"). Kafede,
  lokantada, mahalle dükkânında alınan ürüne market fiyatı gösterilmez; kendi mağazalarını karşılaştırır.
- market_ref: {median, min, cheapest_chain, day, source} | null — grubun en yeni piyasa referansı (app/market_ref.py
  series; son REF_DAYS gün): grafikte tek kesik çizgi "Piyasa ortancası".
- advice: tek öneri ya da null:
  cheaper_at {chain, price, paid, diff, qty, saving, unit_price_per?, per_unit?, source, n?, date?}: son ödediğin
    birim fiyattan en az MIN_DIFF ₺ ve MIN_PCT ucuz bir zincir (kendi fişin değil: topluluk / Market Fiyatı);
    qty = tipik alım adedin (ortanca), saving = diff × qty;
  good_price {paid, median, pct}: ödediğin piyasa ortancasından yüksek değil;
  store_compare {stores [{store, price, date}] ucuzdan, cheapest, diff}: market dışı üründe en az iki mağazan varsa.
suggest(q): kendi fişlerindeki ürün adları + genel grup adları; did_you_mean (neredeyse aynı yazılış).
Kişinin kaydı burada hiçbir zaman değişmez: yalnız öneri.
"""
from datetime import date, timedelta
from statistics import median

MIN_DIFF = 1.0
MIN_PCT = 3.0
REF_DAYS = 14
NON_MARKET_CATEGORIES = {"Kafe & Restoran", "Yemek Siparişi"}


def _market_chain(merchant: str | None, legal_name: str | None = None) -> bool:
    from app.receipt_templates import CHAINS, _demo_chains, chain_of

    ch = chain_of(merchant, legal_name)
    return bool(ch) and (any(b == ch and cat == "Market & Gıda" for b, _, cat in CHAINS)
                         or ch in _demo_chains().values())  # canlı demoda kurgu zincirler ("Market A" …) de market


def is_market(p: dict) -> bool:
    """p["_entries"]: alımların (kayıt kategorisi, unvan) çiftleri (db.products ekler, uç siler)."""
    if (p.get("category") or (p.get("group") or {}).get("category")) == "Kafe":
        return False
    hist = p.get("_entries") or []
    if hist and all(cat in NON_MARKET_CATEGORIES for cat, _ in hist):
        return False
    stores = [s["store"] for s in p.get("stores") or []] + [h.get("store") for h in p.get("history") or []]
    return any(_market_chain(s) for s in stores if s) or any(_market_chain(None, n) for _, n in hist if n)


def _qty(p: dict) -> int:
    qs = [h.get("qty") or 1 for h in p.get("history") or [] if h.get("price") is not None]
    if not qs:
        return 1
    q = median(qs)
    return max(1, int(round(q))) if abs(q - round(q)) < 1e-6 or q >= 1 else 1


def attach(products: list[dict], today: date | None = None) -> None:
    """products[] (yalnız üst düzey) → market, market_ref, advice (yukarıda)."""
    from app import market_ref, money, price_sources

    today = today or date.today()
    for p in products:
        p["market"] = is_market(p)
        p["market_ref"] = None
        p["advice"] = None
    want = [p for p in products if p["market"] and p.get("group") and not p["group"].get("pending")
            and p.get("last_price")]
    keys = sorted({p["group"]["key"] for p in want})
    offers = price_sources.best_offers(keys, limit=10) if keys else {}
    since = (today - timedelta(days=REF_DAYS)).isoformat()
    ser = market_ref.series(keys, since) if keys else {}
    for p in want:
        k = p["group"]["key"]
        if (by := ser.get(k)):
            d = max(by)
            r = by[d]
            p["market_ref"] = {"median": r["median"], "min": r["min"], "cheapest_chain": r["cheapest_chain"], "day": d,
                               "source": r["source"]}
        paid = p["last_price"]
        cand = [o for o in offers.get(k, []) if o["source"] != "own"]
        best = min(cand, key=lambda o: (o["price"], o["chain"])) if cand else None
        if best and paid - best["price"] >= MIN_DIFF and (paid - best["price"]) / paid * 100 >= MIN_PCT:
            q = _qty(p)
            diff = money.money(paid - best["price"])
            p["advice"] = {"kind": "cheaper_at", "chain": best["chain"], "price": best["price"], "paid": paid,
                           "diff": diff, "qty": q, "saving": money.money(diff * q), "source": best["source"],
                           **{x: best[x] for x in ("unit_price_per", "per_unit", "n", "date") if x in best}}
        elif p["market_ref"] and paid <= p["market_ref"]["median"]:
            med = p["market_ref"]["median"]
            p["advice"] = {"kind": "good_price", "paid": paid, "median": med,
                           "pct": round((paid - med) / med * 100, 1) if med else 0.0}
    for p in products:
        if p["market"] or p["advice"]:
            continue
        st = [s for s in p.get("stores") or [] if s.get("price") is not None]
        if len(st) >= 2:
            st = sorted(st, key=lambda s: (s["price"], s["store"]))
            if st[-1]["price"] - st[0]["price"] < MIN_DIFF:  # her yerde aynı fiyat: söylenecek bir şey yok
                continue
            p["advice"] = {"kind": "store_compare", "cheapest": st[0]["store"],
                           "diff": money.money(st[-1]["price"] - st[0]["price"]),
                           "stores": [{"store": s["store"], "price": s["price"], "date": s.get("date")} for s in st]}


# ---------------------------------------------------------------- öneri (fiş kalemi / elle gider adı)

SUGGEST_MAX = 8


def suggest(q: str, limit: int = SUGGEST_MAX) -> dict:
    """{items: [{name, group_key, group_label, last_price, last_store, count}], did_you_mean: {name, group_label} |
    null}. Önce kendi fişlerindeki adlar (her q kelimesi adın bir kelimesinin öneki; çok alınan önce), sonra genel grup
    adları (sözlük). did_you_mean: q (≥ 4 harf) bilinen bir ada neredeyse aynı (db.near_same) ama aynı değilse."""
    from app import db, product_groups

    limit = max(1, min(int(limit or SUGGEST_MAX), 20))
    qk = db.product_key(q or "")
    if not qk:
        return {"items": [], "did_you_mean": None}
    qt = qk.split()
    items = db.priced_items()
    names: dict[str, dict] = {}
    for it in items:  # eski→yeni: son fiyat ve mağaza en yenisi
        n = (it["product"] or it["name"] or "").strip()
        k = db.product_key(n)
        if not k:
            continue
        e = names.setdefault(k, {"spell": {}, "count": 0, "last_price": None, "last_store": None, "it": it})
        e["spell"][n] = e["spell"].get(n, 0) + 1
        e["count"] += 1
        if it["price_known"]:
            e["last_price"], e["last_store"] = it["unit_price"], it["merchant"]
        e["it"] = it
    # neredeyse aynı yazılışlar tek ad (en sık)
    merged: dict[str, dict] = {}
    for k in sorted(names, key=lambda k: (-names[k]["count"], k)):
        rep = next((r for r in merged if db.near_same(k, r)), None)
        if rep:
            m = merged[rep]
            m["count"] += names[k]["count"]
            for n, c in names[k]["spell"].items():
                m["spell"][n] = m["spell"].get(n, 0) + c
        else:
            merged[k] = {**names[k], "spell": dict(names[k]["spell"])}

    def prefix_hit(k: str) -> bool:
        toks = k.split()
        return all(any(t.startswith(w) for t in toks) for w in qt)

    hits = sorted((k for k in merged if prefix_hit(k)), key=lambda k: (not k.startswith(qk), -merged[k]["count"], k))
    near = None
    if len(qk) >= 4:
        cands = [k for k in merged if k != qk and db.near_same(qk, k)]
        if cands and qk not in merged:
            near = max(cands, key=lambda k: (merged[k]["count"], k))
    chosen = hits[:limit]
    if near and near not in chosen:
        chosen = [near, *chosen][:limit]
    groups = product_groups.groups_for([{"name": merged[k]["it"]["name"], "product": merged[k]["it"]["product"],
                                         "brand": merged[k]["it"]["brand"], "unit": merged[k]["it"]["unit"]}
                                        for k in chosen], learn=False) if chosen else []
    out = []
    for k, g in zip(chosen, groups):
        m = merged[k]
        out.append({"name": max(m["spell"], key=lambda n: (m["spell"][n], n)),
                    "group_key": g["key"] if g and not g.get("pending") else None,
                    "group_label": g["label"] if g and not g.get("pending") else None,
                    "last_price": m["last_price"], "last_store": m["last_store"], "count": m["count"]})
    if len(out) < limit:  # genel ürün adları (sözlük): "Süt", "Soğuk Çay"
        have = {db.product_key(o["name"]) for o in out}
        for gname in product_groups.GENERICS:
            gk = db.product_key(gname)
            if gk in have or not all(any(t.startswith(w) for t in gk.split()) for w in qt):
                continue
            out.append({"name": gname, "group_key": product_groups.slug(gname), "group_label": gname,
                        "last_price": None, "last_store": None, "count": 0})
            have.add(gk)
            if len(out) >= limit:
                break
    dym = None
    if near:
        o = next(o for o in out if db.near_same(db.product_key(o["name"]), near))
        dym = {"name": o["name"], "group_label": o["group_label"]}
    return {"items": out, "did_you_mean": dym}
