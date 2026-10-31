import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { CheckCircle2, ChevronDown, ChevronRight, Circle, X } from "lucide-react";
import { useState } from "react";
import { useUI, type Route } from "../ui";
import { useSave } from "./hooks";

interface Step { key: string; done: boolean; route: Route; action?: "add" | "salary"; cta?: string; title: string; text: string; guide?: string[] }
interface Data { steps: Step[]; done: number; total: number; dismissed: boolean }

/** Başlarken: ilk kurulum adımları; tamamlandıkça işaretlenir, istenince kapatılır. */
export default function Onboarding() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["onboarding"], queryFn: () => fetch("/api/onboarding").then((r) => r.json() as Promise<Data>) });
  const dismiss = useSave(() => fetch("/api/onboarding/dismiss", { method: "POST" }));
  // varsayılan kapalı (akordiyon); hiç adım tamamlanmamışsa (yeni kullanıcı) açık başlar
  const [open, setOpen] = useState<boolean | null>(() => { try { const v = localStorage.getItem("hk-onboarding-open"); return v == null ? null : v === "1"; } catch { return null; } });
  const d = q.data;
  if (!d || d.dismissed || d.done === d.total) return null;
  const next = d.steps.find((s) => !s.done);
  const isOpen = open ?? d.done === 0;
  const toggle = () => { const v = !isOpen; setOpen(v); try { localStorage.setItem("hk-onboarding-open", v ? "1" : "0"); } catch { /* özel mod */ } };
  const run = (s: Step) => s.action === "add" ? ui.openAdd()
    : s.action === "salary" ? ui.openEntry(undefined, { kind: "gelir", description: "Maaş", category: "Maaş", recurring: true })
    : ui.go(s.route);
  return (
    <section className="card p-4">
      <div className="mb-2 flex items-start justify-between gap-2">
        <button className="min-w-0 flex-1 text-left" onClick={toggle} aria-expanded={isOpen}>
          <h2 className="flex items-center gap-1 font-semibold">
            Başlarken · {d.done}/{d.total}
            <ChevronDown size={16} className={"text-stone-400 transition-transform" + (isOpen ? " rotate-180" : "")} />
          </h2>
          <p className="text-xs text-stone-500">{isOpen ? "Birkaç adımda kur; sonra her şey kendiliğinden işler." : `Sıradaki: ${next?.title ?? ""}`}</p>
        </button>
        <button className="btn-ghost p-1 text-stone-400" aria-label="Rehberi kapat" title="Rehberi kapat"
          onClick={() => void ask("Başlarken rehberi kapatılsın mı?\n\nAyarlar'dan yeniden açabilirsin.", { confirm: "Kapat", danger: false }).then((ok) => { if (ok) dismiss.mutate(undefined); })}><X size={16} /></button>
      </div>
      <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800">
        <div className="h-full rounded-full bg-green-600" style={{ width: `${(d.done / d.total) * 100}%` }} />
      </div>
      {isOpen && <ul className="space-y-1">
        {d.steps.map((s) => (
          <li key={s.key}>
            <button className={"flex w-full items-start gap-2 rounded-lg p-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50"
              + (s.key === next?.key ? " bg-blue-50/60 dark:bg-blue-950/20" : "")}
              onClick={() => run(s)}>
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
                <button className="btn-primary px-3 py-1.5 text-sm" onClick={() => run(s)}>{s.cta ?? "Ekstreleri yükle"}</button>
              </div>
            )}
          </li>
        ))}
      </ul>}
    </section>
  );
}
