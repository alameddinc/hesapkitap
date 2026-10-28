"""Giden posta (SMTP): şifre sıfırlama kodu gibi sistem mailleri.

Ayarlanmazsa kapalıdır; kendi sunucusunu kuran istediği SMTP'yi (Gmail uygulama şifresi, Resend, Brevo…) verebilir.
SMTP_HOST, SMTP_PORT (465/2465 = SSL, 587/2587 = STARTTLS; bazı sunucu firmaları 465'i kapatır, 587 kullan), SMTP_USER, SMTP_PASSWORD, MAIL_FROM ("HesapKitap <noreply@…>").
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("hesapkitap.mailer")


def enabled() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("MAIL_FROM"))


def send(to: str, subject: str, text: str, html: str | None = None) -> None:
    if not enabled():
        raise RuntimeError("Posta gönderimi ayarlı değil")
    m = EmailMessage()
    m["From"] = os.environ["MAIL_FROM"]
    m["To"] = to
    m["Subject"] = subject
    m.set_content(text)
    if html:
        m.add_alternative(html, subtype="html")
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 465)
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    ctx = ssl.create_default_context()
    if port in (465, 2465):
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
            if user:
                s.login(user, pw or "")
            s.send_message(m)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ctx)
            if user:
                s.login(user, pw or "")
            s.send_message(m)
