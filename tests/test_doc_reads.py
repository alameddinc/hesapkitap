"""Okuma hakkı (hazır sürüm): tek sayaç (ücretsiz ayda 5, Premium/Aile günde 10 ve ayda en çok 300); okunan her belge 1 (PDF her 10 sayfa 1); yöntem ve yapay zekâ çağrısı
sayısı fark etmez; tekrar, "zaten vardı" ve başarısız okuma düşmez; hak doluyken hiçbir belge okunmaz. Kendi
sunucusunda bu sayım yoktur."""
import io
import json
from datetime import date

import httpx
import pytest

from tests import test_plans
from tests.conftest import fixture_bytes
from tests.test_plans import _user

hosted = test_plans.hosted  # hazır sürüm + yönetici oturumu (fixture)

MONTH = date.today().strftime("%Y-%m")


def _today() -> str:
    from app import doc_reads

    return doc_reads._day()


def _used(uid: int) -> int:
    from app import system

    return system.ai_used(uid)


def _set_used(uid: int, n: int) -> None:
    from app import system

    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO ai_usage (user_id, month, count) VALUES (?, ?, ?)", (uid, MONTH, n))


def _set_reads(uid: int, day: int | None = None, belge: int | None = None, fis: int | None = None,
               on: str | None = None) -> None:
    """Günlük sayacı doğrudan yaz (on: YYYY-MM-DD, varsayılan bugün, Türkiye saati). belge/fis: eski iki sayaçlı
    testlerin adı; sayaç tek olduğu için en büyüğü yazılır."""
    from app import system

    n = max(x for x in (day, belge, fis, 0) if x is not None)
    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO read_daily (user_id, day, count) VALUES (?, ?, ?)", (uid, on or _today(), n))


def _reads(c) -> dict:
    return c.get("/api/me/ai").json()


def _jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 60), "white").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def gemini(monkeypatch):
    """Sahte Gemini: her çağrı sayılır; yanıt bir fiş."""
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    calls = []
    doc = {"doc_type": "fis", "merchant": "Migros", "date": "2026-10-20", "total": 125.5, "category": "Market & Gıda",
           "items": [], "confidence": "yüksek"}

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(url)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": _json(doc)}]}}]})

    monkeypatch.setattr(receipt.httpx, "post", fake_post)
    return calls


def _json(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def _stmt(name="vakifbank_a.txt"):
    return [("files", (name.replace(".txt", ".pdf"), fixture_bytes(name), "application/pdf"))]


def test_receipt_and_multi_photo_receipt_count_one(hosted, gemini):
    c, u = _user(hosted)
    r = c.post("/api/receipts/parse", files={"file": ("fis.jpg", b"fis-1", "image/jpeg")})
    assert r.status_code == 200 and r.json()["merchant"] == "Migros"
    assert _used(u["id"]) == 1
    # çok sayfalı fiş (3 fotoğraf, tek belge): 1
    parts = [("file", (f"p{i}.jpg", f"parca-{i}".encode(), "image/jpeg")) for i in range(3)]
    assert c.post("/api/documents/parse", files=parts).status_code == 200
    assert _used(u["id"]) == 2
    # aynı fiş fotoğrafı tekrar: hak düşmez
    assert c.post("/api/receipts/parse", files={"file": ("fis.jpg", b"fis-1", "image/jpeg")}).status_code == 200
    assert _used(u["id"]) == 2


def test_statement_local_once_and_duplicate_and_broken(hosted):
    c, u = _user(hosted)
    r = c.post("/api/statements/upload", files=_stmt()).json()
    assert r[0]["status"] == "saved" and _used(u["id"]) == 1  # yerel ayrıştırıcı (yapay zekâ yok): yine 1
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["status"] == "duplicate"
    assert _used(u["id"]) == 1  # aynı ekstre ikinci kez: 0
    bad = [("files", ("bozuk.pdf", b"%PDF-1.4 bozuk", "application/pdf"))]
    assert c.post("/api/statements/upload", files=bad).json()[0]["status"] == "error"
    assert _used(u["id"]) == 1  # okunamadı: 0


def test_statement_ai_fallback_counts_one_despite_many_ai_calls(hosted, gemini, monkeypatch):
    """Ekstre bankaya özel okuyucuyla okunamadı, yapay zekâ yedeği iki çağrı yaptı: yine 1 belge."""
    from app import parsers, receipt
    from app.parsers import ai

    st = parsers.parse_text(parsers.extract_text(fixture_bytes("kuveytturk.txt")))
    monkeypatch.setattr(parsers, "parse_text", lambda text: None)

    def fake_ai(data, mime="application/pdf"):
        receipt._gemini_json("a", b"1", "text/plain")
        receipt._gemini_json("b", b"2", "text/plain")
        return st

    monkeypatch.setattr(ai, "parse_pdf_with_ai", fake_ai)
    c, u = _user(hosted)
    r = c.post("/api/statements/upload", files=_stmt("kuveytturk.txt")).json()
    assert r[0]["status"] == "saved" and r[0]["ai"] is True
    assert len(gemini) == 2 and _used(u["id"]) == 1  # çift sayım yok


def test_free_6th_read_refused_before_reading(hosted, gemini):
    from app import doc_reads

    c, u = _user(hosted)
    # ücretsiz: ayda 5; türü ne olursa olsun aynı sayaç (ekstre + 4 fiş)
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["status"] == "saved"
    r = c.post("/api/receipts/parse", files={"file": ("fis.jpg", b"fis-1", "image/jpeg")})
    assert r.status_code == 200 and r.json()["charged"] == {"daily": 1, "extra": 0}
    for i in (2, 3, 4):
        assert c.post("/api/receipts/parse", files={"file": ("fis.jpg", f"fis-{i}".encode(), "image/jpeg")}).status_code == 200
    st = _reads(c)["reads"]
    assert (st["period"], st["used"], st["limit"], st["left"], st["monthly_cap"], st["monthly_used"]) == \
        ("day", 5, 5, 0, 5, 5)
    assert st["day_limited"] is False and st["limited_by"] == "month"  # günlük ayrı sınır yok
    when = f"{doc_reads._on(doc_reads._next_month())} yenilenir."
    r = c.post("/api/statements/upload", files=_stmt("vakifbank_b.txt")).json()[0]
    assert r["status"] == "error" and r["manual"] is True and r["limit"] == 5 and r["upgrade"] is True
    assert r["message"] == (f"Bu ayın 5 okuması doldu; {when} Premium ile günde 10 okuma yapabilir ya da ek okuma "
                            "paketi alabilirsin. Bu arada harcamanı elle ekleyebilirsin.")
    assert r["texts"] == {"title": "Bu ayın 5 okuması doldu", "when": when,
                          "offer": "Premium ile günde 10 okuma yapabilir ya da ek okuma paketi alabilirsin."}
    assert (r["reason"], r["kind"], r["trial"], r["upgrade_yearly"]) == ("quota", "belge", False, False)
    assert (r["daily_limit"], r["daily_used"], r["needed"], r["limited_by"]) == (5, 5, 1, "month")
    assert r["resets_at"].endswith("+03:00") and r["resets_at"][8:19] == "01T00:00:00"  # ay başı
    assert [p["id"] for p in r["packs"]] == ["okuma_100", "okuma_300"]
    assert len(c.get("/api/statements").json()) == 1  # okunmadı
    gemini.clear()
    # fiş: okunmaz (Gemini'ye gidilmez), fotoğraf saklanır ve elle girilen gidere bağlanabilir
    r = c.post("/api/receipts/parse", files={"file": ("fis.jpg", _jpeg(), "image/jpeg")})
    body = r.json()
    assert r.status_code == 502 and body["manual"] is True and body["quota_full"] is True and gemini == []
    assert body["kind"] == "fis" and body["limit"] == 5 and "Bu ayın 5 okuması doldu" in body["detail"]
    assert body["receipt_file"] and body["receipt_file"].endswith(".jpg")
    e = c.post("/api/entries", json={"entry_date": "2026-10-20", "description": "Migros", "amount": 125.5,
                                     "kind": "gider", "category": "Market & Gıda", "payment": "nakit",
                                     "receipt_file": body["receipt_file"]})
    assert e.status_code == 200  # hak dolunca kilit yok: elle ekleme açık
    # hesap dökümü, belge, fatura: aynı biçim
    assert c.post("/api/documents/parse", files={"file": ("f.jpg", b"x", "image/jpeg")}).json()["manual"] is True
    p = c.post("/api/bank-import/preview", files={"file": ("d.csv", b"Tarih;Tutar\n01.10.2026;-10", "text/csv")})
    assert p.status_code == 422 and p.json()["manual"] is True and p.json()["kind"] == "belge"
    b = c.post("/api/bills/parse", files={"file": ("f.pdf", b"%PDF-1", "application/pdf")}).json()
    assert b["manual"] is True and "okuması doldu" in b["error"] and b["file_name"]
    assert _used(u["id"]) == 5 and gemini == []
    ai = _reads(c)
    assert (ai["used"], ai["quota"], ai["unit"]) == (5, 5, "docs")
    # geriye uyum: eski iki sayaç ikisi de aynı günlük sayacı gösterir
    assert ai["belge"] == {"used": 5, "limit": 5, "left": 0} and ai["fis"] == {"used": 5, "limit": 5, "left": 0}
    assert ai["extra"] == 0 and ai["reads"]["extra_left"] == 0


def test_premium_refusal_has_no_upgrade_hint(hosted):
    from app import system

    c, u = _user(hosted)
    system.update_user(u["id"], plan="premium", plan_until=None)
    _set_reads(u["id"], day=10)
    r = c.post("/api/statements/upload", files=_stmt()).json()[0]
    assert r["limit"] == 10 and r["upgrade"] is False and "Premium" not in r["message"]
    assert r["message"] == ("Bugünün 10 okuması doldu, yarın yenilenir. Ek okuma paketiyle hemen devam edebilirsin. "
                            "Bu arada harcamanı elle ekleyebilirsin.")
    assert r["texts"]["title"] == "Bugünün 10 okuması doldu" and r["texts"]["when"] == "Yarın yenilenir."
    assert r["day_limited"] is True and r["limited_by"] == "day" and r["monthly_cap"] == 300


def test_personal_quota_comes_first_and_admin_unlimited(hosted):
    from app import system

    c, u = _user(hosted)
    system.update_user(u["id"], ai_quota=1)
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["status"] == "saved"
    assert c.post("/api/statements/upload", files=_stmt("vakifbank_b.txt")).json()[0]["manual"] is True
    assert _reads(c)["reads"]["monthly_cap"] is None  # kişisel kota: günlük hak, aylık tavan yok
    admin = system.user_by_name("admin")
    _set_used(admin["id"], 5000)
    assert hosted.post("/api/statements/upload", files=_stmt()).json()[0]["status"] == "saved"
    assert _used(admin["id"]) == 5000


def test_family_counters_are_per_person_and_members_own_book(tmpdb, monkeypatch):
    """Aile: kişi başı günde 10 (ayda en çok 300); ortak defterde her üyenin kendi sayacı. Sahibin hakkı dolu olsa da üye okur; üyenin
    hakkı dolunca ortak defterde de üyenin kendi defterinde de reddedilir."""
    from app import auth, db, doc_reads, system

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    o = auth.create_user("anne@ornek.com", "anne-sifre-12345")
    system.update_user(o["id"], plan="aile")
    m = auth.create_user("baba@ornek.com", "baba-sifre-12345")
    with system.connect() as c:  # davetle katılmış üye
        c.execute("UPDATE users SET member_of = ? WHERE id = ?", (o["id"], m["id"]))
    o, m = system.user(o["id"]), system.user(m["id"])
    _set_reads(o["id"], day=10)
    _set_reads(m["id"], day=9)
    with db.use_space(system.user_db_path(m), m):
        doc_reads.check(b"belge-10")
        assert doc_reads.charge(b"belge-10") and not doc_reads.charge(b"belge-10")  # aynı içerik bir kez
    with db.use_space(system.user_db_path(o), o), pytest.raises(doc_reads.QuotaError, match=r"Bugünün 10 okuması doldu"):
        doc_reads.check(b"x")
    for ctx, path in ((m, system.user_db_path(m)), ({**m, "own_book": True}, system.own_db_path(m))):
        with db.use_space(path, ctx), pytest.raises(doc_reads.QuotaError, match="Bugünün 10 okuması doldu"):
            doc_reads.check(b"belge-11", doc_reads.FIS)
    assert doc_reads.status(m)["used"] == 10 and doc_reads.status(o)["left"] == 0
    assert doc_reads.status(m)["monthly_cap"] == 300  # aile: kişi başı Premium ile aynı


def test_mail_bill_counts_once_and_full_quota_skips(tmpdb, monkeypatch):
    from app import auth, inbound, receipt, system
    from tests.test_inbound import _raw, signed

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    u = auth.create_user("ayse@ornek.com", "ayse-sifre-12345")
    system.update_user(u["id"], plan="premium")
    bills = iter([{"provider": "Enerjisa", "category": "Faturalar", "amount": 600.0, "due_date": "2026-11-05"},
                  {"provider": "İGDAŞ", "category": "Faturalar", "amount": 300.0, "due_date": "2026-11-09"}])
    monkeypatch.setattr(receipt, "parse_bill", lambda data, mime: next(bills))
    to, sig = inbound.address(system.user(u["id"])), signed("enerjisa.com.tr")
    raw = _raw("efatura@enerjisa.com.tr", body="Faturanız 600 TL", mid="<f1@x>")
    assert inbound.handle(raw, to, verify=sig)["status"] == "ok"
    assert _used(u["id"]) == 1  # mail ile fatura: 1
    assert inbound.handle(raw, to, verify=sig)["status"] == "skip"
    assert _used(u["id"]) == 1  # aynı Message-ID: 0
    _set_reads(u["id"], day=10)
    r = inbound.handle(_raw("efatura@enerjisa.com.tr", body="Faturanız 300 TL", mid="<f2@x>"), to, verify=sig)
    assert (r["status"], r["detail"]) == ("skip", "Belge okuma hakkı doldu")  # günlükte: skip · belge okuma hakkı doldu
    assert _used(u["id"]) == 1


def test_mail_statement_counts_once(tmpdb, monkeypatch):
    from app import auth, inbound, system
    from tests.test_inbound import _raw, signed
    from tests.test_inbound import fixture_bytes as pdf

    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    u = auth.create_user("ali@ornek.com", "ali-sifre-12345")
    system.update_user(u["id"], plan="premium")
    to, sig = inbound.address(system.user(u["id"])), signed("vakifbank.com.tr")
    assert inbound.handle(_raw(pdf=pdf("vakifbank_a.txt"), mid="<s1@x>"), to, verify=sig)["status"] == "ok"
    assert _used(u["id"]) == 1
    r = inbound.handle(_raw(pdf=pdf("vakifbank_a.txt"), mid="<s2@x>"), to, verify=sig)  # başka mail, aynı ekstre
    assert "zaten vardı" in r["detail"] and _used(u["id"]) == 1


def test_bank_import_preview_and_run_count_once(hosted):
    c, u = _user(hosted)
    csv = "Tarih;Açıklama;Tutar;Bakiye\n01.10.2026;Market;-100,00;900,00\n02.10.2026;Maaş;5000,00;5900,00\n".encode()
    f = {"file": ("dokum.csv", csv, "text/csv")}
    p = c.post("/api/bank-import/preview", files=f).json()
    assert p["total"] == 2 and _used(u["id"]) == 1
    r = c.post("/api/bank-import", files=f, data={"mapping": json.dumps(p["mapping"]), "header_row": p["header_row"]})
    assert r.status_code == 200 and _used(u["id"]) == 1  # önizleme + içe aktarma: aynı belge, 1


def test_self_hosted_unchanged(client, monkeypatch):
    """Kendi sunucusu: belge sayımı yok; kota yapay zekâ çağrısı başına (test_users.test_config_split_and_quota)."""
    from app import auth, db, doc_reads, plans, system

    assert not plans.hosted()
    u = auth.create_user("veli", "veli-sifre-12345")
    _set_used(u["id"], 10 ** 6)
    with db.use_space(system.user_db_path(u), system.user(u["id"])):
        doc_reads.check(b"x")  # engellemez
        assert doc_reads.charge(b"x") is False
    assert _used(u["id"]) == 10 ** 6
    assert client.get("/api/me/ai").json()["unit"] == "ai"
    assert client.get("/api/meta").json()["plan"]["ai_docs"] is None
