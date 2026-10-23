import { useQuery } from "@tanstack/react-query";
import { Coins, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, tl } from "../format";
import { useUI } from "../ui";
import AssetForm from "./AssetForm";
import { useSave } from "./hooks";

interface Item {
  id: number; label: string; unit: string; quantity: number; value: number | null; person: string; is_self: boolean;
  acquired: string | null; cost: number | null; gain: number | null; gain_pct: number | null; note: string | null; is_jewelry: number;
}
interface Data { items: Item[]; self_total: number; family_total: number; updated: string | null }
interface ZakatMeta { people: { id: number; name: string }[]; kinds: Record<string, { label: string; unit: string }> }

/** Altın, döviz ve elde tutulan para: bugünkü değer, aldığından beri kazanç. Zekât sayfasıyla aynı liste. */
export default function Holdings() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["holdings"], queryFn: () => fetch("/api/holdings").then((r) => r.json() as Promise<Data>) });
  const meta = useQuery({ queryKey: ["zakat"], queryFn: () => fetch("/api/zakat").then((r) => r.json() as Promise<ZakatMeta>) });
  const [adding, setAdding] = useState(false);
  const del = useSave((id: number) => fetch(`/api/zakat/assets/${id}`, { method: "DELETE" }), () => ui.toast("Silindi"));
  const d = q.data;
  if (!d) return null;
  const cost = d.items.filter((i) => i.is_self && i.cost).reduce((s, i) => s + (i.cost ?? 0), 0);
  const gain = d.items.filter((i) => i.is_self && i.gain != null).reduce((s, i) => s + (i.gain ?? 0), 0);

  return (
    <section className="card p-4">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 font-semibold"><Coins size={18} /> Altın ve döviz</h2>
          {d.items.length > 0 && (
            <p className="text-xs text-stone-500">
              Senin: <b>{tl(d.self_total)}</b>
              {cost > 0 && <span className={gain >= 0 ? "text-green-700 dark:text-green-400" : "text-red-600"}> · alıştan beri {gain >= 0 ? "+" : "−"}{tl(Math.abs(gain))}</span>}
              {d.family_total > 0 && <> · aile: {tl(d.family_total)} <span className="text-stone-400">(net duruma dahil değil)</span></>}
              {d.updated && <> · fiyat {d.updated.slice(11, 16)}</>}
            </p>
          )}
        </div>
        <button className="btn-ghost px-2 py-1 text-sm" disabled={!meta.data} onClick={() => setAdding(true)}><Plus size={16} /> Ekle</button>
      </div>
      {!d.items.length && <p className="text-sm text-stone-500">Çeyrek, gram, bilezik, dolar… Ekle; bugünkü değeriyle net durumuna ve zekât hesabına girsin.</p>}
      <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {d.items.map((i) => (
          <li key={i.id} className="flex items-start justify-between gap-2 py-2">
            <span className="min-w-0">
              {i.quantity} {i.unit !== "adet" && i.unit !== "gr" ? "" : i.unit} {i.label}
              <span className="block text-xs text-stone-500">
                {[!i.is_self && i.person, i.is_jewelry ? "ziynet" : null, i.acquired && `alış ${dateTR(i.acquired)}`,
                  i.cost && `${tl(i.cost)}'ye`, i.note].filter(Boolean).join(" · ")}
              </span>
            </span>
            <span className="flex shrink-0 items-start gap-1 text-right">
              <span>
                <span className="block tabular-nums">{i.value != null ? tl(i.value) : "fiyat yok"}</span>
                {i.gain != null && (
                  <span className={cx("block text-xs tabular-nums", i.gain >= 0 ? "text-green-700 dark:text-green-400" : "text-red-600")}>
                    {i.gain >= 0 ? "+" : "−"}{tl(Math.abs(i.gain))} ({i.gain_pct}%)
                  </span>
                )}
              </span>
              <button className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label="Sil"
                onClick={() => window.confirm(`${i.label} silinsin mi?`) && del.mutate(i.id)}><Trash2 size={13} /></button>
            </span>
          </li>
        ))}
      </ul>
      {adding && meta.data && <AssetForm people={meta.data.people} kinds={meta.data.kinds} onClose={() => setAdding(false)} />}
    </section>
  );
}
