"""Elle kayıtta ve sabit kalem onayında çapa günü sorusu (yetenek entry_anchor_conflict; telefon testi 17.11): kullanıcı
hesabın bakiyesini BUGÜN girdi, SONRA geçmiş tarihli (15 Kas) maaşı "yattı" dedi ya da elle gelir/gider ekledi. Kayıt
günü çapa günü ya da öncesinde → yanıtta anchor_conflict; in_balance true → o bakiyede zaten var (bakiye değişmez),
false → sonra oldu (yansır); yoksa eski kural (çapa gününden önceki kayıt bakiyede sayılır). Tutarlar ve adlar uydurmadır."""
from datetime import date

import pytest

from app import finance
from tests.test_gercek_gun_tekrari import freeze

TODAY = date(2026, 11, 17)
START = 30000.0
MAAS = 42000.0


@pytest.fixture()
def book(client, tmpdb, monkeypatch):
    freeze(monkeypatch, TODAY)
    assert client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": START}).status_code == 200
    with tmpdb.connect() as c:
        aid = c.execute("SELECT id FROM accounts WHERE name = 'Örnek Banka'").fetchone()[0]
    r = client.post("/api/recurring", json={"description": "Maaş", "amount": MAAS, "kind": "gelir", "category": "Maaş",
                                            "day_of_month": 15, "start_month": "2026-10", "account_id": aid})
    assert r.status_code == 200, r.text
    return {"a": aid, "rid": r.json()["id"]}


def _cur(aid):
    return next(a["current"] for a in finance.net_worth(TODAY)["accounts"] if a["id"] == aid)


CONF = {"name": "Örnek Banka", "as_of": TODAY.isoformat()}


@pytest.mark.parametrize("in_balance,expect", [(True, START), (False, START + MAAS), (None, START)])
def test_salary_confirm_before_anchor(client, book, in_balance, expect):
    """Maaş 15'inde yattı, bakiye 17'sinde girildi: sorulur; cevap bakiyeyi belirler (cevapsız: eski kural, içinde)."""
    body = {"month": "2026-11"}
    if in_balance is not None:
        body["in_balance"] = in_balance
    r = client.post(f"/api/recurring/{book['rid']}/confirm", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["anchor_conflict"] == {"account_id": book["a"], **CONF}
    assert _cur(book["a"]) == expect


def test_pending_salary_row_carries_account(client, book):
    """Bekleyen maaş satırı kalemin hesabını taşır: istemci onaydan önce çapa sorusunu sorabilsin."""
    rows = client.get("/api/transactions", params={"month": "2026-11"}).json()
    rows = rows["rows"] if isinstance(rows, dict) else rows
    p = next(x for x in rows if x.get("recurring_id") == book["rid"] and x.get("pending"))
    assert p["account_id"] == book["a"] and p["date"] == "2026-11-15"


def test_expense_recurring_confirm_false_is_deducted(client, book):
    """Gider kalemi (kira) de aynı: 'sonra ödedim' → bakiyeden düşer."""
    r = client.post("/api/recurring", json={"description": "Kira", "amount": 9000.0, "kind": "gider", "category": "Kira",
                                            "day_of_month": 10, "start_month": "2026-10", "account_id": book["a"]})
    rid = r.json()["id"]
    r = client.post(f"/api/recurring/{rid}/confirm", json={"month": "2026-11", "in_balance": False})
    assert r.status_code == 200 and r.json()["anchor_conflict"]["account_id"] == book["a"]
    assert _cur(book["a"]) == START - 9000.0


def test_confirm_after_anchor_no_conflict(client, book, monkeypatch):
    """Kalemin günü çapadan sonra (bakiye 12'sinde girildi, maaş 15'inde): soru yok, maaş yansır."""
    assert client.patch(f"/api/accounts/{book['a']}", json={"balance": START, "as_of": "2026-11-12"}).status_code == 200
    r = client.post(f"/api/recurring/{book['rid']}/confirm", json={"month": "2026-11"})
    assert r.status_code == 200 and "anchor_conflict" not in r.json()
    assert _cur(book["a"]) == START + MAAS


def test_confirm_without_account_no_conflict(client, book):
    r = client.post("/api/recurring", json={"description": "Ek gelir", "amount": 500.0, "kind": "gelir",
                                            "day_of_month": 3, "start_month": "2026-10"})
    r = client.post(f"/api/recurring/{r.json()['id']}/confirm", json={"month": "2026-11", "in_balance": False})
    assert r.status_code == 200 and "anchor_conflict" not in r.json()
    assert _cur(book["a"]) == START


@pytest.mark.parametrize("kind,in_balance,expect", [
    ("gelir", True, START), ("gelir", False, START + 1500.0), ("gelir", None, START),
    ("gider", True, START), ("gider", False, START - 1500.0),
])
def test_manual_entry_before_anchor(client, book, kind, in_balance, expect):
    body = {"entry_date": "2026-11-15", "description": "Örnek kayıt", "amount": 1500.0, "kind": kind,
            "payment": "havale" if kind == "gider" else None, "account_id": book["a"]}
    if in_balance is not None:
        body["in_balance"] = in_balance
    r = client.post("/api/entries", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["anchor_conflict"] == {"account_id": book["a"], **CONF}
    assert _cur(book["a"]) == expect


def test_manual_entry_same_day_true_stays_inside(client, book):
    """Çapa GÜNÜ girilen kayıt eski kuralla bakiyeye eklenir; 'zaten var' denirse eklenmez."""
    body = {"entry_date": TODAY.isoformat(), "description": "Örnek gelir", "amount": 700.0, "kind": "gelir", "account_id": book["a"]}
    r = client.post("/api/entries", json=body)
    assert r.json()["anchor_conflict"]["as_of"] == TODAY.isoformat()
    assert _cur(book["a"]) == START + 700.0
    r = client.post("/api/entries", json={**body, "in_balance": True})
    assert _cur(book["a"]) == START + 700.0


def test_manual_entry_after_anchor_or_without_account(client, book):
    r = client.post("/api/entries", json={"entry_date": "2026-11-18", "description": "Örnek", "amount": 100.0, "kind": "gelir",
                                          "account_id": book["a"]})
    assert r.status_code == 200 and "anchor_conflict" not in r.json()
    r = client.post("/api/entries", json={"entry_date": "2026-11-15", "description": "Örnek", "amount": 100.0, "kind": "gider",
                                          "payment": "kredi_karti", "in_balance": False})
    assert r.status_code == 200 and "anchor_conflict" not in r.json()


def test_capability_listed(client):
    assert "entry_anchor_conflict" in client.get("/api/meta").json()["capabilities"]
