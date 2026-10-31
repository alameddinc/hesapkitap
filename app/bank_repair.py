"""Önceden içe aktarılmış hesap dökümü kayıtlarını, dosyayı yeniden yüklemeden onarır.

Neyi düzeltir (2026-10 canlı bulguları):
- Halkbank şablon okuyucusu açıklamanın sık tekrarlanan devam satırlarını sayfa başlığı sanıp atıyordu
  ('GIDEN FAST - AYŞE YILMAZ TARAFI … Sayfa No:4 / 5'): alıcı kayboluyor, kendi hesabına giden para gider sayılıyordu.
- Hesap sahibinin adı bilinmediğinde (kural tabanlı okuyucular adı vermez) kendi hesabından gelen para gelir sayılıp
  'Ayşe Yılmaz' kişi olarak listeleniyordu.
- Fatura tahsilatları ham banka metniyle kalıyordu; bankanın iptal edip iade ettiği ödeme hem gider hem gelir sayılıyordu.

Nasıl: saklanan döküm dosyaları (bank_documents) yapay zekâsız yeniden okunur; parmak izi (import_hash) aynı olduğundan
kayıtlar birebir bulunur ve YALNIZ açıklama/kişi/ödeme biçimi/not/fatura kurumu güncellenir — tutar, tarih, hash
değişmez, yeniden yüklemede kopya oluşmaz. Kullanıcının elle değiştirdiği kayıtlara (açıklama, içe aktarmanın ürettiği
haliyle aynı değilse) ve borç/eşleşme/bölme bağlantısı olanlara dokunulmaz. Kendi hesapları arası olduğu anlaşılan kayıt
own_transfers'a taşınır, iptal çifti silinir. Dosyası saklanmamış kayıtlar notlarındaki ham metinle sınıflanır.

Denetim (2026-10-31, M2) düzeltmeleri de buradan onarılır:
- bakiyesiz Excel/CSV'de aynı gün aynı tutarlı eş satırın kaybolanı ("missing"; artık Excel/CSV dökümleri de okunur),
- aynı dönem PDF ve Excel olarak iki kez yüklenmiş hareketler ("duplicates": sonra yüklenen belgenin fazlası silinir;
  elle değiştirilmiş ya da bağlantılı kayda dokunulmaz),
- yeniden eskiye sıralı dökümde günün ilk hareketinden alınmış hesap bakiyesi ("anchors"),
- virmanlara hesap bilgisi (own_transfers.account_id). Dökümden gelen virman zaten çapanın içinde: bakiye değişmez
  ("transfer_accounts"). Yalnız elle ya da e-posta bildirimiyle gelmiş ve hesabın çapasından SONRAKİ virman bakiyeye
  girer ("transfer_balance", bakiye etkisiyle). Raporda her hesabın bakiyesi onarım öncesi/sonrası ("balances").

Çalıştırma (önce yedek!):
    python -m app.bank_repair <kullanıcı-adı>            # kuru çalıştırma: ne değişeceğini yazar
    python -m app.bank_repair <kullanıcı-adı> --apply    # uygular
"""
import re
import sys

from app import db
from app import rawnote
from app.money import eq as money_eq, ne as money_ne

_EXTRA = re.compile(r"(Telefon|Abone no|Sözleşme no|Tesisat no|Mukavele no|Fatura no|Son ödeme) [^·]{1,40} · ")


def _owners() -> list[str]:
    """Kendisi = hesap dökümlerinin sahibi (içe aktarmadaki gibi). Kart sahipleri yalnız döküm sahibi hiç bilinmiyorsa:
    ek kart sahibi (eş) kişi olarak kalmalı — yoksa 'Zeynep Yılmaz'e giden havale kişisiz kalır."""
    return [o for o in db.owner_names() if o] or [o for o in db.card_holders() if o]


def _raw_of(e: dict) -> str | None:
    """Kaydın içe aktarıldığı ham banka metni (app/rawnote.py)."""
    from app import rawnote

    return rawnote.legacy_raw(e.get("note"), e.get("description"))


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


def _read_document(d: dict, full: dict, owners: list[str], report: dict) -> list[dict] | None:
    """Saklanan dökümü yapay zekâsız yeniden oku: PDF, Excel ya da CSV (kayıtlı sütun eşlemesiyle). Satırlar."""
    import json

    from app import bank_import, bank_pdf
    from app.storage import book_get

    name = (full["file_name"] or "").lower()
    if not d["stored"] or not name.endswith((".pdf", ".xlsx", ".xls", ".csv")):
        return None
    data = book_get("statements-bank", full["file_name"])
    if not data:
        report["docs_missing"].append(d["filename"])
        return None
    try:
        if name.endswith(".pdf"):
            res = bank_pdf.parse_account_pdf(data, db.pdf_passwords(), allow_ai=False)
            if res.get("owner_name") and res["owner_name"] not in owners:
                owners.append(res["owner_name"])
                report["owner_names"].append(res["owner_name"])
            rows = bank_import.finalize(res["rows"], [res["owner_name"]] if res.get("owner_name") else owners)
            bank = res.get("bank") or full.get("bank") or ""
        else:
            from app.replay_check import _profile_key

            raw = bank_import.read_rows(data, d["filename"] or full["file_name"])
            det = bank_import.detect(raw)
            saved = db.get_setting(_profile_key(det["columns"]))
            rows = bank_import.parse(raw, json.loads(saved) if saved else det["mapping"], det["header_row"], owners)
            bank = full.get("bank") or ""
    except Exception as e:  # noqa: BLE001
        report["docs_unread"].append(f"{d['filename']}: {e}")
        return None
    report["docs_read"].append(d["filename"])
    return [{**r, "bank": bank, "account_id": full.get("account_id"), "doc_id": d["id"]} for r in rows]


def _document_rows(owners: list[str], report: dict) -> tuple[dict[str, dict], list[tuple[dict, list[dict]]]]:
    """Saklanan dökümlerden hash → satır (eski biçim parmak izi alt_hash ile de) ve belge başı satırlar (yükleme
    sırasıyla). Okunamayan belge atlanır."""
    out: dict[str, dict] = {}
    docs = []
    for d in sorted(db.list_bank_documents(), key=lambda x: x["id"]):
        full = db.get_bank_document(d["id"])
        rows = _read_document(d, full, owners, report)
        if rows is None:
            continue
        docs.append((full, rows))
        for r in rows:
            out[r["hash"]] = r
            if r.get("alt_hash"):
                out.setdefault(r["alt_hash"], r)
    return out, docs


def _signed_records(c, acc_id: int, account: str) -> list[dict]:
    """Hesabın dökümden gelmiş kayıtları (kayıt, gün, işaretli kuruş, parmak izi)."""
    from app import money

    doc = "import_hash IS NOT NULL AND import_hash NOT LIKE 'mail:%' AND import_hash NOT LIKE 'dekont:%'"
    out = [{"table": "entries", "id": r[0], "date": r[1], "k": money.kurus(r[3] if r[2] == "gelir" else -r[3]),
            "hash": r[4], "desc": r[5]}
           for r in c.execute(f"SELECT id, entry_date, kind, amount, import_hash, description FROM entries "
                              f"WHERE account_id = ? AND {doc}", (acc_id,))]
    out += [{"table": "card_payments", "id": r[0], "date": r[1], "k": money.kurus(-r[2]), "hash": r[3], "desc": r[4]}
            for r in c.execute(f"SELECT id, pay_date, amount, import_hash, description FROM card_payments "
                               f"WHERE account_id = ? AND {doc}", (acc_id,))]
    out += [{"table": "own_transfers", "id": r[0], "date": r[1], "k": money.kurus(r[2]), "hash": r[3], "desc": r[4]}
            for r in c.execute(f"SELECT id, transfer_date, amount, import_hash, description FROM own_transfers "
                               f"WHERE {doc} AND (account_id = ? OR (account_id IS NULL AND account = ?))", (acc_id, account))]
    return out


def _duplicates(c, docs: list[tuple[dict, list[dict]]]) -> list[dict]:
    """Aynı hesabın aynı hareketi iki belgeden (PDF + Excel) iki kez kayıtlı: o gün o tutarda, hiçbir belgede
    olmadığı kadar çok kayıt. Fazlası: en son yüklenen belgeye ait olanlar (ilk belgeninki kalır)."""
    from collections import Counter, defaultdict

    from app import money

    by_acc = defaultdict(list)
    for full, rows in docs:
        if full.get("account_id"):
            by_acc[full["account_id"]].append((full, rows))
    out = []
    for acc_id, items in by_acc.items():
        if len(items) < 2:
            continue
        max_in_doc: Counter = Counter()
        owner_doc: dict[str, int] = {}
        for full, rows in items:
            cnt = Counter((r["date"], money.kurus(r["amount"])) for r in rows)
            for k, n in cnt.items():
                max_in_doc[k] = max(max_in_doc[k], n)
            for r in rows:
                for h in (r["hash"], r.get("alt_hash")):
                    if h:
                        owner_doc.setdefault(h, full["id"])
        recs = [r for r in _signed_records(c, acc_id, items[0][0].get("bank") or "") if r["hash"] in owner_doc]
        groups = defaultdict(list)
        for r in recs:
            groups[(r["date"], r["k"])].append(r)
        for k, rs in groups.items():
            extra = len(rs) - max_in_doc.get(k, 0)
            if extra <= 0 or len({owner_doc[r["hash"]] for r in rs}) < 2:
                continue
            rs.sort(key=lambda r: (owner_doc[r["hash"]], r["id"]))  # ilk yüklenen belgeninki önde: o kalır
            for r in rs[-extra:]:
                out.append({**r, "doc_id": owner_doc[r["hash"]], "amount": r["k"] / 100})
    return out


_BANKISH = re.compile(r"\b(FAST|EFT|HAVALE|POS|REF|SORGU|IBAN|TR\d{2}|ODEME|ÖDEME|TAHSILAT|VIRMAN|ATM|KART|GELEN|GIDEN|"
                      r"GİDEN|HESAB|NOLU|NO'LU|PARA|MASRAF|KOMISYON|AIDAT|SATIS|ALIS)\b|\d{6,}", re.I)


def _looks_bank_text(desc: str) -> bool:
    """Açıklama hâlâ bankanın ham metni mi (kullanıcı değiştirmemiş)? Büyük harf ağırlıklı ve banka sözcükleri/uzun
    numara içeriyor."""
    letters = [ch for ch in desc or "" if ch.isalpha()]
    upper = sum(ch.isupper() for ch in letters) / len(letters) if letters else 0
    return bool(_BANKISH.search(desc or "")) and upper >= 0.6


def _raw_notes(by_hash: dict, report: dict, apply: bool) -> None:
    """Eski kayıtlar: içe aktarma açıklama ham metinle aynıysa notuna yalnız "Hesap hareketi" yazıyordu; kullanıcı
    açıklamayı düzeltince banka açıklaması kayboluyordu. Açıklama değişmemişse ham metin notun ilk satırına geri
    yazılır (kullanıcı notu altında korunur). Değişmiş ya da emin olunamıyorsa dokunulmaz (raw null kalır)."""
    report.update(raw_notes=[], raw_unknown=[])
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, entry_date, amount, kind, description, note, import_hash FROM entries "
            "WHERE import_hash IS NOT NULL AND (note = 'Hesap hareketi' OR note LIKE 'Hesap hareketi' || char(10) || '%')")]
        for e in rows:
            signed = e["amount"] if e["kind"] == "gelir" else -e["amount"]
            doc = by_hash.get(e["import_hash"])
            desc = e["description"] or ""
            if doc:
                raw = doc["description"]
                same = desc in (raw, raw[:120])
            else:
                raw, same = desc, _looks_bank_text(desc)
            brief = {"id": e["id"], "date": e["entry_date"], "amount": signed, "was": desc}
            if not same:
                report["raw_unknown"].append({**brief, "why": "açıklama değiştirilmiş ya da ham metin bilinmiyor"})
                continue
            report["raw_notes"].append({**brief, "now": raw[:60], "why": "döküm dosyasından" if doc else "açıklamadan"})
            if apply:
                user = rawnote.user_note(e["note"])
                c.execute("UPDATE entries SET note = ? WHERE id = ?",
                          (f"Hesap hareketi · {raw}" + (f"\n{user}" if user else ""), e["id"]))


def repair(apply: bool = False) -> dict:
    """Onarım. apply=False: hiçbir şey yazmaz, ne değişeceğini döner."""
    from app import bank_import

    owners = _owners()
    report = {"docs_read": [], "docs_unread": [], "docs_missing": [], "owner_names": [], "updated": [],
              "to_own_transfer": [], "cancelled": [], "skipped_edited": 0, "skipped_linked": 0, "paired": [], "missing": [], "restored": []}
    report["warnings"] = []
    report.update(duplicates=[], anchors=[], transfer_accounts=[], transfer_balance=[], overlap_skipped=0)
    before = {a["id"]: (a["name"], a["current"]) for a in db.list_accounts()}
    # kişi adlarının başındaki banka artığı (eski sürüm 'Bank', 'Cumhuriyeti' atmıyordu). İLK adım ve döküm dosyasından
    # bağımsız: kayıt karşı tarafı, borç defteri ve kişi ayarı doğrudan taranır; elle değiştirilmiş kayıtta da ad
    # düzelir (ayrıştırma artığı, kullanıcı girişi değil). Aynı kişi varsa birleşir (db.rename_counterparty: kayıtlar,
    # yalnız "Ad" / "Ad · not" biçimindeki açıklama öneki, borçlar, kişi kategorisi/ayarı); kullanıcının yazdığı başka
    # açıklama ve notlara dokunulmaz.
    report["names"] = []
    with db.connect() as c:
        names = sorted({n for (n,) in c.execute(
            "SELECT counterparty FROM entries WHERE counterparty IS NOT NULL UNION SELECT name FROM counterparties "
            "UNION SELECT person FROM debts") if n})
    for n in names:
        new = bank_import.clean_person_name(n)
        if new and new != n:
            with db.connect() as c:
                cnt = c.execute("SELECT COUNT(*) FROM entries WHERE counterparty = ?", (n,)).fetchone()[0]
                exists = c.execute("SELECT 1 FROM entries WHERE counterparty = ? UNION SELECT 1 FROM counterparties "
                                   "WHERE name = ? UNION SELECT 1 FROM debts WHERE person = ?", (new, new, new)).fetchone()
            report["names"].append({"id": None, "date": "", "amount": cnt, "was": n, "now": new,
                                    "why": "mevcut kişiyle birleşir" if exists else "ad düzelir"})
            if apply:
                db.rename_counterparty(n, new)
    by_hash, docs = _document_rows(owners, report)
    _raw_notes(by_hash, report, apply)
    if report["owner_names"] and not db.owner_names():  # dökümden öğrenilen sahip: kart sahipleri kişi kalsın
        owners[:] = [o for o in owners if o in report["owner_names"]]

    def keep_message(e_id, raw, signed, new_desc):
        msg = bank_import.transfer_note(raw, owners)
        if msg and bank_import._squash(msg) not in bank_import._squash(new_desc):
            report["warnings"].append(f"#{e_id}: gönderenin mesajı '{msg}' yeni açıklamada yok → {new_desc!r}")
    with db.connect() as c:
        accounts = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM accounts")}
        entries = [dict(r) for r in c.execute(
            "SELECT * FROM entries WHERE import_hash IS NOT NULL AND import_hash NOT LIKE 'dekont:%' "
            "AND import_hash NOT LIKE 'mail:%' AND kind IN ('gelir', 'gider') AND wallet_id IS NULL "
            "AND COALESCE(payment, '') <> 'kredi_karti' AND recurring_id IS NULL")]
        entries = [e for e in entries if not e.get("not_virman")] + [
            {**e, "_keep": True} for e in entries if e.get("not_virman")]
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
            if e.get("_keep") and typ == "virman":  # kullanıcı "virman değil" dedi: yalnız açıklama onarılır
                typ = "gelir" if signed > 0 else "gider"
            if typ == "virman":
                label = bank_import.own_label(raw, signed, accounts.get(e["account_id"], ""), owners)
                keep_message(e["id"], raw, signed, label)
                report["to_own_transfer"].append({**brief, "now": label,
                                                  "why": bank_import.classify_reason(raw, signed, owners)[1]})
                if apply:
                    db.add_own_transfer(c, e["entry_date"], signed, accounts.get(e["account_id"], ""), raw,
                                        e["import_hash"], owners)
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
            if (not row and rawnote.raw_of(e.get("note")) is None
                    and (e.get("description") or None) == (new.get("description") or None)):
                # eski kayıt, ham metin bilinmiyor ve açıklamaya dokunulmuyor: not elle bırakılır (raw_notes karar verir).
                # Onarım açıklamayı değiştiriyorsa içe aktarmanın notu (ham satırı dahil) yazılır: ham metin kaybolmaz.
                new.pop("note", None)
            if new.get("note") and rawnote.user_note(e.get("note")):  # kullanıcının ham satır altındaki notu korunur
                new["note"] = f"{new['note']}\n{rawnote.user_note(e.get('note'))}"
            changed = {k: v for k, v in new.items() if (e.get(k) or None) != (v or None)}
            if not changed:
                continue
            keep_message(e["id"], raw, signed, desc)
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
        # ya da eski kural ('kendi adı açıklamada iki kez') üçüncü kişiye ödemeyi / kurumdan geleni virman saymış olabilir
        for o in [dict(x) for x in c.execute("SELECT * FROM own_transfers WHERE import_hash IS NOT NULL")]:
            r = by_hash.get(o["import_hash"])
            if r and (r["type"] == "gelir" or (r["type"] == "gider" and not db._pairs_own_transfer(
                    -r["amount"], r["date"], r["description"], owners, o["account"] or ""))):
                wrong.append(r)
                if apply:
                    c.execute("DELETE FROM own_transfers WHERE id = ?", (o["id"],))
            elif not o.get("raw"):  # eski kayıt: açıklama ham banka metni → temiz etiket, ham metin raw'a
                raw = r["description"] if r else o["description"] or ""
                label = bank_import.own_label(raw, o["amount"], o["account"], owners)
                keep_message(f"virman {o['id']}", raw, o["amount"], label)
                report.setdefault("relabelled", []).append({"id": o["id"], "date": o["transfer_date"],
                                                            "amount": o["amount"], "was": o["description"], "now": label})
                if apply:
                    c.execute("UPDATE own_transfers SET description = ?, raw = ? WHERE id = ?", (label[:500], raw[:500], o["id"]))
        report["restored"] = [{"id": None, "date": r["date"], "amount": r["amount"], "was": r["description"]} for r in wrong]
        known -= {r["hash"] for r in wrong}
        # PDF + Excel iki kez yüklenmiş hareketler: fazlası (sonra yüklenen belgeninki) silinir
        dups = _duplicates(c, docs)
        for r in dups:
            if r["table"] == "entries" and (_linked(c, r["id"]) or c.execute(
                    "SELECT 1 FROM entries WHERE id = ? AND (debt_id IS NOT NULL OR recurring_id IS NOT NULL)",
                    (r["id"],)).fetchone()):
                report["skipped_linked"] += 1
                continue
            report["duplicates"].append({"id": r["id"], "date": r["date"], "amount": r["amount"], "was": r["desc"],
                                         "why": f"{r['table']}, belge #{r['doc_id']} ile ikinci kez"})
            if apply:
                c.execute(f"DELETE FROM {r['table']} WHERE id = ?", (r["id"],))
        # dökümde olup kaydı olmayan: belge başına, başka biçimde zaten yüklü olanlar (çakışma) hariç
        missing = []
        for full, rows in docs:
            unknown = [r for r in rows if r["hash"] not in known and r.get("alt_hash") not in known]
            if not unknown:
                continue
            ov = db.import_overlaps(rows, full.get("account_id"), full.get("bank") or "", known)
            report["overlap_skipped"] += len(ov["rows"])
            missing += [r for i, r in enumerate(rows) if i not in ov["rows"] and r in unknown
                        and r["type"] in ("gelir", "gider") and r not in wrong]
        report["missing"] = [{"id": None, "date": r["date"], "amount": r["amount"], "was": r["description"]} for r in missing]
        to_add = wrong + missing
        # hesap çapası: yeniden eskiye sıralı dökümde günün son hareketi (önceden ilk hareketi alınıyordu)
        from app.bank_import import last_balance

        accs = {r["id"]: dict(r) for r in c.execute("SELECT * FROM accounts")}
        for full, rows in docs:
            a = accs.get(full.get("account_id"))
            lb = last_balance(rows)
            if not a or not lb or a.get("source") != "dokum" or a.get("as_of") != lb[0]:
                continue
            old = max(((r["date"], i, r["balance"]) for i, r in enumerate(rows) if r.get("balance") is not None))
            if money_ne(old[2], lb[1]) and money_eq(a["balance"], old[2]):
                report["anchors"].append({"id": a["id"], "date": lb[0], "amount": lb[1], "was": f"{a['name']} {old[2]:,.2f}",
                                          "now": f"{lb[1]:,.2f}"})
                if apply:
                    c.execute("UPDATE accounts SET balance = ? WHERE id = ?", (lb[1], a["id"]))
        # virmanlara hesap bilgisi. Dökümden gelen virman çapanın içinde (bakiye değişmez); elle ya da e-posta
        # bildirimiyle gelmiş ve çapadan sonraki virman bakiyeye girer (db._account_balance ile aynı kural)
        from app.bank_import import short_bank

        names, as_of = {}, {}
        for r in c.execute("SELECT id, name, alias, as_of FROM accounts WHERE kind = 'banka'"):
            names.setdefault(r["name"], r["id"])
            as_of[r["id"]] = r["as_of"]
            if r["alias"]:
                names.setdefault(r["alias"], r["id"])
        for o in [dict(x) for x in c.execute("SELECT * FROM own_transfers WHERE account_id IS NULL AND account IS NOT NULL")]:
            aid = names.get(short_bank(o["account"] or ""))
            if aid:
                counts = ((not o["import_hash"] or o["import_hash"].startswith("mail:")) and as_of.get(aid)
                          and o["transfer_date"] > as_of[aid])
                item = {"id": o["id"], "date": o["transfer_date"], "amount": o["amount"], "was": o["description"],
                        "now": f"hesap #{aid}", "effect": o["amount"] if counts else 0.0}
                report["transfer_balance" if counts else "transfer_accounts"].append(item)
                if apply:
                    c.execute("UPDATE own_transfers SET account_id = ? WHERE id = ?", (aid, o["id"]))
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
            db.import_bank_rows(part, account=part[0]["bank"], owners=owners, account_id=acc, check_overlap=False)
        # açıklaması kesik kalmış (dosyası yok) giden kendi transferleri: karşı hesaba giriş artık own_transfers'ta
        report["paired"] = [{"id": m["id"], "date": m["entry_date"], "amount": -m["amount"], "was": m["description"]}
                            for m in db.repair_own_transfers(owners)]
    # ekstrelerin ödendi işaretleri: bütün kart ödemelerinden yeniden (eski algoritma aynı ödemeyi birden çok ekstreye
    # yazıyordu, yükleme sırasına bağlıydı); elle işaretlenenlere dokunulmaz
    report["statements"] = db.reassign_statement_payments(apply=apply)["changed"]
    after = {a["id"]: a["current"] for a in db.list_accounts()}
    report["balances"] = [{"account": name, "before": cur, "after": after.get(aid),
                           "diff": round((after.get(aid) or 0) - (cur or 0), 2) if cur is not None else None}
                          for aid, (name, cur) in before.items()]
    return report


def _print(rep: dict, apply: bool) -> None:
    print(("UYGULANDI" if apply else "KURU ÇALIŞTIRMA (yazılmadı)"))
    print("okunan dökümler:", len(rep["docs_read"]), "okunamayan:", rep["docs_unread"], "dosyası yok:", rep["docs_missing"])
    print("öğrenilen hesap sahibi adları:", rep["owner_names"])
    for key, title in (("to_own_transfer", "kendi hesapları arası → virman"), ("cancelled", "iptal çifti → silinir"),
                       ("paired", "giden transfer karşılığıyla eşleşti → virman"),
                       ("restored", "yanlışlıkla virman sayılmış gider → geri eklendi"),
                       ("missing", "dökümde var, kaydı yok → eklendi"),
                       ("duplicates", "aynı hareket iki belgeden (PDF + Excel) → fazlası silinir"),
                       ("raw_notes", "banka açıklaması (ham metin) nota geri yazılır"),
                       ("raw_unknown", "banka açıklaması bilinmiyor (açıklama değişmiş) → dokunulmaz, raw boş"),
                       ("anchors", "hesap bakiyesi (yeniden eskiye sıralı döküm) → günün son hareketi"),
                       ("transfer_accounts", "virmanlara hesap bilgisi eklenir (dökümden gelmiş; bakiye değişmez)"),
                       ("transfer_balance", "virmanlara hesap bilgisi eklenir (elle/e-postadan, çapadan sonra; bakiyeye girer)")):
        rows = rep.get(key, [])
        if key.startswith("transfer_"):
            net = sum(r.get("effect", 0.0) for r in rows)
            print(f"\n{title}: {len(rows)} kayıt, bakiye etkisi {net:+,.2f}")
        else:
            print(f"\n{title}: {len(rows)} kayıt, toplam tutar {sum(r['amount'] for r in rows):,.2f}")
        for r in rows:
            extra = f" → {r['now']!r}" if r.get("now") else ""
            why = f"  [{r['why']}]" if r.get("why") else ""
            eff = f"  bakiye etkisi {r['effect']:+,.2f}" if key == "transfer_balance" else ""
            print(f"  #{r['id'] or '-'} {r['date']} {r['amount']:>12,.2f}  {(r['was'] or '')[:60]}{extra}{why}{eff}")
    names = rep.get("names", [])
    print(f"\nkişi adının başındaki banka artığı (Bank, Cumhuriyeti…) → düzelir/birleşir: {len(names)} kişi")
    for r in names:
        print(f"  {r['was']!r} → {r['now']!r}  ({r['amount']} kayıt; {r['why']})")
    print(f"\naçıklama/kişi düzeltilen: {len(rep['updated'])}")
    for r in rep["updated"]:
        print(f"  #{r['id']} {r['date']} {r['amount']:>12,.2f}  {(r['was'] or '')[:45]!r} → {r['now']!r} {r['fields']}")
    sts = rep.get("statements", [])
    print(f"\nekstre ödendi işareti değişen: {len(sts)}")
    for ch in sts:
        print(f"  {ch['card'][-4:]} {ch['period_end']} borç {ch['total_debt']:,.2f}: {ch['was']} → {ch['now']}  "
              f"ödemeler {ch['payments']}")
    rel = rep.get("relabelled", [])
    print(f"\nvirman açıklaması temizlenen: {len(rel)}")
    for r in rel:
        print(f"  virman #{r['id']} {r['date']} {r['amount']:>12,.2f}  {(r['was'] or '')[:45]!r} → {r['now']!r}")
    print(f"\nUYARI (gönderenin mesajı kayboluyor): {len(rep['warnings'])}")
    for w in rep["warnings"]:
        print("  " + w)
    bal = rep.get("balances", [])
    print(f"\nHESAP BAKİYELERİ (onarım öncesi → sonrası{', kopya üzerinde' if not apply else ''}):")
    for b in bal:
        fmt = lambda v: "—" if v is None else f"{v:,.2f}"  # noqa: E731
        mark = "  " if not b["diff"] else "≠ "
        print(f"  {mark}{b['account'][:28]:28} {fmt(b['before']):>14} → {fmt(b['after']):>14}"
              + (f"  (fark {b['diff']:+,.2f})" if b["diff"] else ""))
    print(f"\ndokunulmayan: elle değiştirilmiş {rep['skipped_edited']}, bağlantılı {rep['skipped_linked']}; "
          f"başka belgeden zaten yüklü (çakışma, eklenmedi) {rep.get('overlap_skipped', 0)}")


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
