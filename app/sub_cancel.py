"""İptalden sonra çekim: karar ve iade eşleşmesi (yetenek subscription_after_cancel).

"X iptal edilmişti ama yine çekildi" uyarısına (finance.insights, store_subs.cancel_alerts) dokununca istemci iptalden
sonraki çekimleri listeler (GET /api/subscriptions/detail, transactions[].after_cancel) ve karar verir
(POST /api/subscriptions/after-cancel {merchant | app, action}):
- iade_istedim: iade istendi (gün saklanır). Uyarı "iade bekleniyor" olur (düzey uyari, push yok); iade gelince kapanır.
  Sonra yeni bir çekim gelirse uyarı yine kritik.
- son_cekim: "İptal tamam, bu son çekimdi": bugüne kadarki iptal sonrası çekimler kapanır; sonra gelen çekim yeniden uyarır.
- iade_geldi: iade başka hesaba geldi ya da ekstrede görünmüyor: aynı kapanış, nedeni iade.
- sifirla: kararı geri al.
"Abonelik devam ediyor" yeni uç istemez: PUT /api/subscriptions status aktif (mağaza: PUT …/apps/{id} action aktif).
Karar iptal gününe bağlı saklanır: iptal günü değişince (aktif yapıp yeniden iptal) eski karar kendiliğinden geçersiz.

İade eşleşmesi (kart aboneliği): aynı işyerinden (merchant_name) iade satırı (txn_type REFUND, eksi tutar), çekimle aynı
gün ya da sonra REFUND_DAYS gün içinde, tutarı eşit (dövizli çekimde FX_TOL kur payı) → çekim "iade" ile kapanır (her
iade bir çekime, eskiden yeniye). Mağaza aboneliğinde store_subs._charges iadesi gelen tahsilatı zaten düşürür.

Kayıt defter başına settings "subs:after_cancel" (JSON): {"card:<merchant>" | "store:<id>": {cancelled_at, refund_at,
closed_until, closed_why}}. Ortak defterde abonelik durumu gibi defterin kaydıdır.
"""
import json
from datetime import date

from app import db, money

SKEY = "subs:after_cancel"
REFUND_DAYS = 120
FX_TOL = 0.05
ACTIONS = ("iade_istedim", "son_cekim", "iade_geldi", "sifirla")
MAX_KEYS = 300


def ref(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def _all() -> dict:
    try:
        raw = json.loads(db.get_setting(SKEY) or "{}")
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def get(r: str, cancelled_at: str | None) -> dict:
    """Bu iptale ait karar ({} = yok). İptal günü değiştiyse eski karar sayılmaz."""
    s = _all().get(r)
    if not isinstance(s, dict) or not cancelled_at or s.get("cancelled_at") != str(cancelled_at)[:10]:
        return {}
    return s


def _put(r: str, value: dict | None) -> None:
    st = _all()
    if value is None:
        st.pop(r, None)
    else:
        st[r] = value
    if len(st) > MAX_KEYS:  # şişmesin: en eski iptaller düşer
        st = dict(sorted(st.items(), key=lambda kv: str((kv[1] or {}).get("cancelled_at") or ""))[-MAX_KEYS:])
    db.set_setting(SKEY, json.dumps(st, ensure_ascii=False) if st else None)


def match_refunds(charges: list[dict], refunds: list[dict]) -> dict[str, dict]:
    """charges [{uid, date, amount, fx?}] ↔ refunds [{uid, date, amount (eksi ya da artı)}]: çekim uid → iade."""
    used: set = set()
    out: dict[str, dict] = {}
    for c in sorted(charges, key=lambda x: (x["date"], x["uid"])):
        cd = date.fromisoformat(str(c["date"])[:10])
        for f in sorted(refunds, key=lambda x: (str(x["date"]), str(x["uid"]))):
            if f["uid"] in used:
                continue
            fd = date.fromisoformat(str(f["date"])[:10])
            amt = abs(float(f["amount"]))
            same = money.eq(amt, c["amount"]) or (c.get("fx") and abs(amt - c["amount"]) <= c["amount"] * FX_TOL)
            if same and 0 <= (fd - cd).days <= REFUND_DAYS:
                used.add(f["uid"])
                out[c["uid"]] = {"uid": f["uid"], "date": str(f["date"])[:10], "amount": money.money(amt)}
                break
    return out


def resolve(after: list[dict], st: dict, refunds: list[dict] | None = None) -> list[dict]:
    """İptal sonrası çekimlere kapanış: her biri {closed: None | "iade" | "son_cekim" | "iade_geldi", refund}."""
    hits = match_refunds(after, refunds or []) if refunds else {}
    until, why = st.get("closed_until"), st.get("closed_why") or "son_cekim"
    out = []
    for c in after:
        closed = "iade" if c["uid"] in hits else why if until and str(c["date"])[:10] <= until else None
        out.append({**c, "closed": closed, "refund": hits.get(c["uid"])})
    return out


def summary(resolved: list[dict], st: dict) -> dict:
    """Açık kalan çekimler ve uyarı biçimi: open_amount, open_count, refund_at, waiting (yalnız iade bekleniyor)."""
    open_ = [c for c in resolved if not c["closed"]]
    refund_at = st.get("refund_at")
    waiting = bool(open_ and refund_at and all(str(c["date"])[:10] <= refund_at for c in open_))
    return {"open_amount": money.money(sum(c["amount"] for c in open_)) + 0.0, "open_count": len(open_),
            "refund_at": refund_at if open_ else None, "waiting": waiting,
            "closed_count": sum(1 for c in resolved if c["closed"])}


def card_refunds(merchants: set[str]) -> dict[str, list[dict]]:
    """İptal edilmiş kart aboneliklerinin işyerlerinden gelen iade satırları (merchant_name → [{uid, date, amount}])."""
    from app.parsers.base import REFUND
    from app.textnorm import merchant_name

    out: dict[str, list[dict]] = {}
    if not merchants:
        return out
    for t in db.card_transactions():
        if t["txn_type"] != REFUND:
            continue
        m = merchant_name(t["description"])
        if m in merchants:
            out.setdefault(m, []).append({"uid": f"c{t['id']}", "date": str(t["txn_date"])[:10], "amount": float(t["amount"])})
    return out


def act(kind: str, key: str, action: str, today: date | None = None) -> dict:
    """Karar kaydet. kind card (key = merchant) | store (key = aday id). Bulunamazsa LookupError, iptal değilse ValueError."""
    from app import finance, store_subs

    today = today or date.today()
    if action not in ACTIONS:
        raise ValueError("Geçersiz işlem")
    if kind == "card":
        ac = finance.card_after_cancel(key)
        if ac is None:
            raise LookupError("Abonelik bulunamadı")
        status, cancelled_at = ac["status"], ac["cancelled_at"]
        after = [c["date"] for c in ac["charges"]]
    else:
        it = store_subs.find(key, today)
        if not it:
            raise LookupError("Abonelik bulunamadı")
        status, cancelled_at = it["status"], it["cancelled_at"]
        after = [c["date"] for c in it["charges"] if cancelled_at and c["date"] > cancelled_at]
    if status != "iptal" or not cancelled_at:
        raise ValueError("Bu abonelik iptal edilmiş görünmüyor")
    r = ref(kind, key)
    cur = dict(get(r, cancelled_at)) or {"cancelled_at": str(cancelled_at)[:10]}
    if action == "sifirla":
        _put(r, None)
        return {"ok": True}
    if action == "iade_istedim":
        cur["refund_at"] = today.isoformat()
    else:
        # iptal sonrası en son çekime kadar kapanır (yoksa bugüne): sonra gelen çekim yeniden uyarır
        cur["closed_until"] = max(str(d)[:10] for d in after) if after else today.isoformat()
        cur["closed_why"] = action
    _put(r, cur)
    return {"ok": True}
