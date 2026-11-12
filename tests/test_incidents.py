"""Sorun kaydı (app/incidents.py, yetenek "incidents"): kişisel veri süzgeci, sunucu hataları (5xx, iş kuralı 422,
doğrulama 422 kaydedilmez, okunamayan belge), istemci olayları (oturumsuz, sınır, bilinmeyen kod), gün içi birleştirme,
yönetici listesi/ayrıntı/zaman çizelgesi, uyarı (yeni tür, ani artış, saatlik sınır), "düzeltildi" haberi (bir kez,
güncelleme eki, tercih), yeniden açılma, saklama ve hesap silme. Kişiler ve metinler uydurma."""
import logging
import time

import pytest

from tests.test_book import _member, owner  # noqa: F401 - fixture
from tests.test_push import nokeys  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _clean():
    from app import incidents

    incidents._buf.clear()
    incidents._rate.clear()
    incidents._alert_times.clear()
    incidents._dev_cache.clear()
    yield
    incidents._buf.clear()


def _kinds():
    from app import incidents, system

    incidents.flush()
    with system.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM incident_kinds ORDER BY id")]


def _rows():
    from app import incidents, system

    incidents.flush()
    with system.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM incidents ORDER BY kind_id, user_id")]


def _uid(name):
    from app import system

    return system.user_by_name(name)["id"]


def _sistem(uid):
    from app import push

    return [x["payload"] for x in push.queue(uid) if x["payload"]["kind"] == "sistem"]


# ---------------------------------------------------------------- süzgeçler

def test_kisa_ozet_drops_personal_data():
    from app import incidents

    s = incidents.kisa_ozet("Ahmet'in \"Market Kira\" kaydı 1.234,50 TL ali@ornek.com https://x.com/a?b=1 "
                            "TR00 0000 0000 0000 0000 0000 00 kart 4111 1111 1111 1111\nTraceback satırı")
    assert "Market" not in s and "1.234" not in s and "ali@" not in s and "x.com" not in s and "4111" not in s
    assert "Traceback" not in s and "TR00" not in s
    assert len(incidents.kisa_ozet("x" * 500)) == 160
    assert incidents.kisa_ozet("   ") is None and incidents.kisa_ozet(None) is None
    # dosya yolu (kişinin dosya adı) atılır
    assert "ekstrem" not in incidents.kisa_ozet("açılamadı /Users/ali/Belgeler/ekstrem.pdf")


@pytest.mark.parametrize("route,want", [
    ("#/diger/kumbara/12?x=1", "diger/kumbara/:id"), ("/islemler", "islemler"), ("", "-"), (None, "-"),
    ("#/diger/ekstreler/2026-10", "diger/ekstreler/:id"), ("#/diger/<script>", "diger/script"),
])
def test_ekran(route, want):
    from app import incidents

    assert incidents.ekran(route) == want


def test_surum():
    from app import incidents

    assert incidents.surum("1.0.8 (45)") == "1.0.8 (45)" and incidents.surum("1.0.8") == "1.0.8"
    assert incidents.surum("bilmem") is None and incidents.surum(None) is None


# ---------------------------------------------------------------- sunucu

def test_server_error_recorded_with_route_template(owner, monkeypatch):  # noqa: F811
    from app import db

    def boom():
        raise KeyError("gizli açıklama")

    monkeypatch.setattr(db, "list_recurring", boom)
    r = owner.get("/api/recurring")
    assert r.status_code == 500
    k = _kinds()
    assert [(x["code"], x["place"]) for x in k] == [("sunucu_hatasi", "GET /api/recurring")]
    assert "KeyError" in k[0]["sample"] and "gizli" not in k[0]["sample"]
    rows = _rows()
    assert rows[0]["user_id"] == _uid("admin") and rows[0]["count"] == 1 and rows[0]["platform"] == "web"


def test_validation_422_not_recorded_business_422_is(owner):  # noqa: F811
    r = owner.post("/api/entries", json={"entry_date": "tarih değil", "amount": "abc"})
    assert r.status_code == 422
    assert _kinds() == []
    r = owner.post("/api/subcategories", json={"name": "Ek", "category": "Kategorisiz"})
    assert r.status_code == 422
    k = _kinds()
    assert [(x["code"], x["place"]) for x in k] == [("is_kurali", "POST /api/subcategories")]
    assert "Kategorisiz altına" in k[0]["sample"]
    # 404 ve 401 kaydedilmez
    owner.get("/api/olmayan-uc")
    assert len(_kinds()) == 1


def test_same_user_code_day_merges(owner):  # noqa: F811
    for _ in range(3):
        owner.post("/api/subcategories", json={"name": "Ek", "category": "Kategorisiz"})
    rows = _rows()
    assert len(rows) == 1 and rows[0]["count"] == 3


def test_upload_failures_have_reason_codes(owner):  # noqa: F811
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    r = owner.post("/api/statements/upload", files=[("files", ("foto.jpg", jpg, "image/jpeg"))])
    assert r.status_code == 200 and r.json()[0]["status"] == "error" and "_inc" not in r.json()[0]
    r = owner.post("/api/statements/upload", files=[("files", ("bos.pdf", b"%PDF-1.4 bozuk", "application/pdf"))])
    assert r.json()[0]["status"] == "error"
    codes = {x["code"]: x["place"] for x in _kinds()}
    assert codes == {"ekstre_yerine_foto": "POST /api/statements/upload", "ekstre_okunamadi": "POST /api/statements/upload"}
    # fatura uç: yanlış tür 422 → okuma türü (iş kuralı değil)
    r = owner.post("/api/bills/parse", files={"file": ("a.txt", b"metin", "text/plain")})
    assert r.status_code == 422
    assert "fatura_okunamadi" in {x["code"] for x in _kinds()}


def test_quota_full_noted(owner, monkeypatch):  # noqa: F811
    from app import incidents

    incidents.note("okuma_hakki_doldu")  # istek dışında: oturumsuz kayıt
    assert [x["code"] for x in _kinds()] == ["okuma_hakki_doldu"]


def test_job_errors_recorded():
    from app import incidents

    incidents.install()
    log = logging.getLogger("ekstre.jobs")
    try:
        raise RuntimeError("u12 defteri: Ahmet")
    except RuntimeError:
        log.exception("job %s failed", "backup")
    log.error("telegram inbox failed")
    k = _kinds()
    assert {(x["code"], x["place"]) for x in k} == {("is_hatasi", "iş: backup"), ("is_hatasi", "iş: telegram inbox failed")}
    assert all("Ahmet" not in (x["sample"] or "") for x in k)


def test_disabled_by_env(owner, monkeypatch):  # noqa: F811
    monkeypatch.setenv("HESAPKITAP_INCIDENTS", "0")
    owner.post("/api/subcategories", json={"name": "Ek", "category": "Kategorisiz"})
    assert _kinds() == []


# ---------------------------------------------------------------- istemci

def test_client_events_anonymous_and_filtered(client, monkeypatch):
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    from app import auth

    auth.create_admin("admin", "yonetici-sifre-1")
    ev = {"code": "js_hatasi", "screen": "#/giris", "detail": "TypeError: x.map is not a function at 'Ayşe' 12",
          "app_version": "1.0.8 (45)", "platform": "ios"}
    r = client.post("/api/incidents/client", json={"events": [ev, {"code": "uydurma"}, {"code": "sunucu_hatasi"}]})
    assert r.status_code == 200 and r.json() == {"ok": True, "accepted": 1}
    k = _kinds()
    assert [(x["code"], x["place"]) for x in k] == [("js_hatasi", "giris")]
    assert "Ayşe" not in k[0]["sample"] and "TypeError" in k[0]["sample"]
    rows = _rows()
    assert rows[0]["user_id"] == 0 and rows[0]["app_version"] == "1.0.8 (45)" and rows[0]["platform"] == "ios"
    # oturumsuz sınır: saatte 20
    for _ in range(3):
        client.post("/api/incidents/client", json={"events": [{"code": "ag_zaman_asimi", "screen": "ozet"}] * 10})
    assert sum(x["count"] for x in _rows()) == 20
    # en çok 10 olay / istek
    assert client.post("/api/incidents/client", json={"events": [ev] * 11}).status_code == 422


def test_client_events_user_and_plugin(owner):  # noqa: F811
    m = _member(owner, "esim")
    r = m.post("/api/incidents/client", json={"events": [
        {"code": "eklenti_hatasi", "screen": "#/ekle", "plugin": "Kamera!", "detail": "Camera permission denied"},
        {"code": "ekran_coktu", "screen": "#/diger/kumbara/3", "app_version": "1.0.7"}]})
    assert r.json()["accepted"] == 2
    k = {x["code"]: x["place"] for x in _kinds()}
    assert k == {"eklenti_hatasi": "ekle · kamera", "ekran_coktu": "diger/kumbara/:id"}
    assert {x["user_id"] for x in _rows()} == {_uid("esim")}


# ---------------------------------------------------------------- yönetici

def test_admin_list_detail_timeline(owner):  # noqa: F811
    from app import incidents

    m = _member(owner, "esim", "full")
    m.post("/api/subcategories", json={"name": "Ek", "category": "Kategorisiz"})
    m.post("/api/subcategories", json={"name": "Ek", "category": "Kategorisiz"})
    owner.post("/api/subcategories", json={"name": "Ek", "category": "Kategorisiz"})
    incidents.record("js_hatasi", "ozet", uid=_uid("esim"), app_version="1.0.8 (45)", platform="ios")
    incidents.record("js_hatasi", "ozet", app_version="1.0.7", platform="android")

    assert m.get("/api/admin/incidents").status_code == 403
    assert m.patch("/api/admin/incidents/1", json={"status": "inceleniyor"}).status_code == 403
    out = owner.get("/api/admin/incidents").json()
    assert out["counts"] == {"yeni": 2, "inceleniyor": 0, "duzeltildi": 0}
    assert out["thresholds"]["spike_users"] == 5
    js = next(x for x in out["items"] if x["code"] == "js_hatasi")
    assert out["items"][0]["code"] == "js_hatasi"  # kritik önce
    assert js["users"] == 1 and js["count"] == 2 and js["anon"] == 1 and js["label"] == "Uygulama hatası (yakalanmamış)"
    assert {v["key"] for v in js["versions"]} == {"1.0.8 (45)", "1.0.7"}
    assert {p["key"] for p in js["platforms"]} == {"ios", "android"}
    ik = next(x for x in out["items"] if x["code"] == "is_kurali")
    assert ik["users"] == 2 and ik["count"] == 3 and ik["notifiable"] is True

    d = owner.get(f"/api/admin/incidents/{ik['id']}").json()
    assert d["days"][0]["count"] == 3 and {u["username"] for u in d["people"]} == {"admin", "esim"} and d["users"] == 2
    assert owner.get("/api/admin/incidents/9999").status_code == 404

    t = owner.get(f"/api/admin/incidents/users/{_uid('esim')}").json()
    assert t["user"]["username"] == "esim" and {x["code"] for x in t["items"]} == {"is_kurali", "js_hatasi"}

    p = owner.patch(f"/api/admin/incidents/{ik['id']}", json={"status": "inceleniyor", "note": "commit abc123'te bakılıyor"})
    assert p.status_code == 200 and p.json()["kind"]["status"] == "inceleniyor" and p.json()["notified"] is None
    assert owner.get("/api/admin/incidents", params={"status": "inceleniyor"}).json()["items"][0]["id"] == ik["id"]
    assert owner.get("/api/admin/incidents", params={"severity": "kritik"}).json()["items"][0]["code"] == "js_hatasi"
    assert owner.patch(f"/api/admin/incidents/{ik['id']}", json={"status": "bitti"}).status_code == 422
    assert owner.patch(f"/api/admin/incidents/{ik['id']}", json={"fixed_version": "yarın"}).status_code == 422
    assert owner.patch(f"/api/admin/incidents/{ik['id']}", json={"user_text": "Fixed the bug"}).status_code == 422


def test_fixed_notifies_users_once_with_update_hint(owner, nokeys):  # noqa: F811
    from app import incidents, push, system

    ayse = system.create_user("ayse@ornek.com", None)
    can = system.create_user("can@ornek.com", None)
    deniz = system.create_user("deniz@ornek.com", None)
    push.register(ayse["id"], "fcm-ayse-belgesi-0001-abcdefghij", "android", app_version="1.0.8 (45)")
    push.register(can["id"], "fcm-can-belgesi-0002-abcdefghijk", "android", app_version="1.0.9 (46)")
    push.set_prefs(deniz["id"], {"types": {"duzeltme": False}})
    for u, v in ((ayse, "1.0.8 (45)"), (can, "1.0.9 (46)"), (deniz, "1.0.8")):
        incidents.record("fis_okunamadi", "POST /api/receipts/parse", uid=u["id"], app_version=v, platform="android")
    incidents.record("fis_okunamadi", "POST /api/receipts/parse")  # oturumsuz: haber yok
    kid = _kinds()[0]["id"]
    r = owner.patch(f"/api/admin/incidents/{kid}", json={"status": "duzeltildi", "fixed_version": "1.0.9",
                                                         "note": "şablon düzeltildi"})
    assert r.status_code == 200 and r.json()["notified"] == 2  # deniz kapattı
    qa = [x["payload"] for x in push.queue(ayse["id"]) if x["payload"]["kind"] == "duzeltme"]
    qc = [x["payload"] for x in push.queue(can["id"]) if x["payload"]["kind"] == "duzeltme"]
    assert qa[0]["body"] == "Fiş okuma sorununu düzelttik; tekrar deneyebilirsin. Önce uygulamayı güncelle."
    assert qc[0]["body"] == "Fiş okuma sorununu düzelttik; tekrar deneyebilirsin." and qc[0]["route"] == "#/ekle"
    assert qa[0]["title"] == "Sorun düzeltildi"
    # aynı durumu yeniden kaydetmek ikinci haber göndermez
    assert owner.patch(f"/api/admin/incidents/{kid}", json={"status": "duzeltildi"}).json()["notified"] is None
    assert len([x for x in push.queue(ayse["id"]) if x["payload"]["kind"] == "duzeltme"]) == 1
    assert owner.get("/api/push/prefs").json()["labels"]["duzeltme"] == "Düzelttiğimiz sorunlar"
    k = owner.get(f"/api/admin/incidents/{kid}").json()
    assert k["notified"] == 2 and k["fixed_version"] == "1.0.9" and k["status"] == "duzeltildi"


def test_fixed_custom_text_and_no_notify(owner, nokeys):  # noqa: F811
    from app import incidents, push, system

    ali = system.create_user("ali@ornek.com", None)
    incidents.record("sunucu_hatasi", "GET /api/summary", uid=ali["id"], platform="web")
    incidents.record("cok_istek", "POST /api/auth/login", uid=ali["id"], platform="web")
    k = {x["code"]: x["id"] for x in _kinds()}
    r = owner.patch(f"/api/admin/incidents/{k['cok_istek']}", json={"status": "duzeltildi"})
    assert r.json()["notified"] == 0  # haber metni olmayan tür
    r = owner.patch(f"/api/admin/incidents/{k['sunucu_hatasi']}",
                    json={"status": "duzeltildi", "user_text": "Özet sayfasındaki açılış hatasını düzelttik"})
    assert r.json()["notified"] == 1
    q = [x["payload"] for x in push.queue(ali["id"]) if x["payload"]["kind"] == "duzeltme"]
    assert q[0]["body"] == "Özet sayfasındaki açılış hatasını düzelttik."


def test_reopen_only_on_fixed_version_or_later(owner, nokeys, monkeypatch):  # noqa: F811
    from app import incidents

    incidents.record("ekran_coktu", "islemler", uid=_uid("admin"), app_version="1.0.8", platform="ios")
    kid = _kinds()[0]["id"]
    owner.patch(f"/api/admin/incidents/{kid}", json={"status": "duzeltildi", "fixed_version": "1.0.9",
                                                     "notify_users": False})
    later = time.time() + 5
    incidents.record("ekran_coktu", "islemler", app_version="1.0.8", platform="ios", at=later)  # eski uygulama
    assert _kinds()[0]["status"] == "duzeltildi"
    incidents._alert_times.clear()
    incidents.record("ekran_coktu", "islemler", app_version="1.0.9 (46)", platform="ios", at=later + 1)
    k = _kinds()[0]
    assert k["status"] == "yeni" and k["reopened"] == 1


# ---------------------------------------------------------------- uyarı

def test_alert_new_kritik_once_per_hour(owner, nokeys):  # noqa: F811
    from app import incidents

    admin = _uid("admin")
    incidents.record("js_hatasi", "ozet", platform="web")
    incidents.flush()
    q = _sistem(admin)
    assert len(q) == 1 and q[0]["title"] == "Yeni hata türü: Uygulama hatası (yakalanmamış)"
    assert q[0]["route"] == "#/diger/ayarlar/sorunlar" and "ozet" in q[0]["body"]
    # aynı tür yine: saat dolmadan uyarı yok
    for i in range(6):
        incidents.record("js_hatasi", "ozet", uid=1000 + i, platform="web")
    incidents.flush()
    assert len(_sistem(admin)) == 1
    # uyari ağırlığında yeni tür: tek kişi → uyarı yok
    incidents.record("fis_okunamadi", "POST /api/receipts/parse", uid=1)
    incidents.flush()
    assert len(_sistem(admin)) == 1


def test_alert_spike_for_warning_kind(owner, nokeys):  # noqa: F811
    from app import incidents

    admin = _uid("admin")
    for i in range(4):
        incidents.record("fis_okunamadi", "POST /api/receipts/parse", uid=2000 + i)
    incidents.flush()
    assert _sistem(admin) == []
    incidents.record("fis_okunamadi", "POST /api/receipts/parse", uid=2010)
    incidents.flush()
    q = _sistem(admin)
    assert len(q) == 1 and q[0]["title"].startswith("Ani artış") and "5 kişi" in q[0]["body"]
    # bilgi ağırlığı hiç uyarmaz
    for i in range(6):
        incidents.record("okuma_hakki_doldu", "POST /api/receipts/parse", uid=3000 + i)
    incidents.flush()
    assert len(_sistem(admin)) == 1


def test_alert_global_cap(owner, nokeys, monkeypatch):  # noqa: F811
    from app import incidents

    monkeypatch.setattr(incidents, "ALERT_MAX", 2)
    for i in range(4):
        incidents.record("js_hatasi", f"ekran{i}")
    incidents.flush()
    assert len(_sistem(_uid("admin"))) == 2


def test_alert_telegram(owner, nokeys, monkeypatch):  # noqa: F811
    from app import incidents, telegram

    sent = []
    monkeypatch.setattr(telegram, "enabled", lambda: True)
    monkeypatch.setattr(telegram, "send", lambda text: sent.append(text))
    incidents.record("sunucu_hatasi", "GET /api/summary", ozet="KeyError @ finance.py:summary")
    incidents.flush()
    assert len(sent) == 1 and "Sunucu hatası (500)" in sent[0] and "GET /api/summary" in sent[0]


# ---------------------------------------------------------------- saklama ve silme

def test_prune_and_forget(owner):  # noqa: F811
    from app import incidents, system

    ali = system.create_user("ali@ornek.com", None)
    old = time.time() - 100 * 86400
    incidents.record("js_hatasi", "eski", uid=ali["id"], at=old)
    incidents.record("js_hatasi", "yeni", uid=ali["id"])
    incidents.record("js_hatasi", "yeni")
    incidents.flush()
    assert incidents.prune() == 1
    assert {x["place"] for x in _kinds()} == {"yeni"}
    incidents.record("js_hatasi", "yeni", uid=ali["id"])  # tamponda bekleyen de silinir
    system.delete_user(ali["id"])
    assert [r["user_id"] for r in _rows()] == [0]


def test_capability_and_flush_is_cheap(owner):  # noqa: F811
    from app import incidents, version

    assert "incidents" in version.CAPABILITIES
    assert "incidents" in owner.get("/api/auth/status").json()["capabilities"]
    t = time.perf_counter()
    for i in range(1000):
        incidents.record("is_kurali", "POST /api/entries", uid=i % 7)
    assert time.perf_counter() - t < 0.5  # istek yolunda yalnız tampona ekleme
    assert incidents.pending() == 1000
    incidents.flush()
    assert sum(r["count"] for r in _rows()) == 1000 and len(_rows()) == 7
