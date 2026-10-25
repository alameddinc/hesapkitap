import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2, TrendingUp } from "lucide-react";
import { useState } from "react";
import { monthLabel, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface Change { id: number; from_month: string; amount: number }

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
};

/** Kira artışı, aidat zammı: o aydan itibaren yeni tutar; geçmiş aylar eski tutarla kalır. */
export default function AmountChanges({ rid, base }: { rid: number; base: number }) {
  const ui = useUI();
  const list = useQuery({ queryKey: ["amount-changes", rid], queryFn: () => json<Change[]>("GET", `/api/recurring/${rid}/changes`) });
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [amount, setAmount] = useState("");
  const add = useSave(() => json("POST", `/api/recurring/${rid}/changes`, { from_month: month, amount: parseAmount(amount) }), () => {
    setOpen(false); setAmount(""); ui.toast("Tutar değişikliği kaydedildi");
  });
  const del = useSave((id: number) => json("DELETE", `/api/recurring-changes/${id}`));
  const items = list.data ?? [];
  return (
    <div className="space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-sm font-medium"><TrendingUp size={15} /> Tutar değişiklikleri</span>
        {!open && <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => setOpen(true)}><Plus size={14} /> Tutar değişti</button>}
      </div>
      {!items.length && !open && (
        <p className="text-xs text-stone-500">Kira artışı gibi: seçtiğin aydan itibaren yeni tutar geçerli olur, geçmiş aylar eski tutarla kalır.</p>
      )}
      {items.length > 0 && (
        <ul className="text-sm">
          <li className="flex justify-between py-0.5 text-stone-500"><span>Başlangıç</span><span className="tabular-nums">{tl(base)}</span></li>
          {items.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-2 py-0.5">
              <span>{monthLabel(c.from_month, true)}’den itibaren</span>
              <span className="flex items-center gap-1 tabular-nums">{tl(c.amount)}
                <button type="button" className="btn-ghost p-1" aria-label="Sil" onClick={() => del.mutate(c.id)}><Trash2 size={13} /></button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {open && (
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs">Hangi aydan
            <input type="month" className="input mt-1 w-40" value={month} onChange={(e) => setMonth(e.target.value)} />
          </label>
          <label className="text-xs">Yeni tutar
            <input className="input mt-1 w-32 text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus />
          </label>
          <button type="button" className="btn-primary px-3 py-1.5 text-sm" disabled={!(parseAmount(amount) >= 0) || !amount || add.isPending}
            onClick={() => add.mutate(undefined)}>Kaydet</button>
          <button type="button" className="btn-ghost px-2 py-1.5 text-sm" onClick={() => setOpen(false)}>Vazgeç</button>
        </div>
      )}
      {add.error && <p className="text-xs text-red-600">{(add.error as Error).message}</p>}
    </div>
  );
}
