// Ayarlar → "Ekstra durak için en az tasarruf" (Bölgem'in yanında; yetenek shopping_optimize): alışveriş listesi
// "Nereye gideyim?" bir market daha eklemeyi ancak bu kadar ucuzlatıyorsa önerir. PUT /api/me/ui {stop_cost};
// sunucu meta.ui.stop_cost vermiyorsa (eski sunucu) görünmez. Mobil karşılığı: hesapkitap-mobile Ayarlar.
import { Route } from "lucide-react";
import { useState } from "react";
import { useUI } from "../ui";
import { useUiPrefs } from "./hooks";
import Segmented from "./Segmented";

const PRESETS = [50, 100, 200];
export const STOP_COST_HINT = "Alışveriş listesinde bir market daha, en az bu kadar ucuzlatıyorsa önerilir.";

export default function StopCostForm() {
  const ui = useUI();
  const { ui: prefs, save } = useUiPrefs();
  const cur = prefs.stop_cost;
  const [custom, setCustom] = useState<boolean | null>(null);
  const [val, setVal] = useState("");
  if (cur == null) return null;
  const isCustom = custom ?? !PRESETS.includes(cur);
  const put = (v: number) => save.mutate({ stop_cost: v }, { onSuccess: () => ui.toast(`Ekstra durak için en az ₺${v.toLocaleString("tr-TR")}`), onError: (e) => ui.error(e.message) });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const n = Math.round(Number(val.replace(",", ".")));
    if (!Number.isFinite(n) || n < 0 || n > 10000) { ui.error("0 ile 10.000 arasında bir tutar yaz"); return; }
    put(n);
  };
  return (
    <section className="card space-y-2 p-4" data-testid="durak-tasarruf">
      <div className="flex items-start gap-2 text-sm">
        <Route size={18} className="mt-0.5 shrink-0 text-stone-500" aria-hidden />
        <span className="min-w-0 flex-1"><span className="font-semibold">Ekstra durak için en az tasarruf</span>
          <span className="block text-xs text-stone-500">{STOP_COST_HINT}</span></span>
      </div>
      <Segmented label="Ekstra durak için en az tasarruf" value={isCustom ? "ozel" : String(cur)}
        onChange={(v) => { if (v === "ozel") { setCustom(true); setVal(String(cur)); } else { setCustom(false); put(Number(v)); } }}
        options={[...PRESETS.map((p) => ({ value: String(p), label: `₺${p}` })), { value: "ozel", label: "Özel" }]} />
      {isCustom && (
        <form className="flex items-center gap-2" onSubmit={submit}>
          <label className="sr-only" htmlFor="durak-tutar">Tutar (₺)</label>
          <span className="text-sm text-stone-500">₺</span>
          <input id="durak-tutar" className="input w-28 tabular-nums" inputMode="numeric" value={val} placeholder={String(cur)} onChange={(e) => setVal(e.target.value)} />
          <button className="btn-primary" disabled={save.isPending || !val.trim()}>Kaydet</button>
        </form>
      )}
    </section>
  );
}
