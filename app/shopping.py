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
