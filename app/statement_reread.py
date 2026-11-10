"""Kayıtlı kart ekstresini şablonla yeniden oku: yapay zekânın okuduğu ekstrede satır eksik / fazla / işareti ters ama
aynı belgeyi yapay zekâsız okuyan bir şablon (ya da bankaya özel okuyucu, ya da metinden denklemi tutan kural) var.

Kime: statements.source 'yapay_zeka' ya da boş (eski kayıt), PDF (Excel/CSV değil) ekstreler; kayıtlı işlemlerden
kurulan ekstre denklemi (önceki − ödemeler + harcamalar = dönem borcu) kuruşu kuruşuna TUTMUYOR. "Kontrol et" uyarısı
çıkmayanlar da (İş Bankası örneği: özetteki ödeme tutarı doğru ama ödeme satırı listede yok) — burada ölçüt işlemlerin
kendisi.

Şablon okuması kabul ölçütü canlı yüklemedekiyle aynı (card_reader._fixed_read): şablon kendi başına denklemi tutar,
tarihler tutarlı; aynı belge (dönem borcu, kesim, son ödeme aynı; satırların en az %80'i tarih + tutarla — işaretsiz —
eşleşiyor); birleştirilmiş sonuç da denklemi tutar.

Kullanıcı düzenlemeleri korunur: satırlar SİLİNİP YENİDEN YAZILMAZ. Tarih + tutarla eşleşen kayıtlı satır olduğu gibi
kalır (id aynı: elle kategori, not, açıklama, gizli, silindi işareti, "kim harcadı", bölmeler, elle kayıtla eşleşme,
zekât/sadaka bağı); işareti ters okunmuşsa yalnız tutar ve tür değişir (elle kategori yoksa kurallarla yeniden
kategorilenir). Şablonda olup kayıtta olmayan satır eklenir (kurallarla kategorilenir), kayıtta olup şablonda olmayan
satır silinir. Harcama olmaktan çıkan ya da silinen satıra bağlı elle kayıt eşleşmesi sync.unmatch ile ayrılır (elle
kayıt yeniden kendi tutarıyla sayılır). Uygulama tek "geri al" kaydıdır (app/undo.py; POST /api/imports/{id}/undo).
Ekstrenin kaynağı 'sablon' olur.

Yapay zekâ çağrılmaz, okuma hakkı / kota sayılmaz, şablon sayaçlarına (kullanım/hata) ve topluluğa dokunulmaz.

Önbellek: hesap pahalı (PDF depodan okunur, metne çevrilir, şablonlar denenir). Bağlantı açılışında YAPILMAZ;
zamanlayıcının turunda (jobs.scheduler → tick) parça parça ve önizleme isteğinde (GET .../reread-preview) yapılır.
Sonuç defterin settings tablosunda "reread:stmt:<id>" (yalnız sayılar ve imzalar; geri almaya girmez). Yapılacaklar
maddesi ve ekstre listesindeki bayrak yalnız önbellekten okunur; kayıtlı satırlar değiştiyse (imza) gösterilmez.
"""
import hashlib
import json
import logging
import time
from collections import Counter
from datetime import date, datetime, timezone

from app import db, money
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND, Transaction

log = logging.getLogger("hesapkitap.statement_reread")

VERSION = 2                # hesap kuralı değişirse artır: bütün önbellek yeniden hesaplanır
KEY = "reread:stmt:"       # defterin settings'i: + ekstre id
REFRESH_DAYS = 7           # okunamayan / farksız sonuç bu kadar günde bir yeniden denenir (metin çıkarma iyileşebilir)
TICK_EVERY_S = 15 * 60     # tam tarama sıklığı (şablon listesi değişince hemen)
BUDGET_S = 8.0             # tur başına süre bütçesi
EPS = 0.05

_last = {"at": 0.0, "fp": None}


class RereadError(ValueError):
    """Kullanıcıya gösterilecek neden (409)."""


# ---------------------------------------------------------------- aday ekstreler ve imzalar

_CANDIDATE_SQL = ("SELECT * FROM statements WHERE COALESCE(source, '') IN ('', 'yapay_zeka') "
                  "AND lower(COALESCE(filename, '')) NOT LIKE '%.xls%' AND lower(COALESCE(filename, '')) NOT LIKE '%.csv'")


def _candidates(c, sid: int | None = None) -> list[dict]:
    q = _CANDIDATE_SQL + (" AND id = ?" if sid is not None else "") + " ORDER BY id"
    return db.rows(c.execute(q, (sid,) if sid is not None else ()))


def _txns(c, sid: int) -> list[dict]:
    return db.rows(c.execute("SELECT * FROM transactions WHERE statement_id = ? ORDER BY id", (sid,)))


def rows_sig(st: dict, txns: list[dict]) -> str:
    """Kayıtlı ekstre + satırların (tarih, tutar, tür, silindi) imzası: kategori / not düzeltmesi imzayı değiştirmez."""
    raw = json.dumps([VERSION, st["id"], st["file_hash"], st.get("source"), st["period_end"], st.get("due_date"),
                      money.kurus(st.get("total_debt") or 0), money.kurus(st.get("previous_balance") or 0),
                      [(t["id"], t["txn_date"], money.kurus(t["amount"]), t["txn_type"], int(t.get("is_deleted") or 0))
                       for t in txns]], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _card_templates() -> list[dict]:
    from app import templates

    try:
        return [t for t in templates.all_templates() if t["spec"].get("kind") == "card"]
    except Exception:  # noqa: BLE001 - sistem veritabanı yoksa şablon da yok
        return []


def fingerprint(tpls: list[dict] | None = None) -> str:
    """Kart şablonları listesinin imzası (yeni öğrenilen / devre dışı kalan şablon sonucu değiştirir)."""
    tpls = _card_templates() if tpls is None else tpls
    raw = json.dumps(sorted((t["id"], bool(t.get("disabled"))) for t in tpls))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- şablon okuması ve fark

def _date(v):
    return date.fromisoformat(v) if isinstance(v, str) and v else v


def _equation(prev: float, total: float, txns) -> float:
    charges = sum(t["amount"] for t in txns if t["txn_type"] != PAYMENT)
    paid = -sum(t["amount"] for t in txns if t["txn_type"] == PAYMENT)
    return round((prev or 0.0) - paid + charges - (total or 0.0), 2)


def _abs_keys(rows) -> Counter:
    return Counter((str(r["txn_date"])[:10], money.kurus(abs(r["amount"]))) for r in rows)


def _tx(t: Transaction) -> dict:
    return {"txn_date": t.txn_date.isoformat(), "description": t.description, "amount": round(t.amount, 2),
            "txn_type": t.txn_type, "card": t.card, "card_holder": t.card_holder or "", "orig_amount": t.orig_amount,
            "orig_currency": t.orig_currency, "installment_no": t.installment_no,
            "installments_left": t.installments_left, "installment_left_amount": t.installment_left_amount,
            "points": t.points, "note": t.note}


def _local_reads(text: str):
    """Aynı metnin yapay zekâsız okumaları (kaynak, Statement): kayıtlı kart şablonları, bankaya özel okuyucu, metinden
    öğrenme (kaydetmeden). Sayaç / kayıt / topluluk yan etkisi yok."""
    from app import card_reader
    from app.parsers import DETECTORS

    bank = card_reader.bank_of(text)
    for t in _card_templates():
        spec = t["spec"]
        if t.get("disabled") or not card_reader.matches(text, spec):
            continue
        try:
            res = card_reader.apply(text, spec, spec.get("bank") or bank)
        except Exception:  # noqa: BLE001 - bozuk / uyumsuz şablon
            res = None
        if res and res[1] and card_reader.plausible(res[0]):
            yield "sablon", res[0]
    for detect, parse in DETECTORS:
        try:
            if detect(text) and (st := parse(text)) is not None:
                yield "parser", st
        except Exception:  # noqa: BLE001
            pass
    if not card_reader._cid_text(text):
        try:
            got = card_reader.learn(text, bank)
        except Exception:  # noqa: BLE001
            got = None
        if got and got[0].get("bank"):
            yield "sablon", got[1]


def plan_for(st: dict, txns: list[dict], read) -> dict | None:
    """Kayıtlı ekstre (st, satırlar) + aynı belgenin yapay zekâsız okuması (read: Statement) → uygulanacak fark ya da
    None (başka belge, az örtüşme, sonuç denklemi tutmuyor). Eşleşme merge_fix ile aynı: (tarih, |tutar|), önce aynı
    işaretli satır."""
    from app import card_reader

    if money.ne(read.total_debt, st["total_debt"]) or read.period_end != _date(st["period_end"]) \
            or read.due_date != _date(st.get("due_date")):
        return None
    tmpl = [_tx(t) for t in read.transactions]
    common = sum((_abs_keys(txns) & _abs_keys(tmpl)).values())
    if common < 0.8 * max(len(txns), len(tmpl), 1):
        return None
    pool: dict[tuple, list[dict]] = {}
    for r in txns:
        pool.setdefault((r["txn_date"][:10], money.kurus(abs(r["amount"]))), []).append(r)
    card_ok = card_reader._card_key(st["card_no"]) is not None
    kept, changed, added = [], [], []
    for t in tmpl:
        same = pool.get((t["txn_date"], money.kurus(abs(t["amount"]))))
        if not same:
            added.append({**t, "card": st["card_no"] if card_ok else t["card"]})
            continue
        a = next((x for x in same if (x["amount"] < 0) == (t["amount"] < 0)), same[0])
        same.remove(a)
        if (a["amount"] < 0) == (t["amount"] < 0):
            kept.append(a)
        else:
            changed.append({"row": a, "amount": t["amount"], "txn_type": t["txn_type"]})
    removed = [r for rs in pool.values() for r in rs]
    _payment_types(st, kept, changed, added)
    after = kept + [{**c["row"], "amount": c["amount"], "txn_type": c["txn_type"]} for c in changed] + added
    d_after = _equation(st.get("previous_balance"), st["total_debt"], after)
    if abs(d_after) > EPS:
        return None
    from app import statement_fix
    if statement_fix.evaluate(st, statement_fix.aggregate(after))["ok"] is False:
        return None  # satır denklemi tutuyor ama özetle (iade / ödeme ayrımı, harcama toplamı) çelişiyor: önerme
    visible = len(added) + sum(not c["row"].get("is_deleted") for c in changed) \
        + sum(not r.get("is_deleted") for r in removed)
    return {"kept": kept, "changed": changed, "added": added, "removed": removed, "after": after,
            "equation_after": d_after, "rows": visible}


def _payment_types(st: dict, kept: list[dict], changed: list[dict], added: list[dict]) -> None:
    """Şablon ödemeyi iade diye okumuş olabilir (açıklamada "ödeme" kelimesi yok: "HESAPTAN AKTARIM"): eklenecek
    iade satırı, kayıtlı özetin "ödemeler"inde listede karşılığı olmayan tutara kuruşu kuruşuna eşitse ödemedir.
    Satır denklemi ikisini ayıramaz; ayrılmazsa ödeme harcamadan düşülürdü."""
    paid = -sum(r["amount"] for r in kept if r["txn_type"] == PAYMENT) \
        - sum(c["amount"] for c in changed if c["txn_type"] == PAYMENT) \
        - sum(a["amount"] for a in added if a["txn_type"] == PAYMENT)
    gap = round((st.get("payments") or 0.0) - paid, 2)
    if gap < EPS:
        return
    hit = [a for a in added if a["txn_type"] == REFUND and abs(-a["amount"] - gap) <= EPS]
    if len(hit) == 1:
        hit[0]["txn_type"] = PAYMENT


def _text_of(st: dict) -> tuple[str | None, str | None]:
    from app.parsers import PasswordRequired, _text_with_passwords

    try:
        data = db.read_file("uploads", f"{st['file_hash']}.pdf")
    except Exception:  # noqa: BLE001 - depo hatası
        return None, "no_file"
    if not data:
        return None, "no_file"
    try:
        return _text_with_passwords(data, db.pdf_passwords())[0], None
    except PasswordRequired:
        return None, "password"
    except Exception:  # noqa: BLE001 - bozuk PDF / sınır
        return None, "unreadable"


def compute(st: dict, txns: list[dict]) -> tuple[dict | None, str]:
    """(fark, neden kodu). Kodlar: ok | equation_ok (kayıtlı okuma zaten tutuyor) | no_file | password | unreadable |
    no_read (yapay zekâsız tutan okuma yok / başka belge) | no_change (görünür fark yok)."""
    if abs(_equation(st.get("previous_balance"), st["total_debt"], txns)) <= EPS:
        return None, "equation_ok"
    text, why = _text_of(st)
    if text is None:
        return None, why
    best = None
    for src, read in _local_reads(text):
        try:
            p = plan_for(st, txns, read)
        except Exception:  # noqa: BLE001 - tek okuma diğerlerini durdurmasın
            log.warning("yeniden okuma farkı kurulamadı", exc_info=True)
            p = None
        if p is not None:
            best = {**p, "via": src}
            break
    if best is None:
        return None, "no_read"
    if not best["rows"]:
        return None, "no_change"
    return best, "ok"


# ---------------------------------------------------------------- önbellek

def _get_cache(sid: int) -> dict | None:
    try:
        v = json.loads(db.get_setting(f"{KEY}{sid}") or "null")
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def _put_cache(sid: int, val: dict | None) -> None:
    cur = db.get_setting(f"{KEY}{sid}")
    new = json.dumps(val, sort_keys=True) if val is not None else None
    if cur != new:
        db.set_setting(f"{KEY}{sid}", new)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def refresh(st: dict, txns: list[dict], fp: str | None = None) -> dict:
    """Bir ekstreyi hesapla ve önbelleğe yaz; önbellek kaydını döndürür."""
    p, why = compute(st, txns)
    val = {"v": VERSION, "sig": rows_sig(st, txns), "fp": fp or fingerprint(), "at": _now(), "reason": why,
           "rows": p["rows"] if p else 0, "via": p["via"] if p else None}
    _put_cache(st["id"], val)
    return val


def _stale(cache: dict | None, sig: str, fp: str) -> bool:
    if not cache or cache.get("v") != VERSION or cache.get("sig") != sig or cache.get("fp") != fp:
        return True
    if cache.get("reason") in ("ok", "equation_ok"):
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(cache["at"])
    except (KeyError, TypeError, ValueError):
        return True
    return age.days >= REFRESH_DAYS


def scan(fp: str, deadline: float | None = None) -> dict:
    """Açık defterde önbelleği eski aday ekstreleri hesapla. {checked, found, finished}."""
    with db.connect() as c:
        cands = _candidates(c)
        rows = {s["id"]: _txns(c, s["id"]) for s in cands}
        keys = [r[0] for r in c.execute("SELECT key FROM settings WHERE key LIKE ?", (KEY + "%",))]
    alive = {f"{KEY}{s['id']}" for s in cands}
    for k in keys:  # silinen / şablona geçen ekstrenin önbelleği
        if k not in alive:
            db.set_setting(k, None)
    out = {"checked": 0, "found": 0, "finished": True}
    for s in cands:
        if not _stale(_get_cache(s["id"]), rows_sig(s, rows[s["id"]]), fp):
            continue
        if deadline is not None and out["checked"] and time.monotonic() >= deadline:
            out["finished"] = False
            break
        val = refresh(s, rows[s["id"]], fp)
        out["checked"] += 1
        out["found"] += val["reason"] == "ok"
    return out


def tick(now: float | None = None, spaces=None) -> dict | None:
    """Zamanlayıcı turu: TICK_EVERY_S'de bir (şablon listesi değişince hemen) bütün defterlerde önbelleği eski aday
    ekstreler hesaplanır; BUDGET_S dolunca sonraki tur kaldığı yerden. İş yoksa None."""
    from app import jobs

    now = time.monotonic() if now is None else now
    fp = fingerprint()
    if _last["fp"] == fp and now - _last["at"] < TICK_EVERY_S:
        return None
    deadline = time.monotonic() + BUDGET_S
    total = {"checked": 0, "found": 0}
    seen = set()
    import os

    for path, user in (jobs.spaces() if spaces is None else spaces):
        key = os.path.abspath(path) if path else None
        if key in seen:
            continue
        seen.add(key)
        try:
            with jobs.book_space(path, user):
                got = scan(fp, deadline)
        except Exception:  # noqa: BLE001 - bir defter diğerlerini durdurmasın
            log.exception("yeniden okuma taraması başarısız")
            continue
        total["checked"] += got["checked"]
        total["found"] += got["found"]
        if not got["finished"]:
            return {**total, "finished": False}  # _last güncellenmez: sonraki tur sürdürür
    _last.update(at=now, fp=fp)
    return {**total, "finished": True} if total["checked"] else None


# ---------------------------------------------------------------- görünürlük (Yapılacaklar, ekstre listesi)

def _can_apply() -> bool:
    """Ortak defterde ekstreyi değiştirmek yükleme gibi: yalnız sahip ve tam yetkili üye."""
    from app import book

    m = book.member()
    return not m or book.role(m) == "full"


def reminder_id(sid: int) -> str:
    return f"ekstre_oku:{sid}"


def available() -> dict[int, dict]:
    """{ekstre id: önbellek kaydı}: yeniden okunabilir, kayıtlı satırlar hesaplandığından beri değişmemiş, kişi
    "Şimdi değil" dememiş ve görebildiği (ortak defter süzgeci) ekstreler. Yalnız önbellekten; hesap yapmaz."""
    from app import book, reminders

    if not _can_apply():
        return {}
    r = book.active()
    if r and "ekstreler" in r["hidden_pages"]:
        return {}
    with db.connect() as c:
        keys = {int(k[len(KEY):]): v for k, v in c.execute("SELECT key, value FROM settings WHERE key LIKE ?",
                                                             (KEY + "%",)) if k[len(KEY):].isdigit()}
        cands = [s for s in _candidates(c) if s["id"] in keys]
        rows = {s["id"]: _txns(c, s["id"]) for s in cands}
    state, today = reminders._state(), reminders.today()
    out = {}
    for s in book.filter_statements(cands):
        try:
            cache = json.loads(keys[s["id"]])
        except ValueError:
            continue
        if not isinstance(cache, dict) or cache.get("reason") != "ok" or not cache.get("rows") \
                or cache.get("v") != VERSION or cache.get("sig") != rows_sig(s, rows[s["id"]]):
            continue
        if reminders._hidden(reminder_id(s["id"]), state, today):
            continue
        out[s["id"]] = {**cache, "statement": s}
    return out


def _last4(card_no: str) -> str:
    digits = "".join(ch for ch in card_no or "" if ch.isdigit())
    return (digits or card_no or "")[-4:]


def rows_text(n: int) -> str:
    return f"{n} satır farklı"


def todos() -> list[dict]:
    """Yapılacaklar: "Ekstre yeniden okunabilir" — <Banka> ••1234 · N satır farklı · Farkı gör."""
    out = []
    for sid, v in available().items():
        s = v["statement"]
        out.append({"id": reminder_id(sid), "kind": "ekstre_oku", "severity": "warn",
                    "title": "Ekstre yeniden okunabilir",
                    "text": f"{s['bank']} ••{_last4(s['card_no'])} · {rows_text(v['rows'])}",
                    "cta": "Farkı gör", "route": "ekstreler", "action": "statement_reread",
                    "due": (s.get("uploaded_at") or s["period_end"] or "")[:10], "period": s["period_end"][:7],
                    "statement_id": sid, "bank": s["bank"], "last4": _last4(s["card_no"]), "rows": v["rows"]})
    return out


def annotate(statements: list[dict]) -> list[dict]:
    """GET /api/statements: yeniden okunabilen ekstrede reread_rows (görünür fark sayısı); diğerlerinde null."""
    try:
        av = available()
    except Exception:  # noqa: BLE001 - liste bozulmasın
        log.exception("yeniden okuma bayrağı okunamadı")
        av = {}
    try:
        from app import statement_fix
        can_fix = statement_fix.can_edit()
    except Exception:  # noqa: BLE001
        can_fix = False
    for s in statements:
        s["reread_rows"] = av[s["id"]]["rows"] if s["id"] in av else None
        s["can_fix"] = can_fix  # satır ekle / düzelt (statement_fix): sahip ve tam yetkili üye
    return statements


# ---------------------------------------------------------------- önizleme ve uygulama

_LABELS = {
    ("changed", REFUND): "İade olarak düzeltilecek", ("changed", PAYMENT): "Ödeme olarak düzeltilecek",
    ("changed", PURCHASE): "Harcama olarak düzeltilecek", ("changed", FEE): "Faiz / ücret olarak düzeltilecek",
    ("added", PAYMENT): "Eksik ödeme eklenecek", ("added", REFUND): "Eksik iade eklenecek",
    ("added", PURCHASE): "Eksik harcama eklenecek", ("added", FEE): "Eksik faiz / ücret eklenecek",
}
REMOVED_LABEL = "Fazla okunan satır silinecek"
UNLINK_NOTE = "Bağlı elle kaydın ayrılır, kendi tutarıyla sayılır"


def _fmt_day(iso: str) -> str:
    d = _date(iso[:10])
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def _line(r: dict, amount: float | None = None) -> str:
    return f"{r['description']} · {_fmt_day(r['txn_date'])} · {money.tl(r['amount'] if amount is None else amount)}"


def _linked(c, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    return {t: e for e, t in c.execute(f"SELECT id, linked_txn_id FROM entries WHERE linked_txn_id IN ({q})", ids)}


def _totals(st: dict, rows) -> dict:
    live = [r for r in rows if not r.get("is_deleted")]

    def s(kind):
        return round(sum(r["amount"] for r in live if r["txn_type"] == kind), 2)
    return {"spend": s(PURCHASE), "refunds": s(REFUND), "payments": s(PAYMENT), "fees": s(FEE),
            "count": len(live), "equation_diff": _equation(st.get("previous_balance"), st["total_debt"], rows)}


def _load(sid: int) -> tuple[dict, list[dict]]:
    """Görülebilir aday ekstre ve satırları; değilse KeyError (404) / RereadError (409)."""
    from app import book

    with db.connect() as c:
        s = c.execute("SELECT * FROM statements WHERE id = ?", (sid,)).fetchone()
        if not s or not book.filter_statements([dict(s)]):
            raise KeyError(sid)
        s = dict(s)
        if (s.get("source") or "") not in ("", "yapay_zeka") or not _candidates(c, sid):
            raise RereadError("Bu ekstre zaten yapay zekâsız okunmuş")
        return s, _txns(c, sid)


def preview(sid: int) -> dict:
    """GET /api/statements/{id}/reread-preview: güncel hesap (önbelleği de tazeler)."""
    from app import book

    try:
        s, txns = _load(sid)
    except RereadError:
        return {"available": False, "reason": "not_ai", "statement_id": sid}
    p, why = compute(s, txns)
    _put_cache(sid, {"v": VERSION, "sig": rows_sig(s, txns), "fp": fingerprint(), "at": _now(), "reason": why,
                     "rows": p["rows"] if p else 0, "via": p["via"] if p else None})
    from app import statement_fix

    hide = book.hide_fees()
    ev = statement_fix.evaluate(s, statement_fix.aggregate(txns), hide)
    # kartın gösterdiği fark (statement_fix): totals_before.equation_diff satır denklemidir, eksik ödeme satırını da
    # sayar (ekstre kartı "₺50 fark" derken burada önceki borç kadar büyük bir rakam görünmesin)
    base = {"statement_id": sid, "bank": s["bank"], "card_no": s["card_no"], "last4": _last4(s["card_no"]),
            "period_end": s["period_end"], "total_debt": s["total_debt"], "totals_before": _totals(s, txns),
            "fix_before": {"ok": ev["ok"], "diff": ev["diff"]}}
    if not p:
        return {**base, "available": False, "reason": why, "rows": 0, "diff": {"added": [], "removed": [], "changed": []},
                "items": [], "totals_after": None, "equation_ok_after": False}
    hide_fees = hide

    def shown(r: dict) -> bool:  # ortak defter: gizli satır ve (kurala göre) faiz satırı listede gösterilmez
        return not r.get("is_deleted") and not r.get("hidden") and not (hide_fees and r["txn_type"] == FEE)
    with db.connect() as c:
        links = _linked(c, [x["row"]["id"] for x in p["changed"]] + [r["id"] for r in p["removed"]])
    items, diff = [], {"added": [], "removed": [], "changed": []}
    for ch in p["changed"]:
        r = ch["row"]
        if not shown(r) and not shown({**r, "txn_type": ch["txn_type"]}):
            continue
        unlink = r["id"] in links and ch["txn_type"] != PURCHASE
        rec = {"id": r["id"], "before": {k: r[k] for k in ("txn_date", "description", "amount", "txn_type")},
               "after": {"txn_date": r["txn_date"], "description": r["description"], "amount": ch["amount"],
                         "txn_type": ch["txn_type"]},
               "label": _LABELS[("changed", ch["txn_type"])], "text": _line(r, ch["amount"]), "linked": unlink}
        diff["changed"].append(rec)
        items.append({"kind": "changed", **rec, **({"note": UNLINK_NOTE} if unlink else {})})
    for a in p["added"]:
        if hide_fees and a["txn_type"] == FEE:
            continue
        rec = {k: a[k] for k in ("txn_date", "description", "amount", "txn_type")}
        rec.update(label=_LABELS[("added", a["txn_type"])], text=_line(a))
        diff["added"].append(rec)
        items.append({"kind": "added", **rec})
    for r in p["removed"]:
        if not shown(r):
            continue
        rec = {"id": r["id"], **{k: r[k] for k in ("txn_date", "description", "amount", "txn_type")},
               "label": REMOVED_LABEL, "text": _line(r), "linked": r["id"] in links}
        diff["removed"].append(rec)
        items.append({"kind": "removed", **rec, **({"note": UNLINK_NOTE} if rec["linked"] else {})})
    after_ok = statement_fix.evaluate(s, statement_fix.aggregate(p["after"]), hide)["ok"] is not False
    return {**base, "available": True, "reason": "ok", "rows": p["rows"], "sig": rows_sig(s, txns), "via": p["via"],
            "diff": diff, "items": items, "totals_after": _totals(s, p["after"]),
            "equation_ok_after": abs(p["equation_after"]) <= EPS, "fix_after_ok": after_ok}


def apply(sid: int, sig: str | None = None) -> dict:
    """POST /api/statements/{id}/reread-apply: farkı uygula (tek geri al kaydı). sig verildiyse önizlemedeki imzayla
    aynı olmalı (arada satırlar değiştiyse 409)."""
    from app import sync, undo
    from app.categorize import categorize_sub

    s, txns = _load(sid)
    if sig and sig != rows_sig(s, txns):
        raise RereadError("Ekstre önizlemeden sonra değişti; farkı yeniden aç")
    p, why = compute(s, txns)
    if not p:
        _put_cache(sid, None)
        raise RereadError("Bu ekstre için şablonla yeniden okuma artık yok")
    label = f"Ekstre yeniden okundu: {s['bank']} ••{_last4(s['card_no'])} {s['period_end']}"
    summary = {"statement_id": sid, "added": len(p["added"]), "changed": len(p["changed"]),
               "removed": len(p["removed"]), "rows": p["rows"]}
    with undo.batch(label) as b:
        with db.connect() as c:
            links = _linked(c, [x["row"]["id"] for x in p["changed"] if x["txn_type"] != PURCHASE]
                            + [r["id"] for r in p["removed"]])
        unlinked = 0
        for _tid, eid in links.items():  # eşleşmeyi geri al: elle kayıt eski tutarıyla, kart satırının eski kategorisi
            try:
                sync.unmatch(eid)
                unlinked += 1
            except KeyError:
                pass
        with db.connect() as c:
            rules = db.load_rules_sub(c)
            for ch in p["changed"]:
                r = ch["row"]
                cur = c.execute("SELECT category_manual FROM transactions WHERE id = ?", (r["id"],)).fetchone()
                if cur and not cur["category_manual"]:
                    cat, sub = categorize_sub(r["description"], ch["txn_type"], rules)
                    c.execute("UPDATE transactions SET amount = ?, txn_type = ?, category = ?, subcategory = ? WHERE id = ?",
                              (ch["amount"], ch["txn_type"], cat, sub, r["id"]))
                else:
                    c.execute("UPDATE transactions SET amount = ?, txn_type = ? WHERE id = ?",
                              (ch["amount"], ch["txn_type"], r["id"]))
            for a in p["added"]:
                row = dict(a)
                row["category"], sub = categorize_sub(a["description"], a["txn_type"], rules)
                if sub:
                    row["subcategory"] = sub
                row["statement_id"] = sid
                cols = ", ".join(row)
                c.execute(f"INSERT INTO transactions ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
            if p["removed"]:
                ids = [r["id"] for r in p["removed"]]
                c.execute(f"DELETE FROM transactions WHERE id IN ({','.join('?' * len(ids))})", ids)
                db.drop_orphan_spenders(c)
            c.execute("UPDATE statements SET source = 'sablon' WHERE id = ?", (sid,))
        summary["unlinked"] = unlinked
        summary["linked"] = db.auto_link()  # eklenen harcama satırı elle girilmiş karşılığını bulsun
        b["summary"] = summary
    _put_cache(sid, None)
    log.info("ekstre şablonla yeniden okundu: satır=%d via=%s", p["rows"], p["via"])
    return {"ok": True, "undo_id": b["id"], **summary}
