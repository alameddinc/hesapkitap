"""Para: tek yuvarlama kuralı (yarım yukarı), kuruş eşitliği ve yazarken yuvarlama (denetim M3, Ek A F1.1/F1.2).
Tutarlar uydurmadır."""
import re
import sqlite3
from pathlib import Path

import pytest

from app import money

ROOT = Path(__file__).parent.parent


def test_one_cent_differences_always_detected():
    """Deney 2: 0,01–1000 TL arası b = a + 0,01 çiftlerinin hepsi farklı, hiçbiri eşit değil (float ile %81 kaçıyordu)."""
    missed = 0
    for k in range(1, 100_000):
        a = k / 100
        b = a + 0.01
        if money.eq(a, b) or money.diff_k(b, a) != 1 or not money.ne(a, b):
            missed += 1
    assert missed == 0


def test_float_noise_is_equal():
    assert money.eq(0.1 + 0.2, 0.3)
    assert money.eq(1234.57 - 1234.56, 0.01)
    assert money.is_zero(0.1 + 0.2 - 0.3)


@pytest.mark.parametrize("x, want", [(0.125, 0.13), (2.675, 2.68), (1.005, 1.01), (-0.125, -0.13), (1234.565, 1234.57),
                                     (10, 10.0), (0.0049, 0.0), (None, None)])
def test_half_up(x, want):
    assert money.money(x) == want


def test_comparisons():
    assert money.lt(9.99, 10) and money.le(10, 10.001) and money.ge(10.004, 10) and money.gt(10.01, 10)
    assert money.within(100.00, 100.01, 1) and not money.within(100.00, 100.02, 1)
    assert money.kurus(1234.56) == 123456 and money.kurus(-0.005) == -1


def test_three_times_eighth_rows_match_summary(client):
    """Deney 5: 3 × 0,125 TL → satırlar 0,13 + 0,13 + 0,13 = özet 0,39 (önce satırlar 0,36, özet 0,38 idi)."""
    for i in range(3):
        r = client.post("/api/entries", json={"entry_date": "2026-10-05", "description": f"Sakız {i}", "amount": 0.125,
                                              "kind": "gider", "category": "Market & Gıda", "payment": "nakit"})
        assert r.status_code == 200
    rows = [r for r in client.get("/api/transactions", params={"month": "2026-10"}).json() if r["description"].startswith("Sakız")]
    assert [r["amount"] for r in rows] == [0.13, 0.13, 0.13]
    tot = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]
    assert money.eq(sum(r["amount"] for r in rows), tot["expense"])


def test_sub_cent_amounts_rounded_on_write(client, tmpdb):
    eid = client.post("/api/entries", json={"entry_date": "2026-10-05", "description": "Kahve", "amount": 12.345,
                                            "kind": "gider", "category": "Kafe", "payment": "nakit"}).json()["id"]
    client.patch(f"/api/entries/{eid}", json={"amount": 7.005})
    with tmpdb.connect() as c:
        assert c.execute("SELECT amount FROM entries WHERE id = ?", (eid,)).fetchone()[0] == 7.01
    client.post("/api/accounts", json={"name": "Ziraat", "kind": "banka", "balance": 1000.555})
    with tmpdb.connect() as c:
        assert c.execute("SELECT balance FROM accounts WHERE name = 'Ziraat'").fetchone()[0] == 1000.56


def test_db_round_tool(tmp_path):
    """Eski kayıtlar: python -m app.money fix; sonra ABS(x*100 - ROUND(x*100)) > 1e-6 sorgusu 0 satır."""
    p = tmp_path / "eski.db"
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, amount REAL)")
        c.execute("CREATE TABLE debts (id INTEGER PRIMARY KEY, amount REAL, paid_amount REAL, asset TEXT)")
        c.executemany("INSERT INTO entries (amount) VALUES (?)", [(0.125,), (2.675,), (10.0,), (99.999,)])
        c.executemany("INSERT INTO debts (amount, paid_amount, asset) VALUES (?, ?, ?)",
                      [(100.005, 0, "TRY"), (12.345, 0, "ALTIN_GRAM")])  # gram miktarı yuvarlanmaz
    assert money._main(["check", str(p)]) == 0
    with sqlite3.connect(p) as c:
        assert money.off_cents(c) == {"entries.amount": 3, "debts.amount": 1}
    assert money._main(["fix", str(p)]) == 0
    with sqlite3.connect(p) as c:
        assert [r[0] for r in c.execute("SELECT amount FROM entries ORDER BY id")] == [0.13, 2.68, 10.0, 100.0]
        assert [r[0] for r in c.execute("SELECT amount FROM debts ORDER BY id")] == [100.01, 12.345]
        assert c.execute("SELECT COUNT(*) FROM entries WHERE ABS(amount * 100 - ROUND(amount * 100)) > 1e-6").fetchone()[0] == 0
    assert list(tmp_path.glob("eski.db.yuvarlama-oncesi-*"))  # önce yedek


# Para olmayan ya da bilinçli 0.01 kullanımları (oran, yüzde): yeni bir tutar eşiği buraya değil money.py'ye gider
ALLOWED = [
    ("app/money.py", ""),
    ("app/payroll.py", '"unemployment": 0.01'),
    ("app/finance.py", "b * 0.01"),             # tutarın %1'i (en az 1 TL) sapma eşiği
]


def test_no_raw_money_thresholds_left():
    """Kabul ölçütü: grep -nE "0\\.01|< ?0\\.005" yalnız money.py ve izin listesini göstermeli."""
    rx = re.compile(r"0\.01|< ?0\.005|0\.005")
    bad = []
    for f in sorted((ROOT / "app").rglob("*.py")):
        rel = f.relative_to(ROOT).as_posix()
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if rx.search(code) and not any(rel == a and s in line for a, s in ALLOWED):
                bad.append(f"{rel}:{n}: {line.strip()}")
    assert not bad, "\n".join(bad)


def test_tl_single_format():
    """S4: sunucu metinlerinde istemcilerle aynı biçim ₺1.234,50 (tek yardımcı)."""
    from app.money import tl

    assert tl(126.46) == "₺126,46" and tl(1234.5) == "₺1.234,50" and tl(-1000) == "−₺1.000,00"
    assert tl(1234, trim=True) == "₺1.234" and tl(1234.5, trim=True) == "₺1.234,50"
    assert tl(47012.6, decimals=0) == "₺47.013" and tl(0.005) == "₺0,01"


def test_no_suffix_currency_in_server_texts():
    """'126,46 ₺' / '… TL' biçimi sunucu metinlerinde kalmadı (ayrıştırıcılar ve yapay zekâ istemleri hariç)."""
    rx = re.compile(r"""\}\s?(₺|TL)\b|:,\.2f\}\s?(₺|TL)""")
    skip = ("app/parsers/", "app/receipt.py", "app/replay_check.py", "app/share.py")
    bad = []
    for f in sorted((ROOT / "app").rglob("*.py")):
        rel = f.relative_to(ROOT).as_posix()
        if rel.startswith(skip):
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if rx.search(line.split("#", 1)[0]):
                bad.append(f"{rel}:{n}: {line.strip()}")
    assert not bad, "\n".join(bad)
