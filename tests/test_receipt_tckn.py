"""Şahıs firmasının fişi (15.11.2026, kullanıcı bildirimi): vergi numarası yerine 11 haneli T.C. kimlik no (TCKN) yazar.
Önceden TCKN saklanmıyordu (tax_no boş kalıyor, elle de girilemiyordu: vergi kartı kaydı KDV ile birlikte reddediliyordu)
ve KDV satırı küçük yazar kasanın yazımında ("KDV *4,55", "KDV(%10)", "%10 KDV") metin kuralına takılmıyordu.

Burada: TCKN resmî algoritması, 10 haneli VKN eskisi gibi, okumada / metin yedeğinde / karekodda / kayıtta TCKN, KDV
metin yedeği (fişte yoksa boş; uydurma hesap yok), şablon imzasının TCKN tutmaması, günlükte TCKN olmaması, muhasebeci
listesinde tam değer. Bütün numaralar, işyerleri ve tutarlar UYDURMADIR (TCKN'ler yalnız kontrol basamağını tutan
örnek diziler)."""
import csv
import io
import logging

from app import karekod, logs, receipt, receipt_templates as rt, vat
from tests.test_receipt_templates import _jpeg, gemini  # noqa: F401 - fixture

VKN = "8218105998"        # uydurma, kontrol hanesi tutar
TCKN = "12345678950"      # uydurma, kontrol basamakları tutar
TCKN2 = "10000000078"     # uydurma, kontrol basamakları tutar
BAD_10 = "12345678960"    # 10. hane yanlış
BAD_11 = "12345678951"    # 11. hane yanlış
ZERO = "02345678950"      # ilk hane 0
CATS = ["Market & Gıda", "Diğer"]

SAHIS_FIS = f"""ORNEK SARKUTERI
AHMET ORNEK
ORNEK MAH. DENEME SK. NO:3 KADIKOY/ISTANBUL
MODA V.D. TCKN: {TCKN}
TARIH 01.11.2026
PEYNIR                %10   *80,00
ZEYTIN                %10   *30,00
KDV                         *10,00
TOPLAM                     *110,00
NAKIT"""


def _raw(**kw):
    return {"doc_type": "fis", "merchant": "Ornek Sarkuteri", "date": "2026-11-01", "total": 110.0,
            "category": "Market & Gıda", "confidence": "yüksek", **kw}


# ---------------------------------------------------------------- algoritma

def test_tckn_check_digits():
    assert vat.tckn_valid(TCKN) and vat.tckn_valid(TCKN2)
    for bad in (BAD_10, BAD_11, ZERO, "1234567895", "123456789501", "1234567895a", "", None):
        assert not vat.tckn_valid(bad)


def test_tax_id_accepts_vkn_and_tckn_vkn_unchanged():
    assert vat.tax_id(VKN) == VKN and vat.tax_id("821 810 5998") == VKN  # VKN eskisi gibi
    assert vat.tax_id(TCKN) == TCKN and vat.tax_id("123 456 789 50") == TCKN and vat.tax_id("123.456.789-50") == TCKN
    for bad in (BAD_10, BAD_11, ZERO, "8218105997", "12345", None, True):
        assert vat.tax_id(bad) is None
    assert vat.vkn(TCKN) is None and vat.vkn(VKN) == VKN  # vkn(): yalnız VKN (şablon imzası, karekod "vkn")
    assert vat.is_tckn(TCKN) and not vat.is_tckn(VKN)


def test_mask_shows_first3_last2_of_tckn_only():
    assert vat.mask(TCKN) == "123******50" and TCKN not in vat.mask(TCKN)
    assert vat.mask(VKN) == VKN and vat.mask(None) is None


# ---------------------------------------------------------------- okuma

def test_ai_tckn_kept_with_vat_block():
    doc = receipt._doc_of(_raw(tax_no=TCKN, tax_office="Moda", vat_total="10,00",
                               vat_lines=[{"rate": 10, "amount": 10}]), CATS)
    assert doc["tax_no"] == TCKN and doc["tax_office"] == "Moda"
    assert doc["vat_total"] == 10.0 and doc["vat_lines"] == [{"rate": 10, "amount": 10.0}]  # vergi bloğu atılmadı
    assert receipt._doc_of(_raw(tax_no=BAD_11, vat_total=10), CATS)["tax_no"] is None
    assert receipt._doc_of(_raw(tax_no=BAD_11, vat_total=10), CATS)["vat_total"] == 10  # bozuk numara KDV'yi düşürmez


def test_prompt_asks_tckn_not_null():
    for p in (receipt.DOC_PROMPT, receipt.DOC_PROMPT_COMPACT):
        assert "TCKN ise null" not in p and "T.C. kimlik no" in p
        p.format(categories="x", split_rule="")  # biçim dizgesi bozulmadı


def test_text_rule_reads_tckn_and_vat():
    t = vat.from_text(SAHIS_FIS)
    assert t == {"vat_total": 10.0, "vat_lines": [], "tax_no": TCKN, "tax_office": "Moda"}
    for line in (f"VKN/TCKN: {TCKN}", f"T.C. KİMLİK NO: {TCKN}", f"TC KIMLIK NO {TCKN}", f"T.C. NO: {TCKN}",
                 f"V.NO: {TCKN}", f"VERGİ NO: {TCKN}"):
        assert vat.from_text(f"ORNEK BUFE\n{line}\nTOPLAM 20,00")["tax_no"] == TCKN, line
    assert vat.from_text(f"ORNEK BUFE\nTCKN: {BAD_11}")["tax_no"] is None
    assert vat.from_text(f"ORNEK BUFE\nV.D. VKN: {VKN}")["tax_no"] == VKN  # VKN eskisi gibi
    # e-Arşiv alıcı bloğu: "SAYIN …" altındaki TCKN alıcınındır, satıcının değil
    assert vat.from_text(f"ORNEK BUFE\nSAYIN ORNEK KISI\nTCKN: {TCKN}")["tax_no"] is None
    assert vat.from_text(f"ORNEK BUFE\nVKN: {VKN}\nSAYIN ORNEK KISI\nTCKN: {TCKN}")["tax_no"] == VKN


def test_vat_text_fallback_formats():
    cases = {
        "KDV *4,55\nTOPLAM *50,00": (4.55, []),
        "KDV TUTARI: 4,55\nTOPLAM 50,00": (4.55, []),
        "HESAPLANAN KDV 4,55\nTOPLAM 50,00": (4.55, []),
        "KDV(%10) *4,55\nTOPLAM 50,00": (None, [{"rate": 10, "amount": 4.55}]),
        "%10 KDV *4,55\nTOPLAM 50,00": (None, [{"rate": 10, "amount": 4.55}]),
        "KDV %10,00 *4,55 TL\nTOPLAM 50,00": (None, [{"rate": 10, "amount": 4.55}]),
        "TOPKDV *4,55\nTOPLAM *50,00": (4.55, []),  # eski yazım
    }
    for text, (total, lines) in cases.items():
        t = vat.from_text(text)
        assert (t["vat_total"], t["vat_lines"]) == (total, lines), text
    # KDV DAHİL toplam KDV değildir; KDV satırı hiç yoksa boş (hesaplanıp uydurulmaz)
    assert vat.from_text("KDV DAHİL TOPLAM 50,00\nNAKIT 50,00") == {"vat_total": None, "vat_lines": [], "tax_no": None,
                                                                    "tax_office": None}
    doc = vat.fill({"type": "receipt", "total": 50.0}, "ORNEK BUFE\nTOST *50,00\nTOPLAM *50,00")
    assert doc["vat_total"] is None and doc["vat_lines"] == []
    doc = vat.fill({"type": "receipt", "total": 110.0}, "KDV(%10) *10,00\nTOPLAM *110,00")
    assert doc["vat_total"] == 10.0  # oran satırından toplam


def test_photo_read_fills_tckn_and_vat_from_phone_text(tmpdb, gemini):
    """Model KDV'yi ve TCKN'yi vermedi (eski istemle olduğu gibi): telefon metninden kural yedeği doldurur."""
    gemini["replies"] = [_raw(items=[{"name": "PEYNIR", "amount": 80}, {"name": "ZEYTIN", "amount": 30}])]
    doc = receipt.parse_document(_jpeg(), "image/jpeg", CATS, ocr_text=SAHIS_FIS)
    assert gemini["calls"] == 1 and doc["type"] == "receipt"
    assert doc["tax_no"] == TCKN and doc["vat_total"] == 10.0 and doc["tax_office"] == "Moda"


def test_photo_read_keeps_model_tckn_and_vat(tmpdb, gemini):
    gemini["replies"] = [_raw(tax_no=TCKN, vat_total=10, vat_lines=[{"rate": 10, "amount": 10}],
                              items=[{"name": "PEYNIR", "amount": 80}, {"name": "ZEYTIN", "amount": 30}])]
    doc = receipt.parse_document(_jpeg(), "image/jpeg", CATS)
    assert doc["tax_no"] == TCKN and doc["vat_total"] == 10 and doc["vat_lines"] == [{"rate": 10, "amount": 10.0}]


def test_template_signature_never_holds_tckn():
    assert rt.vkn_of(SAHIS_FIS) is None  # imza VKN'siz: TCKN'nin özeti tutulmaz
    assert rt.vkn_of(f"ORNEK\nVKN/TCKN: {VKN}") == VKN


# ---------------------------------------------------------------- karekod

def test_karekod_seller_tckn_is_tax_no_not_vkn():
    q = karekod.parse('{"vkntckn":"%s","avkntckn":"%s","tarih":"2026-11-01","odenecek":"110.00",'
                      '"hesaplanankdv (10)":"10.00","parabirimi":"TRY"}' % (TCKN, TCKN2))
    assert q["vkn"] is None and q["tax_no"] == TCKN and q["tckn"] is True
    assert TCKN2 not in str(q)  # alıcının numarası alınmaz
    doc = karekod.apply_hints(receipt._doc_of(_raw(), CATS), q, None, CATS)
    assert doc["tax_no"] == TCKN and doc["vat_total"] == 10.0 and "tax_no" in doc["qr_fixed"]
    bad = karekod.parse('{"vkntckn":"%s","tarih":"2026-11-01","odenecek":"110.00"}' % BAD_11)
    assert bad["tax_no"] is None and bad["vkn"] is None


def test_karekod_vat_total_without_rate_lines():
    q = karekod.parse('{"vkntckn":"%s","tarih":"2026-11-01","odenecek":"110.00","hesaplanankdv":"10.00",'
                      '"parabirimi":"TRY"}' % VKN)
    doc = karekod.apply_hints(receipt._doc_of(_raw(), CATS), q, None, CATS)
    assert doc["vat_total"] == 10.0 and doc["vat_lines"] == [] and doc["tax_no"] == VKN


# ---------------------------------------------------------------- kayıt, günlük, dışa aktarım

def _entry(client, **kw):
    body = {"entry_date": "2026-11-01", "description": "Ornek Sarkuteri", "amount": 110.0, "kind": "gider",
            "category": "Market & Gıda", "payment": "nakit", **kw}
    return client.post("/api/entries", json=body)


def _row(client, eid):
    return next(r for r in client.get("/api/transactions").json() if r.get("source") == "manual" and r["id"] == eid)


def test_save_tckn_with_vat(client, tmpdb):
    r = _entry(client, tax_no="123 456 789 50", tax_office="Moda", vat_total=10, vat_lines=[{"rate": 10, "amount": 10}])
    assert r.status_code == 200, r.text
    row = _row(client, r.json()["id"])
    assert row["tax_no"] == TCKN and row["vat_total"] == 10 and row["vat_lines"] == [{"rate": 10, "amount": 10.0}]
    eid = r.json()["id"]
    assert client.patch(f"/api/entries/{eid}", json={"tax_no": VKN}).status_code == 200  # VKN eskisi gibi
    assert _row(client, eid)["tax_no"] == VKN
    assert client.patch(f"/api/entries/{eid}", json={"tax_no": TCKN2, "vat_total": 9}).status_code == 200
    assert _row(client, eid)["tax_no"] == TCKN2 and _row(client, eid)["vat_total"] == 9


def test_save_rejects_bad_tckn_without_echoing_it(client, tmpdb, caplog):
    caplog.set_level(logging.DEBUG)
    for bad in (BAD_10, BAD_11, ZERO):
        r = _entry(client, tax_no=bad, vat_total=10)
        assert r.status_code == 422 and bad not in r.text
        assert "11 haneli T.C. kimlik" in r.json()["detail"]
    assert _entry(client, tax_no=TCKN, vat_total=10).status_code == 200
    for n in (TCKN, BAD_10, BAD_11, ZERO):
        assert n not in caplog.text  # günlükte / olay kaydında numara yok


def test_log_mask_hides_tckn():
    assert TCKN not in logs.mask(f"fiş okundu tax_no={TCKN}")
    assert TCKN not in logs.mask(f"Vergi no: 123 456 789 50 / {TCKN}")


def test_receipts_export_has_full_tckn(client, tmpdb):
    assert _entry(client, tax_no=TCKN, tax_office="Moda", vat_total=10).status_code == 200
    r = client.get("/api/receipts/export?from=2026-11-01&to=2026-11-30&format=csv")
    lines = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    assert lines[1][3] == TCKN  # kullanıcının kendi verisi: muhasebeciye tam değer


def test_tckn_capability_announced(client):
    assert "receipt_tax_tckn" in client.get("/api/meta").json()["capabilities"]
