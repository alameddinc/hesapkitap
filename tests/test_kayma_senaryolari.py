"""Hafta sonu / bayram kaymaları (maaş, kart kesimi, son ödeme, düzenli ödeme, abonelik, fatura). Ay dönümü 17:
anahtar 2026-08 = 17 Tem – 16 Ağu. Maaş ayın 15'i → 2026-08 döneminin maaşı 15 Ağustos'tur (db.occurrence_date).

Kural (beklenen): bir oluşum (maaş, kira, kesim) birkaç gün erken/geç gelse de AİT OLDUĞU dönem beklenen gününün
dönemidir; her dönem tam bir kez sayılır, "gecikti/yatmadı" uyarısı hafta sonu/bayram yüzünden erken çıkmaz.
Kırmızılar xfail(strict=True): düzeltme gelince XPASS olur, işaret kaldırılır.

Takvim: 15 Ağu 2026 Cumartesi (17'si Pazartesi); 13 Haz 2026 Cumartesi; 16 May 2026 Cumartesi; 24 Eki 2026
Cumartesi; 30 Eki 2027 Cumartesi; 15 May 2027 Cumartesi. 2027 Kurban Bayramı YAKLAŞIK 16–19 Mayıs (arife 15 Mayıs;
app.zakat.next_hijri 17 Mayıs der, ±1 gün; Diyanet takvimiyle doğrulanmadı) → ilk iş günü 20 Mayıs Perşembe.
Bütün kişi/işyeri/tutar uydurmadır."""
from datetime import date

import pytest

from app import finance, push, reminders, sub_detail
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


XF_BANK_SALARY = pytest.mark.xfail(strict=True, reason=(
    "db.link_salary_entries dönemi e['month'] (= period_of(tarih)) alır; ay dönümünü aşan geç maaş SONRAKİ döneme "
    "bağlanır. Elle 'Maaş' yolu (_attach_recurring → _nearest_recurring_month) doğru; içe aktarma yolu değil."))


# ---------------------------------------------------------------- 1–3, 7: maaş kayması → hangi dönem

@pytest.mark.parametrize("pay_date,want", [
    pytest.param("2026-08-13", "2026-08", id="s2-erken-13-bayram"),
    pytest.param("2026-08-17", "2026-08", id="s1-pazartesi-17-banka", marks=XF_BANK_SALARY),
    pytest.param("2026-08-18", "2026-08", id="s3-uzun-bayram-18-banka", marks=XF_BANK_SALARY),
    pytest.param("2027-05-14", "2027-05", id="s7-kurban-oncesi-cuma-14"),
    pytest.param("2027-05-20", "2027-05", id="s7-kurban-sonrasi-persembe-20-banka", marks=XF_BANK_SALARY),
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
    pytest.param("bank", "2026-08-17", "2026-09-15", ("2026-08", "2026-09"), id="s1-banka", marks=XF_BANK_SALARY),
    pytest.param("bank", "2026-08-18", "2026-09-15", ("2026-08", "2026-09"), id="s3-banka", marks=XF_BANK_SALARY),
    pytest.param("bank", "2027-05-20", "2027-06-15", ("2027-05", "2027-06"), id="s7-banka", marks=XF_BANK_SALARY),
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


@pytest.mark.parametrize("source", ["manual", pytest.param("bank", marks=XF_BANK_SALARY)])
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
    "(beklenen gün + N gün) içinde 'bekliyor' kalmalı."))
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


@pytest.mark.xfail(strict=True, reason=(
    "push._gelir takvim günü sayar (beklenen gün + GELIR_LATE=2): Cumartesi 15'i → Pazartesi 17'si sabah 'Maaş yatmadı "
    "mı?' gider; o gün maaşın yattığı gün. İş günü (sonraki iş günü + 2 iş günü) beklenmeli."))
def test_push_salary_due_on_weekend_not_premature(book):
    _salary_rec(book)
    _salary(book, "2026-07-15", "bank")
    assert _gelir_ids(date(2026, 8, 17)) == []


@pytest.mark.xfail(strict=True, reason=(
    "Bayram: 15 May 2027 Cumartesi/arife, Kurban ≈16–19 May; push._gelir 17 May'da (bayramın 2. günü) bildirim atar. "
    "Resmi tatil listesi olmadan iş günü bilinmez."))
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


XF_CUT_RULE = pytest.mark.xfail(strict=True, reason=(
    "Sonraki kesim tahmini SON ekstrenin gününden (reminders._cut_rule: ends[-1].day; finance._next_cut: pe.day): "
    "hafta sonu yüzünden 15'ine kaymış bir kesim kalıcı 'kesim 15' sanılır; Temmuz kesimi 13 yerine 15 beklenir "
    "(ekstre hatırlatması, kesim/son ödeme tahmini 2 gün kayar). Son 3–6 ekstrenin en sık/küçük günü alınmalı."))


@XF_CUT_RULE
def test_next_cut_after_shifted_cut_uses_usual_day_reminders(book):
    for pe, due in [("2026-03-13", "2026-03-23"), ("2026-04-13", "2026-04-23"), ("2026-05-13", "2026-05-23"),
                    ("2026-06-15", "2026-06-25")]:
        _stmt(book, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    k = next(x for x in reminders.card_cuts(date(2026, 7, 14)) if x["card_no"] == CARD_A)
    assert (k["cut"], k["due"]) == (date(2026, 7, 13), date(2026, 7, 23))


@XF_CUT_RULE
def test_next_cut_after_shifted_cut_uses_usual_day_finance(book):
    for pe, due in [("2026-03-13", "2026-03-23"), ("2026-04-13", "2026-04-23"), ("2026-05-13", "2026-05-23"),
                    ("2026-06-15", "2026-06-25")]:
        _stmt(book, CARD_A, "Örnek Bank", date.fromisoformat(pe), date.fromisoformat(due))
    st = finance._latest_statement_per_account()[CARD_A]
    assert finance._next_cut(st) == (date(2026, 7, 13), date(2026, 7, 23))


@pytest.mark.xfail(strict=True, reason=(
    "Otomatik ay dönümü (db.suggested_cycle_day = her kartın SON kesim günlerinin en geçi + 1): B kartının kesimi "
    "16 May 2026 Cumartesi yüzünden 18'ine kayınca ay dönümü 17 → 19 olur, set_cycle_start_day bütün elle kayıtların "
    "dönemini yeniden hesaplar; sonraki ay geri 17'ye döner (salınım)."))
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


@pytest.mark.xfail(strict=True, reason=(
    "Ekstre dönemi = substr(period_end, 1, 7) (db.list_statements). Ay sonu kesimi (30 Eki 2027 Cumartesi) 1 Kasım'a "
    "kayınca Ekim'de ekstre yok, Kasım'da iki ekstre (Planlama/Özet kart harcaması Ekim 0, Kasım çift). Dönem, "
    "önceki ekstreden ~1 ay sonrası / SAME_PERIOD toleransıyla atanmalı."))
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


@pytest.mark.xfail(strict=True, reason=(
    "finance._status: due < today → 'gecikti'. Ekstrede son ödeme Cumartesi (24 Eki 2026) yazıyorsa Pazar günü "
    "'gecikti' + faiz 'after_due' (spendable'dan gecikme faizi düşülür). Kart yönetmeliği: tatile denk gelen son "
    "ödeme ilk iş günüdür; ödeme günü sonraki iş gününe yuvarlanmalı."))
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
    "dönemin ödenmemiş kalemi de düşülmeli. (Ödemelerden sonra alanı: başka dalda çalışılıyor; yalnız test.)"))
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


@pytest.mark.xfail(strict=True, reason=(
    "sub_detail._gap_months ödemeli ayı TAKVİM ayıyla sayar (date[:7]): 30'unda çekilen abonelik 30 Eki 2027 "
    "Cumartesi yüzünden 1 Kasım'a kayınca Ekim 'ödemesiz' satırı çıkar, Kasım iki ödemeli görünür."))
def test_subscription_month_end_slip_no_gap():
    tx = _sub_txns(["2027-08-30", "2027-09-30", "2027-11-01", "2027-11-30"])
    paid = sorted({x["date"][:7] for x in tx})
    assert sub_detail._gap_months(tx, paid, date(2027, 12, 5)) == []


# ---------------------------------------------------------------- 6: fatura son ödemesi hafta sonu

def _bill(db, due: str) -> int:
    return db.create_bill({"provider": "Örnek Elektrik", "amount": 750.0, "due_date": due, "category": "Fatura"})


def _bill_payable(today: date) -> dict:
    return next(p for p in finance.payables(today) if p["type"] == "bill")


@pytest.mark.xfail(strict=True, reason=(
    "finance._status: son ödemesi Cumartesi (24 Eki 2026) olan fatura Pazar 'gecikti' görünür (_due_this_period "
    "overdue=True); sonraki iş günü (26 Eki Pzt) geçmeden gecikti denmemeli."))
def test_bill_due_saturday_not_late_on_sunday(book):
    _bill(book, "2026-10-24")
    assert _bill_payable(date(2026, 10, 25))["status"] == "bekliyor"
    item = finance._due_this_period(date(2026, 10, 25), [])["bills"]["items"][0]
    assert item["overdue"] is False


def test_bill_due_saturday_late_after_monday(book):
    _bill(book, "2026-10-24")
    assert _bill_payable(date(2026, 10, 27))["status"] == "gecikti"


@pytest.mark.xfail(strict=True, reason=(
    "S7: son ödeme 17 May 2027 (Kurban ≈ 2. gün, Pazartesi). 18 May'da 'gecikti'; ilk iş günü 20 May. Yalnız hafta "
    "sonu kuralı yetmez: resmi tatil (dini bayram) listesi gerekir."))
def test_bill_due_in_kurban_2027_not_late_during_bayram(book):
    _bill(book, "2027-05-17")
    assert _bill_payable(date(2027, 5, 18))["status"] == "bekliyor"
