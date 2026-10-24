"""Yüklemeyi geri al: bir ekstre / döküm / dekont yüklemesinin veritabanında yaptığı her şeyi tersine çevirir.

Yüklemeden önce kullanıcının veritabanının anlık kopyası alınır; yükleme bitince iki hal satır satır karşılaştırılır ve
fark (eklenen, değişen, silinen satırlar) `import_batches` tablosunda saklanır. Kopya hemen silinir. Geri alırken sadece
bu fark tersine çevrilir: yüklemeden sonra yapılan başka işler (elle kayıt, başka bir yükleme) bozulmaz. Yüklemenin
dokunduğu bir satır sonradan değiştirildiyse geri alma önce bunu söyler; kullanıcı yine de isterse uygular.
"""
import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager

from app import db

KEEP = 30  # saklanan son yükleme sayısı
SKIP = {"import_batches", "settings"}  # ayarlar (ör. kayıtlı banka biçimi, şifre) geri alınmaz

SCHEMA = """CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    label TEXT,
    summary TEXT,
    changes TEXT NOT NULL,
    undone_at TEXT
)"""


def _tables(c) -> list[str]:
    return [r[0] for r in c.execute("SELECT name FROM main.sqlite_master WHERE type = 'table' "
                                    "AND name NOT LIKE 'sqlite_%'") if r[0] not in SKIP]


def _pk(c, table: str) -> list[str]:
    cols = sorted((r["pk"], r["name"]) for r in c.execute(f"PRAGMA main.table_info({table})") if r["pk"])
    return [n for _, n in cols] or ["rowid"]


def _cols(c, schema: str, table: str) -> list[str]:
    return [r["name"] for r in c.execute(f"PRAGMA {schema}.table_info({table})")]


def _snapshot_path(bid: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db.db_path())), f".undo-{bid}.db")


def _q(name: str) -> str:
    return "rowid" if name == "__rowid" else f'"{name}"'


def _diff(c, pre: str) -> dict:
    """{tablo: {pk, cols, before: [satır], after: [satır]}}: yalnız değişen satırlar (önce/sonra halleri)."""
    out = {}
    for t in _tables(c):
        cols = _cols(c, "main", t)
        pre_cols = _cols(c, pre, t)
        if pre_cols and pre_cols != cols:
            continue  # şema değiştiyse (göç) karşılaştırma anlamsız
        pk = _pk(c, t)
        names = (["__rowid"] if pk == ["rowid"] else []) + cols
        q = f"SELECT {', '.join(map(_q, names))} FROM {{s}}.\"{t}\""
        if pre_cols:
            after = [list(r) for r in c.execute(f"{q.format(s='main')} EXCEPT {q.format(s=pre)}")]
            before = [list(r) for r in c.execute(f"{q.format(s=pre)} EXCEPT {q.format(s='main')}")]
        else:  # yükleme sırasında oluşan tablo: tamamı yeni
            after, before = [list(r) for r in c.execute(q.format(s="main"))], []
        if after or before:
            out[t] = {"pk": ["__rowid"] if pk == ["rowid"] else pk, "cols": names, "before": before, "after": after}
    return out


@contextmanager
def batch(label: str):
    """with undo.batch("Ekstre: x.pdf") as b: ...; b["summary"] = {...}  → b["id"] (değişiklik yoksa None)."""
    ensure()
    bid = uuid.uuid4().hex[:12]
    snap = _snapshot_path(bid)
    src = sqlite3.connect(db.db_path())
    dst = sqlite3.connect(snap)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    info = {"id": None, "summary": None}
    try:
        yield info
    finally:  # yarıda hata verse de o ana kadar yazılanlar geri alınabilsin
        try:
            _record(bid, snap, label, info)
        except Exception:  # noqa: BLE001 - geri alma kaydı yüklemeyi asla bozmasın
            logging.getLogger(__name__).exception("geri alma kaydı yazılamadı")
        try:
            os.remove(snap)
        except OSError:
            pass


def _record(bid: str, snap: str, label: str, info: dict) -> None:
    with db.connect() as c:
        c.execute("ATTACH DATABASE ? AS pre", (snap,))
        changes = _diff(c, "pre")
        c.commit()
        c.execute("DETACH DATABASE pre")
        if changes:
            c.execute("INSERT INTO import_batches (id, label, summary, changes) VALUES (?, ?, ?, ?)",
                      (bid, label, json.dumps(info["summary"], ensure_ascii=False, default=str),
                       json.dumps(changes, ensure_ascii=False, default=str)))
            c.execute("DELETE FROM import_batches WHERE id NOT IN "
                      "(SELECT id FROM import_batches ORDER BY created_at DESC, rowid DESC LIMIT ?)", (KEEP,))
            info["id"] = bid


def ensure() -> None:
    with db.connect() as c:
        c.execute(SCHEMA)


def _key(row: list, cols: list[str], pk: list[str]) -> tuple:
    return tuple(row[cols.index(k)] for k in pk)


def _counts(changes: dict) -> dict:
    out = {}
    for t, ch in changes.items():
        b = {_key(r, ch["cols"], ch["pk"]) for r in ch["before"]}
        a = {_key(r, ch["cols"], ch["pk"]) for r in ch["after"]}
        out[t] = {"added": len(a - b), "changed": len(a & b), "removed": len(b - a)}
    return out


def list_batches(limit: int = 15) -> list[dict]:
    ensure()
    with db.connect() as c:
        rs = db.rows(c.execute("SELECT id, created_at, label, summary, changes, undone_at FROM import_batches "
                               "ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)))
    for r in rs:
        r["summary"] = json.loads(r["summary"] or "null")
        r["counts"] = _counts(json.loads(r.pop("changes")))
    return rs


def _where(pk: list[str]) -> str:
    return " AND ".join(f"{_q(k)} IS ?" for k in pk)


def conflicts(c, changes: dict) -> list[str]:
    """Yüklemeden sonra elle değiştirilmiş satırlar (geri alınca o değişiklik de gider)."""
    out = []
    for t, ch in changes.items():
        cols, pk = ch["cols"], ch["pk"]
        sel = ", ".join(map(_q, cols))
        for row in ch["after"]:
            cur = c.execute(f"SELECT {sel} FROM \"{t}\" WHERE {_where(pk)}", _key(row, cols, pk)).fetchone()
            if cur is not None and [*cur] != row:
                out.append(f"{t}#{'/'.join(map(str, _key(row, cols, pk)))}")
    return out


def undo(bid: str, force: bool = False) -> dict:
    ensure()
    with db.connect() as c:
        r = c.execute("SELECT changes, undone_at FROM import_batches WHERE id = ?", (bid,)).fetchone()
        if not r:
            raise KeyError(bid)
        if r["undone_at"]:
            return {"ok": True, "already": True}
        changes = json.loads(r["changes"])
        conf = conflicts(c, changes)
        if conf and not force:
            return {"ok": False, "conflicts": conf}
        c.execute("PRAGMA foreign_keys = OFF")
        c.commit()
        for t, ch in changes.items():
            cols, pk = ch["cols"], ch["pk"]
            before = {_key(x, cols, pk) for x in ch["before"]}
            for row in ch["after"]:  # yüklemenin eklediği satırları sil (değiştirdiklerini aşağıda eski haline yaz)
                if _key(row, cols, pk) not in before:
                    c.execute(f"DELETE FROM \"{t}\" WHERE {_where(pk)}", _key(row, cols, pk))
            real = list(map(_q, cols))
            for row in ch["before"]:
                c.execute(f"INSERT OR REPLACE INTO \"{t}\" ({', '.join(real)}) VALUES ({', '.join('?' * len(real))})", row)
        c.execute("UPDATE import_batches SET undone_at = CURRENT_TIMESTAMP WHERE id = ?", (bid,))
        c.commit()
        c.execute("PRAGMA foreign_keys = ON")
    return {"ok": True, "counts": _counts(changes)}
