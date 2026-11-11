"""Borç kapatma planı (GET/PUT /api/debt-plan, yetenek "debt_plan"): açık borçlar tek listede, ay ay ödeme takvimi.

İlke (sahibin kararı, değişmez): plan borçtan çıkmayı ve FAİZ ÖDEMEMEYİ hedefler (niyet: insanları faizden
kurtarmak). Hiçbir metin kredi, borç transferi, taksitlendirme, nakit avans, yapılandırma, erteleme ya da faizli bir
ürün önermez. Tamamı ödenebiliyorken "asgariyi öde" bir seçenek değildir, yalnız uyarıdır (kalan borca ne kadar faiz
işleyeceği). Banka/ortaklık teklifi yoktur.

Zararı azaltma (yalnız tutar bir kartın tamamına son ödeme gününe kadar yetmiyorsa; kart satırında harm): önce son
ödeme gününden önce en az ekstredeki asgari tutar (gecikme faizi işlemesin), asgarinin üstündeki her lira kalan borcun
akdi faizini azaltır; karşılaştırma: yalnız asgari / planlanan / tamamı (₺0). Yasal asgari oranları kodda yok: tutar
ekstreden okunur, sonraki aylar için ekstrenin kendi asgari/borç oranı kullanılır (yaklaşık). Tutar yetmeyen ayda
dağıtım: önce son ödemesi gelen her kartın asgarisi (en erken son ödeme önce), kalanı faiz oranı en yüksek (eşitse
borcu büyük) karta, kart kapanınca sıradakine; kartlar bitince elden borçlar.

Planı başlat (POST /api/debt-plan/commit, ayar "debt_plan_commit"): o günkü borçlarla ay ay hedefler kaydedilir.
Sonraki aylarda ödenen (ekstre ve borç ödemeleri, plandaki borç başına hedefle sınırlı) birikmiş hedefle karşılaştırılır:
plana uygun / gerisinde (bu ay ne kadar ödersen yetişirsin) / önünde (daha erken bitebilir). Plana yeni borç girdiyse ya
da geçen ayların hedefi eksik kaldıysa "Plan değişti" uyarısı; yeniden oluşturmak eski planı geçmişe taşır. Önizleme
(?monthly, ?order) kaydedilen planı değiştirmez.

Borçlar kendiliğinden toplanır:
  kart      her kartın son ekstresinin ödenmemiş kısmı (finance.payables: kısmi ödeme düşülmüş; eski ekstreler yeni
            ekstrenin toplamında olduğu için ayrıca sayılmaz). Gelecek taksitler bilgi olarak (her ay ekstreye eklenir).
  kişi      Borç / Alacak'taki açık "borcum" kayıtları (TL; elden borç ve elle girilen diğer borçlar). Döviz / altın
            borcu planda yer almaz, "excluded" listesinde görünür.
  taksit    Ödeme planı olan borç (app/debt_schedule.py; ör. vergi/SGK, banka, bir kişiyle anlaşılmış taksitler): plan
            bunu yalnız kaydedilmiş bir yükümlülük olarak okur. Her ay vadesi gelen taksit (geçmişte eksik kalan dahil)
            aylık tutardan ÖNCE ayrılır; kalan kartlara, sonra elden borçlara gider. Taksitler öne çekilmez.

Tutarlar (sahibin geri bildirimi: aynı kişiye küsuratlı tutarlar alt alta anlamsız): elden borç ödemeleri kişi
başına toplanır ve ROUND_TO katlarına yuvarlanır; kişi başı en az MIN_PAY (kalan daha azsa kalanın tamamı); FOLD'dan
küçük kalan bırakılmaz, önceki ödemeye eklenir. "Faizi önle"de orantılı pay MIN_PAY'in altına düşerse önce en küçük
borçlar bütün olarak kapanır (daha az, daha büyük ödeme). Kart ve taksit tutarları tam (kuruşuyla) kalır: kart borcu
tamamı ödenmezse faiz işler, taksit tutarı anlaşmadır. Yanıtta this_month.groups ve months[].groups aynı kişinin
borçlarını tek satırda toplar (items / payments eski istemciler için borç borç kalır).

Aylık tutar: her zamanki harcamaların (taksitler dahil) karşılandıktan sonra borçlara ayırabileceğin para. Önerisi
Planlama'nın "her zamanki harcamanla" artanı (months[].net_with_estimate ortalaması), hiçbir zaman eksi değil.

Sıralama (her seçenekte KARTLAR ÖNCE: kartta kalan borca faiz işler; faizli borç her zaman ilk kapanır):
  faiz   Faizi önle (varsayılan): kartlar son ödeme gününe göre; kalan para elden borçlara kalanlarıyla orantılı.
  kucuk  En küçükten başla: kartlardan sonra en küçük elden borç önce (her kapanan borç moral verir).
  soz    Söz verdiğim tarihe göre: kartlardan sonra söz verdiğin tarihi en yakın olan; tarihsizler en eskiden.

Takvim takvim ayıdır (bu ay = 0). Bu ay borçlara zaten ödenen, bu ayın tutarından düşülür. Kart borcu son ödeme
gününün ayında kapanmazsa kalanına aylık faiz (ekstredeki "Oran:%x" biliniyorsa o, yoksa genel varsayım) + vergiler
eklenir: bu yalnız "bu tutarla ne olur" uyarısıdır; plan böyle bir durumu çözüm olarak sunmaz, kapatacak tutarı söyler.

Ortak defter: kartlar ve hesaplar db süzgeçlerinden geçer (gizli kart yok); Borç / Alacak sayfası üyeye kapalıysa
elden borçlar plana girmez; faiz gizliyse faiz tutarı yazılmaz. Ayar (aylık tutar, sıralama) defterindir: "Görür ve
ekler" ya da tam yetkili üye değiştirir (Aile birlikte planlar), "Sadece görür" yalnız önizler.
"""
import json
import math
import re
from datetime import date

from app import db, money

ORDERS = ("faiz", "kucuk", "soz")
ORDER_INFO = {
    "faiz": ("Faizi önle", "Önce kart borçları, son ödeme gününe göre: kartta borç kalmazsa faiz işlemez. Kalan para "
                           "elden borçlara kişi kişi, kalanlarıyla orantılı paylaşılır; pay çok küçük kalırsa önce "
                           "küçük borçlar bütün olarak kapanır."),
    "kucuk": ("En küçükten başla", "Önce kart borçları. Sonra en küçük elden borç; her kapanan borç bir sonrakine güç verir."),
    "soz": ("Söz verdiğim tarihe göre", "Önce kart borçları. Sonra söz verdiğin tarihi en yakın olan; tarihi olmayanlar "
                                       "en eskiden başlayarak."),
}
GENERIC_RATE = 0.035   # aylık akdi faiz varsayımı (ekstrede oran yoksa); metinde "yaklaşık" yazılır
TAX = 0.30             # faize eklenen vergiler: BSMV %15 + KKDF %15
HORIZON = 120          # en çok 10 yıl
SETTING = "debt_plan"  # defter ayarı: {"monthly": float | null, "order": "faiz"}
COMMIT = "debt_plan_commit"  # defter ayarı: {"current": anlık görüntü | null, "history": [{started_at, ...}]}
HISTORY = 5
TOL = 1.0              # plan takibinde 1 TL'lik fark yok sayılır
ROUND_TO = 50          # elden borç ödemeleri bu katlara yuvarlanır (kart ve taksit tutarı tam kalır)
MIN_PAY = 250          # kişi başı en az anlamlı ödeme (kalan daha azsa kalanın tamamı)
FOLD = 100             # bundan küçük kalan bırakılmaz: aynı ayın ödemesine eklenir (aylık tutarı en çok bu kadar aşar)
# "Ocak'ta" gibi bulunma eki (ayın son sesine göre)
_AY_EK = ("'ta", "'ta", "'ta", "'da", "'ta", "'da", "'da", "'ta", "'de", "'de", "'da", "'ta")
_YIL_EK = {"0": "'da", "1": "'de", "2": "'de", "3": "'te", "4": "'te", "5": "'te", "6": "'da", "7": "'de", "8": "'de",
           "9": "'da"}
FAIZ_INFO = ("Faiz neden işler: kartta son ödeme gününe kadar ödenmeyen kalan borca akdi (alışveriş) faizi işler. "
             "Asgariyi de ödemezsen üstüne gecikme faizi eklenir. Faiz kalan borca işlediği için ne kadar çok "
             "yatırırsan o kadar az faiz çıkar; tamamını ödersen hiç çıkmaz.")
AYLAR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
_RATE_RE = re.compile(r"ORAN\s*[:=]?\s*%?\s*(\d{1,2}[.,]\d{1,3})")


def _tl(x: float) -> str:
    return money.tl(x, trim=True)


def _r(x: float) -> float:
    return money.money(x)


def _ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _add(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def month_label(month: str) -> str:
    y, m = month.split("-")
    return f"{AYLAR[int(m) - 1]} {y}"


def _day_label(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {AYLAR[d.month - 1]}"


def month_in(month: str, today_month: str | None = None) -> str:
    """"Ocak'ta" / "Ocak 2028'de" (yıl bu yıl değilse)."""
    y, m = month.split("-")
    if today_month and today_month[:4] == y:
        return AYLAR[int(m) - 1] + _AY_EK[int(m) - 1]
    return f"{AYLAR[int(m) - 1]} {y}{_YIL_EK[y[-1]]}"


def _months_between(a: str, b: str) -> int:
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


# ---------------------------------------------------------------- ayar

def settings() -> dict:
    try:
        raw = json.loads(db.get_setting(SETTING) or "{}")
    except ValueError:
        raw = {}
    monthly = raw.get("monthly")
    order = raw.get("order")
    return {"monthly": _r(monthly) if isinstance(monthly, (int, float)) and monthly >= 0 else None,
            "order": order if order in ORDERS else "faiz"}


def save_settings(data: dict) -> dict:
    cur = settings()
    if "monthly" in data:
        v = data["monthly"]
        cur["monthly"] = None if v is None else _r(max(0.0, float(v)))
    if data.get("order") in ORDERS:
        cur["order"] = data["order"]
    db.set_setting(SETTING, json.dumps(cur))
    return cur


# ---------------------------------------------------------------- girdiler

def _hidden_people() -> bool:
    from app import book

    r = book.active()
    return bool(r and "borclar" in r["hidden_pages"])


def _plan_hidden() -> bool:
    from app import book

    r = book.active()
    return bool(r and "plan" in r["hidden_pages"])


def _learned_rates() -> dict[str, float]:
    """Kart → ekstredeki son akdi (alışveriş) faiz oranı (aylık, ör. 0.0375). Açıklamada "Oran:3.75" yazıyorsa."""
    from app.finance import _fold

    out: dict[str, float] = {}
    with db.connect() as c:
        rows = c.execute("SELECT t.description, s.card_no FROM transactions t JOIN statements s ON s.id = t.statement_id "
                         "WHERE t.txn_type = 'faiz_ucret' AND t.is_deleted = 0 ORDER BY s.period_end DESC").fetchall()
    for desc, card in rows:
        if card in out:
            continue
        k = _fold(desc)
        if not any(w in k for w in ("ALISVERIS", "AKDI")):
            continue
        m = _RATE_RE.search(desc.upper().replace("İ", "I"))
        if m:
            pct = float(m.group(1).replace(",", "."))  # yüzde (3.75)
            if 0.5 <= pct <= 15:
                out[card] = pct / 100
    return out


def _cards(today: date) -> tuple[list[dict], list[dict]]:
    """(açık kart borçları, bu ay tamamı ödenmiş kart ekstreleri)."""
    from app import finance

    stmts = {s["id"]: s for s in db.list_statements()}
    proj = finance.card_projection()
    rates = _learned_rates()
    this_month = _ym(today)
    open_, done = [], []
    for p in finance.payables(today):
        if p["type"] != "statement":
            continue
        st = stmts.get(p["id"]) or {}
        card_no = st.get("card_no") or ""
        paid = p["paid_amount"] or 0.0
        paid_now = paid if (p.get("paid_at") or "")[:7] == this_month else 0.0
        if p["status"] == "odendi":
            if paid_now and not p.get("rolled_into"):
                done.append({"key": f"kart:{p['id']}", "kind": "card", "id": p["id"], "title": _card_title(st, p),
                             "paid": _r(paid_now), "payable": p})
            continue
        if not p.get("in_total"):
            continue  # ödenmemiş eski ekstre: kalanı kartın son ekstresinde
        remaining = _r(max(0.0, p["amount"] - paid))
        if remaining <= 0:
            continue
        inst = [i for i in (proj.get(card_no) or {}).get("installments", []) if i["amount"] > 0]
        future = None
        if inst:
            last = max(i["month"] for i in inst)
            total = _r(sum(i["amount"] for i in inst))
            future = {"total": total, "last_month": last, "last_label": month_label(last),
                      "text": f"İleride {_tl(total)} taksit daha var; her ay ekstrene eklenir, son taksit "
                              f"{month_label(last)}. Aylık tutarın bunların dışında."}
        rate = rates.get(card_no)
        open_.append({
            "key": f"kart:{p['id']}", "kind": "card", "id": p["id"], "title": _card_title(st, p), "card_no": card_no,
            "bank": st.get("bank"), "period_end": st.get("period_end"), "total": _r(p["amount"]), "paid": _r(paid),
            "paid_now": _r(paid_now),
            "remaining": remaining, "due_date": p["due_date"], "min_payment": p["min_payment"],
            "overdue": p["due_date"] < today.isoformat(),
            "rate": {"monthly": rate or GENERIC_RATE, "source": "ekstre" if rate else "genel"},
            "future_installments": future, "payable": p,
        })
    return open_, done


def _card_title(st: dict, p: dict) -> str:
    """Kısa kart adı ("Örnek Bank ••1234"; hatırlatmalarla aynı): uzun kart numarası dar ekranda bölünmesin."""
    card = st.get("card_no") or ""
    return f"{st.get('bank')} ••{card[-4:]}" if st.get("bank") and len(card) >= 4 else p["title"]


def _people(today: date) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """(açık TL elden borçlarım, TL dışı borçlar, bu ay tamamen ödenen borçlar, ödeme planı olan açık borçlar)."""
    from app import debt_schedule, finance

    if _hidden_people():
        return [], [], [], []
    this_month = _ym(today)
    payables = {p["id"]: p for p in finance.payables(today) if p["type"] == "debt"}
    open_, other, done, fixed = [], [], [], []
    for d in db.list_debts():
        if d["direction"] != "borc" or d["written_off_at"]:
            continue
        paid_now = sum(x["amount"] for x in d["payments"] if (x["paid_at"] or "")[:7] == this_month)
        if d["asset"] != "TRY":
            if not d["settled_at"] and d["remaining"] > 0:
                other.append({"key": f"borc:{d['id']}", "title": d["person"],
                              "text": f"{finance.fmt_asset(d['remaining'], d['asset'])}: TL dışı borç plana girmez; "
                                      "Borç / Alacak'ta takip edilir."})
            continue
        payable = payables.get(d["id"]) or _debt_payable(d, today)
        if d["settled_at"] or money.is_zero(d["remaining"]) or d["remaining"] < 0:
            if paid_now and (d["settled_at"] or "")[:7] == this_month:
                done.append({"key": f"borc:{d['id']}", "kind": "person", "id": d["id"], "title": d["person"],
                             "paid": _r(paid_now), "payable": payable})
            continue
        if d.get("schedule"):
            fixed.append(_fixed_item(d, payable, paid_now, today))
            continue
        open_.append({
            "key": f"borc:{d['id']}", "kind": "person", "id": d["id"], "title": d["person"],
            "note": d["note"] or None, "total": _r(d["amount"]), "paid": _r(d["paid_amount"] or 0),
            "paid_now": _r(paid_now), "remaining": _r(d["remaining"]), "due_date": d["due_date"],
            "debt_date": d["debt_date"], "overdue": bool(d["due_date"] and d["due_date"] < today.isoformat()),
            "payable": payable,
        })
    return open_, other, done, fixed


def _fixed_item(d: dict, payable: dict, paid_now: float, today: date) -> dict:
    """Ödeme planı olan borç: kalan taksitler ay ay (ödenen toplam en eski taksitten düşer; geçmişte eksik kalan bu aya)."""
    from app import debt_schedule

    sch = d["schedule"]
    start = _ym(today)
    left_paid = debt_schedule.plan_paid(d)  # ilk taksit ayından önce ödenen plana girmez
    dues: dict[str, float] = {}
    due_nos: list[int] = []  # bu aya kadar vadesi gelip ödenmemiş taksitlerin numaraları
    for r in debt_schedule.installments(d):
        take = min(left_paid, r["amount"])
        left_paid = _r(left_paid - take)
        rest = _r(r["amount"] - take)
        if rest > 0:
            m = max(r["month"], start)
            dues[m] = _r(dues.get(m, 0.0) + rest)
            if r["month"] <= start:
                due_nos.append(r["no"])
    nx = sch["next"]
    return {
        "key": f"borc:{d['id']}", "kind": "fixed", "id": d["id"], "title": d["person"], "note": d["note"] or None,
        "total": _r(d["amount"]), "paid": _r(d["paid_amount"] or 0), "paid_now": _r(paid_now),
        "remaining": _r(d["remaining"]), "due_date": nx["date"] if nx else None, "debt_date": d["debt_date"],
        "overdue": bool(sch["overdue"]), "dues": dues, "due_nos": due_nos, "day": sch["day"], "schedule": sch,
        "creditor_label": sch["creditor_label"], "payable": payable,
    }


def _debt_payable(d: dict, today: date) -> dict:
    """Ödemeler listesinde olmayan (vadesiz) borcun "Ödedim" penceresi için Payable biçimi (finance.payables ile aynı)."""
    due = d["due_date"] or today.isoformat()
    paid = d["paid_amount"] or None
    status = "odendi" if d["settled_at"] else "kismi" if paid else "bekliyor"
    return {"type": "debt", "id": d["id"], "key": f"debt:{d['id']}", "title": f"{d['person']} (borç)",
            "subtitle": " · ".join(x for x in ("Borç", d["note"]) if x), "amount": d["amount"], "min_payment": None,
            "due_date": due, "days_left": (date.fromisoformat(due) - today).days, "paid_amount": paid,
            "paid_at": d["settled_at"], "status": status, "in_total": status != "odendi"}


def suggestion(today: date) -> dict | None:
    """Planlama'nın "her zamanki harcamanla" artanının ortalaması (en çok 3 ay; ücretsizde 1), 100'e aşağı yuvarlı."""
    from app import finance, plans

    if _plan_hidden():
        return None
    try:
        months = finance.plan(plans.plan_months(db.current_user(), 3), today=today)["months"]
    except Exception:  # noqa: BLE001 - öneri hesaplanamazsa plan yine çalışsın
        return None
    if not months:
        return None
    nets = [m["net_with_estimate"] for m in months]
    avg = sum(nets) / len(nets)
    amount = float(max(0, math.floor(avg / 100) * 100))
    text = (f"Planlama'ya göre her zamanki harcamanla ayda yaklaşık {_tl(amount)} artıyor." if amount > 0 else
            "Planlama'ya göre her zamanki harcamanla bu ay artan para görünmüyor; ayırabileceğin tutarı sen yaz.")
    return {"amount": amount, "raw": _r(max(0.0, avg)), "months": len(nets), "text": text}


# ---------------------------------------------------------------- takvim

def _eff(rate: float) -> float:
    return rate * (1 + TAX)


def _sort_cards(cards: list[dict]) -> list[dict]:
    # gecikmiş önce (son ödemesi en eski), sonra son ödeme gününe göre; aynı günde büyük borç önce (faiz riski büyük)
    return sorted(cards, key=lambda d: (d["due_date"] or "9999", -d["remaining"]))


def _sort_people(people: list[dict], order: str) -> list[dict]:
    if order == "kucuk":
        return sorted(people, key=lambda d: (d["remaining"], d["due_date"] or "9999", d["id"]))
    # soz (ve faiz'de kalan dağıtımın sırası): söz verilen tarih önce, tarihsizler en eskiden
    return sorted(people, key=lambda d: (d["due_date"] is None, d["due_date"] or "", d["debt_date"] or "", d["id"]))


def person_key(title: str) -> str:
    """Aynı kişinin borçları tek satırda toplansın: "Nermin Teyze" = "nermin teyze " (Türkçe büyük/küçük harf)."""
    return " ".join(title.replace("İ", "i").replace("I", "ı").lower().split())


def _floor(x: float) -> float:
    """ROUND_TO katına aşağı (ROUND_TO'dan küçükse tam liraya)."""
    if x >= ROUND_TO:
        return float(math.floor(x / ROUND_TO + 1e-9) * ROUND_TO)
    return float(math.floor(max(0.0, x) + 1e-9))


def _part(rem: float, budget: float, any_paid: bool) -> float:
    """Bir kişiye (ya da borca) bütçeden ne kadar: yetiyorsa tamamı; FOLD'dan küçük kalan bırakmaz (tamamı); değilse
    ROUND_TO katına yuvarlı kısmi ve sonraya en az MIN_PAY bırakır. Bu ay başka ödeme varken MIN_PAY'den küçük kısmi
    ödeme yapılmaz (0)."""
    if budget <= 0 or rem <= 0:
        return 0.0
    if money.ge(budget, rem) or rem - budget < FOLD:
        return rem
    amt = _floor(budget)
    if rem - amt < MIN_PAY:
        amt = _floor(rem - MIN_PAY)
    if amt < MIN_PAY and any_paid:
        return 0.0
    return max(0.0, amt)


def _round_shares(shares: dict[str, float], budget: float) -> dict[str, float]:
    """Payları ROUND_TO katlarına: toplam bütçenin yuvarlısı (en büyük küsurat önce)."""
    base = {k: _floor(v) if v >= ROUND_TO else 0.0 for k, v in shares.items()}
    left = _floor(budget) - sum(base.values())
    for k in sorted(shares, key=lambda k: -(shares[k] - base[k])):
        if left < ROUND_TO:
            break
        base[k] += ROUND_TO
        left -= ROUND_TO
    return {k: v for k, v in base.items() if v > 0}


def _group_people(people: list[dict], rem: dict[str, float]) -> dict[str, list[dict]]:
    """Kişi → açık borçları (verilen sırada)."""
    out: dict[str, list[dict]] = {}
    for d in people:
        if rem[d["key"]] > 0:
            out.setdefault(person_key(d["title"]), []).append(d)
    return out


def _within(debts: list[dict], rem: dict[str, float], amount: float) -> dict[str, float]:
    """Kişiye ayrılan tutar borçlarına: söz tarihi yakın, sonra küçük borç önce; önce bütün olarak kapanır."""
    out: dict[str, float] = {}
    for d in sorted(debts, key=lambda d: (d["due_date"] is None, d["due_date"] or "", rem[d["key"]], d["id"])):
        if amount <= 0:
            break
        amt = _r(min(rem[d["key"]], amount))
        if amt > 0:
            out[d["key"]] = amt
            amount = _r(amount - amt)
    return out


def allocate_people(people_s: list[dict], rem: dict[str, float], budget: float, order: str) -> dict[str, float]:
    """Kartlardan (ve taksitlerden) kalan bütçe elden borçlara. kucuk/soz: sıradaki borç önce (borç borç). faiz: kişi
    kişi kalanıyla orantılı; pay MIN_PAY'in altına düşerse en küçük kişinin borcu bütün olarak kapanır. Tutarlar
    _part / _round_shares ile anlamlı (yuvarlı, küçük kalan yok)."""
    out: dict[str, float] = {}
    budget = _r(budget)
    if budget <= 0:
        return out
    if order != "faiz":
        for d in people_s:
            if budget <= 0:
                break
            amt = _part(rem[d["key"]], budget, bool(out))
            if amt > 0:
                out[d["key"]] = _r(amt)
                budget = _r(budget - amt)
        return out
    groups = _group_people(people_s, rem)
    idx = {k: i for i, k in enumerate(groups)}
    prem = {k: _r(sum(rem[d["key"]] for d in v)) for k, v in groups.items()}
    amounts: dict[str, float] = {}
    live = list(groups)
    while budget > 0 and live:
        total = sum(prem[k] for k in live)
        if money.ge(budget, total):
            amounts.update({k: prem[k] for k in live})
            break
        if len(live) == 1:
            amt = _part(prem[live[0]], budget, bool(amounts))
            if amt > 0:
                amounts[live[0]] = amt
            break
        shares = {k: budget * prem[k] / total for k in live}
        # payı kalanına yakın (küçük kalan bırakacak) kişi bütün olarak kapanır
        capped = [k for k in live if shares[k] >= prem[k] - MIN_PAY - ROUND_TO and money.le(prem[k], budget)]
        if not capped and min(shares.values()) < MIN_PAY:
            k = min(live, key=lambda k: (prem[k], idx[k]))
            if money.le(prem[k], budget):
                capped = [k]
            else:  # en küçüğe bile yetmiyor: hepsi ona (bölüp küçük ödemeler yapılmaz)
                amt = _part(prem[k], budget, bool(amounts))
                if amt > 0:
                    amounts[k] = amt
                break
        if capped:
            for k in sorted(capped, key=lambda k: (prem[k], idx[k])):
                if money.le(prem[k], budget):
                    amounts[k] = prem[k]
                    budget = _r(budget - prem[k])
                    live.remove(k)
            continue
        amounts.update(_round_shares(shares, budget))
        break
    for k, amt in amounts.items():
        out.update(_within(groups[k], rem, amt))
    return out


def simulate(cards: list[dict], people: list[dict], monthly: float, order: str, start: str,
             first_budget: float | None = None, fixed: list[dict] | None = None) -> dict:
    """Ay ay ödeme. cards/people: {key, title, remaining, due_date, ...} (kartta rate.monthly); fixed: ödeme planı olan
    borçlar {key, title, remaining, dues {ay: tutar}, day}. Her ay önce o ayın taksitleri (eksik kalan sonraki aya
    devreder), sonra kartlar, sonra elden borçlar. Dönen: months, closes {key: ay}, interest (toplam, varsayım),
    interest_by {key: tutar}, debt_free (ay | None)."""
    fixed = fixed or []
    rem = {d["key"]: d["remaining"] for d in (*fixed, *cards, *people)}
    cards_s = _sort_cards(cards)
    people_s = _sort_people(people, order)
    fixed_s = sorted(fixed, key=lambda d: (d.get("day") or 1, d["id"]))
    carry = {d["key"]: 0.0 for d in fixed_s}
    titles = {d["key"]: d["title"] for d in (*fixed_s, *cards_s, *people_s)}
    canon: dict[str, str] = {}  # aynı kişinin borçları tek ad altında ("nermin teyze" → "Nermin Teyze")
    for d in sorted(people_s, key=lambda d: d["id"]):
        titles[d["key"]] = canon.setdefault(person_key(d["title"]), d["title"])
    kinds = {d["key"]: d["kind"] for d in (*fixed_s, *cards_s, *people_s)}
    closes: dict[str, str] = {}
    interest_by: dict[str, float] = {}
    months: list[dict] = []
    debt_free = None
    if all(v <= 0 for v in rem.values()):
        return {"months": [], "closes": {}, "interest": 0.0, "interest_by": {}, "debt_free": start}
    for k in range(HORIZON):
        m = _add(start, k)
        budget = _r(first_budget if k == 0 and first_budget is not None else monthly)
        pays: dict[str, float] = {}
        # 1) ödeme planı olan borçların bu ayki taksiti (vadesi gelmiş yükümlülük: önce ayrılır)
        fixed_due = fixed_short = 0.0
        for d in fixed_s:
            due = _r(min(rem[d["key"]], carry[d["key"]] + d["dues"].get(m, 0.0)))
            if due <= 0:
                continue
            amt = _r(min(due, budget))
            fixed_due = _r(fixed_due + due)
            if amt > 0:
                pays[d["key"]] = amt
                budget = _r(budget - amt)
            carry[d["key"]] = _r(due - amt)
            fixed_short = _r(fixed_short + due - amt)
        # 2) kartlar
        due_now = [d for d in cards_s if rem[d["key"]] > 0 and (d["due_date"] or "9999")[:7] <= m]
        if due_now and not money.ge(budget, sum(rem[d["key"]] for d in due_now)):
            # tutar son ödemesi gelen kartlara yetmiyor: önce her birinin asgarisi (gecikme faizi işlemesin), kalanı
            # faizi en yüksek karta (eşitse borcu büyük); kart kapanınca sıradakine
            for d in due_now:
                amt = min(_asgari(d, rem[d["key"]], m, k == 0), rem[d["key"]], budget)
                if amt > 0:
                    pays[d["key"]] = _r(amt)
                    budget = _r(budget - amt)
            for d in sorted(due_now, key=lambda d: (-d["rate"]["monthly"], -(rem[d["key"]] - pays.get(d["key"], 0.0)))):
                if budget <= 0:
                    break
                amt = min(_r(rem[d["key"]] - pays.get(d["key"], 0.0)), budget)
                if amt > 0:
                    pays[d["key"]] = _r(pays.get(d["key"], 0.0) + amt)
                    budget = _r(budget - amt)
        else:
            for d in cards_s:
                if budget <= 0:
                    break
                amt = min(rem[d["key"]], budget)
                if amt > 0:
                    pays[d["key"]] = amt
                    budget = _r(budget - amt)
        for key, amt in pays.items():
            rem[key] = _r(rem[key] - amt)
        # 3) elden borçlar (kişi kişi, yuvarlı)
        if budget > 0:
            for key, amt in allocate_people(people_s, rem, budget, order).items():
                amt = _r(amt)
                if amt > 0:
                    pays[key] = _r(pays.get(key, 0.0) + amt)
                    rem[key] = _r(rem[key] - amt)
        used = _r(sum(pays.values()))
        # son ödeme ayı geçen kartta kalan borca faiz (+ vergi) işler: uyarı için varsayım
        month_interest = 0.0
        for d in cards_s:
            if rem[d["key"]] > 0 and (d["due_date"] or "9999")[:7] <= m:
                add = _r(rem[d["key"]] * _eff(d["rate"]["monthly"]))
                rem[d["key"]] = _r(rem[d["key"]] + add)
                interest_by[d["key"]] = _r(interest_by.get(d["key"], 0.0) + add)
                month_interest = _r(month_interest + add)
        rows = []
        for key, amt in pays.items():
            closed = rem[key] <= 0
            if closed and key not in closes:
                closes[key] = m
            rows.append({"key": key, "kind": kinds[key], "title": titles[key], "amount": _r(amt), "closes": closed})
        left = _r(sum(v for v in rem.values() if v > 0))
        months.append({"month": m, "label": month_label(m), "total": used, "payments": rows, "groups": group_rows(rows),
                       "interest": month_interest, "left": left, "fixed_due": fixed_due, "fixed_short": fixed_short})
        if left <= 0:
            debt_free = m
            break
        if monthly <= 0:
            break  # tutar yok: tek ay gösterilir (faiz uyarısıyla)
    return {"months": months, "closes": closes, "interest": _r(sum(interest_by.values())), "interest_by": interest_by,
            "debt_free": debt_free}


def group_rows(rows: list[dict]) -> list[dict]:
    """Ay satırları kişi kişi: aynı kişiye (elden borç) birden çok ödeme tek satır {key, kind, title, count, amount,
    closes (hepsi kapanıyorsa), keys}. Kart ve taksit satırları tek başına kalır."""
    out: list[dict] = []
    by: dict[str, dict] = {}
    for r in rows:
        if r["kind"] != "person":
            out.append({**r, "count": 1, "keys": [r["key"]]})
            continue
        pk = person_key(r["title"])
        g = by.get(pk)
        if g is None:
            g = by[pk] = {"key": f"kisi:{pk}", "kind": "person", "title": r["title"], "amount": 0.0, "closes": True,
                          "count": 0, "keys": []}
            out.append(g)
        g["amount"] = _r(g["amount"] + r["amount"])
        g["closes"] = g["closes"] and r["closes"]
        g["count"] += 1
        g["keys"].append(r["key"])
    return out


def _asgari(d: dict, rem_now: float, month: str, first: bool) -> float:
    """Kartın o ay en az yatırılacak tutarı (gecikme faizi işlemesin). Son ödeme ayına kadar (ve ilk ay) ekstredeki
    asgari − ödenen; sonraki aylarda ekstrenin kendi asgari/borç oranıyla kalan borçtan (yaklaşık). Asgari bilinmiyorsa 0:
    yasal oranlar kodda yok."""
    mp = d.get("min_payment")
    if not mp or mp <= 0:
        return 0.0
    if first or month <= (d.get("due_date") or "9999")[:7]:
        return _r(max(0.0, mp - (d.get("paid") or 0.0)))
    total = d.get("total") or 0.0
    return _r(rem_now * mp / total) if total > 0 else 0.0


def min_monthly(cards: list[dict], start: str, paid_now: float = 0.0, fixed: list[dict] | None = None) -> float:
    """Kart borçlarının hepsi son ödeme aylarında kapansın (faiz işlemesin) diye gereken en az aylık tutar, 10 TL'ye
    yukarı yuvarlı. Bu ay ödenen (paid_now) bu ayın tutarından düşüldüğü için eklenir. Ödeme planı olan borçların
    taksitleri her ay önce ayrıldığı için o aya kadarki taksitler de eklenir (ve tutar her ayın taksitine yetmeli)."""
    fixed_by: dict[str, float] = {}
    for f in fixed or []:
        for m, a in f["dues"].items():
            fixed_by[m] = fixed_by.get(m, 0.0) + a
    def fixed_until(k: int) -> float:
        return sum(a for m, a in fixed_by.items() if _months_between(start, m) <= k)
    need, cum = 0.0, 0.0
    for d in _sort_cards(cards):
        cum += d["remaining"]
        k = max(0, _months_between(start, (d["due_date"] or start)[:7]))
        need = max(need, (cum + paid_now + fixed_until(k)) / (k + 1))
    if cards:
        need = max([need] + [a + (paid_now if m == start else 0.0) for m, a in fixed_by.items()])
    return float(math.ceil(need / 10 - 1e-9) * 10)


# ---------------------------------------------------------------- yanıt

def build(monthly: float | None = None, order: str | None = None, today: date | None = None) -> dict:
    from app import book

    today = today or date.today()
    start = _ym(today)
    saved = settings()
    order = order if order in ORDERS else saved["order"]
    sug = suggestion(today)
    source = "param" if monthly is not None else "saved" if saved["monthly"] is not None else (
        "suggestion" if sug and sug["amount"] > 0 else None)
    if monthly is None:
        monthly = saved["monthly"] if saved["monthly"] is not None else (sug["amount"] if sug else None)
    monthly = _r(max(0.0, monthly)) if monthly is not None else None
    fees_hidden = book.hide_fees()
    people_hidden = _hidden_people()

    cards, cards_done = _cards(today)
    people, other, people_done, fixed = _people(today)
    paid_now = _paid_now(cards, [*people, *fixed], cards_done, people_done)
    need = min_monthly(cards, start, paid_now, fixed) if cards else 0.0
    if source == "suggestion" and sug and need > 0:
        # öneri artanın 100'e aşağı yuvarlısı: artan faizsiz tutara yetiyorsa öneri en az o tutar olsun (yuvarlama
        # yüzünden faizli bir plan önerilmesin); yetmiyorsa öneri cümlesi faizsiz tutarı da söyler
        if sug["amount"] < need <= sug.get("raw", sug["amount"]):
            sug = {**sug, "amount": need}
            monthly = need
        elif sug["amount"] < need:
            sug = {**sug, "text": sug["text"] + f" Faizsiz kapatmak için ayda {_tl(need)} gerekiyor."}
    first = _r(max(0.0, (monthly or 0) - paid_now))
    sim = simulate(cards, people, monthly or 0.0, order, start, first_budget=first, fixed=fixed) \
        if monthly is not None else None
    fixed_s = sorted(fixed, key=lambda d: (d["due_date"] or "9999", d["id"]))

    debts = []
    for i, d in enumerate([*fixed_s, *_sort_cards(cards), *_sort_people(people, order)]):
        item = {k: v for k, v in d.items() if k not in ("paid_now", "debt_date", "period_end", "dues")}
        item["order"] = i + 1
        item["progress"] = round(d["paid"] / d["total"], 3) if d["total"] else 0.0
        item["due_label"] = _day_label(d["due_date"]) if d["due_date"] else None
        close = sim["closes"].get(d["key"]) if sim else None
        item["closes"] = {"month": close, "label": month_label(close)} if close else None
        if d["kind"] == "card":
            item["subtitle"] = ("Son ödeme günü geçti" if d["overdue"] else f"Son ödeme {item['due_label']}") \
                if d["due_date"] else "Kart ekstresi"
            item.update(_card_warning(d, fees_hidden))
            harm = _harm(d, sim, start, fees_hidden) if sim else None
            item["harm"] = harm
            if harm:
                item["warning"] = harm["text"]  # eski istemci de tek metin olarak gösterir
            if sim and sim["interest_by"].get(d["key"]) and not fees_hidden:
                item["projected_interest"] = sim["interest_by"][d["key"]]
        elif d["kind"] == "fixed":
            item["subtitle"] = " · ".join(x for x in (d["creditor_label"], d["schedule"]["text"]) if x)
            item["promise"] = None
        else:
            item["subtitle"] = d["note"] or "Elden borç"
            item["promise"] = _promise(d, close)
        debts.append(item)

    warnings = []
    for it in debts:
        if it.get("warning"):
            warnings.append({"key": f"faiz:{it['key']}", "level": "bad" if it["overdue"] else "warn",
                             "title": it["title"], "text": it["warning"]})
        if it.get("promise") and it["promise"].get("missed"):
            warnings.append({"key": f"soz:{it['key']}", "level": "info", "title": it["title"], "text": it["promise"]["text"]})
        if it["kind"] == "fixed" and it["overdue"] and it["schedule"].get("next"):
            nx = it["schedule"]["next"]
            warnings.append({"key": f"taksit:{it['key']}", "level": "warn", "title": it["title"],
                             "text": f"{nx['no']}. taksitin günü ({_day_label(nx['date'])}) geçti, ödenmemiş görünüyor. "
                                     "Ödediysen Ödedim'le işaretle."})

    # aylık tutar bu ayın taksitlerine yetmiyor: taksitler ödenmesi gereken tutarlardır (kalanı kartlara ve borçlara)
    fixed_short = None
    if sim and sim["months"] and sim["months"][0]["fixed_short"] > 0 and monthly:
        m0 = sim["months"][0]
        fixed_short = {"due": m0["fixed_due"], "short": m0["fixed_short"],
                       "title": "Aylık tutar bu ayın taksitlerine yetmiyor",
                       "text": f"Ödeme planı olan borçlarının bu ayki taksitleri {_tl(m0['fixed_due'])}. Aylık tutar "
                               "önce bunlara ayrılır; kalanı kartlara ve elden borçlara gider."}

    shortfall = None
    if sim and sim["interest"] > 0 and cards:
        shortfall = {
            "min_monthly": need, "interest": None if fees_hidden else sim["interest"],
            "title": "Bu tutarla kart faizi önlenemiyor",
            "text": (f"Aylık tutarı {_tl(need)} yaparsan kart borçların son ödeme günlerine kadar kapanır, faiz işlemez."
                     + ("" if fees_hidden else f" Bu tutarla kartlarda yaklaşık {_tl(sim['interest'])} faiz ve vergi işler.")),
            "hint": "Bu tutar bu ay mümkün değilse: son ödeme gününden önce en az asgari tutarı, elinden geldiğince "
                    "fazlasını yatır. Asgari gecikme faizini önler; üstüne yatırdığın her lira kalan borcun faizini "
                    "azaltır. Kumbaranda ya da hesabında birikmiş para varsa önce kart borcunu kapatmak faizden korur.",
            "allocation": "Tutar yetmediği ayda plan önce her kartın asgarisini son ödeme gününden önceye koyar; kalanı "
                          "faizi en yüksek karta gider, o kapanınca sıradakine. Kartlar bitince elden borçlara geçer.",
        }
    stuck = None
    if sim and sim["debt_free"] is None and monthly:
        stuck = {"title": "Bu tutarla 10 yılda kapanmıyor",
                 "text": "Aylık tutarı artırabilirsen plan kısalır; Bütçeler ve Tasarruf'ta kısılabilecek harcamalara bak."}

    # yalnız elden borç varken (kart yok: faiz uyarısı çıkmaz) nötr bir satır: bu tutarla elden borçlar kaç ayda biter
    pace = None
    if sim and people and not cards and monthly:
        closes_p = [sim["closes"].get(d["key"]) for d in people]
        if all(closes_p):
            n = _months_between(start, max(closes_p)) + 1
            pace = {"months": n, "text": "Bu tutarla elden borçlar bu ay biter." if n == 1 else
                    f"Bu tutarla elden borçlar {n} ayda biter."}

    total_open = _r(sum(d["remaining"] for d in (*fixed, *cards, *people)))
    by_key = {d["key"]: d for d in debts}
    # aynı kişinin borçları açılınca ayırt edilsin: not ya da borç tarihi ("3 Mart 2026 borcu")
    subs = {d["key"]: d["note"] or f"{_day_label(d['debt_date'])} {d['debt_date'][:4]} borcu"
            for d in (*people, *people_done) if d.get("debt_date")}
    items = []
    if sim and sim["months"] and sim["months"][0]["month"] == start:
        for p in sim["months"][0]["payments"]:
            d = by_key[p["key"]]
            full = money.ge(p["amount"], d["remaining"])
            harm = d.get("harm")
            asg = harm["min_left"] if harm and harm.get("min_left") else None
            if d["kind"] == "fixed":
                nx = d["schedule"].get("next") or {}
                due_m = d["schedule"]["due_now"]
                nos = d.get("due_nos") or []
                if len(nos) > 1:  # geçmişte ödenmemiş taksit de bu aya geldi
                    text = f"{nos[0]}–{nos[-1]}. taksit · gecikmiş taksit dahil"
                else:
                    text = (f"{nx['no']}. taksit" if nx else "Taksit") + (f" · son gün {d['due_label']}" if d["due_label"] else "")
                if due_m and p["amount"] + TOL < due_m:
                    text += f" · bu ayki taksit {_tl(due_m)}"
            else:
                text = (("Tamamını öde" if full else "Bu ay bu kadarını öde")
                        + (f" (asgari {_tl(asg)} dahil)" if asg and p["amount"] >= asg else "")
                        + (f" · son ödeme {d['due_label']}" if d["kind"] == "card" and d["due_date"] else ""))
            items.append({"key": p["key"], "kind": d["kind"], "title": d["title"], "amount": p["amount"],
                          "full": full, "done": False, "due_date": d["due_date"], "due_label": d["due_label"],
                          "min_left": asg, "text": text, "sub": subs.get(p["key"]), "payable": d["payable"]})
    for d in (*fixed, *cards, *people):
        if d["paid_now"] > 0:
            items.append({"key": d["key"], "kind": d["kind"], "title": d["title"], "amount": d["paid_now"], "full": False,
                          "done": True, "due_date": d["due_date"], "due_label": None, "sub": subs.get(d["key"]),
                          "text": f"Bu ay {_tl(d['paid_now'])} ödendi", "payable": d["payable"]})
    for d in (*cards_done, *people_done):
        items.append({"key": d["key"], "kind": d["kind"], "title": d["title"], "amount": d["paid"], "full": True,
                      "done": True, "due_date": None, "due_label": None, "sub": subs.get(d["key"]), "text": "Bu ay kapandı",
                      "payable": d["payable"]})

    df = sim["debt_free"] if sim else None
    debt_free = None
    if df and total_open > 0:
        n = _months_between(start, df) + 1
        debt_free = {"month": df, "label": month_label(df), "months": n,
                     "text": f"Bu planla {month_label(df)} sonunda borçsuzsun" + (f" ({n} ay)." if n > 1 else ".")}
        if shortfall:  # faiz işleyen plan başarı gibi okunmasın
            debt_free["interest"] = True
            debt_free["text"] += (" Bu tutarla kartlarda faiz işler." if fees_hidden else
                                  f" Bu tutarla kartlarda yaklaşık {_tl(sim['interest'])} faiz işler.")
        last_inst = max((d["future_installments"]["last_month"] for d in cards if d.get("future_installments")), default=None)
        if last_inst and last_inst > df:
            debt_free["installments_until"] = {"month": last_inst, "label": month_label(last_inst)}
            debt_free["text"] += f" Kart taksitlerinin son ayı: {month_label(last_inst)}."

    commit_raw = _store()
    commitment = _commitment(commit_raw["current"], cards, people, sim, monthly, order, today, people_hidden,
                             any(d.get("harm") for d in debts), fixed) if commit_raw["current"] else None
    offer = None
    if not commitment and (cards or people or fixed) and monthly:
        offer = {"title": "Bu planla başla",
                 "text": "Planı başlatırsan her ay plana göre nerede olduğunu gösteririz. Tutarı denemek planı "
                         "değiştirmez; değiştirmek istediğinde \"Bu planla devam\" dersin.",
                 "action": "Planı başlat"}
        if shortfall:  # faizli plan: ikincil seçenek, uyarıdan sonra (istemci düğmeyi ikincil gösterir)
            offer = {"title": "Bu tutar faizi önlemiyor",
                     "text": "Yine de bu planla başlamak istersen her ay plana göre nerede olduğunu gösteririz. "
                             f"Aylık tutarı {_tl(need)} yaparsan kartlarda faiz işlemez.",
                     "action": "Planı başlat", "secondary": True}
    history = [{"started_at": h["started_at"], "start_month": h["start_month"], "monthly": h["monthly"],
                "ended_at": h.get("ended_at"),
                "label": f"Önceki plan: {month_in(h['start_month'], start)} başladı · ayda {_tl(h['monthly'])}"}
               for h in commit_raw["history"]]

    return {
        "available": True,
        "today": today.isoformat(), "month": start, "month_label": month_label(start),
        "settings": saved, "monthly": monthly, "monthly_source": source, "suggestion": sug,
        "order": order, "orders": [{"key": k, "label": v[0], "text": v[1]} for k, v in ORDER_INFO.items()],
        "intro": "Borçların tek listede. Önce kart borçları kapanır: kartta borç kalmazsa faiz işlemez. Aylık tutar, "
                 "her zamanki harcamaların ve taksitlerin dışında borçlara ayırabileceğin para.",
        "totals": {"open": total_open, "cards": _r(sum(d["remaining"] for d in cards)),
                   "people": _r(sum(d["remaining"] for d in people)), "count": len(cards) + len(people) + len(fixed),
                   "card_count": len(cards), "person_count": len(people), "paid_this_month": paid_now,
                   # ödeme planı olan borçlar (yetenek debt_schedule): kalan toplam, sayı, bu ayki taksitler
                   "fixed": _r(sum(d["remaining"] for d in fixed)), "fixed_count": len(fixed),
                   "fixed_this_month": _r(sum(d["dues"].get(start, 0.0) for d in fixed)),
                   "min_monthly_no_interest": need,
                   "interest_projected": None if fees_hidden or not sim else sim["interest"]},
        "debts": debts,
        "months": sim["months"] if sim else [],
        "debt_free": debt_free,
        "this_month": {"month": start, "label": month_label(start), "budget": first if sim else None,
                       "planned": _r(sum(i["amount"] for i in items if not i["done"])), "paid": paid_now, "items": items,
                       "groups": group_items(items)},
        "rules_info": RULES_INFO,
        "warnings": warnings, "shortfall": shortfall, "fixed_short": fixed_short, "pace": pace, "stuck": stuck,
        "excluded": other,
        "hidden": {"fees": fees_hidden, "people": people_hidden},
        "can_edit": _can_edit(),
        "commitment": commitment, "commit_offer": offer, "commit_history": history, "faiz_info": FAIZ_INFO,
        "summary_card": {"eligible": len(cards) + len(people) + len(fixed) >= 2},
        "empty": None if cards or people or fixed else {
            "title": "Açık borcun görünmüyor",
            "text": "Kart ekstrelerin ödenmiş, Borç / Alacak'ta açık borcun yok. Elden bir borcun varsa Borç / Alacak'tan ekle."},
    }


RULES_INFO = (f"Elden borç ödemeleri kişi kişi toplanır ve ₺{ROUND_TO}'nin katlarına yuvarlanır. Bir kişiye bir ayda "
              f"en az ₺{MIN_PAY} ödenir (borcun kalanı daha azsa kalanın tamamı). Son ödemede ₺{FOLD}'den az kalan "
              "bırakılmaz, bir önceki ödemeye eklenir. \"Faizi önle\"de paylar çok küçük kalırsa önce küçük borçlar "
              "bütün olarak kapanır: daha az ama daha büyük ödeme. Kart borcu ve taksitler tam tutarıyla yazılır. "
              "Ödeme planı olan borçların taksiti her ay önce ayrılır; kalan para kartlara, sonra elden borçlara gider.")


def group_items(items: list[dict]) -> list[dict]:
    """"Bu ay ödenecekler" kişi kişi: aynı kişinin elden borçları tek satır (açınca borç borç). Kart ve taksit tek
    başına. {key, kind, title, count, amount, done, full, text, due_label, items, payable (tek borçta)}."""
    out: list[dict] = []
    by: dict[tuple, dict] = {}
    for it in items:
        if it["kind"] != "person":
            out.append({**it, "count": 1, "items": [it]})
            continue
        gk = (person_key(it["title"]), it["done"])
        g = by.get(gk)
        if g is None:
            g = by[gk] = {"key": f"kisi:{gk[0]}:{'d' if it['done'] else 'p'}", "kind": "person", "title": it["title"],
                          "done": it["done"], "items": []}
            out.append(g)
        g["items"].append(it)
    for g in by.values():
        xs = g["items"]
        n = len(xs)
        g["count"] = n
        g["amount"] = _r(sum(x["amount"] for x in xs))
        g["full"] = all(x["full"] for x in xs)
        g["due_label"] = min((x["due_label"] for x in xs if x.get("due_label")), default=None)
        g["payable"] = xs[0]["payable"] if n == 1 else None
        if n == 1:
            g["text"] = xs[0]["text"]
        elif g["done"]:
            g["text"] = f"{n} borç · bu ay {_tl(g['amount'])} ödendi"
        else:
            closing = sum(1 for x in xs if x["full"])
            g["text"] = f"{n} borç · " + ("hepsi bu ay kapanır" if closing == n else
                                          f"{closing} tanesi bu ay kapanır" if closing else "bu ay bu kadarını öde")
    return out


def _card_warning(d: dict, fees_hidden: bool) -> dict:
    """Kartın bilgi uyarısı: yalnız asgariyi ödersen ne kadar faiz işler (seçenek değil, uyarı). rate_note: hesabın
    dayanağı (ekstredeki oran ya da genel varsayım)."""
    rate = d["rate"]["monthly"]
    pct = f"%{rate * 100:.2f}".replace(".", ",")
    note = (f"Hesap: ekstrendeki aylık {pct} faiz oranı ve vergiler (BSMV, KKDF)." if d["rate"]["source"] == "ekstre"
            else f"Hesap yaklaşık: aylık {pct} faiz ve vergiler (BSMV, KKDF); bankanın oranı ekstrende yazar.")
    if d["overdue"]:
        return {"interest_if_min": None, "rate_note": None, "warning":
                "Son ödeme günü geçti; kalan borca gecikme faizi işliyor. Tamamını bir an önce ödemek faizi durdurur."}
    if fees_hidden:
        return {"interest_if_min": None, "rate_note": None, "warning":
                "Tamamı son ödeme gününe kadar ödenmezse kalan borca faiz işler."}
    min_left = max(0.0, (d["min_payment"] or 0) - d["paid_now"]) if d["min_payment"] else None
    carried = d["remaining"] - min_left if min_left is not None else d["remaining"]
    if carried <= 0:
        return {"interest_if_min": 0.0, "rate_note": None, "warning": None}
    x = _r(carried * _eff(rate))
    if min_left is not None:
        text = f"Bu kartta yalnız asgariyi ödersen bu ay yaklaşık {_tl(x)} faiz işler. Tamamını öde, faiz işlemesin."
    else:
        text = f"Tamamı ödenmezse kalan borca bu ay yaklaşık {_tl(x)} faiz işler. Tamamını öde, faiz işlemesin."
    return {"interest_if_min": x, "rate_note": note, "warning": text}


def _paid_now(cards, people, cards_done, people_done) -> float:
    return _r(sum(d["paid_now"] for d in (*cards, *people)) + sum(d["paid"] for d in (*cards_done, *people_done)))


def _whole(x: float) -> float:
    return float(math.floor(max(0.0, x) + 0.5))


def _harm(d: dict, sim: dict, start: str, fees_hidden: bool) -> dict | None:
    """Zararı azaltma: plandaki tutar kartın tamamına son ödeme gününe kadar yetmiyorsa ne yapılır (yalnız bu durumda).
    Son ödeme günü geçmiş kartta yok (orada uyarı: tamamını bir an önce öde)."""
    if d["overdue"] or not d["due_date"]:
        return None
    due_m = d["due_date"][:7]
    if due_m < start:
        return None
    planned = _r(sum(p["amount"] for mo in sim["months"] if mo["month"] <= due_m for p in mo["payments"]
                     if p["key"] == d["key"]))
    rem = d["remaining"]
    if money.ge(planned, rem):
        return None
    eff = _eff(d["rate"]["monthly"])
    mp = d.get("min_payment")
    min_left = _r(min(rem, max(0.0, mp - (d["paid"] or 0.0)))) if mp else None
    label = _day_label(d["due_date"])
    when = "Bu ay" if due_m == start else f"{AYLAR[int(due_m[5:]) - 1]} ayında"
    title = f"{when} tamamını kapatamıyorsun"
    if min_left is None:
        s1 = f"Son ödeme gününden ({label}) önce en az asgari tutarı (ekstrende yazar) yatır — gecikme faizi işlemez."
    elif min_left <= 0:
        s1 = "Asgari tutarı yatırmışsın; gecikme faizi işlemez."
    else:
        s1 = f"Son ödeme gününden ({label}) önce en az asgari tutarı ({_tl(min_left)}) yatır — gecikme faizi işlemez."
    below = min_left is not None and planned < min_left
    y = _whole((rem - planned) * eff)  # yaklaşık: tam liraya
    if fees_hidden:
        s2 = "Asgarinin üstüne ne kadar yatırırsan faiz o kadar azalır; tamamını ödersen faiz işlemez."
    elif below:
        s2 = (f"Planladığın {_tl(planned)} asgarinin altında kalıyor; asgariyi tamamlayabilirsen gecikme faizi "
              f"işlemez. Asgarinin üstüne ne kadar yatırırsan faiz o kadar azalır (tamamını ödersen ₺0).")
    else:
        s2 = (f"Asgarinin üstüne ne kadar yatırırsan faiz o kadar azalır: planladığın {_tl(planned)} ile bu ay "
              f"yaklaşık {_tl(y)} faiz işler (tamamını ödersen ₺0).")
    compare = None
    if not fees_hidden:
        # sıra: Tamamı (₺0 faiz) önce, yalnız asgari en son: göz önce faizsiz yola gitsin
        compare = [{"key": "full", "label": "Tamamı", "amount": rem, "interest": 0.0, "late": False},
                   {"key": "plan", "label": "Planladığın", "amount": planned, "interest": y, "late": below}]
        if min_left is not None and 0 < min_left < rem:  # asgari borcun tamamıysa "Tamamı" ile aynı satır
            compare.append({"key": "asgari", "label": "Yalnız asgari", "amount": min_left,
                            "interest": _whole((rem - min_left) * eff), "late": False})
    return {"title": title, "steps": [s1, s2], "text": f"{title}. Faizi en aza indirmek için: 1) {s1} 2) {s2}",
            "min_left": min_left, "planned": planned, "interest_planned": None if fees_hidden else y,
            "below_min": below, "due_label": label, "compare": compare, "info": FAIZ_INFO}


# ---------------------------------------------------------------- plan başlat (anlık görüntü)

def _store() -> dict:
    try:
        raw = json.loads(db.get_setting(COMMIT) or "{}")
    except ValueError:
        raw = {}
    cur = raw.get("current") if isinstance(raw.get("current"), dict) else None
    hist = [h for h in raw.get("history") or [] if isinstance(h, dict)]
    return {"current": cur, "history": hist}


def _ident(d: dict) -> str:
    return f"card:{d['card_no']}" if d["kind"] == "card" else f"debt:{d['id']}"


def commit_plan(monthly: float | None = None, order: str | None = None, today: date | None = None) -> dict:
    """Bugünkü borçlar ve tutarla planı kaydet (eskisi geçmişe). Aylık tutar ve sıralama ayar olarak da kaydedilir."""
    today = today or date.today()
    start = _ym(today)
    saved = settings()
    order = order if order in ORDERS else saved["order"]
    if monthly is None:
        monthly = saved["monthly"]
        if monthly is None:
            sug = suggestion(today)
            monthly = sug["amount"] if sug else None
    if not monthly or monthly <= 0:
        raise ValueError("Önce her ay borçlara ayırabileceğin tutarı yaz.")
    monthly = _r(monthly)
    cards, cards_done = _cards(today)
    people, _, people_done, fixed = _people(today)
    if not cards and not people and not fixed:
        raise ValueError("Açık borcun görünmüyor; başlatılacak bir plan yok.")
    paid_now = _paid_now(cards, [*people, *fixed], cards_done, people_done)
    sim = simulate(cards, people, monthly, order, start, first_budget=_r(max(0.0, monthly - paid_now)), fixed=fixed)
    people = [*fixed, *people]
    ids = {d["key"]: _ident(d) for d in (*cards, *people)}
    planned: dict[str, float] = {}
    months = []
    for mo in sim["months"]:
        pays = {}
        for p in mo["payments"]:
            pays[ids[p["key"]]] = _r(pays.get(ids[p["key"]], 0.0) + p["amount"])
            planned[ids[p["key"]]] = _r(planned.get(ids[p["key"]], 0.0) + p["amount"])
        months.append({"month": mo["month"], "total": mo["total"], "pays": pays})
    snap = {"started_at": today.isoformat(), "start_month": start, "monthly": monthly, "order": order,
            "debt_free": sim["debt_free"], "total": _r(sum(d["remaining"] for d in (*cards, *people))),
            "debts": [{"ident": ids[d["key"]], "kind": d["kind"], "title": d["title"], "remaining": d["remaining"],
                       "planned": planned.get(ids[d["key"]], 0.0), "paid0": d["paid"],
                       "stmt_id": d["id"] if d["kind"] == "card" else None, "period_end": d.get("period_end")}
                      for d in (*cards, *people)],
            "months": months}
    store = _store()
    if store["current"]:
        old = store["current"]
        store["history"] = [{"started_at": old["started_at"], "start_month": old["start_month"],
                             "monthly": old["monthly"], "order": old["order"], "ended_at": today.isoformat()},
                            *store["history"]][:HISTORY]
    store["current"] = snap
    db.set_setting(COMMIT, json.dumps(store))
    save_settings({"monthly": monthly, "order": order})
    return build(today=today)


def _credited(snap: dict, people_hidden: bool) -> dict[str, float]:
    """Plan başladıktan sonra plandaki her borca ödenen (borcun plandaki toplam hedefiyle sınırlı). Kartta: başlangıç
    ekstresine o günden sonra ödenen + aynı kartın sonraki ekstrelerine ödenen. Silinen / vazgeçilen borç tamam sayılır."""
    stmts = db.list_statements()
    by_id = {s["id"]: s for s in stmts}
    debts = {d["id"]: d for d in db.list_debts()} if not people_hidden else {}
    out: dict[str, float] = {}
    for x in snap["debts"]:
        if x["kind"] == "card":
            card = x["ident"][5:]
            s0 = by_id.get(x["stmt_id"])
            if s0 is None:
                got = x["planned"]
            else:
                got = max(0.0, (s0.get("paid_amount") or 0.0) - (x["paid0"] or 0.0))
                got += sum(s.get("paid_amount") or 0.0 for s in stmts
                           if s["card_no"] == card and (s["period_end"] or "") > (x["period_end"] or ""))
        else:
            if people_hidden:
                continue
            d = debts.get(int(x["ident"][5:]))
            got = x["planned"] if d is None or d["written_off_at"] else max(0.0, (d["paid_amount"] or 0.0) - (x["paid0"] or 0.0))
        out[x["ident"]] = _r(min(got, x["planned"]))
    return out


def _commitment(snap: dict, cards: list[dict], people: list[dict], sim: dict | None, monthly: float | None, order: str,
                today: date, people_hidden: bool, harm_now: bool, fixed: list[dict] | None = None) -> dict:
    """Kaydedilen plana göre bu ay nerede olduğun. Sözler yalnız daha çok ayırmayı, harcamayı kısmayı ve (kartta tutar
    yetmiyorsa) son ödeme gününden önce en az asgariyi önerir; başka yol önermez."""
    start = _ym(today)
    skip = (lambda i: i.startswith("debt:")) if people_hidden else (lambda i: False)
    credited = _credited(snap, people_hidden)
    got = _r(sum(credited.values()))
    before = _r(sum(a for mo in snap["months"] if mo["month"] < start for i, a in mo["pays"].items() if not skip(i)))
    target = _r(sum(a for mo in snap["months"] if mo["month"] == start for i, a in mo["pays"].items() if not skip(i)))
    expected = _r(before + target)
    fixed = fixed or []
    total_open = _r(sum(d["remaining"] for d in (*cards, *people, *fixed)))
    df = snap.get("debt_free")
    # aynı tutar ve sıralamayla bugünkü borçlardan hesaplanan plan (önizleme farklıysa ayrı hesaplanır)
    differs = monthly != snap["monthly"] or order != snap["order"]
    live = sim
    if differs or live is None:
        cards_now, people_now = cards, people
        paid_now = _r(sum(d["paid_now"] for d in (*cards_now, *people_now, *fixed)))
        live = simulate(cards_now, people_now, snap["monthly"], snap["order"], start,
                        first_budget=_r(max(0.0, snap["monthly"] - paid_now)), fixed=fixed)
    behind = _r(before - got)
    if total_open <= 0:
        status = {"key": "done", "title": "Plan tamamlandı ✓", "text": "Borçların kapandı. Ellerine sağlık."}
    elif behind > TOL:
        need = _r(expected - got)
        text = (f"Bu ay {_tl(need)} ödersen yetişirsin. Biraz daha "
                "ayırabilir ya da Bütçeler'de kısılabilecek harcamalara bakabilirsin.")
        if harm_now:
            text += " Kartta tamamı olmuyorsa son ödeme gününden önce en az asgari tutarı yatır; gecikme faizi işlemez."
        status = {"key": "behind", "title": f"Planın {_tl(behind)} gerisindesin", "text": text, "need": need}
    elif got > expected + TOL:
        ldf = live.get("debt_free")
        if ldf and df and ldf < df:
            text = f"{AYLAR[int(df[5:]) - 1]}{' ' + df[:4] if df[:4] != start[:4] else ''} yerine " \
                   f"{month_in(ldf, start)} bitebilir."
        else:
            text = f"Plandan {_tl(_r(got - expected))} fazla ödedin. Böyle devam."
        status = {"key": "ahead", "title": "Planın önündesin", "text": text}
    else:
        left = _r(max(0.0, expected - got))
        status = {"key": "ok", "title": "Plana uygun ✓",
                  "text": f"Bu ay planda {_tl(left)} daha var." if left > TOL else "Bu ayın ödemesi tamam."}
    reasons = []
    known = {x["ident"] for x in snap["debts"]}
    new = [d["title"] for d in (*cards, *people, *fixed) if _ident(d) not in known]
    if new:
        reasons.append({"key": "new_debt", "text": f"Yeni borç eklendi: {', '.join(new)}"})
    if behind > TOL:
        prev = max((mo["month"] for mo in snap["months"] if mo["month"] < start and mo["total"] > 0), default=None)
        reasons.append({"key": "missed", "text": (month_in(prev, start) if prev else "Geçen ay") +
                        " plandaki ödeme eksik kaldı"})
    changed = {"title": "Plan değişti", "reasons": reasons, "text": " · ".join(r["text"] for r in reasons),
               "action": "Planı yeniden oluştur",
               "confirm": "Plan bugünkü borçlarınla baştan kurulur. Şimdiki plan \"Önceki plan\" olarak kalır."} \
        if reasons else None
    k = _months_between(snap["start_month"], start)
    return {"started_at": snap["started_at"], "start_month": snap["start_month"],
            "start_label": month_label(snap["start_month"]), "monthly": snap["monthly"], "order": snap["order"],
            "order_label": ORDER_INFO[snap["order"]][0],
            "debt_free": {"month": df, "label": month_label(df)} if df else None,
            "month_no": k + 1, "month_count": len(snap["months"]), "expected": expected, "paid": got,
            "this_target": target, "status": status, "changed": changed, "differs": differs,
            "text": f"{month_in(snap['start_month'], start)} başladı · ayda {_tl(snap['monthly'])} · "
                    f"{ORDER_INFO[snap['order']][0]}",
            "continue": {"action": "Bu planla devam",
                         "confirm": "Plan bu tutar ve sıralamayla yeniden kurulur. Şimdiki plan \"Önceki plan\" olarak kalır."}}


def _promise(d: dict, close: str | None) -> dict | None:
    if not d["due_date"]:
        return None
    label = _day_label(d["due_date"])
    if d["overdue"]:
        return {"date": d["due_date"], "label": label, "missed": True,
                "text": f"{label} için söz verdiğin tarih geçti. Kendisine haber verip yeni bir tarih konuşmak iyi olur."}
    if close and close > d["due_date"][:7]:
        return {"date": d["due_date"], "label": label, "missed": True,
                "text": f"{label} için söz verdin; bu planla {month_label(close)} ayında kapanıyor. Önceden haber "
                        "vermek ya da sıralamayı \"Söz verdiğim tarihe göre\" yapmak iyi olur."}
    return {"date": d["due_date"], "label": label, "missed": False, "text": f"Söz verdiğin tarih: {label}"}


def _can_edit() -> bool:
    from app import book

    m = book.member()
    return not (m and book.role(m) == "viewer")

