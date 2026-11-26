"""Maaş zammı ödemenin TAKVİM ayından başlar: ay dönümü kayması (salary_lead) zammı bir önceki ödemeye çekmez.
Ay dönümü 17, maaş 15, peşin maaş: 15 Ara ödemesi 2027-01 dönemine sayılır ama Aralık maaşıdır; "2027-01'den" zam
(ve 1–14 Oca farkı) ilk kez 15 Oca ödemesinde. Vergi matrahı da ödemenin takvim yılında. Tutarlar uydurma."""
from app.payroll import net_schedule

GROSS, RAISED = 60000.0, 66000.0


def _setup(client, tmpdb, start_day):
    if start_day != 1:
        tmpdb.set_cycle_start_day(start_day)
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 0, "kind": "gelir", "category": "Maaş",
                                              "day_of_month": 15, "start_month": "2026-01",
                                              "gross_amount": GROSS}).json()["id"]
    client.patch(f"/api/recurring/{rid}", json={"advance_pay": True})
    assert client.post(f"/api/recurring/{rid}/salary-changes",
                       json={"from_month": "2027-01", "gross": RAISED}).status_code in (200, 201)
    return rid, dict(next(x for x in tmpdb.list_recurring() if x["id"] == rid))


def _nets():
    dec = net_schedule(2026, {m: GROSS for m in range(1, 13)})["months"][11]["net"]
    first = round(RAISED + (RAISED - GROSS) * 14 / 30, 2)
    jan = net_schedule(2027, {1: first, **{m: RAISED for m in range(2, 13)}})["months"]
    return dec, jan[0]["net"], jan[1]["net"]


def test_cycle17_raise_starts_with_january_payment(client, tmpdb):
    rid, r = _setup(client, tmpdb, 17)
    dec, jan, feb = _nets()
    assert tmpdb.recurring_due(r, "2027-01") == "2026-12-15"
    assert tmpdb.planned_amount(r, "2027-01") == dec  # 15 Ara: zamsız, farksız
    assert tmpdb.recurring_due(r, "2027-02") == "2027-01-15"
    assert tmpdb.planned_amount(r, "2027-02") == jan  # 15 Oca: zamlı + 14 günlük fark
    assert tmpdb.planned_amount(r, "2027-03") == feb
    g, _ = tmpdb.gross_schedule(r, 2026, tmpdb.salary_changes(rid))
    assert g[12] == GROSS


def test_cycle17_preview_uses_calendar_months(client, tmpdb):
    rid, _ = _setup(client, tmpdb, 17)
    d = client.post(f"/api/recurring/{rid}/payroll",
                    json={"year": 2027, "draft": {"from_month": "2027-01", "gross": RAISED}}).json()["draft_resolved"]
    _, jan, feb = _nets()
    assert d["advance"]["first"] == jan and d["new_net"] == feb
    assert d["advance_text"].startswith("Ocak'ta ₺") and "Şubat'tan itibaren" in d["advance_text"]


def test_cycle1_unchanged(client, tmpdb):
    _, r = _setup(client, tmpdb, 1)
    dec, jan, feb = _nets()
    assert tmpdb.planned_amount(r, "2026-12") == dec
    assert tmpdb.planned_amount(r, "2027-01") == jan
    assert tmpdb.planned_amount(r, "2027-02") == feb
