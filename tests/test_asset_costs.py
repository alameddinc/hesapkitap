"""Varlıklarım Aşama 2 (app/asset_costs.py): araç masrafı tanıma, plaka, atama sırası, öğrenme, geriye dönük toplu
ekleme, masraf özeti, paket kilidi ve ortak defter yetkileri. İşyeri adları, plakalar, kart numaraları ve tutarlar
uydurmadır ("06 ORN 123" düzeni)."""
from datetime import date, timedelta

from app import asset_costs as ac
from tests.test_book import _member, owner  # noqa: F401 - fixture

CARD = "4111********1111"
CAR = {"kind": "arac", "name": "Örnek Araba", "plate": "06 ORN 123", "vehicle_class": "otomobil", "model_year": 2022,
       "fuel": "benzin"}
CAR2 = {**CAR, "name": "Örnek İş Aracı", "plate": "06 ORN 456", "fuel": "dizel"}
T = date.today()


def _d(n: int) -> str:
    return (T - timedelta(days=n)).isoformat()


def _car(c, body=None):
    r = c.post("/api/assets", json={**(body or CAR), "obligations": []})
    assert r.status_code == 200, r.text
    return r.json()


def _entry(c, desc, amount, days=0, card="1111", plate=None, cat=None, **kw):
    b = {"entry_date": _d(days), "description": desc, "amount": amount, "kind": "gider", "category": cat,
         "payment": "kredi_karti" if card else "nakit", **({"card_last4": card} if card else {}),
         **({"plate": plate} if plate else {}), **kw}
    r = c.post("/api/entries", json=b)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _stmt(rows):
    from app import db
    from app.parsers.base import Statement, Transaction

    txns = [Transaction(date.fromisoformat(_d(n)), desc, amt, "harcama", CARD) for n, desc, amt in rows]
    s = Statement(bank="Örnek Bank", card_no=CARD, period_end=T, due_date=T + timedelta(days=10),
                  total_debt=round(sum(r[2] for r in rows), 2), min_payment=None, credit_limit=None, transactions=txns)
    return db.save_statement(f"ornek-{T}".encode(), "ornek.pdf", s)


# ---------------------------------------------------------------- tanıma

def test_classify_vehicle_costs():
    assert ac.classify("OPET ORNEK ISTASYON ANKARA") == "yakit"
    assert ac.classify("Shell Örnek") == "yakit"
    assert ac.classify("PETROL OFISI ORNEK") == "yakit"
    assert ac.classify("AYTEMIZ ORNEK") == "yakit" and ac.classify("LUKOIL 123") == "yakit"
    assert ac.classify("ORNEK ESARJ ISTASYONU") == "yakit"
    assert ac.classify("HGS BAKIYE YUKLEME") == "yol" and ac.classify("KGM OTOYOL GECIS") == "yol"
    assert ac.classify("ISPARK ORNEK") == "otopark"
    assert ac.classify("ORNEK LASTIK SAN") == "servis" and ac.classify("ORNEK OTO YIKAMA") == "servis"
    assert ac.classify("ORNEK KAFE") is None
    assert ac.classify("UBER TRIP") is None and ac.classify("BITAKSI") is None
    assert ac.classify("ORNEK ISTASYON", "Akaryakıt") == "yakit"  # kullanıcının "akaryakıt" kategorisi
    assert ac.classify("ORNEK MARKET") is None


def test_plate_normalize_and_text():
    assert ac.normalize_plate("06 orn 123") == "06ORN123"
    assert ac.normalize_plate("06-ORN-123") == "06ORN123"
    assert ac.normalize_plate("99 ORN 1") is None and ac.normalize_plate("") is None
    assert ac.plate_in_text("ORNEK AKARYAKIT\nPLAKA: 06 ORN 123\nTOPLAM 1.000,00") == "06ORN123"
    assert ac.plate_in_text("ORNEK 06 ORN 123") is None  # etiketsiz harf-rakam dizisi plaka sayılmaz


def test_receipt_reading_normalizes_plate():
    from app import receipt

    doc = receipt._doc_of({"doc_type": "fis", "merchant": "Örnek Akaryakıt", "total": 1500, "plate": "06 orn 123",
                           "items": []}, ["Ulaşım & Araç"])
    assert doc["plate"] == "06ORN123"
    bad = receipt._doc_of({"doc_type": "fis", "merchant": "x", "total": 1, "plate": "BİLİNMİYOR", "items": []}, ["Diğer"])
    assert bad["plate"] is None


# ---------------------------------------------------------------- geriye dönük toplu ekleme

def test_backfill_preview_and_all_skips_foreign_plates(client):
    a = _car(client)
    b = _car(client, CAR2)
    _entry(client, "ORNEK AKARYAKIT", 1200, 40)
    _entry(client, "HGS YUKLEME", 300, 20)
    _entry(client, "ORNEK AKARYAKIT", 900, 10, plate="06 ORN 456")   # öbür aracın plakası: kendiliğinden ona
    _entry(client, "ORNEK AKARYAKIT", 800, 5, plate="06 ORN 789")    # tanınmayan plaka: bağlanmaz
    _entry(client, "ORNEK KAFE", 100, 3)
    p = client.get(f"/api/assets/{a['id']}/backfill", params={"since": _d(60)}).json()
    assert p["count"] == 2 and p["total"] == 1500 and p["excluded"] == {"other_plate": 0, "unknown_plate": 1}
    assert {k["kind"] for k in p["by_kind"]} == {"yakit", "yol"}
    r = client.post(f"/api/assets/{a['id']}/backfill", json={"mode": "all", "since": _d(60)}).json()
    assert r["linked"] == 2
    ca = client.get(f"/api/assets/{a['id']}").json()["costs"]
    assert ca["last_12"] == 1500 and {g["kind"] for g in ca["groups"]} == {"yakit", "yol"}
    cb = client.get(f"/api/assets/{b['id']}/costs").json()
    assert [x["amount"] for x in cb["items"]] == [900] and cb["items"][0]["link_source"] == "plate"
    plates = client.get("/api/assets/plates").json()["items"]
    assert [p["plate"] for p in plates] == ["06ORN789"]
    assert client.post("/api/assets/plates/ignore", json={"plate": "06 ORN 789"}).status_code == 200
    assert client.get("/api/assets/plates").json()["items"] == []


def test_single_car_auto_links_from_costs_since_only(client):
    _entry(client, "ORNEK AKARYAKIT", 1000, 30)  # araçtan önceki: kendiliğinden bağlanmaz, toplu eklemede sorulur
    a = _car(client)
    _entry(client, "SHELL ORNEK", 700, 0)
    c = client.get(f"/api/assets/{a['id']}").json()
    assert c["costs"]["last_12"] == 700 and c["backfill_asked"] is False
    assert client.get("/api/assets/questions").json()["count"] == 0  # tek araç: soru yok
    assert client.post(f"/api/assets/{a['id']}/backfill", json={"mode": "review", "since": _d(60)}).status_code == 200
    q = client.get("/api/assets/questions").json()
    assert q["count"] == 1 and q["items"][0]["options"][0]["id"] == a["id"]
    assert client.get(f"/api/assets/{a['id']}").json()["backfill_asked"]


def test_electric_car_fuel_station_is_asked(client):
    a = _car(client, {**CAR, "fuel": "elektrik"})
    _entry(client, "ORNEK ESARJ", 400, 0)
    _entry(client, "OPET ORNEK", 1000, 0)
    assert client.get(f"/api/assets/{a['id']}").json()["costs"]["last_12"] == 400
    assert client.get("/api/assets/questions").json()["count"] == 1


# ---------------------------------------------------------------- iki araç: soru, öğrenme

def test_two_cars_question_learn_and_conflict(client):
    a, b = _car(client), _car(client, CAR2)
    e1 = _entry(client, "OPET ORNEK KADIKOY", 1000, 0)
    e2 = _entry(client, "OPET ORNEK KADIKOY", 800, 0)
    q = client.get("/api/assets/questions").json()
    assert q["count"] == 2 and len(q["items"]) == 1 and q["items"][0]["card_last4"] == "1111"
    g = q["items"][0]
    r = client.post("/api/assets/links", json={"entry_ids": g["entry_ids"], "asset_id": b["id"], "remember": True}).json()
    assert r["count"] == 2 and r["learned"] and all(p["source"] is None for p in r["previous"])
    e3 = _entry(client, "OPET ORNEK KADIKOY", 600, 0)  # öğrenilen kural: sorulmaz
    info = client.get("/api/assets/links", params={"entry_id": e3}).json()
    assert info["link"]["asset_id"] == b["id"] and info["link"]["source"] == "rule"
    # aynı kalıp başka araca: kural silinir (karışık kullanım), sonraki yine sorulur
    client.post("/api/assets/links", json={"entry_ids": [e1], "asset_id": a["id"], "remember": True})
    _entry(client, "OPET ORNEK KADIKOY", 500, 0)
    assert client.get("/api/assets/questions").json()["count"] == 1
    # bağı kaldır: araçla ilgisi yok (yeniden bağlanmaz)
    lid = client.get("/api/assets/links", params={"entry_id": e2}).json()["link"]["id"]
    assert client.delete(f"/api/assets/links/{lid}").status_code == 200
    info = client.get("/api/assets/links", params={"entry_id": e2}).json()
    assert info["link"]["asset_id"] is None
    # geri al (clear): bağ silinir, kurallarla yeniden
    client.post("/api/assets/links", json={"entry_ids": [e2], "clear": True})
    assert client.get("/api/assets/links", params={"entry_id": e2}).json()["link"] is None


def test_card_transactions_are_candidates(client):
    a = _car(client)
    client.post(f"/api/assets/{a['id']}/backfill", json={"mode": "all", "since": _d(60)})
    _stmt([(5, "OPET ORNEK ANKARA", 1500.0), (6, "ORNEK KAFE", 90.0), (7, "ISPARK ORNEK", 60.0)])
    c = client.get(f"/api/assets/{a['id']}/costs").json()
    assert sorted(x["amount"] for x in c["items"]) == [60, 1500]
    fuel = client.get(f"/api/assets/{a['id']}/costs", params={"group": "yakit"}).json()
    assert [x["description"] for x in fuel["items"]] == ["OPET ORNEK ANKARA"]


def test_paid_due_counted_once(client):
    a = _car(client)
    a = client.post(f"/api/assets/{a['id']}/obligations", json={"kind": "kasko", "start": _d(-10), "amount": 9000}).json()
    did = a["dues"][0]["id"]
    assert client.post(f"/api/asset-dues/{did}/pay", json={"paid_on": T.isoformat(), "amount": 9000}).status_code == 200
    c = client.get(f"/api/assets/{a['id']}").json()["costs"]
    vade = next(g for g in c["groups"] if g["kind"] == "vade")
    assert vade["total"] == 9000 and c["this_month"] == 9000 and c["series"][-1]["total"] == 9000
    assert len(c["series"]) == 12 and c["monthly_avg"] == 9000  # tek ay veri: ortalama o ay


# ---------------------------------------------------------------- paket ve ortak defter

def test_free_plan_locked(client, monkeypatch):
    from tests.test_debt_jar import _hosted_user

    c, _ = _hosted_user(client, monkeypatch)
    assert c.get("/api/assets/questions").json()["locked"]
    assert c.post("/api/assets/links", json={"entry_ids": [1], "asset_id": None}).status_code == 402


def test_member_rules(owner):
    a = _car(owner)
    owner.post(f"/api/assets/{a['id']}/backfill", json={"mode": "all", "since": _d(60)})
    hid = _entry(owner, "ORNEK AKARYAKIT", 1000, 0, card=None)
    assert owner.patch(f"/api/entries/{hid}", json={"hidden": True}).status_code == 200
    _entry(owner, "HGS YUKLEME", 200, 0, card=None)
    assert owner.get(f"/api/assets/{a['id']}").json()["costs"]["last_12"] == 1200
    ed = _member(owner, "esim", "editor")
    assert ed.get(f"/api/assets/{a['id']}").json()["costs"]["last_12"] == 200  # gizli kayıt görünmez, toplama girmez
    vw = _member(owner, "goren", "viewer")
    assert ed.post("/api/assets/links", json={"entry_ids": [hid], "asset_id": None}).status_code == 404
    assert vw.post("/api/assets/links", json={"entry_ids": [hid], "asset_id": None}).status_code == 403


def test_capability(client):
    assert "asset_costs" in client.get("/api/meta").json()["capabilities"]


# ---------------------------------------------------------------- mutabakat düzeltmeleri (UAT 22.11; uydurma adlar)

def test_classify_dealer_fuel_names():
    for d in ("ORNEKOGLU PETROL", "ORNEK PETROL INSAAT", "ORNEK PETROL URUNLERI LTD", "ORNEK PETROLCULUK",
              "ORNEK PET SAN TIC", "ORNEK AKARYAKIT URUNLERI"):
        assert ac.classify(d) == "yakit", d
    assert ac.classify("ORNEK PETROKIMYA") is None


def test_bank_fees_never_vehicle_costs():
    for d in ("Vade Farki - ISPARK ORNEK", "BSMV - ISPARK ORNEK", "KKDF - ISPARK ORNEK", "FAIZ - ORNEK PETROL",
              "GECIKME FAIZI OTOYOL"):
        assert ac.classify(d, "Ulaşım & Araç") is None, d
    assert ac.classify("ISPARK ORNEK", "Banka Faiz & Ücret") is None
    assert ac.classify("ISPARK ORNEK", None, "faiz_ucret") is None


def test_unknown_vehicle_category_is_asked_not_linked(client):
    assert ac.classify("ORNEK ODEME/ORNEKLER", "Ulaşım & Araç") == "diger"
    assert ac.classify("UBER TRIP", "Ulaşım & Araç") is None
    a = _car(client)
    _entry(client, "ORNEK ODEME/ORNEKLER", 350, 0, cat="Ulaşım & Araç")
    assert client.get(f"/api/assets/{a['id']}").json()["costs"]["last_12"] == 0  # tek araçta da bağlanmaz
    q = client.get("/api/assets/questions").json()
    assert q["count"] == 1 and q["items"][0]["cost_kind"] == "diger"


def test_inspection_bosch_and_ogs_store():
    assert ac.classify("TUVTURK ORNEK ISTASYON") == "vade"
    assert ac.classify("BOSCH CAR SERVICE ORNEK") == "servis" and ac.classify("ORNEK BOSCH SANAYI") == "servis"
    assert ac.classify("PAYTR*OGS ORNEK") == "yol" and ac.classify("OGSTORE ORNEK") == "yol"


def test_toll_plate_from_statement(client):
    assert ac.toll_plate("HGS /1234567890/06ORN123") == "06ORN123"
    assert ac.toll_plate("HGS 06 ORN 123 GECIS") == "06ORN123"
    assert ac.toll_plate("HGS /1234567890/") is None and ac.toll_plate("ORNEK 06ORN123") is None
    _car(client)
    b = _car(client, CAR2)
    _stmt([(2, "HGS /1234567890/06ORN456", 120.0), (3, "HGS /1234567890/06ORN999", 80.0)])
    items = client.get(f"/api/assets/{b['id']}/costs").json()["items"]
    assert [(x["amount"], x["link_source"]) for x in items] == [(120, "plate")]
    assert [p["plate"] for p in client.get("/api/assets/plates").json()["items"]] == ["06ORN999"]


def test_tuvturk_payment_dedupes_with_paid_inspection(client):
    a = _car(client)
    client.post(f"/api/assets/{a['id']}/backfill", json={"mode": "all", "since": _d(60)})
    a = client.post(f"/api/assets/{a['id']}/obligations", json={"kind": "muayene", "start": _d(-5), "amount": 2000}).json()
    did = next(d for d in a["dues"] if d["kind"] == "muayene")["id"]
    client.post(f"/api/asset-dues/{did}/pay", json={"paid_on": T.isoformat(), "amount": 2000})
    _entry(client, "TUVTURK ORNEK", 2000, 0)
    c = client.get(f"/api/assets/{a['id']}").json()["costs"]
    assert c["last_12"] == 2000 and [g["kind"] for g in c["groups"]] == ["vade"]


def test_group_monthly_uses_own_first_month():
    """Yakıt yalnız bu ay varsa ayda yakıt = bu ayın toplamı (12'ye bölünmez)."""
    import app.asset_costs as ac
    from datetime import date
    on = date(2026, 11, 20)
    rs = [{"date": f"2026-{m:02d}-05", "amount": 100.0, "group": "yol"} for m in range(1, 12)]
    rs += [{"date": "2026-11-10", "amount": 900.0, "group": "yakit"}]
    orig_rows, orig_allowed = ac.rows, ac.allowed
    ac.rows, ac.allowed = (lambda _id, _on: rs), (lambda: True)
    try:
        s = ac.summary({"id": 1, "kind": "arac"}, on)
    finally:
        ac.rows, ac.allowed = orig_rows, orig_allowed
    assert s["fuel_monthly"] == 900.0
    assert next(g for g in s["groups"] if g["kind"] == "yol")["monthly"] == 100.0


def test_backfill_pick_links_only_selected(client):
    a = _car(client)
    _entry(client, "ORNEK AKARYAKIT", 1000, 40)
    _entry(client, "HGS YUKLEME", 250, 30)
    _entry(client, "ORNEK AKARYAKIT", 600, 20)
    p = client.get(f"/api/assets/{a['id']}/backfill", params={"since": _d(60)}).json()
    assert p["count"] == 3 and len(p["items"]) == 3
    it = p["items"][0]
    assert {"ref", "date", "description", "amount", "cost_kind"} <= set(it) and it["ref"][0] in "te"
    pick = [x["ref"] for x in p["items"] if x["amount"] in (1000, 250)]
    r = client.post(f"/api/assets/{a['id']}/backfill", json={"mode": "pick", "since": _d(60), "refs": pick + ["e999999"]})
    assert r.status_code == 200 and r.json()["linked"] == 2 and r.json()["mode"] == "pick"
    d = client.get(f"/api/assets/{a['id']}").json()
    assert d["costs"]["last_12"] == 1250 and d["backfill_asked"]
    assert {x["link_source"] for x in client.get(f"/api/assets/{a['id']}/costs").json()["items"]} == {"bulk"}
    left = client.get(f"/api/assets/{a['id']}/backfill", params={"since": _d(60)}).json()
    assert left["count"] == 1 and left["items"][0]["amount"] == 600  # seçilmeyen kendiliğinden bağlanmadı
    assert client.post(f"/api/assets/{a['id']}/backfill", json={"mode": "pick", "refs": []}).json()["linked"] == 0


def test_backfill_pick_member_rules(owner):
    a = _car(owner)
    _entry(owner, "ORNEK AKARYAKIT", 500, 10, card=None)
    ref = owner.get(f"/api/assets/{a['id']}/backfill").json()["items"][0]["ref"]
    vw = _member(owner, "goren", "viewer")
    assert vw.post(f"/api/assets/{a['id']}/backfill", json={"mode": "pick", "refs": [ref]}).status_code == 403
    ed = _member(owner, "esim", "editor")
    assert ed.post(f"/api/assets/{a['id']}/backfill", json={"mode": "pick", "refs": [ref]}).status_code == 403


def test_backfill_pick_locked_free_plan(client, monkeypatch):
    from tests.test_debt_jar import _hosted_user

    c, _ = _hosted_user(client, monkeypatch)
    p = c.get("/api/assets/1/backfill").json()
    assert p.get("locked") or p.get("count", 0) == 0
