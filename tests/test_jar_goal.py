"""Kumbara hedef durumu: ayda ne kadar ayırmalı, gerçek hız, yolunda / geride / önde / tarihsiz."""
from datetime import date

from app.jar_goal import jar_goal_status

TODAY = date(2026, 11, 11)


def _jar(goal, balance_moves, deadline=None, unit="TRY", monthly=None, initial=0.0):
    moves = [{"move_date": "2026-06-01", "amount": initial, "note": "Başlangıç"}] if initial else []
    moves += [{"move_date": d, "amount": a, "note": None} for d, a in balance_moves]
    return {"goal": goal, "deadline": deadline, "unit": unit, "monthly": monthly, "closed_at": None, "moves": moves,
            "balance": sum(m["amount"] for m in moves)}


MONTHLY = [("2026-08-05", 2500), ("2026-09-05", 2500), ("2026-10-05", 2500)]


def test_on_track():
    # 40.000 hedef, 12.500 başta + 3×2.500 = 20.000; Temmuz 2027'ye 8 ay → ayda 2.500; hız 2.500 → yolunda
    s = jar_goal_status(_jar(40000, MONTHLY, "2027-07-15", initial=12500), TODAY)
    assert (s["status"], s["left"], s["months_left"], s["needed_monthly"]) == ("yolunda", 20000, 8, 2500)
    assert (s["pace"], s["pace_source"], s["raise_by"], s["eta"]) == (2500, "hareket", None, "2027-07")


def test_behind_tells_how_much_more():
    s = jar_goal_status(_jar(40000, MONTHLY, "2027-03-31", initial=12500), TODAY)
    # 20.000 / 4 ay = 5.000; hız 2.500 → ayda 2.500 artır
    assert (s["status"], s["needed_monthly"], s["raise_by"], s["eta"]) == ("geride", 5000, 2500, "2027-07")


def test_ahead_shows_eta_and_months_early():
    s = jar_goal_status(_jar(40000, MONTHLY, "2028-01-31", initial=12500), TODAY)
    # 14 ay → ayda ~1.430 (yukarı 50'ye: 1.450); hız 2.500 → 8 ayda dolar, 6 ay erken
    assert (s["status"], s["months_left"], s["needed_monthly"]) == ("onde", 14, 1450)
    assert (s["eta"], s["eta_months"], s["months_early"]) == ("2027-07", 8, 6)


def test_no_deadline_estimates_month():
    s = jar_goal_status(_jar(40000, MONTHLY, initial=12500), TODAY)
    assert (s["status"], s["eta"], s["needed_monthly"], s["months_left"]) == ("tarihsiz", "2027-07", None, None)


def test_zero_pace():
    # yalnız başlangıç ayırması var, sonra hiç ayırma yok: hız 0 → geride, artırılacak = gereken tamamı
    s = jar_goal_status(_jar(10000, [], "2027-05-31", initial=4000), TODAY)
    assert (s["status"], s["pace"], s["pace_source"], s["eta"]) == ("geride", 0, None, None)
    assert s["needed_monthly"] == s["raise_by"] == 1000
    # tarihsiz + hız yok: tahmin yok
    s = jar_goal_status(_jar(10000, [], initial=4000), TODAY)
    assert (s["status"], s["eta"]) == ("tarihsiz", None)


def test_plan_used_until_first_real_move():
    s = jar_goal_status(_jar(12000, [], "2027-11-30", monthly=1000), TODAY)
    assert (s["pace"], s["pace_source"], s["status"]) == (1000, "plan", "yolunda")


def test_new_jar_pace_not_diluted_and_withdrawals_count():
    # ilk ayırma Ekim'de: hız Eylül/Ağustos'a bölünmez
    s = jar_goal_status(_jar(10000, [("2026-10-03", 2000), ("2026-11-02", 2000)]), TODAY)
    assert s["pace"] == 2000
    # çekilen düşülür; 3 aydan eski hareket hızı etkilemez
    s = jar_goal_status(_jar(10000, [("2026-03-01", 5000), ("2026-09-01", 3000), ("2026-10-01", -1500)]), TODAY)
    assert s["pace"] == 500  # (3000 − 1500) / 3


def test_deadline_passed_and_done_and_no_goal():
    s = jar_goal_status(_jar(10000, MONTHLY, "2026-10-31"), TODAY)
    assert (s["status"], s["deadline_passed"], s["months_left"], s["needed_monthly"]) == ("geride", True, 0, 2500)
    assert jar_goal_status(_jar(5000, MONTHLY, "2027-01-01"), TODAY)["status"] == "tamam"
    assert jar_goal_status(_jar(None, MONTHLY), TODAY) is None
    assert jar_goal_status({**_jar(5000, MONTHLY), "closed_at": "2026-11-01"}, TODAY) is None


def test_gold_in_grams_with_tl_equivalent():
    buys = [("2026-08-02", 1), ("2026-09-02", 1), ("2026-10-02", 1)]
    s = jar_goal_status(_jar(20, buys, "2027-07-31", unit="ALTIN_24", initial=5), TODAY, unit_price=4123.0)
    # 12 gr kaldı / 8 ay = 1,5 gr/ay; hız 1 gr → 0,5 gr artır
    assert (s["status"], s["unit"], s["left"], s["needed_monthly"], s["raise_by"]) == ("geride", "ALTIN_24", 12, 1.5, 0.5)
    assert (s["needed_monthly_try"], s["raise_by_try"], s["left_try"]) == (6200, 2050, 49500)
    # fiyat yoksa TL karşılığı boş
    assert jar_goal_status(_jar(20, buys, "2027-07-31", unit="ALTIN_24"), TODAY)["needed_monthly_try"] is None


def test_quarter_gold_counts_pieces():
    s = jar_goal_status(_jar(10, [("2026-10-10", 1)], "2027-11-30", unit="CEYREK"), TODAY, unit_price=7000)
    # 9 adet / 12 ay = 0,75 → 0,8 adet/ay; hız 1 adet → 9 ayda, 3 ay erken
    assert (s["status"], s["needed_monthly"], s["months_early"], s["pace_try"]) == ("onde", 0.8, 3, 7000)


def test_api_carries_goal_status(client, tmpdb):
    jid = client.post("/api/jars", json={"name": "Tatil", "goal": 30000, "deadline": "2099-12-31", "initial": 1000}).json()["id"]
    client.post(f"/api/jars/{jid}/moves", json={"amount": 2000})
    gs = client.get("/api/jars").json()[0]["goal_status"]
    assert gs["status"] == "onde" and gs["pace"] == 2000 and gs["pace_source"] == "hareket"
    tl = client.post("/api/jars", json={"name": "Serbest"}).json()["id"]
    assert next(j for j in client.get("/api/jars").json() if j["id"] == tl)["goal_status"] is None
