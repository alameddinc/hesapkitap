"""Özet uyarıları eylem taşır: gecikmiş/kısmi ödeme "Ödedim" için hedefini (pay), plan uyarıları bağlantısını
(özellik kapalıysa uyarı kalır, yalnız bağlantı düşer)."""
from datetime import date


def test_overdue_bill_insight_has_pay_target(tmpdb):
    from app import finance

    bid = tmpdb.create_bill({"provider": "Örnek Su", "amount": 300, "due_date": "2026-10-20", "category": "Su"})
    ins = [i for i in finance.insights("2026-10", today=date(2026, 10, 30)) if i["title"].startswith("Örnek Su:")]
    assert ins and ins[0]["pay"] == {"type": "bill", "id": bid}
    tmpdb.pay_bill(bid, 100, "2026-10-29", "havale")  # kısmi: kalan uyarısı da aynı hedefi taşır
    ins = [i for i in finance.insights("2026-10", today=date(2026, 10, 30)) if i["title"].startswith("Örnek Su:")]
    assert ins and "kaldı" in ins[0]["title"] and ins[0]["pay"] == {"type": "bill", "id": bid}


def test_soft_link_survives_module_off(tmpdb):
    from app import finance, prefs

    get = lambda: [i for i in finance.insights("2026-10", today=date(2026, 10, 30)) if i["title"] == "Gelirin tanımlı değil"]
    ins = get()
    assert ins and ins[0]["link"] == "plan" and "soft_link" not in ins[0]
    prefs.set_modules({"plan": False})
    ins = get()
    assert ins and "link" not in ins[0]
