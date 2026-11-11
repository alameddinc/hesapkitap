import { BadgePercent, CheckCircle2, ChevronRight } from "lucide-react";
import type { Summary } from "../api";
import { cx, tl } from "../format";
import { donemObegi, faizKartMetni } from "../lib/faizOzet";
import { useUI } from "../ui";

/** Özet bileşeni "faiz-ucret": "Bu dönem ödenen faiz ve ücret ₺X" (0 ise "Bu dönem faiz ödemedin"); dokununca Faiz ve
 *  ücretler sayfası. Tutar sunucuda (summary.interest_paid); ortak defterde faiz gizliyse sunucu null döner → kart yok.
 *  Faiz GELİRİ ayrı ("içinde faiz geliri ₺X · arındırılacak", Gelir kutusunun altında). */
export default function FaizOzetKarti({ summary, past }: { summary: Summary; past: boolean }) {
  const ui = useUI();
  const m = faizKartMetni(summary.interest_paid, tl, donemObegi(past, summary.period?.name));
  if (!m) return null;
  return (
    <button data-testid="faiz-ozet" className="card flex w-full items-center gap-3 p-3 text-left active:bg-stone-50 dark:active:bg-stone-800"
      onClick={() => ui.go("faiz")} aria-label={`${m.title}${m.value ? ` ${m.value}` : ""}. Faiz ve ücretler sayfasını aç`}>
      <span className={cx("flex size-9 shrink-0 items-center justify-center rounded-xl",
        m.tone === "warn" ? "bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400"
          : m.tone === "ok" ? "bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-400"
            : "bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400")}>
        {m.tone === "ok" ? <CheckCircle2 size={18} /> : <BadgePercent size={18} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{m.title}</span>
        <span className="block text-xs text-stone-500 dark:text-stone-400">{m.sub}</span>
      </span>
      {m.value && <span className="shrink-0 font-semibold tabular-nums text-red-600 dark:text-red-400">{m.value}</span>}
      <ChevronRight size={16} className="shrink-0 text-stone-400" />
    </button>
  );
}
