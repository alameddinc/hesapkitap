<p align="center">
  <img src="docs/logo.png" width="96" alt="HesapKitap logosu">
</p>

<h1 align="center">HesapKitap</h1>

<p align="center">
  Kredi kartı ekstrelerini yükle, paranın nereye gittiğini gör, önümüzdeki ayları planla.<br>
  <b>Kendi sunucunda çalışır · verin sende kalır · mobil uyumlu</b>
</p>

<p align="center">
  <a href="https://github.com/alameddinc/hesapkitap/actions/workflows/ci.yml"><img src="https://github.com/alameddinc/hesapkitap/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/lisans-MIT-blue" alt="MIT"></a>
</p>

<p align="center">
  <img src="docs/screenshots/ozet.png" width="720" alt="Özet ekranı">
</p>

## Neler yapar

- **Ekstre okuma** — Banka PDF'ini sürükle; işlemler, taksitler, dövizli harcamalar, faiz/BSMV/KKDF ayrıştırılır.
  Her ekstre **mutabakat kontrolünden** geçer: okunan işlemlerin toplamı ekstre özetiyle kuruşu kuruşuna tutmalı.
  - Hazır parser: **VakıfBank**, **Kuveyt Türk**
  - Diğer bankalar: Gemini anahtarı tanımlıysa **yapay zekâ ile okunur** (aynı mutabakat kontrolüyle)
- **Otomatik kategori** — Kural tabanlı (regex), düzelttikçe öğrenir. Aynı işyerinin farklı bankalardaki yazılışları birleşir.
- **Ödemeler** — Kart ekstreleri ve faturalar tek listede, son ödeme tarihine göre. "Ödedim" (tam / asgari / kısmi).
  Yeni ekstre gelince önceki ekstre otomatik "ödendi" işaretlenir.
- **Fiş ve fatura** — Telefondan fotoğraf çek ya da e-fatura PDF'i yükle; fiş mi fatura mı kendisi anlar,
  tutar / tarih / son ödeme otomatik dolar. Kredi kartıyla ödenen fiş, ekstre gelince kart işlemiyle eşleşir (iki kez sayılmaz).
- **Gelir & sabit kalemler** — Maaş, kira, aidat… her ay planlamaya girer; gerçekleşince onaylanır.
- **Planlama (3–12 ay)** — Kalan taksitler, abonelikler, faturalar, sabit giderler; **kesin** ile **tahmini** harcama ayrı gösterilir.
- **Tasarruf** — Abonelikleri iptal / paket düşürme olarak işaretle; iptal edilen yine çekilirse uyarı. Kısma hedefleri ay ay "tuttu mu".
- **Bütçe** — Kategori limitleri, %80 ve %100 uyarıları.
- **Tablo** — Excel gibi filtreler, kategori × ay pivotu, Excel'e aktarma.
- **Uyarılar** — Faiz ödedin mi, vade farkı ne kadar, hangi taksitler bitiyor, limit doluluğu, dövizli harcama…
- **Telegram** — Son ödeme hatırlatmaları, aylık özet, günlük **şifreli** yedek.
- **PWA** — Telefonda ana ekrana ekle, uygulama gibi açılır.

<p align="center">
  <img src="docs/screenshots/mobil-ozet.png" width="240" alt="Mobil özet">
  <img src="docs/screenshots/mobil-ekle.png" width="240" alt="Fiş / fatura ekleme">
</p>

## Hızlı kurulum

Gereken tek şey [Docker](https://docs.docker.com/get-docker/).

```bash
git clone https://github.com/alameddinc/hesapkitap.git
cd hesapkitap
docker compose up -d
```

→ **http://localhost:8080** — ilk açılışta şifreni belirle, bitti.

Gemini (fiş/fatura/ekstre okuma) ve Telegram (hatırlatma) anahtarlarını **Ayarlar → Anahtarlar** sayfasından girersin;
`.env` dosyasıyla uğraşman gerekmez. İstersen önce demo verisiyle bakabilirsin:

```bash
docker compose exec hesapkitap python -m app.demo
```

### Sunucuya kurulum

Aynı `docker compose up -d`; önüne HTTPS yapan bir reverse proxy (Caddy, Nginx, Traefik, Cloudflare Tunnel) koy.

- Uygulamayı internete açıp kurulumu yapmadan bırakma: yerel ağ dışından ilk kurulumda container log'undaki
  tek seferlik kod istenir (`docker compose logs | grep kurulum`).
- Şifre PBKDF2 ile hash'lenir, oturum çerezi HttpOnly + (HTTPS'te) Secure, hatalı girişlerde 15 dakika kilit.
- Sunucu yöneticileri ayarları `.env` ile de verebilir (bkz. [`.env.example`](.env.example)); `.env` uygulama içindekinden önceliklidir.
- Veri: `./data` (SQLite + yüklenen dosyalar + gece yedekleri). Yedeklemek için bu klasörü kopyalaman yeterli.

## Ekran görüntüleri

| Planlama | Tablo (pivot) |
|---|---|
| ![Planlama](docs/screenshots/planlama.png) | ![Tablo](docs/screenshots/tablo.png) |

| Tasarruf |
|---|
| ![Tasarruf](docs/screenshots/tasarruf.png) |

## Gizlilik

- Tüm veri kendi sunucunda, tek bir SQLite dosyasında durur. Dışarıya hiçbir şey gönderilmez — **sadece** sen Gemini
  anahtarı girersen fiş/fatura/tanınmayan ekstre okumak için o belge Google'a gider. Google AI Studio'da
  faturalandırmayı açarsan (ücretli katman) içerik model eğitiminde kullanılmaz; ücretsiz katmanda kullanılabilir.
- Kart numaraları sadece maskeli (ilk 4 + son 4) saklanır.
- Telegram'a giden yedekler AES ile şifrelidir; açmak için: `python -m app.backup decrypt dosya.db.enc`

## Geliştirme

```bash
cp .env.example .env            # isteğe bağlı
docker compose -f docker-compose.dev.yml up --build
```

→ http://localhost:5173 (Vite, sıcak yenileme) · API http://localhost:8000

```bash
docker compose -f docker-compose.dev.yml run --rm api pytest -q
```

| Yol | Ne |
|---|---|
| `app/parsers/` | Banka parser'ları + yapay zekâ yedeği |
| `app/categorize.py` | Varsayılan kategori kuralları |
| `app/finance.py` | Aylık özet, planlama, uyarılar, tasarruf |
| `app/api/main.py` | FastAPI uçları |
| `web/` | React + Vite + Tailwind arayüzü |
| `tests/fixtures/` | **Uydurma** ekstre metinleri (gerçek veri yok) |

**Yeni banka eklemek** en değerli katkı — rehber: [CONTRIBUTING.md](CONTRIBUTING.md)

## English

HesapKitap is a self-hosted personal finance app for Turkish credit card statements: upload bank PDFs (exact parsers
for VakıfBank and Kuveyt Türk, Gemini fallback for any other bank, all verified by a reconciliation check), auto-categorize,
track payments and bills, snap receipts, plan the next months, manage subscriptions and budgets, get Telegram reminders.
Quick start: `docker compose up -d` → http://localhost:8080. MIT licensed.

## Lisans

[MIT](LICENSE)
