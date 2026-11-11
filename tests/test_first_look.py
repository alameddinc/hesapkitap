"""Öncelik 3: Özet "Bu dönem ödenen faiz ve ücret" (summary.interest_paid), İlk gün özeti (GET /api/first-look) ve
"Ekstre nasıl indirilir?" tek kaynak (GET /api/statement-help). Veriler uydurmadır (örnek kart no, örnek işyeri)."""
from datetime import date, timedelta

from tests.test_book import _member, _setup_book, owner  # noqa: F401  (owner: fixture)
from tests.test_card_estimate import _payment, _statement
from tests.test_interest import _fee


def _inst(c, period_end, desc, amount, left, left_amount, card="4111***1111", no=1):
    sid = c.execute("SELECT id FROM statements WHERE period_end = ? AND card_no = ?", (period_end, card)).fetchone()[0]
    c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category, installment_no, "
              "installments_left, installment_left_amount) VALUES (?, ?, ?, ?, 'harcama', 'Ev', ?, ?, ?)",
              (sid, period_end, desc, amount, no, left, left_amount))


# ---------------------------------------------------------------- 2.5 Özet: bu dönem ödenen faiz ve ücret

def test_summary_interest_paid_matches_fees_and_interest_page(client, tmpdb):
    from app import finance

    with tmpdb.connect() as c:
        _statement(c, "2026-09-15", "2026-09-25", 34000.00)
        _fee(c, "2026-09-15", "ALIŞVERİŞ FAİZİ (Oran:3.75)", 538.31)
        _fee(c, "2026-09-15", "BSMV", 87.00)
        _fee(c, "2026-09-15", "YILLIK KART AIDATI", 450.00)
        _fee(c, "2026-09-15", "Vade Farki - ORNEK MOBILYA", 120.00)
    s = client.get("/api/summary", params={"month": "2026-09"}).json()
    p = s["interest_paid"]
    assert p["total"] == s["totals"]["fees"] == 1195.31  # Özet'teki Faiz kutusuyla aynı sayım
    assert (p["interest"], p["tax"], p["fee"], p["count"]) == (658.31, 87.0, 450.0, 4)
    assert p["state"] == "var" and p["statements"] == 1 and p["has_cards"] is True
    # Faiz sayfasının o ayı (GET /api/interest by_month) ile aynı
    rep = finance.interest_report(12, today=date(2026, 10, 1))
    assert next(m["total"] for m in rep["by_month"] if m["month"] == "2026-09") == p["total"]
    # silinen faiz satırı ikisinden de düşer
    fid = next(r["id"] for r in client.get("/api/transactions", params={"month": "2026-09"}).json() if r["description"] == "BSMV")
    client.patch(f"/api/card-transactions/{fid}", json={"is_deleted": True})
    s = client.get("/api/summary", params={"month": "2026-09"}).json()
    assert s["interest_paid"]["total"] == s["totals"]["fees"] == 1108.31 and s["interest_paid"]["tax"] == 0
    assert "summary_interest_paid" in client.get("/api/meta").json()["capabilities"]


def test_summary_interest_paid_states(client, tmpdb):
    # hiç kart yok: kart gösterilmez (has_cards false)
    p = client.get("/api/summary", params={"month": "2026-09"}).json()["interest_paid"]
    assert p == {"total": 0.0, "interest": 0.0, "tax": 0.0, "fee": 0.0, "count": 0, "statements": 0, "has_cards": False,
                 "state": "ekstre_yok", "last_period": None}
    with tmpdb.connect() as c:
        _statement(c, "2026-09-15", "2026-09-25", 5000.00)
    # bu dönemin ekstresi var, faiz yok: "Bu dönem faiz ödemedin"
    assert client.get("/api/summary", params={"month": "2026-09"}).json()["interest_paid"]["state"] == "yok"
    # kart var ama bu dönemin ekstresi henüz yok: "faiz ödemedin" demek için erken
    p = client.get("/api/summary", params={"month": "2026-10"}).json()["interest_paid"]
    assert p["state"] == "ekstre_yok" and p["has_cards"] is True and p["total"] == 0
    # son ekstreli dönem: faizsiz → "Eylül döneminde faiz ödemedin"
    assert p["last_period"]["month"] == "2026-09" and p["last_period"]["state"] == "yok" and p["last_period"]["total"] == 0
    assert client.get("/api/summary", params={"month": "2026-09"}).json()["interest_paid"]["last_period"] is None


def test_summary_interest_paid_last_period_with_cycle(client, tmpdb):
    """Ay dönümü 16 (kesim 15): 15 Eylül'de kesilen ekstre 2026-09 anahtarına (16 Ağu – 15 Eyl = "Ağustos dönemi") yazılır;
    20 Eylül'de içinde bulunulan dönem 2026-10 ("Eylül dönemi", ekstresi yok) → kart son ekstreli dönemi gösterir."""
    from app import finance

    with tmpdb.connect() as c:
        _statement(c, "2026-09-15", "2026-09-25", 34000.00)
        _fee(c, "2026-09-15", "Vade Farki - ORNEK MOBILYA", 120.00)
        _fee(c, "2026-09-15", "BSMV", 18.00)
        _fee(c, "2026-09-15", "KKDF", 18.00)
    tmpdb.set_cycle_start_day(16)
    assert tmpdb.period_of("2026-09-15") == "2026-09" and finance.current_period(date(2026, 9, 20)) == "2026-10"
    p = client.get("/api/summary", params={"month": "2026-10"}).json()["interest_paid"]
    assert p["state"] == "ekstre_yok" and p["total"] == 0
    lp = p["last_period"]
    assert (lp["month"], lp["name"], lp["state"], lp["total"], lp["interest"], lp["tax"]) == ("2026-09", "Ağustos", "var", 156.0, 120.0, 36.0)
    # son dönemin tutarı o dönemin Özet'iyle aynı
    assert client.get("/api/summary", params={"month": "2026-09"}).json()["totals"]["fees"] == lp["total"]


def test_summary_interest_paid_excludes_interest_income(client, tmpdb):
    """Faiz geliri (vadesizde gecelik faiz) ayrı satır: ödenen faize karışmaz."""
    with tmpdb.connect() as c:
        _statement(c, "2026-09-15", "2026-09-25", 5000.00)
    tmpdb.create_entry({"entry_date": "2026-09-10", "description": "Vadesiz hesap faiz", "amount": 12.40, "kind": "gelir",
                        "category": "Diğer"})
    s = client.get("/api/summary", params={"month": "2026-09"}).json()
    assert s["totals"]["interest_income"] == 12.4 and s["interest_paid"]["total"] == 0 and s["interest_paid"]["state"] == "yok"


def test_summary_interest_paid_hidden_for_member(owner):  # noqa: F811
    _setup_book(owner)
    m = _member(owner)
    assert m.get("/api/summary").json()["interest_paid"] is not None
    owner.put("/api/book/rules", json={"hide_fees": True})
    assert m.get("/api/summary").json()["interest_paid"] is None
    assert owner.get("/api/summary").json()["interest_paid"] is not None


# ---------------------------------------------------------------- 3.1 İlk gün özeti

def _seed_first(tmpdb, today):
    due = (today + timedelta(days=8)).isoformat()
    with tmpdb.connect() as c:
        _statement(c, (today - timedelta(days=2)).isoformat(), due, 12450.00)
        c.execute("UPDATE statements SET min_payment = 4980")  # asgari tutar yanıta hiç girmemeli
        pe = (today - timedelta(days=2)).isoformat()
        _fee(c, pe, "ALIŞVERİŞ FAİZİ (Oran:3.75)", 210.00)
        _fee(c, pe, "BSMV", 31.50)
        _inst(c, pe, "ORNEK ELEKTRONIK TAKSIT 2/6", 1500.00, left=4, left_amount=1500.00, no=2)
        _inst(c, pe, "ORNEK MOBILYA TAKSIT 1/3", 800.00, left=2, left_amount=800.00, no=1)
        # ikinci kart: son ödeme günü geçmiş, ödenmemiş görünüyor
        _statement(c, (today - timedelta(days=40)).isoformat(), (today - timedelta(days=30)).isoformat(), 900.00,
                   card="5400***2222")
    return due


def test_first_look_summary(client, tmpdb):
    today = date.today()
    due = _seed_first(tmpdb, today)
    r = client.get("/api/first-look").json()
    assert r["first"] is True and r["statement_count"] == 2
    assert [s["last4"] for s in r["statements"]] == ["2222", "1111"]
    assert r["to_pay"]["total"] == 12450.0 and r["to_pay"]["items"][0]["due_date"] == due
    assert r["to_pay"]["items"][0]["days_left"] == 8 and r["to_pay"]["items"][0]["card"] == "VakıfBank …1111"
    assert r["past_due"] == 1
    assert r["to_pay"]["note"] == "Ekstrenin tamamını son ödeme gününe kadar ödersen faiz işlemez."
    assert (r["fees"]["total"], r["fees"]["interest"], r["fees"]["tax"]) == (241.5, 210.0, 31.5)
    inst = r["installments"]
    assert inst["total"] == 7600.0 and inst["count"] == 2 and inst["months"] == 4
    assert inst["next"]["amount"] == 2300.0
    assert [s["key"] for s in r["steps"]] == ["dokum", "butce"]
    # faize yönlendiren metin yok: asgari ödeme ne alan ne metin olarak yanıtta
    body = client.get("/api/first-look").text
    assert "min_payment" not in body and "asgari" not in body.lower() and "4980" not in body
    assert "first_look" in client.get("/api/meta").json()["capabilities"]


def test_first_look_ids_and_steps_done(client, tmpdb):
    today = date.today()
    _seed_first(tmpdb, today)
    sid = tmpdb.list_statements()[0]["id"]
    one = [s["id"] for s in tmpdb.list_statements() if s["card_no"] == "4111***1111"][0]
    r = client.get("/api/first-look", params={"ids": str(one)}).json()
    assert [s["id"] for s in r["statements"]] == [one] and r["past_due"] == 0
    assert client.get("/api/first-look", params={"ids": "1,x"}).status_code == 422
    assert client.get("/api/first-look", params={"ids": "999999"}).json()["statements"] == []
    # ödendi: bu ay ödenecekler boş, not da yok
    client.post(f"/api/statements/{one}/pay", json={"amount": 12450, "paid_at": date.today().isoformat()})
    r = client.get("/api/first-look", params={"ids": str(one)}).json()
    assert r["to_pay"]["items"] == [] and r["to_pay"]["note"] is None
    # bütçe konunca "Bütçe koy" önerisi kalkar
    client.put("/api/budgets", json={"category": "Market", "monthly_limit": 5000})
    assert "butce" not in [s["key"] for s in client.get("/api/first-look").json()["steps"]]
    assert sid


def test_first_look_only_first_week(client, tmpdb):
    _seed_first(tmpdb, date.today())
    with tmpdb.connect() as c:
        c.execute("UPDATE statements SET uploaded_at = ? WHERE card_no = '5400***2222'",
                  ((date.today() - timedelta(days=9)).isoformat() + " 10:00:00",))
    assert client.get("/api/first-look").json()["first"] is False


def test_first_look_hides_fees_for_member(owner):  # noqa: F811
    _setup_book(owner)
    m = _member(owner)
    owner.put("/api/book/rules", json={"hide_fees": True})
    r = m.get("/api/first-look").json()
    assert r["fees"] is None and r["steps"] == []  # kurulum önerileri sahibin işi
    assert owner.get("/api/first-look").json()["fees"] is not None


def test_upload_result_has_statement_id(client, upload):
    res = upload()
    saved = [r for r in res if r["status"] == "saved"]
    assert saved and all(isinstance(r["statement_id"], int) for r in saved)
    ids = ",".join(str(r["statement_id"]) for r in saved)
    r = client.get("/api/first-look", params={"ids": ids}).json()
    assert r["first"] is True and r["statements"]


# ---------------------------------------------------------------- 3.2 Ekstre nasıl indirilir

def test_statement_help_single_source(client):
    web = client.get("/api/statement-help", params={"platform": "web"}).json()
    mob = client.get("/api/statement-help", params={"platform": "mobile"}).json()
    assert web["title"] == mob["title"] == "Ekstre ve hesap dökümü nasıl indirilir?"
    assert [s["key"] for s in web["sections"]] == [s["key"] for s in mob["sections"]] == ["kart", "hesap"]
    assert len(web["sections"][0]["steps"]) == len(mob["sections"][0]["steps"]) == 4
    # mobilde "buraya bırak" (sürükle-bırak) yok; ortak adımlar ikisinde de aynı
    assert not any("bırak" in x["text"] for s in mob["sections"] for x in s["steps"]) and "bırak" in str(web)
    assert web["sections"][1] == mob["sections"][1]
    assert any("+ → Ekstre yükle" in t["text"] for t in mob["tips"]) and not any("+ →" in t["text"] for t in web["tips"])
    allp = client.get("/api/statement-help").json()
    assert {x.get("platform") for x in allp["tips"]} == {"web", "mobile", None}
    assert client.get("/api/statement-help", params={"platform": "tv"}).status_code == 422
    assert "statement_help" in client.get("/api/meta").json()["capabilities"]
