"""Yanıt sıkıştırma (GZip): küçük yanıtlar ve zaten sıkıştırılmış dosyalar olduğu gibi gider.

Starlette'in GZipMiddleware'i yalnız text/event-stream ile önceden kodlanmış yanıtları atlıyor; fiş fotoğrafı (JPEG),
ekstre PDF'i, dışa aktarma (xlsx/zip) ve şifreli yedek de sıkıştırılmaya çalışılıyordu: CPU harcanır, boyut küçülmez.
Seviye 6: büyük JSON'da 9'un yarısı kadar süre, boyut farkı ~%10 (zlib'in de varsayılanı).
"""
from starlette.middleware.gzip import GZipMiddleware

MINIMUM_SIZE = 1000
LEVEL = 6

# kendi içinde sıkıştırılmış ya da rastgele görünen (şifreli) içerik; SVG metindir, sıkıştırılır
_PACKED_PREFIXES = ("image/", "video/", "audio/", "font/woff", "application/pdf", "application/zip",
                    "application/gzip", "application/x-gzip", "application/octet-stream",
                    "application/vnd.openxmlformats-officedocument")


def incompressible(content_type: str) -> bool:
    ct = (content_type or "").lower()
    return ct.startswith(_PACKED_PREFIXES) and not ct.startswith("image/svg")


class GZip(GZipMiddleware):
    """Starlette'in GZip'i (seviye 6). Önceki sürüm GZipResponder'ın iç yapısını eziyordu; Starlette 1.x'te dosya
    yanıtlarını (fiş, fatura, PDF) 500'e düşürdü. Sıkıştırılmış içeriği atlama Starlette'e bırakıldı."""

    def __init__(self, app, minimum_size: int = MINIMUM_SIZE, compresslevel: int = LEVEL) -> None:
        super().__init__(app, minimum_size=minimum_size, compresslevel=compresslevel)
