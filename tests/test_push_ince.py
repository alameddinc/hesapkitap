"""Bildirimlerin ince ayarı (app/push.py; envanter: hesapkitap-mobile docs/bildirimler.md): metin uzunlukları (başlık ≤ 40,
gövde ≤ 90), faize yönlendirme yok, aynı kişinin aynı güne düşen borçları tek bildirim, kesimden 1 gün sonra "ekstreni
yükle", son ödemeye 3 ve 1 gün kala, aynı tür + aynı nesne günde bir, sessiz saat 22–09 varsayılanı, Android kanalı,
iOS rozeti, tür örneği (POST /api/push/test {kind}). Kişiler, kartlar ve tutarlar uydurma."""
import json
import re
from datetime import date
from pathlib import Path

from tests.test_book import _member, owner  # noqa: F401 - fixture
from tests.test_push import FCM_TOKEN, IOS_TOKEN, _at, _user, keys, nokeys  # noqa: F401 - fixture

ON = {"odeme": True, "ekstre": True, "dokum": True}
YASAK = ("asgari", "kredi çek", "taksitlendir", "yapılandır", "ertele", "nakit avans")


def _ok(it: dict) -> None:
    assert len(it["title"]) <= 40, it["title"]
    assert len(it["body"]) <= 90, it["body"]
    low = (it["title"] + " " + it["body"]).lower()
    assert not any(w in low for w in YASAK), it


def _statement(db, card="5400******9602", bank="Örnek Banka", end="2026-10-24", nxt="2026-11-24", due="2026-11-05",
               debt=2500.0):
    with db.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt, next_period_end) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (f"h-{card}-{end}", bank, card, end, due, debt, nxt))


# ---------------------------------------------------------------- metin

def test_every_kind_has_short_sample_with_app_route():
    from app import push

    assert set(push.SAMPLES) == set(push.TYPES)  # her türün örneği (Türleri dene + docs tablosu)
    for kind, (title, body, route) in push.SAMPLES.items():
        _ok({"title": title, "body": body})
        assert route.startswith("#/"), kind
        it = push.item(kind, f"test:{kind}", title, body, route)
        assert (it["title"], it["body"]) == (title, body), kind  # örnek sınıra zaten sığar (kesilmez)


def test_item_cuts_long_text_at_word_boundary():
    from app import push

    it = push.item("gelir", "g:1", "Çok uzun bir düzenli gelir adı olan örnek kalem yatmadı mı?",
                   "Beklenen gün 15 Kasım · ₺40.000. Yattıysa dökümü yükle. " * 3, "#/islemler")
    _ok(it)
    assert it["title"].endswith("…") and it["body"].endswith("…")
    assert push._ad("Kısa ad") == "Kısa ad" and len(push._ad("Örnek " * 10, 16)) == 16
    # duyuru yöneticinin metni: sınırı app/announce.py
    d = push.item("duyuru", "duyuru:1", "Yeni sürüm: " + "x" * 50, "y" * 200, "#/ozet")
    assert len(d["title"]) > 40 and len(d["body"]) == 200


def test_fix_texts_fit():
    """Sorun düzeltildi bildirimi: her türün hazır metni (güncelle notuyla) gövdeye sığar."""
    from app import incidents

    for code in incidents.KODLAR:
        for upd in (False, True):
            t = incidents.fix_text(code, update=upd)
            assert t is None or len(t) <= 90, (code, t)


def test_no_interest_steering_in_push_sources():
    """Bildirim metinlerinde "asgari öde", kredi, yapılandırma, erteleme önerisi yok (kaynak dizgileri)."""
    root = Path(__file__).resolve().parent.parent
    for name in ("push.py", "reminders.py", "store_subs.py", "sadaka_reminder.py"):
        src = (root / "app" / name).read_text(encoding="utf-8")
        src = re.sub(r'"""[\s\S]*?"""', "", src)
        src = re.sub(r"(^|\s)#[^\n]*", "", src)
        for s in re.findall(r'f?"([^"\n]{3,})"', src):
            low = s.lower()
            assert not any(w in low for w in ("asgari", "kredi çek", "taksitlendir", "yapılandır", "nakit avans")), (name, s)


def test_faiz_and_abonelik_event_texts_short():
    from app import push

    t, b = push.faiz_text({"title": "Faiz alarmı: Türkiye Örnek Katılım Bankası …1234 bu dönem ₺1.279,63 faiz ve vergi",
                           "detail": "Neden: önceki ekstre geç ödendi. Önlem: …"})
    assert t.startswith("Faiz alarmı · Türkiye Örnek") and t.endswith("… …1234")
    assert b == "Bu dönem ₺1.279,63 faiz ve vergi. Nedeni Faiz ve ücretler'de."
    _ok({"title": t, "body": b})
    assert "Önlem" not in b and "Neden:" not in b  # açıklama ekranda (Faiz ve ücretler)
    t, b = push.faiz_text({"title": "Taksitlendirme sana ₺2.400 daha vade farkı ve vergi ödetecek", "detail": "…"})
    assert t == "Taksit vade farkı: ₺2.400"
    t, b = push.abonelik_text({"title": "Örnek Çok Uzun Adlı Müzik Servisi iptal edilmişti ama yine çekildi",
                               "detail": "02.11 iptalinden sonra ₺59,99 çekim var. İptal tamamlanmamış olabilir; hesabını "
                                         "kontrol et, iade iste."})
    assert t.endswith(": iptalden sonra çekim") and b == "02.11 iptalinden sonra ₺59,99 çekim var."
    _ok({"title": t, "body": b})


# ---------------------------------------------------------------- zamanlama

def test_ekstre_one_day_after_cut_and_payment_3_and_1_days(client):
    from app import db, push, reminders

    _statement(db)  # kesim 24 Ekim, sonraki kesim 24 Kasım, son ödeme 5 Kasım
    assert not [i for i in push._odeme_ekstre_dokum(date(2026, 11, 24), ON) if i["kind"] == "ekstre"]  # kesim günü
    [e] = [i for i in push._odeme_ekstre_dokum(date(2026, 11, 25), ON) if i["kind"] == "ekstre"]
    assert e["title"] == "Kasım ekstreni yükle · ••9602" and e["route"] == "#/diger/ekstreler"
    assert e["body"] == "Örnek Banka · kesim 24 Kasım. Yükle, son ödeme günün güncellensin."
    assert e["id"] == "ekstre:5400******9602:2026-11"  # Yapılacaklar maddesiyle aynı kimlik (bir kez)
    _ok(e)
    # Yapılacaklar bir gün sonra gösterir (banka PDF'i geç gönderebilir)
    assert not [r for r in reminders.build(date(2026, 11, 25)) if r["kind"] == "ekstre"]
    assert [r for r in reminders.build(date(2026, 11, 26)) if r["kind"] == "ekstre"]
    # ekstre yüklendi: madde ve bildirim yok
    _statement(db, end="2026-11-24", nxt="2026-12-24", due="2026-12-05")
    assert not [i for i in push._odeme_ekstre_dokum(date(2026, 11, 25), ON) if i["kind"] == "ekstre"]

    def odeme(d):
        return [i for i in push._odeme_ekstre_dokum(d, ON) if i["kind"] == "odeme"]

    [three] = odeme(date(2026, 12, 2))
    assert three["title"] == "Son ödeme 3 gün sonra · ••9602" and three["id"].endswith(":3gun")
    assert three["body"] == "Örnek Banka · ₺2.500 · 5 Aralık Cumartesi. Ödediysen işaretle."  # dönem borcu, asgari değil
    assert odeme(date(2026, 12, 3)) == []
    [one] = odeme(date(2026, 12, 4))
    assert one["title"] == "Son ödeme yarın · ••9602" and one["id"].endswith(":1gun")
    assert odeme(date(2026, 12, 5)) == []
    # ödendi: bildirim yok
    with db.connect() as c:
        c.execute("UPDATE statements SET paid_amount = total_debt WHERE period_end = '2026-11-24'")
    assert odeme(date(2026, 12, 4)) == []


def test_same_person_same_day_debts_single_push(client):
    from app import push

    for amt in (2000, 5000, 7000):
        client.post("/api/debts", json={"person": "Ahmet", "direction": "borc", "amount": amt, "debt_date": "2026-06-01",
                                        "due_date": "2026-11-12"})
    client.post("/api/debts", json={"person": "Ayşe", "direction": "borc", "amount": 1000, "debt_date": "2026-06-01",
                                    "due_date": "2026-11-12"})
    client.post("/api/debts", json={"person": "Ahmet", "direction": "alacak", "amount": 300, "debt_date": "2026-06-01",
                                    "due_date": "2026-11-12"})
    client.post("/api/debts", json={"person": "Ahmet", "direction": "borc", "amount": 900, "debt_date": "2026-06-01",
                                    "due_date": "2026-11-20"})  # başka gün: ayrı
    got = {(i["title"]): i for i in push._borc(date(2026, 11, 11))}
    assert set(got) == {"Ahmet · 3 borç · ₺14.000 yarın", "Ayşe · ₺1.000 yarın", "Ahmet · ₺300 yarın"}
    a = got["Ahmet · 3 borç · ₺14.000 yarın"]
    assert a["body"] == "Toplam ₺14.000. Ödediklerini Borç / Alacak'ta işaretle."
    assert a["route"] == "#/diger/borclar/kisi/Ahmet" and a["kind"] == "borc"
    assert got["Ahmet · ₺300 yarın"]["body"] == "Söz verdiği gün. Geldiyse Borç / Alacak'ta işaretle."  # alacak nötr
    for it in got.values():
        _ok(it)
    # o gün yine tek; büyük/küçük harf ve boşluk farkı aynı kişi
    client.post("/api/debts", json={"person": " ahmet ", "direction": "borc", "amount": 1000, "debt_date": "2026-06-01",
                                    "due_date": "2026-11-12"})
    today = [i["title"] for i in push._borc(date(2026, 11, 12)) if "borç" in i["title"]]
    assert today == ["Ahmet · 4 borç · ₺15.000 bugün"]
    # uzun ad kısalır, tutar ve gün kesilmez
    client.post("/api/debts", json={"person": "Örnek Uzun Adlı Kişi Soyadıoğlu", "direction": "borc", "amount": 123456,
                                    "debt_date": "2026-06-01", "due_date": "2026-11-12"})
    [u] = [i for i in push._borc(date(2026, 11, 11)) if i["title"].startswith("Örnek")]
    assert u["title"].endswith("· ₺123.456 yarın") and "…" in u["title"]
    _ok(u)


def test_same_kind_same_object_once_a_day(client, keys):
    from app import push

    u = _user()
    now = _at("2026-11-02T10:00")
    a = push.item("abonelik", "abonelik:zam:store:7:2026-11-01", "Örnek zamlandı", "₺10 → ₺12", "#/diger/tasarruf",
                  obj="abonelik:7")
    b = push.item("abonelik", "abonelik:yenileme:7:2026-11-05", "Örnek 3 gün sonra yenileniyor", "₺12", "#/diger/tasarruf",
                  obj="abonelik:7")
    c = push.item("abonelik", "abonelik:yenileme:8:2026-11-05", "Başka 3 gün sonra yenileniyor", "₺5", "#/diger/tasarruf",
                  obj="abonelik:8")
    out = push.notify(u["id"], [a, b, c], now)
    assert [x["id"] for x in out] == [a["id"], c["id"]]
    assert push.notify(u["id"], [b], now) == []  # aynı gün ikinci olayda da
    assert not push.was_sent(u["id"], b["id"])  # kimlik harcanmadı
    assert [x["id"] for x in push.notify(u["id"], [b], _at("2026-11-03T10:00"))] == [b["id"]]  # ertesi gün gider
    assert push.notify(u["id"], [b], _at("2026-11-04T10:00")) == []  # kimlik bir kez


def test_default_quiet_hours_22_to_09(client, keys):
    from app import push

    u = _user()
    p = push.get_prefs(u["id"])
    assert p["quiet"] == {"from": "22:00", "to": "09:00"}
    assert push.quiet_until(p, _at("2026-11-02T21:59")) is None
    assert push.quiet_until(p, _at("2026-11-02T22:00")) == _at("2026-11-03T09:00")
    push.register(u["id"], FCM_TOKEN, "android")
    push.notify(u["id"], [push.item("destek", "destek:1", "Destek yanıtladı", "Yanıtı görmek için dokun.",
                                    "#/diger/ayarlar/destek")], _at("2026-11-02T22:30"))
    assert push.flush(_at("2026-11-03T08:59")) == 0
    assert push.flush(_at("2026-11-03T09:00")) == 1
    # günlük iş sessiz saatte başlamaz (09:00'dan sonra)
    assert push.run_daily(_at("2026-11-03T08:30")) == {}


# ---------------------------------------------------------------- kanal, rozet, tür örneği

def test_android_channel_per_kind():
    from app import push

    assert push.channel_of("odeme") == push.channel_of("borc") == "odemeler"
    assert push.channel_of("destek") == push.channel_of("duzeltme") == "destek"
    assert push.channel_of("aile") == push.channel_of("liste") == "aile"
    assert push.channel_of("sadaka") == "zekat" and push.channel_of("duyuru") == "duyuru"
    assert push.channel_of("ekstre") == push.channel_of("bilinmeyen") == "hatirlatma"
    assert set(push.KIND_CHANNEL.values()) <= set(push.CHANNELS) and push.ANDROID_CHANNEL in push.CHANNELS
    msg = push.fcm_message("t" * 20, push.item("borc", "borc:1", "A", "B", "#/diger/borclar"))
    assert msg["message"]["android"]["notification"]["channel_id"] == "odemeler"


def test_ios_badge_only_for_app_that_updates_it(owner, keys):  # noqa: F811
    from app import push, support, system

    admin = system.user_by_name("admin")
    push.register(admin["id"], IOS_TOKEN, "ios", app_version="1.0.11 (60)", feats=["badge", "channels", "bilinmez"])
    push.register(admin["id"], "b2" * 32, "ios", app_version="1.0.10 (54)")  # eski uygulama: rozet hiç düşmezdi
    feats = {d["token"]: push.features(d) for d in push.devices(admin["id"])}
    assert feats == {IOS_TOKEN: {"badge", "channels"}, "b2" * 32: set()}
    r = owner.get("/api/push/badge")
    assert r.status_code == 200 and r.json() == {"count": 0, "todos": 0, "support": 0}
    system.put(f"push:todos:{admin['id']}", "2")
    other = _user("veli")
    with system.connect() as c:  # kişinin yazışması (uydurma), sonra yönetici yanıtı
        support._thread_row(c, admin["id"])
        support._add(c, admin["id"], "user", "mesaj", "Merhaba (uydurma)")
    support.reply(admin["id"], other["id"], "Yanıt (uydurma)")
    assert support.unread(admin["id"])["unread"] == 1
    keys.calls.clear()
    push.deliver(admin["id"], push.item("destek", "destek:9", "Destek yanıtladı", "Yanıtı görmek için dokun.",
                                        "#/diger/ayarlar/destek"))
    by_token = {r.url.path.rsplit("/", 1)[-1]: json.loads(r.content) for r in keys.by_host("push.apple.com")}
    assert by_token[IOS_TOKEN]["aps"]["badge"] == 3  # 1 destek + 2 yapılacak
    assert "badge" not in by_token["b2" * 32]["aps"]
    # uygulama açılınca sayı yeniden yazılır
    assert owner.get("/api/push/badge").json() == {"count": 1, "todos": 0, "support": 1}
    assert system.get(f"push:todos:{admin['id']}") == "0"


def test_device_features_via_api(owner, nokeys):  # noqa: F811
    from app import push, system

    r = owner.post("/api/push/devices", json={"token": IOS_TOKEN, "platform": "ios", "env": "sandbox",
                                              "app_version": "1.0.11 (60)", "features": ["badge", "channels"]})
    assert r.status_code == 200
    [d] = push.devices(system.user_by_name("admin")["id"])
    assert push.features(d) == {"badge", "channels"}
    owner.post("/api/push/devices", json={"token": IOS_TOKEN, "platform": "ios", "env": "sandbox"})  # eski istemci
    assert push.features(push.devices(system.user_by_name("admin")["id"])[0]) == set()


def test_test_endpoint_sends_kind_sample(owner, keys):  # noqa: F811
    from app import push, system

    admin = system.user_by_name("admin")
    push.register(admin["id"], FCM_TOKEN, "android")
    r = owner.post("/api/push/test", json={"kind": "borc"})
    assert r.status_code == 200 and r.json()["sent"] == 1
    msg = json.loads(keys.by_host("fcm")[-1].content)["message"]
    assert msg["data"]["kind"] == "borc" and msg["data"]["route"] == "#/diger/borclar/kisi/Ahmet"
    assert msg["notification"]["title"] == "Ahmet · 7 borç · ₺14.000 yarın"
    assert msg["android"]["notification"]["channel_id"] == "odemeler"
    assert owner.post("/api/push/test", json={"kind": "borc"}).status_code == 429  # tür başına dakikada bir
    assert owner.post("/api/push/test", json={"kind": "destek"}).status_code == 200  # başka tür
    assert owner.post("/api/push/test").status_code == 200  # genel deneme ayrı
    assert owner.post("/api/push/test", json={"kind": "yok"}).status_code == 422
    m = _member(owner)
    esim = system.user_by_name("esim")
    push.register(esim["id"], "c3" * 32, "ios")
    assert m.post("/api/push/test", json={"kind": "sistem"}).status_code == 422  # yöneticiye özel tür
    assert m.post("/api/push/test", json={"kind": "sadaka"}).status_code == 200
