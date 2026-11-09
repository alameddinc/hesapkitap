import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, RotateCcw, Save, TableProperties } from "lucide-react";
import { useState } from "react";
import type { AdminPlanTable, Tier } from "../api";
import { cx } from "../format";
import { ask } from "../lib/ask";
import { call } from "../lib/apiError";
import { useUI } from "../ui";
import { useMeta, useSave } from "./hooks";
import { TIER_LABEL, TIERS } from "./Plan";
import Segmented from "./Segmented";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";

const KEY = ["admin-plan-table"];
const KIND_TEXT = { sayfa: "sayfa", ozellik: "özellik" } as const;

/** Yönetici: hangi sayfa / özellik hangi paketten itibaren açık (tek tablo, sunucuda; deploy gerekmez).
 *  Kendi sunucusunda paket yoktur: yalnız not gösterilir. */
export default function PlanTable() {
  const meta = useMeta().data;
  const hosted = !!meta?.plan?.hosted;
  const ui = useUI();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: KEY, queryFn: () => call<AdminPlanTable>("GET", "/api/admin/plan-table"), enabled: hosted });
  const [draft, setDraft] = useState<Record<string, Tier>>({});
  const [yearly, setYearly] = useState<boolean | null>(null);
  const done = (r: AdminPlanTable, msg: string) => {
    qc.setQueryData(KEY, r);
    setDraft({});
    setYearly(null);
    void qc.invalidateQueries({ queryKey: ["meta"] }); // menü kilitleri ve paket karşılaştırması tazelensin
    ui.toast(msg);
  };
  const d = q.data;
  const changes = Object.fromEntries(Object.entries(draft).filter(([k, v]) => d?.rows.find((r) => r.key === k)?.tier !== v));
  const dirty = Object.keys(changes).length > 0 || (yearly != null && yearly !== d?.upgrade_yearly);
  const save = useSave(() => call<AdminPlanTable>("PUT", "/api/admin/plan-table", {
    tiers: changes, ...(yearly != null ? { upgrade_yearly: yearly } : {}),
  }), (r) => done(r, "Paket tablosu kaydedildi"));
  const reset = useSave(() => call<AdminPlanTable>("PUT", "/api/admin/plan-table", { reset: true }),
    (r) => done(r, "Bütün satırlar varsayılana döndü"));
  const busy = save.isPending || reset.isPending;
  const yearlyOn = yearly ?? d?.upgrade_yearly ?? false;
  const changed = d?.rows.some((r) => r.tier !== r.default);

  if (!meta) return null;
  return (
    <section className="card space-y-3 p-4" aria-labelledby="plan-table-h">
      <h2 id="plan-table-h" className="flex items-center gap-2 font-semibold"><TableProperties size={18} aria-hidden="true" /> Paket tablosu</h2>
      {!hosted ? (
        <p className="text-sm text-stone-500">Kendi sunucunda paket yok; bu tablo yalnız hazır sürümde uygulanır. Burada her şey herkese açık.</p>
      ) : <>
        <p className="text-xs text-stone-500">
          Her satır seçtiğin paketten itibaren açık. "Özellik" satırlarını sunucu da uygular; "sayfa" satırları menüde kilit
          ve açıklama olarak görünür. Kaydedince birkaç saniye içinde herkese geçer. Kayıt ekleme hiçbir pakette kilitlenmez.
        </p>
        <SorguDurumu q={q} etiket="Plan tablosu yükleniyor"><SatirIskelet n={5} ikon={false} kart={false} /></SorguDurumu>
        {d && <>
          <p className="rounded-lg bg-stone-50 p-2 text-xs text-stone-600 dark:bg-stone-800/60 dark:text-stone-400">
            Aylık okuma hakları (koddan): {d.tiers.map((t) => `${d.labels[t] ?? TIER_LABEL[t]} ${d.reads[t].belge} belge / ${d.reads[t].fis} fiş${t === "aile" ? " (kişi başı)" : ""}`).join(" · ")}
            {d.packs.length > 0 && <> · Ek paket {d.packs.map((p) => `+${p.reads}`).join(" / ")} (telefondaki uygulamadan)</>}
          </p>
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {d.rows.map((r) => {
              const cur = draft[r.key] ?? r.tier;
              const unsaved = cur !== r.tier;
              return (
                <li key={r.key} className="flex flex-col gap-2 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0 text-sm">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium">{r.label}</span>
                      <span className="rounded bg-stone-100 px-1.5 py-px text-[10px] text-stone-600 dark:bg-stone-800 dark:text-stone-400">{KIND_TEXT[r.kind]}</span>
                      {cur !== r.default && <span className="rounded bg-amber-100 px-1.5 py-px text-[10px] font-medium text-amber-800 dark:bg-amber-900/50 dark:text-amber-300">
                        varsayılan: {d.labels[r.default] ?? TIER_LABEL[r.default]}</span>}
                      {unsaved && <span className="text-[10px] font-medium text-blue-700 dark:text-blue-400">kaydedilmedi</span>}
                    </div>
                    {r.desc && <p className="text-xs text-stone-500">{r.desc}</p>}
                  </div>
                  {r.editable ? (
                    <div className="w-full shrink-0 sm:w-72">
                      <Segmented<Tier> label={`${r.label}: hangi paketten itibaren`} value={cur}
                        onChange={(v) => setDraft((o) => ({ ...o, [r.key]: v }))}
                        options={(d.tiers.length ? d.tiers : TIERS).map((t) => ({ value: t, label: d.labels[t] ?? TIER_LABEL[t] }))} />
                    </div>
                  ) : (
                    <span className="inline-flex shrink-0 items-center gap-1 text-xs text-stone-500 sm:w-72 sm:justify-center">
                      <Lock size={12} aria-hidden="true" /> Her pakette (değiştirilemez)
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
          <label aria-label="Hak dolunca yıllığa geçişi öner" className="flex cursor-pointer items-start gap-2 rounded-lg border border-stone-200 p-2.5 text-sm dark:border-stone-700">
            <input type="checkbox" className="mt-1" checked={yearlyOn} onChange={(e) => setYearly(e.target.checked)} />
            <span><span className="font-medium">Hak dolunca yıllığa geçişi öner</span>
              <span className="block text-xs text-stone-500">Apple'da aynı seviyede aylık→yıllık geçiş bir sonraki yenilemede başlar; varsayılan kapalı. Yıllık daha fazla okuma hakkı vermez.</span></span>
          </label>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" disabled={!dirty || busy} onClick={() => save.mutate(undefined)}>
              <Save size={16} aria-hidden="true" /> Kaydet
            </button>
            {dirty && <button className="btn-ghost" disabled={busy} onClick={() => { setDraft({}); setYearly(null); }}>Vazgeç</button>}
            <button className={cx("btn-ghost border border-stone-300 dark:border-stone-700")} disabled={busy || (!changed && !dirty)}
              onClick={() => void ask("Bütün satırlar varsayılan paketine dönülsün mü? Yıllığa geçiş önerisi ayarı değişmez.", { confirm: "Varsayılana dön" })
                .then((ok) => { if (ok) reset.mutate(undefined); })}>
              <RotateCcw size={16} aria-hidden="true" /> Varsayılana dön
            </button>
          </div>
        </>}
      </>}
    </section>
  );
}
