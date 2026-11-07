"""Telefona push bildirimi (app/push.py): cihaz kaydı, tercihler, FCM v1 ve APNs istekleri (sahte HTTP), JWT imzaları
(anahtarlar test içinde üretilir), geçersiz cihaz temizliği, günlük özet, sessiz saat, tekrar yok, ortak defter
gizlemesi, zekât modülü, olaylar, hesap silme. Kişiler, kartlar ve tutarlar uydurma."""
import base64
import json
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from tests.test_book import _member, owner  # noqa: F401 - fixture

TZ = ZoneInfo("Europe/Istanbul")
FCM_TOKEN = "fcm-cihaz-belgesi-0001-abcdefghijklmnop"
IOS_TOKEN = "a1" * 32


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _jwt_parts(tok: str) -> tuple[dict, dict, bytes, bytes]:
    h, b, s = tok.split(".")
    return json.loads(_unb64(h)), json.loads(_unb64(b)), f"{h}.{b}".encode(), _unb64(s)


class FakePush:
    """Google OAuth, FCM v1 ve APNs uçlarının sahtesi: istekleri kaydeder, istenen yanıtı verir."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.fcm = (200, None)     # (durum, hata kodu)
        self.apns = (200, None)    # (durum, reason)

    def by_host(self, part: str) -> list[httpx.Request]:
        return [r for r in self.calls if part in r.url.host]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        host = req.url.host
        if host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya29.sahte-erisim", "expires_in": 3599})
        if host == "fcm.googleapis.com":
            st, code = self.fcm
            if st == 200:
                return httpx.Response(200, json={"name": "projects/ornek/messages/1"})
            return httpx.Response(st, json={"error": {"status": "NOT_FOUND" if st == 404 else "INTERNAL",
                                                      "details": [{"errorCode": code}] if code else []}})
        if host.endswith("push.apple.com"):
            st, reason = self.apns
            return httpx.Response(st, json={"reason": reason} if reason else None)
        return httpx.Response(404)


@pytest.fixture()
def keys(tmp_path, monkeypatch):
    """Sahte servis hesabı + APNs anahtarı (bu testte üretilir; yalnız geçici klasörde) ve sahte HTTP."""
    from app import push

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = lambda k: k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,  # noqa: E731
                                    serialization.NoEncryption()).decode()
    sa = tmp_path / "fcm.json"
    sa.write_text(json.dumps({"type": "service_account", "project_id": "ornek-proje", "private_key_id": "kid-1",
                              "private_key": pem(rsa_key), "client_email": "push@ornek-proje.iam.gserviceaccount.com",
                              "token_uri": "https://oauth2.googleapis.com/token"}))
    p8 = tmp_path / "apns.p8"
    p8.write_text(pem(ec_key))
    monkeypatch.setenv("HESAPKITAP_FCM_CREDENTIALS", str(sa))
    monkeypatch.setenv("HESAPKITAP_APNS_KEY_PATH", str(p8))
    monkeypatch.setenv("HESAPKITAP_APNS_KEY_ID", "KEYID12345")
    monkeypatch.delenv("HESAPKITAP_APNS_TEAM_ID", raising=False)
    fake = FakePush()
    monkeypatch.setattr(push, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(push, "APNS_CLIENT", httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(push, "flush_soon", lambda: None)  # testler kuyruğu kendisi boşaltır
    push.reset_cache()
    fake.rsa, fake.ec = rsa_key, ec_key
    yield fake
    push.reset_cache()


@pytest.fixture()
def nokeys(monkeypatch):
    from app import push

    for k in ("HESAPKITAP_FCM_CREDENTIALS", "HESAPKITAP_APNS_KEY_PATH", "HESAPKITAP_APNS_KEY_ID"):
        monkeypatch.delenv(k, raising=False)
    push.reset_cache()


def _user(name="ali"):
    from app import system

    return system.create_user(f"{name}@ornek.com", None)


def _p(kind="odeme", pid="odeme:1", title="Başlık", body="Gövde", route="#/diger/odemeler"):
    from app import push

    return push.item(kind, pid, title, body, route)


def _at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


# ---------------------------------------------------------------- yapılandırma

def test_dependencies_import():
    import h2  # noqa: F401 - APNs için httpx[http2]

    from app import push

    httpx.Client(http2=True).close()
    assert push.APNS_HOSTS["sandbox"] == "api.sandbox.push.apple.com"


def test_disabled_without_keys_is_silent(client, nokeys, monkeypatch):
    from app import push

    u = _user()
    push.register(u["id"], FCM_TOKEN, "android")
    assert push.enabled() is False and push.prefs_out(u["id"])["enabled"] is False
    called = []
    monkeypatch.setattr(push, "deliver", lambda *a: called.append(a))
    push.tick(_at("2026-11-02T10:00"))
    assert called == [] and push.flush() == 0
    from app import db

    with db.use_space(None, u):  # kanca: push kapalıyken zamanlayıcı bile kurulmaz
        push.after_statement()
    assert push._pending == {}


def test_meta_has_push_capability(client):
    assert "push" in client.get("/api/meta").json()["capabilities"]


# ---------------------------------------------------------------- cihazlar ve tercihler (API)

def test_device_register_move_delete_and_limit(owner, nokeys):  # noqa: F811
    from app import push, system

    admin = system.user_by_name("admin")
    assert owner.post("/api/push/devices", json={"token": FCM_TOKEN, "platform": "android", "app_version": "1.0"}).json() == {"ok": True}
    assert [d["platform"] for d in push.devices(admin["id"])] == ["android"]
    assert owner.post("/api/push/devices", json={"token": FCM_TOKEN, "platform": "web"}).status_code == 422
    assert owner.post("/api/push/devices", json={"token": "kisa", "platform": "ios"}).status_code == 422

    m = _member(owner, role="viewer")  # sadece görür: kendi telefonu yine kaydedilir
    me = system.user_by_name("esim")
    assert m.post("/api/push/devices", json={"token": FCM_TOKEN, "platform": "android"}).status_code == 200
    assert push.devices(admin["id"]) == [] and len(push.devices(me["id"])) == 1  # telefonda hesap değişti: taşındı
    assert m.post("/api/push/devices", json={"token": IOS_TOKEN, "platform": "ios", "env": "sandbox"}).status_code == 200
    assert {d["env"] for d in push.devices(me["id"])} == {None, "sandbox"}

    assert owner.request("DELETE", "/api/push/devices", json={"token": FCM_TOKEN}).json() == {"ok": True}
    assert len(push.devices(me["id"])) == 2  # başkasının cihazını silemez
    assert m.request("DELETE", "/api/push/devices", json={"token": FCM_TOKEN}).status_code == 200
    assert [d["token"] for d in push.devices(me["id"])] == [IOS_TOKEN]

    for i in range(push.MAX_DEVICES + 3):
        push.register(admin["id"], f"cihaz-belgesi-{i:04d}-xxxxxxxx", "android")
    assert len(push.devices(admin["id"])) == push.MAX_DEVICES


def test_prefs_defaults_and_partial_put(owner, nokeys):  # noqa: F811
    p = owner.get("/api/push/prefs").json()
    assert p["enabled"] is False and p["digest"] is True and p["quiet"] == {"from": "22:00", "to": "09:00"}
    assert p["types"]["ortak"] is False and all(v for k, v in p["types"].items() if k != "ortak")
    assert set(p["labels"]) == set(p["types"]) and p["labels"]["odeme"] == "Kart son ödeme"
    assert len(p["types"]) == 16  # + aile, + geri_bildirim ve sistem (yalnız yöneticiye: owner yönetici)
    # + fiyat (Fiyat artışı)

    r = owner.put("/api/push/prefs", json={"types": {"faiz": False, "bilinmeyen": True}}).json()
    assert r["types"]["faiz"] is False and r["types"]["odeme"] is True and "bilinmeyen" not in r["types"]
    r = owner.put("/api/push/prefs", json={"quiet": {"to": "08:30"}, "digest": False}).json()
    assert r["quiet"] == {"from": "22:00", "to": "08:30"} and r["digest"] is False and r["types"]["faiz"] is False
    assert owner.put("/api/push/prefs", json={"quiet": {"from": "25:00"}}).status_code == 422
    assert owner.get("/api/push/prefs").json()["quiet"]["to"] == "08:30"


def test_test_endpoint_rate_limit(owner, keys):  # noqa: F811
    assert owner.post("/api/push/test").status_code == 409  # kayıtlı telefon yok
    owner.post("/api/push/devices", json={"token": FCM_TOKEN, "platform": "android"})
    r = owner.post("/api/push/test")
    assert r.status_code == 200 and r.json()["sent"] == 1
    sent = json.loads(keys.by_host("fcm")[-1].content)["message"]
    assert sent["notification"]["title"] == "Bildirimler açık"
    assert owner.post("/api/push/test").status_code == 429


def test_test_endpoint_disabled(owner, nokeys):  # noqa: F811
    owner.post("/api/push/devices", json={"token": FCM_TOKEN, "platform": "android"})
    assert owner.post("/api/push/test").status_code == 409


# ---------------------------------------------------------------- gönderim: FCM ve APNs

def test_fcm_request_and_rs256_assertion(client, keys):
    from app import push

    u = _user()
    push.register(u["id"], FCM_TOKEN, "android")
    p = _p(pid="odeme:5400******9602:2026-11-05:3gun", title="VakıfBank ••9602", body="Son ödeme 3 gün sonra")
    assert push.deliver(u["id"], p) == {"sent": 1, "removed": 0, "failed": 0}
    push.deliver(u["id"], p)
    oauth = keys.by_host("oauth2")
    assert len(oauth) == 1  # erişim belirteci önbellekte
    form = {k: v[0] for k, v in parse_qs(oauth[0].content.decode()).items()}
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    head, body, signing, sig = _jwt_parts(form["assertion"])
    assert head == {"alg": "RS256", "typ": "JWT", "kid": "kid-1"}
    assert body["iss"] == "push@ornek-proje.iam.gserviceaccount.com" and body["aud"] == "https://oauth2.googleapis.com/token"
    assert body["scope"] == "https://www.googleapis.com/auth/firebase.messaging" and body["exp"] - body["iat"] == 3600
    keys.rsa.public_key().verify(sig, signing, padding.PKCS1v15(), hashes.SHA256())  # imza geçerli

    req = keys.by_host("fcm")[0]
    assert str(req.url) == "https://fcm.googleapis.com/v1/projects/ornek-proje/messages:send"
    assert req.headers["authorization"] == "Bearer ya29.sahte-erisim"
    msg = json.loads(req.content)["message"]
    assert msg["token"] == FCM_TOKEN
    assert msg["notification"] == {"title": "VakıfBank ••9602", "body": "Son ödeme 3 gün sonra"}
    assert msg["data"] == {"route": "#/diger/odemeler", "kind": "odeme", "id": p["id"]}
    assert msg["android"]["notification"] == {"channel_id": "hatirlatma", "tag": p["id"]}


def test_apns_request_headers_and_es256_jwt(client, keys):
    from app import push

    u = _user()
    push.register(u["id"], IOS_TOKEN, "ios", env="production")
    push.register(u["id"], "b2" * 32, "ios", env="sandbox")
    p = _p(kind="butce", pid="butce:2026-11:market:80", title="Market bütçesi", route="#/diger/butceler")
    assert push.deliver(u["id"], p)["sent"] == 2
    reqs = keys.by_host("push.apple.com")
    assert {r.url.host for r in reqs} == {"api.push.apple.com", "api.sandbox.push.apple.com"}
    prod = next(r for r in reqs if r.url.host == "api.push.apple.com")
    assert prod.url.path == f"/3/device/{IOS_TOKEN}"
    assert prod.headers["apns-topic"] == "com.alameddinc.hesapkitap"
    assert prod.headers["apns-push-type"] == "alert" and prod.headers["apns-collapse-id"] == p["id"]
    auth = prod.headers["authorization"]
    assert auth.startswith("bearer ")
    head, claims, signing, sig = _jwt_parts(auth[7:])
    assert head == {"alg": "ES256", "kid": "KEYID12345"} and claims["iss"] == "D78HMKA7Z8"
    assert len(sig) == 64  # JOSE r || s
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    keys.ec.public_key().verify(der, signing, ec.ECDSA(hashes.SHA256()))
    body = json.loads(prod.content)
    assert body["aps"] == {"alert": {"title": "Market bütçesi", "body": "Gövde"}, "sound": "default", "thread-id": "butce"}
    assert (body["route"], body["kind"], body["id"]) == ("#/diger/butceler", "butce", p["id"])
    # JWT önbellekte: ikinci gönderimde aynı belge
    push.deliver(u["id"], p)
    assert keys.by_host("push.apple.com")[-1].headers["authorization"] == auth


def test_long_id_collapse_is_hashed():
    from app import push

    long = "odeme:" + "9" * 80
    cid = push.collapse_id(long)
    assert len(cid.encode()) <= 64 and cid == push.collapse_id(long) and push.collapse_id("kisa") == "kisa"


@pytest.mark.parametrize("platform,resp,removed", [
    ("android", (404, "UNREGISTERED"), True),
    ("android", (400, "UNREGISTERED"), True),
    ("android", (500, None), False),
    ("ios", (410, "Unregistered"), True),
    ("ios", (400, "BadDeviceToken"), True),
    ("ios", (500, "InternalServerError"), False),
])
def test_invalid_tokens_are_removed(client, keys, platform, resp, removed):
    from app import push

    u = _user()
    token = FCM_TOKEN if platform == "android" else IOS_TOKEN
    push.register(u["id"], token, platform)
    if platform == "android":
        keys.fcm = resp
    else:
        keys.apns = resp
    res = push.deliver(u["id"], _p())
    assert res["removed"] == int(removed) and (push.devices(u["id"]) == []) is removed


# ---------------------------------------------------------------- birleştirme, sessiz saat, tekrar yok

def test_digest_combines_and_separate_max_three():
    from app import push

    today = date(2026, 11, 2)
    items = [_p(pid="odeme:a"), _p(pid="odeme:b"), _p(kind="ekstre", pid="ekstre:c", route="#/diger/ekstreler")]
    [d] = push.combine(items, True, today)
    assert d["body"] == "Bugün 2 ödeme, 1 ekstre" and d["route"] == "#/ozet" and d["id"] == "gunluk:2026-11-02"
    assert push.combine(items[:1], True, today) == items[:1]
    many = items + [_p(kind="fatura", pid="fatura:d"), _p(kind="fatura", pid="fatura:e")]
    out = push.combine(many, False, today)
    assert [x["id"] for x in out[:3]] == ["odeme:a", "odeme:b", "ekstre:c"] and len(out) == 4
    assert out[3]["body"] == "Ayrıca 2 fatura"


def test_quiet_hours_queue_and_flush(client, keys):
    from app import push

    u = _user()
    push.register(u["id"], FCM_TOKEN, "android")
    night = _at("2026-11-02T23:15")
    out = push.notify(u["id"], [_p(kind="belge", pid="belge:1")], night)
    assert len(out) == 1
    [q] = push.queue(u["id"])
    assert datetime.fromtimestamp(q["not_before"], TZ) == _at("2026-11-03T09:00")
    assert push.flush(_at("2026-11-03T02:00")) == 0 and keys.by_host("fcm") == []
    assert push.flush(_at("2026-11-03T09:01")) == 1 and push.queue(u["id"]) == []
    assert json.loads(keys.by_host("fcm")[0].content)["message"]["data"]["kind"] == "belge"

    p = push.get_prefs(u["id"])
    assert push.quiet_until(p, _at("2026-11-02T08:59")) == _at("2026-11-02T09:00")
    assert push.quiet_until(p, _at("2026-11-02T12:00")) is None
    push.set_prefs(u["id"], {"quiet": {"from": "13:00", "to": "13:00"}})  # aynı saat: sessiz saat yok
    assert push.quiet_until(push.get_prefs(u["id"]), _at("2026-11-02T23:30")) is None
    # tercih kuyruktayken değişti: gönderim anında yeniden bakılır
    push.notify(u["id"], [_p(kind="belge", pid="belge:2")], _at("2026-11-02T12:00"))
    push.set_prefs(u["id"], {"quiet": {"from": "11:00", "to": "14:00"}})
    assert push.flush(_at("2026-11-02T12:01")) == 0 and len(push.queue(u["id"])) == 1


def test_no_repeat_and_type_off(client, keys):
    from app import push

    u = _user()
    now = _at("2026-11-02T10:00")
    assert len(push.notify(u["id"], [_p(pid="odeme:x:3gun")], now)) == 1
    assert push.notify(u["id"], [_p(pid="odeme:x:3gun")], now + timedelta(days=1)) == []
    push.set_prefs(u["id"], {"types": {"faiz": False}})
    assert push.notify(u["id"], [_p(kind="faiz", pid="faiz:k:2026-11")], now) == []
    assert not push.was_sent(u["id"], "faiz:k:2026-11")  # kapalı türde işaretlenmez: açılınca gelebilir
    # 60 günden eskisi temizlenir
    assert push.prune(now.date() + timedelta(days=61)) == 1 and not push.was_sent(u["id"], "odeme:x:3gun")


# ---------------------------------------------------------------- günlük iş

def _statement(db, card="5400******9602", bank="VakıfBank", end="2026-10-24", nxt="2026-11-24", due="2026-11-05", debt=2500.0):
    with db.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt, next_period_end) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (f"h-{card}-{end}", bank, card, end, due, debt, nxt))


def test_daily_digest_and_member_hidden_card(owner, keys):  # noqa: F811
    from app import db, push, system

    _statement(db)
    _statement(db, card="4000******1234", bank="Kuveyt Türk")
    m = _member(owner, role="full")
    owner.put("/api/book/rules", json={"hidden_cards": ["4000******1234"]})
    admin, me = system.user_by_name("admin"), system.user_by_name("esim")
    push.register(admin["id"], FCM_TOKEN, "android")
    push.register(me["id"], IOS_TOKEN, "ios")
    assert m.get("/api/push/prefs").status_code == 200

    assert push.run_daily(_at("2026-11-02T08:00")) == {}  # 09:00'dan önce çalışmaz
    done = push.run_daily(_at("2026-11-02T09:30"))
    assert done == {admin["id"]: 1, me["id"]: 1}
    assert push.run_daily(_at("2026-11-02T15:00")) == {}  # günde bir kez
    [a] = [x for x in push.queue(admin["id"]) if not x["payload"]["id"].startswith("katildi:")]  # üye katıldı bildirimi ayrı
    assert a["payload"]["body"] == "Bugün 2 ödeme" and a["payload"]["route"] == "#/ozet"
    [b] = push.queue(me["id"])  # gizli kartın son ödemesi üyeye gitmez
    assert b["payload"]["kind"] == "odeme" and "9602" in b["payload"]["title"] and "1234" not in json.dumps(b["payload"])
    assert b["payload"]["id"] == "odeme:5400******9602:2026-11-05:3gun"
    assert push.flush(_at("2026-11-02T09:31")) == 2
    # son gün sabahı ikinci kez; 1 ve 2 gün kala yok
    assert push.run_daily(_at("2026-11-04T09:30")) == {admin["id"]: 0, me["id"]: 0}
    done = push.run_daily(_at("2026-11-05T09:30"))
    assert done == {admin["id"]: 1, me["id"]: 1}
    assert push.queue(me["id"])[0]["payload"]["id"].endswith(":songun")


def test_daily_kinds_fatura_butce_ozet_ortak(owner, keys):  # noqa: F811
    from app import db, finance, push, system

    m = _member(owner)
    admin, me = system.user_by_name("admin"), system.user_by_name("esim")
    db.create_bill({"provider": "Örnek Elektrik", "amount": 412.5, "due_date": "2026-11-03", "category": "Elektrik"})
    db.set_budget("Market & Gıda", 1000)
    db.create_entry({"entry_date": "2026-11-01", "description": "Market", "amount": 850, "kind": "gider",
                     "category": "Market & Gıda", "payment": "nakit"})
    assert m.post("/api/entries", json={"entry_date": "2026-11-01", "description": "Fırın", "amount": 40, "kind": "gider",
                                        "category": "Market & Gıda", "payment": "nakit"}).status_code == 200
    push.set_prefs(admin["id"], {"types": {"ortak": True}})
    with db.use_space(None, admin):
        items = push.collect(admin, date(2026, 11, 1))
    kinds = {i["kind"]: i for i in items}
    assert kinds["fatura"]["id"] == "fatura:1:2026-11-03:2gun" and "412,5" in kinds["fatura"]["body"]
    slug = finance._slug("Market & Gıda")  # Özet uyarısının anahtarıyla aynı
    assert kinds["butce"]["id"] == f"butce:2026-11:{slug}:80"
    assert kinds["ozet"]["id"] == "ozet:2026-10" and kinds["ozet"]["title"] == "Ekim 2026 dönemi bitti"
    assert kinds["ortak"]["body"] == "Dünden beri 1 yeni kayıt eklendi"  # üyenin eklediği; sahibin kendi kaydı sayılmaz
    with db.use_space(None, me):  # üye: ortak varsayılan kapalı
        assert "ortak" not in {i["kind"] for i in push.collect(me, date(2026, 11, 1))}
    # bütçe: aynı ay/eşik bir kez; %100 olunca ayrı bildirim
    now = _at("2026-11-01T10:00")
    assert any(x["id"].startswith("butce:") for x in push.notify(admin["id"], [kinds["butce"]], now, daily=False))
    assert push.notify(admin["id"], [kinds["butce"]], now) == []
    db.create_entry({"entry_date": "2026-11-01", "description": "Market", "amount": 200, "kind": "gider",
                     "category": "Market & Gıda", "payment": "nakit"})
    with db.use_space(None, admin):
        [b] = [i for i in push.collect(admin, date(2026, 11, 1)) if i["kind"] == "butce"]
    assert b["id"] == f"butce:2026-11:{slug}:100"


def test_gelir_late_only_when_usually_seen(client, keys):
    from app import db, push

    rid = db.create_recurring({"description": "Maaş", "amount": 30000, "kind": "gelir", "category": "Maaş",
                               "day_of_month": 15, "start_month": "2026-01"})
    with db.use_space(None, None):
        assert push._gelir(date(2026, 11, 17)) == []  # hiç yatmış görünmüyor (döküm yüklemiyor): uyarı yok
    for d in ("2026-09-15", "2026-10-15"):
        db.create_entry({"entry_date": d, "description": "MAAS ODEMESI", "amount": 30000, "kind": "gelir",
                         "category": "Maaş", "payment": "havale", "recurring_id": rid})
    [g] = push._gelir(date(2026, 11, 17))
    assert g["id"] == f"gelir:{rid}:2026-11" and g["route"] == "#/islemler"
    assert push._gelir(date(2026, 11, 16)) == []  # 2 gün dolmadı
    db.create_entry({"entry_date": "2026-11-14", "description": "MAAS ODEMESI", "amount": 30000, "kind": "gelir",
                     "category": "Maaş", "payment": "havale", "recurring_id": rid})
    assert push._gelir(date(2026, 11, 17)) == []  # yattı


def test_zekat_only_when_module_on(client, keys, monkeypatch):
    from app import db, push, zakat

    monkeypatch.setattr(zakat, "reminders", lambda today: ["🌙 <b>Zekât</b> · Ali: yıl 10.11.2026 — 7 gün kaldı\n"
                                                           "Güncel hesap: <b>1.250,00 TL</b> ödenecek"])
    monkeypatch.setattr(zakat, "holy_reminders", lambda today: [])
    db.set_setting("modules", json.dumps({"zekat": False}))
    assert push._zekat(date(2026, 11, 3)) == []
    db.set_setting("modules", json.dumps({"zekat": True}))
    [z] = push._zekat(date(2026, 11, 3))
    assert z["title"] == "Zekât" and z["route"] == "#/diger/zekat" and "<b>" not in z["body"]
    assert z["body"].startswith("Ali: yıl 10.11.2026")


def test_member_with_hidden_zekat_page_gets_none(owner, keys, monkeypatch):  # noqa: F811
    from app import db, push, system, zakat

    monkeypatch.setattr(zakat, "reminders", lambda today: ["🌙 <b>Zekât</b> · Ali"])
    monkeypatch.setattr(zakat, "holy_reminders", lambda today: [])
    _member(owner)
    owner.put("/api/book/rules", json={"hidden_pages": ["zekat"]})
    with db.use_space(None, system.user_by_name("esim")):
        assert push._zekat(date(2026, 11, 3)) == []
    with db.use_space(None, system.user_by_name("admin")):
        assert len(push._zekat(date(2026, 11, 3))) == 1


# ---------------------------------------------------------------- olaylar

class _NowTimer:
    """threading.Timer yerine: hemen çalışır (olay hesabı testte eşzamanlı)."""

    def __init__(self, delay, fn):
        self.fn = fn
        self.daemon = True

    def start(self):
        self.fn()

    def cancel(self):
        pass


def test_statement_event_faiz_and_subscription(owner, keys, monkeypatch):  # noqa: F811
    from app import finance, push, system

    monkeypatch.setattr(push, "_Timer", _NowTimer)
    cur = finance.current_period()
    monkeypatch.setattr(finance, "insights", lambda month, today=None: [
        {"key": f"faiz:vakifbank-9602:{month}", "title": "Faiz alarmı: VakıfBank ••9602", "detail": "Asgari ödendi."},
        {"key": "iptal_cekim:ornek-dizi:99", "title": "Örnek Dizi iptal edilmişti ama yine çekildi", "detail": "İade iste."},
        {"key": "butce:market:2026-11", "title": "Bütçe", "detail": ""}] if month == cur else [])
    monkeypatch.setattr(finance, "subscriptions", lambda: [
        {"merchant": "ORNEK MUZIK", "status": "aktif", "monthly": 79.99, "prev_monthly": 59.99, "last_period": "2026-11",
         "last_seen": date.today().isoformat()},
        {"merchant": "SABIT", "status": "aktif", "monthly": 50.0, "prev_monthly": 50.0, "last_period": "2026-11",
         "last_seen": date.today().isoformat()}])
    admin = system.user_by_name("admin")
    push.register(admin["id"], FCM_TOKEN, "android")
    push.set_prefs(admin["id"], {"quiet": {"from": "00:00", "to": "00:00"}})
    files = [("files", ("ekstre.pdf", b"FIXTURE:vakifbank_a.txt", "application/pdf"))]
    assert owner.post("/api/statements/upload", files=files).json()[0]["status"] == "saved"
    q = [x["payload"] for x in push.queue(admin["id"])]
    kinds = sorted(x["kind"] for x in q)
    assert kinds == ["abonelik", "abonelik", "faiz"]  # faiz; iptal sonrası çekim + zam (bütçe uyarısı olay değil)
    zam = next(x for x in q if x["id"].startswith("abonelik:zam:"))
    assert zam["title"] == "Ornek Muzik zamlandı" and zam["route"] == "#/diger/tasarruf"
    assert next(x for x in q if x["kind"] == "faiz")["route"] == "#/diger/faiz"
    n = len(q)
    owner.post("/api/statements/upload", files=[("files", ("b.pdf", b"FIXTURE:kuveytturk.txt", "application/pdf"))])
    assert len(push.queue(admin["id"])) == n  # aynı uyarılar ikinci kez gelmez


def test_statement_event_member_hide_fees(owner, keys, monkeypatch):  # noqa: F811
    from app import db, finance, push, system

    monkeypatch.setattr(finance, "insights", lambda month, today=None: [
        {"key": f"faiz:kart:{month}", "title": "Faiz", "detail": ""}])
    monkeypatch.setattr(finance, "subscriptions", lambda: [])
    _member(owner)
    owner.put("/api/book/rules", json={"hide_fees": True})
    with db.use_space(None, system.user_by_name("esim")):
        assert push.statement_items(date(2026, 11, 2)) == []
    with db.use_space(None, system.user_by_name("admin")):
        assert len(push.statement_items(date(2026, 11, 2))) == 2  # bu ve geçen dönemin faiz uyarısı


def test_document_event(client, keys):
    from app import db, push

    u = _user()
    with db.use_space(None, u):
        push.after_document("posta", "✅ Ekstre: VakıfBank ••9602\n🧾 Fatura: Örnek Su")  # cihaz yok: hiçbir şey
        assert push.queue(u["id"]) == []
        push.register(u["id"], FCM_TOKEN, "android")
        push.after_document("posta", "✅ Ekstre: VakıfBank ••9602\n🧾 Fatura: Örnek Su")
        [q] = push.queue(u["id"])
        assert q["payload"]["kind"] == "belge" and q["payload"]["route"] == "#/diger/posta"
        assert q["payload"]["body"] == "✅ Ekstre: VakıfBank ••9602 (+1 belge daha)"
        push.set_prefs(u["id"], {"types": {"belge": False}})
        push.after_document("paylas", "Fiş: Örnek Market 120 TL")
        assert len(push.queue(u["id"])) == 1


# ---------------------------------------------------------------- hesap silme

def test_account_deletion_removes_push_data(client, keys):
    from app import deletion, push, system

    u = _user()
    push.register(u["id"], FCM_TOKEN, "android")
    push.set_prefs(u["id"], {"digest": False})
    push.notify(u["id"], [_p()], _at("2026-11-02T23:00"))
    deletion.request_delete(system.user(u["id"]))  # silinmek üzere: cihazlar ve kuyruk hemen düşer
    assert push.devices(u["id"]) == [] and push.queue(u["id"]) == []
    push.register(u["id"], FCM_TOKEN, "android")
    system.delete_user(u["id"])
    with system.connect() as c:
        for t in ("push_devices", "push_prefs", "push_sent", "push_queue"):
            assert c.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (u["id"],)).fetchone()[0] == 0, t


def test_revoke_all_sessions_forgets_push_devices(owner, nokeys):  # noqa: F811
    """"Bütün cihazlardan çıkış": çıkış yaptırılan telefonlara hatırlatma gitmesin; bu cihaz yeniden kaydolur."""
    from app import push, system

    admin = system.user_by_name("admin")
    assert owner.post("/api/push/devices", json={"token": FCM_TOKEN, "platform": "android"}).json() == {"ok": True}
    assert len(push.devices(admin["id"])) == 1
    assert owner.post("/api/me/sessions/revoke-all", json={"keep_current": True}).status_code == 200
    assert push.devices(admin["id"]) == []
