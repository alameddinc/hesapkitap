from datetime import date

import pytest

from tests.conftest import STATEMENTS, fixture_bytes


def test_bill_lifecycle(client):
    from app import finance, jobs

    up = client.post("/api/bills/parse", files={"file": ("f.pdf", b"%PDF-1.4 sahte", "application/pdf")}).json()
    assert up["file_name"].endswith(".pdf")
    bid = client.post("/api/bills", json={"provider": "Enerjisa", "amount": 842.5, "due_date": "2026-10-28",
                                          "category": "Elektrik", "file_name": up["file_name"]}).json()["id"]
    assert client.get(f"/api/bills/{bid}/file").content == b"%PDF-1.4 sahte"

    today = date(2026, 10, 25)
    p = finance.payables(today)
    bill = next(x for x in p if x["type"] == "bill")
    assert bill["status"] == "bekliyor" and bill["days_left"] == 3
    assert any("Enerjisa" in m for m in jobs.reminders(today))  # 3 gün kala hatırlatma
    plan = finance.plan(2, today=today)["months"][0]
    assert plan["groups"].get("fatura") == 842.5

    client.post(f"/api/bills/{bid}/pay", json={"amount": 842.5, "paid_at": "2026-10-25", "payment": "havale"})
    assert finance.payables(today)[0]["status"] == "odendi"
    assert not any("Enerjisa" in m for m in jobs.reminders(today))  # ödendi → hatırlatma yok
    plan = finance.plan(2, today=today)["months"][0]
    assert "fatura" not in plan["groups"] and plan["groups"]["tek_seferlik"] == 842.5  # ödeme gider kaydına döndü
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["manual_expense"] == 842.5

    client.delete(f"/api/bills/{bid}/pay")  # geri al → gider kaydı da silinir
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["manual_expense"] == 0


def test_statement_pay(client, upload):
    from app import finance, jobs

    upload()
    today = date(2026, 10, 20)
    st = next(x for x in finance.payables(today) if x["type"] == "statement" and "4543***1234" in x["title"])
    assert st["status"] == "bekliyor"
    assert any("4543***1234" in m for m in jobs.reminders(today))

    client.post(f"/api/statements/{st['id']}/pay", json={"amount": st["min_payment"], "paid_at": "2026-10-20"})  # sadece asgari
    st2 = next(x for x in finance.payables(today) if x["id"] == st["id"] and x["type"] == "statement")
    assert st2["status"] == "kismi"
    assert any("kaldı" in i["title"] for i in finance.insights("2026-10", today=today))

    client.post(f"/api/statements/{st['id']}/pay", json={"amount": st["amount"], "paid_at": "2026-10-20"})
    assert not any("4543***1234" in m for m in jobs.reminders(today))
    # gecikmiş: ödenmemiş diğer ekstre 30.10'da gecikti görünür
    late = [x for x in finance.payables(date(2026, 10, 30)) if x["status"] == "gecikti"]
    assert late and all("4543***1234" not in x["title"] for x in late)


def test_category_increase_needs_same_coverage(client):
    from app import finance

    client.post("/api/entries", json={"entry_date": "2026-09-10", "description": "Pazar", "amount": 600,
                                      "kind": "gider", "category": "Market & Gıda"})
    client.post("/api/entries", json={"entry_date": "2026-10-10", "description": "Pazar", "amount": 5000,
                                      "kind": "gider", "category": "Market & Gıda"})
    # ikisinde de ekstre yok → kapsam aynı → kıyas yapılır
    assert any("Market" in i["title"] and "arttı" in i["title"] for i in finance.insights("2026-10", today=date(2026, 10, 19)))


def test_budgets(client):
    from app import finance, jobs

    client.put("/api/budgets", json={"category": "Kafe & Restoran", "monthly_limit": 1000})
    client.post("/api/entries", json={"entry_date": "2026-10-05", "description": "Kahve", "amount": 850,
                                      "kind": "gider", "category": "Kafe & Restoran"})
    b = client.get("/api/budgets", params={"month": "2026-10"}).json()["status"][0]
    assert b["pct"] == 85 and b["remaining"] == 150
    assert any("Kafe" in i["title"] for i in finance.insights("2026-10", today=date(2026, 10, 19)))
    msgs = jobs.reminders(date(2026, 10, 19))
    assert any("Kafe" in m and "%85" in m for m in msgs)
    assert not any("Kafe" in m for m in jobs.reminders(date(2026, 10, 20)))  # aynı eşik tekrar gönderilmez
    client.post("/api/entries", json={"entry_date": "2026-10-06", "description": "Yemek", "amount": 300,
                                      "kind": "gider", "category": "Kafe & Restoran"})
    assert any("🚨" in m and "Kafe" in m for m in jobs.reminders(date(2026, 10, 21)))  # %100 eşiği ayrı
    client.put("/api/budgets", json={"category": "Kafe & Restoran", "monthly_limit": None})
    assert client.get("/api/budgets").json()["limits"] == {}


def test_subscription_cancel_and_goals(client, upload):
    from app import finance

    upload()
    today = date(2026, 10, 19)

    subs = client.get("/api/savings").json()["subscriptions"]
    anth = next(s for s in subs if s["merchant"] == "ANTHROPIC")
    before = finance.plan(3, today=today)["months"][1]["groups"]["abonelik"]

    # ekstreden ÖNCE iptal edilmiş gibi: iptal sonrası çekim → kritik uyarı
    client.put("/api/subscriptions", json={"merchant": "ANTHROPIC", "status": "iptal", "changed_at": "2026-10-01"})
    after = finance.plan(3, today=today)["months"][1]["groups"]["abonelik"]
    assert round(before - after, 2) == anth["monthly"]  # plandan düştü
    assert any("Anthropic iptal edilmişti" in i["title"] for i in finance.insights("2026-10", today=today))

    # bugün iptal → yeni çekim yok → uyarı yok
    client.put("/api/subscriptions", json={"merchant": "ANTHROPIC", "status": "iptal", "changed_at": "2026-10-19"})
    assert not any("iptal edilmişti" in i["title"] for i in finance.insights("2026-10", today=today))
    sv = client.get("/api/savings").json()
    assert sv["cancelled_monthly"] == anth["monthly"]

    # kısma hedefi: plan değişken tahmini düşer, ay sonucu değerlendirilir
    var_before = finance.plan(3, today=today)["months"][1]["groups"]["degisken"]
    client.post("/api/goals", json={"kind": "category", "name": "Yemek Siparişi", "baseline": 2645, "target": 1500,
                                    "start_month": "2026-11"})
    var_after = finance.plan(3, today=today)["months"][1]["groups"]["degisken"]
    assert abs((var_before - var_after) - 1145) <= 0.02  # hesaplara orantılı dağıtımda kuruş yuvarlaması
    g = finance.goals_review(date(2026, 12, 5))[0]
    assert [m["status"] for m in g["months"]] == ["tuttu", "devam"]  # kasım: harcama yok → tuttu


def test_auto_mark_paid_from_next_statement(tmpdb):
    # vakifbank_a (kesim 13.10) içinde 18.09'da "ÖDEMENİZ İÇİN TEŞEKKÜRLER +20,000.00" var → önceki ekstre ödendi
    from app.parsers import parse_statement

    with tmpdb.connect() as c:  # önceki dönemin ekstresi (sadece başlık) yüklenmiş gibi
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                  "VALUES ('x', 'VakıfBank', '4543***1234', '2026-09-13', '2026-09-23', 20000)")
    for n in STATEMENTS:
        tmpdb.save_statement(fixture_bytes(n), n, parse_statement(fixture_bytes(n)))
    assert tmpdb.auto_mark_paid() == 1
    prev = next(s for s in tmpdb.list_statements() if s["period_end"] == "2026-09-13")
    assert prev["paid_amount"] == 20000 and prev["paid_at"] == "2026-09-18"


def test_subscription_downgrade(client, upload):
    from app import finance

    upload()
    today = date(2026, 10, 19)
    before = finance.plan(3, today=today)["months"][1]["groups"]["abonelik"]
    openai = next(s for s in client.get("/api/savings").json()["subscriptions"] if s["merchant"] == "OPENAI")

    assert client.put("/api/subscriptions", json={"merchant": "OPENAI", "status": "dusuruldu"}).status_code == 422
    client.put("/api/subscriptions", json={"merchant": "OPENAI", "status": "dusuruldu", "new_amount": 100,
                                           "changed_at": "2026-10-19"})
    after = finance.plan(3, today=today)["months"][1]["groups"]["abonelik"]
    assert round(before - after, 2) == round(openai["monthly"] - 100, 2)
    sv = client.get("/api/savings").json()
    o = next(s for s in sv["subscriptions"] if s["merchant"] == "OPENAI")
    assert o["status"] == "dusuruldu" and o["saving"] == round(openai["monthly"] - 100, 2)
    assert sv["cancelled_monthly"] == o["saving"]

    # düşürme eski tarihli olsaydı: eski tutar çekilmeye devam etmiş → uyarı
    client.put("/api/subscriptions", json={"merchant": "OPENAI", "status": "dusuruldu", "new_amount": 100,
                                           "changed_at": "2026-09-01"})
    assert any("paketi düşmemiş" in i["title"] for i in finance.insights("2026-10", today=today))


def test_subscription_state_migration(tmp_path, monkeypatch):
    import sqlite3

    from app import db

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE subscription_state (merchant TEXT PRIMARY KEY, status TEXT NOT NULL "
                "CHECK (status IN ('aktif', 'iptal')), changed_at DATE NOT NULL, note TEXT)")
    con.execute("INSERT INTO subscription_state VALUES ('SCRIBD', 'iptal', '2026-10-19', NULL)")
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    assert db.subscription_states()["SCRIBD"]["status"] == "iptal"  # veri korundu
    db.set_subscription_state("OPENAI", "dusuruldu", "2026-10-19", new_amount=700)  # yeni durum kabul ediliyor
    assert db.subscription_states()["OPENAI"]["new_amount"] == 700


def test_estimate_not_counted_as_committed(client, upload):
    from app import finance

    upload()
    # gelir kesin giderleri karşılıyor ama tahminle birlikte karşılamıyor
    today = date(2026, 11, 2)  # Ekim'in gerçek ekstresi planda olmasın: hepsi tahmini aylar
    months = finance.plan(6, today=today)["months"]
    # maaş tüm kesin giderleri karşılar; tahmini kart harcamasıyla birlikte karşılamaz
    salary = round(max(m["expense"] for m in months) + 1)
    assert salary < max(m["expense"] + m["estimated"] for m in months)
    client.post("/api/recurring", json={"description": "Maaş", "amount": salary, "kind": "gelir", "start_month": "2026-01"})
    months = finance.plan(6, today=today)["months"]
    assert all(m["net"] == round(m["income"] - m["expense"], 2) >= 0 for m in months)
    assert all(m["net_with_estimate"] == round(m["net"] - m["estimated"], 2) for m in months)
    assert any(m["net_with_estimate"] < 0 for m in months)
    ins = finance.insights("2026-11", today=today)
    assert not any("kesin giderler geliri aşıyor" in i["title"] for i in ins)  # tahmin "açık" sayılmaz
    assert any("sıkışabilirsin" in i["title"] and i["level"] == "bilgi" for i in ins)
