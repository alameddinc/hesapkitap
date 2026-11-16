# Hata kaydı — finans doğruluğu

Her satır: hata, onu **ne yakaladı**, etkisi, durumu ve onu artık koruyan test. Gerçek kullanıcı verisi
(isim, tutar, kart/IBAN) bu dosyaya yazılmaz; tutarlar yuvarlak/örnektir.

Yakalayan katmanlar:

- **Kullanıcı** — telefonda gördü, bildirdi.
- **Mutabakat** — canlı verinin salt-okunur kopyasında kalem kalem karşılaştırma (gerçek banka bakiyeleriyle).
- **Canlı kopya** — deploy öncesi eski/yeni kod, tüm defterlerin kopyasında yan yana (`scratchpad/cmp`).
- **Bağımsız doğrulama** — kodu yazmayan ajanın bozma denemesi (`tests/test_finans_cekismeli.py`,
  `tests/test_p0_cekismeli.py`, `tests/test_gercek_gun_tekrari.py`).
- **Simülasyon** — 10 aylık zaman hızlandırmalı, 6 persona, her gün gerçek defterle karşılaştırma
  (`tests/simulasyon/`).
- **Çılgın testler** — rastgele/kötü niyetli işlem dizileri (`tests/cilgin/`).
- **Denetim** — günlük `app/finance_audit.py` (canlıda her gece).

## 16.11.2026

| # | Hata | Yakalayan | Etki | Durum | Koruyan test |
|---|---|---|---|---|---|
| 1 | Kendi hesaplar arası dekont fatura sanıldı / yönsüz kaldı; iki dekont tek aktarım olmadı | Kullanıcı | Aktarım hiç kaydedilmedi, bakiyeler yanlış | Canlıda (0846da3) | test_hesaplar_arasi_transfer |
| 2 | Çapa günü eklenen aktarım bakiyeye girmedi | Mutabakat | Aktarım tutarı kadar | Canlıda | test_hesaplar_arasi_transfer |
| 3 | Erken yatırılan kira düzenli ödemeyle eşleşmedi, iki kez sayıldı | Kullanıcı | Bir kira tutarı | Canlıda | test_recurring_transfer_early |
| 4 | Ekstreye girmiş elle kart harcaması tahminde ikinci kez sayıldı | Mutabakat (kod) | Harcama tutarı | Canlıda | test_open_cycle_carried |
| 5 | Ödemeler "ödendi", Kartlarım "Ödenecek" (ileri tarihli ödeme) | Mutabakat | Gösterim | Canlıda | test_statement_paid_rule |
| 6 | Kart "Ödendi" hesap sormadı → para hiçbir hesaptan düşmedi | Mutabakat | ~100 bin şişik nakit | Canlıda (244f5da) | test_statement_pay_unknown |
| 7 | "Ödemelerden sonra" kalemleri farklı ufuklarda (45 gün / sınırsız / dönem) | Mutabakat | Dönem dönümünde sıçrama | Canlıda | test_spendable_horizon |
| 8 | Ödenmiş ama ekstresi yüklenmemiş döngü düşülmeye devam etti | Mutabakat | Tahmin tutarı | Canlıda | test_spendable_horizon |
| 9 | Kart tahmini yeni taksitleri saymıyordu, sistematik düşük | Mutabakat + backtest | ~%10–15 düşük | Canlıda (medyan + ilk taksit) | card_backtest |
| 10 | Maaş hafta sonu/bayram kayınca yanlış döneme, 3 kez sayıldı | Bağımsız doğrulama (kayma senaryoları) | Bir maaş tutarı | Canlıda | test_kayma_senaryolari |
| 11 | Son ödeme hafta sonu/bayramda "gecikti" + faiz | Bağımsız doğrulama | Yanlış uyarı, faiz düşümü | Canlıda (workdays.py) | test_kayma_senaryolari |
| 12 | Ay dönümü salınımı (17→19→17), ay sonu kesimi ikilendi | Bağımsız doğrulama | Dönemler yeniden hesaplandı | Canlıda | test_kayma_senaryolari |
| 13 | Belirsiz ödeme kuralı başka bankanın dökümünü "içinde" saydı | Bağımsız doğrulama (gün tekrarı) | ~50–100 bin | Canlıda (aday hesap kuralı) | test_gercek_gun_tekrari |
| 14 | Döküm kart ödemesi + hesapsız Ödendi çift düşüm | Bağımsız doğrulama | Ödeme tutarı | Canlıda | test_finans_cekismeli |
| 15 | Ortak defter üyesi sahipten farklı rakam | Bağımsız doğrulama | Gizli hesap tutarı | Canlıda | test_finans_cekismeli |
| 16 | Aynı saniye: bakiye güncellemesi + ödeme | Bağımsız doğrulama | Ödeme tutarı | Canlıda (anchor id) | test_finans_cekismeli |
| 17 | Döküm çapası belirsiz ödemede hiç sayılmadı → tüm eski ödemeler yeniden düşüldü | **Canlı kopya** (deploy durduruldu) | ~280 bin, rakam eksiye düşerdi | Hiç canlıya çıkmadı; muhafazakâr kural | test_finans_cekismeli |
| 18 | Önce bakiye, sonra aynı gün ödeme → çift düşüm | Canlı kopya simülasyonu | Ödeme tutarı | Canlıda (Bakiyede var mı? sorusu) | test_anchor_conflict |
| 19 | Kendi adına giden FAST (alıcısı kesik) harcama sayıldı | Kullanıcı + mutabakat | Aktarım tutarı harcamada | Canlıda (soru + aktarıma çevir) | test_finance_invariants |
| 20 | Halkbank döküm altbilgisi açıklamaya taştı, "TARAFINDAN" kesildi | Kullanıcı | Yanlış açıklama/tanıma | Canlıda | test_doc_footer |
| 21 | Kural değişikliği ay dönümünü kendiliğinden 17→16 yaptı | Kullanıcı | Tüm kayıtların dönemi | Canlıda (histerezis + Geri al) | test_cycle_hysteresis |
| 22 | Borç "Ödendi" hesap/not sormadı | Kullanıcı | Bakiye riski | Canlıda | test_debt_pay_note_anchor |
| 23 | Çapa günü borç ödemesi bakiyeye hiç yansımadı | Geliştirici (borç sayfası işi) | Ödeme tutarı | Canlıda | test_debt_pay_note_anchor |
| 24 | İki maaşta ikinci maaş kayboldu | Araştırma (kod analizi) | Bir maaş tutarı | Canlıda (01c9983) | test_p0_maas_gun |
| 25 | `date.today()` kaçakları (geçmiş tarihli hesap, maaş çift) | Araştırma + geliştirici | Geçmiş günler yanlış | Canlıda | test_p0_maas_gun |
| 26 | Ayın 29–31'i 28'e yuvarlandı | Araştırma | Tarih | Canlıda | test_p0_maas_gun |
| 27 | Maaştan önce vadeli kart ana sayıdan düşülmedi | Araştırma | Ekstre tutarı (iyimser) | Canlıda | test_p0_maas_gun |
| 28 | Adsız zamlı ikinci maaş birincili "yattı" saydı (gün sınırı yok) | Bağımsız doğrulama | ~60 bin | Canlıda | test_p0_cekismeli |
| 29 | Silinen maaş kalemi, maaş farkı, ortak defter maaş kararı, geç maaş tutarsızlığı | Bağımsız doğrulama | Maaş tutarı | Canlıda | test_p0_cekismeli |
| 30 | "Ay sonunda kalacak" maaşı ekledi, maaşın ödeyeceklerini düşmedi | **Kullanıcı** (canlı kopya tablosunda vardı, gözden kaçtı) | ~145 bin iyimser | Canlıda düzeltildi; tanım kullanıcıyla yeniden kararlaştırıldı (#33) | test_ufuk_maas |
| 31 | İki maaş + döküm: eşin maaşı birincinin kaydına yazıldı | **Simülasyon** (P2) | Bir maaş + bakiye şişmesi | Düzeltiliyor | tests/simulasyon (S1) |
| 32 | Dekonttan kira düzenli kaleme bağlanmadı | **Simülasyon** (P2, P3) | Her ay kira tutarı | Düzeltiliyor | tests/simulasyon (S2) |
| 33 | Taksitli borçta funded kalan borcun tamamını düştü | **Simülasyon** (P1) | Kalan taksitler (kötümser) | Düzeltiliyor | tests/simulasyon (S9) |
| 34 | Aynı döküm eşzamanlı iki kez → satırlar ikilendi | **Çılgın testler** | Rapor ×2 | Düzeltiliyor | tests/cilgin (C1) |
| 35 | Yeni dekont dünkü benzer aktarıma bağlandı | **Simülasyon** | İki hesap ±tutar | Düzeltiliyor | tests/simulasyon (S3) |
| 36 | Fatura "Ödendi" hesap sormadı | **Simülasyon** | Fatura tutarı şişik nakit | Düzeltiliyor | tests/simulasyon (S6) |
| 37 | Kesimden sonraki elle kart harcaması kapanmış döngüye yazıldı | **Simülasyon** (5 persona) | 1–4 gün fazla düşüm | Düzeltiliyor | tests/simulasyon (S4) |
| 38 | Çapa günü açılan borç bakiyeye yansımadı | **Simülasyon** | Borç tutarı | Düzeltiliyor | tests/simulasyon (S5) |
| 39 | API'de recurring_id verilince erken maaş yanlış dönem | **Simülasyon** | Maaş dönemi | Düzeltiliyor | tests/simulasyon (S7) |
| 40 | Dekont adında Türkçe büyük/küçük harf | **Simülasyon** | Kozmetik | Düzeltiliyor | tests/simulasyon (S8) |
| 41 | Büyük rakam "14 Oca'ya kadar" (maaş + fonlananlar) kullanıcı için anlamsız; nakit tahmini aktarımlarla şişik | **Kullanıcı** | Rakam ~−36 bin, beklenen ~+30 bin | Düzeltiliyor: "Maaşa kadar kalacak" | — |

## Mutasyon testi (testleri test eden)

İlk ölçüm: 43 bilinçli hatadan 22'si yakalandı (%51). Yakalanmayan anlamlı boşluklar: `debt_after_anchor`
in_anchor tersine, maaşın yattığı gün / bekleme süresinin son günü / ufuk günündeki taksit sınırları,
altın işareti. Bu boşlukları kapatan testler yazılıyor; oran her ölçümde buraya eklenir.

## Kural

Finans değişikliği şu sırayla geçmeden canlıya çıkmaz: birim + değişmez testleri → bağımsız doğrulama →
simülasyon (6 persona, 0 bilinmeyen uyuşmazlık) + çılgın uzun tur (`scripts/deploy_gate.sh`) → canlı kopyada
önce/sonra → deploy. Bu dosya her bulunan hatada güncellenir.
