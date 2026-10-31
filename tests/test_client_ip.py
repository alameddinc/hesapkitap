"""İstemci IP'si: başlıklara yalnız güvenilir vekilden gelince inanılır (hız sınırı ve kurulum kodu atlanamasın).

Adresler uydurmadır: belgeleme aralıkları (RFC 5737) ve example.com bloğu. 198.51.100/24 ve 203.0.113/24
Python'da is_private sayıldığından "internetten gelen" istemci 93.184.216.x ile temsil edilir.
"""
import pytest
from fastapi.testclient import TestClient

PUBLIC = ("93.184.216.50", 40000)      # vekilsiz, internetten doğrudan bağlanan istemci
PROXY = ("172.18.0.5", 40000)         # docker ağındaki nginx (varsayılan güvenilir vekil)
LAN = ("192.168.1.10", 40000)         # evdeki ağdan doğrudan bağlanan kullanıcı


@pytest.fixture()
def app_auth(tmpdb, monkeypatch):
    from app import auth
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.delenv("HESAPKITAP_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("HESAPKITAP_TRUST_CF", raising=False)
    auth._fails.clear()
    auth._account_fails.clear()
    return app


def _login(c, pw, **headers):
    return c.post("/api/auth/login", json={"username": "admin", "password": pw}, headers=headers).status_code


# --- birim: client_ip kuralı -------------------------------------------------------------------------------------

def _req(peer, headers=None):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": peer, "method": "GET", "path": "/"})


def test_direct_public_ignores_all_headers(monkeypatch):
    from app.api.main import client_ip

    monkeypatch.delenv("HESAPKITAP_TRUSTED_PROXIES", raising=False)
    r = _req(PUBLIC, {"cf-connecting-ip": "198.51.100.1", "x-forwarded-for": "127.0.0.1", "x-real-ip": "127.0.0.1"})
    assert client_ip(r) == "93.184.216.50"


def test_trusted_proxy_x_real_ip(monkeypatch):
    from app.api.main import client_ip

    monkeypatch.delenv("HESAPKITAP_TRUSTED_PROXIES", raising=False)
    assert client_ip(_req(PROXY, {"x-real-ip": "198.51.100.7"})) == "198.51.100.7"


def test_trusted_proxy_xff_last_untrusted(monkeypatch):
    from app.api.main import client_ip

    monkeypatch.delenv("HESAPKITAP_TRUSTED_PROXIES", raising=False)
    # istemcinin uydurduğu baştaki öğe yok sayılır; sağdan ilk güvenilmeyen adres gerçek istemcidir
    assert client_ip(_req(PROXY, {"x-forwarded-for": "127.0.0.1, 198.51.100.8, 10.0.0.3"})) == "198.51.100.8"
    assert client_ip(_req(PROXY, {"x-forwarded-for": "not-an-ip"})) == "172.18.0.5"


def test_cf_header_only_when_enabled(monkeypatch):
    from app.api.main import client_ip

    monkeypatch.delenv("HESAPKITAP_TRUSTED_PROXIES", raising=False)
    h = {"cf-connecting-ip": "198.51.100.9", "x-real-ip": "198.51.100.10"}
    monkeypatch.delenv("HESAPKITAP_TRUST_CF", raising=False)
    assert client_ip(_req(PROXY, h)) == "198.51.100.10"
    monkeypatch.setenv("HESAPKITAP_TRUST_CF", "1")
    assert client_ip(_req(PROXY, h)) == "198.51.100.9"
    assert client_ip(_req(PUBLIC, h)) == "93.184.216.50"  # açık olsa da vekil dışından gelene inanılmaz


def test_trusted_proxies_env(monkeypatch):
    from app.api.main import client_ip

    monkeypatch.setenv("HESAPKITAP_TRUSTED_PROXIES", "93.184.216.0/24")
    assert client_ip(_req(PUBLIC, {"x-real-ip": "198.51.100.11"})) == "198.51.100.11"
    assert client_ip(_req(PROXY, {"x-real-ip": "198.51.100.11"})) == "172.18.0.5"  # artık güvenilir değil
    monkeypatch.setenv("HESAPKITAP_TRUSTED_PROXIES", "")
    assert client_ip(_req(PROXY, {"x-real-ip": "198.51.100.11"})) == "172.18.0.5"


# --- uçlar: hız sınırı ---------------------------------------------------------------------------------------------

def test_rate_limit_not_bypassed_by_spoofed_headers(app_auth):
    from app import auth

    auth.create_admin("admin", "dogru-sifre-123")
    c = TestClient(app_auth, client=PUBLIC)
    codes = [_login(c, f"yanlis{i}", **{"cf-connecting-ip": f"198.51.100.{i}", "x-forwarded-for": f"198.51.100.{i}"})
             for i in range(8)]
    assert codes[:5] == [401] * 5
    assert codes[5:] == [429] * 3


def test_users_behind_proxy_counted_separately(app_auth):
    from app import auth

    auth.create_admin("admin", "dogru-sifre-123")
    c = TestClient(app_auth, client=PROXY)
    for _ in range(5):
        assert _login(c, "yanlis", **{"x-real-ip": "198.51.100.20"}) == 401
    assert _login(c, "yanlis", **{"x-real-ip": "198.51.100.20"}) == 429
    # aynı vekilin arkasındaki başka kullanıcı etkilenmez (hepsi tek vekil IP'sine düşmez)
    assert _login(c, "dogru-sifre-123", **{"x-real-ip": "198.51.100.21"}) == 200


# --- hesap başı sayaç (IP'den bağımsız) ----------------------------------------------------------------------------

def test_account_lock_independent_of_ip(app_auth):
    from app import auth

    auth.create_admin("admin", "dogru-sifre-123")
    c = TestClient(app_auth, client=PROXY)
    for i in range(auth.ACCOUNT_MAX_FAILS):
        assert _login(c, "yanlis", **{"x-real-ip": f"198.51.100.{i + 1}"}) == 401
    # her deneme ayrı IP'den geldi; hesap yine de geçici olarak kilitli (doğru şifre de beklemeli)
    assert _login(c, "dogru-sifre-123", **{"x-real-ip": "198.51.100.200"}) == 429
    auth._account_fails.clear()
    assert _login(c, "dogru-sifre-123", **{"x-real-ip": "198.51.100.200"}) == 200


def test_account_counter_reset_on_success(app_auth):
    from app import auth

    auth.create_admin("admin", "dogru-sifre-123")
    c = TestClient(app_auth, client=PROXY)
    for i in range(auth.ACCOUNT_MAX_FAILS - 1):
        _login(c, "yanlis", **{"x-real-ip": f"198.51.100.{i + 1}"})
    assert _login(c, "dogru-sifre-123", **{"x-real-ip": "198.51.100.150"}) == 200
    for i in range(auth.ACCOUNT_MAX_FAILS - 1):
        assert _login(c, "yanlis", **{"x-real-ip": f"198.51.100.{i + 50}"}) == 401
    assert _login(c, "dogru-sifre-123", **{"x-real-ip": "198.51.100.151"}) == 200


def test_unknown_username_locks_too(app_auth):
    """Var olmayan ad da aynı kuralla kilitlenir (kilit, adın varlığını ele vermez)."""
    from app import auth

    auth.create_admin("admin", "dogru-sifre-123")
    c = TestClient(app_auth, client=PROXY)
    codes = [c.post("/api/auth/login", json={"username": "yok-boyle-biri", "password": "x" * 10},
                    headers={"x-real-ip": f"198.51.100.{i + 1}"}).status_code for i in range(auth.ACCOUNT_MAX_FAILS + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {401}


# --- kurulum kodu ------------------------------------------------------------------------------------------------

def _setup(c, **headers):
    return c.post("/api/auth/setup", json={"username": "admin", "password": "saldirgan-sifre-1"}, headers=headers)


def test_setup_code_not_bypassed_with_spoofed_xff(app_auth):
    c = TestClient(app_auth, client=PUBLIC)
    assert c.get("/api/auth/status", headers={"x-forwarded-for": "127.0.0.1"}).json()["setup_code_needed"] is True
    assert _setup(c, **{"x-forwarded-for": "127.0.0.1"}).status_code == 403
    assert _setup(c, **{"x-real-ip": "127.0.0.1", "cf-connecting-ip": "127.0.0.1"}).status_code == 403


def test_setup_behind_proxy_public_client_needs_code(app_auth):
    c = TestClient(app_auth, client=PROXY)
    assert _setup(c, **{"x-real-ip": "93.184.216.30"}).status_code == 403


def test_setup_proxy_passing_spoofed_x_real_ip(app_auth):
    """X-Real-IP'yi ezmeyen vekil (ör. Caddy varsayılanı): istemci X-Real-IP: 127.0.0.1 uydursa da vekilin yazdığı
    X-Forwarded-For gerçek adresi gösterir; kurulum yerel sayılmaz."""
    c = TestClient(app_auth, client=("127.0.0.1", 40000))
    assert _setup(c, **{"x-real-ip": "127.0.0.1", "x-forwarded-for": "93.184.216.31"}).status_code == 403


def test_setup_self_hosted_lan_without_proxy(app_auth):
    """Kendi sunucusu, vekilsiz: evdeki ağdan doğrudan açan kişiye kod sorulmaz (önceki davranış)."""
    c = TestClient(app_auth, client=LAN)
    assert c.get("/api/auth/status").json()["setup_code_needed"] is False
    assert _setup(c).status_code == 200


def test_setup_self_hosted_public_without_proxy(app_auth):
    c = TestClient(app_auth, client=PUBLIC)
    assert c.get("/api/auth/status").json()["setup_code_needed"] is True
    assert _setup(c).status_code == 403


def test_dockerfile_does_not_trust_all_forwarders():
    """uvicorn --forwarded-allow-ips=* soket adresini istemcinin X-Forwarded-For'u ile değiştirir; netaddr boşa çıkar."""
    from pathlib import Path

    cmd = (Path(__file__).parent.parent / "Dockerfile").read_text()
    assert "forwarded-allow-ips=*" not in cmd and "--no-proxy-headers" in cmd
