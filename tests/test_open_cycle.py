"""Kesilmemiş ekstre: bugüne kadar biriken + kesimde tahmini; fişteki kart son 4 hanesi."""
from datetime import date

from app import finance, receipt

TODAY = date(2026, 10, 25)


def _entry(client, d, amount, last4=None, n=1, payment="kredi_karti"):
    body = {"entry_date": d, "description": "Fiş", "amount": amount, "kind": "gider", "category": "Market & Gıda",
            "payment": payment, "installments": n}
    if last4:
        body["card_last4"] = last4
    return client.post("/api/entries", json=body).json()["id"]


def test_open_cycle_counts_receipts_since_last_cut(client, upload):
    upload(["vakifbank_a.txt"])  # kesim 13.10, sonraki kesim 13.11, son ödeme 23.11
    acc = next(iter(finance.card_projection()))
    base = finance.open_cycles(TODAY)[acc]
    assert base["spent"] == 0 and base["cut"] == "2026-11-13" and base["due"] == "2026-11-23"
    assert base["at_cut"] >= base["so_far"]

    _entry(client, "2026-10-20", 600, last4=acc[-4:])
    _entry(client, "2026-10-21", 900, n=3)              # kartı belirtilmemiş, 3 taksit: bu ekstreye 300
    _entry(client, "2026-10-10", 5000)                  # son kesimden önce: sayılmaz
    _entry(client, "2026-10-22", 400, payment="nakit")  # nakit: sayılmaz
    c = finance.open_cycles(TODAY)[acc]
    assert c["spent"] == 900 and c["unassigned"] == 300 and c["entries"] == 2
    assert c["so_far"] == round(base["so_far"] + 900, 2)
    # bugüne kadar kalemlerle açıklanır: taksitler + abonelikler + girilen harcamalar = so_far
    assert round(sum(i["amount"] for i in c["items"]), 2) == c["so_far"]
    assert sum(1 for i in c["items"] if i["type"] == "harcama") == 2
    # her satırın kalıcı uid'i: dokununca düzenleme (harcama → kayıt m<id>, taksit/abonelik → kart işlemi c<id>)
    uids = [i["uid"] for i in c["items"]]
    assert len(set(uids)) == len(uids) and all(u for u in uids)
    assert all(i["uid"] == f"m{i['entry_id']}" for i in c["items"] if i["type"] == "harcama")
    assert all(i["uid"].startswith(f"c{i['txn_id']}:") for i in c["items"] if i["type"] != "harcama")
    assert finance.open_cycles(TODAY)[acc]["items"] == c["items"]  # tekrar hesapta aynı
    pace = c["variable_avg"] * c["days_left"] / c["days_total"]
    assert c["at_cut"] == round(c["installments"] + c["subscriptions"] + max(c["variable_avg"], 900 + pace), 2)
    # ödenecekler ve net durum aynı rakamı kullanır
    est = next(p for p in finance.payables(TODAY) if p["type"] == "estimate")
    assert est["amount"] == c["at_cut"] and est["cycle"]["so_far"] == c["so_far"]


def test_card_last4_from_receipts():
    assert receipt.card_last4("**** 2222") == "2222" and receipt.card_last4(2222) == "2222"
    assert receipt.card_last4(None) is None and receipt.card_last4("yok") is None
    text = "ORNEK MARKET\nTOPLAM *120,00\nKREDI KARTI\n400000******2222\nONAY KODU 123456"
    import app.ocr as o

    orig = o.text_of
    o.text_of = lambda d, m: text
    try:
        assert o.parse(b"x", "image/jpeg", ["Diğer"])["card_last4"] == "2222"
    finally:
        o.text_of = orig


def test_auto_link_prefers_receipt_card(client, upload, tmpdb):
    upload(["vakifbank_a.txt", "kuveytturk.txt"])
    with tmpdb.connect() as c:
        t1, t2 = (r[0] for r in c.execute("SELECT t.id FROM transactions t JOIN statements s ON s.id = t.statement_id "
                                            "GROUP BY s.card_no ORDER BY s.card_no"))
        rows = c.execute("SELECT t.id, s.card_no, t.txn_date FROM transactions t JOIN statements s ON s.id = t.statement_id "
                         "WHERE t.id IN (?, ?)", (t1, t2)).fetchall()
        # iki kartta aynı gün aynı tutar: fişteki son 4 hane hangisiyse ona bağlanmalı
        for tid, _, _ in rows:
            c.execute("UPDATE transactions SET amount = 777.77, txn_date = '2026-09-20', txn_type = 'harcama' WHERE id = ?", (tid,))
    target = next(r for r in rows if r[0] == max(t1, t2))
    eid = _entry(client, "2026-09-20", 777.77, last4=target[1][-4:])
    e = next(x for x in tmpdb.list_entries() if x["id"] == eid)
    assert e["linked_txn_id"] == target[0]

