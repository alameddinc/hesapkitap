"""Okuma hakkı (yalnız hazır sürüm, plans.hosted()).

TEK GÜNLÜK sayaç (plans.DAILY_READS), kişi başı (ortak defterde her üyenin kendi sayacı): fiş, fatura, kart ekstresi,
hesap dökümü, dekont, deftere özel posta adresine gelen belge/bildirim, yapay zekâ kategori önerisi — türü ne olursa
olsun, yerel ayrıştırıcıyla ya da yapay zekâyla okunsun aynı günlük haktan düşer. Gün sınırı Türkiye saatiyle gece
yarısı (_day). Ücretsizde ayrıca aylık tavan (plans.MONTHLY_CAP: günde 3 VE ayda en çok 40).

- Ağırlık (weight): PDF 10 sayfaya kadar 1 hak, uzunsa her 10 sayfa 1 hak (ceil(sayfa / 10)); sayfası bulunamayan
  (şifreli) PDF, fotoğraf, fiş (çok fotoğraflı da), Excel/CSV, mail metni, kategori önerisi 1 hak.
- Elle yeniden okuma (receipt-reread) REREAD_COST (2) hak; otomatik kademeli okuma hak düşürmez.
- Kontrol işten ÖNCE: check(key, kind, needed) gereken hak (verilmezse anahtarın ağırlığı) kalan günlük hak + ek paket
  bakiyesinden fazlaysa QuotaError (receipt.ReceiptError) atar; belge okunmaz (kısmi okuma yok).
- Sayım işten SONRA ve yalnız yeni belge okunduysa: charge(key, kind). Hata çıkan, "zaten vardı" sonucu veren okumada
  charge çağrılmaz. charge idempotenttir: aynı içerik (key = dosya baytları ya da "mail:<Message-ID>") aynı defterde
  ikinci kez sayılmaz (system.doc_reads kaydı); dış uç ve iç işleyici (mailbox.handle_pdf) aynı belgeyi sayabilir.
- Ek okuma paketi (plans.PACKS, mağazada tüketilebilir ürün → RevenueCat NON_RENEWING_PURCHASE): read_credits bakiyesi.
  Önce günlük hak, o bitince ek paket; paketin günlük sınırı ve süresi yok (bitene kadar), paket değişince de kalır.
  Çok haklı işlemde (ör. 3 hak, günlükte 1 kaldı) günlükten 1 + paketten 2 aynı işlemde (transaction) düşer; düşülen
  {daily, extra} döner (istemci "ek paketten 2 okuma kullanıldı" der).
- Hak dolunca hiçbir şey kilitlenmez: yalnız okuma durur, kayıt elle eklenir.
- Sayaçlar: system.read_daily (günlük haktan düşen; aylık tavan bunların ay toplamı), system.ai_usage (bu ay düşen
  toplam hak, ek paket dahil; yönetici listesi, eski istemci), system.read_usage (tür başı okunan belge; artık yalnız
  istatistik, hak hesabına girmez; eski aylık sayaçtan göç _split geçmiş aylar için kalır).
- Hazır sürümde receipt._check_quota/_count_usage (yapay zekâ çağrısı başına) kapalıdır.

Kendi sunucusunda bu modül hiçbir şey yapmaz (kota yapay zekâ çağrısı başına, receipt.py). Yönetici sınırsız.
"""
import hashlib
import math
import os
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.receipt import ReceiptError

BELGE, FIS = "belge", "fis"
TZ = ZoneInfo("Europe/Istanbul")
REREAD_COST = 2       # elle yeniden okuma
PAGES_PER_READ = 10   # PDF: her 10 sayfa 1 hak
_AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
_DA = {"Ocak": "ta", "Şubat": "ta", "Mart": "ta", "Nisan": "da", "Mayıs": "ta", "Haziran": "da", "Temmuz": "da",
       "Ağustos": "ta", "Eylül": "de", "Ekim": "de", "Kasım": "da", "Aralık": "ta"}  # "1 Aralık'ta"


class QuotaError(ReceiptError):
    """Hak doldu. İstemci elle girişe yönlendirir: fields() hata yanıtına eklenir."""

    def __init__(self, msg: str, limit: int, upgrade: bool, kind: str = BELGE, extra: dict | None = None):
        super().__init__(msg)
        self.limit, self.upgrade, self.kind = limit, upgrade, kind
        self.extra = extra or {}

    def fields(self) -> dict:
        # manual/quota_full/limit/upgrade: eski istemciler; reason/kind/...: yapılandırılmış "hak doldu" gövdesi;
        # daily_limit/daily_used/resets_at/needed: günlük tek sayaç (version.CAPABILITIES "daily_reads")
        return {"manual": True, "quota_full": True, "limit": self.limit, "upgrade": self.upgrade,
                "reason": "quota", "kind": self.kind, **self.extra}


def _subject() -> dict | None:
    """Hakkı sayılan kullanıcı: hazır sürümde açık defterin yönetici-olmayan kullanıcısı."""
    from app import db, plans

    if not plans.hosted():
        return None
    u = db.current_user()
    if not u or u.get("role") == "admin" or not u.get("id"):
        return None
    return u


def _key(key: bytes | str) -> str:
    raw = key.encode() if isinstance(key, str) else key
    return hashlib.sha256(raw).hexdigest()


def _book(u: dict) -> int:
    """Tekrar kaydının defteri: ortak defter = sahibin kimliği; üyenin "kendi defteri" = üyenin kimliği."""
    return u["id"] if u.get("own_book") else (u.get("member_of") or u["id"])


def _now() -> datetime:
    """Türkiye saati (testler sahteler)."""
    return datetime.now(TZ)


def _day() -> str:
    return _now().date().isoformat()


def _month() -> str:
    return _now().strftime("%Y-%m")


def _next_day() -> datetime:
    d = _now().date() + timedelta(days=1)
    return datetime(d.year, d.month, d.day, tzinfo=TZ)


def _next_month() -> datetime:
    d = _now().date()
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return datetime(y, m, 1, tzinfo=TZ)


def _on(d: datetime) -> str:
    """"1 Aralık'ta" """
    ay = _AY[d.month - 1]
    return f"{d.day} {ay}'{_DA[ay]}"


def kind_of_doc(doc_type: str | None) -> str:
    """parse_document sonucunun türü (istatistik; sayaç tek): fiş → fis, diğerleri (fatura, dekont...) → belge."""
    return FIS if doc_type == "receipt" else BELGE


def guess_kind(mime: str | None) -> str:
    """Türü okumadan bilinmeyen belge (istatistik; sayaç tek): fotoğraf → fiş, PDF/metin → belge."""
    return FIS if (mime or "").startswith("image/") else BELGE


# ---------------------------------------------------------------- ağırlık (sayfa)

_PAGES: dict[str, int | None] = {}  # sha256 → sayfa sayısı (check ve charge aynı dosyayı iki kez açmasın)


def pages(key: bytes | str | None) -> int | None:
    """PDF'in sayfa sayısı (receipt._pdf_pages); PDF değilse ya da açılamıyorsa (şifreli) None."""
    if not isinstance(key, (bytes, bytearray)) or b"%PDF-" not in bytes(key[:1024]):
        return None
    h = _key(bytes(key))
    if h not in _PAGES:
        from app.receipt import _pdf_pages

        if len(_PAGES) > 256:
            _PAGES.clear()
        _PAGES[h] = _pdf_pages(bytes(key), "application/pdf")
    return _PAGES[h]


def weight(key: bytes | str | None) -> int:
    """Bu belge kaç hak: PDF ceil(sayfa / 10) (1–10 sayfa 1); diğer her şey 1."""
    n = pages(key)
    return max(1, math.ceil(n / PAGES_PER_READ)) if n else 1


# ---------------------------------------------------------------- eski tek sayaçtan göç

def _receipts_this_month(u: dict, month: str) -> int:
    """Kullanıcının KENDİ defterinde bu ay saklanan fiş fotoğrafı sayısı (göçte fiş tahmini). Ortak defter üyesinin
    fişleri sahibin defterindedir; üyeye 0 (hepsi belge sayılır)."""
    from app import system

    if u.get("member_of"):
        return 0
    path = system.user_db_path(u)
    if not path or not os.path.exists(path):
        return 0
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            r = conn.execute("SELECT COUNT(*) FROM stored_files WHERE kind = 'receipts' AND substr(created_at, 1, 7) = ?",
                             (month,)).fetchone()
            return int(r[0] or 0)
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _split(u: dict, month: str | None = None) -> None:
    """Eski tek sayaçtan (ai_usage: o ay okunan toplam belge) iki sayaca göç; her kullanıcı ve ay için BİR KEZ
    (read_split kaydı). O ay saklanan fiş fotoğrafları kadarı fiş sayacına (toplamı aşmadan), kalanı belge sayacına.
    Bu fonksiyon o ayın ilk sayımından önce çalışır: o ana kadarki ai_usage tamamen göç öncesi kullanımdır."""
    from app import system

    month = month or _month()
    with system.connect() as c:
        if not c.execute("INSERT OR IGNORE INTO read_split (user_id, month) VALUES (?, ?)", (u["id"], month)).rowcount:
            return
        r = c.execute("SELECT count FROM ai_usage WHERE user_id = ? AND month = ?", (u["id"], month)).fetchone()
        total = int(r[0]) if r else 0
        if not total or c.execute("SELECT 1 FROM read_usage WHERE user_id = ? AND month = ?", (u["id"], month)).fetchone():
            return
    fis = min(total, _receipts_this_month(u, month))
    with system.connect() as c:
        for kind, n in ((BELGE, total - fis), (FIS, fis)):
            if n:
                c.execute("INSERT INTO read_usage (user_id, month, kind, count) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT(user_id, month, kind) DO UPDATE SET count = count + excluded.count",
                          (u["id"], month, kind, n))


# ---------------------------------------------------------------- sayaçlar

def used(u: dict, kind: str, month: str | None = None) -> int:
    """Eski aylık tür sayacı (read_usage; günlük sayaçtan önceki aylar). Yeni sayım günlük: day_used/month_used."""
    from app import system

    month = month or _month()
    _split(u, month)
    with system.connect() as c:
        r = c.execute("SELECT count FROM read_usage WHERE user_id = ? AND month = ? AND kind = ?",
                      (u["id"], month, kind)).fetchone()
        return int(r[0]) if r else 0


def _day_used(c, uid: int, day: str) -> int:
    r = c.execute("SELECT count FROM read_daily WHERE user_id = ? AND day = ?", (uid, day)).fetchone()
    return int(r[0]) if r else 0


def _month_used(c, uid: int, month: str) -> int:
    r = c.execute("SELECT SUM(count) FROM read_daily WHERE user_id = ? AND substr(day, 1, 7) = ?", (uid, month)).fetchone()
    return int(r[0] or 0)


def _balance(c, uid: int) -> int:
    r = c.execute("SELECT balance FROM read_credits WHERE user_id = ?", (uid,)).fetchone()
    return int(r[0]) if r else 0


def _state(c, u: dict) -> dict:
    """Günlük hak, ücretsizde aylık tavan ve ek paket: {limit, used, left, cap, month_used, extra, by}. left = bugün
    günlük haktan kalan (aylık tavan dahil); by: kalanı sınırlayan ("day" | "month")."""
    from app import plans

    limit, cap = plans.read_limit(u), plans.monthly_cap(u)
    day_used = _day_used(c, u["id"], _day())
    left = max(0, limit - day_used)
    month_used, by = None, "day"
    if cap is not None:
        month_used = _month_used(c, u["id"], _month())
        if cap - month_used < limit - day_used:
            by = "month"
        left = min(left, max(0, cap - month_used))
    return {"limit": limit, "used": day_used, "left": left, "cap": cap, "month_used": month_used,
            "extra": _balance(c, u["id"]), "by": by}


def credits(uid: int) -> int:
    """Ek okuma paketi bakiyesi."""
    from app import system

    with system.connect() as c:
        return _balance(c, uid)


def _resets(st: dict) -> str:
    """Kalan hakkın yenilendiği an (ISO, Türkiye saati): aylık tavan bittiyse ay başı, değilse yarın gece yarısı."""
    return (_next_month() if st["by"] == "month" and st["left"] <= 0 else _next_day()).isoformat()


def status(u: dict | None) -> dict | None:
    """Arayüz için tek günlük sayaç: {period: "day", limit, used, left, resets_at, monthly_cap, monthly_used,
    monthly_resets_at, limited_by, extra_left, reread_cost, pages_per_read} + geriye uyum: belge ve fiş (eski iki
    aylık sayaç) ikisi de aynı günlük sayacı yansıtır ({used, limit, left}), extra = ek paket. Kendi sunucusu /
    yönetici: None (sınırsız)."""
    from app import plans, system

    if not u or not plans.hosted() or u.get("role") == "admin" or not u.get("id"):
        return None
    with system.connect() as c:
        st = _state(c, u)
    old = {"used": st["used"], "limit": st["limit"], "left": st["left"]}
    return {"period": "day", "limit": st["limit"], "used": st["used"], "left": st["left"], "resets_at": _resets(st),
            "monthly_cap": st["cap"], "monthly_used": st["month_used"],
            "monthly_resets_at": _next_month().isoformat() if st["cap"] is not None else None,
            "limited_by": st["by"], "extra_left": st["extra"], "reread_cost": REREAD_COST,
            "pages_per_read": PAGES_PER_READ, BELGE: old, FIS: dict(old), "extra": st["extra"]}


def left(u: dict | None, kind: str = BELGE) -> int | None:
    """Kalan hak (bugünkü + ek paket). None: sınırsız. kind yalnız imza uyumu için (sayaç tek)."""
    st = status(u)
    return None if st is None else st["left"] + st["extra"]


def _upgrade(u: dict) -> bool:
    from app import plans

    return plans.plan_of(u) == plans.FREE


def message(u: dict, st: dict | None = None, needed: int = 1, reread: bool = False, n_pages: int | None = None) -> str:
    """Hak dolu metni. st: _state (verilmezse okunur)."""
    from app import plans, system

    if st is None or isinstance(st, int):  # eski imza: message(u, quota, kind)
        with system.connect() as c:
            st = _state(c, u)
    left_, by = st["left"], st["by"]
    when = "Yarın yenilenir." if by == "day" else f"{_on(_next_month())} yenilenir."
    if reread:
        msg = f"Yeniden okuma {needed} hak ister, {'bugün' if by == 'day' else 'bu ay'} {left_} hakkın kaldı. {when}"
    elif left_ > 0:  # hak var ama bu belgeye yetmiyor (uzun PDF)
        what = f"Bu belge ({n_pages} sayfa)" if n_pages else "Bu belge"
        msg = f"{what} {needed} okuma hakkı ister, {'bugün' if by == 'day' else 'bu ay'} {left_} hakkın kaldı. {when}"
    elif by == "month":
        msg = f"Bu ayki {st['cap']} okuma hakkın doldu; {_on(_next_month())} yenilenir."
    else:
        msg = f"Bugünkü okuma hakkın doldu ({st['used']}/{st['limit']}). Yarın yenilenir."
    if _upgrade(u):
        msg += (f" Ek okuma paketi alabilir ya da Premium'a geçebilirsin (günde {plans.DAILY_READS[plans.PREMIUM]} "
                "okuma). Bu arada harcamanı elle ekleyebilirsin.")
    else:
        msg += " Ek okuma paketiyle hemen devam edebilir ya da harcamanı elle ekleyebilirsin."
    return msg


def _refusal(u: dict, st: dict, needed: int, kind: str, reread: bool = False, n_pages: int | None = None) -> QuotaError:
    from app import plans

    extra = {**plans.trial(u), "packs": [{"id": k, "reads": n} for k, n in plans.PACKS.items()],
             "upgrade_yearly": plans.upgrade_yearly(u), "extra_left": st["extra"], "plan": plans.plan_of(u),
             "period": "day", "daily_limit": st["limit"], "daily_used": st["used"], "left": st["left"],
             "resets_at": _resets(st), "needed": needed, "limited_by": st["by"], "monthly_cap": st["cap"],
             "monthly_used": st["month_used"], "reread": reread, **({"pages": n_pages} if n_pages else {})}
    limit = st["cap"] if st["by"] == "month" and st["left"] <= 0 else st["limit"]
    return QuotaError(message(u, st, needed, reread, n_pages), limit, _upgrade(u), kind, extra)


def check(key: bytes | str | None = None, kind: str = BELGE, needed: int | None = None, reread: bool = False) -> None:
    """Gereken hak (needed; verilmezse anahtarın ağırlığı, weight) bugünkü kalan + ek paket bakiyesinden fazlaysa
    QuotaError: hiçbir belge okunmaz (bilinen bankanın yerel ekstresi, daha önce okunmuş dosya dahil; kısmi okuma yok).
    Tekrar muafiyeti sayımdadır (charge)."""
    from app import system

    u = _subject()
    if not u:
        return
    from app import ai_restrictions
    ai_restrictions.check(u, kind)  # yönetici kısıtlaması: günlük üst sınır (app/ai_restrictions.py)
    n = needed if needed is not None else weight(key)
    with system.connect() as c:
        st = _state(c, u)
    if st["left"] + st["extra"] < n:
        raise _refusal(u, st, n, kind, reread, None if needed is not None else pages(key))


def allowed(key: bytes | str | None = None, kind: str = BELGE) -> bool:
    try:
        check(key, kind)
    except QuotaError:
        return False
    return True


def _spend(c, u: dict, n: int) -> dict:
    """n hak düş (açık bağlantıda, çağıranın işlemi içinde): önce bugünkü hak (aylık tavan dahil), kalanı ek paketten.
    İkisi de yetmezse (yarışta kontrol ile sayım arası) artan günlük sayaca yazılır (sınırı aşar, eksiye inmez)."""
    st = _state(c, u)
    daily = min(n, st["left"])
    extra = min(n - daily, st["extra"])
    if extra:
        c.execute("UPDATE read_credits SET balance = balance - ?, used = used + ? WHERE user_id = ?",
                  (extra, extra, u["id"]))
    over = n - daily - extra
    if daily + over:
        c.execute("INSERT INTO read_daily (user_id, day, count) VALUES (?, ?, ?) "
                  "ON CONFLICT(user_id, day) DO UPDATE SET count = count + excluded.count", (u["id"], _day(), daily + over))
    c.execute("INSERT INTO ai_usage (user_id, month, count) VALUES (?, ?, ?) "
              "ON CONFLICT(user_id, month) DO UPDATE SET count = count + excluded.count", (u["id"], _month(), n))
    return {"daily": daily + over, "extra": extra}


def charge(key: bytes | str, kind: str = BELGE, needed: int | None = None) -> dict | bool:
    """Başarıyla okunmuş YENİ belge: weight(key) hak (önce günlük, kalanı ek paket). Düşülen {daily, extra} döner.
    Aynı içerik bu defterde daha önce sayıldıysa False (hak düşmez)."""
    u = _subject()
    if not u:
        return False
    from app import ai_restrictions, system

    n = needed if needed is not None else weight(key)
    with system.connect() as c:  # kayıt + sayaç tek işlemde: yarışta iki istek aynı belgeyi iki kez sayamaz
        if not c.execute("INSERT OR IGNORE INTO doc_reads (book_id, hash, month, kind) VALUES (?, ?, ?, ?)",
                         (_book(u), _key(key), _month(), kind)).rowcount:
            return False
        ai_restrictions.count(c, u["id"])  # günlük okuma (yönetici kısıtlamasının üst sınırı)
        # tür başı okunan belge sayısı (istatistik: yönetici maliyet özeti, app/ai_costs.py); hak sayımı read_daily'de
        c.execute("INSERT INTO read_usage (user_id, month, kind, count) VALUES (?, ?, ?, 1) "
                  "ON CONFLICT(user_id, month, kind) DO UPDATE SET count = count + 1", (u["id"], _month(), kind))
        return _spend(c, u, n)


def check_reread() -> None:
    """Elle yeniden okuma: REREAD_COST hak yoksa QuotaError ("Yeniden okuma 2 hak ister...")."""
    check(None, FIS, REREAD_COST, reread=True)


def charge_reread() -> dict | None:
    """Elle yeniden okuma başarılı: REREAD_COST hak (tekrar muafiyeti yok: her yeniden okuma ayrı istek)."""
    u = _subject()
    if not u:
        return None
    from app import ai_restrictions, system

    with system.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        ai_restrictions.count(c, u["id"])  # yeniden okuma da yönetici kısıtlamasının günlük sınırına sayılır
        return _spend(c, u, REREAD_COST)


# ---------------------------------------------------------------- ek okuma paketi (mağaza: tüketilebilir ürün)

def add_pack(uid: int, product: str, txn: str, amount: int | None = None) -> bool:
    """Satın alınan paketi bakiyeye ekle. txn (mağaza işlem kimliği) ikinci kez gelirse False: idempotent."""
    from app import plans, system

    n = amount if amount is not None else plans.PACKS[product]
    with system.connect() as c:
        if not c.execute("INSERT OR IGNORE INTO read_packs (txn, user_id, product, amount) VALUES (?, ?, ?, ?)",
                         (txn, uid, product, n)).rowcount:
            return False
        c.execute("INSERT INTO read_credits (user_id, balance) VALUES (?, ?) "
                  "ON CONFLICT(user_id) DO UPDATE SET balance = balance + excluded.balance", (uid, n))
    return True


def refund_pack(uid: int, txn: str) -> int:
    """Mağaza iadesi: o işlemin hakkı bakiyeden düşer (harcanmış olanı geri alınamaz; bakiye eksiye inmez). Bir kez."""
    from app import system

    with system.connect() as c:
        r = c.execute("SELECT amount FROM read_packs WHERE txn = ? AND user_id = ? AND refunded = 0", (txn, uid)).fetchone()
        if not r:
            return 0
        c.execute("UPDATE read_packs SET refunded = 1 WHERE txn = ?", (txn,))
        b = c.execute("SELECT balance FROM read_credits WHERE user_id = ?", (uid,)).fetchone()
        take = min(int(r[0]), int(b[0]) if b else 0)
        if take:
            c.execute("UPDATE read_credits SET balance = balance - ? WHERE user_id = ?", (take, uid))
        return take
