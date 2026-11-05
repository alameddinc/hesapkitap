"""Yapay zekâ maliyeti için yönetici araçları: fiyat tablosu (flash + düşünme token'ı, bilinmeyen model, kur), kullanıcı
başı aylık maliyet listesi (sıralama, eşik) ve kötüye kullanım kısıtlaması (no_ai / günlük üst sınır / kaldırma)."""
import pytest

from tests import test_doc_reads, test_plans
from tests.test_doc_reads import _stmt
from tests.test_plans import _user

hosted = test_plans.hosted
gemini = test_doc_reads.gemini


def _call(uid, model, inp, out, th=0, n=1, kind="fis"):
    from app import ai_costs

    for _ in range(n):
        ai_costs.record(uid, kind, model, {"promptTokenCount": inp, "candidatesTokenCount": out,
                                           "thoughtsTokenCount": th})


# ---------------------------------------------------------------- fiyat

def test_price_table_flash_thoughts_and_fx():
    from app import ai_costs, receipt

    t = ai_costs.DEFAULT_PRICES
    assert t[receipt.DEFAULT_MODEL] == {"input": 0.25, "output": 1.50}
    assert t[receipt.REREAD_MODEL] == {"input": 0.75, "output": 3.75}
    assert ai_costs.DEFAULT_USD_TRY == 50.0 and ai_costs.usd_try() == 50.0
    # 1M girdi + 200k çıktı + 800k düşünme (çıktı fiyatıyla) = 0,75 + 1M × 3,75 = 4,50 $
    assert ai_costs._cost("gemini-3.5-flash", 1_000_000, 200_000, 800_000, t) == pytest.approx(4.50)


def test_unknown_model_falls_to_family():
    from app import ai_costs

    t = ai_costs.DEFAULT_PRICES
    assert ai_costs._price("gemini-9.9-flash-lite-preview", t) == t[ai_costs.LITE]
    assert ai_costs._price("gemini-9.9-flash", t) == t[ai_costs.FLASH]
    assert ai_costs._price("gemini-9-pro", t) == t[ai_costs.FLASH]  # eşleşme yok: düşük tahmin etme
    assert ai_costs._price("", t) == t[ai_costs.FLASH]


def test_old_saved_prices_keep_new_model():
    from app import ai_costs

    ai_costs.set_settings({ai_costs.LITE: {"input": 0.3, "output": 2}})
    p = ai_costs.prices()
    assert p[ai_costs.LITE] == {"input": 0.3, "output": 2.0} and p[ai_costs.FLASH] == ai_costs.DEFAULT_PRICES[ai_costs.FLASH]


def test_bench_prices_match_admin_defaults():
    from app import ai_costs, receipt_bench

    assert (receipt_bench.PRICE_IN, receipt_bench.PRICE_OUT) == (0.25, 1.50)
    assert (receipt_bench.REREAD_PRICE_IN, receipt_bench.REREAD_PRICE_OUT) == (0.75, 3.75)
    assert receipt_bench.USD_TRY == ai_costs.DEFAULT_USD_TRY


# ---------------------------------------------------------------- kullanıcı listesi

def test_user_list_sorted_threshold_and_403(hosted):
    from app import ai_costs

    c1, u1 = _user(hosted, "ayse")
    c2, u2 = _user(hosted, "mehmet")
    _call(u1["id"], ai_costs.LITE, 10_000, 1_000, n=3)                 # ucuz
    _call(u2["id"], ai_costs.FLASH, 1_000_000, 100_000, 500_000)       # 0,75 + 0,6 × 3,75 = 3 $ = 150 ₺
    _call(None, ai_costs.LITE, 100, 10)                                # hesapsız
    assert c1.get("/api/admin/ai-costs/users").status_code == 403
    r = hosted.get("/api/admin/ai-costs/users").json()
    rows = r["users"]
    assert [x["user_id"] for x in rows] == [u2["id"], u1["id"], None]
    top = rows[0]
    assert top["cost_usd"] == pytest.approx(3.0) and top["cost_try"] == pytest.approx(150.0) and top["over"] is True
    assert top["user"] == f"#{u2['id']} · me***@o***.com" and top["username"] == "mehmet@ornek.com"
    assert top["plan"] == "free" and top["calls"] == 1 and top["restriction"] is None
    assert rows[1]["over"] is False and rows[1]["calls"] == 3 and rows[2]["over"] is False
    assert (r["alert_try"], r["over_alert"]) == (50.0, 1)
    assert hosted.get("/api/admin/ai-costs").json()["over_alert"] == 1
    # eşik yönetici ayarı
    assert hosted.put("/api/admin/ai-costs", json={"alert_try": 200}).json()["alert_try"] == 200
    assert hosted.get("/api/admin/ai-costs/users").json()["over_alert"] == 0
    assert hosted.put("/api/admin/ai-costs", json={"alert_try": 0}).status_code == 422
    # önceki ay
    assert hosted.get("/api/admin/ai-costs/users?month=2000-01").json()["users"] == []


def test_user_list_reads_from_counter(hosted):
    c, u = _user(hosted)
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["status"] == "saved"  # yerel: çağrı yok
    row = next(x for x in hosted.get("/api/admin/ai-costs/users").json()["users"] if x["user_id"] == u["id"])
    assert (row["reads"], row["calls"], row["cost_try"]) == (1, 0, 0)


# ---------------------------------------------------------------- kısıtlama

def test_restriction_endpoints_admin_only_and_logged(hosted):
    c, u = _user(hosted)
    uid = u["id"]
    assert c.put(f"/api/admin/ai-restrictions/{uid}", json={"mode": "no_ai"}).status_code == 403
    assert c.get(f"/api/admin/ai-restrictions/{uid}").status_code == 403
    assert c.delete(f"/api/admin/ai-restrictions/{uid}").status_code == 403
    assert hosted.put("/api/admin/ai-restrictions/9999", json={"mode": "no_ai"}).status_code == 404
    assert hosted.put(f"/api/admin/ai-restrictions/{uid}", json={"mode": "cap"}).status_code == 422  # sınır yok
    assert hosted.put(f"/api/admin/ai-restrictions/{uid}", json={"mode": "x"}).status_code == 422
    r = hosted.put(f"/api/admin/ai-restrictions/{uid}", json={"mode": "cap", "daily_cap": 5, "note": "çok yükleme"})
    assert r.status_code == 200 and r.json()["restriction"]["daily_cap"] == 5
    row = next(x for x in hosted.get("/api/admin/ai-costs/users").json()["users"] if x["user_id"] == uid)
    assert row["restriction"]["mode"] == "cap"  # çağrısı olmasa da kısıtlı hesap listede
    d = hosted.delete(f"/api/admin/ai-restrictions/{uid}").json()
    assert d["restriction"] is None and [h["action"] for h in d["history"]] == ["remove", "set"]
    assert hosted.delete(f"/api/admin/ai-restrictions/{uid}").status_code == 404


def test_no_ai_blocks_ai_keeps_local(hosted, gemini, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_SUPPORT_EMAIL", "destek@ornek.com")
    c, u = _user(hosted)
    hosted.put(f"/api/admin/ai-restrictions/{u['id']}", json={"mode": "no_ai"})
    # yerel ayrıştırıcı (banka şablonu) çalışır
    assert c.post("/api/statements/upload", files=_stmt()).json()[0]["status"] == "saved"
    # yapay zekâ yolu: Gemini'ye gidilmez, metin sunucudan
    r = c.post("/api/receipts/parse", files={"file": ("fis.jpg", b"fis-1", "image/jpeg")})
    body = r.json()
    assert r.status_code != 200 and gemini == []
    assert body["restricted"] is True and body["manual"] is True and "quota_full" not in body
    assert body["detail"].startswith("Hesabında okuma geçici olarak sınırlandı; destek için destek@ornek.com")
    # kaldırınca açılır
    hosted.delete(f"/api/admin/ai-restrictions/{u['id']}")
    assert c.post("/api/receipts/parse", files={"file": ("fis.jpg", b"fis-1", "image/jpeg")}).status_code == 200
    assert len(gemini) == 1


def test_daily_cap_lower_than_plan_applies(hosted, gemini):
    c, u = _user(hosted)
    hosted.put(f"/api/admin/ai-restrictions/{u['id']}", json={"mode": "cap", "daily_cap": 2})
    for i in range(2):
        assert c.post("/api/receipts/parse", files={"file": ("f.jpg", f"fis-{i}".encode(), "image/jpeg")}).status_code == 200
    r = c.post("/api/receipts/parse", files={"file": ("f.jpg", b"fis-3", "image/jpeg")})
    assert r.status_code == 502 and r.json()["restricted"] is True and "sınırlandı; destek için bize yaz" in r.json()["detail"]
    assert len(gemini) == 2  # üçüncü okunmadı
    assert hosted.get(f"/api/admin/ai-restrictions/{u['id']}").json()["today_reads"] == 2
    # paket hakkı (ücretsiz 30 fiş) üst sınırdan düşükse o bağlar: sınır 1000 olsa da hak dolunca hak mesajı
    test_doc_reads._set_reads(u["id"], fis=30)
    hosted.put(f"/api/admin/ai-restrictions/{u['id']}", json={"mode": "cap", "daily_cap": 1000})
    r = c.post("/api/receipts/parse", files={"file": ("f.jpg", b"fis-4", "image/jpeg")})
    assert r.json().get("quota_full") is True and "restricted" not in r.json()
    # kaldır: sınır kalkar, paket hakkı kalır
    hosted.delete(f"/api/admin/ai-restrictions/{u['id']}")
    test_doc_reads._set_reads(u["id"], fis=0)
    assert c.post("/api/receipts/parse", files={"file": ("f.jpg", b"fis-5", "image/jpeg")}).status_code == 200


def test_admin_never_restricted(hosted, gemini):
    from app import ai_restrictions, db, system

    admin = next(u for u in system.users() if u["role"] == "admin")
    assert hosted.put(f"/api/admin/ai-restrictions/{admin['id']}", json={"mode": "no_ai"}).status_code == 404
    with system.connect() as c:  # elle yazılmış satır bile yöneticiyi kısıtlamaz
        c.execute("INSERT INTO ai_restrictions (user_id, mode, at) VALUES (?, 'no_ai', 'x')", (admin["id"],))
    with db.use_space(None, admin):
        ai_restrictions.check_ai()
