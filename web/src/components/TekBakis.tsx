// Sade Özet'in üstü ("Tek bakış"; düzende "sade"): tek büyük rakam (Harcanabilir) + dönem sonu tahmini, gerekiyorsa tek
// satırlık uyarı şeridi ve en önemli 3 satır (Bu ay ödenecek · Bu dönem harcadın · Son ekstrelerinde faiz). Gerisi
// Özet'in kapalı "Ayrıntılar" bölümünde (Ayrintilar). Rakamların hepsi sunucudan: net-worth.spendable,
// summary.expected.forecast_net, payables totals.this_period, summary.totals.expense / vs_prev, summary.interest_latest.
// Mobil components/ozet/TekBakis.tsx ile aynı metinler.
import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, BadgePercent, CalendarClock, CheckCircle2, ChevronDown, ChevronRight, Receipt } from "lucide-react";
import type { ReactNode } from "react";
import { api, type Insight, type Summary } from "../api";
import { call } from "../lib/apiError";
import { areAmountsHidden, cx, dayMonthTR, maskText, tl } from "../format";
import { DONEM_SONU, donemSonuVar, eksiMi, isaretli, tamTl } from "../lib/beklenen";
import { ozetFaizMetni } from "../lib/faizOzet";
import { planOdakla } from "../lib/planOdak";
import { TERIM } from "../lib/terimler";
import { useUI } from "../ui";
import { routeVisible, useBook, useMeta } from "./hooks";
import TerimBilgi from "./TerimBilgi";

/** Şeride çıkan uyarılar: kritik düzeydekiler (son ödeme günü geçmiş kart, gecikmiş ödeme, aşılan bütçe…). Faiz alarmı
 *  şeride çıkmaz: 3. satır (Son ekstrelerinde faiz) aynı bilgi. */
export const seritUyarilari = (items: Insight[]) => items.filter((i) => i.level === "kritik" && !/^(faiz|vade_farki):/.test(i.key ?? ""));

const para = (n: number) => (areAmountsHidden() ? tl(n) : tamTl(n));

/** Ayrıntılar'daki bir bileşene kay (kapalıysa önce açılır). */
export type Kaydir = (id: string) => void;

export default function TekBakis({ s, kiyas, kaydir }: { s: Summary; kiyas: { text: string; up: boolean } | null; kaydir: Kaydir }) {
  const ui = useUI();
  const meta = useMeta().data;
  const book = useBook().data;
  const hesaplar = routeVisible(book, "hesaplar", meta?.modules);
  const plan = routeVisible(book, "plan", meta?.modules);
  const nw = useQuery({ queryKey: ["net-worth"], queryFn: () => call<{ spendable: number; spendable_explain?: string }>("GET", "/api/net-worth"), enabled: hesaplar });
  // Özet'in Ödemeler bileşeniyle aynı önbellek (liste + sunucu toplamları)
  const pt = useQuery({ queryKey: ["payables", "totals", 15], queryFn: () => api.payablesWithTotals(15) });
  const e = s.expected;
  const tahmin = donemSonuVar(e) && meta?.modules?.plan !== false ? e : null;
  const serit = seritUyarilari(s.insights);
  const bu = pt.data?.totals?.this_period;
  const faiz = s.totals.fees_hidden ? null : ozetFaizMetni(s, tl, false, "Bu dönem");
  const sp = nw.data?.spendable;

  return (
    <section data-testid="tek-bakis" className="space-y-3">
      <div className="card p-4 sm:p-5">
        {hesaplar ? (<>
          <div className="flex items-center gap-1 text-sm text-stone-500 dark:text-stone-400">
            {TERIM.harcanabilir.ad} <TerimBilgi id="harcanabilir" metin={nw.data?.spendable_explain} />
          </div>
          {sp != null ? (
            <button data-testid="tek-bakis-harcanabilir" onClick={() => ui.go("hesaplar")} aria-label={`${TERIM.harcanabilir.ad} ${tl(sp)}: Hesaplar`}
              className={cx("mt-1 block max-w-full text-left font-display text-[2.6rem] leading-none font-semibold tracking-tight tabular-nums whitespace-nowrap",
                sp < 0 && "text-red-600 dark:text-red-400")}>{tl(sp)}</button>
          ) : <div className="mt-1 h-10 w-56 animate-pulse rounded-lg bg-stone-100 dark:bg-stone-800" />}
        </>) : (<>
          <div className="text-sm text-stone-500 dark:text-stone-400">Bu dönem harcadığın</div>
          <div className="mt-1 font-display text-[2.6rem] leading-none font-semibold tracking-tight tabular-nums whitespace-nowrap">{tl(s.totals.expense)}</div>
          {kiyas && <div className={cx("mt-1 text-sm", kiyas.up ? "text-amber-700 dark:text-amber-400" : "text-green-700 dark:text-green-400")}>{kiyas.text}</div>}
        </>)}
        {tahmin ? (
          <button data-testid="tek-bakis-tahmin" disabled={!plan} onClick={() => { planOdakla(tahmin.month); ui.go("plan"); }}
            aria-label={`Dönem sonu tahmini ${isaretli(tahmin.forecast_net, para)} her zamanki harcamanla${plan ? `: ${DONEM_SONU.planlama}` : ""}`}
            className="mt-2 block text-left text-sm text-stone-600 dark:text-stone-400">
            dönem sonu tahmini <b className={cx("font-semibold whitespace-nowrap tabular-nums", eksiMi(tahmin.forecast_net) ? "text-red-600 dark:text-red-400" : "text-green-700 dark:text-green-400")}>
              {isaretli(tahmin.forecast_net, para)}</b> {DONEM_SONU.her.toLocaleLowerCase("tr")}
          </button>
        ) : hesaplar && sp != null ? (
          <div className="mt-2 text-sm text-stone-500 dark:text-stone-400">{sp < 0 ? `Ödeyeceklerin eldeki paradan ${tl(-sp)} fazla` : TERIM.harcanabilir.kisa}</div>
        ) : null}
      </div>

      {/* önemli durum gizlenmez: varsa 3 satırın üstünde tek satırlık şerit; dokununca Ayrıntılar'daki uyarı */}
      {serit.length > 0 && (
        <button data-testid="tek-bakis-uyari" onClick={() => kaydir("uyarilar")}
          className="flex w-full items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-left text-sm font-medium text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          <AlertOctagon size={18} className="shrink-0" />
          <span className="min-w-0 flex-1 break-words">{maskText(serit[0]!.title)}</span>
          {serit.length > 1 && <span className="shrink-0 text-xs font-semibold tabular-nums">+{serit.length - 1}</span>}
          <ChevronRight size={16} className="shrink-0 opacity-70" />
        </button>
      )}

      <div className="card divide-y divide-stone-100 overflow-hidden dark:divide-stone-800" aria-label="En önemli 3 satır">
        {bu && (
          <Satir testId="tek-bakis-odenecek" icon={<CalendarClock size={18} />} tone="pri" onClick={() => kaydir("odemeler")}
            title="Bu ay ödenecek" value={tl(bu.total)}
            sub={bu.count === 0 ? "Dönem sonuna kadar ödeme yok" : (<>
              {[`${bu.count} ödeme`, bu.next_due ? `en yakını ${dayMonthTR(bu.next_due)}` : null].filter(Boolean).join(" · ")}
              {bu.overdue > 0 && <span className="font-medium text-red-600 dark:text-red-400"> · {tl(bu.overdue)} günü geçti</span>}
            </>)} />
        )}
        {hesaplar && (
          <Satir testId="tek-bakis-harcadin" icon={<Receipt size={18} />} tone="pri" onClick={() => ui.go("islemler")}
            title="Bu dönem harcadın" value={tl(s.totals.expense)} sub={kiyas?.text ?? "Kart, elle ve fiş harcamaların"}
            subCls={kiyas ? (kiyas.up ? "text-amber-700 dark:text-amber-400" : "text-green-700 dark:text-green-400") : undefined} />
        )}
        {faiz && faiz.tone !== "neutral" && (
          <Satir testId="tek-bakis-faiz" icon={faiz.tone === "ok" ? <CheckCircle2 size={18} /> : <BadgePercent size={18} />} tone={faiz.tone === "ok" ? "ok" : "warn"}
            onClick={() => ui.go("faiz")} title={faiz.title} value={faiz.value} valueCls="text-red-600 dark:text-red-400" sub={faiz.sub} />
        )}
      </div>
    </section>
  );
}

const TON = {
  pri: "bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
  warn: "bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400",
  ok: "bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-400",
} as const;

function Satir({ testId, icon, tone, onClick, title, value, valueCls, sub, subCls }: {
  testId: string; icon: ReactNode; tone: keyof typeof TON; onClick: () => void; title: string; value: string | null; valueCls?: string; sub: ReactNode; subCls?: string;
}) {
  return (
    <button data-testid={testId} onClick={onClick} aria-label={`${title}${value ? ` ${value}` : ""}`}
      className="flex w-full items-center gap-3 px-3 py-3 text-left active:bg-stone-50 sm:px-4 dark:active:bg-stone-800">
      <span className={cx("flex size-9 shrink-0 items-center justify-center rounded-xl", TON[tone])}>{icon}</span>
      {/* telefonda tutar başlığın altında (dar ekranda alt metni sıkıştırmasın), geniş ekranda sağda */}
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{title}</span>
        {value && <span className={cx("block text-lg font-semibold whitespace-nowrap tabular-nums sm:hidden", valueCls)}>{value}</span>}
        <span className={cx("block text-xs tabular-nums", subCls ?? "text-stone-500 dark:text-stone-400")}>{sub}</span>
      </span>
      {value && <span className={cx("hidden shrink-0 font-semibold whitespace-nowrap tabular-nums sm:block", valueCls)}>{value}</span>}
      <ChevronRight size={16} className="shrink-0 text-stone-400" />
    </button>
  );
}

const ACIK_KEY = "hk-ozet-ayrintilar";
export const ayrintiAcikMi = () => { try { return localStorage.getItem(ACIK_KEY) === "1"; } catch { return false; } };
export const ayrintiKaydet = (v: boolean) => { try { localStorage.setItem(ACIK_KEY, v ? "1" : "0"); } catch { /* özel pencere */ } };

/** Sade Özet'in "Ayrıntılar" başlığı: kapalıyken içindekilerin adı; açık/kapalı tarayıcıda hatırlanır (varsayılan kapalı). */
export function AyrintiBaslik({ open, onToggle, names }: { open: boolean; onToggle: () => void; names: string[] }) {
  const ozet = names.length > 3 ? `${names.slice(0, 3).join(", ")} · +${names.length - 3} bölüm` : names.join(", ");
  return (
    <button data-testid="ozet-ayrintilar" aria-expanded={open} onClick={onToggle}
      className="card flex w-full items-center gap-3 p-3 text-left active:bg-stone-50 sm:p-4 dark:active:bg-stone-800">
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">Ayrıntılar</span>
        {!open && ozet && <span className="block text-xs text-stone-500 dark:text-stone-400">{ozet}</span>}
      </span>
      <ChevronDown size={18} className={cx("shrink-0 text-stone-400 transition-transform", open && "rotate-180")} />
    </button>
  );
}
