"""PDF'siz banka maili: pazarlama / bilgilendirme yapay zekâya gitmez; gerçek işlem bildirimleri kaçmaz.
Metinler uydurmadır (tutar, kart, kişi)."""
from email.message import EmailMessage

import pytest

NOTIFS = [
    ("Kart harcama bildirimi", "Sayın Müşterimiz, 4543 **** **** 1234 numaralı kartınızla 19.11.2026 14:32 tarihinde "
     "ORNEK MARKET işyerinde 1.245,90 TL harcama yapılmıştır."),
    ("Hesabınıza para girişi", "TR12 0001 0000 0000 0000 0000 01 numaralı hesabınıza 20.11.2026 09:15 itibarıyla "
     "AYSE YILMAZ tarafından 2.500,00 TL FAST ile gönderilmiştir."),
    ("EFT bildirimi", "Hesabınızdan 750,00 TL tutarında EFT işleminiz gerçekleşmiştir."),
    ("Bilgilendirme", "Kredi kartı ödemeniz alınmıştır. Tutar: 9.000,00 TL. Tarih: 18.11.2026"),
    ("Para çekme", "ATM'den 1.000 TL çekim yapılmıştır. 20.11.2026 11:02"),
    # pazarlama kelimesi geçse de işlem kalıbı var: okunur
    ("Kampanya kapsamında iade", "Kampanya kapsamında 19.11.2026 tarihli harcamanıza 50,00 TL iade yapılmıştır."),
]
PROMOS = [
    ("Size özel ihtiyaç finansmanı", "Değerli müşterimiz, size özel %2,49 kâr oranıyla ihtiyaç finansmanı teklifimiz "
     "hazır. Şubemizden Ahmet Bey sizi arayacak."),
    ("Kaçırılmayacak fırsat", "Kredi kartınıza özel avantajlar ve faiz oranı indirimleri sizi bekliyor!"),
    ("Yeni kampanya", "Bu ay market alışverişlerinize puan kazanma fırsatı. Detaylar için tıklayın."),
]


def _msg(subject: str, body: str, bulk: bool = False, mid: str = "<1@x>") -> EmailMessage:
    m = EmailMessage()
    m["From"] = "Banka <bilgi@vakifbank.com.tr>"
    m["To"] = "fatura-abc@posta.example"
    m["Subject"] = subject
    m["Date"] = "Fri, 20 Nov 2026 10:00:00 +0300"
    m["Message-ID"] = mid
    if bulk:
        m["List-Unsubscribe"] = "<mailto:cikis@vakifbank.com.tr>"
    m.set_content(body)
    return m


@pytest.mark.parametrize("subject,body", NOTIFS)
def test_real_notifications_pass(subject, body):
    from app import mailbox

    assert mailbox.promo_reason(_msg(subject, body), subject, body) is None
    assert mailbox.promo_reason(_msg(subject, body, bulk=True), subject, body) is None  # toplu başlık olsa da


@pytest.mark.parametrize("subject,body", PROMOS)
def test_promotions_skipped(subject, body):
    from app import mailbox

    assert "Reklam" in mailbox.promo_reason(_msg(subject, body), subject, body)


def test_bulk_header_without_transaction_skipped():
    from app import mailbox

    m = _msg("Hesap bilgilendirme", "Mobil uygulamamız yenilendi.", bulk=True)
    assert "toplu" in mailbox.promo_reason(m, "Hesap bilgilendirme", "Mobil uygulamamız yenilendi.")
    m2 = _msg("Duyuru", "Mobil uygulamamız yenilendi.")
    m2["Precedence"] = "bulk"
    assert mailbox.promo_reason(m2, "Duyuru", "Mobil uygulamamız yenilendi.")


def test_inbound_promo_never_reaches_ai(tmpdb, monkeypatch):
    from app import auth, db, doc_reads, inbound, mailbox, system

    u = auth.create_user("ece@ornek.com", "ece-sifre-12345")
    calls = []
    monkeypatch.setattr(mailbox, "handle_notification", lambda *a, **k: calls.append(a) or "🏦 bildirim")
    monkeypatch.setattr(doc_reads, "charge", lambda *a, **k: calls.append(("charge", a)))
    to = inbound.address(u)
    s, b = PROMOS[0]
    r = inbound.handle(_msg(s, b, mid="<p@x>").as_bytes(), to, verify=lambda raw: {"vakifbank.com.tr"})
    assert r["status"] == "skip" and "Reklam" in r["detail"] and calls == []
    s, b = NOTIFS[0]
    r = inbound.handle(_msg(s, b, mid="<n@x>").as_bytes(), to, verify=lambda raw: {"vakifbank.com.tr"})
    assert r["status"] == "ok" and len(calls) == 1
    with db.use_space(system.user_db_path(u), u):
        assert "Reklam" in db.mail_log(5)[-1]["detail"] or any("Reklam" in x["detail"] for x in db.mail_log(5))


def test_imap_path_promo_skipped(monkeypatch):
    from app import mailbox

    monkeypatch.setattr(mailbox, "sender_check", lambda msg: (True, "vakifbank.com.tr"))
    monkeypatch.setattr(mailbox, "handle_notification", lambda *a, **k: pytest.fail("yapay zekâya gitmemeli"))
    s, b = PROMOS[1]
    r = mailbox.process("<i@x>", _msg(s, b))
    assert r["status"] == "skip" and "Reklam" in r["detail"]
