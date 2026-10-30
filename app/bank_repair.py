"""Önceden içe aktarılmış hesap dökümü kayıtlarını, dosyayı yeniden yüklemeden onarır.

Neyi düzeltir (2026-10 canlı bulguları):
- Halkbank şablon okuyucusu açıklamanın sık tekrarlanan devam satırlarını sayfa başlığı sanıp atıyordu
  ('GIDEN FAST - AYŞE ÇELIK TARAFI … Sayfa No:4 / 5'): alıcı kayboluyor, kendi hesabına giden para gider sayılıyordu.
- Hesap sahibinin adı bilinmediğinde (kural tabanlı okuyucular adı vermez) kendi hesabından gelen para gelir sayılıp
  'Ayşe Yılmaz' kişi olarak listeleniyordu.
- Fatura tahsilatları ham banka metniyle kalıyordu; bankanın iptal edip iade ettiği ödeme hem gider hem gelir sayılıyordu.

Nasıl: saklanan döküm dosyaları (bank_documents) yapay zekâsız yeniden okunur; parmak izi (import_hash) aynı olduğundan
kayıtlar birebir bulunur ve YALNIZ açıklama/kişi/ödeme biçimi/not/fatura kurumu güncellenir — tutar, tarih, hash
değişmez, yeniden yüklemede kopya oluşmaz. Kullanıcının elle değiştirdiği kayıtlara (açıklama, içe aktarmanın ürettiği
haliyle aynı değilse) ve borç/eşleşme/bölme bağlantısı olanlara dokunulmaz. Kendi hesapları arası olduğu anlaşılan kayıt
own_transfers'a taşınır, iptal çifti silinir. Dosyası saklanmamış kayıtlar notlarındaki ham metinle sınıflanır.

Çalıştırma (önce yedek!):
    python -m app.bank_repair <kullanıcı-adı>            # kuru çalıştırma: ne değişeceğini yazar
    python -m app.bank_repair <kullanıcı-adı> --apply    # uygular
"""
import re
import sys

from app import db

_EXTRA = re.compile(r"(Telefon|Abone no|Sözleşme no|Tesisat no|Mukavele no|Fatura no|Son ödeme) [^·]{1,40} · ")


def _owners() -> list[str]:
    return [o for o in db.card_holders() + db.owner_names() if o]


def _raw_of(e: dict) -> str | None:
    """Kaydın içe aktarıldığı ham banka metni: not 'Hesap hareketi · <ham>' ya da (açıklama hamsa) açıklama."""
    note = e.get("note") or ""
    if not note.startswith("Hesap hareketi"):
        return None
    rest = note[len("Hesap hareketi"):]
    if rest.startswith(" · "):
        rest = rest[3:]
        # fatura notu: 'Telefon 54… · Fatura no FD… · Son ödeme 30.06.2026 · <ham>'
        while m := _EXTRA.match(rest):
            rest = rest[m.end():]
        return rest or None
    return e["description"] if rest.strip() == "" else None


def _untouched(e: dict) -> bool:
    """Açıklama hâlâ içe aktarmanın ürettiği gibi mi (kullanıcı değiştirmediyse)?"""
    raw = _raw_of(e)
    if raw is None:
        return False
    desc = e["description"] or ""
    if desc in (raw, raw[:120]):
        return True
    cp = e.get("counterparty")
    return bool(cp and (desc == cp or desc.startswith(f"{cp} · ")))


def _linked(c, eid: int) -> bool:
    for q in ("SELECT 1 FROM debts WHERE given_entry_id = ? OR settled_entry_id = ?",
              "SELECT 1 FROM debt_payments WHERE entry_id = ? AND ? IS NOT NULL",
              "SELECT 1 FROM entry_matches WHERE entry_id = ? AND ? IS NOT NULL",
              "SELECT 1 FROM splits WHERE entry_id = ? AND ? IS NOT NULL",
              "SELECT 1 FROM bills WHERE entry_id = ? AND ? IS NOT NULL"):
        try:
            if c.execute(q, (eid, eid)).fetchone():
                return True
        except Exception:  # noqa: BLE001 - eski şemada tablo yoksa
            continue
    return False


def _document_rows(owners: list[str], report: dict) -> dict[str, dict]:
    """Saklanan PDF dökümlerinden hash → satır (yapay zekâsız; okunamayan belge atlanır)."""
    from app import bank_import, bank_pdf
    from app.storage import book_get

    out: dict[str, dict] = {}
    for d in db.list_bank_documents():
        full = db.get_bank_document(d["id"])
        if not d["stored"] or not (full["file_name"] or "").endswith(".pdf"):
            continue
        data = book_get("statements-bank", full["file_name"])
        if not data:
            report["docs_missing"].append(d["filename"])
            continue
        try:
            res = bank_pdf.parse_account_pdf(data, db.pdf_passwords(), allow_ai=False)
        except Exception as e:  # noqa: BLE001
            report["docs_unread"].append(f"{d['filename']}: {e}")
            continue
        if res.get("owner_name") and res["owner_name"] not in owners:
            owners.append(res["owner_name"])
            report["owner_names"].append(res["owner_name"])
        for r in bank_import.finalize(res["rows"], owners):
            out[r["hash"]] = {**r, "bank": res.get("bank") or full.get("bank") or "", "account_id": full.get("account_id")}
        report["docs_read"].append(d["filename"])
    return out


def repair(apply: bool = False) -> dict:
    """Onarım. apply=False: hiçbir şey yazmaz, ne değişeceğini döner."""
    from app import bank_import

    owners = _owners()
    report = {"docs_read": [], "docs_unread": [], "docs_missing": [], "owner_names": [], "updated": [],
              "to_own_transfer": [], "cancelled": [], "skipped_edited": 0, "skipped_linked": 0, "paired": [], "missing": [], "restored": []}
    by_hash = _document_rows(owners, report)
    with db.connect() as c:
        accounts = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM accounts")}
        entries = [dict(r) for r in c.execute(
            "SELECT * FROM entries WHERE import_hash IS NOT NULL AND import_hash NOT LIKE 'dekont:%' "
            "AND import_hash NOT LIKE 'mail:%' AND kind IN ('gelir', 'gider') AND wallet_id IS NULL "
            "AND COALESCE(payment, '') <> 'kredi_karti' AND recurring_id IS NULL")]
        for e in entries:
            row = by_hash.get(e["import_hash"])
            raw = row["description"] if row else _raw_of(e)
            if not raw:
                continue
            signed = e["amount"] if e["kind"] == "gelir" else -e["amount"]
            typ = row["type"] if row else bank_import.classify(raw, signed, owners)
            if not _untouched(e) and e["description"] != bank_import.describe(raw, signed, owners)["description"]:
                report["skipped_edited"] += 1
                continue
            if typ in ("virman", "iptal") and _linked(c, e["id"]):
                report["skipped_linked"] += 1
                continue
            brief = {"id": e["id"], "date": e["entry_date"], "amount": signed, "was": e["description"]}
            if typ == "virman":
                report["to_own_transfer"].append(brief)
                if apply:
                    c.execute("INSERT OR IGNORE INTO own_transfers (transfer_date, amount, account, description, import_hash) "
                              "VALUES (?, ?, ?, ?, ?)", (e["entry_date"], signed, accounts.get(e["account_id"], ""),
                                                         raw[:500], e["import_hash"]))
                    c.execute("DELETE FROM entries WHERE id = ?", (e["id"],))
                continue
            if typ == "iptal":
                report["cancelled"].append(brief)
                if apply:
                    c.execute("DELETE FROM entries WHERE id = ?", (e["id"],))
                continue
            info = bank_import.describe(raw, signed, owners)
            who = db._known_counterparty(info["counterparty"]) if info["counterparty"] else None
            desc = info["description"]
            if who and info["counterparty"] and who != info["counterparty"]:
                desc = who + desc[len(info["counterparty"]):]
            new = {"description": desc, "counterparty": who, "note": info["note"],
                   "bill_provider": info["bill_provider"]}
            if e["kind"] == "gider":
                new["payment"] = "havale" if who else info["payment"]
            changed = {k: v for k, v in new.items() if (e.get(k) or None) != (v or None)}
            if not changed:
                continue
            report["updated"].append({**brief, "now": desc, "counterparty": who, "fields": sorted(changed)})
            if apply:
                c.execute(f"UPDATE entries SET {', '.join(f'{k} = ?' for k in changed)} WHERE id = ?",
                          (*changed.values(), e["id"]))
        # dökümde olup hiç kaydı olmayan hareketler (eski parmak izi çakışmasında 'zaten var' sanılıp atlanmış):
        # dökümü yeniden yükleyince eklenir
        known = {h for (h,) in c.execute("SELECT import_hash FROM entries WHERE import_hash IS NOT NULL UNION "
                                         "SELECT import_hash FROM own_transfers WHERE import_hash IS NOT NULL UNION "
                                         "SELECT import_hash FROM card_payments WHERE import_hash IS NOT NULL")}
        # eski eşleme hatası: başka hesaptan aynı tutarda ÇIKIŞ karşılık sanılıp gerçek gider virmana taşınmış olabilir
        # (Halkbank → VakıfBank 35.000, aynı gün VakıfBank'tan kiraya 35.000): geri alınır, gider olarak eklenir
        wrong = []
        for o in [dict(x) for x in c.execute("SELECT * FROM own_transfers WHERE amount < 0 AND import_hash IS NOT NULL")]:
            r = by_hash.get(o["import_hash"])
            if r and r["type"] == "gider" and not db._pairs_own_transfer(-r["amount"], r["date"], r["description"], owners,
                                                                           o["account"] or ""):
                wrong.append(r)
                if apply:
                    c.execute("DELETE FROM own_transfers WHERE id = ?", (o["id"],))
        report["restored"] = [{"id": None, "date": r["date"], "amount": r["amount"], "was": r["description"]} for r in wrong]
        known -= {r["hash"] for r in wrong}
        report["missing"] = [{"id": None, "date": r["date"], "amount": r["amount"], "was": r["description"]}
                             for h, r in by_hash.items() if h not in known and r["type"] in ("gelir", "gider")
                             and r not in wrong]
        to_add = wrong + [r for h, r in by_hash.items() if h not in known and r["type"] in ("gelir", "gider") and r not in wrong]
        # iptal çiftinin kendi hesabına giden yarısı eskiden own_transfers'a yazılmış olabilir
        for h, r in by_hash.items():
            if r["type"] == "iptal" and c.execute("SELECT 1 FROM own_transfers WHERE import_hash = ?", (h,)).fetchone():
                report["cancelled"].append({"id": None, "date": r["date"], "amount": r["amount"], "was": r["description"]})
                if apply:
                    c.execute("DELETE FROM own_transfers WHERE import_hash = ?", (h,))
    if apply:
        for name in report["owner_names"]:
            db.remember_owner_name(name)
        # eksik / geri alınan satırlar içe aktarmanın kendisiyle eklenir (kişi, kategori, eşleşme kuralları aynı);
        # bakiye verilmez: eski bir satır hesabın güncel bakiyesini ezmesin
        for acc in {r["account_id"] for r in to_add}:
            part = [{**r, "balance": None} for r in to_add if r["account_id"] == acc]
            db.import_bank_rows(part, account=part[0]["bank"], owners=owners, account_id=acc)
        # açıklaması kesik kalmış (dosyası yok) giden kendi transferleri: karşı hesaba giriş artık own_transfers'ta
        report["paired"] = [{"id": m["id"], "date": m["entry_date"], "amount": -m["amount"], "was": m["description"]}
                            for m in db.repair_own_transfers(owners)]
    return report


def _print(rep: dict, apply: bool) -> None:
    print(("UYGULANDI" if apply else "KURU ÇALIŞTIRMA (yazılmadı)"))
    print("okunan dökümler:", len(rep["docs_read"]), "okunamayan:", rep["docs_unread"], "dosyası yok:", rep["docs_missing"])
    print("öğrenilen hesap sahibi adları:", rep["owner_names"])
    for key, title in (("to_own_transfer", "kendi hesapları arası → virman"), ("cancelled", "iptal çifti → silinir"),
                       ("paired", "giden transfer karşılığıyla eşleşti → virman"),
                       ("restored", "yanlışlıkla virman sayılmış gider → geri eklendi"),
                       ("missing", "dökümde var, kaydı yok → eklendi")):
        rows = rep[key]
        print(f"\n{title}: {len(rows)} kayıt, toplam {sum(r['amount'] for r in rows):,.2f}")
        for r in rows:
            print(f"  #{r['id'] or '-'} {r['date']} {r['amount']:>12,.2f}  {(r['was'] or '')[:70]}")
    print(f"\naçıklama/kişi düzeltilen: {len(rep['updated'])}")
    for r in rep["updated"]:
        print(f"  #{r['id']} {r['date']} {r['amount']:>12,.2f}  {(r['was'] or '')[:45]!r} → {r['now']!r} {r['fields']}")
    print(f"\ndokunulmayan: elle değiştirilmiş {rep['skipped_edited']}, bağlantılı {rep['skipped_linked']}")


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    from app import system

    apply = "--apply" in argv
    user = system.user_by_name(argv[0])
    if not user:
        print("kullanıcı yok:", argv[0])
        return 1
    with db.use_space(system.user_db_path(user), user):
        if not apply:
            # kuru çalıştırmada repair_own_transfers yazmadan ne yapacağını göremeyiz: geçici kopyada dene
            import os
            import sqlite3
            import tempfile

            scope = db.files_scope()  # dökümler asıl defterin dosya alanından okunur
            tmp = os.path.join(tempfile.mkdtemp(), "kopya.db")
            with sqlite3.connect(db.db_path()) as src, sqlite3.connect(tmp) as dst:
                src.backup(dst)
            with db.use_space(tmp, user):
                db._space.get()["files"] = scope
                rep = repair(apply=True)
            os.remove(tmp)
            _print(rep, False)
            return 0
        _print(repair(apply=True), True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
