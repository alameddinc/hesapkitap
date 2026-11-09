import type { ReactNode } from "react";
import { cx, tl } from "../format";

export default function Kpi({ label, value, sub, tone }: { label: ReactNode; value: number; sub?: string; tone?: "red" | "green" }) {
  return (
    <div className="card p-3 sm:p-4">
      <div className="text-xs text-stone-500">{label}</div>
      <div className={cx("mt-1 text-lg font-semibold tabular-nums sm:text-xl",
        tone === "red" && "text-red-600 dark:text-red-400", tone === "green" && "text-green-700 dark:text-green-400")}>
        {tl(value)}
      </div>
      {sub && <div className="mt-0.5 text-xs [overflow-wrap:break-word] text-stone-500 dark:text-stone-400">{sub}</div>}
    </div>
  );
}
