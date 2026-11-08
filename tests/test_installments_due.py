"""Özet → "Bu ay ödenecek taksitler" (finance.installments_due, GET /api/installments, summary.installments).

Kural: dönemin harcaması taksit dilimlerini zaten içerir; bu bilgi harcamaya EKLENMEZ (çift sayım yok). Ekstresi
henüz gelmemiş dönemin taksitleri son ekstrenin kalan taksitinden gelir (expected) ve harcamaya girmez.
Veriler uydurma: tests/fixtures/*.txt sahte ekstreler, tutarlar örnek.
"""
import pytest

from app import finance, money
from tests.test_book import _member, owner  # noqa: F401 (pytest fixture)

OCT, NOV = "2026-10", "2026-11"


@pytest.fixture()
def loaded(upload):
    assert all(r["status"] == "saved" for r in upload())


def _inst(client, month):
    r = client.get("/api/installments", params={"month": month})
    assert r.status_code == 200, r.text
    return r.json()


def test_statement_installments_are_part_of_spending_not_added(client, loaded):
    s = client.get("/api/summary", params={"month": OCT}).json()
    inst = s["installments"]
    full = _inst(client, OCT)
    # summary'deki özet uçtakiyle aynı (items hariç)
    assert {k: v for k, v in full.items() if k != "items"} == inst
    # ekim ekstreleri yüklü: hepsi harcamaya dahil, beklenen yok
    assert inst["expected"] == 0 and money.eq(inst["total"], inst["in_spend"])
    rows = [r for r in client.get("/api/transactions", params={"month": OCT}).json()
            if r.get("installment") and r["counts_as_spend"]]
    assert money.eq(inst["in_spend"], sum(r["amount"] for r in rows))
    assert {i["uid"] for i in full["items"]} == {r["uid"] for r in rows}
    # harcama satırların toplamı: taksit bilgisi ikinci kez eklenmemiş
    spend = sum(r["amount"] for r in client.get("/api/transactions", params={"month": OCT}).json() if r["counts_as_spend"])
    assert money.eq(s["totals"]["expense"], spend)
    # sayı: vade farkı/BSMV/KKDF taksitleri ve iade taksitleri sayılmaz; tutara girer
    assert inst["count"] == sum(1 for i in full["items"] if not i["fee"] and i["amount"] > 0)
    assert any(i["fee"] for i in full["items"]) and any(i["amount"] < 0 for i in full["items"])
    mob = next(i for i in full["items"] if i["title"] == "ORNEK MOBILYA")
    assert (mob["installment"], mob["no"], mob["of"], mob["amount"], mob["in_spend"]) == ("3/6", 3, 6, 8750.0, True)
    assert mob["card"].endswith("…9012") and mob["due_date"] == "2026-10-26"
    assert mob["row"]["uid"] == mob["uid"] and mob["row"]["source"] == "card"


def test_next_month_expected_from_last_statement(client, loaded):
    nov = _inst(client, NOV)
    proj = finance.card_projection()
    want = sum(i["amount"] for p in proj.values() for i in p["installments"] if i["month"] == NOV)
    assert money.eq(nov["expected"], want) and nov["in_spend"] == 0
    assert all(i["source"] == "projected" and not i["in_spend"] for i in nov["items"])
    mob = next(i for i in nov["items"] if i["title"] == "ORNEK MOBILYA")
    assert mob["installment"] == "4/6" and mob["due_date"] == "2026-11-25"  # ekstredeki "sonraki son ödeme"
    # dokununca açılacak satır: kaynağı olan ekim ekstresindeki kart işlemi
    assert mob["row"]["uid"].startswith("c") and mob["row"]["month"] == OCT
    # beklenen taksit kasım harcamasına girmez
    s = client.get("/api/summary", params={"month": NOV}).json()
    rows = client.get("/api/transactions", params={"month": NOV}).json()
    assert money.eq(s["totals"]["expense"], sum(r["amount"] for r in rows if r["counts_as_spend"]))
    assert not {i["uid"] for i in nov["items"]} & {r["uid"] for r in rows}
    # kalan taksit borcu: ekimden sonrası = kasım dahil bütün kalan taksitler
    oct_ = _inst(client, OCT)
    all_left = sum(i["amount"] for p in proj.values() for i in p["installments"])
    assert money.eq(oct_["remaining"], all_left)
    assert money.eq(nov["remaining"], all_left - want)


def test_manual_installment_slice_counted_once(client, loaded):
    before = client.get("/api/summary", params={"month": NOV}).json()
    r = client.post("/api/entries", json={"entry_date": "2026-10-28", "description": "Örnek Beyaz Eşya", "amount": 6000,
                                          "kind": "gider", "category": "Elektronik", "payment": "kredi_karti",
                                          "installments": 3})
    assert r.status_code == 200
    month = client.get("/api/transactions", params={"month": NOV}).json()
    first = next(x for x in month if x["description"] == "Örnek Beyaz Eşya")
    after = client.get("/api/summary", params={"month": NOV}).json()
    # harcama tek dilim kadar arttı; taksit özeti de aynı dilim kadar (in_spend)
    assert money.eq(after["totals"]["expense"] - before["totals"]["expense"], first["amount"])
    assert money.eq(after["installments"]["in_spend"] - before["installments"]["in_spend"], first["amount"])
    assert after["installments"]["count"] == before["installments"]["count"] + 1
    item = next(i for i in _inst(client, NOV)["items"] if i["title"] == "Örnek Beyaz Eşya")
    assert item["source"] == "manual" and item["installment"] == "1/3" and item["in_spend"]
    assert item["row"]["id"] == r.json()["id"] and item["row"]["original_amount"] == 6000
    # sonraki iki dilim kalan taksit borcunda
    assert money.eq(after["installments"]["remaining"] - before["installments"]["remaining"], 4000)


def test_linked_manual_installment_not_double_counted(client, tmpdb, loaded):
    # ekim ekstresinde "ORNEK TEKNOLOJI 1. Taksit 6.240 (6 taksit)": aynı alışveriş elle de girilmiş, eşleşir
    eid = client.post("/api/entries", json={"entry_date": "2026-09-20", "description": "Örnek Teknoloji", "amount": 37440,
                                            "kind": "gider", "payment": "kredi_karti", "installments": 6}).json()["id"]
    tmpdb.auto_link()
    with tmpdb.connect() as c:
        assert c.execute("SELECT linked_txn_id FROM entries WHERE id = ?", (eid,)).fetchone()[0] is not None
    oct_ = _inst(client, OCT)
    assert [i["source"] for i in oct_["items"] if "TEKNOLOJI" in i["title"].upper()] == ["card"]
    left = sum(i["amount"] for p in finance.card_projection().values() for i in p["installments"])
    assert money.eq(oct_["remaining"], left)  # eşleşmiş kaydın sonraki dilimleri ikinci kez eklenmez
    nov = _inst(client, NOV)
    assert [i["source"] for i in nov["items"] if "TEKNOLOJI" in i["title"].upper()] == ["projected"]


def test_capability_and_empty(client):
    assert "summary_installments" in client.get("/api/meta").json()["capabilities"]
    s = client.get("/api/summary", params={"month": OCT}).json()["installments"]
    assert (s["total"], s["count"], s["remaining"], s["expected"]) == (0, 0, 0, 0)
    assert _inst(client, OCT)["items"] == []
    assert client.get("/api/installments", params={"month": "ekim"}).status_code == 422


def test_shared_book_hides_cards_fees_and_entries(owner):
    from tests.conftest import fixture_bytes

    files = [("files", (n.replace(".txt", ".pdf"), fixture_bytes(n), "application/pdf"))
             for n in ("vakifbank_a.txt", "kuveytturk.txt")]
    assert all(r["status"] == "saved" for r in owner.post("/api/statements/upload", files=files).json())
    sec = owner.post("/api/entries", json={"entry_date": "2026-10-02", "description": "Sürpriz hediye", "amount": 900,
                                           "kind": "gider", "payment": "kredi_karti", "installments": 3}).json()["id"]
    m = _member(owner)
    seen = _inst(m, OCT)
    assert any(i["fee"] for i in seen["items"]) and any(i["title"] == "Sürpriz hediye" for i in seen["items"])
    # Kim harcadı: elle kaydı sahip ekledi; ekstre satırı kartın kişisi yok → Ortak
    gift = next(i for i in seen["items"] if i["title"] == "Sürpriz hediye")
    assert gift["spender"].startswith("u") and gift["row"]["spender"] == gift["spender"]
    assert any(i["spender"] == "ortak" and i["spender_name"] == "Ortak" for i in seen["items"])

    kuveyt = next(c["card_no"] for c in owner.get("/api/book").json()["cards"] if c["bank"] == "Kuveyt Türk")
    owner.put("/api/book/rules", json={"hidden_cards": [kuveyt], "hide_fees": True})
    owner.patch(f"/api/entries/{sec}", json={"hidden": True})
    hid = _inst(m, OCT)
    assert not any(i["fee"] for i in hid["items"])
    assert not any("…" + kuveyt[-4:] in (i["card"] or "") for i in hid["items"])
    assert not any(i["title"] == "Sürpriz hediye" for i in hid["items"])
    # gizli kaydın sonraki dilimleri kalan borçta da yok; özet satırı listeyle aynı
    s = m.get("/api/summary", params={"month": OCT}).json()["installments"]
    assert s == {k: v for k, v in hid.items() if k != "items"}
    assert s["remaining"] < _inst(owner, OCT)["remaining"]
    # sahip hepsini görür
    assert any(i["title"] == "Sürpriz hediye" for i in _inst(owner, OCT)["items"])
    # üyenin işlem listesinde de gizli taksitli kaydın dilimleri yok (installment_entries süzülür)
    assert not any(r["description"] == "Sürpriz hediye" for r in m.get("/api/transactions", params={"month": NOV}).json())
