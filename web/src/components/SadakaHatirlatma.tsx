import { useQuery } from "@tanstack/react-query";
import { Bell, Check } from "lucide-react";
import { call } from "../lib/apiError";
import { cx } from "../format";
import { useUI } from "../ui";
import { routeVisible, useBook, useCap, useMeta, useSave } from "./hooks";

/** GET /api/donations/reminder (sunucu yeteneği sadaka_reminder). Metinler sunucunun: web ve mobil aynı. */
export interface SadakaReminder {
  available: boolean;
  rhythm: "haftalik" | "aylik" | "kapali";
  day: number;
  chosen: boolean;
  /** ritim Her Cuma / Ayda bir: Özet kartı */
  show: boolean;
  show_streak: boolean;
  streak: number;
  /** "4 Cumadır sadakan kayıtlı" (en az 2 dönem ve "Sürekliliği göster" açıksa) */
  streak_text: string | null;
  last_date: string | null;
  days_since: number | null;
  in_period: boolean;
  title: string | null;
  text: string | null;
  tone: "ok" | "nudge" | "empty" | null;
  next_reminder: string | null;
  when: string | null;
  choices: { rhythm: SadakaReminder["rhythm"]; label: string; hint: string; recommended: boolean }[];
  /** seçim yoksa sadaka kaydedilince "Her Cuma hatırlatalım mı?" */
  ask_after_save: boolean;
  ask_title: string;
}

export const REMINDER_URL = "/api/donations/reminder";

/** Zekât ve Sadaka açık ve görünürse (ortak defter üyesine gizli değil) ve sunucu biliyorsa; değilse null. */
export function useSadakaReminder() {
  const on = useCap("sadaka_reminder");
  const book = useBook().data;
  const meta = useMeta().data;
  const visible = !!meta && routeVisible(book, "zekat", meta.modules);
  const q = useQuery({ queryKey: ["sadaka-reminder"], queryFn: () => call<SadakaReminder>("GET", REMINDER_URL), enabled: on && visible, retry: false });
  return on && visible ? q : null;
}

/** Üç seçenek (sunucunun sırası: Her Cuma önerilen / Ayda bir / Hatırlatma). Seçince kaydedilir. */
export function RitimSecici({ d, onDone }: { d: SadakaReminder; onDone?: () => void }) {
  const ui = useUI();
  const save = useSave((rhythm: string) => call<SadakaReminder>("PUT", REMINDER_URL, { rhythm }),
    (r) => { ui.toast(r.rhythm === "kapali" ? "Sadaka hatırlatması kapalı" : `Kaydedildi · ${r.when}`); onDone?.(); });
  const cur = d.chosen ? d.rhythm : null;
  return (
    <div className="space-y-2" role="radiogroup" aria-label="Sadaka hatırlatması">
      {d.choices.map((c) => (
        <button key={c.rhythm} type="button" role="radio" aria-checked={cur === c.rhythm} disabled={save.isPending}
          onClick={() => save.mutate(c.rhythm)}
          className={cx("flex w-full items-center gap-3 rounded-xl border p-3 text-left pointer-coarse:min-h-14",
            cur === c.rhythm ? "border-blue-500 bg-blue-50 dark:border-blue-500 dark:bg-blue-950/40" : "border-stone-200 hover:bg-stone-50 dark:border-stone-700 dark:hover:bg-stone-800/50")}>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2 font-medium">{c.label}
              {c.recommended && !d.chosen && <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-medium text-blue-800 dark:bg-blue-900/60 dark:text-blue-200">Önerilen</span>}</span>
            <span className="block text-xs text-stone-500">{c.hint}</span>
          </span>
          {cur === c.rhythm && <Check size={18} className="shrink-0 text-blue-600 dark:text-blue-400" aria-hidden />}
        </button>
      ))}
    </div>
  );
}

/** Sadaka kaydedildikten sonra (seçim yapılmadıysa) formun son adımı: "Her Cuma hatırlatalım mı?". */
export function KayitSonrasiSoru({ d, onDone }: { d: SadakaReminder; onDone: () => void }) {
  return (
    <div className="space-y-3" data-testid="sadaka-sonra-soru">
      <p className="flex items-center gap-2 text-sm text-green-700 dark:text-green-400"><Check size={16} aria-hidden /> Kaydedildi.</p>
      <div>
        <h3 className="font-semibold">{d.ask_title}</h3>
        <p className="text-xs text-stone-500">O dönemde sadaka kaydın varsa hatırlatmayız. Sonradan Sadaka sekmesinden değiştirebilirsin.</p>
      </div>
      <RitimSecici d={d} onDone={onDone} />
    </div>
  );
}

/** Zekât ve Sadaka → Sadaka sekmesi: ritim ve "Sürekliliği göster". */
export function SadakaHatirlatmaAyari() {
  const q = useSadakaReminder();
  const streak = useSave((on: boolean) => call<SadakaReminder>("PUT", REMINDER_URL, { show_streak: on }));
  const d = q?.data;
  if (!d || !d.available) return null;
  return (
    <section className="card space-y-3 p-4" data-testid="sadaka-hatirlatma-ayari">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-medium"><Bell size={16} aria-hidden /> Sadaka hatırlatması</h3>
        <p className="text-xs text-stone-500">
          {d.rhythm === "kapali" ? "Kapalı. Açarsan Özet'te son sadakandan beri geçen süre görünür; telefona Cuma sabahı hatırlatma gelir."
            : `${d.when} · o dönemde sadaka kaydın varsa hatırlatılmaz. Telefonda Ayarlar → Bildirimler'den de değişir.`}</p>
        {d.show && d.streak_text && <p className="mt-1 text-sm text-green-700 dark:text-green-400" data-testid="sadaka-sureklilik">{d.streak_text}</p>}
      </div>
      <RitimSecici d={d} />
      {d.rhythm !== "kapali" && (
        <label className="flex items-center justify-between gap-3 text-sm pointer-coarse:min-h-11">
          <span>Sürekliliği göster<span className="block text-xs text-stone-500">"4 Cumadır sadakan kayıtlı" gibi; yalnız sana görünür</span></span>
          <input type="checkbox" className="size-5 shrink-0 accent-blue-600" checked={d.show_streak} disabled={streak.isPending}
            onChange={(e) => streak.mutate(e.target.checked)} />
        </label>
      )}
    </section>
  );
}
