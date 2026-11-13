"""İşyeri adından sondaki konum eki (NAME_VERSION 2) ve ada bağlı ayarların arka planda yeni ada taşınması.

Bütün işyeri adları uydurmadır ("ORNEK KITAP", "ORNEK KASAP"); gerçek kişi / ekstre yok. Yaygın dijital hizmet adı
(SPOTIFY) yalnız biçim örneği.
"""
import pytest

from tests.test_uncategorized import _card, _month


@pytest.mark.parametrize("desc,name", [
    ("IZICO /SPOTIFY.COM ISTANBUL TR", "SPOTIFY"),
    ("SPOTIFY.COM ISTANBUL TR", "SPOTIFY"),
    ("SPOTIFY.COM/ISTANBUL", "SPOTIFY"),
    ("SPOTIFY.COM", "SPOTIFY"),
    ("SPOTIFY.COM ISTANBUL TU", "SPOTIFY"),          # satır sonunda kesik ülke
    ("ORNEK KITAP ANKARA TUR", "ORNEK KITAP"),
    ("ORNEK KITAP IST", "ORNEK KITAP"),
    ("ORNEK KASAP KADIKOY ISTANBUL TR", "ORNEK KASAP"),
    ("ORNEK MARKET 34000 ISTANBUL", "ORNEK MARKET"),  # posta kodu
    ("ORNEK OYUN AMSTERDAM NL", "ORNEK OYUN"),
    ("ORNEKZINCIR KADIKOY", "ORNEKZINCIR"),           # zincir + şube: tek işyeri (eğik çizgili yazılışla aynı)
    ("ORNEKZINCIR/KADIKOY", "ORNEKZINCIR"),
    # atılmaz
    ("ISTANBUL", "ISTANBUL"),                         # yalnız konum
    ("ISTANBUL TR", "ISTANBUL TR"),
    ("ISTANBUL KART", "ISTANBUL KART"),               # konum sonda değil
    ("CAFE TAKSIM", "CAFE TAKSIM"),                   # geriye genel dükkân adı kalır
    ("ORNEK AYDIN", "ORNEK AYDIN"),                   # il adı ama sık soyadı
    ("ORNEK BAHCE", "ORNEK BAHCE"),                   # tek başına kesik konum başı sayılmaz (BAHCELIEVLER)
    ("A101 34000 ISTANBUL", "A101"),
    ("ORNEK OYUN NL", "ORNEK OYUN"),                  # yaygın ülke kodu tek başına
    ("ORNEK DUKKAN AT", "ORNEK DUKKAN AT"),           # iki harf tek başına: kelime olabilir
    ("ORNEK OYUN VIENNA AT", "ORNEK OYUN"),           # şehirden sonra ülke kodu
])
def test_trailing_location_dropped(desc, name):
    from app.textnorm import merchant_name

    assert merchant_name(desc) == name


def test_old_rule_still_available_for_migration():
    from app.textnorm import current_gateways, merchant_name_with

    gs = current_gateways()
    assert merchant_name_with("IZICO /SPOTIFY.COM ISTANBUL TR", gs, 1) == "SPOTIFY.COM ISTANBUL TR"
    assert merchant_name_with("IZICO /SPOTIFY.COM ISTANBUL TR", gs, 2) == "SPOTIFY"


def _as_old_book(db):
    """Defter eski kuralla (sürüm 1) adlandırılmış: damga yok, sistem 'bitti' demiyor."""
    from app import merchant_rekey, system

    db.set_setting(merchant_rekey.KEY_VERSION, None)
    system.put(merchant_rekey.KEY_VERSION, None)


def test_rekey_moves_settings_and_keeps_categories(tmpdb):
    from app import finance, merchant_rekey

    db = tmpdb
    m = _month()
    _card(db, [(f"{m}-03", "IZICO /SPOTIFY.COM ISTANBUL TR", 59, "Dijital Abonelik"),
               (f"{m}-04", "SPOTIFY.COM/ISTANBUL", 59, "Dijital Abonelik"),
               (f"{m}-05", "ORNEK KASAP KADIKOY ISTANBUL TR", 300, "Market & Gıda"),
               (f"{m}-06", "ORNEK KITAP ANKARA TUR", 120, "Kitap")], manual=0)
    with db.connect() as c:
        c.execute("INSERT INTO rules (pattern, category, priority) VALUES ('ORNEK KASAP', 'Market & Gıda', 10)")
        c.execute("INSERT INTO subscription_state (merchant, status, changed_at) VALUES "
                  "('SPOTIFY.COM ISTANBUL TR', 'iptal', ?)", (f"{m}-01",))
        c.execute("INSERT INTO savings_goals (kind, name, baseline, target, start_month) VALUES "
                  "('merchant', 'ORNEK KASAP KADIKOY', 300, 200, ?)", (m,))
        c.execute("INSERT INTO match_rules (source, pattern, entry_key, category, hits, used, created_at, updated_at) "
                  "VALUES ('kart', 'ORNEK KITAP ANKARA', 'e1', 'Kitap', 2, 1, ?, ?)", (f"{m}-01", f"{m}-01"))
        c.execute("INSERT INTO recurring (description, amount, kind, day_of_month, start_month, category, match_key) "
                  "VALUES ('Kitap aboneliği', 120, 'gider', 6, ?, 'Kitap', 'm:ORNEKKITAPANKARA')", (m,))
        before = [tuple(r) for r in c.execute("SELECT id, description, amount, category FROM transactions ORDER BY id")]
    _as_old_book(db)

    assert merchant_rekey.pending()
    got = merchant_rekey.tick(spaces=[(None, None)])
    assert got["finished"] and got["books"] == 1 and got["subscriptions"] == 1 and got["goals"] == 1

    states = db.subscription_states()
    assert states["SPOTIFY"]["status"] == "iptal"          # eski anahtarlı iptal yeni grupta
    subs = {s["merchant"]: s for s in finance.subscriptions()}
    assert subs["SPOTIFY"]["status"] == "iptal" and subs["SPOTIFY"]["cancelled_at"] == f"{m}-01"
    assert len([k for k in subs if k.startswith("SPOTIFY")]) == 1  # üç yazılış tek grupta
    with db.connect() as c:
        assert {r[0] for r in c.execute("SELECT name FROM savings_goals")} == {"ORNEK KASAP"}
        assert {r[0] for r in c.execute("SELECT pattern FROM match_rules")} == {"ORNEK KITAP ANKARA", "ORNEK KITAP"}
        assert c.execute("SELECT match_key FROM recurring").fetchone()[0] == "m:ORNEKKITAP"
        after = [tuple(r) for r in c.execute("SELECT id, description, amount, category FROM transactions ORDER BY id")]
    assert after == before                                  # rakam / açıklama / kategori aynen
    db.recategorize_all()
    with db.connect() as c:
        assert c.execute("SELECT category FROM transactions WHERE description LIKE 'ORNEK KASAP%'").fetchone()[0] \
            == "Market & Gıda"                              # kategori kuralı (açıklamaya bakar) bozulmadı
    # sürümlü: bir kez; tekrar koşturmak bir şey değiştirmez
    assert not merchant_rekey.pending() and merchant_rekey.tick(spaces=[(None, None)]) is None
    assert db.get_setting(merchant_rekey.KEY_VERSION) == "2"
    _as_old_book(db)
    assert merchant_rekey.tick(spaces=[(None, None)])["subscriptions"] == 0
    assert db.subscription_states() == states
    finance._CACHE.clear()


def test_merged_names_latest_state_wins(tmpdb):
    """İki eski ad aynı yeni ada iner: en son değiştirilen durum geçerli; yeni adda daha yeni durum varsa o kalır."""
    from app import merchant_rekey

    db = tmpdb
    m = _month()
    _card(db, [(f"{m}-03", "ORNEK DERGI ISTANBUL TR", 30, "Dijital Abonelik"),
               (f"{m}-04", "ORNEK DERGI ANKARA", 30, "Dijital Abonelik"),
               (f"{m}-05", "ORNEK OYUN DUBLIN IE", 40, "Dijital Abonelik")])
    with db.connect() as c:
        c.executemany("INSERT INTO subscription_state (merchant, status, changed_at, new_amount) VALUES (?, ?, ?, ?)", [
            ("ORNEK DERGI ISTANBUL", "dusuruldu", "2026-01-01", 20),   # eski kural: ilk 3 kelime
            ("ORNEK DERGI ANKARA", "iptal", "2026-03-01", None),
            ("ORNEK OYUN DUBLIN", "iptal", "2026-01-01", None),
            ("ORNEK OYUN", "dusuruldu", "2026-05-01", 25)])   # yeni adda kullanıcının daha yeni kararı
    _as_old_book(db)
    merchant_rekey.tick(spaces=[(None, None)])
    st = db.subscription_states()
    assert st["ORNEK DERGI"]["status"] == "iptal" and st["ORNEK DERGI"]["changed_at"] == "2026-03-01"
    assert st["ORNEK OYUN"]["status"] == "dusuruldu" and st["ORNEK OYUN"]["new_amount"] == 25


def test_mark_book_new_and_restored(tmp_path, tmpdb):
    """Boş defter açılışta güncel sürümle işaretlenir (taşıma yok); eski sürümlü defter gelirse sistem damgası düşer."""
    from app import db, merchant_rekey, system

    assert db.get_setting(merchant_rekey.KEY_VERSION) == "2"   # tmpdb: yeni, boş
    system.put(merchant_rekey.KEY_VERSION, "2")
    other = str(tmp_path / "u1" / "data.db")
    with db.use_space(other, {"id": 1}):
        _card(db, [(f"{_month()}-03", "ORNEK DERGI ISTANBUL TR", 30, "Dijital Abonelik")])
        db.set_subscription_state("ORNEK DERGI ISTANBUL", "iptal", f"{_month()}-01")  # eski ad (ilk 3 kelime)
        db.set_setting(merchant_rekey.KEY_VERSION, None)       # geri yüklenmiş eski defter
        db.init_db()
    assert merchant_rekey.pending()
    merchant_rekey.tick(spaces=[(None, None), (other, {"id": 1})])
    with db.use_space(other, {"id": 1}):
        assert db.subscription_states()["ORNEK DERGI"]["status"] == "iptal"
    assert not merchant_rekey.pending()


def test_gateway_sync_reuses_generic_migration(tmpdb):
    """Ödeme aracısı göçü aynı genel taşımadan geçer (migrate_name_keys); rapor anahtarı eskisi gibi."""
    import json

    db = tmpdb
    m = _month()
    _card(db, [(f"{m}-03", "PAYCO/ORNEK DERGI ISTANBUL TR", 49, "Dijital Abonelik")])
    with db.connect() as c:
        c.execute("INSERT INTO subscription_state (merchant, status, changed_at) VALUES ('PAYCO', 'iptal', ?)", (f"{m}-01",))
    rep = db.migrate_gateway_keys()
    assert rep["subscriptions"] == 1 and db.subscription_states()["ORNEK DERGI"]["status"] == "iptal"
    assert json.loads(db.get_setting("gateway_keys_v1_report"))["subscriptions"] == 1
