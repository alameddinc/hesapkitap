"""Aylık özet, birleşik işlem listesi, ileriye dönük plan ve uyarılar.

Ay kavramı:
- Kart işlemleri ekstrenin kesim ayına yazılır (o ay ödenen ekstre).
- Elle girilen kalemler kendi tarihinin ayına yazılır.
"""
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from app import db, money, rawnote, terms
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND

import copy as _copy
import threading

# Hesap önbelleği: aynı veritabanı durumu için ağır hesaplar (tüm işlemler, plan, ödemeler) bir kez yapılır.
# Anahtar veritabanı dosyasının değişim zamanı + boyutu: her yazmada kendiliğinden geçersiz olur.
_CACHE: dict = {}
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()
_MISS = object()


def _db_stamp() -> tuple:
    return db.stamp()


def _memo(fn):
    def wrap(*args, **kwargs):
        u = db.current_user()  # ortak defter üyesi süzülmüş veri görür: sonuç sahibininkiyle karışmasın
        viewer = u["id"] if u and u.get("member_of") else None
        key = (fn.__name__, _db_stamp(), viewer, date.today(), repr(args), repr(sorted(kwargs.items())))
        # Sonuç yerel değişkende tutulur: başka bir iş parçacığı arada _CACHE.clear() yapsa da KeyError olmaz.
        hit = _CACHE.get(key, _MISS)
        if hit is _MISS:
            # aynı anda gelen istekler (Özet + ay özeti) aynı ağır hesabı ikişer kez yapmasın: ikincisi birinciyi bekler
            with _LOCKS_GUARD:
                lock = _LOCKS.setdefault(key, threading.Lock())
            with lock:
                hit = _CACHE.get(key, _MISS)
                if hit is _MISS:
                    hit = fn(*args, **kwargs)
                    with _LOCKS_GUARD:  # temizlik ve ekleme tek kilit altında (eşzamanlı clear/insert yarışmasın)
                        if len(_CACHE) > 128:
                            _CACHE.clear()
                            _LOCKS.clear()
                        _CACHE[key] = hit
        # çağıran sonucu değiştirse de önbellek bozulmasın
        if isinstance(hit, list) and all(isinstance(x, dict) for x in hit):
            return [dict(x) for x in hit]
        return _copy.deepcopy(hit)
    wrap.__name__, wrap.__doc__, wrap.__wrapped__ = fn.__name__, fn.__doc__, fn
    return wrap
from app.textnorm import legal_key, merchant_name

INCOME = "gelir"
EXPENSE = "gider"
DEBT = "borc"  # borç hareketi: gelir/gider toplamına girmez, "Kalan"ı etkiler

_DEBT_STEP = {"verildi": "borç verildi", "alindi": "borç alındı", "tahsil": "tahsil edildi", "odendi": "borç ödendi"}


def _debt_row(d: dict) -> dict:
    base = {"uid": d["uid"], "source": "debt", "id": d["debt_id"], "date": d["date"], "month": d["month"],
            "merchant": d["person"], "card": None, "account": None, "bank": None, "installment": None, "fx": None,
            "note": None, "deleted": False, "debt_id": d["debt_id"], "counterparty": d["person"]}
    if d["step"] == "vazgecildi":  # tahsil edilemeyen alacak gider, silinen borç gelir; para el değiştirmedi
        kind = EXPENSE if d["direction"] == "alacak" else INCOME
        return {**base, "kind": kind, "amount": d["amount"], "category": "Borç / Alacak",
                "description": f"{d['person']} · " + ("tahsil edilemedi" if kind == EXPENSE else "borç silindi"),
                "debt_flow": d["amount"] if kind == EXPENSE else -d["amount"]}
    return {**base, "kind": DEBT, "flow": d["flow"], "amount": d["amount"], "category": "Borç / Alacak",
            "description": f"{d['person']} · {_DEBT_STEP[d['step']]}"}
CARD_SPEND = (PURCHASE, REFUND)
SAVINGS_CATEGORY = db.JAR_BUY_CATEGORY  # kumbaraya altın/döviz alımı: para harcanmadı, birikime gitti → gider sayılmaz


def _saving(r: dict) -> bool:
    return r.get("category") == SAVINGS_CATEGORY and r["kind"] in (*CARD_SPEND, EXPENSE)


def _spends(r: dict, kinds: tuple = ()) -> bool:
    """Gider sayılan satır (birikim hariç)."""
    return r["kind"] in (kinds or (*CARD_SPEND, FEE, EXPENSE)) and not _saving(r)
SUBSCRIPTION_CATEGORIES = {"Dijital Abonelik", "AI & Yazılım Abonelik"}
VARIABLE_LOOKBACK = 6  # değişken harcama tahmini için son kaç dönem (medyan: tek uç ay tahmini şişirmesin)


# ---------------------------------------------------------------- helpers

def ym(d: date | str) -> str:
    return str(d)[:7]


def current_period(today: date | None = None) -> str:
    """İçinde bulunulan dönem (ay dönümü ayarına göre)."""
    return db.period_of(today or date.today())


def add_months(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    y, m = divmod((y * 12 + m - 1) + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def month_range(start: str, end: str) -> list[str]:
    out, m = [], start
    while m <= end:
        out.append(m)
        m = add_months(m, 1)
    return out


def card_label(t: dict) -> str:
    holder = (t.get("card_holder") or "").title()
    return f"{t['card']} {holder}".strip()


def is_active(r: dict, month: str) -> bool:
    return r["start_month"] <= month and (not r["end_month"] or month <= r["end_month"])


def _round(x: float) -> float:
    return money.money(x) + 0.0  # tek kural: yarım yukarı (app/money.py); + 0.0: -0,00 görünmesin


# ---------------------------------------------------------------- unified rows

def _display_merchant(desc: str, aliases: dict | None) -> str:
    """Ekstredeki ad; fişten öğrenilmiş takma ad varsa marka (NEW TASTE GIDA → ESPRESSOLAB)."""
    m = merchant_name(desc)
    if aliases:
        return aliases.get(legal_key(desc)) or aliases.get(m) or m
    return m


def _sub_state(t: dict, states: dict) -> str | None:
    m = merchant_name(t["description"])
    if m in states:
        return states[m]["status"]
    return "aktif" if t["category"] in SUBSCRIPTION_CATEGORIES and t["txn_type"] == PURCHASE else None


def _card_row(t: dict, states: dict | None = None, aliases: dict | None = None) -> dict:
    inst = None
    if t["installment_no"] is not None and t["installments_left"] is not None:
        inst = f"{t['installment_no']}/{t['installment_no'] + t['installments_left']}"
    return {
        "uid": f"c{t['id']}", "source": "card", "id": t["id"],
        "date": t["txn_date"], "month": t["period"],
        "description": t["description"],
        "merchant": _display_merchant(t["description"], aliases),
        "amount": t["amount"], "kind": t["txn_type"], "category": t["category"], "subcategory": t.get("subcategory"),
        "card": card_label(t), "account": t["account"], "bank": t["bank"], "hidden": bool(t.get("hidden")),
        "installment": inst,
        "fx": f"{t['orig_amount']:g} {t['orig_currency']}" if t["orig_currency"] else None,
        "note": t["note"], "deleted": bool(t["is_deleted"]),
        "subscription": _sub_state(t, states) if states is not None else None,
    }


def _raw(e: dict) -> str | None:
    m = e.get("match")
    if m and m.get("source") == "dokum" and m.get("description"):
        return m["description"]
    return rawnote.raw_of(e.get("note"))


def _vat_lines(v) -> list[dict]:
    if not v:
        return []
    from app import vat

    return vat.lines_of(v)


def _entry_row(e: dict) -> dict:
    if e.get("debt_id"):  # borç verildi/alındı/ödendi: gelir-gider değil, sadece eldeki para
        return {**_entry_row({**e, "debt_id": None}), "kind": DEBT, "flow": 1 if e["kind"] == INCOME else -1,
                "category": "Borç / Alacak", "subcategory": None, "debt_id": e["debt_id"]}
    return {
        "uid": f"m{e['id']}", "source": "manual", "id": e["id"],
        "date": e["entry_date"], "month": e["month"],
        "description": e["description"], "merchant": e["description"],
        "amount": e["amount"], "kind": e["kind"], "category": e["category"] or "Diğer",
        # alt kategori (ana kategoriye bağlı; boş olabilir): istemci "Market › Temizlik" gösterir
        "subcategory": e.get("subcategory") if e["category"] else None,
        "card": None, "account": None, "bank": None, "installment": None, "fx": None,
        "note": e["note"], "recurring_id": e["recurring_id"], "skipped": bool(e["skipped"]), "deleted": False,
        # banka açıklaması (ham metin) ve kullanıcının kendi notu: app/rawnote.py (eşleşmiş elle kayıtta dökümün metni)
        "raw": _raw(e), "note_user": rawnote.user_note(e["note"]),
        "payment": e["payment"], "linked_txn_id": e["linked_txn_id"], "counterparty": e.get("counterparty"), "location": e.get("location"),
        "wallet_id": e.get("wallet_id"), "account_id": e.get("account_id"), "installments": e.get("installments") or 1,
        "card_last4": e.get("card_last4"), "hidden": bool(e.get("hidden")), "created_by": e.get("created_by"),
        # fiş fotoğrafı (GET /api/entries/{id}/receipt) ve hesap dökümü (GET /api/bank-import/documents/{id}/file)
        "receipt_file": e.get("receipt_file"), "bank_doc_id": e.get("bank_doc_id"),
        # fişin vergi bilgisi (app/vat.py): unvan, VKN, vergi dairesi, KDV toplamı ve oran oran dökümü
        "legal_name": e.get("legal_name"), "tax_no": e.get("tax_no"), "tax_office": e.get("tax_office"),
        "vat_total": e.get("vat_total"), "vat_lines": _vat_lines(e.get("vat_lines")),
        # eşitleme (app/sync.py): dökümdeki/ekstredeki karşılığı — "Dökümle eşleşti · Garanti · 28 Eki", geri alınabilir
        "match": e.get("match"),
        # kredi kartıyla ödendi ve ekstredeki karşılığı bulundu → kart satırı sayılır, bu satır sayılmaz
        "linked": e["linked_txn_id"] is not None,
        "awaiting_statement": e["payment"] == "kredi_karti" and e["linked_txn_id"] is None and e["kind"] == EXPENSE,
    }


CARD_ESTIMATE_CATEGORY = "Kart harcaması (ekstresiz)"


def _expected_period(p: dict, cut_days: dict[str, int]) -> str:
    """Bu ödemenin karşılığı olan (beklenen) ekstrenin dönemi (kesim ayı, YYYY-MM).
    Kartın yüklü ekstresi varsa kesim gününden: ödeme gününden önceki son kesim; yoksa ödemeden ~10 gün önce."""
    d = date.fromisoformat(p["pay_date"][:10])
    day = cut_days.get(p.get("card_last4") or "")
    if day:
        y, m = d.year, d.month
        for _ in range(3):
            last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
            cut = date(y, m, min(day, last))
            if cut <= d:
                return ym(cut)
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return ym(d - timedelta(days=10))


def _card_payment_estimate_row(p: dict, month: str, cut_days: dict[str, int] | None = None) -> dict:
    bank = re.sub(r"\s+(KATILIM\s+)?BANKASI.*$", "", p.get("account") or "", flags=re.I).strip()
    card = f"{bank} …{p['card_last4']}".strip() if p.get("card_last4") else (bank or "Kredi kartı")
    return {"uid": f"cp{p['id']}", "source": "estimate", "id": p["id"], "date": p["pay_date"], "month": month,
            "description": f"{card} ödemesi (ekstre yok, tahmini harcama)", "merchant": card, "amount": p["amount"],
            "kind": EXPENSE, "category": CARD_ESTIMATE_CATEGORY, "card": card, "account": None, "bank": bank or None,
            "installment": None, "fx": None, "note": None, "recurring_id": None, "skipped": False, "deleted": False,
            "estimated": True, "estimate_kind": "card_payment", "card_last4": p.get("card_last4"),
            "expected_period": _expected_period(p, cut_days or {})}


def _installment_row(e: dict, k: int, month: str, amount: float) -> dict:
    n = int(e["installments"])
    row = _entry_row(e)
    if k == 0:
        return {**row, "amount": amount, "installment": f"1/{n}", "original_amount": e["amount"]}
    return {**row, "uid": f"m{e['id']}-{k}", "month": month, "amount": amount, "installment": f"{k + 1}/{n}",
            "original_amount": e["amount"], "date": db.occurrence_date(month, int(e["entry_date"][8:10])),
            "installment_of": e["id"]}


def _pending_row(r: dict, month: str, overrides: dict | None = None) -> dict:
    return {
        "uid": f"r{r['id']}-{month}", "source": "recurring", "id": r["id"],
        "date": db.occurrence_date(month, db.day_at(r, month)), "month": month,
        "description": r["description"], "merchant": r["description"],
        "amount": db.planned_amount(r, month, overrides), "template_amount": r["amount"], "kind": r["kind"], "category": r["category"] or "Diğer",
        "card": None, "account": None, "bank": None, "installment": None, "fx": None,
        "note": r["note"], "recurring_id": r["id"], "pending": True, "deleted": False,
    }


# banka dökümündeki maaş satırı ("MAAS 2026 OCAK ÖDEMESI", "ISÇI UCRET 2026 TEMMUZ ÜCRET ÖDEM…"): kategorisi
# "Diğer Gelir" kalsa da o ayın maaşı yatmış sayılır, üstüne tahmini maaş eklenmez (çift sayım)
SALARY_TEXT = re.compile(r"MAAS|UCRETODEM|PERSONELODEM|BORDRO")  # _fold boşlukları atar


def _is_salary(r: dict) -> bool:
    return (r["category"] or "") == "Maaş" or bool(r.get("gross_amount")) or "MAAS" in _fold(r["description"])


def _salary_entry(e: dict, salary_ids: set) -> bool:
    return (e["category"] == "Maaş" or (e.get("recurring_id") in salary_ids)
            or bool(SALARY_TEXT.search(_fold(e["description"] or ""))))


def pending_recurring(month: str, recurring: list[dict], entries: list[dict]) -> list[dict]:
    done = {e["recurring_id"] for e in entries if e["recurring_id"] and e["month"] == month}
    ov = db.recurring_overrides()
    # geçmiş ayda maaş geliri hiç yoksa (maaşın yattığı hesabın dökümü yüklenmemiş): maaş kalemi tahmini gelir sayılır;
    # o ayın gerçek maaşı (döküm) gelince kendiliğinden kalkar
    salary_ids = {r["id"] for r in recurring if _is_salary(r)}
    salary_seen = any(e["kind"] == INCOME and e["month"] == month and not e["skipped"] and _salary_entry(e, salary_ids)
                      for e in entries)
    past = month < current_period()
    out = []
    for r in recurring:
        if not is_active(r, month) or r["id"] in done:
            continue
        row = _pending_row(r, month, ov)
        is_salary = _is_salary(r)
        if r["kind"] == INCOME and is_salary and salary_seen:  # o ayın maaşı dökümden gelmiş: "bekliyor" gösterme
            continue
        if past and r["kind"] == INCOME and is_salary and not salary_seen:
            # maaş tahmini GERÇEK gelire sayılır (ayrılmaz): dökümü yüklenmemiş/elle giren kullanıcıda geçmiş aylar
            # gelirsiz görünmesin. Ayrılan yalnız ekstresiz kart ödemesi tahmini (gider şişmesin). Kullanıcı kararı 30.10.
            row = {**row, "pending": False, "estimated": False, "salary_estimate": True, "source": "estimate",
                   "description": f"{r['description']} (tahmini, döküm yok)"}
        out.append(row)
    return out


def transactions(month: str | None, include_deleted: bool = False, include_estimates: bool = False) -> list[dict]:
    """Birleşik işlem listesi. TAHMİNİ satırlar (estimated: ekstresiz kart ödemesi) gerçek değildir: varsayılan listede
    ve hiçbir gerçek toplamda (özet, bütçe, istatistik, kıyas, dönem özeti) yer almaz; ayrı gösterilmek için
    include_estimates=True ya da estimates().
    Değişmez I9'un bilinçli istisnası (kullanıcı kararı 30.10): dökümü olmayan GEÇMİŞ ayın maaş tahmini estimated
    DEĞİLDİR (salary_estimate=True); gerçek gelire sayılır, aksi hâlde döküm yüklemeyen kullanıcıda geçmiş aylar
    gelirsiz görünür. O ayın gerçek maaşı (döküm ya da elle kayıt) gelince tahmin kendiliğinden kalkar.
    Test: tests/test_card_estimate.py::test_salary_estimated_for_months_without_statement."""
    rows = _all_rows(month, include_deleted)
    return rows if include_estimates else [r for r in rows if not r.get("estimated")]


def estimates(month: str | None) -> list[dict]:
    """Sadece tahmini satırlar (en yeni önce)."""
    return [r for r in _all_rows(month, False) if r.get("estimated")]


def estimate_item(r: dict) -> dict:
    """Özet/istatistik yanıtındaki tahmini kalem: ne, ne kadar, hangi ekstre bekleniyor."""
    card = r.get("estimate_kind") == "card_payment"
    return {"uid": r["uid"], "source": "card_payment" if card else "recurring", "kind": r["kind"],
            "title": r["card"] if card else r["merchant"], "amount": r["amount"], "date": r["date"], "month": r["month"],
            "card": r.get("card") if card else None, "card_last4": r.get("card_last4"), "bank": r.get("bank"),
            "expected_period": r.get("expected_period"), "recurring_id": r.get("recurring_id")}


def _estimate_totals(rows: list[dict]) -> dict:
    return {"estimated_expense": _round(sum(r["amount"] for r in rows if r["kind"] == EXPENSE)),
            "estimated_income": _round(sum(r["amount"] for r in rows if r["kind"] == INCOME))}


@_memo
def _all_rows(month: str | None, include_deleted: bool = False) -> list[dict]:
    states, aliases = db.subscription_states(), db.merchant_aliases()
    card = [_card_row(t, states, aliases) for t in db.card_transactions(include_deleted) if not month or t["period"] == month]
    entries = db.list_entries(month)
    rows = []
    for e in entries:
        if int(e.get("installments") or 1) <= 1:
            rows.append(_entry_row(e))
    # taksitli kart harcamaları: her taksit kendi döneminde (önceki aylarda yapılanların bu aya düşen taksiti dahil)
    for e in db.installment_entries():
        for k, m, amt in db.installment_parts(e):
            if month and m != month:
                continue
            if e["linked_txn_id"] and k > 0:  # ekstreyle eşleşti: sonraki taksitler ekstrelerden gelir
                continue
            rows.append(_installment_row(e, k, m, amt))
    # ekstresi olmayan dönemlerin kart ödemesi: o dönemin kart harcaması bilinmiyor, ödeme tutarı TAHMİNİ satır olur
    # (gerçek toplamlara girmez, ayrı gösterilir); ekstre yüklenince kendiliğinden kalkar.
    # Kartın yüklenen ilk ekstresinden eski ödemeler (before_first: bankanın arşivi, çoğunlukla 12 aydan eski) hiç gösterilmez.
    cut_days: dict[str, int] = {}
    for st in db.list_statements():  # en yeni önce: kartın son kesim günü
        cut_days.setdefault(st["card_no"][-4:], int(st["period_end"][8:10]))
    for p in db.card_payments_without_statement():
        if p.get("before_first"):
            continue
        pm = db.period_of(p["pay_date"])
        if not month or pm == month:
            rows.append(_card_payment_estimate_row(p, pm, cut_days))
    out = _apply_splits(card + rows) + [_debt_row(d) for d in db.debt_rows(month)]
    if month:
        out += pending_recurring(month, db.list_recurring(), entries)
    else:  # tüm aylar (trend, devreden): dökümü olmayan geçmiş aylardaki maaş tahminleri de girsin
        cur = current_period()
        recs = db.list_recurring()
        by_m: dict[str, list] = defaultdict(list)
        for e in entries:
            by_m[e["month"]].append(e)
        starts = [r["start_month"] for r in recs if r["kind"] == INCOME]
        m = min(starts) if starts else cur
        while m < cur:
            out += [r for r in pending_recurring(m, recs, by_m.get(m, [])) if r.get("estimated") or r.get("salary_estimate")]
            m = add_months(m, 1)
    # güvenlik ağı: alt kategori yalnız kendi ana kategorisinin listesindeyse gösterilir (kategori başka yoldan
    # değiştiyse — kişiye kategori, eşleşmeyi geri alma — eski alt kategori yeni ana kategoriye taşınmaz)
    valid = {(c, n) for c, names in db.subcategories_map().items() for n in names}
    for r in out:
        if r.get("subcategory") and (r["category"], r["subcategory"]) not in valid:
            r["subcategory"] = None
    return sorted(out, key=lambda r: (r["date"], r["uid"]), reverse=True)


def _apply_splits(rows: list[dict]) -> list[dict]:
    """Bölünmüş harcama: ana satır kalan tutarla, her alt kalem kendi kategorisinde ayrı satır.
    Alt kalem ana satırın tarih/kart/eşleşme bilgilerini taşır; toplamlar ve kategoriler doğal olarak doğru çıkar."""
    sm = db.splits_map()
    if not sm:
        return rows
    out = []
    for r in rows:
        key = ("card", r["id"]) if r["source"] == "card" else ("entry", r["id"]) if r["source"] == "manual" else None
        parts = sm.get(key) if key and r["kind"] in (PURCHASE, EXPENSE) else None
        if not parts:
            out.append(r)
            continue
        used = sum(p["amount"] for p in parts)
        # güvenlik ağı (I6): alt kalemler ana tutarı aşıyorsa ana satır eksiye düşmez, alt kalemler oranla gösterilir
        # (veri db.fit_overflowing_splits ile düzeltilir); toplam her zaman ana tutar
        scale = r["amount"] / used if money.gt(used, r["amount"]) and used else 1.0
        out.append({**r, "amount": _round(max(0.0, r["amount"] - used)), "split_total": _round(min(used, r["amount"])),
                    "original_amount": r["amount"]})
        for p in parts:
            child = {**r, "uid": f"{r['uid']}s{p['id']}", "amount": _round(p["amount"] * scale), "category": p["category"],
                     "subcategory": None,  # alt kalem kendi kategorisinde; ana satırın alt kategorisi ona geçmez
                     "description": f"{r['description']} · {p['description'] or p['category']}",
                     "split_of": r["uid"], "split_origin": p["origin"]}
            if p.get("debt_id"):  # ortak harcamada başkasının payı: gider değil, ona alacak (para senden çıktı)
                child.update(kind=DEBT, flow=-1, debt_id=p["debt_id"], counterparty=p.get("person"),
                             description=f"{r['description']} · {p.get('person')} payı")
            out.append(child)
    return out


# ---------------------------------------------------------------- monthly summary

def _totals(rows: list[dict]) -> dict:
    live = [r for r in rows if not r.get("deleted") and not r.get("skipped") and not r.get("pending") and not r.get("linked")]
    income = sum(r["amount"] for r in live if r["kind"] == INCOME)
    savings = sum(r["amount"] for r in live if _saving(r))
    live = [r for r in live if not _saving(r)]
    card_spend = sum(r["amount"] for r in live if r["kind"] in CARD_SPEND)
    fees = sum(r["amount"] for r in live if r["kind"] == FEE)
    manual = sum(r["amount"] for r in live if r["kind"] == EXPENSE)
    # elle girilmiş kart harcaması, ekstresi henüz gelmedi (manual_expense'in içinde; ekstre gelince kart satırıyla eşleşir)
    card_pending = sum(r["amount"] for r in live if r["kind"] == EXPENSE and r.get("awaiting_statement"))
    refunds = -sum(r["amount"] for r in live if r["kind"] == REFUND)
    expense = card_spend + fees + manual
    # borç verip almak gelir/gider değil ama eldeki parayı değiştirir
    debt_flow = sum(r["flow"] * r["amount"] for r in live if r["kind"] == DEBT) + sum(r.get("debt_flow", 0) for r in live)
    out = {k: _round(v) for k, v in dict(income=income, card_spend=card_spend, fees=fees, manual_expense=manual,
                                         refunds=refunds, expense=expense, net=income - expense, debt_flow=debt_flow,
                                         savings=savings, cash_net=income - expense - savings + debt_flow,
                                         card_pending=card_pending).items()}
    # istemci kendisi hesaplamasın (S1): Özet 'Kalan' = net; Sade görünüm 'Kalan' = net − birikime ayrılan;
    # gider kırılımında elle girilmiş ama ekstresi beklenen kart harcaması 'Kart' tarafında
    out["left"] = out["net"]
    out["left_after_savings"] = _round(income - expense - savings)
    out["card_spend_incl_pending"] = _round(card_spend + card_pending)
    out["manual_expense_excl_card_pending"] = _round(manual - card_pending)
    return out


def counts(r: dict) -> tuple[bool, bool]:
    """Satır gerçek gidere / gelire sayılır mı (_totals ile aynı kural, tek yer): silinen, atlanan, bekleyen, eşleşmiş
    (linked) ve tahmini satır hiçbirine girmez; birikim (altın/döviz alımı) gider değil; iade gideri düşürür (eksi tutar);
    kart ödemesi (odeme) ve borç hareketi ikisine de girmez."""
    if r.get("deleted") or r.get("skipped") or r.get("pending") or r.get("linked") or r.get("estimated"):
        return False, False
    return _spends(r), r["kind"] == INCOME


TOTALS_DEFINITIONS = {  # metinler app/terms.py terimleriyle
    "expense": "Gider: kart harcaması + faiz/ücret + elle gider − iadeler. Birikime ayrılan (altın/döviz alımı), kart "
               "ödemesi, borç hareketi ve bekleyen kayıtlar dahil değil.",
    "income": "Gelir: gerçekleşen gelir kayıtları (bekleyenler dahil değil).",
    "left": f"{terms.LEFT}: gelir − gider.",
    "left_after_savings": f"{terms.LEFT_AFTER_SAVINGS}: gelir − gider − birikime ayrılan (altın/döviz alımı).",
    "cash_net": "Eldeki paraya etkisi: gelir − gider − birikime ayrılan ± borç/alacak hareketleri.",
}


def _group(rows: list[dict], key: str) -> list[dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for r in rows:
        agg[r[key]]["amount"] += r["amount"]
        agg[r[key]]["count"] += 1
    out = [{"name": k, "amount": _round(v["amount"]), "count": v["count"]} for k, v in agg.items() if money.gt(v["amount"], 0)]
    return sorted(out, key=lambda x: -x["amount"])


def _group_categories(rows: list[dict]) -> list[dict]:
    """Kategori dağılımı (_group ile aynı) + alt kategori kırılımı: ana kategoride alt kategorili satır varsa
    children [{name, amount, count}] (en büyükten); alt kategorisi olmayan kalan {name: null} ile en sonda.
    Alt kategorisi hiç olmayan ana kategoride children anahtarı yoktur (eski yanıtla aynı)."""
    out = _group(rows, "category")
    subs: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("subcategory"):
            subs[r["category"]].append(r)
    for g in out:
        if not subs.get(g["name"]):
            continue
        mine = [r for r in rows if r["category"] == g["name"]]
        kids = _group([r for r in mine if r.get("subcategory")], "subcategory")
        rest = [r for r in mine if not r.get("subcategory")]
        left = sum(r["amount"] for r in rest)
        if rest and money.gt(left, 0):
            kids.append({"name": None, "amount": _round(left), "count": len(rest)})
        if kids:
            g["children"] = kids
    return out


def reference_month(today: date | None = None) -> str:
    """Kıyas/öneri için 'son tam ay': en son yüklenen ekstrenin ayı (yoksa geçen ay)."""
    periods = [st["period"] for st in db.list_statements()]
    return max(periods) if periods else add_months(ym(today or date.today()), -1)


@_memo  # her ekran /api/meta'yı çağırıyor: tüm kart işlemleri ve kayıtlar her istekte okunmasın
def available_months() -> list[str]:
    ms = {t["period"] for t in db.card_transactions()} | {e["month"] for e in db.list_entries()}
    ms.add(current_period())
    return sorted(ms, reverse=True)


@_memo  # veri değişmedikçe (dosya damgası) ve gün dönmedikçe tekrar hesaplanmaz
def summary(month: str, today: date | None = None) -> dict:
    rows = transactions(month)
    live = [r for r in rows if not r.get("skipped") and not r.get("pending") and not r.get("linked")]
    expenses = [r for r in live if _spends(r)]
    spend = [r for r in live if _spends(r, CARD_SPEND)]

    statements = [s for s in db.list_statements() if s["period"] == month]
    pending = [r for r in rows if r.get("pending")]

    # Son 12 ay trend (gelir vs gider)
    all_rows = transactions(None)
    by_month: dict[str, list] = defaultdict(list)
    for r in all_rows:
        by_month[r["month"]].append(r)
    # tahmini satırlar (ekstresiz kart ödemesi, dökümsüz maaş) gerçek toplamlara girmez; ayrı katman olarak gelir
    est_by_month: dict[str, list] = defaultdict(list)
    for r in estimates(None):
        est_by_month[r["month"]].append(r)
    trend_months = sorted(set(by_month) | set(est_by_month))[-12:]
    trend = [{"month": m, **{k: v for k, v in _totals(by_month.get(m, [])).items() if k in ("income", "expense", "net")},
              **_estimate_totals(est_by_month.get(m, []))}
             for m in trend_months]

    # Önceki dönemlerden artan para bu döneme devreder (eksi devretmez: açık, kart borcu olarak zaten görünür)
    carry = 0.0
    for m in sorted(by_month):
        if m >= month:
            break
        carry = max(0.0, carry + _totals(by_month[m])["cash_net"])
    totals = _totals(rows)
    totals["carry_in"] = _round(carry)
    totals["balance"] = _round(carry + totals["cash_net"])
    open_try = [d for d in db.list_debts(open_only=True) if d["asset"] == "TRY"]
    totals["receivable"] = _round(sum(d["remaining"] for d in open_try if d["direction"] == "alacak"))
    totals["payable"] = _round(sum(d["remaining"] for d in open_try if d["direction"] == "borc"))
    est = estimates(month)
    totals.update(_estimate_totals(est))

    from app import book

    totals["fees_hidden"] = book.hide_fees()  # ortak defterde faiz gizli: istemci faiz kutusunu göstermesin
    by_category = _group_categories(expenses)
    exp = expected(month, today=today)
    return {
        "month": month,
        # dönemin adı (başladığı ay; anahtar değil) ve aralığı: istemciler aynı kuralı kullanır (db.period_name_month)
        "period": {"name": period_name(month, year=False), "label": period_name(month), "span": period_span(month),
                   "start": period_bounds(month)[0].isoformat(), "end": period_bounds(month)[1].isoformat()},
        "totals": totals,
        "vs_prev": _vs_prev(month, totals, by_month, today or date.today()),
        # "expected": Beklenen'in (i) metni, yalnız beklenen varken (o dönemde hangi bekleyenler varsa onları sayar)
        "definitions": {**TOTALS_DEFINITIONS, **({"expected": _expected_text(exp["parts"])} if exp else {})},
        # gerçek toplamlara (expense, by_category, budgets, trend.expense) GİRMEYEN tahmini kalemler: ekstre bekleniyor
        "estimated_items": [estimate_item(r) for r in sorted(est, key=lambda r: (r["date"], r["uid"]))],
        "budgets": budgets(month, by_category),
        "pending": {
            "income": _round(sum(r["amount"] for r in pending if r["kind"] == INCOME)),
            "expense": _round(sum(r["amount"] for r in pending if r["kind"] == EXPENSE)),
            "count": len(pending),
        },
        "by_category": by_category,
        "by_merchant": _group(spend + [r for r in live if _spends(r, (EXPENSE,))], "merchant")[:15],
        "by_card": _group(spend, "card"),
        "statements": statements,
        "trend": trend,
        "insights": insights(month, today=today),
        # bu dönemin taksitleri (yetenek "summary_installments"): harcamaya EKLENMEZ, kırılımdır — installments_due
        "installments": {k: v for k, v in installments_due(month, today=today).items() if k != "items"},
        # dönem sonu tahmini (yetenek "summary_expected"; finance.expected = Planlama'nın bu ayki satırı). Toplamlara
        # (totals, by_category, budgets, trend) hiçbir şey EKLEMEZ; geçmiş dönemde null
        "expected": exp,
    }


def _vs_prev(month: str, totals: dict, by_month: dict, today: date) -> dict | None:
    """Özet'teki "geçen döneme göre": başlıktaki gider (totals.expense) ile AYNI sayım kuralı (_totals) ve aynı dönem
    tanımı. İçinde bulunulan dönemde geçen dönemin AYNI GÜNÜNE kadarki gider (ör. 5. gün ↔ geçen dönemin 5. günü);
    biten dönemde iki dönemin tamamı. Geçen dönemin satırı tarihine göre süzülür (taksit dilimi gibi eski tarihli satır
    dönemin başında sayılır). incomplete: geçen dönemde kart ekstresi vardı, bu dönemde henüz yok (rakam ekstreyle artar)."""
    prev = add_months(month, -1)
    a, b = period_bounds(month)
    pa, pb = period_bounds(prev)
    if today < a:
        return None  # gelecek dönem
    rows = by_month.get(prev, [])
    if today <= b:
        day = (today - a).days  # 0 = dönemin ilk günü
        cut = min(pb, pa + timedelta(days=day))
        rows = [r for r in rows if str(r["date"])[:10] <= cut.isoformat()]
        basis = "same_day"
    else:
        day, basis = (b - a).days, "full"
    before = _totals(rows)["expense"]
    now = totals["expense"]
    card = lambda rs: any(r.get("source") == "card" for r in rs)  # noqa: E731
    return {"basis": basis, "day": day + 1, "prev_month": prev, "prev_name": period_name(prev, year=False),
            "current": now, "prev": _round(before), "diff": _round(now - before), "change": _change(now, before),
            "incomplete": basis == "same_day" and card(by_month.get(prev, [])) and not card(by_month.get(month, []))}


# ---------------------------------------------------------------- tasarruf: abonelikler + kısma hedefleri

def subscriptions() -> list[dict]:
    """Abonelik kategorisindeki işyerleri: son tutar, görüldüğü aylar, iptal durumu, iptalden sonra çekim var mı."""
    states = db.subscription_states()
    by_m: dict[str, list] = defaultdict(list)
    for t in db.card_transactions():
        if t["txn_type"] == PURCHASE and t["installment_no"] is None and (
                t["category"] in SUBSCRIPTION_CATEGORIES or merchant_name(t["description"]) in states):
            by_m[merchant_name(t["description"])].append(t)
    out = []
    for m, ts in by_m.items():
        last_period = max(t["period"] for t in ts)
        st = states.get(m)
        after = [t for t in ts if st and t["txn_date"] > st["changed_at"]]
        monthly = _round(sum(t["amount"] for t in ts if t["period"] == last_period))
        before = [t["period"] for t in ts if t["period"] < last_period]  # bir önceki görüldüğü dönem (zam bildirimi)
        prev_monthly = _round(sum(t["amount"] for t in ts if t["period"] == max(before))) if before else None
        new_amount = st["new_amount"] if st and st["status"] == "dusuruldu" else None
        # paket düşürüldükten sonraki dönemlerde yeni tutarın %15 fazlasından çok çekildiyse: düşürme tutmamış
        over = 0.0
        if new_amount is not None:
            per: dict[str, float] = defaultdict(float)
            for t in after:
                per[t["period"]] += t["amount"]
            over = _round(sum(v - new_amount for v in per.values() if v > new_amount * 1.15))
        out.append({
            "merchant": m, "category": ts[0]["category"],
            "monthly": monthly, "new_amount": new_amount, "prev_monthly": prev_monthly, "last_period": last_period,
            "saving": _round(monthly if st and st["status"] == "iptal" else max(0.0, monthly - new_amount) if new_amount is not None else 0),
            "overcharged": over,
            "last_seen": max(t["txn_date"] for t in ts), "months": len({t["period"] for t in ts}),
            "fx": next((f"{t['orig_currency']}" for t in ts if t["orig_currency"]), None),
            "status": st["status"] if st else "aktif", "cancelled_at": st["changed_at"] if st else None,
            "charged_after_cancel": _round(sum(t["amount"] for t in after)) if after and st["status"] == "iptal" else 0,
        })
    return sorted(out, key=lambda x: (x["status"] != "aktif", -x["monthly"]))


def _spend_for(kind: str, name: str, month: str) -> float:
    rows = [r for r in transactions(month) if not r.get("pending") and not r.get("skipped") and not r.get("linked")
            and not r.get("deleted") and _spends(r, (*CARD_SPEND, EXPENSE))]
    key = "category" if kind == "category" else "merchant"
    return _round(sum(r["amount"] for r in rows if r[key] == name))


def goals_review(today: date | None = None) -> list[dict]:
    """Her hedef için başlangıçtan bu aya kadar ay ay sonuç. Bu ay 'devam' (henüz bitmedi)."""
    today = today or date.today()
    now = current_period(today)
    out = []
    for g in db.list_goals():
        months = []
        if g["start_month"] <= now:
            for m in month_range(g["start_month"], now):
                spent = _spend_for(g["kind"], g["name"], m)
                status = "devam" if m == now else ("tuttu" if spent <= g["target"] else "tutmadi")
                months.append({"month": m, "spent": spent, "status": status, "saved": _round(g["baseline"] - spent)})
        out.append({**g, "monthly_saving": _round(max(0, g["baseline"] - g["target"])), "months": months})
    return out


# ---------------------------------------------------------------- bütçeler

def budgets_totals(status: list[dict]) -> dict:
    """Bütçe başlığı (istemci toplamasın): limit, harcanan, kalan, yüzde, aşan kategori sayısı."""
    limit = sum(b["limit"] for b in status)
    spent = sum(b["spent"] for b in status)
    return {"limit": _round(limit), "spent": _round(spent), "remaining": _round(limit - spent),
            "pct": round(spent / limit * 100, 1) if limit else 0.0, "over": sum(1 for b in status if b["spent"] > b["limit"]),
            "count": len(status)}


@_memo
def category_spend(month: str) -> list[dict]:
    """Dönemin kategori bazında gideri: summary(month)["by_category"] ile aynı süzgeç ve aynı toplam, ama özetin
    geri kalanı (tüm geçmiş, trend, içgörüler) hesaplanmadan. Bütçe ekranı ve öneriler bunu kullanır."""
    live = [r for r in transactions(month) if not r.get("pending") and not r.get("skipped") and not r.get("linked")]
    return _group_categories([r for r in live if _spends(r)])


def budgets(month: str, by_category: list[dict] | None = None) -> list[dict]:
    limits = db.list_budgets()
    if not limits:
        return []
    if by_category is None:
        by_category = category_spend(month)
    spent = {g["name"]: g["amount"] for g in by_category}
    out = []
    for cat, limit in limits.items():
        s = spent.get(cat, 0.0)
        out.append({"category": cat, "limit": limit, "spent": _round(s), "remaining": _round(limit - s),
                    "pct": round(s / limit * 100, 1)})
    return sorted(out, key=lambda b: -b["pct"])


# ---------------------------------------------------------------- ortak defter: kim ne harcadı (Aile)

SHARED = "ortak"  # harcayanı belli olmayan gider (kart ekstresi, banka dökümü, sabit kalem): tek "Ortak" dilimi


class Spenders:
    """Ortak defterde satırın harcayanı (Kim ne harcadı, ortak bütçe kırılımı, İşlemler ?who=, satırdaki spender):
    tek kural, istek başına bir kez okunur. Öncelik:
      1. kayıtta elle seçilen (spender_overrides; kişi ya da açıkça Ortak) — sabit kalemde (recurring) seçim
         kalemin her ayına geçer: bekleyen satıra da, o kalemden oluşmuş elle kayda da (kaydın kendi seçimi yoksa)
      2. elle kayıt: ekleyen (created_by)
      3. kart işlemi: eşleştiği elle kaydın seçimi, o yoksa o kaydı ekleyen
      4. kart işlemi: kartın kişisi (card_spenders, "kartı kişiye bağla")
      5. Ortak
    Bulunan kişi defterde değilse (ayrılan / çıkarılan üye): kaydı onundur, raporda "Ayşe (ayrıldı)" olarak kalır
    (geçmiş raporlar değişmesin); yalnız kartın kişisi ayrıldıysa kart bağsız sayılır (Ortak). Ayrılan kişiye yeni
    atama yapılmaz (PUT /api/spender yalnız defterdeki kişiler). people: book.shared_people() (boşsa hepsi Ortak)."""

    def __init__(self, people: list[dict]):
        self.ids = {p["id"] for p in people}
        self.names = {f"u{p['id']}": p["name"] for p in people}
        self._left: dict[str, str] = {}  # ayrılan kişinin etiketi (istek başına bir kez okunur)
        self.cards = db.card_spenders()
        self.overrides = db.spender_overrides()
        self.creators: dict[int, int] = {}  # kart işlemi id → eşleşen elle kaydı ekleyen
        self.linked: dict[int, int] = {}    # kart işlemi id → eşleşen elle kayıt id
        with db.connect() as c:
            for t, eid, u in c.execute("SELECT linked_txn_id, id, created_by FROM entries WHERE linked_txn_id IS NOT NULL"):
                self.linked[t] = eid
                if u is not None:
                    self.creators[t] = u

    def _uid(self, r: dict) -> int | None:
        ov, src, rid = self.overrides, r.get("source"), r.get("id")
        if (src, rid) in ov:
            return ov[(src, rid)]
        if src == "manual":
            rec = r.get("recurring_id")
            if rec and ("recurring", rec) in ov:
                return ov[("recurring", rec)]
            return r.get("created_by")
        if src == "card":
            eid = self.linked.get(rid)
            if eid is not None and ("manual", eid) in ov:
                return ov[("manual", eid)]
            if rid in self.creators:
                return self.creators[rid]
            uid = self.cards.get(r.get("account"))
            return uid if uid in self.ids else None  # kartın kişisi ayrıldı: kart bağsız
        return None

    def of(self, r: dict) -> str:
        """Satırın harcayanı: "u<id>" (defterdeki ya da ayrılmış kişi) ya da SHARED."""
        uid = self._uid(r)
        return SHARED if uid is None else f"u{uid}"

    def left(self, key: str) -> bool:
        """Bu kişi defterden ayrılmış mı (raporda adı "(ayrıldı)" ile)."""
        return key != SHARED and key not in self.names

    def name(self, key: str) -> str:
        """Raporda görünen ad: defterdeki kişinin adı, ayrılanın "Ayşe (ayrıldı)", yoksa "Ortak"."""
        if key == SHARED:
            return "Ortak"
        if key in self.names:
            return self.names[key]
        if key not in self._left:
            from app import system

            u = system.user(int(key[1:])) if key[1:].isdigit() else None
            ad = (u.get("username") or "").split("@")[0] if u else ""
            self._left[key] = f"{ad} (ayrıldı)" if ad else "Ayrılan üye"
        return self._left[key]

    def is_set(self, r: dict) -> bool:
        """Bu satırda (kaydın kendisinde) elle seçim var mı? İstemci "otomatik" / "elle seçildi" gösterir."""
        return (r.get("source"), r.get("id")) in self.overrides

    def card(self, card_no: str) -> str | None:
        """Kartın kişisi ("u<id>"); bağlı değilse ya da kişi defterden ayrıldıysa None."""
        uid = self.cards.get(card_no)
        return f"u{uid}" if uid in self.ids else None


def spender_row(source: str, rid: int) -> dict | None:
    """Harcayanı bulmak için yeterli satır (source, id, created_by, recurring_id, account); kayıt yoksa None.
    Görünürlük (ortak defter üyesi) çağıranda denetlenir."""
    with db.connect() as c:
        if source == "manual":
            e = c.execute("SELECT id, created_by, recurring_id FROM entries WHERE id = ?", (rid,)).fetchone()
            return {"source": "manual", **dict(e)} if e else None
        if source == "card":
            t = c.execute("SELECT t.id, s.card_no AS account FROM transactions t JOIN statements s ON s.id = t.statement_id "
                          "WHERE t.id = ?", (rid,)).fetchone()
            return {"source": "card", **dict(t)} if t else None
        if source == "recurring":
            r = c.execute("SELECT id FROM recurring WHERE id = ?", (rid,)).fetchone()
            return {"source": "recurring", "id": r["id"]} if r else None
    return None


def in_spend(r: dict) -> bool:
    """category_spend ile aynı küme: bütçe kırılımı ve kişi başı rapor toplamı Özet'in kategori toplamıyla aynı çıkar."""
    return not r.get("pending") and not r.get("skipped") and not r.get("linked") and _spends(r)


def _spend_rows(month: str) -> list[dict]:
    return [r for r in transactions(month) if in_spend(r)]


def member_spend(month: str, people: list[dict]) -> dict:
    """Dönemin gideri kişi başına (people: book.people()), kategori kırılımıyla; harcayan kuralı Spenders. Veri okuma
    fonksiyonları üye isteğinde süzüldüğü için üyenin göremediği kart/kayıt/faiz hiçbir toplama girmez."""
    sp = Spenders(people)
    by: dict[str, list[dict]] = defaultdict(list)
    for r in _spend_rows(month):
        by[sp.of(r)].append(r)
    total = sum(r["amount"] for rs in by.values() for r in rs)
    names = {f"u{p['id']}": p for p in people}
    out = []
    for key, rs in by.items():
        amt = sum(r["amount"] for r in rs)
        p = names.get(key)
        out.append({"key": key, "user_id": None if key == SHARED else int(key[1:]), "name": sp.name(key),
                    "left": sp.left(key), "owner": bool(p and p.get("owner")), "amount": _round(amt), "count": len(rs),
                    "pct": round(amt / total * 100, 1) if money.gt(total, 0) else 0.0,
                    "categories": _group(rs, "category")})
    out.sort(key=lambda x: (x["key"] == SHARED, -x["amount"]))
    return {"month": month, "total": _round(total), "people": out}


def budget_split(month: str, people: list[dict]) -> dict[str, list[dict]]:
    """Bütçe kategorisi → [{key, user_id, name, amount}] (en büyükten): ortak bütçede kim ne kadar harcadı
    (harcayan kuralı Spenders)."""
    sp = Spenders(people)
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in _spend_rows(month):
        agg[r["category"]][sp.of(r)] += r["amount"]
    return {cat: sorted(({"key": k, "user_id": None if k == SHARED else int(k[1:]), "name": sp.name(k),
                          "left": sp.left(k), "amount": _round(v)} for k, v in d.items() if money.gt(v, 0)), key=lambda x: -x["amount"])
            for cat, d in agg.items()}


# ---------------------------------------------------------------- planning

def _latest_statement_per_account() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for s in db.list_statements():
        if s["card_no"] not in latest or s["period_end"] > latest[s["card_no"]]["period_end"]:
            latest[s["card_no"]] = s
    return latest


def card_projection() -> dict:
    """Hesap bazında: kalan taksitler, abonelik tahmini, değişken harcama ortalaması."""
    txns = db.card_transactions()
    latest = _latest_statement_per_account()
    by_acc_period: dict[tuple, list] = defaultdict(list)
    for t in txns:
        by_acc_period[(t["account"], t["period"])].append(t)

    out = {}
    for acc, st in latest.items():
        period = st["period"]
        cur = by_acc_period[(acc, period)]

        installments = []  # (ay, tutar, açıklama, taksit)
        for t in cur:
            if t["installments_left"] and t["installment_left_amount"] and t["txn_type"] in (*CARD_SPEND, FEE):
                sign = -1 if t["txn_type"] == REFUND else 1
                total_n = t["installment_no"] + t["installments_left"]
                for i in range(1, t["installments_left"] + 1):
                    installments.append({
                        "month": add_months(period, i), "amount": sign * t["installment_left_amount"],
                        "description": t["description"], "installment": f"{t['installment_no'] + i}/{total_n}",
                        "card": card_label(t), "fee": t["txn_type"] == FEE, "txn_id": t["id"],
                    })

        subs: dict[str, dict] = {}
        states = db.subscription_states()
        cancelled = {m for m, st in states.items() if st["status"] == "iptal"}
        for t in cur:
            if t["category"] in SUBSCRIPTION_CATEGORIES and t["txn_type"] == PURCHASE and t["installment_no"] is None:
                m = merchant_name(t["description"])
                if m in cancelled:
                    continue
                subs.setdefault(m, {"description": m, "amount": 0.0, "category": t["category"], "txn_id": t["id"]})
                subs[m]["amount"] += t["amount"]

        # Değişken = toplam kart harcaması - taksitli satırlar - abonelikler; son N dönem ortalaması
        periods = sorted({p for (a, p) in by_acc_period if a == acc})[-VARIABLE_LOOKBACK:]
        variable = []
        for p in periods:
            v = sum(t["amount"] for t in by_acc_period[(acc, p)]
                    if t["txn_type"] in CARD_SPEND and t["installment_no"] is None
                    and t["category"] not in SUBSCRIPTION_CATEGORIES)
            variable.append(v)

        for m, sub in subs.items():
            if states.get(m, {}).get("status") == "dusuruldu" and states[m]["new_amount"] is not None:
                sub["amount"] = states[m]["new_amount"]
                sub["description"] = f"{m} (yeni paket)"

        out[acc] = {
            "account": acc, "bank": st["bank"], "latest_period": period,
            "next_due_date": st["next_due_date"], "next_period_end": st["next_period_end"],
            "installments": installments,
            "subscriptions": [{**s, "amount": _round(s["amount"])} for s in subs.values()],
            "variable_avg": _round(median(variable)) if variable else 0.0,
            "variable_periods": len(variable),
        }
    return out


@_memo
def _next_cut(st: dict) -> tuple[date, date | None]:
    """Son ekstreden sonraki kesim ve son ödeme günü (ekstrede yazmıyorsa bir ay sonrası)."""
    pe = date.fromisoformat(st["period_end"])
    if st.get("next_period_end"):
        cut = date.fromisoformat(st["next_period_end"])
    else:
        y, m = divmod(pe.year * 12 + pe.month, 12)
        cut = pe.replace(year=y, month=m + 1, day=min(pe.day, 28))
    due = st.get("next_due_date")
    if due:
        return cut, date.fromisoformat(due)
    if st.get("due_date"):
        return cut, cut + (date.fromisoformat(st["due_date"]) - pe)
    return cut, None


def open_cycles(today: date | None = None, proj: dict | None = None, entries: list[dict] | None = None) -> dict:
    """Kesilmemiş ekstre, kart başına: bugüne kadar ne birikti, kesimde tahminen ne olur.

    Bugüne kadar = son ekstreden devreden taksitler + abonelikler + son kesimden beri elle/fişle girilen kart harcamaları.
    Kesimde = taksit + abonelik + değişken harcama; değişken için geçmiş dönemlerin medyanı ile "şu ana kadarki +
    kalan günlerin payı"ndan büyüğü alınır (harcama girilmese de tahmin geçmişin altına düşmez).
    Hangi karttan ödendiği belli olmayan harcamalar en çok kullanılan karta yazılır ve ayrıca gösterilir.
    """
    today = today or date.today()
    proj = card_projection() if proj is None else proj
    entries = db.list_entries() if entries is None else entries
    latest = _latest_statement_per_account()
    out: dict[str, dict] = {}
    for acc, p in proj.items():
        st = latest[acc]
        start = date.fromisoformat(st["period_end"])
        cut, due = _next_cut(st)
        if (due or cut + timedelta(days=10)) < today:  # son ödemesi de geçmiş: o ekstre yüklenmemiş, tahmin anlamsız
            continue
        nxt = add_months(p["latest_period"], 1)
        inst_items = [i for i in p["installments"] if i["month"] == nxt]
        inst = sum(i["amount"] for i in inst_items)
        subs = sum(x["amount"] for x in p["subscriptions"])
        # "bugüne kadar"ın kalemleri: ekran toplamı bunlarla açıklar (ekstre kesilmeden işlem listesi yok)
        # uid kalıcı: taksit/abonelik → kaynak kart işlemi (c<id>, GET /api/transactions/...'daki uid ile aynı), harcama →
        # kayıt (m<id>); dokununca o satırın düzenlemesi açılır
        items = ([{"type": "taksit", "uid": f"c{i['txn_id']}:{i.get('installment')}", "txn_id": i.get("txn_id"),
                   "entry_id": None, "description": i["description"], "amount": _round(i["amount"]),
                   "installment": i.get("installment")} for i in inst_items]
                 + [{"type": "abonelik", "uid": f"c{x['txn_id']}:abonelik", "txn_id": x.get("txn_id"), "entry_id": None,
                     "description": x["description"], "amount": _round(x["amount"])} for x in p["subscriptions"]])
        out[acc] = {"account": acc, "bank": p["bank"], "start": start.isoformat(), "cut": cut.isoformat(),
                    "due": due.isoformat() if due else None, "days_left": max(0, (cut - today).days),
                    "days_total": max(1, (cut - start).days), "installments": _round(inst), "subscriptions": _round(subs),
                    "spent": 0.0, "unassigned": 0.0, "entries": 0, "variable_avg": p["variable_avg"], "items": items}
    if not out:
        return {}
    main = max(out.values(), key=lambda c: c["variable_avg"])["account"]
    aliases = db.card_aliases()
    for e in entries:
        if e["payment"] != "kredi_karti" or e["kind"] != EXPENSE or e["linked_txn_id"] or e["skipped"]:
            continue
        last4 = e.get("card_last4")
        acc = aliases.get(last4) if last4 else None
        acc = acc if acc in out else None
        c = out.get(acc or main)
        if not c or e["entry_date"] > today.isoformat():
            continue
        # son kesimden önceki ama o ekstreye girmemiş (eşleşmemiş) harcama: kesimden en çok POSTING_DAYS gün önceyse
        # provizyonda kalıp bu ekstreye devretmiştir (carried); daha eskisi "ekstrede bulunamadı" diye sorulur
        carried = e["entry_date"] <= c["start"]
        if carried and e["entry_date"] <= (date.fromisoformat(c["start"]) - timedelta(days=db.POSTING_DAYS)).isoformat():
            continue
        amt = e["amount"] / max(1, int(e.get("installments") or 1))  # taksitliyse bu ekstreye ilk taksit düşer
        c["spent"] += amt
        c["entries"] += 1
        c["items"].append({"type": "harcama", "uid": f"m{e['id']}", "txn_id": None, "description": e["description"],
                           "amount": _round(amt), "date": e["entry_date"], "entry_id": e["id"], "unassigned": not acc,
                           **({"carried": True} if carried else {})})
        if not acc:
            c["unassigned"] += amt
    for c in out.values():
        pace = c["variable_avg"] * c["days_left"] / c["days_total"]
        c["variable_at_cut"] = _round(max(c["variable_avg"], c["spent"] + pace))
        c["spent"], c["unassigned"] = _round(c["spent"]), _round(c["unassigned"])
        c["so_far"] = _round(c["installments"] + c["subscriptions"] + c["spent"])
        c["at_cut"] = _round(c["installments"] + c["subscriptions"] + c["variable_at_cut"])
    return out


CARD_ROW_KINDS = (PURCHASE, REFUND, FEE, EXPENSE)


def of_card(r: dict, card_no: str) -> bool:
    """Satır bu kredi kartının mı? Ekstre satırı: ekstrenin ana kart numarası (ek kartlar dahil); elle kayıt: kredi
    kartıyla ödendi ve son 4 hane aynı. Silinen ve ekstreyle eşleşmiş (linked; kart satırı zaten var) elle kayıt hariç."""
    if r.get("deleted") or r.get("linked"):
        return False
    if r["source"] == "card":
        return r.get("account") == card_no
    l4 = "".join(ch for ch in card_no if ch.isdigit())[-4:]
    return r["source"] == "manual" and r.get("payment") == "kredi_karti" and bool(l4) and r.get("card_last4") == l4


def card_limit(card_no: str, statements: list[dict], manual: dict[str, float], unpaid: float, cycle: dict | None) -> dict:
    """Kart limiti ve kalan kullanılabilir limit.

    limit: elle girilen (card_limits) varsa o ("elle"), yoksa kartın limit yazan en son ekstresi ("ekstre").
    Kalan = limit − ödenmemiş ekstre borcu (kesimden sonraki ödemeler düşülmüş; net durumdaki kart borcuyla aynı) −
    açık dönemin bugüne kadarı (Kartlar ekranındaki "Bugüne kadar": devreden taksit + abonelik + girilen harcama).
    Son ekstrede "kullanılabilir limit" yazıyorsa kesim günü için o esas alınır; sonrası tahmin: + kesimden sonraki
    ödemeler − açık dönemde yeni eklenen (taksitler bankanın kullanılabilir limitinden zaten düşülmüş) ± elle limit farkı.
    available eksi olabilir (limit aşıldı); used_pct 0'ın altına inmez."""
    own = sorted((s for s in statements if s["card_no"] == card_no and s.get("source") != "excel"),
                 key=lambda s: s["period_end"], reverse=True)
    st_limit = next((s["credit_limit"] for s in own if s.get("credit_limit")), None)
    if manual.get(card_no):
        limit, src = manual[card_no], "elle"
    elif st_limit:
        limit, src = st_limit, "ekstre"
    else:
        return {"limit": None, "limit_source": None, "statement_limit": None, "available": None,
                "available_source": None, "available_as_of": None, "used": None, "used_pct": None}
    cy = cycle or {}
    so_far = cy.get("so_far") or 0.0
    last = own[0] if own else None
    if last and last.get("available_limit") is not None:
        paid = max(0.0, (last["total_debt"] or 0) - unpaid)  # kesimden sonra son ekstreye yapılan ödemeler
        new = so_far - (cy.get("installments") or 0.0)
        shift = limit - last["credit_limit"] if last.get("credit_limit") else 0.0
        avail = last["available_limit"] + shift + paid - new
        how = "ekstre" if money.diff_k(paid, 0) <= 1 and money.diff_k(new, 0) <= 1 and money.diff_k(shift, 0) <= 1 else "tahmini"
        as_of = last["period_end"] if how == "ekstre" else None
    else:
        avail, how, as_of = limit - unpaid - so_far, "tahmini", None
    used = limit - avail
    return {"limit": _round(limit), "limit_source": src, "statement_limit": _round(st_limit) if st_limit else None,
            "available": _round(avail), "available_source": how, "available_as_of": as_of, "used": _round(used),
            "used_pct": round(max(0.0, used) / limit * 100, 1) if limit else None}


def cards_summary(today: date | None = None, recent: int = 20) -> dict:
    """Kartlar ekranının açılışı için kart başına özet (tam işlem geçmişini çekmeden): son ekstre (kalan dahil),
    açık dönem (bugüne kadar / kesimde tahmini, kalemleri), kalan taksitler ve abonelikler, son N işlem.
    Toplamlar net durumla aynı kural (kart başına yalnız son ekstrenin kalanı)."""
    today = today or date.today()
    nw = net_worth(today)
    proj = card_projection()
    cyc = open_cycles(today, proj)
    rows = [r for r in transactions(None) if r["kind"] in CARD_ROW_KINDS]
    sts = {s["id"]: s for s in db.list_statements()}
    manual = db.card_limits()
    cards = []
    for c in nw["credit_cards"]:
        card_no = c["card_no"]
        latest = next((s for s in sorted(sts.values(), key=lambda s: s["period_end"], reverse=True)
                       if s["card_no"] == card_no and s["bank"] == c["bank"]), None)
        p = proj.get(card_no) or {}
        inst = p.get("installments") or []
        mine = [r for r in rows if of_card(r, card_no)]
        cards.append({
            "title": c["title"], "bank": c["bank"], "card_no": card_no,
            "last_statement": {k: latest.get(k) for k in ("id", "period_end", "period", "due_date", "total_debt",
                                                         "min_payment", "paid_amount", "paid_at", "needs_check",
                                                         "source", "txn_count")} | {"unpaid": c["unpaid"]}
            if latest else None,
            "unpaid": c["unpaid"], "next_estimate": c["next_estimate"], "next_due": c["next_due"],
            "cycle": cyc.get(card_no),
            "installments_left": {"count": len({i["txn_id"] for i in inst}), "total": _round(sum(i["amount"] for i in inst)),
                                  "next_month": _round(sum(i["amount"] for i in inst
                                                           if i["month"] == add_months(p.get("latest_period") or "", 1)))
                                  if p.get("latest_period") else 0.0},
            "subscriptions": p.get("subscriptions") or [],
            "recent": mine[:recent], "recent_total": len(mine),
            # kart limiti ve kalan kullanılabilir limit (yetenek card_limit): limit, limit_source, available, used_pct
            **card_limit(card_no, list(sts.values()), manual, c["unpaid"], cyc.get(card_no)),
        })
    so_far = sum((c["cycle"] or {}).get("so_far", 0) for c in cards)
    at_cut = sum((c["cycle"] or {}).get("at_cut", 0) for c in cards)
    return {"cards": cards, "overdue": nw["card_overdue"],
            "totals": {"so_far": _round(so_far), "at_cut": _round(at_cut), "unpaid": nw["totals"]["card_debt"],
                       "estimated": nw["totals"]["card_estimated"], "overdue": nw["totals"]["card_overdue"]}}


# ---------------------------------------------------------------- bu ayın taksitleri (Özet)

def _card_short(bank: str | None, card_no: str | None) -> str | None:
    """"VakıfBank …9012": listede kısa kart adı (kart numarasının yalnız son 4 hanesi)."""
    l4 = "".join(ch for ch in (card_no or "") if ch.isdigit())[-4:]
    s = f"{bank or ''} …{l4}".strip() if l4 else (bank or "")
    return s or None


def _inst_parts(inst: str | None) -> tuple[int, int] | None:
    try:
        no, of = map(int, (inst or "").split("/"))
        return no, of
    except ValueError:
        return None


@_memo
def installments_due(month: str, today: date | None = None) -> dict:
    """Dönemin (Özet'te seçili ay) taksitleri: "Bu ay ödenecek taksitler · ₺4.250 · 6 taksit" ve kalan taksit borcu.

    ANLAM (çift sayım yok): dönemin harcaması (summary.totals.expense) bu dönemin taksit dilimlerini ZATEN içerir —
    ekstredeki "3/6 taksit" satırı ekstrenin kesim ayına, elle girilen taksitli kart harcamasının her dilimi kendi ayına
    yazılır (_all_rows). Bu yanıt harcamaya EKLENMEZ; harcamanın taksit kırılımı + henüz ekstresi gelmemiş ama tutarı
    belli olan taksitlerdir:
      in_spend  bu dönemin harcamasına dahil taksitler (ekstre satırı ya da elle girilen dilim; finance.counts kuralı:
                silinen/atlanan/ekstreyle eşleşmiş elle kayıt sayılmaz — eşleşmişte kart satırı sayılır)
      expected  kartın bu döneme ait ekstresi henüz yok: son ekstrenin "kalan taksit" bilgisinden bu döneme düşen
                taksitler (card_projection; Planlama'daki "taksit" satırlarıyla aynı). Harcamaya ekstre gelince girer.
      total     in_spend + expected = "bu ay ödenecek taksitler" (şu anda ne durumdayız: bu dönem taksitlere ne gidiyor)
      remaining bu dönemden SONRAKİ aylara düşen taksitlerin toplamı ("kalan taksit borcu"): son ekstrelerin kalan
                taksitleri + elle girilen taksitlerin sonraki dilimleri (ekstreyle eşleşmişlerin sonraki dilimleri zaten
                ekstrenin kalan taksitinde; ikinci kez sayılmaz)
    count / remaining_count yalnız alışveriş taksitlerini sayar; vade farkı, BSMV, KKDF taksitleri (fee: true) tutara
    girer ama "6 taksit" sayısını şişirmez. İade taksiti eksi tutarla toplamı düşürür, sayıya girmez.
    Ortak defter: kart işlemleri, kayıtlar ve ekstreler okunurken süzüldüğü için gizli kart/kayıt/faiz hiçbir kaleme
    girmez (hide_fees'te vade farkı taksitleri de yok). items[].row: dokununca açılacak gerçek satır (GET /api/transactions
    satırıyla aynı biçim; beklenen taksitte kaynağı olan son ekstredeki kart işlemi)."""
    today = today or date.today()
    statements = db.list_statements()
    due_of = {(s["card_no"], s["period"]): s.get("due_date") for s in statements}
    latest = _latest_statement_per_account()
    aliases = db.card_aliases()

    def due_for(acc: str | None, m: str) -> str | None:
        if not acc:
            return None
        if (acc, m) in due_of:
            return due_of[(acc, m)]
        st = latest.get(acc)
        if st and add_months(st["period"], 1) == m:  # son ekstreden sonraki ekstre: "sonraki son ödeme"
            due = _next_cut(st)[1]
            return due.isoformat() if due else None
        return None

    items: list[dict] = []
    for r in transactions(month):
        parts = _inst_parts(r.get("installment"))
        if not parts or r["month"] != month or r["kind"] not in (*CARD_SPEND, FEE, EXPENSE):
            continue
        spend, _ = counts(r)
        if not spend:
            continue
        if r["source"] == "card":
            acc, card = r.get("account"), _card_short(r.get("bank"), r.get("account"))
        else:
            acc = aliases.get(r.get("card_last4") or "")
            st = latest.get(acc) if acc else None
            card = _card_short(st["bank"] if st else None, acc) if acc else (
                f"Kart …{r['card_last4']}" if r.get("card_last4") else None)
        items.append({"uid": r["uid"], "source": r["source"], "title": r["merchant"], "description": r["description"],
                      "card": card, "installment": r["installment"], "no": parts[0], "of": parts[1],
                      "amount": _round(r["amount"]), "in_spend": True, "fee": r["kind"] == FEE,
                      "due_date": due_for(acc, month), "row": r})

    proj = card_projection()
    by_id = {t["id"]: t for t in db.card_transactions()}
    states, merchant_aliases = db.subscription_states(), db.merchant_aliases()
    remaining = 0.0
    remaining_keys: set = set()
    for acc, p in proj.items():
        for i in p["installments"]:
            if i["month"] > month:
                remaining += i["amount"]
                if not i["fee"] and i["amount"] > 0:
                    remaining_keys.add(("c", i["txn_id"]))
                continue
            if i["month"] != month or p["latest_period"] >= month:
                continue  # bu dönemin ekstresi var (ya da geçmiş dönem): gerçek satırlar yukarıda
            t = by_id.get(i["txn_id"])
            if not t:
                continue
            row = _card_row(t, states, merchant_aliases)
            no, of = _inst_parts(i["installment"]) or (0, 0)
            items.append({"uid": f"c{i['txn_id']}:{i['installment']}", "source": "projected", "title": row["merchant"],
                          "description": t["description"], "card": _card_short(p["bank"], acc),
                          "installment": i["installment"], "no": no, "of": of, "amount": _round(i["amount"]),
                          "in_spend": False, "fee": i["fee"], "due_date": due_for(acc, month), "row": row})
    # elle girilen taksitlerin sonraki dilimleri (ekstreyle eşleşmemiş; eşleşmişin devamı ekstrenin kalan taksitinde)
    for e in db.installment_entries():
        if e["skipped"] or e["linked_txn_id"]:
            continue
        for k, m, amt in db.installment_parts(e):
            if m > month:
                remaining += amt
                remaining_keys.add(("m", e["id"]))
    # geçmiş dönem seçiliyse: o dönemden sonraki ekstrelerde gerçekleşmiş taksit satırları da "sonrası"dır
    for t in db.card_transactions():
        if t["period"] > month and t["installment_no"] is not None and t["txn_type"] in (*CARD_SPEND, FEE):
            remaining += t["amount"]

    items.sort(key=lambda x: (x["fee"], -x["amount"], x["title"]))
    in_spend = sum(x["amount"] for x in items if x["in_spend"])
    expected = sum(x["amount"] for x in items if not x["in_spend"])
    return {"month": month, "current": month == current_period(today),
            "total": _round(in_spend + expected), "count": sum(1 for x in items if not x["fee"] and x["amount"] > 0),
            "in_spend": _round(in_spend), "expected": _round(expected),
            "remaining": _round(remaining), "remaining_count": len(remaining_keys), "items": items}


def recurring_suggestions(today: date | None = None) -> list[dict]:
    """Hareketlerde her ay tekrarlayan ama sabit kalem olmayan ödemeler (kira, aidat, kreş, kira geliri…).

    Kural: son verili aya kadar son 4 ayın en az 3'ünde var, son 3 ayın aylık toplamları birbirine %25'ten yakın.
    Tutar = son ayın toplamı (kira artışı ve ikiye bölünmüş ödeme doğru çıkar), gün = ödeme günlerinin medyanı.
    """
    today = today or date.today()
    entries = [e for e in db.list_entries() if not e["skipped"] and e["amount"] > 0 and not e.get("debt_id")
               and e["payment"] not in ("kredi_karti", "kart") and e["entry_date"] <= today.isoformat()
               and e["category"] not in ("Nakit Çekim", "Transfer")]  # ATM'den çekilen para sabit kalem değil
    recs = db.list_recurring()
    taken = {r["match_key"] for r in recs if r.get("match_key")}
    names = {(r["kind"], r["description"].strip().casefold()) for r in recs}
    dismissed = set(_json_setting("recurring_dismissed", []))
    groups: dict[tuple, list] = defaultdict(list)
    for e in entries:
        groups[(e["kind"], db.recurring_key(e["description"], e["counterparty"]))].append(e)
    out = []
    for (kind, key), es in groups.items():
        if key in ("p:", "m:") or key in taken or f"{kind}:{key}" in dismissed or any(e["recurring_id"] for e in es):
            continue
        if any((kind, e["description"].strip().casefold()) in names for e in es):
            continue
        by_month: dict[str, float] = defaultdict(float)
        for e in es:
            by_month[e["entry_date"][:7]] += e["amount"]
        last = max(by_month)
        if last < add_months(ym(today), -2):  # artık ödenmiyor
            continue
        window = [add_months(last, -i) for i in range(4)]
        if sum(m in by_month for m in window) < 3:
            continue
        recent = [by_month[m] for m in window[:3] if m in by_month]
        if min(recent) < 100 or max(recent) > min(recent) * 1.25:
            continue
        days = sorted(int(e["entry_date"][8:10]) for e in es if e["entry_date"][:7] in window)
        cats = [e["category"] for e in es if e["category"] and e["category"] != "Kategorisiz"]
        cat = max(set(cats), key=cats.count) if cats else None
        accs = [e["account_id"] for e in es if e["account_id"]]
        who = next((e["counterparty"] for e in reversed(es) if e["counterparty"]), None)
        label = (f"{cat.split(' & ')[0]} ({who})" if cat and who else who or cat or es[-1]["description"])[:60]
        out.append({"key": f"{kind}:{key}", "kind": kind, "description": label, "category": cat,
                    "amount": _round(by_month[last]), "day_of_month": days[len(days) // 2], "months": len(by_month),
                    "first_month": min(by_month), "last_month": last, "account_id": max(set(accs), key=accs.count) if accs else None,
                    "history": [{"month": m, "amount": _round(by_month[m])} for m in sorted(by_month)[-6:]]})
    return sorted(out, key=lambda x: -x["amount"])


def recurring_drift(today: date | None = None, salary: bool = False) -> list[dict]:
    """Sabit kalemin son iki gerçekleşen ayı aynı yeni tutarla geldiyse (kira artışı): "güncelleyeyim mi?" önerisi.
    salary=True: maaş kalemleri app/salary.py kuralıyla (yatan maaş, zam ayı, ikramiye, peşin maaş farkı) eklenir."""
    from app import salary as sal

    today = today or date.today()
    entries = db.list_entries()
    out = []
    for r in db.list_recurring():
        if r.get("gross_amount") or (r["end_month"] and r["end_month"] < ym(today)):
            continue
        if salary and sal.is_salary(r):
            continue
        by_month: dict[str, float] = defaultdict(float)
        for e in entries:
            if e["recurring_id"] == r["id"] and not e["skipped"]:
                by_month[e["month"]] += e["amount"]
        last2 = sorted(by_month)[-2:]
        if len(last2) < 2 or add_months(last2[0], 1) != last2[1]:
            continue
        a, b = by_month[last2[0]], by_month[last2[1]]
        if abs(a - b) > max(1.0, b * 0.01):
            continue
        planned = db.amount_at(r, add_months(last2[1], 1))
        if abs(b - planned) <= planned * 0.03:
            continue
        prev = [m for m in sorted(by_month) if abs(by_month[m] - b) > max(1.0, b * 0.01)]
        start = next((m for m in sorted(by_month) if m > (prev[-1] if prev else "")), last2[0])
        out.append({"recurring_id": r["id"], "description": r["description"], "kind": r["kind"], "old": _round(planned),
                    "new": _round(b), "from_month": start})
    if salary:
        out += sal.drift(today)
    return out


def accept_recurring_suggestion(key: str, description: str | None = None, amount: float | None = None) -> int:
    """Öneriyi sabit kaleme çevir; geçmiş hareketleri ona bağla (her ay bir kayıt), yenileri match_key ile bağlanır."""
    s = next((x for x in recurring_suggestions() if x["key"] == key), None)
    if not s:
        raise KeyError(key)
    kind, mkey = key.split(":", 1)
    rec = {"description": (description or s["description"]).strip(), "amount": amount or s["amount"],
           "kind": kind, "category": s["category"], "day_of_month": s["day_of_month"],
           "start_month": s["first_month"], "account_id": s["account_id"], "match_key": mkey}
    cands = [e for e in db.list_entries()
             if e["kind"] == kind and not e["recurring_id"] and not e["skipped"]
             and e["payment"] not in ("kredi_karti", "kart")
             and db.recurring_key(e["description"], e["counterparty"]) == mkey]
    # dönem kuralı yeni kayıtlarla aynı (db._nearest_recurring_month): gününe en yakın boş dönem. Başlangıç ayı da
    # takvim ayı değil ilk bağlanan kaydın DÖNEMİ (ay dönümü 17'de 17 Ağustos kirası Eylül dönemidir)
    plan = db.attach_recurring_series({**rec, "id": None}, cands)
    if plan:
        rec["start_month"] = min(plan.values())
    rid = db.create_recurring(rec)
    with db.connect() as c:
        c.executemany("UPDATE entries SET recurring_id = ?, month = ? WHERE id = ? AND recurring_id IS NULL",
                      [(rid, m, eid) for eid, m in plan.items()])
    return rid


def dismiss_recurring_suggestion(key: str) -> None:
    keys = _json_setting("recurring_dismissed", [])
    if key not in keys:
        db.set_setting("recurring_dismissed", __import__("json").dumps([*keys, key]))


def _json_setting(key: str, default):
    import json

    v = db.get_setting(key)
    return json.loads(v) if v else default


def _plan_parts(lines: list[dict]) -> dict:
    """Planlama ayının dökümü (months[].parts = Özet summary.expected.parts): gerçekleşen gelir/gider ve bekleyenler
    (ekstresi gelmemiş taksit ve abonelik, girilmemiş sabit kalem, ödenmemiş fatura, bekleyen gelir). Değişken kart
    tahmini burada yok (satırın "estimated" alanı)."""
    by: dict[str, float] = defaultdict(float)
    for ln in lines:
        if ln["kind"] not in (EXPENSE, INCOME) or ln["type"] == "degisken":
            continue
        pending = ln["type"] in ("taksit", "abonelik", "fatura") or (ln["type"] == "sabit" and not ln.get("confirmed"))
        key = {"taksit": "pending_installments", "abonelik": "pending_subscriptions", "fatura": "pending_bills",
               "sabit": "pending_recurring_expense"}.get(ln["type"]) if pending else "recorded_expense"
        if ln["kind"] == INCOME:
            key = "pending_income" if pending else "recorded_income"
        by[key] += ln["amount"]
    parts = {k: _round(by.get(k, 0.0)) for k in ("recorded_expense", "pending_installments", "pending_subscriptions",
                                                  "pending_recurring_expense", "pending_bills", "recorded_income",
                                                  "pending_income")}
    parts["pending_expense"] = _round(parts["pending_installments"] + parts["pending_subscriptions"]
                                      + parts["pending_recurring_expense"] + parts["pending_bills"])
    return parts


@_memo  # yalnız defter verisine ve güne bağlı; Planlama, Özet (summary/insights) ve hatırlatmalar aynı sonucu paylaşır
def plan(months: int = 6, today: date | None = None) -> dict:
    today = today or date.today()
    start = current_period(today)
    span = [add_months(start, i) for i in range(months)]
    proj = card_projection()
    statements = db.list_statements()
    recurring = [{**r, "amount_now": db.planned_amount(r, start)} for r in db.list_recurring()]
    entries = db.list_entries()
    cycles = open_cycles(today, proj, entries)
    bills = db.list_bills()
    goals = db.list_goals(active_only=True)
    raw_recurring = db.list_recurring()
    # ay döngüsünde her ay tüm kayıtları taramamak için bir kez: sabit kalemin o ayki kaydı (ilk eşleşen)
    rec_entry: dict[tuple, dict] = {}
    entries_by_m: dict[str, list] = defaultdict(list)
    for e in entries:
        entries_by_m[e["month"]].append(e)
        if e["recurring_id"]:
            rec_entry.setdefault((e["recurring_id"], e["month"]), e)

    result = []
    cumulative = cumulative_est = 0.0
    for m in span:
        lines = []
        # GERÇEKLEŞEN kısım Özet'in sayım kuralıyla (counts / _totals): dönemin satırlarından sayılanlar — iade gideri
        # düşürür, birikime ayrılan (altın/döviz alımı) ve ortak harcamada başkasının payı gider değil, ekstreyle
        # eşleşmiş elle kayıt bir kez (kart satırı) sayılır. Böylece dönemin "kesin" gelir/gideri = Özet'in gelir/gideri +
        # bekleyenler; Özet'in "Beklenen"i (summary.expected) bu ayın satırıdır, iki ekran hiç ayrışmaz.
        # Kart satırları kartına, sabit kaleme bağlı elle kayıtlar sabit kalemine, kalanlar tek seferlik satırlara.
        card_got: dict[str, list] = defaultdict(list)
        rec_got: dict[int, list] = defaultdict(list)
        other_got: list[dict] = []
        for r in transactions(m):
            if r["month"] != m or not any(counts(r)):
                continue
            if r["source"] == "card":
                card_got[r["account"]].append(r)
            elif r["source"] == "manual" and r.get("recurring_id"):
                rec_got[r["recurring_id"]].append(r)
            else:
                other_got.append(r)
        # henüz girilmemiş sabit kalemler: Özet'in "bekliyor"u ile aynı kaynak (pending_recurring; ör. o ayın maaşı
        # dökümden gelmişse maaş ikinci kez eklenmez)
        pend = {x["recurring_id"]: x for x in pending_recurring(m, raw_recurring, entries_by_m.get(m, [])) if x.get("pending")}
        # --- kart
        for acc, p in proj.items():
            st = next((s for s in statements if s["card_no"] == acc and s["period"] == m), None)
            if st:
                actual = sum(r["amount"] for r in card_got.pop(acc, []))
                lines.append({"type": "kart_gercek", "kind": EXPENSE, "amount": _round(actual),
                              "description": f"{p['bank']} {acc} ekstresi (gerçek)", "due_date": st["due_date"]})
                continue
            if m <= p["latest_period"]:
                continue  # geçmiş ay, ekstresi yüklenmemiş
            for i in p["installments"]:
                if i["month"] == m:
                    lines.append({"type": "taksit", "kind": EXPENSE, "amount": _round(i["amount"]),
                                  "description": f"{i['description']} ({i['installment']})", "account": acc})
            for s in p["subscriptions"]:
                lines.append({"type": "abonelik", "kind": EXPENSE, "amount": s["amount"],
                              "description": s["description"], "account": acc})
            cyc = cycles.get(acc)
            if cyc and ym(cyc["cut"]) == m:
                # açık dönem: elle/fişle girilenler zaten tek seferlik satır; burada sadece kalan tahmini pay
                rest = _round(max(0.0, cyc["variable_at_cut"] - cyc["spent"]))
                if rest > 0:
                    lines.append({"type": "degisken", "kind": EXPENSE, "amount": rest, "account": acc,
                                  "description": f"{acc} kesime kadar tahmini kart harcaması"
                                                 + (f" (girilen {money.tl(cyc['spent'], decimals=0)} hariç)" if cyc["spent"] else "")})
            elif p["variable_avg"] > 0:
                lines.append({"type": "degisken", "kind": EXPENSE, "amount": p["variable_avg"],
                              "description": f"{acc} tahmini kart harcaması (son {p['variable_periods']} dönem medyanı)",
                              "account": acc})
        # --- kısma hedefleri: ekstresi olmayan aylarda değişken kart tahmini hedef kadar azalır
        saving = sum(g["baseline"] - g["target"] for g in goals if g["start_month"] <= m and g["baseline"] > g["target"])
        var_lines = [l for l in lines if l["type"] == "degisken"]
        var_total = sum(l["amount"] for l in var_lines)
        if saving and var_total:
            cut = min(saving, var_total)
            for l in var_lines:
                l["amount"] = _round(l["amount"] * (var_total - cut) / var_total)
                l["description"] += f" − kısma hedefleri"
            lines.append({"type": "tasarruf_notu", "kind": "bilgi", "amount": _round(cut),
                          "description": f"Kısma hedefleriyle {money.tl(cut, decimals=0)} daha az harcama varsayıldı"})

        # ekstresi Planlama'nın kart listesinde olmayan kart satırları (ör. Excel'den gelen ekstre): yine sayılır
        for acc, got in card_got.items():
            st = next((s for s in statements if s["card_no"] == acc and s["period"] == m), None)
            lines.append({"type": "kart_gercek", "kind": EXPENSE, "amount": _round(sum(r["amount"] for r in got)),
                          "description": f"{got[0].get('bank') or 'Kart'} {acc} ekstresi (gerçek)",
                          "due_date": st["due_date"] if st else None})

        # --- tekrarlayan
        for r in recurring:
            if not is_active(r, m):
                continue
            e = rec_entry.get((r["id"], m))
            if e and e["skipped"]:
                continue
            got = rec_got.pop(r["id"], None)
            if e or got:  # girildi: Özet'te sayılan tutarı (kartla ödenip ekstreyle eşleştiyse kart satırında sayılır)
                if got:
                    lines.append({"type": "sabit", "kind": r["kind"], "amount": _round(sum(x["amount"] for x in got)),
                                  "description": r["description"], "recurring_id": r["id"], "confirmed": True,
                                  "custom": False, "template_amount": r["amount"], "month": m})
                continue
            if r["id"] not in pend:
                continue
            planned = pend[r["id"]]["amount"]
            lines.append({"type": "sabit", "kind": r["kind"], "amount": planned,
                          "description": r["description"], "recurring_id": r["id"], "confirmed": False,
                          "custom": planned != r["amount"], "template_amount": r["amount"], "month": m})
        # --- tek seferlik elle girilenler (taksitliyse her taksit kendi ayında; bölünmüşse her kalem ayrı) ve
        # sayılan borç kapanışları (tahsil edilemeyen alacak gider, silinen borç gelir)
        for x in [*other_got, *(y for ys in rec_got.values() for y in ys)]:
            line = {"type": "tek_seferlik", "kind": INCOME if counts(x)[1] else EXPENSE, "amount": x["amount"],
                    "description": x["description"] + (f" ({x['installment']} taksit)" if x.get("installment") else "")}
            if x["source"] == "manual":
                line["entry_id"] = x.get("installment_of") or x["id"]
            lines.append(line)

        # --- ödenmemiş faturalar (ödenenler zaten tek seferlik gider olarak girdi)
        for b in bills:
            if db.period_of(b["due_date"]) == m and b["paid_at"] is None:
                lines.append({"type": "fatura", "kind": EXPENSE, "amount": b["amount"],
                              "description": f"{b['provider']} faturası (son {date.fromisoformat(b['due_date']):%d.%m})"})

        # Kesin: gelir, taksit, abonelik, sabit, fatura, gerçek ekstre. Tahmini: değişken kart harcaması (bilgi amaçlı)
        income = sum(l["amount"] for l in lines if l["kind"] == INCOME)
        estimated = sum(l["amount"] for l in lines if l["kind"] == EXPENSE and l["type"] == "degisken")
        expense = sum(l["amount"] for l in lines if l["kind"] == EXPENSE and l["type"] != "degisken")
        cumulative += income - expense
        cumulative_est += income - expense - estimated
        groups = defaultdict(float)
        for l in lines:
            if l["kind"] in (EXPENSE, INCOME):
                groups[l["type"] if l["kind"] == EXPENSE else "gelir"] += l["amount"]
        result.append({
            "month": m, "income": _round(income), "expense": _round(expense), "estimated": _round(estimated),
            "net": _round(income - expense), "net_with_estimate": _round(income - expense - estimated),
            "cumulative": _round(cumulative), "cumulative_with_estimate": _round(cumulative_est),
            "groups": {k: _round(v) for k, v in groups.items()},
            # gerçekleşen / bekleyen dökümü (yetenek summary_forecast): Özet "Dönem sonu" ile Planlama ayı aynı kaynak
            "parts": _plan_parts(lines),
            "lines": sorted(lines, key=lambda l: (l["kind"] != INCOME, l["kind"] == "bilgi", -l["amount"])),
        })

    dues = []
    for s in statements:
        if s.get("source") == "excel":
            continue
        if s["due_date"] and s["due_date"] >= today.isoformat() and not (
                s["paid_amount"] is not None and money.diff_k(s["paid_amount"], s["total_debt"]) >= -1):
            # kısmi ödenmiş ekstre: takvimde kalanı (ödenen düşülür); total_debt ve paid_amount bilgi
            paid = s["paid_amount"] or 0
            dues.append({"date": s["due_date"], "account": s["card_no"], "bank": s["bank"],
                         "amount": _round(max(0.0, (s["total_debt"] or 0) - paid)) if paid else s["total_debt"],
                         "total_debt": s["total_debt"], "paid_amount": s["paid_amount"],
                         "min_payment": s["min_payment"] if not paid or (s["min_payment"] or 0) > paid else None,
                         "estimated": False})
    for acc, c in cycles.items():
        if c["due"] and c["due"] >= today.isoformat():
            dues.append({"date": c["due"], "account": acc, "bank": c["bank"], "amount": c["at_cut"],
                         "min_payment": None, "estimated": True, "cycle": c})

    return {"months": result, "dues": sorted(dues, key=lambda d: d["date"]),
            "projection": list(proj.values()), "recurring": recurring}


EXPECTED_MAX_MONTHS = 24  # Planlama'nın en uzak ufku (/api/plan months ≤ 24): daha ilerisi için beklenen yok


def _expected_text(parts: dict) -> str:
    """(i) metni: yalnız o dönemde gerçekten olan bekleyenler sayılır (sahibin istediği temel cümle: taksit + gelir)."""
    what = []
    if parts["pending_installments"] or parts["pending_subscriptions"]:
        what.append("ekstresi gelmemiş kart taksitleri" + (" ve abonelikleri" if parts["pending_subscriptions"] else ""))
    if parts["pending_recurring_expense"]:
        what.append("girilmemiş sabit giderler")
    if parts["pending_bills"]:
        what.append("ödenmemiş faturalar")
    if parts["pending_income"]:
        what.append("beklenen gelirler")
    if not what:
        what = ["ekstresi gelmemiş kart taksitleri", "beklenen gelirler"]
    s = what[0] if len(what) == 1 else ", ".join(what[:-1]) + " ve " + what[-1]
    # baştaki "Ad:" yok: mobil (i) metni olduğu gibi gösterir (web baştaki adı atar); iki istemcide aynı cümle
    return f"{s[:1].upper()}{s[1:]} dahil tahmin; yeni harcamalar eklenmez."


def expected(month: str, today: date | None = None) -> dict | None:
    """Özet → "Beklenen" (yetenek summary_expected): dönem sonunda gelir/gider/artan ne olur — Planlama'nın o ayki
    satırının ("kesin" gelir/gider; değişken kart tahmini HARİÇ) kendisi. Ayrı hesap yok: iki ekran aynı rakamı gösterir.

    Planlama'nın gerçekleşen kısmı Özet'in sayım kuralıyla kurulduğu için (plan → counts) her zaman
      expense = totals.expense + pending_installments + pending_subscriptions + pending_recurring_expense + pending_bills
      income  = totals.income  + pending_income        (pending_income = summary.pending.income, Gelir'in "bekliyor"u)
    pending_installments = installments.expected (ekstresi gelmemiş kartın son ekstreden bilinen taksitleri; "harcamana
    dahil" taksitler zaten totals.expense'te — iki kez sayılmaz), pending_subscriptions = aynı kartların abonelikleri,
    pending_recurring_expense = summary.pending.expense (girilmemiş sabit giderler), pending_bills = ödenmemiş faturalar.
    Yeni (değişken) harcama tahmini eklenmez. Geçmiş dönem (kapandı) ve Planlama ufku dışı: None."""
    today = today or date.today()
    cur = current_period(today)
    if month < cur:
        return None
    y0, m0 = map(int, cur.split("-"))
    y1, m1 = map(int, month.split("-"))
    n = (y1 - y0) * 12 + (m1 - m0) + 1
    if n > EXPECTED_MAX_MONTHS:
        return None
    row = next((x for x in plan(max(6, n), today=today)["months"] if x["month"] == month), None)
    if row is None:
        return None
    # variable_estimate / forecast_net (yetenek summary_forecast): Planlama'nın "her zamanki harcamanla" rakamı (değişken
    # kart tahmini dahil = months[].net_with_estimate). Satırın kendisinden; ayrı hesap yok
    return {"month": month, "source": "plan", "expense": row["expense"], "income": row["income"], "net": row["net"],
            "variable_estimate": row["estimated"], "forecast_net": row["net_with_estimate"], "parts": row["parts"]}


# ---------------------------------------------------------------- ödemeler (ekstre + fatura)

PAYABLE_WINDOW = 35  # ödenmiş kalemler kaç gün listede kalsın


def _status(amount: float, paid: float | None, due: str, today: date) -> str:
    if paid is not None and money.diff_k(paid, amount) >= -1:  # 1 kuruş eksik ödeme de ödendi
        return "odendi"
    if paid:
        return "kismi"
    return "gecikti" if date.fromisoformat(due) < today else "bekliyor"


@_memo
def payables(today: date | None = None) -> list[dict]:
    """Ödenecekler: kart ekstreleri, faturalar ve henüz kesilmemiş ekstrelerin tahmini."""
    today = today or date.today()
    out = []
    statements = db.list_statements()
    latest = {st["id"] for st in db.latest_card_statements(today.isoformat(), statements).values()}
    by_card = {st["card_no"]: st["id"] for st in db.latest_card_statements(today.isoformat(), statements).values()}
    for st in statements:
        if not st["due_date"] or st.get("source") == "excel":  # kart Excel'inden üretilen ekstre: borç ve son ödeme kurgu
            continue
        status = _status(st["total_debt"], st["paid_amount"], st["due_date"], today)
        age = (today - date.fromisoformat(st["due_date"])).days
        is_latest = st["id"] in latest
        if status == "odendi" and age > PAYABLE_WINDOW:
            continue
        if status != "odendi" and age > 120 and not is_latest:  # çok eski, işaretlenmemiş ve devredilmiş: listeyi kirletmesin
            continue
        item = {"type": "statement", "id": st["id"], "title": f"{st['bank']} {st['card_no']}",
                "subtitle": f"Ekstre · kesim {date.fromisoformat(st['period_end']):%d.%m}",
                "amount": st["total_debt"], "min_payment": st["min_payment"], "due_date": st["due_date"],
                "days_left": -age, "paid_amount": st["paid_amount"], "paid_at": st["paid_at"], "status": status,
                "in_total": is_latest and status != "odendi"}
        if not is_latest and status != "odendi" and by_card.get(st["card_no"]):
            # ödenmemiş eski ekstre: kalanı kartın son ekstresine devretti; gecikmiş bilgisi, toplama girmez
            item["rolled_into"] = by_card[st["card_no"]]
        out.append(item)
    for b in db.list_bills():
        status = _status(b["amount"], b["paid_amount"], b["due_date"], today)
        if status == "odendi" and b["paid_at"] and (today - date.fromisoformat(b["paid_at"])).days > PAYABLE_WINDOW:
            continue
        out.append({"type": "bill", "id": b["id"], "title": b["provider"],
                    "subtitle": " · ".join(x for x in ("Fatura", b["category"], b["description"]) if x),
                    "amount": b["amount"], "min_payment": None, "due_date": b["due_date"],
                    "days_left": (date.fromisoformat(b["due_date"]) - today).days,
                    "paid_amount": b["paid_amount"], "paid_at": b["paid_at"], "status": status,
                    "in_total": status != "odendi", "has_file": bool(b["file_name"]), "category": b["category"], "note": b["note"],
                    "description": b["description"]})
    # Vadeli borçlarım (kişilere): TL ise tutar, değilse birimiyle (ortak defterde Borç sayfası kapalıysa gösterilmez)
    from app import book

    rules = book.active()
    for d in [] if rules and "borclar" in rules["hidden_pages"] else db.list_debts():
        if d["direction"] != "borc" or not d["due_date"] or d["asset"] != "TRY":  # döviz/altın: Transferler + hatırlatma
            continue
        if d["settled_at"] and (today - date.fromisoformat(d["settled_at"])).days > PAYABLE_WINDOW:
            continue
        status = "odendi" if d["settled_at"] else _status(d["amount"], d["paid_amount"] or None, d["due_date"], today)
        out.append({"type": "debt", "id": d["id"], "title": f"{d['person']} (borç)",
                    "subtitle": " · ".join(x for x in ("Borç", d["note"]) if x),
                    "amount": d["amount"], "min_payment": None, "due_date": d["due_date"],
                    "days_left": (date.fromisoformat(d["due_date"]) - today).days,
                    "paid_amount": d["paid_amount"] or None, "paid_at": d["settled_at"], "status": status,
                    "in_total": status != "odendi"})
    # Kesilmemiş ekstreler: tahmini tutar, ödeme butonu yok
    for d in plan(3, today=today)["dues"]:
        if d["estimated"] and (date.fromisoformat(d["date"]) - today).days <= 45:
            out.append({"type": "estimate", "id": None, "title": f"{d['bank']} {d['account']}",
                        "subtitle": "Sonraki ekstre (tahmini)", "amount": d["amount"] or 0, "min_payment": None,
                        "due_date": d["date"], "days_left": (date.fromisoformat(d["date"]) - today).days,
                        "paid_amount": None, "paid_at": None, "status": "tahmini", "cycle": d.get("cycle"),
                        "in_total": False})
    # key: kalemin kararlı, benzersiz kimliği (yetenek payables_key). Tahminin id'si yok (None): iki kartın tahmini aynı
    # günde düşünce istemcinin "tür-id-tarih" anahtarı çakışıyordu ve React çakışan satırı silmeden bırakıp her yeniden
    # sıralamada (Ödendi / Geri al) bir kopya biriktiriyordu. Aynı anahtar ikinci kez gelirse (olmamalı) ilki kalır.
    seen: set[str] = set()
    uniq = []
    for x in out:
        x["key"] = f"{x['type']}:{x['id']}" if x["id"] is not None else f"{x['type']}:{x['title']}:{x['due_date']}"
        if x["key"] not in seen:
            seen.add(x["key"])
            uniq.append(x)
    order = {"gecikti": 0, "kismi": 1, "bekliyor": 2, "tahmini": 3, "odendi": 4}
    return sorted(uniq, key=lambda x: (order[x["status"]], x["due_date"] if x["status"] != "odendi" else "~" + (x["paid_at"] or "")))


def payables_totals(today: date | None = None, items: list[dict] | None = None, days: int | None = None) -> dict:
    """Ödemeler toplamı (istemci kendisi toplamasın). open: toplama giren kalemlerin (in_total) kalanı; card_debt:
    kart başına son ekstrenin kalanı (net durumla aynı kural); overdue: kalanı yeni ekstreye devretmiş, ödenmemiş eski
    ekstreler (bilgi; toplamda yok); estimated: kesilmemiş ekstrelerin tahmini."""
    today = today or date.today()
    items = payables(today) if items is None else items
    rem = lambda p: max(0.0, p["amount"] - (p["paid_amount"] or 0))  # noqa: E731
    inc = [p for p in items if p.get("in_total")]
    by = {t: _round(sum(rem(p) for p in inc if p["type"] == t)) for t in ("statement", "bill", "debt")}
    rolled = [p for p in items if p.get("rolled_into")]
    out_window = None
    if days is not None:  # başlık toplamı (mobil 'önümüzdeki N gün'): toplama giren kalemlerden vadesi N gün içinde ya da geçmiş
        win = [p for p in inc if p["days_left"] <= days]
        out_window = {"days": days, "total": _round(sum(rem(p) for p in win)), "count": len(win),
                      "overdue": _round(sum(rem(p) for p in win if p["days_left"] < 0)),
                      "estimated": _round(sum(p["amount"] for p in items if p["type"] == "estimate" and p["days_left"] <= days))}
    this_month = today.isoformat()[:7]  # takvim ayı (mobil 'Bu ay ödenen'): ödendi işaretli, ödeme tarihi bu ay
    paid_m = [p for p in items if p["status"] == "odendi" and (p.get("paid_at") or "")[:7] == this_month]
    out_paid = {"month": this_month, "total": _round(sum(p["paid_amount"] or p["amount"] for p in paid_m)),
                "count": len(paid_m)}
    return {"open": _round(sum(rem(p) for p in inc)), "open_count": len(inc), "window": out_window,
            "paid_this_month": out_paid,
            "card_debt": db.card_debt_at(today.isoformat()), "statements": by["statement"], "bills": by["bill"],
            "debts": by["debt"], "overdue": _round(sum(rem(p) for p in rolled)), "overdue_count": len(rolled),
            "estimated": _round(sum(p["amount"] for p in items if p["type"] == "estimate"))}


# ---------------------------------------------------------------- birimler (borç/alacak: TL, döviz, altın)

ASSETS = {"TRY": "TL", "USD": "Dolar", "EUR": "Euro", "GBP": "Sterlin", "GRAM_ALTIN": "gram altın",
          "CEYREK_ALTIN": "çeyrek altın", "YARIM_ALTIN": "yarım altın", "TAM_ALTIN": "tam altın",
          "CUMHURIYET_ALTIN": "Cumhuriyet altını"}


def asset_label(asset: str) -> str:
    return ASSETS.get(asset, asset)


def fmt_asset(x: float, asset: str) -> str:
    if asset == "TRY":
        return _tl(x)
    n = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").removesuffix(",00")
    return f"{n} {asset_label(asset)}"


# ---------------------------------------------------------------- insights

def _tl(x: float) -> str:
    return money.tl(x)


def _fold(s: str) -> str:
    from app.categorize import fold_key

    return fold_key(s)


def _slug(x: str) -> str:
    """Uyarı anahtarında ad: küçük harf, boşluk yerine tire (kararlı; görünen metinden bağımsız)."""
    return re.sub(r"[\s/:]+", "-", (x or "").strip().casefold())[:60] or "-"


def _fp(*parts) -> str:
    """Uyarının kapsamındaki kayıtların parmak izi: kayıtlar ya da tutarlar değişince anahtar da değişir."""
    import hashlib

    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _tx(month: str | None, title: str, **f) -> dict:
    """İşlemler hedefi (mobil: #/islemler/suz/…): month None ise tüm aylar; f: kind, category, merchant, uids, fx, pending, awaiting."""
    return {"screen": "islemler", "month": month, "title": title, **{k: v for k, v in f.items() if v not in (None, False, [], "")}}


# uyarının "link"i (web sayfası) → platformdan bağımsız hedef ekranı (target.screen)
LINK_TARGET = {"plan": "planlama", "zekat": "zekat", "hesaplar": "hesaplar", "tasarruf": "tasarruf", "borclar": "borclar",
               "fiyatlar": "fiyatlar", "ekstreler": "ekstreler", "transferler": "transferler", "istatistik": "istatistik"}
# hedef ekranı → özellik (prefs.MODULES): özellik kapalıysa o hedefe gidilmez
TARGET_MODULE = {"planlama": "plan", "taksitler": "plan", "oneriler": "plan", "zekat": "zekat", "hesaplar": "hesaplar",
                 "kumbara": "hesaplar", "tasarruf": "tasarruf", "abonelikler": "tasarruf", "borclar": "borclar",
                 "fiyatlar": "fiyatlar", "transferler": "transferler", "istatistik": "istatistik"}


def _pay_reason(st: dict) -> dict | None:
    """Faizin ödeme kaynaklı nedeni (db.interest_charges satırı): önceki ekstre geç mi, eksik mi ödendi, hiç mi ödenmedi.
    {"kind": "gec" | "eksik" | "odenmedi", "text", "days"?, "due_date", "paid", "debt"}; önceki ekstre yoksa None."""
    prev, pays = st["prev"], st["payments"]
    if not (prev and prev["due_date"]):
        return None
    debt = prev["total_debt"] or 0
    paid = round(sum(p["amount"] for p in pays), 2)
    on_time = round(sum(p["amount"] for p in pays if p["pay_date"] <= prev["due_date"]), 2)
    due = date.fromisoformat(prev["due_date"])
    base = {"due_date": prev["due_date"], "paid": paid, "debt": debt}
    if pays and on_time < debt - 1:
        first_late = next((p for p in pays if p["pay_date"] > prev["due_date"]), None)
        if first_late:
            days = (date.fromisoformat(first_late["pay_date"]) - due).days
            return {**base, "kind": "gec", "days": days, "pay_date": first_late["pay_date"],
                    "text": f"önceki ekstrenin son ödemesi {due:%d.%m}, ödeme {date.fromisoformat(first_late['pay_date']):%d.%m}: "
                            f"{days} gün geç. Bir gün geç ödemek bile dönem borcunun tamamına faiz işletir"}
        if paid < debt - 1:
            return {**base, "kind": "eksik",
                    "text": f"önceki dönem borcundan {_tl(paid)} ödendi, kalan {_tl(debt - paid)} için faiz işledi"}
    elif not pays:
        return {**base, "kind": "odenmedi",
                "text": f"önceki ekstreye ({_tl(debt)}, son ödeme {due:%d.%m}) ödeme görünmüyor"}
    return None


def interest_alarm(month: str) -> list[dict]:
    """Faiz alarmı: bu dönem ödenen faiz / vade farkı ve NEDENİ (geç ödeme, eksik ödeme, taksit vade farkı)."""
    from app import book

    if book.hide_fees():  # ortak defterde faiz gizli
        return []
    out = []
    for st in db.interest_charges(month):
        card = f"{st['bank']} …{st['card_no'][-4:]}"
        total = round(sum(x["amount"] for x in st["lines"]), 2)
        if total <= 0:
            continue
        keys = [(x, _fold(x["description"])) for x in st["lines"]]
        late = sum(x["amount"] for x, k in keys if "GECIKME" in k)
        shop = sum(x["amount"] for x, k in keys if "ALISVERIS" in k or "AKDI" in k)
        vade = [(x["description"].split("-", 1)[-1].strip(), x["amount"]) for x, k in keys if "VADEFARK" in k]
        reasons = []
        pr = _pay_reason(st) if late or shop else None
        if pr:
            reasons.append(pr["text"])
        if vade:
            reasons.append("taksitli işlemlere vade farkı eklendi: " + ", ".join(f"{m} {_tl(a)}" for m, a in vade[:5])
                           + ". Taksit yerine tek çekim seçersen vade farkı ödemezsin")
        out.append({"level": "kritik", "title": f"Faiz alarmı: {card} bu dönem {_tl(total)} faiz ve vergi",
                    "detail": ("Neden: " + "; ".join(reasons) + "." if reasons else
                               "Ekstrede faiz, vade farkı ya da faize bağlı vergi var.")
                              + (f" (gecikme faizi {_tl(late)}, alışveriş faizi {_tl(shop)})" if late or shop else "")
                              + " Önlem: bankada son ödeme gününde dönem borcunun tamamı için otomatik ödeme talimatı ver.",
                    "filter": {"kind": FEE}, "key": f"faiz:{_slug(card)}:{month}"})
    # istemeden gelen faiz geliri (vadesizde gecelik faiz vb.): arındır
    inc = [r for r in transactions(month) if r["kind"] == INCOME and not r.get("pending")
           and any(k in _fold(r["description"]) for k in ("FAIZ", "FAİZ", "NEMA"))]
    if inc:
        out.append({"level": "uyari", "title": f"Faiz geliri: {_tl(sum(r['amount'] for r in inc))} (arındırılacak)",
                    "detail": "Hesabına faiz işlemiş görünüyor. Faizden uzak durmak istiyorsan bu tutarı sevap beklemeden "
                              "ihtiyaç sahibine vererek arındırabilirsin; hesap türünü (ör. katılım ya da faizsiz vadesiz) kontrol et.",
                    "filter": {"kind": INCOME}, "key": f"faiz_geliri:{month}:{_fp(*sorted(r['uid'] for r in inc))}",
                    "target": _tx(month, "Faiz geliri", kind=INCOME, uids=[r["uid"] for r in inc])})
    return out


def interest_history(months: int = 24) -> dict:
    """Ekstrelerdeki faiz / vade farkı / faize bağlı vergi, dönem dönem (İstatistik → Faiz alarmı)."""
    from app import book

    if book.hide_fees():
        return {"months": [], "total": 0.0, "checked": 0, "hidden": True}
    periods = sorted({st["period_end"][:7] for st in db.list_statements()}, reverse=True)[:months]
    out = []
    for m in periods:
        alarms = interest_alarm(m)
        total = round(sum(sum(x["amount"] for x in st["lines"]) for st in db.interest_charges(m)), 2)
        if total > 0 or alarms:
            out.append({"month": m, "total": total, "alarms": alarms})
    return {"months": out, "total": round(sum(x["total"] for x in out), 2), "checked": len(periods)}


# faiz / ücret satırının türü (açıklamadan; fold_key: büyük harf, Türkçe karaktersiz, boşluksuz). Sıra önemli: "BSMV - ..."
# vergidir, "NAKIT AVANS FAIZI" nakit avanstır.
INTEREST_TYPES = (
    ("vergi", "Vergi (BSMV / KKDF)", ("BSMV", "KKDF", "VERGI")),
    ("nakit_avans", "Nakit avans faizi", ("NAKITAVANS", "AVANSFAIZ", "NAKITCEKIM")),
    ("gecikme", "Gecikme faizi", ("GECIKME",)),
    ("vade_farki", "Taksit vade farkı", ("VADEFARK",)),
    ("akdi", "Alışveriş (akdi) faizi", ("ALISVERIS", "AKDI")),
    ("kar_payi", "Kâr payı", ("KARPAYI",)),
    ("ucret", "Kart aidatı / ücret", ("AIDAT", "UCRET", "MASRAF", "KOMISYON")),
)
INTEREST_LABELS = {k: label for k, label, _ in INTEREST_TYPES} | {"faiz": "Faiz"}
TAX_KINDS = ("vergi",)
FEE_KINDS = ("ucret",)


def interest_type(desc: str) -> str:
    k = _fold(desc)
    return next((key for key, _, words in INTEREST_TYPES if any(w in k for w in words)), "faiz")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _interest_reason(kind: str, desc: str, pr: dict | None) -> str:
    """Satırın kısa nedeni: ödeme kaynaklı (geç / eksik / ödenmedi) bilgi varsa o, yoksa türün genel açıklaması."""
    if kind in ("gecikme", "akdi", "faiz", "kar_payi") and pr:
        return _cap(pr["text"]) + "."
    if kind == "gecikme":
        return "Son ödeme gününden sonra ödendiği için gecikme faizi işledi."
    if kind in ("akdi", "faiz", "kar_payi"):
        return "Dönem borcunun tamamı son ödeme gününe kadar ödenmediğinde (asgari ya da eksik ödeme) kalan borca faiz işler."
    if kind == "vade_farki":
        m = desc.split("-", 1)[-1].strip() if "-" in desc else ""
        return f"Taksitli alışverişe eklenen vade farkı{f' ({m})' if m else ''}. Tek çekimde vade farkı olmaz."
    if kind == "vergi":
        return "Faize ve vade farkına eklenen vergi (BSMV, KKDF): faiz olmazsa vergi de olmaz."
    if kind == "nakit_avans":
        return "Nakit avansta faiz çekildiği gün işlemeye başlar."
    if kind == "ucret":
        return "Bankanın kart aidatı ya da hizmet ücreti."
    return "Ekstrede faiz ya da ücret olarak görünüyor."


def interest_report(months: int = 12, today: date | None = None) -> dict:
    """Faiz ve ücretler sayfası (GET /api/interest): son `months` dönemde (0: tüm geçmiş) kart ekstrelerindeki faiz, vade
    farkı, faize bağlı vergi ve kart ücretleri; ay ay, kart kart, kalem kalem ve NEDENİ (interest_alarm ile aynı mantık:
    _pay_reason), veride karşılığı olan önleme önerileri ve hesaba işleyen faiz geliri. Ortak defterde faiz gizliyse
    (book.hide_fees) boş döner: hidden=True. Gizli kart ve gizli işaretli satır üyeye zaten gelmez (db süzgeçleri)."""
    from app import book

    today = today or date.today()
    cur = current_period(today)
    empty = {"hidden": False, "months": months, "from": None, "to": cur, "total": 0.0, "interest": 0.0, "tax": 0.0,
             "fee": 0.0, "count": 0, "by_month": [], "by_card": [], "by_type": [], "statements": [], "items": [],
             "tips": [], "income": {"total": 0.0, "items": []}}
    if book.hide_fees():
        return {**empty, "hidden": True}
    start = add_months(cur, -(months - 1)) if months > 0 else None
    fees = [t for t in db.card_transactions() if t["txn_type"] == FEE and (not start or t["period"] >= start)]
    # nedeni için önceki ekstre ve ona yapılan ödemeler (dönem başına bir sorgu)
    charges = {st["lines"][0]["statement_id"]: st for p in sorted({t["period"] for t in fees}) for st in db.interest_charges(p)}
    reasons = {sid: _pay_reason(st) for sid, st in charges.items()}
    stmts = {s["id"]: s for s in db.list_statements()}
    items = []
    for t in fees:
        kind = interest_type(t["description"])
        sid = t["statement_id"]
        s = stmts.get(sid) or {}
        items.append({
            "uid": f"c{t['id']}", "id": t["id"], "date": t["txn_date"], "month": t["period"], "statement_id": sid,
            "period_end": t["period_end"], "due_date": s.get("due_date"), "bank": t["bank"], "card_no": t["account"],
            "last4": t["account"][-4:], "card": f"{t['bank']} …{t['account'][-4:]}", "description": t["description"],
            "type": kind, "type_label": INTEREST_LABELS[kind], "amount": _round(t["amount"]),
            "reason": _interest_reason(kind, t["description"], reasons.get(sid)),
        })
    start = start or (min(i["month"] for i in items) if items else cur)
    by_m: dict[str, float] = defaultdict(float)
    by_t: dict[str, float] = defaultdict(float)
    by_c: dict[str, dict] = {}
    by_s: dict[int, dict] = {}
    for i in items:
        by_m[i["month"]] += i["amount"]
        by_t[i["type"]] += i["amount"]
        c = by_c.setdefault(i["card_no"], {"card_no": i["card_no"], "last4": i["last4"], "bank": i["bank"], "card": i["card"],
                                           "total": 0.0, "count": 0, "last_date": i["date"]})
        c["total"] += i["amount"]
        c["count"] += 1
        c["last_date"] = max(c["last_date"], i["date"])
        st = by_s.setdefault(i["statement_id"], {"statement_id": i["statement_id"], "card": i["card"], "card_no": i["card_no"],
                                                 "period_end": i["period_end"], "due_date": i["due_date"], "month": i["month"],
                                                 "total": 0.0})
        st["total"] += i["amount"]
    for sid, st in by_s.items():
        pr = reasons.get(sid)
        st["total"] = _round(st["total"])
        st["reason"] = _cap(pr["text"]) if pr else None
        st["reason_kind"] = pr["kind"] if pr else None  # gec | eksik | odenmedi | None
    total = _round(sum(i["amount"] for i in items))
    tax = _round(sum(i["amount"] for i in items if i["type"] in TAX_KINDS))
    fee = _round(sum(i["amount"] for i in items if i["type"] in FEE_KINDS))
    # istemeden gelen faiz geliri (vadesizde gecelik faiz vb.): interest_alarm ile aynı ölçüt
    inc = [r for r in transactions(None) if r["kind"] == INCOME and not r.get("pending") and r["month"] >= start
           and any(k in _fold(r["description"]) for k in ("FAIZ", "NEMA"))]
    return {
        **empty, "from": start, "total": total, "interest": _round(total - tax - fee), "tax": tax, "fee": fee,
        "count": len(items),
        "by_month": [{"month": m, "total": _round(by_m.get(m, 0))} for m in month_range(start, max(cur, start))],
        "by_card": sorted(({**c, "total": _round(c["total"])} for c in by_c.values()), key=lambda c: -c["total"]),
        "by_type": sorted(({"type": k, "label": INTEREST_LABELS[k], "total": _round(v)} for k, v in by_t.items()),
                          key=lambda x: -x["total"]),
        "statements": sorted(by_s.values(), key=lambda x: x["period_end"], reverse=True),
        "items": items,
        "tips": _interest_tips(items, list(by_s.values()), stmts, f"Son {months} ayda" if months > 0 else "Şimdiye dek"),
        "income": {"total": _round(sum(r["amount"] for r in inc)),
                   "items": [{"uid": r["uid"], "date": r["date"], "description": r["description"], "amount": _round(r["amount"])}
                             for r in sorted(inc, key=lambda r: r["date"], reverse=True)]},
    }


def _interest_tips(items: list[dict], stmts: list[dict], all_stmts: dict[int, dict], span: str) -> list[dict]:
    """Faizi önleme önerileri: yalnız kullanıcının verisinde karşılığı olanlar (ör. vade farkı yoksa "tek çekim" yok).
    span: cümlenin zaman öbeği ("Son 12 ayda")."""
    def total(*kinds):
        return _round(sum(i["amount"] for i in items if i["type"] in kinds))

    out = []
    late = [s for s in stmts if s.get("reason_kind") in ("gec", "odenmedi")]
    if total("gecikme") or late:
        # kartın bilinen sıradaki son ödeme günü (en yeni ekstrenin "sonraki son ödeme"si, yoksa son ödemesi)
        days = []
        for card in sorted({s["card_no"] for s in late} | {i["card_no"] for i in items if i["type"] == "gecikme"}):
            st = max((x for x in all_stmts.values() if x["card_no"] == card), key=lambda x: x["period_end"], default=None)
            d = st and (st.get("next_due_date") or st.get("due_date"))
            if d:
                days.append(f"{st['bank']} …{card[-4:]} {date.fromisoformat(d):%d.%m}")
        out.append({"key": "otomatik_odeme", "title": "Son ödeme gününü kaçırma",
                    "detail": ("Sıradaki son ödeme: " + ", ".join(days) + ". " if days else "")
                              + "Bankada dönem borcunun tamamı için otomatik ödeme talimatı ver; bir gün gecikme bile "
                                "gecikme faizi ve vergisi doğurur."})
    shop = total("akdi", "faiz", "kar_payi")
    if shop or any(s.get("reason_kind") == "eksik" for s in stmts):
        out.append({"key": "tamamini_ode", "title": "Asgari değil, borcun tamamını öde",
                    "detail": (f"{span} {_tl(shop)} faiz ödedin. " if shop else "")
                              + "Asgari ya da eksik ödeme kalan borca faiz işletir; ekstre borcunun tamamını son ödeme "
                                "gününe kadar öde."})
    if total("vade_farki"):
        n = len({i["description"].split("-", 1)[-1].strip() for i in items if i["type"] == "vade_farki"})
        out.append({"key": "tek_cekim", "title": "Taksit yerine tek çekim",
                    "detail": f"{span} {n} taksitli alışverişe {_tl(total('vade_farki'))} vade farkı eklendi. Tek çekim yaparsan "
                              "ya da biriktirip peşin alırsan bu tutarı ve vergisini ödemezsin."})
    if total("nakit_avans"):
        out.append({"key": "nakit_avans", "title": "Karttan nakit çekme",
                    "detail": f"{span} nakit avans için {_tl(total('nakit_avans'))} faiz ödedin. Nakit avansta faiz ilk günden "
                              "işler; nakit ihtiyacını vadesiz hesaptan karşıla."})
    if total("ucret"):
        cards = ", ".join(sorted({i["card"] for i in items if i["type"] == "ucret"}))
        out.append({"key": "aidat", "title": "Kart aidatı ve ücretler",
                    "detail": f"{cards}: {_tl(total('ucret'))} ücret. Bankadan aidat iadesi iste ya da aidatsız bir "
                              "karta geç."})
    return out


def insights(month: str, today: date | None = None) -> list[dict]:
    """Seçili ay + önümüzdeki aylar için dikkat edilmesi gerekenler. level: kritik | uyari | bilgi | iyi"""
    today = today or date.today()
    out = []
    rows = transactions(month)
    live = [r for r in rows if not r.get("pending") and not r.get("skipped")]
    t = _totals(rows)
    statements = db.list_statements()
    p = plan(6, today=today)

    # 1) Faiz
    fee_rows = [r for r in live if r["kind"] == FEE]
    if fee_rows:
        out += interest_alarm(month) or [{
            "level": "kritik", "title": f"{_tl(t['fees'])} faiz ve vergi ödedin",
            "detail": "Ekstre borcunun tamamı son ödeme tarihine kadar ödenmediğinde alışveriş faizi, geç "
                      "ödendiğinde gecikme faizi işler; üstüne BSMV ve KKDF eklenir. Asgari değil, toplam borcu öde.",
            "filter": {"kind": FEE}, "key": f"faiz:{month}"}]

    # 2) Gecikmiş veya kısmi ödenmiş (yaklaşanlar "Ödemeler" kartında)
    for x in payables(today):
        if x["status"] == "gecikti":
            out.append({"level": "kritik", "title": f"{x['title']}: son ödeme {abs(x['days_left'])} gün geçti",
                        "detail": f"{_tl(x['amount'])} ödenmedi olarak görünüyor. Ödediysen \"Ödedim\" ile işaretle; "
                                  "ödemediysen gecikme faizi işliyor.",
                        "pay": {"type": x["type"], "id": x["id"]}, "key": f"gecikti:{x['type']}:{x['id']}:{x.get('due_date') or ''}",
                        "target": {"screen": "odemeler", "type": x["type"], "id": x["id"]}})
        elif x["status"] == "kismi":
            out.append({"level": "uyari", "title": f"{x['title']}: {_tl(x['amount'] - x['paid_amount'])} kaldı",
                        "detail": "Kısmi ödeme yaptın; kalan borca faiz işler.",
                        "pay": {"type": x["type"], "id": x["id"]},
                        "key": f"kismi:{x['type']}:{x['id']}:{_round(x['amount'] - x['paid_amount']):g}",
                        "target": {"screen": "odemeler", "type": x["type"], "id": x["id"]}})

    # 2-) Altın/döviz kumbarası: bu ayın alım günü geldi, henüz alınmadı
    for j in db.list_jars(today, include_closed=False):
        if (j.get("unit") or "TRY") == "TRY" or not j["monthly"] or not j["auto_day"] or today.day < j["auto_day"]:
            continue
        if sum(m["amount"] for m in j["moves"] if m["move_date"][:7] == today.strftime("%Y-%m") and m["cost"]) >= j["monthly"] - 1e-9:
            continue
        from app import zakat

        label, _, sym = zakat.KINDS[j["unit"]]
        try:
            approx = zakat._value_at(j["unit"], j["monthly"], zakat.prices()["prices"])
        except Exception:  # noqa: BLE001
            approx = None
        qty = f"{j['monthly']:g} {sym}" if sym in ("gr", "adet") else f"{sym}{j['monthly']:g}"
        out.append({"level": "bilgi", "title": f"{j['name']}: bu ay {qty} {label.lower()} alma günü"
                    + (f" (≈ {_tl(approx)})" if approx else ""),
                    "detail": "Aldıktan sonra Hesaplar → Kumbaralar'da \"Aldım\" de; altın varlıklarına alış tarihiyle eklenir.",
                    "link": "hesaplar", "key": f"kumbara:{j['id']}:{today:%Y-%m}", "target": {"screen": "kumbara", "id": j["id"]}})

    # 2a) Ramazan / fitre / Kurban yaklaşıyor
    try:
        from app import zakat as _z

        out += _z.holy_insights(today)
    except Exception:  # noqa: BLE001
        pass

    # 2b) Her ay tekrarlayan ama sabit kalem olmayan ödemeler: planlama gelecek ayları bilmiyor
    try:
        sug = recurring_suggestions(today)
    except Exception:  # noqa: BLE001 - öneri hesabı özet sayfasını bozmasın
        sug = []
    if sug:
        top = ", ".join(f"{(s['category'] or 'ödeme').split(' & ')[0]} {_tl(s['amount'])}" for s in sug[:2])  # kişi adı yok
        out.append({"level": "bilgi", "title": f"Her ay tekrarlayan {len(sug)} ödeme sabit kalem değil",
                    "detail": f"{top}{' …' if len(sug) > 2 else ''}. Sabit kalem yaparsan gelecek aylar planlamaya girer.",
                    "link": "plan", "key": f"sabit_oneri:{_fp(*sorted(s['key'] for s in sug))}", "target": {"screen": "oneriler"}})

    # 1z) Zekât günü / ek kontrol günü yaklaşıyor
    try:
        from app import zakat

        for z in zakat.upcoming_checks():
            when = "bugün" if z["days_left"] == 0 else f"{z['days_left']} gün sonra" if z["days_left"] > 0 else f"{-z['days_left']} gün önceydi"
            if z["kind"] == "zekat":
                out.append({"level": "uyari", "title": f"🌙 {z['name']}: zekât günü {when} ({_tl(z['amount'])})",
                            "detail": f"{z['date'][8:10]}.{z['date'][5:7]}.{z['date'][:4]} · {z['hijri']}. Verince Zekât sayfasında \"Ödeme ekle\".",
                            "link": "zekat", "key": f"zekat:{_fp(z['name'])}:{z['date']}"})
            else:
                out.append({"level": "uyari", "title": f"🌙 {z['name']}: zekât kontrolü {when}",
                            "detail": f"{z['date'][8:10]}.{z['date'][5:7]}.{z['date'][:4]} · {z['hijri']}. Bugünkü toplamla yeniden hesapla; "
                                      f"fark {_tl(z['amount'])}" + (" — tamamla." if z["amount"] > 0 else " — ek ödeme gerekmiyor."),
                            "link": "zekat", "key": f"zekat_kontrol:{_fp(z['name'])}:{z['date']}"})
    except Exception:  # noqa: BLE001 - zekât (fiyat servisi) özeti bozmasın
        pass

    # 2a) Maaş görünmeyen aylar: ilk ve son maaş arasında boşluk → o aylara ait döküm eksik
    sal_ids = {r["id"] for r in db.list_recurring() if _is_salary(r)}
    sal = sorted({e["month"] for e in db.list_entries() if e["kind"] == INCOME and not e["skipped"] and _salary_entry(e, sal_ids)})
    if len(sal) >= 2:
        gaps, m = [], sal[0]
        while m < sal[-1]:
            if m not in sal:
                gaps.append(m)
            m = add_months(m, 1)
        if gaps:
            out.append({"level": "uyari", "title": f"{len(gaps)} ayda maaş dökümü yok",
                        "detail": f"{period_name(gaps[0])} – {period_name(gaps[-1])} dönemleri arasında dökümde maaş yok. "
                                  "Maaş kalemin varsa o aylarda tahmini tutar gelire yazılır; kesin tutar için maaşın yattığı hesabın bu aylara "
                                  "ait dökümünü yükle.",
                        "link": "ekstreler", "key": f"maas_yok:{gaps[0]}:{gaps[-1]}:{len(gaps)}", "target": {"screen": "ekstreler"}})

    # 2b) Hesaptan kart ödemesi var ama ekstresi yok → o dönemin harcamaları hiç sayılmamış
    # (kartın yüklenen ilk ekstresinden eski ödemeler — bankanın arşivi — hiç gösterilmez)
    missing: dict[str, list] = defaultdict(list)
    for x in db.card_payments_without_statement():
        if not x["before_first"]:
            missing[x["card_last4"] or "?"].append(x)
    for last4, ps in missing.items():
        bank = next((x["account"] for x in ps if x["account"]), "")
        bank = re.sub(r"\s+(KATILIM\s+)?BANKASI.*$", "", bank, flags=re.I).strip()
        card = f"{bank} …{last4}" if last4 != "?" else "Bir kart"
        out.append({"level": "uyari",
                    "title": f"{card}: {len(ps)} ödemenin ekstresi yüklenmemiş ({_tl(sum(x['amount'] for x in ps))})",
                    "detail": "Hesabından bu karta ödeme yapılmış ama o döneme ait kart ekstresi yok; o dönemin kart harcaması "
                              "bilinmiyor. Ödenen tutar harcamana katılmadı, \"tahmini\" olarak ayrı gösteriliyor. Ödemeler: "
                              + ", ".join(f"{x['pay_date'][8:10]}.{x['pay_date'][5:7]} {_tl(x['amount'])}" for x in ps)
                              + ". Ekstreler sayfasından o ayların kart ekstresini yükle.",
                    "link": "ekstreler", "key": f"ekstre_yok:{last4}:{_fp(*sorted(x['pay_date'] for x in ps))}"})

    # 2c) Ödemeler kartında görünmeyen açık borçlar (vadesiz ya da döviz/altın)
    open_debt = [d for d in db.list_debts(open_only=True) if d["direction"] == "borc" and d["remaining"] > 0
                 and (d["asset"] != "TRY" or not d["due_date"])]
    if open_debt:
        out.append({"level": "bilgi", "title": f"{len(open_debt)} açık borcun var",
                    "detail": " · ".join(f"{d['person']}: {fmt_asset(d['remaining'], d['asset'])}"
                                         + (f" (vade {date.fromisoformat(d['due_date']):%d.%m})" if d["due_date"] else "")
                                         for d in open_debt) + ". Borç / Alacak sayfasından takip et.", "link": "borclar",
                    "key": f"borc:{_fp(*sorted((d['id'], d['remaining']) for d in open_debt))}",
                    "target": {"screen": "borclar", **({"person": open_debt[0]["person"]} if len(open_debt) == 1 else {})}})

    # 2e) Fiyatı belirgin artan ürünler (fişlerden; son 120 gün içinde alınmış olanlar)
    recent = (today - timedelta(days=120)).isoformat()
    up = [p for p in db.price_history() if p["change_pct"] >= 10 and p["last_date"] >= recent][:4]
    if up:
        out.append({"level": "bilgi", "title": f"{len(up)} üründe zam var (fişlerinden)",
                    "detail": " · ".join(f"{p['product']}: {_tl(p['first_price'])} → {_tl(p['last_price'])} (+%{p['change_pct']:g})"
                                         for p in up) + ".", "link": "fiyatlar",
                    "key": f"zam:{_fp(*((p['product'], p['last_price']) for p in up))}"})

    # 2d) Bekleyen alacaklar (sana borçlu olanlar)
    # kapanmış (settled_at) ya da kalanı sıfır olan (tamamı tahsil edilmiş / vazgeçilmiş) alacak sayılmaz
    open_rec = [d for d in db.list_debts(open_only=True) if d["direction"] == "alacak" and d["remaining"] > 0]
    if open_rec:
        parts = [f"{d['person']}: {fmt_asset(d['remaining'], d['asset'])}"
                 + (f" (vade {date.fromisoformat(d['due_date']):%d.%m})" if d["due_date"] else
                    f" ({(today - date.fromisoformat(d['debt_date'])).days} gündür)") for d in open_rec]
        overdue = any(d["due_date"] and d["due_date"] < today.isoformat() for d in open_rec)
        out.append({"level": "uyari" if overdue else "bilgi",
                    "title": f"{len(open_rec)} alacağın var" + (" — vadesi geçen var" if overdue else ""),
                    "detail": " · ".join(parts) + ". Borç / Alacak sayfasından takip et; havale gelince TL alacaklar "
                              "kendiliğinden kapanır.", "link": "borclar",
                    "key": f"alacak:{_fp(*sorted((d['id'], d['remaining']) for d in open_rec))}:{int(overdue)}",
                    "target": {"screen": "borclar", **({"person": open_rec[0]["person"]} if len(open_rec) == 1 else {})}})

    # 3) Açık veren aylar
    has_income = any(r["kind"] == INCOME for r in p["recurring"]) or any(
        l["kind"] == INCOME for mm in p["months"] for l in mm["lines"])
    if has_income:
        # Sadece kesin kalemler: tahmini kart harcaması "açık" sayılmaz
        neg = [mm for mm in p["months"] if mm["net"] < 0]
        if neg:
            worst = min(neg, key=lambda mm: mm["net"])
            out.append({"level": "kritik",
                        "title": f"Önümüzdeki {len(p['months'])} aydan {len(neg)} tanesinde kesin giderler geliri aşıyor",
                        "detail": f"Toplam açık {_tl(-sum(mm['net'] for mm in neg))}; en kötü ay {worst['month']} "
                                  f"({_tl(-worst['net'])}). Taksit, abonelik, sabit gider ve faturalar dahil.",
                        "link": "plan", "key": f"acik:{current_period(today)}:{len(neg)}"})
        else:
            tight = [mm for mm in p["months"] if mm["net_with_estimate"] < 0 and mm["estimated"]]
            if tight:
                out.append({"level": "bilgi",
                            "title": f"Harcamalar son aylardaki gibi giderse {len(tight)} ayda sıkışabilirsin",
                            "detail": f"Kesin giderler gelirin altında; ama tahmini kart harcamasıyla {tight[0]['month']} "
                                      f"için net {_tl(tight[0]['net_with_estimate'])}. Bütçe veya kısma hedefi koymayı düşün.",
                            "link": "plan", "key": f"sikisma:{current_period(today)}:{len(tight)}"})
    else:
        out.append({"level": "bilgi", "title": "Gelirin tanımlı değil",
                    "detail": "Maaşını Planlama → Sabit kalemler'e eklersen her ay ne kadar artı/eksi verdiğini görürsün.",
                    "link": "plan", "soft_link": True, "key": "gelir_yok"})

    # 4) Taksit yükü
    next_m = add_months(current_period(today), 1)
    nxt = next((mm for mm in p["months"] if mm["month"] == next_m), None)
    if nxt and nxt["groups"].get("taksit"):
        inst = nxt["groups"]["taksit"]
        msg = f"{next_m} ayına {_tl(inst)} taksit devrediyor."
        if nxt["income"]:
            msg += f" Gelirine oranı: %{inst / nxt['income'] * 100:.0f}."
        out.append({"level": "uyari" if nxt["income"] and inst / nxt["income"] > 0.3 else "bilgi",
                    "title": "Gelecek ayın taksit yükü", "detail": msg, "link": "plan", "soft_link": True,
                    "key": f"taksit_yuku:{next_m}", "target": {"screen": "taksitler"}})

    # 5a) Taksitlendirme maliyeti (vade farkı/kâr payı + BSMV + KKDF taksitleri)
    fee_inst = [i for acc in p["projection"] for i in acc["installments"] if i.get("fee")]
    if fee_inst:
        total = sum(i["amount"] for i in fee_inst)
        months_left = len({i["month"] for i in fee_inst})
        out.append({"level": "uyari", "title": f"Taksitlendirme sana {_tl(total)} daha vade farkı ve vergi ödetecek",
                    "detail": f"Önümüzdeki {months_left} ay boyunca. Taksitli alışverişte vade farkı tutarın ~%25-35'ine "
                              "çıkabiliyor; mümkünse tek çekim yap ya da taksitleri erken kapat.",
                    "filter": {"kind": FEE}, "key": f"vade_farki:{current_period(today)}:{_round(total):g}",
                    "target": {"screen": "taksitler"}})

    # 5) Biten taksitler (rahatlama)
    ending: dict[str, list] = defaultdict(list)
    for acc in p["projection"]:
        for i in acc["installments"]:
            n, total = map(int, i["installment"].split("/"))
            if n == total and i["amount"] > 0 and not i.get("fee"):
                ending[i["month"]].append(i)
    for m, items in sorted(ending.items())[:2]:
        top = ", ".join(i["description"] for i in sorted(items, key=lambda i: -i["amount"])[:3])
        out.append({"level": "iyi", "title": f"{m} ekstresinden sonra aylık {_tl(sum(i['amount'] for i in items))} taksit bitiyor",
                    "detail": f"Son taksitler: {top}", "link": "plan", "soft_link": True,
                    "key": f"taksit_bitiyor:{m}", "target": {"screen": "taksitler"}})

    # 5b) Bütçe
    for b in budgets(month):
        if b["pct"] >= 100:
            out.append({"level": "kritik", "title": f"{b['category']} bütçesi aşıldı (%{b['pct']:.0f})",
                        "detail": f"{_tl(b['spent'])} / {_tl(b['limit'])} · {_tl(-b['remaining'])} fazla",
                        "filter": {"category": b["category"]}, "key": f"butce:{_slug(b['category'])}:{month}"})
        elif b["pct"] >= 80:
            out.append({"level": "uyari", "title": f"{b['category']}: bütçenin %{b['pct']:.0f} kadarı harcandı",
                        "detail": f"Kalan {_tl(b['remaining'])} (limit {_tl(b['limit'])})", "filter": {"category": b["category"]},
                        "key": f"butce_yakin:{_slug(b['category'])}:{month}"})

    # 6) Kategori artışı
    prev = add_months(month, -1)
    prev_rows = [r for r in transactions(prev) if not r.get("pending") and not r.get("skipped")]
    accounts_now = {st["card_no"] for st in statements if st["period"] == month}
    accounts_prev = {st["card_no"] for st in statements if st["period"] == prev}
    # Ekstre kapsamı farklıysa (ör. önceki ayın ekstresi yüklenmemiş) kıyas yanıltıcı olur
    if prev_rows and accounts_now == accounts_prev:
        cur_c = {g["name"]: g["amount"] for g in _group([r for r in live if _spends(r, (*CARD_SPEND, EXPENSE))], "category")}
        prev_c = {g["name"]: g["amount"] for g in _group([r for r in prev_rows if _spends(r, (*CARD_SPEND, EXPENSE))], "category")}
        for cat, amt in sorted(cur_c.items(), key=lambda x: -x[1]):
            base = prev_c.get(cat, 0)
            if base > 500 and amt > base * 1.3 and amt - base > 1000:
                out.append({"level": "uyari", "title": f"{cat} harcaması %{(amt / base - 1) * 100:.0f} arttı",
                            "detail": f"{prev}: {_tl(base)} → {month}: {_tl(amt)}", "filter": {"category": cat},
                            "key": f"artis:{_slug(cat)}:{month}"})

    # 7) Abonelikler
    for sub in subscriptions():
        if sub["status"] == "iptal" and sub["charged_after_cancel"]:
            out.append({"level": "kritik", "title": f"{sub['merchant'].title()} iptal edilmişti ama yine çekildi",
                        "detail": f"{date.fromisoformat(sub['cancelled_at']):%d.%m} iptalinden sonra {_tl(sub['charged_after_cancel'])} "
                                  "çekim var. İptal tamamlanmamış olabilir; hesabını kontrol et, iade iste.", "link": "tasarruf",
                        "key": f"iptal_cekim:{_slug(sub['merchant'])}:{_round(sub['charged_after_cancel']):g}",
                        "target": {"screen": "abonelikler"}})
        if sub["status"] == "dusuruldu" and sub["overcharged"]:
            out.append({"level": "uyari", "title": f"{sub['merchant'].title()} paketi düşmemiş görünüyor",
                        "detail": f"Yeni paket {_tl(sub['new_amount'])} olmalıydı; {_tl(sub['overcharged'])} fazla çekildi.",
                        "link": "tasarruf", "key": f"paket:{_slug(sub['merchant'])}:{_round(sub['overcharged']):g}",
                        "target": {"screen": "abonelikler"}})
    # 7a) Aynı abonelik satıcısına kısa sürede benzer tutarlı birden çok ödeme: çift üyelik / çift çekim olabilir
    out += duplicate_charges(today)
    subs = [s for acc in p["projection"] for s in acc["subscriptions"]]
    if subs:
        total = sum(s["amount"] for s in subs)
        names = ", ".join(s["description"].title() for s in sorted(subs, key=lambda s: -s["amount"])[:5])
        out.append({"level": "bilgi", "title": f"Aylık abonelik yükü ≈ {_tl(total)}",
                    "detail": f"{len(subs)} abonelik: {names}… Tasarruf sayfasından kullanmadıklarını iptal edildi diye işaretle.",
                    "link": "tasarruf", "key": f"abonelik_yuku:{_round(total):g}", "target": {"screen": "abonelikler"}})

    # 7b) Kısma hedefleri: bu ay hedefi aştıysa
    for g in goals_review(today):
        cur = next((m for m in g["months"] if m["month"] == month), None)
        if g["active"] and cur and cur["spent"] > g["target"]:
            out.append({"level": "uyari", "title": f"{g['name']} hedefini aştın: {_tl(cur['spent'])} / {_tl(g['target'])}",
                        "detail": g["note"] or "Kısma hedefi", "link": "tasarruf",
                        "key": f"hedef:{g.get('id') or _slug(g['name'])}:{month}"})

    # 8) Döviz
    fx = [r for r in live if r.get("fx")]
    if fx:
        out.append({"level": "bilgi", "title": f"{_tl(sum(r['amount'] for r in fx))} dövizli harcama",
                    "detail": f"{len(fx)} işlem dolar/euro bazlı; kur artınca bu kalemler kendiliğinden pahalanır.",
                    "filter": {"fx": True}, "key": f"doviz:{month}"})

    # 9) Limit kullanımı
    for s in statements:
        if s["period"] == month and s["credit_limit"]:
            unpaid = max(0.0, s["total_debt"] - (s["paid_amount"] or 0))
            ratio = unpaid / s["credit_limit"]
            if ratio > 0.5:
                out.append({"level": "uyari", "title": f"{s['card_no']} limit doluluğu: %{ratio * 100:.0f}",
                            "detail": "Ödenmemiş ekstre borcu limite göre yüksek; tamamı son ödeme gününe kadar "
                                      "ödenmezse kalan borca faiz işler.",
                            "link": "ekstreler", "key": f"limit:{_slug(s['card_no'])}:{month}"})

    # 9b) Aynı gelir bu dönemde birden fazla (ör. ay dönümü kayınca ya da yanlış tarihle girilince)
    incomes = defaultdict(list)
    for r in live:
        if r["kind"] == INCOME:
            incomes[r["description"].strip().casefold()].append(r)
    for rs in incomes.values():
        if len(rs) > 1:
            dates = ", ".join(f"{date.fromisoformat(r['date'][:10]):%d.%m.%Y}" for r in sorted(rs, key=lambda r: r["date"]))
            out.append({"level": "uyari", "title": f"\"{rs[0]['description']}\" bu dönemde {len(rs)} kez görünüyor",
                        "detail": f"Tarihler: {dates}. Biri yanlış tarihle girilmiş olabilir; İşlemler → Gelir'den düzelt.",
                        "filter": {"kind": INCOME}, "key": f"gelir_tekrar:{_fp(*sorted(r['uid'] for r in rs))}",
                        "target": _tx(month, rs[0]["description"], kind=INCOME, uids=[r["uid"] for r in rs])})

    # 10) Kategorisiz
    unc = [r for r in live if r["category"] == "Kategorisiz"]
    if unc:
        out.append({"level": "bilgi", "title": f"{len(unc)} işlem kategorisiz",
                    "detail": "Dokun: aynı işyerindekilere tek seferde kategori ver; bundan sonrası için kural oluşur.",
                    "filter": {"category": "Kategorisiz"}, "key": f"kategorisiz:{month}:{_fp(*sorted(r['uid'] for r in unc))}"})

    # 11) Ekstre bekleyen kredi kartı girişleri (eski olanlar eşleşmemiş demektir)
    waiting = [r for r in rows if r.get("awaiting_statement")]
    stale = [r for r in waiting if (today - date.fromisoformat(r["date"][:10])).days > 45]
    if stale:
        out.append({"level": "uyari", "title": f"{len(stale)} kredi kartı gideri ekstreyle eşleşmedi",
                    "detail": "45 günden eski ama ekstrede karşılığı bulunamadı. Kayda dokunup elle eşleştir "
                              "ya da ödeme şeklini düzelt; yoksa iki kez sayılabilir.",
                    "filter": {"awaiting": True}, "key": f"eslesmedi:{_fp(*sorted(r['uid'] for r in stale))}",
                    "target": _tx(month, "Ekstreyle eşleşmeyenler", awaiting=True, uids=[r["uid"] for r in stale])})

    # 12) Bekleyen sabit kalemler
    pend = [r for r in rows if r.get("pending")]
    if pend and month <= current_period(today):
        out.append({"level": "bilgi", "title": f"{len(pend)} sabit kalem onay bekliyor",
                    "detail": ", ".join(r["description"] for r in pend[:4]) + " — gerçekleştiyse İşlemler'den onayla.",
                    "filter": {"pending": True}, "key": f"onay:{month}:{_fp(*sorted(r['uid'] for r in pend))}",
                    "target": _tx(month, "Onay bekleyen sabit kalemler", pending=True)})

    order = {"kritik": 0, "uyari": 1, "bilgi": 2, "iyi": 3}
    from app import prefs

    mods = prefs.modules()  # kapatılan özelliğin uyarıları gösterilmez
    from app import book

    mem = book.member()  # ortak defter üyesine kapalı sayfaya hedef verilmez
    hidden_pages = set(book.rules(mem["id"])["hidden_pages"]) if mem else set()
    # kapatılan özelliğin uyarıları gösterilmez; "soft_link": uyarı kendi başına anlamlı, yalnız bağlantı düşer
    kept = []
    for i in out:
        soft = i.pop("soft_link", False)
        if not mods.get(prefs.LINK_MODULE.get(i.get("link") or "", ""), True):
            if not soft:
                continue
            i.pop("link", None)
            i.pop("target", None)
        _target(i, month, mods, hidden_pages)
        kept.append(i)
    return sorted(kept, key=lambda i: order[i["level"]])


def _target(i: dict, month: str, mods: dict[str, bool], hidden_pages: set[str] = frozenset()) -> None:
    """Her uyarıya dokununca gidilecek yer (target): {"screen": <ekran>, ...parametreler}. Ekranlar platformdan
    bağımsızdır (mobil kendi yoluna çevirir): islemler (süzgeç: month, kind, category, merchant, uids, fx, pending,
    awaiting; month None = tüm aylar), odemeler (type, id), kumbara (id), borclar (person), planlama, taksitler,
    oneriler (sabit kalem önerileri), abonelikler, tasarruf, zekat, ekstreler, hesaplar, fiyatlar, transferler, istatistik.
    Açıkça verilmemişse süzgeçten (işlemler) ya da bağlantıdan türer; özelliği kapalı hedef düşer, süzgeç varsa ona döner.
    Anahtarı (key) olmayan uyarıya başlıktan kararlı bir anahtar verilir (kapatılabilsin)."""
    def ok(t: dict | None) -> bool:
        page = TARGET_MODULE.get(t["screen"], t["screen"]) if t else ""
        return bool(t) and mods.get(page, True) and page not in hidden_pages

    t = i.get("target")
    if not ok(t):
        t = None
    if not t and i.get("filter"):
        f = i["filter"]
        t = _tx(month, i["title"], kind=f.get("kind"), category=f.get("category"), fx=f.get("fx"),
                pending=f.get("pending"), awaiting=f.get("awaiting"))
    if not t and i.get("link") in LINK_TARGET:
        t = {"screen": LINK_TARGET[i["link"]]}
    if t and not ok(t):
        t = None
    if t:
        i["target"] = t
    else:
        i.pop("target", None)
    i.setdefault("key", f"x:{_fp(i['title'])}")


# ---------------------------------------------------------------- "Alırsam ne olur?" simülasyonu

TAX_ON_INTEREST = 0.30  # BSMV %15 + KKDF %15 (taksitli alışveriş / kredi kartı vade farkına)


def installment_schedule(amount: float, n: int, monthly_rate: float) -> list[dict]:
    """Eşit taksit. Vade farkı varsa üstüne BSMV+KKDF eklenir (efektif oran = oran × 1,30)."""
    r = monthly_rate * (1 + TAX_ON_INTEREST)
    pay = amount / n if r == 0 else amount * r / (1 - (1 + r) ** -n)
    out, rem = [], amount
    for i in range(n):
        interest = rem * r
        principal = pay - interest
        rem -= principal
        out.append({"no": i + 1, "payment": _round(pay), "principal": _round(principal), "cost": _round(interest)})
    return out


def simulate_purchase(amount: float, installments: int, monthly_rate: float = 0.0,
                      start_month: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    start = start_month or current_period(today)
    sched = installment_schedule(amount, installments, monthly_rate)
    horizon = max(6, installments + 1)
    base = plan(horizon + 1, today=today)["months"]
    by_m = {m["month"]: m for m in base}
    months, cum_b, cum_a, extra_by_month = [], 0.0, 0.0, {}
    for i, sch in enumerate(sched):
        extra_by_month[add_months(start, i)] = sch["payment"]
    for m in base:
        add = extra_by_month.get(m["month"], 0.0)
        cum_b += m["net"]
        cum_a += m["net"] - add
        months.append({"month": m["month"], "income": m["income"], "payment": _round(add),
                       "net_before": m["net"], "net_after": _round(m["net"] - add),
                       "with_estimate_after": _round(m["net_with_estimate"] - add),
                       "cumulative_before": _round(cum_b), "cumulative_after": _round(cum_a)})
    total = sum(s["payment"] for s in sched)
    tight = [m for m in months if m["net_after"] < 0]
    est_tight = [m for m in months if m["net_after"] >= 0 and m["with_estimate_after"] < 0]
    return {
        "amount": amount, "installments": installments, "monthly_rate": monthly_rate,
        "monthly_payment": sched[0]["payment"], "total_cost": _round(total), "extra_cost": _round(total - amount),
        "schedule": [{**s, "month": add_months(start, i)} for i, s in enumerate(sched)],
        "months": [m for m in months if m["month"] in by_m],
        "negative_months": [m["month"] for m in tight],
        "tight_with_estimate": [m["month"] for m in est_tight],
        "single_payment_month": start,
    }


# ---------------------------------------------------------------- net varlık (hesaplar tamponu)

def net_worth(today: date | None = None) -> dict:
    """Bankada/nakitte ne var, kartlara ne borçlusun, alacak/borç → bugünkü net durum."""
    today = today or date.today()
    accounts = db.list_accounts()
    cash = sum(a["current"] for a in accounts if a["current"] is not None)
    statements = db.list_statements()
    cards, overdue = [], []
    # kart borcu: kart başına yalnız SON ekstrenin kalanı (db.card_debt_at ile aynı kural; Ödemeler toplamı da bu)
    for st in sorted(db.latest_card_statements(today.isoformat(), statements).values(), key=lambda x: x["period_end"]):
        left = db.statement_remaining(st, today.isoformat())
        if left:
            cards.append({"title": f"{st['bank']} {st['card_no']}", "amount": left, "due_date": st["due_date"],
                          "estimated": False, "statement_id": st["id"]})
    for p in payables(today):
        if p["type"] == "estimate":
            cards.append({"title": p["title"], "amount": p["amount"], "due_date": p["due_date"], "estimated": True})
        elif p.get("rolled_into"):  # gecikmiş eski ekstre: bilgi, toplamda yok (kalanı son ekstrede)
            overdue.append({"title": p["title"], "amount": _round(p["amount"] - (p["paid_amount"] or 0)),
                            "due_date": p["due_date"], "statement_id": p["id"], "rolled_into": p["rolled_into"]})
    card_debt = sum(c["amount"] for c in cards if not c["estimated"])
    card_est = sum(c["amount"] for c in cards if c["estimated"])
    open_try = [d for d in db.list_debts(open_only=True) if d["asset"] == "TRY"]
    receivable = sum(d["remaining"] for d in open_try if d["direction"] == "alacak")
    payable = sum(d["remaining"] for d in open_try if d["direction"] == "borc")
    stale = [a["name"] for a in accounts if a["as_of"] and a["source"] == "dokum" and (today - date.fromisoformat(a["as_of"])).days > 35]
    accounts = [a for a in accounts if a["as_of"]]  # bakiyesi bilinmeyenler unknown_banks'ta
    wallets = [{"id": w["id"], "name": w["name"], "card_type": w["card_type"], "balance": w["current"]} for w in db.list_wallets()]
    # ekstresi olan her kredi kartı (hepsi ödenmiş olsa da): son ekstre + sonraki tahmini
    latest = db.latest_card_statements(today.isoformat(), statements, include_excel=True)
    est = {p["title"]: p for p in payables(today) if p["type"] == "estimate"}
    credit_cards = []
    for card_no, st in sorted(latest.items(), key=lambda kv: kv[1]["period_end"], reverse=True):
        title = f"{st['bank']} {card_no}"
        unpaid = 0.0 if st.get("source") == "excel" else db.statement_remaining(st, today.isoformat())
        nxt = est.get(title)
        credit_cards.append({"title": title, "bank": st["bank"], "card_no": card_no, "last_period_end": st["period_end"],
                             "last_total": st["total_debt"], "last_due": st["due_date"], "paid_at": st["paid_at"],
                             "unpaid": unpaid, "next_estimate": nxt["amount"] if nxt else None,
                             "next_due": nxt["due_date"] if nxt else None, "cycle": nxt.get("cycle") if nxt else None})
    # dökümü daha önce yüklenmiş ama bakiyesi bilinmeyen bankalar (tampon özelliğinden önceki yüklemeler)
    from app.bank_import import short_bank

    have = {a["name"] for a in accounts if a["as_of"]}
    seen_banks = sorted(({short_bank(b) for b in db.imported_bank_names()} | {a["name"] for a in accounts if not a["as_of"]})
                        - have - {"Banka hesabı"})
    in_wallets = sum(w["balance"] for w in wallets)
    from app import zakat

    hold = zakat.holdings()  # altın/döviz: sadece "Ben"inkiler net duruma girer
    from app import prefs

    zakat_due = zakat.self_due() if prefs.enabled("zekat") else 0.0  # ödenmemiş geçmiş yıl zekâtı: borç
    jars = db.jar_reserved(today)
    for a in accounts:
        a["reserved"] = jars["by_account"].get(a["id"], 0.0)
    # harcanabilir TL: bankadaki/nakit para − ödenecek kart ekstresi − TL borçlar − kumbaraya ayrılan − zekât borcu.
    # altın/döviz (varlık, hemen harcanmaz), alacak (henüz elde değil) ve yemek/hediye kartı (sadece o işte harcanır)
    # dahil değil; net durumda hepsi var.
    spendable = cash - card_debt - payable - jars["total"] - zakat_due
    spendable_parts = [{"key": k, "label": lbl, "amount": _round(v), "sign": sg} for k, lbl, v, sg in (
        ("cash", "Bankadaki ve nakit para", cash, "+"), ("card_debt", "Ödenecek kart ekstresi", card_debt, "−"),
        ("payable", "TL borçların", payable, "−"), ("reserved", "Kumbaraya ayrılan", jars["total"], "−"),
        ("zakat_due", "Ödenmemiş zekât", zakat_due, "−"))]
    return {"reserved": jars["total"], "free": _round(cash - jars["total"]), "spendable": _round(spendable), "accounts": accounts, "cash": _round(cash), "cards": cards, "card_debt": _round(card_debt),
            "card_estimated": _round(card_est), "receivable": _round(receivable), "payable": _round(payable),
            "wallets": wallets, "in_wallets": _round(in_wallets), "credit_cards": credit_cards, "unknown_banks": seen_banks,
            "holdings": hold["self_total"], "family_holdings": hold["family_total"], "zakat_due": zakat_due,
            "net": _round(cash + in_wallets + hold["self_total"] - card_debt + receivable - payable - zakat_due),
            "net_after_estimates": _round(cash + in_wallets + hold["self_total"] - card_debt - card_est + receivable - payable
                                          - zakat_due),
            "card_overdue": overdue,  # ödenmemiş eski ekstreler (kalanı son ekstrede; toplamlara girmez)
            "totals": {"card_debt": _round(card_debt), "card_estimated": _round(card_est),
                       "card_overdue": _round(sum(o["amount"] for o in overdue)), "cash": _round(cash),
                       "spendable": _round(spendable), "net": _round(cash + in_wallets + hold["self_total"] - card_debt
                                                                    + receivable - payable - zakat_due)},
            "spendable_parts": spendable_parts,  # formülün kalemleri (sign '+'/'−'); istemci yalnız gösterir
            "spendable_explain": SPENDABLE_EXPLAIN,
            "stale": stale}


SPENDABLE_EXPLAIN = (f"{terms.SPENDABLE}: bankadaki ve nakit para − ödenecek kart ekstresi − TL borçların − kumbaraya ayrılan − "
                     "ödenmemiş zekât. Altın/döviz, alacakların ve yemek/hediye kartları dahil değil; eksiye düştüyse "
                     "ödenecekler eldeki parayı aşıyor demektir. Bugünün rakamıdır: Dönem sonu ise dönem bitene kadar "
                     "beklenen gelirleri (maaş gibi) de katar; bu yüzden Harcanabilir eksiyken Dönem sonu artı olabilir.")


def net_worth_at(day: date) -> dict:
    """Geçmiş bir günün net durumu (yaklaşık): o günkü banka bakiyeleri (bugünkü çapadan geriye hareketlerle),
    o gün ödenmemiş kart ekstreleri, açık borç/alacak, o günün fiyatıyla altın/döviz ("Ben"). Yemek kartları dahil değil."""
    from app import zakat

    d = day.isoformat()
    bank, unknown = zakat._bank_at(day)
    card = db.card_debt_at(d)
    try:
        px = zakat.historical_prices(day, offline=False)
    except Exception:  # noqa: BLE001 - fiyat yoksa altın/döviz sayılmaz
        px = {}
    recv = pay = 0.0
    for x in db.list_debts():
        if x["debt_date"] > d or (x["settled_at"] and x["settled_at"] <= d) or (x["written_off_at"] and x["written_off_at"] <= d):
            continue
        left = max(0.0, x["amount"] - sum(p["amount"] for p in x["payments"] if p["paid_at"] <= d))
        rate = 1.0 if x["asset"] == "TRY" else px.get(zakat.DEBT_ASSET_KEYS.get(x["asset"], ""), 0)
        if x["direction"] == "alacak":
            recv += left * rate
        else:
            pay += left * rate
    hold = 0.0
    if px:
        for p in zakat.people():
            if not p["is_self"]:
                continue
            for a in p["assets"]:
                if not a["acquired"] or a["acquired"] <= d:
                    hold += zakat._value_at(a["kind"], a["quantity"], px) or 0
    net = bank + hold - card + recv - pay
    return {"as_of": d, "cash": _round(bank), "holdings": _round(hold), "card_debt": _round(card), "receivable": _round(recv),
            "payable": _round(pay), "net": _round(net), "unknown_accounts": unknown, "prices": bool(px)}


# ---------------------------------------------------------------- dönem sonu raporu (Telegram)

_AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def _pct(now: float, before: float) -> str:
    if before <= 0:
        return ""
    p = round((now - before) / before * 100)
    return f" ({'+' if p > 0 else ''}{p}%)" if p else " (aynı)"


def duplicate_charges(today: date | None = None, days: int = 7, lookback: int = 180) -> list[dict]:
    """Abonelik kategorisinde aynı satıcıya `days` gün içinde benzer tutarlı (±%5) 2+ çekim (son `lookback` gün)."""
    today = today or date.today()
    since = (today - timedelta(days=lookback)).isoformat()
    rows = [t for t in db.card_transactions() if t["txn_type"] == PURCHASE and t["category"] in SUBSCRIPTION_CATEGORIES
            and t["txn_date"] >= since and t["amount"] >= 50]
    by: dict[str, list] = defaultdict(list)
    for t in rows:
        by[merchant_name(t["description"])].append(t)
    out = []
    for m, ts in by.items():
        ts.sort(key=lambda t: t["txn_date"])
        for i, a in enumerate(ts):
            near = [b for b in ts[i + 1:] if (date.fromisoformat(b["txn_date"]) - date.fromisoformat(a["txn_date"])).days <= days
                    and abs(b["amount"] - a["amount"]) <= a["amount"] * 0.05]
            if near:
                group = [a, *near]
                cards = sorted({f"··{(t.get('card') or t['account'])[-4:]}" for t in group})
                out.append({"level": "uyari", "title": f"{m.title()}: {len(group)} benzer çekim {days} gün içinde",
                            "detail": f"{date.fromisoformat(a['txn_date']):%d.%m.%Y} itibarıyla "
                                      + ", ".join(_tl(t['amount']) for t in group)
                                      + (f" ({', '.join(cards)} kartlarından)" if len(cards) > 1 else "")
                                      + ". Aynı üyelik iki kez alınmış ya da iki hesap açılmış olabilir; satıcıdan kontrol et, "
                                        "gerekirse iade iste.", "link": "tasarruf",
                            "key": f"dup:{_slug(m)}:{a['txn_date'][:7]}",
                            "target": _tx(a["period"] if len({t["period"] for t in group}) == 1 else None, m.title(),
                                          uids=[f"c{t['id']}" for t in group])})
                break
    return out


def year_summary(year: int) -> dict:
    """Yıllık özet (İstatistik): toplamlar, aylık ortalama, en yüksek/düşük ay, kategoriler, işyerleri, faiz,
    zekât ve sadaka, geçen yıla göre değişim. Dönemler ay dönümüne göredir."""
    cur = current_period()
    all_rows = transactions(None)
    live = [r for r in all_rows if not r.get("skipped") and not r.get("pending") and not r.get("linked")
            and r["month"] <= cur]  # gelecek dönemlere düşen taksit parçaları yıllık özete girmez
    years = sorted({int(r["month"][:4]) for r in live}, reverse=True)

    def of(y: int) -> list[dict]:
        return [r for r in live if r["month"].startswith(f"{y}-")]

    rows = of(year)
    real_months = {r["month"] for r in rows}
    t = _totals(rows)
    est = [r for r in estimates(None) if r["month"].startswith(f"{year}-") and r["month"] <= cur]
    est_m: dict[str, list] = defaultdict(list)
    for r in est:
        est_m[r["month"]].append(r)
    months = sorted(real_months | set(est_m))  # yalnız tahmini olan ay da grafikte (gerçek 0 + tahmin katmanı)
    per = [{"month": m, **{k: v for k, v in _totals([r for r in rows if r["month"] == m]).items() if k in ("income", "expense", "net")},
            **_estimate_totals(est_m.get(m, []))}
           for m in months]
    # ortalama / en yüksek / en düşük: gerçek verisi olan tamamlanmış dönemler
    done = [m for m in per if m["month"] < cur and m["month"] in real_months]
    expenses = [r for r in rows if _spends(r)]
    cats = _group_categories(expenses)
    total_exp = sum(c["amount"] for c in cats) or 1
    prev = _totals(of(year - 1)) if of(year - 1) else None
    from app import donations

    dn = next((y for y in donations.summary()["years"] if y["year"] == str(year)), {"sadaka": 0.0, "zekat": 0.0})
    subs = [x for x in subscriptions() if x["status"] == "aktif"]
    return {
        "year": year, "years": years, "months": len(real_months),
        "income": _round(t["income"]), "expense": _round(t["expense"]), "net": _round(t["net"]), "fees": _round(t["fees"]),
        # ortalama ve en yüksek/düşük ay: sadece tamamlanmış dönemler (içinde bulunulan yarım dönem yanıltır)
        "avg_expense": _round(sum(m["expense"] for m in done) / len(done)) if done else _round(t["expense"]),
        "savings_rate": round(t["net"] / t["income"] * 100) if t["income"] > 0 else None,
        "highest": max(done, key=lambda m: m["expense"]) if done else None,
        "lowest": min(done, key=lambda m: m["expense"]) if done else None,
        "per_month": per,
        "categories": [{**c, "share": round(c["amount"] / total_exp * 100)} for c in cats[:8]],
        "merchants": _group([r for r in rows if _spends(r, (*CARD_SPEND, EXPENSE))], "merchant")[:5],
        "expense_change": _change(t["expense"], prev["expense"]) if prev else None,
        "income_change": _change(t["income"], prev["income"]) if prev else None,
        "zakat": dn["zekat"], "sadaka": dn["sadaka"],
        "subscriptions_yearly": _round(sum(x["monthly"] for x in subs) * 12),
        # tahmini (ekstre bekleniyor): giderde, kategorilerde, ortalamada yok; ayrı katman
        **_estimate_totals(est), "estimated_items": [estimate_item(r) for r in sorted(est, key=lambda r: r["date"])],
    }


def _change(now: float, before: float) -> int | None:
    return round((now - before) / before * 100) if before > 0 else None


@_memo  # veri değişmedikçe (dosya damgası) ve gün dönmedikçe tekrar hesaplanmaz
def month_digest(month: str, today: date | None = None) -> dict:
    """Biten dönemin özeti (veri): Özet'teki kart ve Telegram mesajı aynı rakamları kullanır."""
    today = today or date.today()
    s, prev = summary(month, today=today), summary(add_months(month, -1), today=today)
    t, pt = s["totals"], prev["totals"]
    before = {c["name"]: c["amount"] for c in prev["by_category"]}
    top = [{"name": c["name"], "amount": c["amount"], "change": _change(c["amount"], before.get(c["name"], 0))}
           for c in s["by_category"][:5]]
    rising = [c["name"] for c in s["by_category"] if before.get(c["name"], 0) > 500 and c["amount"] > before[c["name"]] * 1.3][:3]
    upcoming = [p for p in payables(today) if p["status"] in ("bekliyor", "gecikti", "kismi", "tahmini") and p["days_left"] <= 30]
    try:
        from app import zakat

        zk = zakat.upcoming_checks(today, window=30)
    except Exception:  # noqa: BLE001 - fiyat servisine ulaşılamazsa özet yine çıksın
        zk = []
    try:
        sug = len(recurring_suggestions(today)) + len(recurring_drift(today, salary=True))
    except Exception:  # noqa: BLE001
        sug = 0
    a, b = period_bounds(month)
    return {
        # label: dönemin ADI (başladığı ay, db.period_name_month); anahtar month değişmez. start/end/span: tarih aralığı
        "month": month, "label": period_name(month), "start": a.isoformat(), "end": b.isoformat(), "span": period_span(month),
        "prev_label": period_name(add_months(month, -1)),
        "income": _round(t["income"]), "expense": _round(t["expense"]), "net": _round(t["net"]),
        # gider gerçek harcama; ekstresi beklenen kart ödemeleri ayrı (giderde değil)
        "estimated_expense": t["estimated_expense"], "estimated_count": sum(1 for i in s["estimated_items"] if i["kind"] == EXPENSE),
        "expense_change": _change(t["expense"], pt["expense"]),
        "savings_rate": round(t["net"] / t["income"] * 100) if t["income"] > 0 else None,
        "fees": _round(t["fees"]), "top": top, "rising": rising,
        "budgets_over": [{"category": b["category"], "spent": b["spent"], "limit": b["limit"]} for b in s["budgets"] if b["spent"] > b["limit"]],
        "upcoming": {"count": len(upcoming), "total": _round(sum(p["amount"] - (p["paid_amount"] or 0) for p in upcoming)),
                     "items": [{"title": p["title"], "amount": p["amount"], "due_date": p["due_date"], "estimated": p["status"] == "tahmini"}
                               for p in sorted(upcoming, key=lambda p: p["due_date"])[:5]]},
        "zakat": [{"name": z["name"], "kind": z["kind"], "date": z["date"], "days_left": z["days_left"], "amount": z["amount"]} for z in zk],
        "suggestions": sug, "net_worth": net_worth(today)["net"] if db.list_accounts() else None,
    }


def period_report(month: str) -> str:
    """Biten dönemin kısa özeti (HTML, Telegram için)."""
    import html

    s = summary(month)
    prev = summary(add_months(month, -1))
    t, pt = s["totals"], prev["totals"]
    lines = [f"📊 <b>{period_name(month)} dönemi bitti</b>" + (f" ({period_span(month)})" if db.cycle_start_day() > 1 else ""),
             f"Gelir {_tl(t['income'])} · Gider {_tl(t['expense'])}{_pct(t['expense'], pt['expense'])}",
             f"Net <b>{_tl(t['net'])}</b>" + (f" · borç hareketi {_tl(t['debt_flow'])}" if t.get("debt_flow") else "")]
    if t.get("estimated_expense"):
        n = sum(1 for i in s["estimated_items"] if i["kind"] == EXPENSE)
        lines.append(f"🧾 {n} kart ödemesinin ekstresi yok · tahmini ~{_tl(t['estimated_expense'])} (gidere katılmadı)")
    before = {c["name"]: c["amount"] for c in prev["by_category"]}
    top = s["by_category"][:5]
    if top:
        lines.append("\n<b>En çok</b>")
        lines += [f"• {html.escape(c['name'])}: {_tl(c['amount'])}{_pct(c['amount'], before.get(c['name'], 0))}" for c in top]
    rising = [c for c in s["by_category"] if before.get(c["name"], 0) > 500 and c["amount"] > before[c["name"]] * 1.3][:3]
    if rising:
        lines.append("📈 Artanlar: " + ", ".join(f"{html.escape(c['name'])} {_pct(c['amount'], before[c['name']]).strip(' ()')}"
                                                 for c in rising))
    over = [b for b in s["budgets"] if b["spent"] > b["limit"]]
    if over:
        lines.append("⚠️ Bütçe aşıldı: " + ", ".join(f"{html.escape(b['category'])} ({_tl(b['spent'])} / {_tl(b['limit'])})" for b in over))
    subs = [x for x in subscriptions() if x["status"] == "aktif"]
    if subs:
        lines.append(f"🔁 Aktif abonelikler: {len(subs)} · aylık ~{_tl(sum(x['monthly'] for x in subs))}")
    if t.get("receivable") or t.get("payable"):
        lines.append(f"🤝 Alacakta {_tl(t.get('receivable', 0))} · borçta {_tl(t.get('payable', 0))}")
    nw = net_worth()
    if nw["accounts"]:
        lines.append(f"🏦 Net durum {_tl(nw['net'])} (bankada {_tl(nw['cash'])})")
    d = month_digest(month)
    lines.append(f"⚠️ Faiz ve ücret: <b>{_tl(d['fees'])}</b>" if d["fees"] else "✅ Faiz ödemedin")
    if d["savings_rate"] is not None:
        lines.append(f"💰 Gelirinin %{d['savings_rate']}'i kaldı" if d["savings_rate"] > 0 else "💸 Gider geliri aştı")
    if d["upcoming"]["count"]:
        lines.append(f"\n<b>Önümüzdeki 30 gün</b>: {d['upcoming']['count']} ödeme · {_tl(d['upcoming']['total'])}")
        lines += [f"• {html.escape(i['title'])} {'~' if i['estimated'] else ''}{_tl(i['amount'])} · {i['due_date'][8:10]}.{i['due_date'][5:7]}"
                  for i in d["upcoming"]["items"]]
    for z in d["zakat"]:
        lines.append(f"🌙 {html.escape(z['name'])}: {'zekât günü' if z['kind'] == 'zekat' else 'ek kontrol'} "
                     f"{z['date'][8:10]}.{z['date'][5:7]} ({z['days_left']} gün)")
    if d["suggestions"]:
        lines.append(f"🔁 Planlama'da {d['suggestions']} sabit kalem önerisi bekliyor")
    return "\n".join(lines)


# ---------------------------------------------------------------- birikim hedefleri

def targets(today: date | None = None) -> list[dict]:
    """Her hedef için: biriken (başlangıç dönemi dahil, dönemlerin eldeki para artışı + başta ayrılan), kalan,
    ayda gereken ve son 3 tamamlanmış dönemin ortalamasıyla tahmini ulaşma ayı."""
    today = today or date.today()
    cur = current_period(today)
    all_rows = transactions(None)
    by_month: dict[str, list] = defaultdict(list)
    for r in all_rows:
        by_month[r["month"]].append(r)
    net_of = {m: _totals(rs)["cash_net"] for m, rs in by_month.items()}
    done = [m for m in sorted(net_of) if m < cur][-3:]
    pace = _round(sum(net_of[m] for m in done) / len(done)) if done else 0.0
    out = []
    for t in db.list_targets():
        saved = _round(t["already"] + sum(v for m, v in net_of.items() if t["start_month"] <= m <= cur))
        left = _round(max(0.0, t["amount"] - saved))
        dl = date.fromisoformat(t["deadline"])
        months_left = max(1, (dl.year - today.year) * 12 + dl.month - today.month)
        need = _round(left / months_left)
        eta = None
        if left <= 0:
            eta = cur
        elif pace > 0:
            eta = add_months(cur, int(-(-left // pace)))
        out.append({**t, "saved": saved, "left": left, "pct": min(100.0, round(saved / t["amount"] * 100, 1)) if t["amount"] else 0,
                    "months_left": months_left, "need_per_month": need, "pace": pace, "eta": eta,
                    "on_track": left <= 0 or (pace > 0 and pace >= need)})
    return out


# ---------------------------------------------------------------- dönem karşılaştırması (gün / hafta günü / hafta)

_WD = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
_FIXED_CATEGORIES = {"Kira & Aidat", "Faturalar", "Elektrik", "Su", "Doğalgaz", "İnternet & TV", "Telefon", "Vergi & Harç",
                     "Sigorta", "Banka Faiz & Ücret"}


def period_name(month: str, year: bool = True) -> str:
    """Dönemin adı (anahtar değil): dönemin başladığı ay — db.period_name_month. "Kasım 2026" / year=False: "Kasım"."""
    ym_ = db.period_name_month(month)
    y, m = map(int, ym_.split("-"))
    return f"{_AY[m - 1]} {y}" if year else _AY[m - 1]


def period_span(month: str) -> str:
    """Dönemin tarih aralığı: "17 Kas – 16 Ara" (takvim ayında "1 Kas – 30 Kas")."""
    a, b = period_bounds(month)
    return f"{a.day} {_AY[a.month - 1][:3]} – {b.day} {_AY[b.month - 1][:3]}"


def period_title(month: str, rest: str = "") -> str:
    """Bildirim/mesaj başlığı: "Kasım 2026" + rest + ay dönümü varsa aralık " (17 Kas – 16 Ara)" (takvim ayında gereksiz).
    period_title(m, " dönemi bitti") → "Kasım 2026 dönemi bitti (17 Kas – 16 Ara)"."""
    return period_name(month) + rest + (f" ({period_span(month)})" if db.cycle_start_day() > 1 else "")


def period_bounds(month: str) -> tuple[date, date]:
    """Dönemin ilk ve son günü (ay dönümüne göre): 2026-11, dönüm 20 → 20 Eki – 19 Kas."""
    start_day = db.cycle_start_day()
    y, m = map(int, month.split("-"))
    if start_day > 1:
        py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
        start = date(py, pm, min(start_day, 28))
        end = date(y, m, min(start_day, 28)) - timedelta(days=1)
    else:
        start = date(y, m, 1)
        end = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    return start, end


def compare(month: str | None = None, variable_only: bool = True, today: date | None = None) -> dict:
    """Bu dönemi aynı güne kadar geçen dönem ve son 3 dönem ortalamasıyla kıyasla. Harcama GERÇEK tarihine göre
    (kart işlemi de harcandığı gün; ekstre dönemine göre değil). variable_only: sabit giderler (kira, fatura, sabit kalem)
    hariç — alışkanlığı ölçer."""
    today = today or date.today()
    month = month or current_period(today)
    months = [add_months(month, -i) for i in range(4)]  # bu, geçen, 2 ve 3 önceki
    bounds = {m: period_bounds(m) for m in months}
    lo, hi = bounds[months[-1]][0], bounds[month][1]
    daily: dict[str, dict[int, float]] = {m: defaultdict(float) for m in months}
    for r in transactions(None):
        if r.get("deleted") or r.get("skipped") or r.get("pending") or r.get("linked"):
            continue
        if not _spends(r):
            continue
        if variable_only and (r.get("recurring_id") or r["category"] in _FIXED_CATEGORIES or r.get("installment")):
            continue
        d = date.fromisoformat(str(r["date"])[:10])
        if not lo <= d <= hi:
            continue
        for m in months:
            st, en = bounds[m]
            if st <= d <= en:
                daily[m][(d - st).days] += r["amount"]
                break
    st, en = bounds[month]
    length = (en - st).days + 1
    upto = min(length, max(0, (today - st).days + 1)) if st <= today else 0
    if today > en:
        upto = length

    def cum(m: str, n: int) -> list[float]:
        out, t = [], 0.0
        for i in range(n):
            t += daily[m].get(i, 0.0)
            out.append(_round(t))
        return out

    prev = months[1]
    prev_len = (bounds[prev][1] - bounds[prev][0]).days + 1
    lens = {m: (bounds[m][1] - bounds[m][0]).days + 1 for m in months}
    cur_c, prev_c = cum(month, upto), cum(prev, prev_len)
    avg_c = [_round(sum(cum(m, length)[i] if i < lens[m] else cum(m, lens[m])[-1] for m in months[1:]) / 3) for i in range(length)]
    same = upto - 1
    def at(series: list[float], i: int) -> float:
        return series[min(i, len(series) - 1)] if series and i >= 0 else 0.0
    cur_v, prev_v, avg_v = at(cur_c, same), at(prev_c, same), at(avg_c, same)

    def pct(a: float, b: float) -> float | None:
        return round((a - b) / b * 100, 1) if b > 0 else None

    # hafta günleri: gün başına ortalama (o güne kadar kaç pazartesi geçtiyse)
    def by_wd(m: str, n: int) -> list[float]:
        tot, cnt = [0.0] * 7, [0] * 7
        for i in range(n):
            wd = (bounds[m][0] + timedelta(days=i)).weekday()
            tot[wd] += daily[m].get(i, 0.0)
            cnt[wd] += 1
        return [tot[k] / cnt[k] if cnt[k] else 0.0 for k in range(7)]
    wd_cur = by_wd(month, upto)
    span = lambda m: min(upto, lens[m]) if upto >= 7 else lens[m]  # noqa: E731 - aynı gün aralığı (elma-elma)
    wd_avg = [sum(by_wd(m, span(m))[k] for m in months[1:]) / 3 for k in range(7)]
    weekdays = [{"day": _WD[k], "current": _round(wd_cur[k]), "avg3": _round(wd_avg[k]), "pct": pct(wd_cur[k], wd_avg[k])}
                for k in range(7)]
    # dönemin haftaları (1–7. gün = 1. hafta …)
    def by_week(m: str, n: int) -> list[float]:
        w = [0.0] * 5
        for i in range(n):
            w[min(i // 7, 4)] += daily[m].get(i, 0.0)
        return w
    wk_cur, wk_avg = by_week(month, upto), [sum(by_week(m, lens[m])[k] for m in months[1:]) / 3 for k in range(5)]
    weeks = [{"week": k + 1, "current": _round(wk_cur[k]), "avg3": _round(wk_avg[k]),
              "done": upto >= min((k + 1) * 7, length), "pct": pct(wk_cur[k], wk_avg[k])} for k in range(5)]

    notes = []
    done_wd = [w for w in weekdays if w["pct"] is not None and w["avg3"] >= 20]
    if done_wd and upto >= 7:
        lo_w = min(done_wd, key=lambda w: w["pct"])
        hi_w = max(done_wd, key=lambda w: w["pct"])
        if lo_w["pct"] <= -15:
            notes.append(f"{lo_w['day']} günleri son 3 döneme göre %{abs(round(lo_w['pct']))} daha az harcıyorsun")
        if hi_w["pct"] >= 15:
            notes.append(f"{hi_w['day']} günleri %{round(hi_w['pct'])} daha fazla")
    def daily_list(m: str, n: int) -> list[dict]:
        return [{"date": (bounds[m][0] + timedelta(days=i)).isoformat(), "amount": _round(daily[m].get(i, 0.0))} for i in range(n)]

    cur_days, prev_days = daily_list(month, upto), daily_list(prev, prev_len)
    # tahmini satırlar (ekstresiz kart ödemesi) kıyasa girmez; ödeme gününe göre ayrı toplam
    def est_in(m: str, n: int) -> float:
        a_, b_ = bounds[m][0], bounds[m][0] + timedelta(days=n - 1)
        return _round(sum(r["amount"] for r in estimates(None) if r["kind"] == EXPENSE
                          and a_ <= date.fromisoformat(str(r["date"])[:10]) <= b_))
    # olağandışı gün: son 3 dönemin günlük ortalamasının 2,5 katından fazla
    base = sum(sum(daily[m].values()) for m in months[1:]) / max(1, sum(lens[m] for m in months[1:]))
    for dd in cur_days + prev_days:
        dd["spike"] = base > 0 and dd["amount"] > base * 2.5
    top = sorted([{**x, "period": "bu"} for x in cur_days] + [{**x, "period": "gecen"} for x in prev_days],
                 key=lambda x: -x["amount"])[:4]
    return {"month": month, "start": st.isoformat(), "end": en.isoformat(), "length": length, "day": upto,
            "daily": {"current": cur_days, "prev": prev_days, "avg_per_day": _round(base)},
            "top_days": [t for t in top if t["amount"] > 0],
            "variable_only": variable_only,
            "estimated_expense": {"current": est_in(month, upto) if upto else 0.0, "prev": est_in(prev, prev_len)},
            "current": cur_c, "prev": prev_c[:length], "avg3": avg_c,
            "at_day": {"current": cur_v, "prev": prev_v, "avg3": avg_v,
                       "vs_prev": pct(cur_v, prev_v), "vs_avg3": pct(cur_v, avg_v)},
            "weekdays": weekdays, "weeks": [w for w in weeks if w["week"] <= (length + 6) // 7], "notes": notes}



def day_detail(d: str, variable_only: bool = False) -> dict:
    """Bir günün harcamaları (gerçek tarihe göre; kart işlemi harcandığı gün)."""
    rows = []
    for r in transactions(None):
        if str(r["date"])[:10] != d or r.get("deleted") or r.get("skipped") or r.get("pending") or r.get("linked"):
            continue
        if not _spends(r):
            continue
        fixed = bool(r.get("recurring_id") or r["category"] in _FIXED_CATEGORIES or r.get("installment"))
        if variable_only and fixed:
            continue
        rows.append({**r, "fixed": fixed})
    rows.sort(key=lambda r: -r["amount"])
    # o gün ödenen kart ekstreleri: bankadan para çıkar ama harcama değildir (harcamalar kendi günlerinde sayılır)
    # ödeme tarihi ödeme penceresinde olmalı: geç yüklenen eski ekstreler "yükleme günü ödendi" diye işaretlenmiş olabilir
    paid = [{"title": f"{s_['bank']} {s_['card_no']}", "amount": s_["paid_amount"], "period_end": s_["period_end"]}
            for s_ in db.list_statements() if s_["paid_at"] == d and s_["paid_amount"]
            and s_["period_end"] <= d <= (s_["due_date"] and (date.fromisoformat(s_["due_date"]) + timedelta(days=10)).isoformat() or d)]
    return {"date": d, "total": _round(sum(r["amount"] for r in rows)), "rows": rows,
            "by_category": _group(rows, "category"), "card_payments": paid}
