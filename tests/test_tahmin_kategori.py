"""Kart dışı tahmin kategori başına 6 dönem medyanı (kullanıcı kararı 16.11): geçici patlama (2–3 ay) tahmini şişirmez,
4+ dönemdir süren sayılır; kullanıcı kategoriyi "Bu ay olmayacak" / "Tahmine katma" ile dışlar. Ay dönümü 17; bugün
16 Kas 2026 → tamamlanmış dönemler 2026-10 (17 Eyl–16 Eki, 30 gün) … 2026-05. Kişi, tutar uydurmadır."""
from datetime import date

import pytest

from app import finance

TODAY = date(2026, 11, 16)


def _freeze(monkeypatch, d):
    class _D(date):
        @classmethod
        def today(cls):
            return d
    monkeypatch.setattr(finance, "date", _D)


@pytest.fixture()
def book(client, tmpdb, monkeypatch):
    _freeze(monkeypatch, TODAY)
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 50000.0}).status_code == 200
    return tmpdb


def _gider(db, d, amt, cat):
    db.create_entry({"entry_date": d, "description": f"Örnek {cat}", "amount": amt, "kind": "gider", "category": cat,
                     "payment": "nakit"})


# dönemlerin ortasındaki günler: 2026-05 … 2026-10
GUNLER = ["2026-04-25", "2026-05-25", "2026-06-25", "2026-07-25", "2026-08-25", "2026-09-25"]


def _items(n=None):
    return {x["category"]: x for x in (n or finance.net_worth(TODAY))["forecast_balance"]["variable_cash_items"]}


def test_two_month_burst_is_zero(book):
    """Market 6 dönemin hepsinde 1.000; Sağlık yalnız son 2 dönemde 20.000 → Sağlık medyanı 0, tahmine girmez."""
    for d in GUNLER:
        _gider(book, d, 1000.0, "Market & Gıda")
    for d in GUNLER[-2:]:
        _gider(book, d, 20000.0, "Sağlık")
    it = _items()
    assert "Sağlık" not in it and "Market & Gıda" in it


def test_four_of_six_counts(book):
    """Sağlık 6 dönemin 4'ünde (son 4) → medyan > 0, sayılır."""
    for d in GUNLER:
        _gider(book, d, 1000.0, "Market & Gıda")
    for d in GUNLER[-4:]:
        _gider(book, d, 3000.0, "Sağlık")
    it = _items()
    assert it["Sağlık"]["daily"] > 0
    assert (it["Sağlık"]["months"], it["Sağlık"]["window"]) == (4, 6)
    assert (it["Market & Gıda"]["months"], it["Market & Gıda"]["window"]) == (6, 6)


def test_short_history_uses_available_periods(book):
    """Yalnız son 2 dönem verisi (yeni kullanıcı): olan dönemlerle (öncesi 0 sayılmaz)."""
    for d in GUNLER[-2:]:
        _gider(book, d, 3000.0, "Sağlık")
    it = _items()
    assert it["Sağlık"]["daily"] > 0 and (it["Sağlık"]["months"], it["Sağlık"]["window"]) == (2, 6)


def test_exclude_endpoint(book, client):
    for d in GUNLER:
        _gider(book, d, 1000.0, "Market & Gıda")
        _gider(book, d, 600.0, "Ulaşım")
    n0 = finance.net_worth(TODAY)
    c0 = {x["key"]: x["amount"] for x in n0["forecast_balance"]["parts"]}["variable_cash"]
    r = client.post("/api/forecast/exclude", json={"category": "Ulaşım", "until": "2026-12-16"})
    assert r.status_code == 200 and r.json()["excludes"] == {"Ulaşım": "2026-12-16"}
    n1 = finance.net_worth(TODAY)
    it = _items(n1)
    assert it["Ulaşım"]["excluded"] and it["Ulaşım"]["until"] == "2026-12-16"
    c1 = {x["key"]: x["amount"] for x in n1["forecast_balance"]["parts"]}["variable_cash"]
    assert c1 == round(c0 - it["Ulaşım"]["amount"], 2) and n1["forecast_balance"]["amount"] > n0["forecast_balance"]["amount"]
    # süresi geçince yeniden girer
    assert finance.forecast_excludes(date(2026, 12, 17)) == {}
    # kalıcı, sonra kaldır
    assert client.post("/api/forecast/exclude", json={"category": "Market & Gıda"}).json()["excludes"]["Market & Gıda"] is None
    assert client.request("DELETE", "/api/forecast/exclude", params={"category": "Market & Gıda"}).status_code == 200
    assert "Market & Gıda" not in finance.forecast_excludes(TODAY)


# ---------------------------------------------------------------- "Bu düzenli mi?" (sıklık kalıbı)

def _odeme(db, d, amt=1200.0, kisi="Örnek Temizlikçi", cat="Ev"):
    return db.create_entry({"entry_date": d, "description": f"FAST {kisi}", "amount": amt, "kind": "gider", "category": cat,
                            "payment": "havale", "counterparty": kisi})


def _sorular(client):
    return [i for i in client.get("/api/reminders?regular=1").json() if i.get("kind") == "duzenli_mi"]


def test_biweekly_pattern_asks(book, client):
    for d in ("2026-10-20", "2026-11-03", "2026-11-16"):  # 14, 13 gün
        _odeme(book, d)
    q = _sorular(client)
    assert len(q) == 1 and q[0]["frequency"] == "biweekly" and "2 haftada bir" in q[0]["title"]
    assert q[0]["action"] == "regular_confirm" and q[0]["cta_no"] == "Hayır, geçici"


def test_regular_only_for_opt_in_client_and_snooze(book, client):
    """Eski istemci (?regular yok) bu türü görmez; "Sonra" (snooze) kimliği REMINDER_ID'ye uyar ve maddeyi gizler."""
    for d in ("2026-10-20", "2026-11-03", "2026-11-16"):
        _odeme(book, d)
    assert not [i for i in client.get("/api/reminders").json() if i.get("kind") == "duzenli_mi"]
    rid = _sorular(client)[0]["id"]
    assert client.post(f"/api/reminders/{rid}/snooze", json={"days": 1}).status_code == 200
    assert not _sorular(client)


def test_irregular_does_not_ask(book, client):
    for d in ("2026-10-20", "2026-10-21", "2026-11-10", "2026-11-13"):  # 1, 20, 3 gün
        _odeme(book, d)
    assert not _sorular(client)


def test_temporary_then_new_pattern_asks_again(book, client):
    for d in ("2026-08-01", "2026-08-02", "2026-09-20"):  # düzensiz
        _odeme(book, d)
    assert not _sorular(client)
    key = finance._regular_key({"description": "FAST Örnek Temizlikçi", "counterparty": "Örnek Temizlikçi", "category": "Ev"})
    assert client.post("/api/forecast/temporary", json={"key": key, "pattern": None}).status_code == 200
    for d in ("2026-10-20", "2026-11-03", "2026-11-16"):  # artık 2 haftada bir
        _odeme(book, d)
    q = _sorular(client)
    assert len(q) == 1 and q[0]["changed"] and q[0]["title"].startswith("Örnek Temizlikçi artık 2 haftada bir")
    # aynı kalıp için geçici dendiyse sorulmaz
    client.post("/api/forecast/temporary", json={"key": key, "pattern": "biweekly"})
    assert not _sorular(client)


def test_temporary_group_out_of_estimate(book, client):
    for d in GUNLER:
        _gider(book, d, 1000.0, "Market & Gıda")
    for d in ("2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01"):
        _odeme(book, d, 3000.0)
    assert "Ev" in _items()
    key = finance._regular_key({"description": "FAST Örnek Temizlikçi", "counterparty": "Örnek Temizlikçi", "category": "Ev"})
    client.post("/api/forecast/temporary", json={"key": key, "pattern": "monthly"})
    assert "Ev" not in _items()


def test_yes_creates_biweekly_recurring_and_deducts_twice(book, client):
    """Evet → 2 haftalık sabit kalem (yalnız onayla); geçmiş kayıtlar bağlanır (tahminden çıkar); ufukta (16 Kas son gün →
    16 Ara) dönemin iki oluşumu (24 Kas, 8 Ara) Ödemelerden sonra'dan düşer."""
    for d in ("2026-10-27", "2026-11-10"):
        _odeme(book, d)
    _odeme(book, "2026-10-13")
    q = _sorular(client)[0]
    assert not book.list_recurring()  # otomatik oluşmadı
    r = client.post("/api/recurring", json={"description": "Temizlik", "amount": q["amount"], "kind": "gider",
                                            "category": q["category"], "day_of_month": q["day_of_month"],
                                            "start_month": "2026-11", "frequency": q["frequency"],
                                            "anchor_date": q["anchor_date"], "link_group": q["key"]})
    assert r.status_code == 200, r.text
    assert not _sorular(client)
    nw = finance.net_worth(TODAY)
    dates = sorted(x["date"] for x in nw["recurring_items"] if x["title"] == "Temizlik")
    assert dates == ["2026-11-24", "2026-12-08"]
    assert "Ev" not in _items(nw)
