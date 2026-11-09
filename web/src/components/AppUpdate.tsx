import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Save, Smartphone } from "lucide-react";
import { useState } from "react";
import { ask } from "../lib/ask";
import { call } from "../lib/apiError";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import SorguDurumu from "./SorguDurumu";

type Platform = "ios" | "android";
type Field = "latest" | "min" | "store_url";
interface AdminAppUpdate {
  fields: Record<Platform, Record<Field, { value: string | null; source: "ayar" | "ortam" | null }>>;
  effective: Partial<Record<Platform, { latest: string; min: string | null; store_url: string }>>;
  default_store: Record<Platform, string>;
}

const KEY = ["admin-app-update"];
const PLATFORMS: [Platform, string][] = [["ios", "iOS"], ["android", "Android"]];
const FIELDS: [Field, string, string][] = [
  ["latest", "Son sürüm", "1.0.1"],
  ["min", "En düşük sürüm", "1.0.0"],
  ["store_url", "Mağaza adresi", ""],
];
type Draft = Partial<Record<Platform, Partial<Record<Field, string>>>>;

/** Yönetici: mobil uygulamanın güncelleme yönlendirmesi. Son sürümden eski uygulamada Özet'te kapatılabilir
 *  "Yeni sürüm var" kartı; en düşük sürümden eski uygulama kullanılamaz ("Güncelleme gerekli"). Boş = kapalı. */
export default function AppUpdate() {
  const ui = useUI();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: KEY, queryFn: () => call<AdminAppUpdate>("GET", "/api/admin/app-update") });
  const [draft, setDraft] = useState<Draft>({});
  const d = q.data;
  const val = (p: Platform, f: Field) => draft[p]?.[f] ?? (d?.fields[p][f].source === "ayar" ? d.fields[p][f].value ?? "" : "");
  const done = (r: AdminAppUpdate, msg: string) => { qc.setQueryData(KEY, r); setDraft({}); ui.toast(msg); };
  const save = useSave(() => call<AdminAppUpdate>("PUT", "/api/admin/app-update", draft), (r) => done(r, "Uygulama sürümü kaydedildi"));
  const reset = useSave(() => call<AdminAppUpdate>("PUT", "/api/admin/app-update", { reset: true }), (r) => done(r, "Ayar silindi"));
  const dirty = Object.keys(draft).length > 0;
  const hasOwn = !!d && PLATFORMS.some(([p]) => FIELDS.some(([f]) => d.fields[p][f].source === "ayar"));

  return (
    <section className="card space-y-3 p-4" aria-labelledby="app-update-h">
      <h2 id="app-update-h" className="flex items-center gap-2 font-semibold"><Smartphone size={18} aria-hidden="true" /> Mobil uygulama sürümü</h2>
      <p className="text-sm text-stone-600 dark:text-stone-400">
        Son sürümden eski uygulamada Özet'te kapatılabilir "Yeni sürüm var" kartı çıkar. En düşük sürümden eski uygulama
        açılmaz, mağazaya yönlendirir. Boş bırakılan platformda hiçbir şey gösterilmez.
      </p>
      <SorguDurumu q={q} kompakt />
      {d && (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }}>
          {PLATFORMS.map(([p, label]) => {
            const eff = d.effective[p];
            return (
              <fieldset key={p} className="space-y-2">
                <legend className="text-sm font-medium">{label}</legend>
                <div className="grid gap-2 sm:grid-cols-[8rem_8rem_1fr]">
                  {FIELDS.map(([f, flabel, ph]) => {
                    const env = d.fields[p][f].source === "ortam" ? d.fields[p][f].value : null;
                    return (
                      <label key={f} className="min-w-0 text-xs text-stone-500">{flabel}
                        <input className="input mt-1 block w-full" value={val(p, f)}
                          inputMode={f === "store_url" ? "url" : "decimal"}
                          placeholder={env ?? (f === "store_url" ? d.default_store[p] : ph)}
                          onChange={(e) => setDraft({ ...draft, [p]: { ...draft[p], [f]: e.target.value } })} />
                        {env && <span className="mt-0.5 block truncate">Ortam değişkeni: {env}</span>}
                      </label>
                    );
                  })}
                </div>
                <p className="text-xs text-stone-500">
                  {eff ? <>Şu an: son <b>{eff.latest}</b>{eff.min ? <> · en düşük <b>{eff.min}</b></> : " · zorunlu güncelleme yok"}</> : "Şu an kapalı"}
                </p>
              </fieldset>
            );
          })}
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary inline-flex items-center gap-1" disabled={!dirty || save.isPending}><Save size={16} aria-hidden="true" /> Kaydet</button>
            {hasOwn && (
              <button type="button" className="btn-ghost inline-flex items-center gap-1 border border-stone-300 dark:border-stone-700" disabled={reset.isPending}
                onClick={async () => { if (await ask("Yönetici ayarı silinsin mi? Ortam değişkenleri geçerli olur.")) reset.mutate(undefined); }}>
                <RotateCcw size={16} aria-hidden="true" /> Ayarı sil
              </button>
            )}
          </div>
          <p className="text-xs text-stone-500">Sürüm biçimi 1.0.1. Ortam değişkeni: HESAPKITAP_APP_IOS_LATEST, _MIN, _STORE_URL (Android için ANDROID).</p>
        </form>
      )}
    </section>
  );
}
