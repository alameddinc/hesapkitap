"""Tanınmayan bankalar için yedek: ekstreyi Gemini ile oku.

Sonuç diğer parser'larla aynı mutabakat kontrolünden geçer (Statement.reconciliation_diff):
model işlemlerle birlikte ekstrenin özet toplamlarını da çıkarır, ikisi tutmazsa arayüz "kontrol et" uyarısı gösterir.
"""
from datetime import date

from app import receipt
from app.parsers.base import FEE, PAYMENT, PURCHASE, REFUND, Statement, Transaction

PROMPT = """Bu bir Türkiye'de kesilmiş KREDİ KARTI HESAP ÖZETİ (ekstre). Tüm sayfaları oku ve SADECE şu JSON'u döndür:
{
  "bank": "banka adı (ör. Garanti BBVA, İş Bankası, Yapı Kredi, Akbank, Ziraat)",
  "card_no": "kart numarası maskeli, ilk 4 ve son 4 hane (ör. 5400********1234)",
  "period_end": "YYYY-MM-DD hesap kesim tarihi",
  "due_date": "YYYY-MM-DD son ödeme tarihi",
  "next_period_end": "YYYY-MM-DD veya null",
  "next_due_date": "YYYY-MM-DD veya null",
  "total_debt": 0.00,
  "min_payment": 0.00,
  "credit_limit": 0.00,
  "previous_balance": 0.00,
  "payments": 0.00,
  "period_activity": 0.00,
  "transactions": [
    {"date": "YYYY-MM-DD", "description": "açıklama aynen", "amount": 0.00,
     "type": "harcama | iade | odeme | faiz_ucret",
     "card": "işlemin yapıldığı kart (ek kart ise onun maskeli no'su) veya null",
     "installment_no": null, "installments_total": null,
     "orig_amount": null, "orig_currency": null}
  ]
}
Kurallar:
- amount: BU EKSTREYE YANSIYAN tutar, pozitif. Taksitli işlemde toplam değil bu ayki taksit tutarı.
- type: kart borcu ödemesi = odeme; iade/iptal = iade; faiz, gecikme faizi, kâr payı, vade farkı, BSMV, KKDF, kart aidatı/ücret = faiz_ucret.
- "Önceki dönem bakiyesi/devreden bakiye" satırını işlem olarak YAZMA.
- period_activity: ekstrenin özetindeki dönem içi harcama/işlem toplamı (ödemeler hariç, faiz ve ücretler DAHİL).
- Dövizli işlemde orig_amount/orig_currency (USD, EUR) doldur, amount TL karşılığı.
- Türk sayı formatı: 1.234,56 = 1234.56. Hiçbir işlemi atlama."""


def _d(v) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except (ValueError, TypeError):
        return None


def _mask(card: str | None, fallback: str) -> str:
    if not card:
        return fallback
    digits = "".join(ch for ch in card if ch.isdigit())
    return f"{digits[:4]}***{digits[-4:]}" if len(digits) >= 8 else card


def parse_pdf_with_ai(data: bytes) -> Statement:
    raw, _ = receipt._gemini_json(PROMPT, data, "application/pdf", max_output_tokens=32000)
    main = _mask(raw.get("card_no"), "AI***0000")
    period_end = _d(raw.get("period_end"))
    if not period_end:
        raise ValueError("Yapay zekâ hesap kesim tarihini okuyamadı")
    num = receipt._num
    st = Statement(
        bank=f"{(raw.get('bank') or 'Banka').strip()}",
        card_no=main,
        period_end=period_end,
        due_date=_d(raw.get("due_date")),
        total_debt=num(raw.get("total_debt")) or 0.0,
        min_payment=num(raw.get("min_payment")),
        credit_limit=num(raw.get("credit_limit")),
        previous_balance=num(raw.get("previous_balance")) or 0.0,
        period_activity=num(raw.get("period_activity")) or 0.0,
        interest_fees=0.0,  # faizler period_activity içinde istendi
        payments=num(raw.get("payments")) or 0.0,
        next_period_end=_d(raw.get("next_period_end")),
        next_due_date=_d(raw.get("next_due_date")),
    )
    for t in raw.get("transactions") or []:
        d, amt = _d(t.get("date")), num(t.get("amount"))
        if not d or amt is None:
            continue
        kind = t.get("type") if t.get("type") in (PURCHASE, REFUND, PAYMENT, FEE) else PURCHASE
        amt = abs(amt)
        n, total = t.get("installment_no"), t.get("installments_total")
        left = (int(total) - int(n)) if isinstance(n, int) and isinstance(total, int) and total >= n else None
        st.transactions.append(Transaction(
            txn_date=d, description=str(t.get("description") or "").strip()[:120] or "?",
            amount=-amt if kind in (REFUND, PAYMENT) else amt, txn_type=kind,
            card=_mask(t.get("card"), main), card_holder="",
            orig_amount=num(t.get("orig_amount")), orig_currency=t.get("orig_currency") or None,
            installment_no=n if isinstance(n, int) else None, installments_left=left,
            installment_left_amount=amt if left else None,
            note="Yapay zekâ ile okundu",
        ))
    return st
