"""Günlük tek okuma sayacı (hazır sürüm): gün dönümü (Türkiye saati), ücretsizde aylık tavan, PDF sayfa ağırlığı,
günlük + ek paket karışımı (tek işlem), tekrar muafiyeti, kişi başı sayaç."""
from datetime import datetime

import pytest

from tests import test_plans
from tests.test_doc_reads import _reads, _set_reads
from tests.test_plans import _user

hosted = test_plans.hosted


def _pdf(pages: int) -> bytes:
    """En küçük geçerli PDF (boş sayfalar): pdfplumber sayfa sayısını okur."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [" + " ".join(f"{3 + i} 0 R" for i in range(pages)) + f"] /Count {pages} >>"]
    objs += ["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"] * pages
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


@pytest.fixture()
def clock(monkeypatch):
    """doc_reads._now sahte: clock.set("2026-11-04T23:59")."""
    from app import doc_reads

    class C:
        t = datetime(2026, 11, 4, 12, 0, tzinfo=doc_reads.TZ)

        def set(self, iso: str):
            self.t = datetime.fromisoformat(iso).replace(tzinfo=doc_reads.TZ)

    c = C()
    monkeypatch.setattr(doc_reads, "_now", lambda: c.t)
    return c


def _ctx(u):
    from app import db, system

    return db.use_space(system.user_db_path(u), system.user(u["id"]))


def _premium(u):
    from app import system

    system.update_user(u["id"], plan="premium", plan_until=None)
    return system.user(u["id"])


def _pack(uid, n):
    from app import doc_reads

    doc_reads.add_pack(uid, "okuma_100", f"tx-{uid}-{n}", n)


# ---------------------------------------------------------------- ağırlık

def test_page_weight():
    from app import doc_reads

    assert [doc_reads.weight(_pdf(n)) for n in (1, 10, 11, 20, 21, 25)] == [1, 1, 2, 2, 3, 3]
    assert doc_reads.pages(_pdf(25)) == 25
    # fotoğraf, Excel/CSV, mail anahtarı, bozuk/şifreli (sayfası okunamayan) PDF: 1
    assert [doc_reads.weight(x) for x in (b"\xff\xd8jpeg", b"Tarih;Tutar", "mail:<a@b>", b"%PDF-1.4 bozuk", None)] == [1] * 5


def test_long_pdf_needs_all_its_reads_no_partial(hosted, clock):
    from app import doc_reads

    c, u = _user(hosted)
    u = _premium(u)
    _set_reads(u["id"], day=28)  # 2 kaldı
    with _ctx(u):
        with pytest.raises(doc_reads.QuotaError) as e:
            doc_reads.check(_pdf(25))  # 3 hak ister
        f = e.value.fields()
        assert (f["needed"], f["left"], f["pages"], f["daily_used"], f["daily_limit"]) == (3, 2, 25, 28, 30)
        assert str(e.value).startswith("Bu belge (25 sayfa) 3 okuma hakkı ister, bugün 2 hakkın kaldı. Yarın yenilenir.")
        doc_reads.check(_pdf(11))  # 2 hak: yeter
        assert doc_reads.charge(_pdf(11)) == {"daily": 2, "extra": 0}
    assert doc_reads.status(u)["used"] == 30


# ---------------------------------------------------------------- gün dönümü ve aylık tavan

def test_day_rolls_over_at_istanbul_midnight(hosted, clock):
    from app import doc_reads

    c, u = _user(hosted)
    clock.set("2026-11-04T23:59:00")
    with _ctx(u):
        for i in range(3):
            assert doc_reads.charge(f"belge-{i}".encode())
        with pytest.raises(doc_reads.QuotaError, match=r"Bugünkü okuma hakkın doldu \(3/3\)\. Yarın yenilenir\."):
            doc_reads.check(b"belge-x")
        st = doc_reads.status(u)
        assert st["resets_at"] == "2026-11-05T00:00:00+03:00" and st["left"] == 0
        clock.set("2026-11-05T00:00:01")  # Türkiye saatiyle gece yarısı: yeni gün
        doc_reads.check(b"belge-x")
        st = doc_reads.status(u)
        assert (st["used"], st["left"], st["monthly_used"]) == (0, 3, 3)


def test_free_monthly_cap_40(hosted, clock):
    from app import doc_reads

    c, u = _user(hosted)
    for d in range(1, 14):  # 1–13 Kasım: 13 × 3 = 39
        _set_reads(u["id"], day=3, on=f"2026-11-{d:02d}")
    clock.set("2026-11-20T10:00:00")
    st = doc_reads.status(u)
    assert (st["left"], st["monthly_used"], st["monthly_cap"], st["limited_by"]) == (1, 39, 40, "month")
    with _ctx(u):
        assert doc_reads.charge(b"40.") == {"daily": 1, "extra": 0}
        with pytest.raises(doc_reads.QuotaError) as e:
            doc_reads.check(b"41.")
    msg = str(e.value)
    assert msg.startswith("Bu ayki 40 okuma hakkın doldu; 1 Aralık'ta yenilenir.") and "Premium'a" in msg
    f = e.value.fields()
    assert (f["limit"], f["limited_by"], f["monthly_used"], f["resets_at"]) == (40, "month", 40, "2026-12-01T00:00:00+03:00")
    clock.set("2026-12-01T00:00:00")  # ay başı: tavan yenilenir
    assert doc_reads.status(u)["left"] == 3


def test_meta_reads_and_daily_reads_by_plan(hosted):
    c, u = _user(hosted)
    p = c.get("/api/meta").json()["plan"]
    r = p["reads"]
    assert (r["period"], r["limit"], r["used"], r["left"], r["monthly_cap"], r["extra_left"], r["reread_cost"]) == \
        ("day", 3, 0, 3, 40, 0, 2)
    assert p["daily_reads_by_plan"]["premium"] == {"day": 30, "month": None, "text": "Günde 30 okuma (fiş, fatura, ekstre)"}
    assert p["daily_reads_by_plan"]["free"]["text"] == "Günde 3, ayda en çok 40 okuma"
    assert "daily_reads" in c.get("/api/meta").json()["capabilities"]
    t = hosted.get("/api/admin/plan-table").json()
    assert t["daily_reads"]["aile"]["day"] == 30


# ---------------------------------------------------------------- ek paket

def test_daily_then_pack_in_one_charge(hosted, clock):
    from app import doc_reads, system

    c, u = _user(hosted)
    u = _premium(u)
    _pack(u["id"], 5)
    _set_reads(u["id"], day=29)  # günlükte 1 kaldı
    with _ctx(u):
        doc_reads.check(_pdf(25))  # 1 + 5 ≥ 3
        assert doc_reads.charge(_pdf(25)) == {"daily": 1, "extra": 2}
        assert doc_reads.charge(_pdf(25)) is False  # aynı belge: tekrar muafiyeti
    st = doc_reads.status(u)
    assert (st["used"], st["extra_left"], st["extra"]) == (30, 3, 3)
    with system.connect() as cn:
        assert cn.execute("SELECT used FROM read_credits WHERE user_id = ?", (u["id"],)).fetchone()[0] == 2
    with _ctx(u):
        assert doc_reads.charge(b"fis") == {"daily": 0, "extra": 1}  # günlük bitti: paketten (günlük sınırı yok)
        doc_reads.check(None, needed=2)
        with pytest.raises(doc_reads.QuotaError):
            doc_reads.check(_pdf(25))  # 3 > 0 + 2
    clock.set("2026-11-05T09:00:00")  # ertesi gün: önce günlük, paket korunur
    with _ctx(u):
        assert doc_reads.charge(b"yeni") == {"daily": 1, "extra": 0}
    assert doc_reads.credits(u["id"]) == 2


def test_reread_uses_pack_when_daily_short(hosted, clock):
    from app import doc_reads

    c, u = _user(hosted)
    _set_reads(u["id"], day=2)  # ücretsiz: 1 kaldı
    with _ctx(u):
        with pytest.raises(doc_reads.QuotaError, match="Yeniden okuma 2 hak ister, bugün 1 hakkın kaldı. Yarın yenilenir."):
            doc_reads.check_reread()
        _pack(u["id"], 100)
        doc_reads.check_reread()
        assert doc_reads.charge_reread() == {"daily": 1, "extra": 1}
        assert doc_reads.charge_reread() == {"daily": 0, "extra": 2}  # tekrar muafiyeti yok: her yeniden okuma
    assert doc_reads.status(u)["used"] == 3 and doc_reads.credits(u["id"]) == 97


def test_charge_reports_extra_in_receipt_response(hosted, monkeypatch):
    from app import receipt
    from tests.test_doc_reads import _jpeg

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    monkeypatch.setattr(receipt, "parse_receipt", lambda *a, **k: {"merchant": "Ornek", "total": 10.0, "items": []})
    c, u = _user(hosted)
    _set_reads(u["id"], day=3)
    _pack(u["id"], 10)
    r = c.post("/api/receipts/parse", files={"file": ("f.jpg", _jpeg(), "image/jpeg")}).json()
    assert r["charged"] == {"daily": 0, "extra": 1} and _reads(c)["reads"]["extra_left"] == 9


# ---------------------------------------------------------------- kişi başı

def test_family_members_have_own_daily_counter(tmpdb, monkeypatch, clock):
    from app import auth, db, doc_reads, system

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    o = auth.create_user("sahip@ornek.com", "sahip-sifre-12345")
    system.update_user(o["id"], plan="aile")
    m = auth.create_user("uye@ornek.com", "uye-sifre-12345")
    with system.connect() as c:
        c.execute("UPDATE users SET member_of = ? WHERE id = ?", (o["id"], m["id"]))
    o, m = system.user(o["id"]), system.user(m["id"])
    with db.use_space(system.user_db_path(o), o):
        assert doc_reads.charge(b"ortak-belge")
    with db.use_space(system.user_db_path(m), m):
        assert doc_reads.charge(b"ortak-belge") is False  # aynı defterde aynı içerik: sayılmaz
        assert doc_reads.charge(b"uye-belge")
    assert (doc_reads.status(o)["used"], doc_reads.status(m)["used"]) == (1, 1)
    assert doc_reads.status(m)["limit"] == 30 and doc_reads.status(m)["monthly_cap"] is None
