"""Eşitleme: elle girilen kayıtlar ↔ içe aktarılan hesap dökümü ve kart ekstresi. Aynı para iki kez sayılmasın.

Elle girilen kayıt her zaman korunur (kategori, not, fiş fotoğrafı, kim ekledi); dökümdeki satırın banka açıklaması,
tarihi, tutarı ve bakiyesi `entry_matches` satırında ona bağlanır, ikinci kayıt oluşmaz. Geri almak için kaydın önceki
hali (prev) ve dökümden gelen kaydın kendisi (other) saklanır: "Eşleşmeyi geri al" ikisini yeniden ayırır.

Kurallar (hesap dökümü; kart ekstresinde kesin eşleşmeyi db.auto_link yapar, burada yalnız muhtemeller):
  KESİN    aynı hesap (elle girilende hesap yoksa olur; nakit hesabı olmaz), aynı yön, aynı tutar (kuruş),
           tarih ±EXACT_DAYS gün, iki tarafta da tek aday → kendiliğinden birleşir (how = otomatik).
           Aynı tutarda birden çok aday varsa karşı tarafın adını anan tek kayıt da kesin sayılır (havaleler).
  MUHTEMEL tarih ±NEAR_DAYS, tutar ±NEAR_PCT (komisyon, kuruş farkı) ya da birden çok aday → sorulur
           (Yapılacaklar: "esles:<kayıt id>:<e|t><id>"; e = dökümden gelen kayıt, t = ekstre satırı).
           "Aynı" birleştirir (how = onay), "Farklı" `match_rejects`e yazar: bir daha sorulmaz.
  ÖĞRENME  aynı dökümdeki kalıp (işyeri adı ya da kişi) ↔ aynı elle yazılan açıklama RULE_HITS kez onaylanınca
           `match_rules`e kural olur; sonraki tek adaylı muhtemel eşleşmeler kendiliğinden birleşir (how = kural).

Hesap eşleştirme: döküm hangi hesaba ait? IBAN → hesabın kayıtlı IBAN'ı; yoksa dökümlerin geldiği banka adı (alias);
yoksa aynı bankada elle açılmış hesap varsa sorulur (önizlemede ya da Yapılacaklar'da "hesap:<dökümden açılan>:<elle>").
Cevap hesaba yazılır (iban, alias): sonraki dökümler doğrudan gider.
"""
import bisect
import json
import re
from collections import defaultdict
from datetime import date, timedelta

from app import db, money
from app.textnorm import merchant_name, normalize

EXACT_DAYS = 2
NEAR_DAYS = 5
NEAR_PCT = 0.02
RULE_HITS = 2
ASK_LIMIT = 20
GENERIC_CATEGORIES = {None, "", "Diğer", "Kategorisiz", "Diğer Gelir"}
AY = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]


class SyncError(ValueError):
    pass


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)


def _tol(a: float, b: float) -> float:
    return max(money.CENT, round(max(abs(a), abs(b)) * NEAR_PCT, 2))


def _exact(a: float, b: float, d: int) -> bool:
    return money.eq(a, b) and d <= EXACT_DAYS


def _tl(x: float) -> str:
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "₺" + (s[:-3] if s.endswith(",00") else s)


def _dm(d: str) -> str:
    x = date.fromisoformat(d[:10])
    return f"{x.day} {AY[x.month - 1]}"


def _user_id() -> int | None:
    u = db.current_user()
    return u["id"] if u else None


# ---------------------------------------------------------------- adaylar

# Elle girilen ve dökümde karşılığı aranabilecek kayıtlar: kartla/ön ödemeli kartla ödenmemiş, nakit hesabında değil
_M_BANK = """SELECT e.* FROM entries e LEFT JOIN accounts a ON a.id = e.account_id
    WHERE e.import_hash IS NULL AND e.skipped = 0 AND e.wallet_id IS NULL AND e.debt_id IS NULL
      AND e.linked_txn_id IS NULL AND COALESCE(e.payment, '') NOT IN ('kredi_karti', 'kart')
      AND (a.id IS NULL OR a.kind = 'banka') AND COALESCE(e.category, '') <> ?"""
# Dökümden ya da banka e-posta bildiriminden (mail:) gelmiş, henüz bir elle kayda bağlanmamış kayıtlar (maaş sabit
# kalemi, borç hareketi, bölünmüş olanlar hariç). Bildirim önce gelip elle kayıt sonra girilince de eşleşir.
_I_BANK = """SELECT e.* FROM entries e
    WHERE e.import_hash IS NOT NULL AND (e.import_hash NOT LIKE '%:%' OR e.import_hash LIKE 'mail:%')
      AND e.note LIKE 'Hesap hareketi%'
      AND e.skipped = 0 AND e.wallet_id IS NULL AND e.debt_id IS NULL AND e.recurring_id IS NULL
      AND e.id NOT IN (SELECT entry_id FROM entry_matches)
      AND e.id NOT IN (SELECT entry_id FROM splits WHERE entry_id IS NOT NULL)"""
_M_CARD = """SELECT * FROM entries WHERE payment = 'kredi_karti' AND kind = 'gider' AND linked_txn_id IS NULL
    AND skipped = 0 AND COALESCE(category, '') <> ?"""


def _rejects(c) -> set[tuple[int, str]]:
    return {(r[0], r[1]) for r in c.execute("SELECT entry_id, other FROM match_rejects")}


def bank_desc(i: dict) -> str:
    """Dökümden gelen kaydın bankadaki asıl açıklaması (kısaltıldıysa notta tam hali durur)."""
    note = i.get("note") or ""
    if note.startswith("Hesap hareketi · "):
        return note[len("Hesap hareketi · "):]
    return i.get("description") or ""


def _other_person(m: dict, cp: str | None, people: set[str]) -> bool:
    """Elle girilen kayıt başka bir kişiden mi bahsediyor? (Veli'ye yazılanı Ali'ye giden havaleyle eşleme)"""
    return any(db._mentions(m, p) for p in people if p and p != cp) and not (cp and db._mentions(m, cp))


def _window(items: list[dict], amount: float) -> list[dict]:
    """Tutara göre sıralı listede ±NEAR_PCT aralığı."""
    keys = [x["amount"] for x in items]
    lo = bisect.bisect_left(keys, amount * (1 - NEAR_PCT) - money.CENT)
    hi = bisect.bisect_right(keys, amount / (1 - NEAR_PCT) + money.CENT)
    return items[lo:hi]


def _bank_pairs(c, rejects: set) -> list[dict]:
    ms = db.rows(c.execute(_M_BANK, (db.JAR_BUY_CATEGORY,)))
    if not ms:
        return []
    is_ = db.rows(c.execute(_I_BANK))
    people = {x["counterparty"] for x in is_ + ms if x.get("counterparty")}
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for i in sorted(is_, key=lambda x: x["amount"]):
        by_kind[i["kind"]].append(i)
    out = []
    for m in ms:
        for i in _window(by_kind.get(m["kind"], []), m["amount"]):
            if m["account_id"] and m["account_id"] != i["account_id"]:
                continue
            d = _days(m["entry_date"], i["entry_date"])
            diff = round(i["amount"] - m["amount"], 2)
            if d > NEAR_DAYS or abs(diff) > _tol(m["amount"], i["amount"]) or (m["id"], f"h:{i['import_hash']}") in rejects:
                continue
            if _other_person(m, i.get("counterparty"), people):
                continue
            out.append({"source": "dokum", "m": m, "o": i, "okey": f"e{i['id']}", "days": d, "diff": diff,
                        "exact": _exact(m["amount"], i["amount"], d),
                        "strong": bool(i.get("counterparty")) and db._mentions(m, i["counterparty"])})
    return out


def _card_txns(c) -> list[dict]:
    return db.rows(c.execute(
        """SELECT t.id, t.txn_date, t.description, t.amount, t.category, t.category_manual, t.note, t.card, t.hidden,
                  t.txn_type, s.card_no AS account, s.bank
           FROM transactions t JOIN statements s ON s.id = t.statement_id
           WHERE t.txn_type = 'harcama' AND t.is_deleted = 0
             AND t.id NOT IN (SELECT linked_txn_id FROM entries WHERE linked_txn_id IS NOT NULL)"""))


def _card_pairs(c, rejects: set) -> list[dict]:
    ms = db.rows(c.execute(_M_CARD, (db.JAR_BUY_CATEGORY,)))
    if not ms:
        return []
    ts = sorted(_card_txns(c), key=lambda t: t["amount"])
    out = []
    for m in ms:
        n = int(m.get("installments") or 1)
        target = round(m["amount"] / n, 2)  # taksitli: ekstreye ilk taksit yansır
        last4 = m.get("card_last4")
        for t in _window(ts, target):
            if last4 and last4 not in (t["account"][-4:], (t["card"] or "")[-4:]):
                continue
            d = _days(m["entry_date"], t["txn_date"])
            diff = round(t["amount"] - target, 2)
            if d > NEAR_DAYS or abs(diff) > _tol(target, t["amount"]) or (m["id"], f"t:{t['id']}") in rejects:
                continue
            # kesin olanları auto_link zaten bağladı; kalan kart adayları sorulur (öğrenilmiş kural hariç)
            out.append({"source": "ekstre", "m": m, "o": t, "okey": f"t{t['id']}", "days": d, "diff": diff,
                        "exact": False, "strong": False})
    return out


# ---------------------------------------------------------------- öğrenme

def _pattern(p: dict) -> str:
    o = p["o"]
    if p["source"] == "dokum":
        if o.get("counterparty"):
            return "KISI:" + normalize(o["counterparty"])
        return merchant_name(bank_desc(o))
    return merchant_name(o["description"])


def _active_rules(c) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in db.rows(c.execute("SELECT * FROM match_rules WHERE hits >= ?", (RULE_HITS,))):
        out[(r["source"], r["pattern"])].append(r)
    return out


def _rule_for(p: dict, rules: dict) -> dict | None:
    key = normalize(p["m"]["description"] or "")
    cat = p["m"].get("category")
    for r in rules.get((p["source"], _pattern(p)), []):
        if r["entry_key"] == key or (cat not in GENERIC_CATEGORIES and r["category"] == cat):
            return r
    return None


def _learn(c, p: dict) -> dict | None:
    """Onaylanan eşleşmeyi say; RULE_HITS'e ulaşınca kural olur. Yeni etkinleşen kural döner."""
    pattern, key = _pattern(p), normalize(p["m"]["description"] or "")
    if not pattern or not key:
        return None
    label = p["o"]["counterparty"] if p["source"] == "dokum" and p["o"].get("counterparty") else \
        (bank_desc(p["o"]) if p["source"] == "dokum" else p["o"]["description"])
    c.execute("""INSERT INTO match_rules (source, pattern, entry_key, bank_label, entry_label, category)
                 VALUES (?, ?, ?, ?, ?, ?)
                 ON CONFLICT(source, pattern, entry_key) DO UPDATE SET hits = hits + 1, category = excluded.category,
                 entry_label = excluded.entry_label, updated_at = CURRENT_TIMESTAMP""",
              (p["source"], pattern, key, label[:120], p["m"]["description"][:120], p["m"].get("category")))
    r = c.execute("SELECT * FROM match_rules WHERE source = ? AND pattern = ? AND entry_key = ?",
                  (p["source"], pattern, key)).fetchone()
    return dict(r) if r and r["hits"] == RULE_HITS else None


def list_rules() -> list[dict]:
    with db.connect() as c:
        return db.rows(c.execute("SELECT id, source, pattern, entry_key, bank_label, entry_label, category, hits, used, "
                                 "created_at, updated_at FROM match_rules WHERE hits >= ? ORDER BY updated_at DESC, id DESC",
                                 (RULE_HITS,)))


def delete_rule(rule_id: int) -> bool:
    with db.connect() as c:
        return c.execute("DELETE FROM match_rules WHERE id = ?", (rule_id,)).rowcount > 0


# ---------------------------------------------------------------- eşleşme kararları

def find_pairs() -> list[dict]:
    """Tüm aday çiftler; her birinde auto = otomatik | kural | None (sorulacak) ve reason (tarih | tutar | aday)."""
    with db.connect() as c:
        rejects = _rejects(c)
        pairs = _bank_pairs(c, rejects) + _card_pairs(c, rejects)
        rules = _active_rules(c) if pairs else {}
    by_m: dict[int, list[dict]] = defaultdict(list)
    by_o: dict[tuple, list[dict]] = defaultdict(list)
    for p in pairs:
        by_m[p["m"]["id"]].append(p)
        by_o[(p["source"], p["okey"])].append(p)
    for p in pairs:
        ms, os_ = by_m[p["m"]["id"]], by_o[(p["source"], p["okey"])]
        uniq = len(ms) == 1 and len(os_) == 1
        strong = p["strong"] and sum(q["strong"] for q in ms) == 1 and sum(q["strong"] for q in os_) == 1
        p["candidates"] = max(len(ms), len(os_))
        p["auto"], p["rule"] = None, None
        if p["exact"] and (uniq or strong):
            p["auto"] = "otomatik"
        elif uniq or strong:
            r = _rule_for(p, rules)
            if r:
                p["auto"], p["rule"] = "kural", r
        p["reason"] = "aday" if not uniq else "tutar" if not money.is_zero(p["diff"]) else "tarih"
    return pairs


def _record(c, entry_id: int, source: str, how: str, *, import_hash=None, txn_id=None, bank_date=None, bank_desc_=None,
            bank_amount=None, balance=None, account_id=None, rule_id=None, prev=None, other=None) -> None:
    c.execute("""INSERT OR REPLACE INTO entry_matches (entry_id, source, how, import_hash, txn_id, bank_date, bank_desc,
                 bank_amount, balance, account_id, rule_id, prev, other, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (entry_id, source, how, import_hash, txn_id, bank_date, (bank_desc_ or "")[:500], bank_amount, balance,
               account_id, rule_id, json.dumps(prev, ensure_ascii=False, default=str) if prev is not None else None,
               json.dumps(other, ensure_ascii=False, default=str) if other is not None else None, _user_id()))


_PREV_FIELDS = ("amount", "account_id", "counterparty", "import_hash", "bank_doc_id")


def _merge_bank_entry(c, m: dict, i: dict, how: str, rule_id: int | None = None) -> None:
    """Dökümden gelen kayıt (i) elle girilene (m) katılır; i silinir, m bankadaki tutarı alır."""
    c.execute("UPDATE entries SET import_hash = ?, amount = ?, account_id = COALESCE(account_id, ?), "
              "counterparty = COALESCE(counterparty, ?), bank_doc_id = COALESCE(bank_doc_id, ?) WHERE id = ?",
              (i["import_hash"], i["amount"], i["account_id"], i.get("counterparty"), i.get("bank_doc_id"), m["id"]))
    c.execute("UPDATE bills SET entry_id = ? WHERE entry_id = ?", (m["id"], i["id"]))
    c.execute("DELETE FROM entries WHERE id = ?", (i["id"],))
    _record(c, m["id"], "dokum", how, import_hash=i["import_hash"], bank_date=i["entry_date"], bank_desc_=bank_desc(i),
            bank_amount=i["amount"], account_id=i["account_id"], rule_id=rule_id,
            prev={k: m.get(k) for k in _PREV_FIELDS}, other=i)


def _merge(c, p: dict, how: str) -> None:
    if p["source"] == "dokum":
        _merge_bank_entry(c, p["m"], p["o"], how, (p.get("rule") or {}).get("id"))
    else:
        db._apply_link(c, p["m"], p["o"], how)
    if how == "kural" and p.get("rule"):
        c.execute("UPDATE match_rules SET used = used + 1 WHERE id = ?", (p["rule"]["id"],))


def reconcile() -> dict:
    """Kesin ve kuralla eşleşen çiftleri birleştir (içe aktarmadan, yeni kayıttan, hesap birleştirmeden sonra)."""
    out = {"otomatik": 0, "kural": 0}
    for _ in range(3):  # bir birleşme başka bir çifti tek adaylı yapabilir
        todo = [p for p in find_pairs() if p["auto"]]
        if not todo:
            break
        used: set = set()
        with db.connect() as c:
            for p in todo:
                k1, k2 = ("m", p["m"]["id"]), (p["source"], p["okey"])
                if k1 in used or k2 in used:
                    continue
                used |= {k1, k2}
                _merge(c, p, p["auto"])
                out[p["auto"]] += 1
    return out


def _pair(entry_id: int, other: str) -> dict:
    p = next((p for p in find_pairs() if p["m"]["id"] == entry_id and p["okey"] == other), None)
    if not p:
        raise SyncError("Bu eşleşme artık geçerli değil (kayıtlardan biri değişti ya da eşleşti)")
    return p


def visible_pair(p: dict) -> bool:
    """Ortak defter üyesi iki tarafı da görebiliyor mu?"""
    from app import book

    if not book.member():
        return True
    if not book.filter_entries([p["m"]]):
        return False
    if p["source"] == "dokum":
        return bool(book.filter_entries([p["o"]]))
    return bool(book.filter_card_txns([p["o"]]))


def decide(entry_id: int, other: str, same: bool) -> dict:
    """Muhtemel eşleşme sorusunun cevabı. same: birleştir (ve öğren); değilse bir daha sorma."""
    p = _pair(entry_id, other)
    if not visible_pair(p):
        raise SyncError("Bu eşleşme artık geçerli değil (kayıtlardan biri değişti ya da eşleşti)")
    with db.connect() as c:
        if not same:
            key = f"h:{p['o']['import_hash']}" if p["source"] == "dokum" else f"t:{p['o']['id']}"
            c.execute("INSERT OR IGNORE INTO match_rejects (entry_id, other, created_by) VALUES (?, ?, ?)",
                      (entry_id, key, _user_id()))
            return {"ok": True, "same": False}
        _merge(c, p, "onay")
        learned = _learn(c, p)
    res = {"ok": True, "same": True, "entry_id": entry_id}
    if learned:
        res["learned"] = {"id": learned["id"], "bank": learned["bank_label"], "entry": learned["entry_label"]}
    rec = reconcile()  # yeni kural başka bekleyenleri de birleştirebilir
    if rec["kural"]:
        res["auto_merged"] = rec["kural"]
    return res


def unmatch(entry_id: int) -> dict:
    """Eşleşmeyi geri al: iki ayrı kayda döner; bu çift bir daha kendiliğinden birleşmez ve sorulmaz."""
    with db.connect() as c:
        e = c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not e:
            raise KeyError(entry_id)
        e = dict(e)
        mrow = c.execute("SELECT * FROM entry_matches WHERE entry_id = ?", (entry_id,)).fetchone()
        mrow = dict(mrow) if mrow else None
        prev = json.loads(mrow["prev"]) if mrow and mrow["prev"] else {}
        other = json.loads(mrow["other"]) if mrow and mrow["other"] else None
        if e["linked_txn_id"]:  # kart ekstresi
            tid = e["linked_txn_id"]
            if mrow and mrow["source"] == "ekstre" and other:
                c.execute("UPDATE transactions SET category = ?, category_manual = ?, note = ? WHERE id = ?",
                          (other.get("category"), other.get("category_manual") or 0, other.get("note"), tid))
            c.execute("UPDATE entries SET linked_txn_id = NULL, amount = COALESCE(?, amount) WHERE id = ?",
                      (prev.get("amount"), entry_id))
            c.execute("INSERT OR IGNORE INTO match_rejects (entry_id, other, created_by) VALUES (?, ?, ?)",
                      (entry_id, f"t:{tid}", _user_id()))
            result = {"ok": True, "source": "ekstre", "txn_id": tid}
        elif mrow and mrow["source"] == "dokum" and other:
            c.execute(f"UPDATE entries SET {', '.join(f'{k} = ?' for k in _PREV_FIELDS)} WHERE id = ?",
                      (*(prev.get(k) for k in _PREV_FIELDS), entry_id))
            row = {k: other.get(k) for k in db.ENTRY_FIELDS + ("bank_doc_id",) if k in other}
            row.setdefault("month", db.period_of(row["entry_date"]))
            row["bank_doc_id"] = row.get("bank_doc_id") or (e["bank_doc_id"] if not prev.get("bank_doc_id") else None)
            if not row.get("category"):
                row["category"] = db.categorize(row["description"], "harcama", db.load_rules(c)) if row["kind"] == "gider" \
                    else "Diğer Gelir"
            cols = ", ".join(row)
            new_id = c.execute(f"INSERT INTO entries ({cols}) VALUES ({', '.join('?' * len(row))})",
                               tuple(row.values())).lastrowid
            c.execute("INSERT OR IGNORE INTO match_rejects (entry_id, other, created_by) VALUES (?, ?, ?)",
                      (entry_id, f"h:{mrow['import_hash']}", _user_id()))
            result = {"ok": True, "source": "dokum", "other_id": new_id}
        else:
            raise SyncError("Bu kayıt dökümle eşleşmiş görünmüyor ya da eşleşme bu sürümden eski; geri alınamıyor")
        if mrow:
            if mrow["how"] == "kural" and mrow["rule_id"]:  # yanlış birleştiren kural zayıflar
                c.execute("UPDATE match_rules SET hits = hits - 1 WHERE id = ?", (mrow["rule_id"],))
            c.execute("DELETE FROM entry_matches WHERE entry_id = ?", (entry_id,))
    return result


# ---------------------------------------------------------------- içe aktarma sırasında (satır henüz kayıt değil)

def pick_for_row(c, r: dict, kind: str, amount: float, acc_id: int | None, who: str | None, people: set[str],
                 batch: list[dict]) -> dict | None:
    """Dökümdeki bu satırla KESİN eşleşen elle girilmiş kayıt (yoksa None; muhtemeller sonra sorulur)."""
    d = date.fromisoformat(r["date"])
    lo, hi = (d - timedelta(days=NEAR_DAYS)).isoformat(), (d + timedelta(days=NEAR_DAYS)).isoformat()
    rejects = _rejects(c)
    ms = []
    for m in db.rows(c.execute(_M_BANK + " AND e.kind = ? AND e.entry_date BETWEEN ? AND ?",
                               (db.JAR_BUY_CATEGORY, kind, lo, hi))):
        if acc_id and m["account_id"] and m["account_id"] != acc_id:
            continue
        if abs(m["amount"] - amount) > _tol(m["amount"], amount) or _other_person(m, who, people):
            continue
        if (m["id"], f"h:{r['hash']}") in rejects:
            continue
        ms.append(m)
    if not ms:
        return None
    exact = lambda m: _exact(m["amount"], amount, _days(m["entry_date"], r["date"]))  # noqa: E731
    strong = [m for m in ms if who and db._mentions(m, who)]
    if strong:
        return strong[0] if len(strong) == 1 and exact(strong[0]) else None
    if len(ms) != 1 or not exact(ms[0]):
        return None
    m = ms[0]
    # karşı tarafta da tek aday olmalı: bu dökümdeki başka bir satır ya da önceden gelmiş başka bir kayıt aynı tutardaysa sor
    for x in batch:
        if x["hash"] == r["hash"] or (x["amount"] > 0) != (kind == "gelir"):
            continue
        if _days(m["entry_date"], x["date"]) <= NEAR_DAYS and abs(abs(x["amount"]) - m["amount"]) <= _tol(m["amount"], x["amount"]):
            return None
    for i in db.rows(c.execute(_I_BANK + " AND e.kind = ? AND e.entry_date BETWEEN ? AND ?",
                               (kind, (date.fromisoformat(m["entry_date"]) - timedelta(days=NEAR_DAYS)).isoformat(),
                                (date.fromisoformat(m["entry_date"]) + timedelta(days=NEAR_DAYS)).isoformat()))):
        if (not m["account_id"] or i["account_id"] == m["account_id"]) and \
                abs(i["amount"] - m["amount"]) <= _tol(m["amount"], i["amount"]):
            return None
    return m


def attach_row(c, m: dict, r: dict, kind: str, amount: float, acc_id: int | None, who: str | None) -> None:
    """Satırı elle girilen kayda bağla (yeni kayıt açılmaz). Geri alınınca satır kendi kaydı olarak açılır."""
    payment = "havale" if who or re.search(r"EFT|HAVALE|FAST|TRANSFER", normalize(r["description"])) else "nakit"
    desc = (who or r["description"])[:120]
    other = {"entry_date": r["date"], "description": desc, "amount": amount, "kind": kind,
             "payment": payment if kind == "gider" else None, "import_hash": r["hash"], "counterparty": who,
             "account_id": acc_id, "note": "Hesap hareketi" + ("" if desc == r["description"] else f" · {r['description']}")}
    c.execute("UPDATE entries SET import_hash = ?, counterparty = COALESCE(counterparty, ?), "
              "account_id = COALESCE(account_id, ?) WHERE id = ?", (r["hash"], who, acc_id, m["id"]))
    _record(c, m["id"], "dokum", "otomatik", import_hash=r["hash"], bank_date=r["date"], bank_desc_=r["description"],
            bank_amount=amount, balance=r.get("balance"), account_id=acc_id,
            prev={k: m.get(k) for k in _PREV_FIELDS}, other=other)


# ---------------------------------------------------------------- arayüz: rozet ve sorular

def match_info() -> dict[int, dict]:
    """entry_id → {source, how, date, description, amount, balance, account}: "Dökümle eşleşti · Garanti · 28 Eki"."""
    def load():
        out: dict[int, dict] = {}
        with db.connect() as c:
            for r in c.execute("SELECT m.*, a.name AS account_name FROM entry_matches m "
                               "LEFT JOIN accounts a ON a.id = m.account_id WHERE m.source = 'dokum'"):
                out[r["entry_id"]] = {"source": "dokum", "how": r["how"], "date": r["bank_date"], "description": r["bank_desc"],
                                      "amount": r["bank_amount"], "balance": r["balance"], "account": r["account_name"],
                                      "can_undo": bool(r["other"])}
            how = {r[0]: r[1] for r in c.execute("SELECT entry_id, how FROM entry_matches WHERE source = 'ekstre'")}
            for r in c.execute("SELECT e.id, t.txn_date, t.description, t.amount, s.bank, s.card_no FROM entries e "
                               "JOIN transactions t ON t.id = e.linked_txn_id JOIN statements s ON s.id = t.statement_id"):
                out[r["id"]] = {"source": "ekstre", "how": how.get(r["id"], "otomatik"), "date": r["txn_date"],
                                "description": r["description"], "amount": r["amount"], "balance": None,
                                "account": f"{r['bank']} ••{r['card_no'][-4:]}", "can_undo": True}
        return out
    return db._cached("entry_matches", load)


def _side_entry(c, e: dict, accs: dict) -> dict:
    return {"id": e["id"], "uid": f"m{e['id']}", "description": e["description"], "amount": e["amount"],
            "date": e["entry_date"], "kind": e["kind"], "category": e.get("category"), "note": e.get("note"),
            "account": accs.get(e.get("account_id")) or {"havale": "Havale", "nakit": "Nakit / banka kartı",
                                                          "kredi_karti": "Kredi kartı"}.get(e.get("payment") or "", None),
            "created_by": e.get("created_by"), "receipt": bool(e.get("receipt_file"))}


def questions(limit: int = ASK_LIMIT) -> list[dict]:
    """Yapılacaklar: "Bu ikisi aynı mı?" (en yeni elle kayıttan başlayarak)."""
    ps = [p for p in find_pairs() if not p["auto"] and visible_pair(p)]
    if not ps:
        return []
    ps.sort(key=lambda p: (p["m"]["entry_date"], p["m"]["id"], -abs(p["diff"])), reverse=True)
    with db.connect() as c:
        accs = {r[0]: r[1] for r in c.execute("SELECT id, name FROM accounts")}
    out = []
    for p in ps[:limit]:
        m, o = p["m"], p["o"]
        if p["source"] == "dokum":
            other = {"uid": f"m{o['id']}", "id": o["id"], "description": bank_desc(o), "amount": o["amount"],
                     "date": o["entry_date"], "account": accs.get(o.get("account_id")) or "Hesap dökümü", "source": "dokum"}
        else:
            other = {"uid": f"c{o['id']}", "id": o["id"], "description": o["description"], "amount": o["amount"],
                     "date": o["txn_date"], "account": f"{o['bank']} ••{o['account'][-4:]}", "source": "ekstre"}
        tag = f"{p['candidates']} aday" if p["reason"] == "aday" else \
            f"{_tl(p['diff'])} fark" if p["reason"] == "tutar" else f"{p['days']} gün arayla"
        where = "dökümdeki" if p["source"] == "dokum" else "ekstredeki"
        what = "gelir" if m["kind"] == "gelir" else "harcama"
        out.append({
            "id": f"esles:{m['id']}:{p['okey']}", "kind": "esles", "severity": "info",
            "title": "Bu ikisi aynı mı?",
            "text": f"Elle girdiğin “{m['description']}” {_tl(m['amount'])} ile {where} “{other['description'][:60]}” "
                    f"{_tl(other['amount'])} aynı {what} mı? Aynıysa birleştiririz, iki kez sayılmaz.",
            "cta": "Aynı", "route": "islemler", "action": "match", "due": m["entry_date"], "tag": tag,
            "period": m["month"], "entry_id": m["id"], "other": p["okey"], "reason": p["reason"], "source": p["source"],
            "pair": {"entry": _side_entry(c, m, accs), "other": other},
        })
    return out


# ---------------------------------------------------------------- hesap eşleştirme

_IBAN = re.compile(r"IBAN\s*(?:NO|NUMARASI)?\s*[:.]?\s*(TR\s?\d{2}(?:\s?\d{4}){5}\s?\d{2})", re.I)


def find_iban(text: str) -> str | None:
    """Dökümün başlığındaki hesap IBAN'ı (açıklamalardaki karşı taraf IBAN'ları değil: "IBAN" etiketiyle)."""
    m = _IBAN.search(text or "")
    return re.sub(r"\s", "", m.group(1)).upper() if m else None


def find_iban_rows(rows: list[list[str]]) -> str | None:
    """Excel/CSV: ilk satırlarda "IBAN" etiketli hücre."""
    for row in rows[:40]:
        line = " ".join(str(x) for x in row if x)
        if (iban := find_iban(line)):
            return iban
        for i, cell in enumerate(row):
            if "IBAN" in normalize(str(cell)) and i + 1 < len(row) and re.fullmatch(r"TR[\d ]{24,32}", str(row[i + 1]).strip()):
                return re.sub(r"\s", "", str(row[i + 1])).upper()
    return None


def _bank_accounts(c) -> list[dict]:
    return db.rows(c.execute("SELECT * FROM accounts WHERE kind = 'banka' ORDER BY id"))


def _has(c, acc_id: int, imported: bool) -> bool:
    q = ("import_hash IS NOT NULL AND import_hash NOT LIKE '%:%'" if imported else "import_hash IS NULL")
    return c.execute(f"SELECT 1 FROM entries WHERE account_id = ? AND {q} LIMIT 1", (acc_id,)).fetchone() is not None


def _is_manual(c, a: dict) -> bool:
    return a.get("origin") == "elle" or (a.get("origin") is None and not _has(c, a["id"], True))


def _is_dokum(c, a: dict) -> bool:
    return a.get("origin") == "dokum" or (a.get("origin") is None and _has(c, a["id"], True) and not _has(c, a["id"], False))


def _separate(c, a: int | None, b: int) -> bool:
    return bool(a) and c.execute("SELECT 1 FROM account_separate WHERE (a = ? AND b = ?) OR (a = ? AND b = ?)",
                                 (a, b, b, a)).fetchone() is not None


def _acc_out(a: dict) -> dict:
    return {"id": a["id"], "name": a["name"], "balance": a["balance"], "as_of": a["as_of"],
            "iban_last4": (a.get("iban") or "")[-4:] or None}


def resolve_account(bank: str | None, iban: str | None = None) -> dict:
    """Döküm hangi hesaba gidecek? status: exact (account_id) | ask (candidates, default_id) | new (name) | none."""
    from app.bank_import import short_bank

    name = short_bank(bank) if bank else None
    with db.connect() as c:
        accs = _bank_accounts(c)
        if iban:
            hit = next((a for a in accs if a.get("iban") == iban), None)
            if hit:
                return {"status": "exact", "account_id": hit["id"], "name": hit["name"], "via": "iban", "iban_last4": iban[-4:]}
        if not name:
            return {"status": "none", "candidates": [_acc_out(a) for a in accs], "iban_last4": iban[-4:] if iban else None}
        fits = lambda a: not iban or not a.get("iban") or a["iban"] == iban  # noqa: E731
        alias = next((a for a in accs if a.get("alias") == name and fits(a)), None)
        if alias:
            return {"status": "exact", "account_id": alias["id"], "name": alias["name"], "via": "alias",
                    "iban_last4": iban[-4:] if iban else None}
        named = next((a for a in accs if a["name"] == name and fits(a)), None)
        cands = [a for a in accs if a is not named and a["name"] != name and short_bank(a["name"]) == name and fits(a)
                 and not a.get("alias") and not (iban and a.get("iban")) and _is_manual(c, a)
                 and not _separate(c, named["id"] if named else None, a["id"])]
        if cands:
            return {"status": "ask", "bank": name, "default_id": named["id"] if named else None,
                    "candidates": [_acc_out(a) for a in cands + ([named] if named else [])],
                    "iban_last4": iban[-4:] if iban else None}
        if named:
            return {"status": "exact", "account_id": named["id"], "name": named["name"], "via": "name",
                    "iban_last4": iban[-4:] if iban else None}
        taken = any(a["name"] == name for a in accs)  # aynı bankada başka IBAN'lı hesap: ikinci hesap
        return {"status": "new", "name": f"{name} ••{iban[-4:]}" if taken and iban else name,
                "iban_last4": iban[-4:] if iban else None}


def account_for_import(bank: str | None, iban: str | None, account_id: int | None = None,
                       new_account: bool = False) -> int | None:
    """İçe aktarmada kullanılacak hesap id'si; seçim/IBAN hesaba yazılır ki sonraki döküm doğrudan gelsin."""
    from app.bank_import import short_bank

    name = short_bank(bank) if bank else None
    if account_id:
        with db.connect() as c:
            a = c.execute("SELECT * FROM accounts WHERE id = ? AND kind = 'banka'", (account_id,)).fetchone()
            if not a:
                raise SyncError("Hesap bulunamadı")
            c.execute("UPDATE accounts SET iban = COALESCE(iban, ?), alias = CASE WHEN alias IS NULL AND name <> ? "
                      "THEN ? ELSE alias END WHERE id = ?", (iban, name or "", name, account_id))
        return account_id
    if not name:
        return None
    res = resolve_account(bank, iban)
    if res["status"] == "exact":
        acc = res["account_id"]
    elif res["status"] == "ask":
        acc = res["default_id"] or db.ensure_account(name)  # cevap yok: Yapılacaklar'da sorulur
        if new_account:
            with db.connect() as c:
                for x in res["candidates"]:
                    if x["id"] != acc:
                        c.execute("INSERT OR IGNORE INTO account_separate (a, b) VALUES (?, ?)", (acc, x["id"]))
    else:
        acc = db.ensure_account(res["name"])
    if iban:
        with db.connect() as c:
            c.execute("UPDATE accounts SET iban = COALESCE(iban, ?) WHERE id = ?", (iban, acc))
    return acc


def account_questions() -> list[dict]:
    """Dökümden açılmış hesap + aynı bankada elle açılmış hesap: "Bu döküm 'Garanti' hesabına mı ait?"."""
    from app.bank_import import short_bank

    out = []
    with db.connect() as c:
        accs = _bank_accounts(c)
        for a in accs:
            if not _is_dokum(c, a):
                continue
            bank = short_bank(a["name"])
            for b in accs:
                if b["id"] == a["id"] or short_bank(b["name"]) != bank or b.get("alias") or _separate(c, a["id"], b["id"]):
                    continue
                if (a.get("iban") and b.get("iban") and a["iban"] != b["iban"]) or not _is_manual(c, b):
                    continue
                n = c.execute("SELECT COUNT(*) FROM entries WHERE account_id = ? AND import_hash IS NOT NULL", (a["id"],)).fetchone()[0]
                out.append({"id": f"hesap:{a['id']}:{b['id']}", "kind": "hesap", "severity": "info",
                            "title": f"Bu döküm “{b['name']}” hesabına mı ait?",
                            "text": f"{a['name']} dökümünden {n} hareket geldi; aynı bankada elle açtığın “{b['name']}” hesabı "
                                    "var. Aynı hesapsa birleştirelim; sonraki dökümler doğrudan oraya gider.",
                            "cta": "Evet, birleştir", "route": "hesaplar", "action": "account", "due": a.get("as_of") or
                            date.today().isoformat(), "tag": a["name"], "account_id": a["id"], "into": b["id"],
                            "bank": a["name"], "into_name": b["name"]})
    return out


def keep_separate(a: int, b: int) -> None:
    with db.connect() as c:
        c.execute("INSERT OR IGNORE INTO account_separate (a, b) VALUES (?, ?)", (a, b))


MERGE_TABLES = ("entries", "recurring", "debts", "debt_payments", "jars", "bank_documents", "entry_matches")


def merge_accounts(src: int, dst: int) -> dict:
    """src hesabının işlemleri, sabit kalemleri, borç hareketleri, kumbaraları, dökümleri ve bakiyesi dst'ye; src silinir.
    Bakiye: hangisinin bakiye tarihi daha yeniyse o (aynı günse dökümden gelen). IBAN ve banka adı dst'ye yazılır."""
    from app.bank_import import short_bank

    if src == dst:
        raise SyncError("Hesap kendisiyle birleştirilemez")
    with db.connect() as c:
        a = c.execute("SELECT * FROM accounts WHERE id = ? AND kind <> 'kart'", (src,)).fetchone()
        b = c.execute("SELECT * FROM accounts WHERE id = ? AND kind <> 'kart'", (dst,)).fetchone()
        if not a or not b:
            raise SyncError("Hesap bulunamadı")
        a, b = dict(a), dict(b)
        if a.get("iban") and b.get("iban") and a["iban"] != b["iban"]:
            raise SyncError("İki hesabın IBAN'ı farklı: bunlar ayrı hesaplar")
        moved = {t: c.execute(f"UPDATE {t} SET account_id = ? WHERE account_id = ?", (dst, src)).rowcount for t in MERGE_TABLES}
        newer = bool(a["as_of"]) and (not b["as_of"] or a["as_of"] > b["as_of"]
                                      or (a["as_of"] == b["as_of"] and a["source"] == "dokum" and b["source"] != "dokum"))
        alias = b.get("alias") or (a.get("alias") or a["name"] if short_bank(a["name"]) == short_bank(b["name"]) else None)
        c.execute("UPDATE accounts SET iban = COALESCE(iban, ?), alias = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                  (a.get("iban"), alias, dst))
        if newer:
            c.execute("UPDATE accounts SET balance = ?, as_of = ?, source = ?, anchor_entry_id = ? WHERE id = ?",
                      (a["balance"], a["as_of"], a["source"], a["anchor_entry_id"], dst))
        c.execute("DELETE FROM account_separate WHERE a = ? OR b = ?", (src, src))
        c.execute("DELETE FROM accounts WHERE id = ?", (src,))
        # ortak defter: src gizliyse birleşen hesap da gizli kalır (gizli hareketler artık dst'de)
        for key, raw in c.execute("SELECT key, value FROM settings WHERE key LIKE 'book:rules%'").fetchall():
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            hidden = r.get("hidden_accounts") or []
            if src in hidden:
                r["hidden_accounts"] = sorted({*(x for x in hidden if x != src), dst})
                c.execute("UPDATE settings SET value = ? WHERE key = ?", (json.dumps(r, ensure_ascii=False), key))
    rec = reconcile()  # artık aynı hesapta: kesin eşleşenler birleşir
    return {"ok": True, "into": dst, "moved": {k: v for k, v in moved.items() if v}, "matched": rec["otomatik"] + rec["kural"],
            "balance_from": "merged" if newer else "kept"}
