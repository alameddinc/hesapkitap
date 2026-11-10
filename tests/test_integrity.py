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


# ---------------------------------------------------------------- anlaşılır madde, yeniden kontrol, yazma sonrası

def test_todo_details_have_items_and_hints(client):
    eid = _entry(client, "2026-10-06", "Örnek Market", 100)
    with db.connect() as c:
        c.execute("INSERT INTO splits (entry_id, amount, category) VALUES (?, 150, 'Sigara')", (eid,))
        did = c.execute("INSERT INTO debts (person, direction, amount, paid_amount, debt_date) VALUES ('Ali Örnek', "
                        "'alacak', 100, 100, '2026-09-01')").lastrowid
        c.execute("INSERT INTO debt_payments (debt_id, amount, paid_at) VALUES (?, 1000, '2026-10-01')", (did,))
    integrity.run(date(2026, 10, 31))
    t = next(r for r in client.get("/api/reminders").json() if r["kind"] == "dogruluk")
    by = {d["code"]: d for d in t["details"]}
    it = by["bolme_asim"]["items"][0]
    assert it == {"ref": f"entry:{eid}", "date": "2026-10-06", "amount": -100.0, "title": "Örnek Market",
                  "route": f"islemler/m{eid}"}
    assert by["bolme_asim"]["hint"] and by["borc_fazla_odeme"]["items"][0]["route"] == f"borclar/{did}"
    assert "Ali Örnek" in by["borc_fazla_odeme"]["items"][0]["title"] and t["checked_at"]
    g = client.get("/api/integrity").json()
    assert g["total"] == 2 and g["last"]["counts"] == {"bolme_asim": 1, "borc_fazla_odeme": 1}
    assert {d["code"] for d in g["details"]} == {"bolme_asim", "borc_fazla_odeme"}
    # yönetici kanalında yine yalnız sayılar
    assert "Örnek" not in integrity.admin_text(integrity.admin_summary("2026-10-31"))


def test_rerun_endpoint_clears_fixed_and_is_limited(client):
    eid = _entry(client, "2026-10-06", "Örnek Market", 100)
    with db.connect() as c:
        sid = c.execute("INSERT INTO splits (entry_id, amount, category) VALUES (?, 150, 'Sigara')", (eid,)).lastrowid
    assert client.post("/api/integrity/run").json()["total"] == 1
    with db.connect() as c:
        c.execute("UPDATE splits SET amount = 50 WHERE id = ?", (sid,))  # kullanıcı düzeltti
    r = client.post("/api/integrity/run").json()
    assert r["total"] == 0 and r["details"] == [] and r["rerun"] == integrity.RERUN_PER_HOUR - 2
    assert not [x for x in client.get("/api/reminders").json() if x["kind"] == "dogruluk"]
    for _ in range(integrity.RERUN_PER_HOUR - 2):
        assert client.post("/api/integrity/run").status_code == 200
    assert client.post("/api/integrity/run").status_code == 429


def test_after_write_reruns_in_background(client, monkeypatch):
    import threading

    monkeypatch.setenv("JOBS_ENABLED", "1")
    monkeypatch.setattr(integrity, "AFTER_WRITE_DELAY", 0.01)
    done = threading.Event()
    real = integrity.run
    monkeypatch.setattr(integrity, "run", lambda *a, **k: (real(*a, **k), done.set())[0])
    eid = _entry(client, "2026-10-06", "Örnek Market", 100)
    with db.connect() as c:
        c.execute("INSERT INTO splits (entry_id, amount, category) VALUES (?, 150, 'Sigara')", (eid,))
    real(date(2026, 10, 31))
    assert integrity.last()["counts"] == {"bolme_asim": 1}
    assert client.delete(f"/api/entries/{eid}").status_code == 200  # kaydı silmek sorunu giderir
    assert done.wait(5)
    # kayıt eklemenin tetiklediği önceki arka plan koşusu da done'ı kurmuş olabilir: silmeden sonraki koşuyu bekle
    import time

    end = time.monotonic() + 5
    while integrity.last()["counts"] != {} and time.monotonic() < end:
        time.sleep(0.02)
    assert integrity.last()["counts"] == {}


def test_ran_at_utc_naive_and_night_wording(client, monkeypatch):
    import re

    eid = _entry(client, "2026-10-06", "Örnek Market", 100)
    with db.connect() as c:
        c.execute("INSERT INTO splits (entry_id, amount, category) VALUES (?, 150, 'Sigara')", (eid,))
    integrity.run(date(2026, 10, 31))
    r = integrity.last()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", r["ran_at"])
    assert integrity.todo()["checked_at"] == r["ran_at"]
    monkeypatch.setenv("TZ_NAME", "Europe/Istanbul")
    for ran, night in (("2026-10-31 01:00:00", True),    # 04:00 İstanbul (UTC+3): gece koşusu
                       ("2026-10-31 11:00:00", False),   # 14:00 İstanbul
                       ("2026-10-31T04:10:00", True)):   # eski biçim (yerel)
        db.set_setting(integrity.SETTING, __import__("json").dumps({**r, "ran_at": ran}))
        assert integrity.todo()["text"].startswith("Gece yaptığımız" if night else "Son kontrolde")


def test_member_view_does_not_fake_equation_violation(tmpdb):
    """Ortak defter üyesine faiz gizliyse (hide_fees) üyenin alanında çalışan denetim faizi 0 görüp ayrı faizli ekstreyi
    (önceki − ödeme + harcama + faiz = borç) ihlal sayıyordu; gizli kartın ekstresi de denetimden düşüyordu."""
    from app import book

    with db.connect() as c:
        sid = _stmt(c, "h0101", 2900, 2900, 4400, 54.3, 4454.3, txns=(4400,))  # faiz ayrı satır, denklem tutuyor
        c.execute("INSERT INTO transactions (statement_id, txn_date, description, amount, txn_type) "
                  "VALUES (?, '2026-10-03', 'Akdi faiz', 54.3, 'faiz_ucret')", (sid,))
        bad = _stmt(c, "h0102", 0, 0, 1000, None, 1500, txns=(1000,))  # gizli kartta gerçek ihlal
        card = c.execute("SELECT card_no FROM statements WHERE id = ?", (bad,)).fetchone()[0]
    book.set_rules({"hide_fees": True, "hidden_cards": [card]})
    owner = integrity.check(date(2026, 10, 31))["refs"].get("ekstre_denklem")
    assert owner == [f"statement:{bad}"]
    with db.use_space(None, {"id": 2, "member_of": 1, "book_role": "editor"}):
        assert book.hide_fees()  # üye isteğinde süzme sürüyor
        assert integrity.check(date(2026, 10, 31))["refs"].get("ekstre_denklem") == owner
        assert book.hide_fees()  # denetimden sonra da


def test_owner_view_does_not_mix_member_read_cache(tmpdb):
    """Okuma önbelleği üyenin süzülmüş kopyasıyla sahip görünümünü karıştırmaz (gizli kart işlemi üyeye sızmaz)."""
    from app import book

    with db.connect() as c:
        sid = _stmt(c, "h0201", 0, 0, 300, None, 300, txns=(300,))
        card = c.execute("SELECT card_no FROM statements WHERE id = ?", (sid,)).fetchone()[0]
    book.set_rules({"hidden_cards": [card]})
    with db.use_space(None, {"id": 2, "member_of": 1, "book_role": "editor"}):
        assert db.card_transactions() == []
        with book.owner_view():
            assert len(db.card_transactions()) == 1
        assert db.card_transactions() == []
