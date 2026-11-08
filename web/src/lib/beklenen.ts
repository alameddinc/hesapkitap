// Özet → "Beklenen" (sunucu yeteneği "summary_expected": /api/summary expected + definitions.expected). Metinler web ve
// mobilde BİREBİR aynı: creditcardekstre web/src/lib/beklenen.ts ile hesapkitap-mobile src/lib/beklenen.ts aynı dosyadır
// (biri değişirse öteki de). Rakamlar sunucudan; burada toplama yok.
//
// Anlam (sunucu finance.expected): Planlama'nın o ayki "kesin" satırı. Gider = gerçekleşen gider + ekstresi gelmemiş
// kart taksitleri (ve o kartların abonelikleri) + girilmemiş sabit giderler + ödenmemiş faturalar; Gelir = gerçekleşen
// gelir + bekleyen gelir. Yeni (değişken) harcama tahmini eklenmez. Geçmiş dönemde expected null → satır yok.

export interface BeklenenParcalar {
  recorded_expense: number;
  pending_installments: number;
  pending_subscriptions: number;
  pending_recurring_expense: number;
  pending_bills: number;
  pending_expense: number;
  recorded_income: number;
  pending_income: number;
}

export interface Beklenen {
  month: string;
  source: "plan";
  expense: number;
  income: number;
  net: number;
  parts: BeklenenParcalar;
}

const KURUS = 0.005;
/** işaret ile tutar arasında görünmez "kelime birleştirici": "+" / "₺77.921" ayrı satıra düşmesin */
const WJ = "\u2060";


/** Tam lira, kuruşsuz: "₺137.166" (eksi "−₺", arada WJ). Gizleme çağıranın para işlevinde. */
export const tamTl = (n: number) =>
  (n < 0 ? `−${WJ}₺` : "₺") + new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 }).format(Math.abs(Math.round(n)));

/** Bekleyen bir şey var mı (gelir ya da gider): yoksa Özet'te hiçbir "Beklenen" satırı çıkmaz. */
export const beklenenVar = (e: Beklenen | null | undefined): e is Beklenen =>
  !!e && (e.parts.pending_expense > KURUS || e.parts.pending_income > KURUS);

/** Gider kartının ikinci satırı: "Ekstre gelince ~₺137.166" (yalnız kart ekstresi bekleniyorsa bu söz; başka bekleyen
 *  gider de varsa "Beklenenlerle ~…"). Bekleyen gider yoksa null. para: tam lira biçimi (gizliyse ₺•••). */
export function beklenenGider(e: Beklenen | null | undefined, para: (n: number) => string): string | null {
  if (!e || e.parts.pending_expense <= KURUS) return null;
  const kart = e.parts.pending_installments + e.parts.pending_subscriptions;
  const diger = e.parts.pending_recurring_expense + e.parts.pending_bills;
  return `${kart > KURUS && diger <= KURUS ? "Ekstre gelince" : "Beklenenlerle"} ~${para(e.expense)}`;
}

/** Artan kartının alt satırı: "Beklenen: +₺77.921" (eksi: "Beklenen: −₺12.400"). Bekleyen yoksa null. */
export function beklenenArtan(e: Beklenen | null | undefined, para: (n: number) => string): string | null {
  if (!beklenenVar(e)) return null;
  return `Beklenen: ${e.net > KURUS ? `+${WJ}` : ""}${para(e.net)}`;
}

/** Artan rengi: eksiyse kırmızı (Artan ile aynı kural). */
export const beklenenEksi = (e: Beklenen) => e.net < -KURUS;

/** "Planlamada gör" bağlantısının yazısı (iki istemcide aynı). */
export const BEKLENEN_PLANLAMA = "Planlamada gör";
