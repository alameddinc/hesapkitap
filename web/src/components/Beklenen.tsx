// Özet → "Dönem sonu" kartı ve Planlama ayının dökümü (mobil components/ozet/Beklenen.tsx ile aynı metinler;
// lib/beklenen.ts). Rakamlar sunucudan (summary.expected = Planlama'nın o ayki satırı); geçmiş dönemde expected null,
// eski sunucuda forecast_net yok → kart görünmez.
import { CalendarRange, ChevronRight } from "lucide-react";
import type { Summary } from "../api";
import { areAmountsHidden, cx, tl } from "../format";
import { DONEM_SONU, donemSonuAlt, donemSonuDokum, donemSonuVar, eksiMi, isaret, isaretli, tamTl, type DonemSonu } from "../lib/beklenen";
import { planOdakla } from "../lib/planOdak";
import { TERIM } from "../lib/terimler";
import { useUI } from "../ui";
import { routeVisible, useBook, useMeta } from "./hooks";
import TerimBilgi from "./TerimBilgi";

/** tam lira; tutarlar gizliyse maske */
const para = (n: number) => (areAmountsHidden() ? tl(n) : tamTl(n));
const renk = (n: number) => (eksiMi(n) ? "text-red-600 dark:text-red-400" : "text-green-700 dark:text-green-400");

/** "Dönem sonu (i) · +₺25.000 her zamanki harcamanla · Yeni harcama yapmazsan +₺70.804" → dokununca Planlama (o ay). */
export function DonemSonuKarti({ summary }: { summary: Summary }) {
  const ui = useUI();
  const meta = useMeta().data;
  const book = useBook().data;
  const e = summary.expected;
  if (!donemSonuVar(e)) return null;
  const plan = routeVisible(book, "plan", meta?.modules);
  const body = (
    <>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300">
        <CalendarRange size={16} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">{DONEM_SONU.baslik}</span>
          <span className="tabular-nums"><b className={renk(e.forecast_net)} data-testid="donem-sonu-tahmin">{isaretli(e.forecast_net, para)}</b>
            <span className="whitespace-nowrap text-stone-500"> · {DONEM_SONU.her.toLocaleLowerCase("tr")}</span></span>
        </span>
        <span className="block text-xs text-stone-500 tabular-nums" data-testid="donem-sonu-alt">{donemSonuAlt(e, para)}</span>
      </span>
    </>
  );
  return (
    <div data-testid="donem-sonu" className="card relative flex w-full items-center gap-3 p-3 text-left text-sm">
      {/* kartın tamamı Planlama'ya gider; (i) üstte ayrı düğme (iç içe düğme yok) */}
      {plan ? (
        <button type="button" aria-label={`${DONEM_SONU.baslik}: ${DONEM_SONU.planlama}`} className="absolute inset-0 rounded-[inherit] active:bg-stone-50 dark:active:bg-stone-800"
          onClick={() => { planOdakla(e.month); ui.go("plan"); }} />
      ) : null}
      <span className="pointer-events-none relative flex min-w-0 flex-1 items-center gap-3">{body}</span>
      <TerimBilgi id="donemSonu" className="relative" />
      {plan && <ChevronRight size={16} className="pointer-events-none relative shrink-0 text-stone-400" />}
    </div>
  );
}

/** Planlama ayının üstünde (kuruşlu, Planlama'nın biçimi): Gelir ve Kesin gider (kayıtlı + bekleyenler) → yeni harcama
 *  yapmazsan → tahmini kart harcaması → her zamanki harcamanla. */
export function DonemSonuDokum({ d, className }: { d: DonemSonu; className?: string }) {
  const rows = donemSonuDokum(d);
  return (
    <div data-testid="donem-sonu-dokum" className={cx("space-y-1 text-sm", className)}>
      <div className="flex items-center gap-1 text-xs font-medium tracking-wide text-stone-500 uppercase">
        {TERIM.donemSonu.ad} <TerimBilgi id="donemSonu" />
      </div>
      {rows.map((r) => (
        <div key={r.key} data-testid={`dokum-${r.key}`}
          className={cx("flex items-baseline justify-between gap-3", r.total && "border-t border-stone-200 pt-1 dark:border-stone-700",
            r.level === 1 && "pl-4 text-xs", r.total === "son" && "text-base font-semibold", r.total === "ara" && "font-medium")}>
          <span className={cx("min-w-0", r.level === 1 && "text-stone-500 dark:text-stone-400")}>{r.label}</span>
          <span className={cx("shrink-0 whitespace-nowrap tabular-nums", r.total ? renk(r.amount)
            : r.level === 1 ? "text-stone-500 dark:text-stone-400" : r.amount > 0 && "text-green-700 dark:text-green-400")}>
            {isaret(r.amount)}{tl(Math.abs(r.amount))}</span>
        </div>
      ))}
    </div>
  );
}
