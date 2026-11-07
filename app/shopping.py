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


KEY_RE = r"^[a-z0-9-]{1,40}(\|[a-z0-9.]{1,16})?$"
COMMON_DAYS = 14   # en yaygın boy: son bu kadar günün resmî + topluluk satırları


def common_sizes(bases: list[str]) -> dict[str, list[str]]:
    """Boysuz genel ürün → en yaygın boylu gruplar (çoktan aza): resmî fiyat (son COMMON_DAYS gün, zincir × ürün adı)
    ve topluluk satırları (kişi × gün) sayılır. "sut" → ["sut|1l", "sut|500ml", …]; "elma" → ["elma|kg", …]."""
    from datetime import date, timedelta

    from app import system

    bases = sorted({b for b in bases if b})
    if not bases:
        return {}
    since = (date.today() - timedelta(days=COMMON_DAYS)).isoformat()
    like = " OR ".join("group_key LIKE ?" for _ in bases)
    args = [f"{b}|%" for b in bases]
    with system.connect() as c:
        off = c.execute(f"SELECT group_key, COUNT(DISTINCT chain || '|' || title) FROM official_prices WHERE day >= ? "
                        f"AND ({like}) GROUP BY group_key", [since, *args]).fetchall()
        com = c.execute(f"SELECT group_key, COUNT(DISTINCT contributor || day) FROM community_prices WHERE day >= ? "
                        f"AND ({like}) GROUP BY group_key", [since, *args]).fetchall()
    n: dict[str, int] = {}
    for k, v in [*off, *com]:
        n[k] = n.get(k, 0) + v
    out: dict[str, list[str]] = {}
    for k in sorted(n, key=lambda k: (-n[k], k)):
        out.setdefault(k.split("|", 1)[0], []).append(k)
    return out


def _resolve(texts: list[str]) -> list[dict | None]:
    from app import product_groups

    return product_groups.groups_for([{"name": t} for t in texts])


def _sized(rs: list[dict], groups: list[dict | None]) -> list[tuple[dict | None, dict | None]]:
    """(grup, assumed): kişinin seçtiği boy (pinned_key) önce; boysuz yazılan satır ("Süt", "Elma") en yaygın boya
    çözülür ve assumed {key, label, size_label} döner (istemci "1 L kabul edildi" yazar, dokununca boy seçtirir)."""
    from app import product_groups

    bases = [g["key"] for r, g in zip(rs, groups) if g and "|" not in g["key"] and not g.get("pending")
             and not r.get("pinned_key")]
    common = common_sizes(bases)
    want = [r["pinned_key"] for r in rs if r.get("pinned_key")] + [v[0] for v in common.values()]
    labels = product_groups.labels(want)
    out = []
    for r, g in zip(rs, groups):
        pin = r.get("pinned_key")
        if pin:
            base = (g or {}).get("label", "").split(" · ")[0] or pin.split("|")[0]
            sl = product_groups.size_label(pin)
            lab = labels.get(pin) or (f"{base} · {sl}" if sl else base)
            out.append(({**({k: g[k] for k in ("category", "subcategory") if g and g.get(k)}), "key": pin,
                         "label": lab, "pinned": True}, None))
        elif g and "|" not in g["key"] and not g.get("pending") and common.get(g["key"]):
            k = common[g["key"]][0]
            sl = product_groups.size_label(k)
            lab = labels.get(k) or f"{g['label']} · {sl}"
            out.append(({**g, "key": k, "label": lab}, {"key": k, "label": lab, "size_label": sl}))
        else:
            out.append((g, None))
    return out


def _out(r: dict, group: dict | None, names: dict[int, str], assumed: dict | None = None) -> dict:
    return {"id": r["id"], "text": r["text"], "qty": r["qty"], "done": bool(r["done"]), "group": group,
            "group_key": group["key"] if group else None, "assumed": assumed,
            "added_by": r["added_by"], "added_by_name": names.get(r["added_by"]), "created_at": r["created_at"],
            "done_at": r["done_at"]}


def list_items(with_best: bool = False) -> list[dict]:
    """Liste (önce işaretlenmemişler, eklenme sırasıyla). Geçici grubu öğrenilmiş satırın group_key'i güncellenir.
    with_best: her satıra best (en ucuz teklif {chain, price, unit_price_per?, per_unit?, source} | null)."""
    with db.connect() as c:
        rs = db.rows(c.execute("SELECT * FROM shopping_list ORDER BY done, id"))
    groups = _resolve([r["text"] for r in rs]) if rs else []
    sized = _sized(rs, groups) if rs else []
    stale = [(g["key"] if g else None, r["id"]) for r, (g, _a) in zip(rs, sized) if (g["key"] if g else None) != r["group_key"]]
    if stale:
        with db.connect() as c:
            c.executemany("UPDATE shopping_list SET group_key = ? WHERE id = ?", stale)
    if any(g and g.get("pending") for g in groups):
        from app import product_groups

        product_groups.kick()
    names = _people()
    out = [_out(r, g, names, a) for r, (g, a) in zip(rs, sized)]
    if with_best:
        from app import price_sources

        best = price_sources.best_offers([i["group"]["key"] for i in out if i["group"] and not i["done"]], limit=1)
        for i in out:
            o = (best.get(i["group"]["key"]) or [None])[0] if i["group"] and not i["done"] else None
            i["best"] = {k: o[k] for k in ("chain", "price", "unit_price_per", "per_unit", "source", "n") if k in o} \
                if o else None
    return out


def sizes(rid: int) -> list[dict] | None:
    """Satırın boy seçenekleri (GET /api/shopping-list/{id}/sizes): [{key, label, size_label, chain, price,
    unit_price_per?, per_unit?}] en yaygından, en çok 8. Satır yoksa None."""
    from app import price_sources, product_groups

    with db.connect() as c:
        r = c.execute("SELECT * FROM shopping_list WHERE id = ?", (rid,)).fetchone()
    if not r:
        return None
    g = _resolve([r["text"]])[0]
    if not g:
        return []
    base = g["key"].split("|", 1)[0]
    keys = common_sizes([base]).get(base, [])[:8]
    if "|" in g["key"] and g["key"] not in keys:
        keys = [g["key"], *keys][:8]
    labels = product_groups.labels(keys)
    best = price_sources.best_offers(keys, limit=1)
    name = g["label"].split(" · ")[0]
    out = []
    for k in keys:
        o = (best.get(k) or [None])[0]
        sl = product_groups.size_label(k)
        out.append({"key": k, "label": labels.get(k) or f"{name} · {sl}", "size_label": sl,
                    "chain": o["chain"] if o else None, "price": o["price"] if o else None,
                    **({x: o[x] for x in ("unit_price_per", "per_unit") if o and x in o})})
    return out


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


def _clean_key(key) -> str | None:
    import re

    if key is None or key == "":
        return None
    if not isinstance(key, str) or not re.fullmatch(KEY_RE, key):
        raise ValueError("Geçersiz ürün grubu")
    return key


def _lead_qty(t: str, q: float) -> tuple[str, float]:
    """"6x Süt" / "6 x süt" / "x6 süt" → ("Süt", 6) — adet ayrıca verilmemişse (q == 1)."""
    import re

    m = re.fullmatch(r"(\d{1,3})\s*[xX×*]\s+(\D.*)", t) or re.fullmatch(r"[xX×](\d{1,3})\s+(\D.*)", t)
    if m and q == 1 and 0 < int(m[1]) <= 999:
        return m[2].strip(), float(m[1])
    return t, q


def _row_out(r: dict) -> dict:
    g = _resolve([r["text"]])[0]
    sg, a = _sized([r], [g])[0]
    return _out(r, sg, _people(), a)


def add(text: str, qty: float = 1, uid: int | None = None, group_key: str | None = None) -> dict:
    t, q = _clean_text(text), _clean_qty(qty)
    t, q = _lead_qty(t, q)
    pin = _clean_key(group_key)
    with db.connect() as c:
        if c.execute("SELECT COUNT(*) FROM shopping_list").fetchone()[0] >= MAX_ITEMS:
            raise ValueError(f"Listede en fazla {MAX_ITEMS} satır olabilir")
    g = _resolve([t])[0]
    with db.connect() as c:
        rid = c.execute("INSERT INTO shopping_list (text, group_key, qty, added_by, pinned_key) VALUES (?, ?, ?, ?, ?)",
                        (t, pin or (g["key"] if g else None), q, uid, pin)).lastrowid
        r = dict(c.execute("SELECT * FROM shopping_list WHERE id = ?", (rid,)).fetchone())
    if g and g.get("pending"):
        from app import product_groups

        product_groups.kick()
    return _row_out(r)


def update(rid: int, data: dict) -> dict | None:
    """text, qty, done (işaretlenince done_at bugünün zamanı, kaldırılınca boş), group_key (boy seçimi; null kaldırır;
    metin değişince seçim de kalkar)."""
    sets: dict = {}
    if data.get("text") is not None:
        sets["text"] = _clean_text(data["text"])
        g = _resolve([sets["text"]])[0]
        sets["group_key"] = g["key"] if g else None
        sets["pinned_key"] = None
    if "group_key" in data:
        sets["pinned_key"] = _clean_key(data["group_key"])
        if sets["pinned_key"]:
            sets["group_key"] = sets["pinned_key"]
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
    return _row_out(r)


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
        rows.append({**{k: it[k] for k in ("id", "text", "qty", "group", "assumed")}, "best": offers[0] if offers else None,
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
           "source": o["source"], "date": o.get("date"), "assumed": it.get("assumed")}
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
    rec_plan = plans[rec - 1] if rec else None
    return {"region": region, "date": date.today().isoformat(), "stop_cost": cost, "max_stops": max_stops,
            "headline": headline(rec_plan, plans[0] if plans else None, len(priced)),
            "unpriced_info": [_unpriced(it) for it in items if it["id"] in unpriced],
            "empty": _empty(items, priced),
            "plans": plans, "single": single, "recommended_k": rec,
            "one_store": single[0] if single else None,
            "priced": len(priced), "unpriced": unpriced,
            "sources": attribution,
            "source_info": {"kinds": srcs, "resmi_date": max(resmi_days) if resmi_days else None,
                            "national": any(x["source"] == "resmi" and x.get("region") == "ulusal" for x in used)}}


def headline(p: dict | None, one: dict | None, priced: int) -> dict | None:
    """Önerilen planın tek cümlesi (shopping_headline): {title, subtitle, cta, chains, total, saving, count}.
    k=1: "Önerimiz: Hepsini BİM'den al — ₺195" / "6 ürün" / "BİM'e git"; k>1: "Önerimiz: BİM + Migros — ₺1.200" /
    "₺340 daha ucuz" (tek mağazaya göre) / "BİM + Migros". Fiyatlı satır yoksa None."""
    from app import money
    from app.textnorm import suffix

    if not p:
        return None
    chains = [st["chain"] for st in p["stops"]]
    total, count = p["total"], p["covered"]
    saving = money.money(one["total"] - total) if one and one["covered"] == count and p["k"] > 1 else 0.0
    if p["k"] == 1:
        head = "Hepsini " if count == priced else ""
        title = f"Önerimiz: {head}{suffix(chains[0], 'den')} al — {money.tl(total, trim=True)}"
        cta = f"{suffix(chains[0], 'e')} git"
    else:
        title = f"Önerimiz: {' + '.join(chains)} — {money.tl(total, trim=True)}"
        cta = " + ".join(chains)
    sub = f"{money.tl(saving, trim=True)} daha ucuz" if saving > 0 else f"{count} ürün"
    return {"title": title, "subtitle": sub, "cta": cta, "chains": chains, "total": total, "saving": saving,
            "count": count}


_HINTS = {
    "no_group": "Ürünü tanıyamadık; daha genel yaz: \u201csüt\u201d, \u201cekmek\u201d.",
    "pending": "Ürün tanınıyor; fiyatı birazdan gelir.",
    "no_size": "Ürüne boy ekle: \u201csüt 1 L\u201d.",
    "no_price": "Bu ürünün fiyatı henüz yok; fiş okuttukça fiyatlar gelir.",
}


def _unpriced(it: dict) -> dict:
    g = it.get("group")
    reason = "no_group" if not g else "pending" if g.get("pending") else "no_size" if "|" not in g["key"] else "no_price"
    return {"id": it["id"], "text": it["text"], "reason": reason, "hint": _HINTS[reason]}


def _empty(items: list[dict], priced: list[dict]) -> dict | None:
    """Fiyatlı satır yoksa ne yapmalı (liste boşsa None: istemcinin kendi boş durumu)."""
    if not items or priced:
        return None
    no_size = any(it.get("group") and "|" not in it["group"]["key"] and not it["group"].get("pending") for it in items)
    text = ("Ürüne boy ekle: \u201csüt 1 L\u201d. Fiş okuttukça fiyatlar da gelir." if no_size else
            "Fiş okuttukça fiyatlar gelir; Bölgem'i seçersen market fiyatlarını da görürsün.")
    return {"title": "Henüz fiyat yok", "text": text}
