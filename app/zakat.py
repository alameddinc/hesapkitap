"""Zekât hesabı ve hatırlatıcı (Diyanet / Hanefî esas, Şâfiî seçeneği).

Kurallar (Diyanet İşleri Din İşleri Yüksek Kurulu):
- Nisap: 80,18 gram altın değerinde zekâta tabi servet. Altın, gümüş, para, döviz ve alacaklar aynı havuzda,
  değerleri toplanarak hesaplanır.
- Yıl (havl): nisaba ulaşılan günden itibaren bir kameri yıl (≈354 gün). Nisabın yılın başında ve sonunda
  bulunması yeter; yıl içindeki iniş çıkışlar, altını bozdurup paraya/dövize çevirip yeniden almak yılı bozmaz.
  Zekâta tabi malın tamamı biterse yıl yeniden başlar. Zekât, yıl sonundaki tutarın kırkta biridir (%2,5).
- Mülkiyet kişiseldir: aile içinde her altının sahibi kimse zekâtı ona düşer. Düğünde takılan takılar aksi
  konuşulmadıkça kadına aittir.
- Hanefî: kadının kullandığı altın/gümüş ziynet de zekâta tabidir; vadesi bu yıl içinde dolan borçlar düşülür
  (uzun vadeli kredilerin sadece bu yılki taksitleri).
- Şâfiî: mutad ölçüde kullanılan ziynete zekât gerekmez; borçlar düşülmez.
- Erken ve parça parça ödenebilir.

Fiyatlar: finans.truncgil.com (alış fiyatı; kuyumcunun senden alacağı fiyat). Ulaşılamazsa son bilinen fiyat,
o da yoksa kullanıcının girdiği gram altın fiyatı kullanılır.
"""
import json
import logging
import time
from datetime import date, timedelta

import httpx

from app import db

NISAB_GRAMS = 80.18
RATE = 0.025
LUNAR_YEAR_DAYS = 354
PRICE_URL = "https://finans.truncgil.com/v4/today.json"
PRICE_TTL = 3600

log = logging.getLogger(__name__)

# varlık türü → (etiket, fiyat anahtarı, birim)
KINDS: dict[str, tuple[str, str, str]] = {
    "ALTIN_24": ("Gram altın (24 ayar)", "GRA", "gr"),
    "ALTIN_22": ("22 ayar altın / bilezik", "YIA", "gr"),
    "ALTIN_18": ("18 ayar altın", "18AYARALTIN", "gr"),
    "ALTIN_14": ("14 ayar altın", "14AYARALTIN", "gr"),
    "CEYREK": ("Çeyrek altın", "CEYREKALTIN", "adet"),
    "YARIM": ("Yarım altın", "YARIMALTIN", "adet"),
    "TAM": ("Tam altın", "TAMALTIN", "adet"),
    "CUMHURIYET": ("Cumhuriyet altını", "CUMHURIYETALTINI", "adet"),
    "ATA": ("Ata / Reşat altın", "ATAALTIN", "adet"),
    "GUMUS": ("Gümüş (gram)", "GUMUS", "gr"),
    "USD": ("Dolar", "USD", "$"),
    "EUR": ("Euro", "EUR", "€"),
    "GBP": ("Sterlin", "GBP", "£"),
    "TRY": ("TL (nakit, mevduat, yatırım)", "", "₺"),
}
# borç/alacak birimleri (finance.ASSETS) → fiyat anahtarı
DEBT_ASSET_KEYS = {"GRAM_ALTIN": "GRA", "CEYREK_ALTIN": "CEYREKALTIN", "YARIM_ALTIN": "YARIMALTIN", "TAM_ALTIN": "TAMALTIN",
                   "CUMHURIYET_ALTIN": "CUMHURIYETALTINI", "USD": "USD", "EUR": "EUR", "GBP": "GBP"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS zakat_people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_self INTEGER NOT NULL DEFAULT 0,      -- uygulamadaki banka/borç verileri bu kişiye aittir
    hawl_start DATE,                          -- zekât yılının başladığı gün (nisaba ulaşılan gün)
    note TEXT
);
CREATE TABLE IF NOT EXISTS zakat_assets (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES zakat_people(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    quantity REAL NOT NULL CHECK (quantity > 0),
    is_jewelry INTEGER NOT NULL DEFAULT 0,    -- takılan ziynet (Şâfiî'de muaf)
    acquired DATE,                            -- eline geçtiği gün (yıl başlangıcı önerisi; Şâfiî'de kendi yılı)
    cost REAL,                                -- toplam alış bedeli TL (kazanç hesabı için, opsiyonel)
    estimated INTEGER NOT NULL DEFAULT 0,     -- miktar bilinmiyordu, tutar ve o günkü fiyattan tahmin edildi
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS zakat_payments (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES zakat_people(id) ON DELETE CASCADE,
    year_start DATE NOT NULL,                 -- hangi zekât yılı için
    pay_date DATE NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    note TEXT
);
"""


def _init(c) -> None:
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(zakat_assets)")}
    if "acquired" not in cols:
        c.execute("ALTER TABLE zakat_assets ADD COLUMN acquired DATE")
    if "cost" not in cols:
        c.execute("ALTER TABLE zakat_assets ADD COLUMN cost REAL")
    if "estimated" not in cols:
        c.execute("ALTER TABLE zakat_assets ADD COLUMN estimated INTEGER NOT NULL DEFAULT 0")
    if not c.execute("SELECT 1 FROM zakat_people").fetchone():
        c.execute("INSERT INTO zakat_people (name, is_self) VALUES ('Ben', 1)")


# ---------------------------------------------------------------- fiyatlar

def _num(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v) or None
    if isinstance(v, str) and v.strip():
        try:
            return float(v.replace(".", "").replace(",", ".")) or None
        except ValueError:
            return None
    return None


def _parse(raw: dict) -> dict[str, float]:
    out = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            p = _num(v.get("Buying", v.get("Alış"))) or _num(v.get("Selling", v.get("Satış")))
            if p:
                out[k] = p
    return out


def fetch_prices(force: bool = False) -> dict:
    """{prices: {anahtar: TL}, updated, source}. Sunucu geneli önbellek (system.db); kişisel veri içermez."""
    from app import system

    cached = json.loads(system.get("market_prices") or "null")
    if cached and not force and time.time() - cached["fetched"] < PRICE_TTL:
        return cached
    try:
        r = httpx.get(PRICE_URL, timeout=10, headers={"User-Agent": "HesapKitap"})
        r.raise_for_status()
        raw = r.json()
        prices = _parse(raw)
        if "GRA" not in prices:
            raise ValueError("gram altın fiyatı yok")
        data = {"prices": prices, "updated": raw.get("Update_Date"), "fetched": time.time(), "source": "truncgil"}
        system.put("market_prices", json.dumps(data))
        return data
    except Exception as e:  # noqa: BLE001 - ağ yoksa son bilinen fiyat
        log.warning("fiyatlar alınamadı: %s", e)
        return cached or {"prices": {}, "updated": None, "fetched": 0, "source": None}


def prices() -> dict:
    """Piyasa fiyatı + kullanıcının elle girdiği gram altın (piyasa yoksa ya da üzerine yazmak isterse)."""
    data = fetch_prices()
    p = dict(data["prices"])
    manual = db.get_setting("zakat:manual_gram")
    if manual:
        p["GRA"] = float(manual)
    return {**data, "prices": p, "manual_gram": float(manual) if manual else None}


# ---------------------------------------------------------------- geçmiş fiyat (miktar tahmini)

# saf altın içeriği (gram): ayar oranı × ağırlık. Ziynet/sikkelerde işçilik ve prim tahmine katılmaz:
# ödenen paranın tamamı altına sayılır → miktar olduğundan FAZLA çıkar (zekâtta eksik bırakmamak için ihtiyat).
PURE_GRAMS = {"ALTIN_24": 0.995, "ALTIN_22": 0.916, "ALTIN_18": 0.75, "ALTIN_14": 0.585,
              "CEYREK": 1.75 * 0.916, "YARIM": 3.5 * 0.916, "TAM": 7.0 * 0.916, "CUMHURIYET": 7.216 * 0.916,
              "ATA": 7.216 * 0.916}
SAFETY = 0.98  # fiyatı %2 düşük varsay → miktar biraz daha fazla çıkar
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d"


def _yahoo_low(sym: str, day: date) -> float | None:
    """O güne (ya da hemen önceki işlem gününe) ait günlük en düşük değer."""
    return _yahoo(sym, day, "low")


def _yahoo(sym: str, day: date, field: str = "close") -> float | None:
    """O güne (ya da hemen önceki işlem gününe) ait günlük değer (low/close/high)."""
    import calendar

    p1 = calendar.timegm((day - timedelta(days=6)).timetuple())
    p2 = calendar.timegm((day + timedelta(days=1)).timetuple())
    r = httpx.get(YAHOO.format(sym=sym, p1=p1, p2=p2), timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    best = None
    for ts, v in zip(res.get("timestamp") or [], res["indicators"]["quote"][0].get(field) or []):
        if v and date.fromtimestamp(ts) <= day:
            best = v
    return best


def historical_gram_price(day: date) -> float:
    """24 ayar gram altının o günkü (ihtiyatlı, düşük) TL fiyatı: ons (en düşük) × dolar/TL (en düşük) / 31,1035."""
    from app import system

    key = f"hist_gram:{day.isoformat()}"
    if v := system.get(key):
        return float(v)
    ons, usd = _yahoo_low("GC%3DF", day), _yahoo_low("TRY%3DX", day)
    if not ons or not usd:
        raise ValueError("O tarihin altın fiyatı bulunamadı")
    price = round(ons * usd / 31.1035, 2)
    system.put(key, str(price))
    return price


# geçmiş tarihte değerleme (zekât geçmişi): kapanış fiyatı; 24 ayar gram = ons × dolar / 31,1035
_HIST_FX = {"USD": "TRY%3DX", "EUR": "EURTRY%3DX", "GBP": "GBPTRY%3DX"}


def historical_prices(day: date) -> dict[str, float]:
    """O günün fiyatları (TL): GRA (24 ayar gram), USD, EUR, GBP, GUMUS. Sunucu geneli önbellek."""
    from app import system

    key = f"hist_px:{day.isoformat()}"
    if v := system.get(key):
        return json.loads(v)
    usd = _yahoo("TRY%3DX", day)
    ons = _yahoo("GC%3DF", day)
    if not usd or not ons:
        raise ValueError(f"{day:%d.%m.%Y} fiyatı bulunamadı")
    out = {"USD": usd, "GRA": round(ons * usd / 31.1035, 2)}
    for k in ("EUR", "GBP"):
        if v := _yahoo(_HIST_FX[k], day):
            out[k] = v
    if ag := _yahoo("SI%3DF", day):
        out["GUMUS"] = round(ag * usd / 31.1035, 2)
    if day < date.today():  # bugünün fiyatı gün içinde değişir; geçmiş günler sabit
        system.put(key, json.dumps(out))
    return out


def _value_at(kind: str, qty: float, px: dict) -> float | None:
    if kind == "TRY":
        return qty
    if kind in PURE_GRAMS:  # has değeri (işçiliksiz): kuyumcunun alış fiyatına yakın
        return round(qty * PURE_GRAMS[kind] / 0.995 * px["GRA"], 2) if kind != "ALTIN_24" else round(qty * px["GRA"], 2)
    p = px.get(kind)
    return round(qty * p, 2) if p else None


def estimate_quantity(kind: str, cost: float, day: date) -> dict:
    if kind not in PURE_GRAMS:
        raise ValueError("Tahmin sadece altın türleri için")
    gram = historical_gram_price(day) * SAFETY
    unit_value = gram * PURE_GRAMS[kind]
    raw = cost / unit_value
    gram_kind = kind.startswith("ALTIN")
    qty = round(raw, 1) if gram_kind else max(1, round(raw))
    return {"quantity": qty, "gram_price": round(gram, 2), "unit_value": round(unit_value, 2), "raw": round(raw, 2),
            "unit": "gr" if gram_kind else "adet"}


# ---------------------------------------------------------------- kameri takvim (gösterim için, ±1 gün)

HIJRI_MONTHS = ["Muharrem", "Safer", "Rebiülevvel", "Rebiülahir", "Cemaziyelevvel", "Cemaziyelahir", "Recep", "Şaban",
                "Ramazan", "Şevval", "Zilkade", "Zilhicce"]


def to_hijri(d: date) -> str:
    """Tablolu (aritmetik) hicri takvim; resmi takvimden 1 gün sapabilir."""
    jd = d.toordinal() + 1721425 - 1948440 + 10632
    n = (jd - 1) // 10631
    jd = jd - 10631 * n + 354
    j = ((10985 - jd) // 5316) * ((50 * jd) // 17719) + (jd // 5670) * ((43 * jd) // 15238)
    jd = jd - ((30 - j) // 15) * ((17719 * j) // 50) - (j // 16) * ((15238 * j) // 43) + 29
    m = (24 * jd) // 709
    day = jd - (709 * m) // 24
    year = 30 * n + j - 30
    return f"{day} {HIJRI_MONTHS[m - 1]} {year}"


# ---------------------------------------------------------------- hesap

def _settings() -> dict:
    return {"madhhab": db.get_setting("zakat:madhhab") or "hanefi",
            "include_app": db.get_setting("zakat:include_app") != "0",
            "include_receivables": db.get_setting("zakat:include_receivables") != "0"}


def people() -> list[dict]:
    with db.connect() as c:
        _init(c)
        ps = db.rows(c.execute("SELECT * FROM zakat_people ORDER BY is_self DESC, id"))
        for p in ps:
            p["assets"] = db.rows(c.execute("SELECT * FROM zakat_assets WHERE person_id = ? ORDER BY id", (p["id"],)))
            p["payments"] = db.rows(c.execute("SELECT * FROM zakat_payments WHERE person_id = ? ORDER BY pay_date DESC",
                                              (p["id"],)))
    return ps


def _app_lines(pr: dict, st: dict, today: date) -> tuple[list[dict], list[dict]]:
    """Uygulamadaki veriden (sadece 'Ben'): hesaplar, alacaklar; düşülecek borçlar."""
    from app import finance

    nw = finance.net_worth(today)
    plus, minus = [], []
    for a in nw["accounts"]:
        if a.get("current") is not None and a["current"] > 0:
            plus.append({"label": a["name"], "value": round(a["current"], 2), "source": "hesap"})
    for d in db.list_debts(open_only=True):
        if d["remaining"] <= 0:
            continue
        key = DEBT_ASSET_KEYS.get(d["asset"])
        rate = 1.0 if d["asset"] == "TRY" else pr.get(key or "", 0)
        if not rate:
            continue
        val = round(d["remaining"] * rate, 2)
        if d["direction"] == "alacak" and st["include_receivables"]:
            plus.append({"label": f"Alacak: {d['person']}", "value": val, "source": "alacak"})
        elif d["direction"] == "borc":
            due = d["due_date"]
            if not due or date.fromisoformat(due) <= today + timedelta(days=LUNAR_YEAR_DAYS):
                minus.append({"label": f"Borç: {d['person']}", "value": val, "source": "borc"})
    if nw["card_debt"]:
        minus.append({"label": "Kredi kartı borcu (kesilmiş ekstreler)", "value": nw["card_debt"], "source": "kart"})
    if nw["card_estimated"]:
        minus.append({"label": "Kesilecek ekstreler (tahmini)", "value": nw["card_estimated"], "source": "kart"})
    return plus, minus


def compute(today: date | None = None) -> dict:
    today = today or date.today()
    st = _settings()
    pd = prices()
    pr = pd["prices"]
    gram = pr.get("GRA")
    nisab = round(NISAB_GRAMS * gram, 2) if gram else None
    out = []
    for p in people():
        plus, minus = [], []
        # yılın bittiği gün: Şâfiî'de o güne kadar bir yılını doldurmamış varlık bu yıl sayılmaz
        year_end = (date.fromisoformat(p["hawl_start"]) + timedelta(days=LUNAR_YEAR_DAYS)) if p["hawl_start"] else today
        for a in p["assets"]:
            label, key, unit = KINDS.get(a["kind"], (a["kind"], "", ""))
            base = {"id": a["id"], "qty": a["quantity"], "unit": unit, "source": "varlik", "note": a["note"],
                    "acquired": a["acquired"]}
            if a["is_jewelry"] and st["madhhab"] == "safii":
                plus.append({**base, "label": f"{label} (ziynet, muaf)", "value": 0.0, "exempt": True})
                continue
            if (st["madhhab"] == "safii" and a["acquired"]
                    and date.fromisoformat(a["acquired"]) + timedelta(days=LUNAR_YEAR_DAYS) > year_end):
                ready = date.fromisoformat(a["acquired"]) + timedelta(days=LUNAR_YEAR_DAYS)
                plus.append({**base, "label": f"{label} (yılı {ready:%d.%m.%Y}'de dolar)", "value": 0.0, "exempt": True})
                continue
            price = 1.0 if a["kind"] == "TRY" else pr.get(key)
            plus.append({**base, "estimated": bool(a["estimated"]),
                         "label": label + (" (ziynet)" if a["is_jewelry"] else "") + (" · tahmini" if a["estimated"] else ""),
                         "value": round(a["quantity"] * price, 2) if price else None, "price": price})
        if p["is_self"] and st["include_app"]:
            ap, am = _app_lines(pr, st, today)
            plus += ap
            minus += am
        gross = round(sum(x["value"] or 0 for x in plus), 2)
        deduct = round(sum(x["value"] for x in minus), 2) if st["madhhab"] == "hanefi" else 0.0
        total = round(max(0.0, gross - deduct), 2)
        above = nisab is not None and total >= nisab
        hawl = None
        suggest = None
        if not p["hawl_start"] and nisab:
            # tarihi girilmiş varlıkları sırayla topla: toplam nisaba ilk ulaştığı gün (bugünkü fiyatla, yaklaşık)
            dated = sorted((x["acquired"], x["value"] or 0) for x in plus if x.get("acquired") and not x.get("exempt"))
            undated = sum(x["value"] or 0 for x in plus if not x.get("acquired")) - deduct
            run = undated
            for d_, v in dated:
                run += v
                if run >= nisab:
                    suggest = d_
                    break
        if p["hawl_start"]:
            start = date.fromisoformat(p["hawl_start"])
            due = start + timedelta(days=LUNAR_YEAR_DAYS)
            paid = round(sum(x["amount"] for x in p["payments"] if x["year_start"] == p["hawl_start"]), 2)
            zakat = round(total * RATE, 2) if above else 0.0
            hawl = {"start": p["hawl_start"], "start_hijri": to_hijri(start), "due": due.isoformat(),
                    "due_hijri": to_hijri(due), "days_left": (due - today).days,
                    "progress": min(100.0, round((today - start).days / LUNAR_YEAR_DAYS * 100, 1)),
                    "zakat": zakat, "paid": paid, "remaining": round(max(0.0, zakat - paid), 2)}
        out.append({"id": p["id"], "name": p["name"], "is_self": bool(p["is_self"]), "note": p["note"],
                    "plus": plus, "minus": minus if st["madhhab"] == "hanefi" else [], "gross": gross, "deduct": deduct,
                    "total": total, "above_nisab": above, "estimate": round(total * RATE, 2) if above else 0.0,
                    "hawl": hawl, "suggest_start": suggest, "payments": p["payments"],
                    "missing_prices": [x["label"] for x in plus if x.get("value") is None]})
    return {"nisab": nisab, "nisab_grams": NISAB_GRAMS, "gram_price": gram, "rate": RATE, "settings": st,
            "prices": {"updated": pd["updated"], "source": pd["source"] if not pd.get("manual_gram") else "elle",
                       "manual_gram": pd.get("manual_gram"),
                       "list": {k: pr[v[1]] for k, v in KINDS.items() if v[1] and v[1] in pr}},
            "kinds": {k: {"label": v[0], "unit": v[2]} for k, v in KINDS.items()},
            "people": out, "today_hijri": to_hijri(today)}


# ---------------------------------------------------------------- varlıklar (Net durum)

def holdings() -> dict:
    """Altın/döviz/para varlıkları: bugünkü değer, alış bedeli girildiyse kazanç. 'Ben'inkiler Net durum'a girer,
    aile üyelerininki ayrı gösterilir. Varlık yoksa fiyat çekilmez."""
    ps = people()
    if not any(p["assets"] for p in ps):
        return {"items": [], "self_total": 0.0, "family_total": 0.0, "updated": None}
    pd = prices()
    pr = pd["prices"]
    items = []
    for p in ps:
        for a in p["assets"]:
            label, key, unit = KINDS.get(a["kind"], (a["kind"], "", ""))
            price = 1.0 if a["kind"] == "TRY" else pr.get(key)
            value = round(a["quantity"] * price, 2) if price else None
            gain = round(value - a["cost"], 2) if value is not None and a["cost"] else None
            items.append({**a, "label": label, "unit": unit, "price": price, "value": value, "person": p["name"],
                          "is_self": bool(p["is_self"]), "gain": gain,
                          "gain_pct": round(gain / a["cost"] * 100, 1) if gain is not None else None})
    return {"items": items, "updated": pd["updated"],
            "self_total": round(sum(i["value"] or 0 for i in items if i["is_self"]), 2),
            "family_total": round(sum(i["value"] or 0 for i in items if not i["is_self"]), 2)}


# ---------------------------------------------------------------- geçmiş yıllar (kaza zekâtı)

def _bank_at(day: date) -> tuple[float, list[str]]:
    """O günkü banka/nakit bakiyeleri: bugünkü çapadan geriye (ya da ileriye) hareketlerle. Hareketi o tarihe
    uzanmayan hesap bilinmiyor sayılır."""
    total, unknown = 0.0, []
    with db.connect() as c:
        accs = db.rows(c.execute("SELECT * FROM accounts WHERE kind <> 'kart' AND as_of IS NOT NULL"))
        for a in accs:
            first = c.execute("SELECT MIN(entry_date) FROM entries WHERE account_id = ? AND skipped = 0",
                              (a["id"],)).fetchone()[0]
            d = day.isoformat()
            if d < a["as_of"] and (not first or first > d):
                unknown.append(a["name"])
                continue
            lo, hi, sign = (d, a["as_of"], -1) if d < a["as_of"] else (a["as_of"], d, 1)
            flows = c.execute("SELECT COALESCE(SUM(CASE WHEN kind = 'gelir' THEN amount ELSE -amount END), 0) "
                              "FROM entries WHERE account_id = ? AND skipped = 0 AND entry_date > ? AND entry_date <= ?",
                              (a["id"], lo, hi)).fetchone()[0]
            debt = sum(r["flow"] * r["amount"] for r in db.debt_rows()
                       if r.get("account_id") == a["id"] and lo < r["date"] <= hi and r["step"] != "vazgecildi")
            total += a["balance"] + sign * (flows + debt)
    return round(total, 2), unknown


def _debts_at(day: date, px: dict) -> float:
    """O gün ödenmemiş kart ekstreleri (kesilmiş, son ödemesi bir yıl içinde) + açık kişisel borçlar."""
    d = day.isoformat()
    total = 0.0
    for st in db.list_statements():
        if st["period_end"] <= d and (not st["paid_at"] or st["paid_at"] > d):
            total += st["total_debt"] or 0
    for x in db.list_debts():
        if x["direction"] != "borc" or x["debt_date"] > d or (x["settled_at"] and x["settled_at"] <= d):
            continue
        paid = sum(p["amount"] for p in x["payments"] if p["paid_at"] <= d)
        rate = 1.0 if x["asset"] == "TRY" else px.get(DEBT_ASSET_KEYS.get(x["asset"], ""), 0)
        total += max(0.0, x["amount"] - paid) * rate
    return round(total, 2)


def history(pid: int, today: date | None = None, max_years: int = 6) -> dict:
    """Geçmiş zekât yılları: her yıl sonunda varlık, nisap, zekât, ödenen, kalan (kaza). Yıllar mevcut yıl
    başlangıcından geriye 354'er gün; verinin başladığı yere kadar."""
    today = today or date.today()
    st = _settings()
    p = next((x for x in people() if x["id"] == pid), None)
    if not p:
        raise LookupError("Kişi yok")
    anchor = date.fromisoformat(p["hawl_start"]) if p["hawl_start"] else None
    if not anchor:
        return {"years": [], "need_start": True, "total_due": 0.0, "notes": []}
    dates = [a["acquired"] for a in p["assets"] if a["acquired"]]
    if p["is_self"] and st["include_app"]:
        with db.connect() as c:
            if first := c.execute("SELECT MIN(entry_date) FROM entries WHERE import_hash IS NOT NULL").fetchone()[0]:
                dates.append(first)
    if not dates:
        return {"years": [], "need_start": False, "total_due": 0.0,
                "notes": ["Geçmişi hesaplamak için varlıklara alış tarihi gir ya da eski hesap dökümlerini yükle."]}
    earliest = date.fromisoformat(min(dates))
    notes = set()
    if any(not a["acquired"] for a in p["assets"]):
        notes.add("Tarihi girilmemiş varlıklar her yıl elinde varsayıldı.")
    if p["is_self"] and st["include_app"]:
        notes.add("Sadece dökümü yüklenen bankalar bilinir; evdeki nakit, diğer bankalar ve arada satılanlar hesapta yok.")
    ends = []
    end = anchor
    while end > earliest and len(ends) < max_years:
        ends.append(end)
        end -= timedelta(days=LUNAR_YEAR_DAYS)
    years, carry = [], 0.0
    for end in reversed(ends):  # eskiden yeniye: ödenmeyen zekât sonraki yılın tabanından düşülür (Hanefî)
        start = end - timedelta(days=LUNAR_YEAR_DAYS)
        try:
            px = historical_prices(end)
        except Exception as e:  # noqa: BLE001
            years.append({"start": start.isoformat(), "end": end.isoformat(), "end_hijri": to_hijri(end),
                          "error": str(e)})
            continue
        items = 0.0
        for a in p["assets"]:
            if a["acquired"] and a["acquired"] > end.isoformat():
                continue
            if a["is_jewelry"] and st["madhhab"] == "safii":
                continue
            items += _value_at(a["kind"], a["quantity"], px) or 0
        bank, unknown = (0.0, [])
        deduct = 0.0
        if p["is_self"] and st["include_app"]:
            bank, unknown = _bank_at(end)
            if st["madhhab"] == "hanefi":
                deduct = _debts_at(end, px)
        if st["madhhab"] == "hanefi":
            deduct += carry
        total = round(max(0.0, items + bank - deduct), 2)
        nisab = round(NISAB_GRAMS * px["GRA"], 2)
        due = round(total * RATE, 2) if total >= nisab else 0.0
        paid = round(sum(x["amount"] for x in p["payments"] if x["year_start"] == start.isoformat()), 2)
        left = round(max(0.0, due - paid), 2)
        carry = left
        years.append({"start": start.isoformat(), "end": end.isoformat(), "end_hijri": to_hijri(end),
                      "assets": round(items, 2), "bank": bank, "deduct": deduct, "total": total, "nisab": nisab,
                      "gram_price": px["GRA"], "above_nisab": total >= nisab, "zakat": due, "paid": paid, "remaining": left,
                      "unknown_accounts": unknown, "partial": start < earliest})
    return {"years": list(reversed(years)), "need_start": False, "notes": sorted(notes),
            "total_due": round(sum(y.get("remaining", 0) for y in years), 2)}


# ---------------------------------------------------------------- değişiklikler

def add_person(name: str) -> int:
    with db.connect() as c:
        _init(c)
        return c.execute("INSERT INTO zakat_people (name) VALUES (?)", (name.strip(),)).lastrowid


def update_person(pid: int, data: dict) -> bool:
    data = {k: v for k, v in data.items() if k in ("name", "hawl_start", "note")}
    if not data:
        return False
    with db.connect() as c:
        _init(c)
        return c.execute(f"UPDATE zakat_people SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?",
                         (*data.values(), pid)).rowcount > 0


def delete_person(pid: int) -> bool:
    with db.connect() as c:
        _init(c)
        return c.execute("DELETE FROM zakat_people WHERE id = ? AND is_self = 0", (pid,)).rowcount > 0


def add_asset(pid: int, kind: str, quantity: float, is_jewelry: bool, note: str | None, acquired: str | None = None,
              cost: float | None = None, estimated: bool = False) -> int:
    if kind not in KINDS:
        raise ValueError("Bilinmeyen varlık türü")
    with db.connect() as c:
        _init(c)
        return c.execute("INSERT INTO zakat_assets (person_id, kind, quantity, is_jewelry, note, acquired, cost, estimated) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (pid, kind, quantity, int(is_jewelry), (note or "").strip() or None, acquired, cost,
                          int(estimated))).lastrowid


def update_asset(aid: int, data: dict) -> bool:
    data = {k: v for k, v in data.items() if k in ("person_id", "kind", "quantity", "is_jewelry", "note", "acquired", "cost", "estimated")}
    if not data:
        return False
    with db.connect() as c:
        _init(c)
        return c.execute(f"UPDATE zakat_assets SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?",
                         (*data.values(), aid)).rowcount > 0


def delete_asset(aid: int) -> bool:
    with db.connect() as c:
        _init(c)
        return c.execute("DELETE FROM zakat_assets WHERE id = ?", (aid,)).rowcount > 0


def add_payment(pid: int, amount: float, pay_date: str, note: str | None, year_start: str | None = None) -> int:
    """year_start verilmezse içinde bulunulan zekât yılı (geçmiş yıl için o yılın başlangıcı)."""
    with db.connect() as c:
        _init(c)
        p = c.execute("SELECT hawl_start FROM zakat_people WHERE id = ?", (pid,)).fetchone()
        if not p:
            raise LookupError("Kişi yok")
        if not (year_start or p["hawl_start"]):
            raise ValueError("Önce zekât yılının başladığı günü gir")
        return c.execute("INSERT INTO zakat_payments (person_id, year_start, pay_date, amount, note) VALUES (?, ?, ?, ?, ?)",
                         (pid, year_start or p["hawl_start"], pay_date, amount, (note or "").strip() or None)).lastrowid


def delete_payment(pay_id: int) -> bool:
    with db.connect() as c:
        _init(c)
        return c.execute("DELETE FROM zakat_payments WHERE id = ?", (pay_id,)).rowcount > 0


def next_year(pid: int) -> str:
    """Yıl doldu: yeni zekât yılı bir öncekinin bittiği gün başlar."""
    with db.connect() as c:
        _init(c)
        p = c.execute("SELECT hawl_start FROM zakat_people WHERE id = ?", (pid,)).fetchone()
        if not p or not p["hawl_start"]:
            raise ValueError("Zekât yılı başlangıcı yok")
        new = (date.fromisoformat(p["hawl_start"]) + timedelta(days=LUNAR_YEAR_DAYS)).isoformat()
        c.execute("UPDATE zakat_people SET hawl_start = ? WHERE id = ?", (new, pid))
        return new


def reminders(today: date) -> list[str]:
    """Zekât yılı dolmasına 7 gün kala ve dolduğu gün (Telegram)."""
    msgs = []
    with db.connect() as c:  # yıl başlangıcı girilmemişse fiyat bile çekme
        _init(c)
        if not c.execute("SELECT 1 FROM zakat_people WHERE hawl_start IS NOT NULL").fetchone():
            return msgs
    try:
        data = compute(today)
    except Exception:  # noqa: BLE001 - fiyat/hesap hatası diğer hatırlatmaları engellemesin
        log.exception("zekât hesabı")
        return msgs
    for p in data["people"]:
        h = p["hawl"]
        if not h or h["days_left"] not in (7, 0) or h["remaining"] <= 0:
            continue
        when = "BUGÜN doluyor" if h["days_left"] == 0 else "7 gün kaldı"
        amount = f"{h['remaining']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        msgs.append(f"🌙 <b>Zekât</b> · {p['name']}: yıl {date.fromisoformat(h['due']):%d.%m.%Y} ({h['due_hijri']}) — {when}\n"
                    f"Güncel hesap: <b>{amount} TL</b> ödenecek")
    return msgs
