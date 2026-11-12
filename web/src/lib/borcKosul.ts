// Kaydın koşulları (yetenek debt_terms; alacakta debt_terms_receivable; sunucu app/debt_schedule.py terms_of /
// strict_of): kime / kimden (karşı tarafın türü), nasıl (geri) ödenecek (ödeme koşulu) ve tarih türü (kesin / söz). Mobil
// src/lib/endpoints/para.ts aynı sözler. Borç planı borcu bunlara göre sıralar (alacak plana girmez); hiçbir metin faizli
// bir yol önermez, alacakta faiz sorulmaz.

/** borçta: kisi, vergi_sgk, banka, magaza, diger · alacakta: kisi, kurum, diger */
export type CreditorType = "kisi" | "vergi_sgk" | "banka" | "magaza" | "diger" | "kurum";
export type Yon = "borc" | "alacak";
export type PayTerms = "parca" | "tek" | "taksitli" | "bilinmiyor";

/** Kime: kişi ya da kurum (Vergi / SGK, Banka, Mağaza / senet, Diğer). */
export const ALACAKLI: { value: CreditorType; label: string; hint: string }[] = [
  { value: "kisi", label: "Kişi", hint: "ör. Nermin Teyze" },
  { value: "vergi_sgk", label: "Vergi / SGK", hint: "ör. Vergi dairesi, SGK" },
  { value: "banka", label: "Banka", hint: "ör. Örnek Bank" },
  { value: "magaza", label: "Mağaza / senet", hint: "ör. senetli mobilya alışverişi" },
  { value: "diger", label: "Diğer", hint: "ör. site yönetimi" },
];

/** Nasıl ödenecek. Taksitli seçilince ödeme planı alanları açılır (yalnız TL borçta). */
export const KOSULLAR: { value: PayTerms; label: string; note: string }[] = [
  { value: "parca", label: "Parça parça ödenebilir", note: "Ayırabildiğin kadar ödersin; plan anlamlı tutarlara böler." },
  { value: "tek", label: "Tek seferde ödenecek", note: "Tamamı bir kerede ödenir; plan o güne kadar ne kadar biriktireceğini gösterir." },
  { value: "taksitli", label: "Taksitli (ödeme planı)", note: "Anlaştığın taksitleri kaydedersin: Borç planı her ay önce taksiti ayırır, günü gelince hatırlatılır." },
  { value: "bilinmiyor", label: "Bilmiyorum", note: "Plan parça parça ödenebilir gibi davranır." },
];

/** Tarih türü: kesin (tarihinde ödenmesi şart) ya da söz (esnek). */
export const TARIH_TURU: { value: "kesin" | "soz"; label: string; note: string }[] = [
  { value: "kesin", label: "Tarihinde ödenmesi şart", note: "Gecikirse bedeli ya da yaptırımı var; plan bu tarihe yetişmeyi önce tutar." },
  { value: "soz", label: "Söz verdim (esnek)", note: "Söz verdiğin tarih; plan yetişemezse tahmini ödeme ayını söyler." },
];

/** Alacakta "Kimden": kişi, kurum / firma ya da diğer (vergi / banka / mağaza alacakta anlamsız). */
export const KIMDEN: { value: CreditorType; label: string; hint: string }[] = [
  { value: "kisi", label: "Kişi", hint: "ör. Ahmet" },
  { value: "kurum", label: "Kurum / firma", hint: "ör. Örnek Ltd." },
  { value: "diger", label: "Diğer", hint: "ör. site yönetimi" },
];

/** Alacakta "Nasıl geri ödenecek". Taksitli seçilince ödeme planı alanları açılır (yalnız TL alacakta). */
export const KOSULLAR_ALACAK: { value: PayTerms; label: string; note: string }[] = [
  { value: "parca", label: "Parça parça", note: "Ödedikçe \"Aldım\" dersin; kalan kendiliğinden düşer." },
  { value: "tek", label: "Tek seferde", note: "Tamamı bir kerede geri ödenecek." },
  { value: "taksitli", label: "Taksitli (ödeme planı)", note: "Anlaştığınız taksitleri kaydedersin; taksit günü gelince hatırlatılır." },
  { value: "bilinmiyor", label: "Bilmiyorum", note: "Sonradan değiştirebilirsin." },
];

/** Alacakta tarih türü: söz (esnek) varsayılan, kesin tarih. */
export const TARIH_TURU_ALACAK: { value: "kesin" | "soz"; label: string; note: string }[] = [
  { value: "soz", label: "Söz verdi (esnek)", note: "Geri ödeyeceğini söylediği gün; günü gelince hatırlatılır." },
  { value: "kesin", label: "Kesin tarih", note: "Ödemenin yapılacağı gün belli; günü gelince hatırlatılır." },
];
// alacak sözleri sonu

/** Yöne göre seçenekler ve başlıklar (borç: Kime / Nasıl ödenecek; alacak: Kimden / Nasıl geri ödenecek). */
export const kosulMetni = (yon: Yon) => yon === "alacak"
  ? { kime: "Kimden", nasil: "Nasıl geri ödenecek", turler: KIMDEN, kosullar: KOSULLAR_ALACAK, tarih: TARIH_TURU_ALACAK }
  : { kime: "Kime", nasil: "Nasıl ödenecek", turler: ALACAKLI, kosullar: KOSULLAR, tarih: TARIH_TURU };

/** Tür kaydın yönüne uysun (sunucu debt_schedule.type_for): alacakta vergi / banka / mağaza → kurum, borçta kurum → diğer. */
export const yoneUygun = (yon: Yon, c: CreditorType): CreditorType =>
  yon === "alacak" ? (KIMDEN.some((x) => x.value === c) ? c : "kurum") : c === "kurum" ? "diger" : c;

export const alacakliEtiketi = (c: CreditorType | null | undefined) =>
  ALACAKLI.find((x) => x.value === c)?.label ?? KIMDEN.find((x) => x.value === c)?.label ?? null;
export const kosulEtiketi = (t: PayTerms | null | undefined, yon: Yon = "borc") =>
  kosulMetni(yon).kosullar.find((x) => x.value === t)?.label ?? null;

/** Sunucunun varsayılanı: taksitlide kesin; borçta kurumda kesin, kişide (ve türü boş eski kayıtta) söz; alacakta söz. */
export const varsayilanKesin = (c: CreditorType | null | undefined, terms: PayTerms, yon: Yon = "borc") =>
  terms === "taksitli" || (yon === "borc" && !!c && c !== "kisi");

export interface KosulGirdisi {
  creditor: CreditorType;
  terms: PayTerms;
  /** kullanıcı tarih türünü değiştirdiyse açık değer; değiştirmediyse null (sunucu varsayılanı) */
  strict: boolean | null;
  /** vade / söz tarihi var mı (yoksa tarih türü gönderilmez) */
  hasDue: boolean;
}

/** POST/PATCH /api/debts gövdesindeki koşul alanları (taksitli: plan alanları ayrıca). */
export function kosulGovdesi(k: KosulGirdisi): { creditor_type: CreditorType; pay_terms?: PayTerms; due_strict?: boolean } {
  const out: { creditor_type: CreditorType; pay_terms?: PayTerms; due_strict?: boolean } = { creditor_type: k.creditor };
  if (k.terms !== "taksitli") out.pay_terms = k.terms;
  if (k.strict != null && k.hasDue && k.terms !== "taksitli") out.due_strict = k.strict;
  return out;
}

/** Rozette kısa koşul etiketi (borçta ve alacakta aynı; mobil para.ts KOSUL_KISA) */
export const KOSUL_KISA: Partial<Record<PayTerms, string>> = { parca: "Parça parça", tek: "Tek seferde", taksitli: "Taksitli" };

/** Satırdaki rozetler: ["Mağaza / senet", "Tek seferde ödenecek", "Kesin tarih"]; alacakta ["Kişi", "Parça parça", "Söz"].
 *  tarih: false → tarih türü yok (satırın tarih yazısı zaten "söz" / "vade" der; mobil de böyle). */
export function kosulRozetleri(d: { direction?: Yon; creditor_type?: CreditorType | null; terms?: PayTerms | null;
  strict?: boolean | null; due_date?: string | null; schedule?: unknown }, tarih = true): string[] {
  const yon = d.direction ?? "borc";
  // rozette kısa etiket (borçta da "Parça parça", "Tek seferde"); uzun etiket formda
  const parts = [alacakliEtiketi(d.creditor_type ?? null), d.terms && d.terms !== "bilinmiyor" ? (KOSUL_KISA[d.terms] ?? kosulEtiketi(d.terms, yon)) : null,
    tarih && d.due_date && !d.schedule && d.strict != null ? (d.strict ? "Kesin tarih" : "Söz") : null];
  return parts.filter((x): x is string => !!x);
}

/** Borç satırında kısa özet: "Mağaza / senet · Tek seferde ödenecek · Kesin tarih". */
export function kosulOzeti(d: Parameters<typeof kosulRozetleri>[0]): string {
  return kosulRozetleri(d).join(" · ");
}
