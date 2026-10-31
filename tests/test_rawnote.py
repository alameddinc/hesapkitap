"""Banka açıklaması (ham metin) ve kullanıcı notu: /api/transactions ve /api/transfers'ta raw ve note_user; not ya da
açıklama düzenlense de ham metin kaybolmaz. Adlar ve metinler uydurmadır."""
from app import rawnote

RAW = "FAST Anlık Ödeme nakliye (123456789 sorgu no'lu VakıfBank AYŞE DEMİR hesabından Enpara Bank ALİ VELİ hesabına giden)"


def test_helpers():
    assert rawnote.raw_of(f"Hesap hareketi · {RAW}") == RAW
    assert rawnote.raw_of("Hesap hareketi", "MARKET POS 123") == "MARKET POS 123"  # açıklama hamın kendisi
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
