from dataclasses import dataclass, field
from datetime import date


# İşlem tipleri
PURCHASE = "harcama"
REFUND = "iade"
PAYMENT = "odeme"
FEE = "faiz_ucret"


@dataclass
class Transaction:
    txn_date: date
    description: str
    amount: float  # TL; harcama pozitif, iade/ödeme negatif
    txn_type: str
    card: str  # maskeli: '4543***1234'
    card_holder: str = ""
    orig_amount: float | None = None
    orig_currency: str | None = None
    installment_no: int | None = None
    installments_left: int | None = None
    installment_left_amount: float | None = None  # kalan taksit başına tutar
    points: int | None = None
    note: str | None = None


@dataclass
class Statement:
    bank: str
    card_no: str  # ana kart, maskeli
    period_end: date  # hesap kesim tarihi
    due_date: date | None
    total_debt: float
    min_payment: float | None
    credit_limit: float | None
    previous_balance: float = 0.0
    period_activity: float = 0.0  # "Dönem içi işlemler"
    interest_fees: float = 0.0
    payments: float = 0.0
    next_period_end: date | None = None
    next_due_date: date | None = None
    transactions: list[Transaction] = field(default_factory=list)
    ai_parsed: bool = False  # bankaya özel parser yerine yapay zekâ ile okundu

    def fees(self) -> float:
        """Ekstredeki faiz ve ücret: özet satırı okunduysa o, okunmadıysa faiz/ücret işlemlerinin toplamı."""
        return statement_fees(self.interest_fees, sum(t.amount for t in self.transactions if t.txn_type == FEE))

    def reconciliation_diff(self) -> float:
        """Parse edilen işlemler toplamı ile ekstre özetinin farkı. 0 değilse satır kaçmış demektir."""
        parsed = sum(t.amount for t in self.transactions if t.txn_type != PAYMENT)
        return reconciliation_diff(parsed, self.period_activity, self.interest_fees, self.fees(),
                                   self.previous_balance, self.payments, self.total_debt)


def statement_fees(interest_fees: float | None, fee_txn_sum: float) -> float:
    return round(interest_fees or 0.0, 2) if interest_fees else round(max(fee_txn_sum, 0.0), 2)


def reconciliation_diff(parsed: float, activity: float | None, interest_fees: float | None, fees: float,
                        previous: float | None, payments: float | None, total: float | None) -> float:
    """Okunan işlemler (ödeme hariç, faiz/ücret dahil) − ekstre özeti. Bazı ekstrelerde "Dönem içi işlemler" faizi
    içerir, bazılarında faiz ayrı satırdır (VakıfBank: önceki borç − ödemeler + harcamalar + faiz ve ücret = yeni borç).
    Özetteki faiz satırı okunamadıysa (interest_fees 0) hangisi olduğu denklemden anlaşılır."""
    activity = activity or 0.0
    extra = interest_fees or 0.0
    if not extra and fees:
        inc = equation(previous, payments, activity, fees, total)["fees_included"]
        extra = 0.0 if inc in (True, None) else fees
    return round(parsed - (activity + extra), 2)


def equation(previous: float | None, payments: float | None, activity: float | None, fees: float | None,
             total: float | None) -> dict:
    """Ekstre denklemi: önceki − ödemeler + harcamalar (+ faiz ve ücret) = yeni borç. Faiz bazı ekstrelerde "dönem içi
    işlemler"in içindedir, bazılarında ayrı satırdır: hangi biçim denklemi tutturuyorsa o seçilir; ikisi de tutmuyorsa
    farkı küçük olan. {fees_included: bool|None (faiz yok/hesaplanamaz), diff: yeni borç − hesap, ok}."""
    if None in (previous, payments, activity, total):
        return {"fees_included": None, "diff": None, "ok": None}
    base = previous - payments + activity
    d_in = round(total - base, 2)
    if not fees:
        return {"fees_included": None, "diff": d_in, "ok": abs(d_in) < 1}
    d_sep = round(total - (base + fees), 2)
    included = abs(d_in) <= abs(d_sep)
    diff = d_in if included else d_sep
    return {"fees_included": included, "diff": diff, "ok": abs(diff) < 1}
