"""GÜN TEKRARI (bağımsız doğrulama, lead 16.11): gerçek bir kullanıcının 16 Kas 2026 sabahının YAPISI adım adım yeniden
oynanır; her adımdan sonra Elinde (free), Ödemelerden sonra (spendable), Maaştan sonra ödenecek (spendable_later), Ay
sonunda kalacak (forecast_balance) ve hesap bakiyeleri ELLE hesaplanmış değerlerle karşılaştırılır (hesap yorumda).

Bütün kişi adları, kart numaraları, IBAN'lar, sorgu numaraları UYDURMADIR; yalnız tutarlar/tarihler senaryonun yapısını
taşır. Ay dönümü 17: dönem 2026-11 = 17 Eki – 16 Kas, 2026-12 = 17 Kas – 16 Ara, 2027-01 = 17 Ara – 16 Oca.

Tutarlar da uydurmadır (gerçek senaryodan ölçeklenip değiştirildi; ilişkiler korundu).

Hesaplar (çapa):  Halkbank 15 Kas 281.736,17 (15 Kas maaşı dahil; maaş kaydı çapadan önce girildi)
                  VakıfBank 29 Eki 1.743,58; sonra −615,40 (5 Kas), −238,90 (10 Kas), −31.250 kira (16 Kas, 18 Kas vadeli
                  kira erken ödendi)  → 1.743,58 − 32.104,30 = −30.360,72
                  Kuveyt Türk 18 Eki 1.187,06
Kartlar:          V1 VakıfBank ****0001: kesim 15 Kas, son ödeme 25 Kas, ekstre YÜKLENMEMİŞ (Eki ekstresi son; değişken
                  geçmiş 36.000/37.000/38.000 → medyan 37.000 + bilinen taksit 1.350 = tahmin 38.350; kesin kısım 1.350)
                  V2 VakıfBank ****0002: Kas ekstresi 46.832,72 (kesim 13 Kas, son ödeme 23 Kas; Ara'ya taksit 2.750)
                  K1 Kuveyt ****0003: Kas ekstresi 44.917,36 (kesim 15 Kas, son ödeme 25 Kas; Ara'ya taksit 2.150)
Kumbara 83.000 → 31 Ara vadeli 83.000 borca bağlı. Ödeme planlı borç 7 × 1.750 (Mayıs–Kasım 15'i; hepsi gecikmiş).
Faturalar 467 + 4.890 (19 Kas). Düzenli kira 31.250 (ayın 18'i). Maaş 86.500 (ayın 15'i).
"""
from __future__ import annotations

import sys
from datetime import date

import pytest

from app import finance
from app.parsers.base import PURCHASE, Statement, Transaction

OWNER = "DENEME KİŞİOĞLU"
OWNER_ASCII = "DENEME KISIOGLU"
V1, V2, K1 = "5400***0001", "5400***0002", "5200***0003"
HALK0, VAKIF0, KUVEYT0 = 281736.17, 1743.58, 1187.06
V2_TOTAL, K1_TOTAL = 46832.72, 44917.36
JAR = 83000.0
INSTALLMENT = 1750.0
KISI_BORC = 7 * INSTALLMENT
BILL1, BILL2 = 467.0, 4890.0
BILLS = BILL1 + BILL2
RENT = 31250.0
SALARY = 86500.0
V1_INST, V2_INST, K1_INST = 1350.0, 2750.0, 2150.0
T_VAKIF, T_KUVEYT, T_OWN = 128500.0, 45200.0, 12600.0  # Halk → Vakıf, Halk → Kuveyt, kendi adına alıcısız FAST
V1_PAY, VAKIF_F = 48900.0, 14650.0  # e) V1 döngü ödemesi, f) Vakıf elle bakiye
TODAY = date(2026, 11, 16)
TOMORROW = date(2026, 11, 17)


# ---------------------------------------------------------------- saat dondurma (CI başka günde koşsa da aynı sonuç)

def freeze(monkeypatch, d: date) -> None:
    """app.* modüllerindeki `date` adını d'yi bugün sayan alt sınıfla değiştir (modül düzeyinde import edilenler)."""
    import datetime as _dt

    class _D(_dt.date):
        @classmethod
        def today(cls):
            return cls(d.year, d.month, d.day)

    for name, mod in list(sys.modules.items()):
        if (name == "app" or name.startswith("app.")) and getattr(mod, "date", None) is _dt.date:
            monkeypatch.setattr(mod, "date", _D)
    # bir kez dondurulmuş modül (_D) yeniden dondurulursa
    for name, mod in list(sys.modules.items()):
        cur = getattr(mod, "date", None)
        if (name == "app" or name.startswith("app.")) and isinstance(cur, type) and cur.__name__ == "_D" and cur is not _D:
            monkeypatch.setattr(mod, "date", _D)


# ---------------------------------------------------------------- kurulum

def _stmt(tmpdb, bank, card, pe, due, nxt_pe, nxt_due, lines, total=None):
    total = round(sum(x.amount for x in lines), 2) if total is None else total
    s = Statement(bank=bank, card_no=card, period_end=pe, due_date=due, total_debt=total,
                  min_payment=round(total * 0.2, 2), credit_limit=150000, period_activity=total,
                  next_period_end=nxt_pe, next_due_date=nxt_due)
    s.transactions = lines
    tmpdb.save_statement(f"st-{card}-{pe}".encode(), "e.pdf", s)
    return next(x["id"] for x in tmpdb.list_statements() if x["period_end"] == pe.isoformat() and x["card_no"] == card)


def _buy(d, amt, card, desc="ORNEK MARKET"):
    return Transaction(txn_date=d, description=desc, amount=amt, txn_type=PURCHASE, card=card, card_holder=OWNER)


def _inst(d, amt, card, no, left, desc="ORNEK BEYAZ ESYA"):
    return Transaction(txn_date=d, description=desc, amount=amt, txn_type=PURCHASE, card=card, installment_no=no,
                       installments_left=left, installment_left_amount=amt, card_holder=OWNER)


def acc_ids(tmpdb) -> dict[str, int]:
    with tmpdb.connect() as c:
        return {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM accounts")}


def set_ts(tmpdb, sql: str, *args) -> None:
    with tmpdb.connect() as c:
        c.execute(sql, args)


def build(client, tmpdb, monkeypatch) -> dict:
    """Başlangıç durumu (16 Kas 2026, gün başı). Saatler UTC (SQLite CURRENT_TIMESTAMP gibi): TR = UTC+3."""
    freeze(monkeypatch, TODAY)
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    tmpdb.remember_owner_name(OWNER)
    # ---- kartlar
    # V1: Ağu/Eyl/Eki ekstreleri ödendi (hesaptan değil: eski, belirsiz değil); Eki ekstresinde 3 taksit kaldı (1.350)
    for i, (pe, var) in enumerate([(date(2026, 8, 15), 36000.0), (date(2026, 9, 15), 37000.0), (date(2026, 10, 15), 38000.0)]):
        nx = date(pe.year, pe.month + 1, 15)
        sid = _stmt(tmpdb, "VakıfBank", V1, pe, date(pe.year, pe.month, 25), nx, date(nx.year, nx.month, 25),
                    [_buy(date(pe.year, pe.month, 3), var, V1), _inst(date(2026, 7, 1), V1_INST, V1, 2 + i, 5 - i)])
        tmpdb.mark_statement_paid(sid, var + V1_INST, date(pe.year, pe.month, 25).isoformat(), None)
    # V2: Eki (ödendi) + Kas (46.832,72; ödenmemiş)
    sid = _stmt(tmpdb, "VakıfBank", V2, date(2026, 10, 13), date(2026, 10, 23), date(2026, 11, 13), date(2026, 11, 23),
                [_buy(date(2026, 10, 2), 39800.0, V2), _inst(date(2026, 10, 1), V2_INST, V2, 1, 3)])
    tmpdb.mark_statement_paid(sid, 39800.0 + V2_INST, "2026-10-23", None)
    v2 = _stmt(tmpdb, "VakıfBank", V2, date(2026, 11, 13), date(2026, 11, 23), date(2026, 12, 13), date(2026, 12, 23),
               [_buy(date(2026, 11, 2), V2_TOTAL - V2_INST, V2), _inst(date(2026, 10, 1), V2_INST, V2, 2, 2)])
    # K1: Eki (ödendi) + Kas (44.917,36; ödenmemiş)
    sid = _stmt(tmpdb, "Kuveyt Türk", K1, date(2026, 10, 15), date(2026, 10, 25), date(2026, 11, 15), date(2026, 11, 25),
                [_buy(date(2026, 10, 4), 41300.0, K1), _inst(date(2026, 9, 1), K1_INST, K1, 2, 4)])
    tmpdb.mark_statement_paid(sid, 41300.0 + K1_INST, "2026-10-25", None)
    k1 = _stmt(tmpdb, "Kuveyt Türk", K1, date(2026, 11, 15), date(2026, 11, 25), date(2026, 12, 15), date(2026, 12, 25),
               [_buy(date(2026, 11, 4), K1_TOTAL - K1_INST, K1), _inst(date(2026, 9, 1), K1_INST, K1, 3, 3)])
    # ---- düzenli kalemler
    r = client.post("/api/recurring", json={"description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                                            "day_of_month": 15, "start_month": "2026-01"})
    assert r.status_code == 200, r.text
    rent = client.post("/api/recurring", json={"description": "Ev kirası", "amount": RENT, "kind": "gider",
                                               "category": "Kira & Aidat", "day_of_month": 18, "start_month": "2026-06"})
    assert rent.status_code == 200, rent.text
    # ---- hesaplar: maaş (15 Kas) Halkbank çapasından ÖNCE girildi → çapanın içinde
    tmpdb.set_account_balance("Halkbank", 1.0, "2026-11-01", "elle")
    tmpdb.set_account_balance("VakıfBank", 1.0, "2026-10-01", "elle")
    tmpdb.set_account_balance("Kuveyt Türk", KUVEYT0, "2026-10-18", "elle")
    ids = acc_ids(tmpdb)
    for body in ({"entry_date": "2026-11-15", "description": "Maaş", "amount": SALARY, "kind": "gelir", "category": "Maaş",
                  "account_id": ids["Halkbank"]},
                 {"entry_date": "2026-10-18", "description": "Ev kirası", "amount": RENT, "kind": "gider",
                  "category": "Kira & Aidat", "payment": "havale", "account_id": ids["VakıfBank"]}):
        assert client.post("/api/entries", json=body).status_code == 200
    tmpdb.set_account_balance("Halkbank", HALK0, "2026-11-15", "elle")
    tmpdb.set_account_balance("VakıfBank", VAKIF0, "2026-10-29", "elle")
    set_ts(tmpdb, "UPDATE accounts SET updated_at = CASE name WHEN 'Halkbank' THEN '2026-11-15 18:00:00' "
                  "WHEN 'VakıfBank' THEN '2026-10-29 10:00:00' ELSE '2026-10-18 10:00:00' END")
    for body in ({"entry_date": "2026-11-05", "description": "Örnek Eczane", "amount": 615.40},
                 {"entry_date": "2026-11-10", "description": "Örnek Fırın", "amount": 238.90}):
        assert client.post("/api/entries", json={**body, "kind": "gider", "category": "Market", "payment": "nakit",
                                                 "account_id": ids["VakıfBank"]}).status_code == 200
    r = client.post("/api/entries", json={"entry_date": "2026-11-16", "description": "Ev kirası", "amount": RENT,
                                          "kind": "gider", "category": "Kira & Aidat", "payment": "havale",
                                          "account_id": ids["VakıfBank"]})
    assert r.status_code == 200, r.text
    # ---- fatura, borçlar, kumbara
    for prov, amt in (("Örnek Elektrik", BILL1), ("Örnek Doğalgaz", BILL2)):
        assert client.post("/api/bills", json={"provider": prov, "amount": amt, "due_date": "2026-11-19"}).status_code == 200
    r = client.post("/api/debts", json={"person": "Örnek Alacaklı", "direction": "borc", "creditor_type": "kisi",
                                        "plan_installment": INSTALLMENT, "plan_count": 7, "plan_start": "2026-05",
                                        "plan_day": 15, "debt_date": "2026-05-01"})
    assert r.status_code == 200, r.text
    sultan = r.json()["id"]
    r = client.post("/api/debts", json={"person": "Örnek Akraba", "direction": "borc", "amount": JAR,
                                        "debt_date": "2026-06-01", "due_date": "2026-12-31", "pay_terms": "tek"})
    assert r.status_code == 200, r.text
    big = r.json()["id"]
    client.put("/api/debt-plan", json={"monthly": 1000})
    r = client.post(f"/api/debts/{big}/jar", json={"account_id": ids["Halkbank"]})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    r = client.post(f"/api/jars/{jid}/moves", json={"amount": JAR, "move_date": "2026-11-15", "note": "Borç"})
    assert r.status_code == 200, r.text
    return {"ids": ids, "v2": v2, "k1": k1, "sultan": sultan, "big": big, "jar": jid, "rent": rent.json()["id"]}


# ---------------------------------------------------------------- adımlar

DEKONT = {"date": "2026-11-16", "amount": T_VAKIF, "ref": "9900000777"}


def step_a(client, tmpdb, st):
    """K1 ve V2 hesapsız (account_id anahtarı YOK) "ödendi", 16 Kas 06:00 (UTC 03:00)."""
    for sid, amt in ((st["v2"], V2_TOTAL), (st["k1"], K1_TOTAL)):
        r = client.post(f"/api/statements/{sid}/pay", json={"amount": amt, "paid_at": "2026-11-16"})
        assert r.status_code == 200, r.text
        assert r.json()["link"] is None  # hesabı belirsiz
    set_ts(tmpdb, "UPDATE statements SET paid_marked_at = '2026-11-16 03:00:00' WHERE id IN (?, ?)", st["v2"], st["k1"])


def step_b(client, tmpdb, st):
    """Halk → Vakıf 128.500: iki dekont (gönderen ve alıcı bankanın), aynı sorgu no → tek aktarım (iki bacak)."""
    halk, vakif = st["ids"]["Halkbank"], st["ids"]["VakıfBank"]
    a = client.post("/api/own-transfers", json={**DEKONT, "from_account_id": halk, "to_account_id": vakif,
                                                "dekont_side": "giden"})
    assert a.status_code == 200, a.text
    b = client.post("/api/own-transfers", json={**DEKONT, "from_account_id": halk, "to_account_id": vakif,
                                                "dekont_side": "gelen"})
    assert b.status_code == 200, b.text
    assert b.json()["linked"] is True and b.json()["id"] == a.json()["id"]
    set_ts(tmpdb, "UPDATE own_transfers SET created_at = '2026-11-16 03:10:00'")


HALK_ROWS = [  # eskiden yeniye; bakiye zinciri: 281.736,17 → 153.236,17 → 108.036,17 → 95.436,17
    ("2026-11-16", f"GIDEN FAST - {OWNER_ASCII} TARAFINDAN GÖNDERILEN,Deneme Kişioğlu,Türkiye Vakiflar Bankasi T.A.O.,"
                   "TR220001500000000000123456", -T_VAKIF, 153236.17),
    ("2026-11-16", f"GIDEN FAST - {OWNER_ASCII} TARAFINDAN GÖNDERILEN,Deneme Kişioğlu,Kuveyt Türk Katilim Bankasi A.S.,"
                   "TR330020500000000000654321", -T_KUVEYT, 108036.17),
    ("2026-11-16", f"GIDEN FAST - {OWNER_ASCII} TARAFINDAN GÖNDERİLEN", -T_OWN, 95436.17),
]
KUVEYT_ROWS = [  # 1.187,06 → 46.387,06 → 1.469,70
    ("2026-11-16", f"FAST Gönderen: {OWNER} Alıcı: {OWNER} FAST", T_KUVEYT, 46387.06),
    ("2026-11-16", "KREDI KARTI ODEMESI 5200 **** 0003", -K1_TOTAL, 1469.70),
]


def _import(tmpdb, rows, account, acc_id):
    from app import bank_import

    raw = [{"date": d, "description": desc, "amount": amt, "balance": bal} for d, desc, amt, bal in rows]
    fin = bank_import.finalize(raw, [OWNER])
    return fin, tmpdb.import_bank_rows(fin, account=account, owners=[OWNER], account_id=acc_id)


def step_c(client, tmpdb, st):
    """Halkbank dökümü (16 Kas 07:11 TR = 04:11 UTC): Halk→Vakıf 128.500 (dekontla birleşir), Halk→Kuveyt 45.200,
    −12.600 kendi adına giden FAST (alıcı yazmıyor), yeni çapa 95.436,17."""
    fin, stats = _import(tmpdb, HALK_ROWS, "Halkbank", st["ids"]["Halkbank"])
    st["halk_rows"], st["halk_stats"] = fin, stats
    set_ts(tmpdb, "UPDATE accounts SET updated_at = '2026-11-16 04:11:00' WHERE name = 'Halkbank'")


def step_d(client, tmpdb, st):
    """Kuveyt dökümü: +45.200 (kendi hesabından), −44.917,36 kart ödemesi (K1'e bağlanmalı), çapa 1.469,70."""
    fin, stats = _import(tmpdb, KUVEYT_ROWS, "Kuveyt Türk", st["ids"]["Kuveyt Türk"])
    st["kuveyt_rows"], st["kuveyt_stats"] = fin, stats
    set_ts(tmpdb, "UPDATE accounts SET updated_at = '2026-11-16 04:20:00' WHERE name = 'Kuveyt Türk'")


def step_e(client, tmpdb, st):
    """V1 (ekstresi gelmemiş, kesim 15 Kas) döngüsü 48.900 ödendi, VakıfBank hesabından."""
    r = client.post("/api/cards/cycle-pay", json={"card_no": V1, "amount": V1_PAY, "paid_at": "2026-11-16",
                                                   "account_id": st["ids"]["VakıfBank"]})
    assert r.status_code == 200, r.text
    set_ts(tmpdb, "UPDATE card_payments SET created_at = '2026-11-16 04:30:00' WHERE import_hash IS NULL")


def step_f(client, tmpdb, st):
    """VakıfBank bakiyesi elle 14.650 (bugün, ödemelerden SONRA)."""
    r = client.patch(f"/api/accounts/{st['ids']['VakıfBank']}", json={"balance": VAKIF_F})
    assert r.status_code == 200, r.text
    set_ts(tmpdb, "UPDATE accounts SET updated_at = '2026-11-16 05:00:00' WHERE name = 'VakıfBank'")


def step_h(client, tmpdb, st):
    """Kişi borcu (7 × 1.750) nakit (hesaptan değil) ödendi, 17 Kas."""
    r = client.post(f"/api/debts/{st['sultan']}/pay", json={"paid_at": "2026-11-17"})
    assert r.status_code == 200, r.text


STEPS = {"a": step_a, "b": step_b, "c": step_c, "d": step_d, "e": step_e, "f": step_f}


def run(client, tmpdb, monkeypatch, until: str) -> tuple[dict, dict]:
    st = build(client, tmpdb, monkeypatch)
    for k, fn in STEPS.items():
        if k > until and until in STEPS:
            break
        fn(client, tmpdb, st)
    day = TODAY
    if until in ("g", "h"):
        day = TOMORROW
        freeze(monkeypatch, TOMORROW)
    if until == "h":
        step_h(client, tmpdb, st)
    return st, finance.net_worth(day)


def bal(nw) -> dict[str, float]:
    return {a["name"]: a["current"] for a in nw["accounts"]}


def later(nw) -> dict:
    return {(x["type"], x["title"].split()[-1] if x["type"] == "card_estimate" else x["title"], x["due_date"]): x["amount"]
            for x in nw["spendable_later"]["items"]}


def snap(nw) -> dict:
    return {"free": nw["free"], "spendable": nw["spendable"], "later": nw["spendable_later"]["total"],
            "fb": nw["forecast_balance"]["amount"], "fb_nns": nw["forecast_balance"]["no_new_spend"], "bal": bal(nw),
            "unknown": nw["unknown_payments"], "parts": {p["key"]: p["amount"] for p in nw["spendable_parts"]}}


def invariants(nw) -> None:
    """Lead'in değişmezleri: forecast ≤ no_new_spend ≤ spendable + beklenen gelir; spendable ≤ free; later ≥ 0."""
    fb = nw["forecast_balance"]
    income = (nw["spendable_after_income"] or {}).get("income") or 0.0
    assert fb["amount"] <= fb["no_new_spend"] + 0.005
    assert fb["no_new_spend"] <= round(nw["spendable"] + income, 2) + 0.005
    assert nw["spendable"] <= nw["free"] + 0.005
    assert nw["spendable_later"]["total"] >= 0 and all(x["amount"] > 0 for x in nw["spendable_later"]["items"])
    p = {x["key"]: x["amount"] for x in fb["parts"]}
    assert round(p["free"] - p["payments"] + p["income"] - p["variable"], 2) == fb["amount"]


# ---------------------------------------------------------------- beklenen değerler (ELLE)
#
# Bakiyeler:
#   başlangıç  H 281.736,17 | V 1.743,58 − 615,40 − 238,90 − 31.250 = −30.360,72 | K 1.187,06    nakit 252.562,51
#   b) aktarım H −128.500 = 153.236,17 | V +128.500 = 98.139,28                                 nakit 252.562,51 (aynı)
#   c) H dökümü çapası 95.436,17 (= 281.736,17 − 128.500 − 45.200 − 12.600)                    nakit 194.762,51
#   d) K dökümü çapası 1.469,70 (= 1.187,06 + 45.200 − 44.917,36)                              nakit 195.045,15
#   e) V −48.900 (döngü ödemesi V hesabından) = 49.239,28                                        nakit 146.145,15
#   f) V elle 14.650                                                                            nakit 111.555,87
# Belirsiz ödeme (hesapsız ödendi): a'dan sonra V2 46.832,72 + K1 44.917,36 = 91.750,08; d'de K1 Kuveyt dökümündeki
#   kart ödemesine bağlanır; V2 ancak f'de (V bakiyesi ödemeden SONRA girildi) "bakiyenin içinde" olur. c, d, e'de
#   güncellenen hesapların (H, K) dökümü V2 ödemesini İÇERMİYOR → V2 hâlâ düşülmeli (aday hesap kuralı).
# Elinde (free) = nakit − belirsiz − kumbara 83.000:
#   0: 252.562,51 − 0 − 83.000 = 169.562,51      a/b: 252.562,51 − 91.750,08 − 83.000 = 77.812,43
#   c: 194.762,51 − 91.750,08 − 83.000 = 20.012,43 (Kuveyt'e giden 45.200 henüz Kuveyt'te görünmüyor: ihtiyatlı)
#   d: 195.045,15 − 46.832,72 − 83.000 = 65.212,43  e: 146.145,15 − 46.832,72 − 83.000 = 16.312,43
#   f/g/h: 111.555,87 − 0 − 83.000 = 28.555,87  (ölçekli kullanıcı beklentisi 28.100–28.600 ✓)
# Ödemelerden sonra (spendable) = free − kart ekstresi − V1 döngüsünün kesin kısmı (1.350; e'den sonra ödendi → 0)
#   − kişi borcu 12.250 (7 taksit, hepsi gecikmiş) − faturalar 5.357 (19 Kas ≤ ufuk) − kira 0 (18 Kas kirası 16 Kas'ta
#   ödendi; 18 Eki kirası girildi) − 83.000 borç (kumbara karşılıyor → 0):
#   0: 169.562,51 − 91.750,08 (ekstreler) − 1.350 − 12.250 − 5.357 = 58.855,43
#   a/b: 77.812,43 − 1.350 − 17.607 = 58.855,43 (ödeme kart borcundan belirsize geçti: değişmez)
#   c: 20.012,43 − 18.957 = 1.055,43   d: 65.212,43 − 18.957 = 46.255,43   e: 16.312,43 − 17.607 = −1.294,57
#   f/g: 28.555,87 − 17.607 = 10.948,87   h: kişi borcu nakit ödendi → 28.555,87 − 5.357 = 23.198,87
# Maaştan sonra (spendable_later): V2 Ara döngüsü (son ödeme 23 Ara > ufuk 16 Ara) kesin kısmı = taksit 2.750; K1 Ara
#   (25 Ara) 2.150; V1'in Ara döngüsü modellenmiyor (ekstresi yüklenmemiş kartta yalnız açık döngü). Toplam 4.900.
# Ay sonunda kalacak: no_new_spend = spendable + Ara maaşı (15 Ara ≤ ufuk) 86.500.

EXP = {  # adım: (bakiyeler H, V, K), free, spendable
    "0": ((HALK0, -30360.72, KUVEYT0), 169562.51, 58855.43),
    "a": ((HALK0, -30360.72, KUVEYT0), 77812.43, 58855.43),
    "b": ((153236.17, 98139.28, KUVEYT0), 77812.43, 58855.43),
    "c": ((95436.17, 98139.28, KUVEYT0), 20012.43, 1055.43),
    "d": ((95436.17, 98139.28, 1469.70), 65212.43, 46255.43),
    "e": ((95436.17, 49239.28, 1469.70), 16312.43, -1294.57),
    "f": ((95436.17, 14650.0, 1469.70), 28555.87, 10948.87),
    "g": ((95436.17, 14650.0, 1469.70), 28555.87, 10948.87),
    "h": ((95436.17, 14650.0, 1469.70), 28555.87, 23198.87),
}
def _go(client, tmpdb, monkeypatch, k):
    if k == "0":
        build(client, tmpdb, monkeypatch)
        return finance.net_worth(TODAY)
    return run(client, tmpdb, monkeypatch, k)[1]


@pytest.mark.parametrize("k", list(EXP))
def test_step_balances_and_invariants(client, tmpdb, monkeypatch, k):
    """Her adımda: hesap bakiyeleri elle hesaplananla aynı; değişmezler; kira hiçbir yerde; Ara kart tahminleri
    spendable'da değil later'da; ufuk 16 Ara."""
    n = _go(client, tmpdb, monkeypatch, k)
    b = bal(n)
    assert (b["Halkbank"], b["VakıfBank"], b["Kuveyt Türk"]) == EXP[k][0]
    invariants(n)
    assert n["spendable_horizon"] == "2026-12-16"
    assert not n["recurring_items"] and not [x for x in n["spendable_later"]["items"] if "kira" in x["title"].lower()]
    assert later(n) == {("card_estimate", V2, "2026-12-23"): V2_INST, ("card_estimate", K1, "2026-12-25"): K1_INST}
    assert not [x for x in n["pending_cards_items"] if x["due_date"] > "2026-12-16"]
    p = {x["key"]: x["amount"] for x in n["spendable_parts"]}
    assert p["payable"] == (0.0 if k == "h" else KISI_BORC) and p["bills"] == BILLS and p["reserved"] == JAR
    assert n["forecast_balance"]["no_new_spend"] == round(n["spendable"] + SALARY, 2)


@pytest.mark.parametrize("k", list(EXP))
def test_step_free_and_spendable(client, tmpdb, monkeypatch, k):
    n = _go(client, tmpdb, monkeypatch, k)
    assert (n["free"], n["spendable"]) == EXP[k][1:], (n["free"], n["spendable"], n["unknown_payments_items"])


def test_b_two_dekonts_one_transfer(client, tmpdb, monkeypatch):
    run(client, tmpdb, monkeypatch, "b")
    with tmpdb.connect() as c:
        legs = [tuple(r) for r in c.execute("SELECT account, amount, pair_id FROM own_transfers ORDER BY amount")]
    assert legs == [("Halkbank", -T_VAKIF, 1), ("VakıfBank", T_VAKIF, 1)]


def test_c_halk_dokum_merges_and_classifies(client, tmpdb, monkeypatch):
    """Dekontla girilmiş 128.500 bacağı dökümle birleşir (yeni kayıt yok); 45.200 kendi hesabına (virman); alıcısız kendi
    adına 12.600 FAST da virman bacağı (karşı hesabı belli değil: pair yok; H6, finans-tutarlılık denetimi)."""
    st, _ = run(client, tmpdb, monkeypatch, "c")
    assert [r["type"] for r in st["halk_rows"]][:2] == ["virman", "virman"]
    with tmpdb.connect() as c:
        out = [tuple(r) for r in c.execute("SELECT amount, pair_id, import_hash IS NOT NULL FROM own_transfers "
                                           "WHERE account = 'Halkbank' ORDER BY amount")]
    assert out == [(-T_VAKIF, 1, 1), (-T_KUVEYT, None, 1), (-T_OWN, None, 1)]


def test_c_own_name_fast_without_receiver_is_not_expense(client, tmpdb, monkeypatch):
    run(client, tmpdb, monkeypatch, "c")
    with tmpdb.connect() as c:
        assert not c.execute("SELECT 1 FROM entries WHERE ROUND(amount) = ? AND kind = 'gider'", (T_OWN,)).fetchone()


def test_d_kuveyt_card_payment_links_k1(client, tmpdb, monkeypatch):
    st, n = run(client, tmpdb, monkeypatch, "d")
    with tmpdb.connect() as c:
        cp = dict(c.execute("SELECT * FROM card_payments WHERE import_hash IS NOT NULL").fetchone())
        legs = [tuple(r) for r in c.execute("SELECT account, amount FROM own_transfers WHERE ABS(amount) = ? "
                                            "ORDER BY amount", (T_KUVEYT,))]
    assert cp["statement_id"] == st["k1"] and cp["card_last4"] == "0003"
    assert legs == [("Halkbank", -T_KUVEYT), ("Kuveyt Türk", T_KUVEYT)]
    assert [u["statement_id"] for u in n["unknown_payments_items"]] == [st["v2"]]


def test_e_cycle_paid_and_rent_slot(client, tmpdb, monkeypatch):
    st, n = run(client, tmpdb, monkeypatch, "e")
    v1 = next(c for c in n["credit_cards"] if c["card_no"] == V1)
    assert v1["next_paid"]["covered"] is True and v1["next_paid"]["amount"] == V1_PAY
    assert v1["next_estimate"] == 37000.0 + V1_INST  # medyan 37.000 + bilinen taksit
    # V1 ödendi: net_display tahmininde yalnız V2 ve K1 Ara döngüleri: medyan + bilinen taksit
    # V2: medyan(39.800 + 2.750 [Eki, ilk taksit değişken]; 46.832,72 − 2.750) = (42.550 + 44.082,72) / 2 = 43.316,36 → +2.750
    # K1: medyan(41.300; 44.917,36 − 2.150) = (41.300 + 42.767,36) / 2 = 42.033,68 → +2.150
    assert not n["pending_cards_items"] and n["card_estimated"] == round(46066.36 + 44183.68, 2)
    with tmpdb.connect() as c:
        months = [r[0] for r in c.execute("SELECT month FROM entries WHERE recurring_id = ? ORDER BY entry_date",
                                          (st["rent"],))]
    assert months == ["2026-11", "2026-12"]  # 16 Kas'ta ödenen, 18 Kas vadeli kira sonraki dönemin


def test_f_user_expectation(client, tmpdb, monkeypatch):
    """Kullanıcının f sonrası beklentisi (ölçekli): Elinde ≈ 28.100–28.600; Ödemelerden sonra ≈ Elinde − kişi borcu
    12.250 − faturalar 5.357;
    Ara kart tahminleri spendable'da yok (later'da); kira hiçbir yerde."""
    _, n = run(client, tmpdb, monkeypatch, "f")
    assert 28100 <= n["free"] <= 28600
    assert n["spendable"] == round(n["free"] - KISI_BORC - BILLS, 2)
    assert n["unknown_payments"] == 0 and n["pending_cards"] == 0


def test_g_next_day_new_period(client, tmpdb, monkeypatch):
    """17 Kas (yeni dönem 2026-12): ufuk yine 16 Ara (tek dönem), later 16 Oca'ya kadar; kira (18 Kas) bekleyen değil."""
    _, n = run(client, tmpdb, monkeypatch, "g")
    assert finance.spend_horizon(TOMORROW)["periods"] == ["2026-12"]
    assert n["spendable_later"]["until"] == "2027-01-16" and not n["recurring_items"]


def test_forecast_variable_on_last_day(client, tmpdb, monkeypatch):
    """Lead kararı (16.11): "Ay sonunda kalacak" ufka (16 Ara) kadar NAKİT çıkışı. f'de tahmini kart harcaması 121.156,49
    (V1 37.000×30/31 = 35.806,45 — kesimi geçmiş döngü, harcama sonraki ekstreye; V2 43.316,36; K1 42.033,68) ama bu
    ekstreler 23–25 Ara'da (ufuktan SONRA) ödenir: toplamdan DÜŞÜLMEZ, bilgi satırı variable_card_later. Kart dışı
    değişken gider geçmişi yok → 0. Ay sonunda kalacak = 10.948,87 + maaş 86.500 = 97.448,87 = yeni harcama yapmazsan."""
    _, n = run(client, tmpdb, monkeypatch, "f")
    fb = n["forecast_balance"]
    p = {x["key"]: x["amount"] for x in fb["parts"]}
    assert p["variable"] == 0.0 and p["variable_card_later"] == 121156.49
    assert fb["amount"] == fb["no_new_spend"] == 97448.87 <= round(n["spendable"] + 86500.0, 2)
