"""Simülasyon bulguları S3 (dekontlu aktarım bağlama onayı), S6 (fatura Ödendi hesabı), S8 (dekont adı) — ters yön ve
kenar hâller. Kişiler, IBAN'lar, tutarlar UYDURMADIR."""
from __future__ import annotations

from datetime import date

from app import dekont, finance, finance_audit, version
from tests.test_gercek_gun_tekrari import freeze
from tests.test_hesaplar_arasi_transfer import HALK_IBAN, NAME, VAKIF_IBAN, _accounts, _counts, _current


def _bal(day: date) -> dict[str, float]:
    return {a["name"]: a["current"] for a in finance.net_worth(day)["accounts"]}


def _ids(tmpdb) -> dict[str, int]:
    with tmpdb.connect() as c:
        return {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM accounts")}


# ---------------------------------------------------------------- S3

def _resolve(amount: float, on: str, ref: str) -> dict:
    from app import dekont_parse

    return dekont_parse.resolve({"sender_name": NAME, "receiver_name": NAME, "receiver_iban": VAKIF_IBAN,
                                 "sender_iban": HALK_IBAN, "amount": amount, "date": on, "ref": ref,
                                 "doc_direction": "giden"})


def test_s3_dunku_elle_aktarim_okumada_soru_onayla_baglanir(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 22))
    halk, vakif = _accounts(tmpdb)
    first = client.post("/api/own-transfers", json={"date": "2026-11-21", "amount": 5000, "from_account_id": halk,
                                                    "to_account_id": vakif}).json()
    out = _resolve(5000.0, "2026-11-22", "7700001234")
    assert out["own"]["match"] is None
    m = out["own"]["maybe_match"]
    assert m["id"] == first["id"] and m["date"] == "2026-11-21" and "bağla?" in m["message"]
    # kullanıcı "Bağla" dedi: yeni kayıt açılmaz, dekont dünkü aktarıma bağlanır
    r = client.post("/api/own-transfers", json={"date": "2026-11-22", "amount": 5000, "from_account_id": halk,
                                                "to_account_id": vakif, "ref": "7700001234", "dekont_side": "giden",
                                                "link_to": m["id"]}).json()
    assert r["linked"] is True and r["id"] == first["id"] and r["created"] == 0
    assert _counts(tmpdb) == (2, 0)
    assert _current(tmpdb, "Halkbank") == 195000 and _current(tmpdb, "VakıfBank") == 6000


def test_s3_yeni_aktarim_secilince_ikinci_kayit_ve_bilgi(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 22))
    halk, vakif = _accounts(tmpdb)
    first = client.post("/api/own-transfers", json={"date": "2026-11-21", "amount": 5000, "from_account_id": halk,
                                                    "to_account_id": vakif}).json()
    r = client.post("/api/own-transfers", json={"date": "2026-11-22", "amount": 5000, "from_account_id": halk,
                                                "to_account_id": vakif, "ref": "7700001234",
                                                "dekont_side": "giden"}).json()
    assert r["linked"] is False and r["created"] == 2 and r["maybe_match"]["id"] == first["id"]
    assert _counts(tmpdb) == (4, 0)


def test_s3_ayni_gun_tek_aday_yine_kendiliginden_baglanir(client, tmpdb, monkeypatch):
    """Ters yön (eski davranış korunur): aynı gün, tek aday → sormadan bağlanır."""
    freeze(monkeypatch, date(2026, 11, 22))
    halk, vakif = _accounts(tmpdb)
    first = client.post("/api/own-transfers", json={"date": "2026-11-22", "amount": 5000, "from_account_id": halk,
                                                    "to_account_id": vakif}).json()
    assert _resolve(5000.0, "2026-11-22", "7700001234")["own"]["match"]["id"] == first["id"]
    r = client.post("/api/own-transfers", json={"date": "2026-11-22", "amount": 5000, "from_account_id": halk,
                                                "to_account_id": vakif, "ref": "7700001234",
                                                "dekont_side": "giden"}).json()
    assert r["linked"] is True and r["created"] == 0 and "maybe_match" not in r


def test_s3_ayni_gun_iki_aday_sorulur(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 22))
    halk, vakif = _accounts(tmpdb)
    for _ in range(2):
        client.post("/api/own-transfers", json={"date": "2026-11-22", "amount": 5000, "from_account_id": halk,
                                                "to_account_id": vakif})
    out = _resolve(5000.0, "2026-11-22", "7700001234")
    assert out["own"]["match"] is None and out["own"]["maybe_match"]


def test_s3_link_to_uyusmazsa_422(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 22))
    halk, vakif = _accounts(tmpdb)
    other = client.post("/api/own-transfers", json={"date": "2026-11-21", "amount": 700, "from_account_id": halk,
                                                    "to_account_id": vakif}).json()
    r = client.post("/api/own-transfers", json={"date": "2026-11-22", "amount": 5000, "from_account_id": halk,
                                                "to_account_id": vakif, "ref": "7700001234", "link_to": other["id"]})
    assert r.status_code == 422
    assert _counts(tmpdb) == (2, 0)


# ---------------------------------------------------------------- S6

def _bill(client, amount=640.0, due="2026-11-22") -> int:
    return client.post("/api/bills", json={"provider": "Örnek Elektrik", "amount": amount, "due_date": due}).json()["id"]


def test_s6_hesabimdan_degil_bakiye_degismez_denetim_sormaz(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 20))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-15", "elle")
    bid = _bill(client)
    r = client.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": "2026-11-20", "payment": "havale",
                                                   "account_id": None})
    assert r.status_code == 200 and "anchor_conflict" not in r.json()
    assert _bal(date(2026, 11, 20)) == {"Örnek Banka A": 10000.0}
    assert next(b for b in client.get("/api/bills").json() if b["id"] == bid)["paid_via"] == "disarida"
    s = finance_audit.snapshot(date(2026, 11, 20))
    assert not finance_audit.hesapsiz_hareket(s)


def test_s6_anahtar_yok_belirsiz_odeme_kart_kuraliyla(client, tmpdb, monkeypatch):
    """Hesap söylenmemiş fatura ödemesi = ekstrenin belirsiz ödemesi: unknown_payments_items type fatura, güncel çapa
    ödemeden önceyse free/spendable'dan düşülür, Özet uyarısı belirsiz_odeme:f<id>, denetim ikinci kez sormaz."""
    freeze(monkeypatch, date(2026, 11, 20))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-15", "elle")
    bid = _bill(client)
    assert client.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": "2026-11-20",
                                                      "payment": "havale"}).status_code == 200
    n = finance.net_worth(date(2026, 11, 20))
    assert _bal(date(2026, 11, 20)) == {"Örnek Banka A": 10000.0}
    it = [x for x in n["unknown_payments_items"] if x["type"] == "fatura"]
    assert len(it) == 1 and it[0]["bill_id"] == bid and it[0]["deducted"] is True
    assert n["unknown_payments"] == 640.0 and n["free"] == 9360.0
    assert not finance_audit.hesapsiz_hareket(finance_audit.snapshot(date(2026, 11, 20)))
    p = next(x for x in finance.payables(date(2026, 11, 20)) if x["type"] == "bill" and x["id"] == bid)
    assert p["account_unknown"] is True
    w = next(x for x in finance.insights("2026-11", date(2026, 11, 20)) if x.get("key", "").startswith(f"belirsiz_odeme:f{bid}:"))
    assert "faturasını" in w["detail"] and w["pay"] == {"type": "bill", "id": bid}
    # hesap sonradan seçilince belirsiz kalem kapanır, bakiye düşer
    aid = _ids(tmpdb)["Örnek Banka A"]
    client.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": "2026-11-20", "account_id": aid})
    n = finance.net_worth(date(2026, 11, 20))
    assert not n["unknown_payments_items"] and _bal(date(2026, 11, 20)) == {"Örnek Banka A": 9360.0}


def test_s6_belirsiz_fatura_capa_sonra_ya_da_karsilik_varsa_dusulmez(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 20))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-21", "elle")  # bakiye ödemeden SONRA güncel
    bid = _bill(client)
    client.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": "2026-11-20", "payment": "havale"})
    it = finance.net_worth(date(2026, 11, 21))["unknown_payments_items"]
    assert [(x["type"], x["deducted"], x["reason"]) for x in it] == [("fatura", False, "capa_sonra")]
    # kartla / nakitle ödenen fatura belirsiz değil
    b2 = _bill(client, 99.0)
    client.post(f"/api/bills/{b2}/pay", json={"amount": 99.0, "paid_at": "2026-11-20", "payment": "kredi_karti"})
    assert len(finance.net_worth(date(2026, 11, 21))["unknown_payments_items"]) == 1


def test_s6_yeniden_odemede_hesap_korunur_ve_geri_al(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 20))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-15", "elle")
    aid = _ids(tmpdb)["Örnek Banka A"]
    bid = _bill(client)
    client.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": "2026-11-20", "account_id": aid})
    with tmpdb.connect() as c:
        assert c.execute("SELECT payment FROM entries").fetchone()[0] == "havale"  # ödeme yolu verilmedi
    client.post(f"/api/bills/{bid}/pay", json={"amount": 700.0, "paid_at": "2026-11-20"})  # tutar düzeltildi
    assert _bal(date(2026, 11, 20)) == {"Örnek Banka A": 9300.0}
    assert client.delete(f"/api/bills/{bid}/pay").status_code == 200
    assert _bal(date(2026, 11, 20)) == {"Örnek Banka A": 10000.0}


def test_s6_kartla_hesap_422_ve_bilinmeyen_hesap(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 20))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-15", "elle")
    aid = _ids(tmpdb)["Örnek Banka A"]
    bid = _bill(client)
    assert client.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": "2026-11-20",
                                                      "payment": "kredi_karti", "account_id": aid}).status_code == 422
    assert client.post(f"/api/bills/{bid}/pay", json={"amount": 640.0, "paid_at": "2026-11-20",
                                                      "account_id": 9999}).status_code in (404, 422)
    assert client.post("/api/bills/9999/pay", json={"amount": 1.0, "paid_at": "2026-11-20"}).status_code == 404
    with tmpdb.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0


def test_s6_capa_gunu_cevabi(client, tmpdb, monkeypatch):
    """Ödeme günü = çapa günü: true → bakiyede zaten var (düşmez), false → sonra (düşer). Çapa gününden önceki gün
    ödeme + false → düşer (eski kuralda hiç düşmezdi). Daha yeni bakiye girilince cevap geçersiz (o bakiye içerir)."""
    freeze(monkeypatch, date(2026, 11, 20))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-20", "elle")
    aid = _ids(tmpdb)["Örnek Banka A"]
    b1, b2, b3 = _bill(client, 100.0), _bill(client, 200.0), _bill(client, 400.0)
    r = client.post(f"/api/bills/{b1}/pay", json={"amount": 100.0, "paid_at": "2026-11-20", "account_id": aid,
                                                  "in_balance": True}).json()
    assert r["anchor_conflict"]["account_id"] == aid
    client.post(f"/api/bills/{b2}/pay", json={"amount": 200.0, "paid_at": "2026-11-20", "account_id": aid,
                                              "in_balance": False})
    client.post(f"/api/bills/{b3}/pay", json={"amount": 400.0, "paid_at": "2026-11-19", "account_id": aid,
                                              "in_balance": False})
    assert _bal(date(2026, 11, 20)) == {"Örnek Banka A": 9400.0}
    # zekât geçmiş bakiyesi aynı kuralla: 19 Kas sonu = çapa − çapanın İÇİNDE sayılan 20 Kas hareketleri (yalnız b1)
    from app import zakat

    assert zakat.account_balances_at(date(2026, 11, 19))[aid] == 10100.0
    client.patch(f"/api/accounts/{aid}", json={"balance": 9000.0, "as_of": "2026-11-20"})
    assert _bal(date(2026, 11, 20)) == {"Örnek Banka A": 9000.0}


def test_s6_yetenek():
    assert "bill_pay_account" in version.CAPABILITIES and "own_transfer_maybe" in version.CAPABILITIES


# ---------------------------------------------------------------- S8

def test_s8_title_turkce_kurali():
    assert dekont._title("ORNEK EV SAHIBI") == "Ornek Ev Sahibi"
    assert dekont._title("ELİF ŞAHİN") == "Elif Şahin"
    assert dekont._title("İSMAİL KIRAÇ") == "İsmail Kıraç"
    assert dekont._title("Zaten Düzgün") == "Zaten Düzgün"


def test_s6_odenmis_acilan_fatura_hesapli(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 20))
    tmpdb.set_account_balance("Örnek Banka A", 10000.0, "2026-11-15", "elle")
    aid = _ids(tmpdb)["Örnek Banka A"]
    body = {"provider": "Örnek Su", "amount": 250.0, "due_date": "2026-11-25", "paid_at": "2026-11-20"}
    assert client.post("/api/bills", json={**body, "payment": "kredi_karti", "account_id": aid}).status_code == 422
    assert not client.get("/api/bills").json()  # yarım fatura açılmadı
    r = client.post("/api/bills", json={**body, "payment": "havale", "account_id": aid})
    assert r.status_code == 200 and r.json()["entry_id"]
    assert _bal(date(2026, 11, 20)) == {"Örnek Banka A": 9750.0}
