"""Okunamayan fiş (bulanık, uzak, kadraj dışı): toplam 0/boş ve tutarlı kalem yoksa hak düşmez, istemci yeniden çektirir."""
import pytest

from tests import test_plans
from tests.test_doc_reads import _jpeg, _reads
from tests.test_plans import _user

hosted = test_plans.hosted


@pytest.mark.parametrize("doc", [
    {"merchant": "Fiş", "total": 0.0, "items": []},
    {"merchant": None, "total": None, "items": []},
    {"merchant": "Ornek", "total": None, "items": [{"name": "?", "amount": None}]},
])
def test_unreadable_receipt_not_charged(hosted, monkeypatch, doc):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    monkeypatch.setattr(receipt, "parse_receipt", lambda *a, **k: dict(doc))
    c, _ = _user(hosted)
    before = _reads(c)["reads"]["used"]
    r = c.post("/api/receipts/parse", files={"file": ("f.jpg", _jpeg(), "image/jpeg")}).json()
    assert r["unreadable"] is True and "net değil" in r["message"] and "charged" not in r
    assert _reads(c)["reads"]["used"] == before


def test_readable_receipt_still_charged(hosted, monkeypatch):
    from app import receipt

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST0000")
    monkeypatch.setattr(receipt, "parse_receipt", lambda *a, **k: {"merchant": "Ornek", "total": 72.0, "items": []})
    c, _ = _user(hosted)
    r = c.post("/api/receipts/parse", files={"file": ("f.jpg", _jpeg(), "image/jpeg")}).json()
    assert not r.get("unreadable") and r["charged"]["daily"] == 1


def test_unreadable_helper():
    from app.api.main import _unreadable

    assert _unreadable({"type": "receipt", "total": 0, "items": []})
    assert not _unreadable({"type": "receipt", "total": 0, "items": [{"amount": 12.5}]})
    assert not _unreadable({"type": "bill", "amount": None})
