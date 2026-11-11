"""İlk gün özeti (GET /api/first-look, yetenek "first_look"): kullanıcı ilk kart ekstresini (ya da ilk birkaç belgeyi)
yükleyince yükleme sonucunun üstünde tek ekranlık özet — bu ay ödenecekler (tutar + son ödeme günü), bu ekstrelerde
ödenen faiz ve ücret, gelecek taksitler, sonraki adım önerileri. Hesap sunucuda; istemci toplamaz.

first: kullanıcının İLK HAFTASI mı (hiçbir kart ekstresi 7 günden önce yüklenmemiş). İstemci özeti yalnız first=true
iken belirgin gösterir; sonra normal yükleme sonucu kalır.

Metin kuralı: faize yönlendiren cümle yok — asgari ödeme önerilmez (yanıtta min_payment bile yok); not "tamamını son
ödeme gününe kadar ödersen faiz işlemez".
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app import db, finance, money

FIRST_DAYS = 7  # ilk hafta: ilk kart ekstresinin yüklendiği günden bu yana
MAX_STEPS = 3
FULL_PAY_NOTE = "Ekstrenin tamamını son ödeme gününe kadar ödersen faiz işlemez."


def _card(st: dict) -> str:
    return f"{st['bank']} …{str(st['card_no'])[-4:]}"


def _uploaded_day(st: dict) -> str | None:
    v = st.get("uploaded_at")
    return str(v)[:10] if v else None


def is_first_week(statements: list[dict], today: date) -> bool:
    """Hiç kart ekstresi yoksa ya da en eskisi son FIRST_DAYS gün içinde yüklendiyse True."""
    days = [d for d in (_uploaded_day(s) for s in statements) if d]
    if not statements:
        return True
    if not days:  # yükleme günü bilinmiyor (eski kayıt): ilk hafta sayılmaz
        return False
    return min(days) >= (today - timedelta(days=FIRST_DAYS)).isoformat()


def _pick(statements: list[dict], ids: list[int] | None) -> list[dict]:
    """Özetin ekstreleri: verilen kimlikler (yalnız görünür olanlar: list_statements ortak defter süzgecinden geçmiş)
    ya da verilmezse her kartın en son ekstresi. Kart Excel'inden üretilen ekstre (borç ve son ödeme kurgu) yok."""
    real = [s for s in statements if s.get("source") != "excel"]
    if ids:
        want = set(ids)
        chosen = [s for s in real if s["id"] in want]
        # aynı kartın birkaç ekstresi birlikte yüklendiyse (ör. son 3 ay) kart başına en sonu: "bu ay ödenecek" odur
        latest: dict[str, dict] = {}
        for s in chosen:
            if s["card_no"] not in latest or s["period_end"] > latest[s["card_no"]]["period_end"]:
                latest[s["card_no"]] = s
        return sorted(latest.values(), key=lambda s: (s.get("due_date") or "", s["id"]))
    latest = db.latest_card_statements("9999-12-31", real)
    return sorted(latest.values(), key=lambda s: (s.get("due_date") or "", s["id"]))


def _fees(sids: set[int]) -> dict | None:
    """Bu ekstrelerdeki faiz ve ücret (Faiz sayfasıyla aynı sınıflama). Ortak defterde faiz gizliyse None."""
    from app import book

    if book.hide_fees():
        return None
    by: dict[str, float] = defaultdict(float)
    by_type: dict[str, float] = defaultdict(float)
    n = 0
    for t in db.card_transactions():
        if t["statement_id"] not in sids or t["txn_type"] != finance.FEE:
            continue
        k = finance.interest_type(t["description"])
        by["tax" if k in finance.TAX_KINDS else "fee" if k in finance.FEE_KINDS else "interest"] += t["amount"]
        by_type[k] += t["amount"]
        n += 1
    total = money.money(sum(by.values()))
    return {"total": total, "interest": money.money(by["interest"]), "tax": money.money(by["tax"]),
            "fee": money.money(by["fee"]), "count": n,
            "by_type": sorted(({"type": k, "label": finance.INTEREST_LABELS[k], "total": money.money(v)}
                               for k, v in by_type.items()), key=lambda x: -x["total"])}


AY_KISA = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")
STALE_DAYS = 45  # kart başına son ekstre, en yeni kesimden bu kadar gün eskiyse sayılmaz (kullanılmayan kart)
MAX_NAMED = 2  # alt metinde en çok bu kadar ekstre adıyla; fazlası "3 ekstre"


def _short_day(iso: str) -> str:
    d = date.fromisoformat(str(iso)[:10])
    return f"{d.day} {AY_KISA[d.month - 1]}"


def latest_fees(today: date | None = None) -> dict | None:
    """Özet'in faiz satırı (yetenek "summary_interest_latest"; summary.interest_latest): kart başına EN SON kesilmiş
    ekstredeki faiz, vergi ve ücret (Faiz sayfasıyla aynı sınıflama, _fees). Dönem adına bakmaz: ay dönümü maaş günü
    olan kullanıcıda Kasım başında kesilen ekstre "Eylül döneminde" görünüyordu; burada "Son ekstrelerinde faiz ve
    ücret ₺X" + kaynak "Mavi Bank 7 Kas · Örnek Bank 5 Kas ekstreleri" (MAX_NAMED'den fazlaysa "3 ekstre"). En yeni
    kesimden STALE_DAYS günden eski son ekstre (artık kullanılmayan kart) sayılmaz.
    {total, interest, tax, fee, count, by_type, state var|yok, statement_count, statements [{id, bank, last4,
    period_end, total}], source}. Ortak defterde faiz gizliyse ya da hiç kart ekstresi yoksa None (istemci toplamaz)."""
    today = today or date.today()
    real = [s for s in db.list_statements() if s.get("source") != "excel"]
    latest = sorted(db.latest_card_statements(today.isoformat(), real).values(),
                    key=lambda s: (s["period_end"], s["id"]), reverse=True)
    if not latest:
        return None
    # artık kullanılmayan kartın aylar önceki ekstresi "son ekstre" sayılmaz: en yeni kesimden STALE_DAYS önceye kadar
    cut = (date.fromisoformat(latest[0]["period_end"][:10]) - timedelta(days=STALE_DAYS)).isoformat()
    latest = [s for s in latest if s["period_end"][:10] >= cut]
    fees = _fees({s["id"] for s in latest})
    if fees is None:
        return None
    per: dict[int, float] = defaultdict(float)
    sids = {s["id"] for s in latest}
    for t in db.card_transactions():
        if t["statement_id"] in sids and t["txn_type"] == finance.FEE:
            per[t["statement_id"]] += t["amount"]
    banks = [s["bank"] for s in latest]

    def name(s: dict) -> str:  # aynı bankanın iki kartı: son dört hane ayırır
        last4 = f" …{str(s['card_no'])[-4:]}" if banks.count(s["bank"]) > 1 else ""
        return f"{s['bank']}{last4} {_short_day(s['period_end'])}"

    named = [name(s) for s in latest]
    source = (f"{' · '.join(named)} {'ekstresi' if len(latest) == 1 else 'ekstreleri'}" if len(latest) <= MAX_NAMED
              else f"{len(latest)} ekstre")
    return {**fees, "state": "var" if money.gt(fees["total"], 0) else "yok", "statement_count": len(latest),
            "statements": [{"id": s["id"], "bank": s["bank"], "last4": str(s["card_no"])[-4:], "period_end": s["period_end"],
                            "total": money.money(per.get(s["id"], 0.0))} for s in latest],
            "source": source}


def _installments(cards: set[str]) -> dict:
    """Bu kartların son ekstrelerindeki kalan taksitler (card_projection: Planlama'daki taksit satırlarıyla aynı).
    total: sonraki aylara düşen taksitlerin toplamı; count: kaç alışveriş (vade farkı/vergi taksiti sayılmaz);
    next: ilk ayın tutarı; last_month: son taksitin ayı."""
    per_month: dict[str, float] = defaultdict(float)
    buys: set[int] = set()
    for acc, p in finance.card_projection().items():
        if acc not in cards:
            continue
        for i in p["installments"]:
            per_month[i["month"]] += i["amount"]
            if not i["fee"] and i["amount"] > 0:
                buys.add(i["txn_id"])
    months = sorted(m for m, v in per_month.items() if money.gt(v, 0))
    total = money.money(sum(per_month.values()))
    return {"total": total if money.gt(total, 0) else 0.0, "count": len(buys), "months": len(months),
            "next": {"month": months[0], "label": finance.period_name(months[0]),
                     "amount": money.money(per_month[months[0]])} if months else None,
            "last_month": months[-1] if months else None,
            "last_label": finance.period_name(months[-1]) if months else None}


def _steps(uid: int | None) -> list[dict]:
    """Sonraki adım önerileri (en fazla MAX_STEPS; yalnız yapılmamış olanlar). Ortak defter üyesinde yok (kurulum
    sahibin işi). platform: "mobile" yalnız telefonda anlamlı (bildirim izni)."""
    from app import book, push

    if book.member():
        return []
    with db.connect() as c:
        one = lambda q: bool(c.execute(q).fetchone())  # noqa: E731
        has_dokum = one("SELECT 1 FROM entries WHERE import_hash IS NOT NULL") or one(
            "SELECT 1 FROM accounts WHERE kind <> 'kart' AND as_of IS NOT NULL")
        has_budget = one("SELECT 1 FROM budgets")
    out = []
    if not has_dokum:
        out.append({"key": "dokum", "title": "Banka dökümünü de yükle", "cta": "Döküm yükle",
                    "text": "Maaşın, bakiyen ve kart ödemelerin de gelsin; ödediğin ekstre kendiliğinden işaretlenir."})
    if not has_budget:
        out.append({"key": "butce", "title": "Bütçe koy", "cta": "Bütçe koy",
                    "text": "En çok harcadığın kategoriye aylık sınır koy; yaklaşınca Özet'te uyaralım."})
    try:
        push_on = push.enabled() and uid is not None
        has_device = bool(push.devices(uid)) if push_on else True
    except Exception:  # noqa: BLE001 - bildirim tablosu yoksa öneri de yok
        push_on, has_device = False, True
    if push_on and not has_device:
        out.append({"key": "hatirlatma", "title": "Hatırlatmaları aç", "cta": "Hatırlatmaları aç", "platform": "mobile",
                    "text": "Son ödeme gününden önce telefonuna haber verelim."})
    return out[:MAX_STEPS]


def build(ids: list[int] | None = None, today: date | None = None) -> dict:
    """GET /api/first-look yanıtı.
    {first, statement_count, statements [{id, card, bank, last4, period_end, due_date, total_debt}],
     to_pay {total, items [{statement_id, card, amount (kalan), due_date, days_left, status}], note} — yalnız ödenmemiş
       ve son ödeme günü gelmemiş (ya da bugün) olanlar; past_due: son ödeme günü geçmiş, ödenmemiş görünen sayısı,
     fees {total, interest, tax, fee, count, by_type} | null (ortak defterde faiz gizli),
     installments {total, count, months, next {month, label, amount} | null, last_month, last_label},
     steps [{key dokum|butce|hatirlatma, title, text, cta, platform?}]}"""
    today = today or date.today()
    all_st = db.list_statements()
    chosen = _pick(all_st, ids)
    pays = {p["id"]: p for p in finance.payables(today) if p["type"] == "statement"}
    items, past_due = [], 0
    for s in chosen:
        p = pays.get(s["id"])
        if not p or p["status"] == "odendi" or not s.get("due_date"):
            continue
        left = money.money(max(0.0, (p["amount"] or 0) - (p["paid_amount"] or 0)))
        if not money.gt(left, 0):
            continue
        if s["due_date"] < today.isoformat():
            past_due += 1
            continue
        items.append({"statement_id": s["id"], "card": _card(s), "amount": left, "due_date": s["due_date"],
                      "days_left": (date.fromisoformat(s["due_date"]) - today).days, "status": p["status"]})
    u = db.current_user()  # hazır sürümde oturumun kişisi (bildirim cihazı kişi başına); kendi sunucusunda None
    return {
        "first": is_first_week(all_st, today),
        "statement_count": len(all_st),
        "statements": [{"id": s["id"], "card": _card(s), "bank": s["bank"], "last4": str(s["card_no"])[-4:],
                        "period_end": s["period_end"], "due_date": s.get("due_date"), "total_debt": s.get("total_debt")}
                       for s in chosen],
        "to_pay": {"total": money.money(sum(i["amount"] for i in items)), "items": items,
                   "note": FULL_PAY_NOTE if items else None},
        "past_due": past_due,
        "fees": _fees({s["id"] for s in chosen}),
        "installments": _installments({s["card_no"] for s in chosen}),
        "steps": _steps(u.get("id") if u else None),
    }
