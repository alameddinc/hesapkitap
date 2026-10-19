import { ChevronLeft, ChevronRight } from "lucide-react";
import { addMonths, monthLabel } from "../format";

export default function MonthPicker({ value, onChange }: { value: string; onChange: (m: string) => void }) {
  return (
    <div className="flex items-center gap-1">
      <button className="btn-ghost p-1.5" onClick={() => onChange(addMonths(value, -1))} aria-label="Önceki ay">
        <ChevronLeft size={18} />
      </button>
      <span className="min-w-28 text-center text-sm font-semibold">{monthLabel(value)}</span>
      <button className="btn-ghost p-1.5" onClick={() => onChange(addMonths(value, 1))} aria-label="Sonraki ay">
        <ChevronRight size={18} />
      </button>
    </div>
  );
}
