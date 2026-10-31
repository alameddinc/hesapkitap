// Terim sözlüğü (denetim S4): kullanıcıya görünen finans kavramlarının TEK adı ve kısa açıklaması.
// Kopyası: hesapkitap-mobile/src/lib/terimler.ts (asıl). Bir adı ya da açıklamayı değiştirirsen ikisinde birden değiştir.
//
// Kurallar
// - Arayüzde "virman" kelimesi geçmez: her yerde `kendiHesaplar.ad` ("Kendi hesapların arası").
// - Özet'teki "gelir − gider" rakamı "Artan"; "Kalan" yalnız bir şeyin kalanı için (borcun kalanı, limitten kalan, kalan gün).
// - Tahmini rakam: tutarın önünde "~"; etiket "tahmini" (küçük harf, cümle içinde) ya da "Tahmini" (başlıkta).
// - Eşleşme: "Eşleşti" (iki kayıt birleşti, bir kez sayılır); "Birleştirildi" yalnız hesap birleştirmede.
// - Ekstre denklemi tutmayınca rozet "⚠ Rakamlar tutmadı" (eski: "⚠ Kontrol et"); makine doğruladıysa "Doğrulandı ✓",
//   kullanıcı onayladıysa "Onaylandı ✓".
// - Para biçimi: "₺" önde ("₺1.250,00"); eksi "−₺".
// Her kavram bir ekranda ilk geçtiği yerde (i) ile açıklanır: web/src/components/TerimBilgi.tsx → <TerimBilgi id="harcanabilir" />.

export interface Terim {
  /** arayüzdeki tek adı */
  ad: string;
  /** rakamın altındaki kısa formül/açıklama satırı (isteğe bağlı) */
  kisa?: string;
  /** (i) dokununca açılan açıklama: 1–3 cümle, jargon yok */
  aciklama: string;
}

export const TERIM = {
  harcanabilir: {
    ad: "Harcanabilir",
    kisa: "Eldeki para − ödeyeceklerin",
    aciklama: "Bankadaki ve nakit paran − ödenecek kart ekstresi − TL borçların − kumbaraya ayırdığın − ödenmemiş zekât. " +
      "Altın/döviz, alacakların ve yemek kartları dahil değil. Eksiyse ödeyeceklerin eldeki paranı aşıyor.",
  },
  netDurum: {
    ad: "Net durum",
    kisa: "Sahip oldukların − borçların",
    aciklama: "Banka ve nakit, altın/döviz, yemek kartları ve alacakların toplanır; kart borcu, kişilere borcun ve " +
      "ödenmemiş zekât düşülür. Henüz kesilmemiş ekstreler düşülmez; onlar \"Tahmini ekstre sonrası\" satırında.",
  },
  artan: {
    ad: "Artan",
    kisa: "Gelir − gider",
    aciklama: "Bu dönemin geliri eksi gideri. Birikime ayırdığın gider sayılmaz. Bugün harcayabileceğin para değil; " +
      "onun için \"Harcanabilir\"e bak.",
  },
  eldeKalan: {
    ad: "Elde kalan",
    kisa: "Gelir − gider − birikim",
    aciklama: "Bu ayın geliri eksi gideri, birikime ayırdığın da düşülmüş hali.",
  },
  kendiHesaplar: {
    ad: "Kendi hesapların arası",
    aciklama: "Bir hesabından öbür hesabına aktardığın para (ör. bankadan nakite ya da başka bankana). Gelir ya da gider " +
      "sayılmaz; yalnız iki hesabın bakiyesi değişir. Başkasına gönderdiğin para Transferler'de.",
  },
  tahmini: {
    ad: "Tahmini",
    kisa: "~",
    aciklama: "Ekstre henüz kesilmedi; rakam bugüne kadarki harcamalardan tahmin edildi (\"~\" işareti). Ekstre gelince " +
      "kesin rakam yazılır.",
  },
  kesimTahmini: {
    ad: "Kesimde tahmini",
    aciklama: "Ekstre kesildiğinde bu kartın borcunun yaklaşık ne olacağı: bu dönemin taksitleri ve abonelikleri, " +
      "üstüne bugünkü harcama hızınla dönem sonuna kadar beklenen harcama. Ekstre gelince kesin rakam yazılır.",
  },
  tahminiEkstreSonrasi: {
    ad: "Tahmini ekstre sonrası",
    aciklama: "Net durumdan henüz kesilmemiş ekstrelerin tahmini tutarı da düşülünce kalan.",
  },
  eslesti: {
    ad: "Eşleşti",
    aciklama: "Aynı harcamanın iki kaydı (ör. elle girdiğin ve ekstreden ya da dökümden gelen) birleşti; toplamlarda " +
      "bir kez sayılır. Yanlışsa işlem detayından ayırabilirsin.",
  },
  rakamlarTutmadi: {
    ad: "Rakamlar tutmadı",
    aciklama: "Ekstredeki özet (önceki borç + harcamalar − ödemeler = yeni borç) okunan işlemlerle aynı çıkmadı; bir " +
      "satır eksik ya da fazla okunmuş olabilir. PDF'i açıp karşılaştır; doğruysa \"Doğru, onayla\" de.",
  },
  dogrulandi: {
    ad: "Doğrulandı",
    aciklama: "Ekstredeki özet rakamlar okunan işlemlerle kuruşu kuruşuna tuttu (uygulama kontrol etti).",
  },
  onaylandi: {
    ad: "Onaylandı",
    aciklama: "Rakamlar tutmamıştı; sen PDF'e bakıp doğru olduğunu onayladın.",
  },
} as const satisfies Record<string, Terim>;

export type TerimId = keyof typeof TERIM;

/** Sık kullanılan kısa metinler (bildirimler, rozetler): sözlükle aynı kelimeler. */
export const METIN = {
  /** iki kayıt birleşince bildirim */
  eslestiBildirim: "Eşleşti · bir kez sayılır",
  /** rozet: işlem ekstredeki/dökümdeki kayıtla birleşmiş */
  eslestiEkstre: "Ekstreyle eşleşti",
  eslestiDokum: "Dökümle eşleşti",
  /** ekstre rozeti */
  rakamlarTutmadi: "⚠ Rakamlar tutmadı",
} as const;
