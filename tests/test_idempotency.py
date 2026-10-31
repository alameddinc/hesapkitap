"""Idempotency-Key (denetim M10(c)): ağ zaman aşımından sonra tekrar gönderilen yazma çift kayıt açmaz."""
import threading
import time

import pytest

ENTRY = {"entry_date": "2026-10-05", "description": "Kahve", "amount": 85, "kind": "gider", "category": "Kafe & Restoran",
         "payment": "nakit"}


def _count(tmpdb):
    return len(tmpdb.list_entries())


def test_same_key_twice_creates_one_entry_same_response(client, tmpdb):
    h = {"Idempotency-Key": "kayit-0001-abcd"}
    r1 = client.post("/api/entries", json=ENTRY, headers=h)
    r2 = client.post("/api/entries", json=ENTRY, headers=h)
    assert r1.status_code == r2.status_code == 200
    assert r1.content == r2.content and r2.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in r1.headers
    assert _count(tmpdb) == 1
    # anahtarsız ya da yeni anahtarla yeni kayıt
    client.post("/api/entries", json=ENTRY)
    client.post("/api/entries", json=ENTRY, headers={"Idempotency-Key": "kayit-0002-abcd"})
    assert _count(tmpdb) == 3


def test_same_key_different_body_422(client, tmpdb):
    h = {"Idempotency-Key": "kayit-0003-abcd"}
    client.post("/api/entries", json=ENTRY, headers=h)
    r = client.post("/api/entries", json={**ENTRY, "amount": 90}, headers=h)
    assert r.status_code == 422 and "başka bir istek" in r.json()["detail"]
    r = client.patch("/api/entries/1", json={"amount": 90}, headers=h)  # başka yol da başka istek
    assert r.status_code == 422
    assert _count(tmpdb) == 1


def test_error_responses_replayed_but_5xx_not_stored(client, tmpdb, monkeypatch):
    h = {"Idempotency-Key": "borc-odeme-0001"}
    did = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "alacak", "amount": 500,
                                          "debt_date": "2026-10-01"}).json()["id"]
    body = {"amount": 600, "paid_at": "2026-10-05"}
    r1 = client.post(f"/api/debts/{did}/pay", json=body, headers=h)
    r2 = client.post(f"/api/debts/{did}/pay", json=body, headers=h)
    assert r1.status_code == r2.status_code == 422 and r1.content == r2.content
    from app import idempotency

    assert idempotency.get(None, "borc-odeme-0001")["state"] == "done"
    idempotency.finish(None, "borc-odeme-0001", 503, b"x", None)  # sunucu hatası saklanmaz
    assert idempotency.get(None, "borc-odeme-0001") is None


def test_expired_key_runs_again(client, tmpdb):
    from app import idempotency

    h = {"Idempotency-Key": "kayit-0004-abcd"}
    client.post("/api/entries", json=ENTRY, headers=h)
    with tmpdb.connect() as c:
        c.execute("UPDATE idempotency SET created_at = ?", (time.time() - idempotency.TTL - 5,))
    r = client.post("/api/entries", json=ENTRY, headers=h)
    assert r.status_code == 200 and "Idempotent-Replayed" not in r.headers
    assert _count(tmpdb) == 2


def test_in_progress_then_replay(client, tmpdb, monkeypatch):
    """Eşzamanlı iki istek: ikincisi ilkinin bitmesini bekler ve aynı yanıtı alır; tek kayıt."""
    from app import db as _db

    real = _db.create_entry

    def slow(data):
        time.sleep(0.6)
        return real(data)

    monkeypatch.setattr(_db, "create_entry", slow)
    h = {"Idempotency-Key": "kayit-0005-abcd"}
    out = {}

    def go(n):
        out[n] = client.post("/api/entries", json=ENTRY, headers=h)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    ts[0].start()
    time.sleep(0.15)
    ts[1].start()
    for t in ts:
        t.join()
    assert out[0].status_code == out[1].status_code == 200 and out[0].content == out[1].content
    assert _count(tmpdb) == 1


def test_still_running_409(client, tmpdb, monkeypatch):
    from app import idempotency

    monkeypatch.setattr(idempotency, "WAIT", 0.2)
    fp = idempotency.fingerprint("POST", "/api/entries", "", client.build_request("POST", "/api/entries", json=ENTRY).read())
    assert idempotency.begin(None, "kayit-0006-abcd", fp) is None  # başka bir istek sürüyor
    r = client.post("/api/entries", json=ENTRY, headers={"Idempotency-Key": "kayit-0006-abcd"})
    assert r.status_code == 409 and "sürüyor" in r.json()["detail"]
    assert _count(tmpdb) == 0


@pytest.mark.parametrize("bad", ["kisa", "a" * 200, "bosluk var icinde", "yildiz*isareti"])
def test_invalid_key(client, tmpdb, bad):
    r = client.post("/api/entries", json=ENTRY, headers={"Idempotency-Key": bad})
    assert r.status_code == 422 and _count(tmpdb) == 0


def test_key_scoped_per_user(client, tmpdb):
    """Anahtar kullanıcıya ve deftere özel: başka kullanıcının aynı anahtarı çakışmaz."""
    from app import idempotency

    assert idempotency.begin(1, "ortak-anahtar-01", "fp1") is None
    assert idempotency.begin(2, "ortak-anahtar-01", "fp2") is None


def test_multipart_upload_with_key(client, tmpdb):
    from tests.conftest import fixture_bytes

    files = [("files", ("a.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))]
    h = {"Idempotency-Key": "yukleme-0001-ab"}
    r1 = client.post("/api/statements/upload", files=files, headers=h)
    r2 = client.post("/api/statements/upload", files=files, headers=h)
    assert r1.json()[0]["status"] == "saved" and r2.content == r1.content
    assert len(tmpdb.list_statements()) == 1


def test_cors_allows_header(client):
    r = client.options("/api/entries", headers={"Origin": "capacitor://localhost", "Access-Control-Request-Method": "POST",
                                                "Access-Control-Request-Headers": "idempotency-key,content-type"})
    assert r.status_code == 200 and "idempotency-key" in r.headers["access-control-allow-headers"].lower()
