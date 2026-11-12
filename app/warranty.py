"""Garanti arşivi: ürün, alış tarihi, garanti süresi ve belgeleri (fatura / fiş, garanti belgesi fotoğrafı ya da PDF).

- Bitiş = alış tarihi + months + extra_months (takvim ayı; ayın son gününe kırpılır). months varsayılanı
  DEFAULT_MONTHS (Türkiye'de tüketici ürünlerinde yasal garanti en az 2 yıl); kullanıcı değiştirir.
- Durum: aktif | yakinda (bitişe SOON_DAYS gün ya da daha az) | bitti. Kalan gün bugünden (Türkiye günü) sayılır.
- Belgeler (warranty_files): bu ürüne yüklenen dosya (depo türü "warranty", <sha256>.jpg|pdf; fotoğraf EXIF'siz
  küçültülür, app/images.py) ya da bağlı fişin fotoğrafı (tür "receipts", kayıtla ortak dosya). Ürün başına en çok
  MAX_FILES belge. Ürün silinince başka yerde kullanılmayan dosyası da silinir.
- Kaynak: kayıtlı fiş/elle kayıt (entry_id) ya da kart harcaması (txn_id); draft_from_* formu doldurur (ad, fiyat, tarih,
  mağaza, fiş kalemleri, fiş fotoğrafı). Yeni fotoğraf: istemci mevcut okuma akışını (POST /api/receipts/parse; okuma
  hakkı kuralları) kullanır, dönen receipt_file ve seçilen kalemle ürün kaydedilir.
- Ortak defter: ürün defterindir. Üye kayıt/harcamayı göremiyorsa (gizli kart/hesap/kayıt) o kaynağa bağlı ürünü de
  görmez (fiyatı gizli harcamayı ele vermesin). "Görür ve ekler" üye ekler; düzenleme, silme ve belge işleri yalnız kendi
  eklediği üründe. Sahibin "Garantiler" sayfasını kapatması uçları da kapatır (app/book.PAGES).
- Paket: plans "garanti" satırının paketinde değilse defterde en çok plans.FREE_WARRANTIES ürün (402).
"""
import calendar
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app import db

DEFAULT_MONTHS = 24
MAX_MONTHS = 240           # 20 yıl
SOON_DAYS = 30
MAX_FILES = 5
DOC_TYPES = {"fatura": "Fatura / fiş", "garanti": "Garanti belgesi", "diger": "Diğer"}
FIELDS = ("name", "brand", "model", "store", "purchase_date", "price", "months", "extra_months", "serial_no", "note")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Istanbul"))
STATUS_LABELS = {"aktif": "Garantide", "yakinda": "Bitmek üzere", "bitti": "Garantisi bitti"}


class Forbidden(PermissionError):
    """Bu üye bu ürünü değiştiremez (başkasının eklediği)."""


def today() -> date:
    return datetime.now(TZ).date()


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def ends_at(w: dict) -> date:
    return add_months(date.fromisoformat(str(w["purchase_date"])[:10]), int(w.get("months") or 0) + int(w.get("extra_months") or 0))


def status_of(days_left: int) -> str:
    return "bitti" if days_left < 0 else "yakinda" if days_left <= SOON_DAYS else "aktif"


def left_text(days_left: int) -> str:
    """Listede ve ayrıntıda kalan süre: "1 yıl 3 ay kaldı", "12 gün kaldı", "Bugün bitiyor", "3 ay önce bitti"."""
    if days_left == 0:
        return "Bugün bitiyor"
    n = abs(days_left)
    if n < 31:
        part = f"{n} gün"
    elif n // 30 < 12:
        part = f"{n // 30} ay"
    else:
        y, m = divmod(n // 30, 12)
        part = f"{y} yıl" + (f" {m} ay" if m else "")
    return f"{part} kaldı" if days_left > 0 else f"{part} önce bitti"


# ---------------------------------------------------------------- görünürlük ve yetki

def _me() -> int | None:
    u = db.current_user()
    return u.get("id") if u else None


def _visible(w: dict) -> bool:
    """Ortak defter üyesi: göremediği kayıt / harcamaya bağlı ürün yok sayılır."""
    from app import book

    if not book.member():
        return True
    if w.get("entry_id") and not book.can_see_entry(w["entry_id"]):
        return False
    if w.get("txn_id") and not book.can_see_txn(w["txn_id"]):
        return False
    return True


def can_edit(w: dict) -> bool:
    """Sahip ve tam yetkili üye her ürünü; "görür ve ekler" üye yalnız kendi eklediğini değiştirir."""
    from app import book

    m = book.member()
    if not m:
        return True
    r = book.role(m)
    return r == "full" or (r == "editor" and w.get("created_by") == m["id"])


# ---------------------------------------------------------------- okuma

def _files(c, ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    q = ",".join("?" * len(ids))
    for r in c.execute(f"SELECT * FROM warranty_files WHERE warranty_id IN ({q}) ORDER BY id", ids):
        out[r["warranty_id"]].append({
            "id": r["id"], "doc_type": r["doc_type"], "doc_label": DOC_TYPES.get(r["doc_type"], "Belge"),
            "filename": r["filename"], "size": r["size"], "pdf": r["name"].endswith(".pdf"),
            "from_receipt": r["kind"] == "receipts", "created_at": r["created_at"]})
    return out


def _out(w: dict, files: list[dict], on: date) -> dict:
    end = ends_at(w)
    left = (end - on).days
    st = status_of(left)
    return {**{k: w.get(k) for k in ("id", *FIELDS, "entry_id", "txn_id", "created_by", "created_at")},
            "purchase_date": str(w["purchase_date"])[:10], "ends_at": end.isoformat(), "days_left": left,
            "status": st, "status_label": STATUS_LABELS[st], "left_text": left_text(left),
            "total_months": int(w.get("months") or 0) + int(w.get("extra_months") or 0),
            "files": files, "can_edit": can_edit(w)}


def _raw(c, wid: int) -> dict | None:
    r = c.execute("SELECT * FROM warranties WHERE id = ?", (wid,)).fetchone()
    return dict(r) if r else None


def get(wid: int, on: date | None = None) -> dict | None:
    with db.connect() as c:
        w = _raw(c, wid)
        if not w or not _visible(w):
            return None
        return _out(w, _files(c, [wid])[wid], on or today())


def all_visible(on: date | None = None) -> list[dict]:
    """Görünen ürünler (bitişi yakın olan önce; bitenler sonda, yeni biten önce)."""
    on = on or today()
    with db.connect() as c:
        ws = [dict(r) for r in c.execute("SELECT * FROM warranties ORDER BY id")]
        ws = [w for w in ws if _visible(w)]
        files = _files(c, [w["id"] for w in ws])
    out = [_out(w, files[w["id"]], on) for w in ws]
    out.sort(key=lambda x: (x["status"] == "bitti", -x["days_left"] if x["status"] == "bitti" else x["days_left"], -x["id"]))
    return out


def _match(w: dict, q: str) -> bool:
    from app.textnorm import normalize

    hay = normalize(" ".join(str(w.get(k) or "") for k in ("name", "brand", "model", "store", "serial_no", "note")))
    return all(t in hay for t in normalize(q).split())


def count() -> int:
    """Defterdeki ürün sayısı (paket sınırı: üye görmese de defterin ürünüdür)."""
    with db.connect() as c:
        return c.execute("SELECT COUNT(*) FROM warranties").fetchone()[0]


def quota(u: dict | None) -> dict:
    """{limit (None = sınırsız), used, locked (yeni ürün eklenemez), need (gereken paket adı)}."""
    from app import plans

    used = count()
    if plans.allows(u, "garanti"):
        return {"limit": None, "used": used, "locked": False, "need": None}
    return {"limit": plans.FREE_WARRANTIES, "used": used, "locked": used >= plans.FREE_WARRANTIES,
            "need": plans._needs("garanti")}


def listing(u: dict | None, q: str | None = None, status: str | None = None, on: date | None = None) -> dict:
    items = all_visible(on)
    counts = {"aktif": 0, "yakinda": 0, "bitti": 0}
    for w in items:
        counts[w["status"]] += 1
    counts["all"] = len(items)
    if q and q.strip():
        items = [w for w in items if _match(w, q)]
    if status in counts and status != "all":
        items = [w for w in items if w["status"] == status]
    return {"items": items, "counts": counts, "quota": quota(u), "default_months": DEFAULT_MONTHS,
            "soon_days": SOON_DAYS, "max_files": MAX_FILES, "doc_types": DOC_TYPES}


# ---------------------------------------------------------------- yazma

def _clean(data: dict) -> dict:
    out = {}
    for k in FIELDS:
        if k not in data:
            continue
        v = data[k]
        if isinstance(v, str):
            v = " ".join(v.split()) if k != "note" else v.strip()
            v = v or None
        if k == "purchase_date" and v is not None:
            v = str(v)[:10]
        out[k] = v
    if "name" in out and not out["name"]:
        raise ValueError("Ürün adı gerekli")
    for k in ("months", "extra_months"):
        if k in out:
            if out[k] is None:
                out[k] = DEFAULT_MONTHS if k == "months" else 0
            if not 0 <= int(out[k]) <= MAX_MONTHS:
                raise ValueError("Garanti süresi 0–240 ay olmalı")
            out[k] = int(out[k])
    return out


def create(data: dict, entry_id: int | None = None, txn_id: int | None = None, client_key: str | None = None) -> int:
    d = _clean(data)
    if not d.get("name") or not d.get("purchase_date"):
        raise ValueError("Ürün adı ve alış tarihi gerekli")
    d.setdefault("months", DEFAULT_MONTHS)
    d.setdefault("extra_months", 0)
    with db.connect() as c:
        if client_key:
            dup = c.execute("SELECT id FROM warranties WHERE client_key = ?", (client_key,)).fetchone()
            if dup:
                return dup["id"]
        cols = {**d, "entry_id": entry_id, "txn_id": txn_id, "created_by": _me(), "client_key": client_key}
        return c.execute(f"INSERT INTO warranties ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         tuple(cols.values())).lastrowid


def _editable(c, wid: int) -> dict:
    w = _raw(c, wid)
    if not w or not _visible(w):
        raise KeyError(wid)
    if not can_edit(w):
        raise Forbidden("Bu ürünü ekleyen kişi ya da defter sahibi değiştirebilir")
    return w


def update(wid: int, data: dict) -> None:
    d = _clean(data)
    with db.connect() as c:
        _editable(c, wid)
        if not d:
            return
        d["updated_at"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        c.execute(f"UPDATE warranties SET {', '.join(f'{k} = ?' for k in d)} WHERE id = ?", (*d.values(), wid))


def _drop_unused(kind: str, name: str) -> None:
    """Dosya artık hiçbir garantide ve (fişse) hiçbir kayıtta yoksa depodan sil."""
    with db.connect() as c:
        used = c.execute("SELECT 1 FROM warranty_files WHERE kind = ? AND name = ?", (kind, name)).fetchone()
        if not used and kind == "receipts":
            used = c.execute("SELECT 1 FROM entries WHERE receipt_file = ?", (name,)).fetchone()
    if not used:
        db.remove_file(kind, name)


def delete(wid: int) -> None:
    with db.connect() as c:
        _editable(c, wid)
        files = [(r["kind"], r["name"]) for r in c.execute("SELECT kind, name FROM warranty_files WHERE warranty_id = ?", (wid,))]
        c.execute("DELETE FROM warranty_files WHERE warranty_id = ?", (wid,))
        c.execute("DELETE FROM warranties WHERE id = ?", (wid,))
    for kind, name in set(files):
        _drop_unused(kind, name)


class TooMany(ValueError):
    pass


def link_file(wid: int, kind: str, name: str, doc_type: str = "diger", filename: str | None = None,
              size: int | None = None, check: bool = True) -> int:
    """Dosyayı ürüne bağla (aynı dosya ikinci kez bağlanmaz: var olanın id'si döner)."""
    with db.connect() as c:
        if check:
            _editable(c, wid)
        dup = c.execute("SELECT id FROM warranty_files WHERE warranty_id = ? AND kind = ? AND name = ?",
                        (wid, kind, name)).fetchone()
        if dup:
            return dup["id"]
        n = c.execute("SELECT COUNT(*) FROM warranty_files WHERE warranty_id = ?", (wid,)).fetchone()[0]
        if n >= MAX_FILES:
            raise TooMany(f"Bir ürüne en çok {MAX_FILES} belge eklenebilir; önce birini kaldır")
        return c.execute("INSERT INTO warranty_files (warranty_id, kind, name, doc_type, filename, size) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (wid, kind, name, doc_type if doc_type in DOC_TYPES else "diger",
                          (filename or "")[:120] or None, size)).lastrowid


def file_row(wid: int, fid: int) -> dict | None:
    with db.connect() as c:
        w = _raw(c, wid)
        if not w or not _visible(w):
            return None
        r = c.execute("SELECT * FROM warranty_files WHERE id = ? AND warranty_id = ?", (fid, wid)).fetchone()
        return {**dict(r), "warranty": w} if r else None


def unlink_file(wid: int, fid: int) -> None:
    with db.connect() as c:
        _editable(c, wid)
        r = c.execute("SELECT kind, name FROM warranty_files WHERE id = ? AND warranty_id = ?", (fid, wid)).fetchone()
        if not r:
            raise KeyError(fid)
        c.execute("DELETE FROM warranty_files WHERE id = ?", (fid,))
    _drop_unused(r["kind"], r["name"])


def can_add_file(wid: int) -> int:
    """Belge eklenebilir mi (yetki + sınır); mevcut belge sayısı. Dosya saklanmadan ÖNCE denetlenir."""
    with db.connect() as c:
        _editable(c, wid)
        n = c.execute("SELECT COUNT(*) FROM warranty_files WHERE warranty_id = ?", (wid,)).fetchone()[0]
    if n >= MAX_FILES:
        raise TooMany(f"Bir ürüne en çok {MAX_FILES} belge eklenebilir; önce birini kaldır")
    return n


# ---------------------------------------------------------------- fişten / harcamadan taslak

def draft_from_entry(entry_id: int) -> dict | None:
    """Kayıtlı fiş ya da elle kayıt → form: ad (tek kalemse kalem adı), fiyat, tarih, mağaza, kalemler, fiş fotoğrafı."""
    with db.connect() as c:
        e = c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not e:
            return None
        e = dict(e)
        items = [dict(r) for r in c.execute("SELECT name, qty, amount, merchant FROM receipt_items WHERE entry_id = ? "
                                            "ORDER BY id", (entry_id,))]
    store = next((i["merchant"] for i in items if i.get("merchant")), None) or e["description"]
    one = items[0] if len(items) == 1 else None
    return {"source": "entry", "entry_id": entry_id, "txn_id": None, "store": store,
            "purchase_date": str(e["entry_date"])[:10],
            "name": one["name"] if one else (None if items else e["description"]),
            "price": one["amount"] if one else (None if items else e["amount"]),
            "items": [{"name": i["name"], "qty": i["qty"], "amount": i["amount"]} for i in items if i["amount"] and i["amount"] > 0],
            "receipt_file": e.get("receipt_file"), "total": e["amount"], "default_months": DEFAULT_MONTHS}


def draft_from_txn(txn_id: int) -> dict | None:
    """Kart harcaması → form: işyeri (mağaza ve ad önerisi), tarih, tutar (taksitliyse toplam)."""
    from app.textnorm import merchant_name

    t = next((x for x in db.card_transactions(include_deleted=True) if x["id"] == txn_id), None)
    if not t:
        return None
    n = (t.get("installment_no") or 0) + (t.get("installments_left") or 0)
    price = round(t["amount"] * n, 2) if t.get("installment_no") and n > 1 else t["amount"]
    store = merchant_name(t["description"]).title() or t["description"]
    return {"source": "txn", "entry_id": None, "txn_id": txn_id, "store": store, "purchase_date": str(t["txn_date"])[:10],
            "name": None, "price": abs(price), "items": [], "receipt_file": None, "total": abs(price),
            "installments": n if t.get("installment_no") and n > 1 else None, "default_months": DEFAULT_MONTHS}


def receipts(q: str | None = None, limit: int = 30) -> list[dict]:
    """"Kayıtlı fişten ekle": fotoğrafı ya da kalemleri olan son kayıtlar (üyenin göremediği kayıt yok)."""
    from app import book
    from app.textnorm import normalize

    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT e.id, e.entry_date, e.description, e.amount, e.receipt_file, e.hidden, e.account_id, e.card_last4, "
            "e.payment, e.created_by, (SELECT COUNT(*) FROM receipt_items i WHERE i.entry_id = e.id) AS n "
            "FROM entries e WHERE e.kind = 'gider' AND (e.receipt_file IS NOT NULL OR EXISTS "
            "(SELECT 1 FROM receipt_items i WHERE i.entry_id = e.id)) ORDER BY e.entry_date DESC, e.id DESC LIMIT 400")]
        names: dict[int, list[str]] = {}
        ids = [r["id"] for r in rows]
        if ids:
            for r in c.execute(f"SELECT entry_id, name FROM receipt_items WHERE entry_id IN ({','.join('?' * len(ids))}) "
                               "ORDER BY id", ids):
                names.setdefault(r["entry_id"], []).append(r["name"])
    if book.member():
        rows = [r for r in rows if book.can_see_entry(r["id"])]
    out = []
    nq = normalize(q or "").split()
    for r in rows:
        if nq:
            hay = normalize(" ".join([r["description"] or "", *names.get(r["id"], [])]))
            if not all(t in hay for t in nq):
                continue
        out.append({"entry_id": r["id"], "date": str(r["entry_date"])[:10], "store": r["description"], "amount": r["amount"],
                    "items": r["n"], "photo": bool(r["receipt_file"]), "preview": names.get(r["id"], [])[:3]})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- dışa aktarma ve hatırlatma

def export_rows() -> list[list]:
    out = []
    for w in all_visible():
        out.append([w["name"], " ".join(x for x in (w.get("brand"), w.get("model")) if x) or None, w.get("store"),
                    w["purchase_date"], w.get("price"), w["months"], w["extra_months"], w["ends_at"], w["status_label"],
                    w.get("serial_no"), len(w["files"]), w.get("note")])
    return out


EXPORT_HEAD = ["Ürün", "Marka / model", "Mağaza", "Alış tarihi", "Tutar (₺)", "Garanti (ay)", "Ek garanti (ay)",
               "Bitiş", "Durum", "Seri no", "Belge", "Not"]
PUSH_STAGES = ((7, "7gun"), (SOON_DAYS, "30gun"))  # kalan gün bu sınırın içindeyse o aşamanın tek bildirimi


def push_stage(days_left: int) -> str | None:
    """Bitişe 30 gün kala (7 günden fazla kaldıysa) "30gun", 7 gün ve daha az kala "7gun"; bitmişse yok. Günlük iş bir gün
    çalışmasa ya da ürün bu aralıkta eklenirse de aşama kaçmaz; kimlik aşamaya bağlı olduğundan her aşama bir kez."""
    if days_left < 0:
        return None
    for limit, tag in PUSH_STAGES:
        if days_left <= limit:
            return tag
    return None
