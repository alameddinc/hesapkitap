"""S8: günlüklerde kişisel veri maskeleme, erişim günlüğünde sorgu dizgesi yok (uydurma veri)."""
import logging

from app import logs


def test_mask_patterns():
    s = logs.mask("giriş ayse.ornek+1@ornek.com.tr IBAN TR00 0001 0000 0000 0000 0000 00 kart 5555 4444 3333 1111 "
                  "tc 10000000146 tutar 1.234,56 gün 2026-10-31")
    assert "ornek.com" not in s and "<e-posta>" in s and "<iban>" in s and "<kart>" in s and "<no>" in s
    assert "1.234,56" in s and "2026-10-31" in s  # tutar ve tarih bozulmaz


def test_access_log_query_stripped_and_messages_masked():
    f = logs.MaskFilter()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/api/admin/deleted?email=ayse@ornek.com", "1.1", 200), None)
    f.filter(rec)
    assert "ornek.com" not in rec.getMessage() and "/api/admin/deleted?…" in rec.getMessage()
    rec = logging.LogRecord("ekstre.jobs", logging.INFO, __file__, 1, "job %s %s ok", ("x", "ayse@ornek.com"), None)
    f.filter(rec)
    assert rec.getMessage() == "job x <e-posta> ok"
    out = logs.MaskFormatter("%(message)s")
    try:
        raise ValueError("bozuk: veli@ornek.com")
    except ValueError:
        import sys

        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "hata", (), sys.exc_info())
    assert "ornek.com" not in out.format(rec)


def test_job_log_uses_id():
    assert logs.user_ref({"id": 12, "username": "ayse@ornek.com"}) == "u12" and logs.user_ref(None) == "ana"


def test_admin_deleted_lookup_by_post(client, monkeypatch):
    from app.api import main

    monkeypatch.setattr(main, "_require_admin", lambda: None)
    r = client.post("/api/admin/deleted", json={"email": "yok@ornek.com"})
    assert r.status_code == 200 and r.json()["records"] == []
