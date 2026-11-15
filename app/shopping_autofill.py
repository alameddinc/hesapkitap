"""Alışveriş listesi otomatik doldurma (shopping_autofill): yazarken öneri, sık aldıkların, "Listeyi doldur".

Kaynaklar: kişinin fişleri (db.priced_items: ortak defter üyesinde yalnız görebildiği kayıtlar; ürün grubu
app/product_groups.py), listedeki ve geçmiş alışverişlerdeki satırlar (app/shopping_trips.py), sunucu geneli ürün
grubu adları (product_groups; yalnız ad, kişi bilgisi yok). Öneri satırı {text, group_key, label, source, …}: istemci
POST /api/shopping-list {text, group_key} ile ekler (boy sabitlenir).

Listeyi doldur (due): en az DUE_MIN_BUYS farklı günde alınan ürünün alım aralıklarının ortancası; son alımdan beri
geçen gün ortancanın DUE_RATIO katını geçtiyse "zamanı gelmiş" sayılır (çok eskiden bırakılmış ürün — DUE_STALE kat
ve 30 günden fazla — önerilmez). Listede zaten olan ürün (aynı ana grup) önerilmez.
"""
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from app import db

SUGGEST_MAX = 8
FREQUENT_DAYS = 90
FREQUENT_MAX = 8
DUE_DAYS = 365
DUE_MIN_BUYS = 3
DUE_RATIO = 0.8
DUE_STALE = 3.0
DUE_MAX = 12


def _base(key: str | None) -> str | None:
    return key.split("|", 1)[0] if key else None


def _name(label: str | None) -> str:
    return (label or "").split(" · ")[0]


def _in_list(fast: bool = False) -> tuple[set[str], set[str]]:
    """(listedeki ana gruplar, listedeki metinler); işaretli satır da listede sayılır. fast: grupları yeniden çözmeden
    kayıtlı group_key'den (yazarken öneri her tuşta listeyi baştan çözmesin)."""
    from app import shopping

    if fast:
        with db.connect() as c:
            items = db.rows(c.execute("SELECT text, group_key FROM shopping_list"))
    else:
        items = shopping.list_items()
    return {_base(i["group_key"]) for i in items if i["group_key"]}, {i["text"].strip().lower() for i in items}


_trip_cache: dict = {}


def _recent_trip_items() -> list[dict]:
    """Son 20 geçmiş alışverişin satırları (yeniden eskiye), defter + geçmiş damgası başına önbellekte."""
    import os

    from app import shopping_trips

    with db.connect() as c:
        st = tuple(c.execute("SELECT COUNT(*), IFNULL(MAX(id), 0) FROM shopping_trips").fetchone())
    ck = (os.path.abspath(db.db_path()), (db.current_user() or {}).get("id"))
    hit = _trip_cache.get(ck)
    if hit and hit[0] == st:
        return hit[1]
    rows = [x for t in shopping_trips.trips(limit=20) for x in (shopping_trips.get_trip(t["id"]) or {}).get("items", [])]
    if len(_trip_cache) >= 64:
        _trip_cache.clear()
    _trip_cache[ck] = (st, rows)
    return rows


def _purchases(days: int) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    items = [it for it in db.priced_items() if it["purchased_at"] >= since]
    db.attach_groups(items, learn=False)
    return [it for it in items if it.get("group") and not it["group"].get("pending")]


def _fold(s: str) -> str:
    from app import product_groups

    return product_groups.slug(s)


def suggest(q: str, limit: int = SUGGEST_MAX) -> dict:
    """Yazarken öneri: {items [{text, group_key, label, source recent|own|product|group, count?, last_price?, place?}]}.
    Sıra önce eşleşme katmanı (app/search_rank.py: birebir > ilk kelime > başka kelime başı > kelime içinde > bir harf
    hata; Türkçe harf / büyük-küçük farkı yok): "şeker" yazınca Şeker, sonra Şekerleme — kişi Şekerlemeyi daha sık alsa
    da. Aynı katmanda kişinin geçmişi önce: geçmiş alışverişteki satırlar, kendi aldığın ürün grupları (çok alınan önce),
    kendi aldığın ürünlerin adları ("Örneksan Slims Mavi Sigara · son ₺95"; app/shopping_known.py, en çok
    shopping_known.SUGGEST_MAX), sonra ürün grubu adları ("Süt · 1 L"). 2 harften kısa sorgu boş. Kişinin fişleri ve
    geçmişi defter başına önbellekte (her tuşta yeniden okunmaz)."""
    from app import product_groups, search_rank, shopping, shopping_known, system

    q = " ".join((q or "").split())[:40]
    s = _fold(q)
    if len(s) < search_rank.MIN_LEN:
        return {"items": []}
    cands: list[tuple[tuple, str, str | None, str | None, str, dict]] = []

    def add(t: int | None, order: tuple, text: str, key: str | None, label: str | None, source: str, **extra) -> None:
        if t is not None:
            cands.append(((t, *order), text, key, label, source, extra))

    # 1. son kullanılan satırlar (geçmiş alışveriş; listede olanlar değil)
    have_b, have_t = _in_list(fast=True)
    for i, x in enumerate(_recent_trip_items()):
        if _base(x.get("group_key")) in have_b or x["text"].strip().lower() in have_t:
            continue
        add(search_rank.best_tier(q, x.get("text"), _name(x.get("label"))), (0, 0, i), x["text"],
            x.get("group_key") if x.get("group_key") and "|" in x["group_key"] else None, x.get("label"), "recent")
    # 2. kendi aldıkların (ürün grubu; son DUE_DAYS gün)
    since = (date.today() - timedelta(days=DUE_DAYS)).isoformat()
    counts: dict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    prods = shopping_known.products()
    for p in prods:
        g = p.get("group")
        if g and not g.get("pending") and p["last_date"] >= since:
            counts[g["key"]] += p["count"]
            labels[g["key"]] = g["label"]
    for k in counts:
        add(search_rank.tier(q, _name(labels[k]), strict=True), (1, -counts[k], k), _name(labels[k]),
            k if "|" in k else None, labels[k], "own", count=counts[k])
    # 3. kendi aldığın ürünlerin adları ("örnek" → "Örneksan Slims Mavi Sigara · son ₺95"); katmanı en iyi olanlar
    named = []
    for p in prods:
        if p["name"].strip().lower() in have_t:
            continue
        t = search_rank.tier(q, p["name"])
        if t is not None:
            named.append((t, -p["count"], p["last_date"], p))
    named.sort(key=lambda x: x[2], reverse=True)  # en yeni önce, sonra (kararlı) katman ve çok alınan
    named.sort(key=lambda x: (x[0], x[1]))
    from app import money

    for t, _, _, p in named[:shopping_known.SUGGEST_MAX]:
        k = (p.get("group") or {}).get("key")
        price = p["last_price"]
        add(t, (2, -p["count"], p["name"]), p["name"], k if k and "|" in k else None,
            f"{p['name']} · son {money.tl(price, trim=True)}" if price else p["name"], "product",
            count=p["count"], last_price=price, place=p["place"])
    # 4. ürün grupları (sunucu geneli adlar; fiyatı en çok bilinen boy önce): kelime başı, içinde geçen (≥ 3 harf) ve
    # bir harf hatalı genel adlar (sözlük, ≥ 4 harf)
    slug = product_groups.slug(q)
    pats = [f"{slug}%", f"%-{slug}%"] + ([f"%{slug}%"] if len(s.replace(" ", "")) >= search_rank.INSIDE_MIN else [])
    fuzzy = sorted({product_groups.slug(g) for g in product_groups.GENERICS
                    if search_rank.tier(q, g, strict=True) == search_rank.FUZZY}) \
        if len(s.replace(" ", "")) >= search_rank.FUZZY_MIN else []
    where = " OR ".join(["group_key LIKE ?"] * len(pats) + ["group_key = ? OR group_key LIKE ?"] * len(fuzzy))
    args = pats + [a for f in fuzzy for a in (f, f"{f}|%")]
    with system.connect() as c:
        rows = c.execute(f"SELECT group_key, MAX(label) FROM product_groups WHERE group_key != '' AND ({where}) "
                         "GROUP BY group_key LIMIT 600", args).fetchall()
    keys = {r[0]: r[1] for r in rows}
    bases = sorted({_base(k) for k in keys})
    common = shopping.common_sizes(bases)
    rank = {k: i for v in common.values() for i, k in enumerate(v)}
    for k, lab in keys.items():
        if "|" not in k and common.get(k):
            continue  # boysuz ad: yaygın boyu zaten önerilir
        b = _base(k)
        label = lab if "|" in k else product_groups.labels([k]).get(k, lab)
        add(search_rank.tier(q, _name(lab), strict=True), (3, len(b), b, k not in rank, rank.get(k, 99), k),
            _name(lab), k if "|" in k else None, label, "group")

    out: list[dict] = []
    seen: set[str] = set()
    for _, text, key, label, source, extra in sorted(cands, key=lambda x: x[0]):
        sig = key or text.strip().lower()
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"text": text, "group_key": key, "label": label, "source": source, **extra})
        if len(out) >= limit:
            break
    return {"items": out}


def frequent(limit: int = FREQUENT_MAX) -> dict:
    """Sık aldıkların: son FREQUENT_DAYS günde en çok fişte geçen ürünler (listede olmayanlar) {items [{text, group_key,
    label, count, last_date}]}."""
    have_b, have_t = _in_list()
    n: dict[str, set] = defaultdict(set)
    last: dict[str, str] = {}
    labels: dict[str, str] = {}
    for it in _purchases(FREQUENT_DAYS):
        k = it["group"]["key"]
        n[k].add(it["entry_id"] or it["purchased_at"])
        last[k] = max(last.get(k, ""), it["purchased_at"])
        labels[k] = it["group"]["label"]
    out, bases = [], set()
    for k in sorted(n, key=lambda k: (-len(n[k]), k)):
        b = _base(k)
        if b in have_b or b in bases or _name(labels[k]).lower() in have_t or len(n[k]) < 2:
            continue
        bases.add(b)
        out.append({"text": _name(labels[k]), "group_key": k if "|" in k else None, "label": labels[k],
                    "count": len(n[k]), "last_date": last[k]})
        if len(out) >= limit:
            break
    return {"items": out, "days": FREQUENT_DAYS}


def _qty(rows: list[dict]) -> tuple[float, str]:
    """Bir alımdaki tipik miktar (ortanca) ve birim: tartılıda kg / L, değilse adet."""
    units = [r.get("unit") or "adet" for r in rows]
    unit = max(set(units), key=units.count)
    q = median([float(r.get("qty") or 1) for r in rows if (r.get("unit") or "adet") == unit])
    if unit == "kg":
        return round(q, 1) or 1.0, "kg"
    if unit == "lt":
        return round(q, 1) or 1.0, "L"
    return float(max(1, round(q))), "adet"


def due(today: date | None = None) -> dict:
    """Listeyi doldur: {items [{text, group_key, label, qty, unit, last_date, interval_days, days_since, reason}]} —
    en gecikmiş önce."""
    t = today or date.today()
    have_b, have_t = _in_list()
    by: dict[str, list[dict]] = defaultdict(list)
    for it in _purchases(DUE_DAYS):
        by[it["group"]["key"]].append(it)
    # aynı ana grubun boyları birlikte sayılır (1 L ve 500 ml süt): en çok alınan boy önerilir
    groups: dict[str, list[dict]] = defaultdict(list)
    for k, rows in by.items():
        groups[_base(k)].extend(rows)
    out = []
    for b, rows in groups.items():
        if b in have_b:
            continue
        days = sorted({date.fromisoformat(r["purchased_at"][:10]) for r in rows})
        if len(days) < DUE_MIN_BUYS:
            continue
        gaps = [(y - x).days for x, y in zip(days, days[1:])]
        med = median(gaps)
        since = (t - days[-1]).days
        if med < 1 or since < DUE_RATIO * med or (since > DUE_STALE * med and since > 30):
            continue
        keys = [r["group"]["key"] for r in rows]
        k = max(set(keys), key=lambda x: (keys.count(x), x))
        label = next(r["group"]["label"] for r in rows if r["group"]["key"] == k)
        if _name(label).lower() in have_t:
            continue
        q, u = _qty([r for r in rows if r["group"]["key"] == k])
        m = int(round(med))
        out.append({"text": _name(label), "group_key": k if "|" in k else None, "label": label, "qty": q, "unit": u,
                    "last_date": days[-1].isoformat(), "interval_days": m, "days_since": since,
                    "reason": f"Genelde {m} günde bir alıyorsun · son alım {since} gün önce",
                    "_score": since / med})
    out.sort(key=lambda x: (-x["_score"], x["text"]))
    for x in out:
        x.pop("_score")
    return {"items": out[:DUE_MAX]}


_BULK_MAX = 50


def bulk_add(items: list[dict], uid: int | None = None) -> dict:
    """Birden çok satır ekle ({text, qty?, unit?, group_key?}); eklenemeyen (geçersiz) satır atlanır."""
    from app import shopping

    added, skipped = [], 0
    for x in items[:_BULK_MAX]:
        try:
            added.append(shopping.add(str(x.get("text") or ""), float(x.get("qty") or 1), uid, x.get("group_key"),
                                      x.get("unit")))
        except (ValueError, TypeError):
            skipped += 1
    return {"added": len(added), "skipped": skipped, "items": added}

