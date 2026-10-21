"""Günlük işler: gece yedeği + Telegram hatırlatmaları. Uygulamanın içinde çalışır (host cron'una dokunmadan).

Her iş günde bir kez çalışır; çalıştığı gün settings tablosuna yazılır, container yeniden başlasa da tekrarlamaz.
"""
import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import backup, db, finance, telegram

log = logging.getLogger("ekstre.jobs")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Istanbul"))
BACKUP_HOUR = 3
REMIND_HOUR = 9
REMIND_DAYS = (3, 1, 0)        # son ödemeye kaç gün kala
STATEMENT_LATE_DAYS = 3        # kesimden kaç gün sonra "ekstreyi yükle" denir


def _tl(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " ₺"


# ---------------------------------------------------------------- yedek

def run_backup(today: date) -> str:
    path = backup.make_backup(today)
    pw = backup.passphrase()
    if telegram.enabled() and pw:
        with open(path, "rb") as f:
            blob = backup.encrypt(f.read(), pw)
        telegram.send_document(os.path.basename(path) + ".enc", blob,
                               caption=f"🔒 Günlük yedek {today:%d.%m.%Y} (şifreli)")
    return path


# ---------------------------------------------------------------- hatırlatmalar

def reminders(today: date) -> list[str]:
    """Bugün gönderilecek mesajlar. Saf fonksiyon: test edilebilir, gönderim ayrı."""
    msgs = []
    statements = db.list_statements()

    # 1) Son ödeme yaklaşan ekstreler (gerçek tutar belli, henüz "Ödedim" denmemiş)
    for s in statements:
        if not s["due_date"] or (s["paid_amount"] is not None and s["paid_amount"] >= s["total_debt"] - 0.01):
            continue
        days = (date.fromisoformat(s["due_date"]) - today).days
        if days in REMIND_DAYS:
            when = "BUGÜN" if days == 0 else f"{days} gün kaldı"
            line = f"💳 <b>{s['bank']} {s['card_no']}</b> son ödeme {date.fromisoformat(s['due_date']):%d.%m} ({when})\n" \
                   f"Dönem borcu: <b>{_tl(s['total_debt'])}</b>"
            if s["min_payment"] and s["min_payment"] < s["total_debt"]:
                line += f" · asgari {_tl(s['min_payment'])}\n⚠️ Sadece asgariyi ödersen kalan borca faiz işler."
            msgs.append(line)

    # 1b) Faturalar
    for b in db.list_bills():
        if b["paid_at"]:
            continue
        days = (date.fromisoformat(b["due_date"]) - today).days
        if days in REMIND_DAYS or days == -1:
            when = {0: "BUGÜN", -1: "DÜN GEÇTİ"}.get(days, f"{days} gün kaldı")
            msgs.append(f"🧾 <b>{b['provider']}</b> faturası son ödeme {date.fromisoformat(b['due_date']):%d.%m} ({when})\n"
                        f"Tutar: <b>{_tl(b['amount'])}</b>")

    # 1c) Borç / alacak vadeleri
    for d in db.list_debts(open_only=True):
        if not d["due_date"]:
            continue
        days = (date.fromisoformat(d["due_date"]) - today).days
        if days in REMIND_DAYS or days in (-1, -7):
            when = {0: "BUGÜN", -1: "DÜN GEÇTİ", -7: "1 HAFTA GEÇTİ"}.get(days, f"{days} gün kaldı")
            amount = finance.fmt_asset(d["remaining"], d["asset"])
            what = "Borcun" if d["direction"] == "borc" else "Alacağın"
            msgs.append(f"🤝 {what}: <b>{d['person']}</b> · {amount} · vade {date.fromisoformat(d['due_date']):%d.%m} ({when})")

    # 1d) Hediye/giyim kartı son kullanma (içinde para varken)
    for w in db.list_wallets():
        if w["expires_at"] and w["current"] > 0:
            days = (date.fromisoformat(w["expires_at"]) - today).days
            if days in (30, 7, 1, 0):
                msgs.append(f"🎁 <b>{w['name']}</b> kartında {_tl(w['current'])} var; "
                            + ("bugün sona eriyor" if days == 0 else f"{days} gün sonra sona eriyor") + ", harcamayı unutma.")

    # 2) Kesim tarihi geçti ama yeni ekstre yüklenmedi
    latest: dict[str, dict] = {}
    for s in statements:
        if s["card_no"] not in latest or s["period_end"] > latest[s["card_no"]]["period_end"]:
            latest[s["card_no"]] = s
    for s in latest.values():
        if s["next_period_end"]:
            late = (today - date.fromisoformat(s["next_period_end"])).days
            if late == STATEMENT_LATE_DAYS:
                msgs.append(f"📄 <b>{s['bank']} {s['card_no']}</b> ekstresi {date.fromisoformat(s['next_period_end']):%d.%m}'de kesildi. "
                            "İnternet şubesinden indirip uygulamaya yükle; son ödeme hatırlatması ona göre gelir.")

    # 2b) Bütçe eşikleri: her kategori/eşik ayda bir kez
    month = finance.current_period(today)
    for b in finance.budgets(month):
        for th in (100, 80):
            if b["pct"] >= th:
                key = f"budget:{month}:{b['category']}:{th}"
                if not db.get_setting(key):
                    db.set_setting(key, today.isoformat())
                    icon = "🚨" if th == 100 else "⚠️"
                    msgs.append(f"{icon} <b>{b['category']}</b> bütçesi %{b['pct']:.0f}: {_tl(b['spent'])} / {_tl(b['limit'])}")
                break

    # 3) Yeni dönemin ilk günü (ay dönümü): biten dönemin özeti + yeni dönemin planı
    if finance.current_period(today) != finance.current_period(today - timedelta(days=1)):
        prev = finance.add_months(finance.current_period(today), -1)
        t = finance.summary(prev, today=today)["totals"]
        plan = finance.plan(1, today=today)["months"][0]
        msgs.append(finance.period_report(prev)
                    + (f"\nFaiz/ücret: {_tl(t['fees'])}" if t["fees"] else "")
                    + f"\n\nBu ay plan: gelir {_tl(plan['income'])}, gider {_tl(plan['expense'])} → net <b>{_tl(plan['net'])}</b>"
                    + "".join(f"\n{'✅' if m['status'] == 'tuttu' else '❌'} {g['name']}: {_tl(m['spent'])} / hedef {_tl(g['target'])}"
                              for g in finance.goals_review(today) if g["active"]
                              for m in g["months"] if m["month"] == prev))
    return msgs


def run_reminders(today: date) -> int:
    if not telegram.enabled():
        return 0
    msgs = reminders(today)
    for m in msgs:
        telegram.send(m)
    return len(msgs)


# ---------------------------------------------------------------- zamanlayıcı

def run_wallet_loads(today: date) -> int:
    return db.auto_load_wallets(today)


JOBS = [("backup", BACKUP_HOUR, run_backup), ("remind", REMIND_HOUR, run_reminders), ("wallet", 1, run_wallet_loads)]


def due_jobs(now: datetime) -> list[tuple[str, callable]]:
    today = now.date().isoformat()
    return [(name, fn) for name, hour, fn in JOBS
            if now.hour >= hour and db.get_setting(f"job:{name}") != today]


def mail_due(now: datetime) -> bool:
    """E-posta kutusu her POLL_MINUTES dakikada bir kontrol edilir."""
    from app import mailbox

    if not mailbox.enabled():
        return False
    last = db.get_setting("mail:last_attempt")
    return not last or (now - datetime.fromisoformat(last)).total_seconds() >= mailbox.POLL_MINUTES * 60


async def scheduler() -> None:
    from app import mailbox

    while True:
        now = datetime.now(TZ)
        if mail_due(now):
            db.set_setting("mail:last_attempt", now.isoformat(timespec="seconds"))
            try:
                log.info("mail poll: %s", await asyncio.to_thread(mailbox.poll))
            except Exception:  # noqa: BLE001 - bağlantı hatası vb. bir sonraki turda yeniden denenir
                log.exception("mail poll failed")
        if telegram.enabled():  # bota iletilen PDF'ler (her dakika)
            try:
                if n := await asyncio.to_thread(telegram.poll_inbox):
                    log.info("telegram inbox: %s mesaj", n)
            except Exception:  # noqa: BLE001
                log.exception("telegram inbox failed")
        for name, fn in due_jobs(now):
            try:
                result = await asyncio.to_thread(fn, now.date())
                log.info("job %s ok: %s", name, result)
            except Exception:  # noqa: BLE001 - bir iş patlarsa diğerleri sürsün
                log.exception("job %s failed", name)
            finally:
                db.set_setting(f"job:{name}", now.date().isoformat())
        await asyncio.sleep(60)
