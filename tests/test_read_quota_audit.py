"""Okuma hakkı delikleri (güvenlik denetimi, hazır sürüm): yapay zekâ çağrıldıysa hak düşer. Veriler uydurmadır."""
from tests import test_plans
from tests.test_doc_reads import _jpeg, _set_reads, gemini  # noqa: F401
from tests.test_plans import _user

hosted = test_plans.hosted


def make_pdf(n_pages: int) -> bytes:
    """El yapımı geçerli PDF (xref doğru), n boş sayfa."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{3 + i} 0 R' for i in range(n_pages))}] /Count {n_pages} >>".encode()]
    objs += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"] * n_pages
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


def _daily(uid):
    from app import system

    with system.connect() as c:
        r = c.execute("SELECT SUM(count) FROM read_daily WHERE user_id = ?", (uid,)).fetchone()
    return int(r[0] or 0)


def test_multi_file_document_weight_is_sum_of_files(hosted, gemini):  # noqa: F811
    c, u = _user(hosted)
    _set_reads(u["id"], day=4)  # ücretsiz: ayda 5 haktan 1 kaldı
    pdf = make_pdf(30)  # 30 sayfa = 3 hak
    r = c.post("/api/documents/parse", files=[("file", ("fatura.pdf", pdf, "application/pdf"))])
    assert r.status_code == 502 and r.json().get("needed") == 3
    # aynı PDF + küçük bir fotoğraf: eskiden anahtar özet baytı olduğundan 1 hak sayılıyordu
    r = c.post("/api/documents/parse", files=[("file", ("fatura.pdf", pdf, "application/pdf")),
                                              ("file", ("x.jpg", _jpeg(), "image/jpeg"))])
    assert r.status_code == 502 and r.json().get("needed") == 3
    assert gemini == [] and _daily(u["id"]) == 4


def test_multi_photo_receipt_still_one_read(hosted, gemini):  # noqa: F811
    c, u = _user(hosted)
    parts = [("file", (f"p{i}.jpg", f"parca-{i}".encode(), "image/jpeg")) for i in range(3)]
    assert c.post("/api/documents/parse", files=parts).status_code == 200
    assert _daily(u["id"]) == 1


def test_pdf_header_after_1024_bytes_still_counts_pages(hosted, gemini):  # noqa: F811
    from app import doc_reads

    padded = b" " * 2000 + make_pdf(30)
    assert doc_reads.pages(padded) == 30 and doc_reads.weight(padded) == 3
    c, u = _user(hosted)
    _set_reads(u["id"], day=4)
    r = c.post("/api/documents/parse", files=[("file", ("fatura.pdf", padded, "application/pdf"))])
    assert r.status_code == 502 and r.json().get("needed") == 3
    assert gemini == [] and _daily(u["id"]) == 4


# ---------------------------------------------------------------- yapay zekâ çağrıldıysa hak düşer

def test_share_uses_cache_and_counts_like_the_app(hosted, gemini):  # noqa: F811
    """Paylaş ucu: aynı içerik önbellekten (yapay zekâ yok, hak yok); eskiden her paylaşım yapay zekâya gidiyordu."""
    c, u = _user(hosted)
    img = _jpeg()
    assert c.post("/api/documents/parse", files=[("file", ("f.jpg", img, "image/jpeg"))]).status_code == 200
    assert _daily(u["id"]) == 1 and len(gemini) == 1
    for _ in range(3):
        r = c.post("/api/share", files=[("file", ("f.jpg", img, "image/jpeg"))], headers={"accept": "application/json"})
        assert r.status_code == 200, r.text
    assert len(gemini) == 1 and _daily(u["id"]) == 1
    assert len(c.get("/api/inbox").json()) == 3  # yine de gelen kutusuna düşer


def test_share_new_content_counts_and_cache_expiry_recounts(hosted, gemini):  # noqa: F811
    from app import system

    c, u = _user(hosted)
    r = c.post("/api/share", files=[("file", ("g.jpg", b"paylas-1", "image/jpeg"))], headers={"accept": "application/json"})
    assert r.status_code == 200 and len(gemini) == 1 and _daily(u["id"]) == 1
    with system.connect() as s:  # önbellek süresi doldu
        s.execute("DELETE FROM doc_cache")
    c.post("/api/share", files=[("file", ("g.jpg", b"paylas-1", "image/jpeg"))], headers={"accept": "application/json"})
    assert len(gemini) == 2 and _daily(u["id"]) == 2  # yapay zekâ yeniden çağrıldı: yeniden sayıldı


def test_share_quota_full_is_refused_before_ai(hosted, gemini):  # noqa: F811
    c, u = _user(hosted)
    _set_reads(u["id"], day=5)
    r = c.post("/api/share", files=[("file", ("h.jpg", b"paylas-2", "image/jpeg"))], headers={"accept": "application/json"})
    assert r.status_code == 200 and gemini == [] and c.get("/api/inbox").json() == []


def test_duplicate_statement_reread_by_ai_is_counted(hosted, monkeypatch):
    """Zaten var olan ekstre, önbellek süresi dolmuşken yeniden yüklendi: yapay zekâ çağrıldıysa hak düşer."""
    from app import parsers, system
    from app.parsers import ai
    from tests.conftest import fixture_bytes

    st = parsers.parse_text(parsers.extract_text(fixture_bytes("kuveytturk.txt")))
    monkeypatch.setattr(parsers, "parse_text", lambda text: None)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls = []
    monkeypatch.setattr(ai, "parse_pdf_with_ai", lambda data, mime="application/pdf": calls.append(1) or st)
    c, u = _user(hosted)
    files = [("files", ("k.pdf", fixture_bytes("kuveytturk.txt"), "application/pdf"))]
    assert c.post("/api/statements/upload", files=files).json()[0]["status"] == "saved" and _daily(u["id"]) == 1
    r = c.post("/api/statements/upload", files=files).json()[0]  # önbellekten: bedava
    assert r["status"] == "duplicate" and r.get("cached") and calls == [1] and _daily(u["id"]) == 1
    with system.connect() as s:
        s.execute("DELETE FROM doc_cache")
    r = c.post("/api/statements/upload", files=files).json()[0]
    assert r["status"] == "duplicate" and calls == [1, 1]
    assert _daily(u["id"]) == 2 and r["charged"] == {"daily": 1, "extra": 0}


# ---------------------------------------------------------------- kontrol ile sayım arası yarış

def test_concurrent_request_sees_reserved_read(hosted):
    """Kalan 1 hak: ilk isteğin kontrolü hakkı ayırır, eşzamanlı ikinci istek (ayrı scope) reddedilir; ilk istek
    bitince (sayılmadan) ayırma silinir."""
    import pytest

    from app import db, doc_reads, system

    _, u = _user(hosted)
    _set_reads(u["id"], day=4)
    u = system.user(u["id"])
    with db.use_space(system.user_db_path(u), u):
        with doc_reads.scope():
            doc_reads.check(b"istek-a")
            doc_reads.check(b"istek-a")  # aynı istekte aynı belgeye ikinci kontrol (ör. iç işleyici) kendini saymaz
            with doc_reads.scope(), pytest.raises(doc_reads.QuotaError):
                doc_reads.check(b"istek-b")
        with doc_reads.scope():  # ilk istek sayılmadan bitti (hata / zaten vardı): hak geri açık
            doc_reads.check(b"istek-b")
            assert doc_reads.charge(b"istek-b") == {"daily": 1, "extra": 0}
        with system.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM read_reserve").fetchone()[0] == 0


def test_parallel_uploads_with_one_read_left_only_one_is_read(hosted, monkeypatch):
    """Uçtan uca yarış: kalan 1 hakla aynı anda iki fotoğraf. Eskiden ikisi de kontrolden geçip yapay zekâya gidiyordu."""
    import json
    import threading
    import time

    import httpx
    from fastapi.testclient import TestClient

    from app import receipt
    from app.api.main import app

    c, u = _user(hosted)
    _set_reads(u["id"], day=4)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls = []
    doc = {"doc_type": "fis", "merchant": "Ornek", "date": "2026-10-20", "total": 10.5, "items": []}

    def slow_post(url, json_=None, timeout=None, headers=None, **kw):
        calls.append(url)
        time.sleep(0.4)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(doc)}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", slow_post)
    out = []

    def go(i):
        tc = TestClient(app, cookies=c.cookies)
        r = tc.post("/api/documents/parse", files=[("file", (f"y{i}.jpg", f"yaris-{i}".encode(), "image/jpeg"))])
        out.append(r.status_code)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [200, 502] and len(calls) == 1 and _daily(u["id"]) == 5
