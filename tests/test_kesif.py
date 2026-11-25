"""Keşif koleksiyonu (app/kesif.py, GET /api/kesif, POST /api/kesif/seen). Veriler uydurmadır."""
import pytest
from fastapi.testclient import TestClient

CONSENT = ["kosullar", "acik_riza"]


def _state(client) -> dict:
    r = client.get("/api/kesif")
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()["items"]}


def _gider(client, desc="Deneme market", amount=50, note=None, kind="gider"):
    body = {"entry_date": "2026-10-05", "description": desc, "amount": amount, "kind": kind,
            "category": "Market & Gıda", "payment": "nakit"}
    if note:
        body["note"] = note
    r = client.post("/api/entries", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_book_all_open(client):
    j = client.get("/api/kesif").json()
    assert j["total"] == 12 and j["done"] == 0 and j["seen"] == []
    assert [i["id"] for i in j["items"]] == ["harcama", "maas", "belge", "posta", "banka", "kumbara", "butce", "aile",
                                             "borc", "varlik", "liste", "zekat"]
    assert all(i["done"] is False and i["done_at"] is None and i["seen"] is False for i in j["items"])


def test_receipt_expense_completes_harcama(client, tmpdb):
    """Fişten gelen gider de "İlk harcama"yı tamamlar (yalnız elle girilen değil)."""
    e = _gider(client, "Örnek Market", 87.5, note="Fiş: Örnek Market")
    eid = e.get("id") or e.get("entry", {}).get("id")
    tmpdb.save_receipt_items(eid, [{"name": "Ekmek", "amount": 12.5}, {"name": "Süt", "amount": 75}],
                             "Örnek Market", "2026-10-05", total=87.5)
    s = _state(client)
    assert s["harcama"]["done"] and s["harcama"]["done_at"]
    assert not s["maas"]["done"]


def test_income_and_salary_complete_maas(client, tmpdb):
    _gider(client, "Burs", 1000, kind="gelir")
    s = _state(client)
    assert s["maas"]["done"] and not s["harcama"]["done"]  # gelir harcama sayılmaz


def test_recurring_salary_completes_maas(client, tmpdb):
    with tmpdb.connect() as c:
        c.execute("INSERT INTO recurring (description, amount, kind, start_month) VALUES ('Maaş', 1000, 'gelir', '2026-10')")
    assert _state(client)["maas"]["done"]


def test_card_statement_completes_belge_and_harcama(client, upload):
    upload()
    s = _state(client)
    assert s["belge"]["done"] and s["belge"]["done_at"]
    assert s["harcama"]["done"]  # ekstredeki kart harcaması da sayılır


def test_bank_document_completes_belge(client, tmpdb):
    with tmpdb.connect() as c:
        c.execute("INSERT INTO bank_documents (file_name, filename) VALUES ('statements-bank/x.pdf', 'dokum.pdf')")
    assert _state(client)["belge"]["done"]


@pytest.mark.parametrize("kid,sql", [
    ("kumbara", "INSERT INTO jars (name, goal) VALUES ('Tatil', 100)"),
    ("butce", "INSERT INTO budgets (category, monthly_limit) VALUES ('Market & Gıda', 500)"),
    ("borc", "INSERT INTO debts (person, direction, amount, debt_date) VALUES ('Örnek Kişi', 'alacak', 10, '2026-10-01')"),
    ("varlik", "INSERT INTO assets (kind, name) VALUES ('arac', 'Deneme araç')"),
    ("liste", "INSERT INTO shopping_list (text) VALUES ('Süt')"),
])
def test_table_discoveries(client, tmpdb, kid, sql):
    with tmpdb.connect() as c:
        c.execute(sql)
    s = _state(client)
    assert s[kid]["done"]
    assert sum(i["done"] for i in s.values()) == 1  # yalnız ilgili keşif


def test_deleted_asset_not_counted(client, tmpdb):
    with tmpdb.connect() as c:
        c.execute("INSERT INTO assets (kind, name, deleted_at) VALUES ('arac', 'Silinen', '2026-10-01')")
    assert not _state(client)["varlik"]["done"]


def test_zekat_discovery(client, tmpdb):
    from app import zakat

    with tmpdb.connect() as c:
        zakat._init(c)  # sayfayı açmak ("Ben" kişisi kendiliğinden kurulur) keşfi tamamlamaz
    assert not _state(client)["zekat"]["done"]
    with tmpdb.connect() as c:
        pid = c.execute("SELECT id FROM zakat_people WHERE is_self = 1").fetchone()[0]
        c.execute("INSERT INTO zakat_assets (person_id, kind, quantity) VALUES (?, 'altin_gram', 2)", (pid,))
    assert _state(client)["zekat"]["done"]


def test_donation_completes_zekat(client, tmpdb):
    from app import donations

    with tmpdb.connect() as c:
        donations._init(c)
        c.execute("INSERT INTO donations (kind, amount, given_date) VALUES ('sadaka', 20, '2026-10-02')")
    assert _state(client)["zekat"]["done"]


def test_mail_log_completes_posta_and_banka(client, tmpdb):
    with tmpdb.connect() as c:
        c.execute("INSERT INTO mail_log (message_id, kind, status) VALUES ('m1', 'diger', 'skip')")
    s = _state(client)
    assert s["posta"]["done"] and not s["banka"]["done"]  # gelen ama işlenmeyen posta: banka bağlı sayılmaz
    with tmpdb.connect() as c:
        c.execute("INSERT INTO mail_log (message_id, kind, status) VALUES ('m2', 'pdf', 'ok')")
    assert _state(client)["banka"]["done"]


def test_seen_idempotent_and_validated(client):
    r = client.post("/api/kesif/seen", json={"id": "harcama"})
    assert r.status_code == 200 and r.json()["seen"] == ["harcama"]
    assert client.post("/api/kesif/seen", json={"id": "harcama"}).json()["seen"] == ["harcama"]
    j = client.get("/api/kesif").json()
    assert j["seen"] == ["harcama"] and next(i for i in j["items"] if i["id"] == "harcama")["seen"]
    assert client.post("/api/kesif/seen", json={"id": "yok-boyle"}).status_code == 400


def test_old_onboarding_unchanged(client):
    """Eski istemciler (1.0.12) için Başlarken yanıtı aynı kalır."""
    for v in (1, 2):
        o = client.get(f"/api/onboarding?v={v}").json()
        assert set(o) == {"steps", "done", "total", "dismissed"}
    assert [s["key"] for s in client.get("/api/onboarding?v=2").json()["steps"]][:4] == ["hesap", "maas", "kart", "sabit"]


# ---------------------------------------------------------------- çok kullanıcı: kişi ve defter ayrımı

@pytest.fixture()
def admin(client, monkeypatch):
    from app import auth
    from app.api import main

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _account(admin, name):
    from app.api.main import app

    inv = admin.post("/api/admin/invites", json={}).json()
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"consent": CONSENT, "code": inv["code"], "username": f"{name}@ornek.com",
                                           "password": f"{name}-sifre-12345"})
    assert r.status_code == 200, r.text
    return c


def test_other_users_data_and_seen_isolated(admin):
    _gider(admin, "Yönetici market", 40)
    admin.post("/api/kesif/seen", json={"id": "harcama"})
    other = _account(admin, "deneme")
    s = _state(other)
    assert not s["harcama"]["done"] and not s["harcama"]["seen"]
    assert other.get("/api/kesif").json()["seen"] == []
    assert _state(admin)["harcama"]["done"]


def test_family_member_and_owner(admin):
    from app.api.main import app

    assert not _state(admin)["aile"]["done"]
    inv = admin.post("/api/book/invites", json={"note": "Üye"}).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"consent": CONSENT, "code": inv["code"], "username": "uye@ornek.com",
                                              "password": "uye-sifre-12345"}).status_code == 200
    assert _state(admin)["aile"]["done"]
    assert _state(m)["aile"]["done"]
    # üyenin "görüldü" işareti kişiye ait; sadece görür yetkisinde de yazılabilir, sahibe karışmaz
    assert m.post("/api/kesif/seen", json={"id": "aile"}).status_code == 200
    assert admin.get("/api/kesif").json()["seen"] == []
