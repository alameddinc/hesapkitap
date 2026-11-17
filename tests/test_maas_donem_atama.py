"""Maaş dönem ataması (kullanıcı kararı 17.11): "Bu ay"da her dönemde o dönemi fonlayan TEK maaş. Maaş dönüm gününden en
çok 5 takvim günü ÖNCE yatıyorsa SONRAKİ dönemin geliri (db.salary_lead / recurring_due); dönemin içinde yatan maaş
değişmez. Ay dönümü 17: anahtar 2026-12 = 17 Kas – 16 Ara ("Kasım dönemi"). Bakiye/harcanabilir değişmez: para gerçek
gününde hesapta. Tutarlar, kişiler uydurmadır."""
from datetime import date

import pytest

from app import finance

SALARY = 40000.0
ES = 25000.0


def _rec(tmpdb, day, desc="Maaş", amount=SALARY, start="2026-08"):
    return tmpdb.create_recurring({"description": desc, "amount": amount, "kind": "gelir", "category": "Maaş",
                                   "day_of_month": day, "start_month": start})


def _maas(tmpdb, d, desc="Maaş", amount=SALARY):
    eid = tmpdb.create_entry({"entry_date": d, "description": desc, "amount": amount, "kind": "gelir",
                              "category": "Maaş"})
    return next(e for e in tmpdb.list_entries() if e["id"] == eid)


def _cycle(tmpdb, day):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(day)


def _income(month):
    """Dönemin GERÇEK gelir kayıtları (geçmiş dönemin "tahmini, döküm yok" maaşı hariç)."""
    from app import db

    return sum(e["amount"] for e in db.list_entries(month) if e["kind"] == "gelir" and not e["skipped"])


@pytest.fixture()
def d17(tmpdb):
    _cycle(tmpdb, 17)
    return tmpdb


# ---------------------------------------------------------------- kural (saf)

@pytest.mark.parametrize("start,day,lead", [
    (17, 15, True), (17, 12, True), (17, 11, False), (17, 16, True), (17, 17, False), (17, 20, False), (17, 1, False),
    (1, 31, True), (1, 30, True), (1, 27, True), (1, 26, False), (1, 1, False), (1, 15, False),
    (15, 15, False), (20, 20, False), (3, 30, True), (3, 28, False)])
def test_salary_lead_rule(tmpdb, start, day, lead):
    _cycle(tmpdb, start)
    rid = _rec(tmpdb, day)
    r = next(x for x in tmpdb.list_recurring() if x["id"] == rid)
    assert tmpdb.salary_lead(r, "2026-12") is lead


def test_due_date_moves_to_previous_period_tail(d17):
    rid = _rec(d17, 15)
    r = next(x for x in d17.list_recurring() if x["id"] == rid)
    assert d17.recurring_due(r, "2026-12") == "2026-11-15"   # Kasım dönemi (17 Kas – 16 Ara) maaşı 15 Kasım
    assert d17.recurring_due(r, "2027-01") == "2026-12-15"


def test_only_salary_items_shift(d17):
    rid = d17.create_recurring({"description": "Kira geliri", "amount": 9000.0, "kind": "gelir", "category": "Kira Geliri",
                                "day_of_month": 15, "start_month": "2026-08"})
    r = next(x for x in d17.list_recurring() if x["id"] == rid)
    assert not d17.salary_lead(r, "2026-12")
    assert d17.recurring_due(r, "2026-12") == "2026-12-15"
    gid = d17.create_recurring({"description": "Aidat", "amount": 900.0, "kind": "gider", "category": "Kira & Aidat",
                                "day_of_month": 15, "start_month": "2026-08"})
    g = next(x for x in d17.list_recurring() if x["id"] == gid)
    assert d17.recurring_due(g, "2026-12") == "2026-12-15"


# ---------------------------------------------------------------- ay dönümü 17

def test_on_time_early_late_go_to_funded_period(d17):
    _rec(d17, 15)
    assert _maas(d17, "2026-10-15")["month"] == "2026-11"
    assert _maas(d17, "2026-11-13")["month"] == "2026-12"   # bayram erken (13 Kas): Kasım dönemi
    assert _maas(d17, "2026-12-18")["month"] == "2027-01"   # geç (18 Ara, dönüm günü aşıldı): Aralık dönemi
    assert _maas(d17, "2027-01-15")["month"] == "2027-02"
    for m in ("2026-11", "2026-12", "2027-01", "2027-02"):
        assert _income(m) == SALARY, m                      # her dönemde tek maaş


def test_nov15_salary_counts_for_kasim_period(d17):
    _rec(d17, 15)
    e = _maas(d17, "2026-11-15")
    assert e["month"] == "2026-12" and d17.period_name_month("2026-12") == "2026-11"
    assert _income("2026-12") == SALARY and _income("2026-11") == 0


def test_in_period_salary_unchanged(d17):
    _rec(d17, 20)
    assert _maas(d17, "2026-11-20")["month"] == "2026-12"   # zaten Kasım dönemi (20 Kas ∈ 17 Kas – 16 Ara)
    assert _maas(d17, "2026-12-21")["month"] == "2027-01"


def test_six_days_before_stays(d17):
    _rec(d17, 11)                                          # 6 gün önce: kural dışı, takvim dönemi
    assert _maas(d17, "2026-11-11")["month"] == "2026-11"


def test_bayram_very_early_still_funds_next(d17):
    """Maaş 15'i, bayram yüzünden 11 Kas'ta (dönüm gününden 6 gün önce) yattı: kalemin gününe en yakın dönem
    (15 Kas → 2026-12): o dönemin maaşıdır, Ekim dönemine ikinci maaş olmaz."""
    _rec(d17, 15)
    _maas(d17, "2026-10-15")
    assert _maas(d17, "2026-11-11")["month"] == "2026-12"
    assert _income("2026-11") == SALARY and _income("2026-12") == SALARY


def test_late_salary_does_not_double(d17):
    _rec(d17, 15)
    _maas(d17, "2026-10-16")
    assert _maas(d17, "2026-11-19")["month"] == "2026-12"   # 4 gün geç: yine Kasım dönemi
    assert _maas(d17, "2026-12-14")["month"] == "2027-01"
    assert [_income(m) for m in ("2026-11", "2026-12", "2027-01")] == [SALARY] * 3


def test_two_salaries_each_period_once(d17):
    _rec(d17, 15)                                          # birincil: sonraki dönemi fonlar
    _rec(d17, 1, desc="Eş maaşı", amount=ES)               # ikincil: dönemin içinde (1 Ara ∈ Kasım dönemi)
    assert _maas(d17, "2026-11-15")["month"] == "2026-12"
    assert _maas(d17, "2026-12-01", desc="Eş maaşı", amount=ES)["month"] == "2026-12"
    assert _maas(d17, "2026-12-15")["month"] == "2027-01"
    assert _income("2026-12") == SALARY + ES
    rec = d17.list_recurring()
    got = finance.salary_realized("2026-12", rec, d17.list_entries("2026-12"))
    assert len(got) == 2


# ---------------------------------------------------------------- ay dönümü 1

def test_cycle1_month_end_salary_funds_next_month(tmpdb):
    _cycle(tmpdb, 1)
    _rec(tmpdb, 31)
    assert _maas(tmpdb, "2026-10-30")["month"] == "2026-11"   # 31 Eki Cumartesi → 30'unda
    assert _maas(tmpdb, "2026-11-30")["month"] == "2026-12"
    assert _maas(tmpdb, "2026-12-31")["month"] == "2027-01"
    assert [_income(m) for m in ("2026-10", "2026-11", "2026-12", "2027-01")] == [0, SALARY, SALARY, SALARY]


def test_cycle1_salary_on_1st_unchanged(tmpdb):
    _cycle(tmpdb, 1)
    _rec(tmpdb, 1)
    assert _maas(tmpdb, "2026-11-01")["month"] == "2026-11"
    assert _maas(tmpdb, "2026-11-30")["month"] == "2026-12"   # erken (1 Ara Pazar değil ama bayram varsayımı)


# ---------------------------------------------------------------- "maaş yattı/bekleniyor"

def test_pending_and_arrived_follow_funded_period(d17):
    rid = _rec(d17, 15)
    _maas(d17, "2026-10-15")
    rec = d17.list_recurring()
    today = date(2026, 11, 14)
    rows = finance.pending_recurring("2026-12", rec, d17.list_entries("2026-12"), today)
    row = next(r for r in rows if r["recurring_id"] == rid)
    assert row["date"] == "2026-11-15" and not row.get("status")
    assert not finance.salary_arrived("2026-12", today)
    assert finance.next_salary_date(today) == date(2026, 11, 15)
    _maas(d17, "2026-11-13")
    rows = finance.pending_recurring("2026-12", rec, d17.list_entries("2026-12"), today)
    assert not any(r["recurring_id"] == rid for r in rows)
    assert finance.salary_arrived("2026-12", today)
    assert finance.next_salary_date(today) == date(2026, 12, 15)


def test_horizon_moves_when_next_periods_salary_arrives(d17, monkeypatch):
    _rec(d17, 15)
    _maas(d17, "2026-10-15")
    today = date(2026, 11, 12)                             # dönem 2026-11 (17 Eki – 16 Kas), 4 gün kaldı
    assert finance.spend_horizon(today)["end"] == date(2026, 11, 16)
    _maas(d17, "2026-11-12")                               # bayram öncesi erken: Kasım dönemi maaşı yattı
    assert finance.spend_horizon(today)["end"] == date(2026, 12, 16)


def test_horizon_in_period_salary_old_rule(d17):
    """Maaş 10'u (dönüm 17, 7 gün önce: kayma yok): bu dönemin maaşı yatınca ufuk kayar (eski kural aynen)."""
    _rec(d17, 10)
    today = date(2026, 11, 10)
    assert finance.spend_horizon(today)["end"] == date(2026, 11, 16)
    assert _maas(d17, "2026-11-10")["month"] == "2026-11"
    assert finance.spend_horizon(today)["end"] == date(2026, 12, 16)


def test_entry_date_kept_only_period_moves(d17):
    """Kaydın tarihi (bakiyeye giriş günü) değişmez; yalnız dönemi sonraki dönem."""
    _rec(d17, 15)
    e = _maas(d17, "2026-11-15")
    assert e["month"] == "2026-12" and e["entry_date"] == "2026-11-15"


def test_confirm_uses_funded_date(d17, client):
    rid = _rec(d17, 15)
    r = client.post(f"/api/recurring/{rid}/confirm", json={"month": "2026-12"})
    assert r.status_code == 200, r.text
    e = next(x for x in d17.list_entries() if x["id"] == r.json()["id"])
    assert e["entry_date"] == "2026-11-15" and e["month"] == "2026-12"


def test_link_salary_entries_bank_row(d17):
    """Dökümden gelen bağsız maaş satırı (MAAS metni) kalemin fonladığı döneme bağlanır."""
    rid = _rec(d17, 15)
    eid = d17.create_entry({"entry_date": "2026-11-16", "description": "ORNEK A.S. MAAS ODEMESI", "amount": SALARY,
                            "kind": "gelir", "category": "Diğer Gelir"})
    d17.link_salary_entries()
    e = next(x for x in d17.list_entries() if x["id"] == eid)
    assert e["recurring_id"] == rid and e["month"] == "2026-12" and e["category"] == "Maaş"


def test_cycle_change_reslots_salary(tmpdb):
    _cycle(tmpdb, 1)
    _rec(tmpdb, 15)
    a = _maas(tmpdb, "2026-10-15")
    b = _maas(tmpdb, "2026-11-15")
    assert (a["month"], b["month"]) == ("2026-10", "2026-11")
    tmpdb.set_cycle_start_day(17)
    by = {e["id"]: e["month"] for e in tmpdb.list_entries()}
    assert (by[a["id"]], by[b["id"]]) == ("2026-11", "2026-12")
