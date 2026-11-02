import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Coins, Save } from "lucide-react";
import { useState } from "react";
import type { AiCosts as Costs } from "../api";
import { call } from "../lib/apiError";
import { useUI } from "../ui";
import { useSave } from "./hooks";

const KEY = ["admin-ai-costs"];
const n = (x: number) => x.toLocaleString("tr-TR");
const usd = (x: number) => `$${x.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
const tl = (x: number) => `₺${x.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** Yönetici: bu ayın yapay zekâ maliyeti (tür başına çağrı, okuma, ortalama token, tahmini maliyet; en yoğun 10
 *  kullanıcı maskeli). Model fiyatı ($ / 1M token) ve dolar kuru ayarlanabilir. */
export default function AiCosts() {
  const ui = useUI();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: KEY, queryFn: () => call<Costs>("GET", "/api/admin/ai-costs") });
  const d = q.data;
  const model = d ? Object.keys(d.prices)[0] ?? "" : "";
  const [form, setForm] = useState<{ input: string; output: string; fx: string } | null>(null);
  const f = form ?? (d ? { input: String(d.prices[model]?.input ?? ""), output: String(d.prices[model]?.output ?? ""), fx: String(d.usd_try) } : null);
  const num = (s: string) => Number(s.replace(",", "."));
  const save = useSave(() => call<Costs>("PUT", "/api/admin/ai-costs", {
    prices: { [model]: { input: num(f!.input), output: num(f!.output) } }, usd_try: num(f!.fx),
  }), (r) => { qc.setQueryData(KEY, r); setForm(null); ui.toast("Fiyat ve kur kaydedildi"); });

  return (
    <section className="card space-y-3 p-4" aria-labelledby="ai-costs-h">
      <h2 id="ai-costs-h" className="flex items-center gap-2 font-semibold"><Coins size={18} aria-hidden="true" /> Yapay zekâ maliyeti</h2>
      {q.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      {q.isError && <p className="text-sm text-red-600">{(q.error as Error).message}</p>}
      {d && f && <>
        <p className="text-sm">
          <b>{d.month}</b>: {n(d.calls)} çağrı · tahmini <b>{usd(d.cost_usd)}</b> ({tl(d.cost_try)})
        </p>
        {/* dar ekran: tür başına kart; geniş ekran: tablo */}
        <ul className="space-y-2 sm:hidden">
          {d.kinds.map((k) => (
            <li key={k.kind} className="rounded-lg bg-stone-50 p-2 text-sm dark:bg-stone-800/60">
              <div className="flex justify-between gap-2 font-medium"><span>{k.label}</span><span className="tabular-nums">{usd(k.cost_usd)}</span></div>
              <div className="text-xs text-stone-600 tabular-nums dark:text-stone-400">
                {n(k.calls)} çağrı{k.reads != null ? ` · ${n(k.reads)} okuma` : ""} · {tl(k.cost_try)}
              </div>
              <div className="text-xs text-stone-500 tabular-nums">
                Ortalama girdi {n(k.avg_input)} · çıktı {n(k.avg_output)} · düşünme {n(k.avg_thoughts)} token
              </div>
            </li>
          ))}
        </ul>
        <div className="hidden sm:block">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-stone-500">
                <th className="py-1 font-normal">Tür</th>
                <th className="px-2 py-1 text-right font-normal">Çağrı</th>
                <th className="px-2 py-1 text-right font-normal">Okuma</th>
                <th className="px-2 py-1 text-right font-normal">Ort. girdi</th>
                <th className="px-2 py-1 text-right font-normal">Ort. çıktı</th>
                <th className="px-2 py-1 text-right font-normal">Ort. düşünme</th>
                <th className="py-1 pl-2 text-right font-normal">Maliyet</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-100 tabular-nums dark:divide-stone-800">
              {d.kinds.map((k) => (
                <tr key={k.kind}>
                  <td className="py-1.5 pr-2">{k.label}</td>
                  <td className="px-2 text-right">{n(k.calls)}</td>
                  <td className="px-2 text-right">{k.reads == null ? "—" : n(k.reads)}</td>
                  <td className="px-2 text-right">{n(k.avg_input)}</td>
                  <td className="px-2 text-right">{n(k.avg_output)}</td>
                  <td className="px-2 text-right">{n(k.avg_thoughts)}</td>
                  <td className="py-1.5 pl-2 text-right">{usd(k.cost_usd)}<span className="block text-xs text-stone-500">{tl(k.cost_try)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-stone-500">
          Okuma: aylık haktan düşen belge / fiş sayısı. Çağrı: yapay zekâya giden istek (bir belge birden çok çağrı olabilir;
          yerel okuyucuyla okunan belge çağrı yapmaz). Belge içeriği kaydedilmez, yalnız token sayıları.
        </p>
        <div>
          <h3 className="mb-1 text-sm font-medium">En yoğun 10 kullanıcı</h3>
          {d.top_users.length ? (
            <ol className="space-y-1 text-sm">
              {d.top_users.map((u, i) => (
                <li key={i} className="flex flex-wrap justify-between gap-x-3 tabular-nums">
                  <span className="min-w-0 break-all">{i + 1}. {u.user}</span>
                  <span className="text-stone-600 dark:text-stone-400">{n(u.calls)} çağrı · {usd(u.cost_usd)} ({tl(u.cost_try)})</span>
                </li>
              ))}
            </ol>
          ) : <p className="text-sm text-stone-500">Bu ay çağrı yok.</p>}
        </div>
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }}>
          <label className="text-xs text-stone-500">Girdi $ / 1M ({model})
            <input className="input mt-1 block w-28" inputMode="decimal" value={f.input} onChange={(e) => setForm({ ...f, input: e.target.value })} />
          </label>
          <label className="text-xs text-stone-500">Çıktı $ / 1M
            <input className="input mt-1 block w-28" inputMode="decimal" value={f.output} onChange={(e) => setForm({ ...f, output: e.target.value })} />
          </label>
          <label className="text-xs text-stone-500">Dolar kuru (₺)
            <input className="input mt-1 block w-28" inputMode="decimal" value={f.fx} onChange={(e) => setForm({ ...f, fx: e.target.value })} />
          </label>
          <button className="btn-primary inline-flex items-center gap-1" disabled={!form || save.isPending}><Save size={16} aria-hidden="true" /> Kaydet</button>
        </form>
        <p className="text-xs text-stone-500">Düşünme token'ı çıktı fiyatıyla hesaplanır. Varsayılan fiyat Google'ın liste fiyatıdır; değişirse buradan güncelle.</p>
      </>}
    </section>
  );
}
