"""Maaş › Aylık dağılım (yetenek salary_schedule): GET /api/recurring/{id}/schedule. Her satırın neti planla aynı
(db.planned_amount, tek kaynak); peşin maaşta (memur) zamlı ilk ödeme "maaş + zam farkı" diye ikiye bölünür.
Tutarlar uydurma."""
from app import version
from app.payroll import net_schedule

GROSS = 60000.0


def _maas(client, tmpdb, *, start_day=1, advance=True, raise_pct=8.0, basis="net", day=15):
    if start_day != 1:
        tmpdb.set_cycle_start_day(start_day)
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 0, "kind": "gelir", "category": "Maaş",
                                              "day_of_month": day, "start_month": "2026-01",
                                              "gross_amount": GROSS}).json()["id"]
    if advance:
        client.patch(f"/api/recurring/{rid}", json={"advance_pay": True})
    res = client.post(f"/api/recurring/{rid}/salary-changes",
                      json={"from_month": "2027-01", "pct": raise_pct, "basis": basis})
    assert res.status_code in (200, 201), res.text
    r = dict(next(x for x in tmpdb.list_recurring() if x["id"] == rid))
    return rid, r, res.json()["gross"]


def _sched(client, rid, start="2026-12", months=12):
    res = client.get(f"/api/recurring/{rid}/schedule", params={"start": start, "months": months})
    assert res.status_code == 200, res.text
    return res.json()


def test_capability_listed():
    assert "salary_schedule" in version.CAPABILITIES


def test_memur_january_raise_split_cycle1(client, tmpdb):
    rid, r, raised = _maas(client, tmpdb)
    s = _sched(client, rid)
    rows = s["months"]
    assert s["advance_pay"] is True and len(rows) == 12
    assert [x["period"] for x in rows][:3] == ["2026-12", "2027-01", "2027-02"]
    # tek kaynak: her satırın neti planla aynı
    for x in rows:
        assert x["net"] == tmpdb.planned_amount(r, x["period"])
        assert x["pay_date"] == tmpdb.recurring_due(r, x["period"])
    dec, jan, feb = rows[0], rows[1], rows[2]
    assert dec["raised"] is False and dec["raise_start"] is False and dec["raise_diff"] == 0
    assert jan["pay_date"] == "2027-01-15"
    assert jan["raise_start"] is True and jan["raised"] is True and jan["raise_days"] == 14
    assert jan["raise_diff"] > 0
    assert round(jan["base_net"] + jan["raise_diff"], 2) == jan["net"]
    # farksız maaş = o yıl yalnız zamlı brütle hesaplanan ocak neti
    plain = net_schedule(2027, {m: raised for m in range(1, 13)})["months"]
    assert jan["base_net"] == plain[0]["net"]
    assert jan["gross"] == round(raised + (raised - GROSS) * 14 / 30, 2)
    # şubat: zamlı ama fark yok; zam yalnız bir kez "başlar"
    assert feb["raised"] is True and feb["raise_start"] is False and feb["raise_diff"] == 0 and feb["base_net"] == feb["net"]
    assert sum(x["raise_start"] for x in rows) == 1
    # rozet yüzdesi kullanıcının girdiği zam (%8 net); netten nete hesaplanmaz (ocakta matrah sıfırlanır)
    assert (jan["raise_pct"], jan["raise_pct_basis"]) == (8.0, "net")
    assert all(x["raise_pct"] is None for x in rows if not x["raise_start"])
    # Ocak'ta matrah sıfırlanır: zamsız Aralık neti dilim yükselmiş olduğu için ocaktaki farksız netten düşük
    assert dec["net"] < jan["base_net"]
    assert s["total"] == round(sum(x["net"] for x in rows), 2)


def test_memur_cycle17_raise_on_january_payment(client, tmpdb):
    rid, r, raised = _maas(client, tmpdb, start_day=17)
    rows = {x["period"]: x for x in _sched(client, rid, start="2027-01")["months"]}
    for p, x in rows.items():
        assert x["net"] == tmpdb.planned_amount(r, p)
    # 15 Ara ödemesi 2027-01 dönemine sayılır ama Aralık maaşıdır: zamsız, farksız
    assert rows["2027-01"]["pay_date"] == "2026-12-15"
    assert rows["2027-01"]["raise_start"] is False and rows["2027-01"]["raise_diff"] == 0
    # 15 Oca ödemesi (dönem 2027-02): zam + 14 günlük fark
    j = rows["2027-02"]
    assert j["pay_date"] == "2027-01-15" and j["raise_start"] is True and j["raise_days"] == 14 and j["raise_diff"] > 0
    assert (j["raise_pct"], j["raise_pct_basis"]) == (8.0, "net") and rows["2027-01"]["raise_pct"] is None
    assert round(j["base_net"] + j["raise_diff"], 2) == j["net"]
    assert rows["2027-03"]["raise_diff"] == 0 and rows["2027-03"]["raised"] is True


def test_normal_salary_has_no_diff(client, tmpdb):
    rid, r, _ = _maas(client, tmpdb, advance=False, raise_pct=10.0, basis="brut", day=10)
    s = _sched(client, rid)
    rows = s["months"]
    assert s["advance_pay"] is False
    for x in rows:
        assert x["net"] == tmpdb.planned_amount(r, x["period"])
        assert x["raise_diff"] == 0 and x["base_net"] == x["net"]
    assert [x["raise_start"] for x in rows[:3]] == [False, True, False]
    assert (rows[1]["raise_pct"], rows[1]["raise_pct_basis"]) == (10.0, "brut")
    assert rows[1]["gross"] == 66000.0


def test_month_end_salary_funds_next_period(client, tmpdb):
    """Ayın 28'inde yatan maaş sonraki dönemin geliri (salary_lead): zam 28 Oca ödemesinde, dönemi 2027-02."""
    rid, r, _ = _maas(client, tmpdb, advance=False, raise_pct=10.0, basis="brut", day=28)
    rows = {x["period"]: x for x in _sched(client, rid)["months"]}
    assert rows["2027-01"]["pay_date"] == "2026-12-28" and rows["2027-01"]["raise_start"] is False
    assert rows["2027-02"]["pay_date"] == "2027-01-28" and rows["2027-02"]["raise_start"] is True
    assert all(x["net"] == tmpdb.planned_amount(r, p) for p, x in rows.items())


def test_override_month_keeps_plan_amount(client, tmpdb):
    rid, r, _ = _maas(client, tmpdb)
    assert client.put(f"/api/recurring/{rid}/months/2027-01", json={"amount": 50000}).json() == {"ok": True}
    jan = _sched(client, rid)["months"][1]
    assert jan["net"] == 50000 and jan["overridden"] is True
    assert jan["raise_diff"] == 0 and jan["base_net"] == 50000


def test_net_item_amount_change_and_end_month(client, tmpdb):
    rid = client.post("/api/recurring", json={"description": "Kira geliri", "amount": 15000, "kind": "gelir",
                                              "category": "Kira", "day_of_month": 5, "start_month": "2026-01",
                                              "end_month": "2027-03"}).json()["id"]
    client.post(f"/api/recurring/{rid}/changes", json={"from_month": "2027-02", "amount": 16500})
    s = _sched(client, rid)
    rows = s["months"]
    assert [x["period"] for x in rows] == ["2026-12", "2027-01", "2027-02", "2027-03"]  # bitişten sonrası yok
    assert [x["net"] for x in rows] == [15000, 15000, 16500, 16500]
    assert [x["raise_start"] for x in rows] == [False, False, True, False]
    assert (rows[2]["raise_pct"], rows[2]["raise_pct_basis"]) == (10.0, "net")
    assert all(x["gross"] is None and x["raise_diff"] == 0 for x in rows)
    assert s["total"] == 63000


def test_unknown_item_404_and_bad_params(client, tmpdb):
    assert client.get("/api/recurring/9999/schedule").status_code == 404
    rid, _, _ = _maas(client, tmpdb)
    assert client.get(f"/api/recurring/{rid}/schedule", params={"months": 0}).status_code == 422
    assert client.get(f"/api/recurring/{rid}/schedule", params={"start": "2026-13x"}).status_code == 422
    assert len(client.get(f"/api/recurring/{rid}/schedule").json()["months"]) == 12  # varsayılan: bu dönemden 12 ay


def test_second_raise_by_gross_uses_gross_to_gross_pct(client, tmpdb):
    """Temmuz zammı brütle girildi (yüzde yok): rozet yüzdesi brütten brüte. Ocak'taki net zam kendi yüzdesini taşır."""
    rid, r, jan_gross = _maas(client, tmpdb)
    july = round(jan_gross * 1.12, 2)
    assert client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2027-07", "gross": july}).status_code == 200
    rows = {x["period"]: x for x in _sched(client, rid, start="2027-01")["months"]}
    assert (rows["2027-01"]["raise_pct"], rows["2027-01"]["raise_pct_basis"]) == (8.0, "net")
    t = rows["2027-07"]
    assert t["raise_start"] is True and t["raise_diff"] > 0
    assert (t["raise_pct"], t["raise_pct_basis"]) == (round((july - jan_gross) / jan_gross * 100, 1), "brut") == (12.0, "brut")
    assert [p for p, x in rows.items() if x["raise_start"]] == ["2027-01", "2027-07"]


def test_job_change_and_correction_not_raise(client, tmpdb):
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 0, "kind": "gelir", "category": "Maaş",
                                              "day_of_month": 5, "start_month": "2026-01",
                                              "gross_amount": GROSS}).json()["id"]
    client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2027-01", "gross": 70000,
                                                             "reason": "is_degisikligi", "employer": "Örnek AŞ"})
    client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2027-03", "gross": 71000, "note": "Düzeltme"})
    rows = _sched(client, rid)["months"]
    assert not any(x["raise_start"] for x in rows) and all(x["raise_pct"] is None for x in rows)


def test_net_defined_salary_first_raise(client, tmpdb):
    """Netle tanımlı maaşın ilk zammı (önceki brüt bilinmiyor): yine zam başlangıcı, girilen yüzdeyle."""
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 40000, "kind": "gelir", "category": "Maaş",
                                              "day_of_month": 5, "start_month": "2026-01"}).json()["id"]
    assert client.post(f"/api/recurring/{rid}/salary-changes",
                       json={"from_month": "2027-01", "pct": 15, "basis": "brut"}).status_code == 200
    rows = _sched(client, rid)["months"]
    assert [x["raise_start"] for x in rows[:3]] == [False, True, False]
    assert (rows[1]["raise_pct"], rows[1]["raise_pct_basis"]) == (15.0, "brut")
