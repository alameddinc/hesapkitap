"""Fişin vergi bilgisi (app/vat.py): okumada KDV toplamı, oran oran KDV, VKN (10 hane, kontrol hanesi; 11 haneli TCKN
saklanmaz), vergi dairesi; model boş bırakırsa fiş metninden kural ile; kayıtta saklanır, PATCH ile düzenlenir;
Excel dışa aktarımında ve muhasebeci fiş listesinde (GET /api/receipts/export) sütun olarak. Veriler uydurmadır:
VKN'ler kontrol hanesi tutan uydurma numaralar."""
import csv
import io
import json

from openpyxl import load_workbook

from app import receipt, vat

VKN = "8218105998"    # uydurma, kontrol hanesi tutar
VKN2 = "4605721929"   # uydurma, kontrol hanesi tutar
BAD_VKN = "8218105997"  # son hane yanlış
TCKN = "12345678901"  # 11 hane: saklanmaz
CATS = ["Market & Gıda", "Diğer"]

FIS_TEXT = """ORNEK GIDA SAN. VE TIC. LTD. STI.
ORNEK MAH. DENEME CAD. NO:1 KADIKOY/ISTANBUL
KADIKOY V.D. V.NO: 8218105998
TARIH 01.11.2026
EKMEK            %01   *10,00
PEYNIR           %01   *1.815,00
DETERJAN         %20   *120,00
TOPKDV                  *38,00
%1                      *18,00
%20                     *20,00
TOPLAM                  *1.945,00
KREDI KARTI"""


def _raw(**kw):
    return {"doc_type": "fis", "merchant": "Ornek Market", "date": "2026-11-01", "total": 1945.0, "category": "Market & Gıda",
            "confidence": "yüksek", **kw}


# ---------------------------------------------------------------- ayrıştırma

def test_vkn_check_digit():
    assert vat.vkn_valid(VKN) and vat.vkn_valid(VKN2)
    assert not vat.vkn_valid(BAD_VKN)
    assert vat.vkn("821 810 5998") == VKN  # boşluklu yazım
    assert vat.vkn(BAD_VKN) is None
    assert vat.vkn(TCKN) is None  # TCKN kişisel veri: saklanmaz
    assert vat.vkn("82181059") is None and vat.vkn(None) is None and vat.vkn(True) is None


def test_doc_prompt_asks_tax_fields():
    for k in ("vat_total", "vat_lines", "tax_no", "tax_office"):
        assert f'"{k}"' in receipt.DOC_PROMPT
    receipt.DOC_PROMPT.format(categories="x", split_rule="")  # biçim dizgesi bozulmadı


def test_doc_of_reads_vat_and_tax_no():
    doc = receipt._doc_of(_raw(vat_total="38,00", vat_lines=[{"rate": 1, "amount": "18,00"}, {"rate": "%20", "amount": 20}],
                               tax_no=VKN, tax_office="Kadıköy V.D."), CATS)
    assert doc["vat_total"] == 38.0
    assert doc["vat_lines"] == [{"rate": 1, "amount": 18.0}, {"rate": 20, "amount": 20.0}]
    assert doc["tax_no"] == VKN and doc["tax_office"] == "Kadıköy"


def test_doc_of_rejects_bad_vkn_and_tckn_and_odd_vat():
    doc = receipt._doc_of(_raw(tax_no=TCKN, vat_total=2000, vat_lines=[{"rate": 7, "amount": 5}]), CATS)
    assert doc["tax_no"] is None  # 11 hane
    assert doc["vat_total"] is None  # KDV toplamdan büyük olamaz
    assert doc["vat_lines"] == []  # %7 oranı yok
    assert receipt._doc_of(_raw(tax_no=BAD_VKN), CATS)["tax_no"] is None
    # döküm var, toplam yok → toplam dökümden; döküm toplamı tutmuyorsa döküm atılır
    assert receipt._doc_of(_raw(vat_lines=[{"rate": 10, "amount": 5}, {"rate": 20, "amount": 7.5}]), CATS)["vat_total"] == 12.5
    d = receipt._doc_of(_raw(vat_total=38, vat_lines=[{"rate": 1, "amount": 3}]), CATS)
    assert d["vat_total"] == 38 and d["vat_lines"] == []


def test_doc_of_without_fields_keeps_keys():
    doc = receipt._doc_of(_raw(), CATS)
    assert doc["vat_total"] is None and doc["vat_lines"] == [] and doc["tax_no"] is None and doc["tax_office"] is None
    bill = receipt._doc_of({"doc_type": "fatura", "merchant": "Ornek Enerji", "total": 100, "due_date": "2026-11-20"}, CATS)
    assert "vat_total" not in bill  # fatura sonucu değişmedi


def test_rule_fallback_from_text():
    t = vat.from_text(FIS_TEXT)
    assert t["vat_total"] == 38.0
    assert t["vat_lines"] == [{"rate": 1, "amount": 18.0}, {"rate": 20, "amount": 20.0}]  # ürün satırları değil
    assert t["tax_no"] == VKN and t["tax_office"] == "Kadıkoy"
    other = vat.from_text("ORNEK KAFE\nVERGİ DAİRESİ: MODA\nVKN 4605721929\nTOPLAM KDV 3,33\nTOPLAM 20,00")
    assert other == {"vat_total": 3.33, "vat_lines": [], "tax_no": VKN2, "tax_office": "Moda"}
    assert vat.from_text(f"ORNEK BUFE\nV.NO: {TCKN}\nTOPLAM 20,00")["tax_no"] is None


def test_fill_only_empty_fields():
    doc = receipt._doc_of(_raw(vat_total=38.0), CATS)
    vat.fill(doc, FIS_TEXT)
    assert doc["vat_total"] == 38.0 and doc["tax_no"] == VKN and len(doc["vat_lines"]) == 2
    doc = receipt._doc_of(_raw(tax_no=VKN2), CATS)
    assert vat.fill(doc, FIS_TEXT)["tax_no"] == VKN2  # modelin okuduğu korunur


def test_text_path_fills_from_text(monkeypatch):
    monkeypatch.setattr(receipt, "_gemini_json", lambda *a, **k: (_raw(), {"model": "x", "input": 1, "output": 1}))
    doc = receipt.parse_document_text(FIS_TEXT, CATS)
    assert doc["vat_total"] == 38.0 and doc["tax_no"] == VKN and doc["tax_office"] == "Kadıkoy"


def test_local_ocr_text_has_tax(monkeypatch):
    from app import ocr

    monkeypatch.setattr(ocr, "text_of", lambda data, mime: FIS_TEXT)
    doc = ocr.parse(b"x", "image/jpeg", CATS)
    assert doc["vat_total"] == 38.0 and doc["tax_no"] == VKN and doc["vat_lines"][1] == {"rate": 20, "amount": 20.0}


# ---------------------------------------------------------------- saklama, düzenleme, dışa aktarım

def _entry(client, **kw):
    body = {"entry_date": "2026-11-01", "description": "Ornek Market", "amount": 1945.0, "kind": "gider",
            "category": "Market & Gıda", "payment": "kredi_karti", "card_last4": "1234", **kw}
    r = client.post("/api/entries", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _row(client, eid):
    return next(r for r in client.get("/api/transactions").json() if r.get("source") == "manual" and r["id"] == eid)


def test_columns_migrated(tmpdb):
    from app import db

    with db.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(entries)")}
    assert {"legal_name", "tax_no", "tax_office", "vat_total", "vat_lines"} <= cols


def test_save_and_patch_tax_fields(client, tmpdb):
    eid = _entry(client, legal_name="ORNEK GIDA LTD. STI.", tax_no=VKN, tax_office="Kadıköy",
                 vat_lines=[{"rate": 1, "amount": 18}, {"rate": 20, "amount": 20}])
    r = _row(client, eid)
    assert r["legal_name"] == "ORNEK GIDA LTD. STI." and r["tax_no"] == VKN and r["tax_office"] == "Kadıköy"
    assert r["vat_total"] == 38.0  # dökümden
    assert r["vat_lines"] == [{"rate": 1, "amount": 18.0}, {"rate": 20, "amount": 20.0}]
    from app import db

    with db.connect() as c:
        assert json.loads(c.execute("SELECT vat_lines FROM entries WHERE id = ?", (eid,)).fetchone()[0])[0]["rate"] == 1

    assert client.patch(f"/api/entries/{eid}", json={"tax_no": TCKN}).status_code == 422
    assert client.patch(f"/api/entries/{eid}", json={"tax_no": BAD_VKN}).status_code == 422
    assert client.patch(f"/api/entries/{eid}", json={"tax_no": VKN2, "vat_total": 40, "vat_lines": [{"rate": 20, "amount": 40}],
                                                     "tax_office": "Moda"}).status_code == 200
    r = _row(client, eid)
    assert r["tax_no"] == VKN2 and r["vat_total"] == 40 and r["vat_lines"] == [{"rate": 20, "amount": 40.0}]
    assert client.patch(f"/api/entries/{eid}", json={"tax_no": "", "vat_total": None, "vat_lines": [],
                                                     "legal_name": ""}).status_code == 200
    r = _row(client, eid)
    assert r["tax_no"] is None and r["vat_total"] is None and r["vat_lines"] == [] and r["legal_name"] is None
    assert r["tax_office"] == "Moda"  # gönderilmeyen alan kalır


def test_create_rejects_tckn(client, tmpdb):
    r = client.post("/api/entries", json={"entry_date": "2026-11-01", "description": "Ornek", "amount": 10, "kind": "gider",
                                          "tax_no": TCKN})
    assert r.status_code == 422 and "10 haneli" in r.json()["detail"]


def test_export_xlsx_has_tax_columns(client, tmpdb):
    _entry(client, legal_name="ORNEK GIDA LTD. STI.", tax_no=VKN, tax_office="Kadıköy", vat_total=38,
           vat_lines=[{"rate": 1, "amount": 18}, {"rate": 20, "amount": 20}])
    r = client.get("/api/settings/export.xlsx")
    ws = load_workbook(io.BytesIO(r.content))["İşlemler"]
    head = [c.value for c in ws[1]]
    for h in ("Unvan", "VKN", "Vergi dairesi", "KDV (₺)", "KDV %1", "KDV %10", "KDV %20"):
        assert h in head
    row = next(dict(zip(head, [c.value for c in x])) for x in ws.iter_rows(min_row=2) if x[2].value == "Ornek Market")
    assert row["VKN"] == VKN and row["KDV (₺)"] == 38 and row["KDV %1"] == 18 and row["KDV %20"] == 20 and row["KDV %10"] is None


def test_receipts_export_xlsx_and_csv(client, tmpdb):
    _entry(client, legal_name="ORNEK GIDA LTD. STI.", tax_no=VKN, tax_office="Kadıköy", vat_total=38,
           vat_lines=[{"rate": 1, "amount": 18}, {"rate": 20, "amount": 20}])
    _entry(client, entry_date="2026-11-03", description="Ornek Kafe", amount=120, payment="nakit", card_last4=None,
           vat_lines=[{"rate": 10, "amount": 10.91}])
    _entry(client, entry_date="2026-10-20", description="Eski Fiş", amount=50, tax_no=VKN2)  # dönem dışı
    _entry(client, entry_date="2026-11-02", description="Fişsiz gider", amount=70)  # fiş değil
    r = client.get("/api/receipts/export?from=2026-11-01&to=2026-11-30&format=xlsx")
    assert r.status_code == 200 and "fisler-20261101-20261130.xlsx" in r.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(r.content))["Fiş listesi"]
    head = [c.value for c in ws[1]]
    assert head == ["Tarih", "İşyeri", "Unvan", "VKN", "Vergi dairesi", "Toplam (₺)", "KDV %1", "KDV %10", "KDV %20",
                    "KDV diğer", "KDV toplam (₺)", "Ödeme", "Kategori"]
    rows = [[c.value for c in x] for x in ws.iter_rows(min_row=2)]
    assert [x[1] for x in rows] == ["Ornek Market", "Ornek Kafe", None]  # son satır toplam
    m = dict(zip(head, rows[0]))
    assert m["VKN"] == VKN and m["Unvan"] == "ORNEK GIDA LTD. STI." and m["KDV %1"] == 18 and m["KDV toplam (₺)"] == 38
    assert m["Ödeme"] == "Kredi kartı · •••• 1234" and m["Kategori"] == "Market & Gıda"
    assert dict(zip(head, rows[1]))["KDV %10"] == 10.91
    total = dict(zip(head, rows[2]))
    assert total["Tarih"] == "Toplam" and total["Toplam (₺)"] == 2065 and total["KDV toplam (₺)"] == 48.91

    r = client.get("/api/receipts/export?from=2026-11-01&to=2026-11-30&format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert lines[0][3] == "VKN" and lines[1][3] == VKN and lines[1][5] == "1945,00" and lines[1][10] == "38,00"
    assert len(lines) == 3
    assert client.get("/api/receipts/export?from=2026-11-30&to=2026-11-01").status_code == 422


def test_receipts_export_period_and_yearly_summary(client, tmpdb):
    _entry(client, entry_date="2026-03-05", description="Ornek Market", amount=110, vat_lines=[{"rate": 1, "amount": 1.09}])
    _entry(client, entry_date="2026-03-20", description="Ornek Kafe", amount=220, payment="nakit", card_last4=None,
           vat_lines=[{"rate": 10, "amount": 10}, {"rate": 20, "amount": 10}])
    _entry(client, entry_date="2026-11-02", description="Ornek Eczane", amount=50, vat_total=4.55, tax_no=VKN)
    _entry(client, entry_date="2025-12-31", description="Geçen yıl", amount=99, vat_total=9)

    r = client.get("/api/receipts/export?period=year&value=2026")
    assert r.status_code == 200 and "fisler-2026.xlsx" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Fiş listesi", "KDV özeti"]
    assert [x[1].value for x in wb["Fiş listesi"].iter_rows(min_row=2)] == ["Ornek Market", "Ornek Kafe", "Ornek Eczane", None]
    ws = wb["KDV özeti"]
    head = [c.value for c in ws[1]]
    assert head == ["Ay", "Fiş sayısı", "Toplam (₺)", "KDV %1", "KDV %10", "KDV %20", "KDV diğer", "KDV toplam (₺)"]
    rows = [dict(zip(head, [c.value for c in x])) for x in ws.iter_rows(min_row=2)]
    assert [x["Ay"] for x in rows] == [f"{m} 2026" for m in ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
                                                            "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")] + ["Yıl toplamı"]
    mart, kasim, yil = rows[2], rows[10], rows[12]
    assert mart["Fiş sayısı"] == 2 and mart["Toplam (₺)"] == 330 and mart["KDV %1"] == 1.09 and mart["KDV %10"] == 10
    assert mart["KDV %20"] == 10 and mart["KDV toplam (₺)"] == 21.09
    assert kasim["Fiş sayısı"] == 1 and kasim["KDV toplam (₺)"] == 4.55 and kasim["KDV %1"] == 0
    assert rows[0]["Fiş sayısı"] == 0 and rows[0]["KDV toplam (₺)"] == 0  # boş ay da satır
    assert yil["Fiş sayısı"] == 3 and yil["Toplam (₺)"] == 380 and yil["KDV toplam (₺)"] == 25.64 and yil["KDV %20"] == 10
    assert round(sum(x["KDV toplam (₺)"] for x in rows[:12]), 2) == yil["KDV toplam (₺)"]  # ay toplamları yılı tutar

    r = client.get("/api/receipts/export?period=month&value=2026-03")
    ws = load_workbook(io.BytesIO(r.content))["KDV özeti"]
    assert [[c.value for c in x][:2] for x in ws.iter_rows(min_row=2)] == [["Mart 2026", 2], ["Ay toplamı", 2]]
    r = client.get("/api/receipts/export?period=month&value=2026-03&format=csv")
    assert len(r.content.decode("utf-8-sig").strip().splitlines()) == 3  # CSV: başlık + 2 fiş, özet yok
    assert client.get("/api/receipts/export?period=month&value=2026-13").status_code == 422
    assert client.get("/api/receipts/export?period=year&value=26").status_code == 422


EVIL = '=HYPERLINK("http://ornek.invalid/x","tikla")'


def test_exports_neutralize_formula_cells(client, tmpdb):
    """Güvenlik denetimi (CSV/Excel formül enjeksiyonu): "=", "+", "-", "@" ile başlayan metin formül olarak yazılmaz;
    sayılar (negatif tutar dahil) olduğu gibi kalır."""
    _entry(client, description=EVIL, legal_name="+ORNEK UNVAN", note="@ornek not", vat_total=10)
    _entry(client, entry_date="2026-11-02", description="-Ornek Kafe", amount=50, payment="nakit", card_last4=None)

    ws = load_workbook(io.BytesIO(client.get("/api/settings/export.xlsx").content))["İşlemler"]
    head = [c.value for c in ws[1]]
    cells = {c.value: c for x in ws.iter_rows(min_row=2) for c in x if isinstance(c.value, str)}
    assert EVIL in cells and cells[EVIL].data_type == "s" and cells[EVIL].quotePrefix  # düz metin, formül değil
    assert cells["-Ornek Kafe"].data_type == "s"
    amounts = [x[head.index("Tutar (₺)")].value for x in ws.iter_rows(min_row=2)]
    assert sorted(amounts) == [-1945, -50] and all(isinstance(a, (int, float)) for a in amounts)

    ws = load_workbook(io.BytesIO(client.get("/api/receipts/export?from=2026-11-01&to=2026-11-30").content))["Fiş listesi"]
    vals = {c.value: c for x in ws.iter_rows(min_row=2) for c in x if isinstance(c.value, str)}
    assert vals[EVIL].data_type == "s" and vals["+ORNEK UNVAN"].data_type == "s"

    text = client.get("/api/receipts/export?from=2026-11-01&to=2026-11-30&format=csv").content.decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(text), delimiter=";"))
    row = next(r for r in lines[1:] if "HYPERLINK" in r[1])
    assert row[1] == "'" + EVIL and row[2] == "'+ORNEK UNVAN" and row[5] == "1945,00"
    assert not any(c.startswith(("=", "+", "@")) for r in lines for c in r)
