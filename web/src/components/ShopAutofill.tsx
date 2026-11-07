// Alışveriş listesi otomatik doldurma (yetenek shopping_autofill): ekleme kutusunun altında yazarken öneriler (en az 2
// harf, GET /api/shopping-list/suggest; seçilince POST {text, group_key} — boy sabitlenir), "Sık aldıkların" çipleri
// (GET /frequent, tek dokunuşla ekle) ve "Listeyi doldur" (yalnız GET /due bir şey döndürürse: işaretli kutular,
// "Listeye ekle (N)" → POST /bulk). Metinden miktar okumak sunucuda ("2,5 kg elma", "6 süt").
import { useQuery } from "@tanstack/react-query";
import { ListPlus, Plus } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { cx, fold } from "../format";
import { send } from "../lib/apiError";
import { amountText } from "../lib/shopList";
import Modal from "./Modal";

export interface Suggestion { text: string; group_key: string | null; label: string; source: "recent" | "own" | "group" | string; count?: number | null }
export interface Frequent { text: string; group_key: string | null; label: string; count: number; last_date?: string | null }
export interface Due extends Frequent { qty: number; unit: string; interval_days: number; days_since: number; reason: string }

export const FREQUENT = ["shopping-frequent"] as const;
export const DUE = ["shopping-due"] as const;

export function useDebounced<T>(v: T, ms = 250): T {
  const [d, setD] = useState(v);
  useEffect(() => { const h = setTimeout(() => setD(v), ms); return () => clearTimeout(h); }, [v, ms]);
  return d;
}

/** Yazarken öneri (q en az 2 harf; bekleme 250 ms). */
export function useSuggest(text: string, on: boolean) {
  const q = useDebounced(text.trim(), 250);
  return useQuery({
    queryKey: ["shopping-suggest", q], enabled: on && q.length >= 2, retry: false, staleTime: 60_000,
    queryFn: () => send<{ items: Suggestion[] }>(`/api/shopping-list/suggest?q=${encodeURIComponent(q)}&limit=8`).then((d) => d.items ?? []),
    placeholderData: (prev) => prev,
  });
}

const srcText = (s: Suggestion) => (s.source === "own" ? "Sık aldığın" : s.source === "recent" ? "Son alışverişten" : "");

/** Öneri listesi (combobox'ın listbox'ı): fareyle tıklama input'un odağını kaçırmasın diye mousedown'da seçilir. */
export function SuggestList({ id, items, active, onPick, className }: {
  id: string; items: Suggestion[]; active: number; onPick: (s: Suggestion) => void; className?: string;
}) {
  return (
    <ul id={id} role="listbox" aria-label="Öneriler" data-testid="oneriler"
      className={cx("max-h-72 overflow-y-auto rounded-xl border border-stone-200 bg-white py-1 shadow-lg dark:border-stone-700 dark:bg-stone-900", className)}>
      {items.map((s, i) => (
        <li key={`${s.text}|${s.group_key ?? ""}`} id={`${id}-${i}`} role="option" aria-selected={i === active}
          className={cx("flex cursor-pointer items-center gap-3 px-3 py-2 text-sm", i === active ? "bg-stone-100 dark:bg-stone-800" : "hover:bg-stone-50 dark:hover:bg-stone-800/60")}
          onMouseDown={(e) => { e.preventDefault(); onPick(s); }}>
          <span className="min-w-0 flex-1 break-words">{s.label || s.text}</span>
          {srcText(s) && <span className="shrink-0 text-xs text-stone-500 dark:text-stone-400">{srcText(s)}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Ekleme kutusu. Yeni sunucuda (shopping_units) adet kutusu yok: miktar metinden okunur. */
export function AddForm({ autofill, units, onAdd }: {
  autofill: boolean; units: boolean; onAdd: (a: { text: string; qty?: number; group_key?: string | null }) => void;
}) {
  const [text, setText] = useState("");
  const [qty, setQty] = useState("1");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const sug = useSuggest(text, autofill && open);
  const items = text.trim().length >= 2 ? sug.data ?? [] : [];
  const shown = open && items.length > 0;
  const lid = useId().replace(/:/g, "");
  const reset = () => { setText(""); setQty("1"); setActive(-1); setOpen(false); };
  const pick = (s: Suggestion) => { onAdd({ text: s.text, group_key: s.group_key }); reset(); };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (shown && active >= 0 && items[active]) return pick(items[active]);
    const t = text.trim();
    if (!t) return;
    onAdd(units ? { text: t } : { text: t, qty: Math.max(1, Math.round(Number(qty.replace(",", ".")) || 1)) });
    reset();
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (!shown) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % items.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a <= 0 ? items.length - 1 : a - 1)); }
    else if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
  };
  return (
    <form className="card flex flex-wrap gap-2 p-3" onSubmit={submit} data-testid="ekle-formu">
      <div className="relative min-w-0 flex-[1_1_12rem]">
        <label className="sr-only" htmlFor="al-text">Ürün</label>
        <input id="al-text" className="input w-full" placeholder={units ? "Süt, 2 kg elma, 6 yumurta…" : "Süt, yumurta 30'lu, deterjan…"} value={text}
          onChange={(e) => { setText(e.target.value); setOpen(true); setActive(-1); }} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
          onKeyDown={onKey} maxLength={120} autoComplete="off"
          {...(autofill ? { role: "combobox", "aria-expanded": shown, "aria-controls": lid, "aria-autocomplete": "list" as const,
            "aria-activedescendant": shown && active >= 0 ? `${lid}-${active}` : undefined } : {})} />
        {shown && <SuggestList id={lid} items={items} active={active} onPick={pick} className="absolute inset-x-0 top-full z-30 mt-1" />}
      </div>
      {!units && <>
        <label className="sr-only" htmlFor="al-qty">Adet</label>
        <input id="al-qty" className="input w-16 text-center tabular-nums" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value)} aria-label="Adet" />
      </>}
      <button className="btn-primary justify-center" disabled={!text.trim()}><Plus size={16} aria-hidden /> Ekle</button>
    </form>
  );
}

/** "Sık aldıkların" çipleri ve "Listeyi doldur" düğmesi (zamanı gelen varsa). */
export function QuickAdd({ onAdd, onFill, have }: {
  onAdd: (f: Frequent) => void; onFill: (due: Due[]) => void;
  /** listedeki satırlar (işaretliler dahil): sunucu işaretlileri "listede yok" sayıyor, çip olarak yine görünmesin */
  have?: { text: string; group_key: string | null }[];
}) {
  const freq = useQuery({ queryKey: FREQUENT, retry: false, staleTime: 60_000,
    queryFn: () => send<{ items: Frequent[] }>("/api/shopping-list/frequent?limit=8").then((d) => d.items ?? []) });
  const due = useQuery({ queryKey: DUE, retry: false, staleTime: 60_000,
    queryFn: () => send<{ items: Due[] }>("/api/shopping-list/due").then((d) => d.items ?? []) });
  const base = (k: string | null | undefined) => (k ? k.split("|")[0] : null);
  const keys = new Set((have ?? []).map((h) => base(h.group_key)).filter(Boolean));
  const names = new Set((have ?? []).map((h) => fold(h.text)));
  const fresh = (x: Frequent) => !(x.group_key && keys.has(base(x.group_key))) && !names.has(fold(x.text));
  const f = (freq.data ?? []).filter(fresh), d = (due.data ?? []).filter(fresh);
  if (!f.length && !d.length) return null;
  return (
    <section className="space-y-2" aria-label="Hızlı ekle" data-testid="hizli-ekle">
      {d.length > 0 && (
        <button type="button" className="btn w-full justify-center border border-stone-300 bg-white text-stone-800 hover:bg-stone-50 sm:w-auto dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100 dark:hover:bg-stone-800" onClick={() => onFill(d)} data-testid="listeyi-doldur">
          <ListPlus size={16} aria-hidden /> Listeyi doldur <span className="text-stone-500 dark:text-stone-400">· {d.length} ürünün zamanı geldi</span>
        </button>
      )}
      {f.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs font-medium text-stone-500 dark:text-stone-400">Sık aldıkların</span>
          {f.map((x) => (
            <button key={`${x.text}|${x.group_key ?? ""}`} type="button" onClick={() => onAdd(x)} aria-label={`${x.label || x.text} listeye ekle`}
              className="inline-flex items-center gap-1 rounded-full border border-stone-200 bg-white px-2.5 py-1 text-sm text-stone-700 hover:bg-stone-50 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-200 dark:hover:bg-stone-800">
              <Plus size={13} className="text-stone-400" aria-hidden />{x.label || x.text}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

/** "Listeyi doldur": zamanı gelenler, hepsi işaretli; "Listeye ekle (N)". */
export function FillDialog({ items, busy, onClose, onConfirm }: { items: Due[]; busy?: boolean; onClose: () => void; onConfirm: (sel: Due[]) => void }) {
  const [off, setOff] = useState<Set<number>>(new Set());
  const sel = items.filter((_, i) => !off.has(i));
  const toggle = (i: number) => setOff((s) => { const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  return (
    <Modal title="Listeyi doldur" onClose={onClose} footer={<>
      <div className="flex-1" />
      <button type="button" className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button type="button" className="btn-primary" disabled={!sel.length || busy} onClick={() => onConfirm(sel)} data-testid="doldur-ekle">Listeye ekle ({sel.length})</button>
    </>}>
      <p className="mb-2 text-sm text-stone-500 dark:text-stone-400">Fişlerine göre bunları alma zamanın geldi.</p>
      <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
        {items.map((x, i) => (
          <li key={`${x.text}|${x.group_key ?? ""}`}>
            <label className="flex cursor-pointer items-start gap-3 px-3 py-2.5">
              <input type="checkbox" className="mt-0.5 size-5 shrink-0" checked={!off.has(i)} onChange={() => toggle(i)} />
              <span className="min-w-0 flex-1 font-medium break-words">{x.label || x.text}
                <span className="block text-xs font-normal text-stone-500 dark:text-stone-400">{x.reason}</span>
              </span>
              <span className="shrink-0 text-sm text-stone-600 tabular-nums dark:text-stone-300">{amountText(x.qty, x.unit)}</span>
            </label>
          </li>
        ))}
      </ul>
    </Modal>
  );
}
