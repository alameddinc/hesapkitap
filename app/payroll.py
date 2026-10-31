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
    from app import money

    return money.money(x)


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


def gross_for_net(net: float, year: int) -> float:
    """Neti bilinen maaşın brüt karşılığı: yıllık ortalama neti bu nete eşit olan sabit brüt (ikili arama, kuruş).
    Netle tanımlı maaşa yıl ortasında brütlü zam gelince, zamdan önceki aylar bu brütle sayılır; yoksa zamlı brüt
    yılın başına da yazılır ve vergi matrahı şişer."""
    if net <= 0:
        return 0.0
    lo, hi = net, net * 3
    for _ in range(60):
        mid = (lo + hi) / 2
        if net_by_month(mid, year)["annual_net"] / 12 < net:
            lo = mid
        else:
            hi = mid
    return _r(hi)


def net_for(gross: float, year: int, month: int) -> float:
    return next(x["net"] for x in net_by_month(gross, year)["months"] if x["month"] == month)


def net_schedule(year: int, gross_by_month: dict[int, float], resets: set[int] | None = None) -> dict:
    """Ay ay farklı brütle (zam, iş değişikliği) yıl boyu net. resets: vergi matrahının sıfırdan başladığı aylar
    (yeni işveren önceki matrahı devralmadıysa); asgari ücret istisnası sayacı da orada yeniden başlar.
    Brütü 0 olan ay (iki iş arası) atlanır."""
    used_year, p = params(year)
    b = p["brackets"]
    mw_base = p["min_wage"] * (1 - p["sgk"] - p["unemployment"])
    cum, k = 0.0, 0
    months = []
    for m in range(1, 13):
        if resets and m in resets:
            cum, k = 0.0, 0
        gross = gross_by_month.get(m) or 0.0
        if gross <= 0:
            months.append({"month": m, "gross": 0.0, "sgk": 0.0, "unemployment": 0.0, "income_tax": 0.0, "stamp": 0.0,
                           "net": 0.0, "cumulative_base": _r(cum), "bracket_rate": None})
            continue
        sgk_base = min(gross, p["sgk_cap"])
        sgk = _r(sgk_base * p["sgk"])
        unemp = _r(sgk_base * p["unemployment"])
        base = _r(gross - sgk - unemp)
        tax = _r(income_tax(cum + base, b) - income_tax(cum, b))
        exemption = _r(income_tax(mw_base * (k + 1), b) - income_tax(mw_base * k, b))
        tax_net = max(0.0, _r(tax - exemption))
        stamp = max(0.0, _r(_r(gross * p["stamp"]) - _r(p["min_wage"] * p["stamp"])))
        cum += base
        k += 1
        rate = next(r for upper, r in b if upper is None or cum <= upper)
        months.append({"month": m, "gross": _r(gross), "sgk": sgk, "unemployment": unemp, "income_tax": tax_net,
                       "stamp": stamp, "net": _r(gross - sgk - unemp - tax_net - stamp),
                       "cumulative_base": _r(cum), "bracket_rate": rate})
    return {"year": year, "params_year": used_year, "months": months,
            "annual_net": _r(sum(x["net"] for x in months))}
