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
