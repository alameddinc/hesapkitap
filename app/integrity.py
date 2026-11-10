"""Gece doğruluk denetimi, 1. katman (ucuz SQL değişmezleri; denetim belgesi Ek A §2).

Her gece her kullanıcı defterinde çalışır (app/jobs.py "integrity"); yazdığı tek şey defterin kendi ayarıdır
(settings['integrity:last']). İhlal varsa kullanıcının Yapılacaklar listesine "Rakamları kontrol et" maddesi düşer
(app/reminders.py). Yöneticiye yalnız SAYILAR gider (system.db integrity_runs; günlük + isteğe bağlı Telegram/posta):
kullanıcı verisi, tutar, açıklama, ad yok.

Değişmezler (kod → anlamı):
  bakiye_capa         hesap bakiyesi iki yoldan (çapa + sonraki hareketler / geçmiş bakiye hesabı) farklı çıkıyor (I1)
  ekstre_denklem      ekstre özet denklemi tutmuyor (faiz içinde/ayrı biçimi dahil), kullanıcı onaylamamış (I3)
  ekstre_satir_farki  okunan işlemler özetle uyuşmuyor; kullanıcının sildiği satırlarla açıklanan fark sayılmaz (I3)
  odeme_ekstre        ödendi işaretleri ödemelerden yeniden hesaplanınca değişiyor: bir ödeme yanlış ekstrede (I4)
  virman_tek_bacak    karşı hesap da takip ediliyor (dökümü o günü kapsıyor) ama aktarımın karşı bacağı yok (I5)
  bolme_asim          alt kalemlerin toplamı harcamanın tutarını aşıyor (I6)
  borc_fazla_odeme    borca ödenen satırların toplamı borcun tutarını aşıyor (I7; A4 öncesi eski veri olabilir)
  borc_odenen_tutarsiz  debts.paid_amount ödeme satırlarıyla uyuşmuyor (I7)
  kart_borcu          kart borcu iki yoldan farklı (her kartın son ekstresi kuralı) (I12)
  kurus_alti          kuruştan küçük basamaklı tutar (F1.2)
  cift_aktarim        aynı hareket iki ayrı belgeden iki kez içe aktarılmış ya da aynı parmak izi iki kez (I11)
  cift_ekstre         aynı kartın aynı ayı için iki ekstre (I11)

Denetlenmeyen, kodda korunan değişmez:
  I9  tahmini satırlar (ekstresiz kart ödemesi) gerçek toplamlara girmez (finance.transactions). BİLİNÇLİ İSTİSNA
      (30.10): dökümü olmayan geçmiş ayın maaş tahmini gerçek gelir sayılır (salary_estimate); ihlal değildir.

Elle çalıştırma: python -m app.integrity [kullanıcı adı]   (ad yoksa ana defter; --all: herkes)
"""
import hashlib
import json
import logging
import os
import sys
import threading
import time
from datetime import date, datetime, timezone

from app import db, money, terms

log = logging.getLogger("ekstre.integrity")
SETTING = "integrity:last"

LABELS = {
    "bakiye_capa": "Hesap bakiyesi iki hesaplamada farklı çıkıyor",
    "ekstre_denklem": "Ekstrenin özet rakamları kendi içinde tutmuyor",
    "ekstre_satir_farki": "Ekstreden okunan işlemlerin toplamı ekstre özetiyle uyuşmuyor",
    "odeme_ekstre": "Bir kart ödemesi yanlış ekstreye bağlanmış olabilir",
    "virman_tek_bacak": "Kendi hesapların arası bir aktarımın öbür hesapta karşılığı yok",
    "bolme_asim": "Bölünmüş bir harcamanın parçaları harcamadan büyük",
    "borc_fazla_odeme": "Bir borca, borcun kendisinden fazla ödeme yazılmış",
    "borc_odenen_tutarsiz": "Bir borcun ödenen tutarı ödemelerin toplamıyla uyuşmuyor",
    "kart_borcu": "Kart borcu iki hesaplamada farklı çıkıyor",
    "kurus_alti": "Kuruştan küçük basamaklı tutar var",
    "cift_aktarim": "Aynı hareket iki kez içe aktarılmış olabilir",
    "cift_ekstre": "Aynı kartın aynı ayı için iki ekstre var",
}
ROUTES = {"ekstre_denklem": "ekstreler", "ekstre_satir_farki": "ekstreler", "odeme_ekstre": "ekstreler", "cift_ekstre": "ekstreler", "kart_borcu": "ekstreler",
          "bakiye_capa": "hesaplar", "virman_tek_bacak": "transferler", "borc_fazla_odeme": "borclar", "borc_odenen_tutarsiz": "borclar"}

def _q(c, sql: str, *args) -> list:
    return [dict(r) for r in c.execute(sql, args)]


# ---------------------------------------------------------------- tek tek denetimler: [ref, ...] döner (yalnız kimlik)

def _bakiye_capa(today: date) -> list[str]:
    from app import zakat

    out = []
    with db.connect() as c:
        accs = _q(c, "SELECT * FROM accounts WHERE kind <> 'kart' AND as_of IS NOT NULL")
        future = {r["account_id"] for r in _q(c, "SELECT DISTINCT account_id FROM entries WHERE entry_date > ?",
                                               today.isoformat())}
        now = {a["id"]: db._account_balance(c, a) for a in accs}
    at = zakat.account_balances_at(today) if hasattr(zakat, "account_balances_at") else None
    if at is None:  # eski sürüm: yalnız toplam karşılaştırılabilir
        if any(a["id"] in future for a in accs):
            return []
        total, unknown = zakat._bank_at(today)
        if not unknown and money.ne(total, sum(v for v in now.values() if v is not None)):
            out.append("accounts")
        return out
    for a in accs:
        if a["id"] in future or at.get(a["id"]) is None or now[a["id"]] is None:
            continue
        if money.ne(at[a["id"]], now[a["id"]]):
            out.append(f"account:{a['id']}")
    return out


def _ekstre_denklem(statements: list[dict]) -> list[str]:
    """I3, özet denklemi: önceki − ödemeler + harcamalar (+ faiz, ekstrenin biçimine göre içinde ya da ayrı) = yeni borç.
    list_statements'ın equation_diff'i (parsers.base.equation, fees_included dahil). Kullanıcının onayladığı ekstre ve
    kart Excel'inden üretilen (borcu kurgu) ekstre sayılmaz. Okunan satırlar ile özetin farkı ayrı denetim (satır farkı)."""
    return [f"statement:{s['id']}" for s in statements if s.get("source") != "excel" and not s.get("confirmed_at")
            and not s.get("rows_ok")  # satırlar önceki borçtan yeni borca kuruşu kuruşuna götürüyor: özetin ara rakamı yanlış
            and s.get("equation_diff") is not None and money.diff_k(abs(s["equation_diff"]), 1) >= 0]


def _ekstre_satir_farki(statements: list[dict]) -> list[str]:
    """I3, satırlar: okunan işlemler (ödeme hariç) − özet ≥ 1 TL. Fark kullanıcının sildiği satırlardan geliyorsa
    (silinenler dahil edilince kapanıyorsa) bilinçli silmedir, ihlal değil."""
    cand = [s for s in statements if s.get("source") != "excel" and not s.get("confirmed_at") and not s.get("rows_ok")
            and s.get("reconciliation_diff") is not None and money.diff_k(abs(s["reconciliation_diff"]), 1) >= 0]
    if not cand:
        return []
    with db.connect() as c:
        deleted = {r["statement_id"]: r["t"] for r in _q(
            c, "SELECT statement_id, SUM(amount) AS t FROM transactions WHERE is_deleted = 1 AND txn_type <> 'odeme' "
               "GROUP BY statement_id")}
    return [f"statement:{s['id']}" for s in cand
            if money.diff_k(abs(s["reconciliation_diff"] + deleted.get(s["id"], 0.0)), 1) >= 0]


def _odeme_ekstre() -> list[str]:
    return [f"statement:{ch['id']}" for ch in db.reassign_statement_payments(apply=False)["changed"]]


def _other_bank(t: dict) -> str | None:
    """Aktarımın karşı bankası, temiz açıklamadan ('Kendi hesabına · Halkbank → VakıfBank'; bank_import.own_label)."""
    part = next((p for p in (t.get("description") or "").split(" · ") if " → " in p), None)
    if not part:
        return None
    src, dst = (x.strip() for x in part.split(" → ", 1))
    other = dst if t["amount"] < 0 else src
    return None if other in ("", "?") else other


def _virman_tek_bacak() -> list[str]:
    """I5: kendi hesapların arası aktarımın karşı bacağı yok — YALNIZ karşı hesap da takip ediliyorsa (hesaplarda var
    ve dökümü o günü kapsıyor). Takip edilmeyen bir hesaba (dökümü yüklenmemiş başka banka) giden/gelen aktarım tek
    bacaklıdır ve normaldir; karşı banka açıklamadan anlaşılamıyorsa da sayılmaz."""
    from app.bank_import import short_bank

    with db.connect() as c:
        ts = _q(c, "SELECT id, transfer_date, amount, account_id, description FROM own_transfers WHERE account_id IS NOT NULL")
        accs = _q(c, "SELECT id, name, alias FROM accounts WHERE kind = 'banka'")
        cover = {r["account_id"]: (r["lo"], r["hi"]) for r in _q(
            c, "SELECT account_id, MIN(entry_date) AS lo, MAX(entry_date) AS hi FROM entries "
               "WHERE import_hash IS NOT NULL AND account_id IS NOT NULL GROUP BY account_id")}
    by_name: dict[str, set[int]] = {}
    for a in accs:
        for n in {a["name"], a["alias"], short_bank(a["name"])} - {None, ""}:
            by_name.setdefault(n, set()).add(a["id"])
    out = []
    for t in ts:
        day = t["transfer_date"][:10]
        other = _other_bank(t)
        if not other:
            continue
        targets = {aid for aid in by_name.get(other, set()) | by_name.get(short_bank(other), set())
                   if aid != t["account_id"] and aid in cover and cover[aid][0] <= day <= cover[aid][1]}
        if not targets:  # karşı hesap takip edilmiyor ya da bilinmiyor: tek bacak doğal
            continue
        d = date.fromisoformat(day)
        paired = any(o["id"] != t["id"] and o["account_id"] in targets and money.eq(o["amount"], -t["amount"])
                     and abs((date.fromisoformat(o["transfer_date"][:10]) - d).days) <= 3 for o in ts)
        if not paired:
            out.append(f"own_transfer:{t['id']}")
    return out


def _bolme_asim() -> list[str]:
    with db.connect() as c:
        rows = _q(c, """SELECT 'entry:' || e.id AS ref FROM entries e JOIN splits s ON s.entry_id = e.id
                          GROUP BY e.id HAVING ROUND(SUM(s.amount) * 100) > ROUND(e.amount * 100)
                          UNION ALL
                          SELECT 'txn:' || t.id FROM transactions t JOIN splits s ON s.txn_id = t.id
                          GROUP BY t.id HAVING ROUND(SUM(s.amount) * 100) > ROUND(t.amount * 100)""")
    return [r["ref"] for r in rows]


def _borc_rows() -> list[dict]:
    with db.connect() as c:
        return _q(c, """SELECT d.id, d.amount, d.paid_amount, COALESCE(SUM(p.amount), 0) AS paid, COUNT(p.id) AS n
                         FROM debts d LEFT JOIN debt_payments p ON p.debt_id = d.id GROUP BY d.id""")


def _borc_fazla_odeme() -> list[str]:
    """I7: ödeme satırlarının toplamı borcun tutarını aşıyor (A4 öncesi fazla ödeme kabul ediliyordu; paid_amount
    tutarla sınırlı tutulduğu için borç 'kapandı' görünür, fazlası nakit akışında sayılır)."""
    return [f"debt:{r['id']}" for r in _borc_rows() if money.gt(r["paid"], r["amount"])]


def _borc_odenen_tutarsiz() -> list[str]:
    """I7: debts.paid_amount ≠ min(Σ ödemeler, tutar) — özet alanı ödeme satırlarıyla uyuşmuyor."""
    return [f"debt:{r['id']}" for r in _borc_rows() if money.ne(r["paid_amount"] or 0, min(r["paid"], r["amount"]))]


def _kart_borcu(today: date, statements: list[dict]) -> list[str]:
    d = today.isoformat()
    latest: dict[str, dict] = {}  # bağımsız hesap: kart başına son (kurgu olmayan) ekstrenin ödenmemiş kısmı
    for st in statements:
        if st.get("source") == "excel" or st["period_end"] > d:
            continue
        if st["card_no"] not in latest or st["period_end"] > latest[st["card_no"]]["period_end"]:
            latest[st["card_no"]] = st
    mine = sum(db.statement_remaining(st, d) for st in latest.values())
    return [] if money.eq(mine, db.card_debt_at(d, statements)) else ["card_debt"]


_REF = {"entries": "entry", "transactions": "txn", "statements": "statement", "debts": "debt",
        "debt_payments": "debt_payment", "own_transfers": "own_transfer", "card_payments": "card_payment",
        "accounts": "account", "bills": "bill", "splits": "split"}


def _kurus_alti() -> list[str]:
    """Kuruş altı tutar taşıyan kayıtlar (app/money.py ile aynı tablo/kolon listesi; altın/döviz borcu hariç)."""
    out = []
    with db.connect() as c:
        for t, col, extra in money._targets(c):
            for (rid,) in c.execute(f"SELECT rowid FROM {t} WHERE {money._OFF.format(col=col)}{extra}"):
                out.append(f"{_REF.get(t, t)}:{rid}")
    return out


def _cift_aktarim() -> list[str]:
    with db.connect() as c:
        same_hash = _q(c, "SELECT id FROM entries e WHERE import_hash IS NOT NULL AND EXISTS (SELECT 1 FROM entries o "
                          "WHERE o.import_hash = e.import_hash AND o.id < e.id)")  # aynı parmak izinin ikinci kaydı
        # aynı hesap, aynı gün, aynı tutar, aynı yön; iki FARKLI belgeden (PDF + Excel aynı dönemi iki kez)
        cross = _q(c, """SELECT a.id FROM entries a JOIN entries b ON b.account_id = a.account_id
                          AND b.entry_date = a.entry_date AND b.kind = a.kind AND ROUND(b.amount * 100) = ROUND(a.amount * 100)
                          AND b.id > a.id AND b.bank_doc_id IS NOT NULL AND a.bank_doc_id IS NOT NULL
                          AND b.bank_doc_id <> a.bank_doc_id
                         WHERE a.import_hash IS NOT NULL AND b.import_hash IS NOT NULL AND a.skipped = 0 AND b.skipped = 0""")
    return [f"entry:{r['id']}" for r in same_hash + cross]


def _cift_ekstre() -> list[str]:
    with db.connect() as c:
        rows = _q(c, """SELECT s.id FROM statements s WHERE COALESCE(s.source, '') <> 'excel' AND EXISTS (
                        SELECT 1 FROM statements o WHERE o.card_no = s.card_no AND o.id <> s.id
                        AND COALESCE(o.source, '') <> 'excel' AND substr(o.period_end, 1, 7) = substr(s.period_end, 1, 7))""")
    return [f"statement:{r['id']}" for r in rows]


# ---------------------------------------------------------------- çalıştırma

def check(today: date | None = None) -> dict:
    """Defterin değişmezlerini denetle (yazmaz). {day, counts: {kod: n}, refs: {kod: [kimlik]}, ms}.
    Ortak defter üyesi çalıştırsa da (Yeniden kontrol et, gece işi üyenin alanında) defterin tamamı denetlenir: üyenin
    süzmesi (hide_fees faizi 0 yapar, gizli kart ekstresi düşer) denklemi bozar ve sahibe sahte ihlal yazardı."""
    from app import book

    with book.owner_view():
        return _check(today)


def _check(today: date | None) -> dict:
    today = today or date.today()
    t0 = time.perf_counter()
    statements = db.list_statements()
    refs: dict[str, list[str]] = {}
    checks = (("bakiye_capa", lambda: _bakiye_capa(today)), ("ekstre_denklem", lambda: _ekstre_denklem(statements)),
              ("ekstre_satir_farki", lambda: _ekstre_satir_farki(statements)),
              ("odeme_ekstre", _odeme_ekstre), ("virman_tek_bacak", _virman_tek_bacak), ("bolme_asim", _bolme_asim),
              ("borc_fazla_odeme", _borc_fazla_odeme), ("borc_odenen_tutarsiz", _borc_odenen_tutarsiz), ("kart_borcu", lambda: _kart_borcu(today, statements)),
              ("kurus_alti", _kurus_alti), ("cift_aktarim", _cift_aktarim), ("cift_ekstre", _cift_ekstre))
    for code, fn in checks:
        try:
            if r := fn():
                refs[code] = sorted(set(r))
        except Exception:  # noqa: BLE001 - bir denetimin hatası ötekileri durdurmasın (yalnız kod adı günlüğe)
            log.exception("doğruluk denetimi %s çalışamadı", code)
            refs.setdefault("_hata", []).append(code)
    return {"day": today.isoformat(), "counts": {k: len(v) for k, v in refs.items()}, "refs": refs,
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


def last() -> dict | None:
    try:
        v = json.loads(db.get_setting(SETTING) or "null")
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def run(today: date | None = None) -> dict:
    """Denetle, sonucu deftere yaz (yeni çıkan ihlalleri işaretle). Dönüş yalnız sayılar (günlüğe yazılabilir)."""
    res = check(today)
    res["ran_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # UTC naive, diğer *_at alanları gibi
    prev = last() or {}
    prev_refs = {(k, r) for k, v in (prev.get("refs") or {}).items() for r in v}
    res["new"] = sum(1 for k, v in res["refs"].items() for r in v if (k, r) not in prev_refs)
    res["first_seen"] = prev.get("first_seen") if prev.get("counts") and res["counts"] else res["day"]
    db.set_setting(SETTING, json.dumps(res, ensure_ascii=False))
    _record(res)
    return {"total": sum(v for k, v in res["counts"].items() if k != "_hata"), "new": res["new"], "ms": res["ms"]}


def _uid() -> int:
    u = db.current_user()
    return int(u["id"]) if u and u.get("id") else 0


def _record(res: dict) -> None:
    """Yönetici için yalnız sayılar: system.db integrity_runs (kullanıcı id, gün, tür başına sayı, süre)."""
    from app import system

    try:
        with system.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS integrity_runs (user_id INTEGER NOT NULL, day TEXT NOT NULL,
                         counts_json TEXT NOT NULL, ms REAL, PRIMARY KEY (user_id, day))""")
            c.execute("INSERT OR REPLACE INTO integrity_runs (user_id, day, counts_json, ms) VALUES (?, ?, ?, ?)",
                      (_uid(), res["day"], json.dumps(res["counts"]), res["ms"]))
            c.execute("DELETE FROM integrity_runs WHERE day < date(?, '-90 days')", (res["day"],))
    except Exception:  # noqa: BLE001 - sistem veritabanı yoksa (tek kullanıcı) yalnız deftere yazılır
        log.exception("integrity_runs yazılamadı")


def admin_summary(day: str) -> dict:
    """O günün özeti, yalnız sayılar: {day, spaces, with_issues, by_code: {kod: kullanıcı sayısı}, total, max_ms}."""
    from app import system

    with system.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS integrity_runs (user_id INTEGER NOT NULL, day TEXT NOT NULL,
                     counts_json TEXT NOT NULL, ms REAL, PRIMARY KEY (user_id, day))""")
        rows = [dict(r) for r in c.execute("SELECT counts_json, ms FROM integrity_runs WHERE day = ?", (day,))]
    by: dict[str, int] = {}
    total = with_issues = 0
    for r in rows:
        counts = json.loads(r["counts_json"])
        with_issues += bool(counts)
        for k, n in counts.items():
            by[k] = by.get(k, 0) + 1
            total += n
    return {"day": day, "spaces": len(rows), "with_issues": with_issues, "by_code": dict(sorted(by.items())),
            "total": total, "max_ms": max((r["ms"] or 0 for r in rows), default=0)}


def admin_text(s: dict) -> str:
    parts = ", ".join(f"{k} {n}" for k, n in s["by_code"].items()) or "yok"
    return (f"Doğruluk denetimi {s['day']}: {s['spaces']} defter, {s['with_issues']} defterde ihlal "
            f"(toplam {s['total']}); tür başına defter: {parts}; en uzun {s['max_ms']:.0f} ms")


def report_admin(now: datetime) -> dict | None:
    """Günde bir kez (gece denetiminden sonra): yöneticiye yalnız sayılar — günlük, Telegram (ana alan bağlıysa),
    HESAPKITAP_ADMIN_EMAIL tanımlıysa posta. Hiçbir kullanıcı verisi içermez."""
    from app import mailer, system, telegram

    day = now.date().isoformat()
    if now.hour < REPORT_HOUR or system.get("job:integrity_report") == day:
        return None
    system.put("job:integrity_report", day)
    s = admin_summary(day)
    if not s["spaces"]:
        return s
    text = admin_text(s)
    log.info(text)
    if s["with_issues"]:
        with db.use_space(None, None):
            if telegram.enabled():
                try:
                    telegram.send(text)
                except Exception:  # noqa: BLE001
                    log.warning("doğruluk özeti Telegram'a gönderilemedi")
        to = os.environ.get("HESAPKITAP_ADMIN_EMAIL")
        if to and mailer.enabled():
            try:
                mailer.send(to, f"HesapKitap doğruluk denetimi {day}", text)
            except Exception:  # noqa: BLE001
                log.warning("doğruluk özeti postayla gönderilemedi")
    return s


REPORT_HOUR = 5  # denetim 4'te; özet herkesin denetimi bittikten sonra


# ---------------------------------------------------------------- güncelleme sonrası denetim

POST_DEPLOY_KEY = "integrity:post_deploy"  # sys_settings: {ver, pos, new, books, done}
POST_DEPLOY_BUDGET_S = 2.0  # tur başına süre; bitmezse sonraki tur kaldığı defterden
POST_DEPLOY_CODES = ("ekstre_denklem", "ekstre_satir_farki")


def _existed(ref: str, cutoff: str) -> bool:
    """Ekstre son gece denetiminden önce de var mıydı (sonradan yüklenen ekstrenin uyarısı güncellemeden gelmez)."""
    if not ref.startswith("statement:"):
        return True
    with db.connect() as c:
        r = c.execute("SELECT uploaded_at FROM statements WHERE id = ?", (int(ref.split(":", 1)[1]),)).fetchone()
    return bool(r and r[0] and r[0][:19] <= cutoff)


def post_deploy_new(prev: dict | None) -> int:
    """Bu defterde şimdi çıkan, son gece denetiminde olmayan ekstre uyarısı sayısı (yazmaz). Önceki koşu yoksa 0."""
    if not prev or not prev.get("ran_at"):
        return 0
    cutoff = prev["ran_at"].replace("T", " ")[:19]
    old = prev.get("refs") or {}
    refs = check()["refs"]
    return sum(1 for k in POST_DEPLOY_CODES for r in refs.get(k, ())
               if r not in set(old.get(k, ())) and _existed(r, cutoff))


def post_deploy_tick(spaces=None) -> dict | None:
    """Yeni sürüm açıldıktan sonra her defteri bir kez denetle (yazmaz: deftere ve son koşuya dokunmaz); son gece
    denetiminde olmayan ekstre uyarısı çıkarsa yöneticiye "sistem" bildirimi. Okuma ya da denklem gerilemesi dakikalar
    içinde görülsün, ertesi geceyi beklemesin. Bildirimde yalnız sayılar (kişi, kart, tutar yok). jobs.scheduler her
    turda çağırır; süre bütçesiyle parça parça (kaldığı defter sys_settings'te). İş yoksa None."""
    from app import jobs, system, version

    ver = version.current()
    if not ver:
        return None
    try:
        st = json.loads(system.get(POST_DEPLOY_KEY) or "null") or {}
    except ValueError:
        st = {}
    if st.get("ver") == ver and st.get("done"):
        return None
    if st.get("ver") != ver:
        st = {"ver": ver, "pos": 0, "new": 0, "books": 0, "done": False}
    seen, todo = set(), []
    for path, user in (jobs.spaces() if spaces is None else spaces):
        key = os.path.abspath(path) if path else None
        if key not in seen:  # ortak defter üyesinin alanı sahibin dosyasıdır: bir kez
            seen.add(key)
            todo.append((path, user))
    t0 = time.monotonic()
    while st["pos"] < len(todo):
        if st["pos"] and time.monotonic() - t0 >= POST_DEPLOY_BUDGET_S:
            system.put(POST_DEPLOY_KEY, json.dumps(st))
            return {**st, "finished": False}
        path, user = todo[st["pos"]]
        try:
            with db.use_space(path, user):
                if n := post_deploy_new(last()):
                    st["new"] += n
                    st["books"] += 1
        except Exception:  # noqa: BLE001 - bir defter denetlenemezse ötekiler sürsün
            log.exception("güncelleme sonrası denetim bir defterde çalışamadı")
        st["pos"] += 1
    st["done"] = True
    system.put(POST_DEPLOY_KEY, json.dumps(st))
    if st["new"]:
        log.warning("güncelleme sonrası denetim (%s): %d yeni ekstre uyarısı, %d defter", ver[:12], st["new"], st["books"])
        try:
            from app import feedback, push

            text = f"{st['new']} yeni ekstre uyarısı ({st['books']} defter); son gece denetiminde yoktu."
            for a in feedback.admins():
                push.notify(a["id"], [push.item("sistem", f"sistem:denetim:{ver[:12]}", "Denetim: güncelleme sonrası",
                                                text, push.R_SISTEM)])
        except Exception:  # noqa: BLE001 - bildirim gitmese de sonuç günlükte
            log.exception("güncelleme sonrası denetim bildirimi gönderilemedi")
    return {**st, "finished": True}


# ---------------------------------------------------------------- Yapılacaklar maddesi

HINTS = {
    "bakiye_capa": "Hesabın bakiyesini bankadaki son bakiyeyle karşılaştır; farklıysa güncel bakiyeyi gir ya da eksik "
                   "dökümü yükle.",
    "ekstre_denklem": "Ekstreyi PDF'le karşılaştır: önceki borç, ödemeler ve harcamalar doğru okunmuş mu? Doğruysa "
                      "'Rakamlar doğru' de.",
    "ekstre_satir_farki": "Ekstrede eksik ya da fazla okunmuş bir işlem olabilir; işlemleri PDF'le karşılaştır.",
    "odeme_ekstre": "Bu ekstrenin 'ödendi' bilgisi ödemelerinle uyuşmuyor; ödeme tarihini ve tutarını kontrol et.",
    "virman_tek_bacak": "Bu aktarımın karşı hesaptaki girişi bulunamadı; o hesabın dökümünü yükle ya da aktarım "
                        "başkasına gittiyse 'Kendi hesaplarım arası değil' de.",
    "bolme_asim": "Harcamanın parçalarını düzelt: parçaların toplamı harcamadan büyük olamaz.",
    "borc_fazla_odeme": "Bu borca borcun kendisinden fazla ödeme yazılmış; fazla ödeme satırını sil.",
    "borc_odenen_tutarsiz": "Borcun ödenen tutarı ödemelerle uyuşmuyor; ödeme satırlarına bak.",
    "kart_borcu": "Kart borcu iki yoldan farklı çıktı; kartların son ekstrelerine bak.",
    "kurus_alti": "Tutarda kuruştan küçük basamak var; kaydı açıp tutarı yeniden kaydet.",
    "cift_aktarim": "Bu hareket iki kez içe aktarılmış görünüyor; fazla olanı sil.",
    "cift_ekstre": "Aynı kartın aynı ayı için iki ekstre var; yanlış yüklenmiş olanı sil.",
}
MAX_ITEMS = 10


def _item(c, ref: str) -> dict | None:
    """Kimliği kullanıcıya gösterilecek öğeye çevir: {ref, date, amount, title, route}. Kayıt silinmişse None.
    Yalnız kullanıcının kendi istemcisine gider (Yapılacaklar, /api/integrity); yönetici özetinde yok."""
    kind, _, rid = ref.partition(":")
    if kind == "card_debt":
        return {"ref": ref, "date": None, "amount": None, "title": "Kart borcu (tüm kartlar)", "route": "ekstreler"}
    if not rid.isdigit():
        return None
    rid = int(rid)
    one = lambda sql: c.execute(sql, (rid,)).fetchone()  # noqa: E731
    if kind == "entry" and (r := one("SELECT entry_date, amount, kind, description, counterparty FROM entries WHERE id = ?")):
        return {"ref": ref, "date": r["entry_date"], "amount": r["amount"] if r["kind"] == "gelir" else -r["amount"],
                "title": r["description"] or r["counterparty"] or "Kayıt", "route": f"islemler/m{rid}"}
    if kind == "txn" and (r := one("SELECT t.txn_date, t.amount, t.description FROM transactions t WHERE t.id = ?")):
        return {"ref": ref, "date": r["txn_date"], "amount": -r["amount"], "title": r["description"],
                "route": f"islemler/c{rid}"}
    if kind == "statement" and (r := one("SELECT bank, card_no, period_end, total_debt FROM statements WHERE id = ?")):
        l4 = "".join(ch for ch in r["card_no"] or "" if ch.isdigit())[-4:]
        return {"ref": ref, "date": r["period_end"], "amount": r["total_debt"],
                "title": f"{r['bank']} ••{l4} ekstresi", "route": f"ekstreler/{rid}"}
    if kind == "account" and (r := one("SELECT * FROM accounts WHERE id = ?")):
        return {"ref": ref, "date": r["as_of"], "amount": db._account_balance(c, dict(r)), "title": r["name"],
                "route": f"hesaplar/{rid}"}
    if kind == "own_transfer" and (r := one("SELECT transfer_date, amount, description FROM own_transfers WHERE id = ?")):
        return {"ref": ref, "date": r["transfer_date"], "amount": r["amount"],
                "title": r["description"] or terms.OWN_TRANSFER, "route": f"transferler/v{rid}"}
    if kind == "debt" and (r := one("SELECT person, direction, amount, debt_date FROM debts WHERE id = ?")):
        return {"ref": ref, "date": r["debt_date"], "amount": r["amount"],
                "title": f"{r['person']} ({'borcun' if r['direction'] == 'borc' else 'alacağın'})", "route": f"borclar/{rid}"}
    if kind == "debt_payment" and (r := one("SELECT p.paid_at, p.amount, p.debt_id, d.person FROM debt_payments p "
                                            "JOIN debts d ON d.id = p.debt_id WHERE p.id = ?")):
        return {"ref": ref, "date": r["paid_at"], "amount": r["amount"], "title": f"{r['person']} · ödeme",
                "route": f"borclar/{r['debt_id']}"}
    if kind == "card_payment" and (r := one("SELECT pay_date, amount, description FROM card_payments WHERE id = ?")):
        return {"ref": ref, "date": r["pay_date"], "amount": -r["amount"], "title": r["description"] or "Kart ödemesi",
                "route": "ekstreler"}
    if kind == "bill" and (r := one("SELECT due_date, amount, provider FROM bills WHERE id = ?")):
        return {"ref": ref, "date": r["due_date"], "amount": r["amount"], "title": r["provider"], "route": "faturalar"}
    if kind == "split" and (r := one("SELECT entry_id, txn_id, amount, category FROM splits WHERE id = ?")):
        route = f"islemler/m{r['entry_id']}" if r["entry_id"] else f"islemler/c{r['txn_id']}"
        return {"ref": ref, "date": None, "amount": r["amount"], "title": f"Bölme parçası · {r['category']}", "route": route}
    return None


def details(r: dict | None = None) -> list[dict]:
    """Son koşunun kuralları ve kayıtları: [{code, label, count, hint, items: [...en çok 10]}]. Ortak defter üyesine
    kayıt gösterilmez (gizlenen kart/hesap sızmasın); sayılar ve açıklama kalır."""
    from app import book

    r = r or last() or {}
    member = bool(book.active())
    out = []
    with db.connect() as c:
        for code, refs in (r.get("refs") or {}).items():
            if code not in LABELS:
                continue
            items = [] if member else [i for i in (_item(c, x) for x in refs[:MAX_ITEMS]) if i]
            out.append({"code": code, "label": LABELS[code], "count": len(refs), "hint": HINTS.get(code), "items": items,
                        "route": ROUTES.get(code, "ozet")})
    return out


def summary() -> dict:
    """GET /api/integrity: son koşu (gün, saat, sayılar, süre) ve ayrıntılar; hiç çalışmadıysa last null."""
    r = last()
    if not r:
        return {"last": None, "details": [], "total": 0, "rerun": rerun_left()}
    d = details(r)
    return {"last": {k: r.get(k) for k in ("day", "ran_at", "ms")} | {"counts": {k: v for k, v in r["counts"].items()
                                                                                 if k in LABELS}},
            "details": d, "total": sum(x["count"] for x in d), "rerun": rerun_left()}


RERUN_PER_HOUR = 6


def _rerun_key() -> str:
    u = db.current_user()
    return f"integrity:reruns:{u['id']}" if u and u.get("id") else "integrity:reruns"


def rerun_left() -> int:
    now = time.time()
    try:
        ts = [t for t in json.loads(db.get_setting(_rerun_key()) or "[]") if now - t < 3600]
    except ValueError:
        ts = []
    return max(0, RERUN_PER_HOUR - len(ts))


def rerun() -> dict | None:
    """Kullanıcının 'Yeniden kontrol et'i: saatte RERUN_PER_HOUR. Sınırdaysa None."""
    now = time.time()
    try:
        ts = [t for t in json.loads(db.get_setting(_rerun_key()) or "[]") if now - t < 3600]
    except ValueError:
        ts = []
    if len(ts) >= RERUN_PER_HOUR:
        return None
    db.set_setting(_rerun_key(), json.dumps(ts + [now]))
    run()
    return summary()


# ---------------------------------------------------------------- yazmadan sonra (arka planda, gecikmeli)

AFTER_WRITE_DELAY = 3.0  # saniye: art arda düzenlemeler tek koşuda birleşir
_pending: dict[str, "threading.Timer"] = {}
_pending_lock = threading.Lock()


def auto_enabled() -> bool:
    """Yazmadan sonra yeniden denetim: zamanlanmış işlerle aynı anahtar (JOBS_ENABLED; testlerde kapalı)."""
    return os.environ.get("JOBS_ENABLED", "1") == "1" and os.environ.get("HESAPKITAP_INTEGRITY_AUTO", "1") == "1"


def after_write(db_file: str | None, user: dict | None) -> None:
    """Defterde kayıt değişti: AFTER_WRITE_DELAY sonra o defterde integrity.run (düzeltilen sorunun maddesi düşsün).
    Defter başına tek zamanlayıcı; bekleyen varsa ertelenir. Yalnız daha önce denetlenmiş deftere (koşu kaydı var)."""
    if not auto_enabled():
        return
    key = os.path.abspath(db_file or db.DB_PATH)

    def go():
        with _pending_lock:
            _pending.pop(key, None)
        try:
            with db.use_space(db_file, user):
                if last() is not None:
                    run()
        except Exception:  # noqa: BLE001 - arka plan: isteği etkilemez
            log.exception("yazma sonrası doğruluk denetimi")

    with _pending_lock:
        if old := _pending.pop(key, None):
            old.cancel()
        t = threading.Timer(AFTER_WRITE_DELAY, go)
        t.daemon = True
        _pending[key] = t
        t.start()


def _local_hour(ran_at: str | None) -> int | None:
    """ran_at (UTC naive 'YYYY-MM-DD HH:MM:SS'; eski kayıtta yerel ISO 'T'li) → yerel saat (TZ_NAME)."""
    if not ran_at:
        return None
    from zoneinfo import ZoneInfo

    try:
        t = datetime.fromisoformat(ran_at)
    except ValueError:
        return None
    if "T" in ran_at:  # eski biçim: zaten yerel saat
        return t.hour
    return t.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(os.environ.get("TZ_NAME", "Europe/Istanbul"))).hour


def todo() -> dict | None:
    """Son denetimde ihlal varsa Yapılacaklar maddesi. Kimlik ihlal kümesine bağlı: kapatılırsa aynı küme bir daha
    sorulmaz, yeni bir ihlal çıkınca yeniden görünür. details[].items: hangi kayıt (kullanıcının kendi istemcisine)."""
    r = last()
    if not r or not any(k != "_hata" for k in r.get("counts") or {}):
        return None
    lines = details(r)
    if not lines:
        return None
    sig = hashlib.sha256(json.dumps(r["refs"], sort_keys=True).encode()).hexdigest()[:10]
    n = sum(x["count"] for x in lines)
    when = "Gece yaptığımız kontrolde" if _local_hour(r.get("ran_at")) in (None, 3, 4, 5) else "Son kontrolde"
    return {"id": f"dogruluk:{sig}", "kind": "dogruluk", "severity": "warn", "title": "Rakamları kontrol et",
            "text": f"{when} {n} yerde tutmayan bir rakam gördük: "
                    + "; ".join(f"{x['label'].lower()} ({x['count']})" for x in lines)
                    + ". Bunu biz de gördük, inceliyoruz. Aşağıda hangi kayıt olduğunu ve ne yapabileceğini yazdık.",
            "cta": terms.CTA_LOOK, "route": lines[0]["route"], "action": "integrity", "due": r["day"],
            "period": r["day"][:7], "details": lines, "checked_at": r.get("ran_at") or r["day"], "tag": "Kontrol",
            "rerun": True}


# ---------------------------------------------------------------- elle çalıştırma

def _print(res: dict) -> None:
    print(json.dumps({k: res[k] for k in ("day", "counts", "ms")} | {"refs": res["refs"]}, ensure_ascii=False, indent=1))


def main(argv: list[str]) -> int:
    from app import system

    db.init_db()
    if "--all" in argv:
        from app import jobs

        for path, user in jobs.spaces():
            with db.use_space(path, user):
                print((user or {}).get("id", 0), run())
        return 0
    if argv:
        user = system.user_by_name(argv[0])
        if not user:
            print("kullanıcı yok")
            return 1
        with db.use_space(system.user_db_path(user), user):
            run()
            _print(last())
        return 0
    run()
    _print(last())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
