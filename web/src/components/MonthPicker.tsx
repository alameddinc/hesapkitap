import { ChevronLeft, ChevronRight } from "lucide-react";
import { addMonths, monthLabel } from "../format";
import { useMeta } from "./hooks";

const SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];

/** Ay dönümü 1 değilse dönem aralığını da gösterir: "Kasım 2026 · 17 Eki – 16 Kas". */
export function periodRange(month: string, startDay: number) {
  if (startDay <= 1) return null;
  const prev = addMonths(month, -1);
  const [, pm] = prev.split("-").map(Number);
  const [, m] = month.split("-").map(Number);
  return `${startDay} ${SHORT[pm - 1]} – ${startDay - 1} ${SHORT[m - 1]}`;
}

export default function MonthPicker({ value, onChange }: { value: string; onChange: (m: string) => void }) {
  const meta = useMeta();
  const range = periodRange(value, meta.data?.cycle_start_day ?? 1);
  const current = meta.data?.current_month === value;
  return (
    <div className="flex items-center gap-1">
      <button className="btn-ghost p-1.5" onClick={() => onChange(addMonths(value, -1))} aria-label="Önceki ay">
        <ChevronLeft size={18} />
      </button>
      <div className="min-w-28 text-center leading-tight">
        <div className="text-sm font-semibold">{monthLabel(value)}</div>
        {(range || !current) && (
          <div className="text-[11px] text-stone-500">
            {range}
            {!current && meta.data?.current_month && (
              <button className="ml-1 text-blue-600 underline" onClick={() => onChange(meta.data!.current_month)}>bu dönem</button>
            )}
          </div>
        )}
      </div>
      <button className="btn-ghost p-1.5" onClick={() => onChange(addMonths(value, 1))} aria-label="Sonraki ay">
        <ChevronRight size={18} />
      </button>
    </div>
  );
}
