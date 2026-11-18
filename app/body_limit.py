"""İstek gövdesi sınırı (güvenlik denetimi 18.11, D1): gövde uygulamaya akarken sayılır, sınırı aşan istek 413 alır.

Content-Length sınırın üstündeyse istek hiç okunmadan reddedilir; başlıksız (chunked) gövde okunurken sayılır ve sınır
aşılınca okuma kesilir. Dosya başı sınır (limits.max_file_bytes) uçlarda ayrıca denetlenir; bu, tek isteğin toplamıdır.
    HESAPKITAP_MAX_BODY_MB   istek başı en çok gövde (100; 0 = sınırsız). Yedekten geri yükleme hariç (EXEMPT).
"""
import json
import os

EXEMPT = ("/api/settings/backup/restore",)  # kendi kurulumunda yedek yüzlerce MB olabilir (oturum gerekir)
MESSAGE = "İstek çok büyük; dosyaları daha küçük parçalar halinde yükle."


def max_body_bytes() -> int:
    try:
        mb = float(os.environ.get("HESAPKITAP_MAX_BODY_MB", 100))
    except ValueError:
        mb = 100.0
    return int(mb * 1024 * 1024) if mb > 0 else 0


def _too_large_messages() -> list[dict]:
    body = json.dumps({"detail": MESSAGE}, ensure_ascii=False).encode()
    return [{"type": "http.response.start", "status": 413,
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                         (b"connection", b"close")]},
            {"type": "http.response.body", "body": body}]


class BodyLimit:
    """Saf ASGI ara katmanı: yalnız receive'i sayar. Sınır aşılınca uygulamaya istemci kopmuş gibi görünür (okuma
    kesilir) ve uygulamanın bu yüzden vereceği hata yanıtı yerine 413 gider (yanıt henüz başlamadıysa)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "") in EXEMPT:
            return await self.app(scope, receive, send)
        limit = max_body_bytes()
        if not limit:
            return await self.app(scope, receive, send)
        cl = next((v for k, v in scope.get("headers") or [] if k == b"content-length"), b"")
        if cl.strip().isdigit() and int(cl) > limit:
            for m in _too_large_messages():
                await send(m)
            return
        state = {"seen": 0, "over": False, "started": False, "replaced": False}

        async def counted():
            if state["over"]:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg.get("type") == "http.request":
                state["seen"] += len(msg.get("body") or b"")
                if state["seen"] > limit:
                    state["over"] = True
                    return {"type": "http.disconnect"}
            return msg

        async def guarded(msg):
            if state["over"] and not state["started"]:
                if not state["replaced"] and msg.get("type") == "http.response.start":
                    state["replaced"] = True
                    for m in _too_large_messages():
                        await send(m)
                return  # uygulamanın kopma yüzünden verdiği yanıt gitmez
            if msg.get("type") == "http.response.start":
                state["started"] = True
            await send(msg)

        await self.app(scope, counted, guarded)
