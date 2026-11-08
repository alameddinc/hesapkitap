// Özet → "Beklenen" satırları (mobil screens/Ozet.tsx ile aynı metinler; lib/beklenen.ts). Rakamlar sunucudan
// (summary.expected = Planlama'nın o ayki satırı); geçmiş dönemde expected null → hiçbiri görünmez.
import type { Summary } from "../api";
import { areAmountsHidden, cx, tl } from "../format";
import { BEKLENEN_PLANLAMA, beklenenArtan, beklenenEksi, beklenenGider, tamTl } from "../lib/beklenen";
import { useUI } from "../ui";
import { routeVisible, useBook, useMeta } from "./hooks";
import TerimBilgi from "./TerimBilgi";

/** tam lira; tutarlar gizliyse maske */
const para = (n: number) => (areAmountsHidden() ? tl(n) : tamTl(n));

/** Gider altında: "Ekstre gelince ~₺137.166 (i) · Planlamada gör". hero: telefondaki mavi kart. */
export function BeklenenGider({ summary, hero }: { summary: Summary; hero?: boolean }) {
  const ui = useUI();
  const meta = useMeta().data;
  const book = useBook().data;
  const text = beklenenGider(summary.expected, para);
  if (!text) return null;
  const plan = routeVisible(book, "plan", meta?.modules);
  return (
    <div data-testid="beklenen-gider" className={cx("mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs tabular-nums",
      hero ? "text-blue-100" : "text-stone-600 dark:text-stone-300")}>
      <span className="font-medium">{text}</span>
      <TerimBilgi id="beklenen" metin={summary.definitions?.expected} className={hero ? "text-blue-100" : undefined} />
      {plan && (
        // nokta bağlantıyla birlikte alt satıra iner (satır sonunda tek başına kalmaz)
        <span className="inline-flex items-center gap-x-1.5 whitespace-nowrap">
          <span aria-hidden>·</span>
          <button type="button" className={cx("underline underline-offset-2", hero ? "text-white" : "text-blue-700 dark:text-blue-400")}
            onClick={() => ui.go("plan")}>{BEKLENEN_PLANLAMA}</button>
        </span>
      )}
    </div>
  );
}

/** Artan altında: "Beklenen: +₺77.921" (eksiyse kırmızı, Artan gibi). */
export function BeklenenArtan({ summary, hero }: { summary: Summary; hero?: boolean }) {
  const text = beklenenArtan(summary.expected, para);
  if (!text || !summary.expected) return null;
  const eksi = beklenenEksi(summary.expected);
  return (
    <div data-testid="beklenen-artan" className={cx("mt-0.5 text-xs font-medium tabular-nums",
      hero ? (eksi ? "text-rose-200" : "text-blue-100")
        : eksi ? "text-red-600 dark:text-red-400" : "text-green-700 dark:text-green-400")}>
      {text}
    </div>
  );
}
