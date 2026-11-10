import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { AlertTriangle, ChevronRight, Coins } from "lucide-react";
import { useEffect, useState } from "react";
import { cx, parseAmount, tl, periodLabel } from "../format";
import { alsamSoz, TAKSIT_SECENEK, VARSAYILAN_TAKSIT, type Ton } from "../lib/alsamMi";
import { JarForm } from "./Jars";
import Modal from "./Modal";
import { LockCard, useLock } from "./Plan";
import Segmented from "./Segmented";

interface SimMonth {
  month: string;
  payment: number;
  net_before: number;
  net_after: number;
  with_estimate_after: number;
}
interface SimResult {
  monthly_payment: number;
  total_cost: number;
  extra_cost: number;
  months: SimMonth[];
  negative_months: string[];
  tight_with_estimate: string[];
}

const TON: Record<Ton, string> = {
  iyi: "text-green-700 dark:text-green-400", notr: "text-stone-700 dark:text-stone-200",
  uyari: "text-amber-700 dark:text-amber-300", kotu: "text-red-700 dark:text-red-400",
};

function useDebounced<T>(v: T, ms = 350) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

/** "Alırsam ne olur?" (mobil: Alsam mı?): bir alımın önümüzdeki aylara etkisi. Varsayılan peşin; vade farkı faiz diye
 *  yazılır ve onaylanmaz, yerine biriktirip peşin alma yolu (kumbara). Sözler lib/alsamMi.ts (mobille aynı). */
export default function Simulator({ onClose }: { onClose: () => void }) {
  const [ne, setNe] = useState("");
  const [amount, setAmount] = useState("");
  const [n, setN] = useState(VARSAYILAN_TAKSIT);
  const [jar, setJar] = useState(false);
  const jarLock = useLock("kumbara");
  const [interest, setInterest] = useState<"yok" | "var">("yok");
  const [rate, setRate] = useState("3,75");
  const a = parseAmount(amount);
  const r = interest === "var" ? parseAmount(rate) / 100 : 0;
  const params = useDebounced({ a, n, r });
  const q = useQuery({
    queryKey: ["simulate", params],
    queryFn: () => send<SimResult>("/api/plan/simulate", {
      method: "POST", json: { amount: params.a, installments: params.n, monthly_rate: params.r },
    }),
    enabled: params.a > 0 && Number.isFinite(params.r),
  });
  const d = q.data;
  const soz = d && a > 0 ? alsamSoz({ amount: a, n: params.n, extra: d.extra_cost, monthly: d.monthly_payment,
    negative: d.negative_months, tight: d.tight_with_estimate }, tl, undefined, ne.trim()) : null;
  const aylik = Math.ceil((a / Math.max(1, n)) * 100) / 100;

  if (jar && !jarLock) {
    return <JarForm preset={{ name: ne.trim(), goal: a > 0 ? a : null, monthly: n > 1 && a > 0 ? aylik : null }}
      onClose={() => setJar(false)} onSaved={onClose} />;
  }
  return (
    <Modal title="Alırsam ne olur?" onClose={onClose}>
      <div className="space-y-3">
        <div>
          <label className="label" htmlFor="sim-ne">Ne alıyorsun (ops.)</label>
          <input id="sim-ne" className="input" placeholder="ör. Buzdolabı" maxLength={60} value={ne} onChange={(e) => setNe(e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="sim-amt">Tutar (₺)</label>
            <input id="sim-amt" className="input text-right" inputMode="decimal" placeholder="ör. 40.000"
              value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="sim-n">Taksit</label>
            <input id="sim-n" type="number" min={1} max={12} className="input text-right" value={n}
              onChange={(e) => setN(Math.max(1, Math.min(12, Number(e.target.value) || 1)))} />
          </div>
        </div>
        <div role="radiogroup" aria-label="Taksit sayısı" className="flex flex-wrap gap-1.5">
          {TAKSIT_SECENEK.map(([c, ad]) => (
            <button key={c} type="button" role="radio" aria-checked={n === c} onClick={() => setN(c)}
              className={cx("rounded-full border px-3 py-1 text-xs", n === c ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>
              {c === 1 ? ad : `${ad} taksit`}
            </button>
          ))}
        </div>
        {n > 1 && (
          <div className="space-y-2">
            <Segmented value={interest} onChange={setInterest}
              options={[{ value: "yok", label: "Vade farksız" }, { value: "var", label: "Vade farklı (faiz)" }]} />
            {interest === "var" && (
              <div className="flex items-center gap-2 text-sm">
                Aylık oran %
                <input className="input w-20 text-right" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
                <span className="text-xs text-stone-500">+ BSMV %15 + KKDF %15 (ekstrende "Alışveriş faiz oranı")</span>
              </div>
            )}
          </div>
        )}

        {d && soz && (
          <>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg bg-stone-100 p-2 dark:bg-stone-800">
                <div className="text-xs text-stone-500">{n > 1 ? "Aylık" : "Peşin"}</div><div className="font-semibold tabular-nums">{tl(d.monthly_payment)}</div>
              </div>
              <div className="rounded-lg bg-stone-100 p-2 dark:bg-stone-800">
                <div className="text-xs text-stone-500">Toplam</div><div className="font-semibold tabular-nums">{tl(d.total_cost)}</div>
              </div>
              <div className={cx("rounded-lg p-2", d.extra_cost > 0 ? "bg-red-50 dark:bg-red-950/40" : "bg-green-50 dark:bg-green-950/40")}>
                <div className="text-xs text-stone-500">{d.extra_cost > 0 ? "Vade farkı (faiz)" : "Vade farkı"}</div>
                <div className={cx("font-semibold tabular-nums", d.extra_cost > 0 ? "text-red-600" : "text-green-700")}>{tl(d.extra_cost)}</div>
              </div>
            </div>
            <p className={cx("text-sm font-medium", TON[soz.maliyet.ton])} data-testid="sim-maliyet">{soz.maliyet.t}</p>
            {soz.faiz && (
              <p role="alert" className="flex gap-2 rounded-lg bg-amber-50 p-2 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" data-testid="sim-faiz">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />{soz.faiz}
              </p>
            )}
            <p className={cx("text-sm font-semibold", TON[soz.sonuc.ton])} data-testid="sim-sonuc">{soz.sonuc.t}</p>
            {soz.biriktir && <p className="text-sm text-stone-700 dark:text-stone-200" data-testid="sim-biriktir">{soz.biriktir}</p>}
            {jarLock ? <LockCard lock={jarLock} title="Biriktir: kumbara aç" compact /> : (
              <button type="button" className="flex w-full items-center gap-3 rounded-xl border border-stone-200 p-3 text-left hover:bg-stone-50 dark:border-stone-700 dark:hover:bg-stone-800/50"
                onClick={() => setJar(true)}>
                <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300" aria-hidden><Coins size={17} /></span>
                <span className="min-w-0 flex-1"><span className="block font-medium">Biriktir: kumbara aç</span>
                  <span className="block text-xs text-stone-500">{soz.biriktirAlt}</span></span>
                <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden />
              </button>
            )}
            <table className="w-full text-sm tabular-nums whitespace-nowrap [overflow-wrap:normal]">
              <thead>
                <tr className="text-left text-xs text-stone-500">
                  <th className="py-1 font-medium">Ay</th>
                  <th className="py-1 text-right font-medium">Taksit</th>
                  <th className="py-1 text-right font-medium">Net (önce → sonra)</th>
                </tr>
              </thead>
              <tbody>
                {d.months.map((m) => (
                  <tr key={m.month} className="border-t border-stone-100 dark:border-stone-800">
                    <td className="py-1">{periodLabel(m.month, true)}</td>
                    <td className="py-1 text-right">{m.payment ? tl(m.payment) : "—"}</td>
                    <td className="py-1 text-right">
                      <span className="text-stone-400">{tl(m.net_before)}</span> →{" "}
                      <b className={m.net_after < 0 ? "text-red-600" : undefined}>{tl(m.net_after)}</b>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-stone-500">Net = gelir − kesin giderler (taksit, abonelik, sabit, fatura). Tahmini kart harcaması dahil değil.</p>
          </>
        )}
        {!(a > 0) && <p className="text-sm text-stone-500">Tutarı yaz; önümüzdeki aylarına etkisini görelim.</p>}
      </div>
    </Modal>
  );
}
