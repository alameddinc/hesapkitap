"""Kişinin kendi aldığı ürünler (alışveriş listesi için): yazılan adı fişteki ürüne bağlama, yazarken öneri, kafe yeri.

Kaynak db.priced_items (ortak defter üyesinde yalnız görebildiği kayıtların fiş satırları); ürün = db.product_key (aynı
yazılış farklı fişlerde tek ürün). Kayıt: {key, name (görünen ad "Örneksan Slims Mavi Sigara"), tokens, words, group,
count, last_date, last_price, place, cafe {place, price, date} | null}.

- match(text): kuralın / önbelleğin / kataloğun tanımadığı liste satırı yapay zekâya gitmeden önce kişinin kendi
  ürünlerine bakılır: yazılan adın anlamlı kelimelerinin hepsi bir ürün adında geçiyorsa (en az iki kelime; tek kelimede
  yalnız tek ürün içeriyorsa) ya da kelime kümelerinin Jaccard benzerliği ≥ JACCARD_MIN ise o ürün (birden çoksa en
  yeni, sonra en çok alınan).
- cafe: alımı market zinciri olmayan bir yerde (Kafe & Restoran / Yemek Siparişi kaydı ya da Kafe kategorili ürün)
  yapılmışsa son kafe alımı; liste satırında own_place ("Senin son alımın: Örnekkahve ₺120") olarak görünür, market
  eşleşmesi değişmez.
- suggest(q): kelime öneki eşleşen ürünler, çok alınan önce, sonra en yeni.

Önbellek: defter dosyası + kullanıcı + ortak defter görünümü başına, damga (fiş satırı / kayıt sayısı, gizli kayıt
sayısı, ürün grubu önbelleği kuşağı) değişene ya da CACHE_TTL saniye dolana kadar: öneri her tuşta fişleri baştan
okumaz. Yalnız okuma; kişinin kaydı hiçbir zaman değişmez.
"""
import os
import threading
import time

from app import db

CACHE_TTL = 120.0
CACHE_MAX = 64
JACCARD_MIN = 0.75
SUGGEST_MAX = 4
NON_MARKET_CATEGORIES = {"Kafe & Restoran", "Yemek Siparişi"}

_cache: dict = {}
_lock = threading.Lock()


def _tokens(name: str) -> frozenset[str]:
    """Eşleşme kelimeleri (boy, rakam, tanımlayıcı, tek harf atılır; marka kalır): katalog eşleşmesiyle aynı."""
    from app import product_catalog

    return frozenset(product_catalog._tokens(name or ""))


def _words(name: str) -> tuple[str, ...]:
    from app import product_groups

    return tuple(product_groups.name_norm(name).split())


def display(name: str) -> str:
    """"ÖRNEKSAN SLIMS MAVİ SİGARA" → "Örneksan Slims Mavi Sigara"; küçük harf içeren yazılış olduğu gibi kalır. Türkçe
    harfli yazılışta I → ı ("KIRMIZI" → "Kırmızı"); Türkçe harfsiz (ASCII) yazılışta I → i ("SIGARA" → "Sigara")."""
    n = " ".join((name or "").split())
    if not n.isupper():
        return n
    tr = any(ch in n for ch in "İŞĞÜÖÇ")
    low = lambda w: (w.replace("I", "ı") if tr else w).replace("İ", "i").lower()  # noqa: E731
    return " ".join(w[0] + low(w[1:]) if w[0].isalpha() else low(w) for w in n.split())


def _place(it: dict) -> str:
    from app.receipt_templates import chain_of

    return chain_of(it.get("merchant"), it.get("legal_name")) or (it.get("merchant") or "?").strip()


def _is_cafe(it: dict) -> bool:
    from app import price_advice

    if price_advice._market_chain(it.get("merchant"), it.get("legal_name")):
        return False
    return it.get("category") in NON_MARKET_CATEGORIES or (it.get("group") or {}).get("category") == "Kafe"


def _stamp() -> tuple:
    from app import product_groups

    with db.connect() as c:
        r = c.execute("SELECT (SELECT COUNT(*) FROM receipt_items), (SELECT IFNULL(MAX(id), 0) FROM receipt_items), "
                      "(SELECT COUNT(*) FROM entries), (SELECT IFNULL(MAX(id), 0) FROM entries), "
                      "(SELECT IFNULL(SUM(hidden), 0) FROM entries)").fetchone()
    return (*tuple(r), product_groups.GENERATION)


def _build() -> list[dict]:
    items = db.priced_items()
    db.attach_groups(items, learn=False)
    by: dict[str, list[dict]] = {}
    for it in items:  # eski → yeni
        by.setdefault(it["product_key"], []).append(it)
    out = []
    for key, xs in by.items():
        last = xs[-1]
        priced = [x for x in xs if x["price_known"] == 1 and x["unit_price"] > 0]
        raw = next((x["product"] for x in reversed(xs) if x.get("product")), None) or last["name"]
        group = next((x["group"] for x in reversed(xs) if x.get("group")), None)
        cafe = next(({"place": _place(x), "price": x["unit_price"], "date": x["purchased_at"]}
                     for x in reversed(priced) if _is_cafe(x)), None)
        tokens = _tokens(raw) | frozenset(t for x in xs for t in _tokens(x["name"]))
        if not tokens:
            continue
        out.append({"key": key, "name": display(raw), "tokens": tokens,
                    "words": frozenset(w for x in xs for w in (*_words(x["name"]), *_words(raw))),
                    "group": group, "count": len(xs), "last_date": last["purchased_at"],
                    "last_price": priced[-1]["unit_price"] if priced else None,
                    "place": _place(priced[-1]) if priced else _place(last),
                    "price_date": priced[-1]["purchased_at"] if priced else None, "cafe": cafe})
    return out


def products() -> list[dict]:
    """Kişinin (görebildiği) ürünleri, önbellekten."""
    from app import book

    u = db.current_user() or {}
    ck = (os.path.abspath(db.db_path()), u.get("id"), bool(book.member()))
    st = _stamp()
    hit = _cache.get(ck)
    if hit and hit[0] == st and time.monotonic() - hit[1] < CACHE_TTL:
        return hit[2]
    data = _build()
    with _lock:
        if len(_cache) >= CACHE_MAX:
            _cache.clear()
        _cache[ck] = (st, time.monotonic(), data)
    return data


def reset() -> None:
    _cache.clear()


def _rank(p: dict) -> tuple:
    return (p["last_date"], p["count"])


def match(text: str, prods: list[dict] | None = None) -> dict | None:
    """Yazılan ad → kişinin ürünü ya da None (kurallar yukarıda)."""
    q = _tokens(text)
    if not q:
        return None
    prods = products() if prods is None else prods
    inside = [p for p in prods if q <= p["tokens"]]
    if inside and (len(q) >= 2 or len(inside) == 1):
        return max(inside, key=_rank)
    close = [(len(q & p["tokens"]) / len(q | p["tokens"]), p) for p in prods if q & p["tokens"]]
    close = [(j, p) for j, p in close if j >= JACCARD_MIN]
    return max(close, key=lambda x: (x[0], _rank(x[1])))[1] if close else None


def cafe_for(base_keys: list[str], prods: list[dict] | None = None) -> dict[str, dict]:
    """Ana grup ("latte") → kişinin o gruptaki son kafe alımı {place, price, date}."""
    want = {b for b in base_keys if b}
    out: dict[str, dict] = {}
    if not want:
        return out
    for p in products() if prods is None else prods:
        b = ((p.get("group") or {}).get("key") or "").split("|", 1)[0]
        if b in want and p["cafe"] and (b not in out or p["cafe"]["date"] > out[b]["date"]):
            out[b] = p["cafe"]
    return out


def suggest(q: str, limit: int = SUGGEST_MAX) -> list[dict]:
    """Kelime öneki eşleşen ürünler: [{text, group_key, label, source "product", last_price, place, count}]."""
    from app import money

    words = _words(q)
    if not words or len("".join(words)) < 2:
        return []
    hits = [p for p in products() if all(any(t.startswith(w) for t in p["words"]) for w in words)]
    out = []
    for p in sorted(hits, key=lambda p: (p["count"], p["last_date"]), reverse=True)[:limit]:
        k = (p.get("group") or {}).get("key")
        price = p["last_price"]
        out.append({"text": p["name"], "group_key": k if k and "|" in k else None,
                    "label": f"{p['name']} · son {money.tl(price, trim=True)}" if price else p["name"],
                    "source": "product", "last_price": price, "place": p["place"], "count": p["count"]})
    return out


def own_product(p: dict | None) -> dict | None:
    """Liste satırının bağlandığı ürün (istemci: "Senin son alımın: ₺95 · Örnekbüfe")."""
    if not p:
        return None
    return {"name": p["name"], "price": p["last_price"], "place": p["place"], "date": p["price_date"]}
