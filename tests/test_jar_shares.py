"""Kumbara katılımcıları ve aylık pay (yetenek kumbara_shares; app/jar_shares.py). Adlar uydurma."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import jar_shares

CONSENT = ["kosullar", "acik_riza"]
NOV = date(2026, 11, 26)


# ---------------------------------------------------------------- saf hesap

def test_split_equal_with_kurus_remainder_to_one_person():
    ppl = [{"id": 1}, {"id": 2}, {"id": 3}]
    s = jar_shares.split(1000, ppl)
    assert s == {1: 333.34, 2: 333.33, 3: 333.33} and round(sum(s.values()), 2) == 1000
    s = jar_shares.split(100.01, [{"id": i} for i in range(1, 4)])
    assert s == {1: 33.35, 2: 33.33, 3: 33.33}  # 2 kuruş kalan tek kişiye: toplam bozulmaz
    assert round(sum(s.values()), 2) == 100.01


def test_split_manual_share_rest_equal_and_no_target():
    ppl = [{"id": 1, "share": 1500}, {"id": 2}, {"id": 3}]
    assert jar_shares.split(3000, ppl) == {1: 1500.0, 2: 750.0, 3: 750.0}
    assert jar_shares.split(1000, ppl) == {1: 1500.0, 2: 0.0, 3: 0.0}  # elle paylar aylık tutarı aşınca kalan yok
    assert jar_shares.split(None, ppl) == {1: 1500.0, 2: None, 3: None}  # aylık hedef yok: yalnız elle pay


def test_month_target_from_monthly_or_goal_and_deadline():
    assert jar_shares.month_target({"share_monthly": 3000, "moves": []}, "2026-11") == (3000, "aylik")
    # otomatik ayırma tutarı toplam pay değildir (sahibin katkısı)
    assert jar_shares.month_target({"monthly": 3000, "moves": []}, "2026-11") == (None, None)
    jar = {"goal": 36000, "deadline": "2027-04-30",
           "moves": [{"move_date": "2026-10-03", "amount": 6000}, {"move_date": "2026-11-05", "amount": 999}]}
    # ay başında kalan 30.000, Kasım–Nisan 6 ay → 5.000; ay içindeki ekleme bu ayın payını değiştirmez
    assert jar_shares.month_target(jar, "2026-11") == (5000, "hedef")
    assert jar_shares.month_target({"goal": 1000, "deadline": "2026-12-31", "moves": [{"move_date": "2026-10-01", "amount": 1000}]},
                                   "2026-11") == (0.0, "tamam")
    assert jar_shares.month_target({"goal": 1000, "moves": []}, "2026-11") == (None, None)
    # son tarih geçti: kalanın hepsi bu ay
    assert jar_shares.month_target({"goal": 1000, "deadline": "2026-09-30", "moves": []}, "2026-11") == (1000, "hedef")
    # küsuratlı: tam liraya yukarı
    assert jar_shares.month_target({"goal": 1000, "deadline": "2027-01-31", "moves": []}, "2026-11") == (334, "hedef")


# ---------------------------------------------------------------- uçlar (tek kullanıcı, kendi sunucusu)

def _jar(client, **kw):
    return client.post("/api/jars", json={"name": "Annemin birikimi", "goal": 90000, "deadline": "2027-12-31", **kw}).json()["id"]


def _status(client, jid):
    return next(j for j in client.get("/api/jars").json() if j["id"] == jid)


def test_people_shares_contributions_and_status(client, tmpdb, monkeypatch):
    from app.api import main

    monkeypatch.setattr(main, "date", type("D", (date,), {"today": staticmethod(lambda: NOV)}))
    jid = client.post("/api/jars", json={"name": "Annemin birikimi"}).json()["id"]
    client.patch(f"/api/jars/{jid}", json={"share_monthly": 3000})
    assert _status(client, jid)["shares"] is None  # katılımcı yok: eski görünüm
    ids = [client.post(f"/api/jars/{jid}/people", json={"name": n}).json()["id"] for n in ("Ayşe", "Mehmet", "Zeynep")]
    assert client.post(f"/api/jars/{jid}/people", json={"name": "ayşe"}).status_code == 409
    assert client.post(f"/api/jars/{jid}/people", json={}).status_code == 422
    a, m, z = ids
    client.post(f"/api/jars/{jid}/moves", json={"amount": 1000, "person_id": a, "move_date": "2026-11-03"})
    client.post(f"/api/jars/{jid}/moves", json={"amount": 500, "person_id": m, "move_date": "2026-11-05", "outside": True})
    client.post(f"/api/jars/{jid}/moves", json={"amount": 1500, "person_id": z, "move_date": "2026-11-06"})
    sh = _status(client, jid)["shares"]
    assert sh["month"] == "2026-11" and sh["target"] == 3000 and sh["basis"] == "aylik" and sh["total"] == 3000
    by = {p["name"]: p for p in sh["people"]}
    assert by["Ayşe"]["share"] == 1000 and by["Ayşe"]["state"] == "tamam" and by["Ayşe"]["left"] == 0
    assert by["Mehmet"]["state"] == "eksik" and by["Mehmet"]["left"] == 500 and by["Mehmet"]["paid"] == 500
    assert by["Zeynep"]["state"] == "tamam" and by["Zeynep"]["over"] == 500
    assert sh["behind"] == [m] and not sh["all_done"] and sh["paid"] == 3000
    assert all(not p["app"] and not p["me"] for p in sh["people"])
    # elden alınan (outside) harcanabilirden düşmez; kumbara bakiyesinde sayılır
    j = _status(client, jid)
    assert j["balance"] == 3000 and j["outside"] == 500
    assert client.get("/api/net-worth").json()["reserved"] == 2500
    # elle pay: Ayşe 1.500, kalan 1.500 iki kişiye
    client.patch(f"/api/jars/{jid}/people/{a}", json={"share": 1500})
    by = {p["name"]: p for p in _status(client, jid)["shares"]["people"]}
    assert (by["Ayşe"]["share"], by["Mehmet"]["share"], by["Zeynep"]["share"]) == (1500, 750, 750)
    assert by["Ayşe"]["manual"] and by["Ayşe"]["state"] == "eksik" and by["Mehmet"]["state"] == "eksik"
    client.patch(f"/api/jars/{jid}/people/{a}", json={"share": None})  # eşit bölüşe döner
    assert next(p for p in _status(client, jid)["shares"]["people"] if p["id"] == a)["share"] == 1000
    # hareketi düzelt: kim attı + tutar (dokun → düzenle)
    mv = next(x for x in _status(client, jid)["moves"] if x["person_id"] == m)
    assert client.patch(f"/api/jar-moves/{mv['id']}", json={"amount": 1000, "outside": False}).status_code == 200
    j = _status(client, jid)
    assert next(p for p in j["shares"]["people"] if p["id"] == m)["state"] == "tamam" and j["outside"] == 0
    assert client.patch(f"/api/jar-moves/{mv['id']}", json={"person_id": 99999}).status_code == 422
    # ad değişir; aynı ad başka kişide 409
    assert client.patch(f"/api/jars/{jid}/people/{z}", json={"name": "Mehmet"}).status_code == 409
    assert client.patch(f"/api/jars/{jid}/people/{z}", json={"name": "Zeynep Abla"}).status_code == 200
    # hatırlatma günü
    assert client.patch(f"/api/jars/{jid}", json={"remind_day": 20}).status_code == 200
    assert _status(client, jid)["shares"]["remind_day"] == 20
    assert client.patch(f"/api/jars/{jid}", json={"remind_day": 31}).status_code == 422


def test_remove_person_keeps_history_and_readd_restores(client, tmpdb):
    jid = _jar(client)
    a = client.post(f"/api/jars/{jid}/people", json={"name": "Ayşe"}).json()["id"]
    b = client.post(f"/api/jars/{jid}/people", json={"name": "Mehmet"}).json()["id"]
    client.post(f"/api/jars/{jid}/moves", json={"amount": 700, "person_id": a})
    assert client.delete(f"/api/jars/{jid}/people/{b}").json()["result"] == "deleted"  # katkısı yok: silinir
    assert client.delete(f"/api/jars/{jid}/people/{a}").json()["result"] == "removed"  # katkısı var: geçmiş kalır
    j = _status(client, jid)
    assert j["shares"] is None  # aktif kişi kalmadı
    assert client.post(f"/api/jars/{jid}/moves", json={"amount": 10, "person_id": a}).status_code == 422
    assert client.post(f"/api/jars/{jid}/people", json={"name": "Ayşe"}).json()["id"] == a  # geri gelir
    sh = _status(client, jid)["shares"]
    assert sh["people"][0]["total"] == 700


def test_unit_jar_and_closed_jar_reject_people(client, tmpdb):
    gold = client.post("/api/jars", json={"name": "Düğün altını", "unit": "ALTIN_24"}).json()["id"]
    assert client.post(f"/api/jars/{gold}/people", json={"name": "Ayşe"}).status_code == 422
    jid = _jar(client)
    client.patch(f"/api/jars/{jid}", json={"closed": True})
    assert client.post(f"/api/jars/{jid}/people", json={"name": "Ayşe"}).status_code == 422
    assert client.post("/api/jars/9999/people", json={"name": "Ayşe"}).status_code == 404


def test_goal_based_target_and_history_counts_full_months(client, tmpdb):
    from app import db

    jid = _jar(client, goal=36000, deadline="2027-04-30")
    a = client.post(f"/api/jars/{jid}/people", json={"name": "Ayşe"}).json()["id"]
    b = client.post(f"/api/jars/{jid}/people", json={"name": "Mehmet"}).json()["id"]
    with db.connect() as c:  # iki ay önce eklenmiş gibi
        c.execute("UPDATE jar_people SET created_at = '2026-09-02 10:00:00' WHERE jar_id = ?", (jid,))
    # Eylül hedefi: 36000 / 8 ay = 4500 → kişi başı 2250
    for pid, amt, d in ((a, 2250, "2026-09-10"), (b, 1000, "2026-09-12"), (a, 2500, "2026-10-08"), (b, 2500, "2026-10-09")):
        client.post(f"/api/jars/{jid}/moves", json={"amount": amt, "person_id": pid, "move_date": d})
    jar = next(j for j in db.list_jars(NOV) if j["id"] == jid)
    st = jar_shares.status(jar, NOV)
    # Kasım: ay başında kalan 36000 − 8250 = 27750, 6 ay → 4625 → 2312,50 + 2312,50
    assert st["target"] == 4625 and st["basis"] == "hedef"
    by = {p["id"]: p for p in st["people"]}
    assert by[a]["share"] == 2312.5 and by[b]["share"] == 2312.5
    assert (by[a]["months_full"], by[a]["months_counted"]) == (2, 2)  # Eylül ve Ekim tam
    assert (by[b]["months_full"], by[b]["months_counted"]) == (1, 2)  # Eylül eksik
    assert st["history_months"] == 2


def test_missing_person_falls_back_to_adder_or_owner(client, tmpdb):
    """Eski istemci: kim attı boşsa ekleyen (created_by) ya da sahip sayılır; ekleme bozulmaz."""
    jid = _jar(client, goal=None, deadline=None)
    client.patch(f"/api/jars/{jid}", json={"share_monthly": 1000})
    a = client.post(f"/api/jars/{jid}/people", json={"name": "Ben"}).json()["id"]
    client.post(f"/api/jars/{jid}/people", json={"name": "Ayşe"})
    assert client.post(f"/api/jars/{jid}/moves", json={"amount": 400}).status_code == 200  # eski gövde
    sh = _status(client, jid)["shares"]
    assert next(p for p in sh["people"] if p["id"] == a)["paid"] == 400
    # çekim kimseye yazılmaz, payı düşürmez
    client.post(f"/api/jars/{jid}/moves", json={"amount": 100, "direction": "cek", "person_id": a})
    assert next(p for p in _status(client, jid)["shares"]["people"] if p["id"] == a)["paid"] == 400


# ---------------------------------------------------------------- ortak defter: yetki, adlar, bildirim

@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _member(owner, name="esim", role=None):
    from app.api.main import app

    body = {"note": name} | ({"book_role": role} if role else {})
    inv = owner.post("/api/book/invites", json=body).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"code": inv["code"], "username": name, "password": f"{name}-sifre-12345"}).status_code == 200
    return m


def test_book_member_permissions_and_names(owner):
    from app import system

    m = _member(owner, "esim")
    v = _member(owner, "kizim", role="viewer")
    me = system.user_by_name("esim")["id"]
    adm = system.user_by_name("admin")["id"]
    jid = owner.post("/api/jars", json={"name": "Annemin birikimi"}).json()["id"]
    owner.patch(f"/api/jars/{jid}", json={"share_monthly": 2000})
    po = owner.post(f"/api/jars/{jid}/people", json={"user_id": adm}).json()["id"]
    pm = owner.post(f"/api/jars/{jid}/people", json={"user_id": me}).json()["id"]
    pa = owner.post(f"/api/jars/{jid}/people", json={"name": "Ayşe", "share": 500}).json()["id"]
    assert owner.post(f"/api/jars/{jid}/people", json={"user_id": 4242}).status_code == 400  # defterde değil
    # üye katılımcı ekleyemez / payı değiştiremez (sahip ya da tam yetkili)
    assert m.post(f"/api/jars/{jid}/people", json={"name": "Zeynep"}).status_code == 403
    assert m.patch(f"/api/jars/{jid}/people/{pa}", json={"share": 100}).status_code == 403
    # üye kendi katkısını ve uygulamayı kullanmayanın katkısını ekler; sahibin adına ekleyemez
    assert m.post(f"/api/jars/{jid}/moves", json={"amount": 750, "person_id": pm}).status_code == 200
    assert m.post(f"/api/jars/{jid}/moves", json={"amount": 500, "person_id": pa, "outside": True}).status_code == 200
    assert m.post(f"/api/jars/{jid}/moves", json={"amount": 100, "person_id": po}).status_code == 403
    assert v.post(f"/api/jars/{jid}/moves", json={"amount": 100}).status_code == 403  # sadece görür
    jar = next(j for j in m.get("/api/jars").json() if j["id"] == jid)
    by = {p["id"]: p for p in jar["shares"]["people"]}
    assert by[pm]["me"] and by[pm]["app"] and by[pm]["name"] == "esim" and by[po]["name"] == "admin"
    assert (by[po]["share"], by[pm]["share"], by[pa]["share"]) == (750, 750, 500)
    assert by[pm]["state"] == "tamam" and by[pa]["state"] == "tamam" and by[po]["state"] == "bekliyor"
    assert "people" not in jar  # ham satırlar istemciye gitmez
    # üye yalnız kendi eklediği hareketi düzeltir
    mine = next(x for x in jar["moves"] if x["person_id"] == pm)
    assert m.patch(f"/api/jar-moves/{mine['id']}", json={"amount": 800}).status_code == 200
    assert m.patch(f"/api/jar-moves/{mine['id']}", json={"person_id": po}).status_code == 403
    oid = owner.post(f"/api/jars/{jid}/moves", json={"amount": 300, "person_id": po}).json()["id"]
    assert m.patch(f"/api/jar-moves/{oid}", json={"amount": 1}).status_code == 403
    assert m.delete(f"/api/jar-moves/{mine['id']}").status_code == 403  # üye silemez
    assert v.get("/api/jars").status_code == 200


def test_push_only_to_app_user_with_own_share_left(owner):
    from app import db, push, system

    _member(owner, "esim")
    esim = system.user_by_name("esim")
    adm = system.user_by_name("admin")
    jid = owner.post("/api/jars", json={"name": "Annemin birikimi"}).json()["id"]
    owner.patch(f"/api/jars/{jid}", json={"share_monthly": 3000})
    pa = owner.post(f"/api/jars/{jid}/people", json={"user_id": adm["id"]}).json()["id"]
    owner.post(f"/api/jars/{jid}/people", json={"user_id": esim["id"]})
    owner.post(f"/api/jars/{jid}/people", json={"name": "Ayşe"})
    owner.post(f"/api/jars/{jid}/moves", json={"amount": 1000, "person_id": pa, "move_date": "2026-11-02"})

    def run(u, day):
        with db.use_space(push._space_of(u), u):
            return push._kumbara_pay(u, date(2026, 11, day))

    assert run(esim, 24) == []  # hatırlatma günü 25
    got = run(esim, 25)
    assert len(got) == 1 and got[0]["kind"] == "kumbara_pay" and got[0]["route"] == f"#/diger/kumbara/{jid}"
    assert got[0]["id"] == f"kumbara_pay:{jid}:2026-11" and got[0]["title"] == "Annemin birikimi: bu ayki payın"
    assert "Bu ay payın ₺1.000" in got[0]["body"]
    assert run(adm, 25) == []  # sahibin payı tamam; Ayşe'nin payı için sahibe bildirim yok
    owner.patch(f"/api/jars/{jid}", json={"remind_day": 10})
    assert len(run(esim, 12)) == 1
    with db.use_space(push._space_of(esim), esim):
        assert "kumbara_pay" in push.TYPES and push.get_prefs(esim["id"])["types"]["kumbara_pay"] is True


def test_partial_share_push_text(owner):
    from app import db, push, system

    adm = system.user_by_name("admin")
    jid = owner.post("/api/jars", json={"name": "Ortak hedef"}).json()["id"]
    owner.patch(f"/api/jars/{jid}", json={"share_monthly": 2000})
    pa = owner.post(f"/api/jars/{jid}/people", json={"user_id": adm["id"]}).json()["id"]
    owner.post(f"/api/jars/{jid}/people", json={"name": "Mehmet"})
    owner.post(f"/api/jars/{jid}/moves", json={"amount": 400, "person_id": pa, "move_date": "2026-11-02"})
    with db.use_space(push._space_of(adm), adm):
        got = push._kumbara_pay(adm, date(2026, 11, 27))
    assert len(got) == 1 and "payından ₺600 kaldı; ₺400 eklendi" in got[0]["body"]
    assert "faiz" not in got[0]["body"].lower()


def test_free_plan_can_use_but_not_set_up_people(client, monkeypatch):
    from app import auth, system
    from app.api import main
    from app.api.main import app

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"})
    inv = client.post("/api/admin/invites", json={}).json()
    c = TestClient(app)
    assert c.post("/api/auth/register", json={"consent": CONSENT, "code": inv["code"], "username": "deniz@ornek.com",
                                              "password": "deniz-sifre-12345"}).status_code == 200
    u = next(x for x in system.users() if x["username"] == "deniz@ornek.com")
    with db_space(u):
        from app import db

        jid = db.create_jar({"name": "Eski kumbara", "share_monthly": 1000})
        pid = db.add_jar_person(jid, "Ayşe")
    r = c.post(f"/api/jars/{jid}/people", json={"name": "Mehmet"})
    assert r.status_code == 402 and r.json()["plan_required"] == "kumbara"
    assert c.patch(f"/api/jars/{jid}/people/{pid}", json={"share": 100}).status_code == 402
    assert c.post(f"/api/jars/{jid}/moves", json={"amount": 300, "person_id": pid}).status_code == 200  # kullanmak serbest
    assert c.get("/api/jars").json()[0]["shares"]["people"][0]["paid"] == 300
    assert c.delete(f"/api/jars/{jid}/people/{pid}").status_code == 200  # çıkarmak serbest


def db_space(u):
    from app import db, system

    return db.use_space(system.user_db_path(u), u)


def test_needed_note_when_total_below_goal_pace(client, tmpdb):
    """Ayda toplam hedefin gerektirdiğinden azsa needed (hedef kutusundaki "ayda ₺… ayır" ile aynı rakam)."""
    jid = _jar(client, goal=120000, deadline=f"{date.today().year + 1}-12-31")
    client.patch(f"/api/jars/{jid}", json={"share_monthly": 1000})
    client.post(f"/api/jars/{jid}/people", json={"name": "Ayşe", "share": 400})
    client.post(f"/api/jars/{jid}/people", json={"name": "Mehmet"})
    j = _status(client, jid)
    need = j["goal_status"]["needed_monthly"]
    assert need > 1000 and j["shares"]["needed"] == need and j["shares"]["total"] == 1000
    # "Toplamı yap": elle pay korunur, kalan eşit
    client.patch(f"/api/jars/{jid}", json={"share_monthly": need})
    sh = _status(client, jid)["shares"]
    assert sh["needed"] is None and sh["total"] == need
    assert [p["share"] for p in sh["people"]] == [400, need - 400]
    # hedeften hesaplanan toplamda ya da son tarihsiz kumbarada not yok
    client.patch(f"/api/jars/{jid}", json={"share_monthly": None})
    assert _status(client, jid)["shares"]["needed"] is None
    k = _jar(client, name="Tarihsiz", deadline=None)
    client.patch(f"/api/jars/{k}", json={"share_monthly": 100})
    client.post(f"/api/jars/{k}/people", json={"name": "Ayşe"})
    assert _status(client, k)["shares"]["needed"] is None
