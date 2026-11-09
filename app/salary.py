"""Maaş zammı: yüzdeyle girilen zam (taslak çözümü) ve yatan maaşa göre "maaşın değişti" önerisi.

Taslak (resolve_draft): kullanıcı zammı üç biçimde yazabilir; hepsi kaydedilen BRÜT'e çevrilir (sunucu her ay netini
bordro hesabıyla bulur, app/payroll.py):
  gross         yeni brüt
  net           yeni net (o ayın neti bu olacak brüt aranır)
  pct + basis   "%15 zam": brut → önceki brüt × 1,15; net → zamsız olsaydı o ay eline geçecek net × 1,15
"Önceki" = seçilen aydan hemen önce geçerli maaş (kayıtlı değişiklikler dahil; netle tanımlı kalemde netin brüt
karşılığı, payroll.gross_for_net). Peşin maaşta (memur, recurring.advance_pay) ilk zamlı ay günlerin farkını da taşır
(db.gross_schedule): net hedefi ve önizleme "normal" aya, yani sonraki aya göre yapılır.

Öneri (drift): maaş kalemine bağlı yatan tutar, planın beklediğinden farklıysa yeni brütü bulur ve sorar.
  * Taban oran k: önceki (en çok 3) normal ayda yatan / planlanan; küçük sapmalar (BES, sendika, yemek kartı) bu oranla
    elenir, yalnız sıçrama sayılır. Sıçrama eşiği %3; o ay için kayıtlı (beklenen) bir değişiklik varsa %1.
  * Tek ay yeter: o aya kullanıcı bir zam girmişse (bekliyordu) ya da ödemenin yapıldığı ay Ocak/Temmuz ve artış
    %0–50 arası (memur ve çoğu toplu sözleşmede zam ayı). Diğer her durumda (ara ay, azalış, %50'den büyük sıçrama =
    büyük olasılıkla ikramiye) art arda İKİ ay aynı yeni seviyede gelmeli; ikramiyeli tek ay öneri doğurmaz.
  * Peşin maaşta ilk zamlı ay fark içerir: yeni brüt, modelin o ay farkı da ekleyeceği bilinerek çözülür; kalıcı tutar
    olarak sonraki ayın neti önerilir (taban + fark değil). Sonraki ayın normal tutarı da aynı brütü doğrular.
  * "Bu ay farklıydı": o ay öneriden ve tabandan çıkar (ayar salary_drift_dismissed).
"""
import json
import statistics

from app import db, money

AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
# Türkçe ek uyumu (ay adının son ünlüsü/ünsüzü): Ocak'ta / Ocak'tan
_TA = ["'ta", "'ta", "'ta", "'da", "'ta", "'da", "'da", "'ta", "'de", "'de", "'da", "'ta"]
_TAN = ["'tan", "'tan", "'tan", "'dan", "'tan", "'dan", "'dan", "'tan", "'den", "'den", "'dan", "'tan"]
RAISE_MONTHS = (1, 7)   # ödeme ayı: memur ve çoğu toplu sözleşme zammı
THR, THR_EXPECTED = 3 / 100, 1 / 100  # oran (para değil): sıçrama eşiği
CONSISTENT = 1.5 / 100  # iki ay "aynı yeni seviye": modelle en çok %1,5 sapma
SINGLE_MAX = 0.50       # tek ayda bundan büyük artış: ikramiye say, ikinci ayı bekle
DISMISS_KEY = "salary_drift_dismissed"
DUZELTME = "Düzeltme"   # mobil "Düzeltme" türü: reason zam + note


def add_months(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    t = y * 12 + m - 1 + n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def _mi(month: str) -> int:
    return int(month[5:7]) - 1


def ay_ta(month: str) -> str:
    return AY[_mi(month)] + _TA[_mi(month)]


def ay_tan(month: str) -> str:
    return AY[_mi(month)] + _TAN[_mi(month)]


def ay_yil(month: str) -> str:
    return f"{AY[_mi(month)]} {month[:4]}"


def pct_text(p: float) -> str:
    """11.234 → "%11,2"; 10.0 → "%10" (işaretsiz)."""
    s = f"{abs(p):.1f}".replace(".", ",")
    return "%" + (s[:-2] if s.endswith(",0") else s)


def _tl(x: float) -> str:
    return money.tl(x, decimals=0)


# ---------------------------------------------------------------- model

def _rec(r) -> dict:
    return dict(r)


def net_at(r: dict, month: str, changes: list[dict], advance: bool | None = None) -> float:
    """Planın o ay beklediği net (aya özel tutar hariç); advance=False: peşin maaş farkı olmadan."""
    r = _rec(r)
    if advance is not None:
        r["advance_pay"] = int(advance)
    return db.planned_amount(r, month, overrides={}, changes=changes)


def _with(changes: list[dict], draft: dict) -> list[dict]:
    keep = [c for c in changes if c["from_month"] != draft["from_month"]]
    full = {"reason": "zam", "reset_cum": 0, "pay_day": None, "employer": None, "note": None, "id": None, **draft}
    return sorted([*keep, full], key=lambda c: c["from_month"])


def prev_gross(r: dict, month: str, changes: list[dict]) -> float | None:
    """Seçilen aydan hemen önce geçerli brüt; netle tanımlı kalemde netin brüt karşılığı."""
    from app.payroll import gross_for_net

    r = _rec(r)
    before = [c for c in changes if c["from_month"] < month]
    g = db.gross_at(r, add_months(month, -1), before)
    if g:
        return g
    if r.get("amount"):
        paid = db.occurrence_date(add_months(month, -1), db.day_at(r, add_months(month, -1), before))
        return gross_for_net(r["amount"], int(paid[:4]))
    return None


def _solve(fn, target: float, hi: float) -> float:
    """fn(brüt) = net olacak brüt (net brütle artar): ikili arama, kuruşa yuvarlı."""
    lo, hi = 0.0, max(hi, target * 2, 1.0)
    while fn(hi) < target and hi < 1e9:
        hi *= 2
    for _ in range(48):
        mid = (lo + hi) / 2
        if fn(mid) < target:
            lo = mid
        else:
            hi = mid
    return money.money(hi)


def has_advance_diff(r: dict, month: str, gross: float, prev: float | None) -> bool:
    r = _rec(r)
    return bool(r.get("advance_pay")) and (r.get("day_of_month") or 1) > 1 and prev is not None and gross > prev


def resolve_draft(r: dict, changes: list[dict], draft: dict) -> dict:
    """Taslağı (gross | net | pct+basis) brüte çevir; önizleme bilgisiyle. ValueError: hesaplanamıyor."""
    r = _rec(r)
    frm = draft["from_month"]
    others = [c for c in changes if c["from_month"] != frm]
    before = [c for c in others if c["from_month"] < frm]
    pg = prev_gross(r, frm, others)
    pct, basis = draft.get("pct"), draft.get("basis") or "brut"
    base = {k: draft.get(k) for k in ("from_month", "reason", "reset_cum", "pay_day", "employer", "note")}

    def model(g: float) -> list[dict]:
        return _with(others, {**base, "gross": g})

    advance = bool(r.get("advance_pay")) and (r.get("day_of_month") or 1) > 1
    if draft.get("gross"):
        g = money.money(draft["gross"])
    elif pct is not None and basis == "brut":
        if not pg:
            raise ValueError("Önceki brüt bilinmiyor; yeni brütü tutar olarak yaz")
        g = money.money(pg * (1 + pct / 100))
    else:
        # net hedefi: peşin maaşta ilk ay farkı taşır, sonraki "normal" aya göre eşleştir (zam olacaksa)
        normal = add_months(frm, 1) if advance else frm
        target = draft.get("net")
        if pct is not None:
            target = net_at(r, normal, before) * (1 + pct / 100)
        if not target or target <= 0:
            raise ValueError("Yeni tutar gerekli")
        g = _solve(lambda x: net_at(r, normal, model(x)), target, (pg or target) * 2)
        if advance and pg and g <= pg:  # zam değil (düşüş): fark yok, aynı ayın netine göre
            g = _solve(lambda x: net_at(r, frm, model(x)), target, (pg or target) * 2)
    new = model(g)
    diff = has_advance_diff(r, frm, g, pg)
    normal = add_months(frm, 1) if diff else frm
    old_net, new_net = net_at(r, normal, before), net_at(r, normal, new)
    out = {"gross": g, "prev_gross": pg, "pct": pct, "basis": basis if pct is not None else None,
           "gross_pct": round((g / pg - 1) * 100, 2) if pg else None,
           "net_pct": round((new_net / old_net - 1) * 100, 2) if old_net else None,
           "old_net": money.money(old_net), "new_net": money.money(new_net), "normal_month": normal,
           "advance": None, "text": None, "advance_text": None}
    if pct is not None:
        if basis == "brut":
            out["text"] = f"Önceki brüt {_tl(pg)} → {pct_text(pct)} zamla {_tl(g)} brüt (≈ {_tl(new_net)} net)."
        else:
            out["text"] = f"Önceki net {_tl(old_net)} → {pct_text(pct)} zamla {_tl(new_net)} net (≈ {_tl(g)} brüt)."
    if diff:
        first = net_at(r, frm, new)
        plain = net_at(r, frm, new, advance=False)
        days = (r.get("day_of_month") or 1) - 1
        out["advance"] = {"month": frm, "first": money.money(first), "base": money.money(plain),
                          "diff": money.money(first - plain), "days": days, "next": money.money(new_net)}
        out["advance_text"] = (f"{ay_ta(frm)} {_tl(first)} (yeni maaş {_tl(plain)} + {days} günlük fark "
                               f"{_tl(first - plain)}), {ay_tan(normal)} itibaren {_tl(new_net)}"
                               + (" (vergi dilimi yükseldiği için daha düşük)." if new_net < plain * 99 / 100 else "."))
    return out


# ---------------------------------------------------------------- yatan maaşa göre öneri

def _dismissed() -> set[str]:
    v = db.get_setting(DISMISS_KEY)
    try:
        return set(json.loads(v)) if v else set()
    except ValueError:
        return set()


def dismiss(rid: int, month: str) -> None:
    keys = _dismissed() | {f"{rid}:{month}"}
    db.set_setting(DISMISS_KEY, json.dumps(sorted(keys)))


def is_salary(r: dict) -> bool:
    r = _rec(r)
    return r["kind"] == "gelir" and (db._is_salary_recurring(r) or bool(db.salary_changes(r["id"])))


def _actuals(r: dict, entries: list[dict]) -> dict[str, float]:
    by: dict[str, float] = {}
    for e in entries:
        if e["recurring_id"] == r["id"] and not e["skipped"] and e["kind"] == "gelir":
            by[e["month"]] = by.get(e["month"], 0.0) + e["amount"]
    return by


def _draft(reg: dict | None, start: str, gross: float) -> dict:
    """Bulunan brüt; o aya kayıtlı değişiklik (iş değişikliği: maaş günü, matrah) varsa diğer alanları korunur."""
    keep = {k: reg[k] for k in ("reason", "reset_cum", "pay_day", "employer", "note")} if reg else {}
    return {**keep, "from_month": start, "gross": gross}


def suggestion_for(r: dict, today=None, entries: list[dict] | None = None) -> dict | None:
    from datetime import date

    r = _rec(r)
    today = today or date.today()
    cur = db.period_of(today)
    if r.get("end_month") and r["end_month"] < cur:
        return None
    actual = _actuals(r, entries if entries is not None else db.list_entries())
    changes = db.salary_changes(r["id"])
    if not r.get("gross_amount") and not changes:
        # netle tanımlı, brütsüz kalem: düz şablon net yerine aynı netin brüt karşılığıyla modelle (yıl başında dilim
        # sıfırlanınca net artar, yıl sonuna doğru azalır; bunlar zam/indirim sanılmasın)
        from app.payroll import gross_for_net

        if not r.get("amount"):
            return None
        r["gross_amount"] = gross_for_net(r["amount"], int(cur[:4]))
        r["_net_defined"] = True
    ov = db.recurring_overrides()
    gone = _dismissed()
    exp_cache: dict[str, float] = {}

    def expected(m: str) -> float:
        if m not in exp_cache:
            exp_cache[m] = net_at(r, m, changes)
        return exp_cache[m]

    ok = sorted(m for m, v in actual.items() if v > 0 and m <= cur and f"{r['id']}:{m}" not in gone
                and (r["id"], m) not in ov and expected(m) > 0)
    if not ok:
        return None
    last = ok[-1]
    if last < add_months(cur, -2):  # eski ay: artık sorulmaz
        return None
    for start in ([add_months(last, -1), last] if add_months(last, -1) in ok else [last]):
        base = [m for m in ok if m < start][-3:]
        if not base:
            continue
        k = statistics.median_low([actual[m] / expected(m) for m in base])
        reg = next((c for c in changes if c["from_month"] == start), None)
        jump = actual[last] / (expected(last) * k) - 1
        if abs(jump) < (THR_EXPECTED if reg else THR):
            continue  # yatan, planın beklediği gibi (bu başlangıca göre)
        if start == last:
            paid_month = int(db.occurrence_date(last, db.day_at(r, last, changes))[5:7])
            if not (reg or (paid_month in RAISE_MONTHS and 0 < jump <= SINGLE_MAX)):
                continue  # ara ay / azalış / ikramiye olabilir: ikinci ayı bekle
        others = [c for c in changes if c["from_month"] != start]
        pg = prev_gross(r, start, others)
        new_gross = _solve(lambda g: net_at(r, last, _with(others, _draft(reg, start, g))),
                           actual[last] / k, (pg or actual[last]) * 2)
        new = _with(others, _draft(reg, start, new_gross))
        months = [m for m in ok if start <= m <= last]
        if any(abs(net_at(r, m, new) * k - actual[m]) > CONSISTENT * actual[m] for m in months):
            continue
        return _describe(r, changes, start, last, months, new_gross, pg, k, actual, reg)
    return None


def _describe(r, changes, start, last, months, g, pg, k, actual, reg) -> dict:
    before = [c for c in changes if c["from_month"] < start]
    new = _with([c for c in changes if c["from_month"] != start], _draft(reg, start, g))
    diff = has_advance_diff(r, start, g, pg)
    normal = add_months(start, 1) if diff else start
    a, b = net_at(r, normal, before) * k, net_at(r, normal, new) * k
    p = (b / a - 1) * 100 if a else 0.0
    gp = (g / pg - 1) * 100 if pg else None
    parts = []
    expected_pct = basis = actual_pct = None
    if reg:
        basis = reg.get("pct_basis") or ("net" if reg.get("pct") is not None else "brut")
        expected_pct = reg["pct"] if reg.get("pct") is not None else gp
        actual_pct = p if basis == "net" else gp
    # başlıktaki yüzde ve tutarlar aynı tabanda (kullanıcı kontrol edebilsin): brüt maaşta / brüt beklentide brüt,
    # netle tanımlı maaşta ya da net beklentide eline geçen
    brut = gp is not None and (basis == "brut" or (basis is None and not r.get("_net_defined")))
    if brut:
        title = (f"Brüt maaşın {pct_text(gp)} {'arttı' if g >= pg else 'azaldı'} ({_tl(pg)} → {_tl(g)}). "
                 "Sabit kalemi güncelle?")
        parts.append(f"Eline geçen {_tl(a)} → {_tl(b)}.")
    else:
        title = f"Maaşın {pct_text(p)} {'arttı' if b >= a else 'azaldı'} ({_tl(a)} → {_tl(b)}). Sabit kalemi güncelle?"
    if expected_pct is not None and actual_pct is not None:
        parts.insert(0, f"{'Brüt ' if basis == 'brut' else ''}{pct_text(expected_pct)} bekliyordun, "
                        f"{pct_text(actual_pct)} geldi.")
    adv = None
    if diff:
        first = actual[start]
        plain = net_at(r, start, new, advance=False) * k
        days = (r.get("day_of_month") or 1) - 1
        adv = {"month": start, "first": money.money(first), "base": money.money(plain), "diff": money.money(first - plain),
               "days": days, "next": money.money(b)}
        parts.append(f"{ay_ta(start)} yatan {_tl(first)}: yeni maaş {_tl(plain)} + {days} günlük zam farkı "
                     f"{_tl(first - plain)} (yalnız bu ay).")
    if len(months) > 1:
        parts.append("Son iki ay yeni tutarla geldi.")
    parts.append(f"{ay_tan(start)} itibaren plana yansır; geçmiş aylar değişmez.")
    return {"recurring_id": r["id"], "description": r["description"], "kind": "gelir", "type": "maas",
            "old": money.money(a), "new": money.money(b), "from_month": start, "month": last,
            "pct": round(p, 2), "gross_pct": None if gp is None else round(gp, 2), "gross": g, "prev_gross": pg, "expected_pct": None if expected_pct is None else round(expected_pct, 2),
            "expected_basis": basis if expected_pct is not None else None,
            "actual_pct": None if actual_pct is None else round(actual_pct, 2), "advance": adv,
            "title": title, "text": " ".join(parts), "accept_label": "Güncelle", "dismiss_label": "Bu ay farklıydı",
            "id": f"maas:{r['id']}:{last}"}


def drift(today=None) -> list[dict]:
    entries = db.list_entries()
    out = []
    for r in db.list_recurring():
        if is_salary(r):
            s = suggestion_for(r, today, entries)
            if s:
                out.append(s)
    return out


def accept(rid: int, month: str, today=None) -> int:
    """Öneriyi uygula: o dönemden itibaren bulunan brüt (aynı döneme kayıtlı beklenen zam varsa yerine geçer)."""
    from datetime import date

    # önerinin dayandığı ay "bugün"den sonra görünmesin diye en az o ayın başı (ileri tarihli onay)
    today = today or max(date.today(), date.fromisoformat(f"{month}-01"))
    r = next((x for x in db.list_recurring() if x["id"] == rid), None)
    s = suggestion_for(r, today) if r and is_salary(r) else None
    if not s or s["month"] != month:
        raise KeyError(rid)
    reg = next((c for c in db.salary_changes(rid) if c["from_month"] == s["from_month"]), None)
    note = DUZELTME if s["new"] < s["old"] else None
    return db.add_salary_change(rid, {"from_month": s["from_month"], "gross": s["gross"],
                                      "reason": reg["reason"] if reg else "zam", "reset_cum": reg["reset_cum"] if reg else 0,
                                      "pay_day": reg["pay_day"] if reg else None,
                                      "employer": reg["employer"] if reg else None, "note": note})
