// Kayıttan sonra "nereye gitti?" (UX denetimi 09.11, Bulgu 12; mobil lib/kayitYeri.ts ile aynı kural ve metin):
// "Kaydedildi · Kasım dönemi" + [İşlemler'de gör] → o dönem açılır. Tarihin dönemi sunucuyla aynı kural (format.periodOf).
import { periodLabel, periodNameMonth, periodOf } from "../format.ts";

/** Dönem adı (başladığı aydan, format.periodLabel): "Kasım"; şimdiki dönemden başka yıldaysa "Aralık 2025" (mobil donemAdi).
 *  startDay verilmezse format'ın bildiği ay dönümü günü kullanılır. */
export function donemAdi(month: string, current?: string | null, startDay?: number): string {
  const full = periodLabel(month, false, startDay); // "Kasım 2026"
  const yil = (ym: string) => periodNameMonth(ym, startDay).slice(0, 4);
  return current && yil(month) !== yil(current) ? full : full.replace(/\s\d{4}$/, "");
}

/** Kaydın düştüğü dönem ve adı. date YYYY-AA-GG; startDay ay dönümü günü (meta.cycle_start_day). */
export function kayitDonemi(date: string, startDay: number, current?: string | null): { month: string; ad: string } {
  const month = periodOf(date.slice(0, 10), startDay || 1);
  return { month, ad: donemAdi(month, current, startDay || 1) };
}

/** Bildirim metni: "Kaydedildi · Kasım dönemi". */
export const kaydedildiMetni = (ad: string) => `Kaydedildi · ${ad} dönemi`;
