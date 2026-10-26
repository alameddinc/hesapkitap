"""Ortak defter: üye sahibin defterini görür ve kayıt ekler; gizleme kuralları sunucuda uygulanır."""
import pytest
from fastapi.testclient import TestClient

from tests.conftest import fixture_bytes


@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _member(owner):
    from app.api.main import app

    inv = owner.post("/api/book/invites", json={"note": "Eşim"}).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"code": inv["code"], "username": "esim", "password": "esim-sifre-12345"}).status_code == 200
    return m


def _setup_book(owner):
    files = [("files", (n.replace(".txt", ".pdf"), fixture_bytes(n), "application/pdf")) for n in ("vakifbank_a.txt", "kuveytturk.txt")]
    assert all(r["status"] == "saved" for r in owner.post("/api/statements/upload", files=files).json())
    owner.post("/api/accounts", json={"name": "Halkbank", "kind": "banka", "balance": 50000})
    owner.post("/api/accounts", json={"name": "Nakit", "kind": "nakit", "balance": 1000})
    sec = owner.post("/api/entries", json={"entry_date": "2026-10-02", "description": "Sürpriz hediye", "amount": 900,
                                           "kind": "gider", "category": "Giyim", "payment": "nakit"}).json()["id"]
    pub = owner.post("/api/entries", json={"entry_date": "2026-10-03", "description": "Market", "amount": 300,
                                           "kind": "gider", "category": "Market & Gıda", "payment": "nakit"}).json()["id"]
    return sec, pub


def test_member_sees_book_and_rules_are_enforced(owner):
    sec, pub = _setup_book(owner)
    m = _member(owner)
    me = m.get("/api/book/me").json()
    assert me["member"] is True and me["owner"] == "admin"
    assert len(m.get("/api/statements").json()) == 2  # henüz kural yok: hepsini görür

    ov = owner.get("/api/book").json()
    assert [x["username"] for x in ov["members"]] == ["esim"]
    kuveyt = next(c["card_no"] for c in ov["cards"] if c["bank"] == "Kuveyt Türk")
    halk = next(a["id"] for a in ov["accounts"] if a["name"] == "Halkbank")
    owner.put("/api/book/rules", json={"hidden_cards": [kuveyt], "hide_fees": True, "hidden_accounts": [halk],
                                       "hidden_pages": ["zekat", "borclar"]})
    owner.patch(f"/api/entries/{sec}", json={"hidden": True})

    # sahip her şeyi görür
    assert len(owner.get("/api/statements").json()) == 2
    assert any(r["description"] == "Sürpriz hediye" for r in owner.get("/api/transactions").json())
    # üye: gizli kart, faiz, gizli kayıt, gizli hesap yok
    rows = m.get("/api/transactions").json()
    assert not any(r.get("account") == kuveyt for r in rows)
    assert not any(r["kind"] == "faiz_ucret" for r in rows)
    assert not any(r["description"] == "Sürpriz hediye" for r in rows)
    assert any(r["description"] == "Market" for r in rows)
    assert [s["bank"] for s in m.get("/api/statements").json()] == ["VakıfBank"]
    assert all(a["name"] != "Halkbank" for a in m.get("/api/accounts").json()["accounts"])
    assert all(a["name"] != "Halkbank" for a in m.get("/api/net-worth").json()["accounts"])
    assert m.get("/api/interest-history").json()["months"] == []
    for month in {r["month"] for r in rows}:
        assert m.get("/api/summary", params={"month": month}).json()["totals"]["fees"] == 0
    # kapalı sayfalar ve sahibe ait bölümler
    for path in ("/api/zakat", "/api/debts", "/api/settings", "/api/settings/export.zip", "/api/imports", "/api/book", "/api/admin/users"):
        assert m.get(path).status_code == 403, path
    assert m.get("/api/book/me").json()["hidden_pages"] == ["zekat", "borclar"]


def test_member_can_add_and_fix_but_not_delete_or_upload(owner):
    sec, pub = _setup_book(owner)
    owner.patch(f"/api/entries/{sec}", json={"hidden": True})
    m = _member(owner)
    from app import system

    mid = system.user_by_name("esim")["id"]
    r = m.post("/api/entries", json={"entry_date": "2026-10-05", "description": "Fırın", "amount": 45, "kind": "gider",
                                     "category": "Market & Gıda", "payment": "nakit"})
    assert r.status_code == 200
    eid = r.json()["id"]
    mine = next(x for x in owner.get("/api/transactions").json() if x.get("id") == eid and x["source"] == "manual")
    assert mine["created_by"] == mid  # sahip kimin eklediğini görür
    assert m.patch(f"/api/entries/{eid}", json={"amount": 50}).status_code == 200  # kendi kaydı: tamamen
    # sahibin kaydında sadece kategori/not
    m.patch(f"/api/entries/{pub}", json={"amount": 1, "category": "Kafe & Restoran", "hidden": True})
    p = next(x for x in owner.get("/api/transactions").json() if x.get("id") == pub and x["source"] == "manual")
    assert p["amount"] == 300 and p["category"] == "Kafe & Restoran" and not p["hidden"]
    assert m.patch(f"/api/entries/{sec}", json={"category": "X"}).status_code == 404  # gizli kayıt: yok gibi
    assert m.delete(f"/api/entries/{pub}").status_code == 403
    up = m.post("/api/statements/upload", files=[("files", ("a.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))])
    assert up.status_code == 403
    assert m.put("/api/book/rules", json={"hide_fees": False}).status_code == 403
    assert m.post("/api/recurring", json={"description": "x", "amount": 1, "kind": "gider", "day_of_month": 1,
                                          "start_month": "2026-10"}).status_code == 403
    # şifre değiştirme serbest
    assert m.post("/api/settings/password", json={"current": "esim-sifre-12345", "new": "esim-yeni-sifre-1"}).status_code == 200


def test_owner_removes_member_and_book_stays(owner):
    _setup_book(owner)
    m = _member(owner)
    m.post("/api/entries", json={"entry_date": "2026-10-05", "description": "Fırın", "amount": 45, "kind": "gider",
                                 "category": "Market & Gıda", "payment": "nakit"})
    uid = owner.get("/api/book").json()["members"][0]["id"]
    assert owner.delete(f"/api/book/members/{uid}").json()["ok"]
    assert m.get("/api/transactions").status_code == 401
    assert any(r["description"] == "Fırın" for r in owner.get("/api/transactions").json())  # eklediği kayıt kalır
    assert owner.get("/api/book").json()["members"] == []


def test_member_cannot_reach_raw_data_around_rules(owner):
    sec, pub = _setup_book(owner)
    m = _member(owner)
    ov = owner.get("/api/book").json()
    kuveyt = next(c["card_no"] for c in ov["cards"] if c["bank"] == "Kuveyt Türk")
    owner.put("/api/book/rules", json={"hidden_cards": [kuveyt], "hidden_pages": ["borclar"]})
    owner.post("/api/debts", json={"person": "Komşu", "direction": "borc", "asset": "TRY", "amount": 500,
                                   "debt_date": "2026-10-01", "due_date": "2026-11-01"})
    assert m.get(f"/api/entries/{pub}/candidates").status_code == 403
    assert m.get("/api/net-worth/at", params={"day": "2026-09-01"}).status_code == 403
    assert m.get("/api/mail/status").status_code == 403
    assert not any(p["type"] == "debt" for p in m.get("/api/payables").json())
    assert any(p["type"] == "debt" for p in owner.get("/api/payables").json())
    assert not any(p["title"].endswith(kuveyt) for p in m.get("/api/payables").json())
