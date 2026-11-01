"""Hızda küçük kazançlar (C12): sonuç değişmeden daha az iş — önbellek, tek hesap, GZip ayarı, ilk istekte yazmama."""
import gzip
from datetime import date

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app import db, finance


def _seed(client):
    """Uydurma veri: iki ay gider (biri iade, biri bölünmüş, biri taksitli), sabit kalem, bütçe."""
    for day, desc, amt, cat in (("2026-09-03", "Örnek Market", 450.5, "Market & Gıda"),
                                ("2026-09-10", "Örnek Kafe", 120, "Kafe & Restoran"),
                                ("2026-10-02", "Örnek Market", 300, "Market & Gıda"),
                                ("2026-10-05", "Örnek Eczane", 89.9, "Sağlık")):
        client.post("/api/entries", json={"entry_date": day, "description": desc, "amount": amt, "kind": "gider",
                                          "category": cat, "payment": "nakit"})
    client.post("/api/entries", json={"entry_date": "2026-10-07", "description": "Örnek Giyim", "amount": 100,
                                      "kind": "gider", "payment": "kredi_karti", "installments": 3, "category": "Giyim"})
    client.post("/api/recurring", json={"description": "Kira", "amount": 20000, "kind": "gider",
                                        "category": "Kira & Aidat", "start_month": "2026-01"})
    client.put("/api/budgets", json={"category": "Market & Gıda", "monthly_limit": 1000})


def test_category_spend_matches_summary(client, upload):
    upload()
    _seed(client)
    for m in ("2026-08", "2026-09", "2026-10", "2026-11"):
        assert finance.category_spend(m) == finance.summary(m)["by_category"]
        assert finance.budgets(m) == finance.summary(m)["budgets"]


def test_budgets_last_month_from_category_spend(client, upload):
    upload()
    _seed(client)
    ref = finance.reference_month()
    want = {g["name"]: g["amount"] for g in finance.summary(ref)["by_category"]}
    assert client.get("/api/budgets").json()["last_month"] == want


def test_plan_memo_invalidated_by_write_and_isolated(client):
    _seed(client)
    a = client.get("/api/plan", params={"months": 6}).json()
    a["months"][0]["lines"].clear()  # istemci tarafı değişiklik önbelleği bozmaz
    b = client.get("/api/plan", params={"months": 6}).json()
    assert b["months"][0]["lines"]
    client.post("/api/recurring", json={"description": "Spor", "amount": 750, "kind": "gider", "category": "Sağlık",
                                        "start_month": "2026-01"})
    c = client.get("/api/plan", params={"months": 6}).json()
    assert any(l["description"] == "Spor" for l in c["months"][0]["lines"])


def test_plan_installments_and_confirmed_recurring_unchanged(client, tmpdb):
    # döngü dışına alınan taramalar: taksit parçaları ve sabit kalemin o ayki kaydı aynı kalır
    m = finance.current_period()
    rid = client.post("/api/recurring", json={"description": "Kira", "amount": 20000, "kind": "gider",
                                              "category": "Kira & Aidat", "start_month": "2026-01"}).json()["id"]
    client.post(f"/api/recurring/{rid}/confirm", json={"month": m, "amount": 21000})
    client.post("/api/entries", json={"entry_date": f"{m}-07", "description": "Örnek Giyim", "amount": 100,
                                      "kind": "gider", "payment": "kredi_karti", "installments": 3, "category": "Giyim"})
    p = finance.plan(4)["months"]
    rent = [next(l for l in x["lines"] if l["description"] == "Kira") for x in p]
    assert (rent[0]["amount"], rent[0]["confirmed"]) == (21000, True)
    assert all((r["amount"], r["confirmed"]) == (20000, False) for r in rent[1:])
    parts = [[l for l in x["lines"] if l.get("type") == "tek_seferlik" and "Örnek Giyim" in l["description"]] for x in p]
    assert [len(x) for x in parts] == [1, 1, 1, 0]
    assert sum(x[0]["amount"] for x in parts[:3]) == 100


def test_available_months_memo_invalidated(client):
    client.post("/api/entries", json={"entry_date": "2025-03-04", "description": "Örnek", "amount": 10, "kind": "gider"})
    assert "2025-03" in client.get("/api/meta").json()["months"]
    client.post("/api/entries", json={"entry_date": "2024-07-04", "description": "Örnek", "amount": 10, "kind": "gider"})
    assert "2024-07" in client.get("/api/meta").json()["months"]


def test_first_summary_does_not_write(client):
    # tek seferlik hedef → kumbara taşıması defter açılışında, bayram/fitre kişi sayısı yalnız okuma: ilk Özet
    # isteği okuma önbelleğini düşürmesin
    before = db._WRITES.get(db.db_path(), 0)
    assert client.get("/api/summary").status_code == 200
    assert db._WRITES.get(db.db_path(), 0) == before


def test_targets_migrated_at_open(tmpdb):
    with db.connect() as c:
        c.execute("DELETE FROM settings WHERE key = 'targets_migrated'")
        c.execute("INSERT INTO saving_targets (name, amount, deadline, start_month, already) "
                  "VALUES ('Örnek hedef', 5000, '2027-06-01', '2026-01', 1200)")
    db.init_db()
    jars = db.list_jars()
    assert [(j["name"], j["goal"], j["balance"]) for j in jars] == [("Örnek hedef", 5000, 1200)]
    db.init_db()  # ikinci kez taşınmaz
    assert len(db.list_jars()) == 1


def test_list_accounts_computes_debt_rows_once(client, tmpdb, monkeypatch):
    for name in ("Örnek Banka A", "Örnek Banka B", "Örnek Banka C"):
        client.post("/api/accounts", json={"name": name, "kind": "banka", "balance": 1000, "as_of": "2026-10-01"})
    acc = tmpdb.list_accounts()[1]
    did = client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 200,
                                          "debt_date": "2026-10-01"}).json()["id"]
    client.post(f"/api/debts/{did}/pay", json={"paid_at": "2026-10-05", "account_id": acc["id"]})
    calls = []
    real = db.debt_rows
    monkeypatch.setattr(db, "debt_rows", lambda *a, **k: calls.append(1) or real(*a, **k))
    cur = {a["name"]: a["current"] for a in tmpdb.list_accounts()}
    assert len(calls) == 1
    assert cur == {"Örnek Banka A": 1000, "Örnek Banka B": 800, "Örnek Banka C": 1000}


def test_setup_required_cached_after_users(monkeypatch, tmp_path):
    from app import auth, system

    monkeypatch.delenv("HESAPKITAP_NO_AUTH")
    monkeypatch.setattr(system, "SYSTEM_DB", str(tmp_path / "kurulum" / "system.db"))
    assert auth.setup_required()  # kullanıcı yok: kurulum gerekli, hatırlanmaz
    assert auth.setup_required()
    system.create_user("ornek", None, role="admin", main_db=True)
    assert not auth.setup_required()
    inits = []
    monkeypatch.setattr(system, "init", lambda: inits.append(1))
    for _ in range(5):
        assert not auth.setup_required()
    assert inits == []  # sistem şeması/göçleri her istekte yeniden çalışmıyor
    # başka sistem veritabanı (yeni kurulum): yeniden bakılır
    monkeypatch.setattr(system, "SYSTEM_DB", str(tmp_path / "baska" / "system.db"))
    (tmp_path / "baska").mkdir()
    monkeypatch.setattr(system, "init", lambda: None)
    monkeypatch.setattr(system, "has_users", lambda: False)
    assert auth.setup_required()


def _gzip_app():
    from app.compress import GZip

    a = FastAPI()
    a.add_middleware(GZip)
    big = ("örnek metin " * 500).encode()

    @a.get("/json")
    def j():
        return {"t": "örnek " * 500}

    @a.get("/small")
    def s():
        return {"ok": True}

    @a.get("/f/{ctype:path}")
    def f(ctype: str):
        return Response(big, media_type=ctype)

    return TestClient(a)


def test_gzip_compresses_big_text_only():
    c = _gzip_app()
    h = {"Accept-Encoding": "gzip"}
    r = c.get("/json", headers=h)
    assert r.headers.get("content-encoding") == "gzip" and "Accept-Encoding" in r.headers.get("vary", "")
    assert c.get("/small", headers=h).headers.get("content-encoding") is None          # < 1 KB
    for ct in ("image/svg+xml", "text/html; charset=utf-8", "application/javascript"):
        assert c.get(f"/f/{ct}", headers=h).headers.get("content-encoding") == "gzip", ct
    for ct in ("image/jpeg", "image/webp", "application/pdf", "application/zip", "application/octet-stream",
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "font/woff2"):
        r = c.get(f"/f/{ct}", headers=h)
        assert r.headers.get("content-encoding") is None and r.content.startswith("örnek".encode()), ct
    assert c.get("/json", headers={"Accept-Encoding": "identity"}).headers.get("content-encoding") is None  # istemedi


def test_gzip_level_6_on_api():
    from app import compress
    from app.api.main import app

    mw = next(m for m in app.user_middleware if m.cls is compress.GZip)
    assert (mw.kwargs or {}).get("compresslevel", compress.LEVEL) == 6 and compress.MINIMUM_SIZE == 1000
    data = ("x" * 50 + "örnek ") * 2000
    assert len(gzip.compress(data.encode(), 6)) < len(data)  # sağlık kontrolü


def test_summary_plan_uses_memo_same_day(client):
    _seed(client)
    a = finance.plan(3, today=date(2026, 10, 20))
    assert finance.plan(3, today=date(2026, 10, 20)) == a


def test_zakat_reads_without_tables_and_old_schema(tmpdb):
    from app import zakat

    # zekât hiç açılmadı: bayram/fitre 1 kişi, yaklaşan zekât günü yok, tablo kurulmadı
    assert zakat.holy(date(2026, 10, 20))["fitre"]["people"] == 1 and zakat.upcoming_checks(date(2026, 10, 20)) == []
    with db.connect() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'zakat_people'").fetchone()
        # eski şema: is_minor / check_date kolonları yok (yıl başı pencere dışında: fiyat istenmez)
        c.execute("CREATE TABLE zakat_people (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                  "is_self INTEGER NOT NULL DEFAULT 0, hawl_start DATE, note TEXT)")
        c.execute("INSERT INTO zakat_people (name, is_self, hawl_start) VALUES ('Ben', 1, '2025-01-01')")
    assert zakat.upcoming_checks(date(2026, 10, 20)) == []  # kolonlar eklenir, hata yok
    with db.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(zakat_people)")}
    assert {"is_minor", "check_date"} <= cols
