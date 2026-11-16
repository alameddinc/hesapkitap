"""Belge okuma sorun kaydı (incidents kodu belge_okuma, yetenek doc_read_report): yer "tür/yol/sebep"; sunucu
okunamayanı ve tür uyuşmazlığını kendisi yazar, istemci yalnız "kullanıcı okunan türü değiştirdi"yi bildirir.
İçerik / kişisel veri yok. Uydurma veri."""
import pytest

from tests.test_book import owner  # noqa: F401 - fixture
from tests.test_incidents import _kinds, _rows


@pytest.fixture(autouse=True)
def _clean():
    from app import incidents

    incidents._buf.clear()
    incidents._rate.clear()
    yield
    incidents._buf.clear()


def _places():
    return {x["place"] for x in _kinds() if x["code"] == "belge_okuma"}


def test_unreadable_bill_counted_by_type_path_reason(owner):  # noqa: F811
    assert owner.post("/api/bills/parse", files={"file": ("a.txt", b"metin", "text/plain")}).status_code == 422
    assert _places() == {"fatura/parse/unreadable"}


def test_note_outside_request_and_unknown_values():
    from app import incidents

    incidents.note("dekont_okunamadi")
    assert _places() == {"dekont/diger/unreadable"}
    assert incidents.belge_yeri("x", "y", "z", "q") == "belge/diger/diger"
    assert incidents.belge_yeri("fatura", "parse", "type_mismatch", "dekont") == "fatura/parse/type_mismatch:dekont"


def test_server_type_mismatch(owner, monkeypatch):  # noqa: F811
    from app import dekont_parse
    from app.api import main

    monkeypatch.setattr(main, "_pdf_text", lambda data: "HAVALE DEKONTU")
    monkeypatch.setattr(dekont_parse, "is_transfer", lambda t: True)
    r = owner.post("/api/bills/parse", files={"file": ("d.pdf", b"%PDF-1.4 x", "application/pdf")},
                   data={"transfer_check": "1"})
    assert r.json()["looks_like_transfer"] is True
    assert _places() == {"fatura/parse/type_mismatch:dekont"}


def test_client_reports_user_changed_type_only(owner):  # noqa: F811
    ok = {"code": "belge_okuma", "doc_type": "fatura", "to_type": "dekont", "path": "parse",
          "reason": "user_changed_type", "app_version": "1.0.9 (47)", "platform": "ios"}
    r = owner.post("/api/incidents/client", json={"events": [ok, {"code": "belge_okuma", "doc_type": "fis"},
                                                             {"code": "belge_okuma", "reason": "user_changed_type"}]})
    assert r.status_code == 200 and r.json()["accepted"] == 1
    assert _places() == {"fatura/parse/user_changed_type:dekont"}
    # serbest metin kabul edilmez (yalnız sabit değerler)
    bad = {**ok, "doc_type": "Ayşe'nin faturası"}
    assert owner.post("/api/incidents/client", json={"events": [bad]}).status_code == 422
    row = next(x for x in _rows())
    assert row["app_version"] == "1.0.9 (47)" and row["platform"] == "ios"


def test_admin_list_shows_counts(owner):  # noqa: F811
    for _ in range(2):
        owner.post("/api/bills/parse", files={"file": ("a.txt", b"metin", "text/plain")})
    items = owner.get("/api/admin/incidents").json()["items"]
    k = next(x for x in items if x["code"] == "belge_okuma")
    assert k["place"] == "fatura/parse/unreadable" and k["count"] == 2 and k["label"].startswith("Belge okuma")
