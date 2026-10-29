"""Aylık belge hatırlatmaları (/api/reminders): kesilen ekstre, ayı kapanan döküm, yaklaşan son ödeme."""
from datetime import date

import pytest

from tests.test_book import _member, owner  # noqa: F401 - fixture


@pytest.fixture()
def at(monkeypatch):
    """Hatırlatmaların "bugün"ünü sabitle (İstanbul günü)."""
    from app import reminders

    def set_(d: str):
        monkeypatch.setattr(reminders, "today", lambda: date.fromisoformat(d))
    return set_


def _statement(db, card="5400******9602", bank="VakıfBank", end="2026-09-26", nxt="2026-10-26", due="2026-10-06",
               debt=1000.0, paid=None):
    with db.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt, next_period_end, "
                  "paid_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (f"h-{card}-{end}", bank, card, end, due, debt, nxt, paid))


def _imported(db, acc_id: int, d: str, n: int = 0):
    db.create_entry({"entry_date": d, "description": f"Hareket {n}", "amount": 10, "kind": "gider",
                     "import_hash": f"x-{acc_id}-{d}-{n}", "account_id": acc_id})


def _ids(client):
    return [r["id"] for r in client.get("/api/reminders").json()]


def test_ekstre_reminder_after_cut_until_uploaded(client, tmpdb, at):
    _statement(tmpdb)
    at("2026-10-27")
    assert _ids(client) == []  # kesimden sonra bir gün bekler (banka PDF'i hemen göndermeyebilir)
    at("2026-10-29")
    r = client.get("/api/reminders").json()
    assert [x["id"] for x in r] == ["ekstre:5400******9602:2026-10"]
    x = r[0]
    assert x["title"] == "VakıfBank ••9602 · Ekim ekstren hazır" and x["cta"] == "Ekstreyi yükle"
    assert "Kesim tarihi 26 Ekim'di" in x["text"] and "posta" not in x["text"]
    assert x["kind"] == "ekstre" and x["severity"] in ("info", "warn") and x["due"] == "2026-10-26"
    _statement(tmpdb, end="2026-10-26", nxt="2026-11-26", due="2026-11-05")  # Ekim ekstresi yüklendi
    assert _ids(client) == []


def test_ekstre_dismiss_hides_this_period_only(client, tmpdb, at):
    _statement(tmpdb)
    at("2026-10-29")
    rid = "ekstre:5400******9602:2026-10"
    assert client.post(f"/api/reminders/{rid}/dismiss").json()["ok"]
    assert _ids(client) == []
    at("2026-11-10")
    assert _ids(client) == []  # aynı dönem: kapalı kalır
    _statement(tmpdb, end="2026-10-26", nxt="2026-11-26", due="2026-11-05", paid=1000)
    at("2026-11-28")
    assert _ids(client) == ["ekstre:5400******9602:2026-11"]  # sonraki dönem yeniden


def test_snooze_hides_until_date(client, tmpdb, at):
    _statement(tmpdb)
    at("2026-10-29")
    rid = "ekstre:5400******9602:2026-10"
    assert client.post(f"/api/reminders/{rid}/snooze", json={"days": 1}).json()["until"] == "2026-10-30"
    assert _ids(client) == []
    at("2026-10-30")
    assert _ids(client) == [rid]
    assert client.post("/api/reminders/bilinmeyen/dismiss").status_code == 422


def test_dokum_only_for_accounts_using_statements(client, tmpdb, at):
    ids = [tmpdb.ensure_account(n) for n in ("Garanti BBVA", "Halkbank", "Ziraat")]
    _imported(tmpdb, ids[0], "2026-09-15")
    _imported(tmpdb, ids[0], "2026-10-20")
    _imported(tmpdb, ids[1], "2026-10-05")
    # Ziraat: elle giriliyor, hiç döküm yok
    tmpdb.create_entry({"entry_date": "2026-10-10", "description": "Elle", "amount": 5, "kind": "gider", "account_id": ids[2]})
    at("2026-10-29")
    assert _ids(client) == []  # ay henüz kapanmadı
    at("2026-11-03")
    r = client.get("/api/reminders").json()
    assert sorted(x["id"] for x in r) == sorted([f"dokum:{ids[0]}:2026-10", f"dokum:{ids[1]}:2026-10"])
    g = next(x for x in r if x["account_id"] == ids[0])
    assert g["title"] == "Garanti BBVA · Ekim dökümü" and g["severity"] == "info"
    assert g["text"].startswith("Ay kapandı. Ekim hesap dökümünü yükle")
    at("2026-11-12")
    assert {x["severity"] for x in client.get("/api/reminders").json()} == {"warn"}  # 10 günden sonra
    _imported(tmpdb, ids[0], "2026-10-31")  # Ekim dökümü geldi (ay sonuna kadar)
    assert _ids(client) == [f"dokum:{ids[1]}:2026-10"]


def test_dokum_uploaded_after_month_end_counts(client, tmpdb, at):
    acc = tmpdb.ensure_account("Halkbank")
    _imported(tmpdb, acc, "2026-10-12")  # sessiz hesap: ayın son günlerinde hareket yok
    with tmpdb.connect() as c:
        c.execute("INSERT INTO bank_documents (file_name, bank, account_id, uploaded_at) VALUES "
                  "('a.pdf', 'Halkbank', ?, '2026-11-02 08:00:00')", (acc,))
    at("2026-11-04")
    assert _ids(client) == []


def test_due_soon_reminder(client, tmpdb, at):
    _statement(tmpdb, end="2026-10-26", nxt="2026-11-26", due="2026-11-05", debt=2500)
    at("2026-11-02")
    r = client.get("/api/reminders").json()
    assert [x["id"] for x in r] == ["odeme:5400******9602:2026-11-05"]
    assert r[0]["title"] == "VakıfBank ••9602 · Son ödeme 3 gün sonra" and r[0]["tag"] == "3 gün kaldı"
    at("2026-11-04")
    assert client.get("/api/reminders").json()[0]["severity"] == "urgent"
    client.post("/api/statements/1/pay", json={"amount": 2500, "paid_at": "2026-11-04"})
    assert _ids(client) == []


def test_member_sees_only_visible_cards_and_own_state(owner, at):  # noqa: F811
    from app import db

    _statement(db)
    _statement(db, card="4000******1234", bank="Kuveyt Türk")
    at("2026-10-29")
    m = _member(owner, role="full")
    assert len(m.get("/api/reminders").json()) == 2
    owner.put("/api/book/rules", json={"hidden_cards": ["4000******1234"]})
    assert [x["bank"] for x in m.get("/api/reminders").json()] == ["VakıfBank"]
    # üyenin kapatması sahibinkini kapatmaz
    assert m.post("/api/reminders/ekstre:5400******9602:2026-10/dismiss").status_code == 200
    assert m.get("/api/reminders").json() == []
    assert len(owner.get("/api/reminders").json()) == 2
    # sadece görür/ekler: yükleyemez, yükleme hatırlatması da yok
    v = _member(owner, name="cocuk", role="viewer")
    assert v.get("/api/reminders").json() == []


def test_onboarding_v2_order_and_member(client, tmpdb):
    o = client.get("/api/onboarding", params={"v": 2}).json()
    assert [s["key"] for s in o["steps"]] == ["hesap", "maas", "kart", "sabit", "butce"]  # posta yalnız hazır sürümde
    assert o["done"] == 0 and all(s["guide"] and s["cta"] for s in o["steps"])
    client.post("/api/accounts", json={"name": "Banka", "kind": "banka", "balance": 10})
    client.post("/api/recurring", json={"description": "Kira", "amount": 15000, "kind": "gider", "day_of_month": 5,
                                        "start_month": "2026-10"})
    o = client.get("/api/onboarding", params={"v": 2}).json()
    assert {s["key"] for s in o["steps"] if s["done"]} == {"hesap", "sabit"} and o["done"] == 2
    assert client.get("/api/onboarding").json()["steps"][0]["key"] == "ekstre"  # web (v1) değişmedi
