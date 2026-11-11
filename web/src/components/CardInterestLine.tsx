import { AlertTriangle, Info } from "lucide-react";
import type { CardInterest } from "../api";
import { cx, maskText } from "../format";

/** Kartın son ekstresinde faiz satırı (yetenek card_interest): metin ve tutar sunucudan (borç planıyla aynı hesap).
 *  Son ödeme günü geçtiyse "Kalan ₺X borca bu ay yaklaşık ₺Y faiz ve vergi eklenir; …", kısmi ödemede "Kalanı son ödeme
 *  gününe kadar öde: …". before_due bu satırda gösterilmez (listedeki "tamamını ödersen faiz işlemez" kalır). Mobil
 *  screens/Kartlar.tsx aynı metin. */
export default function CardInterestLine({ i, className }: { i?: CardInterest | null; className?: string }) {
  if (!i || i.state === "before_due") return null;
  const bad = i.state === "after_due";
  const Icon = bad ? AlertTriangle : Info;
  return (
    <span role="note" data-testid="kart-faiz" className={cx("mt-1 flex gap-1.5 rounded-md px-2 py-1 text-xs",
      bad ? "bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-100" : "bg-amber-50 text-amber-950 dark:bg-amber-950/40 dark:text-amber-50", className)}>
      <Icon size={13} className="mt-0.5 shrink-0" aria-hidden />
      <span className="min-w-0">{maskText(i.text)}{i.rate_note && <span className="block opacity-80">{i.rate_note}</span>}</span>
    </span>
  );
}
