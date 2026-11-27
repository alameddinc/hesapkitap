"""Havale kuralı (1.0.14): gönderen = alıcı (normalize, Türkçe harfler katlanmış) → kendi hesapları arası; aile
paketinde (ortak defter) alıcı defterdeki aile üyesi → ev içi aktarım; diğer her durum gider.
Adlar ve bankalar uydurmadır."""
import pytest

from app import bank_import, book, db

OUT_TO = "GIDEN FAST - AYŞE ÖRNEK TARAFINDAN GONDERILEN,{to},ORNEK BANKA A.S. 123"


@pytest.mark.parametrize("to, kind", [
    ("AYSE ORNEK", "virman"),     # aynı kişi, Türkçe harfsiz yazılmış
    ("AYŞE ÖRNEK", "virman"),
    ("ALİ DENEME", "gider"),      # başka kişi
])
def test_same_name_is_own_transfer(to, kind):
    assert bank_import.classify(OUT_TO.format(to=to), -500.0, ["Ayşe Örnek"]) == kind


def test_household_rule_pure():
    fam = ["Ayşe Örnek", "Ali Örnek"]
    assert db.household_transfer(OUT_TO.format(to="ALİ ÖRNEK"), -500.0, fam)
    assert db.household_transfer(OUT_TO.format(to="ALI ORNEK"), -500.0, fam)
    assert not db.household_transfer(OUT_TO.format(to="VELİ DENEME"), -500.0, fam)
    assert not db.household_transfer(OUT_TO.format(to="ALİ ÖRNEK"), 500.0, fam)   # gelen: kural yok
    assert not db.household_transfer(OUT_TO.format(to="ALİ ÖRNEK"), -500.0, [])  # aile yok
    assert not db.household_transfer("ORNEK MARKET", -500.0, fam)                  # alıcı okunamıyor


def _rows(to, h):
    return [{"date": "2026-11-10", "description": OUT_TO.format(to=to), "amount": -750.0,
             "type": bank_import.classify(OUT_TO.format(to=to), -750.0, ["Ayşe Örnek"]), "hash": h}]


def _counts(tmpdb):
    with tmpdb.connect() as c:
        return (c.execute("SELECT COUNT(*) FROM entries WHERE kind = 'gider'").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM own_transfers").fetchone()[0])


def test_family_member_receiver_not_expense(tmpdb, monkeypatch):
    monkeypatch.setattr(book, "shared_people", lambda: [{"id": 1}, {"id": 2}])
    tmpdb.set_setting("household_names", '["Ali Örnek"]')
    st = tmpdb.import_bank_rows(_rows("ALİ ÖRNEK", "h1"), account="Örnek Banka", owners=["Ayşe Örnek"])
    assert st.get("ev_ici") == 1
    assert _counts(tmpdb) == (0, 1)
    with tmpdb.connect() as c:
        assert c.execute("SELECT note FROM own_transfers").fetchone()[0] == "Ev içi aktarım (aile üyesi)"


def test_non_family_receiver_is_expense_in_family(tmpdb, monkeypatch):
    monkeypatch.setattr(book, "shared_people", lambda: [{"id": 1}, {"id": 2}])
    tmpdb.set_setting("household_names", '["Ali Örnek"]')
    tmpdb.import_bank_rows(_rows("VELİ DENEME", "h2"), account="Örnek Banka", owners=["Ayşe Örnek"])
    assert _counts(tmpdb) == (1, 0)


def test_single_book_family_name_is_expense(tmpdb):
    """Tek kişilik defter: aile kuralı yok, ad ayarda olsa da gider (mevcut davranış)."""
    tmpdb.set_setting("household_names", '["Ali Örnek"]')
    assert db.family_names() == []
    tmpdb.import_bank_rows(_rows("ALİ ÖRNEK", "h3"), account="Örnek Banka", owners=["Ayşe Örnek"])
    assert _counts(tmpdb) == (1, 0)


def test_same_name_import_is_transfer(tmpdb):
    tmpdb.import_bank_rows(_rows("AYSE ORNEK", "h4"), account="Örnek Banka", owners=["Ayşe Örnek"])
    assert _counts(tmpdb) == (0, 1)
