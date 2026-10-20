"""Brütten nete maaş (Türkiye, ücretli). Yıl yıl yasal parametreler; yıl başında güncellenmeli.

Hesap (her ay):
  SGK işçi %14 + işsizlik %1 (tavana kadar) → gelir vergisi matrahı
  Gelir vergisi: yıl içi KÜMÜLATİF matraha artan oranlı tarife (dilim yükseldikçe net düşer)
  Asgari ücret istisnası: asgari ücretin kümülatif matrahına düşen vergi ve damga vergisi kadar indirim
  Damga vergisi: brütün binde 7,59'u
Kaynaklar (2026): GVK 2026 ücret tarifesi (Resmi Gazete), brüt asgari ücret 33.030 TL, SGK tavanı 297.270 TL.
"""

PARAMS = {
    2026: {
        "brackets": [(190_000, 0.15), (400_000, 0.20), (1_500_000, 0.27), (5_300_000, 0.35), (None, 0.40)],
        "min_wage": 33_030.00,
        "sgk_cap": 297_270.00,
        "sgk": 0.14,
        "unemployment": 0.01,
        "stamp": 0.00759,
    },
}
LATEST = max(PARAMS)


def params(year: int) -> tuple[int, dict]:
    """Parametresi olmayan yıl için en yakın (en son) yıl kullanılır; arayüz bunu belirtir."""
    y = year if year in PARAMS else (LATEST if year > LATEST else min(PARAMS))
    return y, PARAMS[y]


def income_tax(cum: float, brackets) -> float:
    """Kümülatif matraha göre toplam gelir vergisi."""
    tax, lower = 0.0, 0.0
    for upper, rate in brackets:
        if upper is None or cum <= upper:
            return tax + (cum - lower) * rate
        tax += (upper - lower) * rate
        lower = upper
    return tax


def _r(x: float) -> float:
    """Bordrodaki gibi kuruşa yarım-yukarı yuvarla (4.211,325 → 4.211,33)."""
    from decimal import ROUND_HALF_UP, Decimal

    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def net_by_month(gross: float, year: int, start_month: int = 1, prior_cum_base: float = 0.0) -> dict:
    """Yılın her ayı için brüt → net dökümü. start_month: işe başlama/hesap başlangıç ayı."""
    used_year, p = params(year)
    b = p["brackets"]
    mw_base = p["min_wage"] * (1 - p["sgk"] - p["unemployment"])  # asgari ücretin aylık GV matrahı
    cum = prior_cum_base
    months = []
    for m in range(start_month, 13):
        sgk_base = min(gross, p["sgk_cap"])
        sgk = _r(sgk_base * p["sgk"])
        unemp = _r(sgk_base * p["unemployment"])
        base = _r(gross - sgk - unemp)
        tax = _r(income_tax(cum + base, b) - income_tax(cum, b))
        k = m - start_month  # asgari ücret istisnası da kendi kümülatifiyle
        exemption = _r(income_tax(mw_base * (k + 1), b) - income_tax(mw_base * k, b))
        tax_net = max(0.0, _r(tax - exemption))
        stamp = max(0.0, _r(_r(gross * p["stamp"]) - _r(p["min_wage"] * p["stamp"])))
        cum += base
        net = gross - sgk - unemp - tax_net - stamp
        rate = next(r for upper, r in b if upper is None or cum <= upper)
        months.append({"month": m, "gross": _r(gross), "sgk": _r(sgk), "unemployment": _r(unemp),
                       "income_tax": _r(tax_net), "stamp": _r(stamp), "net": _r(net),
                       "cumulative_base": _r(cum), "bracket_rate": rate})
    return {"year": year, "params_year": used_year, "months": months,
            "annual_net": _r(sum(x["net"] for x in months))}


def net_for(gross: float, year: int, month: int) -> float:
    return next(x["net"] for x in net_by_month(gross, year)["months"] if x["month"] == month)
