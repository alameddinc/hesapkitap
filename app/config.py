"""Ayarlar: önce ortam değişkeni (.env, sunucu yöneticileri için), yoksa uygulama içinden girilen değer (DB).

Böylece hızlı kurulumda hiç .env gerekmez: anahtarlar Ayarlar sayfasından girilir.
Değerler API'den asla tam haliyle geri dönmez (sadece son 4 karakter).
"""
import os
import secrets

from app import db

# Uygulama içinden düzenlenebilen ayarlar
EDITABLE = {
    "GEMINI_API_KEY": {"secret": True, "label": "Gemini API anahtarı"},
    "GEMINI_MODEL": {"secret": False, "label": "Gemini modeli", "default": "gemini-3.1-flash-lite"},
    "TELEGRAM_BOT_TOKEN": {"secret": True, "label": "Telegram bot token"},
    "BACKUP_PASSPHRASE": {"secret": True, "label": "Yedek şifresi"},
    "MAIL_USER": {"secret": False, "label": "E-posta adresi (otomasyon kutusu)"},
    "MAIL_PASSWORD": {"secret": True, "label": "E-posta uygulama şifresi"},
    "MAIL_IMAP_HOST": {"secret": False, "label": "IMAP sunucusu", "default": "imap.gmail.com"},
    "MAIL_FOLDER": {"secret": False, "label": "Klasör", "default": "INBOX"},
    "MAIL_SENDERS": {"secret": False, "label": "Ek banka alan adları (virgülle)"},
}


def get(name: str) -> str | None:
    v = os.environ.get(name)
    if v:
        return v
    v = db.get_setting(f"cfg:{name}")
    if v:
        return v
    return EDITABLE.get(name, {}).get("default")


def source(name: str) -> str:
    if os.environ.get(name):
        return "env"
    return "app" if db.get_setting(f"cfg:{name}") else "none"


def set_value(name: str, value: str | None) -> None:
    if name not in EDITABLE:
        raise KeyError(name)
    db.set_setting(f"cfg:{name}", value.strip() if value and value.strip() else None)


def describe() -> list[dict]:
    out = []
    for name, meta in EDITABLE.items():
        v, src = get(name), source(name)
        hint = None
        if v and src != "none":
            hint = f"••••{v[-4:]}" if meta["secret"] else v
        out.append({"name": name, "label": meta["label"], "secret": meta["secret"], "source": src,
                    "value_hint": hint, "default": meta.get("default")})
    return out


def secret_key() -> str:
    """Sunucu geneli gizli anahtar (sistem veritabanında): env yoksa ilk açılışta üretilip saklanır."""
    from app import system

    v = os.environ.get("SECRET_KEY") or system.get("secret_key")
    if not v:
        v = secrets.token_hex(32)
        system.put("secret_key", v)
    return v
