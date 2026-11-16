"""Finans tutarlılık denetimi, 2. katman: "Ödemelerden sonra" / bakiye / gelir-gider rakamını sessizce bozan, kullanıcının
düzeltebileceği veri durumları (app/integrity.py 1. katmanı veri bütünlüğüne bakar; bu katman paranın anlamına).

Neden: bir defterde "Ödemelerden sonra" 127 bin görünürken doğrusu 19,7 bin çıktı. Sebeplerin çoğu hata değil, eksik
bağlantıydı (hesap seçilmeden "ödendi" denen ekstre, eski bakiye, iki kez sayılan transfer). Biri yaşadıysa başkası da
yaşar: her gece her defterde denetlenir, kullanıcıya düzeltilebilir uyarı, yöneticiye yalnız sayı.

Değişmezler (kod → anlamı; hepsi SALT-OKUNUR):
  odendi_hesapsiz      ekstre "ödendi" ama hangi hesaptan ödendiği belli değil: ne bağlı kart ödemesi var ne dökümde
                       eşleşen ödeme; o günden sonra güncellenmiş bir hesap da yok → para hiçbir bakiyeden düşmedi
  eksi_bakiye          banka / nakit hesabın bugünkü bakiyesi eksi (eksik giriş, yanlış hesaba yazılmış ödeme ya da eski
                       çapa); KMH'li hesapta gerçek olabilir, kullanıcı doğrular
  capa_eski            banka hesabının bakiyesi CAPA_WARN (14) günden eski ve defter kullanılıyor; CAPA_STALE (35) üstü
                       "çok eski" (finance.net_worth stale ile aynı eşik)
  transfer_cift        aynı tutar ±1 gün, farklı iki hesapta biri gelir biri gider ve karşı taraf ikisinde aynı kişi
                       (kullanıcının kendisi): kendi hesapları arası aktarım hem gelir hem gider sayılmış
  hesapsiz_hareket     havale / dökümden gelmiş gelir-gider kaydının hesabı yok (account_id NULL): hiçbir bakiyeyi
                       etkilemiyor; yalnız bakiyesi takip edilen bir hesabın çapasından sonraki kayıtlar
  odenmis_ekstresiz    karttan ödeme yapılmış (döküm / elle) ama o döneme ait ekstre yüklenmemiş, kesimi geçmiş: o
                       dönemin harcamaları hiç sayılmamış ve tahmini döngü hâlâ düşülüyor olabilir
  cift_satir           aynı kart işlemi iki ekstrede birden (çakışan dönem) ya da iki elle kayıt aynı ekstre satırına bağlı
  harcanabilir_tutarsiz  "Ödemelerden sonra" kendi formülüyle tutmuyor: kalemlerin toplamı farklı, eksi işaretli kalem
                       eksi değer taşıyor ya da serbest paradan (free) büyük
  duzenli_bekliyor     düzenli gider bu dönem "bekliyor" görünüyor ama dönemde aynı tutarlı (±%2) eşleşmemiş havale var:
                       ödenmiş, iki kez düşülüyor

Çıktı: {day, counts: {kod: n}, amounts: {kod: ₺}, refs: {kod: [kimlik]}, ms}. Defterin kendi ayarına
(settings['finance_audit:last']) ve system.db finance_audit_runs'a (yalnız sayılar ve ₺ toplamı YOK — kod başına adet) yazılır;
yeni çıkan kod sorun kaydına (app/incidents.py "finans_tutarsizlik", yer = kod, özet "n=3"). Kullanıcı için Yapılacaklar
maddeleri todos() (app/reminders.py). İş zamanlayıcıda (app/jobs.py "finance_audit", INTEGRITY_HOUR'dan sonra; açılışta
değil): defter başına tek koşu, kısa okumalar, hiç yazma işlemi tutmaz.

Elle: python -m app.finance_audit [--all]
"""
import hashlib
import json
import logging
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from app import db, money

log = logging.getLogger("hesapkitap.finance_audit")
SETTING = "finance_audit:last"
CAPA_WARN = 14
CAPA_STALE = 35
ACTIVE_DAYS = 14        # defterde son bu kadar günde kayıt/belge yoksa çapa yaşlanması uyarılmaz (kullanılmıyor)
PAY_WINDOW = 20         # ekstrenin kesiminden son ödeme +20 güne dek yapılan kart ödemesi o ekstreye sayılır (db ile aynı)
RECUR_TOL = 0.02        # düzenli ödeme ↔ havale tutar toleransı
MAX_ITEMS = 10

CODES = ("odendi_hesapsiz", "eksi_bakiye", "capa_eski", "transfer_cift", "hesapsiz_hareket", "odenmis_ekstresiz",
         "cift_satir", "harcanabilir_tutarsiz", "duzenli_bekliyor")
LABELS = {
    "odendi_hesapsiz": "Ödendi işaretli ekstrenin hangi hesaptan ödendiği belli değil",
    "eksi_bakiye": "Hesap bakiyesi eksi görünüyor",
    "capa_eski": "Hesap bakiyesi uzun süredir güncellenmedi",
    "transfer_cift": "Kendi hesapların arası aktarım gelir ve gider olarak sayılmış olabilir",
    "hesapsiz_hareket": "Hesabı seçilmemiş havale kaydı",
    "odenmis_ekstresiz": "Kart ödemesi var ama o döneme ait ekstre yüklenmemiş",
    "cift_satir": "Aynı kart işlemi iki kez sayılıyor olabilir",
    "harcanabilir_tutarsiz": "Ödemelerden sonra kalemlerle tutmuyor",
    "duzenli_bekliyor": "Ödenmiş düzenli ödeme hâlâ bekliyor görünüyor",
}
# kullanıcının etkisi: ödemelerden sonra / bakiye ne yönde yanlış olabilir (uyarı metni için)
SEVERITY = {"odendi_hesapsiz": "warn", "eksi_bakiye": "warn", "capa_eski": "info", "transfer_cift": "info",
            "hesapsiz_hareket": "info", "odenmis_ekstresiz": "warn", "cift_satir": "warn",
            "harcanabilir_tutarsiz": "warn", "duzenli_bekliyor": "warn"}


def _q(c, sql: str, *args) -> list[dict]:
    return [dict(r) for r in c.execute(sql, args)]


def _l4(card_no: str | None) -> str:
    return "".join(ch for ch in card_no or "" if ch.isdigit())[-4:]


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _squash(s: str | None) -> str:
    from app.textnorm import normalize

    return re.sub(r"[^A-Z0-9]", "", normalize(s or ""))


# ---------------------------------------------------------------- veri (salt-okunur anlık görüntü)

def snapshot(today: date) -> dict:
    """Denetimin girdisi: saf denetim fonksiyonları yalnız bunu görür (testler sentetik sözlük verir). Kısa okumalar;
    hiçbir tabloya yazılmaz. 'nw' (finance.net_worth) ayrıca: hesaplanamazsa None, ilgili denetim atlanır."""
    with db.connect() as c:
        snap = {
            "statements": _q(c, "SELECT id, bank, card_no, period_end, due_date, total_debt, paid_at, paid_amount, "
                                "paid_source, source FROM statements"),
            "card_payments": _q(c, "SELECT id, pay_date, amount, card_last4, account_id, statement_id, import_hash "
                                   "FROM card_payments"),
            "accounts": _q(c, "SELECT * FROM accounts WHERE kind IN ('banka', 'nakit')"),
            "entries": _q(c, "SELECT id, entry_date, month, kind, amount, account_id, payment, counterparty, description, "
                             "import_hash, recurring_id, debt_id, wallet_id, linked_txn_id, skipped, not_virman, created_at "
                             "FROM entries"),
            "txns": _q(c, "SELECT t.id, t.statement_id, t.txn_date, t.amount, t.description, t.txn_type, t.installment_no, "
                          "s.card_no FROM transactions t JOIN statements s ON s.id = t.statement_id "
                          "WHERE t.is_deleted = 0 AND COALESCE(s.source, '') <> 'excel'"),
            "own_transfers": _q(c, "SELECT id, transfer_date, amount, account_id FROM own_transfers"),
            "last_activity": c.execute(
                "SELECT MAX(x) FROM (SELECT MAX(created_at) AS x FROM entries UNION ALL SELECT MAX(uploaded_at) "
                "FROM statements UNION ALL SELECT MAX(created_at) FROM card_payments)").fetchone()[0],
        }
        for a in snap["accounts"]:
            a["current"] = db._account_balance(c, a)
    snap["owner_names"] = [_squash(n) for n in (db.owner_names() + db.card_holders()) if len(_squash(n)) >= 6]
    snap["today"] = today.isoformat()
    try:
        from app import finance

        nw = finance.net_worth(today)
        snap["nw"] = {"spendable": nw["spendable"], "free": nw["free"], "parts": nw["spendable_parts"]}
        month = finance.current_period(today)
        snap["pending_recurring"] = [r for r in finance.pending_recurring(month, db.list_recurring(), db.list_entries(month))
                                     if r.get("kind") == "gider" and r.get("pending", True)]
        snap["period"] = [d.isoformat() for d in finance.period_bounds(month)]
    except Exception:  # noqa: BLE001 - net durum hesaplanamazsa öbür denetimler sürsün
        log.exception("finans denetimi: net durum hesaplanamadı")
        snap.setdefault("nw", None)
        snap.setdefault("pending_recurring", [])
        snap.setdefault("period", None)
    return snap


# ---------------------------------------------------------------- saf denetimler: [{ref, amount, ...}] döner

def odendi_hesapsiz(s: dict) -> list[dict]:
    """Ekstre ödendi (paid_at + paid_amount > 0) ama: bağlı kart ödemesi yok, kesimden son ödeme +20 güne dek aynı
    kartın (son 4 hane; hanesi yoksa tutar ±1) ödemesi de yok, ve bakiyesi takip edilen hiçbir hesap o günden sonra
    güncellenmemiş (güncellenmişse ödeme o bakiyenin içindedir). Kart Excel'inden üretilen ekstre sayılmaz."""
    linked = {p["statement_id"] for p in s["card_payments"] if p.get("statement_id")}
    anchors = [a["as_of"] for a in s["accounts"] if a.get("as_of")]
    out = []
    for st in s["statements"]:
        if st.get("source") == "excel" or not st.get("paid_at") or not money.gt(st.get("paid_amount") or 0, 0):
            continue
        if st["id"] in linked:
            continue
        lo, hi = st["period_end"], (_d(st.get("due_date") or st["period_end"]) + timedelta(days=PAY_WINDOW)).isoformat()
        l4 = _l4(st["card_no"])
        if any(lo <= p["pay_date"] <= hi and ((p.get("card_last4") or "") == l4 if p.get("card_last4")
                                               else money.le(abs(p["amount"] - st["paid_amount"]), 1))
               for p in s["card_payments"]):
            continue
        if not anchors or all(a > st["paid_at"] for a in anchors):
            continue  # bakiyeler ödemeden sonra girildi (ödeme içlerinde) ya da hiç hesap yok: nakit etkisi yok
        out.append({"ref": f"statement:{st['id']}", "amount": money.money(st["paid_amount"]), "date": st["paid_at"]})
    return out


def eksi_bakiye(s: dict) -> list[dict]:
    return [{"ref": f"account:{a['id']}", "amount": money.money(-a["current"]), "date": a.get("as_of")}
            for a in s["accounts"] if a.get("current") is not None and money.lt(a["current"], -1)]


def _active(s: dict, today: date) -> bool:
    la = _d(s.get("last_activity"))
    return bool(la and (today - la).days <= ACTIVE_DAYS)


def capa_eski(s: dict) -> list[dict]:
    """Banka hesabı (bakiyesi bilinen), çapası CAPA_WARN günden eski, defter son ACTIVE_DAYS günde kullanılmış ve o
    hesapta çapadan sonra hareket var ya da hesap dökümle izleniyor (elle açılıp unutulmuş boş hesap sorulmaz)."""
    today = _d(s["today"])
    if not _active(s, today):
        return []
    used = {e["account_id"] for e in s["entries"] if e.get("account_id")}
    out = []
    for a in s["accounts"]:
        if a["kind"] != "banka" or not a.get("as_of") or a.get("current") is None:
            continue
        age = (today - _d(a["as_of"])).days
        if age < CAPA_WARN or (a["id"] not in used and a.get("source") != "dokum"):
            continue
        out.append({"ref": f"account:{a['id']}", "amount": money.money(a["current"]), "date": a["as_of"], "days": age,
                    "stale": age > CAPA_STALE})
    return out


def _excluded(e: dict) -> bool:
    return bool(e.get("skipped") or e.get("debt_id") or e.get("wallet_id") or e.get("not_virman")
                or e.get("payment") == "kredi_karti" or e.get("linked_txn_id") or not e.get("account_id"))


def transfer_cift(s: dict) -> list[dict]:
    """Gelir + gider çifti: aynı tutar (kuruş), ±1 gün, farklı iki hesap; karşı taraf iki bacakta aynı kişi ya da iki
    açıklamada da hesap sahibinin adı geçiyor. Zaten aktarım olarak kayıtlı (own_transfers aynı gün/tutar) sayılmaz."""
    owners = s.get("owner_names") or []
    own_keys = {(t["transfer_date"][:10], money.kurus(abs(t["amount"]))) for t in s["own_transfers"]}

    def who(e):
        return _squash(e.get("counterparty"))

    def names_owner(e):
        sq = _squash(f"{e.get('description') or ''} {e.get('counterparty') or ''}")
        return any(o in sq for o in owners)

    by_amt: dict[int, list[dict]] = defaultdict(list)
    for e in s["entries"]:
        if not _excluded(e):
            by_amt[money.kurus(e["amount"])].append(e)
    out, used = [], set()
    for k, rows in by_amt.items():
        ins = [e for e in rows if e["kind"] == "gelir"]
        outs = [e for e in rows if e["kind"] == "gider"]
        for g in outs:
            for i in ins:
                if g["id"] in used or i["id"] in used or g["account_id"] == i["account_id"]:
                    continue
                if abs((_d(g["entry_date"]) - _d(i["entry_date"])).days) > 1:
                    continue
                same = who(g) and who(g) == who(i) and len(who(g)) >= 6
                if not (same or (names_owner(g) and names_owner(i))):
                    continue
                if (g["entry_date"][:10], k) in own_keys:
                    continue
                used |= {g["id"], i["id"]}
                out.append({"ref": f"pair:{g['id']}:{i['id']}", "amount": money.money(g["amount"]), "date": g["entry_date"],
                            "out_id": g["id"], "in_id": i["id"]})
    return out


def hesapsiz_hareket(s: dict) -> list[dict]:
    """Havale ya da dökümden gelmiş (dekont/e-posta dahil) gelir-gider, hesabı yok; bakiyesi takip edilen en eski
    çapadan sonra ve bugüne kadar (öncesi zaten bakiyelerin içinde). Borç, cüzdan, kart, atlanan hariç."""
    anchors = [a["as_of"] for a in s["accounts"] if a.get("as_of")]
    if not anchors:
        return []
    lo, hi = min(anchors), s["today"]
    return [{"ref": f"entry:{e['id']}", "amount": money.money(e["amount"]), "date": e["entry_date"], "kind": e["kind"]}
            for e in s["entries"]
            if not e.get("account_id") and not e.get("skipped") and not e.get("debt_id") and not e.get("wallet_id")
            and e.get("payment") != "kredi_karti" and (e.get("payment") == "havale" or e.get("import_hash"))
            and lo < e["entry_date"][:10] <= hi]


def odenmis_ekstresiz(s: dict) -> list[dict]:
    """Kart ödemesi (son 4 hanesi belli) var, kesimden son ödeme +20 güne dek kapsayan ekstre yok, ödeme o kartın
    yüklenen ilk ekstresinden sonra (öncesi bankanın arşivi; yüklenemeyebilir) ve son 120 gün içinde."""
    today = _d(s["today"])
    sts = [x for x in s["statements"] if x.get("due_date") and x.get("source") != "excel"]
    first: dict[str, str] = {}
    for x in sts:
        first[_l4(x["card_no"])] = min(first.get(_l4(x["card_no"]), x["period_end"]), x["period_end"])
    out = []
    for p in s["card_payments"]:
        l4 = p.get("card_last4") or ""
        if not l4 or l4 not in first or p["pay_date"] <= first[l4] or (today - _d(p["pay_date"])).days > 120:
            continue
        if p["pay_date"] > s["today"]:
            continue
        covered = any(_l4(x["card_no"]) == l4 and x["period_end"] <= p["pay_date"]
                      <= (_d(x["due_date"]) + timedelta(days=PAY_WINDOW)).isoformat() for x in sts)
        if not covered:
            out.append({"ref": f"card_payment:{p['id']}", "amount": money.money(p["amount"]), "date": p["pay_date"]})
    return out


def cift_satir(s: dict) -> list[dict]:
    """(a) aynı kartın iki ayrı ekstresinde aynı gün, aynı tutar, aynı açıklama, aynı taksit no'lu işlem (çakışan
    dönem okunmuş); (b) iki elle kayıt aynı ekstre satırına bağlı (biri gerçek ek harcamaysa gizleniyor)."""
    seen: dict[tuple, dict] = {}
    out = []
    for t in s["txns"]:
        if t["txn_type"] == "odeme":
            continue
        k = (_l4(t["card_no"]), t["txn_date"], money.kurus(t["amount"]), _squash(t["description"]), t.get("installment_no"))
        o = seen.get(k)
        if o and o["statement_id"] != t["statement_id"]:
            out.append({"ref": f"txn:{t['id']}", "amount": money.money(abs(t["amount"])), "date": t["txn_date"]})
        elif not o:
            seen[k] = t
    by_link: dict[int, list[dict]] = defaultdict(list)
    for e in s["entries"]:
        if e.get("linked_txn_id") and not e.get("skipped"):
            by_link[e["linked_txn_id"]].append(e)
    for rows in by_link.values():
        for e in sorted(rows, key=lambda x: x["id"])[1:]:
            out.append({"ref": f"entry:{e['id']}", "amount": money.money(e["amount"]), "date": e["entry_date"]})
    return out


def harcanabilir_tutarsiz(s: dict) -> list[dict]:
    nw = s.get("nw")
    if not nw:
        return []
    total = sum(p["amount"] if p["sign"] == "+" else -p["amount"] for p in nw["parts"])
    bad = []
    if money.diff_k(abs(total - nw["spendable"]), 1) > 0:  # 1 TL'den büyük fark (yuvarlama payı)
        bad.append("toplam")
    if any(p["sign"] == "−" and money.lt(p["amount"], 0) for p in nw["parts"]):
        bad.append("eksi_kalem")
    if money.gt(nw["spendable"], nw["free"]):
        bad.append("free_ustu")
    return [{"ref": f"spendable:{b}", "amount": money.money(abs(total - nw["spendable"]) if b == "toplam"
                                                               else max(0.0, nw["spendable"] - nw["free"])),
             "date": s["today"]} for b in bad]


def duzenli_bekliyor(s: dict) -> list[dict]:
    """Dönemde bekleyen düzenli gider ↔ aynı dönemde düzenli ödemeye bağlanmamış, tutarı ±%2 havale/döküm gideri."""
    if not s.get("period") or not s.get("pending_recurring"):
        return []
    lo, hi = s["period"]
    cand = [e for e in s["entries"] if e["kind"] == "gider" and not e.get("recurring_id") and not e.get("skipped")
            and not e.get("debt_id") and e.get("payment") != "kredi_karti" and e.get("account_id")
            and lo <= e["entry_date"][:10] <= min(hi, s["today"])]
    out, used = [], set()
    for r in s["pending_recurring"]:
        amt = r.get("amount") or 0
        if not money.gt(amt, 0):
            continue
        for e in cand:
            if e["id"] not in used and abs(e["amount"] - amt) <= max(1.0, amt * RECUR_TOL):
                used.add(e["id"])
                out.append({"ref": f"recurring:{r.get('recurring_id') or r.get('id')}:{e['id']}",
                            "amount": money.money(amt), "date": e["entry_date"], "entry_id": e["id"]})
                break
    return out


CHECKS = {"odendi_hesapsiz": odendi_hesapsiz, "eksi_bakiye": eksi_bakiye, "capa_eski": capa_eski,
          "transfer_cift": transfer_cift, "hesapsiz_hareket": hesapsiz_hareket, "odenmis_ekstresiz": odenmis_ekstresiz,
          "cift_satir": cift_satir, "harcanabilir_tutarsiz": harcanabilir_tutarsiz, "duzenli_bekliyor": duzenli_bekliyor}


def audit(snap: dict) -> dict:
    """Saf: anlık görüntüden bulgular. {day, counts, amounts, refs, findings: {kod: [...]}}."""
    findings: dict[str, list[dict]] = {}
    for code, fn in CHECKS.items():
        try:
            if got := fn(snap):
                findings[code] = got
        except Exception:  # noqa: BLE001 - bir denetimin hatası ötekileri durdurmasın
            log.exception("finans denetimi %s çalışamadı", code)
            findings.setdefault("_hata", []).append({"ref": code, "amount": 0.0})
    return {"day": snap["today"], "counts": {k: len(v) for k, v in findings.items()},
            "amounts": {k: money.money(sum(x["amount"] or 0 for x in v)) for k, v in findings.items() if k != "_hata"},
            "refs": {k: sorted({x["ref"] for x in v}) for k, v in findings.items()}, "findings": findings}


def check(today: date | None = None) -> dict:
    """Bu defteri denetle (yazmaz). Ortak defter üyesi bağlamında da defterin tamamı (sahip görünümü)."""
    from app import book

    today = today or date.today()
    t0 = time.perf_counter()
    with book.owner_view():
        res = audit(snapshot(today))
    res["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return res


# ---------------------------------------------------------------- çalıştırma ve kayıt

def last() -> dict | None:
    try:
        v = json.loads(db.get_setting(SETTING) or "null")
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def _uid() -> int:
    u = db.current_user()
    return int(u["id"]) if u and u.get("id") else 0


def run(today: date | None = None) -> dict:
    """Denetle, sonucu defterin ayarına yaz (tek kısa yazma), yöneticiye sayılar, yeni çıkan kodlar sorun kaydına."""
    res = check(today)
    res["ran_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    prev = last() or {}
    new_codes = sorted(k for k in res["counts"] if k != "_hata" and not (prev.get("counts") or {}).get(k))
    db.set_setting(SETTING, json.dumps(res, ensure_ascii=False, default=str))
    _record(res)
    try:
        from app import incidents

        for code in new_codes:
            incidents.record("finans_tutarsizlik", code, uid=_uid() or None, ozet=f"n={res['counts'][code]}")
    except Exception:  # noqa: BLE001
        log.exception("finans denetimi sorun kaydı yazılamadı")
    return {"total": sum(v for k, v in res["counts"].items() if k != "_hata"), "codes": len(res["counts"]),
            "new": len(new_codes), "ms": res["ms"]}


def _table(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS finance_audit_runs (user_id INTEGER NOT NULL, day TEXT NOT NULL,
                 counts_json TEXT NOT NULL, ms REAL, PRIMARY KEY (user_id, day))""")


def _record(res: dict) -> None:
    """Yönetici için yalnız sayılar (kod → adet); tutar, kayıt kimliği, ad yok."""
    from app import system

    try:
        with system.connect() as c:
            _table(c)
            c.execute("INSERT OR REPLACE INTO finance_audit_runs (user_id, day, counts_json, ms) VALUES (?, ?, ?, ?)",
                      (_uid(), res["day"], json.dumps(res["counts"]), res["ms"]))
            c.execute("DELETE FROM finance_audit_runs WHERE day < date(?, '-90 days')", (res["day"],))
    except Exception:  # noqa: BLE001 - sistem veritabanı yoksa (tek kullanıcı) yalnız deftere
        log.exception("finance_audit_runs yazılamadı")


def admin_summary(day: str | None = None) -> dict:
    """Sınıf bazında sayım: {day, spaces, with_issues, by_code: {kod: {books, count, label}}}. Gün yoksa son gün."""
    from app import system

    with system.connect() as c:
        _table(c)
        day = day or (c.execute("SELECT MAX(day) FROM finance_audit_runs").fetchone()[0] or date.today().isoformat())
        rows = [json.loads(r[0]) for r in c.execute("SELECT counts_json FROM finance_audit_runs WHERE day = ?", (day,))]
    by: dict[str, dict] = {}
    for counts in rows:
        for k, n in counts.items():
            b = by.setdefault(k, {"books": 0, "count": 0, "label": LABELS.get(k, k)})
            b["books"] += 1
            b["count"] += n
    return {"day": day, "spaces": len(rows), "with_issues": sum(1 for r in rows if r),
            "by_code": dict(sorted(by.items()))}


# ---------------------------------------------------------------- kullanıcıya: Yapılacaklar maddeleri

def _tl(x) -> str:
    return money.tl(abs(x or 0), trim=True)


def _item(c, code: str, f: dict) -> dict | None:
    """Bir bulgu → Yapılacaklar maddesi (yalnız kullanıcının kendi istemcisine). Kayıt silinmişse None."""
    ref = f["ref"]
    kind, _, rest = ref.partition(":")
    one = lambda sql, *a: c.execute(sql, a).fetchone()  # noqa: E731
    base = {"id": f"finans:{code}:{rest}", "kind": "finans", "code": code, "severity": SEVERITY[code],
            "due": f.get("date") or date.today().isoformat(), "tag": "Kontrol", "amount": f.get("amount")}
    if code == "odendi_hesapsiz" and (r := one("SELECT bank, card_no, period_end FROM statements WHERE id = ?", int(rest))):
        return {**base, "title": f"{r['bank']} ••{_l4(r['card_no'])} ekstresi hangi hesaptan ödendi?",
                "text": f"Bu ekstreyi {_tl(f['amount'])} ödendi işaretledin ama hangi hesaptan ödendiği belli değil; para "
                        "hiçbir hesabın bakiyesinden düşmedi. Hesabı seç, Ödemelerden sonra doğru çıksın.",
                "cta": "Hesabı seç", "route": f"ekstreler/{rest}", "action": "statement_pay_account",
                "statement_id": int(rest), "paid_amount": f["amount"], "paid_at": f.get("date")}
    if code in ("eksi_bakiye", "capa_eski") and (r := one("SELECT name FROM accounts WHERE id = ?", int(rest))):
        if code == "eksi_bakiye":
            text = (f"{r['name']} bakiyesi {_tl(f['amount'])} eksi görünüyor. Eksik bir giriş, yanlış hesaba yazılmış bir "
                    "ödeme ya da eski bakiye olabilir; bankadaki bakiyeyi gir ya da son dökümü yükle.")
            title, cta, action = f"{r['name']} bakiyesi eksi", "Bakiyeyi düzelt", "account_balance"
        else:
            text = (f"{r['name']} bakiyesi {f['days']} gündür güncellenmedi; Ödemelerden sonra eski bakiyeyle "
                    "hesaplanıyor. Son dökümü yükle ya da güncel bakiyeyi gir.")
            title, cta, action = f"{r['name']} · {f['days']} gündür güncellenmedi", "Döküm yükle", "upload"
        return {**base, "title": title, "text": text, "cta": cta, "route": f"hesaplar/{rest}", "action": action,
                "account_id": int(rest), "bank": r["name"]}
    if code == "transfer_cift":
        a, b = (int(x) for x in rest.split(":"))
        return {**base, "title": f"{_tl(f['amount'])} aynı gün gelir + gider",
                "text": f"{_tl(f['amount'])} bir hesabından çıkıp aynı gün öbür hesabına girmiş; ikisi de gelir/gider "
                        "sayılıyor. Kendi hesapların arası aktarımsa tek dokunuşla aktarıma çevir.",
                "cta": "Aktarıma çevir", "route": f"islemler/m{a}", "action": "own_pair", "out_entry_id": a,
                "in_entry_id": b}
    if code == "hesapsiz_hareket":
        return {**base, "title": f"{_tl(f['amount'])} {'gelir' if f.get('kind') == 'gelir' else 'gider'} · hesap seçilmemiş",
                "text": "Bu havalenin hangi hesaptan geçtiği seçilmemiş; hiçbir bakiyeye yansımıyor. Kaydı aç, hesabı seç.",
                "cta": "Hesabı seç", "route": f"islemler/m{rest}", "action": None, "entry_id": int(rest)}
    if code == "odenmis_ekstresiz":
        return {**base, "title": f"{_tl(f['amount'])} kart ödemesinin ekstresi yok",
                "text": "Bu kart ödemesinin ait olduğu dönemin ekstresi yüklenmemiş: o dönemin harcamaları sayılmıyor "
                        "ve tahmini kart borcu hâlâ düşülüyor olabilir. Ekstreyi yükle.",
                "cta": "Ekstre yükle", "route": "ekstreler", "action": "upload"}
    if code == "cift_satir":
        route = f"islemler/c{rest}" if kind == "txn" else f"islemler/m{rest}"
        return {**base, "title": f"{_tl(f['amount'])} iki kez sayılıyor olabilir",
                "text": "Aynı kart işlemi iki ekstrede ya da iki kayıtta görünüyor. Fazla olanı sil.",
                "cta": "Kayda bak", "route": route, "action": None}
    if code == "duzenli_bekliyor":
        rid, eid = rest.split(":")
        return {**base, "title": f"{_tl(f['amount'])} düzenli ödeme ödendi mi?",
                "text": "Bu düzenli ödeme bu dönem bekliyor görünüyor ama aynı tutarda bir havale var. Aynıysa bağla; "
                        "Ödemelerden sonra iki kez düşmesin.",
                "cta": "Bağla", "route": f"islemler/m{eid}", "action": "match", "entry_id": int(eid),
                "recurring_id": int(rid) if rid.isdigit() else None}
    if code == "harcanabilir_tutarsiz":
        return {**base, "title": "Ödemelerden sonra kontrol ediliyor",
                "text": "Ödemelerden sonra rakamı kalemleriyle tutmadı. Bunu biz de gördük, inceliyoruz.",
                "cta": "Kalemlere bak", "route": "ozet", "action": None}
    return None


def todos() -> list[dict]:
    """Son denetimin Yapılacaklar maddeleri (kod başına en çok MAX_ITEMS). Ortak defter: yalnız sahip / tam yetkili;
    gizli kart / hesap sızmasın diye üyeye madde gösterilmez."""
    from app import book

    if book.member():
        return []
    r = last()
    if not r or not r.get("findings"):
        return []
    out = []
    with db.connect() as c:
        for code, fs in r["findings"].items():
            if code not in SEVERITY:
                continue
            for f in fs[:MAX_ITEMS]:
                try:
                    if it := _item(c, code, f):
                        out.append(it)
                except (ValueError, TypeError):  # bozuk kimlik: madde atlanır
                    continue
    return out


def signature(r: dict | None) -> str:
    return hashlib.sha256(json.dumps((r or {}).get("refs") or {}, sort_keys=True).encode()).hexdigest()[:10]


# ---------------------------------------------------------------- tek dokunuşla düzeltme: gelir+gider → aktarım

class FixError(ValueError):
    pass


def own_pair(out_id: int, in_id: int) -> dict:
    """transfer_cift bulgusu: gider (out) + gelir (in) kaydını kendi hesapları arası aktarıma çevir. İki hesabın
    bakiyesi değişmez (aynı imzalı akış), gelir ve gider toplamlarından düşer. Dökümden gelmişse parmak izi aktarıma
    taşınır (yeniden yüklemede kopya olmaz); yalnız iki bacak da hesabının çapasından önce/çapada ise (bakiye
    zaten dökümde) ya da ikisi de elle girilmişse çevrilir, öbür durumda FixError (kullanıcı Transferler'den yapar)."""
    with db.connect() as c:
        g = c.execute("SELECT * FROM entries WHERE id = ?", (out_id,)).fetchone()
        i = c.execute("SELECT * FROM entries WHERE id = ?", (in_id,)).fetchone()
        if not g or not i:
            raise FixError("Kayıt bulunamadı")
        g, i = dict(g), dict(i)
        if g["kind"] != "gider" or i["kind"] != "gelir" or money.ne(g["amount"], i["amount"]) \
                or not g["account_id"] or not i["account_id"] or g["account_id"] == i["account_id"]:
            raise FixError("Bu iki kayıt bir aktarım çifti değil")
        accs = {a["id"]: dict(a) for a in c.execute("SELECT * FROM accounts WHERE id IN (?, ?)",
                                                    (g["account_id"], i["account_id"]))}
    manual = not g["import_hash"] and not i["import_hash"]
    if manual:
        tid = db.add_own_pair(g["entry_date"], g["amount"], g["account_id"], i["account_id"], g["description"])
        with db.connect() as c:
            c.execute("DELETE FROM entries WHERE id IN (?, ?)", (out_id, in_id))
        return {"own_transfer_id": tid}

    def in_anchor(e):
        a = accs.get(e["account_id"]) or {}
        return bool(e["import_hash"]) and a.get("as_of") and (e["entry_date"] < a["as_of"] or (
            e["entry_date"] == a["as_of"] and e["id"] <= (a.get("anchor_entry_id") or 0)))

    if not (in_anchor(g) and in_anchor(i)):
        raise FixError("Bu aktarımı Transferler ekranından ekle; kayıtlardan biri bakiyenin dışında")
    with db.connect() as c:
        for e, sign in ((g, -1), (i, 1)):
            db.add_own_transfer(c, e["entry_date"], sign * e["amount"], accs[e["account_id"]]["name"],
                                e.get("note") or e["description"], e["import_hash"], account_id=e["account_id"])
        c.execute("DELETE FROM entries WHERE id IN (?, ?)", (out_id, in_id))
    return {"own_transfer_id": None}


# ---------------------------------------------------------------- elle

def main(argv: list[str]) -> int:
    db.init_db()
    if "--all" in argv:
        from app import jobs

        for path, user in jobs.spaces():
            with jobs.book_space(path, user):
                print((user or {}).get("id", 0), run())
        return 0
    r = check()
    print(json.dumps({k: r[k] for k in ("day", "counts", "amounts", "ms")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
