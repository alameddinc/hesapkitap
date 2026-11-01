"""C9: mobilde kurulamayan ayarlar için "bu ayarlar web'de" bilgisi (GET /api/settings/web-only)."""
from tests.test_plans import _user, hosted  # noqa: F401 - fikstür


def _keys(r):
    return [i["key"] for i in r.json()["items"]]


def test_self_hosted_admin_sees_all_web_only_settings(client, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_PUBLIC_URL", "https://hesap.ornek.test/")
    r = client.get("/api/settings/web-only")
    assert r.status_code == 200
    d = r.json()
    assert d["self_hosted"] is True
    assert d["url"] == "https://hesap.ornek.test/#/ayarlar"
    assert _keys(r) == ["keys", "mail", "bank_templates", "telegram", "backup", "share_tokens", "pdf_passwords"]
    # her madde web Ayarlar'daki bölüm başlığıyla yol tarif eder
    assert all(i["section"] and i["title"] and i["why"] for i in d["items"])
    mail = next(i for i in d["items"] if i["key"] == "mail")
    assert mail["configured"] is False


def test_configured_flags_follow_settings(client):
    client.put("/api/settings/config", json={"name": "MAIL_USER", "value": "kutu@ornek.test"})
    client.put("/api/settings/config", json={"name": "MAIL_PASSWORD", "value": "uygulama-sifresi"})
    items = {i["key"]: i for i in client.get("/api/settings/web-only").json()["items"]}
    assert items["mail"]["configured"] is True
    assert items["keys"]["configured"] is False


def test_url_falls_back_to_request_host(client, monkeypatch):
    monkeypatch.delenv("HESAPKITAP_PUBLIC_URL", raising=False)
    assert client.get("/api/settings/web-only").json()["url"].endswith("/#/ayarlar")


def test_hosted_member_sees_only_unlocked_items(hosted):  # noqa: F811
    c, _ = _user(hosted)
    d = c.get("/api/settings/web-only").json()
    assert d["self_hosted"] is False
    # hazır sürümde yönetici olmayan: anahtar/IMAP/şablon kilitli, Telegram/yedek yönetici işi (web ile aynı kural)
    assert [i["key"] for i in d["items"]] == ["share_tokens", "pdf_passwords"]


def test_capability_advertised(client):
    assert "web_only_settings" in client.get("/api/meta").json()["capabilities"]
