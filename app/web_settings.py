"""Yalnız web'de kurulan ayarlar (denetim C9).

Kendi sunucusunu kuran kullanıcı IMAP e-posta kutusunu, Telegram'ı, yedekleri, anahtarları ve paylaş anahtarlarını
mobil uygulamada kuramıyor; bunun söylendiği bir yer de yoktu. Bu modül, o kullanıcının web'de kurabileceği (ve mobilde
karşılığı olmayan) ayarların listesini, web Ayarlar sayfasının bölüm adlarıyla ve "kurulu mu" bilgisiyle döndürür.
Mobil, `web_only_settings` yeteneği varsa GET /api/settings/web-only ile çekip "Bu ayarlar web'de" bağlantısını
gösterir.

Görünürlük kuralları web `pages/Settings.tsx` ile aynı:
- `locked` (hazır sürümde yönetici olmayan): Anahtarlar, Banka şablonları ve E-posta otomasyonu yok.
- Telegram ve Yedekler yalnız yönetici.
- Telefondan paylaş ve Hatırlanan PDF şifreleri herkes.
Rakam/veri değiştirmez; yalnız okur.
"""
from app import config, db, plans

SETTINGS_ROUTE = "#/ayarlar"


def _configured(name: str) -> bool:
    try:
        return bool(config.get(name))
    except Exception:  # noqa: BLE001 - durum bilgisi; okunamazsa "kurulu değil" say
        return False


def items() -> list[dict]:
    """[{key, title, section, why, configured}] — section: web Ayarlar'daki bölüm başlığı (kullanıcıya yol tarifi)."""
    from app import system

    admin = config.is_admin()
    locked = config.locked()
    out: list[dict] = []

    def add(key: str, title: str, section: str, why: str, configured: bool | None) -> None:
        out.append({"key": key, "title": title, "section": section, "why": why, "configured": configured})

    if not locked:
        add("keys", "Yapay zekâ anahtarı", "Anahtarlar",
            "Fiş ve tanınmayan banka belgelerini okumak için Gemini anahtarı.", _configured("GEMINI_API_KEY"))
        add("mail", "E-posta otomasyonu (IMAP)", "E-posta otomasyonu",
            "Bankadan gelen e-ekstre ve bildirim postaları kendiliğinden içe aktarılır.",
            _configured("MAIL_USER") and _configured("MAIL_PASSWORD"))
        add("bank_templates", "Banka şablonları", "Banka şablonları",
            "Tanınmayan bankanın belge düzenini bir kez öğret, sonra yapay zekâsız okunsun.", None)
    if admin:
        from app import telegram

        add("telegram", "Telegram hatırlatmaları", "Telegram hatırlatmaları",
            "Son ödeme hatırlatmaları, ay özeti ve şifreli yedeğin Telegram'a gelmesi.", bool(telegram.chat_id()))
        add("backup", "Yedekler", "Yedekler",
            "Sunucudaki günlük yedekler, yedekten geri dönme ve yedek şifresi.", _configured("BACKUP_PASSPHRASE"))
    u = db.current_user()
    add("share_tokens", "Telefondan paylaş anahtarı", "Telefondan paylaş",
        "iOS Kestirmeler / Android paylaşımı için anahtar.", bool(u and system.tokens(u["id"])) if u else None)
    add("pdf_passwords", "Hatırlanan PDF şifreleri", "Hatırlanan PDF şifreleri",
        "Şifreli ekstrelerin bir kez girilen şifreleri; buradan silinir.", bool(db.pdf_passwords()))
    return out


def info(public_base: str) -> dict:
    """{self_hosted, url, items}. url: web Ayarlar sayfası (web arayüzü API ile aynı adreste sunulur)."""
    return {"self_hosted": not plans.hosted(), "url": f"{public_base.rstrip('/')}/{SETTINGS_ROUTE}", "items": items()}
