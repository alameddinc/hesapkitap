import { cx, tl } from "../format";

export default function Kpi({ label, value, sub, tone }: { label: string; value: number; sub?: string; tone?: "red" | "green" }) {
  return (
    <div className="card p-3 sm:p-4">
      <div className="text-xs text-stone-500">{label}</div>
      <div className={cx("mt-1 text-lg font-semibold tabular-nums sm:text-xl",
        tone === "red" && "text-red-600 dark:text-red-400", tone === "green" && "text-green-700 dark:text-green-400")}>
        {tl(value)}
      </div>
      {sub && <div className="mt-0.5 text-xs text-stone-500">{sub}</div>}
    </div>
  );
}
