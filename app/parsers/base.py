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

    def reconciliation_diff(self) -> float:
        """Parse edilen işlemler toplamı ile ekstre özetinin farkı. 0 değilse satır kaçmış demektir."""
        parsed = sum(t.amount for t in self.transactions if t.txn_type != PAYMENT)
        return round(parsed - (self.period_activity + self.interest_fees), 2)
