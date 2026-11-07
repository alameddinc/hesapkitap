"""Resmî fiyat kaynağı (varsayılan kapalı): Market Fiyatı biçimi → official_prices; "resmi" kaynağı ve gösterim şartı.
Test verisi uydurma (tests/fixtures/resmi_fiyat_ornek.json)."""
import json
import os
from datetime import date

from app import price_import, price_sources, system

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "resmi_fiyat_ornek.json")
DAY = date(2026, 11, 7)


def _data():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


def _rows():
    with system.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM official_prices ORDER BY chain, depot_id")]


def test_import_maps_chains_groups_and_skips_unknown():
    stats = price_import.import_data(_data(), DAY)
    assert stats == {"products": 3, "matched": 2, "rows": 4, "skipped_chain": 1, "unmatched": 1}
    rows = _rows()
    assert [(r["chain"], r["group_key"], r["price"], r["day"], r["depot_id"], r["discount"]) for r in rows] == [
        ("A101", "sut|1l", 29.0, "2026-11-07", "d-3", 1), ("BİM", "sut|1l", 31.5, "2026-11-07", "d-1", 0),
        ("BİM", "sut|1l", 32.5, "2026-11-07", "d-2", 0), ("Migros", "sut|500ml", 18.0, "2026-11-07", "d-5", 0)]
    assert rows[0]["title"] == "Örnekköy Günlük Süt 1 L" and rows[-1]["title"] == "Örnekköy Süt 500 ml"  # boy eklendi
    assert set(rows[0]) == {"id", "chain", "group_key", "title", "brand", "price", "unit_price", "day", "depot_id",
                            "discount", "region", "promotion", "depots", "change_7d", "change_30d"}  # konum / şube adı yok
    assert price_import.import_data(_data(), DAY)["rows"] == 4 and len(_rows()) == 4     # yeniden: üzerine yazar
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM community_prices").fetchone()[0] == 0      # topluluktan ayrı


def test_official_source_offers():
    price_import.import_data(_data(), date.today())
    got = price_sources.OfficialSource().fetch(["sut|1l", "sut|500ml"])
    by = {o["chain"]: o for o in got["sut|1l"]}
    assert by["BİM"]["price"] == 32.0 and by["A101"]["price"] == 29.0                      # şube ortancası
    assert by["BİM"]["source"] == "resmi" and by["BİM"]["attribution"] == price_import.ATTRIBUTION
    assert by["BİM"]["brand"] == "Örnekköy"
    assert set(price_sources.OfficialSource().search("süt")) == {"sut|1l", "sut|500ml"}


def test_disabled_by_default_and_needs_both_settings(client, monkeypatch):
    price_import.import_data(_data(), date.today())
    monkeypatch.delenv(price_import.URL_ENV, raising=False)
    monkeypatch.delenv(price_import.FILE_ENV, raising=False)
    assert client.get("/api/prices/groups", params={"q": "süt"}).json() == []            # varsayılan: kapalı
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi")
    assert "resmi" not in price_sources.enabled_names()                                   # adres / dosya yok
    assert client.get("/api/prices/groups", params={"q": "süt"}).json() == []
    monkeypatch.setenv(price_import.FILE_ENV, FIX)
    got = client.get("/api/prices/groups", params={"q": "1l süt"}).json()
    assert got[0]["key"] == "sut|1l"
    assert [(b["chain"], b["price"], b["source"]) for b in got[0]["best"]] == [("A101", 29.0, "resmi"), ("BİM", 32.0, "resmi")]
    assert got[0]["best"][0]["attribution"].startswith("Kaynak: Market Fiyatı") and got[0]["best"][0]["per_unit"] == "L"
    monkeypatch.delenv("HESAPKITAP_PRICE_SOURCES")
    assert client.get("/api/prices/groups", params={"q": "süt"}).json() == []             # listede yoksa kapalı


def test_cli(capsys):
    assert price_import.main(["x"]) == 2 or price_import.configured()
    assert price_import.main(["x", FIX]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 4


def test_day_and_number_parsing():
    assert price_import._day("07.11.2026 09:00", DAY) == "2026-11-07"
    assert price_import._day(None, DAY) == "2026-11-07"
    assert price_import._num("1.234,50") == 1234.5 and price_import._num(True) is None and price_import._num(12) == 12.0
