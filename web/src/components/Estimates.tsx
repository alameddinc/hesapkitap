import { ChevronDown, ChevronRight, CreditCard, Repeat, Upload } from "lucide-react";
import { useState } from "react";
import type { EstimateItem, Row, Summary } from "../api";
import { cx, dateTR, monthLabel, tl } from "../format";
import { useUI } from "../ui";

// Tahmini satırlar (ekstresiz kart ödemesi, dökümsüz maaş) gerçek değildir: hiçbir toplama girmez, ayrı gösterilir.
// Sunucu bunları summary.estimated_items / totals.estimated_* ve /api/transactions?include_estimates=1 ile verir.

/** Tahmini satır mı? (eski sunucu varsayılan listede source "estimate" ile veriyordu) */
export const isEstimate = (r: Row) => !!r.estimated || r.source === "estimate";

/** Listeyi gerçek ve tahmini olarak ayır. */
export function splitEstimates(rows: Row[]): [Row[], Row[]] {
  const real: Row[] = [], est: Row[] = [];
  for (const r of rows) (isEstimate(r) ? est : real).push(r);
  return [real, est];
}

const monthName = (ym: string) => monthLabel(ym).split(" ")[0];

/** "3 kartın Ekim ekstresi yok" — tek beklenen dönem varsa ay adıyla, değilse sade. */
export function missingCardsText(items: EstimateItem[]): string | null {
  const cards = items.filter((i) => i.source === "card_payment" && i.kind === "gider");
  if (!cards.length) return null;
  const n = new Set(cards.map((c) => c.card_last4 ?? c.title)).size;
  const periods = [...new Set(cards.map((c) => c.expected_period).filter((p): p is string => !!p))];
  return `${n} kartın ${periods.length === 1 ? `${monthName(periods[0])} ekstresi` : "ekstresi"} yok`;
}

/** Özet'te "harcadığın" rakamının altındaki not: tahmin ayrı, gidere katılmadı. Alan yoksa (eski sunucu) hiçbir şey. */
export function EstimateNote({ summary, tone = "plain" }: { summary: Summary; tone?: "plain" | "hero" }) {
  const ui = useUI();
  const items = summary.estimated_items ?? [];
  const cardText = missingCardsText(items);
  const est = summary.totals.estimated_expense ?? 0;
  const salary = items.filter((i) => i.source === "recurring" && i.kind === "gelir");
  const salaryTotal = salary.reduce((a, i) => a + i.amount, 0);
  if (!(cardText && est > 0) && !salaryTotal) return null;
  const hero = tone === "hero";
  return (
    <div className={cx("mt-3 rounded-xl border border-dashed px-3 py-2 text-xs",
      hero ? "border-white/35 bg-white/[0.07] text-blue-50" : "border-stone-300 bg-stone-50/70 text-stone-600 dark:border-stone-700 dark:bg-stone-800/40 dark:text-stone-400")}
      data-testid="estimate-note">
      {cardText && est > 0 && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="min-w-0 flex-1">{cardText} · tahmini <b className="font-semibold tabular-nums">{tl(est)}</b>
            <span className={hero ? "text-blue-100/80" : "text-stone-500"}> (harcamaya katılmadı)</span></span>
          <button onClick={() => ui.go("ekstreler")}
            className={cx("inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 font-medium",
              hero ? "bg-white/90 text-blue-700 active:bg-white" : "bg-blue-600 text-white active:bg-blue-700")}>
            <Upload size={12} /> Ekstreyi yükle
          </button>
        </div>
      )}
      {salaryTotal > 0 && (
        <div className={cx(cardText && est > 0 && "mt-1.5")}>
          Maaş tahmini <b className="font-semibold tabular-nums">{tl(salaryTotal)}</b> · döküm yok
          <span className={hero ? "text-blue-100/80" : "text-stone-500"}> (gelire katılmadı)</span>
        </div>
      )}
    </div>
  );
}

/** İşlemler listesinin başında katlanır bölüm: tahmini satırlar soluk, kesikli kenarlı, toplamı ayrı. */
export function EstimateSection({ rows }: { rows: Row[] }) {
  const ui = useUI();
  const [open, setOpen] = useState(false);
  if (!rows.length) return null;
  const expense = rows.filter((r) => r.kind !== "gelir").reduce((a, r) => a + r.amount, 0);
  const income = rows.filter((r) => r.kind === "gelir").reduce((a, r) => a + r.amount, 0);
  const onlyCards = rows.every((r) => r.kind !== "gelir");
  return (
    <section className="rounded-xl border border-dashed border-stone-300 bg-stone-50/60 dark:border-stone-700 dark:bg-stone-900/40" data-testid="estimate-section">
      <button className="flex w-full items-center gap-2 px-3 py-2.5 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronDown size={16} className={cx("shrink-0 text-stone-400 transition-transform", !open && "-rotate-90")} />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-stone-700 dark:text-stone-300">
            Tahmini · {onlyCards ? "ekstre" : "ekstre/döküm"} bekleniyor <span className="text-stone-500">({rows.length})</span></span>
          <span className="block text-xs text-stone-500">Harcama toplamına girmez · belge yüklenince gerçek işlemler gelir</span>
        </span>
        <span className="shrink-0 text-right text-sm tabular-nums text-stone-600 dark:text-stone-400">
          {expense > 0 && <span className="block">~{tl(expense)}</span>}
          {income > 0 && <span className="block text-xs text-green-700/80 dark:text-green-400/80">+~{tl(income)}</span>}
        </span>
      </button>
      {open && (
        <ul className="space-y-1.5 px-2 pb-2">
          {rows.map((r) => (
            <li key={r.uid}>
              <button onClick={() => ui.go("ekstreler")}
                className="flex w-full items-center gap-3 rounded-lg border border-dashed border-stone-300 bg-white/50 px-3 py-2 text-left opacity-80 hover:opacity-100 dark:border-stone-700 dark:bg-stone-900/30">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-dashed border-stone-300 text-stone-400 dark:border-stone-600">
                  {r.kind === "gelir" ? <Repeat size={14} /> : <CreditCard size={14} />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium text-stone-600 dark:text-stone-300">{r.kind === "gelir" ? r.merchant : (r.card ?? r.merchant)}</span>
                    <span className={cx("shrink-0 text-sm tabular-nums text-stone-500", r.kind === "gelir" && "text-green-700/80 dark:text-green-400/80")}>
                      {r.kind === "gelir" ? "+" : ""}~{tl(r.amount)}</span>
                  </span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-stone-500">
                    <span className="rounded border border-dashed border-stone-400 px-1.5 text-[11px] leading-4 dark:border-stone-600">tahmini</span>
                    <span>{r.kind === "gelir" ? `${dateTR(r.date).slice(0, 5)} · döküm yok`
                      : `${dateTR(r.date).slice(0, 5)} ödendi · ${r.expected_period ? `${monthName(r.expected_period)} ekstresi bekleniyor` : "ekstre bekleniyor"}`}</span>
                  </span>
                </span>
                <ChevronRight size={14} className="hidden shrink-0 text-stone-300 sm:block dark:text-stone-600" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
