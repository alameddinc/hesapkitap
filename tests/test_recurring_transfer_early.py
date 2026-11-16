"""Giden havale → düzenli ödeme (kira): adı ve kişisi kalemden farklı olsa da kategori ya da açıklama, tutar ve vade
penceresi (vadeden 10 gün önce – 15 gün sonra) tutuyorsa o dönemin ödemesi sayılır; ay dönümünden bir gün önce yatan
kira sonraki dönemin kirasıdır (dönemi orası: istatistik, Bu ay, Ödemelerden sonra). Ödemeler "Bu ay ödendi"de
(yetenek payables_recurring) hareket bağlantısıyla görünür. "Eksik tutar" gibi küçük ek ödeme sonraki dönemin yerini
kapmaz. Kişiler, IBAN'lar ve tutarlar uydurmadır; tarihler bugüne göre."""
from datetime import date, timedelta

from app import finance

ACC = "Ornek Banka"
DAY = 17  # ay dönümü ve kira günü aynı: kira dönemin ilk günü vadeli
RENT = 20000.0


def _setup(tmpdb, client, *, desc="Ev kirası", category="Kira & Aidat", amount=RENT):
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(DAY)
    cur = finance.current_period()
    r = client.post("/api/recurring", json={"description": desc, "amount": amount, "kind": "gider", "category": category,
                                            "day_of_month": DAY, "start_month": finance.add_months(cur, -3)})
    assert r.status_code == 200, r.text
    return r.json()["id"], cur


def _row(d: date, h: str, amount: float, note: str = "SOKAK KIRA") -> dict:
    return {"date": d.isoformat(), "description": f"{note},Deniz Ornekci,Ornek Bankasi A.S.,TR000000000000000000000099",
            "amount": -amount, "type": "gider", "hash": h, "balance": None}


def _entry(tmpdb, h: str) -> dict:
    return next(e for e in tmpdb.list_entries() if e["import_hash"] == h)


def _start(month: str) -> date:
    return finance.period_bounds(month)[0]


def test_early_rent_transfer_counts_for_next_period(tmpdb, client):
    """Dönemin son günü (vadeden bir gün önce) yatan kira: o dönemin değil SONRAKİ dönemin kirası; bekleyen değil."""
    rid, cur = _setup(tmpdb, client)
    prev = finance.add_months(cur, -1)
    paid = _start(cur) - timedelta(days=1)  # önceki dönemin son günü
    assert tmpdb.period_of(paid.isoformat()) == prev
    tmpdb.import_bank_rows([_row(paid, "k1", RENT)], account=ACC)
    e = _entry(tmpdb, "k1")
    assert e["category"] == "Kira & Aidat" and e["counterparty"]  # kişiye giden havale, adı kalemden farklı
    assert e["recurring_id"] == rid and e["month"] == cur

    # istatistik / Bu ay: bu dönemde sayılır, önceki dönemde yok
    def rent_in(month):
        return sum(r["amount"] for r in finance.transactions(month) if r["category"] == "Kira & Aidat" and not r.get("pending"))
    assert rent_in(cur) == RENT and rent_in(prev) == 0
    # bu dönemin kirası bekleyen değil (Özet bekleyen gider ve Ödemelerden sonra)
    assert not [r for r in finance.transactions(cur) if r.get("pending") and r.get("recurring_id") == rid]
    nw = client.get("/api/net-worth").json()
    assert not [x for x in nw["recurring_items"] if x["recurring_id"] == rid]
    # Ay sonunda kalacak (plan / expected) da bu dönemin kirasını ikinci kez düşmez; gerçekleşen giderde bir kez
    exp = client.get("/api/summary", params={"month": cur}).json()["expected"]
    assert exp["parts"]["pending_recurring_expense"] == 0
    row = next(m for m in finance.plan(2)["months"] if m["month"] == cur)
    assert not [ln for ln in row["lines"] if ln.get("recurring_id") == rid and not ln.get("confirmed")]

    # Ödemeler: yalnız isteyen istemciye, "ödendi" + erken + hareket bağlantısı; açık toplama girmez
    old = client.get("/api/payables", params={"totals": 1}).json()
    assert not [x for x in old["items"] if x["type"] == "recurring"]
    new = client.get("/api/payables", params={"totals": 1, "recurring": 1}).json()
    it = next(x for x in new["items"] if x["type"] == "recurring")
    assert it["status"] == "odendi" and it["in_total"] is False and it["early"] is True
    assert it["entry_uid"] == f"m{e['id']}" and it["recurring_id"] == rid and it["month"] == cur
    assert it["paid_amount"] == RENT and it["paid_at"] == paid.isoformat() and it["due_date"] == _start(cur).isoformat()
    assert new["totals"]["open"] == old["totals"]["open"]
    if paid.isoformat()[:7] == date.today().isoformat()[:7]:
        assert new["totals"]["paid_this_month"]["count"] == old["totals"]["paid_this_month"]["count"] + 1
    assert "payables_recurring" in client.get("/api/auth/status").json()["capabilities"]


def test_small_top_up_does_not_take_next_period(tmpdb, client):
    """Önceki dönemin kirası + birkaç gün sonra "eksik tutar" tamamlaması: küçük ödeme sonraki dönemin yerini kapmaz;
    sonraki dönemin asıl kirası (erken de olsa) o döneme bağlanır."""
    rid, cur = _setup(tmpdb, client)
    prev = finance.add_months(cur, -1)
    p0 = _start(prev) + timedelta(days=1)
    tmpdb.import_bank_rows([_row(p0, "a1", RENT - 3000), _row(p0 + timedelta(days=2), "a2", 3000.0, "KIRA EKSIK TUTAR"),
                            _row(_start(cur) - timedelta(days=1), "a3", RENT)], account=ACC)
    assert (_entry(tmpdb, "a1")["recurring_id"], _entry(tmpdb, "a1")["month"]) == (rid, prev)
    top = _entry(tmpdb, "a2")
    assert top["recurring_id"] is None and top["month"] == prev  # tek seferlik, tarihinin döneminde
    assert (_entry(tmpdb, "a3")["recurring_id"], _entry(tmpdb, "a3")["month"]) == (rid, cur)


def test_same_name_top_up_stays_one_off(tmpdb, client):
    """Ad kuralıyla (kalemle aynı ad) bağlanan kayıtta da: kendi dönemi doluysa küçük ek ödeme komşu döneme kaymaz."""
    rid, cur = _setup(tmpdb, client, desc="Kira")
    mid = _start(cur) + timedelta(days=1)
    a = tmpdb.create_entry({"entry_date": mid.isoformat(), "description": "Kira", "amount": RENT, "kind": "gider"})
    b = tmpdb.create_entry({"entry_date": (mid + timedelta(days=2)).isoformat(), "description": "Kira", "amount": 2000.0,
                            "kind": "gider"})
    es = {e["id"]: e for e in tmpdb.list_entries()}
    assert (es[a]["recurring_id"], es[a]["month"]) == (rid, cur)
    assert es[b]["recurring_id"] is None and es[b]["month"] == cur
    # tam tutarlı ikinci kira (iki dönem birden ödendi) eskisi gibi komşu döneme bağlanır
    c = tmpdb.create_entry({"entry_date": (mid + timedelta(days=3)).isoformat(), "description": "Kira", "amount": RENT,
                            "kind": "gider"})
    assert {e["id"]: e for e in tmpdb.list_entries()}[c]["recurring_id"] == rid


def test_not_linked_when_rule_does_not_hold(tmpdb, client):
    """Bağlanmaz: tutar çok farklı (aidat), vade penceresi dışında, kredi kartıyla ödenmiş, başka kategori."""
    rid, cur = _setup(tmpdb, client)
    s = _start(cur)
    tmpdb.import_bank_rows([_row(s, "n1", 900.0, "AIDAT"),
                            # önceki vadeden 18 gün sonra (gecikme penceresi 15), bu vadeden 12–13 gün önce (erken 10)
                            _row(_start(finance.add_months(cur, -1)) + timedelta(days=18), "n2", RENT)], account=ACC)
    assert _entry(tmpdb, "n1")["recurring_id"] is None
    assert _entry(tmpdb, "n2")["recurring_id"] is None
    card = tmpdb.create_entry({"entry_date": s.isoformat(), "description": "Ornek Emlak", "amount": RENT, "kind": "gider",
                               "category": "Kira & Aidat", "payment": "kredi_karti"})
    other = tmpdb.create_entry({"entry_date": s.isoformat(), "description": "Deniz Ornekci", "amount": RENT, "kind": "gider",
                                "category": "Eğitim", "payment": "havale", "counterparty": "Deniz Ornekci"})
    es = {e["id"]: e for e in tmpdb.list_entries()}
    assert es[card]["recurring_id"] is None and es[other]["recurring_id"] is None


def test_backfill_links_transfer_made_before_rule_or_item(tmpdb, client):
    """Kalem havaleden SONRA açıldı (ya da havale kuraldan önce yüklendi): kalem açılınca ve içe aktarma sonunda son
    45 günün bağsız havaleleri bağlanır; daha eskisine dokunulmaz. İdempotent."""
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(DAY)
    cur = finance.current_period()
    paid = _start(cur) - timedelta(days=1)
    tmpdb.import_bank_rows([_row(paid, "b1", RENT)], account=ACC)
    assert _entry(tmpdb, "b1")["recurring_id"] is None  # henüz kalem yok
    old_m = finance.add_months(cur, -3)
    old = tmpdb.create_entry({"entry_date": _start(old_m).isoformat(), "description": "Deniz Ornekci", "amount": RENT,
                              "kind": "gider", "category": "Kira & Aidat", "payment": "havale", "counterparty": "Deniz Ornekci"})
    r = client.post("/api/recurring", json={"description": "Ev kirası", "amount": RENT, "kind": "gider",
                                            "category": "Kira & Aidat", "day_of_month": DAY,
                                            "start_month": finance.add_months(cur, -4)})
    rid = r.json()["id"]
    e = _entry(tmpdb, "b1")
    assert e["recurring_id"] == rid and e["month"] == cur
    assert {x["id"]: x for x in tmpdb.list_entries()}[old]["recurring_id"] is None  # 45 günden eski
    assert tmpdb.link_recurring_transfers() == 0


def test_manual_confirm_then_bank_transfer_is_one_record(tmpdb, client):
    """Kira elle "ödendi" onaylandı, sonra dökümden farklı adla (kişi adı) geldi: aynı kayıt olur, iki kez sayılmaz."""
    rid, cur = _setup(tmpdb, client)
    assert client.post(f"/api/recurring/{rid}/confirm", json={"month": cur}).status_code == 200
    tmpdb.import_bank_rows([_row(_start(cur) + timedelta(days=1), "c1", RENT)], account=ACC)
    linked = [e for e in tmpdb.list_entries() if e["recurring_id"] == rid and e["month"] == cur]
    assert len(linked) == 1 and linked[0]["import_hash"] == "c1"
    assert len([e for e in tmpdb.list_entries() if e["kind"] == "gider"]) == 1
