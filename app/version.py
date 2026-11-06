"""Sürüm kontrolü: bu kurulumun sürümü (git commit) ile GitHub'daki son sürüm.

Kurulumun sürümü: APP_VERSION / Render (RENDER_GIT_COMMIT) / Railway (RAILWAY_GIT_COMMIT_SHA) ortam değişkeni ya da
derleme sırasında yazılan app/VERSION dosyası. GitHub sorgusu 6 saatte bir yapılır; sadece herkese açık commit
bilgisi çekilir, bu kurulumdan hiçbir şey gönderilmez.
"""
import json
import os
import time
from pathlib import Path

from app import system

REPO = os.environ.get("HESAPKITAP_REPO", "alameddinc/hesapkitap")

# İstemci el sıkışması (S17): mobil/web eski sunucuya göre davranabilsin. API_VERSION, geriye uyumsuz değişiklikte
# artar; CAPABILITIES yalnız eklenir (bir yetenek adı kaldırılmaz, anlamı değişmez). İstemci bilmediği adı yok sayar.
API_VERSION = 2
CAPABILITIES = (
    "idempotency",            # Idempotency-Key başlığı: kopan yanıtta tek kayıt
    "email_verify",           # kayıtta e-posta kodu adımı (açık olup olmadığı: flags.email_verify_required)
    "sessions",               # /api/me/sessions: açık oturumlar, iptal
    "invites_v2",             # davet önizleme (GET /api/auth/invite/{kod}), yenile/iptal, ortak defter yetkisi
    "raw_note",               # işlem satırında raw (banka açıklaması) ve note_user
    "summary_left",           # /api/summary totals.left / left_after_savings / card_spend_incl_pending / fees_hidden
    "transactions_totals",    # /api/transactions satır bayrakları counts_as_* ve ?totals=1
    "transactions_paging",    # /api/transactions?limit&before → {rows, next}
    "payables_totals",        # /api/payables?totals=1 (window ?days, paid_this_month)
    "networth_explain",       # /api/net-worth spendable_parts / spendable_explain
    "transfer_totals",        # /api/transfers totals
    "bills_overall",          # /api/bills/summary overall, average_12
    "holdings_totals",        # /api/holdings totals
    "budgets_totals",         # /api/budgets totals
    "rules_test_all",         # /api/rules/test pattern'sız: tüm kurallar
    "debt_payment_delete",    # DELETE /api/debt-payments/{id}; pay yanıtında payment_id
    "integrity",              # Yapılacaklar kind 'dogruluk' (gece doğruluk denetimi)
    "verification",           # belge verification alanları, belge_dogrulanamadi, documents/{id}/confirm
    "inbox_documents",        # Gelen kutusu kind dokum|ekstre, /api/inbox/{id}/import ve /file
    "cards_summary",          # GET /api/cards/summary; /api/transactions?card=
    "integrity_details",      # dogruluk details[].items/hint, GET /api/integrity, POST /api/integrity/run
    "bills_by_bill_month",    # /api/bills/summary months[] faturanın ait olduğu aya göre (paid_date / bill_month)
    "universal_links",        # davet link'i https://<adres>/davet/<kod> (+ web_link eski biçim)
    "insights_hidden",        # GET /api/insights/hidden: gizlenen Özet uyarıları (Geri getir: POST /api/insights/restore)
    "web_only_settings",      # GET /api/settings/web-only: mobilde olmayan, web Ayarlar'da kurulan ayarlar + web adresi
    "social_nonce",           # POST /api/auth/social/nonce; /api/auth/{google,apple} gövdesinde nonce (D1)
    "apple_revoke",           # /api/auth/apple gövdesinde authorization_code: hesap silinince Apple belgesi iptal edilir
    "uncategorized_groups",   # GET /api/uncategorized/groups, POST /api/uncategorized/apply (remember) ve /undo
    "uncategorized_ai",       # POST /api/uncategorized/ai-suggest (açık olup olmadığı: groups yanıtında ai.available)
    "uncategorized_bulk",     # POST /api/uncategorized/apply-many (tek undo_id), groups ?sort=total, one_off/one_off_max, ai_max
    "merchant_rename",        # POST /api/merchants/rename {display, key|uid}: görünen işyeri adı (geri al: /api/uncategorized/undo)
    "receipt_text",           # POST /api/documents/parse ocr_text (+ ocr_source vision|mlkit): telefonda çıkarılan metin
                              # (yardımcı: yapay zekâ fotoğrafı okur; metin şablon/KDV-VKN yedeği/yerel okuma için)
    "app_update",             # el sıkışmasında app_update {ios|android: {latest, min, store_url}}; boş {} = kapalı
    "interest_page",          # GET /api/interest: faiz ve ücretler sayfası (kart/ay/kalem, neden, önleme önerileri)
    "daily_reads",            # günlük tek okuma sayacı: plan.reads / me/ai reads {period:"day", limit, used, left, resets_at,
                              # monthly_cap, extra_left}; hak doldu gövdesinde daily_*, needed; yanıtlarda charged {daily, extra}
    "receipt_tax",            # fiş okumasında ve kayıtta vat_total, vat_lines [{rate, amount}], tax_no (VKN), tax_office,
                              # legal_name (POST/PATCH /api/entries); GET /api/receipts/export?period=month|year&value
                              # ya da ?from&to, &format=xlsx|csv (Excel: "Fiş listesi" + "KDV özeti")
    "card_limit",             # cards/summary kart başına limit, limit_source (ekstre|elle), available, used_pct; PUT /api/cards/limit
    "subcategories",          # alt kategori: /api/subcategories (CRUD, order), meta.subcategories{,_editable}, işlem/kayıt/kural
                              # subcategory alanı, /api/transactions?category&subcategory, by_category[].children
    "total_check",            # fiş okumasında total_check "ok"|"kontrol"|null: toplam telefon/PDF metninde geçiyor mu
                              # (kontrol: istemci tutarı vurgular, "Tutarı fişle karşılaştır"); text_match {date, merchant}
    "push",                   # telefona bildirim: POST/DELETE /api/push/devices, GET/PUT /api/push/prefs (enabled: sunucuda
                              # yapılandırılmış mı), POST /api/push/test; yük data {route, kind, id}
    "dekont_parse",           # havale/EFT dekontu: POST /api/dekont/parse (foto/PDF → transfer taslağı, yön/kişi/hesap/
                              # çift kayıt/borç önerisi), POST /api/dekont/save (409 zaten eklenmiş), POST /api/documents/classify
                              # (PDF metni: dekont|ekstre önerisi, hak düşmez); /api/bills/parse transfer_check → looks_like_transfer
    "member_spend",           # Aile "Kim ne harcadı": GET /api/book/member-spend?month (402 plan_required aile_rapor),
                              # /api/transactions?who=u<id>|ortak, /api/budgets status[].by_person (ortak defterde)
    "book_join",              # var olan hesapla ortak deftere katılma: GET/POST /api/book/join/{kod} (önizleme, katıl;
                              # own_subscription), POST /api/book/leave (üye ayrılır, Benim defteri kalır); Aile koltuğu:
                              # book/me seat {state: own|seat|grace|none, grace_until?}, /api/book members[].seat, joined,
                              # seats {total, used, members, max_members, waiting}, POST /api/book/members/{id}/seat
    "spender_assign",         # Aile harcayanı atama (ortak defter; 402 aile_rapor, 404 tek kişilik/Benim, 403 sadece görür):
                              # PUT /api/spender {source manual|card|recurring, id, who u<id>|ortak|null} → {who, set};
                              # PUT /api/cards/{card_no}/spender {who u<id>|null} → {card_no, who}; GET /api/cards/spenders;
                              # transactions ve cards/summary recent satırlarında spender, spender_set; cards[].spender
    "feedback",               # uygulama içi "Sorun bildir": POST /api/feedback multipart message (1–2000), screenshots
                              # (0–3 jpeg/png/webp, ≤ 5 MB), context (JSON ≤ 4 KB) → {ok, id}; oturumsuz/demo/üye açık,
                              # saatte 10 (429)
    "save_book_choice",       # fiş/fatura onayında defter ve harcayan: POST /api/entries ve /api/bills spender u<id>|ortak
                              # (ortak defterde; değilse yok sayılır, defterde olmayan kişi 400); X-HK-Book: kendi|ortak
                              # başlığı hk_book çerezinden önce gelir; okumadaki receipt_file/file_name üyenin öbür
                              # defterindeyse kopyalanır (hak yeniden düşmez)
    "read_cache",             # aynı belge aynı defterde 30 gün yeniden okunmaz: okuma yanıtında cached: true (charged
                              # yok); okunamayan fotoğraf günde 5'ten sonra 1 hak; me/ai reads family_limit/family_left
)


def handshake(with_version: bool = False) -> dict:
    """{api_version, capabilities, flags[, version]}. flags: sunucuda o an açık olan davranışlar."""
    from app import app_update, email_verify

    out = {"api_version": API_VERSION, "capabilities": list(CAPABILITIES), "app_update": app_update.config(),
           "flags": {"email_verify_required": email_verify.required(),
                     "hold_unverified": os.environ.get("HESAPKITAP_HOLD_UNVERIFIED", "0").strip() == "1"}}
    if with_version:
        out["version"] = current()
    return out
TTL = 6 * 3600


def current() -> str | None:
    for k in ("APP_VERSION", "RENDER_GIT_COMMIT", "RAILWAY_GIT_COMMIT_SHA"):
        if v := os.environ.get(k, "").strip():
            return v
    f = Path(__file__).with_name("VERSION")
    if f.exists() and (v := f.read_text().strip()):
        return v
    return None


def platform() -> str:
    if os.environ.get("RENDER"):
        return "render"
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_GIT_COMMIT_SHA"):
        return "railway"
    if os.environ.get("FLY_APP_NAME"):
        return "fly"
    return "docker"


def _get(url: str):
    import httpx

    return httpx.get(url, timeout=8, headers={"Accept": "application/vnd.github+json"}).json()


def check(force: bool = False) -> dict:
    cur = current()
    cached = json.loads(system.get("version_check") or "null")
    if cached and not force and cached.get("current") == cur and time.time() - cached["checked"] < TTL:
        return cached
    out = {"current": cur, "checked": time.time(), "latest": None, "behind": None, "changes": [], "platform": platform()}
    try:
        head = _get(f"https://api.github.com/repos/{REPO}/commits/main")
        out["latest"] = head["sha"]
        out["latest_date"] = head["commit"]["committer"]["date"]
        if cur and not head["sha"].startswith(cur[:7]):
            cmp = _get(f"https://api.github.com/repos/{REPO}/compare/{cur}...main")
            out["behind"] = cmp.get("ahead_by")
            out["changes"] = [c["commit"]["message"].splitlines()[0][:100] for c in reversed(cmp.get("commits") or [])][:8]
        elif cur:
            out["behind"] = 0
    except Exception:  # noqa: BLE001 - GitHub'a ulaşılamazsa sessizce geç
        out["error"] = "GitHub'a ulaşılamadı"
    system.put("version_check", json.dumps(out))
    return out
