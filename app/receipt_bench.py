"""Fiş okuma kıyası: yerel okuma (Tesseract), yapay zekâ (Gemini) ve karma yol aynı fişlerde ne kadar doğru, ne kadara?

    python -m app.receipt_bench --user-id 1 [--user-id 7] [--limit N] [--json] [--no-ai] [--reread]
                                [--text-dir DIR] [--export-photos DIR]
    python -m app.receipt_bench --photo-dir DIR [--text-dir DIR] [--limit N] [--json] [--model M]

Yollar:
    A  yerel: app/ocr.py `parse` (yapay zekâsız; belge sunucudan çıkmaz)
    B  yapay zekâ: app/receipt.py `_parse_document_ai` (bugünkü istem ve GEMINI_MODEL)
    C  karma: önce A; `accept_local` koşullarının hepsi tutarsa A'nın sonucu, tutmazsa B'nin (B ayrıca çağrılmaz, B'nin
       ölçülen sonucu ve maliyeti kullanılır)
    T  metin (--text-dir): telefonda çıkarılmış metin yapay zekâya (app/receipt.py `parse_document_text`), B ile aynı
       ölçütler. T+ uygulamadaki yol: T tutarsızsa (toplam yok, fiyatsız kalem, kalem toplamı tutmuyor) fotoğrafla bir
       kez daha okunur (B'nin ölçülen sonucu ve maliyeti kullanılır); maliyet T + gerekirse B.

Metin dosyaları: DIR/<fiş dosyasının adı, uzantısız>.txt (ör. <sha256>.txt). Fotoğrafları macOS'ta metne çevirmek için
önce `--export-photos DIR` (fotoğraflar DIR'e <ad>.jpg olarak yazılır, kıyas yapılmaz), sonra
`swift scripts/vision_ocr.swift DIR METIN_DIR` (Apple Vision, telefondaki satır birleştirmeyle aynı). İkisi de repo
DIŞINDA bir klasör olmalı; kişisel veri.

Klasör kıyası (--photo-dir): defter yerine bir klasördeki fiş fotoğrafları (macOS'ta önce
`swift scripts/vision_ocr.swift DIR`: her fotoğrafın yanına <ad>.txt). Altın yoktur; her fişte B (fotoğraf → Gemini
görsel) ve T (metin → Gemini metin) okunur, T+ uygulamadaki yoldur. Ölçütler: fiş sayıldı mı, toplam/tarih/işyeri
bulundu mu ve T ile B aynısını mı buldu, fiyatsız kalem, kalem toplamı = fiş toplamı, iki satır belirtisi, token ve
maliyet. Anahtar GEMINI_API_KEY ortam değişkeninden; defter/sistem veritabanı açılmaz.

Gerçek cevap (altın): kayıtlı fişin kaydı — tutar (entries.amount), tarih (entry_date, ±1 gün), işyeri (description,
normalize benzerlik). Kayıtlı fiş kalemleri (receipt_items) eski yapay zekâ çıktısı olduğundan altın SAYILMAZ; kalemler
yapısal ölçütlerle değerlendirilir (`structure`): fiyatsız kalem, kalem toplamı = fiş toplamı, iki satıra bölünmüş ürün
belirtisi (`split_suspects`).

Gizlilik: çıktıda fotoğraf, fiş metni, ad, işyeri ya da tutar YOKTUR; yalnız sıra numarası (#1..), doğru/yanlış
bayrakları, sayılar, token ve süre. Defter salt okunur açılır (şema/göç çalışmaz). Yapay zekâ çağrıları kullanıcının
hakkından/kotasından düşmez, günlük sayaca ve maliyet tablosuna (ai_calls) yazılmaz: `_isolated_ai` bunları kapatır.
"""
import argparse
import contextlib
import difflib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import date

TOL = 0.05  # kalem toplamı / fiş toplamı / altın tutar farkı (TL)
PRICE_IN, PRICE_OUT = 0.25, 1.50  # $ / 1M token (gemini-3.1-flash-lite; düşünme çıktı fiyatıyla)
USD_TRY = 50.0

# /api/documents/parse'taki gider kategorileri (app/api/main.py EXPENSE_CATEGORIES). O modül içe aktarılınca veritabanı
# göçleri çalışır; kıyas salt okunur kalsın diye liste burada.
_EXPENSE_CATEGORIES = ["Altın & Döviz Birikimi", "Kira & Aidat", "Faturalar", "Elektrik", "Doğalgaz", "Su", "İnternet & TV",
                       "Telefon", "Sağlık", "Eğitim", "Ulaşım & Araç", "Market & Gıda", "Kredi & Borç", "Bağış & Sadaka",
                       "Diğer"]


@dataclass
class Case:
    """Bir fiş: fotoğraf (yalnız bellekte) + altın değerler + kayıtlı kalemlerin özeti."""
    image: bytes
    mime: str
    amount: float | None
    on: str | None
    description: str
    linked: bool                      # ekstre/döküm satırıyla eşleşmiş (tutar bankadan doğrulanmış)
    stored_items: list[dict] = field(default_factory=list)  # {amount, price_known}
    categories: list[str] = field(default_factory=list)
    split_categories: list[str] = field(default_factory=list)
    key: str = ""                     # saklı fiş dosyasının adı (metin dosyası eşlemesi: <key uzantısız>.txt)
    text: str | None = None           # T yolu için telefonda/macOS'ta çıkarılmış metin (yalnız bellekte)


# ---------------------------------------------------------------- ölçütler

_SIZE_ONLY = re.compile(r"^[\d.,\s]*(?:(?:G|GR|KG|ML|L|LT|CL|CC|AD|ADT|ADET|X\s*\d+)\.?\s*)*$", re.I)
_JOINERS = "-/&+,("


def continuation_like(name: str) -> bool:
    """Ürün adının devamı gibi görünen satır: 2'den az harf ("SU" gerçek ürün), yalnız gramaj/adet ("500 GR", "X6"), küçük harfle ya da
    bağlaçla ("-", "/", "&", "+", ",", "(") başlıyor."""
    s = (name or "").strip()
    if not s:
        return True
    letters = sum(ch.isalpha() for ch in s)
    return letters < 2 or bool(_SIZE_ONLY.fullmatch(s)) or s[0].islower() or s[0] in _JOINERS


def split_suspects(items: list[dict]) -> int:
    """İki satıra bölünmüş ürün belirtisi sayısı. Ardışık (önceki, sonraki) her kalem çifti için belirti:
    (a) ikisinden birinin fiyatı yok (fiyatsız kalem = komşusunun adının parçası), ya da
    (b) sonrakinin adı devam gibi (`continuation_like`), ya da önceki ad bağlaçla bitiyor ("-", "/", "&", "+", ",").
    Her sonraki kalem en çok bir kez sayılır. Tek kalemli fişte fiyatsız kalem de bir belirtidir."""
    n = 0
    if len(items) == 1 and items[0].get("amount") is None:
        return 1
    for prev, cur in zip(items, items[1:]):
        p, c = (prev.get("name") or "").strip(), (cur.get("name") or "").strip()
        if prev.get("amount") is None or cur.get("amount") is None or continuation_like(c) or (p and p[-1] in _JOINERS):
            n += 1
    return n


def structure(items: list[dict], total: float | None, discounts: list[float] | None = None) -> dict:
    """Kalemlerin yapısal kalitesi. discounts: kalem listesine girmemiş indirim satırları (negatif)."""
    priceless = sum(1 for it in items if it.get("amount") is None)
    s = sum(it["amount"] for it in items if isinstance(it.get("amount"), (int, float))) + sum(discounts or [])
    sum_ok = bool(items) and total is not None and abs(total - s) <= TOL
    splits = split_suspects(items)
    return {"n_items": len(items), "priceless": priceless, "sum_ok": sum_ok, "splits": splits,
            "ok": bool(items) and priceless == 0 and sum_ok and splits == 0}


# işyeri karşılaştırmasında anlamsız (her fişte geçen) kelimeler
_GENERIC = {"SAN", "SANAYI", "VE", "TIC", "TICARET", "LTD", "STI", "SIRKETI", "AS", "A", "ANONIM", "LIMITED", "GIDA",
            "MAGAZA", "MAGAZACILIK", "MAGAZALARI", "PAZARLAMA", "PAZ", "IC", "DIS", "ITH", "IHR", "HIZ", "HIZMETLERI",
            "INS", "TUR", "TURIZM", "SUBE", "SUBESI", "SB", "MAH", "CAD", "SK", "NO"}


def _mkey(name: str | None) -> str:
    from app.textnorm import normalize

    words = re.sub(r"[^A-Z0-9 ]", " ", normalize(name or "")).split()
    return " ".join(w for w in words if w not in _GENERIC)


def merchant_ok(predicted: list[str | None], gold: str | None) -> bool:
    """Tahmin edilen ad(lar)dan biri kaydın açıklamasına benziyor mu: biri ötekini içeriyor, en az 4 harfli ortak kelime
    var ya da benzerlik ≥ 0,6 (normalize: büyük harf, Türkçe karakter ve şirket ekleri atılmış)."""
    b = _mkey(gold)
    if not b:
        return False
    for p in predicted:
        if not p or p.strip() in ("Fiş", "Fatura"):
            continue
        a = _mkey(p)
        if not a:
            continue
        if a in b or b in a or any(len(w) >= 4 and w in a.split() for w in b.split()) or \
                difflib.SequenceMatcher(None, a, b).ratio() >= 0.6:
            return True
    return False


def _date_ok(pred: str | None, gold: str | None) -> bool:
    try:
        return bool(pred and gold) and abs((date.fromisoformat(pred) - date.fromisoformat(gold[:10])).days) <= 1
    except ValueError:
        return False


def evaluate(doc: dict | None, case: Case, discounts: list[float] | None = None) -> dict:
    """Bir yolun sonucunu altına ve yapısal ölçütlere göre değerlendir (yalnız bayrak ve sayı döner)."""
    if not doc or doc.get("type") != "receipt":
        return {"total_ok": False, "date_ok": False, "merchant_ok": False,
                **structure([], None), "found": {"total": False, "date": False, "merchant": False}}
    total = doc.get("total")
    gold = abs(case.amount) if case.amount is not None else None
    return {
        "total_ok": total is not None and gold is not None and abs(total - gold) <= TOL,
        "date_ok": _date_ok(doc.get("date"), case.on),
        "merchant_ok": merchant_ok([doc.get("merchant"), doc.get("legal_name")], case.description),
        **structure(doc.get("items") or [], total, discounts),
        "found": {"total": total is not None, "date": bool(doc.get("date")),
                  "merchant": bool(doc.get("merchant")) and doc.get("merchant") != "Fiş"},
    }


def accept_local(doc: dict | None, discounts: list[float] | None = None) -> tuple[bool, list[str]]:
    """Karma yol: yerel sonuç şu koşulların HEPSİ tutarsa kabul — toplam, tarih ve işyeri bulundu, en az bir kalem,
    her kalemin fiyatı var, kalemler (+ indirim satırları) fiş toplamını ±0,05 tutuyor. Tutmayan koşulların kodları döner."""
    if not doc or doc.get("type") != "receipt":
        return False, ["tur"]
    why = []
    items, total = doc.get("items") or [], doc.get("total")
    if total is None:
        why.append("toplam_yok")
    if not doc.get("date"):
        why.append("tarih_yok")
    if not doc.get("merchant") or doc.get("merchant") == "Fiş":
        why.append("isyeri_yok")
    if not items:
        why.append("kalem_yok")
    if any(it.get("amount") is None for it in items):
        why.append("fiyatsiz_kalem")
    if items and total is not None and not structure(items, total, discounts)["sum_ok"]:
        why.append("toplam_tutmuyor")
    return not why, why


def cost_usd(usage: dict | None, price_in: float = PRICE_IN, price_out: float = PRICE_OUT) -> float:
    u = usage or {}
    return ((u.get("promptTokenCount") or 0) * price_in
            + ((u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0)) * price_out) / 1e6


# ---------------------------------------------------------------- okuyucular

_DISCOUNT = re.compile(r"[İI]ND[İI]R[İI]M|KAMPANYA|PROMOSYON")


def discounts_of(text: str) -> list[float]:
    """Yerel okuma indirim satırlarını kalem saymaz; toplam denetimi için negatif tutarları (toplam satırları hariç)."""
    from app import ocr

    out = []
    for line in text.splitlines():
        u = ocr._up(line)
        if _DISCOUNT.search(u) and "TOPLAM" not in u:
            vals = [ocr._num(x) for x in re.findall(ocr.AMOUNT, line)]
            vals = [v for v in vals if v]
            if vals:
                out.append(-abs(vals[-1]))
    return out


def local_read(case: Case) -> tuple[dict, list[float]]:
    """A: Tesseract bir kez çalışır; metin indirim satırları için de kullanılır (metin dışarı verilmez)."""
    from app import ocr

    text = ocr.text_of(case.image, case.mime)
    real = ocr.text_of
    ocr.text_of = lambda data, mime: text  # parse aynı metni yeniden OCR'lamasın
    try:
        doc = ocr.parse(case.image, case.mime, case.categories)
    finally:
        ocr.text_of = real
    return doc, discounts_of(text)


@contextlib.contextmanager
def _isolated_ai():
    """Yapay zekâ çağrısının yan etkilerini kapat (kota, günlük sayaç, kullanım sayacı, maliyet kaydı) ve ham yanıttaki
    usageMetadata'yı yakala (eski sürümlerde _gemini_json düşünme token'ını döndürmüyor)."""
    import httpx

    from app import receipt

    seen: list[dict] = []
    real_post = httpx.post

    def post(*a, **kw):
        r = real_post(*a, **kw)
        try:
            seen.append(dict(r.json().get("usageMetadata") or {}) if r.status_code == 200 else {})
        except ValueError:
            seen.append({})
        return r

    names = ("_check_quota", "_daily_gate", "_count_usage", "_record_cost")
    saved = {n: getattr(receipt, n) for n in names if hasattr(receipt, n)}
    try:
        for n in saved:
            setattr(receipt, n, lambda *a, **k: None)
        httpx.post = post
        yield seen
    finally:
        httpx.post = real_post
        for n, f in saved.items():
            setattr(receipt, n, f)


def ai_read(case: Case) -> tuple[dict, dict]:
    """B: bugünkü yapay zekâ okuması (tek çağrı). (sonuç, usageMetadata)."""
    from app import receipt

    with _isolated_ai() as seen:
        doc = receipt._parse_document_ai(case.image, case.mime, case.categories, case.split_categories or None)
    return doc, (seen[-1] if seen else {})


def ai_reread(case: Case, total: float | None) -> tuple[dict, dict]:
    """B+: uygulamadaki otomatik yeniden okuma (kalemler tutmayınca güçlü modelle). (sonuç, usageMetadata)."""
    from app import receipt

    with _isolated_ai() as seen:
        doc = receipt.reread_items(case.image, case.mime, total)
    return doc, (seen[-1] if seen else {})


def ai_text_read(case: Case) -> tuple[dict, dict]:
    """T: telefonda çıkarılmış metinden okuma (uygulamanın metin yolu). (sonuç, usageMetadata)."""
    from app import receipt

    with _isolated_ai() as seen:
        doc = receipt.parse_document_text(case.text or "", case.categories, case.split_categories or None)
    return doc, (seen[-1] if seen else {})


def text_inconsistent(doc: dict | None) -> bool:
    """Uygulamadaki metin → fotoğraf yedeğinin tetiği (app/api/main.py `_read_by_text`)."""
    from app import receipt_check

    if not doc:
        return True
    if doc.get("type") != "receipt":
        return False
    items = doc.get("items") or []
    return doc.get("total") is None or receipt_check.needs_reread(items, receipt_check.check(items, doc.get("total")))


def _tokens(usage: dict) -> dict:
    return {"input": usage.get("promptTokenCount"), "output": usage.get("candidatesTokenCount"),
            "thoughts": usage.get("thoughtsTokenCount")}


# ---------------------------------------------------------------- kıyas

def _timed(fn, *a):
    t = time.perf_counter()
    try:
        return fn(*a), None, time.perf_counter() - t
    except Exception as e:  # noqa: BLE001 - yalnız hata türü raporlanır (ileti belge içeriği taşıyabilir)
        return None, type(e).__name__, time.perf_counter() - t


def bench_case(case: Case, local=local_read, ai=ai_read, reread=None, prices=(PRICE_IN, PRICE_OUT),
               text_ai=ai_text_read) -> dict:
    """Tek fiş, üç (metin varsa beş) yol. local/ai/reread/text_ai: test için değiştirilebilir okuyucular; ai None ise B,
    C ve T'nin yapay zekâ ayağı yok. T yalnız case.text varsa."""
    from app import receipt_check

    row: dict = {"linked": case.linked}
    res, err, sec = _timed(local, case)
    a_doc, disc = res if res else (None, [])
    row["A"] = {**evaluate(a_doc, case, disc), "sec": round(sec, 2), "error": err}
    ok, why = accept_local(a_doc, disc)
    b_doc = None
    if ai:
        res, err, sec = _timed(ai, case)
        b_doc, usage = res if res else (None, {})
        row["B"] = {**evaluate(b_doc, case), "sec": round(sec, 2), "error": err,
                    "tokens": {"input": usage.get("promptTokenCount"), "output": usage.get("candidatesTokenCount"),
                               "thoughts": usage.get("thoughtsTokenCount")},
                    "usd": cost_usd(usage, *prices)}
        if reread and b_doc and b_doc.get("type") == "receipt" and b_doc.get("items") and \
                receipt_check.needs_reread(b_doc["items"], receipt_check.check(b_doc["items"], b_doc.get("total"))):  # uygulamadaki tetik
            res, err, sec = _timed(reread, case, b_doc.get("total"))
            r_doc, r_usage = res if res else (None, {})
            merged = {**b_doc, "items": r_doc["items"]} if r_doc else b_doc
            row["B+"] = {**evaluate(merged, case), "sec": round(sec, 2), "error": err, "tried": True,
                         "tokens": {"input": r_usage.get("promptTokenCount"),
                                    "output": r_usage.get("candidatesTokenCount"),
                                    "thoughts": r_usage.get("thoughtsTokenCount")}}
    if ai and text_ai and case.text:
        res, err, sec = _timed(text_ai, case)
        t_doc, t_usage = res if res else (None, {})
        row["T"] = {**evaluate(t_doc, case), "sec": round(sec, 2), "error": err, "tokens": _tokens(t_usage),
                    "usd": cost_usd(t_usage, *prices)}
        fb = text_inconsistent(t_doc)
        b = row.get("B") or {}
        use_b = fb and b_doc is not None
        plus = {k: v for k, v in (b if use_b else row["T"]).items() if k not in ("tokens", "usd", "sec", "error")}
        row["T+"] = {**plus, "fallback": fb, "error": row["T"]["error"] or (b.get("error") if fb else None),
                     "usd": row["T"]["usd"] + (b.get("usd", 0.0) if fb else 0.0),
                     "sec": round(row["T"]["sec"] + (b.get("sec", 0) if fb else 0), 2)}
    c_ev = row["A"] if ok else row.get("B")
    row["C"] = {**(c_ev or evaluate(None, case)), "accepted_local": ok, "reasons": why,
                "usd": 0.0 if ok else (row.get("B") or {}).get("usd", 0.0),
                "sec": row["A"]["sec"] + (0 if ok else (row.get("B") or {}).get("sec", 0))}
    # yanlış kabul: yerel sonuç kabul edildi ama toplam ya da tarih yanlış (işyeri ayrıca)
    row["C"]["wrong_accept"] = ok and not (row["A"]["total_ok"] and row["A"]["date_ok"])
    row["C"]["wrong_accept_merchant"] = ok and not row["A"]["merchant_ok"]
    st = case.stored_items
    row["stored"] = {"n_items": len(st), "priceless": sum(1 for x in st if not x.get("price_known")),
                     "diff_A": (row["A"]["n_items"] - len(st)) if a_doc else None,
                     "diff_B": (row["B"]["n_items"] - len(st)) if row.get("B") and b_doc else None}
    return row


def summarize(rows: list[dict], usd_try: float = USD_TRY) -> dict:
    n = len(rows)

    def pct(xs):
        return round(100 * sum(1 for x in xs if x) / len(xs), 1) if xs else None

    out: dict = {"n": n, "linked": sum(r["linked"] for r in rows), "usd_try": usd_try}
    for path in ("A", "B", "B+", "C", "T", "T+"):
        rs = [r[path] for r in rows if path in r]
        if not rs:
            continue
        s = {"n": len(rs), "total_ok_pct": pct([x["total_ok"] for x in rs]), "date_ok_pct": pct([x["date_ok"] for x in rs]),
             "merchant_ok_pct": pct([x["merchant_ok"] for x in rs]), "structure_ok_pct": pct([x["ok"] for x in rs]),
             "sum_ok_pct": pct([x["sum_ok"] for x in rs]), "priceless": sum(x["priceless"] for x in rs),
             "splits": sum(x["splits"] for x in rs), "receipts_with_splits": sum(1 for x in rs if x["splits"]),
             "avg_sec": round(sum(x["sec"] for x in rs) / len(rs), 2),
             "errors": sum(1 for x in rs if x.get("error"))}
        if path in ("B", "C", "T", "T+"):
            usd = sum(x.get("usd") or 0 for x in rs)
            s.update({"usd_total": round(usd, 6), "usd_per_receipt": round(usd / len(rs), 6),
                      "try_per_receipt": round(usd / len(rs) * usd_try, 4)})
        if path in ("B", "B+", "T"):
            for k in ("input", "output", "thoughts"):
                vals = [x["tokens"][k] or 0 for x in rs]
                s[f"avg_{k}"] = round(sum(vals) / len(vals))
        if path == "T+":
            s["fallback"] = sum(1 for x in rs if x["fallback"])
        if path == "C":
            acc = [x for x in rs if x["accepted_local"]]
            s.update({"accepted_local": len(acc), "sent_to_ai": len(rs) - len(acc),
                      "wrong_accept": sum(x["wrong_accept"] for x in rs),
                      "wrong_accept_merchant": sum(x["wrong_accept_merchant"] for x in rs),
                      "reasons": _count([w for x in rs for w in x["reasons"]])})
        out[path] = s
    st = [r["stored"] for r in rows]
    out["stored"] = {"n_items": sum(x["n_items"] for x in st), "priceless": sum(x["priceless"] for x in st)}
    return out


def _count(xs: list[str]) -> dict:
    d: dict = {}
    for x in xs:
        d[x] = d.get(x, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------- veri (salt okunur)

@contextlib.contextmanager
def _space_of(user_id: int):
    """Kullanıcının defter alanı, şema/göç çalıştırmadan; bağlamda kullanıcı yok (sayaçlar kimseye yazılmaz)."""
    from app import db, system

    u = system.user(user_id)
    if not u:
        raise SystemExit(f"kullanıcı yok: {user_id}")
    path = system.user_db_path(u)
    if path:
        db._ready_dbs.add(os.path.abspath(path))  # init_db (göç) atlanır: canlı deftere yazılmaz
    with db.use_space(path, None):
        yield


def load_cases(user_id: int, limit: int | None = None) -> list[Case]:
    """Fotoğrafı saklı ve kalemi olan kayıtlar (kayıt sırasıyla). Fotoğraflar yalnız bellekte tutulur."""
    from app import categorize, db, storage

    out = []
    with _space_of(user_id):
        con = sqlite3.connect(f"file:{db.db_path()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            matched = "(SELECT COUNT(*) FROM entry_matches m WHERE m.entry_id = e.id)" if "entry_matches" in tables else "0"
            q = ("SELECT e.id, e.entry_date, e.amount, e.description, e.receipt_file, e.linked_txn_id, "
                 f"{matched} AS matched FROM entries e WHERE e.receipt_file IS NOT NULL AND e.receipt_file <> '' "
                 "AND EXISTS (SELECT 1 FROM receipt_items r WHERE r.entry_id = e.id) ORDER BY e.id")
            entries = [dict(r) for r in con.execute(q + (" LIMIT ?" if limit else ""), (limit,) if limit else ())]
            cols = {r[1] for r in con.execute("PRAGMA table_info(receipt_items)")}
            pk = "price_known" if "price_known" in cols else "1"
            cats = sorted(set(categorize.CATEGORIES) | set(_EXPENSE_CATEGORIES)
                          | {r[0] for r in con.execute("SELECT DISTINCT category FROM entries WHERE category IS NOT NULL")}
                          - {"Ödeme", "Kategorisiz"})
            split_cats = sorted({r[0] for r in con.execute("SELECT DISTINCT category FROM item_rules")}) \
                if "item_rules" in tables else []
            for e in entries:
                items = [{"amount": r[0], "price_known": r[1]} for r in
                         con.execute(f"SELECT amount, {pk} FROM receipt_items WHERE entry_id = ? ORDER BY id", (e["id"],))]
                data = storage.book_get("receipts", e["receipt_file"]) if storage.valid_name("receipts", e["receipt_file"]) else None
                if not data:
                    continue
                out.append(Case(image=data, mime=storage.content_type(e["receipt_file"]), amount=e["amount"],
                                on=e["entry_date"], description=e["description"] or "",
                                linked=bool(e["linked_txn_id"] or e["matched"]), stored_items=items,
                                categories=cats, split_categories=split_cats, key=e["receipt_file"]))
        finally:
            con.close()
    return out


def text_for(case: Case, text_dir: str) -> str | None:
    """case.key'e karşılık gelen metin dosyası (yoksa None: T yolu o fişte çalışmaz)."""
    if not case.key:
        return None
    path = os.path.join(text_dir, os.path.splitext(os.path.basename(case.key))[0] + ".txt")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def export_photos(user_ids: list[int], out_dir: str, limit: int | None = None) -> int:
    """Kıyastaki fişlerin fotoğraflarını out_dir'e yaz (<fiş dosyası adı>); yalnız sayı yazdırılır."""
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out = os.path.abspath(out_dir)
    if out == here or out.startswith(here + os.sep):
        raise SystemExit("fotoğraflar repo dışındaki bir klasöre yazılmalı (kişisel veri)")
    os.makedirs(out, exist_ok=True)
    n = 0
    for uid in user_ids:
        for case in load_cases(uid, limit):
            with open(os.path.join(out, os.path.basename(case.key)), "wb") as f:
                f.write(case.image)
            n += 1
    print(f"{n} fotoğraf yazıldı", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- klasör kıyası: fotoğraf yolu (B) ↔ metin yolu (T)

PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
               ".heic": "image/heic"}


def iter_dir_cases(photo_dir: str, text_dir: str | None = None, limit: int | None = None):
    """Klasördeki fiş fotoğrafları (ad sırasıyla; uygulamadaki gibi küçültülüp EXIF'ten arındırılır, açılamayan HEIC
    özgün haliyle) ve <ad uzantısız>.txt metinleri (varsayılan: fotoğrafın yanında). Altın değer yoktur; yollar
    birbiriyle kıyaslanır. Fotoğraflar tek tek okunur (hepsi birden belleğe alınmaz)."""
    from app import categorize, images

    cats = sorted(set(categorize.CATEGORIES) | set(_EXPENSE_CATEGORIES) - {"Ödeme", "Kategorisiz"})
    n = 0
    for name in sorted(os.listdir(photo_dir)):
        mime = PHOTO_TYPES.get(os.path.splitext(name)[1].lower())
        if not mime or name.startswith("."):
            continue
        with open(os.path.join(photo_dir, name), "rb") as f:
            data = f.read()
        small = images.prepare(data)
        if small is not None:
            data, mime = small, "image/jpeg"
        case = Case(image=data, mime=mime, amount=None, on=None, description="", linked=False, categories=cats, key=name)
        case.text = text_for(case, text_dir or photo_dir)
        yield case
        n += 1
        if limit and n >= limit:
            return


def _side(doc: dict | None, usage: dict | None, sec: float, err: str | None, prices=(PRICE_IN, PRICE_OUT)) -> dict:
    """Bir yolun altınsız ölçütleri: fiş mi, toplam/tarih/işyeri bulundu mu, yapı (fiyatsız kalem, kalem toplamı, iki
    satır belirtisi), süre, token, maliyet."""
    rec = bool(doc) and doc.get("type") == "receipt"
    total = doc.get("total") if rec else None
    return {"receipt": rec, **structure((doc.get("items") or []) if rec else [], total),
            "found": {"total": total is not None, "date": rec and bool(doc.get("date")),
                      "merchant": rec and bool(doc.get("merchant")) and doc.get("merchant") != "Fiş"},
            "sec": round(sec, 2), "error": err, "tokens": _tokens(usage or {}), "usd": cost_usd(usage, *prices)}


def agreement(a: dict | None, b: dict | None) -> dict:
    """İki yolun sonucu aynı fişi mi anlatıyor (altın yokken): toplam ±TOL, tarih ±1 gün, işyeri benzer."""
    if not (a and b and a.get("type") == "receipt" and b.get("type") == "receipt"):
        return {"total": False, "date": False, "merchant": False}
    ta, tb = a.get("total"), b.get("total")
    return {"total": ta is not None and tb is not None and abs(ta - tb) <= TOL,
            "date": _date_ok(a.get("date"), b.get("date")),
            "merchant": merchant_ok([a.get("merchant"), a.get("legal_name")], b.get("merchant") or b.get("legal_name"))}


def bench_pair(case: Case, ai=ai_read, text_ai=ai_text_read, prices=(PRICE_IN, PRICE_OUT)) -> dict:
    """Tek fiş: B (fotoğraf → yapay zekâ), metin varsa T (metin → yapay zekâ) ve T+ (uygulamadaki yol: T tutarsızsa B'nin
    sonucu, maliyet T + B). agree: T ile B aynı toplam/tarih/işyerini mi buldu."""
    res, err, sec = _timed(ai, case)
    b_doc, b_usage = res if res else (None, {})
    row: dict = {"has_text": bool(case.text), "B": _side(b_doc, b_usage, sec, err, prices)}
    if not case.text:
        return row
    res, err, sec = _timed(text_ai, case)
    t_doc, t_usage = res if res else (None, {})
    row["T"] = t = _side(t_doc, t_usage, sec, err, prices)
    fb = text_inconsistent(t_doc)
    final = b_doc if fb and b_doc is not None else t_doc
    plus = _side(final, {}, 0, None, prices)
    row["T+"] = {**plus, "fallback": fb, "tokens": None, "usd": t["usd"] + (row["B"]["usd"] if fb else 0.0),
                 "sec": round(t["sec"] + (row["B"]["sec"] if fb else 0), 2),
                 "error": t["error"] if not fb else (row["B"]["error"] if b_doc is None else None)}
    row["agree"] = agreement(t_doc, b_doc)
    return row


def summarize_pair(rows: list[dict], usd_try: float = USD_TRY) -> dict:
    """Klasör kıyasının özeti: yalnız sayılar ve oranlar."""
    def pct(xs):
        return round(100 * sum(1 for x in xs if x) / len(xs), 1) if xs else None

    out: dict = {"n": len(rows), "with_text": sum(r["has_text"] for r in rows), "usd_try": usd_try}
    for path in ("B", "T", "T+"):
        rs = [r[path] for r in rows if path in r]
        if not rs:
            continue
        usd = sum(x["usd"] or 0 for x in rs)
        s = {"n": len(rs), "receipt_pct": pct([x["receipt"] for x in rs]),
             "total_found_pct": pct([x["found"]["total"] for x in rs]),
             "date_found_pct": pct([x["found"]["date"] for x in rs]),
             "merchant_found_pct": pct([x["found"]["merchant"] for x in rs]),
             "structure_ok_pct": pct([x["ok"] for x in rs]), "sum_ok_pct": pct([x["sum_ok"] for x in rs]),
             "priceless": sum(x["priceless"] for x in rs), "receipts_with_priceless": sum(1 for x in rs if x["priceless"]),
             "splits": sum(x["splits"] for x in rs), "receipts_with_splits": sum(1 for x in rs if x["splits"]),
             "avg_sec": round(sum(x["sec"] for x in rs) / len(rs), 2), "errors": sum(1 for x in rs if x.get("error")),
             "usd_total": round(usd, 6), "usd_per_receipt": round(usd / len(rs), 6),
             "try_per_receipt": round(usd / len(rs) * usd_try, 4)}
        if path != "T+":
            for k in ("input", "output", "thoughts"):
                s[f"avg_{k}"] = round(sum(x["tokens"][k] or 0 for x in rs) / len(rs))
        else:
            s["fallback"] = sum(1 for x in rs if x["fallback"])
        out[path] = s
    ag = [r["agree"] for r in rows if "agree" in r]
    if ag:
        out["T_vs_B"] = {"n": len(ag), "total_same_pct": pct([x["total"] for x in ag]),
                         "date_same_pct": pct([x["date"] for x in ag]),
                         "merchant_same_pct": pct([x["merchant"] for x in ag])}
    if "B" in out and "T+" in out and out["B"]["usd_per_receipt"]:
        out["T+_cost_vs_B_pct"] = round(100 * out["T+"]["usd_per_receipt"] / out["B"]["usd_per_receipt"], 1)
    return out


def render_pair(rows: list[dict], summary: dict) -> str:
    def side(x: dict) -> str:
        tk = x.get("tokens") or {}
        return (f"{_b(x['found']['total'])} {_b(x['found']['date'])} {_b(x['found']['merchant'])} {x['n_items']:>3} "
                f"{x['priceless']:>3} {x['splits']:>3} {_b(x['sum_ok'])} {_b(x['ok'])} {x['sec']:>5} "
                f"{tk.get('input') or 0:>5} {tk.get('output') or 0:>5} {x['usd']:.5f}" + (f" !{x['error']}" if x.get("error") else ""))

    lines = ["fiş  | B foto: T D İ kalem fyz bölün Σ yapı sn girdi çıktı $ | T metin: (aynı) | aynı T D İ | yedek"]
    for i, r in enumerate(rows, 1):
        line = f"#{i:<3} | {side(r['B'])}"
        if "T" in r:
            ag = r["agree"]
            line += (f" | {side(r['T'])} | {_b(ag['total'])} {_b(ag['date'])} {_b(ag['merchant'])}"
                     + (" | →foto" if r["T+"]["fallback"] else ""))
        else:
            line += " | metin yok"
        lines.append(line)
    lines.append("")
    lines.append(json.dumps(summary, ensure_ascii=False, indent=1))
    return "\n".join(lines)


def run_dir(photo_dir: str, text_dir: str | None, limit: int | None, model: str | None, prices, usd_try: float,
            as_json: bool) -> int:
    """--photo-dir: fotoğraf ve metin yolunu klasördeki fişlerde kıyasla. Anahtar ortamdan (GEMINI_API_KEY); defter ve
    sistem veritabanı açılmaz."""
    from app import receipt

    if not os.path.isdir(photo_dir):
        raise SystemExit(f"klasör yok: {photo_dir}")
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY ortam değişkeni gerekli (klasör kıyası yapay zekâyla okur)")
    os.environ["GEMINI_MODEL"] = model or os.environ.get("GEMINI_MODEL") or receipt.DEFAULT_MODEL
    rows = []
    for case in iter_dir_cases(photo_dir, text_dir, limit):
        rows.append(bench_pair(case, prices=prices))
        case.image, case.text = b"", None
        print(f"… {len(rows)} fiş", file=sys.stderr)
    if not rows:
        raise SystemExit("klasörde fotoğraf yok (jpg, jpeg, png, webp, heic)")
    summary = summarize_pair(rows, usd_try)
    print(json.dumps({"rows": rows, "summary": summary}, ensure_ascii=False) if as_json else render_pair(rows, summary))
    return 0


# ---------------------------------------------------------------- çıktı

def _b(x) -> str:
    return "✓" if x else "·"


def render(rows: list[dict], summary: dict) -> str:
    lines = ["fiş  bağlı | A: T D İ kalem fyz bölün yapı sn | B: T D İ kalem fyz bölün yapı sn girdi çıktı düşün $ | "
             "C: yerel nedenler"]
    for i, r in enumerate(rows, 1):
        a, b, c = r["A"], r.get("B") or {}, r["C"]
        bt = b.get("tokens") or {}
        lines.append(
            f"#{i:<3} {_b(r['linked']):^5} | {_b(a['total_ok'])} {_b(a['date_ok'])} {_b(a['merchant_ok'])} "
            f"{a['n_items']:>3} {a['priceless']:>3} {a['splits']:>3} {_b(a['ok'])} {a['sec']:>5} | "
            + (f"{_b(b['total_ok'])} {_b(b['date_ok'])} {_b(b['merchant_ok'])} {b['n_items']:>3} {b['priceless']:>3} "
               f"{b['splits']:>3} {_b(b['ok'])} {b['sec']:>5} {bt.get('input') or 0:>5} {bt.get('output') or 0:>5} "
               f"{bt.get('thoughts') or 0:>5} {b['usd']:.5f}" if b else "-")
            + f" | {'KABUL' if c['accepted_local'] else 'yz'}{' YANLIŞ' if c['wrong_accept'] else ''} "
              f"{','.join(c['reasons'])}" + (f" A:{a['error']}" if a.get("error") else "")
            + (f" B:{b['error']}" if b.get("error") else "")
            + (f" | T: {_b(r['T']['total_ok'])} {_b(r['T']['date_ok'])} {_b(r['T']['merchant_ok'])} "
               f"{r['T']['n_items']:>3} {r['T']['priceless']:>3} {r['T']['splits']:>3} {_b(r['T']['ok'])} "
               f"{r['T']['usd']:.5f}{' →foto' if r['T+']['fallback'] else ''}"
               + (f" T:{r['T']['error']}" if r["T"].get("error") else "") if "T" in r else ""))
    lines.append("")
    lines.append(json.dumps(summary, ensure_ascii=False, indent=1))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--user-id", type=int, action="append", help="birden çok kez verilebilir")
    p.add_argument("--photo-dir", metavar="DIR",
                   help="defter yerine klasördeki fiş fotoğrafları: fotoğraf (B) ↔ metin (T) yolu, metinler yanında <ad>.txt")
    p.add_argument("--model", help="--photo-dir: Gemini modeli (varsayılan GEMINI_MODEL ya da uygulamanınki)")
    p.add_argument("--limit", type=int, help="kullanıcı başı en çok fiş")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-ai", action="store_true", help="yalnız yerel okuma (B ve C'nin yapay zekâ ayağı yok)")
    p.add_argument("--reread", action="store_true", help="B kalemleri tutmazsa uygulamadaki gibi güçlü modelle yeniden oku")
    p.add_argument("--text-dir", help="telefonda/macOS'ta çıkarılmış metinler: <fiş dosyası adı uzantısız>.txt (T, T+ yolları)")
    p.add_argument("--export-photos", metavar="DIR",
                   help="kıyas yapma; fotoğrafları DIR'e <ad> olarak yaz (scripts/vision_ocr.swift girdisi; repo dışı)")
    p.add_argument("--price-in", type=float, default=PRICE_IN)
    p.add_argument("--price-out", type=float, default=PRICE_OUT)
    p.add_argument("--usd-try", type=float, default=USD_TRY)
    a = p.parse_args(argv)
    from app import receipt

    if a.photo_dir:
        return run_dir(a.photo_dir, a.text_dir, a.limit, a.model, (a.price_in, a.price_out), a.usd_try, a.json)
    if not a.user_id:
        p.error("--user-id ya da --photo-dir gerekli")
    if a.export_photos:
        return export_photos(a.user_id, a.export_photos, a.limit)
    ai = None if a.no_ai or not receipt.enabled() else ai_read
    rows = []
    for uid in a.user_id:
        for case in load_cases(uid, a.limit):
            if a.text_dir:
                case.text = text_for(case, a.text_dir)
            rows.append(bench_case(case, ai=ai, reread=ai_reread if (a.reread and ai) else None,
                                   prices=(a.price_in, a.price_out)))
            case.image, case.text = b"", None  # fotoğraf ve metin bellekte de tutulmasın
            print(f"… {len(rows)} fiş", file=sys.stderr)
    summary = summarize(rows, a.usd_try)
    print(json.dumps({"rows": rows, "summary": summary}, ensure_ascii=False) if a.json else render(rows, summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
