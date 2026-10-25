import { useQuery } from "@tanstack/react-query";
import { Repeat, X } from "lucide-react";
import { cx, maskText, monthLabel, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface Suggestion {
  key: string; kind: "gelir" | "gider"; description: string; category: string | null; amount: number;
  day_of_month: number; months: number; first_month: string; last_month: string;
  history: { month: string; amount: number }[];
}

const post = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => {
    if (!r.ok) throw new Error("İşlem yapılamadı");
    return r.json();
  });

/** Hareketlerde her ay tekrarlayan ödemeler (kira, aidat, internet…): tek tıkla sabit kalem olur, planlamaya girer. */
export default function RecurringSuggestions() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["recurring-suggestions"], queryFn: () => fetch("/api/recurring/suggestions").then((r) => r.json() as Promise<Suggestion[]>) });
  const accept = useSave((s: Suggestion) => post("/api/recurring/suggestions/accept", { key: s.key }),
    () => ui.toast("Sabit kalem eklendi; geçmiş ödemeler bağlandı"));
  const dismiss = useSave((s: Suggestion) => post("/api/recurring/suggestions/dismiss", { key: s.key }));
  const items = q.data ?? [];
  if (!items.length) return null;
  return (
    <section className="card border-blue-200 p-3 sm:p-4 dark:border-blue-900">
      <h2 className="mb-1 flex items-center gap-2 font-semibold"><Repeat size={18} className="text-blue-600" /> Her ay tekrarlayanlar</h2>
      <p className="mb-2 text-xs text-stone-500">
        Hareketlerinde her ay gördüğüm ödemeler. Sabit kalem yaparsan gelecek aylar planlamaya girer, geçmiş ve sonraki
        hareketler bu kaleme bağlanır (iki kez sayılmaz).
      </p>
      <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {items.map((s) => (
          <li key={s.key} className="flex flex-wrap items-center gap-2 py-2">
            <div className="min-w-0 flex-1">
              <div className="font-medium">{maskText(s.description)}</div>
              <div className="text-xs text-stone-500">
                Her ay ~{s.day_of_month}. gün · {s.months} aydır ({monthLabel(s.first_month, true)}’den beri)
                {s.category ? ` · ${s.category}` : ""}
                <span className="block">Son aylar: {s.history.slice(-4).map((h) => tl(h.amount)).join(" · ")}</span>
              </div>
            </div>
            <span className={cx("whitespace-nowrap font-semibold tabular-nums", s.kind === "gelir" && "text-green-700 dark:text-green-400")}>
              {s.kind === "gelir" ? "+" : "−"}{tl(s.amount)}
            </span>
            <div className="flex gap-1">
              <button className="btn-primary px-2.5 py-1 text-xs" disabled={accept.isPending} onClick={() => accept.mutate(s)}>Sabit kalem yap</button>
              <button className="btn-ghost p-1.5" aria-label="Sabit değil" title="Sabit değil, bir daha önerme"
                onClick={() => dismiss.mutate(s)}><X size={14} /></button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
