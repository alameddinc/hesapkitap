"""Zekât/sadaka "Nasıl ödedin?" genişledi: banka hesabından (hesap ops.), elden, altın bozdurup, altın vererek. Altınla
verilen hesaptan düşmez ama gider yazılır; altın varlığından düşülebilir, ödeme silinince geri konur. Ödeme şekli sonradan
değiştirilebilir. Faiz geliri gelire dahil, Özet'te ayrıca görünür. Tutarlar, kişiler, kurumlar uydurma."""
from app import sync


def _me(client):
    me = next(p for p in client.get("/api/zakat").json()["people"] if p["is_self"])["id"]
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-03-01"})
    return me


def _pay(client, me, **kw):
    r = client.post(f"/api/zakat/people/{me}/payments", json={"amount": 2500, "pay_date": "2026-10-20", **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _rows(client, month="2026-10"):
    return [r for r in client.get("/api/transactions", params={"month": month}).json() if r["category"] == "Bağış & Sadaka"]


def _payment(client, pid):
    return next(x for p in client.get("/api/zakat").json()["people"] for x in p["payments"] if x["id"] == pid)


def _assets(client, me):
    p = next(p for p in client.get("/api/zakat").json()["people"] if p["id"] == me)
    return {a["id"]: a["qty"] for a in p["plus"] if a["source"] == "varlik"}


def _bank(client, tmpdb):
    client.post("/api/accounts", json={"name": "Örnek Bank", "kind": "banka", "balance": 10000, "as_of": "2026-10-01"})
    return next(a for a in tmpdb.list_accounts() if a["name"] == "Örnek Bank")["id"]


def _balance(client, acc):
    return next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == acc)["current"]


def test_gold_payments_are_expense_but_not_from_account(client, tmpdb):
    me = _me(client)
    acc = _bank(client, tmpdb)
    client.post("/api/accounts", json={"name": "Cüzdan", "kind": "nakit", "balance": 500, "as_of": "2026-10-01"})
    a = _pay(client, me, payment="altin_bozdurma", amount=3000, account_id=acc)  # hesap gönderilse de yazılmaz
    b = _pay(client, me, payment="altin", amount=4000)
    rows = {r["payment"]: r for r in _rows(client)}
    assert rows["altin_bozdurma"]["amount"] == 3000 and rows["altin_bozdurma"]["account_id"] is None
    assert rows["altin"]["amount"] == 4000 and rows["altin"]["account_id"] is None  # tek cüzdana da yazılmaz
    assert rows["altin"]["note"] == "Altın vererek"
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["expense"] == 7000
    assert _balance(client, acc) == 10000
    assert _payment(client, a)["how"] == "altin_bozdurma" and _payment(client, b)["how"] == "altin"
    assert _payment(client, b)["method_editable"] is True
    # dökümdeki aynı tutarlı bir hareketle "bu ikisi aynı mı?" diye eşleşmez (para hesaptan çıkmadı)
    tmpdb.create_entry({"entry_date": "2026-10-20", "description": "ORNEK VAKFI EFT", "amount": 4000, "kind": "gider",
                        "payment": "havale", "account_id": acc, "import_hash": "h-ornek-1",
                        "note": "Hesap hareketi · Örnek Bank"})
    eid = _payment(client, b)["entry_id"]
    assert not any(p["m"]["id"] == eid for p in sync.find_pairs())


def test_bank_without_account_is_not_written_to_wallet(client, tmpdb):
    me = _me(client)
    acc = _bank(client, tmpdb)
    client.post("/api/accounts", json={"name": "Cüzdan", "kind": "nakit", "balance": 500, "as_of": "2026-10-01"})
    _pay(client, me, payment="banka", amount=100)                  # hesap seçmeden: cüzdana yazılmaz
    _pay(client, me, payment="banka", amount=200, account_id=acc)  # seçilen hesaptan düşer
    _pay(client, me, payment="nakit", amount=50)                   # elden: tek cüzdandan düşer
    rows = sorted(_rows(client), key=lambda r: r["amount"])
    wallet = next(a for a in tmpdb.list_accounts() if a["name"] == "Cüzdan")["id"]
    assert [(r["amount"], r["payment"], r["account_id"]) for r in rows] == [
        (50, "nakit", wallet), (100, "nakit", None), (200, "nakit", acc)]
    assert _balance(client, acc) == 9800
    hows = sorted((p["amount"], p["how"]) for p in next(p for p in client.get("/api/zakat").json()["people"]
                                                         if p["id"] == me)["payments"])
    assert hows == [(50, "nakit"), (100, "banka"), (200, "banka")]


def test_gold_payment_takes_from_asset_and_gives_back(client, tmpdb):
    me = _me(client)
    aid = client.post("/api/zakat/assets", json={"person_id": me, "kind": "ALTIN_24", "quantity": 20, "is_jewelry": False,
                                                 "cost": 40000}).json()["id"]
    pid = _pay(client, me, payment="altin", amount=12000, asset_id=aid, asset_qty=5)
    assert _assets(client, me) == {aid: 15}
    p = _payment(client, pid)
    assert p["asset_id"] == aid and p["asset_qty"] == 5 and p["asset_label"] == "Gram altın (24 ayar)" and p["asset_unit"] == "gr"
    # hepsini vermek varlığı siler; ödeme silinince aynı hâliyle geri gelir
    pid2 = _pay(client, me, payment="altin_bozdurma", amount=36000, asset_id=aid, asset_qty=15)
    assert _assets(client, me) == {}
    assert client.delete(f"/api/zakat/payments/{pid2}").status_code == 200
    assert _assets(client, me) == {aid: 15}
    assert client.delete(f"/api/zakat/payments/{pid}").status_code == 200
    assert _assets(client, me) == {aid: 20}
    with tmpdb.connect() as c:
        assert c.execute("SELECT cost FROM zakat_assets WHERE id = ?", (aid,)).fetchone()[0] == 40000
    # elindekinden fazlası düşülemez: ne ödeme ne gider yazılır
    r = client.post(f"/api/zakat/people/{me}/payments", json={"amount": 10, "payment": "altin", "asset_id": aid,
                                                              "asset_qty": 25})
    assert r.status_code == 422 and _rows(client) == []
    assert client.post(f"/api/zakat/people/{me}/payments", json={"amount": 10, "payment": "altin", "asset_id": 999,
                                                                 "asset_qty": 1}).status_code == 404
    # altınla olmayan ödemede varlık düşülmez
    _pay(client, me, payment="nakit", amount=10, asset_id=aid, asset_qty=1)
    assert _assets(client, me) == {aid: 20}


def test_change_payment_method_later(client, tmpdb):
    me = _me(client)
    acc = _bank(client, tmpdb)
    aid = client.post("/api/zakat/assets", json={"person_id": me, "kind": "CEYREK", "quantity": 4,
                                                 "is_jewelry": False}).json()["id"]
    pid = _pay(client, me, payment="banka", account_id=acc, amount=1000)
    assert _balance(client, acc) == 9000
    # banka → altın vererek (2 çeyrek): hesaba geri döner, gider kalır, altından düşer
    r = client.patch(f"/api/zakat/payments/{pid}", json={"payment": "altin", "asset_id": aid, "asset_qty": 2})
    assert r.status_code == 200, r.text
    assert r.json()["how"] == "altin" and r.json()["asset_qty"] == 2
    assert _balance(client, acc) == 10000 and _assets(client, me) == {aid: 2}
    [row] = _rows(client)
    assert row["payment"] == "altin" and row["amount"] == 1000 and row["account_id"] is None
    # tutar değişince gider de; altın düşümü aynı kalır
    client.patch(f"/api/zakat/payments/{pid}", json={"amount": 1200})
    assert [r["amount"] for r in _rows(client)] == [1200] and _assets(client, me) == {aid: 2}
    # altın → kredi kartı: altın geri konur, gider "ekstre bekleniyor"
    r = client.patch(f"/api/zakat/payments/{pid}", json={"payment": "kredi_karti"})
    assert r.json()["how"] == "kredi_karti" and r.json()["asset_id"] is None
    assert _assets(client, me) == {aid: 4}
    [row] = _rows(client)
    assert row["payment"] == "kredi_karti" and row["awaiting_statement"] and row["amount"] == 1200
    # aynı şekil yeniden gönderilirse gider yeniden yazılmaz
    eid = _payment(client, pid)["entry_id"]
    client.patch(f"/api/zakat/payments/{pid}", json={"payment": "kredi_karti", "pay_date": "2026-10-21"})
    assert _payment(client, pid)["entry_id"] == eid and _rows(client)[0]["date"] == "2026-10-21"
    assert client.patch(f"/api/zakat/payments/{pid}", json={"payment": "cek"}).status_code == 422
    # ödeme şekli girilmemiş eski ödeme: şekli sonradan girilince gider açılır
    old = _pay(client, me, amount=300)
    assert client.patch(f"/api/zakat/payments/{old}", json={"payment": "nakit"}).json()["how"] == "nakit"
    assert sorted(r["amount"] for r in _rows(client)) == [300, 1200]
    assert client.get("/api/zakat").json()["unexpensed"] == []


def test_marked_statement_payment_method_is_fixed(client, tmpdb):
    """Dökümden işaretlenen zekâtın şekli oradan gelir: değiştirilemez (aynısı gönderilirse sorun yok)."""
    _me(client)
    eid = tmpdb.create_entry({"entry_date": "2026-10-21", "description": "ORNEK VAKFI", "amount": 500, "kind": "gider",
                              "category": "Bağış & Sadaka", "payment": "havale"})
    client.post("/api/donations/mark", json={"source": "entry", "id": eid, "kind": "zekat"})
    pid = next(x["id"] for p in client.get("/api/zakat").json()["people"] for x in p["payments"])
    p = _payment(client, pid)
    assert p["how"] == "havale" and p["method_editable"] is False
    assert client.patch(f"/api/zakat/payments/{pid}", json={"payment": "altin"}).status_code == 422
    assert client.patch(f"/api/zakat/payments/{pid}", json={"payment": "havale", "amount": 450}).status_code == 200
    assert _payment(client, pid)["amount"] == 450
    assert [r["payment"] for r in _rows(client)] == ["havale"]


def test_donation_gold_and_old_values(client, tmpdb):
    r = client.post("/api/donations", json={"kind": "sadaka", "amount": 150, "given_date": "2026-10-20", "payment": "altin"})
    assert r.status_code == 200
    assert [r["payment"] for r in _rows(client)] == ["altin"]
    assert client.post("/api/donations", json={"kind": "sadaka", "amount": 1, "payment": "cek"}).status_code == 422
    # kayıt İşlemler'den düzenlenip altın ödeme şekliyle geri gönderilebilir
    [row] = _rows(client)
    assert client.patch(f"/api/entries/{row['id']}", json={"payment": "altin", "amount": 175}).status_code == 200


def test_interest_income_counts_in_income_and_shown_apart(client, tmpdb):
    tmpdb.create_entry({"entry_date": "2026-10-10", "description": "Maaş", "amount": 30000, "kind": "gelir"})
    tmpdb.create_entry({"entry_date": "2026-10-12", "description": "VADESIZ FAIZ", "amount": 42.5, "kind": "gelir"})
    tmpdb.create_entry({"entry_date": "2026-10-13", "description": "Nema ödemesi", "amount": 7.5, "kind": "gelir"})
    t = client.get("/api/summary", params={"month": "2026-10"}).json()
    assert t["totals"]["income"] == 30050 and t["totals"]["interest_income"] == 50
    assert "faiz geliri" in t["definitions"]["income"].lower()
    from app import finance  # arındırma uyarısı Faiz alarmında kalır

    assert any("arındır" in x["title"] for x in finance.interest_alarm("2026-10"))
    # faiz yoksa 0
    assert client.get("/api/summary", params={"month": "2026-09"}).json()["totals"]["interest_income"] == 0
