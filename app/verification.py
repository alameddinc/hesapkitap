"""Doğrulanamayan belge (S6): yapay zekâ ile okunan ekstrenin denklemi ya da hesap dökümünün bakiye zinciri tutmuyorsa.

- Uygulamadan yükleme (kullanıcı ekranın başında): kaydedilir, yanıtta verified: false, verification: "dogrulanamadi",
  verification_text; Yapılacaklar'a "Belgedeki rakamları kontrol et" düşer (kullanıcı onaylayınca ya da kapatınca gider).
- E-posta / paylaş yolu (kullanıcı yok; HESAPKITAP_HOLD_UNVERIFIED=1 iken): İÇE AKTARILMAZ. Okunan sonuç Gelen kutusu'nda (shared_inbox, kind "dokum" |
  "ekstre") onay bekler; POST /api/inbox/{id}/import ile kaydedilir, /silindi ile atılır. Yapılacaklar'da "belge_onay".

Durum değerleri (sözleşme): "dogrulandi" (zincir/denklem tuttu), "dogrulanamadi" (tutmadı), "onaylandi" (tutmadı ama
kullanıcı belgeyle karşılaştırıp doğru dedi), null (Excel/CSV gibi doğrulaması olmayan biçim).
"""
import json
import os
from dataclasses import asdict
from datetime import date

from app import db, terms

DOGRULANDI, DOGRULANAMADI, ONAYLANDI = "dogrulandi", "dogrulanamadi", "onaylandi"


def hold_enabled() -> bool:
    """HESAPKITAP_HOLD_UNVERIFIED=1: e-posta/paylaş yolunda doğrulanamayan belge Gelen kutusu'nda onay bekler.
    Varsayılan 0 (Gelen kutusu'nda dokum/ekstre türlerini tanımayan eski istemciler için): belge eskisi gibi içe
    aktarılır; verification alanları ve 'belge_dogrulanamadi' Yapılacaklar maddesi yine üretilir."""
    return os.environ.get("HESAPKITAP_HOLD_UNVERIFIED", "0").strip() == "1"
_DOC_STATE = {1: DOGRULANDI, 0: DOGRULANAMADI, 2: ONAYLANDI}
RECENT_DAYS = 45  # bu kadar eski yüklemeler için Yapılacaklar'da ısrar edilmez


def account_text(chain_bad: int, closing_ok: bool) -> str:
    why = []
    if chain_bad:
        why.append(f"{chain_bad} satırda bakiye bir önceki satırla tutmuyor")
    if not closing_ok:
        why.append("son bakiye belgedeki kapanış bakiyesiyle tutmuyor")
    return (f"{terms.EQ_BAD}: " + ("; ".join(why) or "bakiye zinciri doğrulanamadı")
            + ". Bir satır eksik ya da yanlış okunmuş olabilir; hareketleri belgeyle karşılaştır.")


def statement_text(equation_diff: float | None, reconciliation_diff: float | None) -> str:
    from app.money import tl

    why = []
    if equation_diff is not None and abs(equation_diff) >= 1:
        why.append(f"önceki borç − ödemeler + harcamalar yeni borçtan {tl(abs(equation_diff))} farklı")
    if reconciliation_diff is not None and abs(reconciliation_diff) >= 1:
        why.append(f"okunan işlemlerin toplamı özetten {tl(abs(reconciliation_diff))} farklı")
    return (f"{terms.EQ_BAD}: " + ("; ".join(why) or "ekstre denklemi doğrulanamadı")
            + ". Bir işlem eksik ya da yanlış okunmuş olabilir; ekstreyle karşılaştır.")


def statement_check(stmt) -> dict:
    """Kaydedilmemiş Statement için: {verified, verification, verification_text, equation_diff, reconciliation_diff}."""
    from app.parsers.base import equation

    eq = equation(stmt.previous_balance, stmt.payments, stmt.period_activity, stmt.fees(), stmt.total_debt)
    rd = stmt.reconciliation_diff()
    ok = eq["ok"] is not False and abs(rd) < 1
    return {"verified": ok, "verification": DOGRULANDI if ok else DOGRULANAMADI,
            "verification_text": None if ok else statement_text(eq["diff"], rd),
            "equation_diff": eq["diff"], "reconciliation_diff": rd}


def account_check(res: dict) -> dict:
    ok = bool(res.get("verified"))
    cb = res.get("chain_bad")
    n = len(cb) if isinstance(cb, (list, tuple)) else int(cb or 0)
    return {"verified": ok, "verification": DOGRULANDI if ok else DOGRULANAMADI,
            "verification_text": None if ok else account_text(n, res.get("closing_ok", True)), "chain_bad_count": n}


# ---------------------------------------------------------------- hesap dökümü dosyasının durumu

def set_document(doc_id: int | None, verified: bool | None) -> None:
    if doc_id and verified is not None:
        with db.connect() as c:
            c.execute("UPDATE bank_documents SET verified = ? WHERE id = ?", (1 if verified else 0, doc_id))


def confirm_document(doc_id: int) -> bool:
    with db.connect() as c:
        return c.execute("UPDATE bank_documents SET verified = 2 WHERE id = ? AND verified = 0", (doc_id,)).rowcount > 0


def document_state(v) -> str | None:
    return _DOC_STATE.get(v) if v is not None else None


# ---------------------------------------------------------------- e-posta yolu: onaya düşür

def _store_pdf(data: bytes) -> str:
    name = f"{db.file_hash(data)}.pdf"
    db._store_quietly("uploads", name, data, "application/pdf")  # gece temizliği bekleyen paylaşımı korur (gc_files)
    return name


def _jsonable_statement(stmt) -> dict:
    d = asdict(stmt)
    for k, v in list(d.items()):
        if isinstance(v, date):
            d[k] = v.isoformat()
    for t in d["transactions"]:
        t["txn_date"] = t["txn_date"].isoformat() if isinstance(t["txn_date"], date) else t["txn_date"]
    return d


def statement_from_json(d: dict):
    from app.parsers.base import Statement, Transaction

    d = dict(d)
    for k in ("period_end", "due_date", "next_period_end", "next_due_date"):
        if d.get(k):
            d[k] = date.fromisoformat(d[k])
    d["transactions"] = [Transaction(**{**t, "txn_date": date.fromisoformat(t["txn_date"])}) for t in d["transactions"]]
    return Statement(**d)


def hold(kind: str, filename: str, data: bytes, payload: dict, check: dict) -> int:
    """Doğrulanamayan belgeyi içe aktarmadan Gelen kutusu'na koy. kind: dokum | ekstre. Kayıt id'si."""
    from app import share

    doc = {"type": kind, "file": _store_pdf(data), "filename": filename, **check, **payload}
    with db.connect() as c:
        share._init(c)
        return c.execute("INSERT INTO shared_inbox (filename, kind, doc) VALUES (?, ?, ?)",
                         (filename, kind, json.dumps(doc, ensure_ascii=False))).lastrowid


class Held(str):
    """handle_pdf sonucu: belge içe aktarılmadı, Gelen kutusu'nda onay bekliyor (inbox_id)."""
    inbox_id: int | None = None


def held(message: str, iid: int) -> Held:
    h = Held(message)
    h.inbox_id = iid
    return h


def held_doc(iid: int) -> dict | None:
    """Bekleyen dokum/ekstre kaydının belgesi; ortak defterde üyeden gizli kart/hesaba aitse None (404)."""
    from app import book, share
    from app.bank_import import short_bank

    with db.connect() as c:
        share._init(c)
        r = c.execute("SELECT * FROM shared_inbox WHERE id = ? AND kind IN ('dokum', 'ekstre')", (iid,)).fetchone()
    if not r:
        return None
    doc = json.loads(r["doc"])
    rules = book.active()
    if rules:
        if r["kind"] == "ekstre" and (doc.get("statement") or {}).get("card_no") in set(rules["hidden_cards"]):
            return None
        if r["kind"] == "dokum" and rules["hidden_accounts"]:
            with db.connect() as c:
                ids = {x[0] for x in c.execute("SELECT id FROM accounts WHERE name = ? OR alias = ?",
                                               (short_bank(doc.get("bank") or ""), short_bank(doc.get("bank") or "")))}
            if ids & set(rules["hidden_accounts"]):
                return None
    return {**doc, "kind": r["kind"], "status": r["status"], "filename": doc.get("filename") or r["filename"]}


def import_held(iid: int) -> dict:
    """Onay: Gelen kutusu'ndaki doğrulanamayan belgeyi kaydet. {ok, kind, message, stats?}. Bulunamazsa KeyError."""
    from app import doc_reads, share

    with db.connect() as c:
        share._init(c)
        r = c.execute("SELECT * FROM shared_inbox WHERE id = ? AND status = 'bekliyor' AND kind IN ('dokum', 'ekstre')",
                      (iid,)).fetchone()
    if not r or held_doc(iid) is None:
        raise KeyError(iid)
    doc = json.loads(r["doc"])
    data = db.read_file("uploads", doc.get("file")) or b""
    if r["kind"] == "dokum":
        owners = [doc["owner_name"]] if doc.get("owner_name") else db.card_holders()
        if doc.get("owner_name"):
            db.remember_owner_name(doc["owner_name"])
        st = db.import_bank_rows(doc["rows"], account=doc.get("bank") or "", owners=owners, iban=doc.get("iban"))
        db.auto_link()
        if data:
            fname = doc.get("filename") or "dokum.pdf"
            doc_id = db.save_bank_document(data, fname if fname.lower().endswith(".pdf") else fname + ".pdf",
                                           doc.get("bank") or None)
            if doc_id:
                db.link_bank_document(doc_id, [x.get("hash") for x in doc["rows"]])
                with db.connect() as c:
                    c.execute("UPDATE bank_documents SET verified = 2 WHERE id = ?", (doc_id,))  # kullanıcı onayladı
        if data and any(st[k] for k in ("gelir", "gider", "kart_odemesi", "virman")):
            doc_reads.charge(data)
        out = {"ok": True, "kind": "dokum", "stats": st,
               "message": f"{st['gelir']} gelir, {st['gider']} gider, {st['virman']} {terms.OWN_TRANSFER} kaydedildi"}
    else:
        stmt = statement_from_json(doc["statement"])
        if db.statement_exists(data, stmt):
            out = {"ok": True, "kind": "ekstre", "message": "Bu ekstre zaten vardı"}
        else:
            sid = db.save_statement(data, doc.get("filename") or "ekstre.pdf", stmt)
            db.confirm_statement(sid)  # kullanıcı rakamları görüp kaydetti: 'Kontrol et' tekrar sorulmaz
            if data:
                doc_reads.charge(data)
            db.auto_link()
            db.auto_mark_paid()
            db.auto_update_cycle()
            out = {"ok": True, "kind": "ekstre", "statement_id": sid, "message": "Ekstre kaydedildi"}
    share.close(iid, "kaydedildi")
    return out


# ---------------------------------------------------------------- Yapılacaklar

def todos(today: date | None = None) -> list[dict]:
    """Doğrulanamayan belgeler: onay bekleyen e-posta belgeleri, kontrol edilmemiş son yüklemeler."""
    from datetime import timedelta

    from app import share

    today = today or date.today()
    cut = (today - timedelta(days=RECENT_DAYS)).isoformat()
    out = []
    with db.connect() as c:
        share._init(c)
        held_rows = db.rows(c.execute("SELECT id, filename, kind, doc, created_at FROM shared_inbox "
                                      "WHERE status = 'bekliyor' AND kind IN ('dokum', 'ekstre') ORDER BY id"))
        docs = db.rows(c.execute("SELECT id, filename, bank, uploaded_at FROM bank_documents WHERE verified = 0 "
                                 "AND uploaded_at >= ?", (cut,)))
    for r in held_rows:
        doc = json.loads(r["doc"])
        what = "Hesap dökümü" if r["kind"] == "dokum" else "Kart ekstresi"
        out.append({"id": f"gelen:{r['id']}", "kind": "belge_onay", "severity": "warn",
                    "title": f"{what} onay bekliyor",
                    "text": f"E-postayla gelen {r['filename'] or 'belge'} okundu ama "
                            f"{terms.lower_first(doc.get('verification_text') or terms.EQ_BAD + '.')}"
                            " Kaydetmeden önce Gelen kutusu'nda rakamlara bak; doğruysa kaydet.",
                    "cta": "Gelen kutusu", "route": "gelen", "action": "inbox_review", "due": (r["created_at"] or "")[:10],
                    "period": (r["created_at"] or "")[:7], "inbox_id": r["id"], "tag": terms.EQ_BAD})
    from app import book

    hidden = set((book.active() or {}).get("hidden_accounts") or [])
    with db.connect() as c:
        accs = {r[0]: r[1] for r in c.execute("SELECT id, account_id FROM bank_documents WHERE verified = 0")}
    for d in docs:
        if accs.get(d["id"]) in hidden:
            continue
        out.append({"id": f"dokum_kontrol:{d['id']}", "kind": "belge_dogrulanamadi", "severity": "warn",
                    "title": "Dökümdeki rakamları kontrol et",
                    "text": f"{d['bank'] or 'Hesap'} dökümü ({d['filename'] or 'dosya'}) okunurken bakiyeler birbirini "
                            f"tutmadı. Hareketleri belgeyle karşılaştır; doğruysa '{terms.CONFIRM_OK}' de.",
                    "cta": terms.CTA_LOOK, "confirm_label": terms.CONFIRM_OK, "route": "hesaplar", "action": "document_confirm", "due": (d["uploaded_at"] or "")[:10],
                    "period": (d["uploaded_at"] or "")[:7], "document_id": d["id"], "tag": terms.EQ_BAD})
    for s in db.list_statements():
        if not s.get("needs_check") or s.get("source") == "excel" or (s.get("uploaded_at") or "")[:10] < cut:
            continue
        out.append({"id": f"ekstre_kontrol:{s['id']}", "kind": "belge_dogrulanamadi", "severity": "warn",
                    "title": f"{s['bank']} ekstresini kontrol et",
                    "text": statement_text(s.get("equation_diff"), s.get("reconciliation_diff"))
                            + f" Doğruysa '{terms.CONFIRM_OK}' de.",
                    "cta": terms.CTA_LOOK, "confirm_label": terms.CONFIRM_OK, "route": "ekstreler", "action": "statement_confirm",
                    "due": (s.get("uploaded_at") or "")[:10], "period": s["period"], "statement_id": s["id"],
                    "tag": terms.EQ_BAD})
    return out
