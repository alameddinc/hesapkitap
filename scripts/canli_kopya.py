"""Canlı SQLite dosyalarının salt-okunur kopyası, yazmaları kilitlemeden (17.11 olayı: tek adımlı backup system.db'yi
saniyelerce SHARED kilitte tuttu; rollback günlüğünde (journal_mode=delete) bekleyen yazıcı PENDING alınca yeni okumalar da
durdu → GET /api/cards/summary 500 "database is locked").

Adımlı backup: her adımda PAGES sayfa kopyalanır, arada SLEEP_S beklenir; okuma kilidi yalnız adım süresince tutulur.
Kaynak bu sırada başka bağlantıdan değişirse SQLite kopyayı baştan alır (tutarlı anlık görüntü); MAX_S aşılırsa vazgeçer.

Kullanım (sunucuda, konteynerde, kaynak salt-okunur bağlı):
  python scripts/canli_kopya.py /src/system.db /out/system.db [/src/ekstre.db /out/ekstre.db ...]
"""
import os
import sqlite3
import sys
import time

PAGES = int(os.environ.get("KOPYA_PAGES", "256"))       # adım başı sayfa (4 KB sayfada ~1 MB)
SLEEP_S = float(os.environ.get("KOPYA_SLEEP_S", "0.02"))
MAX_S = float(os.environ.get("KOPYA_MAX_S", "600"))


def kopyala(src: str, dst: str, pages: int = PAGES, sleep_s: float = SLEEP_S, max_s: float = MAX_S) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    basla = time.monotonic()

    def ilerleme(status, remaining, total):  # noqa: ARG001 - sqlite3 imzası
        if time.monotonic() - basla > max_s:
            raise TimeoutError(f"{src}: {max_s:.0f} sn içinde kopyalanamadı")

    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=1)
    d = sqlite3.connect(dst)
    try:
        s.backup(d, pages=pages, progress=ilerleme, sleep=sleep_s)
    finally:
        d.close()
        s.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or len(args) % 2:
        sys.exit(__doc__)
    for i in range(0, len(args), 2):
        kopyala(args[i], args[i + 1])
        print(f"{args[i]} -> {args[i + 1]}")
