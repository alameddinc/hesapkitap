"""Topluluk fiyatları → BigQuery (gecelik): `hesapkitapproject.marketfiyati.community_observations`.

Ne gider (app/community_prices.py satırlarından, gün başına toplu): (gün, il, zincir, grup, ad) başına ortanca birim fiyat
(önce kişi başı ortanca, sonra kişiler arası), gözlem sayısı, farklı katkı sayısı; ürün grubunun adı, kategorisi, alt
kategorisi ve boyundan litre / kilo / adet fiyatı (product_groups). Katkı kimliği (günlük HMAC) yalnız SAYILIR, gitmez.
k-anonimlik: (gün, zincir, grup) için farklı katkı MIN_CONTRIBUTORS'tan azsa o grubun satırları gitmez (tek kişinin izi).
İl: satırda region varsa (fişin şube yazısındaki il; provinces.find_in) o, yoksa NULL — kişinin konumu kullanılmaz; (gün,
zincir, grup, il) için farklı katkı MIN_CONTRIBUTORS'tan azsa il yazılmaz (satır NULL ile birleşir).

Akış (tick, app/jobs.scheduler'dan her dakika): yerel saat START (00:30) sonrası, günde bir kez (DONE_KEY); dünden geriye
LOOKBACK_DAYS gün içinde henüz gönderilmemiş ya da gönderildikten sonra kaynağı değişmiş (geç kaydedilen fiş) günler,
eskiden yeniye. Her gün: BigQuery yükleme işi (çok parçalı NEWLINE_DELIMITED_JSON, hedef `tablo$YYYYMMDD`,
WRITE_TRUNCATE: yeniden çalışırsa aynı sonuç; app/bq.py load_ndjson), iş bitene kadar beklenir; community_exports'a
(system.db) gün, satır sayısı, kaynak imzası yazılır. Hata: son hata kaydedilir, RETRY_S sonra yeniden denenir.
Yönetici paneli: status() → GET /api/admin/official-prices community_export.

Ayar: HESAPKITAP_BQ_WRITER_CREDENTIALS (yazıcı servis hesabı; yalnız bu tabloya yazar, iş çalıştırır). Yoksa kapalı.
Elle: python -m app.community_export [YYYY-MM-DD]
"""
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from app import bq

log = logging.getLogger("hesapkitap.community_export")

TABLE = "community_observations"
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Istanbul"))
START = (0, 30)               # yerel saat (sa, dk)
LOOKBACK_DAYS = 7
RETRY_S = 30 * 60
MIN_CONTRIBUTORS = 2
LEASE_KEY, DONE_KEY, STATUS_KEY = "community_export:next_try", "community_export:done", "community_export:status"

SCHEMA = [{"name": "run_date", "type": "DATE", "mode": "REQUIRED"},
          {"name": "region", "type": "STRING", "mode": "NULLABLE"},
          {"name": "chain", "type": "STRING", "mode": "NULLABLE"},
          {"name": "group_key", "type": "STRING", "mode": "NULLABLE"},
          {"name": "group_label", "type": "STRING", "mode": "NULLABLE"},
          {"name": "category", "type": "STRING", "mode": "NULLABLE"},
          {"name": "subcategory", "type": "STRING", "mode": "NULLABLE"},
          {"name": "name_norm", "type": "STRING", "mode": "NULLABLE"},
          {"name": "unit_price", "type": "FLOAT64", "mode": "NULLABLE"},
          {"name": "unit_price_per", "type": "FLOAT64", "mode": "NULLABLE"},
          {"name": "per_unit", "type": "STRING", "mode": "NULLABLE"},
          {"name": "observations", "type": "INT64", "mode": "NULLABLE"},
          {"name": "contributors", "type": "INT64", "mode": "NULLABLE"},
          {"name": "loaded_at", "type": "TIMESTAMP", "mode": "NULLABLE"}]


def enabled() -> bool:
    return bq.writer_configured()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _source(day: str) -> list:
    from app import system

    with system.connect() as c:
        return c.execute("SELECT chain, group_key, name_norm, contributor, unit_price, region FROM community_prices "
                         "WHERE day = ? ORDER BY id", (day,)).fetchall()


def _signature(rows: list) -> str:
    """Kaynağın imzası (satır sayısı + içerik özeti; katkı kimliği dahil, yalnız burada karşılaştırma için)."""
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(list(r), ensure_ascii=False).encode())
    return f"{len(rows)}:{h.hexdigest()[:16]}"


def aggregate(day: str, rows: list | None = None, loaded_at: str | None = None) -> list[dict]:
    """Bir günün dışa aktarılacak satırları (tablo şemasında). k-anonimlik: (zincir, grup) için farklı katkı <
    MIN_CONTRIBUTORS ise o grup atlanır."""
    from app import product_groups

    rows = _source(day) if rows is None else rows
    rows = [r for r in rows if r[1]]
    group_people: dict[tuple[str, str], set[str]] = {}
    region_people: dict[tuple[str, str, str], set[str]] = {}
    for chain, g, _nn, who, _p, region in rows:
        group_people.setdefault((chain, g), set()).add(who)
        if region:
            region_people.setdefault((chain, g, region), set()).add(who)
    people: dict[tuple, dict[str, list[float]]] = {}
    for chain, g, nn, who, price, region in rows:
        if len(group_people[(chain, g)]) < MIN_CONTRIBUTORS:
            continue
        if region and len(region_people[(chain, g, region)]) < MIN_CONTRIBUTORS:
            region = None  # ilde tek kişi: il yazılmaz (ülke satırına katılır)
        people.setdefault((region or None, chain, g, nn), {}).setdefault(who, []).append(price)
    keep = people
    keys = sorted({k[2] for k in keep})
    labels, paths = product_groups.labels(keys), product_groups.paths(keys)
    at = loaded_at or _now_iso()
    out = []
    for (region, chain, g, nn), by in sorted(keep.items(), key=lambda x: tuple(str(v) for v in x[0])):
        price = round(median(median(v) for v in by.values()), 2)
        cat, sub = paths.get(g) or (None, None)
        up = product_groups.unit_price(price, g)
        out.append({"run_date": day, "region": region, "chain": chain, "group_key": g,
                    "group_label": labels.get(g), "category": cat, "subcategory": sub, "name_norm": nn,
                    "unit_price": price, "unit_price_per": up.get("unit_price_per"), "per_unit": up.get("per_unit"),
                    "observations": sum(len(v) for v in by.values()), "contributors": len(by), "loaded_at": at})
    return out


def _exported() -> dict[str, dict]:
    from app import system

    with system.connect() as c:
        return {r["day"]: dict(r) for r in c.execute("SELECT day, rows, signature, exported_at FROM community_exports")}


def export_day(day: str) -> dict:
    """Bir günü BigQuery'ye yaz (gün bölümü baştan). Dönen {day, rows, job_id?}. Hata: bq.BQError."""
    from app import system

    src = _source(day)
    sig = _signature(src)
    rows = aggregate(day, src)
    prev = _exported().get(day)
    job = None
    if rows or (prev and prev["rows"]):  # boş gün ilk kez: iş gereksiz; daha önce dolu gittiyse boşla yeniden yazılır
        job = f"hk_community_{day.replace('-', '')}_{uuid.uuid4().hex[:12]}"
        bq.load_ndjson(TABLE, date.fromisoformat(day), rows, SCHEMA, job)
    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO community_exports (day, rows, signature, exported_at, job_id) "
                  "VALUES (?, ?, ?, ?, ?)", (day, len(rows), sig, _now_iso(), job))
        c.execute("DELETE FROM community_exports WHERE day < ?",
                  ((date.fromisoformat(day) - timedelta(days=400)).isoformat(),))
    log.info("topluluk fiyatları BigQuery'ye yazıldı: %s %d satır", day, len(rows))
    return {"day": day, "rows": len(rows), **({"job_id": job} if job else {})}


def due_days(today: date) -> list[str]:
    """Dünden geriye LOOKBACK_DAYS gün: gönderilmemiş ya da kaynağı değişmiş günler, eskiden yeniye."""
    done = _exported()
    out = []
    for n in range(LOOKBACK_DAYS, 0, -1):
        d = (today - timedelta(days=n)).isoformat()
        prev = done.get(d)
        if not prev or prev["signature"] != _signature(_source(d)):
            out.append(d)
    return out


def _status() -> dict:
    from app import system

    try:
        return json.loads(system.get(STATUS_KEY) or "{}")
    except ValueError:
        return {}


def _save_status(**kw) -> None:
    from app import system

    system.put(STATUS_KEY, json.dumps({**_status(), **kw}, ensure_ascii=False))


def run(today: date) -> dict:
    """Bekleyen günleri gönder; ilk hatada durur. {exported: [[gün, satır]] (yükleme işi çalışan günler), error?}."""
    done: list[list] = []
    try:
        for d in due_days(today):
            if "job_id" in (got := export_day(d)):  # boş gün ilk kez: yalnız kaydedilir
                done.append([d, got["rows"]])
    except Exception as e:  # noqa: BLE001 - BQError ya da beklenmeyen: kaydet, sonra yeniden
        msg = str(e)[:300] if isinstance(e, bq.BQError) else type(e).__name__
        log.warning("topluluk dışa aktarımı: %s", msg)
        _save_status(last_run_at=_now_iso(), last_error=msg, last_error_at=_now_iso())
        return {"exported": done, "error": msg}
    _save_status(last_run_at=_now_iso(), last_error=None, last_error_at=None)
    return {"exported": done}


def _claim(ts: float) -> bool:
    from app import system

    with system.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT value FROM sys_settings WHERE key = ?", (LEASE_KEY,)).fetchone()
        if r and r[0] and ts < float(r[0]):
            c.execute("ROLLBACK")
            return False
        c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES (?, ?)", (LEASE_KEY, str(ts + RETRY_S)))
    return True


def tick(now: datetime) -> dict | None:
    """Zamanlayıcıdan (her dakika). Kapalıysa, 00:30'dan önceyse, bugün bittiyse ya da sırası değilse None."""
    from app import system

    if not enabled():
        return None
    local = now.astimezone(TZ)
    today = local.date().isoformat()
    if (local.hour, local.minute) < START or system.get(DONE_KEY) == today or not _claim(now.timestamp()):
        return None
    out = run(local.date())
    if not out.get("error"):
        system.put(DONE_KEY, today)
    return out


def status() -> dict:
    """{configured, last_day (BigQuery'ye yazılan son gün), rows, exported_at, last_run_at, last_error, last_error_at}."""
    from app import system

    st = _status()
    with system.connect() as c:
        r = c.execute("SELECT day, rows, exported_at FROM community_exports WHERE job_id IS NOT NULL "
                      "ORDER BY day DESC LIMIT 1").fetchone()
    return {"configured": enabled(), "last_day": r[0] if r else None, "rows": r[1] if r else 0,
            "exported_at": r[2] if r else None, "last_run_at": st.get("last_run_at"),
            "last_error": st.get("last_error"), "last_error_at": st.get("last_error_at")}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    if not enabled():
        print(f"BigQuery yazıcı kapalı: {bq.WRITER_ENV} ayarlı değil ya da dosya okunamıyor", file=sys.stderr)
        return 2
    if len(argv) == 2:
        print(json.dumps(export_day(argv[1]), ensure_ascii=False))
    else:
        print(json.dumps(run(datetime.now(TZ).date()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
