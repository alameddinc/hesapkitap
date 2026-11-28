"""Sade Özet (yetenek ozet_sade): maaş günü kuralı, /api/upcoming salary_plan ve GET /api/stats/projection.

Bugün sabit: 27 Kas 2026; ay dönümü 17 (dönem 2026-12 = 17 Kas – 16 Ara); maaş 15'i → sıradaki maaş 15 Ara, maaş dönemi
15 Ara – 14 Oca. Kurum adları, kişiler, kart numaraları ve tutarlar uydurmadır."""
from datetime import date

import pytest

from app import db, finance, upcoming
from app.parsers.base import PURCHASE, Statement, Transaction

TODAY = date(2026, 11, 27)
CARD = "4543***1234"
INST = 1250.0
RENT = 15000.0
SALARY = 60000.0
EST = 700.0  # elektrik tahmini (son üç faturanın ortalaması)


def _post(client, url, body):
    r = client.post(url, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _debt(client, amount, due, person="Örnek Kişi"):
    d = _post(client, "/api/debts", {"person": person, "direction": "borc", "amount": amount,
                                     "debt_date": "2026-11-01", "due_date": due, "pay_terms": "tek"})
    return d.get("id") or d["debt"]["id"]


def _jar(monkeypatch, did, amount):
    real = getattr(db.jar_reserved, "_real", db.jar_reserved)
    fake = lambda today=None: {**real(today), "by_debt": {did: amount}}  # noqa: E731
    fake._real = real
    monkeypatch.setattr(db, "jar_reserved", fake)


@pytest.fixture()
def book(client, tmpdb):
    tmpdb.set_setting("cycle_start_day", "17")
    _post(client, "/api/accounts", {"name": "Örnek Banka", "kind": "banka", "balance": 100000.0})
    lines = [Transaction(txn_date=date(2026, 11, 2), description="ORNEK MARKET", amount=2000.0, txn_type=PURCHASE, card=CARD),
             Transaction(txn_date=date(2026, 9, 5), description="ORNEK BEYAZ ESYA", amount=INST, txn_type=PURCHASE,
                         card=CARD, installment_no=2, installments_left=3, installment_left_amount=INST)]
    total = sum(x.amount for x in lines)
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=date(2026, 11, 13), due_date=date(2026, 11, 23),
                  total_debt=total, min_payment=round(total * 0.2, 2), credit_limit=90000, period_activity=total,
                  next_period_end=date(2026, 12, 13), next_due_date=date(2026, 12, 23))
    s.transactions = lines
    tmpdb.save_statement(b"st-ozet-sade", "e.pdf", s)
    sid = next(x["id"] for x in tmpdb.list_statements() if x["period_end"] == "2026-11-13")
    tmpdb.mark_statement_paid(sid, total, "2026-11-14", None)
    _post(client, "/api/recurring", {"description": "Ev sahibi kirası", "amount": RENT, "kind": "gider",
                                     "category": "Kira", "day_of_month": 18, "start_month": "2026-12"})
    _post(client, "/api/recurring", {"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                                     "day_of_month": 15, "start_month": "2026-09"})
    for m, amt in ((9, 600.0), (10, 700.0), (11, 800.0)):  # elektrik: üç ödenmiş fatura (8'i) → tahmin 700
        b = _post(client, "/api/bills", {"provider": "Kurum Örnekenerji Ayesaş Satış", "amount": amt,
                                         "due_date": f"2026-{m:02d}-08", "category": "Elektrik"})
        with tmpdb.connect() as c:
            c.execute("UPDATE bills SET paid_at = ?, paid_amount = amount WHERE id = ?", (f"2026-{m:02d}-07", b["id"]))
    return client


def _up():
    return upcoming.upcoming(TODAY, 45)


def _plan(out=None):
    sp = (out or _up())["salary_plan"]
    return sp, {x["key"]: x for x in sp["items"]}


def _later(nw, kind):
    return [(x["due_date"], x["amount"]) for x in nw["spendable_later"]["items"] if x["type"] == kind]


# ---------------------------------------------------------------- A) maaş günü kuralı

def test_salary_day_debt_not_deducted(book, client):
    """Vadesi tam maaş günü (15 Ara) olan borç maaştan ödenir: Harcanabilir'den düşülmez, before_salary'ye girmez,
    'Maaştan sonra'ya ve salary_plan.debts'e geçer. Bir gün önce vadeli borç (14 Ara) eskisi gibi düşülür."""
    nw0, up0 = finance.net_worth(TODAY), _up()
    _debt(client, 2000.0, "2026-12-15")
    nw1, up1 = finance.net_worth(TODAY), _up()
    assert nw1["spendable"] == nw0["spendable"]
    assert nw1["cards_close_today"] == round(nw1["spendable"] - nw1["cards_accumulated"]["total"], 2)
    assert _later(nw1, "borc") == [("2026-12-15", 2000.0)]
    assert up1["before_salary"] == up0["before_salary"]
    sp, items = _plan(up1)
    assert (items["debts"]["amount"], items["debts"]["count"], items["debts"]["estimate"]) == (2000.0, 1, False)
    # tek yerde: next_period.fixed_noncard'a girer, sonuç tam 2.000 düşer (ne iki kez ne sıfır kez)
    assert up1["next_period"]["fixed_noncard"] == round(up0["next_period"]["fixed_noncard"] + 2000.0, 2)
    assert up1["next_period"]["result"] == round(up0["next_period"]["result"] - 2000.0, 2)
    assert sp["already_reserved"] == 0
    _debt(client, 900.0, "2026-12-14", person="Örnek Komşu")
    nw2, up2 = finance.net_worth(TODAY), _up()
    assert nw2["spendable"] == round(nw1["spendable"] - 900.0, 2)
    assert up2["before_salary"]["total"] == round(up1["before_salary"]["total"] + 900.0, 2)
    assert _plan(up2)[1]["debts"]["amount"] == 2000.0
    assert up2["next_period"]["result"] == round(up1["next_period"]["result"] - 900.0, 2)  # Harcanabilir'den, bir kez


def test_salary_day_bill_and_recurring_paid_from_salary(book, client):
    """Maaş günü vadeli fatura ve düzenli ödeme de maaştan: Harcanabilir aynı, spendable_later'da (düzenli ödeme
    type 'recurring'), salary_plan'da; before_salary'de yok."""
    nw0, up0 = finance.net_worth(TODAY), _up()
    _post(client, "/api/bills", {"provider": "Örnek Site Yönetimi", "amount": 300.0, "due_date": "2026-12-15"})
    _post(client, "/api/recurring", {"description": "Örnek Kurs", "amount": 500.0, "kind": "gider",
                                     "category": "Eğitim", "day_of_month": 15, "start_month": "2026-12"})
    nw1, up1 = finance.net_worth(TODAY), _up()
    assert [x["date"] for x in up1["items"] if x["title"] == "Örnek Kurs"][0] == "2026-12-15"
    assert nw1["spendable"] == nw0["spendable"]
    assert all(x["date"] != "2026-12-15" for x in nw1["bills_items"] + nw1["recurring_items"])
    assert _later(nw1, "bill") == [("2026-12-15", 300.0)] and _later(nw1, "recurring") == [("2026-12-15", 500.0)]
    assert up1["before_salary"] == up0["before_salary"]
    sp0, it0 = _plan(up0)
    sp1, it1 = _plan(up1)
    assert it1["bills"]["amount"] == round(it0["bills"]["amount"] + 300.0, 2)
    # düzenli ödeme: 15 Ara ve pencere içindeki sonraki oluşumu yok (15 Oca pencere dışı: dönem 14 Oca'da biter)
    assert it1["recurring"]["amount"] == round(it0["recurring"]["amount"] + 500.0, 2)
    assert sp1["remaining"] == round(sp0["remaining"] - 800.0, 2)
    assert up1["next_period"]["result"] == round(up0["next_period"]["result"] - 800.0, 2)


def test_no_salary_rule_unchanged(client, tmpdb):
    """Maaş tanımlı değilse davranış aynı: ufuk içindeki borç düşülür; salary_plan null."""
    tmpdb.set_setting("cycle_start_day", "17")
    _post(client, "/api/accounts", {"name": "Örnek Banka", "kind": "banka", "balance": 10000.0})
    _debt(client, 2000.0, "2026-12-15")
    nw = finance.net_worth(TODAY)
    assert nw["spendable"] == 8000.0 and nw["spendable_later"]["items"] == []
    out = _up()
    assert out["salary_plan"] is None and out["next_period"] is None and out["before_salary"] is None


# ---------------------------------------------------------------- B) salary_plan

def test_salary_plan_totals(book, client):
    """Kalemler: kartlar (kartlarda biriken; tahmini), kira 18 Ara, elektrik tahmini 8 Oca. total, remaining, gün."""
    out = _up()
    sp, items = _plan(out)
    nw = finance.net_worth(TODAY)
    assert sp["salary"] == {"date": "2026-12-15", "amount": SALARY} and sp["end_date"] == "2027-01-14"
    assert [x["key"] for x in sp["items"]] == ["cards", "recurring", "bills"]  # borç yok: 0 tutarlı kalem listelenmez
    assert [x["label"] for x in sp["items"]] == ["Kartlar", "Kira ve düzenli ödemeler", "Faturalar"]
    assert items["cards"] == {"key": "cards", "label": "Kartlar", "amount": nw["cards_accumulated"]["total"],
                              "estimate": True, "count": 1}
    assert items["cards"]["amount"] == INST
    assert (items["recurring"]["amount"], items["recurring"]["estimate"], items["recurring"]["count"]) == (RENT, False, 1)
    assert (items["bills"]["amount"], items["bills"]["estimate"], items["bills"]["count"]) == (EST, True, 1)
    assert sp["total"] == INST + RENT + EST and sp["remaining"] == SALARY - sp["total"]
    assert sp["days"] == 31 and sp["remaining_per_day"] == round(sp["remaining"] / 31, 2)
    var, n = upcoming.variable_average(TODAY)
    assert (sp["usual_monthly"], sp["basis_months"]) == (var, n) and sp["usual_per_day"] == round(var / 30.4, 2)
    assert (sp["already_reserved"], sp["missing_count"]) == (0, 0)
    # taksit bilgisi: kartların İÇİNDE, toplamı büyütmez
    assert sp["installments_in_cards"] == finance.installments_due("2026-12", TODAY)["total"] == INST
    # next_period ile aynı satırlar
    assert round(sp["total"] - items["cards"]["amount"], 2) == out["next_period"]["fixed_noncard"]


def test_salary_plan_jar_funded_debt_excluded(book, client, monkeypatch):
    """Kumbarada ayrılmış borç kısmı girmez (para zaten ayrılmış); ayrılmayan kısmı girer."""
    did = _debt(client, 5000.0, "2026-12-31")
    sp, items = _plan()
    assert items["debts"]["amount"] == 5000.0
    _jar(monkeypatch, did, 2000.0)
    sp2, items2 = _plan()
    assert items2["debts"]["amount"] == 3000.0 and sp2["remaining"] == round(sp["remaining"] + 2000.0, 2)
    _jar(monkeypatch, did, 5000.0)
    sp3, items3 = _plan()
    assert "debts" not in items3 and sp3["total"] == round(sp["total"] - 5000.0, 2)


def test_salary_plan_card_counted_once(book, client):
    """Kart harcaması yalnız 'cards' kaleminde: 10.000 eklenince kartlar ve toplam tam 10.000 artar, öbür kalemler
    aynı; pencerede vadesi olan kart ekstresi (23 Ara) recurring/bills/debts'e girmez."""
    sp0, it0 = _plan()
    _post(client, "/api/entries", {"entry_date": "2026-11-20", "description": "Örnek Elektronik", "amount": 10000.0,
                                   "kind": "gider", "category": "Diğer", "payment": "kredi_karti", "card_last4": "1234"})
    out = _up()
    sp1, it1 = _plan(out)
    assert it1["cards"]["amount"] == round(it0["cards"]["amount"] + 10000.0, 2)
    assert {k: v["amount"] for k, v in it1.items() if k != "cards"} == {k: v["amount"] for k, v in it0.items() if k != "cards"}
    assert sp1["total"] == round(sp0["total"] + 10000.0, 2) and sp1["remaining"] == round(sp0["remaining"] - 10000.0, 2)
    assert [x["date"] for x in out["items"] if x["kind"] == "card"] == ["2026-12-23"]
    assert sum(v["amount"] for k, v in it1.items() if k != "cards") == out["next_period"]["fixed_noncard"]


def test_salary_plan_skips_already_reserved(book, client):
    """Maaştan SONRA ama ufuk içinde (16 Ara; dönem sonu) vadeli kesin ödeme Harcanabilir'den zaten düşülmüş: plana
    ikinci kez girmez (already_reserved'da bilgi), sonuç bir kez düşer."""
    nw0, up0 = finance.net_worth(TODAY), _up()
    _debt(client, 1200.0, "2026-12-16")
    nw1, up1 = finance.net_worth(TODAY), _up()
    assert nw1["spendable"] == round(nw0["spendable"] - 1200.0, 2)
    sp, items = _plan(up1)
    assert "debts" not in items and sp["already_reserved"] == 1200.0 and sp["total"] == up0["salary_plan"]["total"]
    assert up1["next_period"]["result"] == round(up0["next_period"]["result"] - 1200.0, 2)


def test_salary_plan_planned_debt_installments(book, client):
    """Ödeme planlı borç: pencereye düşen her taksit kendi gününde (20 Ara → bu maaş; 20 Oca, 20 Şub sonraki dönemler)."""
    _post(client, "/api/debts", {"person": "Örnek Mağaza", "direction": "borc", "creditor_type": "magaza",
                                 "plan_installment": 1000.0, "plan_count": 3, "plan_start": "2026-12", "plan_day": 20,
                                 "debt_date": "2026-11-20"})
    sp, items = _plan()
    assert (items["debts"]["amount"], items["debts"]["count"]) == (1000.0, 1)
    pr = upcoming.projection(TODAY, 4)["periods"]
    base = RENT + EST
    assert [p["fixed"] for p in pr] == [base + 1000.0, base + 1000.0, base + 1000.0, base]


# ---------------------------------------------------------------- C) projection

def _salary_period(tmpdb, day_iso):
    r = next(x for x in tmpdb.list_recurring() if x["description"] == "Maaş")
    return r["id"], next(p for p in ("2026-12", "2027-01", "2027-02", "2027-03") if tmpdb.recurring_due(r, p) == day_iso)


def test_projection_chain(book, client, tmpdb, monkeypatch):
    """Zincir: start = cards_close_today − maaştan önceki tahmin; end_balance = önceki + salary − fixed − variable_avg.
    İlk dönem next_period ile aynı; maaş ay ay değişebilir (aya özel tutar); borç vadesinin dönemine düşer,
    kumbara-fonlu kısmı girmez."""
    for day in ("2026-09-20", "2026-10-20"):
        _post(client, "/api/entries", {"entry_date": day, "description": "Örnek Market", "amount": 3000.0,
                                       "kind": "gider", "category": "Market & Gıda", "payment": "havale"})
    rid, per = _salary_period(tmpdb, "2027-01-15")
    with tmpdb.connect() as c:
        c.execute("INSERT INTO recurring_overrides (recurring_id, month, amount) VALUES (?, ?, ?)", (rid, per, 66000.0))
    did = _debt(client, 4000.0, "2027-03-01")
    _jar(monkeypatch, did, 1500.0)
    out = upcoming.projection(TODAY, 13)
    nw, np_ = finance.net_worth(TODAY), upcoming.upcoming(TODAY, 45)["next_period"]
    assert out["start"] == round(nw["cards_close_today"] - EST, 2)  # 8 Ara elektrik tahmini maaştan önce
    assert (out["variable_avg"], out["basis_months"]) == upcoming.variable_average(TODAY)
    assert out["variable_avg"] > 0
    pr = out["periods"]
    assert len(pr) == 13
    assert [(p["label"], p["year"], p["salary_date"], p["end_date"]) for p in pr[:3]] == [
        ("Ara", 2026, "2026-12-15", "2027-01-14"), ("Oca", 2027, "2027-01-15", "2027-02-14"),
        ("Şub", 2027, "2027-02-15", "2027-03-14")]
    assert pr[-1]["label"] == "Ara" and pr[-1]["year"] == 2027
    assert [p["salary"] for p in pr[:3]] == [SALARY, 66000.0, SALARY]
    # sabitler: kira (18'i) + elektrik tahmini (8'i) her dönem; 1 Mar borcunun kumbara dışı kısmı Şub döneminde
    assert [p["fixed"] for p in pr[:4]] == [RENT + EST, RENT + EST, RENT + EST + 2500.0, RENT + EST]
    assert pr[0]["fixed"] == np_["fixed_noncard"] and pr[0]["end_balance"] == np_["result"]
    bal = out["start"]
    for p in pr:
        bal = round(bal + p["salary"] - p["fixed"] - out["variable_avg"], 2)
        assert p["end_balance"] == bal
        assert set(p) == {"label", "year", "salary_date", "end_date", "salary", "fixed", "end_balance"}


def test_projection_api_and_capability(book, client, monkeypatch):
    monkeypatch.setattr(upcoming, "_today", lambda: TODAY)
    r = client.get("/api/stats/projection?months=3")
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == {"start", "variable_avg", "basis_months", "periods", "limited"} and len(j["periods"]) == 3
    assert j == {**upcoming.projection(TODAY, 3), "limited": False}  # kendi sunucun: sınır yok
    assert len(client.get("/api/stats/projection").json()["periods"]) == 13
    assert client.get("/api/stats/projection?months=0").status_code == 422
    assert client.get("/api/stats/projection?months=99").status_code == 422
    assert "ozet_sade" in client.get("/api/meta").json()["capabilities"]
    sp = client.get("/api/upcoming?days=45").json()["salary_plan"]
    assert set(sp) == {"salary", "end_date", "items", "installments_in_cards", "total", "remaining", "days",
                       "remaining_per_day", "usual_per_day", "usual_monthly", "basis_months", "already_reserved",
                       "missing_count"}


def test_projection_without_salary(client, tmpdb):
    """Maaş yokken: salary_plan null, projection.periods boş; start = cards_close_today."""
    tmpdb.set_setting("cycle_start_day", "17")
    _post(client, "/api/accounts", {"name": "Örnek Banka", "kind": "banka", "balance": 10000.0})
    out = upcoming.projection(TODAY, 13)
    assert out["periods"] == [] and out["start"] == finance.net_worth(TODAY)["cards_close_today"] == 10000.0
    assert upcoming.upcoming(TODAY, 45)["salary_plan"] is None
    assert upcoming.salary_plan(TODAY) is None


def test_projection_stops_when_salary_ends(book, client, tmpdb):
    """Maaş kalemi ileride bitiyorsa dönemler orada biter."""
    rid, per = _salary_period(tmpdb, "2027-02-15")
    r = client.patch(f"/api/recurring/{rid}", json={"end_month": per})
    assert r.status_code == 200, r.text
    pr = upcoming.projection(TODAY, 13)["periods"]
    assert [p["salary_date"] for p in pr] == ["2026-12-15", "2027-01-15", "2027-02-15"]
    assert pr[-1]["end_date"] == "2027-03-14"


def test_late_salary_no_salary_day_rule(client, tmpdb):
    """Günü geçmiş, hâlâ beklenen maaş 'bugün' sayılırken maaş günü kuralı YOK: bugün vadeli fatura düşülmeye devam
    eder, before_salary'de kalır, salary_plan'a ikinci kez girmez."""
    tmpdb.set_setting("cycle_start_day", "1")
    _post(client, "/api/accounts", {"name": "Örnek Banka", "kind": "banka", "balance": 10000.0})
    _post(client, "/api/recurring", {"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                                     "day_of_month": 26, "start_month": "2026-09"})
    _post(client, "/api/bills", {"provider": "Örnek Site Yönetimi", "amount": 640.0, "due_date": TODAY.isoformat()})
    nw, out = finance.net_worth(TODAY), _up()
    assert out["salary"]["date"] == TODAY.isoformat()  # 26 Kas maaşı yatmadı: bugün bekleniyor
    assert nw["spendable"] == 10000.0 - 640.0
    assert out["before_salary"]["total"] == 640.0
    assert "bills" not in {x["key"] for x in out["salary_plan"]["items"]} or \
        _plan(out)[1]["bills"]["amount"] != 640.0
    assert out["salary_plan"]["already_reserved"] == 0
