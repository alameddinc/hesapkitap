import pytest


def _fake_telegram(monkeypatch, updates):
    from app import telegram

    calls = []

    def call(method, timeout=20, **kw):
        calls.append(method)
        if method == "getMe":
            return {"username": "ornek_bot"}
        if method == "getUpdates":
            return updates
        return {}

    monkeypatch.setattr(telegram, "_call", call)
    monkeypatch.setattr(telegram, "token", lambda: "x")
    return calls


def msg(uid, chat_id, name, text):
    return {"update_id": uid, "message": {"text": text, "chat": {"id": chat_id, "type": "private", "first_name": name}}}


def test_only_chat_with_pairing_code_connects(tmpdb, monkeypatch):
    from app import telegram

    updates = []
    _fake_telegram(monkeypatch, updates)
    with pytest.raises(telegram.TelegramError):
        telegram.discover_chat()  # kod alınmadan bağlanamaz

    code = telegram.start_pairing()["code"]
    # yabancı biri bot adını bilip /start yazdı (en son mesaj o) — bağlanmamalı
    updates += [msg(1, 111, "Sahibi", f"/start {code}"), msg(2, 999, "Yabanci", "/start")]
    info = telegram.discover_chat()
    assert info["chat_id"] == 111 and tmpdb.get_setting("telegram_chat_name") == "Sahibi"
    assert tmpdb.get_setting("telegram_pair") is None  # kod tek kullanımlık
    with pytest.raises(telegram.TelegramError):
        telegram.discover_chat()


def test_wrong_or_expired_code(tmpdb, monkeypatch):
    from app import telegram

    updates = [msg(1, 999, "Yabanci", "/start ZZZZZZ")]
    _fake_telegram(monkeypatch, updates)
    telegram.start_pairing()
    with pytest.raises(telegram.TelegramError):
        telegram.discover_chat()
    code = tmpdb.get_setting("telegram_pair").split(":")[0]
    tmpdb.set_setting("telegram_pair", f"{code}:1")  # süresi geçmiş
    updates.append(msg(2, 111, "Sahibi", f"/start {code}"))
    with pytest.raises(telegram.TelegramError, match="süresi"):
        telegram.discover_chat()
