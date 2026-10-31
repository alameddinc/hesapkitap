"""S1 kalanları: istemcinin hesapladığı başlık toplamları sunucudan (uydurma veri)."""
from datetime import date

from app import db


def _entry(client, d, desc, amount, kind="gider", **kw):
    r = client.post("/api/entries", json={"entry_date": d, "description": desc, "amount": amount, "kind": kind, **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_transfers_totals(client, tmpdb):
    _entry(client, "2026-10-05", "Ali Örnek", 300, "gider", counterparty="Ali Örnek", payment="havale")
    _entry(client, "2026-10-06", "Ali Örnek", 100, "gelir", counterparty="Ali Örnek")
    _entry(client, "2026-09-06", "Veli Örnek", 999, "gider", counterparty="Veli Örnek", payment="havale")
    with db.connect() as c:
        db.add_own_transfer(c, "2026-10-07", -500.0, "Banka A", "Kendi hesabına", "o1")
        db.add_own_transfer(c, "2026-10-07", 500.0, "Banka B", "Kendi hesabından", "o2")
        db.add_own_transfer(c, "2026-09-07", -50.0, "Banka A", "Kendi hesabına", "o3")
    t = client.get("/api/transfers", params={"start": "2026-10-01", "end": "2026-10-31"}).json()["totals"]
    assert t == {"sent": 300.0, "received": 100.0, "net": -200.0, "own_in": 500.0, "own_out": 500.0, "own_count": 2}
    assert client.get("/api/transfers").json()["totals"]["own_count"] == 3


def test_bills_summary_overall(client, tmpdb):
    for d, a in (("2026-08-05", 100), ("2026-09-05", 200), ("2026-10-05", 300)):
        _entry(client, d, "Elektrik A · fatura", a, "gider", category="Elektrik")
        _entry(client, d, "Su B · fatura", 50, "gider", category="Su")
    s = client.get("/api/bills/summary", params={"until": "2026-10"}).json()
    o = s["overall"]
    assert o["last"] == {"month": "2026-10", "amount": 350.0}
    assert o["average_12"] == round((150 + 250 + 350) / 3, 2)
    assert o["vs_prev_month"] == {"month": "2026-09", "amount": 250.0, "diff": 100.0, "pct": 40.0}
    assert all("average_12" in c for c in s["categories"])


def test_holdings_totals(client, tmpdb, monkeypatch):
    from app import zakat

    monkeypatch.setattr(zakat, "prices", lambda *a, **k: {"prices": {"USD": 40.0}, "updated": "2026-10-31"})
    pid = next(p["id"] for p in zakat.people() if p["is_self"]) if zakat.people() else None
    if pid is None:
        pid = client.post("/api/zakat/people", json={"name": "Ben", "is_self": True}).json()["id"]
    for q, cost in ((100, 3000), (50, None)):
        r = client.post("/api/zakat/assets", json={"person_id": pid, "kind": "USD", "quantity": q, "cost": cost})
        assert r.status_code == 200, r.text
    t = client.get("/api/holdings").json()["totals"]["self"]
    assert t["value"] == 6000.0 and t["cost"] == 3000.0 and t["gain"] == 1000.0 and t["gain_pct"] == 33.3
    assert t["with_cost"] == 1 and t["count"] == 2


def test_payables_paid_this_month(client, tmpdb):
    today = date.today().isoformat()
    bid = client.post("/api/bills", json={"provider": "İnternet C", "amount": 400, "due_date": today}).json()["id"]
    client.post("/api/bills", json={"provider": "Telefon D", "amount": 250, "due_date": today})
    client.post(f"/api/bills/{bid}/pay", json={"amount": 400, "paid_at": today, "payment": "havale"})
    p = client.get("/api/payables", params={"totals": 1}).json()["totals"]["paid_this_month"]
    assert p == {"month": today[:7], "total": 400.0, "count": 1}


def test_budgets_totals(client, tmpdb):
    from app import finance

    m = finance.current_period()
    lo, _ = finance.period_bounds(m)
    for cat, lim in (("Market", 1000), ("Ulaşım", 200)):
        client.put("/api/budgets", json={"category": cat, "monthly_limit": lim})
    _entry(client, lo.isoformat(), "Market E", 400, "gider", category="Market")
    _entry(client, lo.isoformat(), "Taksi F", 250, "gider", category="Ulaşım")
    t = client.get("/api/budgets").json()["totals"]
    assert t == {"limit": 1200.0, "spent": 650.0, "remaining": 550.0, "pct": 54.2, "over": 1, "count": 2}


def test_rules_test_whole_ruleset(client, tmpdb):
    rid = client.post("/api/rules", json={"pattern": "ORNEKMARKET", "category": "Market", "priority": 1}).json()["id"]
    r = client.get("/api/rules/test", params={"text": "ÖrnekMarket şube 12"}).json()
    assert r["match"] and r["rule"]["id"] == rid and r["category"] == "Market"
    none = client.get("/api/rules/test", params={"text": "zzqq bilinmeyen"}).json()
    assert none["category"] == "Kategorisiz" and none["rule"] is None
    assert client.get("/api/rules/test", params={"text": "ABC", "pattern": "A.C"}).json()["match"] is True  # eski biçim
    client.post("/api/item-rules", json={"keywords": "ornekkola", "category": "İçecek"})
    it = client.post("/api/item-rules/test", json={"text": "ORNEKKOLA 1L"}).json()
    assert it["category"] == "İçecek" and it["keyword"] == "ornekkola" and it["rule_id"]


def test_cards_summary(client, upload):
    upload()
    s = client.get("/api/cards/summary").json()
    nw = client.get("/api/net-worth").json()
    assert [c["card_no"] for c in s["cards"]] == [c["card_no"] for c in nw["credit_cards"]]
    assert s["totals"]["unpaid"] == nw["totals"]["card_debt"]
    c0 = s["cards"][0]
    assert c0["last_statement"]["period_end"] == nw["credit_cards"][0]["last_period_end"]
    assert len(c0["recent"]) <= 20 and {"count", "total", "next_month"} <= set(c0["installments_left"])
    full = [r for r in client.get("/api/transactions", params={"card": c0["card_no"]}).json()
            if r["kind"] != "odeme"]  # ?card tüm satırlar (ödeme dahil); özetin 'recent'i ödemesiz
    assert len(full) == c0["recent_total"] and [r["uid"] for r in full[:len(c0["recent"])]] == [r["uid"] for r in c0["recent"]]
    assert all(r["account"] == c0["card_no"] for r in full if r["source"] == "card")
    page = client.get("/api/transactions", params={"card": c0["card_no"], "limit": 5}).json()
    assert len(page["rows"]) == min(5, len(full))
    assert client.get("/api/cards/summary", params={"recent": 3}).json()["cards"][0]["recent"] == c0["recent"][:3]
