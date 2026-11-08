"""Topluluk fiyatları (anonim): bilinen zincirlerde (app/receipt_templates.py CHAINS) ürün grubunun (app/product_groups.py)
kaç liraya satıldığı, en az MIN_CONTRIBUTORS farklı kişinin fişinden.

Ne yazılır (fiş kaydedilince, kişinin "fiyat paylaşımı" tercihi açıksa — varsayılan AÇIK, app/prefs.py price_share):
    community_prices(chain, group_key, name_norm, unit_price, day, contributor, region)
  - yalnız fotoğraf/PDF'ten okunmuş fişin kalemleri (elle girilen kayıt değil), yalnız bilinen zincirde;
  - şube, adres, kart, tutarın tamamı, kayıt id'si, kullanıcı id'si YOK; region yalnız fişin şube yazısında il adı
    geçiyorsa o il (provinces.find_in; "Kadıköy Moda" → NULL) — kişinin konumu / Bölgem'i kullanılmaz;
  - contributor = HMAC(günün tuzu, kullanıcı id) — tuz her gün yenilenir, eskisi silinir: aynı kişinin farklı
    günlerdeki satırları birbirine, hiçbir satır bir kullanıcıya bağlanamaz (sunucuyu işleten dahil). Yalnız aynı gün
    aynı kişinin satırları tek kişi sayılır (n şişmesin).
Yalnız sağlam satır yazılır: fişin kalemleri toplamı tutmuyorsa (receipt_check.check ok değil: karışan rakam önerisi,
şüpheli satır, adet × birim tutmayan kalem onaylanmadan) fişin hiçbir satırı yazılmaz. İndirim uygulanmış kalem
(kalemin discount alanı ya da hemen altındaki indirim satırı) net fiyatıyla discounted = 1 yazılır: normal fiyat
ortancasına girmez, ayrı "indirimli ₺X" ipucu olur.
Ne okunur: yalnız toplam — (zincir, grup[, il]) için son WINDOW_DAYS (7) günde en az MIN_CONTRIBUTORS farklı katkı
varsa indirimsiz satırların ortanca fiyatı ve kişi sayısı (median, n; indirimli satırların ortancası discounted, yine en
az MIN_CONTRIBUTORS kişiyle). Önce kişi başı ortanca, sonra kişiler arası ortanca (bir kişinin çok satırı sonucu
çekmesin). Ham satır, şube ya da kişi başı tarih hiçbir uçtan dönmez. Kişinin kendi kaydı piyasa verisiyle hiçbir zaman
değişmez (yalnız kişinin onayladığı öneriler).
Hesap silme: satırlar kişiye bağlanamadığı için silinemez ve kalır (anonim istatistik); kişinin tercihi silinir.
Saklama KEEP_DAYS gün (app/jobs.py run_global → prune). Şema app/system.py SCHEMA'da. Gecelik BigQuery dışa aktarımı
(yalnız gün + zincir + grup + ad başına ortanca ve sayılar): app/community_export.py.
"""
import hashlib
import hmac
import json
import secrets
from datetime import date, timedelta
from statistics import median

from app import system

MIN_CONTRIBUTORS = 3
WINDOW_DAYS = 7
DISAGREE_PCT = 10.0   # topluluk ile Market Fiyatı arasındaki fark (yüzde) bu kadarı aşarsa ikisi de gösterilir
DISAGREE_DAYS = 3     # son 7 günün en az bu kadarında ayrışan zincir × ürün yönetici paneline
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


def sound(items: list[dict], receipt_total: float | None) -> bool:
    """Fişin kalemleri topluluğa yazılacak kadar sağlam mı: adet × birim tutuyor ve (toplam biliniyorsa) kalemler
    toplamı tutuyor (receipt_check.check ok). Tutmuyorsa karışan rakam / şüpheli satır / kayma olabilir: hiçbiri yazılmaz."""
    from app import db, receipt_check

    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        a = it.get("amount")
        if isinstance(a, (int, float)) and not isinstance(a, bool) and a > 0 and \
                db.item_kind(it.get("name") or "", it.get("product"), a) == "discount":
            it = {**it, "amount": -a}  # indirim satırı toplamdan düşer (kayıtta da eksi saklanır)
        rows.append(it)
    chk = receipt_check.check(rows, receipt_total)
    return bool(chk.get("ok"))


def _discounts(items: list[dict]) -> dict[int, float]:
    """Kalem sırası → uygulanan indirim (₺, pozitif): kalemin discount alanı (sayı ya da true) ya da hemen altındaki
    indirim satırı (eksi tutar / "İNDİRİM")."""
    from app import db

    out: dict[int, float] = {}
    for i, it in enumerate(items):
        d = it.get("discount")
        if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0:
            out[i] = float(d)
        elif d is True:
            out[i] = 0.0
        nxt = items[i + 1] if i + 1 < len(items) else None
        if nxt and isinstance(nxt.get("amount"), (int, float)) and \
                db.item_kind(nxt.get("name") or "", nxt.get("product"), nxt["amount"]) == "discount" and \
                db.item_kind(it.get("name") or "", it.get("product"), it.get("amount")) == "product":
            out[i] = out.get(i, 0.0) + abs(float(nxt["amount"]))
    return out


def contribute(uid: int | None, merchant: str | None, legal_name: str | None, items: list[dict], purchased_at: str,
               today: date | None = None, branch: str | None = None, receipt_total: float | None = None) -> int:
    """Kaydedilen fişin kalemlerinden topluluk satırları (yazılan satır sayısı). Kişi tercihi kapalıysa, işyeri bilinen
    bir zincir değilse, demo / kullanıcısız istekte, fiş sağlam değilse (sound) 0. Kalem: fiyatı okunmuş ürün satırı
    (indirim, hizmet, grup satırı, işyeri adı olan satır değil), birim fiyat = tutar ÷ adet; indirimli kalemde net
    (tutar − indirim) ÷ adet, discounted = 1."""
    from app import db, demo_mode, prefs, product_groups
    from app.receipt_templates import chain_of

    today = today or date.today()
    if not uid or demo_mode.enabled() or not prefs.price_share(uid):
        return 0
    from app import film_scope

    if film_scope.is_film_user(uid):  # reklam filmi hesabı topluluğa katılmaz (app/film_scope.py)
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
    items = [it for it in items if isinstance(it, dict)]
    if not sound(items, receipt_total):
        return 0
    disc = _discounts(items)
    rows = []
    for i, it in enumerate(items):
        name, amt = (it.get("name") or "").strip(), it.get("amount")
        qty = it.get("qty") or 1
        if not name or not isinstance(amt, (int, float)) or isinstance(amt, bool) or amt <= 0 or qty <= 0:
            continue
        if db.item_kind(name, it.get("product"), amt) != "product" or db.same_as_merchant(name, merchant, legal_name):
            continue
        it = {**it, "product": db.item_product(name, it.get("product"))}  # ad düzeltildiyse eski standart ad değil
        up = round((amt - disc.get(i, 0.0)) / qty, 2)
        if _MIN_PRICE <= up <= _MAX_PRICE:
            rows.append((it, up, int(i in disc)))
    if not rows:
        return 0
    groups = product_groups.groups_for([it for it, _, _ in rows])
    from app import provinces

    who = contributor(uid, today)
    region = provinces.find_in(branch)
    out = [(chain, g["key"], product_groups.name_norm(it.get("product") or it["name"]), up, day.isoformat(), who, region,
            d) for (it, up, d), g in zip(rows, groups) if g]
    with system.connect() as c:
        c.executemany("INSERT INTO community_prices (chain, group_key, name_norm, unit_price, day, contributor, region, "
                      "discounted) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", out)
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
            min_contributors: int = MIN_CONTRIBUTORS, region: str | None = None) -> dict[tuple[str, str], dict]:
    """(zincir, grup) → {median, n[, discounted, region]}: son WINDOW_DAYS gün, en az min_contributors farklı katkı,
    yalnız indirimsiz satırlar (indirimlilerin ortancası discounted; o da en az min_contributors kişiyle). Önce kişi
    başı ortanca, sonra kişiler arası ortanca. group_keys / chain verilirse yalnız onlar. region (il) verilirse önce o
    ilin satırları (yeterli kişi varsa; region yazılır), yoksa bütün satırlar."""
    from app import film_scope

    since = ((today or date.today()) - timedelta(days=WINDOW_DAYS)).isoformat()
    sql = ("SELECT chain, group_key, contributor, unit_price, region, discounted FROM community_prices WHERE day >= ?"
           + film_scope.community_clause())
    args: list = [since]
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

    def agg(rows) -> dict | None:
        people: dict[str, list[float]] = {}
        for _ch, _g, who, p, _r, _d in rows:
            people.setdefault(who, []).append(p)
        if len(people) < min_contributors:
            return None
        return {"median": round(median(median(v) for v in people.values()), 2), "n": len(people)}

    per: dict[tuple[str, str], list] = {}
    for r in got:
        per.setdefault((r[0], r[1]), []).append(r)
    out = {}
    for k, rows in per.items():
        local = [r for r in rows if region and r[4] == region]
        normal_local = agg([r for r in local if not r[5]]) if local else None
        m = normal_local or agg([r for r in rows if not r[5]])
        if not m:
            continue
        if normal_local:
            m["region"] = region
        if (d := agg([r for r in (local if normal_local else rows) if r[5]])):
            m["discounted"] = d["median"]
        out[k] = m
    return out


def disagreements(today: date | None = None) -> list[dict]:
    """Yönetici paneli "tutarsız ürünler": son 7 günün en az DISAGREE_DAYS'inde topluluk ortancası (o güne kadarki
    WINDOW_DAYS, indirimsiz, en az MIN_CONTRIBUTORS kişi) ile aynı zincirin Market Fiyatı ortancası (o gün, tüm iller)
    %DISAGREE_PCT'ten çok ayrışan (zincir, grup): [{chain, group_key, label, days, community, official, pct}] (son gün;
    fark büyükten küçüğe, en çok 30)."""
    from app import product_groups

    today = today or date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    since = (today - timedelta(days=6 + WINDOW_DAYS)).isoformat()
    with system.connect() as c:
        comm = c.execute("SELECT chain, group_key, contributor, unit_price, day FROM community_prices WHERE day >= ? "
                         "AND discounted = 0 AND contributor NOT LIKE 'film-%'", (since,)).fetchall()
        if not comm:
            return []
        off = c.execute("SELECT chain, group_key, day, price FROM official_prices WHERE day >= ? AND price > 0",
                        (days[0],)).fetchall()
    offd: dict[tuple[str, str, str], list[float]] = {}
    for ch, g, d, p in off:
        offd.setdefault((ch, g, d), []).append(p)
    per: dict[tuple[str, str], list] = {}
    for ch, g, who, p, d in comm:
        per.setdefault((ch, g), []).append((who, p, d))
    out = []
    for (ch, g), rows in per.items():
        hits, last = 0, None
        for d in days:
            lo = (date.fromisoformat(d) - timedelta(days=WINDOW_DAYS)).isoformat()
            people: dict[str, list[float]] = {}
            for who, p, rd in rows:
                if lo <= rd <= d:
                    people.setdefault(who, []).append(p)
            o = offd.get((ch, g, d))
            if len(people) < MIN_CONTRIBUTORS or not o:
                continue
            cm, om = median(median(v) for v in people.values()), median(o)
            pct = (cm - om) / om * 100
            if abs(pct) > DISAGREE_PCT:
                hits += 1
                last = {"community": round(cm, 2), "official": round(om, 2), "pct": round(pct, 1)}
        if hits >= DISAGREE_DAYS and last:
            out.append({"chain": ch, "group_key": g, "days": hits, **last})
    labels = product_groups.labels([x["group_key"] for x in out])
    out.sort(key=lambda x: (-abs(x["pct"]), x["chain"], x["group_key"]))
    return [{**x, "label": labels.get(x["group_key"], x["group_key"])} for x in out[:30]]


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
