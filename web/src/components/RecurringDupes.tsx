import { useQuery } from "@tanstack/react-query";
import { Copy, GitMerge } from "lucide-react";
import { api, type RecurringDup } from "../api";
import { maskText } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

/** Mobil ile aynı metinler (hesapkitap-mobile src/components/planlama/SabitTekrar.tsx). */
export const DUP_TEXT = {
  merge: "Birleştir",
  separate: "Ayrı kalsın",
  merged: (drop: string, keep: string) => `Birleştirildi · “${drop}” kaldırıldı, ödemeler “${keep}” kaleminde`,
  separated: "Tamam, ikisi ayrı sabit kalem olarak kalır; bir daha sormayız",
};

/** "İki sabit kalem aynı olabilir": Birleştir (geri alınabilir) / Ayrı kalsın (bir daha sorulmaz). Asla kendiliğinden. */
export function useDupActions() {
  const ui = useUI();
  const merge = useSave((p: { keep: number; drop: number; keepName: string; dropName: string }) => api.mergeRecurringDup(p.keep, p.drop),
    (res, p) => ui.toast(DUP_TEXT.merged(p.dropName, p.keepName), res.undo_id ? { undo: () => api.undoImport(res.undo_id!, true) } : undefined));
  const separate = useSave((p: { a: number; b: number }) => api.separateRecurringDup(p.a, p.b), () => ui.toast(DUP_TEXT.separated));
  return {
    busy: merge.isPending || separate.isPending,
    merge: (keep: number, drop: number, keepName: string, dropName: string, onError?: (e: Error) => void) =>
      merge.mutate({ keep, drop, keepName, dropName }, onError ? { onError } : undefined),
    separate: (a: number, b: number, onError?: (e: Error) => void) => separate.mutate({ a, b }, onError ? { onError } : undefined),
  };
}

/** Planlama → Sabit kalemler'in üstünde küçük uyarı (Özet'teki Yapılacaklar maddesiyle aynı veri ve metin). */
export default function RecurringDupBanner() {
  const q = useQuery({ queryKey: ["recurring-duplicates"], queryFn: api.recurringDuplicates, retry: false });
  const a = useDupActions();
  const items: RecurringDup[] = q.data ?? [];
  if (!items.length) return null;
  return (
    <div className="mb-3 space-y-2">
      {items.map((p) => (
        <div key={p.id} role="note" className="flex flex-wrap items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-900 dark:bg-amber-950/40">
          <Copy size={16} className="mt-0.5 shrink-0 text-amber-700 dark:text-amber-400" aria-hidden="true" />
          <div className="min-w-0 flex-1 basis-56">
            <p className="font-medium break-words">{maskText(p.title)}</p>
            <p className="text-xs break-words text-stone-600 dark:text-stone-400">{maskText(p.text)}</p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <button className="btn-primary px-3" disabled={a.busy} onClick={() => a.merge(p.keep.id, p.drop.id, p.keep.description, p.drop.description)}>
              <GitMerge size={14} />{DUP_TEXT.merge}</button>
            <button className="btn-ghost border border-stone-300 px-3 dark:border-stone-700" disabled={a.busy}
              onClick={() => a.separate(p.drop.id, p.keep.id)}>{DUP_TEXT.separate}</button>
          </div>
        </div>
      ))}
    </div>
  );
}
