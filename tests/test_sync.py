"""Eşitleme (app/sync.py): elle girilen kayıtlar ↔ hesap dökümü / kart ekstresi. Sahte veriler."""
from tests.test_book import _member, owner  # noqa: F401 - fixture


def _row(d, desc, amount, h, balance=None):
    return {"date": d, "description": desc, "amount": amount, "type": "gelir" if amount > 0 else "gider", "hash": h,
            "balance": balance}


def _manual(client, d="2026-10-20", desc="Market", amount=432.0, **kw):
    body = {"entry_date": d, "description": desc, "amount": amount, "kind": kw.pop("kind", "gider"),
            "category": kw.pop("category", "Market & Gıda"), "payment": kw.pop("payment", "nakit"), **kw}
    r = client.post("/api/entries", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _questions(client):
    return [r for r in client.get("/api/reminders").json() if r["kind"] == "esles"]


def _entries(tmpdb):
    return sorted(tmpdb.list_entries(), key=lambda e: e["id"])


# ---------------------------------------------------------------- kesin eşleşme

def test_exact_match_merges_and_keeps_manual_entry(client, tmpdb):
    eid = _manual(client, note="sütlaç dahil")
    st = tmpdb.import_bank_rows([_row("2026-10-21", "MAHALLE MKT POS", -432.0, "a1", 9568.0)], account="Garanti BBVA")
    assert st["eslesen_elle"] == 1 and st["gider"] == 0
    es = _entries(tmpdb)
    assert len(es) == 1 and es[0]["id"] == eid                       # çift sayım yok, elle girilen kaldı
    e = es[0]
    assert (e["description"], e["category"], e["note"], e["import_hash"]) == ("Market", "Market & Gıda", "sütlaç dahil", "a1")
    m = e["match"]
    assert m["source"] == "dokum" and m["how"] == "otomatik" and m["date"] == "2026-10-21"
    assert m["description"] == "MAHALLE MKT POS" and m["balance"] == 9568.0 and m["account"] == "Garanti BBVA" and m["can_undo"]
    row = next(x for x in client.get("/api/transactions", params={"month": "2026-10"}).json() if x["uid"] == f"m{eid}")
    assert row["match"]["account"] == "Garanti BBVA"
    # tekrar yükleme bir şey eklemez
    assert tmpdb.import_bank_rows([_row("2026-10-21", "MAHALLE MKT POS", -432.0, "a1")], account="Garanti BBVA")["tekrar"] == 1


def test_unmatch_splits_back_and_never_merges_again(client, tmpdb):
    eid = _manual(client)
    tmpdb.import_bank_rows([_row("2026-10-21", "MAHALLE MKT POS", -432.0, "a1", 9568.0)], account="Garanti BBVA")
    r = client.post(f"/api/entries/{eid}/unmatch").json()
    assert r["ok"] and r["source"] == "dokum" and r["undo_id"]
    es = _entries(tmpdb)
    assert len(es) == 2
    manual, bank = es
    assert manual["import_hash"] is None and manual["match"] is None and manual["account_id"] is None
    assert bank["import_hash"] == "a1" and bank["description"] == "MAHALLE MKT POS" and bank["note"].startswith("Hesap hareketi")
    assert bank["account_id"] and bank["entry_date"] == "2026-10-21"
    # bir daha sorulmaz, kendiliğinden de birleşmez
    from app import sync

    assert sync.reconcile() == {"otomatik": 0, "kural": 0} and _questions(client) == []
    # geri almanın geri alınması: ilk hal
    assert client.post(f"/api/imports/{r['undo_id']}/undo").json()["ok"]
    assert len(_entries(tmpdb)) == 1


def test_manual_entry_after_import_merges(client, tmpdb):
    tmpdb.import_bank_rows([_row("2026-10-21", "FAST PSIKOLOG AHMET ORNEK", -3000.0, "p1")], account="Halkbank")
    r = client.post("/api/entries", json={"entry_date": "2026-10-20", "description": "Psikolog", "amount": 3000,
                                          "kind": "gider", "category": "Sağlık", "payment": "havale"}).json()
    assert r["match"] and r["match"]["source"] == "dokum" and r["match"]["account"] == "Halkbank"
    es = _entries(tmpdb)
    assert len(es) == 1 and es[0]["description"] == "Psikolog" and es[0]["import_hash"] == "p1"


def test_cash_account_entry_is_not_matched_to_bank(client, tmpdb):
    client.post("/api/accounts", json={"name": "Cüzdan", "kind": "nakit", "balance": 1000})
    _manual(client)  # tek nakit hesabı: kayıt cüzdana düşer
    st = tmpdb.import_bank_rows([_row("2026-10-20", "MAHALLE MKT POS", -432.0, "a1")], account="Garanti BBVA")
    assert st["eslesen_elle"] == 0 and st["gider"] == 1 and len(_entries(tmpdb)) == 2 and _questions(client) == []


def test_other_bank_account_is_not_matched(client, tmpdb):
    client.post("/api/accounts", json={"name": "Halkbank", "kind": "banka", "balance": 1000})
    hb = next(a["id"] for a in tmpdb.list_accounts() if a["name"] == "Halkbank")
    _manual(client, account_id=hb)
    st = tmpdb.import_bank_rows([_row("2026-10-20", "MAHALLE MKT POS", -432.0, "a1")], account="Garanti BBVA")
    assert st["eslesen_elle"] == 0 and _questions(client) == []


# ---------------------------------------------------------------- muhtemel eşleşme: sor

def test_small_difference_is_asked_and_same_merges(client, tmpdb):
    eid = _manual(client)
    st = tmpdb.import_bank_rows([_row("2026-10-20", "MAHALLE MKT POS", -432.5, "a1", 100.0)], account="Garanti BBVA")
    assert st["eslesen_elle"] == 0 and st["gider"] == 1 and st["eslesme_sorusu"] == 1
    q = _questions(client)
    assert len(q) == 1
    q = q[0]
    bank_id = next(e["id"] for e in _entries(tmpdb) if e["import_hash"] == "a1")
    assert q["id"] == f"esles:{eid}:e{bank_id}" and q["title"] == "Bu ikisi aynı mı?" and q["reason"] == "tutar"
    assert q["tag"] == "₺0,50 fark" and "“Market” ₺432" in q["text"] and "“MAHALLE MKT POS” ₺432,50" in q["text"]
    assert q["pair"]["entry"]["category"] == "Market & Gıda" and q["pair"]["other"]["account"] == "Garanti BBVA"
    r = client.post(f"/api/entries/{eid}/match", json={"other": q["other"], "same": True}).json()
    assert r["ok"] and r["same"] and r["undo_id"] and "learned" not in r
    es = _entries(tmpdb)
    assert len(es) == 1 and es[0]["id"] == eid and es[0]["amount"] == 432.5 and es[0]["match"]["how"] == "onay"
    assert _questions(client) == []
    # stale soru: ikinci kez cevaplanamaz
    assert client.post(f"/api/entries/{eid}/match", json={"other": q["other"], "same": True}).status_code == 409
    # geri al: tutar da eski haline döner
    client.post(f"/api/entries/{eid}/unmatch")
    assert sorted(e["amount"] for e in _entries(tmpdb)) == [432.0, 432.5]


def test_three_days_apart_and_two_candidates_are_asked(client, tmpdb):
    a = _manual(client, d="2026-10-17")                      # 3 gün arayla: sorulur
    st = tmpdb.import_bank_rows([_row("2026-10-20", "MAHALLE MKT POS", -432.0, "a1")], account="Garanti BBVA")
    assert st["eslesen_elle"] == 0
    q = _questions(client)
    assert [x["reason"] for x in q] == ["tarih"] and q[0]["tag"] == "3 gün arayla" and q[0]["entry_id"] == a
    # aynı tutarda ikinci elle kayıt: iki aday → yine sorulur (kendiliğinden birleşmez)
    b = _manual(client, d="2026-10-21", desc="Bakkal")
    assert {x["entry_id"] for x in _questions(client)} == {a, b}
    assert all(x["reason"] == "aday" and x["tag"] == "2 aday" for x in _questions(client))


def test_two_same_rows_in_one_statement_are_asked(client, tmpdb):
    _manual(client)
    st = tmpdb.import_bank_rows([_row("2026-10-20", "MARKET A", -432.0, "a1"), _row("2026-10-21", "MARKET B", -432.0, "a2")],
                                account="Garanti BBVA")
    assert st["eslesen_elle"] == 0 and st["gider"] == 2 and len(_questions(client)) == 2


def test_different_never_asks_again(client, tmpdb):
    eid = _manual(client)
    tmpdb.import_bank_rows([_row("2026-10-20", "MAHALLE MKT POS", -432.5, "a1")], account="Garanti BBVA")
    q = _questions(client)[0]
    assert client.post(f"/api/entries/{eid}/match", json={"other": q["other"], "same": False}).json() == {"ok": True, "same": False}
    assert _questions(client) == [] and len(_entries(tmpdb)) == 2


def test_learning_rule_after_two_confirmations(client, tmpdb):
    for i, d in enumerate(("2026-08-10", "2026-09-10")):
        eid = _manual(client, d=d, amount=200.0 + i)
        tmpdb.import_bank_rows([_row(d, f"MAHALLE MKT/{i}1234", -(200.5 + i), f"r{i}")], account="Garanti BBVA")
        q = _questions(client)[0]
        r = client.post(f"/api/entries/{eid}/match", json={"other": q["other"], "same": True}).json()
    assert r["learned"]["entry"] == "Market"                             # ikinci onayda kural oldu
    rules = client.get("/api/match-rules").json()
    assert len(rules) == 1 and rules[0]["pattern"] == "MAHALLE MKT" and rules[0]["hits"] == 2
    # üçüncü ay: aynı kalıp, küçük fark → sorulmadan birleşir
    eid = _manual(client, d="2026-10-10", amount=300.0)
    st = tmpdb.import_bank_rows([_row("2026-10-11", "MAHALLE MKT/99999", -301.2, "r3")], account="Garanti BBVA")
    assert st.get("eslesen_kural") == 1 and _questions(client) == []
    e = next(x for x in _entries(tmpdb) if x["id"] == eid)
    assert e["match"]["how"] == "kural" and e["amount"] == 301.2
    assert client.get("/api/match-rules").json()[0]["used"] == 1
    # kural silinebilir
    assert client.delete(f"/api/match-rules/{rules[0]['id']}").json()["ok"] and client.get("/api/match-rules").json() == []


# ---------------------------------------------------------------- kart ekstresi

def _card(tmpdb, d, desc, amount):
    with tmpdb.connect() as c:
        sid = c.execute("SELECT id FROM statements").fetchone()
        sid = sid[0] if sid else c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end) "
                                           "VALUES ('k', 'VakıfBank', '4543***1234', '2026-10-26')").lastrowid
        return c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
                         "VALUES (?, ?, ?, ?, 'harcama', 'Kategorisiz')", (sid, d, desc, amount)).lastrowid


def test_card_exact_still_auto_links_and_unmatch_restores(client, tmpdb):
    eid = _manual(client, payment="kredi_karti", note="tatlı")
    tid = _card(tmpdb, "2026-10-21", "MAHALLE MKT", 432.0)
    assert tmpdb.auto_link() == 1
    e = next(x for x in _entries(tmpdb) if x["id"] == eid)
    assert e["linked_txn_id"] == tid and e["match"]["source"] == "ekstre" and e["match"]["account"] == "VakıfBank ••1234"
    card = next(t for t in tmpdb.card_transactions() if t["id"] == tid)
    assert card["category"] == "Market & Gıda" and "tatlı" in card["note"]
    r = client.post(f"/api/entries/{eid}/unmatch").json()
    assert r["source"] == "ekstre" and r["txn_id"] == tid
    card = next(t for t in tmpdb.card_transactions() if t["id"] == tid)
    assert card["category"] == "Kategorisiz" and not card["note"]       # ekstre satırı eski haline döndü
    assert tmpdb.auto_link() == 0                                         # bir daha kendiliğinden bağlanmaz
    assert next(x for x in _entries(tmpdb) if x["id"] == eid)["linked_txn_id"] is None


def test_card_probable_is_asked(client, tmpdb):
    eid = _manual(client, payment="kredi_karti", amount=500.0)
    tid = _card(tmpdb, "2026-10-24", "ORNEK MAGAZA", 505.0)   # 4 gün, %1 fark: auto_link bağlamaz
    assert tmpdb.auto_link() == 0
    q = _questions(client)
    assert [x["id"] for x in q] == [f"esles:{eid}:t{tid}"] and q[0]["source"] == "ekstre"
    assert q[0]["pair"]["other"]["account"] == "VakıfBank ••1234" and "ekstredeki" in q[0]["text"]
    client.post(f"/api/entries/{eid}/match", json={"other": f"t{tid}", "same": True})
    e = next(x for x in _entries(tmpdb) if x["id"] == eid)
    assert e["linked_txn_id"] == tid and e["match"]["how"] == "onay" and _questions(client) == []
    # ayın harcaması bir kez sayılır (kart satırı)
    s = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]
    assert s["manual_expense"] == 0


# ---------------------------------------------------------------- hesap eşleştirme

def test_statement_account_is_asked_then_remembered(client, tmpdb):
    from app import sync

    client.post("/api/accounts", json={"name": "Garanti", "kind": "banka", "balance": 5000, "as_of": "2026-10-01"})
    gid = next(a["id"] for a in tmpdb.list_accounts() if a["name"] == "Garanti")
    r = sync.resolve_account("T. Garanti Bankası A.Ş.", "TR120006200000000012345678")
    assert r["status"] == "ask" and [c["id"] for c in r["candidates"]] == [gid] and r["iban_last4"] == "5678"
    # Evet: seçilen hesaba gider, IBAN ve banka adı hatırlanır
    tmpdb.import_bank_rows([_row("2026-10-20", "x", -10.0, "h1", 990.0)], account="T. Garanti Bankası A.Ş.",
                           account_id=gid, iban="TR120006200000000012345678")
    a = next(a for a in tmpdb.list_accounts() if a["id"] == gid)
    assert (a["balance"], a["as_of"], a["alias"]) == (990.0, "2026-10-20", "Garanti BBVA")
    assert [x["name"] for x in tmpdb.list_accounts()] == ["Garanti"]       # ikinci (kopya) hesap açılmadı
    assert sync.resolve_account("Garanti BBVA", None)["account_id"] == gid
    assert sync.resolve_account("Garanti BBVA", "TR120006200000000012345678")["via"] in ("iban", "alias")


def test_unanswered_account_question_merge_and_undo(client, tmpdb):
    client.post("/api/accounts", json={"name": "Garanti", "kind": "banka", "balance": 5000, "as_of": "2026-10-01"})
    gid = next(a["id"] for a in tmpdb.list_accounts() if a["name"] == "Garanti")
    _manual(client, d="2026-10-05", desc="Kira", amount=15000, account_id=gid, payment="havale")
    # e-postadan gelen döküm gibi: soru cevaplanmadan içe aktarıldı → yeni hesap açıldı, Yapılacaklar'da sorulur
    tmpdb.import_bank_rows([_row("2026-10-05", "KIRA ODEMESI EFT", -15000.0, "k1", 30000.0),
                            _row("2026-10-20", "MAAS", 50000.0, "m1", 80000.0)], account="Garanti BBVA")
    bid = next(a["id"] for a in tmpdb.list_accounts() if a["name"] == "Garanti BBVA")
    q = [r for r in client.get("/api/reminders").json() if r["kind"] == "hesap"]
    assert [x["id"] for x in q] == [f"hesap:{bid}:{gid}"] and q[0]["title"] == "Bu döküm “Garanti” hesabına mı ait?"
    assert len(_entries(tmpdb)) == 3                                     # farklı hesap: kira iki kez (henüz)
    r = client.post(f"/api/accounts/{bid}/merge", json={"into": gid}).json()
    assert r["ok"] and r["undo_id"] and r["matched"] == 1 and r["balance_from"] == "merged"
    accs = tmpdb.list_accounts()
    assert [a["name"] for a in accs] == ["Garanti"]
    g = accs[0]
    assert (g["balance"], g["as_of"], g["alias"]) == (80000.0, "2026-10-20", "Garanti BBVA")
    es = _entries(tmpdb)
    assert len(es) == 2 and all(e["account_id"] == gid for e in es)       # kira birleşti (aynı hesap artık)
    assert [x for x in client.get("/api/reminders").json() if x["kind"] == "hesap"] == []
    # sonraki döküm doğrudan Garanti'ye
    tmpdb.import_bank_rows([_row("2026-10-25", "y", -5.0, "y1", 79995.0)], account="Garanti BBVA")
    assert [a["name"] for a in tmpdb.list_accounts()] == ["Garanti"]
    tmpdb.init_db()                                                      # açılışta dökümün bankası yeniden açılmaz
    assert [a["name"] for a in tmpdb.list_accounts()] == ["Garanti"]
    # geri al: iki hesap geri gelir
    client.post(f"/api/imports/{r['undo_id']}/undo", params={"force": True})
    assert sorted(a["name"] for a in tmpdb.list_accounts()) == ["Garanti", "Garanti BBVA"]


def test_keep_separate_answer(client, tmpdb):
    client.post("/api/accounts", json={"name": "Garanti", "kind": "banka", "balance": 5000})
    tmpdb.import_bank_rows([_row("2026-10-20", "x", -10.0, "h1", 990.0)], account="Garanti BBVA")
    q = next(r for r in client.get("/api/reminders").json() if r["kind"] == "hesap")
    assert client.post(f"/api/accounts/{q['account_id']}/separate", json={"other": q["into"]}).json()["ok"]
    assert [r for r in client.get("/api/reminders").json() if r["kind"] == "hesap"] == []


def test_iban_parsing():
    from app import sync

    assert sync.find_iban("Hesap Sahibi: AYŞE\nIBAN: TR12 0006 2000 0000 0012 3456 78\nFAST TR99 0001 ...") == \
        "TR120006200000000012345678"
    assert sync.find_iban("GIDEN FAST TR990001000000000000000000 Ali") is None  # etiketsiz karşı taraf IBAN'ı değil
    assert sync.find_iban_rows([["Hesap"], ["IBAN", "TR12 0006 2000 0000 0012 3456 78"]]) == "TR120006200000000012345678"


def test_bank_import_preview_reports_account_match(client, tmpdb):
    client.post("/api/accounts", json={"name": "Garanti", "kind": "banka", "balance": 5000})
    csv = "IBAN;TR12 0006 2000 0000 0012 3456 78\nTarih;Açıklama;Tutar\n14.10.2026;MARKET;-10,00\n".encode()
    pv = client.post("/api/bank-import/preview", files={"file": ("h.csv", csv, "text/csv")}).json()
    assert pv["iban"] == "TR120006200000000012345678" and pv["account_match"]["status"] == "none"
    gid = tmpdb.list_accounts()[0]["id"]
    r = client.post("/api/bank-import", files={"file": ("h.csv", csv, "text/csv")},
                    data={"mapping": '{"date": 0, "description": 1, "amount": 2}', "header_row": "1", "account_id": str(gid)})
    assert r.status_code == 200 and r.json()["gider"] == 1
    assert tmpdb.list_entries()[0]["account_id"] == gid and tmpdb.list_accounts()[0]["iban"] == "TR120006200000000012345678"


# ---------------------------------------------------------------- ortak defter ve Başlarken

def test_viewer_cannot_match_editor_can(owner):
    owner.post("/api/entries", json={"entry_date": "2026-10-20", "description": "Market", "amount": 432, "kind": "gider",
                                     "category": "Market & Gıda", "payment": "nakit"})
    v = _member(owner, name="cocuk", role="viewer")
    assert v.post("/api/entries/1/unmatch").status_code == 403
    assert [r for r in v.get("/api/reminders").json() if r["kind"] == "esles"] == []
    ed = _member(owner, name="esim", role="editor")
    assert ed.post("/api/entries/1/unmatch").status_code == 409  # yetkisi var; kayıt eşleşmemiş
    assert ed.post("/api/accounts/1/merge", json={"into": 2}).status_code == 403   # hesap birleştirme sahibin/tam yetkilinin
    assert ed.get("/api/match-rules").status_code == 403


def test_onboarding_v2_first_step_points_to_statement(client, tmpdb):
    s = client.get("/api/onboarding", params={"v": 2}).json()["steps"][0]
    assert s["key"] == "hesap" and s["title"] == "Banka hesabını ekle" and s["cta"] == "Hesap dökümü yükle"
    assert s["text"] == "En kolayı: hesap dökümünü yükle; hesap, bakiye ve hareketler tek seferde gelir."
    assert s["action"] == "dokum" and s["alt"] == {"cta": "Elle ekle", "route": "hesaplar", "action": "account"}
    assert client.get("/api/onboarding").json()["steps"][0]["key"] == "ekstre"  # web (v1) aynı
