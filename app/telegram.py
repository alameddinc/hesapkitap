"""Telegram bildirimleri. Token: Ayarlar ya da TELEGRAM_BOT_TOKEN; sohbet kimliği bağlanınca DB'ye kaydedilir."""
import httpx

from app import config, db

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    pass


def token() -> str | None:
    return config.get("TELEGRAM_BOT_TOKEN") or None


def chat_id() -> str | None:
    return db.get_setting("telegram_chat_id")


def enabled() -> bool:
    return bool(token() and chat_id())


def _call(method: str, timeout: float = 20, **kw) -> dict:
    t = token()
    if not t:
        raise TelegramError("Telegram bot token tanımlı değil (Ayarlar → Anahtarlar)")
    try:
        r = httpx.post(API.format(token=t, method=method), timeout=timeout, **kw)
    except httpx.HTTPError as e:
        raise TelegramError(f"Telegram'a ulaşılamadı: {e}") from e
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not j.get("ok"):
        raise TelegramError(j.get("description") or f"HTTP {r.status_code}")
    return j["result"]


def discover_chat() -> dict:
    """Kullanıcı bota /start yazdıktan sonra son mesajdan sohbet kimliğini bul ve kaydet."""
    updates = _call("getUpdates", json={"timeout": 0, "allowed_updates": ["message"]})
    msgs = [u["message"] for u in updates if "message" in u and u["message"].get("chat", {}).get("type") == "private"]
    if not msgs:
        raise TelegramError("Mesaj bulunamadı. Telegram'da bota /start yaz, sonra tekrar dene.")
    chat = msgs[-1]["chat"]
    db.set_setting("telegram_chat_id", str(chat["id"]))
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username", "")
    return {"chat_id": chat["id"], "name": name}


def send(text: str) -> None:
    _call("sendMessage", json={"chat_id": chat_id(), "text": text, "parse_mode": "HTML",
                               "disable_web_page_preview": True})


def send_document(filename: str, data: bytes, caption: str = "") -> None:
    _call("sendDocument", timeout=60, data={"chat_id": chat_id(), "caption": caption},
          files={"document": (filename, data, "application/octet-stream")})
