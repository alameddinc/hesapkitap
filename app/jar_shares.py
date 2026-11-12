"""Kumbara katılımcıları ve aylık pay (yetenek kumbara_shares).

Bir kumbaraya birden çok kişi düzenli para atar (ör. üç kardeş anneleri için; ailede iki kişi ortak hedefe). Kişi
defterdeki bir kullanıcı (jar_people.user_id) ya da uygulamayı kullanmayan biri (yalnız ad). Yalnız TL kumbarasında.

Ayın toplam payı (target):
  aylik  — katılımcıların ayda toplam atacağı (jars.share_monthly). Otomatik ayırmanın tutarı (jars.monthly) temel
           alınmaz: otomatik ayırma ekleyen/sahibin katkısıdır (kim attı boş → sahip), toplam pay değil
  hedef  — toplam yoksa hedef ve son tarihten: ay başındaki kalan / (bu ay dahil son tarih ayına kadar ay), tam
           liraya yukarı yuvarlı. Ay içinde atılan para o ayın payını değiştirmez (ay boyunca sabit).
  tamam  — hedefe ay başında ulaşılmış: pay yok
  None   — hedef/son tarih/aylık tutar yok: yalnız elle verilen paylar
Kişinin payı: elle verilmişse o (share); kalanlar (target − elle paylar) eşit bölünür. Kuruş hesabı: bölünemeyen
kalan kuruş sıradaki ilk kişiye eklenir, toplam hiç bozulmaz.

Kim attı: hareketin person_id'si; yoksa ekleyen kullanıcının (created_by) katılımcı satırı; o da yoksa defter
sahibinin satırı, o da yoksa sıradaki ilk kişi ("kim attı boşsa sahibi/ekleyen sayılır": eski istemci bozulmaz).
Sayılan: yatırma (amount > 0); başlangıç/aktarma ayırması, çekim ve borç ödemesi sayılmaz.

Geçmiş: kişinin başladığı aydan (eklendiği ay ya da ona bağlı ilk hareketin ayı) geçen aya kadar en çok 12 ay;
payı olan her ay sayılır, payı tam atılan ay "tam" sayılır. Paylar o aylar için bugünkü ayarlarla hesaplanır.

Değer ilkesi: yalnız hesap; faiz/mevduat/yatırım önerisi yok. Metinler istemcide (gizli tutar maskesi için):
web src/lib/kumbaraPay.ts, mobil src/lib/kumbaraPay.ts.
"""
from __future__ import annotations

import math
from datetime import date

from app import money
from app.jar_goal import INITIAL_NOTES, _add_months

DEFAULT_REMIND_DAY = 25
HISTORY_MONTHS = 12


def _kurus(x: float) -> int:
    return int(round(float(x) * 100))


def _counted(m: dict) -> bool:
    return m["amount"] > 0 and m.get("note") not in INITIAL_NOTES and not m.get("debt_payment_id") and m.get("cost") is None


def _months_between(a: str, b: str) -> int:
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + mb - ma


def month_target(jar: dict, ym: str) -> tuple[float | None, str | None]:
    """Ayın toplam payı ve kaynağı (aylik | hedef | tamam | None)."""
    if jar.get("share_monthly"):
        return money.money(jar["share_monthly"]), "aylik"
    goal, dl = jar.get("goal"), jar.get("deadline")
    if not goal or not dl:
        return None, None
    start = f"{ym}-01"
    before = sum(m["amount"] for m in jar.get("moves") or [] if m["move_date"] < start)
    left = goal - before
    if money.le(left, 0):
        return 0.0, "tamam"
    months = max(1, _months_between(ym, str(dl)[:7]) + 1)
    return float(math.ceil(round(left / months, 6))), "hedef"


def split(target: float | None, people: list[dict]) -> dict[int, float | None]:
    """Kişi başı pay: elle verilen sabit, kalan eşit; kuruş kalanı ilk eşit paylı kişiye. target None: eşit paylıların
    payı yok (None)."""
    manual = {p["id"]: _kurus(p["share"]) for p in people if p.get("share")}
    auto = [p for p in people if not p.get("share")]
    out: dict[int, float | None] = {pid: k / 100 for pid, k in manual.items()}
    if target is None:
        out.update({p["id"]: None for p in auto})
        return out
    rem = max(0, _kurus(target) - sum(manual.values()))
    if auto:
        base, extra = divmod(rem, len(auto))
        for i, p in enumerate(auto):
            out[p["id"]] = (base + (extra if i == 0 else 0)) / 100
    return out


def _start_month(p: dict, moves: list[dict]) -> str:
    own = [m["move_date"][:7] for m in moves if m.get("person_id") == p["id"] and _counted(m)]
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


def status(jar: dict, today: date, names: dict[int, str] | None = None, me_id: int | None = None,
           owner_id: int | None = None) -> dict | None:
    """Kumbaranın bu ayki pay durumu (katılımcı yoksa ya da TL kumbarası değilse None).

    names: user_id → defterdeki ad (yoksa kaydedilen ad). Dönüş:
      {month, target, basis, total, remind_day, people: [{id, user_id, name, app, me, manual, share, paid, left, over,
       state (tamam|eksik|bekliyor|paysiz), total, months_full, months_counted}], removed: [{id, name, total}],
       behind: [id…], all_done, history_months}"""
    if (jar.get("unit") or "TRY") != "TRY":
        return None
    ppl = jar.get("people") or []
    active = [p for p in ppl if not p.get("removed_at")]
    if not active:
        return None
    names = names or {}
    moves = jar.get("moves") or []
    who = attribute(jar, owner_id)
    cur = today.strftime("%Y-%m")
    paid: dict[tuple[int | None, str], int] = {}
    total_by: dict[int | None, int] = {}
    for m in moves:
        if m["id"] in who:
            k = (who[m["id"]], m["move_date"][:7])
            paid[k] = paid.get(k, 0) + _kurus(m["amount"])
            total_by[who[m["id"]]] = total_by.get(who[m["id"]], 0) + _kurus(m["amount"])
    target, basis = month_target(jar, cur)
    shares = split(target, active)

    # geçmiş: kişinin başladığı aydan geçen aya kadar (en çok HISTORY_MONTHS ay)
    starts = {p["id"]: _start_month(p, moves) for p in ppl}
    first = min(starts.values()) if starts else cur
    months = []
    ym = max(first, _add_months(cur, -HISTORY_MONTHS))
    while ym < cur:
        months.append(ym)
        ym = _add_months(ym, 1)
    full: dict[int, int] = {}
    counted: dict[int, int] = {}
    for ym in months:
        act = [p for p in ppl if _active_in(p, ym, starts[p["id"]])]
        if not act:
            continue
        t, _ = month_target(jar, ym)
        sh = split(t, act)
        for p in act:
            s = sh.get(p["id"])
            if not s:
                continue
            counted[p["id"]] = counted.get(p["id"], 0) + 1
            if paid.get((p["id"], ym), 0) >= _kurus(s):
                full[p["id"]] = full.get(p["id"], 0) + 1

    out_people = []
    for p in active:
        s = shares.get(p["id"])
        got = paid.get((p["id"], cur), 0)
        if not s:
            state, left, over = "paysiz", None, None
        else:
            left_k = max(0, _kurus(s) - got)
            left, over = left_k / 100, max(0, got - _kurus(s)) / 100
            state = "tamam" if left_k == 0 else "eksik" if got > 0 else "bekliyor"
        uid = p.get("user_id")
        out_people.append({
            "id": p["id"], "user_id": uid, "name": names.get(uid, p["name"]) if uid is not None else p["name"],
            "app": uid is not None, "me": uid is not None and uid == me_id, "manual": bool(p.get("share")),
            "share": s, "paid": got / 100, "left": left, "over": over, "state": state,
            "total": total_by.get(p["id"], 0) / 100,
            "months_full": full.get(p["id"], 0), "months_counted": counted.get(p["id"], 0),
        })
    removed = [{"id": p["id"], "name": names.get(p["user_id"], p["name"]) if p.get("user_id") is not None else p["name"],
                "total": total_by.get(p["id"], 0) / 100, "months_full": full.get(p["id"], 0),
                "months_counted": counted.get(p["id"], 0)} for p in ppl if p.get("removed_at")]
    total = sum(_kurus(s) for s in shares.values() if s) / 100
    behind = [x["id"] for x in out_people if x["state"] in ("eksik", "bekliyor")]
    return {
        "month": cur, "target": target, "basis": basis, "total": total,
        "paid": round(sum(x["paid"] for x in out_people), 2),
        "remind_day": jar.get("remind_day") or DEFAULT_REMIND_DAY,
        "people": out_people, "removed": removed, "behind": behind,
        "all_done": bool(total) and not behind, "history_months": len(months),
    }
