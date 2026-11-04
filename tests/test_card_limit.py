"""Kart limiti ve kalan kullanılabilir limit (cards/summary limit/available/used_pct, PUT /api/cards/limit).
Veriler uydurmadır (örnek kart numaraları, örnek işyerleri)."""
from datetime import date

from tests.test_book import _member, _setup_book, owner  # noqa: F401  (owner: fixture)

CARD = "4111***1111"
TODAY = date(2026, 10, 20)


def _statement(c, total=5000.0, limit=30000.0, available=None, card=CARD):
    sid = c.execute("INSERT INTO statements (file_hash, filename, bank, card_no, period_end, due_date, total_debt, "
                    "credit_limit, available_limit, next_period_end, next_due_date) "
                    "VALUES (?, 'x.pdf', 'Örnek Bank', ?, '2026-10-15', '2026-10-25', ?, ?, ?, '2026-11-15', '2026-11-25')",
                    (f"h{card}", card, total, limit, available)).lastrowid
    c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
              "VALUES (?, '2026-10-01', 'ORNEK MARKET', ?, 'harcama', 'Market & Gıda')", (sid, total))
    return sid


def _spend(tmpdb, amount, d="2026-10-18"):
    tmpdb.create_entry({"entry_date": d, "description": "Örnek Kafe", "amount": amount, "kind": "gider",
                        "category": "Yeme-İçme", "payment": "kredi_karti", "card_last4": "1111"})


def _card(today=TODAY):
    from app import finance

    return next(c for c in finance.cards_summary(today)["cards"] if c["card_no"] == CARD)


def test_reader_reads_limit_and_available():
    from app import card_reader
    from app.parsers import kuveytturk
    from tests.conftest import FIXTURES

    st = kuveytturk.parse_text((FIXTURES / "kuveytturk.txt").read_text())
    assert (st.credit_limit, st.available_limit) == (50000.0, 40000.0)
    h = card_reader.header("Kullanılabilir Kart Limiti 12.000,00\nKart Limiti 20.000,00", "tr")
    assert (h["limit"], h["available"]) == (20000.0, 12000.0)  # "kullanılabilir kart limiti" toplam limit sayılmaz


def test_available_is_limit_minus_debt_and_open_cycle(client, tmpdb):
    with tmpdb.connect() as c:
        _statement(c)
    _spend(tmpdb, 1200)
    k = _card()
    assert k["cycle"]["so_far"] == 1200.0
    assert (k["limit"], k["limit_source"], k["statement_limit"]) == (30000.0, "ekstre", 30000.0)
    assert k["available"] == 30000 - 5000 - 1200 and k["available_source"] == "tahmini"
    assert k["used"] == 6200.0 and k["used_pct"] == 20.7
    # ödeme sonrası: kesilmiş ekstrenin kalanı düşer, kalan limit artar
    with tmpdb.connect() as c:
        c.execute("UPDATE statements SET paid_amount = 2000, paid_at = '2026-10-19'")
    k = _card()
    assert k["unpaid"] == 3000.0 and k["available"] == 30000 - 3000 - 1200


def test_statement_available_limit_then_estimate(client, tmpdb):
    with tmpdb.connect() as c:
        _statement(c, available=24000.0)  # banka taksit blokesini de düşmüş (30000 − 5000 − 1000)
    k = _card()
    assert (k["available"], k["available_source"], k["available_as_of"]) == (24000.0, "ekstre", "2026-10-15")
    _spend(tmpdb, 1200)
    with tmpdb.connect() as c:
        c.execute("UPDATE statements SET paid_amount = 5000, paid_at = '2026-10-19'")
    k = _card()
    assert (k["available"], k["available_source"], k["available_as_of"]) == (24000 + 5000 - 1200, "tahmini", None)


def test_manual_limit(client, tmpdb):
    with tmpdb.connect() as c:
        _statement(c, limit=None)
    assert _card()["limit"] is None and _card()["available"] is None and _card()["used_pct"] is None
    r = client.put("/api/cards/limit", json={"card_no": CARD, "limit": 20000})
    assert r.status_code == 200, r.text
    assert r.json()["limit"] == 20000.0 and r.json()["limit_source"] == "elle"
    k = client.get("/api/cards/summary").json()["cards"][0]
    assert (k["limit"], k["limit_source"], k["available"]) == (20000.0, "elle", 20000 - k["unpaid"] - (k["cycle"] or {}).get("so_far", 0))
    # ekstrede limit varken elle düzeltme önce gelir; kaldırınca ekstredeki geçerli
    with tmpdb.connect() as c:
        c.execute("UPDATE statements SET credit_limit = 30000, available_limit = 25000")
    assert (_card()["limit"], _card()["available"]) == (20000.0, 25000 - 10000)  # bankanın rakamı ± elle limit farkı
    assert client.put("/api/cards/limit", json={"card_no": CARD, "limit": None}).json()["limit_source"] == "ekstre"
    assert _card()["limit"] == 30000.0
    assert client.put("/api/cards/limit", json={"card_no": "5400***9999", "limit": 1000}).status_code == 404
    assert client.put("/api/cards/limit", json={"card_no": CARD, "limit": -5}).status_code == 422
    assert "card_limit" in client.get("/api/meta").json()["capabilities"]


def test_hidden_card_limit_not_leaked_to_member(owner):  # noqa: F811
    _setup_book(owner)
    cards = owner.get("/api/cards/summary").json()["cards"]
    kuveyt = next(c for c in cards if c["bank"] == "Kuveyt Türk")
    assert kuveyt["limit"] == 50000.0 and kuveyt["limit_source"] == "ekstre"
    m = _member(owner)
    owner.put("/api/book/rules", json={"hidden_cards": [kuveyt["card_no"]]})
    seen = m.get("/api/cards/summary").json()["cards"]
    assert kuveyt["card_no"] not in {c["card_no"] for c in seen} and all("limit" in c for c in seen)
    # üye limiti değiştiremez (defter sahibi); gizli kartı hiç
    assert m.put("/api/cards/limit", json={"card_no": kuveyt["card_no"], "limit": 1}).status_code == 403
    assert m.put("/api/cards/limit", json={"card_no": seen[0]["card_no"], "limit": 1}).status_code == 403
    full = _member(owner, "kardes", role="full")
    assert full.put("/api/cards/limit", json={"card_no": kuveyt["card_no"], "limit": 1}).status_code == 404
    assert owner.get("/api/cards/summary").json()["cards"][0]["limit_source"] == "ekstre"
