"""Ödeme planı olan borç (Borç / Alacak "borcum", TL): başka yerde anlaşılmış sabit taksitler (ör. vergi/SGK, banka ya
da bir kişiyle "ayda ₺2.000, 6 ay"). Uygulama bu planı yalnız KAYDEDER; hiçbir metin böyle bir plan yapmayı önermez.

Kayıt (debts sütunları): plan_installment (aylık taksit), plan_count (taksit sayısı), plan_start (ilk taksit ayı
YYYY-MM), plan_day (ayın kaçında; ay kısaysa ayın son günü), creditor_type (vergi_sgk | banka | kisi | diger),
plan_interest (yalnız bilgi: yok | var | bilmiyorum). amount = toplam (verilmezse taksit × sayı; verilirse son taksit
farkı alır). Ödemeler her zamanki borç ödemesidir (POST /api/debts/{id}/pay): ödenen toplam taksitlere sırayla düşer.

Plan borcun alt kaydıdır: borç elden borç olarak girilip plan sonradan eklenebilir, düzenlenebilir, kaldırılabilir (PUT /
DELETE /api/debts/{id}/schedule). İlk taksit ayından ÖNCE yapılmış ödemeler (paid_at < plan_start-01) plana girmez:
taksitler "kalan" (amount - plan öncesi ödenen) üzerinden kurulur, ödenen taksitler plan başlangıcından sonraki
ödemelerle eşleşir. Böylece "ödeme planlı oluştur" ile "diğer borç oluştur + sonradan plan ekle" aynı planı verir.

Borcun koşulları (yalnız "borcum"; borç planı bunlara göre sıralar, app/debt_plan.py). Sütun boşsa eski davranış:
  creditor_type  kime: kisi | vergi_sgk | banka | magaza ("Mağaza / senet") | diger (kurum). Planı olmayan borçta da
                 durur; plan kaldırılınca silinmez.
  pay_terms      ödeme koşulu: taksitli (= ödeme planı var) | parca (parça parça ödenebilir) | tek (tek seferde
                 ödenecek) | bilinmiyor (parca gibi). Boşsa: planlıysa taksitli, değilse bilinmiyor.
  due_strict     tarih türü: 1 kesin (tarihinde ödenmesi şart; gecikirse bedeli/yaptırımı var) | 0 söz (esnek). Boşsa:
                 planlı borçta ve kurumda (vergi_sgk, banka, magaza, diger) kesin, kişide ve türü boş borçta söz.
"""
import calendar
from datetime import date

from app import money

CREDITORS = {"vergi_sgk": "Vergi / SGK", "banka": "Banka", "magaza": "Mağaza / senet", "kisi": "Kişi",
             "diger": "Diğer"}
KURUM = ("vergi_sgk", "banka", "magaza", "diger")  # kurum türleri: tarih varsayılanı kesin
TERMS = {"taksitli": "Taksitli (ödeme planı)", "parca": "Parça parça ödenebilir", "tek": "Tek seferde ödenecek",
         "bilinmiyor": "Bilmiyorum"}
INTEREST = {"yok": "Faiz / gecikme zammı yok", "var": "Faiz ya da gecikme zammı var",
            "bilmiyorum": "Faiz / gecikme zammı bilinmiyor"}
MAX_COUNT = 240  # en çok 20 yıl
AYLAR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def is_scheduled(d: dict) -> bool:
    return bool(d.get("plan_installment")) and bool(d.get("plan_count")) and bool(d.get("plan_start"))


def terms_of(d: dict) -> str:
    """Geçerli ödeme koşulu: planlıysa taksitli; değilse kayıtlı parca/tek; boş ya da bilinmiyor → bilinmiyor (plan
    parça parça ödenebilir gibi davranır). Planı kaldırılmış "taksitli" kaydı bilinmiyor sayılır."""
    if is_scheduled(d):
        return "taksitli"
    t = d.get("pay_terms")
    return t if t in ("parca", "tek") else "bilinmiyor"


def strict_of(d: dict) -> bool:
    """Tarih kesin mi: kayıtlıysa o; boşsa planlı borçta ve kurumda kesin, kişide (ve türü boş eski kayıtta) söz."""
    v = d.get("due_strict")
    if v is not None:
        return bool(v)
    return is_scheduled(d) or d.get("creditor_type") in KURUM


def paid_before(payments: list[dict], start_month: str | None) -> float:
    """İlk taksit ayından önce yapılmış ödemeler (plana girmez; taksitler kalan üzerinden kurulur)."""
    if not start_month:
        return 0.0
    first = f"{start_month}-01"
    return money.money(sum(p["amount"] or 0 for p in payments if (p.get("paid_at") or "")[:10] < first))


def base(d: dict) -> float:
    """Planın kapsadığı tutar: borç toplamı eksi plan öncesi ödenen."""
    return money.money(max(0.0, d["amount"] - (d.get("plan_paid_before") or 0)))


def plan_paid(d: dict) -> float:
    """Plana sayılan ödenen (plan başlangıcından sonraki ödemeler)."""
    return money.money(max(0.0, (d.get("paid_amount") or 0) - (d.get("plan_paid_before") or 0)))


def suggest(remaining: float, count: int) -> float:
    """Kalan ÷ sayı, tam liraya yuvarlı (son taksit farkı alır)."""
    return float(round(money.money(remaining) / count)) if count > 0 and remaining > 0 else 0.0


def _add(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def months_between(a: str, b: str) -> int:
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


def day_in(month: str, day: int) -> str:
    y, m = map(int, month.split("-"))
    return date(y, m, min(max(1, day), calendar.monthrange(y, m)[1])).isoformat()


def count_from(installment: float, total: float | None = None, end_month: str | None = None,
               start_month: str | None = None) -> int | None:
    """Taksit sayısı verilmediyse: bitiş ayından ya da toplamdan (toplam / taksit, yukarı)."""
    if end_month and start_month:
        n = months_between(start_month, end_month) + 1
        return n if n > 0 else None
    if total and installment > 0:
        n = int(-(-money.money(total) // money.money(installment)))
        return n if n > 0 else None
    return None


def validate(installment: float, count: int, total: float | None) -> str | None:
    """Türkçe hata (None: geçerli)."""
    if not installment or installment <= 0:
        return "Aylık taksit tutarını yaz"
    if not count or count < 1 or count > MAX_COUNT:
        return f"Taksit sayısı 1 ile {MAX_COUNT} arasında olmalı"
    if total is not None:
        if money.le(total, installment * (count - 1)):
            return "Toplam, taksitlerden az; taksit sayısını ya da toplamı düzelt"
        if money.gt(total, installment * count * 2):
            return "Toplam taksitlerin iki katından fazla; tutarları kontrol et"
    return None


def installments(d: dict) -> list[dict]:
    """[{no, month, date, amount}] (toplam: base(d), yani d["amount"] eksi plan öncesi ödenen; son taksit farkı alır)."""
    inst = money.money(d["plan_installment"])
    n = int(d["plan_count"])
    total = base(d)
    out, given = [], 0.0
    for i in range(n):
        amt = min(inst, money.money(total - given)) if i < n - 1 else money.money(total - given)  # kalan aşılmaz
        if amt <= 0:
            break
        m = _add(d["plan_start"], i)
        out.append({"no": i + 1, "month": m, "date": day_in(m, d.get("plan_day") or 1), "amount": amt})
        given = money.money(given + amt)
    return out


def due_through(d: dict, month: str) -> float:
    """O aya kadar (o ay dahil) vadesi gelen taksitlerin toplamı."""
    return money.money(sum(i["amount"] for i in installments(d) if i["month"] <= month))


def info(d: dict, today: date) -> dict:
    """İstemcinin göstereceği durum: sıradaki taksit, bu ay ödenecek (geçmiş eksik dahil), gecikme."""
    rows = installments(d)
    paid = plan_paid(d)
    month = today.isoformat()[:7]
    cum, nxt, paid_count = 0.0, None, 0
    for r in rows:
        cum = money.money(cum + r["amount"])
        if money.ge(paid, cum):
            paid_count += 1
        elif nxt is None:
            nxt = {**r, "left": money.money(min(r["amount"], cum - paid))}
    due_now = 0.0 if d.get("settled_at") or d.get("written_off_at") else \
        money.money(max(0.0, min(due_through(d, month) - paid, base(d) - paid)))
    overdue = bool(nxt and nxt["date"] < today.isoformat())
    last = rows[-1] if rows else None
    ctype = d.get("creditor_type") or "diger"
    return {
        "installment": money.money(d["plan_installment"]), "count": len(rows) or int(d["plan_count"]),
        "start_month": d["plan_start"],
        "day": int(d.get("plan_day") or 1), "end_month": last["month"] if last else d["plan_start"],
        "creditor_type": ctype, "creditor_label": CREDITORS.get(ctype, CREDITORS["diger"]),
        "interest": d.get("plan_interest"), "interest_label": INTEREST.get(d.get("plan_interest") or ""),
        "paid_count": paid_count, "left_count": len(rows) - paid_count, "next": nxt, "due_now": due_now,
        "overdue": overdue, "total": base(d), "paid_before": money.money(d.get("plan_paid_before") or 0),
        "last_amount": last["amount"] if last else 0.0,
        "text": summary(d, rows),
    }


def summary(d: dict, rows: list[dict] | None = None) -> str:
    """"Ayda ₺2.000 · 6 taksit · her ayın 15'i · Mart 2027'de biter"."""
    rows = rows if rows is not None else installments(d)
    last = rows[-1]["month"] if rows else d["plan_start"]
    y, m = last.split("-")
    return (f"Ayda {money.tl(d['plan_installment'], trim=True)} · {len(rows) or int(d['plan_count'])} taksit · her ayın "
            f"{int(d.get('plan_day') or 1)}'{_ek(int(d.get('plan_day') or 1))} · son taksit {AYLAR[int(m) - 1]} {y}")


def _ek(n: int) -> str:
    """Gün ekinin ünlüsü (1'i, 2'si, 3'ü, 6'sı, 9'u, 10'u, 15'i, 20'si, 30'u)."""
    last = n % 10
    if last == 0:
        tens = n // 10 % 10
        return {1: "u", 2: "si", 3: "u"}.get(tens, "u")
    return {1: "i", 2: "si", 3: "ü", 4: "ü", 5: "i", 6: "sı", 7: "si", 8: "i", 9: "u"}[last]
