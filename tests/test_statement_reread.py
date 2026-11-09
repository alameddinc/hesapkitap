"""Kayıtlı (yapay zekâ okuması) kart ekstresini şablonla yeniden oku (app/statement_reread.py). UYDURMA veri
(tests/card_layouts.py): Albaraka benzeri taksitli iade harcama yazılmış; İş Bankası benzeri ödeme satırı listede yok
(özet doğru: "Kontrol et" çıkmıyor). Önizleme farkı düz dille verir; uygulama kullanıcının düzenlemelerini korur, tek
geri al kaydıdır; ortak defterde yalnız sahip / tam yetkili; yapay zekâ çağrılmaz, okuma hakkı düşmez."""
from dataclasses import replace

import pytest

from app import card_reader, db, statement_reread as rr, templates, undo
from app.parsers.base import PAYMENT, REFUND
from tests import card_layouts as L
from tests.test_card_learning import _ai
from tests.test_card_templates import pdf  # noqa: F401 - fixture


def _no_ai(monkeypatch):
    """Yapay zekâ ya da okuma hakkı yoluna girilirse test düşer."""
    from app import doc_reads, receipt

    def boom(*a, **k):
        raise AssertionError("yapay zekâ / okuma hakkı kullanılmamalı")
    monkeypatch.setattr(receipt, "_gemini_json", boom)
    monkeypatch.setattr(doc_reads, "charge", boom)
    monkeypatch.setattr(doc_reads, "check", boom)


def _wrong_sign(lay):
    reply = lay.reply()
    return {**reply, "transactions": [{**t, "type": "harcama"} if "TAKSITLIIADE" in t["description"] else t
                                      for t in reply["transactions"]]}


def _save(pdf, key: str, lay, **reply) -> int:  # noqa: F811
    pdf[key] = lay.text
    st = replace(_ai(lay, **reply), source="yapay_zeka")
    return db.save_statement(f"STMT:{key}".encode(), f"{key}.pdf", st)


def _albaraka(pdf):  # noqa: F811
    lay = L.albaraka_glued()
    return lay, _save(pdf, "alb", lay, **_wrong_sign(lay))


def _isbank(pdf):  # noqa: F811
    lay = L.isbank_glued()
    reply = lay.reply()
    return lay, _save(pdf, "is", lay, **{**reply, "transactions": [t for t in reply["transactions"] if t["type"] != "odeme"]})


def _rows(sid: int) -> list[dict]:
    with db.connect() as c:
        return db.rows(c.execute("SELECT * FROM transactions WHERE statement_id = ? ORDER BY id", (sid,)))


def _stmt(sid: int) -> dict:
    return next(s for s in db.list_statements() if s["id"] == sid)


# ---------------------------------------------------------------- önizleme

def test_preview_wrong_sign_refund(tmpdb, pdf, monkeypatch):  # noqa: F811
    _no_ai(monkeypatch)
    lay, sid = _albaraka(pdf)
    p = rr.preview(sid)
    assert p["available"] and p["rows"] == 1 and p["equation_ok_after"] and p["via"] == "sablon"
    assert p["diff"]["added"] == [] and p["diff"]["removed"] == []
    ch = p["diff"]["changed"][0]
    assert (ch["before"]["amount"], ch["after"]["amount"], ch["after"]["txn_type"]) == (137.40, -137.40, REFUND)
    assert [i["label"] for i in p["items"]] == ["İade olarak düzeltilecek"]
    assert p["items"][0]["text"] == "ORNEKELEKTRONIKTAKSITLIIADE · 25.08.2026 · −₺137,40"
    assert p["totals_before"]["equation_diff"] == round(2 * 137.40, 2) and p["totals_after"]["equation_diff"] == 0
    assert p["totals_after"]["refunds"] == round(p["totals_before"]["refunds"] - 137.40, 2)


def test_preview_uses_saved_template_and_good_read_is_left_alone(tmpdb, pdf):  # noqa: F811
    lay, sid = _albaraka(pdf)
    ai = _ai(lay, **_wrong_sign(lay))
    (spec, _), why = card_reader.explain_from_ai(lay.text, ai)
    assert why == "ok_fixed" and templates.save(spec)
    assert rr.preview(sid)["available"]
    good = L.albaraka_glued(cut="08/11/2026", due="18/11/2026")
    gid = _save(pdf, "good", good)
    assert rr.preview(gid) == {**rr.preview(gid), "available": False, "reason": "equation_ok"}


def test_isbank_payment_missing_is_surfaced_without_check_warning(tmpdb, pdf):  # noqa: F811
    lay, sid = _isbank(pdf)
    s = _stmt(sid)
    assert not s["needs_check"]  # özet doğru: "Kontrol et" uyarısı yok…
    p = rr.preview(sid)  # …ama işlem listesinde ödeme satırı eksik
    assert p["available"] and p["rows"] == 1
    assert [(i["kind"], i["label"]) for i in p["items"]] == [("added", "Eksik ödeme eklenecek")]
    assert p["diff"]["added"][0]["amount"] == -lay.prev and p["diff"]["added"][0]["txn_type"] == PAYMENT


def test_unreadable_or_other_document_is_not_offered(tmpdb, pdf):  # noqa: F811
    lay, sid = _albaraka(pdf)
    pdf["alb"] = L.isbank_glued().text  # depodaki PDF başka bir belge
    assert rr.preview(sid)["available"] is False
    pdf["alb"] = "okunamayan metin"
    assert rr.preview(sid)["reason"] == "no_read"
    with db.connect() as c:
        c.execute("UPDATE statements SET source = 'sablon' WHERE id = ?", (sid,))
    assert rr.preview(sid)["reason"] == "not_ai"


# ---------------------------------------------------------------- önbellek, Yapılacaklar, liste

def test_scan_caches_and_reminder_and_statement_flag(tmpdb, pdf, monkeypatch):  # noqa: F811
    from app import reminders

    _no_ai(monkeypatch)
    lay, sid = _albaraka(pdf)
    assert rr.todos() == []  # önbellek yok: hesap yapılmaz
    got = rr.scan(rr.fingerprint())
    assert got == {"checked": 1, "found": 1, "finished": True}
    assert rr.scan(rr.fingerprint())["checked"] == 0  # imza aynı: yeniden hesaplanmaz
    item = next(i for i in reminders.build() if i["kind"] == "ekstre_oku")
    assert item["title"] == "Ekstre yeniden okunabilir" and item["cta"] == "Farkı gör"
    assert item["text"] == "Albaraka Türk ••6631 · 1 satır farklı" and item["statement_id"] == sid
    assert rr.annotate([{"id": sid}])[0]["reread_rows"] == 1
    # düzeltmesi hazır ekstrede "kontrol et" maddesi ayrıca çıkmaz
    assert not any(i.get("action") == "statement_confirm" and i.get("statement_id") == sid for i in reminders.build())
    # kullanıcı satırı düzeltirse (imza değişti) eski sonuç gösterilmez
    with db.connect() as c:
        c.execute("UPDATE transactions SET is_deleted = 1 WHERE id = (SELECT MIN(id) FROM transactions WHERE statement_id = ?)", (sid,))
    assert rr.todos() == []
    with db.connect() as c:
        c.execute("UPDATE transactions SET is_deleted = 0 WHERE statement_id = ?", (sid,))
    # "Şimdi değil": kişiye özel kapatma
    reminders.dismiss(rr.reminder_id(sid))
    assert rr.todos() == [] and rr.annotate([{"id": sid}])[0]["reread_rows"] is None


def test_tick_runs_over_spaces_with_budget(tmpdb, pdf, monkeypatch):  # noqa: F811
    _albaraka(pdf)
    rr._last.update(at=0.0, fp=None)
    got = rr.tick(now=1e9, spaces=[(None, None)])
    assert got == {"checked": 1, "found": 1, "finished": True}
    assert rr.tick(now=1e9 + 60, spaces=[(None, None)]) is None  # sıklık dolmadı, şablon listesi aynı


# ---------------------------------------------------------------- uygulama: düzenlemeler korunur, geri alınır

def test_apply_preserves_user_edits_and_undo(tmpdb, pdf, monkeypatch):  # noqa: F811
    _no_ai(monkeypatch)
    lay, sid = _isbank(pdf)
    before = _rows(sid)
    market = next(r for r in before if r["description"] == "ORNEK MARKET")
    kafe = next(r for r in before if r["description"] == "ORNEK KAFE")
    db.update_card_transaction(market["id"], {"category": "Eğlence", "note": "elle not"})
    db.set_splits("card", market["id"], [{"amount": 100.0, "category": "Sağlık"}])
    db.set_spender_override("card", kafe["id"], None)
    eid = db.create_entry({"entry_date": "2026-09-14", "description": "Kafe elle", "amount": 85.0, "kind": "gider",
                           "category": "Yeme İçme", "payment": "kredi_karti"})
    db.link_entry(eid, kafe["id"])
    p = rr.preview(sid)

    res = rr.apply(sid, p["sig"])
    assert res["ok"] and res["added"] == 1 and res["changed"] == 0 and res["removed"] == 0 and res["undo_id"]
    after = _rows(sid)
    assert {r["id"] for r in before} <= {r["id"] for r in after}  # eski satırlar silinip yeniden yazılmadı
    m2 = next(r for r in after if r["id"] == market["id"])
    assert (m2["category"], m2["category_manual"], m2["note"]) == ("Eğlence", 1, "elle not")
    assert db.splits_map()[("card", market["id"])][0]["category"] == "Sağlık"
    assert db.spender_overrides()[("card", kafe["id"])] is None
    with db.connect() as c:
        assert c.execute("SELECT linked_txn_id FROM entries WHERE id = ?", (eid,)).fetchone()[0] == kafe["id"]
    pay = [r for r in after if r["txn_type"] == PAYMENT]
    assert [r["amount"] for r in pay] == [-lay.prev]
    s = _stmt(sid)
    assert s["source"] == "sablon" and rr.preview(sid)["reason"] == "not_ai"
    assert not any(i["kind"] == "ekstre_oku" for i in __import__("app.reminders").reminders.build())

    assert undo.undo(res["undo_id"])["ok"]
    assert [r["id"] for r in _rows(sid)] == [r["id"] for r in before]
    assert _stmt(sid)["source"] == "yapay_zeka"


def test_apply_sign_flip_unlinks_entry_and_rejects_stale_sig(tmpdb, pdf):  # noqa: F811
    lay, sid = _albaraka(pdf)
    inst = next(r for r in _rows(sid) if "TAKSITLIIADE" in r["description"])
    eid = db.create_entry({"entry_date": "2026-08-25", "description": "Elektronik", "amount": 137.40, "kind": "gider",
                           "category": "Elektronik", "payment": "kredi_karti"})
    db.link_entry(eid, inst["id"])
    p = rr.preview(sid)
    assert p["items"][0]["note"] == rr.UNLINK_NOTE
    with pytest.raises(rr.RereadError):
        rr.apply(sid, "eski-imza")
    res = rr.apply(sid, p["sig"])
    assert res["unlinked"] == 1
    r = next(x for x in _rows(sid) if x["id"] == inst["id"])
    assert (r["amount"], r["txn_type"]) == (-137.40, REFUND)  # aynı satır, işaret düzeldi
    with db.connect() as c:
        assert c.execute("SELECT linked_txn_id FROM entries WHERE id = ?", (eid,)).fetchone()[0] is None
    assert _stmt(sid)["source"] == "sablon"
    with pytest.raises(rr.RereadError):
        rr.apply(sid)  # artık şablon kaynaklı


# ---------------------------------------------------------------- API ve ortak defter yetkisi

def test_api_preview_apply_and_undo(client, pdf):  # noqa: F811
    lay, sid = _albaraka(pdf)
    p = client.get(f"/api/statements/{sid}/reread-preview").json()
    assert p["available"] and p["items"][0]["label"] == "İade olarak düzeltilecek"
    assert client.get("/api/statements/999/reread-preview").status_code == 404
    assert client.post(f"/api/statements/{sid}/reread-apply", json={"sig": "x"}).status_code == 409
    r = client.post(f"/api/statements/{sid}/reread-apply", json={"sig": p["sig"]}).json()
    assert r["ok"] and r["changed"] == 1
    assert client.post(f"/api/imports/{r['undo_id']}/undo").json()["ok"]
    assert next(s for s in client.get("/api/statements").json() if s["id"] == sid)["source"] == "yapay_zeka"
    from app import version

    assert "statement_reread" in version.CAPABILITIES


@pytest.mark.parametrize("role,ok", [("viewer", False), ("editor", False), ("full", True)])
def test_shared_book_permissions(client, pdf, monkeypatch, role, ok):  # noqa: F811
    from tests.test_book import _member

    from app import auth

    monkeypatch.delenv("HESAPKITAP_NO_AUTH", raising=False)
    auth._fails.clear()
    auth.create_admin("admin", "yonetici-sifre-1")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "yonetici-sifre-1"}).status_code == 200
    lay, sid = _albaraka(pdf)
    rr.scan(rr.fingerprint())
    m = _member(client, role=role)
    assert any(i["kind"] == "ekstre_oku" for i in client.get("/api/reminders").json())  # sahip görür
    assert any(i["kind"] == "ekstre_oku" for i in m.get("/api/reminders").json()) is ok
    assert (m.get(f"/api/statements/{sid}/reread-preview").status_code == 200) is ok
    r = m.post(f"/api/statements/{sid}/reread-apply", json={})
    assert (r.status_code == 200) is ok, r.text
    # gizli kart: sahip kartı gizlerse tam yetkili üye de göremez (404)
    if ok:
        undo.undo(r.json()["undo_id"])
        client.put("/api/book/rules", json={"hidden_cards": [_stmt(sid)["card_no"]]})
        assert m.get(f"/api/statements/{sid}/reread-preview").status_code == 404
