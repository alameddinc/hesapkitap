"""Maaş zammı: yüzdeyle giriş (brüt/net), yatan maaşa göre öneri (zam ayı, ikramiye, beklenen zam, peşin maaş farkı).
Tutarlar uydurma."""
from datetime import date

from app import finance, salary


def _salary(client, gross=100000, day=15, start="2026-01", advance=False, amount=0, **kw):
    body = {"description": "Maaş", "amount": amount, "kind": "gelir", "category": "Maaş", "day_of_month": day,
            "start_month": start, **kw}
    if gross:
        body["gross_amount"] = gross
    rid = client.post("/api/recurring", json=body).json()["id"]
    if advance:
        client.patch(f"/api/recurring/{rid}", json={"advance_pay": True})
    return rid


def _rec(tmpdb, rid):
    return dict(next(x for x in tmpdb.list_recurring() if x["id"] == rid))


def _paid(client, rid, month, amount):
    client.post(f"/api/recurring/{rid}/confirm", json={"month": month, "amount": round(amount, 2)})


def _model(tmpdb, rid, month, changes):
    return salary.net_at(_rec(tmpdb, rid), month, changes)


def _ch(month, gross):
    return {"from_month": month, "gross": gross, "reason": "zam", "reset_cum": 0, "pay_day": None, "id": None}


# ---------------------------------------------------------------- yüzdeyle giriş

def test_pct_brut_preview_and_save(client, tmpdb):
    rid = _salary(client, gross=100000)
    r = client.post(f"/api/recurring/{rid}/payroll",
                    json={"year": 2027, "draft": {"from_month": "2027-01", "pct": 15, "basis": "brut"}}).json()
    d = r["draft_resolved"]
    assert d["gross"] == 115000 and d["prev_gross"] == 100000 and d["gross_pct"] == 15
    assert r["months"][0]["gross"] == 115000
    assert d["text"].startswith("Önceki brüt ₺100.000 → %15 zamla ₺115.000 brüt")
    saved = client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2027-01", "pct": 15}).json()
    assert saved["gross"] == 115000
    ch = client.get(f"/api/recurring/{rid}/salary-changes").json()[0]
    assert ch["gross"] == 115000 and ch["pct"] == 15 and ch["pct_basis"] == "brut"


def test_pct_uses_salary_in_effect_before_month(client):
    rid = _salary(client, gross=100000)
    client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2026-07", "gross": 120000})
    d = client.post(f"/api/recurring/{rid}/payroll",
                    json={"year": 2027, "draft": {"from_month": "2027-01", "pct": 10}}).json()["draft_resolved"]
    assert d["prev_gross"] == 120000 and d["gross"] == 132000


def test_pct_net_targets_net_of_same_month(client, tmpdb):
    rid = _salary(client, gross=100000)
    r = _rec(tmpdb, rid)
    old = salary.net_at(r, "2026-11", [])
    d = client.post(f"/api/recurring/{rid}/payroll",
                    json={"year": 2026, "draft": {"from_month": "2026-11", "pct": 10, "basis": "net"}}).json()["draft_resolved"]
    assert abs(d["new_net"] - old * 1.10) < 0.05 and d["old_net"] == round(old, 2)
    assert d["gross"] > 110000  # dilim yüzünden net %10 için brüt %10'dan fazla artar
    assert d["text"].startswith(f"Önceki net ₺{old:,.0f}".replace(",", "."))


def test_net_defined_salary_pct(client, tmpdb):
    rid = _salary(client, gross=None, amount=60000)
    d = client.post(f"/api/recurring/{rid}/payroll",
                    json={"year": 2027, "draft": {"from_month": "2027-01", "pct": 20, "basis": "net"}}).json()["draft_resolved"]
    assert abs(d["new_net"] - 72000) < 0.05 and d["old_net"] == 60000
    # brüt yüzdesi: önceki brüt = netin brüt karşılığı
    d2 = client.post(f"/api/recurring/{rid}/payroll",
                     json={"year": 2027, "draft": {"from_month": "2027-01", "pct": 20}}).json()["draft_resolved"]
    assert d2["prev_gross"] > 60000 and abs(d2["gross"] - d2["prev_gross"] * 1.2) < 0.01


def test_draft_needs_an_amount(client):
    rid = _salary(client)
    assert client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2027-01"}).status_code == 422


def test_advance_pay_preview_explains_first_month(client, tmpdb):
    rid = _salary(client, gross=60000, advance=True, start="2026-07")
    d = client.post(f"/api/recurring/{rid}/payroll",
                    json={"year": 2027, "draft": {"from_month": "2027-01", "pct": 10}}).json()["draft_resolved"]
    a = d["advance"]
    assert d["gross"] == 66000 and a["days"] == 14 and d["normal_month"] == "2027-02"
    assert a["diff"] > 0 and abs(a["first"] - a["base"] - a["diff"]) < 0.02
    g, _ = tmpdb.gross_schedule(_rec(tmpdb, rid), 2027, [_ch("2027-01", 66000)])
    assert g[1] == round(66000 + 6000 * 14 / 30, 2) and g[2] == 66000  # 14 günlük fark ocakta
    assert d["advance_text"].startswith("Ocak'ta ₺") and "14 günlük fark" in d["advance_text"]
    assert "Şubat'tan itibaren" in d["advance_text"]
    # net hedefi peşin maaşta normal aya (şubat) göre: ocak farkı hedefi şişirmez
    old_feb = salary.net_at(_rec(tmpdb, rid), "2027-02", [])
    dn = client.post(f"/api/recurring/{rid}/payroll",
                     json={"year": 2027, "draft": {"from_month": "2027-01", "pct": 10, "basis": "net"}}).json()["draft_resolved"]
    assert abs(dn["new_net"] - old_feb * 1.1) < 0.05 and dn["advance"]["first"] > dn["new_net"]


def test_plan_uses_advance_difference_in_first_raised_month(client, tmpdb):
    rid = _salary(client, gross=60000, advance=True, start="2026-07")
    client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2027-01", "pct": 10})
    months = {m["month"]: m["income"] for m in finance.plan(6, today=date(2026, 11, 20))["months"]}
    r = _rec(tmpdb, rid)
    assert months["2027-01"] == tmpdb.planned_amount(r, "2027-01")
    assert months["2027-01"] > months["2027-02"] > months["2026-12"]


# ---------------------------------------------------------------- yatan maaşa göre öneri

K = 0.97  # bordroda modelde olmayan kesinti (BES vb.): oranla elenir


def _history(client, tmpdb, rid, months, changes=()):
    for m in months:
        _paid(client, rid, m, _model(tmpdb, rid, m, list(changes)) * K)


def test_january_raise_single_month_suggests(client, tmpdb):
    rid = _salary(client, gross=100000, start="2026-08")
    _history(client, tmpdb, rid, ["2026-09", "2026-10", "2026-11", "2026-12"])
    assert finance.recurring_drift(date(2026, 12, 20), salary=True) == []  # plan gibi yattı
    _paid(client, rid, "2027-01", _model(tmpdb, rid, "2027-01", [_ch("2027-01", 120000)]) * K)
    d = finance.recurring_drift(date(2027, 1, 20), salary=True)
    assert len(d) == 1
    s = d[0]
    assert s["type"] == "maas" and s["from_month"] == "2027-01" and abs(s["gross"] - 120000) < 1
    assert s["title"] == "Brüt maaşın %20 arttı (₺100.000 → ₺120.000). Sabit kalemi güncelle?"
    assert s["text"].startswith("Eline geçen ₺")
    assert s["dismiss_label"] == "Bu ay farklıydı" and "Ocak'tan itibaren" in s["text"]
    # eski istemci (parametresiz) maaş önerisini görmez
    assert finance.recurring_drift(date(2027, 1, 20)) == []
    assert client.post("/api/recurring/salary-drift/accept", json={"recurring_id": rid, "month": "2027-01"}).status_code == 200
    ch = client.get(f"/api/recurring/{rid}/salary-changes").json()
    assert len(ch) == 1 and abs(ch[0]["gross"] - 120000) < 1 and ch[0]["reason"] == "zam"
    assert finance.recurring_drift(date(2027, 1, 20), salary=True) == []
    assert client.post("/api/recurring/salary-drift/accept", json={"recurring_id": rid, "month": "2027-01"}).status_code == 409


def test_bonus_month_does_not_trigger(client, tmpdb):
    rid = _salary(client, gross=100000, start="2026-01")
    _history(client, tmpdb, rid, ["2026-01", "2026-02"])
    _paid(client, rid, "2026-03", _model(tmpdb, rid, "2026-03", []) * K * 1.8)  # ikramiye aynı yatışta
    assert finance.recurring_drift(date(2026, 3, 20), salary=True) == []
    _history(client, tmpdb, rid, ["2026-04"])
    assert finance.recurring_drift(date(2026, 4, 20), salary=True) == []


def test_january_bonus_over_half_waits(client, tmpdb):
    rid = _salary(client, gross=100000, start="2026-09")
    _history(client, tmpdb, rid, ["2026-10", "2026-11", "2026-12"])
    _paid(client, rid, "2027-01", _model(tmpdb, rid, "2027-01", []) * K * 2)
    assert finance.recurring_drift(date(2027, 1, 20), salary=True) == []


def test_midyear_raise_needs_two_months(client, tmpdb):
    rid = _salary(client, gross=100000, start="2026-01")
    _history(client, tmpdb, rid, ["2026-01", "2026-02", "2026-03"])
    new = [_ch("2026-04", 115000)]
    _paid(client, rid, "2026-04", _model(tmpdb, rid, "2026-04", new) * K)
    assert finance.recurring_drift(date(2026, 4, 20), salary=True) == []  # nisan tek başına yetmez
    _paid(client, rid, "2026-05", _model(tmpdb, rid, "2026-05", new) * K)
    d = finance.recurring_drift(date(2026, 5, 20), salary=True)
    assert len(d) == 1 and d[0]["from_month"] == "2026-04" and d[0]["month"] == "2026-05"
    assert abs(d[0]["gross"] - 115000) < 1 and "Son iki ay yeni tutarla geldi." in d[0]["text"]


def test_expected_pct_compared_with_actual(client, tmpdb):
    rid = _salary(client, gross=100000, start="2026-09")
    _history(client, tmpdb, rid, ["2026-10", "2026-11", "2026-12"])
    client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2027-01", "pct": 10})  # bekliyordu
    _paid(client, rid, "2027-01", _model(tmpdb, rid, "2027-01", [_ch("2027-01", 111200)]) * K)
    s = finance.recurring_drift(date(2027, 1, 20), salary=True)[0]
    assert s["expected_pct"] == 10 and s["expected_basis"] == "brut" and abs(s["actual_pct"] - 11.2) < 0.05
    assert s["text"].startswith("Brüt %10 bekliyordun, %11,2 geldi.")
    client.post("/api/recurring/salary-drift/accept", json={"recurring_id": rid, "month": "2027-01"})
    ch = client.get(f"/api/recurring/{rid}/salary-changes").json()
    assert len(ch) == 1 and abs(ch[0]["gross"] - 111200) < 1  # beklenen zammın yerine geçti


def test_expected_raise_met_no_suggestion(client, tmpdb):
    rid = _salary(client, gross=100000, start="2026-09")
    _history(client, tmpdb, rid, ["2026-10", "2026-11", "2026-12"])
    client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2027-01", "pct": 10})
    _paid(client, rid, "2027-01", _model(tmpdb, rid, "2027-01", [_ch("2027-01", 110000)]) * K)
    assert finance.recurring_drift(date(2027, 1, 20), salary=True) == []


def test_net_expected_pct_text(client, tmpdb):
    rid = _salary(client, gross=100000, start="2026-09")
    _history(client, tmpdb, rid, ["2026-10", "2026-11", "2026-12"])
    client.post(f"/api/recurring/{rid}/salary-changes", json={"from_month": "2027-01", "pct": 10, "basis": "net"})
    _paid(client, rid, "2027-01", _model(tmpdb, rid, "2027-01", [_ch("2027-01", 100000)]) * K * 1.112)
    s = finance.recurring_drift(date(2027, 1, 20), salary=True)[0]
    assert s["expected_basis"] == "net" and s["text"].startswith("%10 bekliyordun, %11,2 geldi.")


def test_advance_pay_first_month_difference_not_permanent(client, tmpdb):
    rid = _salary(client, gross=60000, start="2026-08", advance=True)
    _history(client, tmpdb, rid, ["2026-09", "2026-10", "2026-11", "2026-12"])
    new = [_ch("2027-01", 69000)]
    jan = _model(tmpdb, rid, "2027-01", new) * K  # yeni maaş + 14 günlük fark
    _paid(client, rid, "2027-01", jan)
    s = finance.recurring_drift(date(2027, 1, 20), salary=True)[0]
    assert abs(s["gross"] - 69000) < 1  # taban + fark değil
    feb = _model(tmpdb, rid, "2027-02", new) * K
    assert abs(s["new"] - feb) < 1 and s["new"] < jan
    a = s["advance"]
    assert a["days"] == 14 and abs(a["first"] - jan) < 0.02 and a["diff"] > 0
    assert "Ocak'ta yatan" in s["text"] and "14 günlük zam farkı" in s["text"]
    # cevap verilmeden şubat da normal yeni tutarla geldi: aynı brüt, ocaktan itibaren
    _paid(client, rid, "2027-02", feb)
    s2 = finance.recurring_drift(date(2027, 2, 20), salary=True)[0]
    assert s2["from_month"] == "2027-01" and abs(s2["gross"] - 69000) < 1


def test_dismiss_this_month_was_different(client, tmpdb):
    rid = _salary(client, gross=100000, start="2026-09")
    _history(client, tmpdb, rid, ["2026-10", "2026-11", "2026-12"])
    _paid(client, rid, "2027-01", _model(tmpdb, rid, "2027-01", []) * K * 1.2)
    assert len(finance.recurring_drift(date(2027, 1, 20), salary=True)) == 1
    assert client.post("/api/recurring/salary-drift/dismiss", json={"recurring_id": rid, "month": "2027-01"}).json()["ok"]
    assert finance.recurring_drift(date(2027, 1, 20), salary=True) == []
    _history(client, tmpdb, rid, ["2027-02"])  # şubat eskisi gibi: soru yok
    assert finance.recurring_drift(date(2027, 2, 20), salary=True) == []


def test_net_defined_salary_january_bracket_reset_is_not_a_raise(client, tmpdb):
    """Netle tanımlı maaş: yıl başında dilim sıfırlanıp net artınca zam sanılmaz (brüt karşılığıyla modellenir)."""
    from app.payroll import gross_for_net, net_by_month

    rid = _salary(client, gross=None, amount=90000, start="2026-08")
    g = gross_for_net(90000, 2026)
    nets = {m["month"]: m["net"] for m in net_by_month(g, 2026)["months"]}
    for m in (9, 10, 11, 12):
        _paid(client, rid, f"2026-{m:02d}", nets[m])
    _paid(client, rid, "2027-01", net_by_month(g, 2027)["months"][0]["net"])
    assert finance.recurring_drift(date(2027, 1, 20), salary=True) == []


def test_api_drift_param_and_capability(client, tmpdb):
    rid = _salary(client, gross=100000, start="2026-09")
    _history(client, tmpdb, rid, ["2026-10", "2026-11", "2026-12"])
    assert client.get("/api/recurring/drift?salary=1").status_code == 200
    from app import version

    assert "salary_raise" in version.CAPABILITIES


def test_net_defined_salary_raise_title_in_net(client, tmpdb):
    from app.payroll import gross_for_net, net_by_month

    rid = _salary(client, gross=None, amount=90000, start="2026-08")
    g = gross_for_net(90000, 2026)
    nets = {m["month"]: m["net"] for m in net_by_month(g, 2026)["months"]}
    for m in (9, 10, 11, 12):
        _paid(client, rid, f"2026-{m:02d}", nets[m])
    jan_old = net_by_month(g, 2027)["months"][0]["net"]
    _paid(client, rid, "2027-01", jan_old * 1.2)
    s = finance.recurring_drift(date(2027, 1, 20), salary=True)[0]
    assert s["title"].startswith("Maaşın %20 arttı (₺") and abs(s["new"] - jan_old * 1.2) < 1
