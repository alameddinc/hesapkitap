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
    assert demo_mode._shift("2026-10-31", 1) == "2026-11-01" and demo_mode._shift("2026-10", 20) == "2026-11"
    assert demo_mode._shift("2026-10-05 12:00:00", 1) == "2026-10-06 12:00:00" and demo_mode._shift("x", 3) == "x"
    assert demo_mode.shift_text("13.10.2026 ve 14/09/2026", 30) == "12.11.2026 ve 14/10/2026"


def test_visitor_gets_private_copy_with_current_dates(demo):
    assert demo.get("/api/auth/status").json()["demo"] is True
    r = demo.get("/api/statements")
    assert r.status_code == 200 and len(r.json()) == 2  # VakıfBank + Kuveyt; diğer VakıfBank örnek yükleme için
    days = demo_mode.days_since_anchor()
    assert max(s["period_end"] for s in r.json()) == demo_mode._shift("2026-10-15", days)  # Kuveyt kesimi
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


def test_stats_track_sessions_without_storing_ip(demo):
    import sqlite3

    ua = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Mobile"}
    demo.get("/", headers=ua)
    for p in ("/api/summary", "/api/plan", "/api/zakat"):
        demo.get(p, headers={**ua, "x-real-ip": "203.0.113.7"})
    demo.post("/api/demo/sample/genel", headers=ua)
    bot = TestClient(demo.app)
    bot.get("/api/summary", headers={"user-agent": "Googlebot/2.1"})
    s = demo_mode.stats(1)
    assert s["oturum"] == 1 and s["bot_oturum"] == 1 and s["mobil_%"] == 100 and s["örnek_deneyen"] == 1
    assert {"ozet", "planlama", "zekat", "ornek"} <= set(s["bölümler"])
    raw = sqlite3.connect(demo_mode.os.path.join(demo_mode._base(), "stats.db")).execute("SELECT * FROM sessions").fetchall()
    assert "203.0.113.7" not in str(raw)  # IP saklanmaz


def test_stats_endpoint_needs_key_and_does_not_create_sandbox(demo, monkeypatch):
    import os

    assert demo.get("/api/demo/stats").status_code == 404  # anahtar tanımlı değil
    monkeypatch.setenv("DEMO_STATS_KEY", "gizli-anahtar")
    assert demo.get("/api/demo/stats", headers={"x-stats-key": "yanlis"}).status_code == 404
    r = demo.get("/api/demo/stats?days=30", headers={"x-stats-key": "gizli-anahtar"})
    assert r.status_code == 200 and "oturum" in r.json() and "son_oturumlar" in r.json()
    assert not demo.cookies.get(demo_mode.COOKIE)
    assert not os.path.isdir(demo_mode._sandbox_dir()) or os.listdir(demo_mode._sandbox_dir()) == []



def test_demo_waitlist(demo):
    demo.get("/")
    r = demo.post("/api/demo/waitlist", json={"email": " Ali@Ornek.com ", "platform": "telefon", "note": "kart ekstreleri"})
    assert r.json() == {"ok": True, "new": True}
    assert demo.post("/api/demo/waitlist", json={"email": "ali@ornek.com"}).json()["new"] is False  # tekrar yazılmaz
    assert demo.post("/api/demo/waitlist", json={"email": "gecersiz"}).status_code == 422
    for i in range(2):
        demo.post("/api/demo/waitlist", json={"email": f"k{i}@ornek.com"})
    assert demo.post("/api/demo/waitlist", json={"email": "k9@ornek.com"}).status_code == 422  # oturum başı en fazla 3
    s = demo_mode.stats(7)
    assert s["bekleme_listesi"] == 3
    assert s["bekleyenler"][-1] == {"e-posta": "ali@ornek.com", "tarih": s["bekleyenler"][-1]["tarih"], "platform": "telefon",
                                    "not": "kart ekstreleri", "testçi": False}
    # önce haber ver demişti, sonra Android testine de katılıyor
    demo.post("/api/demo/waitlist", json={"email": "ali@ornek.com", "tester": True})
    assert demo_mode.stats(7)["testçi"] == 1
