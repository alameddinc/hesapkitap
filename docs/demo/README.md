# Demo dosyaları

HesapKitap'ı denemek için **uydurma** belgeler (Ayşe Yılmaz). Hiçbiri gerçek değildir;
hepsinde "ÖRNEK – GERÇEK DEĞİLDİR" yazar. Yeniden üretmek için: `python scripts/make_demo_files.py`

| Dosya | Ne gösterir |
|---|---|
| `1-vakifbank-ekstre.pdf` | Hazır okuyucu, ek kart, dövizli abonelikler (OpenAI, Anthropic, Spotify…), taksit |
| `2-vakifbank-ek-kartli-ekstre.pdf` | Faiz + BSMV + KKDF, taksitli iadeler, ek kart sahibi |
| `3-kuveytturk-ekstre.pdf` | Taksitlendirme ve vade farkı (planlamada gelecek aylara yansır) |
| `4-ornek-bank-yapay-zeka.pdf` | Hazır okuyucusu olmayan banka → **yapay zekâ ile okuma** (Gemini anahtarı gerekir) |
| `fis-market.jpg` | Kredi kartıyla ödenmiş fiş — 1 no'lu ekstredeki işlemle **eşleşir** |
| `fis-kahve.jpg` | Nakit fiş |
| `fis-eczane.jpg` | Kredi kartı, ekstre henüz gelmedi → "Ekstre bekleniyor" |
| `fatura-elektrik.pdf` | Fatura: son ödeme tarihiyle Ödemeler listesine girer |

## 5 dakikalık demo akışı

1. `docker compose up -d` → http://localhost:8080 → şifre belirle.
2. **Ayarlar → Anahtarlar**: Gemini anahtarını gir (fiş/fatura/yapay zekâ okuması için).
3. **+ → Fiş / fatura çek → Dosyadan yükle**: `fis-market.jpg` → form otomatik dolar (ödeme: kredi kartı) → Kaydet.
   İşlemler'de "Ekstre bekleniyor" etiketi görünür.
4. **Ekstreler**: `1-…` `2-…` `3-…` PDF'lerini sürükle → hepsi "mutabakat tamam".
   Market fişi kart işlemiyle **eşleşti** ("1 elle girilen gider eşleşti"); iki kez sayılmaz.
   Ay dönümü kesim günlerine göre otomatik ayarlanır.
5. `4-ornek-bank-yapay-zeka.pdf` → "yapay zekâ ile okundu", yine mutabakat kontrolünden geçer.
6. `fatura-elektrik.pdf` → Ödemeler'de son ödeme tarihiyle görünür → **Ödedim**.
7. **Özet**: faiz uyarısı, vade farkı maliyeti, biten taksitler, kategoriler → bir çubuğa tıkla, detay açılır.
8. **Planlama**: Sabit kalem → Maaş (brüt seç, ör. 100.000) → 12 ayın neti; kesin ve tahmini gider ayrı.
9. **Tasarruf**: Anthropic → Paket değiştirdim; Scribd → İptal ettim → planlama anında düşer.
10. **Tablo**: kategori × ay pivotu, Excel'e aktar.

Gemini anahtarı yoksa 1–3 numaralı ekstreler yine çalışır (hazır okuyucu); fiş/fatura formu elle doldurulur.
