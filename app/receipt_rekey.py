"""Kayıtlı fiş kalemlerinin fiyat takibi anahtarını (receipt_items.product_key) bugünkü kuralla yeniden türet.

Neden: kalemin adı sonradan düzeltildiğinde (istemci eski "product"ı geri yolluyordu) anahtar eski addan kalıyordu:
"Örnekmarka …" adlı satır "TEKEL URUNLERI" anahtarıyla bakkalın departman satırıyla (birkaç paketin toplamı) aynı ürün
sayılıp "₺655 → ₺133 (−%80)" gösteriliyordu. Yeni kayıtlar db.save_receipt_items'ta doğru yazılır; bu modül eskileri
düzeltir: product ada ait değilse (db.item_product) atılır, anahtar db.item_key ile yeniden yazılır.

Departman satırı ("TEKEL ÜRÜNLERİ", "REYON 3", "KDV %10") ayrıca işaretlenmez: db.item_kind her okumada addan karar
verir (fiyat listesi, grafik, öneri, topluluk, alışveriş listesi eşleşmesi onu süzer) — kural değişince göç gerekmez.

Nasıl: bağlantı açılışında DEĞİL (07.11.2026: açılışta tek işlemde yapılan yeniden gruplama canlı sunucuyu 15 dk
kilitledi), zamanlayıcının her turunda (jobs.scheduler) parça parça: her alanda (ana defter + kullanıcı defterleri)
id sırasıyla CHUNK satırlık kısa işlemler, tur başına BUDGET_S saniye. İlerleme o defterin settings tablosunda
(KEY_CURSOR), alan bitince KEY_VERSION; bütün alanlar bitince sys_settings'e VERSION yazılır ve iş biter. Tekrar
çalıştırmak zararsızdır (aynı sonucu yazar). Kural değişirse VERSION artırılır, iş kendiliğinden yeniden koşar.
"""
import logging
import time

from app import db

log = logging.getLogger("ekstre.receipt_rekey")

VERSION = 1
KEY_VERSION = "receipt_keys:version"   # sys_settings (hepsi bitti) ve her defterin settings'i (o defter bitti)
KEY_CURSOR = "receipt_keys:cursor"     # defterin settings'i: son işlenen receipt_items.id
CHUNK = 500
BUDGET_S = 10.0


def pending() -> bool:
    from app import system

    return int(system.get(KEY_VERSION) or 0) < VERSION


def _fix(r) -> tuple | None:
    """(product, product_key, id) güncellemesi; değişiklik yoksa None."""
    product = db.item_product(r["name"], r["product"])
    key = db.product_key(product or r["name"] or "")
    if not key or (key == r["product_key"] and product == r["product"]):
        return None
    return product, key, r["id"]


def _setting(c, key: str) -> str | None:
    row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def chunk() -> tuple[int, int, bool]:
    """Bu defterde bir parça (tek kısa işlem): (bakılan, değişen, defter bitti mi)."""
    with db.connect() as c:
        if int(_setting(c, KEY_VERSION) or 0) >= VERSION:
            return 0, 0, True
        cur = int(_setting(c, KEY_CURSOR) or 0)
        got = c.execute("SELECT id, name, product, product_key FROM receipt_items WHERE id > ? ORDER BY id LIMIT ?",
                        (cur, CHUNK)).fetchall()
        upd = [u for r in got if (u := _fix(r))]
        if upd:
            c.executemany("UPDATE receipt_items SET product = ?, product_key = ? WHERE id = ?", upd)
        if len(got) < CHUNK:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (KEY_VERSION, str(VERSION)))
            c.execute("DELETE FROM settings WHERE key = ?", (KEY_CURSOR,))
            return len(got), len(upd), True
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (KEY_CURSOR, str(got[-1]["id"])))
        return len(got), len(upd), False


def tick(spaces=None) -> dict | None:
    """Sürüm eskiyse bütün alanlarda parça parça yeniden anahtarla (jobs.scheduler her turda çağırır). İş yoksa None;
    süre bütçesi dolunca {"finished": False} (sonraki tur kaldığı yerden), bitince sys_settings'e sürüm yazılır."""
    from app import jobs, system

    if not pending():
        return None
    t0, done = time.monotonic(), {"rows": 0, "changed": 0}
    for path, user in (jobs.spaces() if spaces is None else spaces):
        with db.use_space(path, user):
            while True:
                if done["rows"] and time.monotonic() - t0 >= BUDGET_S:
                    return {**done, "finished": False}
                n, ch, fin = chunk()
                done["rows"] += n
                done["changed"] += ch
                if fin:
                    break
    system.put(KEY_VERSION, str(VERSION))
    log.info("fiş kalemi anahtarları yeniden türetildi: %s", done)
    return {**done, "finished": True}
