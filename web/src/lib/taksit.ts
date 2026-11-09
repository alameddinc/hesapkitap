// Özet → "Bu ay ödenecek taksitler" (sunucu yeteneği "summary_installments": /api/summary installments,
// GET /api/installments). Metinler web ve mobilde BİREBİR aynı: creditcardekstre web/src/lib/taksit.ts ile
// hesapkitap-mobile src/lib/taksit.ts aynı dosyadır (biri değişirse öteki de). Rakamlar sunucudan; burada toplama yok.
//
// Anlam (sunucu finance.installments_due): dönemin harcaması bu dönemin taksit dilimlerini ZATEN içerir; bu satır
// harcamaya eklenmez, kırılımdır. in_spend = harcamaya dahil taksitler, expected = kartın bu döneme ait ekstresi henüz
// gelmemiş, son ekstreden bilinen taksitler (ekstre gelince harcamaya girer), remaining = sonraki aylara kalan taksitler.

export interface TaksitOzet {
  month: string;
  /** içinde bulunulan dönem mi ("Bu ay ödenecek" / kalan taksit borcu yalnız bu dönemde) */
  current: boolean;
  total: number;
  /** alışveriş taksiti sayısı (vade farkı/vergi taksitleri ve iadeler sayılmaz) */
  count: number;
  in_spend: number;
  expected: number;
  remaining: number;
  remaining_count: number;
}

export interface TaksitKalem {
  uid: string;
  source: "card" | "manual" | "projected";
  title: string;
  description: string;
  card: string | null;
  installment: string;
  no: number;
  of: number;
  amount: number;
  in_spend: boolean;
  /** vade farkı, BSMV, KKDF taksiti */
  fee: boolean;
  due_date: string | null;
  /** ortak defterde: kim harcadı */
  spender?: string;
  spender_name?: string;
}

const AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const AY_KISA = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];

const kisaTarih = (iso: string) => {
  const [, m, d] = iso.slice(0, 10).split("-").map(Number);
  return m && d ? `${d}\u00a0${AY_KISA[m - 1]}` : iso;
};

/** Satır görünsün mü: dönemde taksit var ya da (bu dönemde) kalan taksit borcu var. Eski sunucu: alan yok → hayır. */
export const taksitGoster = (o: TaksitOzet | undefined | null): o is TaksitOzet =>
  !!o && (o.total !== 0 || (o.current && o.remaining > 0));

/** "Bu ay ödenecek taksitler" · geçmiş/ileri dönemde "Ekim dönemi taksitleri". */
export const taksitBaslik = (o: TaksitOzet) =>
  o.current ? "Bu ay ödenecek taksitler" : `${AY[Number(o.month.slice(5, 7)) - 1]} dönemi taksitleri`;

/** "6 taksit" (yalnız iade/vergi taksiti varsa boş). Sayı ile kelime arasında bölünmeyen boşluk (dar ekran). */
export const taksitSayi = (o: TaksitOzet) => (o.count > 0 ? `${o.count}\u00a0taksit` : "");

/** Alt satır: harcamaya dahil mi, ekstre bekleyen var mı. para: tutar biçimi (mobilde göz kapalıysa ₺•••). */
export function taksitDurum(o: TaksitOzet, para: (n: number) => string): string {
  if (o.expected === 0) return "Harcamana dahil";
  if (o.in_spend === 0) return "Ekstresi gelmedi · harcamana henüz dahil değil";
  return `${para(o.in_spend)} harcamana dahil · ${para(o.expected)} henüz gidere yazılmadı (ekstre bekleniyor)`;
}

/** Özet satırının kısa alt yazısı: "₺35.487 ekstre bekleniyor" (hepsi harcamadaysa "Harcamana dahil"). Uzun metin
 *  (taksitDurum) ve kalan taksit borcu listede. */
export const taksitKisaAlt = (o: TaksitOzet, para: (n: number) => string) =>
  o.expected === 0 ? "Harcamana dahil" : `${para(o.expected)} ekstre bekleniyor`;

/** "Kalan taksit borcu ₺18.400 · 7 alışveriş" — yalnız içinde bulunulan dönemde ve borç varsa. */
export function taksitKalan(o: TaksitOzet, para: (n: number) => string): string | null {
  if (!o.current || o.remaining <= 0) return null;
  return `Kalan taksit borcu ${para(o.remaining)}${o.remaining_count > 0 ? ` · ${o.remaining_count}\u00a0alışveriş` : ""}`;
}

/** Listede kalemin alt satırı: "VakıfBank …9012 · 3/6. taksit · son ödeme 26 Eki · ekstre bekliyor · Ayşe".
 *  Kim harcadı adı yalnız ortak defterde gelir (sunucu iki ve daha çok kişide spender_name verir). */
export function taksitKalemAlt(i: TaksitKalem): string {
  // bölünmeyen boşluk: dar ekranda "3/6." ile "taksit", "25" ile "Kas" ayrı satıra düşmesin
  const sira = i.fee ? `vade farkı/vergi · ${i.no}/${i.of}` : i.no === i.of ? `son\u00a0taksit\u00a0(${i.no}/${i.of})` : `${i.no}/${i.of}.\u00a0taksit`;
  return [
    i.card,
    sira,
    i.due_date ? `son ödeme ${kisaTarih(i.due_date)}` : null,
    i.in_spend ? null : "ekstre bekliyor",
    i.spender_name || null,
  ].filter(Boolean).join(" · ");
}

/** Listenin altındaki açıklama. */
export const TAKSIT_NOT = "Taksitler o ayın harcamasına zaten dahil; burada ayrıca gösterilir, iki kez sayılmaz. "
  + "Ekstresi gelmemiş kartın taksitleri son ekstreden bilinir; ekstre gelince harcamana eklenir.";
