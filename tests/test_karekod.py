"""Karekod (app/karekod.py) ve kalemsiz POS slibi: GİB e-belge JSON (boşluklu/büyük harfli anahtar, bozuk JSON), pompa
ÖKC karekodunun iki yazımı (son alan litre), akaryakıtta litre × birim fiyat doğrulaması, karekodlu faturanın yapay
zekâsız okunması, POS slibi kuralı (pozitif/negatif), okuma hakkının ₺0 okumada da düşmesi, eski uygulama (alan yok),
karekod VKN'sinin fiş şablonu imzası olması ve fiş çağrısında düşünme ayarı (app/receipt.py receipt_thinking).
Bütün karekod içerikleri, işyerleri, VKN'ler, plakalar ve tutarlar UYDURMADIR."""
import json

import httpx
import pytest

from app import karekod, receipt, receipt_templates as rt, system
from tests.test_doc_reads import _used, hosted  # noqa: F401 - fixture
from tests.test_plans import _user
from tests.test_receipt_templates import (CATS, FIRST, SECOND, _day, _money, _photo, _rows, _vkn, gemini,  # noqa: F401
                                          market, market_ai)

VKN_BILL = _vkn("997700112")
VKN_PUMP = _vkn("996600223")
TCKN = "12345678901"


def gib(total: float = 642.4, day=None, spaced: bool = True, **extra) -> str:
    day = day or _day(3)
    kdv = round(total / 6, 2)
    d = {"vkntckn": VKN_BILL, "avkntckn": "11111111110", "senaryo": "EARSIVFATURA", "tip": "SATIS",
         "tarih": day.isoformat(), "no": "ABC2026000000042", "ettn": "00000000-0000-0000-0000-000000000000",
         "parabirimi": "TRY", "malhizmettoplam": f"{total - kdv:.2f}",
         ("kdvmatrah (20)" if spaced else "kdvmatrah(20)"): f"{total - kdv:.2f}",
         ("hesaplanankdv (20)" if spaced else "hesaplanankdv(20)"): f"{kdv:.2f}",
         "vergidahil": f"{total:.2f}", "odenecek": f"{total:.2f}", **extra}
    return json.dumps(d)


def bill_text(due=None, total: float = 642.4) -> str:
    due = due or _day(-12)
    return "\n".join(["ÖRNEK ENERJİ PERAKENDE SATIŞ A.Ş.", "e-Arşiv Fatura", "Fatura No: ABC2026000000042",
                      f"Fatura Tarihi: {_day(3).strftime('%d.%m.%Y')}", f"Son Ödeme Tarihi: {due.strftime('%d.%m.%Y')}",
                      "Tüketim: 210 kWh", f"Ödenecek Tutar: {_money(total)} TL"])


def pump_at(litre: str = "35,42", day=None) -> str:
    return f"{VKN_PUMP}@{(day or _day(1)).strftime('%d-%m-%Y')}@0012@MOTORIN@{litre}"


def pump_sp(litre: str = "35,42", day=None) -> str:
    return f"{VKN_PUMP} {(day or _day(1)).isoformat()} 1420 0012 KURSUNSUZ BENZIN 95 {litre}"


def fuel_text(litre: float = 35.42, price: float = 52.49, total: float | None = None, qty_line: bool = True,
              total_line: bool = True) -> str:
    amount = round(litre * price, 2) if total is None else total
    return "\n".join([
        "ÖRNEK PETROL TİC. LTD. ŞTİ.", "DENEME MAH. YOL CAD. NO:5", "EPDK: BAY/000-00/00000",
        f"{_day(1).strftime('%d-%m-%Y')}   14:20", "34 XYZ 999",
        *([f"{_money(litre)} LT X {_money(price)}"] if qty_line else [f"BİRİM FİYAT {_money(price)}"]),
        f"MOTORİN   %20   *{_money(amount)}", f"KDV   *{_money(round(amount / 6, 2))}",
        *([f"TOPLAM   *{_money(amount)}"] if total_line else []), f"KREDİ KARTI   *{_money(amount)}"])


def pos(total: float = 1250.0, extra: list[str] | None = None, day=None) -> str:
    return "\n".join(["ÖRNEK MOBİLYA LTD. ŞTİ.", "İŞYERİ NO: 000000123456", "TERMİNAL NO: AB123456",
                      f"{(day or _day(2)).strftime('%d.%m.%Y')}   18:42", "SATIŞ", "4543 60** **** 1234",
                      f"TUTAR: {_money(total)} TL", "ONAY KODU: 123456", "PROVİZYON İLE YAPILMIŞTIR",
                      *(extra or []), "MÜŞTERİ NÜSHASI"])


# ---------------------------------------------------------------- karekod içeriği

def test_gib_json_spaced_and_compact_keys():
    for spaced in (True, False):
        q = karekod.parse(gib(spaced=spaced))
        assert q["kind"] == "gib" and q["vkn"] == VKN_BILL and q["total"] == 642.4 and q["date"] == _day(3).isoformat()
        assert q["vat_lines"] == [{"rate": 20, "amount": round(642.4 / 6, 2)}] and q["no"] == "ABC2026000000042"
        assert "11111111110" not in json.dumps(q)  # alıcının kimliği alınmaz


def test_gib_json_tolerant_forms():
    # büyük harfli anahtar, Türk sayı biçimi, sayı tipinde değer
    q = karekod.parse('{"VKNTCKN":"%s","TARIH":"%s","ODENECEK":"1.234,56","HesaplananKDV (10)":112.23}'
                      % (VKN_BILL, _day(2).isoformat()))
    assert q["total"] == 1234.56 and q["vat_lines"] == [{"rate": 10, "amount": 112.23}]
    # tek tırnak + kesik JSON (karekod yazılımı bozuk): anahtar:değer çiftleri ayıklanır
    q = karekod.parse("{'vkntckn':'%s','tarih':'%s','odenecek':'99.90','hesaplanankdv (1)':'0.99'"
                      % (VKN_BILL, _day(2).isoformat()))
    assert q and q["total"] == 99.9 and q["vat_lines"][0]["rate"] == 1
    # satıcı TCKN (şahıs): saklanmaz
    q = karekod.parse(gib().replace(VKN_BILL, TCKN))
    assert q["vkn"] is None and q["tckn"] is True
    # tutar yok / tarih yok → tanınmaz
    assert karekod.parse('{"vkntckn":"%s","tarih":"2026-11-01"}' % VKN_BILL) is None
    assert karekod.parse('{"foo": 1}') is None


def test_pump_two_formats_last_field_is_litre():
    a = karekod.parse(pump_at("35,42"))
    b = karekod.parse(pump_sp("35,42"))
    for q in (a, b):
        assert q["kind"] == "pompa" and q["vkn"] == VKN_PUMP and q["liters"] == 35.42 and q["date"] == _day(1).isoformat()
    assert a["fuel"] == "MOTORIN" and b["fuel"] == "KURSUNSUZ BENZIN 95"
    assert karekod.parse(pump_at("12.5"))["liters"] == 12.5
    assert karekod.parse(f"{VKN_PUMP}@31-02-2026@0012@MOTORIN@35,42") is None  # geçersiz tarih
    assert karekod.parse("RASTGELE METİN 12,50") is None


def test_link_recognised_but_not_used_and_best_order():
    assert karekod.parse("https://ebelge.ornek.test/q?id=abc") == {"kind": "link", "host": "ebelge.ornek.test"}
    assert karekod.best(["https://x.test/a", pump_at(), gib()])["kind"] == "gib"
    assert karekod.best(["https://x.test/a", pump_sp()])["kind"] == "pompa"
    assert karekod.best(["anlamsız", ""]) is None and karekod.best(None) is None
    assert karekod.clean(["a", "a", " ", "b" * 9000] + [str(i) for i in range(20)])[:2] == ["a", "b" * 4096]
    assert len(karekod.clean([str(i) for i in range(20)])) == karekod.MAX_CODES


# ---------------------------------------------------------------- akaryakıt: litre × birim fiyat

def test_fuel_total_is_litre_times_unit_price_from_text():
    q = karekod.parse(pump_at("35,42"))
    doc = karekod.fuel_doc(q, fuel_text(), CATS)
    total = round(35.42 * 52.49, 2)
    assert doc["total"] == total and doc["date"] == _day(1).isoformat() and doc["tax_no"] == VKN_PUMP
    assert doc["items"] == [{"name": "Akaryakıt · 35,42 L", "product": "Motorin", "brand": None, "qty": 35.42,
                             "unit": "lt", "unit_price": 52.49, "amount": total, "split": None}]
    assert doc["source"] == "karekod" and doc["rule"] == "qr_fuel" and doc["category"] == "Ulaşım & Araç"
    # litre satırı yok, birim fiyat ayrı satırda: yine bulunur
    assert karekod.fuel_doc(q, fuel_text(qty_line=False), CATS)["total"] == total
    # pompa kuruş yuvarlaması (±0,05 içinde): fişte yazan tutar alınır
    assert karekod.fuel_doc(q, fuel_text(total=total + 0.03), CATS)["total"] == round(total + 0.03, 2)


def test_fuel_rejects_when_text_does_not_agree():
    q = karekod.parse(pump_sp("35,42"))
    total = round(35.42 * 52.49, 2)
    # OCR toplamı litre × fiyatı ±0,05 ile tutmuyor (rakam bozulmuş) → yapay zekâ
    assert karekod.fuel_doc(q, fuel_text(total=total + 9), CATS) is None
    # birim fiyat metinde yok
    no_price = "\n".join(ln for ln in fuel_text().splitlines() if " X " not in ln)
    assert karekod.fuel_doc(q, no_price, CATS) is None
    # karekodun litresi farklı (başka fiş): hiçbir tutar tutmaz
    assert karekod.fuel_doc(karekod.parse(pump_sp("20,00")), fuel_text(), CATS) is None
    # metin yok / eski tarih
    assert karekod.fuel_doc(q, None, CATS) is None
    assert karekod.fuel_doc(karekod.parse(pump_sp(day=_day(900))), fuel_text(), CATS) is None


# ---------------------------------------------------------------- POS slibi

def test_pos_slip_positive():
    d = karekod.pos_slip(pos(), CATS)
    assert d["total"] == 1250.0 and d["date"] == _day(2).isoformat() and d["items"] == []
    assert d["merchant"] == "Örnek Mobilya" and d["card_last4"] == "1234" and d["payment"] == "kart"
    assert d["rule"] == "pos_slip" and d["source"] == "pos"
    # İngilizce terminal metni, tutar iki kez (TUTAR + TOPLAM aynı değer)
    en = "\n".join(["ORNEK KAFE", "MERCHANT ID: 000123", "TID: 99887766", f"{_day(1).strftime('%d/%m/%Y')} 09:10",
                    "SALE", "**** **** **** 9876", "AMOUNT 85,50 TL", "TOPLAM 85,50", "AUTH CODE: 654321"])
    assert karekod.pos_slip(en, CATS)["total"] == 85.5


@pytest.mark.parametrize("text", [
    market(FIRST, _day(2)),                                             # market fişi: kalem, KDV, Z NO
    fuel_text(),                                                        # akaryakıt fişi: KDV
    pos(extra=["İADE"]),                                                # iade slibi
    pos(extra=["İPTAL"]),                                               # iptal
    pos(extra=["BAKİYE: 120,00"]),                                      # ikinci tutar
    pos().replace("ONAY KODU: 123456", "REF: 123456").replace("PROVİZYON İLE YAPILMIŞTIR", ""),  # onay kodu yok
    pos().replace("4543 60** **** 1234", "KART"),                       # maskeli kart no yok
    pos().replace("İŞYERİ NO: 000000123456", "").replace("TERMİNAL NO: AB123456", ""),  # işyeri/terminal yok
    pos().replace("SATIŞ", "BİLGİ"),                                    # satış değil
    pos(extra=["KDV %20 208,33"]),                                      # KDV: yazar kasa fişi
    pos(day=_day(900)),                                                 # 2 yıldan eski
    pos(extra=[f"{_day(5).strftime('%d.%m.%Y')}"]),                     # iki farklı tarih
    pos(extra=[f"ÜRÜN {i}" for i in range(30)]),                        # uzun belge
])
def test_pos_slip_negative(text):
    assert karekod.pos_slip(text, CATS) is None


# ---------------------------------------------------------------- uçtan uca (/api/documents/parse)

def _parse(c, text, qr=None, color="white"):
    data = {"ocr_source": "vision"}
    if text:
        data["ocr_text"] = text
    if qr:
        data["qr_text"] = qr
    return c.post("/api/documents/parse", files=[("file", ("f.jpg", _photo(color), "image/jpeg"))], data=data)


def _ai_calls() -> int:
    with system.connect() as cx:
        try:
            return cx.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0]
        except Exception:  # noqa: BLE001 - tablo henüz yok
            return 0


def test_api_fuel_qr_reads_without_ai_and_charges(hosted, gemini):  # noqa: F811
    c, u = _user(hosted)
    calls = _ai_calls()
    r = _parse(c, fuel_text(), [pump_at()])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "karekod" and d["rule"] == "qr_fuel" and d["items"][0]["name"] == "Akaryakıt · 35,42 L"
    assert d["ocr"]["used"] and d["check"]["ok"] and "reread" not in d
    assert gemini["calls"] == 0 and _ai_calls() == calls      # yapay zekâ yok, maliyet kaydı yok
    assert d.get("charged") and _used(u["id"]) == 1          # okuma hakkı yine düşer


def test_api_pos_slip_reads_without_ai_and_charges(hosted, gemini):  # noqa: F811
    c, u = _user(hosted)
    r = _parse(c, pos())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["rule"] == "pos_slip" and d["total"] == 1250.0 and d["items"] == [] and not d.get("unreadable")
    assert gemini["calls"] == 0 and d.get("charged") and _used(u["id"]) == 1


def test_api_qr_bill_without_ai(hosted, gemini):  # noqa: F811
    c, u = _user(hosted)
    r = _parse(c, bill_text(), [gib()])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["type"] == "bill" and d["source"] == "karekod" and d["rule"] == "qr_bill"
    assert d["amount"] == 642.4 and d["due_date"] == _day(-12).isoformat() and d["file_name"]
    assert d["provider"].startswith("ÖRNEK ENERJİ") and d["description"] == "Fatura no ABC2026000000042"
    assert gemini["calls"] == 0 and d.get("charged") and _used(u["id"]) == 1


def test_qr_bill_provider_from_bill_template_signature(tmpdb):
    from app import bill_templates, db

    with db.connect() as c:
        bill_templates._init(c)
        spec = {"v": 1, "sig": ["ornek", "enerji"], "provider": "Örnek Enerji", "category": "Elektrik",
                "fields": {"amount": [{"label": "odenecek tutar", "off": 0, "nth": 0, "occ": 0}]}}
        c.execute("INSERT INTO bill_templates (sig, provider, spec) VALUES (?, ?, ?)",
                  ("ornek enerji", "Örnek Enerji", json.dumps(spec)))
    d = karekod.bill_doc(karekod.parse(gib()), bill_text())
    assert d["provider"] == "Örnek Enerji" and d["category"] == "Elektrik"


def test_qr_bill_due_date_column_layout_and_missing(tmpdb):
    q = karekod.parse(gib())
    cols = bill_text().replace(f"Fatura Tarihi: {_day(3).strftime('%d.%m.%Y')}\nSon Ödeme Tarihi: "
                               f"{_day(-12).strftime('%d.%m.%Y')}",
                               f"Fatura Tarihi   Son Ödeme Tarihi\n{_day(3).strftime('%d.%m.%Y')}   "
                               f"{_day(-12).strftime('%d.%m.%Y')}")
    assert karekod.bill_doc(q, cols)["due_date"] == _day(-12).isoformat()
    # son ödeme yok (alışveriş e-arşivi): fatura değil; ödeme dekontu da değil
    assert karekod.bill_doc(q, bill_text().replace("Son Ödeme", "Teslim")) is None
    assert karekod.bill_doc(q, bill_text() + "\nÖdendi, dekont") is None
    # yabancı para: kural okuması yok
    assert karekod.bill_doc(karekod.parse(gib(parabirimi="USD")), bill_text()) is None


def test_api_qr_corrects_ai_when_rule_cannot_read(hosted, gemini):  # noqa: F811
    """Son ödeme tarihi okunamayan karekodlu fatura: yapay zekâ okur, tutarı karekod düzeltir."""
    c, _ = _user(hosted)
    gemini["replies"].append({"doc_type": "fatura", "merchant": "Örnek Enerji", "total": 624.4,
                              "due_date": _day(-12).isoformat(), "confidence": "orta"})
    text = bill_text().replace(f"Son Ödeme Tarihi: {_day(-12).strftime('%d.%m.%Y')}", "Son Ödeme Tarihi: ??")
    d = _parse(c, text, [gib()]).json()
    assert gemini["calls"] == 1 and d["type"] == "bill" and d["amount"] == 642.4 and d["qr_fixed"] == ["amount"]


def test_api_old_client_without_qr_unchanged(hosted, gemini):  # noqa: F811
    """Eski uygulama qr_text göndermez: akaryakıt fişi bugünkü gibi yapay zekâya gider."""
    from tests.test_receipt_templates import fuel_ai

    c, _ = _user(hosted)
    gemini["replies"].append(fuel_ai(35.42, 52.49, _day(1)))
    d = _parse(c, fuel_text()).json()
    assert gemini["calls"] == 1 and d["source"] == "yapay_zeka" and "rule" not in d and "qr" not in d


def test_qr_vkn_binds_receipt_template(tmpdb, gemini):  # noqa: F811
    """Metinde VKN okunamasa da (OCR bozmuş) e-arşiv fişinin karekodundaki VKN şablonu VKN imzasıyla öğretir ve bulur."""
    seller = _vkn("995500334")

    def broken(text: str) -> str:
        return "\n".join("DENEME VD: 99?55OO3" if "VD:" in ln else ln for ln in text.splitlines())

    def qr(items, day) -> list[str]:
        return [gib(total=round(sum(a for _, a, _ in items), 2), day=day).replace(VKN_BILL, seller)]

    gemini["replies"].append({**market_ai(FIRST, _day(5)), "tax_no": None})
    r1 = receipt.parse_document(_photo("white"), "image/jpeg", CATS, ocr_text=broken(market(FIRST, _day(5))),
                                qr=qr(FIRST, _day(5)))
    assert r1["source"] == "yapay_zeka" and r1["tax_no"] == seller and "tax_no" in r1["qr_fixed"]
    rows = _rows()
    assert len(rows) == 1 and rows[0]["sig"] == "vkn:" + rt._h(seller, 16)
    r2 = receipt.parse_document(_photo("gray"), "image/jpeg", CATS, ocr_text=broken(market(SECOND, _day(1))),
                                qr=qr(SECOND, _day(1)))
    assert r2["source"] == "sablon" and r2["tax_no"] == seller and gemini["calls"] == 1


# ---------------------------------------------------------------- düşünme ayarı

def test_thinking_setting(monkeypatch, tmpdb):
    monkeypatch.delenv("HESAPKITAP_RECEIPT_THINKING", raising=False)
    assert receipt.receipt_thinking() == "low"  # varsayılan
    system.put(receipt.THINKING_SETTING, "minimal")
    assert receipt.receipt_thinking() == "minimal"
    system.put(receipt.THINKING_SETTING, "off")
    assert receipt.receipt_thinking() is None
    monkeypatch.setenv("HESAPKITAP_RECEIPT_THINKING", "medium")  # ortam önce gelir
    assert receipt.receipt_thinking() == "medium"
    monkeypatch.setenv("HESAPKITAP_RECEIPT_THINKING", "saçma")
    assert receipt.receipt_thinking() == "low"
    assert receipt._thinking_config("gemini-3.1-flash-lite", "low") == {"thinkingLevel": "low"}
    assert receipt._thinking_config("gemini-2.5-flash", "minimal") == {"thinkingBudget": 0}
    assert receipt._thinking_config("gemini-2.5-pro", "minimal") == {"thinkingBudget": 128}
    assert receipt._thinking_config("baska-model", "low") is None
    assert receipt._thinking_config("gemini-3.1-flash-lite", None) is None


def _capture(monkeypatch, status_first: int = 200):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    bodies = []
    reply = {"doc_type": "fis", "merchant": "Örnek", "date": _day(1).isoformat(), "total": 10.0, "items": [],
             "confidence": "orta"}

    def post(url, json=None, timeout=None, headers=None):
        bodies.append(json)
        if status_first != 200 and len(bodies) == 1:
            return httpx.Response(400, json={"error": {"message": "Thinking level is not supported for this model."}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": __import__("json").dumps(reply)}]}}],
                                         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5,
                                                           "thoughtsTokenCount": 3}})

    monkeypatch.setattr(receipt.httpx, "post", post)
    return bodies


def test_receipt_call_sends_thinking_and_cost_record_unchanged(monkeypatch, tmpdb):
    monkeypatch.delenv("HESAPKITAP_RECEIPT_THINKING", raising=False)
    bodies = _capture(monkeypatch)
    before = _ai_calls()
    receipt._parse_document_ai(_photo("white"), "image/jpeg", CATS)
    assert bodies[-1]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert _ai_calls() == before + 1  # ai_calls kaydı aynen
    monkeypatch.setenv("HESAPKITAP_RECEIPT_THINKING", "off")
    receipt._parse_document_ai(_photo("white"), "image/jpeg", CATS)
    assert "thinkingConfig" not in bodies[-1]["generationConfig"]
    # yeniden okuma (güçlü model, doğruluk) düşünme ayarı almaz
    monkeypatch.setenv("HESAPKITAP_RECEIPT_THINKING", "low")
    receipt.reread_items(_photo("white"), "image/jpeg", 10.0)
    assert "thinkingConfig" not in bodies[-1]["generationConfig"]


def test_thinking_rejected_by_model_retries_without(monkeypatch, tmpdb):
    monkeypatch.delenv("HESAPKITAP_RECEIPT_THINKING", raising=False)
    bodies = _capture(monkeypatch, status_first=400)
    d = receipt._parse_document_ai(_photo("white"), "image/jpeg", CATS)
    assert d["total"] == 10.0 and len(bodies) == 2 and "thinkingConfig" not in bodies[1]["generationConfig"]


# ---------------------------------------------------------------- ölçüm (app/template_stats.py)

def test_rule_reads_counted_in_hit_rate(hosted, gemini):  # noqa: F811
    """qr_fuel / pos_slip fişte, qr_bill faturada outcome parser (yapay zekâsız) sayılır; karekodlu ama kuralın
    okuyamadığı fatura ai / qr_no_due; yönetici isabet göstergesinde rules alanında görünür."""
    from app import template_stats

    c, _ = _user(hosted)
    assert _parse(c, fuel_text(), [pump_at()]).json()["rule"] == "qr_fuel"
    assert _parse(c, pos(), color="gray").json()["rule"] == "pos_slip"
    assert _parse(c, bill_text(), [gib()], color="blue").json()["rule"] == "qr_bill"
    gemini["replies"].append({"doc_type": "fatura", "merchant": "Örnek Enerji", "total": 642.4,
                              "due_date": _day(-12).isoformat(), "confidence": "orta"})
    nodue = bill_text().replace(f"Son Ödeme Tarihi: {_day(-12).strftime('%d.%m.%Y')}", "Son Ödeme Tarihi: ??")
    assert _parse(c, nodue, [gib()], color="green").json()["type"] == "bill"
    o = template_stats.overview(30)
    assert o["fis"]["rules"] == {"qr_fuel": 1, "pos_slip": 1} and o["fis"]["by_outcome"]["parser"] == 2
    assert o["fis"]["free"] == 2 and o["fis"]["rate"] == 100
    assert o["fatura"]["rules"] == {"qr_bill": 1} and o["fatura"]["miss_reasons"] == {"qr_no_due": 1}
    assert o["fatura"]["rate"] == 50
    h = hosted.get("/api/admin/receipt-templates").json()["hit_rate"]
    assert h["fatura"]["rules"] == {"qr_bill": 1} and h["fis"]["rules"]["qr_fuel"] == 1


# ---------------------------------------------------------------- sunucu yedeği (zxing-cpp)

def _qr_image(payload: str, fmt: str = "JPEG", rotate: int = 0) -> bytes:
    """Uydurma fiş: beyaz kâğıt, üstte gri satırlar, altta karekod (zxing-cpp ile test sırasında üretilir)."""
    import io

    from PIL import Image, ImageDraw

    zx = pytest.importorskip("zxingcpp")
    q = zx.create_barcode(payload, zx.BarcodeFormat.QRCode).to_image(scale=6)
    qr = Image.frombuffer("L", (q.shape[1], q.shape[0]), bytes(memoryview(q)), "raw", "L", 0, 1)
    page = Image.new("RGB", (700, 1200), "white")
    d = ImageDraw.Draw(page)
    for i in range(12):
        d.rectangle((60, 60 + i * 45, 60 + (i * 37) % 500 + 80, 80 + i * 45), fill=(90, 90, 90))
    page.paste(qr.convert("RGB"), (int((700 - qr.width) / 2), 1200 - qr.height - 60))
    if rotate:
        page = page.rotate(rotate, expand=True, fillcolor="white")
    buf = io.BytesIO()
    page.save(buf, fmt)
    return buf.getvalue()


def test_server_scan_image_and_pdf():
    img = _qr_image(pump_sp())
    assert karekod.scan(img, "image/jpeg") == [pump_sp()]
    assert karekod.scan(_qr_image(gib(), rotate=5), "image/jpeg") == [gib()]  # hafif eğik
    assert karekod.scan(_qr_image(pump_at(), fmt="PDF"), "application/pdf") == [pump_at()]
    assert karekod.scan(_photo("white"), "image/jpeg") == []  # karekodsuz
    assert karekod.scan(b"bozuk", "image/jpeg") == [] and karekod.scan(b"%PDF-bozuk", "application/pdf") == []


def test_server_scan_budget_and_missing_library(monkeypatch):
    img = _qr_image(pump_sp())
    assert karekod.scan(img, "image/jpeg", budget=0) == []  # süre sınırı
    monkeypatch.setattr(karekod, "_zx", lambda: None)
    assert karekod.scan(img, "image/jpeg") == []  # kütüphane yok: sessizce atlanır


def test_api_old_client_server_reads_qr(hosted, gemini):  # noqa: F811
    """Eski uygulama qr_text göndermez: sunucu fotoğraftaki pompa karekodunu okur, akaryakıt yapay zekâsız; hak düşer."""
    c, u = _user(hosted)
    r = c.post("/api/documents/parse", files=[("file", ("f.jpg", _qr_image(pump_at()), "image/jpeg"))],
               data={"ocr_text": fuel_text(), "ocr_source": "vision"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["rule"] == "qr_fuel" and d["qr"] == {"kind": "pompa", "server": True}
    assert gemini["calls"] == 0 and d.get("charged") and _used(u["id"]) == 1


def test_api_pdf_bill_server_qr_with_text_layer(hosted, gemini, monkeypatch):  # noqa: F811
    """Web'den yüklenen e-arşiv PDF'i: karekod sayfadan, son ödeme tarihi PDF metin katmanından; yapay zekâ yok."""
    from app import bill_templates

    c, _ = _user(hosted)
    real = bill_templates.text_of
    monkeypatch.setattr(bill_templates, "text_of",
                        lambda data, mime: bill_text() if mime == "application/pdf" else real(data, mime))
    r = c.post("/api/documents/parse", files=[("file", ("f.pdf", _qr_image(gib(), fmt="PDF"), "application/pdf"))])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["type"] == "bill" and d["rule"] == "qr_bill" and d["amount"] == 642.4 and d["qr"]["server"] is True
    assert gemini["calls"] == 0


# ---------------------------------------------------------------- alışveriş e-Arşivi: karekod tek başına "fatura" değil

def restaurant_text(total: float = 108.0) -> str:
    return "\n".join(["ÖRNEK KAFE RESTORAN LTD. ŞTİ.", "DENEME MAH. ÇARŞI CAD. NO:7", "e-Arşiv Fatura",
                      "Fatura No: ABC2026000000042", f"Fatura Tarihi: {_day(3).strftime('%d.%m.%Y')}",
                      "MERCİMEK ÇORBASI   48,00", "AYRAN   20,00", "KÜNEFE   40,00",
                      f"Ödenecek Tutar: {_money(total)} TL", "KREDİ KARTI", "**** **** **** 4321"])


def market_earsiv_text(total: float = 254.9) -> str:
    return "\n".join(["ÖRNEK GIDA MARKET A.Ş.", "e-Arşiv Fatura", f"Tarih: {_day(2).strftime('%d.%m.%Y')}",
                      "ZEYTİNYAĞI 1 L   189,90", "EKMEK   15,00", "SÜT 1 L   50,00",
                      f"Vergiler Dahil Toplam Tutar: {_money(total)} TL", "GSM: 0555 000 00 00"])


AI_SAYS_BILL = {"doc_type": "fatura", "merchant": "Örnek Kafe Restoran", "legal_name": "ÖRNEK KAFE RESTORAN LTD. ŞTİ.",
                "total": 108.0, "due_date": None, "bill_type": "diger", "confidence": "orta"}


def test_shopping_earsiv_classification(tmpdb):
    q = karekod.parse(gib(total=108.0))
    assert karekod.shopping(q, restaurant_text()) and not karekod.is_bill(q, restaurant_text())
    assert karekod.shopping(karekod.parse(gib(total=254.9)), market_earsiv_text())  # GSM etiketi kurum işareti değil
    assert not karekod.shopping(q, bill_text()) and karekod.is_bill(q, bill_text())  # son ödeme
    utility_no_due = bill_text().replace("Son Ödeme", "Okuma").replace("Tüketim: 210 kWh", "Abone No: 0000000")
    assert karekod.is_bill(q, utility_no_due) and not karekod.shopping(q, utility_no_due)  # kurum işareti
    assert not karekod.shopping(q, restaurant_text() + "\nÖdendi, dekont")  # dekont: mevcut davranış
    assert not karekod.shopping(q, restaurant_text(), due=_day(-10).isoformat())  # son ödeme fatura tarihinden sonra
    assert karekod.shopping(q, restaurant_text(), due=_day(3).isoformat())  # aynı gün: tarih tekrarı
    assert not karekod.shopping(karekod.parse(gib(parabirimi="USD")), restaurant_text())


def test_api_restaurant_earsiv_ai_says_bill_becomes_receipt(hosted, gemini):  # noqa: F811
    """Cihaz testi hatası: restoranın e-Arşivi (son ödeme yok) yapay zekâ "fatura" dese de harcama (fiş) olur."""
    from app import bill_templates, db

    c, u = _user(hosted)
    gemini["replies"].append(dict(AI_SAYS_BILL))
    r = _parse(c, restaurant_text(), [gib(total=108.0)])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["type"] == "receipt" and d["total"] == 108.0 and d["date"] == _day(3).isoformat()
    assert d["merchant"] == "Örnek Kafe Restoran" and d["tax_no"] == VKN_BILL and "type" in d["qr_fixed"]
    assert d["receipt_file"] and "file_name" not in d and d["card_last4"] == "4321"
    assert [i["amount"] for i in d["items"]] == [48.0, 20.0, 40.0]  # metinden, toplam karekodla tutuyor
    assert d["vat_lines"] == [{"rate": 20, "amount": round(108.0 / 6, 2)}]
    assert gemini["calls"] == 1 and d.get("charged") and _used(u["id"]) == 1
    with db.connect() as cx:  # yanlış fatura şablonu öğrenilmedi
        bill_templates._init(cx)
        assert cx.execute("SELECT COUNT(*) FROM bill_templates WHERE source = 'learned'").fetchone()[0] == 0


def test_api_market_earsiv_without_phone_text_becomes_receipt(hosted, gemini):  # noqa: F811
    """Market e-Arşivi, karekod var, telefon metni yok: yapay zekâ "fatura" der → fiş (kalemsiz, tutar karekoddan)."""
    c, _ = _user(hosted)
    gemini["replies"].append({**AI_SAYS_BILL, "merchant": "Örnek Gıda Market", "total": 245.9})
    d = _parse(c, None, [gib(total=254.9, day=_day(2))]).json()
    assert d["type"] == "receipt" and d["total"] == 254.9 and d["merchant"] == "Örnek Gıda Market"
    assert d["date"] == _day(2).isoformat() and d["items"] == []


def test_api_electricity_earsiv_with_due_stays_bill(hosted, gemini):  # noqa: F811
    c, _ = _user(hosted)
    gemini["replies"].append({"doc_type": "fatura", "merchant": "Örnek Enerji", "total": 642.4,
                              "due_date": _day(-12).isoformat(), "confidence": "orta"})
    text = bill_text().replace(f"Son Ödeme Tarihi: {_day(-12).strftime('%d.%m.%Y')}", "Son Ödeme Tarihi: ??")
    d = _parse(c, text, [gib()]).json()
    assert d["type"] == "bill" and d["amount"] == 642.4 and d["due_date"] == _day(-12).isoformat()


def test_paid_bill_dekont_unchanged(hosted, gemini):  # noqa: F811
    """Ödenmiş fatura dekontu (paid): fatura olarak kalır (mevcut davranış)."""
    c, _ = _user(hosted)
    gemini["replies"].append({**AI_SAYS_BILL, "paid": True, "paid_at": _day(3).isoformat(), "pay_method": "kredi_karti"})
    d = _parse(c, restaurant_text() + "\nTahsil edildi, dekont", [gib(total=108.0)]).json()
    assert d["type"] == "bill" and d["paid"] is True


def test_bill_without_due_and_no_qr_becomes_receipt():
    """Karekodu okunamayan restoran e-Arşivi: okuma "fatura" dese de son ödeme / kurum işareti yoksa fiş (12.11)."""
    from app import karekod

    txt = ("ORNEK CAFE RESTORAN\nORNEK MAH. ORNEK CAD. NO:1\nTARIH 12.11.2026 SAAT 13:05\nE-ARSIV FATURA\n"
           "LATTE 1 X 85,00 85,00\nSU 1 X 23,00 23,00\nTOPKDV 9,82\nTOPLAM 108,00\nKREDI KARTI 108,00\n")
    doc = {"type": "bill", "provider": "Ornek Cafe Restoran", "amount": 108.0, "due_date": None, "paid": False,
           "category": "Faturalar"}
    r = karekod.apply_hints(doc, None, txt, ["Kafe & Restoran", "Diğer"])
    assert r["type"] == "receipt" and r["total"] == 108.0 and r["date"] == "2026-11-12"
    elec = "ORNEK ELEKTRIK PERAKENDE\nABONE NO 123\nTUKETIM 210 KWH\nODENECEK 450,00\n"
    d2 = {"type": "bill", "provider": "Ornek Elektrik", "amount": 450.0, "due_date": None, "paid": False,
          "category": "Elektrik"}
    assert karekod.apply_hints(d2, None, elec, [])["type"] == "bill"
    d3 = dict(doc, due_date="2026-11-30")
    assert karekod.apply_hints(d3, None, txt, [])["type"] == "bill"  # son ödeme tarihi okunduysa fatura kalır
    assert karekod.apply_hints(dict(doc), None, None, [])["type"] == "bill"  # metin yoksa eski davranış
