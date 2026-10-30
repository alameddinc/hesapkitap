import { useState } from "react";
import { cx, monthLabel, tl } from "../format";

const SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
const short = (ym: string) => SHORT[Number(ym.slice(5, 7)) - 1];
const full = (ym: string) => `${monthLabel(ym)}`;

/**
 * Tek seri aylık çubuk grafik (son 12 ay): her çubuk bir düğme — üzerine gelince / odaklanınca / dokununca o ayın tutarı
 * üstte yazar. Kesikli çizgi: ödeme olan ayların ortalaması. Renk tek seri (--series-1), metin mürekkep renginde.
 */
export default function MonthBars({ data, average, label }: {
  data: { month: string; amount: number }[];
  average?: number;
  label: string;
}) {
  const lastPaid = [...data].reverse().find((d) => d.amount > 0)?.month ?? null;
  const [pick, setPick] = useState<string | null>(lastPaid);
  const [hover, setHover] = useState<string | null>(null);
  const shown = hover ?? pick;
  const cur = data.find((d) => d.month === shown);
  const max = Math.max(1, ...data.map((d) => d.amount));
  const H = 140;
  const avgY = average && average > 0 ? (average / max) * H : null;
  return (
    <figure className="space-y-2" aria-label={label}>
      <figcaption className="min-h-5 text-sm tabular-nums" aria-live="polite">
        {cur ? <><b>{full(cur.month)}</b>: {cur.amount > 0 ? tl(cur.amount) : "ödeme yok"}</> : "Bir aya dokun"}
      </figcaption>
      <div className="relative" style={{ height: H }}>
        <div className="absolute inset-x-0 bottom-0 border-t border-stone-200 dark:border-stone-700" aria-hidden />
        {avgY != null && (
          <div className="absolute inset-x-0 border-t border-dashed border-stone-400 dark:border-stone-500" style={{ bottom: avgY }} aria-hidden />
        )}
        <div className="absolute inset-0 flex items-end gap-0.5">
          {data.map((d) => {
            const h = d.amount > 0 ? Math.max(3, (d.amount / max) * H) : 0;
            const on = shown === d.month;
            return (
              <button key={d.month} type="button" className="group flex h-full min-w-0 flex-1 items-end justify-center rounded-sm outline-offset-2"
                aria-label={`${full(d.month)}: ${d.amount > 0 ? tl(d.amount) : "ödeme yok"}`} aria-pressed={pick === d.month}
                onClick={() => setPick(d.month)} onMouseEnter={() => setHover(d.month)} onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(d.month)} onBlur={() => setHover(null)}>
                <span className={cx("block w-full max-w-9 rounded-t-[4px] transition-opacity", on ? "opacity-100" : "opacity-60 group-hover:opacity-90")}
                  style={{ height: h, background: "var(--series-1)" }} />
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex gap-0.5" aria-hidden>
        {data.map((d) => (
          <span key={d.month} className={cx("min-w-0 flex-1 text-center text-[10px] leading-none",
            shown === d.month ? "font-semibold text-stone-900 dark:text-stone-100" : "text-stone-500 dark:text-stone-400")}>
            {short(d.month)}
          </span>
        ))}
      </div>
      {avgY != null && <p className="text-xs text-stone-500 dark:text-stone-400">Kesikli çizgi: ortalama {tl(average!)}</p>}
    </figure>
  );
}
