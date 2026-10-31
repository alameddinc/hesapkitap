import { useQuery } from "@tanstack/react-query";
import { Check, CreditCard, FileText, Receipt, HandCoins } from "lucide-react";
import { api, type Payable } from "../api";
import { cx, dateTR, tl } from "../format";
import { useUI } from "../ui";
import CycleLine from "./CardCycle";

function DueBadge({ p }: { p: Payable }) {
  if (p.status === "odendi") return <span className="rounded bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800 dark:bg-green-950 dark:text-green-300">✓ Ödendi {p.paid_at && dateTR(p.paid_at).slice(0, 5)}</span>;
  if (p.status === "gecikti") return <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-800 dark:bg-red-950 dark:text-red-300">{-p.days_left} gün gecikti</span>;
  if (p.status === "kismi") return <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300">Kısmi · {tl(p.amount - (p.paid_amount ?? 0))} kaldı</span>;
  const urgent = p.days_left <= 3;
  const label = p.days_left === 0 ? "Bugün" : `${p.days_left} gün`;
  return <span className={cx("rounded px-1.5 py-0.5 text-xs font-medium",
    p.status === "tahmini" ? "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-400"
      : urgent ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300")}>
    {p.status === "tahmini" ? `tahmini · ${label}` : label}</span>;
}

export default function Payables() {
  const ui = useUI();
  // toplamlar sunucudan: eski ödenmemiş ekstre (kalanı son ekstreye devretti) iki kez sayılmasın
  const q = useQuery({ queryKey: ["payables", "totals"], queryFn: api.payablesWithTotals });
  const items = q.data?.items ?? [];
  const t = q.data?.totals;
  const rolled = (p: Payable) => p.in_total === false && !!p.rolled_into;

  return (
    <section className="card overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-stone-100 px-3 py-2.5 sm:px-4 dark:border-stone-800">
        <div>
          <h2 className="font-semibold">Ödemeler</h2>
          <p className="text-xs text-stone-500 dark:text-stone-400">{t && t.open_count ? `${t.open_count} ödeme bekliyor · ${tl(t.open)}` : t ? "Bekleyen ödeme yok 🎉" : "…"}</p>
          {!!t?.overdue_count && (
            <p className="text-xs text-stone-500 dark:text-stone-400">
              + {t.overdue_count} eski ekstre ödenmemiş ({tl(t.overdue)}); kalanı sonraki ekstreye devretti, toplama ayrıca girmez</p>
          )}
        </div>
        <button className="btn-ghost shrink-0 border border-stone-300 text-xs whitespace-nowrap dark:border-stone-700" onClick={() => ui.openBill()}>
          <Receipt size={14} /> Fatura ekle
        </button>
      </div>
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {items.map((p) => (
          <li key={`${p.type}-${p.id ?? p.title}`} className={cx("flex items-center gap-3 px-3 py-2.5 sm:px-4", (p.status === "odendi" || rolled(p)) && "opacity-60")}>
            <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-stone-100 text-stone-500 dark:bg-stone-800">
              {p.type === "bill" ? <FileText size={15} /> : p.type === "debt" ? <HandCoins size={15} /> : <CreditCard size={15} />}
            </span>
            <button className="min-w-0 flex-1 text-left" disabled={p.type !== "bill"}
              onClick={() => p.type === "bill" && ui.openBill({ id: p.id!, provider: p.title, amount: p.amount, due_date: p.due_date,
                category: p.category, note: p.note, description: p.description })}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="font-medium break-words">{p.title}</span>
                <DueBadge p={p} />
              </div>
              <div className="mt-0.5 text-xs text-stone-500">
                {p.subtitle} · son ödeme {dateTR(p.due_date)}
                {p.min_payment != null && p.min_payment < p.amount && p.status !== "odendi" && ` · asgari ${tl(p.min_payment)}`}
                {rolled(p) && <span className="block text-stone-600 dark:text-stone-300">Ödenmedi; kalanı sonraki ekstreye devretti (orada sayılıyor, toplama ayrıca girmez)</span>}
                {p.cycle && <CycleLine c={p.cycle} />}
                {p.has_file && p.id && <> · <a className="text-blue-600 underline" href={`/api/bills/${p.id}/file`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>dosya</a></>}
              </div>
            </button>
            <div className="flex shrink-0 flex-col items-end gap-1">
              <span className="font-semibold tabular-nums">{tl(p.amount)}</span>
              {p.type !== "estimate" && (
                p.status === "odendi"
                  ? <button className="text-xs text-stone-500 underline" onClick={() => ui.openPay(p)}>düzenle</button>
                  : <button className="btn-primary px-2.5 py-1 text-xs" onClick={() => ui.openPay(p)}><Check size={14} /> Ödedim</button>
              )}
            </div>
          </li>
        ))}
        {!items.length && !q.isLoading && <li className="px-4 py-6 text-center text-sm text-stone-500">Ekstre veya fatura yükleyince burada görünür.</li>}
      </ul>
    </section>
  );
}
