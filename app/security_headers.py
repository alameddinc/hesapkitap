"""Tarayıcı güvenlik başlıkları (D4): CSP (varsayılan engelleyen), HSTS, Permissions-Policy; CSP rapor ucu.

Saf ASGI ara katmanı: yalnız yanıtın başlangıç mesajına (http.response.start) başlık ekler, gövdeye hiç dokunmaz.
Dosya (FileResponse), akış (StreamingResponse) ve sıkıştırılmış yanıtlar olduğu gibi geçer. Uygulamanın kendi koyduğu
başlık ezilmez (yalnız yoksa eklenir).

CSP
- Varsayılan (HESAPKITAP_CSP boş ya da enforce): Content-Security-Policy, politika dışı kaynak ENGELLENİR ve ihlal
  POST /api/csp-report'a bildirilir; rapor sunucu günlüğüne "hesapkitap.csp" adıyla düşer (dakikada sınırlı, kişisel
  veri yok). Geri dönüş: HESAPKITAP_CSP=report → Content-Security-Policy-Report-Only (hiçbir şey engellenmez, yalnız
  rapor), HESAPKITAP_CSP=off → hiç gönderilmez. Tanınmayan değer güvenli tarafa (enforce) düşer.
- Engelleyen kipe geçiş denetimi (11.11): web derlemesi kendi dosyaları dışında kaynak yüklemez (dış adresler yalnız
  bağlantı/gezinme: App Store, Play, GitHub; CSP gezinmeyi kısıtlamaz), iframe/object/worker/eval/WebAssembly yok,
  form başka adrese gönderilmez; mobil uygulama (Capacitor) kendi paketini çalıştırır, sunucudan yalnız JSON alır
  (JSON'a CSP eklenmez); demo ve yönetici sayfaları aynı web derlemesi; Google/Apple girişi ve mağaza ödemesi
  yalnız telefonda (yerel SDK), web'de dış betik yok.
- Yalnız HTML yanıtlarına eklenir. JSON API'yi (mobil uygulama, Capacitor) ve dosyaları etkilemez.
- Web uygulaması (web/): kendi derleme dosyaları ('self'), index.html'deki tek satır içi betik (özeti dosyadan
  hesaplanır; içerik değişince kendiliğinden güncellenir), satır içi stil (grafikler, React style), data:/blob: resim
  (fiş önizlemesi), fontlar (@fontsource derlemeye gömülü; küçükleri data: olabilir), service worker, manifest.
- Tanıtım/hukuk sayfaları (<veri>/site/*.html) ayrı politika: Google Fonts ve satır içi <style> kullanıyorlar.

HSTS: yalnız HTTPS ile gelen istekte (TLS'i vekil bitiriyorsa X-Forwarded-Proto: https). Düz HTTP'de tarayıcı bu
başlığı zaten yok sayar, sahte X-Forwarded-Proto bir şey kazandırmaz. includeSubDomains yok (aynı alan adında başka
siteler var); süre HESAPKITAP_HSTS_MAX_AGE (saniye, 0 = kapalı).
"""
import base64
import hashlib
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("hesapkitap.csp")

REPORT_PATH = "/api/csp-report"
HSTS_MAX_AGE = 15552000  # 180 gün
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()"
REPORT_MAX_BYTES = 16 * 1024
REPORT_LOG_PER_MINUTE = 30

_BASE = {
    "default-src": "'self'",
    "base-uri": "'self'",
    "object-src": "'none'",
    "form-action": "'self'",
    "frame-ancestors": "'none'",
}
APP_POLICY = {
    **_BASE,
    "script-src": "'self'",  # + index.html'deki satır içi betiğin özeti
    "style-src": "'self' 'unsafe-inline'",
    "img-src": "'self' data: blob:",
    "font-src": "'self' data:",
    "connect-src": "'self'",
    "worker-src": "'self'",
    "manifest-src": "'self'",
}
SITE_POLICY = {
    **_BASE,
    "script-src": "'self'",
    "style-src": "'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src": "'self' https://fonts.gstatic.com data:",
    "img-src": "'self' data:",
}
_ORDER = ("default-src", "script-src", "style-src", "img-src", "font-src", "connect-src", "worker-src", "manifest-src",
          "base-uri", "object-src", "form-action", "frame-ancestors")

_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.S | re.I)


def inline_script_hashes(html: str) -> list[str]:
    """src'siz <script> bloklarının CSP özeti ('sha256-…'). Tarayıcı etiketin içindeki metni aynen özetler."""
    out = []
    for body in _INLINE_SCRIPT.findall(html or ""):
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        out.append(f"'sha256-{digest}'")
    return list(dict.fromkeys(out))


def render(policy: dict, extra_script: list[str] | None = None, report: bool = True) -> str:
    p = dict(policy)
    if extra_script:
        p["script-src"] = " ".join([p["script-src"], *extra_script])
    parts = [f"{k} {p[k]}" for k in _ORDER if k in p]
    if report:
        parts.append(f"report-uri {REPORT_PATH}")
    return "; ".join(parts)


_hash_cache: dict = {}


def _index_hashes(index_file: str | None) -> list[str]:
    """Web derlemesinin index.html'indeki satır içi betik özetleri; dosya değişmedikçe yeniden okunmaz."""
    if not index_file:
        return []
    try:
        st = os.stat(index_file)
    except OSError:
        return []
    key = (index_file, st.st_mtime_ns, st.st_size)
    if _hash_cache.get("key") != key:
        try:
            with open(index_file, encoding="utf-8") as f:
                hashes = inline_script_hashes(f.read())
        except OSError:
            hashes = []
        _hash_cache.update(key=key, hashes=hashes)
    return _hash_cache["hashes"]


def csp_mode() -> str:
    v = (os.environ.get("HESAPKITAP_CSP") or "enforce").strip().lower()
    return v if v in ("report", "enforce", "off") else "enforce"


def hsts_value() -> str | None:
    v = (os.environ.get("HESAPKITAP_HSTS_MAX_AGE") or "").strip()
    age = int(v) if v.isdigit() else HSTS_MAX_AGE
    return f"max-age={age}" if age > 0 else None


def _header(headers: list, name: bytes) -> bytes | None:
    for k, v in headers:
        if k.lower() == name:
            return v
    return None


def _is_https(scope) -> bool:
    if scope.get("scheme") == "https":
        return True
    proto = _header(scope.get("headers") or [], b"x-forwarded-proto")
    return bool(proto) and proto.split(b",")[0].strip().lower() == b"https"


# ---------------------------------------------------------------- CSP raporu

_report_lock = threading.Lock()
_report_window = {"start": 0.0, "count": 0, "dropped": 0}


def _origin_only(url) -> str:
    """Raporda adresin yalnız kaynağı (şema + alan adı) ya da 'inline'/'eval' gibi anahtar kelime; yol ve sorgu
    (kişisel veri taşıyabilir) yazılmaz."""
    from urllib.parse import urlsplit

    s = str(url or "")[:300]
    if not s:
        return "-"
    if "://" not in s:
        return re.sub(r"[^\w.:-]", "", s)[:40] or "-"
    u = urlsplit(s)
    return f"{u.scheme}://{u.netloc}"[:120]


def _path_only(url) -> str:
    from urllib.parse import urlsplit

    s = str(url or "")[:300]
    return (urlsplit(s).path or "/")[:80] if "://" in s else "-"


def summarize(body: bytes) -> list[str]:
    """Gelen rapor(lar)ı kısa, kişisel verisiz satırlara çevir. Eski biçim ({"csp-report": {...}}) ve Reporting API
    biçimi ([{"type": "csp-violation", "body": {...}}]) desteklenir."""
    try:
        data = json.loads(body.decode("utf-8", "replace") or "null")
    except ValueError:
        return []
    items = []
    if isinstance(data, dict) and isinstance(data.get("csp-report"), dict):
        items.append(data["csp-report"])
    elif isinstance(data, list):
        items += [d.get("body") for d in data[:20] if isinstance(d, dict) and isinstance(d.get("body"), dict)]
    out = []
    for r in items:
        directive = r.get("effective-directive") or r.get("effectiveDirective") or r.get("violated-directive") or "?"
        blocked = r.get("blocked-uri") or r.get("blockedURL")
        doc = r.get("document-uri") or r.get("documentURL")
        src = r.get("source-file") or r.get("sourceFile")
        line = r.get("line-number") or r.get("lineNumber")
        # disposition: tarayıcının uyguladığı kip ("enforce" engelledi, "report" yalnız bildirdi); yoksa sunucunun kipi
        disp = str(r.get("disposition") or ("report" if csp_mode() == "report" else "enforce")).lower()
        verb = "engellenecekti" if disp == "report" else "engellendi"
        out.append(f"{str(directive)[:40]} {verb}: {_origin_only(blocked)} · sayfa {_path_only(doc)}"
                   + (f" · kaynak {_origin_only(src)}:{line}" if src else ""))
    return out


def log_report(body: bytes) -> None:
    lines = summarize(body)
    now = time.monotonic()
    with _report_lock:
        w = _report_window
        if now - w["start"] >= 60:
            if w["dropped"]:
                log.info("CSP raporu: son dakikada %d rapor daha yazılmadı (sınır)", w["dropped"])
            w.update(start=now, count=0, dropped=0)
        allowed = max(0, REPORT_LOG_PER_MINUTE - w["count"])
        w["count"] += min(allowed, len(lines))
        w["dropped"] += max(0, len(lines) - allowed)
    for line in lines[:allowed]:
        log.warning("CSP raporu: %s", line)


async def _read_body(receive, limit: int) -> bytes | None:
    """İstek gövdesi; sınırı aşarsa None (kalanı okunmaz, yanıt yine 204)."""
    chunks, size = [], 0
    while True:
        msg = await receive()
        if msg["type"] != "http.request":
            return None
        chunk = msg.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not msg.get("more_body"):
            return b"".join(chunks)


async def _no_content(send) -> None:
    await send({"type": "http.response.start", "status": 204,
                "headers": [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]})
    await send({"type": "http.response.body", "body": b""})


# ---------------------------------------------------------------- ara katman

class SecurityHeaders:
    """app.add_middleware(SecurityHeaders, index_file=..., site_paths=...)."""

    def __init__(self, app, index_file=None, site_paths=()):
        self.app = app
        self.index_file = index_file  # str ya da çağrılabilir (WEB_DIST sonradan ayarlanabilir)
        self.site_paths = set(site_paths)

    def _index(self) -> str | None:
        return self.index_file() if callable(self.index_file) else self.index_file

    def _csp(self, path: str) -> str:
        if path in self.site_paths:
            return render(SITE_POLICY)
        return render(APP_POLICY, _index_hashes(self._index()))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path == REPORT_PATH and scope.get("method") == "POST":
            # oturumsuz, defter açmaz, CSRF/okuma sırası yok: tarayıcı raporu çerezsiz ve kendiliğinden yollar
            body = await _read_body(receive, REPORT_MAX_BYTES)
            if body:
                try:
                    log_report(body)
                except Exception:  # noqa: BLE001 - rapor hiçbir zaman hata vermesin
                    log.debug("CSP raporu okunamadı", exc_info=True)
            await _no_content(send)
            return
        https = _is_https(scope)
        mode = csp_mode()

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {k.lower() for k, _ in headers}

                def add(name: str, value: str | None) -> None:
                    if value and name.encode() not in present:
                        headers.append((name.encode(), value.encode("latin-1")))
                        present.add(name.encode())

                add("x-content-type-options", "nosniff")
                add("x-frame-options", "DENY")
                add("referrer-policy", "same-origin")
                add("permissions-policy", PERMISSIONS_POLICY)
                if https:
                    add("strict-transport-security", hsts_value())
                ctype = (_header(headers, b"content-type") or b"").lower()
                if mode != "off" and ctype.startswith(b"text/html"):
                    add("content-security-policy" if mode == "enforce" else "content-security-policy-report-only",
                        self._csp(path))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
