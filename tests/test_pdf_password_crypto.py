"""Hatırlanan PDF şifreleri defter ayarında sunucu anahtarıyla şifreli durur; eski düz kayıtlar ilk okumada göçer."""
import json

TEST_PW = "998877"  # uydurma


def _raw(db) -> list:
    return json.loads(db.get_setting("pdf_passwords"))


def test_written_encrypted(tmpdb):
    tmpdb.remember_pdf_password(TEST_PW)
    raw = _raw(tmpdb)
    assert len(raw) == 1 and raw[0].startswith("pw1:") and TEST_PW not in raw[0]
    assert tmpdb.pdf_passwords() == [TEST_PW]
    tmpdb.remember_pdf_password(TEST_PW)  # tekrar eklenmez
    assert len(_raw(tmpdb)) == 1


def test_same_password_different_ciphertext(tmpdb):
    assert tmpdb._seal_pdf_password(TEST_PW) != tmpdb._seal_pdf_password(TEST_PW)  # rastgele nonce


def test_plain_legacy_migrates_on_read(tmpdb):
    tmpdb.set_setting("pdf_passwords", json.dumps(["1111", "2222"]))
    assert tmpdb.pdf_passwords() == ["1111", "2222"]
    raw = _raw(tmpdb)
    assert all(x.startswith("pw1:") for x in raw) and "1111" not in json.dumps(raw)
    assert tmpdb.pdf_passwords() == ["1111", "2222"]  # göç sonrası aynı sıra, aynı değer


def test_mixed_and_garbage_entries(tmpdb):
    sealed = tmpdb._seal_pdf_password("3333")
    tmpdb.set_setting("pdf_passwords", json.dumps([sealed, "4444", "pw1:bozuk!!", 5, ""]))
    assert tmpdb.pdf_passwords() == ["3333", "4444"]  # okunamayan atlanır
    assert "pw1:bozuk!!" in _raw(tmpdb)  # ama silinmez
    tmpdb.forget_pdf_password(1)  # "4444"
    assert tmpdb.pdf_passwords() == ["3333"] and "pw1:bozuk!!" in _raw(tmpdb)


def test_wrong_key_cannot_decrypt(tmpdb, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-anahtar-bir")
    tmpdb.remember_pdf_password(TEST_PW)
    monkeypatch.setenv("SECRET_KEY", "test-anahtar-iki")
    assert tmpdb.pdf_passwords() == []
    assert len(_raw(tmpdb)) == 1  # kayıt kaybolmaz
    monkeypatch.setenv("SECRET_KEY", "test-anahtar-bir")
    assert tmpdb.pdf_passwords() == [TEST_PW]


def test_key_separate_from_apple_purpose(tmpdb):
    from app import apple

    blob = apple.seal(TEST_PW, "x")
    assert tmpdb._unseal_pdf_password("pw1:" + blob[3:]) is None


def test_api_list_masked(client, tmpdb):
    tmpdb.set_setting("pdf_passwords", json.dumps(["12345678901"]))  # eski düz kayıt
    r = client.get("/api/settings/pdf-passwords")
    assert r.status_code == 200
    body = r.text
    assert "12345678901" not in body and "pw1:" not in body
    assert r.json() == [{"index": 0, "hint": "1•••••••••1"}]
    assert "12345678901" not in tmpdb.get_setting("pdf_passwords")  # okuma göçü tetikledi
    assert client.delete("/api/settings/pdf-passwords/0").json() == {"ok": True}
    assert tmpdb.pdf_passwords() == []
