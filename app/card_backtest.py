"""Kart ekstresi tahmininin geriye dönük sınaması (U3, 16.11): yüklenmiş ekstre geçmişinde her dönem için, yalnız o
dönemden ÖNCEKİ ekstrelerle yapılmış tahmin (finance.card_projection ile aynı kural: bir önceki ekstreden bilinen
taksitler + abonelikler + değişken harcama tahmini) gerçek dönem harcamasıyla karşılaştırılır.

Gerçek = o dönemin ekstresindeki alışveriş/iade satırları (taksitler dahil) + taksitli ücret satırları; devreden borç,
faiz, ödeme yok (tahminin kapsamıyla aynı). Ardışık olmayan dönem (arada ekstre yüklenmemiş) ve gerçeği çok küçük dönem
(< MIN_ACTUAL) atlanır. Yöntemler: "median" (eski: taksitsiz harcamanın son 6 dönem medyanı), "weighted" (yeni: ilk
taksitler dahil, son 3 dönem 3:2:1) ve karşılaştırma için ara adımlar. Kişisel veri dönmez: yalnız sayılar.

Kullanım: backtest() → {method: {n, mape, bias, mae}}; komut satırı: python -m app.card_backtest (EKSTRE_DB'deki defter).
"""
from collections import defaultdict
from statistics import mean, median

from app import db, finance
from app.parsers.base import FEE

MIN_ACTUAL = 500.0  # bu tutarın altındaki dönem (kart pek kullanılmamış) yüzde hatayı şişirir: atlanır
MIN_HISTORY = 3     # tahmin için en az kaç önceki dönem

METHODS = {
    # ad: (ilk taksitler değişkene dahil mi, tahmin yöntemi)
    "median": (False, "median"),
    "median_first_inst": (True, "median"),
    "weighted_no_first": (False, "weighted"),
    "weighted": (True, "weighted"),
}


def _is_var(t: dict, first: bool) -> bool:
    if first:
        return finance.is_variable_spend(t)
    return t["txn_type"] in finance.CARD_SPEND and t["category"] not in finance.SUBSCRIPTION_CATEGORIES \
        and t["installment_no"] is None


def _actual(rows: list[dict]) -> float:
    return sum(t["amount"] for t in rows
               if t["txn_type"] in finance.CARD_SPEND or (t["txn_type"] == FEE and t["installment_no"]))


def _known(prev: list[dict]) -> float:
    """Önceki ekstreden bu döneme düşen taksitler + abonelikler (card_projection'ın kesin kısmı, sade hâli)."""
    inst = sum((-1 if t["txn_type"] == finance.REFUND else 1) * t["installment_left_amount"] for t in prev
               if t["installments_left"] and t["installment_left_amount"] and t["txn_type"] in (*finance.CARD_SPEND, FEE))
    subs = sum(t["amount"] for t in prev if t["category"] in finance.SUBSCRIPTION_CATEGORIES
               and t["txn_type"] == finance.PURCHASE and t["installment_no"] is None)
    return inst + subs


def cases(txns: list[dict] | None = None) -> list[dict]:
    """[{card, period, actual, known, hist_first: [...], hist_plain: [...]}] — her kartın her uygun dönemi."""
    txns = db.card_transactions() if txns is None else txns
    by: dict[tuple, list] = defaultdict(list)
    for t in txns:
        by[(t["account"], t["period"])].append(t)
    out = []
    for acc in sorted({a for a, _ in by}):
        periods = sorted(p for a, p in by if a == acc)
        for i, p in enumerate(periods):
            if i < MIN_HISTORY or finance.add_months(periods[i - 1], 1) != p:
                continue
            actual = _actual(by[(acc, p)])
            if actual < MIN_ACTUAL:
                continue
            past = periods[max(0, i - finance.VARIABLE_LOOKBACK):i]
            out.append({"card": acc, "period": p, "actual": actual, "known": _known(by[(acc, periods[i - 1])]),
                        "hist_first": [sum(t["amount"] for t in by[(acc, q)] if _is_var(t, True)) for q in past],
                        "hist_plain": [sum(t["amount"] for t in by[(acc, q)] if _is_var(t, False)) for q in past]})
    return out


def score(cs: list[dict]) -> dict[str, dict]:
    """Yöntem başına: n, mape (ortalama mutlak yüzde hata, %), bias (ortalama işaretli yüzde hata; eksi = düşük tahmin),
    mae (ortalama mutlak hata, TL), median_ape."""
    res = {}
    for name, (first, method) in METHODS.items():
        errs = []
        for c in cs:
            pred = c["known"] + finance.variable_estimate(c["hist_first"] if first else c["hist_plain"], method)
            errs.append((pred - c["actual"], (pred - c["actual"]) / c["actual"]))
        if not errs:
            res[name] = {"n": 0}
            continue
        res[name] = {"n": len(errs), "mape": round(100 * mean(abs(p) for _, p in errs), 1),
                     "median_ape": round(100 * median(abs(p) for _, p in errs), 1),
                     "bias": round(100 * mean(p for _, p in errs), 1), "mae": round(mean(abs(a) for a, _ in errs), 0)}
    return res


def backtest(txns: list[dict] | None = None) -> dict[str, dict]:
    return score(cases(txns))


if __name__ == "__main__":  # pragma: no cover - elle çalıştırılır (canlı verinin KOPYASINDA)
    import json

    print(json.dumps(backtest(), ensure_ascii=False))
