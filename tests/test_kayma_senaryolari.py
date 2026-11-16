"""Hafta sonu / bayram kaymaları (maaş, kart kesimi, son ödeme, düzenli ödeme, abonelik, fatura). Ay dönümü 17:
anahtar 2026-08 = 17 Tem – 16 Ağu. Maaş ayın 15'i → 2026-08 döneminin maaşı 15 Ağustos'tur (db.occurrence_date).

Kural (beklenen): bir oluşum (maaş, kira, kesim) birkaç gün erken/geç gelse de AİT OLDUĞU dönem beklenen gününün
dönemidir; her dönem tam bir kez sayılır, "gecikti/yatmadı" uyarısı hafta sonu/bayram yüzünden erken çıkmaz.
Kalan kırmızılar xfail(strict=True): düzeltme gelince XPASS olur, işaret kaldırılır. İş günü / tatil: app/workdays.py.

Takvim: 15 Ağu 2026 Cumartesi (17'si Pazartesi); 13 Haz 2026 Cumartesi; 16 May 2026 Cumartesi; 24 Eki 2026
Cumartesi; 30 Eki 2027 Cumartesi; 15 May 2027 Cumartesi. 2027 Kurban Bayramı 16–19 Mayıs (
Diyanet: arife 15 Mayıs, bayram 16–19 Mayıs; app/workdays.RELIGIOUS_HOLIDAYS) → ilk iş günü 20 Mayıs Perşembe.
Bütün kişi/işyeri/tutar uydurmadır."""
from datetime import date

import pytest

from app import finance, push, reminders, sub_detail, workdays
from app.parsers.base import PURCHASE, Statement, Transaction

CYCLE = 17
SALARY = 50000.0
RENT = 20000.0
BANK_SALARY_TEXT = "MAAS ODEMESI ORNEK TEKSTIL AS"


# ---------------------------------------------------------------- kurulum

@pytest.fixture()
def book(tmpdb):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(CYCLE)
    return tmpdb


def _freeze(monkeypatch, d: date) -> None:
    """finance içindeki date.today() (pending_recurring 'geçmiş dönem' kararı) sabit güne."""
    class _D(date):
        @classmethod
        def today(cls):
            return d
    monkeypatch.setattr(finance, "date", _D)


def _salary_rec(db, start="2026-05") -> int:
    return db.create_recurring({"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                                "day_of_month": 15, "start_month": start})


def _salary(db, d: str, source: str = "bank") -> int:
    """bank: içe aktarma yolu (create_entry + link_salary_entries, app/db.py import sonu); manual: elle 'Maaş'."""
    if source == "bank":
        eid = db.create_entry({"entry_date": d, "description": BANK_SALARY_TEXT, "amount": SALARY, "kind": "gelir",
                               "category": "Diğer Gelir", "import_hash": f"h-maas-{d}"})
        db.link_salary_entries()
        return eid
    return db.create_entry({"entry_date": d, "description": "Maaş", "amount": SALARY, "kind": "gelir",
                            "category": "Maaş"})


def _entry(db, eid: int) -> dict:
    return next(e for e in db.list_entries() if e["id"] == eid)


def _salary_counts(month: str) -> tuple[int, int]:
    """(gerçek maaş satırı, 'tahmini, döküm yok' maaş satırı) sayısı."""
    rows = [r for r in finance.transactions(month) if r["kind"] == finance.INCOME and not r.get("pending")]
    est = sum(1 for r in rows if r.get("salary_estimate"))
    real = sum(1 for r in rows if not r.get("salary_estimate") and finance._salary_entry(r, set()))
    return real, est


def _stmt(db, card: str, bank: str, pe: date, due: date, total: float = 3000.0, next_pe: date | None = None) -> int:
    s = Statement(bank=bank, card_no=card, period_end=pe, due_date=due, total_debt=total, min_payment=total * 0.2,
                  credit_limit=50000, period_activity=total, next_period_end=next_pe)
    s.transactions = [Transaction(txn_date=pe.replace(day=5), description="ORNEK MARKET", amount=total,
                                  txn_type=PURCHASE, card=card)]
    return db.save_statement(f"st-{card}-{pe}".encode(), "e.pdf", s)




# ---------------------------------------------------------------- 1–3, 7: maaş kayması → hangi dönem

@pytest.mark.parametrize("pay_date,want", [
    pytest.param("2026-08-13", "2026-08", id="s2-erken-13-bayram"),
    pytest.param("2026-08-17", "2026-08", id="s1-pazartesi-17-banka"),
    pytest.param("2026-08-18", "2026-08", id="s3-uzun-bayram-18-banka"),
    pytest.param("2027-05-14", "2027-05", id="s7-kurban-oncesi-cuma-14"),
    pytest.param("2027-05-20", "2027-05", id="s7-kurban-sonrasi-persembe-20-banka"),
])
def test_bank_salary_belongs_to_expected_period(book, pay_date, want):
    _salary_rec(book)
    e = _entry(book, _salary(book, pay_date, "bank"))
    assert e["category"] == "Maaş"
    assert (e["month"], e["recurring_id"] is not None) == (want, True)


@pytest.mark.parametrize("pay_date,want", [
    ("2026-08-13", "2026-08"), ("2026-08-17", "2026-08"), ("2026-08-18", "2026-08"),
    ("2027-05-14", "2027-05"), ("2027-05-20", "2027-05"),
])
def test_manual_salary_belongs_to_expected_period(book, pay_date, want):
    """Elle 'Maaş': _attach_recurring en yakın boş dönem (geçiyor — referans davranış)."""
    _salary_rec(book)
    e = _entry(book, _salary(book, pay_date, "manual"))
    assert (e["month"], e["recurring_id"] is not None) == (want, True)


@pytest.mark.parametrize("source,late,nxt,months", [
    pytest.param("manual", "2026-08-17", "2026-09-15", ("2026-08", "2026-09"), id="s1-elle"),
    pytest.param("manual", "2026-08-18", "2026-09-15", ("2026-08", "2026-09"), id="s3-elle"),
    pytest.param("bank", "2026-08-17", "2026-09-15", ("2026-08", "2026-09"), id="s1-banka"),
    pytest.param("bank", "2026-08-18", "2026-09-15", ("2026-08", "2026-09"), id="s3-banka"),
    pytest.param("bank", "2027-05-20", "2027-06-15", ("2027-05", "2027-06"), id="s7-banka"),
])
def test_each_period_exactly_one_salary(book, monkeypatch, source, late, nxt, months):
    """Geç maaş + sonraki ayın zamanında maaşı: iki dönemde de TAM BİR maaş; 'tahmini, döküm yok' yok, çift yok.
    Şu an banka yolunda: geç dönem tahmini maaş (gerçek gelire sayılır) + sonraki dönem iki gerçek maaş = 3 maaş."""
    a, b = months
    _freeze(monkeypatch, date.fromisoformat(nxt).replace(day=25))  # ikisi de geçmiş dönem
    _salary_rec(book)
    _salary(book, late, source)
    _salary(book, nxt, source)
    assert {m: _salary_counts(m) for m in (a, b)} == {a: (1, 0), b: (1, 0)}
    for m in (a, b):
        assert finance.summary(m)["totals"]["income"] == SALARY


@pytest.mark.parametrize("source", ["manual", pytest.param("bank")])
def test_late_salary_current_period_still_expects_own_salary(book, monkeypatch, source):
    """S1: 17 Ağu (Pzt) yatan maaş içe aktarıldı, bugün 18 Ağu (dönem 2026-09). Eylül maaşı (15 Eyl) hâlâ beklenmeli:
    Gelir'in 'bekliyor'u, Dönem sonu (expected/forecast), Planlama satırı, 'Maaş gelince' tek maaş."""
    today = date(2026, 8, 18)
    _freeze(monkeypatch, today)
    _salary_rec(book)
    _salary(book, "2026-07-15", source)
    _salary(book, "2026-08-17", source)
    s = finance.summary("2026-09", today=today)
    assert s["pending"]["income"] == SALARY
    assert s["totals"]["income"] == 0
    assert s["expected"]["income"] == SALARY
    plan_row = next(m for m in finance.plan(3, today=today)["months"] if m["month"] == "2026-09")
    assert [ln["amount"] for ln in plan_row["lines"] if ln["kind"] == finance.INCOME] == [SALARY]
    sai = finance.net_worth(today)["spendable_after_income"]
    assert sai and sai["income"] == SALARY and sai["date"] == "2026-09-15"
    assert finance.summary("2026-08", today=today)["totals"]["income"] == SALARY


@pytest.mark.xfail(strict=True, reason=(
    "pending_recurring: dönem bitince (17 Ağu) geçmiş dönemin yatmamış maaşı ANINDA 'tahmini, döküm yok' gerçek gelire "
    "döner; içinde bulunulan dönemin beklenen gelirine (spendable_after_income) girmez. Hafta sonu/bayram toleransı "
    "(beklenen gün + N iş günü) içinde 'bekliyor' kalmalı. Plan 2 → fix/odemelerden-sonra-tek-ufuk dalında."))
def test_salary_due_saturday_not_yet_arrived_is_still_awaited(book, monkeypatch):
    """S1 (içe aktarmadan önce): 15 Ağu Cumartesi, bugün 17 Ağu Pazartesi sabah, Ağustos maaşı henüz görünmüyor.
    Beklenen: Ağustos maaşı 'bekliyor' (tahmini gerçek gelir değil) ve bu dönemin beklenen gelirlerinde (Maaş gelince)
    iki maaş: bugün gelecek Ağustos + 15 Eylül."""
    today = date(2026, 8, 17)
    _freeze(monkeypatch, today)
    _salary_rec(book)
    _salary(book, "2026-07-15", "bank")
    assert _salary_counts("2026-08") == (0, 0)
    sai = finance.net_worth(today)["spendable_after_income"]
    assert sai and sai["income"] == 2 * SALARY


# ---------------------------------------------------------------- "maaş yatmadı mı?" bildirimi (push._gelir)

def _gelir_ids(today: date) -> list[str]:
    return [x["id"] for x in push._gelir(today)]


def test_push_salary_early_no_notification(book):
    """S2: 13'ünde (erken) yatmış → 17'sinde bildirim yok."""
    _salary_rec(book)
    _salary(book, "2026-07-15", "bank")
    _salary(book, "2026-08-13", "bank")
    assert _gelir_ids(date(2026, 8, 17)) == []


def test_push_salary_truly_missing_fires(book):
    """Kontrol: hafta içi beklenen gün (15 Eyl Salı) + 2 gün, maaş yok → bildirim çıkar."""
    _salary_rec(book)
    _salary(book, "2026-07-15", "bank")
    _salary(book, "2026-08-17", "bank")
    assert len(_gelir_ids(date(2026, 9, 17))) == 1


def test_push_salary_due_on_weekend_not_premature(book):
    _salary_rec(book)
    _salary(book, "2026-07-15", "bank")
    assert _gelir_ids(date(2026, 8, 17)) == []


def test_push_salary_kurban_2027_not_during_bayram(book):
    _salary_rec(book, start="2027-01")
    _salary(book, "2027-04-15", "bank")
    assert _gelir_ids(date(2027, 5, 17)) == []


# ---------------------------------------------------------------- 4: kart kesimi / son ödeme kayması

CARD_A, CARD_B = "4543***1111", "5400***2222"


def test_card_cut_shift_same_month_keeps_period(book):
    """S4: kesim 13 → 15 (13 Haz 2026 Cumartesi): ekstre yine 2026-06 döneminde, her ay tek ekstre."""
    for pe, due in [("2026-03-13", "2026-03-23"), ("2026-04-13", "2026-04-23"), ("2026-05-13", "2026-05-23"),
                    ("2026-06-15", "2026-06-25")]:
        _stmt(book, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    periods = sorted(s["period"] for s in book.list_statements())
    assert periods == ["2026-03", "2026-04", "2026-05", "2026-06"]


def test_next_cut_after_shifted_cut_uses_usual_day_reminders(book):
    for pe, due in [("2026-03-13", "2026-03-23"), ("2026-04-13", "2026-04-23"), ("2026-05-13", "2026-05-23"),
                    ("2026-06-15", "2026-06-25")]:
        _stmt(book, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    k = next(x for x in reminders.card_cuts(date(2026, 7, 14)) if x["card_no"] == CARD_A)
    assert (k["cut"], k["due"]) == (date(2026, 7, 13), date(2026, 7, 23))


def test_next_cut_after_shifted_cut_uses_usual_day_finance(book):
    for pe, due in [("2026-03-13", "2026-03-23"), ("2026-04-13", "2026-04-23"), ("2026-05-13", "2026-05-23"),
                    ("2026-06-15", "2026-06-25")]:
        _stmt(book, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    st = finance._latest_statement_per_account()[CARD_A]
    assert finance._next_cut(st) == (date(2026, 7, 13), date(2026, 7, 23))


def test_auto_cycle_day_stable_under_weekend_cut(tmpdb):
    tmpdb.set_setting("cycle_mode", "auto")
    for pe, due in [("2026-03-13", "2026-03-23"), ("2026-04-13", "2026-04-23"), ("2026-05-13", "2026-05-25")]:
        _stmt(tmpdb, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    for pe, due in [("2026-03-16", "2026-03-26"), ("2026-04-16", "2026-04-27")]:
        _stmt(tmpdb, CARD_B, "Deneme Bank", date.fromisoformat(pe), date.fromisoformat(due))
    tmpdb.auto_update_cycle()
    assert tmpdb.cycle_start_day() == 17
    _stmt(tmpdb, CARD_B, "Deneme Bank", date(2026, 5, 18), date(2026, 5, 28))  # 16 May Cumartesi → 18 Pazartesi
    tmpdb.auto_update_cycle()
    assert tmpdb.cycle_start_day() == 17


def test_month_end_cut_shift_crossing_month(book):
    for pe, due in [("2027-08-30", "2027-09-09"), ("2027-09-30", "2027-10-11"), ("2027-11-01", "2027-11-11"),
                    ("2027-11-30", "2027-12-10")]:
        _stmt(book, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    periods = sorted(s["period"] for s in book.list_statements())
    assert periods == ["2027-08", "2027-09", "2027-10", "2027-11"]


def _statement_payable(today: date) -> dict:
    return next(p for p in finance.payables(today) if p["type"] == "statement")


def test_card_due_printed_monday_not_late_on_weekend(book):
    """S4: banka son ödemeyi kaydırıp 26 Eki 2026 Pazartesi yazmış: hafta sonu (24–25) 'bekliyor', faiz yok."""
    _stmt(book, CARD_A, "Örnek Bank", date(2026, 10, 16), date(2026, 10, 26))
    p = _statement_payable(date(2026, 10, 25))
    assert p["status"] == "bekliyor" and p["interest"]["state"] == "before_due"


def test_card_due_saturday_not_late_on_sunday(book):
    _stmt(book, CARD_A, "Örnek Bank", date(2026, 10, 14), date(2026, 10, 24))
    p = _statement_payable(date(2026, 10, 25))
    assert p["status"] == "bekliyor" and p["interest"]["state"] == "before_due"


def test_card_due_saturday_late_after_next_business_day(book):
    """Kontrol: sonraki iş günü (26 Eki Pzt) de geçti → 27 Eki 'gecikti'."""
    _stmt(book, CARD_A, "Örnek Bank", date(2026, 10, 14), date(2026, 10, 24))
    assert _statement_payable(date(2026, 10, 27))["status"] == "gecikti"


# ---------------------------------------------------------------- 5: düzenli ödeme (kira 16) ve abonelik

def _rent_rec(db) -> int:
    return db.create_recurring({"description": "Kira", "amount": RENT, "kind": "gider", "category": "Kira & Aidat",
                                "day_of_month": 16, "start_month": "2026-01"})


@pytest.mark.parametrize("pay_date,source", [
    ("2026-06-14", "havale"), ("2026-06-19", "havale"), ("2026-06-14", "manual"), ("2026-06-19", "manual"),
    ("2026-05-18", "havale"),  # 16 May Cumartesi → Pazartesi ödendi (dönem 2026-06'ya düşen tarih)
])
def test_rent_shift_belongs_to_due_period(book, pay_date, source):
    """S5: kira 16'sı; 14'ünde erken, 19'unda geç → vadesinin dönemi (2026-06 = 17 May – 16 Haz)."""
    rid = _rent_rec(book)
    want = book.period_of(date.fromisoformat(pay_date).replace(day=16))
    if source == "havale":
        eid = book.create_entry({"entry_date": pay_date, "description": "FAST ODEME SOKAK KIRA", "amount": RENT,
                                 "kind": "gider", "category": "Kira & Aidat", "payment": "havale",
                                 "counterparty": "Ayşe Örnek", "import_hash": f"h-kira-{pay_date}"})
    else:
        eid = book.create_entry({"entry_date": pay_date, "description": "Kira", "amount": RENT, "kind": "gider",
                                 "category": "Kira & Aidat", "payment": "nakit"})
    e = _entry(book, eid)
    assert (e["recurring_id"], e["month"]) == (rid, want)


@pytest.mark.xfail(strict=True, reason=(
    "_due_this_period yalnız içinde bulunulan dönemin girilmemiş düzenli giderini düşer (bilinçli: 'geçmiş dönem "
    "düşülmez'). 16 May Cumartesi vadeli kira Pazartesi ödenecek; 17 May Pazar günü dönem 2026-06 başladı ve Mayıs "
    "kirası Ödemelerden sonra'dan düşer, para hâlâ hesapta. Tolerans penceresinde (≤ TRANSFER_LATE_DAYS) önceki "
    "dönemin ödenmemiş kalemi de düşülmeli. Plan 8 → fix/odemelerden-sonra-tek-ufuk dalında."))
def test_rent_due_saturday_still_deducted_on_sunday(book, monkeypatch):
    today = date(2026, 5, 17)
    _freeze(monkeypatch, today)
    _rent_rec(book)
    book.create_entry({"entry_date": "2026-04-16", "description": "Kira", "amount": RENT, "kind": "gider",
                       "category": "Kira & Aidat", "payment": "nakit"})
    due = finance._due_this_period(today, [])["recurring"]["items"]
    assert sorted(x["date"] for x in due) == ["2026-05-16", "2026-06-16"]


def _sub_txns(dates: list[str]) -> list[dict]:
    return [{"date": d, "amount": 99.0} for d in dates]


def test_subscription_slip_within_slack_no_gap():
    """S5: abonelik 15'inde; Ağustos çekimi 18'ine kaydı (GAP_SLACK=3), 17'sinde ay 'ödemesiz' sayılmaz."""
    tx = _sub_txns(["2026-05-15", "2026-06-15", "2026-07-15"])
    assert sub_detail._gap_months(tx, ["2026-05", "2026-06", "2026-07"], date(2026, 8, 17)) == []


def test_subscription_month_end_slip_no_gap():
    tx = _sub_txns(["2027-08-30", "2027-09-30", "2027-11-01", "2027-11-30"])
    paid = sorted({x["date"][:7] for x in tx})
    assert sub_detail._gap_months(tx, paid, date(2027, 12, 5)) == []


# ---------------------------------------------------------------- 6: fatura son ödemesi hafta sonu

def _bill(db, due: str) -> int:
    return db.create_bill({"provider": "Örnek Elektrik", "amount": 750.0, "due_date": due, "category": "Fatura"})


def _bill_payable(today: date) -> dict:
    return next(p for p in finance.payables(today) if p["type"] == "bill")


def test_bill_due_saturday_not_late_on_sunday(book):
    _bill(book, "2026-10-24")
    assert _bill_payable(date(2026, 10, 25))["status"] == "bekliyor"
    item = finance._due_this_period(date(2026, 10, 25), [])["bills"]["items"][0]
    assert item["overdue"] is False


def test_bill_due_saturday_late_after_monday(book):
    _bill(book, "2026-10-24")
    assert _bill_payable(date(2026, 10, 27))["status"] == "gecikti"


def test_bill_due_in_kurban_2027_not_late_during_bayram(book):
    _bill(book, "2027-05-17")
    assert _bill_payable(date(2027, 5, 18))["status"] == "bekliyor"


# ---------------------------------------------------------------- iş günü yardımcıları ve sınırlar


@pytest.mark.parametrize("d,want", [
    ("2026-08-15", "2026-08-17"),  # Cumartesi → Pazartesi
    ("2026-08-14", "2026-08-14"),  # Cuma iş günü
    ("2027-05-15", "2027-05-20"),  # arife Cmt, Kurban 16–19 → Perşembe
    ("2027-05-14", "2027-05-14"),
    ("2026-03-19", "2026-03-19"),  # Ramazan arifesi (yarım gün) iş günü
    ("2026-03-20", "2026-03-23"),  # Ramazan 20–22 Mar → Pazartesi
    ("2026-10-29", "2026-10-30"),  # Cumhuriyet Bayramı Perşembe → Cuma
    ("2026-10-28", "2026-10-28"),  # 28 Ekim yarım gün: iş günü
    ("2030-04-12", "2030-04-12"),  # 2030 Kurban arifesi Cuma
    ("2030-04-13", "2030-04-17"),  # Kurban 13–16 Nis 2030 → Çarşamba
])
def test_next_business_day(d, want):
    assert workdays.next_business_day(d).isoformat() == want


def test_religious_table_matches_hijri_fallback_within_a_day():
    """Tablodaki (Diyanet) 1. günler hicri yaklaşık hesapla en çok 1 gün farklı: tablo yazım hatası yakalanır."""
    from app import zakat

    for y, t in workdays.RELIGIOUS_HOLIDAYS.items():
        for key, (m, d) in {"ramazan": (10, 1), "kurban": (12, 10)}.items():
            approx = zakat.next_hijri(m, d, date(y, 1, 1))
            assert abs((approx - date.fromisoformat(t[key])).days) <= 1, (y, key)


def test_year_outside_table_falls_back_to_hijri():
    hs = workdays.holidays(2031)
    assert date(2031, 10, 29) in hs and sum(1 for d in hs if d.month in (1, 2, 3, 4) and d.day not in (1, 23)) >= 7


@pytest.mark.parametrize("dates,want", [
    (["2026-03-13", "2026-04-13", "2026-05-13", "2026-06-15"], 13),  # 13 Haz Cmt → 15: kayma
    (["2026-01-13", "2026-02-13", "2026-03-20"], 20),               # tatille açıklanmıyor: banka günü değiştirdi
    (["2027-08-30", "2027-09-30", "2027-11-01"], 30),               # 30 Eki Cmt → 1 Kas: önceki ayın kayması
    (["2026-05-16"], 16),
])
def test_usual_day(dates, want):
    assert workdays.usual_day(dates) == want


def test_push_salary_thursday_due_fires_after_two_business_days(book):
    """15 Eki 2026 Perşembe; +2 iş günü = 19 Eki Pazartesi (takvimle 17'si Cumartesi olurdu)."""
    _salary_rec(book)
    _salary(book, "2026-09-15", "bank")
    assert _gelir_ids(date(2026, 10, 17)) == []
    assert _gelir_ids(date(2026, 10, 19)) == ["gelir:1:2026-10"]


def test_bank_salary_extra_small_payment_does_not_take_next_period(book):
    """Aynı dönemde ikinci, küçük 'MAAS' ödemesi (fark/prim) sonraki dönemin maaş yerini kapmaz (elle kayıtla aynı)."""
    _salary_rec(book)
    _salary(book, "2026-08-14", "bank")
    eid = book.create_entry({"entry_date": "2026-08-16", "description": "MAAS FARKI ORNEK TEKSTIL AS", "amount": 2500.0,
                             "kind": "gelir", "category": "Diğer Gelir", "import_hash": "h-fark"})
    book.link_salary_entries()
    e = _entry(book, eid)
    assert (e["recurring_id"], e["month"], e["category"]) == (None, "2026-08", "Maaş")


def test_month_end_cut_not_moved_when_previous_month_has_statement(book):
    """Ay sonu kesimli kartın erken ay kesimi, önceki ayda zaten ekstre varsa kendi ayında kalır (iki ekstre aynı ay
    olmasın)."""
    for pe, due in [("2027-08-30", "2027-09-09"), ("2027-09-30", "2027-10-11"), ("2027-10-29", "2027-11-08"),
                    ("2027-11-02", "2027-11-12")]:
        _stmt(book, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    assert sorted(s["period"] for s in book.list_statements()) == ["2027-08", "2027-09", "2027-10", "2027-11"]
    assert {t["period"] for t in book.card_transactions() if t["period_end"] == "2027-11-02"} == {"2027-11"}


def test_month_end_cut_shift_moves_card_transactions_too(book):
    for pe, due in [("2027-08-30", "2027-09-09"), ("2027-09-30", "2027-10-11"), ("2027-11-01", "2027-11-11")]:
        _stmt(book, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    assert {t["period"] for t in book.card_transactions() if t["period_end"] == "2027-11-01"} == {"2027-10"}
    assert finance.summary("2027-10")["statements"][0]["period_end"] == "2027-11-01"


def test_next_cut_after_month_end_shift_is_same_month(book):
    """30 Eki 2027 Cumartesi → 1 Kas kesimi: sıradaki kesim 30 Kas (reminders) / 28 Kas (finance: gün 28 ile sınırlı),
    Aralık değil."""
    for pe, due in [("2027-08-30", "2027-09-09"), ("2027-09-30", "2027-10-11"), ("2027-11-01", "2027-11-11")]:
        _stmt(book, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    k = next(x for x in reminders.card_cuts(date(2027, 11, 5)) if x["card_no"] == CARD_A)
    assert k["cut"] == date(2027, 11, 30)
    st = finance._latest_statement_per_account()[CARD_A]
    assert finance._next_cut(st)[0] == date(2027, 11, 28)
