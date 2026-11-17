"""system.db kilitliyken (canlı kopya: sqlite backup okuyucusu SHARED tutar, yazıcı PENDING'de bekler) istek 500 dönmez.

17.11 olayı: GET /api/cards/summary → auth.token_user → system.user "database is locked" → 500. Düzeltme: okuma yazıcıdan
uzun bekler (READ_BUSY_S > BUSY_S), oturum "son görülme" yazması en iyi çaba (kısa bekler, kilitliyse atlanır), yine de
kilit hatası çıkarsa ara katman 503 + Retry-After döner.
"""
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    return client


def _login():
    from app.api.main import app

    r = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"},
                             headers={"x-hk-client": "mobile", "user-agent": "HesapKitap/1.0 (iPhone; iOS 18)"})
    assert r.status_code == 200
    return {"authorization": f"Bearer {r.headers['X-Session-Token']}"}


def _reader():
    """hkcopy.py gibi salt-okunur kaynak bağlantısı, açık okuma işlemi: SHARED kilit."""
    from app import system

    ro = sqlite3.connect(f"file:{system._path()}?mode=ro", uri=True, isolation_level=None)
    ro.execute("BEGIN")
    ro.execute("SELECT count(*) FROM users").fetchone()
    return ro


def _pending_writer(timeout: float) -> threading.Thread:
    """Başka bir istek yazıyor: RESERVED → commit'te PENDING (okuyucunun bırakmasını bekler, timeout sonra vazgeçer)."""
    from app import system

    def run():
        w = sqlite3.connect(system._path(), timeout=timeout)
        try:
            w.execute("UPDATE users SET username = username")
            w.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            w.close()  # system.connect gibi: hata olunca bağlantı kapanır, PENDING bırakılır

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.15)
    return t


def test_journal_mode_kaydi():
    """Şu anki varsayım: system.db rollback günlüğünde (WAL'a geçilmedi; geçilirse bu test ve yorumlar güncellenir)."""
    from app import system

    with system.connect() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_user_okumasi_bekleyen_yaziciyi_bekler(owner, monkeypatch):
    """Eski kod: okuma da 5 sn bekleyip "database is locked" atıyordu (yazıcı PENDING tutarken yeni SHARED alınamaz)."""
    from app import system

    monkeypatch.setattr(system, "READ_BUSY_S", 3.0)
    uid = system.user_by_name("admin")["id"]
    ro = _reader()
    t = _pending_writer(timeout=0.6)  # yazıcı vazgeçer (canlıda BUSY_S), okuma ondan uzun bekler
    try:
        assert system.user(uid)["username"] == "admin"
    finally:
        ro.execute("COMMIT")
        ro.close()
        t.join()


def test_oturum_dokunmasi_kilitte_atlanir(owner, monkeypatch):
    from app import sessions, system

    h = _login()
    monkeypatch.setattr(sessions, "TOUCH_EVERY", -1)  # her istekte "son görülme" yazsın
    from app.api.main import app

    ro = _reader()
    try:
        t0 = time.time()
        r = TestClient(app).get("/api/cards/summary", headers=h)
        assert r.status_code == 200, r.text
        assert time.time() - t0 < 3  # yazma kısa bekleyip atlandı (BEST_EFFORT_BUSY_S)
    finally:
        ro.execute("COMMIT")
        ro.close()
    with system.connect() as c:  # kilit kalkınca yazma yeniden çalışır
        before = c.execute("SELECT max(last_seen) FROM sessions").fetchone()[0]
    assert TestClient(app).get("/api/cards/summary", headers=h).status_code == 200
    with system.connect() as c:
        assert c.execute("SELECT max(last_seen) FROM sessions").fetchone()[0] > before


def test_tam_kilitte_500_degil_503(owner, monkeypatch):
    """Okuma da geçemezse (EXCLUSIVE kilit) yanıt 503 + Retry-After, sorun kaydı sunucu_mesgul (500 değil)."""
    from app import incidents, system
    from app.api.main import app

    h = _login()
    monkeypatch.setattr(system, "READ_BUSY_S", 0.3)
    lock = sqlite3.connect(system._path(), isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        r = TestClient(app).get("/api/cards/summary", headers=h)
    finally:
        lock.execute("COMMIT")
        lock.close()
    assert r.status_code == 503, r.text
    assert r.headers.get("retry-after")
    incidents.flush()
    with system.connect() as c:
        codes = {row[0] for row in c.execute("SELECT code FROM incident_kinds")}
    assert "sunucu_mesgul" in codes and "sunucu_hatasi" not in codes


def test_kilit_disi_operational_error_hala_500(owner, monkeypatch):
    """Yalnız kilit hatası 503'e döner; başka OperationalError (ör. tablo yok) 500 olarak kalır (gizlenmez)."""
    from app import system
    from app.api.main import app

    h = _login()
    real = system.user

    def boom(uid):
        raise sqlite3.OperationalError("no such table: users")

    monkeypatch.setattr(system, "user", boom)
    r = TestClient(app, raise_server_exceptions=False).get("/api/cards/summary", headers=h)
    monkeypatch.setattr(system, "user", real)
    assert r.status_code == 500


def test_adimli_kopya_yazmayi_bekletmez(tmp_path):
    """scripts/canli_kopya.py: kopya sürerken başka bağlantının yazması kısa sürede geçer (tek adımlı backup'ta beklerdi)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("canli_kopya", "scripts/canli_kopya.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src, dst = tmp_path / "kaynak.db", tmp_path / "kopya.db"
    c = sqlite3.connect(src)
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v BLOB)")
    c.executemany("INSERT INTO t (v) VALUES (?)", [(b"x" * 3000,) for _ in range(3000)])  # ~10 MB
    c.commit()
    c.close()
    sureler = []

    def yaz():
        w = sqlite3.connect(src, timeout=5)
        for _ in range(5):
            t0 = time.monotonic()
            w.execute("INSERT INTO t (v) VALUES (x'00')")
            w.commit()
            sureler.append(time.monotonic() - t0)
            time.sleep(0.02)
        w.close()

    t = threading.Thread(target=yaz)
    t.start()
    mod.kopyala(str(src), str(dst), pages=16, sleep_s=0.005, max_s=60)
    t.join()
    assert max(sureler) < 1.0
    n = sqlite3.connect(dst).execute("SELECT count(*) FROM t").fetchone()[0]
    assert 3000 <= n <= 3005  # tutarlı anlık görüntü (yazmalardan önce ya da sonra)
