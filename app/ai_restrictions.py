"""Yöneticinin kötüye kullanım kısıtlaması (system.db → ai_restrictions; işlem kaydı ai_restriction_log).

Modlar:
- no_ai: yapay zekâ okuması kapalı. Tek kapı receipt._gemini_json (fiş, fatura, belge, döküm, posta bildirimi, kategori
  önerisi; app/parsers/ai.py ve app/bank_pdf.py da oradan geçer) → check_ai() RestrictedError atar. Yerel okuma
  (banka şablonları, kural tabanlı döküm, Tesseract OCR) maliyetsiz olduğu için AÇIK kalır: parse_document yapay zekâ
  reddedince yerel okumaya düşer.
- cap: günde en çok daily_cap belge okunur (yerel ya da yapay zekâ fark etmez). Paketin hakkı ayrıca uygulanır; ikisinden
  düşük olan bağlar. Kontrol doc_reads.check() içinde (check()), sayım doc_reads.charge() içinde (count()). Sınıra
  ulaşınca yapay zekâ çağrısı da kapanır (kayıtlı fişi yeniden okuma hak düşürmez; sınırsız tekrar denenmesin).
  no_ai ile birlikte daily_cap verilirse o da uygulanır.
- Kaldırma: satır silinir (remove).

Günlük sayaç (ai_restriction_days) herkes için tutulur (7 gün): kısıtlama gün ortasında konursa o günün okumaları da
sayılmış olur. Yönetici kısıtlanmaz. Kullanıcıya giden metin RestrictedError'dadır; yöneticinin notu kullanıcıya gitmez.
Destek adresi HESAPKITAP_SUPPORT_EMAIL (yoksa yalnız "bize yaz").
"""
import logging
import os
from datetime import date, datetime, timezone

from app.doc_reads import QuotaError

MODES = ("no_ai", "cap")
MAX_CAP = 1000
KEEP_DAYS = 7
log = logging.getLogger("hesapkitap.admin")


class RestrictedError(QuotaError):
    """Hesap yönetici tarafından sınırlandı. QuotaError alt sınıfı: hak doldu yolları (okunmadan saklanan fiş, posta)
    aynen çalışır; ama gövdede quota_full yok — istemci paket/abonelik sayfası açmaz, metni gösterir."""

    def __init__(self, msg: str, mode: str, kind: str = "belge"):
        super().__init__(msg, 0, False, kind)
        self.mode = mode

    def fields(self) -> dict:
        return {"manual": True, "restricted": True, "reason": "restricted", "kind": self.kind}


def message() -> str:
    addr = (os.environ.get("HESAPKITAP_SUPPORT_EMAIL") or "").strip()
    to = f"destek için {addr} adresine yaz" if addr else "destek için bize yaz"
    return f"Hesabında okuma geçici olarak sınırlandı; {to}. Bu arada kaydı elle ekleyebilirsin."


def _today() -> str:
    return date.today().isoformat()


def get(uid: int) -> dict | None:
    from app import system

    with system.connect() as c:
        r = c.execute("SELECT * FROM ai_restrictions WHERE user_id = ?", (uid,)).fetchone()
    return dict(r) if r else None


def all_rows() -> dict[int, dict]:
    from app import system

    with system.connect() as c:
        return {r["user_id"]: dict(r) for r in c.execute("SELECT * FROM ai_restrictions")}


def today_reads(uid: int) -> int:
    from app import system

    with system.connect() as c:
        r = c.execute("SELECT n FROM ai_restriction_days WHERE user_id = ? AND day = ?", (uid, _today())).fetchone()
    return int(r[0]) if r else 0


def put(uid: int, mode: str, daily_cap: int | None = None, note: str | None = None, by: int | None = None) -> dict:
    """Kısıtla ya da kısıtlamayı değiştir. ValueError: geçersiz mod / üst sınır."""
    from app import system

    if mode not in MODES:
        raise ValueError("Kısıtlama türü no_ai ya da cap olmalı")
    if mode == "cap" and daily_cap is None:
        raise ValueError("Günlük üst sınır gerekli")
    if daily_cap is not None and not 0 <= daily_cap <= MAX_CAP:
        raise ValueError(f"Günlük üst sınır 0–{MAX_CAP} arasında olmalı")
    note = (note or "").strip()[:300] or None
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with system.connect() as c:
        c.execute("INSERT OR REPLACE INTO ai_restrictions (user_id, mode, daily_cap, note, at, by) VALUES (?, ?, ?, ?, ?, ?)",
                  (uid, mode, daily_cap, note, at, by))
        c.execute("INSERT INTO ai_restriction_log (at, user_id, action, mode, daily_cap, note, by) "
                  "VALUES (?, ?, 'set', ?, ?, ?, ?)", (at, uid, mode, daily_cap, note, by))
    log.info("yapay zekâ kısıtlaması: kullanıcı #%s mod=%s günlük=%s yönetici #%s", uid, mode, daily_cap, by)
    return get(uid)


def remove(uid: int, by: int | None = None) -> bool:
    from app import system

    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with system.connect() as c:
        if not c.execute("DELETE FROM ai_restrictions WHERE user_id = ?", (uid,)).rowcount:
            return False
        c.execute("INSERT INTO ai_restriction_log (at, user_id, action, by) VALUES (?, ?, 'remove', ?)", (at, uid, by))
    log.info("yapay zekâ kısıtlaması kaldırıldı: kullanıcı #%s yönetici #%s", uid, by)
    return True


def history(uid: int, limit: int = 20) -> list[dict]:
    from app import system

    with system.connect() as c:
        return [dict(r) for r in c.execute("SELECT at, action, mode, daily_cap, note, by FROM ai_restriction_log "
                                           "WHERE user_id = ? ORDER BY id DESC LIMIT ?", (uid, limit))]


def _of(u: dict | None) -> dict | None:
    if not u or u.get("role") == "admin" or not u.get("id"):
        return None
    return get(u["id"])


def _cap_reached(u: dict, r: dict) -> bool:
    return r.get("daily_cap") is not None and today_reads(u["id"]) >= r["daily_cap"]


def check(u: dict | None, kind: str = "belge") -> None:
    """Okumadan önce (doc_reads.check): günlük üst sınır dolduysa RestrictedError. no_ai tek başına okumayı engellemez
    (yerel okuma açık; yapay zekâ check_ai'de kapanır)."""
    r = _of(u)
    if r and _cap_reached(u, r):
        raise RestrictedError(message(), r["mode"], kind)


def check_ai() -> None:
    """Yapay zekâ çağrısından önce (receipt._gemini_json): no_ai ya da günlük sınır dolmuşsa RestrictedError."""
    from app import db

    u = db.current_user()
    r = _of(u)
    if r and (r["mode"] == "no_ai" or _cap_reached(u, r)):
        raise RestrictedError(message(), r["mode"])


def count(c, uid: int) -> None:
    """Sayılan yeni belge (doc_reads.charge, aynı bağlantı/işlem): bugünkü okuma +1."""
    day = _today()
    c.execute("INSERT INTO ai_restriction_days (user_id, day, n) VALUES (?, ?, 1) "
              "ON CONFLICT(user_id, day) DO UPDATE SET n = n + 1", (uid, day))
    c.execute("DELETE FROM ai_restriction_days WHERE day < date(?, ?)", (day, f"-{KEEP_DAYS} days"))
