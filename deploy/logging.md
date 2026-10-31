# Günlükler (log) ve kişisel veri

HesapKitap günlüğe kişisel veri yazmamaya çalışır; yine de sunucuyu kuran kişi günlüklerin ne kadar tutulduğunu
ayarlamalıdır (KVKK: silinen hesabın izi süresiz kalmasın).

## Uygulamanın yaptıkları

- Erişim günlüğünde sorgu dizgesi yazılmaz: `GET /api/admin/deleted?…`.
- Her satırda e-posta, IBAN, kart numarası ve TC kimlik numarası biçimindeki diziler maskelenir. İstisna metinleri de
  bu kurala dahildir.
- Kullanıcılar günlükte adla ya da e-postayla değil, kimlik numarasıyla geçer (`job backup u12 ok`).
- Yöneticinin e-postayla silinmiş hesap araması `POST /api/admin/deleted` ile yapılır. E-posta istek gövdesinde
  gittiği için erişim günlüğüne düşmez.
- `HESAPKITAP_LOG_LEVEL`: varsayılan `INFO`. `HESAPKITAP_LOG_FILE` verilirse günlük ayrıca bu dosyaya yazılır
  (10 MB × 5, döndürmeli).

## Docker günlük sürücüsü (önerilen)

Docker'ın `json-file` sürücüsü varsayılan ayarda günlükleri **süresiz** biriktirir. Her servise ekleyin:

```yaml
services:
  hesapkitap:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
```

Bu repodaki `docker-compose.yml`, `deploy/docker-compose.yml` ve `deploy/casaos/docker-compose.yml` dosyalarında bu
ayar zaten var. Kendi sunucu compose dosyanızda da (ör. repoya girmeyen `docker-compose.server.yml`) ekleyin.

Bütün kapsayıcılar için varsayılan yapmak isterseniz `/etc/docker/daemon.json`:

```json
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "5" } }
```

Bu ayar ancak `systemctl restart docker` sonrasında ve yalnız **yeniden oluşturulan** kapsayıcılarda geçerli olur.

## Saklama süresi ve hesap silme

Günlük hacmine göre 5 × 10 MB, aşağı yukarı birkaç haftalık kayıt eder. Aydınlatma metninde günlükler için verilen
saklama süresi (ör. "en çok 30 gün") bununla uyumlu olmalıdır. Günlükte e-posta bulunmadığından hesap silindiğinde
günlükte geriye yalnız kullanıcı kimlik numarası kalır. Bu numara yeniden kullanılmaz.

Nginx gibi bir vekilin erişim günlüğü uygulamanın dışındadır. Vekilde de sorgu dizgesini yazmayın, örneğin nginx'te
`$uri` kullanın, `$request_uri` kullanmayın. Vekilin günlüklerini de logrotate ile döndürün.
