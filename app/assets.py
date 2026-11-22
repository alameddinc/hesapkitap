"""Varlıklarım (Aşama 1 — takvim): araç, konut ve işyerinin dönemlik yükümlülükleri (MTV, emlak vergisi, sigorta,
muayene, bakım, kira artışı…) ve bunlardan üretilen son günler (vadeler).

Kavramlar (docs: hesapkitap-mobile docs/varliklar-ozellik.md):
- Varlık (assets): kind arac | konut | isyeri. Defter içinde yaşar (warranties gibi). Silme yumuşaktır (deleted_at):
  "Geri al" restore ile döner; elden çıkarma (dispose) gelecekteki bekleyen vadeleri "atlandi" yapar, geri alınabilir.
- Yükümlülük (asset_obligations): varlığa bağlı tekrarlayan kural (rule JSON). amount: sabit tarihli türlerde (MTV,
  emlak vergisi, kira geliri beyanı) YILLIK tutar (taksitlere eşit bölünür), diğerlerinde dönem başı tutar. Tutar
  hesaplanmaz, sabit kodlanmaz: kullanıcı girer (MTV'de geçen yılın ödemesinden öneri: mtv_hint).
- Vade (asset_dues): kuraldan üretilen tek son gün. status bekliyor | odendi | atlandi; "gecikti" saklanmaz (bekliyor ve
  son gün — resmî tatil/hafta sonuysa ilk iş günü, app/workdays.py — geçti). Üretim idempotent (UNIQUE(obligation_id,
  seq)); ileriye HORIZON_MONTHS ay. Kural değişince yalnız bugünden sonraki bekleyen vadeler yeniden üretilir.
- Aidat aylıktır: vade üretilmez, mevcut sabit kalem (recurring) satırına bağ (recurring_id).
- Uygulamanın bildiği tek şey tarih kurallarıdır (bkz. LAW). Ceza / oran TUTARI yazılmaz; yalnız niteliksel bilgi ve
  resmî kaynak linki. Faiz, kredi, taksitlendirme önerisi yok: önerilen tek davranış "bu ay ayır" / Kumbara.

Paket (plans "varliklar"): ücretsizde kayıt yok (kilitli önizleme), Premium ve Aile sınırsız. Paket düşerse kayıtlar
ve Planlama'daki vadeler kalır (salt-okunur), yalnız push durur. Yazma uçları plans.GATED ile kapalı.
Ortak defter: varlık defterindir, gizli varlık yok. "Görür ve ekler" üye ekler; düzenleme/silme yalnız kendi eklediğinde,
vadeyi "ödendi" işaretlemek her ortak vadede (kim ödedi: paid_by). Bildirim sahip + tam yetkili üyeye.
"""
import calendar
import json
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import db

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Istanbul"))
HORIZON_MONTHS = 24
SOON_DAYS = 30
MAX_FILES = 5

KINDS = {"arac": "Araç", "konut": "Konut", "isyeri": "İşyeri"}
VEHICLE_CLASSES = {"otomobil": "Otomobil", "kamyonet": "Kamyonet", "motosiklet": "Motosiklet", "diger": "Diğer"}
FUELS = {"benzin": "Benzin", "dizel": "Dizel", "lpg": "LPG", "hibrit": "Hibrit", "elektrik": "Elektrik", "diger": "Diğer"}
TENURES = {"sahibi": "Sahibiyim", "kiraci": "Kiracıyım", "kiraya_verdim": "Sahibiyim, kirada"}
DOC_TYPES = {"ruhsat": "Ruhsat", "police": "Poliçe", "tapu": "Tapu", "sozlesme": "Sözleşme", "diger": "Diğer"}

# tür → (ad, push türü, kural tipi). Push türü: app/push.TYPES varlik_*
OBLIGATIONS = {
    "mtv": ("MTV", "varlik_vergi", "fixed"),
    "trafik": ("Trafik sigortası", "varlik_sigorta", "anniversary"),
    "kasko": ("Kasko", "varlik_sigorta", "anniversary"),
    "muayene": ("Muayene", "varlik_muayene", "anniversary"),
    "bakim": ("Bakım", "varlik_bakim", "anniversary"),
    "emlak": ("Emlak vergisi", "varlik_vergi", "fixed"),
    "dask": ("DASK", "varlik_sigorta", "anniversary"),
    "konut_sig": ("Konut sigortası", "varlik_sigorta", "anniversary"),
    "aidat": ("Aidat", None, "recurring"),
    "kira_artis": ("Kira artış dönemi", "varlik_bakim", "anniversary"),
    "kira_beyan": ("Kira geliri beyanı", "varlik_vergi", "fixed"),
    "diger": ("Diğer", "varlik_bakim", "anniversary"),
}
# sabit tarihli türler: (ay-gün, taksit adı)
FIXED = {
    "mtv": (("01-31", "1. taksit"), ("07-31", "2. taksit")),            # 197 s. MTV Kanunu, ödeme zamanı
    "emlak": (("05-31", "1. taksit"), ("11-30", "2. taksit")),          # 1319 s. Emlak Vergisi Kanunu md. 30
    "kira_beyan": (("03-31", "beyan ve 1. taksit"), ("07-31", "2. taksit")),  # GVK yıllık beyan; doğrulanmalı
}
# öneri: varlık türü / sahiplik → (açık önerilen, kapalı önerilen)
SUGGEST = {
    "arac": (("mtv", "trafik", "muayene"), ("kasko", "bakim")),
    "sahibi": (("emlak", "dask"), ("konut_sig", "aidat")),
    "kiraci": (("kira_artis",), ("konut_sig", "aidat")),
    "kiraya_verdim": (("emlak", "dask", "kira_beyan"), ("konut_sig", "kira_artis", "aidat")),
}

# Mevzuat bilgisi (tek kaynak; docs/varliklar-ozellik.md §13, doğrulama 22.11.2026). verified False: uygulamada
# "doğrulanmalı / tahmin" olarak gösterilir.
LAW = {
    "mtv": {"late": "Son günden sonra her ay için gecikme zammı işler (oranı GİB'de yayımlanır).",
            "where": "GİB Dijital Vergi Dairesi, e-Devlet ya da banka", "url": "https://dijital.gib.gov.tr",
            "verified": True},
    "emlak": {"late": "Son günden sonra her ay için gecikme zammı işler; belediyen ödeme ayını değiştirmiş olabilir.",
              "where": "Belediyenin e-belediye sayfası ya da veznesi", "url": None, "verified": True},
    "kira_beyan": {"late": "Beyan 1–31 Mart; vergi Mart ve Temmuz sonunda iki taksit. İstisna tutarını GİB'den kontrol et.",
                   "where": "GİB Hazır Beyan Sistemi", "url": "https://hazirbeyan.gib.gov.tr", "verified": False},
    "trafik": {"late": "Zorunlu trafik sigortası olmayan araç trafikten men edilir.", "where": None, "url": None,
               "verified": True},
    "kasko": {"late": "Kasko isteğe bağlıdır; bitince hasar karşılanmaz.", "where": None, "url": None, "verified": True},
    "muayene": {"late": "Süresi geçen her ay için muayene ücreti %5 fazlasıyla alınır; muayenesiz araçla trafiğe "
                        "çıkmak idari para cezası gerektirir.", "where": "TÜVTÜRK randevu",
                "url": "https://www.tuvturk.com.tr", "verified": True},
    "dask": {"late": "Zorunlu deprem sigortası; elektrik ve su aboneliğinde aranır, hasarda poliçe gerekir.",
             "where": None, "url": "https://www.dask.gov.tr", "verified": True},
    "konut_sig": {"late": "Bitince eşya ve konut hasarı karşılanmaz.", "where": None, "url": None, "verified": True},
    "kira_artis": {"late": "Artış, önceki kira yılının TÜFE 12 aylık ortalamasını geçemez (TBK md. 344). Oranı TÜİK'ten "
                           "kontrol et.", "where": None, "url": "https://data.tuik.gov.tr", "verified": True},
    "bakim": {"late": None, "where": None, "url": None, "verified": True},
    "diger": {"late": None, "where": None, "url": None, "verified": True},
    "aidat": {"late": None, "where": None, "url": None, "verified": True},
}
PLATE_RE = re.compile(r"^(0[1-9]|[1-7][0-9]|8[01])[A-ZÇĞİÖŞÜ]{1,3}\d{2,4}$")
ASSET_FIELDS = ("kind", "name", "plate", "vehicle_class", "model_year", "commercial", "fuel", "tenure", "province",
                "acquired_on", "note")
STATUS_LABELS = {"bekliyor": "Bekliyor", "gecikti": "Gecikti", "odendi": "Ödendi", "atlandi": "Atlandı"}


class Forbidden(PermissionError):
    """Bu üye bu varlığı değiştiremez (başkasının eklediği / sadece görür)."""


def today() -> date:
    return datetime.now(TZ).date()


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _d(v) -> date | None:
    return None if not v else v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def normalize_plate(p: str | None) -> str | None:
    """Plaka: boşluksuz, büyük harf (Türkçe i → İ). Biçim yanlışsa ValueError; foreign=True çağıran atlar."""
    if p is None:
        return None
    s = re.sub(r"[\s-]+", "", p.replace("i", "İ").replace("ı", "I").upper())
    return s or None


def plate_ok(p: str) -> bool:
    return bool(PLATE_RE.fullmatch(p))


# ---------------------------------------------------------------- muayene kuralı (RG 06.01.2021/31356 md. 14)

def inspection_period(vehicle_class: str | None, commercial: bool) -> tuple[int, int]:
    """(ilk muayene yaşı ay, sonraki periyot ay). Araç Muayene İstasyonlarının Açılması, İşletilmesi ve Araç Muayenesi
    Hakkında Yönetmelik md. 14: hususi otomobil ilk 3 yaş sonunda, sonra 2 yılda bir; iki-üç tekerlekli (motosiklet) ilk
    3 yaş, sonra 2 yılda bir; diğer bütün motorlu araçlar (ticari otomobil, kamyonet dahil) ilk 1 yaş, sonra yılda bir."""
    vc = vehicle_class or "otomobil"
    if vc == "motosiklet":
        return 36, 24
    if vc == "otomobil" and not commercial:
        return 36, 24
    return 12, 12


def estimate_inspection(a: dict, on: date) -> date | None:
    """Ruhsattaki tarih bilinmiyorsa tahmin: imal tarihi bilinmediğinden model yılının 1 Temmuz'u (alım tarihi aynı
    yıldaysa alım tarihi) + ilk muayene yaşı, sonra periyotla bugünden sonraki ilk tarih. Her zaman "Tahmin" etiketli."""
    if not a.get("model_year"):
        return None
    first, every = inspection_period(a.get("vehicle_class"), bool(a.get("commercial")))
    acq = _d(a.get("acquired_on"))
    base = acq if acq and acq.year == int(a["model_year"]) else date(int(a["model_year"]), 7, 1)
    d = add_months(base, first)
    while d < on:
        d = add_months(d, every)
    return d


# ---------------------------------------------------------------- görünürlük ve yetki

def _me() -> int | None:
    u = db.current_user()
    return u.get("id") if u else None


def _role() -> str | None:
    from app import book

    m = book.member()
    return book.role(m) if m else None


def can_edit(a: dict) -> bool:
    from app import book

    m = book.member()
    if not m:
        return True
    r = book.role(m)
    return r == "full" or (r == "editor" and a.get("created_by") == m["id"])


def can_pay() -> bool:
    r = _role()
    return r is None or r in ("full", "editor")


# ---------------------------------------------------------------- kural → tarih

def _rule(o: dict) -> dict:
    try:
        r = json.loads(o.get("rule") or "{}")
    except ValueError:
        r = {}
    return r if isinstance(r, dict) else {}


def occurrences(o: dict, a: dict, start: date, end: date) -> list[tuple[str, date, float | None, str | None]]:
    """[start, end] içindeki vadeler: (seq, son gün, tutar, etiket)."""
    kind, r, amt = o["kind"], _rule(o), o.get("amount")
    out = []
    if kind in FIXED:
        parts = FIXED[kind]
        per = round(amt / len(parts), 2) if amt is not None else None
        for y in range(start.year, end.year + 1):
            for i, (md, label) in enumerate(parts):
                d = date(y, int(md[:2]), int(md[3:]))
                if start <= d <= end:
                    out.append((f"{y}-{i + 1}", d, per, f"{y} {label}"))
        return out
    if kind == "aidat":
        return []
    first = _d(r.get("start"))
    if kind == "muayene" and not first:
        first = estimate_inspection(a, start)
    if not first:
        return []
    every = int(r.get("months") or 0)
    if kind == "muayene" and not r.get("months"):
        every = inspection_period(a.get("vehicle_class"), bool(a.get("commercial")))[1]
    d, k = first, 0
    while d <= end and k < 400:
        if d >= start:
            out.append((d.isoformat(), d, amt, None))
        if every <= 0:
            break
        k += 1
        d = add_months(first, every * k)
    return out


def _ensure(c, on: date) -> None:
    """Aktif yükümlülüklerin vadelerini [kuralın başladığı gün, bugün + HORIZON] aralığında garanti et (idempotent)."""
    end = add_months(on, HORIZON_MONTHS)
    assets = {r["id"]: dict(r) for r in c.execute("SELECT * FROM assets WHERE deleted_at IS NULL")}
    for o in c.execute("SELECT * FROM asset_obligations WHERE active = 1").fetchall():
        o = dict(o)
        a = assets.get(o["asset_id"])
        if not a:
            continue
        start = _d(_rule(o).get("from")) or on
        stop = min(end, _d(a.get("disposed_on")) or end)
        for seq, d, amt, label in occurrences(o, a, start, stop):
            c.execute("INSERT OR IGNORE INTO asset_dues (obligation_id, due_date, amount, seq, label, estimated) "
                      "VALUES (?, ?, ?, ?, ?, ?)", (o["id"], d.isoformat(), amt, seq, label, int(bool(o["estimated"]))))


def ensure(on: date | None = None) -> None:
    with db.connect() as c:
        _ensure(c, on or today())


def _regen(c, oid: int, on: date) -> None:
    """Kural / tutar değişti: bugünden sonraki bekleyen vadeler silinip yeniden üretilir (ödenene dokunulmaz)."""
    c.execute("DELETE FROM asset_dues WHERE obligation_id = ? AND status = 'bekliyor' AND due_date >= ?",
              (oid, on.isoformat()))
    _ensure(c, on)


# ---------------------------------------------------------------- okuma

def _status(d: dict, on: date) -> str:
    from app import workdays

    if d["status"] != "bekliyor":
        return d["status"]
    return "gecikti" if workdays.past_due(d["due_date"], on) else "bekliyor"


def _due_out(d: dict, o: dict, a: dict, on: date) -> dict:
    from app import workdays

    due = _d(d["due_date"])
    eff = workdays.next_business_day(due) if o["kind"] in FIXED else due
    st = _status(d, on)
    name = OBLIGATIONS.get(o["kind"], ("Diğer",))[0] if o["kind"] != "diger" else (o.get("label") or "Diğer")
    law = LAW.get(o["kind"], {})
    return {"id": d["id"], "obligation_id": o["id"], "asset_id": a["id"], "asset_name": a["name"],
            "asset_kind": a["kind"], "kind": o["kind"], "title": name + (f" · {d['label'].split(' ', 1)[1]}"
                                                                         if d.get("label") and " " in d["label"] else ""),
            "label": d.get("label"), "due_date": d["due_date"], "effective_date": eff.isoformat(),
            "shifted": eff != due, "days_left": (eff - on).days, "amount": d["amount"], "status": st,
            "status_label": STATUS_LABELS[st], "estimated": bool(d["estimated"]), "paid_on": d.get("paid_on"),
            "paid_amount": d.get("paid_amount"), "paid_by": d.get("paid_by"),
            "late_text": law.get("late"), "where": law.get("where"), "url": law.get("url"),
            "verified": law.get("verified", True), "push_type": OBLIGATIONS.get(o["kind"], (None, None))[1],
            "asks_end_date": o["kind"] in ("trafik", "kasko", "dask", "konut_sig"),
            "can_pay": can_pay() and not a.get("deleted_at")}


def _ob_out(o: dict, a: dict) -> dict:
    r = _rule(o)
    return {"id": o["id"], "asset_id": o["asset_id"], "kind": o["kind"],
            "name": o.get("label") if o["kind"] == "diger" and o.get("label") else OBLIGATIONS.get(o["kind"], ("Diğer",))[0],
            "label": o.get("label"), "amount": o.get("amount"), "yearly": o["kind"] in FIXED,
            "start": r.get("start"), "months": r.get("months"), "estimated": bool(o["estimated"]),
            "recurring_id": o.get("recurring_id"), "active": bool(o["active"])}


def _load(c, include_deleted: bool = False) -> tuple[dict, dict, list[dict]]:
    assets = {r["id"]: dict(r) for r in c.execute(
        "SELECT * FROM assets" + ("" if include_deleted else " WHERE deleted_at IS NULL"))}
    obs = {r["id"]: dict(r) for r in c.execute("SELECT * FROM asset_obligations")}
    dues = [dict(r) for r in c.execute("SELECT * FROM asset_dues ORDER BY due_date, id")]
    return assets, obs, dues


def dues(start: date | None = None, end: date | None = None, status: str | None = None,
         on: date | None = None) -> list[dict]:
    """Takvim: gecikmiş (bekliyor, son gün geçti) her zaman, diğerleri [start, end]; gecikenler en üstte."""
    on = on or today()
    ensure(on)
    start = start or on
    end = end or add_months(on, 12)
    with db.connect() as c:
        assets, obs, rows = _load(c)
    out = []
    for d in rows:
        o = obs.get(d["obligation_id"])
        a = o and assets.get(o["asset_id"])
        if not a or not o["active"]:
            continue
        x = _due_out(d, o, a, on)
        if x["status"] == "gecikti" or start.isoformat() <= d["due_date"] <= end.isoformat():
            if status and x["status"] != status:
                continue
            out.append(x)
    out.sort(key=lambda x: (x["status"] != "gecikti", x["effective_date"], x["id"]))
    return out


def _asset_out(a: dict, obs: list[dict], next_due: dict | None) -> dict:
    return {**{k: a.get(k) for k in ("id", *ASSET_FIELDS, "disposed_on", "created_by", "created_at")},
            "commercial": bool(a.get("commercial")), "kind_label": KINDS.get(a["kind"]),
            "vehicle_class_label": VEHICLE_CLASSES.get(a.get("vehicle_class") or ""),
            "fuel_label": FUELS.get(a.get("fuel") or ""), "tenure_label": TENURES.get(a.get("tenure") or ""),
            "obligations": [_ob_out(o, a) for o in obs], "next_due": next_due, "can_edit": can_edit(a)}


def listing(u: dict | None, include_disposed: bool = False, on: date | None = None) -> dict:
    from app import plans

    on = on or today()
    ensure(on)
    upcoming = dues(on, add_months(on, 12), on=on)
    with db.connect() as c:
        assets, obs, _ = _load(c)
    by_asset: dict[int, list] = {}
    for o in obs.values():
        by_asset.setdefault(o["asset_id"], []).append(o)
    items = []
    for a in sorted(assets.values(), key=lambda x: (x["kind"] != "arac", x["name"].lower(), x["id"])):
        if a.get("disposed_on") and not include_disposed:
            continue
        nd = next((d for d in upcoming if d["asset_id"] == a["id"] and d["status"] in ("bekliyor", "gecikti")), None)
        items.append(_asset_out(a, sorted(by_asset.get(a["id"], []), key=lambda o: o["id"]), nd))
    open_ = [d for d in upcoming if d["status"] in ("bekliyor", "gecikti")]
    allowed = plans.allows(u, "varliklar")
    return {"items": items, "upcoming": open_[:5], "locked": not allowed, "read_only": not allowed,
            "need": None if allowed else plans._needs("varliklar"),
            "can_add": allowed and _role() != "viewer",
            "disposed_count": sum(1 for a in assets.values() if a.get("disposed_on"))}


def get(aid: int, on: date | None = None) -> dict | None:
    on = on or today()
    ensure(on)
    with db.connect() as c:
        assets, obs, rows = _load(c)
        files = [dict(r) for r in c.execute("SELECT * FROM asset_files WHERE asset_id = ? ORDER BY id", (aid,))]
    a = assets.get(aid)
    if not a:
        return None
    mine = sorted((o for o in obs.values() if o["asset_id"] == aid), key=lambda o: o["id"])
    ids = {o["id"] for o in mine}
    ds = [_due_out(d, obs[d["obligation_id"]], a, on) for d in rows if d["obligation_id"] in ids
          and obs[d["obligation_id"]]["active"]]
    end = add_months(on, 12).isoformat()
    window = [d for d in ds if d["status"] == "gecikti" or on.isoformat() <= d["due_date"] <= end]
    window.sort(key=lambda x: (x["status"] != "gecikti", x["effective_date"]))
    history = sorted((d for d in ds if d["status"] in ("odendi", "atlandi")), key=lambda x: x["due_date"], reverse=True)
    nd = next((d for d in window if d["status"] in ("bekliyor", "gecikti")), None)
    out = _asset_out(a, mine, nd)
    out["dues"] = window
    out["history"] = history[:24]
    out["files"] = [{"id": f["id"], "doc_type": f["doc_type"], "doc_label": DOC_TYPES.get(f["doc_type"], "Belge"),
                     "filename": f["filename"], "size": f["size"], "pdf": f["name"].endswith(".pdf"),
                     "created_at": f["created_at"]} for f in files]
    out["needs_review"] = [o["kind"] for o in mine if o["kind"] == "muayene" and o["estimated"]]
    return out


def due(did: int, on: date | None = None) -> dict | None:
    on = on or today()
    with db.connect() as c:
        d = c.execute("SELECT * FROM asset_dues WHERE id = ?", (did,)).fetchone()
        if not d:
            return None
        o = c.execute("SELECT * FROM asset_obligations WHERE id = ?", (d["obligation_id"],)).fetchone()
        a = o and c.execute("SELECT * FROM assets WHERE id = ? AND deleted_at IS NULL", (o["asset_id"],)).fetchone()
    if not a:
        return None
    return _due_out(dict(d), dict(o), dict(a), on)


# ---------------------------------------------------------------- öneri

def suggest(kind: str, tenure: str | None = None, vehicle_class: str | None = None, commercial: bool = False,
            model_year: int | None = None, acquired_on: str | None = None, on: date | None = None) -> dict:
    on = on or today()
    key = "arac" if kind == "arac" else (tenure or "sahibi")
    on_, off = SUGGEST.get(key, ((), ()))
    a = {"vehicle_class": vehicle_class, "commercial": commercial, "model_year": model_year, "acquired_on": acquired_on}
    out = []
    for k in (*on_, *off):
        x = {"kind": k, "name": OBLIGATIONS[k][0], "checked": k in on_, "yearly": k in FIXED,
             "needs_date": OBLIGATIONS[k][2] == "anniversary", "law": LAW.get(k)}
        if k in FIXED:
            x["dates"] = [f"{md[3:]}.{md[:2]}" for md, _ in FIXED[k]]
        if k == "muayene":
            est = estimate_inspection(a, on)
            first, every = inspection_period(vehicle_class, commercial)
            x.update(estimate=est.isoformat() if est else None, every_months=every, first_months=first)
        if k == "bakim":
            x["every_months"] = 12
        if k == "mtv":
            x["hint"] = mtv_hint(on)
        out.append(x)
    return {"items": out, "vehicle_classes": VEHICLE_CLASSES, "fuels": FUELS, "tenures": TENURES, "kinds": KINDS}


MTV_RX = re.compile(r"\bMTV\b|MOTORLU\s*TA[SŞ]IT", re.I)


def mtv_hint(on: date | None = None) -> dict | None:
    """Geçen takvim yılında ekstre / kayıtlarda MTV ödemesi görünüyorsa toplamı (yıllık tutar önerisi). Yalnız öneri."""
    from app import book

    on = on or today()
    y = str(on.year - 1)
    total, n = 0.0, 0
    for t in db.card_transactions():
        if str(t.get("txn_date", ""))[:4] == y and MTV_RX.search(t.get("description") or "") and (t.get("amount") or 0) > 0:
            total += t["amount"]
            n += 1
    with db.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT id, amount, description FROM entries WHERE kind = 'gider' AND "
                                           "substr(entry_date, 1, 4) = ?", (y,))]
    for e in rows:
        if MTV_RX.search(e["description"] or "") and (not book.member() or book.can_see_entry(e["id"])):
            total += e["amount"] or 0
            n += 1
    return {"year": int(y), "amount": round(total, 2), "count": n} if n else None


# ---------------------------------------------------------------- yazma

def _clean_asset(data: dict, partial: bool = False) -> dict:
    out = {}
    for k in ASSET_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if isinstance(v, str):
            v = (" ".join(v.split()) if k != "note" else v.strip()) or None
        out[k] = v
    if "name" in out and not out["name"]:
        raise ValueError("Ad gerekli")
    if "kind" in out and out["kind"] not in KINDS:
        raise ValueError("Tür araç, konut ya da işyeri olmalı")
    if out.get("plate") is not None:
        p = normalize_plate(out["plate"])
        if p and not data.get("foreign_plate") and not plate_ok(p):
            raise ValueError("Plaka biçimi tanınmadı (ör. 06 ABC 123); yabancı plakaysa \"Diğer\" seç")
        out["plate"] = p
    if out.get("vehicle_class") and out["vehicle_class"] not in VEHICLE_CLASSES:
        raise ValueError("Geçersiz araç cinsi")
    if out.get("fuel") and out["fuel"] not in FUELS:
        raise ValueError("Geçersiz yakıt")
    if out.get("tenure") and out["tenure"] not in TENURES:
        raise ValueError("Geçersiz sahiplik")
    if out.get("model_year") is not None and not 1950 <= int(out["model_year"]) <= today().year + 1:
        raise ValueError("Model yılı geçersiz")
    if "commercial" in out:
        out["commercial"] = int(bool(out["commercial"]))
    if out.get("acquired_on"):
        out["acquired_on"] = str(out["acquired_on"])[:10]
    return out


def _clean_ob(x: dict, on: date) -> dict:
    k = x.get("kind")
    if k not in OBLIGATIONS:
        raise ValueError("Bilinmeyen hatırlatma türü")
    amt = x.get("amount")
    if amt is not None and float(amt) < 0:
        raise ValueError("Tutar eksi olamaz")
    rule: dict = {"from": (x.get("from") or on.isoformat())[:10]}
    start = x.get("start") or x.get("date")
    months = x.get("months")
    if OBLIGATIONS[k][2] == "anniversary":
        if start:
            rule["start"] = str(start)[:10]
        rule["months"] = int(months) if months is not None else (None if k == "muayene" else 12)
        if k == "diger" and months is not None and int(months) == 0:
            rule["months"] = 0  # tek seferlik
        if k != "muayene" and not start:
            raise ValueError(f"{OBLIGATIONS[k][0]} için tarih gerekli")
        if rule["months"] is not None and not 0 <= rule["months"] <= 120:
            raise ValueError("Tekrar 0–120 ay olmalı")
    est = int(k == "muayene" and not start)
    return {"kind": k, "label": (x.get("label") or "").strip()[:60] or None, "rule": json.dumps(rule, sort_keys=True),
            "amount": None if amt is None else round(float(amt), 2), "estimated": est,
            "recurring_id": x.get("recurring_id") if k == "aidat" else None, "active": int(x.get("active", True))}


def create(data: dict, obligations: list[dict], client_key: str | None = None, on: date | None = None) -> int:
    on = on or today()
    a = _clean_asset(data)
    if not a.get("name") or not a.get("kind"):
        raise ValueError("Ad ve tür gerekli")
    if a["kind"] != "arac":
        for k in ("plate", "vehicle_class", "model_year", "fuel"):
            a.pop(k, None)
        a["commercial"] = 0
    else:
        a.pop("tenure", None)
        a["vehicle_class"] = a.get("vehicle_class") or "otomobil"
    obs = [_clean_ob(x, on) for x in obligations or []]
    with db.connect() as c:
        if client_key:
            dup = c.execute("SELECT id FROM assets WHERE client_key = ?", (client_key,)).fetchone()
            if dup:
                return dup["id"]
        cols = {**a, "created_by": _me(), "client_key": client_key}
        aid = c.execute(f"INSERT INTO assets ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        tuple(cols.values())).lastrowid
        for o in obs:
            _insert_ob(c, aid, o)
        _ensure(c, on)
    return aid


def _insert_ob(c, aid: int, o: dict) -> int:
    if o.get("recurring_id") and not c.execute("SELECT 1 FROM recurring WHERE id = ?", (o["recurring_id"],)).fetchone():
        o["recurring_id"] = None
    cols = {**o, "asset_id": aid, "created_by": _me()}
    return c.execute(f"INSERT INTO asset_obligations ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     tuple(cols.values())).lastrowid


def _editable(c, aid: int) -> dict:
    a = c.execute("SELECT * FROM assets WHERE id = ? AND deleted_at IS NULL", (aid,)).fetchone()
    if not a:
        raise KeyError(aid)
    a = dict(a)
    if not can_edit(a):
        raise Forbidden("Bu varlığı ekleyen kişi ya da defter sahibi değiştirebilir")
    return a


def update(aid: int, data: dict, on: date | None = None) -> list[str]:
    """Düzenle. Araç cinsi ya da ticari değişti ve muayene ruhsattan girilmişse: needs_review ["muayene"] (tarih
    değişmiş olabilir — kullanıcıya sorulur; tahmini muayene kendiliğinden yeniden hesaplanır)."""
    on = on or today()
    d = _clean_asset(data, partial=True)
    review = []
    with db.connect() as c:
        a = _editable(c, aid)
        if not d:
            return review
        changed = any(k in d and (d[k] or None) != (a.get(k) or None) for k in ("vehicle_class", "commercial", "model_year",
                                                                                   "acquired_on"))
        d["updated_at"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        c.execute(f"UPDATE assets SET {', '.join(f'{k} = ?' for k in d)} WHERE id = ?", (*d.values(), aid))
        if changed:
            for o in c.execute("SELECT * FROM asset_obligations WHERE asset_id = ? AND kind = 'muayene'", (aid,)).fetchall():
                if o["estimated"] or not _rule(dict(o)).get("months"):
                    _regen(c, o["id"], on)
                if not o["estimated"] and any(k in d for k in ("vehicle_class", "commercial")):
                    review.append("muayene")
    return review


def add_obligation(aid: int, x: dict, on: date | None = None) -> int:
    on = on or today()
    o = _clean_ob(x, on)
    with db.connect() as c:
        _editable(c, aid)
        oid = _insert_ob(c, aid, o)
        _ensure(c, on)
    return oid


def update_obligation(aid: int, oid: int, x: dict, on: date | None = None) -> None:
    on = on or today()
    with db.connect() as c:
        _editable(c, aid)
        o = c.execute("SELECT * FROM asset_obligations WHERE id = ? AND asset_id = ?", (oid, aid)).fetchone()
        if not o:
            raise KeyError(oid)
        o = dict(o)
        r = _rule(o)
        merged = {"kind": o["kind"], "label": x.get("label", o.get("label")),
                  "amount": x["amount"] if "amount" in x else o.get("amount"),
                  "start": x["start"] if "start" in x else r.get("start"),
                  "months": x["months"] if "months" in x else r.get("months"),
                  "from": r.get("from"), "recurring_id": x.get("recurring_id", o.get("recurring_id")),
                  "active": x.get("active", bool(o["active"]))}
        n = _clean_ob(merged, on)
        c.execute("UPDATE asset_obligations SET label = ?, rule = ?, amount = ?, estimated = ?, recurring_id = ?, "
                  "active = ? WHERE id = ?", (n["label"], n["rule"], n["amount"], n["estimated"], n["recurring_id"],
                                              n["active"], oid))
        _regen(c, oid, on)


def delete_obligation(aid: int, oid: int) -> None:
    with db.connect() as c:
        _editable(c, aid)
        if not c.execute("DELETE FROM asset_obligations WHERE id = ? AND asset_id = ?", (oid, aid)).rowcount:
            raise KeyError(oid)
        c.execute("DELETE FROM asset_dues WHERE obligation_id = ?", (oid,))


def delete(aid: int) -> None:
    """Yumuşak silme: "Geri al" (restore) ile döner. Vadeler kalır ama hiçbir yerde görünmez."""
    with db.connect() as c:
        _editable(c, aid)
        c.execute("UPDATE assets SET deleted_at = ? WHERE id = ?", (datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), aid))


def restore(aid: int) -> None:
    with db.connect() as c:
        a = c.execute("SELECT * FROM assets WHERE id = ? AND deleted_at IS NOT NULL", (aid,)).fetchone()
        if not a:
            raise KeyError(aid)
        if not can_edit(dict(a)):
            raise Forbidden("Bu varlığı ekleyen kişi ya da defter sahibi geri alabilir")
        c.execute("UPDATE assets SET deleted_at = NULL WHERE id = ?", (aid,))


def dispose(aid: int, on_date: str | None, on: date | None = None) -> None:
    """Satıldı / taşındı: o günden sonraki bekleyen vadeler atlandi (skip_reason dispose; undispose geri getirir)."""
    on = on or today()
    when = (on_date or on.isoformat())[:10]
    with db.connect() as c:
        _editable(c, aid)
        c.execute("UPDATE assets SET disposed_on = ? WHERE id = ?", (when, aid))
        c.execute("UPDATE asset_dues SET status = 'atlandi', skip_reason = 'dispose' WHERE status = 'bekliyor' AND "
                  "due_date > ? AND obligation_id IN (SELECT id FROM asset_obligations WHERE asset_id = ?)", (when, aid))


def undispose(aid: int, on: date | None = None) -> None:
    on = on or today()
    with db.connect() as c:
        _editable(c, aid)
        c.execute("UPDATE assets SET disposed_on = NULL WHERE id = ?", (aid,))
        c.execute("UPDATE asset_dues SET status = 'bekliyor', skip_reason = NULL WHERE skip_reason = 'dispose' AND "
                  "obligation_id IN (SELECT id FROM asset_obligations WHERE asset_id = ?)", (aid,))
        _ensure(c, on)


def _due_ctx(c, did: int) -> tuple[dict, dict, dict]:
    d = c.execute("SELECT * FROM asset_dues WHERE id = ?", (did,)).fetchone()
    if not d:
        raise KeyError(did)
    o = dict(c.execute("SELECT * FROM asset_obligations WHERE id = ?", (d["obligation_id"],)).fetchone())
    a = c.execute("SELECT * FROM assets WHERE id = ? AND deleted_at IS NULL", (o["asset_id"],)).fetchone()
    if not a:
        raise KeyError(did)
    if not can_pay():
        raise Forbidden("Bu defterde sadece görüntüleme yetkin var")
    return dict(d), o, dict(a)


def pay(did: int, paid_on: str | None = None, amount: float | None = None, next_end_date: str | None = None,
        on: date | None = None) -> None:
    """Ödendi: tarih + tutar (varsayılan planlanan). Poliçede yeni bitiş tarihi verilirse kural oradan devam eder."""
    on = on or today()
    with db.connect() as c:
        d, o, a = _due_ctx(c, did)
        amt = d["amount"] if amount is None else round(float(amount), 2)
        if amt is not None and amt < 0:
            raise ValueError("Tutar eksi olamaz")
        c.execute("UPDATE asset_dues SET status = 'odendi', paid_on = ?, paid_amount = ?, paid_by = ?, skip_reason = NULL "
                  "WHERE id = ?", ((paid_on or on.isoformat())[:10], amt, _me(), did))
        if next_end_date and OBLIGATIONS[o["kind"]][2] == "anniversary":
            r = _rule(o)
            r["start"] = str(next_end_date)[:10]
            r["from"] = min(r.get("from") or on.isoformat(), r["start"])
            c.execute("UPDATE asset_obligations SET rule = ?, estimated = 0 WHERE id = ?", (json.dumps(r, sort_keys=True), o["id"]))
            c.execute("DELETE FROM asset_dues WHERE obligation_id = ? AND status = 'bekliyor' AND id != ? AND due_date >= ?",
                      (o["id"], did, d["due_date"]))
            _ensure(c, on)


def skip(did: int) -> None:
    with db.connect() as c:
        _due_ctx(c, did)
        c.execute("UPDATE asset_dues SET status = 'atlandi', skip_reason = 'user' WHERE id = ?", (did,))


def unpay(did: int) -> None:
    """Geri al: ödendi / atlandı → bekliyor."""
    with db.connect() as c:
        _due_ctx(c, did)
        c.execute("UPDATE asset_dues SET status = 'bekliyor', paid_on = NULL, paid_amount = NULL, paid_by = NULL, "
                  "skip_reason = NULL WHERE id = ?", (did,))


def set_due_amount(did: int, amount: float | None) -> None:
    with db.connect() as c:
        _due_ctx(c, did)
        if amount is not None and amount < 0:
            raise ValueError("Tutar eksi olamaz")
        c.execute("UPDATE asset_dues SET amount = ? WHERE id = ?", (amount, did))


# ---------------------------------------------------------------- belgeler (ruhsat, poliçe): yalnız saklanır

class TooMany(ValueError):
    pass


def can_add_file(aid: int) -> None:
    with db.connect() as c:
        _editable(c, aid)
        if c.execute("SELECT COUNT(*) FROM asset_files WHERE asset_id = ?", (aid,)).fetchone()[0] >= MAX_FILES:
            raise TooMany(f"Bir varlığa en çok {MAX_FILES} belge eklenebilir; önce birini kaldır")


def link_file(aid: int, name: str, doc_type: str, filename: str | None, size: int | None) -> int:
    with db.connect() as c:
        _editable(c, aid)
        dup = c.execute("SELECT id FROM asset_files WHERE asset_id = ? AND name = ?", (aid, name)).fetchone()
        if dup:
            return dup["id"]
        return c.execute("INSERT INTO asset_files (asset_id, kind, name, doc_type, filename, size) VALUES (?, 'assets', ?, ?, ?, ?)",
                         (aid, name, doc_type if doc_type in DOC_TYPES else "diger", (filename or "")[:120] or None,
                          size)).lastrowid


def file_row(aid: int, fid: int) -> dict | None:
    with db.connect() as c:
        a = c.execute("SELECT * FROM assets WHERE id = ? AND deleted_at IS NULL", (aid,)).fetchone()
        r = a and c.execute("SELECT * FROM asset_files WHERE id = ? AND asset_id = ?", (fid, aid)).fetchone()
    return {**dict(r), "asset": dict(a)} if r else None


def unlink_file(aid: int, fid: int) -> None:
    with db.connect() as c:
        _editable(c, aid)
        r = c.execute("SELECT name FROM asset_files WHERE id = ? AND asset_id = ?", (fid, aid)).fetchone()
        if not r:
            raise KeyError(fid)
        c.execute("DELETE FROM asset_files WHERE id = ?", (fid,))
        used = c.execute("SELECT 1 FROM asset_files WHERE name = ?", (r["name"],)).fetchone()
    if not used:
        db.remove_file("assets", r["name"])


# ---------------------------------------------------------------- Planlama, harcanabilir, push, Yapılacaklar

def plan_dues(on: date) -> list[dict]:
    """Planlama / harcanabilir: tutarı girilmiş bekleyen vadeler (ham satırlar; okuma yazma yapmaz)."""
    try:
        with db.connect() as c:
            rows = c.execute(
                "SELECT d.id, d.due_date, d.amount, d.label, o.kind, o.label AS olabel, a.name, a.id AS asset_id "
                "FROM asset_dues d JOIN asset_obligations o ON o.id = d.obligation_id JOIN assets a ON a.id = o.asset_id "
                "WHERE d.status = 'bekliyor' AND o.active = 1 AND a.deleted_at IS NULL ORDER BY d.due_date, d.id").fetchall()
    except Exception:  # noqa: BLE001 - tablo yok (çok eski şema): Planlama bozulmasın
        return []
    out = []
    for r in rows:
        name = r["olabel"] if r["kind"] == "diger" and r["olabel"] else OBLIGATIONS.get(r["kind"], ("Diğer",))[0]
        out.append({"id": r["id"], "due_date": r["due_date"], "amount": r["amount"], "asset_id": r["asset_id"],
                    "title": f"{name} · {r['name']}", "kind": r["kind"]})
    return out


PUSH_STAGES = {  # push türü → kalan gün eşikleri (her eşik vade başına bir kez)
    "varlik_vergi": (14, 3, 0), "varlik_sigorta": (30, 7, 0), "varlik_muayene": (30, 14, 3, 0),
    "varlik_bakim": (14,),
}
LATE_PUSHES = 3  # vergi gecikince: ertesi gün ve sonra ayda bir, en çok bu kadar


def push_stage(push_type: str, kind: str, days_left: int) -> str | None:
    if days_left < 0:
        if push_type != "varlik_vergi":
            return None
        n = (-days_left - 1) // 30
        return f"g{n}" if n < LATE_PUSHES else None
    stages = (30,) if kind == "kira_artis" else PUSH_STAGES.get(push_type, ())
    hit = [s for s in stages if days_left <= s]
    return f"{min(hit)}g" if hit else None


def export_rows() -> list[list]:
    out = []
    for d in dues(date(2000, 1, 1), add_months(today(), 24)):
        out.append([d["asset_name"], d["title"], d["due_date"], d["amount"], d["status_label"], d.get("paid_on"),
                    d.get("paid_amount"), "Evet" if d["estimated"] else None])
    return out


EXPORT_HEAD = ["Varlık", "Yükümlülük", "Son gün", "Tutar (₺)", "Durum", "Ödeme tarihi", "Ödenen (₺)", "Tahmin"]
