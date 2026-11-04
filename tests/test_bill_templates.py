"""Fatura şablonları (app/bill_templates.py): yapay zekâ okumasından öğrenme, ikinci faturada yapay zekâsız okuma,
tutarsızlıkta yapay zekâya düşme ve devre dışı kalma, şablonda kişisel değer olmaması, dekont, e-posta yolu.
Bütün kurum adları, kişiler ve numaralar uydurmadır."""
import json
from datetime import date, timedelta

import httpx
import pytest

from app import bill_templates, db, receipt


def _d(days: int) -> date:
    return date.today() + timedelta(days=days)


def _tr(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _money_tr(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def elektrik(amount: float, due: date, issue: date, tesisat: str = "4001234567", donem: str = "Ekim 2026",
             due_label: str = "Son Ödeme Tarihi") -> str:
    """Uydurma kurumun e-arşiv fatura metni (aynı düzen, değişen değerler)."""
    return f"""ÖRNEK ENERJİ ELEKTRİK PERAKENDE SATIŞ A.Ş.
e-Arşiv Fatura
Sayın DENEME KİŞİ
Deneme Mah. Örnek Sok. No: 7 Test/İstanbul
Fatura No: OEA2026000012345
Fatura Tarihi: {_tr(issue)}
{due_label}: {_tr(due)}
Fatura Dönemi: {donem}
Tesisat No: {tesisat}
Tüketim (kWh) 210
Enerji Bedeli 512,40
KDV (%20) 102,48
Fatura Tutarı {_money_tr(amount)} TL
DENEME KİŞİ Ödenecek Tutar {_money_tr(amount)} TL
"""


def ai_reply(amount: float, due: date, issue: date, **kw) -> dict:
    return {"provider": "Örnek Enerji", "type": "elektrik", "amount": amount, "due_date": due.isoformat(),
            "issue_date": issue.isoformat(), "period": "Ekim 2026", "subscriber_no": "4001234567", "paid": False,
            "confidence": "yüksek", **kw}


@pytest.fixture()
def gemini(monkeypatch):
    """Sahte Gemini: sıradaki yanıtı verir, çağrıları sayar."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    state = {"calls": 0, "replies": []}

    def post(url, json=None, timeout=None, headers=None):
        state["calls"] += 1
        reply = state["replies"].pop(0)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(reply)}]}}],
            "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 100}})

    monkeypatch.setattr(receipt.httpx, "post", post)
    return state


def _rows() -> list[dict]:
    with db.connect() as c:
        bill_templates._init(c)
        return [dict(r) for r in c.execute("SELECT * FROM bill_templates")]


def _ai_calls() -> int:
    from app import system

    with system.connect() as c:
        try:
            return c.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0]
        except Exception:  # noqa: BLE001 - tablo henüz yok
            return 0


def test_learn_then_second_bill_read_without_ai(tmpdb, gemini):
    first = elektrik(845.30, _d(10), _d(-10))
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10)))
    r1 = receipt.parse_bill(first.encode(), "text/plain")
    assert r1["source"] == "yapay_zeka" and gemini["calls"] == 1
    rows = _rows()
    assert len(rows) == 1 and rows[0]["sig"] == "ornek enerji" and rows[0]["source"] == "learned"
    spec = json.loads(rows[0]["spec"])
    assert spec["fields"]["amount"][0]["label"] == "odenecek tutar"  # "Fatura Tutarı"ndan güçlü etiket
    assert spec["fields"]["due_date"][0]["label"] == "son odeme tarihi"
    costs = _ai_calls()

    second = elektrik(1234.56, _d(15), _d(-5), tesisat="4001234567", donem="Kasım 2026")
    r2 = receipt.parse_bill(second.encode(), "text/plain")
    assert gemini["calls"] == 1  # yapay zekâya gidilmedi
    assert _ai_calls() == costs  # maliyet kaydı yazılmadı
    assert r2["source"] == "sablon" and r2["provider"] == "Örnek Enerji" and r2["category"] == "Elektrik"
    assert r2["amount"] == 1234.56 and r2["due_date"] == _d(15).isoformat() and r2["issue_date"] == _d(-5).isoformat()
    assert r2["description"] == "Kasım 2026 · Abone 4001234567"
    assert (r2["paid"], r2["paid_at"], r2["payment"]) == (False, None, None)
    assert _rows()[0]["uses"] == 1


def test_template_has_no_personal_values(tmpdb, gemini):
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10)))
    receipt.parse_bill(elektrik(845.30, _d(10), _d(-10)).encode(), "text/plain")
    spec = _rows()[0]["spec"]
    assert bill_templates.is_safe(json.loads(spec))
    for personal in ("4001234567", "845", "deneme", "DENEME", "Sok", "OEA2026", _tr(_d(10)), str(_d(10).year)):
        assert personal not in spec


def test_columnar_layout_prev_line_anchors(tmpdb, gemini):
    def su(amount, due, issue, abone):
        return f"""ÖRNEK SU VE KANALİZASYON İDARESİ
Fatura Tarihi    Son Ödeme Tarihi
{_tr(issue)}    {_tr(due)}
Abone No
{abone}
TOPLAM TUTAR
{_money_tr(amount)}
"""
    gemini["replies"].append({"provider": "Örnek Su", "type": "su", "amount": 312.75, "due_date": _d(8).isoformat(),
                              "issue_date": _d(-12).isoformat(), "subscriber_no": "1234567", "confidence": "yüksek"})
    receipt.parse_bill(su(312.75, _d(8), _d(-12), "1234567").encode(), "text/plain")
    r = receipt.parse_bill(su(1050.0, _d(20), _d(-1), "1234567").encode(), "text/plain")
    assert gemini["calls"] == 1 and r["source"] == "sablon"
    assert (r["amount"], r["due_date"], r["issue_date"], r["category"]) == (
        1050.0, _d(20).isoformat(), _d(-1).isoformat(), "Su")
    assert r["description"] == "Abone 1234567"


def test_inconsistent_falls_back_to_ai_and_disables(tmpdb, gemini):
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10)))
    receipt.parse_bill(elektrik(845.30, _d(10), _d(-10)).encode(), "text/plain")
    # kurum düzeni değişti: son ödeme tarihi etiketi yok → şablon tarih bulamaz, yapay zekâ okur
    changed = elektrik(500.0, _d(12), _d(-8), due_label="Ödeme Günü")
    gemini["replies"].append(ai_reply(500.0, _d(12), _d(-8)))
    r = receipt.parse_bill(changed.encode(), "text/plain")
    assert r["source"] == "yapay_zeka" and r["amount"] == 500.0 and gemini["calls"] == 2
    assert (_rows()[0]["fails"], _rows()[0]["disabled"]) == (1, 0)
    # tarihi makul aralık dışında (5 yıl önce) olan fatura da tutarsız sayılır
    old = elektrik(400.0, _d(-5 * 365), _d(-5 * 365 - 20))
    gemini["replies"].append(ai_reply(400.0, _d(-5 * 365), _d(-5 * 365 - 20)))
    assert receipt.parse_bill(old.encode(), "text/plain")["source"] == "yapay_zeka"
    assert (_rows()[0]["fails"], _rows()[0]["disabled"]) == (2, 1)  # iki hata → devre dışı
    # devre dışı şablon artık uygulanmaz: düzgün fatura da yapay zekâya gider
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10)))
    assert receipt.parse_bill(elektrik(845.30, _d(10), _d(-10)).encode(), "text/plain")["source"] == "yapay_zeka"
    assert gemini["calls"] == 4


def test_success_resets_fail_count_and_new_rule_relearns(tmpdb, gemini):
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10)))
    receipt.parse_bill(elektrik(845.30, _d(10), _d(-10)).encode(), "text/plain")
    gemini["replies"].append(ai_reply(500.0, _d(12), _d(-8)))
    receipt.parse_bill(elektrik(500.0, _d(12), _d(-8), due_label="Ödeme Günü").encode(), "text/plain")
    assert _rows()[0]["fails"] == 1
    assert receipt.parse_bill(elektrik(600.0, _d(9), _d(-9)).encode(), "text/plain")["source"] == "sablon"
    assert _rows()[0]["fails"] == 0
    # düzen kalıcı değişti ("Son Ödeme" → "Vade Tarihi"): yapay zekâ okuması yeni kuralı öğretir, şablon yenilenir
    new = elektrik(700.0, _d(11), _d(-7), due_label="Vade Tarihi")
    gemini["replies"].append(ai_reply(700.0, _d(11), _d(-7)))
    assert receipt.parse_bill(new.encode(), "text/plain")["source"] == "yapay_zeka"
    assert json.loads(_rows()[0]["spec"])["fields"]["due_date"][0]["label"] == "vade tarihi"
    assert (_rows()[0]["fails"], _rows()[0]["disabled"]) == (0, 0)
    r = receipt.parse_bill(elektrik(710.0, _d(13), _d(-6), due_label="Vade Tarihi").encode(), "text/plain")
    assert r["source"] == "sablon" and r["amount"] == 710.0


def test_paid_receipt_is_read_by_ai_and_not_learned(tmpdb, gemini):
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10)))
    receipt.parse_bill(elektrik(845.30, _d(10), _d(-10)).encode(), "text/plain")
    spec = _rows()[0]["spec"]
    dekont = "ÖDEME DEKONTU\nİşlem başarılı\n" + elektrik(845.30, _d(10), _d(-10))
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10), paid=True, paid_at=_d(-1).isoformat(),
                                      pay_method="havale"))
    r = receipt.parse_bill(dekont.encode(), "text/plain")
    assert gemini["calls"] == 2 and r["source"] == "yapay_zeka"
    assert (r["paid"], r["paid_at"], r["payment"]) == (True, _d(-1).isoformat(), "havale")
    assert _rows()[0]["spec"] == spec and _rows()[0]["fails"] == 0  # dekont şablonu bozmaz, öğretmez


def test_low_confidence_or_missing_provider_not_learned(tmpdb, gemini):
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10), confidence="düşük"))
    receipt.parse_bill(elektrik(845.30, _d(10), _d(-10)).encode(), "text/plain")
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10), provider="Elektrik"))  # yalnız genel sözcük
    receipt.parse_bill(elektrik(845.30, _d(10), _d(-10)).encode(), "text/plain")
    assert _rows() == []


def test_pdf_text_layer_and_documents_path(tmpdb, gemini, monkeypatch):
    """PDF metin katmanı (parse_bill ve parse_document) ve telefondan gelen OCR metni (parse_document_text)."""
    import app.parsers as parsers

    texts = {b"%PDF-1.4 a": elektrik(845.30, _d(10), _d(-10)), b"%PDF-1.4 b": elektrik(99.9, _d(14), _d(-3))}
    monkeypatch.setattr(parsers, "extract_text", lambda data, password=None: texts[data])
    gemini["replies"].append({"doc_type": "fatura", "merchant": "Örnek Enerji", "bill_type": "elektrik",
                              "total": 845.30, "due_date": _d(10).isoformat(), "date": _d(-10).isoformat(),
                              "period": "Ekim 2026", "subscriber_no": "4001234567", "confidence": "yüksek"})
    d1 = receipt.parse_document(b"%PDF-1.4 a", "application/pdf", ["Diğer"])
    assert d1["type"] == "bill" and d1["source"] == "yapay_zeka" and len(_rows()) == 1
    d2 = receipt.parse_document(b"%PDF-1.4 b", "application/pdf", ["Diğer"])
    assert gemini["calls"] == 1 and d2["type"] == "bill" and d2["source"] == "sablon" and d2["amount"] == 99.9
    assert d2["due_date"] == _d(14).isoformat() and d2["provider"] == "Örnek Enerji"
    b = receipt.parse_bill(b"%PDF-1.4 b", "application/pdf")
    assert b["source"] == "sablon" and gemini["calls"] == 1
    # OCR metninde Türkçe harfler düşmüş olabilir
    ocr = elektrik(77.0, _d(6), _d(-6)).replace("Ö", "O").replace("ö", "o").replace("İ", "I").replace("ı", "i")
    t = receipt.parse_document_text(ocr, ["Diğer"])
    assert t["source"] == "sablon" and t["amount"] == 77.0 and gemini["calls"] == 1


def test_receipt_text_not_read_by_bill_template(tmpdb, gemini):
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10)))
    receipt.parse_bill(elektrik(845.30, _d(10), _d(-10)).encode(), "text/plain")
    gemini["replies"].append({"doc_type": "fis", "merchant": "Örnek Market", "total": 42.5, "items": []})
    d = receipt.parse_document_text("ÖRNEK MARKET\nEKMEK *10,00\nTOPLAM *42,50", ["Diğer"])
    assert d["type"] == "receipt" and gemini["calls"] == 2 and len(_rows()) == 1


def test_api_bills_parse_uses_template_and_still_charges(client, gemini, monkeypatch):
    import app.parsers as parsers
    from app import doc_reads

    texts = {b"%PDF-1.4 a": elektrik(845.30, _d(10), _d(-10)), b"%PDF-1.4 b": elektrik(99.9, _d(14), _d(-3))}
    monkeypatch.setattr(parsers, "extract_text", lambda data, password=None: texts[data])
    charged = []
    monkeypatch.setattr(doc_reads, "charge", lambda key, kind=doc_reads.BELGE: charged.append(key))
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10)))
    a = client.post("/api/bills/parse", files={"file": ("a.pdf", b"%PDF-1.4 a", "application/pdf")}).json()
    b = client.post("/api/bills/parse", files={"file": ("b.pdf", b"%PDF-1.4 b", "application/pdf")}).json()
    assert a["source"] == "yapay_zeka" and b["source"] == "sablon" and b["amount"] == 99.9 and b["file_name"]
    assert gemini["calls"] == 1 and charged == [b"%PDF-1.4 a", b"%PDF-1.4 b"]  # şablon okuması da hak düşer


def test_inbound_mail_bill_uses_template(tmpdb, gemini, monkeypatch):
    from app import auth, inbound, system
    from tests.test_inbound import _raw, signed

    owner = auth.create_user("ayse@ornek.com", "ayse-sifre-12345")
    to, sig = inbound.address(owner), signed("ornekenerji.com.tr")
    monkeypatch.setattr(inbound, "BILL_DOMAINS", (*inbound.BILL_DOMAINS, "ornekenerji.com.tr"))
    gemini["replies"].append(ai_reply(845.30, _d(10), _d(-10)))
    r1 = inbound.handle(_raw("efatura@ornekenerji.com.tr", body=elektrik(845.30, _d(10), _d(-10)), mid="<t1@x>"),
                        to, verify=sig)
    r2 = inbound.handle(_raw("efatura@ornekenerji.com.tr", body=elektrik(912.0, _d(40), _d(20)), mid="<t2@x>"),
                        to, verify=sig)
    assert r1["status"] == r2["status"] == "ok" and gemini["calls"] == 1
    with db.use_space(system.user_db_path(owner), owner):
        bills = sorted(db.list_bills(), key=lambda b: b["due_date"])
        assert [(b["provider"], b["amount"]) for b in bills] == [("Örnek Enerji", 845.3), ("Örnek Enerji", 912.0)]
        assert bills[1]["description"] == "Ekim 2026 · Abone 4001234567"
        assert len(_rows()) == 1  # şablon kullanıcının kendi defterinde
    assert _rows() == []  # başka defterde yok


def test_builtin_template(tmpdb, gemini, monkeypatch):
    monkeypatch.setattr(bill_templates, "BUILTINS", [(["ornekgaz"], "Örnekgaz", "Doğalgaz", [])])
    text = f"""ÖRNEKGAZ DOĞALGAZ DAĞITIM A.Ş.
Abone No: 99887766
Fatura Tarihi: {_tr(_d(-9))}
Son Ödeme Tarihi: {_tr(_d(9))}
Ödenecek Tutar: {_money_tr(1520.4)} TL
"""
    r = receipt.parse_bill(text.encode(), "text/plain")
    assert gemini["calls"] == 0 and r["source"] == "sablon"
    assert (r["provider"], r["category"], r["amount"], r["due_date"]) == ("Örnekgaz", "Doğalgaz", 1520.4,
                                                                         _d(9).isoformat())
    assert r["description"] == "Abone 99887766"
    # yerleşik tutmazsa (tarih yok) yapay zekâ; iki kez tutmazsa devre dışı
    bad = text.replace("Son Ödeme Tarihi", "Ödeme")
    gemini["replies"] += [{"provider": "Örnekgaz", "type": "dogalgaz", "amount": 1520.4,
                           "due_date": _d(9).isoformat(), "confidence": "düşük"}] * 2
    receipt.parse_bill(bad.encode(), "text/plain")
    receipt.parse_bill(bad.encode(), "text/plain")
    assert [(r["sig"], r["disabled"]) for r in _rows()] == [("builtin:ornekgaz", 1)]
    gemini["replies"].append({"provider": "Örnekgaz", "type": "dogalgaz", "amount": 1520.4,
                              "due_date": _d(9).isoformat(), "confidence": "düşük"})
    assert receipt.parse_bill(text.encode(), "text/plain")["source"] == "yapay_zeka"


def test_is_safe_rejects_document_values():
    ok = {"v": 1, "sig": ["ornek"], "provider": "Örnek", "category": "Su",
          "fields": {"amount": [{"label": "odenecek tutar", "off": 0, "nth": 0, "occ": 0}],
                     "due_date": [{"label": "son odeme tarihi", "off": 0, "nth": 0, "occ": 0}]}}
    assert bill_templates.is_safe(ok)
    for bad in ({**ok, "fields": {**ok["fields"], "amount": [{"label": "ayse yilmaz", "off": 0, "nth": 0, "occ": 0}]}},
                {**ok, "fields": {**ok["fields"], "amount": [{"label": "tutar 845", "off": 0, "nth": 0, "occ": 0}]}},
                {**ok, "sig": ["4001234567"]}, {**ok, "provider": "Abone 4001234567"}, {**ok, "extra": "x"},
                {**ok, "category": "Market"}):
        assert not bill_templates.is_safe(bad)
