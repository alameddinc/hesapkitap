"""Ufuk maaş yatınca kayar (kullanıcı kararı 16.11): dönem sonuna ≤7 gün kaldı ve bu dönemin maaşı yattıysa Ödemelerden
sonra / forecast_balance / in_horizon ufku sonraki dönem sonu. Maaş yatmadıysa eski kural (son gün). Ay dönümü 17:
2026-12 = 17 Kas – 16 Ara, 2027-01 = 17 Ara – 16 Oca. Kart, tutar ve kişiler uydurmadır."""
from datetime import date

import pytest

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction

CARD = "5400***0002"
INST = 1500.0
SALARY = 40000.0


def _freeze(monkeypatch, d):
    class _D(date):
        @classmethod
        def today(cls):
            return d
    monkeypatch.setattr(finance, "date", _D)


@pytest.fixture()
def book(client, tmpdb):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 50000.0}).status_code == 200
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=date(2026, 11, 13), due_date=date(2026, 11, 23),
                  total_debt=3000.0, min_payment=600.0, credit_limit=90000, period_activity=3000.0,
                  next_period_end=date(2026, 12, 13), next_due_date=date(2026, 12, 23))
    s.transactions = [Transaction(txn_date=date(2026, 11, 5), description="ORNEK MARKET", amount=1500.0, txn_type=PURCHASE, card=CARD),
                      Transaction(txn_date=date(2026, 9, 5), description="ORNEK BEYAZ ESYA", amount=INST, txn_type=PURCHASE,
                                  card=CARD, installment_no=2, installments_left=4, installment_left_amount=INST)]
    tmpdb.save_statement(b"st-ufuk", "e.pdf", s)
    sid = next(x["id"] for x in tmpdb.list_statements())
    tmpdb.mark_statement_paid(sid, 3000.0, "2026-11-20", None)
    tmpdb.create_recurring({"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                            "day_of_month": 15, "start_month": "2026-09"})
    for d in ("2026-09-15", "2026-10-15", "2026-11-14"):
        tmpdb.create_entry({"entry_date": d, "description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş"})
    return tmpdb


def _maas(tmpdb, d):
    return tmpdb.create_entry({"entry_date": d, "description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş"})


def _nw(monkeypatch, d):
    _freeze(monkeypatch, d)
    return finance.net_worth(d)


def _kart_ana(nw):
    return {x["due_date"] for x in nw["pending_cards_items"]}


def _kart_later(nw):
    return {x["due_date"] for x in nw["spendable_later"]["items"] if x["type"] == "card_estimate"}


def test_no_salary_yet_cards_later(book, monkeypatch):
    nw = _nw(monkeypatch, date(2026, 12, 14))
    assert nw["spendable_horizon"] == "2026-12-16"
    assert "2026-12-23" in _kart_later(nw) and not _kart_ana(nw)


def test_salary_entered_moves_horizon(book, monkeypatch):
    _maas(book, "2026-12-15")
    nw = _nw(monkeypatch, date(2026, 12, 15))
    assert nw["spendable_horizon"] == "2027-01-16"
    assert _kart_ana(nw) == {"2026-12-23"} and not _kart_later(nw)
    est = next(p for p in finance.payables(date(2026, 12, 15)) if p["type"] == "estimate")
    assert est["in_horizon"] is True
    # forecast: Aralık maaşı nakitte (girildi), beklenen gelir yalnız Ocak maaşı — çift sayım yok
    assert nw["spendable_after_income"]["income"] == SALARY
    fb = nw["forecast_balance"]
    assert fb["after_salary"]["income"] == SALARY and fb["horizon"] == "2027-01-16"
    assert fb["until"] == "2027-01-14" and fb["no_new_spend"] == nw["spendable"]


def test_early_salary_before_holiday(book, monkeypatch):
    """Bayram öncesi 13 Ara'da erken yattı: 13'ünde (dönem sonuna 3 gün) ufuk kayar."""
    eid = _maas(book, "2026-12-13")
    assert next(e for e in book.list_entries() if e["id"] == eid)["month"] == "2026-12"
    nw = _nw(monkeypatch, date(2026, 12, 13))
    assert nw["spendable_horizon"] == "2027-01-16"


def test_late_salary_last_day_rule(book, monkeypatch):
    """Maaş 18 Ara'ya kaydı: 14 Ara'da ufuk 16 Ara; 16 Ara (son gün) eski kuralla kayar."""
    assert _nw(monkeypatch, date(2026, 12, 14))["spendable_horizon"] == "2026-12-16"
    assert _nw(monkeypatch, date(2026, 12, 16))["spendable_horizon"] == "2027-01-16"


def test_more_than_7_days_left_no_shift(book, monkeypatch):
    """Dönem sonuna 8+ gün varken maaş girilse de (ör. 8 Ara erken) ufuk kaymaz."""
    _maas(book, "2026-12-08")
    assert _nw(monkeypatch, date(2026, 12, 8))["spendable_horizon"] == "2026-12-16"


def test_no_salary_item_old_rule(client, tmpdb, monkeypatch):
    tmpdb.set_cycle_start_day(17)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 1000.0}).status_code == 200
    _freeze(monkeypatch, date(2026, 12, 14))
    assert finance.spend_horizon(date(2026, 12, 14))["end"] == date(2026, 12, 16)


# ---------------------------------------------------------------- maaşa kadar (kullanıcı kararı 16.11, uydurma tutarlar)

def _kira(tmpdb):
    tmpdb.create_recurring({"description": "Kira", "amount": 12000.0, "kind": "gider", "category": "Kira & Aidat",
                            "day_of_month": 18, "start_month": "2026-09"})
    for d in ("2026-09-18", "2026-10-18", "2026-11-16"):  # Kasım kirası 16 Kas'ta erken ödendi
        tmpdb.create_entry({"entry_date": d, "description": "Kira", "amount": 12000.0, "kind": "gider", "category": "Kira & Aidat"})


def _nakit(tmpdb, d, amt, desc="Örnek Market", **kw):
    return tmpdb.create_entry({"entry_date": d, "description": desc, "amount": amt, "kind": "gider",
                               "category": kw.pop("category", "Market & Gıda"), "payment": "nakit", **kw})


def test_last_day_main_number_is_until_salary(book, monkeypatch):
    """16 Kas son gün (canlı vaka): ana sayı 15 Ara maaşından bir gün öncesine (14 Ara) kadar = Ödemelerden sonra −
    nakitten çıkacak tahmin. Maaş eklenmez, kira (18 Ara) ve Aralık kart ekstresi (23 Ara) bu sayıda yok."""
    _kira(book)
    for m in ("08", "09", "10"):
        _nakit(book, f"2026-{m}-20", 3100.0)
    nw = _nw(monkeypatch, date(2026, 11, 16))
    fb = nw["forecast_balance"]
    p = {x["key"]: x["amount"] for x in fb["parts"]}
    assert (fb["until"], fb["label_kind"]) == ("2026-12-14", "maasa_kadar")
    assert p["income"] == 0 and p["funded"] == 0 and fb["funded_items"] == [] and fb["no_new_spend"] == nw["spendable"]
    assert p["variable_card"] == 0 and p["variable_card_later"] > 0  # Aralık ekstresi maaştan sonra: yalnız bilgi
    # tamamlanmış dönemler 2026-10 (30 gün) ve 2026-09 (31 gün; 20 Eki bugünkü dönemde): günlük medyan × 28 gün (→ 14 Ara)
    assert p["variable_cash"] == pytest.approx((3100.0 / 30 + 3100.0 / 31) / 2 * 28, abs=1)
    assert fb["amount"] == round(nw["spendable"] - p["variable_cash"], 2) <= nw["spendable"]
    assert fb["after_salary"]["date"] == "2026-12-15" and fb["after_salary"]["income"] == SALARY


def test_cash_estimate_excludes_non_spending(book, monkeypatch):
    """Kart dışı tahmin şişmesin: kendi adına havale, Bağış & Sadaka, sabit kaleme bağlı kayıt ve tek seferlik büyük
    kayıt (medyanın 3 katı) sayılmaz / kırpılır."""
    book.remember_owner_name("AYSE ORNEK")
    for m in ("08", "09", "10"):
        for i in (5, 12, 22):
            _nakit(book, f"2026-{m}-{i:02d}", 1000.0)
        _nakit(book, f"2026-{m}-15", 50000.0, desc="GIDEN FAST - AYSE ORNEK TARAFINDAN GONDERILEN", import_hash=f"h-own-{m}",
               category="Kategorisiz")  # kendi adına (alıcısız)
        _nakit(book, f"2026-{m}-16", 2000.0, category="Bağış & Sadaka")
    _nakit(book, "2026-09-10", 90000.0)  # tek seferlik aykırılar (2026-09 ve 2026-10 dönemleri)
    _nakit(book, "2026-10-10", 90000.0)
    nw = _nw(monkeypatch, date(2026, 11, 16))
    p = {x["key"]: x["amount"] for x in nw["forecast_balance"]["parts"]}
    # dönemler: 2026-10 (17 Eyl–16 Eki, 30 gün) 3×1.000 + aykırı 3×medyan(1.000) = 6.000; 2026-09 (31 gün) 6.000;
    # 2026-08 (31 gün) 2.000. Kendi adına havale ve sadaka hiç sayılmaz. Günlük medyan 6.000/31 × 28 gün
    assert p["variable_cash"] == pytest.approx(6000.0 / 31 * 28, abs=1)


def test_no_salary_item_is_period_end(client, tmpdb, monkeypatch):
    tmpdb.set_cycle_start_day(17)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 1000.0}).status_code == 200
    fb = _nw(monkeypatch, date(2026, 11, 25))["forecast_balance"]
    assert (fb["until"], fb["label_kind"], fb["after_salary"]) == ("2026-12-16", "donem_sonu", None)
