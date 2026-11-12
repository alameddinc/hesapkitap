"""C7: rakam eşitliği — aynı rakam web oturumunda ve mobil oturumunda aynı gelir (parity/figures.json).

Her anahtar için web istemcisinin (çerez) ve mobil istemcinin (Bearer + X-HK-Client: mobile) kendi kodundaki yolu
istenir; yanıttaki alan iki tarafta da sayı ve birebir eşit olmalı. Ayrıca aynı rakamı veren farklı uçların
birbirini tuttuğu (kart borcu üç uçta, harcanabilir = parçaları, işlemler toplamı = Özet) denetlenir.
Tutarlar uydurmadır; ekstreler tests/fixtures altındaki sahte metinlerdir.
"""
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import STATEMENTS, fixture_bytes

FIGURES = json.loads((Path(__file__).resolve().parent.parent / "parity" / "figures.json").read_text(encoding="utf-8"))
KEYS = sorted(k for k in FIGURES if not k.startswith("_"))
MOBILE = {"X-HK-Client": "mobile", "Origin": "capacitor://localhost"}


def _get(d, path: str):
    for part in path.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


@pytest.fixture()
def clients(client, monkeypatch):
    """(web, mobil): aynı kullanıcı, iki oturum türü; defterde ekstre, elle kayıt, kumbara, borç, fatura, bütçe."""
    from app import auth
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    web = TestClient(app)
    assert web.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    r = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}, headers=MOBILE)
    mobile = TestClient(app, headers={**MOBILE, "Authorization": f"Bearer {r.headers['x-session-token']}"})

    files = [("files", (n.replace(".txt", ".pdf"), fixture_bytes(n), "application/pdf")) for n in STATEMENTS]
    assert web.post("/api/statements/upload", files=files).status_code == 200
    acc = web.post("/api/accounts", json={"name": "Örnek Banka", "kind": "banka", "balance": 50000}).json()
    for d, desc, amt, kind, cat in (("2026-10-01", "Maaş", 40000, "gelir", "Maaş"),
                                    ("2026-10-05", "Kira", 12000, "gider", "Kira & Aidat"),
                                    ("2026-10-09", "Pazar", 640.4, "gider", "Market & Gıda"),
                                    ("2026-10-11", "Altın alımı", 2000, "gider", "Altın & Döviz Birikimi")):
        assert web.post("/api/entries", json={"entry_date": d, "description": desc, "amount": amt, "kind": kind,
                                              "category": cat}).status_code == 200
    web.post("/api/jars", json={"name": "Tatil", "goal": 30000, "initial": 4500, "account_id": acc.get("id")})
    web.post("/api/debts", json={"person": "Örnek Kişi", "direction": "alacak", "amount": 1500, "debt_date": "2026-10-03"})
    bill = web.post("/api/bills", json={"provider": "Örnek Elektrik", "amount": 812.35, "due_date": "2026-10-20"}).json()
    assert web.post(f"/api/bills/{bill['id']}/pay", json={"amount": 812.35, "paid_at": "2026-10-18"}).status_code == 200
    web.put("/api/budgets", json={"category": "Market & Gıda", "monthly_limit": 4000})
    return web, mobile


def _month(c) -> str:
    """En dolu dönem (ay dönümü ekstrelerden kendiliğinden ayarlanır; sabit "2026-10" yazmak kırılgan olur)."""
    months = c.get("/api/meta").json()["months"]
    return max(months, key=lambda m: c.get(f"/api/summary?month={m}").json()["totals"]["expense"])


def _url(tpl: str, month: str) -> str:
    return tpl.format(month=month, until=date.today().isoformat()[:7])


def test_registry_is_well_formed():
    assert len(KEYS) >= 20
    for k in KEYS:
        f = FIGURES[k]
        assert f["web"].startswith("/api/") and f["mobile"].startswith("/api/"), k
        assert f.get("path") or (f.get("path_web") and f.get("path_mobile")), k
        assert f["web_src"].startswith("web/src/") and f["mobile_src"].startswith("src/"), k
        for side in ("web_src",):  # web dosyası bu depoda; mobil ayrı depoda (yerel kontrol: rapor)
            assert (Path(__file__).resolve().parent.parent / f[side]).exists(), f"{k}: {f[side]} yok"


def test_same_figure_on_web_and_mobile(clients):
    web, mobile = clients
    month = _month(web)
    assert month == _month(mobile)
    cache: dict[tuple[str, str], dict] = {}

    def fetch(c, name, url):
        if (name, url) not in cache:
            r = c.get(url)
            assert r.status_code == 200, f"{name} {url}: {r.status_code} {r.text[:200]}"
            cache[(name, url)] = r.json()
        return cache[(name, url)]

    nonzero = 0
    for k in KEYS:
        f = FIGURES[k]
        w = _get(fetch(web, "web", _url(f["web"], month)), f.get("path_web") or f["path"])
        m = _get(fetch(mobile, "mobile", _url(f["mobile"], month)), f.get("path_mobile") or f["path"])
        assert isinstance(w, (int, float)) and not isinstance(w, bool), f"{k}: web {w!r}"
        assert isinstance(m, (int, float)) and not isinstance(m, bool), f"{k}: mobil {m!r}"
        assert w == m, f"{k}: web {w} ≠ mobil {m}"
        nonzero += w != 0
    assert nonzero >= len(KEYS) // 2, "tohum verisi rakamların çoğunu doldurmalı (sıfır = sıfır eşitliği anlamsız)"


def test_cross_endpoint_invariants(clients):
    web, _ = clients
    month = _month(web)
    s = web.get(f"/api/summary?month={month}").json()["totals"]
    tx = web.get(f"/api/transactions?month={month}&totals=1").json()
    nw = web.get("/api/net-worth").json()
    pay = web.get("/api/payables?totals=1&days=15").json()["totals"]
    cards = web.get("/api/cards/summary?recent=20").json()["totals"]

    for k in ("income", "expense", "refunds", "savings", "net", "left", "left_after_savings"):
        assert tx["totals"][k] == s[k], f"işlemler ≠ Özet: {k}"
    assert round(sum(r["amount"] for r in tx["rows"] if r["counts_as_spend"]), 2) == s["expense"]
    # kart borcu: Net durum, Ödemeler ve Kartlar aynı tek tanım
    assert nw["card_debt"] == pay["card_debt"] == cards["unpaid"]
    # harcanabilir = parçaların işaretli toplamı (istemci yalnız gösterir)
    parts = nw["spendable_parts"]
    assert round(sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in parts), 2) == nw["spendable"]
    for k in ("card_debt", "cash", "spendable", "net", "net_display"):
        assert nw["totals"][k] == nw[k], k
    # gösterilen Net durum = eski net − kesilecek kart ekstresi (tahmini); eski ad net_after_estimates aynı rakam
    assert nw["net_display"] == round(nw["net"] - nw["card_estimated"], 2) == nw["net_after_estimates"]
    assert nw["reserved"] > 0 and nw["receivable"] == s["receivable"]
