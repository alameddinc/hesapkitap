import { AlertOctagon, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import type { Insight } from "../api";
import { cx, maskText } from "../format";

const STYLE = {
  kritik: { icon: AlertOctagon, cls: "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/40", ic: "text-red-600", label: "Kritik" },
  uyari: { icon: AlertTriangle, cls: "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40", ic: "text-amber-600", label: "Dikkat" },
  bilgi: { icon: Info, cls: "border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900", ic: "text-blue-600", label: "Bilgi" },
  iyi: { icon: CheckCircle2, cls: "border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/40", ic: "text-green-600", label: "İyi haber" },
};

export default function Insights({ items, onClick }: { items: Insight[]; onClick?: (i: Insight) => void }) {
  if (!items.length) return null;
  return (
    <ul className="grid gap-2 md:grid-cols-2">
      {items.map((i, n) => {
        const s = STYLE[i.level];
        const clickable = !!(i.filter || i.link) && onClick;
        return (
          <li key={n}>
            <button disabled={!clickable} onClick={() => onClick?.(i)}
              className={cx("flex h-full w-full gap-3 rounded-xl border p-3 text-left", s.cls, clickable && "hover:shadow-sm")}>
              <s.icon size={18} className={cx("mt-0.5 shrink-0", s.ic)} aria-label={s.label} />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{maskText(i.title)}</span>
                <span className="mt-0.5 line-clamp-2 text-xs text-stone-600 sm:line-clamp-none dark:text-stone-400">{maskText(i.detail)}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
