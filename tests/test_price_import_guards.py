"""Resmî fiyat içe aktarımında veri kalite denetimi: grup içi aykırı teklif, önceki güne göre satır düşüşü / fiyat
kayması (anomali: alınmaz, yöneticiye push). Ağ yok (sahte BigQuery, tests/test_price_import_bq.py); uydurma veri."""
from datetime import timedelta

import pytest

from app import price_import_bq as pib
from app import push, system
from tests.test_price_import_bq import DAY, TODAY, _after5, _rows, creds  # noqa: F401  (fixture)

PREV = (TODAY - timedelta(days=1)).isoformat()


def _row(chain, g, title, price):
    return (chain, g, title, None, price, None, DAY, "", 0, "İzmir", None, None, None, None)


def _prev(region="İzmir", rows=10, prices=None, day=PREV):
    with system.connect() as c:
        c.execute("INSERT INTO official_imports (run_date, region, rows, products, unmatched, imported_at) "
                  "VALUES (?, ?, ?, ?, 0, '2026-11-06T06:00:00+00:00')", (day, region, rows, rows))
        for i, (g, p) in enumerate((prices or {}).items()):
            c.execute("INSERT INTO official_prices (chain, group_key, title, price, day, region) VALUES "
                      "('BİM', ?, ?, ?, ?, ?)", (g, f"Örnek Ürün {i}", p, day, region))


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "ops_alert", lambda status, region, text, today=None: sent.append((status, region, text)) or 1)
    return sent


def test_drop_outliers():
    rows = [_row("BİM", "sut|1l", "A", 30.0), _row("A101", "sut|1l", "B", 32.0), _row("ŞOK", "sut|1l", "C", 200.0),
            _row("Migros", "sut|1l", "D", 9.0), _row("BİM", "ekmek", "E", 10.0), _row("A101", "ekmek", "F", 90.0)]
    keep, dropped = pib.drop_outliers(rows)
    assert [r[2] for r in keep] == ["A", "B", "E", "F"]                  # 2 teklifli grupta ortanca anlamsız: dokunulmaz
    assert dropped == {"sut|1l": {"median": 31.0, "dropped": 2}}


def test_outliers_counted_in_import_and_status(creds, monkeypatch):  # noqa: F811
    from tests import test_price_import_bq as t

    monkeypatch.setitem(t.OFFERS, "İzmir", t.OFFERS["İzmir"] + [
        ("migros", "p9", 350.0, 1, 350.0, "Örnekköy Süt 1 L Koli", "Örnekköy", "1 L", "Süt", False, None, None, None)])
    st = pib.import_region(DAY, "İzmir")
    assert st["outliers"] == 1 and st["rows"] == 3 and "anomaly" not in st
    s = pib.status()
    assert s["inconsistent_groups"] == {"day": DAY, "groups": [{"group_key": "sut|1l", "median": 60.45, "dropped": 1}]}
    assert s["outliers"] == 1


def test_row_drop_anomaly_keeps_previous_and_alerts(creds, alerts):  # noqa: F811
    _prev(rows=10, prices={"sut|1l": 35.0})
    st = pib.import_region(DAY, "İzmir")                                   # 3 satır < 10'un yarısı
    assert st["anomaly"].startswith("satır sayısı 10 → 3")
    assert _rows("İzmir") == [r for r in _rows("İzmir") if r["day"] == PREV] and _rows("İzmir")  # önceki gün duruyor
    assert alerts == [("anomaly", "İzmir", f"{DAY}: {st['anomaly']}. Veri alınmadı, önceki gün korunuyor.")]
    with system.connect() as c:
        r = c.execute("SELECT status, note, rows FROM official_imports WHERE run_date = ? AND region = 'İzmir'",
                      (DAY,)).fetchone()
    assert (r[0], r[2]) == ("anomaly", 0) and r[1] == st["anomaly"]
    s = pib.status()
    assert s["last_day"] == PREV and s["anomalies"][0]["region"] == "İzmir" and s["anomalies"][0]["day"] == DAY
    assert (DAY, "İzmir") not in pib.pending_runs(TODAY)                  # kendiliğinden yeniden çekilmez
    assert "anomaly" not in pib.import_region(DAY, "İzmir", force=True)   # elle: --force
    assert pib.status()["last_day"] == DAY and not pib.status()["anomalies"]


def test_run_reports_anomaly(creds, alerts):  # noqa: F811
    _prev(region="Ankara", rows=50)
    out = pib.run(_after5())
    assert [DAY, "Ankara", 0, "anomaly"] in out["imported"] and [DAY, "İzmir", 3] in out["imported"]
    assert [a[1] for a in alerts] == ["Ankara"]


def test_price_shift_anomaly(client):
    groups = [f"urun{i}|1l" for i in range(6)]
    _prev(rows=6, prices={g: 10.0 for g in groups})
    with system.connect() as c:
        why = pib.anomaly(c, DAY, "İzmir", [_row("BİM", g, g, 14.0) for g in groups])
        assert why.startswith("fiyat ortancası %+40 (6 grup")
        assert pib.anomaly(c, DAY, "İzmir", [_row("BİM", g, g, 12.0) for g in groups]) is None   # %20
        assert pib.anomaly(c, DAY, "İzmir", [_row("BİM", g, g, 20.0) for g in groups[:4]] +
                           [_row("BİM", "yeni|1l", "x", 1.0)] * 2) is None                        # 4 ortak grup: az
        assert pib.anomaly(c, DAY, "Ankara", [_row("BİM", g, g, 99.0) for g in groups]) is None   # ilk gün


def test_price_shift_falls_back_to_daily_summary(client):
    groups = [f"urun{i}|1l" for i in range(5)]
    _prev(rows=5)
    with system.connect() as c:
        for g in groups:
            c.execute("INSERT INTO official_daily (group_key, region, day, min, median, max, cheapest_chain, chains) "
                      "VALUES (?, 'İzmir', ?, 5, 10, 15, 'BİM', 2)", (g, PREV))
        assert pib.anomaly(c, DAY, "İzmir", [_row("BİM", g, g, 5.0) for g in groups]).startswith("fiyat ortancası %-50")


def test_ops_alert_helper(monkeypatch):
    from app import feedback

    got = []
    monkeypatch.setattr(feedback, "admins", lambda: [{"id": 7}, {"id": 8}])
    monkeypatch.setattr(push, "notify", lambda uid, items, *a, **k: got.append((uid, items[0])) or items)
    assert push.ops_alert("anomaly", "İzmir", "örnek", today=TODAY) == 2
    assert got[0][1]["id"] == f"sistem:{DAY}:anomaly:İzmir" and got[0][1]["title"] == "Fiyat toplama: anomaly · İzmir"
