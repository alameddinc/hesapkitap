"""Hesap dökümü içe aktarma doğruluğu (denetim M2, Ek A F3.2 / F3.3 / F3.3c / F3.6). Deney A, B, C kalıcı test.
Kişi adları, tutarlar ve bankalar uydurmadır."""
import io

from app import bank_import


def _parse(rows):
    det = bank_import.detect(rows)
    return bank_import.parse(rows, det["mapping"], det["header_row"], [])


def _sum(tmpdb, where="1=1"):
    with tmpdb.connect() as c:
        return tuple(c.execute(f"SELECT COUNT(*), ROUND(SUM(amount), 2) FROM entries WHERE {where}").fetchone())


# --- (a) eş satırlar ---------------------------------------------------------------------------------------------

ROWS_A = [["Tarih", "Açıklama", "Tutar"],
          ["01.10.2026", "ORNEK KAFE", "-85,00"],
          ["01.10.2026", "ORNEK KAFE", "-85,00"],
          ["02.10.2026", "MARKET", "-200,00"]]


def test_a_same_day_same_amount_rows_not_lost(tmpdb):
    """Deney A: bakiyesiz CSV'de iki eş kahve. Önce 285 TL kaydediliyordu, olması gereken 370 TL."""
    parsed = _parse(ROWS_A)
    assert len({p["hash"] for p in parsed}) == 3
    st = tmpdb.import_bank_rows(parsed, account="TestBank")
    assert st["gider"] == 3 and st["tekrar"] == 0
    assert _sum(tmpdb) == (3, 370.0)
    # aynı dosya ikinci kez: hepsi tekrar
    assert tmpdb.import_bank_rows(_parse(ROWS_A), account="TestBank")["tekrar"] == 3
    assert _sum(tmpdb) == (3, 370.0)


def test_a_first_hash_unchanged():
    """Geriye uyum: ilk satırın parmak izi eskisiyle aynı (önceden yüklenmiş kayıtlar yeniden yüklemede kopyalanmaz)."""
    from datetime import date

    first = _parse(ROWS_A)[0]
    assert first["hash"] == bank_import.row_hash(date(2026, 10, 1), "ORNEK KAFE", -85.0, "")


# --- (b) PDF ↔ Excel aynı hareket ----------------------------------------------------------------------------------

XLS_B = [["Tarih", "Açıklama", "Tutar", "Bakiye"], ["05.10.2026", "FAST GIDEN ALI VELI KIRA", "-15.000,00", "5.000,00"]]


def test_b_pdf_then_excel_counted_once(tmpdb):
    """Deney B: aynı kira önce PDF, sonra Excel. Önce 2 kayıt / 30.000 TL, olması gereken 15.000."""
    pdf = bank_import.finalize([{"date": "05.10.2026", "description": "FAST GIDEN ALI VELI KIRA", "amount": -15000.0,
                                 "balance": 5000.0}], [])
    xls = _parse(XLS_B)
    assert pdf[0]["hash"] == xls[0]["hash"]  # ortak parmak izi
    acc = tmpdb.ensure_account("Halkbank")
    tmpdb.import_bank_rows(pdf, account="Halkbank", account_id=acc)
    st = tmpdb.import_bank_rows(xls, account="Halkbank", account_id=acc)
    assert st["tekrar"] == 1
    assert _sum(tmpdb, "entry_date = '2026-10-05'") == (1, 15000.0)


def test_b_old_excel_hash_is_still_duplicate(tmpdb):
    """Eski biçim parmak iziyle (açıklama + ham bakiye metni) yüklenmiş Excel tekrar yüklenince 'tekrar' sayılır."""
    acc = tmpdb.ensure_account("Halkbank")
    old = [{**r, "hash": r["alt_hash"]} for r in _parse(XLS_B)]  # eski kodun yazdığı iz
    for r in old:
        r.pop("alt_hash")
    tmpdb.import_bank_rows(old, account="Halkbank", account_id=acc)
    st = tmpdb.import_bank_rows(_parse(XLS_B), account="Halkbank", account_id=acc)
    assert st["tekrar"] == 1 and "cakisma" not in st
    assert _sum(tmpdb) == (1, 15000.0)


def test_b_overlap_with_different_fingerprint(tmpdb):
    """Parmak izi tutmayan (bakiyesiz PDF, farklı açıklama) aynı hareket: çakışma sayılır, eklenmez, uyarı döner."""
    acc = tmpdb.ensure_account("Halkbank")
    tmpdb.import_bank_rows(_parse(XLS_B), account="Halkbank", account_id=acc)
    pdf = bank_import.finalize([{"date": "05.10.2026", "description": "KIRA ODEMESI", "amount": -15000.0, "balance": None},
                                {"date": "06.10.2026", "description": "ORNEK MARKET", "amount": -120.0, "balance": None}], [])
    st = tmpdb.import_bank_rows(pdf, account="Halkbank", account_id=acc)
    assert st["cakisma"] == 1 and st["gider"] == 1
    assert "05.10.2026" in st["uyari"] and "zaten yüklü" in st["uyari"]
    assert _sum(tmpdb) == (2, 15120.0)


def test_b_overlap_keeps_real_twins(tmpdb):
    """Belgede o gün o tutarda iki hareket, kayıtlıda biri: biri tanınır, ikincisi eklenir (çakışma değil)."""
    acc = tmpdb.ensure_account("Halkbank")
    rows = bank_import.finalize([{"date": "01.10.2026", "description": "ORNEK KAFE", "amount": -85.0, "balance": None}], [])
    tmpdb.import_bank_rows(rows, account="Halkbank", account_id=acc)
    both = bank_import.finalize([{"date": "01.10.2026", "description": "ORNEK KAFE", "amount": -85.0, "balance": None},
                                 {"date": "01.10.2026", "description": "ORNEK KAFE", "amount": -85.0, "balance": None}], [])
    st = tmpdb.import_bank_rows(both, account="Halkbank", account_id=acc)
    assert st["tekrar"] == 1 and st["gider"] == 1 and "cakisma" not in st


def test_b_preview_shows_overlap(client, tmpdb):
    import json

    acc = tmpdb.ensure_account("Halkbank")
    tmpdb.import_bank_rows(_parse(XLS_B), account="Halkbank", account_id=acc)
    csv = "Tarih;Açıklama;Tutar\n05.10.2026;KIRA;-15.000,00\n07.10.2026;ORNEK MARKET;-50,00\n"
    pv = client.post("/api/bank-import/preview", files={"file": ("h.csv", csv.encode(), "text/csv")}).json()
    assert pv["overlap"] is None  # hesap belli değil (IBAN yok): önizleme hesabı bilemez
    r = client.post("/api/bank-import", files={"file": ("h.csv", csv.encode(), "text/csv")},
                    data={"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]), "account_id": str(acc)})
    assert r.json()["cakisma"] == 1 and r.json()["gider"] == 1


# --- (c) yeniden eskiye sıralı Excel -------------------------------------------------------------------------------

ROWS_C = [["Tarih", "Açıklama", "Tutar", "Bakiye"],
          ["20.10.2026", "AKSAM MARKET", "-300,00", "9.200,00"],  # günün SON hareketi (en üstte)
          ["20.10.2026", "OGLE YEMEK", "-200,00", "9.500,00"],
          ["20.10.2026", "SABAH KAFE", "-300,00", "9.700,00"],    # günün İLK hareketi
          ["19.10.2026", "MAAS", "10.000,00", "10.000,00"]]


def test_c_newest_first_anchor(tmpdb):
    """Deney C: çapa 9.700 kaydediliyordu, olması gereken 9.200."""
    parsed = _parse(ROWS_C)
    assert bank_import.newest_first(parsed) and bank_import.last_balance(parsed) == ("2026-10-20", 9200.0)
    acc = tmpdb.ensure_account("Garanti")
    tmpdb.import_bank_rows(parsed, account="Garanti", account_id=acc)
    a = next(x for x in tmpdb.list_accounts() if x["id"] == acc)
    assert a["balance"] == 9200.0 and a["current"] == 9200.0


def test_c_oldest_first_unchanged():
    parsed = _parse([ROWS_C[0], *ROWS_C[:0:-1]])  # eskiden yeniye
    assert not bank_import.newest_first(parsed) and bank_import.last_balance(parsed) == ("2026-10-20", 9200.0)


# --- (d) e-postadan gelen kart ödemesi ve virman -------------------------------------------------------------------

def _anchored(tmpdb):
    acc = tmpdb.ensure_account("Halkbank")
    rows = _parse([["Tarih", "Açıklama", "Tutar", "Bakiye"], ["10.10.2026", "MAAS", "20.000,00", "20.000,00"]])
    tmpdb.import_bank_rows(rows, account="Halkbank", account_id=acc)
    return acc


def _current(tmpdb, acc):
    return next(x for x in tmpdb.list_accounts() if x["id"] == acc)["current"]


def test_d_mail_card_payment_lowers_balance(tmpdb):
    acc = _anchored(tmpdb)
    assert _current(tmpdb, acc) == 20000.0
    tmpdb.import_bank_rows([{"date": "2026-10-12", "description": "Kredi karti odemesi", "amount": -3000.0,
                             "type": "kart_odemesi", "hash": "mail:test-kart-1"}], account="Halkbank", account_id=acc)
    assert _current(tmpdb, acc) == 17000.0  # önce 20.000 kalıyordu


def test_d_mail_transfer_changes_balance(tmpdb):
    acc = _anchored(tmpdb)
    tmpdb.import_bank_rows([{"date": "2026-10-13", "description": "Kendi hesabina virman", "amount": -2500.0,
                             "type": "virman", "hash": "mail:test-virman-1"}], account="Halkbank", account_id=acc)
    assert _current(tmpdb, acc) == 17500.0
    # döküm gelince bildirim kaydı silinir, hareket dökümle (çapanın içinde) gelir: iki kez düşülmez
    doc = _parse([["Tarih", "Açıklama", "Tutar", "Bakiye"],
                  ["10.10.2026", "MAAS", "20.000,00", "20.000,00"],
                  ["13.10.2026", "HESAPLAR ARASI VIRMAN", "-2.500,00", "17.500,00"]])
    tmpdb.import_bank_rows(doc, account="Halkbank", account_id=acc)
    assert _current(tmpdb, acc) == 17500.0


# --- bank_repair: kuru çalıştırma Excel/CSV dökümlerini okur --------------------------------------------------------

def _xlsx(rows) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _old_import(tmpdb, rows, acc, account="Halkbank", lose_twins=False):
    """Eski kodun yaptığı içe aktarma: eski biçim parmak izi; eş satırlar tek parmak izine iner (kaybolur)."""
    parsed = _parse(rows)
    for r in parsed:
        r["hash"] = r.pop("alt_hash", r["hash"])
    if lose_twins:
        from datetime import date as _d

        for r in parsed:
            r["hash"] = bank_import.row_hash(_d.fromisoformat(r["date"]), r["description"], r["amount"], "")
    tmpdb.import_bank_rows(parsed, account=account, account_id=acc, check_overlap=False)


def test_repair_dry_run_reads_excel_and_finds_lost_twin(tmpdb):
    from app import bank_repair

    acc = tmpdb.ensure_account("Halkbank")
    _old_import(tmpdb, ROWS_A, acc, lose_twins=True)
    assert _sum(tmpdb) == (2, 285.0)
    tmpdb.save_bank_document(_xlsx(ROWS_A), "hareketler.xlsx", "Halkbank", account_id=acc)
    rep = bank_repair.repair(apply=False)
    assert rep["docs_read"] == ["hareketler.xlsx"]
    assert [(m["date"], m["amount"]) for m in rep["missing"]] == [("2026-10-01", -85.0)]
    assert _sum(tmpdb) == (2, 285.0)  # kuru çalıştırma yazmaz
    bank_repair.repair(apply=True)
    assert _sum(tmpdb) == (3, 370.0)


def test_repair_finds_pdf_excel_double_import(tmpdb):
    """Aynı dönem iki biçimde (CSV ve XLSX, bakiye yazımı farklı) eski kodla iki kez yüklenmiş: fazlası listelenir."""
    from app import bank_repair

    acc = tmpdb.ensure_account("Halkbank")
    xlsx_rows = [XLS_B[0], ["05.10.2026", "FAST GIDEN ALI VELI KIRA", -15000, 5000]]
    _old_import(tmpdb, XLS_B, acc)
    tmpdb.save_bank_document("\n".join(";".join(map(str, r)) for r in XLS_B).encode(), "a.csv", "Halkbank", account_id=acc)
    _old_import(tmpdb, bank_import.read_rows(_xlsx(xlsx_rows), "b.xlsx"), acc)  # dosyadan okunduğu gibi (5000)
    tmpdb.save_bank_document(_xlsx(xlsx_rows), "b.xlsx", "Halkbank", account_id=acc)
    assert _sum(tmpdb) == (2, 30000.0)
    rep = bank_repair.repair(apply=False)
    assert [(d["date"], d["amount"]) for d in rep["duplicates"]] == [("2026-10-05", -15000.0)]
    assert rep["missing"] == []
    bank_repair.repair(apply=True)
    assert _sum(tmpdb) == (1, 15000.0)


def test_repair_anchor_newest_first(tmpdb):
    from app import bank_repair

    acc = tmpdb.ensure_account("Garanti")
    parsed = _parse(ROWS_C)
    tmpdb.import_bank_rows(parsed, account="Garanti", account_id=acc)
    with tmpdb.connect() as c:  # eski kodun yazdığı çapa: günün ilk hareketi
        c.execute("UPDATE accounts SET balance = 9700 WHERE id = ?", (acc,))
    tmpdb.save_bank_document(_xlsx(ROWS_C), "garanti.xlsx", "Garanti", account_id=acc)
    rep = bank_repair.repair(apply=False)
    assert [(a["amount"], a["was"]) for a in rep["anchors"]] == [(9200.0, "Garanti 9,700.00")]
    bank_repair.repair(apply=True)
    assert next(x for x in tmpdb.list_accounts() if x["id"] == acc)["balance"] == 9200.0


def test_repair_transfer_accounts_balance_effect(tmpdb, capsys):
    """Dökümden gelen virmana hesap yazmak bakiyeyi değiştirmez; yalnız e-postadan gelmiş, çapadan sonraki virman
    bakiyeye girer. Kuru çalıştırma çıktısında bakiye etkisi ve hesap bakiyeleri önce/sonra."""
    from app import bank_repair

    acc = _anchored(tmpdb)  # Halkbank, çapa 10.10.2026 = 20.000
    with tmpdb.connect() as c:  # eski kod: hesap bilgisi yazılmamış virmanlar
        c.execute("INSERT INTO own_transfers (transfer_date, amount, account, description, import_hash) "
                  "VALUES ('2026-10-05', -1000, 'Halkbank', 'dokumden', 'doc-1')")
        c.execute("INSERT INTO own_transfers (transfer_date, amount, account, description, import_hash) "
                  "VALUES ('2026-10-15', -2500, 'Halkbank', 'bildirimden', 'mail:x')")
    rep = bank_repair.repair(apply=False)
    assert [(t["amount"], t["effect"]) for t in rep["transfer_accounts"]] == [(-1000.0, 0.0)]
    assert [(t["amount"], t["effect"]) for t in rep["transfer_balance"]] == [(-2500.0, -2500.0)]
    bank_repair._print(rep, False)
    out = capsys.readouterr().out
    assert "bakiye değişmez): 1 kayıt, bakiye etkisi +0.00" in out
    assert "bakiye etkisi -2,500.00" in out
    rep = bank_repair.repair(apply=True)
    b = next(x for x in rep["balances"] if x["account"] == "Halkbank")
    assert (b["before"], b["after"], b["diff"]) == (20000.0, 17500.0, -2500.0)
    assert _current(tmpdb, acc) == 17500.0
