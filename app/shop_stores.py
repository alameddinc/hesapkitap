"""Mağaza süzgeci (shop_store_filter; Premium liste özelliği): kişi girmek istemediği zincirleri ("CarrefourSA",
"Hakmar") ve isterse bütün yerel marketleri (bilinen zincir olmayan, kendi fişindeki işyerleri) kapatır.

Tercih kişi başıdır (ortak defterde herkesin kendi süzgeci) ve sistem veritabanında tutulur: sys_settings
"shop_hidden:<kullanıcı id>" = {"chains": ["CarrefourSA", …], "local": true}. Giriş yoksa (tek kullanıcılı kurulum,
testler) defterin ayarına ("shop_hidden:owner") yazılır. Okuma tek anahtar okumasıdır; bağlantı açılışında iş yok.

Kapatılan mağaza "En ucuz"da, boy listesinde, Nereye gideyim? planlarında, cheaper_elsewhere'de, Nereden alınır'da ve
Fiyatlar "daha ucuz" önerisinde hiç görünmez (price_sources.best_offers hidden=). Piyasa ortancası (tasarruf ölçüsü,
fiş piyasa notu) süzülmez: o bir ölçüdür, öneri değil. Kişinin bir satırı açıkça bir mağazaya bağlaması (store) süzgeçten
önce gelir.
"""
import json

MAX_CHAINS = 40
NAME_MAX = 60


def _key(uid: int | None) -> str:
    return f"shop_hidden:{uid}" if uid else "shop_hidden:owner"


def _uid() -> int | None:
    from app import db

    return (db.current_user() or {}).get("id")


def get(uid: int | None = None) -> dict:
    """{chains: [görünen zincir adı], local: bool}."""
    from app import db, system

    uid = uid if uid is not None else _uid()
    raw = system.get(_key(uid)) if uid else db.get_setting(_key(None))
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        d = {}
    chains = [c for c in d.get("chains") or [] if isinstance(c, str) and c.strip()] if isinstance(d, dict) else []
    return {"chains": chains[:MAX_CHAINS], "local": bool(isinstance(d, dict) and d.get("local"))}


def put(data: dict, uid: int | None = None) -> dict:
    """data {chains?: [ad], local?: bool} (verilmeyen alan korunur). Adlar görünen zincir adına çevrilir, tekrarlar
    atılır; en çok MAX_CHAINS. Hiçbir şey kapalı değilse kayıt silinir."""
    from app import db, price_sources, system

    uid = uid if uid is not None else _uid()
    cur = get(uid)
    chains = cur["chains"]
    if data.get("chains") is not None:
        if not isinstance(data["chains"], list):
            raise ValueError("Mağaza listesi geçersiz")
        seen, chains = set(), []
        for c in data["chains"]:
            if not isinstance(c, str):
                raise ValueError("Mağaza adı geçersiz")
            name = price_sources.chain_name(c.strip()[:NAME_MAX])
            k = price_sources._chain_key(name)
            if name and k not in seen:
                seen.add(k)
                chains.append(name)
        if len(chains) > MAX_CHAINS:
            raise ValueError(f"En çok {MAX_CHAINS} mağaza kapatılabilir")
    local = bool(data["local"]) if data.get("local") is not None else cur["local"]
    val = json.dumps({"chains": chains, "local": local}, ensure_ascii=False) if chains or local else None
    if uid:
        system.put(_key(uid), val)
    else:
        db.set_setting(_key(None), val)
    return {"chains": chains, "local": local}


def is_local(chain: str | None) -> bool:
    """Bilinen market zinciri değil (kendi fişindeki mahalle marketi, manav): receipt_templates.chain_of tanımıyor;
    reklam filmi hesabında uydurma zincirler ("Market A") ve canlı demodaki kurgu zincirler zincir sayılır."""
    from app import film_scope
    from app.receipt_templates import chain_of

    return not (chain_of(chain) or film_scope.market_chain(chain))


class Hidden:
    """İstek başına süzgeç: hides(zincir) — kapatılan zincir ya da (local) yerel market mi. Gizlenen her görünen ad
    seen'e, grup başına hangi zincirlerin gizlendiği by_key'e yazılır ("3 mağaza gizli" ipucu)."""

    def __init__(self, chains: list[str], local: bool):
        from app import price_sources

        self.chains = list(chains)
        self.keys = {price_sources._chain_key(c) for c in chains}
        self.local = local
        self.seen: set[str] = set()
        self.by_key: dict[str, set[str]] = {}
        self._memo: dict[str, tuple[bool, str]] = {}

    def __bool__(self) -> bool:
        return bool(self.keys or self.local)

    def hides(self, chain: str | None, group_key: str | None = None) -> bool:
        from app import price_sources

        raw = chain or ""
        if raw not in self._memo:
            name = price_sources.chain_name(raw)
            self._memo[raw] = (price_sources._chain_key(name) in self.keys or (self.local and is_local(name)), name)
        hit, name = self._memo[raw]
        if hit:
            self.seen.add(name)
            if group_key:
                self.by_key.setdefault(group_key, set()).add(name)
        return hit

    def out(self, keys: list[str] | None = None) -> dict:
        """{count, chains (gizlenen görünen adlar), local} — istemci "N mağaza gizli · Değiştir" yazar."""
        if keys is None:
            got = set(self.seen)
        else:
            got = set().union(*(self.by_key.get(k, set()) for k in keys)) if keys else set()
        names = sorted(got, key=str.casefold)
        return {"count": len(names), "chains": names, "local": self.local}


def current(uid: int | None = None) -> Hidden:
    """İsteği yapan kişinin süzgeci (hiçbir şey kapalı değilse boş süzgeç: bool(h) False)."""
    p = get(uid)
    return Hidden(p["chains"], p["local"])


_TTL = 600.0
_cache: dict = {}


def _market_chains() -> list[str]:
    """Fiyat verisinde görünen zincirler (Market Fiyatı son 7 gün + topluluk son 7 gün; kişinin görebildiği dünya:
    film hesabı yalnız film satırları, resmî kaynak kapalıysa yalnız topluluk). 10 dk önbellek."""
    import time
    from datetime import date, timedelta

    from app import community_prices, film_scope, price_sources, system

    official = "resmi" in price_sources.enabled_names()
    film = film_scope.is_film_user()
    stamp = (official, film, price_sources._official_stamp() if official else None, system._path())
    hit = _cache.get("chains")
    now = time.monotonic()
    if hit and hit[0] == stamp and now - hit[1] < _TTL:
        return hit[2]
    since = (date.today() - timedelta(days=7)).isoformat()
    names: set[str] = set()
    with system.connect() as c:
        if official:
            names |= {r[0] for r in c.execute("SELECT DISTINCT chain FROM official_prices WHERE day >= ?", (since,))}
        cs = (date.today() - timedelta(days=community_prices.WINDOW_DAYS)).isoformat()
        names |= {r[0] for r in c.execute(f"SELECT DISTINCT chain FROM community_prices WHERE day >= ?"
                                          f"{film_scope.community_clause()}", (cs,))}
    out = sorted({price_sources.chain_name(n) for n in names if n}, key=str.casefold)
    _cache["chains"] = (stamp, now, out)
    return out


def stores() -> dict:
    """Mağazalar ekranı (GET /api/shopping-list/stores): {hidden {chains, local}, chains [{chain, hidden}] (bu kişiye
    görünebilecek her mağaza: Market Fiyatı zincirleri, topluluk satırlarındaki zincirler — bölgesel olanlar da —, kendi
    fişlerindeki bilinen zincirler; ada göre), local {hidden, names [kendi fişlerindeki yerel marketler, en çok 8],
    count}}. Süzgeç yalnız bu kişinin görünümünü değiştirir; topluluk verisi, katkılar, dışa aktarım etkilenmez."""
    from app import price_sources

    pref = get()
    hid = {price_sources._chain_key(c) for c in pref["chains"]}
    market = set(_market_chains())  # Market Fiyatı + topluluk (başkalarının fişleri): hepsi tek tek listelenir
    names = set(market)
    local: set[str] = set()
    own = next((s for s in price_sources.open_sources(["own"])), None)
    if own is not None:
        for offs in own._build().values():
            for o in offs.values():
                n = price_sources.chain_name(o["chain"])
                (local if is_local(n) else names).add(n)
    names |= set(pref["chains"])  # kapatılmış ama artık veride görünmeyen zincir de listede kalsın (açabilsin)
    seen, chains = set(), []
    for n in sorted(names, key=str.casefold):
        k = price_sources._chain_key(n)
        if n and k not in seen and (n in market or not is_local(n) or k in hid):
            seen.add(k)
            chains.append({"chain": n, "hidden": k in hid})
    loc = sorted(local, key=str.casefold)
    return {"hidden": pref, "chains": chains, "local": {"hidden": pref["local"], "names": loc[:8], "count": len(loc)}}
