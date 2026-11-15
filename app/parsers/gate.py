"""Okuma tutarlılık kapısı: kural okuması (bankaya özel parser ya da kart şablonu) kaydedilmeden önce denetlenir.

Olay (Kasım 2026): VakıfBank tutar biçimini Türkçe biçime (53.888,98) çevirdi; eski parser "53.88"de kesti, işlem
satırlarını hiç tanımadı ve sessizce "0 işlem, dönem borcu ₺53,27" kaydetti. Biçim kayması her banka okuyucusunda
olabilir; kapı bunu kayıttan önce yakalar (app/parsers/__init__.py parse_statement):

- islem_yok: özetteki dönem içi işlemler > 0 ama okunan işlem yok.
- olcek: binlik ayraç kayması işareti — bir taraf (özet ya da işlemler) 100.000 TL'yi aşarken öbür taraftaki her
  tutar 1.000 TL'nin altında ve ikisinin oranı ~1000 kat (OLCEK_ORAN).
- denklem: önceki bakiye − ödemeler + dönem içi işlemler (+ faiz) ≈ dönem borcu (±TOL). base.equation faizin ve
  iadelerin özetin içinde/ayrı olduğu biçimleri kendisi dener.
- islem_toplami: okunan işlemlerin toplamı (ödeme hariç) ≈ dönem içi işlemler (±TOL; Statement.reconciliation_diff).

Sıra en özelden en genele: alarmdaki "hangi kontrol" kaymanın türünü söylesin. Ölçütler bugünkü doğrulamayla
(app/verification.py statement_check: denklem + işlem toplamı) uyumlu; kapı ek olarak 0 işlem ve ölçek kaymasını
"Rakamlar tutmadı"dan ayırır. Kapı yalnız sayılara bakar, metin ya da kişisel veri okumaz.
"""
from dataclasses import dataclass, field

from app import money
from app.parsers.base import PAYMENT, Statement, equation

TOL = 1.0                  # TL: denklem ve işlem toplamı farkı
OLCEK_KUCUK = 1_000.0      # "küçük" taraftaki her tutar bunun altında …
OLCEK_BUYUK = 100_000.0    # … büyük taraf bunun üstünde …
OLCEK_ORAN = 500.0         # … ve ikisinin oranı en az bu (binlik ayraç kayması ≈ 1000 kat)

ISLEM_YOK, OLCEK, DENKLEM, ISLEM_TOPLAMI = "islem_yok", "olcek", "denklem", "islem_toplami"
CHECKS = (ISLEM_YOK, OLCEK, DENKLEM, ISLEM_TOPLAMI)


@dataclass(frozen=True)
class GateResult:
    ok: bool
    check: str | None = None          # patlayan ilk kontrol (CHECKS), geçtiyse None
    numbers: dict = field(default_factory=dict)  # yalnız sayılar: alarm ve günlük için (isim/açıklama yok)


def _r(v: float | None) -> float:
    return round(float(v or 0.0), 2)


def numbers(st: Statement) -> dict:
    """Kapının baktığı sayılar (alarmda gösterilir): işlem sayısı ve özet / işlem toplamları. Açıklama, kart no,
    isim, işyeri yok."""
    charges = [t.amount for t in st.transactions if t.txn_type != PAYMENT]
    eq = equation(st.previous_balance, st.payments, st.period_activity, st.fees(), st.total_debt, st.refunds())
    return {
        "islem": len(st.transactions),
        "donem_borcu": _r(st.total_debt),
        "onceki": _r(st.previous_balance),
        "odemeler": _r(st.payments),
        "donem_ici": _r(st.period_activity),
        "faiz": _r(st.fees()),
        "islem_toplami": _r(sum(charges)),
        "en_buyuk_islem": _r(max((abs(t.amount) for t in st.transactions), default=0.0)),
        "denklem_farki": eq["diff"] if eq["diff"] is not None else None,
        "islem_farki": _r(st.reconciliation_diff()),
    }


def _scale_shift(st: Statement, n: dict) -> bool:
    if not st.transactions:
        return False
    biggest_txn = n["en_buyuk_islem"]
    summary = max(abs(n["donem_ici"]), abs(n["donem_borcu"]))
    txn_side = max(abs(n["islem_toplami"]), money.CENT)
    # özet büyük, işlemler küçük: işlem tutarları binlik ayraçta kesilmiş
    if summary > OLCEK_BUYUK and biggest_txn < OLCEK_KUCUK and summary / txn_side >= OLCEK_ORAN:
        return True
    # işlemler büyük, özet küçük: özet tutarları kesilmiş
    return (abs(n["islem_toplami"]) > OLCEK_BUYUK and 0 < summary < OLCEK_KUCUK
            and abs(n["islem_toplami"]) / summary >= OLCEK_ORAN)


def check(st: Statement) -> GateResult:
    """Okuma kayda uygun mu: GateResult(ok, check, numbers). Hata fırlatmaz (bozuk değerde ok=False, check denklem)."""
    try:
        n = numbers(st)
    except (TypeError, ValueError):
        return GateResult(False, DENKLEM, {})
    if not st.transactions and n["donem_ici"] > TOL:
        return GateResult(False, ISLEM_YOK, n)
    if _scale_shift(st, n):
        return GateResult(False, OLCEK, n)
    if n["denklem_farki"] is not None and abs(n["denklem_farki"]) > TOL:
        return GateResult(False, DENKLEM, n)
    if abs(n["islem_farki"]) > TOL:
        return GateResult(False, ISLEM_TOPLAMI, n)
    return GateResult(True, None, n)
