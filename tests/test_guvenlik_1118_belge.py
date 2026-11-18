"""Güvenlik denetimi 18.11, Y1/Y2 ve D1: görüntü ve PDF çizim bombası, demo kapalı uçlar, okuma hakkı sırası, gövde
sınırı. Görüntüler ve PDF'ler testte üretilir (uydurma, içeriksiz)."""
import io

import pytest
from PIL import Image

from tests.test_book import owner  # noqa: F401 - fixture
from tests.test_demo_mode import demo  # noqa: F401 - fixture
from tests.test_warranty import _add


def _palette_bomb(side: int = 7700) -> bytes:
    """PoC B1: tek renkli palet PNG (~57 KB), çözülünce side² piksel; RGBA'ya çevrilince ~4×side² bayt."""
    im = Image.new("P", (side, side), 0)
    im.putpalette([255, 255, 255] * 256)
    im.info["transparency"] = 0
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


@pytest.fixture(scope="module")
def bomb() -> bytes:
    return _palette_bomb()


def _pdf(w: int, h: int, pages: int = 1) -> bytes:
    """PoC B2: içeriksiz, verilen MediaBox'lı PDF (195 bayt civarı)."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objs = [b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
            f"2 0 obj<</Type/Pages/Kids[{kids}]/Count {pages}>>endobj".encode()]
    objs += [f"{3 + i} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 {w} {h}]>>endobj".encode() for i in range(pages)]
    return b"%PDF-1.4\n" + b"\n".join(objs) + b"\ntrailer<</Root 1 0 R>>\n%%EOF\n"


@pytest.fixture()
def decoded(monkeypatch):
    """Pillow'un çözdüğü (load) ve dönüştürdüğü (convert) en büyük görüntünün piksel sayısı."""
    from PIL import ImageFile

    seen = {"load": 0, "convert": 0}
    load, convert = ImageFile.ImageFile.load, Image.Image.convert

    def _load(self, *a, **kw):
        seen["load"] = max(seen["load"], self.size[0] * self.size[1])
        return load(self, *a, **kw)

    def _convert(self, *a, **kw):
        seen["convert"] = max(seen["convert"], self.size[0] * self.size[1])
        return convert(self, *a, **kw)

    monkeypatch.setattr(ImageFile.ImageFile, "load", _load)
    monkeypatch.setattr(Image.Image, "convert", _convert)
    return seen


# ---------------------------------------------------------------- Y1: görüntü bombası

def test_png_bomb_rejected_before_decode(bomb, decoded):
    from app import images

    assert len(bomb) < 100 * 1024
    with pytest.raises(images.ImageTooLarge):
        images.prepare(bomb)
    with pytest.raises(images.ImageTooLarge):
        images.check(bomb)
    assert decoded == {"load": 0, "convert": 0}  # yalnız başlık okundu: hiçbir piksel çözülmedi


def test_large_but_allowed_image_is_shrunk_before_color_conversion(decoded):
    """Sınırın altındaki büyük palet görüntü: RGBA'ya çevirmeden önce küçültülür (tam boyutlu RGBA kopyası yok)."""
    from app import images

    out = images.prepare(_palette_bomb(6000))  # 36M piksel < 40M
    assert out and max(Image.open(io.BytesIO(out)).size) == images.MAX_EDGE
    assert decoded["convert"] <= (2 * images.MAX_EDGE) ** 2  # dönüştürülen en büyük görüntü ~3200 px kenarlı


def test_big_jpeg_is_decoded_at_reduced_size(decoded):
    """48 MP sınıfı telefon fotoğrafı reddedilmez: JPEG çözücü küçültülmüş boyutta çözer (draft)."""
    from app import images

    buf = io.BytesIO()
    Image.new("RGB", (8000, 6000), (30, 120, 200)).save(buf, "JPEG", quality=60)
    out = images.prepare(buf.getvalue())
    assert out and max(Image.open(io.BytesIO(out)).size) == images.MAX_EDGE
    assert 0 < decoded["load"] <= 4000 * 3000


def test_rgb_decoded_bytes_cap():
    """Renkli görüntüde Pillow pikseli 4 bayt tutar: 40M piksel sınırının altında kalsa da ~128 MB üstü reddedilir."""
    from app import images

    assert images.MAX_DECODED_BYTES // 4 < images.MAX_PIXELS
    side = int((images.MAX_DECODED_BYTES // 4) ** 0.5) + 50
    buf = io.BytesIO()
    Image.new("RGB", (side, side)).save(buf, "PNG")
    with pytest.raises(images.ImageTooLarge):
        images.check(buf.getvalue())


def test_warranty_png_bomb_is_413(owner, bomb, decoded):  # noqa: F811
    """PoC B1: garanti belgesi ucu (ücretsiz kullanıcı, @_heavy yoktu) bombayı tam boyutta çözüyordu."""
    w = _add(owner)
    r = owner.post(f"/api/warranties/{w['id']}/files", files={"file": ("a.png", bomb, "image/png")},
                   data={"doc_type": "fatura"})
    assert r.status_code == 413 and "çok büyük" in r.json()["detail"]
    assert decoded["load"] == 0
    assert owner.get(f"/api/warranties/{w['id']}").json()["files"] == []


def test_document_and_receipt_parse_png_bomb_is_413(owner, bomb, decoded):  # noqa: F811
    for path, field in (("/api/documents/parse", "file"), ("/api/receipts/parse", "file"),
                        ("/api/dekont/parse", "file")):
        r = owner.post(path, files={field: ("a.png", bomb, "image/png")})
        assert r.status_code == 413, (path, r.status_code, r.text[:200])
    assert decoded["load"] == 0


def test_ocr_and_karekod_skip_bomb(bomb, decoded):
    from app import images, karekod, ocr

    with pytest.raises(images.ImageTooLarge):
        ocr._image_text(bomb)
    assert karekod.scan(bomb, "image/png", bomb) == []
    assert decoded["load"] == 0


def test_demo_closes_image_endpoints(demo, bomb):  # noqa: F811
    """PoC B4: oturumsuz demoda /api/dekont/parse açıktı ve bombayı çözüyordu."""
    for path in ("/api/dekont/parse", "/api/documents/classify", "/api/subscriptions/apps/screenshot"):
        r = demo.post(path, files={"file": ("d.png", bomb, "image/png")})
        assert r.status_code == 403 and "Demoda kapalı" in r.json()["detail"], path
    assert demo.get("/api/statements").status_code == 200  # demo okuma uçları açık


@pytest.mark.parametrize("path", ["/api/documents/parse", "/api/dekont/parse"])
def test_quota_checked_before_image_processing(owner, monkeypatch, path):  # noqa: F811
    """Okuma hakkı denetimi görüntü hazırlamadan (ve karekod/PDF çiziminden) önce."""
    from app import doc_reads, images

    calls = []
    check, prepare = doc_reads.check, images.prepare
    monkeypatch.setattr(doc_reads, "check", lambda *a, **kw: (calls.append("check"), check(*a, **kw))[1])
    monkeypatch.setattr(images, "prepare", lambda d: (calls.append("prepare"), prepare(d))[1])
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (250, 250, 250)).save(buf, "JPEG")
    owner.post(path, files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")})
    assert calls and calls[0] == "check", calls


# ---------------------------------------------------------------- Y2: PDF çizim bombası

@pytest.fixture()
def rendered(monkeypatch):
    import pypdfium2 as pdfium

    seen = []
    render = pdfium.PdfPage.render

    def _render(self, *a, scale=1, **kw):
        w, h = self.get_size()
        seen.append(w * scale * h * scale)
        return render(self, *a, scale=scale, **kw)

    monkeypatch.setattr(pdfium.PdfPage, "render", _render)
    return seen


def test_pdf_page_size_budget():
    from app import images

    a4 = images.pdf_scale(595, 842, 200 / 72)
    assert a4 == 200 / 72  # normal sayfa ölçeği değişmez
    s = images.pdf_scale(5000, 5000, 200 / 72)
    assert s is not None and 5000 * 5000 * s * s <= images.PDF_MAX_PIXELS * 1.0001
    assert images.pdf_scale(200000, 200000, 200 / 72) is None  # anlamsız sayfa çizilmez
    assert images.pdf_scale(0, 842, 2) is None


def test_karekod_pdf_bomb_renders_within_budget(rendered):
    """PoC B2: 195 baytlık 5000×5000 pt sayfa 200 dpi çiziliyordu (~2,9 GB)."""
    from app import karekod

    if karekod._zx() is None:
        pytest.skip("zxing-cpp yok")
    data = _pdf(5000, 5000, pages=3)
    assert len(data) < 400
    assert karekod.scan(data, "application/pdf") == []
    assert rendered and len(rendered) <= karekod.PDF_PAGES
    assert max(rendered) <= karekod.PDF_SCAN_MAX_PIXELS * 1.0001
    rendered.clear()
    karekod.scan(_pdf(595, 842), "application/pdf")  # A4 bütçeye sığar: eski çözünürlük (200 dpi)
    assert rendered and abs(rendered[0] - 595 * 842 * (200 / 72) ** 2) < 1


def test_document_parse_pdf_bomb_within_budget(owner, rendered):  # noqa: F811
    from app import karekod

    if karekod._zx() is None:
        pytest.skip("zxing-cpp yok")
    owner.post("/api/documents/parse", files={"file": ("fis.pdf", _pdf(5000, 5000), "application/pdf")})
    assert rendered and max(rendered) <= karekod.PDF_SCAN_MAX_PIXELS * 1.0001


def test_glyph_ocr_render_within_budget(rendered, monkeypatch):
    """Ekstre glif OCR'ı (Type3 yazı tipi): dev sayfa piksel bütçesine göre küçük ölçekle çizilir."""
    from app import glyphs, images

    monkeypatch.setattr(glyphs, "_ocr_words", lambda png, left: [])
    data = _pdf(5000, 5000, pages=2)
    votes, done = glyphs._ocr_votes(data, None, [0, 1], [[], []], [(0, 0, 5000, 5000)] * 2, glyphs._Registry(), set())
    assert rendered and max(rendered) <= images.PDF_MAX_PIXELS * 1.0001


# ---------------------------------------------------------------- D1: istek gövdesi ve dosya sınırı

def test_body_limit_rejects_large_request(owner, monkeypatch):  # noqa: F811
    monkeypatch.setenv("HESAPKITAP_MAX_BODY_MB", "1")
    big = b"x" * (2 * 1024 * 1024)
    r = owner.post("/api/receipts/parse", files={"file": ("a.jpg", big, "image/jpeg")})
    assert r.status_code == 413

    def chunks():  # Content-Length'siz (chunked) gövde: okunurken sayılır
        for _ in range(4):
            yield b"y" * (512 * 1024)

    r = owner.post("/api/entries", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert owner.get("/api/summary").status_code == 200  # küçük istekler etkilenmez


def test_file_read_is_capped(owner, monkeypatch):  # noqa: F811
    """Dosya belleğe tamamen okunmadan (cap + 1 bayt) reddedilir; sınır HESAPKITAP_MAX_FILE_MB."""
    monkeypatch.setenv("HESAPKITAP_MAX_FILE_MB", "1")
    r = owner.post("/api/receipts/parse", files={"file": ("a.jpg", b"\xff\xd8\xff" + b"0" * (1024 * 1024 + 10), "image/jpeg")})
    assert r.status_code == 413 and "çok büyük" in r.json()["detail"]
