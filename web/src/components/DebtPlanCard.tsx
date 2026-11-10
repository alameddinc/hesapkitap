import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronRight, HandCoins } from "lucide-react";
import { send } from "../lib/apiError";
import { debtPlanUrl, type DebtPlanData } from "../lib/debtPlan";
import { tl } from "../format";
import { useCap } from "./hooks";
import { useUI } from "../ui";

/** Özet kartı (Dashboard "borc-plani"): ≥2 açık borç varsa; dokununca Borç planı. */
export default function DebtPlanCard() {
  const ui = useUI();
  const on = useCap("debt_plan");
  const q = useQuery({ queryKey: ["debt-plan", null, null], queryFn: () => send<DebtPlanData>(debtPlanUrl()), enabled: on });
  const r = q.data;
  if (!on || !r || !r.summary_card.eligible) return null;
  const next = r.this_month.items.find((i) => !i.done);
  return (
    <button className="card flex w-full items-center gap-3 p-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => ui.go("borc-plani")}
      data-testid="ozet-borc-plani">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-400" aria-hidden><HandCoins size={18} /></span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">Borç planı · {tl(r.totals.open)}</span>
        <span className="block text-xs text-stone-500">
          {r.debt_free ? `Borçsuz: ${r.debt_free.label}` : r.monthly == null ? "Aylık tutarı yaz, plan çıksın" : "Bu tutarla kapanmıyor"}
          {next ? ` · sıradaki: ${next.title} ${tl(next.amount)}` : ""}</span></span>
      {r.shortfall && <AlertTriangle size={16} className="shrink-0 text-red-600" aria-label="Kart faizi önlenemiyor" />}
      <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden />
    </button>
  );
}
