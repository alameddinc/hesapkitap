"""Aylık özet, birleşik işlem listesi, ileriye dönük plan ve uyarılar.

Ay kavramı:
- Kart işlemleri ekstrenin kesim ayına yazılır (o ay ödenen ekstre).
- Elle girilen kalemler kendi tarihinin ayına yazılır.
"""
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from app import db
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND

import copy as _copy
import os as _os

# Hesap önbelleği: aynı veritabanı durumu için ağır hesaplar (tüm işlemler, plan, ödemeler) bir kez yapılır.
# Anahtar veritabanı dosyasının değişim zamanı + boyutu: her yazmada kendiliğinden geçersiz olur.
_CACHE: dict = {}


def _db_stamp() -> tuple:
    try:
        path = db.db_path()
        st = _os.stat(path)
        return path, st.st_mtime_ns, st.st_size
    except OSError:
        return db.db_path(), 0, 0


def _memo(fn):
    def wrap(*args, **kwargs):
        key = (fn.__name__, _db_stamp(), date.today(), repr(args), repr(sorted(kwargs.items())))
        if key not in _CACHE:
            if len(_CACHE) > 128:
                _CACHE.clear()
            _CACHE[key] = fn(*args, **kwargs)
        hit = _CACHE[key]
        # çağıran sonucu değiştirse de önbellek bozulmasın
        if isinstance(hit, list) and all(isinstance(x, dict) for x in hit):
            return [dict(x) for x in hit]
        return _copy.deepcopy(hit)
    wrap.__name__, wrap.__doc__, wrap.__wrapped__ = fn.__name__, fn.__doc__, fn
    return wrap
from app.textnorm import legal_key, merchant_name

INCOME = "gelir"
EXPENSE = "gider"
DEBT = "borc"  # borç hareketi: gelir/gider toplamına girmez, "Kalan"ı etkiler

_DEBT_STEP = {"verildi": "borç verildi", "alindi": "borç alındı", "tahsil": "tahsil edildi", "odendi": "borç ödendi"}


def _debt_row(d: dict) -> dict:
    base = {"uid": d["uid"], "source": "debt", "id": d["debt_id"], "date": d["date"], "month": d["month"],
            "merchant": d["person"], "card": None, "account": None, "bank": None, "installment": None, "fx": None,
            "note": None, "deleted": False, "debt_id": d["debt_id"], "counterparty": d["person"]}
    if d["step"] == "vazgecildi":  # tahsil edilemeyen alacak gider, silinen borç gelir; para el değiştirmedi
        kind = EXPENSE if d["direction"] == "alacak" else INCOME
        return {**base, "kind": kind, "amount": d["amount"], "category": "Borç / Alacak",
                "description": f"{d['person']} · " + ("tahsil edilemedi" if kind == EXPENSE else "borç silindi"),
                "debt_flow": d["amount"] if kind == EXPENSE else -d["amount"]}
    return {**base, "kind": DEBT, "flow": d["flow"], "amount": d["amount"], "category": "Borç / Alacak",
            "description": f"{d['person']} · {_DEBT_STEP[d['step']]}"}
CARD_SPEND = (PURCHASE, REFUND)
SUBSCRIPTION_CATEGORIES = {"Dijital Abonelik", "AI & Yazılım Abonelik"}
VARIABLE_LOOKBACK = 6  # değişken harcama tahmini için son kaç dönem (medyan: tek uç ay tahmini şişirmesin)


# ---------------------------------------------------------------- helpers

def ym(d: date | str) -> str:
    return str(d)[:7]


def current_period(today: date | None = None) -> str:
    """İçinde bulunulan dönem (ay dönümü ayarına göre)."""
    return db.period_of(today or date.today())


def add_months(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    y, m = divmod((y * 12 + m - 1) + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def month_range(start: str, end: str) -> list[str]:
    out, m = [], start
    while m <= end:
        out.append(m)
        m = add_months(m, 1)
    return out


def card_label(t: dict) -> str:
    holder = (t.get("card_holder") or "").title()
    return f"{t['card']} {holder}".strip()


def is_active(r: dict, month: str) -> bool:
    return r["start_month"] <= month and (not r["end_month"] or month <= r["end_month"])


def _round(x: float) -> float:
    return round(x + 0.0, 2)


# ---------------------------------------------------------------- unified rows

def _display_merchant(desc: str, aliases: dict | None) -> str:
    """Ekstredeki ad; fişten öğrenilmiş takma ad varsa marka (NEW TASTE GIDA → ESPRESSOLAB)."""
    m = merchant_name(desc)
    if aliases:
        return aliases.get(legal_key(desc)) or aliases.get(m) or m
    return m


def _sub_state(t: dict, states: dict) -> str | None:
    m = merchant_name(t["description"])
    if m in states:
        return states[m]["status"]
    return "aktif" if t["category"] in SUBSCRIPTION_CATEGORIES and t["txn_type"] == PURCHASE else None


def _card_row(t: dict, states: dict | None = None, aliases: dict | None = None) -> dict:
    inst = None
    if t["installment_no"] is not None and t["installments_left"] is not None:
        inst = f"{t['installment_no']}/{t['installment_no'] + t['installments_left']}"
    return {
        "uid": f"c{t['id']}", "source": "card", "id": t["id"],
        "date": t["txn_date"], "month": t["period"],
        "description": t["description"],
        "merchant": _display_merchant(t["description"], aliases),
        "amount": t["amount"], "kind": t["txn_type"], "category": t["category"],
        "card": card_label(t), "account": t["account"], "bank": t["bank"],
        "installment": inst,
        "fx": f"{t['orig_amount']:g} {t['orig_currency']}" if t["orig_currency"] else None,
        "note": t["note"], "deleted": bool(t["is_deleted"]),
        "subscription": _sub_state(t, states) if states is not None else None,
    }


def _entry_row(e: dict) -> dict:
    if e.get("debt_id"):  # borç verildi/alındı/ödendi: gelir-gider değil, sadece eldeki para
        return {**_entry_row({**e, "debt_id": None}), "kind": DEBT, "flow": 1 if e["kind"] == INCOME else -1,
                "category": "Borç / Alacak", "debt_id": e["debt_id"]}
    return {
        "uid": f"m{e['id']}", "source": "manual", "id": e["id"],
        "date": e["entry_date"], "month": e["month"],
        "description": e["description"], "merchant": e["description"],
        "amount": e["amount"], "kind": e["kind"], "category": e["category"] or "Diğer",
        "card": None, "account": None, "bank": None, "installment": None, "fx": None,
        "note": e["note"], "recurring_id": e["recurring_id"], "skipped": bool(e["skipped"]), "deleted": False,
        "payment": e["payment"], "linked_txn_id": e["linked_txn_id"], "counterparty": e.get("counterparty"), "location": e.get("location"),
        "wallet_id": e.get("wallet_id"), "account_id": e.get("account_id"), "installments": e.get("installments") or 1,
        "card_last4": e.get("card_last4"),
        # kredi kartıyla ödendi ve ekstredeki karşılığı bulundu → kart satırı sayılır, bu satır sayılmaz
        "linked": e["linked_txn_id"] is not None,
        "awaiting_statement": e["payment"] == "kredi_karti" and e["linked_txn_id"] is None and e["kind"] == EXPENSE,
    }


CARD_ESTIMATE_CATEGORY = "Kart harcaması (ekstresiz)"


def _card_payment_estimate_row(p: dict, month: str) -> dict:
    bank = re.sub(r"\s+(KATILIM\s+)?BANKASI.*$", "", p.get("account") or "", flags=re.I).strip()
    card = f"{bank} …{p['card_last4']}".strip() if p.get("card_last4") else (bank or "Kredi kartı")
    return {"uid": f"cp{p['id']}", "source": "estimate", "id": p["id"], "date": p["pay_date"], "month": month,
            "description": f"{card} ödemesi (ekstre yok, tahmini harcama)", "merchant": card, "amount": p["amount"],
            "kind": EXPENSE, "category": CARD_ESTIMATE_CATEGORY, "card": card, "account": None, "bank": bank or None,
            "installment": None, "fx": None, "note": None, "recurring_id": None, "skipped": False, "deleted": False,
            "estimated": True}


def _installment_row(e: dict, k: int, month: str, amount: float) -> dict:
    n = int(e["installments"])
    row = _entry_row(e)
    if k == 0:
        return {**row, "amount": amount, "installment": f"1/{n}", "original_amount": e["amount"]}
    return {**row, "uid": f"m{e['id']}-{k}", "month": month, "amount": amount, "installment": f"{k + 1}/{n}",
            "original_amount": e["amount"], "date": db.occurrence_date(month, int(e["entry_date"][8:10])),
            "installment_of": e["id"]}


def _pending_row(r: dict, month: str, overrides: dict | None = None) -> dict:
    return {
        "uid": f"r{r['id']}-{month}", "source": "recurring", "id": r["id"],
        "date": db.occurrence_date(month, db.day_at(r, month)), "month": month,
        "description": r["description"], "merchant": r["description"],
        "amount": db.planned_amount(r, month, overrides), "template_amount": r["amount"], "kind": r["kind"], "category": r["category"] or "Diğer",
        "card": None, "account": None, "bank": None, "installment": None, "fx": None,
        "note": r["note"], "recurring_id": r["id"], "pending": True, "deleted": False,
    }


def pending_recurring(month: str, recurring: list[dict], entries: list[dict]) -> list[dict]:
    done = {e["recurring_id"] for e in entries if e["recurring_id"] and e["month"] == month}
    ov = db.recurring_overrides()
    # geçmiş ayda maaş geliri hiç yoksa (maaşın yattığı hesabın dökümü yüklenmemiş): maaş kalemi tahmini gelir sayılır;
    # o ayın gerçek maaşı (döküm) gelince kendiliğinden kalkar
    salary_seen = any(e["kind"] == INCOME and e["category"] == "Maaş" and e["month"] == month and not e["skipped"] for e in entries)
    past = month < current_period()
    out = []
    for r in recurring:
        if not is_active(r, month) or r["id"] in done:
            continue
        row = _pending_row(r, month, ov)
        is_salary = (r["category"] or "") == "Maaş" or bool(r.get("gross_amount")) or "MAAS" in _fold(r["description"])
        if r["kind"] == INCOME and is_salary and salary_seen:  # o ayın maaşı dökümden gelmiş: "bekliyor" gösterme
            continue
        if past and r["kind"] == INCOME and is_salary and not salary_seen:
            row = {**row, "pending": False, "estimated": True, "source": "estimate",
                   "description": f"{r['description']} (tahmini, döküm yok)"}
        out.append(row)
    return out


@_memo
def transactions(month: str | None, include_deleted: bool = False) -> list[dict]:
    states, aliases = db.subscription_states(), db.merchant_aliases()
    card = [_card_row(t, states, aliases) for t in db.card_transactions(include_deleted) if not month or t["period"] == month]
    entries = db.list_entries(month)
    rows = []
    for e in entries:
        if int(e.get("installments") or 1) <= 1:
            rows.append(_entry_row(e))
    # taksitli kart harcamaları: her taksit kendi döneminde (önceki aylarda yapılanların bu aya düşen taksiti dahil)
    for e in db.installment_entries():
        for k, m, amt in db.installment_parts(e):
            if month and m != month:
                continue
            if e["linked_txn_id"] and k > 0:  # ekstreyle eşleşti: sonraki taksitler ekstrelerden gelir
                continue
            rows.append(_installment_row(e, k, m, amt))
    # ekstresi olmayan dönemlerin kart ödemesi: o dönemin kart harcaması yerine ödeme tutarı (tahmini) sayılır;
    # ekstre yüklenince kendiliğinden kalkar
    for p in db.card_payments_without_statement():
        pm = db.period_of(p["pay_date"])
        if not month or pm == month:
            rows.append(_card_payment_estimate_row(p, pm))
    out = _apply_splits(card + rows) + [_debt_row(d) for d in db.debt_rows(month)]
    if month:
        out += pending_recurring(month, db.list_recurring(), entries)
    else:  # tüm aylar (trend, devreden): dökümü olmayan geçmiş aylardaki maaş tahminleri de girsin
        cur = current_period()
        recs = db.list_recurring()
        by_m: dict[str, list] = defaultdict(list)
        for e in entries:
            by_m[e["month"]].append(e)
        starts = [r["start_month"] for r in recs if r["kind"] == INCOME]
        m = min(starts) if starts else cur
        while m < cur:
            out += [r for r in pending_recurring(m, recs, by_m.get(m, [])) if r.get("estimated")]
            m = add_months(m, 1)
    return sorted(out, key=lambda r: (r["date"], r["uid"]), reverse=True)


def _apply_splits(rows: list[dict]) -> list[dict]:
    """Bölünmüş harcama: ana satır kalan tutarla, her alt kalem kendi kategorisinde ayrı satır.
    Alt kalem ana satırın tarih/kart/eşleşme bilgilerini taşır; toplamlar ve kategoriler doğal olarak doğru çıkar."""
    sm = db.splits_map()
    if not sm:
        return rows
    out = []
    for r in rows:
        key = ("card", r["id"]) if r["source"] == "card" else ("entry", r["id"]) if r["source"] == "manual" else None
        parts = sm.get(key) if key and r["kind"] in (PURCHASE, EXPENSE) else None
        if not parts:
            out.append(r)
            continue
        used = sum(p["amount"] for p in parts)
        out.append({**r, "amount": _round(r["amount"] - used), "split_total": _round(used), "original_amount": r["amount"]})
        for p in parts:
            child = {**r, "uid": f"{r['uid']}s{p['id']}", "amount": p["amount"], "category": p["category"],
                     "description": f"{r['description']} · {p['description'] or p['category']}",
                     "split_of": r["uid"], "split_origin": p["origin"]}
            if p.get("debt_id"):  # ortak harcamada başkasının payı: gider değil, ona alacak (para senden çıktı)
                child.update(kind=DEBT, flow=-1, debt_id=p["debt_id"], counterparty=p.get("person"),
                             description=f"{r['description']} · {p.get('person')} payı")
            out.append(child)
    return out


# ---------------------------------------------------------------- monthly summary

def _totals(rows: list[dict]) -> dict:
    live = [r for r in rows if not r.get("deleted") and not r.get("skipped") and not r.get("pending") and not r.get("linked")]
    income = sum(r["amount"] for r in live if r["kind"] == INCOME)
    card_spend = sum(r["amount"] for r in live if r["kind"] in CARD_SPEND)
    fees = sum(r["amount"] for r in live if r["kind"] == FEE)
    manual = sum(r["amount"] for r in live if r["kind"] == EXPENSE)
    refunds = -sum(r["amount"] for r in live if r["kind"] == REFUND)
    expense = card_spend + fees + manual
    # borç verip almak gelir/gider değil ama eldeki parayı değiştirir
    debt_flow = sum(r["flow"] * r["amount"] for r in live if r["kind"] == DEBT) + sum(r.get("debt_flow", 0) for r in live)
    return {k: _round(v) for k, v in dict(income=income, card_spend=card_spend, fees=fees, manual_expense=manual,
                                          refunds=refunds, expense=expense, net=income - expense, debt_flow=debt_flow,
                                          cash_net=income - expense + debt_flow).items()}


def _group(rows: list[dict], key: str) -> list[dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for r in rows:
        agg[r[key]]["amount"] += r["amount"]
        agg[r[key]]["count"] += 1
    out = [{"name": k, "amount": _round(v["amount"]), "count": v["count"]} for k, v in agg.items() if v["amount"] > 0.005]
    return sorted(out, key=lambda x: -x["amount"])


def reference_month(today: date | None = None) -> str:
    """Kıyas/öneri için 'son tam ay': en son yüklenen ekstrenin ayı (yoksa geçen ay)."""
    periods = [st["period"] for st in db.list_statements()]
    return max(periods) if periods else add_months(ym(today or date.today()), -1)


def available_months() -> list[str]:
    ms = {t["period"] for t in db.card_transactions()} | {e["month"] for e in db.list_entries()}
    ms.add(current_period())
    return sorted(ms, reverse=True)


def summary(month: str, today: date | None = None) -> dict:
    rows = transactions(month)
    live = [r for r in rows if not r.get("skipped") and not r.get("pending") and not r.get("linked")]
    expenses = [r for r in live if r["kind"] in (*CARD_SPEND, FEE, EXPENSE)]
    spend = [r for r in live if r["kind"] in CARD_SPEND]

    statements = [s for s in db.list_statements() if s["period"] == month]
    pending = [r for r in rows if r.get("pending")]

    # Son 12 ay trend (gelir vs gider)
    all_rows = transactions(None)
    by_month: dict[str, list] = defaultdict(list)
    for r in all_rows:
        by_month[r["month"]].append(r)
    trend_months = sorted(by_month)[-12:]
    trend = [{"month": m, **{k: v for k, v in _totals(by_month[m]).items() if k in ("income", "expense", "net")}}
             for m in trend_months]

    # Önceki dönemlerden artan para bu döneme devreder (eksi devretmez: açık, kart borcu olarak zaten görünür)
    carry = 0.0
    for m in sorted(by_month):
        if m >= month:
            break
        carry = max(0.0, carry + _totals(by_month[m])["cash_net"])
    totals = _totals(rows)
    totals["carry_in"] = _round(carry)
    totals["balance"] = _round(carry + totals["cash_net"])
    open_try = [d for d in db.list_debts(open_only=True) if d["asset"] == "TRY"]
    totals["receivable"] = _round(sum(d["remaining"] for d in open_try if d["direction"] == "alacak"))
    totals["payable"] = _round(sum(d["remaining"] for d in open_try if d["direction"] == "borc"))

    by_category = _group(expenses, "category")
    return {
        "month": month,
        "totals": totals,
        "budgets": budgets(month, by_category),
        "pending": {
            "income": _round(sum(r["amount"] for r in pending if r["kind"] == INCOME)),
            "expense": _round(sum(r["amount"] for r in pending if r["kind"] == EXPENSE)),
            "count": len(pending),
        },
        "by_category": by_category,
        "by_merchant": _group(spend + [r for r in live if r["kind"] == EXPENSE], "merchant")[:15],
        "by_card": _group(spend, "card"),
        "statements": statements,
        "trend": trend,
        "insights": insights(month, today=today),
    }


# ---------------------------------------------------------------- tasarruf: abonelikler + kısma hedefleri

def subscriptions() -> list[dict]:
    """Abonelik kategorisindeki işyerleri: son tutar, görüldüğü aylar, iptal durumu, iptalden sonra çekim var mı."""
    states = db.subscription_states()
    by_m: dict[str, list] = defaultdict(list)
    for t in db.card_transactions():
        if t["txn_type"] == PURCHASE and t["installment_no"] is None and (
                t["category"] in SUBSCRIPTION_CATEGORIES or merchant_name(t["description"]) in states):
            by_m[merchant_name(t["description"])].append(t)
    out = []
    for m, ts in by_m.items():
        last_period = max(t["period"] for t in ts)
        st = states.get(m)
        after = [t for t in ts if st and t["txn_date"] > st["changed_at"]]
        monthly = _round(sum(t["amount"] for t in ts if t["period"] == last_period))
        new_amount = st["new_amount"] if st and st["status"] == "dusuruldu" else None
        # paket düşürüldükten sonraki dönemlerde yeni tutarın %15 fazlasından çok çekildiyse: düşürme tutmamış
        over = 0.0
        if new_amount is not None:
            per: dict[str, float] = defaultdict(float)
            for t in after:
                per[t["period"]] += t["amount"]
            over = _round(sum(v - new_amount for v in per.values() if v > new_amount * 1.15))
        out.append({
            "merchant": m, "category": ts[0]["category"],
            "monthly": monthly, "new_amount": new_amount,
            "saving": _round(monthly if st and st["status"] == "iptal" else max(0.0, monthly - new_amount) if new_amount is not None else 0),
            "overcharged": over,
            "last_seen": max(t["txn_date"] for t in ts), "months": len({t["period"] for t in ts}),
            "fx": next((f"{t['orig_currency']}" for t in ts if t["orig_currency"]), None),
            "status": st["status"] if st else "aktif", "cancelled_at": st["changed_at"] if st else None,
            "charged_after_cancel": _round(sum(t["amount"] for t in after)) if after and st["status"] == "iptal" else 0,
        })
    return sorted(out, key=lambda x: (x["status"] != "aktif", -x["monthly"]))


def _spend_for(kind: str, name: str, month: str) -> float:
    rows = [r for r in transactions(month) if not r.get("pending") and not r.get("skipped") and not r.get("linked")
            and not r.get("deleted") and r["kind"] in (*CARD_SPEND, EXPENSE)]
    key = "category" if kind == "category" else "merchant"
    return _round(sum(r["amount"] for r in rows if r[key] == name))


def goals_review(today: date | None = None) -> list[dict]:
    """Her hedef için başlangıçtan bu aya kadar ay ay sonuç. Bu ay 'devam' (henüz bitmedi)."""
    today = today or date.today()
    now = current_period(today)
    out = []
    for g in db.list_goals():
        months = []
        if g["start_month"] <= now:
            for m in month_range(g["start_month"], now):
                spent = _spend_for(g["kind"], g["name"], m)
                status = "devam" if m == now else ("tuttu" if spent <= g["target"] else "tutmadi")
                months.append({"month": m, "spent": spent, "status": status, "saved": _round(g["baseline"] - spent)})
        out.append({**g, "monthly_saving": _round(max(0, g["baseline"] - g["target"])), "months": months})
    return out


# ---------------------------------------------------------------- bütçeler

def budgets(month: str, by_category: list[dict] | None = None) -> list[dict]:
    limits = db.list_budgets()
    if not limits:
        return []
    if by_category is None:
        live = [r for r in transactions(month) if not r.get("pending") and not r.get("skipped") and not r.get("linked")]
        by_category = _group([r for r in live if r["kind"] in (*CARD_SPEND, FEE, EXPENSE)], "category")
    spent = {g["name"]: g["amount"] for g in by_category}
    out = []
    for cat, limit in limits.items():
        s = spent.get(cat, 0.0)
        out.append({"category": cat, "limit": limit, "spent": _round(s), "remaining": _round(limit - s),
                    "pct": round(s / limit * 100, 1)})
    return sorted(out, key=lambda b: -b["pct"])


# ---------------------------------------------------------------- planning

def _latest_statement_per_account() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for s in db.list_statements():
        if s["card_no"] not in latest or s["period_end"] > latest[s["card_no"]]["period_end"]:
            latest[s["card_no"]] = s
    return latest


def card_projection() -> dict:
    """Hesap bazında: kalan taksitler, abonelik tahmini, değişken harcama ortalaması."""
    txns = db.card_transactions()
    latest = _latest_statement_per_account()
    by_acc_period: dict[tuple, list] = defaultdict(list)
    for t in txns:
        by_acc_period[(t["account"], t["period"])].append(t)

    out = {}
    for acc, st in latest.items():
        period = st["period"]
        cur = by_acc_period[(acc, period)]

        installments = []  # (ay, tutar, açıklama, taksit)
        for t in cur:
            if t["installments_left"] and t["installment_left_amount"] and t["txn_type"] in (*CARD_SPEND, FEE):
                sign = -1 if t["txn_type"] == REFUND else 1
                total_n = t["installment_no"] + t["installments_left"]
                for i in range(1, t["installments_left"] + 1):
                    installments.append({
                        "month": add_months(period, i), "amount": sign * t["installment_left_amount"],
                        "description": t["description"], "installment": f"{t['installment_no'] + i}/{total_n}",
                        "card": card_label(t), "fee": t["txn_type"] == FEE,
                    })

        subs: dict[str, dict] = {}
        states = db.subscription_states()
        cancelled = {m for m, st in states.items() if st["status"] == "iptal"}
        for t in cur:
            if t["category"] in SUBSCRIPTION_CATEGORIES and t["txn_type"] == PURCHASE and t["installment_no"] is None:
                m = merchant_name(t["description"])
                if m in cancelled:
                    continue
                subs.setdefault(m, {"description": m, "amount": 0.0, "category": t["category"]})
                subs[m]["amount"] += t["amount"]

        # Değişken = toplam kart harcaması - taksitli satırlar - abonelikler; son N dönem ortalaması
        periods = sorted({p for (a, p) in by_acc_period if a == acc})[-VARIABLE_LOOKBACK:]
        variable = []
        for p in periods:
            v = sum(t["amount"] for t in by_acc_period[(acc, p)]
                    if t["txn_type"] in CARD_SPEND and t["installment_no"] is None
                    and t["category"] not in SUBSCRIPTION_CATEGORIES)
            variable.append(v)

        for m, sub in subs.items():
            if states.get(m, {}).get("status") == "dusuruldu" and states[m]["new_amount"] is not None:
                sub["amount"] = states[m]["new_amount"]
                sub["description"] = f"{m} (yeni paket)"

        out[acc] = {
            "account": acc, "bank": st["bank"], "latest_period": period,
            "next_due_date": st["next_due_date"], "next_period_end": st["next_period_end"],
            "installments": installments,
            "subscriptions": [{**s, "amount": _round(s["amount"])} for s in subs.values()],
            "variable_avg": _round(median(variable)) if variable else 0.0,
            "variable_periods": len(variable),
        }
    return out


@_memo
def _next_cut(st: dict) -> tuple[date, date | None]:
    """Son ekstreden sonraki kesim ve son ödeme günü (ekstrede yazmıyorsa bir ay sonrası)."""
    pe = date.fromisoformat(st["period_end"])
    if st.get("next_period_end"):
        cut = date.fromisoformat(st["next_period_end"])
    else:
        y, m = divmod(pe.year * 12 + pe.month, 12)
        cut = pe.replace(year=y, month=m + 1, day=min(pe.day, 28))
    due = st.get("next_due_date")
    if due:
        return cut, date.fromisoformat(due)
    if st.get("due_date"):
        return cut, cut + (date.fromisoformat(st["due_date"]) - pe)
    return cut, None


def open_cycles(today: date | None = None, proj: dict | None = None, entries: list[dict] | None = None) -> dict:
    """Kesilmemiş ekstre, kart başına: bugüne kadar ne birikti, kesimde tahminen ne olur.

    Bugüne kadar = son ekstreden devreden taksitler + abonelikler + son kesimden beri elle/fişle girilen kart harcamaları.
    Kesimde = taksit + abonelik + değişken harcama; değişken için geçmiş dönemlerin medyanı ile "şu ana kadarki +
    kalan günlerin payı"ndan büyüğü alınır (harcama girilmese de tahmin geçmişin altına düşmez).
    Hangi karttan ödendiği belli olmayan harcamalar en çok kullanılan karta yazılır ve ayrıca gösterilir.
    """
    today = today or date.today()
    proj = card_projection() if proj is None else proj
    entries = db.list_entries() if entries is None else entries
    latest = _latest_statement_per_account()
    out: dict[str, dict] = {}
    for acc, p in proj.items():
        st = latest[acc]
        start = date.fromisoformat(st["period_end"])
        cut, due = _next_cut(st)
        if (due or cut + timedelta(days=10)) < today:  # son ödemesi de geçmiş: o ekstre yüklenmemiş, tahmin anlamsız
            continue
        nxt = add_months(p["latest_period"], 1)
        inst = sum(i["amount"] for i in p["installments"] if i["month"] == nxt)
        subs = sum(x["amount"] for x in p["subscriptions"])
        out[acc] = {"account": acc, "bank": p["bank"], "start": start.isoformat(), "cut": cut.isoformat(),
                    "due": due.isoformat() if due else None, "days_left": max(0, (cut - today).days),
                    "days_total": max(1, (cut - start).days), "installments": _round(inst), "subscriptions": _round(subs),
                    "spent": 0.0, "unassigned": 0.0, "entries": 0, "variable_avg": p["variable_avg"]}
    if not out:
        return {}
    main = max(out.values(), key=lambda c: c["variable_avg"])["account"]
    aliases = db.card_aliases()
    for e in entries:
        if e["payment"] != "kredi_karti" or e["kind"] != EXPENSE or e["linked_txn_id"] or e["skipped"]:
            continue
        last4 = e.get("card_last4")
        acc = aliases.get(last4) if last4 else None
        acc = acc if acc in out else None
        c = out.get(acc or main)
        if not c or not (c["start"] < e["entry_date"] <= today.isoformat()):
            continue
        amt = e["amount"] / max(1, int(e.get("installments") or 1))  # taksitliyse bu ekstreye ilk taksit düşer
        c["spent"] += amt
        c["entries"] += 1
        if not acc:
            c["unassigned"] += amt
    for c in out.values():
        pace = c["variable_avg"] * c["days_left"] / c["days_total"]
        c["variable_at_cut"] = _round(max(c["variable_avg"], c["spent"] + pace))
        c["spent"], c["unassigned"] = _round(c["spent"]), _round(c["unassigned"])
        c["so_far"] = _round(c["installments"] + c["subscriptions"] + c["spent"])
        c["at_cut"] = _round(c["installments"] + c["subscriptions"] + c["variable_at_cut"])
    return out


def recurring_suggestions(today: date | None = None) -> list[dict]:
    """Hareketlerde her ay tekrarlayan ama sabit kalem olmayan ödemeler (kira, aidat, kreş, kira geliri…).

    Kural: son verili aya kadar son 4 ayın en az 3'ünde var, son 3 ayın aylık toplamları birbirine %25'ten yakın.
    Tutar = son ayın toplamı (kira artışı ve ikiye bölünmüş ödeme doğru çıkar), gün = ödeme günlerinin medyanı.
    """
    today = today or date.today()
    entries = [e for e in db.list_entries() if not e["skipped"] and e["amount"] > 0 and not e.get("debt_id")
               and e["payment"] not in ("kredi_karti", "kart") and e["entry_date"] <= today.isoformat()
               and e["category"] not in ("Nakit Çekim", "Transfer")]  # ATM'den çekilen para sabit kalem değil
    recs = db.list_recurring()
    taken = {r["match_key"] for r in recs if r.get("match_key")}
    names = {(r["kind"], r["description"].strip().casefold()) for r in recs}
    dismissed = set(_json_setting("recurring_dismissed", []))
    groups: dict[tuple, list] = defaultdict(list)
    for e in entries:
        groups[(e["kind"], db.recurring_key(e["description"], e["counterparty"]))].append(e)
    out = []
    for (kind, key), es in groups.items():
        if key in ("p:", "m:") or key in taken or f"{kind}:{key}" in dismissed or any(e["recurring_id"] for e in es):
            continue
        if any((kind, e["description"].strip().casefold()) in names for e in es):
            continue
        by_month: dict[str, float] = defaultdict(float)
        for e in es:
            by_month[e["entry_date"][:7]] += e["amount"]
        last = max(by_month)
        if last < add_months(ym(today), -2):  # artık ödenmiyor
            continue
        window = [add_months(last, -i) for i in range(4)]
        if sum(m in by_month for m in window) < 3:
            continue
        recent = [by_month[m] for m in window[:3] if m in by_month]
        if min(recent) < 100 or max(recent) > min(recent) * 1.25:
            continue
        days = sorted(int(e["entry_date"][8:10]) for e in es if e["entry_date"][:7] in window)
        cats = [e["category"] for e in es if e["category"] and e["category"] != "Kategorisiz"]
        cat = max(set(cats), key=cats.count) if cats else None
        accs = [e["account_id"] for e in es if e["account_id"]]
        who = next((e["counterparty"] for e in reversed(es) if e["counterparty"]), None)
        label = (f"{cat.split(' & ')[0]} ({who})" if cat and who else who or cat or es[-1]["description"])[:60]
        out.append({"key": f"{kind}:{key}", "kind": kind, "description": label, "category": cat,
                    "amount": _round(by_month[last]), "day_of_month": days[len(days) // 2], "months": len(by_month),
                    "first_month": min(by_month), "last_month": last, "account_id": max(set(accs), key=accs.count) if accs else None,
                    "history": [{"month": m, "amount": _round(by_month[m])} for m in sorted(by_month)[-6:]]})
    return sorted(out, key=lambda x: -x["amount"])


def recurring_drift(today: date | None = None) -> list[dict]:
    """Sabit kalemin son iki gerçekleşen ayı aynı yeni tutarla geldiyse (kira artışı): "güncelleyeyim mi?" önerisi."""
    today = today or date.today()
    entries = db.list_entries()
    out = []
    for r in db.list_recurring():
        if r.get("gross_amount") or (r["end_month"] and r["end_month"] < ym(today)):
            continue
        by_month: dict[str, float] = defaultdict(float)
        for e in entries:
            if e["recurring_id"] == r["id"] and not e["skipped"]:
                by_month[e["month"]] += e["amount"]
        last2 = sorted(by_month)[-2:]
        if len(last2) < 2 or add_months(last2[0], 1) != last2[1]:
            continue
        a, b = by_month[last2[0]], by_month[last2[1]]
        if abs(a - b) > max(1.0, b * 0.01):
            continue
        planned = db.amount_at(r, add_months(last2[1], 1))
        if abs(b - planned) <= planned * 0.03:
            continue
        prev = [m for m in sorted(by_month) if abs(by_month[m] - b) > max(1.0, b * 0.01)]
        start = next((m for m in sorted(by_month) if m > (prev[-1] if prev else "")), last2[0])
        out.append({"recurring_id": r["id"], "description": r["description"], "kind": r["kind"], "old": _round(planned),
                    "new": _round(b), "from_month": start})
    return out


def accept_recurring_suggestion(key: str, description: str | None = None, amount: float | None = None) -> int:
    """Öneriyi sabit kaleme çevir; geçmiş hareketleri ona bağla (her ay bir kayıt), yenileri match_key ile bağlanır."""
    s = next((x for x in recurring_suggestions() if x["key"] == key), None)
    if not s:
        raise KeyError(key)
    kind, mkey = key.split(":", 1)
    rid = db.create_recurring({"description": (description or s["description"]).strip(), "amount": amount or s["amount"],
                               "kind": kind, "category": s["category"], "day_of_month": s["day_of_month"],
                               "start_month": s["first_month"], "account_id": s["account_id"], "match_key": mkey})
    seen = set()
    with db.connect() as c:
        for e in sorted(db.list_entries(), key=lambda e: (e["entry_date"], -e["amount"])):
            if e["kind"] != kind or e["recurring_id"] or e["skipped"] or e["payment"] in ("kredi_karti", "kart"):
                continue
            if db.recurring_key(e["description"], e["counterparty"]) != mkey or e["month"] in seen:
                continue
            seen.add(e["month"])
            c.execute("UPDATE entries SET recurring_id = ? WHERE id = ?", (rid, e["id"]))
    return rid


def dismiss_recurring_suggestion(key: str) -> None:
    keys = _json_setting("recurring_dismissed", [])
    if key not in keys:
        db.set_setting("recurring_dismissed", __import__("json").dumps([*keys, key]))


def _json_setting(key: str, default):
    import json

    v = db.get_setting(key)
    return json.loads(v) if v else default


def plan(months: int = 6, today: date | None = None) -> dict:
    today = today or date.today()
    start = current_period(today)
    span = [add_months(start, i) for i in range(months)]
    proj = card_projection()
    statements = db.list_statements()
    recurring = [{**r, "amount_now": db.planned_amount(r, start)} for r in db.list_recurring()]
    entries = db.list_entries()
    cycles = open_cycles(today, proj, entries)
    card_rows = db.card_transactions()
    bills = db.list_bills()
    goals = db.list_goals(active_only=True)
    overrides = db.recurring_overrides()

    result = []
    cumulative = cumulative_est = 0.0
    for m in span:
        lines = []
        # --- kart
        for acc, p in proj.items():
            st = next((s for s in statements if s["card_no"] == acc and s["period"] == m), None)
            if st:
                actual = sum(t["amount"] for t in card_rows
                             if t["account"] == acc and t["period"] == m and t["txn_type"] in (*CARD_SPEND, FEE))
                lines.append({"type": "kart_gercek", "kind": EXPENSE, "amount": _round(actual),
                              "description": f"{p['bank']} {acc} ekstresi (gerçek)", "due_date": st["due_date"]})
                continue
            if m <= p["latest_period"]:
                continue  # geçmiş ay, ekstresi yüklenmemiş
            for i in p["installments"]:
                if i["month"] == m:
                    lines.append({"type": "taksit", "kind": EXPENSE, "amount": _round(i["amount"]),
                                  "description": f"{i['description']} ({i['installment']})", "account": acc})
            for s in p["subscriptions"]:
                lines.append({"type": "abonelik", "kind": EXPENSE, "amount": s["amount"],
                              "description": s["description"], "account": acc})
            cyc = cycles.get(acc)
            if cyc and ym(cyc["cut"]) == m:
                # açık dönem: elle/fişle girilenler zaten tek seferlik satır; burada sadece kalan tahmini pay
                rest = _round(max(0.0, cyc["variable_at_cut"] - cyc["spent"]))
                if rest > 0:
                    lines.append({"type": "degisken", "kind": EXPENSE, "amount": rest, "account": acc,
                                  "description": f"{acc} kesime kadar tahmini kart harcaması"
                                                 + (f" (girilen {_round(cyc['spent']):,.0f} ₺ hariç)".replace(",", ".") if cyc["spent"] else "")})
            elif p["variable_avg"] > 0:
                lines.append({"type": "degisken", "kind": EXPENSE, "amount": p["variable_avg"],
                              "description": f"{acc} tahmini kart harcaması (son {p['variable_periods']} dönem medyanı)",
                              "account": acc})
        # --- kısma hedefleri: ekstresi olmayan aylarda değişken kart tahmini hedef kadar azalır
        saving = sum(g["baseline"] - g["target"] for g in goals if g["start_month"] <= m and g["baseline"] > g["target"])
        var_lines = [l for l in lines if l["type"] == "degisken"]
        var_total = sum(l["amount"] for l in var_lines)
        if saving and var_total:
            cut = min(saving, var_total)
            for l in var_lines:
                l["amount"] = _round(l["amount"] * (var_total - cut) / var_total)
                l["description"] += f" − kısma hedefleri"
            lines.append({"type": "tasarruf_notu", "kind": "bilgi", "amount": _round(cut),
                          "description": f"Kısma hedefleriyle {_round(cut):,.0f} ₺ daha az harcama varsayıldı".replace(",", ".")})

        # --- tekrarlayan
        for r in recurring:
            if not is_active(r, m):
                continue
            e = next((e for e in entries if e["recurring_id"] == r["id"] and e["month"] == m), None)
            if e and e["skipped"]:
                continue
            planned = db.planned_amount(r, m, overrides)
            lines.append({"type": "sabit", "kind": r["kind"], "amount": e["amount"] if e else planned,
                          "description": r["description"], "recurring_id": r["id"], "confirmed": bool(e),
                          "custom": not e and planned != r["amount"], "template_amount": r["amount"], "month": m})
        # --- tek seferlik elle girilenler
        for e in entries:
            if e["recurring_id"] or e["skipped"] or e["linked_txn_id"] or e.get("debt_id"):
                continue
            for k, pm, amt in db.installment_parts(e):  # taksitliyse her taksit kendi ayında
                if pm == m:
                    n = int(e.get("installments") or 1)
                    lines.append({"type": "tek_seferlik", "kind": e["kind"], "amount": amt, "entry_id": e["id"],
                                  "description": e["description"] + (f" ({k + 1}/{n} taksit)" if n > 1 else "")})

        # --- ödenmemiş faturalar (ödenenler zaten tek seferlik gider olarak girdi)
        for b in bills:
            if db.period_of(b["due_date"]) == m and b["paid_at"] is None:
                lines.append({"type": "fatura", "kind": EXPENSE, "amount": b["amount"],
                              "description": f"{b['provider']} faturası (son {date.fromisoformat(b['due_date']):%d.%m})"})

        # Kesin: gelir, taksit, abonelik, sabit, fatura, gerçek ekstre. Tahmini: değişken kart harcaması (bilgi amaçlı)
        income = sum(l["amount"] for l in lines if l["kind"] == INCOME)
        estimated = sum(l["amount"] for l in lines if l["kind"] == EXPENSE and l["type"] == "degisken")
        expense = sum(l["amount"] for l in lines if l["kind"] == EXPENSE and l["type"] != "degisken")
        cumulative += income - expense
        cumulative_est += income - expense - estimated
        groups = defaultdict(float)
        for l in lines:
            if l["kind"] in (EXPENSE, INCOME):
                groups[l["type"] if l["kind"] == EXPENSE else "gelir"] += l["amount"]
        result.append({
            "month": m, "income": _round(income), "expense": _round(expense), "estimated": _round(estimated),
            "net": _round(income - expense), "net_with_estimate": _round(income - expense - estimated),
            "cumulative": _round(cumulative), "cumulative_with_estimate": _round(cumulative_est),
            "groups": {k: _round(v) for k, v in groups.items()},
            "lines": sorted(lines, key=lambda l: (l["kind"] != INCOME, l["kind"] == "bilgi", -l["amount"])),
        })

    dues = []
    for s in statements:
        if s["due_date"] and s["due_date"] >= today.isoformat() and not (
                s["paid_amount"] is not None and s["paid_amount"] >= s["total_debt"] - 0.01):
            dues.append({"date": s["due_date"], "account": s["card_no"], "bank": s["bank"],
                         "amount": s["total_debt"], "min_payment": s["min_payment"], "estimated": False})
    for acc, c in cycles.items():
        if c["due"] and c["due"] >= today.isoformat():
            dues.append({"date": c["due"], "account": acc, "bank": c["bank"], "amount": c["at_cut"],
                         "min_payment": None, "estimated": True, "cycle": c})

    return {"months": result, "dues": sorted(dues, key=lambda d: d["date"]),
            "projection": list(proj.values()), "recurring": recurring}


# ---------------------------------------------------------------- ödemeler (ekstre + fatura)

PAYABLE_WINDOW = 35  # ödenmiş kalemler kaç gün listede kalsın


def _status(amount: float, paid: float | None, due: str, today: date) -> str:
    if paid is not None and paid >= amount - 0.01:
        return "odendi"
    if paid:
        return "kismi"
    return "gecikti" if date.fromisoformat(due) < today else "bekliyor"


@_memo
def payables(today: date | None = None) -> list[dict]:
    """Ödenecekler: kart ekstreleri, faturalar ve henüz kesilmemiş ekstrelerin tahmini."""
    today = today or date.today()
    out = []
    statements = db.list_statements()
    for st in statements:
        if not st["due_date"]:
            continue
        status = _status(st["total_debt"], st["paid_amount"], st["due_date"], today)
        age = (today - date.fromisoformat(st["due_date"])).days
        if status == "odendi" and age > PAYABLE_WINDOW:
            continue
        if status != "odendi" and age > 120:  # çok eski ve işaretlenmemiş: muhtemelen ödenmiş, listeyi kirletmesin
            continue
        out.append({"type": "statement", "id": st["id"], "title": f"{st['bank']} {st['card_no']}",
                    "subtitle": f"Ekstre · kesim {date.fromisoformat(st['period_end']):%d.%m}",
                    "amount": st["total_debt"], "min_payment": st["min_payment"], "due_date": st["due_date"],
                    "days_left": -age, "paid_amount": st["paid_amount"], "paid_at": st["paid_at"], "status": status})
    for b in db.list_bills():
        status = _status(b["amount"], b["paid_amount"], b["due_date"], today)
        if status == "odendi" and b["paid_at"] and (today - date.fromisoformat(b["paid_at"])).days > PAYABLE_WINDOW:
            continue
        out.append({"type": "bill", "id": b["id"], "title": b["provider"],
                    "subtitle": " · ".join(x for x in ("Fatura", b["category"], b["description"]) if x),
                    "amount": b["amount"], "min_payment": None, "due_date": b["due_date"],
                    "days_left": (date.fromisoformat(b["due_date"]) - today).days,
                    "paid_amount": b["paid_amount"], "paid_at": b["paid_at"], "status": status,
                    "has_file": bool(b["file_name"]), "category": b["category"], "note": b["note"],
                    "description": b["description"]})
    # Vadeli borçlarım (kişilere): TL ise tutar, değilse birimiyle
    for d in db.list_debts():
        if d["direction"] != "borc" or not d["due_date"] or d["asset"] != "TRY":  # döviz/altın: Transferler + hatırlatma
            continue
        if d["settled_at"] and (today - date.fromisoformat(d["settled_at"])).days > PAYABLE_WINDOW:
            continue
        status = "odendi" if d["settled_at"] else _status(d["amount"], d["paid_amount"] or None, d["due_date"], today)
        out.append({"type": "debt", "id": d["id"], "title": f"{d['person']} (borç)",
                    "subtitle": " · ".join(x for x in ("Borç", d["note"]) if x),
                    "amount": d["amount"], "min_payment": None, "due_date": d["due_date"],
                    "days_left": (date.fromisoformat(d["due_date"]) - today).days,
                    "paid_amount": d["paid_amount"] or None, "paid_at": d["settled_at"], "status": status})
    # Kesilmemiş ekstreler: tahmini tutar, ödeme butonu yok
    for d in plan(3, today=today)["dues"]:
        if d["estimated"] and (date.fromisoformat(d["date"]) - today).days <= 45:
            out.append({"type": "estimate", "id": None, "title": f"{d['bank']} {d['account']}",
                        "subtitle": "Sonraki ekstre (tahmini)", "amount": d["amount"] or 0, "min_payment": None,
                        "due_date": d["date"], "days_left": (date.fromisoformat(d["date"]) - today).days,
                        "paid_amount": None, "paid_at": None, "status": "tahmini", "cycle": d.get("cycle")})
    order = {"gecikti": 0, "kismi": 1, "bekliyor": 2, "tahmini": 3, "odendi": 4}
    return sorted(out, key=lambda x: (order[x["status"]], x["due_date"] if x["status"] != "odendi" else "~" + (x["paid_at"] or "")))


# ---------------------------------------------------------------- birimler (borç/alacak: TL, döviz, altın)

ASSETS = {"TRY": "TL", "USD": "Dolar", "EUR": "Euro", "GBP": "Sterlin", "GRAM_ALTIN": "gram altın",
          "CEYREK_ALTIN": "çeyrek altın", "YARIM_ALTIN": "yarım altın", "TAM_ALTIN": "tam altın",
          "CUMHURIYET_ALTIN": "Cumhuriyet altını"}


def asset_label(asset: str) -> str:
    return ASSETS.get(asset, asset)


def fmt_asset(x: float, asset: str) -> str:
    if asset == "TRY":
        return _tl(x)
    n = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").removesuffix(",00")
    return f"{n} {asset_label(asset)}"


# ---------------------------------------------------------------- insights

def _tl(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " ₺"


def _fold(s: str) -> str:
    from app.categorize import fold_key

    return fold_key(s)


def interest_alarm(month: str) -> list[dict]:
    """Faiz alarmı: bu dönem ödenen faiz / vade farkı ve NEDENİ (geç ödeme, eksik ödeme, taksit vade farkı)."""
    out = []
    for st in db.interest_charges(month):
        card = f"{st['bank']} …{st['card_no'][-4:]}"
        total = round(sum(x["amount"] for x in st["lines"]), 2)
        if total <= 0:
            continue
        keys = [(x, _fold(x["description"])) for x in st["lines"]]
        late = sum(x["amount"] for x, k in keys if "GECIKME" in k)
        shop = sum(x["amount"] for x, k in keys if "ALISVERIS" in k or "AKDI" in k)
        vade = [(x["description"].split("-", 1)[-1].strip(), x["amount"]) for x, k in keys if "VADEFARK" in k]
        reasons = []
        prev, pays = st["prev"], st["payments"]
        if prev and prev["due_date"] and (late or shop):
            paid = round(sum(p["amount"] for p in pays), 2)
            on_time = round(sum(p["amount"] for p in pays if p["pay_date"] <= prev["due_date"]), 2)
            due = date.fromisoformat(prev["due_date"])
            if pays and on_time < (prev["total_debt"] or 0) - 1:
                first_late = next((p for p in pays if p["pay_date"] > prev["due_date"]), None)
                if first_late:
                    days = (date.fromisoformat(first_late["pay_date"]) - due).days
                    reasons.append(f"önceki ekstrenin son ödemesi {due:%d.%m}, ödeme {date.fromisoformat(first_late['pay_date']):%d.%m}: "
                                   f"{days} gün geç. Bir gün geç ödemek bile dönem borcunun tamamına faiz işletir")
                elif paid < (prev["total_debt"] or 0) - 1:
                    reasons.append(f"önceki dönem borcunun {_tl(paid)}'si ödendi, kalan {_tl(prev['total_debt'] - paid)}'ye faiz işledi")
            elif not pays:
                reasons.append(f"önceki ekstreye ({_tl(prev['total_debt'] or 0)}, son ödeme {due:%d.%m}) ödeme görünmüyor")
        if vade:
            reasons.append("taksitli işlemlere vade farkı eklendi: " + ", ".join(f"{m} {_tl(a)}" for m, a in vade[:5])
                           + ". Taksit yerine tek çekim seçersen vade farkı ödemezsin")
        out.append({"level": "kritik", "title": f"Faiz alarmı: {card} bu dönem {_tl(total)} faiz ve vergi",
                    "detail": ("Neden: " + "; ".join(reasons) + "." if reasons else
                               "Ekstrede faiz, vade farkı ya da faize bağlı vergi var.")
                              + (f" (gecikme faizi {_tl(late)}, alışveriş faizi {_tl(shop)})" if late or shop else "")
                              + " Önlem: bankada son ödeme gününde dönem borcunun tamamı için otomatik ödeme talimatı ver.",
                    "filter": {"kind": FEE}})
    # istemeden gelen faiz geliri (vadesizde gecelik faiz vb.): arındır
    inc = [r for r in transactions(month) if r["kind"] == INCOME and not r.get("pending")
           and any(k in _fold(r["description"]) for k in ("FAIZ", "FAİZ", "NEMA"))]
    if inc:
        out.append({"level": "uyari", "title": f"Faiz geliri: {_tl(sum(r['amount'] for r in inc))} (arındırılacak)",
                    "detail": "Hesabına faiz işlemiş görünüyor. Faizden uzak durmak istiyorsan bu tutarı sevap beklemeden "
                              "ihtiyaç sahibine vererek arındırabilirsin; hesap türünü (ör. katılım ya da faizsiz vadesiz) kontrol et."})
    return out


def interest_history(months: int = 24) -> dict:
    """Ekstrelerdeki faiz / vade farkı / faize bağlı vergi, dönem dönem (İstatistik → Faiz alarmı)."""
    periods = sorted({st["period_end"][:7] for st in db.list_statements()}, reverse=True)[:months]
    out = []
    for m in periods:
        alarms = interest_alarm(m)
        total = round(sum(sum(x["amount"] for x in st["lines"]) for st in db.interest_charges(m)), 2)
        if total > 0 or alarms:
            out.append({"month": m, "total": total, "alarms": alarms})
    return {"months": out, "total": round(sum(x["total"] for x in out), 2), "checked": len(periods)}


def insights(month: str, today: date | None = None) -> list[dict]:
    """Seçili ay + önümüzdeki aylar için dikkat edilmesi gerekenler. level: kritik | uyari | bilgi | iyi"""
    today = today or date.today()
    out = []
    rows = transactions(month)
    live = [r for r in rows if not r.get("pending") and not r.get("skipped")]
    t = _totals(rows)
    statements = db.list_statements()
    p = plan(6, today=today)

    # 1) Faiz
    fee_rows = [r for r in live if r["kind"] == FEE]
    if fee_rows:
        out += interest_alarm(month) or [{
            "level": "kritik", "title": f"{_tl(t['fees'])} faiz ve vergi ödedin",
            "detail": "Ekstre borcunun tamamı son ödeme tarihine kadar ödenmediğinde alışveriş faizi, geç "
                      "ödendiğinde gecikme faizi işler; üstüne BSMV ve KKDF eklenir. Asgari değil, toplam borcu öde.",
            "filter": {"kind": FEE}}]

    # 2) Gecikmiş veya kısmi ödenmiş (yaklaşanlar "Ödemeler" kartında)
    for x in payables(today):
        if x["status"] == "gecikti":
            out.append({"level": "kritik", "title": f"{x['title']}: son ödeme {abs(x['days_left'])} gün geçti",
                        "detail": f"{_tl(x['amount'])} ödenmedi olarak görünüyor. Ödediysen \"Ödedim\" ile işaretle; "
                                  "ödemediysen gecikme faizi işliyor."})
        elif x["status"] == "kismi":
            out.append({"level": "uyari", "title": f"{x['title']}: {_tl(x['amount'] - x['paid_amount'])} kaldı",
                        "detail": "Kısmi ödeme yaptın; kalan borca faiz işler."})

    # 2b) Her ay tekrarlayan ama sabit kalem olmayan ödemeler: planlama gelecek ayları bilmiyor
    try:
        sug = recurring_suggestions(today)
    except Exception:  # noqa: BLE001 - öneri hesabı özet sayfasını bozmasın
        sug = []
    if sug:
        top = ", ".join(f"{(s['category'] or 'ödeme').split(' & ')[0]} {_tl(s['amount'])}" for s in sug[:2])  # kişi adı yok
        out.append({"level": "bilgi", "title": f"Her ay tekrarlayan {len(sug)} ödeme sabit kalem değil",
                    "detail": f"{top}{' …' if len(sug) > 2 else ''}. Sabit kalem yaparsan gelecek aylar planlamaya girer.",
                    "link": "plan"})

    # 1z) Zekât günü / ek kontrol günü yaklaşıyor
    try:
        from app import zakat

        for z in zakat.upcoming_checks():
            when = "bugün" if z["days_left"] == 0 else f"{z['days_left']} gün sonra" if z["days_left"] > 0 else f"{-z['days_left']} gün önceydi"
            if z["kind"] == "zekat":
                out.append({"level": "uyari", "title": f"🌙 {z['name']}: zekât günü {when} ({_tl(z['amount'])})",
                            "detail": f"{z['date'][8:10]}.{z['date'][5:7]}.{z['date'][:4]} · {z['hijri']}. Verince Zekât sayfasında \"Ödeme ekle\".",
                            "link": "zekat"})
            else:
                out.append({"level": "uyari", "title": f"🌙 {z['name']}: zekât kontrolü {when}",
                            "detail": f"{z['date'][8:10]}.{z['date'][5:7]}.{z['date'][:4]} · {z['hijri']}. Bugünkü toplamla yeniden hesapla; "
                                      f"fark {_tl(z['amount'])}" + (" — tamamla." if z["amount"] > 0 else " — ek ödeme gerekmiyor."),
                            "link": "zekat"})
    except Exception:  # noqa: BLE001 - zekât (fiyat servisi) özeti bozmasın
        pass

    # 2a) Maaş görünmeyen aylar: ilk ve son maaş arasında boşluk → o aylara ait döküm eksik
    sal = sorted({e["month"] for e in db.list_entries() if e["kind"] == INCOME and e["category"] == "Maaş" and not e["skipped"]})
    if len(sal) >= 2:
        gaps, m = [], sal[0]
        while m < sal[-1]:
            if m not in sal:
                gaps.append(m)
            m = add_months(m, 1)
        if gaps:
            out.append({"level": "uyari", "title": f"{len(gaps)} ayda maaş dökümü yok",
                        "detail": f"{_AY[int(gaps[0][5:]) - 1]} {gaps[0][:4]} – {_AY[int(gaps[-1][5:]) - 1]} {gaps[-1][:4]} arasında dökümde maaş yok. "
                                  "Maaş kalemin varsa o aylarda tahmini sayılır; kesin tutar için maaşın yattığı hesabın bu aylara "
                                  "ait dökümünü yükle.",
                        "link": "ekstreler"})

    # 2b) Hesaptan kart ödemesi var ama ekstresi yok → o dönemin harcamaları hiç sayılmamış
    missing: dict[str, list] = defaultdict(list)
    archived = []
    for x in db.card_payments_without_statement():
        (archived if x["before_first"] else missing[x["card_last4"] or "?"]).append(x)
    archived = [x for x in archived if db.period_of(x["pay_date"]) == month]
    if archived:
        out.append({"level": "bilgi",
                    "title": f"Bu ayın kart harcaması tahmini ({_tl(sum(x['amount'] for x in archived))})",
                    "detail": "Bu ödemeler kartların yüklenen ilk ekstresinden eski (bankalar genelde son 12 ayı verir). "
                              "O ayların kart harcaması, ödenen tutar kadar \"" + CARD_ESTIMATE_CATEGORY + "\" olarak sayıldı. "
                              "Eski ekstreyi bulursan yükle; tahmin kendiliğinden kalkar."})
    for last4, ps in missing.items():
        bank = next((x["account"] for x in ps if x["account"]), "")
        bank = re.sub(r"\s+(KATILIM\s+)?BANKASI.*$", "", bank, flags=re.I).strip()
        card = f"{bank} …{last4}" if last4 != "?" else "Bir kart"
        out.append({"level": "uyari",
                    "title": f"{card}: {len(ps)} ödemenin ekstresi yüklenmemiş ({_tl(sum(x['amount'] for x in ps))})",
                    "detail": "Hesabından bu karta ödeme yapılmış ama o döneme ait kart ekstresi yok; şimdilik ödenen tutar "
                              "o ayın tahmini kart harcaması sayıldı. Ödemeler: "
                              + ", ".join(f"{x['pay_date'][8:10]}.{x['pay_date'][5:7]} {_tl(x['amount'])}" for x in ps)
                              + ". Ekstreler sayfasından o ayların kart ekstresini yükle.",
                    "link": "ekstreler"})

    # 2c) Ödemeler kartında görünmeyen açık borçlar (vadesiz ya da döviz/altın)
    open_debt = [d for d in db.list_debts(open_only=True) if d["direction"] == "borc"
                 and (d["asset"] != "TRY" or not d["due_date"])]
    if open_debt:
        out.append({"level": "bilgi", "title": f"{len(open_debt)} açık borcun var",
                    "detail": " · ".join(f"{d['person']}: {fmt_asset(d['remaining'], d['asset'])}"
                                         + (f" (vade {date.fromisoformat(d['due_date']):%d.%m})" if d["due_date"] else "")
                                         for d in open_debt) + ".", "link": "transferler"})

    # 2e) Fiyatı belirgin artan ürünler (fişlerden; son 120 gün içinde alınmış olanlar)
    recent = (today - timedelta(days=120)).isoformat()
    up = [p for p in db.price_history() if p["change_pct"] >= 10 and p["last_date"] >= recent][:4]
    if up:
        out.append({"level": "bilgi", "title": f"{len(up)} üründe zam var (fişlerinden)",
                    "detail": " · ".join(f"{p['product']}: {_tl(p['first_price'])} → {_tl(p['last_price'])} (+%{p['change_pct']:g})"
                                         for p in up) + ".", "link": "fiyatlar"})

    # 2d) Bekleyen alacaklar (sana borçlu olanlar)
    open_rec = [d for d in db.list_debts(open_only=True) if d["direction"] == "alacak"]
    if open_rec:
        parts = [f"{d['person']}: {fmt_asset(d['remaining'], d['asset'])}"
                 + (f" (vade {date.fromisoformat(d['due_date']):%d.%m})" if d["due_date"] else
                    f" ({(today - date.fromisoformat(d['debt_date'])).days} gündür)") for d in open_rec]
        overdue = any(d["due_date"] and d["due_date"] < today.isoformat() for d in open_rec)
        out.append({"level": "uyari" if overdue else "bilgi",
                    "title": f"{len(open_rec)} alacağın var" + (" — vadesi geçen var" if overdue else ""),
                    "detail": " · ".join(parts) + ". Transferler sayfasından takip et; havale gelince TL alacaklar "
                              "kendiliğinden kapanır.", "link": "transferler"})

    # 3) Açık veren aylar
    has_income = any(r["kind"] == INCOME for r in p["recurring"]) or any(
        l["kind"] == INCOME for mm in p["months"] for l in mm["lines"])
    if has_income:
        # Sadece kesin kalemler: tahmini kart harcaması "açık" sayılmaz
        neg = [mm for mm in p["months"] if mm["net"] < 0]
        if neg:
            worst = min(neg, key=lambda mm: mm["net"])
            out.append({"level": "kritik",
                        "title": f"Önümüzdeki {len(p['months'])} aydan {len(neg)} tanesinde kesin giderler geliri aşıyor",
                        "detail": f"Toplam açık {_tl(-sum(mm['net'] for mm in neg))}; en kötü ay {worst['month']} "
                                  f"({_tl(-worst['net'])}). Taksit, abonelik, sabit gider ve faturalar dahil.",
                        "link": "plan"})
        else:
            tight = [mm for mm in p["months"] if mm["net_with_estimate"] < 0 and mm["estimated"]]
            if tight:
                out.append({"level": "bilgi",
                            "title": f"Harcamalar son aylardaki gibi giderse {len(tight)} ayda sıkışabilirsin",
                            "detail": f"Kesin giderler gelirin altında; ama tahmini kart harcamasıyla {tight[0]['month']} "
                                      f"için net {_tl(tight[0]['net_with_estimate'])}. Bütçe veya kısma hedefi koymayı düşün.",
                            "link": "plan"})
    else:
        out.append({"level": "bilgi", "title": "Gelirin tanımlı değil",
                    "detail": "Maaşını Planlama → Sabit kalemler'e eklersen her ay ne kadar artı/eksi verdiğini görürsün."})

    # 4) Taksit yükü
    next_m = add_months(current_period(today), 1)
    nxt = next((mm for mm in p["months"] if mm["month"] == next_m), None)
    if nxt and nxt["groups"].get("taksit"):
        inst = nxt["groups"]["taksit"]
        msg = f"{next_m} ayına {_tl(inst)} taksit devrediyor."
        if nxt["income"]:
            msg += f" Gelirine oranı: %{inst / nxt['income'] * 100:.0f}."
        out.append({"level": "uyari" if nxt["income"] and inst / nxt["income"] > 0.3 else "bilgi",
                    "title": "Gelecek ayın taksit yükü", "detail": msg})

    # 5a) Taksitlendirme maliyeti (vade farkı/kâr payı + BSMV + KKDF taksitleri)
    fee_inst = [i for acc in p["projection"] for i in acc["installments"] if i.get("fee")]
    if fee_inst:
        total = sum(i["amount"] for i in fee_inst)
        months_left = len({i["month"] for i in fee_inst})
        out.append({"level": "uyari", "title": f"Taksitlendirme sana {_tl(total)} daha vade farkı ve vergi ödetecek",
                    "detail": f"Önümüzdeki {months_left} ay boyunca. Taksitli alışverişte vade farkı tutarın ~%25-35'ine "
                              "çıkabiliyor; mümkünse tek çekim yap ya da taksitleri erken kapat.",
                    "filter": {"kind": FEE}})

    # 5) Biten taksitler (rahatlama)
    ending: dict[str, list] = defaultdict(list)
    for acc in p["projection"]:
        for i in acc["installments"]:
            n, total = map(int, i["installment"].split("/"))
            if n == total and i["amount"] > 0 and not i.get("fee"):
                ending[i["month"]].append(i)
    for m, items in sorted(ending.items())[:2]:
        top = ", ".join(i["description"] for i in sorted(items, key=lambda i: -i["amount"])[:3])
        out.append({"level": "iyi", "title": f"{m} ekstresinden sonra aylık {_tl(sum(i['amount'] for i in items))} taksit bitiyor",
                    "detail": f"Son taksitler: {top}"})

    # 5b) Bütçe
    for b in budgets(month):
        if b["pct"] >= 100:
            out.append({"level": "kritik", "title": f"{b['category']} bütçesi aşıldı (%{b['pct']:.0f})",
                        "detail": f"{_tl(b['spent'])} / {_tl(b['limit'])} · {_tl(-b['remaining'])} fazla",
                        "filter": {"category": b["category"]}})
        elif b["pct"] >= 80:
            out.append({"level": "uyari", "title": f"{b['category']}: bütçenin %{b['pct']:.0f} kadarı harcandı",
                        "detail": f"Kalan {_tl(b['remaining'])} (limit {_tl(b['limit'])})", "filter": {"category": b["category"]}})

    # 6) Kategori artışı
    prev = add_months(month, -1)
    prev_rows = [r for r in transactions(prev) if not r.get("pending") and not r.get("skipped")]
    accounts_now = {st["card_no"] for st in statements if st["period"] == month}
    accounts_prev = {st["card_no"] for st in statements if st["period"] == prev}
    # Ekstre kapsamı farklıysa (ör. önceki ayın ekstresi yüklenmemiş) kıyas yanıltıcı olur
    if prev_rows and accounts_now == accounts_prev:
        cur_c = {g["name"]: g["amount"] for g in _group([r for r in live if r["kind"] in (*CARD_SPEND, EXPENSE)], "category")}
        prev_c = {g["name"]: g["amount"] for g in _group([r for r in prev_rows if r["kind"] in (*CARD_SPEND, EXPENSE)], "category")}
        for cat, amt in sorted(cur_c.items(), key=lambda x: -x[1]):
            base = prev_c.get(cat, 0)
            if base > 500 and amt > base * 1.3 and amt - base > 1000:
                out.append({"level": "uyari", "title": f"{cat} harcaması %{(amt / base - 1) * 100:.0f} arttı",
                            "detail": f"{prev}: {_tl(base)} → {month}: {_tl(amt)}", "filter": {"category": cat}})

    # 7) Abonelikler
    for sub in subscriptions():
        if sub["status"] == "iptal" and sub["charged_after_cancel"]:
            out.append({"level": "kritik", "title": f"{sub['merchant'].title()} iptal edilmişti ama yine çekildi",
                        "detail": f"{date.fromisoformat(sub['cancelled_at']):%d.%m} iptalinden sonra {_tl(sub['charged_after_cancel'])} "
                                  "çekim var. İptal tamamlanmamış olabilir; hesabını kontrol et, iade iste.", "link": "tasarruf"})
        if sub["status"] == "dusuruldu" and sub["overcharged"]:
            out.append({"level": "uyari", "title": f"{sub['merchant'].title()} paketi düşmemiş görünüyor",
                        "detail": f"Yeni paket {_tl(sub['new_amount'])} olmalıydı; {_tl(sub['overcharged'])} fazla çekildi.",
                        "link": "tasarruf"})
    subs = [s for acc in p["projection"] for s in acc["subscriptions"]]
    if subs:
        total = sum(s["amount"] for s in subs)
        names = ", ".join(s["description"].title() for s in sorted(subs, key=lambda s: -s["amount"])[:5])
        out.append({"level": "bilgi", "title": f"Aylık abonelik yükü ≈ {_tl(total)}",
                    "detail": f"{len(subs)} abonelik: {names}… Tasarruf sayfasından kullanmadıklarını iptal edildi diye işaretle.",
                    "link": "tasarruf"})

    # 7b) Kısma hedefleri: bu ay hedefi aştıysa
    for g in goals_review(today):
        cur = next((m for m in g["months"] if m["month"] == month), None)
        if g["active"] and cur and cur["spent"] > g["target"]:
            out.append({"level": "uyari", "title": f"{g['name']} hedefini aştın: {_tl(cur['spent'])} / {_tl(g['target'])}",
                        "detail": g["note"] or "Kısma hedefi", "link": "tasarruf"})

    # 8) Döviz
    fx = [r for r in live if r.get("fx")]
    if fx:
        out.append({"level": "bilgi", "title": f"{_tl(sum(r['amount'] for r in fx))} dövizli harcama",
                    "detail": f"{len(fx)} işlem dolar/euro bazlı; kur artınca bu kalemler kendiliğinden pahalanır.",
                    "filter": {"fx": True}})

    # 9) Limit kullanımı
    for s in statements:
        if s["period"] == month and s["credit_limit"]:
            unpaid = max(0.0, s["total_debt"] - (s["paid_amount"] or 0))
            ratio = unpaid / s["credit_limit"]
            if ratio > 0.5:
                out.append({"level": "uyari", "title": f"{s['card_no']} limit doluluğu: %{ratio * 100:.0f}",
                            "detail": "Ödenmemiş ekstre borcu limite göre yüksek; kredi notunu olumsuz etkiler."})

    # 9b) Aynı gelir bu dönemde birden fazla (ör. ay dönümü kayınca ya da yanlış tarihle girilince)
    incomes = defaultdict(list)
    for r in live:
        if r["kind"] == INCOME:
            incomes[r["description"].strip().casefold()].append(r)
    for rs in incomes.values():
        if len(rs) > 1:
            dates = ", ".join(f"{date.fromisoformat(r['date'][:10]):%d.%m.%Y}" for r in sorted(rs, key=lambda r: r["date"]))
            out.append({"level": "uyari", "title": f"\"{rs[0]['description']}\" bu dönemde {len(rs)} kez görünüyor",
                        "detail": f"Tarihler: {dates}. Biri yanlış tarihle girilmiş olabilir; İşlemler → Gelir'den düzelt.",
                        "filter": {"kind": INCOME}})

    # 10) Kategorisiz
    unc = [r for r in live if r["category"] == "Kategorisiz"]
    if unc:
        out.append({"level": "bilgi", "title": f"{len(unc)} işlem kategorisiz",
                    "detail": "İşlemler sayfasında kategori ata; aynı işyeri için kural otomatik oluşur.",
                    "filter": {"category": "Kategorisiz"}})

    # 11) Ekstre bekleyen kredi kartı girişleri (eski olanlar eşleşmemiş demektir)
    waiting = [r for r in rows if r.get("awaiting_statement")]
    stale = [r for r in waiting if (today - date.fromisoformat(r["date"][:10])).days > 45]
    if stale:
        out.append({"level": "uyari", "title": f"{len(stale)} kredi kartı gideri ekstreyle eşleşmedi",
                    "detail": "45 günden eski ama ekstrede karşılığı bulunamadı. Kayda dokunup elle eşleştir "
                              "ya da ödeme şeklini düzelt; yoksa iki kez sayılabilir.",
                    "filter": {"awaiting": True}})

    # 12) Bekleyen sabit kalemler
    pend = [r for r in rows if r.get("pending")]
    if pend and month <= current_period(today):
        out.append({"level": "bilgi", "title": f"{len(pend)} sabit kalem onay bekliyor",
                    "detail": ", ".join(r["description"] for r in pend[:4]) + " — gerçekleştiyse İşlemler'den onayla.",
                    "filter": {"pending": True}})

    order = {"kritik": 0, "uyari": 1, "bilgi": 2, "iyi": 3}
    return sorted(out, key=lambda i: order[i["level"]])


# ---------------------------------------------------------------- "Alırsam ne olur?" simülasyonu

TAX_ON_INTEREST = 0.30  # BSMV %15 + KKDF %15 (taksitli alışveriş / kredi kartı vade farkına)


def installment_schedule(amount: float, n: int, monthly_rate: float) -> list[dict]:
    """Eşit taksit. Vade farkı varsa üstüne BSMV+KKDF eklenir (efektif oran = oran × 1,30)."""
    r = monthly_rate * (1 + TAX_ON_INTEREST)
    pay = amount / n if r == 0 else amount * r / (1 - (1 + r) ** -n)
    out, rem = [], amount
    for i in range(n):
        interest = rem * r
        principal = pay - interest
        rem -= principal
        out.append({"no": i + 1, "payment": _round(pay), "principal": _round(principal), "cost": _round(interest)})
    return out


def simulate_purchase(amount: float, installments: int, monthly_rate: float = 0.0,
                      start_month: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    start = start_month or current_period(today)
    sched = installment_schedule(amount, installments, monthly_rate)
    horizon = max(6, installments + 1)
    base = plan(horizon + 1, today=today)["months"]
    by_m = {m["month"]: m for m in base}
    months, cum_b, cum_a, extra_by_month = [], 0.0, 0.0, {}
    for i, sch in enumerate(sched):
        extra_by_month[add_months(start, i)] = sch["payment"]
    for m in base:
        add = extra_by_month.get(m["month"], 0.0)
        cum_b += m["net"]
        cum_a += m["net"] - add
        months.append({"month": m["month"], "income": m["income"], "payment": _round(add),
                       "net_before": m["net"], "net_after": _round(m["net"] - add),
                       "with_estimate_after": _round(m["net_with_estimate"] - add),
                       "cumulative_before": _round(cum_b), "cumulative_after": _round(cum_a)})
    total = sum(s["payment"] for s in sched)
    tight = [m for m in months if m["net_after"] < 0]
    est_tight = [m for m in months if m["net_after"] >= 0 and m["with_estimate_after"] < 0]
    return {
        "amount": amount, "installments": installments, "monthly_rate": monthly_rate,
        "monthly_payment": sched[0]["payment"], "total_cost": _round(total), "extra_cost": _round(total - amount),
        "schedule": [{**s, "month": add_months(start, i)} for i, s in enumerate(sched)],
        "months": [m for m in months if m["month"] in by_m],
        "negative_months": [m["month"] for m in tight],
        "tight_with_estimate": [m["month"] for m in est_tight],
        "single_payment_month": start,
    }


# ---------------------------------------------------------------- net varlık (hesaplar tamponu)

def net_worth(today: date | None = None) -> dict:
    """Bankada/nakitte ne var, kartlara ne borçlusun, alacak/borç → bugünkü net durum."""
    today = today or date.today()
    accounts = db.list_accounts()
    cash = sum(a["current"] for a in accounts if a["current"] is not None)
    cards = []
    for p in payables(today):
        if p["type"] == "statement" and p["status"] != "odendi":
            cards.append({"title": p["title"], "amount": _round(p["amount"] - (p["paid_amount"] or 0)), "due_date": p["due_date"],
                          "estimated": False})
        elif p["type"] == "estimate":
            cards.append({"title": p["title"], "amount": p["amount"], "due_date": p["due_date"], "estimated": True})
    card_debt = sum(c["amount"] for c in cards if not c["estimated"])
    card_est = sum(c["amount"] for c in cards if c["estimated"])
    open_try = [d for d in db.list_debts(open_only=True) if d["asset"] == "TRY"]
    receivable = sum(d["remaining"] for d in open_try if d["direction"] == "alacak")
    payable = sum(d["remaining"] for d in open_try if d["direction"] == "borc")
    stale = [a["name"] for a in accounts if a["as_of"] and a["source"] == "dokum" and (today - date.fromisoformat(a["as_of"])).days > 35]
    accounts = [a for a in accounts if a["as_of"]]  # bakiyesi bilinmeyenler unknown_banks'ta
    wallets = [{"id": w["id"], "name": w["name"], "card_type": w["card_type"], "balance": w["current"]} for w in db.list_wallets()]
    # ekstresi olan her kredi kartı (hepsi ödenmiş olsa da): son ekstre + sonraki tahmini
    latest: dict[str, dict] = {}
    for st in db.list_statements():
        if st["card_no"] not in latest:
            latest[st["card_no"]] = st
    est = {p["title"]: p for p in payables(today) if p["type"] == "estimate"}
    credit_cards = []
    for card_no, st in latest.items():
        title = f"{st['bank']} {card_no}"
        unpaid = _round(max(0.0, st["total_debt"] - (st["paid_amount"] or 0))) if st["due_date"] else 0.0
        nxt = est.get(title)
        credit_cards.append({"title": title, "bank": st["bank"], "card_no": card_no, "last_period_end": st["period_end"],
                             "last_total": st["total_debt"], "last_due": st["due_date"], "paid_at": st["paid_at"],
                             "unpaid": unpaid, "next_estimate": nxt["amount"] if nxt else None,
                             "next_due": nxt["due_date"] if nxt else None, "cycle": nxt.get("cycle") if nxt else None})
    # dökümü daha önce yüklenmiş ama bakiyesi bilinmeyen bankalar (tampon özelliğinden önceki yüklemeler)
    from app.bank_import import short_bank

    have = {a["name"] for a in accounts if a["as_of"]}
    seen_banks = sorted(({short_bank(b) for b in db.imported_bank_names()} | {a["name"] for a in accounts if not a["as_of"]})
                        - have - {"Banka hesabı"})
    in_wallets = sum(w["balance"] for w in wallets)
    from app import zakat

    hold = zakat.holdings()  # altın/döviz: sadece "Ben"inkiler net duruma girer
    zakat_due = zakat.self_due()  # ödenmemiş geçmiş yıl zekâtı: borç
    jars = db.jar_reserved(today)
    for a in accounts:
        a["reserved"] = jars["by_account"].get(a["id"], 0.0)
    return {"reserved": jars["total"], "free": _round(cash - jars["total"]), "accounts": accounts, "cash": _round(cash), "cards": cards, "card_debt": _round(card_debt),
            "card_estimated": _round(card_est), "receivable": _round(receivable), "payable": _round(payable),
            "wallets": wallets, "in_wallets": _round(in_wallets), "credit_cards": credit_cards, "unknown_banks": seen_banks,
            "holdings": hold["self_total"], "family_holdings": hold["family_total"], "zakat_due": zakat_due,
            "net": _round(cash + in_wallets + hold["self_total"] - card_debt + receivable - payable - zakat_due),
            "net_after_estimates": _round(cash + in_wallets + hold["self_total"] - card_debt - card_est + receivable - payable
                                          - zakat_due),
            "stale": stale}


def net_worth_at(day: date) -> dict:
    """Geçmiş bir günün net durumu (yaklaşık): o günkü banka bakiyeleri (bugünkü çapadan geriye hareketlerle),
    o gün ödenmemiş kart ekstreleri, açık borç/alacak, o günün fiyatıyla altın/döviz ("Ben"). Yemek kartları dahil değil."""
    from app import zakat

    d = day.isoformat()
    bank, unknown = zakat._bank_at(day)
    card = db.card_debt_at(d)
    try:
        px = zakat.historical_prices(day, offline=False)
    except Exception:  # noqa: BLE001 - fiyat yoksa altın/döviz sayılmaz
        px = {}
    recv = pay = 0.0
    for x in db.list_debts():
        if x["debt_date"] > d or (x["settled_at"] and x["settled_at"] <= d) or (x["written_off_at"] and x["written_off_at"] <= d):
            continue
        left = max(0.0, x["amount"] - sum(p["amount"] for p in x["payments"] if p["paid_at"] <= d))
        rate = 1.0 if x["asset"] == "TRY" else px.get(zakat.DEBT_ASSET_KEYS.get(x["asset"], ""), 0)
        if x["direction"] == "alacak":
            recv += left * rate
        else:
            pay += left * rate
    hold = 0.0
    if px:
        for p in zakat.people():
            if not p["is_self"]:
                continue
            for a in p["assets"]:
                if not a["acquired"] or a["acquired"] <= d:
                    hold += zakat._value_at(a["kind"], a["quantity"], px) or 0
    net = bank + hold - card + recv - pay
    return {"as_of": d, "cash": _round(bank), "holdings": _round(hold), "card_debt": _round(card), "receivable": _round(recv),
            "payable": _round(pay), "net": _round(net), "unknown_accounts": unknown, "prices": bool(px)}


# ---------------------------------------------------------------- dönem sonu raporu (Telegram)

_AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def _pct(now: float, before: float) -> str:
    if before <= 0:
        return ""
    p = round((now - before) / before * 100)
    return f" ({'+' if p > 0 else ''}{p}%)" if p else " (aynı)"


def year_summary(year: int) -> dict:
    """Yıllık özet (İstatistik): toplamlar, aylık ortalama, en yüksek/düşük ay, kategoriler, işyerleri, faiz,
    zekât ve sadaka, geçen yıla göre değişim. Dönemler ay dönümüne göredir."""
    cur = current_period()
    all_rows = transactions(None)
    live = [r for r in all_rows if not r.get("skipped") and not r.get("pending") and not r.get("linked")
            and r["month"] <= cur]  # gelecek dönemlere düşen taksit parçaları yıllık özete girmez
    years = sorted({int(r["month"][:4]) for r in live}, reverse=True)

    def of(y: int) -> list[dict]:
        return [r for r in live if r["month"].startswith(f"{y}-")]

    rows = of(year)
    months = sorted({r["month"] for r in rows})
    t = _totals(rows)
    per = [{"month": m, **{k: v for k, v in _totals([r for r in rows if r["month"] == m]).items() if k in ("income", "expense", "net")}}
           for m in months]
    expenses = [r for r in rows if r["kind"] in (*CARD_SPEND, FEE, EXPENSE)]
    cats = _group(expenses, "category")
    total_exp = sum(c["amount"] for c in cats) or 1
    prev = _totals(of(year - 1)) if of(year - 1) else None
    from app import donations

    dn = next((y for y in donations.summary()["years"] if y["year"] == str(year)), {"sadaka": 0.0, "zekat": 0.0})
    subs = [x for x in subscriptions() if x["status"] == "aktif"]
    return {
        "year": year, "years": years, "months": len(months),
        "income": _round(t["income"]), "expense": _round(t["expense"]), "net": _round(t["net"]), "fees": _round(t["fees"]),
        "avg_expense": _round(t["expense"] / len(months)) if months else 0.0,
        "savings_rate": round(t["net"] / t["income"] * 100) if t["income"] > 0 else None,
        "highest": max(per, key=lambda m: m["expense"]) if per else None,
        "lowest": min(per, key=lambda m: m["expense"]) if per else None,
        "per_month": per,
        "categories": [{**c, "share": round(c["amount"] / total_exp * 100)} for c in cats[:8]],
        "merchants": _group([r for r in rows if r["kind"] in CARD_SPEND or r["kind"] == EXPENSE], "merchant")[:5],
        "expense_change": _change(t["expense"], prev["expense"]) if prev else None,
        "income_change": _change(t["income"], prev["income"]) if prev else None,
        "zakat": dn["zekat"], "sadaka": dn["sadaka"],
        "subscriptions_yearly": _round(sum(x["monthly"] for x in subs) * 12),
    }


def _change(now: float, before: float) -> int | None:
    return round((now - before) / before * 100) if before > 0 else None


def month_digest(month: str, today: date | None = None) -> dict:
    """Biten dönemin özeti (veri): Özet'teki kart ve Telegram mesajı aynı rakamları kullanır."""
    today = today or date.today()
    s, prev = summary(month, today=today), summary(add_months(month, -1), today=today)
    t, pt = s["totals"], prev["totals"]
    before = {c["name"]: c["amount"] for c in prev["by_category"]}
    top = [{"name": c["name"], "amount": c["amount"], "change": _change(c["amount"], before.get(c["name"], 0))}
           for c in s["by_category"][:5]]
    rising = [c["name"] for c in s["by_category"] if before.get(c["name"], 0) > 500 and c["amount"] > before[c["name"]] * 1.3][:3]
    upcoming = [p for p in payables(today) if p["status"] in ("bekliyor", "gecikti", "kismi", "tahmini") and p["days_left"] <= 30]
    try:
        from app import zakat

        zk = zakat.upcoming_checks(today, window=30)
    except Exception:  # noqa: BLE001 - fiyat servisine ulaşılamazsa özet yine çıksın
        zk = []
    try:
        sug = len(recurring_suggestions(today)) + len(recurring_drift(today))
    except Exception:  # noqa: BLE001
        sug = 0
    y, m = map(int, month.split("-"))
    return {
        "month": month, "label": f"{_AY[m - 1]} {y}",
        "income": _round(t["income"]), "expense": _round(t["expense"]), "net": _round(t["net"]),
        "expense_change": _change(t["expense"], pt["expense"]),
        "savings_rate": round(t["net"] / t["income"] * 100) if t["income"] > 0 else None,
        "fees": _round(t["fees"]), "top": top, "rising": rising,
        "budgets_over": [{"category": b["category"], "spent": b["spent"], "limit": b["limit"]} for b in s["budgets"] if b["spent"] > b["limit"]],
        "upcoming": {"count": len(upcoming), "total": _round(sum(p["amount"] - (p["paid_amount"] or 0) for p in upcoming)),
                     "items": [{"title": p["title"], "amount": p["amount"], "due_date": p["due_date"], "estimated": p["status"] == "tahmini"}
                               for p in sorted(upcoming, key=lambda p: p["due_date"])[:5]]},
        "zakat": [{"name": z["name"], "kind": z["kind"], "date": z["date"], "days_left": z["days_left"], "amount": z["amount"]} for z in zk],
        "suggestions": sug, "net_worth": net_worth(today)["net"] if db.list_accounts() else None,
    }


def period_report(month: str) -> str:
    """Biten dönemin kısa özeti (HTML, Telegram için)."""
    import html

    s = summary(month)
    prev = summary(add_months(month, -1))
    t, pt = s["totals"], prev["totals"]
    y, m = map(int, month.split("-"))
    lines = [f"📊 <b>{_AY[m - 1]} {y} dönemi bitti</b>",
             f"Gelir {_tl(t['income'])} · Gider {_tl(t['expense'])}{_pct(t['expense'], pt['expense'])}",
             f"Net <b>{_tl(t['net'])}</b>" + (f" · borç hareketi {_tl(t['debt_flow'])}" if t.get("debt_flow") else "")]
    before = {c["name"]: c["amount"] for c in prev["by_category"]}
    top = s["by_category"][:5]
    if top:
        lines.append("\n<b>En çok</b>")
        lines += [f"• {html.escape(c['name'])}: {_tl(c['amount'])}{_pct(c['amount'], before.get(c['name'], 0))}" for c in top]
    rising = [c for c in s["by_category"] if before.get(c["name"], 0) > 500 and c["amount"] > before[c["name"]] * 1.3][:3]
    if rising:
        lines.append("📈 Artanlar: " + ", ".join(f"{html.escape(c['name'])} {_pct(c['amount'], before[c['name']]).strip(' ()')}"
                                                 for c in rising))
    over = [b for b in s["budgets"] if b["spent"] > b["limit"]]
    if over:
        lines.append("⚠️ Bütçe aşıldı: " + ", ".join(f"{html.escape(b['category'])} ({_tl(b['spent'])} / {_tl(b['limit'])})" for b in over))
    subs = [x for x in subscriptions() if x["status"] == "aktif"]
    if subs:
        lines.append(f"🔁 Aktif abonelikler: {len(subs)} · aylık ~{_tl(sum(x['monthly'] for x in subs))}")
    if t.get("receivable") or t.get("payable"):
        lines.append(f"🤝 Alacakta {_tl(t.get('receivable', 0))} · borçta {_tl(t.get('payable', 0))}")
    nw = net_worth()
    if nw["accounts"]:
        lines.append(f"🏦 Net durum {_tl(nw['net'])} (bankada {_tl(nw['cash'])})")
    d = month_digest(month)
    lines.append(f"⚠️ Faiz ve ücret: <b>{_tl(d['fees'])}</b>" if d["fees"] else "✅ Faiz ödemedin")
    if d["savings_rate"] is not None:
        lines.append(f"💰 Gelirinin %{d['savings_rate']}'i kaldı" if d["savings_rate"] > 0 else "💸 Gider geliri aştı")
    if d["upcoming"]["count"]:
        lines.append(f"\n<b>Önümüzdeki 30 gün</b>: {d['upcoming']['count']} ödeme · {_tl(d['upcoming']['total'])}")
        lines += [f"• {html.escape(i['title'])} {'~' if i['estimated'] else ''}{_tl(i['amount'])} · {i['due_date'][8:10]}.{i['due_date'][5:7]}"
                  for i in d["upcoming"]["items"]]
    for z in d["zakat"]:
        lines.append(f"🌙 {html.escape(z['name'])}: {'zekât günü' if z['kind'] == 'zekat' else 'ek kontrol'} "
                     f"{z['date'][8:10]}.{z['date'][5:7]} ({z['days_left']} gün)")
    if d["suggestions"]:
        lines.append(f"🔁 Planlama'da {d['suggestions']} sabit kalem önerisi bekliyor")
    return "\n".join(lines)


# ---------------------------------------------------------------- birikim hedefleri

def targets(today: date | None = None) -> list[dict]:
    """Her hedef için: biriken (başlangıç dönemi dahil, dönemlerin eldeki para artışı + başta ayrılan), kalan,
    ayda gereken ve son 3 tamamlanmış dönemin ortalamasıyla tahmini ulaşma ayı."""
    today = today or date.today()
    cur = current_period(today)
    all_rows = transactions(None)
    by_month: dict[str, list] = defaultdict(list)
    for r in all_rows:
        by_month[r["month"]].append(r)
    net_of = {m: _totals(rs)["cash_net"] for m, rs in by_month.items()}
    done = [m for m in sorted(net_of) if m < cur][-3:]
    pace = _round(sum(net_of[m] for m in done) / len(done)) if done else 0.0
    out = []
    for t in db.list_targets():
        saved = _round(t["already"] + sum(v for m, v in net_of.items() if t["start_month"] <= m <= cur))
        left = _round(max(0.0, t["amount"] - saved))
        dl = date.fromisoformat(t["deadline"])
        months_left = max(1, (dl.year - today.year) * 12 + dl.month - today.month)
        need = _round(left / months_left)
        eta = None
        if left <= 0:
            eta = cur
        elif pace > 0:
            eta = add_months(cur, int(-(-left // pace)))
        out.append({**t, "saved": saved, "left": left, "pct": min(100.0, round(saved / t["amount"] * 100, 1)) if t["amount"] else 0,
                    "months_left": months_left, "need_per_month": need, "pace": pace, "eta": eta,
                    "on_track": left <= 0 or (pace > 0 and pace >= need)})
    return out


# ---------------------------------------------------------------- dönem karşılaştırması (gün / hafta günü / hafta)

_WD = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
_FIXED_CATEGORIES = {"Kira & Aidat", "Faturalar", "Elektrik", "Su", "Doğalgaz", "İnternet & TV", "Telefon", "Vergi & Harç",
                     "Sigorta", "Banka Faiz & Ücret"}


def period_bounds(month: str) -> tuple[date, date]:
    """Dönemin ilk ve son günü (ay dönümüne göre): 2026-11, dönüm 20 → 20 Eki – 19 Kas."""
    start_day = db.cycle_start_day()
    y, m = map(int, month.split("-"))
    if start_day > 1:
        py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
        start = date(py, pm, min(start_day, 28))
        end = date(y, m, min(start_day, 28)) - timedelta(days=1)
    else:
        start = date(y, m, 1)
        end = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    return start, end


def compare(month: str | None = None, variable_only: bool = True, today: date | None = None) -> dict:
    """Bu dönemi aynı güne kadar geçen dönem ve son 3 dönem ortalamasıyla kıyasla. Harcama GERÇEK tarihine göre
    (kart işlemi de harcandığı gün; ekstre dönemine göre değil). variable_only: sabit giderler (kira, fatura, sabit kalem)
    hariç — alışkanlığı ölçer."""
    today = today or date.today()
    month = month or current_period(today)
    months = [add_months(month, -i) for i in range(4)]  # bu, geçen, 2 ve 3 önceki
    bounds = {m: period_bounds(m) for m in months}
    lo, hi = bounds[months[-1]][0], bounds[month][1]
    daily: dict[str, dict[int, float]] = {m: defaultdict(float) for m in months}
    for r in transactions(None):
        if r.get("deleted") or r.get("skipped") or r.get("pending") or r.get("linked"):
            continue
        if r["kind"] not in (PURCHASE, REFUND, FEE, EXPENSE):
            continue
        if variable_only and (r.get("recurring_id") or r["category"] in _FIXED_CATEGORIES or r.get("installment")):
            continue
        d = date.fromisoformat(str(r["date"])[:10])
        if not lo <= d <= hi:
            continue
        for m in months:
            st, en = bounds[m]
            if st <= d <= en:
                daily[m][(d - st).days] += r["amount"]
                break
    st, en = bounds[month]
    length = (en - st).days + 1
    upto = min(length, max(0, (today - st).days + 1)) if st <= today else 0
    if today > en:
        upto = length

    def cum(m: str, n: int) -> list[float]:
        out, t = [], 0.0
        for i in range(n):
            t += daily[m].get(i, 0.0)
            out.append(_round(t))
        return out

    prev = months[1]
    prev_len = (bounds[prev][1] - bounds[prev][0]).days + 1
    lens = {m: (bounds[m][1] - bounds[m][0]).days + 1 for m in months}
    cur_c, prev_c = cum(month, upto), cum(prev, prev_len)
    avg_c = [_round(sum(cum(m, length)[i] if i < lens[m] else cum(m, lens[m])[-1] for m in months[1:]) / 3) for i in range(length)]
    same = upto - 1
    def at(series: list[float], i: int) -> float:
        return series[min(i, len(series) - 1)] if series and i >= 0 else 0.0
    cur_v, prev_v, avg_v = at(cur_c, same), at(prev_c, same), at(avg_c, same)

    def pct(a: float, b: float) -> float | None:
        return round((a - b) / b * 100, 1) if b > 0 else None

    # hafta günleri: gün başına ortalama (o güne kadar kaç pazartesi geçtiyse)
    def by_wd(m: str, n: int) -> list[float]:
        tot, cnt = [0.0] * 7, [0] * 7
        for i in range(n):
            wd = (bounds[m][0] + timedelta(days=i)).weekday()
            tot[wd] += daily[m].get(i, 0.0)
            cnt[wd] += 1
        return [tot[k] / cnt[k] if cnt[k] else 0.0 for k in range(7)]
    wd_cur = by_wd(month, upto)
    span = lambda m: min(upto, lens[m]) if upto >= 7 else lens[m]  # noqa: E731 - aynı gün aralığı (elma-elma)
    wd_avg = [sum(by_wd(m, span(m))[k] for m in months[1:]) / 3 for k in range(7)]
    weekdays = [{"day": _WD[k], "current": _round(wd_cur[k]), "avg3": _round(wd_avg[k]), "pct": pct(wd_cur[k], wd_avg[k])}
                for k in range(7)]
    # dönemin haftaları (1–7. gün = 1. hafta …)
    def by_week(m: str, n: int) -> list[float]:
        w = [0.0] * 5
        for i in range(n):
            w[min(i // 7, 4)] += daily[m].get(i, 0.0)
        return w
    wk_cur, wk_avg = by_week(month, upto), [sum(by_week(m, lens[m])[k] for m in months[1:]) / 3 for k in range(5)]
    weeks = [{"week": k + 1, "current": _round(wk_cur[k]), "avg3": _round(wk_avg[k]),
              "done": upto >= min((k + 1) * 7, length), "pct": pct(wk_cur[k], wk_avg[k])} for k in range(5)]

    notes = []
    done_wd = [w for w in weekdays if w["pct"] is not None and w["avg3"] >= 20]
    if done_wd and upto >= 7:
        lo_w = min(done_wd, key=lambda w: w["pct"])
        hi_w = max(done_wd, key=lambda w: w["pct"])
        if lo_w["pct"] <= -15:
            notes.append(f"{lo_w['day']} günleri son 3 döneme göre %{abs(round(lo_w['pct']))} daha az harcıyorsun")
        if hi_w["pct"] >= 15:
            notes.append(f"{hi_w['day']} günleri %{round(hi_w['pct'])} daha fazla")
    def daily_list(m: str, n: int) -> list[dict]:
        return [{"date": (bounds[m][0] + timedelta(days=i)).isoformat(), "amount": _round(daily[m].get(i, 0.0))} for i in range(n)]

    cur_days, prev_days = daily_list(month, upto), daily_list(prev, prev_len)
    # olağandışı gün: son 3 dönemin günlük ortalamasının 2,5 katından fazla
    base = sum(sum(daily[m].values()) for m in months[1:]) / max(1, sum(lens[m] for m in months[1:]))
    for dd in cur_days + prev_days:
        dd["spike"] = base > 0 and dd["amount"] > base * 2.5
    top = sorted([{**x, "period": "bu"} for x in cur_days] + [{**x, "period": "gecen"} for x in prev_days],
                 key=lambda x: -x["amount"])[:4]
    return {"month": month, "start": st.isoformat(), "end": en.isoformat(), "length": length, "day": upto,
            "daily": {"current": cur_days, "prev": prev_days, "avg_per_day": _round(base)},
            "top_days": [t for t in top if t["amount"] > 0],
            "variable_only": variable_only,
            "current": cur_c, "prev": prev_c[:length], "avg3": avg_c,
            "at_day": {"current": cur_v, "prev": prev_v, "avg3": avg_v,
                       "vs_prev": pct(cur_v, prev_v), "vs_avg3": pct(cur_v, avg_v)},
            "weekdays": weekdays, "weeks": [w for w in weeks if w["week"] <= (length + 6) // 7], "notes": notes}



def day_detail(d: str, variable_only: bool = False) -> dict:
    """Bir günün harcamaları (gerçek tarihe göre; kart işlemi harcandığı gün)."""
    rows = []
    for r in transactions(None):
        if str(r["date"])[:10] != d or r.get("deleted") or r.get("skipped") or r.get("pending") or r.get("linked"):
            continue
        if r["kind"] not in (PURCHASE, REFUND, FEE, EXPENSE):
            continue
        fixed = bool(r.get("recurring_id") or r["category"] in _FIXED_CATEGORIES or r.get("installment"))
        if variable_only and fixed:
            continue
        rows.append({**r, "fixed": fixed})
    rows.sort(key=lambda r: -r["amount"])
    # o gün ödenen kart ekstreleri: bankadan para çıkar ama harcama değildir (harcamalar kendi günlerinde sayılır)
    # ödeme tarihi ödeme penceresinde olmalı: geç yüklenen eski ekstreler "yükleme günü ödendi" diye işaretlenmiş olabilir
    paid = [{"title": f"{s_['bank']} {s_['card_no']}", "amount": s_["paid_amount"], "period_end": s_["period_end"]}
            for s_ in db.list_statements() if s_["paid_at"] == d and s_["paid_amount"]
            and s_["period_end"] <= d <= (s_["due_date"] and (date.fromisoformat(s_["due_date"]) + timedelta(days=10)).isoformat() or d)]
    return {"date": d, "total": _round(sum(r["amount"] for r in rows)), "rows": rows,
            "by_category": _group(rows, "category"), "card_payments": paid}
