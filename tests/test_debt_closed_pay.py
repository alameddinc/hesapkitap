"""Kapanmış borca "Ödendi" (16.11 canlı sorun kaydı: POST /api/debts/{did}/pay 422). Kanıt: ödeme yazıldıktan 0,6 sn sonra
aynı gövdeyle (yeni Idempotency-Key) ikinci istek; istemcinin listesi tazelenmeden satır hâlâ "Ödedim" gösteriyordu.
Yanıt 422 kalır (ikinci ödeme yazılmaz) ama code "debt_closed" + refresh: istemci listeyi tazeler, anlamlı metin gösterir.
Kişiler ve tutarlar uydurmadır."""
from datetime import date

import pytest



@pytest.fixture()
def debt(client):
    r = client.post("/api/debts", json={"person": "Örnek Komşu", "direction": "borc", "asset": "TRY", "amount": 750.0,
                                        "debt_date": "2026-10-01"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _pay(client, did, key):
    return client.post(f"/api/debts/{did}/pay", json={"amount": None, "paid_at": date.today().isoformat(), "account_id": None},
                       headers={"Idempotency-Key": key})


def test_ayni_odeme_yeni_anahtarla_ikinci_kez(client, debt):
    assert _pay(client, debt, "ilk-anahtar-0001").status_code == 200
    r = _pay(client, debt, "ikinci-anahtar-02")
    assert r.status_code == 422
    j = r.json()
    assert j["code"] == "debt_closed" and j["refresh"] is True
    assert "zaten kapanmış" in j["message"] and j["detail"] == j["message"]
    debts = client.get("/api/debts").json()
    d = next(x for x in (debts if isinstance(debts, list) else debts["items"]) if x["id"] == debt)
    assert abs(d["paid_amount"] - 750.0) < 0.01  # ikinci ödeme yazılmadı


def test_ayni_anahtarla_tekrar_ilk_yaniti_doner(client, debt):
    """Aynı anahtar (ağ zaman aşımı sonrası yeniden gönderim) 422 değil, ilk yanıt (Idempotency)."""
    a = _pay(client, debt, "ayni-anahtar-0003")
    b = _pay(client, debt, "ayni-anahtar-0003")
    assert a.status_code == b.status_code == 200 and a.json() == b.json()


def test_vazgecilmis_kayit_da_debt_closed(client, debt):
    assert client.post(f"/api/debts/{debt}/write-off", json={"on": date.today().isoformat()}).status_code == 200
    r = _pay(client, debt, "vazgecti-anahtar-4")
    assert r.status_code == 422 and r.json()["code"] == "debt_closed"


def test_fazla_odeme_debt_closed_degil(client, debt):
    r = client.post(f"/api/debts/{debt}/pay", json={"amount": 900.0, "paid_at": date.today().isoformat(), "account_id": None})
    assert r.status_code == 422 and "code" not in r.json() and "Kalan" in r.json()["detail"]
