"""Karşılaştırma aracı (python -m app.replay_check): saklı belgeler boş geçici defterde baştan içe aktarılır, canlıyla
karşılaştırılır. Canlı defter, depolama ve belge hakkı değişmez."""
import json

from tests.conftest import fixture_bytes
from tests.test_bank_import import CSV, _upload
from tests.test_doc_reads import _used
from tests.test_plans import _user, hosted  # noqa: F401 - fixture


def test_replay_matches_live_and_has_no_side_effects(hosted):  # noqa: F811
    from app import db, replay_check, system

    c, u = _user(hosted)
    up = c.post("/api/statements/upload", files=[("files", ("kart.pdf", fixture_bytes("vakifbank_a.txt"), "application/pdf"))])
    assert up.json()[0]["status"] == "saved"
    csv = CSV.replace("Müşteri: AYŞE YILMAZ;;;;", "Müşteri: AYŞE YILMAZ;;;;\nIBAN: TR12 0001 2009 9990 0001 2345 67;;;;")
    pv = _upload(c, csv.encode("utf-8"), "hareket.csv").json()
    c.post("/api/accounts", json={"name": "Örnek Bank", "kind": "banka", "balance": 0, "as_of": "2026-10-01"})
    acc = next(a["id"] for a in c.get("/api/accounts").json()["accounts"] if a["name"] == "Örnek Bank")
    r = c.post("/api/bank-import", files={"file": ("hareket.csv", csv.encode("utf-8"), "text/csv")},
               data={"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]), "account_id": str(acc)})
    assert r.status_code == 200, r.text
    c.post("/api/entries", json={"entry_date": "2026-10-21", "description": "Elle", "amount": 50, "kind": "gider",
                                 "category": "Diğer"})
    path = system.user_db_path(u)
    used = _used(u["id"])
    before = open(path, "rb").read()

    r = replay_check.run(u["username"], no_ai=True)
    assert [d["status"] for d in r["documents"]] == ["saved", "saved"]
    assert open(path, "rb").read() == before and _used(u["id"]) == used  # canlı ve hak değişmedi
    # belgeden gelen kayıtlar birebir: hiçbir ayda fark yok (elle girilen kayıt karşılaştırmaya girmez)
    assert all(not m.get("records") for m in r["months"]), r["months"]
    oct_ = next(m for m in r["months"] if m["month"] == "2026-10")
    assert oct_["new"]["gider"] == oct_["live"]["gider"] > 0 and oct_["new"]["virman_out"] == 5000
    assert r["virman"]["new"] == r["virman"]["live"] and r["people"]["new"] == r["people"]["live"]
    st = r["statements"][0]
    assert st["equation_diff"] is not None and "reconciliation_diff" in st
    assert r["payments"] and all(p["same"] for p in r["payments"])
    bal = r["balances"][0]
    assert bal["doc_last_balance"] == 51657.65 and bal["diff_new_vs_doc"] == 0
    text = replay_check.render(r)
    assert "(1) HESAP BAKİYELERİ" in text and "(5) VİRMAN VE KİŞİLER" in text
    json.dumps(r, default=str)
    assert replay_check.main([u["username"], "--no-ai", "--json"]) == 0
    with db.connect() as cc:  # ana (yönetici) defter de etkilenmedi
        assert cc.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
