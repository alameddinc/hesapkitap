"""SALT OKUMA ölçüm: maaş dönem ataması göçünün (db.salary_funding_periods, _once "salary_funding_v1") etkisi.

Kullanıcı kararı 17.11: maaş dönüm gününden en çok 5 takvim günü önce yatıyorsa SONRAKİ dönemin geliridir (ay dönümü 17,
maaş 15 → 15 Kas maaşı Kasım dönemi 17 Kas – 16 Ara, anahtar 2026-12). Eski kayıtlarda bu maaşlar bir önceki döneme
bağlı; göç onları (ve kalemin dönem anahtarlı verisini: zam/iş değişikliği, aya özel tutar, tutar değişikliği,
başlangıç/bitiş dönemi) bir dönem ileri alır.

Bu betik canlı veritabanına HİÇ yazmaz: kaynağı mode=ro açar, geçici bir dosyaya kopyalar (sqlite backup), göçü YALNIZ
o kopyada uygulamanın kendi fonksiyonuyla çalıştırır ve önce/sonra farkını yazar; kopya silinir. Böylece ölçülen, canlıda
çalışacak olanın ta kendisidir. Çıktıda tutar/açıklama yok: kalem id, kayıt id, tarih, dönem.

Kullanım (sunucuda, uygulama kökünde):
    python scripts/olc_maas_fonlama.py data/ekstre.db data/users/*/ekstre.db
    python scripts/olc_maas_fonlama.py -v data/users/*/ekstre.db      # kayıt kayıt döküm
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402

FLAG = "salary_funding_v1"


def _snapshot(path: str) -> dict:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        out = {
            "entries": {r["id"]: (r["recurring_id"], r["entry_date"][:10], r["month"], r["skipped"]) for r in c.execute(
                "SELECT id, recurring_id, entry_date, month, skipped FROM entries WHERE recurring_id IS NOT NULL")},
            "salary_changes": {r["id"]: (r["recurring_id"], r["from_month"]) for r in c.execute(
                "SELECT id, recurring_id, from_month FROM salary_changes")},
            "recurring_changes": {r["id"]: (r["recurring_id"], r["from_month"]) for r in c.execute(
                "SELECT id, recurring_id, from_month FROM recurring_changes")},
            # tutar yalnız eşleştirme için (ay değişti mi); yazdırılmaz
            "overrides": sorted((r["recurring_id"], r["month"], r["amount"]) for r in c.execute(
                "SELECT recurring_id, month, amount FROM recurring_overrides")),
            "recurring": {r["id"]: (r["start_month"], r["end_month"]) for r in c.execute(
                "SELECT id, start_month, end_month FROM recurring")},
        }
        row = c.execute("SELECT value FROM settings WHERE key = 'cycle_start_day'").fetchone()
        out["cycle"] = int(row[0]) if row and str(row[0]).isdigit() else 1
        out["done"] = bool(c.execute("SELECT 1 FROM settings WHERE key = ? AND value = '1'", (FLAG,)).fetchone())
    finally:
        c.close()
    return out


def _doubles(entries: dict) -> int:
    """Aynı kalemin aynı dönemine bağlı 2+ (atlanmamış) kayıt."""
    seen: dict[tuple, int] = {}
    for rid, _d, m, skipped in entries.values():
        if not skipped:
            seen[(rid, m)] = seen.get((rid, m), 0) + 1
    return sum(1 for v in seen.values() if v > 1)


def measure(path: str) -> dict:
    try:
        before = _snapshot(path)
    except sqlite3.OperationalError as e:
        return {"path": path, "skip": f"şema yok ({e})"}
    if before["done"]:
        return {"path": path, "skip": "göç zaten uygulanmış"}
    tmpdir = tempfile.mkdtemp(prefix="olc_maas_fonlama_")
    try:
        tmp = str(Path(tmpdir) / "kopya.db")
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        src.close()
        dst.close()
        old = db.DB_PATH
        db.DB_PATH = tmp  # yalnız kopya: uygulamanın göç fonksiyonu onu yazar
        try:
            db.salary_funding_periods()
        finally:
            db.DB_PATH = old
        after = _snapshot(tmp)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    moves = [{"entry_id": i, "recurring_id": b[0], "date": b[1], "skipped": bool(b[3]), "now": b[2],
              "new": after["entries"][i][2]}
             for i, b in sorted(before["entries"].items()) if after["entries"].get(i, b)[2] != b[2]]
    sc = [(i, b, after["salary_changes"][i][1]) for i, b in sorted(before["salary_changes"].items())
          if after["salary_changes"][i][1] != b[1]]
    rc = [(i, b, after["recurring_changes"][i][1]) for i, b in sorted(before["recurring_changes"].items())
          if after["recurring_changes"][i][1] != b[1]]
    rec = [(i, b, after["recurring"][i]) for i, b in sorted(before["recurring"].items()) if after["recurring"][i] != b]
    ov_moved = len(set(before["overrides"]) - set(after["overrides"]))
    return {"path": path, "cycle": before["cycle"], "moves": moves, "salary_changes": sc, "recurring_changes": rc,
            "recurring": rec, "overrides": ov_moved, "doubles_before": _doubles(before["entries"]),
            "doubles_after": _doubles(after["entries"])}


def main(args: list[str]) -> None:
    verbose = "-v" in args
    paths = [a for a in args if a != "-v"]
    tot = {"kayit": 0, "defter": 0, "kalem": 0, "zam": 0, "ozel_tutar": 0}
    for p in paths:
        r = measure(p)
        if r.get("skip"):
            print(f"{p}: atlandı ({r['skip']})")
            continue
        n = len(r["moves"])
        tot["kayit"] += n
        tot["defter"] += bool(n or r["recurring"] or r["salary_changes"] or r["overrides"])
        tot["kalem"] += len(r["recurring"])
        tot["zam"] += len(r["salary_changes"]) + len(r["recurring_changes"])
        tot["ozel_tutar"] += r["overrides"]
        print(f"{p}: ay dönümü {r['cycle']}, dönemi değişecek maaş kaydı {n} "
              f"(atlanan {sum(m['skipped'] for m in r['moves'])}), kalem başlangıç/bitiş {len(r['recurring'])}, "
              f"zam/iş değişikliği {len(r['salary_changes'])}, tutar değişikliği {len(r['recurring_changes'])}, "
              f"aya özel tutar {r['overrides']}; aynı dönemde 2+ maaş önce {r['doubles_before']} → sonra "
              f"{r['doubles_after']}")
        if verbose:
            for m in r["moves"]:
                print(f"    kalem {m['recurring_id']} kayıt {m['entry_id']} {m['date']}"
                      f"{' (atlandı)' if m['skipped'] else ''}: {m['now']} → {m['new']}")
            for i, b, a in r["recurring"]:
                print(f"    kalem {i} başlangıç/bitiş {b} → {a}")
            for i, b, a in r["salary_changes"]:
                print(f"    kalem {b[0]} maaş değişikliği {i}: {b[1]} → {a}")
    print(f"TOPLAM: etkilenen defter {tot['defter']}, dönemi değişecek maaş kaydı {tot['kayit']}, kalem "
          f"{tot['kalem']}, zam/tutar değişikliği {tot['zam']}, aya özel tutar {tot['ozel_tutar']}")


if __name__ == "__main__":
    main(sys.argv[1:])
