"""Canlı demo fiyat verisi (app/demo_prices.py): BigQuery olmadan Fiyat takibi, öneri, Nereden alınır, alışveriş listesi
planı ve geçmiş alışverişin tasarrufu dolu görünür. Fiyatlar uydurmadır; kaynak yazısı bunu söyler."""
import os
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import demo_mode, demo_prices, price_import


@pytest.fixture()
def demo(tmp_path, monkeypatch, tmpdb):
    monkeypatch.setenv("HESAPKITAP_DEMO", "1")
    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.delenv("HESAPKITAP_PRICE_SOURCES", raising=False)
    from app.api.main import app

    return TestClient(app)


def test_every_demo_product_resolves_by_rules():
    keys = {t: demo_prices.key_of(t) for t, *_ in demo_prices.PRODUCTS}
    assert len(set(keys.values())) == len(demo_prices.PRODUCTS) >= 30
    assert keys["SÜT 1 L"] == "sut|1l" and keys["DOMATES"] == "domates|kg" and keys["KOLA 2,5 L"] == "kola|2.5l"
    for _d, _ch, lines, _b in demo_prices.RECEIPTS:
        assert all(t in keys for t, _q in lines)


def test_template_is_fast_and_versioned(demo):
    base = demo_mode._base()
    os.makedirs(base, exist_ok=True)
    old = os.path.join(base, f"template-{demo_mode.days_since_anchor()}.db")  # sürümsüz eski şablon
    open(old, "w").close()
    t = time.monotonic()
    path = demo_mode.template()
    assert time.monotonic() - t < 5  # (CI yavaş olabilir; yerelde ~0,3 sn)
    assert os.path.basename(path).startswith(f"template-v{demo_mode.TEMPLATE_VERSION}-") and not os.path.exists(old)
    assert demo_prices.seed_system() is False  # bugün zaten yazıldı


def test_demo_prices_advice_and_market(demo):
    out = demo.get("/api/prices").json()
    sut = next(p for p in out["products"] if p["group"] and p["group"]["key"] == "sut|1l")
    assert sut["count"] >= 3 and len({h["store"] for h in sut["history"]}) >= 2
    assert sut["market"] and sut["market_ref"]["median"] > 0
    adv = sut["advice"]
    assert adv["kind"] == "cheaper_at" and adv["chain"] in demo_prices.CHAINS and adv["chain"] != "Market D"
    assert adv["saving"] > 0
    assert any(h.get("vs_market") for h in sut["history"])
    assert any(p["advice"] and p["advice"]["kind"] == "good_price" for p in out["products"])
    mk = demo.get("/api/prices/market", params={"group": "sut|1l", "days": 90}).json()
    assert len(mk["days"]) >= 80 and mk["days"][-1]["day"] == date.today().isoformat()
    assert mk["attribution"] == price_import.DEMO_ATTRIBUTION  # uydurma veri gerçek kaynak adıyla gösterilmez


def test_demo_where_to_buy(demo):
    got = demo.get("/api/prices/groups", params={"q": "süt"}).json()
    sut = next(g for g in got if g["key"] == "sut|1l")
    chains = {o["chain"] for o in sut["best"]}
    assert len(chains) == 5 and {o["source"] for o in sut["best"]} >= {"community", "own"}
    assert any(o.get("n", 0) >= 3 for o in sut["best"])
    kahve = demo.get("/api/prices/groups", params={"q": "kahve"}).json()[0]
    assert kahve["best"][0]["source"] == "resmi" and kahve["best"][0]["attribution"] == price_import.DEMO_ATTRIBUTION
    dom = demo.get("/api/prices/groups", params={"q": "domates"}).json()[0]
    assert dom["key"] == "domates|kg" and dom["label"] == "Domates · kg"


def test_demo_shopping_list_plan_and_trip_savings(demo):
    s = demo.get("/api/shopping-list").json()
    labels = {i["amount_label"] for i in s["items"]}
    assert {"2,5 kg", "6 adet"} <= labels
    assert all(i["best"] for i in s["items"]) and all(i["planned"] for i in s["items"])
    sp = s["saved_plan"]
    assert sp and sp["k"] >= 2 and len(sp["chains"]) == sp["k"] and sp["total"] > 0
    opt = demo.get("/api/shopping-list/optimize").json()
    assert opt["recommended_k"] == sp["k"] and not opt["unpriced"] and opt["headline"]["saving"] > 0
    assert "resmi" in opt["source_info"]["kinds"]
    trips = demo.get("/api/shopping-trips").json()["trips"]
    assert len(trips) == 1 and trips[0]["linked"] and trips[0]["saving"] > 0 and len(trips[0]["chains"]) == 1
    assert trips[0]["chains"][0] in demo_prices.CHAINS
    assert trips[0]["day"][:7] == date.today().isoformat()[:7]
    sv = demo.get("/api/shopping-trips/savings").json()
    assert sv["month"] == trips[0]["saving"] and sv["month_trips"] == 1
    t = demo.get(f"/api/shopping-trips/{trips[0]['id']}").json()
    assert all(i["actual_line"] and i["market_line"] for i in t["items"])


def test_demo_receipt_market_note(demo):
    ents = [e for e in demo.get("/api/transactions").json() if e.get("description") == "Market D"]
    last = max(ents, key=lambda e: e["date"])
    notes = demo.get(f"/api/entries/{last['id']}/receipt-items").json()["check"]["market_notes"]
    assert any(n["group"] == "yumurta|30ad" and n["pct"] >= 10 for n in notes)


def test_demo_user_is_premium_on_hosted(demo, monkeypatch):
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    assert demo.get("/api/prices").status_code == 200
    assert demo.get("/api/shopping-list").status_code == 200
    from app import plans

    assert plans.plan_of({"id": 0, "demo": True}) == plans.PREMIUM
