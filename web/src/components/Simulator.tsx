import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";
import { cx, monthLabel, parseAmount, tl } from "../format";
import Modal from "./Modal";
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

const COUNTS = [1, 2, 3, 6, 9, 12];

function useDebounced<T>(v: T, ms = 350) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

/** "Alırsam ne olur?": taksitli/tek çekim alımın önümüzdeki aylara etkisi. */
export default function Simulator({ onClose }: { onClose: () => void }) {
  const [amount, setAmount] = useState("");
  const [n, setN] = useState(6);
  const [interest, setInterest] = useState<"yok" | "var">("yok");
  const [rate, setRate] = useState("3,75");
  const a = parseAmount(amount);
  const r = interest === "var" ? parseAmount(rate) / 100 : 0;
  const params = useDebounced({ a, n, r });
  const q = useQuery({
    queryKey: ["simulate", params],
    queryFn: () => fetch("/api/plan/simulate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: params.a, installments: params.n, monthly_rate: params.r }),
    }).then((x) => x.json() as Promise<SimResult>),
    enabled: params.a > 0 && Number.isFinite(params.r),
  });
  const d = q.data;

  return (
    <Modal title="Alırsam ne olur?" onClose={onClose}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="sim-amt">Tutar (₺)</label>
            <input id="sim-amt" className="input text-right" inputMode="decimal" autoFocus placeholder="ör. 40.000"
              value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="sim-n">Taksit</label>
            <input id="sim-n" type="number" min={1} max={36} className="input text-right" value={n}
              onChange={(e) => setN(Math.max(1, Math.min(36, Number(e.target.value) || 1)))} />
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {COUNTS.map((c) => (
            <button key={c} type="button" onClick={() => setN(c)}
              className={cx("rounded-full border px-3 py-1 text-xs", n === c ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>
              {c === 1 ? "Tek çekim" : `${c} taksit`}
            </button>
          ))}
        </div>
        {n > 1 && (
          <div className="space-y-2">
            <Segmented value={interest} onChange={setInterest}
              options={[{ value: "yok", label: "Faizsiz taksit" }, { value: "var", label: "Vade farklı" }]} />
            {interest === "var" && (
              <div className="flex items-center gap-2 text-sm">
                Aylık oran %
                <input className="input w-20 text-right" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
                <span className="text-xs text-stone-500">+ BSMV %15 + KKDF %15 (ekstrende "Alışveriş faiz oranı")</span>
              </div>
            )}
          </div>
        )}

        {d && (
          <>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg bg-stone-100 p-2 dark:bg-stone-800">
                <div className="text-xs text-stone-500">Aylık</div><div className="font-semibold tabular-nums">{tl(d.monthly_payment)}</div>
              </div>
              <div className="rounded-lg bg-stone-100 p-2 dark:bg-stone-800">
                <div className="text-xs text-stone-500">Toplam</div><div className="font-semibold tabular-nums">{tl(d.total_cost)}</div>
              </div>
              <div className={cx("rounded-lg p-2", d.extra_cost > 0 ? "bg-red-50 dark:bg-red-950/40" : "bg-green-50 dark:bg-green-950/40")}>
                <div className="text-xs text-stone-500">Ek maliyet</div>
                <div className={cx("font-semibold tabular-nums", d.extra_cost > 0 ? "text-red-600" : "text-green-700")}>{tl(d.extra_cost)}</div>
              </div>
            </div>
            {d.negative_months.length > 0 && (
              <p className="flex gap-2 rounded-lg bg-red-50 p-2 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-300">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                {d.negative_months.map((m) => monthLabel(m, true)).join(", ")}: kesin giderler gelirini aşar.
              </p>
            )}
            {!d.negative_months.length && d.tight_with_estimate.length > 0 && (
              <p className="flex gap-2 rounded-lg bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                Harcamaların son aylardaki gibi giderse {d.tight_with_estimate.map((m) => monthLabel(m, true)).join(", ")} sıkışık.
              </p>
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
                    <td className="py-1">{monthLabel(m.month, true)}</td>
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
        {!(a > 0) && <p className="text-sm text-stone-500">Tutarı gir; taksit sayısını değiştirdikçe önümüzdeki aylara etkisini gör.</p>}
      </div>
    </Modal>
  );
}
