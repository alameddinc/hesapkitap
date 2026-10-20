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


def test_schedule_constant_gross_equals_net_by_month():
    from app.payroll import net_schedule

    for g in (33_030, 100_000, 370_800):
        a = [m["net"] for m in net_by_month(g, 2026)["months"]]
        b = [m["net"] for m in net_schedule(2026, {m: g for m in range(1, 13)})["months"]]
        assert a == b


def test_schedule_raise_and_job_change():
    from app.payroll import net_schedule

    old = net_by_month(100_000, 2026)["months"]
    raised = net_schedule(2026, {m: 100_000 if m == 1 else 150_000 for m in range(1, 13)})["months"]
    assert raised[0]["net"] == old[0]["net"]                  # Ocak eski brütle
    assert raised[1]["gross"] == 150_000 and raised[1]["net"] > old[1]["net"]
    # yeni işveren matrahı sıfırdan başlatırsa o ay vergi düşer → net artar
    cont = net_schedule(2026, {m: 150_000 for m in range(1, 13)})["months"]
    reset = net_schedule(2026, {m: 150_000 for m in range(1, 13)}, resets={9})["months"]
    assert reset[7]["net"] == cont[7]["net"] and reset[8]["net"] > cont[8]["net"]
    # iki iş arası boş ay
    gap = net_schedule(2026, {m: 0 if m == 6 else 100_000 for m in range(1, 13)})["months"]
    assert gap[5]["net"] == 0


def test_salary_change_in_plan(client):
    from datetime import date

    from app import db, finance
    from app.payroll import net_schedule

    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 0, "gross_amount": 100000,
                                              "kind": "gelir", "day_of_month": 15, "start_month": "2026-01"}).json()["id"]
    assert client.post(f"/api/recurring/{rid}/salary-changes",
                       json={"from_month": "2026-11", "gross": 150000}).status_code == 200
    months = {m["month"]: m["income"] for m in finance.plan(3, today=date(2026, 10, 2))["months"]}
    expected = net_schedule(2026, {m: 100000 if m < 11 else 150000 for m in range(1, 13)})["months"]
    assert months["2026-10"] == expected[9]["net"]
    assert months["2026-11"] == expected[10]["net"]
    # iş değişikliği: yeni maaş günü + matrah sıfırdan + işveren adı
    client.post(f"/api/recurring/{rid}/salary-changes",
                json={"from_month": "2026-12", "gross": 150000, "reason": "is_degisikligi", "reset_cum": True,
                      "pay_day": 1, "employer": "Örnek Yazılım"})
    r = next(x for x in db.list_recurring() if x["id"] == rid)
    assert db.day_at(r, "2026-12") == 1 and db.day_at(r, "2026-11") == 15
    pv = client.post(f"/api/recurring/{rid}/payroll", json={"year": 2026}).json()
    assert pv["resets"] == [12] and pv["months"][11]["net"] > pv["months"][10]["net"]
    # taslak önizleme kaydetmez
    client.post(f"/api/recurring/{rid}/payroll", json={"year": 2027, "draft": {"from_month": "2027-02", "gross": 200000}})
    assert len(client.get(f"/api/recurring/{rid}/salary-changes").json()) == 2
    cid = client.get(f"/api/recurring/{rid}/salary-changes").json()[0]["id"]
    assert client.delete(f"/api/salary-changes/{cid}").json()["ok"]


def test_bank_salary_matches_new_employer(client, tmpdb):
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 0, "gross_amount": 100000,
                                              "kind": "gelir", "day_of_month": 1, "start_month": "2026-01"}).json()["id"]
    client.post(f"/api/recurring/{rid}/salary-changes",
                json={"from_month": "2026-12", "gross": 150000, "reason": "is_degisikligi", "employer": "Örnek Yazılım"})
    st = tmpdb.import_bank_rows([{"date": "2026-12-01", "description": "ORNEK YAZILIM AS ODEME", "amount": 110000.0,
                                  "type": "gelir", "hash": "s1"}])
    assert st["maas"] == 1
