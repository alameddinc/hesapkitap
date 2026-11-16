"""Ay dönümü istenmeden 17 → 16 oldu (16.11 canlı): öneri kuralı 'kartların olağan kesim gününün en geçi + 1' olunca
ilk ekstre yüklemesi günü değiştirip bütün kayıtların dönemini yeniden hesapladı. Histerezis: 1 günlük fark yalnız
yeni kart ya da kesim gününün GERÇEKTEN değişmesiyle (son iki ekstre) uygulanır; ≥2 gün fark doğrudan. Otomatik
değişiklik kaydedilir, geri alınabilir. Kart numaraları, bankalar ve tutarlar uydurma."""
from datetime import date, timedelta

from app.parsers.base import PURCHASE, Statement, Transaction

CARD_A, CARD_B, CARD_C = "4543***1111", "5400***2222", "5400***3333"


def _stmt(db, card: str, pe: str) -> int:
    d = date.fromisoformat(pe)
    s = Statement(bank="Örnek Bank", card_no=card, period_end=d, due_date=d + timedelta(days=10), total_debt=1000.0,
                  min_payment=200.0, credit_limit=50000, period_activity=1000.0)
    s.transactions = [Transaction(txn_date=d.replace(day=5), description="ORNEK MARKET", amount=1000.0,
                                  txn_type=PURCHASE, card=card)]
    return db.save_statement(f"st-{card}-{pe}".encode(), "e.pdf", s)


def _base(db, day: int) -> None:
    """Kesimler 13 (A) ve 15 (B); ay dönümü otomatik modda ama daha önce `day` olarak kurulmuş."""
    db.set_setting("cycle_mode", "auto")
    for pe in ("2026-03-13", "2026-04-13", "2026-05-13"):
        _stmt(db, CARD_A, pe)
    for pe in ("2026-04-15", "2026-05-15", "2026-06-15"):
        _stmt(db, CARD_B, pe)
    db.set_cycle_start_day(day)


def test_rule_change_alone_keeps_chosen_day(tmpdb):
    """Kural değişikliği sonrası ilk yükleme 17'yi korur (öneri 16, fark 1, kesimler değişmedi)."""
    _base(tmpdb, 17)
    assert tmpdb.suggested_cycle_day() == 16
    assert tmpdb.auto_update_cycle() is None and tmpdb.cycle_start_day() == 17
    _stmt(tmpdb, CARD_B, "2026-07-15")  # yeni ekstre, aynı kesim günü
    assert tmpdb.auto_update_cycle() is None and tmpdb.cycle_start_day() == 17
    assert tmpdb.cycle_auto_change() is None


def test_real_cut_change_applies_after_two_statements(tmpdb):
    _base(tmpdb, 16)
    assert tmpdb.auto_update_cycle() is None  # teyitli kesimler kaydedilir
    _stmt(tmpdb, CARD_B, "2026-09-14")  # 15 Eylül Salı: tatil kayması değil, banka günü değiştirmiş olabilir
    assert tmpdb.suggested_cycle_day() == 15
    assert tmpdb.auto_update_cycle() is None and tmpdb.cycle_start_day() == 16  # tek ekstre yetmez
    _stmt(tmpdb, CARD_B, "2026-10-14")  # ikinci ekstre aynı yeni gün: gerçek değişim
    assert tmpdb.auto_update_cycle() == 15 and tmpdb.cycle_start_day() == 15
    ch = tmpdb.cycle_auto_change()
    assert (ch["from"], ch["to"]) == (16, 15)


def test_new_card_applies_one_day_change(tmpdb):
    _base(tmpdb, 16)
    tmpdb.auto_update_cycle()
    _stmt(tmpdb, CARD_C, "2026-06-16")  # yeni kart 16'sında kesiliyor → 17
    assert tmpdb.auto_update_cycle() == 17 and tmpdb.cycle_auto_change()["from"] == 16


def test_two_day_difference_applies_immediately(tmpdb):
    _base(tmpdb, 17)
    tmpdb.auto_update_cycle()
    _stmt(tmpdb, CARD_B, "2026-07-20")  # banka kesimi 20'ye almış: öneri 21
    assert tmpdb.auto_update_cycle() == 21


def test_first_setup_and_switch_to_auto_apply_without_hysteresis(tmpdb):
    tmpdb.set_setting("cycle_mode", "auto")
    _stmt(tmpdb, CARD_B, "2026-06-15")
    assert tmpdb.auto_update_cycle() == 16  # hiç kurulmamış: doğrudan
    assert tmpdb.cycle_auto_change() is None  # ilk kurulum "değişti" uyarısı değil
    tmpdb.set_setting("cycle_mode", "manual")
    tmpdb.set_cycle_start_day(17)
    tmpdb.set_setting("cycle_mode", "auto")
    assert tmpdb.auto_update_cycle() is None and tmpdb.cycle_start_day() == 17
    assert tmpdb.auto_update_cycle(force=True) == 16  # kullanıcı "otomatik"i seçti


def test_undo_restores_day_and_periods_via_api(client, tmpdb):
    _base(tmpdb, 16)
    tmpdb.auto_update_cycle()
    eid = tmpdb.create_entry({"entry_date": "2026-11-16", "description": "Kahve", "amount": 100, "kind": "gider",
                              "category": "Kafe & Restoran"})
    _stmt(tmpdb, CARD_C, "2026-06-16")
    assert tmpdb.auto_update_cycle() == 17
    assert client.get("/api/meta").json()["cycle_auto_change"]["from"] == 16
    r = client.post("/api/settings/cycle/undo").json()
    assert r["day"] == 16 and r["mode"] == "manual"
    assert next(e for e in tmpdb.list_entries() if e["id"] == eid)["month"] == "2026-12"  # ay dönümü 16: 16 Kasım yeni dönemde
    assert client.get("/api/meta").json()["cycle_auto_change"] is None
    assert client.post("/api/settings/cycle/undo").status_code == 404
    assert tmpdb.auto_update_cycle() is None  # elle: otomatik bir daha oynatmaz


def test_dismiss_keeps_new_day(client, tmpdb):
    _base(tmpdb, 16)
    tmpdb.auto_update_cycle()
    _stmt(tmpdb, CARD_C, "2026-06-16")
    tmpdb.auto_update_cycle()
    assert client.delete("/api/settings/cycle/change").json() == {"ok": True}
    assert tmpdb.cycle_start_day() == 17 and tmpdb.cycle_auto_change() is None
