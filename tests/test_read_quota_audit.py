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
    _set_reads(u["id"], day=2)  # ücretsiz: 3 haktan 1 kaldı
    pdf = make_pdf(30)  # 30 sayfa = 3 hak
    r = c.post("/api/documents/parse", files=[("file", ("fatura.pdf", pdf, "application/pdf"))])
    assert r.status_code == 502 and r.json().get("needed") == 3
    # aynı PDF + küçük bir fotoğraf: eskiden anahtar özet baytı olduğundan 1 hak sayılıyordu
    r = c.post("/api/documents/parse", files=[("file", ("fatura.pdf", pdf, "application/pdf")),
                                              ("file", ("x.jpg", _jpeg(), "image/jpeg"))])
    assert r.status_code == 502 and r.json().get("needed") == 3
    assert gemini == [] and _daily(u["id"]) == 2


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
    _set_reads(u["id"], day=2)
    r = c.post("/api/documents/parse", files=[("file", ("fatura.pdf", padded, "application/pdf"))])
    assert r.status_code == 502 and r.json().get("needed") == 3
    assert gemini == [] and _daily(u["id"]) == 2
