"""Alışveriş listesi v3: kaydedilen plan, tamamlama ve geçmiş alışverişler, fişi listeye bağlama, tasarruf.

Defter veritabanında (app/db.py SCHEMA: shopping_plan, shopping_trips; ortak defterde herkes aynısını görür):

- Plan kaydı ("Bu planla git", save_plan): Nereye gideyim? planının (app/shopping.py optimize) seçilen durak sayılısı
  satır başına {id, text, chain, unit_price, line_total} olarak saklanır. Liste bunu zincir zincir gösterir ("BİM'den al");
  satırın adedi sonradan değişirse satır tutarı birim teklif × yeni çarpanla yeniden hesaplanır. Silinen satır plandan
  düşer, sonradan eklenen satır planda yoktur (istemci "Planda olmayanlar"). Liste tamamlanınca plan silinir.
- Tamamlama (complete): alınan (işaretli) satırlar bir geçmiş alışverişe (shopping_trips) yazılır: gün, alınanların
  planlanan toplamı (plan yoksa tamamlarken bilinen en ucuz fiyatla), zincirler, satırlar. Alınanlar listeden silinir;
  alınmayanlar carry_over ise listede kalır, değilse alışverişe "alınmadı" diye yazılıp listeden silinir.
- Fiş bağlama (receipt_match / link_receipt): kaydedilen bir fiş (POST /api/entries; ya da başka yoldan gelen, liste
  sayfasında pending_match ile) etkin listeye ya da aynı / önceki gün tamamlanan alışverişe ait mi? Satırların ürün
  grubu (markadan bağımsız, boy hariç ana grup) fiş kalemleriyle eşleşir; en az MIN_MATCH eşleşme aday. Fişin zinciri
  kaydedilen planın (ya da alışverişin) zincirlerinden biriyse ve en az CONFIDENT_MATCH eşleşme varsa kendiliğinden
  bağlanır, değilse istemci sorar. Bağlanınca eşleşen satırlar işaretlenir, gerçek birim fiyat ve satır tutarı saklanır.
- Tasarruf (dürüst ölçü): eşleşen her fiş kalemi için o günün piyasa ortancası (app/market_ref.py; yoksa bugünkü
  zincir tekliflerinin ortancası) × fişteki miktar − ödenen. En pahalıya göre DEĞİL. Piyasa fiyatı bilinmeyen kalem
  tasarrufa girmez. Aylık / yıllık toplam savings().
"""
import json
from datetime import date, timedelta
from statistics import median

from app import db

MIN_MATCH = 2        # fiş en az bu kadar liste satırıyla eşleşirse aday
CONFIDENT_MATCH = 3  # bu kadar eşleşme + zincir planda → sormadan bağla
MAX_TRIPS = 500      # defter başına saklanan geçmiş alışveriş (eskisi silinir)
DISMISS_KEY = "shopping:dismissed_receipts"
ITEM_FIELDS = ("text", "qty", "unit", "amount_label", "group_key", "label", "done", "planned_chain", "planned_unit",
               "planned_line", "actual_unit", "actual_line", "entry_id", "receipt_key", "receipt_qty", "market_unit",
               "market_line")


def _j(v, default):
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default


def _money(x):
    from app import money

    return money.money(x)


def _base(key: str | None) -> str | None:
    return key.split("|", 1)[0] if key else None


def _chain_key(ch: str | None) -> str:
    return db._store_key(ch or "")


# ---------------------------------------------------------------- plan kaydı

def _raw_plan() -> dict | None:
    with db.connect() as c:
        r = c.execute("SELECT data, created_at, created_by FROM shopping_plan WHERE id = 1").fetchone()
    if not r:
        return None
    d = _j(r["data"], None)
    if not isinstance(d, dict):
        return None
    return {**d, "created_at": r["created_at"], "created_by": r["created_by"]}


def save_plan(k: int | None = None, uid: int | None = None) -> dict:
    """Nereye gideyim? planını kaydet (k: durak sayısı; boşsa önerilen). Fiyatlı satır yoksa ValueError."""
    from app import shopping

    o = shopping.optimize()
    plans = o["plans"]
    if not plans:
        raise ValueError("Planlanacak fiyatlı ürün yok")
    k = k or o["recommended_k"] or 1
    p = next((x for x in plans if x["k"] == k), None) or plans[-1]
    items, chains = [], []
    for st in p["stops"]:
        chains.append({"chain": st["chain"], "total": st["total"], "item_ids": [x["id"] for x in st["items"]]})
        items += [{"id": x["id"], "text": x["text"], "chain": st["chain"], "unit_price": x["price"],
                   "line_total": x["line_total"]} for x in st["items"]]
    data = {"k": p["k"], "total": p["total"], "chains": chains, "items": items, "date": o["date"],
            "source_info": o["source_info"], "missing": p["missing"]}
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO shopping_plan (id, data, created_by, created_at) VALUES (1, ?, ?, CURRENT_TIMESTAMP)",
                  (json.dumps(data, ensure_ascii=False), uid))
    return saved_plan()


def clear_plan() -> bool:
    with db.connect() as c:
        return c.execute("DELETE FROM shopping_plan").rowcount > 0


def saved_plan(items: list[dict] | None = None) -> dict | None:
    """Kaydedilen plan, listedeki güncel satırlarla: {k, total, chains [{chain, total, item_ids}], items [{id, text,
    chain, unit_price, line_total}], created_at, date, source_info} ya da None. Listeden silinen satır düşer; satır
    tutarı birim teklif × satırın güncel çarpanı (adet / miktar değiştiyse)."""
    from app import shopping

    p = _raw_plan()
    if not p:
        return None
    if items is None:
        items = shopping.list_items()
    by = {i["id"]: i for i in items}
    out_items = []
    for x in p.get("items", []):
        it = by.get(x["id"])
        if not it:
            continue
        line = _money(x["unit_price"] * it["mult"]) if it.get("mult") is not None else x["line_total"]
        out_items.append({**x, "text": it["text"], "line_total": line})
    chains = []
    for ch in p.get("chains", []):
        mine = [x for x in out_items if x["chain"] == ch["chain"]]
        if mine:
            chains.append({"chain": ch["chain"], "total": _money(sum(x["line_total"] for x in mine)),
                           "item_ids": [x["id"] for x in mine]})
    return {"k": p.get("k"), "total": _money(sum(c["total"] for c in chains)), "chains": chains, "items": out_items,
            "created_at": p.get("created_at"), "created_by": p.get("created_by"), "date": p.get("date"),
            "source_info": p.get("source_info")}


def attach_planned(items: list[dict]) -> dict | None:
    """Liste satırlarına planned {chain, unit_price, line_total} | null; kaydedilen planı döner."""
    p = saved_plan(items)
    by = {x["id"]: x for x in (p or {}).get("items", [])}
    for i in items:
        x = by.get(i["id"])
        i["planned"] = {"chain": x["chain"], "unit_price": x["unit_price"], "line_total": x["line_total"]} if x else None
    return p


# ---------------------------------------------------------------- piyasa referansı ve toplamlar

def _market_units(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], float]:
    """(grup, gün) → piyasa ortancası (birim teklif): o günün (±3 gün) piyasa bandı, yoksa bugünkü zincir
    tekliflerinin ortancası (en az 2 zincir)."""
    from app import market_ref, price_sources

    pairs = [(k, d) for k, d in pairs if k and d]
    if not pairs:
        return {}
    out: dict[tuple[str, str], float] = {}
    since = (min(date.fromisoformat(d[:10]) for _, d in pairs) - timedelta(days=market_ref.NEAR_DAYS)).isoformat()
    try:
        ser = market_ref.series(sorted({k for k, _ in pairs}), since)
    except Exception:  # noqa: BLE001 - piyasa bandı yoksa yedek
        ser = {}
    for k, d in pairs:
        ref = market_ref.nearest(ser.get(k, {}), d)
        if ref and ref.get("median"):
            out[(k, d)] = float(ref["median"])
    rest = sorted({k for k, d in pairs if (k, d) not in out})
    if rest:
        offers = price_sources.best_offers(rest, limit=50)
        for k, d in pairs:
            if (k, d) in out:
                continue
            prices = [o["price"] for o in offers.get(k, [])]
            if len(prices) >= 2:
                out[(k, d)] = float(median(prices))
    return out


def _entry_days(ids: list[int]) -> dict[int, str]:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    with db.connect() as c:
        return {r[0]: r[1] for r in c.execute(
            f"SELECT id, entry_date FROM entries WHERE id IN ({','.join('?' * len(ids))})", ids)}


def _fill_market(items: list[dict]) -> None:
    """Fişle eşleşmiş satırlara market_unit / market_line (fiş kaleminin grubu, fişin günü, fişteki miktar)."""
    days = _entry_days([i.get("entry_id") for i in items])
    pairs = [(i["receipt_key"], days.get(i["entry_id"])) for i in items
             if i.get("entry_id") and i.get("receipt_key") and days.get(i["entry_id"])]
    ref = _market_units(pairs)
    for i in items:
        m = ref.get((i.get("receipt_key"), days.get(i.get("entry_id")))) if i.get("entry_id") else None
        i["market_unit"] = round(m, 2) if m else None
        i["market_line"] = _money(m * (i.get("receipt_qty") or 1)) if m else None


def _totals(items: list[dict]) -> dict:
    bought = [i for i in items if i.get("done")]
    planned = [i["planned_line"] for i in bought if i.get("planned_line") is not None]
    act = [i for i in items if i.get("actual_line") is not None]
    mk = [i for i in act if i.get("market_line") is not None]
    pm = [i for i in act if i.get("planned_line") is not None]
    return {"planned_total": _money(sum(planned)) if planned else None,
            "actual_total": _money(sum(i["actual_line"] for i in act)) if act else None,
            "market_total": _money(sum(i["market_line"] for i in mk)) if mk else None,
            "saving": _money(sum(i["market_line"] - i["actual_line"] for i in mk)) if mk else None,
            "planned_matched": _money(sum(i["planned_line"] for i in pm)) if pm else None,
            "actual_matched": _money(sum(i["actual_line"] for i in pm)) if pm else None}


# ---------------------------------------------------------------- tamamla ve geçmiş

def complete(carry_over: bool = True, uid: int | None = None) -> dict:
    """Alınanları geçmiş alışverişe yaz, listeden sil; alınmayanlar carry_over ise listede kalır. Plan silinir.
    Alınan satır yoksa ValueError."""
    from app import price_sources, shopping

    items = shopping.list_items()
    bought = [i for i in items if i["done"]]
    if not bought:
        raise ValueError("Önce aldıklarını işaretle")
    plan = attach_planned(items)
    with db.connect() as c:
        raw = {r["id"]: dict(r) for r in c.execute("SELECT * FROM shopping_list")}
    need = [i["group"]["key"] for i in bought if not i.get("planned") and i["group"] and i["mult"] is not None]
    best = price_sources.best_offers(need, limit=1) if need else {}
    kept = [i for i in items if not i["done"]] if carry_over else []
    rows = []
    for i in items:
        if i in kept:
            continue
        r = raw.get(i["id"], {})
        pl = i.get("planned")
        if not pl and i["done"] and i["group"] and i["mult"] is not None and best.get(i["group"]["key"]):
            o = best[i["group"]["key"]][0]
            pl = {"chain": price_sources.chain_name(o["chain"]), "unit_price": o["price"],
                  "line_total": _money(o["price"] * i["mult"])}
        rows.append({"text": i["text"], "qty": i["qty"], "unit": i["unit"], "amount_label": i["amount_label"],
                     "group_key": i["group_key"], "label": (i["group"] or {}).get("label"), "done": i["done"],
                     "planned_chain": pl["chain"] if pl and i["done"] else None,
                     "planned_unit": pl["unit_price"] if pl and i["done"] else None,
                     "planned_line": pl["line_total"] if pl and i["done"] else None,
                     "actual_unit": r.get("actual_unit"), "actual_line": r.get("actual_line"),
                     "entry_id": r.get("entry_id"), "receipt_key": r.get("receipt_key"),
                     "receipt_qty": r.get("receipt_qty")})
    _fill_market(rows)
    entry_ids = sorted({x["entry_id"] for x in rows if x.get("entry_id")})
    # gidilen yerler: fiş bağlandıysa fişlerin zincirleri, değilse planınkiler
    chains = _receipt_chains(entry_ids) or [ch["chain"] for ch in (plan or {}).get("chains", [])]
    if not chains:
        chains = list(dict.fromkeys(x["planned_chain"] for x in rows if x.get("planned_chain")))
    tot = _totals(rows)
    with db.connect() as c:
        tid = c.execute(
            "INSERT INTO shopping_trips (day, planned_total, actual_total, market_total, saving, chains, items, plan, "
            "entry_ids, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date.today().isoformat(), tot["planned_total"], tot["actual_total"], tot["market_total"], tot["saving"],
             json.dumps(chains, ensure_ascii=False), json.dumps(rows, ensure_ascii=False),
             json.dumps(plan, ensure_ascii=False) if plan else None, json.dumps(entry_ids), uid)).lastrowid
        gone = [i["id"] for i in items if i not in kept]
        if gone:
            c.execute(f"DELETE FROM shopping_list WHERE id IN ({','.join('?' * len(gone))})", gone)
        c.execute("DELETE FROM shopping_plan")
        c.execute("DELETE FROM shopping_trips WHERE id NOT IN (SELECT id FROM shopping_trips ORDER BY id DESC LIMIT ?)",
                  (MAX_TRIPS,))
    return {**get_trip(tid), "carried": len(kept)}


def _receipt_chains(entry_ids: list[int]) -> list[str]:
    from app import price_sources
    from app.receipt_templates import chain_of

    if not entry_ids:
        return []
    with db.connect() as c:
        rs = c.execute(f"SELECT description, legal_name FROM entries WHERE id IN ({','.join('?' * len(entry_ids))})",
                       entry_ids).fetchall()
    return list(dict.fromkeys(price_sources.chain_name(chain_of(r[0], r[1]) or r[0]) for r in rs if r[0]))


def _trip_out(r, full: bool = True) -> dict:
    from app import shopping

    items = _j(r["items"], [])
    out = {"id": r["id"], "day": r["day"], "completed_at": r["completed_at"], "planned_total": r["planned_total"],
           "actual_total": r["actual_total"], "market_total": r["market_total"], "saving": r["saving"],
           "chains": _j(r["chains"], []), "entry_ids": _j(r["entry_ids"], []), "plan_saved": bool(r["plan"]),
           "item_count": len(items), "bought_count": sum(1 for i in items if i.get("done")),
           "linked": bool(_j(r["entry_ids"], [])), "created_by": r["created_by"],
           "created_by_name": shopping._people().get(r["created_by"]) if r["created_by"] else None}
    tot = _totals(items)
    out["planned_matched"], out["actual_matched"] = tot["planned_matched"], tot["actual_matched"]
    if full:
        out["items"] = [{k: i.get(k) for k in ITEM_FIELDS} for i in items]
        out["plan"] = _j(r["plan"], None)
    return out


def trips(limit: int = 50) -> list[dict]:
    with db.connect() as c:
        rs = c.execute("SELECT * FROM shopping_trips ORDER BY day DESC, id DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    return [_trip_out(r, full=False) for r in rs]


def get_trip(tid: int) -> dict | None:
    with db.connect() as c:
        r = c.execute("SELECT * FROM shopping_trips WHERE id = ?", (tid,)).fetchone()
    return _trip_out(r) if r else None


def delete_trip(tid: int) -> bool:
    with db.connect() as c:
        return c.execute("DELETE FROM shopping_trips WHERE id = ?", (tid,)).rowcount > 0


def reuse(tid: int, only_bought: bool = False, uid: int | None = None) -> dict | None:
    """Geçmiş alışverişin satırlarını listeye ekle (aynı ürün listede varsa atlanır): {added, skipped, items}."""
    from app import shopping

    t = get_trip(tid)
    if not t:
        return None
    have = shopping.list_items()
    seen = {_base(i["group_key"]) for i in have if i["group_key"]} | {i["text"].strip().lower() for i in have}
    added, skipped = [], 0
    for x in t["items"]:
        if only_bought and not x.get("done"):
            continue
        b, t_ = _base(x.get("group_key")), (x.get("text") or "").strip().lower()
        if (b and b in seen) or t_ in seen:
            skipped += 1
            continue
        if len(have) + len(added) >= shopping.MAX_ITEMS:
            break
        pin = x["group_key"] if x.get("group_key") and "|" in x["group_key"] else None
        try:
            added.append(shopping.add(x["text"], x.get("qty") or 1, uid, pin, x.get("unit") or "adet"))
        except ValueError:
            skipped += 1
            continue
        seen |= {b, t_}
    return {"added": len(added), "skipped": skipped, "items": added}


def savings(today: date | None = None) -> dict:
    """{month, year, month_trips, year_trips}: bu ay / bu yıl tamamlanan alışverişlerin tasarrufu (piyasa ortancasına
    göre; yalnız fişle bağlanmış, piyasa fiyatı bilinen satırlar). Net toplam (eksi olabilir)."""
    t = today or date.today()
    y, m = f"{t.year:04d}", f"{t.year:04d}-{t.month:02d}"
    with db.connect() as c:
        rs = c.execute("SELECT day, saving FROM shopping_trips WHERE saving IS NOT NULL AND day >= ?", (f"{y}-01-01",)).fetchall()
    mon = [r[1] for r in rs if r[0].startswith(m)]
    return {"month": _money(sum(mon)), "year": _money(sum(r[1] for r in rs)), "month_trips": len(mon),
            "year_trips": len(rs), "basis": "market_median"}


# ---------------------------------------------------------------- fişi bağla

def _receipt(entry_id: int) -> dict | None:
    from app import price_sources
    from app.receipt_templates import chain_of

    with db.connect() as c:
        e = c.execute("SELECT id, entry_date, description, legal_name, kind FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not e or e["kind"] != "gider":
            return None
        its = db.rows(c.execute("SELECT id, name, product, brand, unit, qty, amount, unit_price FROM receipt_items "
                                "WHERE entry_id = ? AND price_known > 0 AND amount > 0 ORDER BY id", (entry_id,)))
    if not its:
        return None
    db.attach_groups(its, learn=False)
    chain = price_sources.chain_name(chain_of(e["description"], e["legal_name"]) or e["description"] or "")
    return {"id": e["id"], "day": e["entry_date"], "chain": chain, "items": [i for i in its if i.get("group")]}


def _match(rows: list[dict], items: list[dict]) -> list[tuple[dict, dict]]:
    """(liste satırı, fiş kalemi) çiftleri: aynı grup önce, sonra aynı ana grup (boy farklı); kalem bir kez."""
    used: set[int] = set()
    out = []
    for exact in (True, False):
        for r in rows:
            if any(r is a for a, _ in out) or not r.get("group_key"):
                continue
            for it in items:
                if it["id"] in used:
                    continue
                k = it["group"]["key"]
                if (k == r["group_key"]) if exact else (_base(k) == _base(r["group_key"])):
                    out.append((r, it))
                    used.add(it["id"])
                    break
    return out


def _linked_entries() -> set[int]:
    with db.connect() as c:
        ids = {r[0] for r in c.execute("SELECT entry_id FROM shopping_list WHERE entry_id IS NOT NULL")}
        for (v,) in c.execute("SELECT entry_ids FROM shopping_trips WHERE day >= ?",
                              ((date.today() - timedelta(days=30)).isoformat(),)):
            ids |= set(_j(v, []))
    return ids


def receipt_match(entry_id: int, today: date | None = None) -> dict | None:
    """Fiş etkin listeye ya da aynı / önceki gün tamamlanan alışverişe ait mi? {entry_id, target list|trip, trip_id,
    chain, count, in_plan, confident, names [satır adları], day} ya da None (aday yok / zaten bağlı)."""
    from app import shopping

    from app import book

    t = today or date.today()
    if entry_id in _linked_entries() or (book.active() and not book.can_see_entry(entry_id)):
        return None
    rc = _receipt(entry_id)
    if not rc:
        return None
    try:
        rday = date.fromisoformat(rc["day"][:10])
    except ValueError:
        return None
    cands = []
    if rday >= t - timedelta(days=1):
        with db.connect() as c:
            free = {r[0] for r in c.execute("SELECT id FROM shopping_list WHERE entry_id IS NULL")}
        rows = [i for i in shopping.list_items() if i["id"] in free]
        plan = saved_plan()
        chains = [ch["chain"] for ch in (plan or {}).get("chains", [])]
        pairs = _match(rows, rc["items"])
        cands.append({"target": "list", "trip_id": None, "pairs": pairs, "chains": chains})
    with db.connect() as c:
        trs = c.execute("SELECT * FROM shopping_trips WHERE day >= ? AND day <= ? ORDER BY id DESC LIMIT 5",
                        (rday.isoformat(), (rday + timedelta(days=1)).isoformat())).fetchall()
    for tr in trs:
        rows = [x for x in _j(tr["items"], []) if not x.get("entry_id")]
        for n, x in enumerate(rows):
            x["_i"] = n
        cands.append({"target": "trip", "trip_id": tr["id"], "pairs": _match(rows, rc["items"]),
                      "chains": _j(tr["chains"], [])})
    cands = [c_ for c_ in cands if len(c_["pairs"]) >= MIN_MATCH]
    if not cands:
        return None
    best = max(cands, key=lambda c_: (len(c_["pairs"]), c_["target"] == "list"))
    in_plan = _chain_key(rc["chain"]) in {_chain_key(x) for x in best["chains"]}
    n = len(best["pairs"])
    return {"entry_id": entry_id, "target": best["target"], "trip_id": best["trip_id"], "chain": rc["chain"],
            "day": rc["day"], "count": n, "in_plan": in_plan, "confident": n >= CONFIDENT_MATCH and in_plan,
            "names": [r["text"] for r, _ in best["pairs"]]}


def link_receipt(entry_id: int, target: str | None = None, trip_id: int | None = None) -> dict | None:
    """Fişi bağla (receipt_match'in hedefine; target / trip_id verilirse aynı olmalı): eşleşen satırlar işaretlenir,
    gerçek fiyatlar yazılır. {linked, target, trip_id, count, names, saving, actual_total, market_total} ya da None."""
    m = receipt_match(entry_id)
    if not m or (target and m["target"] != target) or (trip_id and m["trip_id"] != trip_id):
        return None
    rc = _receipt(entry_id)
    from app import shopping

    if m["target"] == "list":
        with db.connect() as c:
            free = {r[0] for r in c.execute("SELECT id FROM shopping_list WHERE entry_id IS NULL")}
        rows = [i for i in shopping.list_items() if i["id"] in free]
        pairs = _match(rows, rc["items"])
        with db.connect() as c:
            c.executemany("UPDATE shopping_list SET done = 1, done_at = COALESCE(done_at, CURRENT_TIMESTAMP), "
                          "actual_unit = ?, actual_line = ?, entry_id = ?, receipt_key = ?, receipt_qty = ? WHERE id = ?",
                          [(it["unit_price"], it["amount"], entry_id, it["group"]["key"], it["qty"], r["id"])
                           for r, it in pairs])
        got = [{"actual_line": it["amount"], "entry_id": entry_id, "receipt_key": it["group"]["key"],
                "receipt_qty": it["qty"]} for _, it in pairs]
        _fill_market(got)
        tot = _totals(got)
        return {"linked": True, "target": "list", "trip_id": None, "count": len(pairs), "chain": rc["chain"],
                "names": [r["text"] for r, _ in pairs], "saving": tot["saving"], "actual_total": tot["actual_total"],
                "market_total": tot["market_total"]}
    with db.connect() as c:
        tr = c.execute("SELECT * FROM shopping_trips WHERE id = ?", (m["trip_id"],)).fetchone()
    items = _j(tr["items"], [])
    free = [x for x in items if not x.get("entry_id")]
    pairs = _match(free, rc["items"])
    for x, it in pairs:
        x.update({"done": True, "actual_unit": it["unit_price"], "actual_line": it["amount"], "entry_id": entry_id,
                  "receipt_key": it["group"]["key"], "receipt_qty": it["qty"]})
    _fill_market(items)
    tot = _totals(items)
    eids = sorted(set(_j(tr["entry_ids"], [])) | {entry_id})
    chains = _receipt_chains(eids) or _j(tr["chains"], [])  # gidilen yerler: fişlerin zincirleri
    with db.connect() as c:
        c.execute("UPDATE shopping_trips SET items = ?, chains = ?, entry_ids = ?, planned_total = ?, actual_total = ?, "
                  "market_total = ?, saving = ? WHERE id = ?",
                  (json.dumps(items, ensure_ascii=False), json.dumps(chains, ensure_ascii=False), json.dumps(eids),
                   tot["planned_total"], tot["actual_total"], tot["market_total"], tot["saving"], tr["id"]))
    return {"linked": True, "target": "trip", "trip_id": tr["id"], "count": len(pairs), "chain": rc["chain"],
            "names": [x["text"] for x, _ in pairs], "saving": tot["saving"], "actual_total": tot["actual_total"],
            "market_total": tot["market_total"]}


def on_receipt_saved(entry_id: int) -> dict | None:
    """Fiş kaydında (POST /api/entries): emin olunursa bağla → {status "linked", …}; aday varsa {status "ask", …};
    yoksa None."""
    m = receipt_match(entry_id)
    if not m:
        return None
    if m["confident"]:
        got = link_receipt(entry_id)
        if got:
            return {"status": "linked", **got}
    return {"status": "ask", **m}


def _dismissed() -> list[int]:
    return [int(x) for x in _j(db.get_setting(DISMISS_KEY), []) if isinstance(x, int)]


def dismiss(entry_id: int) -> None:
    ids = [i for i in _dismissed() if i != entry_id][-49:] + [entry_id]
    db.set_setting(DISMISS_KEY, json.dumps(ids))


def pending_match(today: date | None = None) -> dict | None:
    """Liste sayfası için: son 2 günün bağlanmamış, "hayır" denmemiş fişlerinden en yenisinin adayı (fiş başka yoldan
    — e-posta, telefondan paylaş — geldiyse de sorulsun)."""
    t = today or date.today()
    skip = set(_dismissed()) | _linked_entries()
    with db.connect() as c:
        ids = [r[0] for r in c.execute(
            "SELECT DISTINCT e.id FROM entries e JOIN receipt_items r ON r.entry_id = e.id WHERE e.kind = 'gider' "
            "AND e.entry_date >= ? ORDER BY e.id DESC LIMIT 10", ((t - timedelta(days=1)).isoformat(),))]
    for eid in ids:
        if eid in skip:
            continue
        m = receipt_match(eid, t)
        if m:
            return m
    return None
