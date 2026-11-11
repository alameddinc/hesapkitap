"""Borç planı birikimi kumbarada (yetenek debt_jar; Premium "kumbara", plan ücretsiz): "Kumbara aç", "Kumbaraya
ayır", plan takibinde gerçek bakiye, "Kumbaradan öde" (tam / kısmi / fazla), geri al, kredi erken kapatma, paket ve
ortak defter yetkileri. Kişiler, bankalar ve tutarlar uydurmadır."""
from datetime import date

import pytest

from tests.test_book import _member, owner  # noqa: F401  (owner: fixture)
from tests.test_debt_plan import _all_strings, _banned_in

TODAY = date(2026, 11, 11)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    import app.api.main as api
    import app.db as dbmod
    import app.debt_plan as dp

    class D(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(dbmod, "date", D)
    monkeypatch.setattr(dp, "date", D)
    monkeypatch.setattr(api, "date", D)


@pytest.fixture(autouse=True)
def _no_price_download(monkeypatch):
    from app import zakat

    def fail():
        raise RuntimeError("testte fiyat indirilmez")

    monkeypatch.setattr(zakat, "_download_prices", fail)


def _tek(c, amount=12000, due="2027-02-15", person="Deniz"):
    r = c.post("/api/debts", json={"person": person, "direction": "borc", "amount": amount, "debt_date": "2026-09-01",
                                   "due_date": due, "pay_terms": "tek"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _loan(c, payoff=78000):
    body = {"person": "Örnek Bank", "direction": "borc", "plan_installment": 5000, "plan_count": 24,
            "plan_start": "2026-06", "plan_day": 5, "creditor_type": "banka", "plan_interest": "var",
            "payoff_amount": payoff, "payoff_date": "2026-11-11"}
    r = c.post("/api/debts", json=body)
    assert r.status_code == 200, r.text
    did = r.json()["id"]
    for i in range(5):
        assert c.post(f"/api/debts/{did}/pay", json={"amount": 5000, "paid_at": f"2026-{6 + i:02d}-05"}).status_code == 200
    return did


def _plan(c, monthly=None):
    return c.get("/api/debt-plan" + (f"?monthly={monthly}" if monthly else "")).json()


def _debt_item(p, did):
    return next(d for d in p["debts"] if d["kind"] != "card" and d["id"] == did)


def _jar(c, jid):
    return next(j for j in c.get("/api/jars").json() if j["id"] == jid)


def _debt(c, did):
    return next(d for d in c.get("/api/debts").json() if d["id"] == did)


def _put(c, jid, amount, on):
    r = c.post(f"/api/jars/{jid}/moves", json={"amount": amount, "move_date": on, "note": "Borç planı"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- aç → ayır → plan gerçek bakiyeyle

def test_offer_open_and_one_open_jar_per_debt(client):
    did = _tek(client)
    client.put("/api/debt-plan", json={"monthly": 3000})
    p = _plan(client)
    assert "debt_jar" in client.get("/api/meta").json()["capabilities"]
    assert p["debt_jar"]["can_open"] and p["debt_jar"]["lock"] is None
    o = _debt_item(p, did)["jar"]
    assert o["state"] == "offer" and o["label"] == "Kumbara aç"
    assert (o["name"], o["goal"], o["deadline"], o["monthly"]) == ("Deniz borcu", 12000, "2027-02-28", 3000)
    r = client.post(f"/api/debts/{did}/jar", json={})
    assert r.status_code == 200, r.text
    j = r.json()["jar"]
    # aylık saklanmaz: ay ay tutarlar borç planından (tek kaynak)
    assert (j["name"], j["goal"], j["deadline"], j["monthly"], j["auto_day"], j["debt_id"]) == \
        ("Deniz borcu", 12000, "2027-02-28", None, None, did)
    assert j["debt"]["label"] == "Borç: Deniz için" and j["debt"]["state"] == "acik" and j["debt"]["suggestion"] is None
    assert o["plan_text"] == "Kasım 2026–Şubat 2027 ayda ₺3.000 · Şubat 2027'de kumbaradan öde"
    assert r.json()["message"] == 'Kumbara açıldı. Bu ay ₺3.000 ayır: "Kumbaraya ayır"a dokun.'
    # kumbara kartında kendi aylık hesabı yok (planla çelişmesin); planın durumu
    assert j["goal_status"] is None and "need_per_month" not in j
    assert j["debt"]["plan"]["status"] == "bu_ay" and j["debt"]["plan"]["line"] == "Bu ay ₺3.000 ayır"
    assert j["debt"]["plan"]["detail"] == "Kasım 2026–Şubat 2027 ayda ₺3.000 · Şubat 2027'de kumbaradan öde"
    assert client.post(f"/api/debts/{did}/jar", json={}).status_code == 409
    # kumbara kapatılınca yenisi açılabilir; eskisi yeniden açılmak istenirse çakışma
    client.patch(f"/api/jars/{j['id']}", json={"closed": True})
    j2 = client.post(f"/api/debts/{did}/jar", json={"name": "Deniz için"}).json()["id"]
    assert client.patch(f"/api/jars/{j['id']}", json={"closed": False}).status_code == 409
    assert _jar(client, j2)["name"] == "Deniz için"


def test_not_offered_for_parca_or_card(client):
    did = client.post("/api/debts", json={"person": "Komşu", "direction": "borc", "amount": 5000,
                                          "debt_date": "2026-09-01", "pay_terms": "parca"}).json()["id"]
    client.put("/api/debt-plan", json={"monthly": 1000})
    assert _debt_item(_plan(client), did)["jar"] is None
    r = client.post(f"/api/debts/{did}/jar", json={})
    assert r.status_code == 422 and "tek seferde" in r.json()["detail"]
    assert client.post("/api/debts/999/jar", json={}).status_code == 404


def test_put_aside_uses_real_balance_and_recalculates(client):
    did = _tek(client)
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    p = _plan(client)
    row = next(i for i in p["this_month"]["items"] if i["key"] == f"borc:{did}" and not i["done"])
    assert row["jar_action"] == {"label": "Kumbaraya ayır", "jar_id": jid, "amount": 3000, "note": "Borç planı"}
    # tek dokunuş: bu ayın birikimi kumbaraya
    _put(client, jid, row["jar_action"]["amount"], "2026-11-11")
    p = _plan(client)
    items = [i for i in p["this_month"]["items"] if i["key"] == f"borc:{did}"]
    assert [(i["done"], i["amount"], i["text"]) for i in items] == [(True, 3000, "Bu ay kumbaraya ₺3.000 ayrıldı")]
    assert p["debt_jar"]["saved_this_month"] == 3000
    it = _debt_item(p, did)
    assert it["jar"]["state"] == "linked" and it["jar"]["balance"] == 3000 and it["jar"]["pay"] is None
    assert it["jar"]["text"] == "Kumbarada ₺3.000 birikti · hedef ₺12.000"
    # geçmiş aylar (gerçek bakiye): kumbarada 7.000 varsa kalan 5.000 → sonraki aylarda daha az birikim
    _put(client, jid, 4000, "2026-10-20")
    p = _plan(client)
    months = {m["month"]: {x["key"]: x for x in m["payments"]} for m in p["months"]}
    assert "2026-11" not in months or f"borc:{did}" not in months["2026-11"]
    assert months["2026-12"][f"borc:{did}"]["amount"] == 3000 and months["2026-12"][f"borc:{did}"]["saved"] == 10000
    jan = months["2027-01"][f"borc:{did}"]
    assert jan["closes"] and jan["amount"] == 2000 and jan["from_saved"] == 10000  # Ocak'ta kapanır (Şubat yerine)
    assert _debt_item(p, did)["closes"]["month"] == "2027-01"


def test_no_jar_old_behaviour_and_jar_deleted_reverts(client):
    did = _tek(client)
    client.put("/api/debt-plan", json={"monthly": 3000})
    before = _plan(client)
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    _put(client, jid, 6000, "2026-10-01")
    assert _plan(client)["months"] != before["months"]
    client.delete(f"/api/jars/{jid}")  # kumbara silindi: borç etkilenmez, plan eski davranış
    after = _plan(client)
    assert after["months"] == before["months"] and _debt_item(after, did)["jar"]["state"] == "offer"
    assert _debt(client, did)["remaining"] == 12000


def test_commitment_uses_real_jar_balance(client):
    did = _tek(client)
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    assert client.post("/api/debt-plan/commit", json={}).status_code == 200
    c = _plan(client)["commitment"]
    assert c["status"]["key"] == "ok" and c["expected"] == 3000 and c["paid"] == 0
    _put(client, jid, 3000, "2026-11-11")
    c = _plan(client)["commitment"]
    assert c["paid"] == 3000 and c["status"]["text"] == "Bu ayın ödemesi tamam."
    assert [(s["jar_id"], s["amount"], s["text"]) for s in c["saved"]] == [(jid, 3000, "Deniz için kumbarada ₺3.000")]


# ---------------------------------------------------------------- kumbaradan öde: tam / kısmi / fazla, geri al

def test_pay_full_from_jar_and_undo(client):
    did = _tek(client)
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    _put(client, jid, 12000, "2026-11-02")
    p = _plan(client)
    pay = _debt_item(p, did)["jar"]["pay"]
    assert pay["label"] == "Kumbaradan öde" and pay["amount"] == 12000 and not pay["partial"] and pay["leftover"] == 0
    row = next(i for i in p["this_month"]["items"] if i["key"] == f"borc:{did}" and not i["done"])
    assert row["from_saved"] == 12000 and row["jar_pay"] == pay  # bu ay birikimle tek seferde
    r = client.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["paid"], out["from_jar"], out["leftover"], out["jar_closed"], out["debt_closed"], out["ask"]) == \
        (12000, 12000, 0, True, True, None)
    assert out["message"] == "Borç kumbaradan ödendi; kumbara kapandı"
    j = _jar(client, jid)
    assert j["closed_at"] == "2026-11-11" and j["balance"] == 0 and j["debt"]["state"] == "kapandi"
    assert j["debt"]["suggestion"] is None  # kumbara kapalı: öneri yok
    assert _debt(client, did)["settled_at"] == "2026-11-11"
    assert client.get("/api/net-worth").json()["reserved"] == 0
    # geri al: ödeme silinir, kumbara hareketi geri gelir, kumbara yeniden açılır
    assert client.delete(f"/api/debt-payments/{out['payment_id']}").json()["deleted"]
    j = _jar(client, jid)
    assert j["closed_at"] is None and j["balance"] == 12000 and len(j["moves"]) == 1
    assert _debt(client, did)["remaining"] == 12000
    assert client.get("/api/net-worth").json()["reserved"] == 12000


def test_pay_partial_when_due_and_undo(client):
    did = _tek(client, due="2026-11-30")  # ödeme ayı geldi
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    _put(client, jid, 5000, "2026-10-01")
    pay = _debt_item(_plan(client), did)["jar"]["pay"]
    assert pay["partial"] and pay["amount"] == 5000
    assert pay["confirm"] == ("Deniz: kumbaradaki ₺5.000 ödensin mi? Kalan ₺7.000 borçta kalır; kumbara boşalır, "
                              "kalanı için ayırmaya devam edersin.")
    out = client.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"}).json()
    assert (out["paid"], out["debt_closed"], out["debt_left"], out["jar_closed"]) == (5000, False, 7000, False)
    assert "borçta ₺7.000 kaldı" in out["message"]
    j = _jar(client, jid)
    assert j["closed_at"] is None and j["balance"] == 0 and j["goal"] == 7000 and j["debt"]["remaining"] == 7000
    # plan kalan 7.000 için birikime devam eder (kumbara bağlı kalır)
    assert _debt_item(_plan(client), did)["jar"]["state"] == "linked"
    client.delete(f"/api/debt-payments/{out['payment_id']}")
    j = _jar(client, jid)
    assert j["balance"] == 5000 and j["goal"] == 12000 and _debt(client, did)["remaining"] == 12000


def test_pay_with_surplus_asks_then_convert(client):
    did = _tek(client, amount=10000)
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    _put(client, jid, 11500, "2026-11-01")
    pay = _debt_item(_plan(client), did)["jar"]["pay"]
    assert pay["amount"] == 10000 and pay["leftover"] == 1500 and "artan ₺1.500 kumbarada kalır" in pay["confirm"]
    out = client.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"}).json()
    assert (out["from_jar"], out["leftover"], out["jar_closed"], out["debt_closed"]) == (10000, 1500, False, True)
    assert out["ask"]["text"] == ("Borç kapandı; kumbarada ₺1.500 kaldı. Kumbarayı kapatırsan para serbest kalır; "
                                  "istersen başka bir hedefe çevir.")
    j = _jar(client, jid)
    assert j["balance"] == 1500 and j["debt"]["state"] == "kapandi"
    assert j["debt"]["suggestion"]["text"] == "Borç kapandı: kumbarayı kapat ya da başka bir hedefe çevir."
    assert j["goal_status"] is None  # "ayda ₺… ayır" yanıltmasın
    # fazla ödeme yazılamaz
    assert client.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"}).status_code == 422
    # başka hedefe çevir: bağ kalkar, bakiye kalır
    assert client.patch(f"/api/jars/{jid}", json={"unlink_debt": True, "name": "Tatil", "goal": 5000}).status_code == 200
    j = _jar(client, jid)
    assert j["debt"] is None and j["debt_id"] is None and j["name"] == "Tatil" and j["balance"] == 1500


def test_debt_deleted_keeps_jar_with_suggestion(client):
    did = _tek(client)
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    _put(client, jid, 2000, "2026-11-01")
    client.delete(f"/api/debts/{did}")
    j = _jar(client, jid)
    assert j["balance"] == 2000 and j["debt_id"] is None and j["debt"]["state"] == "silindi"
    assert j["debt"]["label"] == "Borç: Deniz için"
    assert j["debt"]["suggestion"]["text"] == "Borç silindi: kumbarayı kapat ya da başka bir hedefe çevir."
    assert client.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"}).status_code == 422
    # yeni bir borç aynı kimliği alsa da kumbara ona bağlanmaz
    nd = _tek(client, person="Ece")
    assert _debt_item(_plan(client), nd)["jar"]["state"] == "offer"


def test_unpay_all_restores_jar(client):
    did = _tek(client, amount=4000)
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    _put(client, jid, 4000, "2026-11-01")
    client.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"})
    client.delete(f"/api/debts/{did}/pay")
    j = _jar(client, jid)
    assert j["closed_at"] is None and j["balance"] == 4000


# ---------------------------------------------------------------- kredi erken kapatma

def test_loan_payoff_jar_and_close_from_jar(client):
    did = _loan(client)  # kalan 95.000, kapatma 78.000
    client.put("/api/debt-plan", json={"monthly": 20000})
    p = _plan(client)
    it = _debt_item(p, did)
    assert it["payoff"]["state"] == "plan"
    o = it["jar"]
    assert o["state"] == "offer" and o["name"] == "Örnek Bank kredisini kapat" and o["goal"] == 78000
    assert o["deadline"][:7] == it["payoff"]["month"] and o["monthly"] > 0
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    p = _plan(client)
    row = next(i for i in p["this_month"]["items"] if i["key"] == f"borc:{did}:kapat")
    assert row["saving"] and row["jar_action"]["jar_id"] == jid
    taksit = next(i for i in p["this_month"]["items"] if i["key"] == f"borc:{did}")
    assert "jar_action" not in taksit and "jar_pay" not in taksit  # kumbara yalnız erken kapatma için
    _put(client, jid, 60000, "2026-10-01")
    p2 = _plan(client)
    assert p2["months"] != p["months"]  # birikim kumbaradan başlar: kapatma ayı öne gelir
    assert _debt_item(p2, did)["payoff"]["month"] <= it["payoff"]["month"]
    _put(client, jid, 18000, "2026-11-02")
    pay = _debt_item(_plan(client), did)["jar"]["pay"]
    # plan bu ay kapatıyor: tutar planın satırıyla aynı (bu ayın taksitinden sonraki tahmini kapatma tutarı)
    row = next(i for i in _plan(client)["this_month"]["items"] if i["key"] == f"borc:{did}:kapat")
    assert pay["close_early"] and pay["amount"] == row["pay_total"] == pytest.approx(73894.74) and pay["from_jar"] == pay["amount"]
    assert row["jar_pay"] == pay and "close_early" in row  # eski istemci yine "Kapattım" görür
    r = client.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"})
    assert r.status_code == 422  # kredide tutar zorunlu (bankanın kapatma tutarı)
    out = client.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11", "amount": 77500}).json()
    assert out["close_early"] and out["debt_closed"] and out["leftover"] == 500 and out["message"] == "Kredi kumbaradan kapandı"
    d = _debt(client, did)
    assert d["settled_at"] == "2026-11-11" and d["amount"] == 25000 + 77500
    client.delete(f"/api/debt-payments/{out['payment_id']}")
    j = _jar(client, jid)
    assert j["balance"] == 78000 and j["closed_at"] is None
    assert _debt(client, did)["settled_at"] is None


# ---------------------------------------------------------------- Premium, ortak defter, değer ilkesi

def test_free_user_sees_neutral_lock_and_402(client, monkeypatch):
    from tests.test_plans import _rc, _user, hosted  # noqa: F401

    from app import auth
    from app.api import main

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    monkeypatch.setenv("HESAPKITAP_HOSTED", "1")
    monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH", "Bearer rc-gizli")
    auth._fails.clear()
    main._signups.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    c, u = _user(client)
    did = _tek(c)
    c.put("/api/debt-plan", json={"monthly": 3000})
    p = _plan(c)
    assert p["debt_jar"]["premium"] is False and p["debt_jar"]["can_open"] is False
    lock = _debt_item(p, did)["jar"]
    assert lock == {"state": "locked", "label": "Kumbarayla takip et", "badge": "Premium",
                    "text": "Bu birikimi kumbarada ayrı tutmak Premium'da. Plan birikimi bugünkü gibi gösterir."}
    assert p["debt_jar"]["lock"] == lock
    # plan ücretsiz: birikim yine planda
    assert _debt_item(p, did)["saving"]["text"].startswith("Deniz için biriktir")
    r = c.post(f"/api/debts/{did}/jar", json={})
    assert r.status_code == 402 and r.json()["plan_required"] == "kumbara"
    _rc(client, "INITIAL_PURCHASE", u["id"])
    assert c.post(f"/api/debts/{did}/jar", json={}).status_code == 200
    jid = _debt_item(_plan(c), did)["jar"]["id"]
    # Premium bitse de var olan kumbara kullanılır (ayır ve kumbaradan öde serbest), yenisi kilitli
    _rc(client, "EXPIRATION", u["id"], exp_ms=1000)
    p = _plan(c)
    assert p["debt_jar"]["premium"] is False and _debt_item(p, did)["jar"]["state"] == "linked"
    assert c.post(f"/api/jars/{jid}/moves", json={"amount": 12000, "move_date": "2026-11-01"}).status_code == 200
    assert c.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"}).status_code == 200


def test_shared_book_member_rights(owner):  # noqa: F811
    did = _tek(owner)
    owner.put("/api/debt-plan", json={"monthly": 3000})
    viewer = _member(owner, "goren", "viewer")
    editor = _member(owner, "ekleyen", "editor")
    full = _member(owner, "tam", "full")
    pv = _plan(viewer)
    assert pv["debt_jar"]["can_open"] is False and pv["debt_jar"]["can_move"] is False and pv["debt_jar"]["can_pay"] is False
    assert _debt_item(pv, did)["jar"] is None  # açamaz: öneri yok
    assert viewer.post(f"/api/debts/{did}/jar", json={}).status_code == 403
    assert editor.post(f"/api/debts/{did}/jar", json={}).status_code == 403  # yeni kumbara sahibin (mevcut kural)
    r = full.post(f"/api/debts/{did}/jar", json={})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    pe = _plan(editor)
    assert pe["debt_jar"]["can_move"] and not pe["debt_jar"]["can_pay"]
    row = next(i for i in pe["this_month"]["items"] if i["key"] == f"borc:{did}" and not i["done"])
    assert row["jar_action"]["jar_id"] == jid  # ekleyen üye ayırır (ortak kumbara)
    assert editor.post(f"/api/jars/{jid}/moves", json={"amount": 12000, "move_date": "2026-11-02"}).status_code == 200
    assert viewer.post(f"/api/jars/{jid}/moves", json={"amount": 100}).status_code == 403
    row = next(i for i in _plan(viewer)["this_month"]["items"] if i["key"] == f"borc:{did}" and not i["done"])
    assert "jar_action" not in row and "jar_pay" not in row
    assert _debt_item(_plan(editor), did)["jar"]["pay"] is None  # borç ödemesi sahibin
    assert editor.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"}).status_code == 403
    assert viewer.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"}).status_code == 403
    assert _debt_item(_plan(owner), did)["jar"]["pay"]["amount"] == 12000
    assert full.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"}).status_code == 200


def test_hidden_jars_page_member_sees_old_plan(owner):  # noqa: F811
    did = _tek(owner)
    owner.put("/api/debt-plan", json={"monthly": 3000})
    jid = owner.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    _put(owner, jid, 6000, "2026-10-01")
    owner.put("/api/book/rules", json={"hidden_pages": ["hesaplar"]})
    m = _member(owner, "esim", "editor")
    p = _plan(m)
    assert p["debt_jar"]["available"] is False and _debt_item(p, did)["jar"] is None
    sav = next(x for x in p["months"][0]["payments"] if x["key"] == f"borc:{did}")
    assert sav["saved"] == 3000  # kumbara görünmüyor: eski davranış (birikim plandan)


def test_spendable_still_deducts_jar(client):
    client.post("/api/accounts", json={"name": "Ana hesap", "kind": "banka", "balance": 50000})
    did = _tek(client)
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    _put(client, jid, 3000, "2026-11-11")
    nw = client.get("/api/net-worth").json()
    parts = {x["key"]: x["amount"] for x in nw["spendable_parts"]}
    assert nw["reserved"] == 3000 and parts["reserved"] == 3000


def test_no_interest_steering_words_in_jar_texts(client):
    from app import debt_plan as dp

    did = _tek(client, due="2026-11-30")
    loan = _loan(client)
    client.put("/api/debt-plan", json={"monthly": 20000})
    texts = _all_strings(_plan(client))
    for d in (did, loan):
        jid = client.post(f"/api/debts/{d}/jar", json={}).json()["id"]
        _put(client, jid, 90000, "2026-11-01")
    texts += _all_strings(_plan(client)) + _all_strings(client.get("/api/jars").json())
    jid = _debt_item(_plan(client), did)["jar"]["id"]
    texts += _all_strings(client.post(f"/api/jars/{jid}/pay-debt", json={"paid_at": "2026-11-11"}).json())
    texts += _all_strings(client.get("/api/jars").json())
    texts += [dp.JAR_LOCK["text"], dp.JAR_LOCK["label"], dp.JAR_OFFER_TEXT, dp.JAR_PAY_LABEL, dp.JAR_MOVE_LABEL]
    bad = {t: _banned_in(t) for t in texts if _banned_in(t)}
    assert not bad, bad
    assert any("Kumbaradan öde" in t for t in texts) and any("kumbara" in t.lower() for t in texts)


def test_old_client_pays_debt_directly_jar_suggests_close(client):
    """Eski istemci kumbarayı bilmez: borcu Ödedim'le kapatır; kumbara silinmez, nötr öneri görünür."""
    did = _tek(client, amount=3000)
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    _put(client, jid, 1000, "2026-11-01")
    assert client.post(f"/api/debts/{did}/pay", json={"paid_at": "2026-11-11"}).status_code == 200
    j = _jar(client, jid)
    assert j["closed_at"] is None and j["balance"] == 1000 and j["debt"]["state"] == "kapandi"
    assert j["debt"]["suggestion"]["close"] == "Kumbarayı kapat" and j["debt"]["suggestion"]["convert"] == "Başka hedefe çevir"
    p = _plan(client)
    assert all(d["id"] != did for d in p["debts"] if d["kind"] != "card")  # borç kapandı: planda yok
    assert client.patch(f"/api/jars/{jid}", json={"closed": True}).status_code == 200
    assert _jar(client, jid)["debt"]["suggestion"] is None


def test_extra_deposit_does_not_starve_this_months_installment(client):
    """Bu ay kumbaraya planın ayırdığından fazlası konursa (birikmiş parayla) fazlası aylık tutardan sayılmaz: bu ayın
    taksiti yine planda, "taksitlere yetmiyor" uyarısı çıkmaz; fazlası kumbarada birikim olarak sayılır."""
    loan = _loan(client)
    client.put("/api/debt-plan", json={"monthly": 20000})
    jid = client.post(f"/api/debts/{loan}/jar", json={}).json()["id"]
    _put(client, jid, 40000, "2026-11-02")
    p = _plan(client)
    assert p["fixed_short"] is None
    taksit = next(i for i in p["this_month"]["items"] if i["key"] == f"borc:{loan}" and not i["done"])
    assert taksit["amount"] == 5000
    # planın bu ayki birikimi (15.000) kumbaradan karşılandı: bu ay ayrıca ayırma yok, fazlası birikimde
    rows = [(i["amount"], i["done"], i["text"]) for i in p["this_month"]["items"] if i["key"] == f"borc:{loan}:kapat"]
    assert rows == [(40000, True, "Bu ay kumbaraya ₺40.000 ayrıldı")]
    assert p["this_month"]["budget"] == 5000 and p["months"][0]["saved"] == 40000
    assert p["debt_jar"]["saved_this_month"] == 40000


def test_plan_starts_later_neutral_status_and_free_put(client, tmpdb):
    """Plan bu ay bu borca birikim ayırmıyorsa (önce kart): kumbara "Plan … başlıyor" der, "Yolunda" demez; toast
    planın takvimini söyler; isteyen yine ayırır (tutar sorulur, önerilen yok)."""
    from tests.test_debt_payoff import _card_stmt

    _card_stmt(tmpdb, total=25000, due="2026-11-20")
    did = _tek(client)
    client.put("/api/debt-plan", json={"monthly": 6000})
    p = _plan(client)
    assert all(i["key"] != f"borc:{did}" for i in p["this_month"]["items"])  # bu ay Deniz'e birikim yok
    r = client.post(f"/api/debts/{did}/jar", json={}).json()
    plan = r["jar"]["debt"]["plan"]
    assert plan["status"] == "baslamadi" and plan["this_month"] == 0 and plan["badge"] is None
    assert plan["line"].startswith("Plan ") and "başlıyor" in plan["line"]
    assert r["message"].startswith("Kumbara açıldı. Plan Deniz için ") and r["message"].endswith(" ayırır.")
    linked = _debt_item(_plan(client), did)["jar"]
    assert linked["put"] == {"label": "Kumbaraya ayır", "jar_id": r["id"], "note": "Borç planı", "amount": None}
    assert linked["plan"] == plan
    # bu ay kendisi ayırırsa: "Bu ay ₺1.000 ayırdın", plan sonraki ayları azaltır
    _put(client, r["id"], 1000, "2026-11-11")
    assert _jar(client, r["id"])["debt"]["plan"]["line"] == "Bu ay ₺1.000 ayırdın"


def test_spendable_counts_debt_and_its_jar_once(client):
    """Borca bağlı kumbaradaki para borcun kalanından düşülmüş sayılır: harcanabilirden borç kalanı kadar düşer
    (kumbara ayrıca düşmez); kumbara borçtan büyükse kumbara kadar. Bağlı olmayan kumbara eskisi gibi."""
    client.post("/api/accounts", json={"name": "Ana hesap", "kind": "banka", "balance": 50000})
    did = _tek(client)  # 12.000
    client.put("/api/debt-plan", json={"monthly": 3000})
    jid = client.post(f"/api/debts/{did}/jar", json={}).json()["id"]
    base = client.get("/api/net-worth").json()
    assert base["spendable"] == 50000 - 12000
    _put(client, jid, 5000, "2026-11-11")
    nw = client.get("/api/net-worth").json()
    parts = {x["key"]: x for x in nw["spendable_parts"]}
    assert nw["spendable"] == 50000 - 12000  # 7.000 borç + 5.000 kumbara
    assert parts["payable"]["amount"] == 7000 and parts["reserved"]["amount"] == 5000
    assert parts["payable"]["label"] == "TL borçların (kumbarada ayrılan kısmı hariç)"
    assert "iki kez düşülmez" in nw["spendable_explain"]
    assert nw["payable"] == 12000 and nw["net"] == base["net"]  # net durum değişmez
    _put(client, jid, 9000, "2026-11-11")  # kumbara 14.000 > borç 12.000
    assert client.get("/api/net-worth").json()["spendable"] == 50000 - 14000
    other = client.post("/api/jars", json={"name": "Tatil", "initial": 2000}).json()["id"]
    assert other and client.get("/api/net-worth").json()["spendable"] == 50000 - 14000 - 2000
