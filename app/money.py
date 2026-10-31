"""Para: tek yuvarlama kuralı ve kuruş düzeyinde karşılaştırma.

Tutarlar veritabanında REAL (float) duruyor. Float ile "en az 1 kuruş fark" (abs(b - a) >= 0.01) ya da "aynı tutar"
(abs(b - a) < 0.01) denetimi ikili gösterime bağlı: 1234,57 − 1234,56 = 0,00999… olduğundan gerçek 1 kuruşluk
farkların çoğu (denemede %81) kaçıyordu. Burada her tutar önce kuruşa (tam sayı) çevrilir, karşılaştırma tam sayıyla
yapılır.

Yuvarlama: muhasebe kuralı, yarım yukarı (ROUND_HALF_UP), ondalık gösterim üzerinden: 0,125 → 0,13; 2,675 → 2,68.
(Python round 0,12 / 2,67, JS Math.round 0,13 / 2,68, SQLite ROUND 0,13 / 2,67 veriyordu.) Web'deki fiş denetimi
(web/src/components/receipt/check.ts) aynı kuralı uygular.

SQL'de eşitlik: "ROUND(amount * 100) = ?" ve parametre kurus(x). Saklanan tutarlar yazılırken money() ile
yuvarlandığından amount * 100 tam sayıya 1e-9'dan yakındır; ROUND güvenli.

Eski kayıtlar: python -m app.money check|fix <veritabanı>  (fix önce yedek alır; kendiliğinden çalışmaz).
"""
from decimal import ROUND_HALF_UP, Decimal

CENT = 0.01  # tolerans/aralık sınırı olarak "1 kuruş" (eşitlik için eq kullan)
_Q = Decimal("0.01")



def sql_eq(col: str) -> str:
    """SQL'de kuruş eşitliği: sql_eq("amount") → "ROUND(amount * 100) = ?"; parametre kurus(x)."""
    return f"ROUND({col} * 100) = ?"


def _dec(x) -> Decimal:
    # repr: float'ın en kısa ondalık gösterimi (0.125 → "0.125", 1234.565 → "1234.565"); Decimal(float) ikili
    # açılımı verirdi (1234.5649999…) ve yarım kuruşlar aşağı yuvarlanırdı
    return x if isinstance(x, Decimal) else Decimal(repr(float(x)))


def money(x):
    """2 ondalık, yarım yukarı. None → None (isteğe bağlı alanlar için)."""
    if x is None:
        return None
    return float(_dec(x).quantize(_Q, rounding=ROUND_HALF_UP))


def kurus(x) -> int:
    """Tutar → kuruş (tam sayı), money() ile aynı yuvarlama."""
    return int(_dec(x).quantize(_Q, rounding=ROUND_HALF_UP).scaleb(2))


def diff_k(a, b) -> int:
    """a − b, kuruş cinsinden."""
    return kurus(a) - kurus(b)


def eq(a, b) -> bool:
    return kurus(a) == kurus(b)


def ne(a, b) -> bool:
    return kurus(a) != kurus(b)


def lt(a, b) -> bool:
    return kurus(a) < kurus(b)


def le(a, b) -> bool:
    return kurus(a) <= kurus(b)


def gt(a, b) -> bool:
    return kurus(a) > kurus(b)


def ge(a, b) -> bool:
    return kurus(a) >= kurus(b)


def is_zero(x) -> bool:
    return kurus(x) == 0


def within(a, b, k: int = 1) -> bool:
    """|a − b| ≤ k kuruş (banka bakiyesi zinciri gibi bilinçli toleranslar için)."""
    return abs(diff_k(a, b)) <= k


# ---------------------------------------------------------------- eski kayıtlar: kuruş altı tutarları yuvarla

# Yalnız TL olan kolonlar. Bilerek dışarıda: transactions.orig_amount (döviz), receipt_items.qty/unit_price (miktar,
# kg fiyatı), jars/jar_moves.amount (birimli kumbarada gram), debts/debt_payments (asset TRY değilse miktar: ayrı sorgu).
TL_COLUMNS = {
    "statements": ("total_debt", "min_payment", "credit_limit", "previous_balance", "period_activity",
                   "interest_fees", "payments", "paid_amount"),
    "transactions": ("amount", "installment_left_amount"),
    "entries": ("amount",),
    "bills": ("amount", "paid_amount"),
    "card_payments": ("amount",),
    "own_transfers": ("amount",),
    "accounts": ("balance",),
    "entry_matches": ("bank_amount", "balance"),
    "splits": ("amount",),
    "receipt_items": ("amount",),
    "recurring": ("amount", "gross_amount"),
    "recurring_overrides": ("amount",),
    "recurring_changes": ("amount",),
    "salary_changes": ("gross",),
    "budgets": ("monthly_limit",),
    "saving_targets": ("amount", "already"),
    "savings_goals": ("baseline", "target"),
    "subscription_state": ("new_amount",),
    "jar_moves": ("cost",),
}
_OFF = "{col} IS NOT NULL AND ABS({col} * 100 - ROUND({col} * 100)) > 1e-6"


def _targets(c):
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for t, cols in TL_COLUMNS.items():
        if t in tables:
            have = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
            for col in cols:
                if col in have:
                    yield t, col, ""
    if "debts" in tables:  # borç: yalnız TL borçlar (altın/döviz borcunda tutar miktardır)
        yield "debts", "amount", " AND asset = 'TRY'"
        yield "debts", "paid_amount", " AND asset = 'TRY'"
        if "debt_payments" in tables:
            yield "debt_payments", "amount", " AND debt_id IN (SELECT id FROM debts WHERE asset = 'TRY')"


def off_cents(c) -> dict[str, int]:
    """Kuruş altı tutar taşıyan satır sayıları {"tablo.kolon": n} (yalnız sıfırdan farklılar)."""
    out = {}
    for t, col, extra in _targets(c):
        n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE {_OFF.format(col=col)}{extra}").fetchone()[0]
        if n:
            out[f"{t}.{col}"] = n
    return out


def round_db(c) -> dict[str, int]:
    """Kuruş altı tutarları money() ile yuvarla (Python'da: SQLite ROUND yarımları farklı yuvarlar). Değişen sayılar."""
    out = {}
    for t, col, extra in _targets(c):
        rows = c.execute(f"SELECT rowid, {col} FROM {t} WHERE {_OFF.format(col=col)}{extra}").fetchall()
        for rid, v in rows:
            c.execute(f"UPDATE {t} SET {col} = ? WHERE rowid = ?", (money(v), rid))
        if rows:
            out[f"{t}.{col}"] = len(rows)
    return out


def _main(argv: list[str]) -> int:
    import shutil
    import sqlite3
    import sys
    import time

    if len(argv) != 2 or argv[0] not in ("check", "fix"):
        print("kullanım: python -m app.money check|fix <veritabanı.db>", file=sys.stderr)
        return 2
    cmd, path = argv
    if cmd == "fix":
        bak = f"{path}.yuvarlama-oncesi-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, bak)
        print(f"yedek: {bak}")
    with sqlite3.connect(path) as c:
        res = round_db(c) if cmd == "fix" else off_cents(c)
        left = off_cents(c)
    for k, n in sorted(res.items()):
        print(f"{k}: {n}")
    print(("yuvarlandı" if cmd == "fix" else "kuruş altı") + f": {sum(res.values())} satır; kalan: {sum(left.values())}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
