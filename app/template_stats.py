"""Şablon isabet ölçümü: belge yapay zekâsız mı (şablon / bankaya özel okuyucu) okundu, yapay zekâya mı gitti, neden.

Okuma hakkı her okumada düşer; yapay zekâ maliyetini sıfıra indiren tek yol şablondur. Bu modül her okumanın sonucunu
gün / belge türü / sonuç / neden kodu olarak SAYAR (system.db → template_events). Belgeden içerik yok: işyeri, banka adı,
tutar, metin yazılmaz; neden yalnız sabit bir kod.

- doc: fis | ekstre | dokum
- outcome: sablon (şablonla, ₺0) | parser (bankaya özel okuyucu, ₺0) | ai (yapay zekâ) | yerel (anahtarsız kural okuması)
  | learn (yapay zekâ okumasından şablon öğrenme denemesi; isabet oranına girmez)
- reason: okuma kaçtıysa neden (receipt_templates.MISS_REASONS: no_text, no_template, no_structure, sum, ocr_digit,
  date, …); learn için sonuç (learned, known, ai_sum, no_match, …).

Yönetici: overview(days) → belge türü başına şablon isabet oranı (sablon + parser) / (sablon + parser + ai + yerel),
neden dağılımı ve öğrenme sonuçları (GET /api/admin/receipt-templates "hit_rate"). Kayıtlar 400 gün tutulur.
"""
import logging
import re
from datetime import date, timedelta

log = logging.getLogger("hesapkitap.template_stats")

SCHEMA = """
CREATE TABLE IF NOT EXISTS template_events (
    day TEXT NOT NULL,          -- YYYY-MM-DD
    doc TEXT NOT NULL,          -- fis | ekstre | dokum
    outcome TEXT NOT NULL,      -- sablon | parser | ai | yerel | learn
    reason TEXT NOT NULL,       -- sabit neden kodu (içerik yok)
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, doc, outcome, reason)
);
"""
DOCS = ("fis", "ekstre", "dokum")
OUTCOMES = ("sablon", "parser", "ai", "yerel", "learn")
FREE = ("sablon", "parser")  # yapay zekâsız okuma
KEEP_DAYS = 400
_CODE = re.compile(r"[a-z_]{1,24}")


def _init(c) -> None:
    c.executescript(SCHEMA)


def record(doc: str, outcome: str, reason: str | None = None) -> None:
    """Bir okumayı say. Hata okumayı bozmaz."""
    if doc not in DOCS or outcome not in OUTCOMES:
        return
    reason = reason if reason and _CODE.fullmatch(reason) else "-"
    try:
        from app import system

        with system.connect() as c:
            _init(c)
            c.execute("INSERT INTO template_events (day, doc, outcome, reason, n) VALUES (?, ?, ?, ?, 1) "
                      "ON CONFLICT (day, doc, outcome, reason) DO UPDATE SET n = n + 1",
                      (date.today().isoformat(), doc, outcome, reason))
    except Exception:  # noqa: BLE001 - sayaç okumayı bozmaz
        log.warning("şablon sayacı yazılamadı", exc_info=True)


def record_statement(stmt) -> None:
    """Kart ekstresi okuması (app/parsers parse_statement sonucu): parser | sablon | ai (neden stmt.template_miss)."""
    src = getattr(stmt, "source", None)
    if src == "parser":
        record("ekstre", "parser")
    elif src == "sablon":
        record("ekstre", "sablon")
    elif getattr(stmt, "ai_parsed", False):
        record("ekstre", "ai", getattr(stmt, "template_miss", None) or "-")


def prune(days: int = KEEP_DAYS) -> int:
    from app import system

    with system.connect() as c:
        _init(c)
        return c.execute("DELETE FROM template_events WHERE day < ?",
                         ((date.today() - timedelta(days=days)).isoformat(),)).rowcount


def overview(days: int = 30) -> dict:
    """Son `days` günde belge türü başına: okuma sayısı, yapay zekâsız (şablon + özel okuyucu) okuma, isabet oranı (%),
    yapay zekâya gidenlerin neden dağılımı, öğrenme denemelerinin sonuçları ve ölçümün başladığı gün."""
    from app import system

    since = (date.today() - timedelta(days=days)).isoformat()
    with system.connect() as c:
        _init(c)
        rows = [dict(r) for r in c.execute(
            "SELECT doc, outcome, reason, SUM(n) n, MIN(day) first FROM template_events WHERE day >= ? "
            "GROUP BY doc, outcome, reason", (since,))]
    out = {}
    for d in DOCS:
        mine = [r for r in rows if r["doc"] == d]
        reads = [r for r in mine if r["outcome"] != "learn"]
        total = sum(r["n"] for r in reads)
        free = sum(r["n"] for r in reads if r["outcome"] in FREE)
        by = {o: sum(r["n"] for r in reads if r["outcome"] == o) for o in OUTCOMES if o != "learn"}
        miss = {}
        for r in reads:
            if r["outcome"] not in FREE:
                miss[r["reason"]] = miss.get(r["reason"], 0) + r["n"]
        learn = {r["reason"]: r["n"] for r in mine if r["outcome"] == "learn"}
        out[d] = {"reads": total, "free": free, "rate": round(100 * free / total) if total else None,
                  "by_outcome": by, "miss_reasons": dict(sorted(miss.items(), key=lambda x: -x[1])),
                  "learn": dict(sorted(learn.items(), key=lambda x: -x[1])),
                  "since": min((r["first"] for r in mine), default=None)}
    return {"days": days, "since": min((r["first"] for r in rows), default=None), **out}
