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
