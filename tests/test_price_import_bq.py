"""BigQuery → official_prices köprüsü ve Bölgem. Ağ yok: BigQuery ve Google token uçları sahte istemciyle; satırlar
uydurma (ürün adları "Örnekköy …", fiyatlar rastgele değil ama gerçek değil)."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import bq, prefs, price_import_bq, price_sources, provinces, system
from tests.test_book import owner  # noqa: F401  (fixture)

TODAY = date.today()
DAY = TODAY.isoformat()


class Resp:
    def __init__(self, status: int, data: dict):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def _schema(*cols):
    return {"fields": [{"name": n, "type": t} for n, t in cols]}


OFFER_COLS = (("market", "STRING"), ("product_id", "STRING"), ("price", "FLOAT"), ("depots", "INTEGER"),
              ("unit_price", "FLOAT"), ("title", "STRING"), ("brand", "STRING"), ("volume_weight", "STRING"),
              ("main_category", "STRING"), ("discount", "BOOLEAN"), ("promotion_text", "STRING"),
              ("change_7d", "FLOAT"), ("change_30d", "FLOAT"))


def _row(*vals):
    return {"f": [{"v": None if v is None else (str(v).lower() if isinstance(v, bool) else str(v))} for v in vals]}


# bölge → BigQuery offers satırları (uydurma)
OFFERS = {
    "İzmir": [("tarim_kredi", "p1", 59.9, 3, 59.9, "Örnekköy Günlük Süt 1 L", "Örnekköy", "1 L", "Süt", True, "2 al 1 öde", 8.0, 12.5),
              ("tarim_kredi", "p2", 61.0, 1, 61.0, "Örnekköy Süt", "Örnekköy", "1 L", "Süt", False, None, None, None),
              ("bim", "p3", 35.0, 5, 35.0, "Örnekköy Süt 1 L", "Örnekköy", "1 L", "Süt", False, None, 1.0, None),
              ("ornekbakkal", "p4", 10.0, 1, 10.0, "Örnekköy Süt 1 L", None, "1 L", "Süt", False, None, None, None),
              ("a101", "p5", 40.0, 1, 40.0, "Zzqx Belirsiz Ürün", None, None, "Diğer", False, None, None, None)],
    "İstanbul": [("carrefour", "p6", 64.5, 2, 64.5, "Örnekköy Süt 1 L", "Örnekköy", "1 L", "Süt", False, None, -4.0, None),
                 ("tarim_kredi", "p1", 99.0, 2, 99.0, "Örnekköy Günlük Süt 1 L", "Örnekköy", "1 L", "Süt", False, None, None, None)],
    "Ankara": [("hakmar", "p7", 30.0, 1, 30.0, "Örnekköy Süt 1 L", "Örnekköy", "1 L", "Süt", False, None, None, None),
               ("sok", "p8", 33.0, 4, 33.0, "Örnekköy Süt 1 L", "Örnekköy", "1 L", "Süt", False, None, None, None)],
}


class FakeHTTP:
    """Google token + BigQuery jobs.query / getQueryResults. İlk offers sorgusu "iş bitmedi" der, sonra iki sayfa."""

    def __init__(self, runs=(("İzmir", "success"), ("İstanbul", "success"), ("Ankara", "success"), ("Muş", "failed"))):
        self.calls: list[tuple] = []
        self.runs = [r for r, st in runs if st == "success"]
        self.jobs: dict[str, list] = {}
        self.fail: int | None = None

    def post(self, url, data=None, **kw):
        self.calls.append(("token", url, data))
        assert data["grant_type"].endswith("jwt-bearer") and data["assertion"].count(".") == 2
        return Resp(200, {"access_token": "tok-1", "expires_in": 3600})

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        self.calls.append((method, url, json, params))
        assert headers["Authorization"] == "Bearer tok-1"
        if self.fail:
            return Resp(self.fail, {"error": {"message": "Access Denied: örnek"}})
        if method == "POST":
            assert json["useLegacySql"] is False and json["location"] == "EU" and json["parameterMode"] == "NAMED"
            p = {q["name"]: q["parameterValue"]["value"] for q in json["queryParameters"]}
            assert "run_date" in json["query"]                               # bölüm süzgeci her sorguda
            if "run_log" in json["query"]:
                return Resp(200, {"jobComplete": True, "jobReference": {"jobId": "j0", "location": "EU"},
                                  "schema": _schema(("run_date", "STRING"), ("region", "STRING")),
                                  "rows": [_row(DAY, r) for r in self.runs]})
            assert {"d", "d7", "d30", "region"} <= set(p)
            assert p["d7"] == (date.fromisoformat(p["d"]) - timedelta(days=7)).isoformat()
            rows = [_row(*r) for r in OFFERS.get(p["region"], [])]
            job = f"j-{p['region']}"
            self.jobs[job] = [rows[:2], rows[2:]]
            return Resp(200, {"jobComplete": False, "jobReference": {"jobId": job, "location": "EU"}})
        job = url.rsplit("/", 1)[-1]
        pages = self.jobs[job]
        if "pageToken" not in params:
            return Resp(200, {"jobComplete": True, "schema": _schema(*OFFER_COLS), "rows": pages[0],
                              **({"pageToken": "t2"} if pages[1] else {})})
        return Resp(200, {"jobComplete": True, "schema": _schema(*OFFER_COLS), "rows": pages[1]})


@pytest.fixture()
def creds(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    p = tmp_path / "bq.json"
    p.write_text(json.dumps({"type": "service_account", "client_email": "okuyucu@ornek.iam.gserviceaccount.com",
                             "private_key": pem, "private_key_id": "k1", "token_uri": "https://oauth2.googleapis.com/token"}))
    monkeypatch.setenv(bq.CRED_ENV, str(p))
    bq.reset_cache()
    fake = FakeHTTP()
    monkeypatch.setattr(bq, "HTTP_CLIENT", fake)
    yield fake
    bq.reset_cache()


def _after5():
    return datetime.combine(TODAY, datetime.min.time(), tzinfo=timezone.utc).replace(hour=6)


def _rows(region=None):
    with system.connect() as c:
        sql = "SELECT * FROM official_prices" + (" WHERE region = ?" if region else "") + " ORDER BY chain, title"
        return [dict(r) for r in c.execute(sql, (region,) if region else ())]


def test_disabled_without_credentials(monkeypatch):
    monkeypatch.delenv(bq.CRED_ENV, raising=False)
    bq.reset_cache()
    assert not price_import_bq.enabled() and price_import_bq.tick(_after5()) is None
    monkeypatch.setenv(bq.CRED_ENV, "/yok/bq.json")
    assert not bq.configured()


def test_query_params_pagination_and_token_cache(creds):
    got = bq.query("SELECT 1 FROM x WHERE run_date = @d", {"d": TODAY, "region": "İzmir", "d7": TODAY - timedelta(days=7),
                                                            "d30": TODAY - timedelta(days=30)})
    assert len(got) == 5 and got[0] == {"market": "tarim_kredi", "product_id": "p1", "price": 59.9, "depots": 3,
                                        "unit_price": 59.9, "title": "Örnekköy Günlük Süt 1 L", "brand": "Örnekköy",
                                        "volume_weight": "1 L", "main_category": "Süt", "discount": True,
                                        "promotion_text": "2 al 1 öde", "change_7d": 8.0, "change_30d": 12.5}
    assert got[1]["change_7d"] is None and got[1]["discount"] is False
    post = next(c for c in creds.calls if c[0] == "POST")
    types = {q["name"]: q["parameterType"]["type"] for q in post[2]["queryParameters"]}
    assert types == {"d": "DATE", "region": "STRING", "d7": "DATE", "d30": "DATE"}
    assert [c[0] for c in creds.calls] == ["token", "POST", "GET", "GET"]               # iş bekleme + 2. sayfa
    bq.query("SELECT 1 FROM run_log WHERE run_date = @d", {"d": TODAY})
    assert sum(c[0] == "token" for c in creds.calls) == 1                               # belirteç önbellekte


def test_query_error_is_logged_without_key(creds, caplog):
    creds.fail = 403
    with pytest.raises(bq.BQError) as e:
        bq.query("SELECT 1 FROM x WHERE run_date = @d", {"d": TODAY})
    assert "403" in str(e.value) and "Access Denied" in str(e.value)
    st = price_import_bq.run(_after5())
    assert st["error"] and price_import_bq.status()["last_error"].startswith("BigQuery 403")
    assert "PRIVATE KEY" not in caplog.text and "tok-1" not in caplog.text


def test_tick_imports_regions_idempotent_and_waits(creds):
    early = _after5().replace(hour=4)
    assert price_import_bq.tick(early) is None and not creds.calls                      # 05:00 UTC'den önce yok
    out = price_import_bq.tick(_after5())
    assert sorted(r[1] for r in out["imported"]) == ["Ankara", "İstanbul", "İzmir"] and out["left"] == 0  # Muş: failed
    iz = _rows("İzmir")
    assert [(r["chain"], r["group_key"], r["price"], r["discount"], r["change_7d"], r["depots"]) for r in iz] == [
        ("BİM", "sut|1l", 35.0, 0, 1.0, 5), ("Tarım Kredi", "sut|1l", 59.9, 1, 8.0, 3), ("Tarım Kredi", "sut|1l", 61.0, 0, None, 1)]
    assert iz[1]["promotion"] == "2 al 1 öde" and iz[2]["title"] == "Örnekköy Süt 1 L"   # adda boy yoksa eklenir
    assert iz[0]["day"] == DAY and iz[0]["depot_id"] == ""
    with system.connect() as c:
        assert c.execute("SELECT name FROM product_group_queue").fetchall()             # belirsiz ad: kuyrukta
        imp = {r[0]: r[1] for r in c.execute("SELECT region, rows FROM official_imports WHERE run_date = ?", (DAY,))}
    assert imp == {"İzmir": 3, "İstanbul": 2, "Ankara": 2}
    n = len(creds.calls)
    assert price_import_bq.tick(_after5() + timedelta(minutes=5)) is None and len(creds.calls) == n  # sırası değil
    out2 = price_import_bq.tick(_after5() + timedelta(minutes=31))
    assert out2 == {"imported": [], "left": 0}                                          # hepsi alınmış: offers yok
    assert not any(c[0] == "POST" and "offers" in c[2]["query"] for c in creds.calls[n:])
    price_import_bq.import_region(DAY, "İzmir")                                         # elle yeniden: aynı sonuç
    assert _rows("İzmir") == [{**r, "id": _rows("İzmir")[i]["id"]} for i, r in enumerate(iz)]
    st = price_import_bq.status()
    assert st["last_day"] == DAY and st["regions"] == ["Ankara", "İstanbul", "İzmir"] and st["rows"] == 7
    assert st["last_error"] is None and st["configured"] is True


def test_cap_per_tick_resumes(creds, monkeypatch):
    monkeypatch.setattr(price_import_bq, "MAX_REGIONS_PER_TICK", 2)
    out = price_import_bq.tick(_after5())
    assert len(out["imported"]) == 2 and out["left"] == 1
    assert price_import_bq.tick(_after5() + timedelta(minutes=6))["imported"][0][1] == "Ankara"  # kısa bekleme


def test_retention_prunes_old_days(creds):
    with system.connect() as c:
        c.execute("INSERT INTO official_prices (chain, group_key, title, price, day, region) VALUES "
                  "('BİM', 'sut|1l', 'Eski', 1.0, ?, 'İzmir')", ((TODAY - timedelta(days=40)).isoformat(),))
    price_import_bq.import_region(DAY, "Ankara")
    assert all(r["title"] != "Eski" for r in _rows())


def test_offers_sql_filters_partitions():
    sql = price_import_bq.offers_sql()
    assert "run_date = @d" in sql and "run_date IN (@d7, @d30)" in sql and "`hesapkitapproject.marketfiyati.offers`" in sql


def _seed(creds):
    price_import_bq.run(_after5())


def test_source_by_region(creds):
    _seed(creds)
    by = lambda region: {o["chain"]: o for o in price_sources.OfficialSource(region).fetch(["sut|1l"])["sut|1l"]}  # noqa: E731
    iz = by("İzmir")
    assert iz["Tarım Kredi"]["price"] == 59.9 and iz["Tarım Kredi"]["region"] == "İzmir" and iz["Tarım Kredi"]["discount"] is True
    assert iz["Tarım Kredi"]["change_7d"] == 8.0 and iz["Tarım Kredi"]["promotion"] == "2 al 1 öde"
    assert iz["BİM"]["region"] == "ulusal" and iz["BİM"]["price"] == 35.0               # ilin satırı
    assert iz["ŞOK"]["region"] == "ulusal" and iz["ŞOK"]["price"] == 33.0                # başka ilden (ulusal)
    assert iz["CarrefourSA"]["region"] == "İstanbul" and iz["CarrefourSA"]["change_7d"] == -4.0  # yedek il
    assert "Hakmar" not in iz                                                           # yalnız Ankara'da
    assert by("Konya")["Hakmar"]["region"] == "Ankara"                                  # Konya → Ankara
    ist = by("İstanbul")
    assert ist["Tarım Kredi"]["price"] == 99.0 and "discount" not in ist["Tarım Kredi"]
    assert by(None)["Tarım Kredi"]["region"] == "İstanbul"                                   # seçilmemiş: İstanbul
    assert by("Muş")["Tarım Kredi"]["region"] == "İstanbul"                                  # bilinmeyen yedek: İstanbul


def test_region_alias_default_kadikoy(creds):
    with system.connect() as c:
        c.execute("INSERT INTO official_prices (chain, group_key, title, price, day, region) VALUES "
                  "('Tarım Kredi', 'sut|1l', 'Örnekköy Süt 1 L', 50.0, ?, 'DEFAULT_KADIKOY')", (DAY,))
    o = price_sources.OfficialSource("İstanbul").fetch(["sut|1l"])["sut|1l"][0]
    assert o["region"] == "İstanbul" and o["price"] == 50.0


def test_provinces():
    assert len(provinces.PROVINCES) == 81 == len(set(provinces.PROVINCES))
    assert provinces.valid(" İzmir ") == "İzmir" and provinces.valid("Izmir") is None
    assert provinces.fallback_chain("Manisa") == ["Manisa", "İzmir", "İstanbul"]
    assert provinces.fallback_chain(None) == ["İstanbul"]
    assert all(v in provinces.PROVINCES and k in provinces.PROVINCES for k, v in provinces.FALLBACK.items())


def test_region_pref_api_and_offers(owner, creds, monkeypatch):  # noqa: F811
    assert owner.get("/api/meta").json()["ui"]["region"] is None
    assert owner.put("/api/me/ui", json={"region": "Atlantis"}).status_code == 422
    assert owner.put("/api/me/ui", json={"region": "İzmir"}).json()["region"] == "İzmir"
    assert "region" not in owner.put("/api/me/ui", json={"simple": False}).json()       # gönderilmezse dokunulmaz
    assert owner.get("/api/meta").json()["ui"]["region"] == "İzmir"
    assert "price_region" in owner.get("/api/meta").json()["capabilities"]
    _seed(creds)
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    got = owner.get("/api/prices/groups", params={"q": "sut|1l"}).json()
    best = {b["chain"]: b for b in got[0]["best"]}
    assert best["Tarım Kredi"]["region"] == "İzmir" and best["BİM"]["region"] == "ulusal"
    assert [b["price"] for b in got[0]["best"]] == sorted(b["price"] for b in got[0]["best"])  # ucuz önce
    assert owner.put("/api/me/ui", json={"region": None}).json()["region"] is None
    assert owner.get("/api/meta").json()["ui"]["region"] is None


def test_admin_app_regions_and_status(owner, client):  # noqa: F811
    from fastapi.testclient import TestClient

    from app.api.main import app

    ids = [system.create_user(f"kisi{i}", None)["id"] for i in range(5)]
    for uid in ids[:3]:
        prefs.set_region(uid, "İzmir")
    prefs.set_region(ids[3], "Ankara")
    prefs.set_region(ids[4], "İzmir")
    system.update_user(ids[4], disabled=1)                                              # kapalı hesap sayılmaz
    assert owner.get("/api/admin/app-regions").json() == [{"region": "İzmir", "users": 3}]
    st = owner.get("/api/admin/official-prices").json()
    assert st["configured"] is False and st["last_day"] is None and st["rows"] == 0
    anon = TestClient(app)
    assert anon.get("/api/admin/app-regions").status_code in (401, 403)
    system.delete_user(ids[0])
    assert system.get(f"region:{ids[0]}") is None


def test_migrates_old_official_prices_table(tmp_path, monkeypatch):
    import sqlite3

    p = tmp_path / "eski.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE official_prices (id INTEGER PRIMARY KEY, chain TEXT NOT NULL, group_key TEXT NOT NULL, "
              "title TEXT NOT NULL, brand TEXT, price REAL NOT NULL, unit_price REAL, day TEXT NOT NULL, "
              "depot_id TEXT NOT NULL DEFAULT '', discount INTEGER NOT NULL DEFAULT 0, UNIQUE (chain, depot_id, title, day))")
    c.execute("INSERT INTO official_prices (chain, group_key, title, price, day, depot_id) VALUES "
              "('BİM', 'sut|1l', 'Örnekköy Süt 1 L', 30.0, '2026-11-01', 'd-1')")
    c.commit()
    c.close()
    monkeypatch.setattr(system, "SYSTEM_DB", str(p))
    with system.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(official_prices)")}
        rows = [dict(r) for r in c.execute("SELECT * FROM official_prices")]
    assert {"region", "change_7d", "change_30d", "depots", "promotion"} <= cols
    assert rows[0]["region"] == "" and rows[0]["price"] == 30.0 and rows[0]["depot_id"] == "d-1"
