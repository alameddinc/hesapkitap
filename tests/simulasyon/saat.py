"""Zaman hızlandırma: tarih dondurma (tests/test_gercek_gun_tekrari.freeze) + SQLite saat damgası.

SQLite CURRENT_TIMESTAMP gerçek duvar saatini yazar: 10 aylık simülasyon birkaç saniyede koşar, bütün kayıtlar aynı
saniyeye düşer ve "aynı gün, çapadan sonra mı girildi" kuralları (updated_at > paid_marked_at; db.unknown_statement_payments)
eşitlikte yanlış karar verir. Uygulamanın kodu DEĞİŞMEZ: yalnız testte db.connect'in açtığı bağlantıya sim_now()
fonksiyonu eklenir ve SQL metnindeki CURRENT_TIMESTAMP / datetime('now') simülasyon saatine çevrilir. Saat her
kullanıcı adımında 1 dakika ilerler (UTC; TR = UTC+3)."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta


class SimClock:
    def __init__(self, day: date):
        self.day = day
        self.minute = 0

    def set_day(self, d: date) -> None:
        self.day, self.minute = d, 0

    def tick(self) -> None:
        self.minute += 1

    def now(self) -> datetime:
        # 05:00 UTC (08:00 TR) + adım dakikası; gün içinde 13 saati aşmaz (adım sayısı < 780)
        return datetime(self.day.year, self.day.month, self.day.day, 5, 0) + timedelta(minutes=min(self.minute, 779))

    def stamp(self) -> str:
        return self.now().strftime("%Y-%m-%d %H:%M:%S")


def _rewrite(sql: str) -> str:
    if "CURRENT_TIMESTAMP" in sql:
        sql = sql.replace("CURRENT_TIMESTAMP", "(sim_now())")
    if "'now'" in sql:
        sql = sql.replace("datetime('now'", "datetime(sim_now()").replace("date('now'", "date(sim_now()")
    return sql


def install(monkeypatch, clock: SimClock) -> None:
    from app import db

    class Conn(sqlite3.Connection):
        def execute(self, sql, *a):
            return super().execute(_rewrite(sql), *a)

        def executemany(self, sql, *a):
            return super().executemany(_rewrite(sql), *a)

        def executescript(self, sql):
            return super().executescript(_rewrite(sql))

    class Shim:
        def __getattr__(self, name):
            return getattr(sqlite3, name)

        @staticmethod
        def connect(path, *a, **k):
            k.setdefault("factory", Conn)
            c = sqlite3.connect(path, *a, **k)
            c.create_function("sim_now", 0, clock.stamp)
            return c

    monkeypatch.setattr(db, "sqlite3", Shim())


def freeze(monkeypatch, d: date) -> None:
    from tests.test_gercek_gun_tekrari import freeze as _freeze

    _freeze(monkeypatch, d)
