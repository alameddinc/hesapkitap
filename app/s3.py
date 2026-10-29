"""S3 uyumlu nesne deposu istemcisi (SigV4, httpx ile; ek bağımlılık yok). Cloudflare R2, MinIO, Backblaze B2, AWS S3.

İki kullanıcı: dosya depolama (app/storage.py, HESAPKITAP_FILES_S3_*) ve sunucu yedeği (app/cloud_backup.py, BACKUP_S3_*).

Güvenlik: hata mesajlarına uç adresi (endpoint), erişim anahtarı ya da imza GİRMEZ. Mesajda yalnız işlem, HTTP durumu ve
S3 hata kodu (NoSuchKey, AccessDenied...) bulunur; nesne anahtarı yalnız `detail` alanında, sunucu günlüğü içindir.
"""
import hashlib
import hmac
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

# httpx her isteği INFO düzeyinde adresiyle loglar: depo adresi (hesap kimliği) loglara düşmesin
logging.getLogger("httpx").setLevel(logging.WARNING)

CHUNK = 1 << 20
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


class S3Error(RuntimeError):
    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status, self.code = status, code


def _q(s: str, safe: str = "-_.~") -> str:
    return quote(s, safe=safe)


def sigv4_headers(method: str, host: str, path: str, query: dict, headers: dict, payload_sha: str,
                  access_key: str, secret: str, region: str, now: datetime, service: str = "s3") -> dict:
    """AWS Signature V4 (S3). Saf fonksiyon: AWS'nin örnek vektörüyle test edilir."""
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    h = {k.lower(): str(v).strip() for k, v in headers.items()}
    h |= {"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha}
    signed = ";".join(sorted(h))
    canonical = "\n".join([
        method, _q(path, safe="/-_.~"),
        "&".join(f"{_q(k)}={_q(str(v))}" for k, v in sorted(query.items())),
        "".join(f"{k}:{h[k]}\n" for k in sorted(h)), signed, payload_sha])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (day, region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return h


def config_from_env(prefix: str, default_prefix: str = "") -> dict | None:
    """<prefix>ENDPOINT / BUCKET / KEY / SECRET (dördü birlikte) + REGION (varsayılan auto) + PREFIX.
    Hiçbiri yoksa None; bir kısmı varsa ValueError (hangilerinin eksik olduğu, değerleri değil)."""
    names = ("ENDPOINT", "BUCKET", "KEY", "SECRET")
    vals = {n: os.environ.get(prefix + n) for n in names}
    if not any(vals.values()):
        return None
    if not all(vals.values()):
        raise ValueError("S3 ayarı eksik: " + ", ".join(prefix + n for n, v in vals.items() if not v))
    return {"endpoint": vals["ENDPOINT"].rstrip("/"), "bucket": vals["BUCKET"], "key": vals["KEY"],
            "secret": vals["SECRET"], "region": os.environ.get(prefix + "REGION") or "auto",
            "prefix": os.environ.get(prefix + "PREFIX", default_prefix)}


def _error_code(body: bytes) -> str | None:
    m = re.search(rb"<Code>([A-Za-z]{1,64})</Code>", body or b"")
    return m[1].decode() if m else None


class S3:
    """Yol biçimli (path-style) adres: <endpoint>/<bucket>/<anahtar>."""

    def __init__(self, cfg: dict, client=None):
        import httpx

        self.cfg = cfg
        self.host = urlsplit(cfg["endpoint"]).netloc
        self.http = client or httpx.Client(timeout=httpx.Timeout(60.0, write=600.0))

    def _req(self, method: str, key: str = "", query: dict | None = None, headers: dict | None = None,
             payload_sha: str = EMPTY_SHA, content=None, stream: bool = False, ok404: bool = False):
        import httpx

        path = f"/{self.cfg['bucket']}" + (f"/{key}" if key else "")
        query = query or {}
        h = sigv4_headers(method, self.host, path, query, headers or {}, payload_sha, self.cfg["key"],
                          self.cfg["secret"], self.cfg["region"], datetime.now(timezone.utc))
        url = self.cfg["endpoint"] + _q(path, safe="/-_.~")
        if query:
            url += "?" + "&".join(f"{_q(k)}={_q(str(v))}" for k, v in sorted(query.items()))
        h.pop("host")
        try:
            req = self.http.build_request(method, url, headers=h, content=content)
            r = self.http.send(req, stream=stream)
        except httpx.HTTPError as e:  # mesajda adres olabilir: yalnız türü
            raise S3Error(f"S3 {method}: bağlantı hatası ({type(e).__name__})") from None
        if ok404 and r.status_code == 404:
            if stream:
                r.close()
            return None
        if r.status_code >= 300:
            body = r.read()[:500] if stream else r.content[:500]
            if stream:
                r.close()
            code = _error_code(body)
            raise S3Error(f"S3 {method}: HTTP {r.status_code}" + (f" {code}" if code else ""), r.status_code, code)
        return r

    # ---------------------------------------------------------------- bayt
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._req("PUT", key, headers={"content-length": len(data), "content-type": content_type},
                  payload_sha=hashlib.sha256(data).hexdigest(), content=data)

    def get_bytes(self, key: str) -> bytes | None:
        r = self._req("GET", key, ok404=True)
        return None if r is None else r.content

    def head(self, key: str) -> dict | None:
        r = self._req("HEAD", key, ok404=True)
        if r is None:
            return None
        return {"size": int(r.headers.get("content-length") or 0), "etag": (r.headers.get("etag") or "").strip('"')}

    def copy(self, src: str, dst: str) -> None:
        """Sunucu tarafı kopya (CopyObject); veri bu sunucudan geçmez."""
        r = self._req("PUT", dst, headers={"x-amz-copy-source": _q(f"/{self.cfg['bucket']}/{src}", safe="/-_.~")})
        if b"<Error>" in r.content[:200]:  # S3: kopya 200 dönüp gövdede hata verebilir
            raise S3Error(f"S3 COPY: {_error_code(r.content) or 'hata'}")

    # ---------------------------------------------------------------- dosya (yedek arşivleri, akış)
    def put_file(self, path: str, key: str) -> None:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
        size = os.path.getsize(path)

        def body():
            with open(path, "rb") as f:
                yield from iter(lambda: f.read(CHUNK), b"")

        self._req("PUT", key, headers={"content-length": size, "content-type": "application/octet-stream"},
                  payload_sha=h.hexdigest(), content=body())

    def get_file(self, key: str, dest: str) -> None:
        r = self._req("GET", key, stream=True)
        try:
            with open(dest + ".part", "wb") as f:
                for block in r.iter_bytes(CHUNK):
                    f.write(block)
        finally:
            r.close()
        os.replace(dest + ".part", dest)

    # ---------------------------------------------------------------- liste / silme
    def list(self, prefix: str) -> list[dict]:
        out, token = [], None
        while True:
            q = {"list-type": "2", "prefix": prefix} | ({"continuation-token": token} if token else {})
            root = ET.fromstring(self._req("GET", query=q).content)
            ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
            for c in root.findall(f"{ns}Contents"):
                out.append({"key": c.findtext(f"{ns}Key"), "size": int(c.findtext(f"{ns}Size") or 0)})
            if root.findtext(f"{ns}IsTruncated") != "true":
                return out
            token = root.findtext(f"{ns}NextContinuationToken")

    def delete(self, key: str) -> None:
        self._req("DELETE", key, ok404=True)  # zaten yoksa sorun değil
