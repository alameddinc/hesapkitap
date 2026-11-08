"""Piyasa fiyatı: bir ürün grubunun gün gün piyasa bandı ve kendi alımının "ucuz / normal / pahalı" karşılaştırması.

Gün başına referans {day, min, median, max, cheapest_chain, chains, source}: o gün zincir başına en ucuz ürünün fiyatı,
zincirler arası en düşük / ortanca / en yüksek.
Kaynaklar (sırayla; bir gün için ilk bulunan):
1. official_daily (system.db): gecelik içe aktarım (app/price_import_bq.py import_region) her (gün, il) için yazar,
   DAILY_KEEP_DAYS gün tutulur — official_prices yalnız birkaç gün tutulduğu için bant zamanla bu tablodan büyür.
2. official_prices: henüz özetlenmemiş günler (dosya içe aktarımı, eski satırlar).
3. community_prices: o gün en az community_prices.MIN_CONTRIBUTORS farklı kişinin katkısı olan zincirler (ortanca).
Resmî veri yalnız resmî kaynak etkinse okunur (price_sources.enabled_names). İl: kişinin Bölgem'i →
provinces.fallback_chain (yakın büyük şehir, İstanbul); o grupta verisi olan ilk il; yoksa bölgesiz ('') satırlar.

vs_market (kendi alımı): alım gününün ya da en yakın NEAR_DAYS gün içindeki referansın ortancasına göre yüzde;
pct ≤ -THRESHOLD "ucuz", ≥ +THRESHOLD "pahali", arası "normal". Ağ çağrısı yok.
"""
from datetime import date, timedelta
from statistics import median

DAILY_KEEP_DAYS = 400
NEAR_DAYS = 3
THRESHOLD = 5.0
MAX_DAYS = 400


def _band(per_chain: dict[str, float]) -> dict:
    ch = min(per_chain, key=lambda k: (per_chain[k], k))
    vals = list(per_chain.values())
    return {"min": round(min(vals), 2), "median": round(median(vals), 2), "max": round(max(vals), 2),
            "cheapest_chain": ch, "chains": len(vals)}


def summarize_day(c, day: str, region: str) -> int:
    """official_prices'tan (gün, kaynak bölgesi) için grup özetlerini official_daily'ye yaz (yeniden çalışırsa aynı);
    DAILY_KEEP_DAYS'ten eskileri sil. c: system.connect() bağlantısı (içe aktarımla aynı işlem). Yazılan satır sayısı."""
    from app import provinces

    prov = provinces.province_of(region) if region else ""
    if prov is None:
        return 0
    per: dict[str, dict[str, float]] = {}
    for g, ch, p in c.execute("SELECT group_key, chain, MIN(price) FROM official_prices WHERE day = ? AND region = ? "
                              "AND price > 0 GROUP BY group_key, chain", (day, region)):
        per.setdefault(g, {})[ch] = p
    rows = [(g, prov, day, b["min"], b["median"], b["max"], b["cheapest_chain"], b["chains"])
            for g, by in per.items() for b in [_band(by)]]
    c.executemany("INSERT OR REPLACE INTO official_daily (group_key, region, day, min, median, max, cheapest_chain, "
                  "chains) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    c.execute("DELETE FROM official_daily WHERE day < ?", ((date.today() - timedelta(days=DAILY_KEEP_DAYS)).isoformat(),))
    return len(rows)


def _official_on() -> bool:
    from app import price_sources

    return "resmi" in price_sources.enabled_names()


def series(group_keys: list[str], since: str, region: str | None = None,
           used: dict[str, str] | None = None) -> dict[str, dict[str, dict]]:
    """Grup → {gün → referans} (since ve sonrası). region: il (None: kişinin Bölgem'i / İstanbul). used verilirse
    grup → resmî günlerin alındığı il ('' bölgesiz) yazılır (topluluk günlerinin ili yoktur)."""
    from app import community_prices, db, prefs, provinces, system

    keys = sorted({k for k in group_keys if k})
    if not keys:
        return {}
    if region is None:
        region = prefs.region((db.current_user() or {}).get("id"))
    order = provinces.fallback_chain(region) + [""]
    q = ",".join("?" * len(keys))
    # (grup, il) → gün → referans
    off: dict[tuple[str, str], dict[str, dict]] = {}
    if _official_on():
        with system.connect() as c:
            for r in c.execute(f"SELECT group_key, region, day, min, median, max, cheapest_chain, chains FROM official_daily "
                               f"WHERE day >= ? AND group_key IN ({q})", [since, *keys]):
                off.setdefault((r[0], r[1]), {})[r[2]] = {"min": r[3], "median": r[4], "max": r[5],
                                                          "cheapest_chain": r[6], "chains": r[7], "source": "resmi"}
            live: dict[tuple[str, str, str], dict[str, float]] = {}
            for g, reg, day, ch, p in c.execute(
                    f"SELECT group_key, region, day, chain, MIN(price) FROM official_prices WHERE day >= ? AND price > 0 "
                    f"AND group_key IN ({q}) GROUP BY group_key, region, day, chain", [since, *keys]):
                prov = provinces.province_of(reg) if reg else ""
                if prov is not None:
                    by = live.setdefault((g, prov, day), {})
                    by[ch] = min(p, by.get(ch, p))
        for (g, prov, day), by in live.items():
            off.setdefault((g, prov), {}).setdefault(day, {**_band(by), "source": "resmi"})
    with system.connect() as c:
        got = c.execute(f"SELECT group_key, day, chain, contributor, unit_price FROM community_prices WHERE day >= ? "
                        f"AND group_key IN ({q})", [since, *keys]).fetchall()
    people: dict[tuple[str, str, str], dict[str, list[float]]] = {}
    for g, day, ch, who, p in got:
        people.setdefault((g, day, ch), {}).setdefault(who, []).append(p)
    comm: dict[str, dict[str, dict[str, float]]] = {}
    for (g, day, ch), ps in people.items():
        if len(ps) >= community_prices.MIN_CONTRIBUTORS:
            comm.setdefault(g, {}).setdefault(day, {})[ch] = median(median(v) for v in ps.values())
    out: dict[str, dict[str, dict]] = {}
    for g in keys:
        prov = next((p for p in order if off.get((g, p))), None)
        merged = dict(off[(g, prov)]) if prov is not None else {}
        if used is not None and prov is not None:
            used[g] = prov
        for day, by in comm.get(g, {}).items():
            merged.setdefault(day, {**_band(by), "source": "community"})
        if merged:
            out[g] = merged
    return out


def market(group_key: str, days: int = 90) -> dict:
    """GET /api/prices/market: {group, region, days: [{day, min, median, max, cheapest_chain, chains, source}] eskiden
    yeniye, attribution (resmî gün varsa)}."""
    from app import db, prefs, price_import, provinces

    days = max(1, min(int(days), MAX_DAYS))
    region = prefs.region((db.current_user() or {}).get("id")) or provinces.DEFAULT
    since = (date.today() - timedelta(days=days)).isoformat()
    got = series([group_key], since, region).get(group_key, {})
    rows = [{"day": d, **got[d]} for d in sorted(got)]
    return {"group": group_key, "region": region, "days": rows,
            "attribution": price_import.attribution() if any(r["source"] == "resmi" for r in rows) else None}


def compare(price: float | None, ref: dict | None) -> dict | None:
    """{median, min, max, pct, verdict, cheapest_chain, day, source} ya da None."""
    if price is None or not ref or not ref.get("median"):
        return None
    pct = round((price - ref["median"]) / ref["median"] * 100, 1)
    verdict = "ucuz" if pct <= -THRESHOLD else "pahali" if pct >= THRESHOLD else "normal"
    return {"median": ref["median"], "min": ref["min"], "max": ref["max"], "pct": pct, "verdict": verdict,
            "cheapest_chain": ref["cheapest_chain"], "day": ref["day"], "source": ref["source"]}


def nearest(by_day: dict[str, dict], day: str) -> dict | None:
    """Alım günü ya da en yakın NEAR_DAYS gün içindeki referans (eşitse önceki gün)."""
    try:
        d0 = date.fromisoformat(day[:10])
    except (TypeError, ValueError):
        return None
    for off in [0] + [x for i in range(1, NEAR_DAYS + 1) for x in (-i, i)]:
        d = (d0 + timedelta(days=off)).isoformat()
        if d in by_day:
            return {**by_day[d], "day": d}
    return None


def attach(products: list[dict]) -> None:
    """/api/prices ürünlerinin history[]'sine vs_market (fiyatı bilinen, grubu olan alımlar; son MAX_DAYS gün)."""
    cut = (date.today() - timedelta(days=MAX_DAYS)).isoformat()
    want = [p for p in products if p.get("group") and not p["group"].get("pending")
            and any(h.get("price") is not None and (h.get("date") or "") >= cut for h in p.get("history") or [])]
    if not want:
        return
    since = min(h["date"] for p in want for h in p["history"] if h.get("date") and h["date"] >= cut)
    since = (date.fromisoformat(since[:10]) - timedelta(days=NEAR_DAYS)).isoformat()
    ser = series([p["group"]["key"] for p in want], since)
    for p in want:
        by = ser.get(p["group"]["key"])
        if not by:
            continue
        for h in p["history"]:
            if h.get("price") is not None and (h.get("date") or "") >= cut:
                v = compare(h["price"], nearest(by, h["date"]))
                if v:
                    h["vs_market"] = v


# fiş notu (check.market_notes): birim fiyat piyasa ortancasından en az NOTE_PCT yüksek VE en az NOTE_MIN_DIFF ₺ fazla
NOTE_PCT = 10.0
NOTE_MIN_DIFF = 2.0
NOTE_MAX = 5


def _skipped(chk: dict | None) -> set[int]:
    """Okuma hatası olabilecek kalemler (okuma hatası piyasa notundan önce gelir): karışan rakam önerisi (digit_fix),
    şüpheli (suspect), önerilen kaydırmanın etkilediği satırlar, adet × birim tutmayan kalem."""
    chk = chk or {}
    out = {f["index"] for f in chk.get("digit_fix") or [] if isinstance(f, dict) and "index" in f}
    out |= {i for i in chk.get("suspect") or [] if isinstance(i, int)}
    out |= {x["index"] for x in chk.get("items") or [] if isinstance(x, dict) and x.get("mismatch")}
    if (sh := chk.get("suggested_shift")) and isinstance(sh.get("from_index"), int):
        out |= set(range(sh["from_index"], len(chk.get("items") or []) + 1))
    return out


def _paid_unit(it: dict, key: str) -> float | None:
    """Kalemin grubun birimindeki fiyatı (tutar ÷ adet) ya da None (adet / birim tutarsız: tartılı ürün tartısız grupta
    ya da tersi)."""
    from app import money

    amt, qty = it.get("amount"), it.get("qty") or 1
    if not isinstance(amt, (int, float)) or isinstance(amt, bool) or amt <= 0 or not isinstance(qty, (int, float)) \
            or isinstance(qty, bool) or qty <= 0:
        return None
    unit = (it.get("unit") or "").strip().lower()
    weighed = unit in ("kg", "lt", "l") or abs(qty - round(qty)) > 1e-6
    size = key.split("|", 1)[1] if "|" in key else ""
    if weighed != (size == "kg" or (size == "1l" and unit in ("lt", "l"))):
        return None
    return money.money(amt / qty)


def receipt_notes(items: list[dict], chk: dict | None, merchant: str | None, day: str | None,
                  legal_name: str | None = None) -> list[dict]:
    """check.market_notes: fişte piyasadan pahalı kalemler, en pahalı önce, en çok NOTE_MAX —
    [{index, name, group, label, paid_unit, market_median, market_min, cheapest_chain, pct, day, source resmi|community,
    region (il | "ulusal" | null)}]. Piyasa: alım günü ±NEAR_DAYS içindeki referans (series; kişinin Bölgem'i →
    yakın büyük şehir → İstanbul). Fişin zinciri ulusal fiyatlı (price_sources.national_chains(): BİM, A101, ŞOK, Migros; demoda kurgu zincirler)
    ise region "ulusal" (o zincirin fiyatı ilden bağımsız). Okuma hatası olabilecek kalemler atlanır (_skipped).
    Fiyat takibi paketi / sayfası kapalıysa []. Yapay zekâ ve ağ çağrısı yok."""
    from app import db, money, price_alerts, product_groups, price_sources
    from app.receipt_templates import chain_of

    if not items or not price_alerts.allowed():
        return []
    try:
        d0 = date.fromisoformat(str(day)[:10]) if day else date.today()
    except ValueError:
        d0 = date.today()
    skip = _skipped(chk)
    groups = product_groups.groups_for(items, learn=False)
    want: list[tuple[int, dict, dict, float]] = []
    for i, (it, g) in enumerate(zip(items, groups)):
        if i in skip or not g or g.get("pending"):
            continue
        name = it.get("name") or ""
        if db.item_kind(name, it.get("product"), it.get("amount")) != "product":
            continue
        if (paid := _paid_unit(it, g["key"])) is not None:
            want.append((i, it, g, paid))
    if not want:
        return []
    used: dict[str, str] = {}
    ser = series([g["key"] for _, _, g, _ in want], (d0 - timedelta(days=NEAR_DAYS)).isoformat(), used=used)
    national = chain_of(merchant, legal_name) in price_sources.national_chains()
    out = []
    for i, it, g, paid in want:
        ref = nearest(ser.get(g["key"]) or {}, d0.isoformat())
        if not ref or not ref.get("median"):
            continue
        med = ref["median"]
        if paid - med < NOTE_MIN_DIFF or (pct := round((paid - med) / med * 100, 1)) < NOTE_PCT:
            continue
        region = ("ulusal" if national else used.get(g["key"]) or None) if ref["source"] == "resmi" else None
        out.append({"index": i, "name": it.get("name") or g["label"], "group": g["key"], "label": g["label"],
                    "paid_unit": paid, "market_median": money.money(med), "market_min": money.money(ref["min"]),
                    "cheapest_chain": ref["cheapest_chain"], "pct": pct, "day": ref["day"], "source": ref["source"],
                    "region": region})
    out.sort(key=lambda n: (-n["pct"], n["index"]))
    return out[:NOTE_MAX]
