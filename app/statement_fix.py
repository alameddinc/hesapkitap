"""Ekstre tutmuyor: ne tutmuyor, nasıl düzeltilir. Kayıtlı ekstrenin durumu (list_statements ve satır düzeltmesinin
önizlemesi AYNI hesabı kullanır: evaluate) ve elle satır ekle / düzenle (tek "geri al" kaydı, app/undo.py).

Durum. Üç denetim:
  özet denklemi   önceki − ödemeler + harcamalar (+ faiz, + iade ayrıysa) = yeni borç   (parsers.base.equation)
  satır farkı     okunan işlemler (ödeme hariç) − özetin harcaması                       (parsers.base.reconciliation_diff)
  satır denklemi  önceki borç + bütün satırlar (ödeme ve iade eksi) − yeni borç
Tuttu: özet denklemi tutuyor (ya da okunamadı) ve satır farkı < 1 TL; YA DA özet denklemi tutmuyor / okunamadı ama
satır denklemi < 1 TL (satırlar önceki borçtan yeni borca kuruşu kuruşuna götürüyor: özetteki bir ara rakam yanlış
okunmuş). Özet tutuyorsa satır denklemine bakılmaz: o, iade ile ödemenin karışmasını göremez (ikisi de eksi).

Fark (diff, işaretli: "satırlar − beklenen"; artı = satırlar fazla, eksi = satırlar eksik) düzeltilecek olandır: özet
tutuyorsa satır farkı, tutmuyorsa satır denklemi. Bir satır eklemek ya da tutarını değiştirmek farkı o kadar oynatır;
öneri (hint) farkı açıklayan en olası satırdır: ödeme satırı yok, tam o tutarda fazla bir satır, işareti ters satır,
faiz / BSMV / KKDF satırı eksik, iade / kampanya / puan satırı eksik. Cümleler sunucudan: web ve mobil aynı metni
gösterir.

Satır düzeltmesi: tutar her zaman artı girilir, işareti türden gelir (iade ve ödeme eksi). Yalnız ekstreyi
değiştirebilen (sahip / tam yetkili üye; statement_reread._can_apply). dry_run: yazmadan sonucu döndürür (canlı
"Tuttu ✓"). Harcama olmaktan çıkan / hesaba katılmayan satıra bağlı elle kayıt eşleşmesi ayrılır (sync.unmatch).
"""
from datetime import date

from app import db, money
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND, equation, reconciliation_diff, statement_fees

TYPES = (PURCHASE, REFUND, PAYMENT, FEE)
CREDIT = (REFUND, PAYMENT)  # eksi tutarlı türler
TOL = 1.0
FEE_HINT_MAX = 1000.0  # bu kadarına kadar "eksi" fark önce faiz / BSMV / KKDF satırı sayılır
TYPE_LABEL = {PURCHASE: "harcama", REFUND: "iade", PAYMENT: "ödeme", FEE: "faiz / ücret"}


class FixError(ValueError):
    """Kullanıcıya gösterilecek neden (409 / 422)."""


def aggregate(rows: list[dict]) -> dict:
    """Satırlardan denetimin ihtiyacı olan toplamlar (silinen / hesaba katılmayan satır sayılmaz)."""
    live = [r for r in rows if not r.get("is_deleted")]

    def s(pred):
        return round(sum(r["amount"] for r in live if pred(r)), 2)
    return {"parsed": s(lambda r: r["txn_type"] != PAYMENT), "fee_txns": s(lambda r: r["txn_type"] == FEE),
            "refunds": s(lambda r: r["txn_type"] == REFUND), "rows_total": s(lambda r: True),
            "payment_rows": sum(r["txn_type"] == PAYMENT for r in live)}


def evaluate(st: dict, agg: dict, hide_fees: bool = False, rows: list[dict] | None = None) -> dict:
    """Ekstrenin durumu (st: statements satırı, agg: aggregate). rows verilirse öneri tek satırı da arar."""
    fees = 0.0 if hide_fees else statement_fees(st.get("interest_fees"), agg["fee_txns"])
    prev, pay, act, total = st.get("previous_balance"), st.get("payments"), st.get("period_activity"), st.get("total_debt")
    eq = equation(prev, pay, act, fees, total, agg["refunds"])
    rec = (reconciliation_diff(agg["parsed"], act, st.get("interest_fees"), fees, prev, pay, total, agg["refunds"])
           if act is not None else None)
    rows_diff = round(prev + agg["rows_total"] - total, 2) if prev is not None and total is not None else None
    # satır denklemi iade ile ödemeyi ayıramaz (ikisi de eksi): yalnız özet denklemi tutmuyor / okunamadıysa esas alınır
    rows_ok = rows_diff is not None and abs(rows_diff) < TOL and eq["ok"] is not True
    summary_ok = eq["ok"] is not False and (rec is None or abs(rec) < TOL)
    known = eq["ok"] is not None or rec is not None
    ok = (summary_ok or rows_ok) if known else None
    if ok or not known:
        diff = None
    elif eq["ok"] is not False:  # özet kendi içinde tutuyor: satırlar özetin harcamasını tutmuyor
        diff = rec
    else:  # özet tutmuyor: satırlar önceki borçtan yeni borca götürmeli
        diff = rows_diff if rows_diff is not None else (-eq["diff"] if eq["diff"] is not None else None)
    kind = None if ok is not False else ("summary" if eq["ok"] is False else "rows")
    out = {"fees": fees, "fees_included": eq["fees_included"], "refunds_separate": bool(eq.get("refunds_separate")),
           "equation_diff": eq["diff"], "reconciliation_diff": rec, "rows_diff": rows_diff, "rows_ok": rows_ok,
           "ok": ok, "kind": kind, "diff": diff, "hint": None,
           **texts(kind, diff, suspect(eq["diff"], fees, agg["refunds"], pay, prev) if kind == "summary" else None)}
    if diff is not None and abs(diff) >= TOL:
        out["hint"] = hint(st, agg, diff, via_rows=eq["ok"] is False, rows=rows)
    return out


BADGE_SUMMARY = "Özet tutmuyor"
BADGE_ROWS_SHORT = "İşlemler eksik"
BADGE_ROWS_EXTRA = "İşlemler fazla"


def suspect(eq_diff: float | None, fees: float, refunds: float, pay: float | None, prev: float | None) -> str | None:
    """Özet tutmuyorsa hangi özet rakamı şüpheli: farkı kuruşu kuruşuna (1 TL) açıklayan TEK bileşen. Yoksa None."""
    if eq_diff is None:
        return None
    # özetin ödemesi okunmadıysa (0) önceki borcu kapatan ödeme şüphelidir, önceki borç değil
    unpaid = (pay or 0.0) < TOL
    parts = [("faiz / BSMV / KKDF", fees), ("iade / kampanya", abs(refunds or 0.0)),
             ("ödeme", (prev or 0.0) if unpaid else pay), ("önceki borç", 0.0 if unpaid else (prev or 0.0))]
    hit = [name for name, v in parts if v >= TOL and abs(abs(eq_diff) - v) < TOL]
    return hit[0] if len(hit) == 1 else None


def texts(kind: str | None, diff: float | None, suspected: str | None = None) -> dict:
    """Rozet ve tek cümle (web ve mobil aynı): "rows" özet doğru, işlem listesi tutmuyor; "summary" özet tutmuyor
    (suspected: farkı açıklayan özet rakamı)."""
    if kind is None:
        return {"badge": None, "title": None}
    amount = _tl(diff) if diff is not None else None
    if kind == "rows":
        return {"badge": BADGE_ROWS_EXTRA if (diff or 0) > 0 else BADGE_ROWS_SHORT,
                "title": f"Özet doğru, işlem listesinde {amount} fark var"}
    head = f"Ekstre özeti {amount} tutmuyor" if amount else "Ekstre özetinin rakamları birbirini tutmuyor"
    return {"badge": BADGE_SUMMARY, "title": f"{head}: {suspected} rakamı yanlış okunmuş olabilir" if suspected else head}


def _tl(v: float) -> str:
    return money.tl(abs(v))


def hint(st: dict, agg: dict, diff: float, via_rows: bool = False, rows: list[dict] | None = None) -> dict:
    """Farkı en iyi açıklayan düzeltme: {kind, text, suggest: {txn_type, amount (artı), txn_date}, row_id?}.
    kind: payment_missing | extra_row | sign | fee_missing | charge_missing | credit_missing."""
    cut = str(st.get("period_end") or "")[:10]
    pay = st.get("payments") or 0.0
    head = f"{_tl(diff)} fark"
    live = [r for r in rows or [] if not r.get("is_deleted")]
    # ödeme satırı listede yok (satır denklemiyle bakılır: satır farkı ödemeyi görmez)
    paid = pay if pay >= TOL else (st.get("previous_balance") or 0.0)  # özetin ödemesi okunmadıysa önceki borç
    if via_rows and not agg["payment_rows"] and paid >= TOL and abs(diff - paid) < TOL:
        return {"kind": "payment_missing", "text": f"{head}: ekstredeki {_tl(paid)} ödeme satırı listede yok",
                "suggest": {"txn_type": PAYMENT, "amount": round(paid, 2), "txn_date": cut}}
    if rows is not None:
        considered = [r for r in live if via_rows or r["txn_type"] != PAYMENT]
        same = [r for r in considered if money.eq(r["amount"], diff)]
        if len(same) == 1:  # tam o tutarda TEK satır: fazla okunmuş (ekstrede yok ya da bilgi satırı)
            r = same[0]
            return {"kind": "extra_row", "row_id": r["id"],
                    "text": f"{head}: {_tl(r['amount'])} tutarındaki {TYPE_LABEL.get(r['txn_type'], 'satır')} satırı "
                            "ekstrede yoksa fazla okunmuş; hesaba katma",
                    "suggest": None}
        flip = [r for r in considered if r["txn_type"] != PAYMENT and money.eq(2 * r["amount"], diff)]
        if len(flip) == 1:
            r = flip[0]
            to = PURCHASE if r["amount"] < 0 else REFUND
            return {"kind": "sign", "row_id": r["id"],
                    "text": f"{head}: {_tl(r['amount'])} tutarındaki satırın işareti ters okunmuş olabilir "
                            f"({TYPE_LABEL[to]} olmalı)",
                    "suggest": {"txn_type": to, "amount": round(abs(r["amount"]), 2), "txn_date": r["txn_date"][:10]}}
    if diff < 0:  # satırlar eksik: eksik borç satırı
        if not agg["fee_txns"] and -diff <= FEE_HINT_MAX:
            return {"kind": "fee_missing", "text": f"{head}: muhtemelen faiz / BSMV / KKDF satırı eksik",
                    "suggest": {"txn_type": FEE, "amount": round(-diff, 2), "txn_date": cut}}
        return {"kind": "charge_missing", "text": f"{head}: muhtemelen bir harcama satırı eksik okunmuş",
                "suggest": {"txn_type": PURCHASE, "amount": round(-diff, 2), "txn_date": cut}}
    return {"kind": "credit_missing", "text": f"{head}: muhtemelen bir iade / kampanya / puan iadesi satırı eksik",
            "suggest": {"txn_type": REFUND, "amount": round(diff, 2), "txn_date": cut}}


# ---------------------------------------------------------------- satır düzeltmesi

def _load(c, sid: int) -> tuple[dict, list[dict]]:
    from app import book

    s = c.execute("SELECT * FROM statements WHERE id = ?", (sid,)).fetchone()
    if not s or not book.filter_statements([dict(s)]):
        raise KeyError(sid)
    return dict(s), db.rows(c.execute("SELECT * FROM transactions WHERE statement_id = ? ORDER BY txn_date, id", (sid,)))


def can_edit() -> bool:
    from app import statement_reread

    return statement_reread._can_apply()


def _check(st: dict, rows: list[dict]) -> dict:
    from app import book

    return evaluate(st, aggregate(rows), book.hide_fees(), rows)


def _signed(amount: float, txn_type: str) -> float:
    a = round(abs(float(amount)), 2)
    return -a if txn_type in CREDIT else a


def _day(v) -> str:
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError as e:
        raise FixError("Tarih geçersiz") from e


def _public_row(r: dict) -> dict:
    return {k: r.get(k) for k in ("id", "txn_date", "description", "amount", "txn_type", "is_deleted", "card",
                                  "installment_no", "orig_currency")}


def rows_of(sid: int) -> dict:
    """GET /api/statements/{id}/rows: ekstrenin satırları (gizlenenler hariç) ve güncel durumu."""
    from app import book

    with db.connect() as c:
        st, rows = _load(c, sid)
    hide = book.hide_fees()
    shown = [r for r in book.filter_card_txns([{**r, "statement_id": sid} for r in rows])
             if not (hide and r["txn_type"] == FEE)]
    return {"statement_id": sid, "rows": [_public_row(r) for r in shown], "check": _check(st, rows)}


def add_row(sid: int, body: dict, dry_run: bool = False) -> dict:
    """POST /api/statements/{id}/rows: eksik satırı ekle (tutar artı, işaret türden)."""
    from app import undo
    from app.categorize import categorize_sub

    kind = body.get("txn_type")
    if kind not in TYPES:
        raise FixError("Satır türü geçersiz")
    if not body.get("amount") or money.is_zero(float(body["amount"])):
        raise FixError("Tutar gir")
    with db.connect() as c:
        st, rows = _load(c, sid)
    row = {"txn_date": _day(body.get("txn_date") or st["period_end"]),
           "description": (body.get("description") or "").strip()[:120] or TYPE_LABEL[kind].capitalize(),
           "amount": _signed(body["amount"], kind), "txn_type": kind, "card": st["card_no"], "card_holder": "",
           "is_deleted": 0}
    after = _check(st, [*rows, {**row, "id": 0}])
    if dry_run:
        return {"ok": True, "dry_run": True, "check": after}
    label = f"Ekstreye satır eklendi: {st['bank']} {st['period_end']}"
    with undo.batch(label) as b:
        with db.connect() as c:
            rules = db.load_rules_sub(c)
            cat, sub = categorize_sub(row["description"], kind, rules)
            rec = {**row, "statement_id": sid, "category": cat, **({"subcategory": sub} if sub else {})}
            cur = c.execute(f"INSERT INTO transactions ({', '.join(rec)}) VALUES ({', '.join('?' * len(rec))})",
                            tuple(rec.values()))
            new_id = cur.lastrowid
        linked = db.auto_link() if kind == PURCHASE else 0
        b["summary"] = {"statement_id": sid, "added": 1, "linked": linked}
    return {"ok": True, "id": new_id, "undo_id": b["id"], "check": after}


def edit_row(sid: int, txn_id: int, body: dict, dry_run: bool = False) -> dict:
    """PATCH /api/statements/{id}/rows/{txn_id}: tutar (artı), tür, tarih, hesaba katma. Kategori / not değişmez."""
    from app import sync, undo
    from app.categorize import categorize_sub

    with db.connect() as c:
        st, rows = _load(c, sid)
    r = next((x for x in rows if x["id"] == txn_id), None)
    if r is None:
        raise KeyError(txn_id)
    kind = body.get("txn_type") or r["txn_type"]
    if kind not in TYPES:
        raise FixError("Satır türü geçersiz")
    amount = body.get("amount")
    if amount is not None and money.is_zero(float(amount)):
        raise FixError("Tutar gir")
    new = {"txn_type": kind, "amount": _signed(r["amount"] if amount is None else amount, kind)}
    if body.get("txn_date"):
        new["txn_date"] = _day(body["txn_date"])
    if body.get("is_deleted") is not None:
        new["is_deleted"] = int(bool(body["is_deleted"]))
    new = {k: v for k, v in new.items() if v != r.get(k)}
    after = _check(st, [{**x, **new} if x["id"] == txn_id else x for x in rows])
    if dry_run or not new:
        return {"ok": True, "dry_run": dry_run, "check": after}
    label = f"Ekstre satırı düzeltildi: {st['bank']} {st['period_end']}"
    with undo.batch(label) as b:
        leaves = new.get("txn_type", PURCHASE) != PURCHASE or new.get("is_deleted")
        unlinked = 0
        if leaves:  # harcama olmaktan çıkan / hesaba katılmayan satırın elle kayıt eşleşmesi ayrılır
            with db.connect() as c:
                eids = [e for (e,) in c.execute("SELECT id FROM entries WHERE linked_txn_id = ?", (txn_id,))]
            for eid in eids:
                try:
                    sync.unmatch(eid)
                    unlinked += 1
                except KeyError:
                    pass
        with db.connect() as c:
            if "txn_type" in new:
                cur = c.execute("SELECT category_manual FROM transactions WHERE id = ?", (txn_id,)).fetchone()
                if cur and not cur["category_manual"]:
                    new["category"], sub = categorize_sub(r["description"], kind, db.load_rules_sub(c))
                    new["subcategory"] = sub
            c.execute(f"UPDATE transactions SET {', '.join(f'{k} = ?' for k in new)} WHERE id = ?",
                      (*new.values(), txn_id))
        b["summary"] = {"statement_id": sid, "changed": 1, "unlinked": unlinked}
    return {"ok": True, "undo_id": b["id"], "check": after}
