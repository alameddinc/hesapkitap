from app.payroll import PARAMS, _r, income_tax, net_by_month


def test_minimum_wage_net_matches_official():
    # 2026 asgari ücret: brüt 33.030 → net 28.075,50 (her ay; vergi ve damga istisnası)
    r = net_by_month(33_030, 2026)
    assert all(m["net"] == 28_075.50 for m in r["months"])
    assert all(m["income_tax"] == 0 and m["stamp"] == 0 for m in r["months"])


def test_minimum_wage_exemption_amounts_match_source():
    # kaynak: istisna Ocak–Haziran 4.211,33 · Temmuz–Aralık 5.615,10 (kümülatif dilim geçişi)
    b = PARAMS[2026]["brackets"]
    base = 33_030 * 0.85
    ex = [_r(income_tax(base * (k + 1), b) - income_tax(base * k, b)) for k in range(12)]
    assert ex[0] == 4_211.33 and ex[5] == 4_211.33
    assert ex[7] == 5_615.10  # Ağustos; Temmuz geçiş ayı (kısmen %15, kısmen %20)


def test_hand_calculated_100k_gross():
    r = {m["month"]: m for m in net_by_month(100_000, 2026)["months"]}
    # Ocak: SGK 14.000, işsizlik 1.000 → matrah 85.000; GV 12.750 − istisna 4.211,33; damga 759 − 250,70
    assert r[1]["income_tax"] == 8_538.67 and r[1]["stamp"] == 508.30
    assert r[1]["net"] == 75_953.03
    # Mart: kümülatif 255.000 → bu ay vergisi 41.500 − 25.500 = 16.000 − 4.211,33
    assert r[3]["net"] == 72_703.03
    # dilim yükseldikçe net düşer
    assert r[12]["net"] < r[1]["net"]


def test_sgk_cap():
    r = net_by_month(400_000, 2026)["months"][0]
    assert r["sgk"] == round(297_270 * 0.14, 2)


def test_recurring_gross_salary_in_plan(client):
    from datetime import date

    from app import finance
    from app.payroll import net_for

    client.post("/api/recurring", json={"description": "Maaş", "amount": 75953.03, "gross_amount": 100000,
                                        "kind": "gelir", "day_of_month": 15, "start_month": "2026-01"})
    months = {m["month"]: m["income"] for m in finance.plan(3, today=date(2026, 10, 2))["months"]}
    assert months["2026-10"] == net_for(100000, 2026, 10)
    assert months["2026-12"] == net_for(100000, 2026, 12)
    r = client.get("/api/payroll/net", params={"gross": 33030, "year": 2026}).json()
    assert r["months"][0]["net"] == 28075.5 and r["params_year"] == 2026
