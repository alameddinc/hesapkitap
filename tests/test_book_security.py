"""Ortak defter yetki açıkları (denetim O1, O9): /share-target yetki kontrolü, gizli nesneye id ile yazma, /api/rules
sızıntısı. Veriler test fixture'larındaki uydurma ekstrelerden gelir."""
from tests.conftest import fixture_bytes
from tests.test_book import _member, _setup_book, owner  # noqa: F401  (fixture)


def _share_file():
    return [("file", ("e.pdf", b"%PDF-1.4\n" + fixture_bytes("kuveytturk.txt"), "application/pdf"))]


def _hide_all(owner):
    """_setup_book + Kuveyt Türk kartı, Halkbank hesabı ve 'Sürpriz hediye' kaydı gizli."""
    sec, pub = _setup_book(owner)
    ov = owner.get("/api/book").json()
    kuveyt = next(c["card_no"] for c in ov["cards"] if c["bank"] == "Kuveyt Türk")
    halk = next(a["id"] for a in ov["accounts"] if a["name"] == "Halkbank")
    owner.patch(f"/api/entries/{sec}", json={"hidden": True})
    owner.put("/api/book/rules", json={"hidden_cards": [kuveyt], "hidden_accounts": [halk], "hide_fees": True})
    sid = next(s["id"] for s in owner.get("/api/statements").json() if s["card_no"] == kuveyt)
    return {"sec": sec, "pub": pub, "kuveyt": kuveyt, "halk": halk, "sid": sid}


# --- O1: /share-target ------------------------------------------------------------------------------------------

def test_viewer_cannot_write_via_share_target(owner):
    viewer = _member(owner, "cocuk", "viewer")
    assert viewer.post("/api/share", files=_share_file()).status_code == 403
    r = viewer.post("/share-target", files=_share_file(), follow_redirects=False)
    assert r.status_code == 403
    assert owner.get("/api/statements").json() == []  # sahibin defterine hiçbir şey yazılmadı


def test_editor_can_share_target(owner):
    editor = _member(owner, "esim", "editor")
    r = editor.post("/share-target", files=_share_file(), follow_redirects=False)
    assert r.status_code == 303
    assert [s["bank"] for s in owner.get("/api/statements").json()] == ["Kuveyt Türk"]


def test_share_target_requires_password_change(owner):
    from app import system

    editor = _member(owner, "esim", "editor")
    u = system.user_by_name("esim")
    system.update_user(u["id"], must_change_password=1)
    r = editor.post("/share-target", files=_share_file(), follow_redirects=False)
    assert r.status_code == 403 and r.json()["detail"] == "password_change_required"
    assert owner.get("/api/statements").json() == []


def test_share_target_size_limit(owner, monkeypatch):
    from app.api import main

    monkeypatch.setattr(main, "SHARE_MAX_BYTES", 10)
    r = owner.post("/share-target", files=_share_file(), follow_redirects=False)
    assert r.status_code == 303 and "b%C3%BCy%C3%BCk" in r.headers["location"]  # "çok büyük"
    assert owner.get("/api/statements").json() == []


# --- O9: gizli nesneye yazma ------------------------------------------------------------------------------------

def test_full_member_cannot_delete_hidden_entry_or_statement(owner):
    h = _hide_all(owner)
    full = _member(owner, "esim", "full")
    assert full.delete(f"/api/entries/{h['sec']}").status_code == 404
    assert any(e.get("id") == h["sec"] and e.get("source") == "manual" for e in owner.get("/api/transactions").json())
    assert full.delete(f"/api/statements/{h['sid']}").status_code == 404
    assert full.post(f"/api/statements/{h['sid']}/pay", json={"amount": 1, "paid_at": "2026-10-20"}).status_code == 404
    assert full.post(f"/api/statements/{h['sid']}/confirm").status_code == 404
    assert sorted(s["bank"] for s in owner.get("/api/statements").json()) == ["Kuveyt Türk", "VakıfBank"]
    # görünen kayıtta tam yetki çalışmaya devam eder
    assert full.delete(f"/api/entries/{h['pub']}").status_code == 200


def test_full_member_cannot_touch_hidden_account(owner):
    h = _hide_all(owner)
    full = _member(owner, "esim", "full")
    assert full.patch(f"/api/accounts/{h['halk']}", json={"name": "X"}).status_code == 404
    assert full.delete(f"/api/accounts/{h['halk']}").status_code == 404
    r = full.post("/api/entries", json={"entry_date": "2026-10-05", "description": "Deneme", "amount": 10,
                                        "kind": "gider", "category": "Diğer", "payment": "havale", "account_id": h["halk"]})
    assert r.status_code == 404
    vak = next(s["id"] for s in owner.get("/api/statements").json() if s["bank"] == "VakıfBank")
    r = full.post(f"/api/statements/{vak}/pay", json={"amount": 1, "paid_at": "2026-10-20", "account_id": h["halk"]})
    assert r.status_code == 404
    assert any(a["name"] == "Halkbank" for a in owner.get("/api/accounts").json()["accounts"])


def test_full_member_cannot_link_hidden_card_txn(owner):
    from app import db

    h = _hide_all(owner)
    with db.connect() as c:  # sahibin defteri (yönetici: ana veritabanı)
        hidden_txn = c.execute("SELECT t.id FROM transactions t JOIN statements s ON s.id = t.statement_id "
                               "WHERE s.card_no = ? LIMIT 1", (h["kuveyt"],)).fetchone()[0]
    full = _member(owner, "esim", "full")
    assert full.patch(f"/api/card-transactions/{hidden_txn}", json={"category": "Diğer"}).status_code == 404
    assert full.put(f"/api/splits/card/{hidden_txn}", json={"parts": []}).status_code == 404
    assert full.get(f"/api/splits/card/{hidden_txn}").status_code == 404
    assert full.post(f"/api/entries/{h['pub']}/link", json={"txn_id": hidden_txn}).status_code == 404
    assert full.post(f"/api/entries/{h['pub']}/match", json={"other": f"t{hidden_txn}", "same": True}).status_code == 404


# --- /api/rules ------------------------------------------------------------------------------------------------

def test_rules_do_not_leak_hidden_card_merchants(owner):
    _hide_all(owner)
    assert "HENNES" in owner.get("/api/rules").text  # sahip her kuralı görür
    full = _member(owner, "esim", "full")
    r = full.get("/api/rules")
    assert r.status_code == 200 and "HENNES" not in r.text
    assert len(r.json()) > 0  # gizli kartla ilgisi olmayan kurallar duruyor
