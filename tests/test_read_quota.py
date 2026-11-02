"""Okuma hakları v2 (hazır sürüm): iki sayaç (belge / fiş), ek okuma paketi (mağaza tüketilebilir ürün, idempotent),
deneme bilgisi, yapılandırılmış "hak doldu" gövdesi, eski tek sayaçtan göç, sayfa tablosu ve yönetici değişikliği.
Kendi sunucusunda hiçbiri uygulanmaz."""
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tests import test_plans
from tests.conftest import fixture_bytes
from tests.test_doc_reads import MONTH, _set_reads, _set_used, gemini  # noqa: F401 - fixture
from tests.test_plans import _user

hosted = test_plans.hosted


def _rc(event: dict):
    from app.api.main import app

    return TestClient(app).post("/api/billing/revenuecat", headers={"Authorization": "Bearer rc-gizli"},
                                json={"event": event})


def _pack(uid: int, product="okuma_100", txn="tx-1", kind="NON_RENEWING_PURCHASE"):
    return _rc({"type": kind, "app_user_id": str(uid), "product_id": product, "transaction_id": txn,
                "entitlement_ids": [], "id": f"ev-{txn}-{kind}"})


def _stmt(name="vakifbank_a.txt"):
    return [("files", (name.replace(".txt", ".pdf"), fixture_bytes(name), "application/pdf"))]


def _ai(c) -> dict:
    return c.get("/api/me/ai").json()


# ---------------------------------------------------------------- sınırlar

def test_limits_by_plan_and_meta_reads(hosted):
    from app import plans, system

    c, u = _user(hosted)
    p = c.get("/api/meta").json()["plan"]
    assert p["reads"]["belge"] == {"used": 0, "limit": 10, "left": 0 + 10} and p["reads"]["fis"]["limit"] == 30
    assert p["reads_by_plan"] == {"free": {"belge": 10, "fis": 30}, "premium": {"belge": 200, "fis": 300},
                                  "aile": {"belge": 200, "fis": 300}}
    assert [x["id"] for x in p["packs"]] == ["okuma_100", "okuma_300"] and p["family_seats"] == 4
    for plan, (b, f) in (("premium", (200, 300)), ("aile", (200, 300))):
        system.update_user(u["id"], plan=plan, plan_until=None)
        assert (plans.read_limit(system.user(u["id"]), "belge"), plans.read_limit(system.user(u["id"]), "fis")) == (b, f)


def test_trial_has_normal_limits_and_is_reported(hosted):
    """Denemede özel sınır yok: Premium'un normal hakları; deneme bilgisi meta'da ve hak doldu gövdesinde."""
    c, u = _user(hosted)
    exp = int((date.today().toordinal() - date(1970, 1, 1).toordinal() + 7) * 86400 * 1000)  # 7 gün sonra
    assert _rc({"type": "INITIAL_PURCHASE", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
                "product_id": "premium_monthly", "period_type": "TRIAL", "expiration_at_ms": exp}).status_code == 200
    p = c.get("/api/meta").json()["plan"]
    assert p["plan"] == "premium" and p["trial"] is True and p["trial_days_left"] in (6, 7)
    assert p["reads"]["belge"]["limit"] == 200 and p["reads"]["fis"]["limit"] == 300
    _set_reads(u["id"], belge=200)
    r = c.post("/api/statements/upload", files=_stmt()).json()[0]
    assert r["quota_full"] and r["trial"] is True and r["trial_days_left"] in (6, 7) and r["upgrade"] is False
    # deneme bitti, ücretli dönem (RENEWAL, period_type NORMAL): deneme bilgisi düşer
    _rc({"type": "RENEWAL", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"], "product_id": "premium_monthly",
         "period_type": "NORMAL", "expiration_at_ms": exp + 30 * 86400 * 1000})
    assert c.get("/api/meta").json()["plan"]["trial"] is False


def test_upgrade_yearly_default_off_and_admin_switch(hosted):
    from app import system

    c, u = _user(hosted)
    _rc({"type": "INITIAL_PURCHASE", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
         "product_id": "premium_monthly:monthly", "period_type": "NORMAL", "expiration_at_ms": 4102444800000})
    assert system.user(u["id"])["plan_product"] == "premium_monthly"
    _set_reads(u["id"], belge=200)
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["upgrade_yearly"] is False  # varsayılan kapalı
    assert hosted.put("/api/admin/plan-table", json={"upgrade_yearly": True}).json()["upgrade_yearly"] is True
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["upgrade_yearly"] is True
    # yıllık planda öneri yok
    _rc({"type": "RENEWAL", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"], "product_id": "premium_yearly",
         "expiration_at_ms": 4102444800000})
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["upgrade_yearly"] is False
    # PRODUCT_CHANGE yalnız bilgi: ürünü değiştirmez
    _rc({"type": "PRODUCT_CHANGE", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
         "product_id": "premium_yearly", "new_product_id": "premium_monthly", "expiration_at_ms": 4102444800000})
    assert system.user(u["id"])["plan_product"] == "premium_yearly"


# ---------------------------------------------------------------- ek okuma paketi

def test_pack_purchase_is_idempotent_and_leaves_subscription(hosted):
    from app import system

    c, u = _user(hosted)
    assert _pack(u["id"]).status_code == 200
    assert _ai(c)["extra"] == 100
    assert _pack(u["id"]).status_code == 200  # aynı işlem (webhook yeniden denemesi): yazılmaz
    assert _pack(u["id"], txn="tx-1", kind="INITIAL_PURCHASE").status_code == 200  # aynı işlem başka olay adıyla
    assert _ai(c)["extra"] == 100
    _pack(u["id"], "okuma_300", "tx-2")
    assert _ai(c)["extra"] == 400
    me = system.user(u["id"])
    assert (me["plan"] or "free", me["plan_source"], me["plan_renews"]) == ("free", None, None)  # abonelik değişmedi
    # işlem kimliği yoksa yazılmaz
    _rc({"type": "NON_RENEWING_PURCHASE", "app_user_id": str(u["id"]), "product_id": "okuma_100"})
    assert _ai(c)["extra"] == 400


def test_pack_used_after_monthly_for_both_kinds(hosted, gemini):
    c, u = _user(hosted)
    _pack(u["id"])
    _set_reads(u["id"], belge=10, fis=30)
    from app import system

    with system.connect() as cn:  # bakiyeyi 2'ye indir
        cn.execute("UPDATE read_credits SET balance = 2 WHERE user_id = ?", (u["id"],))
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["status"] == "saved"  # belge: ekten
    assert c.post("/api/receipts/parse", files={"file": ("f.jpg", b"fis-x", "image/jpeg")}).status_code == 200  # fiş: ekten
    ai = _ai(c)
    assert ai["extra"] == 0 and ai["belge"]["used"] == 10 and ai["fis"]["used"] == 30  # aylık sayaç sınırda kaldı
    r = c.post("/api/statements/upload", files=_stmt("vakifbank_b.txt")).json()[0]
    assert r["quota_full"] is True and r["extra_left"] == 0


def test_monthly_first_then_pack(hosted):
    c, u = _user(hosted)
    _pack(u["id"])
    _set_reads(u["id"], belge=9)
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["status"] == "saved"
    ai = _ai(c)
    assert ai["belge"]["used"] == 10 and ai["extra"] == 100  # aylık hak önce
    assert c.post("/api/statements/upload", files=_stmt("vakifbank_b.txt")).json()[0]["status"] == "saved"
    assert _ai(c)["extra"] == 99


def test_pack_survives_premium_and_month_change(hosted):
    from app import doc_reads, system

    c, u = _user(hosted)
    _pack(u["id"], "okuma_300", "tx-9")
    _rc({"type": "INITIAL_PURCHASE", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
         "product_id": "premium_yearly", "expiration_at_ms": 4102444800000})
    assert _ai(c)["extra"] == 300 and _ai(c)["belge"]["limit"] == 200  # Premium başlayınca ek hak kalır
    # gelecek ay: aylık sayaç sıfır, ek hak aynı
    assert doc_reads.used(system.user(u["id"]), "belge", "2099-01") == 0 and doc_reads.credits(u["id"]) == 300


def test_pack_refund(hosted):
    from app import system

    c, u = _user(hosted)
    _rc({"type": "INITIAL_PURCHASE", "app_user_id": str(u["id"]), "entitlement_ids": ["premium"],
         "product_id": "premium_monthly", "expiration_at_ms": 4102444800000})
    _pack(u["id"], txn="tx-r")
    with system.connect() as cn:
        cn.execute("UPDATE read_credits SET balance = 60 WHERE user_id = ?", (u["id"],))  # 40'ı harcanmış
    _pack(u["id"], txn="tx-r", kind="CANCELLATION")
    _pack(u["id"], txn="tx-r", kind="CANCELLATION")  # ikinci iade olayı: bir kez
    assert _ai(c)["extra"] == 0
    assert system.user(u["id"])["plan_renews"] == 1  # abonelik iptal sayılmadı


def test_pack_for_unknown_user_ignored(hosted):
    assert _pack(999999).status_code == 200
    from app import system

    with system.connect() as c:
        assert not c.execute("SELECT 1 FROM read_packs").fetchone()


# ---------------------------------------------------------------- tür tahmini (documents/parse)

def test_document_photo_counts_by_detected_type(hosted, monkeypatch):
    from app import receipt

    c, u = _user(hosted)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    monkeypatch.setattr(receipt, "parse_document", lambda *a, **k: {
        "type": "bill", "provider": "Örnek Elektrik", "amount": 410.0, "due_date": "2026-11-20", "category": "Faturalar"})
    assert c.post("/api/documents/parse", files={"file": ("fatura.jpg", b"fatura-foto", "image/jpeg")}).status_code == 200
    ai = _ai(c)
    assert (ai["belge"]["used"], ai["fis"]["used"]) == (1, 0)  # fotoğraf ama fatura: belge sayacı
    monkeypatch.setattr(receipt, "parse_document", lambda *a, **k: {
        "type": "receipt", "merchant": "Market", "total": 10.0, "items": [], "date": "2026-11-01"})
    assert c.post("/api/documents/parse", files={"file": ("fis.jpg", b"fis-foto", "image/jpeg")}).status_code == 200
    assert _ai(c)["fis"]["used"] == 1


# ---------------------------------------------------------------- eski tek sayaçtan göç

def test_split_migration_receipts_to_fis_and_idempotent(hosted):
    from app import db, doc_reads, system

    c, u = _user(hosted)
    _set_used(u["id"], 15)  # göç öncesi: bu ay 15 belge (tek sayaç)
    path = system.user_db_path(system.user(u["id"]))
    with db.use_space(path, system.user(u["id"])), db.connect():
        pass  # defter şeması
    conn = sqlite3.connect(path)
    now = date.today().isoformat() + " 10:00:00"
    for i in range(4):  # bu ay 4 fiş fotoğrafı saklanmış; geçen aydan 1 (sayılmaz)
        conn.execute("INSERT INTO stored_files (kind, name, size, created_at) VALUES ('receipts', ?, 1, ?)", (f"r{i}.jpg", now))
    conn.execute("INSERT INTO stored_files (kind, name, size, created_at) VALUES ('receipts', 'eski.jpg', 1, '2000-01-01')")
    conn.commit()
    conn.close()
    st = doc_reads.status(system.user(u["id"]))
    assert (st["belge"]["used"], st["fis"]["used"]) == (11, 4)
    _set_used(u["id"], 40)  # göçten sonra tek sayaç değişse de yeniden bölünmez
    st = doc_reads.status(system.user(u["id"]))
    assert (st["belge"]["used"], st["fis"]["used"]) == (11, 4)


def test_split_caps_receipts_at_total_and_member_all_belge(hosted):
    from app import doc_reads, system

    c, u = _user(hosted)
    _set_used(u["id"], 0)
    assert doc_reads.status(system.user(u["id"]))["belge"]["used"] == 0
    m = system.create_user("uye@ornek.com", None, member_of=u["id"])
    with system.connect() as cn:
        cn.execute("INSERT OR REPLACE INTO ai_usage (user_id, month, count) VALUES (?, ?, 7)", (m["id"], MONTH))
    st = doc_reads.status(system.user(m["id"]))
    assert (st["belge"]["used"], st["fis"]["used"]) == (7, 0)


# ---------------------------------------------------------------- sayfa tablosu

def test_plan_table_defaults_and_meta(hosted):
    from app import plans

    c, u = _user(hosted)
    p = c.get("/api/meta").json()["plan"]
    t = {r["key"]: r for r in p["table"]}
    assert t["zekat"]["tier"] == "free" and t["zekat"]["editable"] is False
    assert t["ortak_defter"]["tier"] == "aile" and t["fiyatlar"]["tier"] == "premium"
    assert {k for k, v in p["tiers"].items() if v == "free"} >= {"planlama", "tablo", "faturalar", "transferler",
                                                                  "odemeler", "tasarruf", "zekat"}
    assert p["features"]["tablo"] is True and p["features"]["istatistik"] is False
    assert set(plans.FEATURES) == set(p["tiers"])  # eski FEATURES: aynı anahtarlar, varsayılan tablo


def test_admin_changes_table_without_deploy(hosted):
    c, u = _user(hosted)
    assert c.get("/api/admin/plan-table").status_code == 403  # yönetici değil
    assert c.get("/api/year-summary").status_code == 402
    r = hosted.put("/api/admin/plan-table", json={"tiers": {"istatistik": "free", "tablo": "premium"}})
    assert r.status_code == 200
    rows = {x["key"]: x for x in r.json()["rows"]}
    assert rows["istatistik"]["tier"] == "free" and rows["istatistik"]["default"] == "premium"
    assert c.get("/api/year-summary").status_code == 200  # sunucu kapısı tablodan
    f = c.get("/api/meta").json()["plan"]["features"]
    assert f["istatistik"] is True and f["tablo"] is False
    # ortak defter Premium'a: Premium davet edebilir
    from app import system

    system.update_user(u["id"], plan="premium", plan_until=None)
    assert c.post("/api/book/invites", json={"note": "Eşim"}).status_code == 402
    hosted.put("/api/admin/plan-table", json={"tiers": {"ortak_defter": "premium"}})
    assert c.post("/api/book/invites", json={"note": "Eşim"}).status_code == 200
    # zekât değiştirilemez; bilinmeyen satır, geçersiz paket
    assert hosted.put("/api/admin/plan-table", json={"tiers": {"zekat": "premium"}}).status_code == 422
    assert hosted.put("/api/admin/plan-table", json={"tiers": {"yok": "free"}}).status_code == 422
    assert hosted.put("/api/admin/plan-table", json={"tiers": {"tablo": "altin"}}).status_code == 422
    # varsayılana dön
    r = hosted.put("/api/admin/plan-table", json={"reset": True}).json()
    assert all(x["tier"] == x["default"] for x in r["rows"])
    assert system.get("plan_table") is None


def test_table_drives_history_plan_and_budget_limits(hosted):
    c, u = _user(hosted)
    lim = c.get("/api/meta").json()["plan"]["limits"]
    assert (lim["history_months"], lim["plan_months"], lim["budgets"]) == (1, 1, 2)
    hosted.put("/api/admin/plan-table", json={"tiers": {"gecmis": "free", "uzun_plan": "free", "butce": "free"}})
    lim = c.get("/api/meta").json()["plan"]["limits"]
    assert (lim["history_months"], lim["plan_months"], lim["budgets"]) == (None, None, None)


def test_admin_users_list_shows_reads(hosted):
    c, u = _user(hosted)
    _set_reads(u["id"], belge=3, fis=5)
    row = next(x for x in hosted.get("/api/admin/users").json()["users"] if x["id"] == u["id"])
    assert row["reads"]["belge"]["used"] == 3 and row["reads"]["fis"]["used"] == 5 and row["reads"]["extra"] == 0


# ---------------------------------------------------------------- kendi sunucusu

def test_self_hosted_untouched(client):
    from app import auth, db, doc_reads, plans, system

    assert not plans.hosted()
    system.put("plan_table", '{"tablo": "aile", "istatistik": "aile"}')
    plans._cache = None
    p = client.get("/api/meta").json()["plan"]
    assert p["plan"] == "self" and all(p["features"].values()) and "table" not in p and "reads" not in p
    assert client.get("/api/year-summary").status_code == 200
    ai = client.get("/api/me/ai").json()
    assert ai["unit"] == "ai" and "belge" not in ai
    u = auth.create_user("veli", "veli-sifre-12345")
    with db.use_space(system.user_db_path(u), system.user(u["id"])):
        assert doc_reads.status(system.user(u["id"])) is None
        doc_reads.check(b"x", "fis")
        assert doc_reads.charge(b"x", "fis") is False
    with system.connect() as c:
        assert not c.execute("SELECT 1 FROM read_usage").fetchone()
    assert plans._apply_pack is not None and client.post("/api/billing/revenuecat", json={}).status_code in (401, 404)
    system.put("plan_table", None)
    plans._cache = None


@pytest.fixture(autouse=True)
def _fresh_table():
    from app import plans

    plans._cache = None
    yield
    plans._cache = None
