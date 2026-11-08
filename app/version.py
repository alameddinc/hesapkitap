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
    "digit_fix",              # fiş denetiminde karışan rakam (termal baskı 6↔8 …): check.digit_fix [{index, name, from, to,
                              # digits "6→8", unit_price_from?, unit_price_to?, evidence [ocr|qty|history|group], score,
                              # history_price?}], digit_fix_all {indices, total_ok} | null, residual (TL), suspect [sıra],
                              # groups [{rate, items_total, tax, printed, indices, ok}], line_count {printed, read} | null;
                              # kendiliğinden uygulanmaz
    "price_groups",           # markadan bağımsız ürün grubu: /api/prices products[].group {key "sut|1l", label "Süt · 1 L",
                              # pending? (yapay zekâ bekliyor; geçici grup)} | null; history[].estimated, history[].uid ("m<kayıt id>")
    "community_prices",       # anonim topluluk fiyatı: GET/PUT /api/me/price-share {on} → {on, text} (meta.price_share);
                              # meta.ui.price_share ve PUT /api/me/ui {price_share} aynı tercih;
                              # fişte digit_fix evidence "community" + community_price, community_note "başkaları ₺89,00 ödüyor"
    "where_to_buy",           # GET /api/prices/groups?q&limit → [{key, label, best: [{chain, price, brand?, source own|community,
                              # date?, n?}] ucuzdan, ≤5}] (402 fiyatlar); q grup anahtarıysa ("sut|1l", "ekmek") yalnız o
    "shopping_list",          # GET/POST /api/shopping-list, PATCH/DELETE /api/shopping-list/{id}, DELETE ?done=true,
                              # GET /api/shopping-list/plan (402 alisveris_listesi; ortak defterde paylaşılır)
    "price_alerts",           # fiş okuma/kayıtlı fiş check.price_alerts ve POST /api/entries price_alerts [{index, name, paid,
                              # last_paid, last_date, change_pct, chain, note "geçen sefer ₺30"}]; push türü "fiyat"
    "price_unit",             # birim fiyat: /api/prices products[] ve stores[], /api/prices/groups best[] ve plan best:
                              # unit_price_per (₺ / L, kg, adet; grubun boyundan) + per_unit "L"|"kg"|"adet" (boysuzda yok);
                              # aramada boy ("1l süt") ve adı tam eşleşen önce, boylar farklıysa litre fiyatı ucuz önce
    "price_categories",       # kategori: products[].category, products[].group.category, /api/prices/groups [].category
                              # (sabit liste product_groups.CATEGORIES; "Süt ve Kahvaltılık" …), yoksa null
    "price_subcategories",    # alt kategori: products[].subcategory, products[].group.subcategory, /api/prices/groups
                              # [].subcategory ("Süt ve Kahvaltılık" › "Peynir"; app/product_taxonomy.py), yoksa null
    "official_prices",        # /api/prices/groups best[].source "resmi" (varsayılan kapalı; sunucu ayarlıysa) + attribution
                              # "Kaynak: Market Fiyatı (…)" — istemci teklif listesinin altında gösterir
    "price_region",           # Bölgem: meta.ui.region (il adı | null), PUT /api/me/ui {region} (81 il, app/provinces.py;
                              # null/"" kaldırır, bilinmeyen 422); resmi teklifte region ("İzmir" | "ulusal"),
                              # discount?, promotion?, change_7d?/change_30d? (yüzde); seçilmemişse İstanbul
    "shopping_optimize",      # GET /api/shopping-list/optimize?max_stops=1..3&stop_cost → en iyi 1/2/3 duraklı plan,
                              # recommended_k, single (hepsi tek yerden, vs_recommended), unpriced; meta.ui.stop_cost,
                              # PUT /api/me/ui {stop_cost} (₺, null → 100)
    "price_market",           # piyasa bandı: GET /api/prices/market?group&days=90 → {group, region, days [{day, min, median,
                              # max, cheapest_chain, chains, source resmi|community}], attribution}; /api/prices history[]
                              # vs_market {median, min, max, pct, verdict ucuz|normal|pahali, cheapest_chain, day, source}
    "market_notes",           # fiş okuma / kayıtlı fiş check.market_notes [{index, name, group, label, paid_unit, market_median,
                              # market_min, cheapest_chain, pct, day, source resmi|community, region il|"ulusal"|null}]:
                              # birim fiyat piyasa ortancasından ≥%10 ve ≥₺2 yüksek (fiş günü ±3), en çok 5, pahalı önce;
                              # okuma hatası olabilecek kalem (digit_fix, suspect, kayma, adet × birim) atlanır
    "price_advice",           # /api/prices products[] (üst düzey): market (bool: piyasa karşılaştırması anlamlı mı; kafe /
                              # lokanta / zincir dışı false), market_ref {median, min, cheapest_chain, day, source} | null,
                              # advice null | {kind cheaper_at, chain, price, paid, diff, qty, saving, unit_price_per?,
                              # per_unit?, source, n?, date?} | {kind good_price, paid, median, pct} | {kind store_compare,
                              # stores [{store, price, date}], cheapest, diff}; aliases? (birleşen yazılışlar)
    "price_suggest",          # GET /api/prices/suggest?q&limit → {items [{name, group_key, group_label, last_price,
                              # last_store, count}], did_you_mean {name, group_label} | null} (402 fiyatlar)
    "shopping_assumed_size",  # alışveriş satırı assumed {key, label, size_label} | null (boysuz satır en yaygın boya),
                              # group.pinned?; GET /api/shopping-list items[].best {chain, price, unit_price_per?, per_unit?,
                              # source, n?} | null; GET /api/shopping-list/{id}/sizes → [{key, label, size_label, chain,
                              # price, unit_price_per?, per_unit?}]; PATCH {group_key | null}, POST {text, qty, group_key?};
                              # "6x Süt" → text "Süt", qty 6
    "shopping_headline",      # /api/shopping-list/optimize headline {title, subtitle, cta, chains, total, saving, count} |
                              # null, unpriced_info [{id, text, reason no_size|no_group|no_price|pending, hint}],
                              # empty {title, text} | null; stops[].items[].assumed
    "community_quality",      # topluluk teklifi: son 7 gün, ≥3 kişi, indirimsiz; n, window_days, discounted_price?, region?;
                              # aynı zincirde Market Fiyatı da varsa topluluk önce + official {price, date?, region?},
                              # differs (fark > %10); yalnız toplamı tutan fişin satırları yazılır
    "push_routes",            # "sistem" push route "#/diger/ayarlar/sistem" (yöneticiye Sistem durumu), geri_bildirim
                              # "#/diger/ayarlar", fiyat tek üründe "#/diger/fiyatlar/<ürün anahtarı>"
    "grouping_quality",       # GET /api/admin/official-prices grouping_quality {names, grouped, grouped_pct, pending,
                              # not_product, by_source, conflicts {count, top [{form, groups [{key, label, n}], resolved_to}],
                              # at}, wide_spread {count, day, top [{group_key, label, min, max, ratio}]}, catalog
                              # {products, with_generic, brands, updated_at} | null}, disagreements [{chain, group_key,
                              # label, days, community, official, pct}]
    "shopping_units",         # alışveriş satırı qty + unit (adet|kg|g|L|ml), amount_label "2,5 kg", mult, unit_mismatch;
                              # POST/PATCH {qty ≤ 999 (g/ml ≤ 99999), unit}; metinden "2.5 kg elma", "elma 2,5kg", "6 süt";
                              # best.line_total, plan line_total = teklif × mult (g→kg, ml→L); unpriced reason unit_mismatch;
                              # zincir adları yalnız zincir ("BİM (Dost)" → "BİM", şube/semt yok)
    "shopping_autofill",      # GET /api/shopping-list/suggest?q → {items [{text, group_key, label, source recent|own|group,
                              # count?}]}, /frequent → {items [{text, group_key, label, count, last_date}], days}, /due →
                              # {items [{text, group_key, label, qty, unit, last_date, interval_days, days_since, reason}]};
                              # POST /api/shopping-list/bulk {items [{text, qty?, unit?, group_key?}]} → {added, skipped, items}
    "shopping_trips",         # PUT/DELETE /api/shopping-list/saved-plan {k?} ("Bu planla git"); GET /api/shopping-list
                              # saved_plan {k, total, chains [{chain, total, item_ids}], items, created_at} | null, items[].planned;
                              # POST /api/shopping-list/complete {carry_over} → trip; GET /api/shopping-trips {trips},
                              # /{id} (items), POST /{id}/reuse {only_bought}, DELETE /{id}; GET /api/shopping-trips/savings
                              # {month, year, month_trips, year_trips, basis "market_median"} (402 alisveris_listesi)
    "shopping_receipt_link",  # POST /api/entries shopping_link {status linked|ask, target list|trip, trip_id, chain, count,
                              # names, in_plan, confident, saving?} | null; GET /api/shopping-list/receipt-match[?entry_id]
                              # (son 2 günün bağlanmamış fişi) → aday | null; POST /link-receipt {entry_id, target?, trip_id?},
                              # POST /receipt-dismiss {entry_id}; items[].actual {unit_price, line_total, entry_id}
    "shop_store_filter",      # mağaza süzgeci (kişi başı): meta.ui.shop_hidden {chains, local}, PUT /api/me/ui {shop_hidden
                              # {chains?, local?}}; GET /api/shopping-list/stores {hidden, chains [{chain, hidden}], local
                              # {hidden, names, count}}; kapatılan mağaza En ucuz / boy / plan / Nereden alınır / daha ucuz
                              # önerisinde yok; GET /api/shopping-list hidden_stores, optimize hidden {count, chains, local},
                              # /api/prices/groups [].hidden_count, sizes [].hidden_count
    "shop_store_pin",         # satırı mağazaya bağla: PATCH /api/shopping-list/{id} {store | null}, items[].store,
                              # best.pinned, store_missing; optimize pinned [{id, text, chain}], stops[].pinned,
                              # pinned_unpriced, chains="A,B" (1–3, "Marketleri ben seçeyim") → manual {chains, missing},
                              # chain_options; PUT saved-plan {k?, chains?}; unpriced reason store_no_price
    "shop_brand_size",        # marka + boy: GET /api/prices/brands?group → {group, brands [{brand, sizes, chains}]},
                              # GET /api/shopping-list/{id}/brands, GET /api/prices/offers?group&brand → {group, label,
                              # size_label, brand, offers, hidden, attribution}; PATCH {brand | null}, items[].brand,
                              # sizes?brand=; plan markalı satırda o markanın fiyatı (unpriced reason brand_no_price)
    "shop_best_value",        # sizes küçükten büyüğe + size_amount, best_value ("En kârlı": birim fiyatı en düşük boy);
                              # optimize plans[].saving_vs_market, size_saving, items[].better_size (En kârlı boy önerisi),
                              # headline.profit {saving, text, basis, size_saving, size_text} | null
    "summary_installments",   # /api/summary installments {month, current, total, count, in_spend, expected, remaining,
                              # remaining_count} — dönemin taksitleri, harcamaya EKLENMEZ (in_spend zaten totals.expense
                              # içinde; expected ekstresi beklenen); GET /api/installments?month → aynısı + items [{uid,
                              # source card|manual|projected, title, card, installment "3/6", no, of, amount, in_spend,
                              # fee, due_date, row (dokununca düzenlenecek satır), spender?, spender_name?}]
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
