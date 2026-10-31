"""Banka açıklaması (ham metin) ve kullanıcı notu: /api/transactions ve /api/transfers'ta raw ve note_user; not ya da
açıklama düzenlense de ham metin kaybolmaz. Adlar ve metinler uydurmadır."""
from app import rawnote

RAW = "FAST Anlık Ödeme nakliye (123456789 sorgu no'lu VakıfBank AYŞE DEMİR hesabından Enpara Bank ALİ VELİ hesabına giden)"


def test_helpers():
    assert rawnote.raw_of(f"Hesap hareketi · {RAW}") == RAW
    assert rawnote.raw_of("Hesap hareketi", "MARKET POS 123") is None  # açıklamadan türetilmez (düzeltilince değişir)
    assert rawnote.legacy_raw("Hesap hareketi", "MARKET POS 123") == "MARKET POS 123"  # yalnız onarım/iç eşleştirme
    assert rawnote.raw_of(f"Hesap hareketi · Telefon 5550000000 · Fatura no FD1 · {RAW}") == RAW
    assert rawnote.raw_of("kendi notum") is None and rawnote.raw_of(None) is None
    assert rawnote.user_note(f"Hesap hareketi · {RAW}\nkoli 3 adet") == "koli 3 adet"
    assert rawnote.user_note(f"Hesap hareketi · {RAW}") is None and rawnote.user_note("kendi notum") == "kendi notum"
    assert rawnote.raw_of(f"Hesap hareketi · {RAW}\nkoli 3 adet") == RAW


def _import(tmpdb):
    tmpdb.import_bank_rows([{"date": "2026-10-12", "description": RAW, "amount": -1500.0, "type": "gider", "hash": "r1"}],
                           owners=["AYŞE DEMİR"])
    return next(e for e in tmpdb.list_entries())["id"]


def _row(client, eid):
    return next(r for r in client.get("/api/transactions", params={"month": "2026-10"}).json() if r["uid"] == f"m{eid}")


def test_transactions_and_transfers_have_raw(client, tmpdb):
    eid = _import(tmpdb)
    r = _row(client, eid)
    assert r["raw"] == RAW and r["note_user"] is None and r["counterparty"] == "Ali Veli"
    t = client.get("/api/transfers", params={"month": "2026-10"}).json()
    item = next(i for i in t["items"] if i["id"] == eid)
    assert item["raw"] == RAW and item["note_user"] is None
    # elle girilmiş kayıt: raw yok, notun tamamı kullanıcının
    client.post("/api/entries", json={"entry_date": "2026-10-13", "description": "Kahve", "amount": 85, "kind": "gider",
                                      "category": "Diğer", "payment": "nakit", "note": "ofiste"})
    k = next(x for x in client.get("/api/transactions", params={"month": "2026-10"}).json() if x["description"] == "Kahve")
    assert k["raw"] is None and k["note_user"] == "ofiste"


def test_editing_note_and_description_keeps_raw(client, tmpdb):
    eid = _import(tmpdb)
    assert client.patch(f"/api/entries/{eid}", json={"note": "koli 3 adet", "description": "Nakliye"}).status_code == 200
    r = _row(client, eid)
    assert r["raw"] == RAW and r["note_user"] == "koli 3 adet" and r["description"] == "Nakliye"
    client.patch(f"/api/entries/{eid}", json={"note": ""})  # kullanıcı notunu sildi: ham metin kalır
    r = _row(client, eid)
    assert r["raw"] == RAW and r["note_user"] is None
    # eski istemci notun tamamını geri gönderirse olduğu gibi yazılır (ham satır kendisinde)
    client.patch(f"/api/entries/{eid}", json={"note": f"Hesap hareketi · {RAW}\nyeni not"})
    r = _row(client, eid)
    assert r["raw"] == RAW and r["note_user"] == "yeni not"


def test_repair_still_reads_raw_after_user_note(tmpdb):
    from app import bank_repair

    eid = _import(tmpdb)
    with tmpdb.connect() as c:
        c.execute("UPDATE entries SET note = note || char(10) || 'koli' WHERE id = ?", (eid,))
        e = dict(c.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone())
    assert bank_repair._raw_of(e) == RAW


# --- ham metin her içe aktarma yolunda notta; raw açıklamadan türetilmez; eski kayıtlar onarılır --------------------

CSV_RAW = "FAST GIDEN ORNEK REF 4455667788"


def test_csv_import_without_bank_name_writes_raw_and_survives_edit(client, tmpdb):
    """Web ajanının bulgusu: bankasız CSV'de not yalnız 'Hesap hareketi'ydi; açıklama düzeltilince raw da değişiyordu."""
    tmpdb.import_bank_rows([{"date": "2026-10-12", "description": CSV_RAW, "amount": -250.0, "type": "gider", "hash": "c1"}])
    eid = tmpdb.list_entries()[0]["id"]
    assert _row(client, eid)["raw"] == CSV_RAW
    client.patch(f"/api/entries/{eid}", json={"description": "Ali · gönderildi"})
    r = _row(client, eid)
    assert r["raw"] == CSV_RAW and r["description"] == "Ali · gönderildi"


def test_dekont_and_mail_paths_write_raw(tmpdb):
    from app import dekont

    tmpdb.import_bank_rows([{"date": "2026-10-12", "description": "ORNEK MARKET POS 998877", "amount": -80.0,
                             "type": "gider", "hash": "mail:abc1"}])
    e = tmpdb.list_entries()[0]
    assert rawnote.raw_of(e["note"]) == "ORNEK MARKET POS 998877"
    assert hasattr(dekont, "import_text")


def test_legacy_note_shows_no_raw(client, tmpdb):
    """Eski kayıt (not yalnız 'Hesap hareketi'): onarılana kadar raw null; açıklamadan türetilmez."""
    with tmpdb.connect() as c:
        c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, note, import_hash) "
                  "VALUES ('2026-10-05', '2026-10', 'Ali · gönderildi', 100, 'gider', 'Diğer', 'Hesap hareketi', 'old1')")
    eid = tmpdb.list_entries()[0]["id"]
    assert _row(client, eid)["raw"] is None


def test_repair_backfills_raw_only_when_description_unchanged(tmpdb):
    from app import bank_repair

    with tmpdb.connect() as c:
        for d, h, note in ((CSV_RAW, "old-a", "Hesap hareketi"),                     # değişmemiş: geri yazılır
                           ("Ali · gönderildi", "old-b", "Hesap hareketi"),          # değişmiş: dokunulmaz
                           ("EFT GELEN ORNEK 1234567", "old-c", "Hesap hareketi\nkira payı")):  # kullanıcı notu korunur
            c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, note, import_hash) "
                      "VALUES ('2026-10-05', '2026-10', ?, 100, 'gider', 'Diğer', ?, ?)", (d, note, h))
    rep = bank_repair.repair(apply=False)
    assert sorted(r["was"] for r in rep["raw_notes"]) == ["EFT GELEN ORNEK 1234567", CSV_RAW]
    assert [r["was"] for r in rep["raw_unknown"]] == ["Ali · gönderildi"]
    bank_repair.repair(apply=True)
    with tmpdb.connect() as c:
        notes = dict(c.execute("SELECT import_hash, note FROM entries"))
    assert notes["old-a"] == f"Hesap hareketi · {CSV_RAW}"
    assert notes["old-b"] == "Hesap hareketi"
    assert notes["old-c"] == "Hesap hareketi · EFT GELEN ORNEK 1234567\nkira payı"
