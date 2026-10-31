import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { send } from "../lib/apiError";
import { Share2, Trash2 } from "lucide-react";
import { useEffect } from "react";
import { tl } from "../format";
import { useUI } from "../ui";
import { docToDraft, type DocResult } from "./AddSheet";
import { useSave } from "./hooks";

interface Item { id: number; filename: string; kind: "receipt" | "bill"; doc: DocResult; created_at: string }

/** Telefondan paylaşılan fiş/faturalar: okundu, onay bekliyor. Dokununca form dolu açılır. */
export default function SharedInbox() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["inbox"], queryFn: () => send<Item[]>("/api/inbox") });
  const close = useSave((a: { id: number; action: "kaydedildi" | "silindi" }) => fetch(`/api/inbox/${a.id}/${a.action}`, { method: "POST" }));
  // Android paylaşımından dönüşte sonucu göster (#/ozet?paylasim=…)
  useEffect(() => {
    const m = window.location.hash.match(/paylasim=([^&]*)/);
    if (m) {
      ui.toast(decodeURIComponent(m[1]) || "Paylaşıldı");
      window.history.replaceState(null, "", window.location.hash.replace(/\?paylasim=[^&]*/, ""));
    }
  }, [ui]);
  if (!q.data?.length) return null;
  const open = (i: Item) => {
    close.mutate({ id: i.id, action: "kaydedildi" });
    if (i.kind === "bill") {
      const d = i.doc;
      ui.openBill({ provider: d.provider, amount: d.amount ?? undefined, due_date: d.due_date ?? undefined, category: d.category,
        description: d.description, file_name: d.file_name, confidence: d.confidence });
    } else ui.openEntry(undefined, docToDraft(i.doc));
  };
  return (
    <section className="card p-3">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold"><Share2 size={16} /> Telefondan paylaşılanlar · onay bekliyor</h2>
      <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {q.data.map((i) => (
          <li key={i.id} className="flex items-center justify-between gap-2 py-2">
            <button className="min-w-0 flex-1 text-left" onClick={() => open(i)}>
              {i.kind === "bill" ? "🧾 " : "🛍 "}
              <b>{i.kind === "bill" ? i.doc.provider : i.doc.merchant}</b>
              {" · "}{tl((i.kind === "bill" ? i.doc.amount : i.doc.total) ?? 0)}
              {(i.doc.installments ?? 1) > 1 && ` · ${i.doc.installments} taksit`}
              <span className="block text-xs text-stone-500">Dokun: kontrol et ve kaydet</span>
            </button>
            <button className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label="Sil" disabled={close.isPending}
              onClick={() => void ask("Paylaşılan belge silinsin mi?\n\nKaydedilmeden atılır.").then((ok) => { if (ok) close.mutate({ id: i.id, action: "silindi" }); })}><Trash2 size={14} /></button>
          </li>
        ))}
      </ul>
    </section>
  );
}
