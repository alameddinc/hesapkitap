import { useQuery } from "@tanstack/react-query";
import { Flag, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, monthLabel, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface Target {
  id: number; name: string; amount: number; deadline: string; saved: number; left: number; pct: number;
  months_left: number; need_per_month: number; pace: number; eta: string | null; on_track: boolean;
}

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
};

/** Birikim hedefi: "Haziran'a kadar 100 bin" → biriken, ayda gereken, bu hızla ne zaman. */
export default function Targets() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["targets"], queryFn: () => json<Target[]>("GET", "/api/targets") });
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [deadline, setDeadline] = useState("");
  const [already, setAlready] = useState("");
  const add = useSave(() => json("POST", "/api/targets", { name, amount: parseAmount(amount), deadline, already: parseAmount(already) || 0 }),
    () => { ui.toast("Hedef eklendi"); setAdding(false); setName(""); setAmount(""); setDeadline(""); setAlready(""); });
  const del = useSave((id: number) => json("DELETE", `/api/targets/${id}`), () => ui.toast("Silindi"));
  const items = q.data ?? [];
  return (
    <section className="card p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold"><Flag size={18} /> Birikim hedefleri</h2>
        {!adding && <button className="btn-ghost px-2 py-1 text-sm" onClick={() => setAdding(true)}><Plus size={16} /> Hedef</button>}
      </div>
      {adding && (
        <div className="mb-3 space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
          <input className="input" placeholder="Ne için? (ör. Tatil, Araba peşinatı)" value={name} onChange={(e) => setName(e.target.value)} />
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs"><span className="label">Tutar</span>
              <input className="input text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /></label>
            <label className="text-xs"><span className="label">Son tarih</span>
              <input type="date" className="input" value={deadline} onChange={(e) => setDeadline(e.target.value)} /></label>
          </div>
          <label className="block text-xs"><span className="label">Şu an bu iş için ayırdığın (opsiyonel)</span>
            <input className="input text-right" inputMode="decimal" value={already} onChange={(e) => setAlready(e.target.value)} /></label>
          <div className="flex gap-2">
            <button className="btn-primary" disabled={!name.trim() || !(parseAmount(amount) > 0) || !deadline || add.isPending}
              onClick={() => add.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Kaydet</button>
            <button className="btn-ghost" onClick={() => setAdding(false)}>Vazgeç</button>
          </div>
        </div>
      )}
      <ul className="space-y-3">
        {items.map((t) => (
          <li key={t.id}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-medium">{t.name}</span>
              <span className="text-sm tabular-nums">{tl(t.saved)} / {tl(t.amount)}</span>
            </div>
            <div className="mt-1 h-2 overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800">
              <div className={cx("h-full rounded-full", t.on_track ? "bg-green-600" : "bg-amber-500")} style={{ width: `${t.pct}%` }} />
            </div>
            <div className="mt-1 flex items-start justify-between gap-2 text-xs text-stone-500">
              <span>
                {t.left <= 0 ? "Hedefe ulaştın 🎉" : <>
                  son tarih {dateTR(t.deadline)} ({t.months_left} ay) · ayda <b>{tl(t.need_per_month)}</b> ayırman gerek ·{" "}
                  {t.pace <= 0 ? <span className="text-amber-600">son 3 dönemde artı kalmadı</span>
                    : t.on_track ? <span className="text-green-700 dark:text-green-400">son 3 dönem ortalaman {tl(t.pace)} · tahmini ulaşma: {t.eta && monthLabel(t.eta)}</span>
                    : <span className="text-amber-600">son 3 dönem ortalaman {tl(t.pace)} · bu hızla tahmini ulaşma: {
                      t.eta && Number(t.eta.slice(0, 4)) - Number(t.deadline.slice(0, 4)) <= 3 ? monthLabel(t.eta) : "yetişmiyor"}</span>}
                </>}
              </span>
              <button className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label="Sil"
                onClick={() => window.confirm(`${t.name} hedefi silinsin mi?`) && del.mutate(t.id)}><Trash2 size={13} /></button>
            </div>
          </li>
        ))}
        {!items.length && !adding && <li className="text-sm text-stone-500">ör. "Haziran'a kadar tatil için 100 bin": ayda ne kadar ayırman gerektiğini ve bu hızla ne zaman ulaşacağını gösterir.</li>}
      </ul>
    </section>
  );
}
