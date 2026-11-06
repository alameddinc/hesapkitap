"""Alışveriş listesi: ekle / işaretle / sil, ürün grubu, plan (en ucuz zincir ve tek yerden toplam), ortak defter yetkisi."""
from datetime import date

from app import community_prices as cp
from tests.test_book import _member, owner  # noqa: F401  (fixture)

TODAY = date.today().isoformat()


def _buy(client, store, name, price):
    client.post("/api/entries", json={"entry_date": TODAY, "description": store, "amount": price, "kind": "gider",
                                      "category": "Market & Gıda", "payment": "nakit",
                                      "items": [{"name": name, "qty": 1, "amount": price}]})


def test_crud_and_groups(client):
    a = client.post("/api/shopping-list", json={"text": "  süt 1 lt ", "qty": 2}).json()
    assert (a["text"], a["qty"], a["done"], a["group"]) == ("süt 1 lt", 2, False, {"key": "sut|1l", "label": "Süt · 1 L"})
    b = client.post("/api/shopping-list", json={"text": "ekmek"}).json()
    assert client.post("/api/shopping-list", json={"text": ""}).status_code == 422
    assert client.post("/api/shopping-list", json={"text": "su", "qty": 0}).status_code == 422
    p = client.patch(f"/api/shopping-list/{b['id']}", json={"done": True}).json()
    assert p["done"] is True and p["done_at"]
    assert [i["id"] for i in client.get("/api/shopping-list").json()["items"]] == [a["id"], b["id"]]  # alınmış sonda
    assert client.patch(f"/api/shopping-list/{a['id']}", json={"text": "Yumurta 10'lu"}).json()["group"]["key"] == \
        "yumurta|10ad"
    assert client.patch("/api/shopping-list/999", json={"done": True}).status_code == 404
    assert client.delete("/api/shopping-list").json() == {"deleted": 1}
    assert client.delete(f"/api/shopping-list/{a['id']}").json() == {"deleted": True}
    assert client.get("/api/shopping-list").json()["items"] == []


def test_plan_cheapest_and_one_stop(client):
    _buy(client, "A101", "Torku Süt 1L", 36)
    _buy(client, "BİM", "Pınar Süt 1L", 38)
    _buy(client, "BİM", "Ekmek", 12)
    for uid, p in ((1, 14), (2, 15), (3, 16)):
        cp.contribute(uid, "A101", None, [{"name": "Ekmek", "qty": 1, "amount": p}], TODAY)
    milk = client.post("/api/shopping-list", json={"text": "Süt 1 L", "qty": 2}).json()
    bread = client.post("/api/shopping-list", json={"text": "Ekmek"}).json()
    odd = client.post("/api/shopping-list", json={"text": "Örnek Şey"}).json()
    done = client.post("/api/shopping-list", json={"text": "Su"}).json()
    client.patch(f"/api/shopping-list/{done['id']}", json={"done": True})
    plan = client.get("/api/shopping-list/plan").json()
    by = {r["id"]: r for r in plan["items"]}
    assert set(by) == {milk["id"], bread["id"], odd["id"]}                     # alınmış satır plana girmez
    assert (by[milk["id"]]["best"]["chain"], by[milk["id"]]["best"]["price"]) == ("A101", 36.0)
    assert (by[bread["id"]]["best"]["chain"], by[bread["id"]]["best"]["source"]) == ("BİM", "own")
    assert by[odd["id"]]["best"] is None and plan["unpriced_ids"] == [odd["id"]]
    assert plan["chains"] == [
        {"chain": "A101", "total": 87.0, "covered": 2, "missing": 1, "missing_ids": [odd["id"]]},
        {"chain": "BİM", "total": 88.0, "covered": 2, "missing": 1, "missing_ids": [odd["id"]]}]
    assert plan["split_total"] == 84.0 and plan["priced"] == 2                  # 2 × 36 + 12


def test_shared_book_roles(owner):  # noqa: F811
    item = owner.post("/api/shopping-list", json={"text": "Süt"}).json()
    editor = _member(owner, "esim", "editor")
    assert editor.post("/api/shopping-list", json={"text": "Ekmek"}).status_code == 200
    assert editor.patch(f"/api/shopping-list/{item['id']}", json={"done": True}).status_code == 200
    viewer = _member(owner, "cocuk", "viewer")
    got = viewer.get("/api/shopping-list").json()["items"]
    assert [i["text"] for i in got] == ["Ekmek", "Süt"] and got[0]["added_by_name"] == "esim"
    assert viewer.post("/api/shopping-list", json={"text": "Su"}).status_code == 403
    assert viewer.delete(f"/api/shopping-list/{item['id']}").status_code == 403


def test_premium_row_is_retierable():
    from app import plans

    assert plans.gate_for("/api/shopping-list") == plans.gate_for("/api/shopping-list/plan") == "alisveris_listesi"
    row = next(r for r in plans.table() if r["key"] == "alisveris_listesi")
    assert (row["label"], row["kind"], row["tier"], row["editable"]) == ("Alışveriş listesi", "sayfa", "premium", True)
