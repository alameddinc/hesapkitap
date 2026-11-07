"""Topluluk fiyatları → BigQuery gecelik dışa aktarım. Ağ yok: token ve BigQuery yükleme uçları sahte istemciyle;
satırlar uydurma."""
import json
from datetime import date, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import bq, community_export as ce
from app import community_prices as cp
from app import system

TODAY = date(2026, 11, 7)
Y = (TODAY - timedelta(days=1)).isoformat()


class Resp:
    def __init__(self, status: int, data: dict):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class FakeHTTP:
    """Google token + BigQuery çok parçalı yükleme + jobs.get (ilk bakışta RUNNING, sonra DONE)."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.loads: list[dict] = []
        self.fail: int | None = None
        self.job_error: str | None = None

    def post(self, url, data=None, **kw):
        self.calls.append(("token", url, data))
        return Resp(200, {"access_token": "tok-w", "expires_in": 3600})

    def request(self, method, url, headers=None, json=None, params=None, timeout=None, content=None):
        self.calls.append((method, url, params))
        assert headers["Authorization"] == "Bearer tok-w"
        if self.fail:
            return Resp(self.fail, {"error": {"message": "Permission denied: örnek"}})
        if method == "POST":
            assert url == "https://bigquery.googleapis.com/upload/bigquery/v2/projects/hesapkitapproject/jobs"
            assert params == {"uploadType": "multipart"}
            sep = headers["Content-Type"].split("boundary=")[1]
            assert headers["Content-Type"].startswith("multipart/related")
            parts = content.decode().split(f"--{sep}")
            conf = _json.loads(parts[1].split("\r\n\r\n", 1)[1])
            data = parts[2].split("\r\n\r\n", 1)[1].rstrip("\r\n")
            rows = [_json.loads(x) for x in data.split("\n") if x]
            self.loads.append({"conf": conf, "rows": rows})
            return Resp(200, {"jobReference": conf["jobReference"], "status": {"state": "RUNNING"}})
        assert "/jobs/" in url and params == {"location": "EU"}
        st = {"state": "DONE", **({"errorResult": {"message": self.job_error}} if self.job_error else {})}
        return Resp(200, {"status": st, "statistics": {"load": {"outputRows": str(len(self.loads[-1]["rows"]))}}})


_json = json


@pytest.fixture()
def writer(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    p = tmp_path / "bq-writer.json"
    p.write_text(json.dumps({"type": "service_account", "client_email": "yazici@ornek.iam.gserviceaccount.com",
                             "private_key": pem, "private_key_id": "w1", "token_uri": "https://oauth2.googleapis.com/token"}))
    monkeypatch.setenv(bq.WRITER_ENV, str(p))
    monkeypatch.delenv(bq.CRED_ENV, raising=False)
    monkeypatch.setattr(bq, "LOAD_POLL_S", 0)
    bq.reset_cache()
    fake = FakeHTTP()
    monkeypatch.setattr(bq, "HTTP_CLIENT", fake)
    yield fake
    bq.reset_cache()


def _seed():
    milk = lambda p: [{"name": "Süt 1L", "qty": 1, "amount": p}]  # noqa: E731
    cp.contribute(1, "A101", None, milk(30.0), Y, TODAY, branch="İzmir Karşıyaka")
    cp.contribute(2, "A101", None, milk(34.0), Y, TODAY)
    cp.contribute(2, "A101", None, milk(36.0), Y, TODAY)                  # aynı kişi ikinci gözlem
    cp.contribute(3, "BİM", None, milk(20.0), Y, TODAY)                   # BİM'de tek kişi: k-anonimlik, gitmez
    cp.contribute(1, "A101", None, milk(99.0), (TODAY - timedelta(days=9)).isoformat(), TODAY)  # pencere dışı


def _local(h, m=0, day=TODAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=ce.TZ)


def test_disabled_without_writer(monkeypatch):
    monkeypatch.delenv(bq.WRITER_ENV, raising=False)
    bq.reset_cache()
    assert not ce.enabled() and ce.tick(_local(1)) is None


def test_aggregate_k_anonymity_and_no_contributor(client):
    _seed()
    rows = ce.aggregate(Y, loaded_at="2026-11-07T00:31:00+00:00")
    assert [(r["region"], r["chain"], r["group_key"], r["unit_price"], r["observations"], r["contributors"])
            for r in rows] == [(None, "A101", "sut|1l", 32.5, 3, 2)]       # İzmir'de tek kişi: il yazılmaz
    r = rows[0]
    assert r["run_date"] == Y and r["group_label"] == "Süt · 1 L" and r["category"] == "Süt ve Kahvaltılık"
    assert (r["unit_price_per"], r["per_unit"]) == (32.5, "L") and r["name_norm"] == "SUT 1 L"
    assert set(r) == {f["name"] for f in ce.SCHEMA}                        # katkı kimliği yok
    cp.contribute(5, "A101", None, [{"name": "Süt 1L", "qty": 1, "amount": 32.0}], Y, TODAY, branch="İZMİR BORNOVA")
    rows = ce.aggregate(Y)
    assert [(r["region"], r["unit_price"], r["contributors"]) for r in rows] == [(None, 35.0, 1), ("İzmir", 31.0, 2)]


def test_tick_exports_yesterday_once_idempotent(client, writer):
    _seed()
    assert ce.tick(_local(0, 20)) is None and not writer.calls            # 00:30'dan önce yok
    out = ce.tick(_local(0, 31))
    assert out == {"exported": [[Y, 1]]}                                  # öteki günler boş: iş yok
    load = writer.loads[0]
    dest = load["conf"]["configuration"]["load"]
    assert dest["destinationTable"] == {"projectId": "hesapkitapproject", "datasetId": "marketfiyati",
                                        "tableId": f"community_observations${Y.replace('-', '')}"}
    assert dest["writeDisposition"] == "WRITE_TRUNCATE" and dest["sourceFormat"] == "NEWLINE_DELIMITED_JSON"
    assert dest["createDisposition"] == "CREATE_NEVER" and dest["schema"]["fields"] == ce.SCHEMA
    assert load["conf"]["jobReference"]["location"] == "EU" and len(load["rows"]) == 1
    assert all("contributor" not in r for r in load["rows"])
    token = next(c for c in writer.calls if c[0] == "token")
    claims = json.loads(__import__("base64").urlsafe_b64decode(token[2]["assertion"].split(".")[1] + "=="))
    assert claims["scope"] == "https://www.googleapis.com/auth/bigquery"
    n = len(writer.calls)
    assert ce.tick(_local(2)) is None and len(writer.calls) == n          # bugün bitti
    assert ce.tick(_local(0, 40, TODAY + timedelta(days=1))) == {"exported": []}   # ertesi gün: değişen yok
    cp.contribute(4, "A101", None, [{"name": "Süt 1L", "qty": 1, "amount": 40.0}], Y, TODAY)  # geç kaydedilen fiş
    assert ce.tick(_local(0, 40, TODAY + timedelta(days=2))) == {"exported": [[Y, 1]]}    # gün yeniden yazılır
    assert writer.loads[-1]["rows"][0]["contributors"] == 3
    st = ce.status()
    assert (st["last_day"], st["rows"], st["last_error"], st["configured"]) == (Y, 1, None, True)


def test_failure_recorded_and_retried(client, writer):
    _seed()
    writer.fail = 403
    out = ce.tick(_local(1))
    assert out["exported"] == [] and "403" in out["error"]
    st = ce.status()
    assert st["last_error"].startswith("BigQuery 403") and st["last_day"] is None
    writer.fail = None
    assert ce.tick(_local(1, 10)) is None                                  # yeniden deneme RETRY_S sonra
    assert ce.tick(_local(1, 41)) == {"exported": [[Y, 1]]}
    assert ce.status()["last_error"] is None


def test_job_error_result(client, writer):
    _seed()
    writer.job_error = "Provided Schema does not match"
    out = ce.run(TODAY)
    assert "Schema" in out["error"]
    with system.connect() as c:
        assert not c.execute("SELECT 1 FROM community_exports WHERE day = ?", (Y,)).fetchone()   # gönderilmedi sayılır


def test_admin_status_includes_export(owner):
    st = owner.get("/api/admin/official-prices").json()
    assert st["community_export"] == {"configured": False, "last_day": None, "rows": 0, "exported_at": None,
                                      "last_run_at": None, "last_error": None, "last_error_at": None}


from tests.test_book import owner  # noqa: E402,F401  (fixture)
