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

Elle çalıştırma: python -m app.integrity [kullanıcı adı]   (ad yoksa ana defter; --all: herkes)
"""
import hashlib
import json
import logging
import os
import sys
import time
from datetime import date, datetime

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
            and s.get("equation_diff") is not None and money.diff_k(abs(s["equation_diff"]), 1) >= 0]


def _ekstre_satir_farki(statements: list[dict]) -> list[str]:
    """I3, satırlar: okunan işlemler (ödeme hariç) − özet ≥ 1 TL. Fark kullanıcının sildiği satırlardan geliyorsa
    (silinenler dahil edilince kapanıyorsa) bilinçli silmedir, ihlal değil."""
    cand = [s for s in statements if s.get("source") != "excel" and not s.get("confirmed_at")
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


def _kurus_alti() -> list[str]:
    with db.connect() as c:  # app/money.py ile aynı tablo/kolon listesi (altın/döviz borcu hariç)
        return [k for k, n in money.off_cents(c).items() for _ in range(n)]


def _cift_aktarim() -> list[str]:
    with db.connect() as c:
        same_hash = _q(c, "SELECT import_hash AS h FROM entries WHERE import_hash IS NOT NULL "
                          "GROUP BY import_hash HAVING COUNT(*) > 1")
        # aynı hesap, aynı gün, aynı tutar, aynı yön; iki FARKLI belgeden (PDF + Excel aynı dönemi iki kez)
        cross = _q(c, """SELECT a.id FROM entries a JOIN entries b ON b.account_id = a.account_id
                          AND b.entry_date = a.entry_date AND b.kind = a.kind AND ROUND(b.amount * 100) = ROUND(a.amount * 100)
                          AND b.id > a.id AND b.bank_doc_id IS NOT NULL AND a.bank_doc_id IS NOT NULL
                          AND b.bank_doc_id <> a.bank_doc_id
                         WHERE a.import_hash IS NOT NULL AND b.import_hash IS NOT NULL AND a.skipped = 0 AND b.skipped = 0""")
    return [f"hash:{hashlib.sha256(r['h'].encode()).hexdigest()[:8]}" for r in same_hash] + [f"entry:{r['id']}" for r in cross]


def _cift_ekstre() -> list[str]:
    with db.connect() as c:
        rows = _q(c, """SELECT card_no || ':' || substr(period_end, 1, 7) AS k FROM statements
                        WHERE COALESCE(source, '') <> 'excel' GROUP BY card_no, substr(period_end, 1, 7) HAVING COUNT(*) > 1""")
    return [f"card_month:{hashlib.sha256(r['k'].encode()).hexdigest()[:8]}" for r in rows]  # kart no kimlikte açık değil


# ---------------------------------------------------------------- çalıştırma

def check(today: date | None = None) -> dict:
    """Defterin değişmezlerini denetle (yazmaz). {day, counts: {kod: n}, refs: {kod: [kimlik]}, ms}."""
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
                refs[code] = sorted(set(r)) if code != "kurus_alti" else r
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


# ---------------------------------------------------------------- Yapılacaklar maddesi

def todo() -> dict | None:
    """Son denetimde ihlal varsa Yapılacaklar maddesi. Kimlik ihlal kümesine bağlı: kapatılırsa aynı küme bir daha
    sorulmaz, yeni bir ihlal çıkınca yeniden görünür."""
    r = last()
    if not r or not any(k != "_hata" for k in r.get("counts") or {}):
        return None
    counts = {k: v for k, v in r["counts"].items() if k in LABELS}
    sig = hashlib.sha256(json.dumps(r["refs"], sort_keys=True).encode()).hexdigest()[:10]
    lines = [{"code": k, "label": LABELS[k], "count": n} for k, n in counts.items()]
    n = sum(counts.values())
    first = next(iter(counts))
    return {"id": f"dogruluk:{sig}", "kind": "dogruluk", "severity": "warn", "title": "Rakamları kontrol et",
            "text": f"Gece yaptığımız kontrolde {n} yerde tutmayan bir rakam gördük: "
                    + "; ".join(f"{x['label'].lower()} ({x['count']})" for x in lines)
                    + ". Bunu biz de gördük, inceliyoruz. Bu arada ilgili kayda bir göz atmak istersen buradan açabilirsin.",
            "cta": terms.CTA_LOOK, "route": ROUTES.get(first, "ozet"), "action": "integrity", "due": r["day"],
            "period": r["day"][:7], "details": lines, "tag": "Kontrol"}


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
