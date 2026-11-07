"""Alışveriş listesi v3: miktar + birim (metinden okuma, birimli fiyat), otomatik doldurma, plan kaydı, tamamlama ve
geçmiş alışverişler, fişi listeye bağlama (emin / sor), tasarruf (piyasa ortancasına göre). Fiyatlar ve ürünler uydurmadır."""
from datetime import date, timedelta

import pytest

from app import price_sources, shopping, system
from tests.test_book import _member, owner  # noqa: F401  (fixture)

TODAY = date.today().isoformat()


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)


def _official(rows, day=TODAY, region="İstanbul"):
    """rows: [(zincir, grup, fiyat)]"""
    with system.connect() as c:
        c.executemany("INSERT INTO official_prices (chain, group_key, title, brand, price, day, region) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(ch, g, f"Örnek {g}", "Örnekmarka", p, day, region) for ch, g, p in rows])


def _receipt(client, store, items, day=TODAY):
    """items: [(ad, adet, tutar[, birim])] → POST /api/entries yanıtı."""
    total = round(sum(x[2] for x in items), 2)
    return client.post("/api/entries", json={
        "entry_date": day, "description": store, "amount": total, "kind": "gider", "category": "Market & Gıda",
        "payment": "nakit", "items": [{"name": n, "qty": q, "amount": a, **({"unit": x[3]} if len(x) > 3 else {})}
                                      for x in items for n, q, a in [x[:3]]]}).json()


# ---------------------------------------------------------------- miktar ve birim

@pytest.mark.parametrize("text,want", [
    ("2.5 kg elma", ("elma", 2.5, "kg")),
    ("elma 2,5kg", ("elma", 2.5, "kg")),
    ("6x süt", ("süt", 6, "adet")),
    ("6 süt", ("süt", 6, "adet")),
    ("3 adet limon", ("limon", 3, "adet")),
    ("500 g kıyma", ("kıyma", 500, "g")),
    ("süt 1 lt", ("süt 1 lt", 1, "adet")),        # bilgi yokken hacim paket boyu: metin aynen kalır
    ("ekmek", ("ekmek", 1, "adet")),
])
def test_parse_amount(text, want):
    assert shopping.parse_amount(text) == want


def test_volume_is_amount_when_not_a_known_pack(client, resmi):
    _official([("BİM", "sut|1l", 30), ("A101", "sut|1l", 32), ("BİM", "su|1.5l", 10)])
    assert shopping.parse_amount("1,5 L süt") == ("süt", 1.5, "L")      # 1,5 L'lik süt yok: miktar
    assert shopping.parse_amount("su 1,5 L") == ("su 1,5 L", 1, "adet")  # bilinen paket boyu: boy


def test_unit_pricing(client, resmi):
    _official([("BİM", "elma|kg", 40), ("Migros", "elma|kg", 50), ("BİM", "sut|1l", 30), ("Migros", "sut|1l", 34),
               ("BİM", "peynir|1kg", 200), ("BİM", "yumurta|30ad", 150)])
    elma = client.post("/api/shopping-list", json={"text": "2,5 kg elma"}).json()
    assert (elma["text"], elma["qty"], elma["unit"], elma["amount_label"]) == ("elma", 2.5, "kg", "2,5 kg")
    sut = client.post("/api/shopping-list", json={"text": "Süt", "qty": 1.5, "unit": "L"}).json()
    peynir = client.post("/api/shopping-list", json={"text": "peynir", "qty": 750, "unit": "g"}).json()
    yum = client.post("/api/shopping-list", json={"text": "Yumurta"}).json()
    by = {i["id"]: i for i in client.get("/api/shopping-list").json()["items"]}
    assert (by[elma["id"]]["best"]["chain"], by[elma["id"]]["best"]["line_total"]) == ("BİM", 100.0)   # 2,5 × 40
    assert by[sut["id"]]["best"]["line_total"] == 45.0 and by[sut["id"]]["assumed"] is None           # 1,5 × 30
    assert by[peynir["id"]]["mult"] == 0.75 and by[peynir["id"]]["best"]["line_total"] == 150.0        # 750 g → 0,75 kg
    assert by[yum["id"]]["best"]["line_total"] == 150.0                                                # 1 adet × paket
    # yumurta kilo ile satılmaz: fiyat hesaplanmaz, nedeni söylenir
    bad = client.patch(f"/api/shopping-list/{yum['id']}", json={"unit": "kg", "qty": 2}).json()
    assert bad["unit_mismatch"] is True and bad["mult"] is None
    o = client.get("/api/shopping-list/optimize").json()
    assert next(u for u in o["unpriced_info"] if u["id"] == yum["id"])["reason"] == "unit_mismatch"
    bim = next(s for s in o["plans"][0]["stops"] if s["chain"] == "BİM")
    assert {x["id"]: x["line_total"] for x in bim["items"]} == {elma["id"]: 100.0, sut["id"]: 45.0, peynir["id"]: 150.0}
    assert client.post("/api/shopping-list", json={"text": "un", "qty": 5, "unit": "ton"}).status_code == 422
    assert client.post("/api/shopping-list", json={"text": "un", "qty": 1500, "unit": "adet"}).status_code == 422
    assert client.post("/api/shopping-list", json={"text": "un", "qty": 1500, "unit": "g"}).status_code == 200


def test_chain_names_only():
    assert price_sources.chain_name("BİM (Dost)") == "BİM"
    assert price_sources.chain_name("Pizza Amore · Üsküdar") == "Pizza Amore"
    assert price_sources.chain_name("Örnek Fırın - Kadıköy") == "Örnek Fırın"


# ---------------------------------------------------------------- otomatik doldurma

def test_suggest_frequent_due(client):
    t = date.today()
    for d in (28, 21, 14, 8):  # süt: haftada bir, son alım 8 gün önce → zamanı geldi
        _receipt(client, "BİM", [("Pınar Süt 1L", 2, 60), ("Ekmek", 1, 12)], (t - timedelta(days=d)).isoformat())
    _receipt(client, "BİM", [("Ekmek", 1, 12)], (t - timedelta(days=1)).isoformat())  # ekmek dün alındı: zamanı değil
    s = client.get("/api/shopping-list/suggest?q=sü").json()["items"]
    assert s[0]["text"] == "Süt" and s[0]["group_key"] == "sut|1l" and s[0]["source"] == "own"
    f = client.get("/api/shopping-list/frequent").json()["items"]
    assert [x["text"] for x in f] == ["Ekmek", "Süt"] and f[0]["count"] == 5
    due = client.get("/api/shopping-list/due").json()["items"]
    assert [x["text"] for x in due] == ["Süt"]
    assert (due[0]["interval_days"], due[0]["days_since"], due[0]["qty"], due[0]["unit"]) == (7, 8, 2.0, "adet")
    r = client.post("/api/shopping-list/bulk", json={"items": [{"text": x["text"], "qty": x["qty"], "unit": x["unit"],
                                                               "group_key": x["group_key"]} for x in due]}).json()
    assert r["added"] == 1 and r["items"][0]["group_key"] == "sut|1l" and r["items"][0]["qty"] == 2
    # listede olan önerilmez
    assert client.get("/api/shopping-list/due").json()["items"] == []
    assert "Süt" not in [x["text"] for x in client.get("/api/shopping-list/frequent").json()["items"]]


# ---------------------------------------------------------------- plan kaydı, tamamla, geçmiş

def _seed(client):
    _official([("BİM", "elma|kg", 40), ("Migros", "elma|kg", 50), ("A101", "elma|kg", 60),
               ("BİM", "sut|1l", 30), ("Migros", "sut|1l", 34), ("A101", "sut|1l", 36),
               ("BİM", "ekmek|250g", 10), ("Migros", "ekmek|250g", 12), ("A101", "ekmek|250g", 14)])
    return {t: client.post("/api/shopping-list", json={"text": t}).json()["id"]
            for t in ("2 kg elma", "Süt", "Ekmek 250 g", "Örnek Şey")}


def test_save_plan_complete_reuse(client, resmi):
    ids = _seed(client)
    p = client.put("/api/shopping-list/saved-plan", json={}).json()
    assert p["k"] == 1 and [c["chain"] for c in p["chains"]] == ["BİM"] and p["total"] == 120.0  # 80 + 30 + 10
    lst = client.get("/api/shopping-list").json()
    assert lst["saved_plan"]["chains"][0]["item_ids"] == [ids["2 kg elma"], ids["Süt"], ids["Ekmek 250 g"]]
    by = {i["id"]: i for i in lst["items"]}
    assert by[ids["2 kg elma"]]["planned"] == {"chain": "BİM", "unit_price": 40, "line_total": 80.0}
    assert by[ids["Örnek Şey"]]["planned"] is None
    client.patch(f"/api/shopping-list/{ids['2 kg elma']}", json={"qty": 3})  # adet değişince plan satırı da
    assert client.get("/api/shopping-list").json()["saved_plan"]["total"] == 160.0
    assert client.post("/api/shopping-list/complete", json={}).status_code == 422  # alınan yok
    for k in ("2 kg elma", "Süt"):
        client.patch(f"/api/shopping-list/{ids[k]}", json={"done": True})
    trip = client.post("/api/shopping-list/complete", json={"carry_over": True}).json()
    assert trip["planned_total"] == 150.0 and trip["chains"] == ["BİM"] and trip["carried"] == 2
    assert (trip["bought_count"], trip["item_count"], trip["plan_saved"], trip["saving"]) == (2, 2, True, None)
    left = client.get("/api/shopping-list").json()
    assert sorted(i["text"] for i in left["items"]) == ["Ekmek 250 g", "Örnek Şey"] and left["saved_plan"] is None
    assert [t["id"] for t in client.get("/api/shopping-trips").json()["trips"]] == [trip["id"]]
    d = client.get(f"/api/shopping-trips/{trip['id']}").json()
    assert {i["text"]: (i["qty"], i["unit"], i["planned_chain"]) for i in d["items"]} == {
        "elma": (3, "kg", "BİM"), "Süt": (1, "adet", "BİM")}
    # alınmayanlar taşınmadan tamamla: alışverişe "alınmadı" yazılır, liste boşalır
    client.patch(f"/api/shopping-list/{ids['Ekmek 250 g']}", json={"done": True})
    t2 = client.post("/api/shopping-list/complete", json={"carry_over": False}).json()
    assert (t2["bought_count"], t2["item_count"], t2["carried"]) == (1, 2, 0)
    assert t2["planned_total"] == 10.0  # plan yoksa tamamlarken en ucuz fiyat
    assert client.get("/api/shopping-list").json()["items"] == []
    # tekrar kullan: listede olan atlanır
    client.post("/api/shopping-list", json={"text": "süt"})
    r = client.post(f"/api/shopping-trips/{trip['id']}/reuse", json={}).json()
    assert (r["added"], r["skipped"]) == (1, 1) and r["items"][0]["unit"] == "kg" and r["items"][0]["qty"] == 3
    assert client.get("/api/shopping-trips/999").status_code == 404
    assert client.delete(f"/api/shopping-trips/{t2['id']}").json() == {"deleted": True}


# ---------------------------------------------------------------- fişi bağla ve tasarruf

def test_receipt_auto_link_and_saving(client, resmi):
    ids = _seed(client)
    client.put("/api/shopping-list/saved-plan", json={})  # BİM
    r = _receipt(client, "BİM", [("Elma", 2, 76, "kg"), ("Pınar Süt 1L", 1, 29), ("Ekmek 250 G", 1, 10),
                                 ("Poşet", 1, 0.5)])
    link = r["shopping_link"]
    assert (link["status"], link["target"], link["count"]) == ("linked", "list", 3)
    by = {i["id"]: i for i in client.get("/api/shopping-list").json()["items"]}
    assert by[ids["2 kg elma"]]["done"] and by[ids["2 kg elma"]]["actual"] == {"unit_price": 38.0, "line_total": 76.0,
                                                                              "entry_id": r["id"]}
    assert not by[ids["Örnek Şey"]]["done"]
    # piyasa ortancası (BİM 40 · Migros 50 · A101 60 → 50) × 2 kg = 100; süt 34 − 29; ekmek 12 − 10 → 24 + 5 + 2
    assert link["saving"] == 31.0 and link["actual_total"] == 115.0 and link["market_total"] == 146.0
    trip = client.post("/api/shopping-list/complete", json={"carry_over": False}).json()
    assert (trip["saving"], trip["actual_total"], trip["planned_total"], trip["linked"]) == (31.0, 115.0, 120.0, True)
    assert trip["planned_matched"] == 120.0 and trip["actual_matched"] == 115.0
    elma = next(i for i in client.get(f"/api/shopping-trips/{trip['id']}").json()["items"] if i["text"] == "elma")
    assert (elma["market_unit"], elma["market_line"], elma["actual_line"]) == (50.0, 100.0, 76.0)
    s = client.get("/api/shopping-trips/savings").json()
    assert (s["month"], s["year"], s["month_trips"]) == (31.0, 31.0, 1)
    # aynı fiş ikinci kez sorulmaz
    assert client.get(f"/api/shopping-list/receipt-match?entry_id={r['id']}").json() is None


def test_receipt_ask_then_link_and_dismiss(client, resmi):
    ids = _seed(client)
    client.put("/api/shopping-list/saved-plan", json={})  # BİM
    r = _receipt(client, "Migros", [("Elma", 2, 100, "kg"), ("Süt 1 L", 1, 34)])  # plan dışı zincir, 2 eşleşme: sor
    assert r["shopping_link"]["status"] == "ask" and r["shopping_link"]["count"] == 2
    assert not r["shopping_link"]["confident"] and not r["shopping_link"]["in_plan"]
    assert sorted(r["shopping_link"]["names"]) == ["Süt", "elma"]
    pend = client.get("/api/shopping-list/receipt-match").json()
    assert pend["entry_id"] == r["id"] and pend["target"] == "list"
    got = client.post("/api/shopping-list/link-receipt", json={"entry_id": r["id"], "target": "list"}).json()
    assert got["linked"] and got["count"] == 2
    assert client.get(f"/api/shopping-list/{ids['Süt']}/sizes").status_code == 200
    # tek eşleşme: aday değil
    assert _receipt(client, "A101", [("Ekmek 250 g", 1, 14), ("Örnekmarka Kalem", 1, 20)])["shopping_link"] is None
    # "hayır": bir daha sorulmaz
    r2 = _receipt(client, "A101", [("Ekmek 250 g", 1, 14), ("Örnek Şey", 1, 5), ("Deterjan", 1, 99)])
    assert r2["shopping_link"]["status"] == "ask"
    client.post("/api/shopping-list/receipt-dismiss", json={"entry_id": r2["id"]})
    assert client.get("/api/shopping-list/receipt-match").json() is None


def test_receipt_links_to_completed_trip(client, resmi):
    ids = _seed(client)
    client.put("/api/shopping-list/saved-plan", json={})
    for k in ("2 kg elma", "Süt", "Ekmek 250 g"):
        client.patch(f"/api/shopping-list/{ids[k]}", json={"done": True})
    trip = client.post("/api/shopping-list/complete", json={"carry_over": False}).json()
    assert client.get("/api/shopping-list").json()["items"] == []
    r = _receipt(client, "BİM", [("Elma", 2, 80, "kg"), ("Süt 1L", 1, 30), ("Ekmek 250g", 1, 10)])
    assert (r["shopping_link"]["status"], r["shopping_link"]["target"], r["shopping_link"]["trip_id"]) == \
        ("linked", "trip", trip["id"])
    t = client.get(f"/api/shopping-trips/{trip['id']}").json()
    assert (t["actual_total"], t["saving"], t["entry_ids"]) == (120.0, 26.0, [r["id"]])  # 20 + 4 + 2
    assert client.get("/api/shopping-trips/savings").json()["month"] == 26.0


def test_old_receipt_not_matched_to_list(client, resmi):
    _seed(client)
    old = (date.today() - timedelta(days=5)).isoformat()
    assert _receipt(client, "BİM", [("Elma", 2, 80, "kg"), ("Süt 1L", 1, 30)], old)["shopping_link"] is None


def test_trips_shared_and_gated(owner):  # noqa: F811
    owner.post("/api/shopping-list", json={"text": "Süt"})
    it = owner.get("/api/shopping-list").json()["items"][0]
    owner.patch(f"/api/shopping-list/{it['id']}", json={"done": True})
    trip = owner.post("/api/shopping-list/complete", json={}).json()
    editor = _member(owner, "esim3", "editor")
    assert [t["id"] for t in editor.get("/api/shopping-trips").json()["trips"]] == [trip["id"]]
    assert editor.post(f"/api/shopping-trips/{trip['id']}/reuse", json={}).json()["added"] == 1
    assert editor.post("/api/shopping-list/complete", json={}).status_code == 422  # yazabilir; alınan yok
    assert editor.delete(f"/api/shopping-trips/{trip['id']}").status_code == 403  # silmek sahibin
    viewer = _member(owner, "goz3", "viewer")
    assert viewer.get(f"/api/shopping-trips/{trip['id']}").status_code == 200
    assert viewer.post(f"/api/shopping-trips/{trip['id']}/reuse", json={}).status_code == 403


def test_trips_premium_gate():
    from app import plans

    assert plans.gate_for("/api/shopping-trips/savings") == "alisveris_listesi"
    assert plans.gate_for("/api/shopping-list/due") == "alisveris_listesi"
