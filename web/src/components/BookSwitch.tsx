import { BookOpen, ChevronDown, Users2 } from "lucide-react";
import { cx } from "../format";
import { useBook, useSwitchBook } from "./hooks";

/** Ortak defter üyesi: "Ortak defter · <sahip>" ile "Benim defterim" arasında geçiş (sahipte görünmez). */
export default function BookSwitch({ compact }: { compact?: boolean }) {
  const b = useBook().data;
  const go = useSwitchBook();
  if (!b?.member || b.can_switch === false) return null;
  const mine = b.book === "kendi";
  const shared = `Ortak defter · ${b.owner ?? ""}`;

  if (compact) {  // mobil üst çubuk: tek düğme, dokununca diğer deftere geçer
    return (
      <button className="btn-ghost flex max-w-[45vw] items-center gap-1 px-2 py-1 text-xs"
        title={mine ? `${shared} defterine geç` : "Benim defterime geç"}
        onClick={() => go(mine ? "ortak" : "kendi")}>
        {mine ? <BookOpen size={14} /> : <Users2 size={14} />}
        <span className="truncate">{mine ? "Benim defterim" : b.owner}</span>
        <ChevronDown size={12} className="shrink-0 text-stone-400" />
      </button>
    );
  }
  return (
    <div className="space-y-0.5" role="radiogroup" aria-label="Defter">
      {([["ortak", shared, Users2], ["kendi", "Benim defterim", BookOpen]] as const).map(([k, label, Icon]) => (
        <button key={k} type="button" role="radio" aria-checked={(k === "kendi") === mine}
          onClick={() => (k === "kendi") !== mine && go(k)}
          className={cx("flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-xs",
            (k === "kendi") === mine ? "bg-blue-50 font-medium text-blue-700 dark:bg-blue-950/40 dark:text-blue-300"
              : "text-stone-500 hover:bg-stone-50 dark:hover:bg-stone-800/50")}>
          <Icon size={14} className="shrink-0" /> <span className="truncate">{label}</span>
        </button>
      ))}
      {!mine && b.role_label && <p className="px-2 text-[11px] text-stone-400">Yetkin: {b.role_label}</p>}
    </div>
  );
}
