import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ChevronRight, Circle, X } from "lucide-react";
import { useUI, type Route } from "../ui";
import { useSave } from "./hooks";

interface Step { key: string; done: boolean; route: Route; action?: "add"; title: string; text: string; guide?: string[] }
interface Data { steps: Step[]; done: number; total: number; dismissed: boolean }

/** Başlarken: ilk kurulum adımları; tamamlandıkça işaretlenir, istenince kapatılır. */
export default function Onboarding() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["onboarding"], queryFn: () => fetch("/api/onboarding").then((r) => r.json() as Promise<Data>) });
  const dismiss = useSave(() => fetch("/api/onboarding/dismiss", { method: "POST" }));
  const d = q.data;
  if (!d || d.dismissed || d.done === d.total) return null;
  const next = d.steps.find((s) => !s.done);
  return (
    <section className="card p-4">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">Başlarken · {d.done}/{d.total}</h2>
          <p className="text-xs text-stone-500">Birkaç adımda kur; sonra her şey kendiliğinden işler.</p>
        </div>
        <button className="btn-ghost p-1 text-stone-400" aria-label="Rehberi kapat" title="Rehberi kapat"
          onClick={() => window.confirm("Başlarken rehberi kapatılsın mı?") && dismiss.mutate(undefined)}><X size={16} /></button>
      </div>
      <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800">
        <div className="h-full rounded-full bg-green-600" style={{ width: `${(d.done / d.total) * 100}%` }} />
      </div>
      <ul className="space-y-1">
        {d.steps.map((s) => (
          <li key={s.key}>
            <button className={"flex w-full items-start gap-2 rounded-lg p-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50"
              + (s.key === next?.key ? " bg-blue-50/60 dark:bg-blue-950/20" : "")}
              onClick={() => (s.action === "add" ? ui.openAdd() : ui.go(s.route))}>
              {s.done ? <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-green-600" /> : <Circle size={18} className="mt-0.5 shrink-0 text-stone-300" />}
              <span className="min-w-0 flex-1">
                <span className={"block text-sm font-medium" + (s.done ? " text-stone-400 line-through" : "")}>{s.title}</span>
                {(!s.done || s.key === "ekstre") && <span className="block text-xs text-stone-500">{s.text}</span>}
              </span>
              {!s.done && <ChevronRight size={16} className="mt-0.5 shrink-0 text-stone-400" />}
            </button>
            {s.key === next?.key && s.guide && (
              <div className="mb-1 ml-8 mr-2 space-y-2">
                <ol className="list-decimal space-y-1 pl-4 text-xs text-stone-600 dark:text-stone-400">
                  {s.guide.map((g) => <li key={g}>{g}</li>)}
                </ol>
                <button className="btn-primary px-3 py-1.5 text-sm" onClick={() => ui.go(s.route)}>Ekstreleri yükle</button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
