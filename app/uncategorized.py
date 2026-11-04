"""Kategorisizleri düzenle: kategorisiz kayıtları işyerine/kişiye göre grupla, her gruba ücretsiz öneri, tek dokunuşla
uygula (isteğe bağlı öğren: kural ya da kişi kategorisi), geri al; istenirse yapay zekâdan öneri.

Küme: Özet'teki "N işlem kategorisiz" uyarısıyla (finance.insights, madde 10) AYNI tanım — finance.transactions(ay)
satırlarından beklemede (pending) ve atlanmış (skipped) olmayan, kategorisi "Kategorisiz" olanlar. Ay verilmezse tüm
zaman. "Diğer Gelir" kategorisiz sayılmaz (gelirin genel kategorisidir; uyarıda da sayılmaz).

Grup anahtarı:
- "k:<g|c>:<kişi>" — karşı tarafı (counterparty) bilinen elle/döküm kaydı: kişi (havale/EFT).
- "i:<g|c>:<işyeri>" — öteki her şey: kart satırında görünen işyeri adı (takma ad dahil), elle kayıtta açıklamanın
  işyeri kökü (textnorm.merchant_name). g = gelir, c = çıkış (gider/harcama/iade): aynı kişinin gelen ve giden parası
  ayrı gruptur, kategorileri farklıdır.

Öneri kaynakları (sırayla; hepsi yerel, ücretsiz):
1. "gecmis": aynı işyerinin/kişinin kullanıcı tarafından verilmiş en sık kategorisi (kartta category_manual=1, elle
   kayıtta genel olmayan kategori; kişide önce counterparties.category).
2. "kural": güncel kurallar (kullanıcı + varsayılan, app/categorize.py) açıklamaya şimdi bir kategori veriyorsa.
3. "bilinen": kart Excel'indeki sektör (card_excel.sector_category) ya da fişten öğrenilmiş marka adının kuralı.
"""
from collections import Counter, defaultdict

from app import db, finance
from app.categorize import UNCATEGORIZED, categorize, fold_key, suggest_pattern
from app.textnorm import drop_gateway, legal_key, merchant_name, normalize

GENERIC = {None, "", "Diğer", "Diğer Gelir", UNCATEGORIZED, "Ödeme"}
UNDO_LABEL = "Kategori: "
RENAME_LABEL = "İşyeri adı: "
AI_MAX_KEYS = 150   # "Yapay zekâya hepsini sor": tek çağrı (tek hak); yalnız adlar gider, istem kısa kalır
ONE_OFF_MAX = 250.0  # "Tek seferlikleri Diğer yap": tek işlemli, tutarı bunu aşmayan gider grupları
BULK_MAX = 500


def _tag(kind: str) -> str:
    return "g" if kind == finance.INCOME else "c"


def _key(r: dict) -> tuple[str, str]:
    """(anahtar, görünen ad)."""
    t = _tag(r["kind"])
    if r["source"] == "manual" and r.get("counterparty"):
        return f"k:{t}:{r['counterparty']}", r["counterparty"]
    desc = (r.get("description") or "").strip()
    if r["source"] == "card":  # ekstrede görünen işyeri adı (İşlemler'deki gibi)
        name = r["merchant"] or desc
        return f"i:{t}:{fold_key(name)}", name
    # elle/döküm kaydı: anahtar işyeri kökü, görünen ad kullanıcının yazdığı (Türkçe harfler korunur; not kısmı yok)
    shown = drop_gateway(desc)  # 'Payco/Örnek Bilişim' → 'Örnek Bilişim' (aracı adı görünmez)
    return f"i:{t}:{fold_key(merchant_name(desc) or desc)}", shown.split(" · ")[0].strip() or desc or "?"


def _rows(month: str | None) -> list[dict]:
    """finance.insights'taki kategorisiz kümesi (ay verilmezse tüm zaman)."""
    return [r for r in finance.transactions(month)
            if not r.get("pending") and not r.get("skipped") and r["category"] == UNCATEGORIZED]


def _target(r: dict) -> tuple[str, int] | None:
    """Kategorisi yazılacak tablo satırı: ('card', txn id) | ('entry', entry id) | ('split', split id)."""
    if r.get("split_of"):
        sid = r["uid"].rsplit("s", 1)[-1]
        return ("split", int(sid)) if sid.isdigit() else None
    if r["source"] == "card":
        return "card", r["id"]
    if r["source"] == "manual":
        return "entry", r.get("installment_of") or r["id"]
    return None


def _history() -> dict[str, Counter]:
    """Anahtar → kullanıcının verdiği kategorilerin sayımı (kategorisiz olmayan kayıtlardan)."""
    out: dict[str, Counter] = defaultdict(Counter)
    aliases = db.merchant_aliases()
    for t in db.card_transactions():
        if t.get("category_manual") and t["category"] not in GENERIC:
            name = finance._display_merchant(t["description"], aliases)
            out[f"i:{_tag(t['txn_type'])}:{fold_key(name)}"][t["category"]] += 1
    for e in db.list_entries():
        if e.get("debt_id") or e.get("skipped") or e["category"] in GENERIC or e["category"] == "Borç / Alacak":
            continue
        r = {"source": "manual", "kind": e["kind"], "counterparty": e.get("counterparty"),
             "description": e["description"], "merchant": e["description"]}
        out[_key(r)[0]][e["category"]] += 1
    return out


def _suggest(key: str, rows: list[dict], hist: dict[str, Counter], rules: list, people: dict) -> dict | None:
    if key.startswith("k:"):
        cp = people.get(key.split(":", 2)[2])
        if cp and cp.get("category") and cp["category"] not in GENERIC and not key.startswith("k:g:"):
            return {"category": cp["category"], "source": "gecmis", "confidence": 0.95}
    h = hist.get(key)
    if h:
        cat, n = h.most_common(1)[0]
        share = n / sum(h.values())
        return {"category": cat, "source": "gecmis", "confidence": round(min(0.95, 0.5 + 0.45 * share) if n > 1
                                                                          else 0.6 * share + 0.1, 2)}
    if key.startswith("k:"):
        return None  # kişi adına kural/sektör uygulanmaz
    kind = "harcama"
    for r in rows:
        for text in (r.get("raw"), r.get("description"), r.get("merchant")):
            if text and (cat := categorize(text, kind, rules)) != UNCATEGORIZED:
                return {"category": cat, "source": "kural", "confidence": 0.8}
    from app.card_excel import sector_category

    sectors = Counter(cat for r in rows if r["source"] == "card" and (cat := sector_category(_sector_of(r))))
    if sectors:
        return {"category": sectors.most_common(1)[0][0], "source": "bilinen", "confidence": 0.6}
    return None


def _sector_of(r: dict) -> str:
    """Kart Excel'inden gelen satırda sektör notta durur (card_excel.import_file)."""
    return r.get("note") or ""


def groups(month: str | None = None, sort: str = "count") -> list[dict]:
    """sort: "count" (en çok işlem önce; eski istemciler) | "total" (en çok tutan önce)."""
    rows = _rows(month)
    by: dict[str, list[dict]] = defaultdict(list)
    labels: dict[str, str] = {}
    manual_label: dict[str, bool] = {}  # elle yazılmış ad (Türkçe harfli) kartın ASCII adından önce gelir
    for r in rows:
        k, name = _key(r)
        by[k].append(r)
        if k not in labels or (r["source"] == "manual" and not manual_label.get(k)):
            labels[k] = name
            manual_label[k] = r["source"] == "manual"
    if not by:
        return []
    hist = _history()
    with db.connect() as c:
        rules = db.load_rules(c)
        people = {p["name"]: p for p in db.rows(c.execute("SELECT * FROM counterparties"))}
    out = []
    for k, rs in by.items():
        out.append({
            "key": k, "label": labels[k], "kind": "kisi" if k.startswith("k:") else "isyeri",
            "flow": "gelir" if k.split(":")[1] == "g" else "gider",
            "count": len(rs), "total": finance._round(sum(r["amount"] for r in rs)),
            "sample_ids": [r["uid"] for r in rs[:5]],
            "first_date": min(r["date"] for r in rs)[:10], "last_date": max(r["date"] for r in rs)[:10],
            "suggestion": _suggest(k, rs, hist, rules, people),
            "renamable": any(r["source"] == "card" for r in rs),
        })
        g = out[-1]
        g["one_off"] = g["flow"] == "gider" and g["count"] == 1 and abs(g["total"]) <= ONE_OFF_MAX
    if sort == "total":
        out.sort(key=lambda g: (-abs(g["total"]), -g["count"], g["label"]))
    else:
        out.sort(key=lambda g: (-g["count"], -abs(g["total"]), g["label"]))
    return out


def group_rows(key: str, month: str | None = None) -> list[dict]:
    return [r for r in _rows(month) if _key(r)[0] == key]


def _write(c, target: tuple[str, int], category: str) -> None:
    kind, oid = target
    if kind == "card":
        c.execute("UPDATE transactions SET category = ?, category_manual = 1 WHERE id = ?", (category, oid))
    elif kind == "entry":
        c.execute("UPDATE entries SET category = ? WHERE id = ?", (category, oid))
    else:
        c.execute("UPDATE splits SET category = ? WHERE id = ?", (category, oid))


def _add_rule(c, rows: list[dict], category: str) -> str | None:
    """İşyeri için kural (var olan aynı desenli kuralın kategorisi güncellenir); desen."""
    base = next((r for r in rows if r["source"] == "card"), rows[0])
    pattern = suggest_pattern(base.get("description") or base.get("merchant") or "")
    if not pattern:
        return None
    cur = c.execute("SELECT id FROM rules WHERE pattern = ?", (pattern,)).fetchone()
    if cur:
        c.execute("UPDATE rules SET category = ?, subcategory = CASE WHEN category = ? THEN subcategory END WHERE id = ?",
                  (category, category, cur["id"]))
    else:
        c.execute("INSERT INTO rules (pattern, category, priority) VALUES (?, ?, 10)", (pattern, category))
    return pattern


def _spread_rules(learned: list[tuple[str, str]], before: int) -> int:
    """Yeni kurallardan sonra: kartta elle atanmamış işlemler yeniden kategorilenir (tek sefer); elle/döküm
    kayıtlarından desene uyan kategorisizler de o kategoriyi alır. Ek değişen kayıt sayısı."""
    import re

    db.recategorize_all()
    extra = 0
    with db.connect() as c:
        after = c.execute("SELECT COUNT(*) FROM transactions WHERE category = ?", (UNCATEGORIZED,)).fetchone()[0]
        extra += max(0, before - after)
        entries = db.rows(c.execute("SELECT id, description FROM entries WHERE category = ? AND counterparty IS NULL "
                                    "AND kind = 'gider'", (UNCATEGORIZED,)))
        for e in entries:
            text = normalize(e["description"] or "")
            for pattern, category in learned:
                try:
                    hit = re.search(pattern, text)
                except re.error:
                    continue
                if hit:
                    c.execute("UPDATE entries SET category = ? WHERE id = ?", (category, e["id"]))
                    extra += 1
                    break
    return extra


def _uncat_cards(c) -> int:
    return c.execute("SELECT COUNT(*) FROM transactions WHERE category = ?", (UNCATEGORIZED,)).fetchone()[0]


def _remember_rule(rows: list[dict], category: str) -> tuple[str | None, int]:
    """İşyeri için kural + yayılım (bkz. _spread_rules). (desen, ek sayı)."""
    with db.connect() as c:
        pattern = _add_rule(c, rows, category)
        before = _uncat_cards(c)
    if not pattern:
        return None, 0
    return pattern, _spread_rules([(pattern, category)], before)


def _remember_person(name: str, category: str) -> None:
    """Kişi kategorisi: bundan sonra bu kişiye giden havaleler (döküm, dekont) bu kategoriyle gelir. Kişinin kategorili
    eski kayıtlarına dokunulmaz (db.set_counterparty'nin tersine): yalnız bu gruptaki kategorisizler değişti."""
    cur = db.get_counterparty(name) or {"track_debt": 0, "note": None}
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO counterparties (name, category, track_debt, note) VALUES (?, ?, ?, ?)",
                  (name, category, cur["track_debt"], cur["note"]))


def apply(key: str, category: str, remember: bool, month: str | None = None) -> dict:
    """Gruptaki kayıtlara kategori; remember: işyeri → kural, kişi (giden para) → kişi kategorisi. Geri alınabilir:
    undo_id → undo(). {changed, also_changed, remembered, rule, undo_id}."""
    from app import undo

    rows = group_rows(key, month)
    if not rows:
        raise KeyError(key)
    targets = list(dict.fromkeys(t for r in rows if (t := _target(r))))
    out = {"changed": 0, "also_changed": 0, "remembered": None, "rule": None, "undo_id": None}
    with undo.batch(f"{UNDO_LABEL}{rows[0].get('counterparty') or _key(rows[0])[1]} → {category}") as b:
        with db.connect() as c:
            for t in targets:
                _write(c, t, category)
        out["changed"] = len(rows)
        if remember:
            if key.startswith("k:c:"):
                _remember_person(key.split(":", 2)[2], category)
                out["remembered"] = "kisi"
            elif key.startswith("i:"):
                out["rule"], out["also_changed"] = _remember_rule(rows, category)
                out["remembered"] = "kural" if out["rule"] else None
        b["summary"] = {k: out[k] for k in ("changed", "also_changed", "remembered", "rule")}
    out["undo_id"] = b["id"]
    return out


def _by_key(month: str | None) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in _rows(month):
        by[_key(r)[0]].append(r)
    return by


def apply_many(items: list[tuple[str, str]], remember: bool, month: str | None = None) -> dict:
    """Birden çok gruba kategori, TEK geri alma kaydıyla ("Önerilenlerin hepsini uygula", çoklu seçim, "Tek
    seferlikleri Diğer yap"). items: [(anahtar, kategori)]. remember: apply() ile aynı (işyeri → kural, kişiye giden
    para → kişi kategorisi); kurallar eklendikten sonra yeniden kategorileme bir kez yapılır.
    {groups, changed, also_changed, rules, people, skipped: [anahtar], undo_id}."""
    from app import undo

    by = _by_key(month)
    todo = [(k, cat) for k, cat in dict(items).items() if by.get(k)]
    out = {"groups": len(todo), "changed": 0, "also_changed": 0, "rules": 0, "people": 0,
           "skipped": [k for k, _ in dict(items).items() if not by.get(k)], "undo_id": None}
    if not todo:
        return out
    cats = sorted({cat for _, cat in todo})
    label = f"{UNDO_LABEL}{len(todo)} grup → {cats[0] if len(cats) == 1 else f'{len(cats)} kategori'}"
    with undo.batch(label) as b:
        learned: list[tuple[str, str]] = []
        with db.connect() as c:
            for k, cat in todo:
                rows = by[k]
                for t in dict.fromkeys(t for r in rows if (t := _target(r))):
                    _write(c, t, cat)
                out["changed"] += len(rows)
                if not remember:
                    continue
                if k.startswith("i:") and (p := _add_rule(c, rows, cat)):
                    learned.append((p, cat))
            before = _uncat_cards(c)
        if remember:
            for k, cat in todo:
                if k.startswith("k:c:"):
                    _remember_person(k.split(":", 2)[2], cat)
                    out["people"] += 1
        if learned:
            out["rules"] = len(learned)
            out["also_changed"] = _spread_rules(learned, before)
        b["summary"] = {k: out[k] for k in ("groups", "changed", "also_changed", "rules", "people")}
    out["undo_id"] = b["id"]
    return out


def rename(display: str, key: str | None = None, uid: str | None = None, month: str | None = None) -> dict:
    """Kullanıcının verdiği görünen işyeri adı (merchant_aliases: kaynak → görünen). Ekstredeki ham açıklama değişmez;
    bu işyerinin bütün kart işlemleri (her dönem) yeni adla görünür, aynı adla gruplanır. display boşsa takma ad
    kaldırılır (ekstredeki ada döner). key: Kategorisizler grubu; uid: tek işlem ("c123"). Geri alınabilir.
    {display, sources, changed, undo_id}."""
    from app import finance, undo

    display = " ".join((display or "").split())[:60]
    descs: list[str] = []
    if key:
        descs = [r["description"] for r in _by_key(month).get(key, []) if r["source"] == "card"]
    elif uid and uid.startswith("c") and uid[1:].isdigit():
        with db.connect() as c:
            r = c.execute("SELECT description FROM transactions WHERE id = ?", (int(uid[1:]),)).fetchone()
        descs = [r["description"]] if r else []
    if not descs:
        raise KeyError(key or uid)
    aliases = db.merchant_aliases()
    old_shown = {finance._display_merchant(d, aliases) for d in descs}
    sources = set()
    for d in descs:
        sources.add(merchant_name(d))
        if (lk := legal_key(d)) in aliases:  # fişten öğrenilmiş takma ad önce bakılır: o da yeni adı göstersin
            sources.add(lk)
    sources.discard("")
    shown = display or None
    with undo.batch(f"{RENAME_LABEL}{', '.join(sorted(old_shown))} → {display or 'ekstredeki ad'}") as b:
        with db.connect() as c:
            for src in sources:
                if display and display != src:
                    c.execute("INSERT OR REPLACE INTO merchant_aliases (source, display) VALUES (?, ?)", (src, display))
                else:
                    c.execute("DELETE FROM merchant_aliases WHERE source = ?", (src,))
            new_shown = display or None
            if new_shown is None:  # kaldırıldı: tek kaynaksa yeni ad o
                new_shown = next(iter(sources)) if len(sources) == 1 else None
            if new_shown:  # işyeri kısma hedefi görünen ada bağlı: adla birlikte taşınır
                for old in old_shown - {new_shown}:
                    c.execute("UPDATE savings_goals SET name = ? WHERE kind = 'merchant' AND name = ?", (new_shown, old))
            changed = sum(1 for r in c.execute("SELECT description FROM transactions WHERE is_deleted = 0")
                          if merchant_name(r["description"]) in sources or legal_key(r["description"]) in sources)
        b["summary"] = {"display": shown, "sources": sorted(sources), "changed": changed}
    return {"display": shown or (next(iter(sources)) if len(sources) == 1 else None), "sources": sorted(sources),
            "changed": changed, "undo_id": b["id"]}


def undo_apply(undo_id: str) -> dict:
    """Yalnız bu özelliğin kaydı geri alınır (yükleme geçmişindeki başka işler değil)."""
    from app import undo

    undo.ensure()
    with db.connect() as c:
        r = c.execute("SELECT label FROM import_batches WHERE id = ?", (undo_id,)).fetchone()
    if not r or not (r["label"] or "").startswith((UNDO_LABEL, RENAME_LABEL)):
        raise KeyError(undo_id)
    return undo.undo(undo_id, force=True)


# ---------------------------------------------------------------- yapay zekâ önerisi (isteğe bağlı)

AI_PROMPT = """Türkiye'de kredi kartı ekstresinde ya da banka hesap dökümünde geçen işyeri adları aşağıda.
Her biri için en uygun harcama kategorisini SADECE verilen listeden seç. Emin değilsen null yaz.
SADECE şu JSON'u döndür: {"items": [{"name": "<ad, aynen>", "category": "<listeden>" | null, "confidence": 0.0-1.0}]}

Kategoriler: {categories}

İşyerleri:
{names}"""


def ai_names(keys: list[str], month: str | None = None) -> dict[str, str]:
    """Yapay zekâya gidecek adlar: yalnız İŞYERİ grupları (kişi adı gönderilmez), tutar/tarih yok. anahtar → ad."""
    wanted = [k for k in dict.fromkeys(keys) if k.startswith("i:")][:AI_MAX_KEYS]
    if not wanted:
        return {}
    names = {}
    for r in _rows(month):
        k, name = _key(r)
        if k in wanted and k not in names:
            names[k] = name
    return names


def ai_suggest(names: dict[str, str], categories: list[str]) -> dict[str, dict]:
    """Gemini'ye tek çağrı (metin; belge yok). anahtar → {category, source: "yz", confidence}. Kota/hak denetimi ve
    sayımı çağıranın işi (api: uncategorized_ai_suggest)."""
    from app import receipt

    listed = sorted(set(names.values()))
    prompt = AI_PROMPT.replace("{categories}", ", ".join(categories)).replace(
        "{names}", "\n".join(f"- {n}" for n in listed))
    data, _meta = receipt.gemini_text_json(prompt)
    allowed = {fold_key(c): c for c in categories}
    by_name = {}
    for it in (data.get("items") or []) if isinstance(data, dict) else []:
        if not isinstance(it, dict):
            continue
        cat = allowed.get(fold_key(str(it.get("category") or "")))
        if cat:
            try:
                conf = max(0.0, min(1.0, float(it.get("confidence") or 0.5)))
            except (TypeError, ValueError):
                conf = 0.5
            by_name[fold_key(str(it.get("name") or ""))] = {"category": cat, "source": "yz", "confidence": round(conf, 2)}
    return {k: by_name[fold_key(n)] for k, n in names.items() if fold_key(n) in by_name}


def ai_status() -> dict:
    """{available, left}: anahtar var mı, bu ay kalan hak (None = sınırsız). Hazır sürümde belge okuma hakkı havuzu,
    kendi sunucusunda ortak anahtarın çağrı kotası (kendi anahtarı olana ya da yöneticiye sınırsız)."""
    from app import plans, receipt, system

    if not receipt.enabled():
        return {"available": False, "left": None}
    if plans.hosted():
        from app import doc_reads

        return {"available": True, "left": doc_reads.left(doc_reads._subject(), doc_reads.BELGE)}  # bugünkü hak + ek paket
    s = receipt._shared_key_user()
    if not s:
        return {"available": True, "left": None}
    quota = system.ai_quota(s)
    return {"available": True, "left": None if quota is None else max(0, quota - system.ai_used_pool(s))}
