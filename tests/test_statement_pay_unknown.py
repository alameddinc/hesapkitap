"""Hesabı belli olmayan kart ekstresi ödemesi (yetenek statement_pay_account): hesap söylenmeden "Ödendi" denince kart
borcu kapanır ama para hiçbir hesaptan düşmez — nakit şişmesin diye ödenen tutar döküm gelene kadar serbest /
Harcanabilir / net'ten ayrı kalem olarak düşülür (cash'ten değil), Özet'te uyarı çıkar; döküm gelince aynı ödemeyle
eşleşir, kalem kapanır, çift düşüm olmaz. Hesap uyarıları (yetenek account_alerts): eksi bakiye, 14+ gün güncel değil.
Uydurma veri."""
from datetime import date, timedelta

from app import bank_import, finance

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731 - n gün önce


def _setup(client, tmpdb, as_of=None, balance=50000):
    client.post("/api/accounts", json={"name": "Örnek Bank", "kind": "banka", "balance": balance, "as_of": as_of or D(10)})
    acc = next(a for a in client.get("/api/accounts").json()["accounts"] if a["name"] == "Örnek Bank")
    with tmpdb.connect() as c:
        sid = c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                        "VALUES ('k', 'Deneme Bank', '4543***1234', ?, ?, 12500)", (D(12), D(-5))).lastrowid
    return acc["id"], sid


def _nw():
    return finance.net_worth(TODAY)


def _current(client, aid):
    return next(a["current"] for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid)


def _import(tmpdb, aid, day, bal=37500.0, amount=-12500.0):
    row = {"date": f"{day[8:10]}.{day[5:7]}.{day[:4]}", "description": "KREDI KARTI BORC ODEMESI 4543****1234",
           "amount": amount, "balance": bal}
    return tmpdb.import_bank_rows(bank_import.finalize([row], []), account="Örnek Bank", owners=[], account_id=aid)


def test_unknown_account_pay_does_not_leak_into_spendable(client, tmpdb):
    aid, sid = _setup(client, tmpdb)
    before = _nw()
    assert before["card_debt"] == 12500 and before["unknown_payments"] == 0
    r = client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(2)}).json()
    assert r["ok"] and r["card_payment_id"] is None and not r["linked"]
    after = _nw()
    assert after["card_debt"] == 0 and after["cash"] == before["cash"] == 50000  # hesap bakiyesi gerçek kalır
    assert after["unknown_payments"] == 12500 and after["unknown_payments_items"][0]["statement_id"] == sid
    # kart borcu nakde sızmaz: serbest / Harcanabilir / net değişmez
    assert after["spendable"] == before["spendable"] and after["free"] == before["free"] - 12500
    assert after["net"] == before["net"] and after["net_display"] == before["net_display"]
    assert after["totals"]["unknown_payments"] == 12500
    part = next(p for p in after["spendable_parts"] if p["key"] == "unknown_payments")
    assert part["amount"] == 12500 and part["sign"] == "−"
    assert not any(p["key"] == "unknown_payments" for p in before["spendable_parts"])  # sıfırken satır yok
    # Özet uyarısı ve Ödemeler kalemi
    ins = [i for i in finance.insights(TODAY.strftime("%Y-%m"), TODAY) if i["key"].startswith("belirsiz_odeme:")]
    assert len(ins) == 1 and "hesabı belli değil" in ins[0]["title"] and ins[0]["target"]["id"] == sid
    item = next(p for p in client.get("/api/payables").json() if p["type"] == "statement" and p["id"] == sid)
    assert item["account_unknown"] is True and item["pay_account_hint"] is None


def test_with_account_balance_drops_and_no_unknown(client, tmpdb):
    aid, sid = _setup(client, tmpdb)
    before = _nw()
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(2), "account_id": aid})
    after = _nw()
    assert _current(client, aid) == 37500 and after["unknown_payments"] == 0
    assert after["spendable"] == before["spendable"] and after["net"] == before["net"]
    item = next(p for p in client.get("/api/payables").json() if p["type"] == "statement" and p["id"] == sid)
    assert item["account_unknown"] is False and item["pay_account_hint"] == aid  # bir dahaki Ödendi'de varsayılan


def test_statement_import_later_closes_unknown_without_double_count(client, tmpdb):
    aid, sid = _setup(client, tmpdb)
    before = _nw()
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(2)})
    st = _import(tmpdb, aid, D(3))  # gerçek ödeme işaretlemeden bir gün önce
    assert st["kart_odemesi"] == 1
    with tmpdb.connect() as c:
        cps = [dict(r) for r in c.execute("SELECT * FROM card_payments")]
    assert len(cps) == 1 and cps[0]["statement_id"] == sid and cps[0]["import_hash"]
    after = _nw()
    assert after["unknown_payments"] == 0 and after["cash"] == 37500  # dökümün bakiyesi: bir kez düştü
    assert after["spendable"] == before["spendable"] and after["net"] == before["net"]
    s = next(x for x in client.get("/api/statements").json() if x["id"] == sid)
    assert s["paid_link"] == "dokum" and s["paid_amount"] == 12500


def test_statement_import_first_then_unknown_pay_links(client, tmpdb):
    aid, sid = _setup(client, tmpdb)
    with tmpdb.connect() as c:  # ekstre elle "ödenmedi" denmiş olsun: otomatik eşleme işaretlemesin
        c.execute("UPDATE statements SET paid_source = 'elle' WHERE id = ?", (sid,))
    _import(tmpdb, aid, D(3))
    r = client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(1)}).json()
    assert r["linked"] and r["link"] == "dokum"
    assert _nw()["unknown_payments"] == 0 and _current(client, aid) == 37500


def test_choose_account_or_outside_closes_unknown(client, tmpdb):
    aid, sid = _setup(client, tmpdb)
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(2)})
    assert _nw()["unknown_payments"] == 12500
    # sonradan hesabı seçti: hesaptan düşer, belirsiz kalem kapanır
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(2), "account_id": aid})
    nw = _nw()
    assert nw["unknown_payments"] == 0 and _current(client, aid) == 37500
    # "Hesaptan değil" (nakit / takip edilmeyen hesap): belirsiz sayılmaz, hesap kaydı silinir
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(2), "account_id": None})
    assert _nw()["unknown_payments"] == 0 and _current(client, aid) == 50000
    # tutar düzeltmesi (anahtar yok) "hesaptan değil"i korur
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12000, "paid_at": D(2)})
    assert _nw()["unknown_payments"] == 0
    # geri al: kalem yok
    client.delete(f"/api/statements/{sid}/pay")
    assert _nw()["unknown_payments"] == 0


def test_unknown_only_when_some_balance_predates_payment(client, tmpdb):
    # hesabın bakiyesi ödemeden SONRA güncellenmiş: ödeme bakiyenin içinde, ayrıca düşülmez
    aid, sid = _setup(client, tmpdb, as_of=D(1))
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(2)})
    assert _nw()["unknown_payments"] == 0
    # çok eski işaret aranmaz
    with tmpdb.connect() as c:
        c.execute("UPDATE accounts SET as_of = ? WHERE id = ?", (D(200), aid))
        c.execute("UPDATE statements SET paid_at = ? WHERE id = ?", (D(90), sid))
    assert _nw()["unknown_payments"] == 0


def test_account_alerts_negative_and_stale(client, tmpdb):
    client.post("/api/accounts", json={"name": "Eksi Bank", "kind": "banka", "balance": -3000, "as_of": D(2)})
    client.post("/api/accounts", json={"name": "Eski Bank", "kind": "banka", "balance": 1000, "as_of": D(20)})
    client.post("/api/accounts", json={"name": "Taze Bank", "kind": "banka", "balance": 1000, "as_of": D(3)})
    nw = _nw()
    by = {a["name"]: a for a in nw["accounts"]}
    assert by["Eksi Bank"]["alert"]["kind"] == "eksi" and "eksi" in by["Eksi Bank"]["alert"]["text"]
    assert by["Eski Bank"]["alert"]["kind"] == "guncel_degil" and "20 gündür güncel değil" in by["Eski Bank"]["alert"]["text"]
    assert by["Taze Bank"]["alert"] is None
    assert {a["name"] for a in nw["account_alerts"]} == {"Eksi Bank", "Eski Bank"}
    keys = [i["key"] for i in finance.insights(TODAY.strftime("%Y-%m"), TODAY)]
    assert sum(k.startswith("hesap_eksi:") for k in keys) == 1 and sum(k.startswith("hesap_guncel_degil:") for k in keys) == 1


def test_capabilities():
    from app import version

    assert {"statement_pay_account", "account_alerts", "doc_read_report"} <= set(version.CAPABILITIES)
