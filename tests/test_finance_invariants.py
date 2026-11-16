"""Finans değişmezleri (invariant): sentetik defterlerde temel kurallar, tablo güdümlü. Banka adları, kart numaraları,
kişiler ve tutarlar uydurmadır; tarihler bugüne göre.

Kurallar:
  I-T  kendi hesapların arası aktarım toplam nakdi ve "Ödemelerden sonra"yı değiştirmez
  I-K  kart ekstresi ödemesi (hesaptan) nakit − kart borcu toplamını değiştirmez
  I-B  aynı belge iki kez yüklenince sonuç değişmez
  I-S  spendable ≤ free; spendable kalemlerinin toplamı; eksi işaretli kalem eksi olmaz
  I-D  dönem dönümünde yeni veri yokken nakit ve kart borcu değişmez; değişen kalemler açıklanabilir
  I-A  finans denetimi salt-okunurdur, aynı defterde aynı sonucu verir; her bulgu sınıfı kendi senaryosunda çıkar

Şu an kırmızı olanlar xfail(strict=True): düzeltecek dal gerekçede yazılı. Dal birleşince test kendiliğinden
"XPASS(strict)" ile kırmızıya döner; o zaman xfail işareti kaldırılır.
"""
import hashlib
from datetime import date, timedelta

import pytest

from app import finance, finance_audit
from app.parsers.base import PURCHASE, Statement, Transaction

T = date.today()
CARD = "4543***1234"
BANK_A, BANK_B = "Örnek Banka A", "Örnek Banka B"


# ---------------------------------------------------------------- kurulum yardımcıları

def _acc(db, name=BANK_A, balance=50000.0, as_of=None, kind="banka") -> int:
    db.set_account_balance(name, balance, (as_of or T).isoformat(), "elle", kind)
    with db.connect() as c:
        return c.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()[0]


def _stmt(db, total=3000.0, card=CARD, pe=None, due=None, tag="a", lines=None) -> int:
    pe = pe or T - timedelta(days=10)
    s = Statement(bank="Örnek Bank", card_no=card, period_end=pe, due_date=due or T + timedelta(days=5),
                  total_debt=total, min_payment=100, credit_limit=50000, period_activity=total,
                  next_period_end=pe + timedelta(days=30), next_due_date=(due or T + timedelta(days=5)) + timedelta(days=30))
    s.transactions = lines if lines is not None else [
        Transaction(txn_date=pe - timedelta(days=5), description="ORNEK MARKET", amount=total, txn_type=PURCHASE, card=card)]
    return db.save_statement(f"st-{card}-{pe}-{tag}".encode(), "e.pdf", s)


def _entry(db, kind, amount, account_id=None, days_ago=1, payment="havale", counterparty=None, desc="Örnek kayıt",
           **kw) -> int:
    d = T - timedelta(days=days_ago)
    return db.create_entry({"entry_date": d.isoformat(), "description": desc, "amount": amount, "kind": kind,
                            "category": "Diğer", "payment": payment, "account_id": account_id,
                            "counterparty": counterparty, **kw})


def _nw():
    return finance.net_worth(T)


def _parts_total(nw) -> float:
    return round(sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in nw["spendable_parts"]), 2)


def _codes(today=T) -> dict:
    return finance_audit.check(today)["counts"]


# ---------------------------------------------------------------- I-T: aktarım nakdi değiştirmez

@pytest.mark.parametrize("amount,days_ago", [(1000.0, 0), (12500.55, 1), (0.01, 3), (49999.99, 0)])
def test_transfer_keeps_total_cash(tmpdb, amount, days_ago):
    a = _acc(tmpdb, BANK_A, 50000.0, as_of=T - timedelta(days=5))
    b = _acc(tmpdb, BANK_B, 2000.0, as_of=T - timedelta(days=5))
    before = _nw()
    tmpdb.add_own_pair((T - timedelta(days=days_ago)).isoformat(), amount, a, b)
    after = _nw()
    assert after["cash"] == before["cash"]
    assert after["spendable"] == before["spendable"]
    cur = {x["id"]: x["current"] for x in after["accounts"]}
    assert round(cur[a] + cur[b], 2) == round(52000.0, 2)
    assert finance_audit.check(T)["counts"].get("transfer_cift") is None


def test_transfer_fix_own_pair_keeps_balances(tmpdb):
    """transfer_cift bulgusu → 'Aktarıma çevir': bakiyeler aynı kalır, bulgu düşer."""
    a = _acc(tmpdb, BANK_A, 10000.0, as_of=T - timedelta(days=5))
    b = _acc(tmpdb, BANK_B, 0.0, as_of=T - timedelta(days=5))
    g = _entry(tmpdb, "gider", 4000.0, a, counterparty="Deneme Kişi Örnek")
    i = _entry(tmpdb, "gelir", 4000.0, b, counterparty="Deneme Kişi Örnek")
    before = _nw()
    res = finance_audit.check(T)
    assert res["counts"].get("transfer_cift") == 1 and res["amounts"]["transfer_cift"] == 4000.0
    finance_audit.own_pair(g, i)
    after = _nw()
    assert after["cash"] == before["cash"] and after["spendable"] == before["spendable"]
    assert {x["id"]: x["current"] for x in after["accounts"]} == {x["id"]: x["current"] for x in before["accounts"]}
    assert finance_audit.check(T)["counts"].get("transfer_cift") is None


def test_transfer_fix_rejects_non_pair(tmpdb):
    a = _acc(tmpdb, BANK_A)
    g = _entry(tmpdb, "gider", 100.0, a)
    i = _entry(tmpdb, "gelir", 90.0, a)
    with pytest.raises(finance_audit.FixError):
        finance_audit.own_pair(g, i)


# ---------------------------------------------------------------- I-K: kart ödemesi nakit − kart borcunu değiştirmez

@pytest.mark.parametrize("pay", [3000.0, 1200.0])  # tamamı / kısmi
def test_card_payment_from_account_keeps_net(tmpdb, pay):
    a = _acc(tmpdb, BANK_A, 20000.0, as_of=T - timedelta(days=3))
    sid = _stmt(tmpdb, 3000.0)
    before = _nw()
    tmpdb.mark_statement_paid(sid, pay, T.isoformat(), a)
    after = _nw()
    assert round(after["cash"] - after["card_debt"], 2) == round(before["cash"] - before["card_debt"], 2)
    assert after["cash"] == round(before["cash"] - pay, 2)
    assert after["spendable"] == before["spendable"]
    assert "odendi_hesapsiz" not in _codes()


@pytest.mark.xfail(strict=True, reason="fix/ekstre-odendi-hesap: hesap seçilmeden 'ödendi' denince para hiçbir hesaptan "
                                       "düşmüyor, Ödemelerden sonra ödeme kadar şişiyor (bulgu sebebi 1)")
def test_card_payment_without_account_keeps_net(tmpdb):
    _acc(tmpdb, BANK_A, 20000.0, as_of=T - timedelta(days=3))
    sid = _stmt(tmpdb, 3000.0)
    before = _nw()
    tmpdb.mark_statement_paid(sid, 3000.0, T.isoformat(), None)
    after = _nw()
    assert after["spendable"] == before["spendable"]


def test_card_payment_without_account_is_flagged(tmpdb):
    """Sebep 1 düzelene dek denetim yakalar; düzeldikten sonra da (eski veri) yakalamaya devam etmeli."""
    _acc(tmpdb, BANK_A, 20000.0, as_of=T - timedelta(days=3))
    sid = _stmt(tmpdb, 3000.0)
    tmpdb.mark_statement_paid(sid, 3000.0, T.isoformat(), None)
    res = finance_audit.check(T)
    assert res["refs"]["odendi_hesapsiz"] == [f"statement:{sid}"] and res["amounts"]["odendi_hesapsiz"] == 3000.0
    # bakiye ödemeden SONRA güncellendiyse ödeme bakiyenin içindedir: uyarı yok
    tmpdb.set_account_balance(BANK_A, 17000.0, (T + timedelta(days=1)).isoformat(), "elle")
    assert "odendi_hesapsiz" not in _codes()


# ---------------------------------------------------------------- I-B: aynı belge iki kez

def test_same_statement_twice_is_idempotent(tmpdb, upload):
    _acc(tmpdb, BANK_A, 30000.0)
    upload()
    first = _nw()
    audit1 = finance_audit.check(T)["counts"]
    upload()
    second = _nw()
    for k in ("cash", "card_debt", "card_estimated", "pending_cards", "spendable", "net_display"):
        assert second[k] == first[k], k
    assert finance_audit.check(T)["counts"] == audit1


def test_same_bank_rows_twice_is_idempotent(tmpdb):
    aid = _acc(tmpdb, BANK_A, 10000.0, as_of=T - timedelta(days=10))
    rows = [{"date": (T - timedelta(days=4)).isoformat(), "amount": -350.0, "description": "ORNEK MARKET ODEME", "type": "gider",
             "hash": "h-ornek-1"},
            {"date": (T - timedelta(days=3)).isoformat(), "amount": 1200.0, "description": "ORNEK GELEN HAVALE", "type": "gelir",
             "hash": "h-ornek-2"}]
    tmpdb.import_bank_rows([dict(r) for r in rows], BANK_A, account_id=aid)
    first = _nw()
    tmpdb.import_bank_rows([dict(r) for r in rows], BANK_A, account_id=aid)
    second = _nw()
    assert (second["cash"], second["spendable"]) == (first["cash"], first["spendable"])
    assert "cift_satir" not in _codes()


# ---------------------------------------------------------------- I-S: spendable ≤ free, kalemler tutar

def _sc_empty(db):
    pass


def _sc_cash_only(db):
    _acc(db, BANK_A, 12000.0)


def _sc_card_debt(db):
    _acc(db, BANK_A, 12000.0)
    _stmt(db, 4500.0)


def _sc_overspent(db):
    _acc(db, BANK_A, 1000.0)
    _stmt(db, 4500.0)


def _sc_jar_debt_recurring(db):
    _acc(db, BANK_A, 8000.0)
    db.create_recurring({"description": "Örnek kira", "amount": 3000.0, "kind": "gider", "category": "Kira",
                         "day_of_month": min(28, (T + timedelta(days=2)).day), "start_month": finance.current_period(T)})


def _sc_negative_account(db):
    _acc(db, BANK_A, -2500.0)
    _acc(db, BANK_B, 1000.0)


SCENARIOS = [_sc_empty, _sc_cash_only, _sc_card_debt, _sc_overspent, _sc_jar_debt_recurring, _sc_negative_account]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__[4:])
def test_spendable_le_free_and_parts_sum(tmpdb, scenario):
    scenario(tmpdb)
    nw = _nw()
    assert nw["spendable"] <= nw["free"]
    assert _parts_total(nw) == nw["spendable"]
    assert all(p["amount"] >= 0 for p in nw["spendable_parts"] if p["sign"] == "−")
    assert "harcanabilir_tutarsiz" not in _codes()


def test_card_estimate_superset_of_pending(tmpdb):
    """Kesilecek ekstre tahmini (net_display'de düşülen) kesin kısmın (spendable'da düşülen) üst kümesi."""
    _acc(tmpdb, BANK_A, 20000.0)
    _stmt(tmpdb, 3000.0, pe=T - timedelta(days=20), due=T + timedelta(days=1))
    nw = _nw()
    assert nw["card_estimated"] + 0.005 >= nw["pending_cards"]


# ---------------------------------------------------------------- I-D: dönem dönümü

def test_period_turn_without_new_data_keeps_cash_and_card_debt(tmpdb):
    _acc(tmpdb, BANK_A, 15000.0, as_of=T - timedelta(days=40))
    _stmt(tmpdb, 2000.0, pe=T - timedelta(days=20), due=T + timedelta(days=1))
    start, end = finance.period_bounds(finance.current_period(T))
    a, b = finance.net_worth(end), finance.net_worth(end + timedelta(days=1))
    assert a["cash"] == b["cash"]
    # değişen kalemler yalnız dönem kalemleri (bekleyen ödemeler, kesilecek ekstre) olabilir; nakit satırı sabit
    allowed = {"card_debt", "pending_cards", "pending_other", "card_interest", "card_late_interest", "bills", "recurring"}
    pa = {p["key"]: p["amount"] for p in a["spendable_parts"]}
    pb = {p["key"]: p["amount"] for p in b["spendable_parts"]}
    changed = {k for k in pa.keys() | pb.keys() if pa.get(k, 0) != pb.get(k, 0)}
    assert changed <= allowed
    assert _parts_total(b) == b["spendable"]


@pytest.mark.xfail(strict=True, reason="fix/odemelerden-sonra-tek-ufuk: kesimi geçmiş, ekstresi yüklenmemiş ama hesaptan "
                                       "ödenmiş döngü hâlâ 'kesilecek ekstre' olarak düşülüyor (sebep 4): ödeme iki kez")
def test_paid_cycle_without_statement_not_deducted_twice(tmpdb):
    aid = _acc(tmpdb, BANK_A, 20000.0, as_of=T - timedelta(days=45))
    # son yüklenen ekstre 40 gün önce kesildi; sıradaki kesim 10 gün önce geçti (ekstresi yüklenmedi), son ödemesi 2 gün sonra
    pe = T - timedelta(days=40)
    s = Statement(bank="Örnek Bank", card_no=CARD, period_end=pe, due_date=pe + timedelta(days=10), total_debt=2000.0,
                  min_payment=100, credit_limit=50000, period_activity=2000.0, next_period_end=T - timedelta(days=10),
                  next_due_date=T + timedelta(days=2))
    s.transactions = [Transaction(txn_date=pe - timedelta(days=3), description="ORNEK MARKET", amount=2000.0,
                                  txn_type=PURCHASE, card=CARD,
                                  installment_no=1, installments_left=3, installment_left_amount=500.0)]
    sid = tmpdb.save_statement(b"st-paid-cycle", "e.pdf", s)
    tmpdb.mark_statement_paid(sid, 2000.0, (pe + timedelta(days=8)).isoformat(), aid)
    before = _nw()
    # dökümden: yüklenmemiş ekstrenin ödemesi bu hesaptan çıktı (500 = sıradaki taksit)
    with tmpdb.connect() as c:
        c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account, account_id, created_at) "
                  "VALUES (?, 500, '1234', ?, ?, CURRENT_TIMESTAMP)", ((T - timedelta(days=1)).isoformat(), BANK_A, aid))
    after = _nw()
    assert after["spendable"] == before["spendable"]  # nakit 500 azaldı, kesilecek ekstre 500 düşmeli


@pytest.mark.xfail(strict=True, reason="fix/odemelerden-sonra-tek-ufuk: Ödemelerden sonra kalemlerinin ufku farklı "
                                       "(sebep 5) — kesilecek ekstre 45 gün ileriyi, düzenli ödeme/fatura dönem sonunu sayıyor")
def test_spendable_items_share_one_horizon(tmpdb):
    _acc(tmpdb, BANK_A, 30000.0)
    _, end = finance.period_bounds(finance.current_period(T))
    pe = T - timedelta(days=10)  # sıradaki kesim dönem sonundan sonra, son ödemesi 45 gün içinde (takvimden bağımsız)
    s = Statement(bank="Örnek Bank", card_no=CARD, period_end=pe, due_date=pe + timedelta(days=10), total_debt=1500.0,
                  min_payment=100, credit_limit=50000, period_activity=1500.0, next_period_end=end + timedelta(days=1),
                  next_due_date=end + timedelta(days=5))
    s.transactions = [Transaction(txn_date=pe - timedelta(days=3), description="ORNEK BEYAZ ESYA", amount=1500.0,
                                  txn_type=PURCHASE, card=CARD, installment_no=1, installments_left=5,
                                  installment_left_amount=1500.0)]
    tmpdb.save_statement(b"st-horizon", "e.pdf", s)
    nw = _nw()
    dated = [x for k in ("pending_cards_items", "recurring_items", "bills_items", "pending_other_items")
             for x in nw.get(k) or []]
    assert all((x.get("due_date") or x.get("date") or "") <= end.isoformat() for x in dated), dated


def test_paid_cycle_without_statement_is_flagged(tmpdb):
    aid = _acc(tmpdb, BANK_A, 20000.0, as_of=T - timedelta(days=60))
    sid = _stmt(tmpdb, 2000.0, pe=T - timedelta(days=50), due=T - timedelta(days=40))
    tmpdb.mark_statement_paid(sid, 2000.0, (T - timedelta(days=41)).isoformat(), aid)
    with tmpdb.connect() as c:  # sıradaki dönemin (ekstresi yok) ödemesi
        c.execute("INSERT INTO card_payments (pay_date, amount, card_last4, account, account_id, import_hash) "
                  "VALUES (?, 700, '1234', ?, ?, 'h-ornek-kart')", ((T - timedelta(days=5)).isoformat(), BANK_A, aid))
    assert _codes().get("odenmis_ekstresiz") == 1


# ---------------------------------------------------------------- I-A: denetimin kendisi

def _file_hash(db) -> str:
    with open(db.db_path(), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_audit_is_read_only_and_deterministic(tmpdb):
    a = _acc(tmpdb, BANK_A, -100.0, as_of=T - timedelta(days=30))
    _entry(tmpdb, "gider", 250.0, None, days_ago=2)
    _entry(tmpdb, "gider", 90.0, a, days_ago=1)
    _stmt(tmpdb, 1500.0)
    finance.net_worth(T)  # olası tembel göçler denetimden önce
    h = _file_hash(tmpdb)
    r1, r2 = finance_audit.check(T), finance_audit.check(T)
    assert _file_hash(tmpdb) == h
    assert (r1["counts"], r1["amounts"], r1["refs"]) == (r2["counts"], r2["amounts"], r2["refs"])


def test_clean_book_has_no_findings(tmpdb):
    a = _acc(tmpdb, BANK_A, 25000.0)
    sid = _stmt(tmpdb, 3000.0)
    tmpdb.mark_statement_paid(sid, 3000.0, T.isoformat(), a)
    _entry(tmpdb, "gider", 400.0, a)
    assert _codes() == {}


def _f_eksi(db):
    _acc(db, BANK_A, -3200.0)


def _f_capa(db):
    a = _acc(db, BANK_A, 5000.0, as_of=T - timedelta(days=20))
    _entry(db, "gider", 100.0, a, days_ago=19)


def _f_capa_stale(db):
    a = _acc(db, BANK_A, 5000.0, as_of=T - timedelta(days=40))
    _entry(db, "gider", 100.0, a, days_ago=39)


def _f_hesapsiz(db):
    _acc(db, BANK_A, 5000.0, as_of=T - timedelta(days=5))
    _entry(db, "gider", 777.0, None, days_ago=1)


def _f_transfer(db):
    a = _acc(db, BANK_A, 5000.0, as_of=T - timedelta(days=5))
    b = _acc(db, BANK_B, 100.0, as_of=T - timedelta(days=5))
    _entry(db, "gider", 2500.0, a, counterparty="Deneme Kişi Örnek")
    _entry(db, "gelir", 2500.0, b, days_ago=0, counterparty="Deneme Kişi Örnek")


def _f_duzenli(db):
    a = _acc(db, BANK_A, 9000.0, as_of=T - timedelta(days=40))
    start, end = finance.period_bounds(finance.current_period(T))
    day = min(28, max(start, T - timedelta(days=0)).day)
    db.create_recurring({"description": "Örnek aidat", "amount": 1500.0, "kind": "gider", "category": "Aidat",
                         "day_of_month": day, "start_month": finance.current_period(T)})
    db.create_entry({"entry_date": max(start, T - timedelta(days=1)).isoformat(), "description": "Örnek havale",
                     "amount": 1500.0, "kind": "gider", "category": "Diğer", "payment": "havale", "account_id": a})


def _f_cift_link(db):
    _acc(db, BANK_A, 5000.0)
    sid = _stmt(db, 600.0)
    with db.connect() as c:
        tid = c.execute("SELECT id FROM transactions WHERE statement_id = ?", (sid,)).fetchone()[0]
    for _ in range(2):
        _entry(db, "gider", 600.0, None, payment="kredi_karti", linked_txn_id=tid)


def _f_bakiye_yok(db):
    _stmt(db, 2500.0)


@pytest.mark.parametrize("setup,code,amount", [
    (_f_bakiye_yok, "bakiye_yok", 2500.0),
    (_f_eksi, "eksi_bakiye", 3200.0), (_f_capa, "capa_eski", 4900.0), (_f_capa_stale, "capa_eski", 4900.0),
    (_f_hesapsiz, "hesapsiz_hareket", 777.0), (_f_transfer, "transfer_cift", 2500.0),
    (_f_duzenli, "duzenli_bekliyor", 1500.0), (_f_cift_link, "cift_satir", 600.0),
], ids=lambda x: x.__name__[3:] if callable(x) else None)
def test_each_finding_class(tmpdb, setup, code, amount):
    setup(tmpdb)
    res = finance_audit.check(T)
    assert res["counts"].get(code) == 1, res["counts"]
    assert res["amounts"][code] == amount


def test_findings_become_todos_and_admin_counts(tmpdb):
    from app import reminders, system

    _f_hesapsiz(tmpdb)
    _acc(tmpdb, BANK_B, -3200.0)
    out = finance_audit.run(T)
    assert out["total"] == 2 and out["new"] == 2
    items = [i for i in reminders.build(T) if i["kind"] == "finans"]
    assert {i["code"] for i in items} == {"eksi_bakiye", "hesapsiz_hareket"}
    assert all(i["id"].startswith("finans:") and i["route"] and i["title"] for i in items)
    s = finance_audit.admin_summary(T.isoformat())
    assert s["spaces"] == 1 and s["by_code"]["eksi_bakiye"]["books"] == 1
    with system.connect() as c:  # yöneticiye yalnız sayı: tutar yok
        assert "3200" not in c.execute("SELECT counts_json FROM finance_audit_runs").fetchone()[0]


def test_new_class_records_incident_without_amounts(tmpdb, monkeypatch):
    from app import incidents

    got = []
    monkeypatch.setattr(incidents, "record", lambda code, place="-", **kw: got.append((code, place, kw.get("ozet"))))
    _f_eksi(tmpdb)
    finance_audit.run(T)
    finance_audit.run(T)  # aynı sınıf ikinci gece: yeni değil
    assert got == [("finans_tutarsizlik", "eksi_bakiye", "n=1")]


def test_api_items_and_own_pair(client, tmpdb):
    _f_transfer(tmpdb)
    finance_audit.run(T)
    items = client.get("/api/finance-audit").json()["items"]
    it = next(i for i in items if i["code"] == "transfer_cift")
    assert it["action"] == "own_pair"
    r = client.post("/api/finance-audit/own-pair", json={"out_entry_id": it["out_entry_id"], "in_entry_id": it["in_entry_id"]})
    assert r.status_code == 200, r.text
    assert not [i for i in client.get("/api/finance-audit").json()["items"] if i["code"] == "transfer_cift"]
    assert client.post("/api/finance-audit/own-pair", json={"out_entry_id": 999, "in_entry_id": 998}).status_code == 422
