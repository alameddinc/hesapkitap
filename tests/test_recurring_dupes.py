"""Aynı olabilecek iki sabit kalem: öneri (Yapılacaklar + GET /api/recurring/duplicates), birleştir + geri al, ayrı kalsın.

Uydurma veri: "Kira" (ödemeye hiç bağlanmamış) ve "Kira (Ev Sahibi)" (her ay havaleye bağlı). Asla kendiliğinden
birleşmez; iki ayrı kira ödeyen de var.
"""
from datetime import date

from app import finance, recurring_dupes
from tests.test_book import _member, owner  # noqa: F401 - fixture

TODAY = date.today()
CUR = f"{TODAY:%Y-%m}"  # ay dönümü 1 (varsayılan): dönem = takvim ayı


def _m(n: int) -> str:
    return finance.add_months(CUR, n)


def _rec(client, desc, amount=35000, cat=None, start=None, kind="gider", day=5):
    return client.post("/api/recurring", json={"description": desc, "amount": amount, "kind": kind, "category": cat,
                                               "day_of_month": day, "start_month": start or _m(-12)}).json()["id"]


def _pay(client, month, amount=35000, who="Ev Sahibi", desc="Kira (Ev Sahibi)", day=5, account_id=None):
    body = {"entry_date": f"{month}-{day:02d}", "description": desc, "amount": amount, "kind": "gider",
            "category": "Kira & Aidat", "payment": "havale", "counterparty": who}
    if account_id:
        body["account_id"] = account_id
    return client.post("/api/entries", json=body).json()["id"]


def _linked(tmpdb, rid):
    return sorted(e["month"] for e in tmpdb.list_entries() if e["recurring_id"] == rid and not e["skipped"])


def _setup(client, tmpdb, months=(-3, -2, -1)):
    a = _rec(client, "Kira")                                     # kategorisiz, hiç bağlanmamış
    b = _rec(client, "Kira (Ev Sahibi)", cat="Kira & Aidat")     # her ay havaleye bağlı (aynı ad)
    for n in months:
        _pay(client, _m(n))
    assert _linked(tmpdb, a) == [] and len(_linked(tmpdb, b)) == len(months)
    return a, b


def test_unmatched_duplicate_is_suggested_with_text(client, tmpdb):
    a, b = _setup(client, tmpdb)
    ps = client.get("/api/recurring/duplicates").json()
    assert len(ps) == 1
    p = ps[0]
    assert p["id"] == f"sabit:{a}:{b}" and p["reason"] == "bagli_degil"
    assert p["keep"]["id"] == b and p["drop"]["id"] == a and p["keep"]["linked"] == 3
    assert p["title"] == "İki sabit kalem aynı olabilir: “Kira” ve “Kira (Ev Sahibi)”"
    assert p["text"] == "₺35.000 · Birini ödemeye bağlayamadık, Planlama'da iki kez sayılıyor olabilir."
    r = [x for x in client.get("/api/reminders").json() if x["kind"] == "sabit_tekrar"]
    assert len(r) == 1 and r[0]["id"] == p["id"] and r[0]["title"] == p["title"] and r[0]["text"] == p["text"]
    assert (r[0]["keep_id"], r[0]["drop_id"], r[0]["cta"], r[0]["action"]) == (b, a, "Birleştir", "recurring_merge")
    assert "recurring_duplicates" in client.get("/api/meta").json()["capabilities"]


def test_two_real_rents_with_separate_payments_not_suggested(client, tmpdb):
    a = _rec(client, "Kira")
    b = _rec(client, "Kira (Ev Sahibi)", cat="Kira & Aidat")
    for n in (-3, -2, -1):
        _pay(client, _m(n))                                                # → b
        _pay(client, _m(n), amount=34000, who="Öbür Ev", desc="Kira", day=12)  # → a (ayrı ödeme)
    assert len(_linked(tmpdb, a)) == 3 and len(_linked(tmpdb, b)) == 3
    assert recurring_dupes.find() == []
    assert not [x for x in client.get("/api/reminders").json() if x["kind"] == "sabit_tekrar"]


def test_negatives_amount_name_category_kind(client, tmpdb):
    for n in (-3, -2, -1):
        _pay(client, _m(n))
    _rec(client, "Kira (Ev Sahibi)", cat="Kira & Aidat")
    _rec(client, "Kira", amount=20000)                         # tutar çok farklı
    _rec(client, "Spor salonu", amount=35000)                   # ad benzemiyor
    _rec(client, "Kira geliri", kind="gelir")                   # yön farklı
    _rec(client, "Kira deposu", cat="Market & Gıda")            # kategori farklı
    assert recurring_dupes.find() == []


def test_one_month_of_evidence_is_not_enough(client, tmpdb):
    _setup(client, tmpdb, months=(-1,))
    assert recurring_dupes.find() == []


def test_ended_item_is_not_suggested(client, tmpdb):
    a, b = _setup(client, tmpdb)
    client.patch(f"/api/recurring/{a}", json={"end_month": _m(-1)})
    assert recurring_dupes.find() == []


def test_keep_separate_is_permanent(client, tmpdb):
    a, b = _setup(client, tmpdb)
    assert client.post("/api/recurring/duplicates/separate", json={"a": b, "b": a}).json()["ok"]
    assert client.get("/api/recurring/duplicates").json() == []
    assert not [x for x in client.get("/api/reminders").json() if x["kind"] == "sabit_tekrar"]
    _pay(client, CUR)
    assert recurring_dupes.find() == []
    assert client.post("/api/recurring/duplicates/separate", json={"a": a, "b": 99999}).status_code == 404


def test_merge_moves_links_and_overrides_then_undo(client, tmpdb):
    a, b = _setup(client, tmpdb)
    # bağlanmamış kalemin atlaması, aya özel tutarı ve eski bir ödemesi (pencere dışında)
    client.post(f"/api/recurring/{a}/skip", json={"month": _m(-6)})
    client.put(f"/api/recurring/{a}/months/{_m(2)}", json={"amount": 36000})
    client.put(f"/api/recurring/{b}/months/{_m(3)}", json={"amount": 37000})
    client.put(f"/api/recurring/{a}/months/{_m(3)}", json={"amount": 99999})  # keep'te zaten var: taşınmaz
    old = client.post(f"/api/recurring/{a}/confirm", json={"month": _m(-8)}).json()["id"]
    pend_before = finance.plan(6)["months"][0]
    assert client.get("/api/recurring/duplicates").json()  # pencere dışı eski ödeme öneriyi bozmaz

    assert client.post("/api/recurring/duplicates/merge", json={"keep": a, "drop": b}).status_code == 409  # ters yön
    r = client.post("/api/recurring/duplicates/merge", json={"keep": b, "drop": a}).json()
    assert r["ok"] and r["undo_id"] and r["kept"] == b and r["removed"] == a and r["moved"] == 1 and r["skips_removed"] == 1
    assert [x["id"] for x in tmpdb.list_recurring()] == [b]
    rec = tmpdb.list_recurring()[0]
    assert rec["category"] == "Kira & Aidat"
    assert next(e for e in tmpdb.list_entries() if e["id"] == old)["recurring_id"] == b
    ov = tmpdb.recurring_overrides()
    assert ov[(b, _m(2))] == 36000 and ov[(b, _m(3))] == 37000
    assert client.get("/api/recurring/duplicates").json() == []
    assert not [x for x in client.get("/api/reminders").json() if x["kind"] == "sabit_tekrar"]
    pend_after = finance.plan(6)["months"][0]
    assert pend_after["expense"] < pend_before["expense"]  # kira artık bir kez

    u = client.post(f"/api/imports/{r['undo_id']}/undo", params={"force": True}).json()
    assert u["ok"]
    assert sorted(x["id"] for x in tmpdb.list_recurring()) == sorted([a, b])
    assert next(e for e in tmpdb.list_entries() if e["id"] == old)["recurring_id"] == a
    assert tmpdb.recurring_overrides()[(a, _m(2))] == 36000
    assert [p["id"] for p in client.get("/api/recurring/duplicates").json()] == [f"sabit:{a}:{b}"]


def test_both_linked_to_same_payments_suggested(client, tmpdb):
    a = _rec(client, "Kira")
    b = _rec(client, "Kira (Ev Sahibi)", cat="Kira & Aidat")
    for n in (-3, -2):
        _pay(client, _m(n))                                                     # → b (dökümden gibi)
        client.post(f"/api/recurring/{a}/confirm", json={"month": _m(n)})       # aynı ödeme a'ya da elle onaylanmış
    assert len(_linked(tmpdb, a)) == 2 and len(_linked(tmpdb, b)) == 2
    ps = recurring_dupes.find()
    assert len(ps) == 1 and ps[0]["reason"] == "ayni_odeme" and ps[0]["keep"]["id"] == b
    assert ps[0]["text"].endswith("İkisine de aynı ödemeler bağlanmış, Planlama'da iki kez sayılıyor olabilir.")


def test_three_rents_one_unmatched_gives_single_suggestion(client, tmpdb):
    a = _rec(client, "Kira")
    b = _rec(client, "Kira (Ev Sahibi)", cat="Kira & Aidat")
    c = _rec(client, "Kira (Yazlık)", amount=34000, cat="Kira & Aidat")
    for n in (-3, -2, -1):
        _pay(client, _m(n))
        _pay(client, _m(n), amount=34000, who="Yazlık", desc="Kira (Yazlık)", day=20)
    ps = recurring_dupes.find()
    assert [(p["drop"]["id"], p["keep"]["id"]) for p in ps] == [(a, b)]  # b ↔ c ayrı ödemeler: önerilmez
    assert c not in {p["keep"]["id"] for p in ps}


def test_shared_book_member_rules(owner):
    a, b = _rec(owner, "Kira"), _rec(owner, "Kira (Ev Sahibi)", cat="Kira & Aidat")
    for n in (-3, -2, -1):
        _pay(owner, _m(n))
    assert [p["id"] for p in owner.get("/api/recurring/duplicates").json()] == [f"sabit:{a}:{b}"]
    full = _member(owner, name="esim", role="full")
    assert [r["id"] for r in full.get("/api/reminders").json() if r["kind"] == "sabit_tekrar"] == [f"sabit:{a}:{b}"]
    ed = _member(owner, name="kardes", role="editor")
    assert not [r for r in ed.get("/api/reminders").json() if r["kind"] == "sabit_tekrar"]
    assert ed.post("/api/recurring/duplicates/merge", json={"keep": b, "drop": a}).status_code == 403
    assert ed.post("/api/recurring/duplicates/separate", json={"a": a, "b": b}).status_code == 403
    v = _member(owner, name="cocuk", role="viewer")
    assert not [r for r in v.get("/api/reminders").json() if r["kind"] == "sabit_tekrar"]
    # Planlama üyeye kapalıysa: ne öneri ne uç
    owner.put("/api/book/rules", json={"hidden_pages": ["plan"]})
    assert not [r for r in full.get("/api/reminders").json() if r["kind"] == "sabit_tekrar"]
    assert full.get("/api/recurring/duplicates").status_code == 403
    owner.put("/api/book/rules", json={"hidden_pages": []})
    r = full.post("/api/recurring/duplicates/merge", json={"keep": b, "drop": a})
    assert r.status_code == 200 and r.json()["removed"] == a


def test_member_does_not_see_pair_backed_by_hidden_payments(owner):
    owner.post("/api/accounts", json={"name": "Gizli Banka", "kind": "banka", "balance": 1000})
    from app import db

    acc = next(x["id"] for x in db.list_accounts() if x["name"] == "Gizli Banka")
    a, b = _rec(owner, "Kira"), _rec(owner, "Kira (Ev Sahibi)", cat="Kira & Aidat")
    for n in (-3, -2, -1):
        _pay(owner, _m(n), account_id=acc)
    full = _member(owner, name="esim", role="full")
    assert [p["id"] for p in owner.get("/api/recurring/duplicates").json()] == [f"sabit:{a}:{b}"]
    owner.put("/api/book/rules", json={"hidden_accounts": [acc]})
    assert full.get("/api/recurring/duplicates").json() == []
    assert not [r for r in full.get("/api/reminders").json() if r["kind"] == "sabit_tekrar"]


def test_name_similarity():
    s = recurring_dupes.similar_names
    assert s({"description": "Kira"}, {"description": "Kira (Ev Sahibi)"})
    assert s({"description": "Ev kirası", "category": None}, {"description": "Kira"})
    assert s({"description": "Aidat", "category": "Kira & Aidat"}, {"description": "Site aidatı", "category": "Kira & Aidat"})
    assert not s({"description": "Netflix"}, {"description": "Spotify"})
    assert not s({"description": "Kira"}, {"description": "Kreş"})
