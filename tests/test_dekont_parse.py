"""Havale/EFT dekontu (fotoğraf ya da PDF) → Transferler: okuma (PDF metni yapay zekâsız, fotoğraf sahte Gemini),
kimden kime (hesap IBAN'ı / kendi adı), IBAN maskeleme, okunamadı → hak düşmez, çift dekont, borç/alacak önerisi,
döküm birleşmesi ve fatura akışında yanlış seçim koruması. Bütün ad ve IBAN'lar uydurmadır."""
import json

import httpx
import pytest

from tests import test_plans
from tests.test_dekont import DEKONT
from tests.test_doc_reads import _jpeg, _reads
from tests.test_plans import _user

hosted = test_plans.hosted

# DEKONT (tests/test_dekont.py): gönderen AYŞE ÖRNEK TR12 0001 2009 9990 0001 2345 67 → alıcı ALİ ÖRNEK
# TR99 0006 4000 0000 0000 0000 01, 24.10.2026, 4.850,00, Kira, sorgu no 1234567890
SENDER_IBAN = "TR120001200999900001234567"
RECEIVER_IBAN = "TR990006400000000000000001"


def _pdf_text(monkeypatch, text=DEKONT):
    import app.parsers as parsers

    monkeypatch.setattr(parsers, "extract_text", lambda data, pw=None: text)


def _no_ai(monkeypatch):
    from app import receipt

    def boom(*a, **k):
        raise AssertionError("yapay zekâ çağrılmamalıydı")

    monkeypatch.setattr(receipt.httpx, "post", boom)


def _gemini(monkeypatch, out: dict) -> list:
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json["contents"][0]["parts"][0]["text"])
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dumps(out)}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", fake_post)
    return calls


def _dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def _account(tmpdb, name="Halkbank", iban=None) -> int:
    acc = tmpdb.ensure_account(name)
    if iban:
        with tmpdb.connect() as c:
            c.execute("UPDATE accounts SET iban = ? WHERE id = ?", (iban, acc))
    return acc


def _pdf(client, path="/api/dekont/parse", data=None, name="Dekont.pdf"):
    return client.post(path, files={"file": (name, b"%PDF-1.4 dekont", "application/pdf")}, data=data or {})


GELEN_AI = {"is_transfer": True, "amount": "1.250,00", "date": "2026-11-04", "sender_name": "MEHMET DENEME",
            "sender_iban": "TR33 0001 0000 0000 0000 0000 77", "sender_bank": "Ziraat Bankası",
            "receiver_name": "AYŞE ÖRNEK", "receiver_iban": "TR12 **** **** **** **** 2345 67",
            "receiver_bank": "Halkbank", "bank": "Ziraat Bankası", "description": "Kasım kira payı",
            "transfer_type": "fast", "ref": "AB-998877"}


# ---------------------------------------------------------------- okuma

def test_pdf_text_path_without_ai_giden_by_iban(client, tmpdb, monkeypatch):
    _pdf_text(monkeypatch)
    _no_ai(monkeypatch)
    acc = _account(tmpdb, iban=SENDER_IBAN)
    r = _pdf(client)
    assert r.status_code == 200
    d = r.json()
    assert (d["type"], d["source"], d["direction"], d["amount"], d["date"]) == ("dekont", "metin", "giden", 4850.0, "2026-10-24")
    assert d["counterparty"] == "Ali Örnek" and d["counterparty_known"] is False
    assert d["account_hint"] == acc and d["iban_last4"] == "0001" and d["description"] == "Kira"
    assert d["transfer_type"] == "fast" and d["ref"] == "1234567890"
    assert d["parties"]["sender"]["is_me"] is True and d["parties"]["receiver"]["is_me"] is False
    assert d["dekont_file"] and d["dekont_file"].endswith(".pdf")
    # IBAN tam dönmez (maskeleme): yalnız son 4 hane
    body = r.text.replace(" ", "")
    assert SENDER_IBAN not in body and RECEIVER_IBAN not in body and "TR12" not in body


def test_photo_ai_gelen_by_masked_iban_last4(client, tmpdb, monkeypatch):
    calls = _gemini(monkeypatch, GELEN_AI)
    acc = _account(tmpdb, iban=SENDER_IBAN)  # alıcının maskeli IBAN'ı ...2345 67 → bu hesap
    d = client.post("/api/dekont/parse", files={"file": ("d.jpg", _jpeg(), "image/jpeg")}).json()
    assert len(calls) == 1 and "PARA TRANSFERİ dekontu" in calls[0]
    assert (d["source"], d["direction"], d["amount"], d["counterparty"]) == ("yapay_zeka", "gelen", 1250.0, "Mehmet Deneme")
    assert d["account_hint"] == acc and d["iban_last4"] == "0077" and d["description"] == "Kasım kira payı"
    assert d["ref"] == "AB998877" and d["dekont_file"].endswith(".jpg")
    assert "TR330001" not in d.__repr__().replace(" ", "")


def test_direction_by_own_name_and_bank_account_hint(client, tmpdb, monkeypatch):
    _gemini(monkeypatch, {**GELEN_AI, "receiver_iban": None})
    tmpdb.remember_owner_name("AYŞE ÖRNEK")  # dökümlerin sahibi
    acc = _account(tmpdb, "Halkbank")  # IBAN'sız tek Halkbank hesabı: alıcı bankasından öneri
    d = client.post("/api/dekont/parse", files={"file": ("d.jpg", _jpeg(), "image/jpeg")}).json()
    assert d["direction"] == "gelen" and d["counterparty"] == "Mehmet Deneme" and d["account_hint"] == acc


def test_ambiguous_direction_left_to_user(client, tmpdb, monkeypatch):
    _gemini(monkeypatch, GELEN_AI)  # ne hesap ne ad tanınıyor
    d = client.post("/api/dekont/parse", files={"file": ("d.jpg", _jpeg(), "image/jpeg")}).json()
    assert d["direction"] is None and d["counterparty"] is None and d["account_hint"] is None
    assert d["parties"]["sender"]["name"] == "Mehmet Deneme" and d["parties"]["receiver"]["name"] == "Ayşe Örnek"
    assert d["parties"]["sender"]["iban_last4"] == "0077" and d["parties"]["receiver"]["iban_last4"] == "4567"


def test_counterparty_matches_known_person(client, tmpdb, monkeypatch):
    client.post("/api/entries", json={"entry_date": "2026-09-01", "description": "Mehmet Deneme", "amount": 50,
                                      "kind": "gelir", "counterparty": "Mehmet Deneme"})
    tmpdb.remember_owner_name("Ayşe Örnek")
    _gemini(monkeypatch, {**GELEN_AI, "sender_name": "MEHMET  DENEME"})
    d = client.post("/api/dekont/parse", files={"file": ("d.jpg", _jpeg(), "image/jpeg")}).json()
    assert d["counterparty"] == "Mehmet Deneme" and d["counterparty_known"] is True


def test_iban_parts_masking():
    from app import dekont_parse as dp

    assert dp.iban_parts("TR12 0001 2009 9990 0001 2345 67") == (SENDER_IBAN, "4567")
    assert dp.iban_parts("TR12 **** **** **** **** 2345 67") == ("", "4567")
    assert dp.iban_parts("—") == ("", "")
    assert dp.iban_bank(SENDER_IBAN) == "Halkbank"


# ---------------------------------------------------------------- okuma hakkı

def test_unreadable_not_charged_and_readable_charged_once(hosted, monkeypatch):
    c, _ = _user(hosted)
    _gemini(monkeypatch, {"is_transfer": True, "amount": None, "date": None})
    before = _reads(c)["reads"]["used"]
    d = c.post("/api/dekont/parse", files={"file": ("d.jpg", _jpeg(), "image/jpeg")}).json()
    assert d["unreadable"] is True and "okunamadı" in d["message"] and "charged" not in d
    assert _reads(c)["reads"]["used"] == before
    _gemini(monkeypatch, GELEN_AI)
    d = c.post("/api/dekont/parse", files={"file": ("d2.jpg", _jpeg() + b"2", "image/jpeg")}).json()
    assert not d.get("unreadable") and d["charged"]["daily"] == 1
    assert _reads(c)["reads"]["used"] == before + 1


def test_quota_full_keeps_file(hosted, monkeypatch):
    from tests.test_doc_reads import _set_reads

    c, u = _user(hosted)
    _set_reads(u["id"], day=999)
    _gemini(monkeypatch, GELEN_AI)
    r = c.post("/api/dekont/parse", files={"file": ("d.jpg", _jpeg(), "image/jpeg")})
    assert r.status_code == 502 and r.json()["quota_full"] and r.json()["dekont_file"].endswith(".jpg")


# ---------------------------------------------------------------- kayıt, çift kayıt, birleşme

def _save(client, **kw):
    body = {"date": "2026-10-24", "amount": 4850, "direction": "giden", "counterparty": "ALİ ÖRNEK",
            "description": "Kira", "ref": "1234567890", "transfer_type": "fast", "bank": "Halkbank", **kw}
    return client.post("/api/dekont/save", json=body)


def test_save_keeps_message_and_duplicate_is_refused(client, tmpdb, monkeypatch):
    _pdf_text(monkeypatch)
    _no_ai(monkeypatch)
    acc = _account(tmpdb, iban=SENDER_IBAN)
    d = _pdf(client).json()
    r = _save(client, account_id=acc, dekont_file=d["dekont_file"], note="Ekim")
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    e = next(x for x in tmpdb.list_entries() if x["id"] == eid)
    assert (e["description"], e["counterparty"], e["kind"], e["account_id"]) == ("Ali Örnek · Kira", "Ali Örnek", "gider", acc)
    assert e["import_hash"] == "dekont:2026-10-24:4850.00:1234567890" and e["receipt_file"] == d["dekont_file"]
    assert e["note"].endswith("\nEkim")
    # Transferler'de kişi olarak görünür
    tr = client.get("/api/transfers").json()
    assert any(p["name"] == "Ali Örnek" and p["sent"] == 4850 for p in tr["people"])
    # aynı dekont tekrar okunursa: zaten eklenmiş
    again = _pdf(client).json()
    assert again["duplicate"]["entry_id"] == eid and again["duplicate"]["message"] == "Bu dekont zaten eklenmiş"
    r2 = _save(client, account_id=acc)
    assert r2.status_code == 409 and r2.json()["entry_id"] == eid and r2.json()["detail"] == "Bu dekont zaten eklenmiş"
    # referanssız: tarih + tutar + kişi aynıysa da çift kayıt
    assert _save(client, ref="").status_code == 409
    # başka referans numaralı ayrı bir dekont aynı gün aynı tutarda olabilir
    assert _save(client, ref="555").status_code == 200
    # dosya: kaydın fotoğrafı/PDF'i
    f = client.get(f"/api/entries/{eid}/receipt")
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf"


def test_saved_dekont_merges_with_statement_keeping_note(client, tmpdb):
    acc = _account(tmpdb)
    tmpdb.set_account_balance("Halkbank", 10000, "2026-10-01", "dokum")
    eid = _save(client, account_id=acc, note="Ekim kirası").json()["id"]
    rows = [{"date": "2026-10-24", "description": "FAST ALI ORNEK KIRA", "amount": -4850.0, "balance": 5150.0,
             "type": "gider", "hash": "d1"}]
    st = tmpdb.import_bank_rows(rows, account="Halkbank", owners=[])
    assert st["tekrar"] == 1
    es = [e for e in tmpdb.list_entries() if e["entry_date"] == "2026-10-24"]
    assert len(es) == 1 and es[0]["id"] == eid
    assert es[0]["import_hash"] == "d1" and es[0]["description"] == "Ali Örnek · Kira"
    assert es[0]["note"].endswith("\nEkim kirası")


def test_accountless_dekont_merges_and_gets_account(client, tmpdb):
    eid = _save(client).json()["id"]  # hesap seçilmeden kaydedildi
    tmpdb.ensure_account("Halkbank")
    rows = [{"date": "2026-10-24", "description": "FAST ALI ORNEK", "amount": -4850.0, "balance": 100.0,
             "type": "gider", "hash": "d2"}]
    assert tmpdb.import_bank_rows(rows, account="Halkbank", owners=[])["tekrar"] == 1
    e = next(x for x in tmpdb.list_entries() if x["id"] == eid)
    assert e["account_id"] is not None and e["import_hash"] == "d2"
    assert sum(1 for x in tmpdb.list_entries() if x["entry_date"] == "2026-10-24") == 1


def test_statement_first_then_dekont_is_duplicate(client, tmpdb, monkeypatch):
    acc = _account(tmpdb, iban=SENDER_IBAN)
    rows = [{"date": "2026-10-24", "description": "FAST ALI ORNEK", "amount": -4850.0, "balance": 100.0,
             "type": "gider", "hash": "d3"}]
    tmpdb.import_bank_rows(rows, account="Halkbank", owners=[])
    _pdf_text(monkeypatch)
    _no_ai(monkeypatch)
    assert _pdf(client).json()["duplicate"]["entry_id"]


# ---------------------------------------------------------------- borç / alacak

def test_incoming_dekont_suggests_and_settles_receivable(client, tmpdb, monkeypatch):
    did = client.post("/api/debts", json={"person": "Mehmet Deneme", "direction": "alacak", "amount": 1250,
                                          "debt_date": "2026-10-01"}).json()["id"]
    tmpdb.remember_owner_name("Ayşe Örnek")
    _gemini(monkeypatch, GELEN_AI)
    d = client.post("/api/dekont/parse", files={"file": ("d.jpg", _jpeg(), "image/jpeg")}).json()
    assert d["direction"] == "gelen" and d["debt_match"]["id"] == did and d["debt_match"]["full"] is True
    r = client.post("/api/dekont/save", json={"date": d["date"], "amount": d["amount"], "direction": "gelen",
                                              "counterparty": d["counterparty"], "description": d["description"],
                                              "ref": d["ref"], "debt_id": did, "dekont_file": d["dekont_file"]})
    assert r.status_code == 200, r.text
    assert r.json()["debt_settled"] == did
    debt = next(x for x in tmpdb.list_debts() if x["id"] == did)
    assert debt["settled_at"] == "2026-11-04" and debt["remaining"] == 0
    e = next(x for x in tmpdb.list_entries() if x["id"] == r.json()["id"])
    assert e["debt_id"] == did and e["category"] == "Borç / Alacak"  # borç hareketi: gelir sayılmaz
    assert e["description"] == "Mehmet Deneme · Kasım kira payı"


def test_debt_no_skips_auto_link_and_wrong_debt_refused(client, tmpdb):
    did = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "borc", "amount": 4850,
                                          "debt_date": "2026-10-01"}).json()["id"]
    alacak = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "alacak", "amount": 9000,
                                             "debt_date": "2026-10-01"}).json()["id"]
    assert _save(client, debt_id=alacak).status_code == 422  # giden havale alacağı ödeyemez
    assert not any(e["entry_date"] == "2026-10-24" for e in tmpdb.list_entries())  # yarım kayıt kalmadı
    r = _save(client, debt_link=False)
    assert r.status_code == 200 and r.json()["debt_settled"] is None
    assert next(x for x in tmpdb.list_debts() if x["id"] == did)["settled_at"] is None


def test_outgoing_debt_match_suggested(client, tmpdb, monkeypatch):
    did = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "borc", "amount": 6000,
                                          "debt_date": "2026-10-01"}).json()["id"]
    _pdf_text(monkeypatch)
    _no_ai(monkeypatch)
    _account(tmpdb, iban=SENDER_IBAN)
    d = _pdf(client).json()
    assert d["debt_match"]["id"] == did and d["debt_match"]["full"] is False  # 4.850 / kalan 6.000: kısmi


# ---------------------------------------------------------------- yanlış seçim koruması ve sınıflandırma

def test_bill_parse_pdf_dekont_redirects_without_ai(client, monkeypatch):
    _pdf_text(monkeypatch)
    _no_ai(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    d = _pdf(client, "/api/bills/parse", data={"transfer_check": "1"}).json()
    assert d["looks_like_transfer"] is True and d["amount"] is None and "charged" not in d


def test_bill_parse_ai_doc_kind_transfer_flag(client, monkeypatch):
    _gemini(monkeypatch, {"provider": None, "type": "diger", "amount": 1250, "doc_kind": "transfer"})
    d = client.post("/api/bills/parse", files={"file": ("f.jpg", _jpeg(), "image/jpeg")}).json()
    assert d["looks_like_transfer"] is True
    _gemini(monkeypatch, {"provider": "Örnek Elektrik", "type": "elektrik", "amount": 410.5, "subscriber_no": "123",
                          "doc_kind": "fatura", "due_date": "2026-11-20"})
    d = client.post("/api/bills/parse", files={"file": ("f2.jpg", _jpeg() + b"x", "image/jpeg")}).json()
    assert d["looks_like_transfer"] is False and d["provider"] == "Örnek Elektrik"


def test_bill_payment_dekont_is_not_transfer():
    from app import dekont_parse as dp

    assert not dp.is_transfer("ORNEK BANKA ODEME DEKONTU / Alici: Ornek Elektrik / Tutar: 412,50 TL / Tarih: 02.11.2026")
    assert not dp.is_transfer("DEKONT\nFATURA ÖDEME\nKURUM : Örnek Su\nABONE NO : 123\nTUTAR : 100,00\nIBAN : -")
    assert dp.is_transfer(DEKONT)


def test_classify(client, monkeypatch):
    _pdf_text(monkeypatch)
    assert _pdf(client, "/api/documents/classify").json() == {"suggest": "dekont"}
    assert client.post("/api/documents/classify", files={"file": ("a.jpg", _jpeg(), "image/jpeg")}).json() == {"suggest": None}
    _pdf_text(monkeypatch, "Fatura metni")
    assert _pdf(client, "/api/documents/classify").json() == {"suggest": None}


# Paylaş PDF önerisi "fis" (yetenek share_pdf_receipt). Metinler uydurmadır.
SALE_EARSIV = """ORNEK EV GERECLERI TICARET A.S.
e-Arşiv Fatura
Fatura No: XYZ2026000000123
Fatura Tarihi: 08.11.2026
ETTN: 11111111-2222-3333-4444-555555555555
SANDALYE 2 x 750,00 1.500,00
MASA LAMBASI 1 x 349,90 349,90
KDV %20 308,32
Ödenecek Tutar 1.849,90 TL
Kredi kartı ile ödenmiştir"""
LOKANTA_EARSIV = """ORNEK LOKANTA GIDA LTD. STI.
e-Arşiv Fatura
Fatura No: LOK2026000000042
Fatura Tarihi: 09.11.2026
Yerinde tüketim
MERCIMEK CORBASI 1 x 90,00
IZGARA KOFTE 1 x 330,00
Genel Toplam 420,00 TL"""
UTIL_DUE = """ORNEK DOGALGAZ DAGITIM A.S.
e-Fatura
Fatura No: DGZ2026000009876
Fatura Tarihi: 01.11.2026
Son Ödeme Tarihi: 20.11.2026
Tesisat No: 1234567
Ödenecek Tutar 612,40 TL"""
UTIL_ABONE = """ORNEK ELEKTRIK PERAKENDE SATIS A.S.
e-Fatura
Fatura No: ELK2026000005555
Abone No: 7654321
Tüketim 210 kWh
Ödenecek Tutar 450,00 TL"""
CARD_EKSTRE = """ORNEK BANK KREDI KARTI HESAP EKSTRESI
Hesap Kesim Tarihi: 05.11.2026
Son Ödeme Tarihi: 15.11.2026
Dönem Borcu: 3.250,00 TL
Asgari Ödeme Tutarı: 1.300,00 TL
01.11.2026 ORNEK MARKET 250,00"""
ACCT_EKSTRE = """ORNEK BANK
Vadesiz Hesap Hareketleri
Hesap No: 0000000
01.11.2026 10:15 Maaş 25.000,00
03.11.2026 12:30 Kira -10.000,00"""


@pytest.mark.parametrize("text,want", [(SALE_EARSIV, "fis"), (LOKANTA_EARSIV, "fis"), (UTIL_DUE, None),
                                       (UTIL_ABONE, None), (DEKONT, "dekont"), (CARD_EKSTRE, "ekstre"),
                                       (ACCT_EKSTRE, "ekstre"), ("Fatura metni", None), ("", None)])
def test_classify_text_receipt(tmpdb, text, want):
    """Satış e-Arşivi (son ödemesiz, kurum işaretsiz, "ödenmiştir" yazsa da) fiş; son ödemeli / aboneli kurum faturası
    öneri yok (ne fiş ne ekstre: Fatura seçeneği); dekont ve ekstre eskisi gibi."""
    from app import dekont_parse

    assert dekont_parse.classify_text(text) == want


def test_classify_endpoint_receipt(client, monkeypatch):
    _pdf_text(monkeypatch, SALE_EARSIV)
    assert _pdf(client, "/api/documents/classify", name="belge.pdf").json() == {"suggest": "fis"}
    _pdf_text(monkeypatch, UTIL_DUE)
    assert _pdf(client, "/api/documents/classify", name="fatura.pdf").json() == {"suggest": None}


def test_capability_listed():
    from app import version

    assert "dekont_parse" in version.CAPABILITIES
    assert "share_pdf_receipt" in version.CAPABILITIES


@pytest.mark.parametrize("path", ["/api/dekont/parse", "/api/dekont/save", "/api/documents/classify"])
def test_member_may_use_dekont(path):
    import re

    from app import book

    assert any(m == "POST" and re.fullmatch(p, path) for m, p in book.MEMBER_WRITES)
