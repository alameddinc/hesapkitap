"""Aile harcayanı atama (yetenek "spender_assign"): kartı kişiye bağlama (PUT /api/cards/{card_no}/spender) ve kayıtta
kim harcadı seçimi (PUT /api/spender). Kim ne harcadı, İşlemler ?who=, ortak bütçe kırılımı ve satırdaki spender aynı
kuraldan (finance.Spenders) geçer. Kişiler, kartlar ve tutarlar uydurma."""
from app import money
from tests.test_book import _member, _setup_book, owner  # noqa: F401 - fixture
from tests.test_plans import _user, hosted  # noqa: F401 - fixture

MONTH = "2026-10"


def _entry(c, desc, amount, category="Market & Gıda", day="2026-10-05", headers=None):
    r = c.post("/api/entries", json={"entry_date": day, "description": desc, "amount": amount, "kind": "gider",
                                     "category": category, "payment": "nakit"}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _uid(name):
    from app import system

    return system.user_by_name(name)["id"]


def _spend(c, month):
    out = c.get("/api/book/member-spend", params={"month": month}).json()
    return {p["key"]: p["amount"] for p in out["people"]}


def _rows(c, month, **params):
    return c.get("/api/transactions", params={"month": month, **params}).json()


def _card_with_rows(owner):
    """Kart ekstresinde gider satırı olan bir (kart numarası, dönem)."""
    r = next(r for r in owner.get("/api/transactions").json() if r["source"] == "card" and r["counts_as_spend"])
    return r["account"], r["month"]


def test_card_owner_moves_card_rows_from_ortak(owner):  # noqa: F811
    _setup_book(owner)
    _member(owner)
    esim = _uid("esim")
    card, cm = _card_with_rows(owner)
    card_rows = {r["uid"] for r in _rows(owner, cm) if r["source"] == "card" and r["account"] == card and r["counts_as_spend"]}
    assert card_rows and f"u{esim}" not in _spend(owner, cm)
    assert all(r["spender"] == "ortak" and r["spender_set"] is False for r in _rows(owner, cm) if r["uid"] in card_rows)

    r = owner.put(f"/api/cards/{card}/spender", json={"who": f"u{esim}"})
    assert r.status_code == 200 and r.json() == {"card_no": card, "who": f"u{esim}"}
    who = _rows(owner, cm, who=f"u{esim}")
    assert {x["uid"] for x in who} == card_rows  # yalnız o kartın satırları üyeye geçti
    assert money.eq(_spend(owner, cm)[f"u{esim}"], sum(x["amount"] for x in who))
    rows = {x["uid"]: x for x in _rows(owner, cm)}
    assert all(rows[u]["spender"] == f"u{esim}" and rows[u]["spender_set"] is False for u in card_rows)
    # Kartlar ekranı: kartın kişisi; bağlı olmayan kart null
    cards = {c["card_no"]: c for c in owner.get("/api/cards/summary").json()["cards"]}
    assert cards[card]["spender"] == f"u{esim}"
    assert all(c["spender"] is None for no, c in cards.items() if no != card)
    assert all(x["spender"] == f"u{esim}" for x in cards[card]["recent"] if x["source"] == "card")
    assert owner.get("/api/cards/spenders").json() == {"cards": {card: f"u{esim}"}}

    # null: bağ kalkar, satırlar yine Ortak
    assert owner.put(f"/api/cards/{card}/spender", json={"who": None}).json() == {"card_no": card, "who": None}
    assert f"u{esim}" not in _spend(owner, cm)
    assert owner.get("/api/cards/spenders").json() == {"cards": {}}
    assert owner.put("/api/cards/0000/spender", json={"who": f"u{esim}"}).status_code == 404


def test_record_override_beats_creator_and_card_owner(owner):  # noqa: F811
    _setup_book(owner)
    m = _member(owner)
    admin, esim = _uid("admin"), _uid("esim")
    eid = _entry(m, "Fırın", 120)
    assert _spend(owner, MONTH)[f"u{esim}"] == 120
    # elle kayıt: ekleyen üye, seçim sahibe çevirir
    r = owner.put("/api/spender", json={"source": "manual", "id": eid, "who": f"u{admin}"})
    assert r.status_code == 200 and r.json() == {"who": f"u{admin}", "set": True}
    assert f"u{esim}" not in _spend(owner, MONTH)
    row = next(x for x in _rows(owner, MONTH) if x["uid"] == f"m{eid}")
    assert row["spender"] == f"u{admin}" and row["spender_set"] is True
    # açıkça Ortak
    assert owner.put("/api/spender", json={"source": "manual", "id": eid, "who": "ortak"}).json() == {"who": "ortak", "set": True}
    assert any(x["uid"] == f"m{eid}" for x in _rows(owner, MONTH, who="ortak"))
    # null: otomatiğe döner (ekleyen)
    assert owner.put("/api/spender", json={"source": "manual", "id": eid, "who": None}).json() == {"who": f"u{esim}", "set": False}

    # kart işlemi: kartın kişisi üye, kayıtta seçim sahip
    card, cm = _card_with_rows(owner)
    owner.put(f"/api/cards/{card}/spender", json={"who": f"u{esim}"})
    txn = next(x for x in _rows(owner, cm) if x["source"] == "card" and x["account"] == card and x["counts_as_spend"])
    r = owner.put("/api/spender", json={"source": "card", "id": txn["id"], "who": f"u{admin}"})
    assert r.json() == {"who": f"u{admin}", "set": True}
    assert txn["uid"] in {x["uid"] for x in _rows(owner, cm, who=f"u{admin}")}
    assert txn["uid"] not in {x["uid"] for x in _rows(owner, cm, who=f"u{esim}")}
    assert owner.put("/api/spender", json={"source": "card", "id": txn["id"], "who": None}).json() == {"who": f"u{esim}", "set": False}
    # yok olan kayıt
    assert owner.put("/api/spender", json={"source": "manual", "id": 999999, "who": "ortak"}).status_code == 404
    assert owner.put("/api/spender", json={"source": "recurring", "id": 999999, "who": "ortak"}).status_code == 404


def test_linked_entry_override_carries_to_card_row(owner):  # noqa: F811
    from app import db

    _setup_book(owner)
    m = _member(owner)
    admin, esim = _uid("admin"), _uid("esim")
    card, cm = _card_with_rows(owner)
    txn = next(x for x in _rows(owner, cm) if x["source"] == "card" and x["account"] == card and x["counts_as_spend"]
               and not x.get("split_of"))
    eid = _entry(m, "Kartla market", txn["amount"], day=txn["date"][:10])
    with db.connect() as c:  # ekstreyle eşleşmiş elle kayıt (eşitleme bunu yapar)
        c.execute("UPDATE entries SET linked_txn_id = ?, payment = 'kredi_karti' WHERE id = ?", (txn["id"], eid))
    owner.put(f"/api/cards/{card}/spender", json={"who": f"u{admin}"})
    assert txn["uid"] in {x["uid"] for x in _rows(owner, cm, who=f"u{esim}")}  # eşleşen kaydı ekleyen kart sahibinden önce
    owner.put("/api/spender", json={"source": "manual", "id": eid, "who": "ortak"})  # elle kayıtta seçim kart satırına geçer
    assert txn["uid"] in {x["uid"] for x in _rows(owner, cm, who="ortak")}


def test_recurring_override_applies_every_month(owner):  # noqa: F811
    _member(owner)
    esim = _uid("esim")
    rid = owner.post("/api/recurring", json={"description": "Kira", "amount": 20000, "kind": "gider", "category": "Kira & Aidat",
                                             "day_of_month": 3, "start_month": "2026-09"}).json()["id"]
    for month in ("2026-09", "2026-10"):
        assert owner.post(f"/api/recurring/{rid}/confirm", json={"month": month}).status_code == 200
    assert all(f"u{esim}" not in _spend(owner, mo) for mo in ("2026-09", "2026-10"))  # ekleyeni yok: Ortak
    r = owner.put("/api/spender", json={"source": "recurring", "id": rid, "who": f"u{esim}"})
    assert r.json() == {"who": f"u{esim}", "set": True}
    for month in ("2026-09", "2026-10"):
        assert money.eq(_spend(owner, month)[f"u{esim}"], 20000), month
        [kira] = _rows(owner, month, who=f"u{esim}")
        assert kira["recurring_id"] == rid and kira["spender_set"] is False  # seçim kalemde, kayıtta değil
    pending = [x for x in _rows(owner, "2027-01") if x["source"] == "recurring" and x["id"] == rid]
    assert pending and pending[0]["spender"] == f"u{esim}" and pending[0]["spender_set"] is True
    # o ayın kaydında ayrı seçim kalemin seçiminden önce gelir
    [oct_kira] = _rows(owner, "2026-10", who=f"u{esim}")
    owner.put("/api/spender", json={"source": "manual", "id": oct_kira["id"], "who": "ortak"})
    assert f"u{esim}" not in _spend(owner, "2026-10") and money.eq(_spend(owner, "2026-09")[f"u{esim}"], 20000)
    # kalem silinince seçimi de gider
    from app import db

    db.delete_recurring(rid)
    assert ("recurring", rid) not in db.spender_overrides()


def test_removed_member_falls_back_to_ortak(owner):  # noqa: F811
    _setup_book(owner)
    _member(owner)
    k = _member(owner, name="kardes")
    kardes = _uid("kardes")
    eid = _entry(k, "Kırtasiye", 80, category="Eğitim")
    card, cm = _card_with_rows(owner)
    owner.put(f"/api/cards/{card}/spender", json={"who": f"u{kardes}"})
    assert f"u{kardes}" in _spend(owner, cm) and f"u{kardes}" in _spend(owner, MONTH)
    assert owner.delete(f"/api/book/members/{kardes}").status_code == 200
    assert f"u{kardes}" not in _spend(owner, cm) and f"u{kardes}" not in _spend(owner, MONTH)
    row = next(x for x in _rows(owner, MONTH) if x["uid"] == f"m{eid}")
    assert row["spender"] == "ortak"
    assert owner.get("/api/cards/spenders").json() == {"cards": {}}
    assert next(c for c in owner.get("/api/cards/summary").json()["cards"] if c["card_no"] == card)["spender"] is None
    assert owner.put(f"/api/cards/{card}/spender", json={"who": f"u{kardes}"}).status_code == 400
    assert owner.put("/api/spender", json={"source": "manual", "id": eid, "who": f"u{kardes}"}).status_code == 400


def test_invalid_who(owner):  # noqa: F811
    _member(owner)
    eid = _entry(owner, "Market", 300)
    for who in ("u999999", "x1", "U1", "u", "kimse"):
        assert owner.put("/api/spender", json={"source": "manual", "id": eid, "who": who}).status_code == 400, who
    assert owner.put("/api/spender", json={"source": "debt", "id": eid, "who": "ortak"}).status_code == 422


def test_member_roles_and_hidden_records(owner):  # noqa: F811
    sec, pub = _setup_book(owner)
    editor = _member(owner)
    viewer = _member(owner, name="kardes", role="viewer")
    admin, esim = _uid("admin"), _uid("esim")
    card, cm = _card_with_rows(owner)
    # sadece görür: yazamaz, okur
    assert viewer.put("/api/spender", json={"source": "manual", "id": pub, "who": "ortak"}).status_code == 403
    assert viewer.put(f"/api/cards/{card}/spender", json={"who": f"u{esim}"}).status_code == 403
    assert viewer.get("/api/cards/spenders").status_code == 200
    # görür ve ekler: yazar
    assert editor.put("/api/spender", json={"source": "manual", "id": pub, "who": f"u{esim}"}).json() == {"who": f"u{esim}", "set": True}
    assert editor.put(f"/api/cards/{card}/spender", json={"who": f"u{esim}"}).status_code == 200
    assert next(x for x in _rows(editor, MONTH) if x["uid"] == f"m{pub}")["spender"] == f"u{esim}"
    # gizli kayıt ve gizli kart: üye göremediğini atayamaz (404)
    owner.patch(f"/api/entries/{sec}", json={"hidden": True})
    assert editor.put("/api/spender", json={"source": "manual", "id": sec, "who": "ortak"}).status_code == 404
    kuveyt = next(c["card_no"] for c in owner.get("/api/book").json()["cards"] if c["bank"] == "Kuveyt Türk")
    ktxn = next(x for x in owner.get("/api/transactions").json() if x["source"] == "card" and x["account"] == kuveyt)
    owner.put(f"/api/cards/{kuveyt}/spender", json={"who": f"u{admin}"})
    owner.put("/api/book/rules", json={"hidden_cards": [kuveyt]})
    assert editor.put(f"/api/cards/{kuveyt}/spender", json={"who": f"u{esim}"}).status_code == 404
    assert editor.put("/api/spender", json={"source": "card", "id": ktxn["id"], "who": "ortak"}).status_code == 404
    assert kuveyt not in editor.get("/api/cards/spenders").json()["cards"]
    assert owner.get("/api/cards/spenders").json()["cards"][kuveyt] == f"u{admin}"


def test_own_book_and_single_person_book_404(owner):  # noqa: F811
    eid = _entry(owner, "Market", 300)
    body = {"source": "manual", "id": eid, "who": "ortak"}
    r = owner.put("/api/spender", json=body)
    assert r.status_code == 404 and r.json()["detail"] == "Yalnız ortak defterde"
    assert owner.get("/api/cards/spenders").status_code == 404
    assert "spender" not in _rows(owner, MONTH)[0]  # tek kişilik defter: alan yok
    m = _member(owner)
    own = {"X-HK-Book": "kendi"}
    mine = _entry(m, "Kendi alışverişim", 500, headers=own)
    assert m.put("/api/spender", json={**body, "id": mine}, headers=own).status_code == 404
    assert m.get("/api/cards/spenders", headers=own).status_code == 404
    assert all("spender" in x for x in _rows(m, MONTH))  # ortak defterde alan var
    assert all("spender" not in x for x in m.get("/api/transactions", params={"month": MONTH}, headers=own).json())


def test_budget_by_person_follows_override(owner):  # noqa: F811
    m = _member(owner)
    admin, esim = _uid("admin"), _uid("esim")
    owner.put("/api/budgets", json={"category": "Market & Gıda", "monthly_limit": 1000})
    eid = _entry(owner, "Market", 300)
    _entry(m, "Fırın", 150)
    owner.put("/api/spender", json={"source": "manual", "id": eid, "who": f"u{esim}"})
    [b] = owner.get("/api/budgets", params={"month": MONTH}).json()["status"]
    assert [(p["key"], p["amount"]) for p in b["by_person"]] == [(f"u{esim}", 450)]
    owner.put("/api/spender", json={"source": "manual", "id": eid, "who": "ortak"})
    [b] = owner.get("/api/budgets", params={"month": MONTH}).json()["status"]
    assert [(p["key"], p["user_id"], p["amount"]) for p in b["by_person"]] == [("ortak", None, 300), (f"u{esim}", esim, 150)]
    assert f"u{admin}" not in {p["key"] for p in b["by_person"]}


def test_hosted_requires_family(hosted):  # noqa: F811
    from app import system

    c, u = _user(hosted)
    for plan in (None, "premium"):
        system.update_user(u["id"], plan=plan)
        for method, path, body in (("PUT", "/api/spender", {"source": "manual", "id": 1, "who": "ortak"}),
                                   ("PUT", "/api/cards/1234/spender", {"who": None}), ("GET", "/api/cards/spenders", None)):
            r = c.request(method, path, json=body)
            assert r.status_code == 402 and r.json()["plan_required"] == "aile_rapor", (plan, path)
    system.update_user(u["id"], plan="aile")
    assert c.get("/api/cards/spenders").status_code == 404  # Aile ama henüz üye yok


def test_capability_listed(owner):  # noqa: F811
    assert "spender_assign" in owner.get("/api/meta").json()["capabilities"]
