"""Borç planı v4 (app/debt_plan.py): borcun koşulları (alacaklı türü, ödeme koşulu, tarih türü), söz tarihine göre
gruplar, tek seferde ödenecek borçta birikim, kesin tarihler, altın/döviz borcu, yetişmeyen tarih. Kişiler, kurumlar ve
tutarlar uydurmadır (SORUN senaryosunun rakamları sahadaki bir durumu canlandırır; adlar kurgu)."""
import json
import time
from datetime import date

import pytest

from tests.test_debt_plan import _all_strings, _banned_in, _card, _harm_in  # noqa: F401
from tests.test_debt_schedule import _restructure_in

TODAY = date(2026, 11, 11)


@pytest.fixture(autouse=True)
def _no_price_download(monkeypatch):
    from app import zakat

    def fail():
        raise RuntimeError("testte fiyat indirilmez")

    monkeypatch.setattr(zakat, "_download_prices", fail)


def _p(key, remaining, due=None, *, title=None, terms="parca", strict=False, i=0, debt_date="2026-01-01", **kw):
    return {"key": key, "kind": "person", "title": title or key, "remaining": remaining, "due_date": due,
            "debt_date": debt_date, "id": i, "terms": terms, "strict": strict, **kw}


def _pays(month: dict) -> dict:
    return {p["key"]: p["amount"] for p in month["payments"]}


def _post(client, **body):
    r = client.post("/api/debts", json={"direction": "borc", **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _sorun_people():
    """Bir kişiye 6 ayrı kayıt (toplam 12.000, söz 15 Kasım) + bir kuruma 95.000 (söz 31 Aralık); hiçbiri faizli değil."""
    amts = (3000, 2500, 1900, 1800, 1500, 1300)
    ppl = [_p(f"borc:{i + 1}", a, "2026-11-15", title="Deniz", i=i + 1, debt_date=f"2026-09-0{i + 1}")
           for i, a in enumerate(amts)]
    ppl.append(_p("borc:9", 95000, "2026-12-31", title="Örnek Kurum", i=9, debt_date="2026-08-01"))
    return ppl


# ---------------------------------------------------------------- SORUN: orantılı ufalama yok

@pytest.mark.parametrize("order", ["tarih", "faiz", "soz"])
def test_sorun_person_paid_whole_in_november_then_institution(order):
    from app import debt_plan as dp

    sim = dp.simulate([], _sorun_people(), 15000, order, "2026-11")
    m = sim["months"]
    groups = {g["title"]: g["amount"] for g in m[0]["groups"]}
    assert groups == {"Deniz": 12000.0, "Örnek Kurum": 3000.0}  # kişi tamamı, kalan kuruma
    assert all(sim["closes"][f"borc:{i}"] == "2026-11" for i in range(1, 7))
    assert [mo["total"] for mo in m[1:-1]] == [15000.0] * 6 and all(_pays(mo) == {"borc:9": 15000.0} for mo in m[1:-1])
    assert sim["debt_free"] == "2027-06" and _pays(m[-1]) == {"borc:9": 2000.0}  # 107.000 / 15.000: bitiş aynı
    assert sim["debt_free"] == dp.simulate([], _sorun_people(), 15000, "kucuk", "2026-11")["debt_free"]


def test_sorun_end_to_end_via_api(client):
    from app import debt_plan as dp

    for i, a in enumerate((3000, 2500, 1900, 1800, 1500, 1300)):
        _post(client, person="Deniz", amount=a, debt_date=f"2026-09-0{i + 1}", due_date="2026-11-15")
    kurum = _post(client, person="Örnek Kurum", amount=95000, debt_date="2026-08-01", due_date="2026-12-31",
                  creditor_type="diger", due_strict=False)
    p = dp.build(monthly=15000, today=TODAY)
    assert p["order"] == "tarih"
    nov = {g["title"]: g["amount"] for g in p["this_month"]["groups"]}
    assert nov == {"Deniz": 12000.0, "Örnek Kurum": 3000.0}
    assert [m["total"] for m in p["months"]] == [15000.0] * 7 + [2000.0]
    k = next(d for d in p["debts"] if d["id"] == kurum)
    assert k["strict"] is False and k["date_kind"] == "soz" and k["late"]["kind"] == "soz"
    assert k["late"]["text"] == "Söz: 31 Aralık · O tarihte açık: ₺77.000 · Tahmini ödeme: Haziran 2027"
    assert k["late"]["action"]["label"] == "Yeni tarihte anlaştık" and k["late"]["action"]["date"] == "2027-06-30"
    # "Yeni tarihte anlaştık": kullanıcı basınca söz tarihi güncellenir; plan artık yetişiyor
    assert client.patch(f"/api/debts/{kurum}", json={"due_date": k["late"]["action"]["date"]}).status_code == 200
    k2 = next(d for d in dp.build(monthly=15000, today=TODAY)["debts"] if d["id"] == kurum)
    assert k2["late"] is None and k2["promise"]["missed"] is False


# ---------------------------------------------------------------- aynı ay: Ali / Ahmet

def test_same_month_both_fit():
    from app import debt_plan as dp

    ppl = [_p("ali", 4000, "2026-12-10", title="Ali", i=1), _p("ahmet", 5000, "2026-12-20", title="Ahmet", terms="tek", i=2)]
    assert _pays(dp.simulate([], ppl, 10000, "tarih", "2026-11")["months"][0]) == {"ali": 4000.0, "ahmet": 5000.0}


def test_same_month_one_fits_tek_first_else_small():
    from app import debt_plan as dp

    ppl = [_p("ali", 4000, "2026-12-10", title="Ali", i=1), _p("ahmet", 5000, "2026-12-20", title="Ahmet", terms="tek", i=2)]
    m0 = dp.simulate([], ppl, 6000, "tarih", "2026-11")["months"][0]
    assert _pays(m0) == {"ahmet": 5000.0, "ali": 1000.0}  # tek seferde olan önce kapanır, kalan parça ödenebilene
    both = [_p("ali", 4000, "2026-12-10", title="Ali", i=1), _p("ahmet", 5000, "2026-12-20", title="Ahmet", i=2)]
    assert _pays(dp.simulate([], both, 6000, "tarih", "2026-11")["months"][0]) == {"ali": 4000.0, "ahmet": 2000.0}
    # tarihleri ve koşulları eşitse altın/döviz borcu TL'den önce
    gold = [_p("ali", 4000, "2026-12-10", title="Ali", i=1),
            _p("ahmet", 5000, "2026-12-20", title="Ahmet", i=2, asset="GRAM_ALTIN", price=2500.0)]
    assert _pays(dp.simulate([], gold, 6000, "tarih", "2026-11")["months"][0]) == {"ahmet": 5000.0, "ali": 1000.0}


def test_same_month_none_fits_shares_and_saving():
    from app import debt_plan as dp

    ppl = [_p("ali", 8000, "2026-12-10", title="Ali", i=1), _p("ahmet", 6000, "2026-12-20", title="Ahmet", terms="tek", i=2)]
    sim = dp.simulate([], ppl, 3500, "tarih", "2026-11")
    m0 = sim["months"][0]
    rows = {p["key"]: p for p in m0["payments"]}
    assert rows["ali"]["amount"] == 2000.0 and not rows["ali"].get("saving")  # anlamlı pay (₺50 katı)
    assert rows["ahmet"] == {**rows["ahmet"], "amount": 1500.0, "saving": True, "saved": 1500.0, "closes": False}
    assert m0["left"] == 12000.0  # birikim borcu düşürmez: Ahmet hâlâ 6.000
    assert [g.get("saving", False) for g in m0["groups"]] == [False, True]
    # Ahmet tek seferde, birikimin yettiği ay: ödeme satırında birikimden gelen
    pay = [(mo["month"], p) for mo in sim["months"] for p in mo["payments"] if p["key"] == "ahmet" and not p.get("saving")]
    assert len(pay) == 1 and pay[0][1]["closes"] and pay[0][1]["amount"] + pay[0][1]["from_saved"] == 6000.0
    assert sim["closes"]["ahmet"] == pay[0][0]
    for mo in sim["months"]:
        for p in mo["payments"]:
            assert p["closes"] or p["amount"] % dp.ROUND_TO == 0, p  # ufalama yok


def test_tek_debt_saving_shown_in_build(client):
    from app import debt_plan as dp

    did = _post(client, person="Burak", amount=12000, debt_date="2026-07-20", due_date="2027-02-15", pay_terms="tek",
                creditor_type="kisi")
    p = dp.build(monthly=4000, today=TODAY)
    d = next(x for x in p["debts"] if x["id"] == did)
    assert d["terms"] == "tek" and d["terms_label"] == "Tek seferde ödenecek" and d["strict"] is False
    assert d["saving"]["text"] == "Burak için biriktir: Ocak 2027'de ₺12.000 birikmiş olur, tek seferde öde."
    it = next(i for i in p["this_month"]["items"] if i["key"] == f"borc:{did}")
    assert it["saving"] and it["amount"] == 4000 and not it["full"] and it["text"] == d["saving"]["text"]
    assert d["remaining"] == 12000 and p["months"][1]["left"] == 12000  # birikim borcu düşürmez
    assert p["totals"]["saving_this_month"] == 4000
    jan = p["months"][2]["payments"][0]
    assert jan["closes"] and jan["from_saved"] == 8000 and jan["amount"] == 4000


# ---------------------------------------------------------------- kesin tarihler

def test_strict_installment_card_and_short_budget():
    """Tutar yetmiyor: önce taksit ve kart asgarisi (tarih sırasıyla), vadesi gelen kesin borç; kalan karta; esnek
    borç bekler."""
    from app import debt_plan as dp

    fixed = [{"key": "borc:7", "kind": "fixed", "id": 7, "title": "Örnek Mobilya", "remaining": 6000.0, "day": 5,
              "dues": {"2026-11": 1500.0, "2026-12": 1500.0, "2027-01": 1500.0, "2027-02": 1500.0}}]
    cards = [_card("kart:a", 10000, "2026-11-20", min_payment=3000)]
    strict = _p("borc:8", 2000, "2026-11-25", title="Örnek Vergi", terms="tek", strict=True, i=8)
    flex = _p("borc:9", 5000, "2026-11-15", title="Komşu", i=9)
    sim = dp.simulate(cards, [strict, flex], 7000, "tarih", "2026-11", fixed=fixed)
    m0 = sim["months"][0]
    assert list(_pays(m0)) == ["borc:7", "kart:a", "borc:8"]  # tarih sırası: 5'i, 20'si, 25'i
    assert _pays(m0) == {"borc:7": 1500.0, "kart:a": 3500.0, "borc:8": 2000.0}  # asgari 3.000 + kalan 500 karta
    assert "borc:9" not in _pays(m0) and sim["closes"]["borc:8"] == "2026-11"
    # asgarilere bile yetmiyor: tarih sırası
    low = dp.simulate(cards, [strict, flex], 3000, "tarih", "2026-11", fixed=fixed)["months"][0]
    assert _pays(low) == {"borc:7": 1500.0, "kart:a": 1500.0}
    assert low["fixed_short"] == 0


def test_strict_future_reserves_only_what_is_needed():
    from app import debt_plan as dp

    strict = _p("borc:1", 30000, "2027-01-31", title="Örnek Banka", strict=True, i=1)
    flex = _p("borc:2", 10000, "2026-11-30", title="Komşu", i=2)
    sim = dp.simulate([], [strict, flex], 12000, "tarih", "2026-11")
    assert _pays(sim["months"][0]) == {"borc:1": 6000.0, "borc:2": 6000.0}  # yetişmek için gereken 6.000, kalan söze
    assert _pays(sim["months"][1]) == {"borc:1": 12000.0}
    assert _pays(sim["months"][2]) == {"borc:1": 12000.0}
    assert sim["closes"]["borc:1"] == "2027-01" and sim["closes"]["borc:2"] == "2027-02"
    # tutar yetiyorsa kesin borç esnek borcun önüne geçmez
    roomy = dp.simulate([], [strict, flex], 25000, "tarih", "2026-11")
    assert _pays(roomy["months"][0]) == {"borc:1": 15000.0, "borc:2": 10000.0}


def test_strict_late_needs_monthly_amount(client):
    from app import debt_plan as dp

    for i, a in enumerate((3000, 2500, 1900, 1800, 1500, 1300)):
        _post(client, person="Deniz", amount=a, debt_date=f"2026-09-0{i + 1}", due_date="2026-11-15")
    kurum = _post(client, person="Örnek Kurum", amount=95000, debt_date="2026-08-01", due_date="2026-12-31",
                  creditor_type="vergi_sgk")
    p = dp.build(monthly=15000, today=TODAY)
    k = next(d for d in p["debts"] if d["id"] == kurum)
    assert k["strict"] is True and k["date_kind"] == "kesin" and k["late"]["kind"] == "kesin"
    assert k["late"]["need_monthly"] == 47500 and k["late"]["warning"] == "Bu tarih kesin; yetişmek için ayda ₺47.500 gerekir."
    assert "action" not in k["late"]  # kesinde tarih değiştirme önerilmez
    assert k["late"]["text"].startswith("Vade: 31 Aralık · O tarihte açık: ")
    w = next(w for w in p["warnings"] if w["key"] == f"kesin:borc:{kurum}")
    assert w["level"] == "bad" and "₺47.500" in w["text"]
    # kesin borç önce: kasımda kişiye para kalmaz (gerekirse esnek borcun payı kayar)
    assert {g["title"] for g in p["this_month"]["groups"]} == {"Örnek Kurum"}
    ok = dp.build(monthly=47500, today=TODAY)
    assert next(d for d in ok["debts"] if d["id"] == kurum)["late"] is None


def test_day_note_when_due_day_before_salary(client):
    from app import debt_plan as dp

    client.post("/api/recurring", json={"description": "Maaş", "amount": 60000, "kind": "gelir", "category": "Maaş",
                                        "day_of_month": 15, "start_month": "2026-01"})
    did = _post(client, person="Örnek Mobilya", plan_installment=1500, plan_count=6, plan_start="2026-11", plan_day=5,
                creditor_type="magaza")
    late = _post(client, person="Örnek Banka", amount=4000, debt_date="2026-10-01", due_date="2026-12-20",
                 creditor_type="banka")
    p = dp.build(monthly=20000, today=TODAY)
    d = next(x for x in p["debts"] if x["id"] == did)
    assert d["day_note"]["text"] == "Örnek Mobilya 5'inde, maaş 15'inde: bu ödemeyi önceki aydan ayır."
    assert next(x for x in p["debts"] if x["id"] == late)["day_note"] is None  # 20'si maaştan sonra
    assert [n["key"] for n in p["day_notes"]] == [f"borc:{did}"]
    assert d["creditor_type"] == "magaza" and d["subtitle"].startswith("Mağaza / senet · ")


# ---------------------------------------------------------------- altın / döviz

def _cache_prices(gram: float):
    from app import system

    system.put("market_prices", json.dumps({"prices": {"GRA": gram, "USD": 40.0}, "updated": "test",
                                            "fetched": time.time(), "source": "test"}))


def test_gold_debt_in_plan_with_price(client):
    from app import debt_plan as dp

    _cache_prices(3000.0)
    did = _post(client, person="Kuzen", amount=2, asset="GRAM_ALTIN", debt_date="2026-08-01", due_date="2026-12-10")
    _post(client, person="Komşu", amount=5000, debt_date="2026-08-01", due_date="2027-01-25")
    p = dp.build(monthly=4000, today=TODAY)
    assert p["excluded"] == [] and p["price_note"] == "TL karşılığı fiyata göre değişir."
    d = next(x for x in p["debts"] if x["id"] == did)
    assert d["remaining"] == 6000.0 and d["qty_text"] == "2 gram altın" and d["tl_text"] == "2 gram altın · ≈₺6.000"
    assert d["approx_note"] == "TL karşılığı fiyata göre değişir."
    it = next(i for i in p["this_month"]["items"] if i["key"] == f"borc:{did}")
    assert it["amount"] == 4000 and it["units_text"] == "1,33 gram altın" and "≈₺4.000" in it["text"]
    assert p["totals"]["people"] == 11000.0


def test_gold_debt_without_price_stays_excluded(client):
    from app import debt_plan as dp

    _post(client, person="Kuzen", amount=2, asset="GRAM_ALTIN", debt_date="2026-08-01")
    p = dp.build(monthly=4000, today=TODAY)
    assert len(p["excluded"]) == 1 and "TL dışı" in p["excluded"][0]["text"] and p["totals"]["count"] == 0


# ---------------------------------------------------------------- geri uyum

def test_legacy_order_setting_maps_to_tarih(client, tmpdb):
    from app import debt_plan as dp

    tmpdb.set_setting(dp.SETTING, json.dumps({"monthly": 5000, "order": "faiz"}))
    assert dp.settings() == {"monthly": 5000.0, "order": "tarih"}
    assert client.put("/api/debt-plan", json={"order": "soz"}).json()["order"] == "tarih"
    assert client.put("/api/debt-plan", json={"order": "kucuk"}).json()["order"] == "kucuk"
    r = client.get("/api/debt-plan", params={"order": "faiz"}).json()
    assert r["order"] == "tarih" and [o["key"] for o in r["orders"]] == ["tarih", "kucuk"]
    assert r["orders"][0]["label"] == "Söz tarihine göre"
    assert client.get("/api/debt-plan", params={"order": "x"}).status_code == 422


def test_old_client_without_new_fields_works(client):
    from app import debt_plan as dp

    a = _post(client, person="Örnek Kişi", amount=5000, debt_date="2026-06-01", due_date="2026-12-15")
    b = _post(client, person="Vergi Dairesi", plan_installment=2000, plan_count=3, plan_start="2026-11", plan_day=20)
    ds = {d["id"]: d for d in client.get("/api/debts").json()}
    assert ds[a]["terms"] == "bilinmiyor" and ds[a]["strict"] is False and ds[a]["creditor_type"] is None
    assert ds[a]["pay_terms"] is None and ds[a]["due_strict"] is None
    assert ds[b]["terms"] == "taksitli" and ds[b]["strict"] is True and ds[b]["creditor_type"] == "diger"
    p = dp.build(monthly=4000, today=TODAY)
    assert [d["kind"] for d in p["debts"]] == ["fixed", "person"] and p["debts"][1]["subtitle"] == "Diğer borç"
    assert {i["key"]: i["amount"] for i in p["this_month"]["items"]} == {f"borc:{b}": 2000.0, f"borc:{a}": 2000.0}


def test_debt_terms_api(client):
    did = _post(client, person="Örnek Mağaza", amount=7000, debt_date="2026-10-01", due_date="2026-12-01",
                creditor_type="magaza", pay_terms="tek")
    d = next(x for x in client.get("/api/debts").json() if x["id"] == did)
    assert d["creditor_label"] == "Mağaza / senet" and d["terms"] == "tek" and d["strict"] is True  # kurum: kesin
    assert client.patch(f"/api/debts/{did}", json={"due_strict": False, "pay_terms": "parca"}).status_code == 200
    d = next(x for x in client.get("/api/debts").json() if x["id"] == did)
    assert d["strict"] is False and d["due_strict"] is False and d["terms"] == "parca"
    assert client.patch(f"/api/debts/{did}", json={"due_strict": None}).status_code == 200
    assert next(x for x in client.get("/api/debts").json() if x["id"] == did)["strict"] is True  # varsayılana döndü
    bad = client.patch(f"/api/debts/{did}", json={"pay_terms": "taksitli"})
    assert bad.status_code == 422 and "ödeme planını" in bad.json()["detail"]
    bad = client.post("/api/debts", json={"person": "X", "direction": "borc", "amount": 100, "pay_terms": "taksitli"})
    assert bad.status_code == 422
    al = _post(client, person="Ali", amount=100, debt_date="2026-10-01", pay_terms="tek", due_strict=True)
    client.patch(f"/api/debts/{al}", json={"direction": "alacak"})
    x = next(x for x in client.get("/api/debts").json() if x["id"] == al)
    # alacakta da koşullar (yetenek debt_terms_receivable): kayıtlı koşul kalır, alacak etiketiyle
    assert x["terms"] == "tek" and x["terms_label"] == "Tek seferde" and x["strict"] is True
    # taksitli: plan alanlarıyla; PUT schedule / plan_off ödeme koşulunu yönetir, alacaklı türü kalır
    sid = _post(client, person="Örnek Mağaza", amount=6000, debt_date="2026-10-01", creditor_type="magaza")
    assert client.put(f"/api/debts/{sid}/schedule", json={"plan_installment": 1000, "plan_count": 6}).status_code == 200
    s = next(x for x in client.get("/api/debts").json() if x["id"] == sid)
    assert s["pay_terms"] == "taksitli" and s["terms"] == "taksitli" and s["creditor_type"] == "magaza"
    assert client.delete(f"/api/debts/{sid}/schedule").status_code == 200
    s = next(x for x in client.get("/api/debts").json() if x["id"] == sid)
    assert s["pay_terms"] is None and s["terms"] == "bilinmiyor" and s["creditor_type"] == "magaza"


# ---------------------------------------------------------------- planı başlat: birikim hedefe sayılır

def test_commit_counts_savings_until_paid(client):
    from app import debt_plan as dp

    did = _post(client, person="Burak", amount=12000, debt_date="2026-07-20", due_date="2027-02-15", pay_terms="tek")
    dp.commit_plan(monthly=4000, today=TODAY)
    dec = date(2026, 12, 10)
    c = dp.build(today=dec)["commitment"]
    assert c["status"]["key"] == "ok" and c["changed"] is None  # kasımın 4.000 birikimi biriktirildi sayılır
    assert c["saved"] == [{"ident": f"debt:{did}", "title": "Burak", "amount": 4000.0,
                           "text": "Burak için biriktirildi: ₺4.000"}]
    client.post(f"/api/debts/{did}/pay", json={"amount": 12000, "paid_at": "2026-12-08"})
    c = dp.build(today=dec)["commitment"]
    assert c["saved"] == [] and c["status"]["key"] == "done"


# ---------------------------------------------------------------- bildirim ve yasak ifadeler

def test_push_strict_due_date(client):
    from app import push

    _post(client, person="Örnek Mağaza", amount=3000, debt_date="2026-10-01", due_date="2026-11-13",
          creditor_type="magaza")
    two = push._borc(date(2026, 11, 11))
    assert len(two) == 1 and two[0]["title"] == "Örnek Mağaza: ödeme günü 2 gün sonra"


def test_no_interest_steering_words_in_v4_outputs(client):
    from app import debt_plan as dp

    _cache_prices(3000.0)
    client.post("/api/recurring", json={"description": "Maaş", "amount": 60000, "kind": "gelir", "category": "Maaş",
                                        "day_of_month": 15, "start_month": "2026-01"})
    for i, a in enumerate((3000, 2500, 1900)):
        _post(client, person="Deniz", amount=a, debt_date=f"2026-09-0{i + 1}", due_date="2026-11-15")
    _post(client, person="Örnek Kurum", amount=95000, debt_date="2026-08-01", due_date="2026-12-31",
          creditor_type="vergi_sgk")
    _post(client, person="Burak", amount=12000, debt_date="2026-07-20", due_date="2027-02-15", pay_terms="tek")
    _post(client, person="Ali", amount=6500, debt_date="2026-09-10", due_date="2026-11-01", pay_terms="tek")
    _post(client, person="Kuzen", amount=2, asset="GRAM_ALTIN", debt_date="2026-08-01")
    _post(client, person="Örnek Mobilya", plan_installment=1500, plan_count=6, plan_start="2026-11", plan_day=5,
          creditor_type="magaza")
    for monthly in (0, 500, 4000, 15000, 60000, 200000):
        for order in ("tarih", "kucuk"):
            p = dp.build(monthly=monthly, order=order, today=TODAY)
            bad = {s: _banned_in(s) + _restructure_in(s) for s in _all_strings(p) if _banned_in(s) or _restructure_in(s)}
            assert not bad, bad
    p = dp.commit_plan(monthly=15000, today=TODAY)
    assert not [s for s in _all_strings(dp.build(today=date(2026, 12, 10))) if _banned_in(s) or _restructure_in(s)]
    assert p["commitment"]


def test_agreed_date_keeps_debt_on_time_after_reordering(client):
    """"Yeni tarihte anlaştık": söz tarihi ileri alınınca borç sırada başka sözlerin arkasına düşebilir; önerilen tarih
    yeni sırayla hesaplanır, kaydedildikten sonra borç yetişir görünür."""
    from app import debt_plan as dp

    kurum = _post(client, person="Örnek Kurum", amount=60000, debt_date="2026-08-01", due_date="2026-12-31")
    _post(client, person="Komşu", amount=30000, debt_date="2026-08-01", due_date="2027-03-15")
    _post(client, person="Kuzen", amount=30000, debt_date="2026-08-01", due_date="2027-04-15")
    p = dp.build(monthly=10000, today=TODAY)
    late = next(d for d in p["debts"] if d["id"] == kurum)["late"]
    new = late["action"]["date"]
    assert client.patch(f"/api/debts/{kurum}", json={"due_date": new}).status_code == 200
    again = next(d for d in dp.build(monthly=10000, today=TODAY)["debts"] if d["id"] == kurum)
    assert again["late"] is None, (new, again["late"])

