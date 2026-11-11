import { useQuery } from "@tanstack/react-query";
import { BadgePercent, CalendarClock, CheckCircle2, ChevronRight, Layers, Sparkles } from "lucide-react";
import { useState } from "react";
import { api, type FirstLook as FirstLookData } from "../api";
import { dateTR, tl } from "../format";
import { useUI } from "../ui";
import { BudgetForm } from "./Budgets";
import { useMeta } from "./hooks";

/** "8 gün kaldı" / "bugün" / "yarın" */
export const kalanGun = (d: number) => (d <= 0 ? "bugün" : d === 1 ? "yarın" : `${d} gün kaldı`);

/**
 * İlk gün özeti (sunucu GET /api/first-look; app/first_look.py): kullanıcının ilk haftasında kart ekstresi yüklenince
 * yükleme sonucunun üstünde tek kart — bu ay ödenecekler (tutar + son ödeme günü), bu ekstrelerde ödenen faiz/ücret,
 * gelecek taksitler, sonraki adımlar. first=false ise hiç görünmez (normal sonuç). Tutarlar sunucuda; asgari ödeme yok.
 */
export default function FirstLook({ ids, onUpload }: { ids: number[]; onUpload: () => void }) {
  const ui = useUI();
  const meta = useMeta().data;
  const on = !!meta?.capabilities?.includes("first_look");
  const key = [...ids].sort((a, b) => a - b).join(",");
  const q = useQuery({ queryKey: ["first-look", key], queryFn: () => api.firstLook(ids), enabled: on });
  const [budget, setBudget] = useState(false);
  const d = q.data;
  if (!on || !d || !d.first || !d.statements.length) return null;
  return (
    <section className="card space-y-4 border-blue-200 p-4 dark:border-blue-900" data-testid="ilk-gun" aria-labelledby="ilk-gun-baslik">
      <div>
        <div className="flex items-center gap-2 text-xs font-medium tracking-wide text-blue-700 uppercase dark:text-blue-400"><Sparkles size={14} /> İlk bakış</div>
        <h2 id="ilk-gun-baslik" className="mt-1 text-lg font-semibold">{d.statements.length > 1 ? `${d.statements.length} kartın hazır` : "Ekstren hazır"}: işte özetin</h2>
      </div>
      <FirstLookBody d={d} />
      {d.steps.filter((s) => s.platform !== "mobile").length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-stone-500">Sonraki adım</h3>
          <ul className="space-y-2">
            {d.steps.filter((s) => s.platform !== "mobile").map((s) => (
              <li key={s.key}>
                <button className="flex w-full items-center gap-3 rounded-xl border border-stone-200 p-3 text-left text-sm active:bg-stone-50 dark:border-stone-700 dark:active:bg-stone-800"
                  onClick={() => (s.key === "dokum" ? onUpload() : s.key === "butce" ? setBudget(true) : undefined)}>
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium">{s.title}</span>
                    <span className="block text-xs text-stone-500 dark:text-stone-400">{s.text}</span>
                  </span>
                  <ChevronRight size={16} className="shrink-0 text-stone-400" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {budget && <BudgetForm month={ui.month} onClose={() => setBudget(false)} onSaved={(m) => { ui.toast(m); void q.refetch(); }} />}
    </section>
  );
}

/** Üç bölüm: ödenecekler, faiz ve ücret, taksitler. Boş bölüm yazılmaz (ödenecek yoksa "Ödenecek ekstre yok"). */
function FirstLookBody({ d }: { d: FirstLookData }) {
  const ui = useUI();
  const row = "flex items-start gap-3";
  const ic = "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg";
  return (
    <div className="space-y-3 text-sm">
      <div className={row}>
        <span className={`${ic} bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-400`}><CalendarClock size={16} /></span>
        <div className="min-w-0 flex-1">
          <div className="font-medium">Bu ay ödenecek</div>
          {d.to_pay.items.length ? (
            <>
              <ul className="mt-1 space-y-1">
                {d.to_pay.items.map((i) => (
                  <li key={i.statement_id} className="flex flex-wrap items-baseline justify-between gap-x-3">
                    <span className="min-w-0 break-words">{i.card} · son ödeme {dateTR(i.due_date)} <span className="text-stone-500">({kalanGun(i.days_left)})</span></span>
                    <b className="tabular-nums">{tl(i.amount)}</b>
                  </li>
                ))}
              </ul>
              {d.to_pay.items.length > 1 && <div className="mt-1 flex justify-between border-t border-stone-100 pt-1 dark:border-stone-800"><span>Toplam</span><b className="tabular-nums">{tl(d.to_pay.total)}</b></div>}
              {d.to_pay.note && <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">{d.to_pay.note}</p>}
            </>
          ) : <div className="text-stone-500 dark:text-stone-400">Son ödeme günü gelmemiş ödenecek ekstre yok.</div>}
          {d.past_due > 0 && <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">{d.past_due} ekstrenin son ödeme günü geçmiş; ödediysen Ödemeler'de işaretle.</p>}
        </div>
      </div>
      {d.fees && (
        <button className={`${row} w-full rounded-lg text-left active:bg-stone-50 dark:active:bg-stone-800`} onClick={() => ui.go("faiz")}>
          <span className={`${ic} ${d.fees.total > 0 ? "bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400" : "bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-400"}`}>
            {d.fees.total > 0 ? <BadgePercent size={16} /> : <CheckCircle2 size={16} />}
          </span>
          <span className="min-w-0 flex-1">
            {d.fees.total > 0 ? (
              <>
                <span className="flex justify-between gap-3 font-medium"><span>Bu ekstrede ödenen faiz ve ücret</span><b className="tabular-nums text-red-600 dark:text-red-400">{tl(d.fees.total)}</b></span>
                <span className="block text-xs text-stone-500 dark:text-stone-400">{d.fees.by_type.map((t) => `${t.label} ${tl(t.total)}`).join(" · ")}</span>
              </>
            ) : <span className="font-medium">Bu ekstrede faiz ya da ücret yok</span>}
          </span>
        </button>
      )}
      <div className={row}>
        <span className={`${ic} bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-400`}><Layers size={16} /></span>
        <div className="min-w-0 flex-1">
          {d.installments.total > 0 ? (
            <>
              <div className="flex justify-between gap-3 font-medium"><span>Gelecek taksitler</span><b className="tabular-nums">{tl(d.installments.total)}</b></div>
              <div className="text-xs text-stone-500 dark:text-stone-400">
                {d.installments.count} alışveriş · {d.installments.months} ay
                {d.installments.next && <> · {d.installments.next.label} ekstresine {tl(d.installments.next.amount)}</>}
                {d.installments.last_label && d.installments.months > 1 && <> · son taksit {d.installments.last_label}</>}
              </div>
            </>
          ) : <div className="font-medium">Gelecek aylara taksit yok</div>}
        </div>
      </div>
    </div>
  );
}
