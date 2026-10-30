import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, CheckCircle2, Pencil } from "lucide-react";
import { useState } from "react";
import { api, type Budget } from "../api";
import { cx, parseAmount, tl } from "../format";
import { useMeta, useSave } from "./hooks";
import Modal from "./Modal";

function state(b: Budget) {
  if (b.pct >= 100) return { icon: AlertOctagon, bar: "bg-red-600", text: "text-red-600 dark:text-red-400", label: "Aşıldı" };
  if (b.pct >= 80) return { icon: AlertTriangle, bar: "bg-amber-500", text: "text-amber-600 dark:text-amber-400", label: "Sınırda" };
  return { icon: CheckCircle2, bar: "bg-green-600", text: "text-green-700 dark:text-green-400", label: "İyi" };
}

export function BudgetList({ items, onSelect, onEdit }: { items: Budget[]; onSelect: (c: string) => void; onEdit: () => void }) {
  return (
    <section className="card p-3 sm:p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-semibold">Bütçeler</h2>
        <button className="btn-ghost text-xs" onClick={onEdit}><Pencil size={14} /> {items.length ? "Düzenle" : "Bütçe belirle"}</button>
      </div>
      {!items.length && (
        <p className="text-sm text-stone-500">Kategoriler için aylık limit koy; %80'de ve aşınca uyarı (Telegram dahil) gelsin.</p>
      )}
      <ul className="space-y-2.5">
        {items.map((b) => {
          const st = state(b);
          return (
            <li key={b.category}>
              <button className="w-full text-left" onClick={() => onSelect(b.category)}>
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="flex items-center gap-1.5 break-words">
                    <st.icon size={14} className={st.text} aria-label={st.label} /> {b.category}
                  </span>
                  <span className="shrink-0 tabular-nums">
                    <b>{tl(b.spent)}</b> <span className="text-stone-500">/ {tl(b.limit)}</span>
                  </span>
                </div>
                <div className="mt-1 h-2 rounded-full bg-stone-100 dark:bg-stone-800">
                  <div className={cx("h-2 rounded-full", st.bar)} style={{ width: `${Math.min(100, b.pct)}%` }} />
                </div>
                <div className={cx("mt-0.5 text-xs", b.remaining < 0 ? st.text : "text-stone-500")}>
                  %{b.pct.toFixed(0)} · {b.remaining >= 0 ? `${tl(b.remaining)} kaldı` : `${tl(-b.remaining)} aşıldı`}
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function BudgetForm({ month, onClose, onSaved }: { month: string; onClose: () => void; onSaved?: (m: string) => void }) {
  const meta = useMeta();
  const q = useQuery({ queryKey: ["budgets", month], queryFn: () => api.budgets(month) });
  const [values, setValues] = useState<Record<string, string>>({});
  const limits = q.data?.limits ?? {};
  const last = q.data?.last_month ?? {};
  const cats = (meta.data?.categories ?? [])
    .filter((c) => !["Kategorisiz", "Ödeme", "Banka Faiz & Ücret"].includes(c))
    .sort((a, b) => (limits[b] ? 1e12 : 0) + (last[b] ?? 0) - ((limits[a] ? 1e12 : 0) + (last[a] ?? 0)));

  const save = useSave(async () => {
    const changed = Object.entries(values);
    for (const [cat, v] of changed) {
      const n = parseAmount(v);
      await api.setBudget(cat, v.trim() && n > 0 ? n : null);
    }
    return changed.length;
  }, (n) => { onSaved?.(`${n} bütçe güncellendi`); onClose(); });

  return (
    <Modal title="Aylık bütçeler" onClose={onClose} footer={
      <>
        <div className="flex-1 text-xs text-stone-500">Boş bırak = limit yok</div>
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate(undefined)}>Kaydet</button>
      </>
    }>
      <ul className="space-y-2">
        {cats.map((c) => (
          <li key={c} className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="break-words text-sm">{c}</div>
              {last[c] ? <div className="text-xs text-stone-500">son ekstre ayı {tl(last[c])}</div> : null}
            </div>
            <input className="input w-32 text-right" inputMode="decimal" aria-label={`${c} limiti`}
              placeholder={last[c] ? String(Math.ceil(last[c] / 500) * 500) : "—"}
              value={values[c] ?? (limits[c] ? String(limits[c]) : "")}
              onChange={(e) => setValues({ ...values, [c]: e.target.value })} />
          </li>
        ))}
      </ul>
    </Modal>
  );
}
