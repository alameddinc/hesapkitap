"""Varlıklarım Aşama 2 — araç masrafları: ekstre / döküm / fiş harcamasını araca bağlama ve masraf özeti.

docs: hesapkitap-mobile docs/varliklar-ozellik.md §10 (eşleme kuralları).

Aday harcama: kart işlemi (transactions, txn_type harcama) ya da gider kaydı (entries; ekstredeki karşılığına bağlı kayıt
sayılmaz: tutar iki kez düşmesin). Açıklama / kategori kalıbından masraf türü (cost_kind): yakit, yol, otopark, servis.
Sigorta / vergi işlemi kalıptan BAĞLANMAZ: onlar vadelerin "ödendi"siyle sayılır (çift sayım olmasın).

Atama sırası (asset_links.source):
1. user / bulk — kullanıcının elle bağı ya da "Hepsini ekle" (her zaman kazanır; asset_id NULL = araçla ilgisi yok)
2. plate — fişte okunan plaka kayıtlı bir aracın plakası. Kayıtlı değilse BAĞLANMAZ: "Tanımadığımız plakalar"
3. rule — öğrenilen kural: işyeri anahtarı + kart (kart fark etmeksizin tek araca gidiyorsa o da)
4. auto — harcama gününde aktif tek araç varsa, aracın costs_since gününden sonrası (araç eklenince geçmiş sorulur)
5. aksi halde soru: "Bu hangi araç?" (işyeri + kart başına tek kart; yanıt "Hatırla" ile kurala döner)
Kendiliğinden bağlanmayıp soruya düşenler: elektrikli araçta akaryakıt (ya da tersi), akaryakıt marketinde küçük tutar,
havalimanı otoparkı.

Bağlar okuma anında (sync) yazılır; yalnız paketi açık (Premium / Aile) defterde. Ortak defter üyesi yalnız görebildiği
işlemleri görür, bağlar ve masrafı bunlardan hesaplanır (db.card_transactions / list_entries zaten süzülü).
Km yok (Aşama 3): yalnız tutar, "ayda ₺X yakıt", aylık ortalama.
"""
import json
import re
from datetime import date, timedelta

from app import db

WINDOW_MONTHS = 12
MAX_BACK_MONTHS = 24
SMALL_MARKET = 150.0

COST_KINDS = {"yakit": "Yakıt", "yol": "Otoyol ve köprü", "otopark": "Otopark", "servis": "Bakım ve servis",
              "vade": "Sigorta, vergi, muayene", "diger": "Diğer"}
GROUPS = dict(COST_KINDS)
# vadesi ödenen yükümlülük → masraf grubu
DUE_GROUP = {"mtv": "vade", "trafik": "vade", "kasko": "vade", "muayene": "vade", "bakim": "servis", "diger": "diger"}

_EV = r"ESARJ|E-SARJ|\bZES\b|TRUGO|VOLTRUN|SHARZ|\bSARJ\b"
PATTERNS = [
    ("yakit", re.compile(r"\bOPET\b|\bSHELL\b|\bBP\b|PETROL OFIS|\bPOAS\b|^PO\b|\bTOTAL(ENERGIES)?\b|\bAYTEMIZ\b|"
                         r"TURKIYE PETROL|\bTP\b.*PETROL|\bLUKOIL\b|\bALPET\b|\bMOIL\b|\bKADOIL\b|\bSUNPET\b|"
                         r"AKARYAKIT|\bBENZIN|\bMOTORIN\b|\bPETROL(CULUK|ULUK)?\b|PETROL URUN|\bPET SAN\b|" + _EV)),
    ("vade", re.compile(r"TUVTURK|ARAC MUAYENE|MUAYENE ISTASYON")),
    ("yol", re.compile(r"OGSTORE|PAYTR.*\bOGS\b|OTOYOL|\bHGS\b|\bOGS\b|\bKGM\b|KOPRU|AVRASYA TUNEL|OSMANGAZI|YAVUZ SULTAN|KARAYOLLARI")),
    ("otopark", re.compile(r"ISPARK|OTOPARK|\bPARKING\b|\bVALE\b")),
    ("servis", re.compile(r"OTO ?SERVIS|YETKILI SERVIS|LASTIK|OTO ?YIKAMA|ARAC YIKAMA|OTO ?BAKIM|YAG DEGISIM|"
                          r"OTO ?EKSPERTIZ|OTO ?YEDEK|OTO ?KUAFOR|CAR SERVICE|\bBOSCH\b|\bAKU\b")),
]
NOT_VEHICLE = re.compile(r"\bUBER\b|BITAKSI|\bMARTI\b|\bTAKSI\b|YEMEK|MARKET (?!.*AKARYAKIT)")
EV_RX = re.compile(_EV)
# banka ücreti / vergisi işyeri adını taşısa da ("Vade Farki - ISPARK…", "BSMV - …") araç masrafı değildir
BANK_FEE = re.compile(r"^(VADE FARKI|BSMV|KKDF|FAIZ|GECIKME)\b|\bFAIZ\b|GECIKME FAIZ")
BANK_CATS = re.compile(r"FAIZ|BANKA UCRET|BANKA FAIZ")
VEHICLE_CAT = re.compile(r"ULASIM ?& ?ARAC|\bARAC\b")
# HGS / OGS satırında plaka ("HGS /<no>/06ORN123"): harf-rakam bitişiğindeki rakam dizisi plaka sayılmaz
TOLL_PLATE = re.compile(r"(?<![A-Z0-9])((?:0[1-9]|[1-7]\d|8[01]) ?[A-Z]{1,3} ?\d{2,4})(?![A-Z0-9])")
AIRPORT = re.compile(r"HAVALIMANI|HAVA LIMANI|AIRPORT|\bIGA\b|\bSAW\b")
CAT_KIND = [(re.compile(r"YAKIT|AKARYAKIT|BENZIN"), "yakit"), (re.compile(r"OTOPARK"), "otopark"),
            (re.compile(r"OTOYOL|KOPRU|HGS"), "yol"), (re.compile(r"ARAC BAKIM|OTO BAKIM|SERVIS"), "servis")]
PLATE_IN_TEXT = re.compile(r"PLAKA\s*(?:NO)?\s*[:.]?\s*((?:0[1-9]|[1-7]\d|8[01])\s?[A-Z]{1,3}\s?\d{2,4})\b")
IGNORED_PLATES = "assets:ignored_plates"


class Forbidden(PermissionError):
    pass


def today() -> date:
    from app import assets

    return assets.today()


def _norm(s: str | None) -> str:
    from app.textnorm import normalize

    return normalize(s or "")


def classify(description: str, category: str | None = None, txn_type: str | None = None) -> str | None:
    """Açıklama / kategoriden araç masraf türü; araçla ilgili değilse None. UBER, taksi, market (yakıt markası
    adıyla da olsa yemek/market), banka faizi / ücreti / vergisi (Vade farkı, BSMV, KKDF) araç masrafı değildir.
    TÜVTÜRK "vade" (muayene vadesiyle eşleşir). Kategorisi "Ulaşım & Araç" olup kalıba uymayan satır "diger": hiçbir
    zaman kendiliğinden bağlanmaz, "Bu araç masrafı mı?" diye sorulur."""
    d = _norm(description)
    c = _norm(category)
    if txn_type == "faiz_ucret" or BANK_FEE.search(d) or BANK_CATS.search(c):
        return None
    from app import asset_pay

    pk = asset_pay.payment_kind(d)
    if pk and pk["group"] != "muayene":
        return None  # sigorta / vergi ödemesi: vadenin "ödendi"siyle sayılır (taksitleri de; çift sayım olmasın)
    for kind, rx in PATTERNS:
        if rx.search(d):
            if kind != "yakit" and NOT_VEHICLE.search(d):
                return None
            return kind
    if NOT_VEHICLE.search(d):
        return None
    for rx, kind in CAT_KIND:
        if rx.search(c):
            return kind
    return "diger" if VEHICLE_CAT.search(c) else None


def toll_plate(description: str) -> str | None:
    """HGS / OGS ekstre satırındaki plaka (normalize); yoksa None."""
    d = _norm(description)
    if not re.search(r"\bHGS\b|\bOGS\b", d):
        return None
    for m in TOLL_PLATE.finditer(d):
        if p := normalize_plate(m.group(1)):
            return p
    return None


def normalize_plate(p: str | None) -> str | None:
    """Fişteki / yazılan plaka → "06ORN123"; TR plaka biçimine uymuyorsa None (yabancı plaka fişten okunmaz)."""
    from app import assets

    if not p:
        return None
    n = assets.normalize_plate(_norm(p))
    return n if n and assets.plate_ok(n) else None


def plate_in_text(text: str | None) -> str | None:
    """Metinde "PLAKA: 06 ORN 123" etiketi varsa plaka (etiket şart: rastgele harf+rakam dizisi plaka sayılmaz)."""
    m = PLATE_IN_TEXT.search(_norm(text))
    return normalize_plate(m.group(1)) if m else None


def _months_back(on: date, n: int) -> date:
    from app import assets

    return assets.add_months(on, -n)


# ---------------------------------------------------------------- adaylar

def _card_of(account: str | None) -> str:
    digits = re.sub(r"\D", "", account or "")
    return digits[-4:] if len(digits) >= 4 else ""


def _entry_plate(e: dict, items: dict[int, list[str]]) -> str | None:
    if e.get("plate"):
        return normalize_plate(e["plate"])
    if not e.get("receipt_file"):
        return None  # eski fiş: yeniden okunmaz (okuma hakkı / maliyet); yalnız eldeki metin
    return plate_in_text(" \n".join([e.get("note") or "", *items.get(e["id"], [])]))


def candidates(since: date, until: date | None = None) -> list[dict]:
    """[since, until] arasındaki araç masrafı adayları (görünür olanlar): {ref, txn_id|entry_id, date, description,
    amount, cost_kind, merchant_key, card, plate}."""
    from app.textnorm import merchant_name

    until = until or today()
    lo, hi = since.isoformat(), until.isoformat()
    out = []
    for t in db.card_transactions():
        if t.get("txn_type") != "harcama" or (t.get("amount") or 0) <= 0 or not (lo <= t["txn_date"][:10] <= hi):
            continue
        k = classify(t["description"], t.get("category"), t.get("txn_type"))
        if k:
            out.append({"ref": f"t{t['id']}", "txn_id": t["id"], "entry_id": None, "date": t["txn_date"][:10],
                        "description": t["description"], "amount": round(float(t["amount"]), 2), "cost_kind": k,
                        "merchant_key": merchant_name(t["description"]), "card": _card_of(t.get("account")),
                        "plate": toll_plate(t["description"]) if k == "yol" else None, "source_label": "Ekstre"})
    ents = [e for e in db.list_entries() if e.get("kind") == "gider" and not e.get("skipped")
            and not e.get("linked_txn_id") and lo <= (e.get("entry_date") or "")[:10] <= hi]
    rec = [e["id"] for e in ents if e.get("receipt_file") and not e.get("plate")]
    items: dict[int, list[str]] = {}
    if rec:
        with db.connect() as c:
            for r in c.execute(f"SELECT entry_id, name FROM receipt_items WHERE entry_id IN ({','.join('?' * len(rec))})", rec):
                items.setdefault(r[0], []).append(r[1] or "")
    for e in ents:
        plate = _entry_plate(e, items)
        k = classify(e["description"], e.get("category")) or ("yakit" if plate else None)
        if k == "yol" and not plate:
            plate = toll_plate(e["description"])
        if k:
            out.append({"ref": f"e{e['id']}", "txn_id": None, "entry_id": e["id"], "date": e["entry_date"][:10],
                        "description": e["description"], "amount": round(float(e["amount"]), 2), "cost_kind": k,
                        "merchant_key": merchant_name(e["description"]),
                        "card": (e.get("card_last4") or "") if e.get("payment") == "kredi_karti" else "",
                        "plate": plate, "source_label": "Fiş" if e.get("receipt_file") else "Kayıt"})
    out.sort(key=lambda x: (x["date"], x["ref"]), reverse=True)
    return out


# ---------------------------------------------------------------- araçlar ve bağlar

def vehicles(c) -> list[dict]:
    return [dict(r) for r in c.execute("SELECT * FROM assets WHERE kind = 'arac' AND deleted_at IS NULL ORDER BY id")]


def _active_on(a: dict, d: str) -> bool:
    return (not a.get("acquired_on") or a["acquired_on"] <= d) and (not a.get("disposed_on") or d < a["disposed_on"])


def _auto_from(a: dict) -> str:
    """Aracın kendiliğinden bağlanan harcamaları bu günden (costs_since; yoksa eklendiği gün)."""
    return a.get("costs_since") or (a.get("created_at") or today().isoformat())[:10]


def _ask_from(a: dict) -> str:
    return min(_auto_from(a), a.get("review_since") or "9999")


def _links(c) -> dict[str, dict]:
    out = {}
    for r in c.execute("SELECT * FROM asset_links"):
        r = dict(r)
        out[f"t{r['txn_id']}" if r["txn_id"] is not None else f"e{r['entry_id']}"] = r
    return out


def _rules(c) -> list[dict]:
    return [dict(r) for r in c.execute("SELECT * FROM asset_rules")]


def _ignored(c) -> set[str]:
    r = c.execute("SELECT value FROM settings WHERE key = ?", (IGNORED_PLATES,)).fetchone()
    try:
        return set(json.loads(r[0])) if r else set()
    except ValueError:
        return set()


def resolve(x: dict, cars: list[dict], rules: list[dict]) -> tuple[str, int | None] | None:
    """Kullanıcı bağı yoksa adayın aracı: (source, asset_id) ya da None (soru / bağlanmaz). Plakası kayıtlı olmayan
    fiş ("plate_unknown") hiç bağlanmaz."""
    d = x["date"]
    by_id = {a["id"]: a for a in cars}
    if x.get("plate"):
        own = [a for a in cars if a.get("plate") == x["plate"]]
        if own:
            return ("plate", own[0]["id"]) if _active_on(own[0], d) else None
        return ("plate_unknown", None)
    hit = next((r for r in rules if r["merchant_key"] == x["merchant_key"] and r["card_last4"] == x["card"]), None)
    if hit:
        if hit["asset_id"] is None:
            return ("rule", None)
        a = by_id.get(hit["asset_id"])
        if a and _active_on(a, d):
            return ("rule", a["id"])
    same = {r["asset_id"] for r in rules if r["merchant_key"] == x["merchant_key"]}
    if len(same) == 1:
        aid = next(iter(same))
        a = by_id.get(aid) if aid is not None else None
        if aid is None:
            return ("rule", None)
        if a and _active_on(a, d):
            return ("rule", aid)
    active = [a for a in cars if _active_on(a, d)]
    if len(active) != 1:
        return None
    a = active[0]
    if d < _auto_from(a) or doubtful(x, a):
        return None
    return ("auto", a["id"])


def doubtful(x: dict, a: dict) -> bool:
    """Tek araçta da sorulur: yakıt türü uyuşmazlığı, akaryakıt marketinde küçük tutar, havalimanı otoparkı."""
    d = _norm(x["description"])
    if x["cost_kind"] == "diger":
        return True  # yalnız kategoriden: "Bu araç masrafı mı?"
    if x["cost_kind"] == "yakit":
        ev = bool(EV_RX.search(d))
        fuel = a.get("fuel") or ""
        if (ev and fuel not in ("elektrik", "hibrit", "")) or (not ev and fuel == "elektrik"):
            return True
        if "MARKET" in d and x["amount"] < SMALL_MARKET:
            return True
    return x["cost_kind"] == "otopark" and bool(AIRPORT.search(d))


def allowed() -> bool:
    from app import plans

    return plans.allows(db.current_user(), "varliklar")


def sync(on: date | None = None) -> list[dict]:
    """Son WINDOW_MONTHS ayın adaylarını kurallarla bağla (idempotent); bağlanmamış adayları döndür (sorular ve
    tanınmayan plakalar için). Paket kapalıysa yazmaz."""
    on = on or today()
    cands = candidates(_months_back(on, MAX_BACK_MONTHS), on)
    with db.connect() as c:
        cars = vehicles(c)
        if not cars:
            return []
        links, rules = _links(c), _rules(c)
        write = allowed()
        left = []
        for x in cands:
            if x["ref"] in links:
                continue
            r = resolve(x, cars, rules)
            if r and r[0] != "plate_unknown" and write:
                c.execute("INSERT OR IGNORE INTO asset_links (asset_id, txn_id, entry_id, cost_kind, source) "
                          "VALUES (?, ?, ?, ?, ?)", (r[1], x["txn_id"], x["entry_id"], x["cost_kind"], r[0]))
                continue
            left.append({**x, "unknown_plate": bool(r and r[0] == "plate_unknown")})
    return left


# ---------------------------------------------------------------- sorular

def _car_out(a: dict) -> dict:
    return {"id": a["id"], "name": a["name"], "fuel": a.get("fuel")}


def can_link() -> bool:
    from app import assets

    return assets.can_pay()  # sahip, full, editor (viewer bağlayamaz)


def questions(on: date | None = None) -> dict:
    """ "Bu hangi araç?" kartları: işyeri + kart başına bir kart (bağlanmamış harcamalar). Aracın soru penceresinden
    (eklendiği gün ya da "Tek tek bakayım" tarihi) önceki harcama sorulmaz; o geçmiş için toplu ekleme var."""
    on = on or today()
    if not allowed():
        return {"items": [], "vehicles": [], "can_answer": False, "locked": True}
    left = sync(on)
    with db.connect() as c:
        cars = vehicles(c)
    act = [a for a in cars if not a.get("disposed_on")]
    groups: dict[tuple, dict] = {}
    for x in left:
        if x["unknown_plate"]:
            continue
        here = [a for a in cars if _active_on(a, x["date"])]
        if not here or x["date"] < min(_ask_from(a) for a in here):
            continue
        g = groups.setdefault((x["merchant_key"], x["card"]), {
            "key": f"{x['merchant_key']}|{x['card']}", "merchant": x["merchant_key"] or x["description"][:40],
            "card_last4": x["card"] or None, "cost_kind": x["cost_kind"], "cost_label": COST_KINDS[x["cost_kind"]],
            "count": 0, "total": 0.0, "txn_ids": [], "entry_ids": [], "items": [], "options": [_car_out(a) for a in here]})
        g["count"] += 1
        g["total"] = round(g["total"] + x["amount"], 2)
        (g["txn_ids"] if x["txn_id"] else g["entry_ids"]).append(x["txn_id"] or x["entry_id"])
        if len(g["items"]) < 20:
            g["items"].append(_item_out(x))
    items = sorted(groups.values(), key=lambda g: (-g["count"], g["merchant"]))
    return {"items": items, "vehicles": [_car_out(a) for a in act], "can_answer": can_link(), "locked": False,
            "count": sum(g["count"] for g in items)}


def _item_out(x: dict, link: dict | None = None) -> dict:
    return {"txn_id": x["txn_id"], "entry_id": x["entry_id"], "date": x["date"], "description": x["description"],
            "amount": x["amount"], "cost_kind": x["cost_kind"], "card_last4": x["card"] or None, "plate": x.get("plate"),
            "source_label": x.get("source_label"), "link_id": link["id"] if link else None,
            "link_source": link["source"] if link else None}


def unknown_plates(on: date | None = None) -> list[dict]:
    """Fişte okunup hiçbir araca ait olmayan plakalar ("Bu plaka senin mi?"); yoksayılanlar hariç."""
    on = on or today()
    if not allowed():
        return []
    left = [x for x in sync(on) if x["unknown_plate"]]
    with db.connect() as c:
        ign = _ignored(c)
    out: dict[str, dict] = {}
    for x in left:
        if x["plate"] in ign:
            continue
        p = out.setdefault(x["plate"], {"plate": x["plate"], "count": 0, "total": 0.0, "last_date": x["date"]})
        p["count"] += 1
        p["total"] = round(p["total"] + x["amount"], 2)
        p["last_date"] = max(p["last_date"], x["date"])
    return sorted(out.values(), key=lambda p: (-p["count"], p["plate"]))


def ignore_plate(plate: str) -> None:
    if not can_link():
        raise Forbidden("Bu defterde bağlama yetkin yok")
    p = normalize_plate(plate)
    if not p:
        raise ValueError("Plaka biçimi tanınmadı")
    with db.connect() as c:
        ign = _ignored(c) | {p}
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (IGNORED_PLATES, json.dumps(sorted(ign))))


# ---------------------------------------------------------------- bağlama (elle)

def _visible_refs(txn_ids: list[int], entry_ids: list[int]) -> None:
    from app import book

    for t in txn_ids:
        if not book.visible("txn", t):
            raise KeyError(t)
    for e in entry_ids:
        if not book.visible("entry", e):
            raise KeyError(e)


def link(txn_ids: list[int], entry_ids: list[int], asset_id: int | None, cost_kind: str | None = None,
         remember: bool = True, clear: bool = False, on: date | None = None) -> dict:
    """Elle bağ: harcamalar asset_id'ye (None: araçla ilgisi yok). remember: işyeri + kart kuralı öğrenilir; aynı kalıp
    başka araca verilmiş kuralı silinir (karışık kullanım: sonraki harcama yine sorulur). clear: kullanıcı bağı
    kaldırılır (yeniden kurallarla; Geri al). Yanıt previous: geri almak için önceki bağlar."""
    from app import assets

    if not can_link():
        raise Forbidden("Bu defterde harcama bağlama yetkin yok")
    if not txn_ids and not entry_ids:
        raise ValueError("Harcama seçilmedi")
    if cost_kind is not None and cost_kind not in COST_KINDS:
        raise ValueError("Masraf türü tanınmadı")
    _visible_refs(txn_ids, entry_ids)
    on = on or today()
    me = assets._me()
    by_ref = {x["ref"]: x for x in candidates(_months_back(on, 60), on)}
    with db.connect() as c:
        if asset_id is not None:
            a = c.execute("SELECT * FROM assets WHERE id = ? AND kind = 'arac' AND deleted_at IS NULL", (asset_id,)).fetchone()
            if not a:
                raise KeyError(asset_id)
        links = _links(c)
        refs = [f"t{t}" for t in txn_ids] + [f"e{e}" for e in entry_ids]
        previous = [{"txn_id": (links[r]["txn_id"]), "entry_id": links[r]["entry_id"], "asset_id": links[r]["asset_id"],
                     "cost_kind": links[r]["cost_kind"], "source": links[r]["source"]} if r in links
                    else {"txn_id": int(r[1:]) if r[0] == "t" else None, "entry_id": int(r[1:]) if r[0] == "e" else None,
                          "asset_id": None, "source": None} for r in refs]
        learned = False
        for r in refs:
            col, oid = ("txn_id", int(r[1:])) if r[0] == "t" else ("entry_id", int(r[1:]))
            c.execute(f"DELETE FROM asset_links WHERE {col} = ?", (oid,))
            if clear:
                continue
            kind = cost_kind or (by_ref.get(r) or {}).get("cost_kind") or (links.get(r) or {}).get("cost_kind") or "diger"
            c.execute(f"INSERT INTO asset_links (asset_id, {col}, cost_kind, source, created_by) VALUES (?, ?, ?, 'user', ?)",
                      (asset_id, oid, kind, me))
        if remember and not clear:
            keys = {(by_ref[r]["merchant_key"], by_ref[r]["card"]) for r in refs if r in by_ref and by_ref[r]["merchant_key"]
                    and not by_ref[r].get("plate")}
            for mk, card in keys:
                old = c.execute("SELECT * FROM asset_rules WHERE merchant_key = ? AND card_last4 = ?", (mk, card)).fetchone()
                if old and old["asset_id"] != asset_id:
                    c.execute("DELETE FROM asset_rules WHERE id = ?", (old["id"],))
                elif old:
                    c.execute("UPDATE asset_rules SET hits = hits + 1 WHERE id = ?", (old["id"],))
                    learned = True
                else:
                    c.execute("INSERT INTO asset_rules (asset_id, merchant_key, card_last4) VALUES (?, ?, ?)",
                              (asset_id, mk, card))
                    learned = True
    return {"ok": True, "count": len(refs), "learned": learned, "previous": previous}


def unlink(link_id: int) -> None:
    """Bağı kaldır: harcama "araçla ilgisi yok" olur (kendiliğinden yeniden bağlanmasın)."""
    if not can_link():
        raise Forbidden("Bu defterde harcama bağlama yetkin yok")
    with db.connect() as c:
        r = c.execute("SELECT * FROM asset_links WHERE id = ?", (link_id,)).fetchone()
    if not r:
        raise KeyError(link_id)
    link([r["txn_id"]] if r["txn_id"] is not None else [], [r["entry_id"]] if r["entry_id"] is not None else [],
         None, r["cost_kind"], remember=False)


def link_info(txn_id: int | None = None, entry_id: int | None = None, on: date | None = None) -> dict:
    """İşlem detayındaki seçici: bu harcamanın bağı, tahmini masraf türü, seçilebilir araçlar."""
    from app import book

    on = on or today()
    if (txn_id is None) == (entry_id is None):
        raise ValueError("txn_id ya da entry_id")
    if not book.visible("txn" if txn_id else "entry", txn_id or entry_id):
        raise KeyError(txn_id or entry_id)
    if allowed():
        sync(on)
    ref = f"t{txn_id}" if txn_id else f"e{entry_id}"
    x = next((x for x in candidates(_months_back(on, 60), on) if x["ref"] == ref), None)
    with db.connect() as c:
        cars = vehicles(c)
        lk = _links(c).get(ref)
    a = next((v for v in cars if lk and v["id"] == lk["asset_id"]), None)
    return {"available": bool(cars) and allowed(), "candidate": bool(x), "cost_kind": (lk or x or {}).get("cost_kind"),
            "link": {"id": lk["id"], "asset_id": lk["asset_id"], "asset_name": a["name"] if a else None,
                     "source": lk["source"], "cost_kind": lk["cost_kind"]} if lk else None,
            "plate": (x or {}).get("plate"), "vehicles": [_car_out(v) for v in cars if not v.get("disposed_on")],
            "can_edit": can_link() and allowed(), "cost_kinds": COST_KINDS}


# ---------------------------------------------------------------- geriye dönük (araç eklenince)

def _backfill_split(aid: int, since: date, on: date) -> tuple[list[dict], dict]:
    left = sync(on)
    lo = since.isoformat()
    with db.connect() as c:
        cars = vehicles(c)
    a = next((v for v in cars if v["id"] == aid), None)
    if not a:
        raise KeyError(aid)
    take, other, unknown = [], 0, 0
    for x in left:
        if x["date"] < lo or not _active_on({**a, "acquired_on": None}, x["date"]):
            continue
        if x["unknown_plate"]:
            unknown += 1
        elif x.get("plate") and x["plate"] != a.get("plate"):
            other += 1
        else:
            take.append(x)
    return take, {"other_plate": other, "unknown_plate": unknown}


def _since(aid: int, since: str | None, on: date) -> date:
    with db.connect() as c:
        a = c.execute("SELECT * FROM assets WHERE id = ? AND deleted_at IS NULL", (aid,)).fetchone()
    if not a or a["kind"] != "arac":
        raise KeyError(aid)
    d = date.fromisoformat(since) if since else (date.fromisoformat(a["acquired_on"]) if a["acquired_on"]
                                                 else _months_back(on, WINDOW_MONTHS))
    return max(d, _months_back(on, MAX_BACK_MONTHS))


def backfill_preview(aid: int, since: str | None = None, on: date | None = None) -> dict:
    """ "<tarih>'ten bugüne N akaryakıt / otoyol harcaması bulundu": bağlanmamış adaylar. Plakası başka araca ait
    olanlar ve tanınmayan plakalı fişler bu toplu eklemeye girmez (ayrı sayılır)."""
    on = on or today()
    if not allowed():
        return {"locked": True, "count": 0, "total": 0, "by_kind": [], "excluded": {}, "since": None}
    d = _since(aid, since, on)
    take, excluded = _backfill_split(aid, d, on)
    by: dict[str, list] = {}
    for x in take:
        by.setdefault(x["cost_kind"], []).append(x)
    return {"locked": False, "since": d.isoformat(), "default_since": _since(aid, None, on).isoformat(),
            "min_since": _months_back(on, MAX_BACK_MONTHS).isoformat(), "count": len(take),
            "total": round(sum(x["amount"] for x in take), 2),
            "by_kind": [{"kind": k, "label": COST_KINDS[k], "count": len(v), "total": round(sum(x["amount"] for x in v), 2)}
                        for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))],
            "excluded": excluded, "items": [_item_out(x) for x in take[:50]]}


def backfill(aid: int, mode: str, since: str | None = None, on: date | None = None) -> dict:
    """mode all: bulunanların hepsi bu araca (source bulk), bundan sonra da kendiliğinden; review: geçmiş "Bu hangi
    araç?" sorusuna düşer; skip: yalnız bugünden sonrası (varsayılan)."""
    from app import assets

    if not can_link():
        raise Forbidden("Bu defterde harcama bağlama yetkin yok")
    on = on or today()
    d = _since(aid, since, on)
    with db.connect() as c:
        a = c.execute("SELECT * FROM assets WHERE id = ?", (aid,)).fetchone()
    if not assets.can_edit(dict(a)) and mode != "skip":
        raise Forbidden("Bu aracı yalnız ekleyen ya da tam yetkili üye düzenleyebilir")
    n = 0
    if mode == "all":
        take, _ = _backfill_split(aid, d, on)
        with db.connect() as c:
            for x in take:
                col = "txn_id" if x["txn_id"] else "entry_id"
                c.execute(f"INSERT OR IGNORE INTO asset_links (asset_id, {col}, cost_kind, source, created_by) "
                          "VALUES (?, ?, ?, 'bulk', ?)", (aid, x["txn_id"] or x["entry_id"], x["cost_kind"], assets._me()))
                n += 1
            c.execute("UPDATE assets SET costs_since = ? WHERE id = ?", (d.isoformat(), aid))
    elif mode == "review":
        with db.connect() as c:
            c.execute("UPDATE assets SET review_since = ? WHERE id = ?", (d.isoformat(), aid))
    elif mode == "skip":
        with db.connect() as c:
            c.execute("UPDATE assets SET costs_since = COALESCE(costs_since, ?) WHERE id = ?", (on.isoformat(), aid))
    else:
        raise ValueError("mode: all | review | skip")
    return {"ok": True, "linked": n, "since": d.isoformat(), "mode": mode}


# ---------------------------------------------------------------- masraf özeti

def _month_keys(on: date, n: int = WINDOW_MONTHS) -> list[str]:
    out = []
    for i in range(n - 1, -1, -1):
        d = _months_back(on.replace(day=1), i)
        out.append(f"{d.year:04d}-{d.month:02d}")
    return out


def rows(aid: int, on: date | None = None) -> list[dict]:
    """Aracın son 12 ayki masraf satırları (görünür bağlı harcamalar + ödenen vadeler; vadeyle aynı tutarda bağlı
    harcama ±30 günde varsa vade ayrıca sayılmaz)."""
    on = on or today()
    start = _months_back(on.replace(day=1), WINDOW_MONTHS - 1)
    if allowed():
        sync(on)
    cands = {x["ref"]: x for x in candidates(start, on)}
    with db.connect() as c:
        links = [lk for lk in _links(c).values() if lk["asset_id"] == aid]
        dues = [dict(r) for r in c.execute(
            "SELECT d.*, o.kind AS ob_kind, o.label AS ob_label FROM asset_dues d JOIN asset_obligations o "
            "ON o.id = d.obligation_id WHERE o.asset_id = ? AND d.status = 'odendi' AND d.paid_on >= ? AND d.paid_on <= ?",
            (aid, start.isoformat(), on.isoformat()))]
    out = []
    for lk in links:
        x = cands.get(f"t{lk['txn_id']}" if lk["txn_id"] is not None else f"e{lk['entry_id']}")
        if x:
            out.append({**_item_out(x, lk), "group": lk["cost_kind"], "cost_kind": lk["cost_kind"]})
    from app import asset_pay, assets

    for d in dues:
        amt = d["paid_amount"] if d["paid_amount"] is not None else d["amount"]
        if not amt:
            continue
        pd = date.fromisoformat(d["paid_on"])
        dup = any(abs((date.fromisoformat(r["date"]) - pd).days) <= 30 and abs(r["amount"] - amt) <= 0.15 * amt
                  for r in out if r.get("link_id"))
        if dup:
            continue
        name = d["ob_label"] if d["ob_kind"] == "diger" and d["ob_label"] else assets.OBLIGATIONS.get(d["ob_kind"], ("Diğer",))[0]
        inst = asset_pay.installment_info(d.get("installment"), on)
        if inst:  # poliçe toplamı bir kez; aylık taksitler ekstre dökümünde (bağlanmaz)
            name += f" · {inst['m']} taksit"
        out.append({"txn_id": None, "entry_id": None, "due_id": d["id"], "date": d["paid_on"], "description": name,
                    "installment": inst,
                    "amount": round(float(amt), 2), "cost_kind": None, "group": DUE_GROUP.get(d["ob_kind"], "diger"),
                    "source_label": "Son gün", "link_id": None, "link_source": None, "card_last4": None, "plate": None})
    out.sort(key=lambda r: (r["date"], r.get("link_id") or 0), reverse=True)
    return out


def summary(a: dict, on: date | None = None) -> dict | None:
    """Araç ayrıntısındaki "Masraflar": bu ay, son 12 ay, aylık ortalama, ayda ₺X yakıt, tür dökümü, 12 aylık seri.
    Ortalama aracın bu defterdeki ilk ayından (alım, ilk masraf ya da 12 ay önce; hangisi sonra) sayılır."""
    if a.get("kind") != "arac":
        return None
    on = on or today()
    if not allowed():
        return {"locked": True}
    rs = rows(a["id"], on)
    keys = _month_keys(on)
    this = keys[-1]
    by_month = {k: 0.0 for k in keys}
    by_group: dict[str, dict] = {}
    for r in rs:
        k = r["date"][:7]
        if k in by_month:
            by_month[k] += r["amount"]
        g = by_group.setdefault(r["group"], {"kind": r["group"], "label": GROUPS[r["group"]], "total": 0.0, "count": 0,
                                             "this_month": 0.0, "_first": k})
        g["_first"] = min(g["_first"], k)
        g["total"] += r["amount"]
        g["count"] += 1
        if k == this:
            g["this_month"] += r["amount"]
    first = max([keys[0], *([a["acquired_on"][:7]] if a.get("acquired_on") else []),
                 *([min(r["date"][:7] for r in rs)] if rs else [this])])
    months = max(1, sum(1 for k in keys if k >= first))
    total = round(sum(by_month.values()), 2)
    groups = sorted(by_group.values(), key=lambda g: -g["total"])
    for g in groups:
        g["total"], g["this_month"] = round(g["total"], 2), round(g["this_month"], 2)
        # Tür ortalaması o türün kendi ilk ayından sayılır (yakıt yalnız bu ay bağlandıysa 12'ye bölünmez).
        g_first = max(first, g.pop("_first"))
        g["monthly"] = round(g["total"] / max(1, sum(1 for k in keys if k >= g_first)), 2)
    fuel = next((g for g in groups if g["kind"] == "yakit"), None)
    return {"locked": False, "this_month": round(by_month[this], 2), "last_12": total, "months": months,
            "monthly_avg": round(total / months, 2), "fuel_monthly": fuel["monthly"] if fuel else 0.0,
            "groups": groups, "series": [{"month": k, "total": round(v, 2)} for k, v in by_month.items()],
            "count": len(rs), "km": None}


def costs(aid: int, group: str | None = None, on: date | None = None) -> dict:
    """Masraf listesi (tür süzgeçli): dokun → bağı değiştir."""
    if group is not None and group not in GROUPS:
        raise ValueError("Tür tanınmadı")
    with db.connect() as c:
        a = c.execute("SELECT * FROM assets WHERE id = ? AND deleted_at IS NULL", (aid,)).fetchone()
    if not a or a["kind"] != "arac":
        raise KeyError(aid)
    if not allowed():
        return {"locked": True, "items": [], "total": 0}
    rs = [r for r in rows(aid, on) if group is None or r["group"] == group]
    return {"locked": False, "group": group, "label": GROUPS.get(group) if group else "Tüm masraflar",
            "items": rs, "total": round(sum(r["amount"] for r in rs), 2), "can_edit": can_link()}


def question_count(on: date | None = None) -> int:
    try:
        return questions(on)["count"]
    except Exception:  # noqa: BLE001 - Yapılacaklar bozulmasın
        return 0
