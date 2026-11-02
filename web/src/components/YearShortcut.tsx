import { CalendarRange, ChevronRight, X } from "lucide-react";
import { useState } from "react";

/** Mevsimlik yıl özeti kısayolunun yılı: Aralık 18–31 o yıl, Ocak 1–14 geçen yıl; başka zaman null (mobil Ozet ile aynı). */
export function mevsimYili(d = new Date()): number | null {
  const ay = d.getMonth(), gun = d.getDate();
  if (ay === 11 && gun >= 18) return d.getFullYear();
  if (ay === 0 && gun <= 14) return d.getFullYear() - 1;
  return null;
}
const key = (y: number) => `hk-yil-ozeti-kapat-${y}`;

/** İstatistik'teki yıl özetine git (yıl seçili, bölüme kaydırılır). */
export const yilOzetineGit = (year?: number) => {
  window.location.hash = `/istatistik${year ? `?yil=${year}` : ""}`;
  setTimeout(() => document.getElementById("yil-ozeti")?.scrollIntoView({ behavior: "smooth", block: "start" }), 400);
};

/** Özet: hero altında "2026 yılın nasıl geçti?" (kapatınca o yıl için çıkmaz; tercih bu tarayıcıda). */
export default function YearShortcut() {
  const year = mevsimYili();
  const [gone, setGone] = useState(() => { try { return year == null || localStorage.getItem(key(year)) === "1"; } catch { return false; } });
  if (year == null || gone) return null;
  const close = () => { try { localStorage.setItem(key(year), "1"); } catch { /* özel mod */ } setGone(true); };
  return (
    <section className="card flex items-center gap-3 p-3" data-testid="yil-ozeti-kisayol">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300"><CalendarRange size={18} aria-hidden /></span>
      <button className="min-w-0 flex-1 text-left" onClick={() => yilOzetineGit(year)}>
        <span className="block text-sm font-medium">{year} yılın nasıl geçti?</span>
        <span className="inline-flex items-center text-sm font-medium text-blue-700 dark:text-blue-400">Yıl özeti <ChevronRight size={14} aria-hidden /></span>
      </button>
      <button className="btn-ghost p-2" aria-label="Yıl özeti kısayolunu kapat" onClick={close}><X size={16} aria-hidden /></button>
    </section>
  );
}
