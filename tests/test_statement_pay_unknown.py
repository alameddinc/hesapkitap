"""Hesabı belli olmayan kart ekstresi ödemesi (yetenek statement_pay_account): hesap söylenmeden "Ödendi" denince kart
borcu kapanır ama para hiçbir hesaptan düşmez — nakit şişmesin diye ödenen tutar döküm gelene kadar serbest /
Harcanabilir / net'ten ayrı kalem olarak düşülür (cash'ten değil), Özet'te uyarı çıkar; döküm gelince aynı ödemeyle
eşleşir, kalem kapanır, çift düşüm olmaz. Hesap uyarıları (yetenek account_alerts): eksi bakiye, 14+ gün güncel değil.
Uydurma veri."""
from datetime import date, timedelta

from app import bank_import, finance

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731 - n gün önce


def _due() -> str:
    """5 gün sonraki vade, Harcanabilir'in ufkunu (spend_horizon) aşmadan: ayın 26'sından sonra 5 gün sonrası sonraki
    döneme düşer, ödenmemiş ekstre o zaman baştan düşülmez ve "spendable değişmez" kontrolleri tarihe bağlı kırmızı olur."""
    return min(TODAY + timedelta(days=5), finance.spend_horizon(TODAY)["end"]).isoformat()


def _setup(client, tmpdb, as_of=None, balance=50000):
    client.post("/api/accounts", json={"name": "Örnek Bank", "kind": "banka", "balance": balance, "as_of": as_of or D(10)})
    acc = next(a for a in client.get("/api/accounts").json()["accounts"] if a["name"] == "Örnek Bank")
    with tmpdb.connect() as c:
        sid = c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                        "VALUES ('k', 'Deneme Bank', '4543***1234', ?, ?, 12500)", (D(12), _due())).lastrowid
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


def _acc(client, name, as_of, balance=10000, kind="banka"):
    client.post("/api/accounts", json={"name": name, "kind": kind, "balance": balance, "as_of": as_of})


def _st(tmpdb, card, amount, period_end, h):
    with tmpdb.connect() as c:
        return c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                         "VALUES (?, 'Deneme Bank', ?, ?, ?, ?)", (h, card, period_end, period_end, amount)).lastrowid


def test_anchor_rule_live_like(client, tmpdb):
    """Canlıdaki tabloya benzer uydurma veri: çapalar 15 Kas / 29 Eki / 18 Eki. 31.10'da hesapsız ödenen ekstreler
    15 Kas bakiyesinin içinde (düşülmez, uyarı çıkar); 16 Kas'ta hesapsız ödenenler hiçbir bakiyede yok (düşülür)."""
    from datetime import date as _d

    day = _d(2026, 11, 16)
    _acc(client, "Birinci Bank", "2026-11-15")
    _acc(client, "İkinci Bank", "2026-10-29")
    _acc(client, "Üçüncü Bank", "2026-10-18")
    old = [_st(tmpdb, f"4000***{n}", a, "2026-10-08", f"e{n}") for n, a in ((1111, 50000.10), (2222, 54000.20), (3333, 74000.30))]
    new = [_st(tmpdb, f"4000***{n}", a, "2026-11-08", f"y{n}") for n, a in ((2222, 48712.40), (3333, 47296.15))]
    for sid in old:
        client.post(f"/api/statements/{sid}/pay", json={"amount": 1, "paid_at": "2026-10-31"})
    for sid in new:
        client.post(f"/api/statements/{sid}/pay", json={"amount": 1, "paid_at": "2026-11-16"})
    with tmpdb.connect() as c:  # tutarlar ekstre borcu kadar
        c.execute("UPDATE statements SET paid_amount = total_debt")
    nw = finance.net_worth(day)
    items = {u["statement_id"]: u for u in nw["unknown_payments_items"]}
    assert set(items) == set(old) | set(new)
    assert not any(items[s]["deducted"] for s in old)  # (b) bir çapa 15 Kas → bakiyenin içinde
    assert all(items[s]["deducted"] for s in new)  # (a) bütün çapalar ≤ 15 Kas
    assert nw["unknown_payments"] == 96008.55  # beklenen düşüm: yalnız 16 Kas ödemeleri
    keys = [i["key"] for i in finance.insights("2026-11", day) if i["key"].startswith("belirsiz_odeme:")]
    assert len(keys) == 5  # uyarı hepsinde


def test_anchor_rule_choose_account_and_same_day(client, tmpdb):
    aid, sid = _setup(client, tmpdb, as_of=D(1))
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(2)})
    nw = _nw()
    assert nw["unknown_payments"] == 0 and nw["unknown_payments_items"][0]["deducted"] is False  # bakiyenin içinde
    # (c) hesap seçilince: ödeme çapadan önce → bakiye zaten içeriyor, değişmez; belirsiz kalmaz
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(2), "account_id": aid})
    assert _current(client, aid) == 50000 and _nw()["unknown_payments_items"] == []
    # aynı gün: çapa işaretlemeden SONRA güncellendiyse içinde, önce güncellendiyse değil
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(1)})  # hesapsıza geri: anahtar yok
    client.post(f"/api/statements/{sid}/pay", json={"amount": 12500, "paid_at": D(1), "account_id": None})
    with tmpdb.connect() as c:
        c.execute("UPDATE statements SET paid_via = NULL, paid_marked_at = '2026-01-01 10:00:00' WHERE id = ?", (sid,))
        c.execute("UPDATE accounts SET updated_at = '2026-01-01 09:00:00'")
    assert _nw()["unknown_payments"] == 12500  # çapa işaretlemeden önce: ödeme içinde değil
    with tmpdb.connect() as c:
        c.execute("UPDATE accounts SET updated_at = '2026-01-01 11:00:00'")
    assert _nw()["unknown_payments"] == 0  # çapa sonra güncellendi: içinde
    # çok eski işaret aranmaz
    with tmpdb.connect() as c:
        c.execute("UPDATE accounts SET as_of = ? WHERE id = ?", (D(200), aid))
        c.execute("UPDATE statements SET paid_at = ? WHERE id = ?", (D(90), sid))
    assert _nw()["unknown_payments_items"] == []


def test_pay_account_hint_last_then_same_bank(client, tmpdb):
    _acc(client, "Halkbank Vadesiz", D(5))
    _acc(client, "VakıfBank Maaş", D(5))
    _acc(client, "VakıfBank Ek", D(5))
    _acc(client, "Cüzdan", D(5), kind="nakit")
    accs = {a["name"]: a["id"] for a in client.get("/api/accounts").json()["accounts"]}
    with tmpdb.connect() as c:  # VakıfBank Ek daha çok kullanılıyor
        for _ in range(3):
            c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, account_id) "
                      "VALUES (?, ?, 'Deneme', 10, 'gider', ?)", (D(3), D(3)[:7], accs["VakıfBank Ek"]))
        v = c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                      "VALUES ('v', 'T. VAKIFLAR BANKASI T.A.O.', '4000***5555', ?, ?, 900)", (D(12), D(-5))).lastrowid
        z = c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                      "VALUES ('z', 'Örnek Kart Bank', '4000***6666', ?, ?, 900)", (D(12), D(-5))).lastrowid
    hint = lambda sid: next((p["pay_account_hint"], p["pay_account_hint_source"])  # noqa: E731
                            for p in client.get("/api/payables").json() if p["type"] == "statement" and p["id"] == sid)
    assert hint(v) == (accs["VakıfBank Ek"], "ayni_banka")  # aynı banka, en çok kullanılan
    assert hint(z) == (None, None)  # bu bankada hesap yok: seçim boş
    client.post(f"/api/statements/{v}/pay", json={"amount": 900, "paid_at": D(1), "account_id": accs["Halkbank Vadesiz"]})
    assert hint(v) == (accs["Halkbank Vadesiz"], "son")  # son ödenen hesap önce gelir


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
