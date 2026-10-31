"""Kart borcu: kart başına yalnız SON ekstrenin kalanı (denetim M1, Ek A F3.1 / I12). Ödenmemiş eski ekstre yeni
ekstrenin devreden borcunda zaten var; net durum, harcanabilir, Ödemeler toplamı ve Hanefî zekât düşümü onu ikinci kez
saymamalı. Tutarlar ve kart numarası uydurmadır."""
from datetime import date

import pytest

from app.parsers.base import PURCHASE, Statement, Transaction

CARD = "4543***1234"
TODAY = date(2026, 10, 31)


def _st(pe, due, prev, act, total, card=CARD):
    s = Statement(bank="VakıfBank", card_no=card, period_end=pe, due_date=due, total_debt=total,
                  min_payment=round(total * 0.4, 2), credit_limit=50000, previous_balance=prev, period_activity=act,
                  payments=0.0)
    s.transactions = [Transaction(txn_date=pe, description="ORNEK MARKET", amount=act, txn_type=PURCHASE, card=card)]
    return s


@pytest.fixture()
def two_unpaid(tmpdb):
    """Eylül ekstresi 10.000 (ödenmedi), Ekim ekstresi 15.000 (Eylül'ü devralıyor). Gerçek borç 15.000."""
    tmpdb.save_statement(b"sep", "sep.pdf", _st(date(2026, 9, 15), date(2026, 9, 25), 0.0, 10000.0, 10000.0))
    tmpdb.save_statement(b"oct", "oct.pdf", _st(date(2026, 10, 15), date(2026, 10, 25), 10000.0, 5000.0, 15000.0))
    return tmpdb


def test_net_worth_counts_only_latest_statement(two_unpaid):
    from app import finance

    nw = finance.net_worth(TODAY)
    assert nw["card_debt"] == 15000.0  # önce 25.000
    assert nw["card_debt"] == two_unpaid.card_debt_at(TODAY.isoformat())
    assert nw["totals"]["card_debt"] == 15000.0
    assert [c["amount"] for c in nw["cards"] if not c["estimated"]] == [15000.0]
    # eski ekstre ayrı "gecikmiş" bilgisi olarak döner, toplama girmez
    assert [(o["amount"], o["due_date"]) for o in nw["card_overdue"]] == [(10000.0, "2026-09-25")]
    assert nw["totals"]["card_overdue"] == 10000.0
    assert nw["spendable"] == nw["cash"] - 15000.0 - nw["payable"] - nw["reserved"] - nw["zakat_due"]
    assert [c["unpaid"] for c in nw["credit_cards"]] == [15000.0]


def test_payables_totals(two_unpaid):
    from app import finance

    items = finance.payables(TODAY)
    sts = {p["due_date"]: p for p in items if p["type"] == "statement"}
    assert sts["2026-10-25"]["in_total"] is True and "rolled_into" not in sts["2026-10-25"]
    assert sts["2026-09-25"]["in_total"] is False and sts["2026-09-25"]["rolled_into"] == sts["2026-10-25"]["id"]
    t = finance.payables_totals(TODAY, items)
    assert t["card_debt"] == 15000.0 and t["statements"] == 15000.0 and t["open"] == 15000.0
    assert t["overdue"] == 10000.0 and t["overdue_count"] == 1


def test_payables_api_totals(client, two_unpaid):
    assert isinstance(client.get("/api/payables").json(), list)  # eski sözleşme değişmedi
    r = client.get("/api/payables", params={"totals": 1}).json()
    assert set(r) == {"items", "totals"} and r["totals"]["card_debt"] == client.get("/api/net-worth").json()["card_debt"]


def test_partial_payment_of_latest_is_deducted(two_unpaid):
    from app import finance

    oct_id = next(s["id"] for s in two_unpaid.list_statements() if s["period_end"] == "2026-10-15")
    two_unpaid.mark_statement_paid(oct_id, 4000.0, "2026-10-20")
    assert two_unpaid.card_debt_at(TODAY.isoformat()) == 11000.0  # önce kısmi ödemede 0 sayılıyordu
    assert two_unpaid.card_debt_at("2026-10-19") == 15000.0       # ödemeden önceki gün
    assert finance.net_worth(TODAY)["card_debt"] == 11000.0
    two_unpaid.mark_statement_paid(oct_id, 15000.0, "2026-10-21")
    assert finance.net_worth(TODAY)["card_debt"] == 0.0


def test_two_cards_each_latest(two_unpaid):
    from app import finance

    two_unpaid.save_statement(b"other", "o.pdf", _st(date(2026, 10, 10), date(2026, 10, 20), 0.0, 2500.0, 2500.0,
                                                      card="5111***9876"))
    assert finance.net_worth(TODAY)["card_debt"] == 17500.0


def test_old_unpaid_latest_not_dropped(tmpdb):
    """120 günden eski ödenmemiş ekstre listeden sessizce düşüyordu; kartın SON ekstresiyse borç olarak kalır."""
    from app import finance

    tmpdb.save_statement(b"old", "old.pdf", _st(date(2026, 4, 15), date(2026, 4, 25), 0.0, 900.0, 900.0))
    nw = finance.net_worth(TODAY)
    assert nw["card_debt"] == 900.0 == tmpdb.card_debt_at(TODAY.isoformat())
    assert finance.payables_totals(TODAY)["card_debt"] == 900.0


def test_hanefi_zakat_deducts_real_card_debt(two_unpaid):
    from app import zakat

    _, minus = zakat._app_lines({}, zakat._settings(), TODAY)
    assert [m["value"] for m in minus if m["label"].startswith("Kredi kartı borcu")] == [15000.0]  # önce 25.000
