"""Eski veride bir kez çalışan iki onarım: dökümdeki maaş satırı maaş kalemine bağlanır / 'Maaş' olur; iptal edilip geri
ödenen havale ile iadesi (ikisi de sayılıyordu) silinir. Veriler uydurma."""


def _acc(db):
    db.set_account_balance("Örnek Banka", 1000, "2026-01-01", source="elle")
    with db.connect() as c:
        return c.execute("SELECT id FROM accounts WHERE name = 'Örnek Banka'").fetchone()[0]


def _bank(db, d, desc, amount, kind, acc, h, **kw):
    return db.create_entry({"entry_date": d, "description": desc, "amount": amount, "kind": kind,
                            "category": kw.pop("category", "Diğer Gelir" if kind == "gelir" else "Kategorisiz"),
                            "import_hash": h, "account_id": acc, "note": f"Hesap hareketi · {desc}", **kw})


def _get(db, eid):
    with db.connect() as c:
        r = c.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
        return dict(r) if r else None


def test_salary_lines_link_to_salary_recurring(tmpdb):
    from app import finance

    acc = _acc(tmpdb)
    # banka metni her ay farklı kısaltılmış; biri aynı ayın ikinci maaş benzeri satırı (ikramiye)
    a = _bank(tmpdb, "2026-02-15", "ISÇI UCRET 2026 SUBAT ÜCRET ÖDEMES M1", 200000, "gelir", acc, "h1")
    b = _bank(tmpdb, "2026-03-15", "ISÇI UCRET 2026 MART ÜCRET ÖDEM M2", 201000, "gelir", acc, "h2")
    b2 = _bank(tmpdb, "2026-03-16", "MAAS FARKI ODEMESI M3", 5000, "gelir", acc, "h3")
    other = _bank(tmpdb, "2026-03-20", "Örnek Kişi", 7000, "gelir", acc, "h4")
    rid = tmpdb.create_recurring({"description": "Maaş", "amount": 200000, "kind": "gelir", "category": "Maaş",
                                  "day_of_month": 15, "start_month": "2026-01"})
    ea, eb, eb2, eo = (_get(tmpdb, x) for x in (a, b, b2, other))
    assert (ea["recurring_id"], ea["category"]) == (rid, "Maaş")
    assert (eb["recurring_id"], eb["category"]) == (rid, "Maaş")
    assert (eb2["recurring_id"], eb2["category"]) == (None, "Maaş")  # o dönemin maaşı dolu: yalnız kategori
    assert (eo["recurring_id"], eo["category"]) == (None, "Diğer Gelir")
    assert tmpdb.link_salary_entries() == 0  # idempotent
    mar = [r for r in finance.transactions("2026-03") if r["kind"] == "gelir"]
    assert sorted(r["amount"] for r in mar) == [5000, 7000, 201000]  # tahmini maaş eklenmedi


def test_salary_text_without_recurring_gets_category(tmpdb):
    acc = _acc(tmpdb)
    e = _bank(tmpdb, "2026-02-15", "Maaş Ödemesi ÖRNEK KİŞİ 0001", 150000, "gelir", acc, "h1")
    assert tmpdb.link_salary_entries() == 1
    assert _get(tmpdb, e)["category"] == "Maaş"


def test_cancelled_transfer_pair_removed(tmpdb):
    acc = _acc(tmpdb)
    out = _bank(tmpdb, "2026-06-30", "FAST Anlık Ödeme örnek mesaj (30/06/2026 tarihli 1234567890 sorgu no'lu)", 400, "gider", acc, "o1")
    back = _bank(tmpdb, "2026-06-30", "FAST Anlık Ödeme İptali örnek mesaj (30/06/2026 tarihli 1234567890 sorgu no'lu)", 400, "gelir", acc, "i1")
    keep = _bank(tmpdb, "2026-06-30", "Örnek Kişi · kira", 400, "gider", acc, "o2")
    # sorgu numarası tutmayan iade: eşleşmez
    out2 = _bank(tmpdb, "2026-07-02", "FAST Anlık Ödeme (1111111111 sorgu no)", 250, "gider", acc, "o3")
    back2 = _bank(tmpdb, "2026-07-02", "FAST Anlık Ödeme İptali (2222222222 sorgu no)", 250, "gelir", acc, "i2")
    # kullanıcının borca bağladığı gider: dokunulmaz
    did = tmpdb.create_debt({"person": "Örnek", "direction": "alacak", "amount": 90, "debt_date": "2026-07-05", "asset": "TRY"})
    out3 = _bank(tmpdb, "2026-07-05", "FAST Anlık Ödeme (3333333333)", 90, "gider", acc, "o4", debt_id=did)
    back3 = _bank(tmpdb, "2026-07-05", "FAST Anlık Ödeme İptali (3333333333)", 90, "gelir", acc, "i3")
    assert tmpdb.pair_cancelled_entries() == 1
    assert _get(tmpdb, out) is None and _get(tmpdb, back) is None
    assert all(_get(tmpdb, x) for x in (keep, out2, back2, out3, back3))
    assert tmpdb.pair_cancelled_entries() == 0


def test_repairs_run_once_on_init(tmpdb):
    acc = _acc(tmpdb)
    e = _bank(tmpdb, "2026-02-15", "MAAS 2026 SUBAT ODEMESI", 150000, "gelir", acc, "h1")
    tmpdb.set_setting("salary_linked_v1", "")
    tmpdb.init_db()
    assert _get(tmpdb, e)["category"] == "Maaş"
    assert tmpdb.get_setting("salary_linked_v1") == "1" and tmpdb.get_setting("cancel_pairs_v1") == "1"


def test_cancel_pairs_with_most_similar_withdrawal(tmpdb):
    """Aynı gün aynı tutarda iki ATM çekimi, biri iptal: iadeye metni benzeyen çekim silinir."""
    acc = _acc(tmpdb)
    first = _bank(tmpdb, "2025-08-11", "ATM QR Para Çek ÖRNEK ATM 'sinde para çekti", 800, "gider", acc, "o1")
    second = _bank(tmpdb, "2025-08-11", "QR Limit Üstü Para Çekme ÖRNEK ATM 'sinde para çekti", 800, "gider", acc, "o2")
    back = _bank(tmpdb, "2025-08-11", "QR Limit Üstü Para Çekme ÖRNEK ATM 'den yapmış olduğu para çekme işleminin iptali",
                 800, "gelir", acc, "i1")
    assert tmpdb.pair_cancelled_entries() == 1
    assert _get(tmpdb, first) and _get(tmpdb, second) is None and _get(tmpdb, back) is None
