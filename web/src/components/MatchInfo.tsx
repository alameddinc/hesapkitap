import { Landmark, Link2, Loader2, PenLine, Undo2 } from "lucide-react";
import { ask } from "../lib/ask";
import { useState } from "react";
import { api, type EntryMatch } from "../api";
import { cx, dateTR, tl } from "../format";
import { isViewer, useBook, useSave } from "./hooks";

const MON = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
/** "2026-10-28" → "28 Eki" */
export const shortDay = (iso: string) => {
  const [, m, d] = iso.slice(0, 10).split("-").map(Number);
  return m && d ? `${d} ${MON[m - 1]}` : iso;
};

const HOW: Record<EntryMatch["how"], string> = {
  otomatik: "tarih ve tutar birebir tuttuğu için kendiliğinden eşleşti",
  onay: "“Aynı” dediğin için eşleşti",
  kural: "öğrenilen eşleşmeyle kendiliğinden eşleşti",
  elle: "elle eşleştirdin",
};

/** "Dökümle eşleşti · Garanti · 28 Eki" */
export const matchLabel = (m: EntryMatch) =>
  [m.source === "dokum" ? "Dökümle eşleşti" : "Ekstreyle eşleşti", m.account, shortDay(m.date)].filter(Boolean).join(" · ");

/** İşlem listesindeki küçük rozet. */
export function MatchBadge({ m }: { m: EntryMatch }) {
  return (
    <span className="inline-flex items-center gap-1 rounded bg-green-100 px-1.5 py-0.5 font-medium text-green-800 dark:bg-green-950 dark:text-green-300"
      title={`${matchLabel(m)} · bankadaki: ${m.description}`}>
      <Link2 size={11} aria-hidden="true" />
      {m.source === "dokum" ? "Dökümle eşleşti" : "Ekstreyle eşleşti"}
    </span>
  );
}

/** Kayıt detayında: bankadaki karşılığı ve "Eşleşmeyi geri al". */
export function MatchPanel({ entryId, m, onUndone }: { entryId: number; m: EntryMatch; onUndone: (msg: string) => void }) {
  const viewer = isViewer(useBook().data);
  const [err, setErr] = useState<string | null>(null);
  const undo = useSave(() => api.unmatchEntry(entryId), () =>
    onUndone(m.source === "dokum" ? "Eşleşme geri alındı · dökümdeki satır ayrı kayıt oldu" : "Eşleşme geri alındı · ekstredeki işlem ayrı sayılıyor"));
  const where = m.source === "dokum" ? "Dökümde" : "Ekstrede";
  const run = async () => {
    const what = m.source === "dokum" ? "dökümdeki satır" : "ekstredeki işlem";
    if (!(await ask(`Eşleşme geri alınsın mı?\n\nElle girdiğin kayıt ile ${what} yeniden iki ayrı kayıt olur ve ikisi de sayılır. Bu ikisi bir daha kendiliğinden birleşmez.`))) return;
    setErr(null);
    undo.mutate(undefined, { onError: (e) => setErr(e.message) });
  };
  return (
    <section aria-label="Banka eşleşmesi" className="rounded-xl border border-green-200 bg-green-50/70 p-3 text-sm dark:border-green-900/70 dark:bg-green-950/30">
      <div className="flex items-start gap-2 text-green-800 dark:text-green-300">
        <Link2 size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="font-medium">{matchLabel(m)}</div>
          <div className="text-xs opacity-80">İki kez sayılmıyor · {HOW[m.how]}</div>
        </div>
      </div>
      <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-lg bg-white/80 px-3 py-2 text-xs dark:bg-stone-900/60">
        <dt className="text-stone-500">{where}</dt>
        <dd className="min-w-0 font-medium break-words text-stone-800 dark:text-stone-200">{m.description}</dd>
        <dt className="text-stone-500">Tarih</dt>
        <dd className="tabular-nums">{dateTR(m.date)}</dd>
        <dt className="text-stone-500">Tutar</dt>
        <dd className="tabular-nums">{tl(Math.abs(m.amount))}</dd>
        {m.balance != null && <>
          <dt className="text-stone-500">Bakiye</dt>
          <dd className="tabular-nums">{tl(m.balance)}</dd>
        </>}
      </dl>
      {err && <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400">{err}</p>}
      {m.can_undo && !viewer && (
        <button type="button" className="btn-ghost mt-2 border border-green-300 px-2.5 py-1 text-xs dark:border-green-900"
          disabled={undo.isPending} onClick={run}>
          {undo.isPending ? <Loader2 size={14} className="animate-spin" /> : <Undo2 size={14} />} Eşleşmeyi geri al
        </button>
      )}
    </section>
  );
}

export interface PairSide { description: string; amount: number; date: string; account?: string | null }

/** "Bu ikisi aynı mı?": solda elle girilen, sağda dökümdeki/ekstredeki; farklı olan alan vurgulanır. */
export function PairCards({ entry, other, source, reason }: {
  entry: PairSide; other: PairSide; source: "dokum" | "ekstre"; reason: "tarih" | "tutar" | "aday";
}) {
  const amtDiff = Math.abs(Math.abs(entry.amount) - Math.abs(other.amount)) >= 0.005;
  const dayDiff = entry.date.slice(0, 10) !== other.date.slice(0, 10);
  const side = (s: PairSide, label: string, Ic: typeof Landmark) => (
    <div className="min-w-0 flex-1 rounded-lg border border-stone-200 bg-stone-50/80 px-2.5 py-2 dark:border-stone-700 dark:bg-stone-800/50">
      <div className="flex items-center gap-1 text-[11px] font-medium tracking-wide text-stone-500 uppercase dark:text-stone-400">
        <Ic size={11} aria-hidden="true" />{label}
      </div>
      <div className="mt-0.5 line-clamp-2 text-xs font-medium break-words" title={s.description}>{s.description}</div>
      <div className="mt-1 flex flex-wrap items-baseline justify-between gap-x-2 text-xs">
        <span className={cx("font-semibold tabular-nums", amtDiff && "rounded bg-amber-100 px-1 text-amber-900 dark:bg-amber-900/50 dark:text-amber-200")}>
          {tl(Math.abs(s.amount))}</span>
        <span className={cx("tabular-nums text-stone-500", dayDiff && "rounded bg-amber-100 px-1 text-amber-900 dark:bg-amber-900/50 dark:text-amber-200")}>
          {shortDay(s.date)}</span>
      </div>
      {s.account && <div className="mt-0.5 truncate text-[11px] text-stone-400">{s.account}</div>}
    </div>
  );
  return (
    <div className="mt-2 flex items-stretch gap-1.5" aria-label={reason === "aday" ? "Birden çok aday var" : undefined}>
      {side(entry, "Elle girdiğin", PenLine)}
      <span className="self-center font-display text-sm font-semibold text-stone-400" aria-hidden="true">≟</span>
      {side(other, source === "dokum" ? "Dökümde" : "Ekstrede", Landmark)}
    </div>
  );
}
