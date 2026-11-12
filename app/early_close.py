"""Kart taksitlerini erken kapatma (yetenek "card_early_close"; PUT/DELETE /api/cards/early-close).

Kullanıcı bankayı arayıp vade farklı taksitlerini erken kapattırınca kalan taksitlerin hepsi bir sonraki ekstrede tek
seferde gelir, kalan vade farkı ve vergisi (BSMV/KKDF) silinir. Yeni ekstre gelene kadar Planlama, Özet, Kartlar ve Borç
planı son ekstrenin "kalan taksit" bilgisini (finance.card_projection) kullanır; bu modül o bilgiyi düzeltir.

Elle kayıt (defter ayarı SETTING, kart başına tek): {card_no: {period_end, month, amount, at}}. Kartın O ANKİ son
ekstresine (period_end) bağlıdır; daha yeni ekstre yüklenince kendiliğinden devre dışı kalır (projeksiyon yeni ekstreden;
kapama satırı tek çekim okunur: card_reader.close_installments). Etkin kayıtta son ekstrenin kalan taksitlerinden
`month` ve sonrasına düşenler: vade farkı/vergi taksitleri düşer, alışveriş taksitleri `month`ta tek satır "Taksit
erken kapama" olur (tutar: bankanın söylediği kapatma tutarı, yoksa kalan alışveriş taksitlerinin toplamı). `month`tan
önceki aylara düşen taksitler olduğu gibi kalır. Kazanç: tutar girildiyse kalan toplam (vade farkı dahil) − tutar,
yoksa düşen vade farkı/vergi toplamı; eksiye düşmez.

Ekstreden tanıma (kendiliğinden): kartın son ekstresinde (L) bir önceki ekstrenin (P) kalan taksitleri tek seferde
geldiyse — kapama satırı (card_reader.is_close_line) ya da P'deki alışverişin kalan dilimlerinin toplamı L'de tek
seferde (tek satır ya da aynı ekstrede tek tek) — kart için erken kapama tanınır. Kazanç = P'nin kalan vade farkı/vergi
taksitleri − L'de süren vade farkı/vergi taksitleri. P'ye bağlı elle kayıt varsa "ekstreyle doğrulandı" (çift sayım
yok: kayıt zaten devre dışı, tutar L'nin gerçek satırlarında). L'de kalan taksit gibi yazılmış kapanmış satırlar
(ör. "3/6" ama tutarı kalanların toplamı) projeksiyona girmez (skip_ids).

Ortak defter: veri okuma fonksiyonları süzüldüğü için gizli kartın kaydı da tanıması da görünmez; faiz gizliyse
(book.hide_fees) tutar ve kazanç alanları boş döner. Yazma: sahip ve tam yetkili üye (kart limiti gibi; app/book.py).

Değer ilkesi: erken kapatma faizden çıkıştır, önerilebilir; metinler kredi, taksitlendirme, erteleme önermez.
"""
import json
from datetime import datetime, timezone

from app import db, money
from app.card_reader import installment_base, is_close_line
from app.parsers.base import FEE, PAYMENT, PURCHASE

SETTING = "card_early_close"
TITLE = "Taksit erken kapama"
MAX_AHEAD = 3  # kapama en çok bu kadar ekstre sonrasına girilebilir
AYLAR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _r(x: float) -> float:
    return money.money(x)


def _add(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def month_label(month: str) -> str:
    y, m = month.split("-")
    return f"{AYLAR[int(m) - 1]} {y}"


def _hide_fees() -> bool:
    from app import book

    return book.hide_fees()


# ---------------------------------------------------------------- kayıt

def records() -> dict[str, dict]:
    raw = db.get_setting(SETTING)
    try:
        out = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def save(card_no: str, period_end: str, month: str, amount: float | None) -> dict:
    recs = records()
    old = recs.get(card_no) or {}
    recs[card_no] = {"period_end": period_end, "month": month, "amount": _r(amount) if amount else None,
                     "at": old.get("at") if old.get("period_end") == period_end else None}
    recs[card_no]["at"] = recs[card_no]["at"] or datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.set_setting(SETTING, json.dumps(recs, ensure_ascii=False))
    return recs[card_no]


def remove(card_no: str) -> bool:
    recs = records()
    if card_no not in recs:
        return False
    recs.pop(card_no)
    db.set_setting(SETTING, json.dumps(recs, ensure_ascii=False) if recs else None)
    return True


# ---------------------------------------------------------------- ekstreden tanıma

def _rest(t: dict) -> float:
    return (t["installments_left"] or 0) * (t["installment_left_amount"] or 0.0)


def _has_rest(t: dict) -> bool:
    return bool(t["installments_left"] and t["installment_left_amount"])


def detect(cur: list[dict], prev: list[dict]) -> dict | None:
    """Son ekstre (cur) bir önceki ekstrenin (prev) kalan taksitlerini tek seferde mi getirdi? Satırlar
    db.card_transactions biçiminde (silinenler hariç). Dönen: {skip_ids, saved, closed, by_line} ya da None."""
    buys = [t for t in prev if t["txn_type"] == PURCHASE and _has_rest(t)]
    if not buys:
        return None
    lines = [t for t in cur if t["txn_type"] != PAYMENT and is_close_line(t["description"])]
    skip: set[int] = {t["id"] for t in lines}
    closed = 0
    for b in buys:
        if b["installments_left"] < 2:
            continue  # tek dilim kaldıysa son taksitten ayırt edilemez
        base, of = installment_base(b["description"]), b["installment_no"] + b["installments_left"]
        if not base:
            continue
        same = [t for t in cur if t["txn_type"] == PURCHASE and t["id"] not in skip
                and installment_base(t["description"]) == base
                and (t["installment_no"] is None
                     or (t["installment_no"] > b["installment_no"] and t["installment_no"] + (t["installments_left"] or 0) == of))]
        got, want = sum(t["amount"] for t in same), _rest(b)
        if same and abs(money.diff_k(got, want)) <= max(100, money.kurus(want) // 100) and got > 1.5 * b["installment_left_amount"]:
            closed += 1
            skip |= {t["id"] for t in same}
    if not closed and not lines:
        return None
    fee_before = sum(_rest(t) for t in prev if t["txn_type"] == FEE and _has_rest(t))
    fee_now = sum(t["amount"] + _rest(t) for t in cur if t["txn_type"] == FEE and t["installment_no"] is not None
                  and t["id"] not in skip)
    return {"skip_ids": skip, "closed": closed, "by_line": bool(lines), "saved": _r(max(0.0, fee_before - fee_now))}


# ---------------------------------------------------------------- projeksiyon

def apply(card_no: str, st: dict, installments: list[dict], rec: dict | None, detected: dict | None,
          prev_period_end: str | None) -> tuple[list[dict], dict | None]:
    """Kartın son ekstresinden kurulan taksit dilimleri (finance.card_projection biçimi) → (düzeltilmiş dilimler, bilgi).
    bilgi (card_projection'da early_close; istemciye card_info ile gider): kalan toplamlar ham dilimlerden (kayıt
    öncesi), etkin kayıt, ekstreden tanıma."""
    buy = [i for i in installments if not i["fee"]]
    fee = [i for i in installments if i["fee"]]
    info = {"card_no": card_no, "latest_period": st["period"], "period_end": st["period_end"],
            "rest": _r(sum(i["amount"] for i in buy)), "fee_left": _r(sum(i["amount"] for i in fee)),
            "last_month": max((i["month"] for i in buy), default=None), "record": None, "detected": None}
    if detected:
        verified = bool(rec and prev_period_end and rec.get("period_end") == prev_period_end)
        info["detected"] = {"saved": detected["saved"], "verified": verified, "by_line": detected["by_line"]}
    active = rec and rec.get("period_end") == st["period_end"] and rec.get("month")
    m = rec["month"] if active else None
    closing = [i for i in buy if m and i["month"] >= m]
    if not closing:
        return installments, info if (buy or detected) else None
    keep = [i for i in installments if i["month"] < m]
    gone = [i for i in installments if i["month"] >= m]
    rest_buy = sum(i["amount"] for i in closing)
    amount = rec.get("amount") or rest_buy
    rest_all = sum(i["amount"] for i in gone)
    saved = max(0.0, rest_all - amount) if rec.get("amount") else sum(i["amount"] for i in gone if i["fee"])
    top = max(closing, key=lambda i: i["amount"])
    lump = {"month": m, "amount": _r(amount), "description": TITLE, "installment": None, "card": top["card"],
            "fee": False, "txn_id": top["txn_id"], "early_close": True,
            "count": len({i["txn_id"] for i in closing if i["amount"] > 0})}
    info["record"] = {"month": m, "label": month_label(m), "amount": rec.get("amount"), "total": _r(amount),
                      "rest": _r(rest_buy), "saved": _r(saved), "at": rec.get("at")}
    return [*keep, lump], info


def card_info(info: dict | None, bank: str | None = None) -> dict | None:
    """Kartlar ekranı ve Faiz sayfası için: eylem, kazanç tahmini, etkin kayıt, ekstreden tanıma (cards_summary
    early_close). Faiz gizliyse tutarlar ve kazanç metni yok."""
    if not info:
        return None
    hide = _hide_fees()
    rec, det = info["record"], info["detected"]
    first = _add(info["latest_period"], 1)
    last = info["last_month"]
    months = [] if not last else [{"month": m, "label": f"{month_label(m)} ekstresi"}
                                  for m in (_add(info["latest_period"], k) for k in range(1, MAX_AHEAD + 1)) if m <= last]
    fee_left = info["fee_left"] if not rec else 0.0
    out = {
        "period_end": info["period_end"],  # kaydın bağlı olduğu son ekstre
        "available": bool(months) or bool(rec),  # "Taksitleri erken kapattım" gösterilsin mi (kalan alışveriş taksiti var)
        "rest": info["rest"],
        "fee_left": None if hide else _r(fee_left),
        "hint": None if hide or rec or not money.gt(fee_left, 0) else
        f"Taksitleri erken kapatırsan en çok {money.tl(fee_left)} vade farkı ve vergi ödemezsin; kesin tutarı bankandan öğren.",
        "months": months, "default_month": first if months else None,
        "record": None, "detected": None,
    }
    if rec:
        saved = None if hide else rec["saved"]
        out["record"] = {**rec, "saved": saved,
                         "text": f"Kalan taksitler {rec['label']} ekstrende tek seferde: {money.tl(rec['total'])}.",
                         "saved_text": f"Erken kapama: {money.tl(saved)} vade farkından kurtuldun."
                         if saved is not None and money.gt(saved, 0) else None}
    if det:
        who = f"{bank} kartında" if bank else "Kartında"
        saved = None if hide else det["saved"]
        out["detected"] = {**det, "saved": saved,
                           "text": f"{who} taksitler erken kapandı" + (
                               f": {money.tl(saved)} vade farkından kurtuldun." if saved and money.gt(saved, 0) else ".")
                           + (" Ekstreyle doğrulandı." if det["verified"] else "")}
    if not out["available"] and not out["detected"]:
        return None
    return out
