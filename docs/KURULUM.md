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
- **Başka bir sunucudan taşınıyorsan:** eski sunucuda Ayarlar → Yedekler'den yedeği indir, yeni kurulumda veri
  klasörüne (`/app/data/ekstre.db`) koy.
