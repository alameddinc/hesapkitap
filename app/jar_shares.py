"""Kumbara katılımcıları ve aylık pay (yetenek kumbara_shares; v2 eklemeleri kumbara_shares_v2).

Bir kumbaraya birden çok kişi düzenli para (ya da altın/döviz) atar (ör. üç kardeş anneleri için). Kişi defterdeki bir
kullanıcı (jar_people.user_id) ya da uygulamayı kullanmayan biri (yalnız ad). TL, altın ve döviz kumbarasında; tutarlar
kumbaranın biriminde (₺, gram, adet, $ …). Hassasiyet: TL kuruş, gram/döviz 0,01, adet tam sayı (UNIT_SCALE).

Ayın toplam payı (target), o ay için:
  aylik  — katılımcıların ayda toplam atacağı (jars.share_monthly; tarihli: jar_share_changes person_id NULL).
           Otomatik ayırmanın tutarı (jars.monthly) temel alınmaz: otomatik ayırma ekleyen/sahibin katkısıdır
  hedef  — toplam yoksa hedef ve son tarihten: ay başındaki kalan / (bu ay dahil son tarih ayına kadar ay), birim
           hassasiyetine yukarı yuvarlı. Ay içinde atılan, o ayın payını değiştirmez
  tamam  — hedefe ay başında ulaşılmış: pay yok
  None   — hedef/son tarih/toplam yok: yalnız elle verilen paylar
Kişinin payı o ay: elle verilmişse o (tarihli: jar_share_changes; "şu aydan itibaren"), yoksa (target − elle paylar)
eşit bölünür; bölünemeyen kalan (kuruş / 0,01 gr / 1 adet) sıradaki ilk eşit paylı kişiye. "Bu ay yok" (jar_exempt)
işaretli kişinin o ayki payı 0 (başkasının payı artmaz); o ay geçmişte tam sayılır.

Defter (kişi başı, aydan aya):
  - Eksik devreder: ay biterken payın kalanı "tamamlanacak" olur (muaf ay hariç).
  - Yeni katkı önce en eski tamamlanacak aya, sonra o ayın payına sayılır; artan "fazla".
  - Fazla: kumbara ayarı carry_over açıksa sonraki ayların payına sayılır ("Geçen aydan ₺500 fazla geldi"), kapalıysa
    o ayda fazla olarak kalır.
Kim attı: hareketin person_id'si; yoksa ekleyen kullanıcının (created_by) satırı; o da yoksa defter sahibinin satırı, o
da yoksa sıradaki ilk kişi (eski istemci bozulmaz). Sayılan: yatırma (amount > 0, TL'de ayırma, altın/döviz'de alım ya
da eklenen miktar); başlangıç/aktarma, çekim ve borç ödemesi sayılmaz.
Geçmiş (son 12 ay): payı olan ya da muaf her ay sayılır; sonradan tamamlanan ay da tam sayılır, açık kalan ay
"tamamlanacak" listesinde görünür.

Değer ilkesi: yalnız hesap; faiz/mevduat/yatırım önerisi yok. Dil nötr ("tamamlanacak"; borç/gecikme sözü yok).
Metinler istemcide (gizli tutar maskesi için): web src/lib/kumbaraPay.ts, mobil src/lib/kumbaraPay.ts.
"""
from __future__ import annotations

import math
from collections import deque
from datetime import date

from app.jar_goal import INITIAL_NOTES, _add_months

DEFAULT_REMIND_DAY = 25
HISTORY_MONTHS = 12
ADET_UNITS = {"CEYREK", "YARIM", "TAM", "CUMHURIYET", "ATA"}
ALL = "0000-00"  # tarihli pay geçmişinde "tüm aylar" (ilk değişiklikten önceki değer)


def scale_of(unit: str | None) -> int:
    """Birim hassasiyeti (1 birimde kaç adım): TL kuruş 100, gram/döviz 100, adet 1."""
    return 1 if (unit or "TRY") in ADET_UNITS else 100


def _q(x: float, sc: int) -> int:
    return int(round(float(x) * sc))


def _counted(m: dict) -> bool:
    return m["amount"] > 0 and m.get("note") not in INITIAL_NOTES and not m.get("debt_payment_id")


def _months_between(a: str, b: str) -> int:
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + mb - ma


def effective(changes: list[dict], base, ym: str):
    """Tarihli değer: from_month <= ym olan en son değişiklik; değişiklik yoksa taban değer."""
    best = None
    for c in changes:
        if c["from_month"] <= ym and (best is None or c["from_month"] >= best["from_month"]):
            best = c
    return best["share"] if best else base


def _changes(jar: dict, pid: int | None) -> list[dict]:
    return [c for c in jar.get("share_changes") or [] if c.get("person_id") == pid]


def month_target(jar: dict, ym: str) -> tuple[float | None, str | None]:
    """Ayın toplam payı ve kaynağı (aylik | hedef | tamam | None), kumbaranın biriminde."""
    sc = scale_of(jar.get("unit"))
    total = effective(_changes(jar, None), jar.get("share_monthly"), ym)
    if total:
        return _q(total, sc) / sc, "aylik"
    goal, dl = jar.get("goal"), jar.get("deadline")
    if not goal or not dl:
        return None, None
    start = f"{ym}-01"
    before = sum(m["amount"] for m in jar.get("moves") or [] if m["move_date"] < start)
    left_q = _q(goal, sc) - _q(before, sc)
    if left_q <= 0:
        return 0.0, "tamam"
    months = max(1, _months_between(ym, str(dl)[:7]) + 1)
    step = sc if (jar.get("unit") or "TRY") == "TRY" else 1  # TL'de tam liraya, gram/dövizde 0,01'e, adette 1'e yukarı
    return math.ceil(left_q / months / step) * step / sc, "hedef"


def split(target: float | None, people: list[dict], scale: int = 100) -> dict[int, float | None]:
    """Kişi başı pay: elle verilen (share) sabit, kalan eşit; bölünemeyen kalan ilk eşit paylı kişiye. target None:
    eşit paylıların payı yok (None)."""
    manual = {p["id"]: _q(p["share"], scale) for p in people if p.get("share")}
    auto = [p for p in people if not p.get("share")]
    out: dict[int, float | None] = {pid: k / scale for pid, k in manual.items()}
    if target is None:
        out.update({p["id"]: None for p in auto})
        return out
    rem = max(0, _q(target, scale) - sum(manual.values()))
    if auto:
        base, extra = divmod(rem, len(auto))
        for i, p in enumerate(auto):
            out[p["id"]] = (base + (extra if i == 0 else 0)) / scale
    return out


def _start_month(p: dict, moves: list[dict], exempt: set) -> str:
    own = [m["move_date"][:7] for m in moves if m.get("person_id") == p["id"] and _counted(m)]
    own += [ym for (pid, ym) in exempt if pid == p["id"]]
    created = str(p.get("created_at") or date.today().isoformat())[:7]
    return min([created, *own])


def _active_in(p: dict, ym: str, start: str) -> bool:
    if ym < start:
        return False
    return not p.get("removed_at") or ym < str(p["removed_at"])[:7]


def attribute(jar: dict, owner_id: int | None) -> dict[int, int | None]:
    """hareket id → katılımcı id (sayılan yatırmalar)."""
    ppl = jar.get("people") or []
    ids = {p["id"] for p in ppl}
    by_user = {p["user_id"]: p["id"] for p in ppl if p.get("user_id") is not None}
    active = [p for p in ppl if not p.get("removed_at")]
    fallback = by_user.get(owner_id) if owner_id is not None else None
    if fallback is None:
        fallback = (active or ppl or [{"id": None}])[0]["id"]
    out: dict[int, int | None] = {}
    for m in jar.get("moves") or []:
        if not _counted(m):
            continue
        if m.get("person_id") in ids:
            out[m["id"]] = m["person_id"]
        elif m.get("created_by") is not None and m["created_by"] in by_user:
            out[m["id"]] = by_user[m["created_by"]]
        else:
            out[m["id"]] = fallback
    return out


def month_shares(jar: dict, ym: str, act: list[dict], exempt: set) -> dict[int, float | None]:
    """O ayın kişi başı payı (tarihli elle paylar, muaf = 0)."""
    sc = scale_of(jar.get("unit"))
    t, _ = month_target(jar, ym)
    ppl = [{**p, "share": effective(_changes(jar, p["id"]), p.get("share"), ym)} for p in act]
    sh = split(t, ppl, sc)
    for p in act:
        if (p["id"], ym) in exempt:
            sh[p["id"]] = 0.0
    return sh


def status(jar: dict, today: date, names: dict[int, str] | None = None, me_id: int | None = None,
           owner_id: int | None = None) -> dict | None:
    """Kumbaranın bu ayki pay durumu (aktif katılımcı yoksa None).

    names: user_id → defterdeki ad (yoksa kaydedilen ad). Dönüş:
      {month, unit, scale, target, basis, total, paid, remind_day, carry_over,
       people: [{id, user_id, name, app, me, manual, share, paid, paid_for_month, to_past, carried_in, left, over,
                 state (tamam|eksik|bekliyor|paysiz|muaf), exempt, exempt_months, due [{month, amount}], due_total,
                 total, months_full, months_counted}],
       removed: [{id, name, total, months_full, months_counted, due, due_total}],
       allocations: {hareket id: {person_id, past [{month, amount}], month, month_amount, extra}},
       behind: [id…], all_done, history_months}"""
    ppl = jar.get("people") or []
    active = [p for p in ppl if not p.get("removed_at")]
    if not active:
        return None
    unit = jar.get("unit") or "TRY"
    sc = scale_of(unit)
    names = names or {}
    moves = sorted((m for m in jar.get("moves") or []), key=lambda m: (m["move_date"], m["id"]))
    exempt = {(e["person_id"], e["month"]) for e in jar.get("exempt") or []}
    who = attribute(jar, owner_id)
    carry = bool(jar.get("carry_over"))
    cur = today.strftime("%Y-%m")
    starts = {p["id"]: _start_month(p, moves, exempt) for p in ppl}
    first = min(starts.values())
    by_month: dict[str, list[dict]] = {}
    total_by: dict[int | None, int] = {}
    for m in moves:
        if m["id"] in who:
            by_month.setdefault(m["move_date"][:7], []).append(m)
            total_by[who[m["id"]]] = total_by.get(who[m["id"]], 0) + _q(m["amount"], sc)

    dues: dict[int, deque] = {p["id"]: deque() for p in ppl}      # [ay, açık miktar]
    credit: dict[int, int] = {p["id"]: 0 for p in ppl}
    share_q: dict[tuple[int, str], int] = {}
    opened: dict[tuple[int, str], int] = {}                         # ay sonunda açık kalan (sonra azalır)
    cur_info: dict[int, dict] = {}
    alloc: dict[int, dict] = {}
    hist_from = _add_months(cur, -HISTORY_MONTHS)
    counted_months: dict[int, list[str]] = {p["id"]: [] for p in ppl}
    ym = first
    while ym <= cur:
        act = [p for p in ppl if _active_in(p, ym, starts[p["id"]])]
        sh = month_shares(jar, ym, act, exempt) if act else {}
        remaining: dict[int, int] = {}
        info: dict[int, dict] = {}
        for p in act:
            s = sh.get(p["id"])
            k = _q(s, sc) if s else 0
            share_q[(p["id"], ym)] = k
            if (s or (p["id"], ym) in exempt) and ym >= hist_from and ym < cur:
                counted_months[p["id"]].append(ym)
            use = min(credit[p["id"]], k) if carry else 0
            credit[p["id"]] -= use
            remaining[p["id"]] = k - use
            info[p["id"]] = {"carried_in": use, "paid": 0, "to_past": 0, "for_month": 0, "over": 0}
        for m in by_month.get(ym, []):
            pid = who[m["id"]]
            amt = _q(m["amount"], sc)
            a = {"person_id": pid, "past": [], "month": ym, "month_amount": 0, "extra": 0}
            q = dues.get(pid)
            while amt and q:
                take = min(amt, q[0][1])
                a["past"].append({"month": q[0][0], "amount": take / sc})
                q[0][1] -= take
                opened[(pid, q[0][0])] = q[0][1]
                amt -= take
                if not q[0][1]:
                    q.popleft()
            if pid in remaining:
                take = min(amt, remaining[pid])
                remaining[pid] -= take
                a["month_amount"] = take / sc
                amt -= take
            a["extra"] = amt / sc
            if amt and carry and pid in credit:
                credit[pid] += amt
            if pid in info:
                i = info[pid]
                i["paid"] += _q(m["amount"], sc)
                i["to_past"] += _q(m["amount"], sc) - _q(a["month_amount"], sc) - amt
                i["for_month"] += _q(a["month_amount"], sc)
                i["over"] += amt
            alloc[m["id"]] = a
        if ym < cur:
            for p in act:
                left = remaining[p["id"]]
                opened[(p["id"], ym)] = left
                if left > 0:
                    dues[p["id"]].append([ym, left])
        else:
            for pid, i in info.items():
                cur_info[pid] = {**i, "left": remaining[pid]}
        ym = _add_months(ym, 1)

    def _due(pid):
        d = [{"month": a, "amount": b / sc} for a, b in dues[pid] if b > 0]
        return d, sum(b for _, b in dues[pid]) / sc

    def _hist(pid):
        ms = counted_months[pid]
        return sum(1 for x in ms if opened.get((pid, x), 0) <= 0), len(ms)

    def _name(p):
        return names.get(p["user_id"], p["name"]) if p.get("user_id") is not None else p["name"]

    cur_act = [p for p in active if _active_in(p, cur, starts[p["id"]]) or starts[p["id"]] > cur]
    sh_cur = month_shares(jar, cur, cur_act, exempt)
    out_people = []
    for p in cur_act:
        pid = p["id"]
        s = sh_cur.get(pid)
        i = cur_info.get(pid) or {"carried_in": 0, "paid": 0, "to_past": 0, "for_month": 0, "over": 0, "left": _q(s, sc) if s else 0}
        due, due_total = _due(pid)
        ex = (pid, cur) in exempt
        if ex:
            state = "muaf"
        elif not s:
            state = "paysiz"
        elif i["left"] <= 0:
            state = "tamam"
        elif i["for_month"] + i["carried_in"] > 0:
            state = "eksik"
        else:
            state = "bekliyor"
        full, cnt = _hist(pid)
        uid = p.get("user_id")
        out_people.append({
            "id": pid, "user_id": uid, "name": _name(p), "app": uid is not None, "me": uid is not None and uid == me_id,
            "manual": bool(effective(_changes(jar, pid), p.get("share"), cur)),
            "share": s, "paid": i["paid"] / sc, "paid_for_month": i["for_month"] / sc, "to_past": i["to_past"] / sc,
            "carried_in": i["carried_in"] / sc,
            "left": (i["left"] / sc) if s is not None and not ex else None,
            "over": (i["over"] / sc) if s is not None else None,
            "credit": credit.get(pid, 0) / sc if carry else 0.0,
            "state": state, "exempt": ex,
            "exempt_months": sorted(m for (q, m) in exempt if q == pid),
            "due": due, "due_total": due_total,
            "total": total_by.get(pid, 0) / sc, "months_full": full, "months_counted": cnt,
        })
    removed = []
    for p in ppl:
        if p.get("removed_at"):
            due, due_total = _due(p["id"])
            full, cnt = _hist(p["id"])
            removed.append({"id": p["id"], "name": _name(p), "total": total_by.get(p["id"], 0) / sc,
                            "months_full": full, "months_counted": cnt, "due": due, "due_total": due_total})
    target, basis = month_target(jar, cur)
    total = sum(_q(s, sc) for s in sh_cur.values() if s) / sc
    behind = [x["id"] for x in out_people if (x["left"] or 0) > 0 or x["due_total"] > 0]
    prev = _add_months(cur, -1)
    return {
        "month": cur, "unit": unit, "scale": sc, "target": target, "basis": basis, "total": total,
        "paid": sum(_q(x["paid"], sc) for x in out_people) / sc,
        # bu ayın paylarına sayılan (önceki aylara sayılan hariç, geçen aydan devreden fazla dahil): "Kasım: ₺4.000 / ₺6.000"
        "paid_month": sum(_q(x["paid_for_month"], sc) + _q(x["carried_in"], sc) for x in out_people) / sc,
        "remind_day": jar.get("remind_day") or DEFAULT_REMIND_DAY, "carry_over": carry,
        "people": out_people, "removed": removed,
        "allocations": {mid: a for mid, a in alloc.items() if a["month"] >= prev},
        "behind": behind, "all_done": bool(total) and not behind,
        "history_months": sum(1 for x in range(HISTORY_MONTHS) if _add_months(cur, -1 - x) >= first),
    }
