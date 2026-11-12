"""Destek yazışması (support_chat), okunamayan ekstre bildirimi (upload_report) ve bekleyen dosyayı yeniden yüklemeden
okuma (upload_retry). Kişiler, bankalar, tutarlar ve belge metinleri uydurmadır."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests import test_plans
from tests.conftest import fixture_bytes
from tests.test_plans import _user
from tests.test_book import owner  # noqa: F401 - fixture
from tests.test_read_quota_audit import _daily
from tests.test_upload_docs import UNRELATED_LINES, _up, ai, text_pdf  # noqa: F401 - fixture

hosted = test_plans.hosted

# yapıyı bozmayan ama kişisel veri taşıyan uydurma ekstre metni
STMT_LINES = ["ORNEK BANK A.S. KREDI KARTI HESAP OZETI", "Sayin MEHMET YILMAZTEPE", "Kart No 5400 12** **** 9876",
              "IBAN TR12 0006 4000 0011 2345 6789 01", "Hesap Kesim Tarihi 05.11.2026", "Son Odeme Tarihi 15.11.2026",
              "Donem Borcu 12.345,67", "01.11.2026 KAHVECI DUKKANI ISTANBUL 85,40",
              "02.11.2026 ORNEK MARKET 1.234,56", "TCKN 12345678901 ornek@posta.com"]


@pytest.fixture(autouse=True)
def _sync(monkeypatch):
    """Bildirimler istek içinde: kuyruğa giren push sınanabilsin."""
    from app import feedback, support

    monkeypatch.setattr(feedback, "notify_async", feedback.notify_admins)
    monkeypatch.setattr(support, "notify_report_async",
                        lambda fid, uid, summary: feedback.notify_admins(fid, f"Ekstre bildirimi: {summary}", uid, False, None))


def _report(c, row, send_doc=False, data=None, name="ekstre.pdf", bank="Ornek Bank", note="Her ay aynı hata"):
    rep = dict(row["report"]) | {"app_version": "1.0.9 (46)", "platform": "ios"}
    files = {"file": (name, data, "application/pdf")} if send_doc else None
    return c.post("/api/support/report", data={"report": json.dumps(rep), "bank_name": bank, "note": note,
                                                 "send_doc": "true" if send_doc else "false"}, files=files)


def test_upload_error_row_carries_report(hosted, ai):  # noqa: F811
    c, _ = _user(hosted)
    r = _up(c, "ekstre.pdf", text_pdf(STMT_LINES), "application/pdf", docs=True)
    assert r["status"] == "error"
    assert r["report"]["code"] == "ekstre_bulunamadi" and r["report"]["file_type"] == "pdf"
    assert r["report"]["pages"] == 1 and r["report"]["has_text"] is True
    assert set(r["report"]) == {"code", "file_type", "pages", "bank_guess", "has_text"}
    # fatura / belge yolundaki hata satırında report yok (yalnız ekstre/döküm)
    from tests.test_upload_docs import GSM_LINES

    b = _up(c, "fatura.pdf", text_pdf(GSM_LINES), "application/pdf", docs=False)
    assert b["status"] == "error" and "report" not in b


def test_report_without_document_reaches_admin_inbox(hosted, ai):  # noqa: F811
    from app import push

    c, u = _user(hosted)
    row = _up(c, "ekstre.pdf", text_pdf(STMT_LINES), "application/pdf")
    r = _report(c, row)
    assert r.status_code == 200, r.text
    assert r.json()["pending"] is None
    fid = r.json()["id"]

    inbox = hosted.get("/api/admin/support").json()
    assert inbox["counts"]["okunmamis"] == 1 and inbox["counts"]["acik"] == 1
    it = inbox["items"][0]
    assert it["user"]["username"] == "ayse@ornek.com" and it["unread"] == 1
    assert it["last"]["body"].startswith("Ekstre okunamadı · Banka: Ornek Bank") and it["context"]["platform"] == "ios"

    th = hosted.get(f"/api/admin/support/{u['id']}").json()
    m = th["messages"][0]
    assert m["kind"] == "ekstre" and m["report"]["code"] == "ekstre_bulunamadi" and m["report"]["sent_doc"] is False
    assert m["structure"] is False and m["seen"] is False
    assert hosted.get(f"/api/admin/support/messages/{fid}/structure").status_code == 404

    # yöneticiye push: yalnız özet (kullanıcının notu yok)
    q = [x["payload"] for x in push.queue(next(a["id"] for a in hosted_admins())) if x["payload"]["kind"] == "geri_bildirim"]
    assert q and "Ornek Bank" in q[-1]["body"] and "Her ay" not in q[-1]["body"]

    # okundu: kullanıcı "Görüldü"yü görür
    assert hosted.post(f"/api/admin/support/{u['id']}/seen").status_code == 200
    mine = c.get("/api/support").json()
    assert mine["messages"][0]["seen"] is True
    assert hosted.get("/api/admin/support").json()["counts"]["okunmamis"] == 0


def hosted_admins():
    from app import feedback

    return feedback.admins()


def test_document_sends_only_masked_structure_and_keeps_pending(hosted, ai):  # noqa: F811
    from app import storage, support, system

    c, u = _user(hosted)
    pdf = text_pdf(STMT_LINES)
    row = _up(c, "ekstre-mehmet.pdf", pdf, "application/pdf")
    r = _report(c, row, send_doc=True, data=pdf, name="ekstre-mehmet.pdf")
    assert r.status_code == 200, r.text
    pend = r.json()["pending"]
    assert pend["filename"] == "ekstre-mehmet.pdf"
    fid = r.json()["id"]

    st = hosted.get(f"/api/admin/support/messages/{fid}/structure").json()
    anon = st["anon"]
    for secret in ("MEHMET", "YILMAZTEPE", "KAHVECI", "9876", "12345678901", "ornek@posta.com", "12.345,67", "1.234,56",
                   "85,40", "0011 2345"):
        assert secret not in anon, secret
    assert "Hesap Kesim Tarihi 05.11.2026" in anon  # yapı (etiket + tarih) kalır
    assert "Donem Borcu 99.999,99" in anon and " 9.999,99" in anon  # tutarın biçimi ve öncesindeki boşluk kalır
    assert isinstance(st["diag"], dict) and "banka" in st["diag"]
    # yönetici yazışmasında dosya adı da yok; bekleyen dosya yalnız kullanıcıya görünür
    adm = json.dumps(hosted.get(f"/api/admin/support/{u['id']}").json(), ensure_ascii=False)
    assert "ekstre-mehmet" not in adm and "MEHMET" not in adm

    mine = c.get("/api/support").json()
    assert mine["messages"][0]["pending"]["id"] == pend["id"]
    assert c.get("/api/support/pending").json()["items"][0]["filename"] == "ekstre-mehmet.pdf"
    # dosya kullanıcının kendi defterinde (pending deposu), 30 gün
    exp = datetime.strptime(pend["expires_at"], "%Y-%m-%d %H:%M:%S")
    assert timedelta(days=29) < exp - datetime.now(timezone.utc).replace(tzinfo=None) <= timedelta(days=30)
    with support.own_space(u["id"]):
        assert storage.book_get("pending", support.pending_get(pend["id"])["name"]) == pdf
    assert system.user(u["id"])
    # yönetici ucundan dosya inmez: yapı ucu yalnız JSON    assert "application/pdf" not in hosted.get(f"/api/admin/support/messages/{fid}/structure").headers["content-type"]


def test_admin_reply_retry_flow_reads_without_reupload(hosted, ai, monkeypatch):  # noqa: F811
    from app import push
    from app.api import main
    from app.parsers import parse_statement as real_parse

    c, u = _user(hosted)
    pdf = text_pdf(STMT_LINES)
    row = _up(c, "ekstre.pdf", pdf, "application/pdf")
    fid = _report(c, row, send_doc=True, data=pdf).json()["id"]
    pid = c.get("/api/support/pending").json()["items"][0]["id"]

    # düzeltilmeden tekrar: ekstre yine bulunamaz, hak düşmez, dosya bekler
    before = _daily(u["id"])
    r = c.post(f"/api/support/pending/{pid}/retry")
    assert r.status_code == 200 and r.json()["status"] == "error" and r.json()["done"] is False
    assert _daily(u["id"]) == before and "report" not in r.json()
    assert len(c.get("/api/support/pending").json()["items"]) == 1

    # yönetici yanıtı + "Düzeltildi — tekrar dene"
    assert hosted.post(f"/api/admin/support/{u['id']}/reply", json={"message": "Bankan eklendi", "retry_ref": 999}).status_code == 422
    rep = hosted.post(f"/api/admin/support/{u['id']}/reply", json={"message": "Bankan eklendi, tekrar dene", "retry_ref": fid})
    assert rep.status_code == 200, rep.text
    assert c.get("/api/support/unread").json() == {"unread": 1, "status": "acik"}
    q = [x["payload"] for x in push.queue(u["id"]) if x["payload"]["kind"] == "destek"]
    assert len(q) == 1 and q[0]["title"] == "Ekstre okuyucusu düzeltildi" and "Bankan" not in q[0]["body"]
    assert q[0]["route"] == "#/diger/ayarlar"  # Destek ekranlı uygulama yok: Ayarlar
    th = c.get("/api/support").json()
    act = th["messages"][-1]["action"]
    assert act == {"type": "retry", "ref": fid, "done": False, "pending": act["pending"]} and act["pending"]["id"] == pid
    assert c.post("/api/support/seen").status_code == 200 and c.get("/api/support/unread").json()["unread"] == 0

    # okuyucu düzeldi: aynı yükleme yolu, kaydedilir, hak düşer, dosya bekleyenlerden düşer
    monkeypatch.setattr(main, "parse_statement", lambda data, passwords=None: real_parse(fixture_bytes("vakifbank_a.txt")))
    r = c.post(f"/api/support/pending/{pid}/retry")
    assert r.status_code == 200 and r.json()["status"] == "saved" and r.json()["done"] is True, r.text
    assert _daily(u["id"]) == before + 1
    assert c.get("/api/support/pending").json()["items"] == []
    assert len(c.get("/api/statements").json()) == 1
    th = c.get("/api/support").json()
    assert th["messages"][-1]["author"] == "system" and th["messages"][-2]["action"]["done"] is True
    assert c.post(f"/api/support/pending/{pid}/retry").status_code == 404


def test_feedback_messages_form_thread_and_old_list_unchanged(hosted):
    c, u = _user(hosted)
    assert c.post("/api/feedback", data={"message": "Grafik boş görünüyor"}).status_code == 200
    hosted.post(f"/api/admin/support/{u['id']}/reply", json={"message": "Bakıyoruz", "resolve": True})
    th = c.get("/api/support").json()
    assert [m["author"] for m in th["messages"]] == ["user", "admin"] and th["thread"]["status"] == "cozuldu"
    old = hosted.get("/api/admin/feedback").json()  # eski liste yalnız kullanıcı mesajları
    assert [x["message"] for x in old["items"]] == ["Grafik boş görünüyor"] and old["counts"]["cozuldu"] == 1
    # yeni mesaj yazışmayı yeniden açar
    c.post("/api/feedback", data={"message": "Hâlâ boş"})
    assert c.get("/api/support").json()["thread"]["status"] == "acik"
    assert hosted.patch(f"/api/admin/support/{u['id']}", json={"status": "cozuldu"}).json()["status"] == "cozuldu"
    assert hosted.patch("/api/admin/support/9999", json={"status": "cozuldu"}).status_code == 404


def test_access_rules(hosted, ai):  # noqa: F811
    c, u = _user(hosted)
    other, _ = _user(hosted, "mehmet")
    c.post("/api/feedback", data={"message": "Bir sorun"})
    fid = c.get("/api/support").json()["messages"][0]["id"]
    assert other.get("/api/support").json()["messages"] == []  # başkasının yazışması görünmez
    assert other.get(f"/api/support/messages/{fid}/screenshot/0").status_code == 404
    for path in ("/api/admin/support", f"/api/admin/support/{u['id']}"):
        assert c.get(path).status_code == 403
    assert c.post(f"/api/admin/support/{u['id']}/reply", json={"message": "x"}).status_code == 403
    # oturumsuz: yazışma yok, rozet 0
    from fastapi.testclient import TestClient

    from app.api.main import app

    anon = TestClient(app)
    assert anon.get("/api/support/unread").status_code in (200, 401)
    # oturumsuz/demo bildirimleri yöneticide uid 0 altında, yanıtlanamaz
    assert hosted.post("/api/admin/support/0/reply", json={"message": "x"}).status_code == 409
    assert hosted.get("/api/admin/support/0").status_code == 200


def test_report_rate_limit_and_validation(hosted, ai, monkeypatch):  # noqa: F811
    from app import feedback

    c, _ = _user(hosted)
    row = _up(c, "ekstre.pdf", text_pdf(UNRELATED_LINES), "application/pdf")
    assert c.post("/api/support/report", data={"report": "{}", "send_doc": "true"}).status_code == 422  # dosya yok
    img = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    assert c.post("/api/support/report", data={"report": "{}", "send_doc": "true"},
                  files={"file": ("x.jpg", img, "image/jpeg")}).status_code == 422
    # bilinmeyen alanlar atılır, kod bilinmiyorsa "okunamadi"
    r = c.post("/api/support/report", data={"report": json.dumps({"code": "uydurma", "kart": "5400"}), "bank_name": "Banka 5400123412341234"})
    assert r.status_code == 200
    th = c.get("/api/support").json()
    assert th["messages"][-1]["report"]["code"] == "okunamadi" and "5400123412341234" not in th["messages"][-1]["body"]
    monkeypatch.setattr(feedback, "RATE_MAX", 1)
    assert _report(c, row).status_code == 429


def test_pending_cap_delete_and_nightly_prune(hosted, ai, monkeypatch):  # noqa: F811
    from app import support

    c, u = _user(hosted)
    monkeypatch.setattr(support, "MAX_PENDING", 1)
    row = _up(c, "a.pdf", text_pdf(STMT_LINES), "application/pdf")
    assert _report(c, row, send_doc=True, data=text_pdf(STMT_LINES)).status_code == 200
    # aynı içerik: yer var (süre yenilenir)
    assert _report(c, row, send_doc=True, data=text_pdf(STMT_LINES)).status_code == 200
    other = text_pdf(STMT_LINES + ["EK SAYFA"])
    assert _report(c, row, send_doc=True, data=other).status_code == 409
    items = c.get("/api/support/pending").json()["items"]
    assert len(items) == 1
    assert c.delete(f"/api/support/pending/{items[0]['id']}").status_code == 200
    assert c.delete(f"/api/support/pending/{items[0]['id']}").status_code == 404

    assert _report(c, row, send_doc=True, data=other).status_code == 200
    from app import storage

    with support.own_space(u["id"]):
        stored = support.pending_get(support.pending_list()[0]["id"])["name"]
        assert storage.book_get("pending", stored)
        assert support.prune_pending() == 0
        later = datetime.now(timezone.utc) + timedelta(days=31)
        assert support.prune_pending(later) == 1
        assert support.pending_list() == [] and storage.book_get("pending", stored) is None


def test_pending_job_registered_for_every_book():
    from app import jobs

    assert "pending" in {n for n, _, _ in jobs.JOBS} and "pending" not in jobs.MAIN_ONLY


def test_account_delete_forgets_thread(hosted):
    from app import system

    c, u = _user(hosted)
    c.post("/api/feedback", data={"message": "Silinecek"})
    hosted.post(f"/api/admin/support/{u['id']}/reply", json={"message": "Tamam"})
    system.delete_user(u["id"])
    with system.connect() as cn:
        assert cn.execute("SELECT COUNT(*) FROM feedback WHERE user_id = ?", (u["id"],)).fetchone()[0] == 0
        assert cn.execute("SELECT COUNT(*) FROM support_threads WHERE user_id = ?", (u["id"],)).fetchone()[0] == 0


def test_member_report_goes_to_own_book(owner, ai):  # noqa: F811
    """Ortak defter üyesi: bekleyen dosya üyenin KENDİ ("Benim") defterinde, ortak defterde değil."""
    from app import support, system

    from tests.test_book import _member

    m = _member(owner, "esim", role="viewer")
    uid = system.user_by_name("esim")["id"]
    r = m.post("/api/support/report", data={"report": json.dumps({"code": "ekstre_bulunamadi"}), "send_doc": "true"},
               files={"file": ("e.pdf", text_pdf(STMT_LINES), "application/pdf")})
    assert r.status_code == 200, r.text
    with support.own_space(uid):
        assert len(support.pending_list()) == 1
    # sahibin defterinde bekleyen yok
    assert owner.get("/api/support/pending").json()["items"] == []


def test_capabilities_and_push_type(hosted):
    caps = hosted.get("/api/meta").json()["capabilities"]
    assert {"support_chat", "feedback_reply", "upload_report", "upload_retry"} <= set(caps)
    c, _ = _user(hosted)
    prefs = c.get("/api/push/prefs").json()
    assert prefs["labels"]["destek"] == "Destek yanıtları" and prefs["types"]["destek"] is True


def test_anonymize_amounts_flag_keeps_default():
    from app import card_reader

    t = "Donem Borcu 12.345,67\n01.11.2026 ORNEK MARKET 1,234.56"
    assert "12.345,67" in card_reader.anonymize(t)  # gönüllü aracı: tutarlar kalır (eski davranış)
    masked = card_reader.anonymize(t, amounts=True)
    assert "12.345,67" not in masked and "Borcu 99.999,99" in masked and " 9,999.99" in masked and "01.11.2026" in masked
    assert card_reader.anonymize("Toplam -1.234,56", amounts=True).endswith(" -9.999,99")
