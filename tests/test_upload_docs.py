"""Yükleme kuyruğuna gelen ekstre olmayan belge (yetenek upload_docs): fatura PDF'i ve fiş/fatura fotoğrafı belge okuma
yolunda okunur; fotoğraf PDF ayrıştırıcıya gitmez; kütüphanenin İngilizce hatası kullanıcıya sızmaz. Veriler uydurmadır
(operatör, numara, tutar)."""
import json

import httpx
import pytest

from tests import test_plans
from tests.test_doc_reads import _jpeg, _set_reads
from tests.test_plans import _user
from tests.test_read_quota_audit import _daily

hosted = test_plans.hosted


def text_pdf(lines: list[str]) -> bytes:
    """Metin katmanlı tek sayfalık geçerli PDF (Helvetica; ASCII metin)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    body = "BT /F1 10 Tf 40 800 Td 14 TL " + " ".join(f"({esc(x)}) Tj T*" for x in lines) + " ET"
    stream = body.encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return bytes(out)


GSM_LINES = [
    "ORNEKCELL ILETISIM HIZMETLERI A.S.",
    "Mobil Hat Faturasi",
    "Fatura No: ORN2026000012345",
    "Fatura Tarihi: 01.11.2026",
    "Fatura Donemi: 01.10.2026 - 31.10.2026",
    "ETTN: 00000000-1111-2222-3333-444444444444",
    "Hizmet Numarasi: 0500 000 00 00",
    "Son Odeme Tarihi: 20.11.2026",
    "Paket Ucreti 249,90",
    "KDV 49,98",
    "Odenecek Tutar: 299,88 TL",
]
# fatura işareti zayıf (başlıkta tek işaret): önce kart ekstresi denenir, yapay zekâ ekstre bulamayınca belge olarak okunur
WEAK_BILL_LINES = ["ORNEK SU IDARESI", "Su Faturasi", "Tuketim 12 m3", "Toplam 184,20 TL", "Son Odeme Tarihi 25.11.2026"]
UNRELATED_LINES = ["KIRA SOZLESMESI", "Taraflar asagida imzasi bulunan kiraya veren ve kiraci",
                   "Madde 1 - Konu", "Kiralanan tasinmazin kullanimi ile ilgili hukumler", "Son Odeme Tarihi her ayin 5'i"]

BILL = {"doc_type": "fatura", "merchant": "Ornekcell", "bill_type": "telefon", "total": 299.88, "due_date": "2026-11-20",
        "period": "Ekim 2026", "confidence": "yüksek"}
RECEIPT = {"doc_type": "fis", "merchant": "Ornek Market", "date": "2026-11-10", "total": 87.5,
           "category": "Market & Gıda", "items": [], "confidence": "yüksek"}
NOT_STATEMENT = {"bank": None, "card_no": None, "period_end": None, "transactions": []}


@pytest.fixture()
def ai(monkeypatch):
    """Sahte Gemini: kart ekstresi istemine ekstre bulunamadı (kesim tarihi yok), belge istemine PDF'te fatura,
    fotoğrafta fiş döner. Her çağrı türüyle kaydedilir."""
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls: list[str] = []

    def fake_post(url, json=None, timeout=None, headers=None):
        blob = _dump(json)
        if "KREDİ KARTI HESAP ÖZETİ" in blob:
            calls.append("ekstre")
            out = NOT_STATEMENT
        else:
            calls.append("belge")
            out = BILL if "application/pdf" in blob else RECEIPT
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dump(out)}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", fake_post)
    return calls


def _dump(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def _up(c, name, data, mime, docs=True, **extra):
    form = {"docs": "true"} if docs else {}
    r = c.post("/api/statements/upload", files=[("files", (name, data, mime))], data={**form, **extra})
    assert r.status_code == 200, r.text
    return r.json()[0]


ENGLISH = ("Root", "really a PDF", "EOF", "Traceback", "pdfminer", "pypdf", "PdfReadError")


def _clean(msg: str):
    assert msg and not any(w in msg for w in ENGLISH), msg


# ---------------------------------------------------------------- fatura PDF'i

def test_gsm_bill_pdf_reads_as_bill_one_read(hosted, ai):
    c, u = _user(hosted)
    r = _up(c, "fatura-ekim.pdf", text_pdf(GSM_LINES), "application/pdf")
    assert r["status"] == "document" and r["doc_type"] == "bill", r
    assert r["doc"]["type"] == "bill" and r["doc"]["amount"] == 299.88 and r["doc"]["due_date"] == "2026-11-20"
    assert r["doc"].get("file_name")  # fatura dosyası saklandı: Ödemeler'e eklenince bağlanır
    assert r["message"] == "Fatura okundu — onayla"
    assert ai == ["belge"]  # kart ekstresi diye yapay zekâya gitmedi
    assert _daily(u["id"]) == 1 and r.get("charged") == {"daily": 1, "extra": 0}
    assert c.get("/api/statements").json() == [] and c.get("/api/bills").json() == []  # onaysız kayıt yok


def test_bill_pdf_old_client_gets_turkish_reason_without_ai(hosted, ai):
    from app.api.main import UPLOAD_BILL_MSG

    c, u = _user(hosted)
    r = _up(c, "fatura.pdf", text_pdf(GSM_LINES), "application/pdf", docs=False)
    assert r["status"] == "error" and r["message"] == UPLOAD_BILL_MSG
    assert ai == [] and _daily(u["id"]) == 0


def test_weak_bill_falls_back_after_statement_attempt_without_double_charge(hosted, ai):
    c, u = _user(hosted)
    r = _up(c, "su.pdf", text_pdf(WEAK_BILL_LINES), "application/pdf")
    assert r["status"] == "document" and r["doc_type"] == "bill", r
    assert ai == ["ekstre", "belge"]
    assert _daily(u["id"]) == 1  # ekstre denemesi hak düşürmedi, belge okuması 1


def test_unrelated_pdf_keeps_clear_error_and_no_second_ai_call(hosted, ai):
    c, u = _user(hosted)
    r = _up(c, "sozlesme.pdf", text_pdf(UNRELATED_LINES), "application/pdf")
    assert r["status"] == "error" and "kredi kartı ekstresi bulunamadı" in r["message"]
    assert ai == ["ekstre"] and _daily(u["id"]) == 0


def test_forced_card_does_not_turn_into_document(hosted, ai):
    c, _ = _user(hosted)
    r = _up(c, "su.pdf", text_pdf(WEAK_BILL_LINES), "application/pdf", kind="card")
    assert r["status"] == "error" and ai == ["ekstre"]


def test_bill_quota_full_is_reported_on_the_row(hosted, ai):
    c, u = _user(hosted)
    _set_reads(u["id"], day=5)  # ücretsiz: ayda 5, doldu
    r = _up(c, "fatura.pdf", text_pdf(GSM_LINES), "application/pdf")
    assert r["status"] == "error" and r.get("quota_full") and ai == []


# ---------------------------------------------------------------- fotoğraf

def test_jpg_never_reaches_pdf_parser(hosted, ai, monkeypatch):
    import app.parsers as parsers
    from app.api.main import UPLOAD_IMAGE_MSG

    seen = []
    real = parsers.extract_text
    monkeypatch.setattr(parsers, "extract_text", lambda d, pw=None: seen.append(1) or real(d, pw))
    c, u = _user(hosted)
    r = _up(c, "Screenshot_20261112.jpg", _jpeg(), "image/jpeg", docs=False)
    assert r["status"] == "error" and r["message"] == UPLOAD_IMAGE_MSG
    # istemci türü bildirmese de (application/octet-stream) içerikten fotoğraf anlaşılır
    r = _up(c, "ekran", _jpeg(), "application/octet-stream", docs=False)
    assert r["message"] == UPLOAD_IMAGE_MSG
    assert seen == [] and ai == [] and _daily(u["id"]) == 0


def test_jpg_with_docs_reads_as_receipt(hosted, ai):
    c, u = _user(hosted)
    r = _up(c, "fis.jpg", _jpeg(), "image/jpeg")
    assert r["status"] == "document" and r["doc_type"] == "receipt" and r["doc"]["total"] == 87.5, r
    assert r["message"] == "Fiş okundu — onayla" and r["doc"].get("receipt_file")
    assert ai == ["belge"] and _daily(u["id"]) == 1


def test_unsupported_image_type_is_explained(hosted, ai):
    from app.api.main import UPLOAD_IMAGE_TYPE_MSG

    c, _ = _user(hosted)
    r = _up(c, "resim.gif", b"GIF89a" + b"\x00" * 40, "image/gif")
    assert r["status"] == "error" and r["message"] == UPLOAD_IMAGE_TYPE_MSG and ai == []


# ---------------------------------------------------------------- ham kütüphane hatası

def test_broken_pdf_error_is_turkish(client):
    from app.parsers import PDF_UNREADABLE

    r = _up(client, "ekstre.pdf", b"bu bir PDF degil, bozuk indirme", "application/pdf")
    assert r["status"] == "error" and r["message"] == PDF_UNREADABLE
    _clean(r["message"])


def test_library_text_is_not_user_text():
    from app import errors

    assert not errors.is_user_text("PDF açılamadı: No /Root object! - Is this really a PDF?")
    assert "Root" not in errors.user_text(ValueError("PDF açılamadı: No /Root object! - Is this really a PDF?"))


# ---------------------------------------------------------------- sınıflandırma

def test_looks_like_bill_is_conservative():
    from app import mailbox
    from tests.conftest import FIXTURES, STATEMENTS
    from tests.test_statement_kind import VB_TEXT

    assert mailbox.looks_like_bill("\n".join(GSM_LINES))
    assert not mailbox.looks_like_bill("\n".join(WEAK_BILL_LINES))  # tek işaret: önce ekstre denenir
    for name in STATEMENTS:  # kart ekstreleri asla fatura sayılmaz
        assert not mailbox.looks_like_bill((FIXTURES / name).read_text(encoding="utf-8")), name
    assert not mailbox.looks_like_bill(VB_TEXT)
    # hesap dökümünde fatura ödemesi satırları (abone no, fatura no) başlıkta değil: döküm kalır
    rows = "\n".join(f"0{i}.10.2026 10:00 ORNEKCELL FATURA ODEME Abone No 12345 Fatura No X{i} -100,00" for i in range(1, 9))
    assert not mailbox.looks_like_bill("VADESİZ HESAP HAREKETLERİ\nIBAN TR00\n" + rows)
    # kart ekstresinde fatura talimatı satırı: kart işareti var, fatura değil
    assert not mailbox.looks_like_bill("Fatura No 1 Fatura Tarihi 2 Hesap Kesim Tarihi 15.10.2026 Asgari Ödeme 50,00")
    assert mailbox.mentions_bill("\n".join(WEAK_BILL_LINES))
    assert not mailbox.mentions_bill("\n".join(UNRELATED_LINES))


def test_capability():
    from app import version

    assert "upload_docs" in version.CAPABILITIES
