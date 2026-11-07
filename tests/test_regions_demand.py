"""Fiyat toplayan iş için oturumsuz il talebi ucu: yalnız kendi anahtarıyla açılır."""


def test_regions_demand_requires_own_key(client, monkeypatch):
    assert client.get("/api/regions/demand").status_code == 404  # anahtar ayarlı değil
    monkeypatch.setenv("HESAPKITAP_APP_REGIONS_KEY", "ornek-anahtar-123")
    assert client.get("/api/regions/demand").status_code == 404
    assert client.get("/api/regions/demand", headers={"Authorization": "Bearer yanlis"}).status_code == 404
    r = client.get("/api/regions/demand", headers={"Authorization": "Bearer ornek-anahtar-123"})
    assert r.status_code == 200 and isinstance(r.json(), list)
