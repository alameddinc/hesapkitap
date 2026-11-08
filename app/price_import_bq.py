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
   çalışırsa aynı sonuç), official_imports'a yazılır; KEEP_DAYS'ten eskiler silinir. Aynı işlemde günün grup özeti
   official_daily'ye (app/market_ref.py summarize_day; 400 gün tutulur: ürün grafiğindeki piyasa bandı).
Veri kalite denetimi (yazmadan önce):
- Grup içi aykırı teklif: (gün, bölge, grup) içinde en az OUTLIER_MIN_ROWS teklif varsa ortancanın 1/OUTLIER_FACTOR'ünden
  ucuz ya da OUTLIER_FACTOR katından pahalı teklif atılır (büyük olasılıkla yanlış boy eşlemesi); stats.outliers, yönetici
  panelinde "tutarsız gruplar" (en çok atılan INCONSISTENT_TOP grup).
- Bölgenin önceki alınmış günüyle kıyas: satır sayısı DROP_LIMIT'ten fazla düştüyse ya da eşleşen grupların fiyat
  değişiminin ortancası SHIFT_LIMIT'i aştıysa (en az SHIFT_MIN_GROUPS ortak grupta) o gün ALINMAZ: önceki veri kalır,
  official_imports'a status "anomaly" + note yazılır (yeniden çekilmez; elle: python -m app.price_import_bq GÜN BÖLGE
  --force) ve yöneticilere push ("sistem" türü, push.ops_alert).
Kip (run_log.mode; sütun yoksa full): regional_chains çalışmasında o ilde yalnız il zincirleri (CarrefourSA, Hakmar,
Tarım Kredi) çekilir — yalnız gelen zincirlerin satırları değişir, satır düşüşü / kayma denetimi aynı (il, kip)ın önceki
alımıyla; official_imports'a kip yazılır.
Yarıda kalırsa (hata, yeniden başlatma) yazılmayan bölge sonraki turda yeniden denenir. Birden çok işçi aynı anda
çalıştırmasın diye tur hakkı sys_settings'te kilitle alınır. Son hata ve son tur özeti yönetici paneline (status()).
Elle: python -m app.price_import_bq [YYYY-MM-DD bölge [--force] [--mode=regional_chains]]
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
OUTLIER_FACTOR = 3.0
OUTLIER_MIN_ROWS = 3
DROP_LIMIT = 0.5             # satır sayısı önceki güne göre yarıdan çok düştüyse
SHIFT_LIMIT = 0.3            # eşleşen grupların fiyat değişimi ortancası %30'u aştıysa
SHIFT_MIN_GROUPS = 5
INCONSISTENT_TOP = 20


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


MODES = ("full", "regional_chains")
_no_mode: dict = {}  # run_log'da mode sütunu yok (eski toplayıcı): gün başına bir kez denenir


def runs_sql(with_mode: bool = True) -> str:
    mode = ", IFNULL(mode, 'full') AS mode" if with_mode else ""
    return (f"SELECT CAST(run_date AS STRING) AS run_date, region{mode} FROM {bq.table('run_log')} "
            f"WHERE run_date BETWEEN @d0 AND @d1 AND status = 'success' GROUP BY run_date, region{', mode' if with_mode else ''}")


def imported() -> set[tuple[str, str, str]]:
    from app import system

    with system.connect() as c:
        return {(r[0], r[1], r[2] or "full") for r in c.execute("SELECT run_date, region, mode FROM official_imports")}


def _mode(v) -> str:
    return v if v in MODES else "full"


def pending_runs(today: date) -> list[tuple[str, str, str]]:
    """BigQuery'de başarıyla biten, burada henüz alınmamış (gün, bölge, kip)lar: yeni gün önce. Kip run_log.mode
    (full | regional_chains: o ilde yalnız CarrefourSA / Hakmar / Tarım Kredi şubeleri çekildi); sütun yoksa full."""
    params = {"d0": today - timedelta(days=LOOKBACK_DAYS), "d1": today}
    got = None
    if _no_mode.get("day") != today.isoformat():
        try:
            got = bq.query(runs_sql(), params)
        except bq.BQError as e:
            if "mode" not in str(e).lower():
                raise
            _no_mode["day"] = today.isoformat()
    if got is None:
        got = bq.query(runs_sql(with_mode=False), params)
    done = imported()
    runs = {(r["run_date"], r["region"], _mode(r.get("mode"))) for r in got if r.get("run_date") and r.get("region")}
    return sorted(runs - done, key=lambda x: (x[0], x[1], x[2]), reverse=True)


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
    from app import product_catalog
    from app import product_groups as pg

    shelves = product_catalog.shelves()  # raf etiketi: adın söylemediği biçim (çekirdek / öğütülmüş kahve)
    names = []
    for _, r in keep:
        t = price_import.full_title(r["title"], r.get("volume_weight"))
        names.append({"name": t, "brand": r.get("brand") or None,
                      "shelf": shelves.get(pg.name_norm(t)) or product_catalog.shelf_text(r.get("main_category"), None)
                      or None})
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
    out, dropped = drop_outliers(list(best.values()))
    stats["outliers"] = sum(d["dropped"] for d in dropped.values())
    stats["inconsistent"] = dropped
    stats["rows"] = len(out)
    return out, stats


def drop_outliers(rows: list[tuple]) -> tuple[list[tuple], dict[str, dict]]:
    """Grup içi aykırı teklifleri at (satır: (zincir, grup, ad, marka, fiyat, …)). Dönen: (kalan satırlar,
    {grup: {median, dropped}} — yalnız teklifi atılan gruplar)."""
    from statistics import median

    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r[1], []).append(r[4])
    meds = {g: median(v) for g, v in by.items() if len(v) >= OUTLIER_MIN_ROWS}
    keep, dropped = [], {}
    for r in rows:
        m = meds.get(r[1])
        if m and (r[4] < m / OUTLIER_FACTOR or r[4] > m * OUTLIER_FACTOR):
            d = dropped.setdefault(r[1], {"median": round(m, 2), "dropped": 0})
            d["dropped"] += 1
        else:
            keep.append(r)
    return keep, dropped


def _group_medians(rows) -> dict[str, float]:
    from statistics import median

    by: dict[str, list[float]] = {}
    for g, p in rows:
        by.setdefault(g, []).append(p)
    return {g: median(v) for g, v in by.items()}


def anomaly(c, day: str, region: str, rows: list[tuple], mode: str = "full") -> str | None:
    """Bölgenin aynı kipteki (full | regional_chains) önceki alınmış (status ok) günüyle kıyas; sorun varsa neden
    metni, yoksa None. İlk gün denetlenmez. regional_chains kipinde fiyat kayması yalnız o çalışmadaki zincirlerle."""
    from statistics import median

    prev = c.execute("SELECT run_date, rows FROM official_imports WHERE region = ? AND mode = ? AND run_date < ? "
                     "AND status = 'ok' ORDER BY run_date DESC LIMIT 1", (region, mode, day)).fetchone()
    if not prev:
        return None
    pday, prows = prev[0], prev[1] or 0
    if prows and len(rows) < prows * (1 - DROP_LIMIT):
        return f"satır sayısı {prows} → {len(rows)} (önceki gün {pday})"
    chains = sorted({r[0] for r in rows}) if mode != "full" else None
    only = f" AND chain IN ({','.join('?' * len(chains))})" if chains else ""
    old = _group_medians(c.execute(f"SELECT group_key, price FROM official_prices WHERE day = ? AND region = ?{only}",
                                   (pday, region, *(chains or []))).fetchall())
    if not old and mode == "full":  # önceki günün satırları silinmiş (KEEP_DAYS): günlük özetten
        from app import provinces

        prov = provinces.province_of(region) or ""
        old = {g: m for g, m in c.execute("SELECT group_key, median FROM official_daily WHERE day = ? AND region = ?",
                                          (pday, prov))}
    new = _group_medians([(r[1], r[4]) for r in rows])
    common = [g for g in new if g in old and old[g] > 0]
    if len(common) < SHIFT_MIN_GROUPS:
        return None
    shift = median(new[g] / old[g] - 1 for g in common)
    if abs(shift) > SHIFT_LIMIT:
        return f"fiyat ortancası %{shift * 100:+.0f} ({len(common)} grup, önceki gün {pday})"
    return None


def import_region(day: str, region: str, force: bool = False, mode: str = "full") -> dict:
    """Bir (gün, bölge, kip): BigQuery'den çek, o gün + bölgenin satırlarını değiştir, alındı diye işaretle. Kalite
    denetimi (anomaly, aynı kipin önceki günüyle) tutmazsa yazmaz: stats.anomaly = neden (force: denetimi atla).
    regional_chains kipinde yalnız bu çalışmada gelen zincirlerin satırları değişir (ulusal zincirlerin — BİM, A101,
    ŞOK, Migros — o ildeki satırları silinmez)."""
    mode = _mode(mode)
    from app import market_ref, price_import, product_groups, system

    d = date.fromisoformat(day)
    got = bq.query(offers_sql(), {"d": d, "d7": d - timedelta(days=7), "d30": d - timedelta(days=30), "region": region},
                   max_rows=MAX_ROWS_PER_REGION)
    rows, stats = rows_for(got, day, region)
    _note_inconsistent(day, stats.pop("inconsistent"))
    cut = (date.today() - timedelta(days=price_import.KEEP_DAYS)).isoformat()
    with system.connect() as c:
        if not force and (why := anomaly(c, day, region, rows, mode)):
            c.execute("INSERT OR REPLACE INTO official_imports (run_date, region, rows, products, unmatched, imported_at, "
                      "status, note, outliers, mode) VALUES (?, ?, 0, ?, ?, ?, 'anomaly', ?, ?, ?)",
                      (day, region, stats["products"], stats["unmatched"], _now_iso(), why, stats["outliers"], mode))
            stats["anomaly"] = why
    if stats.get("anomaly"):
        log.warning("resmî fiyat alınmadı (anomali): %s %s %s", day, region, stats["anomaly"])
        _alert(day, region, stats["anomaly"])
        return stats
    with system.connect() as c:
        if mode == "full":
            c.execute("DELETE FROM official_prices WHERE day = ? AND region = ?", (day, region))
        else:  # yalnız il zincirleri çekildi: ulusal zincirlerin satırları kalır
            chains = sorted({r[0] for r in rows})
            if chains:
                c.execute(f"DELETE FROM official_prices WHERE day = ? AND region = ? AND chain IN "
                          f"({','.join('?' * len(chains))})", (day, region, *chains))
        c.executemany("INSERT OR REPLACE INTO official_prices (chain, group_key, title, brand, price, unit_price, day, "
                      "depot_id, discount, region, promotion, depots, change_7d, change_30d) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        c.execute("INSERT OR REPLACE INTO official_imports (run_date, region, rows, products, unmatched, imported_at, "
                  "status, note, outliers, mode) VALUES (?, ?, ?, ?, ?, ?, 'ok', NULL, ?, ?)",
                  (day, region, stats["rows"], stats["products"], stats["unmatched"], _now_iso(), stats["outliers"], mode))
        market_ref.summarize_day(c, day, region)  # günlük piyasa özeti (400 gün; bant zamanla büyür)
        c.execute("DELETE FROM official_prices WHERE day < ?", (cut,))
        c.execute("DELETE FROM official_imports WHERE run_date < ?", (cut,))
    product_groups.kick()  # kuyruğa giren adlar: arka planda yapay zekâ (açıksa)
    log.info("resmî fiyat içe aktarıldı: %s %s %s", day, region, stats)
    return stats


def _alert(day: str, region: str, why: str) -> None:
    """Anomali: yöneticilere push (aynı gün + bölge bir kez). Hata içe aktarımı bozmaz."""
    try:
        from app import push

        push.ops_alert("anomaly", region, f"{day}: {why}. Veri alınmadı, önceki gün korunuyor.")
    except Exception:  # noqa: BLE001
        log.exception("anomali bildirimi gönderilemedi")


def _note_inconsistent(day: str, groups: dict[str, dict]) -> None:
    """Tutarsız gruplar (yönetici paneli): aynı günün bölgeleri birleşir (atılan toplanır), yeni gün öncekini siler."""
    cur = _status().get("inconsistent") or {}
    by = dict(cur.get("groups") or {}) if cur.get("day") == day else {}
    for g, d in groups.items():
        old = by.get(g) or {"median": d["median"], "dropped": 0}
        by[g] = {"median": d["median"], "dropped": old["dropped"] + d["dropped"]}
    if groups or cur.get("day") != day:
        _save_status(inconsistent={"day": day, "groups": by})


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
        for day, region, mode in todo[:max_regions or MAX_REGIONS_PER_TICK]:
            st = import_region(day, region, mode=mode)
            done.append([day, region, 0 if st.get("anomaly") else st["rows"]] + (["anomaly"] if st.get("anomaly") else []))
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
        r = c.execute("SELECT MAX(run_date) FROM official_imports WHERE status = 'ok'").fetchone()
        last = r[0] if r else None
        regs = c.execute("SELECT region, rows, imported_at, outliers FROM official_imports WHERE run_date = ? "
                         "AND status = 'ok' ORDER BY region", (last,)).fetchall() if last else []
        since = (date.today() - timedelta(days=7)).isoformat()
        anomalies = [{"day": a[0], "region": a[1], "note": a[2], "at": a[3]} for a in c.execute(
            "SELECT run_date, region, note, imported_at FROM official_imports WHERE status = 'anomaly' AND run_date >= ? "
            "ORDER BY run_date DESC, region", (since,))]
    err_newer = st.get("last_error") and (not regs or (st.get("last_error_at") or "") >= max(x[2] for x in regs))
    inc = st.get("inconsistent") or {}
    top = sorted(({"group_key": g, **d} for g, d in (inc.get("groups") or {}).items()),
                 key=lambda x: (-x["dropped"], x["group_key"]))[:INCONSISTENT_TOP]
    from app import community_export

    return {"configured": enabled(), "last_day": last, "regions": sorted({x[0] for x in regs}),
            "rows": sum(x[1] for x in regs), "imported_at": max((x[2] for x in regs), default=None),
            "last_run_at": st.get("last_run_at"), "last_error": st.get("last_error") if err_newer else None,
            "last_error_at": st.get("last_error_at") if err_newer else None,
            "outliers": sum(x[3] or 0 for x in regs), "anomalies": anomalies,
            "inconsistent_groups": {"day": inc.get("day"), "groups": top},
            "community_export": community_export.status()}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    if not enabled():
        print(f"BigQuery kapalı: {bq.CRED_ENV} ayarlı değil ya da dosya okunamıyor", file=sys.stderr)
        return 2
    force = "--force" in argv
    mode = next((a.split("=", 1)[1] for a in argv if a.startswith("--mode=")), "full")
    argv = [a for a in argv if a != "--force" and not a.startswith("--mode=")]
    if len(argv) == 3:
        print(json.dumps(import_region(argv[1], argv[2], force=force, mode=mode), ensure_ascii=False))
    else:
        print(json.dumps(run(datetime.now(timezone.utc), max_regions=100), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
