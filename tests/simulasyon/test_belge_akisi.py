"""(C) Belge okuma uçtan uca: sentetik kart ekstresi, hesap dökümü (CSV) ve dekont → yükleme → okuma → kayıt → sayılar.
Gerçek kullanıcı belgesi YOK (ornekdata/, hk-fis-test kullanılmaz): metinler tests/simulasyon/belgeler.py'de üretilir.
Simülasyon (test_simulasyon.py) kart ekstresini, dökümü ve kira/aktarım dekontlarını da bu yoldan geçirir."""
from __future__ import annotations

import json
from datetime import date

import pytest

from app import finance
from tests.simulasyon import belgeler
from tests.test_gercek_gun_tekrari import freeze

OWNER, OWNER_A = "DENEME KİŞİOĞLU", "DENEME KISIOGLU"
CARD = "5400***0123"
IBAN_A, IBAN_B = "TR110001200000000000000901", "TR220001500000000000000902"


@pytest.fixture()
def defter(client, tmpdb, monkeypatch):
    belgeler.install(monkeypatch)
    freeze(monkeypatch, date(2026, 12, 20))
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    tmpdb.remember_owner_name(OWNER)
    for name, iban, bal in (("Örnek Banka A", IBAN_A, 50000.0), ("Örnek Banka B", IBAN_B, 8000.0)):
        assert client.post("/api/accounts", json={"name": name, "kind": "banka", "balance": bal,
                                                  "as_of": "2026-12-01"}).status_code == 200
    with tmpdb.connect() as c:
        for name, iban in (("Örnek Banka A", IBAN_A), ("Örnek Banka B", IBAN_B)):
            c.execute("UPDATE accounts SET iban = ? WHERE name = ?", (iban, name))
        return {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM accounts")}


def _upload_stmt(client, text: str):
    return client.post("/api/statements/upload",
                       files=[("files", ("ekstre.pdf", belgeler.register(text), "application/pdf"))]).json()


def test_kart_ekstresi_okunur_sayilar_tutar_odeme_dokumle_kapanir(client, tmpdb, defter):
    buys = [(date(2026, 11, 20), "ORNEK MARKET 12", 1250.4), (date(2026, 12, 2), "ORNEK AKARYAKIT 7", 2100.0),
            (date(2026, 12, 15), "ORNEK ECZANE 3", 89.9)]   # kesim günü satırı da ekstrede
    text = belgeler.card_statement(OWNER, CARD, date(2026, 12, 15), date(2026, 12, 25), date(2027, 1, 15),
                                   date(2027, 1, 25), 3000.0, [(date(2026, 11, 24), 3000.0)], buys)
    for _ in range(3):  # aynı belge 3 kez
        r = _upload_stmt(client, text)
        assert r and r[0].get("status") != "error", r
    sts = tmpdb.list_statements()
    assert len(sts) == 1
    st = sts[0]
    assert (st["total_debt"], st["due_date"], st["period_end"]) == (3440.3, "2026-12-25", "2026-12-15")
    with tmpdb.connect() as c:
        rows = [(r["txn_date"], r["amount"]) for r in c.execute(
            "SELECT txn_date, amount FROM transactions WHERE statement_id = ? ORDER BY txn_date, amount", (st["id"],))]
    assert ("2026-12-15", 89.9) in rows and ("2026-11-24", -3000.0) in rows and len(rows) == 4
    n = finance.net_worth(date(2026, 12, 20))
    assert n["card_debt"] == 3440.3
    # ödeme hesap dökümünden gelir: ekstre ödendi, bakiye döküm kapanışı
    csv = belgeler.bank_csv(OWNER_A, [(date(2026, 12, 19), "KREDI KARTI ODEMESI 5400 **** 0123", -3440.3, 46559.7)])
    pv = client.post("/api/bank-import/preview", files={"file": ("d.csv", csv, "text/csv")}).json()
    assert pv["counts"].get("kart_odemesi") == 1
    st2 = client.post("/api/bank-import", files={"file": ("d.csv", csv, "text/csv")},
                      data={"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]),
                            "account_id": str(defter["Örnek Banka A"])}).json()
    assert st2["kart_odemesi"] == 1 and st2["odendi_isaretlenen"] == 1
    n = finance.net_worth(date(2026, 12, 20))
    assert n["card_debt"] == 0 and {a["name"]: a["current"] for a in n["accounts"]}["Örnek Banka A"] == 46559.7


def test_dokum_turleri_ve_bakiye(client, tmpdb, defter):
    assert client.post("/api/recurring", json={"description": "Maaş", "amount": 40000, "kind": "gelir",
                                               "category": "Maaş", "day_of_month": 15,
                                               "start_month": "2026-06"}).status_code == 200
    rows = [(date(2026, 12, 15), "ORNEK A.S. MAAS ODEMESI", 40000.0, 90000.0),
            (date(2026, 12, 16), "ORNEK MARKET POS", -730.25, 89269.75),
            (date(2026, 12, 17), f"GIDEN FAST - {OWNER_A} TARAFINDAN GÖNDERILEN,{OWNER},Örnek Bankası A.Ş.,{IBAN_B}",
             -5000.0, 84269.75),
            (date(2026, 12, 18), "KIRA ODEMESI EFT ORNEK EV SAHIBI", -15000.0, 69269.75)]
    for rev in (False, True):  # ters sıralı ikinci yükleme: hiçbir şey eklenmez
        csv = belgeler.bank_csv(OWNER_A, rows, reverse=rev)
        pv = client.post("/api/bank-import/preview", files={"file": ("d.csv", csv, "text/csv")}).json()
        st = client.post("/api/bank-import", files={"file": ("d.csv", csv, "text/csv")},
                         data={"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]),
                               "account_id": str(defter["Örnek Banka A"])}).json()
        if not rev:
            assert (st["gelir"], st["gider"], st["virman"], st["maas"]) == (1, 2, 1, 1), st
        else:
            assert st["tekrar"] == 4, st
    n = finance.net_worth(date(2026, 12, 20))
    assert {a["name"]: a["current"] for a in n["accounts"]}["Örnek Banka A"] == 69269.75
    # 15 Ara maaşı (dönem 2026-12) dökümle yattı; beklenen gelir yalnız 15 Oca maaşı (dönem 2027-01, ufukta) — bir kez
    assert (n["spendable_after_income"] or {}).get("income") == 40000.0


def test_dekont_kendi_hesaplari_iki_taraf_tek_aktarim(client, tmpdb, defter):
    text = belgeler.dekont(OWNER, IBAN_A, OWNER, IBAN_B, "Örnek Bankası A.Ş.", date(2026, 12, 19), 7500.0, "7712345678")
    p = client.post("/api/dekont/parse", files={"file": ("dekont.pdf", belgeler.register(text), "application/pdf")}).json()
    assert p["amount"] == 7500.0 and p["date"] == "2026-12-19" and p.get("own"), p
    body = {"date": "2026-12-19", "amount": 7500.0, "from_account_id": defter["Örnek Banka A"],
            "to_account_id": defter["Örnek Banka B"], "ref": p["ref"], "dekont_file": p.get("dekont_file")}
    a = client.post("/api/own-transfers", json={**body, "dekont_side": "giden"}).json()
    b = client.post("/api/own-transfers", json={**body, "dekont_side": "gelen"})
    assert b.status_code in (200, 409)
    if b.status_code == 200:
        assert b.json()["linked"] is True and b.json()["id"] == a["id"]
    n = finance.net_worth(date(2026, 12, 20))
    bal = {x["name"]: x["current"] for x in n["accounts"]}
    assert bal == {"Örnek Banka A": 42500.0, "Örnek Banka B": 15500.0} and n["cash"] == 58000.0
