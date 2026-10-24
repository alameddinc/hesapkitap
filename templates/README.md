# Banka şablonları (topluluk)

Hesap dökümlerini **yapay zekâsız** okumak için okuma kuralları. Her HesapKitap sunucusu bu klasörü günde bir kez
çeker; tanımadığı bir bankanın dökümü gelince önce buradaki kurallara bakar.

Bir şablon **kod değildir** ve **belgeden metin taşımaz**:

```json
{
  "v": 1, "kind": "account", "bank": "Halkbank",
  "keywords": ["hesap özeti", "bakiye"],
  "date": {"sep": "-", "order": "dmy"},
  "amount_idx": 0, "balance_idx": 1, "scope": "block"
}
```

| Alan | Anlamı |
|---|---|
| `bank` | IBAN'daki banka kodundan bulunan banka |
| `keywords` | Başlıkta geçen kelimeler — **sabit bir sözlükten** (isim, IBAN, tutar olamaz) |
| `date` | Hareket satırı bu biçimde tarihle başlar (`dmy` + ayraç) |
| `amount_idx`, `balance_idx` | Satırdaki para değerlerinden hangisi tutar, hangisi bakiye |
| `scope` | `block`: tutarlar satırda ya da devam satırlarında · `line`: sadece tarih satırında |

Her okuma **bakiye zinciriyle** doğrulanır (önceki bakiye + tutar = bakiye); tutmazsa şablon kullanılmaz.

**Nasıl eklenir?** Uygulama yeni bir bankayı yapay zekâsız okumayı başarınca kuralı kendisi öğrenir ve (Ayarlar'da
kapatılmadıysa) merkeze gönderir; onaylananlar buraya eklenir. Elle eklemek için aynı biçimde bir `.json` dosyası
(tek nesne ya da liste) ile PR aç.
