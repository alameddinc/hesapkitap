"""Ödeme aracısı önekleri: veriden aday, yönetici onayı, deploy beklemeden geçerli liste.

Kart ekstresinde sanal POS / ödeme kuruluşu satıcının önüne kendi adını yazar ("IYZICO/ORNEK KITAP"). Sabit liste
app/textnorm.py PAYMENT_GATEWAYS; bu modül ona ek olarak yöneticinin onayladığı önekleri system.db'de tutar
(payment_gateways). textnorm her GATEWAY_RELOAD_SECONDS'ta bir sürüm damgasına (sys_settings 'gateways:ver') bakar;
onay aynı süreçte hemen (textnorm.reload_gateways), başka süreçte en geç 30 sn içinde geçerli olur.

Aday taraması (scan; gece, jobs.run_global): bütün defterlerde kart işlemleri ve hesap hareketlerinde "ÖN EK / SAĞ"
kalıbı sayılır (önce bilinen aracılar atılır, sonra kalan baş). Aday ölçütü:
- ön ek listede değil (tam ya da benzer yazımla), reddedilmemiş, bilinen zincir / abonelik / pazar yeri öneki değil,
  kendisi konum değil;
- sağ tarafta en az MIN_MERCHANTS (3) farklı normalize işyeri (konum sözcükleri atılmış, referans kodsuz ad);
- sağ tarafların çoğu konum değil: farklı konum-sağ sayısı < farklı işyeri sayısı ("MIGROS/ISTANBUL", "A101/KADIKOY"
  zincir + şube; aracı değil);
- en az MIN_BOOKS (2) farklı defterde görülmüş YA DA tek defterde en az MIN_SINGLE_BOOK (5) farklı işyeri.
  Gerekçe: aracı birbirinden bağımsız kişilerin farklı satıcılarında çıkar; iki defter bağımsız gözlemdir. Kendi
  sunucusunu kuranlarda çoğunlukla tek defter vardır; orada şube adlarıyla yazan bir zincirin (ilçe sözlüğü eksik
  kalabilir) 3-4 farklı "işyeri" üretmesi olasıdır, 5 farklı konum dışı satıcı ise aracıya işaret eder. Eşik yalnız
  aday çıkarır; listeye girmesi için yönetici onayı gerekir.

Kişisel veri saklanmaz: ön ek, sayılar (işyeri / defter / satır), ilk-son görülme günü ve önizleme için en çok
SAMPLES (5) sağ taraf işyeri adı (önce birden çok defterde görülenler). Hazır sürümde (HESAPKITAP_HOSTED=1) yalnız
en az iki farklı defterde görülen işyeri adı örnek olur; tek kişide görülenin adı yazılmaz, sayısı (hidden) kalır. Tutar, tarih, kişi, defter kimliği yok; hesap
hareketlerinde karşı tarafı (kişi) olan satırlar hiç okunmaz.

Reddedilen ön ek bir daha aday olmaz ve benzer yazım toleransının da dışında kalır (textnorm.GatewaySet blocked).
Onaylı ön ek kaldırılırsa reddedilmiş sayılır (geri alınabilir: kaydı silinir, yeniden aday olabilir).

Liste değişince her defterde işyeri ADINA bağlı ayarlar yeni ada taşınır (app/db.py sync_gateway_keys): abonelik
durumu, işyeri kısma hedefi, öğrenilmiş eşleşme kuralı, sabit kalem eşleşme anahtarı.
"""
import json
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta

log = logging.getLogger("hesapkitap.gateways")

SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_gateways (
    prefix TEXT PRIMARY KEY,            -- normalize() biçimi ('IZICO')
    status TEXT NOT NULL CHECK (status IN ('bekliyor', 'onaylandi', 'reddedildi')),
    source TEXT NOT NULL DEFAULT 'veri',  -- veri (gece taraması) | elle (yönetici yazdı)
    merchants INTEGER NOT NULL DEFAULT 0, -- sağ tarafta farklı işyeri sayısı (bütün defterler)
    books INTEGER NOT NULL DEFAULT 0,     -- kaç defterde görüldü
    top_book INTEGER NOT NULL DEFAULT 0,  -- tek defterdeki en çok farklı işyeri
    rows INTEGER NOT NULL DEFAULT 0,      -- onaylanırsa ayrılacak satır (kart + hesap hareketi; benzer yazım dahil)
    samples TEXT,                         -- JSON: en çok 5 sağ taraf işyeri adı (önizleme)
    hidden INTEGER NOT NULL DEFAULT 0,    -- hazır sürüm: yalnız tek defterde görüldüğü için örneğe girmeyen işyeri sayısı
    first_seen TEXT,                      -- YYYY-MM-DD
    last_seen TEXT,
    decided_at TEXT,                      -- onay / ret zamanı
    notified INTEGER NOT NULL DEFAULT 0   -- yöneticiye haber verildi mi
);
"""
STATUSES = ("bekliyor", "onaylandi", "reddedildi")
MIN_MERCHANTS = 3
MIN_BOOKS = 2
MIN_SINGLE_BOOK = 5
SAMPLES = 5
NOTIFY_DAYS = 7          # yöneticiye en çok haftada bir haber
SCAN_HOUR = 4
PREFIX_RX = re.compile(r"[A-Z0-9][A-Z0-9 .]{0,22}[A-Z0-9.]|[A-Z0-9]")
VER_KEY = "gateways:ver"


def _init(c) -> None:
    c.executescript(SCHEMA)


def _bump(c) -> None:
    v = c.execute("SELECT value FROM sys_settings WHERE key = ?", (VER_KEY,)).fetchone()
    c.execute("INSERT OR REPLACE INTO sys_settings (key, value) VALUES (?, ?)", (VER_KEY, str(int(v[0] if v else 0) + 1)))


def remember_set(sig: str, data: dict) -> None:
    """Aracı kümesinin içeriği (db.sync_gateway_keys): defterde yalnız özeti (sig) tutulur, adlar sistem
    veritabanında — kullanıcı defterine / dışa aktarıma marka adı yazılmaz."""
    from app import system

    key = f"gateways:set:{sig}"
    if not system.get(key):
        system.put(key, json.dumps(data))


def recall_set(sig: str) -> dict | None:
    from app import system

    try:
        raw = system.get(f"gateways:set:{sig}")
        return json.loads(raw) if raw else None
    except (ValueError, Exception):  # noqa: BLE001
        return None


def load() -> tuple[str, str | None, tuple[str, ...], tuple[str, ...]]:
    """(sistem veritabanı yolu, sürüm, onaylılar, reddedilenler) — textnorm bunu okur. Dosya yoksa yaratmaz."""
    from app import system

    path = system._path()
    if not os.path.isfile(path):
        return path, None, (), ()
    with system.connect() as c:
        _init(c)
        v = c.execute("SELECT value FROM sys_settings WHERE key = ?", (VER_KEY,)).fetchone()
        rows = c.execute("SELECT prefix, status FROM payment_gateways WHERE status != 'bekliyor'").fetchall()
    return (path, v[0] if v else None, tuple(r[0] for r in rows if r[1] == "onaylandi"),
            tuple(r[0] for r in rows if r[1] == "reddedildi"))


# ---------------------------------------------------------------- tarama

def clean_prefix(text: str) -> str | None:
    """Yöneticinin yazdığı / taramada bulunan ön ek → normalize biçim ya da None (geçersiz)."""
    from app.textnorm import normalize

    p = normalize(text or "").strip(" /*")
    if not p or not PREFIX_RX.fullmatch(p) or sum(ch.isalpha() for ch in p) < 2 or len(p.split()) > 3:
        return None
    return p


def _prefix_ok(p: str) -> bool:
    """Taramada aday olabilir mi: biçim, konum değil, pazar yeri / bilinen zincir / bilinen abonelik değil."""
    from app import places, sub_catalog
    from app.receipt_templates import chain_of
    from app.textnorm import MARKETPLACE_PREFIXES

    if clean_prefix(p) != p or p in MARKETPLACE_PREFIXES or places.is_location(p):
        return False
    try:
        if chain_of(p) or sub_catalog.match_description(p):
            return False
    except Exception:  # noqa: BLE001 - yardımcı sözlükler okunamazsa yalnız biçim kontrolü
        pass
    return True


def _right_key(right: str, gs) -> str | None:
    """Sağ taraf → normalize işyeri adı: 'SPOTIFY.COM ISTANBUL TR' → 'SPOTIFY'; yalnız konumsa '' ; boşsa None."""
    from app import places

    r = gs.strip(right)
    head = re.split(r"[*/]", r, maxsplit=1)[0].strip()
    if not head:
        return None
    words = [w for w in head.split() if not re.fullmatch(r"[\d\-#.]+", w) and not (len(w) >= 5 and re.search(r"\d", w))]
    name = places.strip_location(" ".join(words))
    if not name:
        return "" if words or places.is_location(head) else None
    name = re.sub(r"\.COM(\.TR)?$", "", " ".join(name.split()[:3]))
    return name or None


def _book_descriptions(c) -> list[tuple[str, int]]:
    """Defterdeki (açıklama, satır sayısı): kart işlemleri + bankadan gelen hesap hareketleri (kişiye giden hariç)."""
    out: list[tuple[str, int]] = []
    try:
        out += [(r[0] or "", r[1]) for r in c.execute(
            "SELECT description, COUNT(*) FROM transactions WHERE is_deleted = 0 GROUP BY description")]
    except Exception:  # noqa: BLE001 - eski şema
        pass
    try:
        for r in c.execute("SELECT description, note, COUNT(*) FROM entries WHERE import_hash IS NOT NULL "
                           "AND COALESCE(counterparty, '') = '' GROUP BY description, note"):
            note = r[1] or ""
            desc = note.partition("\n")[0][len("Hesap hareketi · "):] if note.startswith("Hesap hareketi · ") else r[0]
            out.append((desc or "", r[2]))
    except Exception:  # noqa: BLE001
        pass
    return out


def book_stats(gs=None) -> dict[str, dict]:
    """Açık defterde ön ek → {"rights": {işyeri: satır}, "loc": {konum-sağ}, "rows": satır}. Bilinen aracılar önce
    atılır ('IYZICO/YENIARACI/ORNEK' → 'YENIARACI'). Ön ek uygun değilse sayılmaz."""
    from app import db
    from app.textnorm import _GATEWAY_HEAD, current_gateways, normalize

    gs = gs or current_gateways()
    out: dict[str, dict] = {}
    ok: dict[str, bool] = {}
    with db.connect() as c:
        descs = _book_descriptions(c)
    for desc, n in descs:
        if "/" not in desc and "*" not in desc:
            continue
        m = _GATEWAY_HEAD.match(gs.strip(normalize(desc)))
        if not m:
            continue
        p = m.group(1).strip()
        if p not in ok:
            ok[p] = gs.match(p) is None and _prefix_ok(p)
        if not ok[p]:
            continue
        rk = _right_key(m.group(2), gs)
        if rk is None:
            continue
        st = out.setdefault(p, {"rights": {}, "loc": set(), "rows": 0})
        st["rows"] += n
        if rk == "":
            st["loc"].add(normalize(m.group(2))[:40])
        else:
            st["rights"][rk] = st["rights"].get(rk, 0) + n
    return out


def _spaces():
    from app import jobs

    return jobs.spaces()


def _each_book(fn):
    """fn() her defterde (jobs.spaces, sahip görünümü); hata o defteri atlar."""
    from app import jobs

    for path, user in _spaces():
        try:
            with jobs.book_space(path, user):
                yield fn()
        except Exception:  # noqa: BLE001 - bir defter bozuksa ötekiler sürsün
            log.exception("ödeme aracısı taraması: defter atlandı")


def _neighbors(p: str, prefixes) -> set[str]:
    """Benzer yazımı (1 harf) da sayılacak önekler (textnorm.GatewaySet ile aynı kural)."""
    from app.textnorm import FUZZY_MIN_LETTERS, _edits1, _letters

    if _letters(p) < FUZZY_MIN_LETTERS:
        return {p}
    near = {q for q in _edits1(p) if _letters(q) >= FUZZY_MIN_LETTERS}
    return {p} | (near & set(prefixes))


def _private() -> bool:
    """Hazır sürüm (HESAPKITAP_HOSTED=1): yönetici başkalarının defterini görmez; yalnız birden çok defterde görülen
    işyeri adı örnek olabilir. Kendi sunucusunu kuranda yönetici defterlerin sahibi/ailesi: bütün adlar."""
    from app import plans

    return plans.hosted()


def _samples(merchants: dict[str, set]) -> tuple[list[str], int]:
    """Önizleme: (örnekler, gizlenen işyeri sayısı). Önce en çok defterde görülen, kısa ve rakam dizisi içermeyen en çok
    SAMPLES işyeri adı. Hazır sürümde yalnız tek defterde (tek kişide) görülen işyeri örneğe girmez, sayısı döner."""
    names = list(merchants)
    hidden = 0
    if _private():
        hidden = sum(1 for k in names if len(merchants[k]) < 2)
        names = [k for k in names if len(merchants[k]) >= 2]
    ok = [k for k in names if len(k) <= 40 and not re.search(r"\d{4}", k)]
    return sorted(ok, key=lambda k: (-len(merchants[k]), k))[:SAMPLES], hidden


def aggregate(per_book: list[dict[str, dict]]) -> dict[str, dict]:
    """Defter sonuçlarını birleştir: ön ek → işyeri (defter kümesi), konum, defter sayısı, tek defter tavanı, satır."""
    agg: dict[str, dict] = {}
    for i, stats in enumerate(per_book):
        for p, st in stats.items():
            a = agg.setdefault(p, {"merchants": {}, "loc": set(), "books": 0, "top_book": 0, "rows": 0})
            a["books"] += 1
            a["top_book"] = max(a["top_book"], len(st["rights"]))
            a["rows"] += st["rows"]
            a["loc"] |= st["loc"]
            for k in st["rights"]:
                a["merchants"].setdefault(k, set()).add(i)
    return agg


def is_candidate(a: dict) -> bool:
    m = len(a["merchants"])
    return (m >= MIN_MERCHANTS and len(a["loc"]) < m
            and (a["books"] >= MIN_BOOKS or a["top_book"] >= MIN_SINGLE_BOOK))


def scan(today: date | None = None) -> dict:
    """Bütün defterleri tara, adayları system.db'ye yaz, gerekirse yöneticiye haber ver. Dönen: sayılar."""
    from app import system
    from app.textnorm import current_gateways

    today = today or date.today()
    gs = current_gateways()
    per_book = [st for st in _each_book(lambda: book_stats(gs)) if st]
    agg = aggregate(per_book)
    rows_by = {p: a["rows"] for p, a in agg.items()}
    found = new = 0
    with system.connect() as c:
        _init(c)
        known = {r[0]: r[1] for r in c.execute("SELECT prefix, status FROM payment_gateways")}
        for p, a in agg.items():
            if not is_candidate(a) or known.get(p) in ("onaylandi", "reddedildi"):
                continue
            found += 1
            samples, hidden = _samples(a["merchants"])
            vals = (len(a["merchants"]), a["books"], a["top_book"],
                    sum(rows_by[q] for q in _neighbors(p, rows_by)),
                    json.dumps(samples, ensure_ascii=False), hidden, today.isoformat())
            if p in known:
                c.execute("UPDATE payment_gateways SET merchants = ?, books = ?, top_book = ?, rows = ?, samples = ?, "
                          "hidden = ?, last_seen = ? WHERE prefix = ?", (*vals, p))
            else:
                new += 1
                c.execute("INSERT INTO payment_gateways (prefix, status, source, merchants, books, top_book, rows, "
                          "samples, hidden, first_seen, last_seen) VALUES (?, 'bekliyor', 'veri', ?, ?, ?, ?, ?, ?, ?, ?)",
                          (p, *vals, today.isoformat()))
    system.put("gateways:last_scan", today.isoformat())
    pushed = notify(today)
    return {"books": len(per_book), "candidates": found, "new": new, "pushed": pushed}


def notify(today: date | None = None) -> int:
    """Haber verilmemiş bekleyen aday varsa yöneticilere tek "sistem" push'u: en çok NOTIFY_DAYS günde bir."""
    from app import push, system

    today = today or date.today()
    last = system.get("gateways:last_push")
    if last and (today - date.fromisoformat(last)).days < NOTIFY_DAYS:
        return 0
    with system.connect() as c:
        _init(c)
        fresh = [r[0] for r in c.execute("SELECT prefix FROM payment_gateways WHERE status = 'bekliyor' AND notified = 0")]
    if not fresh:
        return 0
    n = len(fresh)
    sent = 0
    try:
        sent = push.system_alert(f"sistem:odeme-araci:{today.isoformat()}", "Ödeme aracıları",
                                 f"{n} yeni ödeme aracı adayı", route=ROUTE)
    except Exception:  # noqa: BLE001 - bildirim taramayı bozmasın
        log.exception("ödeme aracısı bildirimi gönderilemedi")
    with system.connect() as c:
        c.executemany("UPDATE payment_gateways SET notified = 1 WHERE prefix = ?", [(p,) for p in fresh])
    system.put("gateways:last_push", today.isoformat())
    return sent


ROUTE = "#/diger/ayarlar/odeme-araclari"  # mobil: Ayarlar → Ödeme aracıları (yönetici)


def nightly(now: datetime) -> dict | None:
    """Gece işi (jobs.run_global): günde bir kez, SCAN_HOUR'dan sonra. Çalışmadıysa None."""
    from app import system

    day = now.date().isoformat()
    if now.hour < SCAN_HOUR or system.get("job:gateway_scan") == day:
        return None
    system.put("job:gateway_scan", day)
    got = scan(now.date())
    got["synced"] = sync_all()  # onay anındaki arka plan turu kaçırdıysa (yeniden başlatma) ada bağlı ayarlar
    return got


# ---------------------------------------------------------------- yönetici

def _row(r) -> dict:
    d = dict(r)
    try:
        d["samples"] = json.loads(d.get("samples") or "[]")
    except ValueError:
        d["samples"] = []
    return d


def overview() -> dict:
    """Yönetici sayfası: bekleyen adaylar (çok işyerli önce), onaylılar, reddedilenler ve sabit liste."""
    from app import system
    from app.textnorm import PAYMENT_GATEWAYS

    with system.connect() as c:
        _init(c)
        rows = [_row(r) for r in c.execute("SELECT * FROM payment_gateways ORDER BY merchants DESC, prefix")]
        last = c.execute("SELECT value FROM sys_settings WHERE key = 'gateways:last_scan'").fetchone()
    return {"candidates": [r for r in rows if r["status"] == "bekliyor"],
            "approved": sorted((r for r in rows if r["status"] == "onaylandi"), key=lambda r: r["prefix"]),
            "rejected": sorted((r for r in rows if r["status"] == "reddedildi"), key=lambda r: r["prefix"]),
            "builtin": sorted(PAYMENT_GATEWAYS), "last_scan": last[0] if last else None,
            "thresholds": {"merchants": MIN_MERCHANTS, "books": MIN_BOOKS, "single_book": MIN_SINGLE_BOOK}}


def preview(prefix: str) -> dict:
    """Elle eklenecek önek: bütün defterlerde kaç satır ayrılır, kaç farklı işyeri (anlık tarama; yalnız sayılar ve
    en çok SAMPLES örnek)."""
    from app.textnorm import GatewaySet, current_gateways

    p = clean_prefix(prefix)
    if not p:
        raise ValueError("Geçersiz ön ek: harf ve rakam, en çok 3 kelime, 24 karakter")
    gs = current_gateways()
    if gs.match(p):
        return {"prefix": p, "known": True, "rows": 0, "merchants": 0, "samples": [], "hidden": 0}
    trial = GatewaySet(gs.names | {p}, gs.blocked - {p})

    def one():
        from app import db
        from app.textnorm import normalize

        with db.connect() as c:
            descs = _book_descriptions(c)
        rows, rights = 0, set()
        for desc, n in descs:
            if "/" not in desc and "*" not in desc:
                continue
            norm = gs.strip(normalize(desc))
            g = trial.split(norm)
            if g and gs.split(norm) is None and trial.match(g[0]) == p:
                rows += n
                if rk := _right_key(g[1], gs):
                    rights.add(rk)
        return rows, rights

    total, merchants = 0, {}
    for i, (rows, rights) in enumerate(_each_book(one)):
        total += rows
        for k in rights:
            merchants.setdefault(k, set()).add(i)
    samples, hidden = _samples(merchants)
    return {"prefix": p, "known": False, "rows": total, "merchants": len(merchants), "samples": samples, "hidden": hidden}


def _set_status(prefix: str, status: str, source: str | None = None) -> dict:
    from app import system

    p = clean_prefix(prefix)
    if not p:
        raise ValueError("Geçersiz ön ek")
    now = datetime.now().isoformat(timespec="seconds")
    with system.connect() as c:
        _init(c)
        if c.execute("SELECT 1 FROM payment_gateways WHERE prefix = ?", (p,)).fetchone():
            c.execute("UPDATE payment_gateways SET status = ?, decided_at = ?, notified = 1 WHERE prefix = ?", (status, now, p))
        else:
            c.execute("INSERT INTO payment_gateways (prefix, status, source, first_seen, decided_at, notified) "
                      "VALUES (?, ?, ?, ?, ?, 1)", (p, status, source or "elle", date.today().isoformat(), now))
        _bump(c)
    _changed()
    return {"prefix": p, "status": status}


def approve(prefix: str) -> dict:
    return _set_status(prefix, "onaylandi")


def add(prefix: str) -> dict:
    """Elle ekleme: sabit listede ya da onaylıysa değişiklik yok."""
    from app.textnorm import PAYMENT_GATEWAYS

    p = clean_prefix(prefix)
    if not p:
        raise ValueError("Geçersiz ön ek: harf ve rakam, en çok 3 kelime, 24 karakter")
    if p in PAYMENT_GATEWAYS:
        raise ValueError(f"{p} zaten sabit listede")
    return _set_status(p, "onaylandi", "elle")


def reject(prefix: str) -> dict:
    """Ret (ya da onaylının kaldırılması): bir daha aday olmaz, benzer yazımla da eşleşmez."""
    from app.textnorm import PAYMENT_GATEWAYS

    if clean_prefix(prefix) in PAYMENT_GATEWAYS:
        raise ValueError("Sabit listedeki aracı buradan kaldırılamaz")
    return _set_status(prefix, "reddedildi")


def forget(prefix: str) -> bool:
    """Reddedileni geri al: kayıt silinir, sonraki taramada yeniden aday olabilir."""
    from app import system

    p = clean_prefix(prefix)
    with system.connect() as c:
        _init(c)
        n = c.execute("DELETE FROM payment_gateways WHERE prefix = ? AND status = 'reddedildi'", (p,)).rowcount
        if n:
            _bump(c)
    if n:
        _changed()
    return bool(n)


_sync_lock = threading.Lock()


def _changed() -> None:
    """Liste değişti: bu süreçte hemen geçerli; defterlerde ada bağlı ayarlar arka planda taşınır."""
    from app import textnorm

    textnorm.reload_gateways()
    if os.environ.get("HESAPKITAP_GATEWAY_SYNC", "thread") == "inline":
        sync_all()
        return
    threading.Thread(target=sync_all, name="gateway-keys", daemon=True).start()


def sync_all() -> int:
    """Her defterde db.sync_gateway_keys (aynı anda tek tur). Dönen: taşıma yapılan defter sayısı."""
    from app import db

    with _sync_lock:
        return sum(1 for r in _each_book(db.sync_gateway_keys) if r)
