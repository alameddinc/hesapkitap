"""İşyeri adı kuralı sürümü (textnorm.NAME_VERSION) artınca her defterde ada bağlı ayarları yeni ada taşı.

İşyeri adı saklanmaz, her okumada açıklamadan hesaplanır (textnorm.merchant_name); kural değişince ('SPOTIFY.COM
ISTANBUL TR' → 'SPOTIFY', sürüm 2) grup kendiliğinden yeni adla görünür. Ama abonelik durumu, işyeri kısma hedefi,
öğrenilmiş eşleşme kuralı ve sabit kalem anahtarı eski ADLA kayıtlıdır; onlar db.migrate_name_keys ile eski addan
yeniye taşınır (ödeme aracısı listesi değişince çalışan db.sync_gateway_keys ile aynı kural). Rakam, kategori, ham
açıklama değişmez; kategori kuralları (rules) açıklamaya baktığı için etkilenmez.

Nasıl: bağlantı açılışında DEĞİL; zamanlayıcının her turunda (jobs.scheduler) defter defter, tur başına BUDGET_S
saniye (app/receipt_rekey.py düzeni). Bir defterin işi tek kısa işlem: farklı açıklamaları bir kez eski ve yeni kurala
göre adlandırıp eşleme çıkarır (önbelleksiz; açıklama sayısı kadar). Defter bitince settings'e KEY_VERSION; bütün
alanlar bitince sys_settings'e. Tekrar çalışması zararsızdır. Yeni (boş) defter açılışta doğrudan güncel sürümle
işaretlenir; eski sürümlü bir defter sonradan gelirse (geri yükleme, taşıma) sistem damgası düşürülür ve iş yeniden
koşar (db.init_db → mark_book).
"""
import logging
import time

from app import db

log = logging.getLogger("ekstre.merchant_rekey")

KEY_VERSION = "merchant_names:version"   # sys_settings (hepsi bitti) ve her defterin settings'i (o defter bitti)
BUDGET_S = 10.0
FIRST_VERSION = 1  # damgası olmayan defter bu kuralla adlandırılmış sayılır


def _target() -> int:
    from app.textnorm import NAME_VERSION

    return NAME_VERSION


def pending() -> bool:
    from app import system

    return int(system.get(KEY_VERSION) or 0) < _target()


def book_version() -> int:
    return int(db.get_setting(KEY_VERSION) or FIRST_VERSION)


def mark_book(c) -> None:
    """init_db'den (açık bağlantı): damgasız defter boşsa güncel sürümle işaretle (taşınacak ad yok); eskiyse ve
    bütün defterler bitti sayılıyorsa sistem damgasını düşür (bir sonraki tur bu defteri de taşır). Tek ayar okuması."""
    target = _target()
    row = c.execute("SELECT value FROM settings WHERE key = ?", (KEY_VERSION,)).fetchone()
    if row and int(row[0] or 0) >= target:
        return
    if not row and not any(c.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
                           for t in ("subscription_state", "savings_goals", "match_rules", "recurring")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (KEY_VERSION, str(target)))
        return
    try:
        if not pending():
            from app import system

            system.put(KEY_VERSION, None)
    except Exception:  # noqa: BLE001 - sistem veritabanı yoksa sonraki gece taraması görür
        log.debug("işyeri adı sürümü: sistem damgası düşürülemedi", exc_info=True)


def migrate_book() -> dict | None:
    """Bu defterde eski sürüm adlarından güncel adlara taşı; defter zaten günceldeyse None."""
    from app.textnorm import current_gateways, merchant_name_with

    old, new = book_version(), _target()
    if old >= new:
        return None
    gs = current_gateways()
    report = db.migrate_name_keys(lambda d: merchant_name_with(d, gs, old), lambda d: merchant_name_with(d, gs, new))
    db.set_setting(KEY_VERSION, str(new))
    return report


def tick(spaces=None) -> dict | None:
    """Sürüm eskiyse bütün alanlarda defter defter taşı (jobs.scheduler her turda çağırır). İş yoksa None; süre bütçesi
    dolunca {"finished": False} (sonraki tur kaldığı yerden), bitince sys_settings'e sürüm yazılır."""
    from app import jobs, system

    if not pending():
        return None
    t0, done = time.monotonic(), {"books": 0, "subscriptions": 0, "goals": 0}
    for path, user in (jobs.spaces() if spaces is None else spaces):
        if done["books"] and time.monotonic() - t0 >= BUDGET_S:
            return {**done, "finished": False}
        try:
            with jobs.book_space(path, user):
                rep = migrate_book()
        except Exception:  # noqa: BLE001 - bozuk defter ötekileri durdurmasın (damgası yazılmaz; açılınca mark_book yeniden dener)
            log.exception("işyeri adı sürümü: defter atlandı")
            continue
        if rep is not None:
            done["books"] += 1
            done["subscriptions"] += rep.get("subscriptions", 0)
            done["goals"] += rep.get("goals", 0)
    system.put(KEY_VERSION, str(_target()))
    log.info("işyeri adı sürümü %s: %s", _target(), done)
    return {**done, "finished": True}
