import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def fake_parse(monkeypatch):
    from app import receipt

    monkeypatch.setattr(receipt, "parse_document", lambda data, mime, cats, item_cats, more=None: {
        "type": "receipt", "merchant": "H&M", "total": 849.99, "date": "2026-10-23", "payment": "kart", "installments": 3,
        "category": "Giyim", "items": [], "confidence": "yüksek"})


def test_share_with_token_goes_to_inbox(client, monkeypatch, fake_parse):
    from app import auth
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    token = client.post("/api/settings/tokens", json={"name": "iPhone"}).json()["token"]
    assert client.get("/api/settings/tokens").json()[0]["name"] == "iPhone"

    phone = TestClient(app)  # çerez yok: sadece anahtar
    files = {"file": ("fis.jpg", b"\xff\xd8fake", "image/jpeg")}
    assert phone.post("/api/share", files=files).status_code == 401
    assert phone.post("/api/share", files=files, headers={"Authorization": "Bearer hk_yanlis"}).status_code == 401
    r = phone.post("/api/share", files=files, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and "H&M" in r.text and "onayla" in r.text
    # anahtar başka uçlarda geçmez
    assert phone.get("/api/meta", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    inbox = client.get("/api/inbox").json()
    assert len(inbox) == 1 and inbox[0]["doc"]["installments"] == 3
    client.post(f"/api/inbox/{inbox[0]['id']}/kaydedildi")
    assert client.get("/api/inbox").json() == []
    # anahtar silinince geçersiz
    tid = client.get("/api/settings/tokens").json()[0]["id"]
    client.delete(f"/api/settings/tokens/{tid}")
    assert phone.post("/api/share", files=files, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_android_share_target(client, fake_parse):
    r = client.post("/share-target", files={"file": ("fis.jpg", b"\xff\xd8fake", "image/jpeg")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/#/ozet?paylasim=")
    assert len(client.get("/api/inbox").json()) == 1


def test_unsupported_file(client):
    r = client.post("/api/share", files={"file": ("a.txt", b"x", "text/plain")})
    assert "sadece PDF ya da fotoğraf" in r.text


def test_shortcut_download(client, tmp_path, monkeypatch):
    import os

    from app import system

    assert client.get("/api/share/shortcut").json() == {"available": False}
    assert client.get("/hesapkitap.shortcut").status_code == 404
    d = os.path.join(os.path.dirname(os.path.abspath(system.SYSTEM_DB)), "shortcuts")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "hesapkitap.shortcut"), "wb").write(b"AEA1signed")
    assert client.get("/api/share/shortcut").json() == {"available": True}
    r = client.get("/hesapkitap.shortcut")
    assert r.status_code == 200 and r.content == b"AEA1signed"
