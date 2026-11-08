"""Kasanın departman / reyon satırı ("TEKEL ÜRÜNLERİ" — birkaç ürünün toplamı) fiyat takibine girmez; adı düzeltilen
kalemin anahtarı yeni addan türer; eski satırlar arka planda parça parça düzeltilir (app/receipt_rekey.py).
Örnek veriler uydurmadır (işyeri adları "Örnek Bakkal" gibi)."""
import pytest

from app import db, receipt_rekey


@pytest.mark.parametrize("name", [
    "TEKEL ÜRÜNLERİ", "tekel", "TEKEL", "GIDA", "GIDA ÜRÜNLERİ", "MUHTELİF", "ÇEŞİTLİ", "REYON 1", "REYON 12",
    "DEPARTMAN 3", "DPT01", "KDV %1 ÜRÜN", "%18", "%8", "KDV %20", "MANAV", "ŞARKÜTERİ", "KASAP", "FIRIN ÜRÜNLERİ",
    "ÜRÜN", "ÜRÜN 5", "MAL BEDELİ", "SÜT ÜRÜNLERİ", "SİGARA", "TEMEL GIDA %1", "Okunamayan ürün"])
def test_department_lines(name):
    assert db.department_line(name) and db.item_kind(name, None, 100) == "group"


@pytest.mark.parametrize("name", [
    "Tekel 2000 rakı", "TEKEL 2000", "Örnekmarka Slims Mavi Sigara", "KASAP SUCUK", "FIRIN EKMEK", "TEMİZLİK BEZİ",
    "Harras Günlük Süt 1 L", "Latte 16 Oz", "%1 SÜT", "EKMEK"])
def test_real_products_are_not_departments(name):
    assert not db.department_line(name) and db.item_kind(name, None, 50) == "product"


def test_product_fits_name():
    assert db.product_fits("PNR BEYAZ", "Beyaz Peynir") and db.product_fits("DMT", "Domates")
    assert db.product_fits("ULKR CKLT", "Ülker Çikolata") and db.product_fits("KOLA", "Coca Cola")
    assert db.product_fits("12345", "Süt")                                    # adda kelime yok: karar verilemez
    assert not db.product_fits("Örnekmarka Slims Mavi Sigara", "tekel ürünleri")  # ad sonradan düzeltilmiş
    # eski product ada ait değil: tür ve anahtar addan
    assert db.item_kind("Örnekmarka Slims Mavi Sigara", "tekel ürünleri", 266) == "product"
    assert db.item_key("Örnekmarka Slims Mavi Sigara", "tekel ürünleri") == "ORNEKMARKA SLIMS MAVI SIGARA"
    assert db.item_product("PINAR SÜT", "Süt Ürünleri") is None                # bölüm adı ürünün standart adı olamaz
    assert db.item_product("tekel", "Tekel Ürünleri") == "Tekel Ürünleri"      # ikisi de bölüm: satır grup kalır
    assert db.item_kind("tekel", "Tekel Ürünleri", 655) == "group"
    assert db.item_kind("12345", "Tekel Ürünleri", 50) == "group"              # ad kod, standart ad bölüm


def _bakkal(client, day, items, total, shop="Örnek Bakkal"):
    return client.post("/api/entries", json={"entry_date": day, "description": shop, "amount": total, "kind": "gider",
                                             "category": "Market & Gıda", "payment": "nakit", "items": items}).json()["id"]


def test_department_line_never_compared(client, tmpdb):
    """Tek satırlık bakkal fişi (departman satırı = fişin tamamı) ile sonraki gerçek ürün aynı ürün sayılmaz: −%80 yok."""
    _bakkal(client, "2026-11-01", [{"name": "tekel", "product": "Tekel Ürünleri", "qty": 1, "amount": 655}], 655)
    # istemci adı düzeltip eski product'ı geri yolladı
    _bakkal(client, "2026-11-08", [{"name": "Örnekmarka Slims Mavi Sigara", "product": "tekel ürünleri", "qty": 2,
                                    "amount": 266}], 266, shop="ÖRNEK BAKKAL")
    with tmpdb.connect() as c:
        rows = [tuple(r) for r in c.execute("SELECT name, product, product_key FROM receipt_items ORDER BY id")]
    assert rows[1] == ("Örnekmarka Slims Mavi Sigara", None, "ORNEKMARKA SLIMS MAVI SIGARA")
    ps = client.get("/api/prices").json()["products"]
    assert [(p["product"], p["last_price"], p["change_pct"]) for p in ps] == [("Örnekmarka Slims Mavi Sigara", 133, None)]
    assert db.price_history() == []
    # fiş satırları ve tutarlar olduğu gibi (departman satırı kayıtta kalır)
    e1 = db.list_entries()[-1]
    assert client.get(f"/api/entries/{e1['id']}/receipt-items").json()["items"][0]["amount"] == 655
    # alışveriş listesi / fiş denetimi geçmişi: departman satırı geçmiş fiyat sayılmaz
    assert db.recent_unit_prices(["Tekel Ürünleri"], "Örnek Bakkal") == {}
    assert db.last_unit_prices(["tekel"], "Örnek Bakkal", today=__import__("datetime").date(2026, 11, 9)) == {}


def test_put_rename_rederives_key(client, tmpdb):
    """Kayıtlı fişte ad düzeltildi ama product eski kaldı (aynı kelimeyi paylaşsa da): anahtar yeni addan."""
    eid = _bakkal(client, "2026-11-02", [{"name": "ORNEK SUT 1L", "product": "Örnek Süt 1 L", "qty": 1, "amount": 40},
                                         {"name": "EKMEK", "product": "Ekmek", "qty": 1, "amount": 15}], 55)
    client.put(f"/api/entries/{eid}/receipt-items", json={"items": [
        {"name": "Örnek Süt 1 L", "product": "Örnek Süt 1 L", "qty": 1, "amount": 40},   # aynı product yeni yazılış
        {"name": "Tam Buğday Ekmek", "product": "Ekmek", "qty": 1, "amount": 15}]})     # ad değişti, product eski
    with tmpdb.connect() as c:
        rows = [tuple(r) for r in c.execute("SELECT name, product, product_key FROM receipt_items ORDER BY id")]
    assert rows == [("Örnek Süt 1 L", "Örnek Süt 1 L", "ORNEK SUT 1 L"), ("Tam Buğday Ekmek", None, "TAM BUGDAY EKMEK")]
    # ad değişmeden yeniden kaydetmek product'ı korur
    client.put(f"/api/entries/{eid}/receipt-items", json={"items": [
        {"name": "Örnek Süt 1 L", "product": "Örnek Süt 1 L", "qty": 1, "amount": 40},
        {"name": "Tam Buğday Ekmek", "product": None, "qty": 1, "amount": 15}]})
    with tmpdb.connect() as c:
        assert c.execute("SELECT product FROM receipt_items ORDER BY id").fetchone()[0] == "Örnek Süt 1 L"


def _old_rows(tmpdb):
    """Eski sürümün yazdığı satırlar (DB kopyası senaryosu): ad düzeltilmiş ama anahtar departman adında."""
    with tmpdb.connect() as c:
        for i, (name, product, key, amt) in enumerate([
                ("tekel", "Tekel Ürünleri", "TEKEL URUNLERI", 655),
                ("Örnekmarka Slims Mavi Sigara", "tekel ürünleri", "TEKEL URUNLERI", 266),
                ("ORNEK SUT 1L", "Örnek Süt 1 L", "ORNEK SUT 1 L", 40),
                ("Latte 16 Oz", None, "LATTE 16OZ", 120)]):           # eski normalize: yeniden türetilir
            eid = c.execute("INSERT INTO entries (entry_date, month, description, amount, kind, category) VALUES "
                            "(?, '2026-11', 'Örnek Bakkal', ?, 'gider', 'Market & Gıda')",
                            (f"2026-11-0{i + 1}", amt)).lastrowid
            c.execute("INSERT INTO receipt_items (entry_id, purchased_at, merchant, name, product, product_key, qty, "
                      "amount, unit_price) VALUES (?, ?, 'Örnek Bakkal', ?, ?, ?, 1, ?, ?)",
                      (eid, f"2026-11-0{i + 1}", name, product, key, amt, amt))


def test_rekey_background_fixes_old_rows(tmpdb):
    _old_rows(tmpdb)
    assert receipt_rekey.pending()
    got = receipt_rekey.tick(spaces=[(None, None)])
    assert got == {"rows": 4, "changed": 2, "finished": True}
    with tmpdb.connect() as c:
        rows = [tuple(r) for r in c.execute("SELECT name, product, product_key FROM receipt_items ORDER BY id")]
    assert rows == [("tekel", "Tekel Ürünleri", "TEKEL URUNLERI"),
                    ("Örnekmarka Slims Mavi Sigara", None, "ORNEKMARKA SLIMS MAVI SIGARA"),
                    ("ORNEK SUT 1L", "Örnek Süt 1 L", "ORNEK SUT 1 L"), ("Latte 16 Oz", None, "LATTE 16 OZ")]
    assert not receipt_rekey.pending() and receipt_rekey.tick(spaces=[(None, None)]) is None   # sürümlü: bir kez
    assert db.get_setting(receipt_rekey.KEY_VERSION) == str(receipt_rekey.VERSION)
    # tekrar koşturmak (ör. sürüm artınca) değiştirecek bir şey bulmaz
    from app import system

    system.put(receipt_rekey.KEY_VERSION, None)
    db.set_setting(receipt_rekey.KEY_VERSION, None)
    assert receipt_rekey.tick(spaces=[(None, None)])["changed"] == 0
    ps = db.products()["products"]
    assert {p["key"] for p in ps} == {"ORNEKMARKA SLIMS MAVI SIGARA", "ORNEK SUT 1 L", "LATTE 16 OZ"}


def test_rekey_resumes_in_chunks_and_spaces(tmp_path, tmpdb, monkeypatch):
    """Parça parça: her parça kısa işlem, ilerleme defterin settings'inde; süre bütçesi dolunca kalan sonraki tura.
    Birden çok defter (ana + kullanıcı) ayrı ayrı biter; hepsi bitince sistem sürümü yazılır."""
    _old_rows(tmpdb)
    other = str(tmp_path / "u1" / "data.db")
    with db.use_space(other, {"id": 1}):
        _old_rows(db)
    monkeypatch.setattr(receipt_rekey, "CHUNK", 1)
    monkeypatch.setattr(receipt_rekey, "BUDGET_S", 0.0)   # tur başına tek parça
    spaces = [(None, None), (other, {"id": 1})]
    steps = 0
    while receipt_rekey.pending() and steps < 30:
        got = receipt_rekey.tick(spaces=spaces)
        steps += 1
        assert got["rows"] <= 1
    assert not receipt_rekey.pending() and steps >= 8
    for path, user in spaces:
        with db.use_space(path, user):
            with db.connect() as c:
                keys = [r[0] for r in c.execute("SELECT product_key FROM receipt_items ORDER BY id")]
            assert keys[1] == "ORNEKMARKA SLIMS MAVI SIGARA" and db.get_setting(receipt_rekey.KEY_CURSOR) is None


def test_single_whole_total_department_like_line_not_compared(client, tmpdb):
    """Zincir olmayan işyerinde fişin tek satırı fişin tamamı ve adı departmana benziyor (kısaltma): fiyat takibine
    girmez; aynı adlı gerçek ürün (birden çok satırlı fiş) girer."""
    _bakkal(client, "2026-11-01", [{"name": "TEKEL ÜRN.", "qty": 1, "amount": 655}], 655)
    _bakkal(client, "2026-11-08", [{"name": "TEKEL ÜRN.", "qty": 1, "amount": 133},
                                   {"name": "EKMEK", "qty": 1, "amount": 15}], 148)
    ps = {p["product"]: p for p in client.get("/api/prices").json()["products"]}
    assert ps["TEKEL ÜRN."]["last_price"] == 133 and ps["TEKEL ÜRN."]["change_pct"] is None
    e = {"amount": 655, "description": "Örnek Bakkal"}
    assert db.whole_receipt_department("TEKEL ÜRN.", 655, e, 1)
    assert not db.whole_receipt_department("TEKEL ÜRN.", 600, e, 1)          # fişin tamamı değil
    assert not db.whole_receipt_department("Örnek Süt 1 L", 655, e, 1)       # departmana benzemiyor
    assert not db.whole_receipt_department("TEKEL ÜRN.", 655, {**e, "description": "MIGROS"}, 1)  # zincir
