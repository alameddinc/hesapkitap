// Borcun koşulları (yetenek debt_terms; sunucu app/debt_schedule.py terms_of / strict_of): kime (alacaklı türü), nasıl
// ödenecek (ödeme koşulu) ve tarih türü (kesin / söz). Yalnız "borcum"da. Mobil src/lib/borcKosul.ts aynı sözler.
// Borç planı bunlara göre sıralar; hiçbir metin faizli bir yol önermez.

export type CreditorType = "kisi" | "vergi_sgk" | "banka" | "magaza" | "diger";
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

export const alacakliEtiketi = (c: CreditorType | null | undefined) => ALACAKLI.find((x) => x.value === c)?.label ?? null;
export const kosulEtiketi = (t: PayTerms | null | undefined) => KOSULLAR.find((x) => x.value === t)?.label ?? null;

/** Sunucunun varsayılanı: taksitli borçta ve kurumda kesin, kişide (ve türü boş eski kayıtta) söz. */
export const varsayilanKesin = (c: CreditorType | null | undefined, terms: PayTerms) =>
  terms === "taksitli" || (!!c && c !== "kisi");

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

/** Borç satırında kısa özet: "Mağaza / senet · Tek seferde ödenecek · Kesin tarih". */
export function kosulOzeti(d: { creditor_type?: CreditorType | null; terms?: PayTerms | null; strict?: boolean | null;
  due_date?: string | null; schedule?: unknown }): string {
  const parts = [alacakliEtiketi(d.creditor_type ?? null), d.terms && d.terms !== "bilinmiyor" ? kosulEtiketi(d.terms) : null,
    d.due_date && !d.schedule && d.strict != null ? (d.strict ? "Kesin tarih" : "Söz") : null];
  return parts.filter(Boolean).join(" · ");
}
