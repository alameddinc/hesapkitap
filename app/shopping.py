"""Alışveriş listesi (Premium; ortak defterde paylaşılır): defter veritabanında shopping_list (app/db.py SCHEMA).

Ortak defterde herkes aynı listeyi görür; "görür ve ekler" ve "tam yetki" üyeler ekler, işaretler, siler (app/book.py
MEMBER_WRITES), "sadece görür" üye yalnız okur. Satırın metni ürün grubuna çözülür (app/product_groups.py: önce kural,
çözülemezse geçici grup ve arka planda toplu yapay zekâ; sonraki okumada öğrenilen gruba geçer).

Plan (plan()): işaretlenmemiş her satırın en ucuz zinciri (app/price_sources.py: kendi fişlerin + topluluk ortancası)
ve "hepsini tek yerden" için zincir başına toplam: {chain, total, covered, missing, missing_ids} — en çok satırı
karşılayan önce, eşitse ucuz olan. split_total: her satır kendi en ucuz zincirinden alınırsa toplam.

Miktar (shopping_units): qty + unit (adet | kg | g | L | ml). Satır fiyatı = teklif × mult(): adette adet × paket fiyatı;
kg / g / L / ml'de miktar grubun boyuna bölünür (g→kg, ml→L; "2,5 kg elma" × kilo fiyatı, "1,5 L süt" × 1 L'lik fiyat).
Metinden okunur (parse_amount): "2.5 kg elma", "elma 2,5kg", "6x süt", "6 süt". Ağırlık / hacim yazısı ürünün bilinen
bir paket boyuysa ("süt 1 L", "su 1,5 L") boy olarak kalır, değilse miktardır.
Plan kaydı, tamamlama, geçmiş alışverişler ve fiş bağlama app/shopping_trips.py'dedir.
"""
from app import db

MAX_ITEMS = 200
TEXT_MAX = 80
UNITS = ("adet", "kg", "g", "L", "ml")
UNIT_MAX = {"adet": 999, "kg": 999, "L": 999, "g": 99_999, "ml": 99_999}
# tartılı satılan ürünler: ağırlık yazısı her zaman miktardır ("1 kg domates")
WEIGHED_CATEGORIES = {"Meyve ve Sebze", "Et, Tavuk ve Balık"}
_UNIT_WORDS = {"kg": "kg", "kilo": "kg", "kilogram": "kg", "g": "g", "gr": "g", "gram": "g", "l": "L", "lt": "L",
               "litre": "L", "ml": "ml", "adet": "adet", "ad": "adet", "tane": "adet"}


def _people() -> dict[int, str]:
    from app import book

    try:
        return {p["id"]: p["name"] for p in book.people()}
    except Exception:  # noqa: BLE001 - kişi adı yalnız gösterim
        return {}


KEY_RE = r"^[a-z0-9-]{1,40}(\|[a-z0-9.]{1,16})?$"
COMMON_DAYS = 14   # en yaygın boy: son bu kadar günün resmî + topluluk satırları


OUTLIER_SIZE = 20     # varsayılan boy: türün tipik boyunun bu katından küçük / büyük boy en sona (2 g'lık stick)
PARENT_SHARE = 0.25   # üst ad ("kahve") kendi satırı alt biçimlerinkinin bu payından azsa biçimlerin boyları gösterilir


def _family(k: str) -> str:
    from app import product_groups

    return (product_groups.per_unit(k) or (0, "-"))[1]


def order_sizes(keys: list[str], n: dict[str, int]) -> list[str]:
    """Boylu anahtarlar → varsayılan sırası: önce en çok satırı olan tür (ağırlık / hacim / adet: çekirdek kahvede kg,
    sütte L), türün içinde tipik boydan (ayrı boyların ortancası; satır sayısı değil: 2 g'lık stick çok çeşitli olsa da
    tipik değildir) OUTLIER_SIZE kat uzak boylar sona, sonra çoktan aza. 08.11.2026: "Çekirdek kahve" grubunda 3ü1
    arada paketleri yüzünden "10 adet" varsayılıyordu."""
    from statistics import median

    from app import product_groups

    fam_n: dict[str, int] = {}
    for k in keys:
        fam_n[_family(k)] = fam_n.get(_family(k), 0) + n.get(k, 0)
    typical = {f: median(product_groups.per_unit(k)[0] for k in keys if _family(k) == f)
               for f in fam_n if f != "-"}

    def far(k: str) -> bool:
        pu, t = product_groups.per_unit(k), typical.get(_family(k))
        return bool(pu and t and not t / OUTLIER_SIZE <= pu[0] <= t * OUTLIER_SIZE)

    return sorted(keys, key=lambda k: (-fam_n[_family(k)], _family(k), far(k), -n.get(k, 0), k))


def common_sizes(bases: list[str]) -> dict[str, list[str]]:
    """Boysuz genel ürün → en yaygın boylu gruplar (order_sizes sırası): resmî fiyat (son COMMON_DAYS gün, zincir × ürün
    adı) ve topluluk satırları (kişi × gün) sayılır. "sut" → ["sut|1l", "sut|500ml", …]; "elma" → ["elma|kg", …].
    Biçimlere ayrılan ürünün üst adı ("kahve": product_groups.FORM_CHILDREN) kendi satırı azsa her alt biçimin
    varsayılan boyu sırayla eklenir ("kahve" → ["turk-kahvesi|100g", "hazir-kahve|100g", "filtre-kahve|250g", …])."""
    from datetime import date, timedelta

    from app import film_scope, product_groups, system

    bases = sorted({b for b in bases if b})
    if not bases:
        return {}
    children = {b: product_groups.child_bases(b) for b in bases}
    want = sorted({*bases, *(x for v in children.values() for x in v)})
    since = (date.today() - timedelta(days=COMMON_DAYS)).isoformat()
    like = " OR ".join("group_key LIKE ?" for _ in want)
    args = [f"{b}|%" for b in want]
    with system.connect() as c:
        off = c.execute(f"SELECT group_key, COUNT(DISTINCT chain || '|' || title) FROM official_prices WHERE day >= ? "
                        f"AND ({like}) GROUP BY group_key", [since, *args]).fetchall()
        com = c.execute(f"SELECT group_key, COUNT(DISTINCT contributor || day) FROM community_prices WHERE day >= ? "
                        f"AND ({like}){film_scope.community_clause()} GROUP BY group_key", [since, *args]).fetchall()
    n: dict[str, int] = {}
    for k, v in [*off, *com]:
        n[k] = n.get(k, 0) + v
    by: dict[str, list[str]] = {}
    for k in n:
        by.setdefault(k.split("|", 1)[0], []).append(k)
    out: dict[str, list[str]] = {}
    for b in bases:
        own = order_sizes(by.get(b, []), n)
        kids = [(cb, order_sizes(by[cb], n)) for cb in children[b] if by.get(cb)]
        kid_n = sum(n[k] for _cb, ks in kids for k in ks)
        if kids and sum(n[k] for k in own) < kid_n * PARENT_SHARE:
            own = [*own, *(ks[0] for _cb, ks in kids)]
        if own:
            out[b] = own
    return out


def size_text(base: str, key: str) -> str | None:
    """Boyun görünen adı: satırın kendi ürününde yalnız boy ("100 g"); üst addan gelen alt biçimde biçim + boy
    ("kahve" satırında "Türk Kahvesi · 100 g")."""
    from app import product_groups

    sl = product_groups.size_label(key)
    if key.split("|", 1)[0] == base:
        return sl
    name = product_groups.generic_of_key(key)
    return f"{name} · {sl}" if name and sl else sl or name


def _resolve_known(texts: list[str], learn: bool = True) -> tuple[list[dict | None], list[dict | None]]:
    """(gruplar, kişinin ürünleri): kural → önbellek; tanınmayan satır yapay zekâya gitmeden önce kişinin kendi aldığı
    ürünlere (app/shopping_known.py match: "Orneksan slims mavi" → fişteki "ÖRNEKSAN SLIMS MAVİ SİGARA"; satır o ürünün
    grubuna ve son alımına bağlanır) ve katalog ürün adlarına (product_groups.catalog_contains) bakılır. Hâlâ
    tanınmayan ad kuyruğa girer (learn) ve geçici grupta kalır (pending)."""
    from app import product_groups, shopping_known

    groups = product_groups.groups_for([{"name": t} for t in texts], learn=False)
    known: list[dict | None] = [None] * len(texts)
    prods, todo = None, []
    for i, (t, g) in enumerate(zip(texts, groups)):
        if g and not g.get("pending"):
            continue
        prods = shopping_known.products() if prods is None else prods
        kp = shopping_known.match(t, prods)
        if kp and kp.get("group"):
            groups[i], known[i] = kp["group"], kp
        elif (cg := product_groups.catalog_contains(t)):
            groups[i] = cg
        elif g:
            todo.append(t)
    if learn and todo:
        product_groups.enqueue(todo)
    return groups, known


def _resolve(texts: list[str]) -> list[dict | None]:
    return _resolve_known(texts)[0]


def _sized(rs: list[dict], groups: list[dict | None],
           known: list[dict | None] | None = None) -> list[tuple[dict | None, dict | None]]:
    """(grup, assumed): kişinin seçtiği boy (pinned_key) önce; kişinin ürününe bağlanan satır o ürünün grubunda kalır;
    boysuz yazılan satır ("Süt", "Elma") en yaygın boya çözülür ve assumed {key, label, size_label} döner (istemci
    "1 L kabul edildi" yazar, dokununca boy seçtirir)."""
    from app import product_groups

    known = known or [None] * len(rs)
    bases = [g["key"] for r, g, k in zip(rs, groups, known) if g and "|" not in g["key"] and not g.get("pending")
             and not r.get("pinned_key") and not k]
    common = common_sizes(bases)
    want = [r["pinned_key"] for r in rs if r.get("pinned_key")] + [k for v in common.values() for k in v]
    labels = product_groups.labels(want)
    out = []
    for r, g, k in zip(rs, groups, known):
        pin = r.get("pinned_key")
        if k and not pin:
            out.append((g, None))
        elif pin:
            base = (g or {}).get("label", "").split(" · ")[0] or pin.split("|")[0]
            sl = product_groups.size_label(pin)
            lab = labels.get(pin) or (f"{base} · {sl}" if sl else base)
            out.append(({**({k: g[k] for k in ("category", "subcategory") if g and g.get(k)}), "key": pin,
                         "label": lab, "pinned": True}, None))
        elif g and "|" not in g["key"] and not g.get("pending") and common.get(g["key"]):
            k = default_size(common[g["key"]], r.get("qty") or 1, r.get("unit"))
            sl = size_text(g["key"], k)
            lab = labels.get(k) or (f"{g['label']} · {sl}" if k.split("|", 1)[0] == g["key"] else
                                    f"{product_groups.generic_of_key(k) or g['label']} · {product_groups.size_label(k)}")
            out.append(({**g, "key": k, "label": lab}, {"key": k, "label": lab, "size_label": sl}))
        else:
            out.append((g, None))
    return out


UNIT_FAMILY = {"kg": "kg", "g": "kg", "L": "L", "ml": "L"}


def default_size(keys: list[str], qty: float = 1, unit: str | None = None) -> str:
    """Boysuz satırın varsayılan boyu (keys: common_sizes sırası). Adette ilk boy (en yaygın türün en yaygın boyu);
    kg / g / L / ml'de aynı türden boy (miktarla aynı boy varsa o: "1 kg çekirdek kahve" → 1 kg'lık paket, yoksa o
    türün en yaygını: 250 g × 4). O türde boy yoksa ilk boy (satır unit_mismatch olur: o birimde satılmıyor)."""
    from app import product_groups

    fam = UNIT_FAMILY.get(unit or "adet")
    if not fam:
        return keys[0]
    amount = {"kg": qty, "g": qty / 1000, "L": qty, "ml": qty / 1000}[unit]
    same = [k for k in keys if (product_groups.per_unit(k) or (0, ""))[1] == fam]
    exact = [k for k in same if abs(product_groups.per_unit(k)[0] - amount) < 1e-9]
    return (exact or same or keys)[0]


def amount_label(qty: float, unit: str | None) -> str:
    """"2,5 kg", "6 adet", "500 g", "1,5 L"."""
    q = f"{float(qty):g}".replace(".", ",")
    return f"{q} {unit or 'adet'}"


def mult(qty: float, unit: str | None, key: str | None) -> float | None:
    """Teklif fiyatının çarpanı: adette adet (paket fiyatı × adet); kg / g / L / ml'de miktar ÷ grubun boyu (aynı
    türde: ağırlık ↔ kg, hacim ↔ L). Grubun boyu bilinmiyor ya da tür uyuşmuyorsa None (fiyat hesaplanmaz)."""
    from app import product_groups

    unit = unit or "adet"
    if unit == "adet":
        return float(qty)
    amount, fam = {"kg": (qty, "kg"), "g": (qty / 1000, "kg"), "L": (qty, "L"), "ml": (qty / 1000, "L")}[unit]
    pu = product_groups.per_unit(key)
    if not pu or pu[1] != fam:
        return None
    return amount / pu[0]


def _out(r: dict, group: dict | None, names: dict[int, str], assumed: dict | None = None,
         own_product: dict | None = None, own_place: dict | None = None) -> dict:
    from app import shop_brand

    unit = r.get("unit") or "adet"
    key = group["key"] if group else None
    m = mult(r["qty"], unit, key) if key else None
    return {"id": r["id"], "text": r["text"], "qty": r["qty"], "unit": unit,
            "amount_label": amount_label(r["qty"], unit), "done": bool(r["done"]), "group": group,
            "group_key": key, "assumed": assumed if unit == "adet" else None, "mult": m,
            "unit_mismatch": bool(key and "|" in key and unit != "adet" and m is None),
            "added_by": r["added_by"], "added_by_name": names.get(r["added_by"]), "created_at": r["created_at"],
            "done_at": r["done_at"], "own_product": own_product, "own_place": own_place,
            "store": r.get("store"), "brand": r.get("brand"),
            "brand_line": r.get("brand_line") if r.get("brand") else None,
            "brand_auto": bool(r.get("brand") and r.get("brand_auto")),
            "brand_label": shop_brand.label(r.get("brand"), r.get("brand_line")),
            "actual": ({"unit_price": r.get("actual_unit"), "line_total": r.get("actual_line"), "entry_id": r["entry_id"]}
                       if r.get("entry_id") else None)}


def _rows_out(rs: list[dict]) -> list[dict]:
    """Satırlar → istemci biçimi (grup, boy, kişinin ürünü, kafe yeri). Tanınmayan satırlar arka planda hemen
    sınıflandırılır (product_groups.kick_names: yalnız bu adlar, tek çağrı)."""
    from app import product_groups, shopping_known

    if not rs:
        return []
    groups, known = _resolve_known([r["text"] for r in rs])
    sized = _sized(rs, groups, known)
    pending = [r["text"] for r, g, k in zip(rs, groups, known) if g and g.get("pending") and not k and not r["done"]]
    if pending:
        product_groups.kick_names(pending)
    cafe = shopping_known.cafe_for([g["key"].split("|", 1)[0] for g, _a in sized if g])
    names = _people()
    out = []
    for r, (g, a), k in zip(rs, sized, known):
        place = (k or {}).get("cafe") or (cafe.get(g["key"].split("|", 1)[0]) if g else None)
        out.append(_out(r, g, names, a, shopping_known.own_product(k),
                        {"place": place["place"], "price": place["price"]} if place else None))
    return out


def own_offer(it: dict) -> dict | None:
    """Kişinin ürününe bağlanan satırın kendi son alımı (teklif biçiminde: source "own", product)."""
    op = it.get("own_product")
    if not op or not op.get("price"):
        return None
    return {"chain": op["place"], "price": op["price"], "source": "own", "date": op.get("date"), "product": op["name"]}


def offers_for(items: list[dict], limit: int = 5, hidden=None) -> dict[int, list[dict]]:
    """Satır → teklifler (ucuzdan): grubun teklifleri (price_sources.best_offers); kişinin ürününe bağlanan satırda
    gruptaki BAŞKA ürünlerin kendi fişi fiyatı yerine o ürünün son alımı ("Senin son alımın: ₺95 · Örnekbüfe").

    brand seçili satır (shop_brand_size) yalnız o markanın tekliflerini alır (kendi fişlerin + Market Fiyatı; yoksa boş);
    addan okunan seri (brand_line, shop_brand_auto: "Turist") varsa yalnız adında o kelimeler geçen ürünler.
    store bağlı satır (shop_store_pin) yalnız o mağazanın teklifini alır (yoksa boş) ve kişinin mağaza süzgeci
    (hidden, app/shop_stores.py) ona uygulanmaz: açık seçim süzgeçten önce gelir. Diğer satırlarda hidden'daki mağazalar
    hiç görünmez."""
    from app import price_sources, product_groups

    want = [i for i in items if i["group"]]
    calls: dict[tuple[str | None, str | None, bool], list[dict]] = {}
    for it in want:  # (marka, seri, mağazaya bağlı mı) başına tek toplu istek
        b = it.get("brand") or None
        calls.setdefault((b, (it.get("brand_line") or None) if b else None, bool(it.get("store"))), []).append(it)
    out: dict[int, list[dict]] = {}
    for (brand, line, pinned_store), its in calls.items():
        wide = pinned_store or any(i.get("own_product") for i in its)
        best = price_sources.best_offers([i["group"]["key"] for i in its], limit=50 if wide else limit, brand=brand,
                                         hidden=None if pinned_store else hidden, line=line)
        for it in its:
            key = it["group"]["key"]
            offs = best.get(key, [])
            if (own := own_offer(it)) and not brand:
                offs = sorted([o for o in offs if o["source"] != "own"] +
                              [{**own, **product_groups.unit_price(own["price"], key)}],
                              key=lambda o: (o["price"], o["chain"]))
            if pinned_store:
                sk = price_sources._chain_key(price_sources.chain_name(it["store"]))
                offs = [o for o in offs if price_sources._chain_key(price_sources.chain_name(o["chain"])) == sk][:1]
            out[it["id"]] = offs[:limit]
    return out


def list_items(with_best: bool = False, hidden=None) -> list[dict]:
    """Liste (önce işaretlenmemişler, eklenme sırasıyla). Geçici grubu öğrenilmiş satırın group_key'i güncellenir.
    with_best: her satıra best (en ucuz teklif {chain, price, unit_price_per?, per_unit?, source, product?, brand?} |
    null; mağazaya bağlı satırda o mağazanın teklifi + pinned: true, yoksa null ve store_missing: true). hidden: kişinin
    mağaza süzgeci (app/shop_stores.py)."""
    with db.connect() as c:
        rs = db.rows(c.execute("SELECT * FROM shopping_list ORDER BY done, id"))
    out = _rows_out(rs)
    stale = [(i["group_key"], r["id"]) for r, i in zip(rs, out) if i["group_key"] != r["group_key"]]
    if stale:
        with db.connect() as c:
            c.executemany("UPDATE shopping_list SET group_key = ? WHERE id = ?", stale)
    _auto_brands(rs, out)
    if with_best:
        from app import money, price_sources

        best = offers_for([i for i in out if not i["done"]], limit=1, hidden=hidden)
        # markalı satır: "Marka fark etmez" olsaydı en ucuzu (ipucu: "Marka fark etmez: en ucuz ₺200 (Efor)")
        free = offers_for([{**i, "brand": None, "brand_line": None, "store": None, "own_product": None}
                           for i in out if not i["done"] and i.get("brand")], limit=1, hidden=hidden)
        for i in out:
            o = (best.get(i["id"]) or [None])[0]
            i["best"] = {k: o[k] for k in ("chain", "price", "unit_price_per", "per_unit", "source", "n", "date", "product")
                         if k in o and (k not in ("date", "product") or o["source"] in ("own", "resmi"))} if o else None
            if i["best"]:
                i["best"]["chain"] = price_sources.chain_name(i["best"]["chain"])
                i["best"]["line_total"] = money.money(o["price"] * i["mult"]) if i["mult"] is not None else None
                if i.get("brand") and o.get("brand"):
                    i["best"]["brand"] = o["brand"]
                if i.get("store"):
                    i["best"]["pinned"] = True
            if i.get("store") and not i["done"] and i["group"]:
                i["store_missing"] = i["best"] is None
            if i.get("brand") and not i["done"] and i["group"]:
                i["brand_missing"] = i["best"] is None
                i["any_brand"] = _any_brand((free.get(i["id"]) or [None])[0], i["mult"])
    return out


def _auto_brands(rs: list[dict], out: list[dict]) -> None:
    """Henüz bakılmamış satırlarda (brand_auto NULL: eski satır ya da grubu sonradan öğrenilen) addaki markayı yaz
    (app/shop_brand.py detect). Geçici gruptaki satır sonraya kalır; kişinin ürününe bağlanan ya da markası seçili
    satırda bakılmaz."""
    from app import shop_brand

    writes = []
    for r, i in zip(rs, out):
        if r.get("brand_auto") is not None or r["done"]:
            continue
        if r.get("brand") or i.get("own_product"):
            writes.append((r.get("brand"), r.get("brand_line"), 0, r["id"]))
            continue
        g = i.get("group")
        if not g or g.get("pending"):
            continue
        got = shop_brand.detect(r["text"], g)
        writes.append((got["brand"], got["line"], 1, r["id"]) if got else (None, None, 0, r["id"]))
        if got:
            r.update(brand=got["brand"], brand_line=got["line"], brand_auto=1)
            i.update(brand=got["brand"], brand_line=got["line"], brand_auto=True,
                     brand_label=shop_brand.label(got["brand"], got["line"]))
    if writes:
        with db.connect() as c:
            c.executemany("UPDATE shopping_list SET brand = ?, brand_line = ?, brand_auto = ? WHERE id = ? "
                          "AND brand_auto IS NULL", writes)


def _any_brand(o: dict | None, m: float | None = None) -> dict | None:
    """"Marka fark etmez" olsaydı en ucuz teklif (markalı satırın ipucu): {chain, price, brand?, line_total?, …}."""
    from app import money, price_sources

    if not o:
        return None
    out = {"chain": price_sources.chain_name(o["chain"]), "price": o["price"],
           "brand": price_sources._brand_label(o["brand"]) if o.get("brand") else None,
           **{k: o[k] for k in ("unit_price_per", "per_unit") if k in o}}
    if m is not None:
        out["line_total"] = money.money(o["price"] * m)
    return out


def brand_search(q: str, limit: int = 30, hidden=None) -> list[dict] | None:
    """Fiyatlar araması (GET /api/prices/groups?q) adda marka yazılınca ("çaykur turist çay 1 kg"; shop_brand_auto):
    ürünün grubu (boy yazılmışsa o boy, değilse en yaygın boylar) o markanın ve serinin teklifleriyle — [{key, label
    ("Çaykur Turist Çay · 1 kg"), category, subcategory, best [teklif], hidden_count, brand, brand_line, brand_label,
    any_brand (marka fark etmez en ucuz) | null}]; markanın teklifi olmayan boy best boş gelir (any_brand varsa).
    Adda marka yoksa ya da ürün tanınmıyorsa None (olağan arama). Ad öğrenme kuyruğuna girmez."""
    from app import price_sources, product_groups, shop_brand

    g = _resolve_known([q], learn=False)[0][0]
    got = shop_brand.detect(q, g)
    if not got:
        return None
    key = g["key"]
    base = key.split("|", 1)[0]
    keys = [key] if "|" in key else (common_sizes([base]).get(base) or [base])[:max(1, min(limit, 8))]
    best = price_sources.best_offers(keys, brand=got["brand"], line=got["line"], hidden=hidden)
    free = price_sources.best_offers(keys, limit=1, hidden=hidden)
    labels = product_groups.labels(keys)
    paths = product_groups.paths(keys)
    bl = shop_brand.label(got["brand"], got["line"])
    out = []
    for k in keys:
        offs = [{**o, "chain": price_sources.chain_name(o["chain"])} for o in best.get(k, [])]
        f = _any_brand((free.get(k) or [None])[0])
        if not offs and not f:
            continue
        out.append({"key": k, "label": f"{bl} {labels.get(k) or g['label']}", "category": (paths.get(k) or (None, None))[0],
                    "subcategory": (paths.get(k) or (None, None))[1], "best": offs,
                    "hidden_count": len(hidden.by_key.get(k, ())) if hidden is not None else 0,
                    "brand": got["brand"], "brand_line": got["line"], "brand_label": bl, "any_brand": f})
    out.sort(key=lambda r: not r["best"])  # fiyatlı boylar önce
    return out[:limit]


def _size_sort(k: str) -> tuple:
    """Boy sırası: küçükten büyüğe (aynı türde; 500 ml, 750 ml, 1 L, 2 L, 5 L), türler L → kg → adet, boysuz en son."""
    from app import product_groups

    pu = product_groups.per_unit(k)
    if not pu:
        return (9, 0.0, k)
    return ({"L": 0, "kg": 1, "adet": 2}.get(pu[1], 3), pu[0], k)


def sizes(rid: int, brand: str | None = None, hidden=None) -> list[dict] | None:
    """Satırın boy seçenekleri (GET /api/shopping-list/{id}/sizes): [{key, label, size_label, size_amount?, size_unit?,
    chain, price, unit_price_per?, per_unit?, best_value, hidden_count}] küçükten büyüğe (en yaygın en çok 8 boy).
    chain / price: o boyun en ucuzu ("En ucuz"); best_value: aynı türde (L, kg, adet) litre / kilo / adet fiyatı en
    düşük boy ("En kârlı"; en az iki fiyatlı boy varsa). size_unit: boyun türü (L | kg | adet; per_unit fiyatsız boyda da).
    Satır kg / g / L / ml ile yazıldıysa yalnız o türün boyları (miktar paket boyuna bölünür: 1 kg → 4 × 250 g). Üst ad
    ("kahve") yazılmışsa biçimlerin boyları biçim sırasıyla, size_label biçimle ("Türk Kahvesi · 100 g"). brand
    verilirse (yoksa satırın markası; "" marka fark etmez) yalnız o markanın teklifleri ve o markanın boyları da
    eklenir. Kişinin kapattığı mağazalar (hidden) hiç görünmez; hidden_count o boyda gizlenen mağaza sayısı. Satır yoksa
    None."""
    from app import price_sources, product_groups

    with db.connect() as c:
        r = c.execute("SELECT * FROM shopping_list WHERE id = ?", (rid,)).fetchone()
    if not r:
        return None
    g = _resolve([r["text"]])[0]
    if not g:
        return []
    line = r["brand_line"] if brand is None or (r["brand"] and price_sources.brand_norm(brand) ==
                                                price_sources.brand_norm(r["brand"])) else None
    brand = (brand if brand is not None else r["brand"]) or None
    line = line if brand else None
    base = g["key"].split("|", 1)[0]
    fam = UNIT_FAMILY.get(r["unit"] or "adet")
    keys = common_sizes([base]).get(base, [])
    if fam:  # kg / L ile yazılan satır: yalnız o türün boyları (₺/kg karşılaştırılır; "4 × 250 g")
        keys = [k for k in keys if _family(k) == fam]
    keys = keys[:8]
    if "|" in g["key"] and g["key"] not in keys and (not fam or _family(g["key"]) == fam):
        keys = [g["key"], *keys][:8]
    own_bases = {base, *product_groups.child_bases(base)}
    if r["pinned_key"] and r["pinned_key"] not in keys and r["pinned_key"].split("|", 1)[0] in own_bases:
        keys = [r["pinned_key"], *keys][:9]
    if brand:
        bn = price_sources.brand_norm(brand)
        for b in price_sources.brands(brand_keys(base)):
            if price_sources.brand_norm(b["brand"]) == bn:
                keys += [k for k in b["sizes"] if k not in keys and "|" in k and (not fam or _family(k) == fam)][:8]
    kids = product_groups.child_bases(base)  # üst adda ("kahve") biçimler kendi sırasıyla, her biri küçükten büyüğe
    keys = sorted(keys, key=lambda k: (kids.index(k.split("|", 1)[0]) + 1 if k.split("|", 1)[0] in kids else 0,
                                       _size_sort(k)))
    labels = product_groups.labels(keys)
    best = price_sources.best_offers(keys, limit=1, brand=brand, hidden=hidden, line=line)
    name = g["label"].split(" · ")[0]
    out = []
    for k in keys:
        o = (best.get(k) or [None])[0]
        sl = size_text(base, k)
        pu = product_groups.per_unit(k)
        out.append({"key": k, "label": labels.get(k) or f"{product_groups.generic_of_key(k) or name} · "
                                                         f"{product_groups.size_label(k)}", "size_label": sl,
                    **({"size_amount": pu[0], "size_unit": pu[1]} if pu else {}),
                    "chain": price_sources.chain_name(o["chain"]) if o else None, "price": o["price"] if o else None,
                    **({x: o[x] for x in ("unit_price_per", "per_unit") if o and x in o}),
                    "best_value": False, "hidden_count": len(hidden.by_key.get(k, ())) if hidden is not None else 0})
    mark_best_value(out)
    return out


def mark_best_value(rows: list[dict]) -> None:
    """best_value: aynı türde (per_unit) en az iki fiyatlı boy varsa birim fiyatı en düşük olan ("En kârlı")."""
    by_fam: dict[str, list[dict]] = {}
    for x in rows:
        if x.get("unit_price_per") is not None:
            by_fam.setdefault(x["per_unit"], []).append(x)
    for fam in by_fam.values():
        if len(fam) >= 2:
            min(fam, key=lambda x: (x["unit_price_per"], _size_sort(x["key"])))["best_value"] = True


def combo_keys(key: str, extra: list[str] | None = None) -> list[str]:
    """Seçici için boylar: verilen anahtar boyluysa ("sut|1l") yalnız o; boysuzsa ("sut") en yaygın 8 boy (+ extra)."""
    if "|" in key:
        return [key]
    base = key
    keys = common_sizes([base]).get(base, [])[:8]
    return list(dict.fromkeys([*(k for k in extra or [] if k and k.split("|", 1)[0] == base), *keys])) or [base]


def combos_out(keys: list[str], chain: str | None = None, hidden=None) -> dict:
    """Zincir × marka × boy seçicisi (shop_combo_picker): {keys, rows [price_sources.combos satırı + size_label],
    chains [zincir, ucuzdan], brands [marka, çok satırlı önce], sizes [{key, size_label}] küçükten büyüğe, hidden
    {count, chains, local}, attribution [kaynak yazısı]}."""
    from app import price_import, price_sources, product_groups

    rows = price_sources.combos(keys, hidden=hidden, chain=chain)
    for r in rows:
        r["size_label"] = product_groups.size_label(r["key"])
    chains = list(dict.fromkeys(r["chain"] for r in rows))
    cnt: dict[str, int] = {}
    for r in rows:
        if r["brand"]:
            cnt[r["brand"]] = cnt.get(r["brand"], 0) + 1
    present = sorted({r["key"] for r in rows}, key=_size_sort)
    return {"keys": keys, "rows": rows, "chains": chains,
            "brands": sorted(cnt, key=lambda b: (-cnt[b], b.casefold())),
            "sizes": [{"key": k, "size_label": product_groups.size_label(k)} for k in present],
            "hidden": hidden.out(keys) if hidden is not None else {"count": 0, "chains": [], "local": False},
            "attribution": [price_import.attribution()] if any(r["source"] == "resmi" for r in rows) else []}


def item_combos(rid: int, chain: str | None = None, all_sizes: bool = True, hidden=None) -> dict | None:
    """Satırın seçicisi (GET /api/shopping-list/{id}/combos): bütün yaygın boylar (all_sizes) ya da satırın boyu;
    selected {store, brand, key} satırın şimdiki seçimi. Satır yoksa None."""
    with db.connect() as c:
        r = c.execute("SELECT * FROM shopping_list WHERE id = ?", (rid,)).fetchone()
    if not r:
        return None
    g = _resolve([r["text"]])[0]
    sel = {"store": r["store"], "brand": r["brand"], "key": r["pinned_key"] or (g or {}).get("key")}
    if not g or g.get("pending"):
        return {"keys": [], "rows": [], "chains": [], "brands": [], "sizes": [],
                "hidden": {"count": 0, "chains": [], "local": False}, "attribution": [], "selected": sel}
    key = r["pinned_key"] or g["key"]
    keys = combo_keys(key.split("|", 1)[0], [key]) if all_sizes else combo_keys(key)
    return {**combos_out(keys, chain, hidden), "selected": sel}


def brand_keys(base: str) -> list[str]:
    """Bir ürünün (boysuz anahtar "sut") marka aranacak grupları: en yaygın boylar + boysuz anahtar."""
    return [*common_sizes([base]).get(base, [])[:12], base]


def _clean_text(text: str) -> str:
    t = " ".join((text or "").split())[:TEXT_MAX]
    if not t:
        raise ValueError("Ne alınacağını yaz")
    return t


def _clean_qty(qty, unit: str = "adet") -> float:
    q = float(qty)
    hi = UNIT_MAX.get(unit, 999)
    if not 0 < q <= hi:
        raise ValueError("Adet 0 ile 999 arasında olmalı" if unit == "adet" else f"Miktar 0 ile {hi:,} {unit} arasında olmalı".replace(",", "."))
    return round(q, 3)


def _clean_unit(unit) -> str:
    if unit is None or unit == "":
        return "adet"
    if unit not in UNITS:
        raise ValueError("Birim adet, kg, g, L ya da ml olmalı")
    return unit


_NUM_RX = r"(\d{1,5}(?:[.,]\d{1,3})?)"
_UNIT_RX = r"(kg|kilogram|kilo|gram|gr|g|litre|lt|l|ml|adet|ad|tane)"


def _amount_parts(t: str) -> tuple[str, float, str] | None:
    """Metindeki miktar: (ad, miktar, birim) ya da None. "2.5 kg elma", "elma 2,5kg", "6 adet yumurta", "süt 6 adet"."""
    import re

    for rx in (rf"{_NUM_RX}\s*{_UNIT_RX}\.?\s+(\D.*)", rf"(.*\D)\s+{_NUM_RX}\s*{_UNIT_RX}\.?"):
        m = re.fullmatch(rx, t, re.IGNORECASE)
        if not m:
            continue
        name, num, word = (m[3], m[1], m[2]) if rx.startswith(_NUM_RX) else (m[1], m[2], m[3])
        name = name.strip(" ,-")
        if not name:
            return None
        return name, float(num.replace(",", ".")), _UNIT_WORDS[word.lower().replace("ı", "i")]
    return None


def _pack_size(name: str, value: float, unit: str) -> bool:
    """Ağırlık / hacim yazısı paket boyu mu ("süt 1 L") yoksa miktar mı ("2,5 kg elma")? Tartılı ürün (meyve, sebze,
    et) hep miktar; ürünün bilinen boylarından biriyse (son 14 gün fiyat satırları) boy; ürünün aynı türde bilinen boyu
    varsa miktar; hiç bilgi yoksa boy (metin olduğu gibi kalır)."""
    from app import product_groups

    g = _resolve([name])[0]
    if not g:
        return False
    if g.get("category") in WEIGHED_CATEGORIES and unit in ("kg", "g"):
        return False
    base = g["key"].split("|", 1)[0]
    sizes = common_sizes([base]).get(base, [])
    grams = {"kg": ("g", 1000), "g": ("g", 1), "L": ("ml", 1000), "ml": ("ml", 1)}[unit]
    key = f"{base}|{product_groups.size_key({'value': value * grams[1], 'base': grams[0], 'pack': 1})[0]}"
    if key in sizes:
        return True
    fam = "kg" if unit in ("kg", "g") else "L"
    return not any((product_groups.per_unit(k) or (0, ""))[1] == fam for k in sizes)


def parse_amount(text: str, qty: float = 1, unit: str | None = None) -> tuple[str, float, str]:
    """(ad, miktar, birim): miktar ve birim ayrıca verilmemişse (qty 1, unit boş) metinden: "2.5 kg elma" →
    ("elma", 2.5, "kg"), "6x süt" / "6 süt" → ("süt", 6, "adet"); paket boyu metinde kalır ("süt 1 L")."""
    import re

    if unit not in (None, "", "adet") or qty != 1:
        return text, qty, _clean_unit(unit)
    t, q = _lead_qty(text, qty)
    if q != 1 or t != text:
        return t, q, "adet"
    m = re.fullmatch(r"(\d{1,3})\s+(\D{2}.*)", text)  # "6 süt"
    if m and 0 < int(m[1]) <= 999 and not _amount_parts(text):
        return m[2].strip(), float(m[1]), "adet"
    got = _amount_parts(text)
    if not got:
        return text, 1, "adet"
    name, value, u = got
    if not 0 < value <= UNIT_MAX[u]:
        return text, 1, "adet"
    if u == "adet":
        return name, value, "adet"
    if _pack_size(name, value, u):
        return text, 1, "adet"
    return name, value, u


def split_amount(text: str, qty: float = 1, unit: str | None = None) -> tuple[str, float, str]:
    """parse_amount + birimi ayrıca seçilmiş satır: miktar 1 ve birim kg / g / L / ml seçiliyken adda aynı türden miktar
    yazılıysa ("1 kg çekirdek kahve" + kg) ad yalnız ürün olur, miktar addakidir ("çekirdek kahve", 1, kg); adda boy
    yazıp kg seçen kişi aynı kiloyu iki kez yazmış olmasın."""
    fam = UNIT_FAMILY.get(unit or "adet")
    if fam and qty == 1 and (got := _amount_parts(text)) and UNIT_FAMILY.get(got[2]) == fam \
            and 0 < got[1] <= UNIT_MAX[got[2]]:
        return got
    return parse_amount(text, qty, unit)


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
    return _rows_out([r])[0]


def _detect_brand(text: str, g: dict | None, known: dict | None) -> tuple[str | None, str | None, int | None]:
    """(brand, brand_line, brand_auto) yeni / adı değişen satır için (shop_brand_auto): addaki marka ve seri; kişinin
    ürününe bağlanan satırda yok (0); grup henüz geçiciyse sonraya (NULL: list_items okurken bakar)."""
    from app import shop_brand

    if known:
        return None, None, 0
    if not g or g.get("pending"):
        return None, None, None if g else 0
    got = shop_brand.detect(text, g)
    return (got["brand"], got["line"], 1) if got else (None, None, 0)


def add(text: str, qty: float = 1, uid: int | None = None, group_key: str | None = None, unit: str | None = None,
        client_key: str | None = None) -> dict:
    """client_key: istemcinin işlem kimliği (çevrimdışı kuyruğun tekrarı); listede aynı kimlikli satır varsa yenisi
    eklenmez, o satır döner."""
    t, q, u = split_amount(_clean_text(text), float(qty), unit)
    t, q = _clean_text(t), _clean_qty(q, u)
    pin = _clean_key(group_key)
    with db.connect() as c:
        if client_key and (dup := c.execute("SELECT * FROM shopping_list WHERE client_key = ?", (client_key,)).fetchone()):
            return _row_out(dict(dup))
        if c.execute("SELECT COUNT(*) FROM shopping_list").fetchone()[0] >= MAX_ITEMS:
            raise ValueError(f"Listede en fazla {MAX_ITEMS} satır olabilir")
    gs, known = _resolve_known([t])
    g = gs[0]
    b, line, auto = _detect_brand(t, g, known[0])
    with db.connect() as c:
        rid = c.execute("INSERT INTO shopping_list (text, group_key, qty, unit, added_by, pinned_key, brand, brand_line, "
                        "brand_auto, client_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (t, pin or (g["key"] if g else None), q, u, uid, pin, b, line, auto, client_key)).lastrowid
        r = dict(c.execute("SELECT * FROM shopping_list WHERE id = ?", (rid,)).fetchone())
    from app import push

    push.after_list_add()  # ortak defter: diğer kişilere toplu bildirim (arka planda)
    return _row_out(r)  # tanınmayan ad: arka planda hemen sınıflandırılır (kick_names)


STORE_MAX = 60
BRAND_MAX = 40


def _clean_store(v) -> str | None:
    """Satırın bağlandığı mağaza ("Bu ürünü Migros'tan alacağım"): görünen zincir adı; boş / null kaldırır."""
    from app import price_sources

    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if not isinstance(v, str) or len(v.strip()) > STORE_MAX:
        raise ValueError("Mağaza adı geçersiz")
    return price_sources.chain_name(" ".join(v.split()))


def _clean_brand(v) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if not isinstance(v, str) or len(v.strip()) > BRAND_MAX:
        raise ValueError("Marka adı geçersiz")
    return " ".join(v.split())


def update(rid: int, data: dict) -> dict | None:
    """text, qty, unit, done (işaretlenince done_at bugünün zamanı, kaldırılınca boş), group_key (boy seçimi; null kaldırır;
    metin değişince seçim de kalkar), store (shop_store_pin: "Migros" | null), brand (shop_brand_size: "Sütaş" | null;
    metin değişince marka yeni addan okunur (shop_brand_auto; aynı istekte marka verilmediyse); kişinin verdiği
    marka addaki seriyi düşürür ve brand_auto 0 olur — aynı marka yeniden gönderilirse seri kalır). Değişen metinde miktar yazılıysa eklemedeki
    gibi okunur (split_amount: "2,5 kg elma" → elma, 2,5 kg). Birim başka türe geçince (adet → kg) o türde olmayan boy
    seçimi kalkar."""
    sets: dict = {}
    with db.connect() as c:
        cur = c.execute("SELECT qty, unit, text, pinned_key, brand FROM shopping_list WHERE id = ?", (rid,)).fetchone()
    if not cur:
        return None
    if data.get("text") is not None and _clean_text(data["text"]) != cur["text"]:
        # adda miktar yazıldıysa (eklerken olduğu gibi): "1 kg çekirdek kahve" → ad "çekirdek kahve", 1 kg
        u0 = data.get("unit") if data.get("unit") is not None else cur["unit"]
        q0 = float(data["qty"]) if data.get("qty") is not None else float(cur["qty"])
        t, q, u = split_amount(_clean_text(data["text"]), q0, u0)
        if t != _clean_text(data["text"]):
            data = {**data, "text": t, "qty": q, "unit": u}
    if data.get("text") is not None:
        sets["text"] = _clean_text(data["text"])
        gs, known = _resolve_known([sets["text"]])
        g = gs[0]
        sets["group_key"] = g["key"] if g else None
        sets["pinned_key"] = None
        if sets["text"] != cur["text"]:  # aynı metin yeniden gönderildi (düzenle → kaydet): marka kalır
            # yeni addaki marka (shop_brand_auto: "çaykur turist çay" → Çaykur Turist); yoksa marka fark etmez
            sets["brand"], sets["brand_line"], sets["brand_auto"] = _detect_brand(sets["text"], g, known[0])
    if "store" in data:
        sets["store"] = _clean_store(data["store"])
    if "brand" in data:
        from app import price_sources

        b = _clean_brand(data["brand"])
        old = sets["brand"] if "brand" in sets else cur["brand"]
        if not (b and old and price_sources.brand_norm(b) == price_sources.brand_norm(old)):
            # kişinin seçimi (marka fark etmez ya da başka marka): addaki seri düşer, ad yeniden okunmaz
            sets["brand"], sets["brand_line"], sets["brand_auto"] = b, None, 0
    if "group_key" in data:
        sets["pinned_key"] = _clean_key(data["group_key"])
        if sets["pinned_key"]:
            sets["group_key"] = sets["pinned_key"]
    if data.get("unit") is not None:
        sets["unit"] = _clean_unit(data["unit"])
        pin = sets.get("pinned_key", cur["pinned_key"])
        fam = UNIT_FAMILY.get(sets["unit"])
        if pin and fam and _family(pin) != fam:  # 10 adetlik boy seçiliyken kg'a geçildi: boy otomatiğe döner
            g = _resolve([sets.get("text") or cur["text"]])[0]
            sets["pinned_key"], sets["group_key"] = None, (g["key"] if g else None)
    if data.get("qty") is not None or "unit" in sets:
        sets["qty"] = _clean_qty(data["qty"] if data.get("qty") is not None else cur["qty"], sets.get("unit") or cur["unit"])
    with db.connect() as c:
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

    from app import shop_stores

    items = [i for i in list_items() if not i["done"]]
    best = offers_for(items, limit=50, hidden=shop_stores.current())
    rows, chains = [], {}
    for it in items:
        offers = best.get(it["id"], []) if it["group"] and it["mult"] is not None else []
        offers = [{**o, "chain": price_sources.chain_name(o["chain"])} for o in offers]
        rows.append({**{k: it[k] for k in ("id", "text", "qty", "unit", "group", "assumed", "own_product", "own_place",
                                           "store", "brand")},
                     "best": offers[0] if offers else None,
                     "offers": offers[:5], "mult": it["mult"]})
        for o in offers:
            ch = chains.setdefault(o["chain"], {"chain": o["chain"], "total": 0.0, "covered": 0, "ids": set()})
            ch["total"] += it["mult"] * o["price"]
            ch["covered"] += 1
            ch["ids"].add(it["id"])
    all_ids = [it["id"] for it in items]
    out_chains = sorted(({"chain": ch["chain"], "total": money.money(ch["total"]), "covered": ch["covered"],
                          "missing": len(all_ids) - ch["covered"],
                          "missing_ids": [i for i in all_ids if i not in ch["ids"]]} for ch in chains.values()),
                        key=lambda x: (-x["covered"], x["total"], x["chain"]))
    priced = [r for r in rows if r["best"]]
    return {"items": rows, "chains": out_chains,
            "split_total": money.money(sum(r["mult"] * r["best"]["price"] for r in priced)),
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

    out = {"id": it["id"], "text": it["text"], "qty": it["qty"], "unit": it["unit"], "amount_label": it["amount_label"],
           "group_label": it["group"]["label"] if it["group"] else None,
           "price": o["price"], "line_total": money.money(it["mult"] * o["price"]),
           "unit_price_per": o.get("unit_price_per"), "per_unit": o.get("per_unit"), "brand": o.get("brand"),
           "source": o["source"], "date": o.get("date"), "assumed": it.get("assumed"),
           "own_place": it.get("own_place")}
    if o.get("product"):
        out["product"] = o["product"]
    if o.get("region"):
        out["region"] = o["region"]
    if it.get("store"):
        out["pinned"] = True  # kişi bu satırı bu mağazaya bağladı (shop_store_pin)
    return out


def _clean_chains(chains: list[str] | None) -> list[str]:
    """"Marketleri ben seçeyim": görünen zincir adları, tekrarsız, en çok MAX_STOPS."""
    from app import price_sources

    out: list[str] = []
    for c in chains or []:
        n = price_sources.chain_name(" ".join(str(c or "").split())[:60])
        if n and all(price_sources._chain_key(n) != price_sources._chain_key(x) for x in out):
            out.append(n)
    if len(out) > MAX_STOPS:
        raise ValueError(f"En çok {MAX_STOPS} market seçebilirsin")
    return out


SIZE_TIP_MIN = 2.0      # boy önerisi: en az bu kadar ₺ …
SIZE_TIP_PCT = 3.0      # … ve satır tutarının en az bu kadar yüzdesi ucuz
SIZE_TIP_MAX_RATIO = 3  # önerilen toplam miktar en çok ihtiyacın 3 katı


def _size_alts(priced: list[dict], hidden) -> tuple[dict[str, list[str]], dict[str, list[dict]], dict[str, str]]:
    """Boy önerisi için başka boylar: (ürün → boylu gruplar, grup → teklifler, grup → ad). Markalı, mağazaya bağlı ya
    da kişinin ürününe bağlı satırlarda öneri yok (kişi açıkça seçti)."""
    from app import price_sources, product_groups

    bases = sorted({it["group"]["key"].split("|", 1)[0] for it in priced
                    if product_groups.per_unit(it["group"]["key"]) and not it.get("brand") and not it.get("store")
                    and not it.get("own_product")})
    if not bases:
        return {}, {}, {}
    common = common_sizes(bases)
    alts = {b: [k for k in common.get(b, [])[:8] if product_groups.per_unit(k)] for b in bases}
    keys = sorted({k for v in alts.values() for k in v})
    best = price_sources.best_offers(keys, limit=50, hidden=hidden) if keys else {}
    return alts, best, product_groups.labels(keys)


def _size_tip(it: dict, line: dict, chainset: set[str], alts, best, labels) -> dict | None:
    """"En kârlı boy önerisi": aynı ihtiyacı (miktar × boy) plandaki bir mağazada başka boyla daha ucuza karşılamak
    (2 × 1 L yerine 1 × 2 L). Yalnız öneri; satır değişmez. {key, label, size_label, chain, packs, total, saving,
    unit_price_per, per_unit} ya da None."""
    import math

    from app import money, price_sources, product_groups

    key = it["group"]["key"]
    base = key.split("|", 1)[0]
    pu = product_groups.per_unit(key)
    if not pu or base not in alts or it.get("brand") or it.get("store") or it.get("own_product"):
        return None
    need = it["mult"] * pu[0]
    if need <= 0:
        return None
    cur = line["line_total"]
    top = None
    for k in alts[base]:
        if k == key:
            continue
        apu = product_groups.per_unit(k)
        if not apu or apu[1] != pu[1]:
            continue
        packs = math.ceil(need / apu[0] - 1e-9)
        if not 1 <= packs <= 99 or packs * apu[0] > need * SIZE_TIP_MAX_RATIO + 1e-9:
            continue
        for o in best.get(k, []):
            ch = price_sources.chain_name(o["chain"])
            if ch not in chainset:
                continue
            total = money.money(packs * o["price"])
            if cur - total < max(SIZE_TIP_MIN, cur * SIZE_TIP_PCT / 100):
                continue
            if top is None or (total, k) < (top["total"], top["key"]):
                top = {"key": k, "label": labels.get(k) or k, "size_label": product_groups.size_label(k),
                       "chain": ch, "packs": packs, "price": o["price"], "total": total,
                       "saving": money.money(cur - total), **product_groups.unit_price(o["price"], k)}
    return top


def optimize(max_stops: int = MAX_STOPS, stop_cost: float | None = None, chains: list[str] | None = None,
             hidden=None) -> dict:
    """Alınmamış satırlar için en iyi 1..max_stops duraklı plan (app/price_sources.py best_offers: zincir başına tek
    teklif; kişinin ili, resmî / topluluk / kendi fişin). Satır fiyatı = teklif × adet.

    plans[k]: k zincirli birleşimlerden (her zincire en az bir satır düşen) en çok satırı karşılayan, eşitse en ucuz;
    her satır birleşimde onu en ucuza satan zincire. recommended_k: 1 duraktan başla; bir sonraki durak satır
    kazandırıyorsa ya da aynı kapsamda en az stop_cost ₺ ucuzsa geç, değilse dur. saving_vs_single = 1 duraklı plan −
    bu plan; saving_vs_prev = bir önceki plan − bu plan; extra_vs_best = bu plan − önerilen plan.
    single: zincir başına "hepsi buradan" (covered çoktan aza, sonra ucuz); vs_recommended = toplam − önerilen.

    Kişinin mağaza süzgeci (hidden; app/shop_stores.py) kapattığı mağazaları hiç önermez. Mağazaya bağlı satır (store)
    yalnız o mağazadan; o mağaza her planda bir duraktır (durak sayısı en az bağlı mağaza sayısı; fiyatı yoksa durağın
    pinned_unpriced'ında). chains verilirse ("Marketleri ben seçeyim", 1–3 zincir) tek plan yalnız o mağazalar (+ bağlı
    mağazalar) üzerinden kurulur; orada bulunmayan satırlar manual.missing'te (en ucuz başka mağazasıyla).
    Her plan: saving_vs_market (satır başına mağazaların orta fiyatına göre tasarruf), size_saving ve satırlarda
    better_size (En kârlı boy önerisi; _size_tip)."""
    from datetime import date
    from itertools import combinations
    from statistics import median

    from app import db, money, prefs, price_sources, provinces, shop_stores

    max_stops = max(1, min(int(max_stops or MAX_STOPS), MAX_STOPS))
    chosen = _clean_chains(chains)
    cost = stop_cost_pref() if stop_cost is None else max(0.0, float(stop_cost))
    uid = (db.current_user() or {}).get("id")
    region = prefs.region(uid) or provinces.DEFAULT
    hidden = shop_stores.current() if hidden is None else hidden
    items = [i for i in list_items() if not i["done"]]
    best = offers_for(items, limit=50, hidden=hidden)
    offers: dict[int, dict[str, dict]] = {}  # satır → zincir → teklif
    for it in items:
        offs = best.get(it["id"], []) if it["group"] and it["mult"] is not None else []
        if offs:
            offers[it["id"]] = {}
            for o in offs:  # görünen zincir adı ("BİM (Dost)" → "BİM"); aynı ada düşen iki teklifin ucuzu
                ch = price_sources.chain_name(o["chain"])
                if ch not in offers[it["id"]] or o["price"] < offers[it["id"]][ch]["price"]:
                    offers[it["id"]][ch] = {**o, "chain": ch}
    priced = [it for it in items if it["id"] in offers]
    unpriced = [it["id"] for it in items if it["id"] not in offers]
    pins = {it["id"]: price_sources.chain_name(it["store"]) for it in items if it.get("store")}
    pinned_chains = list(dict.fromkeys(pins.values()))
    pin_unpriced = {ch: [{"id": it["id"], "text": it["text"]} for it in items
                         if pins.get(it["id"]) == ch and it["id"] not in offers] for ch in pinned_chains}

    chain_rows: dict[str, dict] = {}
    for it in priced:
        for ch, o in offers[it["id"]].items():
            c = chain_rows.setdefault(ch, {"chain": ch, "total": 0.0, "covered": 0})
            c["total"] += it["mult"] * o["price"]
            c["covered"] += 1
    ranked = sorted(chain_rows.values(), key=lambda c: (-c["covered"], c["total"], c["chain"]))
    cand = [c["chain"] for c in ranked[:MAX_CHAINS]]

    def evaluate(combo: tuple[str, ...], allow_empty: bool = False) -> dict | None:
        stops: dict[str, list] = {ch: [] for ch in combo}
        missing = []
        for it in priced:
            got = [(offers[it["id"]][ch]["price"], combo.index(ch), ch) for ch in combo if ch in offers[it["id"]]]
            if not got:
                missing.append(it["id"])
                continue
            ch = min(got)[2]
            line = _line(it, offers[it["id"]][ch])
            low = min(offers[it["id"]].values(), key=lambda o: (o["price"], o["chain"]))
            if low["chain"] != ch and low["price"] < offers[it["id"]][ch]["price"]:
                line["cheaper_elsewhere"] = {"chain": low["chain"], "price": low["price"]}  # birleşimde olmayan zincir
            stops[ch].append(line)
        if not allow_empty and any(not v and not pin_unpriced.get(ch) for ch, v in stops.items()):
            return None  # boş durak: daha az duraklı planla aynı
        out = [{"chain": ch, "total": money.money(sum(x["line_total"] for x in v)), "items": v,
                **({"pinned": True} if ch in pin_unpriced else {}),
                **({"pinned_unpriced": pin_unpriced[ch]} if pin_unpriced.get(ch) else {})} for ch, v in stops.items()]
        out.sort(key=lambda s: (-len(s["items"]), -s["total"], s["chain"]))
        total = money.money(sum(s["total"] for s in out))
        return {"k": len(combo), "stops": out, "total": total, "covered": len(priced) - len(missing), "missing": missing}

    plans = []
    manual = None
    if chosen:
        combo = tuple(dict.fromkeys([*pinned_chains, *chosen]))
        p = evaluate(combo, allow_empty=True)
        plans = [p]
        miss = []
        for i in p["missing"]:
            it = next(x for x in priced if x["id"] == i)
            low = min(offers[i].values(), key=lambda o: (o["price"], o["chain"]))
            miss.append({"id": i, "text": it["text"], "cheapest": {"chain": low["chain"], "price": low["price"]}})
        manual = {"chains": list(combo), "missing": miss}
    else:
        others = [c for c in cand if c not in pinned_chains]
        for k in range(max(1, len(pinned_chains)), max(max_stops, len(pinned_chains)) + 1):
            extra = k - len(pinned_chains)
            if extra > len(others):
                break
            got = [p for p in (evaluate((*pinned_chains, *c)) for c in combinations(others, extra)) if p]
            if not got:
                break
            plans.append(min(got, key=lambda p: (-p["covered"], p["total"], [s["chain"] for s in p["stops"]])))

    rec_i = 0 if plans else None
    if not chosen:
        for i in range(1, len(plans)):
            prev, cur = plans[i - 1], plans[i]
            if cur["covered"] > prev["covered"] or (cur["covered"] == prev["covered"] and prev["total"] - cur["total"] >= cost):
                rec_i = i
            else:
                break
    rec_total = plans[rec_i]["total"] if rec_i is not None else None
    alts, alt_best, alt_labels = _size_alts(priced, hidden) if plans else ({}, {}, {})
    by_id = {it["id"]: it for it in priced}
    for i, p in enumerate(plans):
        p["saving_vs_single"] = money.money(plans[0]["total"] - p["total"])
        p["saving_vs_prev"] = money.money(plans[i - 1]["total"] - p["total"]) if i else 0.0
        p["extra_vs_best"] = money.money(p["total"] - rec_total)
        p["recommended"] = i == rec_i
        chainset = {s["chain"] for s in p["stops"]}
        vs_market, size_saving = 0.0, 0.0
        for s in p["stops"]:
            for line in s["items"]:
                it = by_id[line["id"]]
                prices = [o["price"] for o in offers[it["id"]].values()]
                if len(prices) >= 2:
                    vs_market += (median(prices) - line["price"]) * it["mult"]
                tip = _size_tip(it, line, chainset, alts, alt_best, alt_labels)
                if tip:
                    line["better_size"] = tip
                    size_saving += tip["saving"]
        p["saving_vs_market"] = money.money(max(0.0, vs_market))
        p["size_saving"] = money.money(size_saving)

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
    rec_plan = plans[rec_i] if rec_i is not None else None
    return {"region": region, "date": date.today().isoformat(), "stop_cost": cost, "max_stops": max_stops,
            "headline": headline(rec_plan, plans[0] if plans and not chosen else None, len(priced), manual=bool(chosen)),
            "unpriced_info": [_unpriced(it) for it in items if it["id"] in unpriced],
            "empty": _empty(items, priced),
            "plans": plans, "single": single, "recommended_k": rec_plan["k"] if rec_plan else None,
            "one_store": single[0] if single else None,
            "priced": len(priced), "unpriced": unpriced,
            "sources": attribution,
            "source_info": {"kinds": srcs, "resmi_date": max(resmi_days) if resmi_days else None,
                            "national": any(x["source"] == "resmi" and x.get("region") == "ulusal" for x in used)},
            "hidden": hidden.out(), "manual": manual,
            "pinned": [{"id": i, "text": next(it["text"] for it in items if it["id"] == i), "chain": ch}
                       for i, ch in pins.items()],
            "chain_options": [{"chain": c["chain"], "covered": c["covered"]} for c in ranked]}


def headline(p: dict | None, one: dict | None, priced: int, manual: bool = False) -> dict | None:
    """Önerilen planın tek cümlesi (shopping_headline): {title, subtitle, cta, chains, total, saving, count, profit}.
    k=1: "Önerimiz: Hepsini BİM'den al — ₺195" / "6 ürün" / "BİM'e git"; k>1: "Önerimiz: BİM + Migros — ₺1.200" /
    "₺340 daha ucuz" (tek mağazaya göre) / "BİM + Migros". manual (kişi marketleri seçti): "Seçtiğin marketler: Migros
    + BİM — ₺1.250". profit (shop_best_value): {saving (ortalama fiyata göre), text "Bu planla ₺120 tasarruf", basis,
    size_saving, size_text} | null. Fiyatlı satır yoksa None."""
    from app import money
    from app.textnorm import suffix

    if not p:
        return None
    chains = [st["chain"] for st in p["stops"]]
    total, count = p["total"], p["covered"]
    saving = money.money(one["total"] - total) if one and one["covered"] == count and p["k"] > 1 else 0.0
    if manual:
        title = f"Seçtiğin marketler: {' + '.join(chains)} — {money.tl(total, trim=True)}"
        cta = " + ".join(chains) if p["k"] > 1 else f"{suffix(chains[0], 'e')} git"
    elif p["k"] == 1:
        head = "Hepsini " if count == priced else ""
        title = f"Önerimiz: {head}{suffix(chains[0], 'den')} al — {money.tl(total, trim=True)}"
        cta = f"{suffix(chains[0], 'e')} git"
    else:
        title = f"Önerimiz: {' + '.join(chains)} — {money.tl(total, trim=True)}"
        cta = " + ".join(chains)
    sub = f"{money.tl(saving, trim=True)} daha ucuz" if saving > 0 else f"{count} ürün"
    profit = None
    vm, ss = p.get("saving_vs_market") or 0.0, p.get("size_saving") or 0.0
    if vm >= 1 or ss >= 1:
        profit = {"saving": vm, "text": f"Bu planla {money.tl(vm, trim=True)} tasarruf" if vm >= 1 else None,
                  "basis": "Ürün başına mağazaların orta fiyatına göre" if vm >= 1 else None,
                  "size_saving": ss,
                  "size_text": f"Boy önerileriyle {money.tl(ss, trim=True)} daha" if ss >= 1 else None}
    return {"title": title, "subtitle": sub, "cta": cta, "chains": chains, "total": total, "saving": saving,
            "count": count, "profit": profit}


_HINTS = {
    "no_group": "Ürünü tanıyamadık; daha genel yaz: “süt”, “ekmek”.",
    "pending": "Ürün tanınıyor; fiyatı birazdan gelir.",
    "no_size": "Ürüne boy ekle: “süt 1 L”.",
    "no_price": "Bu ürünün fiyatı henüz yok; fiş okuttukça fiyatlar gelir.",
    "unit_mismatch": "Bu ürün bu birimle satılmıyor; miktarı adet olarak seç.",
    "store_no_price": "Seçtiğin mağazada bu ürünün fiyatı yok; mağaza seçimini kaldırabilir ya da değiştirebilirsin.",
    "brand_no_price": "Bu markanın fiyatı yok; “Marka fark etmez” seçersen fiyat gelir.",
}


def _unpriced(it: dict) -> dict:
    g = it.get("group")
    reason = "no_group" if not g else "pending" if g.get("pending") else "unit_mismatch" if it.get("unit_mismatch") \
        else "no_size" if "|" not in g["key"] and (it.get("unit") or "adet") == "adet" \
        else "store_no_price" if it.get("store") else "brand_no_price" if it.get("brand") else "no_price"
    hint = _HINTS[reason]
    if reason == "no_size":  # satırın kendi adıyla örnek: "Deterjan" → "deterjan 1 L" ya da "deterjan 500 g"
        t = it["text"].strip().lower()
        hint = f"Ürüne boy ekle: “{t} 1 L” ya da “{t} 500 g”."
    elif reason == "store_no_price":
        hint = f"{it['store']}: bu ürünün fiyatı yok; mağaza seçimini kaldırabilir ya da değiştirebilirsin."
    elif reason == "brand_no_price":  # "Çaykur Turist 1 kg için fiyat yok" (seri ve boy da)
        from app import product_groups

        name = " ".join(x for x in (it.get("brand_label") or it["brand"], product_groups.size_label(g["key"])) if x)
        hint = f"{name} için fiyat yok; “Marka fark etmez” seçersen fiyat gelir."
    return {"id": it["id"], "text": it["text"], "reason": reason, "hint": hint}


def _empty(items: list[dict], priced: list[dict]) -> dict | None:
    """Fiyatlı satır yoksa ne yapmalı (liste boşsa None: istemcinin kendi boş durumu)."""
    if not items or priced:
        return None
    no_size = any(it.get("group") and "|" not in it["group"]["key"] and not it["group"].get("pending") for it in items)
    text = ("Ürüne boy ekle: \u201csüt 1 L\u201d. Fiş okuttukça fiyatlar da gelir." if no_size else
            "Fiş okuttukça fiyatlar gelir; Bölgem'i seçersen market fiyatlarını da görürsün.")
    return {"title": "Henüz fiyat yok", "text": text}
