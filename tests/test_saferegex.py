"""Kategori kuralı deseni ReDoS koruması (app/saferegex.py, güvenlik denetimi). Veriler uydurmadır."""
import threading
import time

import pytest

EVIL = [r"(A+)+$", r"(A|AA)+$", r"(\w*)*$", r"(a)\1", r"(?=A+)X", "A" * 201]


@pytest.mark.parametrize("pattern", EVIL, ids=["nested", "alt", "star-star", "backref", "look", "long"])
def test_unsafe_patterns_rejected_on_test_save_and_patch(client, pattern):
    r = client.get("/api/rules/test", params={"text": "A" * 30 + "!", "pattern": pattern})
    assert r.status_code == 422, r.text
    assert client.post("/api/rules", json={"pattern": pattern, "category": "Diğer"}).status_code == 422
    rid = client.post("/api/rules", json={"pattern": "ORNEKDUKKAN", "category": "Diğer"}).json()["id"]
    assert client.patch(f"/api/rules/{rid}", json={"pattern": pattern}).status_code == 422


@pytest.mark.parametrize("pattern", [r"MIGROS|A101", r"(?:ORNEK )?MARKET", r"UBER(?! EATS)", r"\bKIRA\b", r"A{2,}B",
                                     r"(AB){3}", r"(?:X Y)+", r"GOOGLE ?\*?CLOUD"])
def test_ordinary_patterns_still_allowed(client, pattern):
    from app import saferegex

    assert saferegex.problem(pattern) is None
    assert client.get("/api/rules/test", params={"text": "x", "pattern": pattern}).status_code == 200


def test_default_rules_pass_the_check():
    from app import saferegex
    from app.categorize import DEFAULT_RULES

    assert [p for p, *_ in DEFAULT_RULES if saferegex.problem(p)] == []


def test_stored_evil_rule_is_skipped_without_blocking(client):
    """Denetimden önce kaydedilmiş (ya da yedekten/eşitlemeden gelmiş) kötü desen: çökertmez, süreci kilitlemez,
    atlanır; sonraki kural yine çalışır."""
    from app import db

    with db.connect() as c:
        c.execute("INSERT INTO rules (pattern, category, priority) VALUES (?, 'Diğer', 0)", (r"(A+)+$",))
    client.post("/api/rules", json={"pattern": "AAAA", "category": "Eğitim", "priority": 5})
    s = time.time()
    r = client.get("/api/rules/test", params={"text": "A" * 40 + "!"})
    assert r.status_code == 200 and r.json()["category"] == "Eğitim"
    assert client.get("/api/rules").status_code == 200
    db.recategorize_all()
    assert time.time() - s < 2


def test_runtime_timeout_does_not_hold_the_gil(monkeypatch):
    """Statik denetim bir deseni kaçırsa bile: çalışırken süre sınırı var, diğer iş parçacıkları (başka kullanıcıların
    istekleri) beklemez ve desen bundan sonra atlanır."""
    from app import saferegex

    monkeypatch.setattr(saferegex, "problem", lambda p, max_len=0: None)  # denetimi devre dışı bırak: yalnız 2. katman
    monkeypatch.setattr(saferegex, "_bad", set())
    saferegex.compiled.cache_clear()
    ticks = []
    stop = threading.Event()

    def other():
        while not stop.is_set():
            ticks.append(time.time())
            time.sleep(0.005)

    th = threading.Thread(target=other)
    th.start()
    time.sleep(0.03)
    s = time.time()
    try:
        assert saferegex.search(r"(A+)+$", "A" * 400 + "!") is None
        dur = time.time() - s
    finally:
        stop.set()
        th.join()
        saferegex.compiled.cache_clear()
    assert dur < 0.5
    assert r"(A+)+$" in saferegex._bad
    assert len([x for x in ticks if s <= x <= s + dur]) >= 2  # arama sürerken öteki iş parçacığı ilerledi


def test_runtime_timeout_marks_pattern_bad(monkeypatch):
    from app import saferegex

    pat = "ORNEKZAMAN"
    rx = saferegex.compiled(pat)

    class Slow:
        def search(self, *a, **k):
            raise TimeoutError

    monkeypatch.setattr(saferegex, "compiled", lambda p: Slow() if p == pat else rx)
    monkeypatch.setattr(saferegex, "_bad", set())
    assert saferegex.search(pat, "ORNEKZAMAN") is None
    assert pat in saferegex._bad
