// Kayıttan sonra "nereye gitti?" (UX denetimi 09.11, Bulgu 12; mobil lib/kayitYeri.ts ile aynı kural ve metin):
// "Kaydedildi · Kasım dönemi" + [İşlemler'de gör] → o dönem açılır. Tarihin dönemi sunucuyla aynı kural (format.periodOf).
import { monthLabel, periodOf } from "../format.ts";

/** Dönem adı: "Kasım"; şimdiki dönemden başka yıldaysa "Aralık 2025" (mobil donemAdi). */
export function donemAdi(month: string, current?: string | null): string {
  const full = monthLabel(month); // "Kasım 2026"
  return current && month.slice(0, 4) !== current.slice(0, 4) ? full : full.replace(/\s\d{4}$/, "");
}

/** Kaydın düştüğü dönem ve adı. date YYYY-AA-GG; startDay ay dönümü günü (meta.cycle_start_day). */
export function kayitDonemi(date: string, startDay: number, current?: string | null): { month: string; ad: string } {
  const month = periodOf(date.slice(0, 10), startDay || 1);
  return { month, ad: donemAdi(month, current) };
}

/** Bildirim metni: "Kaydedildi · Kasım dönemi". */
export const kaydedildiMetni = (ad: string) => `Kaydedildi · ${ad} dönemi`;
