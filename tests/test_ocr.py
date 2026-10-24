from app import ocr

MARKET = """ORNEK MARKET
ORNEK MAH. DEMO SK. NO:1
ISTANBUL
TARİH: 25.09.2026 SAAT: 18:42
FİŞ NO: 0042
SUT 1L *54,90
EKMEK *17,50
BEYAZ PEYNIR *1.215,00
TOPKDV *68,79
TOPLAM *1.287,40
KREDI KARTI *1.287,40
3 TAKSİT
"""

FATURA = """ÖRNEK ELEKTRİK DAĞITIM A.Ş.
ELEKTRİK FATURASI
Fatura Tarihi: 01.10.2026
Son Ödeme Tarihi: 28.10.2026
Ödenecek Tutar: 845,30 TL
"""


def test_receipt_from_text(monkeypatch):
    monkeypatch.setattr(ocr, "text_of", lambda data, mime: MARKET)
    d = ocr.parse(b"x", "image/jpeg", ["Market & Gıda", "Diğer"])
    assert d["type"] == "receipt" and d["merchant"] == "ORNEK MARKET" and d["date"] == "2026-09-25"
    assert d["total"] == 1287.40 and d["payment"] == "kart" and d["installments"] == 3
    assert [i["name"] for i in d["items"]] == ["SUT 1L", "EKMEK", "BEYAZ PEYNIR"]


def test_bill_from_text(monkeypatch):
    monkeypatch.setattr(ocr, "text_of", lambda data, mime: FATURA)
    d = ocr.parse(b"x", "application/pdf", ["Diğer"])
    assert d["type"] == "bill" and d["amount"] == 845.30 and d["due_date"] == "2026-10-28"


def test_fallback_when_no_key(client, monkeypatch):
    from app import receipt

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(ocr, "text_of", lambda data, mime: MARKET)
    monkeypatch.setattr(ocr, "available", lambda: True)
    assert not receipt.enabled()
    r = client.post("/api/documents/parse", files={"file": ("f.jpg", b"\xff\xd8x", "image/jpeg")})
    assert r.status_code == 200 and r.json()["total"] == 1287.40 and r.json()["engine"] == "yerel"
    assert client.get("/api/meta").json()["receipt_engine"] == "yerel"
