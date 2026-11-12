"""Net durum (GET /api/net-worth net_display, yetenek "net_display"): kesilecek kart ekstresi (tahmini) de düşülür.
Eski "net" alanı anlamını korur (eski istemciler). Tutarlar uydurmadır."""
from datetime import date

from tests.test_card_estimate import _statement


def _hesap(client, bakiye=50000):
    client.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": bakiye})


def test_net_display_without_estimate_equals_net(client, tmpdb):
    _hesap(client)
    with tmpdb.connect() as c:
        _statement(c, date.today().isoformat(), date.today().isoformat(), 12000.00)
    nw = client.get("/api/net-worth").json()
    assert nw["card_estimated"] == 0
    assert nw["net_display"] == nw["net"] == nw["totals"]["net_display"] == nw["net_after_estimates"]
    assert "net_display" in client.get("/api/meta").json()["capabilities"]


def test_net_display_subtracts_unbilled_card_estimate(client, tmpdb, monkeypatch):
    """Kesilmemiş ekstrenin tahmini (payables "estimate") net_display'den düşer; net ve harcanabilir değişmez."""
    from app import finance

    _hesap(client)
    alacak = client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "alacak", "amount": 1500, "debt_date": "2026-10-03"})
    assert alacak.status_code == 200
    once = client.get("/api/net-worth").json()
    asil = finance.payables

    def payables(today=None):
        return asil(today) + [{"type": "estimate", "id": None, "title": "Örnek Bank 4111***1111", "subtitle": "Sonraki ekstre (tahmini)",
                               "amount": 7350.40, "min_payment": None, "due_date": "2099-01-01", "days_left": 30,
                               "paid_amount": None, "paid_at": None, "status": "tahmini", "in_total": False, "key": "estimate:x"}]

    monkeypatch.setattr(finance, "payables", payables)
    nw = client.get("/api/net-worth").json()
    assert nw["card_estimated"] == 7350.40
    assert nw["net"] == once["net"]  # eski anlam: kesilecek ekstre düşülmez
    assert nw["spendable"] == once["spendable"]  # Harcanabilir aynı kalır
    assert nw["net_display"] == round(once["net"] - 7350.40, 2) == nw["totals"]["net_display"] == nw["net_after_estimates"]
