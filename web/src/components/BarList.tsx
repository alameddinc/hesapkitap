import { ChevronRight } from "lucide-react";
import type { Group } from "../api";
import { cx, tl } from "../format";

/** Bir satır: id verilirse onSelect onu alır (görünen ad "Kira & Aidat · %16" gibi süslü olabilir). */
export type BarItem = Group & { id?: string };

/** Yatay çubuk liste. Grafik + tablo aynı anda: değerler hep yazılı.
 *  onSelect yoksa satırlar düz metin (">" yok: tıklanınca bir şey olmayan satır ok göstermesin).
 *  selected verilirse satır aynı sayfada ayrıntı açar/kapatır (aria-expanded, ok aşağı döner); verilmezse başka yere götürür.
 *  label: ekran okuyucu için satırın ne yaptığı ("Market işlemlerini aç"). */
export default function BarList({ items, selected, onSelect, limit, label }: {
  items: BarItem[];
  selected?: string | null;
  onSelect?: (name: string | null) => void;
  limit?: number;
  label?: (i: BarItem) => string;
}) {
  const shown = limit ? items.slice(0, limit) : items;
  if (!shown.length) {
    return <p className="py-3 text-sm text-stone-500">Bu dönemde henüz harcama yok. Kart harcamaları ekstre gelince, fiş ve elle girdiklerin hemen görünür.</p>;
  }
  const max = Math.max(...shown.map((i) => i.amount), 1);
  const toggle = selected !== undefined;
  return (
    <ul className="space-y-1">
      {shown.map((i) => {
        const key = i.id ?? i.name;
        const active = toggle && selected === key;
        const body = (
          <>
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="break-words">{i.name}</span>
              <span className="flex shrink-0 items-center gap-1 font-medium tabular-nums">
                {tl(i.amount)}
                {onSelect && <ChevronRight size={14} aria-hidden className={cx("text-stone-400 transition-transform", active && "rotate-90")} />}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2">
              <div className="h-1.5 flex-1 rounded-full bg-stone-100 dark:bg-stone-800">
                <div className="h-1.5 rounded-full" style={{ width: `${(i.amount / max) * 100}%`, background: "var(--series-1)" }} />
              </div>
              <span className="w-12 text-right text-xs text-stone-500">{i.count} işl.</span>
            </div>
          </>
        );
        return (
          <li key={key}>
            {onSelect ? (
              <button type="button" onClick={() => onSelect(toggle && active ? null : key)}
                aria-expanded={toggle ? active : undefined}
                aria-label={label ? `${label(i)}: ${tl(i.amount)}, ${i.count} işlem` : undefined}
                className={cx("group w-full rounded-lg px-2 py-1.5 text-left transition-colors",
                  active ? "bg-blue-50 ring-1 ring-blue-200 dark:bg-blue-950/40 dark:ring-blue-900" : "hover:bg-stone-50 dark:hover:bg-stone-800/50")}>
                {body}
              </button>
            ) : <div className="px-2 py-1.5">{body}</div>}
          </li>
        );
      })}
    </ul>
  );
}
