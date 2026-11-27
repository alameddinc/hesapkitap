"""Yaklaşan ödemeler v2 (yetenek upcoming_v2; GET /api/upcoming?days=45) ve aylık ortalama (yetenek monthly_net).

Yaklaşan: bugünden `days` gün sonrasına kadar (gecikmiş, ödenmemiş kalemler dahil) ödenecek her şey, tek liste:
- KESİN: kesilmiş ödenmemiş kart ekstresi (kalan), girilmiş ödenmemiş fatura, girilmemiş düzenli ödeme (kira, aidat…;
  Ödemelerden sonra'nın ufkundan bağımsız: pencere içindeyse görünür), vadeli TL borç / ödeme planı taksiti (kumbarada
  ayrılan kısım note'ta).
- TAHMİNİ: ekstresi kesilmemiş kart (son ödeme gününde; tutar şimdiye kadarki dönem harcaması so_far, kesim tahmini
  at_cut DEĞİL — Harcanabilir'in kesin kısmıyla aynı taban, _pending_cards), bu dönem henüz girilmemiş fatura (aynı
  kurum/abone başına son 3 faturanın ortalaması; tarih = son vade günü + 1 ay).
- EKSİK: tutarı bilinmeyen bilinen fatura / düzenli ödeme (amount null; toplama girmez, missing_count'ta sayılır).
- Maaş: income satırı; toplamlara GİRMEZ. before_salary = maaş gününden ÖNCE vadeli kesin + tahmini (+ maaş günü
  vadeli kart ekstresi). Maaş günü kuralı (yetenek ozet_sade): vadesi tam maaş günü olan fatura / düzenli ödeme / borç
  maaştan ödenir; before_salary'ye ve Harcanabilir'e girmez, salary_plan'a girer.
- salary_plan (yetenek ozet_sade): maaş gelince ödenecekler ve geriye kalan; GET /api/stats/projection: ay ay gidiş.

Çift sayım yok: gerçek fatura girilince o kurumun son kaydı o olur, tahmin bir ay ileri kayar; ayrıca gerçek bir
satırın ±BILL_MATCH_DAYS gün yakınındaki aynı grubun tahmini atılır. Düzenli ödemesi olan kurumun (açıklaması aynı)
fatura tahmini yapılmaz.
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date, timedelta

from app import db, finance, money

BILL_CATEGORIES = ("Elektrik", "Doğalgaz", "Su", "Telefon", "İnternet & TV", "Faturalar")
SHORT_BY_CATEGORY = {"Elektrik": "Elektrik", "Doğalgaz": "Doğalgaz", "Su": "Su", "Telefon": "Telefon",
                     "İnternet & TV": "İnternet", "İnternet": "İnternet"}
BILL_HISTORY = 3          # tahmin: son kaç faturanın ortalaması
BILL_STALE_DAYS = 100     # son faturası bundan eskiyse kurum artık gelmiyor sayılır (tahmin yok)
BILL_MATCH_DAYS = 12      # tahmin günü gerçek kaydın bu kadar yakınındaysa aynı fatura sayılır
CLUSTER_GAP_DAYS = 8      # aynı kurumda ayda iki fatura: ay içi günler arası en az bu boşluk varsa iki küme
CLUSTER_MONTHS = 4        # iki hat kararı: kaydı olan son kaç aya bakılır
CLUSTER_MIN_TWICE = 3     # bu aylardan en az kaçında aynı ay içinde iki ayrı fatura olmalı
KIND_ORDER = {"card": 0, "bill": 1, "recurring": 2, "debt": 3, "income": 4}
CERT_ORDER = {"kesin": 0, "tahmini": 1, "eksik": 2}

# ---------------------------------------------------------------- kurum adı sadeleştirme

# kalıp → tür (kategori bilinmiyorsa addan); sıra önemli: "Doğalgaz" "Su"dan önce, "İnternet" "Telefon"dan önce
_TYPE_PATTERNS = (
    (re.compile(r"DOGAL ?GAZ|\bGAZ\b|GAZ DAGITIM|IGDAS|BASKENTGAZ|IZMIRGAZ"), "Doğalgaz"),
    (re.compile(r"ELEKTRIK|ENERJISA|AYEDAS|BEDAS|BOGAZICI ELEKTRIK|GEDIZ"), "Elektrik"),
    (re.compile(r"\bSU\b|KANALIZASYON|ISKI|ASKI|IZSU|BUSKI"), "Su"),
    (re.compile(r"INTERNET|FIBER|SUPERONLINE|TURKNET|GENISBANT"), "İnternet"),
    (re.compile(r"TELEKOM|TELEFON|GSM|MOBIL|VODAFONE|TURKCELL"), "Telefon"),
)
# atılan ekler (normalize edilmiş, noktasız): kurum/şirket türü ve genel kelimeler
_NOISE = {"KURUM", "AS", "A.S", "A.S.", "SATIS", "HIZM", "HIZMET", "HIZMETLERI", "GNMD", "GN", "MD", "GENEL", "MUDURLUGU",
          "MUDURLUK", "DAGITIM", "IDARESI", "IDARE", "VE", "KANALIZASYON", "TIC", "SAN", "LTD", "STI", "SIRKETI",
          "PAZARLAMA", "PERAKENDE", "ANONIM", "TICARET", "SANAYI"}
# kurum adından sonra gelen tür kelimeleri: adın kendisi değil, burada kesilir ("Örnektel Fiber İnternet" → "Örnektel")
_CUT = {"FIBER", "INTERNET", "DOGALGAZ", "ELEKTRIK", "TELEKOMUNIKASYON", "ILETISIM", "ENERJI", "GAZ"}
_TR_UP = str.maketrans("ıi", "Iİ")
_TR_LO = str.maketrans("Iİ", "ıi")


def _norm_word(w: str) -> str:
    from app.textnorm import normalize

    return normalize(w).replace(".", "").replace(",", "")


def _tr_title(w: str) -> str:
    """Tamamı büyük harf kelimeyi Türkçe kurallarla baş harfli yazar ('İDARESİ' → 'İdaresi', 'ISIK' → 'Isık');
    karışık yazılmış kelime olduğu gibi kalır (kullanıcının/kurumun yazımı)."""
    letters = [c for c in w if c.isalpha()]
    if not letters or not all(c == c.upper() for c in letters):
        return w
    lo = w.translate(_TR_LO).lower()
    return lo[:1].translate(_TR_UP).upper() + lo[1:]


def clean_provider(name: str | None) -> str:
    """Ham kurum adından ekleri atılmış ad: 'Kurum Örnektel Fiber İnternet Hizm. A.Ş.' → 'Örnektel',
    'KURUM ÖRNEK SU VE KANALİZASYON İDARESİ GN.MD.' → 'Örnek Su'. Hiçbir şey kalmazsa ham ad (kırpılmış)."""
    raw = " ".join((name or "").split())
    if not raw:
        return ""
    words = [w for w in re.split(r"\s+", raw.replace("A.Ş.", " A.Ş. ").replace("Gn.Md.", " Gn.Md. ")) if w]
    kept: list[str] = []
    for w in words:
        n = _norm_word(w)
        if not n or n in _NOISE:
            continue
        if n in _CUT and kept:
            break
        kept.append(w.strip(",."))
    if not kept:
        return raw
    return " ".join(_tr_title(w) for w in kept)


def bill_type(provider: str | None, category: str | None) -> str | None:
    """Faturanın türü: kategori biliniyorsa ondan, yoksa kurum adından; bulunamazsa None."""
    if category in SHORT_BY_CATEGORY:
        return SHORT_BY_CATEGORY[category]
    from app.textnorm import normalize

    n = normalize(provider or "")
    return next((t for pat, t in _TYPE_PATTERNS if pat.search(n)), None)


def short_title(provider: str | None, category: str | None = None) -> str:
    """Kurum (fatura) başlığı: tür bulunursa tür (Elektrik, Su, Doğalgaz, İnternet, Telefon), yoksa ekleri atılmış ad.
    Kullanıcının verdiği adlar (düzenli ödeme, borç kişisi) bu işlemden GEÇMEZ (çağıran aynen kullanır)."""
    return bill_type(provider, category) or clean_provider(provider) or (provider or "")


def _key(name: str) -> str:
    from app.bank_import import _squash

    return _squash(clean_provider(name))


# ---------------------------------------------------------------- yardımcılar


def _add_month(d: date, k: int = 1) -> date:
    y, m = d.year + (d.month - 1 + k) // 12, (d.month - 1 + k) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _item(day: str, title: str, short: str, provider: str | None, amount: float | None, kind: str, certainty: str,
          note: str | None, stype: str, sid) -> dict:
    return {"date": day, "title": title, "short_title": short, "provider": provider,
            "amount": None if amount is None else finance._round(amount), "kind": kind, "certainty": certainty,
            "note": note, "source": {"type": stype, "id": sid}}


_SUB_NO = re.compile(r"(?<!\d)(\d{7,14})(?!\d)")


def _subscriber(*texts: str | None) -> str | None:
    """Abone / tesisat no (7-14 hane) metinde geçiyorsa; tarih ve tutar bu uzunlukta olmaz."""
    for t in texts:
        if t and (m := _SUB_NO.search(t.replace(" ", ""))):
            return m.group(1)
    return None


def _bill_history() -> list[dict]:
    """Geçmiş faturalar (her kayıt bir fatura; aynı gün birden çok kayıt = toplu ödeme, her biri ayrı sayılır):
    /api/bills kayıtları (gün = son ödeme), fatura tahsilatı ya da fatura kategorisindeki elle/döküm gider kayıtları (bir
    faturaya bağlı olan faturanın kendisiyle bir kez; gün = notta 'Son ödeme' varsa o, yoksa ödeme günü) ve kartla
    ödenen fatura satırları (kayda bağlı olan bir kez). Ortak defterde gizli hesap/kart süzülür."""
    from app import book

    ph = ",".join("?" * len(BILL_CATEGORIES))
    with db.connect() as c:
        bills = [dict(r) for r in c.execute("SELECT * FROM bills")]
        ents = [dict(r) for r in c.execute(
            f"""SELECT id, entry_date, description, amount, category, note, bill_provider, linked_txn_id, account_id,
                       hidden, payment, card_last4 FROM entries
                WHERE kind = 'gider' AND skipped = 0 AND (bill_provider IS NOT NULL OR category IN ({ph}))""",
            BILL_CATEGORIES)]
        txns = [dict(r) for r in c.execute(
            f"""SELECT t.id, t.txn_date, t.description, t.amount, t.category, t.hidden, s.card_no AS account
                FROM transactions t JOIN statements s ON s.id = t.statement_id
                WHERE t.is_deleted = 0 AND t.txn_type = 'harcama' AND t.amount > 0 AND t.category IN ({ph})""",
            BILL_CATEGORIES)]
    ents, txns = book.filter_entries(ents), book.filter_card_txns(txns)
    from app.bill_summary import _provider_of

    billed = {b["entry_id"] for b in bills if b.get("entry_id")}
    linked = {e["linked_txn_id"] for e in ents if e.get("linked_txn_id")}
    out = []
    for b in bills:
        out.append({"provider": b["provider"], "category": b["category"], "date": b["due_date"],
                    "amount": b["amount"], "sub": _subscriber(b["description"], b["note"]), "src": "bill", "id": b["id"],
                    "paid": b["paid_at"] is not None})
    for e in ents:
        if e["id"] in billed:
            continue
        due = re.search(r"Son ödeme (\d{2})\.(\d{2})\.(\d{4})", e["note"] or "")
        out.append({"provider": e["bill_provider"] or _provider_of(e["description"]), "category": e["category"],
                    "date": f"{due.group(3)}-{due.group(2)}-{due.group(1)}" if due else e["entry_date"],
                    "amount": e["amount"], "sub": _subscriber(e["note"], e["description"]), "src": "entry",
                    "id": e["id"], "paid": True})
    for t in txns:
        if t["id"] in linked:
            continue
        out.append({"provider": _provider_of(t["description"]), "category": t["category"], "date": t["txn_date"],
                    "amount": t["amount"], "sub": None, "src": "card", "id": t["id"], "paid": True})
    return [x for x in out if x["provider"] and x["date"]]


def _merge_keys(keys: set[str]) -> dict[str, str]:
    """'ENERJISA' ile 'ENERJISAAYESAS' aynı kurum: uzun anahtar, ön eki olan kısa anahtara bağlanır (en az 4 harf)."""
    out = {}
    for k in sorted(keys, key=len):
        out[k] = next((s for s in sorted(out.values(), key=len) if len(s) >= 4 and k.startswith(s)), k)
    return out


def _split_clusters(recs: list[dict]) -> list[list[dict]]:
    """Aynı kurumda ayda iki düzenli fatura (iki hat / iki abonelik): önce abone no, yoksa ay içi gün kümeleri (ay başı /
    ay sonu). İki küme için en az iki ayda, aralarında CLUSTER_GAP_DAYS'ten fazla gün olan iki ayrı GÜN gerekir; aynı
    gün birden çok kayıt (toplu ödeme) küme yapmaz."""
    subs = defaultdict(list)
    for r in recs:
        subs[r["sub"]].append(r)
    numbered = {k: v for k, v in subs.items() if k and len(v) >= 2}
    if len(numbered) >= 2:
        groups = list(numbered.values())
        rest = [r for k, v in subs.items() if k not in numbered for r in v]
        if rest:
            max(groups, key=len).extend(rest)
        return groups
    by_month: dict[str, set[int]] = defaultdict(set)
    for r in recs:
        by_month[r["date"][:7]].add(int(r["date"][8:10]))
    # yalnız son CLUSTER_MONTHS ayın (kaydı olan) en az CLUSTER_MIN_TWICE'ında aynı ay içinde iki ayrı fatura varsa iki
    # hat; tek abonelikte vadenin ay sonu ↔ ay başı kayması (30 Kas, 3 Ara) iki hat sayılmaz (UAT 27.11)
    recent = sorted(by_month)[-CLUSTER_MONTHS:]
    twice = sum(1 for m in recent if len(by_month[m]) >= 2 and max(by_month[m]) - min(by_month[m]) > CLUSTER_GAP_DAYS)
    if twice < CLUSTER_MIN_TWICE:
        return [recs]
    days = sorted({int(r["date"][8:10]) for r in recs})
    gap, at = max(((b - a, b) for a, b in zip(days, days[1:])), default=(0, 0))
    if gap <= CLUSTER_GAP_DAYS:
        return [recs]
    lo = [r for r in recs if int(r["date"][8:10]) < at]
    hi = [r for r in recs if int(r["date"][8:10]) >= at]
    return [g for g in (lo, hi) if g]


def bill_estimates(today: date, end: date, real: list[dict], skip_keys: set[str]) -> list[dict]:
    """Bu pencerede girilmemiş faturaların tahmini satırları. real: listedeki gerçek fatura satırları (tahmin onların
    yakınındaysa atılır); skip_keys: düzenli ödemesi olan kurumların anahtarı."""
    hist = _bill_history()
    keys = {_key(h["provider"]) for h in hist} | {_key(r["provider_raw"]) for r in real}
    keys |= skip_keys
    canon = _merge_keys({k for k in keys if k})
    groups: dict[str, list[dict]] = defaultdict(list)
    for h in hist:
        k = canon.get(_key(h["provider"]))
        if k:
            groups[k].append(h)
    skip = {canon.get(k, k) for k in skip_keys}
    real_by: dict[str, list[date]] = defaultdict(list)
    for r in real:
        real_by[canon.get(_key(r["provider_raw"]), "")].append(date.fromisoformat(r["date"]))
    out = []
    for k, recs in groups.items():
        if k in skip:
            continue
        clusters = _split_clusters(sorted(recs, key=lambda r: (r["date"], r["id"])))
        for ci, cl in enumerate(clusters):
            cl.sort(key=lambda r: (r["date"], r["src"], r["id"]))
            last = cl[-1]
            last_d = date.fromisoformat(last["date"])
            if (today - last_d).days > BILL_STALE_DAYS:
                continue
            known = [r["amount"] for r in cl if money.gt(r["amount"] or 0, 0)][-BILL_HISTORY:]
            amount = finance._round(sum(known) / len(known)) if known else None
            cat = max({r["category"] for r in cl}, key=lambda c: (sum(1 for r in cl if r["category"] == c), c or ""))
            # son kaydın adı: kurumun en güncel yazılışı
            raw = last["provider"]
            short = short_title(raw, cat)
            prov = clean_provider(raw)
            n = 1
            while (d := _add_month(last_d, n)) <= end:
                n += 1
                if d < today:
                    continue  # bu ayın faturası girilmemiş ve günü geçmiş: tahmin bugünden önceye yazılmaz
                if any(abs((d - x).days) <= BILL_MATCH_DAYS for x in real_by.get(k, [])):
                    continue
                sid = f"{k}:{ci}" if len(clusters) > 1 else k
                if amount is None:
                    out.append(_item(d.isoformat(), short, short, prov, None, "bill", "eksik", "tutar bilinmiyor",
                                     "bill_estimate", sid))
                else:
                    out.append(_item(d.isoformat(), short, short, prov, amount, "bill", "tahmini",
                                     f"tahmini · son {len(known)} faturanın ortalaması", "bill_estimate", sid))
    return out


def _next_salary(today: date) -> dict | None:
    """Sıradaki birincil maaş {date, amount, recurring_id} (finance.next_salary_date ile aynı seçim; tutar zamlı
    planned_amount)."""
    rec = db.list_recurring()
    ov = db.recurring_overrides()
    cur = finance.current_period(today)
    for per in (finance.add_months(cur, k) for k in (-1, 0, 1, 2)):
        main = finance.primary_salary(per, rec, ov)
        if not main or main["id"] in finance.salary_realized(per, rec, finance._salary_decision_entries(per, None), ov):
            continue
        d = date.fromisoformat(db.recurring_due(main, per))
        late = d < today
        if late:
            if not finance.salary_awaited(d.isoformat(), today):
                continue
            d = today
        # late: günü geçmiş, hâlâ beklenen maaş bugüne çekildi → maaş günü kuralı uygulanmaz (finance.salary_day_rule)
        return {"date": d.isoformat(), "amount": finance._round(db.planned_amount(main, per, ov)),
                "recurring_id": main["id"], "period": per, "late": late}
    return None


def _salary_series(sal: dict, n: int) -> list[dict]:
    """Sıradaki maaştan (sal = _next_salary) başlayarak art arda n birincil maaş [{date, amount, period}]: gün
    db.recurring_due, tutar db.planned_amount (aya özel tutar > brütten net > şablon; ay ay değişir). Maaş kalemi biterse
    liste kısa kalır."""
    rec, ov = db.list_recurring(), db.recurring_overrides()
    out = [{"date": sal["date"], "amount": sal["amount"], "period": sal["period"]}]
    for k in range(1, n + 3):
        if len(out) >= n:
            break
        per = finance.add_months(sal["period"], k)
        main = finance.primary_salary(per, rec, ov)
        if not main:
            break
        d = db.recurring_due(main, per)
        if d <= out[-1]["date"]:  # gecikmiş maaş bugüne çekildiyse sıradaki oluşum onunla çakışmasın
            continue
        out.append({"date": d, "amount": finance._round(db.planned_amount(main, per, ov)), "period": per})
    return out


def _before_salary(day: str, kind: str, sal: dict) -> bool:
    """Satır maaştan ÖNCE mi ödenir: vadesi maaş gününden önce; tam maaş günü vadeli olanlardan yalnız kart ekstresi
    (Harcanabilir onu düşer). Maaş günü vadeli fatura / düzenli ödeme / borç maaştan ödenir (maaş günü kuralı);
    gecikmiş maaş bugüne çekildiyse (sal.late) kural yok: bugün vadeli her şey maaştan önce sayılır."""
    return day < sal["date"] or (day == sal["date"] and (kind == "card" or bool(sal.get("late"))))


# ---------------------------------------------------------------- ana hesap


def upcoming(today: date | None = None, days: int = 45, with_next: bool = True) -> dict:
    today = today or date.today()
    end = today + timedelta(days=days)
    e_iso, t_iso = end.isoformat(), today.isoformat()
    items: list[dict] = []
    real_bills: list[dict] = []
    pay = finance.payables(today)
    for p in pay:
        if p["status"] == "odendi" or not p["due_date"] or p["due_date"] > e_iso:
            continue
        late = p["status"] == "gecikti"
        left = finance._round((p["amount"] or 0) - (p["paid_amount"] or 0)) if p["status"] == "kismi" else p["amount"]
        if p["type"] == "statement" and p.get("in_total"):
            items.append(_item(p["due_date"], p["title"], p["title"], None, left, "card", "kesin",
                               "gecikti" if late else "ekstre kesildi", "statement", p["id"]))
        elif p["type"] == "bill":
            raw = p["title"]
            cat = p.get("category")
            short, prov = short_title(raw, cat), clean_provider(raw)
            real_bills.append({"provider_raw": raw, "date": p["due_date"]})
            if not money.gt(left or 0, 0):
                items.append(_item(p["due_date"], short, short, prov, None, "bill", "eksik", "tutar gir", "bill", p["id"]))
            else:
                items.append(_item(p["due_date"], short, short, prov, left, "bill", "kesin",
                                   "gecikti" if late else "fatura", "bill", p["id"]))
    # TL borç: payables'taki vadeli borç / sıradaki taksit; kumbarada ayrılan kısım note'ta
    jars = db.jar_reserved(today).get("by_debt", {})
    for p in pay:
        if p["type"] != "debt" or p["status"] == "odendi" or not p["due_date"] or p["due_date"] > e_iso:
            continue
        left = finance._round((p["amount"] or 0) - (p["paid_amount"] or 0))
        if not money.gt(left, 0):
            continue
        note = "gecikti" if p["status"] == "gecikti" else "borç"
        if money.gt(jars.get(p["id"], 0.0), 0):
            note = f"kumbarada ayrıldı ({money.tl(min(jars[p['id']], left))})"
        items.append(_item(p["due_date"], p["title"], p["title"], None, left, "debt", "kesin", note, "debt", p["id"]))
    # düzenli ödemeler: pencerenin kapsadığı her dönemin girilmemiş giderleri (ufuktan bağımsız) + önceki dönemden
    # vadesi TRANSFER_LATE_DAYS içinde geçmiş olan (_due_this_period ile aynı gecikme kuralı)
    recurring = db.list_recurring()
    cur = finance.current_period(today)
    pers = [cur]
    while finance.period_bounds(pers[-1])[1] < end:
        pers.append(finance.add_months(pers[-1], 1))
    late_from = (today - timedelta(days=db.TRANSFER_LATE_DAYS)).isoformat()
    prev = finance.add_months(cur, -1)
    skip_keys: set[str] = set()
    seen_uid: set[str] = set()
    for per in [prev, *pers]:
        for r in finance.pending_recurring(per, recurring, db.list_entries(per), today):
            if r["kind"] != finance.EXPENSE or not r.get("pending") or r["uid"] in seen_uid:
                continue
            if r["date"] > e_iso or (per == prev and not (late_from <= r["date"] <= t_iso)):
                continue
            seen_uid.add(r["uid"])
            skip_keys.add(_key(r["description"]))
            late = r["date"] < t_iso
            amt = r["amount"]
            if not money.gt(amt or 0, 0):
                items.append(_item(r["date"], r["description"], r["description"], None, None, "recurring", "eksik",
                                   "tutar gir", "recurring", r["recurring_id"]))
            else:
                items.append(_item(r["date"], r["description"], r["description"], None, amt, "recurring", "kesin",
                                   "gecikti" if late else "düzenli ödeme", "recurring", r["recurring_id"]))
    # ekstresi kesilmemiş kartlar: kesin kısım (so_far), son ödeme gününde
    for x in finance._pending_cards(today):
        if not x["due_date"] or x["due_date"] > e_iso:
            continue
        items.append(_item(x["due_date"], x["title"], x["title"], None, x["amount"], "card", "tahmini",
                           "yeni dönem" if x.get("next_cycle") else "ekstre bekleniyor", "card_cycle", x["account"]))
    items += bill_estimates(today, end, real_bills, {k for k in skip_keys if k})
    sal = _next_salary(today)
    salary = None
    if sal and sal["date"] <= e_iso:
        salary = {"date": sal["date"], "amount": sal["amount"]}
        items.append(_item(sal["date"], "Maaş", "Maaş", None, sal["amount"], "income", "kesin", "toplama girmez",
                           "recurring", sal["recurring_id"]))
    items.sort(key=lambda x: (x["date"], KIND_ORDER[x["kind"]], CERT_ORDER[x["certainty"]], x["title"]))
    pay_items = [x for x in items if x["kind"] != "income"]

    def tot(xs: list[dict], cert: str | None = None) -> float:
        return finance._round(sum(x["amount"] for x in xs if x["amount"] is not None
                                  and (cert is None or x["certainty"] == cert)))

    before = None
    if salary:
        pre = [x for x in pay_items if _before_salary(x["date"], x["kind"], sal)]
        before = {"total": tot(pre), "estimate": tot(pre, "tahmini"),
                  "missing_count": sum(1 for x in pre if x["certainty"] == "eksik")}
    out = {"items": items, "total": tot(pay_items), "total_certain": tot(pay_items, "kesin"),
           "total_estimate": tot(pay_items, "tahmini"), "salary": salary, "before_salary": before,
           "missing_count": sum(1 for x in pay_items if x["certainty"] == "eksik"),
           "from": t_iso, "until": e_iso}
    if with_next:
        ctx = _period_ctx(today, sal, out) if sal else None
        out["next_period"] = next_period(today, sal, ctx)
        out["salary_plan"] = salary_plan(today, sal, ctx)
    return out


# ---------------------------------------------------------------- sonraki dönem (maaştan maaşa)

FIXED_CATEGORIES = set(BILL_CATEGORIES) | {"Kira", "Kira & Aidat", "Aidat"}
NEXT_BASIS = 3  # değişken harcama ortalaması: son kaç tamamlanmış dönem


def _fixed_row(r: dict) -> bool:
    """Gider satırı düzenli/fatura/kira kısmı mı (Yaklaşan'da 'fixed' olarak zaten sayılan tür): düzenli ödemeye bağlı,
    fatura tahsilatı ya da fatura/kira kategorisi. Değişken ortalamadan çıkarılır (çift sayım yok)."""
    return bool(r.get("recurring_id") or r.get("bill_provider")) or (r.get("category") in FIXED_CATEGORIES)


def variable_average(today: date, months: int = NEXT_BASIS) -> tuple[float, int]:
    """Son `months` tamamlanmış dönemin değişken harcama ortalaması: Özet'in gideri (finance._totals kuralı: kart bir
    kez, ekstre ödemesi ve kendi hesapları arası aktarım yok) − düzenli/fatura/kira satırları. Kaydı olmayan baştaki
    dönemler atlanır. Dönüş: (ortalama, dönem sayısı)."""
    cur = finance.current_period(today)
    wanted = [finance.add_months(cur, -k) for k in range(months, 0, -1)]
    by: dict[str, list] = defaultdict(list)
    for r in finance.transactions(None, today=today):
        if r["month"] in wanted:
            by[r["month"]].append(r)
    first = next((m for m in wanted if by.get(m)), None)
    if first is None:
        return 0.0, 0
    vals = []
    for m in (m for m in wanted if m >= first):
        rows = by.get(m, [])
        total = finance._totals(rows)["expense"]
        fixed = finance._totals([r for r in rows if _fixed_row(r)])["expense"]
        vals.append(total - fixed)
    return finance._round(sum(vals) / len(vals)), len(vals)


def _debt_steps(today: date) -> list[dict]:
    """Vadeli TL borçların ödenmemiş adımları [{date, amount, debt_id}] (tarih sırasıyla), kumbaranın karşıladığı kısım
    düşülmüş (kumbara en erken adımdan başlar; para zaten ayrılmış, Harcanabilir'den düşülmüş). Ödeme planlı borç: her
    taksit kendi gününde (finance._debt_steps_after; Harcanabilir'in borç hesabıyla aynı satırlar), tek vadeli borç:
    kalanı vadesinde. Vadesiz borç yok (Harcanabilir'de). Ortak defterde Borç sayfası gizliyse boş (payables kuralı)."""
    from app import book, debt_schedule

    rules = book.active()
    if rules and "borclar" in rules["hidden_pages"]:
        return []
    jars = db.jar_reserved(today).get("by_debt", {})
    out = []
    for d in db.list_debts(open_only=True, today=today):
        if d["asset"] != "TRY" or d["direction"] != "borc" or not money.gt(d["remaining"] or 0, 0):
            continue
        steps = finance._debt_steps_after(d, "")  # ufuk yok: ödenmemiş taksitlerin hepsi
        if not steps and d.get("due_date") and not debt_schedule.is_scheduled(d):
            steps = [{"date": d["due_date"], "amount": d["remaining"]}]
        jar = max(0.0, jars.get(d["id"], 0.0))
        for st in steps:
            take = min(jar, st["amount"])
            jar -= take
            if money.gt(st["amount"] - take, 0):
                out.append({"date": st["date"], "amount": finance._round(st["amount"] - take), "debt_id": d["id"]})
    return sorted(out, key=lambda x: (x["date"], x["debt_id"]))


def _period_ctx(today: date, sal: dict, up: dict | None = None, series: list[dict] | None = None) -> dict:
    """next_period, salary_plan ve projection'ın ORTAK tabanı (üçü aynı satırları toplar; rakamlar birbirini tutar).
    rows: maaş gününden (dahil) itibaren maaştan ödenecek kart DIŞI satırlar [{date, key recurring|bills|debts,
    amount|None (eksik), estimate, reserved}]:
    - düzenli ödeme ve fatura: Yaklaşan'ın satırları (kesin + tahmini; aynı çift sayım kuralları),
    - borç: _debt_steps (kumbara-fonlu kısım hariç; planlı borcun her taksidi kendi gününde).
    reserved: satır Harcanabilir'den ZATEN düşülmüş (maaştan sonra ama ufuk içinde vadeli kesin ödeme; maaş günü vadeli
    olan düşülmez — maaş günü kuralı): toplamlara girmez, böylece her ödeme ya Harcanabilir'de ya burada, bir kez.
    pre_est: maaştan önceki tahmini faturalar (Harcanabilir onları düşmez). end: sonraki maaştan önceki gün."""
    series = series or _salary_series(sal, 2)
    s_iso = sal["date"]
    s_day = date.fromisoformat(s_iso)
    end = (date.fromisoformat(series[1]["date"]) if len(series) > 1 else _add_month(s_day, 1)) - timedelta(days=1)
    if up is None or up["until"] < end.isoformat():
        up = upcoming(today, max(1, (end - today).days), with_next=False)
    nw = finance.net_worth(today)
    h_end = (nw.get("spendable_pre_salary") or {}).get("until") or nw["spendable_horizon"]
    done_bills = {x["id"] for x in nw.get("bills_items", [])}
    done_rec = {(x["recurring_id"], x["date"]) for x in nw.get("recurring_items", [])}
    rows = []
    for x in up["items"]:
        if x["kind"] not in ("bill", "recurring") or _before_salary(x["date"], x["kind"], sal):
            continue
        src = x["source"]
        reserved = (src["type"] == "bill" and src["id"] in done_bills) or \
                   (src["type"] == "recurring" and (src["id"], x["date"]) in done_rec)
        rows.append({"date": x["date"], "key": "bills" if x["kind"] == "bill" else "recurring", "amount": x["amount"],
                     "estimate": x["certainty"] == "tahmini", "reserved": reserved})
    for st in _debt_steps(today):
        if not _before_salary(st["date"], "debt", sal):
            rows.append({"date": st["date"], "key": "debts", "amount": st["amount"], "estimate": False,
                         "reserved": st["date"] <= h_end and st["date"] != s_iso})
    pre_est = finance._round(sum(x["amount"] for x in up["items"] if x["amount"] is not None
                                 and x["source"]["type"] == "bill_estimate"
                                 and _before_salary(x["date"], x["kind"], sal)))
    return {"sal": sal, "series": series, "end": end, "up": up, "nw": nw, "rows": rows, "pre_est": pre_est}


def _window(ctx: dict, a: str, b: str) -> list[dict]:
    """a–b (dahil) arası, tutarı bilinen ve Harcanabilir'de düşülmemiş maaştan ödenecek satırlar."""
    return [r for r in ctx["rows"] if a <= r["date"] <= b and r["amount"] is not None and not r["reserved"]]


def next_period(today: date, sal: dict | None = None, ctx: dict | None = None) -> dict | None:
    """Maaştan sonraki dönem (maaş günü → bir sonraki maaştan önceki gün), tahakkuk bakışı, kart TEK kez:
    result = cards_close_today − pre_salary_estimate + salary − fixed_noncard − variable_avg (eksi olabilir).
    - cards_close_today: Harcanabilir − kartlarda biriken (bugüne kadarki kart harcamasının hepsi burada düşülür).
    - pre_salary_estimate: maaştan önceki tahmini faturalar (Harcanabilir onları düşmez; başka yerde de yok).
    - fixed_noncard: maaş günü (DAHİL; maaş günü kuralı) → end_date arası kart DIŞI kesin + tahmini ödemeler (kira,
      fatura, borç; _period_ctx). Kumbaradan ödenen borç kısmı girmez (para zaten kumbarada ayrılmış, Harcanabilir'den
      düşülmüş); Harcanabilir'den zaten düşülmüş ödeme de girmez. Her ödeme ya Harcanabilir'de ya burada: bir kez.
    - variable_avg: son 3 tamamlanmış dönemin değişken harcaması (kart dahil): gelecek dönemin kart harcaması bu; kart
      ekstresi satırları fixed_noncard'a girmediği için bir ay fazla kart sayılmaz.
    Bilgi alanları: start_cash (Harcanabilir − pre_salary_estimate), fixed (fixed_noncard + maaştan sonra vadeli kart
    satırları; eski alan, sonuçta kullanılmaz). Maaş tanımlı değilse None."""
    sal = sal if sal is not None else _next_salary(today)
    if not sal:
        return None
    ctx = ctx or _period_ctx(today, sal)
    end, nw, pre_est = ctx["end"], ctx["nw"], ctx["pre_est"]
    fixed_nc = finance._round(sum(r["amount"] for r in _window(ctx, sal["date"], end.isoformat())))
    cards = sum(x["amount"] for x in ctx["up"]["items"]
                if x["kind"] == "card" and x["amount"] is not None and sal["date"] < x["date"] <= end.isoformat())
    start = finance._round(nw["spendable"] - pre_est)
    cct = nw["cards_close_today"]
    var, n = variable_average(today)
    return {"end_date": end.isoformat(), "start_cash": start, "cards_close_today": cct,
            "pre_salary_estimate": pre_est, "salary": sal["amount"], "fixed": finance._round(fixed_nc + cards),
            "fixed_noncard": fixed_nc,
            "variable_avg": var, "result": finance._round(cct - pre_est + sal["amount"] - fixed_nc - var),
            "basis_months": n}


# ---------------------------------------------------------------- sade Özet (yetenek ozet_sade)

PLAN_LABELS = (("recurring", "Kira ve düzenli ödemeler"), ("bills", "Faturalar"), ("debts", "Borçlar"))
DAYS_PER_MONTH = 30.4
MONTHS_SHORT = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")


def salary_plan(today: date, sal: dict | None = None, ctx: dict | None = None) -> dict | None:
    """Maaş gelince neler ödenecek, geriye ne kalır (yetenek ozet_sade; /api/upcoming salary_plan). Maaş yoksa None.
    items (tutarı 0 olan kalem yok; her biri {key, label, amount, estimate, count}):
    - cards: net_worth.cards_accumulated.total (kesilmiş + bekleyen; Harcanabilir'den DÜŞÜLMEYEN kartlar). Kart
      satırları yalnız burada: öbür kalemlerde kart yok (çift sayım yok).
    - recurring / bills / debts: maaş günü (DAHİL) → end_date arası kart dışı kesin + tahmini ödemeler (_period_ctx;
      next_period.fixed_noncard ile aynı satırlar: total − cards = fixed_noncard). estimate: kalemde tahmini satır var.
    installments_in_cards: bu dönemin taksit toplamı (finance.installments_due cari dönem total; kartların İÇİNDE,
    eklenmez). remaining = salary.amount − total; days: maaş günü → end_date (ikisi dahil); remaining_per_day.
    usual_monthly = variable_average (her zamanki değişken harcama), usual_per_day = usual_monthly / 30,4.
    already_reserved: pencerede olup Harcanabilir'den zaten düşülmüş kesin ödemeler (bilgi; total'e girmez).
    missing_count: pencerede tutarı bilinmeyen satır sayısı (total'e girmez)."""
    sal = sal if sal is not None else _next_salary(today)
    if not sal:
        return None
    ctx = ctx or _period_ctx(today, sal)
    s_iso, e_iso = sal["date"], ctx["end"].isoformat()
    acc = ctx["nw"]["cards_accumulated"]
    items = []
    if money.gt(acc["total"], 0):
        items.append({"key": "cards", "label": "Kartlar", "amount": acc["total"],
                      "estimate": any(x.get("state") != "kesildi" for x in acc["items"]), "count": len(acc["items"])})
    win = _window(ctx, s_iso, e_iso)
    for key, label in PLAN_LABELS:
        rows = [r for r in win if r["key"] == key]
        amount = finance._round(sum(r["amount"] for r in rows))
        if money.gt(amount, 0):
            items.append({"key": key, "label": label, "amount": amount, "estimate": any(r["estimate"] for r in rows),
                          "count": len(rows)})
    in_win = [r for r in ctx["rows"] if s_iso <= r["date"] <= e_iso]
    total = finance._round(sum(x["amount"] for x in items))
    remaining = finance._round(sal["amount"] - total)
    days = (ctx["end"] - date.fromisoformat(s_iso)).days + 1
    var, n = variable_average(today)
    return {"salary": {"date": s_iso, "amount": sal["amount"]}, "end_date": e_iso, "items": items,
            "installments_in_cards": finance._round(
                finance.installments_due(finance.current_period(today), today)["total"]),
            "total": total, "remaining": remaining, "days": days,
            "remaining_per_day": finance._round(remaining / days) if days > 0 else None,
            "usual_per_day": finance._round(var / DAYS_PER_MONTH), "usual_monthly": var, "basis_months": n,
            "already_reserved": finance._round(sum(r["amount"] for r in in_win
                                                   if r["reserved"] and r["amount"] is not None)),
            "missing_count": sum(1 for r in in_win if r["amount"] is None)}


def projection(today: date | None = None, months: int = 13) -> dict:
    """Böyle giderse ay ay nereye varırım (yetenek ozet_sade; GET /api/stats/projection?months=13), maaştan maaşa:
    start = cards_close_today − pre_salary_estimate (next_period ile aynı taban);
    periods[i] = {label (maaş ayının kısa adı), year, salary_date, end_date, salary, fixed, end_balance};
    end_balance = önceki (ilkinde start) + salary − fixed − variable_avg. İstemci senaryo için variable'ı değiştirip
    zinciri yeniden toplar: salary ve fixed dönem başına açık.
    - salary: o dönemin birincil maaşı (_salary_series: db.planned_amount; brütten net ay ay değişir).
    - fixed: maaş günü (dahil) → sonraki maaştan önceki gün arası kart dışı ödemeler (_period_ctx satırları): düzenli
      ödemeler, fatura (girilmiş + tahmin: son 3 faturanın ortalaması, hat başına ayda bir; iki hat kuralı), vadeli
      borç (kumbara-fonlu hariç). İlk dönem = next_period.fixed_noncard = salary_plan'ın kart dışı toplamı
      (periods[0].end_balance = next_period.result). Kart harcaması variable_avg'in içinde (ayrıca eklenmez).
    Maaş yoksa periods boş (start = cards_close_today). Maaş kalemi ileride bitiyorsa liste orada biter."""
    today = today or date.today()
    var, n = variable_average(today)
    sal = _next_salary(today)
    if not sal:
        return {"start": finance.net_worth(today)["cards_close_today"], "variable_avg": var, "basis_months": n,
                "periods": []}
    series = _salary_series(sal, months + 1)
    k = min(months, len(series))
    starts = [date.fromisoformat(x["date"]) for x in series]
    ends = [(starts[i + 1] if i + 1 < len(starts) else _add_month(starts[i], 1)) - timedelta(days=1) for i in range(k)]
    up = upcoming(today, max(1, (ends[-1] - today).days), with_next=False)
    ctx = _period_ctx(today, sal, up, series)
    start = bal = finance._round(ctx["nw"]["cards_close_today"] - ctx["pre_est"])
    periods = []
    for i in range(k):
        fixed = finance._round(sum(r["amount"] for r in _window(ctx, series[i]["date"], ends[i].isoformat())))
        bal = finance._round(bal + series[i]["amount"] - fixed - var)
        periods.append({"label": MONTHS_SHORT[starts[i].month - 1], "year": starts[i].year,
                        "salary_date": series[i]["date"], "end_date": ends[i].isoformat(),
                        "salary": series[i]["amount"], "fixed": fixed, "end_balance": bal})
    return {"start": start, "variable_avg": var, "basis_months": n, "periods": periods}


# ---------------------------------------------------------------- aylık ortalama


def monthly_net(months: int = 6, today: date | None = None) -> dict:
    """Tamamlanmış son `months` dönem: {period, name, income, expense, net} ve ortalamalar. Gelir/gider Özet'in
    kuralıyla (finance._totals: kart harcaması bir kez — ekstre satırı, eşleşen elle kayıt sayılmaz; ekstre ödemesi ve
    kendi hesapları arası aktarım gider değil). Hiç kaydı olmayan baştaki dönemler atlanır; ortalama listelenenlerle."""
    today = today or date.today()
    cur = finance.current_period(today)
    wanted = [finance.add_months(cur, -k) for k in range(months, 0, -1)]
    by: dict[str, list] = defaultdict(list)
    for r in finance.transactions(None, today=today):
        if r["month"] in wanted:
            by[r["month"]].append(r)
    first = next((m for m in wanted if by.get(m)), None)
    rows = []
    for m in wanted:
        if first is None or m < first:
            continue
        t = finance._totals(by.get(m, []))
        rows.append({"period": m, "name": finance.period_name(m), "income": t["income"], "expense": t["expense"],
                     "net": t["net"]})
    n = len(rows)
    avg = (lambda k: finance._round(sum(r[k] for r in rows) / n) if n else None)
    return {"periods": rows, "count": n, "avg_income": avg("income"), "avg_expense": avg("expense"),
            "avg_net": avg("net")}


def _today() -> date:
    """Uçların bugünü (testler sabitler)."""
    return date.today()
