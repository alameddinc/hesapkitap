import { ChevronDown } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { cx, fold } from "../format";

/** Çoklu seçim filtresi. Boş seçim = hepsi. */
export default function MultiSelect({ label, options, value, onChange, render = (o) => o }: {
  label: string;
  options: string[];
  value: string[];
  onChange: (v: string[]) => void;
  render?: (o: string) => string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);
  const shown = useMemo(() => options.filter((o) => fold(render(o)).includes(fold(q))), [options, q, render]);
  const toggle = (o: string) => onChange(value.includes(o) ? value.filter((x) => x !== o) : [...value, o]);

  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen(!open)}
        className={cx("flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-sm whitespace-nowrap",
          value.length ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300" : "border-stone-300 dark:border-stone-700")}>
        {label}{value.length ? ` (${value.length})` : ""} <ChevronDown size={14} />
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-64 rounded-lg border border-stone-200 bg-white p-2 shadow-lg dark:border-stone-700 dark:bg-stone-900">
          {options.length > 8 && <input className="input mb-2" placeholder="Ara" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />}
          <div className="mb-1 flex gap-2 text-xs">
            <button className="text-blue-600" onClick={() => onChange([])}>Hepsi</button>
            <button className="text-blue-600" onClick={() => onChange(shown)}>Görünenleri seç</button>
          </div>
          <ul className="max-h-64 overflow-y-auto">
            {shown.map((o) => (
              <li key={o}>
                <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-stone-50 dark:hover:bg-stone-800">
                  <input type="checkbox" checked={value.includes(o)} onChange={() => toggle(o)} />
                  <span className="truncate">{render(o)}</span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
