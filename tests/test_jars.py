from datetime import date


def test_jar_deposit_withdraw_and_free_balance(client, tmpdb):
    client.post("/api/accounts", json={"name": "Vakıf", "kind": "banka", "balance": 10000})
    acc = client.get("/api/accounts").json()["accounts"][0]["id"]
    jid = client.post("/api/jars", json={"name": "Tatil", "goal": 5000, "deadline": "2027-06-30",
                                         "account_id": acc, "initial": 1000}).json()["id"]
    assert client.post(f"/api/jars/{jid}/moves", json={"amount": 500}).status_code == 200
    assert client.post(f"/api/jars/{jid}/moves", json={"amount": 2000, "direction": "cek"}).status_code == 422
    assert client.post(f"/api/jars/{jid}/moves", json={"amount": 200, "direction": "cek"}).status_code == 200
    j = client.get("/api/jars").json()[0]
    assert j["balance"] == 1300 and j["left"] == 3700 and j["pct"] == 26.0 and j["need_per_month"] > 0
    nw = client.get("/api/net-worth").json()
    # ayrılan para servetten düşmez, serbest bakiyeden düşer
    assert nw["reserved"] == 1300 and nw["free"] == nw["cash"] - 1300
    assert nw["accounts"][0]["reserved"] == 1300
    # kapatınca ayrılan serbest kalır
    client.patch(f"/api/jars/{jid}", json={"closed": True})
    assert client.get("/api/net-worth").json()["reserved"] == 0
    assert client.post(f"/api/jars/{jid}/moves", json={"amount": 1}).status_code == 422


def test_jar_auto_monthly(tmpdb):
    jid = tmpdb.create_jar({"name": "Araba", "monthly": 1000, "auto_day": 5}, today=date(2026, 8, 10))
    js = tmpdb.list_jars(date(2026, 8, 20))
    assert js[0]["balance"] == 0  # bu ayın 5'i geçmişti: eylülden başlar
    js = tmpdb.list_jars(date(2026, 10, 22))
    assert js[0]["balance"] == 2000 and len(js[0]["moves"]) == 2  # 5 Eylül, 5 Ekim
    assert tmpdb.list_jars(date(2026, 10, 23))[0]["balance"] == 2000  # tekrar eklenmez
    assert jid


def test_targets_migrate_to_jars_and_pace(client, tmpdb):
    tmpdb.create_target("Tatil", 30000, "2027-06-30", "2026-10", already=6000)
    js = client.get("/api/jars").json()
    assert [(j["name"], j["goal"], j["balance"]) for j in js] == [("Tatil", 30000, 6000)]
    assert len(client.get("/api/jars").json()) == 1  # bir kez taşınır
    jid = js[0]["id"]
    client.post(f"/api/jars/{jid}/moves", json={"amount": 3000})
    j = client.get("/api/jars").json()[0]
    assert j["pace"] == 1000 and j["eta"]  # son 3 ayda 3000 → ayda 1000


def test_gold_jar_monthly_buy_adds_asset_entry_and_reminder(client, tmpdb, monkeypatch):
    from datetime import date

    from app import finance, zakat

    monkeypatch.setattr(zakat, "prices", lambda: {"prices": {"GRA": 4000.0, "CEYREKALTIN": 7000.0}})
    client.post("/api/accounts", json={"name": "Banka", "kind": "banka", "balance": 50000})
    acc = next(a["id"] for a in tmpdb.list_accounts() if a["name"] == "Banka")
    jid = client.post("/api/jars", json={"name": "Düğün altını", "unit": "ALTIN_24", "goal": 50, "monthly": 1,
                                         "auto_day": 1}).json()["id"]
    # alım günü geldi, alınmadı → Özet'te hatırlatma; otomatik hareket yok
    ins = [i for i in finance.insights(finance.current_period(), today=date.today()) if "alma günü" in i["title"]]
    assert ins and "1 gr" in ins[0]["title"] and "4.000" in ins[0]["title"]
    assert client.get("/api/jars").json()[0]["balance"] == 0
    # aldım
    r = client.post(f"/api/jars/{jid}/buy", json={"quantity": 1, "cost": 3900, "payment": "havale", "account_id": acc})
    assert r.status_code == 200
    j = client.get("/api/jars").json()[0]
    assert (j["balance"], j["cost"], j["value"], j["gain"], j["unit_symbol"], j["buy_due"]) == (1, 3900, 4000, 100, "gr", False)
    a = next(x for p in zakat.people() for x in p["assets"] if x["note"] == "Kumbara: Düğün altını")
    assert (a["kind"], a["quantity"], a["cost"], a["acquired"]) == ("ALTIN_24", 1, 3900, date.today().isoformat())
    e = next(x for x in tmpdb.list_entries() if x["category"] == "Altın & Döviz Birikimi")
    assert e["amount"] == 3900 and e["account_id"] == acc
    # altın kumbarası "serbest"ten düşmez (para ayırma değil)
    assert tmpdb.jar_reserved()["total"] == 0
    assert not [i for i in finance.insights(finance.current_period(), today=date.today()) if "alma günü" in i["title"]]
    # alımı silmek varlığı ve gider kaydını da siler
    mid = j["moves"][0]["id"]
    client.delete(f"/api/jar-moves/{mid}")
    assert not [x for p in zakat.people() for x in p["assets"] if x["note"] == "Kumbara: Düğün altını"]
    assert not [x for x in tmpdb.list_entries() if x["category"] == "Altın & Döviz Birikimi"]
    # TL kumbarasına "aldım" denmez
    tl = client.post("/api/jars", json={"name": "Tatil"}).json()["id"]
    assert client.post(f"/api/jars/{tl}/buy", json={"quantity": 1, "cost": 10}).status_code == 422


def _gold_jar(client, tmpdb, monkeypatch):
    from app import zakat

    monkeypatch.setattr(zakat, "prices", lambda: {"prices": {"GRA": 6422.0}})
    return client.post("/api/jars", json={"name": "Altın", "unit": "ALTIN_24", "monthly": 1, "auto_day": 1}).json()["id"]


def _state(tmpdb):
    from app import zakat

    e = [x for x in tmpdb.list_entries() if x["category"] == "Altın & Döviz Birikimi"]
    a = [x for p in zakat.people() for x in p["assets"] if (x["note"] or "").startswith("Kumbara")]
    return e, a


def test_bank_statement_corrects_estimated_gold_price(client, tmpdb, monkeypatch):
    jid = _gold_jar(client, tmpdb, monkeypatch)
    client.post(f"/api/jars/{jid}/buy", json={"quantity": 1, "cost": 6422, "buy_date": "2026-10-20", "payment": "havale"})
    rows = [{"date": "2026-10-21", "description": "ORNEK KUYUMCU EFT", "amount": -6550.0, "balance": 1000.0,
             "type": "gider", "hash": "k1"}]
    st = tmpdb.import_bank_rows(rows, account="", owners=[])
    e, a = _state(tmpdb)
    assert st["eslesen_elle"] == 1 and len(e) == 1 and e[0]["amount"] == 6550  # çift sayılmadı, gerçek tutar
    assert a[0]["cost"] == 6550
    assert client.get("/api/jars").json()[0]["cost"] == 6550
    # %10'dan fazla farklı bir ödeme eşleşmez (başka bir harcama)
    client.post(f"/api/jars/{jid}/buy", json={"quantity": 1, "cost": 6422, "buy_date": "2026-11-20", "payment": "havale"})
    tmpdb.import_bank_rows([{"date": "2026-11-20", "description": "MARKET", "amount": -9000.0, "balance": 1, "type": "gider",
                             "hash": "k2"}], account="", owners=[])
    e, _ = _state(tmpdb)
    assert sorted(x["amount"] for x in e) == [6422, 6550]


def test_card_statement_corrects_and_manual_edit(client, tmpdb, monkeypatch):
    jid = _gold_jar(client, tmpdb, monkeypatch)
    client.post(f"/api/jars/{jid}/buy", json={"quantity": 1, "cost": 6422, "buy_date": "2026-09-15", "payment": "kredi_karti"})
    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end) VALUES ('g', 'VakıfBank', '4543***1234', '2026-10-13')")
        sid = c.execute("SELECT id FROM statements").fetchone()[0]
        c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type) "
                  "VALUES (?, '2026-09-15', 'ORNEK KUYUMCU', 6380.0, 'harcama')", (sid,))
    tmpdb.auto_link()
    e, a = _state(tmpdb)
    assert e[0]["linked_txn_id"] and e[0]["amount"] == 6380 and a[0]["cost"] == 6380
    # elle düzeltme: miktar ve tarih varlığa da yansır
    mid = client.get("/api/jars").json()[0]["moves"][0]["id"]
    assert client.patch(f"/api/jar-moves/{mid}", json={"quantity": 1.05, "cost": 6400, "move_date": "2026-09-16"}).status_code == 200
    e, a = _state(tmpdb)
    assert (a[0]["quantity"], a[0]["cost"], a[0]["acquired"]) == (1.05, 6400, "2026-09-16")
    assert e[0]["amount"] == 6400 and e[0]["entry_date"] == "2026-09-16"


def test_gold_buy_is_savings_not_expense(client, tmpdb, monkeypatch):
    from app import finance

    jid = _gold_jar(client, tmpdb, monkeypatch)
    client.post(f"/api/jars/{jid}/buy", json={"quantity": 1, "cost": 6422, "payment": "havale"})
    client.post("/api/entries", json={"kind": "gider", "amount": 500, "description": "Market", "category": "Market",
                                      "entry_date": __import__("datetime").date.today().isoformat()})
    s = finance.summary(finance.current_period())
    t = s["totals"]
    assert (t["expense"], t["savings"]) == (500, 6422)
    assert t["cash_net"] == -6922  # eldeki paradan yine çıkar
    assert "Altın & Döviz Birikimi" not in {c["name"] for c in s["by_category"]}
