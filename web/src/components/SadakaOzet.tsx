import { Check, HeartHandshake } from "lucide-react";
import { useState } from "react";
import { cx } from "../format";
import { SadakaEkleFormu } from "./Sadaka";
import { useSadakaReminder } from "./SadakaHatirlatma";

/** Özet bileşeni "sadaka": ritim (Her Cuma / Ayda bir) seçiliyse son sadakadan beri geçen süre, süreklilik ve "Sadaka ver"
 *  (sadaka formunu açar). Seçim yoksa, ritim kapalıysa ya da özellik kapalıysa hiçbir şey. */
export default function SadakaOzetKarti() {
  const q = useSadakaReminder();
  const [form, setForm] = useState(false);
  const d = q?.data;
  if (!d || !d.show || !d.title) return null;
  const ok = d.tone === "ok";
  return (
    <section className="card flex items-center gap-3 p-3" data-testid="ozet-sadaka">
      <span aria-hidden className={cx("flex size-9 shrink-0 items-center justify-center rounded-lg",
        ok ? "bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-400" : "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-400")}>
        {ok ? <Check size={18} /> : <HeartHandshake size={18} />}</span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">{d.title}</span>
        <span className="block text-xs text-stone-500">{d.text}</span>
        {d.streak_text && <span className="block text-xs text-green-700 dark:text-green-400">{d.streak_text}</span>}
      </span>
      <button className={cx("shrink-0 px-3 py-1.5 text-sm", ok ? "btn-ghost" : "btn-primary")} onClick={() => setForm(true)}>Sadaka ver</button>
      {form && <SadakaEkleFormu onClose={() => setForm(false)} />}
    </section>
  );
}
