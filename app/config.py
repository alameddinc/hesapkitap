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


# Sunucu geneli: sistem veritabanında, sadece yönetici değiştirir. Gemini anahtarı herkes için ortak kullanılır
# (kotayla); kullanıcı kendi anahtarını girerse onunki kullanılır ve kotaya takılmaz.
GLOBAL = {"GEMINI_API_KEY", "GEMINI_MODEL", "TELEGRAM_BOT_TOKEN"}
ADMIN_ONLY = {"GEMINI_MODEL", "TELEGRAM_BOT_TOKEN"}


def is_admin() -> bool:
    """Bağlamda kullanıcı yoksa (tek kullanıcılı kurulum, zamanlayıcı ana alanı, testler) yönetici sayılır."""
    u = db.current_user()
    return u is None or u.get("role") == "admin"


def _system_value(name: str) -> str | None:
    from app import system

    return system.get(f"cfg:{name}")


def locked() -> bool:
    """Barındırılan sürümde kullanıcı anahtar/entegrasyon ayarı görmez ve giremez (Gemini, Telegram, e-posta kutusu):
    yapay zekâ paketin kotasıyla sunucunun anahtarından gelir."""
    from app import plans

    return plans.hosted() and not is_admin()


def own_ai_key() -> str | None:
    """Yönetici olmayan kullanıcının kendi Gemini anahtarı (varsa). Barındırılan sürümde yok."""
    return None if is_admin() or locked() else db.get_setting("cfg:GEMINI_API_KEY")


def get(name: str) -> str | None:
    v = os.environ.get(name)
    if v:
        return v
    if name == "GEMINI_API_KEY" and own_ai_key():
        return own_ai_key()
    v = _system_value(name) if name in GLOBAL else db.get_setting(f"cfg:{name}")
    if v:
        return v
    return EDITABLE.get(name, {}).get("default")


def source(name: str) -> str:
    if name == "GEMINI_API_KEY" and not is_admin():
        return "app" if own_ai_key() else "none"  # kullanıcı sadece kendi anahtarını görür
    if os.environ.get(name):
        return "env"
    v = _system_value(name) if name in GLOBAL else db.get_setting(f"cfg:{name}")
    return "app" if v else "none"


def set_value(name: str, value: str | None) -> None:
    if name not in EDITABLE:
        raise KeyError(name)
    if not is_admin() and (name in ADMIN_ONLY or locked()):
        raise PermissionError(name)
    v = value.strip() if value and value.strip() else None
    if name in GLOBAL and is_admin():
        from app import system

        system.put(f"cfg:{name}", v)
    else:
        db.set_setting(f"cfg:{name}", v)


def describe() -> list[dict]:
    if locked():
        return []
    out = []
    admin = is_admin()
    for name, meta in EDITABLE.items():
        if not admin and name in ADMIN_ONLY:
            continue
        src = source(name)
        v = (own_ai_key() if name == "GEMINI_API_KEY" and not admin else get(name)) if src != "none" else None
        hint = None
        if v:
            hint = f"••••{v[-4:]}" if meta["secret"] else v
        label = meta["label"]
        if name == "GEMINI_API_KEY" and not admin:
            label = "Kendi Gemini anahtarın (isteğe bağlı; girersen ücretsiz kotaya takılmazsın)"
        out.append({"name": name, "label": label, "secret": meta["secret"], "source": src,
                    "value_hint": hint, "default": meta.get("default")})
    return out


def migrate_globals() -> None:
    """Eski kurulum: sunucu geneli anahtarlar ana veritabanındaydı → sistem veritabanına (bir kez)."""
    from app import system

    with db.use_space(None):
        for name in GLOBAL:
            old = db.get_setting(f"cfg:{name}")
            if old and not system.get(f"cfg:{name}"):
                system.put(f"cfg:{name}", old)


def secret_key() -> str:
    """Sunucu geneli gizli anahtar (sistem veritabanında): env yoksa ilk açılışta üretilip saklanır."""
    from app import system

    v = os.environ.get("SECRET_KEY") or system.get("secret_key")
    if not v:
        v = secrets.token_hex(32)
        system.put("secret_key", v)
    return v
