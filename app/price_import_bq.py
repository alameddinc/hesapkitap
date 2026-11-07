"""Resmî fiyatlar: BigQuery (başka ekibin Market Fiyatı toplayıcısı) → official_prices (system.db), gecelik.

Kaynak (salt okuma; app/bq.py): `offers` (run_date ile bölümlü; bölge kipinde satır = o ildeki ürünün en ucuz 1–2 teklifi)
ve `run_log` (run_date, region, status success|failed|blocked …).
Akış (tick, zamanlayıcıdan her dakika; ağır iş TRY_EVERY_S'de bir):
1. UTC 05:00'ten sonra run_log'da bugün / dün status = success olan ve official_imports'ta olmayan (gün, bölge)lar.
2. Her biri için (turda en çok MAX_REGIONS_PER_TICK): (market, product_id) başına en düşük fiyat, o teklifin birim fiyatı,
   adı, markası, boyu, ana kategorisi, indirim / kampanya yazısı, şube sayısı; aynı tablodan 7 ve 30 gün önceki en düşük
   fiyat → change_7d / change_30d (yüzde). Sorgu yalnız bu üç run_date bölümünü okur (maliyet ≈ 0).
3. Zincir adı price_import.CHAIN_MAP ile, ad + marka + boy ürün grubuna (app/product_groups.py; çözülemeyen ad yapay zekâ
   kuyruğuna girer, sonraki içe aktarımda gruplanır). O (gün, bölge)nın satırları tek işlemde değiştirilir (yeniden
   çalışırsa aynı sonuç), official_imports'a yazılır; KEEP_DAYS'ten eskiler silinir.
Yarıda kalırsa (hata, yeniden başlatma) yazılmayan bölge sonraki turda yeniden denenir. Birden çok işçi aynı anda
çalıştırmasın diye tur hakkı sys_settings'te kilitle alınır. Son hata ve son tur özeti yönetici paneline (status()).
Elle: python -m app.price_import_bq [YYYY-MM-DD bölge]
"""
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone

from app import bq

log = logging.getLogger("hesapkitap.price_import_bq")

START_HOUR_UTC = 5
TRY_EVERY_S = 30 * 60        # yeni bölge var mı: yarım saatte bir
RETRY_SOON_S = 5 * 60        # sınır yüzünden kalan bölge varsa
MAX_REGIONS_PER_TICK = 6
MAX_ROWS_PER_REGION = 60_000
LOOKBACK_DAYS = 1            # bugün + dün
LEASE_KEY, STATUS_KEY = "bq:next_try", "bq:status"


def enabled() -> bool:
    return bq.configured()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def offers_sql() -> str:
    t = bq.table("offers")
    return f"""
WITH cur AS (
  SELECT market, product_id, MIN(price) AS price, COUNT(DISTINCT depot_id) AS depots,
    ARRAY_AGG(STRUCT(unit_price_value, title, brand, volume_weight, main_category, discount, promotion_text)
              ORDER BY price LIMIT 1)[OFFSET(0)] AS top
  FROM {t}
  WHERE run_date = @d AND region = @region AND price > 0
  GROUP BY market, product_id
), past AS (
  SELECT run_date, market, product_id, MIN(price) AS price
  FROM {t}
  WHERE run_date IN (@d7, @d30) AND region = @region AND price > 0
  GROUP BY run_date, market, product_id
)
SELECT c.market, c.product_id, c.price, c.depots, c.top.unit_price_value AS unit_price, c.top.title AS title,
  c.top.brand AS brand, c.top.volume_weight AS volume_weight, c.top.main_category AS main_category,
  c.top.discount AS discount, c.top.promotion_text AS promotion_text,
  ROUND(100 * SAFE_DIVIDE(c.price - p7.price, p7.price), 1) AS change_7d,
  ROUND(100 * SAFE_DIVIDE(c.price - p30.price, p30.price), 1) AS change_30d
FROM cur c
LEFT JOIN past p7 ON p7.run_date = @d7 AND p7.market = c.market AND p7.product_id = c.product_id
LEFT JOIN past p30 ON p30.run_date = @d30 AND p30.market = c.market AND p30.product_id = c.product_id
ORDER BY c.market, c.product_id
LIMIT {MAX_ROWS_PER_REGION}"""


def runs_sql() -> str:
    return (f"SELECT CAST(run_date AS STRING) AS run_date, region FROM {bq.table('run_log')} "
            "WHERE run_date BETWEEN @d0 AND @d1 AND status = 'success' GROUP BY run_date, region")


def imported() -> set[tuple[str, str]]:
    from app import system

    with system.connect() as c:
        return {(r[0], r[1]) for r in c.execute("SELECT run_date, region FROM official_imports")}


def pending_runs(today: date) -> list[tuple[str, str]]:
    """BigQuery'de başarıyla biten, burada henüz alınmamış (gün, bölge)lar: yeni gün önce."""
    got = bq.query(runs_sql(), {"d0": today - timedelta(days=LOOKBACK_DAYS), "d1": today})
    done = imported()
    runs = {(r["run_date"], r["region"]) for r in got if r.get("run_date") and r.get("region")}
    return sorted(runs - done, key=lambda x: (x[0], x[1]), reverse=True)


def rows_for(bq_rows: list[dict], day: str, region: str) -> tuple[list[tuple], dict]:
    """BigQuery satırları → official_prices satırları ve özet {products, matched, rows, skipped_chain, unmatched}."""
    from app import price_import

    stats = {"products": len(bq_rows), "matched": 0, "rows": 0, "skipped_chain": 0, "unmatched": 0}
    keep = []
    for r in bq_rows:
        chain = price_import.CHAIN_MAP.get(str(r.get("market") or "").strip().lower())
        if not chain:
            stats["skipped_chain"] += 1
        elif r.get("title") and (r.get("price") or 0) > 0:
            keep.append((chain, r))
    names = [{"name": price_import.full_title(r["title"], r.get("volume_weight")), "brand": r.get("brand") or None}
             for _, r in keep]
    groups = price_import.group_names(names)
    best: dict[tuple[str, str], tuple] = {}
    for (chain, r), n, g in zip(keep, names, groups):
        if not g or g.get("pending"):
            stats["unmatched"] += 1
            continue
        stats["matched"] += 1
        row = (chain, g["key"], n["name"], r.get("brand") or None, round(float(r["price"]), 2), r.get("unit_price"),
               day, "", 1 if r.get("discount") else 0, region, (r.get("promotion_text") or None),
               r.get("depots"), r.get("change_7d"), r.get("change_30d"))
        k = (chain, n["name"])  # aynı adlı iki ürün: ucuz olan (tekillik zincir + bölge + ad + gün)
        if k not in best or row[4] < best[k][4]:
            best[k] = row
    out = list(best.values())
    stats["rows"] = len(out)
    return out, stats


def import_region(day: str, region: str) -> dict:
    """Bir (gün, bölge): BigQuery'den çek, o gün + bölgenin satırlarını değiştir, alındı diye işaretle."""
    from app import price_import, product_groups, system

    d = date.fromisoformat(day)
    got = bq.query(offers_sql(), {"d": d, "d7": d - timedelta(days=7), "d30": d - timedelta(days=30), "region": region},
                   max_rows=MAX_ROWS_PER_REGION)
    rows, stats = rows_for(got, day, region)
    cut = (date.today() - timedelta(days=price_import.KEEP_DAYS)).isoformat()
    with system.connect() as c:
        c.execute("DELETE FROM official_prices WHERE day = ? AND region = ?", (day, region))
        c.executemany("INSERT OR REPLACE INTO official_prices (chain, group_key, title, brand, price, unit_price, day, "
                      "depot_id, discount, region, promotion, depots, change_7d, change_30d) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        c.execute("INSERT OR REPLACE INTO official_imports (run_date, region, rows, products, unmatched, imported_at) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (day, region, stats["rows"], stats["products"], stats["unmatched"],
                                                _now_iso()))
        c.execute("DELETE FROM official_prices WHERE day < ?", (cut,))
        c.execute("DELETE FROM official_imports WHERE run_date < ?", (cut,))
    product_groups.kick()  # kuyruğa giren adlar: arka planda yapay zekâ (açıksa)
    log.info("resmî fiyat içe aktarıldı: %s %s %s", day, region, stats)
    return stats


def _claim(now: datetime) -> bool:
    """Tur hakkı: sıradaki deneme zamanı geldiyse bir sonrakini yaz (tek işlem; birden çok işçide yalnız biri alır)."""
    from app import system

    with system.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT value FROM sys_settings WHERE key = ?", (LEASE_KEY,)).fetchone()
        if r and r[0] and now.timestamp() < float(r[0]):
            c.execute("ROLLBACK")
            return False
        c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES (?, ?)",
                  (LEASE_KEY, str(now.timestamp() + TRY_EVERY_S)))
    return True


def _set_next(ts: float) -> None:
    from app import system

    system.put(LEASE_KEY, str(ts))


def _save_status(**kw) -> None:
    from app import system

    try:
        old = json.loads(system.get(STATUS_KEY) or "{}")
    except ValueError:
        old = {}
    system.put(STATUS_KEY, json.dumps({**old, **kw}, ensure_ascii=False))


def run(now: datetime, max_regions: int | None = None) -> dict:
    """Bekleyen (gün, bölge)ları içe aktar (en çok max_regions). {imported: [[gün, bölge, satır]], left, error?}."""
    utc = now.astimezone(timezone.utc)
    done: list[list] = []
    try:
        todo = pending_runs(utc.date())
        for day, region in todo[:max_regions or MAX_REGIONS_PER_TICK]:
            st = import_region(day, region)
            done.append([day, region, st["rows"]])
    except bq.BQError as e:
        log.warning("resmî fiyat içe aktarımı: %s", e)
        _save_status(last_run_at=_now_iso(), last_error=str(e)[:300], last_error_at=_now_iso())
        return {"imported": done, "left": None, "error": str(e)}
    left = max(0, len(todo) - len(done))
    _save_status(last_run_at=_now_iso(), last_imported=done or (_status().get("last_imported") or []))
    return {"imported": done, "left": left}


def tick(now: datetime) -> dict | None:
    """Zamanlayıcıdan (app/jobs.scheduler, her dakika). Kapalıysa / saati gelmediyse / sırası değilse None."""
    if not enabled():
        return None
    if now.astimezone(timezone.utc).hour < START_HOUR_UTC or not _claim(now):
        return None
    try:
        out = run(now)
    except Exception as e:  # noqa: BLE001 - beklenmeyen hata: kaydet, yarım saat sonra yeniden
        log.exception("resmî fiyat içe aktarımı başarısız")
        _save_status(last_run_at=_now_iso(), last_error=f"{type(e).__name__}"[:300], last_error_at=_now_iso())
        return {"imported": [], "left": None, "error": type(e).__name__}
    if out.get("left"):
        _set_next(now.timestamp() + RETRY_SOON_S)
    return out


def _status() -> dict:
    from app import system

    try:
        return json.loads(system.get(STATUS_KEY) or "{}")
    except ValueError:
        return {}


def status() -> dict:
    """Yönetici paneli: {configured, last_day, regions, rows, imported_at, last_run_at, last_error, last_error_at}."""
    from app import system

    st = _status()
    with system.connect() as c:
        r = c.execute("SELECT MAX(run_date) FROM official_imports").fetchone()
        last = r[0] if r else None
        regs = c.execute("SELECT region, rows, imported_at FROM official_imports WHERE run_date = ? ORDER BY region",
                         (last,)).fetchall() if last else []
    err_newer = st.get("last_error") and (not regs or (st.get("last_error_at") or "") >= max(x[2] for x in regs))
    return {"configured": enabled(), "last_day": last, "regions": [x[0] for x in regs],
            "rows": sum(x[1] for x in regs), "imported_at": max((x[2] for x in regs), default=None),
            "last_run_at": st.get("last_run_at"), "last_error": st.get("last_error") if err_newer else None,
            "last_error_at": st.get("last_error_at") if err_newer else None}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    if not enabled():
        print(f"BigQuery kapalı: {bq.CRED_ENV} ayarlı değil ya da dosya okunamıyor", file=sys.stderr)
        return 2
    if len(argv) == 3:
        print(json.dumps(import_region(argv[1], argv[2]), ensure_ascii=False))
    else:
        print(json.dumps(run(datetime.now(timezone.utc), max_regions=100), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
