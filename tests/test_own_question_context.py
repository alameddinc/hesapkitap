"""'Alıcısı okunamadı — kendi hesabına mı?' (finans denetimi kendi_adi_hareket, karşı bacaksız): yalnız son 30 günün havalesi
sorulur; soruda bağlam (banka satırı, öbür hesaplardaki yakın hareketler, aynı tutarlı önceki cevaplar); "Hatırlamıyorum" ve
"Eskileri geç". Ad, banka, tutar uydurmadır; tarihler bugüne göre."""
from datetime import date, timedelta

from app import finance_audit

T = date.today()
OWNER = "AYSE ORNEK"
OUT_DESC = f"GIDEN FAST - {OWNER} TARAFINDAN GONDERILEN"
BANK_A, BANK_B = "Örnek Banka A", "Örnek Banka B"


def _acc(db, name, balance=10000.0, days_ago=60) -> int:
    db.set_account_balance(name, balance, (T - timedelta(days=days_ago)).isoformat(), "elle", "banka")
    with db.connect() as c:
        return c.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()[0]


def _gider(db, acc, days_ago, amount=14000.0, h=None, desc=OUT_DESC) -> int:
    return db.create_entry({"entry_date": (T - timedelta(days=days_ago)).isoformat(), "description": desc, "amount": amount,
                            "kind": "gider", "category": "Kategorisiz", "payment": "havale", "account_id": acc,
                            "import_hash": h or f"h-{days_ago}-{amount}"})


def _gelir(db, acc, days_ago, amount, h):
    return db.create_entry({"entry_date": (T - timedelta(days=days_ago)).isoformat(), "description": "GELEN HAVALE ORNEK",
                            "amount": amount, "kind": "gelir", "category": "Diğer Gelir", "payment": "havale",
                            "account_id": acc, "import_hash": h})


def _questions(client):
    return {i["entry_id"]: i for i in client.get("/api/finance-audit").json()["items"] if i.get("action") == "own_question"}


def test_only_last_30_days_are_asked_older_are_quiet(client, tmpdb):
    tmpdb.remember_owner_name(OWNER)
    a = _acc(tmpdb, BANK_A)
    _acc(tmpdb, BANK_B)
    new, edge, old = _gider(tmpdb, a, 5), _gider(tmpdb, a, 30, 9000.0), _gider(tmpdb, a, 45, 7000.0)
    finance_audit.run(T)
    res = finance_audit.last()
    assert sorted(_questions(client)) == sorted([new, edge])
    assert f"entry:{old}" not in res["refs"].get("kendi_adi_hareket", [])
    assert res["quiet"] == {"kendi_adi_hareket": 1}
    assert client.get("/api/finance-audit").json()["quiet"] == {"kendi_adi_hareket": 1}
    with tmpdb.connect() as c:  # sessizce gider kalır
        assert c.execute("SELECT kind, not_virman FROM entries WHERE id = ?", (old,)).fetchone()[:] == ("gider", 0)


def test_old_entry_with_counter_leg_still_suggests_transfer(client, tmpdb):
    """Kanıtlı (öbür hesapta aynı tutar ±1 gün giriş) eski kayıt soru değil: "Aktarıma çevir" yaşından bağımsız kalır."""
    tmpdb.remember_owner_name(OWNER)
    a, b = _acc(tmpdb, BANK_A, days_ago=90), _acc(tmpdb, BANK_B, days_ago=90)
    eid = _gider(tmpdb, a, 50)
    _gelir(tmpdb, b, 50, 14000.0, "h-in-50")
    finance_audit.run(T)
    it = next(i for i in client.get("/api/finance-audit").json()["items"] if i.get("entry_id") == eid)
    assert it["action"] == "entry_to_transfer" and it["counter_account_id"] == b


def test_question_carries_context_near_legs_and_prior_answers(client, tmpdb):
    tmpdb.remember_owner_name(OWNER)
    a, b = _acc(tmpdb, BANK_A), _acc(tmpdb, BANK_B)
    eid = _gider(tmpdb, a, 6)
    _gelir(tmpdb, b, 3, 14000.0, "h-in-3")          # aynı tutar, 3 gün sonra (karşı bacak değil: ±1 gün dışı)
    _gelir(tmpdb, b, 6, 2500.0, "h-in-other")       # aynı gün ama tutar uzak: aday değil
    prev = _gider(tmpdb, a, 20, h="h-prev")           # aynı tutarlı önceki soru → "Başkasına" dendi
    finance_audit.run(T)
    assert client.post("/api/finance-audit/not-own", json={"entry_id": prev}).status_code == 200
    it = _questions(client)[eid]
    assert it["description"] == OUT_DESC and it["date"] == (T - timedelta(days=6)).isoformat()
    assert it["account"] == BANK_A and it["age_days"] == 6 and it["cta_unknown"] == "Hatırlamıyorum"
    assert [(n["account_id"], n["amount"], n["same_amount"], n["days"]) for n in it["near"]] == [(b, 14000.0, True, 3)]
    assert "giriş var — o mu?" in it["text"] and BANK_B in it["text"]
    assert [p["answer"] for p in it["prior"]] == ["baskasi"]


def test_prior_answer_own_account_names_target(client, tmpdb):
    tmpdb.remember_owner_name(OWNER)
    a, b = _acc(tmpdb, BANK_A), _acc(tmpdb, BANK_B)
    first = _gider(tmpdb, a, 15, h="h-first")
    second = _gider(tmpdb, a, 2, h="h-second")
    finance_audit.run(T)
    r = client.post("/api/finance-audit/entry-to-transfer", json={"entry_id": first, "account_id": b})
    assert r.status_code == 200, r.text
    it = _questions(client)[second]
    assert [(p["answer"], p["account_id"], p["account"]) for p in it["prior"]] == [("kendi", b, BANK_B)]


def test_hatirlamiyorum_keeps_expense_and_never_asks_again(client, tmpdb):
    tmpdb.remember_owner_name(OWNER)
    a = _acc(tmpdb, BANK_A)
    _acc(tmpdb, BANK_B)
    eid = _gider(tmpdb, a, 4)
    finance_audit.run(T)
    assert eid in _questions(client)
    r = client.post("/api/finance-audit/not-own", json={"entry_id": eid, "reason": "hatirlamiyorum"})
    assert r.status_code == 200, r.text
    assert eid not in _questions(client)
    with tmpdb.connect() as c:
        assert c.execute("SELECT kind, not_virman FROM entries WHERE id = ?", (eid,)).fetchone()[:] == ("gider", 1)
    assert client.post("/api/finance-audit/not-own", json={"entry_id": eid, "reason": "bilmem"}).status_code == 422


def test_skip_old_questions_bulk(client, tmpdb):
    tmpdb.remember_owner_name(OWNER)
    a = _acc(tmpdb, BANK_A)
    _acc(tmpdb, BANK_B)
    fresh = _gider(tmpdb, a, 3, 1000.0)
    old1, old2 = _gider(tmpdb, a, 10, 2000.0), _gider(tmpdb, a, 25, 3000.0)
    finance_audit.run(T)
    q = _questions(client)
    assert sorted(q) == sorted([fresh, old1, old2])
    assert q[fresh]["skip_old"] == {"count": 2, "days": 7}
    r = client.post("/api/finance-audit/skip-old-questions")
    assert r.status_code == 200 and r.json() == {"ok": True, "skipped": 2}
    q = _questions(client)
    assert sorted(q) == [fresh] and "skip_old" not in q[fresh]  # tek eski kalmadı: toplu düğme yok
    assert client.post("/api/finance-audit/skip-old-questions").json()["skipped"] == 0


def test_single_old_question_has_no_bulk_skip(client, tmpdb):
    tmpdb.remember_owner_name(OWNER)
    a = _acc(tmpdb, BANK_A)
    _acc(tmpdb, BANK_B)
    eid = _gider(tmpdb, a, 12)
    finance_audit.run(T)
    assert "skip_old" not in _questions(client)[eid]
