"""Aynı olabilecek iki sabit kalem: "Kira" (hiçbir ödemeye bağlanmamış) ve "Kira (ev sahibi)" (her ay ödemeye bağlı).

Bağlanmayan kalem her ay "henüz girilmedi" sayılır: Planlama'da, Özet'teki beklenen giderde ve onay bekleyenlerde
ikinci bir kira gibi durur. Burada yalnız ÖNERİ çıkar (Yapılacaklar + Planlama → Sabit kalemler); kendiliğinden
birleştirme yok: iki (ya da üç) ayrı kira ödeyen de var.

Aday çift: ikisi de bu dönemde etkin, aynı yön (gider/gelir), tutarları ±%10 içinde, adları benzer (kısa adın her
kelimesi ötekinde var ya da önek: "Kira" ⊂ "Kira (X)", "Ev kirası" ~ "Kira") ve kategorileri aynı ya da biri boş.
Kanıt (son WINDOW dönem, ikisinin de başladığı aylar):
  bağlanmadı  biri pencerede hiç ödemeye bağlanmamış, öteki en az 2 dönemde bağlı (o dönemlerde öbürü atlanmamış)
  aynı ödeme  ikisi de en az 2 dönemde bağlı ve her ortak dönemde bağlı kayıtlar aynı ödeme (tutar, ±3 gün, aynı kişi)
İkisinin aynı dönemde AYRI ödemeleri varsa (iki kira) öneri çıkmaz. "Ayrı kalsın" denen çift (settings
recurring_dup_separate) bir daha sorulmaz. Bir kalem en çok bir öneride görünür (tutarı en yakın eş).

Ortak defter: üye defterin sabit kalemlerini görür; kanıttaki bağlı kayıtlardan biri üyeye gizliyse (gizli hesap,
gizli kayıt) o çift üyeye hiç önerilmez (gizli ödemenin varlığı sızmasın). Yazma yetkisi uçlarda (middleware).
"""
import json
import re
from collections import defaultdict
from datetime import date

from app import db, money

WINDOW = 4          # son kaç dönem (bu dönem dahil)
MIN_MONTHS = 2      # kanıt için en az kaç dönem
AMOUNT_TOL = 0.10   # tutar farkı (büyüğe göre)
SAME_DAYS = 3       # "aynı ödeme" tarih toleransı
SAME_PCT = 1 / 100  # "aynı ödeme" tutar toleransı (en az ₺1)
SETTING = "recurring_dup_separate"


def _tokens(s: str | None) -> set[str]:
    from app.categorize import fold_key

    return {fold_key(w) for w in re.split(r"[^\wçğıöşüÇĞİÖŞÜ]+", s or "") if len(w) >= 2 and not w.isdigit()}


def _tok_match(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def similar_names(a: dict, b: dict) -> bool:
    ta, tb = _tokens(a["description"]), _tokens(b["description"])
    if not ta or not tb:
        return False
    small, big = sorted((ta, tb), key=len)
    covered = all(any(_tok_match(x, y) for y in big) for x in small)
    same_cat = bool(a.get("category")) and a.get("category") == b.get("category")
    return covered or (same_cat and any(_tok_match(x, y) for x in small for y in big))


def _cat_ok(a: dict, b: dict) -> bool:
    ca, cb = a.get("category") or "", b.get("category") or ""
    return not ca or not cb or ca == cb


def _amount_ok(x: float, y: float) -> bool:
    return x > 0 and y > 0 and abs(x - y) <= max(x, y) * AMOUNT_TOL + money.CENT


def _active(r: dict, month: str) -> bool:
    return r["start_month"] <= month and (not r["end_month"] or r["end_month"] >= month)


def _key(a: int, b: int) -> str:
    return f"{min(a, b)}:{max(a, b)}"


def separated() -> set[str]:
    try:
        v = json.loads(db.get_setting(SETTING) or "[]")
        return {str(x) for x in v} if isinstance(v, list) else set()
    except ValueError:
        return set()


def keep_separate(a: int, b: int) -> None:
    keys = separated() | {_key(a, b)}
    db.set_setting(SETTING, json.dumps(sorted(keys)))


def _same_payments(xs: list[dict], ys: list[dict]) -> bool:
    """İki kalemin aynı dönemdeki bağlı kayıtları aynı ödemeler mi (çift kayıt) yoksa ayrı ödemeler mi?"""
    if len(xs) != len(ys):
        return False
    left = list(ys)
    for x in xs:
        hit = next((y for y in left if abs(x["amount"] - y["amount"]) <= max(1.0, max(x["amount"], y["amount"]) * SAME_PCT)
                    and abs((date.fromisoformat(x["entry_date"][:10]) - date.fromisoformat(y["entry_date"][:10])).days) <= SAME_DAYS
                    and (not x.get("counterparty") or not y.get("counterparty")
                         or x["counterparty"].casefold() == y["counterparty"].casefold())), None)
        if not hit:
            return False
        left.remove(hit)
    return True


def _label(r: dict) -> str:
    return (r["description"] or "").strip()


def _tl(x: float) -> str:
    return money.tl(x, trim=True)


def _texts(keep: dict, drop: dict, reason: str) -> tuple[str, str]:
    amt = _tl(keep["amount"]) if money.eq(keep["amount"], drop["amount"]) else f"{_tl(drop['amount'])} / {_tl(keep['amount'])}"
    title = f"İki sabit kalem aynı olabilir: “{_label(drop)}” ve “{_label(keep)}”"
    why = ("Birini ödemeye bağlayamadık" if reason == "bagli_degil" else "İkisine de aynı ödemeler bağlanmış")
    return title, f"{amt} · {why}, Planlama'da iki kez sayılıyor olabilir."


def find(today: date | None = None) -> list[dict]:
    """Aynı olabilecek sabit kalem çiftleri (öneri). Her öğe: {id "sabit:<silinecek>:<kalacak>", reason, title, text,
    keep {id, description, category, amount, linked}, drop {...}, months}."""
    from app import book
    from app.finance import add_months, current_period

    today = today or date.today()
    cur = current_period(today)
    window = [add_months(cur, -i) for i in range(WINDOW - 1, -1, -1)]
    recs = [r for r in db.list_recurring() if _active(r, cur)]
    if len(recs) < 2:
        return []
    ids = [r["id"] for r in recs]
    with db.connect() as c:
        raw = db.rows(c.execute(f"SELECT * FROM entries WHERE recurring_id IN ({','.join('?' * len(ids))})", ids))
    visible = {e["id"] for e in book.filter_entries(raw)} if book.member() else {e["id"] for e in raw}
    linked: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    skipped: dict[int, set[str]] = defaultdict(set)
    total: dict[int, int] = defaultdict(int)
    hidden_in_window: set[int] = set()
    for e in raw:
        rid = e["recurring_id"]
        if e["skipped"]:
            skipped[rid].add(e["month"])
            continue
        total[rid] += 1
        if e["month"] in window:
            linked[rid][e["month"]].append(e)
            if e["id"] not in visible:
                hidden_in_window.add(rid)
    amount = {r["id"]: db.planned_amount(r, cur) for r in recs}
    sep = separated()

    cands = []
    for i, a in enumerate(recs):
        for b in recs[i + 1:]:
            if a["kind"] != b["kind"] or _key(a["id"], b["id"]) in sep or not _amount_ok(amount[a["id"]], amount[b["id"]]):
                continue
            if not _cat_ok(a, b) or not similar_names(a, b):
                continue
            if a["id"] in hidden_in_window or b["id"] in hidden_in_window:
                continue
            months = [m for m in window if _active(a, m) and _active(b, m)]
            la, lb = linked[a["id"]], linked[b["id"]]
            both = [m for m in months if la.get(m) and lb.get(m)]
            if any(not _same_payments(la[m], lb[m]) for m in both):
                continue  # aynı dönemde ayrı ödemeler: gerçekten iki kalem
            pick = None
            for x, y in ((a, b), (b, a)):
                lx, ly = linked[x["id"]], linked[y["id"]]
                y_only = [m for m in months if ly.get(m) and not lx.get(m) and m not in skipped[x["id"]]]
                if not any(lx.get(m) for m in months) and len(y_only) >= MIN_MONTHS:
                    pick = (y, x, "bagli_degil", y_only)
                    break
            if not pick and len(both) >= MIN_MONTHS:
                keep, drop = sorted((a, b), key=lambda r: (-total[r["id"]], not r.get("category"), r["id"]))
                pick = (keep, drop, "ayni_odeme", both)
            if pick:
                cands.append(pick)

    out, used = [], set()
    for keep, drop, reason, months in sorted(cands, key=lambda p: (abs(amount[p[0]["id"]] - amount[p[1]["id"]]), p[1]["id"])):
        if drop["id"] in used or keep["id"] in used:
            continue
        used |= {drop["id"], keep["id"]}

        def side(r: dict) -> dict:
            return {"id": r["id"], "description": r["description"], "category": r.get("category"),
                    "amount": amount[r["id"]], "linked": total[r["id"]]}

        k, d = side(keep), side(drop)
        title, text = _texts(k, d, reason)
        out.append({"id": f"sabit:{drop['id']}:{keep['id']}", "reason": reason, "title": title, "text": text,
                    "keep": k, "drop": d, "kind": keep["kind"], "months": months})
    return sorted(out, key=lambda x: x["id"])


class DupError(ValueError):
    pass


def merge(keep_id: int, drop_id: int) -> dict:
    """drop'u keep'e kat: drop'a bağlı kayıtlar keep'e geçer (keep'in zaten bağlı olduğu dönemdekiler bağsız kalır, atlama
    kayıtları silinir), keep'te olmayan aya özel tutarlar taşınır, keep'in boş kategori/hesap/eşleştirme anahtarı
    drop'tan dolar; drop silinir. Geri alma çağıranda (undo.batch)."""
    if keep_id == drop_id:
        raise DupError("Sabit kalem kendisiyle birleştirilemez")
    with db.connect() as c:
        k = c.execute("SELECT * FROM recurring WHERE id = ?", (keep_id,)).fetchone()
        d = c.execute("SELECT * FROM recurring WHERE id = ?", (drop_id,)).fetchone()
        if not k or not d:
            raise DupError("Sabit kalem bulunamadı")
        k, d = dict(k), dict(d)
        if k["kind"] != d["kind"]:
            raise DupError("Gelir ile gider birleştirilemez")
        taken = {m for (m,) in c.execute("SELECT DISTINCT month FROM entries WHERE recurring_id = ? AND skipped = 0", (keep_id,))}
        removed_skips = c.execute("DELETE FROM entries WHERE recurring_id = ? AND skipped = 1", (drop_id,)).rowcount
        rows_ = c.execute("SELECT id, month FROM entries WHERE recurring_id = ?", (drop_id,)).fetchall()
        move = [r["id"] for r in rows_ if r["month"] not in taken]
        unlink = [r["id"] for r in rows_ if r["month"] in taken]
        c.executemany("UPDATE entries SET recurring_id = ? WHERE id = ?", [(keep_id, i) for i in move])
        c.executemany("UPDATE entries SET recurring_id = NULL WHERE id = ?", [(i,) for i in unlink])
        ov = c.execute("INSERT OR IGNORE INTO recurring_overrides (recurring_id, month, amount) "
                       "SELECT ?, month, amount FROM recurring_overrides WHERE recurring_id = ?", (keep_id, drop_id)).rowcount
        fill = {f: d[f] for f in ("category", "account_id", "match_key", "note") if not k.get(f) and d.get(f)}
        if fill:
            c.execute(f"UPDATE recurring SET {', '.join(f'{f} = ?' for f in fill)} WHERE id = ?", (*fill.values(), keep_id))
        for t in ("recurring_overrides", "recurring_changes", "salary_changes"):
            c.execute(f"DELETE FROM {t} WHERE recurring_id = ?", (drop_id,))
    db.delete_recurring(drop_id)
    return {"ok": True, "kept": keep_id, "removed": drop_id, "moved": len(move), "unlinked": len(unlink),
            "skips_removed": removed_skips, "overrides_moved": ov, "kept_name": k["description"], "removed_name": d["description"]}
