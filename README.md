<p align="center">
  <img src="docs/logo.png" width="88" alt="HesapKitap logosu">
</p>

<h1 align="center">HesapKitap</h1>

<p align="center">
  <b>Ekstreni bırak, gerisini HesapKitap yapsın — verin sende kalır.</b><br>
  Ekstre ve döküm okuma, fişi çekince kendiliğinden kayıt, bütçe ve tahmin, fiyat takibi, kumbara, faiz alarmı,<br>
  ortak defter — zekât ve sadaka da dahil.<br>
  Türkiye'deki bankalar ve alışkanlıklar için, kendi sunucunda çalışan kişisel finans uygulaması.
</p>

<p align="center">
  <a href="https://github.com/alameddinc/hesapkitap/actions/workflows/ci.yml"><img src="https://github.com/alameddinc/hesapkitap/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/alameddinc/hesapkitap/pkgs/container/hesapkitap"><img src="https://img.shields.io/badge/docker-ghcr.io-2496ED?logo=docker&logoColor=white" alt="Docker imajı"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/lisans-PolyForm%20Noncommercial-blue" alt="PolyForm Noncommercial"></a>
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black" alt="React 19">
  <img src="https://img.shields.io/badge/SQLite-kullanıcı%20başına%20dosya-003B57?logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/PWA-iOS%20%C2%B7%20Android-5A0FC8?logo=pwa&logoColor=white" alt="PWA">
</p>

<p align="center">
  🧪 <b>Beta</b> — hata görürsen ya da bankan okunmazsa <a href="https://github.com/alameddinc/hesapkitap/issues/new/choose">issue aç</a>, kısa sürede düzeltiyorum.
</p>

<p align="center">
  <b>🇹🇷 Türkçe</b> · <a href="README.en.md">🇬🇧 English</a>
</p>

<p align="center">
  <a href="https://hesapkitap.alameddinc.com"><b>🧪 Canlı demo</b></a> — kurmadan dene: uydurma verilerle, sana özel 1 saatlik kopya
</p>

<p align="center">
  <a href="#-hızlı-kurulum"><b>Kurulum</b></a> ·
  <a href="docs/KURULUM.md"><b>Tek tıkla / evde / VPS</b></a> ·
  <a href="#-ekran-görüntüleri"><b>Ekran görüntüleri</b></a> ·
  <a href="#-tüm-özellikler"><b>Tüm özellikler</b></a> ·
  <a href="#-gizlilik"><b>Gizlilik</b></a>
</p>

<p align="center">
  <img src="docs/screenshots/hero.png" width="900" alt="HesapKitap masaüstü ve telefon">
</p>

## ✨ Öne çıkanlar

<table>
<tr>
<td width="33%" valign="top">

### 📄 Ekstreni bırak
Kart ekstresi, hesap dökümü, fiş, fatura — **kendisi anlar**. Mutabakat ve bakiye zinciriyle kuruşu kuruşuna doğrular.
Yapay zekâ anahtarı olmadan da çalışır: fişler sunucuda **yerel OCR** ile okunur.

</td>
<td width="33%" valign="top">

### 📊 Paranı gör
Gelir, gider, **gerçek net durum**; geçen dönemlerle karşılaştırma, en yoğun günler, bütçe uyarıları.

</td>
<td width="33%" valign="top">

### 🗓️ Önünü gör
Brütten nete maaş, taksitler, sabit giderler — **3–12 ay plan**. "Alırsam ne olur?" simülatörü.

</td>
</tr>
<tr>
<td valign="top">

### 💰 Kumbara ve altın
Hedef için para ayır, harcanabilir bakiyenden düşsün. Altın/döviz anlık fiyatla; **ne zaman, kaça aldığın**.

</td>
<td valign="top">

### 🌙 Zekât ve sadaka
Nisap, **kameri yıl**, aile üyeleri ayrı, geçmiş yıllar ve kaza zekâtı. Sadaka, fitre, kurban — tutarlar gizli.

</td>
<td valign="top">

### 🚨 Faiz alarmı
Faiz ya da vade farkı ödediysen **nedenini** söyler: "son ödeme 25.08, ödeme 26.08 — 1 gün geç".

</td>
</tr>
<tr>
<td valign="top">

### 📲 Telefondan paylaş
Banka uygulamasından PDF'i, galeriden fişi **Paylaş** ile gönder. iPhone kısayolu tek dokunuş, Android doğrudan.

</td>
<td valign="top">

### 👨‍👩‍👧 Aile ve arkadaşlar
Davetle hesap aç; herkesin verisi **ayrı dosyada**, yönetici kimsenin harcamasını göremez.

</td>
<td valign="top">

### 🔒 Verin sende
Kendi sunucunda, tek tıkla kurulum ya da evdeki cihazda. Kodu açık, abonelik ve reklam yok.

</td>
</tr>
</table>

## 📸 Ekran görüntüleri

| Özet | İstatistik |
|---|---|
| ![Özet](docs/screenshots/ozet.png) | ![İstatistik](docs/screenshots/istatistik.png) |
| **Hesaplar, altın, kumbara** | **Zekât** |
| ![Hesaplar](docs/screenshots/hesaplar.png) | ![Zekât](docs/screenshots/zekat.png) |
| **Planlama** | **Tablo (pivot)** |
| ![Planlama](docs/screenshots/planlama.png) | ![Tablo](docs/screenshots/tablo.png) |

<p align="center">
  <img src="docs/screenshots/mobil-ozet.png" width="200" alt="Mobil özet">
  <img src="docs/screenshots/mobil-hesaplar.png" width="200" alt="Mobil hesaplar">
  <img src="docs/screenshots/mobil-zekat.png" width="200" alt="Mobil zekât">
  <img src="docs/screenshots/mobil-ekle.png" width="200" alt="Fiş / fatura ekleme">
</p>

<sub>Tüm görüntüler uydurma demo verisiyle alındı (<code>python -m app.demo</code>).</sub>

## 📋 Tüm özellikler

Ayrıntılar için başlığa dokun.

<details>
<summary><b>Belgeleri içeri almak</b></summary>

- **Tek yükleme alanı** — Kart ekstresi mi, hesap dökümü mü, fiş mi, fatura mı kendisi anlar. PDF, fotoğraf, Excel/CSV.
- **Kart ekstreleri** — İşlemler, taksitler (kaçıncı taksit, kalan), dövizli harcamalar, faiz/BSMV/KKDF, vade farkı ayrıştırılır.
  Her ekstre **mutabakat kontrolünden** geçer: okunan işlemlerin toplamı ekstre özetiyle kuruşu kuruşuna tutmalı.
  - Hazır okuyucu: **VakıfBank**, **Kuveyt Türk**
  - Diğer bankalar: **genel okuyucu**, yapay zekâsız. Etiketlerden ("Hesap Kesim Tarihi", "Dönem Borcu"…) ve tarihli
    satırlardan okur; sonuç **ekstre denklemiyle** (önceki borç − ödemeler + harcamalar = dönem borcu) doğrulanırsa o bankanın
    kuralı öğrenilir ve toplulukla paylaşılır. Tutmazsa Gemini anahtarı varsa **yapay zekâyla okunur**.
  - Bankan okunmuyorsa: `python -m app.card_reader ekstre.pdf` nerede takıldığını yazar (kişisel veri yazmaz);
    `--anonim` yapıyı koruyup kişisel veriyi maskeler. Çıktıyı ["Bankam okunmuyor"](https://github.com/alameddinc/hesapkitap/issues/new/choose) ile paylaş.
- **Hesap dökümleri (vadesiz)** — Her satır **bakiye zinciriyle** doğrulanır (önceki bakiye + tutar = bakiye).
  VakıfBank "Hesap Hareketleri" yapay zekâsız, kuralla okunur (yıllarca hareket, ücretsiz). Diğer bankalarda **şablon**
  denenir: kayıtlı ya da topluluktan gelen bir kural, yoksa bakiye zincirini tutturan kural **kendiliğinden öğrenilir**
  (Halkbank dahil); hiçbiri tutmazsa yapay zekâ. Banka, IBAN'daki banka kodundan tanınır.
  Maaş sabit kaleme bağlanır, kart ödemesi ekstreyi "ödendi" yapar, **kendi hesapların arası aktarım** gelir/gider sayılmaz,
  aynı hareket iki kez eklenmez, eski döküm güncel bakiyeyi ezmez.
- **Yanlış mı yükledin? Geri al** — Her yükleme (ekstre, döküm, dekont, telefondan paylaşılan) tek tıkla geri alınır:
  eklediği her şey silinir, değiştirdiği her şey (bakiye, eşleşmeler, "ödendi" işaretleri) eski haline döner; sonradan
  yaptığın başka işler bozulmaz. Belgenin ne olduğundan emin olamazsa tahmin edip bakiyeni bozmak yerine **sorar**.
- **Fiş ve fatura** — Fotoğraf çek (uzun fişte birkaç kare), tutar, tarih, işyeri, şube, ürün satırları, ödeme şekli,
  **taksit sayısı** ve yemek kartı markası okunur. Kredi kartıyla ödenen fiş ekstre gelince kart işlemiyle eşleşir; iki kez sayılmaz.
  Fişteki marka ile ekstredeki resmi unvan (ör. Espressolab ↔ NEW TASTE GIDA) öğrenilir.
  **Anahtarsız:** Gemini anahtarı yoksa (ya da kota dolduysa) fiş ve faturalar sunucuda Tesseract (Türkçe) ile okunur —
  işyeri, tarih, toplam, ödeme şekli, taksit; belge dışarı çıkmaz.
- **E-posta otomasyonu** — Ayrı bir posta kutusuna yönlendirilen banka mailleri (e-ekstre, işlem bildirimi, hesap özeti) 10 dakikada
  bir içe aktarılır. Sadece banka alan adından gelen ve DKIM imzası doğrulanan mailler işlenir.
- **Telegram'dan** — Bota gönderilen PDF içe aktarılır (sadece bağlı sohbetten).
- **Şifreli PDF'ler** — Bankaların şifreli ekstreleri; şifre bir kez sorulur, hatırlanır.

</details>

<details>
<summary><b>Harcama, gelir ve plan</b></summary>

- **Özet** — Dönem geliri/gideri/neti, "Dikkat etmen gerekenler", net durum, bütçeler, kategoriler, en çok harcanan yerler, ödemeler.
- **Ay dönümü** — Dönemler kart kesim tarihine hizalanır (ör. 20 Ekim – 19 Kasım "Kasım dönemi").
- **Otomatik kategori** — Kural tabanlı, düzelttikçe öğrenir; aynı işyerinin farklı bankalardaki yazılışları birleşir.
- **Bölme ve ortak harcama** — Bir harcamayı kategorilere böl ya da "300'ü Ali'nin payı" de: o kısım gider değil alacak olur.
  Fişte belirli ürünler (ör. sigara) kural ile ayrı kategoriye ayrılır; öneri formda onaylanır.
- **Taksitli alışveriş** — Elle/fişten girilen kart harcamasında taksit sayısı; her taksit kendi ayına gider yazılır,
  ekstre gelince ilk taksitle eşleşir.
- **Maaş** — **Brütten nete** (vergi dilimleri, asgari ücret istisnası, SGK tavanı) ay ay net. Zam / iş değişikliği kayıtları,
  vergi matrahının sıfırlanması, **peşin ödenen maaşta** (ör. kamu) zam farkının ilk zamlı maaşa eklenmesi.
  Dökümü olmayan geçmiş aylarda maaş **tahmini** sayılır ve işaretlenir; döküm gelince gerçeğiyle değişir.
- **Sabit kalemler** — Kira, aidat, abonelik… her ay plana girer, gerçekleşince gerçek tutarla onaylanır.
- **Planlama (3–12 ay)** — Kalan taksitler, abonelikler, faturalar, sabit giderler; **kesin** ve **tahmini** ayrı.
  **"Alırsam ne olur?"** simülatörü: taksit ve vade farkı maliyeti, önümüzdeki aylara etkisi.
- **Ödemeler** — Kart ekstreleri ve faturalar son ödeme tarihine göre; "Ödedim" (tam / asgari / kısmi), yeni ekstre gelince
  önceki kendiliğinden "ödendi". Eski (bankanın arşivinden önceki) dönemlerde ekstre yoksa karta yapılan ödeme o ayın
  tahmini kart harcaması sayılır.
- **İstatistik** — Geçen dönemlere göre gün gün / birikimli / haftanın günü / haftalar karşılaştırması, en yoğun günler
  (güne dokun, o günün harcamaları açılır), aylara göre gelir/gider, kart bazında, faiz geçmişi.
- **Tasarruf** — Abonelikleri iptal / paket düşürme olarak işaretle (iptal edilen yine çekilirse uyarı), kısma hedefleri ay ay "tuttu mu".
- **Bütçe** — Kategori limitleri, %80 ve %100 uyarıları.
- **Fiyat takibi** — Fişlerdeki ürünler ürün / mağaza / marka bazında; "bunu en ucuz nereden aldım", fiyat değişimi.
- **Tablo** — Excel gibi filtreler, kategori × ay pivotu, Excel'e aktarma.
- **Uyarılar** — Faiz, vade farkı, biten taksitler, limit doluluğu, dövizli harcama, eksik ekstre, maaşı görünmeyen aylar…

</details>

<details>
<summary><b>Varlıklar, kumbara, borç/alacak</b></summary>

- **Hesaplar ve net durum** — Banka, nakit, yemek/hediye kartları, kredi kartı borçları, altın/döviz, borç/alacak → gerçek net durum.
  Bakiyeler dökümden kendiliğinden güncellenir ya da elle düzeltilir.
- **Altın ve döviz** — Çeyrek, gram, 22 ayar bilezik, Cumhuriyet, dolar, euro… anlık fiyatla değer; alış tarihi ve bedeli
  girilirse kazanç (TL ve %). Kimin olduğu (aile üyeleri) ayrı tutulur.
  - **"Ne zaman aldım?"** — Para çekimleri (ATM, gişe), giden havale/EFT ve kuyumcu harcamaları listelenir; seçince tarih ve tutar dolar.
  - **Miktarı bilinmiyorsa** — Ödenen tutar ve o günkü altın fiyatından **ihtiyatlı (fazla)** tahmin; "tartıp düzelt" işaretiyle.
- **Kumbara** — Bir hedef için para ayır (elle ya da her ay otomatik); para hesabında kalır ama **harcanabilir bakiyeden düşer**.
  Hedef ve son tarih varsa "ayda ne kadar" ve "bu hızla ne zaman ulaşırsın".
- **Yemek / hediye kartları** — Bakiye, aylık otomatik yükleme, son kullanma hatırlatması.
- **Borç / alacak defteri** — TL, döviz, altın; kısmi ödemeler, vadesinde hatırlatma, havale görülünce kendiliğinden kapanma,
  "vazgeçtim". **Transferler**: kime ne gitti, kişi kişi.

</details>

<details>
<summary><b>Zekât ve sadaka</b></summary>

- **Zekât** — Diyanet (Hanefî) esas, Şâfiî seçeneği. Kişi bazında (aile üyeleri ayrı), **nisap** 80,18 gr altın,
  **kameri yıl** (354 gün) ve hicri tarih, %2,5.
  - Banka hesapları, alacaklar, altın/döviz otomatik; Hanefî'de vadesi bu yıl dolan borçlar ve kart ekstreleri düşülür.
  - Ziynet (takılan altın) ayrımı; yıl içinde bozdurup yeniden almak yılı bozmaz.
  - Erken / parça parça ödeme, yıl dolmadan 7 gün önce ve dolduğu gün Telegram hatırlatması.
  - **Geçmiş yıllar ve kaza zekâtı** — Geçmiş tarihlerdeki banka bakiyesi ve o günkü altın fiyatıyla her yıl yeniden hesaplanır;
    ödenmemiş görünen yıllar işaretlenir, Net durum'da borç olarak düşülür.
  - Kurallar sayfada sade dille; bilgilendirme amaçlıdır.
- **Sadaka ve bağış** — Sadaka, fitre, fidye, kurban, adak, kefaret, vakıf bağışı. Bilinen kurumlara (Diyanet Vakfı, Kızılay,
  Deniz Feneri, İHH, LÖSEV, Darüşşafaka, AFAD…) giden ödemeler yakalanıp "sadaka mı, zekât mı?" diye sorulur; herhangi bir
  harcama "zekât" olarak işaretlenirse o yılın zekât ödemesine yazılır. Tutarlar varsayılan gizli.

</details>

<details>
<summary><b>Faiz alarmı</b></summary>

- Ekstrede faiz, vade farkı ya da faize bağlı vergi varsa **nedeniyle birlikte** uyarır: önceki ekstre **kaç gün geç** ödendi,
  eksik mi ödendi, hangi taksitli alışverişe vade farkı eklendi. Önlem: otomatik ödeme talimatı.
- Hesaba faiz işlerse "arındırılacak" uyarısı. İstatistik'te dönem dönem faiz geçmişi.

</details>

<details>
<summary><b>Telefonda</b></summary>

- **Uygulama gibi** — "Uygulamayı yükle" düğmesi (Android/masaüstü Chrome, Edge); iPhone'da Ana Ekrana Ekle.
  Yeni sürüm öne gelince kendiliğinden yüklenir. Açılışta tutarlar gizli (göz ikonuyla açılır).
- **Paylaş ile gönder** — Banka uygulamasındaki ekstre PDF'ini ya da galerideki fiş fotoğrafını Paylaş menüsünden gönder:
  - **Android:** yüklü uygulama Paylaş menüsünde görünür.
  - **iPhone:** imzalı Kısayol tek dokunuşla eklenir (kişisel erişim anahtarıyla; anahtar sadece dosya göndermeye yarar).
  - Ekstre ve dökümler hemen içe aktarılır; fiş ve faturalar Özet'te "onay bekliyor" olarak bekler.
- **Telegram** — Son ödeme ve zekât hatırlatmaları, ay sonu raporu, günlük **şifreli** yedek.

</details>

<details>
<summary><b>Birden fazla kullanıcı</b></summary>

- Arkadaşlarına **davet bağlantısı** ya da geçici şifreyle hesap aç. Her kullanıcının verisi **ayrı bir SQLite dosyasında**;
  yönetici ekranında sadece kullanıcı adı, son giriş ve yapay zekâ kullanımı görünür, kimsenin harcaması görünmez.
- Yöneticinin Gemini anahtarı paylaşılır, kişi başı **aylık okuma kotasıyla** (varsayılan 100); kendi anahtarını giren sınırsız.
- Başlarken rehberi: ekstre yükle → bakiyeleri kontrol et → maaş (net/brüt) → fiş okut → bildirimler.
- **Ortak defter (eş, aile):** Ayarlar → Ortak defter'den davet et; kendi kullanıcısıyla girer, **senin defterini** görür,
  fiş ve harcama ekler, kategori düzeltir. Ekstre yükleme, silme ve ayarlar sende. Neyi görmeyeceğini sen seçersin:
  belirli kartlar ve hesaplar, faiz ve gecikme ücretleri, sayfalar (Zekât, Borçlar…) ya da tek tek işlemler. Gizleme
  **sunucuda** uygulanır: gizlenen veri ona giden hiçbir yanıtta, toplamda ya da grafikte yer almaz.

</details>

## 🚀 Hızlı kurulum

### Tek tıkla, kendi sunucunda

Teknik bilgi gerekmeden kendi HesapKitap'ını kur, telefondan kendi adresinle kullan:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/alameddinc/hesapkitap)

Railway, Fly.io, evdeki cihaz (CasaOS, Unraid, Synology, Raspberry Pi — hazır imaj `ghcr.io/alameddinc/hesapkitap`)
ve kendi VPS'in için adım adım rehber: **[docs/KURULUM.md](docs/KURULUM.md)**

### Bilgisayarında Docker ile

Gereken tek şey [Docker](https://docs.docker.com/get-docker/).

```bash
git clone https://github.com/alameddinc/hesapkitap.git
cd hesapkitap
docker compose up -d
```

→ **http://localhost:8080** — ilk açılışta yönetici hesabını oluştur, bitti.

Gemini (fiş/fatura/ekstre okuma) ve Telegram (hatırlatma) anahtarlarını **Ayarlar → Anahtarlar** sayfasından girersin;
`.env` dosyasıyla uğraşman gerekmez.

**Denemek için örnek belgeler:** [`docs/demo/`](docs/demo/) — uydurma ekstre PDF'leri (VakıfBank, Kuveyt Türk,
yapay zekâ ile okunan "Örnek Bank"), fiş fotoğrafları ve fatura; adım adım 5 dakikalık demo akışı orada.

Ya da hazır demo verisiyle bak:

```bash
docker compose exec hesapkitap python -m app.demo
```

### Sunucuya kurulum

Aynı `docker compose up -d`; önüne HTTPS yapan bir reverse proxy (Caddy, Nginx, Traefik, Cloudflare Tunnel) koy.

- Uygulamayı internete açıp kurulumu yapmadan bırakma: yerel ağ dışından ilk kurulumda container log'undaki
  tek seferlik kod istenir (`docker compose logs | grep kurulum`).
- Şifreler PBKDF2 ile hash'lenir; oturum çerezi HttpOnly + (HTTPS'te) Secure, imzalı ve şifre değişince geçersiz olur;
  hatalı girişlerde 15 dakika kilit.
- Sunucu yöneticileri ayarları `.env` ile de verebilir (bkz. [`.env.example`](.env.example)); `.env` uygulama içindekinden önceliklidir.
- Veri: `./data` — `system.db` (kullanıcılar, davetler), `ekstre.db` (yönetici), `users/<id>/` (diğer kullanıcılar),
  yüklenen dosyalar ve gece yedekleri. Yedeklemek için bu klasörü kopyalaman yeterli.
- **iPhone kısayolu** kendi adresin için (macOS gerekir): `python3 scripts/make_shortcut.py https://alanadin` →
  çıkan dosyayı `data/shortcuts/hesapkitap.shortcut` olarak koy; Ayarlar'daki "Kısayolu ekle" düğmesi onu verir.

## 🔒 Gizlilik

- **Kendi sunucunda** tüm veri o sunucuda, SQLite dosyalarında durur. Dışarıya sadece senin açtığın özellikler konuşur:
  - Gemini anahtarı girersen fiş, fatura ve tanınmayan bankaların ekstreleri okunmak için Google'a gider.
    Anahtar girmezsen fişler sunucuda yerel OCR ile okunur, hiçbir yere gitmez.
  - **Banka şablonları (varsayılan açık):** yeni bir bankanın dökümü yapay zekâsız okunmayı öğrenince, okuma kuralı
    (banka adı ve satır düzeni — isim, IBAN, tutar, açıklama **değil**) topluluk deposuna gönderilir; onaylananlar
    [`templates/`](templates/) klasörüne eklenir ve herkes yararlanır. Kurulumda ya da Ayarlar → Banka şablonları'ndan kapatılır.
    Tanınan bankaların (VakıfBank, Kuveyt Türk) ekstreleri ve VakıfBank hesap dökümü sunucuda okunur, dışarı çıkmaz.
    Google AI Studio'da faturalandırmayı açarsan (ücretli katman) içerik model eğitiminde kullanılmaz.
  - Anlık ve geçmiş altın/döviz fiyatı için sadece fiyat sorgulanır (finans.truncgil.com, Yahoo Finance); kişisel veri gitmez.
  - Telegram'a giden yedekler AES ile şifrelidir; açmak için: `python -m app.backup decrypt dosya.db.enc`
- **Başkasının sunucusunda hesabın varsa** verin sana ait ayrı bir dosyadadır ve uygulamada yöneticinin onu görebileceği
  bir ekran yoktur; ama sunucuyu işleten kişi dosyaya teknik olarak erişebilir. Güvendiğin birinin sunucusunu kullan ya da kendininkini kur.
  Verini istediğin an Ayarlar → Yedekler'den tek dosya olarak indirebilirsin.
- Kart numaraları sadece maskeli (ilk 4 + son 4) saklanır. Paylaş kısayolunun anahtarı sunucuda sadece özet (hash) olarak tutulur.

## 🛠️ Geliştirme

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
| `app/parsers/` | Kart ekstresi okuyucuları + yapay zekâ yedeği |
| `app/bank_pdf.py`, `app/bank_import.py` | Hesap dökümü: kural tabanlı okuyucu, bakiye zinciri, içe aktarma |
| `app/categorize.py` | Varsayılan kategori kuralları |
| `app/finance.py` | Özet, planlama, uyarılar, faiz alarmı, istatistik |
| `app/zakat.py`, `app/donations.py` | Zekât (nisap, kameri yıl, geçmiş yıllar), varlıklar, sadaka |
| `app/payroll.py` | Brütten nete maaş |
| `app/system.py`, `app/auth.py` | Kullanıcılar, davetler, oturum, kota, erişim anahtarları |
| `app/share.py` | Telefondan Paylaş |
| `app/api/main.py` | FastAPI uçları |
| `web/` | React + Vite + Tailwind arayüzü |
| `tests/fixtures/` | **Uydurma** ekstre metinleri (gerçek veri yok) |

**Yeni banka eklemek** en değerli katkı — rehber: [CONTRIBUTING.md](CONTRIBUTING.md)

## English

HesapKitap is a self-hosted personal finance app built for Turkish banks: upload credit card statements and bank account
PDFs (exact parsers for VakıfBank and Kuveyt Türk, Gemini fallback for others, all verified by reconciliation and balance-chain
checks), snap receipts, auto-categorize, plan the coming months with gross-to-net salary, track gold/FX holdings, savings jars,
debts, zakat (Hanafi/Shafi'i, lunar year, missed years) and charity, get interest alarms with their cause, share files from
iPhone/Android, and invite friends with fully separate per-user databases. Quick start: `docker compose up -d` →
http://localhost:8080. Source-available under the PolyForm Noncommercial License 1.0.0: free for personal and other
noncommercial use; commercial use requires a separate license from the author.

## 📜 Lisans

[PolyForm Noncommercial 1.0.0](LICENSE) — kaynağı açık, **ticari olmayan** her kullanım serbest: kendin için kurup
kullanabilir, değiştirebilir, arkadaşlarınla paylaşabilirsin. Ticari kullanım (ücretli hizmet sunmak, bir şirket ürününe
gömmek vb.) için yazardan ayrıca izin/lisans gerekir.

Bu değişiklikten önce MIT ile yayımlanmış sürümler MIT lisansıyla kalır; yeni lisans bundan sonraki sürümler içindir.

</details>

