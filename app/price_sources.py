"""Fiyat kaynakları (eklenebilir): bir ürün grubu (app/product_groups.py) için "hangi zincirde kaça" teklifleri.

Arayüz (PriceSource):
    name                       kaynağın adı ("own", "community", …); teklifin source alanı
    fetch(group_keys) -> {group_key: [Offer, …]}   grupların teklifleri (toplu: istek başına bir sorgu)
    search(query) -> {group_key: label}            kaynağın bildiği, sorguyla eşleşen gruplar ("süt")
Offer: {chain, price (birim fiyat ₺), source, date? (kendi fişin), n? (kaç kişi), brand?, unit_price_per?, per_unit?}
unit_price_per / per_unit: grubun boyundan litre / kilo / adet fiyatı (product_groups.unit_price; "₺59,00/L").

Kayıtlı kaynaklar:
- "own": kullanıcının kendi defterindeki fişler (db.priced_items; ortak defter üyesinde yalnız görebildiği kayıtlar),
  zincir/mağaza başına son fiyat.
- "community": anonim topluluk ortancası (app/community_prices.py; son 14 gün, en az 3 kişi).
- "resmi": resmî kaynak (Market Fiyatı biçimi; app/price_import.py ile official_prices'a aktarılır). VARSAYILAN KAPALI:
  yalnız HESAPKITAP_PRICE_SOURCES "resmi" içerir VE HESAPKITAP_OFFICIAL_PRICES_URL / _FILE ayarlıysa etkin. Teklifte
  attribution (kaynak yazısı) gelir; istemci gösterir. Zincir başına: son günün şube ortancası, gruptaki en ucuz ürün.
Başka bir resmî kaynak ancak izin alındıktan sonra register() ile eklenir ve HESAPKITAP_PRICE_SOURCES'a adı yazılınca
etkin olur (varsayılan "own,community"). Hiçbir zincirin sitesi ya da izni olmayan bir servis kazınmaz; bu modül ağ
çağrısı yapmaz (içe aktarım ayrı komut).

Birleştirme (best_offers): zincir başına tek teklif — kendi fişin son OWN_FRESH_DAYS gün içindeyse o, değilse topluluk
ortancası, o da yoksa kendi eski fiyatın; ucuzdan pahalıya, en çok limit.
"""
import os
from datetime import date, timedelta
from typing import Protocol, TypedDict


class Offer(TypedDict, total=False):
    chain: str
    price: float
    source: str
    date: str
    n: int
    brand: str
    unit_price_per: float
    per_unit: str
    attribution: str


class PriceSource(Protocol):
    name: str

    def fetch(self, group_keys: list[str]) -> dict[str, list[Offer]]: ...

    def search(self, query: str) -> dict[str, str]: ...


OWN_FRESH_DAYS = 14
DEFAULT_SOURCES = "own,community"
_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    """Kaynak sınıfını adıyla kaydet (sınıf bağımsız oluşturulabilir olmalı: cls())."""
    _REGISTRY[cls.name] = cls
    return cls


def registered() -> list[str]:
    return list(_REGISTRY)


def enabled_names() -> list[str]:
    from app import price_import

    raw = os.environ.get("HESAPKITAP_PRICE_SOURCES") or DEFAULT_SOURCES
    names = [n for n in (x.strip() for x in raw.split(",")) if n in _REGISTRY]
    return [n for n in names if n != "resmi" or price_import.configured()]  # resmî kaynak: adres / dosya da gerekir


def open_sources(names: list[str] | None = None) -> list[PriceSource]:
    """İstek başına kaynak örnekleri (kendi verisi istek bağlamındaki deftere göre)."""
    return [_REGISTRY[n]() for n in (names or enabled_names()) if n in _REGISTRY]


def _match(q: str, key: str, label: str) -> bool:
    """Boş sorgu her şey; grup anahtarı ("sut|1l", istemci anahtar gönderir) yalnız o grup; metin ("süt") anahtarda ya
    da adda geçen."""
    from app import product_groups

    if not q:
        return True
    if "|" in q:
        return q == key
    s, size = product_groups.query_parts(q)
    if not s:  # yalnız boy ("1 L"): o boydaki gruplar
        return bool(size) and key.endswith(f"|{size}")
    return s in key.split("|")[0] or s in product_groups.slug(label.split(" · ")[0])


@register
class OwnSource:
    """Kullanıcının kendi fişleri: zincir (bilinen zincirse görünen adı, değilse fişteki işyeri) başına son fiyat."""
    name = "own"

    def __init__(self):
        self._index: dict[str, dict[str, Offer]] | None = None
        self._labels: dict[str, str] = {}

    def _build(self) -> dict[str, dict[str, Offer]]:
        if self._index is not None:
            return self._index
        from app import db
        from app.receipt_templates import chain_of

        items = [it for it in db.priced_items() if it["price_known"] == 1 and it["unit_price"] > 0]
        db.attach_groups(items)
        idx: dict[str, dict[str, Offer]] = {}
        for it in items:  # eski→yeni: son yazılan son fiyat
            g = it.get("group")
            if not g:
                continue
            chain = chain_of(it["merchant"], it.get("legal_name")) or (it["merchant"] or "?").strip()
            off: Offer = {"chain": chain, "price": it["unit_price"], "source": self.name, "date": it["purchased_at"]}
            if it.get("brand"):
                off["brand"] = it["brand"]
            idx.setdefault(g["key"], {})[db._store_key(chain)] = off
            self._labels[g["key"]] = g["label"]
        self._index = idx
        return idx

    def fetch(self, group_keys: list[str]) -> dict[str, list[Offer]]:
        idx = self._build()
        return {k: list(idx[k].values()) for k in group_keys if k in idx}

    def search(self, query: str) -> dict[str, str]:
        self._build()
        return {k: v for k, v in self._labels.items() if _match(query, k, v)}

    def last_dates(self) -> dict[str, str]:
        """Grup → son alım günü (sıralama için)."""
        return {k: max(o["date"] for o in offs.values()) for k, offs in self._build().items()}


@register
class CommunitySource:
    """Anonim topluluk ortancası (en az 3 kişi, son 14 gün)."""
    name = "community"

    def fetch(self, group_keys: list[str]) -> dict[str, list[Offer]]:
        from app import community_prices

        out: dict[str, list[Offer]] = {}
        for (chain, g), m in community_prices.medians(group_keys).items():
            out.setdefault(g, []).append({"chain": chain, "price": m["median"], "source": self.name, "n": m["n"]})
        return out

    def search(self, query: str) -> dict[str, str]:
        from app import community_prices, product_groups

        keys = community_prices.groups_like(query)
        labels = product_groups.labels(keys)
        return {k: labels.get(k, k) for k in keys if _match(query, k, labels.get(k, k))}


@register
class OfficialSource:
    """Resmî kaynak (official_prices; app/price_import.py): zincir başına son günün fiyatı — ürün başına şube ortancası,
    gruptaki en ucuz ürün (markası ile). Son WINDOW_DAYS gün."""
    name = "resmi"
    WINDOW_DAYS = 7

    def _rows(self, group_keys: list[str] | None) -> list:
        from app import system

        since = (date.today() - timedelta(days=self.WINDOW_DAYS)).isoformat()
        sql, args = "SELECT chain, group_key, title, brand, price, day FROM official_prices WHERE day >= ?", [since]
        if group_keys is not None:
            if not group_keys:
                return []
            sql += f" AND group_key IN ({','.join('?' * len(group_keys))})"
            args += group_keys
        with system.connect() as c:
            return c.execute(sql, args).fetchall()

    def fetch(self, group_keys: list[str]) -> dict[str, list[Offer]]:
        from statistics import median

        from app import price_import

        last: dict[tuple[str, str], str] = {}
        rows = self._rows(sorted({k for k in group_keys if k}))
        for ch, g, _t, _b, _p, day in rows:
            last[(ch, g)] = max(day, last.get((ch, g), day))
        per: dict[tuple[str, str], dict[tuple[str, str | None], list[float]]] = {}
        for ch, g, t, b, p, day in rows:
            if day == last[(ch, g)]:
                per.setdefault((ch, g), {}).setdefault((t, b), []).append(p)
        out: dict[str, list[Offer]] = {}
        for (ch, g), prods in per.items():
            (t, b), price = min(((k, median(v)) for k, v in prods.items()), key=lambda x: (x[1], x[0][0]))
            off: Offer = {"chain": ch, "price": round(price, 2), "source": self.name, "date": last[(ch, g)],
                          "attribution": price_import.ATTRIBUTION}
            if b:
                off["brand"] = b
            out.setdefault(g, []).append(off)
        return out

    def search(self, query: str) -> dict[str, str]:
        from app import product_groups

        keys = sorted({r[1] for r in self._rows(None)})
        labels = product_groups.labels(keys)
        return {k: labels.get(k, k) for k in keys if _match(query, k, labels.get(k, k))}


def _chain_key(chain: str) -> str:
    from app import db

    return db._store_key(chain)


def merge(offers: list[Offer], limit: int = 5, today: date | None = None) -> list[Offer]:
    """Zincir başına tek teklif (kendi taze fiyatın > topluluk > kendi eski fiyatın), ucuzdan pahalıya."""
    fresh = ((today or date.today()) - timedelta(days=OWN_FRESH_DAYS)).isoformat()

    def rank(o: Offer) -> int:
        if o["source"] == "own":
            return 0 if o.get("date", "") >= fresh else 2
        return 1 if o["source"] in ("community", "resmi") else 3

    best: dict[str, Offer] = {}
    for o in offers:
        k = _chain_key(o["chain"])
        if k not in best or (rank(o), o["price"]) < (rank(best[k]), best[k]["price"]):
            best[k] = o
    return sorted(best.values(), key=lambda o: (o["price"], o["chain"]))[:limit]


def best_offers(group_keys: list[str], sources: list[PriceSource] | None = None, limit: int = 5,
                today: date | None = None) -> dict[str, list[Offer]]:
    """Grup → birleştirilmiş en iyi teklifler (merge)."""
    keys = sorted({k for k in group_keys if k})
    if not keys:
        return {}
    pool: dict[str, list[Offer]] = {k: [] for k in keys}
    for src in sources if sources is not None else open_sources():
        for k, offs in src.fetch(keys).items():
            pool.setdefault(k, []).extend(offs)
    from app import product_groups

    return {k: [{**o, **product_groups.unit_price(o["price"], k)} for o in merge(v, limit, today)]
            for k, v in pool.items()}
