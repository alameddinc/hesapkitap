"""Öncelik 4: çevrimdışı kuyruğun gönderdiği yazmalar tekrar gelse de çift kayıt açmaz (Idempotency-Key 24 saat; borç
ödemesi ve listeye ekle kalıcı client_key ile) ve kumbara / ön ödemeli kart push hatırlatmaları."""
import time
from datetime import date, timedelta

from app import push
from tests.test_book import _member, owner  # noqa: F401 - fixture


def _expire_keys(tmpdb):
    """Ara katmanın 24 saatlik kaydı silinmiş gibi: kuyruk günler sonra aynı anahtarla gönderdi."""
    from app import idempotency

    with tmpdb.connect() as c:
        c.execute("UPDATE idempotency SET created_at = ?", (time.time() - idempotency.TTL - 5,))
        c.execute("DELETE FROM idempotency")


# ---------------------------------------------------------------- tekrar gönderime dayanıklılık

def test_debt_pay_replay_after_key_expiry_one_payment(client, tmpdb):
    did = client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "borc", "amount": 1000,
                                          "debt_date": "2026-10-01"}).json()["id"]
    h = {"Idempotency-Key": "kuyruk-borc-0001"}
    body = {"amount": 300, "paid_at": "2026-11-10"}
    r1 = client.post(f"/api/debts/{did}/pay", json=body, headers=h)
    r2 = client.post(f"/api/debts/{did}/pay", json=body, headers=h)  # 24 saat içinde: ara katman
    assert r1.status_code == r2.status_code == 200 and r2.headers.get("Idempotent-Replayed") == "true"
    _expire_keys(tmpdb)
    r3 = client.post(f"/api/debts/{did}/pay", json=body, headers=h)  # günler sonra: client_key
    assert r3.status_code == 200 and r3.json()["payment_id"] == r1.json()["payment_id"]
    pays = tmpdb.debt_payments(did)
    assert len(pays) == 1 and pays[0]["amount"] == 300
    assert "client_key" not in pays[0]  # iç kimlik yanıtta yok
    d = next(x for x in client.get("/api/debts").json() if x["id"] == did)
    assert d["remaining"] == 700 and "client_key" not in str(d)
    # yeni anahtar (gerçekten ikinci ödeme) yine yazılır; anahtarsız eski istemci de
    client.post(f"/api/debts/{did}/pay", json=body, headers={"Idempotency-Key": "kuyruk-borc-0002"})
    client.post(f"/api/debts/{did}/pay", json={"amount": 100, "paid_at": "2026-11-10"})
    assert [p["amount"] for p in tmpdb.debt_payments(did)] == [300, 300, 100]


def test_debt_pay_whole_replay_after_close_is_not_rejected(client, tmpdb):
    """"Tamamı" (amount boş) kuyruktan iki kez: borç ilk gönderimde kapandı; tekrar 422 değil, aynı ödeme döner."""
    did = client.post("/api/debts", json={"person": "Örnek Kişi", "direction": "alacak", "amount": 250,
                                          "debt_date": "2026-10-01"}).json()["id"]
    h = {"Idempotency-Key": "kuyruk-borc-0003"}
    r1 = client.post(f"/api/debts/{did}/pay", json={"amount": None, "paid_at": "2026-11-10"}, headers=h)
    _expire_keys(tmpdb)
    r2 = client.post(f"/api/debts/{did}/pay", json={"amount": None, "paid_at": "2026-11-10"}, headers=h)
    assert r1.status_code == r2.status_code == 200 and r1.json()["payment_id"] == r2.json()["payment_id"]
    assert len(tmpdb.debt_payments(did)) == 1
    # aynı anahtar başka borçta başka işlemdir (kimlik borca bağlı)
    did2 = client.post("/api/debts", json={"person": "Başka Kişi", "direction": "alacak", "amount": 90,
                                           "debt_date": "2026-10-01"}).json()["id"]
    _expire_keys(tmpdb)
    assert client.post(f"/api/debts/{did2}/pay", json={"amount": None, "paid_at": "2026-11-10"}, headers=h).status_code == 200
    assert len(tmpdb.debt_payments(did2)) == 1


def test_shopping_add_replay_after_key_expiry_one_row(client, tmpdb):
    h = {"Idempotency-Key": "kuyruk-liste-0001"}
    r1 = client.post("/api/shopping-list", json={"text": "Örnek peynir"}, headers=h)
    r2 = client.post("/api/shopping-list", json={"text": "Örnek peynir"}, headers=h)
    assert r1.status_code == r2.status_code == 200
    _expire_keys(tmpdb)
    r3 = client.post("/api/shopping-list", json={"text": "Örnek peynir"}, headers=h)
    assert r3.status_code == 200 and r3.json()["id"] == r1.json()["id"] and "client_key" not in r3.json()
    items = client.get("/api/shopping-list").json()["items"]
    assert [i["text"] for i in items] == ["Örnek peynir"] and "client_key" not in items[0]
    # aynı ad yeni anahtarla (kişi bilerek ikinci kez ekledi) ayrı satır: eski davranış değişmez
    client.post("/api/shopping-list", json={"text": "Örnek peynir"}, headers={"Idempotency-Key": "kuyruk-liste-0002"})
    assert len(client.get("/api/shopping-list").json()["items"]) == 2


def test_shopping_complete_replay_does_not_make_second_trip(client, tmpdb):
    iid = client.post("/api/shopping-list", json={"text": "Örnek ekmek"}).json()["id"]
    client.patch(f"/api/shopping-list/{iid}", json={"done": True})
    h = {"Idempotency-Key": "kuyruk-tamam-0001"}
    r1 = client.post("/api/shopping-list/complete", json={"carry_over": True}, headers=h)
    assert r1.status_code == 200
    _expire_keys(tmpdb)
    r2 = client.post("/api/shopping-list/complete", json={"carry_over": True}, headers=h)
    assert r2.status_code == 422  # alınan kalmadı: kuyruk bunu hata olarak gösterir, ikinci alışveriş yazılmaz
    assert len(client.get("/api/shopping-trips").json()["trips"]) == 1


def test_bill_pay_replay_keeps_single_entry(client, tmpdb):
    bid = client.post("/api/bills", json={"provider": "Örnek Elektrik", "amount": 410.5, "due_date": "2026-11-10",
                                          "category": "Faturalar"}).json()["id"]
    h = {"Idempotency-Key": "kuyruk-fatura-0001"}
    body = {"amount": 410.5, "paid_at": "2026-11-09", "payment": "nakit"}
    assert client.post(f"/api/bills/{bid}/pay", json=body, headers=h).status_code == 200
    _expire_keys(tmpdb)
    assert client.post(f"/api/bills/{bid}/pay", json=body, headers=h).status_code == 200
    ents = [e for e in tmpdb.list_entries() if e["description"] == "Örnek Elektrik"]
    assert len(ents) == 1 and ents[0]["amount"] == 410.5  # ödeme yazılır, eklenmez: önceki kayıt silinir


def test_statement_pay_replay_sets_same_amount(client, tmpdb, upload):
    upload()
    sid = client.get("/api/statements").json()[0]["id"]
    h = {"Idempotency-Key": "kuyruk-ekstre-0001"}
    body = {"amount": 1234.5, "paid_at": "2026-11-09"}
    assert client.post(f"/api/statements/{sid}/pay", json=body, headers=h).status_code == 200
    _expire_keys(tmpdb)
    assert client.post(f"/api/statements/{sid}/pay", json=body, headers=h).status_code == 200
    st = next(s for s in client.get("/api/statements").json() if s["id"] == sid)
    assert st["paid_amount"] == 1234.5  # toplanmaz, yazılır


def test_bad_key_is_ignored_for_client_key(client, tmpdb):
    from app.api import main

    class R:
        headers = {"idempotency-key": "kısa"}

    assert main._client_key(R()) is None
    R.headers = {"idempotency-key": "gecerli-anahtar-01"}
    assert main._client_key(R()).endswith(":gecerli-anahtar-01")


# ---------------------------------------------------------------- push: kumbara hedef tarihi

TODAY = date(2026, 11, 11)


def _jar(client, **kw):
    body = {"name": "Örnek tatil", "goal": 5000, "deadline": (TODAY + timedelta(days=7)).isoformat(), "initial": 1000, **kw}
    return client.post("/api/jars", json=body).json()["id"]


def test_kumbara_push_seven_days_before_when_behind(client, tmpdb):
    jid = _jar(client)
    got = push._kumbara(TODAY)
    assert len(got) == 1
    it = got[0]
    assert it["kind"] == "kumbara" and it["route"] == f"#/diger/kumbara/{jid}"
    assert it["title"] == "Örnek tatil: hedef tarihine 7 gün kaldı"
    assert "Hedef ₺5.000" in it["body"] and "biriken ₺1.000" in it["body"] and "₺4.000 eksik" in it["body"]
    assert it["id"] == f"kumbara:{jid}:{(TODAY + timedelta(days=7)).isoformat()}:7gun"
    # başka gün yok (tek hatırlatma); hedefe ulaşan, hedefsiz, tarihsiz ve kapalı kumbara yok
    assert push._kumbara(TODAY - timedelta(days=1)) == [] and push._kumbara(TODAY + timedelta(days=1)) == []
    client.post(f"/api/jars/{jid}/moves", json={"amount": 4000})
    assert push._kumbara(TODAY) == []


def test_kumbara_push_skips_without_goal_or_deadline_and_closed(client, tmpdb):
    _jar(client, goal=None)
    _jar(client, deadline=None, name="Tarihsiz")
    jid = _jar(client, name="Kapalı")
    client.patch(f"/api/jars/{jid}", json={"closed": True})
    assert push._kumbara(TODAY) == []


def test_kumbara_push_unit_jar_uses_units(client, tmpdb):
    jid = client.post("/api/jars", json={"unit": "CEYREK", "name": "Örnek çeyrek", "goal": 10,
                                         "deadline": (TODAY + timedelta(days=7)).isoformat()}).json()["id"]
    got = push._kumbara(TODAY)
    assert len(got) == 1 and got[0]["route"].endswith(f"/{jid}") and "₺" not in got[0]["body"]


def test_kumbara_push_hidden_page_for_member(client, tmpdb, monkeypatch):
    from app import book

    _jar(client)
    monkeypatch.setattr(book, "active", lambda: {"hidden_pages": ["hesaplar"], "hidden_accounts": []})
    assert push._kumbara(TODAY) == [] and push._kart_suresi(TODAY) == []


# ---------------------------------------------------------------- push: ön ödemeli kart son kullanma

def _wallet(client, days, balance=750, **kw):
    body = {"name": "Örnek hediye kartı", "card_type": "hediye", "balance": balance,
            "expires_at": (TODAY + timedelta(days=days)).isoformat(), **kw}
    return client.post("/api/wallets", json=body).json()["id"]


def test_card_expiry_push_30_and_7_days(client, tmpdb):
    w30 = _wallet(client, 30)
    w7 = _wallet(client, 7, name="Örnek yemek kartı", card_type="yemek")
    _wallet(client, 10, name="Başka gün")
    _wallet(client, 30, balance=0, name="Boş kart")
    client.post("/api/wallets", json={"name": "Süresiz", "card_type": "yemek", "balance": 300})
    got = {i["id"]: i for i in push._kart_suresi(TODAY)}
    e30, e7 = (TODAY + timedelta(days=30)).isoformat(), (TODAY + timedelta(days=7)).isoformat()
    assert set(got) == {f"kart_suresi:{w30}:{e30}:30gun", f"kart_suresi:{w7}:{e7}:7gun"}
    it = got[f"kart_suresi:{w7}:{e7}:7gun"]
    assert it["kind"] == "kart_suresi" and it["route"] == "#/diger/yemek-kartlari"
    assert it["title"] == "Örnek yemek kartı: son kullanmaya 7 gün kaldı" and "₺750" in it["body"]


def test_new_types_in_prefs_labels(owner):  # noqa: F811
    p = owner.get("/api/push/prefs").json()
    assert p["labels"]["kumbara"] == "Kumbara hedef tarihi" and p["types"]["kumbara"] is True
    assert p["labels"]["kart_suresi"] == "Yemek / hediye kartı son kullanma" and p["types"]["kart_suresi"] is True
    r = owner.put("/api/push/prefs", json={"types": {"kumbara": False}}).json()
    assert r["types"]["kumbara"] is False and r["types"]["kart_suresi"] is True


def test_collect_respects_toggle(client, tmpdb):
    _jar(client)
    _wallet(client, 30)
    on = {"types": {k: True for k in push.TYPES}}
    kinds = {i["kind"] for i in push.collect({"id": 1}, TODAY, on)}
    assert {"kumbara", "kart_suresi"} <= kinds
    off = {"types": {**on["types"], "kumbara": False, "kart_suresi": False}}
    assert not ({"kumbara", "kart_suresi"} & {i["kind"] for i in push.collect({"id": 1}, TODAY, off)})
    assert push.NOUNS["kumbara"] and push.NOUNS["kart_suresi"]
