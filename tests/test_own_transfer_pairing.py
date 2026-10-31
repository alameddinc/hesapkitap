"""Kendi hesapları arası transfer: giden açıklamada alıcı kesik olsa da (Halkbank), aynı gün aynı tutar başka kendi
hesabına giriş varsa harcama sayılmaz — dökümler hangi sırayla yüklenirse yüklensin."""


def _rows(bank_rows):
    from app.bank_import import finalize

    return finalize(bank_rows, ["AYŞE YILMAZ"])


HALK_OUT = {"date": "18.10.2026", "description": "GIDEN FAST - AYŞE YILMAZ TARAFI M20990101-120000-000001/", "amount": -120000.0}
VAKIF_IN = {"date": "18.10.2026", "amount": 120000.0,
            "description": "Gelen FAST Anlık Ödeme GİDEN FAST - AYŞE YILMAZ TARAFINDAN GÖNDERİLEN (18.10.2026 tarihli 1000000001 "
                           "sorgu no'lu Türkiye Halk Bankası A.Ş. AYŞE YILMAZ hesabından Ayşe Yılmaz hesabına gelen FAST ödemesi)"}
RENT = {"date": "18.10.2026", "description": "Elif Demir · kira", "amount": -27000.0}


def _gider(db):
    with db.connect() as c:
        return c.execute("SELECT COALESCE(SUM(amount), 0) FROM entries WHERE kind = 'gider'").fetchone()[0]


def test_outgoing_first_then_incoming_repairs(tmpdb):
    db = tmpdb
    db.import_bank_rows(_rows([HALK_OUT, RENT]), account="Halkbank", owners=["AYŞE YILMAZ"])
    assert _gider(db) == 147000  # henüz karşılığı yok: gider görünür
    db.import_bank_rows(_rows([VAKIF_IN]), account="VakıfBank", owners=["AYŞE YILMAZ"])
    assert _gider(db) == 27000  # yalnız kira kalır; 120.000 virmana taşındı


def test_incoming_first_then_outgoing_is_virman(tmpdb):
    db = tmpdb
    db.import_bank_rows(_rows([VAKIF_IN]), account="VakıfBank", owners=["AYŞE YILMAZ"])
    st = db.import_bank_rows(_rows([HALK_OUT, RENT]), account="Halkbank", owners=["AYŞE YILMAZ"])
    assert _gider(db) == 27000 and st["virman"] >= 1


def test_same_amount_to_someone_else_stays_expense(tmpdb):
    """Başkasına giden aynı tutar (açıklamada kullanıcının adı yok) virman sayılmaz."""
    db = tmpdb
    db.import_bank_rows(_rows([VAKIF_IN]), account="VakıfBank", owners=["AYŞE YILMAZ"])
    other = {"date": "18.10.2026", "description": "GIDEN FAST - MEHMET YILMAZ TARAFINA", "amount": -120000.0}
    db.import_bank_rows(_rows([other]), account="Halkbank", owners=["AYŞE YILMAZ"])
    assert _gider(db) == 120000


HAVALE_ES = ("HAVALE - AYŞE ÖRNEK TARAFINDAN GÖNDERILEN,AYŞE ÖRNEK,TR000001000000000000000001,MEHMET ÖRNEK,"
             "TR000006000000000000000002")


def test_havale_to_other_person_not_paired_as_own_transfer(tmpdb):
    """Canlı bulgu: sahibin gönderdiği, alıcısı başka kişi olan Halkbank havalesi; aynı gün aynı tutarda başka kendi
    hesabına giriş var diye virman sayılıyordu. Alıcı açıkça farklıysa gider kalır (uydurma ad/IBAN)."""
    owners = ["AYŞE ÖRNEK"]
    tmpdb.import_bank_rows([{"date": "2026-10-10", "description": "Kendi hesabından gelen", "amount": 60300.0,
                             "type": "virman", "hash": "in-1"}], account="VakıfBank", owners=owners)
    st = tmpdb.import_bank_rows([{"date": "2026-10-10", "description": HAVALE_ES, "amount": -60300.0, "type": "gider",
                                  "hash": "out-1"}], account="Halkbank", owners=owners)
    assert st["gider"] == 1 and st["virman"] == 0
    e = next(x for x in tmpdb.list_entries() if x["import_hash"] == "out-1")
    assert e["kind"] == "gider" and e["counterparty"] and "Mehmet" in e["counterparty"]
    assert not tmpdb.repair_own_transfers(owners)  # onarım da taşımıyor


def test_bank_repair_restores_wrong_own_transfer_without_file(tmpdb):
    from app import bank_repair

    owners = ["AYŞE ÖRNEK"]
    with tmpdb.connect() as c:
        tmpdb.add_own_transfer(c, "2026-10-10", -60300.0, "Halkbank", HAVALE_ES, "out-2", owners)
        tmpdb.add_own_transfer(c, "2026-10-11", -100.0, "Halkbank",
                               "HAVALE - AYŞE ÖRNEK TARAFINDAN GÖNDERILEN,AYŞE ÖRNEK,TR01,AYŞE ÖRNEK,TR02", "own-ok", owners)
    import pytest

    mp = pytest.MonkeyPatch()
    mp.setattr(bank_repair, "_owners", lambda: owners)
    try:
        dry = bank_repair.repair(apply=False)
        assert [r["amount"] for r in dry["restored"]] == [-60300.0]  # kuru çalıştırmada görünür, yazılmaz
        with tmpdb.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM own_transfers").fetchone()[0] == 2
        bank_repair.repair(apply=True)
    finally:
        mp.undo()
    with tmpdb.connect() as c:
        assert [r[0] for r in c.execute("SELECT import_hash FROM own_transfers")] == ["own-ok"]
        e = c.execute("SELECT kind, amount, not_virman FROM entries WHERE import_hash = 'out-2'").fetchone()
    assert tuple(e) == ("gider", 60300.0, 1)


def test_incoming_leg_paired_when_outgoing_is_own_transfer(tmpdb):
    """Canlı bulgu: VakıfBank → Halkbank çıkışı virman, Halkbank'taki giriş ('X,X 'DAN GELEN FAST') gelir kalmıştı.
    Hesap sahibinin adı bilinmese de gönderen = alıcı kalıbı ve karşı çıkış varsa giriş de virmana bağlanır."""
    from app import bank_repair

    with tmpdb.connect() as c:
        tmpdb.add_own_transfer(c, "2026-06-30", -29000.0, "VakıfBank", "Kendi hesabına", "vb-out", [])
        for h, desc in (("hb-in", "AYŞE ÖRNEK,AYŞE ÖRNEK 'DAN GELEN FAST ODEMESI"),
                        ("hb-other", "Kira,MEHMET ÖRNEK 'DAN GELEN FAST ODEMESI"),  # başka kişi: gelir kalır
                        ("hb-plain", "EFT GELEN ODEME")):  # gönderen bilinmiyor: gelir kalır
            c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, import_hash, note) "
                      "VALUES ('2026-06-30', '2026-06', ?, 29000, 'gelir', ?, NULL)", (desc, h))  # ham satırı notta yok
    import pytest

    mp = pytest.MonkeyPatch()
    mp.setattr(bank_repair, "_owners", lambda: [])
    try:
        rep = bank_repair.repair(apply=True)
    finally:
        mp.undo()
    assert [p["amount"] for p in rep["paired"]] == [29000.0]
    with tmpdb.connect() as c:
        left = {r[0] for r in c.execute("SELECT import_hash FROM entries WHERE kind = 'gelir'")}
        moved = c.execute("SELECT amount FROM own_transfers WHERE import_hash = 'hb-in'").fetchone()
    assert left == {"hb-other", "hb-plain"} and moved[0] == 29000.0
    assert not tmpdb.repair_own_transfers([])  # ikinci çalıştırma: değişiklik yok


def test_incoming_import_pairs_with_existing_outgoing(tmpdb):
    owners = ["AYŞE ÖRNEK"]
    tmpdb.import_bank_rows([{"date": "2026-06-30", "description": "Kendi hesabına", "amount": -500.0, "type": "virman",
                             "hash": "o-1"}], account="VakıfBank", owners=owners)
    st = tmpdb.import_bank_rows([{"date": "2026-07-01", "description": "AYŞE ÖRNEK hesabından gelen", "amount": 500.0,
                                  "type": "gelir", "hash": "i-1"}], account="Halkbank", owners=owners)
    assert st["virman"] == 1 and st["gelir"] == 0
