"""Garanti arşivi (app/warranty.py): ürün, bitiş ve durum, belgeler, fişten / harcamadan ekleme, paket sınırı, ortak
defter yetkileri, push hatırlatması, dışa aktarma ve hesap silme. Ürün ve mağaza adları uydurmadır."""
import io
import os
import zipfile
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import push, warranty
from tests.test_book import _member, owner  # noqa: F401 - fixture

TODAY = date(2026, 11, 12)
BASE = {"name": "Örnek Süpürge X1", "brand": "Kuzey", "model": "X1", "store": "Örnek Elektronik",
        "purchase_date": "2026-03-10", "price": 8499.9}


def _jpeg(color=(200, 30, 30), size=(64, 48)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


PDF = b"%PDF-1.4\n% ornek garanti belgesi\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"


def _add(c, **kw):
    r = c.post("/api/warranties", json={**BASE, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _file(c, wid, data=None, name="fatura.jpg", ctype="image/jpeg", doc_type="fatura"):
    return c.post(f"/api/warranties/{wid}/files", files={"file": (name, data if data is not None else _jpeg(), ctype)},
                  data={"doc_type": doc_type})


# ---------------------------------------------------------------- bitiş, durum, kalan süre

def test_add_months_clamps_month_end():
    assert warranty.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert warranty.add_months(date(2024, 2, 29), 24) == date(2026, 2, 28)
    assert warranty.add_months(date(2026, 11, 12), 14) == date(2028, 1, 12)


def test_left_text_and_status():
    assert warranty.left_text(0) == "Bugün bitiyor"
    assert warranty.left_text(12) == "12 gün kaldı"
    assert warranty.left_text(95) == "3 ay kaldı"
    assert warranty.left_text(400) == "1 yıl 1 ay kaldı"
    assert warranty.left_text(-40) == "1 ay önce bitti"
    assert [warranty.status_of(d) for d in (-1, 0, 30, 31)] == ["bitti", "yakinda", "yakinda", "aktif"]


def test_create_defaults_to_two_years_and_lists(client):
    w = _add(client)
    assert w["months"] == 24 and w["extra_months"] == 0 and w["ends_at"] == "2028-03-10"
    assert w["status"] == "aktif" and w["can_edit"] and w["files"] == []
    w2 = _add(client, name="Örnek Kulaklık", purchase_date=(date.today() - timedelta(days=360)).isoformat(), months=12,
              extra_months=0, store="Örnek Mağaza", brand=None, model=None)
    assert w2["status"] == "yakinda" and 0 <= w2["days_left"] <= 30
    w3 = _add(client, name="Örnek Tost Makinesi", purchase_date="2023-01-05", months=24, extra_months=12,
              brand="Güney", model="T5")
    assert w3["ends_at"] == "2026-01-05" and w3["status"] == "bitti" and w3["total_months"] == 36
    out = client.get("/api/warranties").json()
    assert out["counts"] == {"aktif": 1, "yakinda": 1, "bitti": 1, "all": 3}
    assert [x["id"] for x in out["items"]] == [w2["id"], w["id"], w3["id"]]  # bitmek üzere önce, biten sonda
    assert out["default_months"] == 24 and out["soon_days"] == 30 and out["max_files"] == 5
    assert out["quota"] == {"limit": None, "used": 3, "locked": False, "need": None}  # kendi sunucusu: sınırsız
    # arama (Türkçe harf farkı yok), durum süzgeci
    assert [x["id"] for x in client.get("/api/warranties", params={"q": "kulaklik"}).json()["items"]] == [w2["id"]]
    assert [x["id"] for x in client.get("/api/warranties", params={"q": "kuzey x1"}).json()["items"]] == [w["id"]]
    assert [x["id"] for x in client.get("/api/warranties", params={"status": "bitti"}).json()["items"]] == [w3["id"]]


def test_patch_and_validation(client):
    w = _add(client)
    r = client.patch(f"/api/warranties/{w['id']}", json={"extra_months": 12, "serial_no": "  SN-0001  ", "note": "Kutu dolapta"})
    assert r.status_code == 200 and r.json()["ends_at"] == "2029-03-10" and r.json()["serial_no"] == "SN-0001"
    assert client.patch(f"/api/warranties/{w['id']}", json={"name": None}).status_code == 422
    assert client.patch(f"/api/warranties/{w['id']}", json={"months": 500}).status_code == 422
    assert client.post("/api/warranties", json={**BASE, "name": ""}).status_code == 422
    assert client.post("/api/warranties", json={k: v for k, v in BASE.items() if k != "purchase_date"}).status_code == 422
    assert client.get("/api/warranties/9999").status_code == 404
    assert client.delete(f"/api/warranties/{w['id']}").json() == {"ok": True}
    assert client.get(f"/api/warranties/{w['id']}").status_code == 404


def test_create_replay_same_key_one_product(client, tmpdb):
    h = {"Idempotency-Key": "kuyruk-garanti-0001"}
    a = client.post("/api/warranties", json=BASE, headers=h).json()
    with tmpdb.connect() as c:  # ara katmanın 24 saatlik kaydı silinmiş gibi: kalıcı client_key yakalar
        c.execute("DELETE FROM idempotency")
    b = client.post("/api/warranties", json=BASE, headers=h).json()
    assert a["id"] == b["id"] and client.get("/api/warranties").json()["counts"]["all"] == 1


# ---------------------------------------------------------------- belgeler

def test_files_photo_pdf_limits_and_cleanup(client, tmpdb):
    w = _add(client)
    r = _file(client, w["id"])
    assert r.status_code == 200
    f = r.json()["files"][0]
    assert f["doc_type"] == "fatura" and f["doc_label"] == "Fatura / fiş" and not f["pdf"] and not f["from_receipt"]
    r = _file(client, w["id"], PDF, "garanti.pdf", "application/pdf", "garanti")
    pdf = r.json()["files"][1]
    assert pdf["pdf"] and pdf["doc_type"] == "garanti"
    got = client.get(f"/api/warranties/{w['id']}/files/{pdf['id']}")
    assert got.status_code == 200 and got.content == PDF and got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith("inline")
    dl = client.get(f"/api/warranties/{w['id']}/files/{pdf['id']}", params={"download": 1})
    assert dl.headers["content-disposition"].startswith("attachment") and "garanti-belgesi" in dl.headers["content-disposition"]
    img = client.get(f"/api/warranties/{w['id']}/files/{f['id']}")
    assert img.headers["content-type"] == "image/jpeg" and img.content[:3] == b"\xff\xd8\xff"
    # yalnız fotoğraf / PDF; açılmayan görüntü reddedilir
    assert _file(client, w["id"], b"merhaba", "not.txt", "text/plain").status_code == 422
    assert _file(client, w["id"], b"\xff\xd8\xffbozuk", "bozuk.jpg").status_code == 422
    # ürün başına en çok 5 belge
    for i in range(3):
        assert _file(client, w["id"], _jpeg((i * 40, 90, 10))).status_code == 200
    r = _file(client, w["id"], _jpeg((1, 2, 3)))
    assert r.status_code == 422 and "en çok 5" in r.json()["detail"]
    # aynı dosya ikinci kez eklenmez (sınır dolu olsa da ikinci kez saymaz: önce denetlenir)
    with tmpdb.connect() as c:
        names = {n for (n,) in c.execute("SELECT name FROM warranty_files WHERE kind = 'warranty'")}
    assert len(names) == 5 and all(tmpdb.file_indexed("warranty", n) for n in names)
    # belge kaldırılınca dosyası da gider; ürün silinince hepsi
    r = client.delete(f"/api/warranties/{w['id']}/files/{f['id']}")
    assert r.status_code == 200 and len(r.json()["files"]) == 4
    assert sum(tmpdb.file_indexed("warranty", n) for n in names) == 4
    client.delete(f"/api/warranties/{w['id']}")
    assert not any(tmpdb.file_indexed("warranty", n) for n in names)
    assert client.get(f"/api/warranties/{w['id']}/files/{pdf['id']}").status_code == 404


def test_photo_exif_dropped_and_gc_keeps_linked(client, tmpdb):
    from PIL import Image

    im = Image.new("RGB", (40, 30), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "OrnekCihaz"  # Make
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    w = _add(client)
    fid = _file(client, w["id"], buf.getvalue()).json()["files"][0]["id"]
    data = client.get(f"/api/warranties/{w['id']}/files/{fid}").content
    assert b"OrnekCihaz" not in data
    with tmpdb.connect() as c:
        c.execute("UPDATE stored_files SET created_at = datetime('now', '-3 days')")
    assert tmpdb.gc_files() == []  # bağlı belge temizlikte silinmez


def test_storage_full_returns_507(client, monkeypatch):
    from app import plans

    monkeypatch.setattr(plans, "storage_limit", lambda u: 10)
    w = _add(client)
    r = _file(client, w["id"])
    assert r.status_code == 507 and "alanın doldu" in r.json()["detail"]
    assert client.get(f"/api/warranties/{w['id']}").json()["files"] == []


# ---------------------------------------------------------------- fişten / harcamadan

def _receipt_entry(client, tmpdb, items=True):
    photo = _jpeg((5, 5, 5), (80, 120))
    name = f"{tmpdb.file_hash(photo)}.jpg"
    assert tmpdb.store_file("receipts", name, photo)
    eid = client.post("/api/entries", json={"entry_date": "2026-10-20", "description": "Örnek Teknoloji Market", "amount": 3150,
                                            "kind": "gider", "category": "Elektronik", "payment": "nakit",
                                            "receipt_file": name}).json()["id"]
    if items:
        r = client.put(f"/api/entries/{eid}/receipt-items", json={"items": [
            {"name": "Örnek Kettle K2", "qty": 1, "amount": 2900}, {"name": "Poşet", "qty": 1, "amount": 250}]})
        assert r.status_code == 200, r.text
    return eid, name


def test_from_receipt_entry_links_photo_and_survives_entry_delete(client, tmpdb):
    eid, name = _receipt_entry(client, tmpdb)
    d = client.get("/api/warranties/draft", params={"entry_id": eid}).json()
    assert d["source"] == "entry" and d["purchase_date"] == "2026-10-20" and d["store"] == "Örnek Teknoloji Market"
    assert d["name"] is None and d["receipt_file"] == name  # iki kalem: kişi seçer
    assert [i["name"] for i in d["items"]] == ["Örnek Kettle K2", "Poşet"]
    lst = client.get("/api/warranties/receipts").json()
    assert lst[0]["entry_id"] == eid and lst[0]["photo"] and lst[0]["items"] == 2
    assert client.get("/api/warranties/receipts", params={"q": "kettle"}).json()[0]["entry_id"] == eid
    assert client.get("/api/warranties/receipts", params={"q": "buzdolabi"}).json() == []
    w = _add(client, name="Örnek Kettle K2", price=2900, purchase_date="2026-10-20", store=d["store"], entry_id=eid)
    assert w["entry_id"] == eid and len(w["files"]) == 1 and w["files"][0]["from_receipt"]
    assert w["files"][0]["doc_type"] == "fatura"
    assert client.get(f"/api/warranties/{w['id']}/files/{w['files'][0]['id']}").status_code == 200
    # kayıt silinse de fiş fotoğrafı garantide kalır
    client.delete(f"/api/entries/{eid}")
    w = client.get(f"/api/warranties/{w['id']}").json()
    assert w["entry_id"] is None and tmpdb.file_indexed("receipts", name)
    assert client.get(f"/api/warranties/{w['id']}/files/{w['files'][0]['id']}").status_code == 200
    # ürün silinince artık kimse kullanmıyor: dosya gider
    client.delete(f"/api/warranties/{w['id']}")
    assert not tmpdb.file_indexed("receipts", name)


def test_new_photo_receipt_file_and_unknown_file_ignored(client, tmpdb):
    photo = _jpeg((9, 9, 9))
    name = f"{tmpdb.file_hash(photo)}.jpg"
    tmpdb.store_file("receipts", name, photo)  # POST /api/receipts/parse'ın sakladığı fotoğraf
    w = _add(client, receipt_file=name)
    assert len(w["files"]) == 1 and w["files"][0]["from_receipt"]
    w2 = _add(client, name="Örnek Ütü", receipt_file="a" * 64 + ".jpg")  # bu defterde yok: bağlanmaz
    assert w2["files"] == []
    with tmpdb.connect() as c:
        c.execute("UPDATE stored_files SET created_at = datetime('now', '-3 days')")
    assert tmpdb.gc_files() == []  # kayda bağlı değil ama garantide: silinmez


def test_from_card_transaction(client, upload):
    from app import db

    upload()
    txn = next(x for x in db.card_transactions() if x["txn_type"] == "harcama")
    d = client.get("/api/warranties/draft", params={"txn_id": txn["id"]}).json()
    assert d["source"] == "txn" and d["txn_id"] == txn["id"] and d["purchase_date"] == str(txn["txn_date"])[:10]
    assert d["price"] > 0 and d["store"]
    w = _add(client, name="Örnek Telefon", txn_id=txn["id"], price=d["price"], purchase_date=d["purchase_date"])
    assert w["txn_id"] == txn["id"] and w["files"] == []
    assert client.get("/api/warranties/draft").status_code == 422
    assert client.get("/api/warranties/draft", params={"txn_id": 99999}).status_code == 404


# ---------------------------------------------------------------- paket: ücretsizde 3 ürün

def test_free_plan_three_products_then_402_premium_unlimited(client, monkeypatch):
    from tests.test_debt_jar import _hosted_user
    from tests.test_plans import _rc

    c, u = _hosted_user(client, monkeypatch)
    for i in range(3):
        assert c.post("/api/warranties", json={**BASE, "name": f"Örnek Ürün {i}"}).status_code == 200
    q = c.get("/api/warranties").json()["quota"]
    assert q == {"limit": 3, "used": 3, "locked": True, "need": "Premium"}
    r = c.post("/api/warranties", json={**BASE, "name": "Dördüncü"})
    assert r.status_code == 402 and r.headers.get("X-Plan-Required") == "garanti" and "Premium" in r.json()["detail"]
    # düzenleme, belge ve silme sınırdan bağımsız
    wid = c.get("/api/warranties").json()["items"][0]["id"]
    assert c.patch(f"/api/warranties/{wid}", json={"note": "not"}).status_code == 200
    assert _file(c, wid).status_code == 200
    meta = c.get("/api/meta").json()["plan"]
    assert meta["tiers"]["garanti"] == "premium" and meta["features"]["garanti"] is False
    assert _rc(client, "INITIAL_PURCHASE", u["id"]).status_code == 200
    assert c.post("/api/warranties", json={**BASE, "name": "Dördüncü"}).status_code == 200
    assert c.get("/api/warranties").json()["quota"]["limit"] is None


def test_admin_can_make_unlimited_free(client, monkeypatch):
    from tests.test_debt_jar import _hosted_user

    from app import plans

    c, _ = _hosted_user(client, monkeypatch)
    assert plans.set_tiers({"garanti": "free"})["garanti"] == "free"
    try:
        for i in range(4):
            assert c.post("/api/warranties", json={**BASE, "name": f"Örnek Ürün {i}"}).status_code == 200
        assert c.get("/api/warranties").json()["quota"]["limit"] is None
    finally:
        plans.set_tiers({}, reset=True)


# ---------------------------------------------------------------- ortak defter

def test_member_roles_and_hidden(owner):  # noqa: F811
    w = _add(owner)
    viewer = _member(owner, "bakan", "viewer")
    editor = _member(owner, "ekleyen", "editor")
    assert [x["id"] for x in viewer.get("/api/warranties").json()["items"]] == [w["id"]]
    assert viewer.post("/api/warranties", json={**BASE, "name": "Görenin ürünü"}).status_code == 403
    mine = editor.post("/api/warranties", json={**BASE, "name": "Eşin ürünü"})
    assert mine.status_code == 200 and mine.json()["can_edit"]
    mid = mine.json()["id"]
    assert editor.get(f"/api/warranties/{w['id']}").json()["can_edit"] is False
    assert editor.patch(f"/api/warranties/{w['id']}", json={"note": "x"}).status_code == 403
    assert editor.delete(f"/api/warranties/{w['id']}").status_code == 403
    assert _file(editor, w["id"]).status_code == 403
    assert editor.patch(f"/api/warranties/{mid}", json={"note": "benim"}).status_code == 200
    assert _file(editor, mid).status_code == 200
    # ürün defterin: sahip hepsini görür ve düzenler
    assert {x["name"] for x in owner.get("/api/warranties").json()["items"]} == {BASE["name"], "Eşin ürünü"}
    assert owner.get(f"/api/warranties/{mid}").json()["can_edit"]
    # gizli kayda bağlı ürün üyeye görünmez
    eid = owner.post("/api/entries", json={"entry_date": "2026-10-02", "description": "Sürpriz", "amount": 900, "kind": "gider",
                                           "category": "Giyim", "payment": "nakit"}).json()["id"]
    owner.patch(f"/api/entries/{eid}", json={"hidden": True})
    sec = _add(owner, name="Sürpriz saat", entry_id=eid)
    assert sec["id"] not in [x["id"] for x in editor.get("/api/warranties").json()["items"]]
    assert editor.get(f"/api/warranties/{sec['id']}").status_code == 404
    assert editor.get("/api/warranties/draft", params={"entry_id": eid}).status_code == 404
    # sahip sayfayı kapatınca uçlar da kapanır
    assert owner.put("/api/book/rules", json={"hidden_pages": ["garantiler"]}).status_code == 200
    assert editor.get("/api/warranties").status_code == 403
    assert editor.post("/api/warranties", json=BASE).status_code == 403


# ---------------------------------------------------------------- push

def _device(uid=1, version="1.0.7 (40)"):
    push.register(uid, "t" * 40 + str(uid), "ios", "production", version)


def test_push_stages_30_and_7_once_each(client, tmpdb):
    _device()
    a = warranty.create({"name": "Örnek Fırın", "purchase_date": "2024-12-12", "months": 24})  # bitiş 12.12.2026: 30 gün
    b = warranty.create({"name": "Örnek Saç Kurutma", "purchase_date": "2024-11-19", "months": 24})  # 19.11.2026: 7 gün
    c = warranty.create({"name": "Örnek Blender", "purchase_date": "2024-11-25", "months": 24})  # 13 gün: 30 aşaması
    warranty.create({"name": "Uzak", "purchase_date": "2026-01-01", "months": 24})
    warranty.create({"name": "Bitmiş", "purchase_date": "2024-01-01", "months": 24})
    got = {i["id"]: i for i in push._garanti({"id": 1}, TODAY)}
    assert set(got) == {f"garanti:{a}:2026-12-12:30gun", f"garanti:{b}:2026-11-19:7gun", f"garanti:{c}:2026-11-25:30gun"}
    it = got[f"garanti:{b}:2026-11-19:7gun"]
    assert it["kind"] == "garanti" and it["route"] == f"#/diger/garantiler/{b}"
    assert it["title"] == "Örnek Saç Kurutma: garantinin bitmesine 7 gün kaldı"
    assert it["body"] == ("Garanti 19 Kasım 2026 tarihinde bitiyor. Arızası varsa şimdi servise götür; fatura ve garanti "
                          "belgesi Garantiler'de.")
    # ertesi günler aynı aşama yeniden gitmez (kimlik aynı → push_sent), 7 güne girince ikinci bildirim
    sent = push.notify(1, list(got.values()), background=False)
    assert sent and not push.notify(1, push._garanti({"id": 1}, TODAY + timedelta(days=1)), background=False)
    later = push._garanti({"id": 1}, date(2026, 12, 6))
    assert f"garanti:{a}:2026-12-12:7gun" in {i["id"] for i in later}
    last = {i["id"]: i for i in push._garanti({"id": 1}, date(2026, 12, 12))}
    assert last[f"garanti:{a}:2026-12-12:7gun"]["title"] == "Örnek Fırın: garantinin bugün bitiyor"


def test_push_needs_new_app_and_toggle(client, tmpdb):
    warranty.create({"name": "Örnek Fırın", "purchase_date": "2024-12-12", "months": 24})
    assert push._garanti({"id": 1}, TODAY) == []  # telefon yok
    _device(version="1.0.6 (38)")
    assert push._garanti({"id": 1}, TODAY) == []  # eski uygulamada Garantiler ekranı yok
    _device(version="1.0.7")
    assert len(push._garanti({"id": 1}, TODAY)) == 1
    on = push.get_prefs(1)
    assert on["types"]["garanti"] is True and "garanti" in {i["kind"] for i in push.collect({"id": 1}, TODAY, on)}
    off = {**on, "types": {**on["types"], "garanti": False}}
    assert "garanti" not in {i["kind"] for i in push.collect({"id": 1}, TODAY, off)}
    assert push.TYPES["garanti"] == ("Garanti bitişi", True) and push.NOUNS["garanti"] == "garanti hatırlatması"


def test_push_hidden_page_for_member(owner, monkeypatch):  # noqa: F811
    from app import book

    _add(owner, purchase_date="2024-12-12")
    monkeypatch.setattr(push, "has_app", lambda uid, v: True)
    monkeypatch.setattr(book, "active", lambda: {"hidden_pages": ["garantiler"]})
    assert push._garanti({"id": 1}, TODAY) == []


# ---------------------------------------------------------------- dışa aktarma ve hesap silme

def test_export_workbook_and_zip_include_warranties(client, tmpdb):
    from openpyxl import load_workbook

    from app import backup

    w = _add(client, serial_no="SN-42")
    fid = _file(client, w["id"], PDF, "garanti.pdf", "application/pdf", "garanti").json()["files"][0]["id"]
    wb = load_workbook(io.BytesIO(client.get("/api/settings/export.xlsx").content))
    assert wb.sheetnames[-1] == "Garantiler"
    rows = list(wb["Garantiler"].values)
    assert rows[0][:4] == ("Ürün", "Marka / model", "Mağaza", "Alış tarihi")
    assert rows[1][0] == BASE["name"] and rows[1][1] == "Kuzey X1" and rows[1][7] == "2028-03-10" and rows[1][9] == "SN-42"
    path = backup.export_zip()
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        assert any(n.startswith("warranty/") and n.endswith(".pdf") for n in names)
    finally:
        os.remove(path)
    assert fid


def test_export_without_warranties_keeps_sheets(client):
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(client.get("/api/settings/export.xlsx").content))
    assert "Garantiler" not in wb.sheetnames


def test_account_delete_removes_warranty_files(client, monkeypatch):
    from tests.test_debt_jar import _hosted_user

    from app import system

    monkeypatch.setenv("HESAPKITAP_DELETE_GRACE_DAYS", "0")
    c, u = _hosted_user(client, monkeypatch)
    w = c.post("/api/warranties", json=BASE).json()
    assert _file(c, w["id"], PDF, "g.pdf", "application/pdf").status_code == 200
    folder = os.path.dirname(system.user_db_path(u))
    wdir = os.path.join(folder, "warranty")
    assert os.path.isdir(wdir) and os.listdir(wdir)
    assert c.post("/api/me/delete", json={"password": "ayse-sifre-12345"}).json()["purged"] is True
    assert not os.path.exists(wdir)


def test_demo_blocks_file_upload():
    from app import demo_mode

    assert demo_mode.closed("/api/warranties/3/files")
    assert not demo_mode.closed("/api/warranties") and not demo_mode.closed("/api/warranties/3/files/7")


@pytest.mark.parametrize("cap", ["warranty"])
def test_capability(client, cap):
    assert cap in client.get("/api/meta").json()["capabilities"]
