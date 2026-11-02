"""Apple ile giriş, sunucu tarafı (app/apple.py): istemci sırrı (ES256), authorizationCode → refresh_token (şifreli),
hesap kalıcı silinince iptal (hata silmeyi engellemez, yeniden denenir), ayar yokken kapalı.
Anahtarlar ve kimlikler sahte."""
import base64
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient

BUNDLE = "com.ornek.hesapkitap"
SUB = "000000.0123456789abcdef0123456789abcdef.0000"
RELAY = "abc123@privaterelay.appleid.com"
CONSENT = ["kosullar", "acik_riza"]
MOBILE = {"x-hk-client": "mobile"}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _ec_pem() -> tuple[str, ec.EllipticCurvePrivateKey]:
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem.decode(), k


class FakeApple:
    """Apple REST uçlarının sahtesi: gelen formları kaydeder, istenen yanıtı verir."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.token_status, self.revoke_status = 200, 200
        self.revoke_error = None
        self.refresh_token = "r-sahte-belge-0001"
        self.sub = SUB

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.url.path, form))
        if request.url.path == "/auth/token":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            idt = f"{_b64(b'{}')}.{_b64(json.dumps({'sub': self.sub}).encode())}.x"
            return httpx.Response(200, json={"access_token": "a", "refresh_token": self.refresh_token, "id_token": idt})
        if request.url.path == "/auth/revoke":
            if self.revoke_status != 200:
                return httpx.Response(self.revoke_status, json={"error": self.revoke_error} if self.revoke_error else None)
            return httpx.Response(200)
        return httpx.Response(404)

    def paths(self) -> list[str]:
        return [p for p, _ in self.calls]


@pytest.fixture()
def apple(client, monkeypatch):
    from app import apple as apple_mod
    from app import auth, social
    from app.api import main

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = rsa_key.public_key().public_numbers()
    jwk = {"kid": "a1", "kty": "RSA", "n": _b64(n.n.to_bytes(256, "big")), "e": _b64(n.e.to_bytes(3, "big"))}
    monkeypatch.setattr(social, "_apple_keys", lambda force=False: {"a1": jwk})
    monkeypatch.setenv("HESAPKITAP_APPLE_CLIENT_IDS", BUNDLE)
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("HESAPKITAP_OPEN_SIGNUP", "1")
    for k in ("HESAPKITAP_APPLE_TEAM_ID", "HESAPKITAP_APPLE_KEY_ID", "HESAPKITAP_APPLE_PRIVATE_KEY",
              "HESAPKITAP_APPLE_PRIVATE_KEY_FILE"):
        monkeypatch.delenv(k, raising=False)
    fake = FakeApple()
    monkeypatch.setattr(apple_mod, "HTTP_CLIENT", httpx.Client(transport=httpx.MockTransport(fake)))
    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")

    def sign(body: dict, signer=rsa_key, kid="a1") -> str:
        head = _b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
        payload = _b64(json.dumps(body).encode())
        return f"{head}.{payload}.{_b64(signer.sign(f'{head}.{payload}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"

    def id_token(**claims) -> str:
        now = int(time.time())
        return sign({"iss": "https://appleid.apple.com", "aud": BUNDLE, "sub": SUB, "email": RELAY,
                     "email_verified": "true", "iat": now, "exp": now + 600, **claims})

    def notification(event: dict, signer=rsa_key, **claims) -> str:
        now = int(time.time())
        return sign({"iss": "https://appleid.apple.com", "aud": BUNDLE, "iat": now, "jti": "j1",
                     "events": json.dumps({"sub": SUB, "event_time": now * 1000, **event}), **claims}, signer=signer)

    def configure():
        pem, key = _ec_pem()
        monkeypatch.setenv("HESAPKITAP_APPLE_TEAM_ID", "TEAMID0000")
        monkeypatch.setenv("HESAPKITAP_APPLE_KEY_ID", "KEYID00000")
        monkeypatch.setenv("HESAPKITAP_APPLE_PRIVATE_KEY", pem.replace("\n", "\\n"))  # .env'de tek satır
        return key

    class NS:
        pass

    ns = NS()
    ns.fake, ns.id_token, ns.notification, ns.configure, ns.rsa_key = fake, id_token, notification, configure, rsa_key
    return ns


def _login(c: TestClient, apple, code: str | None = "c-sahte-kod", headers=None, **claims):
    body = {"id_token": apple.id_token(**claims), "consent": CONSENT}
    if code:
        body["authorization_code"] = code
    return c.post("/api/auth/apple", json=body, headers=headers or {})


def _uid(email=RELAY) -> int:
    from app import system

    return system.user_by_name(email)["id"]


def _purge_now():
    from app import deletion, system

    u = system.user(_uid())
    deletion.request_delete(u)
    return deletion.purge_due(datetime.now(timezone.utc) + timedelta(days=8))


# ---------------------------------------------------------------- istemci sırrı

def test_client_secret_is_es256_jwt_signed_with_p8(apple):
    from app import apple as a

    key = apple.configure()
    now = 1_800_000_000
    jwt = a.client_secret(BUNDLE, now=now)
    h, p, s = jwt.split(".")
    assert json.loads(_unb64(h)) == {"alg": "ES256", "kid": "KEYID00000", "typ": "JWT"}
    assert json.loads(_unb64(p)) == {"iss": "TEAMID0000", "iat": now, "exp": now + 300,
                                     "aud": "https://appleid.apple.com", "sub": BUNDLE}
    raw = _unb64(s)
    assert len(raw) == 64  # JOSE r||s, DER değil
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))  # hata atmazsa imza doğru


def test_private_key_from_file(apple, tmp_path, monkeypatch):
    from app import apple as a

    apple.configure()
    pem, _ = _ec_pem()
    f = tmp_path / "AuthKey_SAHTE.p8"
    f.write_text(pem)
    monkeypatch.delenv("HESAPKITAP_APPLE_PRIVATE_KEY")
    monkeypatch.setenv("HESAPKITAP_APPLE_PRIVATE_KEY_FILE", str(f))
    assert a.revoke_enabled()
    monkeypatch.setenv("HESAPKITAP_APPLE_PRIVATE_KEY_FILE", str(tmp_path / "yok.p8"))
    assert not a.revoke_enabled()


def test_seal_roundtrip_and_aad_binding(apple):
    from app import apple as a

    blob = a.seal("gizli", "apple-rt:5")
    assert "gizli" not in blob and a.unseal(blob, "apple-rt:5") == "gizli"
    with pytest.raises(Exception):
        a.unseal(blob, "apple-rt:6")  # başka kullanıcının satırına kopyalanan belge çözülmez


# ---------------------------------------------------------------- ayar yokken kapalı

def test_disabled_without_keys_login_unchanged(apple):
    from app import apple as a
    from app import system
    from app.api.main import app

    assert not a.revoke_enabled()
    r = _login(TestClient(app), apple)
    assert r.status_code == 200 and r.json()["created"] is True
    assert apple.fake.calls == []  # Apple'a hiç istek yok
    link = a.link_of(_uid())
    assert link["rt"] is None and a.user_for_sub(SUB) == _uid()  # bildirimler için yalnız bağ (özet)
    assert SUB not in json.dumps(link) and SUB not in (system.get(f"apple:{_uid()}") or "")  # sub düz saklanmaz
    assert _purge_now() and apple.fake.calls == []


def test_capability_advertised(apple):
    from app.api.main import app

    assert "apple_revoke" in TestClient(app).get("/api/auth/status").json()["capabilities"]


# ---------------------------------------------------------------- giriş: token değişimi

def test_login_exchanges_code_and_stores_token_encrypted(apple):
    from app import apple as a
    from app import system
    from app.api.main import app

    apple.configure()
    r = _login(TestClient(app), apple, headers=MOBILE)
    assert r.status_code == 200
    assert apple.fake.paths() == ["/auth/token"]
    form = apple.fake.calls[0][1]
    assert form["grant_type"] == "authorization_code" and form["code"] == "c-sahte-kod" and form["client_id"] == BUNDLE
    assert json.loads(_unb64(form["client_secret"].split(".")[1]))["iss"] == "TEAMID0000"
    raw = system.get(f"apple:{_uid()}")
    assert apple.fake.refresh_token not in raw  # diskte düz belge yok
    assert a.unseal(a.link_of(_uid())["rt"], f"apple-rt:{_uid()}") == apple.fake.refresh_token


def test_consent_retry_keeps_code_for_second_attempt(apple):
    """409 (onay) adımında kod harcanmaz: tek kullanımlık, aynı kodla yeniden denemede değişim yapılır."""
    from app.api.main import app

    apple.configure()
    c = TestClient(app)
    t = apple.id_token()
    assert c.post("/api/auth/apple", json={"id_token": t, "authorization_code": "k1"}).status_code == 409
    assert apple.fake.calls == []
    assert c.post("/api/auth/apple", json={"id_token": t, "authorization_code": "k1", "consent": CONSENT}).status_code == 200
    assert apple.fake.paths() == ["/auth/token"]


def test_exchange_failure_does_not_block_login(apple):
    from app import apple as a
    from app.api.main import app

    apple.configure()
    apple.fake.token_status = 400
    assert _login(TestClient(app), apple).status_code == 200
    assert a.link_of(_uid())["rt"] is None


def test_exchange_with_other_users_code_not_stored(apple):
    """Değişimdeki kimlik giriş belgesindekiyle tutmuyorsa (başkasının kodu) belge saklanmaz."""
    from app import apple as a
    from app.api.main import app

    apple.configure()
    apple.fake.sub = "000000.ffffffffffffffffffffffffffffffff.0000"
    assert _login(TestClient(app), apple).status_code == 200
    assert a.link_of(_uid())["rt"] is None


def test_old_client_without_code_keeps_previous_token(apple):
    from app import apple as a
    from app.api.main import app

    apple.configure()
    _login(TestClient(app), apple)
    first = a.link_of(_uid())["rt"]
    assert _login(TestClient(app), apple, code=None).status_code == 200
    assert a.link_of(_uid())["rt"] == first


# ---------------------------------------------------------------- kalıcı silmede iptal

def test_purge_revokes_refresh_token(apple):
    from app import apple as a
    from app import deletion, system
    from app.api.main import app

    apple.configure()
    _login(TestClient(app), apple)
    uid = _uid()
    apple.fake.calls.clear()
    from app import deletion as d

    d.request_delete(system.user(uid))
    assert apple.fake.calls == []  # yumuşak silmede değil (7 gün içinde geri alınabilir)
    assert deletion.purge_due(datetime.now(timezone.utc) + timedelta(days=8)) == [uid]
    assert apple.fake.paths() == ["/auth/revoke"]
    form = apple.fake.calls[0][1]
    assert form == {**form, "token": apple.fake.refresh_token, "token_type_hint": "refresh_token", "client_id": BUNDLE}
    assert a.link_of(uid) is None and a.user_for_sub(SUB) is None and system.get(f"apple_revoke:{uid}") is None
    assert any(n.get("action") == "apple_revoke" and n.get("result") == "revoked" for n in deletion.record(uid)["notes"])


def test_revoke_failure_does_not_block_delete_and_retries(apple):
    from app import apple as a
    from app import deletion, system
    from app.api.main import app

    apple.configure()
    _login(TestClient(app), apple)
    uid = _uid()
    apple.fake.revoke_status = 503
    assert _purge_now() == [uid]
    assert system.user(uid) is None  # hesap ve veri yine silindi
    q = json.loads(system.get(f"apple_revoke:{uid}"))
    assert q["attempts"] == 1 and apple.fake.refresh_token not in system.get(f"apple_revoke:{uid}")
    n = len(apple.fake.calls)
    assert a.retry_pending(time.time()) == []  # zamanı gelmedi (artan aralık)
    assert len(apple.fake.calls) == n
    apple.fake.revoke_status = 200
    assert a.retry_pending(time.time() + 2 * 3600) == [uid]
    assert apple.fake.paths()[-1] == "/auth/revoke" and system.get(f"apple_revoke:{uid}") is None
    results = [x.get("result") for x in deletion.record(uid)["notes"] if x.get("action") == "apple_revoke"]
    assert results == ["retry", "revoked"]


def test_revoke_invalid_grant_counts_as_done_and_give_up_after_30_days(apple):
    from app import apple as a
    from app import system
    from app.api.main import app

    apple.configure()
    _login(TestClient(app), apple)
    uid = _uid()
    apple.fake.revoke_status, apple.fake.revoke_error = 400, "invalid_grant"  # kullanıcı bağı zaten kaldırmış
    _purge_now()
    assert system.get(f"apple_revoke:{uid}") is None
    # sürekli hata: 30 gün sonra bırakılır (belge sonsuza dek saklanmaz)
    system.put("apple_revoke:999", json.dumps({"rt": a.seal("x", "apple-rt:999"), "client_id": BUNDLE,
                                               "since": time.time() - 31 * 86400, "attempts": 9, "next": 0}))
    apple.fake.revoke_status, apple.fake.revoke_error = 500, None
    assert a.retry_pending() == [999] and system.get("apple_revoke:999") is None


def test_admin_delete_also_revokes(apple):
    from app import auth
    from app.api.main import app

    apple.configure()
    _login(TestClient(app), apple)
    uid = _uid()
    apple.fake.calls.clear()
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    assert c.delete(f"/api/admin/users/{uid}").status_code == 200
    assert apple.fake.paths() == ["/auth/revoke"]
    assert auth.token_user(None) is None


def test_purge_survives_apple_module_crash(apple, monkeypatch):
    from app import apple as a
    from app import system
    from app.api.main import app

    _login(TestClient(app), apple)
    monkeypatch.setattr(a, "on_delete", lambda uid, now=None: (_ for _ in ()).throw(RuntimeError("bozuk")))
    uid = _uid()
    assert _purge_now() == [uid] and system.user(uid) is None
