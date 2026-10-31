"""Karşılaştırma aracı: kullanıcının saklı belgelerini BOŞ geçici bir defterde baştan içe aktarır, sonucu canlı defterle
karşılaştırır. Canlı veritabanına yazmaz (salt okunur kopyasından okur), depoya (R2) yazmaz, belge okuma hakkı ve
yapay zekâ kotası saymaz, topluluk şablonlarına dokunmaz.

    python -m app.replay_check <kullanıcı-adı> [--no-ai] [--json] [--keep]

Belgeler: hesap dökümleri (bank_documents: PDF/Excel/CSV) ve kart ekstreleri (statements: saklı PDF), yükleme sırasıyla.
Kart Excel'inden gelen ekstrelerin dosyası saklanmaz: canlıdan kopyalanır (raporda belirtilir). Yapay zekâ gerektiren
belge (ör. Kuveyt Türk hesap dökümü) --no-ai ile atlanır.

Rapor: (1) hesap bakiyeleri: dökümün son bakiye satırı / yeni defterin hesapladığı / canlı; (2) kart ekstresi denklemi
ve reconciliation_diff; (3) ay ay gelir / gider / virman (belgeden gelen kayıtlar) yeni vs canlı ve farkı oluşturan
kayıtlar (import_hash ile: yalnız canlıda / yalnız yenide / tutar ya da tür farklı); (4) ekstre ödendi işaretleri;
(5) virman sayısı/toplamı ve kişi sayısı.
"""
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict

from app import db, money

DOC_SQL = ("import_hash IS NOT NULL AND import_hash NOT LIKE 'mail:%' AND import_hash NOT LIKE 'dekont:%' "
           "AND kind IN ('gelir', 'gider')")


@contextlib.contextmanager
def _isolated(no_ai: bool):
    """Yan etkisiz okuma: topluluk şablonu kaydı/paylaşımı/sayacı yok, yapay zekâ isteğe bağlı kapalı."""
    from app import receipt, templates

    saved = {k: getattr(templates, k) for k in ("save", "_used", "share_async", "sync_community")}
    templates.save = lambda spec, source="learned": False
    templates._used = lambda tid: None
    templates.share_async = lambda spec: None
    templates.sync_community = lambda force=False: 0
    enabled = receipt.enabled
    if no_ai:
        receipt.enabled = lambda: False
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(templates, k, v)
        receipt.enabled = enabled


def _profile_key(columns: list[str]) -> str:
    import hashlib

    return "bank_profile:" + hashlib.sha1("|".join(c.strip().casefold() for c in columns).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- canlı defterden belgeler

def _collect(live_copy: str, live_path: str, user: dict) -> tuple[list[dict], dict]:
    """(belgeler yükleme sırasıyla, taşınacak ayarlar). Dosyalar canlı defterin dosya alanından okunur."""
    from app import storage

    docs, settings = [], {}
    with db.use_space(live_copy, user):
        db._space.get()["files"] = db._files_scope(live_path)
        with db.connect() as c:
            for (k, v) in c.execute("SELECT key, value FROM settings WHERE key = 'pdf_passwords' OR key LIKE 'bank_profile:%'"):
                settings[k] = v
            bank = [dict(r) for r in c.execute(
                "SELECT d.*, a.name AS account_name FROM bank_documents d LEFT JOIN accounts a ON a.id = d.account_id "
                "AND a.kind = 'banka' ORDER BY d.uploaded_at, d.id")]
            sts = [dict(r) for r in c.execute("SELECT * FROM statements ORDER BY uploaded_at, id")]
        for d in bank:
            data = storage.book_get("statements-bank", d["file_name"]) if d["stored"] else None
            docs.append({"type": "bank", "at": d["uploaded_at"] or "", "id": d["id"], "name": d["filename"] or d["file_name"],
                         "data": data, "bank": d["bank"], "account_name": d["account_name"]})
        seen_files: set[str] = set()
        for s in sts:
            excel = (s["filename"] or "").lower().endswith((".xlsx", ".xls", ".csv"))
            if excel:
                docs.append({"type": "card_copy", "at": s["uploaded_at"] or "", "id": s["id"],
                             "name": f"{s['filename']} ({s['period_end']})", "data": None})
                continue
            if s["file_hash"] in seen_files:  # aynı PDF birden çok ekstre (birden çok kart) olabilir: bir kez oku
                continue
            seen_files.add(s["file_hash"])
            data = storage.book_get("uploads", f"{s['file_hash']}.pdf")
            docs.append({"type": "card", "at": s["uploaded_at"] or "", "id": s["id"], "name": s["filename"] or s["file_hash"],
                         "data": data})
    docs.sort(key=lambda d: (d["at"], d["type"] != "card", d["id"]))
    return docs, settings


# ---------------------------------------------------------------- yeni defterde içe aktarma

def _import_bank(doc: dict, passwords: list[str], last_bal: dict) -> dict:
    from app import bank_import, bank_pdf, sync

    data, name = doc["data"], (doc["name"] or "").lower()
    if name.endswith(".pdf"):
        res = bank_pdf.parse_account_pdf(data, passwords)
        owners = [res["owner_name"]] if res.get("owner_name") else db.card_holders() + db.owner_names()
        parsed = bank_import.finalize(res["rows"], [res.get("owner_name") or "", *db.card_holders(), *db.owner_names()])
        account, iban = res.get("bank") or "", res.get("iban")
        if res.get("owner_name"):
            db.remember_owner_name(res["owner_name"])
        extra = {"verified": res.get("verified"), "bank": account}
    else:
        rows = bank_import.read_rows(data, doc["name"])
        det = bank_import.detect(rows)
        saved = db.get_setting(_profile_key(det["columns"]))
        mapping = json.loads(saved) if saved else det["mapping"]
        owners = db.card_holders() + db.owner_names()
        parsed = bank_import.parse(rows, mapping, det["header_row"], owners)
        account, iban = doc.get("bank") or "", sync.find_iban_rows(rows)
        extra = {"profile": bool(saved), "bank": account}
    if not parsed:
        raise ValueError("satır yok")
    # kullanıcının canlıda seçtiği hesap (aynı adla); yoksa içe aktarmanın kendi seçimi
    chosen = db.ensure_account(doc["account_name"]) if doc.get("account_name") else None
    acc_id = sync.account_for_import(account or None, iban, chosen, False) if (account or chosen) else None
    stats = db.import_bank_rows(parsed, account=account, owners=owners, account_id=acc_id)
    db.auto_link()
    with_bal = [(r["date"], i, r["balance"]) for i, r in enumerate(parsed) if r.get("balance") is not None]
    if with_bal and acc_id:
        d, _, bal = max(with_bal)
        if acc_id not in last_bal or d >= last_bal[acc_id][0]:
            last_bal[acc_id] = (d, bal)
    return {**extra, "rows": len(parsed), "stats": stats}


def _import_card(doc: dict, passwords: list[str], allow_ai: bool) -> dict:
    from app.parsers import parse_statement

    st = parse_statement(doc["data"], allow_ai=allow_ai, passwords=passwords)
    if db.statement_exists(doc["data"], st):
        return {"status": "duplicate"}
    db.save_statement(doc["data"], doc["name"], st)
    return {"status": "saved", "bank": st.bank, "card": st.card_no, "period_end": st.period_end.isoformat(),
            "linked": db.auto_link(), "marked_paid": db.auto_mark_paid(), "ai": st.ai_parsed}


def _copy_card(doc: dict, live_copy: str) -> dict:
    """Kart Excel'inden gelmiş ekstre (dosyası saklanmaz): canlıdaki satırlarıyla kopyala."""
    with sqlite3.connect(live_copy) as src:
        src.row_factory = sqlite3.Row
        s = dict(src.execute("SELECT * FROM statements WHERE id = ?", (doc["id"],)).fetchone())
        txns = [dict(r) for r in src.execute("SELECT * FROM transactions WHERE statement_id = ?", (doc["id"],))]
    with db.connect() as c:
        cols = [k for k in s if k not in ("id", "confirmed_at")]
        sid = c.execute(f"INSERT OR IGNORE INTO statements ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [s[k] for k in cols]).lastrowid
        tcols = [r[1] for r in c.execute("PRAGMA table_info(transactions)") if r[1] != "id"]
        for t in txns:
            t["statement_id"] = sid
            c.execute(f"INSERT INTO transactions ({', '.join(tcols)}) VALUES ({', '.join('?' * len(tcols))})",
                      [t.get(k) for k in tcols])
    db.auto_link()
    db.auto_mark_paid()
    return {"status": "copied", "txns": len(txns)}


def replay(docs: list[dict], settings: dict, live_copy: str, no_ai: bool) -> tuple[list[dict], dict]:
    from app import sync

    log, last_bal = [], {}
    for k, v in settings.items():
        db.set_setting(k, v)
    passwords = db.pdf_passwords()
    for d in docs:
        entry = {"type": d["type"], "name": d["name"], "uploaded_at": d["at"]}
        try:
            if d["type"] == "card_copy":
                entry.update(_copy_card(d, live_copy))
            elif d["data"] is None:
                entry.update(status="skipped", reason="dosya saklanmamış")
            elif d["type"] == "bank":
                entry.update(status="saved", **_import_bank(d, passwords, last_bal))
            else:
                entry.update(_import_card(d, passwords, not no_ai))
            sync.reconcile()
            db.auto_update_cycle()
        except Exception as e:  # noqa: BLE001 - bir belge okunamazsa diğerleri devam etsin
            entry.update(status="error", reason=str(e)[:200])
        log.append(entry)
    return log, last_bal


# ---------------------------------------------------------------- ölçümler

def _measure() -> dict:
    """Açık defterden karşılaştırma verisi."""
    from app import finance

    with db.connect() as c:
        accounts = {r["id"]: dict(r) for r in c.execute("SELECT * FROM accounts")}
        ents = [dict(r) for r in c.execute(f"SELECT id, entry_date, kind, amount, description, import_hash, account_id "
                                           f"FROM entries WHERE {DOC_SQL}")]
        own = [dict(r) for r in c.execute("SELECT transfer_date, amount, account, description, import_hash FROM own_transfers")]
        cps = [dict(r) for r in c.execute("SELECT pay_date, amount, card_last4, import_hash FROM card_payments")]
    current = {a["id"]: a.get("current") for a in db.list_accounts()}
    sts = db.list_statements()
    people = [p for p in db.transfers_overview()["people"] if p.get("count")]
    months: dict[str, dict] = defaultdict(lambda: {"gelir": 0.0, "gider": 0.0, "virman_in": 0.0, "virman_out": 0.0,
                                                   "kart_odemesi": 0.0})
    for e in ents:
        months[e["entry_date"][:7]][e["kind"]] += e["amount"]
    for o in own:
        months[o["transfer_date"][:7]]["virman_in" if o["amount"] > 0 else "virman_out"] += abs(o["amount"])
    for p in cps:
        months[p["pay_date"][:7]]["kart_odemesi"] += p["amount"]
    card_spend: dict[str, float] = defaultdict(float)
    for t in finance.transactions(None):
        if t.get("source") == "card" and not t.get("deleted") and t["kind"] in ("harcama", "faiz_ucret", "iade"):
            card_spend[t["month"]] += t["amount"]
    return {
        "accounts": {a["name"]: {"id": a["id"], "balance": a["balance"], "as_of": a["as_of"], "current": current.get(a["id"])}
                     for a in accounts.values()},
        "entries": {e["import_hash"]: e for e in ents},
        "own": {o["import_hash"]: o for o in own if o["import_hash"]},
        "months": {m: {k: round(v, 2) for k, v in d.items()} | {"kart_harcama": round(card_spend.get(m, 0.0), 2)}
                   for m, d in months.items()} | {m: {"gelir": 0.0, "gider": 0.0, "virman_in": 0.0, "virman_out": 0.0,
                                                     "kart_odemesi": 0.0, "kart_harcama": round(v, 2)}
                                                 for m, v in card_spend.items() if m not in months},
        "statements": [{k: s.get(k) for k in ("id", "bank", "card_no", "period_end", "previous_balance", "payments",
                                               "period_activity", "fees", "total_debt", "reconciliation_diff",
                                               "needs_check", "paid_amount", "paid_at", "confirmed_at", "min_payment",
                                               "fees_included", "equation_diff")} for s in sts],
        "virman": {"count": len(own), "in": round(sum(o["amount"] for o in own if o["amount"] > 0), 2),
                   "out": round(-sum(o["amount"] for o in own if o["amount"] < 0), 2)},
        "people": len(people),
        "account_names": {a["id"]: a["name"] for a in accounts.values()},
    }


def compare(new: dict, live: dict, last_bal: dict) -> dict:
    out: dict = {}
    # (1) bakiyeler
    bal = []
    for name, a in new["accounts"].items():
        if a["id"] not in last_bal and a["current"] is None:
            continue
        d, b = last_bal.get(a["id"], (None, None))
        lv = live["accounts"].get(name, {})
        bal.append({"account": name, "doc_last_date": d, "doc_last_balance": b, "new_current": a["current"],
                    "live_current": lv.get("current"),
                    "diff_new_vs_doc": round(a["current"] - b, 2) if a["current"] is not None and b is not None else None,
                    "diff_live_vs_doc": round(lv["current"] - b, 2) if lv.get("current") is not None and b is not None else None})
    out["balances"] = bal
    # (2) ekstre denklemi
    eq = []
    for s in new["statements"]:
        d = s.get("equation_diff")
        calc = round(s["total_debt"] - d, 2) if d is not None else None
        eq.append({**s, "equation": calc})
    out["statements"] = eq
    # (3) aylar
    months = []
    for m in sorted(set(new["months"]) | set(live["months"])):
        n, lv = new["months"].get(m, {}), live["months"].get(m, {})
        keys = ("gelir", "gider", "virman_in", "virman_out", "kart_odemesi", "kart_harcama")
        diff = {k: round(n.get(k, 0) - lv.get(k, 0), 2) for k in keys}
        row = {"month": m, "new": {k: n.get(k, 0) for k in keys}, "live": {k: lv.get(k, 0) for k in keys}, "diff": diff}
        if any(not money.is_zero(v) for v in diff.values()):
            row["records"] = _month_records(m, new, live)
        months.append(row)
    out["months"] = months
    # (4) ödendi işaretleri
    paid = []
    live_st = {(s["card_no"], s["period_end"]): s for s in live["statements"]}

    def flags(st_list):
        """(kart, dönem) → işaretler: tutar uyuşmazlığı (ödenen ne borç ne asgari), aynı ödeme birden çok ekstrede."""
        out_f: dict = defaultdict(list)
        seen_pay: dict = defaultdict(list)
        for s in st_list:
            if s["paid_at"]:
                seen_pay[((s["card_no"] or "")[-4:], s["paid_at"], s["paid_amount"])].append(s)
                if abs((s["paid_amount"] or 0) - (s["total_debt"] or 0)) > 1 and \
                        abs((s["paid_amount"] or 0) - (s.get("min_payment") or -9)) > 1:
                    out_f[(s["card_no"], s["period_end"])].append("tutar uyuşmazlığı")
        for group in seen_pay.values():
            if len(group) > 1:
                for s in group:
                    out_f[(s["card_no"], s["period_end"])].append(f"aynı ödeme {len(group)} ekstrede")
        return out_f

    nf, lf = flags(new["statements"]), flags(live["statements"])
    for s in new["statements"]:
        lv = live_st.get((s["card_no"], s["period_end"]), {})
        k = (s["card_no"], s["period_end"])
        paid.append({"card": s["card_no"], "period_end": s["period_end"], "total_debt": s["total_debt"],
                     "new_paid": s["paid_amount"], "new_paid_at": s["paid_at"], "live_paid": lv.get("paid_amount"),
                     "live_paid_at": lv.get("paid_at"), "new_flags": nf.get(k, []), "live_flags": lf.get(k, []),
                     "same": (s["paid_amount"] or 0) == (lv.get("paid_amount") or 0) and s["paid_at"] == lv.get("paid_at")})
    out["payments"] = paid
    # (5) virman ve kişiler
    out["virman"] = {"new": new["virman"], "live": live["virman"]}
    out["people"] = {"new": new["people"], "live": live["people"]}
    return out


def _month_records(m: str, new: dict, live: dict) -> list[dict]:
    recs = []

    def label(src: dict, h: str) -> tuple[str, float, str] | None:
        if h in src["entries"]:
            e = src["entries"][h]
            return e["kind"], e["amount"], e["description"]
        if h in src["own"]:
            o = src["own"][h]
            return "virman", o["amount"], o["description"]
        return None

    hashes = {h for h, e in new["entries"].items() if e["entry_date"][:7] == m} | \
             {h for h, e in live["entries"].items() if e["entry_date"][:7] == m} | \
             {h for h, o in new["own"].items() if o["transfer_date"][:7] == m} | \
             {h for h, o in live["own"].items() if o["transfer_date"][:7] == m}
    for h in sorted(hashes):
        a, b = label(new, h), label(live, h)
        if a and b and a[0] == b[0] and money.eq(abs(a[1]), abs(b[1])):
            continue
        recs.append({"import_hash": h, "where": "yalnız yenide" if not b else "yalnız canlıda" if not a else "farklı",
                     "new": {"kind": a[0], "amount": a[1], "description": a[2]} if a else None,
                     "live": {"kind": b[0], "amount": b[1], "description": b[2]} if b else None})
    return recs


# ---------------------------------------------------------------- çalıştırma

def run(username: str, no_ai: bool = False, keep: bool = False) -> dict:
    from app import system

    user = system.user_by_name(username)
    if not user:
        raise LookupError(f"kullanıcı yok: {username}")
    live_path = system.user_db_path(user) or db.DB_PATH
    tmp = tempfile.mkdtemp(prefix="replay-")
    try:
        live_copy = os.path.join(tmp, "live", "canli.db")
        os.makedirs(os.path.dirname(live_copy))
        with sqlite3.connect(f"file:{live_path}?mode=ro", uri=True) as src, sqlite3.connect(live_copy) as dst:
            src.backup(dst)
        docs, settings = _collect(live_copy, live_path, user)
        with db.use_space(live_copy, user):
            live = _measure()
        new_path = os.path.join(tmp, "new", "yeni.db")
        os.makedirs(os.path.dirname(new_path))
        with _isolated(no_ai), db.use_space(new_path, None):  # kullanıcısız: belge hakkı ve kota sayılmaz
            log, last_bal = replay(docs, settings, live_copy, no_ai)
            new = _measure()
        return {"user": username, "no_ai": no_ai, "documents": log, **compare(new, live, last_bal),
                "tmp": tmp if keep else None}
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def _tl(v) -> str:
    return "—" if v is None else f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def render(r: dict) -> str:
    out = [f"Karşılaştırma: {r['user']}{' (yapay zekâsız)' if r['no_ai'] else ''}", "", "BELGELER (yükleme sırası)"]
    for d in r["documents"]:
        extra = d.get("reason") or (f"{d.get('rows', '')} satır {d.get('stats', '')}" if d["type"] == "bank" else
                                     f"{d.get('bank', '')} {d.get('card', '')} {d.get('period_end', '')}")
        out.append(f"  [{d['status']:9}] {d['type']:9} {d['uploaded_at'][:16]}  {d['name'][:50]}  {extra}"[:220])
    out += ["", "(1) HESAP BAKİYELERİ: dökümün son satırı / yeni defter / canlı"]
    for b in r["balances"]:
        out.append(f"  {b['account'][:24]:24} döküm {_tl(b['doc_last_balance'])} ({b['doc_last_date']})  yeni {_tl(b['new_current'])}"
                   f" (fark {_tl(b['diff_new_vs_doc'])})  canlı {_tl(b['live_current'])} (fark {_tl(b['diff_live_vs_doc'])})")
    out += ["", "(2) KART EKSTRELERİ: önceki − ödeme + harcama + faiz/ücret = yeni borç"]
    for s in r["statements"]:
        flag = "OK" if s["equation_diff"] is not None and abs(s["equation_diff"]) < 1 else "KONTROL"
        fee_txt = (f" + {_tl(s['fees'])}" if s.get("fees_included") is False else
                   f" (faiz {_tl(s['fees'])} içinde)" if s.get("fees_included") else "")
        out.append(f"  [{flag:7}] {s['bank']} {s['card_no'][-4:]} {s['period_end']}: {_tl(s['previous_balance'])} − "
                   f"{_tl(s['payments'])} + {_tl(s['period_activity'])}{fee_txt} = {_tl(s['equation'])} / "
                   f"ekstre {_tl(s['total_debt'])} (fark {_tl(s['equation_diff'])}, okunan−özet {_tl(s['reconciliation_diff'])})")
    out += ["", "(3) AY AY (belgeden gelen kayıtlar): yeni / canlı / fark"]
    for m in r["months"]:
        cols = " · ".join(f"{k} {_tl(m['new'][k])}/{_tl(m['live'][k])}" + (f" (Δ {_tl(m['diff'][k])})" if not money.is_zero(m['diff'][k]) else "")
                          for k in m["new"])
        out.append(f"  {m['month']}: {cols}")
        for rec in m.get("records", []):
            n, lv = rec["new"], rec["live"]
            out.append(f"      {rec['where']:15} yeni: {n and (n['kind'] + ' ' + _tl(n['amount']) + ' ' + n['description'][:40])}"
                       f" | canlı: {lv and (lv['kind'] + ' ' + _tl(lv['amount']) + ' ' + lv['description'][:40])}")
    out += ["", "(4) EKSTRE ÖDENDİ İŞARETLERİ: yeni / canlı"]
    for p in r["payments"]:
        out.append(f"  {'  ' if p['same'] else '≠ '}{p['card'][-4:]} {p['period_end']} borç {_tl(p['total_debt'])}: "
                   f"yeni {_tl(p['new_paid'])} {p['new_paid_at'] or ''} {','.join(p['new_flags'])} / "
                   f"canlı {_tl(p['live_paid'])} {p['live_paid_at'] or ''} {','.join(p['live_flags'])}")
    v = r["virman"]
    out += ["", "(5) VİRMAN VE KİŞİLER",
            f"  virman: yeni {v['new']['count']} kayıt (giriş {_tl(v['new']['in'])}, çıkış {_tl(v['new']['out'])}) / "
            f"canlı {v['live']['count']} kayıt (giriş {_tl(v['live']['in'])}, çıkış {_tl(v['live']['out'])})",
            f"  kişi: yeni {r['people']['new']} / canlı {r['people']['live']}"]
    if r.get("tmp"):
        out.append(f"\ngeçici defter korundu: {r['tmp']}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    r = run(args[0], no_ai="--no-ai" in argv, keep="--keep" in argv)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str) if "--json" in argv else render(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
