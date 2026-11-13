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
    "payables_key",           # /api/payables kalemlerinde key: kararlı, benzersiz kimlik (liste anahtarı; tahminin id'si yok)
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
    "receipt_qr",             # POST /api/documents/parse qr_text (tekrarlanabilir): telefonun çözdüğü karekod içerikleri
                              # (GİB e-belge JSON, pompa ÖKC); yanıtta rule qr_bill|qr_fuel|pos_slip, qr {kind}, qr_fixed
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
    "push_announce",          # yöneticinin duyurusu: POST /api/admin/announce/preview {platform, below_version, test_only} →
                              # {users, devices, by_platform, opted_out, enabled}; POST /api/admin/announce {title (≤80),
                              # body (≤300), route "#/…"|"store", platform all|ios|android, below_version, test_only,
                              # force} (409 duplicate: aynı metin 10 dk); GET /api/admin/announcements. Push türü "duyuru";
                              # yük data action "store" → istemci mağaza sayfasını açar (route yine "#/ozet")
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
    "funnel_events",          # Ölçüm: POST /api/events {name} (paywall_view | plans_open | purchase_start |
                              # purchase_success | purchase_cancel | signup_demo) → {ok, counted}; yalnız günlük toplam,
                              # kişi başı saatte 60 (429); bilinmeyen ad 422. Eski sunucu: istemci hiç göndermez
    "feedback",               # uygulama içi "Sorun bildir": POST /api/feedback multipart message (1–2000), screenshots
                              # (0–3 jpeg/png/webp, ≤ 5 MB), context (JSON ≤ 4 KB) → {ok, id}; oturumsuz/demo/üye açık,
                              # saatte 10 (429)
    "support_chat",           # destek yazışması (app/support.py): GET /api/support {thread {status, unread}, messages[]
                              # author user|admin|system, seen, report, pending, action {type retry, ref, pending, done}},
                              # GET /api/support/unread, POST /api/support/seen, GET .../messages/{id}/screenshot/{n};
                              # mesaj POST /api/feedback ile; yönetici GET /api/admin/support (gelen kutusu),
                              # GET|PATCH /api/admin/support/{uid} (0: oturumsuz/demo), POST .../{uid}/seen|reply
                              # {message, retry_ref, resolve}, GET .../messages/{id}/structure; push türü "destek"
    "feedback_reply",         # yöneticinin yanıtı kullanıcıya ulaşır (support_chat ile aynı sürüm; eş ad)
    "upload_report",          # yükleme hata satırında report {code, file_type, pages, bank_guess, has_text};
                              # POST /api/support/report multipart report (JSON), bank_name, note, send_doc, file →
                              # {ok, id, pending}; yöneticiye yalnız maskeli yapı + teşhis (dosya değil)
    "upload_retry",           # GET /api/support/pending, DELETE /api/support/pending/{id}, POST .../{id}/retry {password,
                              # remember} → yükleme sonucu satırı + {pending_id, done}; 30 gün, kendi defterinde
    "save_book_choice",       # fiş/fatura onayında defter ve harcayan: POST /api/entries ve /api/bills spender u<id>|ortak
                              # (ortak defterde; değilse yok sayılır, defterde olmayan kişi 400); X-HK-Book: kendi|ortak
                              # başlığı hk_book çerezinden önce gelir; okumadaki receipt_file/file_name üyenin öbür
                              # defterindeyse kopyalanır (hak yeniden düşmez)
    "read_cache",             # aynı belge aynı defterde 30 gün yeniden okunmaz: okuma yanıtında cached: true (charged
                              # yok); okunamayan fotoğraf günde 5'ten sonra 1 hak; me/ai reads family_limit/family_left
    "digit_fix",              # fiş denetiminde karışan rakam (termal baskı 6↔8 …): check.digit_fix [{index, name, from, to,
                              # digits "6→8", unit_price_from?, unit_price_to?, evidence [ocr|qty|history|group], score,
                              # history_price?, ocr_agrees? (telefon metni de aynı yanlışı okumuş: güven düşük)}],
                              # digit_fix_all {indices, total_ok} | null, residual (TL), suspect [sıra],
                              # groups [{rate, items_total, tax, printed, indices, ok}], line_count {printed, read} | null;
                              # kendiliğinden uygulanmaz
    "reread_keeps_edits",     # POST /api/receipts/reread {receipt_file, total, items?} ve POST /api/entries/{id}/receipt-
                              # reread {items?} (yoksa kayıtlı kalemler): elle düzeltilen tutar yeni okumada korunur;
                              # yanıtta kept_edits [{index, name, read, amount}]
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
                              # okuma hatası olabilecek kalem (digit_fix, suspect, kayma, adet × birim) atlanır; bant
                              # dağınık / marka rafında güvenilmez karşılaştırma (market_ref.comparable) not almaz
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
    "shop_combo_picker",      # zincir × marka × boy seçicisi: GET /api/prices/combos?group&chain, GET /api/shopping-list/
                              # {id}/combos?chain&all_sizes → {keys, rows [{chain, brand|null, key, size_label, price,
                              # unit_price_per?, per_unit?, source, date?, n?, product?}], chains, brands, sizes, hidden,
                              # attribution[, selected {store, brand, key}]}; seçim tek PATCH {store, brand, group_key}
    "shop_brand_auto",        # addaki marka (app/shop_brand.py): "çaykur turist çay 1 kg" → items[].brand "Çaykur",
                              # brand_line "Turist" (adında bu kelimeler geçen ürünler), brand_label "Çaykur Turist",
                              # brand_auto true; brand_missing (o marka + boyda fiyat yok), any_brand {chain, price,
                              # brand?, line_total, unit_price_per?, per_unit?} | null (marka fark etmez olsaydı en
                              # ucuzu); PATCH {brand: null} "Marka fark etmez" (bir daha addan okunmaz); /api/prices/
                              # groups?q="çaykur çay" → satırlarda brand / brand_label / any_brand
    "shop_unit_sizes",        # kg / g / L / ml satırında da boy listesi: sizes yalnız o türün boyları + size_unit
                              # (L|kg|adet), varsayılan boy o türden (miktarla aynı boy önce); "kahve" gibi üst adda
                              # biçimlerin boyları (size_label "Türk Kahvesi · 100 g"); PATCH text'te miktar okunur
                              # ("1 kg çekirdek kahve" → text "çekirdek kahve", qty 1, unit kg)
    "summary_installments",   # /api/summary installments {month, current, total, count, in_spend, expected, remaining,
                              # remaining_count} — dönemin taksitleri, harcamaya EKLENMEZ (in_spend zaten totals.expense
                              # içinde; expected ekstresi beklenen); GET /api/installments?month → aynısı + items [{uid,
                              # source card|manual|projected, title, card, installment "3/6", no, of, amount, in_spend,
                              # fee, due_date, row (dokununca düzenlenecek satır), spender?, spender_name?}]
    "summary_expected",       # /api/summary expected {month, source "plan", expense, income, net, parts {recorded_expense,
                              # pending_installments, pending_subscriptions, pending_recurring_expense, pending_bills,
                              # pending_expense, recorded_income, pending_income}} | null (geçmiş dönem) +
                              # definitions.expected ((i) metni). = Planlama'nın o ayki "kesin" satırı (finance.expected);
                              # totals/by_category/budgets/trend'e hiçbir şey eklemez
    "recurring_duplicates",   # aynı olabilecek iki sabit kalem (yalnız öneri): GET /api/recurring/duplicates → [{id
                              # "sabit:<drop>:<keep>", reason bagli_degil|ayni_odeme, title, text, kind, months, keep {id,
                              # description, category, amount, linked}, drop {...}}]; POST .../merge {keep, drop} → {kept,
                              # removed, moved, unlinked, undo_id} (409 öneri yok; geri al POST /api/imports/{id}/undo);
                              # POST .../separate {a, b} ("Ayrı kalsın", bir daha önerilmez); /api/reminders kind
                              # "sabit_tekrar" (action recurring_merge, keep_id, drop_id, keep_name, drop_name, amount)
    "summary_forecast",       # Özet "Dönem sonu": /api/summary expected'a variable_estimate (= Planlama months[].estimated)
                              # ve forecast_net (= months[].net_with_estimate, "her zamanki harcamanla"); /api/plan
                              # months[].parts (expected.parts ile aynı döküm). Eski alanlar aynen durur
    "period_naming",          # dönem ADI başladığı aydan (anahtar YYYY-MM dönemin bittiği ay, değişmez): ay dönümü 5 →
                              # 2026-12 = 5 Kas – 4 Ara = "Kasım dönemi". /api/meta period {name, label} + period_naming
                              # "start"; /api/summary period {name, label, span, start, end} ve vs_prev {basis same_day|
                              # full, day, prev_month, prev_name, current, prev, diff, change, incomplete} (başlıktaki
                              # giderle aynı sayım); /api/digest label (yeni ad), start, end, span, prev_label
    "txn_search",             # GET /api/transactions?q=… metin araması (işyeri/açıklama/kategori/not/kişi/tutar; month
                              # yoksa tüm dönemler, satırda month)
    "salary_raise",           # maaş zammı yüzdeyle + yatan maaşa göre öneri (app/salary.py): SalaryChangeIn gross |
                              # net | pct+basis (brut|net) → sunucu brüte çevirir, pct/pct_basis saklanır; POST
                              # /api/recurring/{id}/payroll draft_resolved {gross, prev_gross, old_net, new_net,
                              # gross_pct, net_pct, normal_month, text, advance {first, base, diff, days, next},
                              # advance_text}; GET /api/recurring/drift?salary=1 → maaş kalemleri type "maas" (title,
                              # text, old, new, pct, expected_pct, actual_pct, advance, month, accept_label,
                              # dismiss_label); POST /api/recurring/salary-drift/accept|dismiss {recurring_id, month}
    "reminders_doc_group",    # /api/reminders ekstre/dokum maddelerinde short ("Örnek Bank ••1234 (ekstre 14 Kas)",
                              # "Örnek Bank Vadesiz (döküm)"): Özet'te iki ve daha çok bekleyen belge tek "N belge
                              # bekleniyor" grubunda, her biri kendi satırı ve Yükle'siyle. Kesim günü son ekstrelerden
                              # öğrenilir (ay sonu kırpılmaz); atlanan ay varsa en son geçen kesim sorulur
    "donation_payment",       # zekât ödemesi / bağış nasıl verildi: POST /api/zakat/people/{id}/payments ve POST
                              # /api/donations gövdesinde payment nakit|havale|kredi_karti (+ account_id, card_last4) →
                              # gider olarak da yazılır (Bağış & Sadaka; kredi kartı: "ekstre bekleniyor", ekstreyle
                              # eşleşince tek sayım), havalede recipient (kime). Ödeme silinir/düzeltilirse açtığı gider
                              # de. payment yoksa eski davranış (gider yok). GET /api/zakat unexpensed [{id, person_id,
                              # person, amount, pay_date, year_start}]: ödenmiş ama gider olarak görünmeyen zekât → POST
                              # /api/zakat/payments/{id}/expense {payment, account_id?, card_last4?, recipient?, pay_date?}
                              # → {entry_id, created} ya da POST .../no-expense ("gerek yok", bir daha sorulmaz)
    "donation_payment_gold",  # "Nasıl ödedin?" genişledi: payment banka (banka hesabından; hesap ops., cüzdana yazılmaz) |
                              # altin_bozdurma (altın satılıp parası verildi) | altin (doğrudan altın). Altınla: gider TL
                              # karşılığıyla yazılır, hesaptan düşmez, döküm/ekstreyle eşleşmez; zekâtta asset_id +
                              # asset_qty (ops.) o altın varlığından düşer, ödeme silinince geri konur. Bilinmeyen payment
                              # 422 (eski davranış). GET /api/zakat people[].payments[] how, account_id, card_last4,
                              # method_editable, asset_id, asset_qty, asset_label, asset_unit; PATCH
                              # /api/zakat/payments/{id} payment (+ account_id, card_last4, recipient, asset_id,
                              # asset_qty): ödeme şekli sonradan değişir (açtığı gider yeniden yazılır)
    "summary_interest_income", # GET /api/summary totals.interest_income: gelirin İÇİNDEKİ faiz geliri (gelire dahil;
                              # Özet "içinde faiz geliri ₺X · arındırılacak"); ortak defterde faiz gizliyse 0
    "statement_reread",       # yapay zekâ okuması tutmayan ekstre şablonla yeniden okunabilir: GET /api/statements
                              # [].reread_rows (N | null), /api/reminders kind "ekstre_oku" (action statement_reread,
                              # statement_id, rows; "Şimdi değil" = POST /api/reminders/ekstre_oku:<id>/dismiss), GET
                              # /api/statements/{id}/reread-preview → {available, rows, sig, items [{kind added|removed|
                              # changed, label, text, note?}], diff, totals_before/after, equation_ok_after}, POST
                              # .../reread-apply {sig?} → {ok, undo_id, added, changed, removed} (409: değişti / yok)
    "debt_plan",              # borç kapatma planı (app/debt_plan.py): GET /api/debt-plan?monthly&order=tarih|kucuk (eski faiz|soz → tarih)
                              # (önizleme) → {monthly, monthly_source, suggestion, order, orders, totals, debts [{key
                              # kart:<id>|borc:<id>, kind card|person, remaining, progress, closes, warning, payable}],
                              # months [{month, label, total, payments, interest, left}], debt_free, this_month {items
                              # [{key, amount, done, text, payable}]}, warnings, shortfall {min_monthly, text} | null,
                              # pace {months, text} | null (yalnız elden borç), stuck, excluded, can_edit, summary_card
                              # {eligible}}; istemci yazarken önizler (kaydetmez), durunca PUT /api/debt-plan {monthly, order};
                              # paket satırı "borc_plani" (ücretsiz); push türü "borc" (elden borcun söz verilen günü)
    "statement_fix",          # tutmayan ekstreyi elle düzelt: GET /api/statements [].check_kind ("rows": özet doğru,
                              # işlem listesi tutmuyor | "summary": özet tutmuyor | null), fix_diff (satırlar −
                              # beklenen), fix_hint {kind, text, suggest {txn_type, amount, txn_date} | null, row_id?},
                              # can_fix; GET /api/statements/{id}/rows → {rows, check}; POST .../rows {txn_date?,
                              # description?, amount (artı), txn_type, dry_run?} ve PATCH .../rows/{txn_id} {amount?,
                              # txn_type?, txn_date?, is_deleted?, dry_run?} → {ok, undo_id?, check {ok, diff, hint}};
                              # işaret türden (iade, ödeme eksi); geri al POST /api/imports/{undo_id}/undo
    "debt_plan_commit",       # borç planını başlat (anlık görüntü): POST /api/debt-plan/commit {monthly?, order?} →
                              # plan yanıtı; GET /api/debt-plan commitment {started_at, start_label, monthly, order,
                              # status {key ok|behind|ahead|done, title, text}, changed {title, reasons, action,
                              # confirm} | null, differs, ...} | null, commit_offer, commit_history; kart satırında harm
                              # {title, steps, compare, info} (tutar son ödeme gününe kadar kartın tamamına yetmezse)
    "debt_plan_groups",       # borç planında kişi kişi satırlar: this_month.groups [{key, kind card|person|fixed, title,
                              # count, amount, done, full, text, due_label, items [this_month.items satırı], payable (tek
                              # borçta)}], months[].groups [{key, kind, title, count, amount, closes, keys}]; elden borç
                              # ödemeleri yuvarlı (₺50 katı, kişi başı en az ₺250, küçük kalan yok); rules_info (ⓘ metni)
    "debt_schedule",          # ödeme planı olan borç (yalnız kayıt; app/debt_schedule.py): POST/PATCH /api/debts
                              # {plan_installment, plan_count | plan_end (YYYY-MM), plan_start (YYYY-MM), plan_day,
                              # creditor_type vergi_sgk|banka|kisi|diger, plan_interest yok|var|bilmiyorum, amount?
                              # (toplam; boşsa taksit × sayı)}; PATCH {plan_off: true} planı kaldırır; GET /api/debts
                              # [].schedule {installment, count, start_month, end_month, day, creditor_label, next {no,
                              # date, amount, left} | null, due_now, overdue, paid_count, text} | null. Borç planında
                              # kind "fixed": her ay önce ayrılır; POST /api/debts/{id}/pay amount null → sıradaki taksit
                              # (bütün borç değil); Ödemeler'de sıradaki taksit; push "borc" taksit gününde
    "debt_schedule_edit",     # ödeme planı borcun alt kaydı: PUT /api/debts/{id}/schedule {plan_installment, plan_count |
                              # plan_end, plan_start, plan_day, creditor_type, plan_interest, amount? (borç toplamını da
                              # değiştir)} açık TL "borcum"a sonradan plan ekler / planı düzenler (tutar korunur; taksitler
                              # kalan üzerinden: ilk taksit ayından önce ödenen plana girmez); DELETE …/schedule planı
                              # kaldırır (elden borç kalır). Dönüş {ok, debt}. schedule'a total, paid_before, left_count,
                              # last_amount eklendi
    "summary_interest_paid",  # GET /api/summary interest_paid {total, interest, tax, fee, count, statements, has_cards,
                              # state var|yok|ekstre_yok} | null (ortak defterde faiz gizli): Özet "Bu dönem ödenen faiz
                              # ve ücret ₺X" kartı; total = totals.fees = Faiz sayfasının o ayı (finance.interest_paid);
                              # last_period {month, name, label, total, interest, tax, fee, count, statements, state} |
                              # null: ekstre_yok iken son ekstreli dönem ("Ekim döneminde …")
    "ozet_gorunum",           # Özet görünümü: Minimal (düzen "sade" ile başlar; kaydı yoksa varsayılan) / Ayrıntılı (klasik) /
                              # Sade (ui.simple); düzende "=ad": öbür görünümün saklı sırası (80 öğeye kadar). POST /api/events
                              # ozet_gorunum_{minimal|ayrintili|sade}, ozet_degisti_{nereden}_{nereye} (yalnız toplam);
                              # admin funnel "gorunum" kartı
    "net_display",            # GET /api/net-worth net_display (ve totals.net_display): ekranda gösterilen Net durum =
                              # net − kesilecek kart ekstresi (tahmini; card_estimated) = net_after_estimates. "net" eski
                              # anlamında (kesilecek ekstre düşülmez) kalır; eski istemci onu gösterir
    "summary_interest_latest",  # GET /api/summary interest_latest {total, interest, tax, fee, count, by_type, state
                              # var|yok, statement_count, statements [{id, bank, last4, period_end, total}], source
                              # ("Mavi Bank 7 Kas · Örnek Bank 5 Kas ekstreleri" | "3 ekstre")} | null (faiz gizli ya da
                              # kart ekstresi yok): Özet "Son ekstrelerinde faiz ve ücret ₺X" (first_look.latest_fees)
    "payables_this_period",   # GET /api/payables?totals=1 totals.this_period {month, end, total, count, overdue,
                              # overdue_count, next_due, estimated}: Özet "Bu ay ödenecek ₺X" (dönem sonuna kadar)
    "first_look",             # İlk gün özeti: POST /api/statements/upload kaydedilen ekstrede statement_id; GET
                              # /api/first-look?ids=1,2 → {first (ilk hafta), statement_count, statements, to_pay {total,
                              # items [{statement_id, card, amount, due_date, days_left, status}], note}, past_due, fees
                              # | null, installments {total, count, months, next, last_month, last_label}, steps [{key
                              # dokum|butce|hatirlatma, title, text, cta, platform?}]} (app/first_look.py)
    "statement_help",         # GET /api/statement-help?platform=web|mobile → {title, sections [{key, title, icon, steps
                              # [{text}]}], tips [{text}]}: "Ekstre nasıl indirilir?" tek kaynak; **kalın** işaretli metin
    "debt_terms",             # borç planı v4, borcun koşulları (yalnız "borcum"; boş = eski davranış): POST/PATCH /api/debts
                              # {creditor_type kisi|vergi_sgk|banka|magaza|diger (planı olmayan borçta da), pay_terms
                              # taksitli|parca|tek|bilinmiyor (taksitli = ödeme planı alanlarıyla), due_strict true kesin |
                              # false söz | null varsayılan (kurumda ve planlıda kesin)}; GET /api/debts [].terms,
                              # terms_label, strict, creditor_label (geçerli değerler). plan_off / DELETE schedule alacaklı
                              # türünü korur. GET /api/debt-plan: orders [tarih, kucuk]; debts[] terms, terms_label,
                              # strict, date_kind kesin|soz, late {kind, head, text, open, close, need_monthly?, warning?,
                              # hint?, action? {label "Yeni tarihte anlaştık", date, confirm}} (istemci PATCH due_date),
                              # saving {month, text} (tek seferde: birikim), day_note {text}, tl_text/approx_note (altın,
                              # döviz: fiyatı varsa plana girer); months[].payments[] saving/saved/from_saved/units_text;
                              # this_month.items[] saving/from_saved/pay_total; day_notes, price_note; commitment.saved
                              # [{title, amount, text}]; push "borc" kesin tarihte "ödeme günü"
    "debt_terms_receivable",  # alacakta da koşullar ve ödeme planı (borçla aynı yapı): POST/PATCH /api/debts direction
                              # "alacak" {creditor_type kisi|kurum|diger ("Kimden"; yöne uymayan tür çevrilir: vergi/banka/
                              # mağaza → kurum, borçta kurum → diger), pay_terms taksitli|parca|tek|bilinmiyor, due_strict
                              # (boşsa söz), plan_* (TL; plan_interest alacakta yok sayılır)}; PUT/DELETE /api/debts/{id}/
                              # schedule alacakta da; GET /api/debts alacakta da terms, terms_label (Parça parça, Tek
                              # seferde…), strict, creditor_label ("Kurum / firma"). Borç planı alacağı almaz; push "borc"
                              # alacakta nötr ("Ahmet: 2. taksit günü bugün", "söz verdiği gün"), rota #/diger/borclar/kayit/<id>
    "card_interest",          # Kartlar ekranında faiz satırı (sunucu hesaplar; debt_plan.card_interest, planla aynı hesap):
                              # GET /api/cards/summary cards[].interest ve GET /api/payables (statement, in_total) [].interest
                              # {state after_due|partial|before_due, level bad|warn|info, remaining, amount (faiz + vergi;
                              # ortak defterde faiz gizliyse null), rate, rate_source ekstre|genel, rate_note, text} | null
    "debt_payoff",            # faizli kredinin erken kapatılması (faizden çıkış): POST/PATCH /api/debts ve PUT
                              # /api/debts/{id}/schedule {payoff_amount (bugün kapatma tutarı; null siler), payoff_date?
                              # (boşsa bugün), plan_rate? (aylık %, ≤15)} yalnız plan_interest "var"ken (yoksa 422); yalnız
                              # gönderilen alan yazılır (eski istemci silmez); schedule.payoff {amount, date, rate, now,
                              # left, interest, benefit} | null, schedule.rate. GET /api/debt-plan: debts[] (kind fixed)
                              # payoff {state ask|plan|no_benefit|not_reached|known, title, text, note?, month?, label?,
                              # amount?, installments?, saved?} | null; months[].payments[] ve this_month.items[] key
                              # "borc:<id>:kapat" (kind fixed, payoff: true; birikim saving/saved ya da kapatma
                              # from_saved/pay_total/interest_saved); kapatma satırında close_early {debt_id, amount,
                              # label, confirm} → POST /api/debts/{id}/pay {amount, paid_at, close_early: true} (borç
                              # ödenen toplamla kapanır); totals.payoff_saving
    "debt_jar",               # borç planı birikimi kumbarada (Premium "kumbara"; plan ücretsiz): POST /api/debts/{id}/jar
                              # {name?, goal?, deadline?, monthly?, account_id?} → {id, jar} (402 kumbara, 409 açık kumbarası
                              # var, 422 uygun değil); GET /api/debt-plan debts[].jar {state offer|locked|linked, ...; linked:
                              # id, balance, goal, pct, text, can_move, pay {label, jar_id, debt_id, amount, from_jar,
                              # close_early, partial, leftover, text, confirm} | null}, this_month.items[] jar_id, jar_action
                              # {label, jar_id, amount, note} (POST /api/jars/{id}/moves), jar_pay; debt_jar {available,
                              # premium, can_open, can_move, can_pay, lock, saved_this_month}; commitment.saved[].jar_id.
                              # POST /api/jars/{id}/pay-debt {amount?, paid_at, account_id?} → {payment_id, move_id, paid,
                              # from_jar, leftover, jar_closed, debt_closed, debt_left, close_early, message, ask | null}
                              # (Geri al: DELETE /api/debt-payments/{payment_id}: kumbara hareketi de geri gelir).
                              # GET /api/jars [].debt {id, title, state acik|kapandi|silindi, label, remaining?, suggestion?}
                              # | null; PATCH /api/jars/{id} {unlink_debt: true} ("başka hedefe çevir")
    "kumbara_shares",         # kumbara katılımcıları ve aylık pay (yalnız TL kumbarası): POST /api/jars/{id}/people
                              # {user_id | name, share?} (402 kumbara, 409 zaten var, 400 defterde değil), PATCH
                              # /api/jars/{id}/people/{pid} {name?, share? (null: eşit böl), sort?} (402 kumbara), DELETE
                              # (katkısı varsa geçmiş kalır: result removed|deleted); PATCH /api/jars/{id} {remind_day 1–28, share_monthly (ayda toplam;
                              # null: hedef ve son tarihten)};
                              # POST /api/jars/{id}/moves {person_id?, outside?} (kim attı; para hesaplarda değil: harcanabilirden
                              # düşmez); PATCH /api/jar-moves/{id} TL hareketinde {amount?, move_date?, note?, person_id?,
                              # outside?}; GET /api/jars [].shares {month, target, basis aylik|hedef|tamam|null, total, paid,
                              # remind_day, needed (paylar toplamı hedefin gerektirdiğinden azsa ayda
                              # gereken; yoksa null), people [{id, user_id, name, app, me, manual, share, paid, left, over, state
                              # tamam|eksik|bekliyor|paysiz, total, months_full, months_counted}], removed, behind, all_done}
                              # | null, [].outside, moves[].person_id/outside; push türü "kumbara_pay"
    "kumbara_shares_v2",      # kumbara payları v2: altın/döviz kumbarasında da (pay kumbaranın biriminde; shares.unit, scale:
                              # TL/gram/döviz 100, adet 1; POST /api/jars/{id}/buy person_id); tarihli pay: PATCH
                              # /api/jars/{id}/people/{pid} {share, from_month YYYY-MM|all} ve PATCH /api/jars/{id}
                              # {share_monthly, share_from}; eksik devri: people[].due [{month, amount}], due_total; yeni katkı
                              # önce en eski tamamlanacak aya (shares.allocations {hareket id: {person_id, past, month,
                              # month_amount, extra}}); PATCH /api/jars/{id} {carry_over}: fazla sonraki aylara (people[].
                              # carried_in, credit); "Bu ay yok": PUT/DELETE /api/jars/{id}/people/{pid}/exempt/{YYYY-MM}
                              # (state muaf, exempt, exempt_months; PUT Premium "kumbara"); people[].paid_for_month, to_past; shares.paid_month
    "debt_plan_plus",         # borç planının gelişmiş kısmı Premium (plans "borc_plani_plus"; plan, sıra ve tutarlar ücretsiz):
                              # GET /api/debt-plan plus {available, had_plan, lock {commit, payoff} | null}; ücretsizde
                              # commitment/commit_offer null, commit_history [], debts[].payoff {state "locked", title,
                              # badge, text}, erken kapatma satırlarında kapatma tutarı / kurtulunan faiz / close_early yok
                              # (plus_locked: true), totals.payoff_saving null; POST /api/debt-plan/commit 402
                              # plan_required "borc_plani_plus". Kayıtlı plan (commit) silinmez: Premium'la geri gelir
    "sadaka_reminder",        # sadaka hatırlatması (kişiye özel; seçim yoksa kapalı): GET /api/donations/reminder
                              # {available, rhythm haftalik|aylik|kapali, day (4 Cuma), chosen, show (Özet kartı),
                              # show_streak, streak, streak_text, last_date, days_since, in_period, period_key, title, text,
                              # tone ok|nudge|empty, next_reminder, when, rhythms, days, choices [{rhythm, label, hint,
                              # recommended}], ask_after_save + ask_title (sadaka kaydedilince "Her Cuma hatırlatalım
                              # mı?")}; PUT {rhythm?, day?, show_streak?} (409: Zekât ve Sadaka kapalı). Push türü
                              # "sadaka" ("Hayırlı Cumalar"), yol #/diger/zekat/sadaka (sadaka formu)
    "sadaka_ratio",           # GET /api/donations/ratio?months=6|12 {available, months [{month, label, short, sadaka,
                              # income|null, ratio (%)|null, note "gelir kaydı yok"|null, zakat|null (ayrı, oranda yok),
                              # partial}], sentence|null, ratio_available, basis kisi|defter, min_income, note}
    "warranty",               # garanti arşivi (app/warranty.py): GET /api/warranties?q&status → {items [{id, name, brand, model,
                              # store, purchase_date, price, months, extra_months, ends_at, days_left, status aktif|yakinda|bitti,
                              # status_label, left_text, serial_no, note, entry_id, txn_id, files [{id, doc_type, doc_label,
                              # filename, pdf, from_receipt}], can_edit}], counts, quota {limit, used, locked, need}, default_months,
                              # soon_days, max_files, doc_types}; POST /api/warranties (402 plan_required "garanti": ücretsizde 3
                              # ürün), GET/PATCH/DELETE /api/warranties/{id}, POST /api/warranties/{id}/files (file, doc_type
                              # fatura|garanti|diger; foto/PDF, okuma hakkı düşmez), GET /api/warranties/{id}/files/{fid}[?download=1],
                              # DELETE …/files/{fid}; "Garantiye ekle": GET /api/warranties/draft?entry_id|txn_id, kayıtlı fişler
                              # GET /api/warranties/receipts?q; yeni fotoğraf POST /api/receipts/parse → receipt_file. Push "garanti";
                              # ortak defter sayfası "garantiler"
    "store_subscriptions",    # uygulama mağazası abonelikleri (app/store_subs.py): GET /api/subscriptions/apps {items
                              # [aday: id, title (adsız: "Apple aboneliği"), status abonelik|aday|degil|iptal, cycle,
                              # cycle_text "her ay 14'ü", amount, next_date, next_text, first_text, price_up, guesses
                              # ("Olabilir"), charges, manage [{store, url, app_url}], pending (ekstreye düşmemiş)], other,
                              # totals {monthly, yearly, text}, help}; PUT /api/subscriptions/apps/{id} {action adlandir|
                              # abonelik|degil|iptal|aktif|bilmiyorum|sifirla, catalog_id?, name?, cycle?, date?};
                              # GET /api/subscriptions/catalog; POST /api/subscriptions/apps/screenshot (files[], ocr_text[],
                              # store?; 1 görüntü = 1 okuma); POST …/apps/manual; PATCH|DELETE …/apps/sources/{id};
                              # /api/savings subscriptions[].store + logo. Push "abonelik" yenilemeye 3 gün kala, yol
                              # #/diger/tasarruf/abonelik/<id>
    "subscription_detail",    # abonelik ayrıntısı (app/sub_detail.py): GET /api/subscriptions/detail?merchant=|app= →
                              # toplam ödenen (tümü / son 12 ay, dövizli: döviz toplamı + not), ay ay tutar (zam/indirim/
                              # kur), işlemler (uid → mevcut işlem ayrıntısı). Yoksa istemci abonelik satırını açmaz
    "subscription_gaps",      # GET /api/subscriptions/detail?gaps=1: months[] her satırda gap (bool); aylık abonelikte
                              # (döngü sunucuda tahmin: ilk çekimler arası ortanca 20–45 gün) ilk ödeme ile bugün / iptal
                              # günü arasındaki ödemesiz aylar {gap true, amount 0, count 0, change null}; sonda yalnız
                              # verisi olan (kartın son ekstre kesimi / hesabın son kaydı) aylar. Zam/kur rozeti önceki
                              # ödemeli aya göre. Yıllıkta boşluk yok. gaps verilmezse yanıt eskisi gibi
    "card_early_close",       # taksitleri erken kapattım (app/early_close.py): GET /api/cards/summary cards[].early_close
                              # {available, rest, fee_left (faiz gizliyse null), hint, months [{month, label}], default_month,
                              # record {month, label, amount, total, rest, saved, at, text, saved_text} | null, detected
                              # {saved, verified, by_line, text} | null} | null; PUT /api/cards/early-close {card_no, month,
                              # amount?} (düzenle: tekrar PUT), DELETE /api/cards/early-close?card_no= (geri al); yanıt
                              # {card_no, early_close}. GET /api/interest early_close [kart + aynı alanlar] ve tips
                              # "erken_kapat"; /api/installments items[] early_close, card_no, count (kapama satırı);
                              # cards/summary cycle.items[] early_close. Ekstreden tanınan kapama push "faiz" (bir kez)
    "upload_docs",            # POST /api/statements/upload docs=true (yeni istemci): ekstre olmayan fiş/fatura (fotoğraf,
                              # fatura PDF'i — app/mailbox.looks_like_bill, ya da yapay zekâ kart ekstresi bulamadı ve
                              # metinde fatura işareti var) belge okuma yolunda okunur (POST /api/documents/parse ile
                              # aynı; okuma hakkı orada, tek sayım) → {status "document", doc_type bill|receipt, doc,
                              # message}; okunamadıysa status "error". docs yoksa (eski istemci) aynı belgeler Türkçe
                              # nedenle "error"; fotoğraf hiçbir durumda PDF ayrıştırıcıya gitmez
    "payment_gateways",       # yönetici: GET /api/admin/payment-gateways (adaylar, onaylı, reddedilen, sabit liste),
                              # /preview?prefix=, POST approve | reject | forget {prefix}, POST (elle ekle). Gece
                              # taramasında yeni aday: "sistem" push'u (haftada en çok bir), route
                              # #/diger/ayarlar/odeme-araclari
    "incidents",              # sorun kaydı (app/incidents.py): POST /api/incidents/client {events [{code js_hatasi|
                              # ekran_coktu|ag_zaman_asimi|sunucuya_ulasilamadi|eklenti_hatasi, screen?, detail?,
                              # app_version?, platform?, plugin?}] ≤10} → {ok, accepted} (oturumsuz da; saatlik sınır);
                              # yönetici: GET /api/admin/incidents?status&severity&days, GET …/{id}, GET …/users/{uid},
                              # PATCH …/{id} {status yeni|inceleniyor|duzeltildi, note, fixed_version, user_text,
                              # notify_users}. Push "sistem" (yöneticiye, yol #/diger/ayarlar/sorunlar) ve "duzeltme"
                              # (kullanıcıya: "… düzelttik, tekrar deneyebilirsin")
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
