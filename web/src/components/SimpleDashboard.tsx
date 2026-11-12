import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { gorunumAcildi } from "../lib/gorunum";
import { TERIM, faizGeliriNotu } from "../lib/terimler";
import TerimBilgi from "./TerimBilgi";
import { CheckCircle2, Camera, ChevronRight, List, Minimize2 } from "lucide-react";
import { api, type Summary } from "../api";
import { donemObegi, ozetFaizMetni } from "../lib/faizOzet";
import { addMonths, dateTR, tl, periodLabel } from "../format";
import { useUI } from "../ui";
import { payableKey } from "../lib/odeme";
import { EstimateNote } from "./Estimates";
import { isViewer, useBook, useMeta, useUiPrefs } from "./hooks";

/** Sade görünüm: büyük yazı, tek bakışta "bu ay ne kadar harcadım" (kuruşu kuruşuna), yaklaşan ödemeler, iki düğme. */
export default function SimpleDashboard() {
  const ui = useUI();
  const { save } = useUiPrefs();
  const viewer = isViewer(useBook().data); // ortak defterde sadece görür
  const meta = useMeta().data;
  const summary = useQuery({ queryKey: ["summary", ui.month], queryFn: () => api.summary(ui.month) });
  const prevMonth = addMonths(ui.month, -1);
  const prev = useQuery({ queryKey: ["summary", prevMonth], queryFn: () => api.summary(prevMonth) });
  const payables = useQuery({ queryKey: ["payables"], queryFn: api.payables });
  useEffect(() => { if (meta) gorunumAcildi("sade", meta.capabilities); }, [meta]); // ölçüm: yalnız toplam
  const t = summary.data?.totals;
  const due = (payables.data ?? []).filter((p) => p.status !== "odendi").slice(0, 4);
  return (
    <div className="space-y-5">
      <section className="card p-6 text-center">
        <div className="text-lg text-stone-500">{periodLabel(ui.month)} · bu ay harcadığın</div>
        <div className="mt-1 text-[min(3rem,12vw)] font-bold whitespace-nowrap tabular-nums tracking-tight">{t ? tl(t.expense) : "…"}</div>
        {prev.data && <div className="mt-1 text-base text-stone-500">Geçen ay ({periodLabel(prevMonth)}): {tl(prev.data.totals.expense)}</div>}
        {summary.data && <div className="text-left"><EstimateNote summary={summary.data} /></div>}
        {t && (
          <div className="mt-4 grid grid-cols-1 gap-3 text-left min-[440px]:grid-cols-2">
            <div className="rounded-xl bg-green-50 p-3 dark:bg-green-950/30">
              <div className="text-base text-stone-500">Gelen</div>
              <div className="text-2xl font-semibold whitespace-nowrap tabular-nums text-green-700 dark:text-green-400">{tl(t.income)}</div>
              {(t.interest_income ?? 0) > 0 && <div className="mt-0.5 text-sm text-amber-700 dark:text-amber-400">{faizGeliriNotu(tl(t.interest_income!))}</div>}
            </div>
            <div className="rounded-xl bg-stone-50 p-3 dark:bg-stone-800/50">
              <div className="text-base text-stone-500">{TERIM.eldeKalan.ad} <TerimBilgi id="eldeKalan" metin={summary.data?.definitions?.left_after_savings} /></div>
              <div className={`text-2xl font-semibold whitespace-nowrap tabular-nums ${(t.left_after_savings ?? t.net - t.savings) < 0 ? "text-red-600" : ""}`}>{tl(t.left_after_savings ?? t.net - t.savings)}</div>
            </div>
          </div>
        )}
        {!!t?.savings && <p className="mt-3 text-lg text-amber-700 dark:text-amber-400">Birikime giden (altın/döviz): {tl(t.savings)}</p>}
        {/* faiz ve ücret: sunucu interest_paid (dokununca Faiz ve ücretler); eski sunucuda eski satır */}
        {summary.data?.interest_paid !== undefined ? <SadeFaiz s={summary.data} past={ui.month < (meta?.current_month ?? ui.month)}
          donem={ui.month < (meta?.current_month ?? ui.month) ? donemObegi(true, summary.data.period?.name) : "Bu ay"} />
          : !!t?.fees && <p className="mt-3 text-lg text-red-600">Bu ay {tl(t.fees)} faiz / ücret ödendi</p>}
      </section>

      <div className="grid grid-cols-2 gap-3">
        <button className="btn-primary flex-col justify-center gap-1 py-4 text-lg" disabled={viewer} onClick={ui.openAdd}><Camera size={26} /> Ekle</button>
        <button className="btn-ghost flex-col justify-center gap-1 whitespace-nowrap border border-stone-300 py-4 text-lg dark:border-stone-700"
          onClick={() => ui.go("islemler")}><List size={26} /> Harcamalar</button>
      </div>

      {due.length > 0 && (
        <section className="card p-4">
          <h2 className="mb-2 text-xl font-semibold">Yaklaşan ödemeler</h2>
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {due.map((p) => (
              <li key={payableKey(p)} className="flex items-center justify-between gap-3 py-3 text-lg">
                <span className="min-w-0">
                  <span className="block break-words font-medium">{p.title}</span>
                  <span className={`text-base ${p.status === "gecikti" ? "text-red-600" : "text-stone-500"}`}>
                    {p.status === "gecikti" ? "gecikti · " : ""}son gün {dateTR(p.due_date)}{p.status === "tahmini" ? " · tahmini" : ""}
                    {p.in_total === false && p.rolled_into ? " · kalanı sonraki ekstreye devretti" : ""}
                  </span>
                </span>
                <span className="shrink-0 font-semibold tabular-nums">{tl(p.amount - (p.paid_amount ?? 0))}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <button className="btn-ghost mx-auto flex text-base text-stone-500" onClick={() => save.mutate({ simple: false })}>
        <Minimize2 size={18} /> Tam görünüme geç
      </button>
    </div>
  );
}

/** Sade görünümde faiz satırı: büyük yazı; içinde bulunulan dönemde son ekstreler ("Son ekstrelerinde faiz ve ücret",
 *  altında kaynak ekstreler), geçmiş dönemde dönem metni; dokununca Faiz ve ücretler. Kart ekstresi yoksa satır yok. */
function SadeFaiz({ s, past, donem }: { s: Summary; past: boolean; donem: string }) {
  const ui = useUI();
  const m = s.totals.fees_hidden ? null : ozetFaizMetni(s, tl, past, donem);
  const kaynak = !past && s.interest_latest !== undefined;
  if (!m || m.tone === "neutral") return null;
  return (
    <button data-testid="sade-faiz" onClick={() => ui.go("faiz")}
      className={`mt-3 flex w-full items-center gap-2 rounded-xl p-3 text-left text-lg ${m.tone === "ok" ? "bg-green-50 text-green-800 dark:bg-green-950/30 dark:text-green-300" : "bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300"}`}>
      {m.tone === "ok" && <CheckCircle2 size={22} className="shrink-0" />}
      <span className="min-w-0 flex-1">{m.title}
        {kaynak && <span data-testid="sade-faiz-kaynak" className="block text-base opacity-80">{m.sub}</span>}</span>
      {m.value && <span className="shrink-0 font-semibold whitespace-nowrap tabular-nums">{m.value}</span>}
      <ChevronRight size={20} className="shrink-0 opacity-60" />
    </button>
  );
}
