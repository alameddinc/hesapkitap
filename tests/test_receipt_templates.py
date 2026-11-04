"""Zincir mağaza / akaryakıt fişi şablonları (app/receipt_templates.py): yapay zekâ okumasından öğrenme, sonraki fişte
telefon metninden yapay zekâsız okuma, kabul ölçütleri tutmazsa yapay zekâya düşme ve devre dışı kalma, miktar satırı
üstte/altta, KG, indirim, akaryakıt litresi, POS slibi, VKN özeti, şablonda kişisel değer olmaması, topluluk paylaşımı,
bir imzada birden çok düzen, yönetici görünümü. Bütün işyerleri, adresler, VKN'ler, plakalar ve tutarlar uydurmadır."""
import json
from datetime import date, timedelta

import httpx
import pytest

from app import receipt, receipt_templates as rt, system, vat
from tests.test_doc_reads import _jpeg, _used, hosted  # noqa: F401 - fixture
from tests.test_plans import _user

CATS = ["Market & Gıda", "Ulaşım & Araç", "Diğer"]


def _vkn(prefix: str) -> str:
    """Kontrol hanesi tutan uydurma VKN."""
    return next(prefix + str(d) for d in range(10) if vat.vkn_valid(prefix + str(d)))


VKN = _vkn("999123456")
VKN_FUEL = _vkn("998765432")


def _money(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _day(n: int) -> date:
    return date.today() - timedelta(days=n)


def market(items: list[tuple], day: date, discount: float = 0.0, qty_below: bool = False, vkn: str = VKN,
           dot: bool = False) -> str:
    """Uydurma zincir marketin fiş metni. items: (ad, tutar, miktar satırı ya da None). dot: başka yazar kasa (25.00)."""
    _tr = (lambda x: f"{x:.2f}") if dot else _money
    rows = []
    for name, amount, qty in items:
        line = f"{name}   %1   *{_tr(amount)}"
        rows += [line, qty] if qty and qty_below else [qty, line] if qty else [line]
    if discount:
        rows.append(f"İNDİRİM   *-{_tr(discount)}")
    total = round(sum(a for _, a, _ in items) - discount, 2)
    return "\n".join([
        "ÖRNEKZİNCİR MARKETCİLİK A.Ş.", "DENEME MAH. ÖRNEK CAD. NO:1", "TESTİLÇE / İSTANBUL", f"DENEME VD: {vkn}",
        f"TARİH : {day.strftime('%d/%m/%Y')}", "SAAT : 10:15:00", "FİŞ NO : 0042", *rows,
        f"TOPKDV   *{_tr(round(total / 101, 2))}", f"TOPLAM   *{_tr(total)}", f"KREDİ KARTI   *{_tr(total)}",
        "**** **** **** 1234", "EKÜ NO:0001   Z NO:0099"])


def market_ai(items: list[tuple], day: date, discount: float = 0.0, **kw) -> dict:
    """Yapay zekânın aynı fişi okuması (DOC_PROMPT biçimi)."""
    its = []
    for name, amount, qty in items:
        it = {"name": name, "amount": amount}
        if qty:
            n, unit, price = qty.split()[0], qty.split()[1], qty.split()[3]
            it.update(qty=float(n.replace(",", ".")), unit="kg" if unit == "KG" else "adet",
                      unit_price=float(price.replace(",", ".")))
        its.append(it)
    if discount:
        its.append({"name": "İNDİRİM", "amount": -discount})
    total = round(sum(a for _, a, _ in items) - discount, 2)
    return {"doc_type": "fis", "merchant": "Örnekzincir", "legal_name": "ÖRNEKZİNCİR MARKETCİLİK A.Ş.",
            "date": day.isoformat(), "total": total, "payment": "kart", "card_last4": "1234",
            "category": "Market & Gıda", "tax_no": VKN, "items": its, "confidence": "yüksek", **kw}


FIRST = [("SU 0,5 LT", 25.0, "2 ADET X 12,50"), ("EKMEK", 10.0, None), ("DOMATES KG", 30.0, "0,750 KG X 40,00")]
SECOND = [("PEYNİR 500 GR", 89.9, None), ("ÇAY 1 KG", 159.5, None), ("ELMA KG", 22.48, "1,124 KG X 20,00"),
          ("SU 0,5 LT", 37.5, "3 ADET X 12,50")]


def fuel(litre: float, price: float, day: date, vkn: str = VKN_FUEL, pay: str = "NAKİT") -> str:
    amount = round(litre * price, 2)
    return "\n".join([
        "ÖRNEK AKARYAKIT TİC. LTD. ŞTİ.", "DENEME MAH. YOL CAD. NO:5", f"DENEMEKÖY VD: {vkn}", "EPDK: BAY/000-00/00000",
        f"{day.strftime('%d-%m-%Y')}   14:20", "FİŞ NO:0012", "34 XYZ 999", f"{_money(litre)} LT X {_money(price)}",
        f"MOTORİN   %20   *{_money(amount)}", f"KDV   *{_money(round(amount / 6, 2))}", f"TOPLAM   *{_money(amount)}",
        f"{pay}   *{_money(amount)}", "İYİ YOLCULUKLAR"])


def fuel_ai(litre: float, price: float, day: date) -> dict:
    amount = round(litre * price, 2)
    return {"doc_type": "fis", "merchant": "Örnek Akaryakıt", "legal_name": "ÖRNEK AKARYAKIT TİC. LTD. ŞTİ.",
            "date": day.isoformat(), "total": amount, "payment": "nakit", "category": "Ulaşım & Araç",
            "tax_no": VKN_FUEL, "confidence": "yüksek",
            "items": [{"name": "MOTORİN", "qty": litre, "unit": "lt", "unit_price": price, "amount": amount}]}


@pytest.fixture()
def gemini(monkeypatch):
    """Sahte Gemini: sıradaki yanıtı verir, çağrıları sayar."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    state = {"calls": 0, "replies": []}

    def post(url, json=None, timeout=None, headers=None):
        state["calls"] += 1
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(state["replies"].pop(0))}]}}],
            "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 100}})

    monkeypatch.setattr(receipt.httpx, "post", post)
    return state


def _photo(color: str) -> bytes:
    """Ayrı belge sayılan (okuma hakkı ayrı düşen) fotoğraflar."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 60), color).save(buf, "JPEG")
    return buf.getvalue()


def _read(text: str) -> dict:
    return receipt.parse_document(_jpeg(), "image/jpeg", CATS, ocr_text=text)


def _rows() -> list[dict]:
    with system.connect() as c:
        rt._init(c)
        return [dict(r) for r in c.execute("SELECT * FROM receipt_templates ORDER BY id")]


def test_learn_then_second_receipt_read_without_ai(tmpdb, gemini):
    gemini["replies"].append(market_ai(FIRST, _day(5)))
    r1 = _read(market(FIRST, _day(5)))
    assert r1["source"] == "yapay_zeka" and gemini["calls"] == 1
    rows = _rows()
    assert len(rows) == 1 and rows[0]["sig"].startswith("vkn:") and rows[0]["source"] == "learned"
    spec = json.loads(rows[0]["spec"])
    assert spec["qty"] == "above" and spec["total"] == [{"label": "toplam", "off": 0, "occ": 0}]
    assert spec["date"] == {"sep": "/", "year": 4, "label": True} and spec["num"] == "tr"

    r2 = _read(market(SECOND, _day(1)))
    assert gemini["calls"] == 1  # yapay zekâya gidilmedi
    assert r2["source"] == "sablon" and r2["type"] == "receipt"
    assert r2["total"] == 309.38 and r2["date"] == _day(1).isoformat()
    assert [i["amount"] for i in r2["items"]] == [89.9, 159.5, 22.48, 37.5]
    assert [i["name"] for i in r2["items"]] == ["PEYNİR 500 GR", "ÇAY 1 KG", "ELMA KG", "SU 0,5 LT"]
    elma, su = r2["items"][2], r2["items"][3]
    assert (elma["qty"], elma["unit"], elma["unit_price"]) == (1.124, "kg", 20.0)  # KG satırı
    assert (su["qty"], su["unit"], su["unit_price"]) == (3, "adet", 12.5)
    assert r2["payment"] == "kart" and r2["card_last4"] == "1234" and r2["tax_no"] == VKN
    assert r2["category"] == "Market & Gıda" and r2["merchant"] == "Örnekzincir Marketcilik"
    assert _rows()[0]["uses"] == 1 and _rows()[0]["last_used"]


def test_quantity_line_below_and_discount(tmpdb, gemini):
    gemini["replies"].append(market_ai(FIRST, _day(4), discount=5.0))
    _read(market(FIRST, _day(4), discount=5.0, qty_below=True))
    spec = json.loads(_rows()[0]["spec"])
    assert spec["qty"] == "below"
    r = _read(market(SECOND, _day(2), discount=12.4, qty_below=True))
    assert gemini["calls"] == 1 and r["source"] == "sablon" and r["total"] == round(309.38 - 12.4, 2)
    assert r["items"][-1]["amount"] == -12.4  # indirim satırı eksi kalem
    assert (r["items"][3]["qty"], r["items"][3]["unit_price"]) == (3, 12.5)  # miktar satırı üstteki ürüne


def test_fuel_litre_and_inconsistent_litre_falls_back(tmpdb, gemini):
    gemini["replies"].append(fuel_ai(27.73, 97.40, _day(6)))
    _read(fuel(27.73, 97.40, _day(6)))
    spec = json.loads(_rows()[0]["spec"])
    assert spec["fuel"] is True and spec["category"] == "Ulaşım & Araç"
    r = _read(fuel(10.24, 97.70, _day(2), pay="K.KARTI"))
    assert gemini["calls"] == 1 and r["source"] == "sablon" and r["total"] == 1000.45
    it = r["items"][0]
    assert (it["qty"], it["unit"], it["unit_price"], it["amount"]) == (10.24, "lt", 97.7, 1000.45)
    assert r["payment"] == "kart" and r["category"] == "Ulaşım & Araç"
    assert "34 XYZ" not in json.dumps(r, ensure_ascii=False)  # plaka kalem adı olmaz
    # OCR litreyi bozmuş: litre × birim fiyat tutarı vermiyor → yapay zekâ
    bad = fuel(10.24, 97.70, _day(1)).replace("10,24 LT", "16,24 LT")
    gemini["replies"].append(fuel_ai(10.24, 97.70, _day(1)))
    assert _read(bad)["source"] == "yapay_zeka" and gemini["calls"] == 2


def test_acceptance_failure_falls_back_then_disables(tmpdb, gemini):
    gemini["replies"].append(market_ai(FIRST, _day(5)))
    _read(market(FIRST, _day(5)))
    # kalemler toplamı tutmuyor (OCR bir satırı düşürmüş): şablon kabul edilmez, yapay zekâ okur
    broken = market(SECOND, _day(1)).replace("EKMEK", "X").replace("ÇAY 1 KG   %1   *159,50\n", "")
    for n in (2, 3):
        gemini["replies"].append({**market_ai(SECOND, _day(1)), "confidence": "düşük"})  # öğretmesin
        assert _read(broken)["source"] == "yapay_zeka" and gemini["calls"] == n
    row = _rows()[0]
    assert row["fails"] == 2 and row["disabled"] == 1
    # devre dışı şablon sağlam fişte de uygulanmaz
    gemini["replies"].append({**market_ai(SECOND, _day(1)), "confidence": "düşük"})
    assert _read(market(SECOND, _day(1)))["source"] == "yapay_zeka" and gemini["calls"] == 4
    # yapay zekâ aynı kuralı yeniden öğretirse açılır
    gemini["replies"].append(market_ai(SECOND, _day(1)))
    _read(market(SECOND, _day(1)))
    assert _rows()[0]["disabled"] == 0 and _rows()[0]["fails"] == 0


def test_implausible_date_and_wrong_total_label_rejected(tmpdb, gemini):
    gemini["replies"].append(market_ai(FIRST, _day(5)))
    _read(market(FIRST, _day(5)))
    spec = json.loads(_rows()[0]["spec"])
    future = rt.apply(market(SECOND, date.today() + timedelta(days=3)), spec, CATS)
    old = rt.apply(market(SECOND, date.today() - timedelta(days=800)), spec, CATS)
    assert future["total"] == 309.38 and not rt.acceptable(future) and not rt.acceptable(old)
    # toplam yalnız TOPLAM etiketinin satırından: "ARA TOPLAM" / "TOPLAM KDV" sayılmaz
    text = market(SECOND, _day(1)).replace("TOPLAM   *309,38", "ARA TOPLAM   *309,38\nTOPLAM KDV   *3,06")
    assert rt.apply(text, spec, CATS) is None


def test_template_has_no_personal_values_and_hashes_vkn(tmpdb, gemini):
    gemini["replies"].append(market_ai(FIRST, _day(5)))
    _read(market(FIRST, _day(5)))
    row = _rows()[0]
    js = row["spec"] + row["sig"]
    for value in (VKN, "ornekzincir", "ÖRNEKZİNCİR", "SU 0,5", "EKMEK", "DOMATES", "25,00", "25.0", "1234", "DENEME",
                  "TESTİLÇE", "12,50"):
        assert value.lower() not in js.lower()
    spec = json.loads(row["spec"])
    assert rt.is_safe(spec) and spec["sig"]["vkn"] == rt._h(VKN, 16) and row["sig"] == "vkn:" + rt._h(VKN, 16)
    assert not rt.shareable(spec)  # bilinen zincir değil: bu sunucuda kalır
    # başka VKN'li işyerinin aynı düzendeki fişi bu şablonla okunmaz
    other = market(SECOND, _day(1), vkn=_vkn("997000111"))
    gemini["replies"].append({**market_ai(SECOND, _day(1)), "tax_no": None, "confidence": "düşük"})
    assert _read(other)["source"] == "yapay_zeka"


def test_is_safe_rejects_document_values():
    good = {"v": 1, "kind": "receipt", "sig": {"vkn": "0123456789abcdef"}, "num": "tr", "fuel": False,
            "date": {"sep": "/", "year": 4, "label": True}, "total": [{"label": "toplam", "off": 0, "occ": 0}],
            "qty": "above", "single": False}
    assert rt.is_safe(good)
    bad = [
        {**good, "merchant": "Deneme Kişi Bakkal"},                                 # izinsiz alan (ad)
        {**good, "sig": {"vkn": VKN}},                                               # VKN açık yazılmış
        {**good, "sig": {"words": ["deneme"]}},                                      # özetlenmemiş kelime
        {**good, "brand": "Deneme Bakkal"},                                          # listede olmayan zincir
        {**good, "total": [{"label": "deneme sokak", "off": 0, "occ": 0}]},          # sözlük dışı etiket
        {**good, "total": [{"label": "toplam", "off": 0, "occ": 0, "value": 12.5}]},  # tutar
        {**good, "total": [{"label": "toplam", "off": 7, "occ": 0}]},
        {**good, "date": {"sep": "/", "year": 4, "label": True, "day": "2026-11-01"}},
        {**good, "category": "Deneme Mah. No 1"}, {**good, "qty": "yan"}, {**good, "fuel": "evet"},
    ]
    assert not any(rt.is_safe(s) for s in bad)
    chain = {**good, "brand": "Opet", "sig": {"vkn": "0123456789abcdef", "words": [rt._h("opet", 12)]}}
    assert rt.shareable(chain) and not rt.shareable({**chain, "sig": {"words": [rt._h("opet", 12)]}})
    from app import templates

    assert templates.is_safe(chain) and not templates.is_safe(good)  # topluluk denetimi aynı kuraldan


def test_chain_template_shared_and_community_saved(tmpdb, gemini, monkeypatch):
    from app import templates

    shared = []
    monkeypatch.setattr(templates, "share_async", shared.append)
    ai = {**market_ai(FIRST, _day(5)), "merchant": "Opet", "legal_name": None}
    text = market(FIRST, _day(5)).replace("ÖRNEKZİNCİR MARKETCİLİK A.Ş.", "ÖRNEK OTOYOL A.Ş.\nOPET")
    gemini["replies"].append(ai)
    _read(text)
    assert len(shared) == 1 and shared[0]["brand"] == "Opet" and rt.shareable(shared[0])
    assert shared[0]["sig"]["words"] == [rt._h("opet", 12)]  # zincirin sabit anahtar kelimesi, belgeden değil
    # başka sunucu (topluluktan gelen): kendi tablosuna kaydedilir, yerel olanı ezmez
    with system.connect() as c:
        c.execute("DELETE FROM receipt_templates")
    assert rt.save(shared[0], "community") and _rows()[0]["source"] == "community"
    assert not rt.save({**shared[0], "brand": None}, "community")


def test_several_layouts_per_signature(tmpdb, gemini):
    gemini["replies"].append(market_ai(FIRST, _day(5)))
    _read(market(FIRST, _day(5)))
    # aynı zincirin başka şubesi: miktar satırı altta (başka yazar kasa) → ilk şablon tutmaz, yapay zekâ yeni düzen öğretir
    gemini["replies"].append(market_ai(SECOND, _day(3)))
    assert _read(market(SECOND, _day(3), dot=True))["source"] == "yapay_zeka"
    rows = _rows()
    assert len(rows) == 2 and [json.loads(r["spec"])["num"] for r in rows] == ["tr", "dot"]
    # iki düzen de okunur
    assert _read(market(SECOND, _day(2)))["source"] == "sablon"
    assert _read(market(SECOND, _day(2), dot=True))["source"] == "sablon" and gemini["calls"] == 2
    # imza başına en çok MAX_PER_SIG düzen; en az kullanılan düşer
    spec = json.loads(rows[0]["spec"])
    for n in range(rt.MAX_PER_SIG + 2):
        rt.save({**spec, "total": [{"label": "toplam", "off": 0, "occ": n % 4}], "single": n >= 4})
    assert len([r for r in _rows() if r["sig"] == rows[0]["sig"]]) == rt.MAX_PER_SIG
    assert {r["id"] for r in _rows()} >= {rows[0]["id"], rows[1]["id"]}  # kullanılanlar kaldı


def test_pos_slip_single_item_for_fuel(tmpdb, gemini):
    def slip(amount: float, day: date) -> str:
        return "\n".join(["ÖRNEKPET AKARYAKIT", "DENEME MAH. YOL CAD. NO:5", f"{day.strftime('%d/%m/%Y')}-16:40",
                          "AID : A0000000000000", "K.N. :************1234", "EMV SATIŞ TUTARI", f"{_money(amount)} TL",
                          "TEMASSIZ"])

    ai = {"doc_type": "fis", "merchant": "Örnekpet Akaryakıt", "date": _day(4).isoformat(), "total": 1600.34,
          "payment": "kart", "category": "Ulaşım & Araç", "confidence": "yüksek",
          "items": [{"name": "Akaryakıt", "amount": 1600.34}]}
    gemini["replies"].append(ai)
    _read(slip(1600.34, _day(4)))
    spec = json.loads(_rows()[0]["spec"])
    assert spec["single"] and "vkn" not in spec["sig"] and spec["total"][0] == {"label": "emv satis tutari", "off": 1, "occ": 0}
    r = _read(slip(2850.9, _day(1)))
    assert gemini["calls"] == 1 and r["source"] == "sablon" and r["total"] == 2850.9
    assert [(i["name"], i["amount"]) for i in r["items"]] == [("Akaryakıt", 2850.9)] and r["card_last4"] == "1234"


def test_vkn_of_variants():
    for line in (f"DENEME VD: {VKN}", f"DENEME V.D. {VKN}", f"VKN/TCKN: {VKN}", f"DENEME V.D V.NO:{VKN}",
                 f"Büyük Mükellefler VDM {VKN}", f"{VKN}\nDENEME KURUMLAR VD"):
        assert rt.vkn_of("ÖRNEK A.Ş.\n" + line) == VKN, line
    assert rt.vkn_of("TEL: 0212 000 00 00\n12345678901") is None  # TCKN / telefon değil


def test_api_template_read_charges_and_no_ai_cost(hosted, gemini):  # noqa: F811
    c, u = _user(hosted)

    def parse(text, color):
        return c.post("/api/documents/parse", files=[("file", ("f.jpg", _photo(color), "image/jpeg"))],
                      data={"ocr_text": text, "ocr_source": "vision"}).json()

    gemini["replies"].append(market_ai(FIRST, _day(5)))
    assert parse(market(FIRST, _day(5)), "white")["source"] == "yapay_zeka"
    with system.connect() as cx:
        calls = cx.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0]
    r = parse(market(SECOND, _day(1)), "gray")
    assert r["source"] == "sablon" and r["ocr"]["used"] and r["check"]["ok"] and "reread" not in r
    assert gemini["calls"] == 1 and _used(u["id"]) == 2  # okuma hakkı düşer
    with system.connect() as cx:
        assert cx.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0] == calls  # maliyet kaydı yok
    a = hosted.get("/api/admin/receipt-templates").json()
    assert a["total"] == 1 and a["templates"][0]["uses"] == 1 and a["templates"][0]["signature"] == "VKN"
    assert VKN not in json.dumps(a) and "rnekzincir" not in json.dumps(a)
    assert c.get("/api/admin/receipt-templates").status_code == 403
