"""Hesap dökümü açıklamaları ve kendi hesapları arası aktarım (2026-10 canlı bulguları). Tüm isimler/IBAN'lar uydurma.

- Halkbank şablon okuyucusu açıklamanın sık tekrarlanan devam satırlarını sayfa başlığı sanıp atıyordu.
- Hesap sahibinin adı bilinmeyince kendi hesabından gelen para gelir, giden para gider sayılıyordu.
- Halkbank 'not,Alıcı,Banka,IBAN' biçiminde alıcı/ödeme biçimi bulunamıyordu; VakıfBank notu '(28' kalıyordu.
- İptal edilen ödeme ve iadesi hem gider hem gelir sayılıyordu; aynı gün/tutar/bakiyeli ikinci hareket kayboluyordu.
"""
from app import bank_import, bank_pdf, templates
from app.parsers import _remember_pages

OWNER = "AYŞE YILMAZ"

# Halkbank düzeni: sütun sabit genişlikte kesilir ('TARAFI' + 'NDAN', 'Tür' + 'kiye'), dekont no ilk satırın sonunda.
# Kendi VakıfBank hesabına giden satırın devam satırları her ay aynı → eski kural onları sayfa başlığı sanıyordu.
_SELF = ("{d} -{a} {b} GIDEN FAST - AYSE YILMAZ TARAFI M20990101-120000-00000{i}/01\n"
         "NDAN GÖNDERILEN,Ayşe Yılmaz,Tür\nkiye Vakiflar Bankasi T.A.O.,TR12000150\n0100000000000000001\n")
_NOTE = ("{d} -{a} {b} Kira,Hale Yildiz,Türkiye Is Ban M20990101-120100-00001{i}/01\n"
         "kasi A.S.,TR77000640000000000000001\n1\n")
_HEAD = ("HESAP ÖZETİ\nMüşteri Bilgileriniz Hesap Özeti Bilgileriniz\n"
         "Müşteri Adı / Ünvanı :AYSE YILMAZ Dönemi :24.10.2025-24.10.2026\n"
         "IBAN :TR120001200000000000000001 Bloke Bakiyesi :0,00\n"
         "İşlem Tarihi İşlem Tutarı Bakiye Açıklama Dekont No\n")
_FOOT = ("Sayfa No:{p} / 3\nTürkiye Halk Bankası A.Ş halkbank.com.tr Dekont\nyerine kullanılamaz.\n")
_PAGE_HEAD = "HESAP ÖZETİ\nİşlem Tarihi İşlem Tutarı Bakiye Açıklama Dekont No\n"


def _money(v: float) -> str:
    return f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _halkbank_pages() -> list[str]:
    bal, pages, i = 500000.0, [], 0
    for p in range(1, 4):
        body = _HEAD if p == 1 else _PAGE_HEAD
        for k in range(3):
            i += 1
            for tpl, amt in ((_SELF, 20000.0 + 1000 * i), (_NOTE, 3000.0)):
                bal -= amt
                body += tpl.format(d=f"{i:02d}-10-2026", a=_money(amt), b=_money(bal), i=i % 10)
        pages.append(body + _FOOT.format(p=p))
    return pages


def test_halkbank_repeated_continuation_lines_are_kept():
    pages = _halkbank_pages()
    text = "\n".join(pages)
    _remember_pages(text, pages)  # PDF'ten okunmuş gibi: sayfa sınırları bilinir
    spec, res = templates.learn(text, "Halkbank")
    assert res["verified"] and len(res["rows"]) == 18
    self_rows = [r for r in res["rows"] if "GIDEN FAST" in r["description"]]
    d = self_rows[-1]["description"]
    assert "Sayfa" not in d and "HESAP" not in d and "M2099" not in d
    assert d == "GIDEN FAST - AYSE YILMAZ TARAFINDAN GÖNDERILEN,Ayşe Yılmaz,Türkiye Vakiflar Bankasi T.A.O.,TR120001500100000000000000001"
    note_rows = [r for r in res["rows"] if r["description"].startswith("Kira")]
    assert note_rows[0]["description"] == "Kira,Hale Yildiz,Türkiye Is Bankasi A.S.,TR770006400000000000000011"  # IBAN da bölünmüştü


def test_halkbank_page_footer_dropped_without_known_pages():
    """Sayfa sınırı bilinmese de 'Sayfa No:x / y' satırları sınır sayılır."""
    text = "\n".join(_halkbank_pages())
    spec, res = templates.learn(text, "Halkbank")
    assert res["verified"]
    assert all("Sayfa" not in r["description"] and "Ayşe Yılmaz" in r["description"]
               for r in res["rows"] if "GIDEN" in r["description"])


def test_owner_name_from_header():
    assert bank_pdf.owner_from_text(_HEAD) == "AYSE YILMAZ"
    assert bank_pdf.owner_from_text("Hesap Hareketleri\nAY ŞE YILMAZ\nHesap No: 001") == "AY ŞE YILMAZ"
    assert bank_pdf.owner_from_text("HesapBilgileri: 1\nSayın AYŞE YILMAZ\nToplam") == "AYŞE YILMAZ"
    assert bank_pdf.owner_from_text("Ad Soyad/Unvan: A******** Ç****") == ""


VAKIF_IN = ("Gelen FAST Anlık Ödeme GİDEN FAST - AYŞE YILMAZ TARAFINDAN GÖNDERİLEN (18.10.2026 tarihli 1000000001 sorgu "
            "no'lu Türkiye Halk Bankası A.Ş. AYŞE YILMAZ hesabından Ayşe Yılmaz hesabına gelen FAST ödemesi)")
HALK_OUT = "GIDEN FAST - AYSE YILMAZ TARAFINDAN GÖNDERILEN,Ayşe Yılmaz,Türkiye Vakiflar Bankasi T.A.O.,TR120001500100000000000000001"


def test_self_transfer_recognised_without_owner_names():
    assert bank_import.classify(VAKIF_IN, 120000, []) == "virman"
    assert bank_import.classify(HALK_OUT, -120000, []) == "virman"
    assert bank_import.counterparty(VAKIF_IN, 120000, []) is None
    other = HALK_OUT.replace(",Ayşe Yılmaz,", ",Hale Yildiz,")
    assert bank_import.classify(other, -500, []) == "gider"
    assert bank_import.counterparty(other, -500, []) == "Hale Yildiz"


def test_halkbank_note_format_and_vakif_note():
    info = bank_import.describe("Kira,Hale Yildiz,Türkiye Is Bankasi A.S.,TR77000640000000000000001", -3000, [OWNER])
    assert info["description"] == "Hale Yildiz · Kira" and info["counterparty"] == "Hale Yildiz"
    assert info["payment"] == "havale"
    own_note = bank_import.describe("Ayşe Yılmaz,Hale Yildiz,Türkiye Is Bankasi A.S.,TR77000640000000000000001", -3000, [OWNER])
    assert own_note["description"] == "Hale Yildiz"  # not alanında kendi adı: not sayılmaz
    vb = ("FAST Anlık Ödeme (28/10/2026 tarihli 1000000002 sorgu no'lu AYŞE YILMAZ hesabından Kuveyt Türk Katılım "
          "Bankası A.Ş. Deniz Ak hesabına giden FAST ödemesi)")
    assert bank_import.describe(vb, -765, [OWNER])["description"] == "Deniz Ak"  # '(28' değil
    havale = ("HAVALE - AYSE YILMAZ TARAFINDAN GÖNDERILEN,AYSE YILMAZ,TR990001200000000000000001,MERT DEMIR,"
              "TR55******************0000")
    assert bank_import.counterparty(havale, -60000, [OWNER]) == "Mert Demir"


def test_bill_descriptions():
    vf = ("Vodafone Tahsilatı Telefon Numarası:5000000001, Ad Soyad/Unvan: E******** K****, Fatura No: FD00000A000B01, "
          "Son Ödeme Tarihi:30.06.2026, Vodafone Tahsilatı")
    info = bank_import.describe(vf, -1200.0, [OWNER])
    assert info["description"] == "Vodafone · Telefon faturası" and info["bill_provider"] == "Vodafone"
    assert "Telefon 5000000001" in info["note"] and "Fatura no FD00000A000B01" in info["note"] and vf in info["note"]
    ig = ("İgdaş Tahsilatı Sözleşme Numarası: 500100000001, Ad Soyad /Unvan: ,Fatura Numarası:00000000001F,"
          "Son Ödeme Tarihi: 2024/10/25, İgdaş Tahsilatı")
    assert bank_import.bill_info(ig) == {"provider": "İGDAŞ", "kind": "Doğalgaz faturası", "ref": "Sözleşme no 500100000001",
                                         "invoice": "00000000001F", "due": "2024-10-25"}
    tt = "TTNET Tahsilatı 7000000001 Abone Nolu, Adı/Ünvanı:EL*** KA*** , Fatura No:2099000000001, Son Ödeme Tarihi:18.11.2024"
    assert bank_import.bill_info(tt)["provider"] == "Türk Telekom"
    assert bank_import.bill_info("Masraf Tahsilatı ORTAK ATM PARA ÇEK MASRAF") is None
    assert bank_import.bill_info("Vergi Tahsilatı 9264/SRCK Tahsilatı Dönem") is None
    assert bank_import.describe("Masraf Tahsilatı ORTAK ATM PARA ÇEK MASRAF", -30.0, [])["description"] == "Banka masrafı · ATM komisyonu"


def test_cancelled_payment_and_duplicate_fingerprint():
    out = "FAST Anlık Ödeme yemek parası (30/06/2026 tarihli 1000000003 sorgu no'lu AYŞE YILMAZ hesabından X Bankası A.Ş. Mert Demir hesabına giden FAST ödemesi)"
    back = out.replace("FAST Anlık Ödeme", "FAST Anlık Ödeme İptali") + " İptal Nedeni: uyumsuz"
    rows = bank_import.finalize([
        {"date": "2026-06-30", "description": out, "amount": -32000.0, "balance": 5000.00},
        {"date": "2026-06-30", "description": back, "amount": 32000.0, "balance": 37000.00},
        {"date": "2026-06-30", "description": out, "amount": -32000.0, "balance": 5000.00},  # aynı parmak izi
        {"date": "2026-06-30", "description": back, "amount": 32000.0, "balance": 37000.00},
        {"date": "2026-06-30", "description": "ATM QR Para Çek", "amount": -2000.0, "balance": 35000.00},
    ], [OWNER])
    assert [r["type"] for r in rows] == ["iptal"] * 4 + ["gider"]
    assert len({r["hash"] for r in rows}) == 5
    assert bank_import.counterparty(back, 40000, []) != "Lu Ayşe Yılmaz"


def test_vakifbank_operation_name_split_around_row():
    text = ("Hesap Hareketleri\nAYŞE YILMAZ\nTARİH SAAT İŞLEM NO MİKTAR BAKİYE İŞLEM ADI\n"
            "30.06.2026 11:18 2026000000000001 -1.200,00 20.000,00 Vodafone Tahsilatı\n"
            "Telefon Numarası:5000000001, Vodafone Tahsilatı\n"
            "Diğer Banka ATM-QR İle Para\n"
            "30.06.2026 15:51 2026000000000002 -3.000,00 17.000,00\n"
            "Çekme\n"
            "***Dekont yerine kullanılamaz. Uyuşmazlık halinde Banka kayıtları esas alınacaktır\n"
            "www.vakifbank.com.tr | 444 0 724\n"
            "30.06.2026 15:51 2026000000000003 -30,00 16.970,00 Masraf Tahsilatı\n")
    res = bank_pdf.parse_rule_based(text)
    assert [r["description"] for r in res["rows"]] == [
        "Vodafone Tahsilatı Telefon Numarası:5000000001, Vodafone Tahsilatı",
        "Diğer Banka ATM-QR İle Para Çekme", "Masraf Tahsilatı"]


def _gider(db):
    with db.connect() as c:
        return c.execute("SELECT COALESCE(SUM(amount), 0) FROM entries WHERE kind = 'gider'").fetchone()[0]


def test_live_order_without_owner_names(tmpdb):
    """Canlıdaki sıra: önce VakıfBank (gelen), sonra Halkbank (giden); hiçbir yerde hesap sahibi adı yok."""
    db = tmpdb
    vin = bank_import.finalize([{"date": "18.10.2026", "description": VAKIF_IN, "amount": 120000.0, "balance": 130000.00}], [])
    db.import_bank_rows(vin, account="VakıfBank", owners=[])
    hout = bank_import.finalize([{"date": "18-10-2026", "description": HALK_OUT, "amount": -120000.0, "balance": 200000.00},
                                 {"date": "18-10-2026", "description": "Kira,Hale Yildiz,Türkiye Is Bankasi A.S.,TR77000640000000000000001",
                                  "amount": -3000.0, "balance": 197000.00}], [])
    db.import_bank_rows(hout, account="Halkbank", owners=[])
    with db.connect() as c:
        kinds = [tuple(r) for r in c.execute("SELECT kind, amount, description, counterparty, payment FROM entries")]
        own = [r[0] for r in c.execute("SELECT amount FROM own_transfers ORDER BY amount")]
    assert kinds == [("gider", 3000.0, "Hale Yildiz · Kira", "Hale Yildiz", "havale")]
    assert own == [-120000.0, 120000.0]


def test_outgoing_elsewhere_is_not_a_counterpart(tmpdb):
    """Halkbank → VakıfBank 35.000 (virman) ve aynı gün VakıfBank'tan kiraya 35.000: kira gider kalır."""
    db = tmpdb
    db.import_bank_rows(bank_import.finalize([{"date": "18-06-2026", "description": HALK_OUT, "amount": -28500.0,
                                               "balance": 1000.0}], [OWNER]), account="Halkbank", owners=[OWNER])
    rent = ("FAST Anlık Ödeme kira (18/06/2026 tarihli 1000000004 sorgu no'lu AYŞE YILMAZ hesabından Türkiye İş Bankası A.Ş. "
            "Selin Ak hesabına giden FAST ödemesi)")
    db.import_bank_rows(bank_import.finalize([{"date": "18.06.2026", "description": rent, "amount": -28500.0,
                                               "balance": 500.0}], [OWNER]), account="VakıfBank", owners=[OWNER])
    assert _gider(db) == 28500


def test_repair_fixes_old_imports_without_documents(tmpdb):
    """Eski içe aktarma: gelen kendi transferi gelir + kişi 'Ayşe Yılmaz', giden açıklaması kesik, fatura ham metin.
    Onarım (dosya olmadan, nottaki ham metinle) virmana taşır, faturayı temizler; hash değişmez."""
    from app import bank_repair

    db = tmpdb
    db.remember_owner_name(OWNER)
    acc = db.create_entry  # kısaltma
    acc({"entry_date": "2026-10-18", "description": "Ayşe Yılmaz", "amount": 120000, "kind": "gelir", "category": "Diğer Gelir",
         "note": f"Hesap hareketi · {VAKIF_IN}", "import_hash": "h-in", "counterparty": "Ayşe Yılmaz"})
    trunc = "GIDEN FAST - AYSE YILMAZ TARAFI M20990101-120000-000007/01 Sayfa No:4 / 5"
    acc({"entry_date": "2026-10-18", "description": trunc, "amount": 120000, "kind": "gider", "category": "Kategorisiz",
         "note": "Hesap hareketi", "import_hash": "h-out", "payment": "havale"})
    vf = "Vodafone Tahsilatı Telefon Numarası:5000000001, Fatura No: FD00000A000B01, Son Ödeme Tarihi:30.06.2026"
    acc({"entry_date": "2026-06-30", "description": vf, "amount": 1415.75, "kind": "gider", "category": "Telefon",
         "note": "Hesap hareketi", "import_hash": "h-bill", "payment": "nakit"})
    acc({"entry_date": "2026-06-30", "description": "Elle yazdığım", "amount": 99, "kind": "gider", "category": "Telefon",
         "note": "Hesap hareketi · Vodafone Tahsilatı Telefon Numarası:5000000001", "import_hash": "h-edited"})
    with db.connect() as c:  # gelen tarafın hesabı VakıfBank, giden Halkbank
        c.execute("INSERT INTO accounts (name, kind) VALUES ('VakıfBank', 'banka'), ('Halkbank', 'banka')")
        c.execute("UPDATE entries SET account_id = (SELECT id FROM accounts WHERE name = 'VakıfBank') WHERE import_hash = 'h-in'")
        c.execute("UPDATE entries SET account_id = (SELECT id FROM accounts WHERE name = 'Halkbank') WHERE import_hash = 'h-out'")
    dry = bank_repair.repair(apply=False)
    assert len(dry["to_own_transfer"]) == 1 and _gider(db) > 120000  # kuru çalıştırma yazmaz
    rep = bank_repair.repair(apply=True)
    assert len(rep["to_own_transfer"]) == 1 and len(rep["paired"]) == 1
    with db.connect() as c:
        left = {r[0]: (r[1], r[2]) for r in c.execute("SELECT import_hash, description, bill_provider FROM entries")}
        own = {r[0] for r in c.execute("SELECT import_hash FROM own_transfers")}
    assert own == {"h-in", "h-out"}
    assert left == {"h-bill": ("Vodafone · Telefon faturası", "Vodafone"), "h-edited": ("Elle yazdığım", None)}
    again = bank_repair.repair(apply=True)
    assert not again["updated"] and not again["to_own_transfer"]  # ikinci çalıştırma bir şey değiştirmez


def test_transfers_month_filter_self_and_rename(client, tmpdb):
    db = tmpdb
    db.remember_owner_name("AY ŞE YILMAZ")
    for d, amt, kind, who in (("2026-09-05", 100, "gider", "Selin Ak"), ("2026-10-05", 250, "gider", "Selin Ak"),
                              ("2026-10-07", 40, "gelir", "Selin ak"), ("2026-10-09", 500, "gelir", "Ayşe Yılmaz"),
                              ("2026-10-10", 70, "gider", "Selin Akdeniz")):
        db.create_entry({"entry_date": d, "description": who, "amount": amt, "kind": kind, "category": "Diğer",
                         "counterparty": who})
    r = client.get("/api/transfers").json()
    assert "Ayşe Yılmaz" not in [p["name"] for p in r["people"]]  # kendisi kişi değil
    assert [i["amount"] for i in r["self"]] == [500]
    selin = next(p for p in r["people"] if p["name"] == "Selin Ak")
    assert selin["months"] == [{"month": "2026-10", "sent": 250, "received": 0, "count": 1},
                               {"month": "2026-09", "sent": 100, "received": 0, "count": 1}]
    r = client.get("/api/transfers?month=2026-10").json()
    assert {p["name"]: p["sent"] for p in r["people"]} == {"Selin Ak": 250, "Selin ak": 0, "Selin Akdeniz": 70}
    assert client.get("/api/transfers?start=2026-09-01&end=2026-09-30").json()["people"][0]["sent"] == 100
    assert client.get("/api/transfers?month=10-2026").status_code == 422
    out = client.patch("/api/transfers/people/Selin ak", json={"name": "Selin Ak"}).json()
    assert out["merged"] and out["entries"] == 1 and out["descriptions"] == 1
    people = {p["name"]: p for p in client.get("/api/transfers").json()["people"]}
    assert people["Selin Ak"]["count"] == 3 and "Selin ak" not in people


def test_bills_summary(client, tmpdb):
    db = tmpdb
    raw = "İgdaş Tahsilatı Sözleşme Numarası: 500100000001, Fatura Numarası:{inv},Son Ödeme Tarihi: 2026/{m}/25"
    rows = []
    for d, amt, inv in (("2025-10-20", 300.0, "FT000A1"), ("2026-09-20", 500.0, "FT000A2"), ("2026-10-20", 2636.0, "FT000A3"),
                        ("2026-10-20", 398.0, "FT000A4")):
        rows.append({"date": d, "description": raw.format(inv=inv, m=d[5:7]), "amount": -amt, "balance": None})
    db.import_bank_rows(bank_import.finalize(rows, [OWNER]), account="VakıfBank", owners=[OWNER])
    db.create_entry({"entry_date": "2026-10-03", "description": "Vodafone", "amount": 600, "kind": "gider",
                     "category": "Telefon"})
    r = client.get("/api/bills/summary?until=2026-10&months=13").json()
    assert r["window"] == {"start": "2025-10", "end": "2026-10"}
    ig = next(p for p in r["providers"] if p["name"] == "İGDAŞ")
    assert ig["category"] == "Doğalgaz" and ig["total"] == 3834 and ig["paid_months"] == 3
    last = ig["months"][-1]
    assert last["month"] == "2026-10" and last["amount"] == 3034 and last["count"] == 2
    assert sorted(i["invoice"] for i in last["items"]) == ["FT000A3", "FT000A4"]  # aynı ay iki fatura: toplanır, ayrı görünür
    assert ig["vs_prev_month"] == {"month": "2026-09", "amount": 500, "diff": 2534, "pct": 506.8}
    assert ig["vs_last_year"]["month"] == "2025-10" and ig["max"] == {"month": "2026-10", "amount": 3034}
    assert ig["average"] == 1278
    cats = {c["name"]: c for c in r["categories"]}
    assert cats["Doğalgaz"]["total"] == 3834 and cats["Telefon"]["total"] == 600
    assert client.get("/api/bills/summary?until=10-2026").status_code == 422


# ---------------------------------------------------------------- kuru çalıştırma bulguları (2026-10-30)

def test_own_name_only_in_message_is_not_self_transfer(tmpdb):
    """Kendi adı yalnız gönderenin mesajında: üçüncü kişiye ödeme, virman değil; mesaj açıklamada kalır."""
    raw = ("FAST Anlık Ödeme Ayşe Yılmaz kurs ücreti (10/03/2025 tarihli 1000000005 sorgu no'lu AYŞE YILMAZ "
           "hesabından Akbank T.A.Ş. Can Demir hesabına giden FAST ödemesi)")
    assert bank_import.classify(raw, -1350, [OWNER]) == "gider"
    assert bank_import.describe(raw, -1350, [OWNER])["description"] == "Can Demir · Ayşe Yılmaz kurs ücreti"
    rows = bank_import.finalize([{"date": "10.03.2025", "description": raw, "amount": -1200.0, "balance": 100.0}], [OWNER])
    tmpdb.import_bank_rows(rows, account="VakıfBank", owners=[OWNER])
    assert _gider(tmpdb) == 1200


def test_institution_money_is_income_not_virman():
    raw = "Gelen EFT ORNEK EMEKLILIK A.S. tarafından AYSE YILMAZ hesabına BES birikim KATILMA HESABI iade"
    assert bank_import.classify_reason(raw, 70000.00, [OWNER]) == ("gelir", "kurum/şirket")
    g = ("Gelen EFT Otomatik Yatan AB000XY0ZZ/ORNEK BANK A.Ş.-0000-00000-000000 sorgu numaralı ORNEK PAYMENT CORP. "
         "tarafından AYSE YILMAZ tarafına gelen EFT")
    assert bank_import.classify(g, 30.00, [OWNER]) == "gelir"
    assert bank_import.describe(g, 30.00, [OWNER])["description"] == "Ornek Payment"


def test_branch_cash_withdrawal_is_not_virman():
    raw = ("Mevduat Para Çek 445000000001 AYŞE YILMAZ ne ait TR12 0001 5001 0000 0000 0000 01 nolu mevduat hesabından "
           "çekilen. Adına işlem Yapan: AYŞE YILMAZ")
    assert bank_import.classify(raw, -30000, [OWNER]) == "gider"
    assert bank_import.describe(raw, -30000, [OWNER])["description"] == "Para çekme · şube"
    hb = "2026-02-15/6587850104650839/011145 23/PN 023869/ORTAK PR ÇKM DZ/ORTAK NOKTA BNK"
    assert bank_import.describe(hb, -500, [OWNER]) == {**bank_import.describe(hb, -500, [OWNER]),
                                                       "description": "ATM para çekme · ortak nokta", "payment": "nakit"}


def test_messages_survive_and_ibans_stay_out():
    vb = ("FAST Anlık Ödeme (01/09/2026 tarihli 1000000006 sorgu no'lu AYŞE YILMAZ hesabından Türkiye Halk Bankası A.Ş. "
          "Mert Demir hesabına giden FAST ödemesi)")
    assert bank_import.describe(vb, -10000, [OWNER])["description"] == "Mert Demir"  # '(01' değil
    hb = "Harçlık,AYSE YILMAZ,TR990001200000000000000001,MERT DEMIR,TR55******************0000"
    d = bank_import.describe(hb, -40000, [OWNER])
    assert d["description"] == "Mert Demir · Harçlık" and "TR99" not in d["description"] and hb in d["note"]
    hb2 = "Ayşe Yılmaz,Hale Yildiz,Türkiye Is Bankasi A.S.,TR77000640000000000000001"
    assert bank_import.describe(hb2, -3000, [OWNER])["description"] == "Hale Yildiz"  # mesaj kendi adı: atlanır


def test_own_transfer_label_keeps_message():
    out = ("HAVALE - AYSE YILMAZ TARAFINDAN GÖNDERILEN,AYSE YILMAZ,TR990001200000000000000001,AYSE YILMAZ,"
           "TR120001500000000000000001")
    assert bank_import.own_label(out, -60300, "Halkbank", [OWNER]) == "Kendi hesabına · Halkbank → VakıfBank"
    inc = "AYSE YILMAZ,AYSE YILMAZ 'DAN GELEN FAST ODEMESI , Türkiye Vakiflar Bankasi T.A.O.,TR12******************0001"
    assert bank_import.classify(inc, 29000, []) == "virman"
    assert bank_import.own_label(inc, 29000, "Halkbank", [OWNER]) == "Kendi hesabından · VakıfBank → Halkbank"
    vb = ("FAST Anlık Ödeme taksit (16/01/2025 tarihli 1 sorgu no'lu AYŞE YILMAZ hesabından Kuveyt Türk Katılım "
          "Bankası A.Ş. AYŞE YILMAZ hesabına giden FAST ödemesi)")
    assert bank_import.own_label(vb, -12550, "VakıfBank", [OWNER]) == "Kendi hesabına · VakıfBank → Kuveyt Türk · taksit"


def test_import_writes_clean_own_transfer_with_raw(tmpdb):
    rows = bank_import.finalize([{"date": "18.10.2026", "description": VAKIF_IN, "amount": 120000.0, "balance": 1.0}], [])
    tmpdb.import_bank_rows(rows, account="VakıfBank", owners=[])
    with tmpdb.connect() as c:
        r = c.execute("SELECT description, raw FROM own_transfers").fetchone()
    assert r["description"] == "Kendi hesabından · Halkbank → VakıfBank" and r["raw"] == VAKIF_IN


def test_own_transfer_edit_and_not_transfer(client, tmpdb):
    """PATCH /api/own-transfers/{id}: not/açıklama; 'virman değil' → gider kaydı, onarım ve eşleme geri taşımaz."""
    from app import bank_repair

    db = tmpdb
    db.remember_owner_name(OWNER)
    out = ("FAST Anlık Ödeme kira (18/06/2026 tarihli 1000000004 sorgu no'lu AYŞE YILMAZ hesabından Türkiye İş Bankası A.Ş. "
           "AYŞE YILMAZ hesabına giden FAST ödemesi)")
    db.import_bank_rows(bank_import.finalize([{"date": "18.06.2026", "description": out, "amount": -28500.0,
                                               "balance": 500.0}], [OWNER]), account="VakıfBank", owners=[OWNER])
    own = client.get("/api/transfers").json()["own"]
    assert len(own) == 1 and own[0]["description"] == "Kendi hesabına · VakıfBank → İş Bankası · kira"
    tid = own[0]["id"]
    r = client.patch(f"/api/own-transfers/{tid}", json={"note": "annemin hesabı", "description": "Annemin İş Bankası"}).json()
    assert r["own_transfer"]["note"] == "annemin hesabı" and r["own_transfer"]["description"] == "Annemin İş Bankası"
    r = client.patch(f"/api/own-transfers/{tid}", json={"not_transfer": True}).json()
    assert r["kind"] == "gider"
    with db.connect() as c:
        e = dict(c.execute("SELECT * FROM entries WHERE id = ?", (r["entry_id"],)).fetchone())
        assert c.execute("SELECT COUNT(*) FROM own_transfers").fetchone()[0] == 0
    assert e["amount"] == 28500 and e["not_virman"] == 1 and "annemin hesabı" in e["note"]
    # onarım ve tekrar yükleme virmana geri taşımaz, kopya açmaz
    assert not bank_repair.repair(apply=True)["to_own_transfer"]
    st = db.import_bank_rows(bank_import.finalize([{"date": "18.06.2026", "description": out, "amount": -28500.0,
                                                    "balance": 500.0}], [OWNER]), account="VakıfBank", owners=[OWNER])
    assert st["tekrar"] == 1 and _gider(db) == 28500
    assert client.patch("/api/own-transfers/999", json={"note": "x"}).status_code == 404


# --- kişi adının başındaki banka artığı (canlı bulgu; adlar uydurma) ---------------------------------------------------

import pytest  # noqa: E402


@pytest.mark.parametrize("bank", ["Türkiye Cumhuriyeti Ziraat Bankası A.Ş.", "Enpara Bank A.Ş.", "QNB Bank A.Ş.",
                                  "Kuveyt Türk Katılım Bankası A.Ş.", "Türkiye Finans Katılım Bankası A.Ş.",
                                  "Odeabank A.Ş.", "T.C. Ziraat Bankası", "Burgan Bank A.Ş."])
def test_counterparty_has_no_bank_residue(bank):
    from app.bank_import import counterparty

    d = f"FAST Anlık Ödeme kira (123456789 sorgu no'lu VakıfBank AYŞE DEMİR hesabından {bank} ALİ VELİ hesabına giden)"
    assert counterparty(d, -100.0, ["AYŞE DEMİR"]) == "Ali Veli"


def test_clean_person_name_keeps_real_names():
    from app.bank_import import clean_person_name

    assert clean_person_name("Bank Ayşe Örnek") == "Ayşe Örnek"
    assert clean_person_name("Cumhuriyeti Ali Veli Yılmaz") == "Ali Veli Yılmaz"
    assert clean_person_name("Deniz Yılmaz") == "Deniz Yılmaz"  # ad olabilecek kelime atılmaz
    assert clean_person_name("Ayşe Bankacı") == "Ayşe Bankacı"


def test_repair_merges_bank_prefixed_person(tmpdb):
    """Eski sürümün yazdığı 'Cumhuriyeti Ali Veli' mevcut 'Ali Veli' ile birleşir: kayıtlar, açıklama, borç, kategori."""
    from app import bank_repair

    with tmpdb.connect() as c:
        for cp, desc in (("Cumhuriyeti Ali Veli", "Cumhuriyeti Ali Veli · kira"), ("Ali Veli", "Ali Veli"),
                         ("Bank Zeynep Kaya", "Bank Zeynep Kaya")):
            c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, counterparty) "
                      "VALUES ('2026-10-05', '2026-10', ?, 100, 'gider', 'Diğer', ?)", (desc, cp))
        c.execute("INSERT INTO counterparties (name, category) VALUES ('Cumhuriyeti Ali Veli', 'Kira & Aidat')")
        c.execute("INSERT INTO debts (person, direction, asset, amount, debt_date) VALUES ('Bank Zeynep Kaya', 'alacak', "
                  "'TRY', 500, '2026-10-01')")
    rep = bank_repair.repair(apply=False)
    assert sorted((r["was"], r["now"], r["why"]) for r in rep["names"]) == [
        ("Bank Zeynep Kaya", "Zeynep Kaya", "ad düzelir"), ("Cumhuriyeti Ali Veli", "Ali Veli", "mevcut kişiyle birleşir")]
    with tmpdb.connect() as c:  # kuru çalıştırma yazmaz
        assert c.execute("SELECT COUNT(*) FROM entries WHERE counterparty LIKE 'Cumhuriyeti%'").fetchone()[0] == 1
    bank_repair.repair(apply=True)
    with tmpdb.connect() as c:
        assert sorted(r[0] for r in c.execute("SELECT DISTINCT counterparty FROM entries")) == ["Ali Veli", "Zeynep Kaya"]
        assert c.execute("SELECT description FROM entries WHERE description LIKE '%kira'").fetchone()[0] == "Ali Veli · kira"
        assert c.execute("SELECT category FROM counterparties WHERE name = 'Ali Veli'").fetchone()[0] == "Kira & Aidat"
        assert c.execute("SELECT person FROM debts").fetchone()[0] == "Zeynep Kaya"


def test_repair_names_without_document_and_in_dry_run(tmpdb, capsys):
    """Canlı bulgu: dosyası saklanmamış kayıt (bank_doc_id yok) ve kuru çalıştırma (kopya üzerinde uygulanır) 'banka
    artığı: 0 kişi' gösteriyordu; açıklama/kişi bölümü adı önce düzeltip birleşmeyi atlıyordu."""
    import os
    import sqlite3
    import tempfile

    from app import bank_repair, db

    note = ("Hesap hareketi · FAST Anlık Ödeme nakliye (12/12/2024 tarihli 123456789 sorgu no'lu VakıfBank AYŞE DEMİR "
            "hesabından Enpara Bank AYŞE ÖRNEK hesabına giden)")
    with tmpdb.connect() as c:
        c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, counterparty, note, "
                  "import_hash) VALUES ('2024-12-12', '2024-12', 'Bank Ayşe Örnek · nakliye', 500, 'gider', 'Diğer', "
                  "'Bank Ayşe Örnek', ?, 'h-bank-1')", (note,))
        c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, counterparty) "  # elle değişmiş
                  "VALUES ('2024-12-20', '2024-12', 'Taşınma ücreti', 300, 'gider', 'Diğer', 'Bank Ayşe Örnek')")
        c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category, counterparty) "
                  "VALUES ('2025-01-05', '2025-01', 'Ayşe Örnek', 200, 'gider', 'Diğer', 'Ayşe Örnek')")
    # main()'in kuru çalıştırması: kopya üzerinde uygula, raporla
    tmp = os.path.join(tempfile.mkdtemp(), "kopya.db")
    with sqlite3.connect(db.db_path()) as src, sqlite3.connect(tmp) as dst:
        src.backup(dst)
    with db.use_space(tmp, None):
        rep = bank_repair.repair(apply=True)
    assert [(r["was"], r["now"], r["amount"], r["why"]) for r in rep["names"]] == [
        ("Bank Ayşe Örnek", "Ayşe Örnek", 2, "mevcut kişiyle birleşir")]
    with tmpdb.connect() as c:  # canlı (asıl) defter değişmedi
        assert c.execute("SELECT COUNT(*) FROM entries WHERE counterparty = 'Bank Ayşe Örnek'").fetchone()[0] == 2
    bank_repair.repair(apply=True)
    with tmpdb.connect() as c:
        rows = sorted(tuple(r) for r in c.execute("SELECT counterparty, description FROM entries"))
    assert rows == [("Ayşe Örnek", "Ayşe Örnek"), ("Ayşe Örnek", "Ayşe Örnek · nakliye"), ("Ayşe Örnek", "Taşınma ücreti")]
