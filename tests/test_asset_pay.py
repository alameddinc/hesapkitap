"""Varlıklarım H9 (app/asset_pay.py): vergi / sigorta / muayene ödemesinden vade "ödendi" önerisi, taksitli poliçe grubu,
onay / ret / geri al, çift sayım koruması (Planlama, Masraflar), Yapılacaklar kartı ve varlik_soru push'u. Şirket adları
yalnız kalıp için; kişi, plaka, tutar ve işyeri adları uydurmadır."""
from datetime import date, timedelta

from app import asset_pay as ap, assets, push
from tests.test_book import _member, owner  # noqa: F401 - fixture

CARD = "4111********2222"
CAR = {"kind": "arac", "name": "Örnek Araba", "plate": "06 ORN 321", "vehicle_class": "otomobil", "model_year": 2021,
       "fuel": "benzin"}
CAR2 = {**CAR, "name": "Örnek İkinci", "plate": "06 ORN 654"}
T = date.today()
_N = [0]


def _d(n: int) -> str:
    return (T - timedelta(days=n)).isoformat()


def _car(c, body=None):
    r = c.post("/api/assets", json={**(body or CAR), "obligations": []})
    assert r.status_code == 200, r.text
    return r.json()


def _ob(c, aid, kind, start, amount=None):
    r = c.post(f"/api/assets/{aid}/obligations", json={"kind": kind, "start": start, "amount": amount})
    assert r.status_code == 200, r.text
    return next(d for d in r.json()["dues"] if d["kind"] == kind)


def _entry(c, desc, amount, days=0):
    r = c.post("/api/entries", json={"entry_date": _d(days), "description": desc, "amount": amount, "kind": "gider",
                                     "payment": "nakit"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _stmt(rows, period_end: date):
    """rows: (gün önce, açıklama, tutar, installment_no, installments_left)."""
    from app import db
    from app.parsers.base import Statement, Transaction

    _N[0] += 1
    txns = [Transaction(date.fromisoformat(_d(n)), desc, amt, "harcama", CARD, installment_no=no, installments_left=left)
            for n, desc, amt, no, left in rows]
    s = Statement(bank="Örnek Bank", card_no=CARD, period_end=period_end, due_date=period_end + timedelta(days=10),
                  total_debt=round(sum(r[2] for r in rows), 2), min_payment=None, credit_limit=None, transactions=txns)
    return db.save_statement(f"ornek-pay-{_N[0]}".encode(), "ornek.pdf", s)


def _sug(c):
    return c.get("/api/assets/pay-suggestions").json()


# ---------------------------------------------------------------- kalıplar

def test_payment_kind_patterns():
    assert ap.payment_kind("GIB MTV ODEME 06ORN321")["kinds"] == ("mtv",)
    assert ap.payment_kind("Motorlu Taşıtlar Vergisi")["kinds"] == ("mtv",)
    assert ap.payment_kind("GIB DIJITAL VERGI DAIRESI")["kinds"] == ("mtv", "kira_beyan")
    assert ap.payment_kind("ORNEK BELEDIYESI EMLAK VERGISI")["kinds"] == ("emlak",)
    assert ap.payment_kind("NEOVA SİGORTA A. Ş. ORNEK 7/8 Taksit")["company"] == "NEOVA"
    assert ap.payment_kind("ALLIANZ SIGORTA KASKO")["kinds"] == ("kasko",)
    assert ap.payment_kind("Anadolu Anonim Türk Sigorta")["company"] == "ANADOLU SIGORTA"
    assert ap.payment_kind("ORNEK SIGORTA ARACILIK TRAFIK")["kinds"] == ("trafik",)
    assert ap.payment_kind("TÜRKİYE SİGORTA DASK")["kinds"] == ("dask",)
    assert ap.payment_kind("TUVTURK ORNEK ISTASYON")["kinds"] == ("muayene",)
    for d in ("SOMPO", "MAPFRE", "HDI SIGORTA", "AXA SIGORTA", "QUICK SIGORTA", "RAY SIGORTA", "AK SIGORTA", "DOGA SIGORTA",
              "UNICO SIGORTA", "BEREKET SIGORTA", "GROUPAMA", "ZURICH", "EUREKO SIGORTA"):
        assert ap.payment_kind(f"{d} ORNEK")["group"] == "sigorta", d
    assert ap.payment_kind("ANADOLU HAYAT EMEKLILIK") is None  # hayat / BES varlık sigortası değil
    assert ap.payment_kind("ORNEK SAGLIK SIGORTASI") is None
    assert ap.payment_kind("BSMV - ORNEK SIGORTA") is None and ap.payment_kind("ORNEK KAFE") is None
    assert ap.payment_kind("ANADOLU ORNEK MARKET") is None


def test_parse_installment():
    assert ap.parse_installment("NEOVA SİGORTA A. Ş. ORNEK 7/8 Taksit") == (7, 8)
    assert ap.parse_installment("ORNEK SIGORTA TAKSIT 07/08") == (7, 8)
    assert ap.parse_installment("ORNEK SIGORTA TKS 3/6") == (3, 6)
    assert ap.parse_installment("TOPLAM TL İşlemin 1 / 9 Taksidi") == (1, 9)
    assert ap.parse_installment("ORNEK SIGORTA", 2, 4) == (2, 6)
    assert ap.parse_installment("ORNEK 12/2026") is None and ap.parse_installment("ORNEK 9/8 Taksit") is None
    assert ap.parse_installment("ORNEK 1/1 Taksit") is None


def test_group_installments_same_policy():
    def x(ref, n, amt, d, month, comp="NEOVA"):
        return {"ref": ref, "txn_id": 1, "entry_id": None, "date": d, "month": month, "description": "x", "amount": amt,
                "pk": {"company": comp, "kinds": ("kasko",), "group": "sigorta"}, "inst": (n, 8)}
    xs = [x("t1", 5, 1000, "2026-09-10", "2026-09"), x("t2", 6, 1005, "2026-10-10", "2026-10"),
          x("t3", 7, 1000, "2026-11-10", "2026-11"),
          x("t4", 6, 1200, "2026-10-12", "2026-10"),            # başka tutar: başka poliçe
          x("t5", 2, 1000, "2026-11-11", "2026-11", "ALLIANZ")]  # başka şirket
    gs = ap.group_installments(xs)
    g = next(g for g in gs if g["refs"] == ["t1", "t2", "t3"])
    assert g["m"] == 8 and g["seen"] == [5, 6, 7] and g["total"] == 8000 and g["start"] == date(2026, 5, 10)
    assert len(gs) == 3
    # asıl işlem tarihini her taksitte yazan banka: başlangıç o tarih
    same = [x("a", 3, 500, "2026-08-01", "2026-10"), x("b", 4, 500, "2026-08-01", "2026-11")]
    g2 = ap.group_installments(same)
    assert len(g2) == 1 and g2[0]["start"] == date(2026, 8, 1) and g2[0]["total"] == 4000


# ---------------------------------------------------------------- öneri, onay, geri al

def test_single_payment_suggested_then_accepted(client):
    a = _car(client)
    due = _ob(client, a["id"], "kasko", _d(-10), 9000)
    other = _entry(client, "ORNEK SIGORTA KASKO", 12000, 3)      # tutar %15 dışında: önerilmez
    assert other and _sug(client)["count"] == 0
    eid = _entry(client, "ORNEK SIGORTA KASKO", 8800, 2)
    s = _sug(client)
    assert s["count"] == 1 and s["can_answer"]
    x = s["items"][0]
    assert x["question"] == "paid" and x["options"][0]["due_id"] == due["id"] and not x["ask_amount"]
    assert x["title"] == "Örnek Araba · Kasko ödendi mi?" and "06ORN321" not in x["title"] + x["text"]
    plan_before = [p["id"] for p in assets.plan_dues(T)]
    assert due["id"] in plan_before
    r = client.post("/api/assets/pay-suggestions/accept", json={"key": x["key"], "due_id": due["id"]})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/asset-dues/{due['id']}").json()
    assert d["status"] == "odendi" and d["paid_amount"] == 8800 and d["paid_on"] == _d(2) and d["paid_entry_id"] == eid
    assert due["id"] not in [p["id"] for p in assets.plan_dues(T)]  # harcanabilirden ayrıca düşmez
    assert _sug(client)["count"] == 0
    assert client.post("/api/assets/pay-suggestions/accept", json={"key": x["key"], "due_id": due["id"]}).status_code == 404
    # geri al: vade bekliyor, öneri geri gelir
    assert client.post(f"/api/asset-dues/{due['id']}/unpay").status_code == 200
    assert _sug(client)["count"] == 1
    # ret: bir daha önerilmez
    assert client.post("/api/assets/pay-suggestions/dismiss", json={"key": x["key"]}).status_code == 200
    assert _sug(client)["count"] == 0


def test_window_45_days(client):
    a = _car(client)
    _ob(client, a["id"], "trafik", _d(-50), 3000)  # son gün 50 gün sonra: bugünkü ödeme pencere dışında
    _entry(client, "ORNEK SIGORTA TRAFIK", 3000, 0)
    assert _sug(client)["count"] == 0


def test_no_due_amount_asks_amount(client):
    a = _car(client)
    due = _ob(client, a["id"], "trafik", _d(-5))
    _entry(client, "ORNEK SIGORTA TRAFIK", 4321, 0)
    x = _sug(client)["items"][0]
    assert x["ask_amount"] and x["text"].endswith("Tutarı da kaydedelim mi?")
    client.post("/api/assets/pay-suggestions/accept", json={"key": x["key"], "due_id": due["id"], "save_amount": False})
    d = client.get(f"/api/asset-dues/{due['id']}").json()
    assert d["status"] == "odendi" and d["paid_amount"] is None


def test_two_cars_which_car(client):
    a, b = _car(client), _car(client, CAR2)
    _ob(client, a["id"], "trafik", _d(-5), 3000)
    db_ = _ob(client, b["id"], "trafik", _d(-8), 3100)
    _entry(client, "ORNEK SIGORTA TRAFIK", 3050, 0)
    x = _sug(client)["items"][0]
    assert x["question"] == "which" and x["title"].endswith("Bu hangi araç?")
    assert {o["asset_name"] for o in x["options"]} == {"Örnek Araba", "Örnek İkinci"}
    client.post("/api/assets/pay-suggestions/accept", json={"key": x["key"], "due_id": db_["id"]})
    assert client.get(f"/api/asset-dues/{db_['id']}").json()["status"] == "odendi"
    assert _sug(client)["count"] == 0  # işlem kullanıldı: öbür araca yeniden önerilmez


def test_mtv_both_installments_at_once(client):
    a = _car(client)
    aid = a["id"]
    on = date(T.year, 1, 20)
    assets.add_obligation(aid, {"kind": "mtv", "amount": 8000, "from": f"{T.year}-01-01"}, on=on)  # 2 × 4.000
    _entry(client, "GIB MTV ODEME", 8000, (T - on).days)
    s = ap.suggestions(on)
    x = s["items"][0]
    o = x["options"][0]
    assert o["double"] and len(o["due_ids"]) == 2
    ap.accept(x["key"], o["due_id"], on=on)
    with __import__("app").db.connect() as c:
        rows = c.execute("SELECT status, paid_amount FROM asset_dues WHERE id IN (?, ?)", o["due_ids"]).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("odendi", 4000), ("odendi", 4000)]


# ---------------------------------------------------------------- taksitli poliçe

def _policy_rows(per=1000.0):
    """NEOVA kasko 8 taksit; 5., 6., 7. taksit son üç ekstrede (biri metinde, ikisi ekstre alanında)."""
    for k, n in ((2, 5), (1, 6), (0, 7)):
        pe = assets.add_months(T, -k)
        desc = f"NEOVA SİGORTA A. Ş. ORNEK {n}/8 Taksit" if n == 7 else "NEOVA SIGORTA A.S. ORNEK"
        _stmt([((T - pe).days, desc, per, None if n == 7 else n, None if n == 7 else 8 - n)], pe)


def test_installment_policy_matches_kasko_and_counts_once(client):
    a = _car(client)
    start = assets.add_months(T, -6)  # 1. taksit = poliçe başlangıcı = kasko son günü
    oid = assets.add_obligation(a["id"], {"kind": "kasko", "start": start.isoformat(), "amount": 8200,
                                          "from": (start - timedelta(days=30)).isoformat()})
    assert oid
    _policy_rows()
    s = _sug(client)
    assert s["count"] == 1
    x = s["items"][0]
    assert x["inst"]["m"] == 8 and x["inst"]["seen"] == [5, 6, 7] and x["amount"] == 8000
    assert x["date"] == start.isoformat() and "iki kez düşmeyiz" in x["text"] and "taksit" in x["text"]
    did = x["options"][0]["due_id"]
    client.post("/api/assets/pay-suggestions/accept", json={"key": x["key"], "due_id": did})
    d = client.get(f"/api/asset-dues/{did}").json()
    assert d["status"] == "odendi" and d["status_label"] == "Taksitle ödeniyor (7/8)" and d["paid_amount"] == 8000
    assert d["installment"]["m"] == 8 and not d["installment"]["done"]
    # yeni taksit gelse de yeniden önerilmez (aynı grup)
    pe = assets.add_months(T, 1)
    _stmt([(0, "NEOVA SİGORTA A. Ş. ORNEK 8/8 Taksit", 1000.0, None, None)], pe)
    assert _sug(client)["count"] == 0
    # Masraflar: poliçe toplamı bir kez (taksitler araca bağlanmaz), tür sigorta-vergi-muayene
    c = client.get(f"/api/assets/{a['id']}").json()["costs"]
    vade = next(g for g in c["groups"] if g["kind"] == "vade")
    assert vade["total"] == 8000 and vade["count"] == 1
    items = client.get(f"/api/assets/{a['id']}/costs", params={"group": "vade"}).json()["items"]
    assert items[0]["description"] == "Kasko · 8 taksit" and items[0]["installment"]["m"] == 8
    # Planlama'da vade yok (kalan taksitler kart ödemesinde)
    assert did not in [p["id"] for p in assets.plan_dues(T)]


def test_insurance_installments_not_linked_as_vehicle_cost():
    from app import asset_costs

    assert asset_costs.classify("NEOVA SIGORTA ORNEK 7/8 Taksit", "Ulaşım & Araç") is None
    assert asset_costs.classify("GIB MTV ODEME", "Ulaşım & Araç") is None
    assert asset_costs.classify("TUVTURK ORNEK") == "vade"  # muayene eskisi gibi (masrafta vadeyle tekilleşir)


# ---------------------------------------------------------------- Yapılacaklar, push, yetki

def test_reminder_card(client):
    a = _car(client)
    _ob(client, a["id"], "kasko", _d(-3), 5000)
    _entry(client, "ORNEK SIGORTA KASKO", 5000, 0)
    items = client.get("/api/reminders", params={"assets": 1}).json()
    card = next(i for i in items if i.get("action") == "asset_paid")
    assert card["kind"] == "varlik" and card["suggestion_key"] and card["tag"] == "Sigorta" and card["cta"] == "Onayla"
    assert not [i for i in client.get("/api/reminders").json() if i.get("action") == "asset_paid"]  # eski istemci


def test_push_varlik_soru_weekly(client, monkeypatch):
    monkeypatch.setattr(push, "has_app", lambda uid, v: True)
    assert push.TYPES["varlik_soru"][1] is False  # varsayılan kapalı
    a = _car(client)
    _ob(client, a["id"], "kasko", _d(-3), 5000)
    _entry(client, "ORNEK SIGORTA KASKO", 5000, T.weekday() + 1)  # geçen pazar: haftanın her günü görünür
    mon = T - timedelta(days=T.weekday())
    items = push._varlik_soru({"id": 1}, mon)
    assert len(items) == 1 and items[0]["kind"] == "varlik_soru" and items[0]["route"].startswith("#/diger/varliklar")
    y, w, _ = mon.isocalendar()
    assert items[0]["id"] == f"varlik_soru:{y}-W{w:02d}" and len(items[0]["title"]) <= 40
    # aynı hafta başka gün: aynı kimlik (push_sent ikinci kez göndermez); sonraki hafta yeni kimlik
    assert push._varlik_soru({"id": 1}, mon + timedelta(days=6))[0]["id"] == items[0]["id"]
    assert push._varlik_soru({"id": 1}, mon + timedelta(days=7))[0]["id"] != items[0]["id"]


def test_viewer_cannot_answer(owner):
    a = _car(owner)
    due = _ob(owner, a["id"], "kasko", _d(-3), 5000)
    _entry(owner, "ORNEK SIGORTA KASKO", 5000, 0)
    vw = _member(owner, "goren", "viewer")
    s = vw.get("/api/assets/pay-suggestions").json()
    assert not s["can_answer"]
    key = owner.get("/api/assets/pay-suggestions").json()["items"][0]["key"]
    assert vw.post("/api/assets/pay-suggestions/accept", json={"key": key, "due_id": due["id"]}).status_code == 403
    assert vw.post("/api/assets/pay-suggestions/dismiss", json={"key": key}).status_code == 403


def test_capability(client):
    assert "asset_pay" in client.get("/api/meta").json()["capabilities"]
