"""Yanıt sıkıştırma (GZip): küçük yanıtlar ve zaten sıkıştırılmış dosyalar olduğu gibi gider.

Starlette'in GZipMiddleware'i yalnız text/event-stream ile önceden kodlanmış yanıtları atlıyor; fiş fotoğrafı (JPEG),
ekstre PDF'i, dışa aktarma (xlsx/zip) ve şifreli yedek de sıkıştırılmaya çalışılıyordu: CPU harcanır, boyut küçülmez.
Seviye 6: büyük JSON'da 9'un yarısı kadar süre, boyut farkı ~%10 (zlib'in de varsayılanı).
"""
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware, GZipResponder
from starlette.types import Message, Receive, Scope, Send

MINIMUM_SIZE = 1000
LEVEL = 6

# kendi içinde sıkıştırılmış ya da rastgele görünen (şifreli) içerik; SVG metindir, sıkıştırılır
_PACKED_PREFIXES = ("image/", "video/", "audio/", "font/woff", "application/pdf", "application/zip",
                    "application/gzip", "application/x-gzip", "application/octet-stream",
                    "application/vnd.openxmlformats-officedocument")


def incompressible(content_type: str) -> bool:
    ct = (content_type or "").lower()
    return ct.startswith(_PACKED_PREFIXES) and not ct.startswith("image/svg")


class _Responder(GZipResponder):
    async def send_with_compression(self, message: Message) -> None:
        await super().send_with_compression(message)
        if message["type"] == "http.response.start" and \
                incompressible(Headers(raw=message["headers"]).get("content-type", "")):
            self.content_type_is_excluded = True  # gövde olduğu gibi gider (Starlette'in event-stream yolu)


class GZip(GZipMiddleware):
    def __init__(self, app, minimum_size: int = MINIMUM_SIZE, compresslevel: int = LEVEL) -> None:
        super().__init__(app, minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and "gzip" in Headers(scope=scope).get("Accept-Encoding", ""):
            await _Responder(self.app, self.minimum_size, compresslevel=self.compresslevel)(scope, receive, send)
            return
        await super().__call__(scope, receive, send)
