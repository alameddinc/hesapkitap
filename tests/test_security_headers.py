"""Tarayıcı güvenlik başlıkları (D4): CSP varsayılan engelleyen kipte (HESAPKITAP_CSP=report: yalnız rapor) ve yalnız HTML'de, HSTS yalnız HTTPS'te, Permissions-Policy;
CSP rapor ucu oturumsuz, kişisel veri yazmadan. Mobil uygulama (CORS, Bearer) ve evrensel bağlantı dosyaları bozulmaz;
dosya ve akış yanıtlarının gövdesi değişmez."""
import base64
import gzip
import hashlib
import importlib
import logging

import pytest
from fastapi.testclient import TestClient

from app import security_headers as sh

INLINE = "\n      if (/iPhone/.test(navigator.userAgent)) { document.title += ''; }\n    "
INDEX = f"""<!doctype html><html><head><script>{INLINE}</script>
<script type="module" crossorigin src="/assets/index-abc.js"></script></head><body><div id="root"></div></body></html>"""


@pytest.fixture()
def spa(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX, encoding="utf-8")
    (dist / "assets" / "index-abc.js").write_text("console.log('x');" * 500)
    (dist / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40)
    site = tmp_path / "site"
    site.mkdir()
    (site / "gizlilik.html").write_text("<html><head><style>p{}</style></head><body>gizlilik</body></html>")
    monkeypatch.setenv("WEB_DIST", str(dist))
    monkeypatch.setenv("HESAPKITAP_SITE_DIR", str(site))
    monkeypatch.setenv("HESAPKITAP_ANDROID_SHA256", "AA:BB")
    from app import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    import app.api.main as main

    main = importlib.reload(main)
    yield TestClient(main.app), dist
    monkeypatch.delenv("WEB_DIST")
    importlib.reload(main)


CSP = "content-security-policy"
RO = "content-security-policy-report-only"


def _no_csp(headers) -> bool:
    return CSP not in headers and RO not in headers


def _sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def test_spa_html_gets_enforced_csp_with_inline_hash(spa, monkeypatch):
    monkeypatch.delenv("HESAPKITAP_CSP", raising=False)
    c, _ = spa
    r = c.get("/")
    assert r.status_code == 200
    csp = r.headers[CSP]
    assert RO not in r.headers  # varsayılan: engelleyen başlık, yalnız biri
    assert _sha(INLINE) in csp  # index.html'deki satır içi betik izinli
    for part in ("default-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:",
                 "font-src 'self' data:", "connect-src 'self'", "worker-src 'self'", "object-src 'none'",
                 "frame-ancestors 'none'", "report-uri /api/csp-report"):
        assert part in csp, part
    assert "unsafe-eval" not in csp
    assert r.headers["permissions-policy"].startswith("camera=()")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/islemler").headers[CSP] == csp  # SPA yönlendirmesi de


def test_csp_hash_follows_index_changes(spa):
    c, dist = spa
    (dist / "index.html").write_text("<html><head><script>var yeni = 1;</script></head></html>", encoding="utf-8")
    csp = c.get("/").headers[CSP]
    assert _sha("var yeni = 1;") in csp and _sha(INLINE) not in csp


def test_inline_hash_helper_ignores_external_scripts():
    html = '<script src="/a.js"></script><script type="module">x()</script><script>x()</script>'
    assert sh.inline_script_hashes(html) == [_sha("x()")]


def test_site_pages_have_their_own_policy(spa):
    c, _ = spa
    csp = c.get("/gizlilik").headers[CSP]
    assert "https://fonts.googleapis.com" in csp and "https://fonts.gstatic.com" in csp
    assert "fonts.googleapis" not in c.get("/").headers[CSP]


def test_api_and_files_get_no_csp_and_bodies_are_intact(spa):
    c, dist = spa
    r = c.get("/api/meta")
    assert r.status_code == 200 and _no_csp(r.headers)
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
    js = c.get("/assets/index-abc.js", headers={"accept-encoding": "gzip"})
    assert js.text == (dist / "assets" / "index-abc.js").read_text()  # sıkıştırılmış dosya yanıtı açılınca aynı
    assert js.headers.get("content-encoding") == "gzip"
    png = c.get("/assets/logo.png", headers={"accept-encoding": "gzip"})
    assert png.content == (dist / "assets" / "logo.png").read_bytes() and "content-encoding" not in png.headers
    assert _no_csp(png.headers)


def test_universal_link_files_stay_plain_json(spa):
    c, _ = spa
    for path in ("/.well-known/apple-app-site-association", "/.well-known/assetlinks.json"):
        r = c.get(path, follow_redirects=False, headers={"x-forwarded-proto": "https"})
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith("application/json"), path
        assert _no_csp(r.headers)
        assert r.json()


def test_hsts_only_over_https(spa, monkeypatch):
    c, _ = spa
    assert "strict-transport-security" not in c.get("/").headers
    r = c.get("/", headers={"x-forwarded-proto": "https"})
    assert r.headers["strict-transport-security"] == "max-age=15552000"
    assert "includeSubDomains" not in r.headers["strict-transport-security"]
    monkeypatch.setenv("HESAPKITAP_HSTS_MAX_AGE", "0")
    assert "strict-transport-security" not in c.get("/", headers={"x-forwarded-proto": "https"}).headers


def test_report_enforce_and_off_modes(spa, monkeypatch):
    """Geri dönüş yolu: report → yalnız rapor başlığı (aynı politika), off → hiç; tanınmayan değer enforce'a düşer."""
    c, _ = spa
    monkeypatch.setenv("HESAPKITAP_CSP", "enforce")
    enforced = c.get("/").headers
    assert CSP in enforced and RO not in enforced
    monkeypatch.setenv("HESAPKITAP_CSP", "report")
    r = c.get("/")
    assert RO in r.headers and CSP not in r.headers and r.headers[RO] == enforced[CSP]
    assert RO in c.get("/gizlilik").headers
    monkeypatch.setenv("HESAPKITAP_CSP", "off")
    r = c.get("/")
    assert not any(k.startswith("content-security-policy") for k in r.headers)
    assert r.headers["x-frame-options"] == "DENY"  # öteki başlıklar sürer
    for v in ("REPORT", " Report "):
        monkeypatch.setenv("HESAPKITAP_CSP", v)
        assert RO in c.get("/").headers
    monkeypatch.setenv("HESAPKITAP_CSP", "yanlis")
    assert CSP in c.get("/").headers
    monkeypatch.setenv("HESAPKITAP_CSP", "")
    assert CSP in c.get("/").headers


def test_mobile_cors_unchanged(client, monkeypatch):
    from app import auth
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth.create_admin("admin", "yonetici-sifre-1")
    c = TestClient(app)
    pre = c.options("/api/summary", headers={"Origin": "capacitor://localhost", "Access-Control-Request-Method": "GET",
                                              "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "capacitor://localhost"
    r = c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"},
               headers={"Origin": "https://localhost", "x-hk-client": "mobile"})
    token = r.headers["X-Session-Token"]
    r = c.get("/api/summary", headers={"Origin": "http://localhost", "authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost"
    assert _no_csp(r.headers)


REPORT = (b'{"csp-report": {"document-uri": "https://ornek.test/islemler?ara=ayse%40ornek.test",'
          b' "effective-directive": "script-src-elem", "blocked-uri": "https://kotu.example/x.js?k=1",'
          b' "source-file": "https://ornek.test/assets/index-abc.js", "line-number": 3}}')


def test_csp_report_endpoint_logs_without_personal_data(client, monkeypatch, caplog):
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)  # oturum yok: yine 204
    sh._report_window.update(start=0.0, count=0, dropped=0)
    with caplog.at_level(logging.WARNING, logger="hesapkitap.csp"):
        r = TestClient(app).post("/api/csp-report", content=REPORT, headers={"content-type": "application/csp-report",
                                                                             "sec-fetch-site": "cross-site"})
    assert r.status_code == 204
    text = caplog.text
    assert "script-src-elem" in text and "https://kotu.example" in text and "/islemler" in text
    assert "ayse" not in text and "k=1" not in text and "ara=" not in text  # sorgu ve yol ayrıntısı yazılmaz


def test_csp_report_reporting_api_format_and_limits(client, caplog):
    from app.api.main import app

    c = TestClient(app)
    sh._report_window.update(start=0.0, count=0, dropped=0)
    batch = b'[{"type": "csp-violation", "body": {"effectiveDirective": "img-src", "blockedURL": "data",' \
            b' "documentURL": "https://ornek.test/"}}]'
    with caplog.at_level(logging.WARNING, logger="hesapkitap.csp"):
        assert c.post("/api/csp-report", content=batch, headers={"content-type": "application/reports+json"}).status_code == 204
        assert c.post("/api/csp-report", content=b"{bozuk").status_code == 204
        assert c.post("/api/csp-report", content=b"x" * (sh.REPORT_MAX_BYTES + 10)).status_code == 204
        for _ in range(sh.REPORT_LOG_PER_MINUTE + 5):
            c.post("/api/csp-report", content=REPORT)
    assert "img-src engellendi: data" in caplog.text  # varsayılan kip enforce
    assert caplog.text.count("CSP raporu:") == sh.REPORT_LOG_PER_MINUTE  # dakikada sınırlı
    assert c.get("/api/csp-report").status_code != 204  # yalnız POST yakalanır


def test_csp_report_wording_follows_disposition(client, monkeypatch):
    """Günlük satırı tarayıcının kipini söyler: enforce'ta "engellendi", report'ta "engellenecekti"."""
    body = lambda d: ('[{"type": "csp-violation", "body": {"effectiveDirective": "script-src-elem", "blockedURL": '
                      '"https://kotu.example/a.js", "documentURL": "https://ornek.test/", "disposition": "%s"}}]' % d).encode()
    assert sh.summarize(body("enforce"))[0].startswith("script-src-elem engellendi: https://kotu.example")
    assert sh.summarize(body("report"))[0].startswith("script-src-elem engellenecekti: https://kotu.example")
    monkeypatch.setenv("HESAPKITAP_CSP", "report")  # disposition yoksa sunucunun kipi
    assert "engellenecekti" in sh.summarize(REPORT)[0]
    monkeypatch.delenv("HESAPKITAP_CSP")
    assert "engellendi" in sh.summarize(REPORT)[0]


def test_real_web_index_inline_scripts_are_hashed(tmp_path):
    """Gerçek web/index.html'deki her satır içi betik (iOS yakınlaştırma düzeltmesi) politikada özetle izinli: engelleyen
    kipte beyaz ekran olmasın. Vite classic satır içi betiği değiştirmez; çalışma anında özet derlenmiş dosyadan alınır."""
    import pathlib

    html = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    hashes = sh.inline_script_hashes(html)
    assert len(hashes) == 1  # yeni satır içi betik eklenirse bu test hatırlatır
    f = tmp_path / "index.html"
    f.write_text(html, encoding="utf-8")
    csp = sh.render(sh.APP_POLICY, sh._index_hashes(str(f)))
    assert hashes[0] in csp and "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]


def test_csp_report_does_not_open_demo_sandbox(client, monkeypatch, tmp_path):
    from app import demo_mode
    from app.api.main import app

    monkeypatch.setenv("HESAPKITAP_DEMO", "1")
    opened = []
    monkeypatch.setattr(demo_mode, "new_sandbox", lambda: opened.append(1) or "x")
    r = TestClient(app).post("/api/csp-report", content=REPORT)
    assert r.status_code == 204 and not opened


async def _collect(app, path: str, headers=None):
    import asyncio

    sent, done, first = [], asyncio.Event(), [True]

    async def receive():
        if first[0]:
            first[0] = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await done.wait()  # yanıt bitene kadar bağlantı açık
        return {"type": "http.disconnect"}

    async def send(m):
        sent.append(m)
        if m["type"] == "http.response.body" and not m.get("more_body"):
            done.set()

    scope = {"type": "http", "method": "GET", "path": path, "headers": headers or [], "scheme": "http",
             "query_string": b"", "root_path": ""}
    await app(scope, receive, send)
    return sent


def test_streaming_and_file_responses_pass_through_untouched(tmp_path):
    """Ara katman yalnız başlık ekler: akışın parçaları ve dosyanın baytları aynen, aynı sırayla gider."""
    import asyncio

    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import FileResponse, StreamingResponse
    from starlette.routing import Route

    from app.compress import GZip

    blob = tmp_path / "fis.pdf"
    blob.write_bytes(b"%PDF-1.4\n" + bytes(range(256)) * 300)

    async def stream(request):
        async def gen():
            for i in range(5):
                yield f"parca-{i};".encode() * 400
        return StreamingResponse(gen(), media_type="text/html")

    async def file(request):
        return FileResponse(blob, media_type="application/pdf")

    inner = Starlette(routes=[Route("/akis", stream), Route("/dosya", file)], middleware=[Middleware(GZip)])
    app = sh.SecurityHeaders(inner)
    sent = asyncio.run(_collect(app, "/akis"))
    start = sent[0]
    names = {k for k, _ in start["headers"]}
    assert b"content-security-policy" in names and b"permissions-policy" in names
    body = b"".join(m.get("body", b"") for m in sent[1:])
    assert body == b"".join(f"parca-{i};".encode() * 400 for i in range(5))
    sent = asyncio.run(_collect(app, "/dosya", [(b"accept-encoding", b"gzip"), (b"x-forwarded-proto", b"https")]))
    hdrs = dict(sent[0]["headers"])
    assert hdrs[b"strict-transport-security"] == b"max-age=15552000"
    raw = b"".join(m.get("body", b"") for m in sent[1:])
    assert (gzip.decompress(raw) if hdrs.get(b"content-encoding") == b"gzip" else raw) == blob.read_bytes()
    sent = asyncio.run(_collect(app, "/akis", [(b"accept-encoding", b"gzip")]))
    assert gzip.decompress(b"".join(m.get("body", b"") for m in sent[1:])).startswith(b"parca-0;")
