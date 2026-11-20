from app import mail_approval as m


def _info(monkeypatch, body):
    saved = {}
    monkeypatch.setattr(m.db, "set_setting", lambda k, v: saved.update({k: v}))
    return m.save_gmail_confirmation(body, "2026-01-01")


def test_turkce_dogrulama_kodu(monkeypatch):
    body = ("ornek@example.com adlı kullanıcı postalarını otomatik olarak iletmek istiyor.\n"
            "Doğrulama kodu: 123456789\nOnaylamak için: https://mail-settings.google.com/mail/vf-abc")
    assert _info(monkeypatch, body)["code"] == "123456789"


def test_ingilizce(monkeypatch):
    body = "Confirmation code: 987654321\nhttps://mail-settings.google.com/mail/vf-x"
    assert _info(monkeypatch, body)["code"] == "987654321"


def test_etiketsiz_tek_dokuz_hane(monkeypatch):
    body = "Bu isteği onaylamak için 555666777 numarasını girin https://mail-settings.google.com/mail/vf-y"
    assert _info(monkeypatch, body)["code"] == "555666777"


def test_kod_yok_baglanti_var(monkeypatch):
    info = _info(monkeypatch, "Onayla: https://mail-settings.google.com/mail/vf-z")
    assert info["code"] is None and info["link"]


# ---------------------------------------------------------------- kimden geldi: güvenilir mi (uydurma adresler)

BODY = ("{who} has requested to automatically forward mail to your email address.\n"
        "Confirmation code: 246813579\nhttps://mail-settings.google.com/mail/vf-ornek\n")


def test_trusted_only_if_known_person(monkeypatch):
    assert _info(monkeypatch, BODY.format(who="Ece.Yil@ornek.com"))["trusted"] is False  # kişi listesi yok
    saved = {}
    monkeypatch.setattr(m.db, "set_setting", lambda k, v: saved.update({k: v}))
    ok = m.save_gmail_confirmation(BODY.format(who="Ece.Yil@ornek.com"), "2026-01-01", ["ece.yil@ornek.com"])
    assert ok["trusted"] is True
    bad = m.save_gmail_confirmation(BODY.format(who="yabanci@kotu.example"), "2026-01-01", ["ece.yil@ornek.com"])
    assert bad["trusted"] is False
    nowho = m.save_gmail_confirmation("Confirmation code: 246813579", "2026-01-01", ["ece.yil@ornek.com"])
    assert nowho["from_email"] is None and nowho["trusted"] is False


def _mail(who: str) -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "Gmail Team <forwarding-noreply@google.com>"
    msg["To"] = "x@ornek.app"
    msg["Subject"] = "Gmail Forwarding Confirmation"
    msg["Date"] = "Tue, 20 Oct 2026 10:00:00 +0300"
    msg["Message-ID"] = f"<g-{who}@x>"
    msg.set_content(BODY.format(who=who))
    return msg.as_bytes()


def test_untrusted_hidden_until_reveal_and_not_added(client, monkeypatch):
    """Saldırgan kendi Gmail'inde kurbanın adresini yönlendirme olarak ekler: kod/bağlantı "Benim" demeden dönmez."""
    from app import auth, inbound
    from app.api import main

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    main._name_checks.clear()
    owner = auth.create_admin("ece.yil@ornek.com", "ece-sifre-12345")
    assert client.post("/api/auth/login", json={"username": "ece.yil@ornek.com", "password": "ece-sifre-12345"}).status_code == 200
    to = inbound.address(owner)
    inbound.handle(_mail("saldirgan@kotu.example"), to, verify=lambda raw: {"google.com"})
    g = client.get("/api/mail/pending").json()["gmail_confirmation"]
    assert g["trusted"] is False and g["code"] is None and g["link"] is None and g["from_email"] == "saldirgan@kotu.example"
    rv = client.post("/api/mail/gmail-confirmation/reveal").json()
    assert rv["code"] == "246813579" and rv["link"].startswith("https://mail-settings.google.com/")
    assert "saldirgan@kotu.example" not in inbound.senders()  # gösterildi, güvenilen listeye eklenmedi
    assert client.delete("/api/mail/gmail-confirmation").status_code == 200
    assert client.post("/api/mail/gmail-confirmation/reveal").status_code == 404
    # kendi adresinden gelen istek: doğrudan gösterilir
    inbound.handle(_mail("ece.yil@ornek.com"), to, verify=lambda raw: {"google.com"})
    g = client.get("/api/mail/pending").json()["gmail_confirmation"]
    assert g["trusted"] is True and g["code"] == "246813579"
