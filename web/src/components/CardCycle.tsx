import type { CardCycle } from "../api";
import { dateTR, tl } from "../format";

/** "Bugüne kadar 8.420 ₺ (taksit · abonelik · harcama) · kesim 13.11 (19 gün) · kesimde ~14.000 ₺" */
export default function CycleLine({ c }: { c: CardCycle }) {
  const parts = [
    c.installments > 0 && `taksit ${tl(c.installments)}`,
    c.subscriptions > 0 && `abonelik ${tl(c.subscriptions)}`,
    c.spent > 0 && `girilen harcama ${tl(c.spent)}`,
  ].filter(Boolean);
  return (
    <span className="block text-xs text-stone-500">
      Bugüne kadar <b className="text-stone-700 dark:text-stone-300">{tl(c.so_far)}</b>
      {parts.length > 0 && ` (${parts.join(" · ")})`}
      {" · "}kesim {dateTR(c.cut).slice(0, 5)}{c.days_left > 0 ? ` (${c.days_left} gün)` : ""} · kesimde tahmini <b>~{tl(c.at_cut)}</b>
      {c.unassigned > 0 && (
        <span className="block text-amber-600">{tl(c.unassigned)} harcamanın hangi karttan olduğu seçilmemiş; bu karta sayıldı.</span>
      )}
    </span>
  );
}
