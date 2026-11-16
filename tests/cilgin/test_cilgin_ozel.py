"""Çılgın testçiler — hedefli kötü niyetli senaryolar: ters sıralı döküm, gelecek/geçmiş tarih, 29 Şubat, eşzamanlı
istek, ortak defter üyesi. Bulunan hata → test_cilgin_bulgular.py'de xfail(strict). Veriler UYDURMADIR."""
from __future__ import annotations

import json
import threading
from datetime import date, timedelta

import pytest

from app import finance
from tests.cilgin import ortak
from tests.cilgin.ortak import Defter, invariants, snapshot
from tests.simulasyon import belgeler

D0 = date(2028, 2, 27)


def _acc(d: Defter, name="Örnek Banka A", bal=20000.0, as_of: date | None = None) -> int:
    r = d.call("post", "/api/accounts", json={"name": name, "kind": "banka", "balance": bal,
                                              "as_of": (as_of or d.today - timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    return d.ids()[name]


def _import(d: Defter, aid: int, rows, reverse=False):
    data = belgeler.bank_csv("DENEME KISIOGLU", rows, reverse=reverse)
    pv = d.call("post", "/api/bank-import/preview", files={"file": ("d.csv", data, "text/csv")}).json()
    r = d.call("post", "/api/bank-import", files={"file": ("d.csv", data, "text/csv")},
               data={"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]), "account_id": str(aid)})
    assert r.status_code == 200, r.text
    return r.json()


ROWS = [(date(2028, 2, 24), "ORNEK MARKET POS", -820.5, 19179.5),
        (date(2028, 2, 25), "ORNEK A.S. MAAS ODEMESI", 30000.0, 49179.5),
        (date(2028, 2, 25), "KIRA ODEMESI EFT ORNEK EV SAHIBI", -15000.0, 34179.5),
        (date(2028, 2, 26), "ORNEK MARKET POS", -820.5, 33359.0),       # aynı gün değil, aynı tutar
        (date(2028, 2, 26), "ORNEK MARKET POS", -820.5, 32538.5)]       # aynı gün, aynı tutar, farklı bakiye


def test_ters_sirali_dokum_ayni_sonuc(tmp_path, client):
    out = []
    for rev in (False, True):
        d = Defter(tmp_path, D0)
        try:
            aid = _acc(d)
            st = _import(d, aid, ROWS, reverse=rev)
            out.append((st, snapshot(D0)))
            assert not invariants(D0)
        finally:
            d.close()
    assert out[0] == out[1]
    assert out[0][1]["bal"]["Örnek Banka A"] == 32538.5


def test_ayni_dokum_uc_kez_ve_ortusen_donem(tmp_path, client):
    d = Defter(tmp_path, D0)
    try:
        aid = _acc(d)
        _import(d, aid, ROWS)
        s1 = snapshot(D0)
        for _ in range(2):
            st = _import(d, aid, ROWS)
            assert st["tekrar"] == len(ROWS), st
        _import(d, aid, ROWS[2:])  # örtüşen kısa döküm
        assert snapshot(D0) == s1
    finally:
        d.close()


def test_29_subat_ve_ay_donumu(tmp_path, client):
    """29 Şubat 2028 kesimli ekstre, 29'unda yatan maaş, ay dönümü 29 isteği (28'e kırpılır), gün gün 1 Mart'a."""
    d = Defter(tmp_path, date(2028, 2, 28), cycle_day=28)
    try:
        aid = _acc(d)
        r = d.call("put", "/api/settings/cycle", json={"mode": "manual", "day": 29})
        assert r.status_code == 422  # 1..28
        d.call("post", "/api/recurring", json={"description": "Maaş", "amount": 30000, "kind": "gelir",
                                               "category": "Maaş", "day_of_month": 31, "start_month": "2027-12",
                                               "account_id": aid})
        data = ortak.stmt_text("5400***0229", date(2028, 2, 29), date(2028, 3, 10), date(2028, 3, 31),
                               date(2028, 4, 10), [(date(2028, 2, 20), "ORNEK MARKET", 1500.0)])
        for day in (date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1), date(2028, 3, 2)):
            d.set_day(day)
            if day == date(2028, 2, 29):
                assert d.call("post", "/api/statements/upload",
                              files=[("files", ("e.pdf", data, "application/pdf"))]).status_code == 200
                assert d.call("post", "/api/entries", json={"entry_date": "2028-02-29", "description": "Maaş",
                                                            "amount": 30000, "kind": "gelir", "category": "Maaş",
                                                            "account_id": aid}).status_code == 200
            assert not invariants(day), day
            n = finance.net_worth(day)
            assert n["spendable_horizon"] >= day.isoformat()
    finally:
        d.close()


def test_gelecek_ve_cok_eski_tarih(tmp_path, client):
    """Çapadan çok önceki kayıt bakiyeyi değiştirmez; gelecek tarihli (planlanan) kayıt bugün olmamış bir harcamadır."""
    d = Defter(tmp_path, D0)
    try:
        aid = _acc(d, as_of=D0 - timedelta(days=2))
        s0 = snapshot(D0)
        assert d.call("post", "/api/entries", json={"entry_date": "2025-01-01", "description": "Eski", "amount": 500,
                                                    "kind": "gider", "category": "Market", "payment": "havale",
                                                    "account_id": aid}).status_code == 200
        assert snapshot(D0)["bal"] == s0["bal"]
        assert not invariants(D0)
    finally:
        d.close()


def test_eszamanli_ayni_odeme(tmp_path, client):
    """İki sekme / çift dokunuş: aynı ekstre aynı anda iki kez Ödendi (hesaptan) → tek kart ödemesi. (Aynı dökümün
    eşzamanlı iki yüklemesi: test_cilgin_bulgular.py C1.)"""
    from fastapi.testclient import TestClient

    from app.api.main import app

    d = Defter(tmp_path, D0)
    try:
        aid = _acc(d)
        data = ortak.stmt_text("5400***0777", D0 - timedelta(days=5), D0 + timedelta(days=5), D0 + timedelta(days=25),
                               D0 + timedelta(days=35), [(D0 - timedelta(days=9), "ORNEK MARKET", 4000.0)])
        assert d.call("post", "/api/statements/upload", files=[("files", ("e.pdf", data, "application/pdf"))]).status_code == 200
        sid = d.db.list_statements()[0]["id"]
        clients = [TestClient(app) for _ in range(2)]
        codes = []

        def pay(c):
            codes.append(c.post(f"/api/statements/{sid}/pay", json={"amount": 4000.0, "paid_at": D0.isoformat(),
                                                                     "account_id": aid}).status_code)

        ts = [threading.Thread(target=pay, args=(c,)) for c in clients]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert all(c < 500 for c in codes), codes
        assert snapshot(D0)["bal"]["Örnek Banka A"] == 16000.0
        with d.db.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM card_payments WHERE statement_id = ?", (sid,)).fetchone()[0] == 1

        assert not invariants(D0)
    finally:
        d.close()


@pytest.fixture()
def owner_member(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    inv = client.post("/api/book/invites", json={"note": "esim"}).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"code": inv["code"], "username": "esim",
                                              "password": "esim-sifre-12345"}).status_code == 200
    return client, m


def test_ortak_defter_uyesi_karisik_sira(owner_member, tmpdb, monkeypatch):
    """Sahip ve üye aynı hesaba sırayla/karışık kayıt girer, üye Ödendi der; iki görünümde değişmezler ve aynı nakit."""
    from tests.simulasyon import saat

    owner, m = owner_member
    saat.freeze(monkeypatch, D0)
    belgeler.install(monkeypatch)
    assert owner.post("/api/accounts", json={"name": "Örnek Banka A", "kind": "banka", "balance": 30000.0,
                                             "as_of": "2028-02-20"}).status_code == 200
    aid = owner.get("/api/accounts").json()
    aid = next(a["id"] for a in (aid if isinstance(aid, list) else aid.get("accounts", [])) if a["name"] == "Örnek Banka A")
    data = ortak.stmt_text("5400***0555", D0 - timedelta(days=4), D0 + timedelta(days=6), D0 + timedelta(days=26),
                           D0 + timedelta(days=36), [(D0 - timedelta(days=8), "ORNEK MARKET", 2500.0)])
    assert owner.post("/api/statements/upload", files=[("files", ("e.pdf", data, "application/pdf"))]).status_code == 200
    sid = owner.get("/api/statements").json()
    sid = (sid if isinstance(sid, list) else sid.get("items", []))[0]["id"]
    seq = [(owner, 400.0), (m, 250.0), (m, 99.9), (owner, 1200.0), (m, 0.01)]
    for who, amt in seq:
        r = who.post("/api/entries", json={"entry_date": D0.isoformat(), "description": "Örnek Market", "amount": amt,
                                           "kind": "gider", "category": "Market", "payment": "havale",
                                           "account_id": aid})
        assert r.status_code == 200, r.text
    r = m.post(f"/api/statements/{sid}/pay", json={"amount": 2500.0, "paid_at": D0.isoformat(), "account_id": aid})
    assert r.status_code in (200, 403), r.text
    on, mn = owner.get("/api/net-worth").json(), m.get("/api/net-worth").json()
    paid = 2500.0 if r.status_code == 200 else 0.0
    assert on["cash"] == round(30000.0 - sum(a for _, a in seq) - paid, 2)
    assert mn["cash"] == on["cash"] and mn["spendable"] == on["spendable"]
    for n in (on, mn):
        tot = round(sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in n["spendable_parts"]), 2)
        assert tot == n["spendable"] and n["spendable"] <= n["free"]
