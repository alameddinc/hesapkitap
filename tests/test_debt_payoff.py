"""Faizden çıkış: Kartlar ekranında faiz satırı (debt_plan.card_interest) ve faizli kredinin erken kapatılması (ödeme
planlı borçta "bugün kapatma tutarı"; app/debt_schedule.py payoff_*, app/debt_plan.py simulate 2b). Bankalar, kişiler,
kart numaraları ve tutarlar uydurmadır."""
from datetime import date

import pytest

from tests.test_book import _member, owner  # noqa: F401  (owner: fixture)
from tests.test_debt_plan import _all_strings, _banned_in, _card  # noqa: F401
from tests.test_debt_schedule import _restructure_in

TODAY = date(2026, 11, 11)


@pytest.fixture(autouse=True)
def _no_price_download(monkeypatch):
    from app import zakat

    def fail():
        raise RuntimeError("testte fiyat indirilmez")

    monkeypatch.setattr(zakat, "_download_prices", fail)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    """list_debts (schedule.info) bugünü date.today()'den alır: testler sabit güne bağlansın."""
    import app.db as dbmod
    import app.debt_plan as dp

    class D(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(dbmod, "date", D)
    monkeypatch.setattr(dp, "date", D)


def _card_stmt(tmpdb, total=12000.0, due="2026-11-20", paid=None, card="4111***1111", pe="2026-11-05", rate=None):
    with tmpdb.connect() as c:
        sid = c.execute("INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt, "
                        "min_payment, paid_amount, paid_at) VALUES (?, 'x.pdf', 'Örnek Bank', ?, ?, ?, ?, ?, ?, ?)",
                        (f"h{pe}{card}", card, pe, due, total, total * 0.4, paid,
                         "2026-11-08" if paid else None)).lastrowid
        if rate:
            c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) VALUES "
                      "(?, ?, ?, 12.5, 'faiz_ucret', 'Banka Faiz & Ücret')", (sid, pe, f"ALIŞVERİŞ FAİZİ (Oran:{rate})"))
    return sid


def _loan(client, payoff=None, rate=None, interest="var", paid_months=5, **kw):
    """Örnek Bank kredisi: ayda 5.000 × 24 (Haziran 2026'dan), ilk 5 taksit ödenmiş → kalan 19 × 5.000 = 95.000."""
    body = {"person": "Örnek Bank", "direction": "borc", "plan_installment": 5000, "plan_count": 24,
            "plan_start": "2026-06", "plan_day": 5, "creditor_type": "banka", "plan_interest": interest, **kw}
    if payoff is not None:
        body["payoff_amount"] = payoff
        body["payoff_date"] = "2026-11-11"
    if rate is not None:
        body["plan_rate"] = rate
    r = client.post("/api/debts", json=body)
    assert r.status_code == 200, r.text
    did = r.json()["id"]
    for i in range(paid_months):
        assert client.post(f"/api/debts/{did}/pay", json={"amount": 5000, "paid_at": f"2026-{6 + i:02d}-05"}).status_code == 200
    return did


def _deniz(client, amount=10000, due="2026-10-15"):
    r = client.post("/api/debts", json={"person": "Deniz", "direction": "borc", "amount": amount,
                                        "debt_date": "2026-08-01", "due_date": due})
    return r.json()["id"]


def _debt(client, did):
    return next(d for d in client.get("/api/debts").json() if d["id"] == did)


def _rows(p):
    """Ay ay: {ay: {anahtar: tutar}} (erken kapatma satırı anahtar:kapat)."""
    return {m["month"]: {x["key"]: x["amount"] for x in m["payments"]} for m in p["months"]}


# ---------------------------------------------------------------- İŞ 1: kartta faiz satırı

def test_card_interest_states():
    from app import debt_plan as dp

    after = dp.card_interest(10000, "2026-11-05", None, 0.0375, TODAY)
    assert after["state"] == "after_due" and after["amount"] == 487.5  # 10.000 × %3,75 × 1,3
    assert after["text"] == "Kalan ₺10.000 borca bu ay yaklaşık ₺487,50 faiz ve vergi eklenir; ödemedikçe her ay büyür."
    gen = dp.card_interest(10000, "2026-11-05", None, None, TODAY)
    assert gen["rate"] == dp.GENERIC_RATE and gen["rate_source"] == "genel" and gen["amount"] == 455.0
    assert "yaklaşık" in gen["text"] and "yaklaşık" in gen["rate_note"]
    part = dp.card_interest(4000, "2026-11-20", 8000, 0.0375, TODAY)
    assert part["state"] == "partial" and part["text"] == "Kalanı son ödeme gününe kadar öde: yaklaşık ₺195 faizden kurtulursun."
    before = dp.card_interest(12000, "2026-11-20", None, 0.0375, TODAY)
    assert before["state"] == "before_due" and before["text"] == "Tamamını ödersen faiz işlemez."
    assert dp.card_interest(0, "2026-11-05", 12000, 0.0375, TODAY) is None
    hidden = dp.card_interest(10000, "2026-11-05", None, 0.0375, TODAY, fees_hidden=True)
    assert hidden["amount"] is None and "₺487" not in hidden["text"] and hidden["rate_note"] is None
    assert "Kalan ₺10.000 borca faiz ve vergi eklenir" in hidden["text"]
    # plan ile aynı hesap: simulate'teki aylık faiz de monthly_interest
    sim = dp.simulate([_card("k", 10000, "2026-11-05", rate=0.0375)], [], 0, "tarih", "2026-11")
    assert sim["months"][0]["interest"] == dp.monthly_interest(10000, 0.0375) == after["amount"]


def test_cards_summary_and_payables_interest(client, tmpdb):
    _card_stmt(tmpdb, total=10000, due="2026-11-05", rate="3.75", card="4111***1111", pe="2026-10-20")
    _card_stmt(tmpdb, total=12000, due="2026-11-20", paid=8000, card="5400***2222")
    _card_stmt(tmpdb, total=6000, due="2026-11-25", card="5400***3333", pe="2026-11-06")
    cards = {c["card_no"]: c for c in client.get("/api/cards/summary").json()["cards"]}
    a, b, c = cards["4111***1111"]["interest"], cards["5400***2222"]["interest"], cards["5400***3333"]["interest"]
    assert a["state"] == "after_due" and a["amount"] == 487.5 and a["remaining"] == 10000
    assert b["state"] == "partial" and b["remaining"] == 4000 and b["amount"] == 182.0  # genel %3,5
    assert c["state"] == "before_due"
    pay = {p["id"]: p for p in client.get("/api/payables").json() if p["type"] == "statement"}
    assert {p["interest"]["state"] for p in pay.values()} == {"after_due", "partial", "before_due"}
    assert "card_interest" in client.get("/api/meta").json()["capabilities"]


def test_card_paid_in_full_has_no_interest_line(client, tmpdb):
    _card_stmt(tmpdb, total=12000, due="2026-11-05", paid=12000)
    card = client.get("/api/cards/summary").json()["cards"][0]
    assert card["interest"] is None


def test_card_interest_hidden_in_shared_book(owner, tmpdb):  # noqa: F811
    from app import book

    owner.put("/api/book/rules", json={"hide_fees": True})
    assert book  # kural kaydı ortak defterde
    _card_stmt(tmpdb, total=10000, due="2026-11-05")
    viewer = _member(owner, "goren", "viewer")
    it = viewer.get("/api/cards/summary").json()["cards"][0]["interest"]
    assert it["state"] == "after_due" and it["amount"] is None and "₺455" not in it["text"]


# ---------------------------------------------------------------- İŞ 2: erken kapatma alanları

def test_payoff_fields_saved_and_estimated(client):
    did = _loan(client, payoff=78000)
    d = _debt(client, did)
    po = d["schedule"]["payoff"]
    assert d["payoff_amount"] == 78000 and d["payoff_date"] == "2026-11-11" and d["plan_rate"] is None
    assert po == {"amount": 78000.0, "date": "2026-11-11", "rate": None, "now": 78000.0, "left": 95000.0,
                  "interest": 17000.0, "benefit": True}
    # bir taksit daha ödenince kapatma tutarı orantılı azalır (oran yok: 78.000 × 90.000 / 95.000)
    client.post(f"/api/debts/{did}/pay", json={"amount": 5000, "paid_at": "2026-11-12"})
    assert _debt(client, did)["schedule"]["payoff"]["now"] == pytest.approx(73894.74, abs=0.01)


def test_payoff_with_rate_uses_principal_share(client):
    from app import debt_schedule as ds

    did = _loan(client, payoff=78000, rate=2.0)
    client.post(f"/api/debts/{did}/pay", json={"amount": 5000, "paid_at": "2026-11-12"})
    now = _debt(client, did)["schedule"]["payoff"]["now"]
    assert now == pytest.approx(78000 * 1.02 - 5000, abs=0.01)  # anapara payı = taksit − faiz
    assert ds.payoff_at({"p0": 78000, "s0": 95000, "inst": 5000, "rate": 0.02}, 95000) == 78000


def test_payoff_only_with_interest_and_schedule(client):
    r = client.post("/api/debts", json={"person": "Vergi Dairesi", "direction": "borc", "plan_installment": 2000,
                                        "plan_count": 6, "creditor_type": "vergi_sgk", "plan_interest": "yok",
                                        "payoff_amount": 9000})
    assert r.status_code == 422 and "faizli" in r.json()["detail"]
    r = client.post("/api/debts", json={"person": "Komşu", "direction": "borc", "amount": 900, "payoff_amount": 800})
    assert r.status_code == 422
    did = _loan(client, payoff=78000, rate=2.5)
    # faiz "yok"a çekilince erken kapatma silinir
    assert client.patch(f"/api/debts/{did}", json={"plan_interest": "yok"}).status_code == 200
    d = _debt(client, did)
    assert d["payoff_amount"] is None and d["plan_rate"] is None and d["schedule"]["payoff"] is None


def test_payoff_edit_later_and_old_client_keeps_it(client):
    did = _loan(client)
    assert _debt(client, did)["schedule"]["payoff"] is None
    # sonradan: ödeme planını düzenle (PUT schedule) ya da PATCH ile
    body = {"plan_installment": 5000, "plan_count": 24, "plan_start": "2026-06", "plan_day": 5, "creditor_type": "banka",
            "plan_interest": "var"}
    r = client.put(f"/api/debts/{did}/schedule", json={**body, "payoff_amount": 80000, "plan_rate": 2.2})
    assert r.status_code == 200 and r.json()["debt"]["payoff_amount"] == 80000 and r.json()["debt"]["plan_rate"] == 2.2
    assert r.json()["debt"]["payoff_date"] == TODAY.isoformat()
    # eski istemci (yeni alanları bilmez) planı düzenleyince girilen tutar silinmez
    assert client.put(f"/api/debts/{did}/schedule", json=body).json()["debt"]["payoff_amount"] == 80000
    assert client.patch(f"/api/debts/{did}", json={"note": "Konut dışı"}).status_code == 200
    assert _debt(client, did)["payoff_amount"] == 80000
    # silmek: null
    assert client.patch(f"/api/debts/{did}", json={"payoff_amount": None}).status_code == 200
    d = _debt(client, did)
    assert d["payoff_amount"] is None and d["payoff_date"] is None and d["plan_rate"] == 2.2
    # planı kaldırınca erken kapatma da gider
    client.put(f"/api/debts/{did}/schedule", json={**body, "payoff_amount": 80000})
    client.delete(f"/api/debts/{did}/schedule")
    assert _debt(client, did)["payoff_amount"] is None


def test_close_early_settles_with_payoff(client):
    did = _loan(client, payoff=78000)
    r = client.post(f"/api/debts/{did}/pay", json={"amount": 78000, "paid_at": "2026-11-20", "close_early": True})
    assert r.status_code == 200
    d = _debt(client, did)
    assert d["settled_at"] == "2026-11-20" and d["remaining"] == 0 and d["amount"] == 25000 + 78000
    # geri al: borç kapatma tutarı kadar açık kalır
    client.delete(f"/api/debt-payments/{r.json()['payment_id']}")
    d = _debt(client, did)
    assert d["settled_at"] is None and d["remaining"] == 78000
    nd = client.post("/api/debts", json={"person": "Komşu", "direction": "borc", "amount": 900}).json()["id"]
    assert client.post(f"/api/debts/{nd}/pay", json={"amount": 900, "paid_at": "2026-11-20",
                                                     "close_early": True}).status_code == 422


# ---------------------------------------------------------------- İŞ 2: plan sırası ve kombinasyonlar

def _scenario(client, tmpdb, payoff=78000, rate=None, card=12000, deniz=10000):
    if card:
        _card_stmt(tmpdb, total=card, due="2026-11-20", rate="3.75")
    loan = _loan(client, payoff=payoff, rate=rate)
    dz = _deniz(client, amount=deniz) if deniz else None
    return loan, dz


def test_payoff_plan_card_first_then_loan_then_due_person(client, tmpdb):
    """Kart 12.000 + kredi (kalan 95.000, kapatma 78.000) + söz tarihi geçmiş kişi borcu 10.000; ayda 30.000."""
    from app import debt_plan as dp

    loan, dz = _scenario(client, tmpdb)
    p = dp.build(monthly=30000, today=TODAY)
    rows = _rows(p)
    L, K, P, C = f"borc:{loan}", f"borc:{loan}:kapat", f"borc:{dz}", "kart:1"
    # Kasım: taksit + kartın tamamı, kalan 13.000: söz tarihi gelmiş borç varken birikim en çok yarısı
    assert rows["2026-11"] == {L: 5000.0, C: 12000.0, K: 6500.0, P: 6500.0}
    # Aralık: taksit, kalan 25.000 → Deniz'in kalan 3.500'ü için pay (yarısına kadar), kalanı birikime
    assert rows["2026-12"] == {L: 5000.0, K: 21500.0, P: 3500.0}
    pb = next(d for d in p["debts"] if d["key"] == L)["payoff"]
    assert pb["state"] == "plan"
    # kapanış ayı ve kurtulunan faiz: kalan taksitler − kapatma tutarı (orantılı yaklaşım)
    close = pb["month"]
    assert rows[close][K] > 0 and p["months"][[m["month"] for m in p["months"]].index(close)]
    assert pb["saved"] == pytest.approx(pb["installments"] - pb["amount"], abs=0.01) and pb["saved"] > 10000
    assert pb["text"].startswith("Krediyi ") and "faizden kurtulursun (kalan taksitler ₺" in pb["text"]
    assert p["totals"]["payoff_saving"] == pb["saved"]
    # kredi kapandıktan sonra taksit yok
    later = [m for m in p["months"] if m["month"] > close]
    assert all(L not in _rows({"months": [m]})[m["month"]] for m in later)
    # bu ay: birikim satırı (Ödedim yok) ve kişiye pay
    it = {i["key"]: i for i in p["this_month"]["items"]}
    assert it[K]["saving"] and it[K]["payable"] is None and it[K]["payoff"]
    assert it[K]["text"].startswith("Krediyi erken kapatmak için biriktir:")
    bad = {s: _banned_in(s) + _restructure_in(s) for s in _all_strings(p) if _banned_in(s) or _restructure_in(s)}
    assert not bad, bad


@pytest.mark.parametrize("monthly", [12000, 20000, 30000, 60000, 120000])
def test_payoff_scenarios_various_monthly(client, tmpdb, monthly):
    from app import debt_plan as dp

    loan, dz = _scenario(client, tmpdb)
    p = dp.build(monthly=monthly, today=TODAY)
    rows = _rows(p)
    L, K, P, C = f"borc:{loan}", f"borc:{loan}:kapat", f"borc:{dz}", "kart:1"
    nov = rows["2026-11"]
    assert nov[L] == 5000.0  # o ayın taksiti her zaman önce
    assert nov.get(C, 0) == min(12000.0, monthly - 5000.0)  # kart kredinin erken kapatılmasından önce
    if monthly >= 30000:
        assert nov.get(K, 0) > 0 and nov.get(P, 0) > 0  # söz tarihi gelmiş kişi tamamen aç kalmaz
    else:
        # artan para kapatma tutarını taksitler bitmeden biriktiremez: birikim yok, hepsi söz tarihi gelmiş kişiye
        assert K not in nov and nov.get(P, 0) == monthly - 5000 - nov.get(C, 0)
    # her ay: kişi borcu açıkken birikim kalan paranın yarısından fazlasını almaz (kişi o ay kapanmıyorsa)
    for m in p["months"]:
        r = _rows({"months": [m]})[m["month"]]
        pay = {x["key"]: x for x in m["payments"]}
        if K in r and P in r and pay[K].get("saving") and not pay[P]["closes"]:
            assert r[P] >= (r[K] + r[P]) / 2 - 50
    assert sum(r.get(P, 0) for r in rows.values()) == 10000  # kişi borcu kapanır
    po = next(d for d in p["debts"] if d["key"] == L)["payoff"]
    if monthly >= 20000:
        assert po["state"] == "plan" and p["debt_free"]
    bad = {s: _banned_in(s) + _restructure_in(s) for s in _all_strings(p) if _banned_in(s) or _restructure_in(s)}
    assert not bad, bad


def test_payoff_closes_this_month_when_affordable(client, tmpdb):
    """Aylık tutar kapatma tutarına yetiyorsa kredi bu ay kapanır (kişi borcu kalanla)."""
    from app import debt_plan as dp

    loan, dz = _scenario(client, tmpdb, card=0)
    p = dp.build(monthly=100000, today=TODAY)
    nov = _rows(p)["2026-11"]
    L, K = f"borc:{loan}", f"borc:{loan}:kapat"
    # Kasım taksiti ödendi; kalan 90.000 → kapatma 78.000 × 90/95 = 73.894,74
    assert nov[L] == 5000.0 and nov[K] == pytest.approx(73894.74, abs=0.01)
    assert nov[f"borc:{dz}"] == 10000.0
    po = next(d for d in p["debts"] if d["key"] == L)["payoff"]
    assert po["month"] == "2026-11" and po["saved"] == pytest.approx(16105.26, abs=0.01)
    it = next(i for i in p["this_month"]["items"] if i["key"] == K)
    assert it["close_early"]["debt_id"] == loan and it["close_early"]["amount"] == pytest.approx(73894.74, abs=0.01)
    assert it["text"].startswith("Krediyi erken kapat: ₺73.894,74 · yaklaşık ₺16.105,26 faizden kurtulursun")
    assert p["debt_free"]["month"] == "2026-11"


def test_no_payoff_amount_keeps_installments_and_hints(client, tmpdb):
    from app import debt_plan as dp

    loan, dz = _scenario(client, tmpdb, payoff=None)
    p = dp.build(monthly=30000, today=TODAY)
    L = f"borc:{loan}"
    assert all(not k.endswith(":kapat") for r in _rows(p).values() for k in r)
    assert all(r.get(L) == 5000.0 for m, r in _rows(p).items() if m <= "2028-05")  # 19 taksit sabit
    po = next(d for d in p["debts"] if d["key"] == L)["payoff"]
    assert po == {"state": "ask", "title": "Erken kapatma",
                  "text": "Bankana bugün kapatma tutarını sor; yazarsan erken kapatıp faizden kurtulma planı çıkar."}
    # faizsiz planda erken kapatma satırı yok
    tax = client.post("/api/debts", json={"person": "Vergi Dairesi", "direction": "borc", "plan_installment": 2000,
                                          "plan_count": 6, "plan_start": "2026-11", "creditor_type": "vergi_sgk",
                                          "plan_interest": "yok"}).json()["id"]
    p = dp.build(monthly=30000, today=TODAY)
    assert next(d for d in p["debts"] if d["key"] == f"borc:{tax}")["payoff"] is None


def test_payoff_no_benefit_and_not_reached(client, tmpdb):
    from app import debt_plan as dp

    loan = _loan(client, payoff=95000)  # kalan taksitlerle aynı: kazanç yok
    p = dp.build(monthly=30000, today=TODAY)
    assert next(d for d in p["debts"] if d["id"] == loan)["payoff"]["state"] == "no_benefit"
    assert all(not k.endswith(":kapat") for r in _rows(p).values() for k in r)
    client.patch(f"/api/debts/{loan}", json={"payoff_amount": 80000})
    # ayda 5.500: taksitten sonra 500 artar; 80.000 taksitler bitmeden birikmez → birikim yok, taksitler sabit
    p = dp.build(monthly=5500, today=TODAY)
    po = next(d for d in p["debts"] if d["id"] == loan)["payoff"]
    assert po["state"] == "not_reached" and all(not k.endswith(":kapat") for r in _rows(p).values() for k in r)
    # aylık tutar yokken: bugünkü tutar
    p = dp.build(monthly=None, today=TODAY)
    assert next(d for d in p["debts"] if d["id"] == loan)["payoff"]["state"] in ("known", "ask", "not_reached")


def test_card_balance_always_before_loan_payoff(client, tmpdb):
    """Tutar kartın tamamına yetmiyorsa (kartta faiz işliyor) erken kapatmaya para ayrılmaz; kart kapanınca başlar."""
    from app import debt_plan as dp

    _card_stmt(tmpdb, total=40000, due="2026-11-20", rate="4.25")
    loan = _loan(client, payoff=78000, rate=5.0)  # kredinin oranı karttan yüksek olsa da kart önce
    p = dp.build(monthly=20000, today=TODAY)
    K = f"borc:{loan}:kapat"
    first_save = next(m["month"] for m in p["months"] if K in _rows({"months": [m]})[m["month"]])
    card_close = next(d for d in p["debts"] if d["kind"] == "card")["closes"]["month"]
    assert first_save >= card_close
    for m in p["months"]:
        if m["month"] < card_close:
            assert K not in _rows({"months": [m]})[m["month"]]


def test_two_loans_higher_rate_first(client, tmpdb):
    from app import debt_plan as dp

    a = _loan(client, payoff=78000, rate=1.5)
    b = client.post("/api/debts", json={"person": "Örnek Katılım", "direction": "borc", "plan_installment": 2000,
                                        "plan_count": 12, "plan_start": "2026-11", "plan_day": 10,
                                        "creditor_type": "banka", "plan_interest": "var", "payoff_amount": 20000,
                                        "plan_rate": 3.1}).json()["id"]
    p = dp.build(monthly=30000, today=TODAY)
    first = next(k for m in p["months"] for k in _rows({"months": [m]})[m["month"]] if k.endswith(":kapat"))
    assert first == f"borc:{b}:kapat"
    pos = {d["id"]: d["payoff"] for d in p["debts"] if d["kind"] == "fixed"}
    assert pos[a]["state"] == pos[b]["state"] == "plan" and pos[b]["month"] <= pos[a]["month"]


def test_strict_debt_before_payoff(client, tmpdb):
    """Kesin tarihli borç (kurum, vade Ocak) erken kapatmadan önce: tarihine yetişir."""
    from app import debt_plan as dp

    loan = _loan(client, payoff=78000)
    tax = client.post("/api/debts", json={"person": "Vergi Dairesi", "direction": "borc", "amount": 30000,
                                          "debt_date": "2026-10-01", "due_date": "2027-01-15",
                                          "creditor_type": "vergi_sgk"}).json()["id"]
    p = dp.build(monthly=20000, today=TODAY)
    d = next(x for x in p["debts"] if x["id"] == tax)
    assert d["closes"]["month"] <= "2027-01" and not d["late"]
    assert next(x for x in p["debts"] if x["id"] == loan)["payoff"]["state"] == "plan"


def test_commit_and_tracking_with_payoff(client, tmpdb):
    from app import debt_plan as dp

    loan, dz = _scenario(client, tmpdb)
    r = client.post("/api/debt-plan/commit", json={"monthly": 30000})
    assert r.status_code == 200, r.text
    snap = dp._store()["current"]
    ln = next(x for x in snap["debts"] if x["ident"] == f"debt:{loan}")
    assert ln["payoff"] and ln["kind"] == "fixed"
    nov = next(m for m in snap["months"] if m["month"] == "2026-11")
    assert nov["pays"][f"debt:{loan}"] == 11500.0 and nov["saves"][f"debt:{loan}"] == 6500.0
    # Aralık: Kasım birikimi biriktirildi sayılır (taksit ödendiyse plana uygun)
    client.post(f"/api/debts/{loan}/pay", json={"amount": 5000, "paid_at": "2026-11-12"})
    client.post(f"/api/debts/{dz}/pay", json={"amount": 6500, "paid_at": "2026-11-12"})
    with tmpdb.connect() as c:
        c.execute("UPDATE statements SET paid_amount = 12000, paid_at = '2026-11-15'")
    p = dp.build(today=date(2026, 12, 2))
    c = p["commitment"]
    assert c["status"]["key"] in ("ok", "ahead"), c["status"]
    assert any(x["text"].startswith("Örnek Bank: erken kapatma için biriktirildi: ₺6.500") for x in c["saved"])


def test_old_client_payload_without_payoff_fields(client, tmpdb):
    """Eski istemci: yeni alanları göndermez, tanımadığı alanları yok sayar; plan eskisi gibi çalışır."""
    from app import debt_plan as dp

    r = client.post("/api/debts", json={"person": "Örnek Bank", "direction": "borc", "plan_installment": 3000,
                                        "plan_count": 12, "plan_start": "2026-11", "creditor_type": "banka",
                                        "plan_interest": "var"})
    assert r.status_code == 200
    p = client.get("/api/debt-plan", params={"monthly": 10000}).json()
    d = next(x for x in p["debts"] if x["kind"] == "fixed")
    assert d["payoff"]["state"] == "ask" and d["schedule"]["payoff"] is None
    assert all(not x["key"].endswith(":kapat") for m in p["months"] for x in m["payments"])
    assert dp.simulate([], [], 1000, "tarih", "2026-11")["payoff_by"] == {}


def test_payoff_hidden_fees(owner, tmpdb):  # noqa: F811
    owner.put("/api/book/rules", json={"hide_fees": True})
    _loan(owner, payoff=78000)
    member = _member(owner, "tam", "full")
    p = member.get("/api/debt-plan", params={"monthly": 100000}).json()
    po = next(d for d in p["debts"] if d["kind"] == "fixed")["payoff"]
    assert po["state"] == "plan" and po["saved"] is None and "₺" not in po["text"]
    assert p["totals"]["payoff_saving"] is None


def test_sources_no_banned_words_payoff():
    """Yeni metinler (sunucu ve web): faizli bir yol önermez; erken kapatma faizden çıkıştır, önerilebilir."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    files = [root / "app" / "debt_plan.py", root / "app" / "debt_schedule.py",
             root / "web" / "src" / "pages" / "DebtPlan.tsx", root / "web" / "src" / "components" / "DebtScheduleSection.tsx",
             root / "web" / "src" / "components" / "Debts.tsx", root / "web" / "src" / "components" / "Payables.tsx",
             root / "web" / "src" / "components" / "NetWorth.tsx", root / "web" / "src" / "components" / "CardLimit.tsx",
             root / "web" / "src" / "lib" / "odemePlani.ts"]
    for f in files:
        text = f.read_text(encoding="utf-8")
        text = re.sub(r'"""[\s\S]*?"""', "", text)
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        text = re.sub(r"(^|\s)(#|//)[^\n]*", "", text)
        flat = " | ".join(a or b for a, b in re.findall(r'"([^"\n]{3,})"|`([^`]{3,})`', text))
        assert not _banned_in(flat) and not _restructure_in(flat), (f.name, _banned_in(flat), _restructure_in(flat))


def test_banned_scanner_still_catches_steering_near_payoff_words():
    for s in ("Krediyi kapatmak için kredi çek", "Kapatma tutarını taksitlendir", "Borcu yapılandır, faizden kurtul",
              "Ödemeyi ertele", "Nakit avansla kapat"):
        assert _banned_in(s) or _restructure_in(s), s
    for s in ("Krediyi Mart'ta kapatırsan yaklaşık ₺9.000 faizden kurtulursun (kalan taksitler ₺45.000, kapatma tutarı "
              "₺36.000).", "Bankana bugün kapatma tutarını sor; yazarsan erken kapatıp faizden kurtulma planı çıkar.",
              "Kalan ₺10.000 borca bu ay yaklaşık ₺487,50 faiz ve vergi eklenir; ödemedikçe her ay büyür.",
              "Kalanı son ödeme gününe kadar öde: yaklaşık ₺195 faizden kurtulursun."):
        assert not _banned_in(s) and not _restructure_in(s), s
