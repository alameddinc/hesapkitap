"""Tek okuma sayacı (hazır sürüm; kural 12.11.2026: ücretsiz ayda 5, Premium ve Aile kişi başı günde 10 ve ayda en çok
300): gün dönümü (Türkiye saati), aylık tavan, kural değişince ay içi kullanım, PDF sayfa ağırlığı, paketin hakkı + ek
paket karışımı (tek işlem), tekrar muafiyeti, kişi başı sayaç."""
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
    _set_reads(u["id"], day=8)  # 2 kaldı
    with _ctx(u):
        with pytest.raises(doc_reads.QuotaError) as e:
            doc_reads.check(_pdf(25))  # 3 hak ister
        f = e.value.fields()
        assert (f["needed"], f["left"], f["pages"], f["daily_used"], f["daily_limit"]) == (3, 2, 25, 8, 10)
        assert str(e.value).startswith("Bu belge (25 sayfa) 3 okuma hakkı ister, bugün 2 hakkın kaldı. Yarın yenilenir.")
        doc_reads.check(_pdf(11))  # 2 hak: yeter
        assert doc_reads.charge(_pdf(11)) == {"daily": 2, "extra": 0}
    assert doc_reads.status(u)["used"] == 10


def test_free_long_pdf_uses_monthly_reads(hosted, clock):
    """Ücretsiz: 25 sayfalık PDF 3 hak; ayda 5 haktan 2 kaldıysa okunmaz, 11 sayfalık (2 hak) okunur."""
    from app import doc_reads

    c, u = _user(hosted)
    _set_reads(u["id"], day=2, on="2026-11-01")
    _set_reads(u["id"], day=1, on="2026-11-03")
    with _ctx(u):
        with pytest.raises(doc_reads.QuotaError) as e:
            doc_reads.check(_pdf(25))
        assert str(e.value).startswith("Bu belge (25 sayfa) 3 okuma hakkı ister, bu ay 2 hakkın kaldı. 1 Aralık'ta yenilenir.")
        assert e.value.fields()["limited_by"] == "month"
        assert doc_reads.charge(_pdf(11)) == {"daily": 2, "extra": 0}
    st = doc_reads.status(u)
    assert (st["left"], st["monthly_used"], st["monthly_cap"]) == (0, 5, 5)


# ---------------------------------------------------------------- gün dönümü ve aylık tavan

def test_premium_day_rolls_over_at_istanbul_midnight(hosted, clock):
    from app import doc_reads

    c, u = _user(hosted)
    u = _premium(u)
    clock.set("2026-11-04T23:59:00")
    with _ctx(u):
        for i in range(10):
            assert doc_reads.charge(f"belge-{i}".encode())
        with pytest.raises(doc_reads.QuotaError) as e:
            doc_reads.check(b"belge-x")
        assert str(e.value) == ("Bugünün 10 okuması doldu, yarın yenilenir. Ek okuma paketiyle hemen devam edebilirsin. "
                                "Bu arada harcamanı elle ekleyebilirsin.")
        assert e.value.fields()["upgrade"] is False and e.value.fields()["texts"]["title"] == "Bugünün 10 okuması doldu"
        st = doc_reads.status(u)
        assert st["resets_at"] == "2026-11-05T00:00:00+03:00" and st["left"] == 0 and st["limited_by"] == "day"
        assert st["day_limited"] is True and st["texts"]["when"] == "Yarın yenilenir."
        clock.set("2026-11-05T00:00:01")  # Türkiye saatiyle gece yarısı: yeni gün
        doc_reads.check(b"belge-x")
        st = doc_reads.status(u)
        assert (st["used"], st["left"], st["monthly_used"], st["monthly_cap"]) == (0, 10, 10, 300)


def test_free_has_no_daily_reset_only_monthly(hosted, clock):
    """Ücretsiz: günlük ayrı sınır yok; gece yarısı hak yenilenmez, ay başında yenilenir."""
    from app import doc_reads

    c, u = _user(hosted)
    clock.set("2026-11-04T23:59:00")
    with _ctx(u):
        for i in range(3):
            assert doc_reads.charge(f"belge-{i}".encode())
    clock.set("2026-11-05T00:00:01")
    st = doc_reads.status(u)
    assert (st["used"], st["left"], st["monthly_used"], st["limited_by"], st["day_limited"]) == (0, 2, 3, "month", False)
    with _ctx(u):
        for i in range(2):  # aynı gün 2 daha: günlük ayrı sınır olsaydı da 5 içinde
            assert doc_reads.charge(f"yeni-{i}".encode())
        with pytest.raises(doc_reads.QuotaError, match=r"^Bu ayın 5 okuması doldu; 1 Aralık'ta yenilenir\. Premium ile günde 10"):
            doc_reads.check(b"yeni-x")
    clock.set("2026-11-30T23:59:00")
    assert doc_reads.status(u)["left"] == 0
    clock.set("2026-12-01T00:00:00")  # ay başı
    assert doc_reads.status(u)["left"] == 5


def test_free_monthly_cap_5(hosted, clock):
    from app import doc_reads

    c, u = _user(hosted)
    for d, n in ((1, 1), (2, 2), (13, 1)):  # Kasım: 4
        _set_reads(u["id"], day=n, on=f"2026-11-{d:02d}")
    _set_reads(u["id"], day=9, on="2026-10-31")  # geçen ay: sayılmaz
    clock.set("2026-11-20T10:00:00")
    st = doc_reads.status(u)
    assert (st["left"], st["monthly_used"], st["monthly_cap"], st["limited_by"]) == (1, 4, 5, "month")
    with _ctx(u):
        assert doc_reads.charge(b"5.") == {"daily": 1, "extra": 0}
        with pytest.raises(doc_reads.QuotaError) as e:
            doc_reads.check(b"6.")
    msg = str(e.value)
    assert msg.startswith("Bu ayın 5 okuması doldu; 1 Aralık'ta yenilenir.") and "Premium ile günde 10 okuma" in msg
    f = e.value.fields()
    assert (f["limit"], f["limited_by"], f["monthly_used"], f["resets_at"]) == (5, "month", 5, "2026-12-01T00:00:00+03:00")
    assert f["texts"] == {"title": "Bu ayın 5 okuması doldu", "when": "1 Aralık'ta yenilenir.",
                          "offer": "Premium ile günde 10 okuma yapabilir ya da ek okuma paketi alabilirsin."}
    assert f["upgrade"] is True and [p["id"] for p in f["packs"]] == ["okuma_100", "okuma_300"]
    clock.set("2026-12-01T00:00:00")  # ay başı: tavan yenilenir
    assert doc_reads.status(u)["left"] == 5


def test_premium_monthly_cap_300(hosted, clock):
    """Premium: günde 10 ve ayda en çok 300. Ay sonuna yakın aylık kalan günlükten azsa o bağlar (ay başında yenilenir);
    eşitlikte de ay."""
    from app import doc_reads

    c, u = _user(hosted)
    u = _premium(u)
    for d in range(1, 30):  # 1–29 Kasım: 29 × 10 = 290
        _set_reads(u["id"], day=10, on=f"2026-11-{d:02d}")
    _set_reads(u["id"], day=5, on="2026-11-30")  # 295
    clock.set("2026-11-30T15:00:00")
    st = doc_reads.status(u)
    assert (st["used"], st["left"], st["monthly_used"], st["limited_by"]) == (5, 5, 295, "month")  # eşit: 5 = 5
    _set_reads(u["id"], day=3, on="2026-11-30")  # 293: aylık 7, günlük 7
    assert doc_reads.status(u)["limited_by"] == "month"
    _set_reads(u["id"], day=0, on="2026-11-30")  # 290: aylık 10, günlük 10 → yine ay
    with _ctx(u):
        for i in range(10):
            assert doc_reads.charge(f"son-{i}".encode()) == {"daily": 1, "extra": 0}
        with pytest.raises(doc_reads.QuotaError) as e:
            doc_reads.check(b"301.")
    assert str(e.value) == ("Bu ayın 300 okuması doldu; 1 Aralık'ta yenilenir. Ek okuma paketiyle hemen devam "
                            "edebilirsin. Bu arada harcamanı elle ekleyebilirsin.")
    f = e.value.fields()
    assert (f["limit"], f["limited_by"], f["upgrade"], f["resets_at"]) == (300, "month", False, "2026-12-01T00:00:00+03:00")
    clock.set("2026-12-01T09:00:00")
    st = doc_reads.status(u)
    assert (st["left"], st["monthly_used"], st["limited_by"]) == (10, 0, "day")


def test_premium_mid_month_monthly_left_smaller_than_daily(hosted, clock):
    from app import doc_reads

    c, u = _user(hosted)
    u = _premium(u)
    for d in range(1, 30):
        _set_reads(u["id"], day=10, on=f"2026-11-{d:02d}")  # 290
    _set_reads(u["id"], day=6, on="2026-11-30")  # 296: bugün 4 daha (günlük), ay 4 daha
    clock.set("2026-11-30T10:00:00")
    _set_reads(u["id"], day=2, on="2026-11-30")  # 292: günlük 8, aylık 8 → ay
    _set_reads(u["id"], day=0, on="2026-11-30")
    _set_reads(u["id"], day=5, on="2026-11-29")  # 285: günlük 10, aylık 15 → gün
    st = doc_reads.status(u)
    assert (st["left"], st["limited_by"]) == (10, "day")


def test_rule_change_counts_this_months_earlier_reads(hosted, clock):
    """Kural deploy anından geçerli, ay içindeki eski kullanım sayılır: eski kuralda (günde 3) bu ay 12 okumuş ücretsiz
    kullanıcı yeni okuma yapamaz (nazik mesaj, ek paket ve Premium önerisi); ek paketi varsa paketten okur. Eski kuralda
    bugün 25 okumuş Premium bugün okuyamaz, yarın 10."""
    from app import doc_reads

    c, u = _user(hosted)
    for d in (1, 2, 3, 4):
        _set_reads(u["id"], day=3, on=f"2026-11-{d:02d}")
    clock.set("2026-11-12T10:00:00")
    st = doc_reads.status(u)
    assert (st["left"], st["monthly_used"], st["limited_by"]) == (0, 12, "month")
    with _ctx(u):
        with pytest.raises(doc_reads.QuotaError) as e:
            doc_reads.check(b"yeni")
    assert str(e.value).startswith("Bu ayın 5 okuması doldu; 1 Aralık'ta yenilenir. Premium ile günde 10 okuma")
    _pack(u["id"], 3)
    with _ctx(u):
        doc_reads.check(b"yeni")
        assert doc_reads.charge(b"yeni") == {"daily": 0, "extra": 1}
    assert doc_reads.status(u)["monthly_used"] == 12  # ek paket aylık sayıma girmez

    c2, p = _user(hosted, "premiumcu")
    p = _premium(p)
    _set_reads(p["id"], day=25, on="2026-11-12")
    st = doc_reads.status(p)
    assert (st["used"], st["left"], st["limited_by"]) == (25, 0, "day")
    clock.set("2026-11-13T08:00:00")
    st = doc_reads.status(p)
    assert (st["left"], st["monthly_used"]) == (10, 25)


def test_meta_reads_and_daily_reads_by_plan(hosted):
    c, u = _user(hosted)
    p = c.get("/api/meta").json()["plan"]
    r = p["reads"]
    assert (r["period"], r["limit"], r["used"], r["left"], r["monthly_cap"], r["extra_left"], r["reread_cost"]) == \
        ("day", 5, 0, 5, 5, 0, 2)
    assert r["day_limited"] is False and r["limited_by"] == "month"
    d = p["daily_reads_by_plan"]
    assert d["free"] == {"day": 5, "month": 5, "day_limited": False, "text": "Ayda 5 okuma"}
    assert d["premium"] == {"day": 10, "month": 300, "day_limited": True, "text": "Günde 10, ayda en çok 300 okuma"}
    assert d["aile"] == {"day": 10, "month": 300, "day_limited": True, "text": "Kişi başı günde 10, ayda en çok 300 okuma"}
    assert p["reads_upgrade"] == "Premium ile günde 10 okuma"
    assert "daily_reads" in c.get("/api/meta").json()["capabilities"]
    t = hosted.get("/api/admin/plan-table").json()
    assert t["daily_reads"]["aile"]["day"] == 10 and t["daily_reads"]["premium"]["month"] == 300
    assert t["daily_reads"]["free"]["text"] == "Ayda 5 okuma"


def test_rule_numbers_live_in_one_place(hosted, monkeypatch):
    """Sayılar yalnız plans.py'de: değişince metinler ve durum onları izler (istemciye sabit sayı gitmez)."""
    from app import plans

    monkeypatch.setitem(plans.DAILY_READS, plans.PREMIUM, 12)
    monkeypatch.setitem(plans.MONTHLY_CAP, plans.PREMIUM, 250)
    c, u = _user(hosted)
    p = c.get("/api/meta").json()["plan"]
    assert p["daily_reads_by_plan"]["premium"]["text"] == "Günde 12, ayda en çok 250 okuma"
    assert p["reads_upgrade"] == "Premium ile günde 12 okuma"


# ---------------------------------------------------------------- ek paket

def test_daily_then_pack_in_one_charge(hosted, clock):
    from app import doc_reads, system

    c, u = _user(hosted)
    u = _premium(u)
    _pack(u["id"], 5)
    _set_reads(u["id"], day=9)  # günlükte 1 kaldı
    with _ctx(u):
        doc_reads.check(_pdf(25))  # 1 + 5 ≥ 3
        assert doc_reads.charge(_pdf(25)) == {"daily": 1, "extra": 2}
        assert doc_reads.charge(_pdf(25)) is False  # aynı belge: tekrar muafiyeti
    st = doc_reads.status(u)
    assert (st["used"], st["extra_left"], st["extra"]) == (10, 3, 3)
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


def test_pack_after_monthly_cap_for_premium(hosted, clock):
    """Premium aylık 300 dolunca (günlük hak kalsa da) ek paketten düşer; ay başında yine önce paketin hakkı."""
    from app import doc_reads

    c, u = _user(hosted)
    u = _premium(u)
    for d in (*range(1, 4), *range(5, 30)):  # 4 Kasım (bugün) hariç 28 gün × 10 = 280
        _set_reads(u["id"], day=10, on=f"2026-11-{d:02d}")
    _set_reads(u["id"], day=20, on="2026-11-30")  # ileri tarihli satır (test kolaylığı): ay toplamı 300, bugün 0
    _pack(u["id"], 4)
    st = doc_reads.status(u)
    assert (st["left"], st["used"], st["limited_by"]) == (0, 0, "month")
    with _ctx(u):
        assert doc_reads.charge(_pdf(25)) == {"daily": 0, "extra": 3}
    clock.set("2026-12-01T09:00:00")
    with _ctx(u):
        assert doc_reads.charge(b"aralik") == {"daily": 1, "extra": 0}
    assert doc_reads.credits(u["id"]) == 1


def test_reread_uses_pack_when_daily_short(hosted, clock):
    from app import doc_reads

    c, u = _user(hosted)
    _set_reads(u["id"], day=4)  # ücretsiz: ayda 5, 1 kaldı
    with _ctx(u):
        with pytest.raises(doc_reads.QuotaError, match="Yeniden okuma 2 hak ister, bu ay 1 hakkın kaldı. 1 Aralık'ta yenilenir."):
            doc_reads.check_reread()
        _pack(u["id"], 100)
        doc_reads.check_reread()
        assert doc_reads.charge_reread() == {"daily": 1, "extra": 1}
        assert doc_reads.charge_reread() == {"daily": 0, "extra": 2}  # tekrar muafiyeti yok: her yeniden okuma
    assert doc_reads.status(u)["used"] == 5 and doc_reads.credits(u["id"]) == 97


def test_charge_reports_extra_in_receipt_response(hosted, monkeypatch):
    from app import receipt
    from tests.test_doc_reads import _jpeg

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    monkeypatch.setattr(receipt, "parse_receipt", lambda *a, **k: {"merchant": "Ornek", "total": 10.0, "items": []})
    c, u = _user(hosted)
    _set_reads(u["id"], day=5)
    _pack(u["id"], 10)
    r = c.post("/api/receipts/parse", files={"file": ("f.jpg", _jpeg(), "image/jpeg")}).json()
    assert r["charged"] == {"daily": 0, "extra": 1} and _reads(c)["reads"]["extra_left"] == 9


# ---------------------------------------------------------------- kişisel kota

def test_personal_quota_is_daily_and_lifts_monthly_cap(hosted, clock):
    """Yöneticinin kişisel kotası (users.ai_quota) önce gelir: günlük hak olarak, aylık tavan yok (ücretsizde de)."""
    from app import doc_reads, system

    c, u = _user(hosted)
    for d in (1, 2, 3):
        _set_reads(u["id"], day=5, on=f"2026-11-{d:02d}")  # bu ay 15 (ücretsiz tavanın üstü)
    system.update_user(u["id"], ai_quota=2)
    u = system.user(u["id"])
    st = doc_reads.status(u)
    assert (st["limit"], st["left"], st["monthly_cap"], st["limited_by"], st["day_limited"]) == (2, 2, None, "day", True)
    with _ctx(u):
        doc_reads.charge(b"k1")
        doc_reads.charge(b"k2")
        with pytest.raises(doc_reads.QuotaError, match=r"^Bugünün 2 okuması doldu, yarın yenilenir\."):
            doc_reads.check(b"k3")


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
    assert doc_reads.status(m)["limit"] == 10 and doc_reads.status(m)["monthly_cap"] == 300


def test_family_member_monthly_cap_is_per_person(tmpdb, monkeypatch, clock):
    """Aile: aylık 300 kişi başı; sahibin ayı dolsa da üye okur."""
    from app import auth, db, doc_reads, system

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    o = auth.create_user("sahip2@ornek.com", "sahip-sifre-12345")
    system.update_user(o["id"], plan="aile")
    m = auth.create_user("uye2@ornek.com", "uye-sifre-12345")
    with system.connect() as c:
        c.execute("UPDATE users SET member_of = ? WHERE id = ?", (o["id"], m["id"]))
    o, m = system.user(o["id"]), system.user(m["id"])
    for d in (*range(1, 4), *range(5, 30)):  # 4 Kasım (bugün) hariç 28 gün × 10 = 280
        _set_reads(o["id"], day=10, on=f"2026-11-{d:02d}")
    _set_reads(o["id"], day=20, on="2026-11-30")  # sahibin ayı 300, bugün 0
    st = doc_reads.status(o)
    assert (st["left"], st["limited_by"]) == (0, "month")
    with db.use_space(system.user_db_path(m), m):
        doc_reads.check(b"uye-ay")
        assert doc_reads.charge(b"uye-ay") == {"daily": 1, "extra": 0}
