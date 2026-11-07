"""Ürün kataloğu: Market Fiyatı'nın ürün listesi (BigQuery offers, salt okuma) gruplamanın dayanağı.

Neden: fişteki ad ("COLA TURKA 330ML") kurallarla çözülemeyince yapay zekâya gidiyordu; aynı ürünün iki yazılışı iki ayrı
gruba düşebiliyordu ("kola|2.5l" ve "gazli-icecek|330ml"). Katalogda ~25 bin ürün markası, boyu ve raf kategorisiyle
durur; her katalog ürününün genel adı (kurallarla adından; çözülmezse raf etiketinden: "Kola", "Soğuk Çay") bir kez
hesaplanır. Fiş adı katalogda en çok benzeyen ürünlere (marka dahil kelimeler; kısaltma öneki) eşlenir, en iyi
eşleşmelerin genel adı oylanır; kural çözemezse ve güven eşiği geçerse o genel ad kullanılır
(app/product_groups.py resolve).

Saklama (system.db): catalog_products (ürün kimliği, ad, marka, boy, raf kategorileri, genel ad), catalog_brands (marka,
ürün sayısı). Markalar product_groups'un marka listesine eklenir (app/brands_tr.txt yedek olarak kalır).
Yenileme: gecelik (UTC 06:00 sonrası, günde bir; app/jobs.py), offers'ın son CATALOG_DAYS günü (run_date bölümü süzülür;
maliyet ≈ 60 MB). Kişi verisi yok: yalnız market ürün listesi.
"""
import logging
import threading
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger("hesapkitap.product_catalog")

CATALOG_DAYS = 30
START_HOUR_UTC = 6
DAY_KEY = "catalog:day"
VERSION_KEY = "catalog:version"
MIN_CONFIDENCE = 0.55  # kural çözemezse bu güvenin üstünde katalog eşleşmesi (ölçüm: 0.45 tutarsızlığı artırdı)
TOP_SPREAD = 0.08  # en iyi skora bu kadar yakın adaylar oylanır
MAX_POSTING = 4000  # bu kadar üründe geçen kelime aday üretmez (yalnız puanlar)

# Şema app/system.py SCHEMA'da (catalog_products, catalog_brands).


def _sql() -> str:
    from app import bq

    return f"""
SELECT product_id, ANY_VALUE(title) AS title, ANY_VALUE(brand) AS brand, ANY_VALUE(volume_weight) AS volume,
  ANY_VALUE(menu_category) AS menu_category, ANY_VALUE(main_category) AS main_category,
  ANY_VALUE(categories) AS categories
FROM {bq.table('offers')}
WHERE run_date >= @since AND title IS NOT NULL
GROUP BY product_id"""


def _clean(s) -> str:
    return str(s or "").replace("̇", "").strip()


def generic_of(title: str, brand: str | None, volume: str | None, categories: str | None,
               main_category: str | None = None) -> str | None:
    """Katalog ürününün genel adı: kurallarla adından (marka bilinir); çözülmezse ana raf adı sözlükte güçlü bir adsa o
    ("Çikolata", "Cips"), o da değilse raf etiketlerinden sözlükte olan en uzun güçlü ad ("Gazlı Içecek Kola|Kola" →
    Kola)."""
    from app import price_import
    from app import product_groups as pg

    g = pg.rule_group(price_import.full_title(title, volume), brand)
    if g:
        return g["label"].split(" · ")[0]
    key = tuple(pg.name_norm(_clean(main_category)).split())
    if key and key in pg._GENERIC and key not in pg._WEAK:  # raf adı ("Çikolata", "Cips") etiketlerden güvenilir
        return pg._GENERIC[key]
    best = None
    for tag in _clean(categories).split("|"):
        key = tuple(pg.name_norm(tag).split())
        if key and key in pg._GENERIC and key not in pg._WEAK:
            if best is None or len(key) > best[0]:
                best = (len(key), pg._GENERIC[key])
    return best[1] if best else None


def store(rows: list[dict], now: str | None = None) -> dict:
    """Katalog satırlarını yaz (tümü değişir): [{product_id, title, brand, volume, menu_category, main_category,
    categories}]. Dönen {products, brands, generic}."""
    from app import system

    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    out, brands = [], {}
    for r in rows:
        title = _clean(r.get("title"))
        if not r.get("product_id") or not title:
            continue
        brand = _clean(r.get("brand")) or None
        cats = r.get("categories")
        cats = "|".join(cats) if isinstance(cats, list) else _clean(cats)
        out.append((str(r["product_id"]), title[:160], brand, _clean(r.get("volume") or r.get("volume_weight")) or None,
                    _clean(r.get("menu_category")) or None, _clean(r.get("main_category")) or None, cats[:600] or None,
                    None, now))
        if brand:
            brands[brand] = brands.get(brand, 0) + 1
    with system.connect() as c:
        c.execute("DELETE FROM catalog_brands")
        c.executemany("INSERT INTO catalog_brands (brand, products) VALUES (?, ?)", sorted(brands.items()))
    _refresh_brands()  # markalar önce: genel ad kuralları onları atar
    final = [(*r[:7], generic_of(r[1], r[2], r[3], r[6], r[5]), r[8]) for r in out]
    with system.connect() as c:
        c.execute("DELETE FROM catalog_products")
        c.executemany("INSERT INTO catalog_products (product_id, title, brand, volume, menu_category, main_category, "
                      "categories, generic, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", final)
        c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES (?, ?)", (VERSION_KEY, now))
    reset()
    return {"products": len(final), "brands": len(brands), "generic": sum(1 for r in final if r[7])}


def refresh(today: date | None = None) -> dict:
    """BigQuery'den kataloğu çek ve yaz (gecelik; elle: python -m app.product_catalog)."""
    from app import bq

    since = (today or date.today()) - timedelta(days=CATALOG_DAYS)
    rows = bq.query(_sql(), {"since": since}, max_rows=100_000)
    got = store(rows)
    log.info("ürün kataloğu yenilendi: %s", got)
    return got


def tick(now: datetime) -> dict | None:
    """Zamanlayıcıdan: BigQuery açıksa, UTC START_HOUR_UTC sonrası günde bir yenile, ardından tutarlılık işi."""
    from app import bq, system

    if not bq.configured():
        return None
    utc = now.astimezone(timezone.utc)
    day = utc.date().isoformat()
    if utc.hour < START_HOUR_UTC or system.get(DAY_KEY) == day:
        return None
    system.put(DAY_KEY, day)
    try:
        got = refresh(utc.date())
    except Exception as e:  # noqa: BLE001 - yarın yeniden; eski katalog kalır
        log.warning("ürün kataloğu yenilenemedi: %s", type(e).__name__)
        return {"error": type(e).__name__}
    from app import product_groups

    with system.connect() as c:
        got["consistency"] = product_groups.consistency(c)
    return got


# ---------------------------------------------------------------- bellek içi dizin

_lock = threading.Lock()
_index: dict | None = None
_memo: dict = {}


_seen: dict = {}   # {"path", "version", "checked"}: başka işçi kataloğu yenilediyse dizin yeniden kurulur
RECHECK_S = 600


def reset() -> None:
    global _index
    with _lock:
        _index = None
        _memo.clear()
        _seen.clear()
    from app import product_groups

    product_groups.add_brands([])


def _stale() -> bool:
    import time

    from app import system

    path = system._path()
    if _seen.get("path") != path:
        return True
    if time.time() - _seen.get("checked", 0) < RECHECK_S:
        return False
    _seen["checked"] = time.time()
    try:
        return system.get(VERSION_KEY) != _seen.get("version")
    except Exception:  # noqa: BLE001
        return False


def _tokens(name: str, brand: str | None = None, keep_brand: bool = True) -> list[str]:
    """Eşleşme kelimeleri: boy, rakam, tanımlayıcı, tek harf atılır; marka kelimeleri kalır (fişte marka adda geçer)."""
    from app import product_groups as pg

    toks = pg._expand(pg.name_norm(name).split())
    _, used = pg.parse_size(toks)
    return [t for i, t in enumerate(toks) if i not in used and t and t not in pg.DESCRIPTORS
            and not t.replace(".", "").replace(",", "").isdigit() and len(t) > 1]


def _load() -> dict:
    global _index
    if _index is not None and _stale():
        reset()
    with _lock:
        if _index is not None:
            return _index
        import time

        from app import system

        _seen.update(path=system._path(), checked=time.time(), version=system.get(VERSION_KEY))

        try:
            with system.connect() as c:
                rows = c.execute("SELECT product_id, title, brand, volume, generic FROM catalog_products "
                                 "WHERE generic IS NOT NULL").fetchall()
        except Exception:  # noqa: BLE001 - tablo yoksa (eski veritabanı) katalogsuz
            rows = []
        prods, post, pref = [], {}, {}
        for pid, title, brand, volume, generic in rows:
            toks = frozenset(_tokens(f"{title} {volume or ''}", brand))
            if not toks:
                continue
            i = len(prods)
            prods.append((pid, toks, generic))
            for t in toks:
                post.setdefault(t, []).append(i)
                if len(t) >= 4:
                    pref.setdefault(t[:3], set()).add(t)
        _index = {"prods": prods, "post": post, "pref": pref}
    if prods:
        _refresh_brands()
    return _index


def size() -> int:
    return len(_load()["prods"])


def match(name: str, exclude: str | None = None) -> tuple[str | None, float]:
    """Ad → (genel ad, güven 0..1). Güven: en iyi adayın skoru × oy payı. Skor: ortak kelime / sorgu kelimesi ve
    Jaccard ortalaması; fişteki kısaltma (≥3 harf) katalog kelimesinin öneki ise 0.8 sayılır. exclude: değerlendirmede
    ürünün kendisi."""
    key = (name, exclude)
    if key in _memo:
        return _memo[key]
    idx = _load()
    if not idx["prods"]:
        return None, 0.0
    q = list(dict.fromkeys(_tokens(name)))
    if not q:
        return None, 0.0
    # sorgu kelimesi → katalog kelimeleri (tam ya da önek)
    alias: dict[str, set[str]] = {}
    for t in q:
        if t in idx["post"]:
            alias[t] = {t}
        elif len(t) >= 3:
            alias[t] = {w for w in idx["pref"].get(t[:3], ()) if w.startswith(t) and w != t}
        else:
            alias[t] = set()
    cand: set[int] = set()
    for t in q:
        for w in alias[t]:
            p = idx["post"].get(w, ())
            if len(p) <= MAX_POSTING:
                cand.update(p)
    if not cand:  # yalnız çok sık kelimeler: en seyrek olanınkiler
        ws = [w for t in q for w in alias[t]]
        if ws:
            cand.update(idx["post"][min(ws, key=lambda w: len(idx["post"][w]))])
    scored = []
    for i in cand:
        pid, toks, generic = idx["prods"][i]
        if pid == exclude:
            continue
        hit = 0.0
        for t in q:
            if t in toks:
                hit += 1
            elif alias[t] & toks:
                hit += 0.8
        if not hit:
            continue
        cover = hit / len(q)
        jacc = hit / (len(q) + len(toks) - hit)
        scored.append((0.6 * cover + 0.4 * jacc, generic))
    if not scored:
        _memo[key] = (None, 0.0)
        return None, 0.0
    best = max(s for s, _ in scored)
    votes: dict[str, float] = {}
    for s, g in scored:
        if s >= best - TOP_SPREAD:
            votes[g] = votes.get(g, 0) + s
    g = max(votes, key=lambda k: (votes[k], k))
    conf = round(best * votes[g] / sum(votes.values()), 3)
    if len(_memo) > 20_000:
        _memo.clear()
    _memo[key] = (g, conf)
    return g, conf


CONTAIN_SHARE = 0.6  # içeren ürünlerin bu payı aynı genel addaysa o ad


def contains(name: str) -> str | None:
    """Alışveriş listesine yazılan ad ("Orneksan slims mavi"): kelimelerinin HEPSİ bir katalog ürününün adında geçiyorsa
    (en az iki kelime; tek kelimede yalnız tek ürün içeriyorsa) içeren ürünlerin çoğunluk genel adı (pay ≥
    CONTAIN_SHARE); yoksa None. match()'in oylamasından katı: yazılan kısa adın fazladan kelimesi yok."""
    idx = _load()
    q = set(_tokens(name))
    if not q or not idx["prods"]:
        return None
    posts = sorted((idx["post"].get(t) or () for t in q), key=len)
    if not posts[0]:
        return None
    cand = set(posts[0])
    for p in posts[1:]:
        cand &= set(p)
        if not cand:
            return None
    if len(q) == 1 and len(cand) != 1:
        return None
    votes: dict[str, int] = {}
    for i in cand:
        g = idx["prods"][i][2]
        votes[g] = votes.get(g, 0) + 1
    g = max(votes, key=lambda k: (votes[k], k))
    return g if votes[g] / len(cand) >= CONTAIN_SHARE else None


def brands() -> list[str]:
    from app import system

    try:
        with system.connect() as c:
            return [r[0] for r in c.execute("SELECT brand FROM catalog_brands")]
    except Exception:  # noqa: BLE001
        return []


def _refresh_brands() -> None:
    from app import product_groups

    product_groups.add_brands(brands())


def status() -> dict | None:
    from app import system

    try:
        with system.connect() as c:
            n = c.execute("SELECT COUNT(*), SUM(generic IS NOT NULL) FROM catalog_products").fetchone()
            b = c.execute("SELECT COUNT(*) FROM catalog_brands").fetchone()[0]
    except Exception:  # noqa: BLE001
        return None
    if not n[0]:
        return None
    return {"products": n[0], "with_generic": n[1] or 0, "brands": b, "updated_at": system.get(VERSION_KEY)}


def main() -> int:  # pragma: no cover - elle çalıştırma
    import json

    logging.basicConfig(level=logging.INFO)
    print(json.dumps(refresh(), ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
