import { useQuery } from "@tanstack/react-query";
import { History, Undo2 } from "lucide-react";
import { useState } from "react";
import { api, type ImportBatch } from "../api";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import { parseTs } from "../format";

const NAMES: Record<string, string> = {
  entries: "kayıt", transactions: "kart işlemi", statements: "ekstre", accounts: "hesap",
  own_transfers: "hesaplar arası aktarım", card_payments: "kart ödemesi", recurring: "sabit kalem",
};

function describe(b: ImportBatch) {
  const parts: string[] = [];
  for (const [t, c] of Object.entries(b.counts)) {
    const n = NAMES[t];
    if (!n) continue;
    if (c.added) parts.push(`${c.added} ${n} eklendi`);
    if (c.changed) parts.push(`${c.changed} ${n} güncellendi`);
    if (c.removed) parts.push(`${c.removed} ${n} silindi`);
  }
  return parts.join(" · ") || "küçük değişiklikler";
}

/** Son yüklemeler: her biri tek tıkla geri alınır (yüklemenin eklediği/değiştirdiği her şey eski haline döner). */
export default function RecentImports() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["imports"], queryFn: api.imports });
  const undo = useSave(({ id, force }: { id: string; force?: boolean }) => api.undoImport(id, force));

  const run = (b: ImportBatch) => {
    if (!confirm(`"${b.label}" yüklemesi geri alınsın mı?\n\n${describe(b)}\n\nBu yüklemenin eklediği her şey silinir, değiştirdiği her şey (bakiye, eşleşmeler) eski haline döner.`)) return;
    undo.mutate({ id: b.id }, {
      onSuccess: (r) => {
        if (r.ok) return ui.toast("Yükleme geri alındı");
        if (confirm(`Bu yüklemeden sonra ${r.conflicts?.length} satırı elle değiştirmişsin (ör. kategori). Geri alırsan o değişiklikler de gider. Yine de geri alınsın mı?`))
          undo.mutate({ id: b.id, force: true }, { onSuccess: () => ui.toast("Yükleme geri alındı") });
      },
    });
  };

  const items = (q.data ?? []).slice(0, 8);
  const [openedAt] = useState(() => Date.now()); // son 1 saatte yükleme varsa açık başlar
  if (!items.length) return null;
  return (
    <details className="card p-3 text-sm" open={items.some((b) => !b.undone_at && openedAt - parseTs(b.created_at).getTime() < 3600e3)}>
      <summary className="flex cursor-pointer items-center gap-2 font-medium">
        <History size={16} className="text-stone-500" /> Son yüklemeler
        <span className="text-xs font-normal text-stone-500">yanlış bir şey mi yükledin? geri al</span>
      </summary>
      <ul className="mt-2 divide-y divide-stone-100 dark:divide-stone-800">
        {items.map((b) => (
          <li key={b.id} className="flex items-center gap-3 py-2">
            <div className={b.undone_at ? "min-w-0 flex-1 text-stone-400 line-through" : "min-w-0 flex-1"}>
              <div className="truncate">{b.label}</div>
              <div className="text-xs text-stone-500">
                {parseTs(b.created_at).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" })} · {describe(b)}
              </div>
            </div>
            {b.undone_at ? <span className="text-xs text-stone-500">geri alındı</span> : (
              <button className="btn-ghost flex items-center gap-1 whitespace-nowrap border border-stone-200 dark:border-stone-700" disabled={undo.isPending} onClick={() => run(b)}>
                <Undo2 size={14} /> Geri al
              </button>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
