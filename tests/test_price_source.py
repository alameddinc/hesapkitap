"""Fiyat kaynağı (C5): kaynak çökünce istekler beklemez — kısa zaman aşımı, negatif önbellek, tek uçuş, arka plan yenileme."""
import json
import threading
import time
from datetime import date

import httpx
import pytest

from app import system, zakat

RAW = {"Update_Date": "2026-11-01 10:00", "GRA": {"Buying": "6.400,50"}, "USD": {"Buying": "50,10"}}


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return RAW


@pytest.fixture()
def source(monkeypatch):
    """Sahte kaynak: calls sayılır; down=True iken `delay` bekleyip bağlantı hatası verir."""
    state = {"calls": 0, "down": False, "delay": 0.0}
    lock = threading.Lock()

    def get(url, **kw):
        with lock:
            state["calls"] += 1
        time.sleep(state["delay"])
        if state["down"]:
            raise httpx.ConnectTimeout("bağlantı zaman aşımı")
        return _Resp()

    monkeypatch.setattr(zakat.httpx, "get", get)
    return state


def _seed(age: float, gram: float = 6000.0):
    system.put("market_prices", json.dumps({"prices": {"GRA": gram}, "updated": "eski", "fetched": time.time() - age,
                                            "source": "truncgil"}))


def _wait_bg():
    for t in threading.enumerate():
        if t.name == "fiyat-yenile":
            t.join(5)


def test_short_timeout():
    assert zakat.PRICE_TIMEOUT.connect <= 2 and zakat.PRICE_TIMEOUT.read <= 4


def test_fresh_cache_no_request(source):
    _seed(age=10)
    assert zakat.fetch_prices()["prices"] == {"GRA": 6000.0} and source["calls"] == 0


def test_stale_cache_returns_at_once_and_refreshes_in_background(source):
    _seed(age=zakat.PRICE_TTL + 5)
    source["delay"] = 0.5
    t = time.perf_counter()
    got = [zakat.fetch_prices() for _ in range(5)]
    assert time.perf_counter() - t < 0.2                       # kimse kaynağı beklemedi
    assert all(g["prices"] == {"GRA": 6000.0} for g in got)    # bayat ama bilinen fiyat
    _wait_bg()
    assert source["calls"] == 1                                # tek uçuş: 5 istek, dışarıya 1
    assert zakat.fetch_prices()["prices"]["GRA"] == 6400.5     # yenilenmiş fiyat


def test_source_down_negative_cache(source):
    source["down"] = True
    first = zakat.fetch_prices()                               # önbellek yok: bir kez dener
    assert first["prices"] == {} and source["calls"] == 1
    source["delay"] = 5                                        # yeniden denense test takılırdı
    t = time.perf_counter()
    for _ in range(10):
        assert zakat.fetch_prices()["prices"] == {}
    assert time.perf_counter() - t < 0.2 and source["calls"] == 1
    # süre dolunca yeniden denenir
    source.update(down=False, delay=0)
    zakat._price_fail_until = time.time() - 1
    assert zakat.fetch_prices()["prices"]["GRA"] == 6400.5 and source["calls"] == 2


def test_source_down_keeps_last_known_price(source):
    _seed(age=zakat.PRICE_TTL + 5)
    source["down"] = True
    assert zakat.fetch_prices()["prices"] == {"GRA": 6000.0}
    _wait_bg()
    assert source["calls"] == 1 and zakat._price_fail_until > time.time()
    assert zakat.fetch_prices()["prices"] == {"GRA": 6000.0} and source["calls"] == 1


def test_force_ignores_negative_cache(source):
    source["down"] = True
    zakat.fetch_prices()
    source["down"] = False
    assert zakat.fetch_prices(force=True)["prices"]["GRA"] == 6400.5 and source["calls"] == 2


def test_single_flight_without_cache(source):
    source["delay"] = 0.3
    out = []
    ts = [threading.Thread(target=lambda: out.append(zakat.fetch_prices())) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert source["calls"] == 1 and len(out) == 6 and all(o["prices"]["GRA"] == 6400.5 for o in out)


def test_summary_fast_when_source_down(client, source):
    # birimli kumbara Özet'te bugünkü fiyatı istiyor: kaynak çökükken Özet dış ağı beklemesin
    client.post("/api/jars", json={"name": "Altın", "unit": "ALTIN_24", "monthly": 1, "auto_day": 1})
    _seed(age=zakat.PRICE_TTL + 5)
    source.update(down=True, delay=3)
    t = time.perf_counter()
    assert client.get("/api/summary").status_code == 200
    assert time.perf_counter() - t < 1
    t = time.perf_counter()
    assert client.get("/api/summary").status_code == 200
    assert time.perf_counter() - t < 1
    assert source["calls"] <= 1


def test_historical_negative_cache(source):
    source["down"] = True
    day = date(2026, 5, 4)
    with pytest.raises(httpx.HTTPError):
        zakat.historical_prices(day)
    n = source["calls"]
    assert n == 1                                              # ilk sembolde düştü, gerisi denenmedi
    with pytest.raises(ValueError):
        zakat.historical_prices(day)                           # aynı gün: hatırlanan hata
    with pytest.raises(ValueError):
        zakat.historical_prices(date(2025, 5, 4))              # başka gün: kaynak düştü bayrağı
    with pytest.raises(ValueError):
        zakat.historical_gram_price(day)
    assert source["calls"] == n
