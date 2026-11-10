"""Gece işlerinin defterleri (jobs.spaces): her fiziksel veritabanı bir kez, sahibin bağlamıyla ve sahip görünümüyle.

10.11.2026 hatası: ortak defter üyesi (kendi db_file'ı olan) sahibin dosyasıyla eşleniyordu; üyenin kendi ("Benim")
defteri hiç gece işi almıyordu, üye id'si sahibinkinden küçükse işler sahibin defterinde üyenin süzülmüş görünümüyle
koşuyordu (ör. hide_fees: faiz görünmez).
"""
import json
import os
from datetime import datetime

import pytest


@pytest.fixture()
def book_pair(tmpdb):
    """Üye (id küçük, kendi defteri var) + sahip (id büyük, kendi defteri) + sahibin defterinde üyeye faiz gizli."""
    from app import book, db, system

    member = system.create_user("esim", "x" * 60)  # önce kendi hesabı: db_file var, id küçük
    owner = system.create_user("sahip", "x" * 60)
    assert member["id"] < owner["id"]
    with db.use_space(system.own_db_path(member), member):  # "Benim" defteri dosyası
        db.set_setting("isaret", "uye-kendi")
    system.set_membership(member["id"], owner["id"], "editor")
    member, owner = system.user(member["id"]), system.user(owner["id"])
    assert system.user_db_path(member) == system.user_db_path(owner)  # istekte ortak defter: sahibin dosyası
    with db.use_space(system.user_db_path(owner), owner):
        db.set_setting("isaret", "sahip")
        book.set_rules({"hide_fees": True, "hidden_cards": ["1234"]}, member["id"])
    return member, owner


def _norm(p):
    return os.path.abspath(p) if p else None


def test_each_book_once_with_owner_context(book_pair):
    from app import jobs, system

    member, owner = book_pair
    sp = jobs.spaces()
    keys = [_norm(p) for p, _ in sp]
    assert len(keys) == len(set(keys))  # her dosya bir kez
    by_path = {_norm(p): u for p, u in sp}
    shared = by_path[_norm(system.user_db_path(owner))]
    assert shared["id"] == owner["id"] and not shared.get("member_of")  # ortak defter: sahibin bağlamı
    own = by_path[_norm(system.own_db_path(member))]  # üyenin kendi defteri de listede
    assert own["id"] == member["id"] and own["own_book"] is True


def test_member_without_own_file_does_not_create_one(tmpdb):
    """Davetle açılmış, "Benim" defterini hiç açmamış üye: boş dosya yaratılmaz, listede yok."""
    from app import jobs, system

    owner = system.create_user("sahip", "x" * 60)
    m = system.create_user("davetli", "x" * 60, member_of=owner["id"], book_role="viewer")
    assert not os.path.exists(system.own_db_path(m))
    paths = [_norm(p) for p, _ in jobs.spaces()]
    assert _norm(system.own_db_path(m)) not in paths and not os.path.exists(system.own_db_path(m))
    assert paths.count(_norm(system.user_db_path(owner))) == 1


def test_disabled_and_deleted_skipped(book_pair):
    from app import jobs, system

    member, owner = book_pair
    with system.connect() as c:
        c.execute("UPDATE users SET disabled = 1 WHERE id = ?", (member["id"],))
    assert all((u or {}).get("id") != member["id"] for _, u in jobs.spaces())


def test_book_space_is_owner_view_even_with_member_context(book_pair):
    """Bağlam yanlışlıkla üye olsa da (eski kayıt, elle verilen alan) iş sahip görünümünde: süzgeç yok."""
    from app import book, db, jobs, system

    member, owner = book_pair
    with db.use_space(system.user_db_path(member), member):
        assert book.member() and book.hide_fees()  # istek yolu: üye süzülür
    with jobs.book_space(system.user_db_path(member), member):
        assert book.member() is None and not book.hide_fees()


def test_scheduler_runs_each_book_once_in_owner_view(book_pair, monkeypatch):
    """Zamanlayıcı turu: üyenin kendi defteri dahil her defterde işler bir kez, sahip görünümünde; job:* yazılır."""
    import asyncio

    from app import book, db, jobs, system

    member, owner = book_pair
    seen = []

    def fake(today):
        seen.append((os.path.abspath(db.db_path()), db.get_setting("isaret"), book.member(), book.hide_fees()))
        return "ok"

    monkeypatch.setattr(jobs, "JOBS", [("integrity", 0, fake)])
    monkeypatch.setattr(jobs.telegram, "enabled", lambda: False)
    monkeypatch.setattr(jobs, "mail_due", lambda now: False)
    for name in ("run_account_purge", "run_global"):
        monkeypatch.setattr(jobs, name, lambda now: None)

    class Stop(Exception):
        pass

    async def stop(_):
        raise Stop

    monkeypatch.setattr(jobs.asyncio, "sleep", stop)
    with pytest.raises(Stop):
        asyncio.run(jobs.scheduler())
    paths = [p for p, *_ in seen]
    assert len(paths) == len(set(paths))
    marks = sorted(m for _, m, *_ in seen if m)
    assert marks == ["sahip", "uye-kendi"]
    assert all(m is None and not hf for _, _, m, hf in seen)  # süzgeç yok
    today = datetime.now(jobs.TZ).date().isoformat()
    with db.use_space(system.own_db_path(member), member):
        assert db.get_setting("job:integrity") == today


def test_integrity_runs_in_member_own_book(book_pair):
    """Doğruluk denetimi üyenin kendi defterinde de koşu kaydı bırakır."""
    from app import db, integrity, jobs, system

    member, _ = book_pair
    for path, user in jobs.spaces():
        with jobs.book_space(path, user):
            integrity.run()
    with db.use_space(system.own_db_path(member), {**member, "own_book": True}):
        assert integrity.last() is not None
        assert json.loads(db.get_setting("integrity:last") or "null") is not None
