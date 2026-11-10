"""Faiz ve ücretler sayfası (GET /api/interest): kart / ay / kalem, neden, önleme önerileri, ortak defterde gizleme.
Veriler uydurmadır (örnek kart numaraları, örnek işyerleri)."""
from datetime import date

from tests.test_book import _member, _setup_book, owner  # noqa: F401  (owner: fixture)
from tests.test_card_estimate import _payment, _statement


def _fee(c, period_end, desc, amount, card="4111***1111"):
    sid = c.execute("SELECT id FROM statements WHERE period_end = ? AND card_no = ?", (period_end, card)).fetchone()[0]
    c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, category) "
              "VALUES (?, ?, ?, ?, 'faiz_ucret', 'Banka Faiz & Ücret')", (sid, period_end, desc, amount))


def _seed(tmpdb):
    with tmpdb.connect() as c:
        _statement(c, "2026-08-15", "2026-08-25", 33000.00)
        _statement(c, "2026-09-15", "2026-09-25", 34000.00)
        _payment(c, "2026-08-27", 33000.00, "1111")  # iki gün geç
        _fee(c, "2026-09-15", "GECİKME FAİZİ (Oran:4.05)", 41.53)
        _fee(c, "2026-09-15", "ALIŞVERİŞ FAİZİ (Oran:3.75)", 538.31)
        _fee(c, "2026-09-15", "BSMV", 87.00)
        _fee(c, "2026-09-15", "Vade Farki - ORNEK MOBILYA", 120.00)
        _fee(c, "2026-09-15", "YILLIK KART AIDATI", 450.00)
        # başka kart: yalnız ücret, geçen yıl (12 ay penceresinin dışında)
        _statement(c, "2025-06-10", "2025-06-20", 900.00, card="5400***2222")
        _fee(c, "2025-06-10", "KART UCRETI", 99.90, card="5400***2222")


def test_interest_report_groups_types_and_reasons(client, tmpdb):
    from app import finance

    _seed(tmpdb)
    r = finance.interest_report(12, today=date(2026, 10, 20))
    assert r["hidden"] is False and r["count"] == 5
    assert r["total"] == 1236.84 and r["tax"] == 87.0 and r["fee"] == 450.0 and r["interest"] == 699.84
    assert len(r["by_month"]) == 12 and r["by_month"][-1]["month"] == r["to"]
    assert {m["month"]: m["total"] for m in r["by_month"] if m["total"]} == {"2026-09": 1236.84}
    assert [(c["card"], c["count"]) for c in r["by_card"]] == [("VakıfBank …1111", 5)]
    types = {i["description"]: i["type"] for i in r["items"]}
    assert types == {"GECİKME FAİZİ (Oran:4.05)": "gecikme", "ALIŞVERİŞ FAİZİ (Oran:3.75)": "akdi", "BSMV": "vergi",
                     "Vade Farki - ORNEK MOBILYA": "vade_farki", "YILLIK KART AIDATI": "ucret"}
    late = next(i for i in r["items"] if i["type"] == "gecikme")
    assert "2 gün geç" in late["reason"] and "25.08" in late["reason"] and late["uid"].startswith("c")
    assert late["statement_id"] and late["due_date"] == "2026-09-25"
    assert "ORNEK MOBILYA" in next(i for i in r["items"] if i["type"] == "vade_farki")["reason"]
    st = r["statements"][0]
    assert st["reason_kind"] == "gec" and st["total"] == 1236.84
    assert [t["key"] for t in r["tips"]] == ["otomatik_odeme", "tamamini_ode", "tek_cekim", "aidat"]
    assert "Son 12 ayda" in r["tips"][1]["detail"]


def test_interest_report_all_history_and_endpoint(client, tmpdb):
    _seed(tmpdb)
    r = client.get("/api/interest", params={"months": 0}).json()
    assert r["count"] == 6 and r["from"] == "2025-06"
    assert [c["last4"] for c in r["by_card"]] == ["1111", "2222"]
    assert client.get("/api/interest", params={"months": -1}).status_code == 422
    assert "interest_page" in client.get("/api/meta").json()["capabilities"]


def test_interest_report_tips_only_from_data(client, tmpdb):
    """Yalnız vade farkı var: "otomatik ödeme" ya da "aidat" önerisi çıkmaz; faiz yoksa öneri de yok."""
    from app import finance

    assert finance.interest_report(12)["tips"] == [] and finance.interest_report(12)["total"] == 0
    with tmpdb.connect() as c:
        _statement(c, "2026-09-15", "2026-09-25", 5000.00)
        _fee(c, "2026-09-15", "Vade Farki - ORNEK ELEKTRONIK", 64.00)
        _fee(c, "2026-09-15", "KKDF - ORNEK ELEKTRONIK", 9.60)
    r = finance.interest_report(12, today=date(2026, 10, 1))
    assert [t["key"] for t in r["tips"]] == ["tek_cekim"]
    # çözüm tek çekim ya da biriktirip peşin almak: bankanın taksit kampanyası önerilmez
    assert "biriktirip peşin" in r["tips"][0]["detail"] and "kampanya" not in r["tips"][0]["detail"]
    assert r["tax"] == 9.6 and r["statements"][0]["reason"] is None


def test_interest_income_section(client, tmpdb):
    from app import finance

    tmpdb.create_entry({"entry_date": "2026-09-30", "description": "Vadesiz hesap faiz", "amount": 12.40, "kind": "gelir",
                        "category": "Diğer"})
    r = finance.interest_report(12, today=date(2026, 10, 1))
    assert r["income"]["total"] == 12.4 and r["income"]["items"][0]["uid"].startswith("m")


def test_interest_hidden_for_member(owner):  # noqa: F811
    _setup_book(owner)
    assert owner.get("/api/interest").json()["count"] > 0  # kuveytturk örneğinde vade farkı ve vergisi var
    m = _member(owner)
    assert m.get("/api/interest").json()["count"] > 0  # kural yok: üye de görür
    owner.put("/api/book/rules", json={"hide_fees": True})
    r = m.get("/api/interest").json()
    assert r["hidden"] is True and r["items"] == [] and r["total"] == 0
    assert owner.get("/api/interest").json()["hidden"] is False


def test_interest_hidden_card_not_leaked_to_member(owner):  # noqa: F811
    _setup_book(owner)
    kuveyt = next(c["card_no"] for c in owner.get("/api/book").json()["cards"] if c["bank"] == "Kuveyt Türk")
    m = _member(owner)
    owner.put("/api/book/rules", json={"hidden_cards": [kuveyt]})
    r = m.get("/api/interest").json()
    assert all(i["card_no"] != kuveyt for i in r["items"]) and r["count"] == 0
    # faiz alarmı (özet uyarısı, faiz geçmişi) da gizli kartın faizini göstermez
    assert not any("Kuveyt" in a["title"] for mo in m.get("/api/interest-history").json()["months"] for a in mo["alarms"])
    assert any("Kuveyt" in a["title"] for mo in owner.get("/api/interest-history").json()["months"] for a in mo["alarms"])


def test_due_reminder_shows_total_not_minimum(client, tmpdb):
    """Telegram son ödeme hatırlatması: dönem borcu öne, asgari tutar yazılmaz (küçük rakam çapa olmasın)."""
    from app import jobs

    with tmpdb.connect() as c:
        _statement(c, "2026-11-01", "2026-11-11", 12000.00)
        c.execute("UPDATE statements SET min_payment = 4800 WHERE period_end = '2026-11-01'")
    [m] = [m for m in jobs.reminders(date(2026, 11, 10)) if "4111***1111" in m]
    assert "Dönem borcu: <b>₺12.000,00</b>" in m and "Tamamını son ödeme gününe kadar ödersen faiz işlemez." in m
    assert "asgari" not in m.lower() and "4.800" not in m
