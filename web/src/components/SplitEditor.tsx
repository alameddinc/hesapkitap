import { useQuery } from "@tanstack/react-query";
import { Plus, Scissors, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { cx, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import CategoryPicker from "./CategoryPicker";
import { useMeta, useSave } from "./hooks";

interface Part { amount: string; category: string; description: string; person: string; share: boolean }
interface Saved { id: number; amount: number; category: string; description: string | null; origin: string; person: string | null }

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
};

/** Harcamayı alt kalemlere böl: ör. 520 ₺ market fişinin 220 ₺'si Sigara. Ana kayıt kalan tutarla kendi kategorisinde kalır. */
export default function SplitEditor({ target, id, total, onDone }: {
  target: "entry" | "card"; id: number; total: number; onDone?: () => void;
}) {
  const ui = useUI();
  const meta = useMeta();
  const q = useQuery({ queryKey: ["splits", target, id], queryFn: () => json<Saved[]>("GET", `/api/splits/${target}/${id}`) });
  const [open, setOpen] = useState(false);
  const [parts, setParts] = useState<Part[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (q.data) setParts(q.data.map((p) => ({ amount: String(p.amount).replace(".", ","), category: p.person ? "" : p.category,
      description: p.description ?? "", person: p.person ?? "", share: !!p.person })));
  }, [q.data]);

  const used = parts.reduce((a, p) => a + (parseAmount(p.amount) || 0), 0);
  const rest = Math.round((total - used) * 100) / 100;
  const save = useSave(() => json("PUT", `/api/splits/${target}/${id}`, {
    parts: parts.filter((p) => parseAmount(p.amount) > 0 && (p.share ? p.person.trim() : p.category.trim()))
      .map((p) => ({ amount: parseAmount(p.amount), category: p.share ? "" : p.category.trim(), description: p.description.trim() || null,
        person: p.share ? p.person.trim() : null })),
  }), () => { ui.toast(parts.length ? "Bölündü" : "Bölme kaldırıldı"); setOpen(false); onDone?.(); });
  const set = (i: number, k: keyof Part, v: string | boolean) => setParts(parts.map((p, j) => (j === i ? { ...p, [k]: v } : p)));
  const people = useQuery({ queryKey: ["people"], queryFn: () => fetch("/api/transfers").then((r) => r.json())
    .then((t: { people: { name: string }[] }) => t.people.map((x) => x.name)) });

  const saved = q.data ?? [];
  if (!open) {
    return (
      <div className="rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
        <div className="flex items-center gap-2">
          <Scissors size={16} className="text-stone-500" />
          <span className="flex-1">
            {saved.length ? <>Bölündü: {saved.map((p) => `${p.person ? `${p.person} payı` : p.category} ${tl(p.amount)}`).join(" · ")}</>
              : <span className="text-stone-500">Bir kısmı başka kategoride ya da başkasının payı mı? (ör. sigara, ortak yemek)</span>}
          </span>
          <button type="button" className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700"
            onClick={() => { setOpen(true); if (!parts.length) setParts([{ amount: "", category: "", description: "", person: "", share: false }]); }}>
            {saved.length ? "Düzenle" : "Böl"}
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-2 rounded-lg border border-blue-200 bg-blue-50/40 p-3 dark:border-blue-900 dark:bg-blue-950/20">
      <div className="flex items-center gap-2 text-sm font-medium"><Scissors size={16} /> Alt kalemler</div>
      {parts.map((p, i) => (
        <div key={i} className="grid grid-cols-[1fr_auto] gap-2 sm:grid-cols-[7rem_1fr_1fr_auto]">
          <input className="input text-right" inputMode="decimal" placeholder="Tutar" value={p.amount}
            onChange={(e) => set(i, "amount", e.target.value)} />
          <button type="button" className="btn-ghost p-2 text-stone-400 hover:text-red-600 sm:order-last" aria-label="Kaldır"
            onClick={() => setParts(parts.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
          <div className="col-span-2 space-y-1 sm:col-span-1">
            <div className="flex gap-1 text-xs">
              <button type="button" className={cx("rounded px-1.5 py-0.5", !p.share ? "bg-stone-800 text-white dark:bg-stone-200 dark:text-stone-900" : "text-stone-500")}
                onClick={() => set(i, "share", false)}>Kategori</button>
              <button type="button" className={cx("rounded px-1.5 py-0.5", p.share ? "bg-stone-800 text-white dark:bg-stone-200 dark:text-stone-900" : "text-stone-500")}
                onClick={() => set(i, "share", true)}>Kişinin payı</button>
            </div>
            {p.share
              ? <CategoryPicker value={p.person} onChange={(v) => set(i, "person", v)} options={people.data ?? []} placeholder="Kim? (ona alacak açılır)" />
              : <CategoryPicker value={p.category} onChange={(v) => set(i, "category", v)} options={meta.data?.categories ?? []} placeholder="Kategori" />}
          </div>
          <input className="input col-span-2 sm:col-span-1" placeholder="Not (ör. 2× Marlboro)" value={p.description}
            onChange={(e) => set(i, "description", e.target.value)} />
        </div>
      ))}
      <button type="button" className="btn-ghost px-2 py-1 text-xs"
        onClick={() => setParts([...parts, { amount: rest > 0 ? String(rest).replace(".", ",") : "", category: "", description: "", person: "", share: false }])}>
        <Plus size={14} /> Kalem ekle
      </button>
      <p className={cx("text-xs", rest < 0 ? "text-red-600" : "text-stone-500")}>
        Toplam {tl(total)} · ayrılan {tl(used)} · ana kategoride kalan <b>{tl(rest)}</b>
      </p>
      {parts.some((p) => p.share) && (
        <p className="text-xs text-stone-500">Kişinin payı senin giderin sayılmaz; o kişiye alacak açılır, havale gelince kendiliğinden kapanır.</p>
      )}
      {err && <p className="text-xs text-red-600">{err}</p>}
      <div className="flex gap-2">
        <button type="button" className="btn-primary" disabled={rest < 0 || save.isPending}
          onClick={() => { setErr(null); save.mutate(undefined, { onError: (e) => setErr(e.message) }); }}>Kaydet</button>
        <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>Vazgeç</button>
        {saved.length > 0 && (
          <button type="button" className="btn-ghost ml-auto text-red-600"
            onClick={() => { setParts([]); json("PUT", `/api/splits/${target}/${id}`, { parts: [] }).then(() => { q.refetch(); setOpen(false); onDone?.(); ui.toast("Bölme kaldırıldı"); }); }}>
            Bölmeyi kaldır
          </button>
        )}
      </div>
    </div>
  );
}
