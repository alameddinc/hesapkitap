"""P0 (iki maaş, verilen gün, ay sonu günü, maaştan önce vadeli ödeme) için çekişmeli testler (bağımsız doğrulama).
Kırmızılar xfail(strict) ve gerekçesiyle; düzeltme geliştiriciye. Kişi, işveren, kart ve tutarlar UYDURMADIR."""
from __future__ import annotations

from datetime import date

import pytest

from app import finance
from tests.test_gercek_gun_tekrari import freeze

A_DESC, B_DESC = "Maaş Örnek Yazılım", "Maaş Deneme Lojistik"


def _cycle(tmpdb, day=1):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(day)


def _salary(tmpdb, desc, amount, day, start="2026-06", **extra):
    return tmpdb.create_recurring({"description": desc, "amount": amount, "kind": "gelir", "category": "Maaş",
                                   "day_of_month": day, "start_month": start, **extra})


def _raw(tmpdb, d, desc, amount, account_id=None, category="Diğer Gelir", recurring_id=None):
    """Bağsız banka satırı (dökümden gelmiş gibi; otomatik bağlama çalışmaz)."""
    with tmpdb.connect() as c:
        return c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, account_id, "
                         "recurring_id) VALUES (?, ?, ?, ?, 'gelir', ?, ?, ?)",
                         (d, tmpdb.period_of(d), desc, amount, category, account_id, recurring_id)).lastrowid


def _pending_income(month, today):
    rows = finance.pending_recurring(month, finance.db.list_recurring(), finance.db.list_entries(month), today)
    return {r["description"]: r["amount"] for r in rows if r["kind"] == "gelir" and r.get("pending")}


def _bill(client, due, amount=700.0, prov="Örnek Su"):
    assert client.post("/api/bills", json={"provider": prov, "amount": amount, "due_date": due}).status_code == 200


def _bills(nw):
    return sorted(x["date"] for x in nw["bills_items"])


def _later_bills(nw):
    return sorted(x["due_date"] for x in nw["spendable_later"]["items"] if x["type"] == "bill")


def _inv(nw):
    parts = sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in nw["spendable_parts"])
    assert parts == pytest.approx(nw["spendable"], abs=0.011)
    assert nw["spendable"] <= nw["free"] + 0.001
    fb = nw["forecast_balance"]
    income = (nw["spendable_after_income"] or {}).get("income") or 0.0
    assert fb["amount"] <= fb["no_new_spend"] + 0.001 <= nw["spendable"] + income + 0.002
    now = {(x["title"], x["date"]) for x in nw["bills_items"]}
    assert not now & {(x["title"], x["due_date"]) for x in nw["spendable_later"]["items"]}


# ---------------------------------------------------------------- iki maaş: zam, ad yok

def test_two_salaries_raise_25_no_name_both_paid_strict(tmpdb):
    _cycle(tmpdb)
    _salary(tmpdb, A_DESC, 60000.0, 1)
    _salary(tmpdb, B_DESC, 40000.0, 15)
    _raw(tmpdb, "2026-11-01", "MAAS ODEMESI", 60000.0)
    _raw(tmpdb, "2026-11-15", "MAAS ODEMESI", 50000.0)
    assert _pending_income("2026-11", date(2026, 11, 16)) == {}


def test_raised_second_salary_does_not_realize_primary(tmpdb):
    _cycle(tmpdb)
    _salary(tmpdb, A_DESC, 60000.0, 20)
    _salary(tmpdb, B_DESC, 40000.0, 15)
    _raw(tmpdb, "2026-11-15", "MAAS ODEMESI", 50000.0)
    today = date(2026, 11, 24)
    assert finance.salary_arrived("2026-11", today) is False
    assert A_DESC in _pending_income("2026-11", today)


def test_two_salary_small_diff_line_does_not_realize(tmpdb):
    """İki maaşta 2.500'lük 'MAAS FARKI' (adsız) hiçbir kalemi yatmış yapmaz (±%20 dışı)."""
    _cycle(tmpdb)
    _salary(tmpdb, A_DESC, 60000.0, 20)
    _salary(tmpdb, B_DESC, 40000.0, 15)
    _raw(tmpdb, "2026-11-05", "MAAS FARKI ODEMESI", 2500.0)
    assert _pending_income("2026-11", date(2026, 11, 6)) == {A_DESC: 60000.0, B_DESC: 40000.0}


def test_single_salary_diff_line_does_not_realize(tmpdb):
    _cycle(tmpdb)
    _salary(tmpdb, "Maaş", 50000.0, 15)
    _raw(tmpdb, "2026-11-05", "MAAS FARKI ODEMESI", 2500.0, category="Maaş")
    today = date(2026, 11, 10)
    assert _pending_income("2026-11", today) == {"Maaş": 50000.0}
    assert finance.salary_arrived("2026-11", today) is False


# ---------------------------------------------------------------- silinmiş / pasif maaş kalemi

def test_deleted_second_salary_entry_does_not_realize_remaining(tmpdb):
    _cycle(tmpdb)
    a = _salary(tmpdb, A_DESC, 60000.0, 20)
    b = _salary(tmpdb, B_DESC, 40000.0, 15)
    _raw(tmpdb, "2026-11-15", "Maaş", 40000.0, category="Maaş", recurring_id=b)
    tmpdb.delete_recurring(b)
    assert a and _pending_income("2026-11", date(2026, 11, 16)) == {A_DESC: 60000.0}


def test_inactive_second_salary_not_expected(tmpdb):
    """Bitiş ayı geçmiş (pasif) maaş kalemi beklenmez."""
    _cycle(tmpdb)
    _salary(tmpdb, A_DESC, 60000.0, 20)
    _salary(tmpdb, B_DESC, 40000.0, 15, end_month="2026-10")
    assert _pending_income("2026-11", date(2026, 11, 16)) == {A_DESC: 60000.0}


# ---------------------------------------------------------------- birincil: eşit tutar

def test_equal_salaries_primary_is_first_and_day_decides(tmpdb):
    """Eşit tutarlı iki maaş: birincil önce açılan (A, 1'i). Yalnız B'nin günü (15) adsız satır: B'ye gider, A bekler,
    ufuk kaymaz."""
    _cycle(tmpdb)
    _salary(tmpdb, A_DESC, 40000.0, 1)
    _salary(tmpdb, B_DESC, 40000.0, 15)
    _raw(tmpdb, "2026-11-15", "MAAS ODEMESI", 40000.0)
    today = date(2026, 11, 26)
    assert finance.primary_salary("2026-11", finance.db.list_recurring())["description"] == A_DESC
    assert _pending_income("2026-11", today) == {A_DESC: 40000.0}
    assert finance.salary_arrived("2026-11", today) is False


# ---------------------------------------------------------------- bayramda erken + iki maaş

def test_feast_early_salary_with_two_salaries(tmpdb):
    """Ramazan Bayramı 2027 (9–11 Mar): A'nın 10 Mar maaşı arife 8 Mar'da adsız yattı → A yattı; B (25'i) bekler."""
    _cycle(tmpdb)
    _salary(tmpdb, A_DESC, 60000.0, 10)
    _salary(tmpdb, B_DESC, 30000.0, 25)
    _raw(tmpdb, "2027-03-08", "MAAS ODEMESI", 60000.0)
    today = date(2027, 3, 9)
    assert _pending_income("2027-03", today) == {B_DESC: 30000.0}
    assert finance.salary_arrived("2027-03", today) is True


# ---------------------------------------------------------------- verilen gün = sistem saati (kaçak yok), önbellek

@pytest.fixture()
def book(client, tmpdb):
    """Takvim ayı; maaş 1'i (Kasım yattı), kira 31'i, fatura 30 Kas ve 4 Oca, ödeme planlı borç (her ayın 5'i)."""
    _cycle(tmpdb)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 90000.0,
                                              "as_of": "2026-11-01"}).status_code == 200
    rid = _salary(tmpdb, "Maaş", 50000.0, 1)
    tmpdb.create_entry({"entry_date": "2026-11-01", "description": "Maaş", "amount": 50000.0, "kind": "gelir",
                        "category": "Maaş", "recurring_id": rid, "month": "2026-11"})
    tmpdb.create_recurring({"description": "Kira", "amount": 18000.0, "kind": "gider", "category": "Kira",
                            "day_of_month": 31, "start_month": "2026-06"})
    _bill(client, "2026-11-30", 650.0, "Örnek Elektrik")
    _bill(client, "2027-01-04", 900.0, "Örnek Doğalgaz")
    r = client.post("/api/debts", json={"person": "Örnek Mağaza", "direction": "borc", "creditor_type": "magaza",
                                        "plan_installment": 1000.0, "plan_count": 6, "plan_start": "2026-11",
                                        "plan_day": 5, "debt_date": "2026-10-20"})
    assert r.status_code == 200, r.text
    return client


KEYS = ("free", "spendable", "spendable_horizon", "spendable_pre_salary", "spendable_after_income", "bills_items",
        "recurring_items", "spendable_parts", "spendable_later", "forecast_balance")


def _pick(nw):
    return {k: nw[k] for k in KEYS}


@pytest.mark.parametrize("day", [date(2026, 11, 30), date(2026, 12, 1), date(2026, 12, 31), date(2027, 1, 1)])
def test_given_day_equals_system_clock_day(book, monkeypatch, day):
    """Gece yarısı ve ay/yıl sonu sınırları: net_worth(gün) saat o gündeyken ve saat 40 gün sonrayken AYNI. Önbellek
    temiz başlanır."""
    from datetime import timedelta

    freeze(monkeypatch, day)
    finance._CACHE.clear()
    same = _pick(finance.net_worth(day))
    freeze(monkeypatch, day + timedelta(days=40))
    finance._CACHE.clear()
    later = _pick(finance.net_worth(day))
    assert later == same


def test_cache_does_not_mix_days(book, monkeypatch):
    """Aynı süreçte farklı günler sırayla (ileri-geri) hesaplanır: her gün, temiz önbellekle hesaplananla aynı."""
    freeze(monkeypatch, date(2026, 11, 20))
    days = [date(2026, 11, 20), date(2026, 11, 30), date(2026, 12, 1), date(2026, 11, 20), date(2027, 1, 1)]
    seq = [_pick(finance.net_worth(d)) for d in days]
    for d, got in zip(days, seq):
        finance._CACHE.clear()
        assert got == _pick(finance.net_worth(d)), d
    assert seq[0] == seq[3]
    summ = [finance.summary("2026-11", today=d) for d in (date(2026, 11, 20), date(2026, 12, 3))]
    finance._CACHE.clear()
    assert summ[1]["pending"] == finance.summary("2026-11", today=date(2026, 12, 3))["pending"]


# ---------------------------------------------------------------- 31'inde düzenli, ay dönümü 1, Şubat / artık yıl

@pytest.mark.parametrize("month,day", [("2027-02", "2027-02-28"), ("2028-02", "2028-02-29"), ("2027-04", "2027-04-30"),
                                       ("2027-03", "2027-03-31")])
def test_31st_recurring_month_end(tmpdb, month, day):
    _cycle(tmpdb)
    tmpdb.create_recurring({"description": "Kira", "amount": 18000.0, "kind": "gider", "category": "Kira",
                            "day_of_month": 31, "start_month": "2026-06"})
    rows = finance.pending_recurring(month, tmpdb.list_recurring(), [], date(2026, 11, 16))
    assert [r["date"] for r in rows] == [day]


def test_31st_rent_paid_one_day_late_links_to_feb(client, tmpdb):
    """31'i kirası Şubat'ta 28'inde vadeli; 1 Mar'da ödendi → Şubat'ın kirası (gecikmiş), Mart'ın değil."""
    _cycle(tmpdb)
    rid = tmpdb.create_recurring({"description": "Kira", "amount": 18000.0, "kind": "gider", "category": "Kira",
                                  "day_of_month": 31, "start_month": "2026-06"})
    r = client.post("/api/entries", json={"entry_date": "2027-03-01", "description": "Kira", "amount": 18000.0,
                                          "kind": "gider", "category": "Kira", "payment": "havale"})
    assert r.status_code == 200, r.text
    with tmpdb.connect() as c:
        assert c.execute("SELECT month FROM entries WHERE recurring_id = ?", (rid,)).fetchone()[0] == "2027-02"


def test_cycle_28_with_31st_salary_stays_in_period(tmpdb):
    """Ay dönümü 28, maaş 31'i: Şubat'ı içeren dönemde 28 Şub (dönemin ilk günü), artık yılda 29 Şub; dönem içinde."""
    _cycle(tmpdb, 28)
    _salary(tmpdb, "Maaş", 50000.0, 31)
    for month, exp in (("2027-03", "2027-02-28"), ("2028-03", "2028-02-29"), ("2027-02", "2027-01-31")):
        rows = finance.pending_recurring(month, tmpdb.list_recurring(), [], date(2026, 11, 16))
        d = date.fromisoformat(rows[0]["date"])
        lo, hi = finance.period_bounds(month)
        assert rows[0]["date"] == exp and lo <= d <= hi


# ---------------------------------------------------------------- maaş 1'i, kart/fatura 30–31, maaştan önce vade

def test_salary_1st_bills_30_and_holiday_new_year(book, client):
    """Maaş 1'i. 20 Kas: ufuk 30 Kas; sonraki maaş 1 Ara (Salı) +1 iş günü → 1 Ara'ya kadar vadeliler düşülür, 2 Ara
    sonraya. 20 Ara: maaş 1 Oca (Yılbaşı, Cuma) → Pzt 4 Oca + 1 iş günü → 4 Oca dahil düşülür, 5 Oca sonraya."""
    _bill(client, "2026-12-01", 300.0, "Örnek İnternet")
    _bill(client, "2026-12-02", 400.0, "Örnek Telefon")
    nw = finance.net_worth(date(2026, 11, 20))
    _inv(nw)
    assert nw["spendable_horizon"] == "2026-11-30"
    assert _bills(nw) == ["2026-11-30", "2026-12-01"] and "2026-12-02" in _later_bills(nw)
    assert nw["spendable_pre_salary"] == {"until": "2026-12-01", "salary_date": "2026-12-01"}
    _bill(client, "2027-01-05", 500.0, "Örnek Aidat")
    nw = finance.net_worth(date(2026, 12, 20))
    _inv(nw)
    assert "2027-01-04" in _bills(nw) and "2027-01-05" in _later_bills(nw)


def test_weekend_salary_margin(client, tmpdb):
    """Maaş 1 Ağu 2026 (Cumartesi) → Pzt 3 Ağu yatar; +1 iş günü pay: 3 Ağu vadeli düşülür, 4 Ağu sonraya."""
    _cycle(tmpdb)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 40000.0,
                                              "as_of": "2026-07-20"}).status_code == 200
    rid = _salary(tmpdb, "Maaş", 50000.0, 1)
    tmpdb.create_entry({"entry_date": "2026-07-01", "description": "Maaş", "amount": 50000.0, "kind": "gelir",
                        "category": "Maaş", "recurring_id": rid, "month": "2026-07"})
    _bill(client, "2026-08-03", 300.0, "Örnek İnternet")
    _bill(client, "2026-08-04", 400.0, "Örnek Telefon")
    nw = finance.net_worth(date(2026, 7, 20))  # dönem sonuna 11 gün: ufuk 31 Tem (maaş-yatınca kayması yok)
    _inv(nw)
    assert _bills(nw) == ["2026-08-03"] and _later_bills(nw) == ["2026-08-04"]


def test_feast_salary_margin(client, tmpdb):
    """Ay dönümü 25. Maaş 28'i: 28 May 2026 Kurban Bayramı (27–30 May) → Pzt 1 Haz yatar; 1 Haz düşülür, 2 Haz sonraya."""
    _cycle(tmpdb, 25)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 40000.0,
                                              "as_of": "2026-05-10"}).status_code == 200
    rid = _salary(tmpdb, "Maaş", 50000.0, 28, start="2026-01")
    tmpdb.create_entry({"entry_date": "2026-04-28", "description": "Maaş", "amount": 50000.0, "kind": "gelir",
                        "category": "Maaş", "recurring_id": rid, "month": "2026-05"})
    _bill(client, "2026-06-01", 300.0, "Örnek İnternet")
    _bill(client, "2026-06-02", 400.0, "Örnek Telefon")
    nw = finance.net_worth(date(2026, 5, 15))  # dönem sonuna 9 gün: ufuk 24 May
    _inv(nw)
    assert nw["spendable_horizon"] == "2026-05-24"
    assert "2026-06-01" in _bills(nw) and _later_bills(nw) == ["2026-06-02"]


@pytest.mark.parametrize("today,exp_until", [(date(2026, 11, 13), None), (date(2026, 11, 20), "2026-12-10")])
def test_late_salary_tolerance_and_deduct_end(client, tmpdb, today, exp_until):
    """Maaş 10'u (10 Kas Salı) yatmadı. 13 Kas tolerans içi (5 iş günü → 17 Kas): sonraki maaş bugün sayılır, ufuk değişmez.
    20 Kas tolerans dışı: sonraki maaş 10 Ara → 10 Ara'ya kadar vadeliler düşülür."""
    _cycle(tmpdb)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 40000.0,
                                              "as_of": "2026-11-01"}).status_code == 200
    rid = _salary(tmpdb, "Maaş", 50000.0, 10)
    tmpdb.create_entry({"entry_date": "2026-10-10", "description": "Maaş", "amount": 50000.0, "kind": "gelir",
                        "category": "Maaş", "recurring_id": rid, "month": "2026-10"})
    _bill(client, "2026-12-05", 300.0, "Örnek İnternet")
    nw = finance.net_worth(today)
    _inv(nw)
    assert (nw["spendable_pre_salary"] or {}).get("until") == exp_until
    assert ("2026-12-05" in _bills(nw)) == bool(exp_until)


def test_late_salary_out_of_tolerance_consistent(client, tmpdb):
    _cycle(tmpdb)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 40000.0,
                                              "as_of": "2026-11-01"}).status_code == 200
    rid = _salary(tmpdb, "Maaş", 50000.0, 10)
    tmpdb.create_entry({"entry_date": "2026-10-10", "description": "Maaş", "amount": 50000.0, "kind": "gelir",
                        "category": "Maaş", "recurring_id": rid, "month": "2026-10"})
    nw = finance.net_worth(date(2026, 11, 20))
    awaited = bool(nw["spendable_after_income"]) and nw["spendable_after_income"]["main"]["date"] == "2026-11-10"
    pre = nw["spendable_pre_salary"]
    assert awaited == (pre is None or pre["salary_date"] == "2026-11-20")


# ---------------------------------------------------------------- ortak defter üyesi

@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def test_member_same_salary_decision(owner, tmpdb, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.main import app

    freeze(monkeypatch, date(2026, 11, 26))
    _cycle(tmpdb)
    for name in ("Örnek Banka A", "Örnek Banka B"):
        assert owner.post("/api/accounts", json={"name": name, "kind": "banka", "balance": 10000.0,
                                                 "as_of": "2026-11-01"}).status_code == 200
    ids = {a["name"]: a["id"] for a in owner.get("/api/accounts").json()["accounts"]}
    rid = _salary(tmpdb, "Maaş", 50000.0, 25, account_id=ids["Örnek Banka A"])
    tmpdb.create_entry({"entry_date": "2026-11-25", "description": "Maaş", "amount": 50000.0, "kind": "gelir",
                        "category": "Maaş", "recurring_id": rid, "month": "2026-11", "account_id": ids["Örnek Banka A"]})
    inv = owner.post("/api/book/invites", json={"note": "es"}).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"code": inv["code"], "username": "esim",
                                              "password": "esim-sifre-12345"}).status_code == 200
    assert owner.put("/api/book/rules", json={"hidden_accounts": [ids["Örnek Banka A"]]}).status_code == 200
    on, mn = owner.get("/api/net-worth").json(), m.get("/api/net-worth").json()
    assert on["spendable_horizon"] == "2026-12-31"
    assert mn["spendable_horizon"] == on["spendable_horizon"]
    # karar (beklenen gelir) aynı; "amount" = spendable + gelir, spendable üyede gizli hesap olmadan (gösterim süzülü)
    pick = ("income", "count", "label", "date", "main")
    assert {k: mn["spendable_after_income"][k] for k in pick} == {k: on["spendable_after_income"][k] for k in pick}
