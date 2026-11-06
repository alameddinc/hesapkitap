"""Okuma hakkı delikleri (hazır sürüm): aynı içerik önbellekten (yapay zekâ yok, hak düşmez; defter başı, 30 gün; elle
yeniden okuma atlar ve tazeler), okunamayan fotoğraf günde 5'ten sonra 1 hak, ortak defterin günlük aile tavanı (üye
döndürmek yeni hak getirmez). Kişiler, işyerleri ve tutarlar uydurma."""
import json
from datetime import timedelta

import httpx
import pytest

from tests import test_plans
from tests.conftest import fixture_bytes
from tests.test_daily_reads import clock  # noqa: F401 - fixture
from tests.test_doc_reads import _jpeg, _set_reads, _used, gemini  # noqa: F401 - fixture
from tests.test_plans import _user

hosted = test_plans.hosted


def _premium(u):
    from app import system

    system.update_user(u["id"], plan="premium", plan_until=None)
    return system.user(u["id"])


def _doc(c, data=b"fis-1", name="fis.jpg", path="/api/documents/parse"):
    r = c.post(path, files=[("file", (name, data, "image/jpeg"))])
    assert r.status_code == 200, r.text
    return r.json()


def _rows(table: str) -> list[dict]:
    from app import system

    with system.connect() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table}")]


# ---------------------------------------------------------------- 1. aynı içerik: önbellek

def test_repeat_document_is_cached_no_ai_no_charge(hosted, gemini):  # noqa: F811
    c, u = _user(hosted)
    first = _doc(c)
    assert first["charged"] == {"daily": 1, "extra": 0} and "cached" not in first and len(gemini) == 1
    again = _doc(c)
    assert again["cached"] is True and "charged" not in again and len(gemini) == 1  # yapay zekâ yok
    assert again["merchant"] == "Migros" and again["receipt_file"] == first["receipt_file"]
    assert _used(u["id"]) == 1 and c.get("/api/me/ai").json()["reads"]["used"] == 1
    # /api/receipts/parse da aynı: ilk okuma sayılır, tekrarı önbellekten
    r1 = _doc(c, b"fis-2", path="/api/receipts/parse")
    r2 = _doc(c, b"fis-2", path="/api/receipts/parse")
    assert r1["charged"]["daily"] == 1 and r2["cached"] is True and "charged" not in r2 and len(gemini) == 2
    assert r2["receipt_file"] == r1["receipt_file"] and _used(u["id"]) == 2
    row = next(r for r in _rows("doc_cache") if r["kind"] == "belge")
    assert "fis-1" not in row["result"] and json.loads(row["result"])["merchant"] == "Migros"  # yalnız sonuç JSON'u


def test_cache_served_even_when_quota_is_full(hosted, gemini):  # noqa: F811
    c, u = _user(hosted)
    _doc(c)
    _set_reads(u["id"], day=3)  # ücretsiz: bugünkü 3 hak bitti
    assert _doc(c)["cached"] is True and len(gemini) == 1
    assert c.post("/api/documents/parse", files=[("file", ("b.jpg", b"fis-yeni", "image/jpeg"))]).status_code == 502


def test_cache_is_per_book(hosted, gemini):  # noqa: F811
    a, ua = _user(hosted, "ayse")
    b, ub = _user(hosted, "mehmet")
    _doc(a)
    r = _doc(b)  # başka defter: önbellekten gelmez, okunur ve sayılır
    assert "cached" not in r and r["charged"]["daily"] == 1 and len(gemini) == 2
    assert _used(ua["id"]) == 1 and _used(ub["id"]) == 1
    assert {row["book_id"] for row in _rows("doc_cache")} == {ua["id"], ub["id"]}


def test_cache_expires_after_30_days_then_read_and_charged_again(hosted, gemini, clock):  # noqa: F811
    from app import doc_cache

    c, u = _user(hosted)
    clock.set("2026-11-04T10:00:00")
    _doc(c)
    clock.set("2026-12-03T10:00:00")  # 29 gün: hâlâ önbellekte
    assert _doc(c)["cached"] is True and len(gemini) == 1
    clock.set("2026-12-05T10:00:00")  # 31 gün: süresi doldu, yeniden okunur ve (eski tekrar kaydına rağmen) sayılır
    r = _doc(c)
    assert "cached" not in r and r["charged"] == {"daily": 1, "extra": 0} and len(gemini) == 2
    # gece işi (jobs.run_global) süresi dolanları temizler
    assert doc_cache.prune(clock.t.timestamp()) == 0
    assert doc_cache.prune((clock.t + timedelta(days=31)).timestamp()) == 1 and _rows("doc_cache") == []


def test_reread_bypasses_cache_and_refreshes_it(hosted, monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    seen = []
    fis = {"doc_type": "fis", "merchant": "Ornek", "date": "2026-10-29", "total": 285.0, "category": "Market & Gıda",
           "items": [{"name": "POSET", "amount": 285.0}], "confidence": "yüksek"}
    again = {"total": 285.0, "items": [{"name": "MADEN SUYU", "amount": 200.0}, {"name": "MUZ", "amount": 85.0}],
             "confidence": "yüksek"}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.append(json["contents"][0]["parts"][0]["text"])
        body = again if "yeniden oku" in seen[-1] else fis
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _dumps(body)}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", fake_post)
    c, u = _user(hosted)
    _premium(u)
    r = _doc(c, _jpeg())
    assert len(seen) == 1 and [i["name"] for i in r["items"]] == ["POSET"]
    out = c.post("/api/receipts/reread", json={"receipt_file": r["receipt_file"], "total": 285.0}).json()
    assert out["charged"] == {"daily": 2, "extra": 0} and len(seen) == 2  # önbellek atlandı: model çağrıldı, 2 hak
    hit = _doc(c, _jpeg())  # aynı fotoğraf yeniden: önbellek yeniden okumanın kalemleriyle
    assert hit["cached"] is True and len(seen) == 2 and [i["name"] for i in hit["items"]] == ["MADEN SUYU", "MUZ"]
    assert hit["check"]["ok"] and _used(u["id"]) == 3


def _dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def test_bill_parse_cached(hosted, monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls = []

    def fake_bill(data, mime):
        calls.append(1)
        return {"provider": "Ornek Enerji", "amount": 410.0, "due_date": "2026-11-20", "category": "Faturalar"}

    monkeypatch.setattr(receipt, "parse_bill", fake_bill)
    c, u = _user(hosted)
    pdf = b"%PDF-1.4 ornek fatura"
    r1 = c.post("/api/bills/parse", files={"file": ("f.pdf", pdf, "application/pdf")}).json()
    r2 = c.post("/api/bills/parse", files={"file": ("f.pdf", pdf, "application/pdf")}).json()
    assert r1["charged"]["daily"] == 1 and r2["cached"] is True and "charged" not in r2 and calls == [1]
    assert r2["provider"] == "Ornek Enerji" and r2["file_name"] == r1["file_name"] and _used(u["id"]) == 1


def test_dekont_ai_fields_cached(hosted, monkeypatch):
    from tests.test_dekont_parse import GELEN_AI, _gemini

    calls = _gemini(monkeypatch, GELEN_AI)
    c, u = _user(hosted)
    d1 = c.post("/api/dekont/parse", files={"file": ("d.jpg", _jpeg(), "image/jpeg")}).json()
    d2 = c.post("/api/dekont/parse", files={"file": ("d.jpg", _jpeg(), "image/jpeg")}).json()
    assert d1["charged"]["daily"] == 1 and len(calls) == 1
    assert d2["cached"] is True and "charged" not in d2 and d2["amount"] == d1["amount"] and d2["source"] == "yapay_zeka"
    assert _used(u["id"]) == 1


def test_statement_ai_fallback_cached(hosted, monkeypatch):
    from app import parsers
    from app.parsers import ai

    st = parsers.parse_text(parsers.extract_text(fixture_bytes("kuveytturk.txt")))
    monkeypatch.setattr(parsers, "parse_text", lambda text: None)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls = []

    def fake_ai(data, mime="application/pdf"):
        calls.append(1)
        return st

    monkeypatch.setattr(ai, "parse_pdf_with_ai", fake_ai)
    c, u = _user(hosted)
    files = [("files", ("k.pdf", fixture_bytes("kuveytturk.txt"), "application/pdf"))]
    r1 = c.post("/api/statements/upload", files=files).json()[0]
    assert r1["status"] == "saved" and r1["ai"] is True and calls == [1]
    c.delete(f"/api/statements/{c.get('/api/statements').json()[0]['id']}")  # silinip yeniden yüklense de
    r2 = c.post("/api/statements/upload", files=files).json()[0]
    assert r2["status"] == "saved" and r2["cached"] is True and calls == [1]  # yapay zekâ yok
    assert r2["txn_count"] == r1["txn_count"] and r2["period_end"] == r1["period_end"] and _used(u["id"]) == 1


def test_statement_dict_round_trip():
    from app import parsers
    from app.parsers.base import Statement

    st = parsers.parse_text(parsers.extract_text(fixture_bytes("kuveytturk.txt")))
    back = Statement.from_dict(json.loads(json.dumps(st.to_dict())))
    assert back == st


def test_self_hosted_has_no_cache(client, gemini):  # noqa: F811
    _doc(client)
    r = _doc(client)
    assert "cached" not in r and len(gemini) == 2 and _rows("doc_cache") == []


def test_deleting_account_removes_cache_and_counters(hosted, gemini):  # noqa: F811
    from app import system

    c, u = _user(hosted)
    _doc(c)
    assert _rows("doc_cache")
    system.delete_user(u["id"])
    assert _rows("doc_cache") == [] and _rows("read_unreadable") == []


# ---------------------------------------------------------------- 2. okunamayan fotoğraf

def test_unreadable_first_five_free_then_charged(hosted, monkeypatch):
    from app import doc_reads, receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    monkeypatch.setattr(receipt, "parse_receipt", lambda *a, **k: {"merchant": None, "total": None, "items": []})
    c, u = _user(hosted)
    _premium(u)
    for i in range(doc_reads.FREE_UNREADABLE_PER_DAY):
        r = _doc(c, _jpeg(), path="/api/receipts/parse")  # aynı bulanık fotoğraf: tekrar muafiyeti yok
        assert r["unreadable"] is True and "charged" not in r and r["message"] == "Fotoğraf net değil, fişi yakından ve düz çek."
    assert c.get("/api/me/ai").json()["reads"]["used"] == 0
    r = _doc(c, _jpeg(), path="/api/receipts/parse")  # 6.
    assert r["unreadable"] is True and r["charged"] == {"daily": 1, "extra": 0}
    assert r["message"].endswith("Bugünkü 5 ücretsiz denemeden sonra okunamayan fotoğraf da 1 hak düşer.")
    assert c.get("/api/me/ai").json()["reads"]["used"] == 1
    assert _rows("doc_cache") == []  # okunamayan sonuç saklanmaz


def test_unreadable_document_path_and_new_day(hosted, monkeypatch, clock):  # noqa: F811
    from app import doc_reads, receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    monkeypatch.setattr(receipt, "parse_document", lambda *a, **k: {"type": "receipt", "total": 0, "items": []})
    c, u = _user(hosted)
    _premium(u)
    clock.set("2026-11-04T23:00:00")
    for i in range(doc_reads.FREE_UNREADABLE_PER_DAY):
        assert "charged" not in _doc(c, f"bulanik-{i}".encode())
    assert _doc(c, b"bulanik-6")["charged"]["daily"] == 1
    clock.set("2026-11-05T08:00:00")  # yeni gün: yine 5 ücretsiz
    assert "charged" not in _doc(c, b"bulanik-7")


# ---------------------------------------------------------------- 3. aile tavanı

def _family(tmpdb, monkeypatch, members=3, own_premium=0):
    """Aile sahibi + üyeler (davetle katılmış; ilk own_premium üyenin kendi Premium'u var)."""
    from app import auth, system

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    o = auth.create_user("anne@ornek.com", "anne-sifre-12345")
    system.update_user(o["id"], plan="aile")
    ms = []
    for i in range(members):
        m = auth.create_user(f"uye{i}@ornek.com", f"uye{i}-sifre-12345")
        with system.connect() as c:
            c.execute("UPDATE users SET member_of = ? WHERE id = ?", (o["id"], m["id"]))
        if i < own_premium:
            system.update_user(m["id"], plan="premium", plan_until=None)
        ms.append(system.user(m["id"]))
    return system.user(o["id"]), ms


def _book_used(book_id: int) -> int:
    from app import doc_reads, system

    with system.connect() as c:
        r = c.execute("SELECT count FROM read_book_daily WHERE book_id = ? AND day = ?", (book_id, doc_reads._day())).fetchone()
    return int(r[0]) if r else 0


def _set_book(book_id: int, n: int) -> None:
    from app import doc_reads, system

    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO read_book_daily (book_id, day, count) VALUES (?, ?, ?)", (book_id, doc_reads._day(), n))


def _in(u, own=False):
    from app import db, system

    return db.use_space(system.own_db_path(u) if own else system.user_db_path(u), {**u, "own_book": True} if own else u)


def test_family_cap_120_with_seated_members(tmpdb, monkeypatch):
    from app import doc_reads

    o, (m1, m2, m3) = _family(tmpdb, monkeypatch)
    st = doc_reads.status(m1)
    assert (st["family_limit"], st["family_left"], st["left"]) == (120, 120, 30)
    with _in(m1):
        assert doc_reads.charge(b"belge-1") == {"daily": 1, "extra": 0}
    with _in(o):
        assert doc_reads.charge(b"belge-2")
    assert _book_used(o["id"]) == 2  # sahip ve üye aynı defter toplamına
    _set_book(o["id"], 119)
    with _in(m2):
        doc_reads.check(b"belge-3")  # 1 kaldı
        doc_reads.charge(b"belge-3")
        with pytest.raises(doc_reads.QuotaError) as e:
            doc_reads.check(b"belge-4")
    assert str(e.value).startswith("Ailenin bugünkü toplam okuma hakkı (120) doldu; yarın yenilenir.")
    f = e.value.fields()
    assert f["family_full"] is True and f["family_limit"] == 120 and f["family_left"] == 0 and f["limited_by"] == "day"
    assert f["limit"] == 120
    for u in (o, m1, m3):  # kişisel hakkı dolmamış olsa da ortak defterde kimse okuyamaz
        with _in(u), pytest.raises(doc_reads.QuotaError, match="Ailenin bugünkü toplam"):
            doc_reads.check(b"x")
    st = doc_reads.status(m3)
    assert (st["left"], st["used"], st["family_left"], st["limited_by"]) == (0, 0, 0, "day")


def test_own_premium_member_raises_family_cap(tmpdb, monkeypatch):
    from app import doc_reads

    o, ms = _family(tmpdb, monkeypatch, members=3, own_premium=2)
    assert doc_reads.status(o)["family_limit"] == 120 + 2 * 30
    _set_book(o["id"], 150)
    with _in(ms[2]):
        doc_reads.check(b"belge-151")  # 180'lik tavanın 30'u kaldı


def test_members_own_book_not_counted_nor_capped(tmpdb, monkeypatch):
    from app import doc_reads

    o, (m1, *_) = _family(tmpdb, monkeypatch)
    _set_book(o["id"], 120)  # ortak defter dolu
    with _in(m1, own=True):  # üyenin "Benim" defteri: aile tavanı yok, toplama girmez
        doc_reads.check(b"benim-1")
        assert doc_reads.charge(b"benim-1") == {"daily": 1, "extra": 0}
    assert _book_used(o["id"]) == 120 and _book_used(m1["id"]) == 0
    assert "family_limit" not in doc_reads.status({**m1, "own_book": True})
    assert doc_reads.status(m1)["family_left"] == 0


def test_rotating_invited_member_gets_no_fresh_family_reads(tmpdb, monkeypatch):
    from app import auth, doc_reads, system

    o, (m1,) = _family(tmpdb, monkeypatch, members=1)
    _set_book(o["id"], 119)
    with _in(m1):
        doc_reads.charge(b"belge-son")
    system.delete_user(m1["id"])  # sahip davetle açılan üyeyi çıkardı: hesap silindi
    assert _book_used(o["id"]) == 120  # defterin bugünkü toplamı kalır
    new = auth.create_user("yeni@ornek.com", "yeni-sifre-12345")
    with system.connect() as c:
        c.execute("UPDATE users SET member_of = ? WHERE id = ?", (o["id"], new["id"]))
    new = system.user(new["id"])
    assert doc_reads.status(new)["used"] == 0  # kişisel sayacı yeni ...
    with _in(new), pytest.raises(doc_reads.QuotaError, match=r"Ailenin bugünkü toplam okuma hakkı \(120\)"):
        doc_reads.check(b"belge-yeni")  # ... ama aile tavanı dolu


def test_pack_still_usable_when_family_cap_full(tmpdb, monkeypatch):
    from app import doc_reads

    o, (m1, *_) = _family(tmpdb, monkeypatch)
    _set_book(o["id"], 120)
    doc_reads.add_pack(m1["id"], "okuma_100", "tx-aile-1", 5)
    with _in(m1):
        doc_reads.check(b"paket-1")
        assert doc_reads.charge(b"paket-1") == {"daily": 0, "extra": 1}  # satın alınmış hak tavana girmez
    assert _book_used(o["id"]) == 120


def test_single_user_status_has_no_family_fields(hosted):
    c, _ = _user(hosted)
    st = c.get("/api/me/ai").json()["reads"]
    assert "family_limit" not in st and "family_left" not in st


def test_family_fields_in_me_ai(hosted):
    from app import system

    c, u = _user(hosted)
    system.update_user(u["id"], plan="aile", plan_until=None)
    inv = c.post("/api/book/invites", json={"note": "es"}).json()
    from fastapi.testclient import TestClient

    from app.api.main import app

    m = TestClient(app)
    assert m.post("/api/auth/register", json={"consent": test_plans.CONSENT, "code": inv["code"],
                                              "username": "es@ornek.com", "password": "es-sifre-12345"}).status_code == 200
    st = m.get("/api/me/ai").json()["reads"]
    assert (st["family_limit"], st["family_left"]) == (120, 120)
    assert (st["family_limit"], st["family_left"]) == tuple(c.get("/api/me/ai").json()["reads"][k]
                                                            for k in ("family_limit", "family_left"))


def test_capabilities_listed():
    from app import version

    assert {"save_book_choice", "read_cache"} <= set(version.CAPABILITIES)

