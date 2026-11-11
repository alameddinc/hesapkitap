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
    assert r.status_code == 200 and len(r.json()) == 2  # Örnek Bank + Mavi Bank; ek kartlı Örnek Bank örnek yükleme için
    days = demo_mode.days_since_anchor()
    assert max(s["period_end"] for s in r.json()) == demo_mode._shift("2026-10-15", days)  # Mavi Bank kesimi
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
    assert isinstance(r["statement_id"], int)  # İlk bakış (first_look) demoda da çalışır
    assert demo.post("/api/demo/sample/vakifbank").json()[0]["status"] == "duplicate"
    g = demo.post("/api/demo/sample/genel").json()[0]
    assert g["status"] == "saved" and g["bank"] == "Yeşil Bank" and g["reconciliation_diff"] == 0
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


def test_stats_daily_platform_split(demo):
    """Ölçüm sayfası için günlük oturumlar platforma göre: mobil uygulama (X-HK-Client) iOS / Android, gerisi web."""
    TestClient(demo.app).get("/api/summary", headers={"x-hk-client": "mobile", "user-agent": "Mozilla/5.0 (iPhone) Mobile"})
    TestClient(demo.app).get("/api/summary", headers={"x-hk-client": "mobile", "user-agent": "Mozilla/5.0 (Android 15)"})
    TestClient(demo.app).get("/api/summary", headers={"user-agent": "Mozilla/5.0 (iPhone) Safari"})  # telefon tarayıcısı
    day = next(iter(demo_mode.stats(1)["günlük"].values()))
    assert day["oturum"] == 3 and day["platform"] == {"web": 1, "ios": 1, "android": 1}


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


PROVIDERS = {"Örnek Elektrik", "Örnek İnternet", "Örnek Doğalgaz"}


def _bills_check(s: dict, bills: list[dict], today: date) -> None:
    by = {p["name"]: p for p in s["providers"]}
    assert PROVIDERS <= set(by)
    for name in PROVIDERS:
        p = by[name]
        assert p["paid_months"] >= 11 and p["average"] > 0 and p["max"] and p["vs_prev_month"], name
        assert all(m["count"] <= 1 for m in p["months"]), name  # her ay tek fatura (ay sınırında üst üste binmez)
        assert p["last"]["month"] < next(b["due_date"] for b in bills if b["provider"] == name and not b["paid_at"])
    assert by["Örnek Doğalgaz"]["max"]["month"][5:7] == "01"  # doğalgaz kışın
    assert by["Örnek Elektrik"]["max"]["month"][5:7] == "08"  # elektrik yazın (klima)
    assert by["Örnek İnternet"]["vs_prev_month"]["pct"] == 0 and by["Örnek İnternet"]["max"]["amount"] == 549.90
    # her kurumun bir ödenmemiş faturası (son ödemesi önümüzde), 12 ödenmiş faturası (gider kaydına bağlı)
    unpaid = [b for b in bills if not b["paid_at"]]
    assert {b["provider"] for b in unpaid} == PROVIDERS and len(unpaid) == 3
    assert all(b["due_date"] >= today.isoformat() for b in unpaid)
    paid = [b for b in bills if b["paid_at"]]
    assert len(paid) == 36 and all(b["entry_id"] and b["paid_at"] < today.isoformat() for b in paid)


def test_demo_bills_have_twelve_months_of_history(demo):
    """Faturalar: her kurumun önceki 12 ayı ödenmiş (ortalama, en yüksek ay, önceki aya göre, grafik dolu)."""
    s = demo.get("/api/bills/summary").json()
    _bills_check(s, demo.get("/api/bills").json(), date.today())
    # fatura giderleri işlemlerde bir kez (kart satırı yok): kurum başı 12 gider kaydı, hepsi gidere sayılır
    txns = demo.get("/api/transactions").json()
    for name in PROVIDERS:
        rows = [t for t in txns if t["description"] == name]
        assert len(rows) == 12 and all(t["counts_as_spend"] for t in rows), name


@pytest.mark.parametrize("today", [date(2026, 1, 31), date(2026, 2, 27), date(2026, 7, 24), date(2026, 12, 30)])
def test_demo_bills_any_day(tmpdb, today):
    """Ay sonuna yakın günlerde de her ay tek fatura, kış/yaz en yüksek ayı doğru."""
    from app import bill_summary, db, demo

    db.init_db()
    demo.add_bills(today)
    _bills_check(bill_summary.summary(24, demo_mode._shift(today.isoformat(), 8)[:7]), db.list_bills(), today)


def test_demo_debt_plan_video_points(demo, monkeypatch):
    """Şablon v9 (tanıtım videosu): bitiş ayı aylık tutarla değişir (altın fiyatından bağımsız), Kerem söze yetişmez, Burak birikim satırı, maaş günü notu, altın borcu
    (dış fiyat yoksa demo yedek fiyatı), geçen ay başlatılmış planın takibi (gerisinde), Özet düzeninde Borç planı."""
    from app import zakat

    def offline():
        raise RuntimeError("ağ yok")

    monkeypatch.setattr(zakat, "_download_prices", offline)
    real_prices = zakat.prices

    def ends(gram: float) -> dict:
        monkeypatch.setattr(zakat, "prices", lambda: {**real_prices(), "prices": {"GRA": gram}})
        out = {}
        for a in (20000, 30000, 35000, 40000):
            r = demo.get("/api/debt-plan", params={"monthly": a}).json()
            kerem = [d["late"] for d in r["debts"] if d["title"] == "Kerem" and d.get("late") and d["late"]["kind"] == "soz"]
            if a >= 30000:  # "Söze yetişmezse" ve "Yeni tarihte anlaştık" 30–40k arası her tutarda
                assert kerem and kerem[-1].get("action"), a
            out[a] = r["debt_free"]["month"]
            assert next(d for d in r["debts"] if d["title"] == "Zeynep")["remaining"] == 2 * gram
        return out

    cheap, dear = ends(3000.0), ends(8000.0)
    assert cheap == dear, (cheap, dear)  # altının TL karşılığı borçsuz ayı kaydırmaz
    assert cheap[20000] > cheap[30000] > cheap[40000], cheap  # tutar artınca bitiş öne gelir
    monkeypatch.setattr(zakat, "prices", real_prices)
    p = demo.get("/api/debt-plan").json()
    assert p["monthly"] == 30000
    by = {d["title"]: d for d in p["debts"]}
    for a in (15000, 20000, 25000, 30000, 35000):
        b = next(d for d in demo.get("/api/debt-plan", params={"monthly": a}).json()["debts"] if d["title"] == "Burak")
        assert b["saving"] and b["saving"]["text"].startswith("Burak için biriktir: ") and "tek seferde öde" in b["saving"]["text"]
    assert by["Örnek Mobilya"]["day_note"]["text"] == "Örnek Mobilya 5'inde, maaş 15'inde: bu ödemeyi önceki aydan ayır."
    assert by["Zeynep"]["tl_text"].startswith("2 gram altın · ≈₺") and not p["excluded"]
    c = p["commitment"]
    assert c and c["month_no"] == 2 and c["status"]["key"] == "behind"
    assert c["status"]["title"] == "Planın ₺2.000 gerisindesin" and "ödersen yetişirsin" in c["status"]["text"]
    assert p["summary_card"]["eligible"]
    ui = demo.get("/api/meta").json()["ui"]
    assert "borc-plani" in ui["layout"] and "borcplani" in ui["layout_mobile"]
