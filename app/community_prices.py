"""Topluluk fiyatları (anonim): bilinen zincirlerde (app/receipt_templates.py CHAINS) ürün grubunun (app/product_groups.py)
kaç liraya satıldığı, en az MIN_CONTRIBUTORS farklı kişinin fişinden.

Ne yazılır (fiş kaydedilince, kişinin "fiyat paylaşımı" tercihi açıksa — varsayılan AÇIK, app/prefs.py price_share):
    community_prices(chain, group_key, name_norm, unit_price, day, contributor)
  - yalnız fotoğraf/PDF'ten okunmuş fişin kalemleri (elle girilen kayıt değil), yalnız bilinen zincirde;
  - şube, adres, kart, tutarın tamamı, kayıt id'si, kullanıcı id'si YOK;
  - contributor = HMAC(günün tuzu, kullanıcı id) — tuz her gün yenilenir, eskisi silinir: aynı kişinin farklı
    günlerdeki satırları birbirine, hiçbir satır bir kullanıcıya bağlanamaz (sunucuyu işleten dahil). Yalnız aynı gün
    aynı kişinin satırları tek kişi sayılır (n şişmesin).
Ne okunur: yalnız toplam — (zincir, grup) için son WINDOW_DAYS günde en az MIN_CONTRIBUTORS farklı katkı varsa
ortanca fiyat ve kişi sayısı (median, n). Önce kişi başı ortanca, sonra kişiler arası ortanca (bir kişinin çok satırı
sonucu çekmesin). Ham satır, şube ya da kişi başı tarih hiçbir uçtan dönmez.
Hesap silme: satırlar kişiye bağlanamadığı için silinemez ve kalır (anonim istatistik); kişinin tercihi silinir.
Saklama KEEP_DAYS gün (app/jobs.py run_global → prune). Şema app/system.py SCHEMA'da.
"""
import hashlib
import hmac
import json
import secrets
from datetime import date, timedelta
from statistics import median

from app import system

MIN_CONTRIBUTORS = 3
WINDOW_DAYS = 14
KEEP_DAYS = 90
SALT_KEY = "community_salt"
_MIN_PRICE, _MAX_PRICE = 0.5, 100_000.0


def _salt(today: date) -> bytes:
    """Günün tuzu: gün değişince yenisi üretilir, eskisi üzerine yazılır (geri getirilemez)."""
    day = today.isoformat()
    try:
        cur = json.loads(system.get(SALT_KEY) or "{}")
    except ValueError:
        cur = {}
    if cur.get("day") != day or not cur.get("salt"):
        cur = {"day": day, "salt": secrets.token_hex(32)}
        system.put(SALT_KEY, json.dumps(cur))
    return bytes.fromhex(cur["salt"])


def contributor(uid: int, today: date | None = None) -> str:
    return hmac.new(_salt(today or date.today()), str(uid).encode(), hashlib.sha256).hexdigest()[:24]


def contribute(uid: int | None, merchant: str | None, legal_name: str | None, items: list[dict], purchased_at: str,
               today: date | None = None) -> int:
    """Kaydedilen fişin kalemlerinden topluluk satırları (yazılan satır sayısı). Kişi tercihi kapalıysa, işyeri bilinen
    bir zincir değilse, demo / kullanıcısız istekte 0. Kalem: fiyatı okunmuş ürün satırı (indirim, hizmet, grup satırı,
    işyeri adı olan satır değil), birim fiyat = tutar ÷ adet."""
    from app import db, demo_mode, prefs, product_groups
    from app.receipt_templates import chain_of

    today = today or date.today()
    if not uid or demo_mode.enabled() or not prefs.price_share(uid):
        return 0
    chain = chain_of(merchant, legal_name)
    if not chain:
        return 0
    try:
        day = min(date.fromisoformat(str(purchased_at)[:10]), today)
    except ValueError:
        return 0
    if (today - day).days > KEEP_DAYS:
        return 0
    rows = []
    for it in items:
        name, amt = (it.get("name") or "").strip(), it.get("amount")
        qty = it.get("qty") or 1
        if not name or not isinstance(amt, (int, float)) or isinstance(amt, bool) or amt <= 0 or qty <= 0:
            continue
        if db.item_kind(name, it.get("product"), amt) != "product" or db.same_as_merchant(name, merchant, legal_name):
            continue
        up = round(amt / qty, 2)
        if _MIN_PRICE <= up <= _MAX_PRICE:
            rows.append((it, up))
    if not rows:
        return 0
    groups = product_groups.groups_for([it for it, _ in rows])
    who = contributor(uid, today)
    out = [(chain, g["key"], product_groups.name_norm(it.get("product") or it["name"]), up, day.isoformat(), who)
           for (it, up), g in zip(rows, groups) if g]
    with system.connect() as c:
        c.executemany("INSERT INTO community_prices (chain, group_key, name_norm, unit_price, day, contributor) "
                      "VALUES (?, ?, ?, ?, ?, ?)", out)
    if any(g and g.get("pending") for g in groups):
        product_groups.kick()
    return len(out)


def relabel(c, nn: str, group: dict | None) -> None:
    """Yapay zekâ bir adın grubunu öğrenince (product_groups.classify_pending, aynı bağlantı): geçici gruptaki satırlar
    doğru gruba geçer; ürün değilse silinir. Tartılı (|kg) satırın birimi korunur."""
    if not group:
        c.execute("DELETE FROM community_prices WHERE name_norm = ?", (nn,))
        return
    base = group["key"].split("|")[0]
    c.execute("UPDATE community_prices SET group_key = CASE WHEN group_key LIKE '%|kg' THEN ? ELSE ? END "
              "WHERE name_norm = ?", (f"{base}|kg", group["key"], nn))


def medians(group_keys: list[str] | None = None, chain: str | None = None, today: date | None = None,
            min_contributors: int = MIN_CONTRIBUTORS) -> dict[tuple[str, str], dict]:
    """(zincir, grup) → {median, n}: son WINDOW_DAYS gün, en az min_contributors farklı katkı. Önce kişi başı ortanca,
    sonra kişiler arası ortanca. group_keys / chain verilirse yalnız onlar."""
    since = ((today or date.today()) - timedelta(days=WINDOW_DAYS)).isoformat()
    sql, args = "SELECT chain, group_key, contributor, unit_price FROM community_prices WHERE day >= ?", [since]
    if group_keys is not None:
        keys = sorted({k for k in group_keys if k})
        if not keys:
            return {}
        sql += f" AND group_key IN ({','.join('?' * len(keys))})"
        args += keys
    if chain:
        sql += " AND chain = ?"
        args.append(chain)
    with system.connect() as c:
        got = c.execute(sql, args).fetchall()
    per: dict[tuple[str, str], dict[str, list[float]]] = {}
    for ch, g, who, p in got:
        per.setdefault((ch, g), {}).setdefault(who, []).append(p)
    out = {}
    for k, people in per.items():
        if len(people) >= min_contributors:
            out[k] = {"median": round(median(median(v) for v in people.values()), 2), "n": len(people)}
    return out


def median_for(chain: str | None, group_key: str | None, today: date | None = None) -> dict | None:
    if not chain or not group_key:
        return None
    return medians([group_key], chain, today).get((chain, group_key))


def groups_like(q: str, today: date | None = None) -> list[str]:
    """Topluluk verisi olan (eşik geçmiş) grupların anahtarları; q: grup anahtarı ("sut|1l": yalnız o) ya da anahtarda
    geçen kelime (Türkçe harfsiz)."""
    from app import product_groups

    keys = sorted({g for _, g in medians(today=today)})
    if q and "|" in q:
        return [k for k in keys if k == q]
    if not q:
        return keys
    s, size = product_groups.query_parts(q)
    if not s:
        return [k for k in keys if size and k.endswith(f"|{size}")]
    return [k for k in keys if s in k.split("|")[0]]


def prune(today: date | None = None) -> int:
    cut = ((today or date.today()) - timedelta(days=KEEP_DAYS)).isoformat()
    with system.connect() as c:
        return c.execute("DELETE FROM community_prices WHERE day < ?", (cut,)).rowcount
