"""Alışveriş listesi: son seçilen markalar ve mağazalar (shop_free_pick) — "Zincir ve marka seç"te en üstte çipler.

Kişinin satıra elle verdiği marka / mağaza (PATCH /api/shopping-list/{id} {brand | store}; serbest metin: fiyat verisinde
olmayan "Örnek Bakkal" ya da "Örnekmarka" da olur) defter ayarında kişi başı saklanır (settings shop_recent:<kullanıcı>;
ortak defterde herkesin kendi listesi), yeni → eski, en çok MAX. Okurken listedeki satırların elle seçilmiş marka ve
mağazaları da eklenir (addan okunan marka — brand_auto — sayılmaz). Aynı ad (Türkçe harf / büyük-küçük farkı yok) bir kez.
"""
import json

from app import db

MAX = 8


def _key() -> str:
    u = db.current_user() or {}
    return f"shop_recent:{u.get('id') or 'owner'}"


def _load() -> dict[str, list[str]]:
    try:
        d = json.loads(db.get_setting(_key()) or "{}")
    except ValueError:
        d = {}
    return {k: [str(x) for x in d.get(k, []) if isinstance(x, str) and x.strip()][:MAX] for k in ("brands", "stores")}


def _merge(*lists: list[str]) -> list[str]:
    from app import search_rank

    out, seen = [], set()
    for xs in lists:
        for x in xs:
            f = search_rank.fold(x)
            if f and f not in seen:
                seen.add(f)
                out.append(x)
    return out[:MAX]


def record(brand: str | None = None, store: str | None = None) -> None:
    """Seçileni başa al (boş olan yazılmaz). Hata sessiz: satır kaydı bundan etkilenmez."""
    if not brand and not store:
        return
    d = _load()
    if brand:
        d["brands"] = _merge([brand], d["brands"])
    if store:
        d["stores"] = _merge([store], d["stores"])
    db.set_setting(_key(), json.dumps(d, ensure_ascii=False))


def get() -> dict[str, list[str]]:
    """{brands, stores}: son seçilenler, sonra listedeki satırların elle seçilmiş marka / mağazaları."""
    d = _load()
    with db.connect() as c:
        rows = c.execute("SELECT brand, brand_auto, store FROM shopping_list ORDER BY id DESC LIMIT 200").fetchall()
    return {"brands": _merge(d["brands"], [r[0] for r in rows if r[0] and not r[1]]),
            "stores": _merge(d["stores"], [r[2] for r in rows if r[2]])}
