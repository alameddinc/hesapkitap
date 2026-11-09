// Kayıttan sonra nereye gitti (UX denetimi 09.11, Bulgu 12; mobil ile aynı metin): "Kaydedildi · Kasım dönemi" +
// [İşlemler'de gör] → o dönem açılır.
import { useCallback } from "react";
import { DURUM } from "../lib/durum";
import { kayitDonemi } from "../lib/kayitYeri";
import { useUI } from "../ui";
import { useMeta } from "./hooks";

/** msg "Kaydedildi…" ile başlıyorsa dönemi ekler ve "İşlemler'de gör" düğmesi koyar; değilse olduğu gibi gösterir. */
export function useKayitBildir() {
  const ui = useUI();
  const meta = useMeta().data;
  return useCallback((msg: string, date: string | null | undefined) => {
    if (!date || !/^Kaydedildi/.test(msg)) return ui.toast(msg);
    const d = kayitDonemi(date, meta?.cycle_start_day ?? 1, meta?.current_month);
    const rest = msg.replace(/^Kaydedildi/, "");
    ui.toast(`${DURUM.kaydedildi(d.ad)}${rest}`, { action: { label: DURUM.gor, run: () => { ui.setMonth(d.month); ui.go("islemler"); } } });
  }, [ui, meta?.cycle_start_day, meta?.current_month]);
}
