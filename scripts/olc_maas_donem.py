"""SALT OKUMA ölçüm: maaş kaydı yanlış döneme bağlanmış mı (hafta sonu/bayram yüzünden ay dönümünü aşan geç maaş).

fix/kayma-is-gunu öncesi link_salary_entries dökümden gelen maaşı tarihinin takvim dönemine (period_of) bağlıyordu;
ay dönümü 17, maaş 15'i Cumartesi → 17'sinde yatan maaş SONRAKİ döneme gidiyordu (o dönem 2 maaş, önceki dönem
"tahmini" maaş). Bu betik hiçbir şey yazmaz: veritabanını belleğe kopyalar (sqlite backup, kaynak mode=ro) ve her
maaş kalemi için bağlı kayıtların bugünkü dönemi ile yeni kuralın (db._nearest_recurring_month, tarih sırasıyla,
her dönem bir kez) vereceği dönemi karşılaştırır. Çıktıda tutar/açıklama yok: yalnız kayıt id, tarih, dönem.

Kullanım (sunucuda, uygulama kökünde):
    python scripts/olc_maas_donem.py data/ekstre.db data/users/*/ekstre.db
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402  (yalnız saf yardımcılar: period_of, occurrence_date, day_at, _is_salary_recurring)


def _load(path: str) -> sqlite3.Connection:
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    src.close()
    mem.row_factory = sqlite3.Row
    return mem


def _months_around(p: str) -> list[str]:
    y, m = map(int, p.split("-"))
    return [f"{(y * 12 + m - 1 + d) // 12:04d}-{(y * 12 + m - 1 + d) % 12 + 1:02d}" for d in (-1, 0, 1)]


def measure(path: str) -> dict:
    c = _load(path)
    try:
        row = c.execute("SELECT value FROM settings WHERE key = 'cycle_start_day'").fetchone()
    except sqlite3.OperationalError:
        return {"path": path, "skip": "şema yok"}
    cycle = int(row[0]) if row and str(row[0]).isdigit() else 1
    recs = [dict(r) for r in c.execute("SELECT * FROM recurring") if db._is_salary_recurring(dict(r))]
    out = {"path": path, "cycle": cycle, "salary_items": len(recs), "moves": [], "double_periods": 0}
    for rec in recs:
        try:
            changes = [dict(x) for x in c.execute(
                "SELECT * FROM salary_changes WHERE recurring_id = ? ORDER BY from_month, id", (rec["id"],))]
        except sqlite3.OperationalError:
            changes = []
        es = [dict(e) for e in c.execute(
            "SELECT id, entry_date, month FROM entries WHERE recurring_id = ? AND kind = 'gelir' AND skipped = 0 "
            "ORDER BY entry_date, amount DESC", (rec["id"],))]
        per: dict[str, int] = {}
        for e in es:
            per[e["month"]] = per.get(e["month"], 0) + 1
        out["double_periods"] += sum(1 for v in per.values() if v > 1)
        taken: set[str] = set()
        for e in es:
            d = date.fromisoformat(e["entry_date"][:10])
            cands = [m for m in _months_around(db.period_of(d, cycle)) if m not in taken]
            if not cands:
                continue

            def gap(m: str) -> int:
                occ = date.fromisoformat(db.occurrence_date(m, db.day_at(rec, m, changes), cycle))
                return abs((occ - d).days)

            best = min(cands, key=gap)
            taken.add(best)
            if best != e["month"]:
                out["moves"].append({"recurring_id": rec["id"], "entry_id": e["id"], "date": e["entry_date"][:10],
                                     "now": e["month"], "new": best})
    c.close()
    return out


def main(paths: list[str]) -> None:
    total = 0
    for p in paths:
        r = measure(p)
        if r.get("skip"):
            print(f"{p}: atlandı ({r['skip']})")
            continue
        total += len(r["moves"])
        print(f"{p}: ay dönümü {r['cycle']}, maaş kalemi {r['salary_items']}, aynı dönemde 2+ maaş: "
              f"{r['double_periods']}, dönemi değişecek kayıt: {len(r['moves'])}")
        for m in r["moves"]:
            print(f"    kalem {m['recurring_id']} kayıt {m['entry_id']} {m['date']}: {m['now']} → {m['new']}")
    print(f"TOPLAM dönemi değişecek kayıt: {total}")


if __name__ == "__main__":
    main(sys.argv[1:])
