import { useQuery } from "@tanstack/react-query";
import { FileCheck2, Minus, Plus, RefreshCw } from "lucide-react";
import { api, type RereadItem } from "../api";
import { call } from "../lib/apiError";
import { cx, maskText } from "../format";
import { OKU, okuKimlik, okuSatiri } from "../lib/yenidenOku";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Modal from "./Modal";
import SorguDurumu from "./SorguDurumu";

/** "Şimdi değil": kişiye özel; Yapılacaklar maddesi ve ekstre kartındaki bant birlikte düşer. */
export const rereadDismiss = (sid: number) => call("POST", `/api/reminders/${encodeURIComponent(okuKimlik(sid))}/dismiss`);

const ICON = { added: Plus, removed: Minus, changed: RefreshCw } as const;
const TONE = {
  added: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  removed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  changed: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
} as const;

function Item({ it }: { it: RereadItem }) {
  const Ic = ICON[it.kind];
  return (
    <li className="flex items-start gap-2.5 py-2">
      <span className={cx("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full", TONE[it.kind])} aria-hidden><Ic size={13} /></span>
      <div className="min-w-0 flex-1 text-sm">
        <div className="font-medium">{it.label}</div>
        <div className="break-words text-stone-600 dark:text-stone-300">{maskText(it.text)}</div>
        {it.note && <div className="text-xs text-stone-500 dark:text-stone-400">{it.note}</div>}
      </div>
    </li>
  );
}

/** Fark penceresi: düz dille satırlar, rakam denklemi önce / sonra, "Uygula" (geri alınabilir) ve "Şimdi değil". */
export function RereadSheet({ sid, onClose }: { sid: number; onClose: () => void }) {
  const ui = useUI();
  const q = useQuery({ queryKey: ["reread", sid], queryFn: () => api.rereadPreview(sid), staleTime: 0 });
  const p = q.data;
  const apply = useSave(() => api.rereadApply(sid, p?.sig), (r) => {
    onClose();
    ui.toast(OKU.uygulandi(r.rows), r.undo_id ? { undo: () => api.undoImport(r.undo_id!, true) } : undefined);
  });
  const later = useSave(() => rereadDismiss(sid), () => { onClose(); ui.toast(OKU.gizlendi); });
  const busy = apply.isPending || later.isPending;
  return (
    <Modal title={OKU.baslik} onClose={onClose} footer={p?.available ? <>
      <button className="btn-ghost border border-stone-200 px-3 dark:border-stone-700" disabled={busy} onClick={() => later.mutate(undefined)}>{OKU.simdiDegil}</button>
      <button className="btn-primary ml-auto px-4" disabled={busy} onClick={() => apply.mutate(undefined)} data-testid="yeniden-oku-uygula">
        <FileCheck2 size={15} aria-hidden />{OKU.uygula}</button>
    </> : undefined}>
      <SorguDurumu q={q} kompakt etiket={OKU.yukleniyor} />
      {p && !p.available && <p className="pb-2 text-sm text-stone-600 dark:text-stone-300">{OKU.yok}</p>}
      {p?.available && (
        <div className="space-y-3 pb-1">
          <div className="text-sm font-semibold">{okuSatiri(p.bank, p.last4, p.rows)}</div>
          <p className="text-sm text-stone-600 dark:text-stone-300">{OKU.aciklama}</p>
          <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 px-3 dark:divide-stone-800 dark:border-stone-700" data-testid="yeniden-oku-fark">
            {p.items.map((it, i) => <Item key={i} it={it} />)}
          </ul>
          <p className="rounded-lg bg-stone-50 p-2.5 text-xs text-stone-600 dark:bg-stone-800/60 dark:text-stone-300">
            {maskText(OKU.rakam(p.totals_before.equation_diff, p.equation_ok_after))} {OKU.korunur}</p>
        </div>
      )}
    </Modal>
  );
}

/** Ekstre kartında (detay) bant: "Ekstre yeniden okunabilir · N satır farklı" + Farkı gör / Şimdi değil. */
export function RereadBanner({ sid, bank, last4, rows, onOpen }: { sid: number; bank: string; last4: string; rows: number; onOpen: () => void }) {
  const ui = useUI();
  const later = useSave(() => rereadDismiss(sid), () => ui.toast(OKU.gizlendi), { inline: true });
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 p-2.5 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
      role="status" data-testid="yeniden-oku-bant">
      <RefreshCw size={15} className="shrink-0" aria-hidden />
      <div className="min-w-0 flex-1 basis-40">
        <div className="font-semibold">{OKU.baslik}</div>
        <div className="text-xs">{okuSatiri(bank, last4, rows)}</div>
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-1.5">
        <button className="btn-ghost px-2 text-xs" disabled={later.isPending} onClick={() => later.mutate(undefined)}>{OKU.simdiDegil}</button>
        <button className="btn-primary px-3" onClick={onOpen}>{OKU.farkiGor}</button>
      </div>
    </div>
  );
}
