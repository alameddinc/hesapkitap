"""Piyasa fiyatı: günlük resmî özet (official_daily, gecelik içe aktarımda yazılır), gün gün bant
(/api/prices/market) ve kendi alımının ucuz / normal / pahalı karşılaştırması (history[].vs_market). Uydurma veri."""
from datetime import date, timedelta

import pytest

from app import community_prices as cp
from app import market_ref, price_import_bq, price_sources, system
from tests.test_price_import_bq import DAY, _after5, creds  # noqa: F401  (fixture)

TODAY = date.today()


def _d(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


@pytest.fixture
def resmi(monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    monkeypatch.setattr(price_sources, "official_configured", lambda: True)


def _daily(day, mn, med, mx, chain="BİM", region="İstanbul", g="sut|1l"):
    with system.connect() as c:
        c.execute("INSERT INTO official_daily (group_key, region, day, min, median, max, cheapest_chain, chains) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, 3)", (g, region, day, mn, med, mx, chain))


def _buy(client, day, price, store="Örnek Market", name="Süt 1L"):
    client.post("/api/entries", json={"entry_date": day, "description": store, "amount": price, "kind": "gider",
                                      "category": "Market & Gıda", "payment": "nakit",
                                      "items": [{"name": name, "qty": 1, "amount": price}]})


def _history(client):
    p = next(p for p in client.get("/api/prices").json()["products"] if p["group"] and p["group"]["key"] == "sut|1l")
    return {h["date"]: h.get("vs_market") for h in p["history"]}


def test_import_writes_daily_summary_and_prunes(creds):  # noqa: F811
    _daily(_d(401), 1, 1, 1)
    price_import_bq.run(_after5())
    with system.connect() as c:
        rows = {(r[0], r[1]): r[2:] for r in c.execute(
            "SELECT group_key, region, min, median, max, cheapest_chain, chains FROM official_daily WHERE day = ?", (DAY,))}
        old = c.execute("SELECT COUNT(*) FROM official_daily WHERE day < ?", (_d(400),)).fetchone()[0]
    assert rows[("sut|1l", "İzmir")] == (35.0, 47.45, 59.9, "BİM", 2)    # BİM 35, Migros en ucuzu 59,90
    assert rows[("sut|1l", "Ankara")] == (30.0, 31.5, 33.0, "Hakmar", 2)
    assert old == 0                                                      # 400 günden eski silinir
    price_import_bq.import_region(DAY, "İzmir")                          # yeniden: aynı özet
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM official_daily WHERE region = 'İzmir'").fetchone()[0] == 1


def test_market_band_endpoint(client, resmi):
    _daily(_d(10), 30, 34, 40)
    _daily(_d(5), 31, 35, 41, chain="A101")
    _daily(_d(5), 99, 99, 99, region="Ankara")                            # başka il: okunmaz
    with system.connect() as c:                                          # özetlenmemiş gün: official_prices'tan
        c.executemany("INSERT INTO official_prices (chain, group_key, title, price, day, region) VALUES (?, 'sut|1l', ?, ?, ?, ?)",
                      [("BİM", "Örnek Süt 1 L", 32.5, _d(0), "İstanbul"), ("Migros", "Örnek Süt 1 L", 45.0, _d(0), "İstanbul"),
                       ("Migros", "Başka Süt 1 L", 41.0, _d(0), "İstanbul")])
    r = client.get("/api/prices/market?group=sut|1l&days=90").json()
    assert r["region"] == "İstanbul" and r["attribution"].startswith("Kaynak: Market Fiyatı")
    assert [(x["day"], x["min"], x["median"], x["max"], x["cheapest_chain"]) for x in r["days"]] == [
        (_d(10), 30, 34, 40, "BİM"), (_d(5), 31, 35, 41, "A101"), (_d(0), 32.5, 36.75, 41.0, "BİM")]
    assert client.get("/api/prices/market?group=sut|1l&days=7").json()["days"][0]["day"] == _d(5)
    assert client.get("/api/prices/market?group=yok|1l").json()["days"] == []
    assert "price_market" in client.get("/api/meta").json()["capabilities"]


def test_official_ignored_when_source_off(client):
    _daily(_d(3), 30, 34, 40)
    assert client.get("/api/prices/market?group=sut|1l").json() == {"group": "sut|1l", "region": "İstanbul", "days": [],
                                                                     "attribution": None}


def test_vs_market_verdicts(client, resmi):
    _daily(_d(20), 30, 40, 50, chain="BİM")
    _daily(_d(12), 30, 40, 50, chain="A101")
    _daily(_d(2), 30, 40, 50)
    _buy(client, _d(20), 36.0)        # %10 altında: ucuz
    _buy(client, _d(10), 46.0)        # 2 gün önceki referans: %15 pahalı
    _buy(client, _d(0), 41.0)         # 2 gün önceki: %2,5 normal
    _buy(client, _d(40), 10.0)        # yakında referans yok
    h = _history(client)
    assert h[_d(20)] == {"median": 40, "min": 30, "max": 50, "pct": -10.0, "verdict": "ucuz", "cheapest_chain": "BİM",
                         "day": _d(20), "source": "resmi"}
    assert (h[_d(10)]["verdict"], h[_d(10)]["pct"], h[_d(10)]["cheapest_chain"]) == ("pahali", 15.0, "A101")
    assert (h[_d(0)]["verdict"], h[_d(0)]["day"]) == ("normal", _d(2))
    assert h[_d(40)] is None
    assert market_ref.compare(42.0, {"median": 40, "min": 30, "max": 50, "cheapest_chain": "BİM", "day": "x",
                                     "source": "resmi"})["verdict"] == "pahali"   # tam +%5: pahalı


def test_vs_market_from_community_days(client):
    for uid, p in ((1, 38.0), (2, 40.0), (3, 42.0)):
        cp.contribute(uid, "A101", None, [{"name": "Süt 1L", "qty": 1, "amount": p}], _d(1))
    cp.contribute(4, "BİM", None, [{"name": "Süt 1L", "qty": 1, "amount": 20.0}], _d(1))   # tek kişi: sayılmaz
    _buy(client, _d(1), 46.0)
    v = _history(client)[_d(1)]
    assert (v["median"], v["cheapest_chain"], v["source"], v["verdict"]) == (40.0, "A101", "community", "pahali")
    r = client.get("/api/prices/market?group=sut|1l").json()
    assert [(x["day"], x["source"], x["chains"]) for x in r["days"]] == [(_d(1), "community", 1)] and r["attribution"] is None
