"""Ortak defter: üye (ör. eş) kendi kullanıcısıyla girer, sahibin defterini görür ve kayıt ekler.

Sahip neyin görünmeyeceğine karar verir; kurallar defterin içinde (settings "book:rules") durur:
    hidden_cards     kart numaraları: işlemleri, ekstreleri, ödemeleri, tahminleri görünmez
    hidden_accounts  hesap id'leri: bakiyesi ve hareketleri görünmez
    hide_fees        faiz, gecikme ve kart ücretleri görünmez (faiz alarmı dahil)
    hidden_pages     sayfalar: menüde yoktur, arka uçları da kapalıdır
ve tek tek işlemler "gizli" işaretlenebilir (entries.hidden, transactions.hidden).

Gizleme sunucuda uygulanır: veri okuma fonksiyonları (kart işlemleri, kayıtlar, ekstreler, hesaplar) üye isteğinde
süzülür; özet, grafik, plan ve tahminler bunlardan hesaplandığı için gizli veri hiçbir toplama da girmez.
"""
import json
import re

from app import db

PAGES = {  # sayfa → üyeye kapanan API önekleri
    "plan": ("/api/plan", "/api/recurring"),
    "istatistik": ("/api/compare", "/api/year-summary", "/api/interest-history"),
    "ekstreler": ("/api/statements",),
    "hesaplar": ("/api/accounts", "/api/jars", "/api/wallets", "/api/net-worth"),
    "transferler": ("/api/transfers",),
    "borclar": ("/api/debts", "/api/debt-payments"),
    "zekat": ("/api/zakat", "/api/donations", "/api/holdings"),
    "tasarruf": ("/api/savings", "/api/goals", "/api/subscriptions", "/api/targets"),
    "fiyatlar": ("/api/prices",),
}
PAGE_LABELS = {"plan": "Planlama", "istatistik": "İstatistik", "ekstreler": "Ekstreler", "hesaplar": "Hesaplar",
               "transferler": "Transferler", "borclar": "Borç / Alacak", "zekat": "Zekât ve Sadaka",
               "tasarruf": "Tasarruf", "fiyatlar": "Fiyatlar"}
# üyenin yapabileceği değişiklikler (geri kalan her yazma sahibe ait)
MEMBER_WRITES = [
    ("POST", r"/api/entries"), ("PATCH", r"/api/entries/\d+"), ("PATCH", r"/api/card-transactions/\d+"),
    ("POST", r"/api/receipts/parse"), ("POST", r"/api/bills/parse"), ("POST", r"/api/splits/propose"),
    ("POST", r"/api/share"), ("POST", r"/api/inbox/\d+/\w+"), ("POST", r"/api/digest/dismiss"),
    ("POST", r"/api/settings/password"), ("PUT", r"/api/me/ui"), ("POST", r"/api/settings/tokens"), ("DELETE", r"/api/settings/tokens/\d+"),
]
MEMBER_CLOSED = ("/api/settings", "/api/admin", "/api/imports", "/api/templates", "/api/book/", "/api/rules",
                 "/api/bank-import", "/api/onboarding", "/api/mail")
MEMBER_OPEN = ("/api/settings/password", "/api/settings/tokens", "/api/book/me")
ENTRY_MEMBER_FIELDS = {"category", "note", "description", "counterparty", "location"}  # başkasının kaydında
EMPTY = {"hidden_cards": [], "hidden_accounts": [], "hide_fees": False, "hidden_pages": []}


def member() -> dict | None:
    """İsteği yapan ortak defter üyesi mi? (sahip ve tek kullanıcılı kurulumda None)"""
    u = db.current_user()
    return u if u and u.get("member_of") else None


def rules() -> dict:
    try:
        return {**EMPTY, **json.loads(db.get_setting("book:rules") or "{}")}
    except ValueError:
        return dict(EMPTY)


def set_rules(data: dict) -> dict:
    r = {**rules(), **{k: v for k, v in data.items() if k in EMPTY}}
    r["hidden_pages"] = [p for p in r["hidden_pages"] if p in PAGES]
    db.set_setting("book:rules", json.dumps(r, ensure_ascii=False))
    return r


def active() -> dict | None:
    """Üye isteğinde uygulanacak kurallar; sahip için None (her şeyi görür)."""
    return rules() if member() else None


# ---------------------------------------------------------------- erişim (middleware)

def denied(method: str, path: str) -> str | None:
    """Üye bu isteği yapabilir mi? Yapamıyorsa nedeni."""
    if any(path.startswith(p) for p in MEMBER_OPEN):
        pass
    elif any(path.startswith(p) for p in MEMBER_CLOSED):
        return "Bu bölüm defter sahibine ait"
    r = rules()
    for page in r["hidden_pages"]:
        if any(path.startswith(p) for p in PAGES.get(page, ())):
            return "Bu sayfa ortak defterde kapalı"
    if re.fullmatch(r"/api/entries/\d+/candidates", path):  # eşleştirme adayları ham kart işlemlerinden gelir
        return "Bu işlemi defter sahibi yapabilir"
    if path.startswith("/api/net-worth/at") and (r["hidden_accounts"] or r["hidden_cards"]):
        return "Geçmiş net durum ortak defterde kapalı"  # geçmiş bakiyeler tüm hesaplardan hesaplanır
    if method in ("GET", "HEAD") or path == "/api/auth/logout":
        return None
    if not any(method == m and re.fullmatch(p, path) for m, p in MEMBER_WRITES):
        return "Bu işlemi defter sahibi yapabilir"
    return None


# ---------------------------------------------------------------- süzgeçler (db okuma fonksiyonları çağırır)

def _hidden_last4(r: dict) -> set[str]:
    return {c[-4:] for c in r["hidden_cards"]}


def filter_statements(rows: list[dict]) -> list[dict]:
    r = active()
    if not r:
        return rows
    hidden = set(r["hidden_cards"])
    return [s for s in rows if s["card_no"] not in hidden]


def filter_card_txns(rows: list[dict]) -> list[dict]:
    r = active()
    if not r:
        return rows
    hidden = set(r["hidden_cards"])
    return [t for t in rows if t.get("account") not in hidden and not t.get("hidden")
            and not (r["hide_fees"] and t.get("txn_type") == "faiz_ucret")]


def filter_entries(rows: list[dict]) -> list[dict]:
    r = active()
    if not r:
        return rows
    accs, last4 = set(r["hidden_accounts"]), _hidden_last4(r)
    return [e for e in rows if not e.get("hidden") and e.get("account_id") not in accs
            and not (e.get("payment") == "kredi_karti" and e.get("card_last4") in last4)]


def filter_card_payments(rows: list[dict]) -> list[dict]:
    """Hesaptan yapılan kart ödemeleri: gizli kartın ya da gizli hesabın ödemesi görünmez."""
    r = active()
    if not r:
        return rows
    last4 = _hidden_last4(r)
    with db.connect() as c:
        names = {n for (n,) in c.execute(f"SELECT name FROM accounts WHERE id IN ({','.join('?' * len(r['hidden_accounts']))})",
                                         r["hidden_accounts"])} if r["hidden_accounts"] else set()
    from app.bank_import import short_bank

    return [p for p in rows if p.get("card_last4") not in last4 and short_bank(p.get("account") or "") not in names]


def filter_accounts(rows: list[dict]) -> list[dict]:
    r = active()
    if not r:
        return rows
    accs = set(r["hidden_accounts"])
    return [a for a in rows if a["id"] not in accs]


def hide_fees() -> bool:
    r = active()
    return bool(r and r["hide_fees"])


def can_see_entry(entry_id: int) -> bool:
    with db.connect() as c:
        e = c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return bool(e) and bool(filter_entries([dict(e)]))


def can_see_txn(txn_id: int) -> bool:
    return any(t["id"] == txn_id for t in db.card_transactions(include_deleted=True))


def info() -> dict:
    """Arayüz için: bu kullanıcı üye mi, neler kapalı."""
    u = member()
    if not u:
        return {"member": False}
    from app import system

    owner = system.user(u["member_of"]) or {}
    r = rules()
    return {"member": True, "owner": owner.get("username"), "hidden_pages": r["hidden_pages"], "hide_fees": r["hide_fees"]}
