"""Fiyat toplayan iş için oturumsuz il talebi ucu: yalnız kendi anahtarıyla açılır."""


def test_regions_demand_requires_own_key(client, monkeypatch):
    assert client.get("/api/regions/demand").status_code == 404  # anahtar ayarlı değil
    monkeypatch.setenv("HESAPKITAP_APP_REGIONS_KEY", "ornek-anahtar-123")
    assert client.get("/api/regions/demand").status_code == 404
    assert client.get("/api/regions/demand", headers={"Authorization": "Bearer yanlis"}).status_code == 404
    r = client.get("/api/regions/demand", headers={"Authorization": "Bearer ornek-anahtar-123"})
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_ops_alert_pushes_admins_with_own_key(client, monkeypatch):
    from app import feedback, push

    sent = []
    monkeypatch.setattr(feedback, "admins", lambda: [{"id": 1}])
    monkeypatch.setattr(push, "notify", lambda uid, items, *a, **k: sent.extend((uid, i) for i in items) or items)
    body = {"status": "schema_changed", "region": "İzmir", "text": "content[].price alanı yok"}
    assert client.post("/api/ops/alert", json=body).status_code == 404
    monkeypatch.setenv("HESAPKITAP_OPS_ALERT_KEY", "ornek-uyari-anahtari")
    assert client.post("/api/ops/alert", json=body, headers={"Authorization": "Bearer yanlis"}).status_code == 404
    r = client.post("/api/ops/alert", json=body, headers={"Authorization": "Bearer ornek-uyari-anahtari"})
    assert r.status_code == 200 and r.json()["sent"] == 1
    assert sent[0][1]["kind"] == "sistem" and sent[0][1]["title"] == "Fiyat toplama: schema_changed · İzmir"
