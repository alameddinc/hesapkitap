"""İş günü ve resmî tatiller (Türkiye): hafta sonu/bayram kaymaları için tek kaynak.

Kullananlar: son ödeme "gecikti" kararı (finance._status, debt_plan.card_interest, finance._due_this_period,
reminders._odeme), "maaş yatmadı mı?" bildirimi (push._gelir), kart kesim gününün kaymayla mı değiştiği
(usual_day: reminders._cut_rule, finance._next_cut, db.suggested_cycle_day).

Kural: son ödeme tatile (hafta sonu / resmî tatil) denk gelirse ilk iş günü son ödeme sayılır (Banka Kartları ve
Kredi Kartları Yönetmeliği; faturalarda da çoğu kurum böyle). Arife yarım gündür: İŞ GÜNÜ sayılır (bankalar açık).

Tablo tek yerde (yönetici ileride buraya / ayara ekleyebilir): FIXED_HOLIDAYS + RELIGIOUS_HOLIDAYS (+ EXTRA_HOLIDAYS:
idari izin, köprü günü gibi tek seferlik eklemeler). Tabloda olmayan yılın dini bayramları app.zakat.next_hijri ile
YAKLAŞIK hesaplanır (±1 gün).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from functools import lru_cache

# 2429 sayılı Ulusal Bayram ve Genel Tatiller Hakkında Kanun: tam gün tatiller (28 Ekim öğleden sonra yarım gün →
# iş günü sayılır). (ay, gün)
FIXED_HOLIDAYS: tuple[tuple[int, int], ...] = (
    (1, 1),    # Yılbaşı
    (4, 23),   # Ulusal Egemenlik ve Çocuk Bayramı
    (5, 1),    # Emek ve Dayanışma Günü
    (5, 19),   # Atatürk'ü Anma, Gençlik ve Spor Bayramı
    (7, 15),   # Demokrasi ve Milli Birlik Günü
    (8, 30),   # Zafer Bayramı
    (10, 29),  # Cumhuriyet Bayramı
)

# Dini bayramların 1. günü (Ramazan 3 gün, Kurban 4 gün tatil; arife iş günü). Kaynak: T.C. Diyanet İşleri Başkanlığı
# Vakit Hesaplama "Dini Günler Listesi" (vakithesaplama.diyanet.gov.tr/dinigunler.php?yil=2026; icerik.php?icerik=154
# (2027), 185 (2028), 186 (2029), 187 (2030)), 16.11.2026'da okundu.
RELIGIOUS_HOLIDAYS: dict[int, dict[str, str]] = {
    2026: {"ramazan": "2026-03-20", "kurban": "2026-05-27"},  # arife 19 Mar Per / 26 May Sal
    2027: {"ramazan": "2027-03-09", "kurban": "2027-05-16"},  # arife 8 Mar Pzt / 15 May Cmt
    2028: {"ramazan": "2028-02-26", "kurban": "2028-05-05"},  # arife 25 Şub Cum / 4 May Per
    2029: {"ramazan": "2029-02-14", "kurban": "2029-04-24"},  # arife 13 Şub Sal / 23 Nis Pzt
    2030: {"ramazan": "2030-02-04", "kurban": "2030-04-13"},  # arife 3 Şub Paz / 12 Nis Cum
}
FEAST_DAYS = {"ramazan": 3, "kurban": 4}

# tek seferlik ek tatiller (idari izin / köprü günü, resmî ilanla): ISO tarih
EXTRA_HOLIDAYS: set[str] = set()


def _religious_first_days(year: int) -> dict[str, date]:
    t = RELIGIOUS_HOLIDAYS.get(year)
    if t:
        return {k: date.fromisoformat(v) for k, v in t.items()}
    from app import zakat  # tabloda yok: hicri takvimden yaklaşık (±1 gün)

    out = {}
    for key, (m, d) in {"ramazan": (10, 1), "kurban": (12, 10)}.items():
        x = zakat.next_hijri(m, d, date(year, 1, 1))
        if x.year == year:
            out[key] = x
    return out


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset[date]:
    """O yılın tam gün resmî tatilleri (hafta sonu hariç)."""
    out = {date(year, m, d) for m, d in FIXED_HOLIDAYS}
    for key, first in _religious_first_days(year).items():
        out |= {first + timedelta(days=i) for i in range(FEAST_DAYS[key])}
    out |= {date.fromisoformat(x) for x in EXTRA_HOLIDAYS if x[:4] == str(year)}
    return frozenset(out)


def _d(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def is_business_day(d) -> bool:
    d = _d(d)
    return d.weekday() < 5 and date(d.year, d.month, d.day) not in holidays(d.year)


def next_business_day(d) -> date:
    """d iş günüyse kendisi, değilse sonraki ilk iş günü."""
    d = _d(d)
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def prev_business_day(d) -> date:
    d = _d(d)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def add_business_days(d, n: int) -> date:
    """d'den sonraki n'inci iş günü (d iş günü değilse önce sonraki iş gününe gelir)."""
    d = next_business_day(d)
    for _ in range(n):
        d = next_business_day(d + timedelta(days=1))
    return d


def past_due(due, today) -> bool:
    """Son ödeme günü geçti mi: son ödeme tatile denk geldiyse ilk iş günü sayılır (o gün de geçmedikçe False)."""
    if not due:
        return False
    return next_business_day(due) < _d(today)


def shift_explains(nominal: date, actual: date) -> bool:
    """actual, nominal günün tatil yüzünden kaymış hâli mi (sonraki ya da önceki iş günü)?"""
    return actual in (next_business_day(nominal), prev_business_day(nominal))


def usual_day(dates: list) -> int | None:
    """Ayın olağan günü (kart kesimi gibi): son 6 tarihin en sık günü, eşitlikte en yenisi. En son tarihin günü
    farklıysa ve fark tatille AÇIKLANMIYORSA (banka günü değiştirdi) en son tarihin günü. Ör. 13, 13, 13, 15 (13
    Cumartesi → 15 Pazartesi) → 13; 13, 13, 20 → 20."""
    ds = sorted(_d(x) for x in dates)[-6:]
    if not ds:
        return None
    cnt = Counter(d.day for d in ds)
    best = max(cnt.values())
    usual = next(d.day for d in reversed(ds) if cnt[d.day] == best)
    last = ds[-1]
    if last.day == usual:
        return usual
    import calendar

    prev = (last.year - 1, 12) if last.month == 1 else (last.year, last.month - 1)
    for y, m in ((last.year, last.month), prev):  # ay sonu kesimi sonraki ayın başına kayabilir (30 → 1)
        nominal = date(y, m, min(usual, calendar.monthrange(y, m)[1]))
        if shift_explains(nominal, last):
            return usual
    return last.day
