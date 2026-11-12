"""Günlük işler: gece yedeği + Telegram hatırlatmaları. Uygulamanın içinde çalışır (host cron'una dokunmadan).

Her iş günde bir kez çalışır; çalıştığı gün settings tablosuna yazılır, container yeniden başlasa da tekrarlamaz.
"""
import asyncio
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import backup, db, finance, logs, money, telegram

log = logging.getLogger("ekstre.jobs")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Istanbul"))
BACKUP_HOUR = 3
REMIND_HOUR = 9
REMIND_DAYS = (3, 1, 0)        # son ödemeye kaç gün kala
STATEMENT_LATE_DAYS = 3        # kesimden kaç gün sonra "ekstreyi yükle" denir


def _tl(x: float) -> str:
    return money.tl(x)


# ---------------------------------------------------------------- yedek

def _main_space() -> bool:
    """Yöneticinin (ana) veritabanı mı? Telegram yönetici kanalıdır: başka kullanıcının verisi oraya gitmez."""
    return os.path.abspath(db.db_path()) == os.path.abspath(db.DB_PATH)


def run_backup(today: date) -> str:
    try:  # gece bakımı: hiçbir kayda bağlanmamış (okunup kaydedilmemiş) fiş/fatura dosyalarını sil
        if removed := db.gc_files():
            log.info("bağsız dosya temizliği: %d", len(removed))
    except Exception:  # noqa: BLE001 - temizlik yedeği engellemesin
        log.exception("bağsız dosya temizliği başarısız")
    path = backup.make_backup(today)
    pw = backup.passphrase()
    if telegram.enabled() and pw and _main_space():
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
        if not s["due_date"] or (s["paid_amount"] is not None and money.diff_k(s["paid_amount"], s["total_debt"]) >= -1):
            continue
        days = (date.fromisoformat(s["due_date"]) - today).days
        if days in REMIND_DAYS:
            when = "BUGÜN" if days == 0 else f"{days} gün kaldı"
            line = f"💳 <b>{s['bank']} {s['card_no']}</b> son ödeme {date.fromisoformat(s['due_date']):%d.%m} ({when})\n" \
                   f"Dönem borcu: <b>{_tl(s['total_debt'])}</b>"
            # asgari tutar bilerek yazılmaz (küçük rakam çapa olmasın; mobil Kartlar ve push ile aynı dil)
            line += "\nTamamını son ödeme gününe kadar ödersen faiz işlemez."
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
        sch = d.get("schedule")
        if sch and sch["next"]:  # ödeme planı olan borç: sıradaki taksit
            nx = sch["next"]
            days = (date.fromisoformat(nx["date"]) - today).days
            if days in REMIND_DAYS or days in (-1, -7):
                when = {0: "BUGÜN", -1: "DÜN GEÇTİ", -7: "1 HAFTA GEÇTİ"}.get(days, f"{days} gün kaldı")
                msgs.append(f"🤝 {'Alacak taksiti' if d['direction'] == 'alacak' else 'Taksit'}: <b>{d['person']}</b> · {nx['no']}. taksit "
                            f"{finance.fmt_asset(max(nx['left'], sch['due_now']), 'TRY')} · "
                            f"{date.fromisoformat(nx['date']):%d.%m} ({when})")
            continue
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
        plan = finance.plan(1, today=today)["months"][0]
        msgs.append(finance.period_report(prev)
                    + f"\n\nBu ay plan: gelir {_tl(plan['income'])}, gider {_tl(plan['expense'])} → net <b>{_tl(plan['net'])}</b>"
                    + "".join(f"\n{'✅' if m['status'] == 'tuttu' else '❌'} {g['name']}: {_tl(m['spent'])} / hedef {_tl(g['target'])}"
                              for g in finance.goals_review(today) if g["active"]
                              for m in g["months"] if m["month"] == prev))

    # 6) Zekât yılı dolmak üzere
    from app import zakat

    msgs += zakat.reminders(today)
    msgs += zakat.holy_reminders(today)
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


def run_session_cleanup(today: date) -> int:
    """Sistem veritabanı: iptal edilmiş ve çok eski oturum kayıtları (app/sessions.py)."""
    from app import sessions

    return sessions.cleanup()


def run_integrity(today: date) -> dict:
    """Gece doğruluk denetimi (app/integrity.py): yalnız sayılar döner; ayrıntı defterin kendi ayarında."""
    from app import integrity

    return integrity.run(today)


def run_zakat(today: date) -> float:
    """Zekât: dolan kameri yılı kalıcı ilerlet, ödenmemiş geçmiş yıl zekâtını (Net durum borcu) yeniden hesapla. Okuma
    yolları bunu yapmaz (sayfa açmak bakiye değiştirmesin); sunucu açılınca ilk turda da çalışır (saat 1 geçtiyse)."""
    from app import zakat

    return zakat.nightly(today)


def run_pending_cleanup(today: date) -> int:
    """Okunamayan ekstrenin bekleyen kopyası (app/support.py): 30 günü dolan dosyalar bu defterden silinir."""
    from app import support

    return support.prune_pending()


INTEGRITY_HOUR = 4
JOBS = [("zakat", 1, run_zakat), ("backup", BACKUP_HOUR, run_backup), ("remind", REMIND_HOUR, run_reminders), ("wallet", 1, run_wallet_loads),
        ("sessions", 3, run_session_cleanup), ("integrity", INTEGRITY_HOUR, run_integrity), ("pending", 2, run_pending_cleanup)]
MAIN_ONLY = {"remind", "sessions"}  # Telegram'a yazan ve sistem veritabanına bakan işler yalnız yöneticinin alanında


def due_jobs(now: datetime) -> list[tuple[str, callable]]:
    today = now.date().isoformat()
    main = _main_space()
    return [(name, fn) for name, hour, fn in JOBS
            if (main or name not in MAIN_ONLY) and now.hour >= hour and db.get_setting(f"job:{name}") != today]


def spaces() -> list[tuple[str | None, dict | None]]:
    """Zamanlanmış işlerin çalışacağı defterler: her fiziksel veritabanı dosyası BİR KEZ, sahibinin bağlamıyla.

    - ana veritabanı (None, None)
    - kendi defteri olan her etkin kullanıcı (ortak defterin sahibi dahil): (defteri, kullanıcı)
    - ortak defter üyesinin kendi ("Benim") defteri, dosyası varsa: (own_db_path, {..., own_book: True}); istekteki
      "kendi" seçimiyle aynı bağlam (app/api/main.py _access)
    Ortak defter üyesi sahibinin dosyasıyla eşlenmez: 10.11.2026'ya dek üye satırı sahibin defterine gidiyordu; üyenin
    kendi defteri hiç gece işi almıyordu, üye id'si sahibinkinden küçükse de işler sahibin defterinde üyenin süzülmüş
    görünümüyle koşabiliyordu. İşler ayrıca jobs.book_space ile sahip görünümünde (book.owner_view) çalışır."""
    from app import system

    out: list[tuple[str | None, dict | None]] = [(None, None)]
    seen: set[str | None] = {None}

    def add(path: str | None, user: dict) -> None:
        key = os.path.abspath(path) if path else None
        if key not in seen:
            seen.add(key)
            out.append((path, user))

    try:
        for u in system.users():
            if u.get("disabled") or u.get("deleted_at"):  # silinmek üzere: iş yok
                continue
            if u.get("member_of"):
                own = system.own_db_path(u)
                if os.path.isfile(own):  # hiç açılmamış "Benim" defteri: boş dosya yaratılmaz
                    add(own, {**u, "own_book": True})
            elif u.get("db_file"):
                add(system.user_db_path(u), u)
    except Exception:  # noqa: BLE001 - sistem veritabanı yoksa (tek kullanıcı) sadece ana alan
        log.exception("kullanıcı listesi okunamadı")
    return out


@contextmanager
def book_space(path: str | None, user: dict | None):
    """Bir defterde arka plan işi: o dosya + sahip görünümü (ortak defter süzgeci yok; işler defterin bütününe bakar)."""
    from app import book

    with db.use_space(path, user), book.owner_view():
        yield


def mail_due(now: datetime) -> bool:
    """E-posta kutusu her POLL_MINUTES dakikada bir kontrol edilir."""
    from app import mailbox

    if not mailbox.enabled():
        return False
    last = db.get_setting("mail:last_attempt")
    return not last or (now - datetime.fromisoformat(last)).total_seconds() >= mailbox.POLL_MINUTES * 60


PURGE_HOUR = 4


def run_global(now: datetime) -> list[str] | None:
    """Sunucu geneli günlük iş (kullanıcı alanından bağımsız): users/_silinen/ altında süresi dolan silinmiş hesap
    klasörlerini kalıcı sil. Günde bir kez; çalıştığı gün sistem veritabanına yazılır. Çalışmadıysa None."""
    from app import system

    today = now.date().isoformat()
    if now.hour < PURGE_HOUR or system.get("job:purge_deleted") == today:
        return None
    system.put("job:purge_deleted", today)
    from app import deletion

    if n := deletion.expire_records(now):  # 1 yılı dolan anonim silme kayıtları
        log.info("eski silme kayıtları düştü: %d", n)
    from app import ai_costs

    ai_costs.prune()  # 13 aydan eski yapay zekâ maliyet kayıtları
    from app import funnel

    funnel.prune()  # Ölçüm: 400 günden eski kişi başı gün satırları, 3 yıldan eski toplamlar
    from app import template_stats

    template_stats.prune()  # 400 günden eski şablon isabet sayaçları
    from app import doc_cache

    doc_cache.prune(now.timestamp())  # 30 günü dolan okuma sonuçları (app/doc_cache.py)
    from app import community_prices

    community_prices.prune()  # KEEP_DAYS'ten eski anonim topluluk fiyatları
    try:  # gün içinde kuyruğa giren ürün adları: gecede bir kez toplu yapay zekâ (günlük çağrı + sert ₺ tavanıyla)
        from app import product_groups

        if n := product_groups.classify_pending(max_calls=product_groups.AI_CALLS_PER_DAY):
            log.info("ürün grubu öğrenildi: %d ad", n)
        with system.connect() as c:  # aynı biçim tek genel ada (çelişen gruplar yönetici panelinde)
            product_groups.consistency(c)
    except Exception:  # noqa: BLE001 - gece bakımını engellemesin
        log.exception("ürün grubu sınıflandırma başarısız")
    try:  # ödeme aracısı adayları: bütün defterlerde "ÖN EK / SAĞ" kalıbı (yalnız ön ek ve sayılar; app/gateways.py);
        # ardından liste değiştiyse kaçan defterlerde işyeri adına bağlı ayarlar taşınır (sync_all)
        from app import gateways

        if got := gateways.nightly(now):
            log.info("ödeme aracısı taraması: %s", got)
    except Exception:  # noqa: BLE001 - gece bakımını engellemesin
        log.exception("ödeme aracısı taraması başarısız")
    return system.purge_archived(now=now.timestamp())


def run_account_purge(now: datetime) -> list[int]:
    """Bekleme süresi dolan (silinmek üzere bekleyen) hesapları kalıcı sil. Her turda: tek ucuz sorgu; böylece
    "6 Kasım'da silinecek" denen hesap o gün silinir, gece 4'ü beklemez."""
    from app import apple, deletion

    done = deletion.purge_due(now)
    try:  # davetle açılıp INVITE_TTL içinde e-postası doğrulanmayan hesaplar (app/email_verify.py)
        from app import email_verify

        if n := email_verify.purge_invite_pending(now.timestamp()):
            log.info("doğrulanmayan davet hesapları silindi: %d", len(n))
    except Exception:  # noqa: BLE001
        log.exception("invite pending purge failed")
    try:  # Apple belgesi iptali başarısız olduysa (ağ, Apple): zamanı gelenler yeniden denenir
        apple.retry_pending(now.timestamp())
    except Exception:  # noqa: BLE001
        log.exception("apple revoke retry failed")
    return done


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
            except telegram.TelegramError as e:  # geçici ağ hatası: bir dakika sonra yeniden denenir, traceback gereksiz
                log.warning("telegram inbox: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("telegram inbox failed")
        try:
            if purged := await asyncio.to_thread(run_account_purge, now):
                log.info("süresi dolan silinmiş hesaplar kalıcı silindi: %s", purged)
        except Exception:  # noqa: BLE001
            log.exception("account purge failed")
        try:
            if removed := await asyncio.to_thread(run_global, now):
                log.info("silinmiş hesap klasörleri temizlendi: %s", removed)
        except Exception:  # noqa: BLE001
            log.exception("purge_deleted failed")
        try:  # dünkü/bugünkü doğruluk denetiminin yönetici özeti (yalnız sayılar), günde bir kez
            from app import integrity

            await asyncio.to_thread(integrity.report_admin, now)
        except Exception:  # noqa: BLE001
            log.exception("integrity report failed")
        try:  # yeni sürüm açıldı: her defteri bir kez denetle; son gecede olmayan ekstre uyarısı → yöneticiye bildirim
            from app import integrity

            if (got := await asyncio.to_thread(integrity.post_deploy_tick)) and got.get("finished"):
                log.info("güncelleme sonrası denetim: %s", got)
        except Exception:  # noqa: BLE001
            log.exception("post-deploy integrity failed")
        try:  # ürün grubu sürümü yükseldiyse arka planda parça parça yeniden gruplama (app/product_groups.py regroup_tick)
            from app import product_groups

            if product_groups.regroup_pending() and (got := await asyncio.to_thread(product_groups.regroup_tick)):
                log.info("ürün grupları yeniden gruplama: %s", got)
        except Exception:  # noqa: BLE001
            log.exception("regroup tick failed")
        try:  # yapay zekâ okuması tutmayan ekstreler: şablonla yeniden okunabilir mi (app/statement_reread.py; önbellek)
            from app import statement_reread

            if got := await asyncio.to_thread(statement_reread.tick):
                log.info("ekstre yeniden okuma taraması: %s", got)
        except Exception:  # noqa: BLE001
            log.exception("statement reread tick failed")
        try:  # fiş kalemi anahtarları (ad düzeltilmiş eski satırlar) arka planda parça parça (app/receipt_rekey.py)
            from app import receipt_rekey

            if receipt_rekey.pending() and (got := await asyncio.to_thread(receipt_rekey.tick)):
                log.info("fiş kalemi anahtarları: %s", got)
        except Exception:  # noqa: BLE001
            log.exception("receipt rekey tick failed")
        try:  # resmî fiyatlar: BigQuery → official_prices (UTC 05:00 sonrası, yarım saatte bir bakar; app/price_import_bq.py)
            from app import price_import_bq

            if price_import_bq.enabled() and (got := await asyncio.to_thread(price_import_bq.tick, now)):
                log.info("resmî fiyat: %s", got)
        except Exception:  # noqa: BLE001
            log.exception("official prices import failed")
        try:  # ürün kataloğu (Market Fiyatı ürün listesi → gruplama dayanağı) + gruplama tutarlılığı, günde bir
            from app import product_catalog

            if (got := await asyncio.to_thread(product_catalog.tick, now)):
                log.info("ürün kataloğu: %s", got)
        except Exception:  # noqa: BLE001
            log.exception("product catalog refresh failed")
        try:  # topluluk fiyatları → BigQuery (00:30 sonrası, dün; app/community_export.py; yazıcı kimliği yoksa kapalı)
            from app import community_export

            if community_export.enabled() and (got := await asyncio.to_thread(community_export.tick, now)):
                log.info("topluluk dışa aktarımı: %s", got)
        except Exception:  # noqa: BLE001
            log.exception("community export failed")
        try:  # telefona bildirim (app/push.py): günlük hatırlatmalar 09:00'dan sonra, kuyruk her dakika
            from app import push

            if push.enabled():
                await asyncio.to_thread(push.tick, now)
        except Exception:  # noqa: BLE001
            log.exception("push failed")
        for path, user in spaces():
            with book_space(path, user):  # her defterin işi kendi veritabanında, sahip görünümüyle (to_thread bağlamı taşır)
                for name, fn in due_jobs(now):
                    try:
                        result = await asyncio.to_thread(fn, now.date())
                        log.info("job %s %s ok: %s", name, logs.user_ref(user), result)  # ad/e-posta değil id (S8)
                    except Exception:  # noqa: BLE001 - bir iş patlarsa diğerleri sürsün
                        log.exception("job %s failed", name)
                    finally:
                        db.set_setting(f"job:{name}", now.date().isoformat())
        await asyncio.sleep(60)
