"""Eklenebilir fiyat kaynakları: kayıt, ortam değişkeniyle seçim, birleştirme kuralı, resmî kaynak eklenebilirliği."""
from datetime import date, timedelta

from app import community_prices as cp
from app import price_sources as ps

TODAY = date.today()


def _buy(client, store, name, price, day=None, brand=None):
    item = {"name": name, "qty": 1, "amount": price} | ({"brand": brand} if brand else {})
    client.post("/api/entries", json={"entry_date": (day or TODAY).isoformat(), "description": store, "amount": price,
                                      "kind": "gider", "category": "Market & Gıda", "payment": "nakit", "items": [item]})


def test_registry_and_env_selection(monkeypatch):
    assert {"own", "community"} <= set(ps.registered())
    assert ps.enabled_names() == ["own", "community"]
    monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "community,olmayan")
    assert ps.enabled_names() == ["community"]
    assert [s.name for s in ps.open_sources()] == ["community"]


def test_own_and_community_sources(client):
    _buy(client, "A101", "Torku Süt 1L", 36, brand="Torku")
    _buy(client, "A101", "Torku Süt 1L", 37)                       # son fiyat
    _buy(client, "Örnek Bakkal", "Süt 1 L", 40)                    # zincir değil: işyeri adıyla
    for uid, p in ((1, 33), (2, 34), (3, 35)):
        cp.contribute(uid, "Migros", None, [{"name": "Süt 1L", "qty": 1, "amount": p}], TODAY.isoformat())
    own = ps.OwnSource()
    got = sorted((o["chain"], o["price"]) for o in own.fetch(["sut|1l"])["sut|1l"])
    assert got == [("A101", 37.0), ("Örnek Bakkal", 40.0)]
    assert own.search("süt") == {"sut|1l": "Süt · 1 L"} and own.search("ekmek") == {}
    com = ps.CommunitySource()
    assert com.fetch(["sut|1l"]) == {"sut|1l": [{"chain": "Migros", "price": 34.0, "source": "community", "n": 3,
                                                 "window_days": 7}]}
    assert com.search("süt") == {"sut|1l": "Süt · 1 L"}


def test_merge_prefers_fresh_own_then_community():
    old = (TODAY - timedelta(days=30)).isoformat()
    offers = [{"chain": "A101", "price": 30.0, "source": "own", "date": old},
              {"chain": "A101", "price": 34.0, "source": "community", "n": 3},
              {"chain": "BİM", "price": 40.0, "source": "community", "n": 4},
              {"chain": "BİM", "price": 39.0, "source": "own", "date": TODAY.isoformat()},
              {"chain": "Migros", "price": 45.0, "source": "own", "date": old}]
    out = ps.merge(offers, today=TODAY)
    assert [(o["chain"], o["price"], o["source"]) for o in out] == [
        ("A101", 34.0, "community"), ("BİM", 39.0, "own"), ("Migros", 45.0, "own")]
    assert len(ps.merge(offers, limit=2, today=TODAY)) == 2


def test_official_source_can_be_plugged_in(client, monkeypatch):
    """İzin alındıktan sonra eklenecek resmî kaynak: yalnız register + ortam değişkeni (bu testte ağ çağrısı yok)."""
    class Official:
        name = "resmi_test"

        def fetch(self, group_keys):
            return {k: [{"chain": "Örnek Zincir", "price": 30.0, "source": self.name}] for k in group_keys if k == "sut|1l"}

        def search(self, query):
            return {"sut|1l": "Süt · 1 L"}

    ps.register(Official)
    try:
        _buy(client, "A101", "Torku Süt 1L", 36)
        assert "resmi_test" not in ps.enabled_names()                 # kayıtlı ama açılmadı
        monkeypatch.setenv("HESAPKITAP_PRICE_SOURCES", "own,community,resmi_test")
        best = ps.best_offers(["sut|1l"])["sut|1l"]
        assert [(b["chain"], b["source"]) for b in best] == [("Örnek Zincir", "resmi_test"), ("A101", "own")]
    finally:
        ps._REGISTRY.pop("resmi_test", None)
