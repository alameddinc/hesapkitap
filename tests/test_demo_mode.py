"""Canlı demo: şifresiz, ziyaretçi başına kopya, tarihler bugüne kayar, yükleme/ayarlar kapalı, örnek dosyalar."""
import shutil
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import demo_mode


@pytest.fixture()
def demo(tmp_path, monkeypatch, tmpdb):
    monkeypatch.setenv("HESAPKITAP_DEMO", "1")
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    from app.api.main import app

    return TestClient(app)


def test_shift_helpers():
    assert demo_mode._shift("2026-10-31", 4) == "2027-02-28" and demo_mode._shift("2026-10", -10) == "2025-12"
    assert demo_mode._shift("2026-10-05 12:00:00", 1) == "2026-11-05 12:00:00" and demo_mode._shift("x", 3) == "x"
    assert demo_mode.shift_text("13.10.2026 ve 14/09/2026", 2) == "13.12.2026 ve 14/11/2026"


def test_visitor_gets_private_copy_with_current_dates(demo):
    assert demo.get("/api/auth/status").json()["demo"] is True
    r = demo.get("/api/statements")
    assert r.status_code == 200 and len(r.json()) == 2  # VakıfBank + Kuveyt; diğer VakıfBank örnek yükleme için
    months = demo_mode.months_since_anchor()
    assert max(s["period_end"] for s in r.json())[:7] == demo_mode._shift("2026-10", months)
    sid = demo.cookies.get(demo_mode.COOKIE)
    assert sid and demo.get("/api/demo").json()["expires_at"] == demo_mode.expires_at(sid)
    # başka ziyaretçi: ayrı kopya
    other = TestClient(demo.app)
    demo.post("/api/entries", json={"entry_date": date.today().isoformat(), "description": "Benim kaydım", "amount": 1,
                                    "kind": "gider", "payment": "nakit"})
    assert any(e["description"] == "Benim kaydım" for e in demo.get("/api/transactions").json())
    assert not any(e["description"] == "Benim kaydım" for e in other.get("/api/transactions").json())
    assert other.cookies.get(demo_mode.COOKIE) != sid


def test_closed_endpoints_and_samples(demo):
    for method, path in (("post", "/api/statements/upload"), ("post", "/api/documents/parse"), ("get", "/api/settings"),
                         ("post", "/api/bank-import/preview"), ("get", "/api/admin/users"), ("get", "/api/book"),
                         ("put", "/api/settings/config"), ("get", "/api/settings/export.zip")):
        assert getattr(demo, method)(path).status_code == 403, path
    assert demo.get("/api/book/me").status_code == 200
    r = demo.post("/api/demo/sample/vakifbank").json()[0]
    assert r["status"] == "saved" and r["reconciliation_diff"] == 0 and r["txn_count"] > 10
    assert demo.post("/api/demo/sample/vakifbank").json()[0]["status"] == "duplicate"
    g = demo.post("/api/demo/sample/genel").json()[0]
    assert g["status"] == "saved" and g["bank"] == "Garanti" and g["reconciliation_diff"] == 0
    assert len(demo.get("/api/statements").json()) == 4
    assert demo.get("/api/imports").json()[0]["label"].startswith("Ekstre: Örnek")  # geri alınabilir
    assert demo.post("/api/demo/sample/yok").status_code == 404


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract yok")
def test_sample_receipt_reads_locally(demo):
    r = demo.post("/api/demo/sample/fis-market").json()
    assert r["type"] == "receipt" and r.get("engine") == "yerel" and r["total"]


def test_expired_sandbox_and_reset(demo, monkeypatch):
    demo.get("/api/summary")
    sid = demo.cookies.get(demo_mode.COOKIE)
    monkeypatch.setattr(demo_mode, "TTL", 0)
    time.sleep(1.1)
    demo.get("/api/summary")
    assert demo.cookies.get(demo_mode.COOKIE) != sid  # süresi doldu: yeni temiz kopya
    monkeypatch.setattr(demo_mode, "TTL", 3600)
    sid2 = demo.cookies.get(demo_mode.COOKIE)
    demo.post("/api/demo/reset")
    demo.get("/api/summary")
    assert demo.cookies.get(demo_mode.COOKIE) not in (None, sid2)


def test_page_load_sets_sandbox_cookie_once(demo):
    """Sayfa açılışında çerez verilir; ardından gelen API istekleri aynı kopyayı kullanır (yeni kopya açılmaz)."""
    import os

    demo.get("/")
    sid = demo.cookies.get(demo_mode.COOKIE)
    assert sid
    for path in ("/api/summary", "/api/transactions", "/api/statements"):
        demo.get(path)
    assert demo.cookies.get(demo_mode.COOKIE) == sid
    assert os.listdir(demo_mode._sandbox_dir()) == [sid]
