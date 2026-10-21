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


PAIR_TTL = 10 * 60  # saniye


def start_pairing() -> dict:
    """Tek seferlik bağlantı kodu üret. Sadece bu kodu /start ile gönderen sohbet bağlanabilir;
    böylece bot adını bilen başka biri araya giremez."""
    import secrets
    import time

    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    db.set_setting("telegram_pair", f"{code}:{int(time.time()) + PAIR_TTL}")
    bot = _call("getMe")["username"]
    return {"code": code, "bot": bot, "link": f"https://t.me/{bot}?start={code}", "expires_in": PAIR_TTL}


def discover_chat() -> dict:
    """Eşleştirme kodunu içeren /start mesajını bul, o sohbeti kaydet."""
    import time

    raw = db.get_setting("telegram_pair") or ""
    code, _, exp = raw.partition(":")
    if not code or not exp.isdigit() or int(exp) < time.time():
        raise TelegramError("Bağlantı kodu yok ya da süresi doldu. Önce 'Bağlantı kodu al'a bas.")
    updates = _call("getUpdates", json={"timeout": 0, "allowed_updates": ["message"]})
    msgs = [u["message"] for u in updates if "message" in u
            and u["message"].get("chat", {}).get("type") == "private"
            and code in (u["message"].get("text") or "").upper()]
    if not msgs:
        raise TelegramError("Kod bota henüz gelmedi. Bağlantıya dokunup Telegram'da BAŞLAT'a bas, sonra tekrar dene.")
    chat = msgs[-1]["chat"]
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username", "")
    db.set_setting("telegram_chat_id", str(chat["id"]))
    db.set_setting("telegram_chat_name", name)
    db.set_setting("telegram_pair", None)
    # işlenen mesajları Telegram kuyruğundan düşür
    if updates:
        _call("getUpdates", json={"offset": updates[-1]["update_id"] + 1, "timeout": 0})
    return {"chat_id": chat["id"], "name": name}


def send(text: str) -> None:
    _call("sendMessage", json={"chat_id": chat_id(), "text": text, "parse_mode": "HTML",
                               "disable_web_page_preview": True})


def send_document(filename: str, data: bytes, caption: str = "") -> None:
    _call("sendDocument", timeout=60, data={"chat_id": chat_id(), "caption": caption},
          files={"document": (filename, data, "application/octet-stream")})


# ---------------------------------------------------------------- gelen mesajlar: bota PDF ilet → içe aktar

MAX_FILE = 20 * 1024 * 1024  # Bot API indirme sınırı


def download(file_id: str) -> bytes:
    f = _call("getFile", json={"file_id": file_id})
    try:
        r = httpx.get(f"https://api.telegram.org/file/bot{token()}/{f['file_path']}", timeout=60)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise TelegramError(f"Dosya indirilemedi: {e}") from e
    return r.content


def handle_message(m: dict) -> str | None:
    """Bağlı sohbetten gelen tek mesaj. PDF → ekstre/döküm içe aktarılır; sonuç metni döner."""
    doc = m.get("document")
    if doc:
        name = doc.get("file_name") or "belge.pdf"
        if doc.get("mime_type") != "application/pdf" and not name.lower().endswith(".pdf"):
            return "Şimdilik sadece PDF alıyorum (kart ekstresi ya da hesap dökümü)."
        if (doc.get("file_size") or 0) > MAX_FILE:
            return "Dosya 20 MB'tan büyük; uygulamadan yükle."
        from app import mailbox

        try:
            return "✅ " + mailbox.handle_pdf(name, download(doc["file_id"]))
        except (mailbox.MailError, TelegramError) as e:
            return f"⚠️ {e}"
    text = (m.get("text") or "").strip()
    if not text or text.startswith("/start"):
        return None
    return ("Kart ekstresi ya da hesap dökümü PDF'ini buraya gönder (mailden, WhatsApp'tan ya da Dosyalar'dan "
            "\"Paylaş → Telegram\"), içe aktarıp sonucu yazayım.")


def poll_inbox() -> int:
    """Bota gelen yeni mesajları işle. SADECE bağlı sohbet dikkate alınır; başkası PDF gönderse de işlenmez."""
    import html
    import logging

    if not enabled():
        return 0
    offset = int(db.get_setting("telegram_offset") or 0)
    updates = _call("getUpdates", json={"offset": offset, "timeout": 0, "allowed_updates": ["message"]})
    handled = 0
    for u in updates:
        # önce ilerle: işlerken patlayan bir mesaj sonsuza dek tekrar denenmesin
        db.set_setting("telegram_offset", str(u["update_id"] + 1))
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(chat_id()):
            continue
        handled += 1
        try:
            reply = handle_message(m)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).exception("telegram message failed")
            reply = f"⚠️ İşlenemedi: {e}"
        if reply:
            send(html.escape(reply))
    return handled
