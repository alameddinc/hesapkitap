"""BigQuery okuyucu (salt okuma; google-cloud kütüphanesi yok: httpx + cryptography).

Ayar (ortam):
- HESAPKITAP_BQ_CREDENTIALS: servis hesabı JSON dosyasının yolu (kapsayıcıda /app/data/secrets/bq.json). Yoksa kapalı.
- HESAPKITAP_BQ_WRITER_CREDENTIALS: yazıcı servis hesabı (kapsayıcıda /app/data/secrets/bq-writer.json; scope bigquery,
  yalnız community_observations tablosuna yazar + iş çalıştırır). Yalnız load_ndjson kullanır (app/community_export.py).
- HESAPKITAP_BQ_PROJECT (varsayılan hesapkitapproject), HESAPKITAP_BQ_DATASET (varsayılan marketfiyati),
  HESAPKITAP_BQ_LOCATION (varsayılan EU).
Erişim belirteci: servis hesabının anahtarıyla RS256 imzalı JWT (scope bigquery.readonly) → oauth2 token; süresi
dolana kadar önbellekte. Sorgu: POST …/projects/{p}/queries (adlı sorgu parametreleriyle; standart SQL), iş bitmediyse
getQueryResults ile beklenir, sayfalar pageToken ile toplanır. Anahtar ve belirteç hiçbir zaman günlüğe yazılmaz.
Maliyet: offers tablosu run_date ile bölümlü; her sorgu run_date süzgeci içermeli (app/price_import_bq.py).
"""
import base64
import json
import logging
import os
import threading
import time
from datetime import date

log = logging.getLogger("hesapkitap.bq")

SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/bigquery"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://bigquery.googleapis.com/bigquery/v2"
UPLOAD_API = "https://bigquery.googleapis.com/upload/bigquery/v2"
CRED_ENV = "HESAPKITAP_BQ_CREDENTIALS"
WRITER_ENV = "HESAPKITAP_BQ_WRITER_CREDENTIALS"
LOAD_POLL_S = 2
TIMEOUT_S = 30           # HTTP isteği
QUERY_WAIT_MS = 20_000   # sunucuda iş bekleme (istek başına)
MAX_WAIT_S = 120         # bir sorgunun toplam bekleme sınırı
PAGE_ROWS = 10_000
MAX_ROWS = 200_000       # bir sorgudan en çok satır (güvenlik sınırı)

_lock = threading.Lock()
_cache: dict = {}
HTTP_CLIENT = None  # testler sahte istemci koyar


class BQError(RuntimeError):
    pass


def project() -> str:
    return (os.environ.get("HESAPKITAP_BQ_PROJECT") or "hesapkitapproject").strip()


def dataset() -> str:
    return (os.environ.get("HESAPKITAP_BQ_DATASET") or "marketfiyati").strip()


def location() -> str:
    return (os.environ.get("HESAPKITAP_BQ_LOCATION") or "EU").strip()


def table(name: str) -> str:
    """Tam tablo adı (`proje.veri_kümesi.tablo`); adlar ortamdan, kullanıcıdan değil."""
    return f"`{project()}.{dataset()}.{name}`"


def configured() -> bool:
    """Kimlik dosyası ayarlı ve okunabilir mi?"""
    return _creds() is not None


def writer_configured() -> bool:
    """Yazıcı kimlik dosyası ayarlı ve okunabilir mi?"""
    return _creds(WRITER_ENV) is not None


def _creds(env: str = CRED_ENV) -> dict | None:
    path = (os.environ.get(env) or "").strip()
    if not path:
        return None
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None
    key = ("creds", path, mtime)
    with _lock:
        if key in _cache:
            return _cache[key]
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    out = None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        pk = load_pem_private_key(data["private_key"].encode(), password=None)
        if isinstance(pk, rsa.RSAPrivateKey) and data.get("client_email"):
            out = {"client_email": data["client_email"], "key": pk, "kid": data.get("private_key_id"),
                   "token_uri": data.get("token_uri") or TOKEN_URL}
    except Exception:  # noqa: BLE001 - bozuk dosya: kapalı sayılır (içerik günlüğe yazılmaz)
        log.warning("bq: servis hesabı dosyası okunamadı")
    with _lock:
        _cache[key] = out
    return out


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def assertion(creds: dict, now: float | None = None, scope: str = SCOPE) -> str:
    """jwt-bearer onayı: RS256, 1 saat geçerli (app/push.py fcm_assertion ile aynı kalıp)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    iat = int(now or time.time())
    head = {"alg": "RS256", "typ": "JWT", **({"kid": creds["kid"]} if creds.get("kid") else {})}
    body = {"iss": creds["client_email"], "scope": scope, "aud": creds["token_uri"], "iat": iat, "exp": iat + 3600}
    signing = f"{_b64(json.dumps(head, separators=(',', ':')).encode())}." \
              f"{_b64(json.dumps(body, separators=(',', ':')).encode())}"
    sig = creds["key"].sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{_b64(sig)}"


def _http():
    global HTTP_CLIENT
    if HTTP_CLIENT is None:
        import httpx

        HTTP_CLIENT = httpx.Client(timeout=TIMEOUT_S)
    return HTTP_CLIENT


def access_token(fresh: bool = False, writer: bool = False) -> str:
    """Erişim belirteci (writer: yazıcı hesap, scope bigquery); her hesap için ayrı önbellek."""
    creds = _creds(WRITER_ENV if writer else CRED_ENV)
    if not creds:
        raise BQError("BigQuery kimliği yok")
    slot = "token:w" if writer else "token"
    with _lock:
        c = _cache.get(slot)
        if c and not fresh and time.time() < c[0]:
            return c[1]
    r = _http().post(creds["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                               "assertion": assertion(creds, scope=WRITE_SCOPE if writer else SCOPE)})
    if r.status_code != 200:
        raise BQError(f"BigQuery erişim belirteci alınamadı ({r.status_code})")
    data = r.json()
    ttl = max(60, int(data.get("expires_in") or 3600) - 300)
    with _lock:
        _cache[slot] = (time.time() + ttl, data["access_token"])
    return data["access_token"]


def _param(name: str, v) -> dict:
    if isinstance(v, (list, tuple)):
        inner = "INT64" if v and all(isinstance(x, int) and not isinstance(x, bool) for x in v) else "STRING"
        return {"name": name, "parameterType": {"type": "ARRAY", "arrayType": {"type": inner}},
                "parameterValue": {"arrayValues": [{"value": str(x)} for x in v]}}
    typ = ("BOOL" if isinstance(v, bool) else "INT64" if isinstance(v, int) else "FLOAT64" if isinstance(v, float)
           else "DATE" if isinstance(v, date) else "STRING")
    val = ("true" if v else "false") if isinstance(v, bool) else v.isoformat() if isinstance(v, date) else str(v)
    return {"name": name, "parameterType": {"type": typ}, "parameterValue": {"value": val}}


def _cell(v, field: dict):
    if v is None:
        return None
    t = field.get("type")
    if field.get("mode") == "REPEATED":
        return [_cell(x.get("v"), {**field, "mode": None}) for x in v]
    if t in ("RECORD", "STRUCT"):
        return {f["name"]: _cell(c.get("v"), f) for f, c in zip(field.get("fields") or [], v.get("f") or [])}
    if t in ("INTEGER", "INT64"):
        return int(v)
    if t in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
        return float(v)
    if t in ("BOOLEAN", "BOOL"):
        return v in (True, "true", "TRUE")
    return v


def _rows(schema: dict | None, rows: list | None) -> list[dict]:
    fields = (schema or {}).get("fields") or []
    return [{f["name"]: _cell(c.get("v"), f) for f, c in zip(fields, r.get("f") or [])} for r in rows or []]


def _call(method: str, url: str, writer: bool = False, headers: dict | None = None, **kw) -> dict:
    import httpx

    for attempt in range(2):
        try:
            h = {"Authorization": f"Bearer {access_token(fresh=bool(attempt), writer=writer)}", **(headers or {})}
            r = _http().request(method, url, headers=h, timeout=TIMEOUT_S + QUERY_WAIT_MS / 1000, **kw)
        except httpx.HTTPError as e:
            raise BQError(f"BigQuery'ye ulaşılamadı: {type(e).__name__}") from e
        if r.status_code == 401 and not attempt:  # belirteç süresi dolmuş: bir kez yenile
            continue
        if r.status_code != 200:
            try:
                msg = (r.json().get("error") or {}).get("message") or ""
            except ValueError:
                msg = ""
            raise BQError(f"BigQuery {r.status_code}: {msg[:300]}")
        return r.json()
    raise BQError("BigQuery yetki hatası")


def query(sql: str, params: dict | None = None, max_rows: int = MAX_ROWS) -> list[dict]:
    """Standart SQL sorgusu (adlı parametreler @ad) → satırlar [{sütun: değer}] (tür dönüşümüyle). Hata: BQError."""
    body = {"query": sql, "useLegacySql": False, "location": location(), "timeoutMs": QUERY_WAIT_MS,
            "maxResults": PAGE_ROWS}
    if params:
        body["parameterMode"] = "NAMED"
        body["queryParameters"] = [_param(k, v) for k, v in params.items()]
    t0 = time.monotonic()
    res = _call("POST", f"{API}/projects/{project()}/queries", json=body)
    job = (res.get("jobReference") or {}).get("jobId")
    loc = (res.get("jobReference") or {}).get("location") or location()
    while not res.get("jobComplete"):
        if not job or time.monotonic() - t0 > MAX_WAIT_S:
            raise BQError("BigQuery sorgusu zaman aşımına uğradı")
        res = _call("GET", f"{API}/projects/{project()}/queries/{job}",
                    params={"location": loc, "timeoutMs": QUERY_WAIT_MS, "maxResults": PAGE_ROWS})
    if res.get("errors"):
        raise BQError(f"BigQuery: {str(res['errors'][0].get('message') or '')[:300]}")
    schema = res.get("schema")
    out = _rows(schema, res.get("rows"))
    token = res.get("pageToken")
    while token and len(out) < max_rows:
        if time.monotonic() - t0 > MAX_WAIT_S:
            raise BQError("BigQuery sonuçları zaman aşımına uğradı")
        res = _call("GET", f"{API}/projects/{project()}/queries/{job}",
                    params={"location": loc, "pageToken": token, "maxResults": PAGE_ROWS, "timeoutMs": QUERY_WAIT_MS})
        out += _rows(res.get("schema") or schema, res.get("rows"))
        token = res.get("pageToken")
    return out[:max_rows]


def load_ndjson(table_name: str, partition: date, rows: list[dict], schema: list[dict], job_id: str) -> dict:
    """Yazıcı hesapla bir gün bölümünü yeniden yaz: çok parçalı yükleme (iş tanımı JSON + NEWLINE_DELIMITED_JSON),
    hedef `tablo$YYYYMMDD`, WRITE_TRUNCATE (yeniden çalışırsa aynı sonuç), CREATE_NEVER; iş bitene kadar beklenir.
    Dönen: iş durumu {state, output_rows}. Hata: BQError."""
    import uuid

    conf = {"jobReference": {"projectId": project(), "jobId": job_id, "location": location()},
            "configuration": {"load": {
                "destinationTable": {"projectId": project(), "datasetId": dataset(),
                                     "tableId": f"{table_name}${partition:%Y%m%d}"},
                "sourceFormat": "NEWLINE_DELIMITED_JSON", "writeDisposition": "WRITE_TRUNCATE",
                "createDisposition": "CREATE_NEVER", "schema": {"fields": schema}}}}
    data = "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows).encode()
    sep = f"hk{uuid.uuid4().hex}"
    body = (f"--{sep}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(conf)}\r\n"
            f"--{sep}\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{sep}--\r\n".encode()
    res = _call("POST", f"{UPLOAD_API}/projects/{project()}/jobs", writer=True, params={"uploadType": "multipart"},
                headers={"Content-Type": f"multipart/related; boundary={sep}"}, content=body)
    t0 = time.monotonic()
    while (res.get("status") or {}).get("state") != "DONE":
        if time.monotonic() - t0 > MAX_WAIT_S:
            raise BQError("BigQuery yükleme işi zaman aşımına uğradı")
        time.sleep(LOAD_POLL_S)
        res = _call("GET", f"{API}/projects/{project()}/jobs/{job_id}", writer=True, params={"location": location()})
    err = (res.get("status") or {}).get("errorResult")
    if err:
        raise BQError(f"BigQuery yükleme: {str(err.get('message') or err.get('reason') or '')[:300]}")
    out = ((res.get("statistics") or {}).get("load") or {}).get("outputRows")
    return {"state": "DONE", "output_rows": int(out) if out is not None else None}
