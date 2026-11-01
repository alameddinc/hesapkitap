"""Yanıt sıkıştırma (GZip): küçük yanıtlar olduğu gibi gider.

Starlette'in GZipMiddleware'i yalnız text/event-stream ile önceden kodlanmış yanıtları atlar. Zaten sıkıştırılmış
dosyaları (JPEG, PDF, xlsx/zip) atlayan özel sürüm Starlette 1.x'te dosya yanıtlarını bozduğu için kaldırıldı (01ba2c7).
Seviye 6: büyük JSON'da 9'un yarısı kadar süre, boyut farkı ~%10 (zlib'in de varsayılanı).
"""
from starlette.middleware.gzip import GZipMiddleware

MINIMUM_SIZE = 1000
LEVEL = 6

class GZip(GZipMiddleware):
    """Starlette'in GZip'i (seviye 6). Önceki sürüm GZipResponder'ın iç yapısını eziyordu; Starlette 1.x'te dosya
    yanıtlarını (fiş, fatura, PDF) 500'e düşürdü. Sıkıştırılmış içeriği atlama Starlette'e bırakıldı."""

    def __init__(self, app, minimum_size: int = MINIMUM_SIZE, compresslevel: int = LEVEL) -> None:
        super().__init__(app, minimum_size=minimum_size, compresslevel=compresslevel)
