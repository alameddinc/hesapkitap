"""Güvenlik denetimi 18.11, O1/O2/D3: iptal edilmiş oturumun gece temizliğinden sonra yeniden canlanması, kişisel API
anahtarlarının şifre değişimi / sıfırlama / "tümünden çık"ta düşmemesi. Veriler uydurmadır."""
import time

from fastapi.testclient import TestClient

from tests.test_sessions import _get, _mobile_login, owner  # noqa: F401 - fixture


def _age(token: str, days: float, created_too: bool = False) -> None:
    from app import auth, sessions, system

    ts = time.time() - days * 86400
    with system.connect() as c:
        c.execute("UPDATE sessions SET last_seen = ?" + (", created_at = ?" if created_too else "") + " WHERE sid_hash = ?",
                  (ts, ts, sessions._h(auth.token_nonce(token))) if created_too else (ts, sessions._h(auth.token_nonce(token))))


# ---------------------------------------------------------------- O1

def test_logged_out_token_stays_dead_after_cleanup(owner):  # noqa: F811
    """PoC C1: çıkış → 31 gün → gece temizliği satırı siliyordu → aynı anahtar "eski oturum" sayılıp 200 alıyordu."""
    from app import sessions

    c, h = _mobile_login()
    assert c.post("/api/auth/logout", headers=h).status_code == 200
    assert _get(h) == 401
    _age(h["authorization"][7:], 31)
    sessions.cleanup()
    assert _get(h) == 401


def test_idle_expired_token_stays_dead_after_cleanup(owner):  # noqa: F811
    """PoC C1b: boşta süresi (14 gün) dolan anahtar temizlikten sonra yeniden geçerli oluyordu."""
    from app import sessions

    c, h = _mobile_login()
    _age(h["authorization"][7:], 40)
    assert _get(h) == 401
    sessions.cleanup()
    assert _get(h) == 401


def test_cleanup_keeps_active_sessions_and_drops_only_expired_rows(owner):  # noqa: F811
    """Etkin mobil oturum düşmez; satır yalnız ondan çıkmış anahtarın mutlak ömrü (180 gün) bittikten sonra silinir."""
    from app import sessions, system

    a, ha = _mobile_login()   # etkin, bugün kullanıldı
    b, hb = _mobile_login()   # çıkış yaptı, satırı 205 gün önce açılmış (anahtarın süresi zaten dolmuş)
    b.post("/api/auth/logout", headers=hb)
    _age(hb["authorization"][7:], 205, created_too=True)  # varsayılan saklama 200 gün
    with system.connect() as c:
        before = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert sessions.cleanup() == 1
    with system.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before - 1
    assert _get(ha) == 200
    # (gerçekte 205 günlük satırın anahtarının imzalı süresi — en çok 180 gün — çoktan dolmuştur; testte anahtar yeni)


def test_legacy_token_without_row_still_works(owner):  # noqa: F811
    """Geriye uyum: oturum tablosundan önce verilmiş (satırı hiç olmayan) anahtar düşürülmez."""
    from app import system

    a, h = _mobile_login()
    with system.connect() as c:
        c.execute("DELETE FROM sessions")
    assert _get(h) == 200


# ---------------------------------------------------------------- O2

def _token(c) -> str:
    r = c.post("/api/settings/tokens", json={"name": "kisayol"}).json()
    tok = r.get("token") or r.get("value")
    assert tok and tok.startswith("hk_")
    return tok


def _login():
    from app.api.main import app

    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return c


def test_password_change_revokes_personal_tokens(owner):  # noqa: F811
    """PoC C2: hesabı ele geçirenin açtığı hk_ anahtarı şifre değişiminden sonra da çalışıyordu."""
    from app import system

    c = _login()
    tok = _token(c)
    assert system.token_owner(tok) is not None
    assert c.post("/api/settings/password", json={"current": "yonetici-sifre-1", "new": "yeni-sifre-98765"}).status_code == 200
    assert system.token_owner(tok) is None
    from app.api.main import app

    assert TestClient(app).get("/api/summary", headers={"authorization": f"Bearer {tok}"}).status_code == 401


def test_revoke_all_revokes_personal_tokens(owner):  # noqa: F811
    from app import system

    c = _login()
    tok = _token(c)
    assert c.post("/api/me/sessions/revoke-all", json={"keep_current": True}).status_code == 200
    assert system.token_owner(tok) is None


def test_set_password_revokes_personal_tokens(owner):  # noqa: F811
    """Şifre sıfırlama ve yöneticinin geçici şifresi auth.set_password'dan geçer."""
    from app import auth, system

    c = _login()
    tok = _token(c)
    auth.set_password(system.user_by_name("admin")["id"], "sifirlanan-sifre-42")
    assert system.token_owner(tok) is None


def test_plain_logout_keeps_personal_tokens(owner):  # noqa: F811
    """Tek oturumdan çıkış kişisel anahtarları düşürmez (kısayollar çalışmaya devam eder)."""
    from app import system

    c = _login()
    tok = _token(c)
    c.post("/api/auth/logout")
    assert system.token_owner(tok) is not None
