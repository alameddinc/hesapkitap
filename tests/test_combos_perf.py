"""Zincir × marka seçicisinin hızı (shop_combo_picker): canlıdaki boyda (~230 bin resmî fiyat satırı) uydurma veriyle
GET /api/shopping-list/{id}/combos ve /api/prices/combos kaba üst sınırın altında kalmalı. Ürün, marka ve fiyatlar
uydurmadır."""
import time
from datetime import date, timedelta

from app import system
from tests.test_shopping_v4 import _add, resmi  # noqa: F401  (fixture)

CHAINS = ["BİM", "A101", "ŞOK", "Migros", "CarrefourSA", "Hakmar", "Tarım Kredi"]
REGIONS = ["İstanbul", "Ankara", "İzmir"]
SIZES = ["1l", "500ml", "200ml", "2l", "1kg"]


def _fixture_rows():
    """~230 bin satır: 500 ürün × 5 boy × 7 zincir × 3 il × 2 ürün adı × 2 gün; süt 1 L / 500 ml'de 30 marka, 12 il,
    7 gün (canlıda en kalabalık gruplar binlerce satır)."""
    rows = []
    for b in range(500):
        base = "sut" if b == 0 else f"urun{b}"
        for s in SIZES:
            heavy = base == "sut" and s in ("1l", "500ml")
            brands = 30 if heavy else 2
            days = [(date.today() - timedelta(days=d)).isoformat() for d in range(7 if heavy else 2)]
            for ch in CHAINS:
                for rg in ([f"{r}{i or ''}" for r in REGIONS for i in range(4)] if heavy else REGIONS):
                    for m in range(brands):
                        for d in days:
                            rows.append((ch, f"{base}|{s}", f"Örnekmarka{m} {base} {s}", f"Örnekmarka{m}",
                                         20.0 + (b % 50) + m + len(ch) * 0.25, d, rg))
    return rows


def _seed():
    rows = _fixture_rows()
    with system.connect() as c:
        c.executemany("INSERT INTO official_prices (chain, group_key, title, brand, price, day, region) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _receipts(n_entries=400, per=12):
    """Kalabalık bir defter: 400 fiş × 12 kalem (uydurma adlar; her onda biri markalı)."""
    from app import db

    names = ["ÖRNEKSÜT SÜT 1 L", "ÖRNEK EKMEK 350 G", "ÖRNEK DOMATES KG", "ÖRNEK PEYNİR 500 G", "ÖRNEK YUMURTA 10 LU",
             "ÖRNEK MAKARNA 500 G", "ÖRNEK DETERJAN 4 KG", "ÖRNEK KOLA 1 L", "ÖRNEK ÇAY 1 KG", "ÖRNEK ZEYTİN 1 KG"]
    with db.connect() as c:
        for e in range(n_entries):
            d = (date.today() - timedelta(days=e % 300)).isoformat()
            eid = c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, payment) "
                            "VALUES (?, ?, 'BİM', 100, 'gider', 'Market & Gıda', 'nakit')", (d, d[:7])).lastrowid
            for i in range(per):
                nm = f"{names[i % 10]} {e % 40}"
                c.execute("INSERT INTO receipt_items (entry_id, purchased_at, merchant, name, product_key, qty, amount, "
                          "unit_price, brand) VALUES (?, ?, 'BİM', ?, ?, 1, 10, 10, ?)",
                          (eid, d, nm, nm, "Örneksüt" if i % 10 == 0 else None))


def _timed(fn):
    t = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t


def test_combo_picker_fast_on_large_data(client, resmi):  # noqa: F811
    n = _seed()
    assert n > 200_000
    _receipts()
    milk = _add(client, "Süt")
    r, cold = _timed(lambda: client.get(f"/api/shopping-list/{milk}/combos"))
    assert r.status_code == 200 and r.json()["rows"]
    r, warm = _timed(lambda: client.get(f"/api/shopping-list/{milk}/combos"))
    p, warm_p = _timed(lambda: client.get("/api/prices/combos", params={"group": "sut"}))
    assert p.status_code == 200 and p.json()["brands"]
    s, sizes = _timed(lambda: client.get(f"/api/shopping-list/{milk}/sizes"))
    assert s.status_code == 200 and s.json()
    # kaba üst sınır (CI makinesi yavaş olabilir): canlıda hedef sıcak < 300 ms, soğuk < 1,5 sn
    assert cold < 3.0, cold
    assert warm < 1.0 and warm_p < 1.0 and sizes < 1.0, (warm, warm_p, sizes)
    print(f"combos soğuk {cold * 1000:.0f} ms, sıcak {warm * 1000:.0f} ms, prices/combos {warm_p * 1000:.0f} ms, "
          f"sizes {sizes * 1000:.0f} ms")

