import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, Check, CheckCircle2, ChevronRight, Info, ListFilter } from "lucide-react";
import { api, type Insight } from "../api";
import { cx, maskText } from "../format";
import { useUI, type Route } from "../ui";
import { routeVisible, useBook, useMeta } from "./hooks";

const STYLE = {
  kritik: { icon: AlertOctagon, cls: "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/40", ic: "text-red-600 dark:text-red-400", label: "Kritik" },
  uyari: { icon: AlertTriangle, cls: "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40", ic: "text-amber-600 dark:text-amber-400", label: "Dikkat" },
  bilgi: { icon: Info, cls: "border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900", ic: "text-blue-600 dark:text-blue-400", label: "Bilgi" },
  iyi: { icon: CheckCircle2, cls: "border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/40", ic: "text-green-600 dark:text-green-400", label: "İyi haber" },
};

/** Uyarının götürdüğü sayfanın düğme adı. */
const LINK_LABEL: Partial<Record<Route, string>> = {
  borclar: "Borç / Alacak", plan: "Planlama", ekstreler: "Ekstreler", tasarruf: "Tasarruf", zekat: "Zekât",
  hesaplar: "Hesaplar", fiyatlar: "Fiyatlar", transferler: "Transferler", istatistik: "İstatistik", islemler: "İşlemler",
};

/**
 * Özet uyarıları. Her eylem ayrı, görünür bir düğme (kartın tamamı tıklanmaz):
 * - pay: gecikmiş / kısmi ödenmiş ekstre, fatura ya da borç → "Ödedim" (tutar, tarih, hesap)
 * - link: ilgili sayfa (kapalıysa düğme gösterilmez)
 * - filter: bu dönemin ilgili işlemleri (aşağıda açılır)
 * Metin hiç kesilmez: anlamı çoğu zaman son cümlede ("Ödediysen 'Ödedim' ile işaretle").
 */
export default function Insights({ items, active, onFilter }: {
  items: Insight[];
  active?: Insight | null;
  onFilter?: (i: Insight) => void;
}) {
  const ui = useUI();
  const book = useBook().data;
  const meta = useMeta().data;
  const needPay = items.some((i) => i.pay);
  const pay = useQuery({ queryKey: ["payables"], queryFn: api.payables, enabled: needPay });
  if (!items.length) return null;

  const openPay = (i: Insight) => {
    const p = pay.data?.find((x) => x.type === i.pay!.type && x.id === i.pay!.id);
    if (p) ui.openPay(p);
    else ui.toast(pay.isLoading ? "Ödemeler yükleniyor, birazdan tekrar dene" : "Bu ödeme artık listede yok");
  };

  return (
    <ul className="grid gap-2 md:grid-cols-2">
      {items.map((i, n) => {
        const s = STYLE[i.level];
        const link = i.link && LINK_LABEL[i.link as Route] && routeVisible(book, i.link, meta?.modules) ? (i.link as Route) : null;
        const filter = !!i.filter && !!onFilter;
        const on = !!active && active.title === i.title;
        return (
          <li key={n} className={cx("flex h-full gap-3 rounded-xl border p-3", s.cls)}>
            <s.icon size={18} className={cx("mt-0.5 shrink-0", s.ic)} aria-label={s.label} />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="block text-sm font-medium">{maskText(i.title)}</span>
              <span className="mt-0.5 text-xs text-stone-600 dark:text-stone-400">{maskText(i.detail)}</span>
              {(i.pay || link || filter) && (
                <div className="mt-auto flex flex-wrap gap-2 pt-2">
                  {i.pay && (
                    <button className="btn-primary px-3 py-1 text-xs" onClick={() => openPay(i)}>
                      <Check size={14} aria-hidden /> Ödedim
                    </button>
                  )}
                  {link && (
                    <button className="btn border border-stone-300 bg-white/70 px-3 py-1 text-xs text-stone-700 hover:bg-white dark:border-stone-700 dark:bg-stone-900/60 dark:text-stone-200 dark:hover:bg-stone-800"
                      onClick={() => ui.go(link)}>
                      {LINK_LABEL[link]} <ChevronRight size={14} aria-hidden />
                    </button>
                  )}
                  {filter && (
                    <button aria-pressed={on} onClick={() => onFilter!(i)}
                      className={cx("btn border px-3 py-1 text-xs",
                        on ? "border-blue-600 bg-blue-600 text-white hover:bg-blue-700"
                          : "border-stone-300 bg-white/70 text-stone-700 hover:bg-white dark:border-stone-700 dark:bg-stone-900/60 dark:text-stone-200 dark:hover:bg-stone-800")}>
                      <ListFilter size={14} aria-hidden /> {on ? "İşlemleri gizle" : "İşlemleri göster"}
                    </button>
                  )}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
