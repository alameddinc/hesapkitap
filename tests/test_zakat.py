from datetime import date

import pytest

PRICES = {"GRA": 6400.0, "YIA": 5900.0, "CEYREKALTIN": 10400.0, "USD": 50.0, "GUMUS": 87.0}


@pytest.fixture(autouse=True)
def fake_prices(monkeypatch):
    from app import zakat

    monkeypatch.setattr(zakat, "fetch_prices", lambda force=False: {"prices": dict(PRICES), "updated": "test",
                                                                     "fetched": 0, "source": "test"})


def _person(data, name):
    return next(p for p in data["people"] if p["name"] == name)


def test_nisab_and_ownership(client, tmpdb):
    d = client.get("/api/zakat").json()
    assert d["nisab"] == round(80.18 * 6400, 2)
    me = _person(d, "Ben")
    wife = client.post("/api/zakat/people", json={"name": "Eşim"}).json()["id"]
    # düğün takıları eşin: zekât ona düşer
    client.post("/api/zakat/assets", json={"person_id": wife, "kind": "ALTIN_22", "quantity": 100, "is_jewelry": True})
    client.post("/api/zakat/assets", json={"person_id": me["id"], "kind": "CEYREK", "quantity": 5})
    d = client.get("/api/zakat").json()
    w, me = _person(d, "Eşim"), _person(d, "Ben")
    assert w["total"] == 590000 and w["above_nisab"] and w["estimate"] == 14750
    assert me["total"] == 52000 and not me["above_nisab"]  # kendi payı nisabın altında
    # Şâfiî: kullanılan ziynet muaf
    client.put("/api/zakat/settings", json={"madhhab": "safii"})
    assert _person(client.get("/api/zakat").json(), "Eşim")["total"] == 0


def test_app_data_and_debts_hanefi(client, tmpdb):
    client.post("/api/accounts", json={"name": "Vakıf", "kind": "banka", "balance": 700000})
    client.post("/api/debts", json={"person": "Ali", "direction": "borc", "amount": 100000, "debt_date": "2026-10-01"})
    client.post("/api/debts", json={"person": "Veli", "direction": "alacak", "amount": 100, "asset": "USD",
                                    "debt_date": "2026-10-01"})
    me = _person(client.get("/api/zakat").json(), "Ben")
    assert me["gross"] == 705000 and me["deduct"] == 100000 and me["total"] == 605000 and me["above_nisab"]
    client.put("/api/zakat/settings", json={"madhhab": "safii"})  # Şâfiî: borç düşülmez
    assert _person(client.get("/api/zakat").json(), "Ben")["total"] == 705000


def test_hawl_payments_and_next_year(client, tmpdb):
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    assert client.post(f"/api/zakat/people/{me}/payments", json={"amount": 10}).status_code == 422  # yıl yok
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "TRY", "quantity": 1000000})
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-01-01"})
    client.post(f"/api/zakat/people/{me}/payments", json={"amount": 5000})
    h = _person(client.get("/api/zakat").json(), "Ben")["hawl"]
    assert h["due"] == "2026-12-21" and h["zakat"] == 25000 and h["paid"] == 5000 and h["remaining"] == 20000
    assert "Ramazan" in h["start_hijri"] or h["start_hijri"]  # kameri tarih gösterilir
    assert client.post(f"/api/zakat/people/{me}/next-year").json()["hawl_start"] == "2026-12-21"
    assert _person(client.get("/api/zakat").json(), "Ben")["hawl"]["paid"] == 0  # yeni yıl temiz başlar


def test_reminder(tmpdb):
    from app import jobs, zakat

    me = zakat.people()[0]["id"]
    zakat.add_asset(me, "TRY", 1000000, False, None)
    zakat.update_person(me, {"hawl_start": "2026-01-01"})
    assert any("Zekât" in m for m in jobs.reminders(date(2026, 12, 14)))
    assert not any("Zekât" in m for m in jobs.reminders(date(2026, 12, 13)))


def test_hijri():
    from app.zakat import to_hijri

    assert to_hijri(date(2026, 2, 19)).endswith("Ramazan 1447")  # Ramazan 1447 ≈ 18/19 Şubat 2026


def test_acquired_date_suggest_and_safii(client, tmpdb, monkeypatch):
    from app import zakat as _z

    monkeypatch.setattr(_z, "_roll_years", lambda today: None)  # geçmiş bir yılı sınıyor: yeni yıla geçmesin
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "CEYREK", "quantity": 20, "acquired": "2025-03-01"})
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "CEYREK", "quantity": 40, "acquired": "2025-09-01"})
    p = _person(client.get("/api/zakat").json(), "Ben")
    assert p["suggest_start"] == "2025-09-01"  # 60 çeyrek ≈ 624 bin > nisap; 20'si tek başına yetmez
    # Hanefî: yıl içinde eklenen de sayılır; Şâfiî: yılını doldurmamış olan sayılmaz
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2025-03-01"})  # yıl 2026-02-18'de biter
    assert _person(client.get("/api/zakat").json(), "Ben")["total"] == 624000
    client.put("/api/zakat/settings", json={"madhhab": "safii"})
    assert _person(client.get("/api/zakat").json(), "Ben")["total"] == 208000


def test_holdings_in_net_worth(client, tmpdb):
    assert client.get("/api/holdings").json()["items"] == []
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    wife = client.post("/api/zakat/people", json={"name": "Eşim"}).json()["id"]
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "CEYREK", "quantity": 2, "acquired": "2025-01-10",
                                           "cost": 16000})
    client.post("/api/zakat/assets", json={"person_id": wife, "kind": "ALTIN_22", "quantity": 10, "is_jewelry": True})
    h = client.get("/api/holdings").json()
    mine = next(i for i in h["items"] if i["is_self"])
    assert mine["value"] == 20800 and mine["gain"] == 4800 and mine["gain_pct"] == 30.0
    assert h["self_total"] == 20800 and h["family_total"] == 59000
    nw = client.get("/api/net-worth").json()
    assert nw["holdings"] == 20800 and nw["family_holdings"] == 59000 and nw["net"] == 20800  # eşinki net duruma girmez


def test_holding_candidates(client, tmpdb):
    tmpdb.create_entry({"entry_date": "2025-03-02", "description": "ATM PARA CEKME", "amount": 25000, "kind": "gider",
                        "category": "Nakit Çekim"})
    tmpdb.create_entry({"entry_date": "2025-03-03", "description": "Market", "amount": 2500, "kind": "gider"})
    tmpdb.create_entry({"entry_date": "2025-03-04", "description": "ATM", "amount": 200, "kind": "gider"})
    # şubeden çekim, boşluksuz açıklama
    tmpdb.create_entry({"entry_date": "2025-11-24", "description": "NakitÖdeme,AdıSoyadı:X", "amount": 250000, "kind": "gider"})
    tmpdb.create_entry({"entry_date": "2025-12-01", "description": "ABC KUYUMCULUK", "amount": 9000, "kind": "gider"})
    tmpdb.create_entry({"entry_date": "2025-12-05", "description": "FAST Giden Ödeme X Ltd", "amount": 40000, "kind": "gider"})
    rows = client.get("/api/holdings/candidates").json()
    assert [(r["date"], r["amount"], r["kind"]) for r in rows] == [
        ("2025-12-05", 40000, "gonderim"), ("2025-12-01", 9000, "kuyumcu"), ("2025-11-24", 250000, "nakit"),
        ("2025-03-02", 25000, "nakit")]


def test_estimate_quantity_is_conservative(client, tmpdb, monkeypatch):
    from app import zakat

    monkeypatch.setattr(zakat, "_yahoo_low", lambda sym, day: 4036.4 if "GC" in sym else 42.407)
    r = client.get("/api/holdings/estimate", params={"kind": "ALTIN_22", "cost": 250000, "day": "2025-11-24"}).json()
    # 24 ayar ≈ 5503 TL; %2 ihtiyat; 22 ayar saf oran 0,916 → işçilik dahil edilmediği için gram fazla çıkar
    assert 50 <= r["quantity"] <= 51 and r["unit"] == "gr"
    c = client.get("/api/holdings/estimate", params={"kind": "CEYREK", "cost": 18000, "day": "2025-11-24"}).json()
    assert c["quantity"] == 2 and c["unit"] == "adet"
    assert client.get("/api/holdings/estimate", params={"kind": "USD", "cost": 1, "day": "2025-11-24"}).status_code == 422
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "ALTIN_22", "quantity": r["quantity"], "cost": 250000,
                                           "acquired": "2025-11-24", "estimated": True, "is_jewelry": True})
    item = client.get("/api/holdings").json()["items"][0]
    assert item["estimated"] == 1


def _last_payment_id(client, pid):
    from app import zakat

    p = next(x for x in zakat.people() if x["id"] == pid)
    return max(x["id"] for x in p["payments"])


def test_history_missed_years(client, tmpdb, monkeypatch):
    from app import zakat

    monkeypatch.setattr(zakat, "historical_prices", lambda day, offline=False: {"GRA": 5000.0, "USD": 40.0})
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    assert client.get(f"/api/zakat/people/{me}/history").json()["need_start"] is True
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "ALTIN_22", "quantity": 100, "acquired": "2024-06-01"})
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-10-01"})
    h = client.get(f"/api/zakat/people/{me}/history").json()
    ends = [y["end"] for y in h["years"]]
    assert ends == ["2026-10-01", "2025-10-12", "2024-10-23"]  # yeniden eskiye, 354'er gün
    oldest = h["years"][-1]
    assert oldest["above_nisab"] and oldest["zakat"] == round(oldest["total"] * 0.025, 2) and oldest["partial"]
    # Hanefî: ödenmeyen zekât sonraki yılın tabanından düşülür
    assert h["years"][1]["deduct"] == oldest["remaining"]
    # geçmiş yılın ödemesi o yıla yazılır. BUGÜN (geriye dönük) ödenen eski yıl, sonraki yıl bittiğinde hâlâ
    # borçtu: sonraki yılların düşümü ve zekâtı DEĞİŞMEZ (eskiden değişiyordu: her ödemede yeni küçük "kalan" çıkıyordu)
    client.post(f"/api/zakat/people/{me}/payments", json={"amount": oldest["zakat"], "year_start": oldest["start"]})
    h2 = client.get(f"/api/zakat/people/{me}/history").json()
    assert h2["years"][-1]["remaining"] == 0
    assert h2["years"][1]["deduct"] == h["years"][1]["deduct"] and h2["years"][1]["zakat"] == h["years"][1]["zakat"]
    assert h2["years"][0]["zakat"] == h["years"][0]["zakat"]
    assert h2["total_due"] == round(h["total_due"] - oldest["zakat"], 2)
    # zamanında (sonraki yıl bitmeden) ödenmiş olsaydı: o tarihte borç yoktu, düşüm olmaz
    client.delete(f"/api/zakat/payments/{_last_payment_id(client, me)}")
    client.post(f"/api/zakat/people/{me}/payments",
                json={"amount": oldest["zakat"], "year_start": oldest["start"], "pay_date": oldest["end"]})
    h3 = client.get(f"/api/zakat/people/{me}/history").json()
    assert h3["years"][1]["deduct"] == 0 and h3["years"][1]["zakat"] > h["years"][1]["zakat"]
    # Net durum'da borç (sadece önbellekteki fiyatlarla; burada sahte fiyat önbellek yerine geçiyor)
    nw = client.get("/api/net-worth").json()
    assert nw["zakat_due"] == h3["total_due"] and nw["net"] == round(nw["holdings"] - h3["total_due"], 2)


def test_bank_balance_at_past_date(client, tmpdb):
    from app import zakat

    client.post("/api/accounts", json={"name": "Vakıf", "kind": "banka", "balance": 10000})
    acc = client.get("/api/accounts").json()["accounts"][0]
    with tmpdb.connect() as c:
        c.execute("UPDATE accounts SET as_of = '2026-10-01' WHERE id = ?", (acc["id"],))
    tmpdb.create_entry({"entry_date": "2026-03-01", "description": "Maaş", "amount": 5000, "kind": "gelir", "account_id": acc["id"]})
    tmpdb.create_entry({"entry_date": "2026-06-01", "description": "Kira", "amount": 2000, "kind": "gider", "account_id": acc["id"]})
    assert zakat._bank_at(date(2026, 4, 1)) == (12000.0, [])   # 10000 + 2000 (kira sonra çıktı)
    assert zakat._bank_at(date(2026, 3, 1)) == (12000.0, [])   # maaş günü (maaş dahil)
    assert zakat._bank_at(date(2026, 2, 1)) == (0.0, ["Vakıf"])  # ilk hareketten önce: bilinmiyor


def test_net_worth_at_past_date(client, tmpdb, monkeypatch):
    from app import zakat

    monkeypatch.setattr(zakat, "historical_prices", lambda day, offline=False: {"GRA": 5000.0, "USD": 40.0})
    client.post("/api/accounts", json={"name": "Vakıf", "kind": "banka", "balance": 10000})
    acc = client.get("/api/accounts").json()["accounts"][0]
    with tmpdb.connect() as c:
        c.execute("UPDATE accounts SET as_of = '2026-10-01' WHERE id = ?", (acc["id"],))
    tmpdb.create_entry({"entry_date": "2026-03-01", "description": "Maaş", "amount": 5000, "kind": "gelir", "account_id": acc["id"]})
    tmpdb.create_entry({"entry_date": "2026-06-01", "description": "Kira", "amount": 2000, "kind": "gider", "account_id": acc["id"]})
    r = client.get("/api/net-worth/at", params={"month": "2026-04"}).json()
    assert r["cash"] == 12000 and r["net"] == 12000 and r["as_of"].startswith("2026-04")


def test_bank_at_includes_card_payments_and_own_transfers(client, tmpdb):
    from app import zakat

    client.post("/api/accounts", json={"name": "VakıfBank", "kind": "banka", "balance": 10000})
    acc = client.get("/api/accounts").json()["accounts"][0]
    with tmpdb.connect() as c:
        c.execute("UPDATE accounts SET as_of = '2026-10-01' WHERE id = ?", (acc["id"],))
        c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account) VALUES ('2026-09-20', 3000, '1234', 'VakıfBank')")
        c.execute("INSERT INTO own_transfers (transfer_date, amount, account) VALUES ('2026-09-17', 5000, 'VakıfBank')")
    # 17 Eylül akşamı (aktarım gelmiş): sonra 3000 karta ödendi → 10000 + 3000
    assert zakat._bank_at(date(2026, 9, 17)) == (13000.0, [])
    assert zakat._bank_at(date(2026, 9, 20)) == (10000.0, [])
    assert zakat._bank_at(date(2026, 9, 16)) == (0.0, ["VakıfBank"])  # ilk hareketten önce: bilinmiyor


def test_minor_has_no_zakat(client, tmpdb):
    kid = client.post("/api/zakat/people", json={"name": "Bebek"}).json()["id"]
    client.post("/api/zakat/assets", json={"person_id": kid, "kind": "ALTIN_24", "quantity": 200})
    client.patch(f"/api/zakat/people/{kid}", json={"hawl_start": "2025-01-01"})
    p = _person(client.get("/api/zakat").json(), "Bebek")
    assert p["above_nisab"] and p["estimate"] > 0
    client.patch(f"/api/zakat/people/{kid}", json={"is_minor": True})
    p = _person(client.get("/api/zakat").json(), "Bebek")
    assert p["is_minor"] and not p["above_nisab"] and p["estimate"] == 0 and p["hawl"] is None
    assert client.get(f"/api/zakat/people/{kid}/history").json()["years"] == []
    assert client.get("/api/holdings").json()["family_total"] > 0  # varlık olarak görünmeye devam eder


def test_history_requires_nisab_at_start(client, tmpdb, monkeypatch):
    from app import zakat

    monkeypatch.setattr(zakat, "historical_prices", lambda day, offline=False: {"GRA": 5000.0, "USD": 40.0})
    w = client.post("/api/zakat/people", json={"name": "Eşim"}).json()["id"]
    client.post("/api/zakat/assets", json={"person_id": w, "kind": "ALTIN_22", "quantity": 30, "acquired": "2023-10-08"})
    client.post("/api/zakat/assets", json={"person_id": w, "kind": "ALTIN_22", "quantity": 80, "acquired": "2026-02-20"})
    client.patch(f"/api/zakat/people/{w}", json={"hawl_start": "2026-02-20"})
    h = client.get(f"/api/zakat/people/{w}/history").json()
    # 20.02.2026'da biten "yıl"ın başında sadece 30 gr vardı (nisap altı): zekât yok
    assert h["years"][0]["end"] == "2026-02-20" and h["years"][0]["zakat"] == 0 and h["years"][0]["start_below"]
    assert h["total_due"] == 0


def test_check_date_reminder_and_difference(client, tmpdb):
    from app import jobs, zakat

    w = client.post("/api/zakat/people", json={"name": "Eşim"}).json()["id"]
    client.post("/api/zakat/assets", json={"person_id": w, "kind": "ALTIN_24", "quantity": 120})  # 768.000
    client.patch(f"/api/zakat/people/{w}", json={"hawl_start": "2025-11-24", "check_date": "2027-02-09"})
    client.post(f"/api/zakat/people/{w}/payments", json={"amount": 18000, "pay_date": "2026-11-13"})
    ck = _person(zakat.compute(date(2027, 2, 2)), "Eşim")["check"]
    assert ck["days_left"] == 7 and ck["paid"] == 18000 and ck["difference"] == round(768000 * 0.025 - 18000, 2)
    assert any("Zekât kontrolü" in m for m in jobs.reminders(date(2027, 2, 2)))
    assert zakat.upcoming_checks(date(2027, 2, 5))[0]["kind"] == "kontrol"


def test_holy_days_fitre_and_reminders(client, tmpdb):
    from datetime import date, timedelta

    from app import zakat

    h = zakat.holy(date(2026, 10, 26))
    d = {x["key"]: date.fromisoformat(x["date"]) for x in h["days"]}
    # Diyanet: Ramazan 2027 ≈ 8 Şubat, Ramazan Bayramı ≈ 10 Mart, Kurban Bayramı ≈ 16 Mayıs (tablolu takvim ±1 gün)
    assert abs((d["ramazan"] - date(2027, 2, 8)).days) <= 1
    assert abs((d["ramazan_bayrami"] - date(2027, 3, 10)).days) <= 1
    assert abs((d["kurban"] - date(2027, 5, 16)).days) <= 1
    assert not h["in_ramadan"] and h["fitre"]["amount"] == 0
    client.put("/api/zakat/settings", json={"fitre_amount": 240, "fitre_people": 4})
    f = client.get("/api/zakat/holy").json()["fitre"]
    assert f == {"amount": 240, "people": 4, "total": 960}
    before = d["ramazan"] - timedelta(days=20)
    ins = zakat.holy_insights(before)
    assert ins and "Ramazan'a 20 gün" in ins[0]["title"] and "960" in ins[0]["detail"]
    assert any("Fitreyi unutma" in m for m in zakat.holy_reminders(d["ramazan_bayrami"] - timedelta(days=10)))
    assert any("Kurban" in m for m in zakat.holy_reminders(d["kurban"] - timedelta(days=45)))
    assert zakat.holy_insights(date(2026, 10, 26)) == []


def test_new_zakat_year_starts_automatically(tmpdb):
    from datetime import date, timedelta

    from app import zakat

    pid = zakat.add_person("Test Kişi")
    start = date(2025, 1, 10)
    zakat.update_person(pid, {"hawl_start": start.isoformat()})
    due = start + timedelta(days=zakat.LUNAR_YEAR_DAYS)
    zakat.compute(today=due - timedelta(days=1))
    assert next(p for p in zakat.people() if p["id"] == pid)["hawl_start"] == start.isoformat()  # henüz dolmadı
    zakat.compute(today=due)
    assert next(p for p in zakat.people() if p["id"] == pid)["hawl_start"] == due.isoformat()  # yeni yıl başladı


def test_wipe_removes_zakat_data_keeps_own_assets(client, tmpdb):
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    wife = client.post("/api/zakat/people", json={"name": "Eşim"}).json()["id"]
    client.post("/api/zakat/assets", json={"person_id": wife, "kind": "ALTIN_22", "quantity": 100})
    own = client.post("/api/zakat/assets", json={"person_id": me, "kind": "TRY", "quantity": 1000000}).json()["id"]
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-01-01"})
    client.post(f"/api/zakat/people/{me}/payments", json={"amount": 5000})
    client.post("/api/donations", json={"kind": "sadaka", "amount": 100, "given_date": "2026-10-01"})
    client.put("/api/zakat/settings", json={"madhhab": "safii"})
    # varlık düzenlenebilir; özet düzenleme için türü ve ziynet bilgisini döner
    assert client.patch(f"/api/zakat/assets/{own}", json={"quantity": 900000, "note": "kasada"}).status_code == 200
    line = next(x for x in _person(client.get("/api/zakat").json(), "Ben")["plus"] if x.get("id") == own)
    assert line["kind"] == "TRY" and line["qty"] == 900000 and line["note"] == "kasada" and line["is_jewelry"] is False

    r = client.delete("/api/zakat/data").json()
    assert r["payments"] == 1 and r["donations"] == 1 and r["people"] == 1 and r["assets"] == 1
    d = client.get("/api/zakat").json()
    assert [p["name"] for p in d["people"]] == ["Ben"] and d["settings"]["madhhab"] == "hanefi"
    ben = d["people"][0]
    assert ben["hawl"] is None and not ben["payments"]
    assert [x["id"] for x in ben["plus"] if x.get("id")] == [own]  # kendi varlığı Hesaplar'da kalır
    assert client.get("/api/donations").json()["items"] == []


def test_patch_payment_recomputes_hanafi_deduction(client, tmpdb, monkeypatch):
    """PATCH /api/zakat/payments/{id}: tek işlemde düzenleme; pay_date değişince Hanefî düşümü yeniden hesaplanır."""
    from app import zakat

    monkeypatch.setattr(zakat, "historical_prices", lambda day, offline=False: {"GRA": 5000.0, "USD": 40.0})
    me = _person(client.get("/api/zakat").json(), "Ben")["id"]
    client.post("/api/zakat/assets", json={"person_id": me, "kind": "ALTIN_22", "quantity": 100, "acquired": "2024-06-01"})
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-10-01"})
    h = client.get(f"/api/zakat/people/{me}/history").json()
    oldest = h["years"][-1]
    # zamanında ödendi (yıl sonunda): sonraki yılda düşüm yok
    client.post(f"/api/zakat/people/{me}/payments",
                json={"amount": oldest["zakat"], "year_start": oldest["start"], "pay_date": oldest["end"]})
    pid = _last_payment_id(client, me)
    assert client.get(f"/api/zakat/people/{me}/history").json()["years"][1]["deduct"] == 0
    # tarih bugüne alındı (geriye dönük ödeme): sonraki yıl bittiğinde borçtu → düşüm geri gelir
    r = client.patch(f"/api/zakat/payments/{pid}", json={"pay_date": "2026-10-30", "note": "düzeltme"})
    assert r.status_code == 200 and r.json()["pay_date"] == "2026-10-30" and r.json()["note"] == "düzeltme"
    assert r.json()["amount"] == oldest["zakat"] and r.json()["year_start"] == oldest["start"]  # diğerleri aynı
    h2 = client.get(f"/api/zakat/people/{me}/history").json()
    assert h2["years"][1]["deduct"] == h["years"][1]["deduct"] and h2["years"][-1]["remaining"] == 0
    # tutar yarıya: eski yılda kalan çıkar
    r = client.patch(f"/api/zakat/payments/{pid}", json={"amount": round(oldest["zakat"] / 2, 2)}).json()
    assert client.get(f"/api/zakat/people/{me}/history").json()["years"][-1]["remaining"] == round(oldest["zakat"] - r["amount"], 2)
    # atomik: geçersiz alan varsa hiçbir alan değişmez
    assert client.patch(f"/api/zakat/payments/{pid}", json={"amount": 0, "note": "x"}).status_code == 422
    import pytest

    with pytest.raises(ValueError):
        zakat.update_payment(pid, {"note": "y", "year_start": ""})
    p = next(x for x in zakat.people() if x["id"] == me)["payments"][0]
    assert p["note"] == "düzeltme" and p["amount"] == r["amount"]
    assert client.patch("/api/zakat/payments/99999", json={"note": "x"}).status_code == 404
