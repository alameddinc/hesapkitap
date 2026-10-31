"""Faturalar özeti: her ay gelen ama tutarı değişen giderler (elektrik, doğalgaz, su, telefon, internet).

Kaynak: banka dökümündeki fatura tahsilatları (entries.bill_provider, app/bank_import.bill_info), bu kategorilerdeki
elle/fiş kayıtları ve kartla ödenen faturalar (ekstre satırları; kayda bağlıysa bir kez sayılır). /api/bills (ödenecek
fatura listesi, belge) ayrı kalır: bu uç geçmişin özetidir.

Ay = faturanın AİT OLDUĞU ay (bill_month), ödendiği gün değil: biriken üç fatura aynı gün ödenince üç ayrı aya düşer.
Öncelik: (i) faturadaki dönem ('Dönem 01.09–30.09': dönemin bittiği ay), (ii) son ödeme tarihinin ayı (dökümden
'Son ödeme dd.mm.yyyy' ya da /api/bills kaydının due_date'i), (iii) ödeme tarihi. Kalemde paid_date ve bill_month ayrı,
month_source: donem | son_odeme | odeme.
"""
import re
from datetime import date

from app import db

CATEGORIES = ("Elektrik", "Doğalgaz", "Su", "Telefon", "İnternet & TV", "Faturalar")


def _months_back(end: str, n: int) -> list[str]:
    y, m = int(end[:4]), int(end[5:7])
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def _shift(month: str, k: int) -> str:
    y, m = int(month[:4]), int(month[5:7]) + k
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


_DONEM = re.compile(r"D[öo]nem\w*\s*:?\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s*[-–—]\s*(\d{1,2})[./](\d{1,2})"
                    r"(?:[./](\d{2,4}))?", re.I)
_SON = re.compile(r"Son [öo]deme\w*\s*:?\s*(\d{2})[./](\d{2})[./](\d{4})", re.I)


def bill_month(paid: str, texts: tuple[str | None, ...] = (), due: str | None = None) -> tuple[str, str]:
    """(YYYY-MM, kaynak). texts: not/açıklama (dönem, son ödeme); due: fatura kaydının son ödeme günü (ISO)."""
    for t in texts:
        if t and (m := _DONEM.search(t)):
            d1, mo1, y1, d2, mo2, y2 = m.groups()
            y = int(y2 or y1 or paid[:4])
            y = y + 2000 if y < 100 else y
            if not (y2 or y1) and int(mo2) > int(paid[5:7]):  # yıl yazmıyor, ödemeden sonraki ay olamaz: geçen yıl
                y -= 1
            if 1 <= int(mo2) <= 12:
                return f"{y:04d}-{int(mo2):02d}", "donem"
    for t in texts:
        if t and (m := _SON.search(t)):
            return f"{m.group(3)}-{m.group(2)}", "son_odeme"
    if due:
        return due[:7], "son_odeme"
    return paid[:7], "odeme"


def _provider_of(desc: str) -> str:
    """Kayıt açıklamasından kurum: 'Vodafone · Telefon faturası' → 'Vodafone'; kart satırı → işyeri adı."""
    from app.bank_import import bill_info
    from app.textnorm import merchant_name

    if " · " in desc:
        return desc.split(" · ")[0].strip()
    b = bill_info(desc)
    if b:
        return b["provider"]
    name = merchant_name(desc) or desc
    return name.title() if name.isupper() else name


def _change(series: dict[str, float], month: str, other: str) -> dict | None:
    if other not in series or not series[other]:
        return None
    cur, prev = series.get(month, 0.0), series[other]
    return {"month": other, "amount": round(prev, 2), "diff": round(cur - prev, 2), "pct": round((cur - prev) / prev * 100, 1)}


def _summarise(key: str, items: list[dict], window: list[str]) -> dict:
    by_month: dict[str, dict] = {}
    for it in items:
        b = by_month.setdefault(it["month"], {"month": it["month"], "amount": 0.0, "count": 0, "items": []})
        b["amount"] = round(b["amount"] + it["amount"], 2)
        b["count"] += 1
        b["items"].append({k: it[k] for k in ("id", "source", "date", "amount", "description", "invoice", "due",
                                              "paid_date", "bill_month", "month_source")})
    first = min(by_month)
    months = [by_month.get(m) or {"month": m, "amount": 0.0, "count": 0, "items": []} for m in window if m >= first]
    paid = [m for m in months if m["count"]]
    last = paid[-1] if paid else None
    series = {m["month"]: m["amount"] for m in months}
    top = max(paid, key=lambda m: m["amount"]) if paid else None
    last12 = {_shift(last["month"], -i) for i in range(12)} if last else set()
    paid12 = [m for m in paid if m["month"] in last12]
    return {
        # son ödemeli aydan geriye 12 ay içinde ödeme olan ayların ortalaması (web ve mobil aynı rakamı göstersin)
        "average_12": round(sum(m["amount"] for m in paid12) / len(paid12), 2) if paid12 else 0.0,
        "name": key, "months": months,
        "total": round(sum(m["amount"] for m in months), 2),
        "average": round(sum(m["amount"] for m in paid) / len(paid), 2) if paid else 0.0,
        "paid_months": len(paid),
        "last": {"month": last["month"], "amount": last["amount"]} if last else None,
        "max": {"month": top["month"], "amount": top["amount"]} if top else None,
        "vs_prev_month": _change(series, last["month"], _shift(last["month"], -1)) if last else None,
        "vs_last_year": _change(series, last["month"], _shift(last["month"], -12)) if last else None,
    }


def summary(months: int = 24, until: str | None = None) -> dict:
    """{providers: [...], categories: [...], window: {start, end}}. Her öğe: months (ay → amount, count, items —
    aynı ay birden çok ödeme toplanır, items'ta ayrı), total, average (ödeme yapılan aylar), last, max,
    vs_prev_month / vs_last_year ({month, amount, diff, pct}; o ay ödeme yoksa null)."""
    end = until or date.today().isoformat()[:7]
    window = _months_back(end, max(1, min(months, 60)))
    # ödeme penceresi biraz geniş: ayın faturası sonraki aylarda ödenmiş olabilir (bill_month penceredeyse sayılır)
    start_d, end_d = f"{window[0]}-01", f"{_shift(window[-1], 3)}-31"
    inwin = set(window)
    ph = ",".join("?" * len(CATEGORIES))
    with db.connect() as c:
        ents = [dict(r) for r in c.execute(
            f"""SELECT id, entry_date, description, amount, category, note, bill_provider, linked_txn_id, account_id,
                       hidden, payment, card_last4 FROM entries
                WHERE kind = 'gider' AND skipped = 0 AND entry_date BETWEEN ? AND ?
                AND (bill_provider IS NOT NULL OR category IN ({ph}))""", (start_d, end_d, *CATEGORIES))]
        linked = {e["linked_txn_id"] for e in ents if e["linked_txn_id"]}
        due_of = {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT entry_id, due_date, description FROM bills WHERE entry_id IS NOT NULL")}  # /api/bills ile ödenen
        txns = [dict(r) for r in c.execute(
            f"""SELECT t.id, t.txn_date, t.description, t.amount, t.category, t.txn_type, t.hidden, s.card_no AS account
                FROM transactions t JOIN statements s ON s.id = t.statement_id WHERE t.is_deleted = 0 AND t.txn_type = 'harcama' AND t.amount > 0 AND t.txn_date BETWEEN ? AND ?
                AND t.category IN ({ph})""", (start_d, end_d, *CATEGORIES))]
    from app import book

    ents, txns = book.filter_entries(ents), book.filter_card_txns(txns)  # ortak defterde gizli hesap/kart görünmez
    items = []
    for e in ents:
        note = e["note"] or ""
        inv = re.search(r"Fatura no (\S+)", note)
        due = re.search(r"Son ödeme (\d{2})\.(\d{2})\.(\d{4})", note)
        bdue, bdesc = due_of.get(e["id"], (None, None))
        bm, src = bill_month(e["entry_date"], (bdesc, note, e["description"]), bdue)
        items.append({"id": e["id"], "source": "entry", "date": e["entry_date"], "month": bm, "paid_date": e["entry_date"],
                      "bill_month": bm, "month_source": src,
                      "amount": e["amount"], "description": e["description"],
                      "provider": e["bill_provider"] or _provider_of(e["description"]),
                      "category": e["category"] if e["category"] in CATEGORIES else "Faturalar",
                      "invoice": inv.group(1) if inv else None,
                      "due": f"{due.group(3)}-{due.group(2)}-{due.group(1)}" if due else None})
    for t in txns:
        if t["id"] in linked:
            continue
        items.append({"id": t["id"], "source": "card", "date": t["txn_date"], "month": t["txn_date"][:7],
                      "paid_date": t["txn_date"], "bill_month": t["txn_date"][:7], "month_source": "odeme",
                      "amount": t["amount"], "description": t["description"], "provider": _provider_of(t["description"]),
                      "category": t["category"], "invoice": None, "due": None})
    items = [it for it in items if it["month"] in inwin]
    groups: dict[str, list] = {}
    cats: dict[str, list] = {}
    for it in items:
        groups.setdefault(it["provider"], []).append(it)
        cats.setdefault(it["category"], []).append(it)
    providers = []
    for name, its in groups.items():
        s = _summarise(name, its, window)
        s["category"] = max({i["category"] for i in its}, key=lambda k: sum(1 for i in its if i["category"] == k))
        providers.append(s)
    providers.sort(key=lambda p: -p["total"])
    categories = sorted((_summarise(k, v, window) for k, v in cats.items()), key=lambda p: -p["total"])
    # genel: tüm faturalar (her ödeme bir kez); last/vs_prev_month/vs_last_year/average_12 kategorilerle aynı kural
    overall = _summarise("Toplam", items, window) if items else None
    return {"providers": providers, "categories": categories, "overall": overall, "month_basis": "bill_month",
            "window": {"start": window[0], "end": window[-1]}}
