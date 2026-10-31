"""S1: istemcilerin kendisi hesapladığı rakamlar sunucudan hazır gelir (tek tanım). Tutarlar uydurmadır."""
from datetime import date, timedelta

M = "2026-10"


def _entry(client, d, desc, amount, kind="gider", category=None, **kw):
    r = client.post("/api/entries", json={"entry_date": d, "description": desc, "amount": amount, "kind": kind,
                                          "category": category, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _seed(client):
    _entry(client, "2026-10-05", "Maaş", 10000, "gelir", "Maaş")
    _entry(client, "2026-10-06", "Market", 1200.5, "gider", "Market")
    _entry(client, "2026-10-07", "Altın alımı", 2000, "gider", "Altın & Döviz Birikimi")
    _entry(client, "2026-10-08", "Kira", 3000, "gider", "Kira & Aidat")


def test_summary_left_fields(client):
    _seed(client)
    t = client.get("/api/summary", params={"month": M}).json()["totals"]
    assert t["net"] == 10000 - 1200.5 - 3000  # birikim gider değil
    assert t["left"] == t["net"]  # Özet 'Kalan' tek tanım
    assert t["left_after_savings"] == round(t["net"] - 2000, 2)  # Sade görünüm 'Kalan' (önceden istemcide)
    assert t["card_spend_incl_pending"] == round(t["card_spend"] + t["card_pending"], 2)
    assert t["manual_expense_excl_card_pending"] == round(t["manual_expense"] - t["card_pending"], 2)
    assert t["fees_hidden"] is False
    d = client.get("/api/summary", params={"month": M}).json()["definitions"]
    assert "left" in d and "left_after_savings" in d


def test_transactions_totals_single_rule(client):
    _seed(client)
    rows = client.get("/api/transactions", params={"month": M}).json()
    assert isinstance(rows, list)  # parametresiz: eski biçim
    by = {r["description"]: r for r in rows}
    assert by["Market"]["counts_as_spend"] is True and by["Market"]["counts_as_income"] is False
    assert by["Altın alımı"]["counts_as_spend"] is False  # birikim gider değil
    assert by["Maaş"]["counts_as_income"] is True and by["Maaş"]["counts_as_spend"] is False

    r = client.get("/api/transactions", params={"month": M, "totals": 1}).json()
    assert {"rows", "totals"} <= set(r)
    t = r["totals"]
    s = client.get("/api/summary", params={"month": M}).json()["totals"]
    for k in ("income", "expense", "refunds", "savings", "net"):
        assert t[k] == s[k], k  # Özet ile aynı kural
    assert t["count"] == len(r["rows"])
    # istemci süzgeci: bayrağı taşıyan satırların toplamı sunucu toplamına eşit
    assert round(sum(x["amount"] for x in r["rows"] if x["counts_as_spend"]), 2) == t["expense"]


def test_refund_counts_negative_and_pending_not_counted(client, upload):
    upload()
    rows = client.get("/api/transactions").json()
    for x in rows:
        if x["kind"] == "iade" and not x.get("linked"):
            assert x["counts_as_spend"] is True and x["amount"] < 0  # iade gideri düşer
        if x.get("pending") or x.get("linked") or x.get("skipped") or x.get("deleted"):
            assert not x["counts_as_spend"] and not x["counts_as_income"]


def test_payables_days_window(client):
    today = date.today()
    soon = (today + timedelta(days=5)).isoformat()
    far = (today + timedelta(days=40)).isoformat()
    late = (today - timedelta(days=3)).isoformat()
    for p, a, d in (("Elektrik A", 500, soon), ("Su B", 300, far), ("Doğalgaz C", 200, late)):
        assert client.post("/api/bills", json={"provider": p, "amount": a, "due_date": d}).status_code == 200
    r = client.get("/api/payables", params={"days": 15}).json()
    w = r["totals"]["window"]
    assert w["days"] == 15 and w["total"] == 700 and w["count"] == 2  # gecikmiş + 15 gün içindeki; tahmin hariç
    assert r["totals"]["open"] == 1000
    assert "estimated" in w
    assert isinstance(client.get("/api/payables").json(), list)  # eski biçim


def test_networth_spendable_explained(client):
    nw = client.get("/api/net-worth").json()
    assert nw["spendable_explain"]
    parts = nw["spendable_parts"]
    assert round(sum(p["amount"] * (1 if p["sign"] == "+" else -1) for p in parts), 2) == nw["spendable"]


def test_meta_period_bounds(client):
    m = client.get("/api/meta").json()
    cur = m["current_month"]
    p = m["period"]
    assert p["month"] == cur and p["start"] <= date.today().isoformat() <= p["end"]


def test_transactions_fast_json_identical(client, upload):
    """S10: jsonable_encoder atlandı; parametresiz yanıt eskisiyle bayt bayt aynı."""
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    upload()
    _seed(client)
    r = client.get("/api/transactions")
    assert r.content == JSONResponse(jsonable_encoder(r.json())).body
    assert isinstance(r.json(), list)


def test_transactions_pagination(client, upload):
    upload()
    _seed(client)
    full = client.get("/api/transactions").json()
    got, cur, n = [], None, 0
    while True:
        p = client.get("/api/transactions", params={"limit": 7, **({"before": cur} if cur else {})}).json()
        got += p["rows"]
        cur, n = p["next"], n + 1
        if not cur:
            break
    assert [x["uid"] for x in got] == [x["uid"] for x in full] and n == -(-len(full) // 7)
    t = client.get("/api/transactions", params={"limit": 3, "totals": 1}).json()
    assert len(t["rows"]) == 3 and t["totals"]["count"] == len(full)  # toplamlar kümenin tamamı
    assert client.get("/api/transactions", params={"before": "dün|x"}).status_code == 422
    m = client.get("/api/transactions", params={"month": M, "limit": 2}).json()
    assert all(x["month"] == M for x in m["rows"])
