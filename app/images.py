"""Fiş/fatura fotoğrafını saklamadan ve okumadan önce hazırla (Pillow).

- EXIF yönü uygulanır (yan çekilmiş fotoğraf düz durur), sonra EXIF'in TAMAMI atılır: konum (GPS), cihaz, saat
  bilgisi saklanmaz ve yapay zekâya gitmez (gizlilik).
- Uzun kenar en çok 1600 px, JPEG kalite ~78; sonuç 1.5 MB'ı aşarsa kalite, gerekirse boyut düşürülür.
- Okuma (Gemini / Tesseract) küçültülmüş görüntüyle yapılır: daha az veri, aynı okunurluk.

Pillow pdfplumber'ın bağımlılığı olarak zaten kurulu; requirements.txt'te ayrıca sabitlendi (doğrudan kullanılıyor).
HEIC: Pillow eklentisiz açamaz. Mobil uygulama ve web zaten JPEG gönderir; açılamayan görüntü özgün haliyle okunur ama
SAKLANMAZ (hazırlanmamış görüntüde konum bilgisi olabilir) ve günlüğe yazılır.
"""
import io
import logging

log = logging.getLogger("ekstre.images")

MAX_EDGE = 1600
QUALITY = 78
MAX_BYTES = int(1.5 * 1024 * 1024)
MAX_PIXELS = 60_000_000  # açmadan önce: dev boyutlu (sıkıştırma bombası) görüntüyü reddet


def prepare(data: bytes) -> bytes | None:
    """Saklanmaya ve okunmaya hazır JPEG (EXIF'siz); açılamazsa None."""
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow pdfplumber ile hep gelir
        log.warning("Pillow yok: fiş fotoğrafı hazırlanamadı")
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > MAX_PIXELS:
                raise ValueError(f"görüntü çok büyük ({im.width}x{im.height})")
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
            quality, out = QUALITY, b""
            while True:
                buf = io.BytesIO()
                # exif/icc/xmp verilmez: Pillow yeni dosyaya hiçbir meta veri yazmaz
                im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
                out = buf.getvalue()
                if len(out) <= MAX_BYTES:
                    return out
                if quality > 50:
                    quality -= 10
                else:
                    im = im.resize((max(1, int(im.width * 0.8)), max(1, int(im.height * 0.8))), Image.LANCZOS)
                    if max(im.size) < 400:
                        return out
    except Exception as e:  # noqa: BLE001 - HEIC, bozuk dosya: okuma özgün görüntüyle sürer
        log.info("fotoğraf hazırlanamadı (%s): %s", type(e).__name__, e)
        return None
