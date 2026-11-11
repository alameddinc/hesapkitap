import { useQuery } from "@tanstack/react-query";
import { BookOpen, ChevronDown } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { cx } from "../format";
import { kalinParcalar } from "../lib/kalin";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";

const ICON: Record<string, string> = { card: "💳", bank: "🏦" };

/** **kalın** işaretli sunucu metni */
export function Kalin({ s }: { s: string }) {
  return <>{kalinParcalar(s).map((p, i) => (p.bold ? <b key={i}>{p.text}</b> : <span key={i}>{p.text}</span>))}</>;
}

/** Ekstreler: kart ekstresi ve hesap dökümü nereden, nasıl indirilir. İçerik sunucuda (GET /api/statement-help,
 *  app/statement_help.py): mobil "Ekstre nasıl indirilir?" ekranıyla aynı kaynak. */
export default function StatementHelp() {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem("hk-stmt-help") !== "0"; } catch { return true; } });
  const toggle = () => { setOpen(!open); try { localStorage.setItem("hk-stmt-help", open ? "0" : "1"); } catch { /* özel mod */ } };
  const q = useQuery({ queryKey: ["statement-help"], queryFn: api.statementHelp, staleTime: Infinity, enabled: open });
  const d = q.data;
  return (
    <section className="card p-4 text-sm" data-testid="ekstre-rehberi">
      <button className="flex w-full items-center gap-2 text-left font-semibold" onClick={toggle} aria-expanded={open}>
        <BookOpen size={18} /> {d?.title ?? "Ekstre ve hesap dökümü nasıl indirilir?"}
        <ChevronDown size={16} className={cx("ml-auto text-stone-400 transition-transform", open && "rotate-180")} />
      </button>
      {open && !d && <div className="mt-3"><SorguDurumu q={q} kompakt etiket="Rehber yükleniyor"><SatirIskelet n={2} /></SorguDurumu></div>}
      {open && d && (
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          {d.sections.map((s) => (
            <div key={s.key}>
              <h3 className="font-medium">{ICON[s.icon] ? `${ICON[s.icon]} ` : ""}{s.title}</h3>
              <ol className="mt-1 list-decimal space-y-1 pl-5 text-stone-600 dark:text-stone-400">
                {s.steps.map((x, i) => <li key={i}><Kalin s={x.text} /></li>)}
              </ol>
            </div>
          ))}
          {d.tips.length > 0 && (
            <div className="space-y-1 text-xs text-stone-500 md:col-span-2">
              {d.tips.map((t, i) => <p key={i}><Kalin s={t.text} /></p>)}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
