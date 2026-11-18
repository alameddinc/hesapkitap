"""Güvenlik denetimi 18.11, O4–O7: ortak defterde gizli kart/hesap sızıntıları. Kişiler, tutarlar ve dekont baytları
uydurmadır."""
from tests.test_book import _member, _setup_book, owner  # noqa: F401 - fixture
from tests.test_book_security import _hide_all


def _hidden_account_book(owner):  # noqa: F811
    """PoC A (test_poc_hidden_account): gizli hesaba bağlı aktarım (dekontlu), kayıt, sabit kalem, borç ve kumbara."""
    from app import db, system

    _setup_book(owner)
    ov = owner.get("/api/book").json()
    halk = next(a["id"] for a in ov["accounts"] if a["name"] == "Halkbank")
    nakit = next(a["id"] for a in ov["accounts"] if a["name"] == "Nakit")
    tid = owner.post("/api/own-transfers", json={"date": "2026-10-04", "amount": 777, "from_account_id": halk,
                                                 "to_account_id": nakit, "description": "GIZLITRANSFER"}).json()["id"]
    admin = system.user_by_name("admin")
    name = "a" * 64 + ".jpg"
    with db.use_space(system.user_db_path(admin), admin):
        db.store_file("receipts", name, b"\xff\xd8\xffGIZLIDEKONT")
        with db.connect() as c:
            c.execute("UPDATE own_transfers SET receipt_file = ?", (name,))
    owner.post("/api/entries", json={"entry_date": "2026-10-06", "description": "FAST giden", "amount": 4321,
                                     "kind": "gider", "category": "Transfer", "payment": "havale", "account_id": halk,
                                     "counterparty": "GIZLIKARSI"})
    owner.post("/api/jars", json={"name": "Tatil", "account_id": halk, "initial": 100})
    return tid, halk


# ---------------------------------------------------------------- O4

def test_full_member_cannot_list_or_undo_imports(owner):  # noqa: F811
    """PoC A/imports: tam yetkili üye gizli kartın numarasını görüyor ve gizli ekstreyi geri alarak silebiliyordu."""
    h = _hide_all(owner)
    full = _member(owner, "esim", "full")
    assert full.get("/api/imports").status_code == 403
    bid = owner.get("/api/imports").json()[0]["id"]
    assert full.post(f"/api/imports/{bid}/undo", params={"force": True}).status_code == 403
    assert any(s["card_no"] == h["kuveyt"] for s in owner.get("/api/statements").json())  # gizli ekstre yerinde
    assert full.get("/api/statements").status_code == 200  # tam yetkinin diğer uçları açık


# ---------------------------------------------------------------- O5

def test_member_cannot_fetch_hidden_account_transfer_receipt(owner):  # noqa: F811
    tid, halk = _hidden_account_book(owner)
    assert owner.get(f"/api/own-transfers/{tid}/receipt").status_code == 200  # sahip görür
    owner.put("/api/book/rules", json={"hidden_accounts": [halk], "hidden_pages": ["transferler"]})
    v = _member(owner, "cocuk", "viewer")
    r = v.get(f"/api/own-transfers/{tid}/receipt")  # Transferler kapalı: önek de kapalı
    assert r.status_code == 403 and b"GIZLIDEKONT" not in r.content
    owner.put("/api/book/rules", json={"hidden_accounts": [halk], "hidden_pages": []})
    for t in (tid, tid + 1):  # iki bacak da: biri gizli hesapta
        r = v.get(f"/api/own-transfers/{t}/receipt")
        assert r.status_code == 404 and b"GIZLIDEKONT" not in r.content


def test_member_sees_receipt_of_visible_transfer(owner):  # noqa: F811
    tid, halk = _hidden_account_book(owner)
    v = _member(owner, "cocuk", "viewer")
    assert v.get(f"/api/own-transfers/{tid}/receipt").status_code == 200  # gizleme yok: eskisi gibi


# ---------------------------------------------------------------- O6

def test_transfers_hide_hidden_account_legs_and_entries(owner):  # noqa: F811
    tid, halk = _hidden_account_book(owner)
    mine = owner.get("/api/transfers").text
    assert "GIZLITRANSFER" in mine and "GIZLIKARSI" in mine
    owner.put("/api/book/rules", json={"hidden_accounts": [halk]})
    for role, name in (("viewer", "cocuk"), ("full", "esim")):
        m = _member(owner, name, role)
        r = m.get("/api/transfers")
        assert r.status_code == 200
        body = r.json()
        assert "GIZLITRANSFER" not in r.text and "GIZLIKARSI" not in r.text and "4321" not in r.text
        assert all(o.get("account_id") != halk for o in body["own"])
        assert body["totals"]["own_out"] == 0 and body["totals"]["sent"] == 0
        assert all(p["name"] != "GIZLIKARSI" for p in body["people"])


# ---------------------------------------------------------------- O7 (yalnız ad)

def test_hidden_account_name_masked(owner):  # noqa: F811
    from app import book

    tid, halk = _hidden_account_book(owner)
    owner.put("/api/book/rules", json={"hidden_accounts": [halk]})
    assert '"account_name":"Halkbank"' in owner.get("/api/jars").text  # sahip adı görür
    v = _member(owner, "cocuk", "viewer")
    jars = v.get("/api/jars").json()
    jar = next(j for j in (jars if isinstance(jars, list) else jars.get("jars", [])) if j["name"] == "Tatil")
    assert jar["account_name"] == book.HIDDEN_ACCOUNT_LABEL  # kumbaranın kendisi görünür (ürün kararı), adı değil
    assert "Halkbank" not in v.get("/api/jars").text
    assert "Halkbank" not in v.get("/api/net-worth").text
