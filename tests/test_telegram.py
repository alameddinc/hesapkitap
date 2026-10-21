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


def test_inbox_imports_pdf_only_from_paired_chat(tmpdb, monkeypatch):
    from app import mailbox, telegram

    tmpdb.set_setting("telegram_chat_id", "42")
    monkeypatch.setattr(telegram, "token", lambda: "T")
    sent, calls = [], []
    updates = [
        {"update_id": 5, "message": {"chat": {"id": 99}, "document": {"file_id": "x", "file_name": "e.pdf",
                                                                       "mime_type": "application/pdf"}}},
        {"update_id": 6, "message": {"chat": {"id": 42}, "document": {"file_id": "f1", "file_name": "ekstre.pdf",
                                                                       "mime_type": "application/pdf", "file_size": 1000}}},
        {"update_id": 7, "message": {"chat": {"id": 42}, "text": "merhaba"}},
    ]

    def fake_call(method, timeout=20, **kw):
        calls.append((method, kw))
        if method == "getUpdates":
            return updates if kw["json"]["offset"] == 0 else []
        if method == "sendMessage":
            sent.append(kw["json"]["text"])
            return {}
        raise AssertionError(method)

    monkeypatch.setattr(telegram, "_call", fake_call)
    monkeypatch.setattr(telegram, "download", lambda fid: b"%PDF" if fid == "f1" else (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(mailbox, "handle_pdf", lambda name, data: f"{name} içe aktarıldı")
    assert telegram.poll_inbox() == 2          # 99'dan gelen yok sayıldı
    assert sent[0] == "✅ ekstre.pdf içe aktarıldı" and "PDF" in sent[1]
    assert tmpdb.get_setting("telegram_offset") == "8"
    assert telegram.poll_inbox() == 0          # tekrar işlenmez
