import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { ask } from "../lib/ask";
import { Coins, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import AssetForm from "./AssetForm";
import { useSave } from "./hooks";

interface Item {
  id: number; label: string; unit: string; quantity: number; value: number | null; person: string; is_self: boolean;
  acquired: string | null; cost: number | null; gain: number | null; gain_pct: number | null; note: string | null; is_jewelry: number;
  estimated: number;
}
interface Data { items: Item[]; self_total: number; family_total: number; updated: string | null }
interface ZakatMeta { people: { id: number; name: string }[]; kinds: Record<string, { label: string; unit: string }> }

/** Altın, döviz ve elde tutulan para: bugünkü değer, aldığından beri kazanç. Zekât sayfasıyla aynı liste. */
export default function Holdings() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["holdings"], queryFn: () => send<Data>("/api/holdings") });
  const meta = useQuery({ queryKey: ["zakat"], queryFn: () => send<ZakatMeta>("/api/zakat") });
  const [adding, setAdding] = useState(false);
  const del = useSave((id: number) => fetch(`/api/zakat/assets/${id}`, { method: "DELETE" }), () => ui.toast("Silindi"));
  const fix = useSave((a: { id: number; quantity: number }) => fetch(`/api/zakat/assets/${a.id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ quantity: a.quantity, estimated: false }),
  }), () => ui.toast("Miktar güncellendi"));
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
              <button className="underline decoration-dotted" title="Miktarı düzelt" onClick={() => {
                const v = window.prompt(`${i.label}: gerçek miktar (${i.unit})`, String(i.quantity).replace(".", ","));
                const n = v == null ? NaN : parseAmount(v);
                if (n > 0) fix.mutate({ id: i.id, quantity: n });
              }}>{String(i.quantity).replace(".", ",")} {i.unit !== "adet" && i.unit !== "gr" ? "" : i.unit}</button> {i.label}
              {!!i.estimated && (
                <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                  title="Tutar ve tarihten fazla tahmin edildi; tartıp gerçek miktara dokunarak düzelt">tahmini · tartıp düzelt</span>
              )}
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
                onClick={() => void ask(`${i.label} silinsin mi?`).then((ok) => { if (ok) del.mutate(i.id); })}><Trash2 size={13} /></button>
            </span>
          </li>
        ))}
      </ul>
      {adding && meta.data && <AssetForm people={meta.data.people} kinds={meta.data.kinds} onClose={() => setAdding(false)} />}
    </section>
  );
}
