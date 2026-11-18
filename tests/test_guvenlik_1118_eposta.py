"""Güvenlik denetimi 18.11, D3: e-posta doğrulama kodu deneme sayacı atomik. E-postalar uydurmadır."""
from concurrent.futures import ThreadPoolExecutor

from tests.test_email_verify import _app, _code, _register, hosted  # noqa: F401 - fixture


def test_email_code_tries_are_atomic(hosted):  # noqa: F811
    """Aynı anda gelen yanlış denemeler sayacı aynı değerden okuyup 5'i aşamaz; 5 denemeden sonra doğru kod da geçmez."""
    from app import email_verify, system

    _register(_app(), "deneme@example.com", "deneme-sifre-123")
    code = _code(hosted, "deneme@example.com")
    wrong = "000000" if code != "000000" else "111111"
    with ThreadPoolExecutor(8) as pool:
        assert not any(pool.map(lambda _: email_verify.verify("deneme@example.com", wrong), range(20)))
    with system.connect() as c:
        assert c.execute("SELECT tries FROM email_codes").fetchone()[0] == email_verify.MAX_TRIES
    assert email_verify.verify("deneme@example.com", code) is None


def test_email_code_correct_within_tries(hosted):  # noqa: F811
    from app import email_verify

    _register(_app(), "dogru@example.com", "dogru-sifre-123")
    code = _code(hosted, "dogru@example.com")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(email_verify.MAX_TRIES - 1):
        assert email_verify.verify("dogru@example.com", wrong) is None
    assert email_verify.verify("dogru@example.com", code) is not None
