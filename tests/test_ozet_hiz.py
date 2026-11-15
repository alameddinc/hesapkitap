"""Özet açılış hızı (perf/ozet-acilis): ortak hesap önbelleği rakamları değiştirmez.

- zekât ayrımından bağımsız hesaplar (satırlar, plan, ödemeler, taksitler) ?zakat=1 ile ve onsuz aynı sonucu verir
  (önbellek anahtarında zekât yok: Özet zakat=1, Net durum / Ödemeler zakat'sız ister, iki kez hesaplanmıyordu);
- kısa plan (plan(3), plan(1)) 6 aylık planın ilk ayları ile birebir aynı (ayrıca hesaplanmaz);
- ekstre listesi damga değişmedikçe bir kez okunur; çağıranın değişikliği önbelleğe sızmaz, yazınca tazelenir;
- Özet açılışının bütün uçları ortak önbellekle de, her biri boş önbellekle de aynı yanıtı verir;
- hesap önbelleği dolunca en uzun süredir kullanılmayan kayıt düşer (LRU): bir kişinin açılışı ötekinin önbelleğini
  boşaltmaz; yazandan kalan eski damgalı kayıtlar okunmaz, en eski olarak düşer.
Veriler uydurma (sahte ekstreler, kurgu adlar)."""
import json
from collections import OrderedDict
from datetime import date, timedelta

import pytest

from app import db, finance

TODAY = date.today()


def _clear():
    finance._CACHE.clear()
    finance._LOCKS.clear()
    with db._CACHE_LOCK:
        db._READ_CACHE.clear()


@pytest.fixture()
def dolu(client, upload, monkeypatch):
    """Ekstreler + düzenli ödeme + taksitli kart harcaması + fatura + borç + kumbara + bu dönem zekât ödemesi (zekât
    ayrımı gerçekten bir şeyi değiştirsin). Altın fiyatı dışarıdan çekilmez."""
    from app import zakat

    def offline():
        raise RuntimeError("test: ağ yok")
    monkeypatch.setattr(zakat, "_download_prices", offline)
    upload()
    d = TODAY.isoformat()
    ok = lambda r: r.status_code == 200 or pytest.fail(r.text)  # noqa: E731
    ok(client.post("/api/recurring", json={"description": "Maaş", "amount": 90000, "kind": "gelir", "category": "Maaş",
                                           "day_of_month": 15, "start_month": "2026-01"}))
    ok(client.post("/api/recurring", json={"description": "Kira", "amount": 20000, "kind": "gider",
                                           "category": "Kira & Aidat", "day_of_month": 5, "start_month": "2026-01"}))
    ok(client.post("/api/entries", json={"entry_date": d, "description": "Örnek Beyaz Eşya", "amount": 12000,
                                         "kind": "gider", "category": "Elektronik", "payment": "kredi_karti",
                                         "installments": 6}))
    ok(client.post("/api/entries", json={"entry_date": d, "description": "ORNEK MARKET", "amount": 450,
                                         "kind": "gider", "category": "Market & Gıda"}))
    ok(client.post("/api/bills", json={"provider": "Örnek Elektrik", "amount": 640.5, "category": "Elektrik",
                                       "due_date": (TODAY + timedelta(days=6)).isoformat()}))
    ok(client.post("/api/debts", json={"person": "Kurgu Kişi", "direction": "borc", "amount": 5000,
                                       "debt_date": d, "due_date": (TODAY + timedelta(days=20)).isoformat()}))
    ok(client.post("/api/jars", json={"name": "Tatil", "goal": 20000}))
    ok(client.post("/api/donations", json={"kind": "sadaka", "amount": 300, "given_date": d, "payment": "nakit"}))
    me = next(p for p in client.get("/api/zakat").json()["people"] if p["is_self"])["id"]
    client.patch(f"/api/zakat/people/{me}", json={"hawl_start": "2026-03-01"})
    ok(client.post(f"/api/zakat/people/{me}/payments", json={"amount": 2000, "pay_date": d, "payment": "havale"}))
    return client


def _both(fn):
    """fn() zekât ayrımı kapalı ve açıkken, her seferinde boş önbellekle."""
    _clear()
    with finance.zakat_split(False):
        off = fn()
    _clear()
    with finance.zakat_split(True):
        on = fn()
    return off, on


def test_veri_zekati_ayiriyor(dolu):
    """Ön koşul: bu veride ayrım kırılımı gerçekten değiştiriyor (yoksa aşağıdaki eşitlik bir şey denemez)."""
    off, on = _both(lambda: finance.summary(finance.current_period())["by_category"])
    assert off != on
    assert any(g["name"] == finance.ZAKAT_GROUP for g in on)


@pytest.mark.parametrize("ad, cagir", [
    ("_all_rows bu dönem", lambda: finance._all_rows(finance.current_period())),
    ("_all_rows tümü", lambda: finance._all_rows(None)),
    ("plan 6", lambda: finance.plan(6)),
    ("plan 12", lambda: finance.plan(12)),
    ("ödemeler", lambda: finance.payables()),
    ("taksitler", lambda: finance.installments_due(finance.current_period())),
    ("aylar", lambda: finance.available_months()),
    ("sonraki kesim", lambda: [finance._next_cut(s) for s in db.list_statements()]),
])
def test_zekatsiz_onbellek_iki_kipte_ayni(dolu, ad, cagir):
    off, on = _both(cagir)
    assert off == on, ad


def test_zekatsiz_isaretli_hesaplar_kirilima_dokunmuyor():
    """zakat=False ile önbelleğe alınan hesaplar kategori kırılımı (report_category / _group "category") yapmaz.
    Yeni bir kırılım eklenirse bu liste güncellenmeli (yoksa ?zakat=1 ile ve onsuz aynı sonuç paylaşılır)."""
    import ast
    import inspect

    zekatli = {"report_category", "_group", "_group_categories", "summary", "category_spend", "insights", "budgets",
               "month_digest", "member_spend", "year_summary", "day_detail", "budget_split"}
    src = ast.parse(inspect.getsource(finance))
    funcs = {n.name: n for n in src.body if isinstance(n, ast.FunctionDef)}

    def cagrilar(name, seen):
        if name in seen or name not in funcs:
            return set()
        seen.add(name)
        out = set()
        for c in ast.walk(funcs[name]):
            if isinstance(c, ast.Name):
                out.add(c.id)
                out |= cagrilar(c.id, seen)
        return out

    for name in ("_all_rows", "available_months", "_next_cut", "installments_due", "_plan", "_payables"):
        assert not (cagrilar(name, set()) & zekatli), name


def test_kisa_plan_uzunun_ilk_aylari(dolu):
    _clear()
    for k in (1, 2, 3, 5):
        full = finance._plan.__wrapped__(k, TODAY)  # eski yol: k ay ayrıca hesaplanır
        assert finance.plan(k) == full, k
        assert finance.plan(k, today=TODAY) == full, k
    assert finance.plan(0)["months"] == []
    assert finance.plan(6) == finance._plan.__wrapped__(6, TODAY)


def test_odemeler_bugunle_ayni_kayit(dolu):
    _clear()
    a = finance.payables()
    b = finance.payables(TODAY)
    assert a == b == finance._payables.__wrapped__(TODAY)
    assert sum(1 for k in finance._CACHE if k[0] == "_payables") == 1


def test_ekstre_listesi_onbellekte_bozulmaz(dolu):
    _clear()
    fresh = db._load_statements()
    a = db.list_statements()
    assert a == fresh
    a[0]["bank"] = "DEĞİŞTİ"
    a.pop()
    assert db.list_statements() == fresh  # çağıranın değişikliği önbelleğe geçmez
    sid = fresh[0]["id"]
    db.confirm_statement(sid)  # yazma: önbellek tazelenir
    assert next(s for s in db.list_statements() if s["id"] == sid)["confirmed_at"]


OZET = ["/api/meta", "/api/summary?zakat=1", "/api/net-worth", "/api/payables", "/api/compare?all=true", "/api/digest",
        "/api/jars", "/api/transactions?month={cur}", "/api/debt-plan", "/api/donations/reminder",
        "/api/payables?totals=1&days=15", "/api/cards/summary?recent=20", "/api/debts", "/api/summary",
        "/api/summary?month={prev}&zakat=1", "/api/compare?month={prev}&all=true", "/api/plan?months=3",
        "/api/installments"]


def test_ozet_acilisi_ortak_onbellekle_ayni_yanit(dolu):
    cur = finance.current_period()
    urls = [u.format(cur=cur, prev=finance.add_months(cur, -1)) for u in OZET]
    tek = {}
    for u in urls:
        _clear()
        r = dolu.get(u)
        assert r.status_code == 200, (u, r.text)
        tek[u] = r.json()
    _clear()
    misses = []

    class Say(OrderedDict):
        def __setitem__(self, k, v):
            misses.append(k[0])
            super().__setitem__(k, v)
    orig, finance._CACHE = finance._CACHE, Say()
    try:
        for u in urls:
            assert dolu.get(u).json() == tek[u], u
    finally:
        finance._CACHE = orig
    # hız: Özet açılışında plan ve ödemeler bir kez hesaplanır (zekâtlı ve zekâtsız uçlar ortak)
    assert misses.count("_plan") == 1, misses
    assert misses.count("_payables") == 1, misses
    assert json.dumps(tek["/api/summary?zakat=1"]["totals"]) == json.dumps(tek["/api/summary"]["totals"])


# ---------------------------------------------------------------- hesap önbelleği: LRU (çok kullanıcı)

def test_lru_en_eskiyi_atar_kullanilani_tutar(monkeypatch):
    monkeypatch.setattr(finance, "_CACHE", OrderedDict())
    monkeypatch.setattr(finance, "_CACHE_MAX", 3)
    calls = []

    @finance._memo
    def kare(n):
        calls.append(n)
        return {"n": n * n}

    for n in (1, 2, 3):
        assert kare(n) == {"n": n * n}
    assert kare(1) == {"n": 1}  # 1 yeniden kullanıldı: en eski artık 2
    kare(4)  # sınır aşıldı
    names = [k[5] for k in finance._CACHE]
    assert names == ["(3,)", "(1,)", "(4,)"] and len(finance._CACHE) == 3
    calls.clear()
    assert kare(1) == {"n": 1} and kare(3) == {"n": 9} and kare(4) == {"n": 16}
    assert calls == []  # hepsi önbellekten (eski davranış: taşınca hepsi silinirdi)
    assert kare(2) == {"n": 4} and calls == [2]  # atılan yeniden hesaplanır, rakam aynı
    assert not finance._LOCKS  # tek uçuş kilitleri birikmez


def _iki_defter(tmp_path, monkeypatch, client):
    """İki ayrı defter (kişi A ve B): aynı süreçte ayrı dosyalar, ayrı damgalar."""
    from tests.conftest import STATEMENTS, fixture_bytes

    paths = {}
    for who in ("a", "b"):
        p = str(tmp_path / f"{who}.db")
        monkeypatch.setattr(db, "DB_PATH", p)
        db.init_db()
        files = [("files", (n.replace(".txt", ".pdf"), fixture_bytes(n), "application/pdf")) for n in STATEMENTS]
        assert client.post("/api/statements/upload", files=files).status_code == 200
        if who == "b":
            client.post("/api/entries", json={"entry_date": TODAY.isoformat(), "description": "ORNEK MARKET",
                                              "amount": 777, "kind": "gider", "category": "Market & Gıda"})
        paths[who] = p
    return paths


OZET_KISA = ["/api/summary?zakat=1", "/api/net-worth", "/api/payables", "/api/debt-plan", "/api/cards/summary?recent=20"]


def test_lru_bir_kisinin_acilisi_otekini_silmez(tmp_path, monkeypatch, client):
    paths = _iki_defter(tmp_path, monkeypatch, client)

    def ac(who):
        monkeypatch.setattr(db, "DB_PATH", paths[who])
        return {u: client.get(u).json() for u in OZET_KISA}

    _clear()
    a1 = ac("a")
    n_a = len(finance._CACHE)
    monkeypatch.setattr(finance, "_CACHE_MAX", n_a + 5)  # B'nin açılışı sığmaz: eski düzende ikisi birden silinirdi
    b1 = ac("b")
    a_keys = [k for k in finance._CACHE if k[1][0] == paths["a"]]
    b_keys = [k for k in finance._CACHE if k[1][0] == paths["b"]]
    assert len(finance._CACHE) == n_a + 5
    assert a_keys, "A'nın kayıtları tamamen silindi"
    assert len(b_keys) >= 5
    # rakamlar aynı: önbellekten de, boş önbellekle de
    assert ac("a") == a1 and ac("b") == b1
    _clear()
    assert ac("a") == a1 and ac("b") == b1
    assert a1["/api/summary?zakat=1"]["totals"] != b1["/api/summary?zakat=1"]["totals"]  # iki defter gerçekten ayrı


def test_lru_yazinca_eski_damgali_kayit_okunmaz_ve_duser(dolu, monkeypatch):
    _clear()
    once = dolu.get("/api/summary").json()
    old_stamp = db.stamp()
    old = [k for k in finance._CACHE if k[1] == old_stamp]
    assert old
    r = dolu.post("/api/entries", json={"entry_date": TODAY.isoformat(), "description": "ORNEK KAFE", "amount": 150,
                                        "kind": "gider", "category": "Kafe & Restoran"})
    assert r.status_code == 200
    assert db.stamp() != old_stamp  # yazma damgayı değiştirdi: eski anahtarlar bir daha eşleşmez
    sonra = dolu.get("/api/summary").json()
    assert round(sonra["totals"]["expense"] - once["totals"]["expense"], 2) == 150
    # eski kayıtlar en başta (en uzun süredir kullanılmayan): sınır gelince ilk onlar düşer
    keys = list(finance._CACHE)
    assert keys[:len(old)] == old
    monkeypatch.setattr(finance, "_CACHE_MAX", len(keys) - len(old))
    finance.available_months()  # yeni damgayla bir kayıt daha: sınırı aşar
    assert not [k for k in finance._CACHE if k[1] == old_stamp]
    assert dolu.get("/api/summary").json() == sonra
