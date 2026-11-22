"""Varlıklarım (app/assets.py): kural → vade üretimi, muayene periyotları, uçlar, ödendi / atla / geri al, elden çıkarma,
yumuşak silme, Planlama ve harcanabilir, push aşamaları, Yapılacaklar, paket kilidi ve ortak defter yetkileri.
Varlık adları, plakalar ve tutarlar uydurmadır."""
from datetime import date, timedelta

import pytest

from app import assets, push
from tests.test_book import _member, owner  # noqa: F401 - fixture

ON = date(2026, 11, 22)
CAR = {"kind": "arac", "name": "Örnek Araba", "plate": "06 abc 123", "vehicle_class": "otomobil", "model_year": 2022,
       "fuel": "benzin"}


def _add(c, body=None, obligations=()):
    r = c.post("/api/assets", json={**(body or CAR), "obligations": list(obligations)})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- kurallar

def test_inspection_periods_follow_regulation():
    assert assets.inspection_period("otomobil", False) == (36, 24)   # hususi otomobil: 3 yaş, sonra 2 yılda bir
    assert assets.inspection_period("motosiklet", False) == (36, 24)  # iki/üç tekerlekli
    assert assets.inspection_period("otomobil", True) == (12, 12)    # ticari otomobil: diğer motorlu araçlar
    assert assets.inspection_period("kamyonet", False) == (12, 12)
    est = assets.estimate_inspection({"model_year": 2022, "vehicle_class": "otomobil"}, ON)
    assert est == date(2027, 7, 1)  # 2022-07-01 + 3 yıl = 2025-07-01 geçti → +2 yıl


def test_plate_normalize_and_validation():
    assert assets.normalize_plate("34 abc 12") == "34ABC12" and assets.plate_ok("34ABC12")
    assert not assets.plate_ok("99ABC123")
    with pytest.raises(ValueError):
        assets._clean_asset({"kind": "arac", "name": "x", "plate": "ZZ 1"})
    assert assets._clean_asset({"kind": "arac", "name": "x", "plate": "ZZ 1", "foreign_plate": True})["plate"] == "ZZ1"


def test_fixed_dates_split_yearly_amount():
    o = {"kind": "mtv", "rule": "{}", "amount": 5000.0}
    got = assets.occurrences(o, {}, date(2027, 1, 1), date(2027, 12, 31))
    assert [(s, d.isoformat(), a) for s, d, a, _ in got] == [("2027-1", "2027-01-31", 2500.0), ("2027-2", "2027-07-31", 2500.0)]
    emlak = assets.occurrences({"kind": "emlak", "rule": "{}", "amount": None}, {}, date(2027, 1, 1), date(2027, 12, 31))
    assert [d.isoformat() for _, d, _, _ in emlak] == ["2027-05-31", "2027-11-30"]


# ---------------------------------------------------------------- uçlar

def test_create_car_with_obligations_generates_dues(client):
    end = (date.today() + timedelta(days=40)).isoformat()
    a = _add(client, obligations=[{"kind": "mtv", "amount": 6000}, {"kind": "trafik", "start": end, "amount": 3000},
                                  {"kind": "muayene"}])
    assert a["plate"] == "06ABC123" and a["can_edit"] and len(a["obligations"]) == 3
    kinds = {d["kind"] for d in a["dues"]}
    assert kinds == {"mtv", "trafik", "muayene"}
    mtv = [d for d in a["dues"] if d["kind"] == "mtv"]
    assert all(d["amount"] == 3000 for d in mtv) and mtv[0]["late_text"]
    mu = next(d for d in a["dues"] if d["kind"] == "muayene")
    assert mu["estimated"] and a["needs_review"] == ["muayene"]
    tr = next(d for d in a["dues"] if d["kind"] == "trafik")
    assert tr["due_date"] == end and tr["asks_end_date"]
    out = client.get("/api/assets").json()
    assert [x["id"] for x in out["items"]] == [a["id"]] and out["items"][0]["next_due"]
    assert out["locked"] is False and out["can_add"]
    cal = client.get("/api/asset-dues").json()["items"]
    assert cal and all(x["asset_name"] == "Örnek Araba" for x in cal)


def test_validation_errors(client):
    assert client.post("/api/assets", json={**CAR, "plate": "99 X 1"}).status_code == 422
    assert client.post("/api/assets", json={**CAR, "obligations": [{"kind": "trafik"}]}).status_code == 422  # tarih gerekli
    assert client.post("/api/assets", json={**CAR, "obligations": [{"kind": "yok"}]}).status_code == 422


def test_pay_skip_unpay_and_policy_renewal(client):
    end = date.today() + timedelta(days=10)
    a = _add(client, obligations=[{"kind": "kasko", "start": end.isoformat(), "amount": 9000}])
    d = a["dues"][0]
    nxt = end + timedelta(days=380)
    r = client.post(f"/api/asset-dues/{d['id']}/pay", json={"amount": 9500, "next_end_date": nxt.isoformat()})
    assert r.status_code == 200 and r.json()["status"] == "odendi" and r.json()["paid_amount"] == 9500
    a2 = client.get(f"/api/assets/{a['id']}").json()
    assert nxt.isoformat() in [x["due_date"] for x in a2["dues"]] + [x["due_date"] for x in client.get(
        "/api/asset-dues", params={"to": (nxt + timedelta(days=1)).isoformat()}).json()["items"]]
    assert [h["id"] for h in a2["history"]] == [d["id"]]
    assert client.post(f"/api/asset-dues/{d['id']}/unpay").json()["status"] in ("bekliyor", "gecikti")
    assert client.post(f"/api/asset-dues/{d['id']}/skip").json()["status"] == "atlandi"
    assert client.patch(f"/api/asset-dues/{d['id']}", json={"amount": 100}).json()["amount"] == 100


def test_edit_rule_regenerates_future_only(client):
    a = _add(client, obligations=[{"kind": "mtv", "amount": 4000}])
    oid = a["obligations"][0]["id"]
    first = a["dues"][0]
    client.post(f"/api/asset-dues/{first['id']}/pay", json={})
    a2 = client.patch(f"/api/assets/{a['id']}/obligations/{oid}", json={"amount": 8000}).json()
    assert client.get(f"/api/asset-dues/{first['id']}").json()["amount"] == 2000  # ödenen: dokunulmadı
    assert {d["amount"] for d in a2["dues"] if d["status"] == "bekliyor"} == {4000}  # yıllık 8000 → taksit 4000


def test_commercial_change_asks_review_when_inspection_from_registration(client):
    a = _add(client, obligations=[{"kind": "muayene", "start": (date.today() + timedelta(days=200)).isoformat()}])
    r = client.patch(f"/api/assets/{a['id']}", json={"commercial": True}).json()
    assert r["needs_review"] == ["muayene"] and r["commercial"] is True


def test_dispose_undispose_delete_restore(client):
    a = _add(client, obligations=[{"kind": "mtv", "amount": 2000}])
    n = len(a["dues"])
    d = client.post(f"/api/assets/{a['id']}/dispose", json={"on": date.today().isoformat()}).json()
    assert d["disposed_on"] == date.today().isoformat() and not [x for x in d["dues"] if x["status"] == "bekliyor"]
    assert client.get("/api/assets").json()["items"] == []
    assert client.get("/api/assets", params={"include_disposed": True}).json()["items"][0]["id"] == a["id"]
    u = client.post(f"/api/assets/{a['id']}/undispose").json()
    assert u["disposed_on"] is None and len([x for x in u["dues"] if x["status"] == "bekliyor"]) == n
    assert client.delete(f"/api/assets/{a['id']}").json()["undo"].endswith("/restore")
    assert client.get(f"/api/assets/{a['id']}").status_code == 404
    assert client.get("/api/asset-dues").json()["items"] == []
    assert client.post(f"/api/assets/{a['id']}/restore").status_code == 200


def test_home_suggestions_by_tenure(client):
    s = client.get("/api/assets/suggest", params={"kind": "konut", "tenure": "kiraya_verdim"}).json()
    on = [x["kind"] for x in s["items"] if x["checked"]]
    assert on == ["emlak", "dask", "kira_beyan"]
    s = client.get("/api/assets/suggest", params={"kind": "konut", "tenure": "kiraci"}).json()
    assert "emlak" not in [x["kind"] for x in s["items"]]
    car = client.get("/api/assets/suggest", params={"kind": "arac", "model_year": 2024}).json()
    mu = next(x for x in car["items"] if x["kind"] == "muayene")
    assert mu["estimate"] and mu["every_months"] == 24


def test_mtv_hint_from_last_year_entry(client):
    y = date.today().year - 1
    client.post("/api/entries", json={"entry_date": f"{y}-01-20", "description": "GIB MTV ODEMESI", "amount": 1500,
                                      "kind": "gider", "category": "Ulaşım & Araç", "payment": "nakit"})
    car = client.get("/api/assets/suggest", params={"kind": "arac"}).json()
    assert next(x for x in car["items"] if x["kind"] == "mtv")["hint"] == {"year": y, "amount": 1500.0, "count": 1}


def test_files_are_stored_without_read_quota(client):
    a = _add(client)
    pdf = b"%PDF-1.4\n% ornek ruhsat\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"
    r = client.post(f"/api/assets/{a['id']}/files", files={"file": ("r.pdf", pdf, "application/pdf")},
                    data={"doc_type": "ruhsat"})
    assert r.status_code == 200, r.text
    f = r.json()["files"][0]
    assert f["doc_label"] == "Ruhsat" and f["pdf"]
    assert client.get(f"/api/assets/{a['id']}/files/{f['id']}").content.startswith(b"%PDF")
    assert client.delete(f"/api/assets/{a['id']}/files/{f['id']}").json()["files"] == []


# ---------------------------------------------------------------- Planlama, harcanabilir, Yapılacaklar

def test_plan_includes_pending_due_with_amount_and_warns_unknown(client):
    soon = date.today() + timedelta(days=3)
    _add(client, {"kind": "konut", "name": "Örnek Ev", "tenure": "sahibi"},
         [{"kind": "dask", "start": soon.isoformat(), "amount": 1200}, {"kind": "konut_sig", "start": soon.isoformat()}])
    p = client.get("/api/plan", params={"months": 2}).json()
    lines = [ln for m in p["months"] for ln in m["lines"] if ln["type"] == "varlik"]
    assert [ln["amount"] for ln in lines] == [1200]
    m = next(m for m in p["months"] if any(ln["type"] == "varlik" for ln in m["lines"]))
    assert m["parts"]["pending_assets"] == 1200
    assert [u["title"] for u in p["asset_unknown"]] == ["Konut sigortası · Örnek Ev"]
    # ödendi: Planlama'dan çıkar (çift sayım yok)
    did = lines[0]["due_id"]
    client.post(f"/api/asset-dues/{did}/pay", json={})
    p = client.get("/api/plan", params={"months": 2}).json()
    assert not [ln for m in p["months"] for ln in m["lines"] if ln["type"] == "varlik"]


def test_spendable_subtracts_due_in_horizon(client):
    from app import finance

    today = date.today()
    aid = assets.create({"kind": "arac", "name": "Örnek İş Aracı", "vehicle_class": "kamyonet"},
                        [{"kind": "trafik", "start": today.isoformat(), "amount": 2500}])
    assert aid
    due = finance._due_this_period(today, [])
    assert due["assets"]["amount"] == 2500 and due["assets"]["items"][0]["title"].startswith("Trafik sigortası")


def test_reminders_only_for_aware_client(client):
    _add(client, obligations=[{"kind": "trafik", "start": (date.today() + timedelta(days=2)).isoformat()}])
    assert not [r for r in client.get("/api/reminders").json() if r["kind"] == "varlik"]
    got = [r for r in client.get("/api/reminders", params={"assets": 1}).json() if r["kind"] == "varlik"]
    assert got and got[0]["action"] == "asset_due" and got[0]["due_id"]


# ---------------------------------------------------------------- push

def test_push_stages():
    assert assets.push_stage("varlik_vergi", "mtv", 14) == "14g"
    assert assets.push_stage("varlik_vergi", "mtv", 15) is None
    assert assets.push_stage("varlik_vergi", "mtv", 2) == "3g"
    assert assets.push_stage("varlik_vergi", "mtv", 0) == "0g"
    assert assets.push_stage("varlik_vergi", "mtv", -1) == "g0"
    assert assets.push_stage("varlik_vergi", "mtv", -31) == "g1"
    assert assets.push_stage("varlik_vergi", "mtv", -100) is None
    assert assets.push_stage("varlik_sigorta", "trafik", -1) is None
    assert assets.push_stage("varlik_muayene", "muayene", 20) == "30g"
    assert assets.push_stage("varlik_bakim", "kira_artis", 25) == "30g"
    assert assets.push_stage("varlik_bakim", "bakim", 25) is None


def test_push_items_have_no_plate(client, monkeypatch):
    monkeypatch.setattr(push, "has_app", lambda uid, v: True)
    _add(client, obligations=[{"kind": "trafik", "start": (date.today() + timedelta(days=7)).isoformat(), "amount": 1}])
    on = {k: True for k in push.TYPES}
    items = push._varlik({"id": 1}, date.today(), on)
    assert items and items[0]["kind"] == "varlik_sigorta" and items[0]["route"].startswith("#/diger/varliklar/vade/")
    assert "06ABC123" not in items[0]["title"] + items[0]["body"] and "Örnek Araba" in items[0]["title"]
    assert len(items[0]["title"]) <= 40
    on["varlik_sigorta"] = False
    assert push._varlik({"id": 1}, date.today(), on) == []


# ---------------------------------------------------------------- paket ve ortak defter

def test_free_plan_locked_writes_premium_opens(client, monkeypatch):
    from tests.test_debt_jar import _hosted_user
    from tests.test_plans import _rc

    c, u = _hosted_user(client, monkeypatch)
    out = c.get("/api/assets").json()
    assert out["locked"] and out["need"] == "Premium" and not out["can_add"]
    r = c.post("/api/assets", json={**CAR, "obligations": []})
    assert r.status_code == 402 and r.json()["plan_required"] == "varliklar"
    assert c.get("/api/meta").json()["plan"]["tiers"]["varliklar"] == "premium"
    assert _rc(client, "INITIAL_PURCHASE", u["id"]).status_code == 200
    a = c.post("/api/assets", json={**CAR, "obligations": [{"kind": "mtv", "amount": 100}]})
    assert a.status_code == 200 and not c.get("/api/assets").json()["locked"]


def test_member_permissions(owner):
    a = _add(owner, obligations=[{"kind": "mtv", "amount": 1000}])
    ed = _member(owner, "esim", "editor")
    assert ed.get("/api/assets").json()["items"][0]["can_edit"] is False
    assert ed.patch(f"/api/assets/{a['id']}", json={"name": "Başka"}).status_code == 403
    did = a["dues"][0]["id"]
    assert ed.post(f"/api/asset-dues/{did}/pay", json={}).status_code == 200  # ödendi: her ortak vade
    mine = ed.post("/api/assets", json={"kind": "konut", "name": "Örnek Daire", "tenure": "kiraci", "obligations": []})
    assert mine.status_code == 200 and mine.json()["can_edit"]
    vw = _member(owner, "goren", "viewer")
    assert vw.get("/api/assets").json()["can_add"] is False
    assert vw.post(f"/api/asset-dues/{did}/unpay").status_code == 403
    assert vw.post("/api/assets", json={**CAR, "obligations": []}).status_code == 403


def test_capability(client):
    assert "assets" in client.get("/api/meta").json()["capabilities"]
