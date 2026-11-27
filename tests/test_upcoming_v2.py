"""Yaklaşan v2 (yetenek upcoming_v2; GET /api/upcoming), cards_close_today ve aylık ortalama (monthly_net).

Bugün sabit: 27 Kas 2026; ay dönümü 17 (2026-12 = 17 Kas – 16 Ara). Pencere 45 gün: 27 Kas – 11 Oca.
Kurum adları, kişiler, kart numaraları ve tutarlar uydurmadır."""
from datetime import date

import pytest

from app import finance, upcoming
from app.parsers.base import PURCHASE, Statement, Transaction

TODAY = date(2026, 11, 27)
CARD = "4543***1234"
INST = 1250.0
RENT = 15000.0
SALARY = 60000.0


def _stmt(tmpdb, *, pe, due, nxt_pe, nxt_due, lines):
    total = sum(x.amount for x in lines)
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=pe, due_date=due, total_debt=total,
                  min_payment=round(total * 0.2, 2), credit_limit=90000, period_activity=total,
                  next_period_end=nxt_pe, next_due_date=nxt_due)
    s.transactions = lines
    tmpdb.save_statement(f"st-{CARD}-{pe}".encode(), "e.pdf", s)
    return next(x["id"] for x in tmpdb.list_statements() if x["period_end"] == pe.isoformat())


def _post(client, url, body):
    r = client.post(url, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _bill(client, provider, amount, due, paid=None, category=None, description=None):
    b = _post(client, "/api/bills", {"provider": provider, "amount": amount, "due_date": due,
                                     **({"category": category} if category else {}),
                                     **({"description": description} if description else {})})
    if paid:
        from app import db

        with db.connect() as c:
            c.execute("UPDATE bills SET paid_at = ?, paid_amount = amount WHERE id = ?", (paid, b["id"]))
    return b["id"]


def _entry(client, day, desc, amount, category, payment="havale", **kw):
    return _post(client, "/api/entries", {"entry_date": day, "description": desc, "amount": amount, "kind": "gider",
                                          "category": category, "payment": payment, **kw})


@pytest.fixture()
def book(client, tmpdb):
    tmpdb.set_setting("cycle_start_day", "17")
    _post(client, "/api/accounts", {"name": "Örnek Banka", "kind": "banka", "balance": 100000.0})
    lines = [Transaction(txn_date=date(2026, 11, 2), description="ORNEK MARKET", amount=2000.0, txn_type=PURCHASE, card=CARD),
             Transaction(txn_date=date(2026, 9, 5), description="ORNEK BEYAZ ESYA", amount=INST, txn_type=PURCHASE,
                         card=CARD, installment_no=2, installments_left=3, installment_left_amount=INST)]
    sid = _stmt(tmpdb, pe=date(2026, 11, 13), due=date(2026, 11, 23), nxt_pe=date(2026, 12, 13),
                nxt_due=date(2026, 12, 23), lines=lines)
    tmpdb.mark_statement_paid(sid, 3250.0, "2026-11-14", None)
    _post(client, "/api/recurring", {"description": "Ev sahibi kirası", "amount": RENT, "kind": "gider",
                                     "category": "Kira", "day_of_month": 18, "start_month": "2026-12"})
    _post(client, "/api/recurring", {"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                                     "day_of_month": 15, "start_month": "2026-09"})
    # elektrik: üç ödenmiş fatura (8 Eyl/Eki/Kas) → tahmin ortalaması 700
    for m, amt in ((9, 600.0), (10, 700.0), (11, 800.0)):
        _bill(client, "Kurum Örnekenerji Ayesaş Satış", amt, f"2026-{m:02d}-08", paid=f"2026-{m:02d}-07",
              category="Elektrik")
    return client


def _up(days=45):
    return upcoming.upcoming(TODAY, days)


def _by(out, **f):
    return [x for x in out["items"] if all(x.get(k) == v if k != "source" else x["source"]["type"] == v
                                           for k, v in f.items())]


# ---------------------------------------------------------------- kurum adı

@pytest.mark.parametrize("raw, cat, short, prov", [
    ("Kurum Örnekenerji Ayesaş Satış", "Elektrik", "Elektrik", "Örnekenerji Ayesaş"),
    ("Kurum Örnektel Fiber İnternet Hizm. A.Ş.", None, "İnternet", "Örnektel"),
    ("KURUM ÖRNEK SU VE KANALİZASYON İDARESİ GN.MD.", None, "Su", "Örnek Su"),
    ("Kurum Örnekgaz Doğalgaz Dağıtım A.Ş.", "Faturalar", "Doğalgaz", "Örnekgaz"),
    ("ÖRNEK DENEME TİC. A.Ş.", None, "Örnek Deneme", "Örnek Deneme"),
    ("İLKER IŞIK", None, "İlker Işık", "İlker Işık"),
])
def test_short_title(raw, cat, short, prov):
    assert upcoming.short_title(raw, cat) == short
    assert upcoming.clean_provider(raw) == prov


def test_user_given_name_unchanged(book):
    """Kullanıcının verdiği ad (düzenli ödeme) sadeleştirilmez."""
    rent = _by(_up(), kind="recurring")
    assert {x["title"] for x in rent} == {"Ev sahibi kirası"} and {x["short_title"] for x in rent} == {"Ev sahibi kirası"}


# ---------------------------------------------------------------- satırlar

def test_rent_outside_horizon_visible(book):
    """18 Ara kirası Ödemelerden sonra ufkunun (16 Ara) dışında ama pencerede: görünür, kesin. 18 Kas'ınki girilmemiş:
    gecikti, kesin. spendable bu kuralla değişmez (sonraki dönemin kirası düşülmez)."""
    out = _up()
    rent = {x["date"]: x for x in _by(out, kind="recurring")}
    assert set(rent) == {"2026-11-18", "2026-12-18"}
    assert rent["2026-12-18"]["certainty"] == "kesin" and rent["2026-12-18"]["amount"] == RENT
    assert rent["2026-11-18"]["note"] == "gecikti"
    nw = finance.net_worth(TODAY)
    assert nw["recurring_items"] and all(x["date"] != "2026-12-18" for x in nw["recurring_items"])


def test_card_estimate_is_so_far(book, client):
    """Ekstresi kesilmemiş kart: son ödeme gününde (23 Ara), tutar şimdiye kadarki (taksit + kartla girilen), tahmini."""
    _entry(client, "2026-11-20", "Örnek Kırtasiye", 400.0, "Diğer", payment="kredi_karti", card_last4="1234")
    out = _up()
    c = _by(out, kind="card")
    assert len(c) == 1
    c = c[0]
    assert (c["date"], c["amount"], c["certainty"], c["note"]) == ("2026-12-23", INST + 400.0, "tahmini",
                                                                    "ekstre bekleniyor")
    assert c["source"] == {"type": "card_cycle", "id": CARD}
    cyc = finance.open_cycles(TODAY)[CARD]
    assert c["amount"] == cyc["so_far"] and c["amount"] < cyc["at_cut"]


def test_bill_estimate_average_and_shift(book):
    """Son üç faturanın ortalaması (700), son vade + 1 ay (8 Ara) ve pencerede bir sonraki (8 Oca)."""
    el = _by(_up(), kind="bill", short_title="Elektrik")
    assert [(x["date"], x["amount"], x["certainty"]) for x in el] == [("2026-12-08", 700.0, "tahmini"),
                                                                       ("2027-01-08", 700.0, "tahmini")]
    assert el[0]["provider"] == "Örnekenerji Ayesaş" and el[0]["source"]["type"] == "bill_estimate"


def test_real_bill_replaces_estimate(book, client):
    """Gerçek fatura girilince o ayın tahmini kalkar (çift sayım yok); sonraki ayın tahmini kalır."""
    bid = _bill(client, "Kurum Örnekenerji Ayesaş Satış", 950.0, "2026-12-09", category="Elektrik")
    out = _up()
    el = _by(out, kind="bill", short_title="Elektrik")
    assert [(x["date"], x["amount"], x["certainty"]) for x in el] == [("2026-12-09", 950.0, "kesin"),
                                                                       ("2027-01-09", 816.67, "tahmini")]
    assert el[0]["source"] == {"type": "bill", "id": bid}


def test_batch_payment_each_record_is_a_bill(book, client):
    """Aynı gün üç kayıt (toplu ödeme): her biri ayrı fatura; ortalama 600 (toplam 1.800 tek fatura sayılmaz)."""
    _entry(client, "2026-10-04", "Örnekgaz", 1200.0, "Doğalgaz")
    for amt in (300.0, 600.0, 900.0):
        _entry(client, "2026-11-04", "Örnekgaz", amt, "Doğalgaz")
    gas = _by(_up(), short_title="Doğalgaz")
    assert [(x["date"], x["amount"]) for x in gas] == [("2026-12-04", 600.0), ("2027-01-04", 600.0)]


def test_two_lines_same_provider(book, client):
    """Aynı kurumda ayda iki fatura (ay başı ~300, ay sonu ~1.500): iki ayrı tahmin satırı."""
    for m in (9, 10, 11):
        _entry(client, f"2026-{m:02d}-03", "Örnekmobil", 300.0 + m, "Telefon")
        _entry(client, f"2026-{m:02d}-25", "Örnekmobil", 1500.0 + m, "Telefon")
    tel = _by(_up(), short_title="Telefon")
    assert [(x["date"], x["amount"]) for x in tel] == [("2026-12-03", 310.0), ("2026-12-25", 1510.0),
                                                       ("2027-01-03", 310.0)]
    assert len({x["source"]["id"] for x in tel}) == 2


def test_two_lines_by_subscriber_no(book, client):
    """Abone no farklı iki fatura aynı gün: iki satır."""
    for m in (10, 11):
        _bill(client, "Örnek Su İdaresi", 200.0, f"2026-{m:02d}-12", paid=f"2026-{m:02d}-10", category="Su",
              description="Abone 1234567")
        _bill(client, "Örnek Su İdaresi", 90.0, f"2026-{m:02d}-12", paid=f"2026-{m:02d}-10", category="Su",
              description="Abone 7654321")
    su = _by(_up(), short_title="Su", date="2026-12-12")
    assert sorted(x["amount"] for x in su) == [90.0, 200.0]


def test_bill_without_history_is_missing(book, client):
    """Tutarı girilmemiş fatura (geçmişi yok): amount null, eksik; toplama girmez, missing_count'ta."""
    _bill(client, "Kurum Örnek Su Ve Kanalizasyon İdaresi", 0.0, "2026-12-12")
    out = _up()
    su = _by(out, short_title="Su")
    assert [(x["date"], x["amount"], x["certainty"]) for x in su] == [("2026-12-12", None, "eksik")]
    assert out["missing_count"] == 1
    assert out["total"] == round(out["total_certain"] + out["total_estimate"], 2)


def test_stale_provider_no_estimate(book, client):
    """Son faturası 100 günden eski kurum tahmin üretmez."""
    _bill(client, "Örnek Eski Telekom", 500.0, "2026-06-01", paid="2026-06-01", category="Telefon")
    assert not _by(_up(), short_title="Telefon")


def test_salary_not_in_totals(book):
    """Maaş: income satırı, zamlı tutar (planned_amount); toplamlara girmez. before_salary 15 Ara'ya kadar (dahil)."""
    out = _up()
    sal = _by(out, kind="income")
    assert [(x["date"], x["amount"]) for x in sal] == [("2026-12-15", SALARY)]
    assert out["salary"] == {"date": "2026-12-15", "amount": SALARY}
    pay = [x for x in out["items"] if x["kind"] != "income"]
    assert out["total"] == round(sum(x["amount"] for x in pay if x["amount"] is not None), 2)
    pre = [x for x in pay if x["date"] <= "2026-12-15" and x["amount"] is not None]
    assert out["before_salary"]["total"] == round(sum(x["amount"] for x in pre), 2)
    assert out["before_salary"]["estimate"] == round(sum(x["amount"] for x in pre if x["certainty"] == "tahmini"), 2)
    # 18 Kas kirası (gecikti) + 8 Ara elektrik tahmini
    assert out["before_salary"]["total"] == RENT + 700.0 and out["before_salary"]["estimate"] == 700.0


def test_salary_uses_planned_amount(book, client, tmpdb):
    """Aya özel tutar (zam) varsa maaş satırı onu gösterir."""
    rid = next(r["id"] for r in tmpdb.list_recurring() if r["description"] == "Maaş")
    per = next(p for p in ("2026-12", "2027-01") if tmpdb.recurring_due(
        next(r for r in tmpdb.list_recurring() if r["id"] == rid), p) == "2026-12-15")
    with tmpdb.connect() as c:
        c.execute("INSERT INTO recurring_overrides (recurring_id, month, amount) VALUES (?, ?, ?)", (rid, per, 66000.0))
    assert _up()["salary"]["amount"] == 66000.0


def test_no_salary(client, tmpdb):
    tmpdb.set_setting("cycle_start_day", "17")
    out = _up()
    assert out["salary"] is None and out["before_salary"] is None and out["items"] == []
    assert (out["total"], out["total_certain"], out["total_estimate"], out["missing_count"]) == (0, 0, 0, 0)


def test_recurring_bill_suppresses_estimate(book, client):
    """Faturası düzenli ödeme olarak da girilmiş kurum (aynı ad): tahmin satırı üretilmez (çift sayım yok)."""
    _post(client, "/api/recurring", {"description": "Örnekenerji", "amount": 750.0, "kind": "gider",
                                     "category": "Elektrik", "day_of_month": 8, "start_month": "2026-12"})
    out = _up()
    assert not _by(out, source="bill_estimate", short_title="Elektrik")
    assert [x["amount"] for x in _by(out, kind="recurring", title="Örnekenerji")] == [750.0, 750.0]


def test_debt_with_jar_note(book, client, monkeypatch):
    """Vadeli borç kesin satır; kumbarada ayrılan kısım note'ta."""
    d = _post(client, "/api/debts", {"person": "Örnek Kişi", "direction": "borc", "amount": 5000.0,
                                     "debt_date": "2026-11-01", "due_date": "2026-12-31", "pay_terms": "tek"})
    did = d.get("id") or d["debt"]["id"]
    out = _up()
    debt = _by(out, kind="debt")
    assert [(x["date"], x["amount"], x["certainty"], x["note"]) for x in debt] == [("2026-12-31", 5000.0, "kesin", "borç")]
    from app import db

    real = db.jar_reserved
    monkeypatch.setattr(db, "jar_reserved", lambda today=None: {**real(today), "by_debt": {did: 2000.0}})
    assert _by(_up(), kind="debt")[0]["note"] == "kumbarada ayrıldı (₺2.000,00)"


def test_window_limits(book):
    """days=10: 7 Ara sonrası yok; gecikmiş kira yine var."""
    out = _up(10)
    assert out["until"] == "2026-12-07"
    assert all(x["date"] <= "2026-12-07" for x in out["items"])
    assert [x["date"] for x in _by(out, kind="recurring")] == ["2026-11-18"]


def test_api_contract(book, client, monkeypatch):
    monkeypatch.setattr(upcoming, "_today", lambda: TODAY)
    r = client.get("/api/upcoming?days=45")
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) >= {"items", "total", "total_certain", "total_estimate", "salary", "before_salary", "missing_count"}
    for x in j["items"]:
        assert set(x) == {"date", "title", "short_title", "provider", "amount", "kind", "certainty", "note", "source"}
        assert x["kind"] in ("card", "bill", "recurring", "debt", "income")
        assert x["certainty"] in ("kesin", "tahmini", "eksik")
        assert set(x["source"]) == {"type", "id"}
    assert client.get("/api/upcoming?days=0").status_code == 422
    assert client.get("/api/upcoming?days=400").status_code == 422
    caps = client.get("/api/meta").json()["capabilities"]
    assert "upcoming_v2" in caps and "monthly_net" in caps


# ---------------------------------------------------------------- Kartları bugün kapatsaydın

def test_cards_close_today_negative(book, client):
    """Harcanabilir − ufuk dışında kalan kart birikimi (so_far tabanı; Harcanabilir'den düşülmeyen). Kartlar büyükse
    eksi."""
    _entry(client, "2026-11-20", "Örnek Elektronik", 120000.0, "Diğer", payment="kredi_karti", card_last4="1234")
    nw = finance.net_worth(TODAY)
    acc = nw["cards_accumulated"]
    assert [(x["account"], x["amount"]) for x in acc["items"]] == [(CARD, INST + 120000.0)]
    assert acc["total"] == INST + 120000.0
    assert nw["cards_close_today"] == round(nw["spendable"] - acc["total"], 2)
    assert nw["cards_close_today"] < 0
    # Kartlarda biriken = Harcanabilir'den düşülmeyen kart kalemleri (spendable_later'daki kart satırları)
    later_cards = sum(x["amount"] for x in nw["spendable_later"]["items"] if x["type"] in ("card", "card_estimate"))
    assert acc["total"] == round(later_cards, 2)


def test_cards_close_today_in_summary(book, client, monkeypatch):
    s = client.get("/api/summary").json()
    nw = finance.net_worth()
    assert s["cards_close_today"] == nw["cards_close_today"]
    assert client.get("/api/summary?month=2026-01").json()["cards_close_today"] is None


# ---------------------------------------------------------------- aylık ortalama

def test_monthly_net(client, tmpdb):
    """Tamamlanmış dönemler; kart ödemesi (transfer) gider değil; ortalamalar listelenen dönemlerle."""
    tmpdb.set_setting("cycle_start_day", "17")
    for day, amt in (("2026-09-20", 1000.0), ("2026-10-20", 3000.0)):
        _post(client, "/api/entries", {"entry_date": day, "description": "Örnek Gelir", "amount": 5000.0,
                                       "kind": "gelir", "category": "Diğer Gelir", "payment": "havale"})
        _entry(client, day, "Örnek Market", amt, "Market & Gıda")
    _entry(client, "2026-11-20", "Bu dönem", 999.0, "Market & Gıda")  # içinde bulunulan dönem: sayılmaz
    out = upcoming.monthly_net(6, TODAY)
    assert [(p["period"], p["income"], p["expense"], p["net"]) for p in out["periods"]] == [
        ("2026-10", 5000.0, 1000.0, 4000.0), ("2026-11", 5000.0, 3000.0, 2000.0)]
    assert (out["avg_expense"], out["avg_net"], out["count"]) == (2000.0, 3000.0, 2)


def test_monthly_net_api(client, tmpdb, monkeypatch):
    monkeypatch.setattr(upcoming, "_today", lambda: TODAY)
    j = client.get("/api/stats/monthly?months=6").json()
    assert j["periods"] == [] and j["avg_expense"] is None
    assert client.get("/api/stats/monthly?months=0").status_code == 422


# ---------------------------------------------------------------- sonraki dönem (maaştan maaşa)

def test_next_period(book, client):
    """Maaş 15 Ara → dönem sonu 14 Oca. fixed = 15 Ara'dan sonra 14 Oca'ya kadar: kira 18 Ara, kart 23 Ara (so_far),
    elektrik tahmini 8 Oca. start_cash = Harcanabilir − maaştan önceki elektrik tahmini (8 Ara). Değişken ortalama:
    son 3 tamamlanmış dönemin gideri − kira/fatura/düzenli satırları."""
    for day in ("2026-09-10", "2026-09-20", "2026-10-20"):  # dönemler 2026-09, 2026-10, 2026-11
        _entry(client, day, "Örnek Market", 3000.0, "Market & Gıda")
        _entry(client, day, "Örnek Ev Kirası", 15000.0, "Kira")  # sabit kısım: değişkene girmez
    np_ = upcoming.upcoming(TODAY, 45)["next_period"]
    nw = finance.net_worth(TODAY)
    assert np_["end_date"] == "2027-01-14" and np_["salary"] == SALARY
    assert np_["fixed"] == RENT + INST + 700.0
    assert np_["start_cash"] == round(nw["spendable"] - 700.0, 2)
    assert (np_["variable_avg"], np_["basis_months"]) == (round((3 * 3000.0 + 2000.0 + INST) / 3, 2), 3)
    assert np_["result"] == round(np_["start_cash"] + SALARY - np_["fixed"] - np_["variable_avg"], 2)


def test_next_period_can_be_negative(book, client):
    _entry(client, "2026-11-01", "Örnek Market", 500000.0, "Market & Gıda")
    np_ = upcoming.upcoming(TODAY, 45)["next_period"]
    assert np_["result"] < 0


def test_next_period_null_without_salary(client, tmpdb):
    tmpdb.set_setting("cycle_start_day", "17")
    assert upcoming.upcoming(TODAY, 45)["next_period"] is None
