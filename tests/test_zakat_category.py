"""Zekât sadakadan ayrı grup (yetenek zakat_category): zekât ödemesine bağlı hareket Özet/İstatistik kategori kırılımında
"Zekât", satırda donation_kind "zekat"; kategori "Bağış & Sadaka" kalır, toplamlar aynı. Tutarlar, kurumlar uydurma."""
from datetime import date

from app.parsers.base import PURCHASE, Statement, Transaction
from tests.test_book import _member, owner  # noqa: F401 - fixture

MONTH = "2026-10"
CAT = "Bağış & Sadaka"
CARD = "4543***9876"


def _me(client) -> int:
    me = next(p for p in client.get("/api/zakat").json()["people"] if p["is_self"])["id"]
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-03-01"})
    return me


def _setup(client) -> None:
    """10.000 sadaka + 20.000 zekât (gider olarak yazılan ödeme) + 300 market."""
    r = client.post("/api/donations", json={"kind": "sadaka", "amount": 10000, "given_date": "2026-10-18",
                                            "payment": "nakit"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/zakat/people/{_me(client)}/payments",
                    json={"amount": 20000, "pay_date": "2026-10-20", "payment": "havale"})
    assert r.status_code == 200, r.text
    r = client.post("/api/entries", json={"entry_date": "2026-10-15", "description": "ORNEK MARKET", "amount": 300,
                                          "kind": "gider", "category": "Market & Gıda"})
    assert r.status_code == 200, r.text


def _groups(client, zakat: bool = True) -> dict[str, float]:
    params = {"month": MONTH, **({"zakat": 1} if zakat else {})}
    return {g["name"]: g["amount"] for g in client.get("/api/summary", params=params).json()["by_category"]}


def test_zakat_separate_group_same_total(client, tmpdb):
    _setup(client)
    s = client.get("/api/summary", params={"month": MONTH, "zakat": 1}).json()
    groups = {g["name"]: g for g in s["by_category"]}
    assert groups[CAT]["amount"] == 10000 and groups[CAT]["count"] == 1
    assert groups["Zekât"]["amount"] == 20000 and groups["Zekât"]["count"] == 1
    # yalnız gruplama: toplam gider ve grupların toplamı aynı
    assert s["totals"]["expense"] == 30300
    assert round(sum(g["amount"] for g in s["by_category"]), 2) == 30300


def test_old_client_without_param_sees_single_group(client, tmpdb):
    """Eski istemci (1.0.9) ?zakat göndermez: kırılım bugünkü gibi, zekât Bağış & Sadaka içinde; aynı önbellekte iki
    biçim karışmaz."""
    _setup(client)
    assert _groups(client) == {"Zekât": 20000, CAT: 10000, "Market & Gıda": 300}
    assert _groups(client, zakat=False) == {CAT: 30000, "Market & Gıda": 300}
    assert _groups(client) == {"Zekât": 20000, CAT: 10000, "Market & Gıda": 300}


def test_rows_keep_category_and_carry_donation_kind(client, tmpdb):
    _setup(client)
    rows = client.get("/api/transactions", params={"month": MONTH}).json()
    don = sorted((r for r in rows if r["category"] == CAT), key=lambda r: r["amount"])
    assert [r["amount"] for r in don] == [10000, 20000]
    assert "donation_kind" not in don[0]                 # sadaka: alan yok
    assert don[1]["donation_kind"] == "zekat"            # zekât: kategori yine "Bağış & Sadaka"
    assert all("donation_kind" not in r for r in rows if r["category"] != CAT)


def test_category_filter_virtual_group(client, tmpdb):
    _setup(client)

    def amounts(cat):
        return [r["amount"] for r in client.get("/api/transactions", params={"month": MONTH, "category": cat}).json()]

    assert amounts("Zekât") == [20000]
    assert amounts(CAT) == [10000]
    assert amounts("Market & Gıda") == [300]
    t = client.get("/api/transactions", params={"month": MONTH, "category": "Zekât", "totals": 1}).json()["totals"]
    assert t["expense"] == 20000 and t["count"] == 1


def test_year_summary_and_day_detail_split(client, tmpdb):
    _setup(client)
    y = client.get("/api/year-summary", params={"year": 2026, "zakat": 1}).json()
    cats = {c["name"]: c["amount"] for c in y["categories"]}
    assert cats[CAT] == 10000 and cats["Zekât"] == 20000 and y["expense"] == 30300
    d = client.get("/api/day/2026-10-20", params={"zakat": 1}).json()
    assert {c["name"] for c in d["by_category"]} == {"Zekât"} and d["rows"][0]["donation_kind"] == "zekat"
    # parametresiz (eski istemci): eski yanıt
    y = client.get("/api/year-summary", params={"year": 2026}).json()
    assert {c["name"]: c["amount"] for c in y["categories"]}[CAT] == 30000
    assert {c["name"] for c in client.get("/api/day/2026-10-20").json()["by_category"]} == {CAT}


def test_zakat_marked_card_row_and_linked_statement(client, tmpdb):
    """Kartla ödenen zekât ekstreyle eşleşince sayılan kart satırı da Zekât; ekstrede zekât diye işaretlenen satır da."""
    me = _me(client)
    r = client.post(f"/api/zakat/people/{me}/payments",
                    json={"amount": 2500, "pay_date": "2026-10-20", "payment": "kredi_karti", "card_last4": "9876"})
    assert r.status_code == 200, r.text
    lines = [(date(2026, 10, 20), "ORNEK VAKFI BAGIS", 2500.0), (date(2026, 10, 12), "ORNEK DERNEGI BAGIS", 700.0)]
    total = sum(a for _, _, a in lines)
    s = Statement(bank="VakıfBank", card_no=CARD, period_end=date(2026, 10, 26), due_date=date(2026, 11, 5),
                  total_debt=total, min_payment=100, credit_limit=50000, previous_balance=0, period_activity=total,
                  payments=0)
    s.transactions = [Transaction(txn_date=d, description=t, amount=a, txn_type=PURCHASE, card=CARD) for d, t, a in lines]
    tmpdb.save_statement(b"st-zekat-kat", "z.pdf", s)
    assert tmpdb.auto_link() == 1
    rows = client.get("/api/transactions", params={"month": MONTH}).json()
    card = {r["amount"]: r for r in rows if r["source"] == "card"}
    assert card[2500]["donation_kind"] == "zekat"
    assert card[2500]["category"] == CAT and card[700]["category"] == CAT
    assert _groups(client) == {"Zekât": 2500, CAT: 700}
    # ekstre satırını zekât diye işaretle: o da Zekât grubuna geçer, toplam aynı
    assert client.post("/api/donations/mark", json={"source": "card", "id": card[700]["id"], "kind": "zekat"}).status_code == 200
    assert _groups(client) == {"Zekât": 3200}
    assert client.get("/api/summary", params={"month": MONTH}).json()["totals"]["expense"] == 3200


def test_unmarking_returns_to_sadaka(client, tmpdb):
    _me(client)
    eid = tmpdb.create_entry({"entry_date": "2026-10-21", "description": "ORNEK VAKFI", "amount": 500, "kind": "gider",
                              "category": CAT})
    assert client.post("/api/donations/mark", json={"source": "entry", "id": eid, "kind": "zekat"}).status_code == 200
    assert _groups(client) == {"Zekât": 500}
    client.post("/api/donations/mark", json={"source": "entry", "id": eid, "kind": "sadaka"})
    assert _groups(client) == {CAT: 500}


def test_budget_follows_groups(client, tmpdb):
    _setup(client)
    assert client.put("/api/budgets", json={"category": CAT, "monthly_limit": 15000}).status_code == 200
    b = next(x for x in client.get("/api/budgets", params={"month": MONTH}).json()["status"] if x["category"] == CAT)
    assert b["spent"] == 10000  # parametresiz istekte de: bütçe her istemcide zekâtsız
    s = client.get("/api/summary", params={"month": MONTH}).json()
    assert next(x for x in s["budgets"] if x["category"] == CAT)["spent"] == 10000


def test_capability(client):
    assert "zakat_category" in client.get("/api/meta").json()["capabilities"]


def test_member_without_zakat_page_sees_plain_category(owner):
    """Ortak defter: Zekât sayfası üyeye kapalıysa işaret de grup da yok (toplam aynı); açıksa üye de ayrı görür."""
    _setup(owner)
    m = _member(owner, name="esim", role="editor")
    assert _groups(m)["Zekât"] == 20000
    sp = m.get("/api/book/member-spend", params={"month": MONTH, "zakat": 1}).json()
    assert {c["name"] for p in sp["people"] for c in p["categories"]} >= {"Zekât", CAT}
    sp = m.get("/api/book/member-spend", params={"month": MONTH}).json()
    assert "Zekât" not in {c["name"] for p in sp["people"] for c in p["categories"]}
    owner.put("/api/book/rules", json={"hidden_pages": ["zekat"]})
    g = _groups(m)
    assert "Zekât" not in g and g[CAT] == 30000
    assert all("donation_kind" not in r for r in m.get("/api/transactions", params={"month": MONTH}).json())
    assert _groups(owner)["Zekât"] == 20000  # sahip yine ayrı görür
