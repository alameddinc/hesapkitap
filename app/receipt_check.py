"""Fiş kalemlerinin denetimi: kalemler fiş toplamını tutuyor mu, adet × birim fiyat satır tutarını tutuyor mu, fiyatlar
bir satır kaymış mı? /api/receipts/parse, /api/documents/parse ve kayıtlı fişin kalemleri bunu döndürür; istemci tutmayan
kalemi işaretler ve önerilen kaydırmayı tek dokunuşla uygulatır (sonra PUT /api/entries/{id}/receipt-items).

Çıktı:
    {"items_total", "receipt_total", "diff" (fiş − kalemler), "ok",
     "items": [{"index", "expected" (adet × birim, bilinmiyorsa null), "mismatch"}],
     "shift_start" (art arda tutmayan kalemlerin başladığı sıra, yoksa null),
     "suggested_shift": null | {"from_index", "direction": "up"|"down", "amounts": [...], "items_total", "diff"},
     "residual" (öneriler uygulanınca kalan fark, TL), "digit_fix": [{"index", "name", "from", "to", "digits": "6→8"[, "unit_price_from", "unit_price_to"][, "evidence":
                   "ocr"|"qty"]}], "digit_fix_all": null | {"indices": [...], "total_ok"}, "suspect": [sıra, ...]}
direction: "down" = from_index'ten itibaren her fiyat bir alt satıra iner (o satıra boşluk: adet × birim ile dolar);
"up" = her fiyat bir üst satıra çıkar.
digit_fix / digit_fix_all / suspect: termal fişte karışan rakam (6↔8 …) düzeltilince kalemler fiş toplamını kuruşu kuruşuna
tutuyor mu (digit_check; telefon metni okuma anında tanık olur). Hiçbiri kendiliğinden uygulanmaz.

Yuvarlama app/money.py ile (yarım yukarı); web karşılığı (web/src/components/receipt/check.ts) aynı kuralı uygular.
"""
import re

from app import money

TOL = 0.02
SUM_TOL = 0.05  # yeniden okuma kararında kalem toplamı ile fiş toplamı arasındaki pay (TL)


def _expected(it: dict) -> float | None:
    up, q = it.get("unit_price"), it.get("qty") or 1
    return money.money(q * up) if isinstance(up, (int, float)) and up > 0 else None


def _mismatches(items: list[dict], amounts: list) -> list[bool]:
    out = []
    for it, a in zip(items, amounts):
        e = _expected(it)
        out.append(e is not None and (a is None or abs(a - e) > TOL))
    return out


def _fill(items: list[dict], amounts: list) -> list:
    """Boş kalan fiyatı (kaydırmada açılan yuva) adet × birimle doldur."""
    return [a if a is not None else _expected(it) for it, a in zip(items, amounts)]


def _total(amounts: list) -> float:
    return money.money(sum(a for a in amounts if isinstance(a, (int, float))))


def check(items: list[dict], receipt_total: float | None) -> dict:
    amounts = [it.get("amount") for it in items]
    mis = _mismatches(items, amounts)
    items_total = _total(amounts)
    diff = money.money(receipt_total - items_total) if receipt_total is not None else None
    shift_start = next((i for i, m in enumerate(mis) if m), None)
    suggested = None
    if shift_start is not None:  # kaydırma ancak adet × birim tutmayan kalem varsa önerilir (toplam farkı tek başına yetmez)
        base = (sum(mis), abs(diff) if diff is not None else 0.0)
        best = None
        for k in range(shift_start + 1):
            for direction in ("down", "up"):
                tail = amounts[k:]
                moved = [None, *tail[:-1]] if direction == "down" else [*tail[1:], None]
                cand = _fill(items, amounts[:k] + moved)
                score = (sum(_mismatches(items, cand)), abs(receipt_total - _total(cand)) if receipt_total is not None else 0.0)
                if score < base and (best is None or score < best[0]):
                    best = (score, k, direction, cand)
        if best:
            _, k, direction, cand = best
            suggested = {"from_index": k, "direction": direction, "amounts": cand, "items_total": _total(cand),
                         "diff": money.money(receipt_total - _total(cand)) if receipt_total is not None else None}
    return {"items_total": items_total, "receipt_total": receipt_total, "diff": diff,
            "ok": not any(mis) and (diff is None or abs(diff) <= TOL),
            "items": [{"index": i, "expected": _expected(it), "mismatch": m} for i, (it, m) in enumerate(zip(items, mis))],
            "shift_start": shift_start, "suggested_shift": suggested,
            **(digit_check(items, receipt_total) if diff is not None and abs(diff) > TOL else dict(_NO_DIGIT))}


# Termal fişte birbirine benzeyen rakamlar, sık görülenden aza (gerçek bir fişte 89,00 üç okumada da 69,00
# okundu, kalemler fişten ₺20 eksik kaldı). Sıra önerinin sırası.
CONFUSABLE = (("6", "8"), ("3", "8"), ("5", "6"), ("1", "7"), ("0", "8"), ("9", "8"), ("5", "8"))
_PAIR_RANK = {frozenset(p): r for r, p in enumerate(CONFUSABLE)}
MULTI_MAX_LINES = 3      # tanıksız aramada en çok bu kadar satır değişir (k=1, 2, 3)
SUSPECT_MAX = 5
NAME_OVERLAP = 0.6       # telefon metninde kalemin satırı: ad kelimelerinin en az bu kadarı satırda
_SOL_CAP = 20            # çok satırlı aramada bu kadar çözümden sonra durulur (zaten belirsiz)
_NO_DIGIT = {"digit_fix": [], "digit_fix_all": None, "residual": 0.0, "suspect": [], "groups": [], "line_count": None}


def _swaps(value: float, need: int | None = None):
    """Tutarın kuruş yazımında ("8900") tek rakamı karışan eşiyle değiştir: (eş sırası, basamak üssü, yeni tutar, "6→8").
    need verilirse farktan doğrudan: basamak 10^p ancak need 10^p'ye bölünüyorsa ve need / 10^p o basamaktaki rakamın bir
    eşine olan farkıysa (6→8 = +2) aday olur (kalem başına basamak sayısı kadar deneme). Baştaki rakam 0'a dönmez."""
    s = str(money.kurus(value))
    for pos, ch in enumerate(s):
        exp = len(s) - 1 - pos
        if need is not None:
            if need % 10 ** exp:
                continue
            nds = [int(ch) + need // 10 ** exp]
        else:
            nds = [int(d) for pair in CONFUSABLE if ch in pair for d in pair if d != ch]
        for nd in nds:
            if not 0 <= nd <= 9 or nd == int(ch) or (pos == 0 and nd == 0 and len(s) > 1):
                continue
            if (rank := _PAIR_RANK.get(frozenset((ch, str(nd))))) is not None:
                yield rank, exp, int(s[:pos] + str(nd) + s[pos + 1:]) / 100, f"{ch}→{nd}"


def _variant(a: float, b: float) -> str | None:
    """b, a'nın bir ya da birkaç rakamı karışmış yazımı mı (aynı hane sayısı, her farklı rakam bir eş): "6→8, 3→8"."""
    x, y = str(money.kurus(a)), str(money.kurus(b))
    if len(x) != len(y) or x == y:
        return None
    pairs = [(c, d) for c, d in zip(x, y) if c != d]
    return ", ".join(f"{c}→{d}" for c, d in pairs) if all(frozenset(p) in _PAIR_RANK for p in pairs) else None


def _consistent(it: dict) -> bool:
    a, up, q = it.get("amount"), it.get("unit_price"), it.get("qty") or 1
    return isinstance(up, (int, float)) and up > 0 and isinstance(a, (int, float)) and abs(money.money(q * up) - a) <= TOL


def _fix(i: int, it: dict, to: float, digits: str, new_up: float | None = None, key=(9, 0), evidence: str | None = None) -> dict:
    f = {"index": i, "name": it.get("name") or "", "from": money.money(it["amount"]), "to": money.money(to), "digits": digits}
    if new_up is not None:
        f.update(unit_price_from=money.money(it["unit_price"]), unit_price_to=money.money(new_up))
    return {**f, "evidence": [evidence] if evidence else [], "_k": (*key, i),
            "_d": money.kurus(to) - money.kurus(it["amount"])}


def _priced(items: list[dict]):
    for i, it in enumerate(items):
        a = it.get("amount")
        if isinstance(a, (int, float)) and not isinstance(a, bool) and a > 0:
            yield i, it


def _line_fixes(i: int, it: dict, need: int | None = None) -> list[dict]:
    """Bir kalemin tek rakam düzeltmeleri (need verilirse yalnız tutarı tam need kuruş değiştirenler). Adet × birimi
    tutan kalemde birim fiyatın basamağı (tutar yeniden hesaplanır), 1'den çok adetliyse ayrıca tutarın basamağı (birim
    tutar ÷ adet); birimi olmayan ya da tutmayan kalemde yalnız tutar."""
    a, q, up = it["amount"], it.get("qty") or 1, it.get("unit_price")
    ok = _consistent(it)
    out: dict[int, dict] = {}
    tries = []
    if ok:  # birim farkı = fark ÷ adet (tartılı üründe yuvarlanıp aşağıda doğrulanır)
        tries += [(r, e, money.money(q * v), d, v) for r, e, v, d in _swaps(up, None if need is None else round(need / q))]
    if not ok or q != 1:
        tries += [(r, e, v, d, money.money(v / q) if ok else None) for r, e, v, d in _swaps(a, need)]
    for r, e, to, d, new_up in tries:
        f = _fix(i, it, to, d, new_up, key=(r, -e))
        if f["_d"] == 0 or (need is not None and f["_d"] != need):
            continue
        k = money.kurus(to)
        if k not in out or f["_k"] < out[k]["_k"]:
            out[k] = f
    return list(out.values())


def _tokens(s: str) -> set[str]:
    from app.textnorm import normalize

    return {w for w in re.sub(r"[^A-Z0-9]", " ", normalize(s or "")).split() if len(w) >= 2}


def _same_word(a: str, b: str) -> bool:
    """OCR'a dayanıklı kelime eşleşmesi: aynı, kesik (ön ek) ya da bir iki harf farkı ("PUR3SI" ~ "PURESI")."""
    from difflib import SequenceMatcher

    if a == b:
        return True
    return min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a) or SequenceMatcher(None, a, b).ratio() >= 0.8)


def _ocr_rows(name: str, text: str | None) -> list[tuple[list[float], str]]:
    """Telefon metninde kalemin adına benzeyen her satırın tutarları (ad kelimelerinin ≥ NAME_OVERLAP'ı satırda; OCR'ın
    kestiği kelime ön ekle eşleşir). Satırda tutar yoksa hemen alt satırınki (fiyat alt satıra düşebilir)."""
    from app import receipt_verify as rv

    want = _tokens(name)
    if not want or not (text or "").strip():
        return []
    lines = rv._clean(text).splitlines()
    found = lambda ln: [v for v in (rv._num(m.group(0)) for m in rv._AMOUNT.finditer(ln)) if v]  # noqa: E731
    out = []
    for j, line in enumerate(lines):
        got = _tokens(line)
        if sum(any(_same_word(w, g) for g in got) for w in want) / len(want) >= NAME_OVERLAP:
            nxt = lines[j + 1] if j + 1 < len(lines) else ""
            vals, src = (found(line), line) if found(line) else (found(nxt), f"{line} {nxt}")
            if vals:
                out.append((vals, src))
    return out


_RATE = re.compile(r"%\s?0?(\d{1,2})(?!\d)")


def _line_rate(it: dict, rows: list[tuple[list[float], str]]) -> int | None:
    """Kalemin KDV oranı: kalemde vat_rate varsa o, yoksa telefon metninde kalemin satırındaki "%1" / "%20"."""
    from app import vat

    r = it.get("vat_rate")
    if isinstance(r, (int, float)) and int(r) in vat.RATES:
        return int(r)
    for _, line in rows:
        if (m := _RATE.search(line)) and int(m.group(1)) in vat.RATES:
            return int(m.group(1))
    return None


def _witness(i: int, it: dict, rows: list[tuple[list[float], str]]) -> tuple[dict | None, bool]:
    """Satır başına tanık (fiş toplamından bağımsız): (düzeltme, okunan tutar metinde kalemin satırında geçiyor mu).
    - "ocr": telefon metninde kalemin satırındaki tutar (ya da birim fiyat) okunanın rakamları karışmış hali;
    - "qty": adet × birim fiyat tutmuyor ve tutar beklenenin rakamları karışmış hali (tutar yanlış okunmuş)."""
    a, q, up = it["amount"], it.get("qty") or 1, it.get("unit_price")
    agrees = any(money.kurus(v) == money.kurus(a) for vals, _ in rows for v in vals)
    if rows and not agrees:
        ok = _consistent(it)
        for vals, _ in rows:
            for v in vals:
                if d := _variant(a, v):
                    return _fix(i, it, v, d, money.money(v / q) if ok else None, key=(-1, 0), evidence="ocr"), False
                if ok and q != 1 and (d := _variant(up, v)):
                    return _fix(i, it, money.money(q * v), d, v, key=(-1, 0), evidence="ocr"), False
    e = _expected(it)
    if e is not None and not _consistent(it) and (d := _variant(a, e)):
        return _fix(i, it, e, d, key=(-1, 0), evidence="qty"), agrees
    return None, agrees


def _vat_groups(items: list[dict], rates: dict[int, int | None], vat_lines: list[dict] | None) -> list[dict]:
    """KDV oranı grupları: her oranın kalem toplamından hesaplanan KDV (brüt × oran / (100 + oran)) fişte basılan o oranın
    KDV'siyle (vat_lines) tutuyor mu. Yalnız fiyatlı her kalemin oranı biliniyorsa ve döküm varsa (yoksa []). Tolerans
    satır başı yuvarlama: max(0,02; 0,006 × kalem sayısı)."""
    printed = {int(x["rate"]): x["amount"] for x in vat_lines or [] if isinstance(x, dict) and x.get("amount")}
    priced = [i for i, _ in _priced(items)]
    if not printed or not priced or any(rates.get(i) is None for i in priced):
        return []
    out = []
    for rate in sorted({rates[i] for i in priced} | set(printed)):
        idx = [i for i in priced if rates[i] == rate]
        gross = _total([items[i]["amount"] for i in idx])
        tax = money.money(gross * rate / (100 + rate))
        p = printed.get(rate)
        out.append({"rate": rate, "items_total": gross, "tax": tax, "printed": p, "indices": idx,
                    "ok": p is not None and abs(tax - p) <= max(0.02, 0.006 * len(idx))})
    return out


_COUNT = re.compile(r"(?:TOPLAM\s*)?(\d{1,3})\s*(?:ADET\s*)?(?:URUN|KALEM)\b|(?:URUN|KALEM)\s*(?:SAYISI|ADEDI)\s*[:.]?\s*(\d{1,3})")


def _printed_count(text: str | None) -> int | None:
    """Fişte basılı ürün sayısı ("TOPLAM 23 ÜRÜN", "ÜRÜN SAYISI: 23", "23 ADET ÜRÜN")."""
    from app.textnorm import normalize

    for line in (text or "").splitlines():
        if m := _COUNT.search(normalize(line)):
            return int(m.group(1) or m.group(2))
    return None


HISTORY_TOL = 0.02  # önceki fiyatla eşleşme payı (±%2)


def _hist_match(unit: float, prices: list[float]) -> float | None:
    return next((p for p in prices if p > 0 and abs(unit - p) <= p * HISTORY_TOL), None)


def _score(f: dict, it: dict, history: dict[int, list[float]] | None, group_hit: bool) -> dict:
    """Kanıtları birleştir: ocr 4, qty 4, history +2 (düzeltilmiş birim fiyat önceki fiyatı tutuyor; okunan tutuyorsa −2),
    group +1 (fark bu kalemin KDV grubunda). Sıra: ocr+history > ocr > history > group > hiçbiri."""
    q = it.get("qty") or 1
    ev = list(f["evidence"])
    score = 4 * len(ev)
    prices = (history or {}).get(f["index"]) or []
    if prices:
        new, old = f.get("unit_price_to") or money.money(f["to"] / q), f.get("unit_price_from") or money.money(f["from"] / q)
        if (p := _hist_match(new, prices)) is not None and _hist_match(old, prices) is None:
            ev.append("history")
            f["history_price"] = p
            score += 2
        elif _hist_match(old, prices) is not None and _hist_match(new, prices) is None:
            score -= 2
    if group_hit:
        ev.append("group")
        score += 1
    f["evidence"], f["score"] = ev, score
    return f


def _deepen(items: list[dict], pool: set[int], need: int) -> list[tuple[dict, ...]]:
    """Tanıksız arama, kaç satırın yanlış olduğu varsayılmadan: önce k=1 (farktan doğrudan, _line_fixes need), sonra
    k=2, k=3 farklı satırın tek rakam düzeltmesi farkı birlikte tam kapatıyor mu; çözümü olan ilk k'nin çözümleri döner
    (en çok _SOL_CAP; k=3'ten öteye gidilmez). Yalnız pool'daki satırlar."""
    rows = [(i, it) for i, it in _priced(items) if i in pool]
    one = [(f,) for i, it in rows for f in _line_fixes(i, it, need)]
    if one or MULTI_MAX_LINES < 2:
        return one
    cands = [f for i, it in rows for f in _line_fixes(i, it)]
    by_d: dict[int, list[dict]] = {}
    for f in cands:
        by_d.setdefault(f["_d"], []).append(f)
    sols: list[tuple[dict, ...]] = []
    for f in cands:
        sols += [(f, g) for g in by_d.get(need - f["_d"], []) if g["index"] > f["index"]]
        if len(sols) > _SOL_CAP:
            return sols
    if sols or MULTI_MAX_LINES < 3:
        return sols
    for f in cands:
        for g in cands:
            if g["index"] > f["index"]:
                sols += [(f, g, h) for h in by_d.get(need - f["_d"] - g["_d"], []) if h["index"] > g["index"]]
            if len(sols) > _SOL_CAP:
                return sols
    return sols


def _public(fixes) -> list[dict]:
    return [{k: v for k, v in f.items() if not k.startswith("_")} for f in fixes]


def digit_check(items: list[dict], receipt_total: float | None, text: str | None = None, *,
                vat_lines: list[dict] | None = None, history: dict[int, list[float]] | None = None) -> dict:
    """Kalemler fişi tutmuyorsa karışan rakam (termal baskı 6↔8 …) önerileri; kaç satırın yanlış olduğu varsayılmaz.
    Çıktı: {"digit_fix": [{index, name, from, to, digits, unit_price_from?, unit_price_to?, evidence: [...], score,
    history_price?}], "digit_fix_all": null | {"indices", "total_ok"}, "residual": TL, "suspect": [sıra],
    "groups": [{rate, items_total, tax, printed, indices, ok}], "line_count": null | {"printed", "read"}}.
    Hiçbiri kendiliğinden uygulanmaz. Kanıtlar (yalnız bu fişin metni ve kullanıcının kendi defterindeki geçmiş):
    1. Satır başına tanık, fiş toplamından bağımsız (_witness: telefon metni "ocr", adet × birim "qty"): hepsi öneri olur.
    2. KDV grupları (_vat_groups: oran başına hesaplanan KDV basılanla tutuyor mu): fark tutmayan gruplardaysa arama ve
       şüpheliler o gruplarla sınırlanır ("group").
    3. Basılı ürün sayısı okunan kalemden çoksa (line_count) bir satır eksik olabilir: rakam araması yapılmaz.
    4. Kalan fark tanıksız satırlarda (metin kalemin satırında okunan tutarı gösteriyorsa o satır doğrulanmış sayılır)
       _deepen ile aranır: k=1, 2, 3; çözümü olan ilk k'de çözümler puanlanır (_score; history: önceki fiyat) ve en
       yüksek puanlı tek çözüm (eşi yoksa) eklenir; yoksa suspect (o k'nin çözümlerinde en çok geçen satırlar, çözüm
       yoksa en çok karışabilir rakamı olanlar; en çok SUSPECT_MAX).
    residual: önerilerin hepsi uygulanınca kalan fark (0: fiş tutar); öneri yoksa fiş − kalemler. 2+ öneride
    digit_fix_all {indices, total_ok: residual == 0} ("Hepsini düzelt")."""
    if receipt_total is None:
        return dict(_NO_DIGIT)
    need = money.kurus(receipt_total) - money.kurus(_total([it.get("amount") for it in items]))
    if need == 0:
        return dict(_NO_DIGIT)
    rows = {i: _ocr_rows(it.get("name") or "", text) if text else [] for i, it in _priced(items)}
    seen = {i: _witness(i, it, rows[i]) for i, it in _priced(items)}
    groups = _vat_groups(items, {i: _line_rate(it, rows.get(i, [])) for i, it in enumerate(items)}, vat_lines)
    bad_groups = [g for g in groups if not g["ok"]]
    local = {i for g in bad_groups for i in g["indices"]} if bad_groups and len(bad_groups) < len(groups) else None
    fixes = [_score(f, items[f["index"]], history, local is not None and f["index"] in local) for f, _ in seen.values() if f]
    rest = need - sum(f["_d"] for f in fixes)
    count = _printed_count(text)
    n_read = sum(1 for _ in _priced(items))
    line_count = {"printed": count, "read": n_read} if count and count > n_read and \
        count != round(sum(it.get("qty") or 1 for _, it in _priced(items))) else None
    suspect: list[int] = []
    if rest and not line_count:
        pool = {i for i, (f, agrees) in seen.items() if not f and not agrees and (local is None or i in local)}
        sols = [tuple(_score(dict(f), items[f["index"]], history, local is not None) for f in sol)
                for sol in _deepen(items, pool, rest)]
        ranked = sorted(sols, key=lambda sol: -sum(f["score"] for f in sol))
        if ranked and (len(ranked) == 1 or sum(f["score"] for f in ranked[0]) > sum(f["score"] for f in ranked[1])):
            fixes += list(ranked[0])
            rest = 0
        else:
            hits: dict[int, int] = {}
            for sol in sols:
                for f in sol:
                    hits[f["index"]] = hits.get(f["index"], 0) + 1
            swaps = {i: len(_line_fixes(i, it)) for i, it in _priced(items) if i in pool}
            cand = hits or {i: 0 for i, n in swaps.items() if n}
            suspect = sorted(cand, key=lambda i: (-cand[i], -swaps.get(i, 0), i))[:SUSPECT_MAX]
    fixes.sort(key=lambda f: f["index"])
    return {"digit_fix": _public(fixes), "residual": rest / 100, "suspect": suspect, "groups": groups,
            "line_count": line_count,
            "digit_fix_all": {"indices": [f["index"] for f in fixes], "total_ok": rest == 0} if len(fixes) > 1 else None}


def digit_fixes(items: list[dict], receipt_total: float | None, text: str | None = None) -> list[dict]:
    """digit_check'in yalnız önerileri."""
    return digit_check(items, receipt_total, text)["digit_fix"]


def add_evidence(chk: dict | None, items: list[dict], text: str | None = None, vat_lines: list[dict] | None = None,
                 history: dict[int, list[float]] | None = None) -> None:
    """Okuma ya da kayıtlı fiş anında kanıtlarla (telefon/PDF metni, KDV dökümü, fiyat geçmişi) digit_* alanlarını yeniden
    hesapla (chk yerinde; metin saklanmaz). Fiş tutuyorsa dokunmaz."""
    if (text or vat_lines or history) and chk and "digit_fix" in chk and chk.get("diff") is not None and abs(chk["diff"]) > TOL:
        chk.update(digit_check(items, chk.get("receipt_total"), text, vat_lines=vat_lines, history=history))


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
    Her sonraki kalem en çok bir kez sayılır. Tek kalemli fişte fiyatsız kalem de bir belirtidir. Hem yeniden okuma
    tetiği (needs_reread) hem kıyas (app/receipt_bench.py structure) bunu kullanır."""
    n = 0
    if len(items) == 1 and items[0].get("amount") is None:
        return 1
    for prev, cur in zip(items, items[1:]):
        p, c = (prev.get("name") or "").strip(), (cur.get("name") or "").strip()
        if prev.get("amount") is None or cur.get("amount") is None or continuation_like(c) or (p and p[-1] in _JOINERS):
            n += 1
    return n


def inconsistent(items: list[dict], chk: dict) -> list[str]:
    """Kalemler fişi açıkça tutmuyor: "fiyatsiz" (fiyatsız kalem) ve/veya "toplam" (kalem toplamı fiş toplamından
    ±SUM_TOL'dan fazla sapıyor). Eski metin yolunun (HESAPKITAP_RECEIPT_TEXT_FIRST=1) fotoğraf yedeği yalnız buna bakar."""
    if not items:
        return []
    out = []
    if any(it.get("amount") is None for it in items):
        out.append("fiyatsiz")
    diff = chk.get("diff")
    if diff is not None and abs(diff) > SUM_TOL:
        out.append("toplam")
    return out


def reread_reasons(items: list[dict], chk: dict, total_check: str | None = None) -> list[str]:
    """Fişi güçlü modelle yeniden okuma nedenleri (boşsa okunmaz):
    - "fiyatsiz": fiyatsız kalem var (satır kayması belirtisi);
    - "toplam": kalem toplamı (indirim satırları dahil) fiş toplamından ±SUM_TOL'dan fazla sapıyor;
    yalnız kalemli fişte (2+ kalem; benzin, kart slibi gibi kalemsiz/tek kalemli fişte gereksiz maliyet) ayrıca:
    - "birim": adet × birim fiyat satır tutarını tutmayan kalem (check.items[].mismatch);
    - "bolunmus": iki satıra bölünmüş ürün belirtisi (split_suspects);
    - "metin": toplam telefon/PDF metninde geçmiyor (app/receipt_verify.py total_check "kontrol"; metin yoksa None).
    Ölçüm 04.11.2026: kalemli 12 fişin 5'inde ilk okumanın kalemleri yanlış hizalıydı, yalnız toplam farkı tetiklerken
    biri yakalanıyordu."""
    out = inconsistent(items, chk)
    if len(items) >= 2:
        if any(i.get("mismatch") for i in chk.get("items") or []):
            out.append("birim")
        if split_suspects(items):
            out.append("bolunmus")
        if total_check == "kontrol":
            out.append("metin")
    return out


def needs_reread(items: list[dict], chk: dict, total_check: str | None = None) -> bool:
    """Fişi güçlü modelle yeniden okumak gerekir mi? Nedenler: reread_reasons."""
    return bool(reread_reasons(items, chk, total_check))


def score(chk: dict) -> tuple[int, float]:
    """Kalemlerin fişi tutma puanı (küçük iyi): (adet × birim tutmayan kalem, |fiş − kalem toplamı|)."""
    return sum(1 for i in chk["items"] if i["mismatch"]), abs(chk["diff"]) if chk["diff"] is not None else 0.0


def reread_hint(doc: dict) -> float | None:
    """Yeniden okumaya bildirilecek fiş toplamı: toplam metinde yoksa (total_check "kontrol") şüphelidir, bildirilmez
    (model kalemleri uydurma toplama uydurmasın); güçlü model toplamı kendisi okur."""
    return None if doc.get("total_check") == "kontrol" else doc.get("total")


def take_reread(doc: dict, again: dict, text: str | None = None) -> bool:
    """Yeniden okuma sonucunu yalnız daha iyiyse al (doc yerinde değişir; alındıysa True).
    - İlk toplam metinde yoktu ve yeni toplam metinde VAR: yeni toplam + kalemler alınır (total_check "ok").
    - Yoksa toplam değişmez; yeni kalemler ilk toplama göre daha az tutmayan kalem ve daha küçük farkla (biri kesin
      iyi, öteki kötü değil) tutuyorsa alınır."""
    from app import receipt_verify

    items = again.get("items") or []
    if not items:
        return False
    new_total = again.get("total")
    if doc.get("total_check") == "kontrol" and new_total and new_total != doc.get("total") and \
            receipt_verify.total_in_text(new_total, text):
        doc.update(total=new_total, items=items, check=check(items, new_total), total_check="ok")
        return True
    chk2 = check(items, doc.get("total"))
    (m1, d1), (m2, d2) = score(doc.get("check") or check(doc.get("items") or [], doc.get("total"))), score(chk2)
    if (m2 < m1 and d2 <= d1) or (m2 <= m1 and d2 < d1):
        doc["items"], doc["check"] = items, chk2
        return True
    return False
