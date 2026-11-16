"""Simülasyonun ve çılgın testçilerin bulduğu hataların MİNİMAL tekrarları. Açık olanlar xfail(strict=True) (S1, S4,
S5, S7, S9 düzeltildi: işaretsiz, gerileme testi). Düzeltme
gelince test "XPASS(strict)" ile kırmızıya döner, işaret kaldırılır (ve tests/simulasyon/sim.py'deki BULGU_* modeli
silinir). Kimlik (S1…) rapordaki sıra. Tutarlar, kişiler, IBAN'lar UYDURMADIR."""
from __future__ import annotations

import json
from datetime import date

import pytest

from app import finance
from tests.simulasyon import belgeler
from tests.test_gercek_gun_tekrari import freeze

OWNER = "DENEME KİŞİOĞLU"


def _ids(tmpdb) -> dict[str, int]:
    with tmpdb.connect() as c:
        return {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM accounts")}


def _bal(day: date) -> dict[str, float]:
    return {a["name"]: a["current"] for a in finance.net_worth(day)["accounts"]}


def _dokum(client, aid: int, rows: list) -> dict:
    data = belgeler.bank_csv("DENEME KISIOGLU", rows)
    pv = client.post("/api/bank-import/preview", files={"file": ("d.csv", data, "text/csv")}).json()
    r = client.post("/api/bank-import", files={"file": ("d.csv", data, "text/csv")},
                    data={"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]),
                          "account_id": str(aid)})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- S1 iki maaş + döküm

def test_s1_iki_maas_dokum_birinci_maasin_ustune_yazmaz(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 12, 18))
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(1)
    tmpdb.remember_owner_name(OWNER)
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-30", "elle")
    tmpdb.set_account_balance("Örnek Banka B", 5000.0, "2026-12-10", "elle")
    ids = _ids(tmpdb)
    for desc, amt, day, acc in (("Maaş", 48000.0, 1, "Örnek Banka A"), ("Eş maaşı", 39000.0, 15, "Örnek Banka B")):
        assert client.post("/api/recurring", json={"description": desc, "amount": amt, "kind": "gelir", "category": "Maaş",
                                                   "day_of_month": day, "start_month": "2026-06",
                                                   "account_id": ids[acc]}).status_code == 200
    # 1 Ara: birinci maaş A'ya elle girildi; 14 Ara: A'nın bakiyesi güncellendi (maaş içinde)
    assert client.post("/api/entries", json={"entry_date": "2026-12-01", "description": "Maaş", "amount": 48000.0,
                                             "kind": "gelir", "category": "Maaş",
                                             "account_id": ids["Örnek Banka A"]}).status_code == 200
    assert client.patch(f"/api/accounts/{ids['Örnek Banka A']}", json={"balance": 58000.0,
                                                                       "as_of": "2026-12-14"}).status_code == 200
    # 15 Ara: eşin maaşı B'ye yattı, B'nin dökümü yüklendi
    _dokum(client, ids["Örnek Banka B"], [(date(2026, 12, 15), "ORNEK A.S. MAAS ODEMESI", 39000.0, 44000.0)])
    n = finance.net_worth(date(2026, 12, 18))
    with tmpdb.connect() as c:
        maas = dict(c.execute("SELECT amount, entry_date, account_id FROM entries WHERE description = 'Maaş' "
                              "AND month = '2026-12'").fetchone())
    assert maas == {"amount": 48000.0, "entry_date": "2026-12-01", "account_id": ids["Örnek Banka A"]}
    assert _bal(date(2026, 12, 18)) == {"Örnek Banka A": 58000.0, "Örnek Banka B": 44000.0}
    assert ((n["spendable_after_income"] or {}).get("income") or 0.0) == 0.0  # iki maaş da yattı


# ---------------------------------------------------------------- S2 dekonttan kira

@pytest.mark.xfail(strict=True, reason="S2 (YÜKSEK): dekonttan kaydedilen kira (POST /api/dekont/save → "
                   "app/dekont_parse.py save ~419) düzenli kaleme bağlanmıyor (kategori 'Kategorisiz', recurring_id yok; "
                   "db._attach_recurring/_transfer_recurring_slots tutmuyor). Kira hem bakiyeden düştü hem 'Düzenli "
                   "ödemeler'de bekliyor: Ödemelerden sonra her ay kira kadar (14–24 bin ₺) EKSİK; denetim "
                   "duzenli_bekliyor doğru yakalıyor.")
def test_s2_dekonttan_kira_duzenli_kaleme_baglanir(client, tmpdb, monkeypatch):
    belgeler.install(monkeypatch)
    freeze(monkeypatch, date(2026, 11, 10))
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(1)
    tmpdb.set_account_balance("Örnek Banka A", 50000.0, "2026-11-01", "elle")
    aid = _ids(tmpdb)["Örnek Banka A"]
    assert client.post("/api/recurring", json={"description": "Ev kirası", "amount": 14000.0, "kind": "gider",
                                               "category": "Kira & Aidat", "day_of_month": 10, "start_month": "2026-06",
                                               "account_id": aid}).status_code == 200
    text = belgeler.dekont(OWNER, "TR660001000000000000000301", "ORNEK EV SAHIBI", "TR990006400000000000000077",
                           "Örnek Bankası A.Ş.", date(2026, 11, 10), 14000.0, "7700012345", "Kira")
    p = client.post("/api/dekont/parse", files={"file": ("dekont.pdf", belgeler.register(text), "application/pdf")}).json()
    assert p["amount"] == 14000.0 and p["date"] == "2026-11-10"
    r = client.post("/api/dekont/save", json={"date": "2026-11-10", "amount": 14000.0, "direction": "giden",
                                              "counterparty": p.get("counterparty") or "ORNEK EV SAHIBI",
                                              "account_id": aid, "description": "Kira", "ref": p.get("ref"),
                                              "dekont_file": p.get("dekont_file")})
    assert r.status_code == 200, r.text
    n = finance.net_worth(date(2026, 11, 10))
    assert n["cash"] == 36000.0
    assert not n["recurring_items"], n["recurring_items"]


# ---------------------------------------------------------------- S3 dekontlu aktarım dünkü elle aktarıma bağlanıyor

@pytest.mark.xfail(strict=True, reason="S3 (ORTA): app/dekont_parse.py save_own → db.find_own_leg (app/db.py ~3825) "
                   "±1 gün, aynı tutar ve hesapta sorgu numarasız bir bacak bulunca YENİ dekontu ona bağlıyor "
                   "(linked: true, created: 0). Dün elle girilmiş 5.000'lik aktarımdan sonra bugün yapılan ikinci "
                   "5.000'lik aktarımın dekontu kayboluyor: iki hesabın bakiyesi 5.000 yanlış (toplam nakit doğru). "
                   "Öneri: tarihi farklı + öbür bacakta ref yoksa sor ya da yeni kayıt aç.")
def test_s3_dekontlu_ikinci_aktarim_dunkune_baglanmaz(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 22))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-10", "elle")
    tmpdb.set_account_balance("Örnek Banka B", 30000.0, "2026-11-10", "elle")
    ids = _ids(tmpdb)
    body = {"amount": 5000.0, "from_account_id": ids["Örnek Banka B"], "to_account_id": ids["Örnek Banka A"]}
    assert client.post("/api/own-transfers", json={**body, "date": "2026-11-21"}).status_code == 200
    r = client.post("/api/own-transfers", json={**body, "date": "2026-11-22", "ref": "7700001234",
                                                "dekont_side": "giden"}).json()
    assert r["linked"] is False and r["created"] == 2
    assert _bal(date(2026, 11, 22)) == {"Örnek Banka A": 20000.0, "Örnek Banka B": 20000.0}


# ---------------------------------------------------------------- S4 kesimden sonra elle kart harcaması

def test_s4_kesimden_sonraki_elle_harcama_kapanmis_donguye_yazilmaz(client, tmpdb, monkeypatch):
    from tests.cilgin.ortak import stmt_text

    belgeler.install(monkeypatch)
    freeze(monkeypatch, date(2026, 12, 16))
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    tmpdb.remember_owner_name(OWNER)
    tmpdb.set_account_balance("Örnek Banka A", 50000.0, "2026-12-10", "elle")
    card = "5400***0001"
    data = stmt_text(card, date(2026, 11, 15), date(2026, 11, 25), date(2026, 12, 15), date(2026, 12, 25),
                     [(date(2026, 11, 3), "ORNEK MARKET", 3000.0)])
    assert client.post("/api/statements/upload", files=[("files", ("e.pdf", data, "application/pdf"))]).status_code == 200
    sid = tmpdb.list_statements()[0]["id"]
    client.post(f"/api/statements/{sid}/pay", json={"amount": 3000.0, "paid_at": "2026-11-24", "account_id": None})
    for d, amt in (("2026-12-10", 400.0), ("2026-12-16", 1300.0)):  # kesimden önce ve SONRA
        assert client.post("/api/entries", json={"entry_date": d, "description": "Örnek Market", "amount": amt,
                                                 "kind": "gider", "category": "Market", "payment": "kredi_karti",
                                                 "card_last4": "0001"}).status_code == 200
    n = finance.net_worth(date(2026, 12, 16))
    assert [(x["due_date"], x["amount"]) for x in n["pending_cards_items"]] == [("2026-12-25", 400.0)]


# ---------------------------------------------------------------- S5 hesaplı borç/alacak açılışı çapa günündeyse

def test_s5_hesapli_alacak_acilisi_capa_gunu_bakiyeden_duser(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 1))
    assert client.post("/api/accounts", json={"name": "Örnek Banka A", "kind": "banka", "balance": 24000.0,
                                              "as_of": "2026-11-01"}).status_code == 200
    aid = _ids(tmpdb)["Örnek Banka A"]
    r = client.post("/api/debts", json={"person": "Örnek Arkadaş", "direction": "alacak", "amount": 6000.0,
                                        "debt_date": "2026-11-01", "account_id": aid, "creditor_type": "kisi"})
    assert r.status_code == 200, r.text
    assert _bal(date(2026, 11, 1)) == {"Örnek Banka A": 18000.0}


# ---------------------------------------------------------------- S6 fatura Ödendi hesap sormuyor

@pytest.mark.xfail(strict=True, reason="S6 (ORTA, tasarım): POST /api/bills/{id}/pay (PayIn: amount, paid_at, payment) "
                   "hesap almıyor; havaleyle ödenen faturanın gideri hesapsız yazılır (db.pay_bill ~3011), banka "
                   "bakiyesi uygulamada DÜŞMEZ (free fatura kadar fazla; denetim hesapsiz_hareket uyarır). Simülasyonda "
                   "elle hesaplı personalarda her fatura ödemesinde görülür; ancak döküm/elle bakiye ile düzelir.")
def test_s6_fatura_odendi_hesaptan_duser(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 20))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-15", "elle")
    aid = _ids(tmpdb)["Örnek Banka A"]
    bid = client.post("/api/bills", json={"provider": "Örnek Elektrik", "amount": 640.0,
                                          "due_date": "2026-11-22"}).json()["id"]
    r = client.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": "2026-11-20", "payment": "havale",
                                                   "account_id": aid})
    assert r.status_code == 200, r.text
    assert _bal(date(2026, 11, 20)) == {"Örnek Banka A": 9360.0}


# ---------------------------------------------------------------- S7 açık recurring_id ile erken maaş

def test_s7_acik_recurring_id_erken_maasi_dogru_doneme_yazar(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 1))
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(1)
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-10-29", "elle")
    aid = _ids(tmpdb)["Örnek Banka A"]
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 48000.0, "kind": "gelir",
                                              "category": "Maaş", "day_of_month": 1, "start_month": "2026-06",
                                              "account_id": aid}).json()["id"]
    assert client.post("/api/entries", json={"entry_date": "2026-10-30", "description": "Maaş", "amount": 48000.0,
                                             "kind": "gelir", "category": "Maaş", "account_id": aid,
                                             "recurring_id": rid}).status_code == 200
    with tmpdb.connect() as c:
        assert c.execute("SELECT month FROM entries").fetchone()[0] == "2026-11"


# ---------------------------------------------------------------- S8 kişi adı büyük/küçük harf

@pytest.mark.xfail(strict=True, reason="S8 (KOZMETİK): dekonttaki ASCII büyük harfli ad 'ORNEK EV SAHIBI' kayıtta "
                   "'Ornek Ev Sahıbı' oluyor (dekont._title: I → ı). Türkçe olmayan (ASCII) metinde I → i olmalı.")
def test_s8_dekont_ascii_ad_dogru_kucuk_harf(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 10))
    tmpdb.set_account_balance("Örnek Banka A", 50000.0, "2026-11-01", "elle")
    aid = _ids(tmpdb)["Örnek Banka A"]
    r = client.post("/api/dekont/save", json={"date": "2026-11-10", "amount": 100.0, "direction": "giden",
                                              "counterparty": "ORNEK EV SAHIBI", "account_id": aid}).json()
    assert r["counterparty"] in ("Ornek Ev Sahibi", "Örnek Ev Sahibi")


# ---------------------------------------------------------------- S9 Ay sonunda kalacak: taksitli borç (89629f9)

def test_s9_ay_sonu_taksitli_borcun_yalniz_vadesi_gelen_taksidi(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 1))
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    tmpdb.set_account_balance("Örnek Banka A", 100000.0, "2026-10-31", "elle")
    assert client.post("/api/recurring", json={"description": "Maaş", "amount": 61500.0, "kind": "gelir",
                                               "category": "Maaş", "day_of_month": 15,
                                               "start_month": "2026-06"}).status_code == 200
    r = client.post("/api/debts", json={"person": "Örnek Komşu", "direction": "borc", "creditor_type": "kisi",
                                        "plan_installment": 1500.0, "plan_count": 6, "plan_start": "2026-11",
                                        "plan_day": 10, "debt_date": "2026-11-01"})
    assert r.status_code == 200, r.text
    n = finance.net_worth(date(2026, 11, 1))
    fb = n["forecast_balance"]
    inc = (n["spendable_after_income"] or {}).get("income") or 0.0
    assert inc == 61500.0 and n["spendable_horizon"] == "2026-11-16" and fb["until"] == "2026-12-14"
    # ufuk 16 Kas: 10 Kas taksidi spendable'da; until 14 Ara: yalnız 10 Ara taksidi (1.500) fonlanır
    assert fb["no_new_spend"] == round(n["spendable"] + inc - 1500.0, 2), fb.get("funded_items")
