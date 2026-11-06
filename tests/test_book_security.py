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


def test_viewer_cannot_delete_debt_payment(owner):
    did = owner.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 100,
                                         "debt_date": "2026-09-01"}).json()["id"]
    pid = owner.post(f"/api/debts/{did}/pay", json={"amount": 40, "paid_at": "2026-10-01"}).json()["payment_id"]
    viewer = _member(owner, "izleyici", "viewer")
    assert viewer.delete(f"/api/debt-payments/{pid}").status_code == 403
    assert owner.delete(f"/api/debt-payments/{pid}").json()["deleted"] is True


# --- güvenlik denetimi: gizli kaydın fiş kalemleri ve gövdedeki id ------------------------------------------------

def test_prices_do_not_leak_hidden_receipt_items(owner):
    r = owner.post("/api/entries", json={
        "entry_date": "2026-10-05", "description": "ORNEK ECZANE", "amount": 250, "kind": "gider", "category": "Sağlık",
        "payment": "nakit", "items": [{"name": "GIZLI-URUN-X", "qty": 1, "unit": "adet", "amount": 250}]})
    eid = r.json()["id"]
    owner.post("/api/entries", json={
        "entry_date": "2026-10-06", "description": "ORNEK MARKET", "amount": 40, "kind": "gider",
        "category": "Market & Gıda", "payment": "nakit", "items": [{"name": "ACIK-URUN-Y", "qty": 1, "unit": "adet", "amount": 40}]})
    assert owner.patch(f"/api/entries/{eid}", json={"hidden": True}).status_code == 200
    assert "GIZLI-URUN-X" in owner.get("/api/prices").text  # sahip hepsini görür
    viewer = _member(owner, "izleyici", "viewer")
    out = viewer.get("/api/prices").text
    assert "GIZLI-URUN-X" not in out and "ORNEK ECZANE" not in out
    assert "ACIK-URUN-Y" in out  # görünen kaydın kalemi duruyor


def test_full_member_cannot_mark_hidden_entry_as_donation(owner):
    h = _hide_all(owner)
    full = _member(owner, "esim", "full")
    r = full.post("/api/donations/mark", json={"source": "entry", "id": h["sec"], "kind": "sadaka"})
    assert r.status_code == 404
    assert "Sürpriz hediye" not in full.get("/api/donations").text
    assert full.post("/api/donations/mark", json={"source": "entry", "id": h["pub"], "kind": "sadaka"}).status_code == 200


def test_full_member_cannot_use_hidden_account_in_body(owner):
    h = _hide_all(owner)
    did = owner.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 100,
                                         "debt_date": "2026-09-01"}).json()["id"]
    pid = owner.post(f"/api/debts/{did}/pay", json={"amount": 40, "paid_at": "2026-10-01"}).json()["payment_id"]
    jid = owner.post("/api/jars", json={"unit": "CEYREK", "name": "Örnek kumbara"}).json()["id"]
    full = _member(owner, "esim", "full")
    halk = h["halk"]
    assert full.patch(f"/api/debt-payments/{pid}", json={"account_id": halk}).status_code == 404
    assert full.post(f"/api/jars/{jid}/buy", json={"quantity": 1, "cost": 10, "account_id": halk}).status_code == 404
    assert full.post("/api/jars", json={"name": "Gizli", "account_id": halk}).status_code == 404
    assert full.patch(f"/api/jars/{jid}", json={"account_id": halk}).status_code == 404
    assert full.post("/api/debts", json={"person": "Örnek", "direction": "borc", "amount": 5, "debt_date": "2026-09-01",
                                         "account_id": halk}).status_code == 404
    assert full.patch(f"/api/debts/{did}", json={"account_id": halk}).status_code == 404
    assert full.post(f"/api/debts/{did}/pay", json={"amount": 1, "paid_at": "2026-10-02", "account_id": halk}).status_code == 404
    assert full.post("/api/donations", json={"kind": "sadaka", "amount": 5, "account_id": halk}).status_code == 404
    with_rec = {"description": "Örnek abonelik", "amount": 10, "kind": "gider", "category": "Diğer",
                "start_month": "2026-10", "account_id": halk}
    assert full.post("/api/recurring", json=with_rec).status_code == 404
    # görünen hesapla çalışır
    nakit = next(a["id"] for a in owner.get("/api/accounts").json()["accounts"] if a["name"] == "Nakit")
    assert full.patch(f"/api/debt-payments/{pid}", json={"account_id": nakit}).status_code == 200


# --- gelen kutusu: sahibin bekleyen belgeleri üyelere görünmez ---------------------------------------------------

def test_inbox_members_see_and_close_only_their_own(owner, monkeypatch):
    from app import receipt

    def fake(data, mime, cats, item_cats, more=None):
        who = data.decode(errors="ignore").split(":")[-1]
        return {"type": "receipt", "merchant": f"ORNEK-{who}", "total": 99.5, "date": "2026-10-23", "items": [],
                "category": "Diğer", "confidence": "yüksek"}

    monkeypatch.setattr(receipt, "parse_document", fake)
    editor = _member(owner, "esim", "editor")
    full = _member(owner, "tam", "full")
    owner.post("/api/share", files={"file": ("a.jpg", b"\xff\xd8fis:SAHIP", "image/jpeg")})
    editor.post("/api/share", files={"file": ("b.jpg", b"\xff\xd8fis:ESIM", "image/jpeg")})

    def shops(c):
        return sorted(r["doc"]["merchant"] for r in c.get("/api/inbox").json())

    assert shops(owner) == ["ORNEK-ESIM", "ORNEK-SAHIP"]
    assert shops(full) == ["ORNEK-ESIM", "ORNEK-SAHIP"]
    assert shops(editor) == ["ORNEK-ESIM"]  # sahibin bekleyen fişi (mağaza, tutar) görünmez
    owner_item = next(r["id"] for r in owner.get("/api/inbox").json() if r["doc"]["merchant"] == "ORNEK-SAHIP")
    assert editor.post(f"/api/inbox/{owner_item}/silindi").status_code == 404  # başkasınınkini kapatamaz
    assert shops(owner) == ["ORNEK-ESIM", "ORNEK-SAHIP"]
    mine = editor.get("/api/inbox").json()[0]["id"]
    assert editor.post(f"/api/inbox/{mine}/kaydedildi").status_code == 200
    assert shops(owner) == ["ORNEK-SAHIP"]
