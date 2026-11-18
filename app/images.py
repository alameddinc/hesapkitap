"""Fiş/fatura fotoğrafını saklamadan ve okumadan önce hazırla (Pillow).

- EXIF yönü uygulanır (yan çekilmiş fotoğraf düz durur), sonra EXIF'in TAMAMI atılır: konum (GPS), cihaz, saat
  bilgisi saklanmaz ve yapay zekâya gitmez (gizlilik).
- Uzun kenar en çok 1600 px, JPEG kalite ~78; sonuç 1.5 MB'ı aşarsa kalite, gerekirse boyut düşürülür.
- Okuma (Gemini / Tesseract) küçültülmüş görüntüyle yapılır: daha az veri, aynı okunurluk.

Görüntü bombası (güvenlik denetimi 18.11, Y1): 57 KB'lık 7700×7700 palet PNG tam boyutta RGBA'ya çevrilince ~750 MB
bellek yiyordu. Artık piksel sınırı çözmeden ÖNCE (yalnız başlık okunmuşken) denetlenir; JPEG çözücü küçültülmüş
boyutta çözer (draft), diğer biçimler renk dönüşümünden önce küçültülür. Sınırı aşan görüntü ImageTooLarge (istemciye
413); açılamayan (HEIC, bozuk) görüntü eskisi gibi None.

Pillow pdfplumber'ın bağımlılığı olarak zaten kurulu; requirements.txt'te ayrıca sabitlendi (doğrudan kullanılıyor).
HEIC: Pillow eklentisiz açamaz. Mobil uygulama ve web zaten JPEG gönderir; açılamayan görüntü özgün haliyle okunur ama
SAKLANMAZ (hazırlanmamış görüntüde konum bilgisi olabilir) ve günlüğe yazılır.
"""
import io
import logging
import math

log = logging.getLogger("ekstre.images")

MAX_EDGE = 1600
QUALITY = 78
MAX_BYTES = int(1.5 * 1024 * 1024)
# Çözülecek (JPEG'de draft sonrası) en çok piksel; ayrıca çözülmüş ham boyut MAX_DECODED_BYTES'ı aşamaz (Pillow RGB/RGBA
# pikseli 4 bayt tutar: renkli görüntüde fiili sınır 32M piksel, gri/palette 40M).
MAX_PIXELS = 40_000_000
MAX_DECODED_BYTES = 128 * 1024 * 1024
# PDF sayfası çizimi (glif OCR'ı; karekod yedeğinin kendi bütçesi var): sayfa başı en çok bu kadar piksel — A4 300 dpi
# (~8,7M) sığar; büyük sayfada ölçek düşer (195 baytlık 5000×5000 pt sayfa ~2,9 GB çizdiriyordu, Y2)
PDF_MAX_PIXELS = 9_000_000
PDF_MIN_SCALE = 0.25  # bütçeye sığmak için bundan küçük ölçek gerekirse sayfa çizilmez (anlamsız boyutta sayfa)

_ORIENT = {2: "FLIP_LEFT_RIGHT", 3: "ROTATE_180", 4: "FLIP_TOP_BOTTOM", 5: "TRANSPOSE", 6: "ROTATE_270",
           7: "TRANSVERSE", 8: "ROTATE_90"}


class ImageTooLarge(ValueError):
    """Görüntü çözülemeyecek kadar büyük (sıkıştırma bombası olabilir): istemciye 413."""

    message = "Görüntü çok büyük; daha düşük çözünürlükte bir fotoğraf yükle."


def _bytes_per_pixel(mode: str) -> int:
    return 1 if mode in ("1", "L", "P") else 2 if mode in ("LA", "PA", "I;16", "I;16B", "I;16L") else 4


def open_limited(data: bytes, max_pixels: int = MAX_PIXELS, edge: int | None = None):
    """Görüntüyü YALNIZ başlığını okuyarak aç; JPEG'de çözücüyü küçültülmüş boyuta ayarla (draft: edge verilirse
    uzun kenar ~2×edge, her durumda max_pixels'e sığacak ölçek). Çözülecek boyut max_pixels'i ya da
    MAX_DECODED_BYTES'ı aşarsa ImageTooLarge (piksel çözülmeden). Açılamayan dosyada Pillow'un hatası geçer.
    Dönen görüntüyü çağıran kapatır."""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as e:  # Pillow'un kendi tavanı (MAX_IMAGE_PIXELS × 2)
        raise ImageTooLarge(str(e)) from e
    try:
        w, h = im.size
        if w <= 0 or h <= 0:
            raise ImageTooLarge(f"geçersiz boyut ({w}x{h})")
        if im.format == "JPEG":
            s = 1
            while s < 8 and (w // s) * (h // s) > max_pixels:
                s *= 2
            req = [max(1, w // s), max(1, h // s)]
            if edge:
                k = 2 * edge / max(w, h)
                if k < 1:
                    req = [min(req[0], max(1, int(w * k))), min(req[1], max(1, int(h * k)))]
            if tuple(req) != (w, h):
                im.draft(None, tuple(req))  # DCT ölçekleme: 1/2, 1/4, 1/8 boyutta çözer
        w, h = im.size
        cap = min(max_pixels, MAX_DECODED_BYTES // _bytes_per_pixel(im.mode))
        if w * h > cap:
            raise ImageTooLarge(f"görüntü çok büyük ({w}x{h}, {im.mode})")
    except BaseException:
        im.close()
        raise
    return im


def check(data: bytes, max_pixels: int = MAX_PIXELS) -> None:
    """Görüntü sınırı aşıyorsa ImageTooLarge; açılamayan (HEIC, bozuk) dosyada sessiz (başka yol okur/reddeder)."""
    try:
        open_limited(data, max_pixels).close()
    except ImageTooLarge:
        raise
    except Exception:  # noqa: BLE001 - açılamadı: büyüklük denetimi bu dosyaya uygulanamaz
        return


def reduce_native(im, edge: int):
    """Renk dönüşümünden ÖNCE kabaca küçült (uzun kenar ~2×edge): tam boyutlu RGBA/RGB kopyaları oluşmaz.
    Alfa ve palet kiplerinde en yakın komşu (premultiplied tam kopya yok), diğerlerinde kutu ortalaması (reduce)."""
    from PIL import Image

    k = math.ceil(max(im.size) / (2 * edge))  # sonuç uzun kenarı ≥ edge kalır (uzun kenar > 2×edge iken)
    if k < 2:
        return im
    if im.mode in ("L", "RGB", "I", "F", "CMYK", "YCbCr"):
        return im.reduce(k)
    return im.resize((max(1, im.width // k), max(1, im.height // k)), Image.NEAREST)


def transpose_exif(src, im):
    """src'nin EXIF yönünü (0x0112; XMP dahil, Pillow getexif) im'e uygula; yön yoksa im aynen döner."""
    from PIL import Image

    try:
        orient = src.getexif().get(0x0112)
    except Exception:  # noqa: BLE001 - bozuk EXIF: yön uygulanmaz
        orient = None
    method = _ORIENT.get(orient)
    return im.transpose(getattr(Image.Transpose, method)) if method else im


def pdf_scale(width_pt: float, height_pt: float, scale: float, max_pixels: int = PDF_MAX_PIXELS) -> float | None:
    """Sayfa (pt) bu ölçekte çizilince piksel bütçesini aşarsa ölçeği bütçeye düşür; gereken ölçek PDF_MIN_SCALE'den
    küçükse (dev MediaBox) None: sayfa çizilmez (Y2, PDF çizim bombası)."""
    area = float(width_pt) * float(height_pt)
    if not area > 0 or not math.isfinite(area):
        return None
    if area * scale * scale <= max_pixels:
        return scale
    s = math.sqrt(max_pixels / area)
    return s if s >= PDF_MIN_SCALE else None


def prepare(data: bytes) -> bytes | None:
    """Saklanmaya ve okunmaya hazır JPEG (EXIF'siz); açılamazsa None. Sınırı aşan görüntüde ImageTooLarge."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow pdfplumber ile hep gelir
        log.warning("Pillow yok: fiş fotoğrafı hazırlanamadı")
        return None
    try:
        src = open_limited(data, MAX_PIXELS, edge=MAX_EDGE)
    except ImageTooLarge:
        log.warning("görüntü sınırı aştı (%d bayt): reddedildi", len(data))
        raise
    except Exception as e:  # noqa: BLE001 - HEIC, bozuk dosya: okuma özgün görüntüyle sürer
        log.info("fotoğraf hazırlanamadı (%s): %s", type(e).__name__, e)
        return None
    try:
        with src:
            im = transpose_exif(src, reduce_native(src, MAX_EDGE))
            if im.mode in ("RGBA", "LA", "P", "PA"):
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
    except Exception as e:  # noqa: BLE001 - bozuk dosya (çözerken hata): okuma özgün görüntüyle sürer
        log.info("fotoğraf hazırlanamadı (%s): %s", type(e).__name__, e)
        return None
