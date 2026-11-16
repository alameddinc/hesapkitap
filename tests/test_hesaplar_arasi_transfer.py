"""Kendi hesapları arası transfer: dekontta yön (GİDEN / GELEN), "Gelen FAST" fatura sanılmaz, gönderen = alıcı →
hesaplar arası aktarım (iki bacak, gelir/gider değil), ikinci dekont aynı aktarıma bağlanır, döküm gelince birleşir,
elle giriş / düzenleme / silme. Bütün ad, IBAN ve numaralar uydurmadır (banka kodları gerçek: 00012 Halkbank, 00015
VakıfBank)."""
import json

import httpx

from tests.test_doc_reads import _jpeg

NAME = "DENEME KİŞİOĞLU"
HALK_IBAN = "TR110001200000000000000001"
VAKIF_IBAN = "TR220001500000000000123456"

GIDEN = f"""DEKONT
MÜŞTERİ NO : 11111111 İŞLEM TARİHİ : 16/11/2026 - 08:14
IBAN : TR11 0001 2000 0000 0000 0000 01
İŞLEM TÜRÜ : PARA TRANSFERİ - GİDEN FAST
GÖNDEREN : {NAME}
GÖNDEREN IBAN : TR11 0001 2000 0000 0000 0000 01
ALICI : {NAME}
ALICI IBAN : TR22 0001 5000 0000 0000 1234 56
ALICI BANKA : TÜRKİYE VAKIFLAR BANKASI T.A.O.
ÖDEME AMACI : BİREYSEL ÖDEME
SORGU NO : 9900000001
İŞLEM TUTARI (TL) : 137,500.00
AÇIKLAMA : GİDEN FAST - {NAME} TARAFINDAN GÖNDERİLEN
"""

# alıcı bankanın ekran görüntüsü (telefon metni): başlıkta DEKONT yok, GONDEREN Türkçe harfsiz, açıklama "GİDEN FAST"
# diyor ama işlem türü "Gelen FAST"
GELEN_OCR = f"""VAKIFBANK
İŞLEM BİLGİLERİ
MÜŞTERİ AD SOYAD Deneme Kişioğlu
MÜŞTERİ NUMARASI 222222222
İŞLEM A01 Gelen FAST Anlık Ödeme
İŞLEM TARİHİ 16.11.2026 08:14
BANKA Türkiye Halk Bankası A.Ş.
ALICI HESAP NO 00150000000000123456
GONDEREN AD SOYAD/UNVAN {NAME}
İŞLEM TUTARI 137.500,00 TL
SORGU REFERANS NO 9900000001
İŞLEM AÇIKLAMASI
GİDEN FAST - {NAME} TARAFINDAN GÖNDERİLEN
"""

GELEN_AI = {"is_transfer": True, "amount": "137.500,00", "date": "2026-11-16", "sender_name": NAME,
            "sender_iban": None, "sender_bank": "Türkiye Halk Bankası A.Ş.", "receiver_name": "Deneme Kişioğlu",
            "receiver_iban": "00150000000000123456", "receiver_bank": None, "bank": "VakıfBank",
            "description": f"GİDEN FAST - {NAME} TARAFINDAN GÖNDERİLEN", "transfer_type": "fast",
            "doc_direction": "gelen", "ref": "9900000001"}


def _gemini(monkeypatch, by_prompt) -> list:
    """Sahte Gemini: by_prompt(prompt) → model çıktısı (dict)."""
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        prompt = json["contents"][0]["parts"][0]["text"]
        calls.append(prompt)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dumps(by_prompt(prompt))}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", fake_post)
    return calls


def _dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def _accounts(tmpdb, as_of="2026-11-10"):
    tmpdb.set_account_balance("Halkbank", 200000, as_of, "elle")
    tmpdb.set_account_balance("VakıfBank", 1000, as_of, "elle")
    with tmpdb.connect() as c:
        c.execute("UPDATE accounts SET iban = ? WHERE name = 'Halkbank'", (HALK_IBAN,))
        c.execute("UPDATE accounts SET iban = ? WHERE name = 'VakıfBank'", (VAKIF_IBAN,))
        ids = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM accounts")}
    return ids["Halkbank"], ids["VakıfBank"]


def _current(tmpdb, name):
    return next(a for a in tmpdb.list_accounts() if a["name"] == name)["current"]


def _counts(tmpdb):
    with tmpdb.connect() as c:
        return (c.execute("SELECT COUNT(*) FROM own_transfers").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM entries").fetchone()[0])


# ---------------------------------------------------------------- yön ve tür

def test_direction_from_document_kind():
    from app import dekont_parse

    assert dekont_parse.doc_direction(GIDEN) == "giden"
    assert dekont_parse.doc_direction(GELEN_OCR) == "gelen"  # açıklamadaki "GİDEN FAST" değil, işlem türü
    assert dekont_parse.doc_direction("İŞLEM TÜRÜ : Gelen EFT\nAÇIKLAMA : x") == "gelen"
    assert dekont_parse.doc_direction("HAVALE\nTUTAR : 10,00") is None


def test_incoming_fast_screenshot_text_is_transfer_not_bill():
    from app import dekont_parse

    assert dekont_parse.is_transfer(GELEN_OCR)
    assert dekont_parse.is_transfer(GIDEN)


def test_own_transfer_resolves_accounts_from_ibans(client, tmpdb, monkeypatch):
    import app.parsers as parsers

    halk, vakif = _accounts(tmpdb)
    monkeypatch.setattr(parsers, "extract_text", lambda data, pw=None: GIDEN)
    d = client.post("/api/dekont/parse", files={"file": ("d.pdf", b"%PDF-1.4 a", "application/pdf")}).json()
    assert d["own_transfer"] is True and d["direction"] is None  # eski istemci: bugünkü gibi yön sorulur
    assert d["doc_direction"] == "giden"
    assert d["own"] == {"from_account": halk, "to_account": vakif, "side": "giden", "match": None}
    assert "duplicate" not in d and "debt_match" not in d


def test_same_name_is_own_transfer_even_if_owner_unknown(tmpdb):
    from app import dekont_parse

    out = dekont_parse.resolve({"sender_name": NAME, "receiver_name": NAME.title(), "amount": 10.0, "date": "2026-11-16",
                                "doc_direction": "giden"})
    assert out["own_transfer"] is True and out["own"]["side"] == "giden"


def test_unknown_parties_direction_from_document(tmpdb):
    from app import dekont_parse

    out = dekont_parse.resolve({"sender_name": "ALİ DENEME", "receiver_name": "VELİ ÖRNEKOĞLU", "amount": 10.0,
                                "date": "2026-11-16", "doc_direction": "gelen"})
    assert out["direction"] == "gelen" and out["counterparty"] == "Ali Deneme"


def test_documents_parse_incoming_fast_returns_dekont_with_phone_text(client, tmpdb, monkeypatch):
    _halk, vakif = _accounts(tmpdb)
    calls = _gemini(monkeypatch, lambda p: GELEN_AI if "PARA TRANSFERİ dekontu (havale" in p else
                    {"doc_type": "fatura", "merchant": "VakıfBank", "total": 137500})
    d = client.post("/api/documents/parse", files={"file": ("v.jpg", _jpeg(), "image/jpeg")},
                    data={"ocr_text": GELEN_OCR, "ocr_source": "vision", "dekont": "1"}).json()
    assert d["type"] == "dekont" and d["own_transfer"] is True
    assert d["own"]["to_account"] == vakif and d["own"]["side"] == "gelen"
    assert len(calls) == 1 and "doc_type" not in calls[0]  # fiş/fatura okunmadı, doğrudan dekont okuması
    assert d["dekont_file"]


def test_documents_parse_ai_says_transfer_then_dekont_read(client, tmpdb, monkeypatch):
    _accounts(tmpdb)
    calls = _gemini(monkeypatch, lambda p: {"doc_type": "transfer", "merchant": "VakıfBank", "total": 137500}
                    if "doc_type" in p else GELEN_AI)
    d = client.post("/api/documents/parse", files={"file": ("v2.jpg", _jpeg() + b"1", "image/jpeg")},
                    data={"dekont": "1"}).json()
    assert d["type"] == "dekont" and d["amount"] == 137500 and len(calls) == 2
    # eski istemci (dekont yok): bugünkü biçim (fatura) + looks_like_transfer, fatura şablonu öğrenilmez
    old = client.post("/api/documents/parse", files={"file": ("v3.jpg", _jpeg() + b"2", "image/jpeg")}).json()
    assert old["type"] == "bill" and old["looks_like_transfer"] is True


def test_upload_queue_image_dekont(client, tmpdb, monkeypatch):
    _accounts(tmpdb)
    _gemini(monkeypatch, lambda p: {"doc_type": "transfer", "merchant": "VakıfBank", "total": 137500}
            if "doc_type" in p else GELEN_AI)
    r = client.post("/api/statements/upload", files={"files": ("v.jpg", _jpeg() + b"q", "image/jpeg")},
                    data={"docs": "1", "dekont_docs": "1"}).json()[0]
    assert r["status"] == "document" and r["doc_type"] == "dekont" and r["doc"]["own_transfer"] is True


def test_upload_queue_pdf_dekont_goes_to_form_for_new_client(client, tmpdb, monkeypatch):
    import app.parsers as parsers

    _accounts(tmpdb)
    monkeypatch.setattr(parsers, "extract_text", lambda data, pw=None: GIDEN)
    r = client.post("/api/statements/upload", files={"files": ("Dekont.pdf", b"%PDF-1.4 q", "application/pdf")},
                    data={"docs": "1", "dekont_docs": "1"}).json()[0]
    assert r["status"] == "document" and r["doc_type"] == "dekont" and r["doc"]["own"]["side"] == "giden"
    assert _counts(tmpdb) == (0, 0)  # kendiliğinden kaydedilmedi


# ---------------------------------------------------------------- kayıt

def test_manual_transfer_moves_balance_not_income_or_expense(client, tmpdb):
    halk, vakif = _accounts(tmpdb)
    r = client.post("/api/own-transfers", json={"date": "2026-11-16", "amount": 137500, "from_account_id": halk,
                                                "to_account_id": vakif, "description": "Birikim"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["linked"] is False and [x["amount"] for x in body["legs"]] == [-137500, 137500]
    assert {x["pair_id"] for x in body["legs"]} == {body["id"]} and {x["source"] for x in body["legs"]} == {"elle"}
    assert _current(tmpdb, "Halkbank") == 62500 and _current(tmpdb, "VakıfBank") == 138500
    assert _counts(tmpdb) == (2, 0)  # gelir/gider kaydı yok
    t = client.get("/api/transfers").json()["totals"]
    assert (t["sent"], t["received"], t["own_in"], t["own_out"]) == (0, 0, 137500, 137500)


def test_same_day_transfer_after_balance_update_counts(client, tmpdb):
    halk, vakif = _accounts(tmpdb, as_of="2026-11-16")
    with tmpdb.connect() as c:  # bakiye biraz önce güncellendi (aynı saniye değil)
        c.execute("UPDATE accounts SET updated_at = datetime('now', '-1 minute')")
    client.post("/api/own-transfers", json={"date": "2026-11-16", "amount": 500, "from_account_id": halk,
                                            "to_account_id": vakif})
    assert _current(tmpdb, "Halkbank") == 199500 and _current(tmpdb, "VakıfBank") == 1500


def test_validation(client, tmpdb):
    halk, vakif = _accounts(tmpdb)
    assert client.post("/api/own-transfers", json={"date": "2026-11-16", "amount": 5, "from_account_id": halk,
                                                   "to_account_id": halk}).status_code == 422
    assert client.post("/api/own-transfers", json={"date": "2026-11-16", "amount": 0, "from_account_id": halk,
                                                   "to_account_id": vakif}).status_code == 422
    assert client.post("/api/own-transfers", json={"date": "2026-11-16", "amount": 5, "from_account_id": halk,
                                                   "to_account_id": 999}).status_code == 422


def test_two_dekonts_one_transfer(client, tmpdb, monkeypatch):
    halk, vakif = _accounts(tmpdb)
    first = client.post("/api/own-transfers", json={
        "date": "2026-11-16", "amount": 137500, "from_account_id": halk, "to_account_id": vakif,
        "ref": "9900000001", "dekont_file": None, "dekont_side": "giden"}).json()
    assert first["linked"] is False and {x["source"] for x in first["legs"]} == {"dekont"}
    # ikinci dekont (alıcı bankanın): okumada eşleşme görünür, kayıtta yeni bacak açılmaz
    from app import dekont_parse

    out = dekont_parse.resolve({"sender_name": NAME,
                                "receiver_name": NAME, "receiver_iban": VAKIF_IBAN, "sender_iban": HALK_IBAN,
                                "amount": 137500.0, "date": "2026-11-16", "ref": "9900000001", "doc_direction": "gelen"})
    assert out["own"]["match"]["id"] == first["id"]
    second = client.post("/api/own-transfers", json={
        "date": "2026-11-16", "amount": 137500, "from_account_id": halk, "to_account_id": vakif,
        "ref": "9900000001", "dekont_side": "gelen"}).json()
    assert second["linked"] is True and second["id"] == first["id"] and second["created"] == 0
    assert _counts(tmpdb) == (2, 0)
    assert _current(tmpdb, "Halkbank") == 62500 and _current(tmpdb, "VakıfBank") == 138500


def test_second_dekont_file_attaches_and_repeat_is_duplicate(client, tmpdb):
    halk, vakif = _accounts(tmpdb)
    a, b = "a" * 64 + ".jpg", "b" * 64 + ".jpg"
    with tmpdb.connect() as c:
        for n in (a, b):
            c.execute("INSERT INTO stored_files (kind, name, size, created_at, legacy) VALUES ('receipts', ?, 1, "
                      "CURRENT_TIMESTAMP, 0)", (n,))
    base = {"date": "2026-11-16", "amount": 137500, "from_account_id": halk, "to_account_id": vakif, "ref": "9900000001"}
    p = client.post("/api/own-transfers", json={**base, "dekont_file": a, "dekont_side": "giden"}).json()
    client.post("/api/own-transfers", json={**base, "dekont_file": b, "dekont_side": "gelen"})
    with tmpdb.connect() as c:
        files = {r[0]: r[1] for r in c.execute("SELECT amount, receipt_file FROM own_transfers")}
    assert files == {-137500: a, 137500: b}
    again = client.post("/api/own-transfers", json={**base, "dekont_file": b, "dekont_side": "gelen"})
    assert again.status_code == 409 and again.json()["own_transfer_id"] == p["id"]


def test_wrongly_saved_dekont_entry_becomes_transfer(client, tmpdb):
    """Önceden gider sanılıp kaydedilmiş aynı sorgu numaralı dekont: aktarıma dönüşür, çift sayım kalmaz."""
    halk, vakif = _accounts(tmpdb)
    r = client.post("/api/dekont/save", json={"date": "2026-11-16", "amount": 137500, "direction": "giden",
                                              "counterparty": "Deneme Kişioğlu", "account_id": halk,
                                              "ref": "9900000001"})
    assert r.status_code == 200
    assert _current(tmpdb, "Halkbank") == 62500 and _current(tmpdb, "VakıfBank") == 1000
    out = client.post("/api/own-transfers", json={"date": "2026-11-16", "amount": 137500, "from_account_id": halk,
                                                  "to_account_id": vakif, "ref": "9900000001",
                                                  "dekont_side": "gelen"}).json()
    assert out["replaced"] == [r.json()["id"]]
    assert _counts(tmpdb) == (2, 0)
    assert _current(tmpdb, "Halkbank") == 62500 and _current(tmpdb, "VakıfBank") == 138500


def test_statement_leg_completed_by_dekont(client, tmpdb):
    """Halkbank dökümünde çıkış zaten virman; VakıfBank'ın dekontu eksik giriş bacağını ekler."""
    halk, vakif = _accounts(tmpdb)
    with tmpdb.connect() as c:
        tmpdb.add_own_transfer(c, "2026-11-16", -137500.0, "Halkbank", "Kendi hesabına", "st-out", [], halk)
    out = client.post("/api/own-transfers", json={"date": "2026-11-16", "amount": 137500, "from_account_id": halk,
                                                  "to_account_id": vakif, "ref": "9900000001",
                                                  "dekont_side": "gelen"}).json()
    assert out["linked"] is True and out["created"] == 1
    assert sorted(x["amount"] for x in out["legs"]) == [-137500, 137500]
    assert _current(tmpdb, "VakıfBank") == 138500


def test_statement_import_merges_manual_legs(client, tmpdb):
    halk, vakif = _accounts(tmpdb)
    pid = client.post("/api/own-transfers", json={"date": "2026-11-16", "amount": 137500, "from_account_id": halk,
                                                  "to_account_id": vakif}).json()["id"]
    st = tmpdb.import_bank_rows([{"date": "2026-11-16", "description": "GIDEN FAST - DENEME", "amount": -137500.0,
                                  "type": "gider", "hash": "h-out"}], account="Halkbank", account_id=halk)
    st2 = tmpdb.import_bank_rows([{"date": "2026-11-17", "description": "Gelen FAST", "amount": 137500.0,
                                   "type": "gelir", "hash": "h-in"}], account="VakıfBank", account_id=vakif)
    assert st["virman"] == 1 and st2["virman"] == 1 and st["gider"] == 0 and st2["gelir"] == 0
    with tmpdb.connect() as c:
        legs = [dict(r) for r in c.execute("SELECT amount, import_hash, pair_id FROM own_transfers ORDER BY amount")]
    assert legs == [{"amount": -137500, "import_hash": "h-out", "pair_id": pid},
                    {"amount": 137500, "import_hash": "h-in", "pair_id": pid}]
    assert _counts(tmpdb)[1] == 0


def test_edit_and_delete_manual_only(client, tmpdb):
    halk, vakif = _accounts(tmpdb)
    pid = client.post("/api/own-transfers", json={"date": "2026-11-16", "amount": 100, "from_account_id": halk,
                                                  "to_account_id": vakif}).json()["id"]
    r = client.put(f"/api/own-transfers/{pid}", json={"date": "2026-11-15", "amount": 250, "from_account_id": vakif,
                                                      "to_account_id": halk, "note": "geri"})
    assert r.status_code == 200
    assert [(x["account_id"], x["amount"], x["transfer_date"]) for x in r.json()["legs"]] == \
        [(vakif, -250, "2026-11-15"), (halk, 250, "2026-11-15")]
    assert _current(tmpdb, "Halkbank") == 200250 and _current(tmpdb, "VakıfBank") == 750
    # "gelir/gider say" elle girilen aktarımda yok (silinir)
    assert client.patch(f"/api/own-transfers/{pid}", json={"not_transfer": True}).status_code == 409
    assert client.delete(f"/api/own-transfers/{pid}").json() == {"deleted": 2}
    assert _counts(tmpdb) == (0, 0)
    with tmpdb.connect() as c:
        tmpdb.add_own_transfer(c, "2026-11-16", -10.0, "Halkbank", "x", "st-1", [], halk)
        sid = c.execute("SELECT id FROM own_transfers").fetchone()[0]
    assert client.delete(f"/api/own-transfers/{sid}").status_code == 409
    assert client.put(f"/api/own-transfers/{sid}", json={"date": "2026-11-16", "amount": 5, "from_account_id": halk,
                                                         "to_account_id": vakif}).status_code == 409


def test_capabilities():
    from app.version import CAPABILITIES

    assert "own_transfer_manual" in CAPABILITIES and "dekont_doc" in CAPABILITIES


def test_classify_photo_with_phone_text(client):
    r = client.post("/api/documents/classify", files={"file": ("v.jpg", _jpeg(), "image/jpeg")}, data={"ocr_text": GELEN_OCR})
    assert r.json() == {"suggest": "dekont"}
    assert client.post("/api/documents/classify", files={"file": ("v.jpg", _jpeg(), "image/jpeg")}).json() == {"suggest": None}
