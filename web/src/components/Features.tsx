import { Crown, LayoutGrid } from "lucide-react";
import { useState } from "react";
import { useUI } from "../ui";
import { useMeta, useSave } from "./hooks";

/** Hangi özellikler açık: kapatılan menüden, Özet'ten ve uyarılardan kalkar (veri silinmez). */
export default function Features({ intro = false }: { intro?: boolean }) {
  const ui = useUI();
  const meta = useMeta().data;
  const [draft, setDraft] = useState<Record<string, boolean> | null>(null);
  const [agreed, setAgreed] = useState<string[]>([]);
  const save = useSave((m: Record<string, boolean>) => fetch("/api/settings/modules", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ modules: m, consent: agreed }),
  }).then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Kaydedilemedi"); }),
  () => { setDraft(null); setAgreed([]); ui.toast("Özellikler kaydedildi"); });
  if (!meta?.modules || !meta.module_labels) return null;
  const cur = draft ?? meta.modules;
  const set = (k: string, v: boolean) => setDraft({ ...cur, [k]: v });
  const consent = meta.module_consent ?? {};
  // açılırken rıza isteyenler (hazır sürümde zekât): kutu işaretlenmeden kaydedilmez
  const pending = Object.keys(consent).filter((k) => cur[k] && !meta.modules![k]);
  const missing = pending.filter((k) => !agreed.includes(k));
  const locked = (k: string) => meta.plan?.features[k] === false; // paketinde yok (ör. fiyat takibi → Premium)
  return (
    <section className={intro ? "card space-y-3 border-blue-200 p-4 dark:border-blue-900" : "card space-y-3 p-4"}>
      <h2 className="flex items-center gap-2 font-semibold"><LayoutGrid size={18} className={intro ? "text-blue-600" : ""} />
        {intro ? "Hangi özellikleri kullanacaksın?" : "Özellikler"}</h2>
      <p className="text-sm text-stone-500">
        {intro ? "Sadece işine yarayanları açık bırak; uygulama sadeleşir. Sonradan Ayarlar → Özellikler'den değiştirebilirsin."
          : "Kapattıkların menüden, Özet'ten ve uyarılardan kalkar; veriler silinmez, tekrar açınca yerinde."}
        {" "}Özet, İşlemler ve Ekstreler her zaman açık.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {Object.entries(meta.module_labels).map(([k, v]) => (
          <label key={k} aria-label={v.label} className="flex cursor-pointer items-start gap-2 rounded-lg border border-stone-200 p-2 text-sm dark:border-stone-700">
            <input type="checkbox" className="mt-1" checked={cur[k] !== false} onChange={(e) => set(k, e.target.checked)} />
            <span><span className="font-medium">{v.label}</span>
              {locked(k) && <span className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-amber-100 px-1.5 py-px align-middle text-[10px] font-semibold text-amber-800 dark:bg-amber-900/50 dark:text-amber-300"><Crown size={10} /> Premium</span>}
              <span className="block text-xs text-stone-500">{v.desc}</span></span>
          </label>
        ))}
      </div>
      {pending.map((k) => (
        <label key={k} className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs dark:border-amber-800 dark:bg-amber-950/40">
          <input type="checkbox" className="mt-0.5" checked={agreed.includes(k)}
            onChange={(e) => setAgreed(e.target.checked ? [...agreed, k] : agreed.filter((x) => x !== k))} />
          <span><b>{meta.module_labels![k].label}:</b> {consent[k]} Bu amaçla işlenmesine açık rıza veriyorum.</span>
        </label>
      ))}
      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" disabled={save.isPending || (!intro && !draft) || missing.length > 0}
          onClick={() => save.mutate(cur, { onError: (e) => ui.toast(e.message) })}>
          {intro ? "Bu şekilde başla" : "Kaydet"}
        </button>
        {intro && <button className="btn-ghost" onClick={() => save.mutate(meta.modules!)}>
          {Object.keys(consent).length ? "Varsayılanlarla başla" : "Şimdilik hepsi açık kalsın"}</button>}
      </div>
    </section>
  );
}
