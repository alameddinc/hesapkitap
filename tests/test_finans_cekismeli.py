"""Çekişmeli uç durumlar (bağımsız doğrulama, lead 16.11): tek ufuk, hesabı belirsiz ödeme (B1 çapa kuralı), döngü
ödemesi, kendi hesapları arası aktarım, kumbara, kısmi ödeme, ortak defter üyesi, boş kullanıcı, eski istemci yanıt şekli.
Kırmızılar xfail(strict) ve gerekçesiyle: düzeltme geliştiriciye. Kişiler, kart numaraları, tutarlar UYDURMADIR.
Ay dönümü 17 (aksi yazmadıkça); bugün 16 Kas 2026 Pazartesi (donmuş)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction
from tests.test_gercek_gun_tekrari import OWNER, freeze, set_ts

TODAY = date(2026, 11, 16)
CARD = "5400***0002"
TOTAL = 10000.0
A0, B0 = 50000.0, 20000.0


def _stmt(tmpdb, card=CARD, pe=date(2026, 11, 13), due=date(2026, 11, 23), total=TOTAL, bank="VakıfBank",
          nxt=(date(2026, 12, 13), date(2026, 12, 23)), lines=None):
    s = Statement(bank=bank, card_no=card, period_end=pe, due_date=due, total_debt=total, min_payment=round(total * .2, 2),
                  credit_limit=90000, period_activity=total, next_period_end=nxt[0], next_due_date=nxt[1])
    s.transactions = lines or [Transaction(txn_date=pe - timedelta(days=5), description="ORNEK MARKET", amount=total,
                                           txn_type=PURCHASE, card=card)]
    tmpdb.save_statement(f"st-{card}-{pe}".encode(), "e.pdf", s)
    return next(x["id"] for x in tmpdb.list_statements() if x["period_end"] == pe.isoformat() and x["card_no"] == card)


@pytest.fixture()
def two(client, tmpdb, monkeypatch):
    """İki banka hesabı (A çapa 10 Kas, B çapa 10 Kas) + ödenmemiş ekstre 10.000 (son ödeme 23 Kas)."""
    freeze(monkeypatch, TODAY)
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    tmpdb.set_account_balance("Örnek Banka A", A0, "2026-11-10", "elle")
    tmpdb.set_account_balance("Örnek Banka B", B0, "2026-11-10", "elle")
    set_ts(tmpdb, "UPDATE accounts SET updated_at = '2026-11-10 09:00:00'")
    with tmpdb.connect() as c:
        ids = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM accounts")}
    sid = _stmt(tmpdb)
    return {"a": ids["Örnek Banka A"], "b": ids["Örnek Banka B"], "sid": sid}


def nw(day=TODAY):
    return finance.net_worth(day)


def cur(n, aid):
    return next(a["current"] for a in n["accounts"] if a["id"] == aid)


def pay(client, sid, amount, **acc):
    r = client.post(f"/api/statements/{sid}/pay", json={"amount": amount, "paid_at": "2026-11-16", **acc})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- tek ufuk: dönem özellikleri (ay dönümü 1..28, Şubat)

@pytest.mark.parametrize("start", [1, 2, 15, 17, 28])
def test_horizon_properties_every_day(tmpdb, start):
    """Her gün için: bugün ufkun ilk döneminde; ufuk = son dönemin son günü; tek dönemse dönem sonuna ≥ 2 gün; iki
    dönemse ilk dönemin bitmesine ≤ 1 gün; dönemler bitişik (Şubat ve artık yıl dahil); later_end > end."""
    tmpdb.set_setting("cycle_start_day", str(start))
    d = date(2027, 1, 1)
    while d <= date(2028, 3, 31):
        h = finance.spend_horizon(d)
        p0 = finance.period_bounds(h["periods"][0])
        assert p0[0] <= d <= p0[1], (start, d)
        assert h["end"] == finance.period_bounds(h["periods"][-1])[1]
        if len(h["periods"]) == 1:
            assert (h["end"] - d).days >= 2, (start, d)
        else:
            assert (p0[1] - d).days <= 1 and h["periods"][1] == finance.add_months(h["periods"][0], 1), (start, d)
        nxt = finance.period_bounds(finance.add_months(h["periods"][-1], 1))
        assert nxt[0] == h["end"] + timedelta(days=1) and h["later_end"] == nxt[1] > h["end"]
        d += timedelta(days=1)


def test_cycle_day_31_is_clamped_not_silently_reset(tmpdb):
    """Ay dönümü 31 isteyen kullanıcı: API 28'e kırpar (set_cycle_start_day); ayara doğrudan 31 yazılmışsa (eski veri,
    elle) cycle_start_day 1'e döner — takvim ayına sessizce geçer. Belgelemek için."""
    assert tmpdb.set_cycle_start_day(31) == 28
    tmpdb.set_setting("cycle_start_day", "31")
    assert tmpdb.cycle_start_day() == 1


# ---------------------------------------------------------------- maaş günü 13 / 17 / 18 (ay dönümü 17)

@pytest.mark.parametrize("day,early", [(13, False), (17, False), (18, False), (17, True)])
def test_salary_counted_once_in_horizon(client, tmpdb, monkeypatch, day, early):
    """16 Kas (ufuk 16 Ara, dönemler 2026-11 + 2026-12): önceki maaşlar yatmış; beklenen gelir TAM BİR maaş. 17'si
    maaşı 16 Kas'ta erken yattıysa (Cuma/bayram öncesi) bekleyen maaş YOK (iki dönemde de)."""
    freeze(monkeypatch, TODAY)
    tmpdb.set_cycle_start_day(17)
    tmpdb.set_account_balance("Örnek Banka A", 1000.0, "2026-11-01", "elle")
    r = client.post("/api/recurring", json={"description": "Maaş", "amount": 40000.0, "kind": "gelir", "category": "Maaş",
                                            "day_of_month": day, "start_month": "2026-09"})
    assert r.status_code == 200, r.text
    paid = [date(2026, 9, day), date(2026, 10, day)] + ([date(2026, 11, day)] if day < 17 else [])
    if early:
        paid.append(date(2026, 11, 16))
    for d in paid:
        assert client.post("/api/entries", json={"entry_date": d.isoformat(), "description": "Maaş", "amount": 40000.0,
                                                 "kind": "gelir", "category": "Maaş"}).status_code == 200
    for today in (TODAY, TODAY + timedelta(days=1)):
        freeze(monkeypatch, today)
        sai = finance.net_worth(today)["spendable_after_income"]
        assert ((sai or {}).get("income") or 0.0) == (0.0 if early else 40000.0), (day, early, today, sai)


# ---------------------------------------------------------------- B1: hesabı belirsiz ödeme, çapa kuralı

def _dokum(tmpdb, rows, account, aid):
    from app import bank_import

    raw = [{"date": d, "description": desc, "amount": amt, "balance": bal} for d, desc, amt, bal in rows]
    return tmpdb.import_bank_rows(bank_import.finalize(raw, [OWNER]), account=account, owners=[OWNER], account_id=aid)


def test_unknown_payment_is_deducted_once_then_dokum_links(client, two, tmpdb):
    """Hesapsız ödendi → döküm (ödeme satırı A'da) gelir: spendable baştan sona aynı (ne çift düşüm ne hiç düşmeme)."""
    s0 = nw()["spendable"]
    assert s0 == A0 + B0 - TOTAL
    pay(client, two["sid"], TOTAL)
    set_ts(tmpdb, "UPDATE statements SET paid_marked_at = '2026-11-16 03:00:00' WHERE id = ?", two["sid"])
    n = nw()
    assert n["unknown_payments"] == TOTAL and n["spendable"] == s0 and n["free"] == A0 + B0 - TOTAL
    _dokum(tmpdb, [("2026-11-16", "KREDI KARTI ODEMESI 5400 **** 0002", -TOTAL, A0 - TOTAL)], "Örnek Banka A", two["a"])
    n = nw()
    assert n["unknown_payments"] == 0 and not n["unknown_payments_items"]
    assert n["spendable"] == s0 and n["free"] == A0 + B0 - TOTAL and cur(n, two["a"]) == A0 - TOTAL


def test_unknown_payment_eft_without_card_digits_links(client, two, tmpdb):
    """Ödeme dökümde kart hanesi olmadan (başka bankanın kartına FAST) gelir: tutar ekstreyle birebir → bağlanır."""
    pay(client, two["sid"], TOTAL)
    set_ts(tmpdb, "UPDATE statements SET paid_marked_at = '2026-11-16 03:00:00' WHERE id = ?", two["sid"])
    _dokum(tmpdb, [("2026-11-16", "FAST ODEME ORNEK BANKASI", -TOTAL, A0 - TOTAL)], "Örnek Banka A", two["a"])
    n = nw()
    assert n["unknown_payments"] == 0 and n["free"] == A0 + B0 - TOTAL


def test_unknown_payment_stays_deducted_when_other_account_dokum_lacks_it(client, two, tmpdb):
    pay(client, two["sid"], TOTAL)
    set_ts(tmpdb, "UPDATE statements SET paid_marked_at = '2026-11-16 03:00:00' WHERE id = ?", two["sid"])
    # B'nin dökümü 16 Kas'ı kapsıyor, içinde kart ödemesi YOK (yalnız market harcaması)
    _dokum(tmpdb, [("2026-11-16", "ORNEK MARKET POS", -500.0, B0 - 500.0)], "Örnek Banka B", two["b"])
    set_ts(tmpdb, "UPDATE accounts SET updated_at = '2026-11-16 04:00:00' WHERE id = ?", two["b"])
    n = nw()
    assert n["unknown_payments"] == TOTAL  # ödeme hâlâ hiçbir bakiyede değil
    assert n["free"] == A0 + B0 - 500.0 - TOTAL


def test_unknown_payment_manual_balance_after_is_inside(client, two, tmpdb):
    """Ödemeden SONRA elle bakiye girildi (gün tekrarı f): ödeme o bakiyenin içinde, düşülmez (çift düşüm yok)."""
    pay(client, two["sid"], TOTAL)
    set_ts(tmpdb, "UPDATE statements SET paid_marked_at = '2026-11-16 03:00:00' WHERE id = ?", two["sid"])
    assert client.patch(f"/api/accounts/{two['a']}", json={"balance": A0 - TOTAL}).status_code == 200
    set_ts(tmpdb, "UPDATE accounts SET updated_at = '2026-11-16 05:00:00' WHERE id = ?", two["a"])
    n = nw()
    assert n["unknown_payments"] == 0 and n["free"] == A0 - TOTAL + B0


def test_no_accounts_user_unknown_not_deducted(client, tmpdb, monkeypatch):
    """Hiç hesabı olmayan (yalnız kart ekstresi yükleyen) kullanıcı: hesapsız ödendi → belirsiz kalem yok, kart borcu
    kalkar; spendable 0 (eksi değil), hata yok."""
    freeze(monkeypatch, TODAY)
    tmpdb.set_cycle_start_day(17)
    sid = _stmt(tmpdb)
    assert nw()["spendable"] == -TOTAL
    pay(client, sid, TOTAL)
    n = nw()
    assert n["unknown_payments_items"] == [] and n["spendable"] == 0 and n["card_debt"] == 0


# ---------------------------------------------------------------- değişmez: kart ödemesi (nakit + kart borcu) aynı

@pytest.mark.parametrize("acc", ["a", None, "yok"])
@pytest.mark.parametrize("amount", [4000.0, TOTAL])
def test_card_payment_keeps_spendable(client, two, tmpdb, acc, amount):
    """Kısmi ya da tam ödeme; hesaptan (A), hesapsız (anahtar yok) ya da hesaptan değil (null; nakit/takip dışı):
    hesaptan ve hesapsızda spendable DEĞİŞMEZ (para çıktı, borç azaldı); 'hesaptan değil'de borç kadar artar."""
    s0 = nw()["spendable"]
    body = {} if acc is None else {"account_id": two["a"]} if acc == "a" else {"account_id": None}
    pay(client, two["sid"], amount, **body)
    set_ts(tmpdb, "UPDATE statements SET paid_marked_at = '2026-11-16 03:00:00' WHERE id = ?", two["sid"])
    n = nw()
    assert n["card_debt"] == TOTAL - amount
    assert n["spendable"] == (s0 + amount if acc == "yok" else s0)


def test_paid_undo_repaid_other_account(client, two, tmpdb):
    """A'dan ödendi → geri al → B'den ödendi → hesabı korunarak tutar düzeltildi → 'hesaptan değil': her adımda
    bakiyeler ve belirsiz kalem doğru."""
    sid = two["sid"]
    pay(client, sid, TOTAL, account_id=two["a"])
    n = nw()
    assert (cur(n, two["a"]), cur(n, two["b"]), n["unknown_payments"]) == (A0 - TOTAL, B0, 0)
    assert client.delete(f"/api/statements/{sid}/pay").status_code == 200
    n = nw()
    assert (cur(n, two["a"]), n["card_debt"]) == (A0, TOTAL)
    pay(client, sid, TOTAL, account_id=two["b"])
    n = nw()
    assert (cur(n, two["a"]), cur(n, two["b"]), n["unknown_payments"]) == (A0, B0 - TOTAL, 0)
    pay(client, sid, 9000.0)  # düzeltme, anahtar yok: hesap (B) korunur
    n = nw()
    assert (cur(n, two["b"]), n["card_debt"], n["unknown_payments"]) == (B0 - 9000.0, 1000.0, 0)
    pay(client, sid, TOTAL, account_id=None)
    n = nw()
    assert (cur(n, two["b"]), n["unknown_payments"], n["card_debt"]) == (B0, 0, 0)


def test_unknown_then_corrected_with_account(client, two, tmpdb):
    """Önce hesapsız (belirsiz) → sonra 'A'dan ödedim' düzeltmesi: belirsiz kalkar, A düşer; toplam bir kez."""
    s0 = nw()["spendable"]
    pay(client, two["sid"], TOTAL)
    pay(client, two["sid"], TOTAL, account_id=two["a"])
    n = nw()
    assert n["unknown_payments"] == 0 and cur(n, two["a"]) == A0 - TOTAL and n["spendable"] == s0


# ---------------------------------------------------------------- aynı saniye: çapa ve ödeme/aktarım sırası

def test_same_second_anchor_then_card_payment(client, two, tmpdb):
    assert client.patch(f"/api/accounts/{two['a']}", json={"balance": A0}).status_code == 200
    pay(client, two["sid"], TOTAL, account_id=two["a"])  # bakiye güncellemesinden SONRA
    set_ts(tmpdb, "UPDATE accounts SET updated_at = '2026-11-16 05:00:00' WHERE id = ?", two["a"])
    set_ts(tmpdb, "UPDATE card_payments SET created_at = '2026-11-16 05:00:00'")
    assert cur(nw(), two["a"]) == A0 - TOTAL


# ---------------------------------------------------------------- kendi hesapları arası aktarım

def test_transfer_never_changes_totals(client, two, tmpdb):
    """Aktarım (elle, dekontla; çapa öncesi/sonrası gün) toplam nakdi, Elinde'yi ve Ödemelerden sonra'yı değiştirmez."""
    before = nw()
    for d in ("2026-11-12", "2026-11-16"):
        r = client.post("/api/own-transfers", json={"date": d, "amount": 7000.0, "from_account_id": two["a"],
                                                    "to_account_id": two["b"]})
        assert r.status_code == 200, r.text
        n = nw()
        assert (n["cash"], n["free"], n["spendable"]) == (before["cash"], before["free"], before["spendable"])


def test_same_dekont_three_times(client, two, tmpdb):
    """Aynı dekont (aynı sorgu no, aynı yön) üç kez: tek aktarım, bakiyeler bir kez değişir."""
    body = {"date": "2026-11-16", "amount": 7000.0, "from_account_id": two["a"], "to_account_id": two["b"],
            "ref": "9900001234", "dekont_side": "giden"}
    codes = [client.post("/api/own-transfers", json=body).status_code for _ in range(3)]
    assert codes[0] == 200 and all(c in (200, 409) for c in codes)
    with tmpdb.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM own_transfers").fetchone()[0] == 2
    n = nw()
    assert (cur(n, two["a"]), cur(n, two["b"])) == (A0 - 7000.0, B0 + 7000.0)


def test_same_dekont_entry_three_times(client, two, tmpdb):
    """Kişiye giden dekont (gider) üç kez kaydedilmek istenir: bir kayıt, ikinci ve üçüncü 409."""
    body = {"date": "2026-11-16", "amount": 1500.0, "direction": "giden", "counterparty": "Örnek Usta",
            "account_id": two["a"], "ref": "9900005678"}
    codes = [client.post("/api/dekont/save", json=body).status_code for _ in range(3)]
    assert codes == [200, 409, 409]
    assert cur(nw(), two["a"]) == A0 - 1500.0


# ---------------------------------------------------------------- döngü ödemesi (ekstresi yüklenmemiş)

@pytest.fixture()
def cut_cycle(client, tmpdb, monkeypatch):
    """Kesimi 15 Kas'ta geçmiş, ekstresi yüklenmemiş döngü (son ödeme 25 Kas); geçmiş 3 dönem 10.000 + taksit 1.250."""
    freeze(monkeypatch, TODAY)
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    tmpdb.set_account_balance("Örnek Banka A", A0, "2026-11-10", "elle")
    for i, pe in enumerate([date(2026, 8, 15), date(2026, 9, 15), date(2026, 10, 15)]):
        nx = date(pe.year, pe.month + 1, 15)
        lines = [Transaction(txn_date=pe - timedelta(days=9), description="ORNEK MARKET", amount=10000.0, txn_type=PURCHASE,
                             card=CARD),
                 Transaction(txn_date=date(2026, 7, 5), description="ORNEK BEYAZ ESYA", amount=1250.0, txn_type=PURCHASE,
                             card=CARD, installment_no=1 + i, installments_left=5 - i, installment_left_amount=1250.0)]
        sid = _stmt(tmpdb, pe=pe, due=date(pe.year, pe.month, 25), total=11250.0, nxt=(nx, date(nx.year, nx.month, 25)),
                    lines=lines)
        tmpdb.mark_statement_paid(sid, 11250.0, date(pe.year, pe.month, 25).isoformat(), None)
    with tmpdb.connect() as c:
        return c.execute("SELECT id FROM accounts").fetchone()[0]


def _pending(n):
    return {x["account"]: x["amount"] for x in n["pending_cards_items"]}


@pytest.mark.parametrize("paid,left", [(1000.0, 250.0), (20000.0, 0.0)])
def test_cycle_partial_and_over_payment(client, cut_cycle, paid, left):
    """Kısmi (kesin kısmın altında) döngü ödemesi: kalan düşülür; fazla ödeme: döngü kapanır. Hesaptan: nakit düşer."""
    s0 = nw()["spendable"]
    r = client.post("/api/cards/cycle-pay", json={"card_no": CARD, "amount": paid, "account_id": cut_cycle})
    assert r.status_code == 200, r.text
    n = nw()
    assert _pending(n).get(CARD, 0.0) == left
    assert n["spendable"] == round(s0 - paid + (1250.0 - left), 2)


def test_cycle_pay_unknown_links_eft_without_digits(client, cut_cycle, tmpdb):
    _dokum(tmpdb, [("2026-11-16", "KREDI KARTI ODEMESI ORNEK BANKASI", -20000.0, A0 - 20000.0)], "Örnek Banka A",
           cut_cycle)
    assert _pending(nw()) == {CARD: 1250.0}  # hanesiz, 1,5×tahmin üstü: kendiliğinden sayılmadı (doğru)
    r = client.post("/api/cards/cycle-pay", json={"card_no": CARD, "amount": 20000.0, "paid_at": "2026-11-16"})
    assert r.status_code == 200 and r.json()["linked"] is True
    assert _pending(nw()) == {}


def test_cycle_pay_unknown_after_dokum_gider_no_double(client, cut_cycle, tmpdb):
    _dokum(tmpdb, [("2026-11-16", "FAST ODEME ORNEK BANKASI", -20000.0, A0 - 20000.0)], "Örnek Banka A", cut_cycle)
    set_ts(tmpdb, "UPDATE accounts SET updated_at = '2026-11-16 04:00:00'")
    r = client.post("/api/cards/cycle-pay", json={"card_no": CARD, "amount": 20000.0, "paid_at": "2026-11-16"})
    assert r.status_code == 200, r.text
    set_ts(tmpdb, "UPDATE card_payments SET created_at = '2026-11-16 05:00:00' WHERE import_hash IS NULL")
    n = nw()
    assert n["free"] == A0 - 20000.0, n["unknown_payments_items"]


def test_cycle_pay_then_undo_restores(client, cut_cycle):
    s0 = nw()["spendable"]
    client.post("/api/cards/cycle-pay", json={"card_no": CARD, "amount": 5000.0, "account_id": cut_cycle})
    assert client.request("DELETE", "/api/cards/cycle-pay", params={"card_no": CARD}).status_code == 200
    assert nw()["spendable"] == s0


# ---------------------------------------------------------------- hafta sonu / bayram son ödeme

def test_weekend_due_card_not_overdue_on_monday(client, tmpdb, monkeypatch):
    freeze(monkeypatch, date(2026, 11, 30))  # Pazartesi
    tmpdb.set_cycle_start_day(17)
    tmpdb.set_account_balance("Örnek Banka A", A0, "2026-11-29", "elle")
    _stmt(tmpdb, pe=date(2026, 11, 18), due=date(2026, 11, 28))  # Cumartesi
    n = finance.net_worth(date(2026, 11, 30))
    assert not n["card_late_interest_items"] and not n["card_interest_items"]  # iş günü kuralı: gecikmedi
    assert [x["overdue"] for x in n["card_debt_items"]] == [False]


# ---------------------------------------------------------------- kumbara, borç, eksi bakiye

@pytest.mark.parametrize("jar,payable,reserved", [(2000.0, 3000.0, 2000.0), (7000.0, 0.0, 7000.0)])
def test_jar_partially_covers_overdue_debt(client, two, tmpdb, jar, payable, reserved):
    """Gecikmiş 5.000 borç + bağlı kumbara: kumbaranın karşılamadığı kısım borç satırında, kumbara ayrı; borç iki kez
    düşülmez; kumbara borçtan büyükse fazlası yine ayrılmış (harcanamaz)."""
    r = client.post("/api/debts", json={"person": "Örnek Komşu", "direction": "borc", "amount": 5000.0,
                                        "debt_date": "2026-10-01", "due_date": "2026-11-10", "pay_terms": "tek"})
    assert r.status_code == 200, r.text
    client.put("/api/debt-plan", json={"monthly": 1000})
    j = client.post(f"/api/debts/{r.json()['id']}/jar", json={})
    assert j.status_code == 200, j.text
    assert client.post(f"/api/jars/{j.json()['id']}/moves", json={"amount": jar, "move_date": "2026-11-15"}).status_code == 200
    n = nw()
    p = {x["key"]: x["amount"] for x in n["spendable_parts"]}
    assert (p["payable"], p["reserved"]) == (payable, reserved)
    assert n["spendable"] == A0 + B0 - TOTAL - payable - reserved
    assert not [x for x in n["spendable_later"]["items"] if x["type"] == "borc"]


def test_negative_balance_alert_and_invariants(client, two, tmpdb):
    assert client.patch(f"/api/accounts/{two['b']}", json={"balance": -5000.0}).status_code == 200
    n = nw()
    assert n["spendable"] == A0 - 5000.0 - TOTAL
    assert any(a["kind"] == "eksi" and a["account_id"] == two["b"] for a in n["account_alerts"])
    _invariants(n)


def _invariants(n):
    fb = n["forecast_balance"]
    income = (n["spendable_after_income"] or {}).get("income") or 0.0
    assert fb["amount"] <= fb["no_new_spend"] <= round(n["spendable"] + income, 2) + 0.005
    assert n["spendable"] <= n["free"] + 0.005
    assert n["spendable_later"]["total"] >= 0


def test_fx_debt_not_in_tl_spendable(client, two):
    """Dolar borcu TL Ödemelerden sonra'ya ve spendable_later'a girmez (Transferler/hatırlatma)."""
    s0 = nw()["spendable"]
    r = client.post("/api/debts", json={"person": "Örnek Dost", "direction": "borc", "asset": "USD", "amount": 300.0,
                                        "debt_date": "2026-11-01", "due_date": "2026-11-20"})
    assert r.status_code == 200, r.text
    n = nw()
    assert n["spendable"] == s0 and not n["spendable_later"]["items"]


# ---------------------------------------------------------------- boş kullanıcı, eski istemci

def test_empty_user_zeros_no_error(client, tmpdb, monkeypatch):
    freeze(monkeypatch, TODAY)
    r = client.get("/api/net-worth")
    assert r.status_code == 200, r.text
    n = r.json()
    assert (n["free"], n["spendable"], n["cash"], n["unknown_payments"]) == (0, 0, 0, 0)
    assert n["spendable_later"] == {"total": 0, "items": [], "from": n["spendable_horizon"], "until": n["spendable_later"]["until"]}
    assert n["forecast_balance"]["amount"] == 0 and n["spendable_after_income"] is None
    assert client.get("/api/payables", params={"totals": 1}).status_code == 200


OLD_KEYS = {"reserved", "free", "spendable", "accounts", "cash", "cards", "card_debt", "card_estimated", "receivable",
            "payable", "wallets", "in_wallets", "credit_cards", "unknown_banks", "holdings", "family_holdings", "zakat_due",
            "net", "net_after_estimates", "unknown_payments", "unknown_payments_items", "account_alerts", "net_display",
            "card_overdue", "totals", "spendable_parts", "pending_cards", "pending_cards_items", "card_debt_items",
            "pending_other_items", "card_interest_items", "card_late_interest_items", "bills_items", "recurring_items",
            "spendable_after_income", "spendable_explain", "stale"}


def test_old_client_shape_kept(client, two, tmpdb):
    """Eski istemci: net-worth'ün önceki anahtarları ve spendable_parts'ın her zaman olan satırları (sırası) yerinde;
    yeni alanlar yalnız ek."""
    pay(client, two["sid"], TOTAL)
    n = client.get("/api/net-worth").json()
    assert OLD_KEYS <= set(n)
    keys = [p["key"] for p in n["spendable_parts"]]
    always = [k for k in keys if k in ("cash", "card_debt", "pending_cards", "payable", "reserved", "zakat_due")]
    assert always == ["cash", "card_debt", "pending_cards", "payable", "reserved", "zakat_due"]
    assert {"title", "card_no", "next_estimate", "next_due", "cycle", "unpaid"} <= set(n["credit_cards"][0])
    for it in client.get("/api/payables").json():
        assert {"type", "id", "title", "amount", "due_date", "status", "in_total"} <= set(it)


# ---------------------------------------------------------------- ortak defter üyesi

@pytest.fixture()
def owner(client, monkeypatch):
    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    return client


def _member(owner, name="esim"):
    from fastapi.testclient import TestClient

    from app.api.main import app

    inv = owner.post("/api/book/invites", json={"note": name}).json()
    m = TestClient(app)
    assert m.post("/api/auth/register", json={"code": inv["code"], "username": name,
                                              "password": f"{name}-sifre-12345"}).status_code == 200
    return m


def test_member_sees_same_unknown_decision(owner, tmpdb, monkeypatch):
    freeze(monkeypatch, TODAY)
    tmpdb.set_cycle_start_day(17)
    for name, b in (("Örnek Banka A", A0), ("Örnek Banka B", B0)):
        assert owner.post("/api/accounts", json={"name": name, "kind": "banka", "balance": b,
                                                 "as_of": "2026-11-10"}).status_code == 200
    with tmpdb.connect() as c:
        ids = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM accounts")}
    sid = _stmt(tmpdb)
    m = _member(owner)
    assert owner.put("/api/book/rules", json={"hidden_accounts": [ids["Örnek Banka A"]]}).status_code == 200
    r = owner.post(f"/api/statements/{sid}/pay", json={"amount": TOTAL, "paid_at": "2026-11-16"})
    assert r.status_code == 200, r.text
    set_ts(tmpdb, "UPDATE statements SET paid_marked_at = '2026-11-16 03:00:00' WHERE id = ?", sid)
    # gizli A hesabının bakiyesi ödemeden SONRA güncellendi (ödeme A'dan çıkmış)
    assert owner.patch(f"/api/accounts/{ids['Örnek Banka A']}", json={"balance": A0 - TOTAL}).status_code == 200
    set_ts(tmpdb, "UPDATE accounts SET updated_at = '2026-11-16 05:00:00' WHERE id = ?", ids["Örnek Banka A"])
    on, mn = owner.get("/api/net-worth").json(), m.get("/api/net-worth").json()
    assert on["unknown_payments"] == 0
    assert mn["unknown_payments"] == on["unknown_payments"]
