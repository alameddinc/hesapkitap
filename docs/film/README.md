# Tanıtım filmi belgeleri

Film hesaplarında (app/film_seed.py) kamera önünde okutulacak **kurgu** belgeler. Kişiler Ayşe Örnek / Mehmet Örnek;
kurumlar Örnek Bank, Mavi Bank, Örnek Elektrik, MARKET A. Kart numarası maskeli (`5555 **** **** 2222`), IBAN'lar
`TR00 0000 …` (kontrol hanesi geçersiz), VKN `1234567890` (yer tutucu). Her belgede "Kurgu belge – gerçek değildir" yazar.

Yeniden üretmek (tarihler `--today`'e göre; varsayılan 2026-11-10):

    uv run --with-requirements requirements.txt --with reportlab --with matplotlib \
        python scripts/make_film_files.py [--today 2026-11-10]

Çekim günü farklıysa o günle üret (testler varsayılan tarihli dosyaları bekler; yeniden üretilen set commit'lenmez).
Okuma testleri: `tests/test_film_files.py`.

| Dosya | Ne gösterir | Okuyucu |
|---|---|---|
| `fis-market-a.jpg` | Kartla ödenmiş MARKET A fişi; deterjanın 82,95'i silik (6 gibi). Toplam 502,89 yalnız 8 ile tutar → **"6 ile 8 karışmış olabilir → Uygula"**. Son ekstredeki MARKET A satırıyla **eşleşir** | Yapay zekâ (fotoğraf) |
| `fis-market-a-adetli.jpg` | Nakit fiş; "4 AD X 20,50" satırının 82,00'ı silik (62,00 gibi). Adet × birim tanığıyla aynı öneri — telefon metni olsa da | Yapay zekâ (fotoğraf) |
| `ekstre-1-ornek-bank-2026-09.pdf` | Televizyon taksit 1/6, yıllık kart ücreti, tam ödeme | Genel okuyucu (şablon), yapay zekâsız |
| `ekstre-2-ornek-bank-2026-10.pdf` | Taksit 2/6 + koltuk 1/3, iade | Genel okuyucu |
| `ekstre-3-ornek-bank-2026-11.pdf` | Taksit 3/6 + 2/3, **kısmi ödeme → akdi faiz + BSMV + KKDF**, asgari ödeme, son ödeme 13.11 | Genel okuyucu |
| `hesap-dokumu-ornek-bank.pdf` | Maaş (58.000), kira (24.000), kart ödemesi, FAST/EFT, fatura, ATM; kapanış 38.420,50 = film hesabının bakiyesi | Öğrenilen döküm şablonu (bakiye zinciri) |
| `fatura-ornek-elektrik.pdf` | 1.120,40 TL, son ödeme bugün + 6 (film_seed'deki faturanın aynısı) | Canlıda yapay zekâ; yapay zekâsız yerel okuma da tutar |
| `dekont-ornek-bank-eft.pdf` | Kasım kirası EFT'si (24.000); dökümdeki 05.11 satırıyla birleşir | Etiket okuması, yapay zekâsız |

## Film hesaplarıyla uyum ve dikkat

- **Kart …2222**: film_seed'in Örnek Bank …0000 kartının son iki dönemi zaten yüklü; aynı kartın yeni dönemi ancak
  bugünden sonra olurdu. Bu yüzden ekstreler aynı bankanın üçüncü bir kartı. Üçü sırayla yüklenebilir.
- Ekstrelerde banka adı **"Kredi kartı"** görünür: "Örnek Bank" uygulamanın tanıdığı bir banka değil (genel okuyucu
  adı bilmez). Kart …2222 olarak ayrı görünür.
- **Fatura film_seed'de zaten var** (Örnek Elektrik 1.120,40): kamera önünde okutulacaksa önce seed'deki faturayı sil,
  yoksa Ödemeler'de iki kez görünür (faturada mükerrer denetimi yok).
- Dökümü yüklemek "Örnek Bank" hesabının hareketlerini ekler; kapanış bakiyesi seed'dekiyle aynı (38.420,50).

## Karışan rakam önerisi ne zaman çıkar?

Öneri (app/receipt_check.py `digit_check`) yapay zekânın okuduğu kalemlerden hesaplanır; yapay zekâ silik 8'i
**6 okursa** çıkar, 8 okursa fiş zaten doğrudur (öneri gerekmez).

- `fis-market-a.jpg`: öneriyi yalnız fiş toplamı verir. **Telefon uygulaması fotoğrafla birlikte cihazda çıkardığı
  metni (Apple Vision / ML Kit) de gönderir**; o metin aynı satırda 62,95 okursa satır "doğrulanmış" sayılır ve
  öneri **çıkmaz** (Apple Vision bu fotoğrafta 62,95 okuyor). Bu fiş ancak metinsiz yüklemede (web'den dosya) öneri
  gösterir.
- `fis-market-a-adetli.jpg`: öneriyi adet × birim (4 × 20,50 = 82,00) verir; telefon metninden bağımsızdır.
  **Telefonla çekimde bunu kullan.**
- Toplam tutmayınca uygulama fişi bir kez daha güçlü modelle, fiş toplamını söyleyerek okur (`_auto_reread`); güçlü
  model rakamı 8 okursa kalemler doğru gelir ve öneri görünmez. Prova et.
