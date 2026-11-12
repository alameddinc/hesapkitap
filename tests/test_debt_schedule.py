"""Borç planı v3: kişi kişi gruplama, anlamlı (yuvarlı) tutarlar ve ödeme planı olan borç (app/debt_schedule.py; yalnız
kayıt). Kişiler, kurumlar ve tutarlar uydurmadır."""
import re
from datetime import date

import pytest

from tests.test_debt_plan import _all_strings, _banned_in, _card, _low, _person

TODAY = date(2026, 11, 10)
# Yeniden yapılandırma, erteleme, borç transferi, kredi vb. ÖNERİSİ yok: ödeme planı olan borç yalnız kaydedilir
RESTRUCTURE = [r"yapılandır", r"taksitlendir", r"ertele", r"refinans", r"borç\s*transfer", r"kredi\s*(çek|al|kullan)",
               r"öteleme", r"vade(yi|sini)?\s*uzat", r"borcunu\s*böl"]


def _restructure_in(text: str) -> list[str]:
    return [p for p in RESTRUCTURE if re.search(p, _low(text))]


def _sched(client, person="Vergi Dairesi", inst=2000, count=6, start="2026-11", day=15, ctype="vergi_sgk", **kw):
    body = {"person": person, "direction": "borc", "plan_installment": inst, "plan_count": count, "plan_start": start,
            "plan_day": day, "creditor_type": ctype, **kw}
    r = client.post("/api/debts", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _debt(client, did):
    return next(d for d in client.get("/api/debts").json() if d["id"] == did)


# ---------------------------------------------------------------- gruplama ve tutarlar

def test_same_person_debts_grouped_in_this_month_and_months(client):
    from app import debt_plan as dp

    for amt, day in ((1200, "2026-03-01"), (900, "2026-05-01"), (700, "2026-07-01")):
        client.post("/api/debts", json={"person": "Nermin Teyze", "direction": "borc", "amount": amt, "debt_date": day})
    client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 30000, "debt_date": "2026-04-01"})
    p = dp.build(monthly=15000, order="kucuk", today=TODAY)
    groups = [g for g in p["this_month"]["groups"] if not g["done"]]
    nermin = [g for g in groups if g["title"] == "Nermin Teyze"]
    assert len(nermin) == 1  # tek satır, üç ayrı satır değil
    g = nermin[0]
    assert g["count"] == 3 and len(g["items"]) == 3 and g["payable"] is None
    assert g["amount"] == 2800.0 and g["full"] and g["text"] == "3 borç · hepsi bu ay kapanır"
    assert sum(i["amount"] for i in g["items"]) == g["amount"]
    # söz tarihine göre (eski "faizi önle" de): tarihsizler en eskiden, borç borç; orantılı ufalama yok
    f = dp.build(monthly=15000, order="faiz", today=TODAY)
    fg = [g for g in f["this_month"]["groups"] if g["title"] == "Nermin Teyze"]
    assert len(fg) == 1 and fg[0]["amount"] == 1200.0 and fg[0]["count"] == 1 and fg[0]["full"]
    assert next(g for g in f["this_month"]["groups"] if g["title"] == "Örnek Kişi")["amount"] == 13800.0
    # eski istemciler için borç borç satırlar kalır
    assert len([i for i in p["this_month"]["items"] if i["title"] == "Nermin Teyze"]) == 3
    m0 = p["months"][0]
    assert [x["title"] for x in m0["groups"]].count("Nermin Teyze") == 1
    assert next(x for x in m0["groups"] if x["title"] == "Nermin Teyze")["count"] == 3
    one = next(x for x in groups if x["title"] == "Örnek Kişi")
    assert one["count"] == 1 and one["payable"] and one["payable"]["type"] == "debt"


def test_person_payments_are_round_and_never_tiny():
    from app import debt_plan as dp

    people = [_person("borc:1", 312.45, i=1), _person("borc:2", 4999.9, i=2), _person("borc:3", 12345.67, i=3),
              _person("borc:4", 3333.33, "2026-12-20", i=4)]
    for order in dp.ORDERS:
        for monthly in (1000, 2600, 3777.77, 15000):
            sim = dp.simulate([], people, monthly, order, "2026-11")
            rem = {d["key"]: d["remaining"] for d in people}
            for mo in sim["months"]:
                for x in mo["payments"]:
                    before = rem[x["key"]]
                    rem[x["key"]] = round(before - x["amount"], 2)
                    if x["closes"]:
                        continue  # kalanın tamamı (kuruşuyla)
                    assert x["amount"] % dp.ROUND_TO == 0, (order, monthly, x)  # ₺50 katı
                    assert x["amount"] >= dp.MIN_PAY or len(mo["payments"]) == 1, (order, monthly, x)
                    assert rem[x["key"]] >= dp.FOLD, (order, monthly, x)  # küçük kalan bırakılmaz
                assert mo["total"] <= monthly + dp.FOLD
            assert sim["debt_free"], (order, monthly)


def test_tiny_last_remainder_folded_into_previous_payment():
    from app import debt_plan as dp

    sim = dp.simulate([], [_person("borc:1", 5012, i=1)], 2500, "kucuk", "2026-11")
    assert [m["total"] for m in sim["months"]] == [2500.0, 2512.0]  # ₺12 tek başına üçüncü aya kalmaz
    # kalan ₺100 ile ₺250 arası: bu ay biraz az ödenir, son ödeme en az ₺250
    sim = dp.simulate([], [_person("borc:1", 5180, i=1)], 2500, "kucuk", "2026-11")
    assert [m["total"] for m in sim["months"]] == [2500.0, 2400.0, 280.0]


def test_faiz_order_switches_to_whole_small_debts_when_shares_too_small():
    from app import debt_plan as dp

    people = [_person("borc:a", 600, i=1), _person("borc:b", 800, i=2), _person("borc:c", 40000, i=3)]
    pays = {p["key"]: p["amount"] for p in dp.simulate([], people, 3000, "faiz", "2026-11")["months"][0]["payments"]}
    # orantılı olsa a ₺42, b ₺57 alırdı: ikisi bütün olarak kapanır, kalan büyüğe
    assert pays == {"borc:a": 600.0, "borc:b": 800.0, "borc:c": 1600.0}
    # tutar hiçbirini kapatmaya yetmiyorsa hepsi en küçüğe (bölünmez)
    small = {p["key"]: p["amount"] for p in dp.simulate([], people, 500, "faiz", "2026-11")["months"][0]["payments"]}
    assert small == {"borc:a": 350.0}  # 600'den 500 ödenseydi ₺100 kalırdı: ₺250 bırakılır


def test_rules_info_explains_rounding():
    from app import debt_plan as dp

    t = dp.RULES_INFO
    assert "₺50" in t and "₺250" in t and "küçük borçlar" in t and "taksit" in t and "kesin" in t and "birikim" in t
    assert not _banned_in(t) and not _restructure_in(t)


# ---------------------------------------------------------------- ödeme planı olan borç: kayıt ve takvim

def test_schedule_installments_total_end_month_and_day():
    from app import debt_schedule as ds

    d = {"plan_installment": 2000, "plan_count": 6, "plan_start": "2026-11", "plan_day": 31, "amount": 11500,
         "paid_amount": 0}
    rows = ds.installments(d)
    assert [r["amount"] for r in rows] == [2000.0] * 5 + [1500.0]  # son taksit farkı alır
    assert [r["date"] for r in rows[:4]] == ["2026-11-30", "2026-12-31", "2027-01-31", "2027-02-28"]  # kısa ay: son gün
    assert ds.count_from(2000, end_month="2027-04", start_month="2026-11") == 6
    assert ds.count_from(2000, total=11500) == 6
    assert ds.validate(2000, 6, 9000) and ds.validate(2000, 6, 30000) and not ds.validate(2000, 6, 11500)
    info = ds.info({**d, "paid_amount": 2500, "settled_at": None}, TODAY)
    assert info["paid_count"] == 1 and info["next"]["no"] == 2 and info["next"]["left"] == 1500.0
    assert info["due_now"] == 0.0 and not info["overdue"]  # kasım taksiti ödenmiş, aralığın 500'ü peşin
    assert info["end_month"] == "2027-04" and "son taksit Nisan 2027" in info["text"] and "31'i" in info["text"]


def test_create_scheduled_debt_via_api_and_validation(client):
    did = _sched(client, plan_interest="var", note="2026 yılı")
    d = _debt(client, did)
    assert d["amount"] == 12000.0 and d["due_date"] is None and d["debt_date"] == "2026-11-01"
    s = d["schedule"]
    assert s["installment"] == 2000.0 and s["count"] == 6 and s["creditor_label"] == "Vergi / SGK"
    assert s["next"]["date"] == "2026-11-15" and s["due_now"] == 2000.0 and s["interest"] == "var"
    # son taksit ayıyla (sayı yerine) ve toplamla
    did2 = _sched(client, person="Örnek Bank", inst=1500, count=None, plan_end="2027-04", ctype="banka", amount=8700)
    s2 = _debt(client, did2)["schedule"]
    assert s2["count"] == 6 and s2["end_month"] == "2027-04"
    bad = client.post("/api/debts", json={"person": "X", "direction": "borc", "plan_installment": 2000, "plan_count": 6,
                                          "amount": 5000})
    assert bad.status_code == 422 and "Toplam" in bad.json()["detail"]
    # alacakta da ödeme planı (yetenek debt_terms_receivable); TL dışında yok
    fx = client.post("/api/debts", json={"person": "X", "direction": "alacak", "plan_installment": 1, "plan_count": 2,
                                         "asset": "GRAM_ALTIN"})
    assert fx.status_code == 422 and "TL alacakta" in fx.json()["detail"]
    fx = client.post("/api/debts", json={"person": "X", "direction": "borc", "plan_installment": 1, "plan_count": 2,
                                         "asset": "USD"})
    assert fx.status_code == 422 and "TL borçta" in fx.json()["detail"]
    nocount = client.post("/api/debts", json={"person": "X", "direction": "borc", "plan_installment": 100})
    assert nocount.status_code == 422 and "Taksit sayısını" in nocount.json()["detail"]
    # elden borç eskisi gibi (tutar zorunlu)
    assert client.post("/api/debts", json={"person": "X", "direction": "borc", "debt_date": "2026-11-01"}).status_code == 422
    # planı kaldır: elden borç olarak kalır
    assert client.patch(f"/api/debts/{did2}", json={"plan_off": True}).status_code == 200
    assert _debt(client, did2)["schedule"] is None and _debt(client, did2)["amount"] == 8700.0
    # düzenle: taksit değişir, toplam yeniden hesaplanır
    assert client.patch(f"/api/debts/{did}", json={"plan_installment": 2500, "plan_count": 4, "plan_start": "2026-11",
                                                   "plan_day": 10}).status_code == 200
    d = _debt(client, did)
    assert d["amount"] == 10000.0 and d["schedule"]["day"] == 10


def test_pay_without_amount_pays_installment_not_whole_debt(client):
    did = _sched(client)
    r = client.post(f"/api/debts/{did}/pay", json={"amount": None, "paid_at": "2026-11-10"})
    assert r.status_code == 200
    d = _debt(client, did)
    assert d["paid_amount"] == 2000.0 and d["remaining"] == 10000.0 and not d["settled_at"]
    assert d["schedule"]["next"]["no"] == 2 and d["schedule"]["next"]["date"] == "2026-12-15"
    assert d["schedule"]["due_now"] == 0.0
    # açık tutarla bütün borç da ödenebilir
    client.post(f"/api/debts/{did}/pay", json={"amount": 10000, "paid_at": "2026-11-10"})
    assert _debt(client, did)["settled_at"]


def test_payables_show_next_installment(client):
    from app import finance

    did = _sched(client)
    ps = [p for p in finance.payables(TODAY) if p["type"] == "debt" and p["id"] == did]
    assert len(ps) == 1 and ps[0]["due_date"] == "2026-11-15" and ps[0]["amount"] == 2000.0
    assert ps[0]["title"] == "Vergi Dairesi (1. taksit)" and "Ödeme planı" in ps[0]["subtitle"]
    client.post(f"/api/debts/{did}/pay", json={"amount": 2000, "paid_at": "2026-11-10"})
    p2 = next(p for p in finance.payables(TODAY) if p["type"] == "debt" and p["id"] == did)
    assert p2["due_date"] == "2026-12-15" and p2["amount"] - p2["paid_amount"] == 2000.0


# ---------------------------------------------------------------- borç planında sabit yükümlülük

def test_fixed_installments_reserved_first_each_month():
    from app import debt_plan as dp

    fixed = [{"key": "borc:9", "kind": "fixed", "id": 9, "title": "Vergi Dairesi", "remaining": 6000.0, "day": 15,
              "dues": {"2026-11": 2000.0, "2026-12": 2000.0, "2027-01": 2000.0}}]
    cards = [_card("kart:a", 5000, "2026-11-20")]
    people = [_person("borc:1", 3000, i=1)]
    sim = dp.simulate(cards, people, 8000, "faiz", "2026-11", fixed=fixed)
    m0 = sim["months"][0]
    assert [p["key"] for p in m0["payments"]][:2] == ["borc:9", "kart:a"]  # önce taksit, sonra kart
    assert {p["key"]: p["amount"] for p in m0["payments"]} == {"borc:9": 2000.0, "kart:a": 5000.0, "borc:1": 1000.0}
    assert m0["fixed_due"] == 2000.0 and m0["fixed_short"] == 0
    # taksit öne çekilmez: kalan para elden borca gider, taksit yalnız kendi ayında
    m1 = sim["months"][1]
    assert {p["key"]: p["amount"] for p in m1["payments"]} == {"borc:9": 2000.0, "borc:1": 2000.0}
    assert sim["closes"]["borc:9"] == "2027-01" and sim["debt_free"] == "2027-01" and sim["interest"] == 0
    # aylık tutar taksite yetmiyor: eksik kalan sonraki aya devreder, kartlara bu ay para kalmaz
    low = dp.simulate(cards, [], 1500, "faiz", "2026-11", fixed=fixed)
    assert low["months"][0]["fixed_short"] == 500.0 and low["months"][0]["total"] == 1500.0
    assert low["months"][1]["payments"][0] == {"key": "borc:9", "kind": "fixed", "title": "Vergi Dairesi",
                                               "amount": 1500.0, "closes": False}
    assert low["months"][1]["fixed_due"] == 2500.0
    # faizsiz tutar taksitleri de sayar: kasımda 2.000 taksit + 5.000 kart
    assert dp.min_monthly(cards, "2026-11", fixed=fixed) == 7000


def test_build_with_fixed_debt(client):
    from app import debt_plan as dp

    did = _sched(client, inst=2000, count=6, day=15)
    _sched(client, person="Nermin Teyze", inst=1000, count=3, day=5, ctype="kisi")
    client.post("/api/debts", json={"person": "Komşu", "direction": "borc", "amount": 3000, "debt_date": "2026-08-01"})
    p = dp.build(monthly=5000, order="faiz", today=TODAY)
    assert p["totals"]["fixed"] == 15000.0 and p["totals"]["fixed_count"] == 2 and p["totals"]["fixed_this_month"] == 3000
    assert p["totals"]["person_count"] == 1 and p["totals"]["open"] == 18000.0
    kinds = [d["kind"] for d in p["debts"]]
    assert kinds == ["fixed", "fixed", "person"]
    fx = next(d for d in p["debts"] if d["id"] == did)
    assert fx["subtitle"].startswith("Vergi / SGK · Ayda ₺2.000 · 6 taksit · her ayın 15'i")
    items = {i["title"]: i for i in p["this_month"]["items"]}
    assert items["Vergi Dairesi"]["amount"] == 2000.0 and items["Vergi Dairesi"]["text"] == "1. taksit · son gün 15 Kasım"
    assert items["Nermin Teyze"]["kind"] == "fixed" and items["Nermin Teyze"]["text"].startswith("1. taksit")
    assert items["Nermin Teyze"]["payable"]["amount"] == 1000.0  # Ödedim penceresi taksitle açılır
    assert items["Komşu"]["amount"] == 2000.0  # 5.000 − 3.000 taksit
    assert p["fixed_short"] is None
    # 5 Kasım taksiti geçti (bugün 10 Kasım): uyarı nötr
    w = next(w for w in p["warnings"] if w["key"].startswith("taksit:"))
    assert "1. taksitin günü (5 Kasım) geçti" in w["text"]
    short = dp.build(monthly=2000, today=TODAY)
    assert short["fixed_short"]["title"] == "Aylık tutar bu ayın taksitlerine yetmiyor"
    assert "₺3.000" in short["fixed_short"]["text"]
    # planı başlat: taksitler de anlık görüntüde
    snap = client.post("/api/debt-plan/commit", json={"monthly": 5000}).json()
    assert snap["commitment"]["status"]["key"] == "ok" and not snap["commitment"]["changed"]


def test_no_advice_text_mentions_restructuring(client, tmpdb):
    """Ödeme planı olan borç yalnız kayıttır: plan hiçbir metinde yapılandırma, erteleme, borç transferi, kredi vb.
    önermez (bütün yanıt; her tutar ve sıralamada)."""
    from app import debt_plan as dp
    from tests.test_debt_plan import _seed_cards

    _seed_cards(tmpdb)
    _sched(client)
    _sched(client, person="Örnek Bank", inst=3000, count=12, ctype="banka", plan_interest="var")
    client.post("/api/debts", json={"person": "Nermin Teyze", "direction": "borc", "amount": 900, "debt_date": "2026-08-01"})
    for monthly in (0, 500, 4000, 15000, 100000):
        for order in dp.ORDERS:
            p = dp.build(monthly=monthly, order=order, today=TODAY)
            bad = {s: _banned_in(s) + _restructure_in(s) for s in _all_strings(p) if _banned_in(s) or _restructure_in(s)}
            assert not bad, bad
    for d in client.get("/api/debts").json():
        if d["schedule"]:
            assert not _restructure_in(d["schedule"]["text"])


def test_restructure_scanner_catches_examples():
    for s in ("Borcunu yapılandır", "Yapılandırma başvurusu yap", "Ödemeyi ertele", "Vadeyi uzat"):
        assert _restructure_in(s) or _banned_in(s), s
    for s in ("Ödeme planı olan borç (ör. vergi/SGK)", "1. taksit · son gün 15 Kasım", "Ayda ₺2.000 · 6 taksit"):
        assert not _restructure_in(s) and not _banned_in(s), s


# ---------------------------------------------------------------- hatırlatma

def test_push_reminds_each_installment(client):
    from app import push

    did = _sched(client, day=12)
    two = push._borc(date(2026, 11, 10))
    assert len(two) == 1 and two[0]["kind"] == "borc" and "1. taksitin günü 2 gün sonra" in two[0]["title"]
    assert "₺2.000" in two[0]["body"] and two[0]["route"] == "#/diger/borc-plani"
    assert push._borc(date(2026, 11, 11)) == []
    assert "bugün" in push._borc(date(2026, 11, 12))[0]["title"]
    client.post(f"/api/debts/{did}/pay", json={"amount": 2000, "paid_at": "2026-11-10"})
    assert push._borc(date(2026, 11, 12)) == []  # ödendi
    nxt = push._borc(date(2026, 12, 10))
    assert len(nxt) == 1 and "2. taksitin" in nxt[0]["title"] and nxt[0]["id"] != two[0]["id"]


@pytest.mark.parametrize("n,ek", [(1, "i"), (2, "si"), (3, "ü"), (5, "i"), (6, "sı"), (9, "u"), (10, "u"), (15, "i"),
                                  (20, "si"), (28, "i"), (30, "u"), (31, "i")])
def test_day_suffix(n, ek):
    from app import debt_schedule as ds

    assert ds._ek(n) == ek
