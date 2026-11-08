"""Tanıtım filmi belgeleri (docs/film, scripts/make_film_files.py): her dosya uygulamanın kendi yapay zekâsız
okuyucularıyla okunuyor mu, belgedeki sayılar üreticinin verisiyle aynı mı, fişteki silik rakam "6 → 8" önerisini
gerçekten tetikliyor mu? Yapay zekâ gerektiren adımlar (fiş fotoğrafının okunması) burada çalışmaz: atlanır.

Belgeler --today'siz (varsayılan TODAY) üretilmiş olmalı; başka günle yeniden üretildiyse bu testler hatırlatır."""
import importlib.util
import re
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILM = ROOT / "docs" / "film"
_spec = importlib.util.spec_from_file_location("make_film_files", ROOT / "scripts" / "make_film_files.py")
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)
T = mk.TODAY
STATEMENTS = mk.statements(T)


def _pdf(name: str) -> bytes:
    return (FILM / name).read_bytes()


def _text(name: str) -> str:
    from app.parsers import extract_text

    return extract_text(_pdf(name))


def _stmt_name(i: int) -> str:
    return f"ekstre-{i}-ornek-bank-{STATEMENTS[i - 1]['cut']:%Y-%m}.pdf"


def test_file_set_is_complete_and_fictional():
    names = set(mk.files(T))
    assert names == {p.name for p in FILM.iterdir() if p.suffix in (".pdf", ".jpg")}
    for n in names:
        if n.endswith(".pdf"):
            t = _text(n)
            assert "Kurgu belge" in t
            assert not re.search(r"(?<![\d*])\d{16}(?!\d)", t.replace(" ", ""))  # tam kart numarası yok
            assert not re.search(r"\bTR(?!00 0000 0000 0000 0000 0000 0\d)\d{2}[\d ]{20,}", t)  # yalnız TR00 0000… IBAN
            assert set(re.findall(r"\b(\w+) ÖRNEK\b", t)) & {"AYŞE", "MEHMET"}  # kişiler yalnız Ayşe / Mehmet Örnek
    # banka adı tanıyıcıları belgelerde gerçek bir banka görmüyor (ör. "Bonus" Garanti, "Emlak" Emlak Katılım sayılır)
    from app import card_reader, templates

    for n in names:
        if n.endswith(".pdf"):
            assert card_reader.bank_of(_text(n)) == "" and templates.bank_of(_text(n)) == "", n


# ---------------------------------------------------------------- kart ekstreleri: genel okuyucu (şablon), yapay zekâsız

@pytest.mark.parametrize("i", [1, 2, 3])
def test_card_statement_generic_reader(i):
    from app.mailbox import classify
    from app.parsers import parse_statement
    from app.parsers.base import FEE, PAYMENT, REFUND

    exp = STATEMENTS[i - 1]
    assert classify(_text(_stmt_name(i))) == ("card", True)
    st = parse_statement(_pdf(_stmt_name(i)), allow_ai=False)
    assert st.source == "sablon" and not st.ai_parsed  # hazır okuyucu yok: denklem tutan genel kural
    assert st.bank == "Kredi kartı"  # "Örnek Bank" bilinen bir banka değil (card_reader.BANK_HINTS): genel ad
    assert st.card_no == "5555***2222"
    assert (st.period_end, st.due_date) == (exp["cut"], exp["due"])
    assert (st.next_period_end, st.next_due_date) == (exp["next_cut"], exp["next_due"])
    assert st.total_debt == exp["total"] and st.min_payment == exp["min"]
    assert st.previous_balance == exp["prev"] and st.payments == exp["paid"]
    assert st.credit_limit == mk.CARD_LIMIT and st.available_limit == exp["available"]
    assert len(st.transactions) == len(exp["rows"])
    assert abs(st.reconciliation_diff()) < 0.01
    by = {t.description: t for t in st.transactions}
    tv = next(t for d, t in by.items() if "TELEVİZYON" in d)
    assert (tv.installment_no, tv.installments_left, tv.amount) == (i, 6 - i, 2250.0)
    assert sum(t.amount for t in st.transactions if t.txn_type == PAYMENT) == -exp["paid"]
    if i == 1:
        assert [t.description for t in st.transactions if t.txn_type == FEE] == ["YILLIK KART ÜCRETİ"]
    if i == 2:
        assert [t.amount for t in st.transactions if t.txn_type == REFUND] == [-450.0]
        assert by["ÖRNEK MOBİLYA KOLTUK TAKSİT 1/3"].installments_left == 2
    if i == 3:  # kısmi ödeme → akdi faiz + BSMV + KKDF; MARKET A fişinin kart satırı
        fees = {t.description: t.amount for t in st.transactions if t.txn_type == FEE}
        assert set(fees) == {"AKDİ FAİZ", "BSMV", "KKDF"}
        assert fees["BSMV"] == fees["KKDF"] == round(fees["AKDİ FAİZ"] * 0.15, 2)
        assert st.interest_fees == round(sum(fees.values()), 2)
        assert by["MARKET A İSTANBUL"].amount == mk.receipt_total(mk.receipt_items(T, "a"))


def test_card_statements_upload_and_receipt_match(client):
    """Üç ekstre arka arkaya yüklenir (hepsi kaydedilir, yapay zekâ yok); kartla ödenmiş MARKET A fişi son ekstredeki
    satırla eşleşir; aynı dosya ikinci kez "duplicate"."""
    rd = mk.receipt_date(T, "a")
    total = mk.receipt_total(mk.receipt_items(T, "a"))
    r = client.post("/api/entries", json={"entry_date": rd.isoformat(), "description": "MARKET A", "amount": total,
                                          "kind": "gider", "category": "Market & Gıda", "payment": "kredi_karti",
                                          "card_last4": mk.CARD_LAST4})
    assert r.status_code == 200, r.text
    files = [("files", (_stmt_name(i), _pdf(_stmt_name(i)), "application/pdf")) for i in (1, 2, 3)]
    res = client.post("/api/statements/upload", files=files).json()
    assert [x["status"] for x in res] == ["saved"] * 3
    assert [x["source"] for x in res] == ["sablon"] * 3 and not any(x["ai"] for x in res)
    assert sum(x.get("linked") or 0 for x in res) == 1
    again = client.post("/api/statements/upload", files=[files[2]]).json()
    assert again[0]["status"] == "duplicate"


# ---------------------------------------------------------------- hesap dökümü: öğrenilen şablon (bakiye zinciri)

def test_account_statement_rule_reader(client):
    from app import bank_pdf
    from app.mailbox import classify

    name = "hesap-dokumu-ornek-bank.pdf"
    assert classify(_text(name)) == ("account", True)
    res = bank_pdf.parse_account_pdf(_pdf(name), allow_ai=False)
    rows = mk.account_rows(T)[3]
    assert res["verified"] and res.get("template")
    assert res["owner_name"] == "AYŞE ÖRNEK" and res["iban"] == mk.IBAN_OWNER.replace(" ", "")
    assert res["bank"] == ""  # bilinmeyen banka: başlık metni banka adı olmaz (templates.bank_of)
    assert [(r["date"], r["amount"]) for r in res["rows"]] == [(d.isoformat(), a) for d, _, a in rows]
    assert res["rows"][-1]["balance"] == mk.CLOSING  # film_seed "Örnek Bank" hesabının bakiyesi
    assert any("MAAŞ" in r["description"] and r["amount"] == 58_000 for r in res["rows"])
    card_pay = next(r for r in res["rows"] if "2222" in r["description"])
    assert card_pay["amount"] == -STATEMENTS[-1]["paid"] and card_pay["date"] == next(
        d for d, t, a in STATEMENTS[-1]["rows"] if "ÖDEME" in t).isoformat()
    # arayüzün önizlemesi: yükleme "hesap dökümü" der, önizleme satırları okur (yapay zekâ yok)
    up = client.post("/api/statements/upload", files=[("files", (name, _pdf(name), "application/pdf"))]).json()
    assert up[0]["status"] == "account_statement"
    pv = client.post("/api/bank-import/preview", files={"file": (name, _pdf(name), "application/pdf")}).json()
    assert pv["pdf"] and pv["total"] == len(rows)


# ---------------------------------------------------------------- dekont: etiket okuması

def test_dekont_rule_reader(client):
    from app import dekont, dekont_parse
    from app.mailbox import classify

    name = "dekont-ornek-bank-eft.pdf"
    t = _text(name)
    assert classify(t) == ("dekont", True) and dekont_parse.is_transfer(t)
    day, _, amount = mk.rent_row(T)
    f = dekont_parse.fields_from_text(t)
    assert (f["date"], f["amount"], f["transfer_type"]) == (day.isoformat(), -amount, "eft")
    assert f["sender_name"] == "AYŞE ÖRNEK" and f["receiver_name"] == "ÖRNEK KONUT YÖNETİM LTD. ŞTİ."
    assert f["receiver_bank"] == "MAVİ BANK" and f["bank"] == "" and "KİRA" in f["description"]
    assert dekont.parse(t)["kind"] == "gider"
    r = client.post("/api/dekont/parse", files={"file": (name, _pdf(name), "application/pdf")}).json()
    assert r["source"] == "metin" and r["amount"] == -amount and r["parties"]["receiver"]["iban_last4"] == "0002"


# ---------------------------------------------------------------- fatura: yerel okuma (canlıda yapay zekâ okur)

def test_bill_local_reader(client):
    from app import ocr

    b = mk.bill(T)
    assert b["total"] == 1_120.40 and b["due"] == date(2026, 11, 16)  # film_seed: 1.120,40, bugün + 6
    doc = ocr.parse(_pdf("fatura-ornek-elektrik.pdf"), "application/pdf", ["Faturalar", "Diğer"])
    assert doc["type"] == "bill" and doc["amount"] == b["total"] and doc["due_date"] == b["due"].isoformat()
    assert doc["provider"].startswith("ÖRNEK ELEKTRİK")
    r = client.post("/api/documents/parse", files={"file": ("fatura.pdf", _pdf("fatura-ornek-elektrik.pdf"),
                                                            "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "bill" and r.json()["amount"] == b["total"]


# ---------------------------------------------------------------- fiş: silik rakam → "6 ile 8 karışmış olabilir"

def _ai_items(key: str) -> list[dict]:
    """Yapay zekânın silik 8'i 6 okuduğu kalemler (adetli kalemde adet ve birim fiyat da gelir)."""
    out = []
    for i, it in enumerate(mk.receipt_items(T, key)):
        x = {"name": it["name"], "qty": it["qty"], "unit": it["unit"], "amount": it["amount"],
             "unit_price": it["unit_price"] if it["qty"] != 1 else None}
        if i == mk.RECEIPTS[key]["faded"]:
            x["amount"] = mk.misread(x["amount"])
        out.append(x)
    return out


def test_receipt_text_reads_without_ai():
    """Fişin metni (telefon metni benzetimi) yerel okuyucuyla: toplam, tarih, VKN, KDV dökümü, kalemler."""
    from app import ocr, receipt_templates, vat

    for key in ("a", "b"):
        items = mk.receipt_items(T, key)
        text = mk.receipt_text(T, key)
        doc = ocr.parse_text(text, ["Market & Gıda", "Diğer"])
        assert doc["total"] == mk.receipt_total(items) and doc["date"] == mk.receipt_date(T, key).isoformat()
        assert doc["tax_no"] == mk.VKN and receipt_templates.vkn_of(text) == mk.VKN
        assert doc["vat_lines"] == [{"rate": r, "amount": v} for r, v in mk.receipt_vat(items).items()]
        assert [i["amount"] for i in doc["items"]] == [i["amount"] for i in items]
    assert vat.vkn(mk.VKN) == mk.VKN  # kontrol hanesi tutuyor: fişin VKN'si okunur ve saklanır


def test_digit_fix_single_line_receipt():
    """fis-market-a.jpg: 82,95 → 62,95 okunursa kalemler ₺20 eksik; tek satırlık çözüm deterjan 6→8."""
    from app import receipt_check

    items = _ai_items("a")
    total = mk.receipt_total(mk.receipt_items(T, "a"))
    chk = receipt_check.check(items, total)
    assert chk["diff"] == 20.0 and not chk["ok"]
    assert [(f["index"], f["from"], f["to"], f["digits"]) for f in chk["digit_fix"]] == [(4, 62.95, 82.95, "6→8")]
    assert chk["residual"] == 0
    # telefon metni silik rakamı 8 okuduysa: tanık "ocr", KDV grubu da (yalnız %20 grubu tutmuyor) aynı satırı gösterir
    vl = [{"rate": r, "amount": v} for r, v in mk.receipt_vat(mk.receipt_items(T, "a")).items()]
    receipt_check.add_evidence(chk, items, mk.receipt_text(T, "a"), vl)
    assert chk["digit_fix"][0]["to"] == 82.95 and set(chk["digit_fix"][0]["evidence"]) == {"ocr", "group"}


def test_digit_fix_single_line_when_phone_text_reads_the_same_six():
    """fis-market-a.jpg telefonla: Apple Vision de silik 8'i 6 okur (62,95), her satır metinde okunanla aynı. Eskiden
    bütün satırlar "doğrulanmış" sayılıp öneri çıkmazdı; şimdi tanıksız satırda çözüm yoksa metnin doğruladığı
    satırlarda da tek rakam aranır: deterjan 6→8 (güven düşük, ocr_agrees), fiş tutar."""
    from app import receipt_check

    items = _ai_items("a")
    real = mk.receipt_items(T, "a")
    total = mk.receipt_total(real)
    text = mk.receipt_text(T, "a", faded_as="6")
    assert "*62,95" in text
    vl = [{"rate": r, "amount": v} for r, v in mk.receipt_vat(real).items()]
    for kw in ({}, {"vat_lines": vl}):
        chk = receipt_check.check(items, total)
        receipt_check.add_evidence(chk, items, text, kw.get("vat_lines"))
        fix = chk["digit_fix"]
        assert [(f["index"], f["from"], f["to"], f["digits"]) for f in fix] == [(4, 62.95, 82.95, "6→8")]
        assert fix[0]["ocr_agrees"] is True and "ocr" not in fix[0]["evidence"]
        assert chk["residual"] == 0 and chk["suspect"] == []
    # fişin kendisi tutuyorsa (yapay zekâ 8 okudu, metin 6 okudu) öneri yok
    ok = [{**x, "amount": r["amount"]} for x, r in zip(items, real)]
    chk = receipt_check.check(ok, total)
    receipt_check.add_evidence(chk, ok, text, vl)
    assert chk["ok"] and chk["digit_fix"] == []


def test_digit_fix_quantity_receipt_survives_phone_text():
    """fis-market-a-adetli.jpg: "4 AD X 20,50" satırının tutarı 82,00 → 62,00 okunursa adet × birim tanığı ("qty")
    öneriyi verir — telefon metni de 62,00 okusa bile (Apple Vision bu fotoğrafta 62,00 okuyor)."""
    from app import receipt_check

    items = _ai_items("b")
    total = mk.receipt_total(mk.receipt_items(T, "b"))
    for text in (None, mk.receipt_text(T, "b", faded_as="6")):
        chk = receipt_check.check(items, total)
        receipt_check.add_evidence(chk, items, text, [{"rate": 1, "amount": sum(mk.receipt_vat(mk.receipt_items(T, "b")).values())}])
        fix = chk["digit_fix"]
        assert [(f["index"], f["from"], f["to"], f["digits"]) for f in fix] == [(1, 62.0, 82.0, "6→8")]
        assert "qty" in fix[0]["evidence"] and chk["residual"] == 0


@pytest.mark.skip(reason="yapay zekâ (Gemini) gerekir: fotoğrafın okunması ve silik 8'in 6 okunması canlıda denenir; "
                         "kural tarafı test_digit_fix_* testlerinde")
def test_receipt_photo_ai_reads_faded_digit_as_six():
    pass
