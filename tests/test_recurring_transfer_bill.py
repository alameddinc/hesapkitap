"""Sabit kalem yapılan havale (EFT/FAST) ve fatura: kayıtlar Transferler ve Faturalar'da aynen kalır.

Kullanıcı sorusu: "bir EFT'yi ya da faturayı sabit kalem yaparsam kayıtlarda bozulma olur mu?" Sabit kaleme bağlama
yalnız recurring_id yazar; kişi (counterparty) ve fatura kurumu (bill_provider) kayıtta durur. Dökümden önce elle
"onaylanan" sabit kalem, döküm gelince aynı harekete bağlanır (tek kayıt) ve kişi/kurum dökümden dolar. Sahte veriler.
"""
from datetime import date

from app import finance

ACC = "Garanti BBVA"


def _months(n: int) -> list[str]:
    """Bu aydan önceki n ay (eskiden yeniye): öneri kuralı bugüne göre çalışır."""
    cur = finance.ym(date.today())
    return [finance.add_months(cur, -i) for i in range(n, 0, -1)]


def _rent(d: str, h: str, amount: float = 15000.0) -> dict:
    return {"date": d, "description": "Kira,Hale Yildiz,Türkiye Is Bankasi A.S.,TR77000640000000000000001",
            "amount": -amount, "type": "gider", "hash": h, "balance": None}


def _bill(d: str, h: str, amount: float, n: int) -> dict:
    desc = (f"Vodafone Tahsilatı Telefon Numarası:5000000001, Ad Soyad/Unvan: E******** K****, Fatura No: FD00000A00{n:04d}, "
            f"Son Ödeme Tarihi:{d[8:10]}.{d[5:7]}.{d[:4]}, Vodafone Tahsilatı")
    return {"date": d, "description": desc, "amount": -amount, "type": "gider", "hash": h, "balance": None}


def _history(tmpdb, months):
    rows = []
    for i, m in enumerate(months):
        rows += [_rent(f"{m}-10", f"k{i}"), _bill(f"{m}-12", f"v{i}", 1200.0 + i * 10, i)]
    tmpdb.import_bank_rows(rows, account=ACC)


def _accept(client, kind_key_part: str) -> int:
    sug = client.get("/api/recurring/suggestions").json()
    s = next(x for x in sug if kind_key_part in x["key"])
    return client.post("/api/recurring/suggestions/accept", json={"key": s["key"]}).json()["id"]


def _transfer_ids(client) -> set[int]:
    return {i["id"] for i in client.get("/api/transfers").json()["items"]}


def _bill_items(client, until: str) -> dict[int, dict]:
    s = client.get("/api/bills/summary", params={"until": until, "months": 12}).json()
    out = {}
    for p in s["providers"]:
        for m in p["months"]:
            for it in m["items"]:
                out[it["id"]] = {**it, "provider": p["name"]}
    return out


def _snap(tmpdb):
    return {e["id"]: {k: e[k] for k in ("entry_date", "amount", "counterparty", "bill_provider", "description", "category")}
            for e in tmpdb.list_entries()}


def test_accepting_transfer_and_bill_as_recurring_keeps_records(client, tmpdb):
    """(a) öneriden sabit kalem: kayıtlar Transferler ve Faturalar'da aynen kalır, tutar/tarih değişmez."""
    months = _months(3)
    _history(tmpdb, months)
    before = _snap(tmpdb)
    tr_before, bills_before = _transfer_ids(client), _bill_items(client, months[-1])
    assert len(tr_before) == 3 and len(bills_before) == 3

    rent_id = _accept(client, "p:")
    bill_id = _accept(client, "m:")

    after = _snap(tmpdb)
    assert after == before  # tutar, tarih, kişi, kurum, açıklama, kategori aynı
    ents = {e["id"]: e for e in tmpdb.list_entries()}
    assert {ents[i]["recurring_id"] for i in tr_before} == {rent_id}
    assert {ents[i]["recurring_id"] for i in bills_before} == {bill_id}
    assert _transfer_ids(client) == tr_before
    bills_after = _bill_items(client, months[-1])
    assert bills_after.keys() == bills_before.keys()
    assert {i["provider"] for i in bills_after.values()} == {"Vodafone"}
    assert [bills_after[i]["amount"] for i in sorted(bills_after)] == [bills_before[i]["amount"] for i in sorted(bills_before)]


def test_next_month_statement_attaches_and_keeps_counterparty(client, tmpdb):
    """(b) sonraki ayın aynı havalesi/faturası dökümle gelince sabit kaleme bağlanır; kişi ve kurum korunur."""
    months = _months(3)
    _history(tmpdb, months)
    rent_id, bill_id = _accept(client, "p:"), _accept(client, "m:")
    nxt = finance.add_months(months[-1], 1)
    tmpdb.import_bank_rows([_rent(f"{nxt}-10", "k9"), _bill(f"{nxt}-12", "v9", 1250.0, 9)], account=ACC)
    ents = [e for e in tmpdb.list_entries() if e["entry_date"][:7] == nxt]
    rent = next(e for e in ents if e["import_hash"] == "k9")
    bill = next(e for e in ents if e["import_hash"] == "v9")
    assert rent["recurring_id"] == rent_id and rent["counterparty"] == "Hale Yildiz" and rent["month"] == nxt
    assert bill["recurring_id"] == bill_id and bill["bill_provider"] == "Vodafone" and bill["month"] == nxt
    assert rent["id"] in _transfer_ids(client)
    assert bill["id"] in _bill_items(client, nxt)


def test_confirmed_before_statement_merges_and_fills_counterparty(client, tmpdb):
    """(c) bekleyen sabit kalem dökümden önce "onaylandı" (elle kayıt, kişi/kurum yok); döküm gelince tek kayıt kalır
    ve kayıt Transferler/Faturalar'da görünür (kişi ve kurum döküm satırından)."""
    months = _months(3)
    _history(tmpdb, months)
    rent_id, bill_id = _accept(client, "p:"), _accept(client, "m:")
    nxt = finance.add_months(months[-1], 1)
    recs = {r["id"]: r for r in tmpdb.list_recurring()}
    for rid in (rent_id, bill_id):
        r = client.post(f"/api/recurring/{rid}/confirm", json={"month": nxt})
        assert r.status_code == 200, r.text
    manual = {e["recurring_id"]: e for e in tmpdb.list_entries() if e["month"] == nxt and e["recurring_id"]}
    assert manual[rent_id]["counterparty"] is None and manual[bill_id]["bill_provider"] is None
    bill_amount = recs[bill_id]["amount"]
    st = tmpdb.import_bank_rows([_rent(f"{nxt}-10", "k9"), _bill(f"{nxt}-12", "v9", bill_amount, 9)], account=ACC)
    assert st["eslesen_elle"] == 2, st

    in_nxt = [e for e in tmpdb.list_entries() if e["entry_date"][:7] == nxt or e["month"] == nxt]
    assert len(in_nxt) == 2  # çift sayım yok
    rent = next(e for e in in_nxt if e["recurring_id"] == rent_id)
    bill = next(e for e in in_nxt if e["recurring_id"] == bill_id)
    assert rent["id"] == manual[rent_id]["id"] and rent["import_hash"] == "k9" and rent["counterparty"] == "Hale Yildiz"
    assert bill["id"] == manual[bill_id]["id"] and bill["import_hash"] == "v9" and bill["bill_provider"] == "Vodafone"
    assert rent["month"] == nxt and bill["month"] == nxt
    assert rent["id"] in _transfer_ids(client)
    items = _bill_items(client, nxt)
    assert bill["id"] in items and items[bill["id"]]["provider"] == "Vodafone"


def test_confirmed_then_late_payment_and_different_bill_amount_merge(client, tmpdb):
    """(c) kira 3 gün geç ödendi, fatura plandan farklı geldi: yine tek kayıt (eskiden yeni kayıt komşu döneme bağlanıp
    iki kez sayılıyordu). Kayıt bankadaki tutarı alır, dönemi onaylandığı ay kalır; geri alınca iki kayda ayrılır."""
    months = _months(3)
    _history(tmpdb, months)
    rent_id, bill_id = _accept(client, "p:"), _accept(client, "m:")
    nxt = finance.add_months(months[-1], 1)
    for rid in (rent_id, bill_id):
        client.post(f"/api/recurring/{rid}/confirm", json={"month": nxt})
    st = tmpdb.import_bank_rows([_rent(f"{nxt}-13", "k9"), _bill(f"{nxt}-14", "v9", 1265.0, 9)], account=ACC)
    assert st["eslesen_elle"] == 2 and st["gider"] == 0, st
    recd = [e for e in tmpdb.list_entries() if e["recurring_id"] in (rent_id, bill_id) and e["month"] >= nxt]
    assert len(recd) == 2 and {e["month"] for e in recd} == {nxt}
    bill = next(e for e in recd if e["recurring_id"] == bill_id)
    rent = next(e for e in recd if e["recurring_id"] == rent_id)
    assert bill["amount"] == 1265.0 and bill["bill_provider"] == "Vodafone" and bill["import_hash"] == "v9"
    assert rent["counterparty"] == "Hale Yildiz" and rent["id"] in _transfer_ids(client)
    assert _bill_items(client, nxt)[bill["id"]]["provider"] == "Vodafone"
    # sonraki dönem hâlâ bekliyor (komşu ay "ödendi" sayılmadı)
    after = finance.add_months(nxt, 1)
    assert not [e for e in tmpdb.list_entries() if e["recurring_id"] == bill_id and e["month"] == after]

    r = client.post(f"/api/entries/{bill['id']}/unmatch")
    assert r.status_code == 200, r.text
    ents = {e["id"]: e for e in tmpdb.list_entries()}
    back = ents[bill["id"]]
    assert back["amount"] == 1220.0 and back["import_hash"] is None and back["bill_provider"] is None
    other = ents[r.json()["other_id"]]
    assert other["amount"] == 1265.0 and other["import_hash"] == "v9" and other["bill_provider"] == "Vodafone"


def test_small_extra_payment_to_same_person_stays_separate(client, tmpdb):
    """Aynı kişiye ayrıca küçük bir havale onaylanmış kiranın yerine geçmez."""
    months = _months(3)
    _history(tmpdb, months)
    rent_id = _accept(client, "p:")
    nxt = finance.add_months(months[-1], 1)
    client.post(f"/api/recurring/{rent_id}/confirm", json={"month": nxt})
    tmpdb.import_bank_rows([_rent(f"{nxt}-11", "x1", amount=500.0)], account=ACC)
    manual = next(e for e in tmpdb.list_entries() if e["recurring_id"] == rent_id and e["month"] == nxt)
    assert manual["import_hash"] is None and manual["amount"] == 15000.0


def test_cycle_day_shift_keeps_transfer_listing(client, tmpdb):
    """(d) ay dönümü 17, kira 17'sinde: 16'sında (bir gün erken) yatan kira bir sonraki dönemin kirası sayılır
    (entries.month = sonraki dönem; her dönemde tam bir kira). Transferler ve Faturalar tarihe göre süzer: kayıt
    takvim ayında, tutarı ve tarihi aynen görünür. (Bilinen davranış; dönem özetinde sonraki döneme yazılır.)"""
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    ms = _months(3)
    tmpdb.import_bank_rows([_rent(f"{m}-17", f"r{i}") for i, m in enumerate(ms)], account=ACC)
    rid = _accept(client, "p:")
    nxt = finance.add_months(ms[-1], 1)
    tmpdb.import_bank_rows([_rent(f"{nxt}-16", "early")], account=ACC)
    e = next(x for x in tmpdb.list_entries() if x["import_hash"] == "early")
    assert e["recurring_id"] == rid and e["entry_date"] == f"{nxt}-16" and e["amount"] == 15000.0
    assert tmpdb.period_of(e["entry_date"]) == nxt and e["month"] == finance.add_months(nxt, 1)
    items = client.get("/api/transfers", params={"month": nxt}).json()["items"]
    assert any(i["id"] == e["id"] and i["amount"] == 15000.0 for i in items)
