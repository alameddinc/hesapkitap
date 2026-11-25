"""Keşif koleksiyonu (mobil Özet'teki Başlarken kartının yerine): 12 keşfin durumu ve görülen kutlamalar.

Durum yeni bir kayıt tutmaz; defterdeki mevcut veriden türetilir (GET /api/kesif). Ortak defter üyesinde defterin
üyeye görünen kısmı sayılır (book.entry_sql / filter_statements). "Görülen" liste kişiye aittir (defter değil):
sistem veritabanında kesif_seen; yeniden kurulumda konfeti tekrar oynamasın diye.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app import book, db, system

IDS = ("harcama", "maas", "belge", "posta", "banka", "kumbara", "butce", "aile", "borc", "varlik", "liste", "zekat")

_SEEN_SQL = """CREATE TABLE IF NOT EXISTS kesif_seen (
    user_id INTEGER NOT NULL,
    id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
)"""


def _uid() -> int:
    u = db.current_user()
    return int(u["id"]) if u else 0  # tek kullanıcılı kurulum (oturum yok): 0


def _has_table(c, name: str) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone())


def _first(c, table: str, where: str = "1", ts: str | None = "created_at", args: tuple = ()) -> tuple[bool, str | None]:
    """(en az bir satır var mı, ilk satırın zamanı). Tablo yoksa (tembel kurulan tablolar) (False, None)."""
    if not _has_table(c, table.split()[0]):
        return False, None
    if ts:
        r = c.execute(f"SELECT COUNT(*), MIN({ts}) FROM {table} WHERE {where}", args).fetchone()
        return bool(r[0]), r[1]
    return bool(c.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", args).fetchone()), None


def _any(*parts: tuple[bool, str | None]) -> tuple[bool, str | None]:
    done = any(p[0] for p in parts)
    times = [p[1] for p in parts if p[0] and p[1]]
    return done, (min(times) if times else None)


def _visible_statements(c) -> list[dict]:
    return book.filter_statements([dict(r) for r in c.execute("SELECT id, card_no, uploaded_at FROM statements")])


def _family(u: dict | None) -> bool:
    """Ortak defter: üyesi olunan ya da en az bir üyesi olan defter."""
    if not u:
        return False
    if u.get("member_of"):
        return True
    return bool(system.members(u["id"]))


def _mail(u: dict | None) -> tuple[tuple[bool, str | None], tuple[bool, str | None]]:
    """(posta, banka). posta: kişisel adres oluşturuldu ya da posta geldi. banka: yönlendirilen bir banka postası işlendi."""
    with db.connect() as c:
        any_mail = _first(c, "mail_log", ts="processed_at")
        bank_mail = _first(c, "mail_log", "kind IN ('pdf', 'bildirim') AND status = 'ok'", ts="processed_at")
    addr: tuple[bool, str | None] = (False, None)
    if u:
        from app import inbound

        bid = inbound._book_user(u)
        with system.connect() as sc:
            addr = _any(_first(sc, "inbound", "user_id = ? AND revoked = 0", args=(bid,)),
                        _first(sc, "mail_names", "user_id = ? AND released_at IS NULL", args=(bid,)))
    return _any(addr, any_mail), bank_mail


def status() -> dict:
    u = db.current_user()
    esql, eargs = book.entry_sql("e")
    out: dict[str, tuple[bool, str | None]] = {}
    with db.connect() as c:
        stmts = _visible_statements(c)
        sids = [s["id"] for s in stmts]
        card_spend: tuple[bool, str | None] = (False, None)
        if sids:
            qs = ",".join("?" * len(sids))
            r = c.execute(f"SELECT MIN(s.uploaded_at) FROM transactions t JOIN statements s ON s.id = t.statement_id "
                          f"WHERE t.statement_id IN ({qs}) AND t.is_deleted = 0 AND t.amount > 0 "
                          f"AND t.txn_type = 'harcama'", sids).fetchone()
            card_spend = (r[0] is not None, r[0])
        # harcama: kaynak fark etmez (fiş, elle, ekstre, döküm, paylaş): gider kaydı ya da karttaki harcama
        out["harcama"] = _any(_first(c, "entries e", f"e.kind = 'gider' AND e.amount > 0 AND e.skipped = 0{esql}",
                                     ts="e.created_at", args=tuple(eargs)), card_spend)
        out["maas"] = _any(_first(c, "recurring", "kind = 'gelir'", ts=None),
                           _first(c, "entries e", f"e.kind = 'gelir' AND e.amount > 0 AND e.skipped = 0{esql}",
                                  ts="e.created_at", args=tuple(eargs)))
        st_times = [s["uploaded_at"] for s in stmts if s["uploaded_at"]]
        out["belge"] = _any((bool(stmts), min(st_times) if st_times else None),
                            _first(c, "bank_documents", ts="uploaded_at"),
                            _first(c, "entries e", f"e.import_hash IS NOT NULL{esql}", ts="e.created_at",
                                   args=tuple(eargs)))
        out["kumbara"] = _first(c, "jars")
        out["butce"] = _first(c, "budgets", ts=None)
        out["borc"] = _first(c, "debts")
        out["varlik"] = _first(c, "assets", "deleted_at IS NULL")
        out["liste"] = _first(c, "shopping_list")
        # zekât sayfası açılınca "Ben" kişisi kendiliğinden kurulur (app/zakat._init): o sayılmaz
        out["zekat"] = _any(_first(c, "zakat_people", "is_self = 0", ts=None), _first(c, "zakat_assets"),
                            _first(c, "zakat_payments", ts="pay_date"), _first(c, "donations", ts="given_date"))
    out["posta"], out["banka"] = _mail(u)
    out["aile"] = (_family(u), None)
    seen = seen_ids()
    items = [{"id": k, "done": bool(out[k][0]), "done_at": out[k][1] if out[k][0] else None, "seen": k in seen}
             for k in IDS]
    return {"items": items, "done": sum(i["done"] for i in items), "total": len(items), "seen": sorted(seen)}


def seen_ids() -> set[str]:
    with system.connect() as c:
        c.execute(_SEEN_SQL)
        return {r[0] for r in c.execute("SELECT id FROM kesif_seen WHERE user_id = ?", (_uid(),))}


def mark_seen(kid: str) -> set[str]:
    """İdempotent: ikinci işaret ilk zamanı değiştirmez."""
    if kid not in IDS:
        raise ValueError("Bilinmeyen keşif")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with system.connect() as c:
        c.execute(_SEEN_SQL)
        c.execute("INSERT OR IGNORE INTO kesif_seen (user_id, id, seen_at) VALUES (?, ?, ?)", (_uid(), kid, now))
    return seen_ids()
