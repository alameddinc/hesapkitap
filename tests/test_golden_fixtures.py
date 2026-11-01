"""Altın fikstür seti (C15, Ek A §4): her belge boş deftere API'den yüklenir; satır sayısı, giren/çıkan toplamı ve son
bakiye (hesap dökümü) ya da işlem sayısı, harcama toplamı ve dönem borcu (kart ekstresi) belgenin kendisinden elle
yazılmış değerlerle (tests/fixtures/altin/beklenen.json) karşılaştırılır. Aynı belge ikinci kez hiçbir şey eklemez.
Veriler uydurmadır (tests/fixtures/altin/make_altin.py)."""
import json
from pathlib import Path

import pytest

from app import money
from app.parsers.base import PAYMENT

HERE = Path(__file__).parent / "fixtures" / "altin"
SPEC = json.loads((HERE / "beklenen.json").read_text(encoding="utf-8"))


def _post(client, data: bytes, name: str, form: dict | None = None, preview: bool = False):
    files = {"file": (name, data, "application/octet-stream")}
    return client.post("/api/bank-import/preview" if preview else "/api/bank-import", files=files, data=form or {})


@pytest.mark.parametrize("doc", SPEC["hesap"], ids=[d["dosya"] for d in SPEC["hesap"]])
def test_bank_document_totals_and_closing_balance(client, tmpdb, doc):
    data = (HERE / doc["dosya"]).read_bytes()
    client.post("/api/accounts", json={"name": "Ornek Vadesiz", "kind": "banka", "balance": 0, "as_of": "2026-01-01"})
    acc = next(a["id"] for a in tmpdb.list_accounts() if a["name"] == "Ornek Vadesiz")
    pv = _post(client, data, doc["dosya"], preview=True).json()
    assert pv["total"] == doc["satir"], pv
    form = {"mapping": json.dumps(pv["mapping"]), "header_row": str(pv["header_row"]), "account_id": str(acc)}
    r = _post(client, data, doc["dosya"], form)
    assert r.status_code == 200, r.text
    assert r.json()["tekrar"] == 0
    entries = [e for e in tmpdb.list_entries() if e["account_id"] == acc]
    assert len(entries) == doc["satir"]  # eş satırlar dahil hiçbiri kaybolmadı
    giren = sum(e["amount"] for e in entries if e["kind"] == "gelir")
    cikan = sum(e["amount"] for e in entries if e["kind"] == "gider")
    assert money.eq(giren, doc["giren"]) and money.eq(cikan, doc["cikan"]), (giren, cikan)
    a = next(x for x in tmpdb.list_accounts() if x["id"] == acc)
    assert money.eq(a["balance"], doc["son_bakiye"]) and a["as_of"][:10] == doc["son_bakiye_tarihi"], a  # çapa
    assert money.eq(a["current"], doc["son_bakiye"]), a  # çapadan sonra hareket yok: güncel bakiye aynı
    again = _post(client, data, doc["dosya"], form).json()  # ikinci yükleme: hepsi "zaten var"
    assert again["tekrar"] == doc["satir"]
    assert len([e for e in tmpdb.list_entries() if e["account_id"] == acc]) == doc["satir"]


@pytest.mark.parametrize("doc", SPEC["kart"], ids=[d["dosya"] for d in SPEC["kart"]])
def test_card_statement_totals_and_debt(client, tmpdb, doc):
    from tests.conftest import fixture_bytes

    name = Path(doc["dosya"]).name
    r = client.post("/api/statements/upload", files=[("files", (name.replace(".txt", ".pdf"), fixture_bytes(name),
                                                                "application/pdf"))]).json()
    assert [x["status"] for x in r] == ["saved"], r
    st = tmpdb.list_statements()
    assert len(st) == 1
    st = st[0]
    assert (st["period_end"], st["due_date"]) == (doc["kesim"], doc["son_odeme"])
    assert money.eq(st["total_debt"], doc["donem_borcu"])
    txns = [t for t in tmpdb.card_transactions() if t["statement_id"] == st["id"]]
    spend = [t for t in txns if t["txn_type"] != PAYMENT]
    paid = [t for t in txns if t["txn_type"] == PAYMENT]
    assert len(spend) == doc["islem"]
    assert money.eq(sum(t["amount"] for t in spend), doc["harcama_toplami"])
    assert money.eq(-sum(t["amount"] for t in paid), doc["odeme"]) or money.eq(sum(t["amount"] for t in paid), doc["odeme"])
    again = client.post("/api/statements/upload", files=[("files", (name.replace(".txt", ".pdf"), fixture_bytes(name),
                                                                    "application/pdf"))]).json()
    assert again[0]["status"] != "saved" and len(tmpdb.list_statements()) == 1


def test_manifest_is_self_consistent():
    """Beklenen değerlerin kendi içinde tutarlılığı: (son bakiye − net akış) belgedeki açılış bakiyesi olmalı."""
    opening = {"hesap-eskiden-yeniye.csv": 10000.00, "hesap-yeniden-eskiye.xlsx": 5000.00}
    for d in SPEC["hesap"]:
        assert money.eq(opening[d["dosya"]] + d["giren"] - d["cikan"], d["son_bakiye"]), d["dosya"]
