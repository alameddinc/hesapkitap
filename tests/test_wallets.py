"""Ön ödemeli kartlar: yemek / hediye / giyim."""
from datetime import date

from app import db, finance, jobs


def _wallet(client, **kw):
    body = {"name": "Multinet", "card_type": "yemek", "balance": 1000, **kw}
    return client.post("/api/wallets", json=body).json()["id"]


def test_spend_and_load_update_balance_and_totals(client, tmpdb):
    wid = _wallet(client)
    r = client.post("/api/entries", json={"entry_date": date.today().isoformat(), "description": "Kahve Dünyası",
                                          "amount": 220, "kind": "gider", "category": "Kafe & Restoran", "payment": "kart",
                                          "wallet_id": wid})
    assert r.status_code == 200
    client.post(f"/api/wallets/{wid}/load", json={"amount": 500})
    w = db.get_wallet(wid)
    assert w["current"] == 1000 - 220 + 500
    m = db.period_of(date.today())
    t = client.get("/api/summary", params={"month": m}).json()["totals"]
    assert t["expense"] == 220 and t["income"] == 500          # yükleme gelir, harcama gider
    nw = finance.net_worth()
    assert nw["in_wallets"] == 1280 and nw["net"] == 1280
    # kart seçilmeden "kart" ödemesi olmaz
    assert client.post("/api/entries", json={"entry_date": "2026-10-21", "description": "x", "amount": 1, "kind": "gider",
                                             "payment": "kart"}).status_code == 422


def test_balance_correction_reanchors(client, tmpdb):
    wid = _wallet(client)
    client.post("/api/entries", json={"entry_date": date.today().isoformat(), "description": "Yemek", "amount": 300,
                                      "kind": "gider", "payment": "kart", "wallet_id": wid})
    client.patch(f"/api/wallets/{wid}", json={"balance": 650})       # uygulamada gerçek bakiye 650
    assert db.get_wallet(wid)["current"] == 650


def test_monthly_load_auto_stop_and_resume(client, tmpdb):
    tmpdb.set_cycle_start_day(1)
    day = min(date.today().day, 28)
    wid = _wallet(client, balance=0, monthly_amount=5000, load_day=day)    # yükleme günü bugün
    month = db.period_of(date.today())
    assert tmpdb.auto_load_wallets(date.today()) == 1
    assert db.recurring_done(db.get_wallet(wid)["schedule"]["id"], month)
    assert db.get_wallet(wid)["current"] == (5000 if day == date.today().day else 0)
    assert tmpdb.auto_load_wallets(date.today()) == 0                    # aynı dönem iki kez yüklenmez
    client.post(f"/api/wallets/{wid}/schedule/stop", json={"mode": "now"})
    assert db.get_wallet(wid)["schedule"]["end_month"] == month          # bu dönemki yükleme kalır
    nxt = db._shift_month(month, 1)
    assert tmpdb.auto_load_wallets(date.fromisoformat(f"{nxt}-02")) == 0  # sonraki ay yüklenmez
    client.post(f"/api/wallets/{wid}/schedule/stop", json={"mode": "resume"})
    assert db.get_wallet(wid)["schedule"]["end_month"] is None
    # tutar değişimi: yeni dönemden itibaren
    client.put(f"/api/wallets/{wid}/schedule", json={"amount": 6000, "day": day, "from_month": nxt})
    recs = [r for r in tmpdb.list_recurring() if r.get("wallet_id") == wid]
    assert sorted((r["amount"], r["end_month"]) for r in recs) == [(5000, month), (6000, None)]


def test_expiry_reminder_and_bank_import_ignores_wallet_spend(client, tmpdb):
    from datetime import timedelta

    wid = _wallet(client, name="Boyner", card_type="hediye", balance=850,
                  expires_at=(date.today() + timedelta(days=7)).isoformat())
    assert any("Boyner" in m and "7 gün" in m for m in jobs.reminders(date.today()))
    client.post("/api/entries", json={"entry_date": "2026-10-21", "description": "Boyner", "amount": 400, "kind": "gider",
                                      "payment": "kart", "wallet_id": wid})
    st = tmpdb.import_bank_rows([{"date": "2026-10-21", "description": "POS BOYNER", "amount": -400.0, "type": "gider",
                                  "hash": "b"}])
    assert st["eslesen_elle"] == 0 and st["gider"] == 1   # banka satırı kart harcamasına bağlanmaz


def test_load_dated_before_wallet_creation_not_double_counted(client, tmpdb):
    tmpdb.set_cycle_start_day(1)
    if date.today().day == 1:
        return
    wid = _wallet(client, balance=4000, monthly_amount=5000, load_day=1)   # bu ayın 1'indeki yükleme bakiyenin içinde
    tmpdb.auto_load_wallets(date.today())
    w = db.get_wallet(wid)
    assert w["current"] == 4000 and db.recurring_done(w["schedule"]["id"], db.period_of(date.today()))


def test_meal_card_not_in_spendable(client, tmpdb):
    from app import finance

    client.post("/api/accounts", json={"name": "Banka", "kind": "banka", "balance": 10000})
    client.post("/api/wallets", json={"name": "Multinet", "card_type": "yemek", "balance": 1500})
    nw = finance.net_worth()
    assert nw["spendable"] == 10000  # yemek kartı sadece yemekte harcanır: harcanabilir TL'ye katılmaz
    assert nw["net"] == 11500  # net durumda var
