"""Varlıklarım H9 — vade "ödendi" önerisi: ekstre / dökümde vergi, sigorta, muayene ödemesi görününce ilgili vadenin
ödendi olduğu ÖNERİLİR (kendiliğinden işaretlenmez; tek dokunuşla onay). docs: hesapkitap-mobile
docs/varliklar-ozellik.md §10.4, §20.

Eşleme:
- Tür kalıbı (payment_kind): MTV / GİB / vergi dairesi → mtv, kira_beyan; belediye emlak → emlak; sigorta şirketleri ve
  "SIGORTA / KASKO / DASK" → trafik, kasko, dask, konut_sig (metinde KASKO / TRAFIK / DASK / KONUT geçiyorsa yalnız o);
  TÜVTÜRK → muayene. Hayat / sağlık / emeklilik sigortası ve banka ücreti (BSMV, KKDF, faiz) aday değildir.
- Tarih: vadenin son günü ±WINDOW_DAYS. Tutar: vade tutarı girilmişse ±AMOUNT_TOL; girilmemişse tutar şartı yok (öneri
  "tutarı da kaydedelim mi?" der). MTV / emlak iki taksidi tek seferde (≈ 2× taksit) → iki vade birden.
- Taksitli poliçe ("NEOVA SIGORTA ... 7/8 Taksit", "TKS 07/08", ekstre satırının installment_no / installments_left'i):
  aynı şirket, aynı taksit sayısı (M), taksit tutarı ±INST_TOL ve ardışık aylar (poliçe başlangıç ayı ±1) tek ödeme
  grubu. Poliçe toplamı = taksit × M, tarih = poliçe başlangıcı (1. taksit). Onaylanınca vade "Taksitle ödeniyor (n/M)"
  ile ödendi sayılır: kalan taksitler Planlama'da zaten kart ödemesinin içinde, vade ayrıca harcanabilirden düşmez.
  Masraflar'da poliçe toplamı bir kez (sigorta-vergi-muayene grubu); aylık taksitler ekstre dökümünde kalır.
  Değer ilkesi: taksit önermez / özendirmez, yalnız takip eder.

Okuma yazmaz (öneri hesaplanır); yazma yalnız accept / dismiss. Paket kapalıysa ya da üye görüntüleyiciyse öneri yok.
Ortak defter üyesi yalnız görebildiği işlemleri görür (db.card_transactions / list_entries süzülü).
"""
import json
import re
from datetime import date

from app import db

WINDOW_DAYS = 45
AMOUNT_TOL = 0.15
INST_TOL = 0.02
LOOKBACK_DAYS = 400
MAX_OPTIONS = 4
DISMISSED = "assets:pay_dismissed"

INSURERS = [
    ("ALLIANZ", r"\bALLIANZ"), ("AXA SIGORTA", r"\bAXA\b"), ("ANADOLU SIGORTA", r"\bANADOLU (?:ANONIM )?(?:TURK )?SIG"),
    ("MAPFRE", r"\bMAPFRE"), ("HDI SIGORTA", r"\bHDI\b"), ("SOMPO", r"\bSOMPO"), ("QUICK SIGORTA", r"\bQUICK ?SIG"),
    ("RAY SIGORTA", r"\bRAY ?SIG"), ("NEOVA", r"\bNEOVA\b"), ("TURKIYE SIGORTA", r"\bTURKIYE SIG"),
    ("AK SIGORTA", r"\bAK ?SIG"), ("DOGA SIGORTA", r"\bDOGA ?SIG"), ("UNICO", r"\bUNICO\b"),
    ("BEREKET SIGORTA", r"\bBEREKET ?SIG"), ("GROUPAMA", r"\bGROUPAMA"), ("ZURICH", r"\bZURICH"),
    ("EUREKO", r"\bEUREKO"), ("GENERALI", r"\bGENERALI"), ("HEPIYI", r"\bHEPIYI"), ("ANKARA SIGORTA", r"\bANKARA ?SIG"),
    ("SEKER SIGORTA", r"\bSEKER ?SIG"), ("KORU SIGORTA", r"\bKORU ?SIG"), ("MAGDEBURGER", r"\bMAGDEBURGER"),
    ("TURK NIPPON", r"\bTURK NIPPON"), ("ORIENT SIGORTA", r"\bORIENT ?SIG"), ("GULF SIGORTA", r"\bGULF ?SIG"),
    ("KOALAY", r"\bKOALAY"), ("SIGORTAM.NET", r"\bSIGORTAM ?\.? ?NET"),
]
_INS = [(n, re.compile(r)) for n, r in INSURERS]
SIGORTA = re.compile(r"\bSIGORTA|\bSIG\b\.?|\bKASKO\b|\bDASK\b|ZORUNLU DEPREM")
NOT_ASSET_INS = re.compile(r"\bHAYAT\b|EMEKLILIK|SAGLIK|\bBES\b|SEYAHAT|FERDI KAZA")
MTV = re.compile(r"\bMTV\b|MOTORLU ?TASIT")
EMLAK = re.compile(r"EMLAK ?VERG|BELEDIYE.*\b(EMLAK|VERGI)|\bEMLAK\b.*BELEDIYE")
GIB = re.compile(r"\bGIB\b|GELIR IDARESI|VERGI DAIRESI|DIJITAL VERGI|VERGI ODEME|VERGI TAHSIL|\bHAZINE ?VERGI")
TUV = re.compile(r"TUVTURK|TUV TURK|ARAC MUAYENE|MUAYENE ISTASYON")
BANK_FEE = re.compile(r"^(VADE FARKI|BSMV|KKDF|FAIZ|GECIKME)\b|\bFAIZ\b|GECIKME FAIZ|\bBSMV\b|\bKKDF\b")
INST_RX = [
    re.compile(r"\b(\d{1,2}) ?/ ?(\d{1,2}) ?\.? ?(?:TAKSIT|TAKSIDI|TKS|TK)\b"),
    re.compile(r"\b(?:TAKSIT|TAKSIDI|TKS)\.? ?:? ?(\d{1,2}) ?/ ?(\d{1,2})\b"),
]
INST_STRIP = re.compile(r"\b\d{1,2} ?/ ?\d{1,2} ?\.? ?(?:TAKSIT|TAKSIDI|TKS|TK)\b|\b(?:TAKSIT|TAKSIDI|TKS)\.? ?:? ?\d{1,2} ?/ ?\d{1,2}\b")
GROUP_KINDS = {"sigorta": ("trafik", "kasko", "dask", "konut_sig"), "vergi": ("mtv", "kira_beyan"), "emlak": ("emlak",),
               "muayene": ("muayene",)}
GROUP_LABEL = {"sigorta": "Sigorta", "vergi": "Vergi", "emlak": "Emlak vergisi", "muayene": "Muayene"}
DOUBLE_KINDS = ("mtv", "emlak")  # iki taksit tek seferde


def _norm(s: str | None) -> str:
    from app.textnorm import normalize

    return normalize(s or "")


def payment_kind(description: str) -> dict | None:
    """Açıklamadan vade ödemesi türü: {group, kinds, company} ya da None. company: sigorta şirketinin kalıp adı (yoksa
    taksit ifadesi atılmış ilk üç kelime)."""
    d = _norm(description)
    if not d or BANK_FEE.search(d):
        return None
    if TUV.search(d):
        return {"group": "muayene", "kinds": GROUP_KINDS["muayene"], "company": "TUVTURK"}
    if MTV.search(d):
        return {"group": "vergi", "kinds": ("mtv",), "company": "MTV"}
    if EMLAK.search(d):
        return {"group": "emlak", "kinds": ("emlak",), "company": "BELEDIYE"}
    comp = next((n for n, rx in _INS if rx.search(d)), None)
    if comp or SIGORTA.search(d):
        if NOT_ASSET_INS.search(d):
            return None
        if re.search(r"\bKASKO\b", d):
            kinds = ("kasko",)
        elif re.search(r"\bTRAFIK\b|ZORUNLU MALI", d):
            kinds = ("trafik",)
        elif re.search(r"\bDASK\b|DEPREM|DOGAL AFET", d):
            kinds = ("dask",)
        elif re.search(r"\bKONUT\b|\bEV SIG", d):
            kinds = ("konut_sig",)
        else:
            kinds = GROUP_KINDS["sigorta"]
        if not comp:
            comp = " ".join(INST_STRIP.sub(" ", d).split()[:3])
        return {"group": "sigorta", "kinds": kinds, "company": comp}
    if GIB.search(d):
        return {"group": "vergi", "kinds": GROUP_KINDS["vergi"], "company": "GIB"}
    return None


def parse_installment(description: str, no: int | None = None, left: int | None = None) -> tuple[int, int] | None:
    """Taksit sırası ve sayısı (n, M): ekstre satırının alanları (installment_no + installments_left) ya da metindeki
    "7/8 Taksit", "TAKSIT 07/08", "TKS 7/8", "İşlemin 1 / 9 Taksidi". Tek çekim ya da tutarsız sayı → None."""
    if no and left is not None and left >= 0:
        n, m = int(no), int(no) + int(left)
    else:
        d = _norm(description)
        hit = next((rx.search(d) for rx in INST_RX if rx.search(d)), None)
        if not hit:
            return None
        n, m = int(hit.group(1)), int(hit.group(2))
    return (n, m) if 1 <= n <= m and 2 <= m <= 36 else None


# ---------------------------------------------------------------- adaylar

def _mi(d: date) -> int:
    return d.year * 12 + d.month - 1


def _shift(d: date, months: int) -> date:
    from app import assets

    return assets.add_months(d, months)


def rows(on: date) -> list[dict]:
    """Görünür ödeme adayları: kart işlemleri (harcama) ve gider kayıtları (ekstredeki karşılığına bağlı kayıt sayılmaz).
    Her satır {ref, txn_id, entry_id, date, month, description, amount, pk, inst}."""
    lo = date.fromordinal(on.toordinal() - LOOKBACK_DAYS).isoformat()
    hi = on.isoformat()
    out = []
    for t in db.card_transactions():
        amt = t.get("amount") or 0
        if amt <= 0 or t.get("txn_type") in ("odeme", "iade", "faiz_ucret") or not (lo <= t["txn_date"][:10] <= hi):
            continue
        pk = payment_kind(t["description"])
        if pk:
            out.append({"ref": f"t{t['id']}", "txn_id": t["id"], "entry_id": None, "date": t["txn_date"][:10],
                        "month": (t.get("period_end") or t["txn_date"])[:7], "description": t["description"],
                        "amount": round(float(amt), 2), "pk": pk,
                        "inst": parse_installment(t["description"], t.get("installment_no"), t.get("installments_left"))})
    for e in db.list_entries():
        if e.get("kind") != "gider" or e.get("skipped") or e.get("linked_txn_id") or not (lo <= (e.get("entry_date") or "")[:10] <= hi):
            continue
        pk = payment_kind(e["description"])
        if pk:
            out.append({"ref": f"e{e['id']}", "txn_id": None, "entry_id": e["id"], "date": e["entry_date"][:10],
                        "month": e["entry_date"][:7], "description": e["description"], "amount": round(float(e["amount"]), 2),
                        "pk": pk, "inst": parse_installment(e["description"])})
    out.sort(key=lambda x: (x["date"], x["ref"]))
    return out


def _start_of(x: dict) -> date:
    """Taksit satırından poliçe başlangıcı (1. taksit): satır tarihi ekstre ayına yakınsa (aylık tarih) n−1 ay geri;
    bazı bankalar her taksitte asıl işlem tarihini yazar, o zaman tarihin kendisi."""
    d = date.fromisoformat(x["date"])
    y, m = map(int, x["month"].split("-"))
    if abs(_mi(d) - (y * 12 + m - 1)) <= 1:
        return _shift(d, -(x["inst"][0] - 1))
    return d


def group_installments(xs: list[dict]) -> list[dict]:
    """Aynı poliçenin taksitleri tek grup: aynı şirket, aynı M, taksit tutarı ±INST_TOL, başlangıç ayı ±1.
    Dönen {key, company, m, per, total, start, seen [n...], refs, rows, pk}."""
    groups: list[dict] = []
    for x in sorted((x for x in xs if x.get("inst")), key=lambda x: (x["date"], x["ref"])):
        n, m = x["inst"]
        st = _start_of(x)
        comp = x["pk"]["company"]
        g = next((g for g in groups if g["company"] == comp and g["m"] == m and abs(x["amount"] - g["per"]) <= INST_TOL * g["per"]
                  and abs(_mi(st) - _mi(g["start"])) <= 1 and n not in g["seen"]), None)
        if not g:
            g = {"company": comp, "m": m, "per": x["amount"], "start": st, "seen": [], "refs": [], "rows": [], "pk": x["pk"]}
            groups.append(g)
        g["seen"].append(n)
        g["refs"].append(x["ref"])
        g["rows"].append(x)
        if st < g["start"]:
            g["start"] = st
    for g in groups:
        g["seen"].sort()
        g["total"] = round(g["per"] * g["m"], 2)
        g["key"] = f"tk:{g['company']}|{g['m']}|{g['start'].isoformat()[:7]}|{int(round(g['per']))}"
    return groups


# ---------------------------------------------------------------- vade eşleşmesi

def _open_dues(c) -> list[dict]:
    from app import assets

    q = ("SELECT d.*, o.kind AS ob_kind, o.label AS ob_label, a.id AS asset_id, a.name AS asset_name, a.kind AS asset_kind "
         "FROM asset_dues d JOIN asset_obligations o ON o.id = d.obligation_id JOIN assets a ON a.id = o.asset_id "
         "WHERE d.status = 'bekliyor' AND o.active = 1 AND a.deleted_at IS NULL ORDER BY d.due_date, d.id")
    out = []
    for r in c.execute(q):
        r = dict(r)
        name = r["ob_label"] if r["ob_kind"] == "diger" and r["ob_label"] else assets.OBLIGATIONS.get(r["ob_kind"], ("Diğer",))[0]
        part = r["label"].split(" ", 1)[1] if r.get("label") and " " in r["label"] else None
        r["title"] = name + (f" · {part}" if part else "")
        out.append(r)
    return out


def _used(c) -> tuple[set[str], set[str]]:
    refs, keys = set(), set()
    for r in c.execute("SELECT txn_id, entry_id, installment FROM asset_dues WHERE status = 'odendi'"):
        if r["txn_id"] is not None:
            refs.add(f"t{r['txn_id']}")
        if r["entry_id"] is not None:
            refs.add(f"e{r['entry_id']}")
        if r["installment"]:
            try:
                keys.add(json.loads(r["installment"]).get("key"))
            except ValueError:
                pass
    return refs, keys


def _dismissed(c) -> set[str]:
    r = c.execute("SELECT value FROM settings WHERE key = ?", (DISMISSED,)).fetchone()
    try:
        return set(json.loads(r[0])) if r else set()
    except ValueError:
        return set()


def match(unit: dict, dues: list[dict]) -> list[dict]:
    """Birimin (tek ödeme ya da taksit grubu) eşleşen vadeleri, yakından uzağa. Seçenek {due_ids, due_id, asset_id,
    asset_name, asset_kind, kind, title, due_date, due_amount, amount (vadeye yazılacak tutar), double, score}."""
    when = date.fromisoformat(unit["date"])
    amt = unit["amount"]
    out = []
    for d in dues:
        if d["ob_kind"] not in unit["kinds"]:
            continue
        dd = date.fromisoformat(d["due_date"])
        gap = abs((dd - when).days)
        if gap > WINDOW_DAYS:
            continue
        due_amt = d["amount"]
        double = None
        if due_amt:
            ok = abs(amt - due_amt) <= AMOUNT_TOL * due_amt
            if not ok and not unit.get("inst") and d["ob_kind"] in DOUBLE_KINDS and abs(amt - 2 * due_amt) <= AMOUNT_TOL * 2 * due_amt:
                double = next((x for x in dues if x["obligation_id"] == d["obligation_id"] and x["id"] != d["id"]
                               and x["due_date"][:4] == d["due_date"][:4] and x["due_date"] > d["due_date"]), None)
                ok = double is not None
            if not ok:
                continue
            score = gap / WINDOW_DAYS + abs(amt - due_amt * (2 if double else 1)) / (due_amt * (2 if double else 1))
        else:
            score = gap / WINDOW_DAYS + 0.5  # tutarsız vade: tutarlı eşleşmenin gerisinde
        ids = [d["id"]] + ([double["id"]] if double else [])
        out.append({"due_id": d["id"], "due_ids": ids, "asset_id": d["asset_id"], "asset_name": d["asset_name"],
                    "asset_kind": d["asset_kind"], "kind": d["ob_kind"],
                    "title": d["title"] + (" · iki taksit birden" if double else ""), "due_date": d["due_date"],
                    "due_amount": due_amt, "amount": amt, "double": bool(double), "score": round(score, 4)})
    out.sort(key=lambda o: (o["score"], o["due_date"], o["due_id"]))
    # aynı vadeyi iki seçenek olarak gösterme (tek ve çift eşleşme)
    seen, uniq = set(), []
    for o in out:
        if o["due_id"] in seen:
            continue
        seen.add(o["due_id"])
        uniq.append(o)
    return uniq[:MAX_OPTIONS]


def allowed() -> bool:
    from app import asset_costs

    return asset_costs.allowed()


def can_answer() -> bool:
    from app import assets

    return assets.can_pay()


def _units(xs: list[dict]) -> list[dict]:
    units = []
    for x in xs:
        if not x.get("inst"):
            units.append({"key": x["ref"], "refs": [x["ref"]], "txn_id": x["txn_id"], "entry_id": x["entry_id"],
                          "date": x["date"], "description": x["description"], "amount": x["amount"],
                          "kinds": x["pk"]["kinds"], "group": x["pk"]["group"], "company": x["pk"]["company"], "inst": None})
    for g in group_installments(xs):
        last = g["rows"][-1]
        units.append({"key": g["key"], "refs": g["refs"], "txn_id": last["txn_id"], "entry_id": last["entry_id"],
                      "date": g["start"].isoformat(), "description": INST_STRIP.sub(" ", _norm(last["description"])).strip(),
                      "amount": g["total"], "kinds": g["pk"]["kinds"], "group": g["pk"]["group"], "company": g["company"],
                      "inst": {"m": g["m"], "per": g["per"], "total": g["total"], "seen": g["seen"],
                               "last_n": max(g["seen"]), "start": g["start"].isoformat(), "key": g["key"],
                               "payments": [{"n": r["inst"][0], "date": r["date"], "amount": r["amount"]} for r in g["rows"]]}})
    return units


def suggestions(on: date | None = None) -> dict:
    """Bekleyen "ödendi mi?" önerileri: {items [{key, date, description, amount, group, group_label, company, inst,
    options [...], question ("paid" | "which"), ask_amount, title, text}], can_answer, locked, count}."""
    from app import assets

    on = on or assets.today()
    if not allowed():
        return {"items": [], "can_answer": False, "locked": True, "count": 0}
    assets.ensure(on)
    xs = rows(on)
    if not xs:
        return {"items": [], "can_answer": can_answer(), "locked": False, "count": 0}
    with db.connect() as c:
        dues = _open_dues(c)
        used_refs, used_keys = _used(c)
        gone = _dismissed(c)
    xs = [x for x in xs if x["ref"] not in used_refs]
    items = []
    for u in _units(xs):
        if u["key"] in gone or u["key"] in used_keys:
            continue
        opts = match(u, dues)
        if not opts:
            continue
        items.append(_out(u, opts))
    items.sort(key=lambda s: (s["date"], s["key"]), reverse=True)
    return {"items": items, "can_answer": can_answer(), "locked": False, "count": len(items)}


def _tl(x: float) -> str:
    from app import money

    return money.tl(x, trim=True)


def _out(u: dict, opts: list[dict]) -> dict:
    assets_n = {o["asset_id"] for o in opts}
    which = len(opts) > 1
    cars = all(o["asset_kind"] == "arac" for o in opts)
    ask_amount = any(o["due_amount"] is None for o in opts)
    head = u["company"] if u["group"] == "sigorta" else GROUP_LABEL[u["group"]]
    if which:
        title = f"{head} · " + ("Bu hangi araç?" if cars and len(assets_n) > 1 else "Hangi vadenin ödemesi?")
    else:
        o = opts[0]
        title = f"{o['asset_name']} · {o['title']} ödendi mi?"
    i = u["inst"]
    if i:
        text = (f"{u['company']} {i['m']} taksit, ayda {_tl(i['per'])} (poliçe toplamı {_tl(i['total'])}). Kalan taksitler "
                "kart ödemende zaten var; vadeyi ödendi sayarız, iki kez düşmeyiz.")
    else:
        text = f"{u['description'][:40]} · {_tl(u['amount'])}. Ödendiyse tek dokunuşla işaretle."
    if ask_amount:
        text += " Tutarı da kaydedelim mi?"
    return {"key": u["key"], "txn_id": u["txn_id"], "entry_id": u["entry_id"], "date": u["date"],
            "description": u["description"], "amount": u["amount"], "group": u["group"],
            "group_label": GROUP_LABEL[u["group"]], "company": u["company"], "inst": i, "options": opts,
            "question": "which" if which else "paid", "ask_amount": ask_amount, "title": title, "text": text}


# ---------------------------------------------------------------- yazma

class Forbidden(PermissionError):
    pass


def accept(key: str, due_id: int, save_amount: bool = True, on: date | None = None) -> dict:
    """Öneriyi onayla: seçilen vade(ler) ödendi — tarih ödeme günü (taksitte poliçe başlangıcı), tutar ödeme tutarı
    (taksitte poliçe toplamı; vadenin tutarı yoksa yalnız save_amount ile). Bağlanan işlem / taksit grubu bir daha
    önerilmez. Geri al: POST /api/asset-dues/{id}/unpay."""
    from app import assets

    if not can_answer():
        raise Forbidden("Bu defterde sadece görüntüleme yetkin var")
    s = next((x for x in suggestions(on)["items"] if x["key"] == key), None)
    if not s:
        raise KeyError(key)
    o = next((o for o in s["options"] if o["due_id"] == due_id), None)
    if not o:
        raise KeyError(due_id)
    ids = o["due_ids"]
    amt = s["amount"] / len(ids)
    inst = json.dumps({k: s["inst"][k] for k in ("key", "m", "per", "total", "start")}, sort_keys=True) if s["inst"] else None
    for did in ids:
        keep = o["due_amount"] is not None or save_amount
        assets.pay(did, s["date"], round(amt, 2) if keep else None, on=on)
        with db.connect() as c:
            c.execute("UPDATE asset_dues SET txn_id = ?, entry_id = ?, installment = ? WHERE id = ?",
                      (s["txn_id"], s["entry_id"], inst, did))
    return {"ok": True, "due_ids": ids, "undo": [f"/api/asset-dues/{d}/unpay" for d in ids]}


def dismiss(key: str) -> None:
    """ "Hayır, bu değil": bu ödeme (ya da taksit grubu) bir daha önerilmez."""
    if not can_answer():
        raise Forbidden("Bu defterde sadece görüntüleme yetkin var")
    if not key or len(key) > 200:
        raise ValueError("Öneri anahtarı geçersiz")
    with db.connect() as c:
        keys = sorted(_dismissed(c) | {key})[-500:]
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (DISMISSED, json.dumps(keys)))


def installment_info(raw: str | None, on: date) -> dict | None:
    """Vadeye yazılan taksit bilgisi + bugünkü sıra: {m, per, total, start, n, label}."""
    if not raw:
        return None
    try:
        i = json.loads(raw)
        start = date.fromisoformat(i["start"])
    except (ValueError, KeyError, TypeError):
        return None
    n = max(1, min(int(i["m"]), _mi(on) - _mi(start) + 1))
    done = n >= int(i["m"])
    return {"m": int(i["m"]), "per": i.get("per"), "total": i.get("total"), "start": i["start"], "n": n, "done": done,
            "label": f"{i['m']} taksitle ödendi" if done else f"Taksitle ödeniyor ({n}/{i['m']})"}


def count(on: date | None = None) -> int:
    try:
        s = suggestions(on)
        return s["count"] if s.get("can_answer") else 0
    except Exception:  # noqa: BLE001 - Yapılacaklar / push bozulmasın
        return 0
