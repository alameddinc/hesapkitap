"""Alışveriş listesi (Premium; ortak defterde paylaşılır): defter veritabanında shopping_list (app/db.py SCHEMA).

Ortak defterde herkes aynı listeyi görür; "görür ve ekler" ve "tam yetki" üyeler ekler, işaretler, siler (app/book.py
MEMBER_WRITES), "sadece görür" üye yalnız okur. Satırın metni ürün grubuna çözülür (app/product_groups.py: önce kural,
çözülemezse geçici grup ve arka planda toplu yapay zekâ; sonraki okumada öğrenilen gruba geçer).

Plan (plan()): işaretlenmemiş her satırın en ucuz zinciri (app/price_sources.py: kendi fişlerin + topluluk ortancası)
ve "hepsini tek yerden" için zincir başına toplam: {chain, total, covered, missing, missing_ids} — en çok satırı
karşılayan önce, eşitse ucuz olan. split_total: her satır kendi en ucuz zincirinden alınırsa toplam.
"""
from app import db

MAX_ITEMS = 200
TEXT_MAX = 80


def _people() -> dict[int, str]:
    from app import book

    try:
        return {p["id"]: p["name"] for p in book.people()}
    except Exception:  # noqa: BLE001 - kişi adı yalnız gösterim
        return {}


def _resolve(texts: list[str]) -> list[dict | None]:
    from app import product_groups

    return product_groups.groups_for([{"name": t} for t in texts])


def _out(r: dict, group: dict | None, names: dict[int, str]) -> dict:
    return {"id": r["id"], "text": r["text"], "qty": r["qty"], "done": bool(r["done"]), "group": group,
            "group_key": group["key"] if group else None,
            "added_by": r["added_by"], "added_by_name": names.get(r["added_by"]), "created_at": r["created_at"],
            "done_at": r["done_at"]}


def list_items() -> list[dict]:
    """Liste (önce işaretlenmemişler, eklenme sırasıyla). Geçici grubu öğrenilmiş satırın group_key'i güncellenir."""
    with db.connect() as c:
        rs = db.rows(c.execute("SELECT * FROM shopping_list ORDER BY done, id"))
    groups = _resolve([r["text"] for r in rs]) if rs else []
    stale = [(g["key"] if g else None, r["id"]) for r, g in zip(rs, groups) if (g["key"] if g else None) != r["group_key"]]
    if stale:
        with db.connect() as c:
            c.executemany("UPDATE shopping_list SET group_key = ? WHERE id = ?", stale)
    if any(g and g.get("pending") for g in groups):
        from app import product_groups

        product_groups.kick()
    names = _people()
    return [_out(r, g, names) for r, g in zip(rs, groups)]


def _clean_text(text: str) -> str:
    t = " ".join((text or "").split())[:TEXT_MAX]
    if not t:
        raise ValueError("Ne alınacağını yaz")
    return t


def _clean_qty(qty) -> float:
    q = float(qty)
    if not 0 < q <= 999:
        raise ValueError("Adet 0 ile 999 arasında olmalı")
    return round(q, 3)


def add(text: str, qty: float = 1, uid: int | None = None) -> dict:
    t, q = _clean_text(text), _clean_qty(qty)
    with db.connect() as c:
        if c.execute("SELECT COUNT(*) FROM shopping_list").fetchone()[0] >= MAX_ITEMS:
            raise ValueError(f"Listede en fazla {MAX_ITEMS} satır olabilir")
    g = _resolve([t])[0]
    with db.connect() as c:
        rid = c.execute("INSERT INTO shopping_list (text, group_key, qty, added_by) VALUES (?, ?, ?, ?)",
                        (t, g["key"] if g else None, q, uid)).lastrowid
        r = dict(c.execute("SELECT * FROM shopping_list WHERE id = ?", (rid,)).fetchone())
    if g and g.get("pending"):
        from app import product_groups

        product_groups.kick()
    return _out(r, g, _people())


def update(rid: int, data: dict) -> dict | None:
    """text, qty, done (işaretlenince done_at bugünün zamanı, kaldırılınca boş)."""
    sets: dict = {}
    if data.get("text") is not None:
        sets["text"] = _clean_text(data["text"])
        g = _resolve([sets["text"]])[0]
        sets["group_key"] = g["key"] if g else None
    if data.get("qty") is not None:
        sets["qty"] = _clean_qty(data["qty"])
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM shopping_list WHERE id = ?", (rid,)).fetchone():
            return None
        if sets:
            c.execute(f"UPDATE shopping_list SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), rid))
        if data.get("done") is not None:
            c.execute("UPDATE shopping_list SET done = ?, done_at = CASE WHEN ? THEN CURRENT_TIMESTAMP END WHERE id = ?",
                      (int(bool(data["done"])), int(bool(data["done"])), rid))
        r = dict(c.execute("SELECT * FROM shopping_list WHERE id = ?", (rid,)).fetchone())
    return _out(r, _resolve([r["text"]])[0], _people())


def delete(rid: int) -> bool:
    with db.connect() as c:
        return c.execute("DELETE FROM shopping_list WHERE id = ?", (rid,)).rowcount > 0


def clear_done() -> int:
    with db.connect() as c:
        return c.execute("DELETE FROM shopping_list WHERE done = 1").rowcount


def plan() -> dict:
    """{items: [{id, text, qty, group, best: Offer | null, offers: [Offer ≤5]}], chains: [{chain, total, covered,
    missing, missing_ids}], split_total, priced, unpriced_ids}. Offer: price_sources (chain, price, source, date?, n?,
    brand?). total = Σ adet × birim fiyat (o zincirde fiyatı bilinen satırlar)."""
    from app import money, price_sources

    items = [i for i in list_items() if not i["done"]]
    keys = [i["group"]["key"] for i in items if i["group"]]
    best = price_sources.best_offers(keys, limit=50)
    rows, chains = [], {}
    for it in items:
        offers = best.get(it["group"]["key"], []) if it["group"] else []
        rows.append({**{k: it[k] for k in ("id", "text", "qty", "group")}, "best": offers[0] if offers else None,
                     "offers": offers[:5]})
        for o in offers:
            ch = chains.setdefault(o["chain"], {"chain": o["chain"], "total": 0.0, "covered": 0, "ids": set()})
            ch["total"] += it["qty"] * o["price"]
            ch["covered"] += 1
            ch["ids"].add(it["id"])
    all_ids = [it["id"] for it in items]
    out_chains = sorted(({"chain": ch["chain"], "total": money.money(ch["total"]), "covered": ch["covered"],
                          "missing": len(all_ids) - ch["covered"],
                          "missing_ids": [i for i in all_ids if i not in ch["ids"]]} for ch in chains.values()),
                        key=lambda x: (-x["covered"], x["total"], x["chain"]))
    priced = [r for r in rows if r["best"]]
    return {"items": rows, "chains": out_chains,
            "split_total": money.money(sum(r["qty"] * r["best"]["price"] for r in priced)),
            "priced": len(priced), "unpriced_ids": [r["id"] for r in rows if not r["best"]]}


# ---------------------------------------------------------------- Nereye gideyim? (en iyi 1 / 2 / 3 durak)

MAX_STOPS = 3
MAX_CHAINS = 10            # en çok satırı karşılayan 10 zincir: C(10,1)+C(10,2)+C(10,3) = 175 birleşim
DEFAULT_STOP_COST = 100.0  # ekstra durak için en az tasarruf (₺; kişi tercihi meta.ui.stop_cost)


def stop_cost_pref() -> float:
    from app import prefs

    v = prefs.ui().get("stop_cost")
    return float(v) if isinstance(v, (int, float)) and v >= 0 else DEFAULT_STOP_COST


def _line(it: dict, o: dict) -> dict:
    """Plan satırı: birim teklif × adet."""
    from app import money

    out = {"id": it["id"], "text": it["text"], "qty": it["qty"],
           "group_label": it["group"]["label"] if it["group"] else None,
           "price": o["price"], "line_total": money.money(it["qty"] * o["price"]),
           "unit_price_per": o.get("unit_price_per"), "per_unit": o.get("per_unit"), "brand": o.get("brand"),
           "source": o["source"], "date": o.get("date")}
    if o.get("region"):
        out["region"] = o["region"]
    return out


def optimize(max_stops: int = MAX_STOPS, stop_cost: float | None = None) -> dict:
    """Alınmamış satırlar için en iyi 1..max_stops duraklı plan (app/price_sources.py best_offers: zincir başına tek
    teklif; kişinin ili, resmî / topluluk / kendi fişin). Satır fiyatı = teklif × adet.

    plans[k]: k zincirli birleşimlerden (her zincire en az bir satır düşen) en çok satırı karşılayan, eşitse en ucuz;
    her satır birleşimde onu en ucuza satan zincire. recommended_k: 1 duraktan başla; bir sonraki durak satır
    kazandırıyorsa ya da aynı kapsamda en az stop_cost ₺ ucuzsa geç, değilse dur. saving_vs_single = 1 duraklı plan −
    bu plan; saving_vs_prev = bir önceki plan − bu plan; extra_vs_best = bu plan − önerilen plan.
    single: zincir başına "hepsi buradan" (covered çoktan aza, sonra ucuz); vs_recommended = toplam − önerilen."""
    from datetime import date
    from itertools import combinations

    from app import db, money, prefs, price_sources, provinces

    max_stops = max(1, min(int(max_stops or MAX_STOPS), MAX_STOPS))
    cost = stop_cost_pref() if stop_cost is None else max(0.0, float(stop_cost))
    uid = (db.current_user() or {}).get("id")
    region = prefs.region(uid) or provinces.DEFAULT
    items = [i for i in list_items() if not i["done"]]
    best = price_sources.best_offers([i["group"]["key"] for i in items if i["group"]], limit=50)
    offers: dict[int, dict[str, dict]] = {}  # satır → zincir → teklif
    for it in items:
        offs = best.get(it["group"]["key"], []) if it["group"] else []
        if offs:
            offers[it["id"]] = {o["chain"]: o for o in offs}
    priced = [it for it in items if it["id"] in offers]
    unpriced = [it["id"] for it in items if it["id"] not in offers]

    chains: dict[str, dict] = {}
    for it in priced:
        for ch, o in offers[it["id"]].items():
            c = chains.setdefault(ch, {"chain": ch, "total": 0.0, "covered": 0})
            c["total"] += it["qty"] * o["price"]
            c["covered"] += 1
    ranked = sorted(chains.values(), key=lambda c: (-c["covered"], c["total"], c["chain"]))
    cand = [c["chain"] for c in ranked[:MAX_CHAINS]]

    def evaluate(combo: tuple[str, ...]) -> dict | None:
        stops: dict[str, list] = {ch: [] for ch in combo}
        missing = []
        for it in priced:
            got = [(offers[it["id"]][ch]["price"], combo.index(ch), ch) for ch in combo if ch in offers[it["id"]]]
            if not got:
                missing.append(it["id"])
                continue
            ch = min(got)[2]
            stops[ch].append(_line(it, offers[it["id"]][ch]))
        if any(not v for v in stops.values()):
            return None  # boş durak: daha az duraklı planla aynı
        out = [{"chain": ch, "total": money.money(sum(x["line_total"] for x in v)), "items": v} for ch, v in stops.items()]
        out.sort(key=lambda s: (-len(s["items"]), -s["total"], s["chain"]))
        total = money.money(sum(s["total"] for s in out))
        return {"k": len(combo), "stops": out, "total": total, "covered": len(priced) - len(missing), "missing": missing}

    plans = []
    for k in range(1, min(max_stops, len(cand)) + 1):
        got = [p for p in (evaluate(c) for c in combinations(cand, k)) if p]
        if not got:
            break
        plans.append(min(got, key=lambda p: (-p["covered"], p["total"], [s["chain"] for s in p["stops"]])))

    rec = 1 if plans else None
    for i in range(1, len(plans)):
        prev, cur = plans[i - 1], plans[i]
        if cur["covered"] > prev["covered"] or (cur["covered"] == prev["covered"] and prev["total"] - cur["total"] >= cost):
            rec = cur["k"]
        else:
            break
    rec_total = plans[rec - 1]["total"] if rec else None
    for i, p in enumerate(plans):
        p["saving_vs_single"] = money.money(plans[0]["total"] - p["total"])
        p["saving_vs_prev"] = money.money(plans[i - 1]["total"] - p["total"]) if i else 0.0
        p["extra_vs_best"] = money.money(p["total"] - rec_total)
        p["recommended"] = p["k"] == rec

    all_priced = [it["id"] for it in priced]
    single = []
    for c in ranked:
        have = {it["id"] for it in priced if c["chain"] in offers[it["id"]]}
        total = money.money(c["total"])
        single.append({"chain": c["chain"], "total": total, "covered": c["covered"],
                       "missing": [i for i in all_priced if i not in have],
                       "vs_recommended": money.money(total - rec_total) if rec_total is not None else None})

    used = [x for p in plans for s in p["stops"] for x in s["items"]]
    order = ["resmi", "community", "own"]
    srcs = sorted({x["source"] for x in used}, key=lambda x: (order.index(x) if x in order else len(order), x))
    resmi_days = [x["date"] for x in used if x["source"] == "resmi" and x.get("date")]
    attribution = sorted({o["attribution"] for v in offers.values() for o in v.values() if o.get("attribution")})
    return {"region": region, "date": date.today().isoformat(), "stop_cost": cost, "max_stops": max_stops,
            "plans": plans, "single": single, "recommended_k": rec,
            "one_store": single[0] if single else None,
            "priced": len(priced), "unpriced": unpriced,
            "sources": attribution,
            "source_info": {"kinds": srcs, "resmi_date": max(resmi_days) if resmi_days else None,
                            "national": any(x["source"] == "resmi" and x.get("region") == "ulusal" for x in used)}}
