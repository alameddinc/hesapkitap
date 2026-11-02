"""Okuma hakkı (yalnız hazır sürüm, plans.hosted()).

İki ayrı aylık sayaç (plans.READS), kişi başı (ortak defterde her üyenin kendi sayacı):
- "belge": kart ekstresi (PDF/Excel), hesap dökümü, fatura (dosya ya da mail metni), deftere özel posta adresine gelen
  belge/bildirim, yapay zekâ kategori önerisi (/api/uncategorized/ai-suggest; bir istek = 1).
- "fis": fiş fotoğrafı (tek ya da çok sayfalı fiş tek belge).
Okuma yöntemi (yerel ayrıştırıcı, OCR, Gemini) ve bir belgenin içindeki yapay zekâ çağrısı sayısı fark etmez. Kayıtlı
fişi yeniden okuma (receipt-reread, otomatik kademeli okuma) hak düşürmez: belge ilk okumada sayıldı.

- Kontrol işten ÖNCE: check(key, kind) hak doluysa QuotaError (receipt.ReceiptError) atar; belge okunmaz. Türü okumadan
  belli olmayan belgede (/api/documents/parse, paylaşım) kontrol tahmine göredir (fotoğraf → fiş, PDF → belge); sayım
  okunan gerçek türe gider (fiş → fis, diğerleri → belge). Tahmin yanlışsa o tek belge sayacı 1 aşabilir.
- Sayım işten SONRA ve yalnız yeni belge okunduysa: charge(key, kind). Hata çıkan, "zaten vardı" sonucu veren okumada
  charge çağrılmaz.
- charge idempotenttir: aynı içerik (key = dosya baytları ya da "mail:<Message-ID>") aynı defterde ikinci kez sayılmaz
  (system.doc_reads kaydı). Bu yüzden aynı belgeyi hem dış uç (endpoint) hem iç işleyici (mailbox.handle_pdf) sayabilir;
  çift sayım olmaz.
- Ek okuma paketi (plans.PACKS, mağazada tüketilebilir ürün → RevenueCat NON_RENEWING_PURCHASE): read_credits bakiyesi.
  Önce aylık hak, o türün aylık hakkı bitince ek hak düşer; ek hak belge ve fişte ortaktır, ay dönümünde silinmez, paket
  değişince (ör. Premium'a geçince) de kalır. Aynı mağaza işlemi (transaction id) iki kez yazılmaz.
- Hak dolunca hiçbir şey kilitlenmez: yalnız okuma durur, kayıt elle eklenir.
- Sayaçlar system.read_usage (tür başı, aylık hak) ve system.ai_usage (toplam okunan belge; yönetici listesi, eski
  istemci). Eski tek sayaçtan göç: _split (ilk erişimde bir kez, idempotent).
- Hazır sürümde receipt._check_quota/_count_usage (yapay zekâ çağrısı başına) kapalıdır.

Kendi sunucusunda bu modül hiçbir şey yapmaz (kota yapay zekâ çağrısı başına, receipt.py). Yönetici sınırsız.
"""
import hashlib
import os
import sqlite3
from datetime import date

from app.receipt import ReceiptError

BELGE, FIS = "belge", "fis"


class QuotaError(ReceiptError):
    """Hak doldu. İstemci elle girişe yönlendirir: fields() hata yanıtına eklenir."""

    def __init__(self, msg: str, limit: int, upgrade: bool, kind: str = BELGE, extra: dict | None = None):
        super().__init__(msg)
        self.limit, self.upgrade, self.kind = limit, upgrade, kind
        self.extra = extra or {}

    def fields(self) -> dict:
        # manual/quota_full/limit/upgrade: eski istemciler; reason/kind/...: yapılandırılmış "hak doldu" gövdesi
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


def _month() -> str:
    return date.today().strftime("%Y-%m")


def kind_of_doc(doc_type: str | None) -> str:
    """parse_document sonucunun türü → sayaç: fiş → fis, diğerleri (fatura, dekont...) → belge."""
    return FIS if doc_type == "receipt" else BELGE


def guess_kind(mime: str | None) -> str:
    """Türü okumadan bilinmeyen belgede ön kontrol: fotoğraf → fiş, PDF/metin → belge."""
    return FIS if (mime or "").startswith("image/") else BELGE


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
    """Bu ay aylık haktan düşen (ek paketten düşenler hariç)."""
    from app import system

    month = month or _month()
    _split(u, month)
    with system.connect() as c:
        r = c.execute("SELECT count FROM read_usage WHERE user_id = ? AND month = ? AND kind = ?",
                      (u["id"], month, kind)).fetchone()
        return int(r[0]) if r else 0


def credits(uid: int) -> int:
    """Ek okuma paketi bakiyesi (belge + fiş ortak)."""
    from app import system

    with system.connect() as c:
        r = c.execute("SELECT balance FROM read_credits WHERE user_id = ?", (uid,)).fetchone()
        return int(r[0]) if r else 0


def status(u: dict | None) -> dict | None:
    """Arayüz için: {belge: {used, limit, left}, fis: {...}, extra}. Kendi sunucusu / yönetici: None (sınırsız)."""
    from app import plans

    if not u or not plans.hosted() or u.get("role") == "admin" or not u.get("id"):
        return None
    out: dict = {}
    for kind in (BELGE, FIS):
        lim, n = plans.read_limit(u, kind), used(u, kind)
        out[kind] = {"used": n, "limit": lim, "left": max(0, lim - n)}
    out["extra"] = credits(u["id"])
    return out


def left(u: dict | None, kind: str) -> int | None:
    """Kalan hak (aylık + ek). None: sınırsız."""
    st = status(u)
    return None if st is None else st[kind]["left"] + st["extra"]


def _upgrade(u: dict) -> bool:
    from app import plans

    return plans.plan_of(u) == plans.FREE


def message(u: dict, quota: int, kind: str = BELGE) -> str:
    from app import plans

    noun = plans.KIND_LABELS[kind]
    msg = f"Bu ayki {quota} {noun} okuma hakkın doldu. Ay başında yenilenir. Bu arada harcamanı elle ekleyebilirsin."
    if _upgrade(u):
        msg += f" Premium'da ayda {plans.READS[plans.PREMIUM][kind]} {noun}."
    return msg


def _refusal(u: dict, quota: int, kind: str) -> QuotaError:
    from app import plans

    extra = {**plans.trial(u), "packs": [{"id": k, "reads": n} for k, n in plans.PACKS.items()],
             "upgrade_yearly": plans.upgrade_yearly(u), "extra_left": credits(u["id"]), "plan": plans.plan_of(u)}
    return QuotaError(message(u, quota, kind), quota, _upgrade(u), kind, extra)


def check(key: bytes | str | None = None, kind: str = BELGE) -> None:
    """Hak doluysa QuotaError: hiçbir belge okunmaz (bilinen bankanın yerel ekstresi, daha önce okunmuş dosya dahil).
    Hak dolu = o türün aylık hakkı bitti VE ek okuma paketi bakiyesi yok. key yalnız imza uyumu için (tekrar muafiyeti
    sayımdadır)."""
    from app import plans

    u = _subject()
    if not u:
        return
    quota = plans.read_limit(u, kind)
    if used(u, kind) >= quota and credits(u["id"]) <= 0:
        raise _refusal(u, quota, kind)


def allowed(key: bytes | str | None = None, kind: str = BELGE) -> bool:
    try:
        check(key, kind)
    except QuotaError:
        return False
    return True


def charge(key: bytes | str, kind: str = BELGE) -> bool:
    """Başarıyla okunmuş YENİ belge: 1 hak (önce aylık, o bittiyse ek paket). Aynı içerik bu defterde daha önce
    sayıldıysa False (hak düşmez)."""
    u = _subject()
    if not u:
        return False
    from app import plans, system

    month = _month()
    quota = plans.read_limit(u, kind)
    _split(u, month)
    with system.connect() as c:  # kayıt + sayaç tek işlemde: yarışta iki istek aynı belgeyi iki kez sayamaz
        if not c.execute("INSERT OR IGNORE INTO doc_reads (book_id, hash, month, kind) VALUES (?, ?, ?, ?)",
                         (_book(u), _key(key), month, kind)).rowcount:
            return False
        c.execute("INSERT INTO ai_usage (user_id, month, count) VALUES (?, ?, 1) "
                  "ON CONFLICT(user_id, month) DO UPDATE SET count = count + 1", (u["id"], month))
        r = c.execute("SELECT count FROM read_usage WHERE user_id = ? AND month = ? AND kind = ?",
                      (u["id"], month, kind)).fetchone()
        if (int(r[0]) if r else 0) >= quota and c.execute(
                "UPDATE read_credits SET balance = balance - 1, used = used + 1 WHERE user_id = ? AND balance > 0",
                (u["id"],)).rowcount:
            return True  # aylık hak bitmişti: ek paketten düştü
        # aylık haktan (ya da tahmin yanlış çıkan tek belgede sınırın 1 üstüne)
        c.execute("INSERT INTO read_usage (user_id, month, kind, count) VALUES (?, ?, ?, 1) "
                  "ON CONFLICT(user_id, month, kind) DO UPDATE SET count = count + 1", (u["id"], month, kind))
    return True


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
