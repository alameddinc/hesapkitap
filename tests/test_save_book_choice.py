"""Fiş/fatura onayında defter ve harcayan seçimi (yetenek save_book_choice): POST /api/entries ve /api/bills spender;
üye öbür defterine (X-HK-Book) kaydederken okumadaki dosya kopyalanır (yalnız aynı üyenin iki defteri arasında);
açık başlık çerezden önce gelir. Kişiler ve tutarlar uydurma."""
import pytest

from tests.test_book import _member, owner  # noqa: F401 - fixture
from tests.test_doc_reads import _jpeg

OWN, SHARED = {"X-HK-Book": "kendi"}, {"X-HK-Book": "ortak"}
FIS = {"entry_date": "2026-10-05", "description": "Ornek Market", "amount": 72.5, "kind": "gider",
       "category": "Market & Gıda", "payment": "nakit"}


@pytest.fixture()
def fake_receipt(monkeypatch):
    from app import receipt

    monkeypatch.setattr(receipt, "parse_receipt", lambda *a, **k: {"merchant": "Ornek Market", "total": 72.5, "items": []})


def _parse(c, headers=None, data=None):
    r = c.post("/api/receipts/parse", files={"file": ("f.jpg", data or _jpeg(), "image/jpeg")}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["receipt_file"]


def _uid(name):
    from app import system

    return system.user_by_name(name)["id"]


def _row(c, eid, headers=None):
    return next(r for r in c.get("/api/transactions", params={"month": "2026-10"}, headers=headers or {}).json()
                if r["source"] == "manual" and r["id"] == eid)


def _overrides(eid):
    from app import db, system

    with db.use_space(system.user_db_path(system.user_by_name("admin")), None), db.connect() as c:
        return [tuple(r) for r in c.execute("SELECT source, ref_id, user_id FROM spender_overrides WHERE ref_id = ?", (eid,))]


def test_member_saves_receipt_parsed_in_shared_book_into_own_book(owner, fake_receipt):  # noqa: F811
    m = _member(owner)
    name = _parse(m)  # okuma ortak defterde (varsayılan): fotoğraf sahibin defterinde
    r = m.post("/api/entries", json={**FIS, "receipt_file": name}, headers=OWN)
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    photo = m.get(f"/api/entries/{eid}/receipt", headers=OWN)
    assert photo.status_code == 200 and photo.content[:2] == b"\xff\xd8"  # fotoğraf "Benim" defterine kopyalandı
    assert not any(x["description"] == "Ornek Market" for x in owner.get("/api/transactions").json())
    # tersi: "Benim"de okunan fiş ortak deftere
    name2 = _parse(m, OWN, _jpeg() + b"ikinci")
    eid2 = m.post("/api/entries", json={**FIS, "receipt_file": name2}, headers=SHARED).json()["id"]
    assert owner.get(f"/api/entries/{eid2}/receipt").status_code == 200


def test_file_from_foreign_book_is_not_linked(owner, fake_receipt):  # noqa: F811
    a = _member(owner, "esim")
    b = _member(owner, "kardes")
    theirs = _parse(b, OWN)  # başka üyenin "Benim" defteri
    eid = a.post("/api/entries", json={**FIS, "receipt_file": theirs}).json()["id"]
    assert a.get(f"/api/entries/{eid}/receipt").status_code == 404
    eid = a.post("/api/entries", json={**FIS, "receipt_file": theirs}, headers=OWN).json()["id"]
    assert a.get(f"/api/entries/{eid}/receipt", headers=OWN).status_code == 404
    # sahibin kaydına bağlı (ortak defterdeki) fotoğraf üyenin "Benim" defterine alınamaz
    own_photo = _parse(owner)
    owner.post("/api/entries", json={**FIS, "receipt_file": own_photo})
    eid = a.post("/api/entries", json={**FIS, "receipt_file": own_photo}, headers=OWN).json()["id"]
    assert a.get(f"/api/entries/{eid}/receipt", headers=OWN).status_code == 404


def test_spender_choice_on_save(owner):  # noqa: F811
    m = _member(owner)
    esim, admin = _uid("esim"), _uid("admin")
    eid = m.post("/api/entries", json={**FIS, "spender": "ortak"}).json()["id"]  # üye ekledi, "Ortak" seçti
    row = _row(owner, eid)
    assert row["spender"] == "ortak" and row["spender_set"] is True
    eid = owner.post("/api/entries", json={**FIS, "spender": f"u{esim}"}).json()["id"]  # sahip, üye adına
    row = _row(owner, eid)
    assert row["spender"] == f"u{esim}" and row["spender_set"] is True
    eid = owner.post("/api/entries", json={**FIS, "spender": f"u{admin}"}).json()["id"]  # otomatikle aynı: yazılmaz
    assert _row(owner, eid)["spender"] == f"u{admin}" and _overrides(eid) == []
    r = owner.post("/api/entries", json={**FIS, "spender": "u99999"})
    assert r.status_code == 400 and r.json()["detail"] == "Kişi bu defterde değil"


def test_viewer_cannot_save(owner):  # noqa: F811
    v = _member(owner, "izleyici", "viewer")
    assert v.post("/api/entries", json={**FIS, "spender": "ortak"}).status_code == 403


def test_spender_ignored_outside_shared_book(owner, client):  # noqa: F811
    # tek kişilik defter: yok sayılır (hata yok)
    eid = owner.post("/api/entries", json={**FIS, "spender": "u12345"}).json()["id"]
    assert _overrides(eid) == []
    m = _member(owner)  # üyenin "Benim" defteri: kişi başı kırılım yok
    r = m.post("/api/entries", json={**FIS, "spender": "ortak"}, headers=OWN)
    assert r.status_code == 200


def test_bill_saved_into_other_book_and_spender(owner, monkeypatch):  # noqa: F811
    from app import receipt

    monkeypatch.setattr(receipt, "enabled", lambda: True)
    monkeypatch.setattr(receipt, "parse_bill", lambda d, m: {"provider": "Ornek Su", "amount": 180.0,
                                                             "due_date": "2026-10-20", "category": "Faturalar"})
    m = _member(owner, "esim", "full")
    esim = _uid("esim")
    parsed = m.post("/api/bills/parse", files={"file": ("f.pdf", b"%PDF-1.4 ornek su", "application/pdf")}).json()
    body = {"provider": "Ornek Su", "amount": 180.0, "due_date": "2026-10-20", "file_name": parsed["file_name"]}
    bid = m.post("/api/bills", json=body, headers=OWN).json()["id"]
    assert m.get(f"/api/bills/{bid}/file", headers=OWN).status_code == 200  # dosya "Benim" defterine kopyalandı
    out = owner.post("/api/bills", json={**body, "paid_at": "2026-10-18", "payment": "nakit", "spender": f"u{esim}"}).json()
    row = _row(owner, out["entry_id"])
    assert row["spender"] == f"u{esim}" and row["spender_set"] is True


def test_header_wins_over_cookie(owner):  # noqa: F811
    m = _member(owner)
    m.cookies.set("hk_book", "kendi")
    assert m.get("/api/book/me").json()["book"] == "kendi"  # başlık yok: çerez
    assert m.get("/api/book/me", headers=SHARED).json()["book"] == "ortak"  # açık başlık çerezden önce
    assert m.get("/api/book/me", headers=OWN).json()["book"] == "kendi"
    m.cookies.delete("hk_book")
    assert m.get("/api/book/me").json()["book"] == "ortak"
    assert m.get("/api/book/me", headers={"X-HK-Book": "baska"}).json()["book"] == "ortak"  # tanınmayan başlık


def test_capability():
    from app import version

    assert "save_book_choice" in version.CAPABILITIES
