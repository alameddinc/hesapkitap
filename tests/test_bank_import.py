"""Banka hesap hareketleri içe aktarma (sahte veriler)."""
import io

from app import bank_import

CSV = """Hesap Hareketleri;;;;
Müşteri: AYŞE YILMAZ;;;;
Tarih;Açıklama;Tutar;Bakiye;Kanal
15.10.2026;ORNEK A.S. MAAS ODEMESI EKIM;85.000,00;95.000,00;EFT
16.10.2026;KREDI KARTI BORC ODEMESI 4543****1234;-12.500,00;82.500,00;MOBIL
17.10.2026;HESAPLAR ARASI VIRMAN;-5.000,00;77.500,00;MOBIL
18.10.2026;MIGROS ORNEK SUBE;-842,35;76.657,65;POS
19.10.2026;FAST PSIKOLOG AHMET ORNEK;-3.000,00;73.657,65;FAST
20.10.2026;KIRA ODEMESI EFT;-22.000,00;51.657,65;EFT
"""


def _upload(client, data: bytes, name: str, mapping=None, header_row=None):
    import json

    files = {"file": (name, data, "application/octet-stream")}
    form = {}
    if mapping is not None:
        form = {"mapping": json.dumps(mapping), "header_row": str(header_row)}
    return client.post("/api/bank-import" if mapping is not None else "/api/bank-import/preview", files=files, data=form)


def test_detect_and_parse_csv():
    rows = bank_import.read_rows(CSV.encode("utf-8"), "hareket.csv")
    det = bank_import.detect(rows)
    assert det["header_row"] == 2
    assert det["mapping"]["date"] == 0 and det["mapping"]["description"] == 1 and det["mapping"]["amount"] == 2
    parsed = bank_import.parse(rows, det["mapping"], det["header_row"])
    types = [p["type"] for p in parsed]
    assert types == ["gelir", "kart_odemesi", "virman", "gider", "gider", "gider"]
    assert parsed[0]["amount"] == 85000 and parsed[3]["amount"] == -842.35


def test_amount_formats():
    p = bank_import.parse_amount
    assert p("1.234,56") == 1234.56 and p("-1.234,56") == -1234.56 and p("1,234.56") == 1234.56
    assert p("12.500") == 12500 and p("842,35 TL") == 842.35 and p("1.000,00-") == -1000


def test_import_flow(client, tmpdb):
    # maaş sabit kalemi + daha önce elle girilmiş psikolog + ödenmemiş kart ekstresi
    client.post("/api/recurring", json={"description": "Maaş", "amount": 85000, "kind": "gelir", "category": "Maaş",
                                        "day_of_month": 15, "start_month": "2026-01"})
    client.post("/api/entries", json={"entry_date": "2026-10-19", "description": "Psikolog", "amount": 3000,
                                      "kind": "gider", "category": "Sağlık", "payment": "havale"})
    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                  "VALUES ('k', 'VakıfBank', '4543***1234', '2026-10-13', '2026-10-23', 12500)")

    pv = _upload(client, CSV.encode("cp1254"), "hareket.csv").json()  # Windows Türkçe kodlama
    assert pv["total"] == 6 and pv["counts"]["kart_odemesi"] == 1 and pv["counts"]["virman"] == 1

    r = _upload(client, CSV.encode("utf-8"), "hareket.csv", pv["mapping"], pv["header_row"]).json()
    assert r == {"gelir": 1, "gider": 2, "kart_odemesi": 1, "virman": 1, "tekrar": 0, "eslesen_elle": 1,
                 "odendi_isaretlenen": 1, "maas": 1}
    st = tmpdb.list_statements()[0]
    assert st["paid_amount"] == 12500 and st["paid_at"] == "2026-10-16"   # kart ödemesi → ekstre ödendi
    entries = tmpdb.list_entries()
    maas = next(e for e in entries if e["kind"] == "gelir")
    assert maas["description"] == "Maaş" and maas["recurring_id"] and maas["month"] == "2026-10"  # sabit kaleme bağlandı
    assert sum(1 for e in entries if e["description"] == "Psikolog") == 1   # elle girilen tekrar eklenmedi
    assert not any("VIRMAN" in e["description"] for e in entries)

    again = _upload(client, CSV.encode("utf-8"), "hareket.csv", pv["mapping"], pv["header_row"]).json()
    # ikinci yükleme hiçbir şey eklemez; kart ödemesi ve virman da "zaten var" sayılır
    assert again["tekrar"] == 6 and again["virman"] == 0 and again["kart_odemesi"] == 0 and again["gider"] == 0 and again["gelir"] == 0
    assert again["eslesen_elle"] == 0 and again["odendi_isaretlenen"] == 0
    # eşleme hatırlandı
    assert _upload(client, CSV.encode("utf-8"), "hareket.csv").json()["profile_saved"] is True


def test_xlsx_debit_credit_columns(client, tmpdb):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["ORNEK BANKASI HESAP DÖKÜMÜ"])
    ws.append([])
    ws.append(["İşlem Tarihi", "Açıklama", "Borç", "Alacak", "Bakiye"])
    ws.append(["14.10.2026", "ORNEK MARKET POS", "450,00", "", "10.000,00"])
    ws.append(["15.10.2026", "PRIM ODEMESI", "", "5.000,00", "15.000,00"])
    buf = io.BytesIO()
    wb.save(buf)
    pv = _upload(client, buf.getvalue(), "dokum.xlsx").json()
    assert pv["mapping"]["debit"] == 2 and pv["mapping"]["credit"] == 3 and pv["total"] == 2
    r = _upload(client, buf.getvalue(), "dokum.xlsx", pv["mapping"], pv["header_row"]).json()
    assert r["gider"] == 1 and r["gelir"] == 1
    kinds = sorted((e["kind"], e["amount"]) for e in tmpdb.list_entries())
    assert kinds == [("gelir", 5000.0), ("gider", 450.0)]


def test_salary_updates_manual_entry_with_actual_amount(client, tmpdb):
    """Maaş elle 225.000 girildi; bankada 224.987,40 yattı → yeni kayıt değil, mevcut güncellenir."""
    rid = client.post("/api/recurring", json={"description": "Maaş", "amount": 220000, "kind": "gelir", "category": "Maaş",
                                              "day_of_month": 15, "start_month": "2026-01"}).json()["id"]
    client.post("/api/entries", json={"entry_date": "2026-09-15", "description": "Maaş", "amount": 225000, "kind": "gelir"})
    assert tmpdb.list_entries()[0]["recurring_id"] == rid
    csv = "Tarih;Açıklama;Tutar\n14.09.2026;ORNEK AS MAAS ODEMESI;224.987,40\n".encode()
    pv = _upload(client, csv, "h.csv").json()
    r = _upload(client, csv, "h.csv", pv["mapping"], pv["header_row"]).json()
    assert r["eslesen_elle"] == 1 and r["gelir"] == 0
    e = tmpdb.list_entries()
    assert len(e) == 1 and e[0]["amount"] == 224987.40 and e[0]["entry_date"] == "2026-09-14" and e[0]["month"] == "2026-09"
    assert client.get("/api/summary", params={"month": "2026-09"}).json()["totals"]["income"] == 224987.40


def test_card_payment_detected_by_amount(client, tmpdb):
    """Başka bankanın kartına EFT: açıklamada 'kart' yok; tutar ekstreyle birebir → gider sayılmaz."""
    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt, min_payment) "
                  "VALUES ('k', 'Kuveyt Türk', '4111***1111', '2026-10-15', '2026-10-26', 73791.01, 29516.40)")
    csv = ("Tarih;Açıklama;Tutar\n"
           "20.10.2026;EFT KUVEYT TURK KATILIM BANKASI;-73.791,01\n"
           "21.10.2026;EFT AHMET ORNEK KIRA;-22.000,00\n").encode()
    pv = _upload(client, csv, "h.csv").json()
    assert pv["counts"] == {"kart_odemesi": 1, "gider": 1}
    r = _upload(client, csv, "h.csv", pv["mapping"], pv["header_row"]).json()
    assert r["kart_odemesi"] == 1 and r["gider"] == 1 and r["odendi_isaretlenen"] == 1
    assert tmpdb.list_statements()[0]["paid_at"] == "2026-10-20"
    assert [e["description"] for e in tmpdb.list_entries()] == ["EFT AHMET ORNEK KIRA"]  # sadece kira gider


def test_self_transfer_detection():
    owners = ["AYŞE YILMAZ"]
    c = bank_import.classify
    # Kuveyt Türk: boşluksuz
    assert c("GİDENFAST-AYŞEYILMAZTARAFINDANGÖNDERİLEN-Gönderen:AYŞEYILMAZ,Alıcı:AyşeYılmaz,FASTParaTransferi", 55000, owners) == "virman"
    assert c("Gönderen:AYŞEYILMAZ,Alıcı:AYŞEYILMAZ,FASTParaTransferi", 2000, []) == "virman"  # isim bilinmese de
    # VakıfBank: kendi Kuveyt Türk hesabına giden FAST
    assert c("FAST (24/08/2026 tarihli 1 sorgu no'lu AYŞE YILMAZ hesabından Kuveyt Türk Katılım Bankası A.Ş. "
             "AYŞE YILMAZ hesabına giden FAST ödemesi)", -2000, owners) == "virman"
    # başkasına giden kira: virman değil
    assert c("ev kirası (… AYŞE YILMAZ hesabından Türkiye İş Bankası A.Ş. Ayşe Örnek hesabına giden FAST ödemesi)",
             -35000, owners) == "gider"
    # boşluksuz kart borcu ödemesi
    assert c("KrediKartıNo:9792********9106,KrediKartıBorçÖdemesi", -1362.24, owners) == "kart_odemesi"


def test_counterparty_extraction():
    owners = ["AYŞE YILMAZ"]
    cp = bank_import.counterparty
    assert cp("FAST Anlık Ödeme ev kirası (19/07/2026 tarihli 1 sorgu no'lu AYŞE YILMAZ hesabından "
              "Türkiye İş Bankası A.Ş. Ayten Örnek hesabına giden FAST ödemesi)", -35000, owners) == "Ayten Örnek"
    assert cp("Gelen FAST Anlık Ödeme GİDEN FAST - FATMA ÖRNEK TARAFINDAN GÖNDERİLEN (22.09.2026 …)", 800, owners) == "Fatma Örnek"
    assert cp("Gönderilen havale TR68 0001 5001 00** **** **00 00 nolu AYŞE YILMAZ hesabından TR40 0001 5001 0000 0000 0000 00 "
              "nolu ZEYNEP ÖRNEK hesabına havale", -8000, owners) == "Zeynep Örnek"
    assert cp("Alınan havale TR04 0001 5001 00** **** **00 00 nolu ALİ ÖRNEK hesabından TR68 … nolu AYŞE YILMAZ hesabına",
              1400, owners) == "Ali Örnek"
    # kendisi → None
    assert cp("GİDEN FAST - AYŞE YILMAZ TARAFINDAN GÖNDERİLEN", 5000, owners) is None
    assert bank_import.transfer_note("FAST Anlık Ödeme ev kirası (19/07/2026 …)") == "ev kirası"


def test_transfers_panel(client, tmpdb):
    import json
    rows = [
        {"date": "2026-09-24", "description": "Gönderilen havale ali / TR68 0001 nolu AYŞE YILMAZ hesabından TR04 0001 nolu "
         "ALİ ÖRNEK hesabına havale", "amount": -200.0, "type": "gider", "hash": "h1"},
        {"date": "2026-09-24", "description": "Alınan havale TR04 nolu ALİ ÖRNEK hesabından TR68 nolu AYŞE YILMAZ hesabına",
         "amount": 1400.0, "type": "gelir", "hash": "h2"},
        {"date": "2026-09-01", "description": "GİDEN FAST - AYŞE YILMAZ TARAFINDAN GÖNDERİLEN", "amount": 10000.0,
         "type": "virman", "hash": "h3"},
    ]
    tmpdb.import_bank_rows(rows, account="Örnek Bank", owners=["AYŞE YILMAZ"])
    t = client.get("/api/transfers").json()
    ali = next(p for p in t["people"] if p["name"] == "Ali Örnek")
    assert (ali["sent"], ali["received"], ali["net"]) == (200, 1400, 1200)
    assert len(t["own"]) == 1 and t["own"][0]["amount"] == 10000
    assert client.put("/api/transfers/people/Ali Örnek", json={"category": "Aile", "track_debt": True}).json()["updated"] == 2
    assert all(e["category"] == "Aile" for e in tmpdb.list_entries())


def test_pdf_hash_ignores_description_wording():
    a = bank_import.finalize([{"date": "2026-07-22", "description": "Gönderilen havale kısa", "amount": -8000.0,
                               "balance": 18231.19}], [])
    b = bank_import.finalize([{"date": "2026-07-22", "description": "Gönderilen havale TR68 … uzun hali", "amount": -8000.0,
                               "balance": 18231.19}], [])
    assert a[0]["hash"] == b[0]["hash"]


def test_counterparty_halkbank_layout():
    owners = ["AYŞE YILMAZ"]
    cp = bank_import.counterparty
    assert cp("GIDEN FAST - AYŞE YILMAZ TARAFINDAN GÖNDERILEN,Zeynep Örnek,Türkiye Is Bankasi A.S.,TR00", -8000, owners) \
        == "Zeynep Örnek"
    assert cp("Kira,Ali Örnek,Kuveyt Türk Katilim Bankasi A.S.,TR00", -6500, owners) == "Ali Örnek"
    # kendi hesabına → None (virman)
    assert cp("GIDEN FAST - AYŞE YILMAZ TARAFINDAN GÖNDERILEN,Ayşe Yılmaz,Türkiye Vakiflar Bankasi T.A.O.,TR00", -5, owners) is None


def test_counterparty_company_and_casing():
    cp = bank_import.counterparty
    assert cp("GIDEN FAST - AYŞE YILMAZ TARAFINDAN GÖNDERILEN,Zeynep Örnek Egitim ve Danismanlik Limited Sirketi,"
              "Kuveyt Türk Katilim Bankasi A.S.,TR00", -2000, ["AYŞE YILMAZ"]) == "Zeynep Örnek Egitim"
    assert cp("Not,Esra ÖRNEK,Akbank T.A.S.,TR00", -4850, ["AYŞE YILMAZ"]) == "Esra Örnek"
    assert cp("GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,Elif Demir,Türkiye Is Bankasi A.S.,TR00", -8000,
              ["AYŞE DEMIR"]) == "Elif Demir"


def test_counterparty_ignores_bill_commas_and_fixes_dotless_i():
    cp = bank_import.counterparty
    assert cp("Enerjisa Tahsilatı Sözleşme No:1, Ad Soyad:, Son Ödeme Tarihi:01.10.2026, Bankası", -475, []) is None
    assert cp("Not,Elif DEMIR,Türkiye Is Bankasi A.S.,TR00", -5000, ["AYŞE DEMIR"]) == "Elif Demir"


def test_card_payment_without_statement_warns(client, tmpdb):
    from app import finance

    rows = [{"date": "2026-08-15", "description": "KrediKartıNo:1111********9999,KrediKartıBorçÖdemesi",
             "amount": -65000.0, "type": "kart_odemesi", "hash": "kp1"}]
    tmpdb.import_bank_rows(rows, account="Örnek Katılım Bankası A.Ş.")
    assert [p["card_last4"] for p in tmpdb.card_payments_without_statement()] == ["9999"]
    titles = [i["title"] for i in finance.insights("2026-08")]
    assert any("…9999" in t and "ekstresi yüklenmemiş" in t for t in titles)
    # ekstre yüklenince uyarı kalkar
    with tmpdb.connect() as c:
        c.execute("INSERT INTO statements (file_hash, bank, card_no, period_end, due_date, total_debt) "
                  "VALUES ('x', 'Örnek', '1111***9999', '2026-08-05', '2026-08-15', 65000)")
    assert tmpdb.card_payments_without_statement() == []
    # aynı döküm tekrar yüklenirse kart ödemesi "zaten var" sayılır
    assert tmpdb.import_bank_rows(rows)["tekrar"] == 1


def test_same_amount_transfers_match_right_person(client, tmpdb):
    # 5 kişiye aynı gün 100'er TL; elle girilenler banka satırlarıyla kişi kişi eşleşmeli
    names = ["Ali", "Veli", "Zeynep", "Fatma", "Can"]
    for n in names:
        client.post("/api/entries", json={"entry_date": "2026-11-03", "description": f"{n}'e borç", "amount": 100,
                                          "kind": "gider", "category": "Borç / Alacak", "payment": "havale"})
    rows = [{"date": "2026-11-03", "description": f"GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,{n} Örnek,"
             f"Türkiye Is Bankasi A.S.,TR00", "amount": -100.0, "type": "gider", "hash": f"x{n}"}
            for n in reversed(names)]
    st = tmpdb.import_bank_rows(rows, owners=["AYŞE DEMIR"])
    assert st["eslesen_elle"] == 5 and st["gider"] == 0
    for e in tmpdb.list_entries():
        assert e["counterparty"] == e["description"].split("'")[0] + " Örnek"


def test_manual_entry_for_other_person_not_stolen(client, tmpdb):
    client.post("/api/entries", json={"entry_date": "2026-11-03", "description": "Veli'ye", "amount": 100,
                                      "kind": "gider", "category": "Borç / Alacak", "payment": "havale"})
    rows = [{"date": "2026-11-03", "description": "GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,Ali Örnek,X Bankasi A.S.,TR00",
             "amount": -100.0, "type": "gider", "hash": "a"},
            {"date": "2026-11-03", "description": "GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,Veli Örnek,X Bankasi A.S.,TR00",
             "amount": -100.0, "type": "gider", "hash": "v"}]
    st = tmpdb.import_bank_rows(rows, owners=["AYŞE DEMIR"])
    assert st["eslesen_elle"] == 1 and st["gider"] == 1
    veli = next(e for e in tmpdb.list_entries() if e["description"] == "Veli'ye")
    assert veli["counterparty"] == "Veli Örnek"


def test_debt_ledger_settles_by_transfer_and_assets(client, tmpdb):
    from app import finance, jobs
    from datetime import date

    # nakit borç aldım (TL), altın borç verdim
    d1 = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "borc", "amount": 200,
                                         "debt_date": "2026-10-10", "due_date": "2026-10-25"}).json()["id"]
    client.post("/api/debts", json={"person": "Veli", "direction": "alacak", "asset": "CEYREK_ALTIN", "amount": 2,
                                    "debt_date": "2026-10-01", "due_date": "2026-10-20"})
    assert any(p["type"] == "debt" and p["id"] == d1 for p in finance.payables(date(2026, 10, 20)))
    t = client.get("/api/transfers").json()
    assert next(p for p in t["people"] if p["name"] == "Veli")["open"] == {"CEYREK_ALTIN": 2}
    msgs = jobs.reminders(date(2026, 10, 20))
    assert any("Veli" in m and "2 çeyrek altın" in m for m in msgs)
    # Ali'ye havale → borç kapanır; aynı satır tekrar gelse ikinci kez kapatmaz
    st = tmpdb.import_bank_rows([{"date": "2026-10-20", "amount": -200.0, "type": "gider", "hash": "h",
                                  "description": "GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,Ali Örnek,X Bankasi A.S.,TR00"}],
                                owners=["AYŞE DEMIR"])
    assert st["borc_hareketi"] == 1
    assert next(d for d in tmpdb.list_debts() if d["id"] == d1)["settled_at"] == "2026-10-20"
    # kısmi tahsilat (altın)
    vid = next(d["id"] for d in tmpdb.list_debts() if d["person"] == "Veli")
    client.post(f"/api/debts/{vid}/pay", json={"amount": 1, "paid_at": "2026-10-21"})
    v = next(d for d in tmpdb.list_debts() if d["id"] == vid)
    assert v["remaining"] == 1 and v["settled_at"] is None


def test_manual_entry_with_person_settles_debt(client, tmpdb):
    client.post("/api/debts", json={"person": "Ali Örnek", "direction": "alacak", "amount": 500, "debt_date": "2026-10-01"})
    r = client.post("/api/entries", json={"entry_date": "2026-10-05", "description": "Ali ödedi", "amount": 500,
                                          "kind": "gelir", "category": "Borç / Alacak", "counterparty": "ali örnek"}).json()
    assert r["debt_settled"]
    e = next(x for x in tmpdb.list_entries() if x["id"] == r["id"])
    assert e["counterparty"] == "Ali Örnek"  # mevcut yazılışa oturur


def test_debt_movements_not_income_or_expense(client, tmpdb):
    from app import finance

    # Ali'ye 600 TL nakit borç verdim (Ekim), Kasım'da elden geri aldım
    did = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "alacak", "amount": 600,
                                          "debt_date": "2026-10-10"}).json()["id"]
    oct_ = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]
    assert oct_["expense"] == 0 and oct_["debt_flow"] == -600 and oct_["cash_net"] == -600
    assert oct_["receivable"] == 600
    rows = finance.transactions("2026-10")
    assert any(r["kind"] == "borc" and r["flow"] == -1 and "borç verildi" in r["description"] for r in rows)
    client.post(f"/api/debts/{did}/pay", json={"paid_at": "2026-11-05"})
    nov = client.get("/api/summary", params={"month": "2026-11"}).json()["totals"]
    assert nov["income"] == 0 and nov["debt_flow"] == 600 and nov["receivable"] == 0


def test_bank_transfers_linked_to_debt_are_not_expense(client, tmpdb):
    # havaleyle borç verdim, havaleyle geri geldi: ikisi de dökümde; ikisi de gelir/gider sayılmaz
    client.post("/api/debts", json={"person": "Ali Örnek", "direction": "alacak", "amount": 600, "debt_date": "2026-10-10"})
    fast = "GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,Ali Örnek,X Bankasi A.S.,TR00"
    st = tmpdb.import_bank_rows([
        {"date": "2026-10-11", "description": fast, "amount": -600.0, "type": "gider", "hash": "out"},
        {"date": "2026-11-02", "description": "Gelen FAST GİDEN FAST - ALİ ÖRNEK TARAFINDAN GÖNDERİLEN", "amount": 600.0,
         "type": "gelir", "hash": "in"}], owners=["AYŞE DEMIR"])
    assert st["borc_hareketi"] == 2
    d = tmpdb.list_debts()[0]
    assert d["given_entry_id"] and d["settled_at"] == "2026-11-02"
    for m in ("2026-10", "2026-11"):
        t = client.get("/api/summary", params={"month": m}).json()["totals"]
        assert t["income"] == 0 and t["expense"] == 0
    # transfer geçmişinde yine görünür
    ali = next(p for p in client.get("/api/transfers").json()["people"] if p["name"] == "Ali Örnek")
    assert ali["sent"] == 600 and ali["received"] == 600


def test_debt_created_after_import_links_existing_transfer(client, tmpdb):
    fast = "GIDEN FAST - AYŞE DEMIR TARAFINDAN GÖNDERILEN,Ali Örnek,X Bankasi A.S.,TR00"
    tmpdb.import_bank_rows([{"date": "2026-10-11", "description": fast, "amount": -600.0, "type": "gider", "hash": "o"}],
                           owners=["AYŞE DEMIR"])
    assert client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]["expense"] == 600
    client.post("/api/debts", json={"person": "Ali Örnek", "direction": "alacak", "amount": 600, "debt_date": "2026-10-10"})
    t = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]
    assert t["expense"] == 0 and t["debt_flow"] == -600


def test_write_off_turns_remaining_into_expense(client, tmpdb):
    did = client.post("/api/debts", json={"person": "Ali Örnek", "direction": "alacak", "amount": 600,
                                          "debt_date": "2026-10-01"}).json()["id"]
    client.post(f"/api/debts/{did}/pay", json={"amount": 200, "paid_at": "2026-10-05"})
    client.post(f"/api/debts/{did}/write-off", json={"on": "2026-10-10"})
    t = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]
    # 600 çıktı, 200 geldi, 400 tahsil edilemedi → gider 400; eldeki para toplamda −400
    assert t["expense"] == 400 and t["cash_net"] == -400 and t["receivable"] == 0
    client.delete(f"/api/debts/{did}/pay")  # geri al
    t = client.get("/api/summary", params={"month": "2026-10"}).json()["totals"]
    assert t["expense"] == 0 and t["receivable"] == 600
