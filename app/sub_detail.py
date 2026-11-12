"""Abonelik ayrıntısı (GET /api/subscriptions/detail, yetenek subscription_detail): "Bu aboneliğe toplam ne ödedim, hangi
ay ne ödedim", işlemler (dokununca işlemin kendisi açılır).

İki tür:
- Kart aboneliği (?merchant=): finance.subscription_groups() ile AYNI gruplama (aynı merchant_name, ödeme aracısı ön
  ekleri atılmış; Tasarruf listesindeki satırla birebir aynı işlemler). Durum subscription_states'ten (iptal/düşürüldü).
- Mağaza aboneliği (?app=<id>): app/store_subs.py serisi; tüm tahsilatlar (listede yalnız son 12 gösterilir), kart
  ekstresinden ve hesap dökümünden.

Ay: işlemin tarihinin takvim ayı ("Ekim 2026'da ne ödedim"). Ay satırında değişim bir önceki ödemeli aya göre: dövizli
abonelikte döviz tutarı değiştiyse zam/indirim, yalnız TL değiştiyse "kur" (zam sayılmaz). Aynı ayda birden çok çekim
"adet" farkıdır, zam sayılmaz.

Toplamlar sunucuda (istemci toplamaz): tümü ve son 12 ay (bu ay dahil 12 takvim ayı); dövizli olanlarda döviz toplamı.
Ortak defter: gizli kart/hesap işlemleri db okumalarında süzülür (sayılmaz, listelenmez); sayfa "tasarruf" kapalıysa uç
kapalı (app/book.py, /api/subscriptions öneki).
"""
import re
from collections import defaultdict
from datetime import date

from app import db, finance, money, store_subs, sub_catalog
from app.textnorm import merchant_name

AY = store_subs.AY
LAST_N = 12


def title_of(s: str) -> str:
    """Web/mobil title() ile aynı: küçült, boşluktan sonraki harfi büyüt ("ORNEK MUZIK" → "Ornek Muzik")."""
    return re.sub(r"(^|\s)(\S)", lambda m: m.group(1) + m.group(2).upper(), (s or "").lower())


def month_label(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{AY[m - 1]} {y}"


def _card_label(t: dict) -> str:
    bank = re.sub(r"\s+(KATILIM\s+)?BANKASI.*$", "", t.get("bank") or "", flags=re.I).strip()
    no = re.sub(r"\D", "", t.get("account") or t.get("card") or "")
    return f"{bank} …{no[-4:]}".strip() if no else (bank or "Kredi kartı")


def _fx(t: dict) -> tuple[float, str] | None:
    if t.get("orig_currency") and t.get("orig_amount") is not None:
        return float(t["orig_amount"]), str(t["orig_currency"])
    return None


def _fx_text(by: dict[str, float]) -> str | None:
    return " + ".join(f"{money.money(v):g} {k}" for k, v in sorted(by.items())) if by else None


def _card_txn(t: dict) -> dict:
    fx = _fx(t)
    return {"uid": f"c{t['id']}", "date": str(t["txn_date"])[:10], "row_month": t["period"], "amount": float(t["amount"]),
            "label": _card_label(t), "via": "kart", "fx": fx, "description": t["description"]}


def _since(today: date) -> str:
    return finance.add_months(f"{today:%Y-%m}", -(LAST_N - 1))


def _total(txns: list[dict], since: str | None) -> dict:
    sel = [x for x in txns if since is None or x["date"][:7] >= since]
    fx: dict[str, float] = defaultdict(float)
    for x in sel:
        if x["fx"]:
            fx[x["fx"][1]] += x["fx"][0]
    first = min((x["date"] for x in sel), default=None)
    n = len(sel)
    text = None
    if first:
        y, m = int(first[:4]), int(first[5:7])
        # ayrılma eki = bulunma eki + n: "2026'da" → "Mart 2026'dan", "2025'te" → "2025'ten"
        text = f"{AY[m - 1]} {store_subs.year_loc(y)}n bu yana {n} ödeme" if since is None else f"Son 12 ayda {n} ödeme"
    return {"amount": money.money(sum(x["amount"] for x in sel)) + 0.0, "count": n, "since": since, "first_date": first,
            "fx_text": _fx_text(fx), "text": text}


def _change(cur: dict, prev: dict | None) -> dict | None:
    if not prev:
        return None
    diff = money.money(cur["amount"] - prev["amount"])
    if cur["count"] != prev["count"]:
        return {"kind": "adet", "diff": diff, "from": prev["amount"], "to": cur["amount"], "fx_text": None}
    cf, pf = cur["_fx"], prev["_fx"]
    if cf and pf and len(cf) == 1 and cf.keys() == pf.keys():
        k = next(iter(cf))
        a, b = pf[k], cf[k]
        if abs(b - a) >= max(money.CENT, a / 100):
            return {"kind": "zam" if b > a else "indirim", "diff": diff, "from": prev["amount"], "to": cur["amount"],
                    "fx_text": f"{money.money(a):g} {k} → {money.money(b):g} {k}"}
        if not money.is_zero(diff):
            return {"kind": "kur", "diff": diff, "from": prev["amount"], "to": cur["amount"], "fx_text": None}
        return None
    if abs(diff) >= max(0.5, prev["amount"] / 100):
        return {"kind": "zam" if diff > 0 else "indirim", "diff": diff, "from": prev["amount"], "to": cur["amount"],
                "fx_text": None}
    return None


def _payload(txns: list[dict], today: date, cancelled_at: str | None, new_amount: float | None,
             changed_at: str | None) -> dict:
    """Ortak gövde: toplamlar, ay ay, işlemler (yeniden eskiye). cancelled_at: iptal günü (sonraki çekim uyarılı);
    new_amount + changed_at: paket düşürüldü (sonraki aylarda yeni tutarın %15 fazlası "fazla")."""
    txns = sorted(txns, key=lambda x: (x["date"], x["uid"]))
    by: dict[str, dict] = {}
    for x in txns:
        x["after_cancel"] = bool(cancelled_at and x["date"] > cancelled_at)
        ym = x["date"][:7]
        m = by.setdefault(ym, {"month": ym, "label": month_label(ym), "amount": 0.0, "count": 0, "_fx": defaultdict(float),
                               "after_cancel": False})
        m["amount"] += x["amount"]
        m["count"] += 1
        m["after_cancel"] = m["after_cancel"] or x["after_cancel"]
        if x["fx"]:
            m["_fx"][x["fx"][1]] += x["fx"][0]
    months, prev = [], None
    for ym in sorted(by):
        m = by[ym]
        m["amount"] = money.money(m["amount"]) + 0.0
        m["change"] = _change(m, prev)
        m["over"] = bool(new_amount is not None and changed_at and ym > changed_at[:7] and m["amount"] > new_amount * 1.15)
        prev = m
        months.append(m)
    since = _since(today)
    out_months = [{k: v for k, v in m.items() if k != "_fx"} | {"fx_text": _fx_text(m["_fx"])} for m in reversed(months)]
    out_txns = [{"uid": x["uid"], "date": x["date"], "month": x["row_month"], "amount": money.money(x["amount"]) + 0.0,
                 "label": x["label"], "via": x["via"], "fx": f"{x['fx'][0]:g} {x['fx'][1]}" if x["fx"] else None,
                 "description": x["description"], "after_cancel": x["after_cancel"]} for x in reversed(txns)]
    after = [x for x in txns if x["after_cancel"]]
    has_fx = any(x["fx"] for x in txns)
    return {
        "totals": {"all": _total(txns, None), "last12": _total(txns, since)},
        "since12": since,
        "months": out_months, "transactions": out_txns,
        "first_date": txns[0]["date"] if txns else None, "last_date": txns[-1]["date"] if txns else None,
        "fx": has_fx,
        "fx_note": "Dövizle ödendi: tutarlar kart ekstresindeki TL karşılığı; kur değiştikçe aydan aya oynar." if has_fx else None,
        "after_cancel": {"amount": money.money(sum(x["amount"] for x in after)) + 0.0, "count": len(after)} if after else None,
    }


# ---------------------------------------------------------------- kart aboneliği

def card_detail(merchant: str, today: date | None = None) -> dict | None:
    today = today or date.today()
    states = db.subscription_states()
    groups = finance.subscription_groups(states)
    key = merchant if merchant in groups else merchant_name(merchant)
    ts = groups.get(key)
    if not ts:
        return None
    sub = next((s for s in finance.subscriptions() if s["merchant"] == key), None)
    st = states.get(key)
    status = st["status"] if st else "aktif"
    cancelled_at = st["changed_at"] if st and status == "iptal" else None
    new_amount = st["new_amount"] if st and status == "dusuruldu" else None
    body = _payload([_card_txn(t) for t in ts], today, cancelled_at, new_amount, st["changed_at"] if st else None)
    monthly = sub["monthly"] if sub else None
    status_text = {"aktif": "Etkin", "iptal": "İptal ettim", "dusuruldu": "Paket düşürüldü"}[status]
    return {
        "kind": "card", "key": key, "merchant": key, "title": title_of(key),
        "logo": sub["logo"] if sub else sub_catalog.glyph_for(title_of(key)),
        "category": ts[0]["category"], "status": status, "status_text": status_text,
        "status_date": st["changed_at"][:10] if st and status != "aktif" else None,
        "amount": new_amount if new_amount is not None else monthly, "prev_amount": monthly if new_amount is not None else None,
        "per": "ay", "store_label": None,
        **body,
    }


# ---------------------------------------------------------------- mağaza aboneliği

def _store_txns(charges: list[dict]) -> list[dict]:
    cards = {f"c{t['id']}": t for t in db.card_transactions()}
    need_entries = any(c["uid"].startswith("m") for c in charges)
    entries = {f"m{e['id']}": e for e in db.list_entries()} if need_entries else {}
    accs = {a["id"]: a["name"] for a in db.list_accounts()} if need_entries else {}
    out = []
    for c in charges:
        if c["uid"] in cards:
            out.append(_card_txn(cards[c["uid"]]))
            continue
        e = entries.get(c["uid"])
        if not e:
            continue
        out.append({"uid": c["uid"], "date": str(e["entry_date"])[:10], "row_month": e["month"], "amount": float(e["amount"]),
                    "label": accs.get(e.get("account_id")) or "Hesap dökümü", "via": "hesap", "fx": None,
                    "description": e["description"]})
    return out


def store_detail(sid: str, today: date | None = None) -> dict | None:
    today = today or date.today()
    it = store_subs.find(sid, today)
    if not it:
        return None
    x = next((s for s in store_subs._raw_series(today) if s["id"] == sid), None)
    charges = [{"uid": c["uid"]} for c in x["_charges"]] if x else [{"uid": c["uid"]} for c in it["charges"]]
    status = it["status"]
    cancelled_at = it["cancelled_at"] if status == "iptal" else None
    body = _payload(_store_txns(charges), today, cancelled_at, None, None)
    status_text = {"abonelik": "Etkin", "aday": "Tek çekim · abonelik mi?", "iptal": "İptal ettim",
                   "degil": "Abonelik değil"}.get(status, status)
    if it["stale"]:
        status_text = "Durmuş olabilir"
    return {
        "kind": "store", "key": sid, "merchant": None, "title": it["title"], "logo": it["logo"], "category": None,
        "status": status, "status_text": status_text, "status_date": cancelled_at,
        "amount": it["amount"], "prev_amount": it["prev_amount"],
        "per": "ay" if it["cycle"] == "aylik" else "yıl" if it["cycle"] == "yillik" else None,
        "store_label": it["store_label"],
        **body,
    }
