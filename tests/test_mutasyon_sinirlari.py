"""Mutasyon testinin hayatta bıraktığı sınırlar (scripts/mutasyon.py raporu, 16.11): çapa günü cevabı (in_anchor),
maaşın yattığı gün, gecikme payının son günü, ufuk günündeki taksit, net durumda altın/döviz işareti. Her test bir
sınırı İKİ yandan sabitler (sınırın kendisi ve bir ötesi). Tutarlar, kişiler UYDURMADIR."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import db, finance, workdays
from tests.test_gercek_gun_tekrari import freeze

AS_OF = "2026-11-10"


def _row(day: str, in_anchor=None, opening=False, debt_id=5) -> dict:
    return {"date": day, "in_anchor": in_anchor, "opening": opening, "debt_id": debt_id}


# ---------------------------------------------------------------- db.debt_after_anchor (çapa günü cevabı)

@pytest.mark.parametrize("day,in_anchor,want", [
    ("2026-11-11", None, True),   # çapadan sonra, cevapsız: bakiyeye eklenir
    ("2026-11-11", 0, True),      # "sonra yaptım"
    ("2026-11-11", 1, False),     # çapadan sonraki tarih ama "o bakiyede zaten var"
    ("2026-11-10", 0, True),      # çapa günü, "sonra yaptım": eklenir
    ("2026-11-10", 1, False),     # çapa günü, "bakiyede var": eklenmez
    ("2026-11-10", None, False),  # çapa günü ödeme, cevapsız: eski kural (çapanın içinde)
    ("2026-11-09", 0, True),      # çapadan önce ama "sonra yaptım"
    ("2026-11-09", None, False),
])
def test_debt_after_anchor_cevap_sinirlari(day, in_anchor, want):
    assert db.debt_after_anchor(_row(day, in_anchor), AS_OF) is want


@pytest.mark.parametrize("debt_id,anchor_id,day,want", [
    (6, 5, "2026-11-10", True),    # çapa günü açılış, çapadan SONRA girildi (id büyük): eklenir (S5)
    (5, 5, "2026-11-10", False),   # çapa anında zaten vardı
    (6, None, "2026-11-10", False),  # eski çapa (anchor_debt_id yok): içinde
    (6, 5, "2026-11-09", False),   # çapadan önceki açılış: çapanın içinde
])
def test_debt_after_anchor_acilis_capa_gunu(debt_id, anchor_id, day, want):
    assert db.debt_after_anchor(_row(day, None, True, debt_id), AS_OF, anchor_id) is want
    # açık cevap id kuralını ezer
    assert db.debt_after_anchor(_row(day, 1, True, debt_id), AS_OF, anchor_id) is False


def _acc(client, tmpdb, day: str, bal: float) -> int:
    assert client.post("/api/accounts", json={"name": "Örnek Banka A", "kind": "banka", "balance": bal,
                                              "as_of": day}).status_code == 200
    with tmpdb.connect() as c:
        return c.execute("SELECT id FROM accounts WHERE name = 'Örnek Banka A'").fetchone()[0]


def _cur() -> float:
    return {a["name"]: a["current"] for a in db.list_accounts()}["Örnek Banka A"]


def test_alacak_acilisi_capa_gunu_cevapla(client, tmpdb, monkeypatch):
    """POST /api/debts in_balance: çapa günü açılışta anchor_conflict döner; true → bakiyede var (düşmez)."""
    freeze(monkeypatch, date(2026, 11, 10))
    aid = _acc(client, tmpdb, "2026-11-10", 24000.0)
    r = client.post("/api/debts", json={"person": "Örnek Arkadaş", "direction": "alacak", "amount": 6000.0,
                                        "debt_date": "2026-11-10", "account_id": aid, "creditor_type": "kisi",
                                        "in_balance": True}).json()
    assert r["anchor_conflict"] == {"account_id": aid, "name": "Örnek Banka A", "as_of": "2026-11-10"}
    assert _cur() == 24000.0
    r2 = client.post("/api/debts", json={"person": "Örnek Komşu", "direction": "borc", "amount": 1500.0,
                                         "debt_date": "2026-11-10", "account_id": aid, "creditor_type": "kisi",
                                         "in_balance": False}).json()
    assert "anchor_conflict" in r2
    assert _cur() == 25500.0  # alınan borç bakiyeye eklenir


def test_alacak_acilisi_capadan_sonra_catisma_yok(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 12))
    aid = _acc(client, tmpdb, "2026-11-10", 24000.0)
    r = client.post("/api/debts", json={"person": "Örnek Arkadaş", "direction": "alacak", "amount": 6000.0,
                                        "debt_date": "2026-11-11", "account_id": aid, "creditor_type": "kisi"}).json()
    assert "anchor_conflict" not in r
    assert _cur() == 18000.0
    # bakiye yeniden çapalanınca açılış artık çapanın içinde (çift sayılmaz)
    assert client.patch(f"/api/accounts/{aid}", json={"balance": 18000.0, "as_of": "2026-11-12"}).status_code == 200
    assert _cur() == 18000.0


def test_borc_odemesi_capa_gunu_cevabi_bakiyede(client, tmpdb, monkeypatch):
    """Hesaplı borç ödemesi çapa gününde: in_balance false → düşer, true → düşmez, cevapsız → düşmez (eski kural)."""
    freeze(monkeypatch, date(2026, 11, 10))
    aid = _acc(client, tmpdb, "2026-11-10", 30000.0)
    did = client.post("/api/debts", json={"person": "Örnek Komşu", "direction": "borc", "amount": 9000.0,
                                          "debt_date": "2026-10-01", "creditor_type": "kisi"}).json()["id"]
    for amt, ans, want in ((1000.0, False, 29000.0), (2000.0, True, 29000.0), (500.0, None, 29000.0)):
        body = {"amount": amt, "paid_at": "2026-11-10", "account_id": aid}
        if ans is not None:
            body["in_balance"] = ans
        assert client.post(f"/api/debts/{did}/pay", json=body).status_code == 200
        assert _cur() == want, (amt, ans)
    # çapadan sonraki gün çakışma yok: cevap sorulmaz (yok sayılır), ödeme düşer
    freeze(monkeypatch, date(2026, 11, 11))
    r = client.post(f"/api/debts/{did}/pay", json={"amount": 300.0, "paid_at": "2026-11-11", "account_id": aid,
                                                   "in_balance": True}).json()
    assert "anchor_conflict" not in r
    assert _cur() == 28700.0


# ---------------------------------------------------------------- maaşın yattığı gün / gecikme payının son günü

def _salary(client, tmpdb, day: int = 15) -> None:
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(1)
    assert client.post("/api/recurring", json={"description": "Maaş", "amount": 40000.0, "kind": "gelir",
                                               "category": "Maaş", "day_of_month": day,
                                               "start_month": "2026-06"}).status_code == 200


def test_maas_yattigi_gun_yatmis_sayilir(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 16))
    _salary(client, tmpdb)
    assert client.post("/api/entries", json={"entry_date": "2026-11-16", "description": "Maaş", "amount": 40000.0,
                                             "kind": "gelir", "category": "Maaş"}).status_code == 200
    assert finance.salary_arrived("2026-11", date(2026, 11, 16)) is True   # yattığı gün
    assert finance.salary_arrived("2026-11", date(2026, 11, 15)) is False  # bir gün önce: henüz değil


def test_gecikme_payi_son_gunu_hala_beklenir():
    exp = "2026-11-16"  # Pazartesi
    last = workdays.add_business_days(exp, finance.SALARY_GRACE_DAYS)
    assert finance.salary_awaited(exp, last) is True
    assert finance.salary_awaited(exp, last + timedelta(days=1)) is False
    assert finance.salary_awaited(exp, last - timedelta(days=1)) is True


# ---------------------------------------------------------------- ufuk günündeki taksit

def test_ufuk_gunu_vadeli_taksit_simdi_sonraki_sonra(client, tmpdb, monkeypatch):
    """Ufuk 16 Kas (ay dönümü 17): 16 Kas vadeli taksit Harcanabilir'den düşer; kalanı spendable_later'da, vadesi 16 Ara."""
    freeze(monkeypatch, date(2026, 11, 1))
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    tmpdb.set_account_balance("Örnek Banka A", 50000.0, "2026-10-31", "elle")
    assert client.post("/api/debts", json={"person": "Örnek Komşu", "direction": "borc", "creditor_type": "kisi",
                                           "plan_installment": 1000.0, "plan_count": 3, "plan_start": "2026-11",
                                           "plan_day": 16, "debt_date": "2026-11-01"}).status_code == 200
    n = finance.net_worth(date(2026, 11, 1))
    assert n["spendable_horizon"] == "2026-11-16"
    parts = {p["key"]: p["amount"] for p in n["spendable_parts"]}
    assert parts["payable"] == 1000.0
    later = [x for x in n["spendable_later"]["items"] if x["type"] == "borc"]
    assert [(x["amount"], x["due_date"]) for x in later] == [(2000.0, "2026-12-16")]
    assert later[0]["steps"] == [{"date": "2026-12-16", "amount": 1000.0}, {"date": "2027-01-16", "amount": 1000.0}]


def test_erken_odenen_taksit_funded_icinde_iki_kez_dusmez(client, tmpdb, monkeypatch):
    """S9 sınırı: ufuktan sonraki taksit önceden ödendiyse funded'a girmez (steps ödemeyi önce sayar)."""
    freeze(monkeypatch, date(2026, 11, 1))
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    tmpdb.set_account_balance("Örnek Banka A", 100000.0, "2026-10-31", "elle")
    assert client.post("/api/recurring", json={"description": "Maaş", "amount": 61500.0, "kind": "gelir",
                                               "category": "Maaş", "day_of_month": 15,
                                               "start_month": "2026-06"}).status_code == 200
    did = client.post("/api/debts", json={"person": "Örnek Komşu", "direction": "borc", "creditor_type": "kisi",
                                          "plan_installment": 1500.0, "plan_count": 6, "plan_start": "2026-11",
                                          "plan_day": 10, "debt_date": "2026-11-01"}).json()["id"]
    # 10 Kas ve 10 Ara taksitleri bugün birlikte ödendi
    assert client.post(f"/api/debts/{did}/pay", json={"amount": 3000.0, "paid_at": "2026-11-01"}).status_code == 200
    n = finance.net_worth(date(2026, 11, 1))
    fb = n["forecast_balance"]
    assert fb["until"] == "2026-12-14"
    assert not [x for x in fb["funded_items"] if x["type"] == "borc"]
    later = [x for x in n["spendable_later"]["items"] if x["type"] == "borc"]
    assert later[0]["amount"] == 6000.0 and later[0]["steps"][0] == {"date": "2027-01-10", "amount": 1500.0}


# ---------------------------------------------------------------- net durumda altın/döviz işareti

def test_net_durum_altin_doviz_eklenir(client, tmpdb, monkeypatch):
    from app import zakat

    freeze(monkeypatch, date(2026, 11, 10))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-01", "elle")
    monkeypatch.setattr(zakat, "holdings", lambda: {"items": [], "self_total": 5000.0, "family_total": 700.0,
                                                    "updated": None, "totals": {}})
    n = finance.net_worth(date(2026, 11, 10))
    assert n["holdings"] == 5000.0
    assert n["net"] == 15000.0 == n["totals"]["net"]
    assert n["net_after_estimates"] == 15000.0
