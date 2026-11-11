"""Kumbara hedef durumu: kalan, kalan ay, ayda ayırman gereken (yuvarlı), son aylardaki gerçek ayırma hızı ve durum.

YNAB "sinking fund" / Monarch "Save up" kalıbı: hedef + son tarih → "ayda ₺2.500 ayır"; gerçek hız buna yetiyor mu?
Ürün ya da faiz önerisi yok; yalnız hesap.

Durumlar:
  tamam     — hedefe ulaşıldı
  yolunda   — bu hızla son tarihte dolar
  onde      — bu hızla son tarihten en az bir ay önce dolar (eta / months_early)
  geride    — bu hızla yetişmez (raise_by: ayda ne kadar artırmalı); son tarih geçtiyse deadline_passed
  tarihsiz  — son tarih yok: bu hızla tahmini ulaşma ayı (eta; hız yoksa None)

Altın/döviz kumbarasında tutarlar kumbaranın biriminde (gram, adet, $); unit_price verilirse TL karşılığı yaklaşık.
Kumbara hatırlatması (hedef tarihi yaklaşınca push) bu fonksiyonu kullanır: jar_goal_status(jar, today, unit_price).
"""
from __future__ import annotations

import math
from datetime import date

# Kumbara açılırken konan / eski hedeften aktarılan: birikim hızı sayılmaz
INITIAL_NOTES = ("Başlangıç", "Hedeften aktarıldı")
PACE_MONTHS = 3  # son 3 tam ay (+ bu ay hareket varsa bu ay)


def _add_months(ym: str, n: int) -> str:
    y, m = map(int, ym.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def _step(amount: float, unit: str) -> float:
    """Yuvarlama adımı: TL'de 10/50/100; gram ve adette 0,1; dövizde 1/10."""
    if unit == "TRY":
        return 10 if amount < 1000 else 50 if amount < 10000 else 100
    if unit in ("USD", "EUR", "GBP"):
        return 1 if amount < 100 else 10
    return 0.1


def _ceil(amount: float, unit: str) -> float:
    if amount <= 0:
        return 0.0
    s = _step(amount, unit)
    return round(math.ceil(round(amount / s, 6)) * s, 2)


def _round(amount: float, unit: str) -> float:
    if amount <= 0:
        return 0.0
    s = _step(amount, unit)
    return round(round(amount / s) * s, 2) or round(s, 2)


def _actual_pace(moves: list[dict], today: date) -> tuple[float, int]:
    """Son aylardaki gerçek ayırma hızı (ay başına net; çekilen düşülür) ve kaç aya bakıldığı (0 = hiç hareket yok).
    Pencere: son 3 tam ay + bu ayda hareket varsa bu ay; ilk gerçek hareketten önceki aylar sayılmaz
    (yeni açılan kumbara ilk ayında düşük görünmesin)."""
    real = [m for m in moves if m.get("note") not in INITIAL_NOTES]
    if not real:
        return 0.0, 0
    cur = today.strftime("%Y-%m")
    first = min(m["move_date"][:7] for m in real)
    months = [_add_months(cur, -i) for i in range(PACE_MONTHS, 0, -1)]
    if any(m["move_date"][:7] == cur for m in real):
        months.append(cur)
    months = [m for m in months if m >= first] or [cur]
    net = sum(m["amount"] for m in real if m["move_date"][:7] in months)
    return max(0.0, net / len(months)), len(months)


def jar_goal_status(jar: dict, today: date | None = None, unit_price: float | None = None) -> dict | None:
    """Kumbaranın hedef durumu. Hedef yoksa ya da kumbara kapalıysa None.

    jar: db.list_jars() satırı (goal, deadline, balance, moves, monthly, unit, closed_at).
    unit_price: altın/döviz kumbarasında 1 birimin TL fiyatı (TL karşılıkları için; yoksa *_try alanları None).
    Döner: status, left, months_left, needed_monthly, pace, pace_source, raise_by, eta, eta_months,
    months_early, deadline_passed, unit ve altın/döviz için left_try, needed_monthly_try, raise_by_try."""
    today = today or date.today()
    goal = jar.get("goal")
    if not goal or jar.get("closed_at"):
        return None
    unit = jar.get("unit") or "TRY"
    balance = float(jar.get("balance") or 0)
    left = max(0.0, goal - balance)
    pace, seen = _actual_pace(jar.get("moves") or [], today)
    source = "hareket" if seen else None
    if not seen and jar.get("monthly"):  # henüz hiç ayırma yok: kurulan aylık plan
        pace, source = float(jar["monthly"]), "plan"
    out: dict = {
        "status": None, "unit": unit, "left": round(left, 3), "months_left": None, "needed_monthly": None,
        "pace": _round(pace, unit) if pace else 0.0, "pace_source": source, "raise_by": None,
        "eta": None, "eta_months": None, "months_early": None, "deadline_passed": False,
    }
    cur = today.strftime("%Y-%m")
    if left <= 1e-9:
        out["status"] = "tamam"
    else:
        if pace > 0:
            n = math.ceil(round(left / pace, 6))
            out["eta_months"], out["eta"] = n, _add_months(cur, n)
        if not jar.get("deadline"):
            out["status"] = "tarihsiz"
        else:
            dl = date.fromisoformat(str(jar["deadline"])[:10])
            diff = (dl.year - today.year) * 12 + dl.month - today.month
            if dl < today:  # son tarih geçti, hedef dolmadı: kalanın hepsi şimdi
                out.update(status="geride", deadline_passed=True, months_left=0, needed_monthly=_ceil(left, unit))
            else:
                months = max(1, diff)
                need = left / months
                out["months_left"], out["needed_monthly"] = months, _ceil(need, unit)
                if pace + 1e-9 < need:
                    out["status"] = "geride"
                    out["raise_by"] = _ceil(need - pace, unit)
                elif out["eta_months"] is not None and out["eta_months"] < months:
                    out["status"], out["months_early"] = "onde", months - out["eta_months"]
                else:
                    out["status"] = "yolunda"
    if unit != "TRY":
        def tl(v):
            return _round(v * unit_price, "TRY") if v and unit_price else None  # yaklaşık: 10/50/100'e yuvarlı
        out.update(left_try=tl(left), needed_monthly_try=tl(out["needed_monthly"]), raise_by_try=tl(out["raise_by"]),
                   pace_try=tl(out["pace"]) if out["pace"] else None)
    return out
