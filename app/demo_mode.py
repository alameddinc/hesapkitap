"""Canlı demo (HESAPKITAP_DEMO=1): şifresiz giriş, her ziyaretçiye örnek verinin kendi kopyası, 1 saat.

- Şablon: app.demo'nun uydurma verisi, bugünün ayına kaydırılmış (demo hiç eskimez); ayda bir yeniden üretilir.
- Sandbox: data/demo/sb/<oluşma zamanı>-<rastgele>/ekstre.db — çerezle bulunur, süresi dolunca yenisi açılır,
  eskiler kendiliğinden silinir. Kimse başkasının verisini görmez.
- Dosya yükleme, yapay zekâ, Telegram, e-posta, ayarlar, kullanıcı ve ortak defter işlemleri kapalı; yerine örnek
  ekstre / fiş düğmeleri var (yerel OCR ve genel kart okuyucu çalışır).
"""
import os
import re
import secrets
import shutil
import sqlite3
import time
from datetime import date

from app import db

TTL = int(os.environ.get("HESAPKITAP_DEMO_TTL", "3600"))  # saniye
MAX_SANDBOXES = 500
COOKIE = "hk_demo"
ANCHOR = (2026, 10)  # app.demo verisinin yazıldığı ay
CLOSED = ("/api/statements/upload", "/api/bank-import", "/api/receipts/parse", "/api/bills/parse", "/api/share",
          "/api/documents/parse",
          "/api/settings", "/api/admin", "/api/book", "/api/mail", "/api/templates")
OPEN = ("/api/book/me",)
SAMPLES = {
    "vakifbank": {"label": "Örnek VakıfBank ekstresi", "kind": "statement", "file": "vakifbank_b.txt"},
    "genel": {"label": "Örnek Garanti ekstresi (genel okuyucu)", "kind": "statement", "file": "garanti"},
    "fis-market": {"label": "Market fişi", "kind": "receipt", "file": "fis-market.jpg"},
    "fis-kahve": {"label": "Kahve fişi", "kind": "receipt", "file": "fis-kahve.jpg"},
    "fis-eczane": {"label": "Eczane fişi", "kind": "receipt", "file": "fis-eczane.jpg"},
}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def enabled() -> bool:
    return os.environ.get("HESAPKITAP_DEMO") == "1"


def _base() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "demo")


def months_since_anchor(today: date | None = None) -> int:
    t = today or date.today()
    return (t.year * 12 + t.month) - (ANCHOR[0] * 12 + ANCHOR[1])


def _shift(v: str, months: int) -> str:
    """'2026-10-05…' / '2026-10' → n ay sonrası (gün ay sonuna sığdırılır)."""
    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?", v)
    if not m:
        return v
    y, mo = divmod(int(m.group(1)) * 12 + int(m.group(2)) - 1 + months, 12)
    head = f"{y:04d}-{mo + 1:02d}"
    if m.group(3):
        head += f"-{min(int(m.group(3)), 28 if mo + 1 == 2 else 30 if mo + 1 in (4, 6, 9, 11) else 31):02d}"
    return head + v[m.end():]


def shift_db(path: str, months: int) -> None:
    """Veritabanındaki bütün tarih/ay değerlerini kaydır (demo verisi bugüne gelsin)."""
    if not months:
        return
    c = sqlite3.connect(path)
    try:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall():
            cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]
            for col in cols:
                rows = c.execute(f'SELECT rowid, "{col}" FROM "{t}" WHERE typeof("{col}") = \'text\' '
                                 f'AND "{col}" GLOB \'[12][0-9][0-9][0-9]-[01][0-9]*\'').fetchall()
                for rid, v in rows:
                    c.execute(f'UPDATE "{t}" SET "{col}" = ? WHERE rowid = ?', (_shift(v, months), rid))
        c.commit()
    finally:
        c.close()


def shift_text(text: str, months: int) -> str:
    """Örnek ekstre metnindeki gg.aa.yyyy / gg/aa/yyyy tarihlerini kaydır."""
    def rep(m):
        d, mo, y, sep = int(m.group(1)), int(m.group(3)), int(m.group(4)), m.group(2)
        iso = _shift(f"{y:04d}-{mo:02d}-{d:02d}", months)
        return f"{iso[8:10]}{sep}{iso[5:7]}{sep}{iso[0:4]}"
    return re.sub(r"\b(\d{2})([./])(\d{2})\2(\d{4})\b", rep, text) if months else text


def template() -> str:
    """Bu ayın şablon veritabanı (yoksa üret)."""
    months = months_since_anchor()
    path = os.path.join(_base(), f"template-{months}.db")
    if os.path.exists(path):
        return path
    os.makedirs(_base(), exist_ok=True)
    tmp = path + f".{os.getpid()}.tmp"
    for p in (tmp,):
        if os.path.exists(p):
            os.remove(p)
    from app import demo, templates

    real_sync = templates.sync_community
    templates.sync_community = lambda force=False: 0  # şablon üretirken ağa çıkma
    try:
        with db.use_space(tmp, None):
            demo.seed(("vakifbank_a.txt", "kuveytturk.txt"))  # vakifbank_b "örnek ekstre yükle" için
    finally:
        templates.sync_community = real_sync
    shift_db(tmp, months)
    os.replace(tmp, path)
    return path


def _sandbox_dir() -> str:
    return os.path.join(_base(), "sb")


def _valid(sid: str | None) -> bool:
    return bool(sid and re.fullmatch(r"\d{10}-[0-9a-f]{16}", sid)
                and os.path.exists(os.path.join(_sandbox_dir(), sid, "ekstre.db")))


def expires_at(sid: str) -> int:
    return int(sid.split("-")[0]) + TTL


def cleanup() -> None:
    d = _sandbox_dir()
    if not os.path.isdir(d):
        return
    now = time.time()
    names = sorted(os.listdir(d))
    for i, n in enumerate(names):
        stale = not re.fullmatch(r"\d{10}-[0-9a-f]{16}", n) or int(n.split("-")[0]) + TTL + 600 < now
        if stale or len(names) - i > MAX_SANDBOXES:
            shutil.rmtree(os.path.join(d, n), ignore_errors=True)


def new_sandbox() -> str:
    cleanup()
    sid = f"{int(time.time())}-{secrets.token_hex(8)}"
    path = os.path.join(_sandbox_dir(), sid)
    os.makedirs(path, exist_ok=True)
    shutil.copy(template(), os.path.join(path, "ekstre.db"))
    return sid


def resolve(sid: str | None) -> tuple[str, bool]:
    """Çerezdeki sandbox geçerliyse o, değilse yenisi. (sid, yeni mi)"""
    if _valid(sid) and expires_at(sid) > time.time():
        return sid, False
    return new_sandbox(), True


def space(sid: str) -> tuple[str, dict]:
    return (os.path.join(_sandbox_dir(), sid, "ekstre.db"),
            {"id": 0, "username": "demo", "role": "user", "demo": True, "member_of": None, "disabled": 0,
             "must_change_password": 0, "expires_at": expires_at(sid)})


def closed(path: str) -> bool:
    if path in OPEN:
        return False
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in CLOSED)


def sample_text(key: str) -> str:
    s = SAMPLES[key]
    if s["file"] == "garanti":
        from tests.test_card_reader import GARANTI
        text = GARANTI
    else:
        with open(os.path.join(ROOT, "tests", "fixtures", s["file"]), encoding="utf-8") as f:
            text = f.read()
    return shift_text(text, months_since_anchor())


def sample_image(key: str) -> bytes:
    with open(os.path.join(ROOT, "docs", "demo", SAMPLES[key]["file"]), "rb") as f:
        return f.read()
