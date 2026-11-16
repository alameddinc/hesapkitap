"""Sayfa altbilgisi satır açıklamasına taşıyordu (16.11 canlı, Halkbank tek sayfalık döküm): son hareketin açıklaması
"GIDEN FAST - X TARAFI Sayfa No:1 / 1 Türkiye Halk Bankası A.Ş halkbank.com.tr 0 850 … Halkbank Dia…" oluyordu.
Tüm isimler, IBAN'lar ve tutarlar uydurma."""
from app import bank_import, doc_footer, templates
from app.parsers import _remember_pages

OWNER = "AYŞE YILMAZ"
FOOT_LINES = ["Sayfa No:{p} / {n}", "Türkiye Halk Bankası A.Ş halkbank.com.tr 0 850 222 0 400 Halkbank Diyalog"]
HEAD = ("HESAP ÖZETİ\nMüşteri Bilgileriniz Hesap Özeti Bilgileriniz\n"
        "Müşteri Adı / Ünvanı :AYSE YILMAZ Dönemi :24.10.2025-24.10.2026\n"
        "IBAN :TR120001200000000000000001 Bloke Bakiyesi :0,00\n"
        "İşlem Tarihi İşlem Tutarı Bakiye Açıklama Dekont No\n")
SELF_FULL = "GIDEN FAST - AYSE YILMAZ TARAFINDAN GÖNDERILEN,Ayşe Yılmaz,Türkiye Vakiflar Bankasi T.A.O.,TR120001500100000000000000001"


def _m(v: float) -> str:
    return f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _rows(n: int, start_bal: float = 90000.0) -> list[str]:
    """Halkbank düzeninde n hareket (her biri bir blok): kira (not,Alıcı,Banka,IBAN) ve kendi hesabına FAST."""
    out, bal = [], start_bal
    for i in range(1, n + 1):
        amt = 1000.0 + i
        bal -= amt
        if i % 2:
            out.append(f"{i:02d}-10-2026 -{_m(amt)} {_m(bal)} Kira,Hale Yildiz,Türkiye Is Ban M20990101-120100-00001{i % 10}/01\n"
                       "kasi A.S.,TR77000640000000000000001\n1")
        else:
            out.append(f"{i:02d}-10-2026 -{_m(amt)} {_m(bal)} GIDEN FAST - AYSE YILMAZ TARAFI M20990101-120000-00000{i % 10}/01\n"
                       "NDAN GÖNDERILEN,Ayşe Yılmaz,Tür\nkiye Vakiflar Bankasi T.A.O.,TR12000150\n0100000000000000001")
    return out


def _last_self_split_by_footer(p: int, n: int) -> str:
    """Son satır: ilk satırı altbilginin ÜSTÜNDE, devamı ALTINDA (PDF metin sırası)."""
    foot = "\n".join(FOOT_LINES).format(p=p, n=n)
    return (f"20-10-2026 -5.000,00 {{bal}} GIDEN FAST - AYSE YILMAZ TARAFI M20990101-120000-000009/01\n{foot}\n"
            "NDAN GÖNDERILEN,Ayşe Yılmaz,Tür\nkiye Vakiflar Bankasi T.A.O.,TR12000150\n0100000000000000001")


# ---------------------------------------------------------------- saf temizleyici

def test_clean_live_shape():
    live = ("GIDEN FAST - AYSE YILMAZ TARAFI Sayfa No:1 / 1 Türkiye Halk Bankası A.Ş halkbank.com.tr 0 850 222 0 400 "
            "Halkbank Dia")
    assert doc_footer.clean(live) == "GIDEN FAST - AYSE YILMAZ TARAFI"


def test_clean_keeps_continuation_after_footer_and_rejoins_split_word():
    s = ("GIDEN FAST - AYSE YILMAZ TARAFI Sayfa No:1 / 1 Türkiye Halk Bankası A.Ş halkbank.com.tr 0 850 222 0 400 "
         "Halkbank Diyalog NDAN GÖNDERILEN,Ayşe Yılmaz,Türkiye Vakiflar Bankasi T.A.O.,TR120001500100000000000000001")
    assert doc_footer.clean(s) == SELF_FULL


def test_clean_generic_other_banks():
    assert doc_footer.clean("EFT ODEME Ali Örnek www.ornekbank.com.tr 0850 222 0 724 Sayfa 1/2") == "EFT ODEME Ali Örnek"
    assert doc_footer.clean("Kira,Hale Yildiz Sayfa 2 / 3 Dekont yerine kullanılamaz.") == "Kira,Hale Yildiz"
    assert doc_footer.clean("POS ORNEK MARKET Page 3 of 4") == "POS ORNEK MARKET"
    assert doc_footer.clean("FAST Ödeme T.C. Ziraat Bankası A.Ş. ziraatbank.com.tr") == "FAST Ödeme"


def test_clean_leaves_real_descriptions_alone():
    for s in (SELF_FULL, "IYZICO/ORNEKSHOP.COM ISTANBUL", "Kira,Hale Yildiz,Türkiye Is Bankasi A.S.,TR770006400000000000000011",
              "Örnek Tahsilatı Telefon Numarası:5000000001, Fatura No: FD00000A000B01", "Sayfa No:1 / 1", "", None):
        assert doc_footer.clean(s) == s


# ---------------------------------------------------------------- şablon yolu (tek ve çok sayfa)

def _single_page() -> str:
    body = _rows(4)
    bal = 90000.0 - sum(1000.0 + i for i in range(1, 5)) - 5000.0
    body.append(_last_self_split_by_footer(1, 1).format(bal=_m(bal)))
    return HEAD + "\n".join(body) + "\n"


def test_single_page_last_row_footer_removed_template():
    text = _single_page()
    spec, res = templates.learn(text, "Halkbank")
    assert res["verified"] and len(res["rows"]) == 5
    rows = bank_import.finalize(res["rows"], [OWNER])
    last = rows[-1]["description"]
    assert last == SELF_FULL
    assert all("Sayfa" not in r["description"] and "halkbank.com.tr" not in r["description"] for r in rows)


def test_multi_page_footer_removed_on_every_page():
    pages, bal = [], 90000.0
    rows = _rows(6)
    for p, chunk in enumerate((rows[:3], rows[3:]), start=1):
        pages.append((HEAD if p == 1 else "HESAP ÖZETİ\nİşlem Tarihi İşlem Tutarı Bakiye Açıklama Dekont No\n")
                     + "\n".join(chunk) + "\n" + "\n".join(FOOT_LINES).format(p=p, n=2) + "\n")
    text = "\n".join(pages)
    _remember_pages(text, pages)
    spec, res = templates.learn(text, "Halkbank")
    assert res["verified"] and len(res["rows"]) == 6
    out = bank_import.finalize(res["rows"], [OWNER])
    assert all("Sayfa" not in r["description"] and "Diyalog" not in r["description"] for r in out)
    assert [r["description"] for r in out if "GIDEN" in r["description"]] == [SELF_FULL] * 3


# ---------------------------------------------------------------- parmak izi, kendi adına FAST, eski kayıtlar

def test_fingerprint_unchanged_by_cleaning():
    dirty = "GIDEN FAST - AYSE YILMAZ TARAFI Sayfa No:1 / 1 Türkiye Halk Bankası A.Ş halkbank.com.tr 0 850 222 0 400"
    a = bank_import.finalize([{"date": "2026-10-20", "description": dirty, "amount": -5000.0, "balance": 1234.5}], [OWNER])
    b = bank_import.finalize([{"date": "2026-10-20", "description": "GIDEN FAST - AYSE YILMAZ TARAFI", "amount": -5000.0,
                               "balance": 1234.5}], [OWNER])
    assert a[0]["hash"] == b[0]["hash"] and a[0]["description"] == "GIDEN FAST - AYSE YILMAZ TARAFI"
    # bakiyesiz satırda iz açıklamaya bakar: eski (altbilgili) iz alt_hash olarak taşınır
    c = bank_import.finalize([{"date": "2026-10-20", "description": dirty, "amount": -5000.0}], [OWNER])
    assert c[0]["alt_hash"] == bank_import.row_hash(bank_import.parse_date("2026-10-20"), dirty, -5000.0, "")


def test_owner_party_transfer_with_footer_and_truncated_tarafi(tmpdb):
    tmpdb.remember_owner_name(OWNER)
    dirty = ("GIDEN FAST - AYSE YILMAZ TARAFI Sayfa No:1 / 1 Türkiye Halk Bankası A.Ş halkbank.com.tr 0 850 222 0 400 "
             "Halkbank Dia")
    assert tmpdb.owner_party_transfer(dirty, -5000.0, [OWNER])
    assert tmpdb.owner_party_transfer("GIDEN FAST - AYSE YILMAZ TARAFI", -5000.0, [OWNER])
    assert tmpdb.owner_party_transfer(doc_footer.clean(dirty), -5000.0, [OWNER])
    # alıcı başka biri: kendi hesabı değil
    other = "GIDEN FAST - AYSE YILMAZ TARAFINDAN GÖNDERILEN,Ali Örnek,X Bankasi A.S.,TR00 Sayfa No:1 / 1"
    assert not tmpdb.owner_party_transfer(other, -5000.0, [OWNER])


def test_old_dirty_entries_cleaned_on_read_only_and_reimport_no_duplicate(tmpdb):
    dirty = ("GIDEN FAST - ALI ORNEK TARAFI Sayfa No:1 / 1 Türkiye Halk Bankası A.Ş halkbank.com.tr 0 850 222 0 400 "
             "Halkbank Diyalog")
    rows = bank_import.finalize([{"date": "2026-10-20", "description": dirty, "amount": -5000.0, "balance": 1234.5}], [])
    eid = tmpdb.create_entry({"entry_date": "2026-10-20", "description": dirty, "amount": 5000, "kind": "gider",
                              "category": "Kategorisiz", "note": f"Hesap hareketi · {dirty}", "import_hash": rows[0]["hash"]})
    e = next(x for x in tmpdb.list_entries() if x["id"] == eid)
    assert e["description"] == "GIDEN FAST - ALI ORNEK TARAFI"
    with tmpdb.connect() as c:  # kayıt değişmedi (canlıya yazma yok)
        assert c.execute("SELECT description FROM entries WHERE id = ?", (eid,)).fetchone()[0] == dirty
    st = tmpdb.import_bank_rows(rows, account="Türkiye Halk Bankası A.Ş")
    assert st["tekrar"] == 1
    with tmpdb.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1


def test_single_page_known_boundaries_keeps_repeated_continuation():
    text = _single_page()
    _remember_pages(text, [text])  # PDF'ten okunmuş tek sayfa
    spec, res = templates.learn(text, "Halkbank")
    out = bank_import.finalize(res["rows"], [OWNER])
    assert [r["description"] for r in out if "GIDEN" in r["description"]] == [SELF_FULL] * 3
