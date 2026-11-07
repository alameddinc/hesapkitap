// Kalem adı alanı + öneri (yetenek price_suggest; GET /api/prices/suggest?q&limit): kendi fişlerindeki ürün adları ve
// genel ürün adları, son fiyatıyla; bilinen bir ada çok yakın yazıldıysa "Bunu mu demek istedin: X?". Eski sunucuda
// (yetenek yok) düz metin alanı. Öneriye dokununca ad onunla değişir.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { cx, maskText, tl } from "../format";
import { send } from "../lib/apiError";
import { useMeta } from "./hooks";

export interface Suggest {
  items: { name: string; group_key: string | null; group_label: string | null; last_price: number | null; last_store: string | null; count: number }[];
  did_you_mean: { name: string; group_label: string | null } | null;
}

function useDebounced<T>(v: T, ms = 250) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export default function NameSuggest({ id, value, onChange, autoFocus, maxLength = 80 }: {
  id: string; value: string; onChange: (v: string) => void; autoFocus?: boolean; maxLength?: number;
}) {
  const on = !!useMeta().data?.capabilities?.includes("price_suggest");
  const [touched, setTouched] = useState(false);
  const [focus, setFocus] = useState(false);
  const [hi, setHi] = useState(-1);
  const q = useDebounced(value.trim());
  const listId = useId();
  const r = useQuery({
    queryKey: ["price-suggest", q], enabled: on && touched && q.length >= 2, retry: false, staleTime: 60_000,
    queryFn: () => send<Suggest>(`/api/prices/suggest?q=${encodeURIComponent(q)}&limit=6`),
  });
  const items = (r.data?.items ?? []).filter((x) => x.name.toLocaleLowerCase("tr") !== value.trim().toLocaleLowerCase("tr"));
  const dym = r.data?.did_you_mean && r.data.did_you_mean.name !== value.trim() ? r.data.did_you_mean : null;
  const show = on && focus && touched && items.length > 0;
  const pick = (name: string) => { onChange(name); setTouched(false); setHi(-1); };
  return (
    <div className="relative">
      <input id={id} className="input" value={value} maxLength={maxLength} autoFocus={autoFocus} autoComplete="off"
        role={on ? "combobox" : undefined} aria-expanded={on ? show : undefined} aria-controls={on ? listId : undefined} aria-autocomplete={on ? "list" : undefined}
        onFocus={() => setFocus(true)} onBlur={() => setTimeout(() => setFocus(false), 120)}
        onChange={(e) => { onChange(e.target.value); setTouched(true); setHi(-1); }}
        onKeyDown={(e) => {
          if (!show) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(items.length - 1, h + 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(-1, h - 1)); }
          else if (e.key === "Enter" && hi >= 0) { e.preventDefault(); pick(items[hi]!.name); }
          else if (e.key === "Escape") { e.stopPropagation(); setTouched(false); }
        }} />
      {show && (
        <ul id={listId} role="listbox" data-testid="ad-oneri"
          className="absolute inset-x-0 top-full z-10 mt-1 max-h-64 overflow-y-auto rounded-lg border border-stone-200 bg-white py-1 shadow-lg dark:border-stone-700 dark:bg-stone-900">
          {items.map((x, i) => (
            <li key={`${x.name}-${i}`} role="option" aria-selected={i === hi}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(x.name)}
                className={cx("flex w-full items-baseline gap-3 px-3 py-2 text-left text-sm hover:bg-stone-50 dark:hover:bg-stone-800", i === hi && "bg-stone-100 dark:bg-stone-800")}>
                <span className="min-w-0 flex-1">
                  <span className="block break-words">{x.name}</span>
                  {x.group_label && x.group_label !== x.name && <span className="block text-xs text-stone-500">{x.group_label}</span>}
                </span>
                {x.last_price != null && <span className="shrink-0 text-xs text-stone-500 tabular-nums">{maskText(tl(x.last_price))}{x.last_store ? ` · ${maskText(x.last_store)}` : ""}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {on && dym && (
        <p className="mt-1 text-sm text-stone-600 dark:text-stone-300" data-testid="bunu-mu">
          Bunu mu demek istedin: <button type="button" className="font-medium text-blue-700 underline underline-offset-2 dark:text-blue-400" onClick={() => pick(dym.name)}>{dym.name}</button>?</p>
      )}
    </div>
  );
}
