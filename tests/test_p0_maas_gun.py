"""P0 düzeltmeleri (araştırma raporu "Maaş öncesi ve sonrası dönem modeli", P0): faz modeli DEĞİL, bugünkü hatalar.

H1 iki maaş: her maaş kalemi kendi kaydıyla değerlendirilir; biri yatınca öbürü beklenen gelirde kalır. Ufku kaydıran
   salary_arrived yalnız BİRİNCİL maaşa (beklenen tutarı en büyük) bakar. Bağsız maaş satırı birden çok kalemde en iyi
   eşleşen kaleme bağlanır (işveren/ad > hesap > en yakın gün > tutar ±%20).
H2 date.today(): pending_recurring (ve onu çağıran satır/plan yolları) verilen günü kullanır; geçmiş tarihli hesap
   sistem saatine bakmaz (bu dosyada saat DONDURULMAZ).
H3 28'e yuvarlama: düzenli kalem günü ayın gerçek son gününe kırpılır (31 → Nisan 30, Şubat 28/29).
H5 maaştan önce vadeli kart: ufuktan sonra ama bir sonraki birincil maaştan (+1 iş günü) önce vadeli kesin ödeme
   Ödemelerden sonra'dan düşülür.

Kişi, işveren, kart numarası ve tutarların hepsi UYDURMADIR."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction


# ---------------------------------------------------------------- yardımcılar

def _calendar_month(tmpdb):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(1)


def _salary(tmpdb, desc, amount, day, start="2026-09", **extra):
    return tmpdb.create_recurring({"description": desc, "amount": amount, "kind": "gelir", "category": "Maaş",
                                   "day_of_month": day, "start_month": start, **extra})


def _raw_income(tmpdb, d, desc, amount, account_id=None):
    """Bağsız (recurring_id NULL), genel kategorili banka satırı: dökümden gelmiş gibi; otomatik bağlama devreye girmez."""
    with tmpdb.connect() as c:
        cur = c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, account_id) "
                        "VALUES (?, ?, ?, ?, 'gelir', 'Diğer Gelir', ?)", (d, tmpdb.period_of(d), desc, amount, account_id))
        return cur.lastrowid


def _pending_income(month, today):
    rows = finance.pending_recurring(month, finance.db.list_recurring(), finance.db.list_entries(month), today)
    return {r["description"]: r["amount"] for r in rows if r["kind"] == "gelir" and r.get("pending")}


def _invariants(nw):
    parts = sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in nw["spendable_parts"])
    assert parts == pytest.approx(nw["spendable"], abs=0.011)  # kalemler toplamı
    assert nw["spendable"] <= nw["free"] + 0.001
    fb = nw["forecast_balance"]
    income = (nw["spendable_after_income"] or {}).get("income") or 0.0
    assert fb["amount"] <= fb["no_new_spend"] + 0.001 <= nw["spendable"] + income + 0.002
    # çift sayım yok: aynı kalem hem düşülen hem "Maaştan sonra"da değil
    now = {(x["title"], x["date"]) for x in nw["card_debt_items"]} | {(x["title"], x["date"]) for x in nw["bills_items"]}
    later = {(x["title"], x["due_date"]) for x in nw["spendable_later"]["items"]}
    assert not now & later


# ---------------------------------------------------------------- H1 iki maaş

def test_two_salaries_1st_and_15th_each_counted_separately(tmpdb):
    """Maaş A 1'i 50.000 (birincil), maaş B 15'i 20.000. 5 Kas'ta yalnız A yattı (bağsız döküm satırı): B beklenen
    gelirde kalır (eskiden A görülünce B de silinirdi). Ufuk A'ya bakar: A yattı → salary_arrived True."""
    _calendar_month(tmpdb)
    _salary(tmpdb, "Maaş Örnek Yazılım", 50000.0, 1)
    _salary(tmpdb, "Maaş Deneme Lojistik", 20000.0, 15)
    _raw_income(tmpdb, "2026-11-01", "MAAS ODEMESI 2026 KASIM", 50000.0)
    today = date(2026, 11, 5)
    assert _pending_income("2026-11", today) == {"Maaş Deneme Lojistik": 20000.0}
    assert finance.salary_arrived("2026-11", today) is True
    sai = finance.net_worth(today)["spendable_after_income"]
    assert sai["income"] == 20000.0 and sai["main"]["label"] == "Maaş Deneme Lojistik"


def test_second_salary_alone_does_not_count_as_primary(tmpdb):
    """Yalnız ikinci (küçük) maaş yattı: birincil hâlâ bekleniyor, ufuk kaymaz (salary_arrived False)."""
    _calendar_month(tmpdb)
    _salary(tmpdb, "Maaş Örnek Yazılım", 50000.0, 1)
    _salary(tmpdb, "Maaş Deneme Lojistik", 20000.0, 15)
    _raw_income(tmpdb, "2026-11-15", "MAAS ODEMESI", 20500.0)
    today = date(2026, 11, 25)
    assert _pending_income("2026-11", today) == {"Maaş Örnek Yazılım": 50000.0}
    assert finance.salary_arrived("2026-11", today) is False


def test_same_day_two_employers_name_then_amount(tmpdb):
    """Aynı gün (15'i) iki işveren, tutarlar birbirine yakın (30.000 / 28.000; ikisi de öbürünün ±%20'sinde): işveren adı
    açıklamada geçen kayıt o kaleme; adsız kayıt tutarı en yakın kaleme. Her kayıt bir kez sayılır."""
    _calendar_month(tmpdb)
    _salary(tmpdb, "Maaş Örnekyazılım", 30000.0, 15)
    _salary(tmpdb, "Maaş Denemetekstil", 28000.0, 15)
    _raw_income(tmpdb, "2026-11-15", "ORNEKYAZILIM AS MAAS ODEMESI", 28100.0)  # tutar B'ye yakın ama ad A
    today = date(2026, 11, 16)
    assert _pending_income("2026-11", today) == {"Maaş Denemetekstil": 28000.0}
    _raw_income(tmpdb, "2026-11-15", "MAAS ODEMESI", 28000.0)
    assert _pending_income("2026-11", today) == {}


def test_unmatched_amount_does_not_realize_other_salary(tmpdb):
    """Birden çok maaş kaleminde adı ve tutarı (±%20) hiçbir kaleme uymayan maaş satırı (ör. küçük fark ödemesi)
    hiçbir kalemi "yattı" yapmaz; tek maaş kaleminde eski kural (her maaş satırı o kalemin) sürer."""
    _calendar_month(tmpdb)
    _salary(tmpdb, "Maaş Örnek Yazılım", 50000.0, 1)
    _salary(tmpdb, "Maaş Deneme Lojistik", 20000.0, 15)
    _raw_income(tmpdb, "2026-11-03", "MAAS FARKI", 3000.0)
    assert _pending_income("2026-11", date(2026, 11, 5)) == {"Maaş Örnek Yazılım": 50000.0,
                                                               "Maaş Deneme Lojistik": 20000.0}


def test_single_salary_keeps_old_rule(tmpdb):
    _calendar_month(tmpdb)
    _salary(tmpdb, "Maaş", 50000.0, 1)
    _raw_income(tmpdb, "2026-11-02", "MAAS ODEMESI", 61000.0)  # zam: ±%20 dışında ama tek kalem
    assert _pending_income("2026-11", date(2026, 11, 5)) == {}
    assert finance.salary_arrived("2026-11", date(2026, 11, 5)) is True


def test_link_salary_entries_picks_best_salary(tmpdb):
    """Bağlama da aynı kural: iki maaş kaleminde işveren kaydı yoksa en iyi eşleşen kalemin boş dönemine bağlanır;
    hiçbirine uymayan satır bağlanmaz (yalnız kategori Maaş olur)."""
    _calendar_month(tmpdb)
    a = _salary(tmpdb, "Maaş Örnek Yazılım", 50000.0, 1)
    b = _salary(tmpdb, "Maaş Deneme Lojistik", 20000.0, 15)
    e1 = _raw_income(tmpdb, "2026-11-01", "MAAS ODEMESI", 50000.0)
    e2 = _raw_income(tmpdb, "2026-11-16", "MAAS ODEMESI", 19800.0)
    e3 = _raw_income(tmpdb, "2026-11-20", "MAAS FARKI", 1500.0)
    tmpdb.link_salary_entries()
    got = {e["id"]: (e["recurring_id"], e["category"]) for e in tmpdb.list_entries("2026-11")}
    assert got[e1] == (a, "Maaş") and got[e2] == (b, "Maaş") and got[e3] == (None, "Maaş")
    assert _pending_income("2026-11", date(2026, 11, 20)) == {}


@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def test_family_book_two_people_salaries(owner):
    """Aile defteri: sahip ve eşinin maaşları ayrı kalem, ayrı hesap. Eşi (üye) kendi maaşını kendi hesabına girer
    (adsız "MAAS ODEMESI"; iki maaş da ayın 1'i, tutar ikisinin de ±%20'sinde, sahibinkine daha yakın): hesap eşinin
    kalemini seçer, sahibin maaşı beklenmeye devam eder. Dönem uzak gelecekte: CI hangi gün koşarsa koşsun "bekliyor" (geçmiş dönem tahmini değil)."""
    from app.api.main import app

    for name in ("Örnek Banka A", "Örnek Banka B"):
        assert owner.post("/api/accounts", json={"name": name, "kind": "banka", "balance": 1000}).status_code == 200
    ids = {a["name"]: a["id"] for a in owner.get("/api/accounts").json()["accounts"]}
    acc_a, acc_b = ids["Örnek Banka A"], ids["Örnek Banka B"]
    for desc, amt, day, acc in (("Ayşe maaş", 40000.0, 1, acc_a), ("Ali maaş", 35000.0, 1, acc_b)):
        r = owner.post("/api/recurring", json={"description": desc, "amount": amt, "kind": "gelir", "category": "Maaş",
                                               "day_of_month": day, "start_month": "2030-01", "account_id": acc})
        assert r.status_code == 200, r.text
    inv = owner.post("/api/book/invites", json={"note": "es"}).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"code": inv["code"], "username": "esim",
                                              "password": "esim-sifre-12345"}).status_code == 200
    r = m.post("/api/entries", json={"entry_date": "2030-03-01", "description": "MAAS ODEMESI", "amount": 37500.0,
                                     "kind": "gelir", "category": "Diğer Gelir", "payment": "havale",
                                     "account_id": acc_b})
    assert r.status_code == 200, r.text
    pend = owner.get("/api/summary", params={"month": "2030-03"}).json()["pending"]
    assert pend["income"] == 40000.0 and pend["count"] == 1


# ---------------------------------------------------------------- H2 date.today() yerine verilen gün

def test_pending_recurring_uses_given_day_not_system_clock(tmpdb):
    """Ekim maaşı 30 Eki (Cuma); 2 Kas'ta Ekim dönemi geçmiş ama maaş tolerans içinde (5 iş günü): "bekliyor".
    Eskiden geçmiş/tolerans sistem saatine göre hesaplanırdı (16 Kas'ta tolerans bitmiş → tahmini gerçek gelir)."""
    _calendar_month(tmpdb)
    _salary(tmpdb, "Maaş", 42000.0, 30)
    day = date(2026, 11, 2)
    assert _pending_income("2026-10", day) == {"Maaş": 42000.0}
    sai = finance.net_worth(day)["spendable_after_income"]
    assert sai["income"] == 42000.0 + 42000.0  # Ekim (bekliyor) + Kasım (30 Kas)
    # Ekim'in kendi içinden bakınca dönem geçmiş değil: tahmin satırı yok, yalnız bekleyen
    rows = finance.pending_recurring("2026-10", tmpdb.list_recurring(), [], date(2026, 10, 20))
    assert [r.get("salary_estimate") for r in rows] == [None] and rows[0]["pending"]
    # tolerans bittikten sonra (geçmiş tarihli de olsa): tahmini gerçek gelir
    rows = finance.pending_recurring("2026-10", tmpdb.list_recurring(), [], date(2026, 11, 13))
    assert rows[0]["salary_estimate"] and not rows[0]["pending"]


def test_plan_past_day_counts_salary_once(tmpdb):
    """plan(today=geçmiş gün): satırlar (transactions) ve bekleyenler aynı güne bakar; maaş bir kez sayılır."""
    _calendar_month(tmpdb)
    _salary(tmpdb, "Maaş", 30000.0, 15, start="2026-01")
    months = {m["month"]: m["income"] for m in finance.plan(2, today=date(2026, 3, 2))["months"]}
    assert months == {"2026-03": 30000.0, "2026-04": 30000.0}


# ---------------------------------------------------------------- H3 ayın gerçek son günü

def test_occurrence_date_real_month_end(tmpdb):
    od = tmpdb.occurrence_date
    assert od("2026-04", 31, 1) == "2026-04-30"
    assert od("2026-05", 31, 1) == "2026-05-31"
    assert od("2026-02", 31, 1) == "2026-02-28"
    assert od("2028-02", 30, 1) == "2028-02-29"  # artık yıl
    assert od("2026-02", 29, 1) == "2026-02-28"
    assert od("2026-03", 31, 17) == "2026-02-28"  # ay dönümü 17: Mart dönemi 17 Şub – 16 Mar
    assert od("2026-01", 31, 17) == "2025-12-31"
    assert od("2026-11", 30, 17) == "2026-10-30"


def test_salary_and_rent_on_31st(tmpdb):
    _calendar_month(tmpdb)
    _salary(tmpdb, "Maaş", 45000.0, 31)
    tmpdb.create_recurring({"description": "Kira", "amount": 18000.0, "kind": "gider", "category": "Kira",
                            "day_of_month": 31, "start_month": "2026-09"})
    rec = tmpdb.list_recurring()
    dates = {m: {r["description"]: r["date"] for r in finance.pending_recurring(m, rec, [], date(2026, 9, 1))}
             for m in ("2026-10", "2026-11", "2027-02")}
    assert dates["2026-10"] == {"Maaş": "2026-10-31", "Kira": "2026-10-31"}
    assert dates["2026-11"] == {"Maaş": "2026-11-30", "Kira": "2026-11-30"}
    assert dates["2027-02"] == {"Maaş": "2027-02-28", "Kira": "2027-02-28"}


# ---------------------------------------------------------------- H5 maaştan önce vadeli kesin ödeme

CARD = "5400***0007"
CASH = 30000.0
CARD_TOTAL = 8000.0


@pytest.fixture()
def pre_salary_book(client, tmpdb):
    """Takvim ayı. Kasım maaşı (5'i, 60.000) yattı. Kart ekstresi 18 Kas kesim, son ödeme 2 Ara (ödenmedi);
    fatura 10 Ara. 20 Kas'ta ufuk 30 Kas (dönem sonuna 10 gün)."""
    _calendar_month(tmpdb)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": CASH}).status_code == 200
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=date(2026, 11, 18), due_date=date(2026, 12, 2),
                  total_debt=CARD_TOTAL, min_payment=1600.0, credit_limit=90000, period_activity=CARD_TOTAL,
                  next_period_end=date(2026, 12, 18), next_due_date=date(2027, 1, 4))
    s.transactions = [Transaction(txn_date=date(2026, 11, 10), description="ORNEK MARKET", amount=CARD_TOTAL,
                                  txn_type=PURCHASE, card=CARD)]
    tmpdb.save_statement(b"st-p0-pre", "e.pdf", s)
    assert client.post("/api/bills", json={"provider": "Örnek Su", "amount": 700.0,
                                           "due_date": "2026-12-10"}).status_code == 200
    return tmpdb


def _with_salary(tmpdb, day):
    rid = _salary(tmpdb, "Maaş", 60000.0, day)
    tmpdb.create_entry({"entry_date": f"2026-11-{day:02d}", "description": "Maaş", "amount": 60000.0, "kind": "gelir",
                        "category": "Maaş", "recurring_id": rid, "month": "2026-11"})


def test_card_due_before_next_salary_is_deducted(pre_salary_book):
    """Sonraki maaş 5 Ara (Cumartesi → Pazartesi 7 Ara; +1 iş günü payı: 7 Ara dahil). 2 Ara vadeli ekstre ufuk (30 Kas)
    dışında ama maaştan önce: Ödemelerden sonra'dan düşülür, "Maaştan sonra"da değil. 10 Ara faturası maaştan sonra."""
    _with_salary(pre_salary_book, 5)
    nw = finance.net_worth(date(2026, 11, 20))
    assert nw["spendable_horizon"] == "2026-11-30"
    assert nw["spendable_pre_salary"] == {"until": "2026-12-07", "salary_date": "2026-12-05"}
    assert {p["key"]: p["amount"] for p in nw["spendable_parts"]}["card_debt"] == CARD_TOTAL
    assert nw["spendable"] == CASH - CARD_TOTAL
    assert [(x["type"], x["due_date"]) for x in nw["spendable_later"]["items"]] == [("bill", "2026-12-10")]
    _invariants(nw)


def test_card_due_after_next_salary_stays_later(pre_salary_book):
    """Maaş 1'inde: sonraki maaş 1 Ara (Salı), pay 1 Ara'ya kadar (ufuk 30 Kas'tan bir gün öte; o güne vadeli bir şey
    yok). 2 Ara vadeli ekstre maaştan sonra: düşülmez, "Maaştan sonra"da kalır."""
    _with_salary(pre_salary_book, 1)
    nw = finance.net_worth(date(2026, 11, 20))
    assert nw["spendable_pre_salary"] == {"until": "2026-12-01", "salary_date": "2026-12-01"}
    assert nw["spendable"] == CASH
    assert ("card", "2026-12-02") in [(x["type"], x["due_date"]) for x in nw["spendable_later"]["items"]]
    _invariants(nw)


def test_no_salary_no_change(pre_salary_book):
    nw = finance.net_worth(date(2026, 11, 20))
    assert nw["spendable_pre_salary"] is None and nw["spendable"] == CASH
    _invariants(nw)


# ---------------------------------------------------------------- doğrulama turu (P1–P6) ek testleri

def test_link_single_salary_skips_extra_and_small(tmpdb):
    """Tek maaş kaleminde de "MAAS FARKI" ve planlananın yarısından küçük satır o dönemin yerine bağlanmaz (yalnız
    kategori); asıl maaş gelince o bağlanır."""
    _calendar_month(tmpdb)
    rid = _salary(tmpdb, "Maaş", 50000.0, 15)
    e1 = _raw_income(tmpdb, "2026-11-05", "MAAS FARKI ODEMESI", 2500.0)
    e2 = _raw_income(tmpdb, "2026-11-06", "MAAS AVANS", 10000.0)
    e3 = _raw_income(tmpdb, "2026-11-16", "MAAS ODEMESI", 50000.0)
    tmpdb.link_salary_entries()
    got = {e["id"]: e["recurring_id"] for e in tmpdb.list_entries("2026-11")}
    assert got[e1] is None and got[e2] is None and got[e3] == rid


def test_deleted_salary_entries_not_relinked(tmpdb):
    """Silinen maaş kaleminin kaydı kalan kaleme bağlanmaz (former_recurring_id)."""
    _calendar_month(tmpdb)
    a = _salary(tmpdb, "Maaş Örnek Yazılım", 60000.0, 15)
    b = _salary(tmpdb, "Maaş Deneme Lojistik", 55000.0, 15)
    e = _raw_income(tmpdb, "2026-11-15", "MAAS ODEMESI", 55000.0)
    tmpdb.link_salary_entries()
    assert {x["id"]: x["recurring_id"] for x in tmpdb.list_entries("2026-11")}[e] == b
    tmpdb.delete_recurring(b)
    tmpdb.link_salary_entries()
    row = {x["id"]: x for x in tmpdb.list_entries("2026-11")}[e]
    assert row["recurring_id"] is None and row["former_recurring_id"] == b
    assert a and _pending_income("2026-11", date(2026, 11, 16)) == {"Maaş Örnek Yazılım": 60000.0}


def test_statement_paid_uses_given_today(tmpdb):
    """İleri tarihli "ödendi" işareti: hesabın günü (today) sistem gününden geride olsa da o gün ödenmiş sayılır."""
    st = {"paid_at": "2026-11-25", "paid_amount": 1000.0, "total_debt": 1000.0}
    assert tmpdb.statement_paid(st, "2026-11-20", "2026-11-20") == 1000.0  # bugün = 20 Kas: ileri tarihli de sayılır
    assert tmpdb.statement_paid(st, "2026-11-20", "2026-11-22") is None    # geçmiş gün sorusu: 25'i henüz değil
    assert tmpdb.statement_remaining(st, "2026-11-20", "2026-11-20") == 0.0


def test_list_debts_schedule_uses_given_today(client, tmpdb):
    r = client.post("/api/debts", json={"person": "Örnek Mağaza", "direction": "borc", "creditor_type": "magaza",
                                        "plan_installment": 1000.0, "plan_count": 6, "plan_start": "2030-01",
                                        "plan_day": 5, "debt_date": "2029-12-20"})
    assert r.status_code == 200, r.text
    a = tmpdb.list_debts(today=date(2030, 1, 2))[0]["schedule"]
    b = tmpdb.list_debts(today=date(2030, 3, 2))[0]["schedule"]
    assert a != b


# ---------------------------------------------------------------- gecikmiş maaş: "Gecikti — maaş yattı mı?" (lead P6)

def _late_book(tmpdb):
    """Takvim ayı; maaş 10'u (10 Kas 2026 Salı), Kasım'da yatmadı. Tolerans 5 iş günü → 17 Kas."""
    _calendar_month(tmpdb)
    return _salary(tmpdb, "Maaş", 50000.0, 10)


def test_late_salary_status_not_in_expected_income(tmpdb):
    rid = _late_book(tmpdb)
    day = date(2026, 11, 20)
    row = next(r for r in finance.transactions("2026-11", today=day) if r.get("recurring_id") == rid)
    assert row["pending"] and row["status"] == "gecikti" and row["status_label"] == "Gecikti — maaş yattı mı?"
    assert row["actions"] == ["yatti_dokum", "yatti_elle", "bu_ay_yok"]
    pend = finance.summary("2026-11", today=day)["pending"]
    assert pend["income"] == 0 and pend["late_income"] == 50000.0 and pend["late_count"] == 1
    m = finance.plan(1, today=day)["months"][0]
    line = next(l for l in m["lines"] if l.get("recurring_id") == rid)
    assert line["status"] == "gecikti" and line["amount"] == 50000.0
    assert m["income"] == 0 and m["parts"]["pending_income"] == 0 and "gelir" not in m["groups"]
    nw = finance.net_worth(day)
    assert nw["spendable_after_income"] is None  # Maaş gelince'de yok; next_salary_date de 10 Ara'yı esas alır
    assert nw["spendable_pre_salary"]["salary_date"] == "2026-12-10"


def test_salary_within_tolerance_still_waiting(tmpdb):
    rid = _late_book(tmpdb)
    day = date(2026, 11, 13)
    row = next(r for r in finance.transactions("2026-11", today=day) if r.get("recurring_id") == rid)
    assert row["pending"] and "status" not in row
    assert finance.summary("2026-11", today=day)["pending"]["income"] == 50000.0


def test_late_salary_actions_clear_status(client, tmpdb):
    """"Bu ay yok" (skip) ya da "yattı" (confirm): satır gecikmiş olmaktan çıkar."""
    rid = _late_book(tmpdb)
    day = date(2026, 11, 20)
    assert client.post(f"/api/recurring/{rid}/skip", json={"month": "2026-11"}).status_code == 200
    assert not [r for r in finance.transactions("2026-11", today=day) if r.get("status") == "gecikti"]
