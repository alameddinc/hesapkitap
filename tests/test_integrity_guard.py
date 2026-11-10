"""Doğruluk denetimi bekçisi: desteklenen her okuyucunun (özel parser: VakıfBank, Kuveyt Türk; yapay zekâdan öğrenilen
şablon: Akbank, İş, Albaraka, Halkbank, Garanti benzeri) uydurma ekstreleri deftere kaydedilir; denetim sıfır ekstre
uyarısı vermeli, özet alanları (denklemin girdileri) sabitlenmiş değerlerle aynı kalmalı ve okuma → kayıt → liste yolu
denklem farkını değiştirmemeli. Ortak defter üyesinin görünümünde (faiz gizli) de sonuç aynı olmalı: 10.11.2026'da
üyenin alanında çalıştırılan denetim faizi 0 görüp ayrı faizli VakıfBank ekstrelerini ihlal saydı.

Bir kod değişikliği okumayı ya da denklemi bozarsa bu dosya CI'da düşer; canlıda ise güncelleme sonrası denetim
(integrity.post_deploy_tick) yeni ekstre uyarılarını yöneticiye bildirir. Veriler uydurmadır."""
import pytest

from app import book, db, integrity, parsers
from app.parsers.base import equation
from tests import card_layouts as L
from tests.conftest import fixture_bytes
from tests.test_card_templates import gemini, pdf  # noqa: F401 - fixture

EKSTRE = ("ekstre_denklem", "ekstre_satir_farki")
MEMBER = {"id": 2, "member_of": 1, "book_role": "editor"}


def _summary(st) -> tuple:
    return (st.bank, st.period_end.isoformat(), st.previous_balance, st.payments, st.period_activity,
            st.interest_fees, st.total_debt)


def _assert_book_clean(stmts: list) -> None:
    """Kaydet → listele: denklem farkı okumadakiyle aynı ve < 1 TL; denetim (sahip ve faizi gizli üye) temiz."""
    ids = {db.save_statement(f"b{i}".encode(), f"e{i}.pdf", st): st for i, st in enumerate(stmts)}
    listed = {s["id"]: s for s in db.list_statements()}
    for sid, st in ids.items():
        want = equation(st.previous_balance, st.payments, st.period_activity, st.fees(), st.total_debt)
        assert listed[sid]["equation_diff"] == want["diff"] and want["ok"], (st.bank, want, listed[sid])
    res = integrity.check()
    assert not {k: v for k, v in res["refs"].items() if k in EKSTRE}, res["refs"]
    book.set_rules({"hide_fees": True})
    with db.use_space(None, MEMBER):
        assert book.hide_fees()
        assert integrity.check()["refs"] == res["refs"]


GOLDEN = {  # okuyucunun ürettiği özet (denklemin girdileri): değişirse bilerek güncellenir
    "vakifbank_a.txt": ("VakıfBank", "2026-10-13", 20000.0, 20000.0, 17752.21, 0.0, 17752.21),
    "vakifbank_b.txt": ("VakıfBank", "2026-10-16", 15000.0, 15000.0, 17204.2, 676.4, 17880.6),  # faiz ayrı satır
    "kuveytturk.txt": ("Kuveyt Türk", "2026-10-15", 10000.0, 10000.0, 7102.38, 0.0, 7102.38),
}


def test_dedicated_parsers_keep_summary_and_clean_integrity(tmpdb):
    stmts = [parsers.parse_statement(fixture_bytes(n), allow_ai=False) for n in GOLDEN]
    for n, st in zip(GOLDEN, stmts):
        assert st.source == "parser", n
        assert _summary(st) == GOLDEN[n], n
    _assert_book_clean(stmts)


LAYOUTS = {
    "akbank": (L.akbank(), L.akbank(cut="12.11.2026", due="22.11.2026", prev=3100.00, month=10)),
    "isbank": (L.isbank(), L.isbank(cut="05.11.2026", due="15.11.2026", prev=1420.90)),
    "albaraka": (L.albaraka(), L.albaraka(cut="08/11/2026", due="18/11/2026")),
    "halkbank": (L.halkbank(), L.halkbank(cut="01/12/2026", due="11/12/2026", prev=3100.00, month=11)),
    "garanti": (L.garanti(), L.garanti(cut="27.11.2026", due="06.12.2026", prev=3100.00, month=11)),
}


@pytest.mark.parametrize("bank", LAYOUTS)
def test_learned_templates_keep_equation_and_clean_integrity(tmpdb, gemini, pdf, bank):  # noqa: F811
    first, second = LAYOUTS[bank]
    pdf["a"], pdf["b"] = first.text, second.text
    gemini["replies"].append(first.reply())
    st1 = parsers.parse_statement(b"STMT:a")
    st2 = parsers.parse_statement(b"STMT:b")
    assert (st1.source, st2.source) == ("yapay_zeka", "sablon"), bank
    assert st2.total_debt == second.total and st2.previous_balance == second.prev, bank
    _assert_book_clean([st1, st2])


def test_post_deploy_check_notifies_admin_only_for_new_statement_warnings(tmpdb, monkeypatch):
    """Güncelleme sonrası denetim: son gece koşusunda olmayan ekstre uyarısı → yöneticiye tek "sistem" bildirimi
    (yalnız sayı); aynı sürümde tekrar çalışmaz; gece koşusundan sonra yüklenen ekstre sayılmaz; deftere yazmaz."""
    from app import feedback, push

    st = parsers.parse_statement(fixture_bytes("vakifbank_b.txt"), allow_ai=False)
    db.save_statement(b"x1", "e1.pdf", st)
    integrity.run()  # gece koşusu: temiz
    sent = []
    monkeypatch.setattr(feedback, "admins", lambda: [{"id": 1}])
    monkeypatch.setattr(push, "notify", lambda uid, items, **k: sent.append((uid, items)) or items)
    monkeypatch.setenv("APP_VERSION", "aaa111")
    assert integrity.post_deploy_tick([(None, None)])["new"] == 0 and not sent  # gerileme yok: bildirim yok
    assert integrity.post_deploy_tick([(None, None)]) is None  # aynı sürüm: bir kez

    # yeni sürüm ekstreyi bozuyor (ör. okuyucu yeni borcu 100 TL kaydırıyor): özet de satırlar da tutmaz
    with db.connect() as c:
        c.execute("UPDATE statements SET total_debt = total_debt + 100")
    monkeypatch.setenv("APP_VERSION", "bbb222")
    before = db.get_setting(integrity.SETTING)
    r = integrity.post_deploy_tick([(None, None), (None, MEMBER)])  # üyenin alanı aynı dosya: bir kez sayılır
    assert (r["new"], r["books"], r["finished"]) == (1, 1, True)
    [(uid, [item])] = sent
    assert uid == 1 and item["kind"] == "sistem" and item["title"] == "Denetim: güncelleme sonrası"
    assert item["body"].startswith("1 yeni ekstre uyarısı")
    assert db.get_setting(integrity.SETTING) == before  # son koşu ve defter değişmedi

    with db.connect() as c:  # gece koşusundan sonra yüklenen ekstre: güncellemeden değil, sayılmaz
        c.execute("UPDATE statements SET uploaded_at = '2999-01-01 00:00:00'")
    monkeypatch.setenv("APP_VERSION", "ccc333")
    assert integrity.post_deploy_tick([(None, None)])["new"] == 0
