"""Ortak alışveriş listesi bildirimleri (app/push.py "liste"): toplu ekleme bildirimi (ekleyen başına 30 dakikada bir),
alışverişi tamamladı, yapana gitmez, tercih ve gizli sayfa. Kişiler ve ürünler uydurmadır."""
import pytest

from app import system
from tests.test_book import _member, owner  # noqa: F401  (fixture)


class _Timers:
    def __init__(self):
        self.made = []

    def __call__(self, delay, fn):
        me = self

        class T:
            daemon = True

            def start(self):
                me.made.append((delay, fn))

            def cancel(self):
                pass

        return T()

    def run(self):
        made, self.made = self.made, []
        for _d, fn in made:
            fn()


@pytest.fixture
def pushed(monkeypatch):
    from app import push

    monkeypatch.setattr(push, "enabled", lambda: True)
    monkeypatch.setattr(push, "flush_soon", lambda: None)
    t = _Timers()
    monkeypatch.setattr(push, "_Timer", t)
    push._list_pending.clear()
    return t


def _bodies(uid):
    from app import push

    return [(q["payload"]["kind"], q["payload"]["body"], q["payload"]["route"]) for q in push.queue(uid)
            if q["payload"]["kind"] == "liste"]


def test_list_add_notifies_others_batched(owner, pushed):  # noqa: F811
    from app import push

    m = _member(owner, "ayse", "editor")
    admin, ayse = system.user_by_name("admin"), system.user_by_name("ayse")
    push.register(admin["id"], "fcm-sahip-belgesi-0001-abcdefghijk", "android")
    push.register(ayse["id"], "fcm-uye-belgesi-0002-abcdefghijklm", "android")
    for t in ("süt", "yumurta", "elma", "ekmek", "peynir"):
        assert m.post("/api/shopping-list", json={"text": t}).status_code == 200
    assert len(pushed.made) == 1 and pushed.made[0][0] == push.LIST_DELAY   # tek zamanlayıcı: toplu
    pushed.run()
    assert _bodies(admin["id"]) == [("liste", "ayse listeye 5 ürün ekledi: süt, yumurta, elma ve 2 ürün daha",
                                     "#/diger/alisveris")]
    assert _bodies(ayse["id"]) == []                                  # ekleyene gitmez
    m.post("/api/shopping-list", json={"text": "su"})                 # 30 dk dolmadı: bir sonraki pencereye
    assert len(pushed.made) == 1 and pushed.made[0][0] > push.LIST_EVERY - 120
    pushed.run()
    assert _bodies(admin["id"])[-1][1] == "ayse listeye 1 ürün ekledi: su"
    owner.post("/api/shopping-list", json={"text": "çay"})            # sahip ekledi: üyeye gider
    pushed.run()
    assert _bodies(ayse["id"]) == [("liste", "admin listeye 1 ürün ekledi: çay", "#/diger/alisveris")]
    # alışverişi tamamla
    items = owner.get("/api/shopping-list").json()["items"]
    for it in items[:3]:
        owner.patch(f"/api/shopping-list/{it['id']}", json={"done": True})
    assert owner.post("/api/shopping-list/complete", json={}).status_code == 200
    assert _bodies(ayse["id"])[-1] == ("liste", "admin alışverişi tamamladı · 3 ürün alındı", "#/diger/alisveris")
    assert [b for b in _bodies(admin["id"]) if "tamamladı" in b[1]] == []


def test_list_notify_respects_prefs_hidden_page_and_single_book(owner, pushed):  # noqa: F811
    from app import push

    owner.post("/api/shopping-list", json={"text": "süt"})            # tek kişilik defter: bildirim yok
    assert pushed.made == []
    m = _member(owner, "ayse", "editor")
    admin, ayse = system.user_by_name("admin"), system.user_by_name("ayse")
    push.register(admin["id"], "fcm-sahip-belgesi-0001-abcdefghijk", "android")
    push.register(ayse["id"], "fcm-uye-belgesi-0002-abcdefghijklm", "android")
    push.set_prefs(admin["id"], {"types": {"liste": False}})
    m.post("/api/shopping-list", json={"text": "elma"})
    pushed.run()
    assert _bodies(admin["id"]) == []                                 # tercih kapalı
    assert owner.put(f"/api/book/rules?member={ayse['id']}", json={"hidden_pages": ["alisveris"]}).status_code == 200
    owner.post("/api/shopping-list", json={"text": "çay"})
    assert pushed.made == []                                          # liste üyeye gizli: kimse yok
