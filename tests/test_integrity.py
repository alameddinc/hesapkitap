"""S2: gece doğruluk denetimi (1. katman). Veriler uydurmadır."""
import json
import time
from datetime import date, datetime

from app import db, integrity, jobs


def _entry(client, d, desc, amount, kind="gider", **kw):
    r = client.post("/api/entries", json={"entry_date": d, "description": desc, "amount": amount, "kind": kind, **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_clean_book_has_no_violations(client, upload):
    upload()
    _entry(client, "2026-10-05", "Maaş", 10000, "gelir")
    _entry(client, "2026-10-06", "Market", 250.4)
    t0 = time.perf_counter()
    res = integrity.check(date(2026, 10, 31))
    assert (time.perf_counter() - t0) < 0.1  # kullanıcı başı ≤ 100 ms
    assert res["counts"].get("bolme_asim") is None and res["counts"].get("kurus_alti") is None
    assert "_hata" not in res["counts"]
    assert integrity.todo() is None  # henüz çalışmadı


def test_injected_violations_detected(client, upload):
    upload()
    eid = _entry(client, "2026-10-06", "Market", 100)
    with db.connect() as c:
        c.execute("INSERT INTO splits (entry_id, amount, category) VALUES (?, 150, 'Sigara')", (eid,))  # I6
        c.execute("INSERT INTO debts (person, direction, amount, paid_amount, debt_date) VALUES ('Ali', 'borc', 100, 100, "
                  "'2026-09-01')")
        did = c.execute("SELECT MAX(id) FROM debts").fetchone()[0]
        c.execute("INSERT INTO debt_payments (debt_id, amount, paid_at) VALUES (?, 130, '2026-09-10')", (did,))  # I7
        c.execute("INSERT INTO entries (entry_date, month, description, amount, kind) "
                  "VALUES ('2026-10-07', '2026-10', 'Kuruş', 10.123, 'gider')")  # F1.2
        for h in ("dup-1", "dup-1"):  # aynı parmak izi iki kez
            c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, import_hash) "
                      "VALUES ('2026-10-08', '2026-10', 'Havale', 50, 'gider', ?)", (h,))
        aid = c.execute("INSERT INTO accounts (name, kind, balance, as_of, source) VALUES ('Banka X', 'banka', 0, NULL, "
                        "'dokum')").lastrowid
        for doc, h in ((1, "pdf-a"), (2, "xls-a")):  # PDF + Excel aynı dönemi iki kez (I11)
            c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, import_hash, account_id, "
                      "bank_doc_id) VALUES ('2026-10-09', '2026-10', 'Kira', 3000, 'gider', ?, ?, ?)", (h, aid, doc))
    res = integrity.check(date(2026, 10, 31))
    c = res["counts"]
    assert c["bolme_asim"] == 1 and c["borc_fazla_odeme"] == 1 and c["kurus_alti"] == 1
    assert c["cift_aktarim"] == 2  # aynı hash + iki belgeden aynı hareket


def test_card_debt_double_count_detected(client, monkeypatch):
    """M1 öncesi davranış (aynı kartın ödenmemiş her ekstresini toplamak) I12 ihlali verir; bugünkü kod vermez."""
    with db.connect() as c:
        for h, pe, due, debt in (("h1", "2026-08-20", "2026-08-30", 10000), ("h2", "2026-09-20", "2026-09-30", 15000)):
            c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                      "VALUES (?, 'Banka Y', '**** 4321', ?, ?, ?)", (h, pe, due, debt))
    assert "kart_borcu" not in integrity.check(date(2026, 10, 31))["counts"]
    real = db.card_debt_at
    old = lambda d, statements=None: sum(db.statement_remaining(s, d) for s in (statements or db.list_statements())  # noqa: E731
                                         if s["period_end"] <= d and s.get("source") != "excel")
    monkeypatch.setattr(db, "card_debt_at", old)
    assert old("2026-10-31") == 25000 and real("2026-10-31") == 15000
    assert integrity.check(date(2026, 10, 31))["counts"].get("kart_borcu") == 1


def test_todo_and_admin_numbers_only(client, monkeypatch):
    eid = _entry(client, "2026-10-06", "Gizli Market Örnek", 100)
    with db.connect() as c:
        c.execute("INSERT INTO splits (entry_id, amount, category) VALUES (?, 150, 'Sigara')", (eid,))
    out = integrity.run(date(2026, 10, 31))
    assert out == {"total": 1, "new": 1, "ms": out["ms"]}  # dönüş (günlüğe giden) yalnız sayı
    todo = [r for r in client.get("/api/reminders").json() if r["kind"] == "dogruluk"]
    assert len(todo) == 1 and todo[0]["title"] == "Rakamları kontrol et"
    assert "biz de gördük" in todo[0]["text"] and todo[0]["details"][0]["code"] == "bolme_asim"
    client.post(f"/api/reminders/{todo[0]['id']}/dismiss")
    assert not [r for r in client.get("/api/reminders").json() if r["kind"] == "dogruluk"]
    assert integrity.run(date(2026, 11, 1))["new"] == 0  # aynı ihlal: yeni değil

    s = integrity.admin_summary("2026-10-31")
    assert s["spaces"] == 1 and s["with_issues"] == 1 and s["by_code"] == {"bolme_asim": 1}
    text = integrity.admin_text(s)
    assert "Gizli" not in text and "150" not in text and "Sigara" not in text
    from app import system

    with system.connect() as c:
        raw = json.dumps([dict(r) for r in c.execute("SELECT * FROM integrity_runs")])
    assert "Gizli" not in raw and "Sigara" not in raw

    sent = []
    monkeypatch.setattr("app.telegram.enabled", lambda: True)
    monkeypatch.setattr("app.telegram.send", lambda t: sent.append(t))
    assert integrity.report_admin(datetime(2026, 10, 31, 3)) is None  # saatinden önce değil
    assert integrity.report_admin(datetime(2026, 10, 31, 6))["with_issues"] == 1
    assert sent and "Gizli" not in sent[0]
    assert integrity.report_admin(datetime(2026, 10, 31, 7)) is None  # günde bir kez

    # yeni ihlal: madde yeniden çıkar
    with db.connect() as c:
        c.execute("INSERT INTO entries (entry_date, month, description, amount, kind) "
                  "VALUES ('2026-10-07', '2026-10', 'K', 1.001, 'gider')")
    assert integrity.run(date(2026, 11, 2))["new"] == 1
    assert [r for r in client.get("/api/reminders").json() if r["kind"] == "dogruluk"]


def test_job_registered_for_every_space():
    names = {n for n, _, _ in jobs.JOBS}
    assert "integrity" in names and "integrity" not in jobs.MAIN_ONLY


def test_cli(client, capsys):
    assert integrity.main([]) == 0
    assert '"counts"' in capsys.readouterr().out


# ---------------------------------------------------------------- yanlış pozitif testleri (canlı bulguları, uydurma veri)

def _stmt(c, h, prev, pay, act, fees, total, txns=(), deleted=()):
    sid = c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, previous_balance, payments, "
                    "period_activity, interest_fees, total_debt) VALUES (?, 'Banka Z', ?, '2026-10-16', '2026-10-26', "
                    "?, ?, ?, ?, ?)", (h, f"**** {h[-4:]}", prev, pay, act, fees, total)).lastrowid
    for a in txns:
        c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type) "
                  "VALUES (?, '2026-10-01', 'Alışveriş', ?, 'harcama')", (sid, a))
    for a in deleted:
        c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type, is_deleted) "
                  "VALUES (?, '2026-10-02', 'Silinen', ?, 'harcama', 1)", (sid, a))
    return sid


def test_statement_equation_holds_not_flagged(tmpdb):
    with db.connect() as c:
        # faiz ayrı satır: 1000 − 1000 + 2000 + 50 = 2050 (tutuyor); okunan satırlar 2000 + faiz 50
        a = _stmt(c, "h0001", 1000, 1000, 2000, 50, 2050, txns=(2000,))
        c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type) "
                  "VALUES (?, '2026-10-03', 'Faiz', 50, 'faiz_ucret')", (a,))
        # faiz işlemlerin içinde, özette faiz yok: 3000 − 3000 + 4000 = 4000
        _stmt(c, "h0002", 3000, 3000, 4000, None, 4000, txns=(3900, 100))
        # kullanıcı bir satırı sildi: okunanlar 900 + silinen 100 = özet 1000 → bilinçli silme, ihlal değil
        _stmt(c, "h0003", 0, 0, 1000, None, 1000, txns=(900,), deleted=(100,))
        bad = _stmt(c, "h0004", 0, 0, 1000, None, 1500, txns=(1000,))  # denklem tutmuyor (500 fark)
        miss = _stmt(c, "h0005", 0, 0, 1000, None, 1000, txns=(800,))  # satır kayıp, silme yok
    r = integrity.check(date(2026, 10, 31))["refs"]
    assert r.get("ekstre_denklem") == [f"statement:{bad}"]
    assert r.get("ekstre_satir_farki") == [f"statement:{miss}"]  # 'bad'in satırları özetle aynı


def _acc(c, name):
    return c.execute("INSERT INTO accounts (name, kind, balance, as_of, source) VALUES (?, 'banka', 0, '2026-10-31', "
                     "'dokum')", (name,)).lastrowid


def _cover(c, aid, h):
    for d in ("2026-10-01", "2026-10-30"):
        c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, import_hash, account_id) "
                  "VALUES (?, '2026-10', 'x', 1, 'gider', ?, ?)", (d, f"{h}{d}", aid))


def test_own_transfer_to_untracked_bank_is_normal(tmpdb):
    with db.connect() as c:
        a, b = _acc(c, "Halkbank"), _acc(c, "VakıfBank")
        _cover(c, a, "a"), _cover(c, b, "b")
        for amt, desc in ((-500, "Kendi hesabına · Halkbank → Akbank"),  # Akbank takip edilmiyor: normal
                          (700, "Kendi hesabından · Vakıf Katılım → Halkbank"),  # takip edilmiyor: normal
                          (-300, "Kendi hesabına · Halkbank"),  # karşı banka bilinmiyor: sayılmaz
                          (-900, "Kendi hesabına · Halkbank → VakıfBank")):  # VakıfBank takipte, karşılık yok: ihlal
            c.execute("INSERT INTO own_transfers (transfer_date, amount, account, description, account_id) "
                      "VALUES ('2026-10-10', ?, 'Halkbank', ?, ?)", (amt, desc, a))
        bad = c.execute("SELECT id FROM own_transfers WHERE amount = -900").fetchone()[0]
        c.execute("INSERT INTO own_transfers (transfer_date, amount, account, description, account_id) "
                  "VALUES ('2026-10-12', -250, 'Halkbank', 'Kendi hesabına · Halkbank → VakıfBank', ?)", (a,))
        c.execute("INSERT INTO own_transfers (transfer_date, amount, account, description, account_id) "
                  "VALUES ('2026-10-12', 250, 'VakıfBank', 'Kendi hesabından · Halkbank → VakıfBank', ?)", (b,))
    assert integrity.check(date(2026, 10, 31))["refs"].get("virman_tek_bacak") == [f"own_transfer:{bad}"]


def test_debt_checks_separate(tmpdb):
    with db.connect() as c:
        over = c.execute("INSERT INTO debts (person, direction, amount, paid_amount, debt_date, settled_at) "
                         "VALUES ('Ali Örnek', 'borc', 100, 100, '2026-09-01', '2026-09-20')").lastrowid
        for a in (100, 30):  # A4 öncesi fazla ödeme
            c.execute("INSERT INTO debt_payments (debt_id, amount, paid_at) VALUES (?, ?, '2026-09-20')", (over, a))
        ok = c.execute("INSERT INTO debts (person, direction, amount, paid_amount, debt_date, settled_at) "
                       "VALUES ('Veli Örnek', 'alacak', 100, 100, '2026-09-01', '2026-09-20')").lastrowid
        c.execute("INSERT INTO debt_payments (debt_id, amount, paid_at) VALUES (?, 100, '2026-09-20')", (ok,))
        odd = c.execute("INSERT INTO debts (person, direction, amount, paid_amount, debt_date) "
                        "VALUES ('Can Örnek', 'borc', 100, 40, '2026-09-01')").lastrowid
        c.execute("INSERT INTO debt_payments (debt_id, amount, paid_at) VALUES (?, 60, '2026-09-20')", (odd,))
    r = integrity.check(date(2026, 10, 31))["refs"]
    assert r.get("borc_fazla_odeme") == [f"debt:{over}"]
    assert r.get("borc_odenen_tutarsiz") == [f"debt:{odd}"]
