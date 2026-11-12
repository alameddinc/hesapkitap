"""Uygulama mağazası abonelikleri (App Store / Google Play): ekstredeki genel satırlardan abonelik adayları.

Sorun: ekstrede "APPLE.COM/BILL", "GOOGLE *YouTube", "GOOGLE PLAY" gibi genel satırlar; aynı mağazadan birden çok
abonelik tek işyeri ("APPLE") gibi görünür, ne yenilendiği yakalanamaz.

Kurulum (her istekte, defterin görülebilen verisinden; önbellek db damgasıyla):
1. Tahsilatlar: kart ekstresi harcamaları (taksitsiz) + hesap dökümünden gelen giderler (karta bağlı olmayan), açıklaması
   mağaza kalıbına uyan (store_of). Aynı tahsilat (mağaza + gün + kuruş) bir kez; iadesi gelen tahsilat düşer.
2. Gruplama: mağaza + satırdaki ad ("GOOGLE *YouTube" → YouTube; Apple satırında ad yok). Her grup içinde tutar (±%25)
   ve ayın günü (±3) tutan tahsilatlar bir seri: "her ay 14'ü ₺59,99" ile "yılda bir 3 Mart ₺599,99" ayrı aday.
   Zam (₺59,99 → ₺69,99) aynı seride kalır, price_up olur.
3. Dönem: ardışık aralıkların ortancası < 200 gün → aylık, ≥ 300 → yıllık; tek tahsilat → belirsiz ("Tek çekim").
4. Ad (öncelik): kullanıcının seçtiği (katalog ya da yazılan) → ekran görüntüsü / makbuz / elle kaynağı → satırdaki ad
   (GOOGLE *Ad) → yok. Adsız aday da TAM abonelik gibi çalışır: "Apple aboneliği · ₺59,99 · her ay 14'ü", sonraki
   tahsilat tahmini, 3 gün önce hatırlatma, yönet bağlantısı, toplamlar. Adsıza fiyattan "Olabilir: …" önerisi
   (app/sub_prices_tr.py; kesin değil, "Bilmiyorum" seçilince önerilmez).
5. Kaynaklar (store_sub_sources): ekran görüntüsü / makbuz / elle. Adayla mağaza + tutar (±%2) + gün (±3) eşleşir;
   eşleşmeyen etkin kaynak "henüz ekstreye düşmemiş abonelik" olarak listede (ortak defter üyesine gösterilmez: gizli
   kartın aboneliği olabilir).
6. Karar (store_sub_labels): abonelik / değil (tek seferlik satın alma) / iptal; parmak izlerine bağlı (yeni ekstre
   gelip aday kimliği değişse de bulunur). İptalden sonra yine çekim olursa Tasarruf uyarısı ve push "abonelik".

Sayılan (toplam + hatırlatma): durumu "abonelik" ve bayat olmayan (aylıkta 75, yıllıkta 400 günden eski son tahsilat
"durmuş olabilir"). Toplam: ayda = aylıkların toplamı, yılda = aylık × 12 + yıllıklar.
Ortak defter: kart/hesap süzgeçleri db okumalarında; sayfa "tasarruf" kapalıysa uçlar kapalı (app/book.py).
"""
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from app import db, money, sub_catalog, sub_prices_tr
from app.parsers.base import PURCHASE, REFUND
from app.textnorm import normalize

STORES = {"apple": "App Store", "google": "Google Play"}
OWNER = {"apple": "Apple", "google": "Google Play"}
STORE_GLYPH = {"apple": {"glyph": "A", "color": "#1D1D1F"}, "google": {"glyph": "GP", "color": "#01875F"}}
MANAGE = {"apple": {"url": "https://apps.apple.com/account/subscriptions",
                    "app_url": "itms-apps://apps.apple.com/account/subscriptions"},
          "google": {"url": "https://play.google.com/store/account/subscriptions", "app_url": None}}
REMIND_DAYS = 3        # tahmini sonraki tahsilattan bu kadar gün önce push (bir kez)
STALE_DAYS = {"aylik": 75, "yillik": 400}
DAY_TOL = 3
AMOUNT_TOL = 0.25      # seri içi tutar oynaması (zam dahil)
MATCH_TOL = 0.02       # kaynak ↔ aday tutar eşleşmesi
AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

_APPLE = re.compile(r"APPLE\.COM ?/ ?BILL|ITUNES|APPLE ?SERVICES|APL ?\* ?ITUNES|APPLE\.COM/US/BILL")
_GOOGLE_STAR = re.compile(r"(?i)google\s*\*\s*([^/]+)")
_GOOGLE_PLAY = re.compile(r"GOOGLE ?PLAY|GOOGLE ?PAYMENT|GOOGLE ?\* ?SERVICES|GOOGLE ?SERVICES")
_GOOGLE_NOT = re.compile(r"GOOGLE ?\*? ?(ADS|ADWORDS|CLOUD|WORKSPACE|GSUITE|G ?SUITE|DOMAINS|FIBER|FI\b)")
_HINT_DROP = {"PLAY", "GOOGLE PLAY", "PLAY STORE", "PAYMENT", "PAYMENTS", "SERVICES", "STORE", "GOOGLE", "TEMPORARY HOLD",
              "PLAY APPS", "APPS"}
_LOC_WORDS = r"\b(G\.?CO|HELPPAY#?|HELP PAY|LONDON|DUBLIN|IRELAND|MOUNTAIN VIEW|CA|GB|IE|US|USA|TR|IST|ISTANBUL)\b.*$"


# ---------------------------------------------------------------- satırdan mağaza

def store_of(description: str) -> tuple[str, str | None] | None:
    """('apple' | 'google', satırdaki ad | None). Mağaza tahsilatı değilse None. Google'ın reklam/bulut satırları değil."""
    n = normalize(description or "")
    if _APPLE.search(n):
        return "apple", None
    if _GOOGLE_NOT.search(n):
        return None
    m = _GOOGLE_STAR.search(description or "")
    if m:
        hint = re.sub(r"(?i)\bg\.?co\b.*$|\bhelppay.*$", "", m.group(1))
        hint = re.sub(r"\s+", " ", hint).strip(" .*-#")
        hn = normalize(hint)
        hn = re.sub(_LOC_WORDS, "", hn).strip()
        hn = re.sub(r"^GOOGLE ", "", hn)
        words = [w for w in hn.split() if not (len(w) >= 5 and re.search(r"\d", w))]
        hn = " ".join(words)
        if not hn or hn in _HINT_DROP:
            return "google", None
        # görünen ad: özgün yazılışından ("YouTube"), konum/kod atılmış
        orig = " ".join(hint.split()[:len(hn.split()) + (1 if hint.lower().startswith("google ") else 0)])
        orig = re.sub(r"(?i)^google ", "", orig).strip() or hn.title()
        return "google", orig[:40]
    if _GOOGLE_PLAY.search(n):
        return "google", None
    return None


def fingerprint(store: str, d: str, amount: float) -> str:
    return f"{store}:{str(d)[:10]}:{money.kurus(amount)}"


def _charges() -> list[dict]:
    """Görülebilen mağaza tahsilatları (eskiden yeniye), iadesi gelenler düşmüş."""
    from app import rawnote

    out, refunds = [], []
    for t in db.card_transactions():
        s = store_of(t["description"])
        if not s:
            continue
        if t["txn_type"] == REFUND:
            refunds.append((s[0], date.fromisoformat(str(t["txn_date"])[:10]), abs(t["amount"])))
            continue
        if t["txn_type"] != PURCHASE or t["installment_no"] is not None or t["amount"] <= 0:
            continue
        out.append({"store": s[0], "hint": s[1], "date": date.fromisoformat(str(t["txn_date"])[:10]),
                    "amount": float(t["amount"]), "uid": f"c{t['id']}", "via": "kart", "fx": t.get("orig_currency")})
    for e in db.list_entries():
        if e.get("skipped") or e.get("linked_txn_id") or e.get("payment") == "kredi_karti" or e.get("debt_id"):
            continue
        raw = rawnote.raw_of(e.get("note")) or ""
        s = store_of(e["description"]) or (store_of(raw) if raw else None)
        if not s:
            continue
        d = date.fromisoformat(str(e["entry_date"])[:10])
        if e["kind"] == "gelir":
            refunds.append((s[0], d, float(e["amount"])))
        elif e["kind"] == "gider" and e["amount"] > 0:
            out.append({"store": s[0], "hint": s[1], "date": d, "amount": float(e["amount"]), "uid": f"m{e['id']}",
                        "via": "hesap", "fx": None})
    seen, uniq = set(), []
    for c in sorted(out, key=lambda x: (x["date"], x["uid"])):
        c["fp"] = fingerprint(c["store"], c["date"].isoformat(), c["amount"])
        if c["fp"] not in seen:
            seen.add(c["fp"])
            uniq.append(c)
    for st, d, amt in refunds:  # iade: aynı mağazada, önceki 60 gün içinde aynı tutarlı ilk tahsilat düşer
        hit = next((c for c in uniq if c["store"] == st and money.eq(c["amount"], amt) and 0 <= (d - c["date"]).days <= 60
                    and not c.get("refunded")), None)
        if hit:
            hit["refunded"] = True
    return [c for c in uniq if not c.get("refunded")]


# ---------------------------------------------------------------- seriler

def _daydist(a: int, b: int) -> int:
    d = abs(a - b)
    return min(d, 31 - d)


def _hint_key(c: dict) -> str:
    if not c["hint"]:
        return ""
    return sub_catalog.match_name(c["hint"]) or normalize(c["hint"])


def _cluster(charges: list[dict]) -> list[list[dict]]:
    series: list[list[dict]] = []
    for c in charges:  # tarih sırasıyla
        best = None
        for s in series:
            last = s[-1]
            gap = (c["date"] - last["date"]).days
            if gap < 20 or abs(c["amount"] - last["amount"]) > max(1.0, AMOUNT_TOL * last["amount"]):
                continue
            if _daydist(c["date"].day, last["date"].day) > DAY_TOL:
                continue
            score = (abs(c["amount"] - last["amount"]), abs(gap - 30) if gap < 200 else abs(gap - 365))
            if best is None or score < best[0]:
                best = (score, s)
        if best:
            best[1].append(c)
        else:
            series.append([c])
    return series


def _cycle(s: list[dict]) -> str | None:
    if len(s) < 2:
        return None
    gaps = [(b["date"] - a["date"]).days for a, b in zip(s, s[1:])]
    g = median(gaps)
    return "aylik" if 20 <= g < 200 else "yillik" if g >= 300 else None


def _add_months(d: date, n: int, day: int) -> date:
    import calendar

    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    m += 1
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def _next(last: date, cycle: str | None, day: int, today: date) -> date | None:
    if cycle == "aylik":
        n = _add_months(last, 1, day)
        while n < today:
            n = _add_months(n, 1, day)
        return n
    if cycle == "yillik":
        n = _add_months(last, 12, last.day)
        while n < today:
            n = _add_months(n, 12, last.day)
        return n
    return None


def _sid(*parts) -> str:
    return "a" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:11]


# ---------------------------------------------------------------- metinler

_POSS = {1: "i", 2: "si", 3: "ü", 4: "ü", 5: "i", 6: "sı", 7: "si", 8: "i", 9: "u", 0: "u"}
_POSS_TENS = {10: "u", 20: "si", 30: "u"}
_LOC = {1: "de", 2: "de", 3: "te", 4: "te", 5: "te", 6: "da", 7: "de", 8: "de", 9: "da"}
_LOC_TENS = {0: "de", 10: "da", 20: "de", 30: "da", 40: "ta", 50: "de", 60: "ta", 70: "te", 80: "de", 90: "da"}


def day_text(day: int) -> str:
    """14 → "14'ü", 6 → "6'sı", 20 → "20'si"."""
    suf = _POSS[day % 10] if day % 10 else _POSS_TENS.get(day, "u")
    return f"{day}'{suf}"


def year_loc(y: int) -> str:
    """2026 → "2026'da", 2025 → "2025'te"."""
    suf = _LOC[y % 10] if y % 10 else _LOC_TENS.get(y % 100, "de")
    return f"{y}'{suf}"


def _dm(d: date, year: bool = False) -> str:
    return f"{d.day} {AY[d.month - 1]}" + (f" {d.year}" if year else "")


def cycle_text(cycle: str | None, day: int | None, d: date | None) -> str:
    if cycle == "aylik" and day:
        return f"her ay {day_text(day)}"
    if cycle == "yillik" and d:
        return f"her yıl {_dm(d)}"
    return f"tek çekim · {_dm(d, True)}" if d else "tek çekim"


def when_text(n: int) -> str:
    return "Bugün" if n == 0 else "Yarın" if n == 1 else f"{n} gün sonra"


# ---------------------------------------------------------------- kurulum

def _labels() -> list[dict]:
    with db.connect() as c:
        out = [dict(r) for r in c.execute("SELECT * FROM store_sub_labels ORDER BY changed_at, rowid")]
    for r in out:
        try:
            r["fps"] = set(json.loads(r["fps"] or "[]"))
        except ValueError:
            r["fps"] = set()
    return out


def sources() -> list[dict]:
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM store_sub_sources ORDER BY seen_date, id")]


def _src_fp(s: dict) -> str:
    return f"kaynak:{s['id']}"


def _match_source(s: dict, series: list[dict]) -> dict | None:
    """Kaynağın adayı: aynı mağaza (biliniyorsa), tutar ±%2, gün ±3 (makbuz: tahsilat günü; ekran/elle: yenileme günü)."""
    amt = s.get("amount")
    best = None
    for x in series:
        if s.get("store") in STORES and x["store"] != s["store"]:
            continue
        recent = [c for c in x["_charges"][-4:]]
        if amt and not any(abs(c["amount"] - amt) <= max(1.0, MATCH_TOL * amt) for c in recent):
            continue
        if s["source"] == "makbuz":
            sd = date.fromisoformat(str(s["seen_date"])[:10])
            if not any(abs((c["date"] - sd).days) <= DAY_TOL for c in x["_charges"]):
                continue
        else:
            if s.get("cycle") in ("aylik", "yillik") and x["cycle"] in ("aylik", "yillik") and s["cycle"] != x["cycle"]:
                continue
            if s.get("renews"):
                r = date.fromisoformat(str(s["renews"])[:10])
                if x["cycle"] == "yillik" or s.get("cycle") == "yillik":
                    last = x["_charges"][-1]["date"]
                    if not (r.month == last.month and abs(r.day - last.day) <= DAY_TOL):
                        continue
                elif _daydist(r.day, x["day"]) > DAY_TOL:
                    continue
            elif not amt:
                continue
        score = abs((x["_charges"][-1]["amount"] - amt) if amt else 0)
        if best is None or score < best[0]:
            best = (score, x)
    return best[1] if best else None


def _raw_series(today: date) -> list[dict]:
    groups: dict[tuple, list] = defaultdict(list)
    for c in _charges():
        groups[(c["store"], _hint_key(c))].append(c)
    out = []
    for (store, hk), cs in groups.items():
        for s in _cluster(cs):
            cyc = _cycle(s)
            days = [c["date"].day for c in s[-6:]]
            day = max(set(days), key=lambda d: (days.count(d), d == s[-1]["date"].day))
            hint = next((c["hint"] for c in reversed(s) if c["hint"]), None)
            out.append({"id": _sid(store, hk, s[0]["fp"]), "store": store, "hint": hint, "cycle": cyc, "day": day,
                        "_charges": s, "fps": {c["fp"] for c in s}})
    return out


def _build(today: date) -> list[dict]:
    from app import book

    series = _raw_series(today)
    member = book.member() is not None
    srcs = sources()
    matched: dict[str, list[dict]] = defaultdict(list)
    loose = []
    for s in srcs:
        x = _match_source(s, series)
        if x:
            matched[x["id"]].append(s)
            x["fps"].add(_src_fp(s))
        elif s["status"] == "aktif" and not member:
            loose.append(s)
    labels = _labels()
    items = []
    for x in series:
        items.append(_item(x, matched.get(x["id"], []), labels, today))
    for s in loose:
        items.append(_loose_item(s, labels, today))
    order = {"abonelik": 0, "aday": 1, "iptal": 2, "degil": 3}
    items.sort(key=lambda i: (order.get(i["status"], 9), i["stale"], -(i["monthly"] or 0), i["title"]))
    return items


def _label_for(fps: set, labels: list[dict]) -> dict | None:
    hit = None
    for lb in labels:  # changed_at sırasıyla: en son karar kazanır
        if lb["fps"] & fps:
            hit = lb
    return hit


def _view_for(store: str | None, name: str | None, cid: str | None) -> dict:
    if cid and sub_catalog.view(cid):
        return sub_catalog.view(cid)
    if name:
        return sub_catalog.glyph_for(name)
    g = STORE_GLYPH.get(store or "", {"glyph": "?", "color": "#6B7280"})
    return {"id": None, "name": None, "glyph": g["glyph"], "color": g["color"],
            "fg": "#FFFFFF", "group": "diger"}


def _common(store: str | None, name: str | None, cid: str | None, named_by: str | None) -> dict:
    title = name or (f"{OWNER[store]} aboneliği" if store in OWNER else "Abonelik")
    return {"store": store, "store_label": STORES.get(store or "", "Diğer"), "name": name, "title": title,
            "catalog_id": cid, "named_by": named_by, "logo": _view_for(store, name, cid),
            "manage": [{"store": k, "label": f"{v} aboneliklerimi yönet", **MANAGE[k]}
                       for k, v in STORES.items() if store in (k, None, "diger")]}


def _item(x: dict, srcs: list[dict], labels: list[dict], today: date) -> dict:
    cs = x["_charges"]
    last = cs[-1]
    lb = _label_for(x["fps"], labels)
    src = max(srcs, key=lambda s: (s["seen_date"], s["id"])) if srcs else None
    cycle = (lb and lb.get("cycle")) or x["cycle"] or (src and src.get("cycle") in ("aylik", "yillik") and src["cycle"]) or None
    # ad
    name, cid, named_by = None, None, None
    if lb and (lb.get("catalog_id") or lb.get("name")):
        cid, named_by = lb.get("catalog_id"), "sen"
        name = lb.get("name") or (sub_catalog.get(cid) or {}).get("name")
    elif src:
        cid, name, named_by = src.get("catalog_id"), src["name"], src["source"]
    elif x["hint"]:
        cid = sub_catalog.match_name(x["hint"])
        name, named_by = ((sub_catalog.get(cid) or {}).get("name") or x["hint"]), "ekstre"
    # durum
    status, cancelled_at = ("abonelik" if cycle or src else "aday"), None
    if src and src["status"] == "iptal":
        status, cancelled_at = "iptal", str(src.get("renews") or src["seen_date"])[:10]
    if lb and lb.get("status"):
        status = lb["status"]
        cancelled_at = str(lb["changed_at"])[:10] if status == "iptal" else None
    amount = last["amount"]
    stale = bool(cycle and (today - last["date"]).days > STALE_DAYS[cycle])
    nxt = None
    if status == "abonelik" and not stale:
        nxt = _next(last["date"], cycle, x["day"], today)
        if src and src.get("renews") and src["status"] == "aktif":
            r = date.fromisoformat(str(src["renews"])[:10])
            if r >= today:
                nxt = r
    prev = cs[-2]["amount"] if len(cs) > 1 and not money.eq(cs[-2]["amount"], amount) else None
    up = None
    if prev is not None and amount - prev >= max(0.5, prev / 100):  # zam: son tahsilat öncekinden yüksek (en az %1)
        up = {"from": money.money(prev), "to": money.money(amount), "date": last["date"].isoformat()}
    after = [c for c in cs if cancelled_at and c["date"].isoformat() > cancelled_at] if status == "iptal" else []
    monthly = money.money(amount) if cycle == "aylik" else money.money(amount / 12) if cycle == "yillik" else None
    yearly = money.money(amount * 12) if cycle == "aylik" else money.money(amount) if cycle == "yillik" else None
    first = cs[0]["date"]
    guesses = []
    if not name and not (lb and lb.get("unknown")) and status in ("abonelik", "aday"):
        guesses = sub_prices_tr.guesses(amount, x["store"], cycle)
    counted = status == "abonelik" and not stale and cycle in ("aylik", "yillik")
    return {
        "id": x["id"], **_common(x["store"], name, cid, named_by),
        "status": status, "confirmed": bool(lb and lb.get("status") == "abonelik"), "unknown": bool(lb and lb.get("unknown")),
        "cycle": cycle, "day": x["day"] if cycle == "aylik" else None,
        "cycle_text": cycle_text(cycle, x["day"], last["date"] if cycle != "aylik" else None),
        "amount": money.money(amount), "monthly": monthly, "yearly": yearly, "counted": counted,
        "prev_amount": money.money(prev) if prev is not None else None, "price_up": up,
        "first_date": first.isoformat(), "last_date": last["date"].isoformat(), "count": len(cs),
        "first_text": f"İlk kez {AY[first.month - 1]} {year_loc(first.year)} çıktı · "
                      + (f"{len(cs)} kez çekildi" if len(cs) > 1 else "bir kez çekildi"),
        "next_date": nxt.isoformat() if nxt else None,
        "next_text": f"Sonraki tahsilat {_dm(nxt)}" + (" (tahmini)" if not (src and src.get("renews")) else "") if nxt else None,
        "stale": stale, "stale_text": f"Son çekim {_dm(last['date'], True)} · durmuş olabilir" if stale else None,
        "cancelled_at": cancelled_at,
        "charged_after_cancel": money.money(sum(c["amount"] for c in after)) if after else 0.0,
        "guesses": guesses, "hint": x["hint"], "fx": last.get("fx"),
        "source": src["source"] if src else None, "source_id": src["id"] if src else None,
        "plan": src.get("plan") if src else None,
        "charges": [{"date": c["date"].isoformat(), "amount": money.money(c["amount"]), "via": c["via"], "uid": c["uid"]}
                    for c in reversed(cs[-12:])],
        "pending": False,
    }


def _loose_item(s: dict, labels: list[dict], today: date) -> dict:
    """Ekstreye henüz düşmemiş (ekran görüntüsü / makbuz / elle) abonelik."""
    lb = _label_for({_src_fp(s)}, labels)
    cycle = (lb and lb.get("cycle")) or (s["cycle"] if s.get("cycle") in ("aylik", "yillik") else "aylik")
    renews = date.fromisoformat(str(s["renews"])[:10]) if s.get("renews") else None
    status = (lb and lb.get("status")) or "abonelik"
    nxt = None
    if renews and status == "abonelik":
        nxt = renews
        while nxt < today:
            nxt = _add_months(nxt, 1 if cycle == "aylik" else 12, renews.day)
    name = (lb and (lb.get("name") or (sub_catalog.get(lb.get("catalog_id")) or {}).get("name"))) or s["name"]
    cid = (lb and lb.get("catalog_id")) or s.get("catalog_id")
    amount = s.get("amount") or 0.0
    counted = status == "abonelik" and bool(amount)
    return {
        "id": f"s{s['id']}", **_common(s.get("store"), name, cid, "sen" if lb and lb.get("name") else s["source"]),
        "status": status, "confirmed": True, "unknown": False, "cycle": cycle,
        "day": (renews or today).day if cycle == "aylik" else None,
        "cycle_text": cycle_text(cycle, (renews or today).day, renews),
        "amount": money.money(amount) if amount else None,
        "monthly": money.money(amount if cycle == "aylik" else amount / 12) if amount else None,
        "yearly": money.money(amount * 12 if cycle == "aylik" else amount) if amount else None, "counted": counted,
        "prev_amount": None, "price_up": None, "first_date": None, "last_date": None, "count": 0,
        "first_text": "Henüz ekstreye düşmemiş" + (" · ekran görüntüsünden" if s["source"] == "ekran"
                                                   else " · makbuzdan" if s["source"] == "makbuz" else " · elle eklendi"),
        "next_date": nxt.isoformat() if nxt else None,
        "next_text": f"Sonraki tahsilat {_dm(nxt)}" if nxt else None,
        "stale": False, "stale_text": None, "cancelled_at": str(lb["changed_at"])[:10] if status == "iptal" else None,
        "charged_after_cancel": 0.0, "guesses": [], "hint": None, "fx": None,
        "source": s["source"], "source_id": s["id"], "plan": s.get("plan"), "charges": [], "pending": True,
    }


def items(today: date | None = None) -> list[dict]:
    from app import book

    today = today or date.today()
    m = book.member()
    hit = db._cached("store_subs", lambda: _build(today), today.isoformat(), m["id"] if m else None)
    return json.loads(json.dumps(hit))  # çağıran değiştirebilir: kopya


def totals(rows: list[dict]) -> dict:
    mon = sum(i["amount"] for i in rows if i["counted"] and i["cycle"] == "aylik" and i["store"] in STORES)
    yr = sum(i["amount"] for i in rows if i["counted"] and i["cycle"] == "yillik" and i["store"] in STORES)
    year = mon * 12 + yr
    n = sum(1 for i in rows if i["counted"] and i["store"] in STORES)
    return {"monthly": money.money(mon), "yearly": money.money(year), "count": n,
            "text": f"Uygulama abonelikleri: ayda {money.tl(mon, trim=True)} · yılda {money.tl(year, trim=True)}" if n else None}


HELP = {
    "title": "Abonelik listeni ekran görüntüsüyle tanıt",
    "text": "Mağazanın abonelikler sayfasının ekran görüntüsünü yükle; uygulama adı, fiyat, dönem ve yenileme günü okunur, "
            "ekstredeki tahsilatlarla eşleşir. Her ekran görüntüsü 1 okuma hakkı.",
    "steps": {
        "apple": ["Ayarlar'ı aç", "En üstte adına dokun", "Abonelikler'e dokun",
                  "Ekran görüntüsü al (yan tuş + ses açma); liste uzunsa kaydırıp bir tane daha"],
        "google": ["Play Store'u aç", "Sağ üstte profil resmine dokun", "Ödemeler ve abonelikler → Abonelikler",
                   "Ekran görüntüsü al (güç + ses kısma); liste uzunsa kaydırıp bir tane daha"],
    },
    "open": {k: {"label": "Abonelikler sayfasını aç", **v} for k, v in MANAGE.items()},
    "mail": {"title": "Gelişmiş: makbuz e-postalarını ilet",
             "text": "Apple ve Google Play makbuzlarını deftere özel posta adresine yönlendirirsen uygulama adı, tutar ve "
                     "yenileme günü kendiliğinden eşleşir. Gmail'de: Ayarlar → Filtreler → Yeni filtre → Kimden: "
                     "no_reply@email.apple.com OR googleplay-noreply@google.com → Şuraya yönlendir: posta adresin.",
             "filter": "from:(no_reply@email.apple.com OR googleplay-noreply@google.com)"},
}


def overview(today: date | None = None) -> dict:
    """GET /api/subscriptions/apps: mağaza grubu, elle eklenen diğerleri, toplamlar, yardım."""
    from app import book, plans

    rows = items(today)
    store_rows = [i for i in rows if i["store"] in STORES]
    other = [i for i in rows if i["store"] not in STORES]
    u = db.current_user()
    mail = dict(HELP["mail"])
    mail["available"] = bool(u and plans.allows(u, "posta") and not book.member())
    return {"available": True, "items": store_rows, "other": other, "totals": totals(rows),
            "candidates": sum(1 for i in store_rows if i["status"] == "aday"),
            "help": {**HELP, "mail": mail}}


def find(sid: str, today: date | None = None) -> dict | None:
    return next((i for i in items(today) if i["id"] == sid), None)


# ---------------------------------------------------------------- kararlar

ACTIONS = ("adlandir", "abonelik", "degil", "iptal", "aktif", "bilmiyorum", "sifirla")


def act(sid: str, action: str, catalog_id: str | None = None, name: str | None = None, cycle: str | None = None,
        when: str | None = None, today: date | None = None) -> dict:
    """Adaya karar. adlandir: katalogdan ya da yazılan ad (abonelik sayılır); abonelik: onayla (dönem verilebilir);
    degil: tek seferlik satın alma; iptal: iptal ettim (when gün); aktif: geri al; bilmiyorum: tahmin önerme;
    sifirla: kararı sil. Bulunamazsa LookupError, geçersizse ValueError."""
    today = today or date.today()
    if action not in ACTIONS:
        raise ValueError("Geçersiz işlem")
    it = find(sid, today)
    if not it:
        raise LookupError("Abonelik bulunamadı")
    if catalog_id and not sub_catalog.get(catalog_id):
        raise ValueError("Katalogda yok")
    if cycle not in (None, "aylik", "yillik"):
        raise ValueError("Dönem aylık ya da yıllık olmalı")
    if action == "adlandir" and not (catalog_id or (name or "").strip()):
        raise ValueError("Bir ad seç ya da yaz")
    fps = sorted({fingerprint(it["store"], c["date"], c["amount"]) for c in it["charges"]}
                 | ({f"kaynak:{it['source_id']}"} if it.get("source_id") else set()))
    # tüm geçmiş tahsilatlar (yalnız son 12 gösterilir): yeniden kur
    for x in _raw_series(today):
        if x["id"] == sid:
            fps = sorted(set(fps) | x["fps"])
    prev = _label_for(set(fps), _labels())
    with db.connect() as c:
        if action == "sifirla":
            if prev:
                c.execute("DELETE FROM store_sub_labels WHERE id = ?", (prev["id"],))
            return {"ok": True}
        row = {k: prev.get(k) for k in ("status", "catalog_id", "name", "cycle", "unknown")} if prev else \
            {"status": None, "catalog_id": None, "name": None, "cycle": None, "unknown": 0}
        if action == "adlandir":
            row.update(catalog_id=catalog_id, name=None if catalog_id else name.strip()[:60], unknown=0,
                       status=row["status"] if row["status"] in ("iptal",) else "abonelik")
        elif action == "abonelik":
            row.update(status="abonelik")
        elif action == "degil":
            row.update(status="degil")
        elif action == "iptal":
            row.update(status="iptal")
        elif action == "aktif":
            row.update(status="abonelik")
        elif action == "bilmiyorum":
            row.update(unknown=1, status=row["status"] or ("abonelik" if it["cycle"] else None))
        if cycle:
            row["cycle"] = cycle
            if row["status"] is None:
                row["status"] = "abonelik"
        changed = (when or today.isoformat())[:10] if action == "iptal" else today.isoformat()
        u = db.current_user()
        if prev:
            c.execute("DELETE FROM store_sub_labels WHERE id = ?", (prev["id"],))
        c.execute("INSERT OR REPLACE INTO store_sub_labels (id, fps, status, catalog_id, name, cycle, unknown, changed_at, "
                  "created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (sid, json.dumps(fps), row["status"], row["catalog_id"], row["name"], row["cycle"], int(row["unknown"] or 0),
                   changed, (u or {}).get("id")))
    return {"ok": True}


# ---------------------------------------------------------------- kaynaklar (ekran / makbuz / elle)

def _dedupe(source: str, x: dict) -> str:
    key = x.get("catalog_id") or normalize(x["name"])
    if source == "makbuz" and x.get("ref"):
        return f"makbuz:{x['ref']}:{key}"
    if source == "elle":
        import secrets

        return f"elle:{secrets.token_hex(6)}"
    return f"{source}:{x.get('store') or '-'}:{key}:{money.kurus(x['amount']) if x.get('amount') else '-'}"


def save_source(source: str, x: dict, seen: date) -> tuple[int, bool]:
    """Kaynak satırı ekle ya da (aynı ekran/makbuz satırı) yenile. (id, yeni mi)."""
    u = db.current_user()
    dd = _dedupe(source, x)
    vals = (source, x.get("store"), x["name"][:80], (x.get("plan") or None), x.get("catalog_id"),
            money.money(x["amount"]) if x.get("amount") else None, x.get("cycle"),
            x["date"].isoformat() if isinstance(x.get("date"), date) else x.get("date"), seen.isoformat(),
            "aktif" if x.get("active", True) else "iptal", x.get("ref"))
    with db.connect() as c:
        r = c.execute("SELECT id FROM store_sub_sources WHERE dedupe = ?", (dd,)).fetchone()
        if r:
            c.execute("UPDATE store_sub_sources SET source=?, store=?, name=?, plan=?, catalog_id=?, amount=?, cycle=?, "
                      "renews=?, seen_date=?, status=?, ref=? WHERE id = ?", (*vals, r[0]))
            return r[0], False
        cur = c.execute("INSERT INTO store_sub_sources (source, store, name, plan, catalog_id, amount, cycle, renews, "
                        "seen_date, status, ref, dedupe, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (*vals, dd, (u or {}).get("id")))
        return cur.lastrowid, True


def update_source(src_id: int, data: dict) -> bool:
    allowed = {"name", "catalog_id", "amount", "cycle", "renews", "store", "plan"}
    data = {k: v for k, v in data.items() if k in allowed}
    if "catalog_id" in data and data["catalog_id"] and not sub_catalog.get(data["catalog_id"]):
        raise ValueError("Katalogda yok")
    if data.get("catalog_id") and not data.get("name"):
        data["name"] = sub_catalog.get(data["catalog_id"])["name"]
    if not data:
        return True
    with db.connect() as c:
        sets = ", ".join(f"{k} = ?" for k in data)
        return c.execute(f"UPDATE store_sub_sources SET {sets} WHERE id = ?",
                         (*[v.isoformat() if isinstance(v, date) else v for v in data.values()], src_id)).rowcount > 0


def delete_source(src_id: int) -> bool:
    with db.connect() as c:
        return c.execute("DELETE FROM store_sub_sources WHERE id = ?", (src_id,)).rowcount > 0


def ingest(rows: list[dict], source: str, seen: date) -> dict:
    """Ekran görüntüsünden ya da makbuzdan gelen satırları kaydet; her satırın eşleştiği aday."""
    out = []
    for x in rows:
        if not x.get("name"):
            continue
        sid, new = save_source(source, x, seen)
        out.append({"source_id": sid, "new": new, **{k: (v.isoformat() if isinstance(v, date) else v)
                                                     for k, v in x.items()}})
    now = items(seen if seen > date.today() else None)
    by_src = {i["source_id"]: i for i in now if i.get("source_id")}
    for o in out:
        it = by_src.get(o["source_id"])
        o["match"] = None if not it else {"id": it["id"], "pending": it["pending"], "title": it["title"]}
    matched = sum(1 for o in out if o["match"] and not o["match"]["pending"])
    pending = sum(1 for o in out if o["match"] and o["match"]["pending"])
    return {"rows": out, "matched": matched, "pending": pending, "skipped": sum(1 for o in out if not o["match"])}


def summary_text(res: dict) -> str:
    parts = []
    if res["matched"]:
        parts.append(f"{res['matched']} abonelik ekstredeki tahsilatla eşleşti")
    if res["pending"]:
        parts.append(f"{res['pending']} abonelik henüz ekstreye düşmemiş, listeye eklendi")
    if res["skipped"]:
        parts.append(f"{res['skipped']} etkin olmayan abonelik atlandı")
    return "; ".join(parts) + "." if parts else "Ekranda abonelik bulunamadı."


# ---------------------------------------------------------------- dışa: hatırlatma, uyarı, plan

def route(sid: str) -> str:
    return f"#/diger/tasarruf/abonelik/{sid}"


def reminders(today: date) -> list[dict]:
    """Push "abonelik": tahmini sonraki tahsilata REMIND_DAYS gün kala (bir kez, kimlik tarihe bağlı)."""
    from app import push

    out = []
    for i in items(today):
        if not i["counted"] or not i["next_date"] or not i["amount"]:
            continue
        nd = date.fromisoformat(i["next_date"])
        left = (nd - today).days
        if left != REMIND_DAYS:
            continue
        amt = money.tl(i["amount"], trim=True)
        out.append(push.item("abonelik", f"abonelik:yenileme:{i['id']}:{i['next_date']}",
                             f"{when_text(left)} {i['title']} {amt} yenileniyor",
                             f"{_dm(nd)} · {i['store_label']}. Kullanmıyorsan yenilenmeden önce iptal edebilirsin: "
                             "Aboneliklerimi yönet.", route(i["id"])))
    return out


def price_up_items(today: date, recent_days: int = 45) -> list[dict]:
    """Ekstre okununca: mağaza aboneliğinde zam (son tahsilat yeni ve öncekinden yüksek)."""
    from app import push

    out = []
    recent = (today - timedelta(days=recent_days)).isoformat()
    for i in items(today):
        up = i.get("price_up")
        if i["status"] == "abonelik" and up and i["last_date"] >= recent:
            out.append(push.item("abonelik", f"abonelik:zam:store:{i['id']}:{i['last_date']}", f"{i['title']} zamlandı",
                                 f"{money.tl(up['from'], trim=True)} → {money.tl(up['to'], trim=True)}. Kullanmıyorsan "
                                 "iptal edip iptal ettim diye işaretle.", route(i["id"])))
    return out


def cancel_alerts(today: date | None = None) -> list[dict]:
    """finance.insights: iptal edilmiş mağaza aboneliğinden yine çekim (anahtar iptal_cekim:… → push "abonelik")."""
    out = []
    for i in items(today):
        if i["status"] == "iptal" and i["charged_after_cancel"]:
            d = date.fromisoformat(i["cancelled_at"])
            slug = re.sub(r"[\s/:]+", "-", i["title"].casefold())[:40]
            out.append({"level": "kritik", "title": f"{i['title']} iptal edilmişti ama yine çekildi",
                        "detail": f"{d:%d.%m} iptalinden sonra {money.tl(i['charged_after_cancel'])} çekim var. İptal "
                                  "tamamlanmamış olabilir; mağazanın abonelikler sayfasından kontrol et, iade iste.",
                        "link": "tasarruf", "key": f"iptal_cekim:store-{i['id']}:{slug}:{money.money(i['charged_after_cancel']):g}",
                        "target": {"screen": "abonelik", "id": i["id"]}})
    return out


def excluded_txn_ids(today: date | None = None) -> set[int]:
    """Planlama (finance.card_projection) "abonelik değil" ve iptal dediğin mağaza tahsilatlarını tekrar eden abonelik
    saymaz: o adayların kart işlemleri."""
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM store_sub_labels WHERE status IN ('degil', 'iptal') LIMIT 1").fetchone():
            return set()
    out = set()
    for i in items(today):
        if i["status"] in ("degil", "iptal"):
            out |= {int(c["uid"][1:]) for c in i["charges"] if c["uid"].startswith("c")}
    return out


def merchant_store(description: str) -> str | None:
    """finance.subscriptions satırı mağaza tahsilatı mı (yeni istemci bu satırları mağaza grubunda gösterir)."""
    s = store_of(description)
    return s[0] if s else None
