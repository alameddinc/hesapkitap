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


def _in_list() -> tuple[set[str], set[str]]:
    from app import shopping

    items = [i for i in shopping.list_items() if not i["done"]]
    return {_base(i["group_key"]) for i in items if i["group_key"]}, {i["text"].strip().lower() for i in items}


def _purchases(days: int) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    items = [it for it in db.priced_items() if it["purchased_at"] >= since]
    db.attach_groups(items, learn=False)
    return [it for it in items if it.get("group") and not it["group"].get("pending")]


def _fold(s: str) -> str:
    from app import product_groups

    return product_groups.slug(s)


def suggest(q: str, limit: int = SUGGEST_MAX) -> dict:
    """Yazarken öneri: {items [{text, group_key, label, source recent|own|group, count?}]}. Önce listedeki / geçmiş
    alışverişteki satırlar, sonra kendi aldıkların (çok alınan önce), sonra ürün grubu adları ("Süt · 1 L")."""
    from app import product_groups, shopping, system

    q = " ".join((q or "").split())[:40]
    s = _fold(q)
    if len(s) < 2:
        return {"items": []}
    out: list[dict] = []
    seen: set[str] = set()

    def push(text: str, key: str | None, label: str | None, source: str, **extra) -> None:
        sig = key or text.strip().lower()
        if sig in seen or len(out) >= limit:
            return
        seen.add(sig)
        out.append({"text": text, "group_key": key, "label": label, "source": source, **extra})

    def hit(*names: str | None) -> bool:
        return any(n and (_fold(n).startswith(s) or f"-{s}" in _fold(n)) for n in names)

    # 1. son kullanılan satırlar (geçmiş alışveriş; listede olanlar değil)
    have_b, have_t = _in_list()
    from app import shopping_trips

    for t in shopping_trips.trips(limit=20):
        for x in (shopping_trips.get_trip(t["id"]) or {}).get("items", []):
            if hit(x.get("text"), x.get("label")) and _base(x.get("group_key")) not in have_b \
                    and x["text"].strip().lower() not in have_t:
                push(x["text"], x.get("group_key") if x.get("group_key") and "|" in x["group_key"] else None,
                     x.get("label"), "recent")
    # 2. kendi aldıkların
    counts: dict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    for it in _purchases(DUE_DAYS):
        g = it["group"]
        counts[g["key"]] += 1
        labels[g["key"]] = g["label"]
    for k in sorted(counts, key=lambda k: (-counts[k], k)):
        if hit(labels[k]):
            push(_name(labels[k]), k if "|" in k else None, labels[k], "own", count=counts[k])
    # 3. ürün grupları (sunucu geneli adlar; fiyatı en çok bilinen boy önce)
    if len(out) < limit:
        with system.connect() as c:
            rows = c.execute("SELECT group_key, MAX(label) FROM product_groups WHERE group_key != '' AND "
                             "(group_key LIKE ? OR group_key LIKE ?) GROUP BY group_key LIMIT 300",
                             (f"{s}%", f"%-{s}%")).fetchall()
        keys = {r[0]: r[1] for r in rows}
        bases = sorted({_base(k) for k in keys})
        common = shopping.common_sizes(bases)
        rank = {k: i for v in common.values() for i, k in enumerate(v)}

        def order(k: str) -> tuple:
            b = _base(k)
            return (not b.startswith(s), len(b), b, k not in rank, rank.get(k, 99), k)

        for k in sorted(keys, key=order):
            if "|" not in k and common.get(k):
                continue  # boysuz ad: yaygın boyu zaten önerilir
            push(_name(keys[k]), k if "|" in k else None, keys[k] if "|" in k else product_groups.labels([k]).get(k, keys[k]),
                 "group")
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

