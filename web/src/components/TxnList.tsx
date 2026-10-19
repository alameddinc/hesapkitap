import { Check, CreditCard, Repeat } from "lucide-react";
import type { Row } from "../api";
import { cx, dateTR, KIND_LABEL, tl } from "../format";
import { useUI } from "../ui";

const BADGE: Record<string, string> = {
  iade: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  faiz_ucret: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  odeme: "bg-stone-200 text-stone-700 dark:bg-stone-800 dark:text-stone-300",
  gelir: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
};

function amountClass(r: Row) {
  if (r.kind === "gelir") return "text-green-700 dark:text-green-400";
  if (r.kind === "iade" || r.amount < 0) return "text-green-700 dark:text-green-400";
  return "";
}

function PendingActions({ r }: { r: Row }) {
  const ui = useUI();
  const open = (e: React.MouseEvent) => {
    e.stopPropagation();
    ui.openRecurringMonth({ rid: r.recurring_id!, month: r.month, description: r.description, amount: r.amount,
      template: r.template_amount ?? r.amount, kind: r.kind, mode: "confirm" });
  };
  return (
    <div className="mt-2 flex gap-2">
      <button className="btn-primary px-2.5 py-1 text-xs" onClick={open}><Check size={14} /> Gerçekleşti</button>
    </div>
  );
}

export default function TxnList({ rows, empty = "İşlem yok" }: { rows: Row[]; empty?: string }) {
  const ui = useUI();
  if (!rows.length) return <p className="py-8 text-center text-sm text-stone-500">{empty}</p>;

  const open = (r: Row) => {
    if (r.source === "card") ui.openCardTxn(r);
    else if (r.source === "manual") ui.openEntry(r);
    else ui.openRecurring(r.recurring_id ?? undefined);
  };

  return (
    <ul className="divide-y divide-stone-100 dark:divide-stone-800">
      {rows.map((r) => (
        <li key={r.uid}>
          <div role="button" tabIndex={0} onClick={() => open(r)} onKeyDown={(e) => e.key === "Enter" && open(r)}
            className={cx("flex cursor-pointer gap-3 px-3 py-2.5 hover:bg-stone-50 dark:hover:bg-stone-800/50",
              (r.deleted || r.skipped || r.linked) && "opacity-50", r.pending && "bg-amber-50/60 dark:bg-amber-950/20")}>
            <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-stone-100 text-stone-500 dark:bg-stone-800">
              {r.source === "card" ? <CreditCard size={15} /> : r.source === "recurring" || r.recurring_id ? <Repeat size={15} /> : <span className="text-xs font-semibold">₺</span>}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-2">
                <span className={cx("truncate font-medium", r.deleted && "line-through")}>{r.description}</span>
                <span className={cx("shrink-0 font-semibold tabular-nums", amountClass(r))}>
                  {r.kind === "gelir" ? "+" : ""}{tl(r.amount)}
                </span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-stone-500">
                <span>{dateTR(r.date)}</span>
                <span>· {r.category}</span>
                {r.installment && <span>· {r.installment} taksit</span>}
                {r.fx && <span>· {r.fx}</span>}
                {r.card && <span className="hidden sm:inline">· {r.card}</span>}
                {BADGE[r.kind] && <span className={cx("rounded px-1.5 py-0.5 font-medium", BADGE[r.kind])}>{KIND_LABEL[r.kind]}</span>}
                {r.pending && <span className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300">Bekliyor</span>}
                {r.skipped && <span className="rounded bg-stone-200 px-1.5 py-0.5 dark:bg-stone-800">Atlandı</span>}
                {r.deleted && <span className="rounded bg-stone-200 px-1.5 py-0.5 dark:bg-stone-800">Silindi</span>}
                {r.awaiting_statement && <span className="rounded bg-blue-100 px-1.5 py-0.5 text-blue-800 dark:bg-blue-950 dark:text-blue-300">Ekstre bekleniyor</span>}
                {r.subscription === "iptal" && <span className="rounded bg-stone-200 px-1.5 py-0.5 dark:bg-stone-800">Abonelik iptal edildi</span>}
                {r.subscription === "dusuruldu" && <span className="rounded bg-green-100 px-1.5 py-0.5 text-green-800 dark:bg-green-950 dark:text-green-300">Paket düşürüldü</span>}
                {r.linked && <span className="rounded bg-green-100 px-1.5 py-0.5 text-green-800 dark:bg-green-950 dark:text-green-300">Ekstreyle eşleşti</span>}
              </div>
              {r.note && <div className="mt-0.5 truncate text-xs text-stone-400">{r.note}</div>}
              {r.pending && <PendingActions r={r} />}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
