# HesapKitap'ı kendine kur

Teknik bilgi gerekmez: aşağıdan sana uyan yolu seç, adımları sırayla yap. Kurulum bitince telefonundan
**kendi adresine** girip uygulama gibi kullanırsın (Ayarlar → Uygulamayı yükle, Telefondan paylaş).

| Yol | Kime göre | Aylık maliyet (yaklaşık) | Süre |
|---|---|---|---|
| [**Render**](#1-render-en-kolay) | Hiç uğraşmak istemeyen | ~7–8 $ | 5 dk |
| [**Railway**](#2-railway) | Render'a alternatif | ~5 $ | 5 dk |
| [**Fly.io**](#3-flyio) | Komut satırından korkmayan | ~3–5 $ | 10 dk |
| [**Evdeki cihaz**](#4-evdeki-cihaz-casaos-unraid-synology-raspberry-pi) (CasaOS, Unraid, Synology, Raspberry Pi) | Evde sürekli açık bir cihazı olan | elektrik | 10 dk |
| [**Kendi sunucun (VPS)**](#5-kendi-sunucun-vps) | Linux sunucusu olan | ~4–6 $ | 15 dk |

> Fiyatlar servislerin kendi sayfalarında değişebilir; kurmadan önce kontrol et. Hepsinde veri **senin hesabındaki**
> kalıcı diskte durur — HesapKitap'ın geliştiricisi dahil kimse erişemez.

**Her kurulumda ilk açılış aynı:** adresine girince "HesapKitap'a hoş geldin" ekranı çıkar → kullanıcı adı ve şifre
belirle → (internetten kurulduysa) **kurulum kodu** istenir. Bu kod, birinin senden önce davranıp yönetici olmasını engeller.
Nerede bulacağın her yolda yazıyor.

---

## 1. Render (en kolay)

1. [render.com](https://render.com)'da hesap aç (GitHub ya da e-posta ile).
2. Bu düğmeye bas:

   [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/alameddinc/hesapkitap)

3. Açılan sayfada **Apply / Deploy**'a bas. Ödeme bilgisi istenir (kalıcı disk ücretli planda çalışır: Starter).
4. 5–10 dakika bekle (ilk derleme). Panelde servis **Live** olunca üstteki `https://hesapkitap-xxxx.onrender.com`
   adresine tıkla.
5. **Kurulum kodu:** Render panelinde servis → **Environment** → `SETUP_CODE` değerini kopyala, kurulum ekranına yapıştır.
6. Bitti. Telefondan aynı adrese gir → **Diğer → Uygulamayı yükle**.

**Güncelleme:** Render panelinde servis → **Manual Deploy → Deploy latest commit**. Verilerin silinmez.
**Kendi alan adın** (ör. `para.adin.com`): Settings → Custom Domains.

---

## 2. Railway

1. [railway.com](https://railway.com)'da hesap aç.
2. **New Project → Deploy from GitHub repo** → `alameddinc/hesapkitap` (ya da kendi kopyan / fork'un).
3. Servis oluşunca:
   - **Variables** sekmesi: `SECRET_KEY` = uzun rastgele bir metin, `SETUP_CODE` = kendi belirlediğin bir kod
     (ör. 8 haneli sayı), `COOKIE_SECURE` = `1`
   - **Settings → Volumes → Add Volume**: mount path **`/app/data`** (bu olmazsa her güncellemede verin silinir!)
   - **Settings → Networking → Generate Domain**
4. Oluşan adrese gir, kurulum ekranında `SETUP_CODE` olarak yazdığın kodu gir.

**Güncelleme:** servis → **Deployments → Redeploy** (ya da fork'unu güncelle).

---

## 3. Fly.io

Bilgisayarında [flyctl](https://fly.io/docs/flyctl/install/) kurulu olmalı.

```bash
git clone https://github.com/alameddinc/hesapkitap.git && cd hesapkitap
# fly.toml içindeki app adını benzersiz bir adla değiştir (ör. hesapkitap-ayse)
fly launch --copy-config --no-deploy
fly volumes create hesapkitap_data --size 1 --region fra
fly secrets set SECRET_KEY=$(openssl rand -hex 32) SETUP_CODE=KENDI-KODUN   # kurulum ekranında bu kodu gireceksin
fly deploy
```

Adres: `https://<app-adın>.fly.dev`. **Güncelleme:** `git pull && fly deploy`.

---

## 4. Evdeki cihaz (CasaOS, Unraid, Synology, Raspberry Pi)

Hazır imaj kullanılır (`ghcr.io/alameddinc/hesapkitap`, Intel/AMD ve ARM — Raspberry Pi 4/5 dahil); derleme yok.

- **CasaOS / ZimaOS:** App Store → **Custom Install** → sağ üstte **Import** → [`deploy/casaos/docker-compose.yml`](../deploy/casaos/docker-compose.yml)
  içeriğini yapıştır → Install. Adres: `http://cihaz-ip:8080`.
- **Unraid:** Docker → Add Container → Template alanına
  `https://raw.githubusercontent.com/alameddinc/hesapkitap/main/deploy/unraid/hesapkitap.xml` → Apply.
- **Synology (Container Manager) / Portainer / herhangi bir Docker:** Proje oluştur, [`deploy/docker-compose.yml`](../deploy/docker-compose.yml)
  içeriğini yapıştır, çalıştır.

Evde kurulduğunda ilk kurulum **ev ağından** yapılırsa kod istenmez.

**Dışarıdan (telefonla, evden uzakta) erişmek** için en güvenli ve kolay yol:
- **Tailscale** (önerilen): cihaza ve telefona Tailscale kur, aynı hesapla gir → telefondan `http://cihaz-adı:8080`.
  İnternete hiçbir port açılmaz.
- ya da **Cloudflare Tunnel**: kendi alan adınla HTTPS, port açmadan.

> Modeminden port yönlendirip uygulamayı doğrudan internete açma; açacaksan önüne HTTPS koy (Cloudflare Tunnel, Caddy).

**Güncelleme:** CasaOS/Portainer'da "Update/Pull" ya da `docker compose pull && docker compose up -d`.

---

## 5. Kendi sunucun (VPS)

Bir Linux sunucu (Hetzner, DigitalOcean… 1 GB RAM yeterli), bir alan adı (DNS'te sunucunun IP'sine A kaydı).

```bash
curl -fsSL https://get.docker.com | sh
mkdir hesapkitap && cd hesapkitap
curl -fsSLO https://raw.githubusercontent.com/alameddinc/hesapkitap/main/deploy/docker-compose.yml
docker compose up -d
# HTTPS için Caddy (alan adını yaz):
docker run -d --name caddy --restart unless-stopped --network host -v caddy:/data caddy \
  caddy reverse-proxy --from para.adin.com --to localhost:8080 --header-up "X-Real-IP {remote_host}"
docker compose logs | grep kurulum      # kurulum kodu
```

`https://para.adin.com` → kurulum. **Güncelleme:** `docker compose pull && docker compose up -d`.

**Önünde vekil (reverse proxy) varken gerçek ziyaretçi adresi:** giriş denemesi sınırları ve "yerel ağdan kurulum"
kararı ziyaretçinin IP'sine bakar. Uygulama vekil başlıklarına (`X-Real-IP`, yoksa `X-Forwarded-For`) yalnız bağlantı
güvenilir bir vekilden geliyorsa inanır: `HESAPKITAP_TRUSTED_PROXIES` (virgüllü CIDR; verilmezse özel ağlar ve
loopback, yani aynı makinedeki ya da docker ağındaki Caddy/nginx). Vekil, istemcinin gönderdiği `X-Real-IP`'yi ezmeli:
Caddy'de yukarıdaki `--header-up`, nginx'te `proxy_set_header X-Real-IP $remote_addr;`. Cloudflare Tunnel
kullanıyorsan `HESAPKITAP_TRUST_CF=1` (o zaman `CF-Connecting-IP` okunur). Vekil yoksa (doğrudan açık) başlıklar hiç
okunmaz. Ayrıca bir hesaba 15 dakikada 10 hatalı şifre denenirse, hangi adresten gelirse gelsin o hesap 15 dakika
kilitlenir; başarılı girişte sayaç sıfırlanır.

---

## Kurulumdan sonra

- **Ayarlar → Anahtarlar:** Gemini anahtarı (fiş, fatura ve tanınmayan bankaları okumak için;
  [aistudio.google.com/apikey](https://aistudio.google.com/apikey)), istersen Telegram botu.
- **Ayarlar → Telefondan paylaş:** Android'de uygulamayı yükle; iPhone'da elle kurulum adımları (kendi kısayolunu
  Mac'te `python3 scripts/make_shortcut.py https://adresin` ile üretebilirsin).
- **Yedek:** Ayarlar → Yedekler'den indir; Telegram bağlıysa her gece şifreli yedek oraya gelir.
- **Arkadaşların için:** Ayarlar → Kullanıcılar → Davet oluştur. Her kullanıcının verisi ayrı dosyada.
- **Telefon uygulamasıyla:** IMAP e-posta otomasyonu, Telegram, yedekler, anahtarlar, banka şablonları, paylaş
  anahtarları ve hatırlanan PDF şifreleri yalnız web'de kurulur (`https://adresin/#/ayarlar`). Sunucu bu listeyi
  `GET /api/settings/web-only` ile verir; uygulama "bu ayarlar web'de" bağlantısını buradan gösterebilir.
- **Başka bir sunucudan taşınıyorsan:** eski sunucuda Ayarlar → Yedekler'den yedeği indir, yeni kurulumda veri
  klasörüne (`/app/data/ekstre.db`) koy.

---

## Google / Apple ile giriş (mobil uygulama)

Telefon uygulaması Google ya da Apple'dan bir kimlik belgesi (ID token) alır ve sunucuya `POST /api/auth/google` /
`POST /api/auth/apple` ile gönderir. Sunucu belgenin imzasını, süresini ve **kimin için üretildiğini** (`aud`) kontrol
eder. `aud` aşağıdaki listelerden birinde değilse giriş reddedilir. İkisi de boşsa (varsayılan) giriş ekranında Google
ve Apple düğmeleri görünmez (`/api/auth/status` → `google: false`, `apple: false`).

| Değişken | Örnek (yer tutucu) | Anlamı |
|---|---|---|
| `HESAPKITAP_GOOGLE_CLIENT_IDS` | `<web-client-id>.apps.googleusercontent.com` | Kabul edilen Google OAuth client ID'leri, virgülle. Mobil uygulama hem iOS'ta hem Android'de belgeyi **web client ID** için ister; bu yüzden listede en az web client ID olmalı. |
| `HESAPKITAP_APPLE_CLIENT_IDS` | `com.ornek.hesapkitap` | Kabul edilen Apple `aud` değerleri, virgülle. iOS uygulamasından girişte bu, uygulamanın **bundle ID**'sidir. |
| `HESAPKITAP_SOCIAL_NONCE` | `required` | Tek kullanımlık nonce zorunlu (bkz. Güvenlik başlıkları). |
| `HESAPKITAP_APPLE_TEAM_ID` | `<TEAM_ID>` | Apple Developer Team ID (token iptali; aşağıya bak). |
| `HESAPKITAP_APPLE_KEY_ID` | `<KEY_ID>` | *Sign in with Apple* yetkili anahtarın Key ID'si. |
| `HESAPKITAP_APPLE_PRIVATE_KEY` | `-----BEGIN PRIVATE KEY-----\n…` | O anahtarın `.p8` içeriği (PEM; tek satırda `\n` olabilir). Ya da `HESAPKITAP_APPLE_PRIVATE_KEY_FILE` = dosya yolu. |

Hangi değerleri yazacağın, hangi mobil uygulamayı kullandığına bağlı:

- **Kendi derlediğin mobil uygulama:** Google Cloud Console'da bir proje aç → *APIs & Services → Credentials*:
  bir **Web** client (bu ID'yi hem sunucuya hem uygulamanın `VITE_GOOGLE_WEB_CLIENT_ID` değişkenine yaz), bir **iOS**
  client (bundle ID ile; `VITE_GOOGLE_IOS_CLIENT_ID`) ve bir **Android** client (paket adı + imza SHA-1). Apple için
  Apple Developer'da uygulamanın App ID'sinde *Sign in with Apple* yeteneğini aç; `HESAPKITAP_APPLE_CLIENT_IDS` =
  bundle ID (uygulamada `VITE_APPLE_CLIENT_ID` ile aynı).
- **Mağazadaki hazır uygulamayla kendi sunucuna bağlanıyorsan:** uygulamanın içine gömülü client ID'ler o uygulamayı
  yayınlayana aittir. Sunucuna bu değerleri yazarsan o uygulamadan Google/Apple girişi çalışır; yazmazsan kullanıcıların
  e-posta ve şifreyle girer (her zaman çalışır).

Kurallar (her iki sağlayıcı için aynı):

- Aynı e-postayla hesap varsa ona girilir. Şifreyle açılmış ama e-postası doğrulanmamış hesapta Google/Apple e-postanın
  sahibini kanıtladığı için eski şifre geçersiz olur ve açık oturumlar kapanır.
- Hesap yoksa: kendi sunucunda (kayıt kapalı) yalnız **davet bağlantısıyla** yeni hesap açılır; davetsiz giriş
  "Bu e-postayla bir hesap yok" der. Açık kayıt yalnız hazır sürümde (`HESAPKITAP_HOSTED=1` ve
  `HESAPKITAP_OPEN_SIGNUP=1`).
- Google/Apple ile açılan hesabın şifresi yoktur. Web'e girmek için "Şifremi unuttum" ile şifre belirlenir (giden
  posta, yani `SMTP_HOST` + `MAIL_FROM`, ayarlı olmalı). Web'de Google/Apple düğmesi yok.

### Apple: hesap silinince belge iptali ve sunucu bildirimleri

App Store kuralı (5.1.1(v)): Apple ile girilmiş hesap silinince sunucu Apple'daki belgeyi iptal etmeli. Bunun için
`HESAPKITAP_APPLE_TEAM_ID`, `HESAPKITAP_APPLE_KEY_ID` ve `HESAPKITAP_APPLE_PRIVATE_KEY` (ya da `_FILE`) gerekir.
Anahtar: Apple Developer → *Certificates, Identifiers & Profiles → Keys* → yeni anahtar, *Sign in with Apple* işaretli,
uygulamanın App ID'sine bağlı; `.p8` bir kez indirilir, gizli tut (repoya koyma). Üçü de yoksa özellik kapalıdır ve
giriş bugünkü gibi çalışır.

- Girişte uygulama `authorization_code` da gönderir (yetenek `apple_revoke`); sunucu bunu Apple'da `refresh_token`'a
  çevirir ve kullanıcı başına şifreli (AES-GCM, anahtar `SECRET_KEY`'den türetilir) saklar. `SECRET_KEY` değişirse saklı
  belgeler çözülemez: iptal yapılamaz (kaydına not düşer), sonraki girişte yenisi alınır.
- Hesap kalıcı silinince (7 günlük bekleme sonunda; yönetici silmesi ve ortak defterden çıkarma da) belge Apple'da
  iptal edilir. Apple'a ulaşılamazsa silme yine tamamlanır; iptal artan aralıklarla 30 gün yeniden denenir.
- Sunucu bildirimleri: Apple Developer'da App ID → *Sign in with Apple → Configure → Server-to-Server Notification
  Endpoint* = `https://<sunucu-adresin>/api/auth/apple/notifications`. Bu uç için ayar gerekmez (imza Apple'ın açık
  anahtarıyla doğrulanır; `HESAPKITAP_APPLE_CLIENT_IDS` dolu olmalı). Kullanıcı uygulamanın bağını kaldırırsa Apple ile
  açılmış oturumları düşer; Apple hesabını silerse, şifresi yoksa ve e-postası Apple'ın gizli aktarma adresiyse hesap
  7 günlük silme akışına girer, değilse yalnız Apple bağı kopar.

## Deftere özel posta adresi (ileri düzey)

Hazır sürümde her defterin kendine ait bir adresi olur (`fatura-<kod>@<alan adı>`). Banka e-ekstreleri ve faturalar bu
adrese yönlendirilir, sunucu DKIM imzasını doğrulayıp içe aktarır. **Kendi sunucunda bunun yerine IMAP kullan**
(Ayarlar → E-posta otomasyonu): kendi posta kutunu okur, ek bir altyapı gerektirmez. Bu bölüm, posta altyapısını da
kendin kuracaksan içindir.

| Değişken | Örnek (yer tutucu) | Anlamı |
|---|---|---|
| `HESAPKITAP_INBOUND_DOMAIN` | `posta.ornek.com` | Adreslerin alan adı. Bu özelliği kullanacaksan **mutlaka** kendi alan adını yaz; boş bırakılırsa projenin hazır sürümünün alan adı varsayılır ve o adrese giden posta sana ulaşmaz. |
| `HESAPKITAP_INBOUND_SECRET` | (uzun rastgele dizgi, ör. `openssl rand -hex 32`) | Posta işçisinin sunucuya gönderdiği gizli anahtar. Boşsa `POST /api/mail/inbound` ucu kapalıdır (404). |

Posta işçisinin (ör. Cloudflare Email Routing + Email Worker ya da kendi SMTP sunucunun bir betiği) yapması gereken:

```
POST https://<adresin>/api/mail/inbound
Authorization: Bearer <HESAPKITAP_INBOUND_SECRET>
X-HK-To: fatura-<kod>@<HESAPKITAP_INBOUND_DOMAIN>
Content-Type: message/rfc822

<ham MIME, en çok 20 MB>
```

- Alan adının MX kaydı işçiye yönlenmeli; `fatura-*` adreslerinin hepsi aynı işçiye gelmeli.
- Sunucu kodu adresten çözer (alan adına bakmaz), DKIM'i kendisi doğrular, aynı `Message-ID`'yi ikinci kez işlemez.
  Yanıt hemen döner, okuma arka planda yapılır.
- Web ve mobil uygulama adresi yalnız hazır sürümde (`HESAPKITAP_HOSTED=1`) gösterir. Kendi sunucunda bu bölümü
  açmak için hazır sürüm kipinin diğer sonuçlarını (paketler, ayar kilidi) da kabul etmen gerekir; çoğu kurulum için
  IMAP daha basittir.

## Güvenlik başlıkları

Uygulama her yanıta `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` ve `Permissions-Policy` ekler.
HTTPS ile gelen isteklere (TLS'i vekil bitiriyorsa `X-Forwarded-Proto: https`) `Strict-Transport-Security` de eklenir.

| Değişken | Varsayılan | Anlamı |
|---|---|---|
| `HESAPKITAP_HSTS_MAX_AGE` | `15552000` (180 gün) | HSTS süresi, saniye. `0`: gönderilmez. `includeSubDomains` eklenmez. |
| `HESAPKITAP_CSP` | `report` | `report`: CSP yalnız rapor modunda (`Content-Security-Policy-Report-Only`), hiçbir şey engellenmez. `enforce`: engelleyen başlık. `off`: gönderilmez. |
| `HESAPKITAP_SOCIAL_NONCE` | (boş) | `required`: Google/Apple girişinde sunucudan alınan tek kullanımlık nonce zorunlu olur. Yalnız nonce gönderen mobil sürüm yayındayken açın. |

CSP raporları `POST /api/csp-report` ucuna gelir ve günlüğe `hesapkitap.csp` adıyla `CSP raporu: …` satırı olarak
düşer. Satırda yalnız yönerge, engellenecek kaynağın alan adı ve sayfanın yolu yazılır, sorgu dizgesi yazılmaz.
Dakikada en çok 30 satır yazılır. Birkaç gün boyunca rapor gelmezse `HESAPKITAP_CSP=enforce` yapılabilir.

## Yedekten geri dönüş ve silinen hesaplar

Sunucuyu (sistem veritabanı ve kullanıcı klasörleri) eski bir yedekten geri yüklerseniz, yedekten **sonra** silinen
hesaplar geri gelir. Bunu önlemek için:

1. Geri yüklemeden önce güncel `system.db` dosyasını kenara kopyalayın (ör. `system-once.db`).
2. Yedeği geri yükleyin.
3. Önce kuru çalıştırın, sonra uygulayın:

   ```sh
   python -m app.deletion reapply /yol/system-once.db           # ne yapılacağını gösterir
   python -m app.deletion reapply /yol/system-once.db --apply   # kalıcı silinenleri yeniden siler, bekleyenleri kapatır
   ```

Dosyalar bir nesne deposundaysa (S3/R2), kova için **sürümleme (versioning) kapalı** olmalıdır. Sürümleme açıksa
silinen hesabın dosyaları eski sürüm olarak kalır. Açık tutmak zorundaysanız eski sürümleri birkaç gün içinde silen
bir yaşam döngüsü kuralı ekleyin.
