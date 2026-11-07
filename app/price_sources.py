"""Fiyat kaynakları (eklenebilir): bir ürün grubu (app/product_groups.py) için "hangi zincirde kaça" teklifleri.

Arayüz (PriceSource):
    name                       kaynağın adı ("own", "community", …); teklifin source alanı
    fetch(group_keys) -> {group_key: [Offer, …]}   grupların teklifleri (toplu: istek başına bir sorgu)
    search(query) -> {group_key: label}            kaynağın bildiği, sorguyla eşleşen gruplar ("süt")
Offer: {chain, price (birim fiyat ₺), source, date? (kendi fişin), n? (kaç kişi), brand?, unit_price_per?, per_unit?}
resmi teklifte ayrıca: attribution, region ("İzmir" | "ulusal"), discount?, promotion?, change_7d?, change_30d? (yüzde)
unit_price_per / per_unit: grubun boyundan litre / kilo / adet fiyatı (product_groups.unit_price; "₺59,00/L").

Kayıtlı kaynaklar:
- "own": kullanıcının kendi defterindeki fişler (db.priced_items; ortak defter üyesinde yalnız görebildiği kayıtlar),
  zincir/mağaza başına son fiyat.
- "community": anonim topluluk ortancası (app/community_prices.py; son 14 gün, en az 3 kişi).
- "resmi": resmî kaynak (Market Fiyatı; app/price_import_bq.py BigQuery'den gecelik ya da app/price_import.py dosyadan
  official_prices'a aktarılır). VARSAYILAN KAPALI: yalnız HESAPKITAP_PRICE_SOURCES "resmi" içerir VE
  HESAPKITAP_BQ_CREDENTIALS (ya da HESAPKITAP_OFFICIAL_PRICES_URL / _FILE) ayarlıysa etkin. Teklifte attribution (kaynak
  yazısı) gelir; istemci gösterir. Zincir başına: kişinin ilinin son günü, gruptaki en ucuz ürün (OfficialSource).
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
    region: str
    discount: bool
    promotion: str
    change_7d: float
    change_30d: float


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
    raw = os.environ.get("HESAPKITAP_PRICE_SOURCES") or DEFAULT_SOURCES
    names = [n for n in (x.strip() for x in raw.split(",")) if n in _REGISTRY]
    return [n for n in names if n != "resmi" or official_configured()]  # resmî kaynak: BigQuery ya da adres / dosya da gerekir


def official_configured() -> bool:
    """Resmî kaynağın verisi var mı: BigQuery kimliği (app/bq.py) ya da dosya / adres (app/price_import.py)."""
    from app import bq, price_import

    return bool(price_import.configured()) or bq.configured()


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


NATIONAL_CHAINS = {"BİM", "A101", "ŞOK"}  # fiyatı ülke genelinde ~aynı: kişinin ilinde yoksa herhangi bir ilin son günü


@register
class OfficialSource:
    """Resmî kaynak (official_prices; app/price_import.py dosyadan, app/price_import_bq.py BigQuery'den): zincir başına
    son günün fiyatı — ürün başına şube ortancası, gruptaki en ucuz ürün (markası ile). Son WINDOW_DAYS gün.

    Bölge (kişinin "Bölgem" tercihi, prefs.region; seçilmemişse İstanbul): teklifler o ilin satırlarından. BİM, A101, ŞOK
    (NATIONAL_CHAINS) her zaman region "ulusal" yazılır: ilin satırı varsa o, yoksa herhangi bir ilin son günü.
    Migros, CarrefourSA, Hakmar, Tarım Kredi yalnız o ilden; yoksa yakın büyük şehir, o da yoksa İstanbul
    (provinces.fallback_chain) — region kullanılan il. Bölgesiz (dosyadan) satırlar en son çare, region yazılmaz.
    Teklif ek alanları: region, discount (yalnız true), promotion?, change_7d / change_30d (yüzde; varsa)."""
    name = "resmi"
    WINDOW_DAYS = 7

    def __init__(self, region: str | None = None):
        if region is None:
            from app import db, prefs

            region = prefs.region((db.current_user() or {}).get("id"))
        from app import provinces

        self.region = provinces.valid(region) or provinces.DEFAULT

    def _rows(self, group_keys: list[str] | None) -> list:
        from app import system

        since = (date.today() - timedelta(days=self.WINDOW_DAYS)).isoformat()
        sql = ("SELECT chain, group_key, title, brand, price, day, region, discount, promotion, change_7d, change_30d "
               "FROM official_prices WHERE day >= ?")
        args: list = [since]
        if group_keys is not None:
            if not group_keys:
                return []
            sql += f" AND group_key IN ({','.join('?' * len(group_keys))})"
            args += group_keys
        with system.connect() as c:
            return c.execute(sql, args).fetchall()

    def _pick(self, chain: str, by_prov: dict[str, list]) -> tuple[str | None, list] | None:
        """(etiket, satırlar): hangi ilin satırları kullanılacak."""
        from app import provinces

        if chain in NATIONAL_CHAINS:
            if self.region in by_prov:
                return "ulusal", by_prov[self.region]
            named = [p for p in by_prov if p]
            if named:  # en yeni günü olan il (eşitse İstanbul önce, sonra ada göre)
                p = max(named, key=lambda p: (max(r["day"] for r in by_prov[p]), p == provinces.DEFAULT, p))
                return "ulusal", by_prov[p]
        else:
            for p in provinces.fallback_chain(self.region):
                if p in by_prov:
                    return p, by_prov[p]
        return (None, by_prov[""]) if "" in by_prov else None

    def fetch(self, group_keys: list[str]) -> dict[str, list[Offer]]:
        from statistics import median

        from app import price_import, provinces

        grouped: dict[tuple[str, str], dict[str, list]] = {}
        for r in self._rows(sorted({k for k in group_keys if k})):
            prov = provinces.province_of(r["region"]) if r["region"] else ""
            if prov is not None:
                grouped.setdefault((r["chain"], r["group_key"]), {}).setdefault(prov, []).append(r)
        out: dict[str, list[Offer]] = {}
        for (ch, g), by_prov in grouped.items():
            got = self._pick(ch, by_prov)
            if not got:
                continue
            label, rows = got
            last = max(r["day"] for r in rows)
            prods: dict[tuple[str, str | None], list] = {}
            for r in rows:
                if r["day"] == last:
                    prods.setdefault((r["title"], r["brand"]), []).append(r)
            (t, b), price = min(((k, median(x["price"] for x in v)) for k, v in prods.items()),
                                key=lambda x: (x[1], x[0][0]))
            top = min(prods[(t, b)], key=lambda x: x["price"])
            off: Offer = {"chain": ch, "price": round(price, 2), "source": self.name, "date": last,
                          "attribution": price_import.ATTRIBUTION}
            if b:
                off["brand"] = b
            if label:
                off["region"] = label
            if any(x["discount"] for x in prods[(t, b)]):
                off["discount"] = True
            if top["promotion"]:
                off["promotion"] = top["promotion"]
            for k in ("change_7d", "change_30d"):
                if top[k] is not None:
                    off[k] = round(float(top[k]), 1)
            out.setdefault(g, []).append(off)
        return out

    def search(self, query: str) -> dict[str, str]:
        from app import product_groups

        keys = sorted({r["group_key"] for r in self._rows(None)})
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
